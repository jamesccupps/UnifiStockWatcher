"""
Unifi Stock Watcher — Core Module
Shared store API, configuration, notifications, price & stock history.
"""

import re
import sys
import json
import time
import threading
import webbrowser
from pathlib import Path
from datetime import datetime, timedelta

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

# ── Paths ────────────────────────────────────────────────────────────────────

BASE_DIR       = Path(__file__).parent
CONFIG_FILE    = BASE_DIR / "watched_items.json"
SETTINGS_FILE  = BASE_DIR / "settings.json"
HISTORY_FILE   = BASE_DIR / "stock_history.json"

# ── Store constants ──────────────────────────────────────────────────────────

STORE_REGIONS = {
    "us": {"label": "United States", "path": "us/en"},
    "eu": {"label": "Europe",        "path": "eu/en"},
    "uk": {"label": "United Kingdom", "path": "uk/en"},
    "ca": {"label": "Canada",        "path": "ca/en"},
}

STORE_BASE = "https://store.ui.com"

CATEGORIES = [
    "category/all-cloud-gateways",
    "category/all-switching",
    "category/all-wifi",
    "category/all-cameras-nvrs",
    "category/all-door-access",
    "category/all-integrations",
    "category/all-advanced-hosting",
    "category/accessories-cables-dacs",
    "category/network-storage",
]

CATEGORY_LABELS = {
    "category/all-cloud-gateways":      "Cloud Gateways",
    "category/all-switching":           "Switching",
    "category/all-wifi":                "WiFi",
    "category/all-cameras-nvrs":        "Cameras & NVRs",
    "category/all-door-access":         "Door Access",
    "category/all-integrations":        "Integrations",
    "category/all-advanced-hosting":    "Advanced Hosting",
    "category/accessories-cables-dacs": "Accessories, Cables & DACs",
    "category/network-storage":         "Network Storage",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html",
}

# ── Default settings ─────────────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    "font_size":      10,
    "font_family":    "Segoe UI",
    "bg":             "#0d1117",
    "panel":          "#161b22",
    "accent":         "#1f6feb",
    "green":          "#3fb950",
    "red":            "#f85149",
    "gold":           "#e3b341",
    "text":           "#e6edf3",
    "muted":          "#7d8590",
    "poll_interval":  60,
    "sound_alerts":   True,
    "auto_open_url":  True,
    "auto_start":     False,
    "region":         "us",
    "max_retries":    3,
}

# ── Settings load/save ───────────────────────────────────────────────────────

def load_settings():
    if SETTINGS_FILE.exists():
        try:
            s = json.loads(SETTINGS_FILE.read_text())
            merged = DEFAULT_SETTINGS.copy()
            merged.update(s)
            return merged
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(s):
    SETTINGS_FILE.write_text(json.dumps(s, indent=2))


# ── Palette builder ──────────────────────────────────────────────────────────

def build_palette(s):
    import colorsys

    def lighten(hex_col, amount=0.15):
        hex_col = hex_col.lstrip("#")
        r, g, b = [int(hex_col[i:i+2], 16) / 255 for i in (0, 2, 4)]
        h, l, sat = colorsys.rgb_to_hls(r, g, b)
        l = min(1.0, l + amount)
        r2, g2, b2 = colorsys.hls_to_rgb(h, l, sat)
        return "#{:02x}{:02x}{:02x}".format(int(r2*255), int(g2*255), int(b2*255))

    def darken(hex_col, amount=0.05):
        hex_col = hex_col.lstrip("#")
        r, g, b = [int(hex_col[i:i+2], 16) / 255 for i in (0, 2, 4)]
        h, l, sat = colorsys.rgb_to_hls(r, g, b)
        l = max(0.0, l - amount)
        r2, g2, b2 = colorsys.hls_to_rgb(h, l, sat)
        return "#{:02x}{:02x}{:02x}".format(int(r2*255), int(g2*255), int(b2*255))

    bg     = s["bg"]
    panel  = s["panel"]
    accent = s["accent"]

    return {
        "bg":       bg,
        "panel":    panel,
        "border":   lighten(bg, 0.08),
        "hover":    lighten(bg, 0.05),
        "text":     s["text"],
        "muted":    s["muted"],
        "accent":   accent,
        "accent_h": lighten(accent, 0.12),
        "green":    s["green"],
        "red":      s["red"],
        "yellow":   "#d29922",
        "gold":     s["gold"],
        "white":    "#ffffff",
        "tag_bg":   lighten(bg, 0.06),
        "fav_bg":   "#1c1a10",
    }


# ── Build ID cache ───────────────────────────────────────────────────────────

class BuildIdCache:
    """Cache the Next.js buildId to avoid hammering the store homepage."""

    def __init__(self, ttl_seconds=300):
        self._lock     = threading.Lock()
        self._build_id = None
        self._fetched  = None
        self._ttl      = ttl_seconds

    def get(self, region="us", force=False):
        with self._lock:
            now = datetime.now()
            if (not force
                    and self._build_id
                    and self._fetched
                    and (now - self._fetched).total_seconds() < self._ttl):
                return self._build_id

        store_home = f"{STORE_BASE}/{STORE_REGIONS[region]['path']}"
        r = requests.get(store_home, headers=HEADERS, timeout=15)
        r.raise_for_status()
        m = re.search(r'"buildId":"([^"]+)"', r.text)
        if not m:
            raise RuntimeError("Could not find buildId on store homepage.")

        with self._lock:
            self._build_id = m.group(1)
            self._fetched  = datetime.now()
            return self._build_id

    def invalidate(self):
        with self._lock:
            self._build_id = None
            self._fetched  = None


# Global instance
_build_cache = BuildIdCache()


def get_build_id(region="us", force=False):
    return _build_cache.get(region, force)


def invalidate_build_id():
    _build_cache.invalidate()


# ── Store API ────────────────────────────────────────────────────────────────

def fetch_all_products(build_id, region="us", progress_cb=None, error_cb=None):
    """Fetch all products from every category, deduplicated. Returns list of dicts.

    progress_cb(int_0_to_100) is called after each category.
    error_cb(category_slug, exception) is called on per-category failures so callers
    (e.g. the GUI) can surface them instead of only printing to stdout.

    If a category 404s — usually because the Next.js buildId rotated mid-fetch —
    the cache is invalidated and that category is retried once with a fresh id.
    """
    products = {}
    region_path = STORE_REGIONS[region]["path"]
    for i, cat in enumerate(CATEGORIES):
        for attempt in range(2):  # one retry slot for build_id rotation
            url = f"{STORE_BASE}/_next/data/{build_id}/{region_path}/{cat}.json"
            try:
                r = requests.get(url, headers=HEADERS, timeout=15)
                r.raise_for_status()
                data = r.json()
                pp = data.get("pageProps", {})
                # Primary path: products nested under subCategories
                for subcat in pp.get("subCategories", []):
                    for p in subcat.get("products", []):
                        if p.get("slug"):
                            p["_category"] = cat
                            products[p["slug"]] = p
                # Belt-and-suspenders: a flat products list, in case UI ever
                # switches a category page to that shape. setdefault so the
                # richer subCategory entry wins if both exist.
                for p in pp.get("products", []):
                    if p.get("slug"):
                        p.setdefault("_category", cat)
                        products.setdefault(p["slug"], p)
                break  # success
            except requests.exceptions.HTTPError as e:
                # 404 typically means buildId rotated between homepage fetch
                # and this category call. Invalidate + retry once.
                if (e.response is not None
                        and e.response.status_code == 404
                        and attempt == 0):
                    try:
                        invalidate_build_id()
                        build_id = get_build_id(region, force=True)
                        continue  # retry the same category with fresh id
                    except Exception:
                        pass
                print(f"[UnifiWatcher] Category fetch failed: {cat} — {e}")
                if error_cb:
                    try:
                        error_cb(cat, e)
                    except Exception:
                        pass
                break
            except Exception as e:
                print(f"[UnifiWatcher] Category fetch failed: {cat} — {e}")
                if error_cb:
                    try:
                        error_cb(cat, e)
                    except Exception:
                        pass
                break
        if progress_cb:
            progress_cb(int((i + 1) / len(CATEGORIES) * 100))
        time.sleep(0.3)
    print(f"[UnifiWatcher] Fetched {len(products)} unique products across {len(CATEGORIES)} categories")
    return list(products.values())


def is_available(product):
    return any(v.get("status") == "Available" for v in product.get("variants", []))


def _format_price(price_val):
    """Format a price value which may be a Money dict, number, or string."""
    if isinstance(price_val, str):
        return price_val
    if isinstance(price_val, dict):
        amount   = price_val.get("amount", 0)
        currency = price_val.get("currency", "USD")
        symbols  = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "C$",
                     "AUD": "A$", "SEK": "", "NOK": "", "DKK": ""}
        sym = symbols.get(currency, "")
        formatted = f"{amount / 100:,.2f}"
        if sym:
            return f"{sym}{formatted}"
        return f"{formatted} {currency}"
    if isinstance(price_val, (int, float)):
        return f"${price_val:,.2f}"
    return str(price_val)


def get_price(product):
    """Extract the display price from a product dict.
    displayPrice can be a Money dict like {'amount': 39900, 'currency': 'USD'}
    or a plain number or string.
    """
    for v in product.get("variants", []):
        price = v.get("displayPrice") or v.get("price")
        if price is not None:
            return _format_price(price)
    return None


def check_slug(build_id, slug, region="us", retries=3):
    """Check if a product slug is in stock. Returns (in_stock: bool, price: str|None)."""
    region_path = STORE_REGIONS[region]["path"]
    url = f"{STORE_BASE}/_next/data/{build_id}/{region_path}/products/{slug}.json"

    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()

            def find_variants(obj):
                if isinstance(obj, dict):
                    if "variants" in obj:
                        return obj["variants"]
                    for v in obj.values():
                        res = find_variants(v)
                        if res is not None:
                            return res
                elif isinstance(obj, list):
                    for item in obj:
                        res = find_variants(item)
                        if res is not None:
                            return res
                return None

            variants = find_variants(r.json().get("pageProps", {}))
            if not variants:
                return False, None

            in_stock = any(v.get("status") == "Available" for v in variants)
            price = None
            for v in variants:
                p = v.get("displayPrice") or v.get("price")
                if p is not None:
                    price = _format_price(p)
                    break
                    break
            return in_stock, price

        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                # Build ID may have rotated
                if attempt < retries - 1:
                    invalidate_build_id()
                    try:
                        build_id = get_build_id(region, force=True)
                        url = f"{STORE_BASE}/_next/data/{build_id}/{region_path}/products/{slug}.json"
                    except Exception:
                        pass
                last_err = e
            else:
                last_err = e
        except Exception as e:
            last_err = e

        if attempt < retries - 1:
            time.sleep(2 ** attempt)

    raise last_err or RuntimeError(f"Failed to check {slug} after {retries} attempts")


# ── Config load/save ─────────────────────────────────────────────────────────

def load_watched():
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            for item in data:
                item.setdefault("favourite", False)
                item.setdefault("price", None)
                item.setdefault("added_at", None)
            return data
        except Exception:
            return []
    return []


def save_watched(items):
    CONFIG_FILE.write_text(json.dumps(items, indent=2))


# ── Stock history ────────────────────────────────────────────────────────────

class StockHistory:
    """Persist stock check events to a JSON file for history/stats."""

    def __init__(self, path=HISTORY_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self):
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except Exception:
                pass
        return {"events": [], "stats": {"total_checks": 0, "in_stock_alerts": 0}}

    def _save(self):
        # Keep only last 2000 events to prevent unbounded growth
        if len(self._data["events"]) > 2000:
            self._data["events"] = self._data["events"][-2000:]
        self._path.write_text(json.dumps(self._data, indent=2))

    def record_check(self, slug, title, in_stock, price=None):
        with self._lock:
            self._data["stats"]["total_checks"] += 1
            if in_stock:
                self._data["stats"]["in_stock_alerts"] += 1
            self._data["events"].append({
                "ts":       datetime.now().isoformat(),
                "slug":     slug,
                "title":    title,
                "in_stock": in_stock,
                "price":    price,
            })
            self._save()

    def get_stats(self):
        with self._lock:
            return self._data["stats"].copy()

    def get_events(self, slug=None, limit=50):
        with self._lock:
            events = self._data["events"]
            if slug:
                events = [e for e in events if e["slug"] == slug]
            return events[-limit:]

    def last_in_stock(self, slug):
        with self._lock:
            for e in reversed(self._data["events"]):
                if e["slug"] == slug and e["in_stock"]:
                    return e["ts"]
            return None

    def clear(self):
        with self._lock:
            self._data = {"events": [], "stats": {"total_checks": 0, "in_stock_alerts": 0}}
            self._save()


# Global instance
stock_history = StockHistory()


# ── Notification ─────────────────────────────────────────────────────────────

def notify_windows(title, message):
    """System tray balloon notification via System.Windows.Forms."""
    ps_title   = title.replace('"', '`"').replace("'", "`'")
    ps_message = message.replace('"', '`"').replace("'", "`'")
    ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$balloon = New-Object System.Windows.Forms.NotifyIcon
$balloon.Icon = [System.Drawing.SystemIcons]::Information
$balloon.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
$balloon.BalloonTipTitle = "{ps_title}"
$balloon.BalloonTipText = "{ps_message}"
$balloon.Visible = $true
$balloon.ShowBalloonTip(10000)
Start-Sleep -Seconds 3
$balloon.Dispose()
"""
    try:
        import subprocess
        result = subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip())
    except Exception as e:
        print("\a")
        print(f"  *** ALERT: {title} — {message}  (popup failed: {e})")


def play_sound():
    """Play a system alert sound (Windows only, fails silently elsewhere)."""
    try:
        import winsound
        winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        pass


# ── Export / Import ──────────────────────────────────────────────────────────

def export_watchlist(filepath):
    """Export current watch list to a JSON file."""
    items = load_watched()
    Path(filepath).write_text(json.dumps(items, indent=2))
    return len(items)


def import_watchlist(filepath):
    """Import watch list from a JSON file, merging with existing."""
    new_items = json.loads(Path(filepath).read_text())
    existing  = load_watched()
    slugs     = {w["slug"] for w in existing}
    added     = 0
    for item in new_items:
        if item.get("slug") and item["slug"] not in slugs:
            item.setdefault("favourite", False)
            item.setdefault("price", None)
            item.setdefault("added_at", datetime.now().isoformat())
            existing.append(item)
            slugs.add(item["slug"])
            added += 1
    save_watched(existing)
    return added
