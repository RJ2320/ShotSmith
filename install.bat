@echo off
REM Shotsmith installer for Windows
REM Copies Shotsmith.py to Resolve's Scripts/Utility folder

setlocal

set "DEST=%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Fusion\Scripts\Utility"
set "SRC=%~dp0Shotsmith.py"

echo.
echo Shotsmith installer
echo -------------------
echo Source:      %SRC%
echo Destination: %DEST%
echo.

if not exist "%SRC%" (
    echo [ERROR] Shotsmith.py not found next to this installer.
    echo         Make sure you extracted the full release zip.
    pause
    exit /b 1
)

if not exist "%DEST%" (
    echo [ERROR] DaVinci Resolve's Scripts folder not found.
    echo         Is Resolve installed?
    pause
    exit /b 1
)

copy /Y "%SRC%" "%DEST%\" >nul
if errorlevel 1 (
    echo [ERROR] Copy failed. Try running this installer as Administrator.
    pause
    exit /b 1
)

echo [OK] Shotsmith installed.
echo.
echo Next steps:
echo   1. In Resolve: Preferences ^> System ^> General
echo      set "External scripting using" to Local, then restart Resolve.
echo   2. Open a project and timeline.
echo   3. Workspace ^> Scripts ^> Utility ^> Shotsmith
echo.
echo PySide6 will auto-install on first run.
echo.
pause
