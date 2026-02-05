# AI Friendly Target Spec (Ideal)

最終更新: 2026-02-04

このドキュメントは「AIが迷わない・誤解しない・詰まらない」ことを最優先にした**理想仕様**です。
開発コストは考慮しません。以降の仕様は**非妥協**の前提です。

---

## 1. 非交渉の原則

1) **唯一解**: 同じ入力は必ず同じ出力（決定性）。
2) **曖昧さゼロ**: 暗黙のデフォルト禁止。全入力に明示的な定義。
3) **完全観測**: AIが状態を完全に取得・比較・検証できる。
4) **検証優先**: すべての変更は適用前に検証可能。
5) **安全な失敗**: 失敗時は構造化エラーと修正指示を返す。
6) **再現性**: 変更履歴と差分が機械的に追跡できる。

---

## 2. 仕様の最上位ルール（Invariants）

- **時間単位**: `time_base = ms` 固定（v1ではframe非対応）。
- **時間値**: すべて整数（ms）。小数は禁止。
- **座標系**: 原点・単位・向き・回転方向は一意。
- **ID参照**: すべての参照は存在チェック必須。
- **重複/衝突**: ルールに従い機械判定（許可/禁止）。
- **依存関係**: clip → asset/layer、keyframe → clip 等は強制整合。
- **エフェクト**: すべて同一スキーマ形式（共通パラメータ構造）。

---

## 2.1 絶対決定（v1固定）

- **time_base**: `ms` のみ
- **frame_rate**: 30 固定
- **resolution**: 1920x1080 固定
- **座標原点**: キャンバス中心 `(0, 0)`
- **軸方向**: +X 右 / +Y 下
- **回転方向**: 度（clockwiseが正）
- **anchor**: 正規化 (0〜1) 必須（省略不可）
- **丸め**: `round_half_up`（四捨五入）
- **時間量子化**: 1ms
- **frame変換**: `frame = floor(time_ms * fps / 1000 + 0.5)`
- **time変換**: `time_ms = floor(frame * 1000 / fps + 0.5)`

---

## 3. AI向け操作モデル（必須）

### 3.1 validate/apply の分離
- **validate-only**: 適用せずに検証。
- **apply**: 検証済み変更のみ適用。

### 3.2 diff / rollback
- **diff**: before/after と影響範囲を返す（`options.include_diff=true`）。
- **rollback**: `operation_id` を使ってロールバック（専用トークンは使わない）。

### 3.3 idempotency
- **Idempotency-Key** が必須。
- 同一キーの再送は同一結果を返す。

---

## 4. AI Friendly APIの最低要件

- **OpenAPI + JSON Schema** が真実。
- **x-constraints** により交差制約（in/out範囲、重複禁止、keyframe範囲など）を機械可読化。
- **capabilities** は許可される列挙値（font_families / blend_modes / easings / transitions 等）を返す。
- **明示的エラー**: `error_code` と `suggested_fix` を必須。
- **計画実行**: `plan.validate()` と `plan.apply()` を分離。
- **バッチ制御**: `atomic` / `best_effort` を必ず選択。
- **差分出力**: 変更結果は `include_diff` 指定時に diff を返す。

---

## 5. 観測性（Observability）

AIは**常に次を取得できる**必要がある:

- L1: 全体要約（時間、レイヤー数、オーディオ状態など）
- L2: 構造（レイヤー/トラック/クリップの階層）
- L3: 詳細（個別パラメータ、keyframe、effects）
- **差分**: 変更前後の差分
- **検証結果**: ルール違反・警告・推奨

---

## 6. 設計上の絶対ルール

- **暗黙の補完禁止**: AIが推測しないように必須値を要求。
- **不明は拒否**: 欠落/曖昧な入力は必ずエラー。
- **数値は丸め規則を固定**（四捨五入 or 切り捨てを明記）。
- **語彙は統一**: 用語辞書にない語はエラー。
- **検証エラーは必ず修正提案付き**。

---

## 7. ドキュメント構成（本仕様の下位文書）

- `docs/API_REFERENCE.md`
- `docs/DATA_CONSTRAINTS.md`
- `docs/COORDINATE_SYSTEMS.md`
- `docs/ERROR_CODES.md`
- `docs/API_EXAMPLES.md`
- `docs/openapi/douga-ai-friendly.yaml`

---

## 8. AI Friendlyの完成像（合格条件）

- AIの操作成功率 > 99%（期待状態達成率）
- AI操作後の手動修正量が「軽微」水準
- すべての操作が validate-only で事前検証可能
- 仕様にない操作は必ず拒否される

---

## 9. 推奨フロー（理想）

1) 状態取得（L1/L2/L3）
2) 計画生成（plan）
3) `plan.validate()`
4) `plan.apply()`
5) 差分確認（diff）
6) 必要なら rollback
