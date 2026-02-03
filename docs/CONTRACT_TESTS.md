# Contract Tests Design (AI Friendly)

最終更新: 2026-02-03

このドキュメントは **OpenAPI + x-constraints** を正とする契約テストの設計です。
目的は「AIが迷わない」ための **機械可読な保証** を提供することです。

---

## 1. 目的

- **仕様と実装のズレを検出**する
- **AIの自己修正が可能**なエラー構造を保証する
- **x-constraints を機械的に検証**し、交差制約を曖昧にしない

---

## 2. 入力ソース

1. `docs/openapi/douga-ai-friendly.yaml` (Single Source of Truth)
2. `docs/ERROR_CODES.md` (error_code と suggested_fix の一覧)
3. `/capabilities` (enum の正: font_families, blend_modes, easings, transitions など)

---

## 3. テスト層 (Layered)

### L1. Spec Integrity (静的検証)

- OpenAPI がスキーマとして valid
- すべての write endpoint に `Idempotency-Key` / `If-Match` が必須
- すべての mutation request に `options` が必須
- x-constraints に `error_code` が設定されている

### L2. Schema Validation (単項目制約)

- min/max/enum/format/pattern が正しく適用される
- UUID / Color / enum の拒否と accepted を確認する

### L3. x-constraints (交差制約)

- `out_point_ms > in_point_ms`
- `in/out` が asset duration の範囲内
- keyframe が clip 範囲内
- overlap_policy=disallow の重複禁止
- font_family が `/capabilities.font_families` に存在
- transition: cut=0, non-cut=100-2000

### L4. Error Contract (AI回復可能性)

- `error.code` が必ず返る
- `suggested_fix` が必ず付与される
- `retryable` が規約に一致
- `request_id` が常に UUID

---

## 4. テストデータ設計 (Fixtures)

最小限の固定プロジェクトを用意する:

```
project:
  id: p-uuid
  settings: time_base=ms, frame_rate=30, resolution=1920x1080, overlap_policy=disallow
assets:
  A: video, duration_ms=10000
  B: audio, duration_ms=20000
layers:
  L1: content
audio_tracks:
  T1: narration
clips:
  C1: video clip (start=0, duration=4000, asset=A)
audio_clips:
  AC1: audio clip (start=0, duration=8000, asset=B)
```

---

## 5. テストケース形式 (提案)

テストは YAML/JSON で定義し、**同一ケースを spec-only と実装テストに使い回す**。

```
id: CT-CLIP-001
description: out_point_ms must be greater than in_point_ms
request:
  method: POST
  path: /api/ai/v1/projects/{project_id}/clips
  headers:
    Idempotency-Key: "test-001"
    If-Match: "etag-1"
  body:
    options: { validate_only: true, return_diff: false }
    clip:
      type: video
      layer_id: "{L1}"
      asset_id: "{A}"
      start_ms: 0
      duration_ms: 3000
      in_point_ms: 2000
      out_point_ms: 1000
      transform: { position:{x:0,y:0}, scale:{x:1,y:1}, rotation:0, opacity:1, anchor:{x:0.5,y:0.5} }
context:
  fixture: base.yaml
expect:
  status: 400
  error_code: INVALID_TIME_RANGE
  suggested_fix: "Set out_point_ms > in_point_ms"
```

---

## 6. x-constraints 実行ルール

- `context` で参照オブジェクトを解決する
- rule は **常に deterministic**
- 複数違反時は **定義順**で評価し、最初の error を返す

例: `context: "clip = Clip(clip_id from path)"` の場合

```
clip.start_ms, clip.end_ms, clip.asset.duration_ms を参照可能
```

---

## 7. /capabilities との連動

enum は OpenAPI だけでなく `/capabilities` が正。
contract test は以下を必須チェックする:

1. `/capabilities` が enum を返す
2. enum 外の値は `UNSUPPORTED_*` で拒否される
3. `max_batch_ops` を上限としてバッチ分割する

---

## 8. 実行形態 (Spec-only / Implementation)

### Spec-only (静的)
- OpenAPI + x-constraints を読み込み
- fixture と test case で **期待結果をシミュレート**
- CI の最小保証として常時実行

### Implementation (実API)
- テスト用 DB を seed
- 実 API へリクエスト
- error_code / suggested_fix / request_id を検証

---

## 9. 合格基準

- 全 x-constraints が **最低1ケース**以上テストされている
- enum 系は **許可/拒否**の両方をテスト
- error_code と suggested_fix が仕様と一致

---

## 10. 追加設計 (推奨)

- `tests/contract/fixtures/` に固定 seed
- `tests/contract/cases/` に YAML
- CI で `spec-only` を必須、`implementation` は nightly
