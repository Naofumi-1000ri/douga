# Checkpoint: #080 V1 API テストレポート

## 日時
2026-02-05

## テスト概要
V1 APIの動作確認テスト（デプロイ後）

---

## テスト環境

- **API URL**: https://douga-api-344056413972.asia-northeast1.run.app
- **認証**: X-API-Key ヘッダー
- **Project ID**: `e035c521-60a1-4839-995a-b1ed1b06ccb9`
- **デプロイ版**: `douga-api-00265-ksk` (2026-02-05)

---

## テスト結果サマリー

### ✅ 成功した機能

| 機能 | エンドポイント | 結果 |
|------|---------------|------|
| プロジェクト作成 | `POST /api/projects` | ✅ |
| アセットアップロード | `POST /assets/upload-url` + `POST /assets` | ✅ |
| メタデータ検証 | file_size, width, height, duration_ms | ✅ 全一致 |
| レイヤー作成 | `POST /api/ai/v1/projects/{id}/layers` | ✅ |
| クリップ追加 | `POST /api/ai/v1/projects/{id}/clips` | ✅ |
| クリップ削除 | `DELETE /api/ai/v1/projects/{id}/clips/{id}` | ✅ |
| 音声抽出 | `POST /assets/{id}/extract-audio` | ✅ |
| オーディオクリップ追加 | `POST /api/ai/v1/projects/{id}/audio-clips` | ✅ |
| Trim操作 | `POST /batch` with `trim` | ✅ |
| Effects更新 | `PATCH /clips/{id}/effects` | ✅ |
| Transform更新 | `PATCH /clips/{id}/transform` | ✅ |
| テキストクリップ作成 | `POST /api/ai/v1/projects/{id}/clips` (text_content) | ✅ |
| プレビューフレーム取得 | `POST /projects/{id}/preview/sample-frame` | ✅ base64画像返却 |
| STT+スライド自動配置 | AI考察による内容マッチング | ✅ ミスマッチ検出・削除・再配置 |

---

## 発見された問題

### 🔴 レイヤー順序がフロントエンドと不一致

**問題**: V1 API `POST /layers` で `insert_at` を使用すると、`order`フィールドが正しく設定されない

**検証結果**:
```
V1 API layers:          Raw layers (order順 = Frontend):
  [0] Text              order=5 スライド  ← 最上位!
  [1] Effects           order=4 Text
  [2] スライド           order=3 Effects
  [3] Avatar            order=2 Avatar
  [4] Content           order=1 Content
  [5] Background        order=0 Background
```

**追加検証**:
フロントエンドUIでスライドレイヤーを正しい位置に移動後、再度比較:
→ **フロントエンドUIでの並べ替えは`order`を正しく更新**するため、V1 APIと一致する

**結論**:
- フロントエンドUIでの並べ替え: ✅ 正常
- V1 API `insert_at`での挿入: 🔴 `order`フィールド不整合

**影響**:
- AIがV1 APIで`insert_at`を使ってレイヤーを挿入すると、フロントエンドとAPIの状態が乖離する

**提案**:
- `POST /layers`の`insert_at`使用時に、既存レイヤーの`order`を再計算して正しい順序を維持すべき

---

### 🟡 座標系の認識（AI向けドキュメント要更新）

**問題**: llms-full.txt の座標系説明が実際の動作と異なる

**llms-full.txt の記載**:
```
| y | float | 0 | -540 to 540 | Pixels from center |
```

**実際の動作**:
```
y = 正の値 → 下方向（bottom）
y = 負の値 → 上方向（top）
```

**AIの誤解**:
- 「右下」に配置しようとして `y = -405` を指定 → 実際は右上に配置された
- 正しくは `y = 405` で右下

**修正提案**:
llms-full.txt の Transform セクションを以下のように更新:
```
| Property | Type | Default | Range | Description |
|----------|------|---------|-------|-------------|
| x | float | 0 | -960 to 960 | Pixels from center (positive = right) |
| y | float | 0 | -540 to 540 | Pixels from center (positive = down, negative = up) |
```

---

### 🔴 アセット画像直接取得API

**問題**: アセット単体の画像/サムネイルをAPI経由でbase64として取得する方法がない

**現状**:
- `GET /api/projects/{id}/assets` → `storage_url`（署名付きURL）を返す
- 画像データ取得には署名付きURLへの別途HTTPリクエストが必要
- AIエージェントがAPIのみでアセット画像を「見る」ことができない

**影響**:
- MCP経由のAIアシスタントがアセットの内容を確認できない
- 画像ベースの自動判断（クロマキー色推定、内容分析等）が不可能

**提案**:
```
GET /api/ai/v1/projects/{project_id}/assets/{asset_id}/preview
Response: { "asset_id": "...", "image_base64": "...", "mime_type": "image/png" }
```

---

### 🔴 クロップ更新エンドポイントの欠如（AI-Friendly問題）

**問題**: V1 APIでクリップのクロップを設定できない

**検証結果**:
```
Grep "crop" in src/api/ → No matches
Grep "crop" in src/render/pipeline.py → Found (レンダラーは crop を読む)
```

**AIの誤判断パターン**:
```
1. APIにcropエンドポイントなし
2. レンダラーがtimeline_data.clip.cropを読む
3. 推論: PUT /timeline でcropを書けば動くはず ← 誤り
```

**問題の本質**:
- レンダラーが読む ≠ APIが保存する
- スキーマに `crop` フィールドが定義されていない可能性
- AIは実装詳細から逆推論しがち（他のAIも同様の誤りをする可能性高）

**AI-Friendly改善案**:
1. `PATCH /api/ai/v1/projects/{id}/clips/{clip_id}/crop` エンドポイント追加
2. `/structure` レスポンスに `supported_clip_properties` を含める
3. llms-full.txt に対応・非対応プロパティを明記

**提案API**:
```
PATCH /api/ai/v1/projects/{project_id}/clips/{clip_id}/crop
Request: {
  "crop": {
    "top": 10,      // pixels
    "right": 10,
    "bottom": 10,
    "left": 10
  }
}
```

---

### 🟡 Preview APIにレイヤーフィルター機能がない

**問題**: 特定レイヤーのみのプレビュー画像を取得できない

**現状**:
```
POST /preview/sample-frame
Request: {"time_ms": 5000, "resolution": "640x360"}
Response: 全レイヤー合成画像（アバター、テキスト等すべて含む）
```

**影響**:
- AIが「スライドレイヤーだけ」の内容を確認できない
- 動画クリップの特定レイヤーのみの確認が不可能
- レイヤー単位の品質チェックができない

**ユースケース**:
- スライドの内容確認（他レイヤーなし）
- アバター動画の表情・動き確認（背景なし）
- Content動画の操作画面確認（アバターなし）

**提案**:
```
POST /api/projects/{id}/preview/sample-frame
Request: {
  "time_ms": 5000,
  "resolution": "640x360",
  "layers": ["スライド"],      // 特定レイヤーのみ
  "exclude_layers": ["Text"]   // または除外指定
}
```

---

### 🟡 STT機能のAPIキー設定

**問題**: STT APIがプロジェクト単位のAPIキーを使用しない

**現状**:
- `POST /api/transcription` は存在する（OpenAI Whisper API使用）
- サーバー環境変数 `OPENAI_API_KEY` を使用
- プロジェクトの `ai_api_key` フィールドは無視される
- リクエストでAPIキーを渡す方法がない

**テスト結果**:
```
Status: completed
Error: OPENAI_API_KEY not configured
```

**回避策**:
ローカルでOpenAI APIを直接呼び出し → テキストクリップとして配置

**提案**:
1. プロジェクト単位の `ai_api_key` を使用
2. または V1 API に STT エンドポイントを追加（リクエストでキー指定可）

---

### 🔴 テキストスタイル更新エンドポイントの欠如

**問題**: V1 APIで既存テキストクリップの`text_style`を更新できない

**検証結果**:
```
POST /clips (作成時)           → text_style ✅ 設定可能
PATCH /clips/{id}/transform   → text_style ❌ 無視される
PATCH /clips/{id}/effects     → text_style ❌ 無視される
Batch update_transform        → text_style ❌ 非対応
Batch update_effects          → text_style ❌ 非対応
```

**影響**:
- フォントサイズ変更不可
- 背景色・透明度変更不可
- 文字色変更不可

**現状の回避策**:
- `PUT /projects/{id}/timeline` で timeline_data 全体を更新（非推奨）
- クリップを削除→再作成

**提案**:
```
PATCH /api/ai/v1/projects/{project_id}/clips/{clip_id}/text-style
Request: {
  "text_style": {
    "font_size": 96,
    "background_color": "#000000",
    "background_opacity": 0.3
  }
}
```

---

### 🔴 クリップ分割（スプリット/カット）機能の欠如

**問題**: V1 APIにクリップを特定時点で分割する機能がない

**現在のBatch Operations**:
```python
operation: Literal["add", "move", "trim", "update_transform", "update_effects", "delete", "update_layer"]
```

**回避策（現状）**:
1. 元クリップを `trim` で前半部分に縮小
2. 新規クリップを `add` で作成（`in_point_ms` で後半開始点を指定）
3. エフェクト・トランスフォーム設定を手動コピー

**問題点**:
- プロパティ（クロマキー設定、transform等）の手動コピーが必要
- コピー漏れ・設定ズレが発生しやすい
- AIエージェントにとって複雑な操作が必要

**提案**:
```
POST /api/ai/v1/projects/{project_id}/clips/{clip_id}/split
Request: { "split_at_ms": 35000 }
Response: {
  "front_clip": { ... },  // 0 - split_at_ms
  "rear_clip": { ... }    // split_at_ms - end（プロパティ自動継承）
}
```

---

## テスト構成（最終状態）

### Video Layers
| Layer | Clips | Time |
|-------|-------|------|
| Text | 24 | 0-35s, 50-204s (STT自動生成テロップ) |
| Effects | 0 | - |
| スライド | 4 | 0-20s |
| Avatar | 2 | 0-35s, 50-212s (クロマキー有効, 右下1/2サイズ) |
| Content | 1 | 0-196.7s |
| Background | 0 | - |

### Audio Tracks
| Track | Clips | Time |
|-------|-------|------|
| Narration | 2 | 0-35s, 50-212s |
| BGM | 0 | - |
| SE | 0 | - |

**注**: Avatar/Narrationの分割は擬似カット（trim + 新規クリップ）で実施

---

## 関連 ISSUE

| 優先度 | 問題 | 対応 |
|--------|------|------|
| 🔴 高 | レイヤー `insert_at` の `order` 不整合 | バックエンド修正必要 |
| 🔴 高 | クリップ分割（split）機能の欠如 | 新規エンドポイント実装 |
| 🟡 中 | 座標系ドキュメント（y正=下）| llms-full.txt 修正 |
| 🟡 中 | アセット画像API | 将来実装検討 |
| 🔴 高 | STT APIがプロジェクトAPIキーを使用しない | 要修正 |
| 🔴 高 | V1 APIに `text_style` 更新エンドポイントがない | 要実装 |
| 🟡 中 | Preview APIにレイヤーフィルター機能がない | 将来実装 |
| 🔴 高 | V1 APIにクロップ更新エンドポイントがない | 要実装 |
