from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from fastapi import HTTPException, Request, status

from src.schemas.envelope import ResponseMeta


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
            detail="Idempotency-Key header required",
        )

    if_match = request.headers.get("If-Match")
    if not if_match:
        context.warnings.append(
            "If-Match header recommended for optimistic locking"
        )

    return {"idempotency_key": idempotency_key, "if_match": if_match}
