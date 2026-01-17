---
name: zotero
description: Access and manage Zotero reference library through MCP tools. Use when working with academic references, citations, PDFs, or research papers. Triggers include searching references, getting item details, organizing collections, managing tags, creating notes, or using semantic/AI-powered search for finding similar papers.
metadata:
  short-description: Zotero library access via MCP
---

# ZotMCP - Zotero MCP Server

Access your Zotero reference library through Claude Code.

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

For detailed tool parameters and examples, see [references/tools.md](references/tools.md).

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

## Configuration

**Local Mode** (default): Connects to Zotero desktop (port 23119)

**Web Mode**: Set `ZOTERO_API_KEY` and `ZOTERO_LIBRARY_ID`

**Semantic Search**: Set `ZOTERO_SEMANTIC_ENABLED=true`

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Zotero not available" | Start Zotero desktop, enable connector |
| "Semantic search unavailable" | Install chromadb, sentence-transformers |
| Slow first search | Model download (~90MB) on first run |
