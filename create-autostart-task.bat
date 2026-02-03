@echo off
:: Create ZotMCP Auto-Start Task
:: Run this script as Administrator

echo Creating ZotMCP auto-start scheduled task...
echo.

schtasks /create /tn "ZotMCP-AutoStart" /tr "F:\code\tools\mng\publish\zotmcp\autostart-zotmcp.bat" /sc onlogon /rl highest /f

if %errorlevel%==0 (
    echo.
    echo ========================================
    echo SUCCESS: Task created successfully!
    echo ========================================
    echo.
    echo Task Name: ZotMCP-AutoStart
    echo Trigger: On user logon
    echo Action: Start ZotMCP server
    echo.
    echo To verify:
    echo   schtasks /query /tn "ZotMCP-AutoStart"
    echo.
    echo To delete:
    echo   schtasks /delete /tn "ZotMCP-AutoStart" /f
    echo.
) else (
    echo.
    echo ========================================
    echo ERROR: Failed to create task
    echo ========================================
    echo.
    echo Please run this script as Administrator:
    echo   1. Right-click this file
    echo   2. Select "Run as administrator"
    echo.
)

pause
