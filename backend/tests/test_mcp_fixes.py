"""Regression tests for MCP tool fixes (Issue #271).

Tests cover:
1. update_plan wraps payload in {"plan": {...}} before sending
2. validate_composition URL matches backend route
3. batch_upload_assets file handles are closed on error
4. update_effects is registered in SUPPORTED_ROLLBACK_OPERATIONS
5. MCP tool API routes match backend route definitions (smoke)
"""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.operation_service import SUPPORTED_ROLLBACK_OPERATIONS

# Absolute path to the worktree root (issue-271/)
_WORKTREE_ROOT = Path(__file__).resolve().parents[2]
_DOUGA_MCP_SRC = _WORKTREE_ROOT / "douga-mcp" / "src"
_BACKEND_SRC = _WORKTREE_ROOT / "backend" / "src"


# =============================================================================
# Fix 1: update_plan wraps plan in {"plan": {...}}
# =============================================================================


@pytest.mark.asyncio
async def test_update_plan_wraps_payload_in_plan_key():
    """server.py update_plan must send {"plan": plan} not bare plan object."""
    captured: dict = {}

    async def fake_call_api(method: str, endpoint: str, data=None):
        captured["method"] = method
        captured["endpoint"] = endpoint
        captured["data"] = data
        return {"plan": {}, "project_id": "test-id"}

    import src.mcp.server as mcp_server_mod

    original = mcp_server_mod._call_api
    mcp_server_mod._call_api = fake_call_api
    try:
        await mcp_server_mod.update_plan(
            project_id="proj-123",
            plan={"sections": [], "title": "My Plan"},
        )
    finally:
        mcp_server_mod._call_api = original

    assert "plan" in captured["data"], (
        "update_plan must wrap the plan in {'plan': ...} — "
        f"actual data sent: {captured['data']}"
    )
    assert captured["data"]["plan"] == {"sections": [], "title": "My Plan"}
    assert captured["method"] == "PUT"


# =============================================================================
# Fix 2: validate_composition URL matches backend route
# =============================================================================


def test_validate_composition_url_matches_backend_route():
    """douga-mcp validate_composition URL must match backend /api/projects/.../preview/validate."""
    api_client_path = _DOUGA_MCP_SRC / "api_client.py"
    source = api_client_path.read_text()

    assert "preview/validate" in source, (
        "api_client.py validate_composition must use '.../preview/validate' path"
    )
    assert "/preview/validate" in source, (
        "api_client.py validate_composition URL must contain '/preview/validate'"
    )


def test_backend_preview_validate_route_definition():
    """Backend preview.py must define the /projects/{project_id}/preview/validate route."""
    preview_path = _BACKEND_SRC / "api" / "preview.py"
    source = preview_path.read_text()

    assert '"/projects/{project_id}/preview/validate"' in source, (
        "backend preview.py must define POST /projects/{project_id}/preview/validate"
    )


# =============================================================================
# Fix 3: batch_upload_assets closes file handles even on error
# =============================================================================

def test_batch_upload_assets_uses_file_handles_list(tmp_path):
    """api_client.py batch_upload_assets must use a separate file_handles list for cleanup.

    This is a static analysis test: verifies the fix pattern is present in source.
    The old code used tuple unpacking `for _, (_, f, _) in files:` which is fragile.
    The fixed code collects file handles in a separate list for safe cleanup.
    """
    api_client_path = _DOUGA_MCP_SRC / "api_client.py"
    source = api_client_path.read_text()

    # Fixed pattern: separate file_handles list
    assert "file_handles" in source, (
        "api_client.py batch_upload_assets must use a 'file_handles' list "
        "to track open file descriptors for safe cleanup in finally block"
    )
    assert "file_handles.append" in source, (
        "file handles must be appended to file_handles list before use"
    )
    assert "for fh in file_handles:" in source, (
        "finally block must iterate over file_handles list to close each handle"
    )


def test_batch_upload_assets_old_tuple_unpack_pattern_removed(tmp_path):
    """The fragile tuple unpack pattern must not be present in api_client.py."""
    api_client_path = _DOUGA_MCP_SRC / "api_client.py"
    source = api_client_path.read_text()

    # Old broken pattern: for _, (_, f, _) in files:
    assert "for _, (_, f, _) in files:" not in source, (
        "Old tuple unpacking pattern 'for _, (_, f, _) in files:' must be removed; "
        "use 'for fh in file_handles:' instead"
    )


# =============================================================================
# Fix 4: update_effects registered in SUPPORTED_ROLLBACK_OPERATIONS
# =============================================================================


def test_update_effects_in_supported_rollback_operations():
    """update_effects must be registered in SUPPORTED_ROLLBACK_OPERATIONS."""
    assert "update_effects" in SUPPORTED_ROLLBACK_OPERATIONS, (
        "update_effects rollback implementation exists but was not registered; "
        "add 'update_effects' to SUPPORTED_ROLLBACK_OPERATIONS"
    )


def test_update_transform_still_in_supported_rollback_operations():
    """Existing update_transform rollback must still be registered."""
    assert "update_transform" in SUPPORTED_ROLLBACK_OPERATIONS


def test_rollback_operations_core_set_intact():
    """Core rollback operations must all remain registered."""
    expected = {
        "add_clip",
        "delete_clip",
        "move_clip",
        "update_transform",
        "update_effects",
        "add_layer",
        "add_audio_clip",
        "delete_audio_clip",
        "move_audio_clip",
    }
    missing = expected - SUPPORTED_ROLLBACK_OPERATIONS
    assert not missing, f"These rollback operations are missing: {missing}"


# =============================================================================
# Smoke: MCP tool API routes vs. backend route definitions
# =============================================================================


def _read_source(path: Path) -> str:
    return path.read_text()


def test_mcp_server_update_plan_uses_plan_wrapper():
    """backend/src/mcp/server.py update_plan must pass {'plan': plan} to _call_api."""
    server_path = _BACKEND_SRC / "mcp" / "server.py"
    source = _read_source(server_path)

    lines = source.splitlines()
    in_update_plan = False
    found_plan_wrapper = False
    for line in lines:
        if "async def update_plan" in line:
            in_update_plan = True
        if in_update_plan and '{"plan":' in line:
            found_plan_wrapper = True
            break
        if in_update_plan and "async def " in line and "update_plan" not in line:
            break
    assert found_plan_wrapper, (
        "server.py update_plan must pass {'plan': plan} to _call_api, not bare plan"
    )


def test_douga_mcp_api_client_preview_routes_match_backend():
    """douga-mcp preview route paths must match backend preview.py route paths."""
    api_client_path = _DOUGA_MCP_SRC / "api_client.py"
    backend_preview_path = _BACKEND_SRC / "api" / "preview.py"

    client_src = _read_source(api_client_path)
    preview_src = _read_source(backend_preview_path)

    # Routes defined in backend preview.py (without /api prefix)
    backend_routes = [
        "/projects/{project_id}/preview/event-points",
        "/projects/{project_id}/preview/sample-frame",
        "/projects/{project_id}/preview/sample-event-points",
        "/projects/{project_id}/preview/validate",
    ]

    # Corresponding URL fragments expected in api_client.py
    client_patterns = [
        "preview/event-points",
        "preview/sample-frame",
        "preview/sample-event-points",
        "preview/validate",
    ]

    for backend_route, client_pattern in zip(backend_routes, client_patterns):
        assert backend_route in preview_src, (
            f"Backend preview.py is missing route: {backend_route}"
        )
        assert client_pattern in client_src, (
            f"api_client.py is missing URL pattern: {client_pattern}"
        )
