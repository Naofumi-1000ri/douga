"""
Main render pipeline for video compositing.

This module orchestrates the entire rendering process:
1. Parse timeline data
2. Download assets from GCS
3. Process audio (mixing, ducking)
4. Composite video layers
5. Encode final output
6. Upload to GCS
"""

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import UUID, uuid4

from PIL import Image, ImageDraw, ImageFont

from src.config import get_settings
from src.render.audio_mixer import AudioClipData, AudioMixer, AudioTrackData, VolumeKeyframeData

logger = logging.getLogger(__name__)

settings = get_settings()


# ============================================================================
# Enums
# ============================================================================


class RenderStatus(Enum):
    """Render job status."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ============================================================================
# Dataclasses
# ============================================================================


@dataclass
class RenderProgress:
    """Progress information for a render job."""

    job_id: str
    status: RenderStatus
    percent: float = 0.0
    current_step: Optional[str] = None
    elapsed_ms: int = 0
    error_message: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "percent": self.percent,
            "current_step": self.current_step,
            "elapsed_ms": self.elapsed_ms,
            "error_message": self.error_message,
        }


@dataclass
class RenderConfig:
    """Configuration for video rendering."""

    width: int = 1920
    height: int = 1080
    fps: int = 30
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    crf: int = 18
    preset: str = "medium"
    audio_bitrate: str = "192k"


@dataclass
class RenderJob:
    """Render job information."""

    id: str
    project_id: str
    status: RenderStatus
    config: Optional[RenderConfig] = None
    output_path: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "status": self.status.value,
            "output_path": self.output_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
        }


@dataclass
class TimelineData:
    """Timeline data for rendering."""

    project_id: str
    duration_ms: int
    layers: list[dict[str, Any]] = field(default_factory=list)
    audio_tracks: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "project_id": self.project_id,
            "duration_ms": self.duration_ms,
            "layers": self.layers,
            "audio_tracks": self.audio_tracks,
        }


@dataclass
class UndoableAction:
    """Represents an action that can be undone/redone."""

    id: str
    action_type: str
    description: str
    data: dict[str, Any]
    reverse_data: dict[str, Any]
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ============================================================================
# Undo Manager
# ============================================================================


class UndoManager:
    """Manages undo/redo history for timeline editing."""

    def __init__(self, max_history: int = 50):
        self.max_history = max_history
        self._undo_stack: deque[UndoableAction] = deque(maxlen=max_history)
        self._redo_stack: list[UndoableAction] = []

    def execute(self, action: UndoableAction) -> None:
        """Execute an action and add to undo stack."""
        self._undo_stack.append(action)
        self._redo_stack.clear()

    def undo(self) -> Optional[UndoableAction]:
        """Undo the last action."""
        if not self._undo_stack:
            return None
        action = self._undo_stack.pop()
        self._redo_stack.append(action)
        return action

    def redo(self) -> Optional[UndoableAction]:
        """Redo the last undone action."""
        if not self._redo_stack:
            return None
        action = self._redo_stack.pop()
        self._undo_stack.append(action)
        return action

    def can_undo(self) -> bool:
        """Check if undo is available."""
        return len(self._undo_stack) > 0

    def can_redo(self) -> bool:
        """Check if redo is available."""
        return len(self._redo_stack) > 0

    def get_undo_description(self) -> Optional[str]:
        """Get description of the next undo action."""
        if not self._undo_stack:
            return None
        return self._undo_stack[-1].description

    def get_redo_description(self) -> Optional[str]:
        """Get description of the next redo action."""
        if not self._redo_stack:
            return None
        return self._redo_stack[-1].description

    def clear(self) -> None:
        """Clear all history."""
        self._undo_stack.clear()
        self._redo_stack.clear()


class RenderPipeline:
    """
    Main render pipeline for compositing Udemy course videos.

    Handles:
    - 5-layer video compositing
    - Chroma key processing
    - Text overlays
    - Audio mixing with BGM ducking
    - Final encoding to H.264/AAC
    """

    def __init__(
        self,
        job_id: Optional[str] = None,
        project_id: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        fps: Optional[int] = None,
    ):
        self.job_id = job_id
        self.project_id = project_id
        # Use project dimensions if provided, otherwise fall back to settings
        self.width = width or settings.render_output_width
        self.height = height or settings.render_output_height
        self.fps = fps or settings.render_fps

        # Job management storage
        self._jobs: dict[str, RenderJob] = {}
        self._timelines: dict[str, TimelineData] = {}
        self._progress: dict[str, RenderProgress] = {}
        self._progress_callbacks: dict[str, Callable[[RenderProgress], None]] = {}

        # Work directories (only created if job_id provided)
        if job_id:
            self.work_dir = tempfile.mkdtemp(prefix=f"douga_render_{job_id}_")
            self.assets_dir = os.path.join(self.work_dir, "assets")
            self.output_dir = os.path.join(self.work_dir, "output")
            os.makedirs(self.assets_dir, exist_ok=True)
            os.makedirs(self.output_dir, exist_ok=True)
            self.audio_mixer = AudioMixer(self.output_dir)
        else:
            self.work_dir = ""
            self.assets_dir = ""
            self.output_dir = ""
            self.audio_mixer = None

        self.ffmpeg_path = settings.ffmpeg_path
        self._progress_callback: Any = None
        self._cancel_check: Optional[Callable[[], Any]] = None

    def set_progress_callback(self, callback: Any) -> None:
        """Set callback for progress updates."""
        self._progress_callback = callback

    def _update_progress(self, progress: int, stage: str) -> None:
        """Update render progress."""
        if self._progress_callback:
            self._progress_callback(progress, stage)

    async def render(
        self,
        timeline_data: dict[str, Any],
        assets: dict[str, str],  # asset_id -> local file path
        output_path: str,
        cancel_check: Optional[Callable[[], Any]] = None,
    ) -> str:
        """
        Execute the full render pipeline.

        Args:
            timeline_data: Project timeline data
            assets: Map of asset IDs to local file paths
            output_path: Output video file path
            cancel_check: Optional async callable that returns True if cancelled

        Returns:
            Path to rendered video

        Raises:
            asyncio.CancelledError: If render was cancelled
        """
        self._cancel_check = cancel_check

        self._update_progress(5, "Preparing render")

        duration_ms = timeline_data.get("duration_ms", 0)
        if duration_ms <= 0:
            raise ValueError("Timeline duration must be greater than 0")

        # Check for cancellation
        if await self._is_cancelled():
            raise asyncio.CancelledError("Render cancelled")

        # Step 1: Mix audio
        self._update_progress(10, "Mixing audio")
        audio_path = await self._mix_audio(timeline_data, assets, duration_ms)

        if await self._is_cancelled():
            raise asyncio.CancelledError("Render cancelled")

        # Step 2: Composite video layers
        self._update_progress(30, "Compositing video")
        video_path = await self._composite_video(timeline_data, assets, duration_ms)

        if await self._is_cancelled():
            raise asyncio.CancelledError("Render cancelled")

        # Step 3: Combine audio and video
        self._update_progress(80, "Encoding final video")
        await self._encode_final(video_path, audio_path, output_path, duration_ms)

        # Step 4: Cleanup
        self._update_progress(95, "Cleaning up")
        self._cleanup()

        self._update_progress(100, "Complete")
        return output_path

    async def _is_cancelled(self) -> bool:
        """Check if render has been cancelled."""
        if self._cancel_check is None:
            return False
        result = self._cancel_check()
        if asyncio.iscoroutine(result):
            return await result
        return result

    async def _mix_audio(
        self,
        timeline_data: dict[str, Any],
        assets: dict[str, str],
        duration_ms: int,
    ) -> str:
        """Mix all audio tracks."""
        audio_tracks = timeline_data.get("audio_tracks", [])
        print(f"[AUDIO MIX] Found {len(audio_tracks)} audio tracks, available assets: {list(assets.keys())}", flush=True)
        tracks: list[AudioTrackData] = []

        for track_data in audio_tracks:
            track_type = track_data.get("type", "unknown")
            track_clips = track_data.get("clips", [])
            print(f"[AUDIO MIX] Track '{track_type}': {len(track_clips)} clips, muted={track_data.get('muted', False)}", flush=True)

            # Skip muted tracks
            if track_data.get("muted", False):
                continue

            clips: list[AudioClipData] = []

            for clip_data in track_data.get("clips", []):
                asset_id = clip_data.get("asset_id")
                print(f"[AUDIO MIX] Clip asset_id={asset_id}, in_assets={asset_id in assets if asset_id else 'N/A'}", flush=True)
                if asset_id and asset_id in assets:
                    # Parse volume keyframes if present
                    volume_keyframes = None
                    raw_keyframes = clip_data.get("volume_keyframes")
                    if raw_keyframes:
                        volume_keyframes = [
                            VolumeKeyframeData(
                                time_ms=kf.get("time_ms", 0),
                                value=kf.get("value", 1.0),
                            )
                            for kf in raw_keyframes
                        ]

                    clips.append(
                        AudioClipData(
                            file_path=assets[asset_id],
                            start_ms=clip_data.get("start_ms", 0),
                            duration_ms=clip_data.get("duration_ms", 0),
                            in_point_ms=clip_data.get("in_point_ms", 0),
                            out_point_ms=clip_data.get("out_point_ms"),
                            volume=clip_data.get("volume", 1.0),
                            fade_in_ms=clip_data.get("fade_in_ms", 0),
                            fade_out_ms=clip_data.get("fade_out_ms", 0),
                            speed=clip_data.get("speed", 1.0),
                            volume_keyframes=volume_keyframes,
                        )
                    )

            ducking = track_data.get("ducking", {})
            tracks.append(
                AudioTrackData(
                    track_type=track_data.get("type", "se"),
                    volume=track_data.get("volume", 1.0),
                    clips=clips if clips else None,
                    ducking_enabled=ducking.get("enabled", False),
                    duck_to=ducking.get("duck_to", 0.1),
                    attack_ms=ducking.get("attack_ms", 200),
                    release_ms=ducking.get("release_ms", 500),
                )
            )

        output_path = os.path.join(self.output_dir, "mixed_audio.aac")
        # Use asyncio.to_thread to avoid blocking the event loop
        return await asyncio.to_thread(self.audio_mixer.mix_tracks, tracks, output_path, duration_ms)

    async def _composite_video(
        self,
        timeline_data: dict[str, Any],
        assets: dict[str, str],
        duration_ms: int,
    ) -> str:
        """
        Composite all video layers.

        Layer order (bottom to top):
        1. Background
        2. Content (slides, screen capture)
        3. Avatar (with chroma key)
        4. Effects (particles, etc.)
        5. Text overlays
        """
        layers = timeline_data.get("layers", [])
        output_path = os.path.join(self.output_dir, "composite.mp4")

        # Debug: log available assets
        logger.info(f"[RENDER DEBUG] Available assets: {list(assets.keys())}")
        logger.info(f"[RENDER DEBUG] Total layers in timeline: {len(layers)}")

        # For MVP, create a simple black background if no layers
        if not layers or all(not layer.get("clips") for layer in layers):
            return await self._create_blank_video(output_path, duration_ms)

        # Build FFmpeg filter complex for layer compositing
        inputs = []
        filter_parts = []
        input_idx = 0

        # Reverse array order for FFmpeg overlay
        # Frontend: array index 0 = TOP of layer list = should appear ON TOP in video
        # FFmpeg: first overlay = bottom, last overlay = top
        # So we need to reverse: process last array element first (bottom), first element last (top)
        sorted_layers = list(reversed(layers))

        # Create base canvas (black background)
        width = self.width
        height = self.height
        fps = self.fps
        duration_s = duration_ms / 1000

        # Add color source as base
        inputs.extend([
            "-f", "lavfi",
            "-i", f"color=c=black:s={width}x{height}:r={fps}:d={duration_s}"
        ])
        current_output = f"{input_idx}:v"
        input_idx += 1

        # Process each layer
        for layer in sorted_layers:
            layer_id = layer.get("id", "unknown")
            layer_name = layer.get("name", "unknown")
            logger.info(f"[RENDER DEBUG] Processing layer: {layer_name} (id={layer_id}, order={layer.get('order')})")

            if not layer.get("visible", True):
                logger.info(f"[RENDER DEBUG] Layer {layer_name} is not visible, skipping")
                continue

            clips = layer.get("clips", [])
            if not clips:
                logger.info(f"[RENDER DEBUG] Layer {layer_name} has no clips, skipping")
                continue

            layer_type = layer.get("type", "content")
            logger.info(f"[RENDER DEBUG] Layer {layer_name} has {len(clips)} clips, type={layer_type}")

            # Track shape index for unique filter labels
            shape_idx = 0

            for clip in clips:
                # Check for shape clips (no asset_id, but has shape property)
                shape = clip.get("shape")
                if shape:
                    logger.info(f"[RENDER DEBUG] Processing shape clip: type={shape.get('type')}, start_ms={clip.get('start_ms')}")
                    # Generate shape PNG using Pillow
                    shape_path = self._generate_shape_image(shape, clip, shape_idx)
                    if shape_path:
                        # Add PNG as FFmpeg input
                        inputs.extend(["-i", shape_path])

                        # Build overlay filter for the shape
                        shape_filter = self._build_shape_overlay_filter(
                            input_idx, clip, current_output, shape_idx
                        )
                        filter_parts.append(shape_filter)
                        current_output = f"shape{shape_idx}"
                        input_idx += 1
                        shape_idx += 1
                        logger.info(f"[RENDER DEBUG] Shape overlay filter added: {shape_filter}")
                    continue

                # Check for text clips (telops)
                text_content = clip.get("text_content")
                if text_content is not None:
                    logger.info(f"[RENDER DEBUG] Processing text clip: '{text_content[:30]}...' start_ms={clip.get('start_ms')}")
                    # Generate text PNG using Pillow
                    text_path = self._generate_text_image(clip, shape_idx)
                    if text_path:
                        # Add PNG as FFmpeg input
                        inputs.extend(["-i", text_path])

                        # Build overlay filter for the text
                        text_filter = self._build_text_overlay_filter(
                            input_idx, clip, current_output, shape_idx
                        )
                        filter_parts.append(text_filter)
                        current_output = f"text{shape_idx}"
                        input_idx += 1
                        shape_idx += 1
                        logger.info(f"[RENDER DEBUG] Text overlay filter added: {text_filter}")
                    continue

                asset_id = str(clip.get("asset_id", ""))
                if not asset_id or asset_id not in assets:
                    logger.info(f"[RENDER DEBUG] Clip asset_id={asset_id[:8] if asset_id else 'None'} not in assets, skipping")
                    continue

                clip_start = clip.get('start_ms', 0)
                clip_duration = clip.get('duration_ms', 0)
                clip_end = clip_start + clip_duration
                print(f"[RENDER DEBUG] Adding clip: asset_id={asset_id[:8]}, start_ms={clip_start}, duration_ms={clip_duration}, end_ms={clip_end}", flush=True)
                asset_path = assets[asset_id]
                inputs.extend(["-i", asset_path])

                # Build clip filter
                clip_filter = self._build_clip_filter(
                    input_idx,
                    clip,
                    layer_type,
                    current_output,
                    duration_ms,
                )
                filter_parts.append(clip_filter)
                print(f"[RENDER DEBUG] Clip overlay enable: start={clip_start/1000}s, end={clip_end/1000}s", flush=True)

                current_output = f"layer{input_idx}"
                input_idx += 1

        if not filter_parts:
            return await self._create_blank_video(output_path, duration_ms)

        # Add explicit final output rename (using null filter to avoid string replacement issues)
        filter_parts.append(f"[{current_output}]null[vout]")
        filter_complex = ";\n".join(filter_parts)

        # Debug logging
        logger.info(f"[RENDER DEBUG] Number of inputs: {len(inputs) // 2}")
        logger.info(f"[RENDER DEBUG] filter_complex:\n{filter_complex}")

        # Build FFmpeg command
        cmd = [
            self.ffmpeg_path,
            "-y",
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-r", str(fps),
            "-pix_fmt", "yuv420p",
            "-t", str(duration_s),
            output_path,
        ]

        print(f"[RENDER DEBUG] FFmpeg composite command (duration_s={duration_s}):", flush=True)
        print(f"[RENDER DEBUG] -t {duration_s}", flush=True)
        # Use asyncio.to_thread to avoid blocking the event loop
        result = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True)
        print(f"[RENDER DEBUG] FFmpeg returncode: {result.returncode}", flush=True)
        if result.returncode != 0:
            logger.error(f"[RENDER DEBUG] FFmpeg stderr: {result.stderr}")
            # Fallback to blank video on error
            return await self._create_blank_video(output_path, duration_ms)

        return output_path

    def _build_clip_filter(
        self,
        input_idx: int,
        clip: dict[str, Any],
        layer_type: str,
        base_output: str,
        total_duration_ms: int,
    ) -> str:
        """Build FFmpeg filter for a single clip."""
        transform = clip.get("transform", {})
        effects = clip.get("effects", {})

        clip_filters = []
        output_label = f"layer{input_idx}"

        # Get clip timing values
        in_point_ms = clip.get("in_point_ms", 0)
        out_point_ms = clip.get("out_point_ms")
        duration_ms = clip.get("duration_ms", 0)
        start_ms = clip.get("start_ms", 0)

        # Validate and calculate duration_ms if needed
        if duration_ms <= 0:
            # Try to calculate from out_point_ms
            if out_point_ms is not None and out_point_ms > in_point_ms:
                duration_ms = out_point_ms - in_point_ms
                logger.warning(f"[CLIP] duration_ms was 0, calculated from out_point: {duration_ms}")
            else:
                logger.error(f"[CLIP] Cannot determine duration for clip (duration_ms={duration_ms}, out_point_ms={out_point_ms})")
                # Return a null filter that passes through without this clip
                return f"[{base_output}]null[{output_label}]"

        # Calculate actual out point for trimming
        if out_point_ms is None:
            out_point_ms = in_point_ms + duration_ms

        logger.info(f"[CLIP DEBUG] in_point={in_point_ms}ms, out_point={out_point_ms}ms, duration={duration_ms}ms, start={start_ms}ms")

        # Apply trim filter to extract the portion of source we need
        start_s = in_point_ms / 1000
        end_s = out_point_ms / 1000
        speed = clip.get("speed", 1.0)
        clip_filters.append(f"trim=start={start_s}:end={end_s}")
        if speed != 1.0:
            clip_filters.append(f"setpts=(PTS-STARTPTS)/{speed}")
        else:
            clip_filters.append("setpts=PTS-STARTPTS")

        # Click highlights (drawbox overlays using normalized coordinates)
        highlights = clip.get("highlights", [])
        for hl in highlights:
            hl_x_norm = hl.get("x_norm", 0)
            hl_y_norm = hl.get("y_norm", 0)
            hl_w_norm = hl.get("w_norm", 0.1)
            hl_h_norm = hl.get("h_norm", 0.08)
            hl_time_s = hl.get("time_ms", 0) / 1000
            hl_dur_s = hl.get("duration_ms", 1500) / 1000
            hl_color = hl.get("color", "FF6600").replace("#", "")
            hl_thickness = hl.get("thickness", 4)
            # Pad the bounding box by 20% for visual clarity
            pad_w = hl_w_norm * 0.2
            pad_h = hl_h_norm * 0.2
            box_x = f"iw*{max(0, hl_x_norm - (hl_w_norm + pad_w) / 2):.4f}"
            box_y = f"ih*{max(0, hl_y_norm - (hl_h_norm + pad_h) / 2):.4f}"
            box_w = f"iw*{hl_w_norm + pad_w:.4f}"
            box_h = f"ih*{hl_h_norm + pad_h:.4f}"
            end_s_hl = hl_time_s + hl_dur_s
            clip_filters.append(
                f"drawbox=x='{box_x}':y='{box_y}':w='{box_w}':h='{box_h}'"
                f":color=0x{hl_color}@0.7:t={hl_thickness}"
                f":enable='between(t,{hl_time_s:.3f},{end_s_hl:.3f})'"
            )

        # Scale/position
        x = transform.get("x", 0)
        y = transform.get("y", 0)
        scale = transform.get("scale", 1.0)
        width = transform.get("width")
        height = transform.get("height")

        # Debug: Log transform data
        logger.info(f"[CLIP DEBUG] transform data: {transform}")

        if width and height:
            clip_filters.append(f"scale={int(width*scale)}:{int(height*scale)}")
        elif scale != 1.0:
            clip_filters.append(f"scale=iw*{scale}:ih*{scale}")

        # Chroma key (available for all layers with video content)
        chroma_key = effects.get("chroma_key", {})
        if chroma_key.get("enabled", False):
            color = chroma_key.get("color", "#00FF00").replace("#", "0x")
            similarity = chroma_key.get("similarity", 0.05)
            blend = chroma_key.get("blend", 0.0)
            clip_filters.append(f"colorkey={color}:{similarity}:{blend}")

        # Rotation
        rotation_raw = transform.get("rotation", 0)
        # Ensure rotation is a number (could be string from JSON or None)
        try:
            rotation = float(rotation_raw) if rotation_raw is not None else 0.0
        except (ValueError, TypeError):
            rotation = 0.0
        logger.info(f"[CLIP DEBUG] rotation value: {rotation} (raw: {rotation_raw})")
        if abs(rotation) > 0.01:  # Use threshold to avoid floating point issues
            # Convert to rgba format first (required for fillcolor=none to work)
            # Then apply rotation with expanded output size to prevent clipping
            # ow/oh use hypot(iw,ih) to ensure rotated content fits completely
            clip_filters.append("format=rgba")
            clip_filters.append(
                f"rotate={rotation}*PI/180:ow='hypot(iw,ih)':oh='hypot(iw,ih)':fillcolor=none"
            )
            logger.info(f"[CLIP DEBUG] Added rotation filter with expanded bounds")

        # Opacity
        opacity = effects.get("opacity", 1.0)
        if opacity < 1.0:
            clip_filters.append(f"format=rgba,colorchannelmixer=aa={opacity}")

        # Build the filter string
        if clip_filters:
            filter_str = f"[{input_idx}:v]" + ",".join(clip_filters) + f"[clip{input_idx}];\n"
            clip_ref = f"clip{input_idx}"
        else:
            filter_str = ""
            clip_ref = f"{input_idx}:v"

        # Overlay on base
        # x/y from frontend are CENTER offsets from canvas CENTER
        # FFmpeg overlay expects TOP-LEFT position
        # Convert: overlay_x = (canvas_w/2) + x - (overlay_w/2)
        #          overlay_y = (canvas_h/2) + y - (overlay_h/2)
        # Using FFmpeg expressions with main_w/main_h (base) and overlay_w/overlay_h
        overlay_x = f"(main_w/2)+({int(x)})-(overlay_w/2)"
        overlay_y = f"(main_h/2)+({int(y)})-(overlay_h/2)"
        # Use validated start_ms and duration_ms (already extracted and validated above)
        start_time = start_ms / 1000
        end_time = (start_ms + duration_ms) / 1000
        logger.info(f"[CLIP DEBUG] Overlay enable: between(t,{start_time},{end_time})")
        filter_str += f"[{base_output}][{clip_ref}]overlay=x={overlay_x}:y={overlay_y}:enable='between(t,{start_time},{end_time})'[{output_label}]"

        return filter_str

    def _generate_shape_image(
        self,
        shape: dict[str, Any],
        clip: dict[str, Any],
        shape_idx: int,
    ) -> str | None:
        """Generate transparent PNG for shape using Pillow.

        Args:
            shape: Shape properties (type, fillColor, strokeColor, etc.)
            clip: Clip data containing transform, effects
            shape_idx: Index for unique filename

        Returns:
            Path to generated PNG file, or None if failed
        """
        shape_type = shape.get("type", "rectangle")
        fill_color = shape.get("fillColor", "#ffffff")
        stroke_color = shape.get("strokeColor", "#000000")
        stroke_width = int(shape.get("strokeWidth", 2))
        filled = shape.get("filled", True)

        transform = clip.get("transform", {})
        effects = clip.get("effects", {})

        # Get dimensions
        width = int(transform.get("width") or shape.get("width", 100))
        height = int(transform.get("height") or shape.get("height", 100))

        # Ensure minimum size
        width = max(width, 1)
        height = max(height, 1)

        # For lines, we need to handle differently
        original_width = width
        original_height = height
        if shape_type == "line":
            # Line: width is length, height is stroke width
            # Create bounding box with padding for stroke
            height = max(stroke_width * 2, 4)

        # Create transparent image
        img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Parse hex color to RGBA tuple
        def hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
            hex_color = hex_color.lstrip('#')
            if len(hex_color) == 3:
                hex_color = ''.join([c*2 for c in hex_color])
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            return (r, g, b, alpha)

        # Apply opacity
        opacity = effects.get("opacity", 1.0)
        alpha = int(opacity * 255)

        fill_rgba = hex_to_rgba(fill_color, alpha) if filled else None
        stroke_rgba = hex_to_rgba(stroke_color, alpha)

        try:
            if shape_type == "rectangle":
                if filled:
                    draw.rectangle([(0, 0), (width-1, height-1)], fill=fill_rgba, outline=stroke_rgba, width=stroke_width)
                else:
                    # For outline only, offset to keep stroke inside bounds
                    offset = stroke_width // 2
                    draw.rectangle(
                        [(offset, offset), (width-1-offset, height-1-offset)],
                        fill=None, outline=stroke_rgba, width=stroke_width
                    )

            elif shape_type == "circle":
                if filled:
                    draw.ellipse([(0, 0), (width-1, height-1)], fill=fill_rgba, outline=stroke_rgba, width=stroke_width)
                else:
                    offset = stroke_width // 2
                    draw.ellipse(
                        [(offset, offset), (width-1-offset, height-1-offset)],
                        fill=None, outline=stroke_rgba, width=stroke_width
                    )

            elif shape_type == "line":
                # Draw horizontal line centered in the bounding box
                y_center = height // 2
                draw.line([(0, y_center), (width, y_center)], fill=stroke_rgba, width=stroke_width)

            else:
                logger.warning(f"[SHAPE] Unknown shape type: {shape_type}")
                return None

            # Apply rotation if specified
            # PIL rotates counter-clockwise, CSS rotates clockwise, so negate the angle
            rotation_raw = transform.get("rotation", 0)
            try:
                rotation = float(rotation_raw) if rotation_raw is not None else 0.0
            except (ValueError, TypeError):
                rotation = 0.0
            if abs(rotation) > 0.01:  # Use threshold to avoid floating point issues
                # expand=True adjusts canvas size to fit rotated image
                # fillcolor is transparent for RGBA
                img = img.rotate(-rotation, expand=True, fillcolor=(0, 0, 0, 0))
                logger.info(f"[SHAPE] Applied rotation: {rotation} degrees")

            # Save to temp file
            output_path = os.path.join(self.output_dir, f"shape_{shape_idx}.png")
            img.save(output_path, 'PNG')
            # Get final size after rotation
            final_width, final_height = img.size
            logger.info(f"[SHAPE] Generated PNG: {output_path} ({final_width}x{final_height}, type={shape_type}, rotation={rotation})")
            return output_path

        except Exception as e:
            logger.error(f"[SHAPE] Failed to generate shape image: {e}")
            return None

    def _build_shape_overlay_filter(
        self,
        input_idx: int,
        clip: dict[str, Any],
        base_output: str,
        shape_idx: int,
    ) -> str:
        """Build FFmpeg overlay filter for shape PNG.

        Args:
            input_idx: FFmpeg input index for the shape PNG
            clip: Clip data containing transform, timing
            base_output: Current filter graph output label
            shape_idx: Shape index for output label

        Returns:
            FFmpeg filter string
        """
        transform = clip.get("transform", {})

        # Get position (center offset from canvas center)
        center_x = transform.get("x", 0)
        center_y = transform.get("y", 0)

        # Get timing
        start_ms = clip.get("start_ms", 0)
        duration_ms = clip.get("duration_ms", 0)
        start_s = start_ms / 1000
        end_s = (start_ms + duration_ms) / 1000

        output_label = f"shape{shape_idx}"

        # Convert center coords to top-left for FFmpeg overlay
        # overlay_x = (canvas_w/2) + center_x - (overlay_w/2)
        # overlay_y = (canvas_h/2) + center_y - (overlay_h/2)
        overlay_x = f"(main_w/2)+({int(center_x)})-(overlay_w/2)"
        overlay_y = f"(main_h/2)+({int(center_y)})-(overlay_h/2)"

        filter_str = (
            f"[{base_output}][{input_idx}:v]overlay="
            f"x={overlay_x}:y={overlay_y}:"
            f"enable='between(t,{start_s},{end_s})'"
            f"[{output_label}]"
        )

        logger.info(f"[SHAPE] Overlay filter: input={input_idx}, pos=({center_x},{center_y}), time={start_s}-{end_s}s")
        return filter_str

    def _generate_text_image(
        self,
        clip: dict[str, Any],
        text_idx: int,
    ) -> str | None:
        """Generate transparent PNG for text (telop) using Pillow.

        Args:
            clip: Clip data containing text_content, text_style, transform, effects
            text_idx: Index for unique filename

        Returns:
            Path to generated PNG file, or None if failed
        """
        text_content = clip.get("text_content", "")
        if not text_content:
            return None

        text_style = clip.get("text_style", {})
        transform = clip.get("transform", {})
        effects = clip.get("effects", {})

        # Extract text style properties
        font_family = text_style.get("fontFamily", "Noto Sans JP")
        font_size = int(text_style.get("fontSize", 48))
        font_weight = text_style.get("fontWeight", "normal")
        font_style_prop = text_style.get("fontStyle", "normal")
        text_color = text_style.get("color", "#ffffff")
        bg_color = text_style.get("backgroundColor", "transparent")
        bg_opacity = float(text_style.get("backgroundOpacity", 1.0))
        stroke_color = text_style.get("strokeColor", "#000000")
        stroke_width = int(text_style.get("strokeWidth", 0))
        text_align = text_style.get("textAlign", "center")
        line_height = float(text_style.get("lineHeight", 1.4))

        # Try to find a suitable font file
        font_paths = {
            "Noto Sans JP": "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
            "Noto Serif JP": "/System/Library/Fonts/ヒラギノ明朝 ProN.ttc",
            "Kosugi Maru": "/System/Library/Fonts/ヒラギノ丸ゴ ProN W4.ttc",
            "default": "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        }
        # Use bold font if specified
        if font_weight == "bold":
            font_paths["Noto Sans JP"] = "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"

        font_path = font_paths.get(font_family, font_paths["default"])

        try:
            # Try to load the font
            try:
                font = ImageFont.truetype(font_path, font_size)
            except Exception:
                # Fallback to default font
                logger.warning(f"[TEXT] Could not load font: {font_path}, using default")
                font = ImageFont.load_default()

            # Parse colors
            def hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
                hex_color = hex_color.lstrip('#')
                if len(hex_color) == 3:
                    hex_color = ''.join([c*2 for c in hex_color])
                r = int(hex_color[0:2], 16)
                g = int(hex_color[2:4], 16)
                b = int(hex_color[4:6], 16)
                return (r, g, b, alpha)

            # Apply opacity
            opacity = effects.get("opacity", 1.0)
            alpha = int(opacity * 255)

            text_rgba = hex_to_rgba(text_color, alpha)
            stroke_rgba = hex_to_rgba(stroke_color, alpha) if stroke_width > 0 else None

            # Handle multi-line text
            lines = text_content.split('\n')

            # Calculate text dimensions
            max_width = 0
            total_height = 0
            line_heights = []

            for line in lines:
                bbox = font.getbbox(line or " ")  # Use space for empty lines
                line_width = bbox[2] - bbox[0]
                line_height_px = int(font_size * line_height)
                max_width = max(max_width, line_width)
                total_height += line_height_px
                line_heights.append(line_height_px)

            # Add padding for background
            padding = 16 if (bg_color != "transparent" and bg_opacity > 0) else stroke_width * 2
            img_width = max_width + padding * 2 + stroke_width * 2
            img_height = total_height + padding * 2

            # Create image
            img = Image.new('RGBA', (int(img_width), int(img_height)), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            # Draw background if not transparent and has opacity
            if bg_color != "transparent" and bg_opacity > 0:
                # Combine main opacity with background-specific opacity
                bg_alpha = int(alpha * bg_opacity)
                bg_rgba = hex_to_rgba(bg_color, bg_alpha)
                draw.rectangle([(0, 0), (img_width - 1, img_height - 1)], fill=bg_rgba)

            # Draw text
            y_offset = padding
            for i, line in enumerate(lines):
                # Calculate x position based on alignment
                bbox = font.getbbox(line or " ")
                line_width = bbox[2] - bbox[0]

                if text_align == "center":
                    x_offset = (img_width - line_width) / 2
                elif text_align == "right":
                    x_offset = img_width - line_width - padding
                else:  # left
                    x_offset = padding

                # Draw stroke/outline first (if specified)
                if stroke_width > 0 and stroke_rgba:
                    # Draw text multiple times offset for stroke effect
                    for dx in range(-stroke_width, stroke_width + 1):
                        for dy in range(-stroke_width, stroke_width + 1):
                            if dx != 0 or dy != 0:
                                draw.text((x_offset + dx, y_offset + dy), line, font=font, fill=stroke_rgba)

                # Draw main text
                draw.text((x_offset, y_offset), line, font=font, fill=text_rgba)
                y_offset += line_heights[i]

            # Apply rotation if specified
            rotation_raw = transform.get("rotation", 0)
            try:
                rotation = float(rotation_raw) if rotation_raw is not None else 0.0
            except (ValueError, TypeError):
                rotation = 0.0
            if abs(rotation) > 0.01:
                img = img.rotate(-rotation, expand=True, fillcolor=(0, 0, 0, 0))
                logger.info(f"[TEXT] Applied rotation: {rotation} degrees")

            # Save to temp file
            output_path = os.path.join(self.output_dir, f"text_{text_idx}.png")
            img.save(output_path, 'PNG')
            final_width, final_height = img.size
            logger.info(f"[TEXT] Generated PNG: {output_path} ({final_width}x{final_height})")
            return output_path

        except Exception as e:
            logger.error(f"[TEXT] Failed to generate text image: {e}")
            return None

    def _build_text_overlay_filter(
        self,
        input_idx: int,
        clip: dict[str, Any],
        base_output: str,
        text_idx: int,
    ) -> str:
        """Build FFmpeg overlay filter for text PNG.

        Args:
            input_idx: FFmpeg input index for the text PNG
            clip: Clip data containing transform, timing
            base_output: Current filter graph output label
            text_idx: Text index for output label

        Returns:
            FFmpeg filter string
        """
        transform = clip.get("transform", {})

        # Get position (center offset from canvas center)
        center_x = transform.get("x", 0)
        center_y = transform.get("y", 0)

        # Get timing
        start_ms = clip.get("start_ms", 0)
        duration_ms = clip.get("duration_ms", 0)
        start_s = start_ms / 1000
        end_s = (start_ms + duration_ms) / 1000

        output_label = f"text{text_idx}"

        # Convert center coords to top-left for FFmpeg overlay
        overlay_x = f"(main_w/2)+({int(center_x)})-(overlay_w/2)"
        overlay_y = f"(main_h/2)+({int(center_y)})-(overlay_h/2)"

        filter_str = (
            f"[{base_output}][{input_idx}:v]overlay="
            f"x={overlay_x}:y={overlay_y}:"
            f"enable='between(t,{start_s},{end_s})'"
            f"[{output_label}]"
        )

        logger.info(f"[TEXT] Overlay filter: input={input_idx}, pos=({center_x},{center_y}), time={start_s}-{end_s}s")
        return filter_str

    async def _create_blank_video(self, output_path: str, duration_ms: int) -> str:
        """Create a blank black video."""
        duration_s = duration_ms / 1000
        width = self.width
        height = self.height
        fps = self.fps

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-f", "lavfi",
            "-i", f"color=c=black:s={width}x{height}:r={fps}:d={duration_s}",
            "-c:v", "libx264",
            "-preset", "medium",
            "-pix_fmt", "yuv420p",
            output_path,
        ]
        # Use asyncio.to_thread to avoid blocking the event loop
        await asyncio.to_thread(subprocess.run, cmd, capture_output=True, check=True)
        return output_path

    async def _encode_final(
        self,
        video_path: str,
        audio_path: str,
        output_path: str,
        duration_ms: int,
    ) -> str:
        """Combine video and audio into final output."""
        duration_s = duration_ms / 1000
        print(f"[ENCODE FINAL] duration_ms={duration_ms}, duration_s={duration_s}", flush=True)

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", settings.render_audio_bitrate,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-t", str(duration_s),  # Explicitly limit output duration
            # Note: removed -shortest flag as it truncates output when audio is shorter than video
            "-movflags", "+faststart",
            output_path,
        ]

        # Use asyncio.to_thread to avoid blocking the event loop
        result = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Final encoding failed: {result.stderr}")

        return output_path

    def _cleanup(self) -> None:
        """Clean up temporary files."""
        try:
            shutil.rmtree(self.work_dir)
        except Exception:
            pass  # Ignore cleanup errors

    # ========================================================================
    # Job Management Methods (for new API)
    # ========================================================================

    def create_job(self, timeline: TimelineData, config: Optional[RenderConfig] = None) -> RenderJob:
        """Create a new render job from timeline data."""
        job_id = str(uuid4())
        job = RenderJob(
            id=job_id,
            project_id=timeline.project_id,
            status=RenderStatus.PENDING,
            config=config or RenderConfig(),
            created_at=datetime.now(timezone.utc),
        )
        self._jobs[job_id] = job
        self._timelines[job_id] = timeline
        self._progress[job_id] = RenderProgress(
            job_id=job_id,
            status=RenderStatus.PENDING,
            percent=0.0,
        )
        return job

    def get_job(self, job_id: str) -> Optional[RenderJob]:
        """Get a render job by ID."""
        return self._jobs.get(job_id)

    def list_jobs(self, project_id: str) -> list[RenderJob]:
        """List all jobs for a project."""
        return [job for job in self._jobs.values() if job.project_id == project_id]

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a pending or processing job."""
        job = self._jobs.get(job_id)
        if not job:
            return False
        if job.status in (RenderStatus.PENDING, RenderStatus.PROCESSING):
            job.status = RenderStatus.CANCELLED
            return True
        return False

    def get_progress(self, job_id: str) -> Optional[RenderProgress]:
        """Get progress for a render job."""
        return self._progress.get(job_id)

    def register_progress_callback(
        self, job_id: str, callback: Callable[[RenderProgress], None]
    ) -> None:
        """Register a callback for progress updates."""
        self._progress_callbacks[job_id] = callback

    def _notify_progress(self, progress: RenderProgress) -> None:
        """Notify registered callbacks of progress update."""
        callback = self._progress_callbacks.get(progress.job_id)
        if callback:
            callback(progress)
