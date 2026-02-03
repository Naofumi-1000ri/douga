# Error Codes (Target)

最終更新: 2026-02-03

AIが自己修正できるように、**全エラーは構造化**する。

---

## 1. エラーフォーマット

```json
{
  "request_id": "uuid",
  "error": {
    "code": "TIMELINE_OVERLAP",
    "message": "Clips overlap on layer L3",
    "details": {"layer_id": "...", "clip_id": "..."},
    "location": {"path": "layers[2].clips[5]"},
    "suggested_fix": "Move clip to after previous clip end",
    "retryable": false
  }
}
```

---

## 2. エラーコード一覧

### 2.1 Validation
- `INVALID_INPUT`
- `MISSING_REQUIRED_FIELD`
- `OUT_OF_RANGE`
- `INVALID_ENUM`
- `INVALID_UUID`
- `INVALID_COLOR`
- `UNSUPPORTED_FONT_FAMILY`
- `UNSUPPORTED_BLEND_MODE`
- `UNSUPPORTED_EASING`
- `INVALID_TRANSITION_DURATION`

### 2.2 Timeline / Clip
- `TIMELINE_OVERLAP`
- `NEGATIVE_DURATION`
- `INVALID_TIME_RANGE`
- `CLIP_OUTSIDE_ASSET_RANGE`
- `CLIP_NOT_FOUND`
- `KEYFRAME_OUTSIDE_CLIP_RANGE`
- `VOLUME_KEYFRAME_OUTSIDE_CLIP_RANGE`

### 2.3 Layer / Track
- `LAYER_NOT_FOUND`
- `LAYER_LOCKED`
- `TRACK_NOT_FOUND`
- `TRACK_LOCKED`

### 2.4 Asset
- `ASSET_NOT_FOUND`
- `ASSET_UNAVAILABLE`
- `ASSET_TYPE_MISMATCH`

### 2.5 Concurrency / Version
- `VERSION_CONFLICT`
- `STALE_ETAG`

### 2.6 Batch / Plan
- `BATCH_PARTIAL_FAILURE`
- `PLAN_INVALID`
- `PLAN_CONFLICT`

### 2.7 Render
- `RENDER_FAILED`
- `RENDER_TIMEOUT`

---

## 3. suggested_fix の例

- `TIMELINE_OVERLAP`: "Move clip start_ms to previous clip end_ms"
- `MISSING_REQUIRED_FIELD`: "Provide field 'start_ms' (int, >=0)"
- `VERSION_CONFLICT`: "Fetch latest project and retry with new ETag"
- `UNSUPPORTED_FONT_FAMILY`: "Use one of /capabilities.font_families"
- `INVALID_UUID`: "IDs must be UUID v4 (see schema)"
- `INVALID_TRANSITION_DURATION`: "Use 0ms for cut, otherwise 100-2000ms"

---

## 4. retryable

- `retryable = true` は **通信・一時的失敗のみ**
- **バリデーション系は常に false**
