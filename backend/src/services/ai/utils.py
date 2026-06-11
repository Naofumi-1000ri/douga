"""Shared helpers for the AI service package."""

from __future__ import annotations

import json
from typing import Any

DEFAULT_TEXT_STYLE: dict[str, Any] = {
    "fontFamily": "Noto Sans JP",
    "fontSize": 48,
    "fontWeight": "bold",
    "fontStyle": "normal",
    "color": "#ffffff",
    "backgroundColor": "#000000",
    "backgroundOpacity": 0.4,
    "textAlign": "center",
    "verticalAlign": "middle",
    "lineHeight": 1.4,
    "letterSpacing": 0,
    "strokeColor": "#000000",
    "strokeWidth": 2,
}

TEXT_STYLE_KEY_MAP = {
    "font_family": "fontFamily",
    "font_size": "fontSize",
    "font_weight": "fontWeight",
    "font_style": "fontStyle",
    "background_color": "backgroundColor",
    "background_opacity": "backgroundOpacity",
    "text_align": "textAlign",
    "vertical_align": "verticalAlign",
    "line_height": "lineHeight",
    "letter_spacing": "letterSpacing",
    "stroke_color": "strokeColor",
    "stroke_width": "strokeWidth",
}


def normalize_text_style_for_storage(
    text_style_data: dict[str, Any] | None,
    *,
    base_style: dict[str, Any] | None = None,
    include_defaults: bool = True,
) -> dict[str, Any]:
    """Normalize text_style to canonical camelCase keys for storage."""

    def _normalize_font_weight(value: Any) -> str:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"bold", "normal"}:
                return lowered
            try:
                value = int(lowered)
            except ValueError:
                return "normal"
        if isinstance(value, (int, float)):
            return "bold" if int(value) >= 600 else "normal"
        return "normal"

    def _canonicalize(source: dict[str, Any] | None) -> dict[str, Any]:
        if not source:
            return {}

        normalized: dict[str, Any] = {}
        for key, value in source.items():
            if value is None:
                continue

            camel_key = TEXT_STYLE_KEY_MAP.get(key, key)
            if camel_key == "fontWeight":
                normalized[camel_key] = _normalize_font_weight(value)
            elif camel_key == "backgroundColor" and isinstance(value, str) and value.strip() == "":
                normalized[camel_key] = "transparent"
            else:
                normalized[camel_key] = value
        return normalized

    normalized = dict(DEFAULT_TEXT_STYLE) if include_defaults else {}
    normalized.update(_canonicalize(base_style))
    normalized.update(_canonicalize(text_style_data))
    return normalized


def normalize_text_clip_for_storage(clip: dict[str, Any]) -> dict[str, Any]:
    """Ensure text clips store canonical text_style data."""
    if clip.get("text_content") is None:
        return clip

    clip["text_style"] = normalize_text_style_for_storage(clip.get("text_style"))
    return clip


# ---------------------------------------------------------------------------
# Timeline ms-field sanitization (defense-in-depth against float accumulation)
# ---------------------------------------------------------------------------

_MS_FIELDS = frozenset(
    {
        "start_ms",
        "duration_ms",
        "end_ms",
        "in_point_ms",
        "out_point_ms",
        "time_ms",
        "fade_in_ms",
        "fade_out_ms",
        "attack_ms",
        "release_ms",
        "export_start_ms",
        "export_end_ms",
    }
)


def _sanitize_timeline_ms(timeline_data: dict[str, Any]) -> dict[str, Any]:
    """Ensure all ms fields in timeline data are integers.

    Applies ``int(round(...))`` to every known ms-valued field so that
    floating-point values produced by speed calculations never leak into
    the persisted timeline JSON.
    """
    for layer in timeline_data.get("layers", []):
        for clip in layer.get("clips", []):
            for field in _MS_FIELDS:
                if field in clip and clip[field] is not None:
                    clip[field] = int(round(clip[field]))

    for track in timeline_data.get("audio_tracks", []):
        for clip in track.get("clips", []):
            for field in _MS_FIELDS:
                if field in clip and clip[field] is not None:
                    clip[field] = int(round(clip[field]))
        ducking = track.get("ducking", {})
        if ducking:
            for field in _MS_FIELDS:
                if field in ducking and ducking[field] is not None:
                    ducking[field] = int(round(ducking[field]))

    if "duration_ms" in timeline_data and timeline_data["duration_ms"] is not None:
        timeline_data["duration_ms"] = int(round(timeline_data["duration_ms"]))
    if "export_start_ms" in timeline_data and timeline_data["export_start_ms"] is not None:
        timeline_data["export_start_ms"] = int(round(timeline_data["export_start_ms"]))
    if "export_end_ms" in timeline_data and timeline_data["export_end_ms"] is not None:
        timeline_data["export_end_ms"] = int(round(timeline_data["export_end_ms"]))

    for marker in timeline_data.get("markers", []):
        if "time_ms" in marker and marker["time_ms"] is not None:
            marker["time_ms"] = int(round(marker["time_ms"]))

    return timeline_data


def _escape_user_string(value: str) -> str:
    """Escape a user-supplied string for safe embedding in a system prompt.

    Uses json.dumps to convert the string to a JSON string literal, which
    escapes newlines, carriage returns, and other control characters that
    could be used for prompt injection attacks.  The returned value is
    already wrapped in double-quotes, e.g. ``"my\\nname"``.
    """
    return json.dumps(value, ensure_ascii=False)
