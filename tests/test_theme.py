"""Drawing, colours, the theme's palette, and the Windows integration that
can be checked without touching the real registry or tray."""

import struct
import sys
import types
import zlib

import pytest

import gfx
import theme
import winplatform


# ── gfx ──────────────────────────────────────────────────────────────────────

def _png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", data[16:24])
    return w, h


def test_png_is_valid_and_carries_alpha():
    r = gfx.Raster(10, 6).draw(gfx.sd_circle(5, 3, 2), "#ff0000")
    data = r.png()
    assert _png_size(data) == (10, 6)
    idat = data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8]
    raw = zlib.decompress(idat)
    assert len(raw) == 6 * (1 + 10 * 4)          # filter byte + RGBA per row
    corner = raw[1:5]
    assert corner[3] == 0                         # outside the circle: clear


def test_antialiased_edges():
    r = gfx.Raster(20, 20).draw(gfx.sd_rrect(2, 2, 18, 18, 5), "#000000")
    alphas = {round(r.px[i + 3], 2) for i in range(0, len(r.px), 4)}
    assert 0.0 in alphas and 1.0 in alphas
    assert any(0 < a < 1 for a in alphas)        # partial coverage on the curve


def test_ico_lists_every_size():
    pngs = [(n, gfx.Raster(n, n).png()) for n in (16, 32, 256)]
    data = gfx.ico_bytes(pngs)
    _, kind, count = struct.unpack("<HHH", data[:6])
    assert (kind, count) == (1, 3)
    sizes = [data[6 + 16 * i] for i in range(3)]
    assert sizes == [16, 32, 0]                   # 0 means 256


def test_contrast_and_mix():
    assert round(gfx.contrast("#000000", "#ffffff"), 1) == 21.0
    assert gfx.mix("#000000", "#ffffff", 0.5) == "#808080"


def test_the_app_icon_draws_at_every_size():
    for n in (16, 24, 32, 48, 256):
        assert _png_size(gfx.bell_icon(n, badge=True).png()) == (n, n)


# ── palette ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("dark", [False, True])
@pytest.mark.parametrize("accent", ["#006fff", "#ffeb3b", "#00ff00", "#101010", "#e8590c"])
def test_any_accent_stays_readable(dark, accent):
    """A user's colour is shaded until buttons stay legible: text on the
    accent, and the accent against the window."""
    p = theme.build_palette(dark, accent)
    assert gfx.contrast(p["accent"], p["on_accent"]) >= 4.5
    if dark:
        assert gfx.contrast(p["accent"], p["bg"]) >= 4.0


@pytest.mark.parametrize("dark", [False, True])
def test_text_contrast(dark):
    p = theme.build_palette(dark, "")
    for fg in ("text", "text2"):
        for bg in ("bg", "surface"):
            assert gfx.contrast(p[fg], p[bg]) >= 4.5, (fg, bg)
    for fg, bg in (("green", "green_bg"), ("red", "red_bg"), ("amber", "amber_bg")):
        assert gfx.contrast(p[fg], p[bg]) >= 4.5, (fg, bg)


def test_theme_setting_resolution(monkeypatch):
    monkeypatch.setattr(winplatform, "system_prefers_dark", lambda: True)
    assert theme.resolve_dark("system") is True
    assert theme.resolve_dark("light") is False
    assert theme.resolve_dark("dark") is True


# ── Start with Windows (a fake registry) ─────────────────────────────────────

class FakeReg:
    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values, self.writes = {}, 0

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def OpenKey(self, *a):
        return self._Key()

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise OSError("missing")
        return self.values[name], self.REG_SZ

    def SetValueEx(self, key, name, _, kind, value):
        self.writes += 1
        self.values[name] = value

    def DeleteValue(self, key, name):
        self.writes += 1
        del self.values[name]


@pytest.fixture
def reg(monkeypatch):
    fake = FakeReg()
    monkeypatch.setattr(winplatform, "IS_WINDOWS", True)
    monkeypatch.setattr(winplatform, "winreg", fake, raising=False)
    return fake


def test_autostart_round_trip(reg):
    assert not winplatform.autostart_enabled()
    assert winplatform.set_autostart(True)
    assert winplatform.autostart_enabled()
    assert "--minimized" in reg.values[winplatform.RUN_VALUE]
    assert winplatform.set_autostart(False)
    assert not winplatform.autostart_enabled()


def test_autostart_writes_only_when_it_changes(reg):
    """Defender flags programs that keep rewriting Run keys."""
    winplatform.set_autostart(True)
    winplatform.set_autostart(True)
    winplatform.refresh_autostart()
    winplatform.set_autostart(False)
    winplatform.set_autostart(False)
    assert reg.writes == 2


def test_autostart_follows_a_moved_copy(reg):
    reg.values[winplatform.RUN_VALUE] = '"C:\\old place\\UnifiStockWatcher.exe" --minimized'
    winplatform.refresh_autostart()
    assert reg.values[winplatform.RUN_VALUE] == winplatform.autostart_command()


def test_autostart_command_quotes_its_paths(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Users\A B\Programs\UnifiStockWatcher.exe")
    assert winplatform.autostart_command().startswith('"C:\\Users\\A B\\')


# ── tray (real, Windows only) ────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="Windows notification area")
def test_tray_starts_stops_and_answers_a_second_copy(monkeypatch, tmp_path):
    """A second copy finds the tray window by class and asks it to show the
    app. Under a test class name, so a real running copy is never poked."""
    import threading
    import tray
    monkeypatch.setattr(winplatform, "TRAY_CLASS", "UnifiStockWatcherTrayTest")
    monkeypatch.setattr(tray, "_class_registered", False)
    import unifi_watcher_gui as gui
    shown = threading.Event()
    icons = {k: gui.resource(f"tray-{k}.ico") for k in ("idle", "watching", "alert")}
    for _ in range(2):                            # a second start in one process
        t = tray.TrayIcon(icons, "test", lambda: [], on_show=shown.set,
                          app_icon=gui.resource("icon.ico"))
        assert t.start()
        t.set_state("watching", "watching 3")
        si = types.SimpleNamespace()
        assert winplatform.SingleInstance.signal_existing(si)
        assert shown.wait(3)
        shown.clear()
        t.stop()
        assert not t._thread.is_alive()
