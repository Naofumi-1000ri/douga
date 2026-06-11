# ADR 0001: MCP 二重実装の一本化と V1 API への全面移行

- **ステータス**: 承認済み
- **日付**: 2026-06-11
- **関連 Issue**: #279
- **決定者**: Naofumi-1000ri

---

## 文脈と問題

AI 統合が 4 面に分裂し、プロダクトの核心（AI による動画編集）の信頼性を構造的に下げていた。

| 面 | 役割 | 状態 |
|---|---|---|
| REST 旧 AI API (`api/ai.py`) | ブラウザ内チャット | Idempotency・操作履歴なし |
| REST AI **V1** (`api/ai_v1.py`) | Envelope / Idempotency / validate_only / rollback 完備 | **MCP から未使用** |
| `backend/src/mcp/server.py` | MCP 35 ツール | 旧 API を呼ぶ |
| `douga-mcp/` | 独立 MCP 17 ツール | 別実装。edit_timeline は GET→ローカル改変→PUT で楽観ロックなし |

V1 に投資した Idempotency / validate_only / rollback が、実際の AI クライアントに届いていない状態だった。

また `douga-mcp/` は `backend/src/mcp/server.py` と機能が重複しており、`scan_folder` / `upload_assets` / `generate_plan` 等は互いにコピー関係で乖離が進行中だった（`ensure_ascii` 等に差分）。

---

## 決定

**`backend/src/mcp/server.py` に一本化し、`douga-mcp/` はアーカイブする。**

- 正: `backend/src/mcp/server.py`（35 ツール）
- 廃止: `douga-mcp/`（17 ツール）→ `_archive/douga-mcp/` に移動

### 移行内容

| カテゴリ | 旧エンドポイント | 新エンドポイント (V1) |
|---|---|---|
| 読み取り: get_project_overview | `/api/ai/project/{id}/overview` | `/api/ai/v1/projects/{id}/overview` |
| 読み取り: get_timeline_structure | `/api/ai/project/{id}/structure` | `/api/ai/v1/projects/{id}/structure` |
| 読み取り: get_timeline_at_time | `/api/ai/project/{id}/at-time/{time_ms}` | `/api/ai/v1/projects/{id}/at-time/{time_ms}` |
| 読み取り: get_asset_catalog | `/api/ai/project/{id}/assets` | `/api/ai/v1/projects/{id}/assets` |
| 読み取り: get_clip_details | `/api/ai/project/{id}/clip/{clip_id}` | `/api/ai/v1/projects/{id}/clips/{clip_id}` |
| 読み取り: get_audio_clip_details | `/api/ai/project/{id}/audio-clip/{clip_id}` | `/api/ai/v1/projects/{id}/audio-clips/{clip_id}` |
| 書き込み: add_layer | `POST /api/ai/project/{id}/layers` | `POST /api/ai/v1/projects/{id}/layers` + Idempotency-Key |
| 書き込み: reorder_layers | `PUT /api/ai/project/{id}/layers/order` | `PUT /api/ai/v1/projects/{id}/layers/order` + Idempotency-Key |
| 書き込み: update_layer | `PATCH /api/ai/project/{id}/layer/{layer_id}` | `PATCH /api/ai/v1/projects/{id}/layers/{layer_id}` + Idempotency-Key |
| 書き込み: add_clip | `POST /api/ai/project/{id}/clips` | `POST /api/ai/v1/projects/{id}/clips` + Idempotency-Key |
| 書き込み: move_clip | `PATCH .../clip/{id}/move` | `PATCH /api/ai/v1/projects/{id}/clips/{id}/move` + Idempotency-Key |
| 書き込み: update_clip_transform | `PATCH .../clip/{id}/transform` | `PATCH /api/ai/v1/projects/{id}/clips/{id}/transform` + Idempotency-Key |
| 書き込み: update_clip_effects | `PATCH .../clip/{id}/effects` | `PATCH /api/ai/v1/projects/{id}/clips/{id}/effects` + Idempotency-Key |
| 書き込み: delete_clip | `DELETE .../clip/{id}` | `DELETE /api/ai/v1/projects/{id}/clips/{id}` + Idempotency-Key |
| 書き込み: add_audio_clip | `POST .../audio-clips` | `POST /api/ai/v1/projects/{id}/audio-clips` + Idempotency-Key |
| 書き込み: move_audio_clip | `PATCH .../audio-clip/{id}/move` | `PATCH /api/ai/v1/projects/{id}/audio-clips/{id}/move` + Idempotency-Key |
| 書き込み: delete_audio_clip | `DELETE .../audio-clip/{id}` | `DELETE /api/ai/v1/projects/{id}/audio-clips/{id}` + Idempotency-Key |
| セマンティック: snap_to_previous | `POST .../semantic` (旧) | `POST /api/ai/v1/projects/{id}/semantic` + Idempotency-Key |
| セマンティック: snap_to_next | `POST .../semantic` (旧) | `POST /api/ai/v1/projects/{id}/semantic` + Idempotency-Key |
| セマンティック: close_gap | `POST .../semantic` (旧) | `POST /api/ai/v1/projects/{id}/semantic` + Idempotency-Key |
| セマンティック: rename_layer | `POST .../semantic` (旧) | `POST /api/ai/v1/projects/{id}/semantic` + Idempotency-Key |
| 分析: analyze_gaps | `GET .../analysis/gaps` (旧) | `GET /api/ai/v1/projects/{id}/analysis/gaps` |
| 分析: analyze_pacing | `GET .../analysis/pacing` (旧) | `GET /api/ai/v1/projects/{id}/analysis/pacing` |

非 V1 ルーターを呼ぶツール（`scan_folder`, `create_project`, `upload_assets`, `reclassify_asset`, `get_ai_asset_catalog`, `generate_plan`, `get_plan`, `update_plan`, `apply_plan`, `render_video`, `get_render_status`）はエンドポイントが専用ルーターにしか存在しないため変更なし。

### douga-mcp の廃止

- `douga-mcp/` ディレクトリを `_archive/douga-mcp/` へ移動（履歴は `git` で保持）
- `douga-mcp/README.md` に Deprecation 警告と migration ガイドを追加
- `claude_desktop_config.json` は `backend/src/mcp/server.py` を指すよう更新が必要（ユーザー側の設定変更）

---

## 採用した理由

1. **旧 API の問題解消**: 旧 API には Idempotency が無く、並行編集時の競合が検出されない。V1 はすべての書き込み操作に Idempotency-Key と ETag ベース楽観ロックが実装済み。
2. **重複コードの排除**: 2 つの実装が乖離し続けると、バグ修正が片方だけに当たりやすい。
3. **backend 内に一本化することで CI が一括カバー**: backend の pytest/mypy/ruff がそのまま適用できる。
4. **douga-mcp の edit_timeline（PUT 直書き込み）は危険**: GET→ローカル改変→PUT は楽観ロックがなく、並行編集を黙って上書きする。V1 の個別クリップ操作（add_clip, move_clip 等）は version フィールドと ETag で 409 を返す。

## 却下した代替案

- **douga-mcp を正として採用する**: backend の CI カバレッジから外れ、型チェックも困難。採用しない。
- **両方を並行維持する**: 乖離が続くため却下。

---

## 結果

- MCP クライアント（Claude Desktop 等）は `backend/src/mcp/server.py` のみを使う
- すべての書き込みツールが V1 経由になり、Idempotency-Key が自動付与される
- douga-mcp の edit_timeline（直接 PUT）は廃止され、個別クリップ操作に置き換え
- MCP 経由の操作は X-Edit-Session を送らずデフォルトシーケンスを対象とする。シーケンスロックが必要なフローは将来対応とする
- CI（ruff / mypy / pytest）が一括でカバーする
