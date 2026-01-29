"""Chroma key background color auto-sampling from avatar videos.

Extracts a frame from a video, samples edge pixels, and determines the
dominant background color for chroma key compositing.
"""

import logging
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from src.config import get_settings

logger = logging.getLogger(__name__)


def _quantize(value: int, step: int = 8) -> int:
    """Quantize a color channel value to reduce noise."""
    return (value // step) * step


def _is_green_or_blue(r: int, g: int, b: int) -> bool:
    """Check if an RGB color is green-ish or blue-ish (common chroma key colors)."""
    # Green: G channel dominant
    if g > r and g > b and g >= 80:
        return True
    # Blue: B channel dominant
    if b > r and b > g and b >= 80:
        return True
    return False


def sample_chroma_key_color(file_path: str) -> str | None:
    """Sample the dominant background color from a video file.

    Algorithm:
    1. Extract a frame at 1 second using FFmpeg
    2. Open with Pillow and sample edge pixels (all 4 borders)
    3. Quantize RGB channels (step=8) to reduce noise
    4. Find the most frequent color and compute coverage ratio
    5. Return hex color if: green/blue with >50% coverage, or any color with >70% coverage
    6. Return None if conditions not met or on any error

    Args:
        file_path: Path to the video file on disk.

    Returns:
        Hex color string (e.g. '#00b800') or None.
    """
    try:
        from PIL import Image

        settings = get_settings()

        # Extract frame at 1 second
        with tempfile.NamedTemporaryFile(suffix=".png", delete=True) as tmp:
            frame_path = tmp.name

            cmd = [
                settings.ffmpeg_path,
                "-ss", "1",
                "-i", file_path,
                "-frames:v", "1",
                "-y",
                frame_path,
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                logger.warning(
                    "FFmpeg frame extraction failed: %s", result.stderr[:200]
                )
                return None

            img = Image.open(frame_path).convert("RGB")

        w, h = img.size
        if w == 0 or h == 0:
            return None

        # Sample edge pixels: all 4 borders
        pixels: list[tuple[int, int, int]] = []

        # Top and bottom rows
        for x in range(w):
            pixels.append(img.getpixel((x, 0)))
            pixels.append(img.getpixel((x, h - 1)))

        # Left and right columns (excluding corners already sampled)
        for y in range(1, h - 1):
            pixels.append(img.getpixel((0, y)))
            pixels.append(img.getpixel((w - 1, y)))

        if not pixels:
            return None

        # Quantize to reduce noise
        quantized = [
            (_quantize(r), _quantize(g), _quantize(b))
            for r, g, b in pixels
        ]

        # Find most frequent color
        counter = Counter(quantized)
        most_common_color, most_common_count = counter.most_common(1)[0]
        coverage = most_common_count / len(quantized)

        r, g, b = most_common_color

        # Decision: green/blue with >50% coverage, or any color with >70%
        if _is_green_or_blue(r, g, b) and coverage > 0.50:
            hex_color = f"#{r:02x}{g:02x}{b:02x}"
            logger.info(
                "Chroma key detected: %s (coverage=%.1f%%)",
                hex_color, coverage * 100,
            )
            return hex_color

        if coverage > 0.70:
            hex_color = f"#{r:02x}{g:02x}{b:02x}"
            logger.info(
                "Chroma key detected (high coverage): %s (coverage=%.1f%%)",
                hex_color, coverage * 100,
            )
            return hex_color

        logger.debug(
            "No chroma key detected: dominant=%s coverage=%.1f%%",
            most_common_color, coverage * 100,
        )
        return None

    except Exception:
        logger.exception("Chroma key sampling failed for %s", file_path)
        return None
