# AI Friendly API Reference (Target)

最終更新: 2026-02-03

このドキュメントは**理想仕様**のAPIリファレンスです。
OpenAPIは `docs/openapi/douga-ai-friendly.yaml` を正とし、この文書は人間向けの要約です。

---

## 0. 共通

### Base URL / Versioning
- Base: `/api/ai/v1`
- すべてのエンドポイントはバージョン付き。

### 必須ヘッダー
- `Idempotency-Key`: **POST/PUT/PATCH/DELETE は必須**
- `If-Match`: 競合防止（ETag、**POST/PUT/PATCH/DELETE は必須**）
- `X-Request-Id`: 任意だが推奨

### 変更系の共通オプション

すべての変更系リクエストは `options` を**必須**で持つ。
**DELETE も JSON body で `options` を送る。**

```json
{
  "options": {
    "validate_only": true,
    "return_diff": true
  }
}
```

### 返信の共通フォーマット

**成功レスポンス**
```json
{
  "request_id": "uuid",
  "data": { },
  "meta": {
    "operation_id": "op-uuid",
    "rollback_id": "rb-uuid",
    "diff": { }
  }
}
```

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
    "retryable": false
  }
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

## 2. Project

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

- `GET /projects/{project_id}/timeline`
  - 完全タイムライン（L3詳細）

- `GET /projects/{project_id}/summary`
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

- `DELETE /projects/{project_id}/layers/{layer_id}`
  - レイヤー削除（依存チェックあり）

- `POST /projects/{project_id}/layers/reorder`
  - レイヤー順序変更

---

## 5. Clip

- `POST /projects/{project_id}/clips`
  - クリップ追加（video/image/shape/text）
  - `transition_in` / `transition_out` を指定可能（任意）

- `PATCH /projects/{project_id}/clips/{clip_id}`
  - クリップ更新（start_ms, duration_ms, transform, effects）
  - `transition_in` / `transition_out` を更新可能

- `DELETE /projects/{project_id}/clips/{clip_id}`
  - クリップ削除

- `POST /projects/{project_id}/clips/{clip_id}/keyframes`
  - キーフレーム追加

- `DELETE /projects/{project_id}/clips/{clip_id}/keyframes/{keyframe_id}`
  - キーフレーム削除

---

## 6. Audio

- `POST /projects/{project_id}/audio-tracks`
  - 音声トラック作成

- `POST /projects/{project_id}/audio-clips`
  - 音声クリップ追加

- `PATCH /projects/{project_id}/audio-clips/{clip_id}`
  - 音声クリップ更新

- `DELETE /projects/{project_id}/audio-clips/{clip_id}`
  - 音声クリップ削除

- `POST /projects/{project_id}/audio-clips/{clip_id}/volume-keyframes`
  - ボリュームキーフレーム追加

- `DELETE /projects/{project_id}/audio-clips/{clip_id}/volume-keyframes/{keyframe_id}`
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

- `POST /projects/{project_id}/semantic/snap_to_previous`
- `POST /projects/{project_id}/semantic/snap_to_next`
- `POST /projects/{project_id}/semantic/close_gap`
- `POST /projects/{project_id}/semantic/auto_duck_bgm`
- `POST /projects/{project_id}/semantic/rename_layer`

各操作は `options.validate_only` をサポートし、`meta.diff` を返す。

---

## 9. Batch / Plan

### バッチ操作
- `POST /projects/{project_id}/batch`
  - `atomic` or `best_effort`
  - 変更結果を `meta.diff` として返す

### Plan (推奨)
- `POST /projects/{project_id}/plans`
  - 編集計画の登録

- `POST /projects/{project_id}/plans/{plan_id}/validate`
  - 事前検証

- `POST /projects/{project_id}/plans/{plan_id}/apply`
  - 適用（rollback_id を返す）

### Operations / Rollback
- `GET /projects/{project_id}/operations`
  - 操作履歴取得
- `POST /projects/{project_id}/operations/{operation_id}/rollback`
  - 変更の取り消し

---

## 10. Validation / Diff

- `POST /projects/{project_id}/validate`
  - 現在のタイムライン検証

- `POST /projects/{project_id}/diff`
  - 変更差分の生成（適用せず）

---

## 11. Render

- `POST /projects/{project_id}/renders`
  - レンダー開始

- `GET /projects/{project_id}/renders/{render_id}`
  - 進捗取得

---

## 12. Assets

- `POST /assets`
  - 署名URL発行

- `GET /assets/{asset_id}`
  - アセット情報取得

- `DELETE /assets/{asset_id}`
  - アセット削除
