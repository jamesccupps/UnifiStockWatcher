@echo off
cd /d "%~dp0"
start "" pythonw unifi_watcher_gui.py
if errorlevel 1 (
    echo pythonw not found - falling back to python.
    start "" python unifi_watcher_gui.py
)
