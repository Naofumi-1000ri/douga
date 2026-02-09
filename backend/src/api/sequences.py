"""Sequences API for multi-sequence timeline editing.

Each sequence has its own timeline_data, version, and lock state.
Locking prevents concurrent edits: lock expires after 2 minutes without heartbeat.
"""

import base64
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from src.api.access import get_accessible_project
from src.api.deps import CurrentUser, DbSession
from src.config import get_settings
from src.models.sequence import Sequence, _default_timeline_data
from src.models.sequence_snapshot import SequenceSnapshot
from src.models.user import User
from src.schemas.sequence import (
    LockResponse,
    SequenceCreate,
    SequenceDefaultResponse,
    SequenceDetail,
    SequenceListItem,
    SequenceUpdate,
    SnapshotCreate,
    SnapshotDetail,
)
from src.services.storage_service import get_storage_service
from src.utils.edit_token import create_edit_token

logger = logging.getLogger(__name__)

router = APIRouter()

# Lock expires after 2 minutes without heartbeat
LOCK_TIMEOUT = timedelta(minutes=2)


def _get_sequence_thumbnail_url(seq: Sequence) -> str | None:
    """Generate thumbnail URL from storage key."""
    if seq.thumbnail_storage_key:
        storage = get_storage_service()
        return storage.generate_download_url(
            seq.thumbnail_storage_key, expires_minutes=60 * 24 * 7
        )  # 7 days
    return None


def _calculate_duration_ms(timeline_data: dict) -> int:
    """Calculate total duration from clips in timeline data."""
    max_end = 0
    for layer in timeline_data.get("layers", []):
        for clip in layer.get("clips", []):
            end = clip.get("start_ms", 0) + clip.get("duration_ms", 0)
            if end > max_end:
                max_end = end
    for track in timeline_data.get("audio_tracks", []):
        for clip in track.get("clips", []):
            end = clip.get("start_ms", 0) + clip.get("duration_ms", 0)
            if end > max_end:
                max_end = end
    return max_end


def _is_lock_expired(locked_at: datetime | None) -> bool:
    """Check if a lock has expired (2 minutes without heartbeat)."""
    if locked_at is None:
        return True
    now = datetime.now(timezone.utc)
    # Ensure locked_at is timezone-aware
    if locked_at.tzinfo is None:
        locked_at = locked_at.replace(tzinfo=timezone.utc)
    return now - locked_at > LOCK_TIMEOUT


@router.get("/{project_id}/sequences", response_model=list[SequenceListItem])
async def list_sequences(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> list[SequenceListItem]:
    """List all sequences for a project."""
    await get_accessible_project(project_id, current_user.id, db)

    result = await db.execute(
        select(Sequence, User.name)
        .outerjoin(User, Sequence.locked_by == User.id)
        .where(Sequence.project_id == project_id)
        .order_by(Sequence.created_at.asc())
    )

    items: list[SequenceListItem] = []
    for seq, lock_holder_name in result.all():
        items.append(
            SequenceListItem(
                id=seq.id,
                name=seq.name,
                version=seq.version,
                duration_ms=seq.duration_ms,
                is_default=seq.is_default,
                locked_by=seq.locked_by,
                lock_holder_name=lock_holder_name,
                thumbnail_url=_get_sequence_thumbnail_url(seq),
                created_at=seq.created_at,
                updated_at=seq.updated_at,
            )
        )
    return items


@router.post("/{project_id}/sequences", response_model=SequenceDetail, status_code=201)
async def create_sequence(
    project_id: UUID,
    body: SequenceCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> SequenceDetail:
    """Create a new sequence for a project."""
    await get_accessible_project(project_id, current_user.id, db)

    seq = Sequence(
        project_id=project_id,
        name=body.name,
        timeline_data=_default_timeline_data(),
        version=1,
        duration_ms=0,
        is_default=False,
    )
    db.add(seq)
    await db.flush()
    await db.refresh(seq)

    return SequenceDetail(
        id=seq.id,
        project_id=seq.project_id,
        name=seq.name,
        timeline_data=seq.timeline_data,
        version=seq.version,
        duration_ms=seq.duration_ms,
        is_default=seq.is_default,
        locked_by=seq.locked_by,
        lock_holder_name=None,
        thumbnail_url=None,
        locked_at=seq.locked_at,
        created_at=seq.created_at,
        updated_at=seq.updated_at,
    )


@router.post("/{project_id}/sequences/{sequence_id}/copy", response_model=SequenceDetail, status_code=201)
async def copy_sequence(
    project_id: UUID,
    sequence_id: UUID,
    body: SequenceCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> SequenceDetail:
    """Copy a sequence with its timeline data."""
    await get_accessible_project(project_id, current_user.id, db)

    result = await db.execute(
        select(Sequence)
        .where(Sequence.id == sequence_id, Sequence.project_id == project_id)
    )
    source = result.scalar_one_or_none()

    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sequence not found")

    seq = Sequence(
        project_id=project_id,
        name=body.name,
        timeline_data=source.timeline_data,
        version=1,
        duration_ms=source.duration_ms,
        is_default=False,
    )
    db.add(seq)
    await db.flush()
    await db.refresh(seq)

    return SequenceDetail(
        id=seq.id,
        project_id=seq.project_id,
        name=seq.name,
        timeline_data=seq.timeline_data,
        version=seq.version,
        duration_ms=seq.duration_ms,
        is_default=seq.is_default,
        locked_by=seq.locked_by,
        lock_holder_name=None,
        thumbnail_url=None,
        locked_at=seq.locked_at,
        created_at=seq.created_at,
        updated_at=seq.updated_at,
    )


@router.get("/{project_id}/sequences/default", response_model=SequenceDefaultResponse)
async def get_default_sequence(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> SequenceDefaultResponse:
    """Get the default sequence ID for a project."""
    await get_accessible_project(project_id, current_user.id, db)

    result = await db.execute(
        select(Sequence.id)
        .where(Sequence.project_id == project_id, Sequence.is_default == True)  # noqa: E712
    )
    seq_id = result.scalar_one_or_none()

    if seq_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No default sequence found for this project",
        )

    return SequenceDefaultResponse(id=seq_id)


@router.get("/{project_id}/sequences/{sequence_id}", response_model=SequenceDetail)
async def get_sequence(
    project_id: UUID,
    sequence_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> SequenceDetail:
    """Get a sequence with full timeline data."""
    await get_accessible_project(project_id, current_user.id, db)

    result = await db.execute(
        select(Sequence, User.name)
        .outerjoin(User, Sequence.locked_by == User.id)
        .where(Sequence.id == sequence_id, Sequence.project_id == project_id)
    )
    row = result.one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sequence not found",
        )

    seq, lock_holder_name = row

    return SequenceDetail(
        id=seq.id,
        project_id=seq.project_id,
        name=seq.name,
        timeline_data=seq.timeline_data,
        version=seq.version,
        duration_ms=seq.duration_ms,
        is_default=seq.is_default,
        locked_by=seq.locked_by,
        lock_holder_name=lock_holder_name,
        thumbnail_url=_get_sequence_thumbnail_url(seq),
        locked_at=seq.locked_at,
        created_at=seq.created_at,
        updated_at=seq.updated_at,
    )


@router.put("/{project_id}/sequences/{sequence_id}", response_model=SequenceDetail)
async def update_sequence(
    project_id: UUID,
    sequence_id: UUID,
    body: SequenceUpdate,
    current_user: CurrentUser,
    db: DbSession,
) -> SequenceDetail:
    """Save sequence timeline data with optimistic locking.

    Requires:
    - User must hold the lock (locked_by == current_user.id)
    - Version must match (optimistic locking)
    """
    await get_accessible_project(project_id, current_user.id, db)

    # Fetch with row-level lock
    result = await db.execute(
        select(Sequence)
        .where(Sequence.id == sequence_id, Sequence.project_id == project_id)
        .with_for_update()
    )
    seq = result.scalar_one_or_none()

    if seq is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sequence not found",
        )

    # Lock check: must be locked by current user
    if seq.locked_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sequence is not locked by you. Acquire a lock before saving.",
        )

    # Version check (optimistic locking)
    if body.version != seq.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "CONCURRENT_MODIFICATION",
                "message": f"Version conflict: expected {body.version}, current {seq.version}",
                "server_version": seq.version,
            },
        )

    # Update timeline_data
    seq.timeline_data = body.timeline_data
    flag_modified(seq, "timeline_data")

    # Increment version
    seq.version += 1

    # Recalculate duration_ms
    seq.duration_ms = _calculate_duration_ms(body.timeline_data)

    # Update locked_at as implicit heartbeat
    seq.locked_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(seq)

    # Fetch lock holder name
    lock_holder_name = None
    if seq.locked_by:
        user_result = await db.execute(select(User.name).where(User.id == seq.locked_by))
        lock_holder_name = user_result.scalar_one_or_none()

    return SequenceDetail(
        id=seq.id,
        project_id=seq.project_id,
        name=seq.name,
        timeline_data=seq.timeline_data,
        version=seq.version,
        duration_ms=seq.duration_ms,
        is_default=seq.is_default,
        locked_by=seq.locked_by,
        lock_holder_name=lock_holder_name,
        thumbnail_url=_get_sequence_thumbnail_url(seq),
        locked_at=seq.locked_at,
        created_at=seq.created_at,
        updated_at=seq.updated_at,
    )


@router.delete("/{project_id}/sequences/{sequence_id}", status_code=204)
async def delete_sequence(
    project_id: UUID,
    sequence_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """Delete a sequence. Cannot delete the default sequence."""
    await get_accessible_project(project_id, current_user.id, db)

    result = await db.execute(
        select(Sequence)
        .where(Sequence.id == sequence_id, Sequence.project_id == project_id)
    )
    seq = result.scalar_one_or_none()

    if seq is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sequence not found",
        )

    if seq.is_default:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the default sequence",
        )

    await db.delete(seq)
    await db.flush()


@router.post("/{project_id}/sequences/{sequence_id}/lock", response_model=LockResponse)
async def acquire_lock(
    project_id: UUID,
    sequence_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> LockResponse:
    """Acquire a lock on a sequence.

    Lock is granted if:
    - No one holds the lock (locked_by is NULL)
    - The existing lock has expired (locked_at > 2 minutes ago)
    - The current user already holds the lock (refresh)
    """
    await get_accessible_project(project_id, current_user.id, db)

    result = await db.execute(
        select(Sequence)
        .where(Sequence.id == sequence_id, Sequence.project_id == project_id)
        .with_for_update()
    )
    seq = result.scalar_one_or_none()

    if seq is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sequence not found",
        )

    now = datetime.now(timezone.utc)

    # Check if lock can be acquired
    if seq.locked_by is not None and seq.locked_by != current_user.id:
        if not _is_lock_expired(seq.locked_at):
            # Lock is held by someone else and not expired
            lock_holder_name = None
            user_result = await db.execute(select(User.name).where(User.id == seq.locked_by))
            lock_holder_name = user_result.scalar_one_or_none()

            return LockResponse(
                locked=False,
                locked_by=seq.locked_by,
                lock_holder_name=lock_holder_name,
                locked_at=seq.locked_at,
            )

    # Grant lock
    seq.locked_by = current_user.id
    seq.locked_at = now
    await db.flush()

    _settings = get_settings()
    token = create_edit_token(
        project_id=project_id,
        sequence_id=sequence_id,
        user_id=current_user.id,
        secret=_settings.edit_token_secret,
    )

    return LockResponse(
        locked=True,
        locked_by=current_user.id,
        lock_holder_name=current_user.name,
        locked_at=now,
        edit_token=token,
    )


@router.post("/{project_id}/sequences/{sequence_id}/heartbeat", response_model=LockResponse)
async def heartbeat(
    project_id: UUID,
    sequence_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> LockResponse:
    """Send a heartbeat to keep the lock alive.

    Should be called every 30 seconds by the client.
    """
    await get_accessible_project(project_id, current_user.id, db)

    result = await db.execute(
        select(Sequence)
        .where(Sequence.id == sequence_id, Sequence.project_id == project_id)
        .with_for_update()
    )
    seq = result.scalar_one_or_none()

    if seq is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sequence not found",
        )

    if seq.locked_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not hold the lock on this sequence",
        )

    now = datetime.now(timezone.utc)
    seq.locked_at = now
    await db.flush()

    _settings = get_settings()
    token = create_edit_token(
        project_id=project_id,
        sequence_id=sequence_id,
        user_id=current_user.id,
        secret=_settings.edit_token_secret,
    )

    return LockResponse(
        locked=True,
        locked_by=current_user.id,
        lock_holder_name=current_user.name,
        locked_at=now,
        edit_token=token,
    )


@router.post("/{project_id}/sequences/{sequence_id}/unlock", response_model=LockResponse)
async def release_lock(
    project_id: UUID,
    sequence_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> LockResponse:
    """Release the lock on a sequence.

    Only the lock holder can release the lock.
    """
    await get_accessible_project(project_id, current_user.id, db)

    result = await db.execute(
        select(Sequence)
        .where(Sequence.id == sequence_id, Sequence.project_id == project_id)
        .with_for_update()
    )
    seq = result.scalar_one_or_none()

    if seq is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Sequence not found",
        )

    if seq.locked_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not hold the lock on this sequence",
        )

    seq.locked_by = None
    seq.locked_at = None
    await db.flush()

    return LockResponse(
        locked=False,
        locked_by=None,
        lock_holder_name=None,
        locked_at=None,
    )


# --- Snapshot (Checkpoint) Endpoints ---


@router.get("/{project_id}/sequences/{sequence_id}/snapshots", response_model=list[SnapshotDetail])
async def list_snapshots(
    project_id: UUID,
    sequence_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> list[SnapshotDetail]:
    """List all snapshots (checkpoints) for a sequence, newest first."""
    await get_accessible_project(project_id, current_user.id, db)

    # Verify sequence exists and belongs to project
    seq_result = await db.execute(
        select(Sequence.id)
        .where(Sequence.id == sequence_id, Sequence.project_id == project_id)
    )
    if seq_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sequence not found")

    result = await db.execute(
        select(SequenceSnapshot)
        .where(SequenceSnapshot.sequence_id == sequence_id)
        .order_by(SequenceSnapshot.created_at.desc())
    )

    return [
        SnapshotDetail(
            id=snap.id,
            sequence_id=snap.sequence_id,
            name=snap.name,
            duration_ms=snap.duration_ms,
            created_at=snap.created_at,
            updated_at=snap.updated_at,
        )
        for snap in result.scalars().all()
    ]


@router.post("/{project_id}/sequences/{sequence_id}/snapshots", response_model=SnapshotDetail, status_code=201)
async def create_snapshot(
    project_id: UUID,
    sequence_id: UUID,
    body: SnapshotCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> SnapshotDetail:
    """Create a snapshot (checkpoint) of the current sequence state.

    Copies the sequence's current timeline_data and duration_ms.
    """
    await get_accessible_project(project_id, current_user.id, db)

    # Fetch the sequence
    result = await db.execute(
        select(Sequence)
        .where(Sequence.id == sequence_id, Sequence.project_id == project_id)
    )
    seq = result.scalar_one_or_none()

    if seq is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sequence not found")

    # Create snapshot from current state
    snap = SequenceSnapshot(
        sequence_id=sequence_id,
        name=body.name,
        timeline_data=seq.timeline_data,
        duration_ms=seq.duration_ms,
    )
    db.add(snap)
    await db.flush()
    await db.refresh(snap)

    return SnapshotDetail(
        id=snap.id,
        sequence_id=snap.sequence_id,
        name=snap.name,
        duration_ms=snap.duration_ms,
        created_at=snap.created_at,
        updated_at=snap.updated_at,
    )


@router.post("/{project_id}/sequences/{sequence_id}/snapshots/{snapshot_id}/restore", response_model=SequenceDetail)
async def restore_snapshot(
    project_id: UUID,
    sequence_id: UUID,
    snapshot_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> SequenceDetail:
    """Restore a sequence from a snapshot.

    Overwrites the sequence's timeline_data with the snapshot's data.
    Requires the user to hold the lock on the sequence.
    Increments the sequence version.
    """
    await get_accessible_project(project_id, current_user.id, db)

    # Fetch sequence with row-level lock
    seq_result = await db.execute(
        select(Sequence)
        .where(Sequence.id == sequence_id, Sequence.project_id == project_id)
        .with_for_update()
    )
    seq = seq_result.scalar_one_or_none()

    if seq is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sequence not found")

    # Lock check
    if seq.locked_by != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sequence is not locked by you. Acquire a lock before restoring.",
        )

    # Fetch snapshot
    snap_result = await db.execute(
        select(SequenceSnapshot)
        .where(SequenceSnapshot.id == snapshot_id, SequenceSnapshot.sequence_id == sequence_id)
    )
    snap = snap_result.scalar_one_or_none()

    if snap is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")

    # Restore: overwrite timeline_data
    seq.timeline_data = snap.timeline_data
    flag_modified(seq, "timeline_data")
    seq.duration_ms = snap.duration_ms
    seq.version += 1
    seq.locked_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(seq)

    # Fetch lock holder name
    lock_holder_name = None
    if seq.locked_by:
        user_result = await db.execute(select(User.name).where(User.id == seq.locked_by))
        lock_holder_name = user_result.scalar_one_or_none()

    return SequenceDetail(
        id=seq.id,
        project_id=seq.project_id,
        name=seq.name,
        timeline_data=seq.timeline_data,
        version=seq.version,
        duration_ms=seq.duration_ms,
        is_default=seq.is_default,
        locked_by=seq.locked_by,
        lock_holder_name=lock_holder_name,
        thumbnail_url=_get_sequence_thumbnail_url(seq),
        locked_at=seq.locked_at,
        created_at=seq.created_at,
        updated_at=seq.updated_at,
    )


@router.delete("/{project_id}/sequences/{sequence_id}/snapshots/{snapshot_id}", status_code=204)
async def delete_snapshot(
    project_id: UUID,
    sequence_id: UUID,
    snapshot_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """Delete a snapshot."""
    await get_accessible_project(project_id, current_user.id, db)

    # Verify sequence belongs to project
    seq_result = await db.execute(
        select(Sequence.id)
        .where(Sequence.id == sequence_id, Sequence.project_id == project_id)
    )
    if seq_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sequence not found")

    # Fetch snapshot
    snap_result = await db.execute(
        select(SequenceSnapshot)
        .where(SequenceSnapshot.id == snapshot_id, SequenceSnapshot.sequence_id == sequence_id)
    )
    snap = snap_result.scalar_one_or_none()

    if snap is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")

    await db.delete(snap)
    await db.flush()


# --- Thumbnail Endpoint ---


class SequenceThumbnailUploadRequest(BaseModel):
    """Request model for uploading sequence thumbnail."""

    image_data: str  # Base64 encoded image data (with or without data URI prefix)


class SequenceThumbnailUploadResponse(BaseModel):
    """Response model for sequence thumbnail upload."""

    thumbnail_url: str


@router.post("/{project_id}/sequences/{sequence_id}/thumbnail", response_model=SequenceThumbnailUploadResponse)
async def upload_sequence_thumbnail(
    project_id: UUID,
    sequence_id: UUID,
    request: SequenceThumbnailUploadRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> SequenceThumbnailUploadResponse:
    """Upload a thumbnail image for a sequence.

    The image should be sent as base64-encoded data.
    Supports PNG and JPEG formats.
    """
    await get_accessible_project(project_id, current_user.id, db)

    result = await db.execute(
        select(Sequence).where(Sequence.id == sequence_id, Sequence.project_id == project_id)
    )
    seq = result.scalar_one_or_none()
    if seq is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sequence not found")

    # Parse base64 data (handle data URI prefix if present)
    image_data = request.image_data
    content_type = "image/png"  # default

    if image_data.startswith("data:"):
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
    storage_key = f"thumbnails/sequences/{sequence_id}/thumbnail.{extension}"

    # Delete old thumbnail if it exists and has different extension
    old_extensions = ["png", "jpg"]
    for ext in old_extensions:
        old_key = f"thumbnails/sequences/{sequence_id}/thumbnail.{ext}"
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

    # Save storage key to the sequence
    seq.thumbnail_storage_key = storage_key
    await db.flush()

    # Generate signed URL for response
    thumbnail_url = storage.generate_download_url(
        storage_key, expires_minutes=60 * 24 * 7
    )  # 7 days

    logger.info(f"Uploaded thumbnail for sequence {sequence_id}")

    return SequenceThumbnailUploadResponse(thumbnail_url=thumbnail_url)
