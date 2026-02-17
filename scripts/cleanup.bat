@echo off
:: ============================================================================
:: ConCall Cleanup — cleanup.bat
:: Stops all Docker services to release GPU VRAM
:: ============================================================================

echo.
echo  ========================================================================
echo   [!] APP CLOSED
echo   [*] Stopping Docker services to release GPU VRAM...
echo  ========================================================================
echo.

pushd "%~dp0.."
docker compose stop
popd

echo  [+] Services stopped.
echo  [+] GPU VRAM released. Bye!
timeout /t 3 >nul
