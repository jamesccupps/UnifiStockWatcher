"""Windows integration: DPI, title bar colours, theme and accent, Start with
Windows, single instance. Every function is a harmless no-op elsewhere, so
the GUI runs (and its tests run) on Linux too.
"""

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("unifi_watcher.win")

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes
    import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "UniFi Stock Watcher"
MUTEX_NAME = "JamesCupps.UnifiStockWatcher"    # Inno Setup waits on this too
TRAY_CLASS = "UnifiStockWatcherTray"
WM_SHOW_WINDOW = 0x8000 + 0x42                 # WM_APP + 0x42, see tray.py


def enable_dpi_awareness():
    """System DPI aware, before the first window. Tk 8.6 can't redraw when a
    window moves to a monitor of another scale, so per-monitor awareness
    would leave it the wrong size there; system awareness lets Windows scale
    it instead, and keeps text sharp on the main monitor."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def set_app_id(app_id):
    """The taskbar and notifications group this process under app_id; the
    installer gives the Start menu shortcut the same id."""
    if IS_WINDOWS:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            pass


def _reg_dword(path, name, root=None):
    try:
        with winreg.OpenKey(root or winreg.HKEY_CURRENT_USER, path) as k:
            return winreg.QueryValueEx(k, name)[0]
    except OSError:
        return None


def system_prefers_dark():
    if not IS_WINDOWS:
        return False
    v = _reg_dword(r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
                   "AppsUseLightTheme")
    return v == 0


def system_accent(dark):
    """Windows' accent colour, in the shade Windows itself uses for accent
    buttons in this theme (AccentPalette: Dark1 in light mode, Light2 in
    dark mode), or None."""
    if not IS_WINDOWS:
        return None
    pal = _reg_dword(r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accent",
                     "AccentPalette")
    if isinstance(pal, bytes) and len(pal) >= 32:
        i = 4 * (1 if dark else 4)
        return "#{:02x}{:02x}{:02x}".format(pal[i], pal[i + 1], pal[i + 2])
    v = _reg_dword(r"Software\Microsoft\Windows\DWM", "AccentColor")
    if isinstance(v, int):
        return "#{:02x}{:02x}{:02x}".format(v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)
    return None


def style_title_bar(tk_window, dark, caption=None, text=None):
    """Dark or light title bar to match the window (Windows 10 1809+), and on
    Windows 11 the caption in the window's own background colour."""
    if not IS_WINDOWS:
        return
    try:
        tk_window.update_idletasks()
        hwnd = wintypes.HWND(int(tk_window.wm_frame(), 16))
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                              ctypes.c_void_p, wintypes.DWORD]
        val = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):           # DWMWA_USE_IMMERSIVE_DARK_MODE, pre-20H1
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), 4) == 0:
                break
        for attr, colour in ((35, caption), (36, text)):   # CAPTION_COLOR, TEXT_COLOR
            if colour:
                r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
                c = ctypes.c_uint(r | (g << 8) | (b << 16))
                dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(c), 4)
    except Exception:
        log.debug("title bar styling failed", exc_info=True)


def set_menu_theme(dark):
    """Native popup menus (tk.Menu, the tray menu) in the app's theme, through
    uxtheme's SetPreferredAppMode (ordinal 135, Windows 10 1903+)."""
    if not IS_WINDOWS:
        return
    try:
        ux = ctypes.WinDLL("uxtheme")
        set_mode = ux[135]
        set_mode.argtypes, set_mode.restype = [ctypes.c_int], ctypes.c_int
        set_mode(2 if dark else 3)      # ForceDark / ForceLight
        flush = ux[136]
        flush()
    except Exception:
        pass


def point_on_screen(x, y):
    """True if (x, y) is on a connected monitor."""
    if not IS_WINDOWS:
        return True
    try:
        return bool(ctypes.windll.user32.MonitorFromPoint(wintypes.POINT(x, y), 0))
    except Exception:
        return True


def open_path(path):
    """Show a folder or file with its default program."""
    try:
        if IS_WINDOWS:
            os.startfile(str(path))
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        log.warning("could not open %s", path, exc_info=True)


# ── Start with Windows ───────────────────────────────────────────────────────

def autostart_command():
    """What the Run key should hold to start this copy at sign-in."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        return f'"{exe}" --minimized --watch'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    interp = pyw if pyw.exists() else Path(sys.executable)
    script = Path(__file__).resolve().parent / "unifi_watcher_gui.py"
    return f'"{interp}" "{script}" --minimized --watch'


def autostart_value():
    if not IS_WINDOWS:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, RUN_VALUE)[0]
    except OSError:
        return None


def autostart_enabled():
    return autostart_value() is not None


def set_autostart(enabled):
    """Add or remove the Run entry, touching the registry only when it has
    to change: Defender flags programs that keep rewriting Run keys."""
    if not IS_WINDOWS:
        return False
    current = autostart_value()
    wanted = autostart_command() if enabled else None
    if current == wanted:
        return True
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            if wanted is None:
                winreg.DeleteValue(k, RUN_VALUE)
            else:
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, wanted)
        return True
    except OSError:
        log.warning("could not change Start with Windows", exc_info=True)
        return False


def refresh_autostart():
    """If Start with Windows is on but points at another copy's path (the
    folder moved, an old install), point it at this one."""
    current = autostart_value()
    if current is not None and current != autostart_command():
        set_autostart(True)


# ── Single instance ──────────────────────────────────────────────────────────

class SingleInstance:
    """A named mutex held for the life of the process.

    `already_running` is True when another copy holds it; that copy is then
    asked to show its window (its tray window handles WM_SHOW_WINDOW).
    """

    def __init__(self, name=MUTEX_NAME):
        self.handle = None
        self.already_running = False
        if not IS_WINDOWS:
            return
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = wintypes.HANDLE
        self.handle = k32.CreateMutexW(None, False, name)
        self.already_running = k32.GetLastError() == 183    # ERROR_ALREADY_EXISTS

    def signal_existing(self):
        if not IS_WINDOWS:
            return False
        u32 = ctypes.windll.user32
        u32.FindWindowW.restype = wintypes.HWND
        hwnd = u32.FindWindowW(TRAY_CLASS, None)
        if hwnd:
            u32.PostMessageW(hwnd, WM_SHOW_WINDOW, 0, 0)
            return True
        return False

    def release(self):
        if self.handle:
            ctypes.windll.kernel32.CloseHandle(self.handle)
            self.handle = None
