"""Integration tests for Zotero backends."""

import pytest
from zotmcp.clients import create_client
from zotmcp.config import Config, ZoteroConfig


@pytest.mark.asyncio
async def test_local_client_connection():
    """Test local Zotero client connection."""
    config = ZoteroConfig(mode="local", local_port=23119)
    client = create_client(config)

    # This will fail if Zotero is not running, which is expected
    available = await client.is_available()
    assert isinstance(available, bool)


@pytest.mark.asyncio
async def test_web_client_requires_credentials():
    """Test web client requires API key."""
    config = ZoteroConfig(mode="web", api_key=None)
    client = create_client(config)

    # Should handle missing credentials gracefully
    available = await client.is_available()
    assert available is False


@pytest.mark.asyncio
@pytest.mark.skipif(True, reason="Requires actual Zotero instance")
async def test_search_items():
    """Test searching items (requires running Zotero)."""
    config = ZoteroConfig(mode="local")
    client = create_client(config)

    if not await client.is_available():
        pytest.skip("Zotero not available")

    items = await client.search_items("test", limit=5)
    assert isinstance(items, list)
    assert len(items) <= 5


@pytest.mark.asyncio
@pytest.mark.skipif(True, reason="Requires actual Zotero instance")
async def test_get_collections():
    """Test getting collections (requires running Zotero)."""
    config = ZoteroConfig(mode="local")
    client = create_client(config)

    if not await client.is_available():
        pytest.skip("Zotero not available")

    collections = await client.get_collections()
    assert isinstance(collections, list)
