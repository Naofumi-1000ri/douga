"""Preview API endpoints — event points, frame sampling, composition validation.

These endpoints support AI-driven visual inspection of timelines without
requiring full renders. Key use cases:

1. get_event_points: Detect key moments for targeted inspection
2. sample_frame: Render a single preview frame at a specific time
3. sample_event_points: Auto-detect + sample frames in one call
4. validate_composition: Check composition rules without rendering
"""

import logging
import os
import shutil
import tempfile
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, status
from sqlalchemy import select

from src.api.access import get_accessible_project
from src.api.deps import CurrentUser, DbSession, get_edit_context
from src.models.asset import Asset
from src.models.project import Project
from src.schemas.preview import (
    ActiveClipInfo,
    EventPointsRequest,
    EventPointsResponse,
    EventPoint,
    SampleEventPointsRequest,
    SampleEventPointsResponse,
    SampledEventPoint,
    SampleFrameRequest,
    SampleFrameResponse,
    ValidateCompositionRequest,
    ValidateCompositionResponse,
    ValidationIssue,
)
from src.services.composition_validator import CompositionValidator
from src.services.event_detector import EventDetector
from src.services.frame_sampler import FrameSampler
from src.services.storage_service import StorageService

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_project(project_id: UUID, current_user: CurrentUser, db: DbSession) -> Project:
    """Get project with access check."""
    return await get_accessible_project(project_id, current_user.id, db)


async def _resolve_timeline(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: str | None = None,
) -> tuple["Project", dict]:
    """Resolve project and timeline data, using sequence if edit token provided."""
    ctx = await get_edit_context(project_id, current_user, db, x_edit_session)
    timeline = ctx.timeline_data
    if not timeline:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No timeline data in project",
        )
    return ctx.project, timeline


async def _download_assets(
    timeline_data: dict,
    db: DbSession,
    temp_dir: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Download all assets referenced in timeline to local temp files.

    Returns:
        Tuple of (asset_id -> local file path, asset_id -> asset name)
    """
    asset_ids: set[str] = set()
    for layer in timeline_data.get("layers", []):
        for clip in layer.get("clips", []):
            if clip.get("asset_id"):
                asset_ids.add(str(clip["asset_id"]))

    if not asset_ids:
        return {}, {}

    result = await db.execute(
        select(Asset).where(Asset.id.in_([UUID(aid) for aid in asset_ids]))
    )
    assets_db = {str(a.id): a for a in result.scalars().all()}

    storage = StorageService()
    assets_local: dict[str, str] = {}
    asset_name_map: dict[str, str] = {}
    assets_dir = os.path.join(temp_dir, "assets")
    os.makedirs(assets_dir, exist_ok=True)

    for asset_id, asset in assets_db.items():
        ext = asset.storage_key.rsplit(".", 1)[-1] if "." in asset.storage_key else ""
        local_path = os.path.join(assets_dir, f"{asset_id}.{ext}")
        await storage.download_file(asset.storage_key, local_path)
        assets_local[asset_id] = local_path
        asset_name_map[asset_id] = asset.name

    return assets_local, asset_name_map


# =============================================================================
# Event Points
# =============================================================================


@router.post(
    "/projects/{project_id}/preview/event-points",
    response_model=EventPointsResponse,
)
async def get_event_points(
    project_id: UUID,
    request: EventPointsRequest,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[Optional[str], Header(alias="X-Edit-Session")] = None,
) -> EventPointsResponse:
    """Detect key event points in the timeline for AI inspection.

    Analyzes clip boundaries, audio starts, section changes, and silence gaps.
    Returns a list of time positions with event types for targeted sampling.
    """
    project, timeline = await _resolve_timeline(project_id, current_user, db, x_edit_session)

    detector = EventDetector(timeline)
    events = detector.detect_all(
        include_visual=request.include_visual,
        include_audio=request.include_audio,
        min_gap_ms=request.min_gap_ms,
    )

    return EventPointsResponse(
        project_id=str(project_id),
        event_points=[
            EventPoint(
                time_ms=e.time_ms,
                event_type=e.event_type,
                description=e.description,
                layer=e.layer,
                clip_id=e.clip_id,
                metadata=e.metadata,
            )
            for e in events
        ],
        total_events=len(events),
        duration_ms=timeline.get("duration_ms", 0),
    )


# =============================================================================
# Frame Sampling
# =============================================================================


@router.post(
    "/projects/{project_id}/preview/sample-frame",
    response_model=SampleFrameResponse,
)
async def sample_frame(
    project_id: UUID,
    request: SampleFrameRequest,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[Optional[str], Header(alias="X-Edit-Session")] = None,
) -> SampleFrameResponse:
    """Render a single preview frame at the specified time.

    Produces a low-resolution JPEG image for AI visual inspection.
    Typical response size: ~30-80KB for 640x360 resolution.
    """
    project, timeline = await _resolve_timeline(project_id, current_user, db, x_edit_session)

    temp_dir = tempfile.mkdtemp(prefix="douga_preview_")

    try:
        # Download assets
        assets_local, asset_name_map = await _download_assets(timeline, db, temp_dir)

        # Sample the frame
        sampler = FrameSampler(
            timeline_data=timeline,
            assets=assets_local,
            asset_name_map=asset_name_map,
            project_width=project.width,
            project_height=project.height,
            project_fps=project.fps,
        )

        result = await sampler.sample_frame(
            time_ms=request.time_ms,
            resolution=request.resolution,
        )

        return SampleFrameResponse(
            time_ms=result["time_ms"],
            resolution=result["resolution"],
            frame_base64=result["frame_base64"],
            size_bytes=result["size_bytes"],
            active_clips=result.get("active_clips", []),
        )

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# =============================================================================
# Combined Event Point Sampling
# =============================================================================


@router.post(
    "/projects/{project_id}/preview/sample-event-points",
    response_model=SampleEventPointsResponse,
)
async def sample_event_points(
    project_id: UUID,
    request: SampleEventPointsRequest,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[Optional[str], Header(alias="X-Edit-Session")] = None,
) -> SampleEventPointsResponse:
    """Auto-detect event points and render preview frames at each.

    Combines event detection + frame sampling in one call.
    AI can inspect all key moments with a single request.
    """
    project, timeline = await _resolve_timeline(project_id, current_user, db, x_edit_session)

    # Step 1: Detect event points
    detector = EventDetector(timeline)
    events = detector.detect_all(
        include_audio=request.include_audio,
        min_gap_ms=request.min_gap_ms,
    )

    # Step 2: Select events to sample (prioritize diverse types, limit count)
    selected_events = _select_diverse_events(events, request.max_samples)

    if not selected_events:
        return SampleEventPointsResponse(
            project_id=str(project_id),
            samples=[],
            total_events=len(events),
            sampled_count=0,
        )

    temp_dir = tempfile.mkdtemp(prefix="douga_preview_")

    try:
        import time as time_mod
        t0 = time_mod.monotonic()

        # Download assets once
        print(f"[SAMPLE-EVENT-POINTS] Downloading assets for {project_id}...", flush=True)
        assets_local, asset_name_map = await _download_assets(timeline, db, temp_dir)
        dl_elapsed = time_mod.monotonic() - t0
        print(f"[SAMPLE-EVENT-POINTS] Downloaded {len(assets_local)} assets in {dl_elapsed:.1f}s", flush=True)

        # Create sampler
        sampler = FrameSampler(
            timeline_data=timeline,
            assets=assets_local,
            asset_name_map=asset_name_map,
            project_width=project.width,
            project_height=project.height,
            project_fps=project.fps,
        )

        # Step 3: Sample frames at each event point
        samples: list[SampledEventPoint] = []
        for i, event in enumerate(selected_events):
            try:
                print(
                    f"[SAMPLE-EVENT-POINTS] Sampling {i+1}/{len(selected_events)} "
                    f"at {event.time_ms}ms ({event.event_type})...",
                    flush=True,
                )
                result = await sampler.sample_frame(
                    time_ms=event.time_ms,
                    resolution=request.resolution,
                )
                samples.append(SampledEventPoint(
                    time_ms=event.time_ms,
                    event_type=event.event_type,
                    description=event.description,
                    frame_base64=result["frame_base64"],
                    active_clips=result.get("active_clips", []),
                ))
            except Exception as e:
                logger.warning(f"Failed to sample frame at {event.time_ms}ms: {e}")

        total_elapsed = time_mod.monotonic() - t0
        print(
            f"[SAMPLE-EVENT-POINTS] Complete: {len(samples)}/{len(selected_events)} "
            f"frames in {total_elapsed:.1f}s",
            flush=True,
        )

        return SampleEventPointsResponse(
            project_id=str(project_id),
            samples=samples,
            total_events=len(events),
            sampled_count=len(samples),
        )

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# =============================================================================
# Composition Validation
# =============================================================================


@router.post(
    "/projects/{project_id}/preview/validate",
    response_model=ValidateCompositionResponse,
)
async def validate_composition(
    project_id: UUID,
    request: ValidateCompositionRequest,
    current_user: CurrentUser,
    db: DbSession,
    x_edit_session: Annotated[Optional[str], Header(alias="X-Edit-Session")] = None,
) -> ValidateCompositionResponse:
    """Validate timeline composition rules without rendering.

    Checks for common issues like overlapping clips, missing assets,
    safe zone violations, and audio-visual sync problems.
    """
    project, timeline = await _resolve_timeline(project_id, current_user, db, x_edit_session)

    # Get known asset IDs and their dimensions for accurate safe zone checks
    result = await db.execute(
        select(Asset.id, Asset.width, Asset.height).where(Asset.project_id == project_id)
    )
    asset_rows = result.all()
    asset_ids = {str(aid) for (aid, _, _) in asset_rows}
    asset_dimensions = {
        str(aid): (w, h)
        for (aid, w, h) in asset_rows
        if w is not None and h is not None
    }

    validator = CompositionValidator(
        timeline_data=timeline,
        project_width=project.width,
        project_height=project.height,
        asset_ids=asset_ids,
        asset_dimensions=asset_dimensions,
    )

    issues = validator.validate(rules=request.rules)

    error_count = sum(1 for i in issues if i.severity == "error")
    warning_count = sum(1 for i in issues if i.severity == "warning")

    return ValidateCompositionResponse(
        project_id=str(project_id),
        is_valid=error_count == 0,
        issues=[
            ValidationIssue(
                rule=i.rule,
                severity=i.severity,
                message=i.message,
                time_ms=i.time_ms,
                clip_id=i.clip_id,
                layer=i.layer,
                suggestion=i.suggestion,
                details=i.details,
            )
            for i in issues
        ],
        total_issues=len(issues),
        errors=error_count,
        warnings=warning_count,
    )


# =============================================================================
# Helpers
# =============================================================================


def _select_diverse_events(
    events: list,
    max_samples: int,
) -> list:
    """Select a diverse set of events for sampling.

    Prioritizes:
    1. Section boundaries
    2. One of each event type
    3. Evenly spaced remaining events
    """
    if len(events) <= max_samples:
        return events

    # Priority event types
    priority_types = [
        "section_boundary",
        "slide_change",
        "avatar_enter",
        "narration_start",
        "effect_point",
        "silence_gap",
    ]

    selected: list = []
    remaining: list = list(events)

    # First pass: one of each priority type
    for etype in priority_types:
        if len(selected) >= max_samples:
            break
        for event in remaining:
            if event.event_type == etype:
                selected.append(event)
                remaining.remove(event)
                break

    # Second pass: fill remaining slots with evenly spaced events
    if remaining and len(selected) < max_samples:
        slots = max_samples - len(selected)
        step = max(1, len(remaining) // slots)
        for i in range(0, len(remaining), step):
            if len(selected) >= max_samples:
                break
            selected.append(remaining[i])

    # Sort by time
    selected.sort(key=lambda e: e.time_ms)
    return selected
