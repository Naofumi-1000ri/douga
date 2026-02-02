# MCP Server - Douga Video Editor

このディレクトリには、Douga動画編集アプリケーション用のMCP（Model Context Protocol）サーバー実装が含まれています。

## 概要

MCPサーバーは、Claude等のAIアシスタントがDouga動画編集APIと対話するためのインターフェースを提供します。FastMCPフレームワークを使用し、階層的なデータアクセスパターン（L1→L2→L3）でプロジェクトデータを効率的に取得・操作できます。

## アーキテクチャ

```
┌─────────────────────────────────────────────────────────────┐
│                     AIアシスタント                          │
│                   (Claude等のMCPクライアント)                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 MCP Server (port 6500)                      │
│                   src/mcp/server.py                         │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  FastMCP Instance: "Douga Video Editor"               │  │
│  │  - L1 Tools: プロジェクト概要                          │  │
│  │  - L2 Tools: 構造情報（タイムライン、アセット）         │  │
│  │  - L3 Tools: 詳細情報（クリップ）                      │  │
│  │  - Write Tools: 編集操作                              │  │
│  │  - AI Video Tools: AI動画制作                         │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼ HTTP/REST
┌─────────────────────────────────────────────────────────────┐
│                 Douga Backend API                           │
│            (Cloud Run / localhost:8000)                     │
│                   /api/ai/* endpoints                       │
└─────────────────────────────────────────────────────────────┘
```

## ディレクトリ構成

```
src/mcp/
├── __init__.py      # モジュール初期化、mcp_serverのエクスポート
├── server.py        # MCPサーバー本体（ツール定義含む）
├── tools/
│   └── __init__.py  # ツール拡張用（現在は空）
└── README.md        # このファイル
```

## 階層的データアクセス（L1/L2/L3）

トークン効率を最適化するため、データは3つのレベルで段階的に取得します。

### L1: サマリーレベル（~300トークン）
プロジェクト全体の概要を取得。まずここから始めてスコープを理解。

| ツール | 説明 |
|--------|------|
| `get_project_overview` | プロジェクトメタデータ、レイヤー/トラック数、アセット数 |

### L2: 構造レベル（~800トークン）
タイムラインやアセットの構造情報。対象を特定するために使用。

| ツール | 説明 |
|--------|------|
| `get_timeline_structure` | レイヤーとオーディオトラックの一覧 |
| `get_timeline_at_time` | 特定時刻のアクティブクリップ |
| `get_asset_catalog` | 利用可能なアセット一覧 |

### L3: 詳細レベル（~400トークン/クリップ）
個別クリップの完全な情報。編集操作前に取得。

| ツール | 説明 |
|--------|------|
| `get_clip_details` | ビデオクリップの詳細（トランスフォーム、エフェクト等） |
| `get_audio_clip_details` | オーディオクリップの詳細（ボリューム、フェード等） |

## 利用可能なツール一覧

### 読み取りツール（Read）

#### L1 サマリー
- `get_project_overview(project_id)` - プロジェクト概要

#### L2 構造
- `get_timeline_structure(project_id)` - タイムライン構造
- `get_timeline_at_time(project_id, time_ms)` - 特定時刻の状態
- `get_asset_catalog(project_id)` - アセットカタログ

#### L3 詳細
- `get_clip_details(project_id, clip_id)` - ビデオクリップ詳細
- `get_audio_clip_details(project_id, clip_id)` - オーディオクリップ詳細

### 書き込みツール（Write）

#### レイヤー操作
- `add_layer(project_id, name, layer_type, insert_at)` - レイヤー追加
- `update_layer(project_id, layer_id, name, visible, locked)` - レイヤー更新
- `reorder_layers(project_id, layer_ids)` - レイヤー並び替え

#### ビデオクリップ操作
- `add_clip(project_id, layer_id, start_ms, duration_ms, ...)` - クリップ追加
- `move_clip(project_id, clip_id, new_start_ms, new_layer_id)` - クリップ移動
- `update_clip_transform(project_id, clip_id, x, y, scale, rotation)` - トランスフォーム更新
- `update_clip_effects(project_id, clip_id, opacity, chroma_key_*)` - エフェクト更新
- `delete_clip(project_id, clip_id)` - クリップ削除

#### オーディオクリップ操作
- `add_audio_clip(project_id, track_id, asset_id, ...)` - オーディオクリップ追加
- `move_audio_clip(project_id, clip_id, new_start_ms, new_track_id)` - オーディオクリップ移動
- `delete_audio_clip(project_id, clip_id)` - オーディオクリップ削除

### セマンティック操作（Semantic）

高レベルな編集操作を1コマンドで実行。

- `snap_to_previous(project_id, target_clip_id)` - 前のクリップに詰める
- `snap_to_next(project_id, target_clip_id)` - 次のクリップを詰める
- `close_gap(project_id, target_layer_id)` - レイヤー内の全ギャップを閉じる
- `auto_duck_bgm(project_id, duck_to, attack_ms, release_ms)` - BGMオートダッキング
- `rename_layer(project_id, layer_id, new_name)` - レイヤー名変更

### 分析ツール（Analysis）

- `analyze_gaps(project_id)` - タイムラインのギャップ検出
- `analyze_pacing(project_id, segment_duration_ms)` - ペーシング分析

### AI動画制作ツール

フルワークフローでの動画制作をサポート。

- `scan_folder(path)` - ローカルフォルダのメディアファイルスキャン
- `create_project(name, description, width, height)` - プロジェクト作成
- `upload_assets(project_id, file_paths)` - アセット一括アップロード
- `reclassify_asset(project_id, asset_id, asset_type, subtype)` - アセット分類修正
- `get_ai_asset_catalog(project_id)` - AI向けアセットカタログ
- `generate_plan(project_id, brief)` - AIによる動画プラン生成
- `get_plan(project_id)` - 現在のプラン取得
- `update_plan(project_id, plan)` - プラン更新
- `apply_plan(project_id)` - プランをタイムラインに適用
- `render_video(project_id)` - レンダリング開始
- `get_render_status(project_id)` - レンダリング状況確認

## 設定方法

### 環境変数

| 変数名 | 説明 | デフォルト |
|--------|------|-----------|
| `DOUGA_API_URL` | バックエンドAPIのベースURL | `http://localhost:8000` |
| `DOUGA_API_KEY` | API認証キー（推奨） | なし |
| `DOUGA_API_TOKEN` | Firebaseトークン（レガシー） | `dev-token` |

**注意**: `DOUGA_API_KEY`が設定されている場合はそちらが優先されます。

### 依存関係のインストール

```bash
pip install mcp[cli] httpx
```

または、プロジェクトの依存関係として:

```bash
uv pip install -e ".[dev]"
```

## 起動方法

### スタンドアロンサーバーとして起動

```bash
# backendディレクトリから
python -m src.mcp.server

# または環境変数を指定して
DOUGA_API_URL=https://douga-api-344056413972.asia-northeast1.run.app \
DOUGA_API_KEY=your-api-key \
python -m src.mcp.server
```

サーバーはポート6500で起動します。

### MCP CLIで起動

```bash
mcp run src.mcp.server:mcp_server
```

### Claude Desktop / Cursorとの連携

`~/.claude/config.json`または該当する設定ファイルに以下を追加:

```json
{
  "mcpServers": {
    "douga": {
      "command": "python",
      "args": ["-m", "src.mcp.server"],
      "cwd": "/path/to/backend",
      "env": {
        "DOUGA_API_URL": "https://douga-api-344056413972.asia-northeast1.run.app",
        "DOUGA_API_KEY": "your-api-key"
      }
    }
  }
}
```

## 使用例

### 典型的なワークフロー

```
1. プロジェクト概要を取得（L1）
   → get_project_overview(project_id)

2. タイムライン構造を確認（L2）
   → get_timeline_structure(project_id)

3. 編集対象のクリップを特定
   → get_clip_details(project_id, clip_id)

4. 編集操作を実行
   → update_clip_transform(project_id, clip_id, x=100, y=200)

5. 結果を確認
   → get_clip_details(project_id, clip_id)
```

### AI動画制作ワークフロー

```
1. 素材フォルダをスキャン
   → scan_folder("/path/to/materials")

2. 新規プロジェクトを作成
   → create_project(name="新講座", description="...")

3. 素材をアップロード
   → upload_assets(project_id, ["/path/to/file1.mp4", ...])

4. 動画プランを生成
   → generate_plan(project_id, brief={...})

5. プランをタイムラインに適用
   → apply_plan(project_id)

6. レンダリング
   → render_video(project_id)
   → get_render_status(project_id)  # 完了まで確認
```

## API認証

MCPサーバーは2つの認証方式をサポート:

1. **APIキー認証（推奨）**: `X-API-Key`ヘッダーで送信
2. **Bearerトークン認証**: `Authorization: Bearer {token}`ヘッダーで送信

本番環境では`DOUGA_API_KEY`の使用を推奨します。

## エラーハンドリング

- HTTPエラーは`httpx.HTTPStatusError`として伝播
- MCPパッケージ未インストール時はスタブクラスで代替（警告表示）
- ファイル操作エラーはJSON形式でエラーメッセージを返却

## 開発・拡張

### 新しいツールの追加

`server.py`に新しいツールを追加:

```python
@mcp_server.tool()
async def my_new_tool(project_id: str, param1: str) -> str:
    """ツールの説明。

    Args:
        project_id: プロジェクトUUID
        param1: パラメータの説明

    Returns:
        結果の説明
    """
    result = await _call_api("POST", f"/api/ai/project/{project_id}/my-endpoint", {
        "param1": param1
    })
    return _format_response(result)
```

### ツールの分割

大規模になる場合は`tools/`ディレクトリにファイルを分割:

```python
# tools/my_tools.py
from src.mcp.server import mcp_server, _call_api, _format_response

@mcp_server.tool()
async def my_tool(...):
    ...
```

## 関連ドキュメント

- [Model Context Protocol仕様](https://modelcontextprotocol.io/)
- [FastMCPドキュメント](https://github.com/jlowin/fastmcp)
- プロジェクトルートの`CLAUDE.md` - 全体アーキテクチャ
- `docs/IMPROVEMENT_PLAN.md` - 機能改善ロードマップ
