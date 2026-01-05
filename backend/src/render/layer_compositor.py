"""Multi-layer video compositing with FFmpeg filter_complex.

Layer structure (5 layers, bottom to top):
L1: 背景（3D空間/グラデーション）- Background
L2: 操作画面・スライド - Screen capture / Slides
L3: アバター（クロマキー合成後）- Avatar with chroma key
L4: エフェクト（キラキラ等）- Effects
L5: テロップ・テキスト - Text overlays
"""

import json
import subprocess
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Optional

from src.config import get_settings


class LayerType(IntEnum):
    """Layer types ordered from bottom to top."""

    BACKGROUND = 1
    SCREEN = 2
    AVATAR = 3
    EFFECT = 4
    TEXT = 5


@dataclass
class Transform:
    """Transform properties for a clip."""

    x: int = 0
    y: int = 0
    scale: float = 1.0
    rotation: int = 0
    opacity: float = 1.0


@dataclass
class ChromaKeyConfig:
    """Chroma key (green screen) configuration."""

    enabled: bool = False
    color: str = "0x00FF00"  # Green
    similarity: float = 0.3
    blend: float = 0.1


@dataclass
class Clip:
    """A clip on a layer."""

    asset_path: str
    start_ms: int  # Position on timeline
    duration_ms: int
    in_point_ms: int = 0  # Trim start in source
    transform: Transform = field(default_factory=Transform)
    chroma_key: ChromaKeyConfig = field(default_factory=ChromaKeyConfig)


@dataclass
class Layer:
    """A layer containing clips."""

    layer_type: LayerType
    clips: list[Clip] = field(default_factory=list)


@dataclass
class CompositeConfig:
    """Configuration for compositing."""

    width: int = 1920
    height: int = 1080
    fps: int = 30
    duration_ms: Optional[int] = None
    crf: int = 18
    preset: str = "medium"


@dataclass
class CompositeOutput:
    """Output result from compositing."""

    path: Path
    duration_ms: int
    width: int
    height: int
    file_size: int = 0
    layers_count: int = 0

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "path": str(self.path),
            "duration_ms": self.duration_ms,
            "width": self.width,
            "height": self.height,
            "file_size": self.file_size,
            "layers_count": self.layers_count,
        }


class LayerCompositor:
    """Service for multi-layer video compositing."""

    def __init__(self):
        self.settings = get_settings()

    def composite(
        self,
        layers: list[Layer],
        output_path: str,
        config: CompositeConfig,
    ) -> CompositeOutput:
        """Composite multiple layers into a single video.

        Args:
            layers: List of layers to composite
            output_path: Path for output video
            config: Compositing configuration

        Returns:
            CompositeOutput with result information
        """
        # Sort layers by type (bottom to top)
        sorted_layers = sorted(layers, key=lambda l: l.layer_type.value)

        # Build input files list
        input_files = []
        for layer in sorted_layers:
            for clip in layer.clips:
                if clip.asset_path not in input_files:
                    input_files.append(clip.asset_path)

        # Build filter_complex
        filter_complex = self._build_filter_complex(sorted_layers, config)

        # Build FFmpeg command
        cmd = [self.settings.ffmpeg_path, "-y"]

        # Add input files
        for input_file in input_files:
            cmd.extend(["-i", input_file])

        # Add filter_complex
        cmd.extend(["-filter_complex", filter_complex])

        # Map the final output
        cmd.extend(["-map", "[out]"])

        # Check if any input has audio and map it
        if self._has_audio_inputs(sorted_layers):
            cmd.extend(["-map", "0:a?"])

        # Output settings
        cmd.extend([
            "-c:v", "libx264",
            "-crf", str(config.crf),
            "-preset", config.preset,
            "-c:a", "aac",
            "-b:a", "192k",
        ])

        # Set duration if specified
        if config.duration_ms:
            cmd.extend(["-t", str(config.duration_ms / 1000)])

        cmd.append(output_path)

        # Run FFmpeg
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {result.stderr}")

        # Get output info
        output_info = self._get_video_info(output_path)
        output_size = Path(output_path).stat().st_size

        return CompositeOutput(
            path=Path(output_path),
            duration_ms=output_info["duration_ms"],
            width=output_info["width"],
            height=output_info["height"],
            file_size=output_size,
            layers_count=len(sorted_layers),
        )

    def _build_filter_complex(
        self,
        layers: list[Layer],
        config: CompositeConfig,
    ) -> str:
        """Build FFmpeg filter_complex string.

        Args:
            layers: Sorted list of layers
            config: Composite configuration

        Returns:
            Filter complex string
        """
        filters = []
        input_index = 0
        current_base = None

        # Track input files to indices
        file_to_index = {}
        for layer in layers:
            for clip in layer.clips:
                if clip.asset_path not in file_to_index:
                    file_to_index[clip.asset_path] = len(file_to_index)

        for layer_idx, layer in enumerate(layers):
            for clip_idx, clip in enumerate(layer.clips):
                input_idx = file_to_index[clip.asset_path]
                clip_label = f"clip_{layer_idx}_{clip_idx}"

                # Start with the input video stream
                current_label = f"[{input_idx}:v]"

                # Apply trim if needed
                if clip.in_point_ms > 0 or clip.duration_ms:
                    trim_start = clip.in_point_ms / 1000
                    trim_end = (clip.in_point_ms + clip.duration_ms) / 1000
                    trim_label = f"[trim_{clip_label}]"
                    filters.append(
                        f"{current_label}trim=start={trim_start}:end={trim_end},"
                        f"setpts=PTS-STARTPTS{trim_label}"
                    )
                    current_label = trim_label

                # Apply scale
                scale_label = f"[scale_{clip_label}]"
                target_w = int(config.width * clip.transform.scale)
                target_h = int(config.height * clip.transform.scale)
                filters.append(
                    f"{current_label}scale={target_w}:{target_h}:force_original_aspect_ratio=decrease{scale_label}"
                )
                current_label = scale_label

                # Apply chroma key if enabled
                if clip.chroma_key.enabled:
                    key_label = f"[key_{clip_label}]"
                    filters.append(
                        self._generate_chroma_key_filter(
                            current_label,
                            key_label,
                            clip.chroma_key,
                        )
                    )
                    current_label = key_label

                # For first layer, create base canvas
                if current_base is None:
                    # Create color background as base
                    base_label = "[base]"
                    filters.append(
                        f"color=c=black:s={config.width}x{config.height}:d={config.duration_ms/1000 if config.duration_ms else 10}{base_label}"
                    )
                    # Overlay first clip
                    overlay_label = f"[overlay_{layer_idx}_{clip_idx}]"
                    filters.append(
                        self._generate_overlay_filter(
                            base_label,
                            current_label,
                            overlay_label,
                            clip.transform.x,
                            clip.transform.y,
                        )
                    )
                    current_base = overlay_label
                else:
                    # Overlay on top of current base
                    overlay_label = f"[overlay_{layer_idx}_{clip_idx}]"
                    filters.append(
                        self._generate_overlay_filter(
                            current_base,
                            current_label,
                            overlay_label,
                            clip.transform.x,
                            clip.transform.y,
                        )
                    )
                    current_base = overlay_label

        # Rename final output
        if current_base:
            # Remove the brackets for final rename
            final_label = current_base.strip("[]")
            filters.append(f"{current_base}copy[out]")
        else:
            # No layers, create black output
            filters.append(f"color=c=black:s={config.width}x{config.height}:d=1[out]")

        return ";".join(filters)

    def _generate_scale_filter(
        self,
        input_label: str,
        output_label: str,
        width: int,
        height: int,
    ) -> str:
        """Generate scale filter string."""
        return f"{input_label}scale={width}:{height}{output_label}"

    def _generate_overlay_filter(
        self,
        base_label: str,
        overlay_label: str,
        output_label: str,
        x: int,
        y: int,
    ) -> str:
        """Generate overlay filter string."""
        return f"{base_label}{overlay_label}overlay={x}:{y}:shortest=1{output_label}"

    def _generate_chroma_key_filter(
        self,
        input_label: str,
        output_label: str,
        config: ChromaKeyConfig,
    ) -> str:
        """Generate chroma key filter string."""
        return (
            f"{input_label}colorkey={config.color}:{config.similarity}:{config.blend}"
            f"{output_label}"
        )

    def _has_audio_inputs(self, layers: list[Layer]) -> bool:
        """Check if any layer has audio."""
        for layer in layers:
            for clip in layer.clips:
                try:
                    result = subprocess.run(
                        [
                            self.settings.ffprobe_path,
                            "-v", "error",
                            "-select_streams", "a",
                            "-show_entries", "stream=codec_type",
                            "-of", "json",
                            clip.asset_path,
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    data = json.loads(result.stdout)
                    if data.get("streams"):
                        return True
                except Exception:
                    continue
        return False

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
