"""
Tests for issue-261: role-based write control and issue-154 auth regression.

These tests use AsyncMock to simulate DB queries and verify:
- viewer members are denied write access (403)
- editor members are allowed write access (200)
- non-members get 404 (not 403, to avoid leaking project existence)
- owner-only fields (ai_api_key, ai_provider) are restricted to owner
- unauthenticated requests return 401 (regression test for #154)

No database connection required (pure unit tests).
"""

import uuid
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api import deps as deps_module
from src.api.access import get_accessible_project
from src.models.project import Project
from src.models.project_member import ProjectMember


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(owner_id: uuid.UUID) -> Project:
    p = MagicMock(spec=Project)
    p.id = uuid.uuid4()
    p.user_id = owner_id
    return p


def _make_member(project_id: uuid.UUID, user_id: uuid.UUID, role: str) -> ProjectMember:
    m = MagicMock(spec=ProjectMember)
    m.project_id = project_id
    m.user_id = user_id
    m.role = role
    m.accepted_at = datetime.now(UTC)  # accepted
    return m


def _make_db(project: Project | None, member: ProjectMember | None) -> AsyncMock:
    """Build a mock AsyncSession that returns the given project and member."""
    db = AsyncMock()

    async def fake_execute(stmt):
        result = MagicMock()
        # Detect which model is being queried by inspecting whereclause columns
        try:
            col_names = [c.key for c in stmt.column_descriptions]
        except Exception:
            col_names = []

        if "ProjectMember" in str(stmt) or "project_members" in str(stmt):
            result.scalar_one_or_none = MagicMock(return_value=member)
        else:
            result.scalar_one_or_none = MagicMock(return_value=project)
        return result

    db.execute = fake_execute
    return db


# ---------------------------------------------------------------------------
# Unit tests for get_accessible_project
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_always_has_access():
    """Project owner bypasses all role checks."""
    owner_id = uuid.uuid4()
    project = _make_project(owner_id)
    db = _make_db(project, None)

    result = await get_accessible_project(project.id, owner_id, db, require_role="owner")
    assert result is project


@pytest.mark.asyncio
async def test_owner_bypasses_write_role():
    """Owner is always allowed, even for require_role='editor'."""
    owner_id = uuid.uuid4()
    project = _make_project(owner_id)
    db = _make_db(project, None)

    result = await get_accessible_project(project.id, owner_id, db, require_role="editor")
    assert result is project


@pytest.mark.asyncio
async def test_editor_member_can_write():
    """Members with role='editor' pass require_role='editor'."""
    owner_id = uuid.uuid4()
    editor_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, editor_id, "editor")
    db = _make_db(project, member)

    result = await get_accessible_project(project.id, editor_id, db, require_role="editor")
    assert result is project


@pytest.mark.asyncio
async def test_viewer_member_denied_write():
    """Members with role='viewer' are denied for require_role='editor'."""
    owner_id = uuid.uuid4()
    viewer_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, viewer_id, "viewer")
    db = _make_db(project, member)

    with pytest.raises(HTTPException) as exc_info:
        await get_accessible_project(project.id, viewer_id, db, require_role="editor")
    assert exc_info.value.status_code == 403
    assert "viewer" in exc_info.value.detail


@pytest.mark.asyncio
async def test_viewer_member_can_read():
    """Members with role='viewer' are allowed for require_role=None (read)."""
    owner_id = uuid.uuid4()
    viewer_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, viewer_id, "viewer")
    db = _make_db(project, member)

    result = await get_accessible_project(project.id, viewer_id, db, require_role=None)
    assert result is project


@pytest.mark.asyncio
async def test_non_member_gets_404_not_403():
    """Non-members get 404 (not 403) to avoid leaking project existence."""
    owner_id = uuid.uuid4()
    stranger_id = uuid.uuid4()
    project = _make_project(owner_id)
    db = _make_db(project, None)  # no member record

    with pytest.raises(HTTPException) as exc_info:
        await get_accessible_project(project.id, stranger_id, db)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_member_cannot_do_owner_only():
    """Editor members are denied for require_role='owner'."""
    owner_id = uuid.uuid4()
    editor_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, editor_id, "editor")
    db = _make_db(project, member)

    with pytest.raises(HTTPException) as exc_info:
        await get_accessible_project(project.id, editor_id, db, require_role="owner")
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_unknown_role_treated_as_editor():
    """Unknown role values are treated as 'editor' for backward compatibility."""
    owner_id = uuid.uuid4()
    user_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, user_id, "future_role")  # unknown role
    db = _make_db(project, member)

    # Should succeed (unknown = editor rank)
    result = await get_accessible_project(project.id, user_id, db, require_role="editor")
    assert result is project


# ---------------------------------------------------------------------------
# Regression test: #154 — unauthenticated request must return 401
# ---------------------------------------------------------------------------


@pytest.fixture
def client_prod_mode(monkeypatch):
    """TestClient with dev_mode=False (simulates production auth)."""
    from copy import deepcopy
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi.testclient import TestClient

    from src.main import app
    from src.models.database import get_db

    patched = deepcopy(deps_module.settings)
    patched.dev_mode = False
    monkeypatch.setattr(deps_module, "settings", patched)

    async def _fake_db():
        session = MagicMock()
        session.execute = AsyncMock(return_value=MagicMock())
        session.commit = AsyncMock()
        session.rollback = AsyncMock()
        session.close = AsyncMock()
        yield session

    app.dependency_overrides[get_db] = _fake_db
    try:
        with (
            patch("src.main.init_db", new=AsyncMock()),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_unauthenticated_sequences_returns_401(client_prod_mode):
    """Regression for #154: GET /sequences without auth must return 401."""
    project_id = uuid.uuid4()
    resp = client_prod_mode.get(f"/api/projects/{project_id}/sequences")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


def test_unauthenticated_project_get_returns_401(client_prod_mode):
    """GET /projects/{id} without auth must return 401."""
    project_id = uuid.uuid4()
    resp = client_prod_mode.get(f"/api/projects/{project_id}")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
