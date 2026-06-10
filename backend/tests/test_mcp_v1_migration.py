"""Regression tests for MCP V1 API migration and consolidation (Issue #279).

Tests cover:
1. All read tools use V1 endpoints (/api/ai/v1/projects/...)
2. All write tools use V1 endpoints and pass Idempotency-Key
3. _call_api_v1_write auto-generates UUID Idempotency-Key
4. _call_api_v1_write unwraps V1 Envelope response {"data": ...}
5. douga-mcp deprecated (README exists and contains DEPRECATED notice)
6. Route consistency: server.py V1 routes match ai_v1.py route definitions
"""

import json
import re
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import src.mcp.server as mcp_server_mod

_WORKTREE_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_SRC = _WORKTREE_ROOT / "backend" / "src"
_DOUGA_MCP_DIR = _WORKTREE_ROOT / "douga-mcp"

# =============================================================================
# 1. Read tools use V1 endpoints
# =============================================================================

V1_READ_TOOL_ROUTES = [
    ("get_project_overview", "/api/ai/v1/projects/{project_id}/overview"),
    ("get_timeline_structure", "/api/ai/v1/projects/{project_id}/structure"),
    ("get_timeline_at_time", "/api/ai/v1/projects/{project_id}/at-time/{time_ms}"),
    ("get_asset_catalog", "/api/ai/v1/projects/{project_id}/assets"),
    ("get_clip_details", "/api/ai/v1/projects/{project_id}/clips/{clip_id}"),
    ("get_audio_clip_details", "/api/ai/v1/projects/{project_id}/audio-clips/{clip_id}"),
    ("analyze_gaps", "/api/ai/v1/projects/{project_id}/analysis/gaps"),
    ("analyze_pacing", "/api/ai/v1/projects/{project_id}/analysis/pacing"),
]


@pytest.mark.parametrize("tool_name,expected_path_template", V1_READ_TOOL_ROUTES)
def test_read_tools_use_v1_endpoints(tool_name: str, expected_path_template: str) -> None:
    """Read tools in server.py must call V1 endpoints (/api/ai/v1/...)."""
    server_path = _BACKEND_SRC / "mcp" / "server.py"
    source = server_path.read_text()

    # Extract the path fragment from the template (the part after /projects/...)
    # e.g. "/api/ai/v1/projects/{project_id}/overview" -> look for "v1/projects" and "overview"
    path_parts = expected_path_template.split("/")
    # Key identifiable fragment that must appear near the tool definition
    key_fragment = expected_path_template.split("{")[0].rstrip("/").split("/")[-1]
    # Check that v1/projects pattern is in source
    assert "/api/ai/v1/projects/" in source, (
        f"server.py must have V1 base path '/api/ai/v1/projects/' but it is missing"
    )

    # Find the function definition and verify V1 path is used nearby
    lines = source.splitlines()
    in_func = False
    found_v1 = False
    for line in lines:
        if f"async def {tool_name}" in line:
            in_func = True
        if in_func and "/api/ai/v1/projects/" in line:
            found_v1 = True
            break
        # Stop searching when we hit the next function
        if in_func and "async def " in line and tool_name not in line:
            break
    assert found_v1, (
        f"{tool_name} in server.py must use a V1 endpoint '/api/ai/v1/projects/...', "
        f"but no V1 path found in the function body"
    )


def test_no_old_api_endpoints_in_read_tools() -> None:
    """server.py must not call old /api/ai/project/... endpoints."""
    server_path = _BACKEND_SRC / "mcp" / "server.py"
    source = server_path.read_text()

    # Old pattern: /api/ai/project/{id}/... (singular "project" without "v1")
    old_pattern = re.compile(r"/api/ai/project/")
    assert not old_pattern.search(source), (
        "server.py still contains old API endpoint pattern '/api/ai/project/' — "
        "all endpoints must be migrated to '/api/ai/v1/projects/'"
    )


# =============================================================================
# 2. Write tools use _call_api_v1_write (V1 endpoint)
# =============================================================================

V1_WRITE_TOOL_ROUTES = [
    # Layers
    ("add_layer", "/api/ai/v1/projects/{project_id}/layers"),
    ("reorder_layers", "/api/ai/v1/projects/{project_id}/layers/order"),
    ("update_layer", "/api/ai/v1/projects/{project_id}/layers/{layer_id}"),
    # Video Clips
    ("add_clip", "/api/ai/v1/projects/{project_id}/clips"),
    ("move_clip", "/api/ai/v1/projects/{project_id}/clips/{clip_id}/move"),
    ("update_clip_transform", "/api/ai/v1/projects/{project_id}/clips/{clip_id}/transform"),
    ("update_clip_effects", "/api/ai/v1/projects/{project_id}/clips/{clip_id}/effects"),
    ("delete_clip", "/api/ai/v1/projects/{project_id}/clips/{clip_id}"),
    # Audio Clips
    ("add_audio_clip", "/api/ai/v1/projects/{project_id}/audio-clips"),
    ("move_audio_clip", "/api/ai/v1/projects/{project_id}/audio-clips/{clip_id}/move"),
    ("delete_audio_clip", "/api/ai/v1/projects/{project_id}/audio-clips/{clip_id}"),
    # Semantic
    ("snap_to_previous", "/api/ai/v1/projects/{project_id}/semantic"),
    ("snap_to_next", "/api/ai/v1/projects/{project_id}/semantic"),
    ("close_gap", "/api/ai/v1/projects/{project_id}/semantic"),
    ("rename_layer", "/api/ai/v1/projects/{project_id}/semantic"),
]


@pytest.mark.parametrize("tool_name,expected_v1_path", V1_WRITE_TOOL_ROUTES)
def test_write_tools_use_v1_write_helper(tool_name: str, expected_v1_path: str) -> None:
    """Write tools in server.py must call _call_api_v1_write (not _call_api)."""
    server_path = _BACKEND_SRC / "mcp" / "server.py"
    source = server_path.read_text()

    lines = source.splitlines()
    in_func = False
    uses_v1_write = False
    for line in lines:
        if f"async def {tool_name}" in line:
            in_func = True
        if in_func and "_call_api_v1_write" in line:
            uses_v1_write = True
            break
        # Stop searching when we hit the next function
        if in_func and "async def " in line and tool_name not in line:
            break
    assert uses_v1_write, (
        f"Write tool '{tool_name}' must use '_call_api_v1_write' for V1 API calls, "
        f"but '_call_api_v1_write' was not found in the function body"
    )


# =============================================================================
# 3. _call_api_v1_write auto-generates Idempotency-Key
# =============================================================================


@pytest.mark.asyncio
async def test_v1_write_auto_generates_idempotency_key() -> None:
    """_call_api_v1_write must attach an Idempotency-Key header when not provided."""
    captured_headers: dict = {}

    fake_request = httpx.Request("POST", "http://localhost:8000/api/ai/v1/projects/test/clips")
    fake_response = httpx.Response(
        status_code=200,
        content=json.dumps({"data": {"id": "clip-1"}, "ok": True}).encode(),
        request=fake_request,
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def fake_post(url: str, headers: dict, json: dict | None = None) -> httpx.Response:
        captured_headers.update(headers)
        return fake_response

    mock_client.post = fake_post

    with patch("src.mcp.server.httpx.AsyncClient", return_value=mock_client):
        await mcp_server_mod._call_api_v1_write(
            "POST",
            "/api/ai/v1/projects/test/clips",
            {"layer_id": "layer-1", "start_ms": 0, "duration_ms": 5000},
        )

    assert "Idempotency-Key" in captured_headers, (
        "_call_api_v1_write must attach 'Idempotency-Key' header automatically"
    )
    # Should be a valid UUID4
    try:
        uuid.UUID(captured_headers["Idempotency-Key"], version=4)
    except ValueError:
        pytest.fail(
            f"Idempotency-Key must be a valid UUID4, got: {captured_headers['Idempotency-Key']}"
        )


@pytest.mark.asyncio
async def test_v1_write_uses_provided_idempotency_key() -> None:
    """_call_api_v1_write must use the provided Idempotency-Key when given."""
    custom_key = "custom-idempotency-key-12345"
    captured_headers: dict = {}

    fake_request = httpx.Request(
        "POST", "http://localhost:8000/api/ai/v1/projects/test/audio-clips"
    )
    fake_response = httpx.Response(
        status_code=200,
        content=json.dumps({"data": {"id": "audio-1"}, "ok": True}).encode(),
        request=fake_request,
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def fake_post(url: str, headers: dict, json: dict | None = None) -> httpx.Response:
        captured_headers.update(headers)
        return fake_response

    mock_client.post = fake_post

    with patch("src.mcp.server.httpx.AsyncClient", return_value=mock_client):
        await mcp_server_mod._call_api_v1_write(
            "POST",
            "/api/ai/v1/projects/test/audio-clips",
            {"track_id": "track-1", "asset_id": "asset-1", "start_ms": 0, "duration_ms": 5000},
            idempotency_key=custom_key,
        )

    assert captured_headers.get("Idempotency-Key") == custom_key, (
        f"Expected Idempotency-Key='{custom_key}', got: {captured_headers.get('Idempotency-Key')}"
    )


# =============================================================================
# 4. _call_api_v1_write unwraps V1 Envelope response
# =============================================================================


@pytest.mark.asyncio
async def test_v1_write_unwraps_envelope_response() -> None:
    """_call_api_v1_write must return the 'data' field from V1 Envelope response."""
    inner_data = {"id": "clip-abc", "start_ms": 1000, "duration_ms": 5000}
    envelope = {"data": inner_data, "meta": {"operation_id": "op-1"}, "ok": True}

    fake_request = httpx.Request("POST", "http://localhost:8000/api/ai/v1/projects/test/clips")
    fake_response = httpx.Response(
        status_code=200,
        content=json.dumps(envelope).encode(),
        request=fake_request,
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def fake_post(url: str, headers: dict, json: dict | None = None) -> httpx.Response:
        return fake_response

    mock_client.post = fake_post

    with patch("src.mcp.server.httpx.AsyncClient", return_value=mock_client):
        result = await mcp_server_mod._call_api_v1_write(
            "POST",
            "/api/ai/v1/projects/test/clips",
            {"layer_id": "layer-1", "start_ms": 1000, "duration_ms": 5000},
        )

    assert result == inner_data, (
        f"_call_api_v1_write must unwrap V1 Envelope 'data' field. "
        f"Expected: {inner_data}, got: {result}"
    )


@pytest.mark.asyncio
async def test_v1_write_returns_raw_when_no_envelope() -> None:
    """_call_api_v1_write must return raw response when no 'data' key (non-Envelope)."""
    raw_response = {"id": "layer-1", "name": "Layer 1"}

    fake_request = httpx.Request("DELETE", "http://localhost:8000/api/ai/v1/projects/test/clips/1")
    fake_response = httpx.Response(
        status_code=200,
        content=json.dumps(raw_response).encode(),
        request=fake_request,
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def fake_delete(url: str, headers: dict) -> httpx.Response:
        return fake_response

    mock_client.delete = fake_delete

    with patch("src.mcp.server.httpx.AsyncClient", return_value=mock_client):
        result = await mcp_server_mod._call_api_v1_write(
            "DELETE",
            "/api/ai/v1/projects/test/clips/clip-1",
        )

    assert result == raw_response


# =============================================================================
# 5. douga-mcp DEPRECATED notice exists
# =============================================================================


def test_douga_mcp_readme_contains_deprecated_notice() -> None:
    """douga-mcp/README.md must exist and contain DEPRECATED notice."""
    readme_path = _DOUGA_MCP_DIR / "README.md"
    assert readme_path.exists(), (
        f"douga-mcp/README.md must exist to announce deprecation. Path: {readme_path}"
    )
    content = readme_path.read_text()
    assert "DEPRECATED" in content or "廃止" in content, (
        "douga-mcp/README.md must contain 'DEPRECATED' or '廃止' deprecation notice"
    )
    # Should point to backend MCP
    assert "backend/src/mcp" in content or "backend" in content, (
        "douga-mcp/README.md must reference the migration target (backend/src/mcp)"
    )


def test_archive_douga_mcp_exists() -> None:
    """_archive/douga-mcp/ must exist as the archived copy."""
    archive_path = _WORKTREE_ROOT / "_archive" / "douga-mcp"
    assert archive_path.exists() and archive_path.is_dir(), (
        f"_archive/douga-mcp/ must exist as the archived copy of douga-mcp. "
        f"Expected path: {archive_path}"
    )


# =============================================================================
# 6. Route consistency: server.py V1 routes vs. ai_v1.py route definitions
# =============================================================================


V1_BACKEND_ROUTES = [
    # Read routes
    "/projects/{project_id}/overview",
    "/projects/{project_id}/structure",
    "/projects/{project_id}/assets",
    "/projects/{project_id}/at-time/{time_ms}",
    "/projects/{project_id}/clips/{clip_id}",
    "/projects/{project_id}/audio-clips/{clip_id}",
    "/projects/{project_id}/analysis/gaps",
    "/projects/{project_id}/analysis/pacing",
    # Write routes
    "/projects/{project_id}/clips",
    "/projects/{project_id}/clips/{clip_id}/move",
    "/projects/{project_id}/clips/{clip_id}/transform",
    "/projects/{project_id}/clips/{clip_id}/effects",
    "/projects/{project_id}/layers",
    "/projects/{project_id}/layers/{layer_id}",
    "/projects/{project_id}/layers/order",
    "/projects/{project_id}/audio-clips",
    "/projects/{project_id}/audio-clips/{clip_id}/move",
    "/projects/{project_id}/semantic",
]


@pytest.mark.parametrize("v1_route", V1_BACKEND_ROUTES)
def test_v1_routes_defined_in_ai_v1(v1_route: str) -> None:
    """Each V1 route used in server.py must be defined in backend ai_v1.py."""
    ai_v1_path = _BACKEND_SRC / "api" / "ai_v1.py"
    source = ai_v1_path.read_text()

    # Convert parameterized path to a searchable pattern
    # e.g. /projects/{project_id}/overview -> "overview" in a @router context
    # Use a more specific search: the path within quotes in the router decorator
    # The ai_v1.py uses paths without /api/ai/v1 prefix (router handles prefix)
    assert f'"{v1_route}"' in source, (
        f"Route '{v1_route}' must be defined in ai_v1.py as a @router decorator path, "
        f"but it was not found as a quoted string in the file"
    )
