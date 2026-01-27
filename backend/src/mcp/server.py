"""MCP Server for Douga Video Editor.

FastMCP-based server that provides AI tools for video editing.

Run as standalone:
    python -m src.mcp.server

Or run with mcp CLI:
    mcp run src.mcp.server:mcp_server

Requirements:
    pip install mcp[cli] httpx
"""

import logging
import os
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    # MCP not installed - provide a stub for import compatibility
    class FastMCP:
        """Stub FastMCP class for when mcp package is not installed."""

        def __init__(self, name: str = "", instructions: str = ""):
            self.name = name
            self.instructions = instructions
            self.app = None

        def tool(self):
            """Decorator that does nothing when mcp is not installed."""
            def decorator(func):
                return func
            return decorator

    import warnings
    warnings.warn(
        "MCP package not installed. Install with: pip install mcp[cli] httpx",
        ImportWarning,
    )

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server instance
mcp_server = FastMCP(
    name="Douga Video Editor",
    instructions="AI tools for video editing with hierarchical data access (L1→L2→L3)",
)

# Backend API configuration
API_BASE_URL = os.environ.get("DOUGA_API_URL", "http://localhost:8000")

# API authentication: prefer API key, fall back to token
# DOUGA_API_KEY: Long-lived API key (recommended for MCP)
# DOUGA_API_TOKEN: Firebase token or dev-token (legacy)
API_KEY = os.environ.get("DOUGA_API_KEY")
API_TOKEN = os.environ.get("DOUGA_API_TOKEN", "dev-token")


# =============================================================================
# Helper: API Client
# =============================================================================


async def _call_api(
    method: str, endpoint: str, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Call the Douga backend API.

    Args:
        method: HTTP method (GET, POST, PATCH, DELETE)
        endpoint: API endpoint path (e.g., /api/ai/project/{id}/overview)
        data: Request body for POST/PATCH

    Returns:
        JSON response from API
    """
    import httpx

    url = f"{API_BASE_URL}{endpoint}"

    # Use API key if available, otherwise fall back to token
    if API_KEY:
        headers = {"X-API-Key": API_KEY}
    else:
        headers = {"Authorization": f"Bearer {API_TOKEN}"}

    async with httpx.AsyncClient() as client:
        if method == "GET":
            response = await client.get(url, headers=headers)
        elif method == "POST":
            response = await client.post(url, headers=headers, json=data)
        elif method == "PATCH":
            response = await client.patch(url, headers=headers, json=data)
        elif method == "DELETE":
            response = await client.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")

        response.raise_for_status()
        return response.json() if response.content else {}


# =============================================================================
# L1: Summary Level Tools
# =============================================================================


@mcp_server.tool()
async def get_project_overview(project_id: str) -> str:
    """Get L1 project overview (~300 tokens).

    Start here to understand the project scope.

    Returns:
        Project metadata, layer/track counts, asset counts, last modified time.
    """
    result = await _call_api("GET", f"/api/ai/project/{project_id}/overview")
    return _format_response(result)


# =============================================================================
# L2: Structure Level Tools
# =============================================================================


@mcp_server.tool()
async def get_timeline_structure(project_id: str) -> str:
    """Get L2 timeline structure (~800 tokens).

    Shows layer and track organization with time coverage.
    Use this to find which layer/track to work with.

    Returns:
        Layers (id, name, type, clip_count, time_coverage, visible, locked)
        Audio tracks (id, name, type, clip_count, time_coverage, volume, muted)
    """
    result = await _call_api("GET", f"/api/ai/project/{project_id}/structure")
    return _format_response(result)


@mcp_server.tool()
async def get_timeline_at_time(project_id: str, time_ms: int) -> str:
    """Get L2 timeline state at a specific time.

    Shows what clips are active at the given timestamp.

    Args:
        project_id: Project UUID
        time_ms: Timestamp in milliseconds

    Returns:
        Active clips at the specified time, next event time.
    """
    result = await _call_api("GET", f"/api/ai/project/{project_id}/at-time/{time_ms}")
    return _format_response(result)


@mcp_server.tool()
async def get_asset_catalog(project_id: str) -> str:
    """Get L2 asset catalog.

    Lists available assets with usage counts.
    Use to find asset IDs for adding new clips.

    Returns:
        Assets (id, name, type, subtype, duration_ms, dimensions, usage_count)
    """
    result = await _call_api("GET", f"/api/ai/project/{project_id}/assets")
    return _format_response(result)


# =============================================================================
# L3: Details Level Tools
# =============================================================================


@mcp_server.tool()
async def get_clip_details(project_id: str, clip_id: str) -> str:
    """Get L3 video clip details (~400 tokens).

    Full clip properties with neighboring context.

    Args:
        project_id: Project UUID
        clip_id: Clip UUID

    Returns:
        Clip timing, transform, effects, transitions, text content,
        previous/next clip info with gap.
    """
    result = await _call_api("GET", f"/api/ai/project/{project_id}/clip/{clip_id}")
    return _format_response(result)


@mcp_server.tool()
async def get_audio_clip_details(project_id: str, clip_id: str) -> str:
    """Get L3 audio clip details.

    Full audio clip properties with neighboring context.

    Args:
        project_id: Project UUID
        clip_id: Audio clip UUID

    Returns:
        Clip timing, volume, fades, previous/next clip info with gap.
    """
    result = await _call_api("GET", f"/api/ai/project/{project_id}/audio-clip/{clip_id}")
    return _format_response(result)


# =============================================================================
# Write Tools: Layers
# =============================================================================


@mcp_server.tool()
async def add_layer(
    project_id: str,
    name: str,
    layer_type: str = "content",
    insert_at: int | None = None,
) -> str:
    """Create a new layer.

    Args:
        project_id: Project UUID
        name: Layer name
        layer_type: Layer type (background, content, avatar, effects, text)
        insert_at: Insert position (0=top, None=bottom)

    Returns:
        Created layer summary
    """
    data = {"name": name, "type": layer_type}
    if insert_at is not None:
        data["insert_at"] = insert_at

    result = await _call_api("POST", f"/api/ai/project/{project_id}/layers", data)
    return _format_response(result)


@mcp_server.tool()
async def reorder_layers(project_id: str, layer_ids: list[str]) -> str:
    """Reorder layers by providing the new order of layer IDs.

    Args:
        project_id: Project UUID
        layer_ids: Layer IDs in new order (top to bottom)

    Returns:
        Updated layer summaries in new order
    """
    data = {"layer_ids": layer_ids}
    result = await _call_api("PUT", f"/api/ai/project/{project_id}/layers/order", data)
    return _format_response(result)


@mcp_server.tool()
async def update_layer(
    project_id: str,
    layer_id: str,
    name: str | None = None,
    visible: bool | None = None,
    locked: bool | None = None,
) -> str:
    """Update layer properties (name, visibility, locked status).

    Args:
        project_id: Project UUID
        layer_id: Layer ID (from L2 structure)
        name: New layer name
        visible: Layer visibility
        locked: Lock layer from editing

    Returns:
        Updated layer summary
    """
    data = {}
    if name is not None:
        data["name"] = name
    if visible is not None:
        data["visible"] = visible
    if locked is not None:
        data["locked"] = locked

    result = await _call_api(
        "PATCH", f"/api/ai/project/{project_id}/layer/{layer_id}", data
    )
    return _format_response(result)


# =============================================================================
# Write Tools: Video Clips
# =============================================================================


@mcp_server.tool()
async def add_clip(
    project_id: str,
    layer_id: str,
    start_ms: int,
    duration_ms: int,
    asset_id: str | None = None,
    x: float | None = None,
    y: float | None = None,
    scale: float | None = None,
    text_content: str | None = None,
) -> str:
    """Add a new video clip to a layer.

    Args:
        project_id: Project UUID
        layer_id: Target layer ID (from L2 structure)
        start_ms: Timeline position in milliseconds (>= 0)
        duration_ms: Clip duration in milliseconds (> 0, max 1 hour)
        asset_id: Asset UUID (optional for text clips)
        x: X position (-3840 to 3840)
        y: Y position (-2160 to 2160)
        scale: Scale factor (0.01 to 10.0)
        text_content: Text content for text clips

    Returns:
        Created clip details (L3)
    """
    data = {
        "layer_id": layer_id,
        "start_ms": start_ms,
        "duration_ms": duration_ms,
    }
    if asset_id:
        data["asset_id"] = asset_id
    if x is not None:
        data["x"] = x
    if y is not None:
        data["y"] = y
    if scale is not None:
        data["scale"] = scale
    if text_content is not None:
        data["text_content"] = text_content

    result = await _call_api("POST", f"/api/ai/project/{project_id}/clips", data)
    return _format_response(result)


@mcp_server.tool()
async def move_clip(
    project_id: str,
    clip_id: str,
    new_start_ms: int,
    new_layer_id: str | None = None,
) -> str:
    """Move a video clip to a new position or layer.

    Args:
        project_id: Project UUID
        clip_id: Clip to move
        new_start_ms: New timeline position in milliseconds
        new_layer_id: Target layer ID (if changing layers)

    Returns:
        Updated clip details (L3)
    """
    data = {"new_start_ms": new_start_ms}
    if new_layer_id:
        data["new_layer_id"] = new_layer_id

    result = await _call_api(
        "PATCH", f"/api/ai/project/{project_id}/clip/{clip_id}/move", data
    )
    return _format_response(result)


@mcp_server.tool()
async def update_clip_transform(
    project_id: str,
    clip_id: str,
    x: float | None = None,
    y: float | None = None,
    scale: float | None = None,
    rotation: float | None = None,
) -> str:
    """Update clip transform (position, scale, rotation).

    Args:
        project_id: Project UUID
        clip_id: Clip to update
        x: X position (-3840 to 3840)
        y: Y position (-2160 to 2160)
        scale: Scale factor (0.01 to 10.0)
        rotation: Rotation in degrees (-360 to 360)

    Returns:
        Updated clip details (L3)
    """
    data = {}
    if x is not None:
        data["x"] = x
    if y is not None:
        data["y"] = y
    if scale is not None:
        data["scale"] = scale
    if rotation is not None:
        data["rotation"] = rotation

    result = await _call_api(
        "PATCH", f"/api/ai/project/{project_id}/clip/{clip_id}/transform", data
    )
    return _format_response(result)


@mcp_server.tool()
async def update_clip_effects(
    project_id: str,
    clip_id: str,
    opacity: float | None = None,
    chroma_key_enabled: bool | None = None,
    chroma_key_color: str | None = None,
) -> str:
    """Update clip effects (opacity, chroma key).

    Args:
        project_id: Project UUID
        clip_id: Clip to update
        opacity: Opacity (0.0 to 1.0)
        chroma_key_enabled: Enable green screen removal
        chroma_key_color: Key color in hex (e.g., "#00FF00")

    Returns:
        Updated clip details (L3)
    """
    data = {}
    if opacity is not None:
        data["opacity"] = opacity
    if chroma_key_enabled is not None:
        data["chroma_key_enabled"] = chroma_key_enabled
    if chroma_key_color is not None:
        data["chroma_key_color"] = chroma_key_color

    result = await _call_api(
        "PATCH", f"/api/ai/project/{project_id}/clip/{clip_id}/effects", data
    )
    return _format_response(result)


@mcp_server.tool()
async def delete_clip(project_id: str, clip_id: str) -> str:
    """Delete a video clip.

    Args:
        project_id: Project UUID
        clip_id: Clip to delete

    Returns:
        Success confirmation
    """
    await _call_api("DELETE", f"/api/ai/project/{project_id}/clip/{clip_id}")
    return "Clip deleted successfully"


# =============================================================================
# Write Tools: Audio Clips
# =============================================================================


@mcp_server.tool()
async def add_audio_clip(
    project_id: str,
    track_id: str,
    asset_id: str,
    start_ms: int,
    duration_ms: int,
    volume: float = 1.0,
    fade_in_ms: int = 0,
    fade_out_ms: int = 0,
) -> str:
    """Add a new audio clip to a track.

    Args:
        project_id: Project UUID
        track_id: Target track ID (from L2 structure)
        asset_id: Audio asset UUID
        start_ms: Timeline position in milliseconds
        duration_ms: Clip duration in milliseconds
        volume: Volume level (0.0 to 2.0)
        fade_in_ms: Fade in duration
        fade_out_ms: Fade out duration

    Returns:
        Created audio clip details (L3)
    """
    data = {
        "track_id": track_id,
        "asset_id": asset_id,
        "start_ms": start_ms,
        "duration_ms": duration_ms,
        "volume": volume,
        "fade_in_ms": fade_in_ms,
        "fade_out_ms": fade_out_ms,
    }

    result = await _call_api("POST", f"/api/ai/project/{project_id}/audio-clips", data)
    return _format_response(result)


@mcp_server.tool()
async def move_audio_clip(
    project_id: str,
    clip_id: str,
    new_start_ms: int,
    new_track_id: str | None = None,
) -> str:
    """Move an audio clip to a new position or track.

    Args:
        project_id: Project UUID
        clip_id: Audio clip to move
        new_start_ms: New timeline position
        new_track_id: Target track ID (if changing tracks)

    Returns:
        Updated audio clip details (L3)
    """
    data = {"new_start_ms": new_start_ms}
    if new_track_id:
        data["new_track_id"] = new_track_id

    result = await _call_api(
        "PATCH", f"/api/ai/project/{project_id}/audio-clip/{clip_id}/move", data
    )
    return _format_response(result)


@mcp_server.tool()
async def delete_audio_clip(project_id: str, clip_id: str) -> str:
    """Delete an audio clip.

    Args:
        project_id: Project UUID
        clip_id: Audio clip to delete

    Returns:
        Success confirmation
    """
    await _call_api("DELETE", f"/api/ai/project/{project_id}/audio-clip/{clip_id}")
    return "Audio clip deleted successfully"


# =============================================================================
# Semantic Operations
# =============================================================================


@mcp_server.tool()
async def snap_to_previous(project_id: str, target_clip_id: str) -> str:
    """Snap a clip to the end of the previous clip (close the gap).

    Args:
        project_id: Project UUID
        target_clip_id: Clip to snap

    Returns:
        Operation result with changes made
    """
    data = {
        "operation": "snap_to_previous",
        "target_clip_id": target_clip_id,
    }
    result = await _call_api("POST", f"/api/ai/project/{project_id}/semantic", data)
    return _format_response(result)


@mcp_server.tool()
async def snap_to_next(project_id: str, target_clip_id: str) -> str:
    """Snap the next clip to the end of this clip.

    Args:
        project_id: Project UUID
        target_clip_id: Reference clip

    Returns:
        Operation result with changes made
    """
    data = {
        "operation": "snap_to_next",
        "target_clip_id": target_clip_id,
    }
    result = await _call_api("POST", f"/api/ai/project/{project_id}/semantic", data)
    return _format_response(result)


@mcp_server.tool()
async def close_gap(project_id: str, target_layer_id: str) -> str:
    """Close all gaps in a layer by shifting clips forward.

    Args:
        project_id: Project UUID
        target_layer_id: Layer to process

    Returns:
        Operation result with changes made
    """
    data = {
        "operation": "close_gap",
        "target_layer_id": target_layer_id,
    }
    result = await _call_api("POST", f"/api/ai/project/{project_id}/semantic", data)
    return _format_response(result)


@mcp_server.tool()
async def auto_duck_bgm(
    project_id: str,
    duck_to: float = 0.1,
    attack_ms: int = 200,
    release_ms: int = 500,
) -> str:
    """Enable automatic BGM volume reduction when narration plays.

    Args:
        project_id: Project UUID
        duck_to: Volume during narration (0.0 to 1.0)
        attack_ms: Fade down duration
        release_ms: Fade up duration

    Returns:
        Operation result with changes made
    """
    data = {
        "operation": "auto_duck_bgm",
        "parameters": {
            "duck_to": duck_to,
            "attack_ms": attack_ms,
            "release_ms": release_ms,
        },
    }
    result = await _call_api("POST", f"/api/ai/project/{project_id}/semantic", data)
    return _format_response(result)


@mcp_server.tool()
async def rename_layer(
    project_id: str,
    layer_id: str,
    new_name: str,
) -> str:
    """Rename a layer (change its display name).

    Use update_layer for general property changes (name, visibility, locked).
    This is a convenience tool specifically for renaming.

    Args:
        project_id: Project UUID
        layer_id: Layer ID (from L2 structure)
        new_name: New layer name

    Returns:
        Operation result with changes made
    """
    data = {
        "operation": "rename_layer",
        "target_layer_id": layer_id,
        "parameters": {
            "name": new_name,
        },
    }
    result = await _call_api("POST", f"/api/ai/project/{project_id}/semantic", data)
    return _format_response(result)


# =============================================================================
# Analysis Tools
# =============================================================================


@mcp_server.tool()
async def analyze_gaps(project_id: str) -> str:
    """Find gaps in the timeline.

    Args:
        project_id: Project UUID

    Returns:
        Total gaps, total gap duration, list of gaps with location and size
    """
    result = await _call_api("GET", f"/api/ai/project/{project_id}/analysis/gaps")
    return _format_response(result)


@mcp_server.tool()
async def analyze_pacing(project_id: str, segment_duration_ms: int = 30000) -> str:
    """Analyze timeline pacing (clip density over time).

    Args:
        project_id: Project UUID
        segment_duration_ms: Duration of each analysis segment

    Returns:
        Overall average, per-segment analysis, improvement suggestions
    """
    result = await _call_api(
        "GET",
        f"/api/ai/project/{project_id}/analysis/pacing?segment_duration_ms={segment_duration_ms}",
    )
    return _format_response(result)


# =============================================================================
# Helper Functions
# =============================================================================


def _format_response(data: dict[str, Any]) -> str:
    """Format API response as readable text."""
    import json

    return json.dumps(data, indent=2, ensure_ascii=False)


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    # Run as standalone server
    logger.info(f"Starting Douga MCP Server (API: {API_BASE_URL})")
    uvicorn.run(mcp_server.app, host="0.0.0.0", port=6500)
