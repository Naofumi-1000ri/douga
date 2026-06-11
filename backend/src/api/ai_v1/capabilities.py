"""Capabilities, version, schemas, and catch-all endpoints for ai_v1 API."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from src.api.ai_v1._helpers import (
    OPERATION_DETAILS,
    _find_allowed_methods,
    envelope_error,
    envelope_success,
    logger,
)
from src.api.deps import CurrentUser, OptionalUser
from src.middleware.request_context import build_meta, create_request_context
from src.schemas.ai import (
    AddAudioClipRequest,
    AddClipRequest,
    BatchClipOperation,
    GapAnalysisResult,
    L1ProjectOverview,
    L2AssetCatalog,
    L2TimelineAtTime,
    L2TimelineStructure,
    L3AudioClipDetails,
    L3ClipDetails,
    L25TimelineOverview,
    MoveClipRequest,
    PacingAnalysisResult,
    SemanticOperation,
    UpdateClipEffectsRequest,
    UpdateClipTextRequest,
    UpdateClipTextStyleRequest,
    UpdateClipTimingRequest,
    UpdateClipTransformRequest,
)
from src.schemas.effects_generated import EFFECTS_CAPABILITIES
from src.schemas.envelope import EnvelopeResponse, ErrorInfo
from src.schemas.options import OperationOptions
from src.utils.interpolation import EASING_FUNCTIONS

router = APIRouter()
catch_all_router = APIRouter()


@router.get("/capabilities", response_model=EnvelopeResponse)
async def get_capabilities(
    current_user: OptionalUser,
    response: Response,
    include: str = "all",
) -> EnvelopeResponse:
    """Get API capabilities.

    Args:
        include: Detail level.
                 "all" (default) returns full capabilities (~53KB).
                 "overview" returns a lightweight summary (~15KB) with semantic_operations
                 as names only and request_formats omitted.
                 "minimal" returns ultra-compact version (~5KB) with endpoint list,
                 semantic operation names, recommended_workflow, and authentication only.
                 Note: "minimal" is accessible without authentication.
    """
    # Unauthenticated access is only allowed for include=minimal
    if current_user is None and include != "minimal":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Authentication required for full capabilities. "
                "Use ?include=minimal for unauthenticated access, or provide "
                "an 'X-API-Key: douga_sk_...' or 'Authorization: Bearer <token>' header."
            ),
        )

    context = create_request_context()
    logger.info(
        "v1.get_capabilities include=%s authenticated=%s", include, current_user is not None
    )

    capabilities = {
        "api_version": "1.0",
        "schema_version": "1.0-unified",  # Accepts both flat and nested clip formats
        "CRITICAL_HEADERS": {
            "X-API-Key": "REQUIRED on every request. Your API key (douga_sk_...).",
            "Idempotency-Key": (
                "REQUIRED on ALL write/mutation requests (POST, PATCH, DELETE, PUT). "
                "Must be a UUID v4 string (e.g., '550e8400-e29b-41d4-a716-446655440000'). "
                "Generate a new UUID for each distinct operation. "
                "If omitted, write requests will fail with IDEMPOTENCY_MISSING error."
            ),
            "Content-Type": "application/json (for all POST/PATCH/PUT requests)",
        },
        "authentication": {
            "methods": [
                {
                    "type": "api_key",
                    "header": "X-API-Key",
                    "format": "douga_sk_...",
                    "description": "Production API key (set via project settings)",
                },
                {
                    "type": "bearer",
                    "header": "Authorization",
                    "format": "Bearer <firebase_token>",
                    "description": "Firebase auth token",
                },
            ],
            "dev_mode": "In development mode, 'Bearer dev-token' is accepted",
        },
        "supported_read_endpoints": [
            # All read endpoints are implemented and available
            "GET /capabilities",
            "GET /version",
            "GET /projects",  # List all projects (id, name, created_at, ...)
            "POST /projects",  # Create a new project (name, width, height, fps)
            "GET /projects/{project_id}/overview",
            "GET /projects/{project_id}/structure",
            "GET /projects/{project_id}/timeline-overview",  # L2.5: Full overview
            "GET /projects/{project_id}/assets",
            # Priority 5: Advanced read endpoints
            "GET /projects/{project_id}/clips/{clip_id}",  # Single clip details
            "GET /projects/{project_id}/audio-clips/{clip_id}",  # Single audio clip details
            "GET /projects/{project_id}/at-time/{time_ms}",  # Timeline at specific time
            # Analysis endpoints (read)
            "GET /projects/{project_id}/analysis/gaps",  # Find gaps across layers/tracks
            "GET /projects/{project_id}/analysis/pacing",  # Clip density & pacing analysis
            # Schema definitions
            "GET /schemas",  # All available schema definitions with levels and endpoints
            # History and operation endpoints
            "GET /projects/{project_id}/history",  # Operation history
            "GET /projects/{project_id}/operations/{operation_id}",  # Operation details
            # Preview / visual inspection (POST but read-only, outside /api/ai/v1 — see preview_api section)
            "POST /api/projects/{project_id}/preview/event-points",  # Detect key events
            "POST /api/projects/{project_id}/preview/sample-frame",  # Render single frame (Base64 JPEG)
            "POST /api/projects/{project_id}/preview/sample-event-points",  # Events + frames in one call
            "POST /api/projects/{project_id}/preview/validate",  # Composition validation
        ],
        "supported_operations": [
            # Write operations currently implemented in v1
            # Priority 1: Clips
            "add_clip",  # POST /projects/{id}/clips
            "move_clip",  # PATCH /projects/{id}/clips/{clip_id}/move
            "transform_clip",  # PATCH /projects/{id}/clips/{clip_id}/transform
            "update_effects",  # PATCH /projects/{id}/clips/{clip_id}/effects
            "chroma_key_preview",  # POST /projects/{id}/clips/{clip_id}/chroma-key/preview
            "chroma_key_apply",  # POST /projects/{id}/clips/{clip_id}/chroma-key/apply
            "update_crop",  # PATCH /projects/{id}/clips/{clip_id}/crop
            "update_text_style",  # PATCH /projects/{id}/clips/{clip_id}/text-style
            "delete_clip",  # DELETE /projects/{id}/clips/{clip_id}
            # Priority 2: Layers
            "add_layer",  # POST /projects/{id}/layers
            "update_layer",  # PATCH /projects/{id}/layers/{layer_id}
            "reorder_layers",  # PUT /projects/{id}/layers/order
            # Priority 3: Audio
            "add_audio_clip",  # POST /projects/{id}/audio-clips
            "move_audio_clip",  # PATCH /projects/{id}/audio-clips/{clip_id}/move
            "update_audio_clip",  # PATCH /projects/{id}/audio-clips/{clip_id} (volume, fades, volume_keyframes)
            "delete_audio_clip",  # DELETE /projects/{id}/audio-clips/{clip_id}
            "add_audio_track",  # POST /projects/{id}/audio-tracks
            # Priority 4: Markers
            "add_marker",  # POST /projects/{id}/markers
            "update_marker",  # PATCH /projects/{id}/markers/{marker_id}
            "delete_marker",  # DELETE /projects/{id}/markers/{marker_id}
            # Priority 5: Advanced operations
            "batch",  # POST /projects/{id}/batch
            "semantic",  # POST /projects/{id}/semantic
            # Clip property updates
            "update_timing",  # PATCH /projects/{id}/clips/{clip_id}/timing (duration, speed, in/out points)
            "update_text",  # PATCH /projects/{id}/clips/{clip_id}/text (text content for text clips)
            "update_shape",  # PATCH /projects/{id}/clips/{clip_id}/shape (fill, stroke, dimensions)
            # Keyframe animation
            "add_keyframe",  # POST /projects/{id}/clips/{clip_id}/keyframes
            "delete_keyframe",  # DELETE /projects/{id}/clips/{clip_id}/keyframes/{keyframe_id}
            # Linked audio operations
            "split_clip",  # POST /projects/{id}/clips/{clip_id}/split
            "unlink_clip",  # POST /projects/{id}/clips/{clip_id}/unlink
            # History and rollback
            "rollback",  # POST /projects/{id}/operations/{op_id}/rollback
            # Preview diff
            "preview-diff",  # POST /projects/{id}/preview-diff
        ],
        "planned_operations": [
            # All write operations are now implemented in v1
        ],
        "planned_endpoints": [
            "POST /projects/{project_id}/analysis/composition",  # Full report: gaps, pacing, audio, layers, suggestions, score
            "POST /projects/{project_id}/analysis/suggestions",  # Lightweight: suggestions + quality_score only
            "POST /projects/{project_id}/analysis/sections",  # Detect logical sections/segments
            "POST /projects/{project_id}/analysis/audio-balance",  # Detailed audio balance analysis
        ],
        "operation_details": OPERATION_DETAILS,
        "features": {
            "validate_only": True,
            "return_diff": True,  # Use options.include_diff=true to get diff in response
            "rollback": True,  # POST /operations/{id}/rollback
            "history": True,  # GET /history, GET /operations/{id}
        },
        "schema_notes": {
            "coordinate_system": {
                "description": "Clip position uses center-relative coordinates. (0,0) = canvas center (pixel 960,540 for 1920x1080).",
                "x": "Horizontal offset from center. 0=center, positive=right, negative=left. Range: -960 to +960 for on-screen.",
                "y": "Vertical offset from center. 0=center, positive=down, negative=up. Range: -540 to +540 for on-screen.",
                "safe_zone": "5% margin from edges. Safe x range: -864 to +864. Safe y range: -486 to +486.",
                "examples": {
                    "center": {"x": 0, "y": 0},
                    "top_left": {"x": -480, "y": -270},
                    "bottom_right": {"x": 480, "y": 270},
                    "bottom_subtitle": {"x": 0, "y": 380},
                },
            },
            "clip_format": "unified",  # Accepts both flat and nested formats
            "clip_id_format": (
                "Clip IDs in timeline-overview are short IDs (first 8 chars of UUID). "
                "Both short IDs and full UUIDs are accepted by all clip endpoints."
            ),
            "transform_formats": ["flat", "nested"],  # x/y/scale or transform.position/scale
            "flat_example": {"layer_id": "...", "x": 0, "y": 0, "scale": 1.0},
            "nested_example": {
                "type": "video",
                "layer_id": "...",
                "transform": {"position": {"x": 0, "y": 0}, "scale": {"x": 1, "y": 1}},
            },
            "transform_field_reference": {
                "description": "Complete list of supported fields for PATCH /clips/{id}/transform. "
                "All fields are optional (PATCH semantics: only provided fields are updated).",
                "flat_format_fields": {
                    "x": "float, -3840..3840 — X offset from canvas center in pixels (0 = center, positive = right)",
                    "y": "float, -2160..2160 — Y offset from canvas center in pixels (0 = center, positive = down)",
                    "scale": "float, 0.01..10.0 — Uniform scale factor (1.0 = original size)",
                    "width": "float, 1..7680 — Width in pixels (alternative to scale)",
                    "height": "float, 1..4320 — Height in pixels (alternative to scale)",
                    "rotation": "float, -360..360 — Rotation in degrees",
                    "anchor": "enum: center | top-left | top-right | bottom-left | bottom-right",
                },
                "nested_format_fields": {
                    "transform.position.x": "float — same as flat 'x'",
                    "transform.position.y": "float — same as flat 'y'",
                    "transform.scale.x": "float — used as uniform scale (scale.y is ignored, coerced to scale.x)",
                    "transform.rotation": "float — same as flat 'rotation'",
                },
                "important_notes": [
                    "scale_x and scale_y are NOT valid flat-format fields. Use 'scale' for uniform scaling.",
                    "Non-uniform scaling (different X/Y) is NOT supported. Nested scale.y is coerced to scale.x.",
                    "Use 'width'/'height' if you need to set exact pixel dimensions instead of a scale factor.",
                    "Flat fields take precedence over nested transform fields when both are provided.",
                ],
            },
            "supported_transform_fields": [
                "x",
                "y",
                "scale",
                "width",
                "height",
                "rotation",
                "anchor",
                "transform.position.x (nested)",
                "transform.position.y (nested)",
                "transform.scale.x (nested)",
                "transform.rotation (nested)",
            ],
            "chroma_key_preview_samples": [0.1, 0.3, 0.5, 0.7, 0.9],
            "unsupported_transform_fields": [
                "scale_x (use 'scale' instead)",
                "scale_y (use 'scale' instead)",
                "opacity (use PATCH /clips/{id}/effects instead)",
                "transform.opacity (not supported)",
                "transform.anchor (not yet supported in nested format; use flat 'anchor' field)",
                "Non-uniform scale (scale.y coerced to scale.x in nested format)",
            ],
            "unsupported_clip_fields": [
                "transition_in",
                "transition_out",
            ],
            "transitions_note": (
                "Transitions (fade, slide, wipe, etc.) between clips are NOT currently supported. "
                "The transition_in and transition_out fields in clip responses are always 'none'. "
                "To achieve fade effects, use the 'effects' endpoint: "
                'PATCH /clips/{id}/effects with {"fade_in_ms": 500, "fade_out_ms": 500}.'
            ),
            "batch_operation_names": (
                "IMPORTANT: Batch operations use short names, NOT the endpoint names. "
                "Use: 'add' (not 'add_clip'), 'move' (not 'move_clip'), 'trim' (not 'update_timing'), "
                "'update_transform' (not 'transform_clip'), 'update_effects', 'delete' (not 'delete_clip'), "
                "'update_layer', 'update_text_style', 'update_text', 'split'. Data goes in the 'data' field. "
                'Example: {"operation": "add", "data": {"layer_id": "...", "asset_id": "...", '
                '"start_ms": 0, "duration_ms": 5000}}'
            ),
            "batch_add_transform_note": (
                "Batch 'add' operations support inline transform fields (x, y, scale) in the clip data. "
                "This avoids a separate update_transform call after adding. Example: "
                '{"operation": "add", "data": {"asset_id": "...", "layer_id": "...", '
                '"start_ms": 0, "duration_ms": 5000, "x": 0, "y": 0, "scale": 1.0}}'
            ),
            "effects_note": "Effects (opacity, fade, chroma_key, blend_mode) cannot be set directly in add_clip. Use PATCH /clips/{clip_id}/effects after adding the clip.",
            "text_style_note": "Unknown text_style keys preserved as-is (passthrough)",
            "text_style_color_format": "All color fields (color, background_color) must use hex format: #RRGGBB or #RRGGBBAA (with alpha). Example: '#FFFFFF' for white, '#00000080' for 50% transparent black. Do NOT use rgba(), rgb(), or named colors.",
            "semantic_operations": [
                {
                    "operation": "snap_to_previous",
                    "description": "Move a clip so it starts exactly where the previous clip ends (no gap).",
                    "required_fields": {
                        "target_clip_id": "ID of the clip to snap (at semantic level)",
                    },
                    "optional_fields": {},
                    "example": {
                        "semantic": {
                            "operation": "snap_to_previous",
                            "target_clip_id": "<clip-id>",
                            "parameters": {},
                        }
                    },
                },
                {
                    "operation": "snap_to_next",
                    "description": "Move the next clip so it starts exactly where this clip ends.",
                    "required_fields": {
                        "target_clip_id": "ID of the reference clip (at semantic level)",
                    },
                    "optional_fields": {},
                    "example": {
                        "semantic": {
                            "operation": "snap_to_next",
                            "target_clip_id": "<clip-id>",
                            "parameters": {},
                        }
                    },
                },
                {
                    "operation": "close_gap",
                    "description": "Close all gaps in a layer by shifting clips forward to remove spaces between them. Starts packing from time 0.",
                    "required_fields": {
                        "target_layer_id": "ID of the layer to close gaps in (at semantic level)",
                    },
                    "optional_fields": {},
                    "example": {
                        "semantic": {
                            "operation": "close_gap",
                            "target_layer_id": "<layer-id>",
                            "parameters": {},
                        }
                    },
                },
                {
                    "operation": "rename_layer",
                    "description": "Rename a layer.",
                    "required_fields": {
                        "target_layer_id": "ID of the layer to rename (at semantic level)",
                        "parameters.name": "New name for the layer",
                    },
                    "optional_fields": {},
                    "example": {
                        "semantic": {
                            "operation": "rename_layer",
                            "target_layer_id": "<layer-id>",
                            "parameters": {"name": "Background Video"},
                        }
                    },
                },
                {
                    "operation": "replace_clip",
                    "description": "Replace a clip's asset while preserving timing and position. Linked audio clips are also updated if new_audio_asset_id is provided.",
                    "required_fields": {
                        "target_clip_id": "ID of the clip to replace (at semantic level)",
                        "parameters.new_asset_id": "UUID of the replacement asset",
                    },
                    "optional_fields": {
                        "parameters.new_audio_asset_id": "UUID of the replacement audio asset for linked audio clips",
                        "parameters.new_duration_ms": "New duration in ms if the asset has a different length",
                    },
                    "example": {
                        "semantic": {
                            "operation": "replace_clip",
                            "target_clip_id": "<clip-id>",
                            "parameters": {"new_asset_id": "<asset-uuid>"},
                        }
                    },
                },
                {
                    "operation": "close_all_gaps",
                    "description": "Remove all gaps in a layer by packing clips tightly from the first clip's position. Linked audio clips are synced automatically. Clips exceeding project boundary are trimmed.",
                    "required_fields": {
                        "target_layer_id": "ID of the layer to pack (at semantic level)",
                    },
                    "optional_fields": {
                        "parameters.max_end_ms": "Maximum allowed end position in ms (default: project duration_ms). Clips exceeding this are trimmed.",
                    },
                    "example": {
                        "semantic": {
                            "operation": "close_all_gaps",
                            "target_layer_id": "<layer-id>",
                            "parameters": {},
                        }
                    },
                },
                {
                    "operation": "add_text_with_timing",
                    "description": "Add a text/telop clip synced to an existing clip's timing (same start_ms and duration_ms). Automatically finds or creates a text layer.",
                    "required_fields": {
                        "target_clip_id": "ID of the clip to sync timing with (at semantic level)",
                        "parameters.text_content": "Text content to display",
                    },
                    "optional_fields": {
                        "parameters.text": "Text content (legacy alias for text_content)",
                        "parameters.font_size": "Font size in pixels (default 48)",
                        "parameters.position": "Vertical position: 'top' (y=200), 'center' (y=540), or 'bottom' (y=800). Default 'bottom'.",
                    },
                    "example": {
                        "semantic": {
                            "operation": "add_text_with_timing",
                            "target_clip_id": "<clip-id>",
                            "parameters": {"text_content": "Hello World"},
                        }
                    },
                },
                {
                    "operation": "distribute_evenly",
                    "description": "Distribute clips evenly in a layer with optional gap between them. Linked audio clips are synced automatically.",
                    "required_fields": {
                        "target_layer_id": "ID of the layer to distribute clips in (at semantic level)",
                    },
                    "optional_fields": {
                        "parameters.start_ms": "Starting position in ms (default: first clip's current start_ms)",
                        "parameters.gap_ms": "Gap in ms between clips (default 0)",
                    },
                    "example": {
                        "semantic": {
                            "operation": "distribute_evenly",
                            "target_layer_id": "<layer-id>",
                            "parameters": {"gap_ms": 500},
                        }
                    },
                },
            ],
            "batch_operation_types": [
                "add",
                "move",
                "trim",
                "update_transform",
                "update_effects",
                "delete",
                "update_layer",
                "update_text_style",
                "update_text",
                "split",
            ],
            "batch_add_example": {
                "description": (
                    "Add multiple clips at once using batch. Each 'add' operation needs a 'clip' key "
                    "with the same fields as POST /clips. Transform fields (x, y, scale) can be included "
                    "inline to position clips in a single operation without a separate update_transform call."
                ),
                "body": {
                    "operations": [
                        {
                            "operation": "add",
                            "clip": {
                                "asset_id": "uuid-of-asset",
                                "layer_id": "uuid-of-layer",
                                "start_ms": 0,
                                "duration_ms": 5000,
                                "x": 0,
                                "y": 0,
                                "scale": 1.0,
                            },
                        },
                        {
                            "operation": "add",
                            "clip": {
                                "asset_id": "uuid-of-asset",
                                "layer_id": "uuid-of-layer",
                                "start_ms": 5000,
                                "duration_ms": 3000,
                            },
                        },
                    ],
                    "options": {},
                },
            },
            "asset_notes": {
                "duration_ms": (
                    "All asset types have duration_ms populated. Images default to 5000ms (same as suggested_display_duration_ms). "
                    "Video and audio durations are auto-detected via server-side probing after upload (~15s). "
                    "You can use duration_ms directly for all asset types when creating clips."
                ),
                "suggested_display_duration_ms": (
                    "Image assets include a suggested_display_duration_ms field (default 5000ms / 5 seconds) "
                    "as a recommended slide display time. Video and audio assets return null for this field."
                ),
            },
            "options_requirement": "All mutation endpoints recommend an 'options' field in the request body. If omitted, options defaults to an empty object {}. It can contain: validate_only (bool), include_audio (bool).",
        },
        "limits": {
            "max_duration_ms": 3600000,
            "max_file_size_mb": 500,
            "max_layers": 5,
            "max_clips_per_layer": 100,
            "max_audio_tracks": 10,
            "max_batch_ops": 20,
        },
        "default_clip_values": {
            "effects": {
                "opacity": 1.0,
                "blend_mode": "normal",
                "fade_in_ms": 0,
                "fade_out_ms": 0,
                "chroma_key": {
                    "description": "These are the DEFAULT values shown for reference only. "
                    "IMPORTANT: To SET chroma key via PATCH /clips/{id}/effects, use FLAT fields, NOT this nested format. "
                    "See the chroma_key_usage example below.",
                    "enabled": False,
                    "color": "#00FF00",
                    "similarity": 0.3,
                    "smoothness": 0.1,
                },
            },
            "transform": {
                "coordinate_system": "(0,0) = canvas center. Positive x = right, positive y = down.",
                "text_layer": {"x": 0, "y": 380, "scale": 1.0, "rotation": 0},
                "content_layer": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0},
                "background_layer": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0},
            },
        },
        "audio_features": {
            "volume_envelope": True,
            "volume_keyframe_format": {
                "time_ms": "int (relative to clip start)",
                "value": "float 0.0-1.0",
            },
            "interpolation": "linear",
        },
        "audio_track_types": ["narration", "bgm", "se", "video"],
        "effects": EFFECTS_CAPABILITIES["supported_effects"],
        "effect_params": EFFECTS_CAPABILITIES["effect_params"],
        "easings": sorted(EASING_FUNCTIONS.keys()),
        "blend_modes": ["normal"],
        "transitions": ["none"],
        "font_families": [
            "Noto Sans JP",
            "Noto Serif JP",
            "M PLUS 1p",
            "M PLUS Rounded 1c",
            "Kosugi Maru",
            "Sawarabi Gothic",
            "Sawarabi Mincho",
            "BIZ UDPGothic",
            "Zen Maru Gothic",
            "Shippori Mincho",
        ],
        "shape_types": ["rectangle", "circle", "line"],
        "text_aligns": ["left", "center", "right"],
        "track_types": ["narration", "bgm", "se", "video"],
        "preview_api": {
            "description": "Visual inspection APIs for AI-driven timeline verification without full renders. "
            "Use these to check composition visually before exporting.",
            "base_path": "/api/projects/{project_id}/preview",
            "note": "These endpoints are outside the /api/ai/v1 prefix. Use /api/projects/{project_id}/preview/... directly.",
            "endpoints": {
                "event_points": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/preview/event-points",
                    "description": "Detect key event points (clip boundaries, audio starts, section changes, silence gaps) for targeted inspection.",
                    "request_body": {
                        "include_audio": "bool (default true) — include audio events",
                        "include_visual": "bool (default true) — include visual layer events",
                        "min_gap_ms": "int (default 500) — minimum silence gap to detect",
                    },
                    "response": {
                        "event_points": "[{time_ms, event_type, description, layer?, clip_id?, metadata}]",
                        "total_events": "int",
                        "duration_ms": "int",
                    },
                    "event_types": [
                        "clip_start",
                        "clip_end",
                        "slide_change",
                        "section_boundary",
                        "avatar_enter",
                        "avatar_exit",
                        "narration_start",
                        "narration_end",
                        "bgm_start",
                        "se_trigger",
                        "silence_gap",
                        "effect_point",
                        "layer_change",
                    ],
                },
                "sample_frame": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/preview/sample-frame",
                    "description": "Render a single preview frame at a specific time. Returns a Base64-encoded JPEG image (~30-80KB at 640x360).",
                    "request_body": {
                        "time_ms": "int (required) — time position in milliseconds",
                        "resolution": "str (default '640x360') — output resolution WxH",
                    },
                    "response": {
                        "time_ms": "int",
                        "resolution": "str",
                        "frame_base64": "str — Base64-encoded JPEG",
                        "size_bytes": "int",
                        "active_clips": "[{clip_id, layer_name, asset_name, clip_type, transform, progress_percent}]",
                    },
                },
                "sample_event_points": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/preview/sample-event-points",
                    "description": "Auto-detect event points and render preview frames at each in one call. "
                    "Best for getting an overview of the entire timeline.",
                    "request_body": {
                        "max_samples": "int (default 10) — maximum frames to sample",
                        "resolution": "str (default '640x360') — output resolution WxH",
                        "include_audio": "bool (default true) — include audio events",
                        "min_gap_ms": "int (default 500) — minimum silence gap",
                    },
                    "response": {
                        "samples": "[{time_ms, event_type, description, frame_base64, active_clips}]",
                        "total_events": "int",
                        "sampled_count": "int",
                    },
                },
                "validate": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/preview/validate",
                    "description": "Check composition rules without rendering. Detects overlapping clips, missing assets, safe zone violations, and audio-visual sync issues.",
                    "request_body": {
                        "rules": "list[str] | null (default null = all rules)",
                    },
                    "response": {
                        "is_valid": "bool — true if no errors",
                        "issues": "[{rule, severity, message, time_ms?, clip_id?, suggestion?}]",
                        "total_issues": "int",
                        "errors": "int",
                        "warnings": "int",
                    },
                },
            },
            "sequence_targeting": {
                "description": "How to target a specific sequence for preview operations.",
                "query_parameter": {
                    "param": "sequence_id",
                    "type": "UUID",
                    "description": "Pass ?sequence_id=<UUID> to any preview endpoint for read-only access. No lock required.",
                    "example": "POST /api/projects/{project_id}/preview/sample-frame?sequence_id={seq_id}",
                },
                "edit_session_header": {
                    "description": "Use X-Edit-Session header with edit_token from lock for read-write session access.",
                    "steps": [
                        "1. POST /api/projects/{project_id}/sequences/{seq_id}/lock → get edit_token",
                        "2. Set X-Edit-Session: {edit_token} header on preview requests",
                        "3. POST /api/projects/{project_id}/preview/sample-frame with X-Edit-Session",
                        "4. POST /api/projects/{project_id}/sequences/{seq_id}/unlock when done",
                    ],
                },
                "fallback_behavior": "When neither sequence_id nor X-Edit-Session is provided, the project's default sequence is used. If no default sequence exists, the project's legacy timeline_data is used.",
                "priority": "X-Edit-Session > sequence_id query param > default sequence > project timeline_data",
            },
            "workflow_tips": [
                "1. Call validate first to check for structural issues",
                "2. Call sample-event-points for a visual overview of key moments",
                "3. Call sample-frame for targeted inspection at specific times",
                "4. Use X-Edit-Session header to preview unsaved sequence edits",
                "5. Use ?sequence_id=<UUID> to preview a specific sequence without acquiring a lock",
            ],
        },
        "ai_video_api": {
            "description": "AI-driven video production pipeline. Handles asset upload, plan generation, "
            "and automated skills (silence trimming, telop, layout, sync, click highlights, avatar dodge).",
            "base_path": "/api/ai-video",
            "note": "Outside /api/ai/v1 prefix. Use /api/ai-video/... directly.",
            "endpoints": {
                "capabilities": {
                    "method": "GET",
                    "path": "/api/ai-video/capabilities",
                    "description": "Full workflow guide with skill specs and dependency graph.",
                },
                "batch_upload": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/assets/batch-upload",
                    "description": "Upload multiple files with auto-classification and metadata probing (multipart).",
                },
                "asset_catalog": {
                    "method": "GET",
                    "path": "/api/ai-video/projects/{project_id}/asset-catalog",
                    "description": "AI-oriented asset catalog with type/subtype summary.",
                },
                "reclassify": {
                    "method": "PUT",
                    "path": "/api/ai-video/projects/{project_id}/assets/{asset_id}/reclassify",
                    "description": "Manually fix asset type/subtype classification.",
                },
                "transcription": {
                    "method": "GET",
                    "path": "/api/ai-video/projects/{project_id}/assets/{asset_id}/transcription",
                    "description": "Get STT transcription for an audio asset. "
                    "Auto-generated on upload for assets with speech (check has_transcription in asset catalog). "
                    "Returns {language, full_text, segments: [{text, start_ms, end_ms, confidence, type}], "
                    "total_segments, speech_segments, silence_segments}.",
                },
                "generate_plan": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/plan/generate",
                    "description": "Generate a VideoPlan from brief + asset catalog using AI (GPT-4o).",
                },
                "get_plan": {
                    "method": "GET",
                    "path": "/api/ai-video/projects/{project_id}/plan",
                    "description": "Get current video plan.",
                },
                "update_plan": {
                    "method": "PUT",
                    "path": "/api/ai-video/projects/{project_id}/plan",
                    "description": "Replace the video plan.",
                },
                "apply_plan": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/plan/apply",
                    "description": "Convert plan to timeline_data with audio extraction and chroma key.",
                },
                "skill_trim_silence": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/skills/trim-silence",
                    "description": "Trim leading/trailing silence from narration and linked avatar clips.",
                },
                "skill_add_telop": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/skills/add-telop",
                    "description": "Transcribe narration (Whisper STT) and place text clips on text layer.",
                },
                "skill_layout": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/skills/layout",
                    "description": "Apply layout transforms. Accepts optional avatar_position, avatar_size, screen_position.",
                },
                "skill_sync_content": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/skills/sync-content",
                    "description": "Variable-speed sync of operation screen to narration timing.",
                },
                "skill_click_highlight": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/skills/click-highlight",
                    "description": "Detect clicks in operation screen and add highlight shapes.",
                },
                "skill_avatar_dodge": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/skills/avatar-dodge",
                    "description": "Add dodge keyframes to avatar when click highlights overlap.",
                },
                "skill_run_all": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/skills/run-all",
                    "description": "Run all 6 skills in dependency order in one call. Stops on first failure.",
                },
                "check": {
                    "method": "POST",
                    "path": "/api/ai-video/projects/{project_id}/check",
                    "description": "Quality check: structure, plan-vs-actual, sync, gaps. Levels: quick/standard/deep.",
                },
            },
            "skill_order": [
                "trim-silence",
                "add-telop",
                "layout",
                "sync-content",
                "click-highlight",
                "avatar-dodge",
            ],
        },
        "render_api": {
            "description": "Async video rendering with progress tracking and download.",
            "base_path": "/api/projects/{project_id}/render",
            "note": "Outside /api/ai/v1 prefix. Use /api/projects/{project_id}/render/... directly.",
            "sequence_targeting": {
                "description": "How to target a specific sequence for render operations.",
                "query_parameter": {
                    "param": "sequence_id",
                    "type": "UUID",
                    "description": "Pass ?sequence_id=<UUID> to start/package endpoints for read-only access. No lock required.",
                    "example": "POST /api/projects/{project_id}/render?sequence_id={seq_id}",
                },
                "edit_session_header": {
                    "description": "Use X-Edit-Session header with edit_token from lock for read-write session access.",
                    "steps": [
                        "1. POST /api/projects/{project_id}/sequences/{seq_id}/lock → get edit_token",
                        "2. Set X-Edit-Session: {edit_token} header on render request",
                        "3. POST /api/projects/{project_id}/render with X-Edit-Session header",
                        "4. POST /api/projects/{project_id}/sequences/{seq_id}/unlock when done",
                    ],
                },
                "fallback_behavior": "When neither sequence_id nor X-Edit-Session is provided, the project's default sequence is used. If no default sequence exists, the project's legacy timeline_data is used.",
                "priority": "X-Edit-Session > sequence_id query param > default sequence > project timeline_data",
            },
            "endpoints": {
                "start": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/render",
                    "description": "Start a render job. Supports start_ms/end_ms for partial export, X-Edit-Session for sequence rendering, and ?sequence_id=<UUID> for lock-free sequence targeting.",
                    "query_params": {
                        "sequence_id": "UUID (optional) — render a specific sequence without a lock. Ignored when X-Edit-Session is set.",
                    },
                },
                "status": {
                    "method": "GET",
                    "path": "/api/projects/{project_id}/render/status",
                    "description": "Poll latest render job progress (status, progress %, stage).",
                },
                "cancel": {
                    "method": "DELETE",
                    "path": "/api/projects/{project_id}/render",
                    "description": "Cancel an active render job.",
                },
                "history": {
                    "method": "GET",
                    "path": "/api/projects/{project_id}/render/history",
                    "description": "List recent completed renders (up to 10) with signed download URLs.",
                },
                "download": {
                    "method": "GET",
                    "path": "/api/projects/{project_id}/render/download",
                    "description": "Get signed download URL for the latest completed render.",
                },
                "package": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/render/package",
                    "description": "Generate a client-side render package (ZIP with assets + FFmpeg scripts). "
                    "No FFmpeg execution on server — download ZIP and run locally with 'bash render.sh'. "
                    "The package is intended to reproduce the same final video as Export for the same input. "
                    "Returns {download_url, package_size, expires_at}. "
                    "Supports ?sequence_id=<UUID> for lock-free sequence targeting.",
                    "query_params": {
                        "sequence_id": "UUID (optional) — package a specific sequence without a lock. Ignored when X-Edit-Session is set.",
                    },
                },
            },
        },
        "sequences_api": {
            "description": "Multi-sequence timeline editing with optimistic locking and snapshots.",
            "base_path": "/api/projects/{project_id}/sequences",
            "note": "Outside /api/ai/v1 prefix. Use /api/projects/{project_id}/sequences/... directly. "
            "V1 endpoints support X-Edit-Session header to target a specific sequence.",
            "endpoints": {
                "list": {
                    "method": "GET",
                    "path": "/api/projects/{project_id}/sequences",
                    "description": "List all sequences for a project.",
                },
                "create": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/sequences",
                    "description": "Create a new sequence with empty timeline.",
                },
                "copy": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}/copy",
                    "description": "Copy a sequence with its timeline data.",
                },
                "get_default": {
                    "method": "GET",
                    "path": "/api/projects/{project_id}/sequences/default",
                    "description": "Get the default sequence ID.",
                },
                "get": {
                    "method": "GET",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}",
                    "description": "Get sequence with full timeline data.",
                },
                "update": {
                    "method": "PUT",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}",
                    "description": "Save timeline data (requires lock + version match).",
                },
                "delete": {
                    "method": "DELETE",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}",
                    "description": "Delete a sequence (cannot delete default).",
                },
                "lock": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}/lock",
                    "description": "Acquire edit lock. Returns edit_token for X-Edit-Session header.",
                },
                "heartbeat": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}/heartbeat",
                    "description": "Keep lock alive (call every 30s). Lock expires after 2 min without heartbeat.",
                },
                "unlock": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}/unlock",
                    "description": "Release edit lock.",
                },
                "list_snapshots": {
                    "method": "GET",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}/snapshots",
                    "description": "List checkpoints (snapshots) for a sequence.",
                },
                "create_snapshot": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}/snapshots",
                    "description": "Create a checkpoint of current sequence state.",
                },
                "restore_snapshot": {
                    "method": "POST",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}/snapshots/{snapshot_id}/restore",
                    "description": "Restore sequence from a checkpoint (requires lock).",
                },
                "delete_snapshot": {
                    "method": "DELETE",
                    "path": "/api/projects/{project_id}/sequences/{sequence_id}/snapshots/{snapshot_id}",
                    "description": "Delete a checkpoint.",
                },
            },
            "sequence_targeting_guide": {
                "description": "Sequence Targeting Guide — How to target a specific sequence for render/preview operations.",
                "methods": {
                    "query_parameter": {
                        "description": "Pass ?sequence_id=<UUID> to render/preview endpoints for read-only access. No lock required. Works with: POST /render, POST /render/package, POST /preview/sample-frame, POST /preview/event-points, POST /preview/sample-event-points, POST /preview/validate.",
                        "example": "POST /api/projects/{project_id}/render?sequence_id={seq_id}",
                        "note": "sequence_id is ignored when X-Edit-Session header is also provided.",
                    },
                    "edit_session_header": {
                        "description": "Use X-Edit-Session header with edit_token from lock for read-write session access.",
                        "steps": [
                            "1. POST /api/projects/{project_id}/sequences/{seq_id}/lock → get edit_token",
                            "2. Set X-Edit-Session: {edit_token} header on render/preview requests",
                            "3. POST /api/projects/{project_id}/render with X-Edit-Session header",
                            "4. POST /api/projects/{project_id}/sequences/{seq_id}/unlock when done",
                        ],
                    },
                },
                "fallback_behavior": "When neither sequence_id nor X-Edit-Session is provided, the project's default sequence is used. If no default sequence exists, the project's legacy timeline_data is used.",
                "priority": "X-Edit-Session > sequence_id query param > default sequence > project timeline_data",
            },
        },
        "workflow_examples": {
            "add_title_text": {
                "description": "テロップ/タイトルテキストを追加",
                "steps": [
                    "POST /clips with layer_id=text-layer, start_ms, duration_ms, text_content",
                    "PATCH /clips/{id}/text-style with font_size, color, background_color, background_opacity",
                    "PATCH /clips/{id}/effects with fade_in_ms=200, fade_out_ms=200",
                ],
            },
            "add_video_with_audio": {
                "description": "動画を音声付きで配置",
                "steps": [
                    "POST /clips with layer_id, asset_id, start_ms, duration_ms (audio auto-placed)",
                    "Verify with GET /timeline-overview",
                ],
            },
            "improve_pacing": {
                "description": "ペーシングの改善",
                "steps": [
                    "GET /analysis/pacing to identify slow sections",
                    "GET /analysis/gaps to find empty spaces",
                    "Add section markers (text clips) at transition points",
                    "Add fade effects to smoothen transitions",
                ],
            },
        },
        "recommended_workflow": [
            "1. GET /api/ai/v1/capabilities?include=minimal — discover API (use ?include=all for full details)",
            "2. POST /api/ai/v1/projects — create a new project (or GET /projects to list existing)",
            "3. Upload assets via /api/projects/{id}/assets/upload-url + PUT + POST /api/projects/{id}/assets",
            "4. GET /api/ai/v1/projects/{id}/assets — list available assets (wait ~15s after upload for probing)",
            "5. GET /api/ai/v1/projects/{id}/timeline-overview — full timeline (add ?include_snapshot=true for visual snapshot)",
            "6. POST /api/projects/{id}/preview/sample-event-points — key frame images",
            "7. Use add_clip, move_clip, batch, semantic etc. to edit",
            "8. POST /api/projects/{id}/preview/validate — check composition",
            "9. POST /api/projects/{id}/render — export final video",
        ],
        "asset_layer_mapping": {
            "slide": {
                "recommended_layer": "content",
                "description": "Slide images go on the Content layer",
            },
            "avatar": {
                "recommended_layer": "avatar",
                "description": "Avatar videos go on the Avatar layer (supports chroma key)",
            },
            "background": {
                "recommended_layer": "background",
                "description": "Background images/videos go on the Background layer",
            },
            "screen_recording": {
                "recommended_layer": "content",
                "description": "Screen recordings go on the Content layer",
            },
            "other": {
                "recommended_layer": "content",
                "description": "General assets default to the Content layer",
            },
        },
        "duration_tip": (
            "All assets have duration_ms populated: video/audio assets are probed server-side after upload; "
            "image assets default to 5000ms (matching suggested_display_duration_ms). "
            "If video/audio duration_ms is null, wait ~15 seconds and re-fetch GET /assets. "
            "Use the duration_ms value directly when creating clips. "
            "Image width/height: uploaded via batch-upload are probed synchronously and available immediately. "
            "Images uploaded via the 3-step signed-URL flow are probed asynchronously (background task, "
            "3-10 seconds). If image width/height is null after 15 seconds, re-fetch GET /assets — "
            "a lazy re-probe is triggered automatically on GET /assets and GET /assets/{id}."
        ),
        "metadata_probing": (
            "All uploaded assets are automatically probed server-side: "
            "video/audio -> duration_ms, width, height, sample_rate, channels; "
            "image -> width, height (synchronous for batch-upload, async background task for signed-URL flow). "
            "Auto-extracted audio from video also gets duration. "
            "If image width/height is null, a lazy re-probe is triggered automatically on the next "
            "GET /assets or GET /assets/{id} call. "
            "Probing takes 3-10 seconds after upload."
        ),
        "asset_upload_guide": {
            "description": "3-step process to upload and register an asset.",
            "steps": [
                {
                    "step": 1,
                    "action": "Get signed upload URL",
                    "method": "POST",
                    "path": "/api/projects/{project_id}/assets/upload-url?filename={url_encoded_filename}&content_type={mime_type}",
                    "response_fields": {
                        "upload_url": "Signed URL to PUT the file to",
                        "storage_key": "SAVE THIS — needed for step 3 registration",
                        "expires_at": "URL expiration time",
                    },
                },
                {
                    "step": 2,
                    "action": "Upload file binary to the signed URL",
                    "method": "PUT",
                    "url": "The upload_url from step 1",
                    "headers": {"Content-Type": "the same mime_type used in step 1"},
                    "body": "Raw file bytes (binary upload, NOT multipart form)",
                },
                {
                    "step": 3,
                    "action": "Register asset metadata in the database",
                    "method": "POST",
                    "path": "/api/projects/{project_id}/assets",
                    "body_fields": {
                        "name": "(string, REQUIRED) Display name for the asset",
                        "type": "(string, REQUIRED) One of: 'video', 'audio', 'image'",
                        "subtype": "(string, REQUIRED) One of: 'avatar', 'background', 'slide', 'narration', 'bgm', 'se', 'effect', 'other'. NOTE: field is 'subtype' (NOT 'sub_type')",
                        "storage_key": "(string, REQUIRED) The storage_key value from step 1 response. NOTE: field is 'storage_key' (NOT 'blob_name')",
                        "storage_url": "(string, REQUIRED) Use the same value as storage_key — server resolves it",
                        "file_size": "(int, REQUIRED) File size in bytes",
                        "mime_type": "(string, REQUIRED) MIME type (e.g., 'video/mp4', 'image/png', 'audio/mpeg')",
                    },
                    "example_body": {
                        "name": "intro_avatar.mp4",
                        "type": "video",
                        "subtype": "avatar",
                        "storage_key": "projects/abc123/assets/def456.mp4",
                        "storage_url": "projects/abc123/assets/def456.mp4",
                        "file_size": 4642385,
                        "mime_type": "video/mp4",
                    },
                    "common_mistakes": [
                        "Using 'blob_name' instead of 'storage_key' — the field is 'storage_key'",
                        "Using 'sub_type' instead of 'subtype' — the field is 'subtype' (no underscore)",
                        "Forgetting to wait 15s after registration for server-side probing to complete",
                        "Assuming image width/height is immediately available via signed-URL upload — it is probed "
                        "asynchronously (3-10s). Re-fetch GET /assets after 15s if width/height is null.",
                    ],
                },
            ],
        },
        "idempotency": {
            "description": (
                "IMPORTANT: All write/mutation requests (POST, PATCH, DELETE, PUT that modify data) "
                "REQUIRE an Idempotency-Key header. Requests without this header will be REJECTED "
                "with a 400 IDEMPOTENCY_MISSING error. This is not optional."
            ),
            "header": "Idempotency-Key",
            "format": "UUID v4 string",
            "example": "550e8400-e29b-41d4-a716-446655440000",
            "behavior": "If the same key is sent twice, the second request returns the cached result.",
            "when_required": "Every POST/PATCH/DELETE/PUT that modifies project data (clips, layers, audio, batch, semantic, markers, etc.)",
            "how_to_generate": "Use any UUID v4 generator. Each distinct operation needs a unique key.",
        },
        "request_formats": {
            "note": "All mutation endpoints recommend an 'options' field (defaults to empty {} if omitted). Write endpoints should include an 'Idempotency-Key' header for safe retries.",
            "common_headers": {
                "X-API-Key": "Required. Your API key.",
                "Idempotency-Key": "Recommended for all write operations. UUID string to prevent duplicate operations. If omitted, the operation is not idempotent.",
                "Content-Type": "application/json",
                "If-Match": "Optional. ETag value for optimistic concurrency control.",
            },
            "endpoints": {
                "POST /clips": {
                    "body": {
                        "clip": {
                            "asset_id": "uuid",
                            "layer_id": "uuid",
                            "start_ms": 0,
                            "duration_ms": 5000,
                        },
                        "options": {},
                    },
                    "notes": "For text clips, use 'text_content' (not 'text') and omit asset_id. Flat body (without 'clip' wrapper) is auto-wrapped.",
                    "text_clip_example": {
                        "clip": {
                            "layer_id": "uuid",
                            "start_ms": 0,
                            "duration_ms": 5000,
                            "text_content": "Your text here",
                        },
                        "options": {},
                    },
                },
                "PATCH /clips/{id}/move": {
                    "body": {"move": {"new_start_ms": 5000}, "options": {}},
                },
                "PATCH /clips/{id}/timing": {
                    "body": {
                        "timing": {"duration_ms": 5000, "in_point_ms": 0, "out_point_ms": 5000},
                        "options": {},
                    },
                    "notes": "Cannot change start_ms here. Use /move endpoint instead.",
                },
                "PATCH /clips/{id}/effects": {
                    "body": {
                        "effects": {"opacity": 0.8, "fade_in_ms": 500, "fade_out_ms": 500},
                        "options": {},
                    },
                    "chroma_key_example": {
                        "body": {
                            "effects": {
                                "chroma_key_enabled": True,
                                "chroma_key_color": "#00FF00",
                                "chroma_key_similarity": 0.3,
                                "chroma_key_blend": 0.1,
                            },
                            "options": {},
                        },
                    },
                    "common_mistakes": [
                        'Using nested format {"chroma_key": {"enabled": true, "color": "#00FF00"}} -- this is SILENTLY IGNORED. '
                        'Use FLAT fields: {"chroma_key_enabled": true, "chroma_key_color": "#00FF00", ...}',
                    ],
                },
                "PATCH /clips/{id}/transform": {
                    "body": {
                        "transform": {"x": 0, "y": 0, "scale": 1.0, "rotation": 0},
                        "options": {},
                    },
                    "notes": "Coordinate system: (0,0) = canvas center. Positive x = right, positive y = down. "
                    "Supported fields: x, y, scale, width, height, rotation, anchor. "
                    "scale_x/scale_y are NOT valid — use 'scale' (uniform) or 'width'/'height' for sizing.",
                },
                "PATCH /clips/{id}/text": {
                    "body": {"text": {"text_content": "Hello"}, "options": {}},
                },
                "PATCH /clips/{id}/text-style": {
                    "body": {
                        "text_style": {
                            "font_size": 48,
                            "font_family": "Noto Sans JP",
                            "color": "#FFFFFF",
                        },
                        "options": {},
                    },
                },
                "DELETE /clips/{id}": {
                    "body": {"options": {}},
                },
                "POST /clips/{id}/split": {
                    "body": {"split_at_ms": 5000, "options": {}},
                },
                "POST /clips/{id}/unlink": {
                    "body": {"options": {}},
                },
                "POST /audio-clips": {
                    "body": {
                        "clip": {
                            "asset_id": "uuid",
                            "track_id": "uuid",
                            "start_ms": 0,
                            "duration_ms": 5000,
                        },
                        "options": {},
                    },
                },
                "PATCH /audio-clips/{id}/move": {
                    "body": {"new_start_ms": 5000, "options": {}},
                    "notes": "Audio move uses flat format (not nested in 'move' key).",
                },
                "POST /layers": {
                    "body": {"layer": {"name": "My Layer", "type": "content"}, "options": {}},
                },
                "POST /audio-tracks": {
                    "body": {"track": {"name": "BGM", "type": "bgm"}, "options": {}},
                },
                "POST /markers": {
                    "body": {
                        "marker": {"name": "Section Start", "time_ms": 5000, "color": "#FF0000"},
                        "options": {},
                    },
                    "notes": "Use 'name' field (not 'label').",
                },
                "POST /semantic": {
                    "body": {
                        "semantic": {
                            "operation": "close_all_gaps",
                            "target_layer_id": "uuid",
                            "parameters": {},
                        },
                        "options": {},
                    },
                    "notes": "Recommended key is 'semantic'. Legacy key 'operation' is also accepted for backward compatibility.",
                },
                "POST /batch": {
                    "body": {
                        "operations": [
                            {
                                "operation": "move",
                                "clip_id": "uuid",
                                "move": {"new_start_ms": 5000},
                            },
                            {
                                "operation": "update_effects",
                                "clip_id": "uuid",
                                "effects": {"opacity": 0.5},
                            },
                            {
                                "operation": "update_text_style",
                                "clip_id": "uuid",
                                "text_style": {"font_size": 48, "color": "#FFFFFF"},
                            },
                            {
                                "operation": "update_text",
                                "clip_id": "uuid",
                                "text": {"text_content": "Updated telop"},
                            },
                            {
                                "operation": "split",
                                "clip_id": "uuid",
                                "data": {
                                    "split_at_ms": 5000,
                                    "left_text_content": "前半テキスト",
                                    "right_text_content": "後半テキスト",
                                },
                            },
                        ],
                        "options": {"validate_only": False, "rollback_on_failure": False},
                    },
                    "notes": "Operation parameters can use endpoint-specific keys (effects, timing, transform, "
                    "text_style, move, text, clip) or the generic 'data' key. "
                    "Endpoint-specific keys are recommended as they match the direct API endpoints. "
                    "clip_id stays at top level.",
                },
                "POST /preview-diff": {
                    "body": {
                        "operation_type": "move",
                        "clip_id": "uuid-prefix",
                        "parameters": {"new_start_ms": 5000},
                    },
                    "notes": "Simulates an operation and returns before/after diff without modifying timeline. "
                    "Supported operation_types: move, trim, delete, close_all_gaps, distribute_evenly, add_text_with_timing.",
                },
            },
        },
    }

    # Promote semantic details to top level for AI discoverability
    capabilities["semantic_operations"] = capabilities["schema_notes"]["semantic_operations"]

    if include == "overview":
        # Lightweight mode: reduce semantic_operations to name list,
        # replace request_formats with compact body skeletons
        capabilities["semantic_operations"] = [
            op["operation"] for op in capabilities["schema_notes"]["semantic_operations"]
        ]
        capabilities["schema_notes"]["semantic_operations"] = capabilities["semantic_operations"]
        # Replace verbose request_formats with compact body skeletons
        capabilities.pop("request_formats", None)
        capabilities["request_formats_compact"] = {
            "IMPORTANT": "All write requests REQUIRE 'Idempotency-Key: <uuid>' header. Omitting causes 400 error.",
            "note": "Body skeletons for each mutation endpoint. "
            "All bodies optionally accept an 'options' field (defaults to {}). Use ?include=all for full details.",
            "POST /clips": {
                "clip": {
                    "layer_id": "uuid",
                    "asset_id": "uuid",
                    "start_ms": 0,
                    "duration_ms": 1000,
                },
                "options": {},
            },
            "PATCH /clips/{id}/move": {"move": {"new_start_ms": 0}, "options": {}},
            "PATCH /clips/{id}/timing": {
                "timing": {"duration_ms": 5000, "in_point_ms": 0, "out_point_ms": 5000},
                "options": {},
            },
            "PATCH /clips/{id}/effects": {
                "effects": {"opacity": 1.0, "fade_in_ms": 0, "fade_out_ms": 0},
                "options": {},
                "_chroma_key_note": "For chroma key, use FLAT fields in effects: chroma_key_enabled, chroma_key_color, chroma_key_similarity, chroma_key_blend. Do NOT use nested {chroma_key: {enabled: ...}} format.",
            },
            "PATCH /clips/{id}/transform": {
                "transform": {"x": 0, "y": 0, "scale": 1.0},
                "options": {},
            },
            "PATCH /clips/{id}/text": {"text": {"text_content": "Hello"}, "options": {}},
            "PATCH /clips/{id}/text-style": {
                "text_style": {"font_size": 48, "font_family": "Noto Sans JP", "color": "#FFFFFF"},
                "options": {},
            },
            "DELETE /clips/{id}": {"options": {}},
            "POST /clips/{id}/split": {"split_at_ms": 5000, "options": {}},
            "POST /audio-clips": {
                "clip": {
                    "asset_id": "uuid",
                    "track_id": "uuid",
                    "start_ms": 0,
                    "duration_ms": 5000,
                },
                "options": {},
            },
            "PATCH /audio-clips/{id}": {
                "audio": {"volume": 0.3, "fade_in_ms": 500, "fade_out_ms": 1000},
                "options": {},
            },
            "POST /layers": {"layer": {"name": "My Layer", "type": "content"}, "options": {}},
            "POST /audio-tracks": {"track": {"name": "BGM", "type": "bgm"}, "options": {}},
            "POST /markers": {
                "marker": {"name": "Section Start", "time_ms": 5000, "color": "#FF0000"},
                "options": {},
            },
            "POST /semantic (snap_to_previous)": {
                "semantic": {"operation": "snap_to_previous", "target_clip_id": "<clip-id>"}
            },
            "POST /semantic (snap_to_next)": {
                "semantic": {"operation": "snap_to_next", "target_clip_id": "<clip-id>"}
            },
            "POST /semantic (close_gap)": {
                "semantic": {"operation": "close_gap", "target_layer_id": "<layer-id>"}
            },
            "POST /semantic (close_all_gaps)": {
                "semantic": {"operation": "close_all_gaps", "target_layer_id": "<layer-id>"}
            },
            "POST /semantic (add_text_with_timing)": {
                "semantic": {
                    "operation": "add_text_with_timing",
                    "target_clip_id": "<clip-id>",
                    "parameters": {"text_content": "Your text here"},
                }
            },
            "POST /semantic (rename_layer)": {
                "semantic": {
                    "operation": "rename_layer",
                    "target_layer_id": "<layer-id>",
                    "parameters": {"name": "New Layer Name"},
                }
            },
            "POST /semantic (distribute_evenly)": {
                "semantic": {"operation": "distribute_evenly", "target_layer_id": "<layer-id>"}
            },
            "POST /semantic (replace_clip)": {
                "semantic": {
                    "operation": "replace_clip",
                    "target_clip_id": "<clip-id>",
                    "parameters": {"new_asset_id": "<asset-id>"},
                }
            },
            "POST /batch": {
                "operations": [
                    {
                        "operation": "update_effects",
                        "clip_id": "uuid",
                        "effects": {"fade_in_ms": 500},
                    },
                    {"operation": "move", "clip_id": "uuid", "move": {"new_start_ms": 5000}},
                    {
                        "operation": "update_text_style",
                        "clip_id": "uuid",
                        "text_style": {"font_size": 48, "color": "#FFFFFF"},
                    },
                    {
                        "operation": "update_text",
                        "clip_id": "uuid",
                        "text": {"text_content": "Updated telop"},
                    },
                    {
                        "operation": "split",
                        "clip_id": "uuid",
                        "data": {
                            "split_at_ms": 5000,
                            "left_text_content": "前半テキスト",
                            "right_text_content": "後半テキスト",
                        },
                    },
                ],
                "options": {},
            },
            "POST /preview-diff": {
                "operation_type": "move",
                "clip_id": "uuid",
                "parameters": {"new_start_ms": 5000},
            },
        }
        # Trim preview_api endpoint details to just method+path+description
        if "preview_api" in capabilities and "endpoints" in capabilities["preview_api"]:
            for _ep_key, ep_val in capabilities["preview_api"]["endpoints"].items():
                for verbose_key in ("request_body", "response", "event_types"):
                    ep_val.pop(verbose_key, None)
        # Trim ai_video_api endpoint details
        if "ai_video_api" in capabilities and "endpoints" in capabilities["ai_video_api"]:
            for _ep_key, ep_val in capabilities["ai_video_api"]["endpoints"].items():
                for verbose_key in ("request_body", "response"):
                    ep_val.pop(verbose_key, None)
        # Trim workflow_examples to just descriptions
        if "workflow_examples" in capabilities:
            capabilities["workflow_examples"] = {
                k: v.get("description", k) for k, v in capabilities["workflow_examples"].items()
            }
        context.warnings.append(
            "Overview mode: request_formats replaced with compact body skeletons. "
            "Use ?include=all for full details."
        )

    elif include == "minimal":
        # Ultra-compact mode (<5KB target): only what an agent needs to get started.
        # Endpoints as compact strings, semantic ops as name-only list,
        # workflow as 3-line summary, limits trimmed to essentials.
        read_endpoints = [
            "GET /capabilities",
            "GET /version",
            "GET /projects",
            "POST /projects",  # Create a new project
            "GET /projects/{id}/overview",
            "GET /projects/{id}/structure",
            "GET /projects/{id}/timeline-overview",
            "GET /projects/{id}/assets",
            "GET /projects/{id}/clips/{cid}",
            "GET /projects/{id}/audio-clips/{cid}",
            "GET /projects/{id}/at-time/{ms}",
            "GET /projects/{id}/analysis/gaps",
            "GET /projects/{id}/analysis/pacing",
            "GET /projects/{id}/history",
            "GET /schemas",
        ]
        write_endpoints = [
            "POST /projects/{id}/clips",
            "PATCH /projects/{id}/clips/{cid}/move",
            "PATCH /projects/{id}/clips/{cid}/transform",
            "PATCH /projects/{id}/clips/{cid}/effects",
            "PATCH /projects/{id}/clips/{cid}/timing",
            "PATCH /projects/{id}/clips/{cid}/text",
            "PATCH /projects/{id}/clips/{cid}/text-style",
            "PATCH /projects/{id}/clips/{cid}/crop",
            "DELETE /projects/{id}/clips/{cid}",
            "POST /projects/{id}/clips/{cid}/split",
            "POST /projects/{id}/layers",
            "PATCH /projects/{id}/layers/{lid}",
            "PUT /projects/{id}/layers/order",
            "POST /projects/{id}/audio-clips",
            "PATCH /projects/{id}/audio-clips/{cid}/move",
            "PATCH /projects/{id}/audio-clips/{cid}",
            "DELETE /projects/{id}/audio-clips/{cid}",
            "POST /projects/{id}/audio-tracks",
            "POST /projects/{id}/markers",
            "POST /projects/{id}/batch",
            "POST /projects/{id}/semantic",
            "POST /projects/{id}/preview-diff",
        ]
        # Name-only list (use ?include=all for descriptions)
        semantic_ops = [
            op["operation"] for op in capabilities["schema_notes"]["semantic_operations"]
        ]

        capabilities = {
            "api_version": capabilities["api_version"],
            "schema_version": capabilities["schema_version"],
            "auth": {"header": "X-API-Key or Authorization: Bearer <token>"},
            "CRITICAL_HEADERS": {
                "Idempotency-Key": "REQUIRED on ALL write requests (UUID v4). Omitting causes 400 IDEMPOTENCY_MISSING error.",
            },
            "endpoints": {
                "read": read_endpoints,
                "write": write_endpoints,
            },
            "semantic_operations": semantic_ops,
            "workflow": "1) GET /capabilities?include=minimal 2) POST /projects to create or GET /projects to list 3) Upload assets → GET /assets (wait 15s for probing) 4) GET /timeline-overview 5) Edit via clips/semantic/batch endpoints",
            "limits": {
                "max_layers": 5,
                "max_clips_per_layer": 100,
                "max_batch_ops": 20,
            },
            "note": "Use ?include=all for full details.",
        }
        context.warnings.append(
            "Minimal mode: most details omitted. "
            "Use ?include=all or ?include=overview for more details."
        )

    # Version-based ETag for semi-static capabilities (changes only on deploy)
    from src.config import get_settings as _get_settings

    _settings = _get_settings()
    response.headers["ETag"] = f'W/"capabilities:{_settings.app_version}:{include}"'

    return envelope_success(context, capabilities)


@router.get("/version", response_model=EnvelopeResponse)
async def get_version(
    current_user: CurrentUser,
) -> EnvelopeResponse:
    context = create_request_context()
    logger.info("v1.get_version")
    data = {
        "api_version": "1.0",
        "schema_version": "1.0-unified",  # Must match /capabilities
    }
    return envelope_success(context, data)


@router.get(
    "/schemas",
    response_model=EnvelopeResponse,
    summary="Get available schema definitions",
    description=(
        "Returns a list of all available AI API schemas with their descriptions and endpoints. "
        "Use ?detail=full to include full JSON Schema field definitions for each schema."
    ),
)
async def get_schemas(
    current_user: CurrentUser,
    response: Response,
    detail: str = "summary",
) -> EnvelopeResponse:
    """Get available schema definitions.

    Returns information about all schema levels (L1, L2, L2.5, L3)
    and write/analysis schemas.

    Query params:
        detail: "summary" (default) returns names and descriptions only.
                "full" includes json_schema with field definitions for each schema.
    """
    context = create_request_context()
    logger.info("v1.get_schemas detail=%s", detail)

    # Each entry: (name, description, level, token_estimate, endpoint, model_class_or_None)
    _schema_entries: list[dict[str, Any]] = [
        {
            "name": "L1ProjectOverview",
            "description": "Lightweight project overview with summary statistics",
            "level": "L1",
            "token_estimate": "~300 tokens",
            "endpoint": "GET /projects/{project_id}/overview",
            "model": L1ProjectOverview,
        },
        {
            "name": "L2TimelineStructure",
            "description": "Timeline layer/track structure without clip details",
            "level": "L2",
            "token_estimate": "~800 tokens",
            "endpoint": "GET /projects/{project_id}/structure",
            "model": L2TimelineStructure,
        },
        {
            "name": "L2AssetCatalog",
            "description": "Available assets with usage counts",
            "level": "L2",
            "token_estimate": "~500 tokens",
            "endpoint": "GET /projects/{project_id}/assets",
            "model": L2AssetCatalog,
        },
        {
            "name": "L2TimelineAtTime",
            "description": "Active clips at a specific timestamp",
            "level": "L2",
            "token_estimate": "~400 tokens",
            "endpoint": "GET /projects/{project_id}/at-time/{time_ms}",
            "model": L2TimelineAtTime,
        },
        {
            "name": "L25TimelineOverview",
            "description": "Full timeline overview with clip summaries, gaps, and overlaps",
            "level": "L2",
            "token_estimate": "~2000 tokens",
            "endpoint": "GET /projects/{project_id}/timeline-overview",
            "model": L25TimelineOverview,
        },
        {
            "name": "L3ClipDetails",
            "description": "Full details for a single video clip with neighbors",
            "level": "L3",
            "token_estimate": "~400 tokens/clip",
            "endpoint": "GET /projects/{project_id}/clips/{clip_id}",
            "model": L3ClipDetails,
        },
        {
            "name": "L3AudioClipDetails",
            "description": "Full details for a single audio clip with neighbors",
            "level": "L3",
            "token_estimate": "~300 tokens/clip",
            "endpoint": "GET /projects/{project_id}/audio-clips/{clip_id}",
            "model": L3AudioClipDetails,
        },
        {
            "name": "AddClipRequest",
            "description": "Add a new video clip to a layer",
            "level": "write",
            "token_estimate": "~200 tokens",
            "endpoint": "POST /projects/{project_id}/clips",
            "model": AddClipRequest,
            "example_body": {
                "clip": {
                    "asset_id": "uuid-here",
                    "layer_id": "uuid-here",
                    "start_ms": 0,
                    "duration_ms": 5000,
                },
            },
        },
        {
            "name": "MoveClipRequest",
            "description": "Move a clip to a different layer or position",
            "level": "write",
            "token_estimate": "~150 tokens",
            "endpoint": "PATCH /projects/{project_id}/clips/{clip_id}/move",
            "model": MoveClipRequest,
            "example_body": {
                "move": {
                    "new_start_ms": 5000,
                },
            },
        },
        {
            "name": "UpdateClipTimingRequest",
            "description": "Update clip timing (duration, speed, in/out points)",
            "level": "write",
            "token_estimate": "~150 tokens",
            "endpoint": "PATCH /projects/{project_id}/clips/{clip_id}/timing",
            "model": UpdateClipTimingRequest,
            "example_body": {
                "timing": {
                    "duration_ms": 5000,
                    "in_point_ms": 0,
                    "out_point_ms": 5000,
                },
            },
        },
        {
            "name": "UpdateClipTransformRequest",
            "description": "Update clip transform (position, scale, rotation, opacity)",
            "level": "write",
            "token_estimate": "~150 tokens",
            "endpoint": "PATCH /projects/{project_id}/clips/{clip_id}/transform",
            "model": UpdateClipTransformRequest,
            "example_body": {
                "transform": {
                    "x": 0,
                    "y": 0,
                    "scale": 1.0,
                },
            },
        },
        {
            "name": "UpdateClipEffectsRequest",
            "description": "Update clip visual effects (filters, color correction, etc.)",
            "level": "write",
            "token_estimate": "~200 tokens",
            "endpoint": "PATCH /projects/{project_id}/clips/{clip_id}/effects",
            "model": UpdateClipEffectsRequest,
            "example_body": {
                "effects": {
                    "opacity": 1.0,
                    "fade_in_ms": 500,
                    "fade_out_ms": 500,
                },
            },
        },
        {
            "name": "UpdateClipTextRequest",
            "description": "Update text content for text clips",
            "level": "write",
            "token_estimate": "~100 tokens",
            "endpoint": "PATCH /projects/{project_id}/clips/{clip_id}/text",
            "model": UpdateClipTextRequest,
            "example_body": {
                "text": {
                    "text_content": "Hello World",
                },
            },
        },
        {
            "name": "UpdateClipTextStyleRequest",
            "description": "Update text style (font, size, color, alignment, etc.)",
            "level": "write",
            "token_estimate": "~200 tokens",
            "endpoint": "PATCH /projects/{project_id}/clips/{clip_id}/text-style",
            "model": UpdateClipTextStyleRequest,
            "example_body": {
                "text_style": {
                    "font_size": 48,
                    "font_family": "Noto Sans JP",
                    "color": "#FFFFFF",
                    "background_color": "#000000",
                    "background_opacity": 0.5,
                },
            },
        },
        {
            "name": "AddAudioClipRequest",
            "description": "Add a new audio clip to a track",
            "level": "write",
            "token_estimate": "~200 tokens",
            "endpoint": "POST /projects/{project_id}/audio-clips",
            "model": AddAudioClipRequest,
            "example_body": {
                "clip": {
                    "asset_id": "uuid-here",
                    "track_id": "uuid-here",
                    "start_ms": 0,
                    "duration_ms": 5000,
                },
            },
        },
        {
            "name": "SemanticOperation",
            "description": "High-level semantic operations (snap, close gap, auto duck, etc.)",
            "level": "write",
            "token_estimate": "~150 tokens",
            "endpoint": "POST /projects/{project_id}/semantic",
            "model": SemanticOperation,
            "example_body": {
                "semantic": {
                    "operation": "close_all_gaps",
                    "target_layer_id": "uuid-here",
                },
            },
        },
        {
            "name": "BatchClipOperation",
            "description": "Batch multiple clip operations in a single request",
            "level": "write",
            "token_estimate": "~300 tokens",
            "endpoint": "POST /projects/{project_id}/batch",
            "model": BatchClipOperation,
            "example_body": {
                "operations": [
                    {"operation": "move", "clip_id": "uuid-here", "move": {"new_start_ms": 5000}},
                    {
                        "operation": "update_effects",
                        "clip_id": "uuid-here",
                        "effects": {"fade_in_ms": 500},
                    },
                    {
                        "operation": "update_text_style",
                        "clip_id": "uuid-here",
                        "text_style": {"font_size": 48, "color": "#FFFFFF"},
                    },
                    {
                        "operation": "update_text",
                        "clip_id": "uuid-here",
                        "text": {"text_content": "Updated telop"},
                    },
                    {
                        "operation": "split",
                        "clip_id": "uuid-here",
                        "data": {
                            "split_at_ms": 5000,
                            "left_text_content": "前半テキスト",
                            "right_text_content": "後半テキスト",
                        },
                    },
                ],
            },
        },
        {
            "name": "OperationOptions",
            "description": "Common options for write operations (dry_run, skip_validation, etc.)",
            "level": "write",
            "token_estimate": "~100 tokens",
            "endpoint": "(included in request body of write endpoints)",
            "model": OperationOptions,
        },
        {
            "name": "GapAnalysisResult",
            "description": "Find gaps in the timeline across layers and tracks",
            "level": "analysis",
            "token_estimate": "~500 tokens",
            "endpoint": "GET /projects/{project_id}/analysis/gaps",
            "model": GapAnalysisResult,
        },
        {
            "name": "PacingAnalysisResult",
            "description": "Analyze clip density and pacing across timeline segments",
            "level": "analysis",
            "token_estimate": "~600 tokens",
            "endpoint": "GET /projects/{project_id}/analysis/pacing",
            "model": PacingAnalysisResult,
        },
    ]

    # Version-based ETag for semi-static schemas (changes only on deploy)
    from src.config import get_settings as _get_settings

    _settings = _get_settings()
    response.headers["ETag"] = f'W/"schemas:{_settings.app_version}:{detail}"'

    if detail == "full":
        # Return full JSON Schema field definitions for each schema
        full_schemas: dict[str, dict[str, Any]] = {}
        for entry in _schema_entries:
            schema_dict: dict[str, Any] = {
                "description": entry["description"],
                "level": entry["level"],
                "token_estimate": entry["token_estimate"],
                "endpoint": entry["endpoint"],
                "json_schema": entry["model"].model_json_schema(),
            }
            if "example_body" in entry:
                schema_dict["example_body"] = entry["example_body"]
            full_schemas[entry["name"]] = schema_dict
        return envelope_success(context, {"schemas": full_schemas})

    # Default: summary mode (backward-compatible list format)
    summary_list: list[dict[str, Any]] = []
    for entry in _schema_entries:
        item: dict[str, Any] = {
            "name": entry["name"],
            "description": entry["description"],
            "level": entry["level"],
            "token_estimate": entry["token_estimate"],
            "endpoint": entry["endpoint"],
        }
        if "example_body" in entry:
            item["example_body"] = entry["example_body"]
        summary_list.append(item)

    return envelope_success(context, {"schemas": summary_list})


@catch_all_router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def v1_catch_all(path: str, request: Request) -> JSONResponse:
    """Return 405 if the path exists but method is wrong, else 404."""
    context = create_request_context()
    # Import the package-level router (all sub-routers combined) lazily to avoid
    # circular imports at module load time.
    from src.api.ai_v1 import router as _package_router

    allowed_methods = _find_allowed_methods(_package_router, path)
    if allowed_methods:
        allow_header = ", ".join(sorted(allowed_methods))
        return JSONResponse(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            content=jsonable_encoder(
                EnvelopeResponse(
                    request_id=context.request_id,
                    error=ErrorInfo(
                        code="METHOD_NOT_ALLOWED",
                        message=(
                            f"Method {request.method} is not allowed for '/{path}'. "
                            f"Allowed methods: {allow_header}. "
                            "Use GET /capabilities for available endpoints."
                        ),
                        retryable=False,
                        suggested_fix=f"Use one of the allowed methods: {allow_header}",
                    ),
                    meta=build_meta(context),
                ).model_dump(exclude_none=True)
            ),
            headers={"Allow": allow_header},
        )
    return envelope_error(
        context,
        code="NOT_FOUND",
        message=f"V1 endpoint '/{path}' does not exist. Use GET /capabilities for available endpoints.",
        status_code=status.HTTP_404_NOT_FOUND,
    )
