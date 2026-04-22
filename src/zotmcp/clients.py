"""
Zotero client adapters - unified interface to different Zotero backends.
"""

import asyncio
import json
import logging
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
import os
from zotmcp.utils import get_zotero_base_attachment_path, strip_html

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ZoteroItem:
    """Unified Zotero item representation."""

    key: str
    item_type: str
    title: str
    creators: list[dict[str, str]]
    date: Optional[str] = None
    abstract: Optional[str] = None
    tags: list[str] = None
    collections: list[str] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    extra: Optional[str] = None
    date_added: Optional[str] = None
    date_modified: Optional[str] = None
    raw_data: Optional[dict] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.collections is None:
            self.collections = []

    @classmethod
    def from_api_response(cls, data: dict) -> "ZoteroItem":
        """Create from Zotero API response."""
        item_data = data.get("data", data)
        return cls(
            key=data.get("key", item_data.get("key", "")),
            item_type=item_data.get("itemType", ""),
            title=item_data.get("title", "Untitled"),
            creators=item_data.get("creators", []),
            date=item_data.get("date"),
            abstract=item_data.get("abstractNote"),
            tags=[t.get("tag", "") for t in item_data.get("tags", [])],
            collections=item_data.get("collections", []),
            doi=item_data.get("DOI"),
            url=item_data.get("url"),
            extra=item_data.get("extra"),
            date_added=item_data.get("dateAdded"),
            date_modified=item_data.get("dateModified"),
            raw_data=data,
        )

    def format_creators(self) -> str:
        """Format creators as string."""
        parts = []
        for creator in self.creators:
            if "name" in creator:
                parts.append(creator["name"])
            elif "lastName" in creator:
                name = creator.get("lastName", "")
                if "firstName" in creator:
                    name = f"{creator['firstName']} {name}"
                parts.append(name)
        return ", ".join(parts) if parts else "Unknown"


@dataclass
class ZoteroCollection:
    """Zotero collection representation."""

    key: str
    name: str
    parent_key: Optional[str] = None
    item_count: int = 0


class ZoteroClientBase(ABC):
    """Abstract base class for Zotero clients."""

    @abstractmethod
    async def is_available(self) -> bool:
        """Check if the backend is available."""
        pass

    @abstractmethod
    async def search_items(
        self,
        query: str,
        limit: int = 10,
        item_type: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> list[ZoteroItem]:
        """Search for items."""
        pass

    @abstractmethod
    async def get_item(self, key: str) -> Optional[ZoteroItem]:
        """Get a single item by key."""
        pass

    @abstractmethod
    async def get_item_fulltext(self, key: str) -> Optional[str]:
        """Get full text content of an item."""
        pass

    @abstractmethod
    async def get_collections(self) -> list[ZoteroCollection]:
        """Get all collections."""
        pass

    @abstractmethod
    async def get_collection_items(
        self, collection_key: str, limit: int = 50
    ) -> list[ZoteroItem]:
        """Get items in a collection."""
        pass

    @abstractmethod
    async def get_tags(self) -> list[str]:
        """Get all tags."""
        pass

    @abstractmethod
    async def update_item_tags(
        self, key: str, add_tags: list[str] = None, remove_tags: list[str] = None
    ) -> bool:
        """Update tags on an item."""
        pass

    @abstractmethod
    async def create_note(
        self, parent_key: str, content: str, tags: list[str] = None
    ) -> Optional[str]:
        """Create a note attached to an item."""
        pass

    @abstractmethod
    async def move_item_to_collection(self, item_key: str, collection_key: str) -> bool:
        """Move an item to a collection."""
        pass

    @abstractmethod
    async def get_item_children(self, item_key: str) -> list[ZoteroItem]:
        """Get children (attachments, notes) of an item."""
        pass

    @abstractmethod
    async def create_collection(self, name: str, parent_key: Optional[str] = None) -> Optional[str]:
        """Create a new collection. Returns collection key if successful."""
        pass

    @abstractmethod
    async def delete_collection(self, collection_key: str) -> bool:
        """Delete a collection."""
        pass

    @abstractmethod
    async def rename_collection(self, collection_key: str, new_name: str) -> bool:
        """Rename a collection."""
        pass

    @abstractmethod
    async def batch_move_to_collection(self, item_keys: list[str], collection_key: str) -> dict[str, bool]:
        """Move multiple items to a collection. Returns dict of item_key -> success."""
        pass

    # --- New methods for notes, annotations, import, duplicates ---

    @abstractmethod
    async def get_notes(
        self, item_key: Optional[str] = None, limit: int = 50
    ) -> list["ZoteroItem"]:
        """Get notes, optionally filtered by parent item."""
        pass

    @abstractmethod
    async def update_note(
        self, note_key: str, content: str, append: bool = True
    ) -> bool:
        """Update note content. If append=True, append to existing; otherwise replace."""
        pass

    @abstractmethod
    async def trash_item(self, key: str) -> bool:
        """Move item to trash."""
        pass

    @abstractmethod
    async def download_attachment(self, key: str) -> Optional[bytes]:
        """Download attachment file content as bytes."""
        pass

    @abstractmethod
    async def create_item_raw(self, item_data: dict) -> Optional[str]:
        """Create a Zotero item from raw data dict. Returns item key or None."""
        pass

    @abstractmethod
    async def get_all_items(
        self, limit: int = 50, item_type: Optional[str] = None
    ) -> list["ZoteroItem"]:
        """Get all items (with optional type filter), not via search."""
        pass


@dataclass
class ZoteroGroup:
    """Zotero group library representation."""

    id: int
    name: str
    description: str = ""
    item_count: int = 0


class ZoteroLocalClient(ZoteroClientBase):
    """Client for Zotero Local API (port 23119)."""

    def __init__(self, host: str = "127.0.0.1", port: int = 23119, linked_base: Optional[str] = None, search_groups: bool = True):
        self.base_url = f"http://{host}:{port}"
        self._client: Optional[httpx.AsyncClient] = None
        # Auto-detect linked attachment base if not provided
        self._linked_base = linked_base or get_zotero_base_attachment_path()
        # Whether to include group libraries in searches
        self._search_groups = search_groups
        # Cache for groups
        self._groups_cache: Optional[list[ZoteroGroup]] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def _request(
        self, method: str, endpoint: str, **kwargs
    ) -> Optional[dict | list]:
        """Make a request to the local API."""
        client = await self._get_client()
        url = f"{self.base_url}{endpoint}"
        try:
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Local API request failed: {e}")
            return None

    async def is_available(self) -> bool:
        """Check if Zotero is running."""
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.base_url}/connector/ping", timeout=5.0
            )
            return response.status_code == 200
        except Exception:
            return False

    async def get_groups(self) -> list[ZoteroGroup]:
        """Get all group libraries."""
        if self._groups_cache is not None:
            return self._groups_cache
        
        result = await self._request("GET", "/api/users/0/groups")
        if not result:
            return []
        
        groups = []
        for g in result:
            data = g.get("data", {})
            groups.append(ZoteroGroup(
                id=g.get("id", 0),
                name=data.get("name", ""),
                description=data.get("description", ""),
                item_count=g.get("meta", {}).get("numItems", 0),
            ))
        
        self._groups_cache = groups
        return groups

    async def search_items(
        self,
        query: str,
        limit: int = 10,
        item_type: Optional[str] = None,
        tags: Optional[list[str]] = None,
        include_groups: Optional[bool] = None,
    ) -> list[ZoteroItem]:
        """Search items via local API.
        
        Args:
            query: Search query string
            limit: Maximum number of results
            item_type: Filter by item type
            tags: Filter by tags
            include_groups: Whether to search group libraries (default: self._search_groups)
        """
        params = {"q": query, "limit": limit}
        if item_type:
            params["itemType"] = item_type

        items = []
        seen_keys = set()
        
        # Search personal library
        result = await self._request("GET", "/api/users/0/items", params=params)
        if result:
            for item_data in result:
                if item_data.get("data", {}).get("itemType") != "attachment":
                    item = ZoteroItem.from_api_response(item_data)
                    if item.key not in seen_keys:
                        items.append(item)
                        seen_keys.add(item.key)

        # Search group libraries if enabled
        should_search_groups = include_groups if include_groups is not None else self._search_groups
        if should_search_groups and len(items) < limit:
            groups = await self.get_groups()
            for group in groups:
                if len(items) >= limit:
                    break
                group_result = await self._request(
                    "GET", 
                    f"/api/groups/{group.id}/items", 
                    params=params
                )
                if group_result:
                    for item_data in group_result:
                        if item_data.get("data", {}).get("itemType") != "attachment":
                            item = ZoteroItem.from_api_response(item_data)
                            # Add group info to item
                            item.extra = f"[Group: {group.name}] " + (item.extra or "")
                            if item.key not in seen_keys:
                                items.append(item)
                                seen_keys.add(item.key)
                                if len(items) >= limit:
                                    break

        # Filter by tags if specified
        if tags:
            items = [
                item for item in items if any(tag in item.tags for tag in tags)
            ]

        return items[:limit]

    async def get_item(self, key: str) -> Optional[ZoteroItem]:
        """Get item by key."""
        result = await self._request("GET", f"/api/users/0/items/{key}")
        if result:
            return ZoteroItem.from_api_response(result)
        return None

    async def get_item_fulltext(self, key: str) -> Optional[str]:
        """Get full text of an item.
        
        First tries Zotero's fulltext API. If that fails and linked_base is provided,
        attempts to read linked PDF files directly.
        """
        # Try Zotero's fulltext API first
        result = await self._request("GET", f"/api/users/0/items/{key}/fulltext")
        if result and "content" in result:
            return result["content"]
        
        # If no fulltext, check for linked PDF attachments
        if self._linked_base:
            return await self._get_linked_pdf_text(key, self._linked_base)
        
        return None
    
    async def _get_linked_pdf_text(self, key: str, linked_base: str) -> Optional[str]:
        """Extract text from linked PDF attachment."""
        # Get item's children (attachments)
        children = await self._request("GET", f"/api/users/0/items/{key}/children")
        if not children:
            return None
        
        for child in children:
            data = child.get("data", {})
            if (data.get("linkMode") == "linked_file" and 
                data.get("contentType") == "application/pdf"):
                path = data.get("path", "")
                if path.startswith("attachments:"):
                    # Remove prefix and join with base
                    rel_path = path[12:]  # Remove "attachments:"
                    import os
                    full_path = os.path.join(linked_base, rel_path)
                    
                    if os.path.exists(full_path):
                        try:
                            import pymupdf
                            doc = pymupdf.open(full_path)
                            text = ""
                            for page in doc:
                                text += page.get_text()
                            doc.close()
                            return text if text.strip() else None
                        except Exception as e:
                            logger.warning(f"Failed to extract PDF text: {e}")
        return None

    async def get_collections(self) -> list[ZoteroCollection]:
        """Get all collections."""
        result = await self._request("GET", "/api/users/0/collections")
        if not result:
            return []

        collections = []
        for coll_data in result:
            data = coll_data.get("data", {})
            collections.append(
                ZoteroCollection(
                    key=coll_data.get("key", ""),
                    name=data.get("name", "Unnamed"),
                    parent_key=data.get("parentCollection") or None,
                )
            )
        return collections

    async def get_collection_items(
        self, collection_key: str, limit: int = 50
    ) -> list[ZoteroItem]:
        """Get items in a collection."""
        result = await self._request(
            "GET",
            f"/api/users/0/collections/{collection_key}/items",
            params={"limit": limit},
        )
        if not result:
            return []

        return [
            ZoteroItem.from_api_response(item)
            for item in result
            if item.get("data", {}).get("itemType") != "attachment"
        ]

    async def get_tags(self) -> list[str]:
        """Get all tags."""
        result = await self._request("GET", "/api/users/0/tags")
        if not result:
            return []
        return sorted([t["tag"] for t in result]) if isinstance(result, list) else []

    async def update_item_tags(
        self, key: str, add_tags: list[str] = None, remove_tags: list[str] = None
    ) -> bool:
        """Update tags on an item."""
        item = await self.get_item(key)
        if not item or not item.raw_data:
            return False

        current_tags = set(item.tags)

        if remove_tags:
            current_tags -= set(remove_tags)
        if add_tags:
            current_tags |= set(add_tags)

        # Update via API
        item.raw_data["data"]["tags"] = [{"tag": t} for t in current_tags]
        result = await self._request(
            "PUT",
            f"/api/users/0/items/{key}",
            json=item.raw_data,
        )
        return result is not None

    async def create_note(
        self, parent_key: str, content: str, tags: list[str] = None
    ) -> Optional[str]:
        """Create a note."""
        note_data = {
            "itemType": "note",
            "parentItem": parent_key,
            "note": content,
            "tags": [{"tag": t} for t in (tags or [])],
        }
        result = await self._request(
            "POST", "/api/users/0/items", json=[note_data]
        )
        if result and "success" in result:
            return list(result["success"].values())[0] if result["success"] else None
        return None

    async def move_item_to_collection(self, item_key: str, collection_key: str) -> bool:
        """Move item to collection."""
        item = await self.get_item(item_key)
        if not item or not item.raw_data:
            return False

        collections = set(item.raw_data.get("data", {}).get("collections", []))
        collections.add(collection_key)
        item.raw_data["data"]["collections"] = list(collections)

        result = await self._request(
            "PUT",
            f"/api/users/0/items/{item_key}",
            json=item.raw_data,
        )
        return result is not None

    async def get_item_children(self, item_key: str) -> list[ZoteroItem]:
        """Get children (attachments, notes) of an item."""
        result = await self._request("GET", f"/api/users/0/items/{item_key}/children")
        if not result:
            return []

        children = []
        for child_data in result:
            children.append(ZoteroItem.from_api_response(child_data))
        return children

    async def create_collection(self, name: str, parent_key: Optional[str] = None) -> Optional[str]:
        """Create a new collection.

        Note: Zotero Local API (port 23119) does NOT support POST /collections (returns 405).
        This method will return None and log a warning.
        Use ZoteroWebClient for collection CRUD operations.
        """
        logger.warning(
            "Zotero Local API does not support creating collections. "
            "Use Web API mode or create collections manually in Zotero."
        )
        return None

    async def delete_collection(self, collection_key: str) -> bool:
        """Delete a collection.

        Note: Zotero Local API does not support DELETE /collections.
        This method will return False and log a warning.
        """
        logger.warning(
            "Zotero Local API does not support deleting collections. "
            "Use Web API mode or delete collections manually in Zotero."
        )
        return False

    async def rename_collection(self, collection_key: str, new_name: str) -> bool:
        """Rename a collection.

        Note: Zotero Local API does not support PUT /collections.
        This method will return False and log a warning.
        """
        logger.warning(
            "Zotero Local API does not support renaming collections. "
            "Use Web API mode or rename collections manually in Zotero."
        )
        return False

    async def batch_move_to_collection(self, item_keys: list[str], collection_key: str) -> dict[str, bool]:
        """Move multiple items to a collection."""
        results = {}
        for item_key in item_keys:
            results[item_key] = await self.move_item_to_collection(item_key, collection_key)
        return results

    async def get_notes(
        self, item_key: Optional[str] = None, limit: int = 50
    ) -> list[ZoteroItem]:
        """Get notes via local API."""
        if item_key:
            # Get children of item, then filter for notes
            children = await self.get_item_children(item_key)
            return [c for c in children if c.item_type == "note"][:limit]
        else:
            result = await self._request(
                "GET", "/api/users/0/items",
                params={"itemType": "note", "limit": limit, "sort": "dateModified", "direction": "desc"},
            )
            if not result:
                return []
            return [ZoteroItem.from_api_response(r) for r in result]

    async def update_note(
        self, note_key: str, content: str, append: bool = True
    ) -> bool:
        """Update note content via local API."""
        item = await self.get_item(note_key)
        if not item or not item.raw_data:
            return False
        if append:
            existing = item.raw_data.get("data", {}).get("note", "")
            if not existing:
                existing = ""
            new_content = f"{existing}<p>{content}</p>"
        else:
            if "<p>" not in content:
                paragraphs = content.split("\n\n")
                content = "".join(f"<p>{p.replace(chr(10), '<br/>')}</p>" for p in paragraphs if p)
            new_content = content
        item.raw_data["data"]["note"] = new_content
        result = await self._request(
            "PUT", f"/api/users/0/items/{note_key}", json=item.raw_data
        )
        return result is not None

    async def trash_item(self, key: str) -> bool:
        """Move item to trash via local API."""
        item = await self.get_item(key)
        if not item or not item.raw_data:
            return False
        item.raw_data["data"]["deleted"] = 1
        result = await self._request(
            "PUT", f"/api/users/0/items/{key}", json=item.raw_data
        )
        return result is not None

    async def download_attachment(self, key: str) -> Optional[bytes]:
        """Download attachment file via local API."""
        # Try the file endpoint first
        client = await self._get_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/users/0/items/{key}/file",
                timeout=60.0,
            )
            resp.raise_for_status()
            return resp.content
        except Exception:
            pass
        # Fallback: try linked file path
        item = await self.get_item(key)
        if not item or not item.raw_data:
            return None
        data = item.raw_data.get("data", {})
        path = data.get("path", "")
        if path.startswith("attachments:") and self._linked_base:
            path = os.path.join(self._linked_base, path[len("attachments:"):])
        if path and os.path.isfile(path):
            try:
                with open(path, "rb") as f:
                    return f.read()
            except Exception as e:
                logger.error(f"Failed to read linked attachment: {e}")
        return None

    async def create_item_raw(self, item_data: dict) -> Optional[str]:
        """Create item from raw data via local API."""
        result = await self._request(
            "POST", "/api/users/0/items", json=[item_data]
        )
        if result and "success" in result:
            return list(result["success"].values())[0] if result["success"] else None
        return None

    async def get_all_items(
        self, limit: int = 50, item_type: Optional[str] = None
    ) -> list[ZoteroItem]:
        """Get items via local API."""
        params: dict = {"limit": limit, "sort": "dateAdded", "direction": "desc"}
        if item_type:
            params["itemType"] = item_type
        result = await self._request("GET", "/api/users/0/items", params=params)
        if not result:
            return []
        return [ZoteroItem.from_api_response(r) for r in result]


class ZoteroWebClient(ZoteroClientBase):
    """Client for Zotero Web API using pyzotero."""

    def __init__(
        self,
        api_key: str,
        library_id: str,
        library_type: str = "user",
    ):
        self.api_key = api_key
        self.library_id = library_id
        self.library_type = library_type
        self._zot = None

    def _get_zot(self):
        """Get pyzotero client (lazy initialization)."""
        if self._zot is None:
            from pyzotero import zotero

            self._zot = zotero.Zotero(
                self.library_id, self.library_type, self.api_key
            )
        return self._zot

    async def is_available(self) -> bool:
        """Check if API is accessible."""
        try:
            zot = self._get_zot()
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, zot.key_info)
            return True
        except Exception:
            return False

    async def search_items(
        self,
        query: str,
        limit: int = 10,
        item_type: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> list[ZoteroItem]:
        """Search items via Web API."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _search():
            zot.add_parameters(
                q=query,
                qmode="titleCreatorYear",
                itemType=item_type or "-attachment",
                limit=limit,
                tag=tags or [],
            )
            return zot.items()

        results = await loop.run_in_executor(None, _search)
        return [ZoteroItem.from_api_response(item) for item in results]

    async def get_item(self, key: str) -> Optional[ZoteroItem]:
        """Get item by key."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _get():
            return zot.item(key)

        try:
            result = await loop.run_in_executor(None, _get)
            return ZoteroItem.from_api_response(result) if result else None
        except Exception:
            return None

    async def get_item_fulltext(self, key: str) -> Optional[str]:
        """Get full text."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _get_fulltext():
            # First get children to find attachment
            children = zot.children(key)
            for child in children:
                if child.get("data", {}).get("itemType") == "attachment":
                    att_key = child.get("key")
                    try:
                        ft = zot.fulltext_item(att_key)
                        if ft and "content" in ft:
                            return ft["content"]
                    except Exception:
                        pass
            return None

        return await loop.run_in_executor(None, _get_fulltext)

    async def get_collections(self) -> list[ZoteroCollection]:
        """Get all collections."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _get():
            return zot.collections()

        results = await loop.run_in_executor(None, _get)
        return [
            ZoteroCollection(
                key=c.get("key", ""),
                name=c.get("data", {}).get("name", "Unnamed"),
                parent_key=c.get("data", {}).get("parentCollection") or None,
            )
            for c in results
        ]

    async def get_collection_items(
        self, collection_key: str, limit: int = 50
    ) -> list[ZoteroItem]:
        """Get items in collection."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _get():
            return zot.collection_items(collection_key, limit=limit)

        results = await loop.run_in_executor(None, _get)
        return [
            ZoteroItem.from_api_response(item)
            for item in results
            if item.get("data", {}).get("itemType") != "attachment"
        ]

    async def get_tags(self) -> list[str]:
        """Get all tags."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _get():
            return zot.tags()

        results = await loop.run_in_executor(None, _get)
        return sorted(results) if results else []

    async def update_item_tags(
        self, key: str, add_tags: list[str] = None, remove_tags: list[str] = None
    ) -> bool:
        """Update item tags."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _update():
            item = zot.item(key)
            if not item:
                return False

            current_tags = {t["tag"] for t in item.get("data", {}).get("tags", [])}
            if remove_tags:
                current_tags -= set(remove_tags)
            if add_tags:
                current_tags |= set(add_tags)

            item["data"]["tags"] = [{"tag": t} for t in current_tags]
            zot.update_item(item)
            return True

        try:
            return await loop.run_in_executor(None, _update)
        except Exception:
            return False

    async def create_note(
        self, parent_key: str, content: str, tags: list[str] = None
    ) -> Optional[str]:
        """Create a note."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _create():
            note_data = {
                "itemType": "note",
                "parentItem": parent_key,
                "note": content,
                "tags": [{"tag": t} for t in (tags or [])],
            }
            result = zot.create_items([note_data])
            if result.get("success"):
                return list(result["success"].values())[0]
            return None

        return await loop.run_in_executor(None, _create)

    async def move_item_to_collection(self, item_key: str, collection_key: str) -> bool:
        """Move item to collection."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _move():
            item = zot.item(item_key)
            if not item:
                return False

            collections = set(item.get("data", {}).get("collections", []))
            collections.add(collection_key)
            item["data"]["collections"] = list(collections)
            zot.update_item(item)
            return True

        try:
            return await loop.run_in_executor(None, _move)
        except Exception:
            return False

    async def get_item_children(self, item_key: str) -> list[ZoteroItem]:
        """Get children (attachments, notes) of an item."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _get_children():
            children_data = zot.children(item_key)
            return [ZoteroItem.from_api_response(child) for child in children_data]

        return await loop.run_in_executor(None, _get_children)

    async def create_collection(self, name: str, parent_key: Optional[str] = None) -> Optional[str]:
        """Create a new collection."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _create():
            collection_data = {"name": name}
            if parent_key:
                collection_data["parentCollection"] = parent_key
            result = zot.create_collections([collection_data])
            if result.get("success"):
                return list(result["success"].values())[0]
            return None

        try:
            return await loop.run_in_executor(None, _create)
        except Exception as e:
            logger.error(f"Failed to create collection: {e}")
            return None

    async def delete_collection(self, collection_key: str) -> bool:
        """Delete a collection."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _delete():
            zot.delete_collection(zot.collection(collection_key))
            return True

        try:
            return await loop.run_in_executor(None, _delete)
        except Exception as e:
            logger.error(f"Failed to delete collection: {e}")
            return False

    async def rename_collection(self, collection_key: str, new_name: str) -> bool:
        """Rename a collection."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _rename():
            collection = zot.collection(collection_key)
            if not collection:
                return False
            collection["data"]["name"] = new_name
            zot.update_collection(collection)
            return True

        try:
            return await loop.run_in_executor(None, _rename)
        except Exception as e:
            logger.error(f"Failed to rename collection: {e}")
            return False

    async def batch_move_to_collection(self, item_keys: list[str], collection_key: str) -> dict[str, bool]:
        """Move multiple items to a collection."""
        results = {}
        for item_key in item_keys:
            results[item_key] = await self.move_item_to_collection(item_key, collection_key)
        return results

    async def get_notes(
        self, item_key: Optional[str] = None, limit: int = 50
    ) -> list[ZoteroItem]:
        """Get notes via pyzotero."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _get():
            if item_key:
                return zot.children(item_key, itemType="note")
            return zot.items(itemType="note", limit=limit, sort="dateModified", direction="desc")

        try:
            data = await loop.run_in_executor(None, _get)
            return [ZoteroItem.from_api_response(r) for r in data]
        except Exception as e:
            logger.error(f"Failed to get notes: {e}")
            return []

    async def update_note(
        self, note_key: str, content: str, append: bool = True
    ) -> bool:
        """Update note via pyzotero."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _update():
            item = zot.item(note_key)
            if not item:
                return False
            existing = item.get("data", {}).get("note", "")
            if append:
                new_content = f"{existing}<p>{content}</p>"
            else:
                if "<p>" not in content:
                    paragraphs = content.split("\n\n")
                    new_content = "".join(
                        f"<p>{p.replace(chr(10), '<br/>')}</p>" for p in paragraphs if p
                    )
                else:
                    new_content = content
            item["data"]["note"] = new_content
            zot.update_item(item)
            return True

        try:
            return await loop.run_in_executor(None, _update)
        except Exception as e:
            logger.error(f"Failed to update note: {e}")
            return False

    async def trash_item(self, key: str) -> bool:
        """Trash item via pyzotero."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _trash():
            item = zot.item(key)
            if not item:
                return False
            zot.trash_item(item)
            return True

        try:
            return await loop.run_in_executor(None, _trash)
        except Exception as e:
            logger.error(f"Failed to trash item: {e}")
            return False

    async def download_attachment(self, key: str) -> Optional[bytes]:
        """Download attachment via pyzotero."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _download():
            return zot.file(key)

        try:
            return await loop.run_in_executor(None, _download)
        except Exception as e:
            logger.error(f"Failed to download attachment: {e}")
            return None

    async def create_item_raw(self, item_data: dict) -> Optional[str]:
        """Create item via pyzotero."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _create():
            result = zot.create_items([item_data])
            if result.get("success"):
                return list(result["success"].values())[0]
            return None

        return await loop.run_in_executor(None, _create)

    async def get_all_items(
        self, limit: int = 50, item_type: Optional[str] = None
    ) -> list[ZoteroItem]:
        """Get items via pyzotero."""
        zot = self._get_zot()
        loop = asyncio.get_event_loop()

        def _get():
            kwargs = {"limit": limit, "sort": "dateAdded", "direction": "desc"}
            if item_type:
                kwargs["itemType"] = item_type
            return zot.items(**kwargs)

        try:
            data = await loop.run_in_executor(None, _get)
            return [ZoteroItem.from_api_response(r) for r in data]
        except Exception as e:
            logger.error(f"Failed to get items: {e}")
            return []


class ZoteroSQLiteClient(ZoteroClientBase):
    """Client for direct SQLite database access."""

    def __init__(self, db_path: str, storage_path: Optional[str] = None):
        self.db_path = Path(db_path)
        self.storage_path = Path(storage_path) if storage_path else None

    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    async def is_available(self) -> bool:
        """Check if database is accessible."""
        if not self.db_path.exists():
            return False
        try:
            conn = self._get_connection()
            conn.execute("SELECT 1 FROM items LIMIT 1")
            conn.close()
            return True
        except Exception:
            return False

    async def search_items(
        self,
        query: str,
        limit: int = 10,
        item_type: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> list[ZoteroItem]:
        """Search items in SQLite database."""
        loop = asyncio.get_event_loop()

        def _search():
            conn = self._get_connection()
            try:
                # Basic search query
                sql = """
                    SELECT i.key, i.itemID, it.typeName,
                           (SELECT value FROM itemData id
                            JOIN itemDataValues idv ON id.valueID = idv.valueID
                            JOIN fields f ON id.fieldID = f.fieldID
                            WHERE id.itemID = i.itemID AND f.fieldName = 'title') as title
                    FROM items i
                    JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
                    WHERE it.typeName != 'attachment' AND it.typeName != 'note'
                    LIMIT ?
                """
                cursor = conn.execute(sql, (limit,))
                results = []
                for row in cursor:
                    if query.lower() in (row["title"] or "").lower():
                        results.append(
                            ZoteroItem(
                                key=row["key"],
                                item_type=row["typeName"],
                                title=row["title"] or "Untitled",
                                creators=[],
                            )
                        )
                return results
            finally:
                conn.close()

        return await loop.run_in_executor(None, _search)

    async def get_item(self, key: str) -> Optional[ZoteroItem]:
        """Get item by key from SQLite."""
        loop = asyncio.get_event_loop()

        def _get():
            conn = self._get_connection()
            try:
                sql = """
                    SELECT i.key, i.itemID, it.typeName
                    FROM items i
                    JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
                    WHERE i.key = ?
                """
                cursor = conn.execute(sql, (key,))
                row = cursor.fetchone()
                if row:
                    # Get title
                    title_sql = """
                        SELECT idv.value FROM itemData id
                        JOIN itemDataValues idv ON id.valueID = idv.valueID
                        JOIN fields f ON id.fieldID = f.fieldID
                        WHERE id.itemID = ? AND f.fieldName = 'title'
                    """
                    title_cursor = conn.execute(title_sql, (row["itemID"],))
                    title_row = title_cursor.fetchone()

                    return ZoteroItem(
                        key=row["key"],
                        item_type=row["typeName"],
                        title=title_row["value"] if title_row else "Untitled",
                        creators=[],
                    )
                return None
            finally:
                conn.close()

        return await loop.run_in_executor(None, _get)

    async def get_item_fulltext(self, key: str) -> Optional[str]:
        """Get full text from SQLite fulltext table."""
        loop = asyncio.get_event_loop()

        def _get():
            conn = self._get_connection()
            try:
                sql = """
                    SELECT ft.content FROM fulltextItems ft
                    JOIN items i ON ft.itemID = i.itemID
                    WHERE i.key = ?
                """
                cursor = conn.execute(sql, (key,))
                row = cursor.fetchone()
                return row["content"] if row else None
            finally:
                conn.close()

        return await loop.run_in_executor(None, _get)

    async def get_collections(self) -> list[ZoteroCollection]:
        """Get collections from SQLite."""
        loop = asyncio.get_event_loop()

        def _get():
            conn = self._get_connection()
            try:
                sql = """
                    SELECT c.key, c.collectionName, pc.key as parentKey
                    FROM collections c
                    LEFT JOIN collections pc ON c.parentCollectionID = pc.collectionID
                """
                cursor = conn.execute(sql)
                return [
                    ZoteroCollection(
                        key=row["key"],
                        name=row["collectionName"],
                        parent_key=row["parentKey"],
                    )
                    for row in cursor
                ]
            finally:
                conn.close()

        return await loop.run_in_executor(None, _get)

    async def get_collection_items(
        self, collection_key: str, limit: int = 50
    ) -> list[ZoteroItem]:
        """Get items in a collection."""
        # Simplified implementation
        return []

    async def get_tags(self) -> list[str]:
        """Get all tags."""
        loop = asyncio.get_event_loop()

        def _get():
            conn = self._get_connection()
            try:
                sql = "SELECT name FROM tags ORDER BY name"
                cursor = conn.execute(sql)
                return [row["name"] for row in cursor]
            finally:
                conn.close()

        return await loop.run_in_executor(None, _get)

    async def update_item_tags(
        self, key: str, add_tags: list[str] = None, remove_tags: list[str] = None
    ) -> bool:
        """SQLite is read-only for safety."""
        logger.warning("SQLite client is read-only, cannot update tags")
        return False

    async def create_note(
        self, parent_key: str, content: str, tags: list[str] = None
    ) -> Optional[str]:
        """SQLite is read-only for safety."""
        logger.warning("SQLite client is read-only, cannot create notes")
        return None

    async def move_item_to_collection(self, item_key: str, collection_key: str) -> bool:
        """SQLite is read-only for safety."""
        logger.warning("SQLite client is read-only, cannot move items")
        return False

    async def get_item_children(self, item_key: str) -> list[ZoteroItem]:
        """Get children (attachments, notes) of an item from SQLite."""
        loop = asyncio.get_event_loop()

        def _get_children():
            conn = self._get_connection()
            try:
                sql = """
                    SELECT i.key, it.typeName
                    FROM items i
                    JOIN itemTypes it ON i.itemTypeID = it.itemTypeID
                    WHERE i.parentItemKey = ?
                """
                cursor = conn.execute(sql, (item_key,))
                children = []
                for row in cursor:
                    # Get title for each child
                    title_sql = """
                        SELECT idv.value FROM itemData id
                        JOIN itemDataValues idv ON id.valueID = idv.valueID
                        JOIN fields f ON id.fieldID = f.fieldID
                        JOIN items i ON id.itemID = i.itemID
                        WHERE i.key = ? AND f.fieldName = 'title'
                    """
                    title_cursor = conn.execute(title_sql, (row["key"],))
                    title_row = title_cursor.fetchone()

                    children.append(ZoteroItem(
                        key=row["key"],
                        item_type=row["typeName"],
                        title=title_row["value"] if title_row else "Untitled",
                        creators=[],
                    ))
                return children
            finally:
                conn.close()

        return await loop.run_in_executor(None, _get_children)

    async def create_collection(self, name: str, parent_key: Optional[str] = None) -> Optional[str]:
        """SQLite is read-only for safety."""
        logger.warning("SQLite client is read-only, cannot create collections")
        return None

    async def delete_collection(self, collection_key: str) -> bool:
        """SQLite is read-only for safety."""
        logger.warning("SQLite client is read-only, cannot delete collections")
        return False

    async def rename_collection(self, collection_key: str, new_name: str) -> bool:
        """SQLite is read-only for safety."""
        logger.warning("SQLite client is read-only, cannot rename collections")
        return False

    async def batch_move_to_collection(self, item_keys: list[str], collection_key: str) -> dict[str, bool]:
        """SQLite is read-only for safety."""
        logger.warning("SQLite client is read-only, cannot move items")
        return {key: False for key in item_keys}

    async def get_notes(
        self, item_key: Optional[str] = None, limit: int = 50
    ) -> list[ZoteroItem]:
        """Get notes from SQLite (limited support)."""
        # Delegate to get_item_children for item-specific notes
        if item_key:
            children = await self.get_item_children(item_key)
            return [c for c in children if c.item_type == "note"][:limit]
        # General note listing not well-supported in SQLite client
        return []

    async def update_note(self, note_key: str, content: str, append: bool = True) -> bool:
        logger.warning("SQLite client is read-only, cannot update notes")
        return False

    async def trash_item(self, key: str) -> bool:
        logger.warning("SQLite client is read-only, cannot trash items")
        return False

    async def download_attachment(self, key: str) -> Optional[bytes]:
        """Try to read attachment from storage path."""
        if not self.storage_path:
            return None
        # Zotero stores files as storage/<key>/<filename>
        storage_dir = Path(self.storage_path) / key
        if storage_dir.is_dir():
            for f in storage_dir.iterdir():
                if f.is_file() and f.suffix.lower() == ".pdf":
                    try:
                        return f.read_bytes()
                    except Exception as e:
                        logger.error(f"Failed to read attachment: {e}")
        return None

    async def create_item_raw(self, item_data: dict) -> Optional[str]:
        logger.warning("SQLite client is read-only, cannot create items")
        return None

    async def get_all_items(
        self, limit: int = 50, item_type: Optional[str] = None
    ) -> list[ZoteroItem]:
        """Get items from SQLite, reuse search logic."""
        return await self.search_items("", limit=limit, item_type=item_type)


class ZoteroHybridClient(ZoteroClientBase):
    """Hybrid client: Local API for reads, Web API for collection CRUD.

    This client combines the speed of Local API for read operations with
    the full functionality of Web API for collection management.

    - All read operations (search, get_item, get_collections, etc.) use Local API
    - Collection CRUD (create/delete/rename) use Web API
    - Item operations (tags, notes, move) use Local API
    """

    def __init__(
        self,
        api_key: str,
        library_id: str,
        library_type: str = "user",
        local_host: str = "127.0.0.1",
        local_port: int = 23119,
        linked_base: Optional[str] = None,
        search_groups: bool = True,
    ):
        # Initialize both clients
        self._local_client = ZoteroLocalClient(
            host=local_host,
            port=local_port,
            linked_base=linked_base,
            search_groups=search_groups,
        )
        self._web_client = ZoteroWebClient(
            api_key=api_key,
            library_id=library_id,
            library_type=library_type,
        )

    async def is_available(self) -> bool:
        """Check if both backends are available."""
        local_ok = await self._local_client.is_available()
        if not local_ok:
            logger.warning("Hybrid mode: Local API not available")
            return False
        # Web API availability is checked lazily when needed
        return True

    # Read operations - use Local API (fast)
    async def search_items(
        self,
        query: str,
        limit: int = 10,
        item_type: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> list[ZoteroItem]:
        """Search items via Local API."""
        return await self._local_client.search_items(query, limit, item_type, tags)

    async def get_item(self, key: str) -> Optional[ZoteroItem]:
        """Get item via Local API."""
        return await self._local_client.get_item(key)

    async def get_item_fulltext(self, key: str) -> Optional[str]:
        """Get fulltext via Local API."""
        return await self._local_client.get_item_fulltext(key)

    async def get_collections(self) -> list[ZoteroCollection]:
        """Get collections via Local API."""
        return await self._local_client.get_collections()

    async def get_collection_items(
        self, collection_key: str, limit: int = 50
    ) -> list[ZoteroItem]:
        """Get collection items via Local API."""
        return await self._local_client.get_collection_items(collection_key, limit)

    async def get_tags(self) -> list[str]:
        """Get tags via Local API."""
        return await self._local_client.get_tags()

    async def get_item_children(self, item_key: str) -> list[ZoteroItem]:
        """Get item children via Local API."""
        return await self._local_client.get_item_children(item_key)

    # Item write operations - use Local API
    async def update_item_tags(
        self, key: str, add_tags: list[str] = None, remove_tags: list[str] = None
    ) -> bool:
        """Update item tags via Local API."""
        return await self._local_client.update_item_tags(key, add_tags, remove_tags)

    async def create_note(
        self, parent_key: str, content: str, tags: list[str] = None
    ) -> Optional[str]:
        """Create note via Local API."""
        return await self._local_client.create_note(parent_key, content, tags)

    async def move_item_to_collection(self, item_key: str, collection_key: str) -> bool:
        """Move item to collection via Local API."""
        return await self._local_client.move_item_to_collection(item_key, collection_key)

    async def batch_move_to_collection(self, item_keys: list[str], collection_key: str) -> dict[str, bool]:
        """Batch move items via Local API."""
        return await self._local_client.batch_move_to_collection(item_keys, collection_key)

    # Collection CRUD - use Web API (only way to modify collections)
    async def create_collection(self, name: str, parent_key: Optional[str] = None) -> Optional[str]:
        """Create collection via Web API."""
        logger.info(f"Hybrid mode: Creating collection '{name}' via Web API")
        return await self._web_client.create_collection(name, parent_key)

    async def delete_collection(self, collection_key: str) -> bool:
        """Delete collection via Web API."""
        logger.info(f"Hybrid mode: Deleting collection '{collection_key}' via Web API")
        return await self._web_client.delete_collection(collection_key)

    async def rename_collection(self, collection_key: str, new_name: str) -> bool:
        """Rename collection via Web API."""
        logger.info(f"Hybrid mode: Renaming collection '{collection_key}' to '{new_name}' via Web API")
        return await self._web_client.rename_collection(collection_key, new_name)

    async def get_notes(
        self, item_key: Optional[str] = None, limit: int = 50
    ) -> list[ZoteroItem]:
        """Get notes via Local API."""
        return await self._local_client.get_notes(item_key, limit)

    async def update_note(self, note_key: str, content: str, append: bool = True) -> bool:
        """Update note via Local API."""
        return await self._local_client.update_note(note_key, content, append)

    async def trash_item(self, key: str) -> bool:
        """Trash item via Local API."""
        return await self._local_client.trash_item(key)

    async def download_attachment(self, key: str) -> Optional[bytes]:
        """Download attachment via Local API."""
        return await self._local_client.download_attachment(key)

    async def create_item_raw(self, item_data: dict) -> Optional[str]:
        """Create item via Local API."""
        return await self._local_client.create_item_raw(item_data)

    async def get_all_items(
        self, limit: int = 50, item_type: Optional[str] = None
    ) -> list[ZoteroItem]:
        """Get items via Local API."""
        return await self._local_client.get_all_items(limit, item_type)


def create_client(config) -> ZoteroClientBase:
    """Factory function to create appropriate client based on config."""
    from zotmcp.config import ZoteroConfig

    if isinstance(config, dict):
        config = ZoteroConfig(**config)

    if config.mode == "local":
        return ZoteroLocalClient(host=config.local_host, port=config.local_port)
    elif config.mode == "web":
        if not config.api_key or not config.library_id:
            raise ValueError("Web API requires api_key and library_id")
        return ZoteroWebClient(
            api_key=config.api_key,
            library_id=config.library_id,
            library_type=config.library_type,
        )
    elif config.mode == "hybrid":
        if not config.api_key or not config.library_id:
            raise ValueError("Hybrid mode requires api_key and library_id for Web API")
        return ZoteroHybridClient(
            api_key=config.api_key,
            library_id=config.library_id,
            library_type=config.library_type,
            local_host=config.local_host,
            local_port=config.local_port,
        )
    elif config.mode == "sqlite":
        if not config.sqlite_path:
            raise ValueError("SQLite mode requires sqlite_path")
        return ZoteroSQLiteClient(
            db_path=config.sqlite_path,
            storage_path=config.storage_path,
        )
    else:
        raise ValueError(f"Unknown mode: {config.mode}")
