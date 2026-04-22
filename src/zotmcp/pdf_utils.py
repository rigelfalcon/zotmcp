"""PDF utilities for ZotMCP using PyMuPDF (fitz)."""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF

    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False


def _require_pymupdf() -> None:
    """Raise ImportError if PyMuPDF is not installed."""
    if not HAS_PYMUPDF:
        raise ImportError(
            "PyMuPDF is required for PDF operations. "
            "Install with: pip install 'zotmcp[pdf]'"
        )


def extract_pdf_outline(pdf_bytes: bytes) -> list[dict]:
    """Extract table of contents from a PDF.

    Args:
        pdf_bytes: Raw PDF file content.

    Returns:
        List of dicts with keys: level, title, page.
        Empty list if no outline or on error.
    """
    _require_pymupdf()
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        toc = doc.get_toc()  # [[level, title, page], ...]
        doc.close()
        return [{"level": entry[0], "title": entry[1], "page": entry[2]} for entry in toc]
    except Exception as e:
        logger.error(f"Failed to extract PDF outline: {e}")
        return []


def find_text_position(pdf_bytes: bytes, page: int, text: str) -> Optional[dict]:
    """Find the bounding box of text on a specific page.

    Used for creating highlight annotations at the correct position.

    Args:
        pdf_bytes: Raw PDF file content.
        page: 0-based page index.
        text: Text to search for.

    Returns:
        Dict with position info: pageIndex, rects (list of [x0, y0, x1, y1]),
        or None if not found.
    """
    _require_pymupdf()
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if page < 0 or page >= len(doc):
            doc.close()
            return None

        pg = doc[page]
        instances = pg.search_for(text)
        doc.close()

        if not instances:
            return None

        # Convert fitz.Rect to Zotero-compatible position format
        # Zotero uses PDF coordinate system (origin at bottom-left)
        page_height = pg.rect.height
        rects = []
        for rect in instances:
            rects.append([
                round(rect.x0, 2),
                round(page_height - rect.y1, 2),  # flip y
                round(rect.x1, 2),
                round(page_height - rect.y0, 2),  # flip y
            ])

        return {"pageIndex": page, "rects": rects}
    except Exception as e:
        logger.error(f"Failed to find text position: {e}")
        return None


def build_area_position(
    page: int, x: float, y: float, w: float, h: float, pdf_bytes: bytes
) -> Optional[dict]:
    """Build a Zotero annotation position dict for an area (image) annotation.

    Coordinates are in PDF points from top-left of page.

    Args:
        page: 0-based page index.
        x: Left edge in PDF points.
        y: Top edge in PDF points.
        w: Width in PDF points.
        h: Height in PDF points.
        pdf_bytes: Raw PDF for page dimension lookup.

    Returns:
        Zotero-compatible position dict, or None on error.
    """
    _require_pymupdf()
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if page < 0 or page >= len(doc):
            doc.close()
            return None

        pg = doc[page]
        page_height = pg.rect.height
        doc.close()

        # Convert top-left origin to PDF bottom-left origin
        return {
            "pageIndex": page,
            "rects": [[
                round(x, 2),
                round(page_height - y - h, 2),
                round(x + w, 2),
                round(page_height - y, 2),
            ]],
        }
    except Exception as e:
        logger.error(f"Failed to build area position: {e}")
        return None


# DOI regex pattern: 10.XXXX/... (standard DOI format)
_DOI_PATTERN = re.compile(r"\b(10\.\d{4,}/[^\s\"'<>\]]+)")


def extract_doi_from_pdf(pdf_bytes: bytes) -> Optional[str]:
    """Extract DOI from PDF metadata or first 2 pages of text.

    Checks PDF metadata first (faster), then falls back to text extraction.

    Args:
        pdf_bytes: Raw PDF file content.

    Returns:
        DOI string (e.g. '10.1234/example') or None if not found.
    """
    _require_pymupdf()
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        # Check metadata first
        metadata = doc.metadata or {}
        for field in ("subject", "keywords", "title"):
            value = metadata.get(field, "") or ""
            match = _DOI_PATTERN.search(value)
            if match:
                doc.close()
                return _clean_doi(match.group(1))

        # Check first 2 pages
        for page_idx in range(min(2, len(doc))):
            text = doc[page_idx].get_text()
            match = _DOI_PATTERN.search(text)
            if match:
                doc.close()
                return _clean_doi(match.group(1))

        doc.close()
        return None
    except Exception as e:
        logger.error(f"Failed to extract DOI from PDF: {e}")
        return None


def _clean_doi(doi: str) -> str:
    """Strip trailing punctuation from a captured DOI."""
    return doi.rstrip(".,;:)")
