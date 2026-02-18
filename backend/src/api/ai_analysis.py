"""V1 API - Timeline Analysis endpoints.

Provides composition quality analysis and improvement suggestions
for AI agents interacting with the timeline.
"""

import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
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
# Request models
# =============================================================================


class SuggestionsFilterRequest(BaseModel):
    """Optional filter parameters for the suggestions endpoint.

    All fields are optional. When omitted, all suggestions are returned.
    """

    priority: Literal["high", "medium", "low"] | None = Field(
        default=None,
        description="Filter by priority level: high, medium, or low",
    )
    category: str | None = Field(
        default=None,
        description="Filter by category: gap, missing_background, low_narration, "
        "missing_text, missing_text_section, missing_narration_section, "
        "missing_bgm, silence, pacing, etc.",
    )
    limit: int | None = Field(
        default=None,
        ge=1,
        le=100,
        description="Maximum number of suggestions to return (1-100)",
    )


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
    summary="Analyze timeline composition quality (full report)",
    description=(
        "Full composition analysis. Returns quality metrics, gap analysis, "
        "pacing analysis, audio coverage, layer coverage, and actionable "
        "improvement suggestions. For suggestions-only (lightweight), "
        "use /analysis/suggestions instead."
    ),
    include_in_schema=False,
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

    For a lightweight version (suggestions + quality_score only),
    use /analysis/suggestions.
    """
    context = create_request_context()
    logger.info("ai_analysis.composition project=%s", project_id)

    try:
        edit_ctx = await get_edit_context(project_id, current_user, db, x_edit_session)
        timeline_data = edit_ctx.timeline_data

        # Build asset map for richer analysis
        asset_map = await _build_asset_map(db, project_id)

        analyzer = TimelineAnalyzer(
            timeline_data, asset_map=asset_map, project_id=str(project_id)
        )
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
    summary="Get improvement suggestions only (lightweight)",
    description=(
        "Lightweight alternative to /analysis/composition. "
        "Returns only suggestions and quality_score, skipping gap_analysis, "
        "pacing_analysis, audio_analysis, and layer_coverage. "
        "Use this when you only need actionable next-steps without the full report. "
        "Supports optional filter parameters in the request body: "
        "priority (high/medium/low), category (gap/pacing/etc.), limit (1-100). "
        "An empty body or {} returns all suggestions."
    ),
    include_in_schema=False,
)
async def get_suggestions(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    filters: SuggestionsFilterRequest | None = None,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Lightweight alternative to /analysis/composition.

    Returns suggestions + quality_score only (skips gap_analysis,
    pacing_analysis, audio_analysis, layer_coverage).

    Use /analysis/composition for a comprehensive report.
    Use /analysis/suggestions when you only need actionable items.

    Optional request body filters:
    - priority: "high", "medium", or "low" — return only matching priority
    - category: "gap", "pacing", "missing_background", etc. — return only matching category
    - limit: 1-100 — cap the number of returned suggestions

    Each suggestion includes:
    - priority: high/medium/low
    - category: gap, missing_background, low_narration, etc.
    - message: Human-readable description
    - suggested_operation: API endpoint and parameters to fix the issue
    """
    context = create_request_context()
    if filters is None:
        filters = SuggestionsFilterRequest()
    logger.info(
        "ai_analysis.suggestions project=%s priority=%s category=%s limit=%s",
        project_id,
        filters.priority,
        filters.category,
        filters.limit,
    )

    try:
        edit_ctx = await get_edit_context(project_id, current_user, db, x_edit_session)
        timeline_data = edit_ctx.timeline_data

        asset_map = await _build_asset_map(db, project_id)

        analyzer = TimelineAnalyzer(
            timeline_data, asset_map=asset_map, project_id=str(project_id)
        )
        suggestions = analyzer.generate_suggestions()
        quality_score = analyzer.calculate_quality_score()

        # Apply filters (post-processing)
        total_before_filter = len(suggestions)
        if filters.priority:
            suggestions = [s for s in suggestions if s.get("priority") == filters.priority]
        if filters.category:
            suggestions = [s for s in suggestions if s.get("category") == filters.category]
        if filters.limit is not None:
            suggestions = suggestions[: filters.limit]

        result = {
            "suggestions": suggestions,
            "quality_score": quality_score,
            "suggestion_count": len(suggestions),
        }
        # Include total count when filters are active so the agent knows
        if filters.priority or filters.category or filters.limit:
            result["total_before_filter"] = total_before_filter

        return _envelope_success(context, result)

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


# =============================================================================
# POST /projects/{project_id}/analysis/sections
# =============================================================================


@router.post(
    "/projects/{project_id}/analysis/sections",
    response_model=EnvelopeResponse,
    summary="Detect timeline sections",
    description=(
        "Automatically detect logical sections/segments in the timeline "
        "based on content gaps, markers, and background changes."
    ),
    include_in_schema=False,
)
async def detect_sections(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Detect logical sections in the timeline.

    Sections are identified by:
    - Gaps (>500ms) in the primary content layer
    - Explicit marker positions
    - Background clip boundaries

    Each section includes:
    - start_ms / end_ms / duration_ms
    - clip_ids that overlap the section
    - has_narration / has_background / has_text flags
    - suggested_improvements for incomplete sections
    """
    context = create_request_context()
    logger.info("ai_analysis.sections project=%s", project_id)

    try:
        edit_ctx = await get_edit_context(project_id, current_user, db, x_edit_session)
        timeline_data = edit_ctx.timeline_data

        asset_map = await _build_asset_map(db, project_id)

        analyzer = TimelineAnalyzer(
            timeline_data, asset_map=asset_map, project_id=str(project_id)
        )
        sections = analyzer.detect_sections()

        return _envelope_success(context, {
            "sections": sections,
            "section_count": len(sections),
            "project_duration_ms": analyzer.project_duration_ms,
        })

    except HTTPException as exc:
        logger.warning(
            "ai_analysis.sections failed project=%s: %s",
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
# POST /projects/{project_id}/analysis/audio-balance
# =============================================================================


@router.post(
    "/projects/{project_id}/analysis/audio-balance",
    response_model=EnvelopeResponse,
    summary="Analyze audio track balance and quality",
    description=(
        "Returns detailed audio analysis including volume consistency, "
        "ducking status, silent intervals, and recommendations."
    ),
    include_in_schema=False,
)
async def analyze_audio_balance(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[str | None, Header(alias="X-Edit-Session")] = None,
) -> EnvelopeResponse | JSONResponse:
    """Detailed audio balance analysis.

    Returns per-track analysis with:
    - clip_count, total_duration_ms, coverage_pct
    - avg_volume, volume_range (min/max)
    - has_ducking flag and per-track issues (volume_inconsistency)

    Cross-track issues:
    - no_bgm: BGM track is empty or missing
    - narration_without_ducking: Overlapping narration/BGM without auto-duck
    - audio_video_misalignment: Video clips with group_id lacking audio counterpart

    Also returns silent_intervals, recommendations, and audio_score (0-100).
    """
    context = create_request_context()
    logger.info("ai_analysis.audio_balance project=%s", project_id)

    try:
        edit_ctx = await get_edit_context(project_id, current_user, db, x_edit_session)
        timeline_data = edit_ctx.timeline_data

        asset_map = await _build_asset_map(db, project_id)

        analyzer = TimelineAnalyzer(
            timeline_data, asset_map=asset_map, project_id=str(project_id)
        )
        result = analyzer.analyze_audio_balance()

        return _envelope_success(context, result)

    except HTTPException as exc:
        logger.warning(
            "ai_analysis.audio_balance failed project=%s: %s",
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
# GET /agent-guide
# =============================================================================


@router.get(
    "/agent-guide",
    response_model=EnvelopeResponse,
    summary="Get AI agent usage guide",
    description=(
        "Returns a structured guide for AI agents on how to effectively "
        "use the V1 API."
    ),
    include_in_schema=False,
)
async def get_agent_guide(
    current_user: CurrentUser,
) -> EnvelopeResponse:
    """Return a structured guide for AI agents.

    Covers recommended workflow, key concepts, common patterns,
    error recovery strategies, and token optimization tips.
    """
    context = create_request_context()
    logger.info("ai_analysis.agent_guide")

    guide = {
        "guide_version": "1.0",
        "recommended_workflow": [
            "1. GET /capabilities -- Discover available operations and request formats",
            "2. GET /projects/{id}/assets -- List available assets",
            "3. GET /projects/{id}/summary -- Get project overview (L1, ~300 tokens)",
            "4. GET /projects/{id}/timeline-overview -- Get detailed timeline state (L2.5, ~2000 tokens)",
            "5. POST /analysis/composition -- Analyze quality and get improvement suggestions",
            "6. Execute suggested operations (add_clip, semantic ops, batch ops)",
            "7. POST /analysis/composition -- Re-analyze to verify improvement",
            "8. GET /timeline-overview?include_snapshot=true -- Visual verification",
        ],
        "key_concepts": {
            "layers": (
                "Video layers stack from bottom (background) to top (text). "
                "5 types: background, content, avatar, effects, text."
            ),
            "audio_tracks": (
                "Separate from video layers. "
                "Types: narration, bgm, se, video."
            ),
            "group_id": (
                "Links video and audio clips. "
                "Operations on one propagate to the other."
            ),
            "semantic_operations": (
                "High-level operations (close_all_gaps, add_text_with_timing, etc.) "
                "that handle complex logic in one call."
            ),
            "batch_operations": (
                "Execute up to 20 operations atomically. "
                "Use rollback_on_failure for safety."
            ),
            "idempotency": (
                "All write operations require an Idempotency-Key header (UUID) "
                "to prevent duplicates."
            ),
            "edit_context": (
                "Timeline edits target the default sequence. "
                "Use X-Edit-Session header for specific sequences."
            ),
        },
        "common_patterns": {
            "add_video_with_audio": {
                "description": (
                    "Adding a video clip automatically places linked audio "
                    "on narration track"
                ),
                "steps": [
                    "POST /clips with asset_id -> video clip + auto audio clip created",
                    "Use include_audio: false in options to skip audio auto-placement",
                ],
            },
            "improve_pacing": {
                "description": "Analyze and fix timeline pacing issues",
                "steps": [
                    "POST /analysis/composition -> check pacing_analysis",
                    "POST /semantic close_all_gaps for layers with gaps",
                    "POST /semantic distribute_evenly for even spacing",
                ],
                "example_bodies": {
                    "close_all_gaps": {
                        "semantic": {
                            "operation": "close_all_gaps",
                            "target_layer_id": "<layer-id>",
                        }
                    },
                    "distribute_evenly": {
                        "semantic": {
                            "operation": "distribute_evenly",
                            "target_layer_id": "<layer-id>",
                        }
                    },
                },
            },
            "add_subtitles": {
                "description": "Add text overlays synced to existing clips",
                "steps": [
                    "GET /timeline-overview -> find clips that need subtitles",
                    "POST /semantic add_text_with_timing for each clip",
                    "PATCH /clips/{id}/text-style to customize appearance",
                ],
                "example_body": {
                    "semantic": {
                        "operation": "add_text_with_timing",
                        "target_clip_id": "<clip-id>",
                        "parameters": {"text_content": "Subtitle text"},
                    }
                },
            },
            "safe_batch_edit": {
                "description": "Make multiple changes safely with rollback",
                "steps": [
                    "POST /batch with validate_only: true -> check for errors",
                    "POST /preview-diff -> see exactly what would change",
                    "POST /batch with rollback_on_failure: true -> execute safely",
                    "If issues: POST /operations/{id}/rollback to undo",
                ],
            },
        },
        "error_recovery": {
            "validation_error": (
                "Check request_formats in /capabilities for correct body structure"
            ),
            "clip_not_found": (
                "Use GET /timeline-overview to find valid clip IDs. "
                "Short prefix matching is supported."
            ),
            "operation_failed": (
                "Check response hints for next steps. "
                "Use POST /operations/{id}/rollback if available."
            ),
            "idempotency_missing": (
                "Add Idempotency-Key header with a UUID to all write requests."
            ),
        },
        "token_optimization": {
            "description": "Use tiered data access to minimize token usage",
            "levels": {
                "L1_summary": "GET /summary -- ~300 tokens, project overview",
                "L2_structure": "GET /structure -- ~800 tokens, layer/track structure",
                "L2.5_overview": (
                    "GET /timeline-overview -- ~2000 tokens, full clip details "
                    "(add ?include_snapshot=true for visual snapshot, ~65K tokens)"
                ),
                "L3_detail": (
                    "GET /clips/{id} -- ~400 tokens per clip, "
                    "full clip with neighbors"
                ),
            },
        },
    }

    return _envelope_success(context, guide)
