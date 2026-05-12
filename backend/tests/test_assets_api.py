from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import assets as assets_api
from src.api.deps import AuthenticatedUser, get_authenticated_user


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
    from datetime import datetime, timezone

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
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
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
        expires_minutes=5760,
    )


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
