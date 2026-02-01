import logging
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
from src.services.event_manager import event_manager

logger = logging.getLogger(__name__)

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

    # Only publish event for changes that affect the timeline/content
    # Skip for settings-only changes like ai_api_key, ai_provider
    settings_only_fields = {"ai_api_key", "ai_provider"}
    if not settings_only_fields.issuperset(update_data.keys()):
        await event_manager.publish(
            project_id=project_id,
            event_type="project_updated",
            data={"source": "api"},
        )

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

    # Recalculate layer order from array position (array index 0 = topmost = highest order)
    layers = timeline_data.get("layers", [])
    for i, layer in enumerate(layers):
        layer["order"] = len(layers) - 1 - i

    # Update both timeline_data.duration_ms and project.duration_ms
    timeline_data["duration_ms"] = max_duration
    project.timeline_data = timeline_data
    project.duration_ms = max_duration
    # Tell SQLAlchemy that the JSON field was modified
    flag_modified(project, "timeline_data")
    logger.info(f"[UPDATE_TIMELINE] Saved timeline_data.duration_ms = {max_duration}")

    await db.flush()
    await db.refresh(project)

    # Publish timeline_updated event for SSE subscribers
    await event_manager.publish(
        project_id=project_id,
        event_type="timeline_updated",
        data={"source": "api"},
    )

    return ProjectResponse.model_validate(project)


