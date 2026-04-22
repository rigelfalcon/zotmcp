"""CrossRef API client for DOI metadata lookup."""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

CROSSREF_API = "https://api.crossref.org/works"

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


async def fetch_crossref_metadata(doi: str) -> Optional[dict]:
    """Fetch metadata from CrossRef and convert to Zotero item format.

    Args:
        doi: DOI string (e.g. '10.1234/example').

    Returns:
        Zotero-compatible item data dict, or None on failure.
    """
    url = f"{CROSSREF_API}/{doi}"
    headers = {
        "User-Agent": "ZotMCP/0.1.0 (https://github.com/user/zotmcp; mailto:zotmcp@example.com)",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPError as e:
        logger.error(f"CrossRef API request failed for DOI {doi}: {e}")
        return None

    message = data.get("message", {})
    if not message:
        return None

    # Map to Zotero item
    cr_type = message.get("type", "journal-article")
    item_type = CROSSREF_TYPE_MAP.get(cr_type, "journalArticle")

    # Extract title
    titles = message.get("title", [])
    title = titles[0] if titles else "Untitled"

    # Extract creators
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

    # Extract date
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

    # Build item
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

    # Type-specific fields
    container = message.get("container-title", [])
    if item_type == "journalArticle":
        item["publicationTitle"] = container[0] if container else ""
        item["volume"] = message.get("volume", "")
        item["issue"] = message.get("issue", "")
        item["pages"] = message.get("page", "")
        item["ISSN"] = (message.get("ISSN") or [""])[0]
    elif item_type in ("conferencePaper", "bookSection"):
        event = message.get("event", {})
        item["proceedingsTitle"] = container[0] if container else ""
        if event:
            item["conferenceName"] = event.get("name", "")
        item["pages"] = message.get("page", "")
    elif item_type == "book":
        item["publisher"] = message.get("publisher", "")
        isbn = message.get("ISBN") or []
        item["ISBN"] = isbn[0] if isbn else ""

    return item
