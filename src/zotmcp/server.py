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
            await semantic_engine.initialize()
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

        lines = [f"@{bibtex_type}{{{item.key},"]
        lines.append(f"  title = {{{item.title}}},")
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
    ctx.info(f"Adding web page: {url}")
    client = get_client()

    if not await client.is_available():
        return "Error: Zotero is not available."

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


def create_server() -> FastMCP:
    """Create and return the MCP server instance."""
    return mcp
