from __future__ import annotations

from collections.abc import Sequence as TypingSequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.api import sequences as sequences_api
from src.models.sequence import Sequence
from src.models.user import User


class _FakeScalars:
    def __init__(self, items: TypingSequence[object]):
        self._items = items

    def all(self) -> list[object]:
        return list(self._items)


class _FakeResult:
    def __init__(
        self,
        *,
        scalar: object | None = None,
        scalar_items: TypingSequence[object] | None = None,
    ):
        self._scalar = scalar
        self._scalar_items = scalar_items or []

    def scalar_one_or_none(self) -> object | None:
        return self._scalar

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._scalar_items)


@dataclass
class _FakeSession:
    sequences: dict[UUID, Sequence]
    user_names: dict[UUID, str]
    flush_count: int = 0

    async def execute(self, query: Any) -> _FakeResult:
        column_name = query.column_descriptions[0]["name"]
        conditions = {
            criterion.left.name: (criterion.operator.__name__, criterion.right.value)
            for criterion in query._where_criteria
        }

        if column_name == "id":
            user_id = conditions["id"][1]
            return _FakeResult(scalar=user_id if user_id in self.user_names else None)

        if column_name == "name":
            user_id = conditions["id"][1]
            return _FakeResult(scalar=self.user_names.get(user_id))

        if column_name != "Sequence":
            raise AssertionError(f"Unexpected query shape: {query}")

        if "project_id" in conditions:
            sequence_id = conditions["id"][1]
            project_id = conditions["project_id"][1]
            seq = self.sequences.get(sequence_id)
            if seq is None or seq.project_id != project_id:
                return _FakeResult()
            return _FakeResult(scalar=seq)

        locked_by = conditions["locked_by"][1]
        excluded_sequence_id = conditions["id"][1]
        other_sequences = [
            seq
            for seq in self.sequences.values()
            if seq.locked_by == locked_by and seq.id != excluded_sequence_id
        ]
        return _FakeResult(scalar_items=other_sequences)

    async def flush(self) -> None:
        self.flush_count += 1


def _make_sequence(
    project_id: UUID,
    *,
    sequence_id: UUID | None = None,
    locked_by: UUID | None = None,
    locked_at: datetime | None = None,
) -> Sequence:
    return Sequence(
        id=sequence_id or uuid4(),
        project_id=project_id,
        name="Sequence",
        timeline_data={"version": "1.0", "layers": [], "audio_tracks": []},
        version=1,
        duration_ms=0,
        is_default=False,
        locked_by=locked_by,
        locked_at=locked_at,
    )


@pytest.fixture(autouse=True)
def _stub_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_get_accessible_project(
        project_id: UUID,
        user_id: UUID,
        db: Any,
    ) -> SimpleNamespace:
        return SimpleNamespace(id=project_id, user_id=user_id)

    monkeypatch.setattr(sequences_api, "get_accessible_project", _fake_get_accessible_project)
    monkeypatch.setattr(
        sequences_api,
        "create_edit_token",
        lambda **kwargs: f"token:{kwargs['sequence_id']}",
    )


@pytest.mark.asyncio
async def test_acquire_lock_releases_other_sequence_locks_for_same_user() -> None:
    project_id = uuid4()
    user_id = uuid4()
    now = datetime.now(UTC)
    current_user = cast(User, SimpleNamespace(id=user_id, name="Current User"))

    previous_sequence = _make_sequence(
        project_id,
        locked_by=user_id,
        locked_at=now - timedelta(seconds=15),
    )
    target_sequence = _make_sequence(project_id)
    db = _FakeSession(
        sequences={
            previous_sequence.id: previous_sequence,
            target_sequence.id: target_sequence,
        },
        user_names={user_id: current_user.name},
    )

    response = await sequences_api.acquire_lock(
        project_id,
        target_sequence.id,
        current_user,
        cast(AsyncSession, db),
    )

    assert response.locked is True
    assert response.locked_by == user_id
    assert response.lock_holder_name == current_user.name
    assert response.edit_token == f"token:{target_sequence.id}"
    assert previous_sequence.locked_by is None
    assert previous_sequence.locked_at is None
    assert target_sequence.locked_by == user_id
    assert target_sequence.locked_at is not None
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_acquire_lock_does_not_drop_existing_lock_when_target_is_owned_by_other_user() -> None:
    project_id = uuid4()
    current_user_id = uuid4()
    other_user_id = uuid4()
    now = datetime.now(UTC)
    current_user = cast(User, SimpleNamespace(id=current_user_id, name="Current User"))

    existing_sequence = _make_sequence(
        project_id,
        locked_by=current_user_id,
        locked_at=now - timedelta(seconds=20),
    )
    target_sequence = _make_sequence(
        project_id,
        locked_by=other_user_id,
        locked_at=now - timedelta(seconds=10),
    )
    db = _FakeSession(
        sequences={
            existing_sequence.id: existing_sequence,
            target_sequence.id: target_sequence,
        },
        user_names={
            current_user_id: current_user.name,
            other_user_id: "Other User",
        },
    )

    response = await sequences_api.acquire_lock(
        project_id,
        target_sequence.id,
        current_user,
        cast(AsyncSession, db),
    )

    assert response.locked is False
    assert response.locked_by == other_user_id
    assert response.lock_holder_name == "Other User"
    assert existing_sequence.locked_by == current_user_id
    assert target_sequence.locked_by == other_user_id
    assert db.flush_count == 0


@pytest.mark.asyncio
async def test_acquire_lock_refresh_clears_stale_duplicate_owned_locks() -> None:
    project_id = uuid4()
    user_id = uuid4()
    now = datetime.now(UTC)
    current_user = cast(User, SimpleNamespace(id=user_id, name="Current User"))

    duplicate_sequence = _make_sequence(
        project_id,
        locked_by=user_id,
        locked_at=now - timedelta(seconds=25),
    )
    target_sequence = _make_sequence(
        project_id,
        locked_by=user_id,
        locked_at=now - timedelta(seconds=5),
    )
    previous_target_lock_time = target_sequence.locked_at
    assert previous_target_lock_time is not None
    db = _FakeSession(
        sequences={
            duplicate_sequence.id: duplicate_sequence,
            target_sequence.id: target_sequence,
        },
        user_names={user_id: current_user.name},
    )

    response = await sequences_api.acquire_lock(
        project_id,
        target_sequence.id,
        current_user,
        cast(AsyncSession, db),
    )

    assert response.locked is True
    assert duplicate_sequence.locked_by is None
    assert duplicate_sequence.locked_at is None
    assert target_sequence.locked_by == user_id
    assert target_sequence.locked_at is not None
    assert target_sequence.locked_at >= previous_target_lock_time
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_previous_sequence_heartbeat_is_rejected_after_lock_moves_to_new_sequence() -> None:
    project_id = uuid4()
    user_id = uuid4()
    now = datetime.now(UTC)
    current_user = cast(User, SimpleNamespace(id=user_id, name="Current User"))

    previous_sequence = _make_sequence(
        project_id,
        locked_by=user_id,
        locked_at=now - timedelta(seconds=10),
    )
    target_sequence = _make_sequence(project_id)
    db = _FakeSession(
        sequences={
            previous_sequence.id: previous_sequence,
            target_sequence.id: target_sequence,
        },
        user_names={user_id: current_user.name},
    )

    await sequences_api.acquire_lock(
        project_id,
        target_sequence.id,
        current_user,
        cast(AsyncSession, db),
    )

    with pytest.raises(HTTPException) as exc_info:
        await sequences_api.heartbeat(
            project_id,
            previous_sequence.id,
            current_user,
            cast(AsyncSession, db),
        )

    assert exc_info.value.status_code == 403
    assert "do not hold the lock" in exc_info.value.detail
