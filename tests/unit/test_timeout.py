"""Unit tests for timeout handler."""

import asyncio
import pytest
from zotmcp.timeout import TimeoutHandler, TimeoutError


@pytest.mark.asyncio
async def test_timeout_success():
    """Test successful operation within timeout."""
    handler = TimeoutHandler(default_timeout=1.0)

    async def quick_task():
        await asyncio.sleep(0.01)
        return "done"

    result = await handler.execute(quick_task())
    assert result == "done"


@pytest.mark.asyncio
async def test_timeout_exceeded():
    """Test timeout exceeded."""
    handler = TimeoutHandler(default_timeout=0.1)

    async def slow_task():
        await asyncio.sleep(1.0)
        return "done"

    with pytest.raises(TimeoutError) as exc_info:
        await handler.execute(slow_task())

    assert exc_info.value.code == "TIMEOUT"


@pytest.mark.asyncio
async def test_timeout_cleanup():
    """Test cleanup callback on timeout."""
    handler = TimeoutHandler(default_timeout=0.1)
    cleanup_called = False

    async def cleanup():
        nonlocal cleanup_called
        cleanup_called = True

    async def slow_task():
        await asyncio.sleep(1.0)

    with pytest.raises(TimeoutError):
        await handler.execute(slow_task(), cleanup=cleanup)

    assert cleanup_called


@pytest.mark.asyncio
async def test_timeout_custom():
    """Test custom timeout override."""
    handler = TimeoutHandler(default_timeout=1.0)

    async def task():
        await asyncio.sleep(0.2)

    with pytest.raises(TimeoutError):
        await handler.execute(task(), timeout=0.1)
