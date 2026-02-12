"""Expected request body formats for V1 API endpoints.

Used by the validation exception handler to include `expected_format` in 422
error responses so that API consumers (especially AI agents) can self-correct
without re-fetching /capabilities.

Each key is a regex-style path pattern that matches one or more V1 PATCH/POST
endpoints.  The value is an example request body showing the correct wrapper
structure and representative field values.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Expected format examples keyed by path pattern
# ---------------------------------------------------------------------------
# Patterns use {param} placeholders which are converted to regex groups.

_EXPECTED_FORMATS: list[tuple[str, dict[str, Any]]] = [
    # -- Clip PATCH endpoints -------------------------------------------------
    (
        "/api/ai/v1/projects/{project_id}/clips/{clip_id}/timing",
        {
            "timing": {
                "duration_ms": 5000,
                "in_point_ms": 0,
                "out_point_ms": 5000,
            },
            "options": {},
        },
    ),
    (
        "/api/ai/v1/projects/{project_id}/clips/{clip_id}/text-style",
        {
            "text_style": {
                "font_size": 48,
                "font_family": "Noto Sans JP",
                "color": "#FFFFFF",
            },
            "options": {},
        },
    ),
    (
        "/api/ai/v1/projects/{project_id}/clips/{clip_id}/effects",
        {
            "effects": {
                "opacity": 0.8,
                "fade_in_ms": 300,
                "fade_out_ms": 300,
            },
            "options": {},
        },
    ),
    (
        "/api/ai/v1/projects/{project_id}/clips/{clip_id}/crop",
        {
            "crop": {
                "top": 0.0,
                "right": 0.0,
                "bottom": 0.1,
                "left": 0.0,
            },
            "options": {},
        },
    ),
    (
        "/api/ai/v1/projects/{project_id}/clips/{clip_id}/text",
        {
            "text": {
                "text_content": "Your text here",
            },
            "options": {},
        },
    ),
    (
        "/api/ai/v1/projects/{project_id}/clips/{clip_id}/shape",
        {
            "shape": {
                "filled": True,
                "fill_color": "#FF0000",
                "stroke_color": "#000000",
                "stroke_width": 2,
                "width": 200,
                "height": 100,
            },
            "options": {},
        },
    ),
    (
        "/api/ai/v1/projects/{project_id}/clips/{clip_id}/transform",
        {
            "transform": {
                "x": 100,
                "y": 200,
                "scale": 1.0,
                "rotation": 0,
            },
            "options": {},
        },
    ),
    (
        "/api/ai/v1/projects/{project_id}/clips/{clip_id}/move",
        {
            "move": {
                "new_start_ms": 5000,
                "new_layer_id": "layer-id",
            },
            "options": {},
        },
    ),
    # -- Clip creation --------------------------------------------------------
    # NOTE: POST /clips also accepts flat body (without "clip" wrapper).
    # The API auto-wraps flat bodies into {"clip": {...}, "options": {}}.
    (
        "/api/ai/v1/projects/{project_id}/clips",
        {
            "_examples": [
                {
                    "_description": "Video/image clip (with asset)",
                    "clip": {
                        "type": "video",
                        "layer_id": "layer-id",
                        "asset_id": "asset-id",
                        "start_ms": 0,
                        "duration_ms": 5000,
                    },
                    "options": {},
                },
                {
                    "_description": "Text clip (use 'text_content', not 'text')",
                    "clip": {
                        "layer_id": "text-layer-id",
                        "start_ms": 0,
                        "duration_ms": 5000,
                        "text_content": "Your text here",
                    },
                    "options": {},
                },
            ],
            "clip": {
                "type": "video",
                "layer_id": "layer-id",
                "asset_id": "asset-id",
                "start_ms": 0,
                "duration_ms": 5000,
            },
            "options": {},
        },
    ),
    # -- Audio clip endpoints -------------------------------------------------
    (
        "/api/ai/v1/projects/{project_id}/audio-clips/{clip_id}",
        {
            "audio": {
                "volume": 1.0,
                "fade_in_ms": 200,
                "fade_out_ms": 200,
            },
            "options": {},
        },
    ),
    # -- Layer endpoints ------------------------------------------------------
    (
        "/api/ai/v1/projects/{project_id}/layers",
        {
            "layer": {
                "name": "New Layer",
                "type": "video",
            },
            "options": {},
        },
    ),
    (
        "/api/ai/v1/projects/{project_id}/layers/{layer_id}",
        {
            "layer": {
                "name": "Updated Layer Name",
                "visible": True,
                "locked": False,
            },
            "options": {},
        },
    ),
    # -- Marker endpoints -----------------------------------------------------
    (
        "/api/ai/v1/projects/{project_id}/markers",
        {
            "marker": {
                "time_ms": 5000,
                "name": "Section Start",
                "color": "#FF0000",
            },
            "options": {},
        },
    ),
    (
        "/api/ai/v1/projects/{project_id}/markers/{marker_id}",
        {
            "marker": {
                "name": "Updated Marker",
                "time_ms": 10000,
                "color": "#00FF00",
            },
            "options": {},
        },
    ),
    # -- Keyframe endpoint ----------------------------------------------------
    (
        "/api/ai/v1/projects/{project_id}/clips/{clip_id}/keyframes",
        {
            "keyframe": {
                "property": "opacity",
                "time_ms": 0,
                "value": 1.0,
                "easing": "linear",
            },
            "options": {},
        },
    ),
]

# Pre-compile patterns to regex for efficient matching.
# {param} -> [^/]+  (matches a single path segment)
_COMPILED_FORMATS: list[tuple[re.Pattern[str], dict[str, Any]]] = []

for _pattern, _fmt in _EXPECTED_FORMATS:
    # Escape the pattern properly, then replace {param} with a regex group
    regex_str = re.escape(_pattern).replace(r"\{", "{").replace(r"\}", "}")
    regex_str = re.sub(r"\{[^}]+\}", r"[^/]+", regex_str)
    # Anchor to exact match
    _COMPILED_FORMATS.append((re.compile(f"^{regex_str}$"), _fmt))


def get_expected_format(path: str) -> dict[str, Any] | None:
    """Look up the expected request format for a given V1 API path.

    Args:
        path: The request URL path (e.g. "/api/ai/v1/projects/xxx/clips/yyy/timing")

    Returns:
        An example request body dict if a match is found, otherwise None.
    """
    for compiled_re, fmt in _COMPILED_FORMATS:
        if compiled_re.match(path):
            return fmt
    return None
