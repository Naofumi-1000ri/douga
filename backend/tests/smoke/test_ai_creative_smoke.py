"""
Smoke test: AI creative instruction flow using local assets.

Run:
  DOUGA_SMOKE_AI=1 pytest tests/smoke/test_ai_creative_smoke.py -v

Env:
  DOUGA_API_URL      (default: http://localhost:8000)
  DOUGA_ASSETS_DIR   (default: /Users/hgs/devel/douga_root/assets)
  DOUGA_AI_PROVIDER  (optional: openai|gemini|anthropic)
  DOUGA_API_KEY      (optional: X-API-Key header)
"""

from __future__ import annotations

import base64
import mimetypes
import os
from pathlib import Path

import httpx
import pytest


ASSETS_DIR_DEFAULT = "/Users/hgs/devel/douga_root/assets"
PLACEHOLDER_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIW2P4"
    "//8/AwAI/AL+R5m5AAAAAElFTkSuQmCC"
)


def _auth_headers() -> dict[str, str]:
    api_key = os.getenv("DOUGA_API_KEY")
    if api_key:
        return {"X-API-Key": api_key}
    # dev-mode default
    return {"Authorization": "Bearer dev-token"}


def _create_project(client: httpx.Client, base_url: str) -> str:
    resp = client.post(
        f"{base_url}/api/projects",
        headers=_auth_headers(),
        json={"name": "Smoke: Product Intro", "description": "AI creative smoke"},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _upload_asset(
    client: httpx.Client,
    base_url: str,
    project_id: str,
    file_path: Path,
    asset_type: str,
    subtype: str,
) -> str:
    mime_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    upload_url_resp = client.post(
        f"{base_url}/api/projects/{project_id}/assets/upload-url",
        params={"filename": file_path.name, "content_type": mime_type},
        headers=_auth_headers(),
    )
    upload_url_resp.raise_for_status()
    upload = upload_url_resp.json()

    # Upload file bytes
    put_resp = client.put(
        upload["upload_url"],
        content=file_path.read_bytes(),
        headers={"Content-Type": mime_type},
    )
    put_resp.raise_for_status()

    # Register asset
    storage_url = upload["upload_url"]
    if "/api/storage/upload/" in storage_url:
        storage_url = storage_url.replace("/upload/", "/files/")
    else:
        storage_url = storage_url.split("?", 1)[0]

    register_resp = client.post(
        f"{base_url}/api/projects/{project_id}/assets",
        headers=_auth_headers(),
        json={
            "name": file_path.name,
            "type": asset_type,
            "subtype": subtype,
            "storage_key": upload["storage_key"],
            "storage_url": storage_url,
            "file_size": file_path.stat().st_size,
            "mime_type": mime_type,
        },
    )
    register_resp.raise_for_status()
    return register_resp.json()["id"]


def _ensure_placeholder_image(path: Path) -> None:
    if path.exists():
        return
    path.write_bytes(base64.b64decode(PLACEHOLDER_PNG_BASE64))


@pytest.mark.requires_test_data
def test_ai_creative_product_intro_smoke() -> None:
    if os.getenv("DOUGA_SMOKE_AI") != "1":
        pytest.skip("Set DOUGA_SMOKE_AI=1 to run creative smoke test")

    base_url = os.getenv("DOUGA_API_URL", "http://localhost:8000")
    assets_dir = Path(os.getenv("DOUGA_ASSETS_DIR", ASSETS_DIR_DEFAULT))

    if not assets_dir.exists():
        pytest.skip(f"Assets dir not found: {assets_dir}")

    with httpx.Client(timeout=300.0) as client:
        # Health check
        health = client.get(f"{base_url}/health")
        if health.status_code != 200:
            pytest.skip("Backend is not running")

        project_id = _create_project(client, base_url)

        # Collect assets
        videos = sorted([p for p in assets_dir.iterdir() if p.suffix.lower() in {".mp4", ".mov"}])
        images = sorted([p for p in assets_dir.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"}])

        # Ensure at least one image (placeholder if none)
        if not images:
            placeholder = assets_dir / "smoke_placeholder.png"
            _ensure_placeholder_image(placeholder)
            images = [placeholder]

        if not videos:
            pytest.skip("No video assets found in assets dir")

        # Upload one video and one image
        video_id = _upload_asset(client, base_url, project_id, videos[0], "video", "other")
        image_id = _upload_asset(client, base_url, project_id, images[0], "image", "slide")

        # AI chat prompt - include asset IDs so AI can use them directly
        provider = os.getenv("DOUGA_AI_PROVIDER")
        prompt = (
            "プロジェクトにアセットを登録しました。\n"
            f"- 動画: {videos[0].name} (asset_id: {video_id})\n"
            f"- 静止画: {images[0].name} (asset_id: {image_id})\n\n"
            "これらのアセットを使って、製品紹介の1分30秒の動画をつくってください。"
        )
        chat_payload = {
            "message": prompt,
            "history": [],
        }
        if provider:
            chat_payload["provider"] = provider

        chat_resp = client.post(
            f"{base_url}/api/ai/project/{project_id}/chat",
            headers=_auth_headers(),
            json=chat_payload,
        )
        chat_resp.raise_for_status()
        chat = chat_resp.json()

        # If API key is not configured, skip
        if (not chat.get("actions_applied")) and "APIキー" in chat.get("message", ""):
            pytest.skip("AI provider not configured (missing API key)")

        assert chat.get("actions_applied") is True, f"AI actions not applied: {chat}"

        # Fetch project timeline and validate output
        project_resp = client.get(
            f"{base_url}/api/projects/{project_id}",
            headers=_auth_headers(),
        )
        project_resp.raise_for_status()
        project = project_resp.json()

        duration_ms = project.get("duration_ms", 0)
        assert 60000 <= duration_ms <= 120000, f"Unexpected duration: {duration_ms}ms"

        timeline = project.get("timeline_data", {})
        used_asset_ids: set[str] = set()

        for layer in timeline.get("layers", []):
            for clip in layer.get("clips", []):
                if clip.get("asset_id"):
                    used_asset_ids.add(str(clip["asset_id"]))
        for track in timeline.get("audio_tracks", []):
            for clip in track.get("clips", []):
                if clip.get("asset_id"):
                    used_asset_ids.add(str(clip["asset_id"]))

        assert used_asset_ids, "No asset-based clips found in timeline"
        assert str(video_id) in used_asset_ids, "Video asset not used in timeline"
        assert str(image_id) in used_asset_ids, "Image asset not used in timeline"
