@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Prepare-Submission.ps1" -ProjectPath "%CD%"
set "SUBMISSION_EXIT=%errorlevel%"
echo.
if not "%SUBMISSION_EXIT%"=="0" echo Submission ZIP was not completed. Read the message above.
pause
exit /b %SUBMISSION_EXIT%
