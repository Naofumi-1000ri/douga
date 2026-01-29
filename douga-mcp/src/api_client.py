"""HTTP client for douga backend API."""

import httpx

from src.config import API_BASE_URL, API_KEY, DEV_TOKEN, REQUEST_TIMEOUT, UPLOAD_TIMEOUT


def _auth_headers() -> dict[str, str]:
    """Build authentication headers."""
    if API_KEY:
        return {"X-API-Key": API_KEY}
    # Dev mode fallback
    return {"Authorization": f"Bearer {DEV_TOKEN}"}


def _client(timeout: float = REQUEST_TIMEOUT) -> httpx.AsyncClient:
    """Create an async HTTP client with auth headers."""
    return httpx.AsyncClient(
        base_url=API_BASE_URL,
        headers=_auth_headers(),
        timeout=timeout,
    )


async def create_project(name: str, description: str = "", width: int = 1920, height: int = 1080) -> dict:
    """Create a new project."""
    async with _client() as client:
        resp = await client.post(
            "/api/projects",
            json={"name": name, "description": description, "width": width, "height": height},
        )
        resp.raise_for_status()
        return resp.json()


async def get_project(project_id: str) -> dict:
    """Get project details."""
    async with _client() as client:
        resp = await client.get(f"/api/projects/{project_id}")
        resp.raise_for_status()
        return resp.json()


async def batch_upload_assets(project_id: str, file_paths: list[str]) -> dict:
    """Upload multiple files to a project via batch upload.

    Args:
        project_id: Project UUID
        file_paths: List of local file paths to upload
    """
    import mimetypes
    from pathlib import Path

    files = []
    for path_str in file_paths:
        p = Path(path_str)
        mime_type = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
        files.append(("files", (p.name, open(str(p), "rb"), mime_type)))

    try:
        async with _client(timeout=UPLOAD_TIMEOUT) as client:
            resp = await client.post(
                f"/api/ai-video/projects/{project_id}/assets/batch-upload",
                files=files,
            )
            resp.raise_for_status()
            return resp.json()
    finally:
        for _, (_, f, _) in files:
            f.close()


async def reclassify_asset(project_id: str, asset_id: str, type: str, subtype: str) -> dict:
    """Reclassify an asset."""
    async with _client() as client:
        resp = await client.put(
            f"/api/ai-video/projects/{project_id}/assets/{asset_id}/reclassify",
            json={"type": type, "subtype": subtype},
        )
        resp.raise_for_status()
        return resp.json()


async def get_asset_catalog(project_id: str) -> dict:
    """Get the AI-oriented asset catalog."""
    async with _client() as client:
        resp = await client.get(
            f"/api/ai-video/projects/{project_id}/asset-catalog",
        )
        resp.raise_for_status()
        return resp.json()


async def generate_plan(project_id: str, brief: dict) -> dict:
    """Generate a video plan from a brief."""
    async with _client(timeout=UPLOAD_TIMEOUT) as client:
        resp = await client.post(
            f"/api/ai-video/projects/{project_id}/plan/generate",
            json={"brief": brief},
        )
        resp.raise_for_status()
        return resp.json()


async def get_plan(project_id: str) -> dict:
    """Get the current video plan."""
    async with _client() as client:
        resp = await client.get(f"/api/ai-video/projects/{project_id}/plan")
        resp.raise_for_status()
        return resp.json()


async def update_plan(project_id: str, plan: dict) -> dict:
    """Update the video plan."""
    async with _client() as client:
        resp = await client.put(
            f"/api/ai-video/projects/{project_id}/plan",
            json={"plan": plan},
        )
        resp.raise_for_status()
        return resp.json()


async def apply_plan(project_id: str) -> dict:
    """Apply the video plan to generate timeline."""
    async with _client() as client:
        resp = await client.post(
            f"/api/ai-video/projects/{project_id}/plan/apply",
        )
        resp.raise_for_status()
        return resp.json()


async def render_video(project_id: str) -> dict:
    """Start video rendering."""
    async with _client() as client:
        resp = await client.post(f"/api/projects/{project_id}/render")
        resp.raise_for_status()
        return resp.json()


async def get_render_status(project_id: str) -> dict:
    """Get render job status."""
    async with _client() as client:
        resp = await client.get(f"/api/projects/{project_id}/render/status")
        resp.raise_for_status()
        return resp.json()


async def update_timeline(project_id: str, timeline_data: dict) -> dict:
    """Update project timeline directly."""
    async with _client() as client:
        resp = await client.put(
            f"/api/projects/{project_id}/timeline",
            json=timeline_data,
        )
        resp.raise_for_status()
        return resp.json()


async def list_assets(project_id: str) -> list[dict]:
    """List all assets for a project."""
    async with _client() as client:
        resp = await client.get(f"/api/projects/{project_id}/assets")
        resp.raise_for_status()
        return resp.json()


# =============================================================================
# Preview / Inspection API
# =============================================================================


async def get_event_points(
    project_id: str,
    include_audio: bool = True,
    include_visual: bool = True,
    min_gap_ms: int = 500,
) -> dict:
    """Detect event points in the timeline."""
    async with _client() as client:
        resp = await client.post(
            f"/api/projects/{project_id}/preview/event-points",
            json={
                "include_audio": include_audio,
                "include_visual": include_visual,
                "min_gap_ms": min_gap_ms,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def sample_frame(
    project_id: str,
    time_ms: int,
    resolution: str = "640x360",
) -> dict:
    """Render a single preview frame."""
    async with _client(timeout=UPLOAD_TIMEOUT) as client:
        resp = await client.post(
            f"/api/projects/{project_id}/preview/sample-frame",
            json={"time_ms": time_ms, "resolution": resolution},
        )
        resp.raise_for_status()
        return resp.json()


async def sample_event_points(
    project_id: str,
    max_samples: int = 10,
    resolution: str = "640x360",
    include_audio: bool = True,
    min_gap_ms: int = 500,
) -> dict:
    """Auto-detect event points and sample frames."""
    async with _client(timeout=UPLOAD_TIMEOUT) as client:
        resp = await client.post(
            f"/api/projects/{project_id}/preview/sample-event-points",
            json={
                "max_samples": max_samples,
                "resolution": resolution,
                "include_audio": include_audio,
                "min_gap_ms": min_gap_ms,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def validate_composition(
    project_id: str,
    rules: list[str] | None = None,
) -> dict:
    """Validate timeline composition rules."""
    async with _client() as client:
        resp = await client.post(
            f"/api/projects/{project_id}/preview/validate",
            json={"rules": rules},
        )
        resp.raise_for_status()
        return resp.json()
