"""Notes tools for ZotMCP.

Sections: Note Tools, Notes Tools Group 1, Annotations Tools Group 2
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
    """Register notes tools on the MCP server."""

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



