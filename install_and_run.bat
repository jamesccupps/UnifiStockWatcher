@echo off
setlocal
cd /d "%~dp0"
title UniFi Stock Watcher

echo ============================================================
echo   UniFi Stock Watcher
echo ============================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Please install Python 3.10+ from https://python.org
    echo Make sure to check "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)

set /a attempts=0

echo Installing dependencies...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo.
    echo WARNING: dependency install failed. Continuing anyway --
    echo the app will tell you what is missing.
    echo.
)

:menu
set /a attempts+=1
if %attempts% GTR 5 (
    echo.
    echo No valid selection - exiting.
    exit /b 1
)
echo.
echo What would you like to do?
echo.
echo   [1] Launch GUI           (recommended)
echo   [2] Start CLI watcher    (console mode)
echo   [3] Change watched items (re-run product picker)
echo   [4] Self-test            (verify notifications work)
echo   [5] Exit
echo.
set "choice="
set /p "choice=Enter 1-5: "

if not defined choice goto menu
if "%choice%"=="1" goto gui
if "%choice%"=="2" goto cli
if "%choice%"=="3" goto setup
if "%choice%"=="4" goto selftest
if "%choice%"=="5" exit /b 0

echo Sorry, "%choice%" is not one of the options.
goto menu

:gui
echo.
echo Launching GUI...
start "" pythonw unifi_watcher_gui.py
exit /b 0

:cli
echo.
echo Starting watcher... Leave this window open. Press Ctrl+C to stop.
echo.
python unifi_watcher.py
pause
exit /b 0

:setup
echo.
echo Starting product picker, then watcher...
echo.
python unifi_watcher.py --setup
pause
exit /b 0

:selftest
echo.
python unifi_watcher.py --test
pause
exit /b 0
