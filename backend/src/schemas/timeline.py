from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


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
    is_filler: bool = False      # 「えー」「あのー」etc
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


class Transform(BaseModel):
    x: float = 0
    y: float = 0
    width: float | None = None
    height: float | None = None
    scale: float = 1.0
    rotation: float = 0
    anchor: str = "center"


class ChromaKeyEffect(BaseModel):
    enabled: bool = False
    color: str = "#00FF00"
    similarity: float = 0.4
    blend: float = 0.1


class Effects(BaseModel):
    chroma_key: ChromaKeyEffect | None = None
    opacity: float = 1.0
    blend_mode: str = "normal"


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
    enabled: bool = True
    duck_to: float = 0.1
    attack_ms: int = 200
    release_ms: int = 500
    trigger_track: str | None = None


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
