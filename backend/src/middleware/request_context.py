import uuid as _uuid_mod
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from fastapi import HTTPException, Request, status

from src.schemas.envelope import ResponseMeta
from src.services.idempotency_store import CachedResponse, idempotency_store


@dataclass
class RequestContext:
    request_id: str
    start_time: float
    warnings: list[str]


def create_request_context() -> RequestContext:
    return RequestContext(
        request_id=str(uuid4()),
        start_time=perf_counter(),
        warnings=[],
    )


def build_meta(context: RequestContext, api_version: str = "1.0") -> ResponseMeta:
    processing_time_ms = int((perf_counter() - context.start_time) * 1000)
    return ResponseMeta(
        api_version=api_version,
        processing_time_ms=processing_time_ms,
        timestamp=datetime.now(timezone.utc),
        warnings=context.warnings,
    )


def validate_headers(
    request: Request,
    context: RequestContext,
    *,
    validate_only: bool,
) -> dict[str, str | None]:
    idempotency_key = request.headers.get("Idempotency-Key")
    if not validate_only and not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Idempotency-Key header is REQUIRED for all write operations. "
                "Add this header to your request: 'Idempotency-Key: <uuid-v4>'. "
                "Example: 'Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000'. "
                "Generate a unique UUID for each operation. "
                "This prevents duplicate operations on retry."
            ),
        )

    if idempotency_key:
        try:
            _uuid_mod.UUID(idempotency_key)
        except (ValueError, AttributeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Idempotency-Key must be a valid UUID format "
                    f"(e.g., '550e8400-e29b-41d4-a716-446655440000'). Received: '{idempotency_key}'"
                ),
            )

    if_match = request.headers.get("If-Match")
    if not if_match:
        context.warnings.append(
            "If-Match header recommended for optimistic locking"
        )

    return {"idempotency_key": idempotency_key, "if_match": if_match}


def check_idempotency(key: str | None) -> CachedResponse | None:
    """Check if an idempotency key has a cached response.

    Returns the cached response if the key was already processed,
    or None if the key is new (or None was passed).
    Note: per-instance only on Cloud Run; cross-instance dedup
    would require Redis/DB (future work).
    """
    if key is None:
        return None
    return idempotency_store.get(key)


def save_idempotency(key: str | None, status_code: int, body: dict) -> None:
    """Save a response for an idempotency key.

    Subsequent requests with the same key will receive this cached response
    instead of re-executing the operation.
    """
    if key is None:
        return
    idempotency_store.set(key, status_code, body)
