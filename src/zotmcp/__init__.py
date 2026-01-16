"""
Zotero MCP Unified - A comprehensive Zotero MCP server with remote access support.

This package provides:
- Unified interface to Zotero (Local API, Web API, SQLite)
- MCP tools for search, read, write operations
- HTTP/SSE transport for remote access
- Semantic search with vector embeddings
"""

__version__ = "0.1.0"
__author__ = "ZotMCP Contributors"

from zotmcp.server import create_server, mcp

__all__ = ["create_server", "mcp", "__version__"]
