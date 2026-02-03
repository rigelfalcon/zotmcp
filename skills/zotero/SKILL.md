---
name: zotero
description: Access and manage Zotero reference library through MCP tools. Use when working with academic references, citations, PDFs, or research papers. Triggers include searching references, getting item details, organizing collections, managing tags, creating notes, semantic/AI-powered search, or finding/downloading PDF files from local disk.
metadata:
  short-description: Zotero library access via MCP
---

# ZotMCP - Zotero MCP Server

Access your Zotero reference library and local PDF files through Claude Code.

## Quick Start

1. Ensure Zotero desktop is running with connector enabled
2. Use `zotero_status` to verify connection
3. Search with `zotero_search` or `zotero_semantic_search`

## Core Tools

| Tool | Purpose |
|------|---------|
| `zotero_search` | Keyword search with filters |
| `zotero_semantic_search` | AI-powered similarity search |
| `zotero_get_item` | Get item metadata (markdown/json/bibtex) |
| `zotero_get_fulltext` | Extract PDF text content |
| `zotero_get_collections` | List collection hierarchy |
| `zotero_get_tags` | List all tags |
| `zotero_update_tags` | Add/remove tags on item |
| `zotero_create_note` | Attach note to item |
| `zotero_status` | Check connection status |

## PDF File Tools (Everything Integration)

These tools use Everything search engine to find and manage PDF files on the server's local disk.

**Prerequisites:** Everything HTTP server enabled (Tools > Options > HTTP Server > Enable, Port 9090)

| Tool | Purpose | Remote Support |
|------|---------|----------------|
| `zotero_find_pdf` | Search PDFs by author/title/year | Yes |
| `zotero_list_pdfs` | List PDF metadata without download | Yes |
| `zotero_copy_pdf` | Copy single PDF to target directory | Local only |
| `zotero_batch_copy_pdfs` | Batch copy multiple PDFs | Local only |
| `zotero_get_pdf_base64` | Download single PDF as base64 | **Yes (Remote)** |
| `zotero_batch_get_pdfs_base64` | Batch download PDFs as base64 | **Yes (Remote)** |

## Common Workflows

**Find papers on a topic:**
```
zotero_semantic_search query="neural network optimization" limit=10
```

**Get citation for a paper:**
```
zotero_get_item item_key="ABC123" format="bibtex"
```

**Organize with tags:**
```
zotero_batch_tags query="machine learning" add_tags=["to-review"]
```

**Find PDF files locally:**
```
zotero_find_pdf query="Cole Voytek 2017 waveform" limit=5
```

**Copy PDFs to a directory (local):**
```
zotero_batch_copy_pdfs queries=["Cole 2017", "Bartz 2019"] target_dir="./refs" filenames=["Cole_2017", "Bartz_2019"]
```

**Download PDF remotely (base64):**
```
zotero_get_pdf_base64 query="Cole Voytek 2017 waveform"
```

## Remote PDF Download (LAN Clients)

Remote clients can download PDFs from the server using base64 encoding:

1. **Search first:** `zotero_list_pdfs query="author name"`
2. **Download:** `zotero_get_pdf_base64 query="exact search terms"`
3. **Decode on client:**
```python
import base64, json
data = json.loads(response)
if data.get("success"):
    with open(data["filename"], "wb") as f:
        f.write(base64.b64decode(data["content_base64"]))
```

## Configuration

**Local Mode** (default): Connects to Zotero desktop (port 23119)

**Web Mode**: Set `ZOTERO_API_KEY` and `ZOTERO_LIBRARY_ID`

**Semantic Search**: Set `ZOTERO_SEMANTIC_ENABLED=true`

**Everything Search**: Enable HTTP server in Everything (localhost:9090)

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Zotero not available" | Start Zotero desktop, enable connector |
| "Semantic search unavailable" | Install chromadb, sentence-transformers |
| Slow first search | Model download (~90MB) on first run |
| "No PDF found" | Enable Everything HTTP server (port 9090) |
| Remote PDF download fails | Check file size < 50MB |
