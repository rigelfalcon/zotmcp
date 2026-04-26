"""
MCP Server implementation with all Zotero tools.
"""

import base64
import json
import logging
import os
import shutil
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional
from urllib.parse import quote

import httpx
from fastmcp import Context, FastMCP

from zotmcp.clients import ZoteroClientBase, ZoteroItem, create_client
from zotmcp.config import Config, load_config

if TYPE_CHECKING:
    from zotmcp.semantic import SemanticEngine

logger = logging.getLogger(__name__)

# Global instances
_client: Optional[ZoteroClientBase] = None
_config: Optional[Config] = None
_semantic_engine: Optional["SemanticEngine"] = None


def get_client() -> ZoteroClientBase:
    """Get the Zotero client instance."""
    global _client, _config
    if _client is None:
        _config = load_config()
        _client = create_client(_config.zotero)
    return _client


def get_semantic_engine() -> Optional["SemanticEngine"]:
    """Get the semantic search engine instance (lazy initialization)."""
    global _semantic_engine, _config
    if _semantic_engine is None:
        if _config is None:
            _config = load_config()
        if _config.semantic.enabled:
            try:
                from zotmcp.semantic import SemanticEngine
                _semantic_engine = SemanticEngine(
                    model_name=_config.semantic.model_name,
                    persist_directory=_config.semantic.persist_directory,
                    collection_name=_config.semantic.collection_name,
                    batch_size=_config.semantic.batch_size,
                )
            except ImportError:
                logger.warning("Semantic search dependencies not installed.")
                return None
            except Exception as e:
                logger.warning(f"Failed to initialize semantic engine: {e}")
                return None
    return _semantic_engine

async def ensure_semantic_engine_initialized(engine: "SemanticEngine") -> None:
    """Initialize semantic search on demand if server lifespan did not run it."""
    if getattr(engine, "initialized", False) or getattr(engine, "_initialized", False):
        return
    await engine.initialize()

def format_item_markdown(item: ZoteroItem, include_abstract: bool = True) -> str:
    """Format a Zotero item as markdown."""
    lines = [
        f"## {item.title}",
        f"**Type:** {item.item_type}",
        f"**Key:** {item.key}",
        f"**Authors:** {item.format_creators()}",
    ]

    if item.date:
        lines.append(f"**Date:** {item.date}")

    if item.doi:
        lines.append(f"**DOI:** {item.doi}")

    if item.url:
        lines.append(f"**URL:** {item.url}")

    if item.tags:
        tag_str = " ".join(f"`{t}`" for t in item.tags)
        lines.append(f"**Tags:** {tag_str}")

    if include_abstract and item.abstract:
        abstract = item.abstract[:500] + "..." if len(item.abstract) > 500 else item.abstract
        lines.append(f"**Abstract:** {abstract}")

    return "\n".join(lines)


@asynccontextmanager
async def server_lifespan(server: FastMCP):
    """Manage server startup and shutdown."""
    sys.stderr.write("Starting Zotero MCP Unified server...\n")

    # Initialize client
    client = get_client()
    if await client.is_available():
        sys.stderr.write("Zotero connection established.\n")
    else:
        sys.stderr.write("Warning: Zotero not available. Some features may not work.\n")

    # Initialize semantic engine if enabled
    semantic_engine = get_semantic_engine()
    if semantic_engine:
        try:
            await ensure_semantic_engine_initialized(semantic_engine)
            sys.stderr.write("Semantic search engine initialized.\n")
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to initialize semantic engine: {e}\n")

    yield {}

    sys.stderr.write("Shutting down Zotero MCP Unified server...\n")


# Create MCP server
mcp = FastMCP("zotero-unified", lifespan=server_lifespan)

# ── Register all tools from modular files ──
from zotmcp.tools import register_all_tools
register_all_tools(mcp, get_client, format_item_markdown,
                   get_semantic_engine, ensure_semantic_engine_initialized)


def create_server() -> FastMCP:
    """Create and return the MCP server instance."""
    return mcp
