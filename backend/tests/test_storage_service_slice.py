from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks

from src.api.preview import _download_assets
from src.api.transcription import (
    TranscribeRequest,
    _transcriptions,
    start_transcription,
)
from src.services.storage_service import LocalStorageService


class _FakeScalarResult:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    def all(self) -> list[object]:
        return self._items


class _FakeExecuteResult:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    def scalars(self) -> _FakeScalarResult:
        return _FakeScalarResult(self._items)


class _FakeDbSession:
    def __init__(self, asset: object, project: object) -> None:
        self._asset = asset
        self._project = project

    async def execute(self, _query: object) -> _FakeExecuteResult:
        return _FakeExecuteResult([self._asset])

    async def get(self, model: object, _id: object) -> object | None:
        model_name = getattr(model, "__name__", "")
        if model_name == "Asset":
            return self._asset
        if model_name == "Project":
            return self._project
        return None


@pytest.fixture
def local_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LocalStorageService:
    from src.services import storage_service as storage_service_module

    monkeypatch.setattr(storage_service_module.settings, "local_storage_path", str(tmp_path))
    return LocalStorageService()


def test_local_storage_supports_shared_file_interfaces(local_storage: LocalStorageService) -> None:
    storage_key = "projects/test/assets/example.bin"

    public_url = local_storage.upload_file_content(b"hello", storage_key)
    assert public_url.endswith(storage_key)
    assert local_storage.download_file_content(storage_key) == b"hello"

    other_key = "projects/test/assets/from-fileobj.bin"
    upload_url = local_storage.upload_file_from_fileobj(
        other_key,
        BytesIO(b"payload"),
        "application/octet-stream",
    )
    assert upload_url.endswith(other_key)
    assert local_storage.get_file_path(other_key).read_bytes() == b"payload"


@pytest.mark.asyncio
async def test_preview_download_assets_uses_shared_storage_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset_id = uuid4()
    asset = SimpleNamespace(
        id=asset_id,
        storage_key="projects/test/assets/mock.mp4",
        name="Mock Asset",
    )
    db = _FakeDbSession(asset=asset, project=SimpleNamespace())

    class FakeStorage:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def download_file(self, storage_key: str, local_path: str) -> str:
            self.calls.append((storage_key, local_path))
            Path(local_path).write_bytes(b"asset-bytes")
            return local_path

    fake_storage = FakeStorage()
    monkeypatch.setattr("src.api.preview.get_storage_service", lambda: fake_storage)

    assets_local, asset_name_map = await _download_assets(
        {"layers": [{"clips": [{"asset_id": str(asset_id)}]}]},
        db,
        str(tmp_path),
    )

    local_path = assets_local[str(asset_id)]
    assert asset_name_map == {str(asset_id): "Mock Asset"}
    assert fake_storage.calls == [(asset.storage_key, local_path)]
    assert Path(local_path).read_bytes() == b"asset-bytes"


@pytest.mark.asyncio
async def test_start_transcription_downloads_via_shared_storage_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset_id = uuid4()
    owner_id = uuid4()
    asset = SimpleNamespace(
        id=asset_id,
        project_id=uuid4(),
        storage_key="projects/test/assets/transcription.mp4",
        type="audio",
    )
    project = SimpleNamespace(id=asset.project_id, user_id=owner_id)
    db = _FakeDbSession(asset=asset, project=project)
    current_user = SimpleNamespace(id=owner_id)
    background_tasks = BackgroundTasks()

    class FakeStorage:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def download_file(self, storage_key: str, local_path: str) -> str:
            self.calls.append((storage_key, local_path))
            Path(local_path).write_bytes(b"audio")
            return local_path

    fake_storage = FakeStorage()
    monkeypatch.setattr("src.api.transcription.get_storage_service", lambda: fake_storage)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    _transcriptions.clear()

    response = await start_transcription(
        TranscribeRequest(asset_id=asset_id),
        background_tasks,
        db=db,
        current_user=current_user,
    )

    assert response.status == "processing"
    assert len(background_tasks.tasks) == 1
    assert asset_id.hex not in _transcriptions
    assert str(asset_id) in _transcriptions
    assert _transcriptions[str(asset_id)].status == "processing"
    assert fake_storage.calls
    downloaded_key, downloaded_path = fake_storage.calls[0]
    assert downloaded_key == asset.storage_key
    assert Path(downloaded_path).read_bytes() == b"audio"
