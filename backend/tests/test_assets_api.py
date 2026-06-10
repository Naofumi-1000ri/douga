from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient

from src.api import assets as assets_api
from src.api.deps import AuthenticatedUser, get_authenticated_user
from src.constants.media_urls import SIGNED_MEDIA_URL_EXPIRES_MINUTES
from src.schemas.asset import AssetCreate


def _make_asset(**overrides):
    values = {
        "id": uuid4(),
        "name": "narration.mp3",
        "type": "audio",
        "subtype": "narration",
        "duration_ms": 6000,
        "sample_rate": 48000,
        "channels": 1,
        "source_asset_id": None,
        "storage_key": "audio/narration.mp3",
        "is_internal": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeScalars:
    def __init__(self, items):
        self._items = items

    def all(self):
        return self._items


class _FakeResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return _FakeScalars(self._items)


class _FakeSession:
    def __init__(self, items):
        self._items = items

    async def execute(self, query):
        limit_clause = getattr(query, "_limit_clause", None)
        offset_clause = getattr(query, "_offset_clause", None)
        limit = getattr(limit_clause, "value", None)
        offset = getattr(offset_clause, "value", 0) or 0

        items = self._items[offset:]
        if limit is not None:
            items = items[:limit]

        return _FakeResult(items)


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _build_client():
    app = FastAPI()
    app.include_router(assets_api.router, prefix="/api")
    app.dependency_overrides[get_authenticated_user] = lambda: AuthenticatedUser(id=uuid4())
    return app


async def _fake_get_accessible_project(project_id, user_id, db):
    return SimpleNamespace(id=project_id, user_id=user_id)


def _extract_x_goog_expires(url: str) -> int:
    values = parse_qs(urlparse(url).query).get("X-Goog-Expires")
    assert values is not None
    return int(values[0])


def test_asset_timing_audit_is_bounded_and_skips_expensive_sources_by_default(monkeypatch):
    project_id = uuid4()
    assets = [_make_asset(name="first.mp3"), _make_asset(name="second.mp3")]
    waveform_calls = []

    async def fake_load_waveform_artifact(storage, current_project_id, asset_id):
        waveform_calls.append((current_project_id, asset_id))
        return {"duration_ms": 6123, "sample_rate": 44100}

    monkeypatch.setattr(
        assets_api, "async_session_maker", lambda: _SessionContext(_FakeSession(assets))
    )
    monkeypatch.setattr(assets_api, "get_accessible_project", _fake_get_accessible_project)
    monkeypatch.setattr(assets_api, "_load_waveform_artifact", fake_load_waveform_artifact)
    monkeypatch.setattr(assets_api, "get_storage_service", lambda: SimpleNamespace())

    with TestClient(_build_client(), raise_server_exceptions=False) as client:
        response = client.get(f"/api/projects/{project_id}/asset-timing-audit?limit=1")

    assert response.status_code == 200
    data = response.json()
    assert data["limit"] == 1
    assert data["offset"] == 0
    assert data["returned_entries"] == 1
    assert data["has_more"] is True
    assert len(data["entries"]) == 1
    assert waveform_calls == []


def test_thumbnail_url_legacy_fallback_dropped():
    """#250: asset.thumbnail_url legacy fallback was removed.

    If thumbnail_storage_key is None, response.thumbnail_url should be None
    even if the DB has a value in asset.thumbnail_url (which may be a stale
    signed URL).
    """
    asset = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        name="video.mp4",
        type="video",
        subtype="background",
        storage_key="video/video.mp4",
        storage_url="https://storage.googleapis.com/bucket/video.mp4",
        thumbnail_storage_key=None,
        thumbnail_url="https://storage.googleapis.com/bucket/old-stale-signed-url?X-Goog-Date=20240101",
        duration_ms=5000,
        width=1920,
        height=1080,
        file_size=1024000,
        mime_type="video/mp4",
        sample_rate=None,
        channels=None,
        has_alpha=False,
        chroma_key_color=None,
        hash=None,
        is_internal=False,
        folder_id=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        asset_metadata=None,
    )

    # storage.generate_download_url succeeds for storage_key but should never
    # be called for thumbnail (no thumbnail_storage_key).
    mock_storage = MagicMock()
    mock_storage.generate_download_url.return_value = "https://signed.example.com/video.mp4"

    result = assets_api._asset_to_response_with_signed_url(asset, mock_storage)

    # Legacy fallback must NOT apply: stale signed URL in DB should be ignored.
    assert result.thumbnail_url is None
    # generate_download_url called exactly once — only for storage_key, not thumbnail.
    mock_storage.generate_download_url.assert_called_once_with(
        storage_key="video/video.mp4",
        expires_minutes=SIGNED_MEDIA_URL_EXPIRES_MINUTES,
    )


def test_thumbnail_diagnostics_identifies_missing_url_object():
    asset = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        name="video.mp4",
        type="video",
        mime_type="video/mp4",
        storage_key="projects/project-id/assets/video.mp4",
        thumbnail_storage_key="thumbnails/project-id/asset-id/0_64x36.jpg",
        thumbnail_url=None,
    )
    mock_storage = MagicMock()
    # _safe_file_exists uses _file_exists_sync directly (called inside asyncio.to_thread)
    mock_storage._file_exists_sync.side_effect = (
        lambda key: key == "projects/project-id/assets/video.mp4"
    )

    result = assets_api._diagnose_thumbnail_failure(
        asset,
        mock_storage,
        url=("https://storage.googleapis.com/bucket/thumbnails/project-id/asset-id/0_64x36.jpg"),
        source="asset-library-thumbnail_url",
        url_http_status=404,
        url_http_error=None,
    )

    assert result["diagnosis"] == "url_object_missing"
    assert result["url"]["storage_key"] == "thumbnails/project-id/asset-id/0_64x36.jpg"
    assert result["url"]["matches_thumbnail_storage_key"] is True
    assert result["storage"] == {
        "asset_storage_key_exists": True,
        "thumbnail_storage_key_exists": False,
        "url_storage_key_exists": False,
    }


def test_thumbnail_diagnostics_identifies_expired_signed_url():
    asset = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        name="video.mp4",
        type="video",
        mime_type="video/mp4",
        storage_key="projects/project-id/assets/video.mp4",
        thumbnail_storage_key="thumbnails/project-id/asset-id/0_64x36.jpg",
        thumbnail_url=None,
    )
    mock_storage = MagicMock()
    mock_storage._file_exists_sync.return_value = True

    result = assets_api._diagnose_thumbnail_failure(
        asset,
        mock_storage,
        url=(
            "https://storage.googleapis.com/bucket/"
            "thumbnails/project-id/asset-id/0_64x36.jpg"
            "?X-Goog-Date=20240101T000000Z&X-Goog-Expires=60"
        ),
        source="asset-library-thumbnail_url",
        url_http_status=403,
        url_http_error=None,
    )

    assert result["diagnosis"] == "signed_url_expired"
    assert result["url"]["is_expired"] is True
    assert result["url"]["expires_in_seconds"] == 60
    assert result["probe"]["http_status"] == 403


def test_thumbnail_diagnostics_does_not_probe_unrelated_url_storage_key():
    asset = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        name="video.mp4",
        type="video",
        mime_type="video/mp4",
        storage_key="projects/project-id/assets/video.mp4",
        thumbnail_storage_key="thumbnails/project-id/asset-id/0_64x36.jpg",
        thumbnail_url=None,
    )
    mock_storage = MagicMock()
    mock_storage._file_exists_sync.return_value = True

    result = assets_api._diagnose_thumbnail_failure(
        asset,
        mock_storage,
        url="https://storage.googleapis.com/bucket/projects/other-project/private.jpg",
        source="asset-library-thumbnail_url",
        url_http_status=None,
        url_http_error="probe_skipped",
    )

    assert result["diagnosis"] == "thumbnail_url_points_to_unexpected_object"
    assert result["storage"]["url_storage_key_exists"] is None
    mock_storage._file_exists_sync.assert_any_call("projects/project-id/assets/video.mp4")
    mock_storage._file_exists_sync.assert_any_call("thumbnails/project-id/asset-id/0_64x36.jpg")
    assert mock_storage._file_exists_sync.call_count == 2


@pytest.mark.asyncio
async def test_thumbnail_diagnostics_probe_rejects_non_gcs_hosts():
    status_code, error = await assets_api._probe_media_url_status("http://127.0.0.1:8000/internal")

    assert status_code is None
    assert error == "unsupported_scheme"


def test_asset_storage_url_persistence_uses_storage_key() -> None:
    assert (
        assets_api._asset_storage_url_for_persistence("projects/project-id/assets/file.mp4")
        == "projects/project-id/assets/file.mp4"
    )


@pytest.mark.asyncio
async def test_register_asset_persists_storage_key_not_client_url(monkeypatch):
    project_id = uuid4()
    user_id = uuid4()
    captured_asset = None

    class FakeResult:
        def scalar_one_or_none(self):
            return None

    class FakeDb:
        async def execute(self, query):
            return FakeResult()

        def add(self, asset):
            nonlocal captured_asset
            captured_asset = asset

        async def commit(self):
            return None

        async def refresh(self, asset):
            asset.id = uuid4()
            asset.created_at = datetime.now(UTC)
            asset.has_alpha = False
            asset.hash = None
            asset.is_internal = False
            asset.folder_id = None
            asset.asset_metadata = None

    async def fake_verify_project_access(current_project_id, current_user_id, db, require_role=None):
        assert current_project_id == project_id
        assert current_user_id == user_id

    storage = MagicMock()
    storage.generate_download_url.return_value = "https://signed.example.com/file.png"

    monkeypatch.setattr(assets_api, "verify_project_access", fake_verify_project_access)
    monkeypatch.setattr(assets_api, "get_storage_service", lambda: storage)

    result = await assets_api.register_asset(
        project_id=project_id,
        asset_data=AssetCreate(
            name="file.png",
            type="image",
            subtype="slide",
            storage_key="projects/project-id/assets/file.png",
            storage_url="https://storage.googleapis.com/douga-assets/stale-signed-url.png",
            file_size=123,
            mime_type="image/png",
            width=100,
            height=100,
        ),
        current_user=SimpleNamespace(id=user_id),
        db=FakeDb(),
        background_tasks=BackgroundTasks(),
    )

    assert captured_asset is not None
    assert captured_asset.storage_url == "projects/project-id/assets/file.png"
    assert result.storage_url == "https://signed.example.com/file.png"


def test_asset_response_storage_signing_failure_falls_back_to_public_url():
    asset = SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        name="video.mp4",
        type="video",
        subtype="background",
        storage_key="video/video.mp4",
        storage_url="https://storage.googleapis.com/bucket/stale-signed-url.mp4",
        thumbnail_storage_key=None,
        thumbnail_url=None,
        duration_ms=5000,
        width=1920,
        height=1080,
        file_size=1024000,
        mime_type="video/mp4",
        sample_rate=None,
        channels=None,
        has_alpha=False,
        chroma_key_color=None,
        hash=None,
        is_internal=False,
        folder_id=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        asset_metadata=None,
    )
    mock_storage = MagicMock()
    mock_storage.generate_download_url.side_effect = RuntimeError("sign failed")
    mock_storage.get_public_url.return_value = (
        "https://storage.googleapis.com/bucket/video/video.mp4"
    )

    result = assets_api._asset_to_response_with_signed_url(asset, mock_storage)

    assert result.storage_url == "https://storage.googleapis.com/bucket/video/video.mp4"
    mock_storage.get_public_url.assert_called_once_with("video/video.mp4")


@pytest.mark.asyncio
async def test_grid_thumbnails_specific_times_use_four_day_signed_url(monkeypatch):
    project_id = uuid4()
    asset_id = uuid4()
    user_id = uuid4()

    async def fake_get_asset_short_lived(current_project_id, current_asset_id, current_user_id):
        assert current_project_id == project_id
        assert current_asset_id == asset_id
        assert current_user_id == user_id
        return SimpleNamespace(type="video", duration_ms=5000)

    class FakeStorage:
        async def file_exists(self, key):
            return True

        def generate_download_url(self, key, expires_minutes):
            return f"https://storage.example.com/{key}?X-Goog-Expires={expires_minutes * 60}"

    monkeypatch.setattr(assets_api, "_get_asset_short_lived", fake_get_asset_short_lived)
    monkeypatch.setattr(assets_api, "get_storage_service", lambda: FakeStorage())

    result = await assets_api.get_grid_thumbnails(
        project_id=project_id,
        asset_id=asset_id,
        times="0,1000",
        current_user=SimpleNamespace(id=user_id),
    )

    assert _extract_x_goog_expires(result.thumbnails[0]) == 345600
    assert _extract_x_goog_expires(result.thumbnails[1000]) == 345600


@pytest.mark.asyncio
async def test_grid_thumbnails_full_list_use_four_day_signed_url(monkeypatch):
    project_id = uuid4()
    asset_id = uuid4()
    user_id = uuid4()

    async def fake_get_asset_short_lived(current_project_id, current_asset_id, current_user_id):
        assert current_project_id == project_id
        assert current_asset_id == asset_id
        assert current_user_id == user_id
        return SimpleNamespace(type="video", duration_ms=5000)

    class FakeStorage:
        async def list_files(self, prefix):
            return [f"{prefix}0.jpg", f"{prefix}1000.jpg"]

        def generate_download_url(self, key, expires_minutes):
            return f"https://storage.example.com/{key}?X-Goog-Expires={expires_minutes * 60}"

    monkeypatch.setattr(assets_api, "_get_asset_short_lived", fake_get_asset_short_lived)
    monkeypatch.setattr(assets_api, "get_storage_service", lambda: FakeStorage())

    result = await assets_api.get_grid_thumbnails(
        project_id=project_id,
        asset_id=asset_id,
        current_user=SimpleNamespace(id=user_id),
    )

    assert _extract_x_goog_expires(result.thumbnails[0]) == 345600
    assert _extract_x_goog_expires(result.thumbnails[1000]) == 345600


def test_asset_timing_audit_requires_asset_id_for_storage_probe(monkeypatch):
    monkeypatch.setattr(assets_api, "get_accessible_project", _fake_get_accessible_project)

    with TestClient(_build_client(), raise_server_exceptions=False) as client:
        response = client.get(
            "/api/projects/00000000-0000-0000-0000-000000000001/asset-timing-audit?include_storage_probe=true"
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "include_storage_probe requires asset_id"


def test_asset_timing_audit_single_asset_can_include_waveform_and_storage_probe(monkeypatch):
    project_id = uuid4()
    asset = _make_asset()
    waveform_calls = []
    probe_calls = []

    async def fake_load_waveform_artifact(storage, current_project_id, asset_id):
        waveform_calls.append((current_project_id, asset_id))
        return {"duration_ms": 6123, "sample_rate": 44100}

    async def fake_probe_storage_media_info(storage, storage_key, asset_type):
        probe_calls.append((storage_key, asset_type))
        return {"duration_ms": 6124, "sample_rate": 48000, "channels": 1}

    monkeypatch.setattr(
        assets_api,
        "async_session_maker",
        lambda: _SessionContext(_FakeSession([asset])),
    )
    monkeypatch.setattr(assets_api, "get_accessible_project", _fake_get_accessible_project)
    monkeypatch.setattr(assets_api, "_load_waveform_artifact", fake_load_waveform_artifact)
    monkeypatch.setattr(assets_api, "_probe_storage_media_info", fake_probe_storage_media_info)
    monkeypatch.setattr(assets_api, "get_storage_service", lambda: SimpleNamespace())

    with TestClient(_build_client(), raise_server_exceptions=False) as client:
        response = client.get(
            f"/api/projects/{project_id}/asset-timing-audit"
            f"?asset_id={asset.id}&include_waveform=true&include_storage_probe=true"
        )

    assert response.status_code == 200
    data = response.json()
    assert data["returned_entries"] == 1
    assert data["has_more"] is False
    assert len(data["entries"][0]["drifts"]) == 5
    assert {drift["field"] for drift in data["entries"][0]["drifts"]} == {
        "duration_ms",
        "sample_rate",
    }
    assert waveform_calls == [(project_id, asset.id)]
    assert probe_calls == [(asset.storage_key, asset.type)]
