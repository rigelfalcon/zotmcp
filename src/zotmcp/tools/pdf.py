"""Pdf tools for ZotMCP.

Sections: Everything Integration Tools, Remote File Transfer Tools, PDF Outline + Citation Key Tools, PDF Fetch Tool, Batch PDF Export Tool, Attachment Path Tool
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
    """Register pdf tools on the MCP server."""

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



