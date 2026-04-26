"""ZotMCP tool modules."""

MODULES = [
    "search",
    "items",
    "collections",
    "notes",
    "tags",
    "pdf",
    "export",
    "metrics",
]


def register_all_tools(mcp, get_client, format_item_markdown=None,
                       get_semantic_engine=None,
                       ensure_semantic_engine_initialized=None):
    """Register all tool modules."""
    from importlib import import_module
    for name in MODULES:
        mod = import_module(f"zotmcp.tools.{name}")
        mod.register(mcp, get_client, format_item_markdown,
                     get_semantic_engine, ensure_semantic_engine_initialized)
