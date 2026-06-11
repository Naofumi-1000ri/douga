"""
Regression tests: dev_mode=False のとき認証バイパスが無効になることを確認。

- settings.dev_mode=False に monkeypatch で上書きし、lru_cache を貫通させる。
- get_db をモックして DB 接続なしで起動できるようにする。
- init_db は Issue #282 で削除済み（lifespan から起動時マイグレーションを廃止）。
- これらのテストは requires_db マーカーを付けない (pure auth check)。
"""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.api import deps as deps_module
from src.main import app
from src.models.database import get_db


async def _fake_db():
    """DB 依存性のダミー。dev_mode=False の場合、DB に到達する前に 401 が上がるため
    実際には呼ばれないが、override しておかないと DB 接続が試みられる。"""
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock())
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    yield session


@pytest.fixture
def client_no_dev_mode(monkeypatch):
    """settings.dev_mode=False に上書きした TestClient を返す。

    lifespan は起動時 DDL を実行しないため、DB モックなしで TestClient を起動できる。
    """
    patched = deepcopy(deps_module.settings)
    patched.dev_mode = False
    monkeypatch.setattr(deps_module, "settings", patched)

    app.dependency_overrides[get_db] = _fake_db
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_auth_required_when_dev_mode_false(client_no_dev_mode):
    """`dev_mode=False` のとき、認証ヘッダなしで /api/auth/me を叩くと 401 が返る。"""
    resp = client_no_dev_mode.get("/api/auth/me")
    assert resp.status_code == 401


def test_dev_token_rejected_when_dev_mode_false(client_no_dev_mode):
    """`dev_mode=False` のとき、dev-token は無効で 401 が返る。"""
    resp = client_no_dev_mode.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer dev-token"},
    )
    assert resp.status_code == 401
