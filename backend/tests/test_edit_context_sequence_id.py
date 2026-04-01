"""Tests for get_edit_context() sequence_id resolution.

Verifies the resolution priority:
  X-Edit-Session > sequence_id query param > default sequence > project fallback
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.api import deps as deps_mod
from src.models.sequence import Sequence

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(project_id: UUID) -> SimpleNamespace:
    return SimpleNamespace(
        id=project_id,
        user_id=uuid4(),
        timeline_data={"source": "project"},
        version=1,
        duration_ms=5000,
    )


def _make_sequence(
    project_id: UUID,
    *,
    sequence_id: UUID | None = None,
    is_default: bool = False,
    timeline_data: dict | None = None,
) -> Sequence:
    return Sequence(
        id=sequence_id or uuid4(),
        project_id=project_id,
        name="Test Sequence",
        timeline_data=timeline_data or {"source": "sequence", "layers": [], "audio_tracks": []},
        version=1,
        duration_ms=10000,
        is_default=is_default,
        locked_by=None,
        locked_at=None,
    )


class _FakeResult:
    def __init__(self, scalar: object | None = None):
        self._scalar = scalar

    def scalar_one_or_none(self) -> object | None:
        return self._scalar


@dataclass
class _FakeDB:
    """Minimal fake AsyncSession that handles select(Sequence).where(...) queries."""

    sequences: dict[UUID, Sequence] = field(default_factory=dict)

    async def execute(self, query: Any) -> _FakeResult:
        # Extract WHERE conditions
        conditions: dict[str, Any] = {}
        for criterion in query._where_criteria:
            col_name = criterion.left.name
            rhs = criterion.right
            if hasattr(rhs, "value"):
                val = rhs.value
            elif type(rhs).__name__ == "True_":
                val = True
            elif type(rhs).__name__ == "False_":
                val = False
            else:
                val = rhs
            conditions[col_name] = val

        # is_default query (step 3 in get_edit_context)
        if "is_default" in conditions:
            pid = conditions["project_id"]
            for seq in self.sequences.values():
                if seq.project_id == pid and seq.is_default:
                    return _FakeResult(scalar=seq)
            return _FakeResult()

        # sequence lookup by id + project_id (step 1 or 2)
        if "id" in conditions and "project_id" in conditions:
            seq_id = (
                UUID(str(conditions["id"]))
                if not isinstance(conditions["id"], UUID)
                else conditions["id"]
            )
            pid = conditions["project_id"]
            seq = self.sequences.get(seq_id)
            if seq and seq.project_id == pid:
                return _FakeResult(scalar=seq)
            return _FakeResult()

        raise AssertionError(f"Unexpected query conditions: {conditions}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub get_accessible_project so it doesn't need a real DB."""

    async def _fake_get_accessible_project(
        project_id: UUID, user_id: UUID, db: Any
    ) -> SimpleNamespace:
        return _make_project(project_id)

    monkeypatch.setattr("src.api.access.get_accessible_project", _fake_get_accessible_project)


# ---------------------------------------------------------------------------
# 1. sequence_id resolves the correct sequence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequence_id_resolves_target_sequence() -> None:
    """sequence_id query param loads the specified sequence."""
    project_id = uuid4()
    seq = _make_sequence(project_id, timeline_data={"source": "targeted"})
    db = _FakeDB(sequences={seq.id: seq})
    user = cast("deps_mod.User", SimpleNamespace(id=uuid4()))

    ctx = await deps_mod.get_edit_context(
        project_id, user, cast(AsyncSession, db), sequence_id=seq.id
    )

    assert ctx.sequence is seq
    assert ctx.timeline_data == {"source": "targeted"}


# ---------------------------------------------------------------------------
# 2. Priority: X-Edit-Session > sequence_id > default sequence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_x_edit_session_takes_priority_over_sequence_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both X-Edit-Session and sequence_id are provided, X-Edit-Session wins."""
    project_id = uuid4()
    user_id = uuid4()

    session_seq = _make_sequence(project_id, timeline_data={"source": "from_edit_session"})
    param_seq = _make_sequence(project_id, timeline_data={"source": "from_query_param"})
    db = _FakeDB(sequences={session_seq.id: session_seq, param_seq.id: param_seq})

    # Stub decode_edit_token to return the session sequence
    monkeypatch.setattr(
        deps_mod,
        "decode_edit_token",
        lambda token, secret: {
            "pid": str(project_id),
            "sid": str(session_seq.id),
            "uid": str(user_id),
        },
    )

    user = cast("deps_mod.User", SimpleNamespace(id=user_id))
    ctx = await deps_mod.get_edit_context(
        project_id,
        user,
        cast(AsyncSession, db),
        x_edit_session="fake-token",
        sequence_id=param_seq.id,
    )

    assert ctx.sequence is not None
    assert ctx.sequence.id == session_seq.id
    assert ctx.timeline_data == {"source": "from_edit_session"}


@pytest.mark.asyncio
async def test_sequence_id_takes_priority_over_default_sequence() -> None:
    """sequence_id is used even when a default sequence exists."""
    project_id = uuid4()

    default_seq = _make_sequence(project_id, is_default=True, timeline_data={"source": "default"})
    target_seq = _make_sequence(project_id, timeline_data={"source": "explicit_target"})
    db = _FakeDB(sequences={default_seq.id: default_seq, target_seq.id: target_seq})
    user = cast("deps_mod.User", SimpleNamespace(id=uuid4()))

    ctx = await deps_mod.get_edit_context(
        project_id, user, cast(AsyncSession, db), sequence_id=target_seq.id
    )

    assert ctx.sequence is target_seq
    assert ctx.timeline_data == {"source": "explicit_target"}


@pytest.mark.asyncio
async def test_default_sequence_used_when_no_session_and_no_sequence_id() -> None:
    """Falls back to default sequence when neither is provided."""
    project_id = uuid4()
    default_seq = _make_sequence(project_id, is_default=True, timeline_data={"source": "default"})
    db = _FakeDB(sequences={default_seq.id: default_seq})
    user = cast("deps_mod.User", SimpleNamespace(id=uuid4()))

    ctx = await deps_mod.get_edit_context(project_id, user, cast(AsyncSession, db))

    assert ctx.sequence is default_seq
    assert ctx.timeline_data == {"source": "default"}


@pytest.mark.asyncio
async def test_project_fallback_when_no_sequences() -> None:
    """Falls back to project timeline_data when no sequences exist."""
    project_id = uuid4()
    db = _FakeDB(sequences={})
    user = cast("deps_mod.User", SimpleNamespace(id=uuid4()))

    ctx = await deps_mod.get_edit_context(project_id, user, cast(AsyncSession, db))

    assert ctx.sequence is None
    assert ctx.timeline_data == {"source": "project"}


# ---------------------------------------------------------------------------
# 3. Non-existent or wrong-project sequence_id → 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nonexistent_sequence_id_raises_404() -> None:
    """Passing a sequence_id that doesn't exist returns 404."""
    project_id = uuid4()
    db = _FakeDB(sequences={})
    user = cast("deps_mod.User", SimpleNamespace(id=uuid4()))

    with pytest.raises(HTTPException) as exc_info:
        await deps_mod.get_edit_context(
            project_id, user, cast(AsyncSession, db), sequence_id=uuid4()
        )

    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail


@pytest.mark.asyncio
async def test_sequence_id_from_different_project_raises_404() -> None:
    """A sequence belonging to another project returns 404."""
    project_id = uuid4()
    other_project_id = uuid4()
    seq = _make_sequence(other_project_id)  # belongs to a different project
    db = _FakeDB(sequences={seq.id: seq})
    user = cast("deps_mod.User", SimpleNamespace(id=uuid4()))

    with pytest.raises(HTTPException) as exc_info:
        await deps_mod.get_edit_context(
            project_id, user, cast(AsyncSession, db), sequence_id=seq.id
        )

    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail
