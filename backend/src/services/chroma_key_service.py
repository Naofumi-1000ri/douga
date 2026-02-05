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

    def resolve_key_color(self, input_path: str, key_color: str) -> str:
        """Resolve key color from input or auto-sampling."""
        if key_color.lower() != "auto":
            return key_color

        detected = sample_chroma_key_color(input_path)
        if not detected:
            raise RuntimeError("Auto chroma key detection failed")
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
        cmd = [
            self.settings.ffmpeg_path,
            "-i", str(input_path),
            "-vf", f"colorkey={color}:{similarity}:{blend}",
            "-c:v", "libvpx-vp9",
            "-pix_fmt", "yuva420p",
            "-auto-alt-ref", "0",
            "-crf", "28",
            "-b:v", "0",
            "-c:a", "copy",
            "-map", "0:v",
            "-map", "0:a?",
            "-y",
            str(output_path),
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()

        if process.returncode != 0:
            error = stderr.decode("utf-8", errors="ignore")
            logger.error("Chroma key processing failed: %s", error[:500])
            raise RuntimeError("FFmpeg chroma key processing failed")

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
        background_color: str = "0x2a2a2a",
    ) -> list[dict[str, Any]]:
        """Render chroma key preview frames directly from a signed URL."""
        width, height = self._parse_resolution(resolution)
        color = key_color.replace("#", "0x")
        frames: list[dict[str, Any]] = []

        for time_ms in times_ms:
            relative_ms = max(0, time_ms - clip_start_ms)
            seek_ms = max(0, in_point_ms + relative_ms)
            seek_s = seek_ms / 1000.0
            output_path = os.path.join(output_dir, f"frame_{time_ms}.jpg")

            cmd = [
                self.settings.ffmpeg_path,
                "-y",
                "-rw_timeout", "20000000",
                "-ss", f"{seek_s:.3f}",
                "-i", input_url,
                "-f", "lavfi",
                "-i", f"color=c={background_color}:s={width}x{height}:r=1",
                "-filter_complex",
                (
                    f"[0:v]scale={width}:{height},"
                    f"colorkey={color}:{similarity}:{blend},"
                    f"format=rgba[fg];"
                    f"[1:v][fg]overlay=0:0:format=auto[out]"
                ),
                "-map", "[out]",
                "-frames:v", "1",
                "-q:v", "5",
                output_path,
            ]

            result = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True
            )
            if result.returncode != 0:
                logger.warning(
                    "Chroma key preview failed at %sms: %s",
                    time_ms,
                    (result.stderr or "")[:200],
                )
                raise RuntimeError("FFmpeg chroma key preview failed")

            with open(output_path, "rb") as handle:
                frame_data = handle.read()
            frames.append(
                {
                    "time_ms": time_ms,
                    "resolution": f"{width}x{height}",
                    "frame_base64": base64.b64encode(frame_data).decode("utf-8"),
                    "size_bytes": len(frame_data),
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
