from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from src.api import assets as assets_api


def test_select_audio_asset_metadata_prefers_probed_audio_payload() -> None:
    duration_ms, sample_rate, channels = assets_api._select_audio_asset_metadata(
        {
            "duration_ms": 6123,
            "sample_rate": 48000,
            "channels": 1,
        }
    )

    assert duration_ms == 6123
    assert sample_rate == 48000
    assert channels == 1


def test_select_audio_asset_metadata_falls_back_to_defaults_when_probe_missing() -> None:
    duration_ms, sample_rate, channels = assets_api._select_audio_asset_metadata(None)

    assert duration_ms is None
    assert sample_rate == 44100
    assert channels == 2


@pytest.mark.asyncio
async def test_generate_waveform_background_syncs_asset_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploaded_payload: dict[str, object] = {}
    synced_duration: dict[str, object] = {}

    class FakeStorage:
        async def download_file(self, storage_key: str, file_path: str) -> None:
            return None

        def upload_file_content(
            self,
            content: bytes,
            storage_key: str,
            content_type: str,
        ) -> None:
            uploaded_payload["content"] = content
            uploaded_payload["storage_key"] = storage_key
            uploaded_payload["content_type"] = content_type

    class FakePreviewService:
        def generate_waveform(
            self, file_path: str, samples_per_second: float = 10.0
        ) -> SimpleNamespace:
            return SimpleNamespace(peaks=[0.1, 0.2, 0.3], duration_ms=6123, sample_rate=44100)

    async def fake_sync_asset_duration(asset_id: UUID, duration_ms: int) -> None:
        synced_duration["asset_id"] = asset_id
        synced_duration["duration_ms"] = duration_ms

    monkeypatch.setattr(assets_api, "get_storage_service", lambda: FakeStorage())
    monkeypatch.setattr(assets_api, "PreviewService", FakePreviewService)
    monkeypatch.setattr(assets_api, "_sync_asset_duration_from_waveform", fake_sync_asset_duration)

    project_id = uuid4()
    asset_id = uuid4()
    await assets_api._generate_waveform_background(project_id, asset_id, "audio/test.mp3")

    assert uploaded_payload["storage_key"] == f"waveforms/{project_id}/{asset_id}.json"
    assert uploaded_payload["content_type"] == "application/json"
    assert synced_duration == {
        "asset_id": asset_id,
        "duration_ms": 6123,
    }


@pytest.mark.asyncio
async def test_auto_extract_audio_uses_probed_audio_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_asset: dict[str, Any] = {}
    follow_up_calls: dict[str, Any] = {"waveform": None, "analysis": None}

    class FakeStorage:
        def get_public_url(self, storage_key: str) -> str:
            return f"https://storage.example/{storage_key}"

    class FakeResult:
        def scalar_one_or_none(self) -> None:
            return None

    class ExistingSession:
        async def execute(self, stmt: Any) -> FakeResult:
            return FakeResult()

        async def commit(self) -> None:
            return None

    class CreateSession:
        def add(self, asset: Any) -> None:
            created_asset["asset"] = asset

        async def commit(self) -> None:
            return None

        async def refresh(self, asset: Any) -> None:
            asset.id = uuid4()

    class SessionContext:
        def __init__(self, session: Any) -> None:
            self.session = session

        async def __aenter__(self) -> Any:
            return self.session

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

    sessions = [ExistingSession(), CreateSession()]

    async def fake_extract_audio_from_gcs(**kwargs: Any) -> tuple[str, int]:
        return ("audio/generated.mp3", 1234)

    async def fake_probe_storage_media_info(
        storage: Any, storage_key: str, asset_type: str
    ) -> dict[str, int]:
        assert storage_key == "audio/generated.mp3"
        assert asset_type == "audio"
        return {
            "duration_ms": 6123,
            "sample_rate": 48000,
            "channels": 1,
        }

    async def fake_generate_waveform_background(
        project_id: UUID, asset_id: UUID, audio_key: str
    ) -> None:
        follow_up_calls["waveform"] = (project_id, asset_id, audio_key)

    async def fake_analyze_audio_background(
        asset_id: UUID, audio_key: str, duration_ms: int | None
    ) -> None:
        follow_up_calls["analysis"] = (asset_id, audio_key, duration_ms)

    monkeypatch.setattr(assets_api, "get_storage_service", lambda: FakeStorage())
    monkeypatch.setattr(assets_api, "extract_audio_from_gcs", fake_extract_audio_from_gcs)
    monkeypatch.setattr(assets_api, "_probe_storage_media_info", fake_probe_storage_media_info)
    monkeypatch.setattr(
        assets_api, "_generate_waveform_background", fake_generate_waveform_background
    )
    monkeypatch.setattr(assets_api, "_analyze_audio_background", fake_analyze_audio_background)
    monkeypatch.setattr(
        assets_api,
        "async_session_maker",
        lambda: SessionContext(sessions.pop(0)),
    )

    project_id = uuid4()
    video_asset_id = uuid4()
    await assets_api._auto_extract_audio_background(
        project_id,
        video_asset_id,
        "video/source.mp4",
        "recording.mp4",
    )

    asset = cast(Any, created_asset["asset"])
    assert asset.duration_ms == 6123
    assert asset.sample_rate == 48000
    assert asset.channels == 1
    assert asset.source_asset_id == video_asset_id
    assert cast(tuple[UUID, str, int | None], follow_up_calls["analysis"])[2] == 6123
