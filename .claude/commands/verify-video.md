# Verify Video — ビルド後検証スキル

ビルドした動画タイムラインの品質をPDCA品質チェックで検証する。
**`/check` エンドポイントで一括検証し、PASS/FAILの総合判定を出す。**

## 引数

`$ARGUMENTS` にはプロジェクトIDを渡す。例: `/verify-video 9a99517c-46f3-4409-90c8-693d5c6cb5f8`

## 共通設定

```bash
# API Base URL
BASE=https://douga-api-344056413972.asia-northeast1.run.app/api/ai-video

# 認証ヘッダー
AUTH="Authorization: Bearer dev-token"

# プロジェクトID
PID={project_id}  # $ARGUMENTS から取得
```

## 検証手順

### Step 1: PDCA品質チェック実行（deep）

構造・同期・完全性・視覚品質を一括検証する:

```bash
curl -s -X POST "$BASE/projects/$PID/check" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"check_level": "deep", "max_visual_samples": 8, "resolution": "640x360"}' \
  | python3 -m json.tool
```

**レスポンス構造**:
- `scores`: `structure`, `sync`, `completeness`, `visual`, `overall`（各0-100）、`grade`（A-F）
- `issues`: 検出された問題リスト（severity, category, description, time_ms, recommended_action, requires_user_input）
- `material_requirements`: 不足素材の一覧（description, suggestions）
- `pass_threshold_met`: `overall >= 70 && critical == 0` なら `true`
- `iteration_recommendation`: `pass` / `auto_fixable` / `needs_user_input` / `needs_manual_review`

### Step 2: 品質スコア報告

スコアとグレードを報告する:

```
■ 品質スコア
  - 構造 (structure): {scores.structure}/100
  - 同期 (sync): {scores.sync}/100
  - 完全性 (completeness): {scores.completeness}/100
  - 視覚 (visual): {scores.visual}/100
  - 総合 (overall): {scores.overall}/100
  - グレード: {scores.grade}
```

### Step 3: Issue一覧報告

検出された各issueの詳細を報告する:

```
■ 検出された問題: {issues.length}件
  {issues を severity 順（critical → warning → info）で列挙}

  [{severity}] {category}: {description}
    時刻: {time_ms}ms
    推奨アクション: {recommended_action}
```

### Step 4: 不足素材の報告

`material_requirements` がある場合、不足素材を報告する:

```
■ 不足素材:
  - {material_requirements[].description}
    候補: {material_requirements[].suggestions}
```

`material_requirements` が空の場合はこのステップをスキップ。

### Step 5: 最終判定

`pass_threshold_met` に基づき総合判定を出す:

```
【Verify Video 結果】プロジェクト: {project_id}

■ 品質スコア: {scores.overall}/100（Grade: {scores.grade}）
  - 構造: {scores.structure} / 同期: {scores.sync} / 完全性: {scores.completeness} / 視覚: {scores.visual}

■ 検出された問題: {issues.length}件
  - critical: {N}件 / warning: {N}件 / info: {N}件
  {各issueの詳細}

■ 不足素材: {material_requirements.length}件
  {各素材の詳細（あれば）}

■ 総合判定: {pass_threshold_met ? "PASS" : "FAIL"}
■ 推奨: {iteration_recommendation}

■ 問題がある場合の推奨アクション:
  {issues 内の recommended_action を列挙}
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
