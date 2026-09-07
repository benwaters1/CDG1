@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo   BRING DOWN THE SQUARESPACE PHOTOGRAPHS
echo ============================================================
echo.
echo   This finds every photograph the old Squarespace account
echo   still holds, downloads them, and sorts them into folders.
echo.
echo   Everything lands in ONE folder:
echo.
echo       D:\Gudanes\photo_harvest\full\
echo.
echo   (plus thumbs\ - small copies so Claude can look at them,
echo    and duplicates\ - second copies of the same picture)
echo.
echo   Nothing is deleted. No website file is touched. Nothing
echo   goes live. It is safe to run twice - it skips what it has.
echo.
pause
echo.

set PY=python
%PY% --version >nul 2>&1 || set PY=py

echo [1/2] Looking first, downloading nothing...
echo.
%PY% tools\harvest_photos.py --list
if errorlevel 1 (
  echo.
  echo   That did not work. Copy the message above and send it to Claude.
  pause
  exit /b 1
)
echo.
echo ============================================================
set /p GO="   Download all of those now? (y/n): "
if /i not "!GO!"=="y" (
  echo   Stopped. Nothing downloaded.
  pause
  exit /b 0
)
echo.
echo [2/2] Downloading. This takes a few minutes - leave it running.
echo.
%PY% tools\harvest_photos.py
if errorlevel 1 (
  echo.
  echo   It stopped early. Run this again - it carries on where it left off.
  pause
  exit /b 1
)
echo.
echo ============================================================
echo   DONE. Now tell Claude: "photos downloaded"
echo   He will go through every thumbnail and sort out the
echo   duplicates and the ones not worth keeping.
echo ============================================================
pause
