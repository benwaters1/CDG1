@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo   GET THE LATEST VERSION
echo ============================================================
echo.
echo   Your files are 12 versions behind the live site.
echo.
echo   This will:
echo     1. clear the stuck git lock
echo     2. put your current changes safely to one side
echo     3. download the latest version
echo.
echo   Nothing is lost. Nothing is uploaded. Nothing goes live.
echo.
pause
echo.

REM ---------- 1. the stuck lock ------------------------------------------
echo [1/3] Clearing the stuck lock file...
if exist ".git\index.lock" (
  echo       Close VS Code and any other Claude or git window FIRST.
  echo.
  set /p OK="      Ready? Press y to clear it: "
  if /i not "!OK!"=="y" ( echo       Stopped. & pause & exit /b 1 )
  del /f /q ".git\index.lock" >nul 2>&1
)
if exist ".git\objects\maintenance.lock" del /f /q ".git\objects\maintenance.lock" >nul 2>&1
if exist ".git\index.lock" (
  echo.
  echo       COULD NOT CLEAR IT. Something still has the folder open.
  echo       Close everything, then run this again.
  pause
  exit /b 1
)
echo       Cleared.
echo.

REM ---------- 2. put current changes aside --------------------------------
echo [2/3] Putting your current changes safely to one side...
git stash push -u -m "design and copy work, before pulling latest" >nul 2>&1
if errorlevel 1 (
  echo       Nothing needed setting aside.
) else (
  echo       Saved. They can be brought back at any time.
)
echo.

REM ---------- 3. pull ----------------------------------------------------
echo [3/3] Downloading the latest version...
git pull
if errorlevel 1 (
  echo.
  echo       PULL FAILED. Copy the message above and send it to Claude.
  pause
  exit /b 1
)
echo.
echo ============================================================
echo   DONE. You now have the latest version.
echo ============================================================
echo.
echo   Now on:
git log -1 --format="   %%h  %%s"
echo.
echo   Tell Claude: "pulled" - and he will re-apply the fixes
echo   on top of this newer version.
echo.
pause
