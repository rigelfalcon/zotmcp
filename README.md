# ZotMCP

A professional Zotero MCP (Model Context Protocol) server with remote access, resource safety, and optional semantic search.

## Features

### Core Capabilities
- **Multiple Backend Support**
  - Local Zotero API (port 23119) - recommended
  - Zotero Web API (pyzotero)
  - Direct SQLite database access (read-only)

- **63 Tools** across 13 categories
  - Search: keyword, semantic, citation key, saved searches, find similar
  - Read: metadata (markdown/json/bibtex), full text, annotations, PDF outline
  - Import: DOI, PMID, URL (auto-DOI detection), file; preprint support (arXiv/bioRxiv)
  - Write: update item fields, create/update notes, manage tags
  - Organize: collections CRUD, move/remove items, batch operations
  - PDF: fetch (Unpaywall/Sci-Hub/arXiv direct, linked files), find, copy, base64
  - Export: batch BibTeX, CSL citation rendering (APA/Nature/IEEE/etc.)
  - Metrics: iCite citation stats, preprint publication check, collection/library stats
  - Duplicates: find, merge, find duplicate PDFs
  - Trash: list, trash, restore
  - DOCX: scan and render citation placeholders
  - Sync: trigger library sync

### Key Features

- **Resource Safety**
  - Connection pooling (max 10 concurrent, 60s idle timeout)
  - Timeout handling (30s default with cleanup callbacks)
  - Memory monitoring (500MB warning threshold)
  - Pagination (100 items/page, 1000 max)

- **Remote Access**
  - HTTP/SSE transport for multi-computer setups
  - Configurable CORS origins
  - Bearer token authentication
  - One-click launcher script

- **Optional: Semantic Search** (requires extra install)
  - AI-powered similarity search using sentence transformers
  - ChromaDB vector storage with persistent indexing
  - Natural language queries (e.g., "papers about neural networks")

## Quick Start

### One-Click Setup (Windows)

```cmd
git clone https://github.com/user/zotmcp.git
cd zotmcp

:: For local Claude Desktop/Code (stdio mode)
start-server.bat

:: For remote access (HTTP mode)
start-server.bat http 8765 0.0.0.0
```

The script automatically:
1. Creates Python 3.11 virtual environment (using `uv` or `venv`)
2. Installs all dependencies including semantic search
3. Tests Zotero connection
4. Starts the server

### Manual Installation

```bash
# Clone repository
git clone https://github.com/user/zotmcp.git
cd zotmcp

# Create venv with uv (recommended)
uv venv --python 3.11 .venv

# Install with semantic search
uv pip install --python .venv/Scripts/python.exe -e ".[semantic]"

# Or basic install without semantic
uv pip install --python .venv/Scripts/python.exe -e .
```

## Architecture

### Single Computer Setup

```
┌─────────────────────────────────────────────────────────────┐
│                     Your Computer                           │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────┐  │
│  │   Zotero    │◄───│   ZotMCP    │◄───│ Claude Desktop  │  │
│  │ (port 23119)│    │  (stdio)    │    │   or Code       │  │
│  └─────────────┘    └─────────────┘    └─────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Multi-Computer Setup (Remote Access)

```
┌─────────────────────────────────────────────────────────────┐
│              Main Computer (with Zotero)                    │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────┐  │
│  │   Zotero    │◄───│   ZotMCP    │◄───│  HTTP Server    │  │
│  │ (port 23119)│    │   Server    │    │  (port 8765)    │  │
│  └─────────────┘    └─────────────┘    └────────┬────────┘  │
└─────────────────────────────────────────────────┼───────────┘
                                                  │
                    ┌─────────────────────────────┼─────────────────────────┐
                    │                             │                         │
                    ▼                             ▼                         ▼
              ┌──────────┐                  ┌──────────┐              ┌──────────┐
              │ Claude   │                  │ Other PC │              │  Mobile  │
              │ Desktop  │                  │ Claude   │              │  / Curl  │
              │ (local)  │                  │ Code     │              │          │
              └──────────┘                  └──────────┘              └──────────┘
```

## Configuration

### Claude Desktop

Add to `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or `~/Library/Application Support/Claude/claude_desktop_config.json` (Mac):

```json
{
  "mcpServers": {
    "zotero": {
      "command": "F:/path/to/zotmcp/.venv/Scripts/python.exe",
      "args": ["-m", "zotmcp.cli", "serve"],
      "cwd": "F:/path/to/zotmcp"
    }
  }
}
```

### Claude Code

Add to `~/.claude.json`:

```json
{
  "mcpServers": {
    "zotero": {
      "args": ["/c", "F:/path/to/zotmcp/.venv/Scripts/python.exe", "-m", "zotmcp.cli", "serve"],
      "command": "cmd",
      "env": {},
      "type": "stdio"
    }
  }
}
```

And add permission in `~/.claude/settings.json`:

```json
{
  "permissions": {
    "allow": ["mcp__zotero"]
  }
}
```

## Remote Access (HTTP Mode)

### Start HTTP Server

On the main computer (with Zotero installed):

```bash
# Using batch script
start-server.bat http 8765 0.0.0.0

# Or manually
.venv/Scripts/python.exe -m zotmcp.cli serve --transport http --host 0.0.0.0 --port 8765
```

### HTTP API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check and connection status |
| `/tools` | GET | List all available tools |
| `/tools/{tool_name}` | POST | Call a specific tool |
| `/sse` | GET | Server-Sent Events stream |

### API Examples

```bash
# Health check
curl http://192.168.x.x:8765/health

# List tools
curl http://192.168.x.x:8765/tools

# Search for papers
curl -X POST http://192.168.x.x:8765/tools/zotero_search \
  -H "Content-Type: application/json" \
  -d '{"query": "neural networks", "limit": 5}'

# Get item details
curl -X POST http://192.168.x.x:8765/tools/zotero_get_item \
  -H "Content-Type: application/json" \
  -d '{"item_key": "ABC12345"}'

# Semantic search (if enabled)
curl -X POST http://192.168.x.x:8765/tools/zotero_semantic_search \
  -H "Content-Type: application/json" \
  -d '{"query": "papers about 1/f noise in neural systems"}'
```

### Firewall Configuration (Windows)

```cmd
netsh advfirewall firewall add rule name="ZotMCP" dir=in action=allow protocol=tcp localport=8765
```

### Authentication (Optional)

Set API token in config for secure remote access:

```json
{
  "server": {
    "api_token": "your-secret-token"
  }
}
```

Then include in requests:

```bash
curl -H "Authorization: Bearer your-secret-token" http://192.168.x.x:8765/tools
```

## Available Tools (59 total)

### Search & Discovery

| Tool | Description |
|------|-------------|
| `zotero_search` | Keyword search across library |
| `zotero_semantic_search` | AI-powered semantic search (requires `[semantic]`) |
| `zotero_find_similar` | Find items similar to a given item |
| `zotero_search_by_citation_key` | Search by BetterBibTeX citation key |
| `zotero_search_notes` | Search note content |
| `zotero_get_recent` | Get recently added items |
| `zotero_list_saved_searches` | List saved Zotero searches |
| `zotero_run_saved_search` | Run a saved search |

### Item Management

| Tool | Description |
|------|-------------|
| `zotero_get_item` | Get metadata (markdown/json/bibtex) |
| `zotero_get_fulltext` | Get full text content (PDF extraction) |
| `zotero_get_item_children` | Get attachments and notes |
| `zotero_update_item` | Update item fields (title, type, date, etc.) |
| `zotero_trash_item` | Move item to trash |
| `zotero_restore_from_trash` | Restore item from trash |
| `zotero_list_trash` | List trashed items |

### Import

| Tool | Description |
|------|-------------|
| `zotero_add_by_doi` | Add by DOI (CrossRef + bioRxiv/arXiv fallback) |
| `zotero_add_by_pmid` | Add by PubMed ID |
| `zotero_add_by_url` | Add by URL (auto-detects DOI URLs) |
| `zotero_add_from_file` | Add from local PDF file |

### PDF

| Tool | Description |
|------|-------------|
| `zotero_fetch_pdf` | Download PDF (Unpaywall/Sci-Hub/arXiv/bioRxiv) as linked file |
| `zotero_find_pdf` | Find PDF on disk (Everything search) |
| `zotero_copy_pdf` | Copy PDF to target directory |
| `zotero_batch_copy_pdfs` | Batch copy PDFs |
| `zotero_get_pdf_base64` | Get PDF as base64 |
| `zotero_batch_get_pdfs_base64` | Batch get PDFs as base64 |
| `zotero_list_pdfs` | List PDF metadata |
| `zotero_get_pdf_outline` | Get PDF table of contents |
| `zotero_find_duplicate_pdfs` | Find duplicate PDFs by hash |
| `zotero_export_pdfs` | Export PDFs for item list to folder |
| `zotero_get_attachment_path` | Get full filesystem path for attachments |
| `zotero_export_collection` | Export all PDFs in a collection with index |

### Collections

| Tool | Description |
|------|-------------|
| `zotero_get_collections` | List all collections |
| `zotero_get_collection_items` | Get items in collection |
| `zotero_create_collection` | Create collection |
| `zotero_delete_collection` | Delete collection |
| `zotero_rename_collection` | Rename collection |
| `zotero_move_to_collection` | Move item to collection |
| `zotero_batch_move_to_collection` | Batch move items |
| `zotero_remove_from_collection` | Remove item (no delete) |
| `zotero_collection_stats` | Collection stats (PDF coverage, year/journal dist.) |

### Tags & Notes

| Tool | Description |
|------|-------------|
| `zotero_get_tags` | List all tags |
| `zotero_update_tags` | Add/remove tags |
| `zotero_batch_tags` | Batch update tags |
| `zotero_rename_tag` | Rename tag across all items |
| `zotero_create_note` | Create note |
| `zotero_get_notes` | Get notes |
| `zotero_update_note` | Update note |
| `zotero_delete_note` | Delete note |

### Annotations

| Tool | Description |
|------|-------------|
| `zotero_get_annotations` | Get PDF annotations |
| `zotero_create_annotation` | Create text highlight |
| `zotero_create_area_annotation` | Create area annotation |

### Export & Citation

| Tool | Description |
|------|-------------|
| `zotero_batch_export_bibtex` | Export items as BibTeX |
| `zotero_cite` | Render formatted citation (APA/Nature/IEEE/etc.) |

### Metrics & Status

| Tool | Description |
|------|-------------|
| `zotero_item_metrics` | NIH iCite citation metrics |
| `zotero_check_preprint_published` | Check if preprint is published |
| `zotero_library_stats` | Library-wide statistics |
| `zotero_status` | Connection status |
| `zotero_sync` | Trigger library sync |

### Duplicates

| Tool | Description |
|------|-------------|
| `zotero_find_duplicates` | Find duplicate items |
| `zotero_merge_duplicates` | Merge duplicates (dry-run default) |

### Semantic Search (requires `[semantic]`)

| Tool | Description |
|------|-------------|
| `zotero_semantic_search` | Semantic similarity search |
| `zotero_update_embeddings` | Update search index |

### DOCX Citation Workflow

| Tool | Description |
|------|-------------|
| `zotero_docx_scan_citations` | Scan DOCX for citation markers |
| `zotero_docx_render_citations` | Render citation placeholders |

## CLI Commands

```bash
# Start server (stdio mode for Claude)
zotmcp serve

# Start HTTP server for remote access
zotmcp serve --transport http --host 0.0.0.0 --port 8765

# Interactive setup wizard
zotmcp setup

# Check connection status
zotmcp status

# Search from command line
zotmcp search "query" --limit 10

# List collections
zotmcp collections
```

## Configuration File

Location: `~/.config/zotero-mcp-unified/config.json`

```json
{
  "zotero": {
    "mode": "local",
    "local_port": 23119
  },
  "semantic": {
    "enabled": true,
    "model_name": "all-MiniLM-L6-v2",
    "persist_directory": "~/.cache/zotero-mcp/chroma",
    "collection_name": "zotero_items",
    "batch_size": 50
  },
  "server": {
    "transport": "stdio",
    "host": "0.0.0.0",
    "port": 8765,
    "cors_origins": ["*"],
    "api_token": null,
    "log_level": "INFO"
  }
}
```

## Environment Variables

```bash
# Zotero connection
ZOTERO_LOCAL=true
ZOTERO_API_KEY=your_key
ZOTERO_LIBRARY_ID=your_id

# Semantic search
ZOTERO_SEMANTIC_ENABLED=true
ZOTERO_SEMANTIC_MODEL=all-MiniLM-L6-v2

# Server
ZOTERO_MCP_HOST=0.0.0.0
ZOTERO_MCP_PORT=8765
ZOTERO_MCP_TOKEN=your_token
```

## Troubleshooting

### "Zotero is not available"
- Ensure Zotero desktop app is running
- Check that Zotero connector is enabled in preferences
- Verify port 23119 is not blocked

### "Semantic search not available"
- Install with semantic dependencies: `uv pip install -e ".[semantic]"`
- Enable in config: `semantic.enabled=true`
- Run `zotero_update_embeddings` to build index

### Port already in use
```bash
# Find process using port
netstat -ano | findstr 8765

# Kill process
taskkill /F /PID <pid>
```

### Memory warnings
- Reduce batch size: `semantic.batch_size=25`
- Reduce pagination limit
- Monitor with: `zotmcp status`

## Project Structure

```
zotmcp/
├── src/zotmcp/
│   ├── clients.py     # Zotero backend clients (local, web, sqlite)
│   ├── config.py      # Configuration management
│   ├── server.py      # MCP server with 14 tools
│   ├── transport.py   # HTTP/SSE transport for remote access
│   ├── semantic.py    # Semantic search engine (optional)
│   ├── cli.py         # Command-line interface
│   └── utils.py       # Utility functions
├── tests/             # Test suite
├── start-server.bat   # One-click Windows launcher
├── pyproject.toml     # Package configuration
└── README.md
```

## Requirements

- Python 3.10+
- Zotero 7+ (for local API)
- Optional: ChromaDB + sentence-transformers (for semantic search)

## Credits & Acknowledgments

### About This Project

This project was brought to life through the magic of **vibe coding** - a collaboration between humans ([@rigelfalcon](https://github.com/rigelfalcon) & [@LMNonlinear](https://github.com/LMNonlinear/HarMNqEEG)) and AI (Claude). We asked, Claude coded, we vibed, bugs happened, more vibing ensued, and somehow it works! 🎉

*"We didn't write most of this code, but we definitely prompted it into existence."* - The Authors, probably

### Core Inspiration

This project stands on the shoulders of giants. Major thanks to these awesome projects:

- **[54yyyu/zotero-mcp](https://github.com/54yyyu/zotero-mcp)** - The OG Zotero MCP that showed us the way
- **[TonybotNi/ZotLink](https://github.com/TonybotNi/ZotLink)** - Brilliant Zotero integration patterns
- **[kujenga/zotero-mcp](https://github.com/kujenga/zotero-mcp)** - Clean MCP architecture reference

### Libraries & Frameworks
- **[FastMCP](https://github.com/jlowin/fastmcp)** - MCP server framework
- **[pyzotero](https://github.com/urschrei/pyzotero)** - Zotero Web API client
- **[ChromaDB](https://github.com/chroma-core/chroma)** - Vector database for semantic search
- **[sentence-transformers](https://github.com/UKPLab/sentence-transformers)** - Embedding models

### Special Thanks
- The [Zotero](https://www.zotero.org/) team for the excellent reference manager and local API
- The [Anthropic](https://www.anthropic.com/) team for Claude and the MCP protocol
- Coffee ☕ and late nights 🌙
- All contributors to the open-source libraries used in this project

## Disclaimer & Warnings

### ⚠️ IMPORTANT: Use at Your Own Risk

**This software is provided "AS IS" without warranty of any kind.**

1. **Not Fully Tested**: This project has NOT been comprehensively tested in all environments and use cases. There may be bugs, unexpected behaviors, or data loss scenarios.

2. **Write Operations Risk**: Tools that modify your Zotero library (`zotero_update_tags`, `zotero_batch_tags`, `zotero_create_note`, `zotero_move_to_collection`) can potentially:
   - Corrupt or delete your bibliographic data
   - Make unintended bulk changes
   - Cause data loss that may be difficult to recover

3. **Backup Recommended**: **ALWAYS backup your Zotero library** before using write operations. You can do this via Zotero's built-in export or by copying your Zotero data directory.

4. **Alpha Software**: This is alpha-stage software (version 0.x). APIs and functionality may change without notice.

5. **No Liability**: The authors and contributors are NOT responsible for any data loss, corruption, or other damages resulting from the use of this software.

### Safe Usage Guidelines

- Start with **read-only operations** (`zotero_search`, `zotero_get_item`, etc.) to familiarize yourself
- Test write operations on a **small subset** of items first
- Keep **regular backups** of your Zotero library
- Review the **source code** if you have concerns about specific operations

## License

MIT License

Copyright (c) 2024-2025 ZotMCP Contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
