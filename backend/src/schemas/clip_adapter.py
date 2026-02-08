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


# ClipEffects: kept as a lightweight subset for clip_adapter use.
# The full Effects model is in effects_generated.py (SSOT).
# This intentionally omits chroma_key/fades since clip_adapter
# currently does not support them in the unified input path.
class ClipEffects(BaseModel):
    """Clip effects (subset for clip adapter input)."""

    opacity: float = Field(default=1.0, ge=0, le=1)
    blend_mode: str = Field(default="normal")


class Transition(BaseModel):
    """Transition configuration."""

    type: str = Field(default="none")
    duration_ms: int = Field(default=0, ge=0)


class TextStyle(BaseModel):
    """Text styling options.

    Uses extra='forbid' so that unknown keys cause validation to fail,
    allowing fallback to dict in the TextStyle | dict union.
    """

    model_config = {"extra": "forbid"}

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

    # Conversion warnings (populated during validation)
    _conversion_warnings: list[str] = []

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="after")
    def validate_and_normalize(self) -> "UnifiedClipInput":
        """Validate format consistency and normalize.

        Collects warnings for:
        - Mixed format (both flat and nested provided) - flat takes precedence
        - Non-uniform scale (scale.x != scale.y) - coerced to scale.x
        - Unsupported nested fields (rotation, opacity, anchor, effects, transitions)
        """
        warnings: list[str] = []
        has_nested = self.transform is not None
        has_flat = any(v is not None for v in [self.x, self.y, self.scale])

        # Warn about mixed format (both flat and nested)
        if has_nested and has_flat:
            warnings.append(
                "Both flat (x/y/scale) and nested (transform) provided; "
                "flat values take precedence, nested transform ignored for positioning"
            )

        # If nested transform is provided without flat values, extract flat values
        if has_nested and not has_flat:
            # Nested format - extract position.x, position.y, scale.x (use uniform scale)
            self.x = self.transform.position.x
            self.y = self.transform.position.y

            # Check for non-uniform scale (only relevant when using nested scale)
            if self.transform.scale.x != self.transform.scale.y:
                warnings.append(
                    f"Non-uniform scale (x={self.transform.scale.x}, y={self.transform.scale.y}) "
                    f"coerced to uniform scale={self.transform.scale.x}"
                )
            self.scale = self.transform.scale.x

        # Always warn about unsupported transform fields when transform exists
        if has_nested:
            if self.transform.rotation != 0:
                warnings.append(
                    f"transform.rotation={self.transform.rotation} is not yet supported, ignored"
                )
            if self.transform.opacity != 1.0:
                warnings.append(
                    f"transform.opacity={self.transform.opacity} is not yet supported, ignored"
                )
            if self.transform.anchor.x != 0.5 or self.transform.anchor.y != 0.5:
                warnings.append(
                    "transform.anchor is not yet supported, ignored"
                )
            # Warn about non-uniform scale even in mixed format
            if has_flat and self.transform.scale.x != self.transform.scale.y:
                warnings.append(
                    f"transform.scale is non-uniform (x={self.transform.scale.x}, y={self.transform.scale.y}), "
                    "but flat scale takes precedence"
                )

        # Warn about unsupported clip-level fields
        if self.effects is not None:
            warnings.append("effects field is not yet supported, ignored")
        if self.transition_in is not None:
            warnings.append("transition_in field is not yet supported, ignored")
        if self.transition_out is not None:
            warnings.append("transition_out field is not yet supported, ignored")

        object.__setattr__(self, "_conversion_warnings", warnings)
        return self

    def get_conversion_warnings(self) -> list[str]:
        """Get warnings generated during conversion."""
        return getattr(self, "_conversion_warnings", [])

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

        # Transform - flat values take precedence, fall back to nested transform
        # This matches the warning in validate_and_normalize()
        if self.x is not None:
            result["x"] = self.x
        elif self.transform is not None:
            result["x"] = self.transform.position.x

        if self.y is not None:
            result["y"] = self.y
        elif self.transform is not None:
            result["y"] = self.transform.position.y

        if self.scale is not None:
            result["scale"] = self.scale
        elif self.transform is not None:
            result["scale"] = self.transform.scale.x

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


# =============================================================================
# Unified Move Clip Input
# =============================================================================


class UnifiedMoveClipInput(BaseModel):
    """Unified move clip input.

    Move is simpler than add - just timeline position and optional layer change.
    No nested/flat format complexity needed.
    """

    new_start_ms: int = Field(ge=0, description="New timeline position in milliseconds")
    new_layer_id: str | None = Field(
        default=None, description="Target layer ID (if changing layers)"
    )


# =============================================================================
# Unified Transform Clip Input
# =============================================================================


class UnifiedTransformInput(BaseModel):
    """Unified transform input accepting both flat and nested formats.

    Flat format:
        {"x": 100, "y": 200, "scale": 1.5}

    Nested format:
        {"transform": {"position": {"x": 100, "y": 200}, "scale": {"x": 1.5, "y": 1.5}}}
    """

    # Nested format fields
    transform: Transform | None = None

    # Flat format fields
    x: float | None = Field(default=None, ge=-3840, le=3840)
    y: float | None = Field(default=None, ge=-2160, le=2160)
    scale: float | None = Field(default=None, ge=0.01, le=10.0)
    width: float | None = Field(default=None, ge=1, le=7680)
    height: float | None = Field(default=None, ge=1, le=4320)
    rotation: float | None = Field(default=None, ge=-360, le=360)
    anchor: Literal["center", "top-left", "top-right", "bottom-left", "bottom-right"] | None = (
        None
    )

    # Conversion warnings
    _conversion_warnings: list[str] = []

    model_config = {"arbitrary_types_allowed": True}

    @model_validator(mode="after")
    def validate_and_normalize(self) -> "UnifiedTransformInput":
        """Validate format consistency and collect warnings."""
        warnings: list[str] = []
        has_nested = self.transform is not None
        has_flat = any(
            v is not None for v in [self.x, self.y, self.scale, self.width, self.height]
        )

        # Warn about mixed format
        if has_nested and has_flat:
            warnings.append(
                "Both flat (x/y/scale) and nested (transform) provided; "
                "flat values take precedence"
            )

        # Warn about unsupported nested fields
        if has_nested:
            # Note: rotation IS supported in flat format, so only warn if using nested
            if self.transform.opacity != 1.0:
                warnings.append(
                    f"transform.opacity={self.transform.opacity} is not yet supported, ignored"
                )
            if self.transform.anchor.x != 0.5 or self.transform.anchor.y != 0.5:
                warnings.append("transform.anchor is not yet supported, ignored")
            if self.transform.scale.x != self.transform.scale.y:
                if not has_flat:
                    warnings.append(
                        f"Non-uniform scale (x={self.transform.scale.x}, y={self.transform.scale.y}) "
                        f"coerced to uniform scale={self.transform.scale.x}"
                    )

        object.__setattr__(self, "_conversion_warnings", warnings)
        return self

    def get_conversion_warnings(self) -> list[str]:
        """Get warnings generated during conversion."""
        return getattr(self, "_conversion_warnings", [])

    def to_flat_dict(self) -> dict[str, Any]:
        """Convert to flat format dict for UpdateClipTransformRequest.

        Flat values take precedence over nested transform.
        Only includes nested transform fields if they were explicitly provided
        (using model_fields_set) to avoid overwriting existing values with defaults.
        This includes checking individual axes within position/scale objects.
        """
        result: dict[str, Any] = {}

        # Check which nested fields were explicitly provided (including nested axes)
        nested_has_x = (
            self.transform is not None
            and "position" in self.transform.model_fields_set
            and "x" in self.transform.position.model_fields_set
        )
        nested_has_y = (
            self.transform is not None
            and "position" in self.transform.model_fields_set
            and "y" in self.transform.position.model_fields_set
        )
        nested_has_scale = (
            self.transform is not None
            and "scale" in self.transform.model_fields_set
            and "x" in self.transform.scale.model_fields_set
        )
        nested_has_rotation = (
            self.transform is not None
            and "rotation" in self.transform.model_fields_set
        )

        # x - flat takes precedence, only use nested if explicitly provided
        if self.x is not None:
            result["x"] = self.x
        elif nested_has_x:
            result["x"] = self.transform.position.x

        # y - flat takes precedence, only use nested if explicitly provided
        if self.y is not None:
            result["y"] = self.y
        elif nested_has_y:
            result["y"] = self.transform.position.y

        # scale - flat takes precedence, only use nested if explicitly provided
        if self.scale is not None:
            result["scale"] = self.scale
        elif nested_has_scale:
            result["scale"] = self.transform.scale.x

        # rotation - flat takes precedence, nested also supported if explicitly provided
        if self.rotation is not None:
            result["rotation"] = self.rotation
        elif nested_has_rotation:
            result["rotation"] = self.transform.rotation

        # width/height - only flat format (not in nested spec)
        if self.width is not None:
            result["width"] = self.width
        if self.height is not None:
            result["height"] = self.height

        # anchor - only flat format
        if self.anchor is not None:
            result["anchor"] = self.anchor

        return result
