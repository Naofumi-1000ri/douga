"""Sequences API for multi-sequence timeline editing.

Each sequence has its own timeline_data, version, and lock state.
Locking prevents concurrent edits: lock expires after 2 minutes without heartbeat.
"""

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.attributes import flag_modified

from src.api.access import get_accessible_project
from src.api.deps import CurrentUser, DbSession
from src.models.sequence import Sequence, _default_timeline_data
from src.models.user import User
from src.schemas.sequence import (
    LockResponse,
    SequenceCreate,
    SequenceDefaultResponse,
    SequenceDetail,
    SequenceListItem,
    SequenceUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Lock expires after 2 minutes without heartbeat
LOCK_TIMEOUT = timedelta(minutes=2)


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

    return LockResponse(
        locked=True,
        locked_by=current_user.id,
        lock_holder_name=current_user.name,
        locked_at=now,
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

    return LockResponse(
        locked=True,
        locked_by=current_user.id,
        lock_holder_name=current_user.name,
        locked_at=now,
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
