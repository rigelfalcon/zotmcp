"""Memory-safe iterators for large result sets.

Provides:
- PaginatedIterator: Async iterator with page-based fetching
- StreamingFullText: Chunked streaming for full text content
- Automatic truncation with warnings
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Coroutine,
    Generic,
    TypeVar,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class PaginationInfo:
    """Information about pagination state.

    Attributes:
        total_available: Total items available (if known)
        total_returned: Items returned so far
        was_truncated: Whether results were truncated
        page_size: Items per page
        max_items: Maximum items allowed
    """

    total_available: int | None = None
    total_returned: int = 0
    was_truncated: bool = False
    page_size: int = 100
    max_items: int = 1000


class PaginatedIterator(Generic[T], AsyncIterator[T]):
    """Memory-safe async iterator with pagination.

    Fetches items in pages to avoid loading entire result sets into memory.
    Automatically truncates at max_items with a warning.

    Example:
        async def fetch_page(offset: int, limit: int) -> list[Item]:
            return await api.search(query, start=offset, limit=limit)

        iterator = PaginatedIterator(
            fetch_page=fetch_page,
            page_size=100,
            max_items=1000
        )

        async for item in iterator:
            process(item)

        if iterator.info.was_truncated:
            print(f"Warning: Results truncated at {iterator.info.max_items}")
    """

    def __init__(
        self,
        fetch_page: Callable[[int, int], Coroutine[Any, Any, list[T]]],
        page_size: int = 100,
        max_items: int = 1000,
        total_hint: int | None = None,
    ) -> None:
        """Initialize paginated iterator.

        Args:
            fetch_page: Async function(offset, limit) -> list[T]
            page_size: Items per page (default 100, max 100)
            max_items: Maximum total items (default 1000)
            total_hint: Optional hint about total available items
        """
        self.fetch_page = fetch_page
        self.page_size = min(page_size, 100)  # Enforce max page size
        self.max_items = max_items

        self._buffer: list[T] = []
        self._buffer_index = 0
        self._offset = 0
        self._exhausted = False
        self._items_yielded = 0

        self.info = PaginationInfo(
            total_available=total_hint,
            page_size=self.page_size,
            max_items=self.max_items,
        )

    def __aiter__(self) -> "PaginatedIterator[T]":
        return self

    async def __anext__(self) -> T:
        # Check if we've hit the limit
        if self._items_yielded >= self.max_items:
            if not self.info.was_truncated:
                self.info.was_truncated = True
                logger.warning(
                    "Results truncated at %d items. Use filters to narrow search.",
                    self.max_items,
                )
            raise StopAsyncIteration

        # Fetch next page if buffer exhausted
        if self._buffer_index >= len(self._buffer):
            if self._exhausted:
                raise StopAsyncIteration

            await self._fetch_next_page()

            if not self._buffer:
                raise StopAsyncIteration

        # Return next item from buffer
        item = self._buffer[self._buffer_index]
        self._buffer_index += 1
        self._items_yielded += 1
        self.info.total_returned = self._items_yielded

        return item

    async def _fetch_next_page(self) -> None:
        """Fetch the next page of results."""
        try:
            # Calculate how many items we can still fetch
            remaining = self.max_items - self._items_yielded
            limit = min(self.page_size, remaining)

            if limit <= 0:
                self._exhausted = True
                return

            self._buffer = await self.fetch_page(self._offset, limit)
            self._buffer_index = 0
            self._offset += len(self._buffer)

            if len(self._buffer) < limit:
                self._exhausted = True

            logger.debug(
                "Fetched page: offset=%d, got=%d items",
                self._offset - len(self._buffer),
                len(self._buffer),
            )

        except Exception as e:
            logger.error("Error fetching page at offset %d: %s", self._offset, e)
            self._exhausted = True
            raise

    async def collect(self) -> list[T]:
        """Collect all items into a list.

        Warning: This loads all items into memory. Use iteration for large sets.

        Returns:
            List of all items
        """
        items = []
        async for item in self:
            items.append(item)
        return items

    async def first(self) -> T | None:
        """Get the first item or None if empty."""
        try:
            return await self.__anext__()
        except StopAsyncIteration:
            return None


class StreamingFullText:
    """Stream full text content in chunks to avoid memory spikes.

    Example:
        async def fetch_chunk(offset: int, size: int) -> bytes:
            return await api.get_fulltext_chunk(key, offset, size)

        stream = StreamingFullText(
            fetch_chunk=fetch_chunk,
            total_size=1000000,
            chunk_size=8192
        )

        async for chunk in stream:
            file.write(chunk)
    """

    def __init__(
        self,
        fetch_chunk: Callable[[int, int], Coroutine[Any, Any, bytes]],
        total_size: int | None = None,
        chunk_size: int = 8192,
    ) -> None:
        """Initialize streaming full text.

        Args:
            fetch_chunk: Async function(offset, size) -> bytes
            total_size: Total content size if known
            chunk_size: Bytes per chunk (default 8KB)
        """
        self.fetch_chunk = fetch_chunk
        self.total_size = total_size
        self.chunk_size = chunk_size

        self._offset = 0
        self._exhausted = False

    def __aiter__(self) -> "StreamingFullText":
        return self

    async def __anext__(self) -> bytes:
        if self._exhausted:
            raise StopAsyncIteration

        # Check if we've read everything
        if self.total_size is not None and self._offset >= self.total_size:
            raise StopAsyncIteration

        try:
            chunk = await self.fetch_chunk(self._offset, self.chunk_size)

            if not chunk:
                self._exhausted = True
                raise StopAsyncIteration

            self._offset += len(chunk)

            # Check if this was the last chunk
            if len(chunk) < self.chunk_size:
                self._exhausted = True

            return chunk

        except Exception as e:
            logger.error("Error fetching chunk at offset %d: %s", self._offset, e)
            self._exhausted = True
            raise

    async def read_all(self) -> bytes:
        """Read all content into memory.

        Warning: This loads entire content into memory. Use iteration for large files.

        Returns:
            Complete content as bytes
        """
        chunks = []
        async for chunk in self:
            chunks.append(chunk)
        return b"".join(chunks)

    async def read_text(self, encoding: str = "utf-8") -> str:
        """Read all content as text.

        Args:
            encoding: Text encoding (default utf-8)

        Returns:
            Complete content as string
        """
        content = await self.read_all()
        return content.decode(encoding)

    @property
    def bytes_read(self) -> int:
        """Number of bytes read so far."""
        return self._offset


class BufferedIterator(Generic[T], AsyncIterator[T]):
    """Wrapper that buffers items from another async iterator.

    Useful for peeking ahead or rewinding within the buffer.
    """

    def __init__(
        self,
        source: AsyncIterator[T],
        buffer_size: int = 10,
    ) -> None:
        """Initialize buffered iterator.

        Args:
            source: Source async iterator
            buffer_size: Maximum items to buffer
        """
        self.source = source
        self.buffer_size = buffer_size

        self._buffer: list[T] = []
        self._index = 0
        self._exhausted = False

    def __aiter__(self) -> "BufferedIterator[T]":
        return self

    async def __anext__(self) -> T:
        # Return from buffer if available
        if self._index < len(self._buffer):
            item = self._buffer[self._index]
            self._index += 1
            return item

        if self._exhausted:
            raise StopAsyncIteration

        # Fetch from source
        try:
            item = await self.source.__anext__()

            # Add to buffer (with size limit)
            if len(self._buffer) >= self.buffer_size:
                self._buffer.pop(0)
                self._index = max(0, self._index - 1)

            self._buffer.append(item)
            self._index = len(self._buffer)

            return item

        except StopAsyncIteration:
            self._exhausted = True
            raise

    async def peek(self) -> T | None:
        """Peek at the next item without consuming it."""
        if self._index < len(self._buffer):
            return self._buffer[self._index]

        if self._exhausted:
            return None

        try:
            item = await self.source.__anext__()
            self._buffer.append(item)
            return item
        except StopAsyncIteration:
            self._exhausted = True
            return None

    def rewind(self, count: int = 1) -> int:
        """Rewind by count items within the buffer.

        Args:
            count: Number of items to rewind

        Returns:
            Actual number of items rewound
        """
        actual = min(count, self._index)
        self._index -= actual
        return actual
