"""Convert a VideoPlan to timeline_data format for the Project model.

This is a deterministic conversion — no AI involved.
Maps VideoPlan sections/elements/audio to the 5-layer + 3-track timeline structure.
"""

import uuid
from typing import Any

from src.schemas.ai_video import VideoPlan, PlanSection, PlanElement, PlanAudioElement


# Layer type to index mapping (matches Project model default)
# Array index 0 = top of layer list = renders on top (NLE convention)
LAYER_INDEX = {
    "text": 0,
    "effects": 1,
    "avatar": 2,
    "content": 3,
    "background": 4,
}

# Audio track type to index mapping
AUDIO_TRACK_INDEX = {
    "narration": 0,
    "bgm": 1,
    "se": 2,
}


def plan_to_timeline(plan: VideoPlan) -> dict[str, Any]:
    """Convert a VideoPlan to timeline_data dict.

    The output matches the structure stored in Project.timeline_data JSONB:
    - 5 layers: Background, Content, Avatar, Effects, Text
    - 3 audio tracks: Narration, BGM, SE

    Args:
        plan: The VideoPlan to convert

    Returns:
        timeline_data dict ready to be stored in Project.timeline_data
    """
    # Create fresh layer/track IDs
    layers = [
        {
            "id": str(uuid.uuid4()),
            "name": name,
            "type": layer_type,
            "order": order,
            "visible": True,
            "locked": False,
            "clips": [],
        }
        for layer_type, order, name in [
            ("text", 4, "Text"),
            ("effects", 3, "Effects"),
            ("avatar", 2, "Avatar"),
            ("content", 1, "Content"),
            ("background", 0, "Background"),
        ]
    ]

    audio_tracks = [
        {
            "id": str(uuid.uuid4()),
            "name": "Narration",
            "type": "narration",
            "volume": 1.0,
            "muted": False,
            "clips": [],
        },
        {
            "id": str(uuid.uuid4()),
            "name": "BGM",
            "type": "bgm",
            "volume": 0.3,
            "muted": False,
            "ducking": {
                "enabled": True,
                "duck_to": 0.1,
                "attack_ms": 200,
                "release_ms": 500,
            },
            "clips": [],
        },
        {
            "id": str(uuid.uuid4()),
            "name": "SE",
            "type": "se",
            "volume": 0.8,
            "muted": False,
            "clips": [],
        },
    ]

    total_duration_ms = 0

    for section in plan.sections:
        section_offset = section.start_ms

        # Convert visual elements to clips
        for elem in section.elements:
            layer_idx = LAYER_INDEX.get(elem.layer)
            if layer_idx is None:
                continue

            clip = _element_to_clip(elem, section_offset)
            layers[layer_idx]["clips"].append(clip)

            clip_end = clip["start_ms"] + clip["duration_ms"]
            total_duration_ms = max(total_duration_ms, clip_end)

        # Convert audio elements to audio clips
        for aud in section.audio:
            track_idx = AUDIO_TRACK_INDEX.get(aud.track)
            if track_idx is None:
                continue

            audio_clip = _audio_element_to_clip(aud, section_offset)
            audio_tracks[track_idx]["clips"].append(audio_clip)

            clip_end = audio_clip["start_ms"] + audio_clip["duration_ms"]
            total_duration_ms = max(total_duration_ms, clip_end)

    return {
        "version": "1.0",
        "duration_ms": total_duration_ms,
        "layers": layers,
        "audio_tracks": audio_tracks,
    }


def _element_to_clip(elem: PlanElement, section_offset_ms: int) -> dict[str, Any]:
    """Convert a PlanElement to a timeline clip dict."""
    clip: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "asset_id": elem.asset_id,
        "start_ms": section_offset_ms + elem.start_ms,
        "duration_ms": elem.duration_ms,
        "in_point_ms": 0,
        "out_point_ms": None,
        "transform": {
            "x": elem.transform.x,
            "y": elem.transform.y,
            "width": None,
            "height": None,
            "scale": elem.transform.scale,
            "rotation": elem.transform.rotation,
            "anchor": "center",
        },
        "effects": {
            "opacity": 1.0,
            "blend_mode": "normal",
            "chroma_key": None,
        },
        "transition_in": {"type": "none", "duration_ms": 0},
        "transition_out": {"type": "none", "duration_ms": 0},
    }

    # Apply chroma key if specified
    if elem.effects.chroma_key:
        clip["effects"]["chroma_key"] = elem.effects.chroma_key

    # Apply fade transitions
    if elem.effects.fade_in_ms > 0:
        clip["transition_in"] = {"type": "fade", "duration_ms": elem.effects.fade_in_ms}
    if elem.effects.fade_out_ms > 0:
        clip["transition_out"] = {"type": "fade", "duration_ms": elem.effects.fade_out_ms}

    # Text clip
    if elem.text_content:
        clip["text_content"] = elem.text_content
        clip["asset_id"] = None  # Text clips don't reference assets
        if elem.text_style:
            clip["text_style"] = {
                "fontFamily": "Hiragino Sans",
                "fontSize": elem.text_style.fontSize,
                "fontWeight": elem.text_style.fontWeight,
                "fontStyle": "normal",
                "color": elem.text_style.color,
                "backgroundColor": elem.text_style.backgroundColor or "",
                "backgroundOpacity": elem.text_style.backgroundOpacity,
                "textAlign": elem.text_style.textAlign,
                "verticalAlign": "middle",
                "lineHeight": 1.5,
                "letterSpacing": 0,
                "strokeColor": elem.text_style.strokeColor,
                "strokeWidth": elem.text_style.strokeWidth,
            }

    return clip


def _audio_element_to_clip(aud: PlanAudioElement, section_offset_ms: int) -> dict[str, Any]:
    """Convert a PlanAudioElement to a timeline audio clip dict."""
    return {
        "id": str(uuid.uuid4()),
        "asset_id": aud.asset_id,
        "start_ms": section_offset_ms + aud.start_ms,
        "duration_ms": aud.duration_ms,
        "in_point_ms": 0,
        "out_point_ms": None,
        "volume": aud.volume,
        "fade_in_ms": aud.fade_in_ms,
        "fade_out_ms": aud.fade_out_ms,
    }
