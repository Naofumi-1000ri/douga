# API Examples (v1 Current)

最終更新: 2026-02-04

この例は **v1 現行実装** を前提にしています。

> POST/PUT/PATCH/DELETE は `Idempotency-Key` を付与する（validate_only=false の場合）。
> `If-Match` は推奨（不一致は 409）。

---

## 0. Capabilities の取得（例）

> ここに示す配列は **例**。実際の値は必ず `GET /capabilities` を呼んで取得する。

```json
GET /api/ai/v1/capabilities
```

```json
{
  "request_id": "d6f9a7a2-7a5b-4d0e-8f68-2a0a1c1f9b21",
  "data": {
    "effects": ["opacity", "blend_mode", "chroma_key", "fade"],
    "easings": ["linear", "ease_in", "ease_out", "ease_in_out"],
    "blend_modes": ["normal", "multiply", "screen", "overlay"],
    "transitions": ["cut", "fade", "crossfade", "dip_to_black", "dip_to_white", "wipe_left", "wipe_right", "wipe_up", "wipe_down"],
    "font_families": ["Noto Sans JP", "Noto Sans", "Roboto", "Inter"],
    "shape_types": ["rect", "ellipse", "triangle", "line"],
    "text_aligns": ["left", "center", "right"],
    "track_types": ["narration", "bgm", "se", "custom"],
    "max_layers": 5,
    "max_duration_ms": 3600000,
    "max_batch_ops": 20
  }
}
```

---

## 1. クリップ追加（validate-only → apply）

### validate-only
```json
POST /api/ai/v1/projects/{project_id}/clips
{
  "options": {
    "validate_only": true,
    "include_diff": true
  },
  "clip": {
    "type": "video",
    "layer_id": "...",
    "asset_id": "...",
    "start_ms": 5000,
    "duration_ms": 4000,
    "in_point_ms": 0,
    "out_point_ms": 4000,
    "transform": {
      "position": {"x": 0, "y": 0},
      "scale": {"x": 1, "y": 1},
      "rotation": 0,
      "opacity": 1,
      "anchor": {"x": 0.5, "y": 0.5}
    }
  }
}
```

### apply
```json
POST /api/ai/v1/projects/{project_id}/clips
{
  "options": {
    "validate_only": false,
    "include_diff": true
  },
  "clip": { ... }
}
```

---

## 1.1 トランジション指定（例）

```json
POST /api/ai/v1/projects/{project_id}/clips
{
  "options": {
    "validate_only": true,
    "include_diff": false
  },
  "clip": {
    "type": "video",
    "layer_id": "...",
    "asset_id": "...",
    "start_ms": 0,
    "duration_ms": 4000,
    "transform": {
      "position": {"x": 0, "y": 0},
      "scale": {"x": 1, "y": 1},
      "rotation": 0,
      "opacity": 1,
      "anchor": {"x": 0.5, "y": 0.5}
    },
    "transition_out": { "type": "fade", "duration_ms": 500 }
  }
}
```

---

## 2. バッチ操作（best_effort）

```json
POST /api/ai/v1/projects/{project_id}/batch
{
  "options": {
    "validate_only": false,
    "include_diff": true
  },
  "operations": [
    {"op": "add_clip", "data": {"type": "text", "layer_id": "...", "start_ms": 0, "duration_ms": 3000, "transform": {"position": {"x": 0, "y": 0}, "scale": {"x": 1, "y": 1}, "rotation": 0, "opacity": 1, "anchor": {"x": 0.5, "y": 0.5}}}},
    {"op": "move_clip", "data": {"clip_id": "...", "new_start_ms": 6000}}
  ]
}
```

---

## 3. Rollback

```json
POST /api/ai/v1/projects/{project_id}/operations/{operation_id}/rollback
```

---

## 4. Chroma Key Preview (5分割)

```json
POST /api/ai/v1/projects/{project_id}/clips/{clip_id}/chroma-key/preview
{
  "key_color": "auto",
  "similarity": 0.4,
  "blend": 0.1,
  "resolution": "640x360"
}
```

**Response (example)**:
```json
{
  "data": {
    "resolved_key_color": "#2AB450",
    "frames": [
      { "time_ms": 1200, "resolution": "640x360", "size_bytes": 42311, "frame_base64": "..." },
      { "time_ms": 3600, "resolution": "640x360", "size_bytes": 41502, "frame_base64": "..." },
      { "time_ms": 6000, "resolution": "640x360", "size_bytes": 40188, "frame_base64": "..." },
      { "time_ms": 8400, "resolution": "640x360", "size_bytes": 40912, "frame_base64": "..." },
      { "time_ms": 10800, "resolution": "640x360", "size_bytes": 42031, "frame_base64": "..." }
    ]
  }
}
```

---

## 5. Chroma Key Apply (新規アセット生成)

```json
POST /api/ai/v1/projects/{project_id}/clips/{clip_id}/chroma-key/apply
{
  "key_color": "auto",
  "similarity": 0.4,
  "blend": 0.1
}
```

**Response (example)**:
```json
{
  "data": {
    "resolved_key_color": "#2AB450",
    "asset_id": "uuid",
    "asset": { "id": "uuid", "name": "clip_chroma.webm", "type": "video", "mime_type": "video/webm" }
  }
}
```
