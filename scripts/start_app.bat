@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion

:: ============================================================================
:: ConCall Launcher — start_app.bat
:: Simulates a native application experience using Docker & Chrome App Mode
:: ============================================================================

TITLE ConCall Launcher

echo.
echo  ========================================================================
echo   ConCall AI Meeting Assistant - Launching...
echo  ========================================================================
echo.

:: 1. Check if Docker is running
echo  [*] Checking Docker Desktop status...
docker info >nul 2>&1
if errorlevel 1 goto StartDocker
echo  [+] Docker is already running.
goto DockerReady

:StartDocker
echo  [!] Docker is not running. Starting Docker Desktop...
start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
echo  [*] Waiting for Docker to start (this may take a minute)...

:WaitDocker
timeout /t 5 /nobreak >nul
docker info >nul 2>&1
if errorlevel 1 (
    echo      - Waiting for Docker daemon...
    goto WaitDocker
)
echo  [+] Docker is ready.

:DockerReady

:: 2. Start Services (from project root, one level up from scripts/)
echo.
echo  [*] Starting AI Services...
echo      GPU 0: ASR, GPU 1: LLM
pushd "%~dp0.."
docker compose up -d
if errorlevel 1 (
    echo  [X] Failed to start services. Please check docker-compose.yml.
    popd
    pause
    exit /b 1
)
popd

:: 3. Wait for Web UI to be ready
echo.
echo  [*] Waiting for Web UI (app-core)...

:WaitWeb
timeout /t 3 /nobreak >nul
curl -s -o nul -w "%%{http_code}" http://localhost:8000/health 2>nul | find "200" >nul
if errorlevel 1 (
    echo      - Services initializing...
    goto WaitWeb
)
echo  [+] Web UI is ready!

:: 4. Launch Chrome in App Mode and Wait
echo.
echo  ========================================================================
echo   APP RUNNING
echo   Close the Chrome window to STOP services and release GPU VRAM.
echo  ========================================================================
echo.

:: Find Chrome path
set "CHROME_PATH="
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
    set "CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe"
)
if not defined CHROME_PATH (
    if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" (
        set "CHROME_PATH=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    )
)

if not defined CHROME_PATH (
    echo  [!] Chrome not found. Opening in default browser...
    start /wait "" http://localhost:8000
    goto Cleanup
)

:: Start Chrome and wait for it to close
start /wait "" "!CHROME_PATH!" --app=http://localhost:8000 --user-data-dir="%TEMP%\ConCallProfile" --no-first-run

:: 5. Cleanup — call cleanup script
:Cleanup
echo.
call "%~dp0cleanup.bat"
exit
