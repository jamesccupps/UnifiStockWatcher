"""
Unifi Stock Watcher — Core Module
Shared store API, configuration, notifications, price & stock history.
"""

import os
import sys
import re
import json
import time
import atexit
import logging
import threading
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

__version__ = "3.1.1"

APP_NAME = "UniFi Stock Watcher"
APP_ID   = "JamesCupps.UnifiStockWatcher"     # AppUserModelID, winget id
REPO     = "jamesccupps/UnifiStockWatcher"
REPO_URL = f"https://github.com/{REPO}"

log = logging.getLogger("unifi_watcher")


class StoreError(RuntimeError):
    """The store could not be reached, or answered something unusable."""


class ProductNotFound(StoreError):
    """The slug does not resolve to a product any more."""

# ── Paths ────────────────────────────────────────────────────────────────────
#
# Program files and the user's files live apart. The installer puts the program
# in %LOCALAPPDATA%\Programs, where updates replace it wholesale; the watch list
# and settings live in %APPDATA%\UniFi Stock Watcher and survive that.
#
# A portable copy (the zip) carries a `portable.txt` beside the executable and
# keeps its files in that folder instead - which is also where 2.x's single
# portable .exe kept them, so unzipping 3.x over a 2.x folder keeps the list.

PORTABLE_MARKER = "portable.txt"
DATA_FILES = ("watched_items.json", "settings.json",
              "stock_history.json", "category_cache.json")


def program_dir():
    """The folder holding the executable (frozen) or these sources."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def is_portable():
    return (program_dir() / PORTABLE_MARKER).is_file()


def _user_data_dir():
    if sys.platform == "win32":
        root = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
        return Path(root) / APP_NAME
    root = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(root) / "unifi-stock-watcher"


def _base_dir():
    """Where the watch list, settings and history live.

    USW_DATA_DIR overrides everything, for tests and CI smoke runs. Never the
    PyInstaller extraction directory: a watch list written there is deleted
    on exit.
    """
    override = os.environ.get("USW_DATA_DIR")
    if override:
        return Path(override)
    if is_portable():
        return program_dir()
    return _user_data_dir()


BASE_DIR       = _base_dir()
CONFIG_FILE    = BASE_DIR / "watched_items.json"
SETTINGS_FILE  = BASE_DIR / "settings.json"
HISTORY_FILE   = BASE_DIR / "stock_history.json"
CATEGORY_CACHE_FILE = BASE_DIR / "category_cache.json"
LOG_FILE       = BASE_DIR / "unifi-stock-watcher.log"


def migrate_legacy_files():
    """Copy 2.x files from beside the program into the data folder, once.

    2.x kept everything next to unifi_watcher_gui.py (or the .exe). A copy
    that now uses %APPDATA% finds its watch list there the first time it
    starts. Copies, never moves: the old folder stays a working 2.x install,
    and nothing is overwritten. Returns the names copied.
    """
    legacy = program_dir()
    if legacy == BASE_DIR or os.environ.get("USW_DATA_DIR"):
        return []
    if (BASE_DIR / "watched_items.json").exists():
        return []
    copied = []
    for name in DATA_FILES:
        src, dst = legacy / name, BASE_DIR / name
        if src.is_file() and not dst.exists():
            try:
                BASE_DIR.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
                copied.append(name)
            except OSError as e:
                log.warning("Could not copy %s from %s: %s", name, legacy, e)
    if copied:
        log.warning("Copied %s from %s", ", ".join(copied), legacy)
        if "stock_history.json" in copied:
            stock_history.reload()
    return copied


def setup_logging(level=logging.INFO, console=False):
    """A size-capped log in the data folder; the first place to look when
    something goes wrong. Console output too for the CLI."""
    from logging.handlers import RotatingFileHandler
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    try:
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(LOG_FILE, maxBytes=512_000, backupCount=1,
                                 encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass
    if console:
        sh = logging.StreamHandler()
        sh.setLevel(logging.WARNING)
        sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s",
                                          "%H:%M:%S"))
        root.addHandler(sh)

# ── Store constants ──────────────────────────────────────────────────────────

STORE_REGIONS = {
    "us": {"label": "United States", "path": "us/en"},
    "eu": {"label": "Europe",        "path": "eu/en"},
    "uk": {"label": "United Kingdom", "path": "uk/en"},
    "ca": {"label": "Canada",        "path": "ca/en"},
}

STORE_BASE = "https://store.ui.com"
MAX_REDIRECTS = 4
CATEGORY_WORKERS = 4
CATEGORY_DISCOVERY_TTL = 24 * 3600   # re-check the store's category list daily

# Category slugs the store actually serves today. These get renamed - cameras
# moved from all-cameras-nvrs to all-physical-security, and all-advanced-hosting
# became an alias for the accessories page - so _fetch_category follows
# redirects and logs a warning when one of these drifts.
CATEGORIES = [
    "category/all-cloud-gateways",
    "category/all-switching",
    "category/all-wifi",
    "category/all-physical-security",
    "category/all-door-access",
    "category/all-integrations",
    "category/accessories-cables-dacs",
    "category/network-storage",
]

CATEGORY_LABELS = {
    "category/all-cloud-gateways":      "Cloud Gateways",
    "category/all-switching":           "Switching",
    "category/all-wifi":                "WiFi",
    "category/all-physical-security":   "Cameras & Physical Security",
    "category/all-door-access":         "Door Access",
    "category/all-integrations":        "Integrations",
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

# A store slug is lowercase letters, digits and hyphens. Anything else - from a
# hand-edited or shared watch list - would be pasted into store URLs.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,199}$", re.IGNORECASE)


def is_valid_slug(slug):
    return isinstance(slug, str) and bool(_SLUG_RE.match(slug)) and ".." not in slug


def store_home(region="us"):
    return f"{STORE_BASE}/{STORE_REGIONS.get(region, STORE_REGIONS['us'])['path']}"


def product_url(slug, region="us"):
    """The store page for a product, or the store's home for a bad slug."""
    if not is_valid_slug(slug):
        return store_home(region)
    return f"{store_home(region)}/products/{slug}"

# ── Default settings ─────────────────────────────────────────────────────────

POLL_MIN, POLL_MAX = 15, 3600
FONT_SIZES = (9, 10, 11, 12, 13, 14)
THEMES = ("system", "light", "dark")
PHONE_SERVICES = ("ntfy", "pushover", "discord", "slack", "webhook")   # webhooks.py

DEFAULT_SETTINGS = {
    "poll_interval":      60,
    "region":             "us",
    "notifications":      True,
    "sound_alerts":       True,
    "auto_open_url":      True,
    "auto_start":         False,    # start watching when the app opens
    "close_to_tray":      True,
    "check_updates":      True,
    "theme":              "system",
    "accent":             "",       # "" = Windows' accent colour
    "font_size":          10,
    "max_retries":        3,
    "window":             None,     # [x, y, w, h] in screen pixels
    "resume_watching":    False,    # set before an update restarts the app
    # Alerts on your phone (webhooks.py). Each service keeps its own address
    # or keys, so trying another one doesn't lose them.
    "phone_alerts":       False,
    "phone_service":      "ntfy",
    "ntfy_url":           "",
    "ntfy_token":         "",       # only for a protected topic
    "pushover_user":      "",
    "pushover_token":     "",
    "discord_url":        "",
    "slack_url":          "",
    "webhook_url":        "",
}

PHONE_TEXT_KEYS = ("ntfy_url", "ntfy_token", "pushover_user", "pushover_token",
                   "discord_url", "slack_url", "webhook_url")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_PRINTABLE_RE = re.compile(r"[^\x00-\x1f\x7f]{0,2048}")


def _valid_setting(key, value):
    default = DEFAULT_SETTINGS[key]
    if key == "poll_interval":
        return isinstance(value, int) and not isinstance(value, bool) \
            and POLL_MIN <= value <= POLL_MAX
    if key == "region":
        return value in STORE_REGIONS
    if key == "theme":
        return value in THEMES
    if key == "accent":
        return value == "" or (isinstance(value, str) and bool(_HEX_RE.match(value)))
    if key == "font_size":
        return value in FONT_SIZES
    if key == "phone_service":
        return value in PHONE_SERVICES
    if key in PHONE_TEXT_KEYS:
        # Checked properly when used (webhooks.problem); here only that it's
        # one line of text, so a damaged file can't put a header in a request.
        return isinstance(value, str) and bool(_PRINTABLE_RE.fullmatch(value))
    if key == "max_retries":
        return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 10
    if key == "window":
        return value is None or (
            isinstance(value, list) and len(value) == 4
            and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
            and value[2] > 0 and value[3] > 0)
    if isinstance(default, bool):
        return isinstance(value, bool)
    return isinstance(value, type(default))

# ── JSON persistence ─────────────────────────────────────────────────────────

def read_json(path, default):
    """Read a JSON file, returning `default` if it is missing or unreadable.

    Explicit UTF-8: the default is the locale codec, which on a Windows box is
    cp1252 and silently mangles any watch list produced elsewhere. utf-8-sig,
    because Windows PowerShell and some editors start a UTF-8 file with a
    byte-order mark, which json rejects: a hand-edited settings.json or watch
    list read as "unreadable", and the next save replaced it with defaults.
    """
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
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
    path.parent.mkdir(parents=True, exist_ok=True)
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
    """Defaults, overlaid with every stored value that is well-formed.

    A hand-edited or damaged settings.json must never stop the app starting:
    an unknown region used to raise KeyError on every store call, and a bad
    colour stopped the window opening at all. Bad values fall back to their
    default; keys this version does not know (2.x's colour scheme) are dropped.
    """
    merged = DEFAULT_SETTINGS.copy()
    stored = read_json(SETTINGS_FILE, {})
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in DEFAULT_SETTINGS and _valid_setting(key, value):
                merged[key] = value
            elif key in PHONE_TEXT_KEYS:         # addresses and keys are secrets
                log.warning("Ignoring invalid setting %s", key)
            elif key in DEFAULT_SETTINGS:
                log.warning("Ignoring invalid setting %s=%r", key, value)
        # 2.x let the interval be typed freely; keep a sane one rather than
        # discarding it.
        pi = stored.get("poll_interval")
        if isinstance(pi, int) and not isinstance(pi, bool) and not _valid_setting("poll_interval", pi):
            merged["poll_interval"] = min(max(pi, POLL_MIN), POLL_MAX)
    return merged


def save_settings(s):
    write_json(SETTINGS_FILE, {k: s[k] for k in DEFAULT_SETTINGS if k in s})


# ── HTTP session ─────────────────────────────────────────────────────────────

_session = None
_session_lock = threading.Lock()


def get_session():
    """Process-wide requests.Session.

    Every call previously opened a fresh connection, paying a full TCP and TLS
    handshake per request - nine of them per poll cycle, plus one per quick
    check. A pooled session reuses the connection and adds bounded retries for
    transient network and 5xx failures.
    """
    global _session
    with _session_lock:
        if _session is None:
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry

            s = requests.Session()
            s.headers.update(HEADERS)
            adapter = HTTPAdapter(
                pool_connections=4,
                pool_maxsize=CATEGORY_WORKERS * 2,
                max_retries=Retry(
                    total=2,
                    connect=2,
                    read=2,
                    status=2,
                    backoff_factor=0.5,
                    status_forcelist=(500, 502, 503, 504),
                    allowed_methods=frozenset(["GET"]),
                    raise_on_status=False,
                ),
            )
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            _session = s
        return _session


def close_session():
    """Release pooled connections (tests, and clean shutdown)."""
    global _session
    with _session_lock:
        if _session is not None:
            _session.close()
            _session = None


# ── Build ID cache ───────────────────────────────────────────────────────────

class BuildIdCache:
    """Cache the Next.js buildId per region, fetching it at most once at a time.

    Keyed by region: the id is currently identical across the four stores, but
    nothing in the API guarantees that, and serving a US id for an EU request
    would 404 every product on the first cycle after a region switch.

    The fetch happens under the lock so that a fleet of category workers all
    starting at once - or all reacting to the same rotation - makes one
    homepage request between them rather than one each.
    """

    def __init__(self, ttl_seconds=300):
        self._lock    = threading.Lock()
        self._entries = {}          # region -> (build_id, fetched_at)
        self._ttl     = ttl_seconds

    def _fresh(self, region):
        entry = self._entries.get(region)
        if not entry:
            return None
        build_id, fetched = entry
        if (datetime.now() - fetched).total_seconds() >= self._ttl:
            return None
        return build_id

    def get(self, region="us", force=False):
        with self._lock:
            if not force:
                cached = self._fresh(region)
                if cached:
                    return cached

            store_home = f"{STORE_BASE}/{STORE_REGIONS[region]['path']}"
            r = get_session().get(store_home, headers=HEADERS, timeout=15)
            r.raise_for_status()
            m = re.search(r'"buildId":"([^"]+)"', r.text)
            if not m:
                raise StoreError("Could not find buildId on store homepage.")

            build_id = m.group(1)
            self._entries[region] = (build_id, datetime.now())
            return build_id

    def invalidate(self, region=None):
        with self._lock:
            if region is None:
                self._entries.clear()
            else:
                self._entries.pop(region, None)


# Global instance
_build_cache = BuildIdCache()


def get_build_id(region="us", force=False):
    return _build_cache.get(region, force)


def invalidate_build_id(region=None):
    _build_cache.invalidate(region)


# ── Store API ────────────────────────────────────────────────────────────────

def discover_category_paths(region="us"):
    """Category paths linked from the store homepage.

    The store does not publish a category index as JSON, but the rendered
    homepage links every one of them.
    """
    region_path = STORE_REGIONS[region]["path"]
    r = get_session().get(f"{STORE_BASE}/{region_path}", timeout=15)
    r.raise_for_status()
    pattern = rf'/{re.escape(region_path)}/(category/[a-z0-9\-]+)'
    return sorted(set(re.findall(pattern, r.text)))


def effective_categories(region="us"):
    """The category list to fetch: the built-ins plus any learned extras."""
    cache = read_json(CATEGORY_CACHE_FILE, {})
    extras = cache.get("extra") if isinstance(cache, dict) else None
    extras = [c for c in (extras or []) if isinstance(c, str)]
    return list(dict.fromkeys(list(CATEGORIES) + extras))


def refresh_category_coverage(build_id, region="us", force=False):
    """Notice categories the store has that we do not, and adopt the useful ones.

    Renames already self-heal, because _fetch_category follows the store's
    redirects. This covers the other direction: a genuinely *new* category.

    Most discovered paths are sub-views that return their parent's full
    product set - all eight extras found at the time of writing added zero
    unique products - so adopting every discovered path would double the
    request count per cycle for nothing. Each unknown category is therefore
    fetched once, kept only if it contributes products the built-ins miss,
    and otherwise recorded as redundant so it is not re-examined.

    Runs at most once a day. Returns the list of newly adopted categories.
    """
    cache = read_json(CATEGORY_CACHE_FILE, {})
    if not isinstance(cache, dict):
        cache = {}
    checked = _parse_ts(cache.get("checked_at"))
    if not force and checked:
        age = (datetime.now(timezone.utc) - checked).total_seconds()
        if age < CATEGORY_DISCOVERY_TTL:
            return []

    try:
        discovered = discover_category_paths(region)
    except Exception as e:
        log.warning("Could not discover store categories: %s", e)
        return []
    if not discovered:
        log.warning("Category discovery found nothing; leaving the list alone")
        return []

    known = set(effective_categories(region))
    redundant = set(cache.get("redundant") or [])
    unknown = [c for c in discovered if c not in known and c not in redundant]

    adopted = []
    if unknown:
        covered = set()
        for cat in known:
            try:
                covered |= set(_fetch_category(build_id, region, cat))
            except Exception:
                pass                      # a broken built-in is reported elsewhere

        for cat in unknown:
            try:
                found = _fetch_category(build_id, region, cat)
            except Exception as e:
                log.warning("Could not examine new category %s: %s", cat, e)
                continue
            new_slugs = set(found) - covered
            if new_slugs:
                adopted.append(cat)
                covered |= new_slugs
                log.warning("Adopted new store category %s (+%d products)",
                            cat, len(new_slugs))
            else:
                redundant.add(cat)

    cache = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "extra": sorted(set(cache.get("extra") or []) | set(adopted)),
        "redundant": sorted(redundant),
        "seen": discovered,
    }
    try:
        write_json(CATEGORY_CACHE_FILE, cache)
    except OSError as e:
        log.warning("Could not save the category cache: %s", e)
    return adopted


def _fetch_category(build_id, region, cat):
    """Fetch one category page. Returns {slug: product}.

    Follows the store's own redirects. Categories get renamed - cameras moved
    from all-cameras-nvrs to all-physical-security - and the old path then
    answers HTTP 200 with a redirect directive and no products. Without this
    the whole category silently disappeared from the catalog: no error, no
    empty-result signal, just 135 products that stopped being watched.

    A 404 usually means the buildId rotated between the homepage fetch and
    this call, so refresh it once and retry with the new id.
    """
    path = f"/{STORE_REGIONS[region]['path']}/{cat}"
    found = {}
    refreshed = False

    for _ in range(MAX_REDIRECTS):
        try:
            r = get_session().get(_data_url(build_id, path), timeout=15)
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("Location") or r.headers.get("x-nextjs-redirect")
                if not loc:
                    raise StoreError(
                        f"{cat}: {r.status_code} with no redirect target")
                path = urlsplit(loc).path.removesuffix(".json")
                continue
            r.raise_for_status()
            page_props = r.json().get("pageProps", {})
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 404 and not refreshed:
                refreshed = True
                fresh = get_build_id(region, force=True)
                if fresh != build_id:
                    build_id = fresh
                    continue
            raise

        target = page_props.get("__N_REDIRECT")
        if target:
            path = urlsplit(target).path.removesuffix(".json")
            if "/category/" not in path:
                # Landing pages like whats-new redirect to the homepage. That
                # is not category drift, so it does not deserve a warning.
                log.debug("%s redirects out of the category space (%s)", cat, path)
                return {}
            log.warning("Category %s now redirects to %s - update CATEGORIES",
                        cat, path)
            continue

        for subcat in page_props.get("subCategories", []):
            for p in subcat.get("products", []):
                if p.get("slug"):
                    p["_category"] = cat
                    found[p["slug"]] = p
        # Belt-and-suspenders: a flat products list, in case UI ever switches a
        # category page to that shape. setdefault so the richer subCategory
        # entry wins if both exist.
        for p in page_props.get("products", []):
            if p.get("slug"):
                p.setdefault("_category", cat)
                found.setdefault(p["slug"], p)
        return found

    raise StoreError(f"{cat}: too many redirects (>{MAX_REDIRECTS})")


def fetch_all_products(build_id, region="us", progress_cb=None, error_cb=None):
    """Fetch every category, deduplicated by slug. Returns a list of dicts.

    progress_cb(int_0_to_100) is called as categories complete.
    error_cb(category_slug, exception) reports per-category failures so callers
    can surface them rather than losing them to stdout.

    The nine category pages are independent, so they are fetched concurrently.
    Sequentially, with a 0.3s pause between each, a cycle spent most of its time
    waiting on round trips it did not need to serialise.
    """
    products = {}
    done = 0
    with ThreadPoolExecutor(max_workers=CATEGORY_WORKERS,
                            thread_name_prefix="unifi-cat") as pool:
        cats = effective_categories(region)
        futures = {pool.submit(_fetch_category, build_id, region, cat): cat
                   for cat in cats}
        for fut in as_completed(futures):
            cat = futures[fut]
            try:
                products.update(fut.result())
            except Exception as e:
                log.warning("Category fetch failed: %s — %s", cat, e)
                if error_cb:
                    try:
                        error_cb(cat, e)
                    except Exception:
                        log.exception("error_cb raised")
            done += 1
            if progress_cb:
                try:
                    progress_cb(int(done / len(cats) * 100))
                except Exception:
                    log.exception("progress_cb raised")

    log.info("Fetched %d unique products across %d categories",
             len(products), len(cats))
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


def _is_unpriced(price_val):
    """True for a Money value of zero, which the store uses for "no price yet".

    Announced-but-unreleased products carry amount 0, and rendering that as
    "$0.00" reads as free rather than unpriced. Nothing in this store is
    legitimately free, so treating zero as absent is the safer reading.
    """
    if isinstance(price_val, dict):
        return not price_val.get("amount")
    if isinstance(price_val, (int, float)):
        return not price_val
    return False


def get_price(product):
    """Extract the display price from a product dict, or None if unpriced.

    displayPrice can be a Money dict like {'amount': 39900, 'currency': 'USD'}
    or a plain number or string.
    """
    for v in product.get("variants", []):
        price = v.get("displayPrice") or v.get("price")
        if price is not None and not _is_unpriced(price):
            return _format_price(price)
    return None


def is_coming_soon(product):
    """True when a product is announced but not yet purchasable.

    Distinct from sold out: a ComingSoon item has never been on sale, so
    "back ~" and "sold out for" would both be wrong.
    """
    statuses = {v.get("status") for v in product.get("variants", [])}
    return bool(statuses) and "Available" not in statuses and "ComingSoon" in statuses


# ── Restock estimates ────────────────────────────────────────────────────────
#
# The store already sends these on every catalog fetch and the app used to
# discard them. Roughly a third of out-of-stock products carry a restock date
# and half carry a sell-out timestamp, which is exactly what a restock watcher
# wants on screen.

def _parse_ts(value):
    """Parse an ISO timestamp or date, returning an aware datetime or None.

    Python 3.10's fromisoformat rejects a trailing Z, and CI covers 3.10.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def get_restock_eta(product):
    """Earliest restock estimate across variants, as a datetime, or None."""
    etas = [_parse_ts(v.get("restockEtaAt")) for v in product.get("variants", [])]
    etas = [d for d in etas if d]
    return min(etas) if etas else None


def get_sold_out_at(product):
    """Most recent sell-out timestamp across variants, as a datetime, or None."""
    vals = [_parse_ts(v.get("soldOutAt")) for v in product.get("variants", [])]
    vals = [d for d in vals if d]
    return max(vals) if vals else None


def _plural(n, unit):
    return f"{n} {unit}" if n == 1 else f"{n} {unit}s"


def describe_restock(product, now=None):
    """Short human phrase for a product's restock estimate, or None.

    e.g. "back ~7 Sep", "back ~tomorrow", "restock date passed".
    """
    eta = get_restock_eta(product)
    if eta is None:
        return None
    now = now or datetime.now(timezone.utc)
    days = (eta.date() - now.date()).days
    if days < 0:
        return "restock date passed"
    if days == 0:
        return "back ~today"
    if days == 1:
        return "back ~tomorrow"
    if days <= 14:
        return f"back ~{_plural(days, 'day')}"
    return f"back ~{eta.strftime('%d %b').lstrip('0')}"


def describe_sold_out_for(product, now=None):
    """Short human phrase for how long a product has been sold out, or None."""
    since = get_sold_out_at(product)
    if since is None:
        return None
    now = now or datetime.now(timezone.utc)
    # Calendar days, not elapsed 24-hour periods: something that sold out on
    # the 6th reads as "13 days" on the 19th, which is how people count, and
    # it matches describe_restock.
    days = (now.date() - since.date()).days
    if days < 0:
        return None
    if days == 0:
        return "sold out today"
    if days == 1:
        return "sold out 1 day"
    if days < 60:
        return f"sold out {days} days"
    return f"sold out {_plural(days // 30, 'month')}"


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
        r = get_session().get(_data_url(build_id, path), timeout=15)

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

    for p in candidates:                       # renamed product
        if slug in (p.get("historicalSlugs") or []):
            return p

    # Last resort. The page was reached by requesting this slug and following
    # the store's own redirects, so whatever it considers "current" is what the
    # slug resolves to. Checked last so an exact slug match always wins.
    current_id = page_props.get("currentProductId")
    if current_id:
        for p in candidates:
            if p.get("id") == current_id:
                return p
    return None


def check_slug(build_id, slug, region="us", retries=3):
    """Check a single product slug. Returns (in_stock: bool, price: str|None).

    Raises ProductNotFound if the slug no longer resolves, and StoreError if
    the store cannot be reached. It never reports a fabricated "out of stock" -
    that silently defeats the entire point of a stock watcher.
    """
    if not is_valid_slug(slug):
        raise ProductNotFound(f"{slug!r}: not a store product id")
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
        if not isinstance(item, dict) or not is_valid_slug(item.get("slug")):
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

MAX_HISTORY_EVENTS = 5000
HISTORY_VERSION = 2


def _empty_history():
    return {"version": HISTORY_VERSION, "events": [],
            "stats": {"total_checks": 0, "alerts": 0}}


def _valid_event(e):
    return (isinstance(e, dict) and isinstance(e.get("ts"), str)
            and isinstance(e.get("slug"), str) and isinstance(e.get("in_stock"), bool))


class StockHistory:
    """Stock changes, persisted to JSON for the History tab and stats.

    An event is a change: a product coming into or going out of stock, or a
    watched item's first state. 2.x appended one event per watched item per
    check, so 50 items polled every minute filled the 2000-event file with
    identical "out of stock" lines in 40 minutes and pushed every real change
    out of it. Checks are counted, not logged.

    Writes are deferred and batched; flush() runs on a timer and at shutdown.
    """

    def __init__(self, path=None, autosave_after=25):
        self._path = Path(path) if path is not None else HISTORY_FILE
        self._lock = threading.Lock()
        self._autosave_after = autosave_after
        self.reload()

    def reload(self):
        """Read the file again, dropping anything unsaved (after a migration
        has put a 2.x history in place under this instance)."""
        with self._lock:
            self._data = self._load()
            self._last = {}             # slug -> last recorded in_stock
            for e in self._data["events"]:
                self._last[e["slug"]] = e["in_stock"]
            self._dirty = False
            self._unsaved = 0

    def _load(self):
        data = read_json(self._path, None)
        if not isinstance(data, dict):
            return _empty_history()
        merged = _empty_history()
        events = [e for e in data.get("events") or [] if _valid_event(e)] \
            if isinstance(data.get("events"), list) else []
        stats = data.get("stats") if isinstance(data.get("stats"), dict) else {}
        if data.get("version") != HISTORY_VERSION:
            # 2.x: one event per check. Keep only the changes, and drop its
            # "in_stock_alerts", which counted in-stock checks, not alerts.
            last, kept = {}, []
            for e in events:
                if last.get(e["slug"]) != e["in_stock"]:
                    e.setdefault("watched", True)
                    kept.append(e)
                last[e["slug"]] = e["in_stock"]
            events = kept
            stats = {"total_checks": stats.get("total_checks", 0)}
        merged["events"] = events[-MAX_HISTORY_EVENTS:]
        for key in merged["stats"]:
            if isinstance(stats.get(key), int):
                merged["stats"][key] = stats[key]
        return merged

    def _save_locked(self):
        if len(self._data["events"]) > MAX_HISTORY_EVENTS:
            self._data["events"] = self._data["events"][-MAX_HISTORY_EVENTS:]
        write_json(self._path, self._data)
        self._dirty = False
        self._unsaved = 0

    def _touch_locked(self):
        self._dirty = True
        self._unsaved += 1
        if self._unsaved >= self._autosave_after:
            self._save_locked()

    def flush(self):
        """Persist pending changes. Safe to call when nothing has changed."""
        with self._lock:
            if self._dirty:
                self._save_locked()

    def record(self, slug, title, in_stock, price=None, watched=True):
        """Record a product's state; logged only when it differs from the
        last state recorded for that product. Returns True if logged."""
        with self._lock:
            if watched:
                self._data["stats"]["total_checks"] += 1
            if self._last.get(slug) == in_stock:
                if watched:
                    self._touch_locked()
                return False
            # Only a watched product's first state is a sighting; any other
            # product is recorded only when it changes.
            first = watched and slug not in self._last
            self._last[slug] = in_stock
            self._data["events"].append({
                "ts":       datetime.now().isoformat(timespec="seconds"),
                "slug":     slug,
                "title":    title,
                "in_stock": bool(in_stock),
                "price":    price,
                "watched":  bool(watched),
                "first":    first,      # a first sighting, not a change
            })
            self._touch_locked()
            return True

    # 2.x name, kept for scripts that drive the core directly.
    def record_check(self, slug, title, in_stock, price=None):
        return self.record(slug, title, in_stock, price, watched=True)

    def record_alert(self):
        with self._lock:
            self._data["stats"]["alerts"] += 1
            self._touch_locked()

    def get_stats(self):
        with self._lock:
            return self._data["stats"].copy()

    def get_events(self, slug=None, limit=50, watched_only=False):
        with self._lock:
            events = self._data["events"]
            if slug:
                events = [e for e in events if e.get("slug") == slug]
            if watched_only:
                events = [e for e in events if e.get("watched")]
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
            self._last = {}
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
        added_at = item["added_at"]
        existing.append({
            "slug":      item["slug"],
            "title":     str(item["title"])[:200],
            "favourite": bool(item["favourite"]),
            "price":     item["price"][:40] if isinstance(item["price"], str) else None,
            "added_at":  added_at[:40] if isinstance(added_at, str) and added_at
                         else datetime.now().isoformat(),
        })
        slugs.add(item["slug"])
        added += 1
    save_watched(existing)
    return added
