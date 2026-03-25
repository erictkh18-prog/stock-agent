@echo off
setlocal

set "PROJECT_DIR=%~dp0"
set "START_SCRIPT=%PROJECT_DIR%start-api.ps1"

if not exist "%START_SCRIPT%" (
	echo Could not find start script: "%START_SCRIPT%"
	exit /b 1
)

echo Starting Stock Agent API locally...
start "Stock Agent API" powershell -NoExit -ExecutionPolicy Bypass -File "%START_SCRIPT%"

echo Waiting for server startup...
timeout /t 4 /nobreak >nul

echo Opening dashboard and API docs in your browser...
start "" "http://127.0.0.1:8000/"
start "" "http://127.0.0.1:8000/docs"

echo Done. Close this window anytime; the API runs in the separate Stock Agent API window.
