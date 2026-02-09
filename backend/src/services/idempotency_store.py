"""In-memory idempotency key store with TTL.

Cloud Run instances are stateless, so this provides per-instance dedup only.
For cross-instance dedup, a Redis/DB backend would be needed (future work).
"""

import threading
import time
from dataclasses import dataclass


@dataclass
class CachedResponse:
    status_code: int
    body: dict
    created_at: float


class IdempotencyStore:
    """Thread-safe in-memory store with TTL-based expiration."""

    def __init__(self, ttl_seconds: int = 86400) -> None:  # 24h default
        self._store: dict[str, CachedResponse] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds

    def get(self, key: str) -> CachedResponse | None:
        """Get cached response for key, or None if not found/expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if time.monotonic() - entry.created_at > self._ttl:
                del self._store[key]
                return None
            return entry

    def set(self, key: str, status_code: int, body: dict) -> None:
        """Cache a response for the given key."""
        with self._lock:
            self._cleanup_expired()
            self._store[key] = CachedResponse(
                status_code=status_code,
                body=body,
                created_at=time.monotonic(),
            )

    def _cleanup_expired(self) -> None:
        """Remove expired entries (called under lock)."""
        now = time.monotonic()
        expired = [
            k for k, v in self._store.items() if now - v.created_at > self._ttl
        ]
        for k in expired:
            del self._store[k]


# Singleton instance
idempotency_store = IdempotencyStore()
