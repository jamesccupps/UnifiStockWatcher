@echo off
title Unifi Stock Watcher v2.0

echo ============================================================
echo   Unifi Stock Watcher v2.0
echo ============================================================
echo.

:: Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Please install Python from https://python.org
    echo Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

echo Installing dependencies...
pip install requests --quiet

echo.
echo What would you like to do?
echo.
echo   [1] Launch GUI          (recommended)
echo   [2] Start CLI watcher   (console mode)
echo   [3] Change watched items (re-run product picker)
echo   [4] Self-test           (verify notifications work)
echo.
set /p choice="Enter 1, 2, 3, or 4: "

if "%choice%"=="1" (
    echo.
    echo Launching GUI...
    echo.
    pythonw unifi_watcher_gui.py
    exit /b
)

if "%choice%"=="3" (
    echo.
    echo Starting product picker, then watcher...
    echo.
    python unifi_watcher.py --setup
    pause
    exit /b
)

if "%choice%"=="4" (
    echo.
    python unifi_watcher.py --test
    pause
    exit /b
)

echo.
echo Starting watcher... Leave this window open. Press Ctrl+C to stop.
echo.
python unifi_watcher.py

pause
