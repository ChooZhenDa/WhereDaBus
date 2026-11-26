@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "BROWSER_OPTION="
if /i "%~1"=="--no-browser" set "BROWSER_OPTION=--no-browser"
call "%~dp0scripts\manage.cmd" StartLive %BROWSER_OPTION%
set "LAUNCH_EXIT=%errorlevel%"
if not "%LAUNCH_EXIT%"=="0" echo Live startup did not finish completely. Read the messages above.
pause
exit /b %LAUNCH_EXIT%
