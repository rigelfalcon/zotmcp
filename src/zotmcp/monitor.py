"""Memory monitoring with background checking.

Provides:
- Background memory usage monitoring
- Warning threshold alerts
- Memory statistics reporting
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

# Try to import psutil, fall back to basic resource module
try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    try:
        import resource

        HAS_RESOURCE = True
    except ImportError:
        HAS_RESOURCE = False


@dataclass
class MemoryStats:
    """Memory usage statistics.

    Attributes:
        rss_mb: Resident Set Size in megabytes
        vms_mb: Virtual Memory Size in megabytes
        percent: Memory usage as percentage of total system memory
        available: Whether stats are available (psutil installed)
    """

    rss_mb: float
    vms_mb: float
    percent: float
    available: bool = True

    @classmethod
    def unavailable(cls) -> "MemoryStats":
        """Create stats indicating monitoring is unavailable."""
        return cls(rss_mb=0.0, vms_mb=0.0, percent=0.0, available=False)

    def __str__(self) -> str:
        if not self.available:
            return "Memory stats unavailable (install psutil)"
        return f"RSS: {self.rss_mb:.1f}MB, VMS: {self.vms_mb:.1f}MB, {self.percent:.1f}%"


def get_memory_stats() -> MemoryStats:
    """Get current memory usage statistics.

    Returns:
        MemoryStats with current usage, or unavailable stats if psutil not installed
    """
    if HAS_PSUTIL:
        try:
            process = psutil.Process(os.getpid())
            mem_info = process.memory_info()
            mem_percent = process.memory_percent()
            return MemoryStats(
                rss_mb=mem_info.rss / (1024 * 1024),
                vms_mb=mem_info.vms / (1024 * 1024),
                percent=mem_percent,
            )
        except Exception as e:
            logger.warning("Failed to get memory stats: %s", e)
            return MemoryStats.unavailable()

    if HAS_RESOURCE:
        try:
            # resource module only provides max RSS on Unix
            usage = resource.getrusage(resource.RUSAGE_SELF)
            # maxrss is in KB on Linux, bytes on macOS
            rss_kb = usage.ru_maxrss
            if os.uname().sysname == "Darwin":
                rss_kb = rss_kb / 1024
            return MemoryStats(
                rss_mb=rss_kb / 1024,
                vms_mb=0.0,  # Not available via resource
                percent=0.0,  # Not available via resource
            )
        except Exception as e:
            logger.warning("Failed to get memory stats via resource: %s", e)
            return MemoryStats.unavailable()

    return MemoryStats.unavailable()


class MemoryMonitor:
    """Background memory monitoring with threshold alerts.

    Monitors memory usage in the background and logs warnings
    when usage exceeds the configured threshold.

    Example:
        monitor = MemoryMonitor(warning_threshold_mb=500)
        await monitor.start()

        # ... application runs ...

        stats = monitor.get_current_usage()
        print(f"Current memory: {stats}")

        await monitor.stop()
    """

    def __init__(
        self,
        warning_threshold_mb: float = 500.0,
        check_interval: float = 60.0,
        on_warning: Callable[[MemoryStats], None] | None = None,
    ) -> None:
        """Initialize memory monitor.

        Args:
            warning_threshold_mb: Memory threshold for warnings in MB (default 500)
            check_interval: Seconds between checks (default 60)
            on_warning: Optional callback when threshold exceeded
        """
        self.warning_threshold_mb = warning_threshold_mb
        self.check_interval = check_interval
        self.on_warning = on_warning

        self._task: asyncio.Task | None = None
        self._running = False
        self._last_stats: MemoryStats | None = None
        self._warning_logged = False

    async def start(self) -> None:
        """Start background memory monitoring."""
        if self._running:
            return

        if not HAS_PSUTIL and not HAS_RESOURCE:
            logger.warning(
                "Memory monitoring unavailable. Install psutil for full support."
            )
            return

        self._running = True
        self._warning_logged = False
        self._task = asyncio.create_task(self._monitor_loop())
        logger.debug(
            "Memory monitor started: threshold=%.0fMB, interval=%.0fs",
            self.warning_threshold_mb,
            self.check_interval,
        )

    async def _monitor_loop(self) -> None:
        """Background monitoring loop."""
        while self._running:
            try:
                await asyncio.sleep(self.check_interval)
                self._check_memory()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Error in memory monitor: %s", e)

    def _check_memory(self) -> None:
        """Check current memory and log warning if needed."""
        stats = get_memory_stats()
        self._last_stats = stats

        if not stats.available:
            return

        if stats.rss_mb > self.warning_threshold_mb:
            if not self._warning_logged:
                logger.warning(
                    "Memory usage high: %.1fMB (threshold: %.0fMB)",
                    stats.rss_mb,
                    self.warning_threshold_mb,
                )
                self._warning_logged = True

            if self.on_warning:
                try:
                    self.on_warning(stats)
                except Exception as e:
                    logger.warning("Error in memory warning callback: %s", e)
        else:
            # Reset warning flag when memory drops below threshold
            if self._warning_logged:
                logger.info(
                    "Memory usage returned to normal: %.1fMB", stats.rss_mb
                )
                self._warning_logged = False

    async def stop(self) -> None:
        """Stop background memory monitoring."""
        self._running = False

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.debug("Memory monitor stopped")

    def get_current_usage(self) -> MemoryStats:
        """Get current memory usage statistics.

        Returns:
            Current MemoryStats
        """
        return get_memory_stats()

    def is_above_threshold(self) -> bool:
        """Check if current memory is above warning threshold.

        Returns:
            True if memory exceeds threshold
        """
        stats = self.get_current_usage()
        return stats.available and stats.rss_mb > self.warning_threshold_mb

    @property
    def is_running(self) -> bool:
        """Whether the monitor is currently running."""
        return self._running

    async def __aenter__(self) -> "MemoryMonitor":
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.stop()
