import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import ai_video as ai_video_api
from src.api.ai_video import skill_generate_telop
from src.api.deps import EditContext, get_current_user, get_db
from src.schemas.ai_video import GenerateTelopRequest


def _make_segment(text: str, start_ms: int, end_ms: int) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        start_ms=start_ms,
        end_ms=end_ms,
        cut=False,
    )


def _make_transcription_service() -> MagicMock:
    service = MagicMock()
    service.transcribe.return_value = SimpleNamespace(
        segments=[_make_segment("こんにちは", 0, 1200)]
    )
    service.detect_silences_ffmpeg.return_value = []
    return service


def _make_db_with_asset(asset: SimpleNamespace) -> AsyncMock:
    db = AsyncMock()
    execute_result = MagicMock()
    execute_result.scalar_one_or_none.return_value = asset
    db.execute = AsyncMock(return_value=execute_result)
    db.flush = AsyncMock()
    return db


def _build_api_client(db: AsyncMock, current_user: SimpleNamespace) -> FastAPI:
    app = FastAPI()
    app.include_router(ai_video_api.router, prefix="/api/ai-video")

    async def _fake_get_db():
        yield db

    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_db] = _fake_get_db
    return app


@pytest.mark.asyncio
async def test_generate_telop_uses_selected_audio_track_on_sequence():
    project_id = uuid.uuid4()
    user_id = uuid.uuid4()
    asset_id = uuid.uuid4()

    project_timeline = {
        "duration_ms": 0,
        "layers": [{"id": "project-layer", "name": "Project Layer", "type": "content", "clips": []}],
        "audio_tracks": [],
    }
    sequence_timeline = {
        "duration_ms": 3000,
        "layers": [{"id": "sequence-layer", "name": "Sequence Layer", "type": "content", "clips": []}],
        "audio_tracks": [
            {
                "id": "track-narration",
                "name": "Narration",
                "type": "narration",
                "clips": [
                    {
                        "id": "clip-audio-source",
                        "asset_id": str(asset_id),
                        "start_ms": 0,
                        "duration_ms": 3000,
                        "in_point_ms": 0,
                        "out_point_ms": 3000,
                        "volume": 1.0,
                        "fade_in_ms": 0,
                        "fade_out_ms": 0,
                    }
                ],
            }
        ],
    }

    project = MagicMock()
    project.id = project_id
    project.timeline_data = project_timeline
    project.duration_ms = 0

    sequence = MagicMock()
    sequence.id = uuid.uuid4()
    sequence.timeline_data = sequence_timeline
    sequence.duration_ms = 3000

    edit_ctx = EditContext(project=project, sequence=sequence)
    current_user = SimpleNamespace(id=user_id)
    asset = SimpleNamespace(
        id=asset_id,
        name="narration.wav",
        storage_key="assets/narration.wav",
        duration_ms=3000,
        type="audio",
    )
    db = _make_db_with_asset(asset)
    storage = MagicMock()
    storage.download_file = AsyncMock(return_value=None)
    transcribe_service = _make_transcription_service()
    silence_service = _make_transcription_service()

    with (
        patch("src.api.ai_video.get_edit_context_for_write", AsyncMock(return_value=edit_ctx)),
        patch("src.api.ai_video.get_storage_service", return_value=storage),
        patch(
            "src.services.transcription_service.TranscriptionService",
            side_effect=[transcribe_service, silence_service],
        ),
    ):
        response = await skill_generate_telop(
            project_id=project_id,
            request=GenerateTelopRequest(source_type="audio_track", source_id="track-narration"),
            current_user=current_user,
            db=db,
            x_edit_session="edit-token",
        )

    assert response.success is True
    assert response.changes["telops_added"] == 1
    assert any(layer["name"] == "テロップ（自動生成）" for layer in sequence.timeline_data["layers"])
    assert all(layer["name"] != "テロップ（自動生成）" for layer in project.timeline_data["layers"])
    assert sequence.duration_ms == sequence.timeline_data["duration_ms"]
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_telop_resolves_layer_source_from_sequence_timeline():
    project_id = uuid.uuid4()
    user_id = uuid.uuid4()
    asset_id = uuid.uuid4()

    project = MagicMock()
    project.id = project_id
    project.timeline_data = {
        "duration_ms": 0,
        "layers": [{"id": "project-layer", "name": "Project Layer", "type": "content", "clips": []}],
        "audio_tracks": [],
    }
    project.duration_ms = 0

    sequence = MagicMock()
    sequence.id = uuid.uuid4()
    sequence.timeline_data = {
        "duration_ms": 3000,
        "layers": [
            {
                "id": "sequence-layer-source",
                "name": "Source Layer",
                "type": "content",
                "clips": [
                    {
                        "id": "clip-video-source",
                        "asset_id": str(asset_id),
                        "start_ms": 0,
                        "duration_ms": 3000,
                        "in_point_ms": 0,
                        "out_point_ms": 3000,
                        "transform": {"x": 0, "y": 0, "scale": 1},
                        "effects": {"opacity": 1},
                    }
                ],
            }
        ],
        "audio_tracks": [],
    }
    sequence.duration_ms = 3000

    edit_ctx = EditContext(project=project, sequence=sequence)
    current_user = SimpleNamespace(id=user_id)
    asset = SimpleNamespace(
        id=asset_id,
        name="screen-recording.mp4",
        storage_key="assets/screen-recording.mp4",
        duration_ms=3000,
        type="video",
    )
    db = _make_db_with_asset(asset)
    storage = MagicMock()
    storage.download_file = AsyncMock(return_value=None)
    transcribe_service = _make_transcription_service()
    silence_service = _make_transcription_service()

    with (
        patch("src.api.ai_video.get_edit_context_for_write", AsyncMock(return_value=edit_ctx)),
        patch("src.api.ai_video.get_storage_service", return_value=storage),
        patch(
            "src.services.transcription_service.TranscriptionService",
            side_effect=[transcribe_service, silence_service],
        ),
    ):
        response = await skill_generate_telop(
            project_id=project_id,
            request=GenerateTelopRequest(layer_id="sequence-layer-source"),
            current_user=current_user,
            db=db,
            x_edit_session="edit-token",
        )

    assert response.success is True
    assert response.changes["telops_added"] == 1
    assert any(layer["name"] == "テロップ（自動生成）" for layer in sequence.timeline_data["layers"])
    assert all(layer["name"] != "テロップ（自動生成）" for layer in project.timeline_data["layers"])


def test_generate_telop_api_accepts_audio_track_source_and_edit_session_header():
    project_id = uuid.uuid4()
    user_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    edit_session_token = "edit-token"

    project = MagicMock()
    project.id = project_id
    project.timeline_data = {"duration_ms": 0, "layers": [], "audio_tracks": []}
    project.duration_ms = 0

    sequence = MagicMock()
    sequence.id = uuid.uuid4()
    sequence.timeline_data = {
        "duration_ms": 3000,
        "layers": [],
        "audio_tracks": [
            {
                "id": "track-narration",
                "name": "Narration",
                "type": "narration",
                "clips": [
                    {
                        "id": "clip-audio-source",
                        "asset_id": str(asset_id),
                        "start_ms": 0,
                        "duration_ms": 3000,
                        "in_point_ms": 0,
                        "out_point_ms": 3000,
                        "volume": 1.0,
                        "fade_in_ms": 0,
                        "fade_out_ms": 0,
                    }
                ],
            }
        ],
    }
    sequence.duration_ms = 3000

    edit_ctx = EditContext(project=project, sequence=sequence)
    current_user = SimpleNamespace(id=user_id)
    db = _make_db_with_asset(
        SimpleNamespace(
            id=asset_id,
            name="narration.wav",
            storage_key="assets/narration.wav",
            duration_ms=3000,
            type="audio",
        )
    )
    storage = MagicMock()
    storage.download_file = AsyncMock(return_value=None)
    transcribe_service = _make_transcription_service()
    silence_service = _make_transcription_service()
    captured_call: dict[str, object] = {}

    async def fake_get_edit_context_for_write(
        requested_project_id, requested_current_user, requested_db, requested_edit_session
    ):
        captured_call["project_id"] = requested_project_id
        captured_call["current_user"] = requested_current_user
        captured_call["db"] = requested_db
        captured_call["x_edit_session"] = requested_edit_session
        return edit_ctx

    with (
        patch("src.api.ai_video.get_edit_context_for_write", fake_get_edit_context_for_write),
        patch("src.api.ai_video.get_storage_service", return_value=storage),
        patch(
            "src.services.transcription_service.TranscriptionService",
            side_effect=[transcribe_service, silence_service],
        ),
        TestClient(_build_api_client(db, current_user), raise_server_exceptions=False) as client,
    ):
        response = client.post(
            f"/api/ai-video/projects/{project_id}/generate-telop",
            json={"source_type": "audio_track", "source_id": "track-narration"},
            headers={"X-Edit-Session": edit_session_token},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["changes"]["telops_added"] == 1
    assert captured_call["project_id"] == project_id
    assert captured_call["current_user"] == current_user
    assert captured_call["db"] == db
    assert captured_call["x_edit_session"] == edit_session_token
    assert any(layer["name"] == "テロップ（自動生成）" for layer in sequence.timeline_data["layers"])
    assert all(layer["name"] != "テロップ（自動生成）" for layer in project.timeline_data["layers"])
