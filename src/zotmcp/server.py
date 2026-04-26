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
# Read Tools
# =============================================================================


@mcp.tool(
    name="zotero_get_item",
    description="Get detailed metadata for a specific Zotero item by its key."
)
async def get_item_metadata(
    item_key: str,
    format: Literal["markdown", "json", "bibtex"] = "markdown",
    *,
    ctx: Context,
) -> str:
    """
    Get detailed metadata for a Zotero item.

    Args:
        item_key: Zotero item key
        format: Output format (markdown, json, or bibtex)
        ctx: MCP context

    Returns:
        Formatted item metadata
    """
    ctx.info(f"Fetching item: {item_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    item = await client.get_item(item_key)
    if not item:
        return f"No item found with key: {item_key}"

    if format == "json":
        return json.dumps(item.raw_data or {}, indent=2)
    elif format == "bibtex":
        # Generate BibTeX
        bibtex_type = {
            "journalArticle": "article",
            "book": "book",
            "bookSection": "incollection",
            "conferencePaper": "inproceedings",
            "thesis": "phdthesis",
            "report": "techreport",
        }.get(item.item_type, "misc")

        def _bib_escape(s):
            """Escape special BibTeX characters."""
            if not s:
                return s
            return s.replace('\\', '\\textbackslash{}').replace('{', '\\{').replace('}', '\\}').replace('&', '\\&').replace('%', '\\%').replace('#', '\\#')

        lines = [f"@{bibtex_type}{{{item.key},"]
        lines.append(f"  title = {{{_bib_escape(item.title)}}},")
        lines.append(f"  author = {{{item.format_creators()}}},")
        if item.date:
            lines.append(f"  year = {{{item.date[:4] if len(item.date) >= 4 else item.date}}},")
        if item.doi:
            lines.append(f"  doi = {{{item.doi}}},")
        if item.url:
            lines.append(f"  url = {{{item.url}}},")
        lines.append("}")
        return "\n".join(lines)
    else:
        return format_item_markdown(item, include_abstract=True)


@mcp.tool(
    name="zotero_get_fulltext",
    description="Get the full text content of a Zotero item (PDF content)."
)
async def get_item_fulltext(
    item_key: str,
    *,
    ctx: Context,
) -> str:
    """
    Get full text content of a Zotero item.

    Args:
        item_key: Zotero item key
        ctx: MCP context

    Returns:
        Full text content or error message
    """
    ctx.info(f"Fetching full text for: {item_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    # Get item metadata first
    item = await client.get_item(item_key)
    if not item:
        return f"No item found with key: {item_key}"

    # Get full text
    fulltext = await client.get_item_fulltext(item_key)

    output = [format_item_markdown(item, include_abstract=False), "", "---", ""]

    if fulltext:
        output.append("## Full Text")
        output.append("")
        output.append(fulltext)
    else:
        output.append("*No full text available for this item.*")

    return "\n".join(output)


# =============================================================================
# Collection Tools
# =============================================================================


@mcp.tool(
    name="zotero_get_collections",
    description="List all collections in your Zotero library."
)
async def get_collections(
    *,
    ctx: Context,
) -> str:
    """
    Get all collections in your Zotero library.

    Args:
        ctx: MCP context

    Returns:
        Markdown-formatted list of collections
    """
    ctx.info("Fetching collections")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    collections = await client.get_collections()

    if not collections:
        return "No collections found in your library."

    # Build hierarchy
    by_parent = {}
    for coll in collections:
        parent = coll.parent_key or "root"
        if parent not in by_parent:
            by_parent[parent] = []
        by_parent[parent].append(coll)

    def format_tree(parent_key: str, level: int = 0) -> list[str]:
        lines = []
        for coll in by_parent.get(parent_key, []):
            indent = "  " * level
            lines.append(f"{indent}- **{coll.name}** (`{coll.key}`)")
            lines.extend(format_tree(coll.key, level + 1))
        return lines

    output = ["# Zotero Collections", ""]
    output.extend(format_tree("root"))

    return "\n".join(output)


@mcp.tool(
    name="zotero_get_collection_items",
    description="Get all items in a specific Zotero collection."
)
async def get_collection_items(
    collection_key: str,
    limit: int = 50,
    *,
    ctx: Context,
) -> str:
    """
    Get items in a collection.

    Args:
        collection_key: Collection key
        limit: Maximum number of items
        ctx: MCP context

    Returns:
        Markdown-formatted list of items
    """
    ctx.info(f"Fetching items in collection: {collection_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    items = await client.get_collection_items(collection_key, limit=limit)

    if not items:
        return f"No items found in collection: {collection_key}"

    output = [f"# Items in Collection", f"Found {len(items)} items:", ""]

    for i, item in enumerate(items, 1):
        output.append(f"### {i}. {item.title}")
        output.append(f"**Key:** `{item.key}` | **Type:** {item.item_type}")
        output.append(f"**Authors:** {item.format_creators()}")
        output.append("")

    return "\n".join(output)


# =============================================================================
# Tag Tools
# =============================================================================


@mcp.tool(
    name="zotero_get_tags",
    description="Get all tags used in your Zotero library."
)
async def get_tags(
    *,
    ctx: Context,
) -> str:
    """
    Get all tags in your library.

    Args:
        ctx: MCP context

    Returns:
        List of tags
    """
    ctx.info("Fetching tags")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    tags = await client.get_tags()

    if not tags:
        return "No tags found in your library."

    output = ["# Zotero Tags", f"Total: {len(tags)} tags", ""]

    # Group by first letter
    current_letter = None
    for tag in tags:
        first = tag[0].upper() if tag else "#"
        if first != current_letter:
            current_letter = first
            output.append(f"## {current_letter}")
        output.append(f"- `{tag}`")

    return "\n".join(output)


@mcp.tool(
    name="zotero_update_tags",
    description="Add or remove tags from a Zotero item."
)
async def update_item_tags(
    item_key: str,
    add_tags: Optional[list[str]] = None,
    remove_tags: Optional[list[str]] = None,
    *,
    ctx: Context,
) -> str:
    """
    Update tags on an item.

    Args:
        item_key: Zotero item key
        add_tags: Tags to add
        remove_tags: Tags to remove
        ctx: MCP context

    Returns:
        Success or error message
    """
    if not add_tags and not remove_tags:
        return "Error: Specify tags to add or remove"

    ctx.info(f"Updating tags for: {item_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    success = await client.update_item_tags(
        key=item_key,
        add_tags=add_tags,
        remove_tags=remove_tags,
    )

    if success:
        msg = ["# Tags Updated Successfully", f"**Item:** `{item_key}`", ""]
        if add_tags:
            msg.append(f"**Added:** {', '.join(f'`{t}`' for t in add_tags)}")
        if remove_tags:
            msg.append(f"**Removed:** {', '.join(f'`{t}`' for t in remove_tags)}")
        return "\n".join(msg)
    else:
        return f"Failed to update tags for item: {item_key}"


@mcp.tool(
    name="zotero_batch_tags",
    description="Batch update tags across multiple items matching a search query."
)
async def batch_update_tags(
    query: str,
    add_tags: Optional[list[str]] = None,
    remove_tags: Optional[list[str]] = None,
    limit: int = 50,
    *,
    ctx: Context,
) -> str:
    """
    Batch update tags on items matching a query.

    Args:
        query: Search query to find items
        add_tags: Tags to add
        remove_tags: Tags to remove
        limit: Maximum items to process
        ctx: MCP context

    Returns:
        Summary of updates
    """
    if not add_tags and not remove_tags:
        return "Error: Specify tags to add or remove"

    ctx.info(f"Batch updating tags for query: {query}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    # Find items
    items = await client.search_items(query=query, limit=limit)

    if not items:
        return f"No items found matching: '{query}'"

    # Update each item
    success_count = 0
    failed_count = 0

    for item in items:
        if item.item_type == "attachment":
            continue

        result = await client.update_item_tags(
            key=item.key,
            add_tags=add_tags,
            remove_tags=remove_tags,
        )

        if result:
            success_count += 1
        else:
            failed_count += 1

    output = [
        "# Batch Tag Update Results",
        f"**Query:** '{query}'",
        f"**Items found:** {len(items)}",
        f"**Updated:** {success_count}",
        f"**Failed:** {failed_count}",
    ]

    if add_tags:
        output.append(f"**Tags added:** {', '.join(f'`{t}`' for t in add_tags)}")
    if remove_tags:
        output.append(f"**Tags removed:** {', '.join(f'`{t}`' for t in remove_tags)}")

    return "\n".join(output)


# =============================================================================
# Organization Tools
# =============================================================================


@mcp.tool(
    name="zotero_move_to_collection",
    description="Move an item to a collection."
)
async def move_to_collection(
    item_key: str,
    collection_key: str,
    *,
    ctx: Context,
) -> str:
    """
    Move an item to a collection.

    Args:
        item_key: Item key to move
        collection_key: Target collection key
        ctx: MCP context

    Returns:
        Success or error message
    """
    ctx.info(f"Moving {item_key} to collection {collection_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    success = await client.move_item_to_collection(item_key, collection_key)

    if success:
        return f"Successfully moved item `{item_key}` to collection `{collection_key}`"
    else:
        return f"Failed to move item to collection"


# =============================================================================
# Note Tools
# =============================================================================


@mcp.tool(
    name="zotero_create_note",
    description="Create a note attached to a Zotero item."
)
async def create_note(
    item_key: str,
    content: str,
    tags: Optional[list[str]] = None,
    *,
    ctx: Context,
) -> str:
    """
    Create a note for an item.

    Args:
        item_key: Parent item key
        content: Note content (can include HTML)
        tags: Tags for the note
        ctx: MCP context

    Returns:
        Success message with note key
    """
    ctx.info(f"Creating note for: {item_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    # Convert plain text to HTML if needed
    if "<p>" not in content and "<div>" not in content:
        paragraphs = content.split("\n\n")
        content = "".join(f"<p>{p.replace(chr(10), '<br/>')}</p>" for p in paragraphs)

    note_key = await client.create_note(
        parent_key=item_key,
        content=content,
        tags=tags,
    )

    if note_key:
        return f"Successfully created note `{note_key}` for item `{item_key}`"
    else:
        return "Failed to create note"


# =============================================================================
# Status Tools
# =============================================================================


@mcp.tool(
    name="zotero_status",
    description="Check Zotero connection status."
)
async def check_status(
    *,
    ctx: Context,
) -> str:
    """
    Check Zotero connection status.

    Args:
        ctx: MCP context

    Returns:
        Status information
    """
    ctx.info("Checking Zotero status")
    client = get_client()
    config = _config

    available = await client.is_available()

    output = ["# Zotero MCP Status", ""]

    if available:
        output.append("**Status:** Connected")
    else:
        output.append("**Status:** Not Available")

    output.append(f"**Mode:** {config.zotero.mode if config else 'unknown'}")

    if config and config.zotero.mode == "local":
        output.append(f"**Port:** {config.zotero.local_port}")
    elif config and config.zotero.mode == "web":
        output.append(f"**Library:** {config.zotero.library_type}/{config.zotero.library_id}")

    # Get some stats if available
    if available:
        collections = await client.get_collections()
        tags = await client.get_tags()
        output.append(f"**Collections:** {len(collections)}")
        output.append(f"**Tags:** {len(tags)}")

    # Semantic search status
    output.append("")
    output.append("## Semantic Search")
    engine = get_semantic_engine()
    if engine:
        stats = engine.get_stats()
        output.append("**Status:** Enabled")
        output.append(f"**Model:** {config.semantic.model_name if config else 'unknown'}")
        output.append(f"**Indexed Items:** {stats.get('count', 0)}")
    else:
        output.append("**Status:** Disabled or unavailable")

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
# Everything Integration Tools (Local File Search)
# =============================================================================

EVERYTHING_API_URL = "http://localhost:9090"


async def _everything_search(
    query: str,
    count: int = 10,
    ext: Optional[str] = None,
) -> list[dict]:
    """
    Search files using Everything HTTP API.

    Args:
        query: Search query
        count: Max results
        ext: File extension filter (e.g., "pdf")

    Returns:
        List of file results with name and path
    """
    search_query = query
    if ext:
        search_query = f"{query} ext:{ext}"

    params = {
        "search": search_query,
        "count": count,
        "json": 1,
        "path_column": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{EVERYTHING_API_URL}/?search={quote(search_query)}&count={count}&json=1&path_column=1"
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])
    except httpx.ConnectError:
        return []
    except Exception as e:
        logger.warning(f"Everything search failed: {e}")
        return []


@mcp.tool(
    name="zotero_find_pdf",
    description="Find PDF files on local disk using Everything search. Searches by author names, title keywords, or year. Requires Everything HTTP server enabled on localhost:9090."
)
async def find_pdf_files(
    query: str,
    limit: int = 10,
    *,
    ctx: Context,
) -> str:
    """
    Find PDF files using Everything local search.

    Args:
        query: Search query (author names, title keywords, year)
        limit: Maximum results (default: 10)
        ctx: MCP context

    Returns:
        List of matching PDF files with full paths
    """
    ctx.info(f"Searching local files for: {query}")

    results = await _everything_search(query, count=limit, ext="pdf")

    if not results:
        return f"No PDF files found matching: '{query}'\n\nNote: Ensure Everything HTTP server is enabled (Tools > Options > HTTP Server > Enable, Port 9090)"

    output = [f"# PDF Files Found for '{query}'", f"Found {len(results)} files:", ""]

    for i, result in enumerate(results, 1):
        name = result.get("name", "Unknown")
        path = result.get("path", "Unknown")
        full_path = f"{path}\\{name}" if path else name
        output.append(f"### {i}. {name}")
        output.append(f"**Path:** `{full_path}`")
        output.append("")

    return "\n".join(output)


@mcp.tool(
    name="zotero_copy_pdf",
    description="Search for PDF files using Everything and copy them to a target directory. Useful for collecting reference papers. Requires Everything HTTP server enabled on localhost:9090."
)
async def copy_pdf_files(
    query: str,
    target_dir: str,
    new_filename: Optional[str] = None,
    limit: int = 1,
    *,
    ctx: Context,
) -> str:
    """
    Search and copy PDF files to a target directory.

    Args:
        query: Search query (author names, title keywords)
        target_dir: Target directory path
        new_filename: Optional new filename (without .pdf extension)
        limit: Number of files to copy (default: 1, copies first match)
        ctx: MCP context

    Returns:
        Success/failure message with copied file paths
    """
    ctx.info(f"Searching and copying PDFs for: {query}")

    # Validate target directory
    target_path = Path(target_dir)
    if not target_path.exists():
        try:
            target_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return f"Error: Cannot create target directory: {e}"

    # Search for files
    results = await _everything_search(query, count=limit, ext="pdf")

    if not results:
        return f"No PDF files found matching: '{query}'"

    copied = []
    failed = []

    for i, result in enumerate(results):
        name = result.get("name", "")
        path = result.get("path", "")

        if not name or not path:
            continue

        source_path = Path(path) / name

        # Determine target filename
        if new_filename and len(results) == 1:
            target_name = f"{new_filename}.pdf" if not new_filename.endswith(".pdf") else new_filename
        else:
            target_name = name

        dest_path = target_path / target_name

        try:
            shutil.copy2(str(source_path), str(dest_path))
            copied.append({
                "source": str(source_path),
                "dest": str(dest_path),
            })
        except Exception as e:
            failed.append({
                "source": str(source_path),
                "error": str(e),
            })

    # Build output
    output = [f"# PDF Copy Results for '{query}'", ""]

    if copied:
        output.append(f"## Successfully Copied ({len(copied)} files)")
        for item in copied:
            output.append(f"- `{item['dest']}`")
            output.append(f"  (from: `{item['source']}`)")
        output.append("")

    if failed:
        output.append(f"## Failed ({len(failed)} files)")
        for item in failed:
            output.append(f"- `{item['source']}`")
            output.append(f"  Error: {item['error']}")

    return "\n".join(output)


@mcp.tool(
    name="zotero_batch_copy_pdfs",
    description="Batch search and copy multiple PDFs to a target directory based on a list of search queries. Each query finds and copies one PDF."
)
async def batch_copy_pdfs(
    queries: list[str],
    target_dir: str,
    filenames: Optional[list[str]] = None,
    *,
    ctx: Context,
) -> str:
    """
    Batch copy multiple PDFs.

    Args:
        queries: List of search queries (one per PDF)
        target_dir: Target directory path
        filenames: Optional list of new filenames (parallel to queries)
        ctx: MCP context

    Returns:
        Summary of copied files
    """
    ctx.info(f"Batch copying {len(queries)} PDFs")

    # Validate target directory
    target_path = Path(target_dir)
    if not target_path.exists():
        try:
            target_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return f"Error: Cannot create target directory: {e}"

    results_summary = []
    success_count = 0
    fail_count = 0

    for i, query in enumerate(queries):
        new_filename = filenames[i] if filenames and i < len(filenames) else None

        # Search for file
        results = await _everything_search(query, count=1, ext="pdf")

        if not results:
            results_summary.append(f"- **{query}**: Not found")
            fail_count += 1
            continue

        result = results[0]
        name = result.get("name", "")
        path = result.get("path", "")

        if not name or not path:
            results_summary.append(f"- **{query}**: Invalid result")
            fail_count += 1
            continue

        source_path = Path(path) / name

        # Determine target filename
        if new_filename:
            target_name = f"{new_filename}.pdf" if not new_filename.endswith(".pdf") else new_filename
        else:
            target_name = name

        dest_path = target_path / target_name

        try:
            shutil.copy2(str(source_path), str(dest_path))
            results_summary.append(f"- **{query}**: Copied to `{target_name}`")
            success_count += 1
        except Exception as e:
            results_summary.append(f"- **{query}**: Failed - {e}")
            fail_count += 1

    output = [
        "# Batch PDF Copy Results",
        f"**Target:** `{target_dir}`",
        f"**Success:** {success_count} | **Failed:** {fail_count}",
        "",
        "## Details",
    ]
    output.extend(results_summary)

    return "\n".join(output)


# =============================================================================
# Remote File Transfer Tools (for LAN clients)
# =============================================================================


@mcp.tool(
    name="zotero_get_pdf_base64",
    description="Search for a PDF using Everything and return its content as base64-encoded string. Enables remote clients to download PDFs from the server. Max file size: 50MB."
)
async def get_pdf_base64(
    query: str,
    *,
    ctx: Context,
) -> str:
    """
    Search for a PDF and return its content as base64.

    Args:
        query: Search query (author names, title keywords)
        ctx: MCP context

    Returns:
        JSON with filename and base64 content, or error message
    """
    ctx.info(f"Fetching PDF as base64 for: {query}")

    # Search for file
    results = await _everything_search(query, count=1, ext="pdf")

    if not results:
        return json.dumps({"error": f"No PDF found matching: '{query}'"})

    result = results[0]
    name = result.get("name", "")
    path = result.get("path", "")

    if not name or not path:
        return json.dumps({"error": "Invalid search result"})

    source_path = Path(path) / name

    if not source_path.exists():
        return json.dumps({"error": f"File not found: {source_path}"})

    # Check file size (max 50MB)
    file_size = source_path.stat().st_size
    max_size = 50 * 1024 * 1024  # 50MB

    if file_size > max_size:
        return json.dumps({
            "error": f"File too large: {file_size / 1024 / 1024:.1f}MB (max 50MB)",
            "filename": name,
            "path": str(source_path),
        })

    try:
        with open(source_path, "rb") as f:
            content = base64.b64encode(f.read()).decode("utf-8")

        return json.dumps({
            "success": True,
            "filename": name,
            "path": str(source_path),
            "size_bytes": file_size,
            "content_base64": content,
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to read file: {e}"})


@mcp.tool(
    name="zotero_batch_get_pdfs_base64",
    description="Batch search and return multiple PDFs as base64. Each query returns one PDF. For remote clients to download multiple files."
)
async def batch_get_pdfs_base64(
    queries: list[str],
    *,
    ctx: Context,
) -> str:
    """
    Batch get multiple PDFs as base64.

    Args:
        queries: List of search queries
        ctx: MCP context

    Returns:
        JSON array with results for each query
    """
    ctx.info(f"Batch fetching {len(queries)} PDFs as base64")

    results = []
    max_size = 50 * 1024 * 1024  # 50MB per file

    for query in queries:
        search_results = await _everything_search(query, count=1, ext="pdf")

        if not search_results:
            results.append({
                "query": query,
                "error": "Not found",
            })
            continue

        result = search_results[0]
        name = result.get("name", "")
        path = result.get("path", "")

        if not name or not path:
            results.append({
                "query": query,
                "error": "Invalid result",
            })
            continue

        source_path = Path(path) / name

        if not source_path.exists():
            results.append({
                "query": query,
                "error": "File not found",
            })
            continue

        file_size = source_path.stat().st_size

        if file_size > max_size:
            results.append({
                "query": query,
                "filename": name,
                "error": f"Too large: {file_size / 1024 / 1024:.1f}MB",
            })
            continue

        try:
            with open(source_path, "rb") as f:
                content = base64.b64encode(f.read()).decode("utf-8")

            results.append({
                "query": query,
                "success": True,
                "filename": name,
                "size_bytes": file_size,
                "content_base64": content,
            })
        except Exception as e:
            results.append({
                "query": query,
                "error": str(e),
            })

    return json.dumps(results)


@mcp.tool(
    name="zotero_list_pdfs",
    description="Search for PDFs and list their metadata without downloading content. Use this first to find files, then use zotero_get_pdf_base64 to download specific ones."
)
async def list_pdfs(
    query: str,
    limit: int = 10,
    *,
    ctx: Context,
) -> str:
    """
    List PDFs matching a query with metadata.

    Args:
        query: Search query
        limit: Max results
        ctx: MCP context

    Returns:
        JSON list of files with metadata
    """
    ctx.info(f"Listing PDFs for: {query}")

    results = await _everything_search(query, count=limit, ext="pdf")

    if not results:
        return json.dumps({"error": f"No PDFs found matching: '{query}'"})

    files = []
    for result in results:
        name = result.get("name", "")
        path = result.get("path", "")

        if not name or not path:
            continue

        source_path = Path(path) / name
        size_bytes = 0

        try:
            if source_path.exists():
                size_bytes = source_path.stat().st_size
        except:
            pass

        files.append({
            "filename": name,
            "path": str(source_path),
            "size_bytes": size_bytes,
            "size_mb": round(size_bytes / 1024 / 1024, 2),
        })

    return json.dumps({
        "query": query,
        "count": len(files),
        "files": files,
    })


# =============================================================================
# Collection Management Tools
# =============================================================================


@mcp.tool(
    name="zotero_get_item_children",
    description="Get children (attachments, notes) of a Zotero item."
)
async def get_item_children(
    item_key: str,
    *,
    ctx: Context,
) -> str:
    """
    Get children (attachments, notes) of an item.

    Args:
        item_key: Item key to get children for
        ctx: MCP context

    Returns:
        JSON list of children items
    """
    ctx.info(f"Getting children for item {item_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    children = await client.get_item_children(item_key)

    if not children:
        return f"No children found for item `{item_key}`"

    output = []
    for child in children:
        output.append({
            "key": child.key,
            "type": child.item_type,
            "title": child.title,
        })

    return json.dumps(output, indent=2)


@mcp.tool(
    name="zotero_create_collection",
    description="Create a new collection in Zotero. Note: Requires Web API mode (local API does not support this)."
)
async def create_collection(
    name: str,
    parent_key: Optional[str] = None,
    *,
    ctx: Context,
) -> str:
    """
    Create a new collection.

    Args:
        name: Collection name
        parent_key: Optional parent collection key (for subcollections)
        ctx: MCP context

    Returns:
        Success message with collection key, or error message
    """
    ctx.info(f"Creating collection: {name}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    collection_key = await client.create_collection(name, parent_key)

    if collection_key:
        return f"Successfully created collection `{name}` with key `{collection_key}`"
    else:
        return (
            "Error: Failed to create collection. "
            "Note: Zotero Local API does not support creating collections. "
            "Please use Web API mode or create collections manually in Zotero."
        )


@mcp.tool(
    name="zotero_delete_collection",
    description="Delete a collection from Zotero. Note: Requires Web API mode."
)
async def delete_collection(
    collection_key: str,
    *,
    ctx: Context,
) -> str:
    """
    Delete a collection.

    Args:
        collection_key: Collection key to delete
        ctx: MCP context

    Returns:
        Success or error message
    """
    ctx.info(f"Deleting collection {collection_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    success = await client.delete_collection(collection_key)

    if success:
        return f"Successfully deleted collection `{collection_key}`"
    else:
        return (
            "Error: Failed to delete collection. "
            "Note: Zotero Local API does not support deleting collections. "
            "Please use Web API mode or delete collections manually in Zotero."
        )


@mcp.tool(
    name="zotero_rename_collection",
    description="Rename a collection in Zotero. Note: Requires Web API mode."
)
async def rename_collection(
    collection_key: str,
    new_name: str,
    *,
    ctx: Context,
) -> str:
    """
    Rename a collection.

    Args:
        collection_key: Collection key to rename
        new_name: New collection name
        ctx: MCP context

    Returns:
        Success or error message
    """
    ctx.info(f"Renaming collection {collection_key} to {new_name}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    success = await client.rename_collection(collection_key, new_name)

    if success:
        return f"Successfully renamed collection `{collection_key}` to `{new_name}`"
    else:
        return (
            "Error: Failed to rename collection. "
            "Note: Zotero Local API does not support renaming collections. "
            "Please use Web API mode or rename collections manually in Zotero."
        )


@mcp.tool(
    name="zotero_batch_move_to_collection",
    description="Move multiple items to a collection at once."
)
async def batch_move_to_collection(
    item_keys: list[str],
    collection_key: str,
    *,
    ctx: Context,
) -> str:
    """
    Move multiple items to a collection.

    Args:
        item_keys: List of item keys to move
        collection_key: Target collection key
        ctx: MCP context

    Returns:
        JSON summary of results
    """
    ctx.info(f"Batch moving {len(item_keys)} items to collection {collection_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    results = await client.batch_move_to_collection(item_keys, collection_key)

    success_count = sum(1 for v in results.values() if v)
    fail_count = len(results) - success_count

    return json.dumps({
        "total": len(item_keys),
        "success": success_count,
        "failed": fail_count,
        "results": results,
    }, indent=2)


# =============================================================================
# Notes Tools (Group 1)
# =============================================================================


@mcp.tool(
    name="zotero_get_notes",
    description="Get notes from Zotero library, optionally filtered by parent item.",
)
async def get_notes(
    item_key: Optional[str] = None,
    limit: int = 50,
    truncate: int = 500,
    raw_html: bool = False,
    *,
    ctx: Context,
) -> str:
    """
    Get notes from Zotero.

    Args:
        item_key: Parent item key to filter notes. If None, returns recent notes.
        limit: Maximum number of notes to return.
        truncate: Truncate note content to this many characters (0 = no truncation).
        raw_html: If True, return raw HTML; otherwise strip to plain text.
        ctx: MCP context.
    """
    ctx.info(f"Getting notes (item_key={item_key}, limit={limit})")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    from zotmcp.utils import strip_html

    notes = await client.get_notes(item_key=item_key, limit=limit)
    if not notes:
        return "No notes found."

    results = []
    for note in notes:
        raw_data = note.raw_data or {}
        content = raw_data.get("data", {}).get("note", "")
        if not raw_html:
            content = strip_html(content)
        if truncate > 0 and len(content) > truncate:
            content = content[:truncate] + "..."

        parent_key = raw_data.get("data", {}).get("parentItem", "")
        results.append({
            "key": note.key,
            "parentItem": parent_key,
            "tags": note.tags,
            "dateModified": note.date_modified,
            "content": content,
        })

    return json.dumps(results, indent=2, ensure_ascii=False)


@mcp.tool(
    name="zotero_search_notes",
    description="Search note content by keyword.",
)
async def search_notes(
    query: str,
    limit: int = 20,
    raw_html: bool = False,
    *,
    ctx: Context,
) -> str:
    """
    Search notes by keyword in content.

    Args:
        query: Search query.
        limit: Maximum results.
        raw_html: Return raw HTML if True.
        ctx: MCP context.
    """
    ctx.info(f"Searching notes for: {query}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    from zotmcp.utils import strip_html

    # Get a batch of notes and filter client-side
    notes = await client.get_notes(limit=200)
    query_lower = query.lower()
    matches = []

    for note in notes:
        raw_data = note.raw_data or {}
        content = raw_data.get("data", {}).get("note", "")
        plain = strip_html(content)
        if query_lower in plain.lower():
            if not raw_html:
                content = plain
            if len(content) > 500:
                content = content[:500] + "..."
            matches.append({
                "key": note.key,
                "parentItem": raw_data.get("data", {}).get("parentItem", ""),
                "tags": note.tags,
                "content": content,
            })
            if len(matches) >= limit:
                break

    if not matches:
        return f"No notes found matching '{query}'."
    return json.dumps(matches, indent=2, ensure_ascii=False)


@mcp.tool(
    name="zotero_update_note",
    description="Update an existing note's content (append or replace).",
)
async def update_note(
    item_key: str,
    note_text: str,
    append: bool = True,
    *,
    ctx: Context,
) -> str:
    """
    Update note content.

    Args:
        item_key: Note item key.
        note_text: New text content.
        append: If True, append to existing content; if False, replace.
        ctx: MCP context.
    """
    ctx.info(f"Updating note {item_key} (append={append})")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    success = await client.update_note(item_key, note_text, append=append)
    if success:
        action = "appended to" if append else "replaced"
        return f"Successfully {action} note `{item_key}`."
    return f"Failed to update note `{item_key}`. Check that the key is a note item."


@mcp.tool(
    name="zotero_delete_note",
    description="Move a note to the Zotero trash.",
)
async def delete_note(
    item_key: str,
    *,
    ctx: Context,
) -> str:
    """
    Delete (trash) a note.

    Args:
        item_key: Note item key.
        ctx: MCP context.
    """
    ctx.info(f"Trashing note: {item_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    success = await client.trash_item(item_key)
    if success:
        return f"Note `{item_key}` moved to trash."
    return f"Failed to trash note `{item_key}`."




@mcp.tool(
    name="zotero_trash_item",
    description="Move any Zotero item (not just notes) to trash by its key.",
)
async def trash_item(
    item_key: str,
    *,
    ctx: Context,
) -> str:
    """
    Move an item to the Zotero trash.

    Args:
        item_key: The key of the item to trash.
        ctx: MCP context.
    """
    ctx.info(f"Trashing item: {item_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    success = await client.trash_item(item_key)
    if success:
        return f"Item `{item_key}` moved to trash."
    return f"Failed to trash item `{item_key}`."

# =============================================================================
# Annotations Tools (Group 2)
# =============================================================================


@mcp.tool(
    name="zotero_get_annotations",
    description="Get PDF annotations (highlights, notes) for an item or all items.",
)
async def get_annotations(
    item_key: Optional[str] = None,
    limit: int = 50,
    *,
    ctx: Context,
) -> str:
    """
    Get annotations from PDF attachments.

    Args:
        item_key: Parent item or attachment key. If None, returns recent annotations.
        limit: Maximum number of annotations.
        ctx: MCP context.
    """
    ctx.info(f"Getting annotations (item_key={item_key})")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    if item_key:
        children = await client.get_item_children(item_key)
    else:
        children_raw = await client.get_all_items(limit=limit, item_type="annotation")
        children = children_raw

    annotations = []
    for child in children:
        raw = child.raw_data or {}
        data = raw.get("data", {})
        if data.get("itemType") == "annotation" or child.item_type == "annotation":
            annotations.append({
                "key": child.key,
                "type": data.get("annotationType", "unknown"),
                "text": data.get("annotationText", ""),
                "comment": data.get("annotationComment", ""),
                "color": data.get("annotationColor", ""),
                "pageLabel": data.get("annotationPageLabel", ""),
                "parentItem": data.get("parentItem", ""),
                "tags": child.tags,
            })
            if len(annotations) >= limit:
                break

    if not annotations:
        return "No annotations found."
    return json.dumps(annotations, indent=2, ensure_ascii=False)


@mcp.tool(
    name="zotero_create_annotation",
    description="Create a text highlight annotation on a PDF attachment.",
)
async def create_annotation(
    attachment_key: str,
    page: int,
    text: str,
    comment: Optional[str] = None,
    color: str = "#ffd400",
    *,
    ctx: Context,
) -> str:
    """
    Create a highlight annotation.

    Args:
        attachment_key: PDF attachment key.
        page: 0-based page number.
        text: Text to highlight.
        comment: Optional comment on the highlight.
        color: Highlight color (hex). Default yellow.
        ctx: MCP context.
    """
    ctx.info(f"Creating highlight annotation on {attachment_key} page {page}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    # Download PDF to find text position
    pdf_bytes = await client.download_attachment(attachment_key)
    if not pdf_bytes:
        return f"Failed to download PDF for attachment `{attachment_key}`."

    try:
        from zotmcp.pdf_utils import find_text_position
    except ImportError:
        return "Error: PyMuPDF not installed. Install with: pip install 'zotmcp[pdf]'"

    position = find_text_position(pdf_bytes, page, text)
    if not position:
        return f"Text not found on page {page} of attachment `{attachment_key}`."

    annotation_data = {
        "itemType": "annotation",
        "parentItem": attachment_key,
        "annotationType": "highlight",
        "annotationText": text,
        "annotationComment": comment or "",
        "annotationColor": color,
        "annotationPageLabel": str(page + 1),
        "annotationPosition": json.dumps(position),
        "tags": [],
    }

    key = await client.create_item_raw(annotation_data)
    if key:
        return f"Created highlight annotation `{key}` on page {page + 1}."
    return "Failed to create annotation."


@mcp.tool(
    name="zotero_create_area_annotation",
    description="Create an area (image/region) annotation on a PDF attachment.",
)
async def create_area_annotation(
    attachment_key: str,
    page: int,
    x: float,
    y: float,
    w: float,
    h: float,
    comment: Optional[str] = None,
    color: str = "#ffd400",
    *,
    ctx: Context,
) -> str:
    """
    Create an area annotation on a PDF page.

    Args:
        attachment_key: PDF attachment key.
        page: 0-based page number.
        x: Left edge in PDF points from top-left.
        y: Top edge in PDF points from top-left.
        w: Width in PDF points.
        h: Height in PDF points.
        comment: Optional comment.
        color: Annotation color (hex).
        ctx: MCP context.
    """
    ctx.info(f"Creating area annotation on {attachment_key} page {page}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    pdf_bytes = await client.download_attachment(attachment_key)
    if not pdf_bytes:
        return f"Failed to download PDF for attachment `{attachment_key}`."

    try:
        from zotmcp.pdf_utils import build_area_position
    except ImportError:
        return "Error: PyMuPDF not installed. Install with: pip install 'zotmcp[pdf]'"

    position = build_area_position(page, x, y, w, h, pdf_bytes)
    if not position:
        return f"Invalid page {page} or coordinates for attachment `{attachment_key}`."

    annotation_data = {
        "itemType": "annotation",
        "parentItem": attachment_key,
        "annotationType": "image",
        "annotationComment": comment or "",
        "annotationColor": color,
        "annotationPageLabel": str(page + 1),
        "annotationPosition": json.dumps(position),
        "tags": [],
    }

    key = await client.create_item_raw(annotation_data)
    if key:
        return f"Created area annotation `{key}` on page {page + 1}."
    return "Failed to create annotation."


# =============================================================================
# DOI/URL Import Tools (Group 3)
# =============================================================================


@mcp.tool(
    name="zotero_add_by_doi",
    description="Add item to Zotero by DOI. Fetches metadata from CrossRef.",
)
async def add_by_doi(
    doi: str,
    collections: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    *,
    ctx: Context,
) -> str:
    """
    Import an item by DOI.

    Args:
        doi: DOI string (e.g. '10.1234/example').
        collections: Collection keys to add item to.
        tags: Tags to apply.
        ctx: MCP context.
    """
    ctx.info(f"Adding item by DOI: {doi}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    from zotmcp.crossref import fetch_crossref_metadata

    item_data = await fetch_crossref_metadata(doi)
    if not item_data:
        return f"Failed to fetch metadata for DOI `{doi}`. Check the DOI is valid."

    if collections:
        item_data["collections"] = collections
    if tags:
        item_data["tags"] = [{"tag": t} for t in tags]

    key = await client.create_item_raw(item_data)
    if key:
        title = item_data.get("title", "Unknown")
        return f"Created item `{key}`: {title}\nDOI: {doi}"
    return f"Failed to create item for DOI `{doi}`."


@mcp.tool(
    name="zotero_add_by_url",
    description="Add a web page item to Zotero by URL.",
)
async def add_by_url(
    url: str,
    collections: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    *,
    ctx: Context,
) -> str:
    """
    Add a web page reference.

    Args:
        url: Web page URL.
        collections: Collection keys.
        tags: Tags to apply.
        ctx: MCP context.
    """
    ctx.info(f"Adding by URL: {url}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    # Detect DOI URLs and delegate to add_by_doi
    import re as _re
    doi_match = _re.search(
        r'(?:doi\.org/|/doi/)(.+?)(?:\?|#|$)',
        url, _re.IGNORECASE
    )
    if not doi_match:
        doi_match = _re.search(r'(10\.\d{4,}/[^\s?#]+)', url)
    if doi_match:
        from urllib.parse import unquote
        doi = unquote(doi_match.group(1).rstrip('/'))
        ctx.info(f"Detected DOI in URL: {doi}, delegating to add_by_doi")
        from zotmcp.crossref import fetch_crossref_metadata
        item_data_doi = await fetch_crossref_metadata(doi)
        if item_data_doi:
            if collections:
                item_data_doi["collections"] = collections
            if tags:
                item_data_doi["tags"] = [{"tag": t} for t in tags]
            key = await client.create_item_raw(item_data_doi)
            if key:
                title = item_data_doi.get("title", "Unknown")
                return f"Created item `{key}`: {title}\nDOI: {doi}"
            return f"Failed to create item for DOI `{doi}`."
        return f"Failed to fetch metadata for DOI `{doi}`. Check the DOI is valid."

    item_data = {
        "itemType": "webpage",
        "title": url,
        "url": url,
        "accessDate": "",
        "creators": [],
        "tags": [{"tag": t} for t in (tags or [])],
        "collections": collections or [],
    }

    # Try to fetch title from URL
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as http:
            resp = await http.get(url)
            resp.raise_for_status()
            # Extract title from HTML
            import re
            title_match = re.search(r"<title[^>]*>([^<]+)</title>", resp.text, re.IGNORECASE)
            if title_match:
                item_data["title"] = title_match.group(1).strip()
    except Exception:
        pass  # Keep URL as title

    key = await client.create_item_raw(item_data)
    if key:
        return f"Created web page item `{key}`: {item_data['title']}"
    return f"Failed to create item for URL `{url}`."


@mcp.tool(
    name="zotero_add_from_file",
    description="Add an item to Zotero from a local file (PDF). Extracts DOI if possible.",
)
async def add_from_file(
    file_path: str,
    title: Optional[str] = None,
    item_type: str = "journalArticle",
    collections: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    *,
    ctx: Context,
) -> str:
    """
    Create a Zotero item from a local file.

    Args:
        file_path: Absolute path to the file (typically PDF).
        title: Item title. If None, uses filename.
        item_type: Zotero item type. Default 'journalArticle'.
        collections: Collection keys.
        tags: Tags to apply.
        ctx: MCP context.
    """
    ctx.info(f"Adding item from file: {file_path}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    if not os.path.isfile(file_path):
        return f"File not found: {file_path}"

    # Try to extract DOI from PDF
    extracted_doi = None
    if file_path.lower().endswith(".pdf"):
        try:
            from zotmcp.pdf_utils import extract_doi_from_pdf
            with open(file_path, "rb") as f:
                pdf_bytes = f.read()
            extracted_doi = extract_doi_from_pdf(pdf_bytes)
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"DOI extraction failed: {e}")

    # If DOI found, use CrossRef metadata
    if extracted_doi:
        from zotmcp.crossref import fetch_crossref_metadata
        item_data = await fetch_crossref_metadata(extracted_doi)
        if item_data:
            ctx.info(f"Found DOI {extracted_doi}, using CrossRef metadata")
        else:
            item_data = None
    else:
        item_data = None

    # Fallback: create minimal item
    if not item_data:
        item_data = {
            "itemType": item_type,
            "title": title or Path(file_path).stem,
            "creators": [],
            "tags": [],
        }

    if collections:
        item_data["collections"] = collections
    if tags:
        item_data["tags"] = [{"tag": t} for t in tags]

    key = await client.create_item_raw(item_data)
    if key:
        result = f"Created item `{key}`: {item_data.get('title', 'Untitled')}"
        if extracted_doi:
            result += f"\nDOI: {extracted_doi}"
        result += f"\nNote: File attachment must be added manually in Zotero or via drag-drop."
        return result
    return "Failed to create item."


# =============================================================================
# Duplicate Management Tools (Group 4)
# =============================================================================


@mcp.tool(
    name="zotero_find_duplicates",
    description="Find potential duplicate items in the library using multiple matching strategies.",
)
async def find_duplicates(
    method: str = "all",
    collection_key: Optional[str] = None,
    limit: int = 500,
    exclude_types: Optional[list[str]] = None,
    *,
    ctx: Context,
) -> str:
    """
    Find duplicate items with enhanced matching.

    Args:
        method: Matching method: 'title', 'doi', 'author_year', 'isbn', 'all' (default).
        collection_key: Limit search to a collection.
        limit: Maximum items to scan (default 500).
        exclude_types: Item types to exclude (default: note, attachment, annotation).
        ctx: MCP context.
    """
    ctx.info(f"Finding duplicates (method={method}, limit={limit})")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    # Get items
    if collection_key:
        items = await client.get_collection_items(collection_key, limit=limit)
    else:
        items = await client.get_all_items(limit=limit)

    if not items:
        return "No items found to check."

    # Filter out notes, attachments, annotations
    skip = set(exclude_types or ["note", "attachment", "annotation"])
    items = [i for i in items if i.item_type not in skip]

    if not items:
        return "No matchable items after filtering."

    import re as _re
    from collections import defaultdict

    def _normalize_doi(doi):
        if not doi:
            return None
        d = doi.strip().lower()
        for prefix in ["https://doi.org/", "http://doi.org/", "doi:", "doi.org/"]:
            if d.startswith(prefix):
                d = d[len(prefix):]
        return d.strip() if d else None

    def _normalize_title(title):
        if not title:
            return None
        t = title.lower().strip()
        t = _re.sub(r"[^\w\s]", "", t)
        t = _re.sub(r"\s+", " ", t).strip()
        for article in ["a ", "an ", "the "]:
            if t.startswith(article):
                t = t[len(article):]
        return t if len(t) > 3 else None

    def _author_year_key(item):
        creators = item.format_creators()
        first = creators.split(",")[0].strip().lower() if creators else ""
        year = ""
        if item.date:
            m = _re.search(r"(\d{4})", item.date)
            if m:
                year = m.group(1)
        return f"{first}_{year}" if first and year else None

    def _get_isbn(item):
        if not item.raw_data:
            return None
        isbn = item.raw_data.get("data", {}).get("ISBN", "")
        return isbn.strip().replace("-", "") if isbn else None

    # Build groups
    title_groups = defaultdict(list)
    doi_groups = defaultdict(list)
    author_year_groups = defaultdict(list)
    isbn_groups = defaultdict(list)

    for item in items:
        if method in ("title", "all"):
            nt = _normalize_title(item.title)
            if nt:
                title_groups[nt].append(item)

        if method in ("doi", "all"):
            nd = _normalize_doi(item.doi)
            if nd:
                doi_groups[nd].append(item)

        if method in ("author_year", "all"):
            ay = _author_year_key(item)
            if ay:
                author_year_groups[ay].append(item)

        if method in ("isbn", "all"):
            isbn = _get_isbn(item)
            if isbn:
                isbn_groups[isbn].append(item)

    # Collect groups, deduplicate across match types
    seen = set()
    dup_groups = []

    def _add_group(match_type, confidence, key, group):
        if len(group) < 2:
            return
        keys = tuple(sorted(i.key for i in group))
        if keys in seen:
            return
        seen.add(keys)
        dup_groups.append({
            "matchType": match_type,
            "confidence": confidence,
            "matchValue": key[:80],
            "count": len(group),
            "items": [
                {
                    "key": i.key,
                    "title": i.title[:80],
                    "type": i.item_type,
                    "date": i.date,
                    "creators": i.format_creators()[:50],
                }
                for i in group
            ],
        })

    for k, g in doi_groups.items():
        _add_group("doi", "high", k, g)
    for k, g in isbn_groups.items():
        _add_group("isbn", "high", k, g)
    for k, g in title_groups.items():
        _add_group("title", "medium", k, g)
    for k, g in author_year_groups.items():
        if len(g) >= 3:
            _add_group("author_year", "low", k, g)

    # Sort by confidence then count
    conf_order = {"high": 0, "medium": 1, "low": 2}
    dup_groups.sort(key=lambda g: (conf_order.get(g["confidence"], 3), -g["count"]))

    if not dup_groups:
        return f"No duplicates found among {len(items)} items."
    return json.dumps({
        "totalScanned": len(items),
        "duplicateGroups": len(dup_groups),
        "byConfidence": {
            "high": sum(1 for g in dup_groups if g["confidence"] == "high"),
            "medium": sum(1 for g in dup_groups if g["confidence"] == "medium"),
            "low": sum(1 for g in dup_groups if g["confidence"] == "low"),
        },
        "groups": dup_groups,
    }, indent=2, ensure_ascii=False)


@mcp.tool(
    name="zotero_merge_duplicates",
    description="Merge duplicate items by keeping one and trashing others. Dry-run by default.",
)
async def merge_duplicates(
    keeper_key: str,
    duplicate_keys: list[str],
    confirm: bool = False,
    *,
    ctx: Context,
) -> str:
    """
    Merge duplicate items.

    Args:
        keeper_key: Key of the item to keep.
        duplicate_keys: Keys of duplicate items to trash.
        confirm: Must be True to actually perform the merge. False = dry-run preview.
        ctx: MCP context.
    """
    ctx.info(f"Merge duplicates: keep {keeper_key}, trash {len(duplicate_keys)} items (confirm={confirm})")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    # Fetch details for preview
    keeper = await client.get_item(keeper_key)
    if not keeper:
        return f"Keeper item `{keeper_key}` not found."

    dupes = []
    for dk in duplicate_keys:
        item = await client.get_item(dk)
        if item:
            dupes.append(item)

    preview = {
        "action": "merge_duplicates",
        "keeper": {"key": keeper.key, "title": keeper.title, "creators": keeper.format_creators()},
        "toTrash": [{"key": d.key, "title": d.title} for d in dupes],
        "confirm": confirm,
    }

    if not confirm:
        preview["message"] = "DRY RUN: Set confirm=True to execute. This will trash the duplicate items."
        return json.dumps(preview, indent=2, ensure_ascii=False)

    # Actually trash duplicates
    trashed = []
    failed = []
    for dupe in dupes:
        success = await client.trash_item(dupe.key)
        if success:
            trashed.append(dupe.key)
        else:
            failed.append(dupe.key)

    preview["result"] = {"trashed": trashed, "failed": failed}
    preview["message"] = f"Merged: trashed {len(trashed)} items, {len(failed)} failed."
    return json.dumps(preview, indent=2, ensure_ascii=False)


# =============================================================================
# PDF Outline + Citation Key Tools (Group 5)
# =============================================================================


@mcp.tool(
    name="zotero_get_pdf_outline",
    description="Get the table of contents (outline/bookmarks) of a PDF attachment.",
)
async def get_pdf_outline(
    item_key: str,
    *,
    ctx: Context,
) -> str:
    """
    Extract PDF outline.

    Args:
        item_key: Attachment key for a PDF item.
        ctx: MCP context.
    """
    ctx.info(f"Getting PDF outline for: {item_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    pdf_bytes = await client.download_attachment(item_key)
    if not pdf_bytes:
        return f"Failed to download PDF for `{item_key}`. Is it a PDF attachment?"

    try:
        from zotmcp.pdf_utils import extract_pdf_outline
    except ImportError:
        return "Error: PyMuPDF not installed. Install with: pip install 'zotmcp[pdf]'"

    outline = extract_pdf_outline(pdf_bytes)
    if not outline:
        return f"No outline/bookmarks found in PDF `{item_key}`."

    # Format as indented text
    lines = []
    for entry in outline:
        indent = "  " * (entry["level"] - 1)
        lines.append(f"{indent}{entry['title']} (p. {entry['page']})")

    return "\n".join(lines)


@mcp.tool(
    name="zotero_search_by_citation_key",
    description="Search for items by citation key (e.g. 'smith2020' from BetterBibTeX or Extra field).",
)
async def search_by_citation_key(
    citekey: str,
    *,
    ctx: Context,
) -> str:
    """
    Search by citation key.

    Looks in the Extra field for 'Citation Key: ...' or 'bibtex: ...' patterns,
    and also checks if the citekey appears as a tag.

    Args:
        citekey: Citation key to search for.
        ctx: MCP context.
    """
    ctx.info(f"Searching for citation key: {citekey}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    # Search by the citekey as a query (will match in Extra and other fields)
    items = await client.search_items(citekey, limit=20)

    matches = []
    citekey_lower = citekey.lower()
    for item in items:
        # Check Extra field for citation key patterns
        extra = (item.extra or "").lower()
        is_match = False

        if f"citation key: {citekey_lower}" in extra:
            is_match = True
        elif f"bibtex: {citekey_lower}" in extra:
            is_match = True
        elif citekey_lower in [t.lower() for t in item.tags]:
            is_match = True
        elif citekey_lower in extra:
            is_match = True

        if is_match:
            matches.append({
                "key": item.key,
                "title": item.title,
                "creators": item.format_creators(),
                "date": item.date,
                "doi": item.doi,
                "extra": item.extra[:200] if item.extra else "",
            })

    if not matches:
        return f"No items found with citation key '{citekey}'."
    return json.dumps(matches, indent=2, ensure_ascii=False)



@mcp.tool(
    name="zotero_find_duplicate_pdfs",
    description="Find duplicate PDF attachments by SHA-256 content hash across stored and linked files.",
)
async def find_duplicate_pdfs(
    limit: int = 500,
    collection_key: Optional[str] = None,
    storage_path: Optional[str] = None,
    linked_base_path: Optional[str] = None,
    include_missing: bool = False,
    *,
    ctx: Context,
) -> str:
    """
    Find duplicate PDFs by file content hash.

    Resolves both Zotero stored PDFs and linked PDFs, computes SHA-256 for
    files with matching sizes, then separates database link duplicates from
    duplicate physical files.

    Args:
        limit: Maximum number of parent items to scan.
        collection_key: Limit to a specific collection.
        storage_path: Override Zotero storage path (auto-detected if not set).
        linked_base_path: Override Zotero linked attachment base path.
        include_missing: Include missing PDF attachment details in output.
        ctx: MCP context.
    """
    import hashlib
    from collections import Counter

    from zotmcp.utils import get_zotero_base_attachment_path

    ctx.info(f"Scanning for duplicate PDFs (limit={limit})")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    def _human_size(size: int) -> str:
        return f"{size/1024/1024:.1f} MB" if size > 1024 * 1024 else f"{size/1024:.0f} KB"

    def _file_sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _norm_path(path: Path) -> str:
        return os.path.normcase(os.path.abspath(str(path)))

    def _expand_path(path: str) -> Path:
        return Path(os.path.expanduser(os.path.expandvars(path)))

    def _find_stored_pdf(att_key: str, filename: str) -> Optional[Path]:
        if not storage_dir:
            return None
        key_dir = storage_dir / att_key
        if not key_dir.is_dir():
            return None
        if filename:
            candidate = key_dir / filename
            if candidate.is_file():
                return candidate
        for f in key_dir.iterdir():
            if f.suffix.lower() == ".pdf" and f.is_file():
                return f
        return None

    def _resolve_linked_pdf(raw_path: str) -> Optional[Path]:
        if not raw_path:
            return None
        path = raw_path
        if path.startswith("attachments:"):
            if not linked_base:
                return None
            rel_path = path[len("attachments:"):].lstrip("/\\")
            return linked_base / rel_path
        candidate = _expand_path(path)
        if candidate.is_absolute():
            return candidate
        if linked_base:
            return linked_base / path
        return candidate

    # Auto-detect paths.
    storage_dir: Optional[Path] = None
    if not storage_path:
        candidates = [
            Path(r"D:/Zotero/ZoteroDB/storage"),
            Path(os.path.expandvars(r"%USERPROFILE%/Zotero/storage")),
            Path(r"C:/Users/KBO/Zotero/storage"),
        ]
        for p in candidates:
            if p.is_dir():
                storage_path = str(p)
                break
    if storage_path:
        storage_candidate = _expand_path(storage_path)
        if storage_candidate.is_dir():
            storage_dir = storage_candidate

    linked_base: Optional[Path] = None
    linked_base_value = linked_base_path or get_zotero_base_attachment_path()
    if linked_base_value:
        linked_base = _expand_path(linked_base_value)

    ctx.info(f"Using storage: {storage_dir or 'not found'}")
    ctx.info(f"Using linked base: {linked_base or 'not found'}")

    # Get items to map keys to metadata
    if collection_key:
        items = await client.get_collection_items(collection_key, limit=limit)
    else:
        items = await client.get_all_items(limit=limit)

    # Build attachment records with resolved disk paths.
    attachments = {}
    link_modes = Counter()
    for item in items:
        children = await client.get_item_children(item.key)
        for child in children:
            raw = child.raw_data or {}
            data = raw.get("data", {})
            ct = data.get("contentType", "")
            filename = data.get("filename") or child.title or ""
            if ct == "application/pdf" or (
                child.item_type == "attachment"
                and ((child.title or "").lower().endswith(".pdf") or filename.lower().endswith(".pdf"))
            ):
                link_mode = data.get("linkMode", "unknown")
                raw_path = data.get("path", "")
                link_modes[link_mode] += 1
                resolved_path = None
                if link_mode == "linked_file":
                    resolved_path = _resolve_linked_pdf(raw_path)
                elif link_mode == "imported_file":
                    resolved_path = _find_stored_pdf(child.key, filename)
                else:
                    stored = _find_stored_pdf(child.key, filename)
                    resolved_path = stored or _resolve_linked_pdf(raw_path)

                exists = bool(resolved_path and resolved_path.is_file())
                size = resolved_path.stat().st_size if exists and resolved_path else None
                attachments[child.key] = {
                    "attachmentKey": child.key,
                    "filename": filename,
                    "attachmentTitle": child.title,
                    "linkMode": link_mode,
                    "zoteroPath": raw_path,
                    "resolvedPath": str(resolved_path) if resolved_path else "",
                    "pathKey": _norm_path(resolved_path) if exists and resolved_path else "",
                    "exists": exists,
                    "size": size,
                    "parentKey": item.key,
                    "parentTitle": item.title,
                    "parentType": item.item_type,
                    "parentCreators": item.format_creators()[:60],
                }

    ctx.info(f"Found {len(attachments)} PDF attachments across {len(items)} items")

    if len(attachments) < 2:
        return f"Found {len(attachments)} PDF(s). Need at least 2 to check."

    existing = {k: v for k, v in attachments.items() if v["exists"]}
    missing = [v for v in attachments.values() if not v["exists"]]

    ctx.info(f"Resolved {len(existing)} PDFs on disk; missing {len(missing)}")

    # Same physical path means database/link duplication, not duplicate disk usage.
    path_groups = {}
    for key, info in existing.items():
        path_groups.setdefault(info["pathKey"], []).append(key)
    same_path_groups = [
        {
            "path": existing[keys[0]]["resolvedPath"],
            "count": len(keys),
            "fileSize": _human_size(existing[keys[0]]["size"]),
            "attachments": [
                {
                    "attachmentKey": k,
                    "linkMode": existing[k]["linkMode"],
                    "parentKey": existing[k]["parentKey"],
                    "parentTitle": existing[k]["parentTitle"][:80],
                    "parentCreators": existing[k]["parentCreators"],
                }
                for k in keys
            ],
        }
        for keys in path_groups.values()
        if len(keys) >= 2
    ]

    # Phase 1: Group by file size (quick pre-filter).
    size_groups = {}
    for key, info in existing.items():
        size_groups.setdefault(info["size"], []).append(key)

    # Only hash files that share a size with at least one other attachment.
    to_hash = set()
    for size, keys in size_groups.items():
        if len(keys) >= 2:
            to_hash.update(keys)

    ctx.info(f"Hashing {len(to_hash)} PDFs with matching sizes...")

    # Phase 2: Compute SHA-256 for candidates.
    hash_groups = {}
    for key in to_hash:
        path = Path(existing[key]["resolvedPath"])
        try:
            sha = _file_sha256(path)
            hash_groups.setdefault(sha, []).append(key)
        except Exception as e:
            existing[key]["hashError"] = str(e)

    # Collect content duplicate groups. Disk waste counts unique physical paths,
    # so two Zotero attachments pointing at one linked file waste 0 bytes.
    dup_groups = []
    total_wasted = 0
    for sha, keys in hash_groups.items():
        if len(keys) >= 2:
            unique_paths = sorted({existing[k]["pathKey"] for k in keys})
            files = []
            for k in keys:
                info = existing[k]
                size = info["size"]
                files.append({
                    "attachmentKey": k,
                    "filename": info["filename"],
                    "linkMode": info["linkMode"],
                    "zoteroPath": info["zoteroPath"],
                    "resolvedPath": info["resolvedPath"],
                    "size": size,
                    "sizeHuman": _human_size(size),
                    "parentKey": info["parentKey"],
                    "parentTitle": info["parentTitle"][:80],
                    "parentType": info["parentType"],
                    "parentCreators": info["parentCreators"],
                })
            wasted = existing[keys[0]]["size"] * (len(unique_paths) - 1)
            total_wasted += wasted
            dup_groups.append({
                "sha256": sha[:16] + "...",
                "count": len(keys),
                "uniquePhysicalFiles": len(unique_paths),
                "duplicateType": "same_path" if len(unique_paths) == 1 else "same_hash_different_path",
                "fileSize": files[0]["sizeHuman"],
                "wastedSpace": _human_size(wasted),
                "files": files,
            })

    dup_groups.sort(key=lambda g: (-g["uniquePhysicalFiles"], -g["count"]))

    result = {
        "storagePath": str(storage_dir) if storage_dir else None,
        "linkedBasePath": str(linked_base) if linked_base else None,
        "totalItems": len(items),
        "totalPDFs": len(attachments),
        "linkModes": dict(link_modes),
        "pdfsOnDisk": len(existing),
        "missingPDFs": len(missing),
        "candidatesHashed": len(to_hash),
        "duplicateGroups": len(dup_groups),
        "samePathGroups": len(same_path_groups),
        "totalWastedSpace": _human_size(total_wasted),
    }

    if same_path_groups:
        result["samePathDetails"] = same_path_groups
    if include_missing and missing:
        result["missingDetails"] = [
            {
                "attachmentKey": m["attachmentKey"],
                "filename": m["filename"],
                "linkMode": m["linkMode"],
                "zoteroPath": m["zoteroPath"],
                "resolvedPath": m["resolvedPath"],
                "parentKey": m["parentKey"],
                "parentTitle": m["parentTitle"][:80],
            }
            for m in missing
        ]
    if not dup_groups:
        result["message"] = "No duplicate PDFs found."
    else:
        result["groups"] = dup_groups

    return json.dumps(result, indent=2, ensure_ascii=False)



# =============================================================================
# Item Update Tool
# =============================================================================


@mcp.tool(
    name="zotero_update_item",
    description="Update fields of an existing Zotero item (title, itemType, date, journal, etc.).",
)
async def update_item_fields(
    item_key: str,
    fields: dict,
    *,
    ctx: Context,
) -> str:
    """
    Update one or more fields of a Zotero item.

    Args:
        item_key: The key of the item to update.
        fields: Dict of field names to new values (e.g. {"title": "New Title", "itemType": "journalArticle"}).
        ctx: MCP context.
    """
    if not isinstance(fields, dict) or not fields:
        return "Error: fields must be a non-empty dict."

    PROTECTED = {"key", "version", "dateAdded", "dateModified"}
    bad = PROTECTED & set(fields.keys())
    if bad:
        return f"Error: cannot modify protected fields: {bad}"

    ctx.info(f"Updating item {item_key}: {list(fields.keys())}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    success = await client.update_item(item_key, fields)
    if success:
        return f"Updated item `{item_key}`: {', '.join(f'{k}={v!r}' for k, v in fields.items())}"
    return f"Failed to update item `{item_key}`. Check the key and field names are valid."


# =============================================================================
# Batch BibTeX Export Tool
# =============================================================================


@mcp.tool(
    name="zotero_batch_export_bibtex",
    description="Export multiple Zotero items as combined BibTeX by their keys.",
)
async def batch_export_bibtex(
    item_keys: list[str],
    *,
    ctx: Context,
) -> str:
    """
    Export a batch of Zotero items as BibTeX.

    Args:
        item_keys: List of Zotero item keys to export.
        ctx: MCP context.
    """
    ctx.info(f"Exporting {len(item_keys)} items as BibTeX")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    results = []
    errors = []

    for key in item_keys:
        item = await client.get_item(key)
        if not item:
            errors.append(f"% Item not found: {key}")
            continue

        # Generate BibTeX
        bibtex_type = {
            "journalArticle": "article",
            "book": "book",
            "bookSection": "incollection",
            "conferencePaper": "inproceedings",
            "thesis": "phdthesis",
            "report": "techreport",
            "preprint": "article",
            "webpage": "misc",
        }.get(item.item_type, "misc")

        def _bib_escape(s):
            """Escape special BibTeX characters."""
            if not s:
                return s
            return s.replace('\\', '\\textbackslash{}').replace('{', '\\{').replace('}', '\\}').replace('&', '\\&').replace('%', '\\%').replace('#', '\\#')

        lines = [f"@{bibtex_type}{{{item.key},"]
        lines.append(f"  title = {{{_bib_escape(item.title)}}},")
        lines.append(f"  author = {{{item.format_creators()}}},")
        if item.date:
            lines.append(f"  year = {{{item.date[:4] if len(item.date) >= 4 else item.date}}},")

        raw = item.raw_data or {}
        data = raw.get("data", raw)

        for field, bib_field in [
            ("publicationTitle", "journal"),
            ("proceedingsTitle", "booktitle"),
            ("volume", "volume"),
            ("issue", "number"),
            ("pages", "pages"),
            ("publisher", "publisher"),
            ("DOI", "doi"),
            ("url", "url"),
            ("ISBN", "isbn"),
            ("ISSN", "issn"),
            ("abstractNote", "abstract"),
        ]:
            val = data.get(field, "")
            if val:
                lines.append(f"  {bib_field} = {{{val}}},")

        lines.append("}")
        results.append("\n".join(lines))

    output = "\n\n".join(results)
    if errors:
        output = "\n".join(errors) + "\n\n" + output
    return output


# =============================================================================
# PDF Fetch Tool (Unpaywall + SciHub fallback)
# =============================================================================


@mcp.tool(
    name="zotero_fetch_pdf",
    description="Fetch PDF for a Zotero item by DOI via Unpaywall (open access) or Sci-Hub, and attach it.",
)
async def fetch_pdf_for_item(
    item_key: str,
    *,
    ctx: Context,
) -> str:
    """
    Fetch PDF for an item and attach to Zotero.

    Tries Unpaywall (open access) first, then Sci-Hub as fallback.

    Args:
        item_key: The key of the Zotero item.
        ctx: MCP context.
    """
    import tempfile
    import os
    import httpx

    ctx.info(f"Fetching PDF for item {item_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    item = await client.get_item(item_key)
    if not item:
        return f"Item `{item_key}` not found."

    raw_data = (item.raw_data or {}).get("data", item.raw_data or {})
    doi = raw_data.get("DOI", "")
    item_url = raw_data.get("url", "")
    archive_id = raw_data.get("archiveID", "")

    if not doi and not item_url:
        return f"Item `{item_key}` has no DOI or URL. Cannot fetch PDF."

    ctx.info(f"DOI: {doi}, URL: {item_url}")

    pdf_url = None
    pdf_data = None
    source = ""

    # Try 0: Direct preprint PDF (arXiv, bioRxiv)
    import re as _re
    arxiv_id = None
    if archive_id and "arXiv:" in archive_id:
        arxiv_id = archive_id.replace("arXiv:", "")
    elif doi and doi.startswith("10.48550/"):
        m = _re.search(r"arXiv\.(\d+\.\d+)", doi, _re.IGNORECASE)
        if m:
            arxiv_id = m.group(1)
    elif item_url and "arxiv.org" in item_url:
        m = _re.search(r"arxiv\.org/abs/(\d+\.\d+)", item_url)
        if m:
            arxiv_id = m.group(1)

    if arxiv_id:
        try:
            arxiv_pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            ctx.info(f"Trying arXiv direct: {arxiv_pdf_url}")
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
                resp = await http.get(arxiv_pdf_url)
                if resp.status_code == 200 and len(resp.content) > 5000:
                    pdf_data = resp.content
                    source = "arXiv (direct)"
        except Exception as e:
            ctx.info(f"arXiv direct failed: {e}")

    if not pdf_data and doi and doi.startswith("10.1101/"):
        try:
            biorxiv_pdf_url = f"https://www.biorxiv.org/content/{doi}v1.full.pdf"
            ctx.info(f"Trying bioRxiv direct: {biorxiv_pdf_url}")
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as http:
                resp = await http.get(biorxiv_pdf_url)
                if resp.status_code == 200 and len(resp.content) > 5000:
                    pdf_data = resp.content
                    source = "bioRxiv (direct)"
        except Exception as e:
            ctx.info(f"bioRxiv direct failed: {e}")

    # Try 1: Unpaywall (open access)
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as http:
            unpaywall_url = f"https://api.unpaywall.org/v2/{doi}?email=zotmcp@example.com"
            ctx.info(f"Checking Unpaywall...")
            resp = await http.get(unpaywall_url)
            if resp.status_code == 200:
                data = resp.json()
                best_oa = data.get("best_oa_location") or {}
                pdf_url = best_oa.get("url_for_pdf") or best_oa.get("url")
                if pdf_url:
                    source = "Unpaywall (open access)"
                    ctx.info(f"Found OA PDF: {pdf_url}")
    except Exception as e:
        ctx.info(f"Unpaywall failed: {e}")

    # Try 2: Sci-Hub
    if not pdf_url:
        scihub_domains = ["sci-hub.se", "sci-hub.st", "sci-hub.ru"]
        for domain in scihub_domains:
            try:
                scihub_url = f"https://{domain}/{doi}"
                ctx.info(f"Trying Sci-Hub ({domain})...")
                async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as http:
                    resp = await http.get(scihub_url)
                    if resp.status_code == 200 and "application/pdf" in resp.headers.get("content-type", ""):
                        # Direct PDF response
                        pdf_data = resp.content
                        source = f"Sci-Hub ({domain})"
                        break
                    elif resp.status_code == 200:
                        # Parse HTML for PDF: embed src, iframe, or button onclick
                        import re as _re2
                        # Sci-Hub typically uses <embed> or <iframe> with src
                        for pattern in [
                            r'<embed[^>]+src=["\'](/[^"\'>]+)["\'\s>]',
                            r'<iframe[^>]+src=["\'](/[^"\'>]+)["\'\s>]',
                            r'<embed[^>]+src=["\']([^"\'>]+)["\']',
                            r'<iframe[^>]+src=["\']([^"\'>]+)["\']',
                            r'(?:src|href)=["\']([^"\'>]*\.pdf[^"\'>]*)',
                            r'(?:src|href)=["\']([^"\'>]*/downloads?/[^"\'>]*)',
                        ]:
                            pdf_match = _re2.search(pattern, resp.text, _re2.IGNORECASE)
                            if pdf_match:
                                pdf_url = pdf_match.group(1)
                                if pdf_url.startswith("//"):
                                    pdf_url = "https:" + pdf_url
                                elif pdf_url.startswith("/"):
                                    pdf_url = f"https://{domain}{pdf_url}"
                                source = f"Sci-Hub ({domain})"
                                break
                        if source:
                            break
            except Exception:
                continue

    if not pdf_data and not pdf_url:
        return f"No PDF found for DOI `{doi}`. Tried Unpaywall and Sci-Hub."

    # Download PDF if we only have URL
    try:
        if pdf_data is None and pdf_url:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http:
                ctx.info(f"Downloading PDF from {source}...")
                resp = await http.get(pdf_url)
                resp.raise_for_status()
                pdf_data = resp.content
    except Exception as e:
        return f"Failed to download PDF: {e}"

    if len(pdf_data) < 1000:
        return f"Downloaded file too small ({len(pdf_data)} bytes), likely not a valid PDF."

    # Save as linked file in Zotero's linked attachment base directory
    from zotmcp.utils import get_zotero_base_attachment_path
    linked_base = get_zotero_base_attachment_path()

    safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in item.title[:80])
    filename = f"{safe_title}.pdf"

    if linked_base:
        pdf_dir = os.path.join(linked_base, safe_title)
        os.makedirs(pdf_dir, exist_ok=True)
        pdf_path = os.path.join(pdf_dir, filename)
    else:
        # Fallback to temp dir if no linked base
        pdf_dir = tempfile.mkdtemp()
        pdf_path = os.path.join(pdf_dir, filename)

    with open(pdf_path, "wb") as f:
        f.write(pdf_data)

    size_kb = len(pdf_data) / 1024
    ctx.info(f"Saved PDF to {pdf_path} ({size_kb:.0f} KB)")

    # Create linked file attachment in Zotero via Web API
    try:
        from zotmcp.clients import ZoteroWebClient
        if hasattr(client, '_web_client') and isinstance(client._web_client, ZoteroWebClient):
            zot = client._web_client._get_zot()
        elif isinstance(client, ZoteroWebClient):
            zot = client._get_zot()
        else:
            return f"PDF saved to `{pdf_path}` ({size_kb:.0f} KB) but cannot create linked attachment: unsupported client type."

        import asyncio
        loop = asyncio.get_event_loop()

        # Build relative path from linked base for Zotero's "attachments:" prefix
        if linked_base:
            rel_path = os.path.relpath(pdf_path, linked_base)
            zotero_path = f"attachments:{rel_path}"
        else:
            zotero_path = pdf_path

        def _create_linked():
            template = zot.item_template("attachment", "linked_file")
            template["title"] = filename
            template["path"] = zotero_path
            template["contentType"] = "application/pdf"
            template["parentItem"] = item_key
            resp = zot.create_items([template])
            return resp

        resp = await loop.run_in_executor(None, _create_linked)

        # Check success
        success_keys = resp.get("successful", resp.get("success", {}))
        if success_keys:
            attach_key = list(success_keys.values())[0] if isinstance(success_keys, dict) else "unknown"
            return f"PDF linked to `{item_key}` ({size_kb:.0f} KB) via {source}.\nPath: `{pdf_path}`"
        else:
            failed = resp.get("failed", {})
            return f"PDF saved to `{pdf_path}` ({size_kb:.0f} KB) but linked attachment creation failed: {failed}"
    except Exception as e:
        return f"PDF saved to `{pdf_path}` ({size_kb:.0f} KB) but link failed: {e}. Create linked attachment manually in Zotero."



# =============================================================================
# Citation Rendering Tools
# =============================================================================


@mcp.tool(
    name="zotero_cite",
    description="Generate a formatted citation or bibliography entry for a Zotero item using a CSL style (e.g., apa, nature, vancouver). Uses Zotero's built-in CSL engine.",
)
async def cite_item(
    item_key: str,
    style: str = "apa",
    format: Literal["citation", "bibliography"] = "bibliography",
    *,
    ctx: Context,
) -> str:
    """
    Render a formatted citation or bibliography entry.

    Args:
        item_key: Zotero item key.
        style: CSL style name (e.g., apa, nature, vancouver, chicago-author-date, ieee).
        format: 'citation' for inline citation, 'bibliography' for full reference.
        ctx: MCP context.
    """
    import httpx, re
    ctx.info(f"Rendering {format} for {item_key} in {style} style")

    # Use Zotero Local API for CSL rendering
    if format == "bibliography":
        url = f"http://127.0.0.1:23119/api/users/0/items/{item_key}?format=bib&style={style}"
    else:
        url = f"http://127.0.0.1:23119/api/users/0/items/{item_key}?format=citation&style={style}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(url)
            if resp.status_code == 200:
                text = resp.text.strip()
                # Strip HTML tags
                text = re.sub(r'<[^>]+>', '', text).strip()
                return text
            else:
                return f"Failed to render citation (HTTP {resp.status_code}). Is Zotero running?"
    except Exception as e:
        return f"Failed to render citation: {e}"


# =============================================================================
# PMID Import Tool
# =============================================================================


@mcp.tool(
    name="zotero_add_by_pmid",
    description="Add a paper to Zotero by PubMed ID (PMID). Fetches metadata from NCBI E-utilities.",
)
async def add_by_pmid(
    pmid: str,
    collections: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    *,
    ctx: Context,
) -> str:
    """
    Add a paper by PubMed ID.

    Args:
        pmid: PubMed ID (numeric string, e.g., '12345678').
        collections: Optional collection keys.
        tags: Optional tags.
        ctx: MCP context.
    """
    import httpx, re
    ctx.info(f"Adding item by PMID: {pmid}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    # Fetch metadata from NCBI E-utilities (efetch)
    efetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={pmid}&retmode=xml"
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            resp = await http.get(efetch_url)
            resp.raise_for_status()
            xml = resp.text
    except Exception as e:
        return f"Failed to fetch PubMed metadata: {e}"

    # Parse XML for key fields
    def xml_text(tag):
        m = re.search(f'<{tag}[^>]*>(.+?)</{tag}>', xml, re.DOTALL)
        return m.group(1).strip() if m else ""

    title = xml_text("ArticleTitle")
    journal = xml_text("Title")  # journal full title
    volume = xml_text("Volume")
    issue = xml_text("Issue")
    year = xml_text("Year")
    pages_start = xml_text("MedlinePgn")

    # Parse authors
    creators = []
    for m in re.finditer(r'<Author[^>]*>.*?<LastName>(.+?)</LastName>.*?<ForeName>(.+?)</ForeName>.*?</Author>', xml, re.DOTALL):
        creators.append({"creatorType": "author", "lastName": m.group(1), "firstName": m.group(2)})

    # Extract DOI if available
    doi_m = re.search(r'<ArticleId IdType="doi">(.+?)</ArticleId>', xml)
    doi = doi_m.group(1) if doi_m else ""

    if not title:
        return f"No article found for PMID {pmid}."

    item_data = {
        "itemType": "journalArticle",
        "title": title,
        "creators": creators,
        "date": year,
        "publicationTitle": journal,
        "volume": volume,
        "issue": issue,
        "pages": pages_start,
        "DOI": doi,
        "extra": f"PMID: {pmid}",
        "tags": [{"tag": t} for t in (tags or [])],
        "collections": collections or [],
    }

    key = await client.create_item_raw(item_data)
    if key:
        return f"Created item `{key}`: {title}\nPMID: {pmid}" + (f"\nDOI: {doi}" if doi else "")
    return f"Failed to create item for PMID {pmid}."


# =============================================================================
# Collection Stats Tool
# =============================================================================


@mcp.tool(
    name="zotero_collection_stats",
    description="Get statistics for a Zotero collection: item count, PDF coverage, year distribution, top journals.",
)
async def collection_stats(
    collection_key: str,
    *,
    ctx: Context,
) -> str:
    """
    Get collection statistics.

    Args:
        collection_key: Zotero collection key.
        ctx: MCP context.
    """
    from collections import Counter
    ctx.info(f"Computing stats for collection {collection_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    items = await client.get_collection_items(collection_key, limit=500)
    if not items:
        return f"Collection `{collection_key}` is empty or not found."

    total = len(items)
    types = Counter()
    years = Counter()
    journals = Counter()
    has_pdf = 0

    for item in items:
        types[item.item_type] += 1
        if item.date:
            year = item.date[:4]
            if year.isdigit():
                years[year] += 1
        raw = (item.raw_data or {}).get("data", {})
        journal = raw.get("publicationTitle", "")
        if journal:
            journals[journal] += 1
        children = await client.get_item_children(item.key)
        for child in children:
            if child.item_type == "attachment" and "pdf" in (child.title or "").lower():
                has_pdf += 1
                break

    lines = [f"## Collection Stats ({total} items)\n"]
    lines.append(f"**PDF coverage:** {has_pdf}/{total} ({100*has_pdf//total if total else 0}%)\n")
    lines.append("**Item types:**")
    for t, c in types.most_common():
        lines.append(f"  - {t}: {c}")
    lines.append("\n**Year distribution (top 10):**")
    for y, c in sorted(years.items(), reverse=True)[:10]:
        lines.append(f"  - {y}: {c}")
    lines.append("\n**Top journals:**")
    for j, c in journals.most_common(10):
        lines.append(f"  - {j}: {c}")

    return "\n".join(lines)


# =============================================================================
# Remove from Collection Tool
# =============================================================================


@mcp.tool(
    name="zotero_remove_from_collection",
    description="Remove an item from a collection (does NOT trash the item, just unlinks it from the collection).",
)
async def remove_from_collection(
    item_key: str,
    collection_key: str,
    *,
    ctx: Context,
) -> str:
    """
    Remove an item from a collection.

    Args:
        item_key: Zotero item key.
        collection_key: Collection key to remove from.
        ctx: MCP context.
    """
    import httpx
    ctx.info(f"Removing {item_key} from collection {collection_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    # Get current item to find its collections and version
    item = await client.get_item(item_key)
    if not item:
        return f"Item `{item_key}` not found."

    raw = (item.raw_data or {}).get("data", {})
    current_collections = raw.get("collections", [])

    if collection_key not in current_collections:
        return f"Item `{item_key}` is not in collection `{collection_key}`."

    new_collections = [c for c in current_collections if c != collection_key]
    success = await client.update_item(item_key, {"collections": new_collections})

    if success:
        return f"Removed `{item_key}` from collection `{collection_key}`."
    return f"Failed to remove `{item_key}` from collection."


# =============================================================================
# Library Stats Tool
# =============================================================================


@mcp.tool(
    name="zotero_library_stats",
    description="Get overall library statistics: total items, type breakdown, tag count, collection count.",
)
async def library_stats(
    *,
    ctx: Context,
) -> str:
    """Get library-wide statistics."""
    ctx.info("Computing library stats")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    collections = await client.get_collections()
    tags = await client.get_tags()
    recent = await client.get_all_items(limit=500)

    from collections import Counter
    types = Counter(item.item_type for item in recent)

    lines = [f"## Library Statistics\n"]
    lines.append(f"**Total items (sampled):** {len(recent)}")
    lines.append(f"**Collections:** {len(collections)}")
    lines.append(f"**Tags:** {len(tags)}")
    lines.append("\n**Item types:**")
    for t, c in types.most_common(15):
        lines.append(f"  - {t}: {c}")

    return "\n".join(lines)


# =============================================================================
# Sync Tool
# =============================================================================


@mcp.tool(
    name="zotero_sync",
    description="Trigger Zotero library sync via the Local API.",
)
async def trigger_sync(
    *,
    ctx: Context,
) -> str:
    """Trigger a Zotero sync."""
    import httpx
    ctx.info("Triggering Zotero sync")
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post("http://127.0.0.1:23119/connector/triggerSync")
            if resp.status_code == 200:
                return "Sync triggered successfully."
            return f"Sync trigger returned HTTP {resp.status_code}."
    except Exception as e:
        return f"Failed to trigger sync: {e}"




# =============================================================================
# iCite Citation Metrics Tool
# =============================================================================


@mcp.tool(
    name="zotero_item_metrics",
    description="Get NIH iCite citation metrics (citation count, Relative Citation Ratio, NIH percentile) for a Zotero item by its PMID or DOI.",
)
async def item_metrics(
    item_key: str,
    *,
    ctx: Context,
) -> str:
    """
    Get citation metrics from NIH iCite API.

    Args:
        item_key: Zotero item key. The item must have a DOI or PMID in its metadata.
        ctx: MCP context.
    """
    import httpx, re
    ctx.info(f"Fetching citation metrics for {item_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    item = await client.get_item(item_key)
    if not item:
        return f"Item `{item_key}` not found."

    raw = (item.raw_data or {}).get("data", {})
    doi = raw.get("DOI", "")
    extra = raw.get("extra", "")

    # Extract PMID from extra field
    pmid = ""
    pmid_m = re.search(r'PMID:\s*(\d+)', extra)
    if pmid_m:
        pmid = pmid_m.group(1)

    if not doi and not pmid:
        return f"Item `{item_key}` has no DOI or PMID. Cannot fetch metrics."

    # iCite API accepts DOI or PMID
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            if pmid:
                resp = await http.get(f"https://icite.od.nih.gov/api/pubs?pmids={pmid}&format=csv")
            else:
                # First resolve DOI to PMID via NCBI
                id_resp = await http.get(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={doi}[doi]&retmode=json")
                if id_resp.status_code == 200:
                    ids = id_resp.json().get("esearchresult", {}).get("idlist", [])
                    if ids:
                        pmid = ids[0]
                        resp = await http.get(f"https://icite.od.nih.gov/api/pubs?pmids={pmid}&format=csv")
                    else:
                        return f"DOI `{doi}` not found in PubMed. iCite requires a PMID."
                else:
                    return f"Failed to resolve DOI to PMID."

            if resp.status_code != 200:
                return f"iCite API returned HTTP {resp.status_code}."

            # Parse CSV response (header + 1 data row)
            lines_csv = resp.text.strip().split("\n")
            if len(lines_csv) < 2:
                return f"No metrics found for PMID {pmid}."

            headers = lines_csv[0].split(",")
            values = lines_csv[1].split(",")
            data = dict(zip(headers, values))

            result_lines = [f"## Citation Metrics for `{item_key}`\n"]
            result_lines.append(f"**PMID:** {pmid}")
            result_lines.append(f"**Title:** {item.title[:80]}")
            result_lines.append(f"**Year:** {data.get('year', 'N/A')}")
            result_lines.append(f"**Citation Count:** {data.get('citation_count', 'N/A')}")
            result_lines.append(f"**Relative Citation Ratio (RCR):** {data.get('relative_citation_ratio', 'N/A')}")
            result_lines.append(f"**NIH Percentile:** {data.get('nih_percentile', 'N/A')}")
            result_lines.append(f"**Expected Citations/Year:** {data.get('expected_citations_per_year', 'N/A')}")
            result_lines.append(f"**Field Citation Rate:** {data.get('field_citation_rate', 'N/A')}")
            result_lines.append(f"**Is Clinical:** {data.get('is_clinical', 'N/A')}")
            result_lines.append(f"**Provisional:** {data.get('provisional', 'N/A')}")
            return "\n".join(result_lines)
    except Exception as e:
        return f"Failed to fetch metrics: {e}"


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
# Trash List / Restore Tools
# =============================================================================


@mcp.tool(
    name="zotero_list_trash",
    description="List items in the Zotero trash.",
)
async def list_trash(
    limit: int = 25,
    *,
    ctx: Context,
) -> str:
    """
    List trashed items.

    Args:
        limit: Max items to return.
        ctx: MCP context.
    """
    import httpx
    ctx.info("Listing trash items")

    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.get(
                "http://127.0.0.1:23119/api/users/0/items/trash",
                params={"limit": str(limit)}
            )
            if resp.status_code == 200:
                items = resp.json()
                if not items:
                    return "Trash is empty."
                lines = [f"## Trash ({len(items)} items)\n"]
                for it in items:
                    data = it.get("data", it)
                    key = data.get("key", "?")
                    title = data.get("title", "Untitled")[:80]
                    itype = data.get("itemType", "?")
                    lines.append(f"- `{key}` ({itype}) {title}")
                return "\n".join(lines)
            return f"Failed to list trash (HTTP {resp.status_code})."
    except Exception as e:
        return f"Failed to list trash: {e}"


@mcp.tool(
    name="zotero_restore_from_trash",
    description="Restore an item from Zotero trash back to the library.",
)
async def restore_from_trash(
    item_key: str,
    *,
    ctx: Context,
) -> str:
    """
    Restore a trashed item.

    Args:
        item_key: Key of the trashed item to restore.
        ctx: MCP context.
    """
    import httpx
    ctx.info(f"Restoring {item_key} from trash")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    # Set deleted=0 to restore
    # Need to get item version first via Web API
    from zotmcp.clients import ZoteroWebClient
    if hasattr(client, '_web_client') and isinstance(client._web_client, ZoteroWebClient):
        zot = client._web_client._get_zot()
    elif isinstance(client, ZoteroWebClient):
        zot = client._get_zot()
    else:
        return "Restore requires Web API client."

    import asyncio
    loop = asyncio.get_event_loop()

    def _get_version():
        item = zot.item(item_key)
        return item.get("version") or item.get("data", {}).get("version") if item else None

    try:
        version = await loop.run_in_executor(None, _get_version)
        if not version:
            return f"Item `{item_key}` not found in trash."

        async with httpx.AsyncClient(timeout=15.0) as http:
            url = f"https://api.zotero.org/users/{zot.library_id}/items/{item_key}"
            headers = {
                "Zotero-API-Key": zot.api_key,
                "If-Unmodified-Since-Version": str(version),
                "Content-Type": "application/json",
            }
            resp = await http.patch(url, json={"deleted": 0}, headers=headers)
            resp.raise_for_status()
        return f"Item `{item_key}` restored from trash."
    except Exception as e:
        return f"Failed to restore item: {e}"


# =============================================================================
# Preprint Update Check Tool
# =============================================================================


@mcp.tool(
    name="zotero_check_preprint_published",
    description="Check whether a preprint (bioRxiv, arXiv) has been published in a journal. Returns the published DOI if found.",
)
async def check_preprint_published(
    item_key: str,
    *,
    ctx: Context,
) -> str:
    """
    Check if a preprint has been formally published.

    Args:
        item_key: Zotero item key of the preprint.
        ctx: MCP context.
    """
    import httpx, re
    ctx.info(f"Checking preprint status for {item_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    item = await client.get_item(item_key)
    if not item:
        return f"Item `{item_key}` not found."

    raw = (item.raw_data or {}).get("data", {})
    doi = raw.get("DOI", "")
    url = raw.get("url", "")

    if not doi and not url:
        return f"Item has no DOI or URL to check."

    results = []

    # Method 1: CrossRef relation check (preprint DOI -> published DOI)
    if doi:
        try:
            from urllib.parse import quote
            encoded_doi = quote(doi, safe="/")
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(
                    f"https://api.crossref.org/works/{encoded_doi}",
                    headers={"User-Agent": "ZotMCP/0.1.0"}
                )
                if resp.status_code == 200:
                    data = resp.json().get("message", {})
                    relations = data.get("relation", {})
                    # Check is-preprint-of relation
                    preprint_of = relations.get("is-preprint-of", [])
                    for rel in preprint_of:
                        pub_doi = rel.get("id", "")
                        if pub_doi:
                            results.append(f"Published version found via CrossRef: `{pub_doi}`")
                    if not preprint_of:
                        results.append("No published version found in CrossRef relations.")
        except Exception as e:
            results.append(f"CrossRef check failed: {e}")

    # Method 2: bioRxiv pubs API
    if doi and doi.startswith("10.1101/"):
        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(
                    f"https://api.biorxiv.org/pubs/biorxiv/{doi}",
                    headers={"User-Agent": "ZotMCP/0.1.0"}
                )
                if resp.status_code == 200:
                    pubs = resp.json().get("collection", [])
                    for pub in pubs:
                        pub_doi = pub.get("published_doi", "")
                        pub_journal = pub.get("published_journal", "")
                        if pub_doi:
                            results.append(f"Published in {pub_journal}: `{pub_doi}`")
        except Exception as e:
            results.append(f"bioRxiv pubs check failed: {e}")

    # Method 3: Semantic Scholar (works for arXiv too)
    if doi:
        try:
            async with httpx.AsyncClient(timeout=15.0) as http:
                resp = await http.get(
                    f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
                    params={"fields": "externalIds,journal,venue,year"}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    ext_ids = data.get("externalIds", {})
                    journal = data.get("journal", {})
                    venue = data.get("venue", "")
                    pub_doi = ext_ids.get("DOI", "")
                    if pub_doi and pub_doi.lower() != doi.lower():
                        results.append(f"Semantic Scholar found published DOI: `{pub_doi}`")
                    elif journal and journal.get("name"):
                        results.append(f"Semantic Scholar venue: {journal.get('name', venue)}")
                    else:
                        results.append("Semantic Scholar: no journal publication found.")
        except Exception as e:
            results.append(f"Semantic Scholar check failed: {e}")

    if not results:
        return f"Could not determine publication status for `{item_key}`."

    header = f"## Preprint Status: {item.title[:60]}\n"
    return header + "\n".join(results)


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
        config = get_config()
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


# =============================================================================
# DOCX Citation Tools
# =============================================================================


@mcp.tool(
    name="zotero_docx_scan_citations",
    description="Scan a DOCX file for Zotero citation markers ({{zotero:KEY}} or Zotero field codes) and return a list of cited items.",
)
async def docx_scan_citations(
    file_path: str,
    *,
    ctx: Context,
) -> str:
    """
    Scan DOCX for citation markers.

    Args:
        file_path: Absolute path to DOCX file.
        ctx: MCP context.
    """
    import re, zipfile, os
    ctx.info(f"Scanning DOCX: {file_path}")

    if not os.path.exists(file_path):
        return f"File not found: {file_path}"

    try:
        # DOCX is a ZIP; read document.xml
        with zipfile.ZipFile(file_path, 'r') as z:
            if 'word/document.xml' not in z.namelist():
                return "Not a valid DOCX file."
            doc_xml = z.read('word/document.xml').decode('utf-8', errors='ignore')

        # Find {{zotero:KEY}} markers
        template_keys = re.findall(r'\{\{zotero:([^}]+)\}\}', doc_xml)

        # Find Zotero field codes (from Zotero Word plugin)
        field_keys = re.findall(r'ADDIN ZOTERO_ITEM CSL_CITATION.*?"uris":\["[^"]*items/([A-Z0-9]+)"\]', doc_xml)

        all_keys = list(dict.fromkeys(template_keys + field_keys))  # deduplicate, preserve order

        if not all_keys:
            return "No Zotero citations found in document."

        lines = [f"## Citations in DOCX ({len(all_keys)} unique keys)\n"]
        client = get_client()
        for key in all_keys:
            item = await client.get_item(key)
            if item:
                lines.append(f"- `{key}`: {item.title[:80]}")
            else:
                lines.append(f"- `{key}`: (not found in Zotero)")

        return "\n".join(lines)
    except Exception as e:
        return f"Failed to scan DOCX: {e}"


@mcp.tool(
    name="zotero_docx_render_citations",
    description="Render {{zotero:KEY}} placeholders in a DOCX file with formatted citations. Creates a new file with rendered citations.",
)
async def docx_render_citations(
    file_path: str,
    output_path: Optional[str] = None,
    style: str = "apa",
    *,
    ctx: Context,
) -> str:
    """
    Render citation placeholders in DOCX.

    Args:
        file_path: Path to input DOCX.
        output_path: Path for output DOCX. Defaults to input_rendered.docx.
        style: CSL citation style.
        ctx: MCP context.
    """
    import re, zipfile, os, shutil, httpx
    ctx.info(f"Rendering citations in {file_path}")

    if not os.path.exists(file_path):
        return f"File not found: {file_path}"

    if not output_path:
        base, ext = os.path.splitext(file_path)
        output_path = f"{base}_rendered{ext}"

    try:
        with zipfile.ZipFile(file_path, 'r') as z:
            doc_xml = z.read('word/document.xml').decode('utf-8', errors='ignore')

        # Find all {{zotero:KEY}} markers
        keys = re.findall(r'\{\{zotero:([^}]+)\}\}', doc_xml)
        if not keys:
            return "No {{zotero:KEY}} placeholders found."

        # Render each citation
        replacements = {}
        async with httpx.AsyncClient(timeout=10.0) as http:
            for key in set(keys):
                url = f"http://127.0.0.1:23119/api/users/0/items/{key}?format=citation&style={style}"
                resp = await http.get(url)
                if resp.status_code == 200:
                    citation = re.sub(r'<[^>]+>', '', resp.text).strip()
                    replacements[key] = citation
                else:
                    replacements[key] = f"[{key}]"

        # Replace placeholders
        for key, citation in replacements.items():
            doc_xml = doc_xml.replace(f"{{{{zotero:{key}}}}}", citation)

        # Rebuild DOCX (replace word/document.xml, not append duplicate)
        import tempfile as _tmp
        tmp_path = _tmp.mktemp(suffix='.docx')
        with zipfile.ZipFile(file_path, 'r') as zin, zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zout:
            for item_info in zin.infolist():
                if item_info.filename == 'word/document.xml':
                    zout.writestr(item_info, doc_xml.encode('utf-8'))
                else:
                    zout.writestr(item_info, zin.read(item_info.filename))
        shutil.move(tmp_path, output_path)

        return f"Rendered {len(replacements)} citations in `{output_path}` using {style} style."
    except Exception as e:
        return f"Failed to render citations: {e}"




# =============================================================================
# Batch PDF Export Tool
# =============================================================================


@mcp.tool(
    name="zotero_export_pdfs",
    description="Export PDFs for multiple items to a target folder. Resolves linked/stored attachment paths.",
)
async def export_pdfs(
    item_keys: list[str],
    target_folder: str,
    *,
    ctx: Context,
) -> str:
    """
    Export PDFs for a list of items to a folder.

    Args:
        item_keys: List of Zotero item keys.
        target_folder: Absolute path to target folder.
        ctx: MCP context.
    """
    import os, shutil
    from zotmcp.utils import get_zotero_base_attachment_path

    ctx.info(f"Exporting PDFs for {len(item_keys)} items to {target_folder}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    os.makedirs(target_folder, exist_ok=True)
    linked_base = get_zotero_base_attachment_path()

    copied = []
    missing = []
    errors = []

    for key in item_keys:
        children = await client.get_item_children(key)
        pdf_found = False
        for child in children:
            raw = (child.raw_data or {}).get("data", {})
            content_type = raw.get("contentType", "")
            if "pdf" not in content_type.lower() and "pdf" not in (child.title or "").lower():
                continue

            path = raw.get("path", "")
            if not path:
                continue

            # Resolve attachments: prefix
            if path.startswith("attachments:") and linked_base:
                rel = path.replace("attachments:", "", 1)
                full_path = os.path.join(linked_base, rel)
            elif os.path.isabs(path):
                full_path = path
            else:
                continue

            if os.path.exists(full_path):
                # Copy with safe filename
                item = await client.get_item(key)
                safe_name = "".join(c if c.isalnum() or c in " -_." else "_" for c in (item.title if item else key)[:60])
                dest = os.path.join(target_folder, f"{safe_name}.pdf")
                # Avoid overwrite
                if os.path.exists(dest):
                    dest = os.path.join(target_folder, f"{safe_name}_{key}.pdf")
                shutil.copy2(full_path, dest)
                copied.append(f"`{key}`: {os.path.basename(dest)}")
                pdf_found = True
                break
            else:
                errors.append(f"`{key}`: path not found: {full_path}")
                pdf_found = True
                break

        if not pdf_found:
            missing.append(f"`{key}`")

    lines = [f"## PDF Export Summary\n"]
    lines.append(f"**Copied:** {len(copied)}/{len(item_keys)}")
    if copied:
        lines.append("\n".join(f"  - {c}" for c in copied))
    if missing:
        lines.append(f"\n**No PDF attachment:** {len(missing)}")
        lines.append("\n".join(f"  - {m}" for m in missing))
    if errors:
        lines.append(f"\n**Errors:** {len(errors)}")
        lines.append("\n".join(f"  - {e}" for e in errors))
    lines.append(f"\nTarget: `{target_folder}`")
    return "\n".join(lines)


# =============================================================================
# Attachment Path Tool
# =============================================================================


@mcp.tool(
    name="zotero_get_attachment_path",
    description="Get the full filesystem path for a Zotero item's PDF attachment. Resolves linked and stored attachment paths.",
)
async def get_attachment_path(
    item_key: str,
    *,
    ctx: Context,
) -> str:
    """
    Get attachment path for an item.

    Args:
        item_key: Zotero item key (parent item or attachment key).
        ctx: MCP context.
    """
    import os
    from zotmcp.utils import get_zotero_base_attachment_path

    ctx.info(f"Getting attachment path for {item_key}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    linked_base = get_zotero_base_attachment_path()

    # Check if key is itself an attachment
    item = await client.get_item(item_key)
    if not item:
        return f"Item `{item_key}` not found."

    raw = (item.raw_data or {}).get("data", {})

    # If this is an attachment item, get its path directly
    if raw.get("itemType") == "attachment":
        children_to_check = [item]
    else:
        children_to_check = await client.get_item_children(item_key)

    results = []
    for child in children_to_check:
        child_raw = (child.raw_data or {}).get("data", {})
        content_type = child_raw.get("contentType", "")
        path = child_raw.get("path", "")
        link_mode = child_raw.get("linkMode", "")
        title = child.title or "untitled"

        if not path:
            continue

        # Resolve path
        if path.startswith("attachments:") and linked_base:
            rel = path.replace("attachments:", "", 1)
            full_path = os.path.join(linked_base, rel)
            zotero_path = path
        elif os.path.isabs(path):
            full_path = path
            zotero_path = path
        else:
            full_path = path
            zotero_path = path

        exists = os.path.exists(full_path)
        size = ""
        if exists:
            size_bytes = os.path.getsize(full_path)
            size = f" ({size_bytes // 1024} KB)"

        results.append(
            f"- **{title}** ({content_type}, {link_mode})\n"
            f"  Zotero path: `{zotero_path}`\n"
            f"  Full path: `{full_path}`\n"
            f"  Exists: {'yes' + size if exists else 'NO'}"
        )

    if not results:
        return f"No attachments found for `{item_key}`."

    return f"## Attachments for `{item_key}`\n\n" + "\n".join(results)


# =============================================================================
# Collection Export Tool
# =============================================================================


@mcp.tool(
    name="zotero_export_collection",
    description="Export all PDFs in a collection to a target folder with an index file.",
)
async def export_collection(
    collection_key: str,
    target_folder: str,
    flatten: bool = True,
    *,
    ctx: Context,
) -> str:
    """
    Export all PDFs in a collection.

    Args:
        collection_key: Zotero collection key.
        target_folder: Target folder path.
        flatten: If True, all PDFs in one folder. If False, per-item subfolders.
        ctx: MCP context.
    """
    import os, shutil
    from zotmcp.utils import get_zotero_base_attachment_path

    ctx.info(f"Exporting collection {collection_key} to {target_folder}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    items = await client.get_collection_items(collection_key, limit=500)
    if not items:
        return f"Collection `{collection_key}` is empty or not found."

    os.makedirs(target_folder, exist_ok=True)
    linked_base = get_zotero_base_attachment_path()

    copied = 0
    no_pdf = 0
    index_lines = ["# Collection Export Index\n"]

    for item in items:
        children = await client.get_item_children(item.key)
        pdf_path = None

        for child in children:
            child_raw = (child.raw_data or {}).get("data", {})
            if "pdf" not in child_raw.get("contentType", "").lower():
                continue
            path = child_raw.get("path", "")
            if path.startswith("attachments:") and linked_base:
                full = os.path.join(linked_base, path.replace("attachments:", "", 1))
            elif os.path.isabs(path):
                full = path
            else:
                continue
            if os.path.exists(full):
                pdf_path = full
                break

        safe_name = "".join(c if c.isalnum() or c in " -_." else "_" for c in item.title[:60])

        if pdf_path:
            if flatten:
                dest = os.path.join(target_folder, f"{safe_name}.pdf")
                if os.path.exists(dest):
                    dest = os.path.join(target_folder, f"{safe_name}_{item.key}.pdf")
            else:
                item_dir = os.path.join(target_folder, safe_name)
                os.makedirs(item_dir, exist_ok=True)
                dest = os.path.join(item_dir, os.path.basename(pdf_path))

            shutil.copy2(pdf_path, dest)
            copied += 1
            index_lines.append(f"- [{item.title[:80]}]({os.path.basename(dest)}) — `{item.key}`")
        else:
            no_pdf += 1
            index_lines.append(f"- {item.title[:80]} — `{item.key}` (no PDF)")

    # Write index
    index_path = os.path.join(target_folder, "INDEX.md")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("\n".join(index_lines))

    return f"Exported {copied}/{len(items)} PDFs to `{target_folder}` ({no_pdf} without PDF).\nIndex: `{index_path}`"


# =============================================================================
# Tag Rename Tool
# =============================================================================


@mcp.tool(
    name="zotero_rename_tag",
    description="Rename a tag across all items that use it. Adds the new tag and removes the old one.",
)
async def rename_tag(
    old_tag: str,
    new_tag: str,
    *,
    ctx: Context,
) -> str:
    """
    Rename a tag library-wide.

    Args:
        old_tag: Current tag name.
        new_tag: New tag name.
        ctx: MCP context.
    """
    ctx.info(f"Renaming tag '{old_tag}' -> '{new_tag}'")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

    # Search for items with the old tag
    items = await client.search_items("", limit=500, tags=[old_tag])
    if not items:
        return f"No items found with tag '{old_tag}'."

    updated = 0
    failed = 0
    for item in items:
        success = await client.update_item_tags(item.key, add_tags=[new_tag], remove_tags=[old_tag])
        if success:
            updated += 1
        else:
            failed += 1

    result = f"Renamed tag '{old_tag}' -> '{new_tag}' on {updated} items."
    if failed:
        result += f" ({failed} failed)"
    return result



def create_server() -> FastMCP:
    """Create and return the MCP server instance."""
    return mcp
