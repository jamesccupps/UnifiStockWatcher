"""The real app against a pretend store, for trying the UI and for screenshots.

    python scripts/demo.py                 # run the app on a sample catalog
    python scripts/demo.py --shots DIR     # take the README/site pictures, then quit

Nothing here touches the real store or your own files: the data folder is a
fresh temporary one, the store is a stub session serving a sample catalog
(real product names, made-up stock, prices and dates), and stock flips between
checks so the changes feed has something to show. Phone alerts are printed,
never sent. --shots captures windows
with PrintWindow, which works while they are covered (Windows only).
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["USW_DATA_DIR"] = tempfile.mkdtemp(prefix="usw-demo-")

import unifi_core  # noqa: E402
import webhooks  # noqa: E402


def _pretend_post(url, body, headers):
    """Phone alerts are pretend too: the demo's stock flips would otherwise
    reach a real phone, as made-up restocks."""
    print(f"demo: not sending {body.get('title') or body.get('content', '')[:60]!r} "
          f"to {urlsplit(url).hostname}")
    return _Resp()


webhooks.http_post = _pretend_post

NOW = datetime.now(timezone.utc)
CATALOG = {
    "category/all-cloud-gateways": [
        ("udm-pro-max", "Dream Machine Pro Max", 59900, "SoldOut", 9, 13),
        ("ucg-ultra", "Cloud Gateway Ultra", 12900, "Available", None, None),
        ("ucg-max", "Cloud Gateway Max", 27900, "Available", None, None),
        ("udr7", "Dream Router 7", 27900, "SoldOut", None, 4),
        ("ucg-fiber", "Cloud Gateway Fiber", 27900, "Available", None, None),
        ("uxg-lite", "Gateway Lite", 12900, "Available", None, None),
    ],
    "category/all-switching": [
        ("usw-pro-max-24-poe", "Switch Pro Max 24 PoE", 79900, "SoldOut", 21, 30),
        ("usw-pro-hd-24-poe", "Switch Pro HD 24 PoE", 119900, "Available", None, None),
        ("usw-lite-8-poe", "Switch Lite 8 PoE", 10900, "Available", None, None),
        ("usw-flex-mini", "Switch Flex Mini", 2900, "SoldOut", 3, 2),
        ("usw-ultra", "Switch Ultra", 12900, "Available", None, None),
        ("usw-enterprise-8-poe", "Switch Enterprise 8 PoE", 39900, "Available", None, None),
        ("usw-aggregation", "Switch Aggregation", 26900, "SoldOut", None, 41),
    ],
    "category/all-wifi": [
        ("u7-pro", "U7 Pro", 18900, "Available", None, None),
        ("u7-pro-max", "U7 Pro Max", 27900, "SoldOut", 6, 11),
        ("u7-lite", "U7 Lite", 9900, "Available", None, None),
        ("u7-pro-xg", "U7 Pro XG", 19900, "SoldOut", None, None),
        ("u7-outdoor", "U7 Outdoor", 19900, "Available", None, None),
        ("ux7", "UniFi Express 7", 19900, "SoldOut", 2, 8),
        ("u7-in-wall", "U7 In-Wall", 14900, "Available", None, None),
        ("e7", "E7", 0, "ComingSoon", 35, None),
    ],
    "category/all-physical-security": [
        ("uvc-g6-bullet", "G6 Bullet", 19900, "SoldOut", 12, 19),
        ("uvc-g6-turret", "G6 Turret", 19900, "Available", None, None),
        ("uvc-g6-instant", "G6 Instant", 12900, "Available", None, None),
        ("uvc-g5-flex", "G5 Flex", 12900, "Available", None, None),
        ("uvc-ai-pro", "AI Pro", 49900, "SoldOut", None, 6),
        ("uvc-g4-doorbell-pro", "G4 Doorbell Pro", 29900, "Available", None, None),
        ("uvc-ai-360", "AI 360", 39900, "Available", None, None),
        ("unvr-pro", "Network Video Recorder Pro", 49900, "SoldOut", 15, 27),
    ],
    "category/all-door-access": [
        ("ua-g3-reader", "Access Reader G3", 14900, "Available", None, None),
        ("ua-hub", "Access Hub", 19900, "Available", None, None),
    ],
    "category/network-storage": [
        ("unas-pro", "UNAS Pro", 49900, "SoldOut", 18, 22),
    ],
    "category/accessories-cables-dacs": [
        ("uacc-dac-sfp10-0-5m", "10 Gbps SFP+ Direct Attach Cable, 0.5 m", 1200, "Available", None, None),
        ("uacc-rack-panel", "Rack Mount Blank Panel", 1900, "Available", None, None),
    ],
}

WATCH = [("udm-pro-max", True), ("usw-flex-mini", True), ("usw-pro-max-24-poe", False),
         ("u7-pro-max", False), ("ux7", False), ("uvc-g6-bullet", False),
         ("unas-pro", False), ("e7", False)]

# What comes back (or sells out) between the first check and the second.
FLIPS = {"usw-flex-mini": "Available", "u7-pro": "SoldOut", "uvc-ai-pro": "Available",
         "usw-ultra": "SoldOut", "ucg-max": "SoldOut", "u7-pro-xg": "Available"}

STATE = {"cycle": 0}


def _product(slug, title, amount, status, eta_days, sold_days):
    v = {"status": status, "displayPrice": {"amount": amount, "currency": "USD"}}
    if eta_days is not None:
        v["restockEtaAt"] = (NOW + timedelta(days=eta_days)).isoformat()
    if sold_days is not None:
        v["soldOutAt"] = (NOW - timedelta(days=sold_days)).isoformat()
    return {"slug": slug, "title": title, "id": f"id-{slug}", "variants": [v]}


class _Resp:
    def __init__(self, data=None, text=""):
        self.status_code, self._data, self.text, self.headers = 200, data, text, {}

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


class DemoSession:
    def get(self, url, **kw):
        if "/_next/data/" not in url:
            links = " ".join(f'href="/us/en/{c}"' for c in CATALOG)
            return _Resp(text=f'<script>{{"buildId":"demo"}}</script> {links}')
        cat = url.split("/us/en/", 1)[1].removesuffix(".json")
        if cat == "category/all-cloud-gateways":
            STATE["cycle"] += 1          # one fetch of this category per check
        prods = []
        for row in CATALOG.get(cat, []):
            slug, title, amount, status, eta, sold = row
            if STATE["cycle"] >= 2 and slug in FLIPS:
                status = FLIPS[slug]
                eta = sold = None if status == "Available" else eta
                if status == "SoldOut":
                    sold = 0
            prods.append(_product(slug, title, amount, status, eta, sold))
        return _Resp({"pageProps": {"subCategories": [{"products": prods}]}})


def seed(theme="system"):
    titles = {r[0]: r[1] for rows in CATALOG.values() for r in rows}
    prices = {r[0]: r[2] for rows in CATALOG.values() for r in rows}
    unifi_core.write_json(unifi_core.CONFIG_FILE, [
        {"slug": s, "title": titles[s], "favourite": fav,
         "price": f"${prices[s] / 100:,.2f}" if prices[s] else None,
         "added_at": NOW.isoformat()} for s, fav in WATCH])
    unifi_core.write_json(unifi_core.SETTINGS_FILE, {
        "auto_open_url": False, "sound_alerts": False, "notifications": False,
        "check_updates": False, "theme": theme})


def capture(win, path):
    """Window (with its title bar) to PNG, by PrintWindow, in two steps.

    On a locked or hidden desktop Windows sends no WM_PAINT for what moved,
    and a capture keeps stale pixels; so everything is invalidated first.
    Tk answers WM_PAINT with Expose *events*, drawn by the event loop (not by
    update_idletasks, and update() would run the next steps' timers inside
    this one), so the picture is taken a moment later.
    """
    import ctypes
    hwnd = int(win.wm_frame(), 16)
    ctypes.windll.user32.RedrawWindow(hwnd, None, None, 0x1 | 0x4 | 0x80 | 0x400)
    win.after(600, lambda: _grab(win, path))


def _grab(win, path):
    import ctypes
    from ctypes import wintypes
    from PIL import Image
    win.update_idletasks()
    hwnd = int(win.wm_frame(), 16)
    u32, g32 = ctypes.windll.user32, ctypes.windll.gdi32
    rect, crect = wintypes.RECT(), wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(rect))
    u32.GetClientRect(hwnd, ctypes.byref(crect))
    pt = wintypes.POINT(0, 0)
    u32.ClientToScreen(hwnd, ctypes.byref(pt))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    hdc = u32.GetWindowDC(hwnd)
    mdc = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    g32.SelectObject(mdc, bmp)
    u32.PrintWindow(hwnd, mdc, 2)                  # PW_RENDERFULLCONTENT

    class BMI(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("rest", wintypes.DWORD * 5)]
    bmi = BMI(biSize=ctypes.sizeof(BMI), biWidth=w, biHeight=-h, biPlanes=1, biBitCount=32)
    buf = ctypes.create_string_buffer(w * h * 4)
    g32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    g32.DeleteObject(bmp)
    g32.DeleteDC(mdc)
    u32.ReleaseDC(hwnd, hdc)
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    side = pt.x - rect.left                        # invisible resize border
    img.crop((side, 1, w - side, pt.y - rect.top + crect.bottom)).save(path)
    print("wrote", path)


SHOTS = [
    # (file, theme, tab)
    ("watch-list-dark.png", "dark", "watch"),
    ("watch-list-light.png", "light", "watch"),
    ("history-light.png", "light", "history"),
    ("settings-dark.png", "dark", "settings"),
    ("add-products-dark.png", "dark", "browse"),
]


def take(out, name, theme, tab):
    """One picture, in a process of its own: the window is built in its final
    theme before it is first shown, and nothing moves once it is."""
    import unifi_watcher_gui as gui
    seed(theme)
    unifi_core.get_session = lambda: DemoSession()
    app = gui.UnifiWatcherApp(use_tray=False, check_updates=False)
    S = app.theme.S
    app.geometry(f"{S(820)}x{S(760)}+40+40")
    if tab in ("history", "settings"):
        app._show_tab(tab)                     # laid out before it is first shown
    steps = [(300, app._force_check), (1500, app._force_check)]

    def settle():
        app._refresh_list()
        app._set_bottom("changes")
        app.watching = True                    # the header as it looks while watching
        app._set_status("Watching", "green")
        app._set_countdown(57)
        app.start_btn.config(text="Stop watching", style="TButton")
        if tab == "history":
            app._history_widget.watched_only.set(False)
            app._history_widget.refresh()
    steps.append((2500, settle))
    target = [app]
    if tab == "browse":
        def browse():
            app._open_browse()
            d = [w for w in app.winfo_children() if isinstance(w, gui.BrowseDialog)][-1]
            d.geometry(f"{S(900)}x{S(620)}+80+80")
            target[0] = d
        steps.append((300, browse))

        def tick():
            d = target[0]
            d._stock_var.set(True)
            d._filter()
            for slug in ("u7-pro-max", "uvc-ai-pro", "usw-aggregation"):
                d._toggle(slug)
        steps.append((2000, tick))
    steps.append((1500, lambda: capture(target[0], out / name)))
    steps.append((1500, lambda: (setattr(app, "watching", False), app.quit_app())))
    t = 0
    for ms, fn in steps:
        t += ms
        app.after(t, fn)
    app.mainloop()


def main():
    if "--take" in sys.argv:
        i = sys.argv.index("--take")
        out, name, theme, tab = sys.argv[i + 1:i + 5]
        take(Path(out), name, theme, tab)
        return
    if "--shots" in sys.argv:
        import subprocess
        out = Path(sys.argv[sys.argv.index("--shots") + 1])
        out.mkdir(parents=True, exist_ok=True)
        for name, theme, tab in SHOTS:
            subprocess.run([sys.executable, __file__, "--take", str(out), name, theme, tab],
                           check=True, timeout=120)
        return
    import unifi_watcher_gui as gui
    seed()
    unifi_core.get_session = lambda: DemoSession()
    app = gui.UnifiWatcherApp(check_updates=False)
    app.after(300, app._toggle_watch)
    app.mainloop()


if __name__ == "__main__":
    main()
