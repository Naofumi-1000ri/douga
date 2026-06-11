import re
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# Import generated effects schemas (SSOT: effects_spec.yaml)
from src.schemas.effects_generated import ChromaKeyEffect, Effects  # noqa: F401

# =============================================================================
# Transcription (文字起こし) Schemas
# =============================================================================

CutReason = Literal["silence", "mistake", "manual", "filler"]


class TranscriptionWord(BaseModel):
    """Individual word with timing information."""

    word: str
    start_ms: int
    end_ms: int
    confidence: float = 1.0


class TranscriptionSegment(BaseModel):
    """A segment of transcription with optional cut flag."""

    id: str
    start_ms: int
    end_ms: int
    text: str
    words: list[TranscriptionWord] = Field(default_factory=list)
    confidence: float = 1.0

    # Cut flag - if set, this segment should be removed
    cut: bool = False
    cut_reason: CutReason | None = None

    # For mistake detection
    is_repetition: bool = False  # 言い直し
    is_filler: bool = False  # 「えー」「あのー」etc
    corrected_text: str | None = None  # AI suggested correction


class Transcription(BaseModel):
    """Full transcription for an asset."""

    asset_id: UUID
    language: str = "ja"
    segments: list[TranscriptionSegment] = Field(default_factory=list)
    duration_ms: int = 0

    # Processing status
    status: Literal["pending", "processing", "completed", "failed"] = "pending"
    error_message: str | None = None

    # Statistics
    total_segments: int = 0
    cut_segments: int = 0
    silence_duration_ms: int = 0
    mistake_count: int = 0


_HEX_COLOR_RE = re.compile(r"^[0-9A-Fa-f]{6}$")


class ClickHighlight(BaseModel):
    """A single click highlight overlay (drawbox) attached to a clip."""

    x_norm: float = Field(default=0.0, ge=0.0, le=1.0)
    y_norm: float = Field(default=0.0, ge=0.0, le=1.0)
    w_norm: float = Field(default=0.1, ge=0.0, le=1.0)
    h_norm: float = Field(default=0.08, ge=0.0, le=1.0)
    time_ms: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=1500, ge=0)
    # color: 6-digit hex without '#' prefix (e.g. "FF6600")
    color: str = Field(default="FF6600")
    thickness: int = Field(default=4, ge=1, le=100)

    @field_validator("color", mode="before")
    @classmethod
    def _validate_color(cls, v: object) -> str:
        s = str(v).strip()
        # Allow optional '#' prefix – strip it before validation
        s = s.lstrip("#")
        if not _HEX_COLOR_RE.match(s):
            raise ValueError(f"color must be a 6-digit hex string (e.g. 'FF6600'), got {v!r}")
        return s.upper()


class Transform(BaseModel):
    x: float = 0
    y: float = 0
    width: float | None = None
    height: float | None = None
    # Legacy uniform scale — kept for backward compatibility (old projects).
    # New code should write scaleX/scaleY. When reading, scaleX/scaleY take
    # precedence; if absent they fall back to this field.
    scale: float = 1.0
    scaleX: float | None = None  # noqa: N815  # X-axis scale; None means "use legacy scale"
    scaleY: float | None = None  # noqa: N815  # Y-axis scale; None means "use legacy scale"
    rotation: float = 0
    anchor: str = "center"

    @property
    def effective_scale_x(self) -> float:
        return self.scaleX if self.scaleX is not None else self.scale

    @property
    def effective_scale_y(self) -> float:
        return self.scaleY if self.scaleY is not None else self.scale


# ChromaKeyEffect and Effects are imported from effects_generated.py (SSOT)
# Old definitions removed to prevent drift.
# ChromaKeyEffect: enabled, color, similarity=0.4, blend=0.1
# Effects: chroma_key, opacity, blend_mode, fade_in_ms, fade_out_ms


class Transition(BaseModel):
    type: Literal["none", "fade", "slide_left", "slide_right", "slide_up", "slide_down"] = "none"
    duration_ms: int = 500


class Clip(BaseModel):
    id: str
    asset_id: UUID | None = None  # None for text/effect clips
    start_ms: int = 0
    duration_ms: int = 0
    in_point_ms: int = 0
    out_point_ms: int | None = None
    transform: Transform = Field(default_factory=Transform)
    effects: Effects = Field(default_factory=Effects)
    transition_in: Transition = Field(default_factory=Transition)
    transition_out: Transition = Field(default_factory=Transition)

    # For text clips
    text_content: str | None = None
    text_style: dict[str, Any] | None = None

    # For effect clips
    effect_type: str | None = None
    effect_settings: dict[str, Any] | None = None

    # For shape clips
    shape: dict[str, Any] | None = None

    # Grouping (clips in same group move/cut together)
    group_id: str | None = None

    # Playback speed (1.0 = normal, 2.0 = 2x fast)
    speed: float = Field(default=1.0, gt=0, le=10.0)

    # Animation keyframes
    keyframes: list[dict[str, Any]] | None = None

    # Click highlights (drawbox overlays)
    highlights: list[ClickHighlight] | None = None


LayerType = Literal["background", "content", "avatar", "effects", "text"]


class Layer(BaseModel):
    id: str
    name: str
    type: LayerType
    order: int
    visible: bool = True
    locked: bool = False
    clips: list[Clip] = Field(default_factory=list)


class Ducking(BaseModel):
    enabled: bool = False
    duck_to: float = 0.1
    attack_ms: int = 200
    release_ms: int = 500
    trigger_track: str | None = None


class VolumeKeyframe(BaseModel):
    """Volume automation keyframe."""

    time_ms: int  # Relative time within the clip (0 = clip start)
    value: float  # Volume value (0.0 - 1.0)


class AudioClip(BaseModel):
    id: str
    asset_id: UUID
    start_ms: int = 0
    duration_ms: int = 0
    in_point_ms: int = 0
    out_point_ms: int | None = None
    volume: float = 1.0
    fade_in_ms: int = 0
    fade_out_ms: int = 0
    speed: float = Field(default=1.0, gt=0, le=10.0)

    # Grouping (clips in same group move/cut together)
    group_id: str | None = None

    # Volume automation keyframes (for ducking, etc.)
    volume_keyframes: list[VolumeKeyframe] | None = None

    # Lip noise (click noise) removal via FFmpeg adeclick filter.
    # Applied at render time only; browser preview is not affected.
    lip_noise_removal: bool = False


AudioTrackType = Literal["narration", "bgm", "se"]


class AudioTrack(BaseModel):
    id: str
    name: str
    type: AudioTrackType
    volume: float = 1.0
    muted: bool = False
    ducking: Ducking | None = None
    clips: list[AudioClip] = Field(default_factory=list)


class TimelineData(BaseModel):
    version: str = "1.0"
    duration_ms: int = 0
    layers: list[Layer] = Field(default_factory=list)
    audio_tracks: list[AudioTrack] = Field(default_factory=list)
