"""Render API endpoints - Async rendering with progress tracking."""

import asyncio
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlalchemy import select

from src.api.deps import CurrentUser, DbSession
from src.models.asset import Asset
from src.models.database import async_session_maker
from src.models.project import Project
from src.models.render_job import RenderJob
from src.render.audio_mixer import AudioClipData, AudioMixer, AudioTrackData
from src.render.pipeline import RenderPipeline
from src.schemas.render import RenderJobResponse, RenderRequest
from src.services.storage_service import StorageService

router = APIRouter()
logger = logging.getLogger(__name__)

# Global dict to track active render processes for cancellation
_active_renders: dict[str, asyncio.subprocess.Process] = {}


async def _update_job_progress(
    job_id: UUID,
    progress: int,
    stage: str,
    status: str = "processing",
    error_message: str | None = None,
    output_key: str | None = None,
    output_url: str | None = None,
    output_size: int | None = None,
) -> None:
    """Update render job progress in database."""
    print(f"[RENDER PROGRESS] Updating job {job_id}: progress={progress}, stage={stage}, status={status}", flush=True)
    async with async_session_maker() as db:
        result = await db.execute(select(RenderJob).where(RenderJob.id == job_id))
        job = result.scalar_one_or_none()
        if job:
            job.progress = progress
            job.current_stage = stage
            job.status = status
            if error_message:
                job.error_message = error_message
            if output_key:
                job.output_key = output_key
            if output_url:
                job.output_url = output_url
            if output_size:
                job.output_size = output_size
            if status == "completed":
                job.completed_at = datetime.now(timezone.utc)
            await db.commit()
            print(f"[RENDER PROGRESS] Job {job_id} updated successfully", flush=True)
        else:
            print(f"[RENDER PROGRESS] Job {job_id} not found!", flush=True)


async def _check_cancelled(job_id: UUID) -> bool:
    """Check if the render job has been cancelled."""
    async with async_session_maker() as db:
        result = await db.execute(select(RenderJob).where(RenderJob.id == job_id))
        job = result.scalar_one_or_none()
        return job is not None and job.status == "cancelled"


async def _run_render_background(
    job_id: UUID,
    project_id: UUID,
    project_name: str,
    project_width: int,
    project_height: int,
    project_fps: int,
    timeline_data: dict,
    duration_ms: int,
) -> None:
    """Background task to run the actual render."""
    temp_dir = None

    try:
        # Check for cancellation
        if await _check_cancelled(job_id):
            logger.info(f"[RENDER] Job {job_id} was cancelled before starting")
            return

        await _update_job_progress(job_id, 5, "Preparing render")

        # Collect all asset IDs from timeline
        asset_ids = set()
        for layer in timeline_data.get("layers", []):
            for clip in layer.get("clips", []):
                if clip.get("asset_id"):
                    asset_ids.add(clip["asset_id"])
        for track in timeline_data.get("audio_tracks", []):
            for clip in track.get("clips", []):
                if clip.get("asset_id"):
                    asset_ids.add(clip["asset_id"])

        if not asset_ids:
            await _update_job_progress(
                job_id, 0, "Failed", "failed", "No assets in timeline"
            )
            return

        # Load assets from database
        async with async_session_maker() as db:
            result = await db.execute(
                select(Asset).where(Asset.id.in_([UUID(aid) for aid in asset_ids]))
            )
            assets_db = {str(a.id): a for a in result.scalars().all()}

        # Check for cancellation
        if await _check_cancelled(job_id):
            logger.info(f"[RENDER] Job {job_id} cancelled after asset lookup")
            return

        await _update_job_progress(job_id, 10, "Downloading assets")

        # Create temp directory
        temp_dir = tempfile.mkdtemp(prefix=f"douga_render_{job_id}_")
        assets_dir = os.path.join(temp_dir, "assets")
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(assets_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        # Download assets from GCS
        storage = StorageService()
        assets_local: dict[str, str] = {}
        total_assets = len(assets_db)

        for idx, (asset_id, asset) in enumerate(assets_db.items()):
            # Check for cancellation periodically
            if await _check_cancelled(job_id):
                logger.info(f"[RENDER] Job {job_id} cancelled during asset download")
                return

            ext = asset.storage_key.rsplit(".", 1)[-1] if "." in asset.storage_key else ""
            local_path = os.path.join(assets_dir, f"{asset_id}.{ext}")
            await storage.download_file(asset.storage_key, local_path)
            assets_local[asset_id] = local_path

            # Update progress (10-30% for downloads)
            download_progress = 10 + int((idx + 1) / total_assets * 20)
            await _update_job_progress(
                job_id, download_progress, f"Downloading assets ({idx + 1}/{total_assets})"
            )

        # Check for cancellation
        if await _check_cancelled(job_id):
            logger.info(f"[RENDER] Job {job_id} cancelled after downloads")
            return

        await _update_job_progress(job_id, 30, "Rendering video")

        # Create render pipeline with progress callback
        pipeline = RenderPipeline(
            job_id=str(job_id),
            project_id=str(project_id),
            width=project_width,
            height=project_height,
            fps=project_fps,
        )

        # Set progress callback
        async def progress_callback(percent: int, stage: str) -> None:
            # Map pipeline progress (0-100) to our range (30-85)
            mapped_progress = 30 + int(percent * 0.55)
            await _update_job_progress(job_id, mapped_progress, stage)

        pipeline.set_progress_callback(
            lambda p, s: asyncio.create_task(progress_callback(p, s))
        )

        # Output path
        output_filename = f"{project_name.replace(' ', '_')}_render.mp4"
        output_path = os.path.join(output_dir, output_filename)

        # Run render (pass job_id for cancel checking)
        await pipeline.render(
            timeline_data,
            assets_local,
            output_path,
            cancel_check=lambda: _check_cancelled(job_id),
        )

        # Check for cancellation before upload
        if await _check_cancelled(job_id):
            logger.info(f"[RENDER] Job {job_id} cancelled after rendering")
            return

        await _update_job_progress(job_id, 90, "Uploading output")

        # Upload to GCS
        output_storage_key = f"projects/{project_id}/renders/{job_id}/{output_filename}"
        await storage.upload_file(output_path, output_storage_key)

        # Generate signed download URL
        download_url = await storage.get_signed_url(output_storage_key, expiration_minutes=1440)

        # Get output file size
        output_size = os.path.getsize(output_path)

        # Mark as completed
        await _update_job_progress(
            job_id,
            100,
            "Complete",
            "completed",
            output_key=output_storage_key,
            output_url=download_url,
            output_size=output_size,
        )

        logger.info(f"[RENDER] Job {job_id} completed successfully")

    except Exception as e:
        logger.exception(f"[RENDER] Job {job_id} failed: {e}")
        await _update_job_progress(job_id, 0, "Failed", "failed", str(e))

    finally:
        # Cleanup temp directory
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass


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
    """
    Start a render job for a project (synchronous).

    Keeps connection open until render completes. Use /render/status to poll for progress.
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

    # Check for existing active render job
    result = await db.execute(
        select(RenderJob).where(
            RenderJob.project_id == project_id,
            RenderJob.status.in_(["queued", "processing"]),
        )
    )
    existing_job = result.scalar_one_or_none()

    if existing_job:
        now = datetime.now(timezone.utc)
        is_stale = False

        # Force flag overrides all checks
        if render_request.force:
            is_stale = True
        # Check if job hasn't been updated in 30 seconds (heartbeat timeout)
        elif existing_job.updated_at and (now - existing_job.updated_at).total_seconds() > 30:
            is_stale = True
        # Fallback: check absolute timeouts
        elif existing_job.status == "queued":
            if existing_job.created_at and (now - existing_job.created_at).total_seconds() > 300:
                is_stale = True
        elif existing_job.status == "processing":
            if existing_job.started_at and (now - existing_job.started_at).total_seconds() > 1800:
                is_stale = True

        if is_stale:
            existing_job.status = "failed"
            existing_job.error_message = "Job timed out (no heartbeat)" if not render_request.force else "Force replaced"
            await db.flush()
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A render job is already in progress for this project",
            )

    # Get timeline data
    timeline_data = project.timeline_data
    if not timeline_data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No timeline data in project",
        )

    # Clean up orphaned audio clips before rendering
    video_clip_ids = set()
    for layer in timeline_data.get("layers", []):
        for clip in layer.get("clips", []):
            video_clip_ids.add(clip.get("id"))

    audio_tracks = timeline_data.get("audio_tracks", [])
    cleaned_audio_tracks = []
    for track in audio_tracks:
        track_type = track.get("type", "")
        if track_type != "video":
            cleaned_audio_tracks.append(track)
            continue
        cleaned_clips = []
        for clip in track.get("clips", []):
            linked_video_id = clip.get("linked_video_clip_id")
            if linked_video_id and linked_video_id in video_clip_ids:
                cleaned_clips.append(clip)
        cleaned_audio_tracks.append({**track, "clips": cleaned_clips})
    timeline_data["audio_tracks"] = cleaned_audio_tracks

    # Use project.duration_ms as the authoritative source
    duration_ms = project.duration_ms or timeline_data.get("duration_ms", 0)
    if duration_ms <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Timeline has no duration",
        )
    timeline_data["duration_ms"] = duration_ms

    # Create render job
    render_job = RenderJob(
        project_id=project_id,
        status="processing",
        progress=0,
        current_stage="Starting render",
        started_at=datetime.now(timezone.utc),
    )
    db.add(render_job)
    await db.flush()
    await db.refresh(render_job)
    await db.commit()

    # Start render as background task
    # Note: subprocess.run blocks the event loop, but with min-instances=1 and
    # no-cpu-throttling, the instance should stay alive
    logger.info(f"[RENDER] Started job {render_job.id} for project {project_id}")
    print(f"[RENDER] Starting background render for job {render_job.id}", flush=True)

    # Use create_task - it will block during subprocess but instance stays alive
    asyncio.create_task(
        _run_render_background(
            render_job.id,
            project.id,
            project.name,
            project.width,
            project.height,
            project.fps,
            timeline_data,
            duration_ms,
        )
    )

    # Return immediately - frontend will poll for status
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
        print(f"[RENDER STATUS] No render job found for project {project_id}", flush=True)
        return None

    print(f"[POLL] job={render_job.id} status={render_job.status} progress={render_job.progress}% stage={render_job.current_stage} updated_at={render_job.updated_at}", flush=True)
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

    # Mark as cancelled - background task will check this
    render_job.status = "cancelled"
    render_job.current_stage = "Cancelled by user"
    await db.commit()

    logger.info(f"[RENDER] Job {render_job.id} cancelled by user")


@router.get("/projects/{project_id}/render/history", response_model=list[RenderJobResponse])
async def get_render_history(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> list[RenderJobResponse]:
    """Get recent completed render jobs for a project (up to 10)."""
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

    # Get recent completed render jobs (limit 10)
    result = await db.execute(
        select(RenderJob)
        .where(
            RenderJob.project_id == project_id,
            RenderJob.status == "completed",
            RenderJob.output_key.isnot(None),
        )
        .order_by(RenderJob.completed_at.desc())
        .limit(10)
    )
    render_jobs = list(result.scalars().all())

    # Regenerate signed URLs for each job (URLs expire after 24 hours)
    storage = StorageService()
    for job in render_jobs:
        if job.output_key:
            try:
                job.output_url = await storage.get_signed_url(job.output_key, expiration_minutes=1440)
            except Exception as e:
                logger.warning(f"Failed to regenerate URL for job {job.id}: {e}")
                job.output_url = None

    await db.commit()

    return [RenderJobResponse.model_validate(job) for job in render_jobs]


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
                await storage.download_file(asset.storage_key, local_path)

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
        background_tasks.add_task(shutil.rmtree, temp_dir, True)
