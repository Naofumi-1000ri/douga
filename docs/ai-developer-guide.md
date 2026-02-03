# Douga AI Developer Guide

> Practical guide for building AI assistants that integrate with the Douga video editor.
> Last updated: 2026-02-03

## Overview

This guide helps AI developers create reliable, user-friendly integrations with Douga's video editing API. The core principle is:

**AIが迷わない = 最高のプロダクト** (AI that doesn't hesitate = best product)

## Core Principles

| Principle | Description |
|-----------|-------------|
| **Deterministic** | Same input → Same result. No exceptions. |
| **Verifiable** | Use validate_composition and sample_frame for checks. |
| **Reversible** | No API rollback; keep client-side snapshots or use UI undo. |
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
| "このクリップ消して" | Medium | 90% | Execute (no API rollback; report clearly) |
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
GET /api/ai/project/{id}/overview      # L1
GET /api/ai/project/{id}/structure     # L2
GET /api/ai/project/{id}/clip/{cid}    # L3
GET /api/ai/project/{id}/assets        # L2
```

### Write (Verify IDs first)
```
POST   /api/ai/project/{id}/clips           # Add clip
PATCH  /api/ai/project/{id}/clip/{cid}/move # Move clip
DELETE /api/ai/project/{id}/clip/{cid}      # Delete clip
```

### Semantic (Preferred for complex ops)
```
POST /api/ai/project/{id}/semantic
{
  "operation": "snap_to_previous" | "close_gap" | "auto_duck_bgm",
  "target_clip_id": "..." | "target_layer_id": "..."
}
```

---

## Future Features

These features are planned and documented for forward compatibility:

| Feature | Status | Description |
|---------|--------|-------------|
| validate_only | Planned | Pre-validate without executing |
| rollback_token | Planned | Undo individual operations |
| snapshots | Planned | Named restore points |
| operation history | Planned | Full audit trail |

When implementing, check API version to see if features are available.

---

## Support

- API Documentation: llms.txt, llms-full.txt
- Issue Reports: Contact development team
- Version Info: Check response headers for API version
