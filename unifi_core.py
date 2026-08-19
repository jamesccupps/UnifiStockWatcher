"""
Unifi Stock Watcher — Core Module
Shared store API, configuration, notifications, price & stock history.
"""

import os
import re
import json
import time
import atexit
import logging
import threading
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

log = logging.getLogger("unifi_watcher")


class StoreError(RuntimeError):
    """The store could not be reached, or answered something unusable."""


class ProductNotFound(StoreError):
    """The slug does not resolve to a product any more."""

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
MAX_REDIRECTS = 4

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

# ── JSON persistence ─────────────────────────────────────────────────────────

def read_json(path, default):
    """Read a JSON file, returning `default` if it is missing or unreadable.

    Explicit UTF-8: the default is the locale codec, which on a Windows box is
    cp1252 and silently mangles any watch list produced elsewhere.
    """
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("Could not read %s (%s); falling back to default", path.name, e)
        return default


def write_json(path, data):
    """Write JSON atomically, so an interrupted write cannot truncate the file.

    write_text() opens with O_TRUNC: a crash, a full disk, or the machine
    sleeping mid-write leaves an empty or half-written watch list. Write to a
    sibling temp file and rename - os.replace is atomic on NTFS and POSIX.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


# ── Settings load/save ───────────────────────────────────────────────────────

def load_settings():
    merged = DEFAULT_SETTINGS.copy()
    stored = read_json(SETTINGS_FILE, {})
    if isinstance(stored, dict):
        merged.update(stored)
    return merged


def save_settings(s):
    write_json(SETTINGS_FILE, s)


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
            raise StoreError("Could not find buildId on store homepage.")

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


def _data_url(build_id, path):
    return f"{STORE_BASE}/_next/data/{build_id}{path}.json"


def _redirect_path(target, slug):
    """Normalise a redirect target to a /<region>/<...> data path."""
    path = urlsplit(target).path.split("?")[0].removesuffix(".json")
    if path.rstrip("/").endswith("/404"):
        raise ProductNotFound(f"{slug}: no longer on the store")
    return path


def _fetch_product_page(build_id, slug, region="us"):
    """Return the pageProps of a product page, following Next.js data redirects.

    The store answers /products/<slug>.json for roughly half the catalog with
    HTTP 200 and a body of {"pageProps": {"__N_REDIRECT": "<canonical path>"}},
    and occasionally with a bare 307 carrying no Location header. Both mean the
    product lives under a category/collection path, not at the bare slug.
    """
    path = f"/{STORE_REGIONS[region]['path']}/products/{slug}"
    for _ in range(MAX_REDIRECTS):
        r = requests.get(_data_url(build_id, path), headers=HEADERS, timeout=15)

        if r.status_code in (301, 302, 303, 307, 308):
            # Next.js signals its own redirects out of band; a plain 307 here
            # usually carries x-nextjs-redirect rather than Location.
            loc = r.headers.get("Location") or r.headers.get("x-nextjs-redirect")
            if not loc:
                raise StoreError(
                    f"{slug}: store returned {r.status_code} with no redirect target")
            path = _redirect_path(loc, slug)
            continue

        r.raise_for_status()
        page_props = r.json().get("pageProps", {})

        target = page_props.get("__N_REDIRECT")
        if target:
            path = _redirect_path(target, slug)
            continue

        return page_props

    raise StoreError(f"{slug}: too many redirects (>{MAX_REDIRECTS})")


def _find_product(page_props, slug):
    """Pick the requested product out of a product page.

    A product page carries its whole collection plus related/upsell products.
    Matching must be by identity - the previous implementation walked the JSON
    for the first "variants" key it could find, which on a page like ua-g2
    returns ua-g3's variants and reports the wrong product's stock.
    """
    candidates = list((page_props.get("collection") or {}).get("products") or [])
    for key in ("product", "currentProduct"):
        if isinstance(page_props.get(key), dict):
            candidates.append(page_props[key])

    for p in candidates:
        if p.get("slug") == slug:
            return p

    current_id = page_props.get("currentProductId")
    if current_id:
        for p in candidates:
            if p.get("id") == current_id:
                return p

    for p in candidates:                       # renamed product
        if slug in (p.get("historicalSlugs") or []):
            return p
    return None


def check_slug(build_id, slug, region="us", retries=3):
    """Check a single product slug. Returns (in_stock: bool, price: str|None).

    Raises ProductNotFound if the slug no longer resolves, and StoreError if
    the store cannot be reached. It never reports a fabricated "out of stock" -
    that silently defeats the entire point of a stock watcher.
    """
    last_err = None
    for attempt in range(retries):
        try:
            page_props = _fetch_product_page(build_id, slug, region)
            product = _find_product(page_props, slug)
            if product is None:
                raise ProductNotFound(f"{slug}: not present on its own page")
            return is_available(product), get_price(product)

        except ProductNotFound:
            raise
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 404:
                # Either the buildId rotated or the product is gone. Ask for a
                # fresh buildId and compare: only a genuine rotation justifies
                # discarding the cached id that every other item is using.
                fresh = get_build_id(region, force=True)
                if fresh != build_id:
                    build_id = fresh
                    last_err = e
                    continue
                raise ProductNotFound(f"{slug}: no longer on the store") from e
            last_err = e
        except Exception as e:
            last_err = e

        if attempt < retries - 1:
            time.sleep(2 ** attempt)

    raise StoreError(f"{slug}: failed after {retries} attempts") from last_err



# ── Config load/save ─────────────────────────────────────────────────────────

def _normalise_watched(data):
    """Keep only well-formed entries, filling in optional fields."""
    items = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or not item.get("slug"):
            log.warning("Skipping malformed watch list entry: %r", item)
            continue
        item.setdefault("title", item["slug"])
        item.setdefault("favourite", False)
        item.setdefault("price", None)
        item.setdefault("added_at", None)
        items.append(item)
    return items


def load_watched():
    return _normalise_watched(read_json(CONFIG_FILE, []))


def save_watched(items):
    write_json(CONFIG_FILE, items)


# ── Stock history ────────────────────────────────────────────────────────────

MAX_HISTORY_EVENTS = 2000


def _empty_history():
    return {"events": [], "stats": {"total_checks": 0, "in_stock_alerts": 0}}


class StockHistory:
    """Stock check events, persisted to JSON for history and stats.

    Writes are deferred rather than issued per event: the watcher records one
    event per watched item per cycle, and re-serialising the whole 2000-event
    file each time meant tens of megabytes of disk churn per hour for a
    modest watch list. flush() is called on a timer and at shutdown.
    """

    def __init__(self, path=HISTORY_FILE, autosave_after=25):
        self._path = Path(path)
        self._lock = threading.Lock()
        self._data = self._load()
        self._dirty = False
        self._unsaved = 0
        self._autosave_after = autosave_after

    def _load(self):
        data = read_json(self._path, None)
        if not isinstance(data, dict):
            return _empty_history()
        # Tolerate a hand-edited or partially written file.
        merged = _empty_history()
        if isinstance(data.get("events"), list):
            merged["events"] = data["events"]
        if isinstance(data.get("stats"), dict):
            merged["stats"].update(data["stats"])
        return merged

    def _save_locked(self):
        if len(self._data["events"]) > MAX_HISTORY_EVENTS:
            self._data["events"] = self._data["events"][-MAX_HISTORY_EVENTS:]
        write_json(self._path, self._data)
        self._dirty = False
        self._unsaved = 0

    def flush(self):
        """Persist pending events. Safe to call when nothing has changed."""
        with self._lock:
            if self._dirty:
                self._save_locked()

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
            self._dirty = True
            self._unsaved += 1
            if self._unsaved >= self._autosave_after:
                self._save_locked()

    def get_stats(self):
        with self._lock:
            return self._data["stats"].copy()

    def get_events(self, slug=None, limit=50):
        with self._lock:
            events = self._data["events"]
            if slug:
                events = [e for e in events if e.get("slug") == slug]
            return events[-limit:] if limit else list(events)

    def last_in_stock(self, slug):
        with self._lock:
            for e in reversed(self._data["events"]):
                if e.get("slug") == slug and e.get("in_stock"):
                    return e["ts"]
            return None

    def clear(self):
        with self._lock:
            self._data = _empty_history()
            self._save_locked()


# Global instance. Deferred writes mean pending events must be flushed on the
# way out, including on Ctrl+C or a closed GUI window.
stock_history = StockHistory()
atexit.register(stock_history.flush)


# ── Notification ─────────────────────────────────────────────────────────────

def notify_windows(title, message):
    """Show a system tray balloon notification (Windows).

    Title and message are passed to PowerShell through the environment, never
    interpolated into the script source — a product title containing $(...) or
    other PowerShell metacharacters is therefore inert data, not code.

    Returns immediately; the helper process lives just long enough for the
    balloon to render so callers (including the Tk main thread) never block.
    """
    ps_script = """
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$balloon = New-Object System.Windows.Forms.NotifyIcon
$balloon.Icon = [System.Drawing.SystemIcons]::Information
$balloon.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
$balloon.BalloonTipTitle = $env:UNIFI_NOTIFY_TITLE
$balloon.BalloonTipText = $env:UNIFI_NOTIFY_TEXT
$balloon.Visible = $true
$balloon.ShowBalloonTip(10000)
Start-Sleep -Seconds 3
$balloon.Dispose()
"""
    env = dict(os.environ)
    # NotifyIcon truncates beyond these; trim here so the balloon stays readable.
    env["UNIFI_NOTIFY_TITLE"] = str(title)[:63]
    env["UNIFI_NOTIFY_TEXT"]  = str(message)[:255]

    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-WindowStyle", "Hidden", "-Command", ps_script],
            env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        log.warning("Balloon notification failed (%s): %s - %s", e, title, message)
        print("", end="", flush=True)


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
    write_json(filepath, items)
    return len(items)


def import_watchlist(filepath):
    """Import a watch list from a JSON file, merging with the existing one.

    Imported entries come from outside the application, so they are normalised
    and their fields constrained before being persisted. Only the keys the
    application actually uses are carried over.
    """
    raw = read_json(filepath, None)
    if not isinstance(raw, list):
        raise ValueError("Watch list file must contain a JSON array of items.")

    existing = load_watched()
    slugs    = {w["slug"] for w in existing}
    added    = 0
    for item in _normalise_watched(raw):
        if item["slug"] in slugs:
            continue
        existing.append({
            "slug":      str(item["slug"])[:200],
            "title":     str(item["title"])[:200],
            "favourite": bool(item["favourite"]),
            "price":     item["price"] if isinstance(item["price"], str) else None,
            "added_at":  item["added_at"] or datetime.now().isoformat(),
        })
        slugs.add(item["slug"])
        added += 1
    save_watched(existing)
    return added
