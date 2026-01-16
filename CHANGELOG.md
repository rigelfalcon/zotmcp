# Changelog

All notable changes to ZotMCP will be documented in this file.

## [2.0.0] - 2025-01-15

### Added

#### Semantic Search
- AI-powered semantic search using sentence transformers
- ChromaDB vector storage with persistent indexing
- Natural language queries for finding similar papers
- Configurable embedding models (default: all-MiniLM-L6-v2)
- Batched embedding generation (50 items/batch)
- New tools: `zotero_semantic_search`, `zotero_update_embeddings`

#### Resource Safety
- Connection pooling with semaphore limiting (max 10 concurrent)
- Automatic connection cleanup (60s idle timeout)
- Timeout handling with cleanup callbacks (30s default)
- Memory monitoring with warning threshold (500MB)
- Pagination for large result sets (100 items/page, 1000 max)
- Streaming full text in chunks (8KB)
- Structured error hierarchy with 6 error types

#### Enhanced Transport
- Configurable CORS origins (default: ["*"])
- Client timeout header support (X-Timeout)
- Graceful shutdown with signal handlers (SIGTERM, SIGINT)
- SSE heartbeat already present (30s interval)
- Bearer token authentication already present

#### Testing & Quality
- Comprehensive test suite (452 lines)
- Unit tests for pool, timeout, iterators, semantic
- Integration tests for Zotero backends
- Safety tests for resource management
- pytest configuration with asyncio support

#### Documentation
- Claude Skill definition with usage patterns
- Updated README with v2 features
- Configuration guide with examples
- Troubleshooting section
- Architecture overview

### Changed
- Renamed project from zotero-mcp-unified to ZotMCP
- Updated config schema with semantic and CORS settings
- Enhanced server lifespan to manage semantic engine
- Improved error messages with structured error types

### Technical Details
- 5 new modules: pool.py, timeout.py, monitor.py, iterators.py, semantic.py
- 2 new MCP tools for semantic search
- 1,661+ lines of new code
- 452 lines of tests
- 190 lines of documentation

## [1.0.0] - 2024-12-XX

### Initial Release
- Multiple backend support (local, web, sqlite)
- 12 MCP tools for Zotero operations
- HTTP/SSE transport for remote access
- API token authentication
- Basic CORS support
- CLI commands
