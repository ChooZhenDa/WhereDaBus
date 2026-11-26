@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "SETUP_ACTION=Setup"
set "BROWSER_OPTION="
if /i "%~1"=="--no-browser" set "BROWSER_OPTION=--no-browser"
if /i "%~2"=="--no-browser" set "BROWSER_OPTION=--no-browser"
if not exist "review\run.ps1" goto normal
if not exist "data\EXPORT_METADATA.json" goto normal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\ensure-docker.ps1"
if errorlevel 1 goto finish
if /i "%~1"=="--live" goto normal
if /i "%~2"=="--live" goto normal
if exist ".env" (
  set "SETUP_ACTION=SetupRecorded"
  goto normal
)
call "%~dp0Start.cmd" %BROWSER_OPTION%
exit /b %errorlevel%

:normal
call "%~dp0scripts\manage.cmd" %SETUP_ACTION% %BROWSER_OPTION%
:finish
set "LAUNCH_EXIT=%errorlevel%"
if not "%LAUNCH_EXIT%"=="0" (
  echo.
  echo Setup stopped. Read the message above and START_HERE.txt.
)
pause
exit /b %LAUNCH_EXIT%
