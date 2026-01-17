"""Safety tests for resource management."""

import asyncio
import pytest
from zotmcp.pool import ConnectionPool
from zotmcp.monitor import MemoryMonitor


@pytest.mark.asyncio
async def test_no_connection_leak():
    """Test that connections are properly released."""
    pool = ConnectionPool(max_connections=5)

    # Start the pool first
    await pool.start()

    # Acquire and release many times
    for _ in range(100):
        async with pool.acquire() as client:
            pass

    # Check active connections
    assert pool.active_connections <= 5

    await pool.close()


@pytest.mark.asyncio
async def test_timeout_cleanup():
    """Test that timeouts properly cleanup resources."""
    from zotmcp.timeout import TimeoutHandler, TimeoutError

    handler = TimeoutHandler(default_timeout=0.1)
    cleanup_count = 0

    async def cleanup():
        nonlocal cleanup_count
        cleanup_count += 1

    async def slow_task():
        await asyncio.sleep(1.0)

    # Run multiple timeouts
    for _ in range(10):
        try:
            await handler.execute(slow_task(), cleanup=cleanup)
        except TimeoutError:
            pass

    assert cleanup_count == 10


@pytest.mark.asyncio
async def test_memory_monitor():
    """Test memory monitoring."""
    monitor = MemoryMonitor(warning_threshold_mb=100.0, check_interval=0.1)

    await monitor.start()
    await asyncio.sleep(0.3)  # Let it run a few checks
    await monitor.stop()

    # Should complete without errors
    assert True


@pytest.mark.asyncio
async def test_iterator_memory_limit():
    """Test that iterators respect memory limits."""
    from zotmcp.iterators import PaginatedIterator

    # Create large dataset
    items = list(range(10000))

    async def fetch_page(start: int, limit: int):
        return items[start : start + limit]

    # Iterator should truncate at max_items
    iterator = PaginatedIterator(fetch_page, page_size=100, max_items=1000)

    count = 0
    async for _ in iterator:
        count += 1

    assert count == 1000
    assert iterator.info.was_truncated
