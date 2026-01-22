from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from src.api.deps import CurrentUser, DbSession
from src.models.project import Project
from src.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)

router = APIRouter()


@router.get("", response_model=list[ProjectListResponse])
async def list_projects(
    current_user: CurrentUser,
    db: DbSession,
) -> list[ProjectListResponse]:
    """List all projects for the current user."""
    result = await db.execute(
        select(Project)
        .where(Project.user_id == current_user.id)
        .order_by(Project.updated_at.desc())
    )
    projects = result.scalars().all()
    return [ProjectListResponse.model_validate(p) for p in projects]


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_data: ProjectCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> ProjectResponse:
    """Create a new project."""
    project = Project(
        user_id=current_user.id,
        name=project_data.name,
        description=project_data.description,
        width=project_data.width,
        height=project_data.height,
        fps=project_data.fps,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)
    return ProjectResponse.model_validate(project)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> ProjectResponse:
    """Get a project by ID."""
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

    return ProjectResponse.model_validate(project)


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    project_data: ProjectUpdate,
    current_user: CurrentUser,
    db: DbSession,
) -> ProjectResponse:
    """Update a project."""
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

    update_data = project_data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(project, field, value)

    # Recalculate duration from timeline
    if project_data.timeline_data:
        max_duration = 0
        for layer in project_data.timeline_data.get("layers", []):
            for clip in layer.get("clips", []):
                clip_end = clip.get("start_ms", 0) + clip.get("duration_ms", 0)
                max_duration = max(max_duration, clip_end)
        for track in project_data.timeline_data.get("audio_tracks", []):
            for clip in track.get("clips", []):
                clip_end = clip.get("start_ms", 0) + clip.get("duration_ms", 0)
                max_duration = max(max_duration, clip_end)
        project.duration_ms = max_duration

    await db.flush()
    await db.refresh(project)
    return ProjectResponse.model_validate(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """Delete a project."""
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

    await db.delete(project)


@router.put("/{project_id}/timeline", response_model=ProjectResponse)
async def update_timeline(
    project_id: UUID,
    timeline_data: dict,
    current_user: CurrentUser,
    db: DbSession,
) -> ProjectResponse:
    """Update project timeline data."""
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

    import logging
    logger = logging.getLogger(__name__)

    # Garbage collection: Remove orphaned audio clips (only for "video" type tracks)
    video_clip_ids = set()
    for layer in timeline_data.get("layers", []):
        for clip in layer.get("clips", []):
            video_clip_ids.add(clip.get("id"))

    cleaned_audio_tracks = []
    orphaned_count = 0
    for track in timeline_data.get("audio_tracks", []):
        track_type = track.get("type", "")

        # Only GC for "video" type tracks (extracted audio from video)
        # All other track types (narration, bgm, se) keep ALL clips - no GC
        if track_type != "video":
            cleaned_audio_tracks.append(track)
            continue

        cleaned_clips = []
        for clip in track.get("clips", []):
            linked_video_id = clip.get("linked_video_clip_id")

            # For "video" type tracks, only remove clips with invalid linked_video_clip_id
            if linked_video_id and linked_video_id in video_clip_ids:
                cleaned_clips.append(clip)
            else:
                orphaned_count += 1
                logger.info(f"[GC] Removing orphaned video-audio clip: {clip.get('id')}")
        cleaned_audio_tracks.append({**track, "clips": cleaned_clips})

    if orphaned_count > 0:
        logger.info(f"[GC] Removed {orphaned_count} orphaned audio clips")
    timeline_data["audio_tracks"] = cleaned_audio_tracks

    # Recalculate duration from all clips
    max_duration = 0
    logger.info(f"[UPDATE_TIMELINE] Recalculating duration for project {project_id}")
    logger.info(f"[UPDATE_TIMELINE] Input timeline_data.duration_ms = {timeline_data.get('duration_ms', 'NOT SET')}")

    for layer in timeline_data.get("layers", []):
        for clip in layer.get("clips", []):
            start_ms = clip.get("start_ms", 0)
            duration_ms = clip.get("duration_ms", 0)
            clip_end = start_ms + duration_ms
            logger.info(f"[UPDATE_TIMELINE] Layer clip: start={start_ms}, duration={duration_ms}, end={clip_end}")
            max_duration = max(max_duration, clip_end)
    for track in timeline_data.get("audio_tracks", []):
        for clip in track.get("clips", []):
            start_ms = clip.get("start_ms", 0)
            duration_ms = clip.get("duration_ms", 0)
            clip_end = start_ms + duration_ms
            logger.info(f"[UPDATE_TIMELINE] Audio clip: start={start_ms}, duration={duration_ms}, end={clip_end}")
            max_duration = max(max_duration, clip_end)

    logger.info(f"[UPDATE_TIMELINE] Calculated max_duration = {max_duration}")

    # Update both timeline_data.duration_ms and project.duration_ms
    timeline_data["duration_ms"] = max_duration
    project.timeline_data = timeline_data
    project.duration_ms = max_duration
    # Tell SQLAlchemy that the JSON field was modified
    flag_modified(project, "timeline_data")
    logger.info(f"[UPDATE_TIMELINE] Saved timeline_data.duration_ms = {max_duration}")

    await db.flush()
    await db.refresh(project)
    return ProjectResponse.model_validate(project)
