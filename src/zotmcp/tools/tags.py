"""Tags tools for ZotMCP.

Sections: Tag Tools, Tag Rename Tool
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
    """Register tags tools on the MCP server."""

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




