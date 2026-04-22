# ZotMCP LAN Client Setup

Connect Claude Code (or any MCP client) to a running ZotMCP server on your LAN.

## Prerequisites

- ZotMCP server running (see README.md for server setup)
- Node.js installed on the client machine
- Claude Code installed

## Step 1: Find the Server IP

Ask the server admin, or on the server machine run:
```cmd
ipconfig | findstr "IPv4"
```
The server listens on port **8765** by default.

## Step 2: Test Connectivity

```bash
# Replace <SERVER_IP> with actual IP
ping <SERVER_IP>
curl http://<SERVER_IP>:8765/health
# Expected: {"status": "healthy", "zotero_available": true, ...}
```

## Step 3: Configure Claude Code

Edit `~/.claude.json` (or `%USERPROFILE%\.claude.json` on Windows).

Add under `"mcpServers"`:

```json
"zotmcp": {
  "command": "npx",
  "args": [
    "-y", "mcp-remote",
    "http://<SERVER_IP>:8765/sse",
    "--allow-http",
    "--transport", "sse-only"
  ]
}
```

**Key flags:**
- `--allow-http` — required because the server is HTTP (not HTTPS) on LAN
- `--transport sse-only` — forces legacy SSE mode; without this, `mcp-remote`
  tries Streamable HTTP first and may hang

## Step 4: Add Permissions

Edit `~/.claude/settings.json`, add to `permissions.allow`:

```json
"mcp__zotmcp"
```

## Step 5: Restart Claude Code

MCP servers load at startup. Restart, then verify:

```
/mcp
```

`zotmcp` should show **connected** with 25 tools.

## Quick Test

After connecting, try:
```
Search my Zotero for "neural networks"
```

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `HTTP 405: Invalid OAuth error response` | Server missing 404 catch-all | Update server `transport.py` |
| `Non-HTTPS URLs are only allowed for localhost` | Missing `--allow-http` | Add `--allow-http` to args |
| Connecting... then timeout | Missing `--transport sse-only` | Add `--transport sse-only` to args |
| `ModuleNotFoundError: No module named 'zotmcp'` | Server PYTHONPATH not set | Use `start-server.bat` to launch |
| Wrong tools / different server | Public `zotero-mcp` on same port | Check process: `netstat -ano \| findstr 8765` |
| Proxy interference | HTTP_PROXY routes LAN traffic | Bypass LAN IPs in proxy settings |

## Alternative Clients

### OpenCode / Cursor / Other MCP Clients

Use SSE transport directly if the client supports it:
- SSE endpoint: `http://<SERVER_IP>:8765/sse`
- Messages endpoint: `http://<SERVER_IP>:8765/messages`

### Direct HTTP API

```bash
# List tools
curl http://<SERVER_IP>:8765/tools

# Search
curl -X POST http://<SERVER_IP>:8765/tools/zotero_search \
  -H "Content-Type: application/json" \
  -d '{"query": "neural networks", "limit": 5}'
```
