"""Export tools for ZotMCP.

Sections: Batch BibTeX Export Tool, Citation Rendering Tools, DOCX Citation Tools
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
    """Register export tools on the MCP server."""

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





