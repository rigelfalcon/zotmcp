"""Collections tools for ZotMCP.

Sections: Collection Tools, Organization Tools, Collection Management Tools, Collection Stats Tool, Remove from Collection Tool, Collection Export Tool
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
    """Register collections tools on the MCP server."""

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



