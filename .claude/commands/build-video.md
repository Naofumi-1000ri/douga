# Build Video — 段階的動画構築スキル

動画タイムラインを6つのスキルで段階的に構築する。
**原則: 一発で動画をつくってはいけない。各ステップ後に結果を報告し確認を取る。**

## 引数

`$ARGUMENTS` にはプロジェクトIDを渡す。例: `/build-video 9a99517c-46f3-4409-90c8-693d5c6cb5f8`

## 実行手順

### 準備

1. プロジェクトIDを `$ARGUMENTS` から取得（未指定なら質問する）
2. API Base URL: `http://localhost:8000/api/ai-video`
3. 認証トークンは開発環境では `dev-token` を使用

### パイプライン

以下の順序で実行する。**各ステップ後に結果を報告し、次に進むか確認する。**

#### Step 0: apply_plan（前提）

```
POST /ai-video/projects/{id}/plan/apply
```

- タイムラインの基本構造を作成（レイヤー5層 + 音声トラック3本）
- アバター動画から音声を自動抽出してナレーショントラックに追加
- **確認ポイント**: レイヤー数、音声クリップ数を報告

#### Step 1: trim-silence

```
POST /ai-video/projects/{id}/skills/trim-silence
```

- ナレーション前後の無音区間をカット
- group_idでリンクされたアバタークリップも同時にトリム
- **確認ポイント**: トリムされたクリップ数、カットされた無音の長さ(ms)
- **スキップ条件**: ナレーションクリップがない場合は自動スキップ

#### Step 2: add-telop

```
POST /ai-video/projects/{id}/skills/add-telop
```

- ナレーション音声をSTT（Whisper）で文字起こし
- 各発話セグメントをtextレイヤーにテロップクリップとして配置
- 転写データを `timeline_data.metadata.transcription` に保存（他スキルが参照可能）
- **確認ポイント**: 生成されたテロップ数、セグメント数
- **スキップ条件**: ナレーションクリップがない場合、またはOPENAI_API_KEY未設定
- **必須**: OPENAI_API_KEY（Whisper API）

#### Step 3: layout

```
POST /ai-video/projects/{id}/skills/layout
```

- content (screen) → 全画面配置
- avatar → 右下 (x:400, y:250, scale:0.8) + クロマキー適用
- slide → 全画面中央配置
- **確認ポイント**: 配置されたクリップ数、アバターの有無

#### Step 4: sync-content

```
POST /ai-video/projects/{id}/skills/sync-content
```

- 操作画面をナレーション尺に合わせてスマートカット/速度調整
- アクティビティ分析 → 不活動区間カット → 残りを軽い速度アップ
- フォールバック: STTベースのスマートシンク
- **確認ポイント**: 生成されたサブクリップ数、速度調整の有無
- **スキップ条件**: 操作画面またはナレーションがない場合

#### Step 5: click-highlight

```
POST /ai-video/projects/{id}/skills/click-highlight
```

- 操作画面のクリック位置を検出
- effectsレイヤーにオレンジ枠(#FF6600)の矩形シェイプを配置
- **確認ポイント**: 検出されたクリック数、追加されたハイライト数

#### Step 6: avatar-dodge

```
POST /ai-video/projects/{id}/skills/avatar-dodge
```

- クリックハイライトとアバターが重なる場合、100msで回避移動
- アバターが右半分→左へ、左半分→右へ (-/+250px)
- **確認ポイント**: 追加された回避キーフレーム数
- **スキップ条件**: ハイライトまたはアバターがない場合

## 実行方法

各ステップのcurlコマンド:

```bash
BASE=http://localhost:8000/api/ai-video
AUTH="Authorization: Bearer dev-token"
PID={project_id}

# Step 0
curl -s -X POST "$BASE/projects/$PID/plan/apply" -H "$AUTH" | python3 -m json.tool

# Step 1-6
curl -s -X POST "$BASE/projects/$PID/skills/trim-silence" -H "$AUTH" | python3 -m json.tool
curl -s -X POST "$BASE/projects/$PID/skills/add-telop" -H "$AUTH" | python3 -m json.tool
curl -s -X POST "$BASE/projects/$PID/skills/layout" -H "$AUTH" | python3 -m json.tool
curl -s -X POST "$BASE/projects/$PID/skills/sync-content" -H "$AUTH" | python3 -m json.tool
curl -s -X POST "$BASE/projects/$PID/skills/click-highlight" -H "$AUTH" | python3 -m json.tool
curl -s -X POST "$BASE/projects/$PID/skills/avatar-dodge" -H "$AUTH" | python3 -m json.tool
```

## 報告フォーマット

各ステップ完了後、以下の形式で報告する:

```
【Step N: {スキル名}】{成功/失敗}
- {SkillResponse.message の内容}
- 処理時間: {duration_ms}ms
- 変更内容: {changes の要約}

→ 次のステップに進みますか？
```

全ステップ完了後:

```
【完了】動画タイムライン構築完了
- 総ステップ: N/6 実行
- スキップ: {スキップしたステップ}
- 最終duration: {duration_ms}ms

ブラウザでプレビューを確認してください。
```

## エラー処理

- 404 "No timeline data" → Step 0 (apply_plan) を先に実行するよう案内
- 404 "No video plan" → plan/generate を先に実行するよう案内
- 500エラー → ログ確認を案内 (`tail -f ~/.unitymcp/server.log` ではなく、バックエンドログを確認)
- 個別ステップの失敗は報告して次のステップに進めるか判断する

$ARGUMENTS
