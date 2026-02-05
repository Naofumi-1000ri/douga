"""Chroma key processing helpers."""

import asyncio
import logging

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
