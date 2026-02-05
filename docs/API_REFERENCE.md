# AI Friendly API Reference (v1 Current)

最終更新: 2026-02-04

このドキュメントは **v1 現行実装** のAPIリファレンスです。
OpenAPI (`docs/openapi/douga-ai-friendly.yaml`) は理想仕様を含むため、実装差分がある場合は本ドキュメントを正とします。

---

## 0. 共通

### Base URL / Versioning
- Base: `/api/ai/v1`
- すべてのエンドポイントはバージョン付き。

### ヘッダー
- `Idempotency-Key`: **POST/PUT/PATCH/DELETE で必須**（`validate_only=true` の場合は任意）
- `If-Match`: 競合防止（ETag、**推奨**。不一致は 409）
- `X-Request-Id`: 任意だが推奨

### 変更系の共通オプション

すべての変更系リクエストは `options` をサポートする。
**DELETE は JSON body が任意**（指定する場合は `options` を含める）。

```json
{
  "options": {
    "validate_only": true,
    "include_diff": true
  }
}
```
`return_diff` は互換のため受け付ける（`include_diff` が正）。

### 返信の共通フォーマット

**成功レスポンス（mutation）**
```json
{
  "request_id": "uuid",
  "data": {
    "operation_id": "op-uuid",
    "rollback_available": true,
    "diff": { }
  },
  "meta": { }
}
```
`diff` は `options.include_diff=true` の場合のみ返却。

**エラーレスポンス**
```json
{
  "request_id": "uuid",
  "error": {
    "code": "TIMELINE_OVERLAP",
    "message": "Clips overlap on layer L3",
    "details": {"layer_id": "...", "clip_id": "..."},
    "location": {"path": "layers[2].clips[5]"},
    "suggested_fix": "Move clip to after previous clip end",
    "retryable": false,
    "suggested_actions": []
  },
  "meta": { }
}
```

### OpenAPIの機械制約（x-constraints）

OpenAPI の各スキーマには `x-constraints` を付与する。
**単独フィールドでは表現できない条件**（in/out範囲、重複禁止、keyframe範囲など）を
機械可読なルールとして記述し、`error_code` と 1:1 で対応させる。

---

## 1. Capabilities / Metadata

- `GET /capabilities`
  - 返却: サポートされるエフェクト、easing、blend_modes、transitions、font_families、shape_types、text_aligns、track_types、最大レイヤー数、上限など
  - AIは **この一覧を正として** enum 値を選択する
  - バッチは `max_batch_ops` を上限として分割する

- `GET /version`
  - 返却: APIバージョン、schemaバージョン

---

## 2. Project (Planned)

- `POST /projects`
  - 新規プロジェクト作成
  - **必須**: `name` と `settings`
  - **固定値**: time_base=ms, frame_rate=30, resolution=1920x1080, overlap_policy=disallow

- `GET /projects/{project_id}`
  - プロジェクト取得（構成 + 設定）
  - `ETag` を返す（If-Matchで使用）

- `PATCH /projects/{project_id}`
  - プロジェクト設定更新（name のみ）
  - `options` 必須
  - **不変**: time_base / frame_rate / resolution / overlap_policy は更新不可

- `DELETE /projects/{project_id}`
  - プロジェクト削除

---

## 3. Timeline / Read Models

- `GET /projects/{project_id}/timeline` (**Planned**)
  - 完全タイムライン（L3詳細）

- `GET /projects/{project_id}/overview`
  - L1概要（時間、レイヤー数、オーディオ状態など）

- `GET /projects/{project_id}/structure`
  - L2構造（レイヤー/トラック/クリップの階層）

- `GET /projects/{project_id}/assets`
  - アセット一覧（メタデータ含む）

---

## 4. Layer

- `POST /projects/{project_id}/layers`
  - レイヤー作成

- `PATCH /projects/{project_id}/layers/{layer_id}`
  - レイヤー属性更新（name, visible, locked, color, order）

- `PUT /projects/{project_id}/layers/order`
  - レイヤー順序変更

---

## 5. Clip

- `POST /projects/{project_id}/clips`
  - クリップ追加（video/image/shape/text）
  - `transition_in` / `transition_out` を指定可能（任意）

- `PATCH /projects/{project_id}/clips/{clip_id}/move`
  - クリップ移動（start_ms, layer_id）

- `PATCH /projects/{project_id}/clips/{clip_id}/transform`
  - トランスフォーム更新（x/y/scale/rotation）

- `PATCH /projects/{project_id}/clips/{clip_id}/effects`
  - エフェクト更新（opacity / fade / chroma key）

- `POST /projects/{project_id}/clips/{clip_id}/chroma-key/preview`
  - クロマキーの **5分割プレビュー** を生成
  - `key_color`: `"auto"` or `#RRGGBB`
  - `similarity`, `blend` を指定可能
  - 固定サンプル位置: **10/30/50/70/90%**

- `POST /projects/{project_id}/clips/{clip_id}/chroma-key/apply`
  - クロマキー処理済みの **新規アセットを生成**
  - 既存クリップは置き換えない（クライアント側で確認）

- `DELETE /projects/{project_id}/clips/{clip_id}`
  - クリップ削除

- `POST /projects/{project_id}/clips/{clip_id}/keyframes` (**Planned**)
  - キーフレーム追加

- `DELETE /projects/{project_id}/clips/{clip_id}/keyframes/{keyframe_id}` (**Planned**)
  - キーフレーム削除

---

## 6. Audio

- `POST /projects/{project_id}/audio-tracks`
  - 音声トラック作成

- `POST /projects/{project_id}/audio-clips`
  - 音声クリップ追加

- `PATCH /projects/{project_id}/audio-clips/{clip_id}/move`
  - 音声クリップ移動

- `DELETE /projects/{project_id}/audio-clips/{clip_id}`
  - 音声クリップ削除

- `POST /projects/{project_id}/audio-clips/{clip_id}/volume-keyframes` (**Planned**)
  - ボリュームキーフレーム追加

- `DELETE /projects/{project_id}/audio-clips/{clip_id}/volume-keyframes/{keyframe_id}` (**Planned**)
  - ボリュームキーフレーム削除

---

## 7. Marker

- `POST /projects/{project_id}/markers`
  - マーカー追加

- `PATCH /projects/{project_id}/markers/{marker_id}`
  - マーカー更新

- `DELETE /projects/{project_id}/markers/{marker_id}`
  - マーカー削除

---

## 8. Semantic Operations

- `POST /projects/{project_id}/semantic`
  - `operation`: `snap_to_previous` / `snap_to_next` / `close_gap` / `auto_duck_bgm` / `rename_layer`

各操作は `options.validate_only` をサポートし、`options.include_diff=true` で `data.diff` を返す。

---

## 9. Batch / Plan

### バッチ操作
- `POST /projects/{project_id}/batch`
  - `operations[]` を順次実行（best_effort）
  - 変更結果を `data.diff` として返す（`options.include_diff=true`）

### Plan (Planned)
- `POST /projects/{project_id}/plans`
  - 編集計画の登録

- `POST /projects/{project_id}/plans/{plan_id}/validate`
  - 事前検証

- `POST /projects/{project_id}/plans/{plan_id}/apply`
  - 適用（operation_id を返す）

### Operations / Rollback
- `GET /projects/{project_id}/history`
  - 操作履歴取得
- `GET /projects/{project_id}/operations/{operation_id}`
  - 操作詳細取得
- `POST /projects/{project_id}/operations/{operation_id}/rollback`
  - 変更の取り消し

---

## 10. Validation / Diff

- `options.validate_only=true` を各 mutation に付与（事前検証）
- `options.include_diff=true` で diff を返却

---

## 11. Render (Planned)

- `POST /projects/{project_id}/renders`
  - レンダー開始

- `GET /projects/{project_id}/renders/{render_id}`
  - 進捗取得

---

## 12. Assets (Planned)

- `POST /assets`
  - 署名URL発行

- `GET /assets/{asset_id}`
  - アセット情報取得

- `DELETE /assets/{asset_id}`
  - アセット削除
