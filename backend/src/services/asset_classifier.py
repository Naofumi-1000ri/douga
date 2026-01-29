"""Automatic asset classification based on filename patterns and media metadata."""

import re
from dataclasses import dataclass


@dataclass
class ClassificationResult:
    """Result of automatic asset classification."""

    type: str  # video, audio, image
    subtype: str  # avatar, background, slide, narration, bgm, se, screen, effect, other
    confidence: float  # 0.0-1.0


# Filename patterns for subtype classification (case-insensitive)
_FILENAME_PATTERNS: list[tuple[str, str, float]] = [
    # (regex pattern, subtype, confidence boost)
    (r"avatar|アバター|character|キャラ|vtuber|greenscreen|グリーン", "avatar", 0.8),
    (r"bg[_\-\s]|background|背景|wallpaper", "background", 0.8),
    (r"slide|スライド|ppt|powerpoint|プレゼン", "slide", 0.8),
    (r"narration|ナレーション|voice|音声|vocal|セリフ|台詞", "narration", 0.8),
    (r"bgm|music|楽曲|背景音楽|soundtrack", "bgm", 0.8),
    (r"se[_\-\s\.]|sfx|効果音|sound.?effect|チャイム|ding|click|whoosh", "se", 0.8),
    (r"screen|capture|操作|demo|キャプチャ|recording|収録", "screen", 0.7),
    (r"effect|エフェクト|particle|パーティクル|sparkle|キラキラ", "effect", 0.7),
    (r"intro|イントロ|opening|オープニング", "other", 0.5),
    (r"outro|アウトロ|ending|エンディング", "other", 0.5),
]


def classify_asset(
    filename: str,
    mime_type: str,
    duration_ms: int | None = None,
    has_audio: bool | None = None,
    width: int | None = None,
    height: int | None = None,
) -> ClassificationResult:
    """Classify an asset based on filename, MIME type, and metadata.

    Args:
        filename: Original filename
        mime_type: MIME type (e.g., "video/mp4", "audio/mpeg", "image/png")
        duration_ms: Duration in milliseconds (for audio/video)
        has_audio: Whether the file has an audio track (for video)
        width: Video/image width
        height: Video/image height

    Returns:
        ClassificationResult with type, subtype, and confidence
    """
    # Determine base type from MIME
    base_type = _get_base_type(mime_type)

    # Try filename pattern matching first
    filename_lower = filename.lower()
    best_match: tuple[str, float] | None = None

    for pattern, subtype, confidence in _FILENAME_PATTERNS:
        if re.search(pattern, filename_lower):
            # Filter incompatible type/subtype combos
            if _is_compatible(base_type, subtype):
                if best_match is None or confidence > best_match[1]:
                    best_match = (subtype, confidence)

    if best_match is not None:
        return ClassificationResult(
            type=base_type,
            subtype=best_match[0],
            confidence=best_match[1],
        )

    # Fall back to metadata-based classification
    return _classify_by_metadata(base_type, duration_ms, has_audio, width, height)


def _get_base_type(mime_type: str) -> str:
    """Get base type from MIME type."""
    if mime_type.startswith("video/"):
        return "video"
    elif mime_type.startswith("audio/"):
        return "audio"
    elif mime_type.startswith("image/"):
        return "image"
    return "other"


def _is_compatible(base_type: str, subtype: str) -> bool:
    """Check if base type and subtype are compatible."""
    compatible = {
        "video": {"avatar", "screen", "background", "effect", "other"},
        "audio": {"narration", "bgm", "se", "other"},
        "image": {"slide", "background", "effect", "other"},
    }
    return subtype in compatible.get(base_type, {"other"})


def _classify_by_metadata(
    base_type: str,
    duration_ms: int | None,
    has_audio: bool | None,
    width: int | None,
    height: int | None,
) -> ClassificationResult:
    """Classify based on media metadata when filename doesn't match."""
    if base_type == "video":
        # Video without audio -> likely avatar (greenscreen)
        if has_audio is False:
            return ClassificationResult(type="video", subtype="avatar", confidence=0.5)
        # Video with audio -> likely screen capture
        if has_audio is True:
            return ClassificationResult(type="video", subtype="screen", confidence=0.4)
        return ClassificationResult(type="video", subtype="other", confidence=0.2)

    if base_type == "audio":
        if duration_ms is not None:
            duration_s = duration_ms / 1000
            # Very short audio -> sound effect
            if duration_s < 5:
                return ClassificationResult(type="audio", subtype="se", confidence=0.6)
            # Long audio -> BGM
            if duration_s > 60:
                return ClassificationResult(type="audio", subtype="bgm", confidence=0.5)
            # Medium audio -> narration
            return ClassificationResult(type="audio", subtype="narration", confidence=0.4)
        return ClassificationResult(type="audio", subtype="narration", confidence=0.2)

    if base_type == "image":
        # Wide aspect ratio -> likely slide
        if width and height and width > height:
            return ClassificationResult(type="image", subtype="slide", confidence=0.4)
        return ClassificationResult(type="image", subtype="background", confidence=0.3)

    return ClassificationResult(type=base_type, subtype="other", confidence=0.1)
