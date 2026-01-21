"""Render API endpoints - Synchronous rendering (no Celery/Redis)."""

import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from sqlalchemy import select

from src.api.deps import CurrentUser, DbSession
from src.models.asset import Asset
from src.models.project import Project
from src.models.render_job import RenderJob
from src.render.audio_mixer import AudioClipData, AudioMixer, AudioTrackData
from src.render.pipeline import RenderPipeline
from src.schemas.render import RenderJobResponse, RenderRequest
from src.services.storage_service import StorageService

router = APIRouter()
logger = logging.getLogger(__name__)


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
    background_tasks: BackgroundTasks,
) -> RenderJobResponse:
    """
    Start a render job for a project (synchronous).

    Renders the video directly and returns when complete.
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

        if existing_job.status == "queued":
            if existing_job.created_at and (now - existing_job.created_at).total_seconds() > 300:
                is_stale = True
        elif existing_job.status == "processing":
            if existing_job.started_at and (now - existing_job.started_at).total_seconds() > 1800:
                is_stale = True

        if is_stale:
            existing_job.status = "failed"
            existing_job.error_message = "Job timed out"
            await db.flush()
        else:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A render job is already in progress for this project",
            )

    # Create render job
    render_job = RenderJob(
        project_id=project_id,
        status="processing",
        current_stage="Starting render",
        started_at=datetime.now(timezone.utc),
    )
    db.add(render_job)
    await db.flush()
    await db.refresh(render_job)
    await db.commit()

    temp_dir = None

    try:
        # Get timeline data
        timeline_data = project.timeline_data
        if not timeline_data:
            render_job.status = "failed"
            render_job.error_message = "No timeline data in project"
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No timeline data in project",
            )

        # Clean up orphaned audio clips before rendering
        # Collect all video clip IDs from layers
        video_clip_ids = set()
        for layer in timeline_data.get("layers", []):
            for clip in layer.get("clips", []):
                video_clip_ids.add(clip.get("id"))

        # Remove orphaned audio clips (linked to non-existent video clips)
        audio_tracks = timeline_data.get("audio_tracks", [])
        cleaned_audio_tracks = []
        for track in audio_tracks:
            cleaned_clips = []
            for clip in track.get("clips", []):
                linked_video_id = clip.get("linked_video_clip_id")
                # Keep clip if:
                # 1. It has no linked video (standalone audio)
                # 2. Its linked video still exists in layers
                if not linked_video_id or linked_video_id in video_clip_ids:
                    cleaned_clips.append(clip)
                else:
                    print(f"[RENDER] Removing orphaned audio clip: {clip.get('id')} (linked to missing video {linked_video_id})", flush=True)
            cleaned_audio_tracks.append({**track, "clips": cleaned_clips})
        timeline_data["audio_tracks"] = cleaned_audio_tracks

        # Debug: Log audio tracks content after cleanup
        print(f"[RENDER DEBUG] Number of audio tracks: {len(cleaned_audio_tracks)}", flush=True)
        for i, track in enumerate(cleaned_audio_tracks):
            clips = track.get("clips", [])
            muted = track.get("muted", False)
            if clips:  # Only log tracks with clips
                print(f"[RENDER DEBUG] Track {i} ({track.get('type', 'unknown')}): {len(clips)} clips, muted={muted}", flush=True)
                for j, clip in enumerate(clips):
                    print(f"[RENDER DEBUG]   Clip {j}: asset_id={clip.get('asset_id')}, linked_video={clip.get('linked_video_clip_id')}", flush=True)

        # Use project.duration_ms as the authoritative source
        print(f"[RENDER DEBUG] project.duration_ms = {project.duration_ms}", flush=True)
        print(f"[RENDER DEBUG] timeline_data.duration_ms = {timeline_data.get('duration_ms', 'NOT SET')}", flush=True)
        if project.duration_ms and project.duration_ms > 0:
            timeline_data["duration_ms"] = project.duration_ms
        print(f"[RENDER DEBUG] Final duration_ms = {timeline_data.get('duration_ms')}", flush=True)

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
            render_job.status = "failed"
            render_job.error_message = "No assets in timeline"
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No assets in timeline",
            )

        # Load assets from database
        result = await db.execute(
            select(Asset).where(Asset.id.in_([UUID(aid) for aid in asset_ids]))
        )
        assets_db = {str(a.id): a for a in result.scalars().all()}

        # Update progress
        render_job.current_stage = "Downloading assets"
        render_job.progress = 10
        await db.commit()

        # Create temp directory
        temp_dir = tempfile.mkdtemp(prefix=f"douga_render_{render_job.id}_")
        assets_dir = os.path.join(temp_dir, "assets")
        output_dir = os.path.join(temp_dir, "output")
        os.makedirs(assets_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        # Download assets from GCS
        storage = StorageService()
        assets_local: dict[str, str] = {}

        for asset_id, asset in assets_db.items():
            ext = asset.storage_key.rsplit(".", 1)[-1] if "." in asset.storage_key else ""
            local_path = os.path.join(assets_dir, f"{asset_id}.{ext}")
            await storage.download_file(asset.storage_key, local_path)
            assets_local[asset_id] = local_path

        # Update progress
        render_job.current_stage = "Rendering video"
        render_job.progress = 30
        await db.commit()

        # Create render pipeline
        pipeline = RenderPipeline(
            job_id=str(render_job.id),
            project_id=str(project.id),
            width=project.width,
            height=project.height,
            fps=project.fps,
        )

        # Output path
        output_filename = f"{project.name.replace(' ', '_')}_render.mp4"
        output_path = os.path.join(output_dir, output_filename)

        # Run render
        await pipeline.render(timeline_data, assets_local, output_path)

        # Update progress
        render_job.current_stage = "Uploading output"
        render_job.progress = 90
        await db.commit()

        # Upload to GCS
        output_storage_key = f"projects/{project.id}/renders/{render_job.id}/{output_filename}"
        await storage.upload_file(output_path, output_storage_key)

        # Generate signed download URL
        download_url = await storage.get_signed_url(output_storage_key, expiration_minutes=1440)  # 24 hours

        # Get output file size
        output_size = os.path.getsize(output_path)

        # Update render job as completed
        render_job.status = "completed"
        render_job.progress = 100
        render_job.current_stage = "Complete"
        render_job.completed_at = datetime.now(timezone.utc)
        render_job.output_key = output_storage_key
        render_job.output_url = download_url
        render_job.output_size = output_size
        await db.commit()

        # Cleanup temp directory in background
        if temp_dir:
            background_tasks.add_task(shutil.rmtree, temp_dir, True)

        return RenderJobResponse.model_validate(render_job)

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Render failed for project {project_id}")
        render_job.status = "failed"
        render_job.error_message = str(e)
        await db.commit()

        # Cleanup temp directory
        if temp_dir and os.path.exists(temp_dir):
            background_tasks.add_task(shutil.rmtree, temp_dir, True)

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Render failed: {str(e)}",
        )


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
    """Cancel an active render job (marks as cancelled but cannot stop in-progress sync render)."""
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
