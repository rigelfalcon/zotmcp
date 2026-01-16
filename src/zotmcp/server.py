"""
MCP Server implementation with all Zotero tools.
"""

import json
import logging
import sys
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Literal, Optional

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
            sys.stderr.write("Semantic search engine initialized.
")
        except Exception as e:
            sys.stderr.write(f"Warning: Failed to initialize semantic engine: {e}
")

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


def create_server() -> FastMCP:
    """Create and return the MCP server instance."""
    return mcp
