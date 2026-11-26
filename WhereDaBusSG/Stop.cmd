@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if not exist ".env" if exist "review\run.ps1" if exist "data\EXPORT_METADATA.json" goto review
call "%~dp0scripts\manage.cmd" Stop
goto finish

:review
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0review\run.ps1" -Action Stop

:finish
set "LAUNCH_EXIT=%errorlevel%"
if not "%LAUNCH_EXIT%"=="0" (
  echo.
  echo Stop did not finish. Read the message above and START_HERE.txt.
)
pause
exit /b %LAUNCH_EXIT%
