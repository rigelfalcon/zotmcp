# ZotMCP TODO

## High Priority

### Batch PDF Export
- [ ] Implement `zotero_export_pdfs(keys: list[str], target_folder: str)` tool
- [ ] Export multiple PDFs in a single operation
- [ ] Auto-resolve `attachments:` prefix to full path
- [ ] Handle missing PDFs gracefully with summary report

### Duplicate Detection
- [ ] Implement `zotero_find_duplicates()` tool
- [ ] Detect duplicates by DOI, ISBN, title similarity
- [ ] Return grouped duplicate sets with merge suggestions
- [ ] Option to auto-merge keeping best metadata

### Attachment Path Retrieval
- [ ] Implement `zotero_get_attachment_path(key: str)` tool
- [ ] Return full filesystem path for linked/stored attachments
- [ ] Support both user library and group libraries

## Medium Priority

### Collection Export
- [ ] Implement `zotero_export_collection(collection_key: str, target_folder: str)` tool
- [ ] Export all PDFs in a collection to target folder
- [ ] Preserve or flatten folder structure option
- [ ] Generate index file with metadata

### Citation Formatting
- [ ] Implement `zotero_format_citation(keys: list[str], style: str)` tool
- [ ] Support BibTeX, APA, MLA, Chicago, IEEE styles
- [ ] Batch format multiple citations
- [ ] Return formatted string ready for use

### Batch Tag Operations
- [ ] Enhance `zotero_batch_tags` with more filtering options
- [ ] Support regex patterns for tag matching
- [ ] Add tag rename functionality

## Low Priority

### Full-text Search
- [ ] Implement `zotero_fulltext_search(query: str)` tool
- [ ] Search within indexed PDF content
- [ ] Return matching items with context snippets
- [ ] Integrate with semantic search when enabled

### Related Items Recommendation
- [ ] Implement `zotero_find_related(key: str, limit: int)` tool
- [ ] Use semantic similarity to find related papers
- [ ] Consider citation relationships if available
- [ ] Return ranked list with similarity scores

### Library Statistics
- [ ] Implement `zotero_stats()` tool
- [ ] Return item counts by type, year, collection
- [ ] Storage usage information
- [ ] Tag cloud / frequency analysis

## Bug Fixes

- [ ] Handle duplicate entries in search results (same item appears multiple times)
- [ ] Graceful handling of missing PDF files in attachment records
- [ ] Improve error messages for connection failures

## Technical Debt

- [ ] Add comprehensive unit tests for new tools
- [ ] Add integration tests with mock Zotero API
- [ ] Document all tools in README.md
- [ ] Add type hints throughout codebase
- [ ] Implement proper logging levels

## Completed

- [x] Basic search functionality
- [x] Item retrieval with metadata
- [x] Collection listing and browsing
- [x] Tag management (add/remove)
- [x] Note creation
- [x] Group library support
- [x] HTTP remote access transport
- [x] Semantic search foundation (ChromaDB integration)
