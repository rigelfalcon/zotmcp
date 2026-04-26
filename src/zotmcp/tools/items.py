"""Items tools for ZotMCP.

Sections: Read Tools, DOI/URL Import Tools, Item Update Tool, PMID Import Tool, Trash List / Restore Tools, Preprint Update Check Tool
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
    """Register items tools on the MCP server."""

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



