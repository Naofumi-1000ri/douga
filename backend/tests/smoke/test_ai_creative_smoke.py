"""
Smoke test: AI creative instruction flow using local assets.

Run:
  DOUGA_SMOKE_AI=1 pytest tests/smoke/test_ai_creative_smoke.py -v

Env:
  DOUGA_API_URL             (default: http://localhost:8000)
  DOUGA_ASSETS_DIR          (default: /Users/hgs/devel/douga_root/assets)
  DOUGA_AI_PROVIDER         (optional: openai|gemini|anthropic)
  DOUGA_API_KEY             (optional: X-API-Key header)
  DOUGA_SMOKE_USER_PROMPT   (set to 1 to run user-like prompt test)
  DOUGA_SMOKE_IMAGE_COUNT   (default: 2)
  DOUGA_SMOKE_STT           (set to 1 to attempt transcription)
  DOUGA_SMOKE_STT_POLL_SEC  (default: 3)
  DOUGA_SMOKE_STT_TIMEOUT   (default: 60)
"""

from __future__ import annotations

import base64
import mimetypes
import os
import time
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


def _maybe_transcribe(
    client: httpx.Client,
    base_url: str,
    asset_id: str,
) -> str | None:
    if os.getenv("DOUGA_SMOKE_STT") != "1":
        return None

    start_resp = client.post(
        f"{base_url}/api/transcription",
        headers=_auth_headers(),
        json={"asset_id": asset_id, "language": "ja"},
    )
    if start_resp.status_code != 200:
        pytest.skip(f"Transcription start failed: {start_resp.text}")

    poll_interval = float(os.getenv("DOUGA_SMOKE_STT_POLL_SEC", "3"))
    timeout_sec = float(os.getenv("DOUGA_SMOKE_STT_TIMEOUT", "60"))
    deadline = time.time() + timeout_sec

    while time.time() < deadline:
        time.sleep(poll_interval)
        status_resp = client.get(
            f"{base_url}/api/transcription/{asset_id}",
            headers=_auth_headers(),
        )
        if status_resp.status_code != 200:
            pytest.skip(f"Transcription status failed: {status_resp.text}")

        payload = status_resp.json()
        status = payload.get("status")
        if status in {"completed", "failed"}:
            error_message = payload.get("error_message")
            if error_message:
                pytest.skip(f"Transcription failed: {error_message}")

            segments = payload.get("segments", [])
            transcript = " ".join(seg.get("text", "") for seg in segments).strip()
            if transcript:
                return transcript[:600]
            return None

    pytest.skip("Transcription timed out")


def _run_smoke(include_asset_ids: bool) -> None:
    if os.getenv("DOUGA_SMOKE_AI") != "1":
        pytest.skip("Set DOUGA_SMOKE_AI=1 to run creative smoke tests")
    if not include_asset_ids and os.getenv("DOUGA_SMOKE_USER_PROMPT") != "1":
        pytest.skip("Set DOUGA_SMOKE_USER_PROMPT=1 to run user-like prompt test")

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

        image_count = max(1, int(os.getenv("DOUGA_SMOKE_IMAGE_COUNT", "2")))
        image_paths = images[:image_count]

        # Upload assets
        video_id = _upload_asset(client, base_url, project_id, videos[0], "video", "other")
        image_ids = [
            _upload_asset(client, base_url, project_id, image_path, "image", "slide")
            for image_path in image_paths
        ]

        transcript = _maybe_transcribe(client, base_url, video_id)
        narration_hint = ""
        if transcript:
            narration_hint = (
                "\n参考ナレーション（自動文字起こし、要約して使用可）:\n"
                f"{transcript}\n"
            )

        # AI chat prompt
        provider = os.getenv("DOUGA_AI_PROVIDER")
        if include_asset_ids:
            image_lines = "\n".join(
                f"- 静止画: {path.name} (asset_id: {asset_id})"
                for path, asset_id in zip(image_paths, image_ids)
            )
            prompt = (
                "プロジェクトにアセットを登録しました。\n"
                f"- 動画: {videos[0].name} (asset_id: {video_id})\n"
                f"{image_lines}\n\n"
                "Udemy用の講座のセクション1（冒頭）を1分30秒で作ってください。\n"
                "目的: 講座の導入、受講で得られること、セクション構成の提示。\n"
                "構成目安: 0-20秒=導入/挨拶, 20-50秒=ゴール提示, 50-90秒=流れ/次予告。\n"
                "日本語で、簡潔かつ講座らしいトーン。"
                f"{narration_hint}"
            )
        else:
            prompt = (
                "assetsフォルダに動画と静止画があります。"
                f"動画: {videos[0].name}, 静止画: {', '.join(p.name for p in image_paths)} を使って、"
                "Udemy用の講座のセクション1（冒頭）を1分30秒で作ってください。"
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
        assert any(str(image_id) in used_asset_ids for image_id in image_ids), (
            "Image assets not used in timeline"
        )


@pytest.mark.requires_test_data
def test_ai_creative_product_intro_smoke_user_prompt() -> None:
    """User-like prompt (no asset IDs)."""
    _run_smoke(include_asset_ids=False)


@pytest.mark.requires_test_data
def test_ai_creative_product_intro_smoke_with_ids() -> None:
    """Deterministic prompt (explicit asset IDs)."""
    _run_smoke(include_asset_ids=True)
