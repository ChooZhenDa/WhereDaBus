@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0.."
set "ACTION=%~1"
set "COMPOSE_PROJECT_NAME="
set "COMPOSE_FILE="
set "COMPOSE_PROFILES="
set "COMPOSE_ENV_FILES="
set "COMPOSE_DISABLE_ENV_FILE="
set "WDB_PROJECT="
set "APP_PORT="
set "POSTGRES_PASSWORD="
set "MONGO_PASSWORD="
set "LTA_ACCOUNT_KEY="
set "SAMPLE_STOPS="
set "SAMPLE_SERVICES="
set "LAUNCH_PROJECT="
set "LAUNCH_PORT="
set "LAUNCH_HAS_KEY="
set "DOCKER_TYPE="
set "NO_BROWSER="
set "LAUNCH_RESULT=0"
if /i "%~2"=="--no-browser" set "NO_BROWSER=1"

if not exist "compose.yaml" (
  echo compose.yaml is missing. Extract the complete standalone ZIP first.
  exit /b 1
)
if /i "%ACTION%"=="Setup" goto preflight
if /i "%ACTION%"=="SetupRecorded" goto preflight
if /i "%ACTION%"=="Start" goto preflight
if /i "%ACTION%"=="StartLive" goto preflight
if /i "%ACTION%"=="RefreshData" goto preflight
if /i "%ACTION%"=="Stop" goto preflight
echo Unknown launcher action.
exit /b 1

:preflight
docker compose version >nul 2>&1
if errorlevel 1 (
  echo Docker Compose was not found. Install and start Docker Desktop, then try again.
  exit /b 1
)
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop is not ready. Start it and wait for the engine, then try again.
  exit /b 1
)
for /f "delims=" %%T in ('docker info --format "{{.OSType}}"') do set "DOCKER_TYPE=%%T"
if not "%DOCKER_TYPE%"=="linux" (
  echo This setup needs Linux containers. Select Linux containers in Docker Desktop.
  exit /b 1
)
if /i "%ACTION%"=="Setup" goto configure_setup
docker run --rm --network none --volume "%CD%:/workspace" --workdir /workspace python:3.12-slim-bookworm python scripts/install_config.py check
if errorlevel 1 exit /b 1
goto read_state

:configure_setup
echo.
echo Preparing your standalone installation...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0select-port.ps1" -ProjectPath "%CD%"
if errorlevel 1 exit /b 1
docker run --rm -it --network none --volume "%CD%:/workspace" --workdir /workspace python:3.12-slim-bookworm python scripts/install_config.py setup
if errorlevel 1 exit /b 1

:read_state
if not exist ".launcher-state" goto invalid_state
for /f "tokens=2 delims==" %%A in ('findstr /r /x "LAUNCH_PROJECT=wheredabus-[0-9a-f]*" ".launcher-state"') do set "LAUNCH_PROJECT=%%A"
for /f "tokens=2 delims==" %%A in ('findstr /r /x "LAUNCH_PORT=[1-9][0-9]*" ".launcher-state"') do set "LAUNCH_PORT=%%A"
for /f "tokens=2 delims==" %%A in ('findstr /r /x "LAUNCH_HAS_KEY=[01]" ".launcher-state"') do set "LAUNCH_HAS_KEY=%%A"
if not defined LAUNCH_PROJECT goto invalid_state
if not defined LAUNCH_PORT goto invalid_state
if not defined LAUNCH_HAS_KEY goto invalid_state
if "%LAUNCH_PROJECT:~26,1%"=="" goto invalid_state
if not "%LAUNCH_PROJECT:~27,1%"=="" goto invalid_state
if not "%LAUNCH_PORT:~5,1%"=="" goto invalid_state
if /i "%ACTION%"=="Stop" goto stop

echo.
echo Starting WhereDaBus SG on http://localhost:%LAUNCH_PORT%/ ...
docker compose --project-name %LAUNCH_PROJECT% --file compose.yaml --env-file .env up --build -d --wait --wait-timeout 300 api
if errorlevel 1 goto startup_failed
if /i "%ACTION%"=="SetupRecorded" goto load_recorded
if /i "%ACTION%"=="Start" goto open_app

:imports
if "%LAUNCH_HAS_KEY%"=="0" (
  echo No LTA key was entered. Opening the labelled demo. Run Setup.cmd --live to add your key later.
  goto open_app
)
echo.
echo Loading bus stops from LTA...
docker compose --project-name %LAUNCH_PROJECT% --file compose.yaml --env-file .env exec -T api python -m backend.cli sync-stops
if errorlevel 1 (
  set "LAUNCH_RESULT=1"
  echo Stop import did not finish. Check your LTA key and connection, then run Refresh-Data.cmd.
)
echo.
echo Loading services and routes from LTA. This can take several minutes...
docker compose --project-name %LAUNCH_PROJECT% --file compose.yaml --env-file .env exec -T api python -m backend.cli sync-static --keep-incomplete-routes
if errorlevel 1 (
  set "LAUNCH_RESULT=1"
  echo Route import did not finish. Check your LTA key and connection, then run Refresh-Data.cmd.
)
goto open_app

:load_recorded
echo Preparing the recorded route network and crowding history...
docker compose --project-name %LAUNCH_PROJECT% --file compose.yaml --env-file .env exec -T api python -m backend.submission_network < "data\postgresql.sql"
if errorlevel 1 exit /b 1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0sample-transfer.ps1" -Action Restore -ProjectPath "%CD%" -Archive "%CD%\data\samples.zip" -CheckMorningPort %LAUNCH_PORT%
if "%errorlevel%"=="3" goto archived_review
if errorlevel 1 exit /b 1
set "APP_URL=http://localhost:%LAUNCH_PORT%/?review=morning#peak"
goto show_url

:archived_review
echo Opening the included archive so the recorded morning is available.
set "REVIEW_OPTION="
if defined NO_BROWSER set "REVIEW_OPTION=-NoBrowser"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\review\run.ps1" -Action Start %REVIEW_OPTION%
exit /b %errorlevel%

:stop
docker compose --project-name %LAUNCH_PROJECT% --file compose.yaml --env-file .env stop
if errorlevel 1 exit /b 1
echo WhereDaBus SG stopped. This installation's saved data is retained.
exit /b 0

:open_app
set "APP_URL=http://localhost:%LAUNCH_PORT%/"
if "%LAUNCH_HAS_KEY%"=="0" set "APP_URL=%APP_URL%?mode=demo"
if /i "%ACTION%"=="Start" if exist "data\samples.zip" if exist "review\run.ps1" set "APP_URL=http://localhost:%LAUNCH_PORT%/"
:show_url
echo.
echo Open %APP_URL%
if not defined NO_BROWSER start "" "%APP_URL%"
exit /b %LAUNCH_RESULT%

:invalid_state
echo Installation settings could not be read. Run Setup.cmd --live to check the live configuration.
exit /b 1

:startup_failed
echo.
echo Startup did not finish. Check Docker Desktop and the message above, then retry.
echo This installation's diagnostic command:
echo docker compose --project-name %LAUNCH_PROJECT% --file compose.yaml --env-file .env logs --tail=100 api setup postgres mongo
echo Read START_HERE.txt for troubleshooting.
exit /b 1
