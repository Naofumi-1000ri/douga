"""Auto-generated effects schemas from effects_spec.yaml.

DO NOT EDIT MANUALLY.
Regenerate with: uv run python backend/scripts/generate_effects.py
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ChromaKeyEffect(BaseModel):
    """Chroma key compositing (green/blue screen removal)"""

    enabled: bool = Field(default=False, description="Whether chroma key is active")
    color: str = Field(default="#00FF00", pattern=r"^#[0-9A-Fa-f]{6}$", description="Key color in hex (#RRGGBB)")
    similarity: float = Field(default=0.4, ge=0.0, le=1.0, description="Color similarity threshold (higher = more colors removed)")
    blend: float = Field(default=0.1, ge=0.0, le=1.0, description="Edge blending amount (higher = softer edges)")


class Effects(BaseModel):
    """Unified effects model for clips.

    Generated from effects_spec.yaml. Contains all supported effects.
    """

    chroma_key: ChromaKeyEffect | None = None
    opacity: float = Field(default=1.0, ge=0.0, le=1.0, description="Opacity value (0=transparent, 1=opaque)")
    blend_mode: str = Field(default="normal", description="Blend mode name")
    fade_in_ms: int = Field(default=0, ge=0, le=10000, description="Fade-in duration in milliseconds")
    fade_out_ms: int = Field(default=0, ge=0, le=10000, description="Fade-out duration in milliseconds")


class GeneratedUpdateClipEffectsRequest(BaseModel):
    """Flat effects update request (API-facing, backward compatible).

    Generated from effects_spec.yaml.
    """

    chroma_key_enabled: bool | None = Field(default=None)
    chroma_key_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    chroma_key_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    chroma_key_blend: float | None = Field(default=None, ge=0.0, le=1.0)
    opacity: float | None = Field(default=None, ge=0.0, le=1.0)
    blend_mode: str | None = Field(default=None)
    fade_in_ms: int | None = Field(default=None, ge=0, le=10000)
    fade_out_ms: int | None = Field(default=None, ge=0, le=10000)


class GeneratedEffectsDetails(BaseModel):
    """Flat effects response model (for L3 clip details).

    Generated from effects_spec.yaml.
    """

    chroma_key_enabled: bool = Field(default=False)
    chroma_key_color: str = Field(default="#00FF00")
    chroma_key_similarity: float = Field(default=0.4, ge=0.0, le=1.0)
    chroma_key_blend: float = Field(default=0.1, ge=0.0, le=1.0)
    opacity: float = Field(default=1.0, ge=0.0, le=1.0, description="Opacity value (0=transparent, 1=opaque)")
    blend_mode: str = Field(default="normal", description="Blend mode name")
    fade_in_ms: int = Field(default=0, ge=0, le=10000, description="Fade-in duration in milliseconds")
    fade_out_ms: int = Field(default=0, ge=0, le=10000, description="Fade-out duration in milliseconds")


# Capabilities data (generated from spec)
EFFECTS_CAPABILITIES: dict = {
    "supported_effects": [
        "chroma_key",
        "opacity",
        "blend_mode",
        "fade_in_ms",
        "fade_out_ms"
    ],
    "effect_params": {
        "chroma_key": {
            "color": {
                "type": "string",
                "format": "hex_color",
                "default": "#00FF00"
            },
            "similarity": {
                "type": "number",
                "min": 0.0,
                "max": 1.0,
                "default": 0.4
            },
            "blend": {
                "type": "number",
                "min": 0.0,
                "max": 1.0,
                "default": 0.1
            }
        },
        "opacity": {
            "type": "number",
            "min": 0.0,
            "max": 1.0,
            "default": 1.0
        },
        "blend_mode": {
            "type": "string",
            "enum": [
                "normal"
            ],
            "default": "normal"
        },
        "fade_in_ms": {
            "type": "integer",
            "min": 0,
            "max": 10000,
            "default": 0
        },
        "fade_out_ms": {
            "type": "integer",
            "min": 0,
            "max": 10000,
            "default": 0
        }
    }
}
