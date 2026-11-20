"""
Unifi Stock Watcher — CLI
Headless console watcher using the shared core module.

Commands:
  python unifi_watcher.py           -- run watcher (setup on first run)
  python unifi_watcher.py --setup   -- re-run product picker
  python unifi_watcher.py --test    -- verify notifications + stock detection
  python unifi_watcher.py --version -- print the version and config directory
"""

import sys
import time
import webbrowser
from datetime import datetime

from _bootstrap import ensure_requests, missing_message

# Never pip-install from the frozen build: its sys.executable is this program,
# so "-m pip" would start another copy of it.
if not ensure_requests(auto_install=not getattr(sys, "frozen", False)):
    print(missing_message())
    sys.exit(1)

from unifi_core import (
    __version__, APP_NAME, BASE_DIR, STORE_REGIONS,
    ProductNotFound, StoreError,
    load_settings, get_build_id, fetch_all_products, is_refusal,
    refresh_category_coverage, product_url, is_portable,
    is_available, get_price, price_amount, product_title, check_slug,
    load_watched, save_watched, stock_history,
    wants_alert, notify_windows, play_sound, migrate_legacy_files, setup_logging,
)
import webhooks
import winplatform


# ── Interactive product picker ────────────────────────────────────────────────

def run_setup():
    settings = load_settings()
    region   = settings.get("region", "us")

    print("=" * 60)
    print(f"  {APP_NAME} v{__version__} — Product Picker")
    print(f"  Region: {STORE_REGIONS[region]['label']}")
    print("=" * 60)
    print()
    print("Fetching out-of-stock items from the Unifi store...")
    print("(This takes about 10 seconds)\n")

    try:
        build_id = get_build_id(region)
        all_products = fetch_all_products(build_id, region)
    except Exception as e:
        print(f"ERROR: Could not reach the store: {e}")
        input("\nPress Enter to exit.")
        sys.exit(1)

    out_of_stock = sorted(
        [p for p in all_products if not is_available(p)],
        key=lambda p: product_title(p).lower()
    )

    if not out_of_stock:
        print("Everything appears to be in stock right now!")
        input("\nPress Enter to exit.")
        sys.exit(0)

    print(f"Found {len(out_of_stock)} out-of-stock items:\n")
    print(f"  {'#':<5} {'Product Name':<45} {'Price'}")
    print("  " + "-" * 60)
    for i, p in enumerate(out_of_stock, 1):
        price = get_price(p) or ""
        print(f"  {i:<5} {product_title(p):<45} {price}")

    print()
    print("Enter the numbers of items you want to watch,")
    print("separated by commas.  Example: 1, 4, 7")
    print()

    while True:
        raw = input("Your selection: ").strip()
        if not raw:
            print("Please enter at least one number.")
            continue
        try:
            picks = [int(x.strip()) for x in raw.split(",")]
            if all(1 <= p <= len(out_of_stock) for p in picks):
                break
            print(f"Please enter numbers between 1 and {len(out_of_stock)}.")
        except ValueError:
            print("Invalid input — please enter numbers separated by commas.")

    watched = []
    for i in picks:
        p = out_of_stock[i - 1]
        watched.append({
            "title":     product_title(p)[:200],
            "slug":      p["slug"],
            "favourite": False,
            "price":     get_price(p),
            "added_at":  datetime.now().isoformat(),
        })

    save_watched(watched)

    print()
    print("=" * 60)
    print("  Watching these items:")
    for w in watched:
        price_str = f" ({w['price']})" if w.get("price") else ""
        print(f"    - {w['title']}{price_str}")
    print()
    print(f"  Saved to {BASE_DIR / 'watched_items.json'}")
    print("=" * 60)
    print()

    return watched


# ── Self-test ─────────────────────────────────────────────────────────────────

def test_mode():
    settings = load_settings()
    region   = settings.get("region", "us")

    print("=" * 60)
    print(f"  {APP_NAME} v{__version__} — TEST MODE")
    print("=" * 60)
    print()

    print("Step 1/3  Firing a test notification…")
    print("          Watch the bottom-right corner of your screen.")
    notify_windows(
        f"TEST: {APP_NAME} works!",
        "This is exactly what an in-stock alert looks like."
    )
    if settings.get("sound_alerts", True):
        play_sound()
    print("          Done. Did a pop-up appear?\n")
    if settings.get("phone_alerts"):
        where = webhooks.label(settings)
        print(f"          Sending a test alert to {where}…")
        reason = webhooks.send(settings, webhooks.test_alert(region))
        print(f"          {'FAIL — ' + reason if reason else 'Sent. Is it on your phone?'}\n")
    time.sleep(2)

    IN_STOCK_TEST = {"title": "Access Point U7 Pro", "slug": "u7-pro"}
    print("Step 2/3  Verifying stock detection…")
    print(f"          Checking: {IN_STOCK_TEST['title']}")
    try:
        build_id = get_build_id(region)
        in_stock, price = check_slug(build_id, IN_STOCK_TEST["slug"], region)
        price_str = f" ({price})" if price else ""
        if in_stock:
            print(f"          PASS — detected as IN STOCK{price_str}.\n")
        else:
            print(f"          WARNING — showed as out of stock{price_str}.\n")
    except Exception as e:
        print(f"          FAIL — could not reach the store: {e}\n")
    time.sleep(1)

    watched = load_watched()
    if watched:
        print("Step 3/3  Checking your watched items…")
        try:
            build_id = get_build_id(region)
            for item in watched:
                in_stock, price = check_slug(build_id, item["slug"], region)
                price_str = f" ({price})" if price else ""
                if in_stock:
                    note = f"IN STOCK{price_str} — you'd get a notification now!"
                else:
                    note = f"Out of stock{price_str} — watcher will alert you."
                print(f"          {item['title']}: {note}")
        except Exception as e:
            print(f"          Could not check: {e}")
        print()
    else:
        print("Step 3/3  No watched items configured yet — run --setup first.\n")

    print("=" * 60)
    print("  Test complete. Run without --test to start watching.")
    print("=" * 60)


# ── Main watcher loop ─────────────────────────────────────────────────────────

DELISTED_RECHECK = 6 * 3600     # seconds before a delisted product's page is asked again
MAX_BACKOFF = 1800              # the longest wait while the store is out
PHONE_QUIT_WAIT = 3             # seconds a stop waits for phone alerts still being sent

def price_changed(settings, phone, phone_result, item, was, now, region):
    """A watched product's price changed: say so, and alert if it dropped and
    the settings ask for that."""
    old, new = price_amount(was), price_amount(now)
    down = old is not None and new is not None and new < old
    # "->", not an arrow: redirected output (Task Scheduler) is in the locale's
    # code page, which may not have one.
    print(f"          {'PRICE DOWN' if down else 'price changed':<16}  {item['title']}: {was} -> {now}")
    if not (down and settings.get("price_alerts") and wants_alert(settings, item)):
        return
    alert = webhooks.price_drop_alert(item["title"], item["slug"], region, now, was,
                                      item.get("favourite", False))
    notify_windows(alert.title, alert.text)
    if settings.get("phone_alerts") and wants_alert(settings, item, phone=True):
        phone.submit(settings, alert, on_result=phone_result)


def main():
    settings = load_settings()
    region   = settings.get("region", "us")
    interval = settings.get("poll_interval", 60)

    watched = load_watched()
    if not watched:
        print("No watched items found — let's pick some now.\n")
        watched = run_setup()
        print("Starting watcher in 3 seconds…\n")
        time.sleep(3)

    print("=" * 60)
    print(f"  {APP_NAME} v{__version__} — Running")
    print(f"  Region: {STORE_REGIONS[region]['label']}  ·  Interval: {interval}s")
    for w in watched:
        price_str = f" ({w.get('price', '')})" if w.get("price") else ""
        print(f"    - {w['title']}{price_str}")
    print("  Press Ctrl+C to stop.")
    print("=" * 60)
    print()

    watcher = Watcher(settings, watched)
    try:
        while True:
            time.sleep(watcher.cycle())
    finally:
        # Stopped (Ctrl+C): an alert raised a moment ago gets a few seconds
        # to go out, as when the app quits.
        if not watcher.phone.wait(PHONE_QUIT_WAIT):
            print("          WARNING  stopped with phone alerts still unsent")


class Watcher:
    """The watch loop's state. cycle() is one check of the store and returns
    how long to wait before the next; the app's _cycle does the same work."""

    def __init__(self, settings, watched, phone=None):
        self.settings = settings
        self.region = settings.get("region", "us")
        self.interval = settings.get("poll_interval", 60)
        self.watched = watched
        # Already alerted if the history last saw it in stock, so a restart
        # doesn't alert again for everything that's in stock (see the app's
        # _seed_notified); one that came back meanwhile still alerts.
        self.notified = {w["slug"]: stock_history.last_state(w["slug"]) is True
                         for w in watched}
        self.delisted = {}      # slug -> when its own page last said it's gone
        self.failures = 0
        self.partial = None     # the categories the last check couldn't read
        self.phone = phone if phone is not None else webhooks.Sender()

    def phone_result(self, where, reason):
        if reason:
            print(f"          WARNING  phone alert to {where} not sent: {reason}")
        else:
            print(f"          >>> Sent to {where}")

    def _backoff(self):
        """Out or refusing: wait longer each time, up to half an hour."""
        self.failures += 1
        return min(self.interval * 2 ** min(self.failures, 5),
                   max(self.interval, MAX_BACKOFF))

    def cycle(self):
        now = datetime.now().strftime("%H:%M:%S")
        failed = {}                 # category -> its error
        try:
            build_id = get_build_id(self.region)
            for cat in refresh_category_coverage(build_id, self.region):
                print(f"          Store added a category - now watching {cat}")
            # One catalog fetch per cycle covers every watched item. Asking
            # per item cost one request each - unthrottled, and growing with
            # the watch list - for data the catalog already carries.
            catalog = {p["slug"]: p for p in fetch_all_products(
                           build_id, self.region,
                           error_cb=lambda cat, e: failed.__setitem__(cat, e))
                       if p.get("slug")}
        except Exception as e:
            wait = self._backoff()
            print(f"[{now}]  WARNING: could not reach the store: {e} "
                  f"(trying again in {wait}s)")
            return wait

        print(f"[{now}]  Checking…")
        # Rate-limited or overloaded (429/503 on a category): the products it
        # didn't send aren't asked for one by one, and the next check waits
        # longer, as when the store is out. Until 3.2.6 every watched product
        # missing from the catalog was asked for with full retries, each check.
        refusing = any(is_refusal(e) for e in failed.values())
        names = ", ".join(sorted(failed)) or None
        if names and names != self.partial:
            print(f"          WARNING  couldn't read {len(failed)} of the store's "
                  f"categories ({names}); checked the rest")
        self.partial = names

        for item in self.watched:
            # One product in an odd shape (or any other surprise) is
            # reported and skipped: it used to end the watcher.
            try:
                self.check(item, catalog, build_id, incomplete=bool(failed),
                           refusing=refusing)
            except Exception as e:
                print(f"          WARNING  {item['title']}: couldn't check it ({e})")

        try:
            stock_history.flush()
        except OSError as e:
            print(f"          WARNING  couldn't save the history: {e}")
        if refusing:
            wait = self._backoff()
            print(f"          WARNING  the store is refusing requests: next check in {wait}s\n")
            return wait
        self.failures = 0
        print(f"          Next check in {self.interval}s\n")
        return self.interval

    def check(self, item, catalog, build_id, incomplete=False, refusing=False):
        settings, region = self.settings, self.region
        slug, title = item["slug"], item["title"]
        product = catalog.get(slug)
        if product is None:
            if refusing:
                return
            if time.monotonic() - self.delisted.get(slug, -DELISTED_RECHECK) \
                    < DELISTED_RECHECK:
                return
        try:
            if product is not None:
                in_stock, price = is_available(product), get_price(product)
            else:
                # Not in the catalog: delisted, or a product the category
                # pages don't carry. Asked once if the catalog was incomplete.
                in_stock, price = check_slug(
                    build_id, slug, region,
                    retries=1 if incomplete else settings.get("max_retries", 3))
        except ProductNotFound:
            if slug not in self.delisted:
                print(f"          {'DELISTED':<16}  {title} — no longer on the store")
            self.delisted[slug] = time.monotonic()
            return
        except StoreError as e:
            print(f"          WARNING  {title}: {e}")
            return
        if self.delisted.pop(slug, None) is not None:
            print(f"          {'BACK':<16}  {title} is on the store again")

        price_str = f" ({price})" if price else ""
        event = stock_history.record(slug, title, in_stock, price)
        label = "IN STOCK" if in_stock else "out of stock"
        print(f"          {label:<16}  {title}{price_str}")
        if event and event.get("price_was"):
            price_changed(settings, self.phone, self.phone_result, item,
                          event["price_was"], price, region)

        if in_stock and not self.notified.get(slug) and not wants_alert(settings, item):
            self.notified[slug] = True
            print(f"          (no alert: {'muted' if item.get('muted') else 'favorites only'})")
        elif in_stock and not self.notified.get(slug):
            notify_windows(
                f"IN STOCK: {title}",
                f"{title} is now available on the Unifi store!"
            )
            if settings.get("sound_alerts", True):
                play_sound()
            if settings.get("phone_alerts") and wants_alert(settings, item, phone=True):
                self.phone.submit(settings, webhooks.in_stock_alert(
                    title, slug, region, price, item.get("favourite", False)),
                    on_result=self.phone_result)
            if settings.get("auto_open_url", True):
                webbrowser.open(product_url(slug, region))
            stock_history.record_alert()
            self.notified[slug] = True
            print("          >>> Notification sent!")
        elif not in_stock:
            self.notified[slug] = False


# ── Entry point ───────────────────────────────────────────────────────────────

ALREADY_RUNNING = {
    "app": f"{APP_NAME} is already running (the app: its icon is in the notification area).\n"
           "One copy watches at a time: quit the app first (right-click its icon, Quit).",
    "cli": "Another command-line watcher is already running. One copy watches at a time.",
    None: f"Another copy of {APP_NAME} is already running (it may still be starting).\n"
          "One copy watches at a time.",
}


def run(argv):
    if "--version" in argv or "-V" in argv:
        # Also prints where runtime files live: that answers the common
        # "where are my settings?" question, and it is what the release
        # smoke test checks, since a frozen build that resolves its paths
        # into PyInstaller's temp directory loses the watch list silently.
        print(f"{APP_NAME} {__version__}")
        print(f"config directory: {BASE_DIR}")
        print(f"portable: {'yes' if is_portable() else 'no'}")
        return 0
    setup_logging(console=True)
    # One copy at a time, the app included (winplatform.SingleInstance): two
    # would alert twice, and the installer and the updater wait for this lock
    # before they replace the program's files. --test watches nothing and
    # writes nothing, so it runs beside the app.
    lock = None
    if "--test" not in argv:
        lock = winplatform.SingleInstance(cli=True)
        if lock.already_running:
            other = lock.wait_for_other(timeout=5)
            if other != "gone":
                print(ALREADY_RUNNING[other])
                return 1
    migrate_legacy_files()
    try:
        if "--test" in argv:
            test_mode()
        elif "--setup" in argv:
            run_setup()
            print("Starting watcher in 3 seconds…\n")
            time.sleep(3)
            main()
        else:
            main()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        stock_history.flush()
        if lock:
            lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
