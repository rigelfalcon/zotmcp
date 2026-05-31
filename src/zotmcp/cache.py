"""In-memory TTL cache for Zotero API responses."""

import hashlib
import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ResponseCache:
    """Simple TTL cache for API responses.

    Caches GET request results with configurable TTL.
    Write operations (POST/PUT/PATCH/DELETE) bypass cache and
    invalidate related entries.
    """

    def __init__(self, default_ttl: int = 300, max_entries: int = 500):
        self._cache: dict[str, tuple[float, Any]] = {}
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._hits = 0
        self._misses = 0

    def _make_key(self, method: str, path: str, params: Optional[dict] = None) -> str:
        """Create a cache key from request parameters."""
        raw = f"{method}:{path}:{json.dumps(params, sort_keys=True) if params else ''}"
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, method: str, path: str, params: Optional[dict] = None) -> Optional[Any]:
        """Get cached response. Returns None on miss or expiry."""
        key = self._make_key(method, path, params)
        entry = self._cache.get(key)
        if entry is None:
            self._misses += 1
            return None
        expires, value = entry
        if time.time() > expires:
            del self._cache[key]
            self._misses += 1
            return None
        self._hits += 1
        return value

    def set(
        self,
        method: str,
        path: str,
        value: Any,
        params: Optional[dict] = None,
        ttl: Optional[int] = None,
    ):
        """Cache a response value."""
        if len(self._cache) >= self._max_entries:
            self._evict_expired()
            if len(self._cache) >= self._max_entries:
                # Remove oldest 20%
                sorted_keys = sorted(
                    self._cache.keys(), key=lambda k: self._cache[k][0]
                )
                for k in sorted_keys[: len(sorted_keys) // 5]:
                    del self._cache[k]

        key = self._make_key(method, path, params)
        expires = time.time() + (ttl if ttl is not None else self._default_ttl)
        self._cache[key] = (expires, value)

    def invalidate(self, pattern: Optional[str] = None):
        """Invalidate cache entries.

        Args:
            pattern: If None, clear all. Otherwise clear entries whose
                     key-generation path contained this substring.
        """
        if pattern is None:
            self._cache.clear()
            logger.debug("Cache fully invalidated")
        else:
            # We need to check against stored paths, but we only have hashes.
            # For pattern-based invalidation, clear all (simple but effective).
            # A more sophisticated approach would store path metadata.
            self._cache.clear()
            logger.debug(f"Cache invalidated (pattern: {pattern})")

    def _evict_expired(self):
        """Remove all expired entries."""
        now = time.time()
        expired = [k for k, (exp, _) in self._cache.items() if now > exp]
        for k in expired:
            del self._cache[k]

    @property
    def stats(self) -> dict:
        """Return cache statistics."""
        total = self._hits + self._misses
        return {
            "hits": self._hits,
            "misses": self._misses,
            "size": len(self._cache),
            "hit_rate": f"{self._hits / max(1, total) * 100:.1f}%",
        }

    def __repr__(self) -> str:
        s = self.stats
        return f"ResponseCache(size={s['size']}, hit_rate={s['hit_rate']})"
