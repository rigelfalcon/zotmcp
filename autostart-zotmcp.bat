@echo off
setlocal enabledelayedexpansion

:: ZotMCP Auto-Start Service with Zotero Wait
:: Waits for Zotero to be ready, then starts ZotMCP HTTP server

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

:: Load .env if exists
if exist "%SCRIPT_DIR%.env" (
    for /f "usebackq tokens=1,* delims==" %%a in ("%SCRIPT_DIR%.env") do (
        if not "%%a"=="" if not "%%a:~0,1%"=="#" set "%%a=%%b"
    )
    call :log "Loaded .env"
)

set "LOG_FILE=%SCRIPT_DIR%autostart.log"
set "MAX_WAIT_MINUTES=30"
set "CHECK_INTERVAL_SECONDS=10"

:: Log function
call :log "=========================================="
call :log "ZotMCP Auto-Start Service"
call :log "Started at: %date% %time%"
call :log "=========================================="

:: Check if already running
tasklist /FI "IMAGENAME eq python.exe" /FI "WINDOWTITLE eq ZotMCP*" 2>nul | find /I "python.exe" >nul
if %errorlevel%==0 (
    call :log "ZotMCP server already running, exiting"
    exit /b 0
)

:: Calculate max attempts
set /a MAX_ATTEMPTS=%MAX_WAIT_MINUTES% * 60 / %CHECK_INTERVAL_SECONDS%
set ATTEMPT=0

:wait_for_zotero
set /a ATTEMPT+=1

call :log "Attempt %ATTEMPT%/%MAX_ATTEMPTS%: Checking Zotero status..."

:: Check if Zotero process is running
tasklist /FI "IMAGENAME eq zotero.exe" 2>nul | find /I "zotero.exe" >nul
if %errorlevel% neq 0 (
    call :log "  Zotero process not found, waiting %CHECK_INTERVAL_SECONDS%s..."
    timeout /t %CHECK_INTERVAL_SECONDS% /nobreak >nul
    if %ATTEMPT% lss %MAX_ATTEMPTS% goto wait_for_zotero
    call :log "ERROR: Zotero did not start within %MAX_WAIT_MINUTES% minutes"
    exit /b 1
)

:: Check if Zotero API is responding
set PYTHONPATH=src
set ZOTMCP_CREDENTIALS=%SCRIPT_DIR%..\..\private\credential.yml
.venv\Scripts\python.exe -c "import asyncio; from zotmcp.clients import create_client; from zotmcp.config import load_config; c = load_config(); client = create_client(c.zotero); exit(0 if asyncio.run(client.is_available()) else 1)" 2>nul
if %errorlevel% neq 0 (
    call :log "  Zotero process found but API not ready, waiting %CHECK_INTERVAL_SECONDS%s..."
    timeout /t %CHECK_INTERVAL_SECONDS% /nobreak >nul
    if %ATTEMPT% lss %MAX_ATTEMPTS% goto wait_for_zotero
    call :log "ERROR: Zotero API did not respond within %MAX_WAIT_MINUTES% minutes"
    exit /b 1
)

call :log "SUCCESS: Zotero is ready!"
call :log "Starting ZotMCP HTTP server on 0.0.0.0:8765..."

:: Start ZotMCP server (PYTHONPATH=src required for module resolution)
start "ZotMCP HTTP Server" /MIN cmd /c "cd /d %SCRIPT_DIR% && set PYTHONPATH=src
set ZOTMCP_CREDENTIALS=%SCRIPT_DIR%..\..\private\credential.yml && .venv\Scripts\python.exe -m zotmcp.cli serve --transport http --host 0.0.0.0 --port 8765"

:: Wait a bit and verify
timeout /t 3 /nobreak >nul

tasklist /FI "WINDOWTITLE eq ZotMCP*" 2>nul | find /I "python.exe" >nul
if %errorlevel%==0 (
    call :log "ZotMCP server started successfully"
    call :log "Server accessible at:"
    for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
        call :log "  - http://%%a:8765"
    )
) else (
    call :log "ERROR: Failed to start ZotMCP server"
    exit /b 1
)

call :log "=========================================="
exit /b 0

:log
echo [%date% %time%] %~1
echo [%date% %time%] %~1 >> "%LOG_FILE%"
goto :eof
