"""In-memory TTL cache for version data from external APIs."""

import time
import logging
from typing import Any

logger = logging.getLogger(__name__)


class VersionCache:
    """Simple dict-based cache with per-key TTL."""

    def __init__(self, default_ttl: int = 600):
        self._store: dict[str, tuple[float, Any]] = {}
        self._default_ttl = default_ttl

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        t = ttl if ttl is not None else self._default_ttl
        self._store[key] = (time.monotonic() + t, value)

    def clear(self) -> None:
        self._store.clear()


# Singleton instance -- 10-minute TTL by default
version_cache = VersionCache(default_ttl=600)
