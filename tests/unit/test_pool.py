"""Unit tests for connection pool."""

import asyncio
import pytest
from zotmcp.pool import ConnectionPool


@pytest.mark.asyncio
async def test_pool_acquire_release():
    """Test basic acquire and release."""
    pool = ConnectionPool(max_connections=2)
    await pool.start()

    async with pool.acquire() as client:
        assert client is not None

    await pool.close()


@pytest.mark.asyncio
async def test_pool_max_connections():
    """Test connection limit enforcement."""
    pool = ConnectionPool(max_connections=2)
    await pool.start()

    # Acquire 2 connections
    async with pool.acquire() as c1:
        async with pool.acquire() as c2:
            # Third should timeout
            with pytest.raises(asyncio.TimeoutError):
                async with asyncio.timeout(0.1):
                    async with pool.acquire() as c3:
                        pass

    await pool.close()


@pytest.mark.asyncio
async def test_pool_reuse():
    """Test connection reuse."""
    pool = ConnectionPool(max_connections=1)
    await pool.start()

    # First acquire
    async with pool.acquire() as c1:
        client_id_1 = id(c1)

    # Second acquire should reuse
    async with pool.acquire() as c2:
        client_id_2 = id(c2)

    assert client_id_1 == client_id_2
    await pool.close()


@pytest.mark.asyncio
async def test_pool_cleanup():
    """Test idle connection cleanup."""
    pool = ConnectionPool(max_connections=2, idle_timeout=0.1)
    await pool.start()

    async with pool.acquire() as c1:
        pass

    # Wait for cleanup
    await asyncio.sleep(0.2)

    # Pool should still work after cleanup
    async with pool.acquire() as c2:
        assert c2 is not None

    await pool.close()
