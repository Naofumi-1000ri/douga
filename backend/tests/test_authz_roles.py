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
        # Detect which model is being queried from the rendered statement
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
async def test_unknown_role_treated_as_viewer_fail_closed():
    """Unknown role values are treated as 'viewer' (fail-closed, finding C).

    If a future migration introduces a new role before the API layer knows it,
    the safe failure mode is read-only — writes must be denied.
    """
    owner_id = uuid.uuid4()
    user_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, user_id, "future_role")  # unknown role
    db = _make_db(project, member)

    # Write must be DENIED (unknown = viewer rank)
    with pytest.raises(HTTPException) as exc_info:
        await get_accessible_project(project.id, user_id, db, require_role="editor")
    assert exc_info.value.status_code == 403

    # Read must still succeed
    result = await get_accessible_project(project.id, user_id, db, require_role=None)
    assert result is project


# ---------------------------------------------------------------------------
# Regression test: #154 — unauthenticated request must return 401
# ---------------------------------------------------------------------------


@pytest.fixture
def client_prod_mode(monkeypatch):
    """TestClient with dev_mode=False (simulates production auth)."""
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


# ---------------------------------------------------------------------------
# Finding A (#261 review): V1 API write endpoints must enforce editor role
# ---------------------------------------------------------------------------
#
# The V1 API (X-API-Key / MCP path) resolves the API key to its owning User
# in deps._authenticate_user, after which authorization is identical to the
# Firebase-token path: get_accessible_project evaluates that User's project
# membership role. These tests therefore cover BOTH auth methods — a viewer
# member's API key hits the exact same 403 as their browser session.


def _make_user(user_id: uuid.UUID) -> MagicMock:
    user = MagicMock()
    user.id = user_id
    return user


@pytest.mark.asyncio
async def test_v1_write_resolver_denies_viewer():
    """_resolve_edit_session_for_write must 403 for viewer members.

    This is the single choke point for all 29 V1 mutation endpoints
    (add_clip, delete_clip, move_clip, update_effects, batch, semantic, ...).
    """
    from src.api.ai_v1 import _resolve_edit_session_for_write

    owner_id = uuid.uuid4()
    viewer_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, viewer_id, "viewer")
    db = _make_db(project, member)

    with pytest.raises(HTTPException) as exc_info:
        await _resolve_edit_session_for_write(project.id, _make_user(viewer_id), db)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_v1_write_resolver_allows_editor():
    """_resolve_edit_session_for_write must pass for editor members."""
    from src.api.ai_v1 import _resolve_edit_session_for_write

    owner_id = uuid.uuid4()
    editor_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, editor_id, "editor")
    db = _make_db(project, member)

    resolved_project, _seq = await _resolve_edit_session_for_write(
        project.id, _make_user(editor_id), db
    )
    assert resolved_project is project


@pytest.mark.asyncio
async def test_v1_read_resolver_allows_viewer():
    """_resolve_edit_session (read path) must remain viewer-accessible."""
    from src.api.ai_v1 import _resolve_edit_session

    owner_id = uuid.uuid4()
    viewer_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, viewer_id, "viewer")
    db = _make_db(project, member)

    resolved_project, _seq = await _resolve_edit_session(project.id, _make_user(viewer_id), db)
    assert resolved_project is project


# Static verification: every V1 mutation endpoint uses the write resolver and
# every read endpoint keeps the read resolver. This pins the choke-point
# wiring so a future endpoint cannot silently regress to the viewer-open path.

_V1_WRITE_ENDPOINTS = {
    "add_clip",
    "move_clip",
    "transform_clip",
    "update_clip_effects",
    "apply_chroma_key",
    "update_clip_crop",
    "update_clip_text_style",
    "delete_clip",
    "add_layer",
    "update_layer",
    "reorder_layers",
    "add_audio_clip",
    "move_audio_clip",
    "delete_audio_clip",
    "add_audio_track",
    "add_marker",
    "update_marker",
    "delete_marker",
    "execute_batch",
    "execute_semantic",
    "rollback_operation",
    "update_audio_clip",
    "update_clip_timing",
    "update_clip_text",
    "update_clip_shape",
    "add_keyframe",
    "delete_keyframe",
    "split_clip",
    "unlink_clip",
}

_V1_READ_ENDPOINTS = {
    "get_project_overview",
    "get_project_summary",
    "get_timeline_structure",
    "get_timeline_overview",
    "get_asset_catalog",
    "preview_chroma_key",  # generates preview frames only; no timeline mutation
    "get_clip_details",
    "get_timeline_at_time",
    "get_history",
    "get_operation",
    "get_audio_clip_details",
    "analyze_gaps",
    "analyze_pacing",
    "preview_diff",  # dry-run simulation; no timeline mutation
}


def _collect_v1_resolver_usage() -> dict[str, set[str]]:
    """Parse ai_v1 source files and map each function to the resolver names it calls.

    Handles both the legacy monolithic ``ai_v1.py`` and the split package
    (``api/ai_v1/`` directory) introduced in issue #284.  When the module is a
    package, all ``*.py`` sibling files in its directory are parsed together so
    that route functions defined in sub-modules (clips.py, layers.py, …) are
    found correctly.
    """
    import ast
    from pathlib import Path

    import src.api.ai_v1 as ai_v1_module

    init_path = Path(ai_v1_module.__file__)
    if init_path.name == "__init__.py":
        # Package layout: parse every .py file in the package directory.
        source_files = list(init_path.parent.glob("*.py"))
    else:
        source_files = [init_path]

    usage: dict[str, set[str]] = {}

    for src_file in source_files:
        source = src_file.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                calls: set[str] = set()
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                        if sub.func.id in (
                            "_resolve_edit_session",
                            "_resolve_edit_session_for_write",
                        ):
                            calls.add(sub.func.id)
                if calls:
                    usage[node.name] = calls
    return usage


def test_v1_write_endpoints_use_write_resolver():
    """All V1 mutation endpoints must resolve via _resolve_edit_session_for_write."""
    usage = _collect_v1_resolver_usage()
    violations = []
    for fn in sorted(_V1_WRITE_ENDPOINTS):
        calls = usage.get(fn, set())
        if "_resolve_edit_session_for_write" not in calls:
            violations.append(f"{fn}: calls {calls or 'nothing'}")
    assert not violations, "V1 write endpoints not enforcing editor role:\n" + "\n".join(violations)


def test_v1_read_endpoints_use_read_resolver():
    """V1 read endpoints must NOT require editor (viewer stays read-capable)."""
    usage = _collect_v1_resolver_usage()
    violations = []
    for fn in sorted(_V1_READ_ENDPOINTS):
        calls = usage.get(fn, set())
        if "_resolve_edit_session_for_write" in calls:
            violations.append(f"{fn}: unexpectedly requires editor")
    assert not violations, (
        "V1 read endpoints over-restricted (viewer would lose read access):\n"
        + "\n".join(violations)
    )


def test_v1_resolver_coverage_is_exhaustive():
    """Every endpoint calling a resolver must be classified as read or write.

    A new V1 endpoint that calls _resolve_edit_session* without being added
    to the lists above fails here, forcing an explicit authz decision.
    """
    usage = _collect_v1_resolver_usage()
    # The write wrapper itself calls _resolve_edit_session internally — exclude.
    endpoint_fns = set(usage.keys()) - {"_resolve_edit_session_for_write"}
    unclassified = endpoint_fns - _V1_WRITE_ENDPOINTS - _V1_READ_ENDPOINTS
    assert not unclassified, (
        f"Unclassified V1 endpoints (add to _V1_WRITE_ENDPOINTS or "
        f"_V1_READ_ENDPOINTS with an explicit authz decision): {sorted(unclassified)}"
    )


# ---------------------------------------------------------------------------
# Finding D (#261 review): PUT /projects/{id} ai_api_key — endpoint-level 403
# ---------------------------------------------------------------------------


@pytest.fixture
def client_as_editor_member():
    """TestClient authenticated as an editor member of someone else's project.

    get_current_user is overridden to return the editor; get_db returns a mock
    whose Project query yields a project owned by a different user and whose
    ProjectMember query yields an accepted editor membership.
    """
    from fastapi.testclient import TestClient

    from src.api.deps import get_current_user
    from src.main import app
    from src.models.database import get_db

    owner_id = uuid.uuid4()
    editor_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, editor_id, "editor")

    editor_user = MagicMock()
    editor_user.id = editor_id
    editor_user.name = "Editor"

    db = _make_db(project, member)
    # update_project also calls db.flush()/db.refresh() on success paths
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    async def _override_user():
        return editor_user

    async def _override_db():
        yield db

    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[get_db] = _override_db
    try:
        with (
            patch("src.main.init_db", new=AsyncMock()),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            yield client, project
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


def test_put_project_ai_api_key_as_editor_returns_403(client_as_editor_member):
    """Endpoint-level: editor member sending ai_api_key must get 403."""
    client, project = client_as_editor_member
    resp = client.put(
        f"/api/projects/{project.id}",
        json={"ai_api_key": "sk-stolen-key-attempt"},
    )
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"
    assert "owner" in resp.json()["detail"].lower()


def test_put_project_ai_provider_as_editor_returns_403(client_as_editor_member):
    """Endpoint-level: editor member sending ai_provider must get 403."""
    client, project = client_as_editor_member
    resp = client.put(
        f"/api/projects/{project.id}",
        json={"ai_provider": "openai"},
    )
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Finding E (#261 review): owner can invite members as viewer
# ---------------------------------------------------------------------------


def test_invite_member_request_role_validation():
    """InviteMemberRequest accepts editor/viewer, rejects others, defaults to editor."""
    from pydantic import ValidationError as PydanticValidationError

    from src.schemas.member import InviteMemberRequest

    assert InviteMemberRequest(email="a@example.com").role == "editor"  # default
    assert InviteMemberRequest(email="a@example.com", role="viewer").role == "viewer"
    assert InviteMemberRequest(email="a@example.com", role="editor").role == "editor"

    with pytest.raises(PydanticValidationError):
        InviteMemberRequest(email="a@example.com", role="owner")  # not invitable
    with pytest.raises(PydanticValidationError):
        InviteMemberRequest(email="a@example.com", role="admin")  # unknown


@pytest.mark.asyncio
async def test_invite_member_persists_requested_viewer_role(monkeypatch):
    """invite_member must store the role from the request (viewer)."""
    from datetime import datetime as dt

    from src.api.members import invite_member
    from src.schemas.member import InviteMemberRequest

    project_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    target_id = uuid.uuid4()

    project = MagicMock()
    project.id = project_id
    project.user_id = owner_id  # current_user IS the owner

    owner_user = MagicMock()
    owner_user.id = owner_id
    owner_user.email = "owner@example.com"

    target_user = MagicMock()
    target_user.id = target_id
    target_user.email = "invitee@example.com"
    target_user.name = "Invitee"
    target_user.avatar_url = None

    # db.execute is called 3 times: project lookup, target user lookup,
    # existing-membership lookup (None = no existing membership).
    results = []
    for value in (project, target_user, None):
        r = MagicMock()
        r.scalar_one_or_none = MagicMock(return_value=value)
        results.append(r)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=results)

    added: list = []
    db.add = MagicMock(side_effect=added.append)
    db.flush = AsyncMock()

    async def _fake_refresh(obj):
        obj.id = uuid.uuid4()
        obj.invited_at = dt.now(UTC)
        obj.accepted_at = None

    db.refresh = AsyncMock(side_effect=_fake_refresh)

    response = await invite_member(
        project_id=project_id,
        request=InviteMemberRequest(email="invitee@example.com", role="viewer"),
        current_user=owner_user,
        db=db,
    )

    assert len(added) == 1
    assert added[0].role == "viewer"
    assert response.role == "viewer"


# ---------------------------------------------------------------------------
# Issue #316: transcription.py authz — get_accessible_project integration
# ---------------------------------------------------------------------------
#
# Previously transcription.py used a bare owner-only check
# (project.user_id != current_user.id → 403).  After the fix, each endpoint
# delegates to get_accessible_project with the appropriate require_role:
#
#   POST   /transcription              → require_role="editor"  (write: starts job)
#   GET    /transcription/{id}         → require_role=None       (read: viewer ok)
#   PUT    /transcription/{id}/segments/{seg} → require_role="editor"  (write)
#   POST   /transcription/{id}/apply-cuts    → require_role="editor"  (write)
#
# The tests below verify this via the transcription endpoint functions directly,
# using the same mock-db pattern as the tests above.


def _make_asset(project_id: uuid.UUID, asset_type: str = "video") -> MagicMock:
    asset = MagicMock()
    asset.id = uuid.uuid4()
    asset.project_id = project_id
    asset.type = asset_type
    asset.asset_metadata = None
    return asset


def _make_db_with_asset(
    project: MagicMock,
    member: MagicMock | None,
    asset: MagicMock,
) -> AsyncMock:
    """Mock AsyncSession that serves project, member and asset lookups."""
    db = AsyncMock()

    async def fake_get(model_cls, pk):  # type: ignore[override]
        from src.models.asset import Asset

        if model_cls is Asset:
            return asset if asset.id == pk else None
        return None

    async def fake_execute(stmt):
        result = MagicMock()
        if "ProjectMember" in str(stmt) or "project_members" in str(stmt):
            result.scalar_one_or_none = MagicMock(return_value=member)
        else:
            result.scalar_one_or_none = MagicMock(return_value=project)
        return result

    db.get = fake_get
    db.execute = fake_execute
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


# --- GET /transcription/{asset_id} (read) ---


@pytest.mark.asyncio
async def test_transcription_get_owner_can_read():
    """Owner can read transcription results."""
    from src.api.transcription import get_transcription
    from src.schemas.timeline import Transcription

    owner_id = uuid.uuid4()
    project = _make_project(owner_id)
    asset = _make_asset(project.id)
    # Populate metadata so the endpoint doesn't return 404
    transcription_data = Transcription(
        asset_id=asset.id, language="ja", status="completed"
    )
    asset.asset_metadata = {
        "transcription": transcription_data.model_dump(mode="json")
    }

    owner_user = MagicMock()
    owner_user.id = owner_id

    db = _make_db_with_asset(project, None, asset)

    result = await get_transcription(asset.id, db=db, current_user=owner_user)
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_transcription_get_viewer_can_read():
    """Viewer member can read transcription results (read-only path)."""
    from src.api.transcription import get_transcription
    from src.schemas.timeline import Transcription

    owner_id = uuid.uuid4()
    viewer_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, viewer_id, "viewer")
    asset = _make_asset(project.id)
    transcription_data = Transcription(
        asset_id=asset.id, language="ja", status="completed"
    )
    asset.asset_metadata = {
        "transcription": transcription_data.model_dump(mode="json")
    }

    viewer_user = MagicMock()
    viewer_user.id = viewer_id

    db = _make_db_with_asset(project, member, asset)

    result = await get_transcription(asset.id, db=db, current_user=viewer_user)
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_transcription_get_non_member_gets_404():
    """Non-member gets 404 (not 403) when reading transcription."""
    from src.api.transcription import get_transcription

    owner_id = uuid.uuid4()
    stranger_id = uuid.uuid4()
    project = _make_project(owner_id)
    asset = _make_asset(project.id)

    stranger_user = MagicMock()
    stranger_user.id = stranger_id

    db = _make_db_with_asset(project, None, asset)  # no member record

    with pytest.raises(HTTPException) as exc_info:
        await get_transcription(asset.id, db=db, current_user=stranger_user)
    assert exc_info.value.status_code == 404


# --- POST /transcription (write: start) ---


@pytest.mark.asyncio
async def test_transcription_start_editor_allowed(monkeypatch):
    """Editor member can start transcription (write path)."""
    from unittest.mock import patch

    from src.api.transcription import TranscribeRequest, start_transcription
    from src.services.storage_service import LocalStorageService

    owner_id = uuid.uuid4()
    editor_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, editor_id, "editor")
    asset = _make_asset(project.id, "video")

    editor_user = MagicMock()
    editor_user.id = editor_id

    db = _make_db_with_asset(project, member, asset)

    # Stub out storage download and BackgroundTasks to avoid real I/O
    storage_stub = MagicMock(spec=LocalStorageService)
    storage_stub.download_file = AsyncMock()

    background_tasks = MagicMock()
    background_tasks.add_task = MagicMock()

    request = TranscribeRequest(asset_id=asset.id)

    with (
        patch("src.api.transcription.get_storage_service", return_value=storage_stub),
        patch("tempfile.NamedTemporaryFile"),
    ):
        response = await start_transcription(
            request=request,
            background_tasks=background_tasks,
            db=db,
            current_user=editor_user,
        )
    assert response.status == "processing"


@pytest.mark.asyncio
async def test_transcription_start_viewer_denied():
    """Viewer member cannot start transcription (write path → 403)."""
    from src.api.transcription import TranscribeRequest, start_transcription

    owner_id = uuid.uuid4()
    viewer_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, viewer_id, "viewer")
    asset = _make_asset(project.id, "video")

    viewer_user = MagicMock()
    viewer_user.id = viewer_id

    db = _make_db_with_asset(project, member, asset)

    background_tasks = MagicMock()
    request = TranscribeRequest(asset_id=asset.id)

    with pytest.raises(HTTPException) as exc_info:
        await start_transcription(
            request=request,
            background_tasks=background_tasks,
            db=db,
            current_user=viewer_user,
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_transcription_start_non_member_gets_404():
    """Non-member gets 404 when starting transcription."""
    from src.api.transcription import TranscribeRequest, start_transcription

    owner_id = uuid.uuid4()
    stranger_id = uuid.uuid4()
    project = _make_project(owner_id)
    asset = _make_asset(project.id, "video")

    stranger_user = MagicMock()
    stranger_user.id = stranger_id

    db = _make_db_with_asset(project, None, asset)

    background_tasks = MagicMock()
    request = TranscribeRequest(asset_id=asset.id)

    with pytest.raises(HTTPException) as exc_info:
        await start_transcription(
            request=request,
            background_tasks=background_tasks,
            db=db,
            current_user=stranger_user,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_transcription_start_owner_allowed():
    """Owner can start transcription (write path)."""
    from src.api.transcription import TranscribeRequest, start_transcription
    from src.services.storage_service import LocalStorageService

    owner_id = uuid.uuid4()
    project = _make_project(owner_id)
    asset = _make_asset(project.id, "video")

    owner_user = MagicMock()
    owner_user.id = owner_id

    db = _make_db_with_asset(project, None, asset)

    storage_stub = MagicMock(spec=LocalStorageService)
    storage_stub.download_file = AsyncMock()

    background_tasks = MagicMock()
    background_tasks.add_task = MagicMock()

    request = TranscribeRequest(asset_id=asset.id)

    with (
        patch("src.api.transcription.get_storage_service", return_value=storage_stub),
        patch("tempfile.NamedTemporaryFile"),
    ):
        response = await start_transcription(
            request=request,
            background_tasks=background_tasks,
            db=db,
            current_user=owner_user,
        )
    assert response.status == "processing"


# --- PUT /transcription/{asset_id}/segments/{segment_id} (write) ---


@pytest.mark.asyncio
async def test_transcription_update_segment_viewer_denied():
    """Viewer member cannot update segment cut flag (write path → 403)."""
    from src.api.transcription import UpdateSegmentRequest, update_segment
    from src.schemas.timeline import Transcription, TranscriptionSegment

    owner_id = uuid.uuid4()
    viewer_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, viewer_id, "viewer")
    asset = _make_asset(project.id)
    segment = TranscriptionSegment(id="seg-1", start_ms=0, end_ms=1000, text="テスト")
    transcription_data = Transcription(
        asset_id=asset.id, language="ja", status="completed", segments=[segment]
    )
    asset.asset_metadata = {
        "transcription": transcription_data.model_dump(mode="json")
    }

    viewer_user = MagicMock()
    viewer_user.id = viewer_id

    db = _make_db_with_asset(project, member, asset)

    with pytest.raises(HTTPException) as exc_info:
        await update_segment(
            asset_id=asset.id,
            segment_id="seg-1",
            request=UpdateSegmentRequest(cut=True, cut_reason="silence"),
            db=db,
            current_user=viewer_user,
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_transcription_update_segment_editor_allowed():
    """Editor member can update segment cut flag."""
    from src.api.transcription import UpdateSegmentRequest, update_segment
    from src.schemas.timeline import Transcription, TranscriptionSegment

    owner_id = uuid.uuid4()
    editor_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, editor_id, "editor")
    asset = _make_asset(project.id)
    segment = TranscriptionSegment(id="seg-1", start_ms=0, end_ms=1000, text="テスト")
    transcription_data = Transcription(
        asset_id=asset.id, language="ja", status="completed", segments=[segment]
    )
    asset.asset_metadata = {
        "transcription": transcription_data.model_dump(mode="json")
    }

    editor_user = MagicMock()
    editor_user.id = editor_id

    db = _make_db_with_asset(project, member, asset)

    result = await update_segment(
        asset_id=asset.id,
        segment_id="seg-1",
        request=UpdateSegmentRequest(cut=True, cut_reason="silence"),
        db=db,
        current_user=editor_user,
    )
    assert result["status"] == "updated"


@pytest.mark.asyncio
async def test_transcription_update_segment_owner_allowed():
    """Owner can update segment cut flag (write path)."""
    from src.api.transcription import UpdateSegmentRequest, update_segment
    from src.schemas.timeline import Transcription, TranscriptionSegment

    owner_id = uuid.uuid4()
    project = _make_project(owner_id)
    asset = _make_asset(project.id)
    segment = TranscriptionSegment(id="seg-1", start_ms=0, end_ms=1000, text="テスト")
    transcription_data = Transcription(
        asset_id=asset.id, language="ja", status="completed", segments=[segment]
    )
    asset.asset_metadata = {
        "transcription": transcription_data.model_dump(mode="json")
    }

    owner_user = MagicMock()
    owner_user.id = owner_id

    db = _make_db_with_asset(project, None, asset)

    result = await update_segment(
        asset_id=asset.id,
        segment_id="seg-1",
        request=UpdateSegmentRequest(cut=True, cut_reason="manual"),
        db=db,
        current_user=owner_user,
    )
    assert result["status"] == "updated"


@pytest.mark.asyncio
async def test_transcription_update_segment_non_member_gets_404():
    """Non-member gets 404 (not 403) when updating segment cut flag."""
    from src.api.transcription import UpdateSegmentRequest, update_segment

    owner_id = uuid.uuid4()
    stranger_id = uuid.uuid4()
    project = _make_project(owner_id)
    asset = _make_asset(project.id)

    stranger_user = MagicMock()
    stranger_user.id = stranger_id

    db = _make_db_with_asset(project, None, asset)  # no member record

    with pytest.raises(HTTPException) as exc_info:
        await update_segment(
            asset_id=asset.id,
            segment_id="seg-1",
            request=UpdateSegmentRequest(cut=True, cut_reason="manual"),
            db=db,
            current_user=stranger_user,
        )
    assert exc_info.value.status_code == 404


# --- POST /transcription/{asset_id}/apply-cuts (write) ---


@pytest.mark.asyncio
async def test_transcription_apply_cuts_viewer_denied():
    """Viewer member cannot apply cuts (write path → 403)."""
    from src.api.transcription import apply_cuts_to_timeline
    from src.schemas.timeline import Transcription, TranscriptionSegment

    owner_id = uuid.uuid4()
    viewer_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, viewer_id, "viewer")
    asset = _make_asset(project.id)
    segment = TranscriptionSegment(id="seg-1", start_ms=0, end_ms=1000, text="テスト")
    transcription_data = Transcription(
        asset_id=asset.id, language="ja", status="completed", segments=[segment]
    )
    asset.asset_metadata = {
        "transcription": transcription_data.model_dump(mode="json")
    }

    viewer_user = MagicMock()
    viewer_user.id = viewer_id

    db = _make_db_with_asset(project, member, asset)

    with pytest.raises(HTTPException) as exc_info:
        await apply_cuts_to_timeline(
            asset_id=asset.id,
            db=db,
            current_user=viewer_user,
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_transcription_apply_cuts_editor_allowed():
    """Editor member can apply cuts to timeline."""
    from src.api.transcription import apply_cuts_to_timeline
    from src.schemas.timeline import Transcription, TranscriptionSegment

    owner_id = uuid.uuid4()
    editor_id = uuid.uuid4()
    project = _make_project(owner_id)
    member = _make_member(project.id, editor_id, "editor")
    asset = _make_asset(project.id)
    segment = TranscriptionSegment(id="seg-1", start_ms=0, end_ms=1000, text="テスト")
    transcription_data = Transcription(
        asset_id=asset.id, language="ja", status="completed", segments=[segment]
    )
    asset.asset_metadata = {
        "transcription": transcription_data.model_dump(mode="json")
    }

    editor_user = MagicMock()
    editor_user.id = editor_id

    db = _make_db_with_asset(project, member, asset)

    result = await apply_cuts_to_timeline(
        asset_id=asset.id,
        db=db,
        current_user=editor_user,
    )
    assert result.clips_created == 1


@pytest.mark.asyncio
async def test_transcription_apply_cuts_owner_allowed():
    """Owner can apply cuts to timeline (write path)."""
    from src.api.transcription import apply_cuts_to_timeline
    from src.schemas.timeline import Transcription, TranscriptionSegment

    owner_id = uuid.uuid4()
    project = _make_project(owner_id)
    asset = _make_asset(project.id)
    segment = TranscriptionSegment(id="seg-1", start_ms=0, end_ms=1000, text="テスト")
    transcription_data = Transcription(
        asset_id=asset.id, language="ja", status="completed", segments=[segment]
    )
    asset.asset_metadata = {
        "transcription": transcription_data.model_dump(mode="json")
    }

    owner_user = MagicMock()
    owner_user.id = owner_id

    db = _make_db_with_asset(project, None, asset)

    result = await apply_cuts_to_timeline(
        asset_id=asset.id,
        db=db,
        current_user=owner_user,
    )
    assert result.clips_created == 1


@pytest.mark.asyncio
async def test_transcription_apply_cuts_non_member_gets_404():
    """Non-member gets 404 (not 403) when applying cuts."""
    from src.api.transcription import apply_cuts_to_timeline

    owner_id = uuid.uuid4()
    stranger_id = uuid.uuid4()
    project = _make_project(owner_id)
    asset = _make_asset(project.id)

    stranger_user = MagicMock()
    stranger_user.id = stranger_id

    db = _make_db_with_asset(project, None, asset)  # no member record

    with pytest.raises(HTTPException) as exc_info:
        await apply_cuts_to_timeline(
            asset_id=asset.id,
            db=db,
            current_user=stranger_user,
        )
    assert exc_info.value.status_code == 404
