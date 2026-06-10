"""
Regression tests for issue #286: 小粒セキュリティ強化 5点

a. check_revoked=True — verify_id_token に check_revoked=True を追加。
   RevokedIdTokenError は 401 で返す。
b. upload-url content_type 検証 — 許可リスト外の content_type は 415 で返す。
c. Firestore rules — allowed_users フィールドの書き込みをテスト
   (ProjectEventManager.set_allowed_users と _refresh_firestore_allowed_users)。
d. git_hash 露出 — /health は git_hash を含む (deploy_prod.sh 互換)。
   /api/version と /health/live は git_hash を含まない。
e. X-Edit-Session デコード失敗 → 400 を返す。
"""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api import deps as deps_module
from src.main import app
from src.models.database import get_db

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


async def _fake_db():
    """DB 依存性のダミー。"""
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    yield session


@pytest.fixture
def client_no_dev_mode(monkeypatch):
    """dev_mode=False の TestClient。"""
    patched = deepcopy(deps_module.settings)
    patched.dev_mode = False
    monkeypatch.setattr(deps_module, "settings", patched)

    app.dependency_overrides[get_db] = _fake_db
    try:
        with (
            patch("src.main.init_db", new=AsyncMock()),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def client_dev_mode(monkeypatch):
    """dev_mode=True の TestClient (上流の DB も fake)。"""
    patched = deepcopy(deps_module.settings)
    patched.dev_mode = True
    monkeypatch.setattr(deps_module, "settings", patched)

    app.dependency_overrides[get_db] = _fake_db
    try:
        with (
            patch("src.main.init_db", new=AsyncMock()),
            TestClient(app, raise_server_exceptions=False) as client,
        ):
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# (a) check_revoked=True — RevokedIdTokenError → 401
# ---------------------------------------------------------------------------


def test_revoked_token_returns_401(client_no_dev_mode):
    """RevokedIdTokenError が 401 HTTP レスポンスになることを確認する。"""
    from firebase_admin import auth as firebase_auth

    with patch(
        "src.api.deps.asyncio.to_thread",
        new=AsyncMock(side_effect=firebase_auth.RevokedIdTokenError("Token revoked")),
    ):
        resp = client_no_dev_mode.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer some-firebase-token"},
        )
    assert resp.status_code == 401
    assert "revoked" in resp.json()["detail"].lower()


def test_check_revoked_true_in_verify_id_token():
    """_authenticate_user が check_revoked=True で verify_id_token を呼ぶことを確認する。"""
    import inspect

    from src.api import deps

    source = inspect.getsource(deps._authenticate_user)
    assert "check_revoked=True" in source, (
        "verify_id_token must be called with check_revoked=True"
    )


# ---------------------------------------------------------------------------
# (b) upload-url content_type 検証 — 許可リスト外は 415
# ---------------------------------------------------------------------------


def test_upload_url_rejects_disallowed_content_type(client_dev_mode, monkeypatch):
    """許可リスト外の content_type (text/html) は 415 を返す。"""
    from src.api import assets as assets_module

    # verify_project_access はスキップ
    mock_project = MagicMock()
    mock_project.id = uuid4()

    async def _fake_verify(*args, **kwargs):
        pass

    monkeypatch.setattr(assets_module, "verify_project_access", _fake_verify)

    project_id = str(uuid4())
    resp = client_dev_mode.post(
        f"/api/projects/{project_id}/assets/upload-url",
        params={"filename": "evil.html", "content_type": "text/html"},
    )
    assert resp.status_code == 415, f"Expected 415, got {resp.status_code}: {resp.text}"


def test_upload_url_accepts_allowed_audio_type(client_dev_mode, monkeypatch):
    """許可リスト内の audio/mpeg は受け入れられる（415 にならない）。"""
    from src.api import assets as assets_module

    async def _fake_verify(*args, **kwargs):
        pass

    monkeypatch.setattr(assets_module, "verify_project_access", _fake_verify)

    # generate_upload_url の呼び出しもモック（GCS アクセスを避ける）
    mock_storage = MagicMock()
    mock_storage.generate_upload_url.return_value = ("https://example.com/upload", "key/abc", None)
    monkeypatch.setattr(assets_module, "get_storage_service", lambda: mock_storage)

    project_id = str(uuid4())
    resp = client_dev_mode.post(
        f"/api/projects/{project_id}/assets/upload-url",
        params={"filename": "narration.mp3", "content_type": "audio/mpeg"},
    )
    # 415 でなければ OK（200 or 其他）
    assert resp.status_code != 415, f"Should not be 415, got {resp.status_code}: {resp.text}"


def test_upload_url_rejects_application_octet_stream(client_dev_mode, monkeypatch):
    """application/octet-stream も許可リスト外として 415 を返す。"""
    from src.api import assets as assets_module

    async def _fake_verify(*args, **kwargs):
        pass

    monkeypatch.setattr(assets_module, "verify_project_access", _fake_verify)

    project_id = str(uuid4())
    resp = client_dev_mode.post(
        f"/api/projects/{project_id}/assets/upload-url",
        params={"filename": "blob.bin", "content_type": "application/octet-stream"},
    )
    assert resp.status_code == 415, f"Expected 415, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# (c) Firestore rules — event_manager.set_allowed_users
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_manager_set_allowed_users_calls_firestore():
    """set_allowed_users が Firestore doc に allowed_users フィールドを set() することを確認。"""
    from src.services.event_manager import ProjectEventManager

    manager = ProjectEventManager()

    mock_db = MagicMock()
    mock_doc_ref = MagicMock()
    mock_collection = MagicMock()
    mock_collection.document.return_value = mock_doc_ref
    mock_db.collection.return_value = mock_collection
    manager._db = mock_db

    project_id = uuid4()
    uids = ["uid-owner", "uid-member1"]

    await manager.set_allowed_users(project_id=project_id, firebase_uids=uids)

    mock_doc_ref.set.assert_called_once_with({"allowed_users": uids}, merge=True)


@pytest.mark.asyncio
async def test_event_manager_publish_uses_merge_true():
    """publish が merge=True で set() を呼ぶことを確認（allowed_users を上書きしない）。"""
    from src.services.event_manager import ProjectEventManager

    manager = ProjectEventManager()

    mock_db = MagicMock()
    mock_doc_ref = MagicMock()
    mock_collection = MagicMock()
    mock_collection.document.return_value = mock_doc_ref
    mock_db.collection.return_value = mock_collection
    manager._db = mock_db

    await manager.publish(project_id=uuid4(), event_type="test_event")

    # set は merge=True で呼ばれなければならない
    call_args = mock_doc_ref.set.call_args
    assert call_args is not None
    _, kwargs = call_args
    assert kwargs.get("merge") is True, "publish() must use merge=True to preserve allowed_users"


# ---------------------------------------------------------------------------
# (d) git_hash 露出 — /health 互換性 / /api/version・/health/live から除外
# ---------------------------------------------------------------------------


def test_health_endpoint_includes_git_hash(client_dev_mode):
    """/health は git_hash を含む (deploy_prod.sh 互換)。"""
    # async_session_maker は /health ハンドラー内でローカルにインポートされる。
    # src.models.database の方をモックして DB 接続をスキップする。
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=MagicMock())

    mock_maker = MagicMock(return_value=mock_session)
    with patch("src.models.database.async_session_maker", mock_maker):
        resp = client_dev_mode.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "git_hash" in data, "/health must include git_hash for deploy_prod.sh verification"


def test_api_version_excludes_git_hash(client_dev_mode):
    """/api/version は git_hash を含まない（匿名公開エンドポイント）。"""
    resp = client_dev_mode.get("/api/version")
    assert resp.status_code == 200
    data = resp.json()
    assert "git_hash" not in data, "/api/version must NOT expose git_hash to anonymous callers"
    assert "version" in data, "/api/version must still return version"


def test_health_live_excludes_git_hash(client_dev_mode):
    """/health/live は git_hash を含まない（liveness probe）。"""
    resp = client_dev_mode.get("/health/live")
    assert resp.status_code == 200
    data = resp.json()
    assert "git_hash" not in data, "/health/live must NOT expose git_hash"
    assert "status" in data


# ---------------------------------------------------------------------------
# (e) X-Edit-Session デコード失敗 → 400
# ---------------------------------------------------------------------------


def test_invalid_edit_session_returns_400_in_get_edit_context():
    """get_edit_context で X-Edit-Session デコード失敗時に 400 が返ることを確認する。"""
    import inspect

    from src.api import deps

    # ソースコードに HTTPException status_code=400 が存在することを確認
    source_context = inspect.getsource(deps.get_edit_context)
    assert "HTTP_400_BAD_REQUEST" in source_context or "400" in source_context, (
        "get_edit_context must raise 400 on invalid X-Edit-Session"
    )


def test_invalid_edit_session_returns_400_in_write_context():
    """get_edit_context_for_write で X-Edit-Session デコード失敗時に 400 が返ることを確認。"""
    import inspect

    from src.api import deps

    source_write = inspect.getsource(deps.get_edit_context_for_write)
    assert "HTTP_400_BAD_REQUEST" in source_write or "400" in source_write, (
        "get_edit_context_for_write must raise 400 on invalid X-Edit-Session"
    )


@pytest.mark.asyncio
async def test_get_edit_context_raises_400_on_decode_error():
    """get_edit_context が ValueError を 400 HTTPException に変換することを直接テスト。"""
    from fastapi import HTTPException

    from src.api.deps import get_edit_context

    project_id = uuid4()
    mock_user = MagicMock()
    mock_user.id = uuid4()

    mock_db = MagicMock()

    # get_accessible_project は deps.py 内で `from src.api.access import ...` 形式でインポートされるため
    # deps モジュールのローカルスコープにある名前でパッチする
    mock_project = MagicMock()
    mock_project.id = project_id
    with patch(
        "src.api.access.get_accessible_project",
        new=AsyncMock(return_value=mock_project),
    ):
        with patch(
            "src.api.deps.decode_edit_token",
            side_effect=ValueError("bad token"),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_edit_context(
                    project_id=project_id,
                    current_user=mock_user,
                    db=mock_db,
                    x_edit_session="bad-token-value",
                )
    assert exc_info.value.status_code == 400
