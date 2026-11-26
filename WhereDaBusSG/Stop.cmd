@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
call "%~dp0scripts\manage.cmd" Stop
set "LAUNCH_EXIT=%errorlevel%"
if not "%LAUNCH_EXIT%"=="0" (
  echo.
  echo Stop did not finish. Read the message above and START_HERE.txt.
)
pause
exit /b %LAUNCH_EXIT%
