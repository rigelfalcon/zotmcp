# ZotMCP LAN Client Setup Guide

## Server Information

**Server Host**: `192.168.8.31` (Ethernet) or `192.168.8.5` (WiFi)
**Server Port**: `8765` (default)
**Protocol**: HTTP with SSE (Server-Sent Events)

---

## Quick Start

### 1. Download ZotMCP

```bash
git clone https://github.com/YOUR_USERNAME/zotmcp.git
cd zotmcp
```

### 2. Install Dependencies

**Option A: Using uv (recommended, faster)**
```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/Scripts/python.exe -e ".[semantic]"
```

**Option B: Using standard pip**
```bash
python -m venv .venv
.venv\Scripts\pip.exe install -e ".[semantic]"
```

### 3. Configure Client

Create or edit `~/.config/zotero-mcp-unified/config.json`:

```json
{
  "zotero": {
    "mode": "http",
    "http_server": {
      "host": "192.168.8.31",
      "port": 8765,
      "api_token": null
    }
  },
  "semantic": {
    "enabled": false
  }
}
```

**Note**: Semantic search runs on server side only, client doesn't need it.

### 4. Test Connection

```bash
# Activate virtual environment
.venv\Scripts\activate

# Test server connectivity
python -c "import requests; r = requests.get('http://192.168.8.31:8765/health'); print(r.json())"
```

Expected output:
```json
{"status": "ok", "zotero": "connected", "semantic": true}
```

---

## Claude Desktop/Code Integration

### Add to MCP Settings

**File**: `%APPDATA%\Claude\claude_desktop_config.json` (Windows)
**File**: `~/.config/Claude/claude_desktop_config.json` (Linux/Mac)

```json
{
  "mcpServers": {
    "zotero-lan": {
      "command": "python",
      "args": [
        "-m",
        "zotmcp.cli",
        "serve",
        "--transport",
        "http",
        "--host",
        "192.168.8.31",
        "--port",
        "8765"
      ],
      "env": {
        "ZOTERO_SEMANTIC_ENABLED": "false"
      }
    }
  }
}
```

**Important**: Use full path to Python if needed:
```json
"command": "C:\path\to\zotmcp\.venv\Scripts\python.exe"
```

---

## Usage Examples

### From Command Line

```bash
# List available tools
curl http://192.168.8.31:8765/tools

# Search for papers
curl -X POST http://192.168.8.31:8765/call_tool \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"zotero_search\", \"arguments\": {\"query\": \"NARMAX\", \"limit\": 5}}"

# Get item details
curl -X POST http://192.168.8.31:8765/call_tool \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"zotero_get_item\", \"arguments\": {\"item_key\": \"ABC123\", \"format\": \"markdown\"}}"
```

### From Claude

Once configured, use natural language:

```
"Search my Zotero library for papers about Volterra series"
"Get the citation for item ABC123 in BibTeX format"
"Show me recent papers in the GFRF collection"
```

---

## Troubleshooting

### Cannot Connect to Server

1. **Check server is running**:
   ```bash
   curl http://192.168.8.31:8765/health
   ```

2. **Ping server**:
   ```bash
   ping 192.168.8.31
   ```

3. **Check firewall**: Windows Firewall may block port 8765
   - Allow Python through firewall
   - Or disable firewall temporarily for testing

4. **Try alternate IP**: Use `192.168.8.5` if Ethernet IP doesn't work

### Server Shows "Zotero Not Available"

- Ensure Zotero desktop is running on **server machine** (`192.168.8.31`)
- Zotero Connector must be enabled (Tools → Preferences → Advanced)

### Slow First Search

- Server downloads embedding model (~90MB) on first semantic search
- Subsequent searches are fast (model cached)

---

## Security Notes

- **No authentication** in current setup (LAN only)
- **Do not expose** port 8765 to internet
- For added security, enable `api_token` in server config

---

## Advanced: Enable API Token

### Server Config

Edit server's `config.json`:
```json
{
  "server": {
    "api_token": "your-secret-token-here"
  }
}
```

### Client Config

Update client's `config.json`:
```json
{
  "zotero": {
    "http_server": {
      "host": "192.168.8.31",
      "port": 8765,
      "api_token": "your-secret-token-here"
    }
  }
}
```

---

## Support

- GitHub Repository: Check README.md for latest updates
- Documentation: See `skills/zotero/` folder
- Server IP may change on reboot - check with `ipconfig` on server
