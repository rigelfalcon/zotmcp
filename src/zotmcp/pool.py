"""Connection pool manager with resource safety.

Provides thread-safe connection pooling with:
- Semaphore-limited concurrent connections (max 10)
- Idle timeout (60s) with automatic cleanup
- Request timeout (30s) with proper cancellation
- Async context manager support
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

logger = logging.getLogger(__name__)


@dataclass
class PooledConnection:
    """A connection with metadata for pool management."""

    client: httpx.AsyncClient
    created_at: float = field(default_factory=time.monotonic)
    last_used: float = field(default_factory=time.monotonic)

    def is_idle(self, idle_timeout: float) -> bool:
        """Check if connection has been idle too long."""
        return time.monotonic() - self.last_used > idle_timeout

    def touch(self) -> None:
        """Update last used timestamp."""
        self.last_used = time.monotonic()


class ConnectionPool:
    """Thread-safe HTTP connection pool with resource safety.

    Features:
    - Limits concurrent connections via semaphore
    - Closes idle connections after timeout
    - Provides async context manager for safe acquisition/release
    - Supports graceful shutdown with timeout

    Example:
        pool = ConnectionPool(max_connections=10)
        await pool.start()

        async with pool.acquire() as client:
            response = await client.get("http://localhost:23119/api")

        await pool.close()
    """

    def __init__(
        self,
        max_connections: int = 10,
        idle_timeout: float = 60.0,
        request_timeout: float = 30.0,
        base_url: str | None = None,
    ) -> None:
        """Initialize connection pool.

        Args:
            max_connections: Maximum concurrent connections (default 10)
            idle_timeout: Seconds before idle connection is closed (default 60)
            request_timeout: Default request timeout in seconds (default 30)
            base_url: Optional base URL for all requests
        """
        self.max_connections = max_connections
        self.idle_timeout = idle_timeout
        self.request_timeout = request_timeout
        self.base_url = base_url

        self._semaphore: asyncio.Semaphore | None = None
        self._connections: list[PooledConnection] = []
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task | None = None
        self._closed = False

    async def start(self) -> None:
        """Start the connection pool and cleanup task."""
        if self._semaphore is not None:
            return

        self._semaphore = asyncio.Semaphore(self.max_connections)
        self._closed = False
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.debug(
            "Connection pool started: max=%d, idle_timeout=%.1fs",
            self.max_connections,
            self.idle_timeout,
        )

    async def _cleanup_loop(self) -> None:
        """Background task to close idle connections."""
        while not self._closed:
            try:
                await asyncio.sleep(self.idle_timeout / 2)
                await self._cleanup_idle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Error in cleanup loop: %s", e)

    async def _cleanup_idle(self) -> None:
        """Close connections that have been idle too long."""
        async with self._lock:
            active = []
            for conn in self._connections:
                if conn.is_idle(self.idle_timeout):
                    logger.debug("Closing idle connection")
                    await self._close_client(conn.client)
                else:
                    active.append(conn)
            self._connections = active

    async def _close_client(self, client: httpx.AsyncClient) -> None:
        """Safely close an HTTP client."""
        try:
            await client.aclose()
        except Exception as e:
            logger.warning("Error closing client: %s", e)

    def _create_client(self) -> httpx.AsyncClient:
        """Create a new HTTP client with configured settings."""
        timeout = httpx.Timeout(self.request_timeout, connect=10.0)
        return httpx.AsyncClient(
            base_url=self.base_url or "",
            timeout=timeout,
            follow_redirects=True,
        )

    @asynccontextmanager
    async def acquire(
        self, timeout: float | None = None
    ) -> AsyncIterator[httpx.AsyncClient]:
        """Acquire a connection from the pool.

        Args:
            timeout: Optional override for request timeout

        Yields:
            httpx.AsyncClient ready for use

        Raises:
            RuntimeError: If pool is closed or not started
        """
        if self._closed:
            raise RuntimeError("Connection pool is closed")
        if self._semaphore is None:
            raise RuntimeError("Connection pool not started. Call start() first.")

        await self._semaphore.acquire()
        client: httpx.AsyncClient | None = None

        try:
            # Try to reuse an existing connection
            async with self._lock:
                for conn in self._connections:
                    if not conn.is_idle(self.idle_timeout):
                        conn.touch()
                        client = conn.client
                        self._connections.remove(conn)
                        break

            # Create new if none available
            if client is None:
                client = self._create_client()
                logger.debug("Created new connection")

            # Apply timeout override if specified
            if timeout is not None:
                client.timeout = httpx.Timeout(timeout, connect=10.0)

            yield client

            # Return to pool if still valid
            async with self._lock:
                if not self._closed:
                    self._connections.append(
                        PooledConnection(client=client, last_used=time.monotonic())
                    )
                    client = None  # Don't close it

        finally:
            self._semaphore.release()
            if client is not None:
                await self._close_client(client)

    async def close(self, timeout: float = 5.0) -> None:
        """Close all connections and stop the pool.

        Args:
            timeout: Maximum seconds to wait for cleanup (default 5)
        """
        self._closed = True

        # Cancel cleanup task
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await asyncio.wait_for(self._cleanup_task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._cleanup_task = None

        # Close all connections with timeout
        async with self._lock:
            close_tasks = [
                self._close_client(conn.client) for conn in self._connections
            ]
            self._connections.clear()

        if close_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*close_tasks, return_exceptions=True),
                    timeout=timeout,
                )
                logger.debug("Closed %d connections", len(close_tasks))
            except asyncio.TimeoutError:
                logger.warning(
                    "Timeout closing connections after %.1fs", timeout
                )

        self._semaphore = None
        logger.debug("Connection pool closed")

    @property
    def active_connections(self) -> int:
        """Number of connections currently in the pool."""
        return len(self._connections)

    async def __aenter__(self) -> "ConnectionPool":
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()
