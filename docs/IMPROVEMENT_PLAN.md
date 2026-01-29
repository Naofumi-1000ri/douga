# douga 改善計画

最終更新: 2025-01-05

## 概要

Udemy講座制作向け動画編集アプリの改善計画。
Premiere Pro のワークフローを参考に、効率的な編集体験を実現する。

---

## 完了済み

| 機能 | 完了日 | 備考 |
|------|--------|------|
| 図形の拡大縮小（高さ・幅ハンドル） | - | PreviewCanvas実装済み |
| 音声トラックに波形表示 | - | WaveformDisplay実装済み |
| 映像トラックにスナップショット | 2025-01-05 | 疎ら読み込み（60s→30s間隔） |
| タイムラインのデフォルトスケール | 2025-01-05 | 10*zoom px/秒、範囲0.1〜20 |
| ドラッグで新しいレイヤー作成・追加 | - | Timeline実装済み |
| カット機能（トラック分割） | 2025-01-05 | Sキーまたはツールバーボタン、out_point_msバグ修正済み |
| イベントポイント検出 | 2026-01-27 | 13種類のイベント型（クリップ境界、音声、セクション） |
| フレームサンプリング | 2026-01-27 | FFmpegで1フレーム低解像度レンダリング |
| 構図ルール検証 | 2026-01-27 | 10ルール（セーフゾーン、ギャップ、テキスト読みやすさ等） |
| 補間ユーティリティ | 2026-01-27 | Remotion互換interpolate()、16イージング関数 |
| MCPプレビューツール | 2026-01-27 | get_event_points/sample_frame/validate_composition |

---

## Phase 1: 基本編集機能

**目的**: 動画編集の最も基本的な操作を実装

### 1.1 カット機能（トラック分割） ✅ 完了
- **優先度**: 最高
- **説明**: 再生ヘッド位置でクリップを2つに分割
- **完了日**: 2025-01-05
- **実装箇所**: Timeline.tsx
- **UI**:
  - ショートカット: `C` キー または `Cmd+B`
  - コンテキストメニュー: 「ここで分割」
- **技術詳細**:
  - クリップのstart_ms, duration_ms, in_point_msを調整
  - 新しいクリップIDを生成
  - 同一トラック/レイヤー内に2つのクリップを作成

### 1.2 前のクリップ末尾に合わせるボタン（スナップ）
- **優先度**: 高
- **説明**: 選択クリップを前のクリップの終了位置に移動
- **実装箇所**: Timeline.tsx
- **UI**:
  - ツールバーボタン
  - ショートカット: `S` キー
- **技術詳細**:
  - 同一トラック/レイヤー内の直前クリップを検索
  - start_ms = 前クリップの (start_ms + duration_ms)

### 1.3 ドラッグ＆ドロップでカレント位置に配置
- **優先度**: 高
- **説明**: ファイルをドロップした時、再生ヘッド（赤ライン）の位置に配置
- **実装箇所**: Timeline.tsx のドロップハンドラ
- **現状**: トラック先頭（0ms）に配置
- **変更**: currentTimeMs を start_ms として使用

---

## Phase 2: 選択・操作の改善

**目的**: 複数クリップの効率的な操作

### 2.1 前方選択（グループ選択）
- **優先度**: 中
- **説明**: 再生ヘッド以降の全クリップを選択
- **UI**:
  - ツールバーボタン: 「→| 前方選択」
  - ショートカット: `Shift+End` または `A` キー
- **技術詳細**:
  - currentTimeMs 以降の start_ms を持つクリップをすべて選択
  - 既存の複数選択機能 (selectedVideoClips, selectedAudioClips) を活用

### 2.2 スクロールバー伸縮でズーム（Premiere Pro風）
- **優先度**: 中
- **説明**: タイムライン下部のスクロールバーの両端をドラッグしてズーム調整
- **UI**:
  - スクロールバーの左右に伸縮ハンドル追加
  - ドラッグで表示範囲を変更
- **技術詳細**:
  - カスタムスクロールバーコンポーネント作成
  - viewStart, viewEnd の状態管理
  - zoom値との連動

---

## Phase 3: コンテンツ機能

**目的**: 素材追加・作成の効率化

### 3.1 音声付きVIDEO → 映像+音声セットで追加
- **優先度**: 中
- **説明**: 動画ファイルをドロップ時、自動で音声を抽出し別トラックに配置
- **現状**: extract-audio APIは実装済み（手動呼び出し）
- **実装箇所**: Timeline.tsx のドロップハンドラ
- **フロー**:
  1. 動画をレイヤーに追加
  2. バックグラウンドで音声抽出API呼び出し
  3. 抽出完了後、音声トラックに自動追加
  4. 映像クリップと音声クリップをリンク

### 3.2 テキストツール
- **優先度**: 中
- **説明**: テロップ・字幕の追加
- **実装箇所**:
  - PreviewCanvas: テキスト描画
  - 新規コンポーネント: TextEditor
  - Timeline: テキストクリップ
- **機能**:
  - フォント選択
  - サイズ・色・位置調整
  - アニメーション（フェードイン等）

---

## Phase AI: AI品質管理（イベントサンプリング）✅ 完了

**目的**: AIが動画タイムラインの重要な瞬間を自動検出・視覚確認できる仕組み

### AI.1 イベントポイント検出 ✅ 完了
- **説明**: タイムラインのクリップ境界、音声イベント、無音区間を自動検出
- **完了日**: 2026-01-27
- **実装箇所**: `backend/src/services/event_detector.py`
- **13種類のイベント型**: clip_start/end, slide_change, section_boundary, avatar_enter/exit, narration_start/end, bgm_start, se_trigger, silence_gap, effect_point, layer_change

### AI.2 フレームサンプリング ✅ 完了
- **説明**: 指定時刻の低解像度プレビューフレームをFFmpegで生成
- **完了日**: 2026-01-27
- **実装箇所**: `backend/src/services/frame_sampler.py`
- **仕様**: 640x360 JPEG、Base64エンコード、~50KB/フレーム

### AI.3 構図ルール検証 ✅ 完了
- **説明**: レンダリングなしでタイムラインの構図問題を検出
- **完了日**: 2026-01-27
- **実装箇所**: `backend/src/services/composition_validator.py`
- **10ルール**: overlapping_clips, clip_bounds, missing_assets, safe_zone, empty_layers, audio_sync, duration_consistency, text_readability, layer_ordering, gap_detection

### AI.4 補間ユーティリティ ✅ 完了
- **説明**: Remotion互換のinterpolate()関数、16イージング関数
- **完了日**: 2026-01-27
- **実装箇所**:
  - Python: `backend/src/utils/interpolation.py`
  - TypeScript: `frontend/src/utils/interpolation.ts`

### AI.5 MCPプレビューツール ✅ 完了
- **説明**: 4つの新MCPツールで上記機能をClaude Codeから利用可能に
- **完了日**: 2026-01-27
- **ツール**: get_event_points, sample_frame, sample_event_points, validate_composition

**詳細設計**: `docs/EVENT_SAMPLING_DESIGN.md`

---

## Phase 4: プロジェクト管理

**目的**: 複数シーケンスの管理

### 4.1 シーケンス保存・切り替え
- **優先度**: 低（他の機能が安定してから）
- **説明**: 1プロジェクト内で複数のタイムライン（シーケンス）を管理
- **実装箇所**:
  - バックエンド: Sequence モデル追加
  - フロントエンド: シーケンス一覧UI
- **機能**:
  - 新規シーケンス作成
  - シーケンス複製
  - シーケンス切り替えタブ

---

## 推奨実装順序

```
Phase 1.1 カット機能 ✅
    ↓
Phase AI  イベントサンプリング ✅
    ↓
Phase 1.3 カレント位置に配置
    ↓
Phase 1.2 前のクリップ末尾に合わせる
    ↓
Phase 2.1 前方選択
    ↓
Phase 3.1 映像+音声セット追加
    ↓
Phase 2.2 スクロールバーズーム
    ↓
Phase 3.2 テキストツール
    ↓
Phase 4.1 シーケンス管理
```

---

## 技術メモ

### 関連ファイル
- `frontend/src/components/editor/Timeline.tsx` - タイムライン本体
- `frontend/src/components/editor/PreviewCanvas.tsx` - プレビュー描画
- `frontend/src/components/editor/VideoClipThumbnails.tsx` - サムネイル表示
- `frontend/src/stores/projectStore.ts` - 状態管理
- `frontend/src/utils/interpolation.ts` - 補間ユーティリティ
- `backend/src/api/assets.py` - アセットAPI
- `backend/src/api/preview.py` - プレビュー/検査API
- `backend/src/services/audio_extractor.py` - 音声抽出
- `backend/src/services/event_detector.py` - イベントポイント検出
- `backend/src/services/frame_sampler.py` - フレームサンプリング
- `backend/src/services/composition_validator.py` - 構図ルール検証
- `backend/src/utils/interpolation.py` - 補間ユーティリティ
- `docs/EVENT_SAMPLING_DESIGN.md` - イベントサンプリング設計書

### タイムライン座標系
- `pixelsPerSecond = 10 * zoom`
- `position = (timeMs / 1000) * pixelsPerSecond`
- `timeMs = (position / pixelsPerSecond) * 1000`
