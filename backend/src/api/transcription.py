"""
Transcription API endpoints.

Provides:
- POST /transcribe - Start transcription for an asset
- GET /transcription/{asset_id} - Get transcription result
- PUT /transcription/{asset_id}/segments/{segment_id} - Update cut flag
- POST /transcription/{asset_id}/apply-cuts - Apply cuts to timeline

Transcription state is persisted in assets.asset_metadata JSONB under the
``transcription`` key.  This makes results durable across Cloud Run instance
restarts and routing changes.
"""

import asyncio
import tempfile
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.api.access import get_accessible_project
from src.api.deps import get_current_user, get_db
from src.models.asset import Asset
from src.models.user import User
from src.schemas.timeline import Transcription
from src.services.storage_service import get_storage_service
from src.services.transcription_service import TranscriptionService

router = APIRouter(prefix="/transcription", tags=["transcription"])


# ---------------------------------------------------------------------------
# Helpers: persist / load transcription via asset_metadata JSONB
# ---------------------------------------------------------------------------


def _transcription_key() -> str:
    return "transcription"


def _load_transcription(asset: Asset) -> Transcription | None:
    """Load transcription from asset.asset_metadata, or return None."""
    if asset.asset_metadata is None:
        return None
    raw = asset.asset_metadata.get(_transcription_key())
    if raw is None:
        return None
    try:
        return Transcription.model_validate(raw)
    except Exception:
        return None


def _save_transcription(asset: Asset, transcription: Transcription) -> None:
    """Persist transcription into asset.asset_metadata and mark it dirty."""
    if asset.asset_metadata is None:
        asset.asset_metadata = {}
    # Work on a copy to ensure SQLAlchemy detects the change.
    meta = dict(asset.asset_metadata)
    meta[_transcription_key()] = transcription.model_dump(mode="json")
    asset.asset_metadata = meta
    try:
        flag_modified(asset, "asset_metadata")
    except Exception:
        # flag_modified only works on SQLAlchemy ORM instances; in unit tests
        # a plain SimpleNamespace is used so the call is a no-op.
        pass


# ---------------------------------------------------------------------------
# Background task: run transcription and persist result
# ---------------------------------------------------------------------------


async def _run_transcription(
    asset_id: str,
    file_path: str,
    language: str,
    model_name: str,
    detect_silences: bool,
    detect_fillers: bool,
    detect_repetitions: bool,
    min_silence_duration_ms: int,
) -> None:
    """Background task: transcribe and write result to DB."""
    from src.models.database import async_session_maker

    async with async_session_maker() as db:
        asset = await db.get(Asset, UUID(asset_id))
        if asset is None:
            return  # Asset deleted while task was queued

        try:
            service = TranscriptionService(
                model_name=model_name,
                min_silence_duration_ms=min_silence_duration_ms,
            )
            # Offload blocking subprocess+OpenAI call to thread pool to avoid
            # stalling the event loop for tens of seconds.
            result = await asyncio.to_thread(
                service.transcribe,
                file_path,
                language=language,
                detect_silences=detect_silences,
                detect_fillers=detect_fillers,
                detect_repetitions=detect_repetitions,
            )
            result.asset_id = UUID(asset_id)
        except Exception as exc:
            result = Transcription(
                asset_id=UUID(asset_id),
                language=language,
                status="failed",
                error_message=str(exc),
            )

        _save_transcription(asset, result)
        await db.commit()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class TranscribeRequest(BaseModel):
    """Request to start transcription."""

    asset_id: UUID
    language: str = "ja"
    model_name: Literal["tiny", "base", "small", "medium", "large"] = "base"
    detect_silences: bool = True
    detect_fillers: bool = True
    detect_repetitions: bool = True
    min_silence_duration_ms: int = 500


class TranscribeResponse(BaseModel):
    """Response from transcription request."""

    status: str
    message: str
    asset_id: UUID


class UpdateSegmentRequest(BaseModel):
    """Request to update a segment's cut flag."""

    cut: bool
    cut_reason: Literal["silence", "mistake", "manual", "filler"] | None = None


class ApplyCutsResponse(BaseModel):
    """Response from applying cuts."""

    clips_created: int
    total_duration_ms: int
    cut_duration_ms: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", response_model=TranscribeResponse)
async def start_transcription(
    request: TranscribeRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TranscribeResponse:
    """
    Start transcription for an asset.

    The transcription runs in the background. Poll GET /transcription/{asset_id}
    for results.
    """
    asset = await db.get(Asset, request.asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    # POST (start transcription) is a write operation — require editor role.
    await get_accessible_project(asset.project_id, current_user.id, db, require_role="editor")

    if asset.type not in ("audio", "video"):
        raise HTTPException(status_code=400, detail="Asset must be audio or video")

    # Download file from GCS to temp location
    storage = get_storage_service()
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = tmp.name

    await storage.download_file(asset.storage_key, tmp_path)

    # Mark as processing (persisted to DB)
    processing = Transcription(
        asset_id=request.asset_id,
        language=request.language,
        status="processing",
    )
    _save_transcription(asset, processing)
    await db.flush()

    # Schedule background task
    background_tasks.add_task(
        _run_transcription,
        str(request.asset_id),
        tmp_path,
        request.language,
        request.model_name,
        request.detect_silences,
        request.detect_fillers,
        request.detect_repetitions,
        request.min_silence_duration_ms,
    )

    return TranscribeResponse(
        status="processing",
        message="Transcription started. Poll GET /transcription/{asset_id} for results.",
        asset_id=request.asset_id,
    )


@router.get("/{asset_id}", response_model=Transcription)
async def get_transcription(
    asset_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Transcription:
    """Get transcription result for an asset."""
    asset = await db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    # GET (read transcription result) — viewer role is sufficient.
    await get_accessible_project(asset.project_id, current_user.id, db, require_role=None)

    transcription = _load_transcription(asset)
    if transcription is None:
        raise HTTPException(status_code=404, detail="Transcription not found")

    return transcription


@router.put("/{asset_id}/segments/{segment_id}")
async def update_segment(
    asset_id: UUID,
    segment_id: str,
    request: UpdateSegmentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Update a segment's cut flag."""
    asset = await db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    # PUT (update segment cut flag) is a write operation — require editor role.
    await get_accessible_project(asset.project_id, current_user.id, db, require_role="editor")

    transcription = _load_transcription(asset)
    if transcription is None:
        raise HTTPException(status_code=404, detail="Transcription not found")

    for segment in transcription.segments:
        if segment.id == segment_id:
            segment.cut = request.cut
            segment.cut_reason = request.cut_reason if request.cut else None
            transcription.cut_segments = sum(1 for s in transcription.segments if s.cut)
            _save_transcription(asset, transcription)
            # No need to flush here — committed by get_db dependency.
            return {"status": "updated", "segment_id": segment_id}

    raise HTTPException(status_code=404, detail="Segment not found")


@router.post("/{asset_id}/apply-cuts", response_model=ApplyCutsResponse)
async def apply_cuts_to_timeline(
    asset_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApplyCutsResponse:
    """
    Apply cut flags to create timeline clips.

    Creates audio clips for non-cut segments, effectively removing
    the cut portions from the final output.
    """
    asset = await db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    # POST apply-cuts (write/mutation) — require editor role.
    await get_accessible_project(asset.project_id, current_user.id, db, require_role="editor")

    transcription = _load_transcription(asset)
    if transcription is None:
        raise HTTPException(status_code=404, detail="Transcription not found")

    if transcription.status != "completed":
        raise HTTPException(status_code=400, detail="Transcription not completed")

    clips_data = []
    current_timeline_position = 0
    cut_duration = 0

    for segment in transcription.segments:
        if segment.cut:
            cut_duration += segment.end_ms - segment.start_ms
        else:
            clips_data.append(
                {
                    "id": segment.id,
                    "asset_id": str(asset_id),
                    "start_ms": current_timeline_position,
                    "duration_ms": segment.end_ms - segment.start_ms,
                    "in_point_ms": segment.start_ms,
                    "out_point_ms": segment.end_ms,
                    "volume": 1.0,
                }
            )
            current_timeline_position += segment.end_ms - segment.start_ms

    return ApplyCutsResponse(
        clips_created=len(clips_data),
        total_duration_ms=current_timeline_position,
        cut_duration_ms=cut_duration,
    )
