"""MCP Server for Douga Video Editor.

FastMCPベースのMCPサーバー。AIアシスタントに動画編集ツールを提供します。

アーキテクチャ:
    AIアシスタント → MCP Server (:6500) → Douga Backend API

階層的データアクセス:
    L1 (Summary):   ~300 tokens - プロジェクト概要
    L2 (Structure): ~800 tokens - タイムライン構造、アセット一覧
    L3 (Details):   ~400 tokens/clip - クリップ詳細

ツールカテゴリ:
    - Read Tools: L1/L2/L3のデータ取得
    - Write Tools: レイヤー、クリップの編集操作
    - Semantic Tools: 高レベル編集（スナップ、ギャップ閉じ等）
    - Analysis Tools: ギャップ検出、ペーシング分析
    - AI Video Tools: フルワークフロー動画制作

起動方法:
    # スタンドアロン
    python -m src.mcp.server

    # MCP CLI
    mcp run src.mcp.server:mcp_server

環境変数:
    DOUGA_API_URL: バックエンドURL (default: http://localhost:8000)
    DOUGA_API_KEY: API認証キー (推奨)
    DOUGA_API_TOKEN: Firebaseトークン (レガシー)

依存関係:
    pip install mcp[cli] httpx
"""

import logging
import os
import uuid
from typing import Any

import httpx

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    # MCP not installed - provide a stub for import compatibility
    class FastMCP:
        """Stub FastMCP class for when mcp package is not installed."""

        def __init__(self, name: str = "", instructions: str = ""):
            self.name = name
            self.instructions = instructions
            self.app = None

        def tool(self):
            """Decorator that does nothing when mcp is not installed."""

            def decorator(func):
                return func

            return decorator

    import warnings

    warnings.warn(
        "MCP package not installed. Install with: pip install mcp[cli] httpx",
        ImportWarning,
    )

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server instance
mcp_server = FastMCP(
    name="Douga Video Editor",
    instructions="AI tools for video editing with hierarchical data access (L1→L2→L3)",
)

# Backend API configuration
API_BASE_URL = os.environ.get("DOUGA_API_URL", "http://localhost:8000")

# API authentication: prefer API key, fall back to token
# DOUGA_API_KEY: Long-lived API key (recommended for MCP)
# DOUGA_API_TOKEN: Firebase token or dev-token (legacy)
API_KEY = os.environ.get("DOUGA_API_KEY")
API_TOKEN = os.environ.get("DOUGA_API_TOKEN", "dev-token")

# モジュール読み込み時に認証設定を警告
if not API_KEY:
    _token_desc = "未設定（dev-token フォールバック）"
    if API_TOKEN and API_TOKEN != "dev-token":
        if API_TOKEN.startswith("eyJ"):
            _token_desc = "JWT トークン（Firebase）"
        else:
            _token_desc = "カスタムトークン"
    logger.warning(
        "DOUGA_API_KEY が未設定です。現在の認証: DOUGA_API_TOKEN (%s)。"
        "Firebase トークンは約1時間で失効します。"
        "長期利用には douga_sk_... 形式の DOUGA_API_KEY の設定を強く推奨します。",
        _token_desc,
    )


# =============================================================================
# Helper: API Client
# =============================================================================


def _build_api_error_message(exc: httpx.HTTPStatusError, auth_mode: str) -> str:
    """HTTPStatusError から AI クライアントが理解できるエラーメッセージを生成する。

    Args:
        exc: httpx が raise した HTTPStatusError
        auth_mode: 認証モード文字列（"X-API-Key" または "Bearer"）

    Returns:
        人間・AI が読めるエラーメッセージ文字列
    """
    status_code = exc.response.status_code
    url = str(exc.request.url)

    # レスポンスボディから detail を抽出（FastAPI の 422 では detail が list になるため
    # 型を問わず str() 化してから 200 文字に切り捨てる）
    detail_text = ""
    try:
        body = exc.response.json()
        if isinstance(body, dict):
            detail_text = str(body.get("detail", ""))[:200]
        else:
            detail_text = str(body)[:200]
    except Exception:
        raw = exc.response.text
        detail_text = raw[:200] if raw else ""

    if status_code == 401:
        return (
            f"douga API 認証エラー (401)。"
            f"認証モード: {auth_mode}。"
            "MCP サーバーの環境変数に DOUGA_API_KEY（douga_sk_... 形式の API キー）を設定してください。"
            "キーはアプリの Settings > API Keys で発行できます。"
            "現在 DOUGA_API_KEY が未設定の場合、DOUGA_API_TOKEN (Bearer) フォールバックで動作しており、"
            "Firebase トークンは約1時間で失効します。"
            + (f" サーバー応答: {detail_text}" if detail_text else "")
            + f" (URL: {url})"
        )
    elif status_code == 403:
        return (
            f"douga API 権限エラー (403)。このリソースへのアクセス権限がありません。"
            f"認証モード: {auth_mode}。"
            + (f" サーバー応答: {detail_text}" if detail_text else "")
            + f" (URL: {url})"
        )
    elif status_code == 404:
        return (
            "douga API リソース未検出エラー (404)。指定されたリソースが存在しません。"
            + (f" サーバー応答: {detail_text}" if detail_text else "")
            + f" (URL: {url})"
        )
    elif status_code == 409:
        return (
            "douga API 競合エラー (409)。リソースが競合しています。"
            + (f" サーバー応答: {detail_text}" if detail_text else "")
            + f" (URL: {url})"
        )
    elif status_code == 422:
        return (
            "douga API バリデーションエラー (422)。リクエストのパラメータが不正です。"
            + (f" サーバー応答: {detail_text}" if detail_text else "")
            + f" (URL: {url})"
        )
    elif status_code >= 500:
        return (
            f"douga API サーバーエラー ({status_code})。バックエンドで内部エラーが発生しました。"
            + (f" サーバー応答: {detail_text}" if detail_text else "")
            + f" (URL: {url})"
        )
    else:
        return (
            f"douga API エラー ({status_code})。"
            + (f" サーバー応答: {detail_text}" if detail_text else "")
            + f" (URL: {url})"
        )


def _build_auth_headers() -> tuple[dict[str, str], str]:
    """認証ヘッダーと認証モードを構築して返す。

    Returns:
        (headers dict, auth_mode string) のタプル
    """
    if API_KEY:
        return {"X-API-Key": API_KEY}, "X-API-Key"
    return {"Authorization": f"Bearer {API_TOKEN}"}, "Bearer"


async def _call_api(
    method: str, endpoint: str, data: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Douga Backend APIを呼び出す。

    DOUGA_API_KEYが設定されている場合はX-API-Keyヘッダーで認証、
    そうでなければDOUGA_API_TOKENをBearerトークンとして使用。

    Args:
        method: HTTPメソッド (GET, POST, PATCH, PUT, DELETE)
        endpoint: APIエンドポイントパス (例: /api/ai/v1/projects/{id}/overview)
        data: POST/PATCH/PUTリクエストのボディ

    Returns:
        APIからのJSONレスポンス

    Raises:
        RuntimeError: HTTP エラーレスポンスの場合（ステータスコードと detail を含む）
        ValueError: サポートされていないHTTPメソッドの場合
    """
    url = f"{API_BASE_URL}{endpoint}"
    headers, auth_mode = _build_auth_headers()

    async with httpx.AsyncClient() as client:
        if method == "GET":
            response = await client.get(url, headers=headers)
        elif method == "POST":
            response = await client.post(url, headers=headers, json=data)
        elif method == "PATCH":
            response = await client.patch(url, headers=headers, json=data)
        elif method == "PUT":
            response = await client.put(url, headers=headers, json=data)
        elif method == "DELETE":
            response = await client.delete(url, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(_build_api_error_message(exc, auth_mode)) from exc

        return response.json() if response.content else {}


async def _call_api_v1_write(
    method: str,
    endpoint: str,
    data: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """V1 API の書き込みエンドポイントを呼び出す。

    - ベースパス: /api/ai/v1/...
    - Idempotency-Key ヘッダーを自動付与（指定がなければ UUID を自動生成）
    - V1 Envelope レスポンス形式（{"data": ..., "meta": ...}）をアンラップして返す

    Args:
        method: HTTPメソッド (POST, PATCH, PUT, DELETE)
        endpoint: V1 APIエンドポイントパス (例: /api/ai/v1/projects/{id}/clips)
        data: リクエストボディ
        idempotency_key: Idempotency-Key（省略時は UUID4 を自動生成）

    Returns:
        APIからのJSONレスポンス（Envelopeアンラップ済み）

    Raises:
        RuntimeError: HTTP エラーレスポンスの場合
        ValueError: サポートされていないHTTPメソッドの場合
    """
    if idempotency_key is None:
        idempotency_key = str(uuid.uuid4())

    url = f"{API_BASE_URL}{endpoint}"
    headers, auth_mode = _build_auth_headers()
    headers["Idempotency-Key"] = idempotency_key

    async with httpx.AsyncClient() as client:
        if method == "POST":
            response = await client.post(url, headers=headers, json=data)
        elif method == "PATCH":
            response = await client.patch(url, headers=headers, json=data)
        elif method == "PUT":
            response = await client.put(url, headers=headers, json=data)
        elif method == "DELETE":
            if data is None:
                response = await client.delete(url, headers=headers)
            else:
                response = await client.request("DELETE", url, headers=headers, json=data)
        else:
            raise ValueError(f"Unsupported method for V1 write: {method}")

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(_build_api_error_message(exc, auth_mode)) from exc

        if not response.content:
            return {}

        result = response.json()
        # V1 Envelope レスポンス {"data": ..., "meta": ..., "ok": true} をアンラップ
        if isinstance(result, dict) and "data" in result:
            return result["data"]  # type: ignore[no-any-return]
        return result  # type: ignore[return-value]


async def _upload_files(
    endpoint: str, file_paths: list[str], timeout: float = 120.0
) -> dict[str, Any]:
    """マルチパートフォームでファイルをアップロードする。

    ローカルファイルをDouga Backend APIにアップロード。
    MIMEタイプは拡張子から自動判定。

    Args:
        endpoint: APIエンドポイントパス
        file_paths: アップロードするローカルファイルパスのリスト
        timeout: リクエストタイムアウト（秒）。デフォルト120秒

    Returns:
        APIからのJSONレスポンス

    Raises:
        RuntimeError: HTTPエラーレスポンスの場合（ステータスコードと detail を含む）
        FileNotFoundError: ファイルが存在しない場合
    """
    import mimetypes
    from pathlib import Path

    if API_KEY:
        headers = {"X-API-Key": API_KEY}
        auth_mode = "X-API-Key"
    else:
        headers = {"Authorization": f"Bearer {API_TOKEN}"}
        auth_mode = "Bearer"

    files = []
    opened = []
    try:
        for path_str in file_paths:
            p = Path(path_str)
            mime = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
            f = open(str(p), "rb")
            opened.append(f)
            files.append(("files", (p.name, f, mime)))

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{API_BASE_URL}{endpoint}", headers=headers, files=files)
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise RuntimeError(_build_api_error_message(exc, auth_mode)) from exc
            return resp.json()
    finally:
        for f in opened:
            f.close()


# =============================================================================
# L1: Summary Level Tools
# =============================================================================


@mcp_server.tool()
async def get_project_overview(project_id: str) -> str:
    """Get L1 project overview (~300 tokens).

    Start here to understand the project scope.

    Returns:
        Project metadata, layer/track counts, asset counts, last modified time.
    """
    result = await _call_api("GET", f"/api/ai/v1/projects/{project_id}/overview")
    return _format_response(result)


# =============================================================================
# L2: Structure Level Tools
# =============================================================================


@mcp_server.tool()
async def get_timeline_structure(project_id: str) -> str:
    """Get L2 timeline structure (~800 tokens).

    Shows layer and track organization with time coverage.
    Use this to find which layer/track to work with.

    Returns:
        Layers (id, name, type, clip_count, time_coverage, visible, locked)
        Audio tracks (id, name, type, clip_count, time_coverage, volume, muted)
    """
    result = await _call_api("GET", f"/api/ai/v1/projects/{project_id}/structure")
    return _format_response(result)


@mcp_server.tool()
async def get_timeline_at_time(project_id: str, time_ms: int) -> str:
    """Get L2 timeline state at a specific time.

    Shows what clips are active at the given timestamp.

    Args:
        project_id: Project UUID
        time_ms: Timestamp in milliseconds

    Returns:
        Active clips at the specified time, next event time.
    """
    result = await _call_api("GET", f"/api/ai/v1/projects/{project_id}/at-time/{time_ms}")
    return _format_response(result)


@mcp_server.tool()
async def get_asset_catalog(project_id: str) -> str:
    """Get L2 asset catalog.

    Lists available assets with usage counts.
    Use to find asset IDs for adding new clips.

    Returns:
        Assets (id, name, type, subtype, duration_ms, dimensions, usage_count)
    """
    result = await _call_api("GET", f"/api/ai/v1/projects/{project_id}/assets")
    return _format_response(result)


# =============================================================================
# L3: Details Level Tools
# =============================================================================


@mcp_server.tool()
async def get_clip_details(project_id: str, clip_id: str) -> str:
    """Get L3 video clip details (~400 tokens).

    Full clip properties with neighboring context.

    Args:
        project_id: Project UUID
        clip_id: Clip UUID

    Returns:
        Clip timing, transform, effects, transitions, text content,
        previous/next clip info with gap.
    """
    result = await _call_api("GET", f"/api/ai/v1/projects/{project_id}/clips/{clip_id}")
    return _format_response(result)


@mcp_server.tool()
async def get_audio_clip_details(project_id: str, clip_id: str) -> str:
    """Get L3 audio clip details.

    Full audio clip properties with neighboring context.

    Args:
        project_id: Project UUID
        clip_id: Audio clip UUID

    Returns:
        Clip timing, volume, fades, previous/next clip info with gap.
    """
    result = await _call_api("GET", f"/api/ai/v1/projects/{project_id}/audio-clips/{clip_id}")
    return _format_response(result)


# =============================================================================
# Write Tools: Layers
# =============================================================================


@mcp_server.tool()
async def add_layer(
    project_id: str,
    name: str,
    layer_type: str = "content",
    insert_at: int | None = None,
) -> str:
    """Create a new layer.

    Args:
        project_id: Project UUID
        name: Layer name
        layer_type: Layer type (background, content, avatar, effects, text)
        insert_at: Insert position (0=top, None=bottom)

    Returns:
        Created layer summary
    """
    layer: dict[str, Any] = {"name": name, "type": layer_type}
    if insert_at is not None:
        layer["insert_at"] = insert_at

    # V1 AddLayerV1Request requires the layer body nested under "layer".
    data: dict[str, Any] = {"layer": layer}
    result = await _call_api_v1_write("POST", f"/api/ai/v1/projects/{project_id}/layers", data)
    return _format_response(result)


@mcp_server.tool()
async def reorder_layers(project_id: str, layer_ids: list[str]) -> str:
    """Reorder layers by providing the new order of layer IDs.

    Args:
        project_id: Project UUID
        layer_ids: Layer IDs in new order (top to bottom)

    Returns:
        Updated layer summaries in new order
    """
    # V1 ReorderLayersV1Request requires the body nested under "order".
    data: dict[str, Any] = {"order": {"layer_ids": layer_ids}}
    result = await _call_api_v1_write("PUT", f"/api/ai/v1/projects/{project_id}/layers/order", data)
    return _format_response(result)


@mcp_server.tool()
async def update_layer(
    project_id: str,
    layer_id: str,
    name: str | None = None,
    visible: bool | None = None,
    locked: bool | None = None,
) -> str:
    """Update layer properties (name, visibility, locked status).

    Args:
        project_id: Project UUID
        layer_id: Layer ID (from L2 structure)
        name: New layer name
        visible: Layer visibility
        locked: Lock layer from editing

    Returns:
        Updated layer summary
    """
    layer: dict[str, Any] = {}
    if name is not None:
        layer["name"] = name
    if visible is not None:
        layer["visible"] = visible
    if locked is not None:
        layer["locked"] = locked

    # V1 UpdateLayerV1Request requires the layer body nested under "layer".
    data: dict[str, Any] = {"layer": layer}
    result = await _call_api_v1_write(
        "PATCH", f"/api/ai/v1/projects/{project_id}/layers/{layer_id}", data
    )
    return _format_response(result)


# =============================================================================
# Write Tools: Video Clips
# =============================================================================


@mcp_server.tool()
async def add_clip(
    project_id: str,
    layer_id: str,
    start_ms: int,
    duration_ms: int,
    asset_id: str | None = None,
    x: float | None = None,
    y: float | None = None,
    scale: float | None = None,
    text_content: str | None = None,
) -> str:
    """Add a new video clip to a layer.

    Args:
        project_id: Project UUID
        layer_id: Target layer ID (from L2 structure)
        start_ms: Timeline position in milliseconds (>= 0)
        duration_ms: Clip duration in milliseconds (> 0, max 1 hour)
        asset_id: Asset UUID (optional for text clips)
        x: X position (-3840 to 3840)
        y: Y position (-2160 to 2160)
        scale: Scale factor (0.01 to 10.0)
        text_content: Text content for text clips

    Returns:
        Created clip details (L3)
    """
    data: dict[str, Any] = {
        "layer_id": layer_id,
        "start_ms": start_ms,
        "duration_ms": duration_ms,
    }
    if asset_id:
        data["asset_id"] = asset_id
    if x is not None:
        data["x"] = x
    if y is not None:
        data["y"] = y
    if scale is not None:
        data["scale"] = scale
    if text_content is not None:
        data["text_content"] = text_content

    result = await _call_api_v1_write("POST", f"/api/ai/v1/projects/{project_id}/clips", data)
    return _format_response(result)


@mcp_server.tool()
async def move_clip(
    project_id: str,
    clip_id: str,
    new_start_ms: int,
    new_layer_id: str | None = None,
) -> str:
    """Move a video clip to a new position or layer.

    Args:
        project_id: Project UUID
        clip_id: Clip to move
        new_start_ms: New timeline position in milliseconds
        new_layer_id: Target layer ID (if changing layers)

    Returns:
        Updated clip details (L3)
    """
    data: dict[str, Any] = {"new_start_ms": new_start_ms}
    if new_layer_id:
        data["new_layer_id"] = new_layer_id

    result = await _call_api_v1_write(
        "PATCH", f"/api/ai/v1/projects/{project_id}/clips/{clip_id}/move", data
    )
    return _format_response(result)


@mcp_server.tool()
async def update_clip_transform(
    project_id: str,
    clip_id: str,
    x: float | None = None,
    y: float | None = None,
    scale: float | None = None,
    rotation: float | None = None,
) -> str:
    """Update clip transform (position, scale, rotation).

    Args:
        project_id: Project UUID
        clip_id: Clip to update
        x: X position (-3840 to 3840)
        y: Y position (-2160 to 2160)
        scale: Scale factor (0.01 to 10.0)
        rotation: Rotation in degrees (-360 to 360)

    Returns:
        Updated clip details (L3)
    """
    data: dict[str, Any] = {}
    if x is not None:
        data["x"] = x
    if y is not None:
        data["y"] = y
    if scale is not None:
        data["scale"] = scale
    if rotation is not None:
        data["rotation"] = rotation

    result = await _call_api_v1_write(
        "PATCH", f"/api/ai/v1/projects/{project_id}/clips/{clip_id}/transform", data
    )
    return _format_response(result)


@mcp_server.tool()
async def update_clip_effects(
    project_id: str,
    clip_id: str,
    opacity: float | None = None,
    chroma_key_enabled: bool | None = None,
    chroma_key_color: str | None = None,
) -> str:
    """Update clip effects (opacity, chroma key).

    Args:
        project_id: Project UUID
        clip_id: Clip to update
        opacity: Opacity (0.0 to 1.0)
        chroma_key_enabled: Enable green screen removal
        chroma_key_color: Key color in hex (e.g., "#00FF00")

    Returns:
        Updated clip details (L3)
    """
    data: dict[str, Any] = {}
    if opacity is not None:
        data["opacity"] = opacity
    if chroma_key_enabled is not None:
        data["chroma_key_enabled"] = chroma_key_enabled
    if chroma_key_color is not None:
        data["chroma_key_color"] = chroma_key_color

    result = await _call_api_v1_write(
        "PATCH", f"/api/ai/v1/projects/{project_id}/clips/{clip_id}/effects", data
    )
    return _format_response(result)


@mcp_server.tool()
async def delete_clip(project_id: str, clip_id: str) -> str:
    """Delete a video clip.

    Args:
        project_id: Project UUID
        clip_id: Clip to delete

    Returns:
        Success confirmation
    """
    await _call_api_v1_write("DELETE", f"/api/ai/v1/projects/{project_id}/clips/{clip_id}")
    return "Clip deleted successfully"


# =============================================================================
# Write Tools: Audio Clips
# =============================================================================


@mcp_server.tool()
async def add_audio_clip(
    project_id: str,
    track_id: str,
    asset_id: str,
    start_ms: int,
    duration_ms: int,
    volume: float = 1.0,
    fade_in_ms: int = 0,
    fade_out_ms: int = 0,
) -> str:
    """Add a new audio clip to a track.

    Args:
        project_id: Project UUID
        track_id: Target track ID (from L2 structure)
        asset_id: Audio asset UUID
        start_ms: Timeline position in milliseconds
        duration_ms: Clip duration in milliseconds
        volume: Volume level (0.0 to 2.0)
        fade_in_ms: Fade in duration
        fade_out_ms: Fade out duration

    Returns:
        Created audio clip details (L3)
    """
    # V1 AddAudioClipV1Request requires the clip body nested under "clip".
    data: dict[str, Any] = {
        "clip": {
            "track_id": track_id,
            "asset_id": asset_id,
            "start_ms": start_ms,
            "duration_ms": duration_ms,
            "volume": volume,
            "fade_in_ms": fade_in_ms,
            "fade_out_ms": fade_out_ms,
        }
    }

    result = await _call_api_v1_write("POST", f"/api/ai/v1/projects/{project_id}/audio-clips", data)
    return _format_response(result)


@mcp_server.tool()
async def move_audio_clip(
    project_id: str,
    clip_id: str,
    new_start_ms: int,
    new_track_id: str | None = None,
) -> str:
    """Move an audio clip to a new position or track.

    Args:
        project_id: Project UUID
        clip_id: Audio clip to move
        new_start_ms: New timeline position
        new_track_id: Target track ID (if changing tracks)

    Returns:
        Updated audio clip details (L3)
    """
    data: dict[str, Any] = {"new_start_ms": new_start_ms}
    if new_track_id:
        data["new_track_id"] = new_track_id

    result = await _call_api_v1_write(
        "PATCH", f"/api/ai/v1/projects/{project_id}/audio-clips/{clip_id}/move", data
    )
    return _format_response(result)


@mcp_server.tool()
async def delete_audio_clip(project_id: str, clip_id: str) -> str:
    """Delete an audio clip.

    Args:
        project_id: Project UUID
        clip_id: Audio clip to delete

    Returns:
        Success confirmation
    """
    await _call_api_v1_write("DELETE", f"/api/ai/v1/projects/{project_id}/audio-clips/{clip_id}")
    return "Audio clip deleted successfully"


# =============================================================================
# Semantic Operations
# =============================================================================


@mcp_server.tool()
async def snap_to_previous(project_id: str, target_clip_id: str) -> str:
    """Snap a clip to the end of the previous clip (close the gap).

    Args:
        project_id: Project UUID
        target_clip_id: Clip to snap

    Returns:
        Operation result with changes made
    """
    # V1 SemanticOperationV1Request requires the operation nested under "semantic".
    data: dict[str, Any] = {
        "semantic": {
            "operation": "snap_to_previous",
            "target_clip_id": target_clip_id,
        }
    }
    result = await _call_api_v1_write("POST", f"/api/ai/v1/projects/{project_id}/semantic", data)
    return _format_response(result)


@mcp_server.tool()
async def snap_to_next(project_id: str, target_clip_id: str) -> str:
    """Snap the next clip to the end of this clip.

    Args:
        project_id: Project UUID
        target_clip_id: Reference clip

    Returns:
        Operation result with changes made
    """
    # V1 SemanticOperationV1Request requires the operation nested under "semantic".
    data: dict[str, Any] = {
        "semantic": {
            "operation": "snap_to_next",
            "target_clip_id": target_clip_id,
        }
    }
    result = await _call_api_v1_write("POST", f"/api/ai/v1/projects/{project_id}/semantic", data)
    return _format_response(result)


@mcp_server.tool()
async def close_gap(project_id: str, target_layer_id: str) -> str:
    """Close all gaps in a layer by shifting clips forward.

    Args:
        project_id: Project UUID
        target_layer_id: Layer to process

    Returns:
        Operation result with changes made
    """
    # V1 SemanticOperationV1Request requires the operation nested under "semantic".
    data: dict[str, Any] = {
        "semantic": {
            "operation": "close_gap",
            "target_layer_id": target_layer_id,
        }
    }
    result = await _call_api_v1_write("POST", f"/api/ai/v1/projects/{project_id}/semantic", data)
    return _format_response(result)


@mcp_server.tool()
async def rename_layer(
    project_id: str,
    layer_id: str,
    new_name: str,
) -> str:
    """Rename a layer (change its display name).

    Use update_layer for general property changes (name, visibility, locked).
    This is a convenience tool specifically for renaming.

    Args:
        project_id: Project UUID
        layer_id: Layer ID (from L2 structure)
        new_name: New layer name

    Returns:
        Operation result with changes made
    """
    # V1 SemanticOperationV1Request requires the operation nested under "semantic".
    data: dict[str, Any] = {
        "semantic": {
            "operation": "rename_layer",
            "target_layer_id": layer_id,
            "parameters": {
                "name": new_name,
            },
        }
    }
    result = await _call_api_v1_write("POST", f"/api/ai/v1/projects/{project_id}/semantic", data)
    return _format_response(result)


# =============================================================================
# Analysis Tools
# =============================================================================


@mcp_server.tool()
async def analyze_gaps(project_id: str) -> str:
    """Find gaps in the timeline.

    Args:
        project_id: Project UUID

    Returns:
        Total gaps, total gap duration, list of gaps with location and size
    """
    result = await _call_api("GET", f"/api/ai/v1/projects/{project_id}/analysis/gaps")
    return _format_response(result)


@mcp_server.tool()
async def analyze_pacing(project_id: str, segment_duration_ms: int = 30000) -> str:
    """Analyze timeline pacing (clip density over time).

    Args:
        project_id: Project UUID
        segment_duration_ms: Duration of each analysis segment

    Returns:
        Overall average, per-segment analysis, improvement suggestions
    """
    result = await _call_api(
        "GET",
        f"/api/ai/v1/projects/{project_id}/analysis/pacing?segment_duration_ms={segment_duration_ms}",
    )
    return _format_response(result)


# =============================================================================
# Preview and Inspection Tools
# =============================================================================


@mcp_server.tool()
async def get_event_points(
    project_id: str,
    include_audio: bool = True,
    include_visual: bool = True,
    min_gap_ms: int = 500,
) -> str:
    """Detect key timeline event points for targeted inspection.

    Args:
        project_id: Project UUID
        include_audio: Include audio events such as narration, BGM, and SE
        include_visual: Include visual events such as clip boundaries and layer changes
        min_gap_ms: Minimum silence gap to detect in milliseconds

    Returns:
        Event points with time_ms, event_type, description, and related metadata
    """
    data: dict[str, Any] = {
        "include_audio": include_audio,
        "include_visual": include_visual,
        "min_gap_ms": min_gap_ms,
    }
    result = await _call_api("POST", f"/api/projects/{project_id}/preview/event-points", data)
    return _format_response(result)


@mcp_server.tool()
async def sample_frame(
    project_id: str,
    time_ms: int,
    resolution: str = "640x360",
) -> str:
    """Render a preview frame at a specific timeline position.

    Args:
        project_id: Project UUID
        time_ms: Timestamp to sample in milliseconds
        resolution: Output resolution, e.g. "640x360" or "320x180"

    Returns:
        Rendered frame data including Base64 JPEG and active clip metadata
    """
    data: dict[str, Any] = {"time_ms": time_ms, "resolution": resolution}
    result = await _call_api("POST", f"/api/projects/{project_id}/preview/sample-frame", data)
    return _format_response(result)


@mcp_server.tool()
async def sample_event_points(
    project_id: str,
    max_samples: int = 10,
    resolution: str = "640x360",
    include_audio: bool = True,
    min_gap_ms: int = 500,
) -> str:
    """Detect event points and render preview frames for selected moments.

    Args:
        project_id: Project UUID
        max_samples: Maximum number of event frames to sample
        resolution: Output resolution, e.g. "640x360"
        include_audio: Include audio events during event detection
        min_gap_ms: Minimum silence gap to detect in milliseconds

    Returns:
        Sampled event points with descriptions, Base64 frames, and active clips
    """
    data: dict[str, Any] = {
        "max_samples": max_samples,
        "resolution": resolution,
        "include_audio": include_audio,
        "min_gap_ms": min_gap_ms,
    }
    result = await _call_api(
        "POST",
        f"/api/projects/{project_id}/preview/sample-event-points",
        data,
    )
    return _format_response(result)


@mcp_server.tool()
async def validate_composition(
    project_id: str,
    rules: list[str] | None = None,
) -> str:
    """Validate composition rules without rendering.

    Args:
        project_id: Project UUID
        rules: Optional list of rule names to check; None runs all rules

    Returns:
        Validation result with is_valid, issues, errors, and warnings
    """
    result = await _call_api(
        "POST",
        f"/api/projects/{project_id}/preview/validate",
        {"rules": rules},
    )
    return _format_response(result)


# =============================================================================
# AI Video Production Tools
# =============================================================================


@mcp_server.tool()
async def scan_folder(path: str) -> str:
    """Scan a local folder for media files usable in video production.

    Returns file list with names, paths, sizes, and MIME types.
    Supported formats: video (.mp4,.mov,.avi,.webm), audio (.mp3,.wav,.aac,.ogg,.m4a),
    image (.png,.jpg,.jpeg,.gif,.webp).

    Args:
        path: Absolute path to local folder to scan

    Returns:
        JSON with folder path, total file count, and file details
    """
    import json
    import mimetypes
    from pathlib import Path

    folder = Path(path)
    if not folder.exists():
        return json.dumps({"error": f"Folder not found: {path}"}, ensure_ascii=False)
    if not folder.is_dir():
        return json.dumps({"error": f"Not a directory: {path}"}, ensure_ascii=False)

    supported_extensions = {
        ".mp4",
        ".mov",
        ".avi",
        ".webm",
        ".mp3",
        ".wav",
        ".aac",
        ".ogg",
        ".m4a",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
    }

    files = []
    for f in sorted(folder.iterdir()):
        if f.is_file() and f.suffix.lower() in supported_extensions:
            mime = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
            files.append(
                {
                    "name": f.name,
                    "path": str(f),
                    "size_mb": round(f.stat().st_size / (1024 * 1024), 2),
                    "mime_type": mime,
                }
            )

    result = {
        "folder": str(folder),
        "total_files": len(files),
        "files": files,
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp_server.tool()
async def create_project(
    name: str,
    description: str = "",
    width: int = 1920,
    height: int = 1080,
) -> str:
    """Create a new video project.

    Args:
        name: Project name
        description: Project description
        width: Video width in pixels (default: 1920)
        height: Video height in pixels (default: 1080)

    Returns:
        Created project details (id, name, status, etc.)
    """
    data = {
        "name": name,
        "description": description,
        "width": width,
        "height": height,
    }
    result = await _call_api("POST", "/api/projects", data)
    return _format_response(result)


@mcp_server.tool()
async def upload_assets(project_id: str, file_paths: list[str]) -> str:
    """Batch upload local files to a project with automatic classification.

    Uploads files and auto-classifies them by type (video/audio/image)
    and subtype (avatar/background/slide/narration/bgm/se/screen/effect/other).

    Args:
        project_id: Project UUID
        file_paths: List of absolute local file paths to upload

    Returns:
        Upload results with asset IDs and classifications
    """
    import json
    from pathlib import Path

    # Validate file existence
    missing = [p for p in file_paths if not Path(p).exists()]
    if missing:
        return json.dumps({"error": "Files not found", "missing": missing}, ensure_ascii=False)

    result = await _upload_files(
        f"/api/ai-video/projects/{project_id}/assets/batch-upload",
        file_paths,
        timeout=300.0,
    )
    return _format_response(result)


@mcp_server.tool()
async def reclassify_asset(
    project_id: str,
    asset_id: str,
    asset_type: str,
    subtype: str,
) -> str:
    """Manually correct an asset's classification.

    Use after upload_assets if auto-classification was wrong.

    Args:
        project_id: Project UUID
        asset_id: Asset UUID to reclassify
        asset_type: New type (video, audio, image)
        subtype: New subtype (avatar, background, slide, narration, bgm, se, screen, effect, other)

    Returns:
        Updated asset details
    """
    data = {"type": asset_type, "subtype": subtype}
    result = await _call_api(
        "PUT",
        f"/api/ai-video/projects/{project_id}/assets/{asset_id}/reclassify",
        data,
    )
    return _format_response(result)


@mcp_server.tool()
async def get_ai_asset_catalog(project_id: str) -> str:
    """Get AI-oriented asset catalog for plan generation.

    Returns assets grouped by type/subtype with metadata optimized
    for AI video plan generation. Different from get_asset_catalog
    which returns L2 timeline assets.

    Args:
        project_id: Project UUID

    Returns:
        Asset catalog with classification stats
    """
    result = await _call_api("GET", f"/api/ai-video/projects/{project_id}/asset-catalog")
    return _format_response(result)


@mcp_server.tool()
async def generate_plan(project_id: str, brief: dict) -> str:
    """Generate a video plan from a brief using AI (GPT-4o).

    Creates a structured video plan with sections, timing, and asset
    assignments based on the creative brief and available assets.

    Args:
        project_id: Project UUID
        brief: VideoBrief object with keys:
            - title (str): Video title
            - description (str): Video description
            - style (str): tutorial/presentation/demo
            - target_duration_seconds (int): Target length
            - language (str): ja/en
            - sections (list): Section definitions
            - preferences (dict): Avatar, BGM, text style preferences

    Returns:
        Generated VideoPlan with timeline structure
    """
    data = {"brief": brief}
    result = await _call_api("POST", f"/api/ai-video/projects/{project_id}/plan/generate", data)
    return _format_response(result)


@mcp_server.tool()
async def get_plan(project_id: str) -> str:
    """Get the current video plan for a project.

    Args:
        project_id: Project UUID

    Returns:
        Current VideoPlan or empty if no plan exists
    """
    result = await _call_api("GET", f"/api/ai-video/projects/{project_id}/plan")
    return _format_response(result)


@mcp_server.tool()
async def update_plan(project_id: str, plan: dict) -> str:
    """Update an existing video plan.

    Modify sections, timing, asset assignments, or other plan properties.

    Args:
        project_id: Project UUID
        plan: Updated VideoPlan object

    Returns:
        Updated plan confirmation
    """
    result = await _call_api("PUT", f"/api/ai-video/projects/{project_id}/plan", {"plan": plan})
    return _format_response(result)


@mcp_server.tool()
async def apply_plan(project_id: str) -> str:
    """Apply the video plan to generate timeline structure.

    Deterministic transformation: converts plan sections/elements into
    5 video layers (L1-L5) + 3 audio tracks. Overwrites existing timeline.

    Args:
        project_id: Project UUID

    Returns:
        Application result with duration, layers populated, clips added
    """
    result = await _call_api("POST", f"/api/ai-video/projects/{project_id}/plan/apply")
    return _format_response(result)


@mcp_server.tool()
async def render_video(project_id: str) -> str:
    """Start video rendering for a project.

    Output: MP4 (H.264 + AAC), 1920x1080, 30fps (Udemy standard).

    Args:
        project_id: Project UUID

    Returns:
        Render job details (job_id, status)
    """
    result = await _call_api("POST", f"/api/projects/{project_id}/render")
    return _format_response(result)


@mcp_server.tool()
async def get_render_status(project_id: str) -> str:
    """Get rendering job progress and status.

    Args:
        project_id: Project UUID

    Returns:
        Render status (queued/processing/completed/failed),
        progress percentage, download URL if completed
    """
    result = await _call_api("GET", f"/api/projects/{project_id}/render/status")
    return _format_response(result)


# =============================================================================
# Helper Functions
# =============================================================================


def _format_response(data: dict[str, Any]) -> str:
    """APIレスポンスを読みやすいJSON文字列に整形する。

    Args:
        data: APIからのレスポンス辞書

    Returns:
        インデント付きJSON文字列（日本語対応）
    """
    import json

    return json.dumps(data, indent=2, ensure_ascii=False)


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    # Run as standalone server
    logger.info(f"Starting Douga MCP Server (API: {API_BASE_URL})")
    uvicorn.run(mcp_server.app, host="0.0.0.0", port=6500)
