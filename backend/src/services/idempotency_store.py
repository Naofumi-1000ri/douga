"""DB-backed idempotency key store.

Replaces the former in-memory implementation.  Cloud Run instances share the
same PostgreSQL database, so every instance can check and set idempotency
records consistently.

Design:
- Primary source of truth: project_operations.idempotency_key (UNIQUE partial index)
- CachedResponse is kept as a dataclass so the rest of the codebase (primarily
  request_context.py) needs no structural changes.
- The store operates synchronously from the caller's perspective; all DB I/O
  is awaitable and handled in request_context helpers.
"""

from dataclasses import dataclass


@dataclass
class CachedResponse:
    """Persisted response for idempotency replay."""

    status_code: int
    body: dict


# Kept as a lightweight sentinel so imports in request_context.py still resolve.
# The actual DB operations are in check_idempotency_db / save_idempotency_db
# (see src/middleware/request_context.py).
class _NoopIdempotencyStore:
    """Placeholder — real storage is the database (project_operations table)."""

    def get(self, key: str) -> CachedResponse | None:
        return None

    def set(self, key: str, status_code: int, body: dict) -> None:
        pass


# Legacy singleton kept so that `from src.services.idempotency_store import idempotency_store`
# continues to work anywhere it's still referenced.
idempotency_store = _NoopIdempotencyStore()
