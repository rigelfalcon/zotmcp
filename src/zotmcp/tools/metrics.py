"""Metrics tools for ZotMCP.

Sections: Status Tools, Duplicate Management Tools, Library Stats Tool, Sync Tool, iCite Citation Metrics Tool
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
    """Register metrics tools on the MCP server."""

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
        from zotmcp.config import load_config
        config = load_config()

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



