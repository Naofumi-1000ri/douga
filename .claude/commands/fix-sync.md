# Fix Sync — ナレーション・操作映像同期修正スキル

ナレーションと操作映像のsync問題を診断・修正する。
**問題を特定し、決定木に従って適切な修正アクションを実行する。**

## 引数

`$ARGUMENTS` にはプロジェクトIDを渡す。例: `/fix-sync 9a99517c-46f3-4409-90c8-693d5c6cb5f8`

## 準備

1. プロジェクトIDを `$ARGUMENTS` から取得（未指定なら質問する）
2. API Base URL: `https://douga-api-344056413972.asia-northeast1.run.app`
3. 認証トークンは開発環境では `dev-token` を使用

## 診断手順

### Step 1: asset-catalog検査

```
GET /api/ai/v1/projects/{id}/assets
Authorization: Bearer dev-token
```

確認:
- 各アセットの `subtype` が正しく分類されているか
  - `avatar`: アバター動画（グリーンバック）
  - `content`: 操作画面キャプチャ
  - `slide`: スライド画像/動画
  - `narration`: ナレーション音声
  - `bgm`: BGM音声
  - `se`: 効果音
- 間違った subtype → PATCH で修正が必要（Step 5 の決定木で対応）

```bash
BASE=https://douga-api-344056413972.asia-northeast1.run.app
AUTH="Authorization: Bearer dev-token"
PID={project_id}

curl -s -X GET "$BASE/api/ai/v1/projects/$PID/assets" \
  -H "$AUTH" | python3 -m json.tool
```

**報告**: アセット一覧（ID, ファイル名, type, subtype）、分類ミスの有無

### Step 2: timeline-overview で現状確認

```
GET /api/ai/v1/projects/{id}/timeline-overview
Authorization: Bearer dev-token
```

確認:
- ナレーション音声クリップの timing（start_ms, duration_ms）
- コンテンツ映像クリップの timing
- アバタークリップの timing
- テロップクリップの timing
- 各クリップ間の gap/overlap
- group_id によるリンク関係（ナレーション音声とアバター動画のペア）

```bash
curl -s -X GET "$BASE/api/ai/v1/projects/$PID/timeline-overview" \
  -H "$AUTH" | python3 -m json.tool
```

**報告**: 各レイヤー/トラックのクリップ timing 一覧、gap/overlap箇所

### Step 3: transcription データ確認

```
GET /api/ai/v1/projects/{id}/structure
Authorization: Bearer dev-token
```

確認:
- `timeline_data.metadata.transcription` の有無
- transcription がある場合: セグメント数、各セグメントの時間範囲とテキスト
- transcription がない場合: `add-telop` スキル未実行の可能性

```bash
curl -s -X GET "$BASE/api/ai/v1/projects/$PID/structure" \
  -H "$AUTH" | python3 -m json.tool
```

**報告**: transcription の有無、セグメント数、カバー時間範囲

### Step 4: 尺比較分析

Step 1-3 で取得したデータを基に、以下の尺を比較:

| 項目 | 確認元 | 期待値 |
|------|--------|--------|
| ナレーション音声の総尺 | audio_tracks (narration) の合計duration_ms | 基準尺 |
| コンテンツソース映像の元尺 | assets のcontent動画メタデータ | 参考値 |
| sync-content後のサブクリップ合計尺 | layers (content) の合計duration_ms | ナレーション尺に近い |
| アバタークリップの合計尺 | layers (avatar) の合計duration_ms | ナレーション尺に近い |
| テロップの合計カバー範囲 | layers (text) のクリップ範囲 | ナレーション尺をカバー |
| タイムライン全体の duration_ms | timeline_data.duration_ms | 最長クリップに合致 |

**差異の判定基準**:
- ナレーション尺 vs コンテンツ尺: 10%以上の差 → sync-content の再実行が必要
- ナレーション尺 vs アバター尺: 500ms以上の差 → trim-silence の確認が必要
- テロップカバー率: 80%未満 → add-telop の再実行が必要

**報告**: 各尺の数値、差異の割合、要調整の有無

### Step 5: 決定木による修正方針

診断結果に基づき、以下の決定木で修正方針を決定する:

```
問題の診断結果
│
├─ アセット分類が間違っている
│   └→ アセットsubtypeをPATCH修正
│      → plan再生成: POST /api/ai-video/projects/{id}/plan/generate
│      → plan適用:   POST /api/ai-video/projects/{id}/plan/apply
│      → 本スキル再実行 (/fix-sync)
│
├─ テロップが欠落している（transcription なし）
│   └→ add-telop スキル再実行
│      POST /api/ai-video/projects/{id}/skills/add-telop
│
├─ 操作画面がナレーション尺に合っていない
│   │
│   ├─ コンテンツクリップが全く配置されていない
│   │   └→ layout → sync-content の順で再実行
│   │      POST /api/ai-video/projects/{id}/skills/layout
│   │      POST /api/ai-video/projects/{id}/skills/sync-content
│   │
│   └─ sync-content済みだが尺がずれている
│       └→ sync-content スキル再実行
│          POST /api/ai-video/projects/{id}/skills/sync-content
│
├─ アバターとナレーションがずれている
│   │
│   ├─ group_id が正しくリンクされていない
│   │   └→ apply_plan からやり直し
│   │      POST /api/ai-video/projects/{id}/plan/apply
│   │
│   └─ group_id はあるが timing がずれている
│       └→ trim-silence 再実行
│          POST /api/ai-video/projects/{id}/skills/trim-silence
│          → group_id 確認（ナレーションとアバターが同じ group_id を持つこと）
│
└─ 複合的な問題
    └→ パイプライン全体をやり直し（/build-video）
```

**修正のcurlコマンド例**:

```bash
# アセットsubtype修正（例: content → narration に変更）
# ※ アセットPATCHエンドポイントで修正

# plan再生成 → 適用
curl -s -X POST "$BASE/api/ai-video/projects/$PID/plan/generate" \
  -H "$AUTH" | python3 -m json.tool
curl -s -X POST "$BASE/api/ai-video/projects/$PID/plan/apply" \
  -H "$AUTH" | python3 -m json.tool

# 個別スキル再実行
curl -s -X POST "$BASE/api/ai-video/projects/$PID/skills/trim-silence" \
  -H "$AUTH" | python3 -m json.tool
curl -s -X POST "$BASE/api/ai-video/projects/$PID/skills/add-telop" \
  -H "$AUTH" | python3 -m json.tool
curl -s -X POST "$BASE/api/ai-video/projects/$PID/skills/layout" \
  -H "$AUTH" | python3 -m json.tool
curl -s -X POST "$BASE/api/ai-video/projects/$PID/skills/sync-content" \
  -H "$AUTH" | python3 -m json.tool
```

### Step 6: 修正後の再検証

修正実行後、`/verify-video` で品質再確認する:

```
/verify-video {project_id}
```

再検証で問題が残っている場合は、本スキルを再度実行して診断をやり直す。

## 報告フォーマット

各ステップの結果を以下の形式でまとめる:

```
【Fix Sync 診断結果】プロジェクト: {project_id}

■ Step 1: アセット分類
  - アセット数: {N}件
  - 分類ミス: {あり/なし}
    - {asset_id}: {現在のsubtype} → {正しいsubtype}

■ Step 2: タイムライン構造
  - ナレーション: {N}クリップ ({total_ms}ms)
  - コンテンツ: {N}クリップ ({total_ms}ms)
  - アバター: {N}クリップ ({total_ms}ms)
  - テロップ: {N}クリップ ({total_ms}ms)
  - Gap箇所: {N}箇所（計{total_gap_ms}ms）
  - Overlap箇所: {N}箇所

■ Step 3: Transcription
  - 状態: {あり (N segments) / なし}

■ Step 4: 尺比較
  - ナレーション総尺: {N}ms
  - コンテンツ総尺: {N}ms (差異: {diff}%)
  - アバター総尺: {N}ms (差異: {diff}ms)
  - テロップカバー率: {N}%

■ Step 5: 修正方針
  - 問題の種類: {問題の説明}
  - 影響範囲: {何クリップ、何秒分}
  - 推奨修正アクション: {具体的なアクション}

→ 修正を実行しますか？
```

修正実行後:

```
【Fix Sync 修正完了】

- 実行したアクション: {アクション内容}
- 修正前: {修正前の状態}
- 修正後: {修正後の状態}

→ /verify-video で再検証を実行しますか？
```

## エラー処理

| エラー | 原因 | 対処 |
|--------|------|------|
| 400 "No timeline data" | タイムラインが未作成 | `/build-video` の Step 0 (apply_plan) を先に実行 |
| 404 Not Found | プロジェクトIDが無効 | IDを確認 |
| 401 Unauthorized | 認証トークンエラー | トークンを確認 |
| assets が 0件 | アセット未アップロード | プロジェクトにファイルをアップロードしてから再実行 |
| transcription が null | add-telop 未実行 | add-telop スキルを先に実行 |
| sync-content で 500エラー | FFmpegエラーまたはアセットアクセス不可 | Cloud Runログ確認 |

## トラブルシューティング

1. **修正しても sync が直らない場合**
   - `apply_plan` からパイプライン全体をやり直す（`/build-video`）
   - アセットファイル自体に問題がないか確認（破損、無音ファイル等）

2. **group_id が見つからない場合**
   - `apply_plan` が正しく実行されていない可能性
   - ナレーション音声とアバター動画のペアが正しくアップロードされているか確認

3. **コンテンツ映像の元尺が取得できない場合**
   - アセットメタデータにduration情報がない → 直接ファイルをffprobeで確認
   - GCSのファイルURLを取得して確認

4. **複数のナレーション/コンテンツがある場合**
   - 各ペアを個別に尺比較する
   - 順序（order）が正しいか確認

$ARGUMENTS
