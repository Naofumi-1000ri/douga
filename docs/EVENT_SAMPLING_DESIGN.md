# イベントポイント・サンプリング設計書

最終更新: 2026-01-27

## 概要

AIが動画タイムラインの「重要な瞬間だけ」を自動検出・視覚確認できる仕組み。
全フレームのレンダリングなしに構図・配置・テキスト可読性を判定し、
95%→99% 自動化を実現する。

### 背景: Remotion との比較

| 観点 | Remotion | douga + イベントサンプリング |
|------|----------|---------------------------|
| 確認範囲 | 全フレーム（重い） | イベントポイントだけ（10-20箇所） |
| フィードバック | ビジュアルのみ | ビジュアル + 構造化データ（JSON） |
| 修正方法 | コード書き直し | atomic API呼び出し |
| 修正確認 | 再コンパイル+レンダリング | 該当フレームだけ再サンプル |
| 所要時間 | 30秒 x 全修正回数 | 2秒 x 修正箇所数 |

---

## アーキテクチャ

```
AI (Claude MCP)
  │
  ├── get_event_points(project_id)
  │     → EventDetector がタイムラインを走査
  │     → イベントポイントのリストを返す
  │
  ├── sample_frame(project_id, time_ms)
  │     → FrameSampler が FFmpeg で1フレームレンダリング
  │     → Base64 JPEG を返す（~50KB）
  │
  ├── sample_event_points(project_id, max_samples=10)
  │     → get_event_points + sample_frame を一括実行
  │     → 重要な瞬間のフレーム画像を一度に取得
  │
  └── validate_composition(project_id)
        → CompositionValidator がルールベース検証
        → レンダリング不要で構図問題を検出
```

### システム構成

```
┌─────────────────────────────────────────────────────┐
│                  MCP Server (douga)                   │
│  tools/preview.py                                     │
│   - get_event_points()                                │
│   - sample_frame()                                    │
│   - sample_event_points()                             │
│   - validate_composition()                            │
└───────────────────┬─────────────────────────────────┘
                    │ HTTP/JSON
                    ▼
┌─────────────────────────────────────────────────────┐
│              Backend API (/api/preview)               │
│  api/preview.py                                       │
│   POST /projects/{id}/preview/event-points            │
│   POST /projects/{id}/preview/sample-frame            │
│   POST /projects/{id}/preview/sample-event-points     │
│   POST /projects/{id}/preview/validate                │
└───────────────────┬─────────────────────────────────┘
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
┌────────────┐ ┌──────────┐ ┌──────────────────┐
│EventDetector│ │ Frame    │ │Composition       │
│             │ │ Sampler  │ │Validator         │
│13イベント型 │ │FFmpeg 1F │ │10ルール          │
└────────────┘ └──────────┘ └──────────────────┘
```

---

## 1. イベントポイント検出（EventDetector）

### ファイル

- `backend/src/services/event_detector.py`

### 検出するイベント型（13種類）

| イベント型 | 説明 | ソース |
|-----------|------|--------|
| `clip_start` | クリップ開始 | 全ビジュアルレイヤー |
| `clip_end` | クリップ終了 | 全ビジュアルレイヤー |
| `slide_change` | スライド/コンテンツ切替 | content レイヤー |
| `section_boundary` | セクション境界（複数レイヤー同時変更） | 全レイヤー |
| `avatar_enter` | アバター登場 | avatar レイヤー |
| `avatar_exit` | アバター退場 | avatar レイヤー |
| `narration_start` | ナレーション開始 | narration トラック |
| `narration_end` | ナレーション終了 | narration トラック |
| `bgm_start` | BGM開始 | bgm トラック |
| `se_trigger` | SE（効果音）挿入 | se トラック |
| `silence_gap` | 無音区間（全トラック無音） | 全オーディオトラック |
| `effect_point` | エフェクト挿入ポイント | effects レイヤー |
| `layer_change` | レイヤー構成変更 | 全レイヤー |

### 検出ロジック

```
タイムラインを走査し、以下のタイミングを自動抽出:

 0ms        5000ms      12000ms     18000ms     25000ms
  |──────────|───────────|───────────|───────────|──→
  ↑          ↑           ↑           ↑           ↑
  イントロ    ナレーション  スライド     エフェクト    アウトロ
  開始       開始         切替        挿入ポイント   開始
```

#### ビジュアルイベント検出

各レイヤーのクリップ境界（start_ms, start_ms + duration_ms）を検出。
レイヤー型に応じてイベント型をマッピング：

- `avatar` → `avatar_enter` / `avatar_exit`
- `content` → `slide_change` / `clip_end`
- `effects` → `effect_point` / `clip_end`

#### オーディオイベント検出

各オーディオトラックのクリップ境界を検出。
ミュートされたトラックはスキップ。

#### 無音区間検出

全オーディオクリップの区間をマージし、ギャップを検出：

```python
# 例: クリップ区間 [(0, 5000), (7000, 12000)]
# → 無音ギャップ: (5000, 7000) = 2000ms
```

`min_gap_ms` パラメータで最小ギャップ長を制御（デフォルト: 500ms）。

#### セクション境界検出

同一タイミングで2つ以上のレイヤーが変化する箇所を検出。
例: アバターが登場し、同時にスライドが切り替わる = セクション境界。

#### FFmpeg silencedetect（オプション）

`detect_audio_events_from_file()` で実音声ファイルから無音検出：

```bash
ffmpeg -i audio.aac -af "silencedetect=noise=-30dB:d=0.5" -f null -
```

タイムライン解析より正確だが、ファイルダウンロードが必要。

### 重複排除

100ms以内の同一イベント型は重複とみなし除去。

---

## 2. フレームサンプリング（FrameSampler）

### ファイル

- `backend/src/services/frame_sampler.py`

### 仕組み

既存の `RenderPipeline._composite_video()` と同じ FFmpeg filter_complex を構築し、
`-ss` で目標時刻にシークして `-frames:v 1` で1フレームだけ抽出。

```bash
ffmpeg -y \
  -f lavfi -i color=c=black:s=1920x1080:r=30:d=60 \
  -i asset1.mp4 -i asset2.mp4 ... \
  -filter_complex "[filter_complex_here];[out]scale=640:360[smp_out]" \
  -map "[smp_out]" \
  -ss 5.0 \          # 5000ms にシーク
  -frames:v 1 \      # 1フレームだけ
  -q:v 5 \           # JPEG品質
  frame.jpg
```

### レスポンス

```json
{
  "time_ms": 5000,
  "resolution": "640x360",
  "frame_base64": "/9j/4AAQ...",  // Base64 JPEG (~30-80KB)
  "size_bytes": 45230
}
```

### 形状・テキストクリップの処理

動画アセットと異なり、形状やテキストは Pillow で PNG を生成してから
FFmpeg overlay で合成。サンプリングでも同じ処理を簡易版で実行。

### デフォルト解像度

| 用途 | 解像度 | サイズ目安 |
|------|--------|-----------|
| AI判定用（デフォルト） | 640x360 | ~50KB |
| 詳細確認 | 960x540 | ~100KB |
| フル解像度 | 1920x1080 | ~300KB |

---

## 3. イベントポイント・サンプリング（統合）

### ファイル

- `backend/src/api/preview.py` (`sample_event_points` エンドポイント)

### フロー

```
1. EventDetector.detect_all() → イベントリスト
2. _select_diverse_events() → 多様なイベント型を優先して max_samples 個に絞る
3. FrameSampler.sample_frame() x N → 各ポイントのフレーム画像
4. レスポンスにまとめて返す
```

### イベント選択の優先順位

1. `section_boundary` — セクション境界（最重要）
2. `slide_change` — スライド切替
3. `avatar_enter` — アバター登場
4. `narration_start` — ナレーション開始
5. `effect_point` — エフェクト挿入
6. `silence_gap` — 無音区間

各型から1つずつ選択した後、残りを時間軸上で均等に分布。

### AIフィードバックループ

```
                    ┌─────────────────────────────┐
                    │                             │
 apply_plan() → get_event_points() → sample_event_points()
                                          │
                                    AI Vision分析
                                          │
                              ┌───────────┼───────────┐
                              ↓           ↓           ↓
                         構図OK      テロップ見えない  ギャップ発見
                              │           │           │
                              │    edit_timeline()  edit_timeline()
                              │     (update_       (close_gap)
                              │      transform)
                              └───────────┴───────────┘
                                          │
                                   再サンプリング → 確認OK → render_video()
```

### ユースケース例

#### ナレーション + SE 自動挿入

```
1. get_event_points() で narration_start を検出
   → time_ms = 8500

2. AI判断: "ここでSE「ポン♪」を挿入"
   → edit_timeline(operations=[
       {"op": "add_audio_clip", "track": "se", "start_ms": 8300, ...}
     ])

3. sample_frame(time_ms=8500) で確認
   → 視覚的にOK → 次のイベントポイントへ
```

#### 無音区間の処理

```
1. get_event_points() で silence_gap を検出
   → time_ms = 15200, gap_duration_ms = 800

2. AI判断: "800ms > 500ms → 意図しないギャップ"
   → 選択肢:
     a) SE挿入（転換音）
     b) edit_timeline(close_gap)
     c) そのまま（意図的な間）

3. sample_frame(time_ms=15200) で視覚確認
```

---

## 4. 構図ルールバリデーション（CompositionValidator）

### ファイル

- `backend/src/services/composition_validator.py`

### 検証ルール（10種類）

| ルール名 | 重要度 | 説明 |
|----------|--------|------|
| `overlapping_clips` | warning | 同一レイヤー上のクリップ重複 |
| `clip_bounds` | warning/error | タイムライン外へのはみ出し |
| `missing_assets` | error | 参照先アセットの不足 |
| `safe_zone` | warning | セーフゾーン（画面端5%）外配置 |
| `empty_layers` | info | 表示中だがクリップなしのレイヤー |
| `audio_sync` | warning | ナレーション中に映像がない |
| `duration_consistency` | warning | タイムライン尺とコンテンツ尺の不一致（>1秒） |
| `text_readability` | warning | テキスト表示時間不足・フォントサイズ不足 |
| `layer_ordering` | info | レイヤー順序が標準と異なる |
| `gap_detection` | warning/error | 映像ギャップ（黒画面）の検出 |

### セーフゾーン検証

```
┌────────────────────────────────────────┐
│  5% margin                              │
│  ┌──────────────────────────────────┐  │
│  │                                  │  │
│  │         SAFE ZONE               │  │
│  │      (テキスト・アバター)        │  │
│  │                                  │  │
│  └──────────────────────────────────┘  │
│                                        │
└────────────────────────────────────────┘
```

text/avatar レイヤーのクリップが5%マージン外にはみ出す場合に警告。

### テキスト読みやすさ検証

```python
# 表示時間チェック
min_display_ms = word_count * 200 + 500  # 200ms/語 + 500ms ベース
if duration_ms < min_display_ms:
    # 警告: テキスト表示時間が短すぎる

# フォントサイズチェック
if font_size < 24:
    # 警告: 1080p動画で24px未満は読みにくい
```

### レスポンス例

```json
{
  "project_id": "xxx",
  "is_valid": false,
  "issues": [
    {
      "rule": "safe_zone",
      "severity": "warning",
      "message": "Text extends outside safe zone (right)",
      "time_ms": 5000,
      "clip_id": "clip-uuid",
      "layer": "text",
      "suggestion": "Adjust position or scale to stay within 5% margin"
    },
    {
      "rule": "silence_gap",
      "severity": "warning",
      "message": "Visual gap: 800ms blank screen at 15200ms",
      "time_ms": 15200,
      "suggestion": "Fill gap with transition, background, or extend adjacent clips"
    }
  ],
  "total_issues": 2,
  "errors": 0,
  "warnings": 2
}
```

---

## 5. 補間ユーティリティ（Interpolation）

### ファイル

- `backend/src/utils/interpolation.py` (Python)
- `frontend/src/utils/interpolation.ts` (TypeScript)

### Remotion互換 API

```python
from src.utils.interpolation import interpolate, Easing

# 基本的な線形補間
value = interpolate(50, [0, 100], [0, 1])  # → 0.5

# イージング付き
value = interpolate(50, [0, 100], [0, 1], easing=Easing.ease_in_out)

# マルチポイント
value = interpolate(75, [0, 50, 100], [0, 1, 0])  # → 0.5

# カスタムベジェ
value = interpolate(50, [0, 100], [0, 1],
                    easing=Easing.bezier(0.25, 0.1, 0.25, 1.0))
```

### 利用可能なイージング関数（16種類）

| カテゴリ | 関数 |
|----------|------|
| 基本 | `linear` |
| Cubic | `ease_in`, `ease_out`, `ease_in_out` |
| Quadratic | `ease_in_quad`, `ease_out_quad`, `ease_in_out_quad` |
| Sine | `ease_in_sine`, `ease_out_sine`, `ease_in_out_sine` |
| Exponential | `ease_in_expo`, `ease_out_expo`, `ease_in_out_expo` |
| Back (overshoot) | `ease_in_back`, `ease_out_back`, `ease_in_out_back` |
| Custom | `bezier(x1, y1, x2, y2)` |

### ExtrapolateType

入力範囲外の値の処理方法：

| 型 | 動作 |
|----|------|
| `clamp` (デフォルト) | 出力範囲の端に固定 |
| `extend` | 最後のセグメントの傾きで外挿 |
| `identity` | 入力値をそのまま返す |

### キーフレーム補間ヘルパー

```python
from src.utils.interpolation import interpolate_all_properties

# タイムラインキーフレームから全プロパティを補間
result = interpolate_all_properties(
    time_ms=2500,
    keyframes=[
        {"time_ms": 0, "transform": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0}},
        {"time_ms": 5000, "transform": {"x": 100, "y": -50, "scale": 1.5, "rotation": 45}},
    ],
    easing_name="ease_in_out",
)
# result: {"x": 50, "y": -25, "scale": 1.25, "rotation": 22.5, "opacity": 1.0}
```

---

## 6. MCP ツール一覧

### 新規追加ツール（4個）

| ツール | 説明 | 典型的な利用場面 |
|--------|------|-----------------|
| `get_event_points` | イベントポイント検出 | タイムライン分析の最初のステップ |
| `sample_frame` | 単一フレームサンプリング | 特定時刻の構図確認 |
| `sample_event_points` | 一括イベントサンプリング | 全体の視覚確認（1回のAPI呼び出し） |
| `validate_composition` | 構図ルール検証 | レンダリング前のプリフライトチェック |

### 推奨ワークフロー

```
1. apply_plan()              # プランをタイムラインに適用
2. validate_composition()    # ルールベースの事前検証
3. sample_event_points()     # 重要ポイントの視覚確認
4. [問題があれば]
   edit_timeline()           # 修正
   sample_frame(time_ms)     # 修正箇所だけ再確認
5. render_video()            # 最終レンダリング
```

---

## 7. 関連ファイル

### Backend

| ファイル | 説明 |
|----------|------|
| `src/utils/interpolation.py` | 補間ユーティリティ（16イージング） |
| `src/schemas/preview.py` | プレビューAPI スキーマ定義 |
| `src/services/event_detector.py` | イベントポイント検出サービス |
| `src/services/frame_sampler.py` | フレームサンプリングサービス |
| `src/services/composition_validator.py` | 構図ルール検証エンジン |
| `src/api/preview.py` | プレビューAPIエンドポイント |

### Frontend

| ファイル | 説明 |
|----------|------|
| `src/utils/interpolation.ts` | 補間ユーティリティ（Python版と同一ロジック） |

### MCP Server

| ファイル | 説明 |
|----------|------|
| `src/tools/preview.py` | プレビュー/検査MCPツール |
| `src/api_client.py` | プレビューAPI HTTPクライアント |
| `src/server.py` | ツール登録（17ツール → 4新規追加） |

---

## 8. 今後の拡張

### 95% → 99% 自動化ロードマップ

| # | 改善 | 優先度 | 状態 |
|---|------|--------|------|
| 1 | イベントポイント・サンプリング | 最高 | **実装済み** |
| 2 | 構図ルールエンジン | 高 | **実装済み** |
| 3 | 補間ユーティリティ | 高 | **実装済み** |
| 4 | 音声イベント検出（FFmpeg silencedetect） | 高 | **実装済み**（関数のみ） |
| 5 | A/Bプラン生成 + スコアリング | 低 | 未着手 |
| 6 | 分散レンダリング（Cloud Run Jobs） | 中 | 未着手 |

### 音声イベント検出の統合（次ステップ）

`detect_audio_events_from_file()` は実装済みだが、
現在はタイムラインデータからの検出のみ使用。
実音声ファイルの解析を統合するには：

1. アセットをダウンロード
2. ナレーション音声に対して `silencedetect` 実行
3. 検出結果をイベントポイントにマージ

これにより、ナレーションの「息継ぎ」や「言い直し」も検出可能になる。
