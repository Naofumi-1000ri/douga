"""Frame sampling service for AI visual inspection.

Renders single frames from the timeline at specified times using FFmpeg.
Produces low-resolution JPEG images for quick AI analysis.
"""

import asyncio
import base64
import logging
import os
import subprocess
import tempfile
from typing import Any

from src.config import get_settings
from src.render.pipeline import RenderPipeline

logger = logging.getLogger(__name__)
settings = get_settings()


class FrameSampler:
    """Renders single frames from a timeline for AI visual inspection."""

    def __init__(
        self,
        timeline_data: dict[str, Any],
        assets: dict[str, str],  # asset_id -> local file path
        project_width: int = 1920,
        project_height: int = 1080,
        project_fps: int = 30,
    ):
        self.timeline = timeline_data
        self.assets = assets
        self.project_width = project_width
        self.project_height = project_height
        self.project_fps = project_fps
        self.duration_ms = timeline_data.get("duration_ms", 0)

    async def sample_frame(
        self,
        time_ms: int,
        resolution: str = "640x360",
    ) -> dict[str, Any]:
        """Render a single frame at the specified time.

        Uses a two-step approach:
        1. Render a short segment (single frame) using the existing pipeline
        2. Extract the frame as JPEG

        Args:
            time_ms: Time position in milliseconds
            resolution: Output resolution (WxH string)

        Returns:
            Dict with frame_base64, time_ms, resolution, size_bytes
        """
        # Parse resolution
        width, height = self._parse_resolution(resolution)

        # Create temp directory for this sample
        temp_dir = tempfile.mkdtemp(prefix="douga_sample_")

        try:
            # Step 1: Build FFmpeg command for single-frame render
            frame_path = os.path.join(temp_dir, "frame.jpg")

            # Use the pipeline to build the filter_complex, then extract a single frame
            await self._render_single_frame(
                time_ms, width, height, frame_path, temp_dir
            )

            # Step 2: Read and encode the frame
            if os.path.exists(frame_path):
                with open(frame_path, "rb") as f:
                    frame_data = f.read()

                frame_base64 = base64.b64encode(frame_data).decode("utf-8")

                return {
                    "time_ms": time_ms,
                    "resolution": f"{width}x{height}",
                    "frame_base64": frame_base64,
                    "size_bytes": len(frame_data),
                }
            else:
                # Fallback: generate a blank frame
                return await self._blank_frame(time_ms, width, height, temp_dir)

        finally:
            # Cleanup temp directory
            import shutil
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

    async def _render_single_frame(
        self,
        time_ms: int,
        width: int,
        height: int,
        output_path: str,
        temp_dir: str,
    ) -> None:
        """Render a single frame using FFmpeg.

        Strategy: Build the same filter_complex as the full render pipeline,
        but seek to the target time and extract only 1 frame.
        """
        layers = self.timeline.get("layers", [])
        duration_ms = self.duration_ms

        # Build inputs and filters identical to pipeline._composite_video
        inputs: list[str] = []
        filter_parts: list[str] = []
        input_idx = 0

        # Reverse layers (frontend top = FFmpeg bottom)
        sorted_layers = list(reversed(layers))

        # Base canvas
        duration_s = duration_ms / 1000
        inputs.extend([
            "-f", "lavfi",
            "-i", f"color=c=black:s={self.project_width}x{self.project_height}:r={self.project_fps}:d={duration_s}",
        ])
        current_output = f"{input_idx}:v"
        input_idx += 1

        shape_idx = 0
        has_clips = False

        for layer in sorted_layers:
            if not layer.get("visible", True):
                continue

            clips = layer.get("clips", [])
            if not clips:
                continue

            layer_type = layer.get("type", "content")

            for clip in clips:
                # Skip shape and text clips for sampling (would need Pillow generation)
                if clip.get("shape") or clip.get("text_content") is not None:
                    # For shapes/text, generate simple PNG
                    png_path = self._generate_simple_overlay(clip, shape_idx, temp_dir)
                    if png_path:
                        inputs.extend(["-i", png_path])
                        transform = clip.get("transform", {})
                        center_x = transform.get("x", 0)
                        center_y = transform.get("y", 0)
                        start_ms = clip.get("start_ms", 0)
                        clip_duration = clip.get("duration_ms", 0)
                        start_s = start_ms / 1000
                        end_s = (start_ms + clip_duration) / 1000

                        overlay_x = f"(main_w/2)+({int(center_x)})-(overlay_w/2)"
                        overlay_y = f"(main_h/2)+({int(center_y)})-(overlay_h/2)"

                        output_label = f"smp_shape{shape_idx}"
                        filter_parts.append(
                            f"[{current_output}][{input_idx}:v]overlay="
                            f"x={overlay_x}:y={overlay_y}:"
                            f"enable='between(t,{start_s},{end_s})'"
                            f"[{output_label}]"
                        )
                        current_output = output_label
                        input_idx += 1
                        shape_idx += 1
                        has_clips = True
                    continue

                asset_id = str(clip.get("asset_id", ""))
                if not asset_id or asset_id not in self.assets:
                    continue

                asset_path = self.assets[asset_id]
                inputs.extend(["-i", asset_path])

                # Build clip filter (simplified version of pipeline._build_clip_filter)
                clip_filter = self._build_sample_clip_filter(
                    input_idx, clip, layer_type, current_output
                )
                filter_parts.append(clip_filter)
                current_output = f"smp{input_idx}"
                input_idx += 1
                has_clips = True

        if not has_clips:
            # No clips - generate blank frame
            cmd = [
                settings.ffmpeg_path, "-y",
                "-f", "lavfi",
                "-i", f"color=c=black:s={width}x{height}:r=1:d=0.1",
                "-frames:v", "1",
                "-q:v", "5",
                output_path,
            ]
            await asyncio.to_thread(subprocess.run, cmd, capture_output=True)
            return

        # Final scale to target resolution
        filter_parts.append(f"[{current_output}]scale={width}:{height}[smp_out]")

        filter_complex = ";\n".join(filter_parts)

        # Seek to target time and extract 1 frame
        seek_s = time_ms / 1000

        cmd = [
            settings.ffmpeg_path, "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[smp_out]",
            "-ss", str(seek_s),
            "-frames:v", "1",
            "-q:v", "5",  # JPEG quality (lower = better, 2-31)
            output_path,
        ]

        result = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.warning(f"Frame sampling failed at {time_ms}ms: {result.stderr[:200]}")
            # Fallback: blank frame
            cmd = [
                settings.ffmpeg_path, "-y",
                "-f", "lavfi",
                "-i", f"color=c=black:s={width}x{height}:r=1:d=0.1",
                "-frames:v", "1",
                "-q:v", "5",
                output_path,
            ]
            await asyncio.to_thread(subprocess.run, cmd, capture_output=True)

    def _build_sample_clip_filter(
        self,
        input_idx: int,
        clip: dict[str, Any],
        layer_type: str,
        base_output: str,
    ) -> str:
        """Build FFmpeg filter for a single clip (sampling version)."""
        transform = clip.get("transform", {})
        effects = clip.get("effects", {})
        output_label = f"smp{input_idx}"

        clip_filters: list[str] = []

        # Trim
        in_point_ms = clip.get("in_point_ms", 0)
        out_point_ms = clip.get("out_point_ms")
        duration_ms = clip.get("duration_ms", 0)
        start_ms = clip.get("start_ms", 0)

        if duration_ms <= 0:
            if out_point_ms is not None and out_point_ms > in_point_ms:
                duration_ms = out_point_ms - in_point_ms
            else:
                return f"[{base_output}]null[{output_label}]"

        if out_point_ms is None:
            out_point_ms = in_point_ms + duration_ms

        start_s = in_point_ms / 1000
        end_s = out_point_ms / 1000
        clip_filters.append(f"trim=start={start_s}:end={end_s}")
        clip_filters.append("setpts=PTS-STARTPTS")

        # Scale
        x = transform.get("x", 0)
        y = transform.get("y", 0)
        scale = transform.get("scale", 1.0)
        w = transform.get("width")
        h = transform.get("height")

        if w and h:
            clip_filters.append(f"scale={int(w * scale)}:{int(h * scale)}")
        elif scale != 1.0:
            clip_filters.append(f"scale=iw*{scale}:ih*{scale}")

        # Chroma key
        chroma_key = effects.get("chroma_key", {})
        if chroma_key.get("enabled", False):
            color = chroma_key.get("color", "#00FF00").replace("#", "0x")
            similarity = chroma_key.get("similarity", 0.3)
            blend = chroma_key.get("blend", 0.1)
            clip_filters.append(f"colorkey={color}:{similarity}:{blend}")

        # Opacity
        opacity = effects.get("opacity", 1.0)
        if opacity < 1.0:
            clip_filters.append(f"format=rgba,colorchannelmixer=aa={opacity}")

        # Build filter string
        if clip_filters:
            filter_str = f"[{input_idx}:v]" + ",".join(clip_filters) + f"[smp_clip{input_idx}];\n"
            clip_ref = f"smp_clip{input_idx}"
        else:
            filter_str = ""
            clip_ref = f"{input_idx}:v"

        # Overlay
        overlay_x = f"(main_w/2)+({int(x)})-(overlay_w/2)"
        overlay_y = f"(main_h/2)+({int(y)})-(overlay_h/2)"
        start_time = start_ms / 1000
        end_time = (start_ms + duration_ms) / 1000

        filter_str += (
            f"[{base_output}][{clip_ref}]overlay="
            f"x={overlay_x}:y={overlay_y}:"
            f"enable='between(t,{start_time},{end_time})'"
            f"[{output_label}]"
        )

        return filter_str

    def _generate_simple_overlay(
        self,
        clip: dict[str, Any],
        idx: int,
        temp_dir: str,
    ) -> str | None:
        """Generate a simple PNG overlay for shape/text clips during sampling."""
        try:
            from PIL import Image, ImageDraw, ImageFont

            transform = clip.get("transform", {})
            width = int(transform.get("width", 100))
            height = int(transform.get("height", 50))
            width = max(width, 1)
            height = max(height, 1)

            img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            shape = clip.get("shape")
            if shape:
                fill_color = shape.get("fillColor", "#ffffff")
                filled = shape.get("filled", True)
                stroke_color = shape.get("strokeColor", "#000000")
                stroke_width = int(shape.get("strokeWidth", 2))

                hex_color = fill_color.lstrip("#")
                if len(hex_color) == 3:
                    hex_color = "".join([c * 2 for c in hex_color])
                r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)

                if shape.get("type") == "circle":
                    if filled:
                        draw.ellipse([(0, 0), (width - 1, height - 1)], fill=(r, g, b, 255))
                    else:
                        draw.ellipse([(0, 0), (width - 1, height - 1)], outline=(r, g, b, 255), width=stroke_width)
                else:
                    if filled:
                        draw.rectangle([(0, 0), (width - 1, height - 1)], fill=(r, g, b, 255))
                    else:
                        draw.rectangle([(0, 0), (width - 1, height - 1)], outline=(r, g, b, 255), width=stroke_width)

            elif clip.get("text_content") is not None:
                text = clip["text_content"]
                text_style = clip.get("text_style", {})
                color = text_style.get("color", "#ffffff")
                hex_color = color.lstrip("#")
                if len(hex_color) == 3:
                    hex_color = "".join([c * 2 for c in hex_color])
                r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)

                font_size = int(text_style.get("fontSize", 24))
                try:
                    font = ImageFont.truetype(
                        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc", font_size
                    )
                except Exception:
                    font = ImageFont.load_default()

                draw.text((4, 4), text, font=font, fill=(r, g, b, 255))

            output_path = os.path.join(temp_dir, f"smp_overlay_{idx}.png")
            img.save(output_path, "PNG")
            return output_path

        except Exception as e:
            logger.warning(f"Failed to generate sample overlay: {e}")
            return None

    async def _blank_frame(
        self,
        time_ms: int,
        width: int,
        height: int,
        temp_dir: str,
    ) -> dict[str, Any]:
        """Generate a blank black frame."""
        frame_path = os.path.join(temp_dir, "blank.jpg")
        cmd = [
            settings.ffmpeg_path, "-y",
            "-f", "lavfi",
            "-i", f"color=c=black:s={width}x{height}:r=1:d=0.1",
            "-frames:v", "1",
            "-q:v", "5",
            frame_path,
        ]
        await asyncio.to_thread(subprocess.run, cmd, capture_output=True)

        with open(frame_path, "rb") as f:
            frame_data = f.read()

        return {
            "time_ms": time_ms,
            "resolution": f"{width}x{height}",
            "frame_base64": base64.b64encode(frame_data).decode("utf-8"),
            "size_bytes": len(frame_data),
        }

    @staticmethod
    def _parse_resolution(resolution: str) -> tuple[int, int]:
        """Parse resolution string 'WxH' into (width, height)."""
        parts = resolution.lower().split("x")
        if len(parts) != 2:
            return 640, 360
        try:
            w = int(parts[0])
            h = int(parts[1])
            # Ensure even dimensions for FFmpeg
            w = w if w % 2 == 0 else w + 1
            h = h if h % 2 == 0 else h + 1
            return w, h
        except ValueError:
            return 640, 360
