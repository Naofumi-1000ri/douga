# Build Video — 段階的動画構築スキル

動画タイムラインを4フェーズで段階的に構築する。
**原則: 一発で動画をつくってはいけない。各ステップ後に結果を報告し確認を取る。**

## 引数

`$ARGUMENTS` にはプロジェクトIDを渡す。例: `/build-video 9a99517c-46f3-4409-90c8-693d5c6cb5f8`

## 共通設定

```bash
# API Base URLs
BASE=https://douga-api-344056413972.asia-northeast1.run.app/api/ai-video
PREVIEW=https://douga-api-344056413972.asia-northeast1.run.app/api
AI_V1=https://douga-api-344056413972.asia-northeast1.run.app/api/ai/v1

# 認証ヘッダー
# 方法1: 開発用トークン
AUTH="Authorization: Bearer dev-token"
# 方法2: ブラウザのlocalStorageからFirebaseトークンを取得
# AUTH="Authorization: Bearer $(ブラウザから取得したトークン)"

# プロジェクトID
PID={project_id}  # $ARGUMENTS から取得
```

---

## Phase 1: Asset Preparation（素材準備）

### Step 1-1: 素材アップロード（batch-upload）

UIから既にアップロード済みならスキップ可能。CLI経由でアップロードする場合:

```bash
curl -s -X POST "$BASE/projects/$PID/assets/batch-upload" \
  -H "$AUTH" \
  -F "files=@avatar.mp4" \
  -F "files=@screen_capture.mp4" \
  -F "files=@slide01.png" \
  -F "files=@bgm.mp3" \
  | python3 -m json.tool
```

**確認ポイント**:
- `success` / `failed` の数
- 全ファイルのアップロード成功を確認

### Step 1-2: アセットカタログ確認

```bash
curl -s "$BASE/projects/$PID/asset-catalog" \
  -H "$AUTH" \
  | python3 -m json.tool
```

**確認ポイント**:
- 各アセットの `type` と `subtype` を確認
  - avatar: `video/avatar`
  - 操作画面: `video/screen`
  - スライド: `image/slide`
  - BGM: `audio/bgm`
  - ナレーション: `audio/narration`
  - 背景: `video/background` or `image/background`
- 分類が誤っている場合は再分類:

```bash
curl -s -X PUT "$BASE/projects/$PID/assets/{asset_id}/reclassify" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"subtype": "avatar"}' \
  | python3 -m json.tool
```

### Step 1-3: プラン生成（必要な場合のみ）

既にプランが存在するか確認:

```bash
curl -s "$BASE/projects/$PID/plan" \
  -H "$AUTH" \
  | python3 -m json.tool
```

プランが存在しない場合、生成する:

```bash
curl -s -X POST "$BASE/projects/$PID/plan/generate" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{
    "brief": {
      "title": "講座タイトル",
      "description": "講座の説明",
      "style": "tutorial",
      "target_duration_seconds": 300,
      "language": "ja"
    }
  }' \
  | python3 -m json.tool
```

**確認ポイント**:
- プランの `sections` 構成が意図通りか
- 各セクションで使用されるアセットが正しいか

**Phase 1 完了報告 → ユーザーに確認を取ってから Phase 2 へ進む**

---

## Phase 2: Timeline Construction（タイムライン構築）

### Step 2-0: plan/apply（タイムライン基本構造作成）

```bash
curl -s -X POST "$BASE/projects/$PID/plan/apply" \
  -H "$AUTH" \
  | python3 -m json.tool
```

**確認ポイント**:
- `layers_populated`: レイヤー数（通常5層 + 音声トラック3本）
- `audio_clips_added`: 音声クリップ数
- `duration_ms`: 全体の長さ

### スキル実行: 2つの方法

#### 方法A: 一括実行（run-all）

全6スキルを依存関係順に一括実行する。手早く結果を見たい場合に推奨:

```bash
curl -s -X POST "$BASE/projects/$PID/skills/run-all" \
  -H "$AUTH" \
  | python3 -m json.tool
```

**レスポンス構造**:
- `success`: 全スキル成功なら `true`
- `results`: 各スキルの結果配列（skill名, success, message, duration_ms, changes）
- `failed_at`: 失敗したスキル名（全成功なら `null`）
- `total_duration_ms`: 合計処理時間

layoutにカスタム設定を渡す場合:

```bash
curl -s -X POST "$BASE/projects/$PID/skills/run-all" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{
    "avatar_position": "bottom-left",
    "avatar_size": "pip",
    "screen_position": "fullscreen"
  }' \
  | python3 -m json.tool
```

#### 方法B: 個別実行（推奨: 段階的確認）

各ステップ後に結果を報告し、次に進むか確認する。

##### Step 2-1: trim-silence

```bash
curl -s -X POST "$BASE/projects/$PID/skills/trim-silence" \
  -H "$AUTH" \
  | python3 -m json.tool
```

- ナレーション前後の無音区間をカット
- group_idでリンクされたアバタークリップも同時にトリム
- **確認ポイント**: トリムされたクリップ数、カットされた無音の長さ(ms)
- **スキップ条件**: ナレーションクリップがない場合は自動スキップ

##### Step 2-2: add-telop

```bash
curl -s -X POST "$BASE/projects/$PID/skills/add-telop" \
  -H "$AUTH" \
  | python3 -m json.tool
```

- ナレーション音声をSTT（Whisper）で文字起こし
- 各発話セグメントをtextレイヤーにテロップクリップとして配置
- 転写データを `timeline_data.metadata.transcription` に保存（他スキルが参照可能）
- **確認ポイント**: 生成されたテロップ数、セグメント数
- **スキップ条件**: ナレーションクリップがない場合、またはOPENAI_API_KEY未設定
- **必須**: OPENAI_API_KEY（Whisper API）

##### Step 2-3: layout

```bash
curl -s -X POST "$BASE/projects/$PID/skills/layout" \
  -H "$AUTH" \
  | python3 -m json.tool
```

カスタム配置を指定する場合:

```bash
curl -s -X POST "$BASE/projects/$PID/skills/layout" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{
    "avatar_position": "bottom-right",
    "avatar_size": "pip",
    "screen_position": "fullscreen"
  }' \
  | python3 -m json.tool
```

- content (screen) → 全画面配置
- avatar → 右下 (x:400, y:250, scale:0.8) + クロマキー適用
- slide → 全画面中央配置
- **avatar_position** の選択肢: `bottom-right`, `bottom-left`, `top-right`, `top-left`, `center-right`, `center-left`
- **avatar_size** の選択肢: `pip`, `medium`, `large`, `fullscreen`
- **screen_position** の選択肢: `fullscreen`, `left-half`, `right-half`
- **確認ポイント**: 配置されたクリップ数、アバターの有無

##### Step 2-4: sync-content

```bash
curl -s -X POST "$BASE/projects/$PID/skills/sync-content" \
  -H "$AUTH" \
  | python3 -m json.tool
```

- 操作画面をナレーション尺に合わせてスマートカット/速度調整
- アクティビティ分析 → 不活動区間カット → 残りを軽い速度アップ
- 発話区間は通常速度、無音区間は加速（2.5倍）
- フォールバック: STTベースのスマートシンク
- **確認ポイント**: 生成されたサブクリップ数、速度調整の有無
- **スキップ条件**: 操作画面またはナレーションがない場合
- **依存**: add-telopの転写データを使用

##### Step 2-5: click-highlight

```bash
curl -s -X POST "$BASE/projects/$PID/skills/click-highlight" \
  -H "$AUTH" \
  | python3 -m json.tool
```

- 操作画面のクリック位置を検出
- effectsレイヤーにオレンジ枠(#FF6600)の矩形シェイプを配置
- **確認ポイント**: 検出されたクリック数、追加されたハイライト数

##### Step 2-6: avatar-dodge

```bash
curl -s -X POST "$BASE/projects/$PID/skills/avatar-dodge" \
  -H "$AUTH" \
  | python3 -m json.tool
```

- クリックハイライトとアバターが重なる場合、100msで回避移動
- アバターが右半分→左へ、左半分→右へ (-/+250px)
- **確認ポイント**: 追加された回避キーフレーム数
- **スキップ条件**: ハイライトまたはアバターがない場合
- **依存**: click-highlightの結果を使用

**Phase 2 完了報告 → Phase 3 へ**

---

## Phase 3: Verification（検証）

### Step 3-1: コンポジション品質チェック（validate-composition）

```bash
curl -s -X POST "$PREVIEW/projects/$PID/preview/validate" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{}' \
  | python3 -m json.tool
```

特定ルールのみチェックする場合:

```bash
curl -s -X POST "$PREVIEW/projects/$PID/preview/validate" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"rules": ["missing_assets", "overlapping_clips", "safe_zone"]}' \
  | python3 -m json.tool
```

**レスポンス構造**:
- `is_valid`: エラー0件なら `true`
- `issues`: 検出された問題のリスト（rule, severity, message, time_ms, suggestion）
- `errors`: エラー数
- `warnings`: 警告数

**確認ポイント**:
- `is_valid` が `true` であること
- `errors` が 0 であること（warnings は許容可）
- 各 issue の `suggestion` に従って修正を検討

### Step 3-2: タイムライン構造確認（timeline-overview）

```bash
curl -s "$AI_V1/projects/$PID/timeline-overview" \
  -H "$AUTH" \
  | python3 -m json.tool
```

**確認ポイント**:
- 全クリップのアセット名と配置時間
- ギャップ（空き）がないか
- オーバーラップの有無
- 警告メッセージの確認

### Step 3-3: 主要フレーム視覚検証（sample-event-points）

```bash
curl -s -X POST "$PREVIEW/projects/$PID/preview/sample-event-points" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{
    "max_samples": 10,
    "resolution": "640x360",
    "include_audio": true,
    "min_gap_ms": 500
  }' \
  | python3 -m json.tool
```

**レスポンス構造**:
- `samples`: 各イベントポイントの画像（base64エンコードされたJPEG）
- `total_events`: 検出されたイベント総数
- `sampled_count`: 実際にサンプルされた数

特定時刻のフレームだけ確認する場合:

```bash
curl -s -X POST "$PREVIEW/projects/$PID/preview/sample-frame" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"time_ms": 5000, "resolution": "640x360"}' \
  | python3 -m json.tool
```

**確認ポイント**:
- アバターの位置が正しいか
- テロップが読めるか
- 操作画面が見切れていないか
- レイヤーの重なり順が正しいか

**Phase 3 完了報告 → 問題があれば Phase 4 へ、なければ完了**

---

## Phase 4: Correction Loop（修正ループ）

問題が見つかった場合の修正手順:

### 4-1: 問題の特定

Phase 3 の結果から、どのスキルで問題が起きたかを特定する:

| 問題 | 原因スキル | 再実行対象 |
|------|-----------|-----------|
| テロップがずれている | add-telop | add-telop |
| アバター位置が悪い | layout | layout（パラメータ変更） |
| 操作画面と音声が同期しない | sync-content | sync-content |
| クリックハイライトが多すぎる/少ない | click-highlight | click-highlight |
| アバターがハイライトと被る | avatar-dodge | avatar-dodge |
| 無音が残っている | trim-silence | trim-silence |

### 4-2: 特定スキルの再実行

各スキルはべき等（idempotent）なので、何度でも再実行可能:

```bash
# 例: sync-contentだけ再実行
curl -s -X POST "$BASE/projects/$PID/skills/sync-content" \
  -H "$AUTH" \
  | python3 -m json.tool
```

```bash
# 例: layoutをパラメータ変更して再実行
curl -s -X POST "$BASE/projects/$PID/skills/layout" \
  -H "$AUTH" \
  -H "Content-Type: application/json" \
  -d '{"avatar_position": "bottom-left", "avatar_size": "medium"}' \
  | python3 -m json.tool
```

### 4-3: 再検証

修正後、Phase 3 の検証を再実行して問題が解消されたことを確認する。

---

## 報告フォーマット

### 各ステップ完了後

```
【Step N: {スキル名}】{成功/失敗}
- {SkillResponse.message の内容}
- 処理時間: {duration_ms}ms
- 変更内容: {changes の要約}

→ 次のステップに進みますか？
```

### run-all 実行後

```
【run-all 完了】{成功/一部失敗}
- 成功: N/6 スキル
- 失敗: {failed_at}（あれば）
- 合計処理時間: {total_duration_ms}ms

各スキル結果:
  1. trim-silence: {success} ({duration_ms}ms)
  2. add-telop: {success} ({duration_ms}ms)
  3. layout: {success} ({duration_ms}ms)
  4. sync-content: {success} ({duration_ms}ms)
  5. click-highlight: {success} ({duration_ms}ms)
  6. avatar-dodge: {success} ({duration_ms}ms)

→ Phase 3（検証）に進みますか？
```

### 全フェーズ完了後

```
【完了】動画タイムライン構築完了
- Phase 1: アセット {N}件準備完了
- Phase 2: スキル {N}/6 実行成功
- Phase 3: バリデーション {is_valid ? "合格" : "要修正"}
- Phase 4: 修正 {N}回実施（修正ありの場合）
- 最終duration: {duration_ms}ms

ブラウザでプレビューを確認してください。
```

---

## エラー処理・トラブルシューティング

### よくあるエラー

| エラー | 原因 | 対処 |
|--------|------|------|
| 404 "Project not found" | プロジェクトIDが間違い、または認証トークンが別ユーザー | PIDと認証を確認 |
| 404 "No timeline data" | plan/apply 未実行 | Phase 2 Step 2-0 を先に実行 |
| 404 "No video plan" | plan/generate 未実行 | Phase 1 Step 1-3 を先に実行 |
| 400 "No timeline data in project" | タイムラインが空 | plan/apply を実行 |
| 500 Internal Server Error | バックエンド障害 | Cloud Runログを確認（下記コマンド） |

### sync-content 失敗時の診断

sync-content は最も複雑なスキルで、失敗しやすい。診断手順:

1. **前提条件チェック**: add-telop が成功しているか確認
   ```bash
   # 転写データ確認
   curl -s "$BASE/projects/$PID/asset-catalog" -H "$AUTH" | python3 -c "
   import json,sys
   d=json.load(sys.stdin)
   for a in d.get('assets',[]):
       if a.get('subtype')=='narration':
           print(f'Narration: {a[\"id\"]} duration={a.get(\"duration_ms\")}ms')
   "
   ```

2. **操作画面の存在チェック**: content (screen) アセットがあるか
   ```bash
   curl -s "$BASE/projects/$PID/asset-catalog" -H "$AUTH" | python3 -c "
   import json,sys
   d=json.load(sys.stdin)
   for a in d.get('assets',[]):
       if a.get('subtype')=='screen':
           print(f'Screen: {a[\"id\"]} duration={a.get(\"duration_ms\")}ms')
   "
   ```

3. **アセット分類の確認**: screen が正しく分類されているか
   - 分類ミスの場合は `reclassify` APIで修正

### Cloud Run ログの確認

```bash
gcloud logging read \
  "resource.type=cloud_run_revision AND resource.labels.service_name=douga-api AND severity>=ERROR" \
  --project=douga-2f6f8 --limit=20 \
  --format="table(timestamp,textPayload)"
```

### 個別ステップ失敗時の方針

- **スキル失敗は報告して判断を仰ぐ**（自動で次に進まない）
- スキルはべき等なので、リトライ可能
- run-all で途中失敗した場合、失敗スキルだけ個別に再実行できる
- 依存関係に注意: sync-content は add-telop 後、avatar-dodge は click-highlight 後

### スキル依存関係グラフ

```
trim-silence ──────────────────────────┐
add-telop ──────┬──────────────────────┤
layout ─────────┤                      ├─→ (完了)
sync-content ←──┘ (add-telopに依存)    │
click-highlight ──┬────────────────────┤
avatar-dodge ←────┘ (click-highlightに依存)
```

$ARGUMENTS
