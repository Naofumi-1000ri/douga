"""Text rendering and effects for video overlays.

Features:
- Japanese text rendering with custom fonts (NotoSansJP)
- Text styling (color, size, shadow, outline)
- Effects (sparkle/キラキラ, glow, pulse)
- Fade transitions
"""

import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Union

from src.config import get_settings


class EffectType(Enum):
    """Types of visual effects."""

    SPARKLE = "sparkle"
    GLOW = "glow"
    PULSE = "pulse"


class TransitionType(Enum):
    """Types of transitions."""

    FADE_IN = "fade_in"
    FADE_OUT = "fade_out"
    FADE_IN_OUT = "fade_in_out"
    SLIDE_IN = "slide_in"
    SLIDE_OUT = "slide_out"


@dataclass
class TextStyle:
    """Text styling configuration."""

    font_size: int = 48
    font_color: str = "white"
    font_family: str = "NotoSansJP"
    bold: bool = False
    italic: bool = False
    outline_color: Optional[str] = None
    outline_width: int = 0
    shadow_color: Optional[str] = None
    shadow_offset: int = 2


@dataclass
class TextPosition:
    """Text positioning configuration."""

    x: Union[int, str] = "center"  # Can be int or "center", "left", "right"
    y: Union[int, str] = "center"  # Can be int or "center", "top", "bottom"
    anchor: str = "center"


@dataclass
class TextConfig:
    """Configuration for text overlay."""

    text: str
    start_ms: int
    duration_ms: int
    style: TextStyle = field(default_factory=TextStyle)
    position: TextPosition = field(default_factory=TextPosition)


@dataclass
class EffectConfig:
    """Configuration for visual effects."""

    effect_type: EffectType
    intensity: float = 0.5
    color: str = "white"
    radius: int = 5


@dataclass
class TransitionConfig:
    """Configuration for transitions."""

    transition_type: TransitionType
    duration_ms: int = 500


@dataclass
class TextOverlay:
    """Complete text overlay with effects and transitions."""

    text_config: TextConfig
    effect: Optional[EffectConfig] = None
    transition_in: Optional[TransitionConfig] = None
    transition_out: Optional[TransitionConfig] = None


class TextRenderer:
    """Service for rendering text and effects."""

    def __init__(self):
        self.settings = get_settings()
        self._font_paths = {
            "NotoSansJP": "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
            "NotoSansJP-Bold": "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        }

    def render_text_image(
        self,
        config: TextConfig,
        output_path: str,
        width: int = 1920,
        height: int = 1080,
    ) -> Path:
        """Render text to a PNG image with transparency.

        Args:
            config: Text configuration
            output_path: Output PNG path
            width: Image width
            height: Image height

        Returns:
            Path to the generated image
        """
        # Build drawtext filter
        drawtext_filter = self._build_drawtext_params(config)

        # Create transparent background with text
        cmd = [
            self.settings.ffmpeg_path,
            "-y",
            "-f", "lavfi",
            "-i", f"color=c=black@0:s={width}x{height}:d=1",
            "-vf", f"format=rgba,{drawtext_filter}",
            "-frames:v", "1",
            output_path,
        ]

        subprocess.run(cmd, capture_output=True, check=True)

        return Path(output_path)

    def generate_drawtext_filter(
        self,
        config: TextConfig,
        transition_in: Optional[TransitionConfig] = None,
        transition_out: Optional[TransitionConfig] = None,
    ) -> str:
        """Generate FFmpeg drawtext filter string.

        Args:
            config: Text configuration
            transition_in: Optional fade in transition
            transition_out: Optional fade out transition

        Returns:
            FFmpeg filter string
        """
        params = self._build_drawtext_params(config)

        # Add timing enable
        start_s = config.start_ms / 1000
        end_s = (config.start_ms + config.duration_ms) / 1000
        enable_expr = f"between(t,{start_s},{end_s})"

        # Add alpha for transitions
        if transition_in or transition_out:
            alpha_expr = self._build_alpha_expression(
                config, transition_in, transition_out
            )
            params = params.replace(
                "drawtext=",
                f"drawtext=alpha='{alpha_expr}':"
            )

        return f"{params}:enable='{enable_expr}'"

    def _build_drawtext_params(self, config: TextConfig) -> str:
        """Build drawtext filter parameters."""
        style = config.style
        position = config.position

        # Escape text for FFmpeg
        escaped_text = config.text.replace("'", "'\\''").replace(":", "\\:")

        # Get font path
        font_path = self._font_paths.get(
            style.font_family,
            self._font_paths["NotoSansJP"]
        )

        # Build position expression
        x_expr = self._position_to_expr(position.x, "w", "text_w")
        y_expr = self._position_to_expr(position.y, "h", "text_h")

        params = [
            f"drawtext=text='{escaped_text}'",
            f"fontfile='{font_path}'",
            f"fontsize={style.font_size}",
            f"fontcolor={style.font_color}",
            f"x={x_expr}",
            f"y={y_expr}",
        ]

        # Add shadow if specified
        if style.shadow_color:
            params.extend([
                f"shadowcolor={style.shadow_color}",
                f"shadowx={style.shadow_offset}",
                f"shadowy={style.shadow_offset}",
            ])

        # Add outline (border) if specified
        if style.outline_color and style.outline_width > 0:
            params.extend([
                f"borderw={style.outline_width}",
                f"bordercolor={style.outline_color}",
            ])

        return ":".join(params)

    def _position_to_expr(
        self,
        pos: Union[int, str],
        dim: str,
        text_dim: str,
    ) -> str:
        """Convert position to FFmpeg expression."""
        if isinstance(pos, int):
            return str(pos)
        elif pos == "center":
            return f"({dim}-{text_dim})/2"
        elif pos in ("left", "top"):
            return "10"
        elif pos in ("right", "bottom"):
            return f"({dim}-{text_dim}-10)"
        return str(pos)

    def _build_alpha_expression(
        self,
        config: TextConfig,
        transition_in: Optional[TransitionConfig],
        transition_out: Optional[TransitionConfig],
    ) -> str:
        """Build alpha expression for fade transitions."""
        start_s = config.start_ms / 1000
        end_s = (config.start_ms + config.duration_ms) / 1000

        parts = []

        if transition_in:
            fade_in_end = start_s + (transition_in.duration_ms / 1000)
            parts.append(
                f"if(lt(t,{fade_in_end}),(t-{start_s})/{transition_in.duration_ms/1000},1)"
            )

        if transition_out:
            fade_out_start = end_s - (transition_out.duration_ms / 1000)
            if parts:
                parts.append(
                    f"if(gt(t,{fade_out_start}),({end_s}-t)/{transition_out.duration_ms/1000},1)"
                )
            else:
                parts.append(
                    f"if(gt(t,{fade_out_start}),({end_s}-t)/{transition_out.duration_ms/1000},1)"
                )

        if not parts:
            return "1"

        # Combine with min for both transitions
        if len(parts) == 2:
            return f"min({parts[0]},{parts[1]})"
        return parts[0]

    def generate_effect_overlay(
        self,
        effect: EffectConfig,
        width: int,
        height: int,
        duration_ms: int,
        output_dir: str,
    ) -> str:
        """Generate effect overlay video.

        Args:
            effect: Effect configuration
            width: Video width
            height: Video height
            duration_ms: Effect duration
            output_dir: Directory for output

        Returns:
            Path to generated effect video
        """
        output_path = Path(output_dir) / "effect.mp4"
        duration_s = duration_ms / 1000

        if effect.effect_type == EffectType.SPARKLE:
            # Generate sparkle effect using noise + threshold
            filter_complex = (
                f"color=c=black:s={width}x{height}:d={duration_s},"
                f"noise=alls={int(effect.intensity * 100)}:allf=t,"
                f"eq=brightness={effect.intensity}:contrast=2,"
                f"colorkey=black:0.3:0.2,"
                f"format=rgba"
            )
        elif effect.effect_type == EffectType.GLOW:
            # Generate glow effect
            filter_complex = (
                f"color=c={effect.color}@0.3:s={width}x{height}:d={duration_s},"
                f"gblur=sigma={effect.radius},"
                f"format=rgba"
            )
        else:
            # Pulse effect
            filter_complex = (
                f"color=c={effect.color}:s={width}x{height}:d={duration_s},"
                f"fade=t=in:st=0:d={duration_s/2},"
                f"fade=t=out:st={duration_s/2}:d={duration_s/2},"
                f"format=rgba"
            )

        cmd = [
            self.settings.ffmpeg_path,
            "-y",
            "-f", "lavfi",
            "-i", filter_complex,
            "-c:v", "png",
            "-pix_fmt", "rgba",
            str(output_path),
        ]

        try:
            subprocess.run(cmd, capture_output=True, check=True)
        except subprocess.CalledProcessError:
            # Fallback: create simple overlay
            simple_filter = f"color=c={effect.color}@0.5:s={width}x{height}:d={duration_s}"
            cmd = [
                self.settings.ffmpeg_path,
                "-y",
                "-f", "lavfi",
                "-i", simple_filter,
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                str(output_path),
            ]
            subprocess.run(cmd, capture_output=True, check=True)

        return str(output_path)

    def generate_effect_filter(self, effect: EffectConfig) -> str:
        """Generate FFmpeg filter string for effect.

        Args:
            effect: Effect configuration

        Returns:
            FFmpeg filter string
        """
        if effect.effect_type == EffectType.GLOW:
            return f"gblur=sigma={effect.radius}"
        elif effect.effect_type == EffectType.SPARKLE:
            return f"noise=alls={int(effect.intensity * 50)}:allf=t"
        elif effect.effect_type == EffectType.PULSE:
            return f"fade=t=in:d=0.5,fade=t=out:st=0.5:d=0.5"
        return ""

    def generate_transition_filter(
        self,
        transition: TransitionConfig,
        start_ms: int,
        total_duration_ms: int,
    ) -> str:
        """Generate FFmpeg filter for transitions.

        Args:
            transition: Transition configuration
            start_ms: Start time in milliseconds
            total_duration_ms: Total duration

        Returns:
            FFmpeg filter string
        """
        duration_s = transition.duration_ms / 1000
        start_s = start_ms / 1000
        total_s = total_duration_ms / 1000

        if transition.transition_type == TransitionType.FADE_IN:
            return f"fade=t=in:st={start_s}:d={duration_s}"

        elif transition.transition_type == TransitionType.FADE_OUT:
            fade_start = total_s - duration_s
            return f"fade=t=out:st={fade_start}:d={duration_s}"

        elif transition.transition_type == TransitionType.FADE_IN_OUT:
            fade_out_start = total_s - duration_s
            return (
                f"fade=t=in:st=0:d={duration_s},"
                f"fade=t=out:st={fade_out_start}:d={duration_s}"
            )

        elif transition.transition_type == TransitionType.SLIDE_IN:
            return f"fade=t=in:st={start_s}:d={duration_s}"

        elif transition.transition_type == TransitionType.SLIDE_OUT:
            return f"fade=t=out:st={start_s}:d={duration_s}"

        return ""
