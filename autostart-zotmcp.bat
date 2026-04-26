@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "LOG_FILE=%SCRIPT_DIR%autostart.log"
cd /d "%SCRIPT_DIR%"

call :log "=========================================="
call :log "ZotMCP Auto-Start"
call :log "Started at: %date% %time%"
call :log "=========================================="

if exist "%SCRIPT_DIR%.env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%.env") do (
        if not "%%a"=="" set "%%a=%%b"
    )
    call :log "Loaded .env"
)

call :is_listening
if not errorlevel 1 (
    call :log "ZotMCP already listening on port 8765; exiting"
    exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
    call :log "ERROR: .venv\Scripts\python.exe not found"
    exit /b 1
)

set "PYTHONPATH=src"
set "ZOTMCP_CREDENTIALS=%SCRIPT_DIR%..\..\private\credential.yml"

call :log "Checking Zotero availability before start..."
.venv\Scripts\python.exe -c "import asyncio; from zotmcp.clients import create_client; from zotmcp.config import load_config; c = load_config(); client = create_client(c.zotero); exit(0 if asyncio.run(client.is_available()) else 1)" >nul 2>nul
if %errorlevel%==0 (
    call :log "Zotero API is available"
) else (
    call :log "WARNING: Zotero API is not ready; starting ZotMCP anyway"
)

call :log "Starting ZotMCP HTTP server on 0.0.0.0:8765"
start "ZotMCP HTTP Server" /MIN "%SCRIPT_DIR%run-zotmcp-http.bat"

powershell -NoProfile -Command "Start-Sleep -Seconds 5" >nul 2>nul
call :is_listening
if not errorlevel 1 (
    call :log "SUCCESS: ZotMCP is listening on port 8765"
    exit /b 0
)

call :log "ERROR: ZotMCP did not start; check server.log"
exit /b 1

:log
echo [%date% %time%] %~1
echo [%date% %time%] %~1 >> "%LOG_FILE%"
goto :eof

:is_listening
netstat -ano | findstr ":8765" | findstr "LISTENING" >nul 2>nul
exit /b %errorlevel%
