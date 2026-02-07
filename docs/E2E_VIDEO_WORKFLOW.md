# E2E Video Production Workflow

AI アシスタント（Claude / GPT）向けの動画制作フローガイド。
このドキュメントは Douga アプリケーションで素材から完成動画までを一貫して制作する手順を定義する。

**API Base URL**: `https://douga-api-344056413972.asia-northeast1.run.app`

> Last updated: 2026-02-07

---

## 1. Prerequisites

### 必要アセット

| アセット種類 | ファイル形式 | 用途 |
|-------------|-------------|------|
| ナレーション音声 | WAV / MP3 | メイン音声。STT でテロップ生成に使用 |
| アバター動画 | MP4（グリーンバック） | クロマキー合成。音声は自動抽出されナレーションに |
| 操作画面キャプチャ | MP4 | チュートリアル操作画面。クリック検出対象 |
| スライド画像 | PNG / JPEG | 説明用スライド |
| BGM | WAV / MP3 | 背景音楽。オートダッキング対象 |
| 背景素材 | PNG / JPEG / MP4 | 背景レイヤー用 |

### 認証

すべてのエンドポイントは Firebase Auth トークンが必要。

```bash
# 環境変数の設定
export API_BASE="https://douga-api-344056413972.asia-northeast1.run.app"
export TOKEN="your-firebase-auth-token"
export PROJECT_ID="your-project-uuid"
```

開発モードでは `Authorization: Bearer dev-token` が使用可能。

### プロジェクト作成

プロジェクトは UI から作成するか、API 経由で作成する。
プロジェクト ID は以降のすべてのリクエストで使用する。

---

## 2. Asset Preparation

### Step 2.1: アップロード

**UI 経由**: エディタの「追加」ボタンからファイルを選択。

**API 経由（batch-upload）**: 複数素材を一括アップロード。

```bash
curl -X POST \
  "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/assets/batch-upload" \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "files=@avatar.mp4" \
  -F "files=@narration.wav" \
  -F "files=@screen_capture.mp4" \
  -F "files=@slide01.png" \
  -F "files=@bgm.mp3"
```

レスポンスで各ファイルの `asset_id`、`type`、`subtype`、`confidence` が返る。
メタデータ（duration、dimensions、chroma key color、thumbnail）はアップロード時に同期解析される。

### Step 2.2: 分類確認

```bash
curl -X GET \
  "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/asset-catalog" \
  -H "Authorization: Bearer ${TOKEN}"
```

各アセットの `subtype` を確認する。確認ポイント:

- アバター動画 → `subtype: "avatar"`, `chroma_key_color` が設定されていること
- 操作画面 → `subtype: "screen"`
- ナレーション音声 → `subtype: "narration"`
- BGM → `subtype: "bgm"`
- スライド → `subtype: "slide"`
- 背景 → `subtype: "background"`

### Step 2.3: 再分類

subtype が誤っている場合は修正する。

```bash
curl -X PUT \
  "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/assets/${ASSET_ID}/reclassify" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"type": "video", "subtype": "screen"}'
```

---

## 3. Plan Generation

### VideoBrief 概念

VideoBrief は「素材をどう組み合わせるか」を構造化した指示書。AIがアセットカタログと Brief を分析し、セクション構成・レイアウト・タイミングを含む VideoPlan を生成する。

### Step 3.1: 計画生成

```bash
curl -X POST \
  "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/plan/generate" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "brief": {
      "title": "Unity入門講座",
      "description": "Unityの基本操作を学ぶ",
      "style": "tutorial",
      "target_duration_seconds": 300,
      "language": "ja",
      "sections": [
        {
          "type": "intro",
          "title": "挨拶",
          "description": "講師挨拶と講座概要",
          "estimated_duration_seconds": 15
        },
        {
          "type": "toc",
          "title": "目次",
          "description": "今回の内容一覧",
          "estimated_duration_seconds": 10
        },
        {
          "type": "content",
          "title": "Unity操作説明",
          "description": "基本的なUI操作とシーン構築",
          "estimated_duration_seconds": 200
        },
        {
          "type": "summary",
          "title": "まとめ",
          "estimated_duration_seconds": 20
        },
        {
          "type": "outro",
          "title": "エンディング",
          "estimated_duration_seconds": 10
        }
      ],
      "preferences": {
        "use_avatar": true,
        "avatar_position": "bottom-right",
        "bgm_style": "calm",
        "include_intro": true,
        "include_outro": true,
        "chroma_key_avatar": true,
        "text_style": "modern"
      }
    }
  }'
```

### Step 3.2: 計画レビュー

生成された計画を確認する。

```bash
curl -X GET \
  "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/plan" \
  -H "Authorization: Bearer ${TOKEN}"
```

確認ポイント:
- セクション数と順序が意図通りか
- 各セクションに適切なアセットが割り当てられているか
- レイアウトタイプが適切か（avatar_fullscreen, slide_with_avatar, screen_capture 等）
- 合計尺がターゲットに近いか

計画を手動修正する場合:
```bash
curl -X PUT \
  "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/plan" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"plan": { ... }}'
```

---

## 4. Timeline Construction

### Step 4.1: 計画適用

```bash
curl -X POST \
  "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/plan/apply" \
  -H "Authorization: Bearer ${TOKEN}"
```

この処理は以下を自動実行する:
- VideoPlan を timeline_data に変換
- アバター動画から音声を抽出しナレーショントラックに配置
- クロマキー設定を自動適用

レスポンス:
```json
{
  "project_id": "...",
  "duration_ms": 300000,
  "layers_populated": 4,
  "audio_clips_added": 3
}
```

### Step 4.2: スキル実行

**方法 A: 一括実行（推奨）**

```bash
curl -X POST \
  "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/skills/run-all" \
  -H "Authorization: Bearer ${TOKEN}"
```

6 つのスキルを正しい依存順序で実行する。最初の失敗で停止する。

**方法 B: 個別実行**

依存順序に従って個別に実行する。各ステップ後に検証が可能。

```bash
# 1. 無音トリミング
curl -X POST "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/skills/trim-silence" \
  -H "Authorization: Bearer ${TOKEN}"

# 2. テロップ追加（STT実行）
curl -X POST "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/skills/add-telop" \
  -H "Authorization: Bearer ${TOKEN}"

# 3. レイアウト適用
curl -X POST "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/skills/layout" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"avatar_position": "bottom-right", "avatar_size": "pip", "screen_position": "fullscreen"}'

# 4. コンテンツ同期（add-telop に依存）
curl -X POST "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/skills/sync-content" \
  -H "Authorization: Bearer ${TOKEN}"

# 5. クリックハイライト
curl -X POST "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/skills/click-highlight" \
  -H "Authorization: Bearer ${TOKEN}"

# 6. アバター回避（click-highlight に依存）
curl -X POST "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/skills/avatar-dodge" \
  -H "Authorization: Bearer ${TOKEN}"
```

### 各ステップの検証ポイント

| ステップ | 検証内容 |
|----------|---------|
| plan/apply 後 | timeline-overview でレイヤー構成確認 |
| trim-silence 後 | ナレーション尺が短くなっていること |
| add-telop 後 | テキストレイヤーにクリップが追加されていること |
| layout 後 | sample-frame でレイアウト確認 |
| sync-content 後 | 操作画面の尺がナレーションに同期していること |
| click-highlight 後 | エフェクトレイヤーにハイライト矩形が追加されていること |
| avatar-dodge 後 | アバターにドッジキーフレームが追加されていること |

---

## 5. Verification Protocol

### Step 5.1: validate-composition（品質チェック）

```bash
curl -X POST \
  "${API_BASE}/api/projects/${PROJECT_ID}/preview/validate" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"rules": null}'
```

10 ルールで品質を検証する:

| ルール | 説明 | severity |
|--------|------|----------|
| `overlapping_clips` | 同一レイヤーでクリップが重複 | error |
| `clip_bounds` | クリップの時間範囲が不正 | error |
| `missing_assets` | 参照アセットが存在しない | error |
| `safe_zone` | 画面外に配置されたクリップ | warning |
| `empty_layers` | 空のレイヤーが存在 | warning |
| `audio_sync` | 音声と映像の同期ずれ | warning |
| `duration_consistency` | 全体尺の不整合 | warning |
| `text_readability` | テキストサイズ・コントラスト不足 | warning |
| `layer_ordering` | レイヤー順序の問題 | warning |
| `gap_detection` | 映像に隙間がある | warning |

`is_valid: true` かつ `errors: 0` であれば品質合格。warning は推奨修正。

### Step 5.2: sample-event-points（視覚検証）

```bash
curl -X POST \
  "${API_BASE}/api/projects/${PROJECT_ID}/preview/sample-event-points" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"max_samples": 10, "resolution": "640x360", "include_audio": true}'
```

主要イベントポイント（セクション境界、スライド切替、アバター登場など）でフレームを自動サンプリング。
返された base64 画像を確認し、レイアウト崩れや意図しない表示がないか検証する。

### Step 5.3: timeline-overview（構造監査）

```bash
curl -X GET \
  "${API_BASE}/api/ai/v1/projects/${PROJECT_ID}/timeline-overview" \
  -H "Authorization: Bearer ${TOKEN}"
```

全クリップのアセット名、配置、ギャップ、重複、警告を一覧で確認する。
L2.5 レベルの情報で、構造全体を俯瞰できる。

---

## 6. Correction Procedures

### sync 不一致

操作画面とナレーションのタイミングがずれている場合。

```bash
# sync-content を再実行
curl -X POST \
  "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/skills/sync-content" \
  -H "Authorization: Bearer ${TOKEN}"
```

すべてのスキルは冪等（idempotent）であるため、再実行しても安全。

### テロップ欠落

一部のナレーション区間にテロップが生成されていない場合。

```bash
# add-telop を再実行
curl -X POST \
  "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/skills/add-telop" \
  -H "Authorization: Bearer ${TOKEN}"
```

### レイアウト修正

**スキル再実行（全体的な変更）:**
```bash
curl -X POST \
  "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/skills/layout" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"avatar_position": "bottom-left", "avatar_size": "medium"}'
```

**V1 API で個別クリップ調整:**
```bash
# 特定クリップの位置・スケール変更
curl -X PATCH \
  "${API_BASE}/api/ai/v1/projects/${PROJECT_ID}/clips/${CLIP_ID}/transform" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{
    "x": 700,
    "y": 350,
    "scale": 0.3,
    "options": {"include_diff": true}
  }'
```

### ギャップ問題

タイムラインに意図しない隙間がある場合。

```bash
# semantic close_gap でレイヤー内のギャップを詰める
curl -X POST \
  "${API_BASE}/api/ai/v1/projects/${PROJECT_ID}/semantic" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{
    "operation": "close_gap",
    "target_layer_id": "layer-content"
  }'
```

### クリップ個別修正

```bash
# クリップ移動
curl -X PATCH \
  "${API_BASE}/api/ai/v1/projects/${PROJECT_ID}/clips/${CLIP_ID}/move" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{
    "start_ms": 15000,
    "options": {"include_diff": true}
  }'

# クリップ削除
curl -X DELETE \
  "${API_BASE}/api/ai/v1/projects/${PROJECT_ID}/clips/${CLIP_ID}" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Idempotency-Key: $(uuidgen)"
```

---

## 7. Export

### Step 7.1: レンダリング開始

```bash
curl -X POST \
  "${API_BASE}/api/projects/${PROJECT_ID}/render" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{}'
```

部分エクスポート（範囲指定）の場合:
```bash
curl -X POST \
  "${API_BASE}/api/projects/${PROJECT_ID}/render" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"start_ms": 0, "end_ms": 60000}'
```

レスポンスで `render_job_id` が返る。レンダリングはバックグラウンドで実行される。

### Step 7.2: 進捗監視

```bash
curl -X GET \
  "${API_BASE}/api/projects/${PROJECT_ID}/render/status" \
  -H "Authorization: Bearer ${TOKEN}"
```

レスポンス:
```json
{
  "id": "render-job-uuid",
  "status": "processing",
  "progress": 45,
  "current_stage": "Rendering video"
}
```

`status` の遷移: `processing` -> `completed` / `failed` / `cancelled`

ポーリング間隔は 3-5 秒を推奨。

### Step 7.3: ダウンロード

レンダリング完了後:

```bash
curl -X GET \
  "${API_BASE}/api/projects/${PROJECT_ID}/render/download" \
  -H "Authorization: Bearer ${TOKEN}"
```

署名付き URL が返る（有効期限 24 時間）。

### Step 7.4: レンダリング取り消し

```bash
curl -X DELETE \
  "${API_BASE}/api/projects/${PROJECT_ID}/render" \
  -H "Authorization: Bearer ${TOKEN}"
```

### 最終品質確認

出力ファイルの検証項目:
- 解像度: 1920x1080
- フレームレート: 30fps
- コーデック: H.264（映像）+ AAC（音声）
- コンテナ: MP4
- Udemy 推奨形式に準拠していること

---

## 8. Troubleshooting

### エラーコード一覧

| エラーコード / 状況 | 原因 | 修正方法 |
|-------------------|------|---------|
| `404 No timeline data` | plan/apply が未実行 | `POST .../plan/apply` を実行 |
| `404 No video plan` | plan/generate が未実行 | `POST .../plan/generate` を実行 |
| `404 Project not found` | プロジェクトIDが不正またはアクセス権なし | PROJECT_ID とトークンを確認 |
| `400 No files provided` | batch-upload にファイルが添付されていない | multipart form data でファイルを送信 |
| `409 Concurrent modification` | 同時編集が発生 | If-Match ヘッダーで ETag を指定、リトライ |
| `409 Render job in progress` | レンダリングが既に実行中 | 完了を待つか `DELETE .../render` でキャンセル |
| `422 Validation error` | リクエストパラメータ不正 | `detail` メッセージを確認し修正 |
| `500 FFmpeg error` | レンダリングエンジンの内部エラー | ログ確認、クリップ設定確認（duration_ms > 0 等） |
| `500 Server error` | バックエンド内部エラー | リトライ（exponential backoff 推奨） |

### よくある問題と対処

**スキル実行で「No narration clips found」**
- plan/apply でナレーションアセットがタイムラインに配置されていない
- asset-catalog で subtype を確認し、必要なら reclassify する

**テロップが表示されない**
- add-telop がまだ実行されていない
- ナレーション音声の品質が低く STT が失敗した可能性。transcription エンドポイントで確認

**アバターの位置がおかしい**
- layout スキルを再実行（パラメータを調整）
- V1 API の transform エンドポイントで個別調整

**レンダリングが途中で失敗する**
- timeline に duration_ms が 0 のクリップがないか確認
- missing_assets ルールで参照切れアセットがないか確認
- render/history で過去のエラーメッセージを確認

**同期がずれている**
- sync-content を再実行
- add-telop が正しく完了していることを確認（sync-content は add-telop に依存）

### デバッグフロー

```
問題発生
  |
  v
1. validate-composition で品質チェック
  |
  v
2. timeline-overview で構造確認
  |
  v
3. sample-frame で視覚確認
  |
  v
4. 問題箇所を特定
  |
  v
5. スキル再実行 or V1 API で個別修正
  |
  v
6. 再検証（1に戻る）
```

---

## Quick Reference: 全エンドポイント一覧

### AI Video Production (`/api/ai-video/`)

| Method | Endpoint | 説明 |
|--------|----------|------|
| GET | `/capabilities` | ワークフロー・スキル仕様 |
| POST | `/projects/{id}/assets/batch-upload` | 素材一括アップロード |
| GET | `/projects/{id}/asset-catalog` | アセットカタログ |
| PUT | `/projects/{id}/assets/{aid}/reclassify` | アセット再分類 |
| GET | `/projects/{id}/assets/{aid}/transcription` | STT 結果取得 |
| POST | `/projects/{id}/plan/generate` | 計画生成 |
| GET | `/projects/{id}/plan` | 計画取得 |
| PUT | `/projects/{id}/plan` | 計画更新 |
| POST | `/projects/{id}/plan/apply` | 計画適用 |
| POST | `/projects/{id}/skills/trim-silence` | 無音トリミング |
| POST | `/projects/{id}/skills/add-telop` | テロップ追加 |
| POST | `/projects/{id}/skills/layout` | レイアウト適用 |
| POST | `/projects/{id}/skills/sync-content` | コンテンツ同期 |
| POST | `/projects/{id}/skills/click-highlight` | クリックハイライト |
| POST | `/projects/{id}/skills/avatar-dodge` | アバター回避 |
| POST | `/projects/{id}/skills/run-all` | 全スキル一括実行 |

### Preview (`/api/`)

| Method | Endpoint | 説明 |
|--------|----------|------|
| POST | `/projects/{id}/preview/sample-frame` | フレーム画像生成 |
| POST | `/projects/{id}/preview/event-points` | イベントポイント検出 |
| POST | `/projects/{id}/preview/sample-event-points` | イベントポイント + フレーム生成 |
| POST | `/projects/{id}/preview/validate` | 品質チェック（10 ルール） |

### Render (`/api/`)

| Method | Endpoint | 説明 |
|--------|----------|------|
| POST | `/projects/{id}/render` | レンダリング開始 |
| GET | `/projects/{id}/render/status` | 進捗確認 |
| GET | `/projects/{id}/render/download` | ダウンロード URL 取得 |
| GET | `/projects/{id}/render/history` | レンダリング履歴 |
| DELETE | `/projects/{id}/render` | レンダリングキャンセル |

### V1 API (`/api/ai/v1/`)

| Method | Endpoint | 説明 |
|--------|----------|------|
| GET | `/capabilities` | 機能一覧 |
| GET | `/projects/{id}/overview` | L1: プロジェクト概要 |
| GET | `/projects/{id}/structure` | L2: タイムライン構造 |
| GET | `/projects/{id}/timeline-overview` | L2.5: 全体概要 |
| GET | `/projects/{id}/assets` | アセット一覧 |
| GET | `/projects/{id}/clips/{cid}` | L3: クリップ詳細 |
| GET | `/projects/{id}/at-time/{ms}` | 特定時刻の状態 |
| POST | `/projects/{id}/clips` | クリップ追加 |
| PATCH | `/projects/{id}/clips/{cid}/move` | クリップ移動 |
| PATCH | `/projects/{id}/clips/{cid}/transform` | 位置・スケール変更 |
| PATCH | `/projects/{id}/clips/{cid}/effects` | エフェクト変更 |
| PATCH | `/projects/{id}/clips/{cid}/crop` | クロップ変更 |
| PATCH | `/projects/{id}/clips/{cid}/text-style` | テキストスタイル変更 |
| DELETE | `/projects/{id}/clips/{cid}` | クリップ削除 |
| POST | `/projects/{id}/semantic` | セマンティック操作 |
| POST | `/projects/{id}/batch` | バッチ操作 |
| GET | `/projects/{id}/history` | 操作履歴 |
| POST | `/projects/{id}/operations/{oid}/rollback` | ロールバック |
