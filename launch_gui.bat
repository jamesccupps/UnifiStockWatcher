@echo off
cd /d "%~dp0"
rem Runs the app from source. The ready-made app needs no Python:
rem https://github.com/jamesccupps/UnifiStockWatcher/releases/latest
where pyw >nul 2>&1 && (start "" pyw -3 unifi_watcher_gui.py & exit /b 0)
where pythonw >nul 2>&1 && (start "" pythonw unifi_watcher_gui.py & exit /b 0)
start "" python unifi_watcher_gui.py
