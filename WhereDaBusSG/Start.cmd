@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "BROWSER_OPTION="
set "REVIEW_BROWSER_OPTION="
if /i "%~1"=="--no-browser" (
  set "BROWSER_OPTION=--no-browser"
  set "REVIEW_BROWSER_OPTION=-NoBrowser"
)
if not exist ".env" if exist "review\run.ps1" if exist "data\EXPORT_METADATA.json" goto review
call "%~dp0scripts\manage.cmd" Start %BROWSER_OPTION%
goto finish

:review
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0review\run.ps1" -Action Start %REVIEW_BROWSER_OPTION%

:finish
set "LAUNCH_EXIT=%errorlevel%"
if not "%LAUNCH_EXIT%"=="0" (
  echo.
  echo Start stopped. Read the message above and START_HERE.txt.
)
pause
exit /b %LAUNCH_EXIT%
