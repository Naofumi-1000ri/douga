import uuid as _uuid_mod
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from uuid import uuid4

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
        timestamp=datetime.now(UTC),
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
        context.warnings.append("If-Match header recommended for optimistic locking")

    return {"idempotency_key": idempotency_key, "if_match": if_match}


# ---------------------------------------------------------------------------
# Legacy in-memory check/save (kept for import compatibility, no-ops now)
# ---------------------------------------------------------------------------


def check_idempotency(key: str | None) -> CachedResponse | None:
    """Legacy in-memory check.  Always returns None (no-op).

    Use check_idempotency_db() for actual cross-instance dedup.
    """
    if key is None:
        return None
    return idempotency_store.get(key)


def save_idempotency(key: str | None, status_code: int, body: dict) -> None:
    """Legacy in-memory save.  No-op.

    Use save_idempotency_db() for actual cross-instance dedup.
    """
    if key is None:
        return
    idempotency_store.set(key, status_code, body)


# ---------------------------------------------------------------------------
# DB-backed idempotency helpers (async, require a live DB session)
# ---------------------------------------------------------------------------


async def check_idempotency_db(
    key: str | None,
    db: AsyncSession,
) -> CachedResponse | None:
    """Look up an idempotency key in the database.

    Returns a CachedResponse if the key was already committed successfully,
    or None if the key is new or has never been saved.

    Only rows with response_body IS NOT NULL (i.e. the operation completed and
    its response was persisted) are treated as a cache hit.
    """
    if key is None:
        return None

    from src.models.operation import ProjectOperation

    result = await db.execute(
        select(
            ProjectOperation.response_status_code,
            ProjectOperation.response_body,
        ).where(
            ProjectOperation.idempotency_key == key,
            ProjectOperation.response_body.isnot(None),
        )
    )
    row = result.one_or_none()
    if row is None:
        return None

    return CachedResponse(
        status_code=row.response_status_code or 200,
        body=row.response_body,
    )


async def save_idempotency_db(
    key: str | None,
    status_code: int,
    body: dict,
    operation_id: _uuid_mod.UUID,
    db: AsyncSession,
) -> None:
    """Persist the response body for an idempotency key.

    Updates the ProjectOperation row created by OperationService.record_operation()
    with the response payload so subsequent requests with the same key can replay it.

    This must be called *after* the operation row is committed (or at least flushed)
    so the row exists.
    """
    if key is None:
        return

    from src.models.operation import ProjectOperation

    result = await db.execute(
        select(ProjectOperation).where(ProjectOperation.id == operation_id)
    )
    operation = result.scalar_one_or_none()
    if operation is not None:
        operation.response_status_code = status_code
        operation.response_body = body
        await db.flush()


# ---------------------------------------------------------------------------
# Idempotency gate: single call-site wrapper
# ---------------------------------------------------------------------------


async def enforce_idempotency(
    key: str | None,
    db: AsyncSession,
) -> CachedResponse | None:
    """Check whether this idempotency key was already processed.

    Returns:
        CachedResponse  — caller MUST short-circuit and return the cached response.
        None            — key is new; proceed with the operation.

    Raises:
        HTTPException(409) — a row with this key exists but has no response body yet
                             (concurrent in-flight request).  The caller should surface
                             this as a conflict and ask the client to retry.
    """
    if key is None:
        return None

    from src.models.operation import ProjectOperation

    # First try: full hit (operation finished and response saved)
    cached = await check_idempotency_db(key, db)
    if cached is not None:
        return cached

    # Second try: in-flight hit (row exists but response not yet saved)
    result = await db.execute(
        select(ProjectOperation.id).where(
            ProjectOperation.idempotency_key == key,
        )
    )
    if result.one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A request with this Idempotency-Key is already being processed. "
                "Wait for the original request to complete, then retry."
            ),
        )

    return None
