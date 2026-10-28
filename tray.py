"""The notification-area icon, its menu, and its notifications (Windows).

Shell_NotifyIconW through ctypes, on a thread of its own with its own hidden
window and message loop; no pystray/Pillow. Notifications are the icon's own
balloons, which Windows 10 and 11 show as toasts under this app's name, and a
click on one runs that notification's callback (open the product's page).

Every callback runs on the tray thread: the GUI marshals them onto Tk's.

The hidden window is a top-level one (not message-only) so that it receives
broadcasts: TaskbarCreated, to put the icon back after Explorer restarts, and
WM_SETTINGCHANGE, when Windows switches between light and dark. A second copy
of the app finds it by class name and posts WM_SHOW_WINDOW to it.
"""

import ctypes
import logging
import queue
import sys
import threading
from ctypes import wintypes

import winplatform
from winplatform import WM_SHOW_WINDOW

log = logging.getLogger("unifi_watcher.tray")

AVAILABLE = sys.platform == "win32"

WM_DESTROY, WM_CLOSE, WM_COMMAND, WM_NULL = 0x0002, 0x0010, 0x0111, 0x0000
WM_SETTINGCHANGE, WM_CONTEXTMENU, WM_LBUTTONDBLCLK = 0x001A, 0x007B, 0x0203
WM_APP = 0x8000
WM_TRAY_CALLBACK = WM_APP + 0x41
WM_TRAY_WAKE = WM_APP + 0x43                   # work queued for this thread
NIN_SELECT, NIN_KEYSELECT = 0x0400, 0x0401
NIN_BALLOONUSERCLICK = 0x0405
NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0, 1, 2, 4
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO, NIF_SHOWTIP = 0x1, 0x2, 0x4, 0x10, 0x80
NIIF_USER, NIIF_NOSOUND, NIIF_LARGE_ICON = 0x4, 0x10, 0x20
NOTIFYICON_VERSION_4 = 4
MF_STRING, MF_SEPARATOR, MF_GRAYED, MF_CHECKED = 0x0, 0x800, 0x1, 0x8
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x10
SM_CXSMICON, SM_CXICON = 49, 11

if AVAILABLE:
    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND),
                    ("uID", wintypes.UINT), ("uFlags", wintypes.UINT),
                    ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                    ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD),
                    ("dwStateMask", wintypes.DWORD), ("szInfo", wintypes.WCHAR * 256),
                    ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                    ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID),
                    ("hBalloonIcon", wintypes.HICON)]

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _s32 = ctypes.WinDLL("shell32", use_last_error=True)
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _u32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _u32.DefWindowProcW.restype = LRESULT
    _u32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                     wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                     wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    _u32.CreateWindowExW.restype = wintypes.HWND
    _u32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    _u32.RegisterClassW.restype = wintypes.ATOM
    _u32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    _u32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    _u32.RegisterWindowMessageW.restype = wintypes.UINT
    _u32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _u32.LoadImageW.restype = wintypes.HANDLE
    _u32.DestroyIcon.argtypes = [wintypes.HICON]
    _u32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                 wintypes.UINT, wintypes.UINT]
    _u32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _u32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _u32.DispatchMessageW.restype = LRESULT
    _u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _u32.DestroyWindow.argtypes = [wintypes.HWND]
    _u32.CreatePopupMenu.restype = wintypes.HMENU
    _u32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    _u32.SetMenuDefaultItem.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
    _u32.TrackPopupMenuEx.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                      ctypes.c_int, wintypes.HWND, wintypes.LPVOID]
    _u32.TrackPopupMenuEx.restype = wintypes.UINT
    _u32.DestroyMenu.argtypes = [wintypes.HMENU]
    _u32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _u32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _s32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    _s32.Shell_NotifyIconW.restype = wintypes.BOOL
    _k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _k32.GetModuleHandleW.restype = wintypes.HMODULE

# One window procedure for the class, for the life of the process: a class
# keeps the procedure it was registered with, so a per-instance callback would
# be called after its TrayIcon (and the ctypes thunk) had been collected.
_instances = {}          # hwnd -> TrayIcon
_creating = None         # the TrayIcon whose window is being created
_class_registered = False


def _dispatch(hwnd, msg, wparam, lparam):
    inst = _instances.get(hwnd) or _creating
    if inst is not None:
        return inst._proc(hwnd, msg, wparam, lparam)
    return _u32.DefWindowProcW(hwnd, msg, wparam, lparam)


if AVAILABLE:
    _DISPATCH = WNDPROC(_dispatch)


class MenuItem:
    """A tray menu entry. text=None is a separator."""

    def __init__(self, text=None, action=None, enabled=True, checked=False, default=False):
        self.text, self.action = text, action
        self.enabled, self.checked, self.default = enabled, checked, default


class TrayIcon:
    """Usage: TrayIcon(icons, tooltip, menu_fn, on_open, on_show, on_theme).start()

    icons: {"idle": path, "watching": path, "alert": path} (.ico files)
    menu_fn(): returns the list of MenuItem to show, built fresh each time
    on_open(): the icon was clicked
    on_show(): another copy of the app asked this one to show itself
    on_theme(): Windows' light/dark setting changed
    """

    def __init__(self, icons, tooltip, menu_fn, on_open=None, on_show=None,
                 on_theme=None, app_icon=None):
        self.icons, self.tooltip = icons, tooltip
        self.menu_fn = menu_fn
        self.on_open, self.on_show, self.on_theme = on_open, on_show, on_theme
        self.app_icon = app_icon
        self.state = "idle"
        self.hwnd = None
        self._handles = {}
        self._jobs = queue.Queue()
        self._balloon_click = None
        self._ready = threading.Event()
        self._thread = None
        self._taskbar_created = None
        self.ok = False

    # Public, thread-safe ----------------------------------------------------

    def start(self, timeout=5):
        if not AVAILABLE:
            return False
        self._thread = threading.Thread(target=self._run, name="unifi-tray", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return self.ok

    def set_state(self, state, tooltip=None):
        self._call(self._apply_state, state, tooltip)

    def notify(self, title, text, on_click=None, sound=False):
        self._call(self._notify, title, text, on_click, sound)

    def stop(self):
        if self.hwnd:
            _u32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(timeout=3)

    # Tray thread --------------------------------------------------------------

    def _call(self, fn, *args):
        if not (AVAILABLE and self.hwnd):
            return
        self._jobs.put((fn, args))
        _u32.PostMessageW(self.hwnd, WM_TRAY_WAKE, 0, 0)

    def _icon(self, name, size_metric=SM_CXSMICON):
        key = (name, size_metric)
        if key not in self._handles:
            path = self.icons.get(name) if name != "app" else self.app_icon
            size = _u32.GetSystemMetrics(size_metric)
            h = _u32.LoadImageW(None, str(path), IMAGE_ICON, size, size, LR_LOADFROMFILE) if path else None
            self._handles[key] = h
        return self._handles[key]

    def _nid(self, flags=0):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        nid.uCallbackMessage = WM_TRAY_CALLBACK
        return nid

    def _add_icon(self):
        nid = self._nid(NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP)
        nid.hIcon = self._icon(self.state)
        nid.szTip = self.tooltip[:127]
        ok = _s32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        nid.uVersion = NOTIFYICON_VERSION_4
        _s32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(nid))
        return bool(ok)

    def _apply_state(self, state, tooltip):
        self.state = state if state in self.icons else self.state
        if tooltip:
            self.tooltip = tooltip
        nid = self._nid(NIF_ICON | NIF_TIP | NIF_SHOWTIP)
        nid.hIcon = self._icon(self.state)
        nid.szTip = self.tooltip[:127]
        _s32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _notify(self, title, text, on_click, sound):
        self._balloon_click = on_click
        nid = self._nid(NIF_INFO)
        nid.szInfoTitle = str(title)[:63]
        nid.szInfo = str(text)[:255] or " "
        big = self._icon("app", SM_CXICON)
        if big:
            nid.hBalloonIcon = big
            nid.dwInfoFlags = NIIF_USER | NIIF_LARGE_ICON
        if not sound:
            nid.dwInfoFlags |= NIIF_NOSOUND
        _s32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _show_menu(self, x=None, y=None):
        items = self.menu_fn() or []
        menu = _u32.CreatePopupMenu()
        actions = {}
        for i, item in enumerate(items, start=1):
            if item.text is None:
                _u32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            flags = MF_STRING | (0 if item.enabled else MF_GRAYED) | (MF_CHECKED if item.checked else 0)
            _u32.AppendMenuW(menu, flags, i, item.text)
            actions[i] = item.action
            if item.default:
                _u32.SetMenuDefaultItem(menu, i, False)
        if x is None:
            pt = wintypes.POINT()
            _u32.GetCursorPos(ctypes.byref(pt))
            x, y = pt.x, pt.y
        # Without the foreground, the menu doesn't close on a click elsewhere.
        _u32.SetForegroundWindow(self.hwnd)
        cmd = _u32.TrackPopupMenuEx(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                                    x, y, self.hwnd, None)
        _u32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        _u32.DestroyMenu(menu)
        self._safe(actions.get(cmd))

    @staticmethod
    def _safe(fn, *args):
        if fn:
            try:
                fn(*args)
            except Exception:
                log.exception("tray callback failed")

    def _proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY_CALLBACK:
                event = lparam & 0xFFFF
                if event in (NIN_SELECT, NIN_KEYSELECT, WM_LBUTTONDBLCLK):
                    self._safe(self.on_open)
                elif event == WM_CONTEXTMENU:
                    x = ctypes.c_short(wparam & 0xFFFF).value
                    y = ctypes.c_short((wparam >> 16) & 0xFFFF).value
                    self._show_menu(x, y)
                elif event == NIN_BALLOONUSERCLICK:
                    self._safe(self._balloon_click)
                return 0
            if msg == WM_TRAY_WAKE:
                while True:
                    try:
                        fn, args = self._jobs.get_nowait()
                    except queue.Empty:
                        break
                    self._safe(fn, *args)
                return 0
            if msg == WM_SHOW_WINDOW:
                self._safe(self.on_show)
                return 0
            if msg == self._taskbar_created:
                self._add_icon()
                return 0
            if msg == WM_SETTINGCHANGE and lparam:
                if ctypes.wstring_at(lparam) == "ImmersiveColorSet":
                    self._safe(self.on_theme)
            if msg == WM_CLOSE:
                _u32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                _s32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid()))
                _u32.PostQuitMessage(0)
                return 0
        except Exception:
            log.exception("tray window procedure failed")
        return _u32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _run(self):
        global _creating, _class_registered
        try:
            hinst = _k32.GetModuleHandleW(None)
            if not _class_registered:
                wc = WNDCLASSW()
                wc.lpfnWndProc = _DISPATCH
                wc.hInstance = hinst
                wc.lpszClassName = winplatform.TRAY_CLASS
                if not _u32.RegisterClassW(ctypes.byref(wc)):
                    raise OSError(ctypes.get_last_error(), "RegisterClassW failed")
                _class_registered = True
            self._taskbar_created = _u32.RegisterWindowMessageW("TaskbarCreated")
            # WS_POPUP, never shown: a top-level window, so broadcasts reach it.
            _creating = self
            try:
                self.hwnd = _u32.CreateWindowExW(0, winplatform.TRAY_CLASS, "UniFi Stock Watcher",
                                                 0x80000000, 0, 0, 0, 0, None, None, hinst, None)
            finally:
                _creating = None
            if not self.hwnd:
                raise OSError(ctypes.get_last_error(), "CreateWindowExW failed")
            _instances[self.hwnd] = self
            self.ok = self._add_icon()
        except Exception:
            log.exception("tray icon unavailable")
            self.ok = False
        finally:
            self._ready.set()
        if not self.hwnd:
            return
        msg = wintypes.MSG()
        while _u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            _u32.TranslateMessage(ctypes.byref(msg))
            _u32.DispatchMessageW(ctypes.byref(msg))
        for h in self._handles.values():
            if h:
                _u32.DestroyIcon(h)
        self._handles.clear()
        _instances.pop(self.hwnd, None)
        self.hwnd = None
