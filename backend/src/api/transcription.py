"""
Transcription API endpoints.

Provides:
- POST /transcribe - Start transcription for an asset
- GET /transcription/{asset_id} - Get transcription result
- PUT /transcription/{asset_id}/segments/{segment_id} - Update cut flag
- POST /transcription/{asset_id}/apply-cuts - Apply cuts to timeline
"""

import tempfile
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_user, get_db
from src.models.asset import Asset
from src.models.project import Project
from src.models.user import User
from src.schemas.timeline import Transcription, TranscriptionSegment
from src.services.storage_service import StorageService
from src.services.transcription_service import TranscriptionService

router = APIRouter(prefix="/transcription", tags=["transcription"])


# In-memory storage for transcriptions (in production, use database)
_transcriptions: dict[str, Transcription] = {}


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


async def _run_transcription(
    asset_id: str,
    file_path: str,
    language: str,
    model_name: str,
    detect_silences: bool,
    detect_fillers: bool,
    detect_repetitions: bool,
    min_silence_duration_ms: int,
):
    """Background task to run transcription."""
    try:
        service = TranscriptionService(
            model_name=model_name,
            min_silence_duration_ms=min_silence_duration_ms,
        )

        result = service.transcribe(
            file_path,
            language=language,
            detect_silences=detect_silences,
            detect_fillers=detect_fillers,
            detect_repetitions=detect_repetitions,
        )

        # Update asset_id
        result.asset_id = UUID(asset_id)

        # Store result
        _transcriptions[asset_id] = result

    except Exception as e:
        # Store error
        _transcriptions[asset_id] = Transcription(
            asset_id=UUID(asset_id),
            language=language,
            status="failed",
            error_message=str(e),
        )


@router.post("", response_model=TranscribeResponse)
async def start_transcription(
    request: TranscribeRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Start transcription for an asset.

    The transcription runs in the background. Poll GET /transcription/{asset_id}
    for results.
    """
    # Get asset
    asset = await db.get(Asset, request.asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    # Verify user has access
    project = await db.get(Project, asset.project_id)
    if not project or project.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Check asset type
    if asset.type not in ("audio", "video"):
        raise HTTPException(status_code=400, detail="Asset must be audio or video")

    # Download file from GCS to temp location
    storage = StorageService()
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_path = tmp.name

    await storage.download_file(asset.storage_key, tmp_path)

    # Mark as processing
    asset_id_str = str(request.asset_id)
    _transcriptions[asset_id_str] = Transcription(
        asset_id=request.asset_id,
        language=request.language,
        status="processing",
    )

    # Start background task
    background_tasks.add_task(
        _run_transcription,
        asset_id_str,
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
):
    """Get transcription result for an asset."""
    # Verify access
    asset = await db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    project = await db.get(Project, asset.project_id)
    if not project or project.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get transcription
    asset_id_str = str(asset_id)
    if asset_id_str not in _transcriptions:
        raise HTTPException(status_code=404, detail="Transcription not found")

    return _transcriptions[asset_id_str]


@router.put("/{asset_id}/segments/{segment_id}")
async def update_segment(
    asset_id: UUID,
    segment_id: str,
    request: UpdateSegmentRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update a segment's cut flag."""
    # Verify access
    asset = await db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    project = await db.get(Project, asset.project_id)
    if not project or project.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get transcription
    asset_id_str = str(asset_id)
    if asset_id_str not in _transcriptions:
        raise HTTPException(status_code=404, detail="Transcription not found")

    transcription = _transcriptions[asset_id_str]

    # Find and update segment
    for segment in transcription.segments:
        if segment.id == segment_id:
            segment.cut = request.cut
            segment.cut_reason = request.cut_reason if request.cut else None

            # Recalculate statistics
            transcription.cut_segments = sum(1 for s in transcription.segments if s.cut)
            return {"status": "updated", "segment_id": segment_id}

    raise HTTPException(status_code=404, detail="Segment not found")


@router.post("/{asset_id}/apply-cuts", response_model=ApplyCutsResponse)
async def apply_cuts_to_timeline(
    asset_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Apply cut flags to create timeline clips.

    This creates audio clips for non-cut segments, effectively removing
    the cut portions from the final output.
    """
    # Verify access
    asset = await db.get(Asset, asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    project = await db.get(Project, asset.project_id)
    if not project or project.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Get transcription
    asset_id_str = str(asset_id)
    if asset_id_str not in _transcriptions:
        raise HTTPException(status_code=404, detail="Transcription not found")

    transcription = _transcriptions[asset_id_str]

    if transcription.status != "completed":
        raise HTTPException(status_code=400, detail="Transcription not completed")

    # Calculate clip data from non-cut segments
    clips_data = []
    current_timeline_position = 0
    cut_duration = 0

    for segment in transcription.segments:
        if segment.cut:
            cut_duration += segment.end_ms - segment.start_ms
        else:
            # Create a clip for this segment
            clips_data.append({
                "id": segment.id,
                "asset_id": str(asset_id),
                "start_ms": current_timeline_position,
                "duration_ms": segment.end_ms - segment.start_ms,
                "in_point_ms": segment.start_ms,  # Source in point
                "out_point_ms": segment.end_ms,   # Source out point
                "volume": 1.0,
            })
            current_timeline_position += segment.end_ms - segment.start_ms

    return ApplyCutsResponse(
        clips_created=len(clips_data),
        total_duration_ms=current_timeline_position,
        cut_duration_ms=cut_duration,
    )
