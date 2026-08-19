# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build: one windowed GUI exe, one console CLI exe.

Both are onefile and portable - unifi_core._base_dir() resolves runtime files
next to the executable when frozen, so the watch list lives beside the .exe
rather than in PyInstaller's temp extraction directory.
"""

block_cipher = None

_common = dict(
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "numpy", "pandas", "PIL", "matplotlib"],
    cipher=block_cipher,
    noarchive=False,
)

gui_a = Analysis(["unifi_watcher_gui.py"], **_common)
cli_a = Analysis(["unifi_watcher.py"], **_common)

gui_pyz = PYZ(gui_a.pure, gui_a.zipped_data, cipher=block_cipher)
cli_pyz = PYZ(cli_a.pure, cli_a.zipped_data, cipher=block_cipher)

gui_exe = EXE(
    gui_pyz, gui_a.scripts, gui_a.binaries, gui_a.zipfiles, gui_a.datas, [],
    name="UnifiStockWatcher",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    runtime_tmpdir=None,
    console=False,              # windowed: no console flash on launch
    disable_windowed_traceback=False,
)

cli_exe = EXE(
    cli_pyz, cli_a.scripts, cli_a.binaries, cli_a.zipfiles, cli_a.datas, [],
    name="UnifiStockWatcher-cli",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    runtime_tmpdir=None,
    console=True,               # the headless watcher needs its console
    disable_windowed_traceback=False,
)
