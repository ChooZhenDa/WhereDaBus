@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\sample-transfer.ps1" -Action Export %*
set "transferResult=%errorlevel%"
if not "%transferResult%"=="0" pause
exit /b %transferResult%
