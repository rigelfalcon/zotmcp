"""Timeout handling with resource cleanup.

Provides:
- Configurable timeout wrapper for async operations
- Cleanup callback support for cancelled operations
- Structured error hierarchy for ZotMCP
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Coroutine, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class ZotMCPError(Exception):
    """Base exception for ZotMCP with structured error info.

    Attributes:
        code: Machine-readable error code
        message: Human-readable error message
        details: Optional additional context
    """

    code: str
    message: str
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        if self.details:
            return f"[{self.code}] {self.message} - {self.details}"
        return f"[{self.code}] {self.message}"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {"code": self.code, "message": self.message}
        if self.details:
            result["details"] = self.details
        return result


@dataclass
class TimeoutError(ZotMCPError):
    """Operation timed out."""

    code: str = field(default="TIMEOUT", init=False)
    message: str = "Operation timed out"
    timeout_seconds: float = 0.0
    operation: str = "unknown"

    def __post_init__(self):
        self.message = (
            f"Operation '{self.operation}' timed out after {self.timeout_seconds:.1f}s"
        )
        self.details = {
            "timeout_seconds": self.timeout_seconds,
            "operation": self.operation,
        }


@dataclass
class ConnectionError(ZotMCPError):
    """Failed to connect to Zotero."""

    code: str = field(default="CONNECTION_ERROR", init=False)
    message: str = "Failed to connect to Zotero"
    host: str = ""
    port: int = 0
    reason: str = ""

    def __post_init__(self):
        if self.host:
            self.message = f"Failed to connect to Zotero at {self.host}:{self.port}"
        if self.reason:
            self.message += f": {self.reason}"
        self.details = {"host": self.host, "port": self.port, "reason": self.reason}


@dataclass
class ResourceExhaustedError(ZotMCPError):
    """Resource limit exceeded."""

    code: str = field(default="RESOURCE_EXHAUSTED", init=False)
    message: str = "Resource limit exceeded"
    resource: str = ""
    limit: int | float = 0
    current: int | float = 0

    def __post_init__(self):
        self.message = (
            f"Resource '{self.resource}' exhausted: {self.current}/{self.limit}"
        )
        self.details = {
            "resource": self.resource,
            "limit": self.limit,
            "current": self.current,
        }


@dataclass
class AuthenticationError(ZotMCPError):
    """Authentication failed."""

    code: str = field(default="AUTHENTICATION_ERROR", init=False)
    message: str = "Authentication failed"
    reason: str = ""

    def __post_init__(self):
        if self.reason:
            self.message = f"Authentication failed: {self.reason}"
        self.details = {"reason": self.reason}


@dataclass
class NotFoundError(ZotMCPError):
    """Resource not found."""

    code: str = field(default="NOT_FOUND", init=False)
    message: str = "Resource not found"
    resource_type: str = ""
    resource_id: str = ""

    def __post_init__(self):
        if self.resource_type and self.resource_id:
            self.message = f"{self.resource_type} '{self.resource_id}' not found"
        self.details = {
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
        }


@dataclass
class ValidationError(ZotMCPError):
    """Input validation failed."""

    code: str = field(default="VALIDATION_ERROR", init=False)
    message: str = "Validation failed"
    field_name: str = ""
    reason: str = ""

    def __post_init__(self):
        if self.field_name:
            self.message = f"Validation failed for '{self.field_name}'"
        if self.reason:
            self.message += f": {self.reason}"
        self.details = {"field": self.field_name, "reason": self.reason}


class TimeoutHandler:
    """Async timeout wrapper with resource cleanup.

    Wraps async operations with configurable timeouts and ensures
    cleanup callbacks are called even when operations are cancelled.

    Example:
        handler = TimeoutHandler(default_timeout=30.0)

        async def cleanup():
            await connection.close()

        result = await handler.execute(
            fetch_data(),
            timeout=10.0,
            cleanup=cleanup,
            operation="fetch_data"
        )
    """

    def __init__(self, default_timeout: float = 30.0) -> None:
        """Initialize timeout handler.

        Args:
            default_timeout: Default timeout in seconds (default 30)
        """
        self.default_timeout = default_timeout

    async def execute(
        self,
        coro: Coroutine[Any, Any, T],
        timeout: float | None = None,
        cleanup: Callable[[], Awaitable[None]] | None = None,
        operation: str = "unknown",
    ) -> T:
        """Execute coroutine with timeout and cleanup.

        Args:
            coro: Coroutine to execute
            timeout: Timeout in seconds (uses default if None)
            cleanup: Optional async cleanup function called on timeout/error
            operation: Name of operation for error messages

        Returns:
            Result of the coroutine

        Raises:
            TimeoutError: If operation times out
        """
        effective_timeout = timeout if timeout is not None else self.default_timeout

        try:
            return await asyncio.wait_for(coro, timeout=effective_timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Operation '%s' timed out after %.1fs", operation, effective_timeout
            )
            if cleanup:
                try:
                    await cleanup()
                except Exception as e:
                    logger.warning("Cleanup failed for '%s': %s", operation, e)
            raise TimeoutError(
                timeout_seconds=effective_timeout, operation=operation
            ) from None
        except asyncio.CancelledError:
            logger.debug("Operation '%s' was cancelled", operation)
            if cleanup:
                try:
                    await cleanup()
                except Exception as e:
                    logger.warning("Cleanup failed for '%s': %s", operation, e)
            raise
        except Exception:
            if cleanup:
                try:
                    await cleanup()
                except Exception as e:
                    logger.warning("Cleanup failed for '%s': %s", operation, e)
            raise

    async def execute_with_retry(
        self,
        coro_factory: Callable[[], Coroutine[Any, Any, T]],
        timeout: float | None = None,
        max_retries: int = 3,
        backoff_base: float = 1.0,
        operation: str = "unknown",
    ) -> T:
        """Execute with timeout and exponential backoff retry.

        Args:
            coro_factory: Factory function that creates the coroutine
            timeout: Timeout per attempt in seconds
            max_retries: Maximum number of retry attempts
            backoff_base: Base delay for exponential backoff
            operation: Name of operation for error messages

        Returns:
            Result of the coroutine

        Raises:
            TimeoutError: If all attempts time out
            Exception: Last exception if all retries fail
        """
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                return await self.execute(
                    coro_factory(),
                    timeout=timeout,
                    operation=f"{operation} (attempt {attempt + 1})",
                )
            except (TimeoutError, ConnectionError) as e:
                last_error = e
                if attempt < max_retries:
                    delay = backoff_base * (2**attempt)
                    logger.info(
                        "Retrying '%s' in %.1fs (attempt %d/%d)",
                        operation,
                        delay,
                        attempt + 2,
                        max_retries + 1,
                    )
                    await asyncio.sleep(delay)

        if last_error:
            raise last_error
        raise RuntimeError(f"Unexpected state in retry loop for '{operation}'")
