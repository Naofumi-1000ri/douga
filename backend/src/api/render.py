import os
import tempfile
from uuid import UUID, uuid4

from celery.result import AsyncResult
from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select

from src.api.deps import CurrentUser, DbSession
from src.celery_app import celery_app
from src.models.asset import Asset
from src.models.project import Project
from src.models.render_job import RenderJob
from src.render.audio_mixer import AudioClipData, AudioMixer, AudioTrackData
from src.schemas.render import RenderJobResponse, RenderRequest
from src.services.storage_service import StorageService
from src.tasks.render_task import render_video_task

router = APIRouter()


@router.post(
    "/projects/{project_id}/render",
    response_model=RenderJobResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_render(
    project_id: UUID,
    render_request: RenderRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> RenderJobResponse:
    """Start a render job for a project."""
    # Verify project access
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.user_id == current_user.id,
        )
    )
    project = result.scalar_one_or_none()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # Check for existing active render job
    result = await db.execute(
        select(RenderJob).where(
            RenderJob.project_id == project_id,
            RenderJob.status.in_(["queued", "processing"]),
        )
    )
    existing_job = result.scalar_one_or_none()

    if existing_job:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A render job is already in progress for this project",
        )

    # Create render job
    render_job = RenderJob(
        project_id=project_id,
        status="queued",
        current_stage="Waiting in queue",
    )
    db.add(render_job)
    await db.flush()
    await db.refresh(render_job)

    # Queue the render task with Celery
    task = render_video_task.delay(str(render_job.id))
    render_job.celery_task_id = task.id
    await db.flush()
    await db.refresh(render_job)

    return RenderJobResponse.model_validate(render_job)


@router.get("/projects/{project_id}/render/status", response_model=RenderJobResponse | None)
async def get_render_status(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> RenderJobResponse | None:
    """Get the latest render job status for a project."""
    # Verify project access
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.user_id == current_user.id,
        )
    )
    project = result.scalar_one_or_none()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # Get latest render job
    result = await db.execute(
        select(RenderJob)
        .where(RenderJob.project_id == project_id)
        .order_by(RenderJob.created_at.desc())
        .limit(1)
    )
    render_job = result.scalar_one_or_none()

    if render_job is None:
        return None

    return RenderJobResponse.model_validate(render_job)


@router.delete("/projects/{project_id}/render", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_render(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """Cancel an active render job."""
    # Verify project access
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.user_id == current_user.id,
        )
    )
    project = result.scalar_one_or_none()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # Find active render job
    result = await db.execute(
        select(RenderJob).where(
            RenderJob.project_id == project_id,
            RenderJob.status.in_(["queued", "processing"]),
        )
    )
    render_job = result.scalar_one_or_none()

    if render_job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active render job found",
        )

    render_job.status = "cancelled"
    await db.flush()

    # Cancel the Celery task
    if render_job.celery_task_id:
        celery_app.control.revoke(render_job.celery_task_id, terminate=True)


@router.get("/projects/{project_id}/render/download")
async def get_download_url(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> dict[str, str]:
    """Get the download URL for a completed render."""
    # Verify project access
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.user_id == current_user.id,
        )
    )
    project = result.scalar_one_or_none()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # Get latest completed render job
    result = await db.execute(
        select(RenderJob)
        .where(
            RenderJob.project_id == project_id,
            RenderJob.status == "completed",
        )
        .order_by(RenderJob.created_at.desc())
        .limit(1)
    )
    render_job = result.scalar_one_or_none()

    if render_job is None or render_job.output_url is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No completed render found",
        )

    return {"download_url": render_job.output_url}


@router.post("/projects/{project_id}/render/audio")
async def export_audio_only(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """
    Export audio-only mix from timeline.

    This endpoint renders the audio timeline with all tracks,
    including ducking and fades, to a single audio file.
    """
    # Verify project access
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.user_id == current_user.id,
        )
    )
    project = result.scalar_one_or_none()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    timeline = project.timeline_data
    if not timeline or not timeline.get("audio_tracks"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No audio tracks in timeline",
        )

    # Check if there are any clips
    has_clips = any(
        len(track.get("clips", [])) > 0
        for track in timeline.get("audio_tracks", [])
    )
    if not has_clips:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No audio clips in timeline",
        )

    # Get all asset IDs from clips
    asset_ids = set()
    for track in timeline.get("audio_tracks", []):
        for clip in track.get("clips", []):
            if clip.get("asset_id"):
                asset_ids.add(clip["asset_id"])

    # Fetch assets
    result = await db.execute(
        select(Asset).where(Asset.id.in_([UUID(aid) for aid in asset_ids]))
    )
    assets = {str(a.id): a for a in result.scalars().all()}

    # Build track data for mixer
    tracks = []
    storage = StorageService()
    temp_dir = tempfile.mkdtemp(prefix="douga_export_")

    try:
        for track in timeline.get("audio_tracks", []):
            clips = []
            for clip in track.get("clips", []):
                asset_id = clip.get("asset_id")
                if not asset_id or asset_id not in assets:
                    continue

                asset = assets[asset_id]
                # Download asset from GCS
                local_path = os.path.join(temp_dir, f"{asset_id}.audio")
                await storage.download_file(asset.gcs_path, local_path)

                clips.append(AudioClipData(
                    file_path=local_path,
                    start_ms=clip.get("start_ms", 0),
                    duration_ms=clip.get("duration_ms", 0),
                    in_point_ms=clip.get("in_point_ms", 0),
                    out_point_ms=clip.get("out_point_ms"),
                    volume=clip.get("volume", 1.0),
                    fade_in_ms=clip.get("fade_in_ms", 0),
                    fade_out_ms=clip.get("fade_out_ms", 0),
                ))

            ducking = track.get("ducking", {})
            tracks.append(AudioTrackData(
                track_type=track.get("type", "se"),
                volume=track.get("volume", 1.0),
                clips=clips,
                ducking_enabled=ducking.get("enabled", False),
                duck_to=ducking.get("duck_to", 0.1),
                attack_ms=ducking.get("attack_ms", 200),
                release_ms=ducking.get("release_ms", 500),
            ))

        # Mix audio
        mixer = AudioMixer(output_dir=temp_dir)
        output_filename = f"{project.name}_audio_export.aac"
        output_path = os.path.join(temp_dir, output_filename)

        mixer.mix_tracks(
            tracks=tracks,
            output_path=output_path,
            duration_ms=timeline.get("duration_ms", 60000),
        )

        # Upload to GCS
        gcs_path = f"projects/{project_id}/exports/{uuid4()}/{output_filename}"
        await storage.upload_file(output_path, gcs_path)

        # Generate download URL
        download_url = await storage.get_signed_url(gcs_path, expiration_minutes=60)

        return {"download_url": download_url, "filename": output_filename}

    finally:
        # Cleanup temp files in background
        def cleanup():
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

        background_tasks.add_task(cleanup)
