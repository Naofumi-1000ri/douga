"""Chroma key processing helpers."""

import asyncio
import base64
import logging
import os
import subprocess
from typing import Any

from src.config import get_settings
from src.services.chroma_key_sampler import sample_chroma_key_color

logger = logging.getLogger(__name__)


class ChromaKeyService:
    """Resolve key color and apply chroma key processing."""

    def __init__(self) -> None:
        self.settings = get_settings()

    def _get_despill_type(self, key_color: str) -> str:
        """Determine despill type based on key color (green or blue).

        Args:
            key_color: Hex color string (e.g., '#00FF00', '0x0000FF')

        Returns:
            'green' or 'blue' for despill filter type
        """
        # Normalize color string: remove # or 0x prefix
        color = key_color.lstrip("#").lstrip("0x").upper()

        # Ensure 6 characters (pad with zeros if needed)
        if len(color) < 6:
            color = color.zfill(6)

        try:
            r = int(color[0:2], 16)
            g = int(color[2:4], 16)
            b = int(color[4:6], 16)
        except (ValueError, IndexError):
            # Default to green if parsing fails
            logger.warning("Failed to parse key color '%s', defaulting to green despill", key_color)
            return "green"

        # If blue channel is dominant and green is not, use blue despill
        # Otherwise default to green (most common chroma key color)
        if b > g and b > r:
            return "blue"
        return "green"

    def resolve_key_color(
        self,
        input_path: str,
        key_color: str,
        *,
        sample_times_ms: list[int] | None = None,
        clip_start_ms: int = 0,
        in_point_ms: int = 0,
    ) -> str:
        """Resolve key color from input or auto-sampling."""
        if key_color.lower() != "auto":
            return key_color

        detected = sample_chroma_key_color(
            input_path,
            sample_times_ms=sample_times_ms,
            clip_start_ms=clip_start_ms,
            in_point_ms=in_point_ms,
        )
        if not detected:
            logger.warning("Chroma key auto-detect failed, using default #00FF00")
            return "#00FF00"
        return detected

    async def apply_to_video(
        self,
        input_path: str,
        output_path: str,
        *,
        key_color: str,
        similarity: float,
        blend: float,
    ) -> None:
        """Apply chroma key filter to a video and write output."""
        color = key_color.replace("#", "0x")
        despill_type = self._get_despill_type(key_color)
        # Convert to rgba for colorkey + despill (don't convert back in filter;
        # let -pix_fmt yuva420p handle the output conversion for the encoder)
        vf_filter = f"format=rgba,colorkey={color}:{similarity}:{blend},despill=type={despill_type}"
        cmd = [
            self.settings.ffmpeg_path,
            "-i", str(input_path),
            "-vf", vf_filter,
            "-pix_fmt", "yuva420p",
            "-c:v", "libvpx-vp9",
            "-auto-alt-ref", "0",
            "-deadline", "realtime",
            "-cpu-used", "8",
            "-row-mt", "1",
            "-b:v", "1M",
            "-c:a", "libopus", "-b:a", "128k",
            "-map", "0:v",
            "-map", "0:a?",
            "-y",
            str(output_path),
        ]
        logger.info("Chroma key FFmpeg cmd: %s", " ".join(cmd))

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="ignore")
            # Log full stderr for debugging (split into chunks if needed)
            logger.error("Chroma key FFmpeg failed (rc=%d). stderr length=%d", process.returncode, len(error))
            # Log last 2000 chars for the actual error
            logger.error("FFmpeg stderr (last 2000): %s", error[-2000:])
            raise RuntimeError(f"FFmpeg chroma key processing failed: {error[-500:]}")

    async def render_preview_frames(
        self,
        *,
        input_url: str,
        output_dir: str,
        times_ms: list[int],
        clip_start_ms: int,
        in_point_ms: int,
        resolution: str,
        key_color: str,
        similarity: float,
        blend: float,
        background_color: str = "0x000000",
        skip_chroma_key: bool = False,
        return_transparent_png: bool = False,
    ) -> list[dict[str, Any]]:
        """Render chroma key preview frames directly from a signed URL.

        If skip_chroma_key is True, returns raw frames without chroma key processing.
        If return_transparent_png is True, returns PNG with transparency instead of
        compositing onto black background (for frontend compositing with other layers).
        """
        # Debug log: input parameters
        logger.info(
            "render_preview_frames called: times_ms=%s, clip_start_ms=%s, in_point_ms=%s, skip_chroma_key=%s",
            times_ms,
            clip_start_ms,
            in_point_ms,
            skip_chroma_key,
        )

        width, height = self._parse_resolution(resolution)
        color = key_color.replace("#", "0x")
        # FFmpeg colorkey requires similarity in range [0.00001 - 1], 0 is not accepted
        similarity = max(0.00001, similarity)
        frames: list[dict[str, Any]] = []

        # Always use solid black background (no checkerboard)
        for time_ms in times_ms:
            relative_ms = max(0, time_ms - clip_start_ms)
            seek_ms = max(0, in_point_ms + relative_ms)
            seek_s = seek_ms / 1000.0
            output_path = os.path.join(output_dir, f"frame_{time_ms}.jpg")

            # Debug log: calculated seek position
            logger.info(
                "render_preview_frames: time_ms=%s, relative_ms=%s, seek_ms=%s, seek_s=%.3f",
                time_ms,
                relative_ms,
                seek_ms,
                seek_s,
            )

            if skip_chroma_key:
                # Raw frame extraction without chroma key processing
                cmd = [
                    self.settings.ffmpeg_path,
                    "-y",
                    "-rw_timeout", "20000000",
                    "-ss", f"{seek_s:.3f}",
                    "-i", input_url,
                    "-vf", f"scale={width}:{height}",
                    "-frames:v", "1",
                    "-q:v", "5",
                    output_path,
                ]
            else:
                # Chroma key processing
                # 1. FFmpeg: apply chromakey + despill and output PNG with transparency
                # 2. Pillow: composite onto black background and save as JPEG
                output_png = output_path.replace(".jpg", ".png")
                despill_type = self._get_despill_type(key_color)
                # Apply chromakey + despill for edge refinement (removes color spill/fringing)
                vf_filter = (
                    f"scale={width}:{height},"
                    f"chromakey={color}:{similarity}:{blend},"
                    f"despill=type={despill_type},"
                    f"format=yuva420p"
                )
                cmd = [
                    self.settings.ffmpeg_path,
                    "-y",
                    "-rw_timeout", "20000000",
                    "-ss", f"{seek_s:.3f}",
                    "-i", input_url,
                    "-vf", vf_filter,
                    "-frames:v", "1",
                    output_png,
                ]

            result = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True
            )
            if result.returncode != 0:
                stderr_full = result.stderr or "(no stderr)"
                stdout_full = result.stdout or "(no stdout)"
                # Log command without URL (privacy/length)
                cmd_safe = [arg if not arg.startswith("http") else "<URL>" for arg in cmd]
                logger.error(
                    "FFmpeg chroma key preview FAILED at time_ms=%s returncode=%s",
                    time_ms,
                    result.returncode,
                )
                logger.error("FFmpeg command: %s", " ".join(cmd_safe))
                logger.error("FFmpeg stderr (full):\n%s", stderr_full)
                logger.error("FFmpeg stdout (full):\n%s", stdout_full)
                raise RuntimeError(
                    f"FFmpeg chroma key preview failed at {time_ms}ms. "
                    f"returncode={result.returncode}. stderr={stderr_full[:2000]}"
                )

            # For chroma key: either return transparent PNG or composite onto black background
            if not skip_chroma_key:
                from PIL import Image
                import io
                # Read PNG with transparency
                fg_img = Image.open(output_png).convert("RGBA")

                if return_transparent_png:
                    # Return PNG with transparency for frontend compositing
                    png_buffer = io.BytesIO()
                    fg_img.save(png_buffer, format="PNG", optimize=True)
                    frame_data = png_buffer.getvalue()
                    image_format = "png"
                else:
                    # Create black background
                    bg_img = Image.new("RGBA", fg_img.size, (0, 0, 0, 255))
                    # Composite foreground onto background
                    composited = Image.alpha_composite(bg_img, fg_img)
                    # Convert to RGB (no alpha) and save as JPEG
                    rgb_img = composited.convert("RGB")
                    jpeg_buffer = io.BytesIO()
                    rgb_img.save(jpeg_buffer, format="JPEG", quality=85)
                    frame_data = jpeg_buffer.getvalue()
                    image_format = "jpeg"
            else:
                with open(output_path, "rb") as handle:
                    frame_data = handle.read()
                image_format = "jpeg"
            frames.append(
                {
                    "time_ms": time_ms,
                    "resolution": f"{width}x{height}",
                    "frame_base64": base64.b64encode(frame_data).decode("utf-8"),
                    "size_bytes": len(frame_data),
                    "skip_chroma_key": skip_chroma_key,
                    "image_format": image_format,
                }
            )

        return frames

    def _parse_resolution(self, resolution: str) -> tuple[int, int]:
        try:
            width_str, height_str = resolution.lower().split("x", 1)
            width = int(width_str)
            height = int(height_str)
            if width <= 0 or height <= 0:
                raise ValueError
            return width, height
        except Exception:
            return (640, 360)

    def _write_checkerboard(
        self,
        path: str,
        width: int,
        height: int,
        tile_size: int = 32,
    ) -> None:
        from PIL import Image, ImageDraw

        color_a = (42, 42, 42)
        color_b = (58, 58, 58)
        img = Image.new("RGB", (width, height), color_a)
        draw = ImageDraw.Draw(img)

        for y in range(0, height, tile_size):
            for x in range(0, width, tile_size):
                if (x // tile_size + y // tile_size) % 2 == 1:
                    draw.rectangle(
                        [x, y, x + tile_size - 1, y + tile_size - 1],
                        fill=color_b,
                    )
        img.save(path, format="PNG")
