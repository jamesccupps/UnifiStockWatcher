"""
Unifi Stock Watcher — CLI  (v2.0)
Headless console watcher using the shared core module.

Commands:
  python unifi_watcher.py           -- run watcher (setup on first run)
  python unifi_watcher.py --setup   -- re-run product picker
  python unifi_watcher.py --test    -- verify notifications + stock detection
"""

import sys
import time
import webbrowser
from datetime import datetime

from unifi_core import (
    REQUESTS_OK, STORE_BASE, STORE_REGIONS, CATEGORIES, CATEGORY_LABELS,
    load_settings, get_build_id, fetch_all_products,
    is_available, get_price, check_slug,
    load_watched, save_watched, stock_history,
    notify_windows, play_sound,
)


def store_home(region="us"):
    return f"{STORE_BASE}/{STORE_REGIONS[region]['path']}"


# ── Interactive product picker ────────────────────────────────────────────────

def run_setup():
    settings = load_settings()
    region   = settings.get("region", "us")

    print("=" * 60)
    print("  Unifi Stock Watcher v2.0 — Product Picker")
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
        key=lambda p: p["title"]
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
        print(f"  {i:<5} {p['title']:<45} {price}")

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
            "title":     p["title"],
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
    print(f"  Saved to watched_items.json")
    print("=" * 60)
    print()

    return watched


# ── Self-test ─────────────────────────────────────────────────────────────────

def test_mode():
    settings = load_settings()
    region   = settings.get("region", "us")

    print("=" * 60)
    print("  Unifi Stock Watcher v2.0 — TEST MODE")
    print("=" * 60)
    print()

    print("Step 1/3  Firing a test notification…")
    print("          Watch the bottom-right corner of your screen.")
    notify_windows(
        "TEST: Unifi Stock Watcher works!",
        "This is exactly what an in-stock alert looks like."
    )
    if settings.get("sound_alerts", True):
        play_sound()
    print("          Done. Did a pop-up appear?\n")
    time.sleep(2)

    IN_STOCK_TEST = {"title": "Access Point U7 Pro", "slug": "u7-pro"}
    print(f"Step 2/3  Verifying stock detection…")
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
    print("  Unifi Stock Watcher v2.0 — Running")
    print(f"  Region: {STORE_REGIONS[region]['label']}  ·  Interval: {interval}s")
    for w in watched:
        price_str = f" ({w.get('price', '')})" if w.get("price") else ""
        print(f"    - {w['title']}{price_str}")
    print("  Press Ctrl+C to stop.")
    print("=" * 60)
    print()

    notified = {w["slug"]: False for w in watched}

    while True:
        now = datetime.now().strftime("%H:%M:%S")
        try:
            build_id = get_build_id(region)
        except Exception as e:
            print(f"[{now}]  WARNING: Could not get build ID: {e}")
            time.sleep(interval)
            continue

        print(f"[{now}]  Checking…")
        for item in watched:
            slug, title = item["slug"], item["title"]
            try:
                in_stock, price = check_slug(build_id, slug, region)
                price_str = f" ({price})" if price else ""

                stock_history.record_check(slug, title, in_stock, price)

                if in_stock:
                    print(f"          {'IN STOCK':<16}  {title}{price_str}")
                else:
                    print(f"          {'out of stock':<16}  {title}{price_str}")

                if in_stock and not notified[slug]:
                    notify_windows(
                        f"IN STOCK: {title}",
                        f"{title} is now available on the Unifi store!"
                    )
                    if settings.get("sound_alerts", True):
                        play_sound()
                    if settings.get("auto_open_url", True):
                        webbrowser.open(f"{store_home(region)}/products/{slug}")
                    notified[slug] = True
                    print("          >>> Notification sent!")
                elif not in_stock:
                    notified[slug] = False
            except Exception as e:
                print(f"          WARNING  {title}: {e}")

        print(f"          Next check in {interval}s\n")
        time.sleep(interval)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not REQUESTS_OK:
        print("Installing requests…")
        import subprocess
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "requests", "--quiet"])

    if "--test" in sys.argv:
        test_mode()
    elif "--setup" in sys.argv:
        run_setup()
        print("Starting watcher in 3 seconds…\n")
        time.sleep(3)
        main()
    else:
        try:
            main()
        except KeyboardInterrupt:
            print("\nStopped.")
