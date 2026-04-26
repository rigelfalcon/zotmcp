# ZotMCP TODO

## Completed (2026-04-26)

- [x] Basic search functionality
- [x] Item retrieval with metadata
- [x] Collection listing and browsing
- [x] Tag management (add/remove)
- [x] Note creation
- [x] Group library support
- [x] HTTP remote access transport
- [x] Semantic search foundation (ChromaDB integration)
- [x] Duplicate Detection — `zotero_find_duplicates`, `zotero_merge_duplicates`, `zotero_find_duplicate_pdfs`
- [x] Citation Formatting — `zotero_cite` (CSL rendering via Zotero Local API: APA, Nature, IEEE, etc.)
- [x] Batch BibTeX Export — `zotero_batch_export_bibtex`
- [x] Related Items Recommendation — `zotero_find_similar` (semantic + keyword fallback)
- [x] Library Statistics — `zotero_library_stats`, `zotero_collection_stats`
- [x] Full-text Search — `zotero_get_fulltext`, `zotero_search_notes`
- [x] Document tools in README.md — 59 tools documented
- [x] PDF fetch & attach — `zotero_fetch_pdf` (Unpaywall/Sci-Hub/arXiv/bioRxiv, linked file mode)
- [x] Add by PMID — `zotero_add_by_pmid`
- [x] Update item fields — `zotero_update_item` (with field validation)
- [x] Trash/restore — `zotero_trash_item`, `zotero_restore_from_trash`, `zotero_list_trash`
- [x] Remove from collection — `zotero_remove_from_collection`
- [x] Saved searches — `zotero_list_saved_searches`, `zotero_run_saved_search`
- [x] Trigger sync — `zotero_sync`
- [x] iCite metrics — `zotero_item_metrics`
- [x] Preprint update check — `zotero_check_preprint_published`
- [x] DOCX citation workflow — `zotero_docx_scan_citations`, `zotero_docx_render_citations`

## High Priority — DONE

### Batch PDF Export ✓
- [x] `zotero_export_pdfs` — export by key list with path resolution and summary report

### Attachment Path Retrieval ✓
- [x] `zotero_get_attachment_path` — resolves linked/stored paths, shows existence and size

## Medium Priority — DONE

### Collection Export ✓
- [x] `zotero_export_collection` — export all PDFs with flatten option and INDEX.md

### Batch Tag Operations Enhancement ✓
- [x] `zotero_rename_tag` — rename tag across all items
- [ ] Support regex patterns for tag matching (deferred)

## Low Priority (from Codex review 2026-04-26)

### Security (internal network — low risk)
- [ ] SSRF protection for `add_by_url` and `fetch_pdf` — validate URL scheme/host, block private/loopback IPs
- [ ] Sanitize exception messages in MCP responses — log details server-side, return generic errors

### Architecture
- [ ] Centralize Local API URL construction — tools hardcode `http://127.0.0.1:23119/api/users/0`, should use config
- [ ] Group library support for trash/restore — currently always uses `/users/` endpoint
- [ ] Linked attachment path traversal protection — validate paths stay under `linked_base`

### Code Quality
- [ ] Replace arXiv XML regex parsing with ElementTree/defusedxml
- [ ] Support old-style arXiv IDs (e.g., `hep-th/9901001`) in DOI extraction
- [ ] Wire up semantic search in `find_similar` properly (currently falls back to keyword)

### Technical Debt
- [ ] Add comprehensive unit tests for new tools
- [ ] Add integration tests with mock Zotero API
- [ ] Add type hints throughout codebase
- [ ] Implement proper logging levels

## Bug Fixes
- [ ] Handle duplicate entries in search results
- [ ] Graceful handling of missing PDF files in attachment records
- [ ] Improve error messages for connection failures
