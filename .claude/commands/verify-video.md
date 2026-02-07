# Verify Video — ビルド後検証スキル

ビルドした動画タイムラインの品質を検証する。
**各ステップの結果を報告し、最終的にPASS/FAILの総合判定を出す。**

## 引数

`$ARGUMENTS` にはプロジェクトIDを渡す。例: `/verify-video 9a99517c-46f3-4409-90c8-693d5c6cb5f8`

## 準備

1. プロジェクトIDを `$ARGUMENTS` から取得（未指定なら質問する）
2. API Base URL: `https://douga-api-344056413972.asia-northeast1.run.app`
3. 認証トークンは開発環境では `dev-token` を使用

## 検証手順

### Step 1: validate-composition（10ルール品質チェック）

```
POST /api/projects/{id}/preview/validate
Content-Type: application/json
Authorization: Bearer dev-token

Body: {}  (全ルール実行。特定ルールのみ: {"rules": ["overlapping_clips", "audio_sync"]})
```

10個のバリデーションルール:

| # | ルール名 | チェック内容 | 重要度 |
|---|----------|-------------|--------|
| 1 | `overlapping_clips` | 同一レイヤー内のクリップ重なり | warning |
| 2 | `clip_bounds` | クリップがタイムライン範囲外に出ていないか | warning/error |
| 3 | `missing_assets` | 参照先アセットが存在するか | error |
| 4 | `safe_zone` | テロップ/アバターがセーフゾーン内か（5%マージン） | warning |
| 5 | `empty_layers` | 可視レイヤーにクリップがあるか | info |
| 6 | `audio_sync` | ナレーション区間に映像があるか | warning |
| 7 | `duration_consistency` | タイムライン尺と実コンテンツ尺の差（1秒以上で警告） | warning |
| 8 | `text_readability` | テロップの表示時間（200ms/語+500ms）・フォントサイズ（24px以上） | warning |
| 9 | `layer_ordering` | レイヤーのorder値が配列位置と一致するか | info |
| 10 | `gap_detection` | 映像なし区間（500ms以上のブランク）がないか | warning/error |

**結果を報告**: PASS/FAIL件数、error/warning/info別の内訳、各issueの内容

```bash
BASE=https://douga-api-344056413972.asia-northeast1.run.app
AUTH="Authorization: Bearer dev-token"
PID={project_id}

# 全ルール実行
curl -s -X POST "$BASE/api/projects/$PID/preview/validate" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{}' | python3 -m json.tool

# 特定ルールのみ
curl -s -X POST "$BASE/api/projects/$PID/preview/validate" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"rules": ["overlapping_clips", "audio_sync", "gap_detection"]}' | python3 -m json.tool
```

### Step 2: timeline-overview（構造監査）

```
GET /api/ai/v1/projects/{id}/timeline-overview
Authorization: Bearer dev-token
```

確認ポイント:
- 全5レイヤー（text, effects, avatar, content, background）にクリップがあるか
- 音声トラック構成（narration, bgm, se）が正しいか
- 各レイヤーのクリップ数とカバー範囲
- ギャップ（gaps）とオーバーラップ（overlaps）の検出結果
- warnings の内容を精査

```bash
curl -s -X GET "$BASE/api/ai/v1/projects/$PID/timeline-overview" \
  -H "$AUTH" | python3 -m json.tool
```

**報告**: レイヤー別クリップ数、音声トラック構成、warningsリスト

### Step 3: sample-event-points（主要フレーム視覚検査）

```
POST /api/projects/{id}/preview/sample-event-points
Content-Type: application/json
Authorization: Bearer dev-token

Body: {
  "max_samples": 10,
  "resolution": "640x360",
  "include_audio": true,
  "min_gap_ms": 500
}
```

確認ポイント:
- アバターの配置が正しいか（右下に表示されているか）
- テロップが読めるか（文字が切れていないか）
- 操作画面の表示タイミングがナレーションと合っているか
- レイアウト崩れがないか（要素の重なり、はみ出し）
- クロマキー合成の品質（緑の残像がないか）

```bash
curl -s -X POST "$BASE/api/projects/$PID/preview/sample-event-points" \
  -H "$AUTH" -H "Content-Type: application/json" \
  -d '{"max_samples": 10, "resolution": "640x360"}' | python3 -m json.tool
```

**報告**: サンプルフレーム数、各フレームのイベントタイプ、視覚的な問題の有無

### Step 4: 結果サマリ

全チェックの結果をまとめて以下のフォーマットで報告する:

```
【Verify Video 結果】プロジェクト: {project_id}

■ Step 1: validate-composition
  - 総合: {PASS/FAIL}
  - errors: {N}件 / warnings: {N}件 / info: {N}件
  - 主な問題:
    - {issue.rule}: {issue.message}
    - ...

■ Step 2: timeline-overview
  - レイヤー構成: {各レイヤーのクリップ数}
  - 音声トラック: {各トラックの状態}
  - Warnings: {warnings内容}

■ Step 3: sample-event-points
  - サンプル数: {N}フレーム
  - 視覚的問題: {問題の有無と内容}

■ 総合判定: {PASS / FAIL}

■ 問題がある場合の推奨アクション:
  - sync不一致 → `/fix-sync {project_id}`
  - テロップ欠落 → `add-telop` スキル再実行
  - レイアウト崩れ → `layout` スキル再実行
  - ギャップ検出 → semantic close_gap または手動調整
  - アセット不足 → アセットアップロード確認
  - セーフゾーン違反 → transform調整
```

## エラー処理

| エラー | 原因 | 対処 |
|--------|------|------|
| 400 "No timeline data" | タイムラインが未作成 | `/build-video` の Step 0 (apply_plan) を先に実行 |
| 404 Not Found | プロジェクトIDが無効 | IDを確認、`GET /api/ai/v1/projects` で一覧取得 |
| 401 Unauthorized | 認証トークンエラー | トークンを確認 |
| 500 Internal Server Error | サーバーエラー | Cloud Runログ確認: `gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=douga-api" --project=douga-2f6f8 --limit=20` |
| sample-event-pointsでframe_base64が空 | FFmpegが利用不可 | サーバー側のFFmpegインストールを確認 |

## トラブルシューティング

1. **validate-compositionで大量のerrorが出る場合**
   - `missing_assets` errorが多い → アセットが正しくアップロードされているか確認
   - `clip_bounds` errorが多い → `apply_plan` からやり直し

2. **timeline-overviewでwarningsが多い場合**
   - 各warningの内容を確認し、該当するスキルを再実行
   - `gap_detection` warnings → sync-content や手動クリップ調整

3. **sample-event-pointsで視覚的問題がある場合**
   - アバター位置 → `layout` スキル再実行
   - テロップ → `add-telop` スキル再実行
   - 操作画面のタイミング → `/fix-sync` で修正

4. **APIがタイムアウトする場合**
   - sample-event-pointsは `max_samples` を減らす（5以下）
   - `resolution` を `320x180` に下げる

$ARGUMENTS
