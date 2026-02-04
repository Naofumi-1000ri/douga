"""Clip input adapter for transitional schema support.

Accepts both:
- Flat format (transitional/legacy): x, y, scale at top level
- Nested format (spec): transform.position.{x,y}, transform.scale.{x,y}, etc.

Converts to internal AddClipRequest format.
"""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


# =============================================================================
# Spec-compliant nested schemas
# =============================================================================


class Position(BaseModel):
    """Position in canvas coordinates."""

    x: float = Field(default=0, ge=-3840, le=3840)
    y: float = Field(default=0, ge=-2160, le=2160)


class Scale(BaseModel):
    """Scale factors."""

    x: float = Field(default=1.0, ge=0.01, le=10.0)
    y: float = Field(default=1.0, ge=0.01, le=10.0)


class Anchor(BaseModel):
    """Anchor point (normalized 0-1)."""

    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)


class Transform(BaseModel):
    """Spec-compliant transform with nested structure."""

    position: Position = Field(default_factory=Position)
    scale: Scale = Field(default_factory=Scale)
    rotation: float = Field(default=0, ge=-360, le=360)
    opacity: float = Field(default=1.0, ge=0, le=1)
    anchor: Anchor = Field(default_factory=Anchor)


class ClipEffects(BaseModel):
    """Clip effects."""

    opacity: float = Field(default=1.0, ge=0, le=1)
    blend_mode: str = Field(default="normal")


class Transition(BaseModel):
    """Transition configuration."""

    type: str = Field(default="none")
    duration_ms: int = Field(default=0, ge=0)


class TextStyle(BaseModel):
    """Text styling options."""

    font_family: str = Field(default="Noto Sans JP")
    font_size: int = Field(default=48, ge=8, le=500)
    font_weight: int = Field(default=400)
    color: str = Field(default="#ffffff")
    text_align: Literal["left", "center", "right"] = Field(default="center")
    background_color: str | None = None
    background_opacity: float = Field(default=0, ge=0, le=1)


# =============================================================================
# Unified Clip Input (accepts both flat and nested)
# =============================================================================


class UnifiedClipInput(BaseModel):
    """Unified clip input that accepts both flat and nested formats.

    Flat format (transitional):
        {
            "layer_id": "...",
            "asset_id": "...",
            "start_ms": 0,
            "duration_ms": 1000,
            "x": 0,
            "y": 0,
            "scale": 1.0
        }

    Nested format (spec):
        {
            "type": "video",
            "layer_id": "...",
            "asset_id": "...",
            "start_ms": 0,
            "duration_ms": 1000,
            "transform": {
                "position": {"x": 0, "y": 0},
                "scale": {"x": 1, "y": 1},
                "rotation": 0,
                "opacity": 1,
                "anchor": {"x": 0.5, "y": 0.5}
            }
        }
    """

    # Common required fields
    layer_id: str
    start_ms: int = Field(ge=0, description="Timeline position in milliseconds")
    duration_ms: int = Field(
        gt=0, le=3600000, description="Clip duration (max 1 hour)"
    )

    # Asset reference (required for video/image, optional for text/shape)
    asset_id: UUID | None = None

    # Timing (trim points)
    in_point_ms: int = Field(default=0, ge=0, description="Trim start in source asset")
    out_point_ms: int | None = Field(
        default=None, ge=0, description="Trim end in source asset"
    )

    # Spec format fields (nested)
    type: Literal["video", "image", "text", "shape"] | None = None
    transform: Transform | None = None
    effects: ClipEffects | None = None
    transition_in: Transition | None = None
    transition_out: Transition | None = None

    # Flat format fields (transitional)
    x: float | None = Field(default=None, ge=-3840, le=3840)
    y: float | None = Field(default=None, ge=-2160, le=2160)
    scale: float | None = Field(default=None, ge=0.01, le=10.0)

    # Text clip fields
    text_content: str | None = None
    text_style: TextStyle | dict[str, Any] | None = None

    # Grouping
    group_id: str | None = None

    @model_validator(mode="after")
    def validate_and_normalize(self) -> "UnifiedClipInput":
        """Validate format consistency and normalize."""
        has_nested = self.transform is not None
        has_flat = any(v is not None for v in [self.x, self.y, self.scale])

        # If nested transform is provided, extract flat values for internal use
        if has_nested and not has_flat:
            # Nested format - extract position.x, position.y, scale.x (use uniform scale)
            self.x = self.transform.position.x
            self.y = self.transform.position.y
            # Use x scale as uniform scale (or average)
            self.scale = self.transform.scale.x

        return self

    def to_flat_dict(self) -> dict[str, Any]:
        """Convert to flat format dictionary for internal processing.

        Returns a dict compatible with AddClipRequest.
        """
        result: dict[str, Any] = {
            "layer_id": self.layer_id,
            "start_ms": self.start_ms,
            "duration_ms": self.duration_ms,
            "in_point_ms": self.in_point_ms,
            "out_point_ms": self.out_point_ms,
            "group_id": self.group_id,
        }

        # Asset
        if self.asset_id is not None:
            result["asset_id"] = self.asset_id

        # Transform - prefer nested, fall back to flat
        if self.transform is not None:
            result["x"] = self.transform.position.x
            result["y"] = self.transform.position.y
            result["scale"] = self.transform.scale.x
        else:
            if self.x is not None:
                result["x"] = self.x
            if self.y is not None:
                result["y"] = self.y
            if self.scale is not None:
                result["scale"] = self.scale

        # Text content
        if self.text_content is not None:
            result["text_content"] = self.text_content
        if self.text_style is not None:
            if isinstance(self.text_style, TextStyle):
                result["text_style"] = self.text_style.model_dump()
            else:
                result["text_style"] = self.text_style

        return result


def adapt_clip_input(data: dict[str, Any]) -> dict[str, Any]:
    """Adapt clip input from either format to flat format.

    Args:
        data: Raw clip input data (either flat or nested format)

    Returns:
        Flat format dict compatible with AddClipRequest
    """
    unified = UnifiedClipInput.model_validate(data)
    return unified.to_flat_dict()
