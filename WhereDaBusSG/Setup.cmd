@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "BROWSER_OPTION="
if /i "%~1"=="--no-browser" set "BROWSER_OPTION=--no-browser"
call "%~dp0scripts\manage.cmd" Setup %BROWSER_OPTION%
set "LAUNCH_EXIT=%errorlevel%"
if not "%LAUNCH_EXIT%"=="0" (
  echo.
  echo Setup stopped. Read the message above and START_HERE.txt.
)
pause
exit /b %LAUNCH_EXIT%
