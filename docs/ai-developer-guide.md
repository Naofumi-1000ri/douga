# Douga AI Developer Guide

> Practical guide for building AI assistants that integrate with the Douga video editor.
> Last updated: 2026-02-07

## Overview

This guide helps AI developers create reliable, user-friendly integrations with Douga's video editing API. The core principle is:

**AIが迷わない = 最高のプロダクト** (AI that doesn't hesitate = best product)

## Core Principles

| Principle | Description |
|-----------|-------------|
| **Deterministic** | Same input → Same result. No exceptions. |
| **Verifiable** | Use validate_composition and sample_frame for checks. |
| **Reversible** | API rollback available for supported ops; otherwise keep client-side snapshots or use UI undo. |
| **Explicit** | No guessing, no implicit conversions. |

---

## AI Capabilities & Limitations

> Be transparent about what AI can and cannot do.

### What AI Can Do Well

| Capability | Example | Confidence |
|------------|---------|------------|
| **Timeline manipulation** | Add/move/delete clips | High |
| **Semantic operations** | snap_to_previous, close_gap | High |
| **Batch operations** | Move multiple clips, apply effects | High |
| **Structure analysis** | Find gaps, summarize timeline | High |
| **Parameter calculations** | Position, scale, timing | Medium-High |
| **Asset management** | Find assets, assign to clips | Medium |

### What AI Cannot Do (or Shouldn't Attempt)

| Limitation | Reason | Mitigation |
|------------|--------|------------|
| **Visual judgment** | Cannot "see" rendered output | Use sample_frame, report issues found |
| **Audio quality** | Cannot hear audio mix | Rely on waveforms, ducking settings |
| **Aesthetic decisions** | "良い感じ" is subjective | Present options, ask user |
| **Creative interpretation** | "かっこよく" varies | Clarify with specific parameters |
| **Cross-session memory** | IDs change between sessions | Always GET fresh structure |
| **Real-time preview** | Cannot watch playback | Sample key frames |

### Transparency Guidelines

**DO:**
```
"3つのクリップを右に移動しました。
 位置関係をプレビューで確認することをお勧めします。"
```

**DON'T:**
```
"完璧に配置しました！" (AIは見た目を確認できない)
```

**When Uncertain:**
```
"scale=0.5で設定しましたが、
 実際のサイズ感はプレビューでご確認ください。"
```

### Capability Matrix for Common Tasks

| Task | AI Solo | AI + User Verify | User Required |
|------|---------|------------------|---------------|
| クリップ移動 | ✓ | | |
| クリップ削除 | ✓ | | |
| 隙間詰め | ✓ | | |
| BGMダッキング | ✓ | | |
| 位置・スケール調整 | | ✓ | |
| クロマキー調整 | | ✓ | |
| テキストスタイル | | ✓ | |
| エフェクト選択 | | ✓ | |
| 全体の雰囲気判断 | | | ✓ |
| 最終品質確認 | | | ✓ |

---

## Udemy Audio Targets

| Metric | Target | Notes |
|--------|--------|-------|
| Integrated Loudness | -19 LUFS ±1 | Course audio consistency |
| True Peak | <= -1 dBTP | Avoid clipping |
| Narration | -19 LUFS | Primary voice |
| BGM | -28 to -24 LUFS | Lower than narration |
| Ducking (during narration) | ~ -32 LUFS | Auto-ducked BGM target |

**duck_to (linear gain) → dB 目安:**  
`gain_db ≈ 20 * log10(duck_to)`  
※ LUFS は素材依存のため、実測が必要。

---

## Information Hierarchy

Always access data in this order to prevent hallucination:

```
L1 Overview (~300 tokens)
    ↓ Understand project scope
L2 Structure (~800 tokens)
    ↓ Find layer/track/clip IDs
L3 Details (~400 tokens/clip)
    ↓ Get specific properties
Write Operations
    → Make changes with verified IDs
```

### Why This Matters

AI assistants can hallucinate IDs, timestamps, or properties. The hierarchy ensures:

1. You always have **verified data** before acting
2. You use **minimal tokens** (don't load full timeline unnecessarily)
3. You can **trace decisions** back to real data

---

## Confidence & When to Ask

> **Critical**: AI must know when to execute vs when to ask.

### Confidence Levels

| Level | Score | Example | Action |
|-------|-------|---------|--------|
| **High** | 85-100% | "clip-abcを5秒右に移動" | Execute |
| **Medium** | 70-84% | "これを大きくして" | Ask: "どのくらい? scale=2.0?" |
| **Low** | 50-69% | "いい感じにして" | Present options: "A, B, or C?" |
| **Very Low** | <50% | "直して" | Clarify: "何を直しますか?" |

### Decision Flow

```
User instruction received
    ↓
Parse instruction → Identify parameters
    ↓
Calculate confidence score
    ↓
├── ≥85%: Execute → Report
├── 70-84%: Ask focused question → Re-evaluate
├── 50-69%: Present multiple interpretations
└── <50%: Ask for clarification
```

### Confidence Reducers

- Missing target: "これ" without clear referent → -30%
- Missing value: "大きく" without scale → -20%
- Ambiguous action: "調整" → -25%
- Multiple valid interpretations → -15% each

### Risk × Confidence Matrix

> Risk level determines how much confidence is needed to act.

| Risk Level | Description | Required Confidence | Example |
|------------|-------------|---------------------|---------|
| **Low** | Single clip change (easy to correct) | 70%+ | Move clip, adjust scale |
| **Medium** | Multiple clips, more effort to correct | 85%+ | Batch move, close_gap |
| **High** | Affects many clips, cascade effects | 95%+ | Delete layer, trim all |
| **Critical** | Irreversible or project-wide | 100% (always ask) | Export, delete project |

**Decision Matrix:**

```
                    CONFIDENCE
                Low(<70%)  Med(70-84%)  High(85-95%)  VHigh(>95%)
         ┌─────────────────────────────────────────────────────┐
    Low  │  Ask      Ask+Suggest   Execute       Execute      │
R  Med   │  Ask      Ask           Ask+Suggest   Execute      │
I  High  │  Refuse   Ask           Ask           Ask+Suggest  │
S  Crit  │  Refuse   Refuse        Ask           Ask          │
K        └─────────────────────────────────────────────────────┘
```

**Examples:**

| User Request | Risk | Confidence | Action |
|--------------|------|------------|--------|
| "clip-abcを2秒右に" | Low | 95% | Execute directly |
| "BGM下げて" | Low | 75% | Ask: "0.3に下げますか?" |
| "全部の隙間詰めて" | High | 80% | Ask: "5レイヤーすべてですか?" |
| "このクリップ消して" | Medium | 90% | Execute (rollback available for supported ops; report clearly) |
| "やり直し効かないよ、消して" | Critical | 95% | Ask: "本当に削除しますか?" |

### Confirmation Thresholds (Must Ask)

| Change | Threshold | Ask? |
|--------|-----------|------|
| scale | <= 0.5 または >= 2.0 | Yes |
| rotation | >= 30° | Yes |
| position | 画面幅/高さの 25%以上移動（x>=480px or y>=270px） | Yes |
| batch | 5件以上 or 3レイヤー以上に影響 | Yes |
| delete | すべての削除操作 | Yes |

---

## Multi-turn Context

> Track what happened in previous turns.

### What to Track

```json
{
  "last_operation": {
    "clip_id": "clip-abc",
    "layer_id": "layer-avatar",
    "type": "add_clip"
  },
  "pronoun_map": {
    "それ/that": "clip-abc",
    "ここ/here": "15000ms"
  }
}
```

### Pronoun Resolution

| User Says | Resolve To |
|-----------|------------|
| "それを大きく" | last affected clip |
| "もっと右に" | last affected clip, increase x |
| "戻して" | Ask which change to reverse; apply compensating edit or suggest UI undo |
| "ここに追加" | playhead position or last layer |

### Example

```
T1: "アバター追加" → clip-abc created
T2: "大きくして" → clip-abc scale increased (not "which clip?")
T3: "左に" → clip-abc x decreased
```

---

## Common AI Mistakes & Prevention

### 1. ID Hallucination

**Problem:** AI uses an ID it "remembers" but doesn't exist.

```
❌ PATCH /clip/clip-remembered-123/move
   → "Clip not found: clip-remembered-123"
```

**Prevention:**
- Always GET /structure before writing
- Never reuse IDs across sessions
- If you get "not found", refresh /structure and retry

### 2. Time Calculation Errors

**Problem:** Manual calculation leads to 1ms overlaps or gaps.

```
❌ Previous clip: start=5000, duration=10000
   AI calculates: 5000 + 10000 = 15000
   But actual end was 14999 due to rounding → 1ms overlap!
```

**Prevention:**
- Use semantic operations: `snap_to_previous` does the math correctly
- Check `previous_clip.end_ms` from L3 details

### 3. Out-of-Range Values

**Problem:** AI suggests physically impossible values.

```
❌ { "scale": 0, "opacity": 1.5, "x": 99999 }
```

**Prevention:**
- Reference Parameter Reference in llms-full.txt
- Expect 422 validation errors if values are out of range

### 4. Stale State

**Problem:** AI acts on old information after user edits manually.

**Prevention:**
- Refresh L2 structure before complex operations
- Handle 404 "not found" by re-fetching structure

### 5. Partial Batch Failure

**Problem:** 5 operations submitted, 3 succeed, 1 fails, 1 skipped.

**Prevention:**
- Batch is best-effort only (no atomic mode)
- Check `results` and `errors` arrays carefully
- Fix inputs and retry only failed operations

### 6. Visual Assumptions

**Problem:** AI sets x=800, assumes it looks good, but clip is off-screen.

**Prevention:**
- Use `sample_frame` after transform changes
- Report visual verification to user when relevant

---

## Recommended Workflow

### For Simple Operations

```
1. GET /structure → Find IDs
2. POST /clips or PATCH /clip/{id}
3. Report result to user
```

### For Complex Operations

```
1. GET /structure → Understand current state
2. Plan operations (use TodoWrite if available)
3. Execute operations
4. Report changes to user
5. Optionally run validate_composition and sample_frame for verification
```

### For User Requests

| User Says | AI Does |
|-----------|---------|
| "Add avatar at the end" | GET structure → find avatar layer → find end time → POST clip |
| "Lower BGM" | POST semantic auto_duck_bgm OR adjust volume |
| "Close gaps" | POST semantic close_gap for relevant layer |
| "Undo that" | Explain no API undo; suggest UI undo or apply a compensating edit |
| "What's the structure?" | GET overview → GET structure → summarize |

---

## Semantic Operations

Prefer semantic operations over manual calculations:

| Operation | Use When | Instead Of |
|-----------|----------|------------|
| `snap_to_previous` | Moving clip to touch previous | Calculating start_ms manually |
| `snap_to_next` | Closing gap after a clip | Moving next clip manually |
| `close_gap` | Removing all gaps in layer | Multiple move operations |
| `auto_duck_bgm` | BGM should lower during narration | Complex volume automation |
| `rename_layer` | Changing layer name | Direct layer PATCH |

**Ducking note:** `duck_to` is linear gain. Approximate dB change:  
`gain_db ≈ 20 * log10(duck_to)`

---

## Error Handling

### Read Error Response Carefully

```json
{
  "detail": "Clip not found: 6b0e..."
}
```

### Error Response Checklist

1. Read the `detail` message
2. If "not found", refresh IDs with GET /structure or GET /assets
3. If 422 validation error, fix input and retry
4. Report error clearly to user if unrecoverable

### Common Error Patterns

| Error Detail | Meaning | Action |
|-------------|---------|--------|
| "not found" | ID doesn't exist | Refresh IDs from structure/assets |
| "out_point_ms ..." | Invalid trim | Fix in_point/out_point |
| 422 validation | Out of range/type | Fix input and retry |
| 5xx | Server error | Retry with backoff |

### Partial Failure Handling (Batch)

**Dependency rule:**
- 失敗した操作が後続に依存されている場合 → 後続は中止し再計画
- 依存がない場合 → 失敗分のみ再試行

**Report template:**
```
⚠️ 一部成功しました

成功: {successful}/{total}
失敗: {failed}
影響: {affected_layers/clips}

失敗内容:
- {op} → {error}

次の選択肢:
1) 失敗分のみ再試行
2) 状態確認（GET /structure / validate_composition）
3) すべて取り消し（可能なら rollback / UI undo）
```

---

## Reporting Changes to Users

After every operation, tell the user:

### What Changed
```
✓ Added 3-second avatar clip at 15.0s
  Layer: Avatar
  Asset: avatar_red.mp4
```

### Validation Issues (if checked)
```
⚠ validate_composition: text_readability
  Suggested: increase font size
```

---

## Prompt Design for AI Assistants

### System Prompt Template

```markdown
You are an AI assistant for Douga video editor.

## Rules

1. **Information Access**
   - Always use L1→L2→L3 hierarchy
   - Never guess or remember IDs across turns
   - Refresh structure before complex operations

2. **Operations**
   - Prefer semantic operations over manual calculations
   - Use validate_composition and sample_frame when needed
   - Report all changes to user

3. **Errors**
   - Read `detail` and refresh IDs if needed
   - Ask user for clarification if stuck

4. **Communication**
   - Summarize what changed after operations
   - Warn about potential issues proactively
```

### Response Pattern

```
For user request: "Add an avatar"

1. "Let me check the current timeline structure."
   → GET /structure

2. "I found the Avatar layer. I'll add the red avatar at 15 seconds."
   → POST /clips

3. "Done! I added a 3-second avatar clip at 15.0s.
   The clip uses avatar_red.mp4 with chroma key enabled."
```

---

## Checklist for AI Developers

### Before Integration

- [ ] Read llms.txt (quick reference)
- [ ] Read llms-full.txt (complete spec)
- [ ] Understand L1→L2→L3 hierarchy
- [ ] Test with simple operations first

### Per-Operation Checklist

- [ ] Have valid IDs from recent GET /structure
- [ ] Parameters within documented ranges
- [ ] Using semantic operation if applicable
- [ ] Ready to handle and report errors
- [ ] Will report changes to user

### Quality Metrics

Target these success rates:

| Metric | Target |
|--------|--------|
| First-try correct operation | > 95% |
| Error recovery after refresh/retry | > 90% |
| Zero hallucinated IDs | > 99% |

---

## API Quick Reference

### Read (Always safe)
```
GET /api/ai/v1/projects/{project_id}/overview      # L1
GET /api/ai/v1/projects/{project_id}/structure     # L2
GET /api/ai/v1/projects/{project_id}/clips/{clip_id}    # L3
GET /api/ai/v1/projects/{project_id}/assets        # L2
```

### Write (Verify IDs first)
```
POST   /api/ai/v1/projects/{project_id}/clips           # Add clip
PATCH  /api/ai/v1/projects/{project_id}/clips/{clip_id}/move # Move clip
DELETE /api/ai/v1/projects/{project_id}/clips/{clip_id}      # Delete clip
```

### Semantic (Preferred for complex ops)
```
POST /api/ai/v1/projects/{project_id}/semantic
{
  "operation": "snap_to_previous" | "close_gap" | "auto_duck_bgm",
  "target_clip_id": "..." | "target_layer_id": "..."
}
```

---

## Available Features (v1)

| Feature | Status | Description |
|---------|--------|-------------|
| validate_only | Available | Pre-validate without executing |
| include_diff | Available | Return diff in mutation responses |
| rollback | Available | `POST /operations/{operation_id}/rollback` |
| operation history | Available | `GET /history` / `GET /operations/{id}` |
| snapshots | Planned | Named restore points |

---

## AI Video Production API (`/api/ai-video/`)

AI による動画制作の自動化 API。素材アップロードからタイムライン構築まで一貫して処理する。

### Batch Upload: 素材一括アップロード

```
POST /api/ai-video/projects/{project_id}/assets/batch-upload
```

複数素材を一括アップロードし、自動分類する。メタデータ（duration、dimensions、chroma key color、thumbnail）はアップロード時に同期的に解析される。

```bash
curl -X POST \
  "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/assets/batch-upload" \
  -H "Authorization: Bearer ${TOKEN}" \
  -F "files=@avatar.mp4" \
  -F "files=@narration.wav" \
  -F "files=@screen_capture.mp4" \
  -F "files=@slide01.png"
```

レスポンス:
```json
{
  "project_id": "...",
  "results": [
    {
      "filename": "avatar.mp4",
      "asset_id": "...",
      "type": "video",
      "subtype": "avatar",
      "confidence": 0.95,
      "duration_ms": 60000,
      "chroma_key_color": "#00FF00"
    }
  ],
  "total": 4,
  "success": 4,
  "failed": 0
}
```

関連エンドポイント:
```
GET  /api/ai-video/projects/{project_id}/asset-catalog         # 分類済みアセット一覧
PUT  /api/ai-video/projects/{project_id}/assets/{id}/reclassify  # 誤分類の修正
GET  /api/ai-video/projects/{project_id}/assets/{id}/transcription  # STT結果取得
```

### Plan: タイムライン計画

素材を分析し、AIがタイムラインの構成計画を生成する。

**計画生成:**
```
POST /api/ai-video/projects/{project_id}/plan/generate
```

```bash
curl -X POST \
  "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/plan/generate" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "brief": {
      "title": "Unity入門",
      "style": "tutorial",
      "target_duration_seconds": 300,
      "language": "ja",
      "sections": [
        {"type": "intro", "title": "挨拶", "estimated_duration_seconds": 10},
        {"type": "content", "title": "操作説明", "estimated_duration_seconds": 200}
      ],
      "preferences": {
        "use_avatar": true,
        "avatar_position": "bottom-right",
        "chroma_key_avatar": true
      }
    }
  }'
```

**計画適用:**
```
POST /api/ai-video/projects/{project_id}/plan/apply
```

計画をタイムラインデータに変換する。アバター動画からの音声抽出やクロマキー適用も自動実行される。

```bash
curl -X POST \
  "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/plan/apply" \
  -H "Authorization: Bearer ${TOKEN}"
```

関連エンドポイント:
```
GET  /api/ai-video/projects/{project_id}/plan     # 現在の計画取得
PUT  /api/ai-video/projects/{project_id}/plan     # 計画の手動更新
```

### Skills: 6つの自動化スキル

計画適用後に実行可能な自動化スキル。依存関係に注意して順番に実行する。

| スキル | エンドポイント | 依存 | 説明 |
|--------|--------------|------|------|
| trim-silence | `POST .../skills/trim-silence` | plan/apply | ナレーション前後の無音をカット。group_idでアバターも連動 |
| add-telop | `POST .../skills/add-telop` | plan/apply | Whisper STTでナレーションを文字起こしし、テキストクリップを配置 |
| layout | `POST .../skills/layout` | plan/apply | アバター・スクリーン・スライドのレイアウト適用 |
| sync-content | `POST .../skills/sync-content` | add-telop | 操作画面をナレーションに同期。発話中は通常速度、無音区間は加速 |
| click-highlight | `POST .../skills/click-highlight` | plan/apply | 操作画面のクリック検出、ハイライト矩形追加 |
| avatar-dodge | `POST .../skills/avatar-dodge` | click-highlight | クリックハイライトとアバターの重なり回避 |

**実行順序（依存グラフ）:**
```
trim-silence ─┐
add-telop ────┤
layout ───────┤─→ (並列可能な3つ)
              │
sync-content ─┘─→ add-telop に依存
click-highlight ──→ (独立)
avatar-dodge ─────→ click-highlight に依存
```

**layout スキルのオプション:**
```bash
curl -X POST \
  "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/skills/layout" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "avatar_position": "bottom-right",
    "avatar_size": "pip",
    "screen_position": "fullscreen"
  }'
```

`avatar_position`: bottom-right, bottom-left, top-right, top-left, center-right, center-left
`avatar_size`: pip, medium, large, fullscreen
`screen_position`: fullscreen, left-half, right-half

### Run-All: 全スキル一括実行

```
POST /api/ai-video/projects/{project_id}/skills/run-all
```

6つのスキルを正しい依存順序で一括実行する。最初の失敗で停止する。

```bash
curl -X POST \
  "${API_BASE}/api/ai-video/projects/${PROJECT_ID}/skills/run-all" \
  -H "Authorization: Bearer ${TOKEN}"
```

レスポンス:
```json
{
  "project_id": "...",
  "success": true,
  "total_duration_ms": 45000,
  "results": [
    {"skill": "trim-silence", "success": true, "message": "...", "duration_ms": 3000},
    {"skill": "add-telop", "success": true, "message": "...", "duration_ms": 15000},
    {"skill": "layout", "success": true, "message": "...", "duration_ms": 2000},
    {"skill": "sync-content", "success": true, "message": "...", "duration_ms": 10000},
    {"skill": "click-highlight", "success": true, "message": "...", "duration_ms": 12000},
    {"skill": "avatar-dodge", "success": true, "message": "...", "duration_ms": 3000}
  ],
  "failed_at": null
}
```

### Video Production Capabilities

```
GET /api/ai-video/capabilities
```

ワークフロー手順、スキル仕様、アセットタイプ一覧を返す静的エンドポイント。キャッシュ可能（Cache-Control: max-age=86400）。AIは初回にこのエンドポイントを呼んでワークフローを理解する。

---

## Preview & Validation API (`/api/preview/`)

レンダリングせずにタイムラインの品質を検証するための API。

### sample-frame: フレーム画像生成

```
POST /api/projects/{project_id}/preview/sample-frame
```

指定時刻のプレビューフレームを低解像度 JPEG（base64）で生成する。

```bash
curl -X POST \
  "${API_BASE}/api/projects/${PROJECT_ID}/preview/sample-frame" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"time_ms": 5000, "resolution": "640x360"}'
```

レスポンス:
```json
{
  "time_ms": 5000,
  "resolution": "640x360",
  "frame_base64": "/9j/4AAQ...",
  "size_bytes": 45000
}
```

### validate-composition: 品質チェック

```
POST /api/projects/{project_id}/preview/validate
```

タイムラインを10のルールで検証する。レンダリング不要。

**10 Validation Rules:**

| ルール | 説明 |
|--------|------|
| `overlapping_clips` | 同一レイヤーでのクリップ重複 |
| `clip_bounds` | クリップの時間範囲の妥当性 |
| `missing_assets` | 参照先アセットの存在確認 |
| `safe_zone` | セーフゾーン違反（画面外配置） |
| `empty_layers` | 空レイヤーの検出 |
| `audio_sync` | 音声と映像の同期チェック |
| `duration_consistency` | 全体尺の整合性 |
| `text_readability` | テキストの可読性（サイズ・コントラスト） |
| `layer_ordering` | レイヤー順序の妥当性 |
| `gap_detection` | 映像の隙間検出 |

```bash
curl -X POST \
  "${API_BASE}/api/projects/${PROJECT_ID}/preview/validate" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"rules": null}'
```

特定ルールのみ検証する場合:
```bash
curl -X POST \
  "${API_BASE}/api/projects/${PROJECT_ID}/preview/validate" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"rules": ["overlapping_clips", "missing_assets", "gap_detection"]}'
```

### sample-event-points: 主要フレーム視覚検証

```
POST /api/projects/{project_id}/preview/sample-event-points
```

イベントポイント（クリップ境界、スライド切替、アバター登場など）を自動検出し、各ポイントのプレビューフレームを一括生成する。

```bash
curl -X POST \
  "${API_BASE}/api/projects/${PROJECT_ID}/preview/sample-event-points" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"max_samples": 10, "resolution": "640x360", "include_audio": true}'
```

検出されるイベントタイプ:
- `clip_start` / `clip_end` : クリップの開始・終了
- `slide_change` : スライド切替
- `section_boundary` : セクション境界
- `avatar_enter` / `avatar_exit` : アバター登場・退場
- `narration_start` / `narration_end` : ナレーション開始・終了
- `silence_gap` : 無音区間
- `effect_point` : エフェクトポイント
- `layer_change` : レイヤー変更

関連エンドポイント:
```
POST /api/projects/{project_id}/preview/event-points   # イベントポイント検出のみ（フレーム生成なし）
```

---

## V1 API 新エンドポイント

### Read-only

```
GET /api/ai/v1/projects/{project_id}/audio-clips/{clip_id}
```
音声クリップの詳細情報を取得する。（※ audio-clips はまだ専用 GET がないが、structure 経由で情報取得可能）

```
GET /api/ai/v1/capabilities
```
サポートされるオペレーション、エフェクト、easing 関数等の一覧を返す。AIはこの一覧を正として enum 値を選択する。

```
GET /api/ai/v1/projects/{project_id}/timeline-overview
```
L2.5 レベルのタイムライン全体概要。クリップ名・アセット名・ギャップ・重複・警告を含む構造監査用エンドポイント。

### PATCH (property updates)

```
PATCH /api/ai/v1/projects/{project_id}/clips/{clip_id}/timing
```
クリップのタイミング変更（start_ms、duration_ms）。Wave 2 で実装予定。

```
PATCH /api/ai/v1/projects/{project_id}/clips/{clip_id}/text
```
テキストクリップの内容変更。Wave 2 で実装予定。

```
PATCH /api/ai/v1/projects/{project_id}/clips/{clip_id}/shape
```
図形クリップのプロパティ変更。Wave 3 で実装予定。

```
PATCH /api/ai/v1/projects/{project_id}/audio-clips/{clip_id}
```
音声クリップのプロパティ更新（volume、fade_in_ms、fade_out_ms など）。Wave 2 で実装予定。

### Keyframes

```
POST /api/ai/v1/projects/{project_id}/clips/{clip_id}/keyframes
```
クリップにキーフレームを追加する。Wave 3 で実装予定。

```
DELETE /api/ai/v1/projects/{project_id}/clips/{clip_id}/keyframes/{kf_id}
```
キーフレームを削除する。Wave 3 で実装予定。

### Analysis

```
GET /api/ai/v1/projects/{project_id}/analysis/gaps
```
ギャップ分析。タイムライン内の映像・音声の隙間を検出する。Wave 3 で実装予定。

```
GET /api/ai/v1/projects/{project_id}/analysis/pacing
```
ペーシング分析。セクションごとの密度とテンポを評価する。Wave 3 で実装予定。

### Schema extensions (Wave 2-3)

| 拡張対象 | フィールド | 説明 |
|----------|-----------|------|
| text-style | `lineHeight` | 行間設定 |
| text-style | `letterSpacing` | 文字間隔 |
| crop | `resize_mode` | クロップ時のリサイズモード |

### Schemas

```
GET /api/ai/v1/schemas
```
スキーマ定義一覧。Wave 3 で実装予定。

---

## capabilities エンドポイント活用

```
GET /api/ai/v1/capabilities
```

このエンドポイントはAIが利用可能な全機能を把握するために使用する。

### 戻り値の構造

```json
{
  "data": {
    "api_version": "1.0",
    "schema_version": "1.0-unified",
    "supported_read_endpoints": ["GET /capabilities", "GET /version", "..."],
    "supported_operations": [
      "add_clip", "move_clip", "transform_clip", "update_effects",
      "chroma_key_preview", "chroma_key_apply", "update_crop",
      "update_text_style", "delete_clip",
      "add_layer", "update_layer", "reorder_layers",
      "add_audio_clip", "move_audio_clip", "delete_audio_clip", "add_audio_track",
      "add_marker", "update_marker", "delete_marker",
      "batch", "semantic", "rollback"
    ],
    "features": {
      "validate_only": true,
      "return_diff": true,
      "rollback": true,
      "history": true
    },
    "max_batch_ops": 20,
    "supported_effects": ["..."],
    "available_analysis_tools": ["validate_composition", "sample_frame", "sample_event_points"]
  }
}
```

### 活用方法

1. **supported_operations**: 実行可能なオペレーションの一覧。ここにないオペレーションは未実装。
2. **max_batch_ops**: バッチリクエストの最大オペレーション数。これを超える場合は分割する。
3. **supported_effects**: 利用可能なエフェクト名の一覧。enum 値の正解はここを参照する。
4. **available_analysis_tools**: 利用可能な分析ツール。validate_composition や sample_frame など。

AIは初回接続時に capabilities を取得し、サポートされる操作のみを使用すること。

---

## Support

- API Documentation: llms.txt, llms-full.txt
- Issue Reports: Contact development team
- Version Info: Check response headers for API version
