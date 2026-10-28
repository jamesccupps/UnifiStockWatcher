# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build: one folder, two programs sharing it.

    python scripts/build_windows.py      (runs this, then makes the zip and the installer)

dist/UnifiStockWatcher/
    UnifiStockWatcher.exe       the app (windowed)
    UnifiStockWatcher-cli.exe   the headless watcher (console), for Task Scheduler
    _internal/                  Python, Tk, requests, assets/

A folder rather than 2.x's single-file exes: a single-file build unpacks
itself to %TEMP% on every start (slower, and what antivirus engines dislike
most about PyInstaller), and a folder is what the installer and the in-app
updater replace. The version resource comes from build/version_info.txt,
which build_windows.py writes from unifi_core.__version__.
"""

from pathlib import Path

ROOT = Path(SPECPATH)
VERSION_FILE = ROOT / "build" / "version_info.txt"

_common = dict(
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(p), "assets") for p in (ROOT / "assets").glob("*") if p.suffix in (".ico", ".png")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # cryptography only signs releases (scripts/), never ships.
    excludes=["pytest", "numpy", "pandas", "PIL", "matplotlib", "cryptography",
              "setuptools", "pkg_resources", "distutils", "_distutils_hack",
              "pydoc", "unittest", "lib2to3", "xmlrpc", "curses", "_pyrepl", "pdb"],
    noarchive=False,
)

gui_a = Analysis([str(ROOT / "unifi_watcher_gui.py")], **_common)
cli_a = Analysis([str(ROOT / "unifi_watcher.py")], **_common)

gui_pyz = PYZ(gui_a.pure)
cli_pyz = PYZ(cli_a.pure)

_exe = dict(
    exclude_binaries=True,
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    icon=str(ROOT / "assets" / "icon.ico"),
    version=str(VERSION_FILE) if VERSION_FILE.exists() else None,
)

gui_exe = EXE(gui_pyz, gui_a.scripts, [], name="UnifiStockWatcher",
              console=False,          # windowed: no console flash on launch
              **_exe)
cli_exe = EXE(cli_pyz, cli_a.scripts, [], name="UnifiStockWatcher-cli",
              console=True,           # the headless watcher needs its console
              **_exe)

coll = COLLECT(
    gui_exe, gui_a.binaries, gui_a.datas,
    cli_exe, cli_a.binaries, cli_a.datas,
    strip=False, upx=False,
    name="UnifiStockWatcher",
)
