# ZotMCP Tool Reference

## Search & Discovery

### zotero_search
Search library by keywords.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | string | yes | - | Search keywords |
| limit | int | no | 10 | Max results |
| item_type | string | no | - | Filter: journalArticle, book, etc. |
| tags | list[string] | no | - | Filter by tags |

### zotero_semantic_search
AI-powered semantic similarity search.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | string | yes | - | Natural language query |
| limit | int | no | 10 | Max results |
| item_type | string | no | - | Filter by type |

Requires `semantic.enabled=true` in config.

### zotero_get_recent
Get recently added items.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| limit | int | no | 10 | Number of items |

## Item Details

### zotero_get_item
Get detailed metadata.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| item_key | string | yes | - | Zotero item key |
| format | string | no | markdown | Output: markdown, json, bibtex |

### zotero_get_fulltext
Extract full text from PDF attachments.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| item_key | string | yes | - | Zotero item key |

## Collections

### zotero_get_collections
List all collections (hierarchical tree).

No parameters.

### zotero_get_collection_items
Get items in a collection.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| collection_key | string | yes | - | Collection key |
| limit | int | no | 50 | Max items |

### zotero_move_to_collection
Move item to collection.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| item_key | string | yes | - | Item to move |
| collection_key | string | yes | - | Target collection |

## Tags

### zotero_get_tags
List all tags (alphabetically grouped).

No parameters.

### zotero_update_tags
Add/remove tags from single item.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| item_key | string | yes | - | Item key |
| add_tags | list[string] | no | - | Tags to add |
| remove_tags | list[string] | no | - | Tags to remove |

### zotero_batch_tags
Batch update tags across multiple items.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | string | yes | - | Search query to find items |
| add_tags | list[string] | no | - | Tags to add |
| remove_tags | list[string] | no | - | Tags to remove |
| limit | int | no | 50 | Max items to process |

## Notes

### zotero_create_note
Create note attached to item.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| item_key | string | yes | - | Parent item key |
| content | string | yes | - | Note content (HTML or plain) |
| tags | list[string] | no | - | Tags for note |

## Semantic Search Management

### zotero_update_embeddings
Update semantic search index.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| limit | int | no | 100 | Max items to process |
| force | bool | no | false | Re-embed existing items |

## Status

### zotero_status
Check connection and library status.

No parameters. Returns server status, mode, and library statistics.

## PDF File Management (Everything Integration)

### zotero_find_pdf
Search for PDF files on local disk using Everything.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | string | yes | - | Search query (author, title, year) |
| limit | int | no | 10 | Max results |

**Prerequisites:** Everything HTTP server enabled on localhost:9090

### zotero_list_pdfs
List PDF metadata without downloading content.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | string | yes | - | Search query |
| limit | int | no | 10 | Max results |

Returns JSON with filename, path, and size. Use this before downloading.

### zotero_copy_pdf
Copy single PDF to target directory (local only).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | string | yes | - | Search query |
| target_dir | string | yes | - | Target directory path |
| new_filename | string | no | - | New filename (without .pdf) |
| limit | int | no | 1 | Number of files to copy |

### zotero_batch_copy_pdfs
Batch copy multiple PDFs (local only).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| queries | list[string] | yes | - | List of search queries |
| target_dir | string | yes | - | Target directory path |
| filenames | list[string] | no | - | New filenames (parallel to queries) |

### zotero_get_pdf_base64
Download single PDF as base64 (remote capable).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| query | string | yes | - | Search query |

Returns JSON with base64-encoded PDF content. Max file size: 50MB.

**Remote Usage:** Decode base64 on client side to save PDF.

### zotero_batch_get_pdfs_base64
Batch download multiple PDFs as base64 (remote capable).

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| queries | list[string] | yes | - | List of search queries |

Returns JSON array with results for each query. Max 50MB per file.

## Configuration Reference

### Environment Variables

```bash
ZOTERO_LOCAL=true                       # Use local mode
ZOTERO_API_KEY=your_key                 # Web API key
ZOTERO_LIBRARY_ID=your_id               # Library ID
ZOTERO_SEMANTIC_ENABLED=true            # Enable semantic search
ZOTERO_SEMANTIC_MODEL=all-MiniLM-L6-v2  # Embedding model
```

### Config File

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
    "persist_directory": "~/.cache/zotero-mcp/chroma"
  }
}
```

## Safety Limits

- Connection pool: Max 10 concurrent connections
- Timeout: 30s default with cleanup
- Pagination: 100 items/page, 1000 max total
- Memory monitoring: 500MB warning threshold
