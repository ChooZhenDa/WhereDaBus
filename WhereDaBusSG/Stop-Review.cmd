@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0review\run.ps1" -Action Stop
set "result=%errorlevel%"
pause
exit /b %result%
