# MCP <-> V1 API Endpoint Mapping

## Overview

This document maps the correspondence between MCP tools (defined in `server.py`) and V1 REST API endpoints (defined in `api/ai_v1.py`).

- **MCP tools**: 35 tools defined in `backend/src/mcp/server.py`
- **V1 endpoints**: 42 routes (41 unique handlers) defined in `backend/src/api/ai_v1.py` (prefix: `/api/ai/v1`)
- **Old AI endpoints**: MCP currently calls the old AI router (`/api/ai/project/...`), not V1

### Important: MCP calls the OLD API, not V1

The MCP server currently calls the **old** AI API (`/api/ai/project/{id}/...`) and other non-V1 routers.
V1 is mounted at `/api/ai/v1/projects/{id}/...` and provides enhanced features (envelope responses, ETags, operation history, rollback, validate_only, etc.).

---

## Mapping Table: MCP Tools -> V1 Endpoints

### Read Tools (L1/L2/L3)

| # | MCP Tool | MCP calls (old API) | V1 Endpoint | Method | V1 Path | Description |
|---|----------|---------------------|-------------|--------|---------|-------------|
| 1 | `get_project_overview` | `GET /api/ai/project/{id}/overview` | `get_project_overview` | GET | `/projects/{id}/overview` | L1 project overview (~300 tokens) |
| 2 | `get_timeline_structure` | `GET /api/ai/project/{id}/structure` | `get_timeline_structure` | GET | `/projects/{id}/structure` | L2 timeline structure (~800 tokens) |
| 3 | `get_timeline_at_time` | `GET /api/ai/project/{id}/at-time/{time_ms}` | `get_timeline_at_time` | GET | `/projects/{id}/at-time/{time_ms}` | L2 timeline state at specific time |
| 4 | `get_asset_catalog` | `GET /api/ai/project/{id}/assets` | `get_asset_catalog` | GET | `/projects/{id}/assets` | L2 asset catalog |
| 5 | `get_clip_details` | `GET /api/ai/project/{id}/clip/{clip_id}` | `get_clip_details` | GET | `/projects/{id}/clips/{clip_id}` | L3 video clip details |
| 6 | `get_audio_clip_details` | `GET /api/ai/project/{id}/audio-clip/{clip_id}` | `get_audio_clip_details` | GET | `/projects/{id}/audio-clips/{clip_id}` | L3 audio clip details |

### Write Tools: Layers

| # | MCP Tool | MCP calls (old API) | V1 Endpoint | Method | V1 Path | Description |
|---|----------|---------------------|-------------|--------|---------|-------------|
| 7 | `add_layer` | `POST /api/ai/project/{id}/layers` | `add_layer` | POST | `/projects/{id}/layers` | Create new layer |
| 8 | `reorder_layers` | `PUT /api/ai/project/{id}/layers/order` | `reorder_layers` | PUT | `/projects/{id}/layers/order` | Reorder layers |
| 9 | `update_layer` | `PATCH /api/ai/project/{id}/layer/{layer_id}` | `update_layer` | PATCH | `/projects/{id}/layers/{layer_id}` | Update layer properties |

### Write Tools: Video Clips

| # | MCP Tool | MCP calls (old API) | V1 Endpoint | Method | V1 Path | Description |
|---|----------|---------------------|-------------|--------|---------|-------------|
| 10 | `add_clip` | `POST /api/ai/project/{id}/clips` | `add_clip` | POST | `/projects/{id}/clips` | Add video clip |
| 11 | `move_clip` | `PATCH /api/ai/project/{id}/clip/{clip_id}/move` | `move_clip` | PATCH | `/projects/{id}/clips/{clip_id}/move` | Move clip position/layer |
| 12 | `update_clip_transform` | `PATCH /api/ai/project/{id}/clip/{clip_id}/transform` | `transform_clip` | PATCH | `/projects/{id}/clips/{clip_id}/transform` | Update position/scale/rotation |
| 13 | `update_clip_effects` | `PATCH /api/ai/project/{id}/clip/{clip_id}/effects` | `update_clip_effects` | PATCH | `/projects/{id}/clips/{clip_id}/effects` | Update opacity/chroma key |
| 14 | `delete_clip` | `DELETE /api/ai/project/{id}/clip/{clip_id}` | `delete_clip` | DELETE | `/projects/{id}/clips/{clip_id}` | Delete video clip |

### Write Tools: Audio Clips

| # | MCP Tool | MCP calls (old API) | V1 Endpoint | Method | V1 Path | Description |
|---|----------|---------------------|-------------|--------|---------|-------------|
| 15 | `add_audio_clip` | `POST /api/ai/project/{id}/audio-clips` | `add_audio_clip` | POST | `/projects/{id}/audio-clips` | Add audio clip |
| 16 | `move_audio_clip` | `PATCH /api/ai/project/{id}/audio-clip/{clip_id}/move` | `move_audio_clip` | PATCH | `/projects/{id}/audio-clips/{clip_id}/move` | Move audio clip |
| 17 | `delete_audio_clip` | `DELETE /api/ai/project/{id}/audio-clip/{clip_id}` | `delete_audio_clip` | DELETE | `/projects/{id}/audio-clips/{clip_id}` | Delete audio clip |

### Semantic Operations

All semantic operations use the same V1 endpoint: `POST /projects/{id}/semantic`

| # | MCP Tool | Operation Name | V1 Endpoint | Description |
|---|----------|---------------|-------------|-------------|
| 18 | `snap_to_previous` | `snap_to_previous` | `execute_semantic` | Snap clip to end of previous clip |
| 19 | `snap_to_next` | `snap_to_next` | `execute_semantic` | Snap next clip to end of this clip |
| 20 | `close_gap` | `close_gap` | `execute_semantic` | Close all gaps in a layer |
| 21 | `auto_duck_bgm` | `auto_duck_bgm` | `execute_semantic` | Auto BGM volume ducking |
| 22 | `rename_layer` | `rename_layer` | `execute_semantic` | Rename a layer (convenience) |

### Analysis Tools

| # | MCP Tool | MCP calls (old API) | V1 Endpoint | Method | V1 Path | Description |
|---|----------|---------------------|-------------|--------|---------|-------------|
| 23 | `analyze_gaps` | `GET /api/ai/project/{id}/analysis/gaps` | `analyze_gaps` | GET | `/projects/{id}/analysis/gaps` | Find timeline gaps |
| 24 | `analyze_pacing` | `GET /api/ai/project/{id}/analysis/pacing` | `analyze_pacing` | GET | `/projects/{id}/analysis/pacing` | Analyze clip density |

### AI Video Production Tools (use non-V1 routers)

These MCP tools call endpoints outside the V1 router (projects router, ai-video router, render router).

| # | MCP Tool | MCP calls (actual) | Router | Description |
|---|----------|-------------------|--------|-------------|
| 25 | `scan_folder` | (local, no API call) | N/A | Scan local folder for media files |
| 26 | `create_project` | `POST /api/projects` | projects | Create new project |
| 27 | `upload_assets` | `POST /api/ai-video/projects/{id}/assets/batch-upload` | ai-video | Batch upload files |
| 28 | `reclassify_asset` | `PUT /api/ai-video/projects/{id}/assets/{asset_id}/reclassify` | ai-video | Correct asset classification |
| 29 | `get_ai_asset_catalog` | `GET /api/ai-video/projects/{id}/asset-catalog` | ai-video | AI-oriented asset catalog |
| 30 | `generate_plan` | `POST /api/ai-video/projects/{id}/plan/generate` | ai-video | Generate video plan (GPT-4o) |
| 31 | `get_plan` | `GET /api/ai-video/projects/{id}/plan` | ai-video | Get current video plan |
| 32 | `update_plan` | `PUT /api/ai-video/projects/{id}/plan` | ai-video | Update video plan |
| 33 | `apply_plan` | `POST /api/ai-video/projects/{id}/plan/apply` | ai-video | Apply plan to timeline |
| 34 | `render_video` | `POST /api/projects/{id}/render` | render | Start video rendering |
| 35 | `get_render_status` | `GET /api/projects/{id}/render/status` | render | Get render progress |

---

## V1 Only (No MCP Counterpart)

These V1 endpoints have no corresponding MCP tool.

| # | V1 Endpoint | Method | V1 Path | Description | Notes |
|---|------------|--------|---------|-------------|-------|
| 1 | `get_capabilities` | GET | `/capabilities` | API capabilities/version info | Infra/meta, not needed for MCP |
| 2 | `get_version` | GET | `/version` | API version | Infra/meta |
| 3 | `get_project_overview` (summary alias) | GET | `/projects/{id}/summary` | Alias for overview | Duplicate; MCP uses `/overview` |
| 4 | `get_timeline_overview` | GET | `/projects/{id}/timeline-overview` | L2.5 full timeline overview with clips/gaps/overlaps | Useful; MCP candidate |
| 5 | `preview_chroma_key` | POST | `/projects/{id}/clips/{clip_id}/chroma-key/preview` | Preview chroma key settings | Advanced feature |
| 6 | `apply_chroma_key` | POST | `/projects/{id}/clips/{clip_id}/chroma-key/apply` | Apply chroma key settings | Advanced feature |
| 7 | `update_clip_crop` | PATCH | `/projects/{id}/clips/{clip_id}/crop` | Update clip crop area | Advanced feature |
| 8 | `update_clip_text_style` | PATCH | `/projects/{id}/clips/{clip_id}/text-style` | Update text style (font, size, color) | Text editing |
| 9 | `add_audio_track` | POST | `/projects/{id}/audio-tracks` | Add new audio track | MCP has no track management |
| 10 | `update_audio_clip` | PATCH | `/projects/{id}/audio-clips/{clip_id}` | Update audio clip properties (volume, fades, keyframes) | MCP only has move/delete |
| 11 | `add_marker` | POST | `/projects/{id}/markers` | Add timeline marker | Marker management |
| 12 | `update_marker` | PATCH | `/projects/{id}/markers/{marker_id}` | Update marker | Marker management |
| 13 | `delete_marker` | DELETE | `/projects/{id}/markers/{marker_id}` | Delete marker | Marker management |
| 14 | `execute_batch` | POST | `/projects/{id}/batch` | Batch operations (multiple ops in one request) | Efficiency feature |
| 15 | `get_history` | GET | `/projects/{id}/history` | Operation history (paginated) | History/audit |
| 16 | `get_operation` | GET | `/projects/{id}/operations/{op_id}` | Operation details | History/audit |
| 17 | `rollback_operation` | POST | `/projects/{id}/operations/{op_id}/rollback` | Rollback operation | Undo support |
| 18 | `get_schemas` | GET | `/schemas` | Schema definitions | Infra/meta |
| 19 | `update_clip_timing` | PATCH | `/projects/{id}/clips/{clip_id}/timing` | Update duration, speed, in/out points | Timing editing |
| 20 | `update_clip_text` | PATCH | `/projects/{id}/clips/{clip_id}/text` | Update text clip content | Text editing |
| 21 | `update_clip_shape` | PATCH | `/projects/{id}/clips/{clip_id}/shape` | Update shape properties (fill, stroke, dimensions) | Shape editing |
| 22 | `add_keyframe` | POST | `/projects/{id}/clips/{clip_id}/keyframes` | Add animation keyframe | Animation |
| 23 | `delete_keyframe` | DELETE | `/projects/{id}/clips/{clip_id}/keyframes/{kf_id}` | Delete animation keyframe | Animation |

---

## MCP Unique Features (No V1 Equivalent)

| # | MCP Tool | Description | Notes |
|---|----------|-------------|-------|
| 1 | `scan_folder` | Scan local folder for media files | Client-side only, no API call |
| 2 | `rename_layer` (semantic) | Convenience wrapper for `update_layer` | Uses semantic endpoint; V1 has `update_layer` directly |

---

## V1 Enhanced Features Not Exposed via MCP

V1 provides these cross-cutting features that MCP does not currently leverage:

| Feature | Description | V1 Support |
|---------|-------------|------------|
| Envelope Responses | Standardized `{data, error, meta, warnings}` | All endpoints |
| ETag / If-Match | Concurrency control | All write endpoints |
| Idempotency-Key | Prevent duplicate writes | All write endpoints |
| validate_only | Dry-run validation | All write endpoints |
| include_diff | Get before/after diff | All write endpoints |
| Operation History | Track all changes | `GET /history`, `GET /operations/{id}` |
| Rollback | Undo operations | `POST /operations/{id}/rollback` |
| Edit Sessions | Sequence-based editing | `X-Edit-Session` header |
| Batch Operations | Multiple ops in one request | `POST /batch` |

---

## Migration Recommendation

To migrate MCP from old API to V1:

1. **Phase 1**: Update API paths from `/api/ai/project/{id}/...` to `/api/ai/v1/projects/{id}/...`
   - Note path differences: `project` -> `projects`, `clip` -> `clips`, `layer` -> `layers`, `audio-clip` -> `audio-clips`
2. **Phase 2**: Add V1-only tools (markers, batch, timing, text, shape, keyframes)
3. **Phase 3**: Leverage V1 features (validate_only, rollback, history)

### Path Difference Summary

| Resource | Old API Path | V1 Path |
|----------|-------------|---------|
| Project | `/project/{id}` | `/projects/{id}` |
| Clip | `/project/{id}/clip/{cid}` | `/projects/{id}/clips/{cid}` |
| Layer | `/project/{id}/layer/{lid}` | `/projects/{id}/layers/{lid}` |
| Audio clip | `/project/{id}/audio-clip/{cid}` | `/projects/{id}/audio-clips/{cid}` |
| Layers | `/project/{id}/layers` | `/projects/{id}/layers` |
| Audio clips | `/project/{id}/audio-clips` | `/projects/{id}/audio-clips` |
| Clips | `/project/{id}/clips` | `/projects/{id}/clips` |

---

## Statistics

| Category | Count |
|----------|-------|
| MCP Tools (total) | 35 |
| V1 Endpoints (routes) | 42 (41 unique handlers; `/summary` is alias for `/overview`) |
| Matched (MCP <-> V1) | 24 MCP tools map to V1 equivalents (rows #1-24 above) |
| V1 only (no MCP) | 23 endpoints |
| MCP only (no V1 equivalent) | 11 tools (ai-video, render, projects, scan_folder) |
