"""Search tools for ZotMCP.

Sections: Search Tools, Semantic Search Tools, Saved Searches Tools, Find Similar Items Tool
"""
import base64
import csv
import hashlib
import json
import logging
import os
import re as _re
import shutil
import sys
import zipfile
from collections import Counter, defaultdict
from io import StringIO
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote, unquote

import httpx
from fastmcp import Context

logger = logging.getLogger(__name__)


def register(mcp, get_client, format_item_markdown=None,
             get_semantic_engine=None, ensure_semantic_engine_initialized=None):
    """Register search tools on the MCP server."""

    # =============================================================================
    # Search Tools
    # =============================================================================


    @mcp.tool(
        name="zotero_search",
        description="Search for items in your Zotero library by keywords."
    )
    async def search_items(
        query: str,
        limit: int = 10,
        item_type: Optional[str] = None,
        tags: Optional[list[str]] = None,
        *,
        ctx: Context,
    ) -> str:
        """
        Search for items in your Zotero library.

        Args:
            query: Search query string
            limit: Maximum number of results (default: 10)
            item_type: Filter by item type (e.g., "journalArticle", "book")
            tags: Filter by tags
            ctx: MCP context

        Returns:
            Markdown-formatted search results
        """
        if not query.strip():
            return "Error: Search query cannot be empty"

        ctx.info(f"Searching Zotero for: {query}")
        client = get_client()

        if not await client.is_available():
            return "Error: Zotero is not available. Please ensure Zotero is running."

        items = await client.search_items(
            query=query,
            limit=limit,
            item_type=item_type,
            tags=tags,
        )

        if not items:
            return f"No items found matching: '{query}'"

        output = [f"# Search Results for '{query}'", f"Found {len(items)} items:", ""]

        for i, item in enumerate(items, 1):
            output.append(f"### {i}. {item.title}")
            output.append(f"**Type:** {item.item_type} | **Key:** `{item.key}`")
            output.append(f"**Authors:** {item.format_creators()}")
            if item.date:
                output.append(f"**Date:** {item.date}")
            if item.tags:
                output.append(f"**Tags:** {' '.join(f'`{t}`' for t in item.tags)}")
            output.append("")

        return "\n".join(output)


    @mcp.tool(
        name="zotero_get_recent",
        description="Get recently added items from your Zotero library."
    )
    async def get_recent_items(
        limit: int = 10,
        *,
        ctx: Context,
    ) -> str:
        """
        Get recently added items.

        Args:
            limit: Number of items to return (default: 10)
            ctx: MCP context

        Returns:
            Markdown-formatted list of recent items
        """
        ctx.info(f"Fetching {limit} recent items")
        client = get_client()

        if not await client.is_available():
            return "Error: Zotero is not available."

        # Search with empty query to get all, sorted by date
        items = await client.search_items(query="", limit=limit)

        if not items:
            return "No items found in your library."

        output = [f"# {len(items)} Most Recent Items", ""]

        for i, item in enumerate(items, 1):
            output.append(f"### {i}. {item.title}")
            output.append(f"**Type:** {item.item_type} | **Key:** `{item.key}`")
            output.append(f"**Authors:** {item.format_creators()}")
            if item.date_added:
                output.append(f"**Added:** {item.date_added}")
            output.append("")

        return "\n".join(output)



    # =============================================================================
    # Semantic Search Tools
    # =============================================================================


    @mcp.tool(
        name="zotero_semantic_search",
        description="Search for semantically similar items using AI embeddings."
    )
    async def semantic_search(
        query: str,
        limit: int = 10,
        item_type: Optional[str] = None,
        *,
        ctx: Context,
    ) -> str:
        """
        Search for semantically similar items using AI-powered similarity.

        Args:
            query: Natural language search query
            limit: Maximum number of results (default 10)
            item_type: Optional filter by item type
            ctx: MCP context

        Returns:
            Markdown formatted results with similarity scores
        """
        ctx.info(f"Semantic search: {query}")

        engine = get_semantic_engine()
        if not engine:
            return "Semantic search not available. Enable in config or install dependencies (chromadb, sentence-transformers)."
        try:
            await ensure_semantic_engine_initialized(engine)
        except Exception as e:
            return f"Semantic search initialization failed: {e}"

        # Apply metadata filter if item_type specified
        filter_metadata = {"item_type": item_type} if item_type else None

        results = await engine.search(query, limit=limit, filter_metadata=filter_metadata)

        if not results:
            return f"No semantically similar items found for: {query}"

        output = [f"# Semantic Search Results: {query}", ""]
        output.append(f"Found {len(results)} similar items:\n")

        for i, result in enumerate(results, 1):
            output.append(f"## {i}. {result.title}")
            output.append(f"**Similarity:** {result.similarity:.3f}")
            output.append(f"**Key:** `{result.item_key}`")
            output.append(f"**Type:** {result.metadata.get('item_type', 'unknown')}")
            if result.metadata.get('date'):
                output.append(f"**Date:** {result.metadata['date']}")
            output.append("")

        return "\n".join(output)


    @mcp.tool(
        name="zotero_update_embeddings",
        description="Update semantic search embeddings for recent items."
    )
    async def update_embeddings(
        limit: int = 100,
        force: bool = False,
        *,
        ctx: Context,
    ) -> str:
        """
        Update semantic search embeddings for items.

        Args:
            limit: Maximum items to process (default 100)
            force: Re-embed existing items (default False)
            ctx: MCP context

        Returns:
            Status message with count of embedded items
        """
        ctx.info(f"Updating embeddings (limit={limit}, force={force})")

        engine = get_semantic_engine()
        if not engine:
            return "Semantic search not available."
        try:
            await ensure_semantic_engine_initialized(engine)
        except Exception as e:
            return f"Semantic search initialization failed: {e}"

        client = get_client()

        # Get recent items
        items = await client.search_items("", limit=limit)

        if not items:
            return "No items found to embed."

        # Convert to dict format for semantic engine
        items_dict = []
        for item in items:
            items_dict.append({
                "key": item.key,
                "title": item.title,
                "abstract": item.abstract,
                "item_type": item.item_type,
                "date": item.date,
                "creators": item.creators,
            })

        # Update embeddings
        count = await engine.update_embeddings(items_dict, force=force)

        stats = engine.get_stats()

        return f"Embedded {count} items. Total in index: {stats.get('count', 'unknown')}"



    # =============================================================================
    # Saved Searches Tools
    # =============================================================================


    @mcp.tool(
        name="zotero_list_saved_searches",
        description="List all saved searches in the Zotero library.",
    )
    async def list_saved_searches(
        *,
        ctx: Context,
    ) -> str:
        """List saved Zotero searches."""
        import httpx
        ctx.info("Listing saved searches")

        try:
            async with httpx.AsyncClient(timeout=10.0) as http:
                resp = await http.get("http://127.0.0.1:23119/api/users/0/searches")
                if resp.status_code == 200:
                    searches = resp.json()
                    if not searches:
                        return "No saved searches found."
                    lines = [f"## Saved Searches ({len(searches)})\n"]
                    for s in searches:
                        data = s.get("data", s)
                        key = data.get("key", "?")
                        name = data.get("name", "Untitled")
                        conditions = data.get("conditions", [])
                        cond_str = "; ".join(
                            f"{c.get('condition','?')} {c.get('operator','?')} {c.get('value','')}"
                            for c in conditions[:3]
                        )
                        if len(conditions) > 3:
                            cond_str += f" (+{len(conditions)-3} more)"
                        lines.append(f"- **{name}** (`{key}`): {cond_str}")
                    return "\n".join(lines)
                return f"Failed to list searches (HTTP {resp.status_code})."
        except Exception as e:
            return f"Failed to list searches: {e}"


    @mcp.tool(
        name="zotero_run_saved_search",
        description="Run a saved Zotero search by its key and return matching items.",
    )
    async def run_saved_search(
        search_key: str,
        limit: int = 25,
        *,
        ctx: Context,
    ) -> str:
        """
        Run a saved search.

        Args:
            search_key: Key of the saved search.
            limit: Max items to return.
            ctx: MCP context.
        """
        import httpx
        ctx.info(f"Running saved search {search_key}")

        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                # Get search definition
                resp = await http.get(f"http://127.0.0.1:23119/api/users/0/searches/{search_key}")
                if resp.status_code != 200:
                    return f"Saved search `{search_key}` not found."
                search_data = resp.json()
                name = search_data.get("data", {}).get("name", "Untitled")

                # Run the search via items endpoint
                resp2 = await http.get(
                    f"http://127.0.0.1:23119/api/users/0/searches/{search_key}/items",
                    params={"limit": str(limit)}
                )
                if resp2.status_code != 200:
                    return f"Failed to run search (HTTP {resp2.status_code})."

                items = resp2.json()
                if not items:
                    return f"Search '{name}' returned no results."

                lines = [f"## Search '{name}' ({len(items)} items)\n"]
                for it in items:
                    data = it.get("data", it)
                    key = data.get("key", "?")
                    title = data.get("title", "Untitled")[:80]
                    itype = data.get("itemType", "?")
                    lines.append(f"- `{key}` ({itype}) {title}")
                return "\n".join(lines)
        except Exception as e:
            return f"Failed to run search: {e}"



    # =============================================================================
    # Find Similar Items Tool
    # =============================================================================


    @mcp.tool(
        name="zotero_find_similar",
        description="Find items similar to a given Zotero item using semantic search on its title and abstract.",
    )
    async def find_similar_items(
        item_key: str,
        limit: int = 10,
        *,
        ctx: Context,
    ) -> str:
        """
        Find semantically similar items to a given item.

        Args:
            item_key: Zotero item key to find similar items for.
            limit: Max items to return.
            ctx: MCP context.
        """
        ctx.info(f"Finding items similar to {item_key}")
        client = get_client()

        if not await client.is_available():
            return "Error: Zotero is not available."

        item = await client.get_item(item_key)
        if not item:
            return f"Item `{item_key}` not found."

        raw = (item.raw_data or {}).get("data", {})
        abstract = raw.get("abstractNote", "")
        query = f"{item.title}"
        if abstract:
            query += f" {abstract[:200]}"

        # Use semantic search if available
        try:
            from zotmcp.semantic import SemanticSearch
            from zotmcp.config import load_config
            config = load_config()
            if hasattr(config, 'semantic_search') and config.semantic_search:
                ss = SemanticSearch(config.semantic_search)
                if await ss.is_available():
                    results = await ss.search(query, limit=limit + 1)
                    # Filter out the source item itself
                    results = [r for r in results if r.get("key") != item_key][:limit]
                    if not results:
                        return f"No similar items found for `{item_key}`."
                    lines = [f"## Items similar to: {item.title[:60]}\n"]
                    for r in results:
                        score = r.get("score", 0)
                        title = r.get("title", "Untitled")[:80]
                        key = r.get("key", "?")
                        lines.append(f"- `{key}` (score: {score:.3f}) {title}")
                    return "\n".join(lines)
        except Exception:
            pass

        # Fallback: keyword search using title words
        words = [w for w in item.title.split() if len(w) > 4][:5]
        search_query = " ".join(words)
        results = await client.search_items(search_query, limit=limit + 1)
        results = [r for r in results if r.key != item_key][:limit]

        if not results:
            return f"No similar items found for `{item_key}`."

        lines = [f"## Items similar to: {item.title[:60]}\n"]
        for r in results:
            lines.append(f"- `{r.key}` ({r.item_type}) {r.title[:80]}")
        return "\n".join(lines)



