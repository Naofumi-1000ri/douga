"""Media file information utilities using FFprobe."""

import json
import subprocess
from dataclasses import dataclass
from typing import Optional

from src.config import get_settings


def _get_settings():
    """Get settings lazily to avoid import issues in tests."""
    return get_settings()


@dataclass
class MediaInfo:
    """Media file information."""

    duration_ms: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    has_video: bool = False
    has_audio: bool = False


def _run_ffprobe(file_path: str, *args) -> dict:
    """Run ffprobe and return parsed JSON."""
    settings = _get_settings()
    cmd = [
        settings.ffprobe_path,
        "-v", "quiet",
        "-print_format", "json",
        *args,
        file_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse ffprobe output: {e}")


def get_media_duration(file_path: str) -> int:
    """
    Get media file duration in milliseconds.

    Args:
        file_path: Path to media file

    Returns:
        Duration in milliseconds

    Raises:
        RuntimeError: If ffprobe fails or duration not found
    """
    data = _run_ffprobe(file_path, "-show_format")
    format_info = data.get("format", {})

    if "duration" not in format_info:
        raise RuntimeError(f"Duration not found in: {file_path}")

    return int(float(format_info["duration"]) * 1000)


def get_video_dimensions(file_path: str) -> tuple[int, int]:
    """
    Get video width and height.

    Args:
        file_path: Path to video file

    Returns:
        Tuple of (width, height)

    Raises:
        RuntimeError: If ffprobe fails or video stream not found
    """
    data = _run_ffprobe(file_path, "-show_streams", "-select_streams", "v")

    streams = data.get("streams", [])
    if not streams:
        raise RuntimeError(f"No video stream found in: {file_path}")

    stream = streams[0]
    width = stream.get("width")
    height = stream.get("height")

    if width is None or height is None:
        raise RuntimeError(f"Video dimensions not found in: {file_path}")

    return width, height


def has_audio_track(file_path: str) -> bool:
    """
    Check if media file has an audio track.

    Args:
        file_path: Path to media file

    Returns:
        True if audio track exists, False otherwise
    """
    try:
        data = _run_ffprobe(file_path, "-show_streams", "-select_streams", "a")
        return len(data.get("streams", [])) > 0
    except RuntimeError:
        return False


def get_audio_info(file_path: str) -> Optional[dict]:
    """
    Get audio stream information.

    Args:
        file_path: Path to media file

    Returns:
        Dictionary with codec, sample_rate, channels, or None if no audio
    """
    try:
        data = _run_ffprobe(file_path, "-show_streams", "-select_streams", "a")
    except RuntimeError:
        return None

    streams = data.get("streams", [])
    if not streams:
        return None

    stream = streams[0]
    return {
        "codec": stream.get("codec_name"),
        "sample_rate": int(stream.get("sample_rate", 0)) or None,
        "channels": stream.get("channels"),
    }


def get_media_info(file_path: str) -> dict:
    """
    Get complete media file information.

    Args:
        file_path: Path to media file

    Returns:
        Dictionary with all media info

    Raises:
        RuntimeError: If ffprobe fails
    """
    data = _run_ffprobe(file_path, "-show_format", "-show_streams")

    result = {
        "duration_ms": None,
        "width": None,
        "height": None,
        "fps": None,
        "video_codec": None,
        "audio_codec": None,
        "sample_rate": None,
        "channels": None,
        "has_video": False,
        "has_audio": False,
    }

    # Get format info
    format_info = data.get("format", {})
    if "duration" in format_info:
        result["duration_ms"] = int(float(format_info["duration"]) * 1000)

    # Get stream info
    for stream in data.get("streams", []):
        codec_type = stream.get("codec_type")

        if codec_type == "video":
            result["has_video"] = True
            result["width"] = stream.get("width")
            result["height"] = stream.get("height")
            result["video_codec"] = stream.get("codec_name")

            # Calculate FPS
            r_frame_rate = stream.get("r_frame_rate", "0/1")
            if "/" in r_frame_rate:
                num, den = r_frame_rate.split("/")
                if int(den) > 0:
                    result["fps"] = int(int(num) / int(den))

        elif codec_type == "audio":
            result["has_audio"] = True
            result["audio_codec"] = stream.get("codec_name")
            result["sample_rate"] = int(stream.get("sample_rate", 0)) or None
            result["channels"] = stream.get("channels")

    return result
