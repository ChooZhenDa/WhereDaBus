@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\evidence.ps1" -Action Status
set "RESULT=%ERRORLEVEL%"
pause
exit /b %RESULT%

