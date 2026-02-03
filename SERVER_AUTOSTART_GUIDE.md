# ZotMCP Server Auto-Start Documentation

## Overview

This documentation describes the auto-start mechanism for ZotMCP HTTP server that waits for Zotero to be ready before starting.

---

## Files Created

| File | Purpose |
|------|---------|
| `autostart-zotmcp.bat` | Main auto-start script with Zotero detection |
| `create-autostart-task.bat` | Windows Task Scheduler setup helper |
| `autostart.log` | Runtime log file (auto-generated) |

---

## How It Works

```
System Boot → User Login
  ↓
Windows Task Scheduler triggers autostart-zotmcp.bat
  ↓
Check every 10 seconds: Is Zotero running?
  ├─ No → Wait (max 30 minutes)
  └─ Yes → Check: Is Zotero API ready (port 23119)?
           ├─ No → Wait
           └─ Yes → Start ZotMCP HTTP Server
                    ↓
                  Server running on 0.0.0.0:8765
                    ↓
                  Accessible from LAN:
                    - http://192.168.8.31:8765
                    - http://192.168.8.5:8765
```

---

## Configuration Details

### Auto-Start Script Parameters

```batch
MAX_WAIT_MINUTES=30          # Maximum wait time for Zotero
CHECK_INTERVAL_SECONDS=10    # Check Zotero every 10 seconds
SERVER_HOST=0.0.0.0          # Listen on all network interfaces
SERVER_PORT=8765             # HTTP server port
SEMANTIC_ENABLED=true        # Enable semantic search
```

### Task Scheduler Settings

- **Task Name**: `ZotMCP-AutoStart`
- **Trigger**: At user logon
- **Action**: Run `autostart-zotmcp.bat`
- **Run Level**: Highest (Administrator)
- **Status**: Enabled

---

## Installation (Already Done)

✅ Task scheduler has been configured and is ready.

### Verification

```bash
# Check task status
schtasks /query /tn "ZotMCP-AutoStart"

# Expected output:
# TaskName: ZotMCP-AutoStart
# Status: Ready
# Schedule Type: At logon time
```

---

## Manual Operations

### Start Server Manually (For Testing)

```bash
cd F:\code\tools\mng\publish\zotmcp
autostart-zotmcp.bat
```

### Check Server Status

```bash
# Check if server is running
tasklist /FI "WINDOWTITLE eq ZotMCP*"

# Test server health endpoint
curl http://localhost:8765/health
curl http://192.168.8.31:8765/health
```

### View Logs

```bash
# View full log
type F:\code\tools\mng\publish\zotmcp\autostart.log

# Tail recent logs (last 20 lines)
powershell "Get-Content F:\code\tools\mng\publish\zotmcp\autostart.log -Tail 20"
```

### Stop Server

```bash
# Kill ZotMCP server process
taskkill /FI "WINDOWTITLE eq ZotMCP*" /F
```

---

## Troubleshooting

### Server Not Starting After Login

1. **Check if Zotero is running**:
   ```bash
   tasklist /FI "IMAGENAME eq zotero.exe"
   ```

2. **Check logs**:
   ```bash
   type F:\code\tools\mng\publish\zotmcp\autostart.log
   ```

3. **Manual test**:
   ```bash
   cd F:\code\tools\mng\publish\zotmcp
   autostart-zotmcp.bat
   ```

### Error: "Zotero API not ready"

- Zotero may be starting up slowly
- Check Zotero Connector is enabled:
  - Zotero → Tools → Preferences → Advanced → Enabled

### Error: "Server already running"

- Script detects existing instance and exits
- This is normal behavior to prevent duplicates

### Port 8765 Already in Use

```bash
# Find process using port 8765
netstat -ano | findstr :8765

# Kill the process (replace PID)
taskkill /PID <PID> /F
```

### Firewall Blocking Connections

1. Open Windows Defender Firewall
2. Advanced Settings → Inbound Rules → New Rule
3. Program: `F:\code\tools\mng\publish\zotmcp\.venv\Scripts\python.exe`
4. Allow connection on port 8765

---

## Log File Examples

### Successful Startup

```
[2026-02-03 10:00:00] ==========================================
[2026-02-03 10:00:00] ZotMCP Auto-Start Service
[2026-02-03 10:00:00] Started at: 2026-02-03 10:00:00
[2026-02-03 10:00:00] ==========================================
[2026-02-03 10:00:00] Attempt 1/180: Checking Zotero status...
[2026-02-03 10:00:00] SUCCESS: Zotero is ready!
[2026-02-03 10:00:00] Starting ZotMCP HTTP server on 0.0.0.0:8765...
[2026-02-03 10:00:03] ZotMCP server started successfully
[2026-02-03 10:00:03] Server accessible at:
[2026-02-03 10:00:03]   - http://192.168.8.31:8765
[2026-02-03 10:00:03]   - http://192.168.8.5:8765
[2026-02-03 10:00:03] ==========================================
```

### Waiting for Zotero

```
[2026-02-03 10:00:00] Attempt 1/180: Checking Zotero status...
[2026-02-03 10:00:00]   Zotero process not found, waiting 10s...
[2026-02-03 10:00:10] Attempt 2/180: Checking Zotero status...
[2026-02-03 10:00:10]   Zotero process found but API not ready, waiting 10s...
[2026-02-03 10:00:20] Attempt 3/180: Checking Zotero status...
[2026-02-03 10:00:20] SUCCESS: Zotero is ready!
```

---

## Maintenance

### Update Auto-Start Script

1. Edit `autostart-zotmcp.bat`
2. No need to recreate task - it will use the updated script

### Change Server Port

Edit `autostart-zotmcp.bat`, change:
```batch
--port 8765
```
to your desired port.

### Disable Auto-Start

```bash
# Disable task (keeps settings)
schtasks /change /tn "ZotMCP-AutoStart" /disable

# Enable again
schtasks /change /tn "ZotMCP-AutoStart" /enable

# Delete task completely
schtasks /delete /tn "ZotMCP-AutoStart" /f
```

---

## Security Considerations

### Current Setup (LAN Only)

- **No authentication** - Anyone on LAN can access
- **HTTP only** - No encryption
- **All interfaces** - Listening on 0.0.0.0

**Safe for**: Home/office LAN environment

### Enhanced Security (Optional)

To add API token authentication, edit server config:

`~/.config/zotero-mcp-unified/config.json`:
```json
{
  "server": {
    "api_token": "your-secret-token-here"
  }
}
```

Clients must include token in requests:
```bash
curl -H "Authorization: Bearer your-secret-token-here" \
     http://192.168.8.31:8765/health
```

---

## Next Steps

1. ✅ Server auto-start is configured
2. 📝 Share `LAN_CLIENT_SETUP.md` with LAN clients
3. 🧪 Test from another device:
   ```bash
   curl http://192.168.8.31:8765/health
   ```
4. 🔄 Reboot to verify auto-start works

---

## Support

- **Server Logs**: `F:\code\tools\mng\publish\zotmcp\autostart.log`
- **Client Setup**: See `LAN_CLIENT_SETUP.md`
- **Zotero API**: http://localhost:23119
- **Server Health**: http://192.168.8.31:8765/health
