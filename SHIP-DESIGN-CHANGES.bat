@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo   SHIP THE DESIGN AND COPY CHANGES
echo ============================================================
echo.
echo   This will:
echo     1. clear the stuck git lock
echo     2. unstage the other agent's files (leaves them on disk)
echo     3. stage only the design/copy files
echo     4. run the test suite  - STOPS if anything fails
echo     5. commit  (asks first)
echo     6. push    (asks separately - push deploys to production)
echo.
echo   Nothing is deleted. Nothing is pushed without you saying yes.
echo.
pause
echo.

REM ---------- 1. the stuck lock -------------------------------------------
if exist ".git\index.lock" (
  echo [1/6] There is a stuck git lock file.
  echo       Close VS Code, any git window, and the other Claude session first.
  echo.
  set /p KILLLOCK="      Delete the lock now? (y/n): "
  if /i "!KILLLOCK!"=="y" (
    del /f /q ".git\index.lock"
    if exist ".git\index.lock" (
      echo       COULD NOT DELETE IT. Something still has the repo open.
      echo       Close it and run this script again.
      pause
      exit /b 1
    )
    echo       Lock cleared.
  ) else (
    echo       Stopped. Nothing changed.
    pause
    exit /b 1
  )
) else (
  echo [1/6] No stuck lock. Good.
)
echo.

REM ---------- 1b. SAFETY: are we behind the live version? -----------------
echo [1b] Checking whether the live site is newer than these files...
git fetch origin >nul 2>&1
for /f %%C in ('git rev-list --count HEAD..origin/main 2^>nul') do set BEHIND=%%C
if not defined BEHIND set BEHIND=0
if not "%BEHIND%"=="0" (
  echo.
  echo ============================================================
  echo   STOP. The live site is %BEHIND% commit^(s^) ahead of these files.
  echo.
  echo   Pushing now would UNDO the newer version that is live
  echo   right now - including the new homepage hero.
  echo.
  echo   Run this first:      git pull
  echo   Then tell Claude, and he will re-apply the fixes on top
  echo   of the newer version instead of over the top of it.
  echo ============================================================
  pause
  exit /b 1
)
echo       Up to date with the live version. Safe to continue.
echo.

REM ---------- 2. unstage the other agent's work ---------------------------
echo [2/6] Unstaging the other agent's files (they stay on disk, untouched)...
for %%F in (
  tests
  translations.py
  templates\booking_confirmation.html
  templates\error.html
  templates\event_confirmation.html
  templates\event_find.html
  templates\event_manage.html
  templates\find_booking.html
  templates\guest_account.html
  templates\guest_account_expired.html
  templates\guest_account_request.html
  templates\guest_feedback_form.html
  templates\guest_feedback_submitted.html
  templates\guest_portal.html
  templates\guest_statement.html
  templates\manage_booking.html
  templates\newsletter_confirmed.html
  templates\restaurant_book.html
  templates\restaurant_confirmation.html
  templates\restaurant_find.html
  templates\restaurant_manage.html
  templates\terms.html
  templates\unsubscribe.html
  templates\workshop_confirmation.html
  templates\workshop_feedback_form.html
  templates\workshop_find.html
  templates\workshop_manage.html
  templates\workshop_register.html
) do (
  git restore --staged "%%F" >nul 2>&1
)
echo       Done.
echo.

REM ---------- 3. stage the design/copy work -------------------------------
echo [3/6] Staging the design and copy files...
for %%F in (
  app.py
  static\gudanes.css
  templates\privacy.html
  templates\_monument_note.html
  templates\home.html
  templates\book_rooms.html
  templates\book_room.html
  templates\workshops_public.html
  templates\workshop_detail.html
  templates\restaurant_info.html
  templates\events_info.html
  templates\restoration.html
  templates\facilities.html
  templates\gallery.html
  templates\contact.html
  templates\whats_on.html
  templates\public_base.html
) do (
  git add "%%F" >nul 2>&1
)
echo       Done.
echo.
echo       These files will go into the commit:
git diff --cached --name-only
echo.

REM ---------- 4. tests ---------------------------------------------------
echo [4/6] Running the test suite. This must pass before anything ships.
echo.
set PY=python
%PY% --version >nul 2>&1 || set PY=py
%PY% tests\run.py
if errorlevel 1 (
  echo.
  echo ============================================================
  echo   TESTS FAILED - STOPPING. Nothing has been committed.
  echo   Your files on disk are untouched. Send me the output above.
  echo ============================================================
  pause
  exit /b 1
)
echo.
echo       Tests passed.
echo.
echo       NOTE: the suite has a deliberate always-failing check.
echo       If you saw NO failure at all, tell me - that means the
echo       harness is not actually reporting failures.
echo.

REM ---------- 5. commit --------------------------------------------------
set /p DOCOMMIT="[5/6] Commit these changes? (y/n): "
if /i not "!DOCOMMIT!"=="y" (
  echo       Stopped before committing. Files are staged and safe.
  pause
  exit /b 0
)
git commit -m "Fix wrong photographs, contradictory room counts and duplicated copy across the guest site; add a real privacy page"
if errorlevel 1 (
  echo       Commit failed. Nothing pushed.
  pause
  exit /b 1
)
echo.
echo       Committed:
git log -1 --stat
echo.

REM ---------- 6. push ---------------------------------------------------
echo ============================================================
echo   PUSHING DEPLOYS TO THE LIVE SITE THAT GUESTS SEE.
echo ============================================================
set /p DOPUSH="[6/6] Push to main and deploy? (type yes to deploy): "
if /i not "!DOPUSH!"=="yes" (
  echo.
  echo       Not pushed. The commit is saved locally.
  echo       When you are ready:  git push
  pause
  exit /b 0
)
git push
if errorlevel 1 (
  echo       Push failed. The commit is still saved locally.
  pause
  exit /b 1
)
echo.
echo ============================================================
echo   DEPLOYED. Railway will rebuild in a minute or two.
echo ============================================================
git log -1 --stat
echo.
pause
