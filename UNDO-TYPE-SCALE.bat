@echo off
cd /d "%~dp0"
echo ============================================================
echo   PUT THE BIG HEADINGS BACK
echo ============================================================
echo.
echo   I made the largest page headings smaller (76px to 68px) so
echo   the type sizes step evenly. It only shows on very wide
echo   screens. If you do not like it, this puts it back.
echo.
pause
powershell -NoProfile -Command "$p='static/gudanes.css'; $t=Get-Content $p -Raw; if($t -match 'clamp\(40px, 5\.6vw, 68px\)'){ (${t} -replace 'clamp\(40px, 5\.6vw, 68px\)','clamp(40px, 5.6vw, 76px)') | Set-Content $p -NoNewline; Write-Host '   Reverted to 76px.' } elseif($t -match 'clamp\(40px, 5\.6vw, 76px\)'){ Write-Host '   Already 76px - nothing to do.' } else { Write-Host '   Could not find that line. Tell Claude.' }"
echo.
pause
