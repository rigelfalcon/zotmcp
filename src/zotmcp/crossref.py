"""CrossRef API client for DOI metadata lookup with preprint fallback."""

import logging
import re
from typing import Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

CROSSREF_API = "https://api.crossref.org/works"

HEADERS = {
    "User-Agent": "ZotMCP/0.1.0 (https://github.com/user/zotmcp; mailto:zotmcp@example.com)",
}

# CrossRef type -> Zotero itemType mapping
CROSSREF_TYPE_MAP = {
    "journal-article": "journalArticle",
    "proceedings-article": "conferencePaper",
    "book-chapter": "bookSection",
    "book": "book",
    "edited-book": "book",
    "monograph": "book",
    "report": "report",
    "dataset": "document",
    "posted-content": "preprint",
    "peer-review": "journalArticle",
    "dissertation": "thesis",
    "reference-entry": "encyclopediaArticle",
}

# Preprint DOI prefixes
BIORXIV_PREFIX = "10.1101/"
ARXIV_PREFIX = "10.48550/"


def _extract_arxiv_id(doi: str) -> Optional[str]:
    """Extract arXiv ID from DOI like 10.48550/arXiv.2301.12345."""
    m = re.search(r'arXiv\.(\d+\.\d+)', doi, re.IGNORECASE)
    return m.group(1) if m else None


def _crossref_message_to_item(message: dict, doi: str) -> dict:
    """Convert CrossRef message to Zotero item dict."""
    cr_type = message.get("type", "journal-article")
    item_type = CROSSREF_TYPE_MAP.get(cr_type, "journalArticle")

    titles = message.get("title", [])
    title = titles[0] if titles else "Untitled"

    creators = []
    for author in message.get("author", []):
        creator = {"creatorType": "author"}
        if "family" in author:
            creator["lastName"] = author["family"]
            creator["firstName"] = author.get("given", "")
        elif "name" in author:
            creator["name"] = author["name"]
        else:
            continue
        creators.append(creator)

    date_parts = None
    for date_field in ("published-print", "published-online", "issued", "created"):
        dp = message.get(date_field, {}).get("date-parts", [[]])
        if dp and dp[0]:
            date_parts = dp[0]
            break

    date_str = ""
    if date_parts:
        parts = [str(p) for p in date_parts if p]
        date_str = "-".join(parts)

    item = {
        "itemType": item_type,
        "title": title,
        "creators": creators,
        "date": date_str,
        "DOI": doi,
        "abstractNote": message.get("abstract", ""),
        "url": message.get("URL", ""),
        "tags": [],
    }

    container = message.get("container-title", [])
    if item_type == "journalArticle":
        item["publicationTitle"] = container[0] if container else ""
        item["volume"] = message.get("volume", "")
        item["issue"] = message.get("issue", "")
        item["pages"] = message.get("page", "")
        item["ISSN"] = (message.get("ISSN") or [""])[0]
    elif item_type == "conferencePaper":
        event = message.get("event", {})
        item["proceedingsTitle"] = container[0] if container else ""
        if event:
            item["conferenceName"] = event.get("name", "")
        item["pages"] = message.get("page", "")
    elif item_type == "bookSection":
        item["bookTitle"] = container[0] if container else ""
        item["publisher"] = message.get("publisher", "")
        item["pages"] = message.get("page", "")
        isbn = message.get("ISBN") or []
        item["ISBN"] = isbn[0] if isbn else ""
    elif item_type == "book":
        item["publisher"] = message.get("publisher", "")
        isbn = message.get("ISBN") or []
        item["ISBN"] = isbn[0] if isbn else ""

    return item


async def _fetch_biorxiv_metadata(doi: str) -> Optional[dict]:
    """Fetch metadata from bioRxiv/medRxiv API."""
    # bioRxiv API expects just the numeric DOI suffix, e.g. 2024.01.15.575712
    # not the full 10.1101/2024.01.15.575712
    short_doi = doi.replace("10.1101/", "")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            api_url = f"https://api.biorxiv.org/details/biorxiv/{short_doi}"
            resp = await client.get(api_url, headers=HEADERS)
            if resp.status_code == 404:
                # Try medRxiv
                api_url = f"https://api.biorxiv.org/details/medrxiv/{short_doi}"
                resp = await client.get(api_url, headers=HEADERS)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        logger.error(f"bioRxiv API failed for {doi}: {e}")
        return None

    collection = data.get("collection", [])
    if not collection:
        return None

    paper = collection[0]
    authors_str = paper.get("authors", "")
    creators = []
    for name in authors_str.split(";"):
        name = name.strip()
        if not name:
            continue
        parts = name.rsplit(" ", 1)
        if len(parts) == 2:
            creators.append({"creatorType": "author", "firstName": parts[0], "lastName": parts[1]})
        else:
            creators.append({"creatorType": "author", "name": name})

    return {
        "itemType": "preprint",
        "title": paper.get("title", "Untitled"),
        "creators": creators,
        "date": paper.get("date", ""),
        "DOI": doi,
        "abstractNote": paper.get("abstract", ""),
        "url": f"https://doi.org/{doi}",
        "repository": paper.get("server", "bioRxiv"),
        "tags": [],
    }


async def _fetch_arxiv_metadata(arxiv_id: str, doi: str) -> Optional[dict]:
    """Fetch metadata from arXiv API."""
    api_url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(api_url, headers=HEADERS)
            resp.raise_for_status()
            xml_text = resp.text
    except httpx.HTTPError as e:
        logger.error(f"arXiv API failed for {arxiv_id}: {e}")
        return None

    # Parse title from <entry><title> (skip <feed><title> which is "ArXiv Query:...")
    titles = re.findall(r"<title[^>]*>(.+?)</title>", xml_text, re.DOTALL)
    # First title is feed title, second is paper title
    title = "Untitled"
    for t in titles:
        t_clean = t.strip().replace("\n", " ")
        if not t_clean.lower().startswith("arxiv"):
            title = t_clean
            break

    creators = []
    for m in re.finditer(r"<name>(.+?)</name>", xml_text):
        name = m.group(1).strip()
        parts = name.rsplit(" ", 1)
        if len(parts) == 2:
            creators.append({"creatorType": "author", "firstName": parts[0], "lastName": parts[1]})
        else:
            creators.append({"creatorType": "author", "name": name})

    published_m = re.search(r"<published>(\d{4}-\d{2}-\d{2})", xml_text)
    date_str = published_m.group(1) if published_m else ""

    abstract_m = re.search(r"<summary[^>]*>(.+?)</summary>", xml_text, re.DOTALL)
    abstract = abstract_m.group(1).strip().replace("\n", " ") if abstract_m else ""

    return {
        "itemType": "preprint",
        "title": title,
        "creators": creators,
        "date": date_str,
        "DOI": doi,
        "abstractNote": abstract,
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "repository": "arXiv",
        "archiveID": f"arXiv:{arxiv_id}",
        "tags": [],
    }


async def _crossref_title_search(title: str) -> Optional[dict]:
    """Search CrossRef by title as fallback when DOI lookup fails."""
    params = {"query": title, "rows": "3"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(CROSSREF_API, params=params, headers=HEADERS)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        logger.error(f"CrossRef title search failed for '{title}': {e}")
        return None

    items = data.get("message", {}).get("items", [])
    if not items:
        return None

    # Return the first result
    return items[0]


async def fetch_crossref_metadata(doi: str, title_hint: str = "") -> Optional[dict]:
    """Fetch metadata from CrossRef and convert to Zotero item format.

    Enhanced with:
    - URL-encoded DOI for old-format DOIs with special chars
    - Preprint DOI detection (bioRxiv, arXiv)
    - Title-based fallback search if DOI lookup fails

    Args:
        doi: DOI string (e.g. '10.1234/example').
        title_hint: Optional title for fallback search.

    Returns:
        Zotero-compatible item data dict, or None on failure.
    """
    # Check for preprint DOIs first
    if doi.startswith(BIORXIV_PREFIX):
        result = await _fetch_biorxiv_metadata(doi)
        if result:
            return result
        logger.info(f"bioRxiv API returned nothing for {doi}, trying CrossRef")

    if doi.startswith(ARXIV_PREFIX):
        arxiv_id = _extract_arxiv_id(doi)
        if arxiv_id:
            result = await _fetch_arxiv_metadata(arxiv_id, doi)
            if result:
                return result
        logger.info(f"arXiv API returned nothing for {doi}, trying CrossRef")

    # URL-encode DOI for CrossRef (handles parentheses, brackets in old DOIs)
    encoded_doi = quote(doi, safe="/")
    url = f"{CROSSREF_API}/{encoded_doi}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=HEADERS)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.warning(f"DOI not found in CrossRef: {doi}")
            # Fallback: title search
            if title_hint:
                logger.info(f"Trying title search: {title_hint}")
                message = await _crossref_title_search(title_hint)
                if message:
                    found_doi = message.get("DOI", "")
                    logger.info(f"Title search found DOI: {found_doi}")
                    return _crossref_message_to_item(message, found_doi or doi)
            return None
        logger.error(f"CrossRef API request failed for DOI {doi}: {e}")
        return None
    except httpx.HTTPError as e:
        logger.error(f"CrossRef API request failed for DOI {doi}: {e}")
        return None

    message = data.get("message", {})
    if not message:
        return None

    return _crossref_message_to_item(message, doi)
