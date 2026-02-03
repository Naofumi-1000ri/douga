# API Examples (Target)

最終更新: 2026-02-03

この例は**AIが操作する前提**の理想フローを示します。

> すべての POST/PUT/PATCH/DELETE は `Idempotency-Key` と `If-Match` を付与する。

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
    "effects": ["chroma_key", "crop", "speed", "fade"],
    "easings": ["linear", "ease_in", "ease_out", "ease_in_out"],
    "blend_modes": ["normal", "multiply", "screen", "overlay"],
    "transitions": ["cut", "fade", "crossfade", "dip_to_black", "dip_to_white", "wipe_left", "wipe_right", "wipe_up", "wipe_down"],
    "font_families": ["Noto Sans JP", "Noto Sans", "Roboto", "Inter"],
    "shape_types": ["rect", "ellipse", "triangle", "line"],
    "text_aligns": ["left", "center", "right"],
    "track_types": ["narration", "bgm", "se", "custom"],
    "max_layers": 5,
    "max_duration_ms": 3600000
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
    "return_diff": true
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
    "return_diff": true
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
    "return_diff": false
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

## 2. バッチ操作（atomic）

```json
POST /api/ai/v1/projects/{project_id}/batch
{
  "options": {
    "validate_only": false,
    "return_diff": true
  },
  "atomic": true,
  "operations": [
    {"op": "add_clip", "data": {"type": "text", "layer_id": "...", "start_ms": 0, "duration_ms": 3000, "transform": {"position": {"x": 0, "y": 0}, "scale": {"x": 1, "y": 1}, "rotation": 0, "opacity": 1, "anchor": {"x": 0.5, "y": 0.5}}}},
    {"op": "move_clip", "data": {"clip_id": "...", "new_start_ms": 6000}}
  ]
}
```

---

## 3. Plan → validate → apply

```json
POST /api/ai/v1/projects/{project_id}/plans
{
  "options": {
    "validate_only": false,
    "return_diff": false
  },
  "title": "Intro cleanup",
  "steps": [
    {"op": "close_gap", "data": {"layer_id": "..."}},
    {"op": "auto_duck_bgm", "data": {"track_id": "...", "duck_db": -12}}
  ]
}
```

```json
POST /api/ai/v1/projects/{project_id}/plans/{plan_id}/validate
{}
```

```json
POST /api/ai/v1/projects/{project_id}/plans/{plan_id}/apply
{
  "options": {
    "validate_only": false,
    "return_diff": true
  }
}
```

---

## 4. Diff の取得

```json
POST /api/ai/v1/projects/{project_id}/diff
{
  "operations": [
    {"op": "move_clip", "data": {"clip_id": "...", "new_start_ms": 10000}}
  ]
}
```

---

## 5. Rollback

```json
POST /api/ai/v1/projects/{project_id}/operations/{operation_id}/rollback
{
  "options": {
    "validate_only": false,
    "return_diff": true
  }
}
```
