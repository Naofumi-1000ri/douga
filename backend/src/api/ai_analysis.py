"""V1 API - Timeline Analysis endpoints.

Provides composition quality analysis and improvement suggestions
for AI agents interacting with the timeline.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select

from src.api.deps import CurrentUser, DbSession, get_edit_context
from src.middleware.request_context import (
    RequestContext,
    build_meta,
    create_request_context,
)
from src.models.asset import Asset
from src.schemas.envelope import EnvelopeResponse, ErrorInfo, ResponseMeta
from src.services.timeline_analysis import TimelineAnalyzer

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# Helpers (self-contained, no dependency on ai_v1.py)
# =============================================================================


def _envelope_success(context: RequestContext, data: object) -> EnvelopeResponse:
    meta: ResponseMeta = build_meta(context)
    return EnvelopeResponse(
        request_id=context.request_id,
        data=data,
        meta=meta,
    )


def _envelope_error(
    context: RequestContext,
    *,
    code: str,
    message: str,
    status_code: int,
) -> JSONResponse:
    from src.constants.error_codes import get_error_spec

    meta: ResponseMeta = build_meta(context)
    spec = get_error_spec(code)
    error = ErrorInfo(
        code=code,
        message=message,
        retryable=spec.get("retryable", False),
        suggested_fix=spec.get("suggested_fix"),
    )
    envelope = EnvelopeResponse(
        request_id=context.request_id,
        error=error,
        meta=meta,
    )
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(envelope.model_dump(exclude_none=True)),
    )


def _http_error_code(status_code: int) -> str:
    """Map HTTP status code to V1 error code."""
    _mapping = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "PROJECT_NOT_FOUND",
        409: "CONCURRENT_MODIFICATION",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
    }
    return _mapping.get(status_code, "HTTP_ERROR")


async def _build_asset_map(db: "AsyncSession", project_id: UUID) -> dict[str, dict]:  # noqa: F821
    """Build asset_id -> {name, type, subtype, duration_ms} map for a project."""
    result = await db.execute(
        select(Asset).where(Asset.project_id == project_id)
    )
    assets = result.scalars().all()
    return {
        str(asset.id): {
            "name": asset.name,
            "type": asset.type,
            "subtype": asset.subtype,
            "duration_ms": asset.duration_ms,
        }
        for asset in assets
    }


# =============================================================================
# POST /projects/{project_id}/analysis/composition
# =============================================================================


@router.post(
    "/projects/{project_id}/analysis/composition",
    response_model=EnvelopeResponse,
    summary="Analyze timeline composition quality",
    description=(
        "Returns quality metrics, gap analysis, pacing analysis, "
        "audio coverage, layer coverage, and actionable improvement suggestions."
    ),
)
async def analyze_composition(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Full composition analysis.

    Runs all quality checks and returns a comprehensive report including:
    - Gap analysis across all layers and audio tracks
    - Pacing analysis (clip density and duration distribution)
    - Audio analysis (narration/BGM coverage, silent intervals)
    - Layer coverage percentages
    - Actionable suggestions with suggested API operations
    - Overall quality score (0-100)
    """
    context = create_request_context()
    logger.info("ai_analysis.composition project=%s", project_id)

    try:
        edit_ctx = await get_edit_context(project_id, current_user, db, x_edit_session)
        timeline_data = edit_ctx.timeline_data

        # Build asset map for richer analysis
        asset_map = await _build_asset_map(db, project_id)

        analyzer = TimelineAnalyzer(timeline_data, asset_map=asset_map)
        result = analyzer.analyze_all()

        return _envelope_success(context, result)

    except HTTPException as exc:
        logger.warning(
            "ai_analysis.composition failed project=%s: %s",
            project_id,
            exc.detail,
        )
        return _envelope_error(
            context,
            code=_http_error_code(exc.status_code),
            message=str(exc.detail),
            status_code=exc.status_code,
        )


# =============================================================================
# POST /projects/{project_id}/analysis/suggestions
# =============================================================================


@router.post(
    "/projects/{project_id}/analysis/suggestions",
    response_model=EnvelopeResponse,
    summary="Get improvement suggestions only",
    description=(
        "Returns actionable suggestions with suggested API operations. "
        "Lighter than full composition analysis."
    ),
)
async def get_suggestions(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Suggestions only (lighter than full analysis).

    Returns a list of prioritized suggestions with:
    - priority: high/medium/low
    - category: gap, missing_background, low_narration, etc.
    - message: Human-readable description
    - suggested_operation: API endpoint and parameters to fix the issue
    """
    context = create_request_context()
    logger.info("ai_analysis.suggestions project=%s", project_id)

    try:
        edit_ctx = await get_edit_context(project_id, current_user, db, x_edit_session)
        timeline_data = edit_ctx.timeline_data

        asset_map = await _build_asset_map(db, project_id)

        analyzer = TimelineAnalyzer(timeline_data, asset_map=asset_map)
        suggestions = analyzer.generate_suggestions()
        quality_score = analyzer.calculate_quality_score()

        return _envelope_success(context, {
            "suggestions": suggestions,
            "quality_score": quality_score,
            "suggestion_count": len(suggestions),
        })

    except HTTPException as exc:
        logger.warning(
            "ai_analysis.suggestions failed project=%s: %s",
            project_id,
            exc.detail,
        )
        return _envelope_error(
            context,
            code=_http_error_code(exc.status_code),
            message=str(exc.detail),
            status_code=exc.status_code,
        )
