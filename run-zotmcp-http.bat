@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

if exist "%SCRIPT_DIR%.env" (
    for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%SCRIPT_DIR%.env") do (
        if not "%%a"=="" set "%%a=%%b"
    )
)

set "PYTHONPATH=src"
set "ZOTMCP_CREDENTIALS=%SCRIPT_DIR%..\..\private\credential.yml"

.venv\Scripts\python.exe -m zotmcp.cli serve --transport http --host 0.0.0.0 --port 8765 >> "%SCRIPT_DIR%server.log" 2>&1
