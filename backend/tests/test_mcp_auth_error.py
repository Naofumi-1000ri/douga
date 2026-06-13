"""Unit tests for MCP server HTTP error handling (Issue #319).

401/403/422/5xx などの HTTP エラー時に、AI クライアントが理解できる
実用的なエラーメッセージに変換されることを検証する。
"""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

import src.mcp.server as mcp_server_mod
from src.mcp.server import _build_api_error_message

# =============================================================================
# ヘルパー: 偽の HTTPStatusError を生成
# =============================================================================


def _make_http_status_error(
    status_code: int,
    body: dict | str,
    url: str = "http://localhost:8000/api/test",
) -> httpx.HTTPStatusError:
    """テスト用 httpx.HTTPStatusError を生成する。"""
    if isinstance(body, dict):
        content = json.dumps(body).encode()
    else:
        content = body.encode() if isinstance(body, str) else body

    request = httpx.Request("GET", url)
    response = httpx.Response(
        status_code=status_code,
        content=content,
        request=request,
    )
    return httpx.HTTPStatusError(
        message=f"Client error '{status_code}' for url '{url}'",
        request=request,
        response=response,
    )


# =============================================================================
# _build_api_error_message のユニットテスト
# =============================================================================


def test_401_error_contains_api_key_guidance():
    """401 エラーメッセージに DOUGA_API_KEY の誘導が含まれること。"""
    exc = _make_http_status_error(
        401,
        {"detail": "Authentication required. Use 'X-API-Key: <key>' header for API access."},
    )
    msg = _build_api_error_message(exc, "Bearer")

    assert "DOUGA_API_KEY" in msg
    assert "401" in msg
    # detail がメッセージに含まれること
    assert "Authentication required" in msg


def test_401_error_contains_auth_mode():
    """401 エラーメッセージに認証モードが含まれること。"""
    exc = _make_http_status_error(401, {"detail": "Unauthorized"})

    msg_bearer = _build_api_error_message(exc, "Bearer")
    assert "Bearer" in msg_bearer

    msg_apikey = _build_api_error_message(exc, "X-API-Key")
    assert "X-API-Key" in msg_apikey


def test_401_error_mentions_token_expiry():
    """401 エラーメッセージに Firebase トークン失効の警告が含まれること。"""
    exc = _make_http_status_error(401, {"detail": "Token expired"})
    msg = _build_api_error_message(exc, "Bearer")

    assert "Firebase" in msg or "失効" in msg


def test_403_error_message():
    """403 エラーは権限エラーのメッセージになること。"""
    exc = _make_http_status_error(403, {"detail": "Forbidden resource"})
    msg = _build_api_error_message(exc, "X-API-Key")

    assert "403" in msg
    assert "権限" in msg
    assert "Forbidden resource" in msg


def test_404_error_message():
    """404 エラーはリソース未検出のメッセージになること。"""
    exc = _make_http_status_error(404, {"detail": "Project not found"})
    msg = _build_api_error_message(exc, "X-API-Key")

    assert "404" in msg
    assert "Project not found" in msg


def test_409_error_message():
    """409 エラーは競合エラーのメッセージになること。"""
    exc = _make_http_status_error(409, {"detail": "Sequence is locked by another editor"})
    msg = _build_api_error_message(exc, "X-API-Key")

    assert "409" in msg
    assert "競合" in msg
    assert "Sequence is locked by another editor" in msg


def test_422_error_message():
    """422 エラーはバリデーションエラーのメッセージになること。"""
    exc = _make_http_status_error(422, {"detail": "value is not a valid integer"})
    msg = _build_api_error_message(exc, "X-API-Key")

    assert "422" in msg
    assert "value is not a valid integer" in msg


def test_422_list_detail_truncated_to_200_chars():
    """422 の detail がリスト型（FastAPI 形式）でも 200 文字に切り捨てられること。"""
    # FastAPI バリデーションエラー形式の長い list detail を生成
    long_detail = [
        {
            "loc": ["body", f"field_{i}"],
            "msg": "field required and must satisfy many conditions " * 3,
            "type": "value_error.missing",
        }
        for i in range(10)
    ]
    exc = _make_http_status_error(422, {"detail": long_detail})
    msg = _build_api_error_message(exc, "X-API-Key")

    assert "422" in msg
    # detail 部分が 200 文字に切り詰められていること
    # （メッセージ全体は固定文 + detail(<=200) + URL なので十分短い）
    assert "field_0" in msg  # 先頭部分は含まれる
    assert "field_9" not in msg  # 末尾は切り捨てられている


def test_500_error_message():
    """500 エラーはサーバーエラーのメッセージになること。"""
    exc = _make_http_status_error(500, {"detail": "Internal Server Error"})
    msg = _build_api_error_message(exc, "X-API-Key")

    assert "500" in msg
    assert "Internal Server Error" in msg


def test_non_json_body_handled():
    """JSON でないレスポンスボディでも先頭 200 文字が含まれること。"""
    exc = _make_http_status_error(401, "Plain text error response from server")
    msg = _build_api_error_message(exc, "Bearer")

    assert "401" in msg
    assert "Plain text error response from server" in msg


def test_v1_envelope_error_body_handled():
    """V1 envelope 形式の error.message も MCP エラーに含めること。"""
    exc = _make_http_status_error(
        422,
        {
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "width must be an even number (got 1919)",
            },
            "meta": {"api_version": "1.0"},
        },
    )
    msg = _build_api_error_message(exc, "X-API-Key")

    assert "422" in msg
    assert "VALIDATION_ERROR" in msg
    assert "width must be an even number" in msg


def test_empty_body_handled():
    """空ボディでもエラーにならないこと。"""
    exc = _make_http_status_error(503, "")
    msg = _build_api_error_message(exc, "X-API-Key")

    assert "503" in msg


# =============================================================================
# _call_api の統合テスト: RuntimeError への変換
# =============================================================================


@pytest.mark.asyncio
async def test_call_api_converts_401_to_runtime_error():
    """_call_api が 401 を RuntimeError に変換し、DOUGA_API_KEY 誘導を含むこと。"""
    detail_text = "Authentication required. Use 'X-API-Key: <key>' header for API access."
    fake_request = httpx.Request("GET", "http://localhost:8000/api/ai/project/test/overview")
    fake_response = httpx.Response(
        status_code=401,
        content=json.dumps({"detail": detail_text}).encode(),
        request=fake_request,
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=fake_response)

    with patch("src.mcp.server.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError) as exc_info:
            await mcp_server_mod._call_api("GET", "/api/ai/project/test/overview")

    error_msg = str(exc_info.value)
    assert "DOUGA_API_KEY" in error_msg, f"DOUGA_API_KEY が含まれていない: {error_msg}"
    assert "401" in error_msg, f"401 が含まれていない: {error_msg}"
    # detail テキストがメッセージに含まれること
    assert "Authentication required" in error_msg or "X-API-Key" in error_msg, (
        f"detail が含まれていない: {error_msg}"
    )


@pytest.mark.asyncio
async def test_call_api_converts_403_to_runtime_error():
    """_call_api が 403 を RuntimeError に変換すること。"""
    fake_request = httpx.Request("GET", "http://localhost:8000/api/test")
    fake_response = httpx.Response(
        status_code=403,
        content=json.dumps({"detail": "Access denied"}).encode(),
        request=fake_request,
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=fake_response)

    with patch("src.mcp.server.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError) as exc_info:
            await mcp_server_mod._call_api("GET", "/api/test")

    assert "403" in str(exc_info.value)
    assert "Access denied" in str(exc_info.value)


@pytest.mark.asyncio
async def test_call_api_converts_transport_error_to_runtime_error():
    """_call_api が通信失敗を空でない RuntimeError に変換すること。"""
    fake_request = httpx.Request("GET", "http://localhost:8000/api/test")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("", request=fake_request))

    with patch("src.mcp.server.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError) as exc_info:
            await mcp_server_mod._call_api("GET", "/api/test")

    error_msg = str(exc_info.value)
    assert error_msg
    assert "douga API 接続エラー" in error_msg
    assert "GET" in error_msg
    assert "/api/test" in error_msg
    assert "HTTP レスポンスは返っていません" in error_msg


@pytest.mark.asyncio
async def test_call_api_v1_write_converts_transport_error_to_runtime_error():
    """_call_api_v1_write も通信失敗を具体的な RuntimeError に変換すること。"""
    fake_request = httpx.Request("POST", "http://localhost:8000/api/ai/v1/projects")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=httpx.ReadError("", request=fake_request))

    with patch("src.mcp.server.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError) as exc_info:
            await mcp_server_mod._call_api_v1_write(
                "POST",
                "/api/ai/v1/projects",
                {"name": "MCP test project"},
            )

    error_msg = str(exc_info.value)
    assert error_msg
    assert "douga API 接続エラー" in error_msg
    assert "POST" in error_msg
    assert "/api/ai/v1/projects" in error_msg


@pytest.mark.asyncio
async def test_call_api_does_not_leak_raw_httpstatuserror():
    """_call_api が HTTPStatusError をそのまま漏らさないこと。"""
    fake_request = httpx.Request("GET", "http://localhost:8000/api/test")
    fake_response = httpx.Response(
        status_code=401,
        content=b'{"detail": "Unauthorized"}',
        request=fake_request,
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=fake_response)

    with patch("src.mcp.server.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError):
            await mcp_server_mod._call_api("GET", "/api/test")
        # HTTPStatusError が漏れないことを確認（pytest.raises が RuntimeError を期待）


# =============================================================================
# _upload_files の統合テスト: RuntimeError への変換
# =============================================================================


@pytest.mark.asyncio
async def test_upload_files_converts_401_to_runtime_error(tmp_path):
    """_upload_files が 401 を RuntimeError に変換し、DOUGA_API_KEY 誘導を含むこと。"""
    # アップロード対象のダミーファイルを作成
    dummy_file = tmp_path / "test_video.mp4"
    dummy_file.write_bytes(b"fake video content")

    detail_text = "Authentication required. Use 'X-API-Key: <key>' header for API access."
    fake_request = httpx.Request(
        "POST", "http://localhost:8000/api/ai-video/projects/test/assets/batch-upload"
    )
    fake_response = httpx.Response(
        status_code=401,
        content=json.dumps({"detail": detail_text}).encode(),
        request=fake_request,
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=fake_response)

    with patch("src.mcp.server.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(RuntimeError) as exc_info:
            await mcp_server_mod._upload_files(
                "/api/ai-video/projects/test/assets/batch-upload",
                [str(dummy_file)],
            )

    error_msg = str(exc_info.value)
    assert "DOUGA_API_KEY" in error_msg, f"DOUGA_API_KEY が含まれていない: {error_msg}"
    assert "401" in error_msg, f"401 が含まれていない: {error_msg}"
    assert "Authentication required" in error_msg, f"detail が含まれていない: {error_msg}"
