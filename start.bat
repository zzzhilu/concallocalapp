@echo off
setlocal EnableDelayedExpansion

:: ============================================================================
:: ConCall Launcher
:: Simulates a native application experience using Docker & Chrome App Mode
:: ============================================================================

TITLE ConCall Launcher
COLOR 0A

echo.
echo  ========================================================================
echo   ConCall AI Meeting Assistant - Launching...
echo  ========================================================================
echo.

:: 1. Check if Docker is running
echo  [*] Checking Docker Desktop status...
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo  [!] Docker is not running. Starting Docker Desktop...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    
    echo  [*] Waiting for Docker to start (this may take a minute)...
    :WaitDocker
    timeout /t 5 /nobreak >nul
    docker info >nul 2>&1
    if !errorlevel! neq 0 (
        echo      - Waiting for Docker daemon...
        goto WaitDocker
    )
    echo  [+] Docker is ready.
) else (
    echo  [+] Docker is already running.
)

:: 2. Start Services
echo.
echo  [*] Starting AI Services...
echo      (GPU 0: ASR, GPU 1: LLM)
docker compose up -d
if %errorlevel% neq 0 (
    echo  [X] Failed to start services. Please check docker-compose.yml.
    pause
    exit /b 1
)

:: 3. Wait for Web UI to be ready
echo.
echo  [*] Waiting for Web UI (app-gateway)...
:WaitWeb
timeout /t 2 /nobreak >nul
curl -s -o nul -w "%%{http_code}" http://localhost:8000/health | find "200" >nul
if %errorlevel% neq 0 (
    echo      - Services initializing...
    goto WaitWeb
)
echo  [+] Web UI is ready!

:: 4. Launch Chrome in App Mode and Wait
echo.
echo  ========================================================================
echo   [!] APP RUNNING
echo   [!] Close the Chrome window to STOP services and release GPU VRAM.
echo  ========================================================================
echo.

:: Find Chrome path
set "CHROME_PATH="
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
    set "CHROME_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe"
) else if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" (
    set "CHROME_PATH=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
) else (
    echo  [!] Chrome not found in standard locations.
    echo      Attempting to launch via system PATH...
    start /wait "" chrome --app=http://localhost:8000
    goto Cleanup
)

:: Start Chrome and wait for it to close
:: Using start /wait "" ... ensures we wait for the process
start /wait "" "%CHROME_PATH%" --app=http://localhost:8000 --user-data-dir="%TEMP%\ConCallProfile" --no-first-run

:: 5. Cleanup
:Cleanup
echo.
echo  ========================================================================
echo   [!] APP CLOSED
echo   [*] Stopping Docker services to release GPU VRAM...
echo  ========================================================================
echo.

docker compose stop

echo  [+] Services stopped. 
echo  [+] Bye!
timeout /t 3 >nul
exit
