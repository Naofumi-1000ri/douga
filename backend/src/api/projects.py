import base64
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm.attributes import flag_modified

from src.api.access import get_accessible_project
from src.api.deps import CurrentUser, DbSession
from src.models.project import Project
from src.models.project_member import ProjectMember
from src.models.sequence import Sequence, _default_timeline_data
from src.models.user import User
from src.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
    TimelineUpdateRequest,
)
from src.services.event_manager import event_manager
from src.services.storage_service import get_storage_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_thumbnail_url(project: Project) -> str | None:
    """Generate thumbnail URL from storage key or return legacy URL."""
    if project.thumbnail_storage_key:
        storage = get_storage_service()
        return storage.generate_download_url(
            project.thumbnail_storage_key, expires_minutes=60 * 24 * 7
        )  # 7 days
    # Backward compatibility: return legacy thumbnail_url if exists
    return project.thumbnail_url


@router.get("", response_model=list[ProjectListResponse])
async def list_projects(
    current_user: CurrentUser,
    db: DbSession,
) -> list[ProjectListResponse]:
    """List all projects for the current user (owned + shared)."""
    result = await db.execute(
        select(Project)
        .where(
            or_(
                Project.user_id == current_user.id,
                Project.id.in_(
                    select(ProjectMember.project_id).where(
                        ProjectMember.user_id == current_user.id,
                        ProjectMember.accepted_at.isnot(None),
                    )
                ),
            )
        )
        .order_by(Project.updated_at.desc())
    )
    projects = result.scalars().all()

    # Build membership lookup for shared projects
    member_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.user_id == current_user.id,
            ProjectMember.accepted_at.isnot(None),
        )
    )
    membership_map = {m.project_id: m for m in member_result.scalars().all()}

    # Get owner names for shared projects
    owner_ids = {p.user_id for p in projects if p.user_id != current_user.id}
    owner_map: dict = {}
    if owner_ids:
        owner_result = await db.execute(select(User).where(User.id.in_(owner_ids)))
        owner_map = {u.id: u.name for u in owner_result.scalars().all()}

    # Generate signed URLs for thumbnails and add collaboration info
    responses = []
    for p in projects:
        response = ProjectListResponse.model_validate(p)
        response.thumbnail_url = _get_thumbnail_url(p)
        is_owner = p.user_id == current_user.id
        response.is_shared = not is_owner
        membership = membership_map.get(p.id)
        response.role = "owner" if is_owner else (membership.role if membership else "editor")
        response.owner_name = owner_map.get(p.user_id) if not is_owner else None
        responses.append(response)
    return responses


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

    # Create default sequence for the project
    default_sequence = Sequence(
        project_id=project.id,
        name="Main",
        timeline_data=_default_timeline_data(),
        version=1,
        duration_ms=0,
        is_default=True,
    )
    db.add(default_sequence)
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
    project = await get_accessible_project(project_id, current_user.id, db)

    response = ProjectResponse.model_validate(project)
    response.thumbnail_url = _get_thumbnail_url(project)
    return response


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    project_data: ProjectUpdate,
    current_user: CurrentUser,
    db: DbSession,
) -> ProjectResponse:
    """Update a project."""
    project = await get_accessible_project(project_id, current_user.id, db)

    update_data = project_data.model_dump(exclude_unset=True)
    # Exclude version/force from setattr — they control concurrency, not model fields
    update_data.pop("version", None)
    update_data.pop("force", None)
    for field, value in update_data.items():
        setattr(project, field, value)

    # Recalculate duration from timeline
    if project_data.timeline_data:
        # Optimistic lock check when timeline changes
        if project_data.version is not None and not project_data.force:
            if project.version != project_data.version:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "CONCURRENT_MODIFICATION",
                        "message": "このプロジェクトは他のユーザーによって変更されました",
                        "server_version": project.version,
                    },
                )
        project.version += 1

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
            data={
                "source": "api",
                "version": project.version,
                "user_id": str(current_user.id),
                "user_name": current_user.name,
            },
        )

    response = ProjectResponse.model_validate(project)
    response.thumbnail_url = _get_thumbnail_url(project)
    return response


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """Delete a project."""
    project = await get_accessible_project(project_id, current_user.id, db, require_role="owner")

    await db.delete(project)


@router.put("/{project_id}/timeline", response_model=ProjectResponse)
async def update_timeline(
    project_id: UUID,
    request: TimelineUpdateRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> ProjectResponse:
    """Update project timeline data."""
    project = await get_accessible_project(project_id, current_user.id, db)

    # Optimistic lock check
    if request.version is not None and not request.force:
        if project.version != request.version:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CONCURRENT_MODIFICATION",
                    "message": "このプロジェクトは他のユーザーによって変更されました",
                    "server_version": project.version,
                },
            )

    timeline_data = request.timeline_data

    # Recalculate duration from all clips
    max_duration = 0
    logger.info(f"[UPDATE_TIMELINE] Recalculating duration for project {project_id}")
    logger.info(
        f"[UPDATE_TIMELINE] Input timeline_data.duration_ms = {timeline_data.get('duration_ms', 'NOT SET')}"
    )

    for layer in timeline_data.get("layers", []):
        for clip in layer.get("clips", []):
            start_ms = clip.get("start_ms", 0)
            duration_ms = clip.get("duration_ms", 0)
            clip_end = start_ms + duration_ms
            logger.info(
                f"[UPDATE_TIMELINE] Layer clip: start={start_ms}, duration={duration_ms}, end={clip_end}"
            )
            max_duration = max(max_duration, clip_end)
    for track in timeline_data.get("audio_tracks", []):
        for clip in track.get("clips", []):
            start_ms = clip.get("start_ms", 0)
            duration_ms = clip.get("duration_ms", 0)
            clip_end = start_ms + duration_ms
            logger.info(
                f"[UPDATE_TIMELINE] Audio clip: start={start_ms}, duration={duration_ms}, end={clip_end}"
            )
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
    project.version += 1
    # Tell SQLAlchemy that the JSON field was modified
    flag_modified(project, "timeline_data")
    logger.info(f"[UPDATE_TIMELINE] Saved timeline_data.duration_ms = {max_duration}")

    await db.flush()
    await db.refresh(project)

    # Publish timeline_updated event for SSE subscribers
    await event_manager.publish(
        project_id=project_id,
        event_type="timeline_updated",
        data={
            "source": "api",
            "version": project.version,
            "user_id": str(current_user.id),
            "user_name": current_user.name,
        },
    )

    response = ProjectResponse.model_validate(project)
    response.thumbnail_url = _get_thumbnail_url(project)
    return response


class ThumbnailUploadRequest(BaseModel):
    """Request model for uploading project thumbnail."""

    image_data: str  # Base64 encoded image data (with or without data URI prefix)


class ThumbnailUploadResponse(BaseModel):
    """Response model for thumbnail upload."""

    thumbnail_url: str


@router.post("/{project_id}/thumbnail", response_model=ThumbnailUploadResponse)
async def upload_thumbnail(
    project_id: UUID,
    request: ThumbnailUploadRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> ThumbnailUploadResponse:
    """Upload a thumbnail image for a project.

    The image should be sent as base64-encoded data.
    Supports PNG and JPEG formats.
    """
    # Verify project access
    project = await get_accessible_project(project_id, current_user.id, db)

    # Parse base64 data (handle data URI prefix if present)
    image_data = request.image_data
    content_type = "image/png"  # default

    if image_data.startswith("data:"):
        # Extract content type and base64 data from data URI
        # Format: data:image/png;base64,<data>
        try:
            header, base64_data = image_data.split(",", 1)
            if "image/jpeg" in header or "image/jpg" in header:
                content_type = "image/jpeg"
            elif "image/png" in header:
                content_type = "image/png"
            image_data = base64_data
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid data URI format",
            )

    # Decode base64
    try:
        image_bytes = base64.b64decode(image_data)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid base64 encoding",
        )

    # Determine file extension
    extension = "png" if content_type == "image/png" else "jpg"

    # Upload to storage
    storage = get_storage_service()
    storage_key = f"thumbnails/projects/{project_id}/thumbnail.{extension}"

    # Delete old thumbnail if it exists and has different extension
    old_extensions = ["png", "jpg"]
    for ext in old_extensions:
        old_key = f"thumbnails/projects/{project_id}/thumbnail.{ext}"
        if old_key != storage_key and storage.file_exists(old_key):
            try:
                storage.delete_file(old_key)
            except Exception:
                pass  # Ignore deletion errors

    storage.upload_file_from_bytes(
        storage_key=storage_key,
        data=image_bytes,
        content_type=content_type,
    )

    # Save storage key (not URL) to avoid String(500) limit
    project.thumbnail_storage_key = storage_key
    project.thumbnail_url = None  # Clear legacy URL field
    await db.flush()
    await db.refresh(project)

    # Generate signed URL for response
    thumbnail_url = storage.generate_download_url(
        storage_key, expires_minutes=60 * 24 * 7
    )  # 7 days

    logger.info(f"Uploaded thumbnail for project {project_id}")

    return ThumbnailUploadResponse(thumbnail_url=thumbnail_url)
