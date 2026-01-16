@echo off
setlocal enabledelayedexpansion

:: ZotMCP Server Launcher
:: Usage: start-server.bat [mode] [port] [host]
::   mode: stdio (default), http, sse
::   port: 8765 (default for http/sse)
::   host: 0.0.0.0 (default for http/sse)

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

set "MODE=%~1"
set "PORT=%~2"
set "HOST=%~3"

if "%MODE%"=="" set "MODE=stdio"
if "%PORT%"=="" set "PORT=8765"
if "%HOST%"=="" set "HOST=0.0.0.0"

echo ============================================
echo   ZotMCP Server Launcher
echo ============================================
echo.

:: Check if .venv exists
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating Python virtual environment...

    :: Try uv first, fallback to python -m venv
    where uv >nul 2>&1
    if !errorlevel! equ 0 (
        uv venv --python 3.11 .venv
    ) else (
        python -m venv .venv
    )

    if not exist ".venv\Scripts\python.exe" (
        echo ERROR: Failed to create virtual environment
        echo Please install Python 3.10+ and try again
        pause
        exit /b 1
    )

    echo [2/3] Installing dependencies...

    :: Try uv pip first, fallback to pip
    where uv >nul 2>&1
    if !errorlevel! equ 0 (
        uv pip install --python .venv\Scripts\python.exe -e ".[semantic]"
    ) else (
        .venv\Scripts\pip.exe install -e ".[semantic]"
    )

    echo [3/3] Setup complete!
    echo.
) else (
    echo [OK] Virtual environment found
)

:: Check Zotero connection
echo.
echo Checking Zotero connection...
.venv\Scripts\python.exe -c "import asyncio; from zotmcp.clients import create_client; from zotmcp.config import load_config; c = load_config(); client = create_client(c.zotero); print('Zotero:', 'Connected' if asyncio.run(client.is_available()) else 'Not Available')"

echo.
echo ============================================

if "%MODE%"=="stdio" (
    echo Starting in STDIO mode (for Claude Desktop/Code)
    echo ============================================
    .venv\Scripts\python.exe -m zotmcp.cli serve --transport stdio
) else (
    echo Starting HTTP server on %HOST%:%PORT%
    echo.
    echo Remote access endpoints:
    echo   Health:  http://%HOST%:%PORT%/health
    echo   Tools:   http://%HOST%:%PORT%/tools
    echo   SSE:     http://%HOST%:%PORT%/sse
    echo ============================================
    echo.
    .venv\Scripts\python.exe -m zotmcp.cli serve --transport %MODE% --host %HOST% --port %PORT%
)

pause
