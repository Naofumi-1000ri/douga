"""Video trimming and processing service.

Provides:
- Video trimming (cut segments)
- Video concatenation
- Video export with codec options
"""

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.config import get_settings


@dataclass
class TrimConfig:
    """Configuration for video trimming."""

    start_ms: int
    end_ms: Optional[int] = None
    reencode: bool = False
    crf: int = 18
    width: Optional[int] = None
    height: Optional[int] = None

    @property
    def expected_duration_ms(self) -> int:
        """Calculate expected duration from config."""
        if self.end_ms is None:
            return 0
        return self.end_ms - self.start_ms


@dataclass
class VideoOutput:
    """Output result from video processing."""

    path: Path
    duration_ms: int
    width: int
    height: int
    file_size: int = 0

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "path": str(self.path),
            "duration_ms": self.duration_ms,
            "width": self.width,
            "height": self.height,
            "file_size": self.file_size,
        }


class VideoTrimmer:
    """Service for video trimming and processing."""

    def __init__(self):
        self.settings = get_settings()

    def _get_video_info(self, video_path: str) -> dict:
        """Get video information using ffprobe."""
        result = subprocess.run(
            [
                self.settings.ffprobe_path,
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,duration",
                "-show_entries", "format=duration",
                "-of", "json",
                video_path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)

        width = height = 0
        duration = 0.0

        if data.get("streams"):
            stream = data["streams"][0]
            width = stream.get("width", 0)
            height = stream.get("height", 0)
            if "duration" in stream:
                duration = float(stream["duration"])

        if data.get("format") and "duration" in data["format"]:
            duration = float(data["format"]["duration"])

        return {
            "width": width,
            "height": height,
            "duration_ms": int(duration * 1000),
        }

    def trim(
        self,
        input_path: str,
        output_path: str,
        config: TrimConfig,
    ) -> VideoOutput:
        """Trim video from start_ms to end_ms.

        Args:
            input_path: Path to input video
            output_path: Path for output video
            config: Trim configuration

        Returns:
            VideoOutput with result information
        """
        # Get input video info
        info = self._get_video_info(input_path)

        # Calculate times
        start_seconds = config.start_ms / 1000
        end_ms = config.end_ms if config.end_ms else info["duration_ms"]
        duration_seconds = (end_ms - config.start_ms) / 1000

        # Build ffmpeg command
        cmd = [self.settings.ffmpeg_path, "-y"]

        if config.reencode:
            # Re-encode: input first, then seek (slower but precise)
            cmd.extend(["-i", input_path])
            cmd.extend(["-ss", str(start_seconds)])
            cmd.extend(["-t", str(duration_seconds)])

            # Video encoding
            cmd.extend(["-c:v", "libx264"])
            cmd.extend(["-crf", str(config.crf)])
            cmd.extend(["-preset", "medium"])

            # Add scaling if specified
            if config.width and config.height:
                cmd.extend(["-vf", f"scale={config.width}:{config.height}"])

            # Audio encoding
            cmd.extend(["-c:a", "aac"])
            cmd.extend(["-b:a", "192k"])
        else:
            # Stream copy: seek first (fast but may be imprecise)
            cmd.extend(["-ss", str(start_seconds)])
            cmd.extend(["-i", input_path])
            cmd.extend(["-t", str(duration_seconds)])
            cmd.extend(["-c", "copy"])

        cmd.append(output_path)

        # Run ffmpeg
        subprocess.run(cmd, capture_output=True, check=True)

        # Get output info
        output_info = self._get_video_info(output_path)
        output_size = Path(output_path).stat().st_size

        return VideoOutput(
            path=Path(output_path),
            duration_ms=output_info["duration_ms"],
            width=config.width or output_info["width"],
            height=config.height or output_info["height"],
            file_size=output_size,
        )

    def concat(
        self,
        input_paths: list[str],
        output_path: str,
        reencode: bool = True,
    ) -> VideoOutput:
        """Concatenate multiple videos.

        Args:
            input_paths: List of input video paths
            output_path: Path for output video
            reencode: Whether to re-encode (required for different codecs)

        Returns:
            VideoOutput with result information
        """
        # Create concat file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as concat_file:
            for path in input_paths:
                concat_file.write(f"file '{path}'\n")
            concat_file_path = concat_file.name

        try:
            cmd = [
                self.settings.ffmpeg_path, "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_file_path,
            ]

            if reencode:
                cmd.extend(["-c:v", "libx264", "-crf", "18", "-preset", "medium"])
                cmd.extend(["-c:a", "aac", "-b:a", "192k"])
            else:
                cmd.extend(["-c", "copy"])

            cmd.append(output_path)

            subprocess.run(cmd, capture_output=True, check=True)

        finally:
            Path(concat_file_path).unlink()

        # Get output info
        output_info = self._get_video_info(output_path)
        output_size = Path(output_path).stat().st_size

        return VideoOutput(
            path=Path(output_path),
            duration_ms=output_info["duration_ms"],
            width=output_info["width"],
            height=output_info["height"],
            file_size=output_size,
        )

    def export(
        self,
        input_path: str,
        output_path: str,
        video_codec: str = "libx264",
        audio_codec: str = "aac",
        crf: int = 23,
        preset: str = "medium",
    ) -> VideoOutput:
        """Export video with specified codecs.

        Args:
            input_path: Path to input video
            output_path: Path for output video
            video_codec: Video codec (e.g., libx264, libx265)
            audio_codec: Audio codec (e.g., aac, libmp3lame)
            crf: CRF value for quality
            preset: Encoding preset

        Returns:
            VideoOutput with result information
        """
        cmd = [
            self.settings.ffmpeg_path, "-y",
            "-i", input_path,
            "-c:v", video_codec,
            "-crf", str(crf),
            "-preset", preset,
            "-c:a", audio_codec,
            "-b:a", "192k",
            output_path,
        ]

        subprocess.run(cmd, capture_output=True, check=True)

        output_info = self._get_video_info(output_path)
        output_size = Path(output_path).stat().st_size

        return VideoOutput(
            path=Path(output_path),
            duration_ms=output_info["duration_ms"],
            width=output_info["width"],
            height=output_info["height"],
            file_size=output_size,
        )
