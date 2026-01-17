"""Unit tests for iterators."""

import pytest
from zotmcp.iterators import PaginatedIterator, StreamingFullText


@pytest.mark.asyncio
async def test_paginated_iterator():
    """Test paginated iteration."""
    items = list(range(250))

    async def fetch_page(start: int, limit: int):
        return items[start : start + limit]

    iterator = PaginatedIterator(fetch_page, page_size=100, max_items=250)

    collected = []
    async for item in iterator:
        collected.append(item)

    assert len(collected) == 250
    assert collected == items


@pytest.mark.asyncio
async def test_paginated_iterator_truncation():
    """Test truncation at max_items."""
    items = list(range(2000))

    async def fetch_page(start: int, limit: int):
        return items[start : start + limit]

    iterator = PaginatedIterator(fetch_page, page_size=100, max_items=1000)

    collected = []
    async for item in iterator:
        collected.append(item)

    assert len(collected) == 1000
    assert iterator.info.was_truncated


@pytest.mark.asyncio
async def test_streaming_fulltext():
    """Test streaming text in chunks."""
    text = b"A" * 10000  # Use bytes instead of string

    async def fetch_chunk(offset: int, size: int) -> bytes:
        """Fetch a chunk of text at given offset."""
        return text[offset : offset + size]

    stream = StreamingFullText(fetch_chunk, total_size=len(text), chunk_size=1000)

    chunks = []
    async for chunk in stream:
        chunks.append(chunk)

    assert len(chunks) == 10
    assert b"".join(chunks) == text


@pytest.mark.asyncio
async def test_streaming_empty():
    """Test streaming empty text."""
    async def fetch_chunk(offset: int, size: int) -> bytes:
        """Return empty bytes."""
        return b""

    stream = StreamingFullText(fetch_chunk, total_size=0)

    chunks = []
    async for chunk in stream:
        chunks.append(chunk)

    assert len(chunks) == 0
