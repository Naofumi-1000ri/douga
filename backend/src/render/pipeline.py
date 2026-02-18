"""
Main render pipeline for video compositing.

This module orchestrates the entire rendering process:
1. Parse timeline data
2. Download assets from GCS
3. Process audio (mixing, ducking)
4. Composite video layers
5. Encode final output
6. Upload to GCS

Memory-safe rendering:
- Pre-render memory estimation to prevent OOM on Cloud Run
- Chunked rendering for long videos that exceed memory budget
- FFmpeg thread/queue limits to reduce per-process memory
"""

import asyncio
import copy
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
# Memory Estimation & OOM Prevention
# ============================================================================


def get_container_memory_limit() -> int:
    """Detect the container memory limit from cgroup (Cloud Run / Docker).

    Returns:
        Memory limit in bytes.  Falls back to 2 GiB if detection fails.
    """
    # cgroup v2 (Cloud Run uses this)
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            with open(path) as f:
                raw = f.read().strip()
                if raw == "max":
                    # No limit – assume generous 8 GiB
                    return 8 * 1024 ** 3
                limit = int(raw)
                if limit > 0:
                    return limit
        except (FileNotFoundError, ValueError, PermissionError):
            continue

    # Manual override from settings
    if settings.render_max_memory_bytes > 0:
        return settings.render_max_memory_bytes

    # Conservative default: 2 GiB (Cloud Run default minimum)
    return 2 * 1024 ** 3


def estimate_render_memory(
    duration_s: float,
    width: int,
    height: int,
    num_layers_with_clips: int,
    total_clips: int,
    has_chroma_key: bool,
    fps: int = 30,
) -> int:
    """Estimate peak memory usage for an FFmpeg render.

    The estimate is intentionally **conservative** (overestimates).

    Memory components:
    1. Base FFmpeg process overhead: ~80 MB
    2. Decoded input frames per active clip: width * height * 4 (RGBA) per frame
       * Each overlay input keeps at least 1 decoded frame in memory
    3. Chroma-key processing doubles frame buffers (split + alphamerge)
    4. Encoder buffers (x264): ~frames-in-flight * frame_size
    5. Audio buffers are comparatively small (~20 MB total)
    6. filter_complex graph buffers scale with clip count

    Returns:
        Estimated peak memory in bytes.
    """
    frame_bytes = width * height * 4  # RGBA

    # 1) FFmpeg base overhead
    base_mb = 80

    # 2) Decoded frame buffers (one per active overlay clip, +1 for canvas)
    #    Conservative: assume all clips could be active simultaneously
    active_frames = total_clips + 1  # +1 for base canvas
    frame_buffer_bytes = active_frames * frame_bytes

    # 3) Chroma-key doubles buffers for each clip that uses it
    if has_chroma_key:
        # Assume up to half the clips might use chroma key
        chroma_extra = (total_clips // 2 + 1) * frame_bytes
    else:
        chroma_extra = 0

    # 4) x264 encoder: ~8 frames in flight (lookahead + reference)
    encoder_frames = 8
    encoder_bytes = encoder_frames * frame_bytes

    # 5) Audio buffers (generous flat estimate)
    audio_bytes = 20 * 1024 * 1024  # 20 MB

    # 6) Filter graph overhead: ~2 MB per filter node
    filter_nodes = total_clips * 3  # trim + scale + overlay per clip
    filter_overhead = filter_nodes * 2 * 1024 * 1024

    # 7) Duration-proportional component: longer videos accumulate muxer queue
    #    Roughly 0.5 MB per second of video
    duration_bytes = int(duration_s * 0.5 * 1024 * 1024)

    total = (
        base_mb * 1024 * 1024
        + frame_buffer_bytes
        + chroma_extra
        + encoder_bytes
        + audio_bytes
        + filter_overhead
        + duration_bytes
    )

    # Apply 20% safety margin on top of our already conservative estimate
    total = int(total * 1.2)

    logger.info(
        f"[MEMORY EST] duration={duration_s:.1f}s, {width}x{height}, "
        f"clips={total_clips}, layers={num_layers_with_clips}, "
        f"chroma={has_chroma_key}, estimate={total / 1024**2:.0f} MB"
    )
    return total


def analyze_timeline_for_memory(
    timeline_data: dict[str, Any],
    width: int,
    height: int,
    fps: int,
) -> dict[str, Any]:
    """Analyze timeline data and return memory estimation info.

    Returns:
        Dict with keys: estimated_bytes, duration_s, total_clips,
        num_layers_with_clips, has_chroma_key, needs_chunking,
        recommended_chunks, max_safe_duration_s
    """
    duration_ms = timeline_data.get("duration_ms", 0)
    duration_s = duration_ms / 1000.0

    layers = timeline_data.get("layers", [])
    total_clips = 0
    num_layers_with_clips = 0
    has_chroma_key = False

    for layer in layers:
        clips = layer.get("clips", [])
        if clips:
            num_layers_with_clips += 1
            total_clips += len(clips)
            for clip in clips:
                effects = clip.get("effects", {})
                ck = effects.get("chroma_key", {})
                if ck.get("enabled", False):
                    has_chroma_key = True

    # Count audio clips too (they consume some memory)
    for track in timeline_data.get("audio_tracks", []):
        total_clips += len(track.get("clips", []))

    estimated_bytes = estimate_render_memory(
        duration_s=duration_s,
        width=width,
        height=height,
        num_layers_with_clips=num_layers_with_clips,
        total_clips=total_clips,
        has_chroma_key=has_chroma_key,
        fps=fps,
    )

    container_limit = get_container_memory_limit()
    safety_limit = int(container_limit * settings.render_memory_safety_ratio)

    needs_chunking = estimated_bytes > safety_limit

    # Calculate how many chunks we need
    chunk_duration_s = settings.render_chunk_duration_s
    if needs_chunking and duration_s > chunk_duration_s:
        import math
        recommended_chunks = math.ceil(duration_s / chunk_duration_s)
    else:
        recommended_chunks = 1

    # Calculate max safe single-render duration (rough inverse of estimation)
    # Solve for duration_s given safety_limit
    # Simplification: estimate for 1s, then scale
    est_1s = estimate_render_memory(
        duration_s=1.0,
        width=width,
        height=height,
        num_layers_with_clips=num_layers_with_clips,
        total_clips=total_clips,
        has_chroma_key=has_chroma_key,
        fps=fps,
    )
    if est_1s > 0:
        # Linear approximation (memory grows sub-linearly, so this underestimates)
        max_safe_duration_s = safety_limit / (est_1s / 1.0)
        # Cap at a reasonable maximum
        max_safe_duration_s = min(max_safe_duration_s, 3600)
    else:
        max_safe_duration_s = 600

    return {
        "estimated_bytes": estimated_bytes,
        "estimated_mb": estimated_bytes / 1024 ** 2,
        "container_limit_bytes": container_limit,
        "container_limit_mb": container_limit / 1024 ** 2,
        "safety_limit_bytes": safety_limit,
        "safety_limit_mb": safety_limit / 1024 ** 2,
        "duration_s": duration_s,
        "total_clips": total_clips,
        "num_layers_with_clips": num_layers_with_clips,
        "has_chroma_key": has_chroma_key,
        "needs_chunking": needs_chunking,
        "recommended_chunks": recommended_chunks,
        "chunk_duration_s": chunk_duration_s,
        "max_safe_duration_s": max_safe_duration_s,
    }


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

    # Frame overlap margin to prevent gaps between adjacent clips.
    # FFmpeg's between(t,start,end) can cause 1-frame gaps at clip boundaries
    # due to floating-point timing precision. Adding a small overlap ensures
    # continuous coverage. Value is in seconds (1 frame at 30fps = 0.0333s).
    FRAME_OVERLAP_MARGIN = 0.034  # Slightly more than 1 frame at 30fps

    def _build_enable_expr(self, start_s: float, end_s: float) -> str:
        """Build FFmpeg enable expression with frame overlap margin.

        Adds a small margin to the end time to prevent 1-frame gaps between
        adjacent clips. The overlap is safe because FFmpeg composites in order,
        so the next clip will simply overlay on top.

        Args:
            start_s: Start time in seconds
            end_s: End time in seconds

        Returns:
            FFmpeg enable expression string
        """
        # Add overlap margin to end time to prevent gaps at clip boundaries
        adjusted_end_s = end_s + self.FRAME_OVERLAP_MARGIN
        return f"between(t,{start_s:.6f},{adjusted_end_s:.6f})"

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

        Automatically detects if the render would exceed memory limits and
        falls back to chunked rendering when needed.

        Args:
            timeline_data: Project timeline data
            assets: Map of asset IDs to local file paths
            output_path: Output video file path
            cancel_check: Optional async callable that returns True if cancelled

        Returns:
            Path to rendered video

        Raises:
            asyncio.CancelledError: If render was cancelled
            MemoryError: If render would exceed memory even with chunking
        """
        self._cancel_check = cancel_check

        self._update_progress(5, "Preparing render")

        duration_ms = timeline_data.get("duration_ms", 0)
        if duration_ms <= 0:
            raise ValueError("Timeline duration must be greater than 0")

        # Memory estimation and OOM prevention
        mem_info = analyze_timeline_for_memory(
            timeline_data, self.width, self.height, self.fps
        )
        logger.info(
            f"[RENDER] Memory estimate: {mem_info['estimated_mb']:.0f} MB, "
            f"container limit: {mem_info['container_limit_mb']:.0f} MB, "
            f"safety limit: {mem_info['safety_limit_mb']:.0f} MB, "
            f"needs_chunking: {mem_info['needs_chunking']}"
        )
        print(
            f"[RENDER MEMORY] Estimated: {mem_info['estimated_mb']:.0f} MB / "
            f"Limit: {mem_info['container_limit_mb']:.0f} MB "
            f"(safety: {mem_info['safety_limit_mb']:.0f} MB) "
            f"chunks: {mem_info['recommended_chunks']}",
            flush=True,
        )

        if mem_info["needs_chunking"] and mem_info["recommended_chunks"] > 1:
            logger.info(
                f"[RENDER] Using chunked rendering: {mem_info['recommended_chunks']} chunks "
                f"of ~{mem_info['chunk_duration_s']}s each"
            )
            return await self._render_chunked(
                timeline_data, assets, output_path, mem_info
            )

        # Standard single-pass render
        return await self._render_single(timeline_data, assets, output_path, duration_ms)

    async def _render_single(
        self,
        timeline_data: dict[str, Any],
        assets: dict[str, str],
        output_path: str,
        duration_ms: int,
    ) -> str:
        """Execute the standard single-pass render pipeline."""

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

    async def _render_chunked(
        self,
        timeline_data: dict[str, Any],
        assets: dict[str, str],
        output_path: str,
        mem_info: dict[str, Any],
    ) -> str:
        """
        Render a long video in chunks to stay within memory limits.

        1. Split timeline into time-based chunks
        2. Render each chunk as a separate FFmpeg process (video + audio)
        3. Concatenate chunks using FFmpeg concat demuxer (lossless)

        The output is identical to a single-pass render.
        """
        duration_ms = timeline_data.get("duration_ms", 0)
        export_start_ms = timeline_data.get("export_start_ms", 0)
        export_end_ms = timeline_data.get("export_end_ms", duration_ms + export_start_ms)
        chunk_duration_s = mem_info["chunk_duration_s"]
        num_chunks = mem_info["recommended_chunks"]

        chunks_dir = os.path.join(self.output_dir, "chunks")
        os.makedirs(chunks_dir, exist_ok=True)

        # Calculate chunk boundaries
        chunk_boundaries = self._calculate_chunk_boundaries(
            timeline_data, export_start_ms, export_end_ms, chunk_duration_s
        )
        num_chunks = len(chunk_boundaries)

        logger.info(f"[CHUNKED] Splitting into {num_chunks} chunks: {chunk_boundaries}")
        print(f"[CHUNKED RENDER] {num_chunks} chunks: {[(s,e) for s,e in chunk_boundaries]}", flush=True)

        chunk_files: list[str] = []

        for chunk_idx, (chunk_start_ms, chunk_end_ms) in enumerate(chunk_boundaries):
            if await self._is_cancelled():
                raise asyncio.CancelledError("Render cancelled")

            chunk_duration_ms = chunk_end_ms - chunk_start_ms
            chunk_pct_start = int(5 + (chunk_idx / num_chunks) * 85)
            chunk_pct_end = int(5 + ((chunk_idx + 1) / num_chunks) * 85)

            self._update_progress(
                chunk_pct_start,
                f"Rendering chunk {chunk_idx + 1}/{num_chunks}",
            )
            print(
                f"[CHUNKED RENDER] Chunk {chunk_idx + 1}/{num_chunks}: "
                f"{chunk_start_ms}ms - {chunk_end_ms}ms ({chunk_duration_ms}ms)",
                flush=True,
            )

            # Create a modified timeline for this chunk
            chunk_timeline = self._create_chunk_timeline(
                timeline_data, chunk_start_ms, chunk_end_ms
            )

            # Create a sub-pipeline for this chunk (with its own work dir)
            chunk_output_path = os.path.join(chunks_dir, f"chunk_{chunk_idx:03d}.mp4")

            # Create sub-pipeline for this chunk
            chunk_pipeline = RenderPipeline(
                job_id=f"{self.job_id}_chunk{chunk_idx}",
                project_id=self.project_id,
                width=self.width,
                height=self.height,
                fps=self.fps,
            )

            # Forward progress callback with chunk-aware mapping
            def make_chunk_callback(idx, pct_start, pct_end):
                def cb(p, s):
                    # Map chunk's 0-100 progress to the overall range for this chunk
                    overall_pct = pct_start + int((p / 100) * (pct_end - pct_start))
                    self._update_progress(
                        overall_pct,
                        f"Chunk {idx + 1}/{num_chunks}: {s}",
                    )
                return cb

            chunk_pipeline.set_progress_callback(
                make_chunk_callback(chunk_idx, chunk_pct_start, chunk_pct_end)
            )

            try:
                await chunk_pipeline._render_single(
                    chunk_timeline, assets, chunk_output_path, chunk_duration_ms
                )
                chunk_files.append(chunk_output_path)
            except Exception as e:
                logger.error(f"[CHUNKED] Chunk {chunk_idx} failed: {e}")
                raise RuntimeError(
                    f"Chunked rendering failed at chunk {chunk_idx + 1}/{num_chunks}: {e}"
                ) from e

        if await self._is_cancelled():
            raise asyncio.CancelledError("Render cancelled")

        # Concatenate all chunks
        self._update_progress(92, "Concatenating chunks")
        print(f"[CHUNKED RENDER] Concatenating {len(chunk_files)} chunks", flush=True)

        await self._concatenate_chunks(chunk_files, output_path)

        # Cleanup chunk files
        self._update_progress(97, "Cleaning up chunks")
        try:
            shutil.rmtree(chunks_dir, ignore_errors=True)
        except Exception:
            pass

        # Also cleanup sub-pipeline work dirs
        for chunk_idx in range(num_chunks):
            sub_dir = None
            for d in Path(tempfile.gettempdir()).glob(f"douga_render_{self.job_id}_chunk{chunk_idx}_*"):
                try:
                    shutil.rmtree(d, ignore_errors=True)
                except Exception:
                    pass

        self._update_progress(100, "Complete")
        return output_path

    def _calculate_chunk_boundaries(
        self,
        timeline_data: dict[str, Any],
        export_start_ms: int,
        export_end_ms: int,
        chunk_duration_s: int,
    ) -> list[tuple[int, int]]:
        """Calculate chunk boundaries, aligning to clip edges when possible.

        Returns list of (start_ms, end_ms) tuples in absolute timeline coordinates.
        """
        chunk_duration_ms = chunk_duration_s * 1000
        total_duration_ms = export_end_ms - export_start_ms

        if total_duration_ms <= chunk_duration_ms:
            return [(export_start_ms, export_end_ms)]

        # Collect all clip edges as potential split points
        clip_edges: set[int] = set()
        for layer in timeline_data.get("layers", []):
            for clip in layer.get("clips", []):
                clip_start = clip.get("start_ms", 0)
                clip_end = clip_start + clip.get("duration_ms", 0)
                if export_start_ms < clip_start < export_end_ms:
                    clip_edges.add(clip_start)
                if export_start_ms < clip_end < export_end_ms:
                    clip_edges.add(clip_end)

        sorted_edges = sorted(clip_edges)

        boundaries: list[tuple[int, int]] = []
        current_start = export_start_ms

        while current_start < export_end_ms:
            ideal_end = current_start + chunk_duration_ms

            if ideal_end >= export_end_ms:
                # Last chunk
                boundaries.append((current_start, export_end_ms))
                break

            # Try to find a clip edge near the ideal boundary
            # Search within +/- 20% of chunk duration
            search_window = chunk_duration_ms * 0.2
            best_edge = ideal_end  # Default: exact boundary

            for edge in sorted_edges:
                if ideal_end - search_window <= edge <= ideal_end + search_window:
                    # Prefer edges that don't split clips
                    best_edge = edge
                    break

            boundaries.append((current_start, best_edge))
            current_start = best_edge

        return boundaries

    def _create_chunk_timeline(
        self,
        original_timeline: dict[str, Any],
        chunk_start_ms: int,
        chunk_end_ms: int,
    ) -> dict[str, Any]:
        """Create a timeline subset for a specific chunk.

        Adjusts clip timings, filters out clips outside the range,
        and sets export_start_ms/export_end_ms for the chunk.
        """
        chunk_duration_ms = chunk_end_ms - chunk_start_ms

        # Deep copy to avoid modifying the original
        chunk_timeline = copy.deepcopy(original_timeline)

        # Set chunk-specific timing
        chunk_timeline["duration_ms"] = chunk_duration_ms
        chunk_timeline["export_start_ms"] = chunk_start_ms
        chunk_timeline["export_end_ms"] = chunk_end_ms

        return chunk_timeline

    async def _concatenate_chunks(
        self,
        chunk_files: list[str],
        output_path: str,
    ) -> None:
        """Concatenate rendered chunk files using FFmpeg concat demuxer.

        Uses -c copy for lossless concatenation (no re-encoding).
        """
        if len(chunk_files) == 1:
            # Single chunk - just copy
            shutil.copy2(chunk_files[0], output_path)
            return

        # Create concat list file
        concat_list_path = os.path.join(self.output_dir, "concat_list.txt")
        with open(concat_list_path, "w") as f:
            for chunk_file in chunk_files:
                # FFmpeg concat requires escaped paths
                escaped = chunk_file.replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_path,
            "-c", "copy",
            "-movflags", "+faststart",
            output_path,
        ]

        logger.info(f"[CHUNKED] Concatenation command: {' '.join(cmd)}")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace")
            logger.error(f"[CHUNKED] Concatenation failed: {stderr_text}")
            raise RuntimeError(f"Chunk concatenation failed: {stderr_text}")

        logger.info(f"[CHUNKED] Concatenation successful: {output_path}")

    async def _is_cancelled(self) -> bool:
        """Check if render has been cancelled."""
        if self._cancel_check is None:
            return False
        result = self._cancel_check()
        if asyncio.iscoroutine(result):
            return await result
        return result

    def _build_audio_tracks(
        self,
        timeline_data: dict[str, Any],
        assets: dict[str, str],
        duration_ms: int,
    ) -> list[AudioTrackData]:
        """Convert timeline JSON audio data into AudioTrackData list.

        Args:
            timeline_data: Project timeline data
            assets: Map of asset IDs to local file paths
            duration_ms: Total duration in milliseconds

        Returns:
            List of AudioTrackData ready for AudioMixer
        """
        audio_tracks = timeline_data.get("audio_tracks", [])
        export_start_ms = timeline_data.get("export_start_ms", 0)
        export_end_ms = timeline_data.get("export_end_ms", duration_ms + export_start_ms)
        print(f"[AUDIO MIX] Found {len(audio_tracks)} audio tracks, export_range={export_start_ms}-{export_end_ms}ms", flush=True)
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
                clip_start_ms = clip_data.get("start_ms", 0)
                clip_duration_ms = clip_data.get("duration_ms", 0)
                clip_end_ms = clip_start_ms + clip_duration_ms

                # Skip clips that are completely outside the export range
                if clip_end_ms <= export_start_ms or clip_start_ms >= export_end_ms:
                    print(f"[AUDIO MIX] Skipping clip (outside range): start={clip_start_ms}, end={clip_end_ms}", flush=True)
                    continue

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

                    # Adjust clip timing relative to export start
                    # Also handle clips that start before export_start_ms (need to trim source)
                    adjusted_start_ms = max(0, clip_start_ms - export_start_ms)
                    in_point_ms = clip_data.get("in_point_ms", 0)

                    # If clip starts before export range, we need to advance the in_point
                    if clip_start_ms < export_start_ms:
                        in_point_offset = export_start_ms - clip_start_ms
                        in_point_ms += in_point_offset
                        clip_duration_ms -= in_point_offset

                    # If clip extends beyond export range, trim it
                    if clip_end_ms > export_end_ms:
                        clip_duration_ms -= (clip_end_ms - export_end_ms)

                    clips.append(
                        AudioClipData(
                            file_path=assets[asset_id],
                            start_ms=adjusted_start_ms,
                            duration_ms=clip_duration_ms,
                            in_point_ms=in_point_ms,
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

        return tracks

    async def _mix_audio(
        self,
        timeline_data: dict[str, Any],
        assets: dict[str, str],
        duration_ms: int,
    ) -> str:
        """Mix all audio tracks."""
        tracks = self._build_audio_tracks(timeline_data, assets, duration_ms)
        output_path = os.path.join(self.output_dir, "mixed_audio.aac")
        # Use asyncio.to_thread to avoid blocking the event loop
        return await asyncio.to_thread(self.audio_mixer.mix_tracks, tracks, output_path, duration_ms)

    def build_composite_command(
        self,
        timeline_data: dict[str, Any],
        assets: dict[str, str],
        duration_ms: int,
        output_path: str,
    ) -> tuple[list[str], dict[str, str]] | None:
        """Build FFmpeg composite command without executing it.

        Also generates text/shape PNGs via Pillow (server-side only).

        Args:
            timeline_data: Project timeline data
            assets: Map of asset IDs to local file paths
            duration_ms: Total duration in milliseconds
            output_path: Output video file path

        Returns:
            Tuple of (FFmpeg command as list[str], generated_files mapping
            {label -> path}), or None if no layers to composite.
        """
        layers = timeline_data.get("layers", [])
        export_start_ms = timeline_data.get("export_start_ms", 0)
        export_end_ms = timeline_data.get("export_end_ms", duration_ms + export_start_ms)

        logger.info(f"[RENDER DEBUG] Available assets: {list(assets.keys())}")
        logger.info(f"[RENDER DEBUG] Total layers in timeline: {len(layers)}")
        logger.info(f"[RENDER DEBUG] Export range: {export_start_ms}ms - {export_end_ms}ms")

        if not layers or all(not layer.get("clips") for layer in layers):
            return None

        inputs = []
        filter_parts = []
        input_idx = 0
        generated_files: dict[str, str] = {}

        sorted_layers = list(reversed(layers))

        width = self.width
        height = self.height
        fps = self.fps
        duration_s = duration_ms / 1000

        inputs.extend([
            "-f", "lavfi",
            "-i", f"color=c=black:s={width}x{height}:r={fps}:d={duration_s}"
        ])
        current_output = f"{input_idx}:v"
        input_idx += 1

        shape_idx = 0

        for layer in sorted_layers:
            layer_id = layer.get("id", "unknown")
            layer_name = layer.get("name", "unknown")
            logger.info(f"[RENDER DEBUG] Processing layer: {layer_name} (id={layer_id}, order={layer.get('order')})")

            if not layer.get("visible", True):
                continue

            clips = layer.get("clips", [])
            if not clips:
                continue

            layer_type = layer.get("type", "content")
            logger.info(f"[RENDER DEBUG] Layer {layer_name} has {len(clips)} clips, type={layer_type}")

            for clip in clips:
                clip_start = clip.get('start_ms', 0)
                clip_duration = clip.get('duration_ms', 0)
                clip_end = clip_start + clip_duration

                if clip_end <= export_start_ms or clip_start >= export_end_ms:
                    continue

                shape = clip.get("shape")
                if shape:
                    shape_path = self._generate_shape_image(shape, clip, shape_idx)
                    if shape_path:
                        generated_files[f"shape_{shape_idx}.png"] = shape_path
                        inputs.extend(["-i", shape_path])
                        shape_filter = self._build_shape_overlay_filter(
                            input_idx, clip, current_output, shape_idx, export_start_ms, export_end_ms
                        )
                        filter_parts.append(shape_filter)
                        current_output = f"shape{shape_idx}"
                        input_idx += 1
                        shape_idx += 1
                    continue

                text_content = clip.get("text_content")
                if text_content is not None:
                    text_path = self._generate_text_image(clip, shape_idx)
                    if text_path:
                        generated_files[f"text_{shape_idx}.png"] = text_path
                        inputs.extend(["-i", text_path])
                        text_filter = self._build_text_overlay_filter(
                            input_idx, clip, current_output, shape_idx, export_start_ms, export_end_ms
                        )
                        filter_parts.append(text_filter)
                        current_output = f"text{shape_idx}"
                        input_idx += 1
                        shape_idx += 1
                    continue

                asset_id = str(clip.get("asset_id", ""))
                if not asset_id or asset_id not in assets:
                    continue

                asset_path = assets[asset_id]
                inputs.extend(["-i", asset_path])

                clip_filter = self._build_clip_filter(
                    input_idx,
                    clip,
                    layer_type,
                    current_output,
                    duration_ms,
                    export_start_ms,
                    export_end_ms,
                )
                filter_parts.append(clip_filter)
                current_output = f"layer{input_idx}"
                input_idx += 1

        if not filter_parts:
            return None

        filter_parts.append(f"[{current_output}]null[vout]")
        filter_complex = ";\n".join(filter_parts)

        logger.info(f"[RENDER DEBUG] Number of inputs: {len(inputs) // 2}")
        logger.info(f"[RENDER DEBUG] filter_complex:\n{filter_complex}")

        duration_s_total = timeline_data.get("duration_ms", duration_ms) / 1000
        preset = "fast" if duration_s_total > 180 else "medium"

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-threads", str(settings.render_ffmpeg_threads),
            *inputs,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-c:v", "libx264",
            "-preset", preset,
            "-crf", "18",
            "-r", str(fps),
            "-pix_fmt", "yuv420p",
            "-max_muxing_queue_size", str(settings.render_ffmpeg_max_muxing_queue),
            "-t", str(duration_s),
            output_path,
        ]

        return cmd, generated_files

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
        output_path = os.path.join(self.output_dir, "composite.mp4")

        result = self.build_composite_command(timeline_data, assets, duration_ms, output_path)
        if result is None:
            return await self._create_blank_video(output_path, duration_ms)

        cmd, _generated_files = result
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
                # Get clip timing for export range filtering
                clip_start = clip.get('start_ms', 0)
                clip_duration = clip.get('duration_ms', 0)
                clip_end = clip_start + clip_duration + clip.get("freeze_frame_ms", 0)

                # Skip clips that are completely outside the export range
                if clip_end <= export_start_ms or clip_start >= export_end_ms:
                    logger.info(f"[RENDER DEBUG] Skipping clip (outside export range): start={clip_start}, end={clip_end}")
                    continue

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
                            input_idx, clip, current_output, shape_idx, export_start_ms, export_end_ms
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
                            input_idx, clip, current_output, shape_idx, export_start_ms, export_end_ms
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
                    export_start_ms,
                    export_end_ms,
                )
                filter_parts.append(clip_filter)
                # Adjusted timing for export range
                adjusted_start = max(0, clip_start - export_start_ms)
                adjusted_end = min(duration_ms, clip_end - export_start_ms)
                print(f"[RENDER DEBUG] Clip overlay enable: start={adjusted_start/1000}s, end={adjusted_end/1000}s (original: {clip_start}-{clip_end})", flush=True)

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

        # Use asyncio subprocess with -progress pipe for incremental progress
        # reporting during the long FFmpeg compositing step.
        cmd_with_progress = cmd.copy()
        # Insert -progress pipe:1 before output_path to get progress on stdout
        cmd_with_progress.insert(-1, "-progress")
        cmd_with_progress.insert(-1, "pipe:1")

        proc = await asyncio.create_subprocess_exec(
            *cmd_with_progress,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        last_reported_pct = 0
        try:
            async for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line.startswith("out_time_us="):
                    try:
                        time_us = int(line.split("=")[1])
                        time_s = time_us / 1_000_000
                        pct = min(99, int(time_s / duration_s * 100))
                        if pct > last_reported_pct + 4:  # Report every ~5%
                            last_reported_pct = pct
                            self._update_progress(
                                30 + int(pct * 0.5),
                                f"Compositing video ({pct}%)",
                            )
                    except (ValueError, ZeroDivisionError):
                        pass
                elif line.startswith("progress=end"):
                    break
        except Exception as e:
            logger.warning(f"[RENDER] Error reading FFmpeg progress: {e}")

        stderr_output = await proc.stderr.read()
        await proc.wait()
        returncode = proc.returncode

        print(f"[RENDER DEBUG] FFmpeg returncode: {returncode}", flush=True)
        if returncode != 0:
            logger.error(f"[RENDER DEBUG] FFmpeg stderr: {stderr_output.decode('utf-8', errors='replace')}")
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
        export_start_ms: int = 0,
        export_end_ms: int | None = None,
    ) -> str:
        """Build FFmpeg filter for a single clip.

        Args:
            export_start_ms: Start of export range in ms (clips are offset relative to this)
            export_end_ms: End of export range in ms (clips extending beyond are trimmed)
        """
        if export_end_ms is None:
            export_end_ms = total_duration_ms + export_start_ms

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

        logger.info(f"[CLIP DEBUG] in_point={in_point_ms}ms, out_point={out_point_ms}ms, duration={duration_ms}ms, start={start_ms}ms, export_start={export_start_ms}ms")

        # Adjust in_point and out_point based on export range
        # If clip starts before export_start_ms, we need to advance the in_point
        clip_end_ms = start_ms + duration_ms + clip.get("freeze_frame_ms", 0)
        adjusted_in_point_ms = in_point_ms
        adjusted_out_point_ms = out_point_ms

        if start_ms < export_start_ms:
            # Clip starts before export range - advance in_point to skip the beginning
            offset_ms = export_start_ms - start_ms
            adjusted_in_point_ms = in_point_ms + offset_ms
            logger.info(f"[CLIP DEBUG] Clip starts before export range, advancing in_point by {offset_ms}ms")

        if clip_end_ms > export_end_ms:
            # Clip extends beyond export range - reduce out_point
            trim_end_ms = clip_end_ms - export_end_ms
            # First trim from freeze frame portion, then from source video
            freeze_trim = min(trim_end_ms, clip.get("freeze_frame_ms", 0))
            remaining_trim = trim_end_ms - freeze_trim
            adjusted_out_point_ms = out_point_ms - remaining_trim
            logger.info(f"[CLIP DEBUG] Clip extends beyond export range, trimming {trim_end_ms}ms from end (freeze: {freeze_trim}ms, source: {remaining_trim}ms)")

        # Apply trim filter to extract the portion of source we need
        start_s = adjusted_in_point_ms / 1000
        end_s = adjusted_out_point_ms / 1000
        speed = clip.get("speed", 1.0)
        clip_filters.append(f"trim=start={start_s}:end={end_s}")
        if speed != 1.0:
            clip_filters.append(f"setpts=(PTS-STARTPTS)/{speed}")
        else:
            clip_filters.append("setpts=PTS-STARTPTS")

        # Freeze frame extension (applied after setpts, before crop/scale)
        freeze_frame_ms = clip.get("freeze_frame_ms", 0)
        if freeze_frame_ms > 0:
            clip_filters.append(f"tpad=stop_mode=clone:stop_duration={freeze_frame_ms / 1000}")

        # Crop filter (applied before scale)
        crop = clip.get("crop", {})
        crop_top = crop.get("top", 0)
        crop_right = crop.get("right", 0)
        crop_bottom = crop.get("bottom", 0)
        crop_left = crop.get("left", 0)
        has_crop = crop_top > 0 or crop_right > 0 or crop_bottom > 0 or crop_left > 0
        # Crop is applied AFTER scale (below) to avoid non-uniform stretching.
        # Frontend uses CSS clip-path: inset() which hides edges without changing
        # element dimensions. We match this by: scale first → crop → adjust overlay position.
        if has_crop:
            logger.info(
                f"[CLIP DEBUG] Crop values: top={crop_top}, right={crop_right}, "
                f"bottom={crop_bottom}, left={crop_left}"
            )

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
        chroma_key = effects.get("chroma_key") or {}
        chroma_key_enabled = chroma_key.get("enabled", False)
        if chroma_key_enabled:
            color = chroma_key.get("color", "#00FF00").replace("#", "0x")
            # Defaults match effects_spec.yaml (SSOT)
            similarity = chroma_key.get("similarity", 0.4)
            blend = chroma_key.get("blend", 0.1)
            clip_filters.append(f"colorkey={color}:{similarity}:{blend}")
            # Despill to remove color fringing
            hex_c = chroma_key.get("color", "#00FF00").lstrip("#")
            try:
                r, g, b = int(hex_c[0:2], 16), int(hex_c[2:4], 16), int(hex_c[4:6], 16)
                despill_type = "blue" if (b > g and b > r) else "green"
            except (ValueError, IndexError):
                despill_type = "green"
            clip_filters.append(f"despill=type={despill_type}")

        # Post-chroma filters (crop, rotation, opacity) go into a separate
        # list when chroma key is enabled so we can insert alpha erosion
        # between the chroma key and these filters.
        post_chroma_filters: list[str] = []
        target_list = post_chroma_filters if chroma_key_enabled else clip_filters

        # Apply crop AFTER colorkey+despill (preserves alpha compositing)
        # Crop before colorkey can interfere with the alpha channel.
        if has_crop:
            target_list.append(
                f"crop=iw*{1 - crop_left - crop_right:.4f}:ih*{1 - crop_top - crop_bottom:.4f}"
                f":iw*{crop_left:.4f}:ih*{crop_top:.4f}"
            )

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
            target_list.append("format=rgba")
            target_list.append(
                f"rotate={rotation}*PI/180:ow='hypot(iw,ih)':oh='hypot(iw,ih)':fillcolor=none"
            )
            logger.info(f"[CLIP DEBUG] Added rotation filter with expanded bounds")

        # Opacity
        opacity = effects.get("opacity", 1.0)
        if opacity < 1.0:
            target_list.append(f"format=rgba,colorchannelmixer=aa={opacity}")

        # Build the filter string
        if chroma_key_enabled and clip_filters:
            # Alpha erosion: split stream, erode alpha channel by 1px, merge back.
            # This removes the dark fringe at chroma key edges.
            ck_m = f"ck{input_idx}_m"
            ck_a = f"ck{input_idx}_a"
            ck_e = f"ck{input_idx}_e"
            pre_str = ",".join(clip_filters)
            post_str = ("," + ",".join(post_chroma_filters)) if post_chroma_filters else ""
            filter_str = (
                f"[{input_idx}:v]{pre_str},split[{ck_m}][{ck_a}];\n"
                f"[{ck_a}]alphaextract,erosion,gblur=sigma=1.5[{ck_e}];\n"
                f"[{ck_m}][{ck_e}]alphamerge{post_str}[clip{input_idx}];\n"
            )
            clip_ref = f"clip{input_idx}"
        elif clip_filters:
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
        # Adjust overlay position for crop offset.
        # Crop reduces overlay dimensions, shifting the center.
        # Compensate so visible content stays in the correct position.
        crop_offset_x = 0
        crop_offset_y = 0
        if has_crop:
            if width and height:
                sw = int(width * scale)
                sh = int(height * scale)
            else:
                sw = sh = 0
            crop_offset_x = int(sw * (crop_left - crop_right) / 2)
            crop_offset_y = int(sh * (crop_top - crop_bottom) / 2)
            logger.info(f"[CLIP DEBUG] Crop overlay offset: x={crop_offset_x}, y={crop_offset_y}")
        overlay_x = f"(main_w/2)+({int(x) + crop_offset_x})-(overlay_w/2)"
        overlay_y = f"(main_h/2)+({int(y) + crop_offset_y})-(overlay_h/2)"

        # Adjust timing relative to export_start_ms
        # Original clip timing is in absolute timeline coordinates
        # We need to offset by export_start_ms to get the position in the exported video
        clip_end_ms = start_ms + duration_ms + clip.get("freeze_frame_ms", 0)
        adjusted_start_ms = max(0, start_ms - export_start_ms)
        adjusted_end_ms = min(total_duration_ms, clip_end_ms - export_start_ms)

        start_time = adjusted_start_ms / 1000
        end_time = adjusted_end_ms / 1000
        enable_expr = self._build_enable_expr(start_time, end_time)
        logger.info(f"[CLIP DEBUG] Overlay enable: {enable_expr} (original: {start_ms}-{clip_end_ms}ms, export_start={export_start_ms}ms)")
        filter_str += f"[{base_output}][{clip_ref}]overlay=x={overlay_x}:y={overlay_y}:enable='{enable_expr}'[{output_label}]"

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
            # Support 8-char hex (RRGGBBAA): embedded alpha overrides the parameter
            if len(hex_color) == 8:
                alpha = int(hex_color[6:8], 16)
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
        export_start_ms: int = 0,
        export_end_ms: int | None = None,
    ) -> str:
        """Build FFmpeg overlay filter for shape PNG.

        Args:
            input_idx: FFmpeg input index for the shape PNG
            clip: Clip data containing transform, timing
            base_output: Current filter graph output label
            shape_idx: Shape index for output label
            export_start_ms: Start of export range in ms (clips are offset relative to this)
            export_end_ms: End of export range in ms

        Returns:
            FFmpeg filter string
        """
        transform = clip.get("transform", {})

        # Get position (center offset from canvas center)
        center_x = transform.get("x", 0)
        center_y = transform.get("y", 0)

        # Get timing and adjust for export range
        start_ms = clip.get("start_ms", 0)
        duration_ms = clip.get("duration_ms", 0)
        clip_end_ms = start_ms + duration_ms

        # Adjust timing relative to export_start_ms
        adjusted_start_ms = max(0, start_ms - export_start_ms)
        adjusted_end_ms = clip_end_ms - export_start_ms

        start_s = adjusted_start_ms / 1000
        end_s = adjusted_end_ms / 1000
        enable_expr = self._build_enable_expr(start_s, end_s)

        output_label = f"shape{shape_idx}"

        # Convert center coords to top-left for FFmpeg overlay
        # overlay_x = (canvas_w/2) + center_x - (overlay_w/2)
        # overlay_y = (canvas_h/2) + center_y - (overlay_h/2)
        overlay_x = f"(main_w/2)+({int(center_x)})-(overlay_w/2)"
        overlay_y = f"(main_h/2)+({int(center_y)})-(overlay_h/2)"

        filter_str = (
            f"[{base_output}][{input_idx}:v]overlay="
            f"x={overlay_x}:y={overlay_y}:"
            f"enable='{enable_expr}'"
            f"[{output_label}]"
        )

        logger.info(f"[SHAPE] Overlay filter: input={input_idx}, pos=({center_x},{center_y}), enable={enable_expr} (original: {start_ms}-{clip_end_ms}ms)")
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
        # Map font families to candidate paths (macOS → Linux fallback)
        font_candidates = {
            "Noto Sans JP": [
                "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",  # macOS
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Linux (fonts-noto-cjk)
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            ],
            "Noto Sans JP Bold": [
                "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",  # macOS
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",  # Linux
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            ],
            "Noto Serif JP": [
                "/System/Library/Fonts/ヒラギノ明朝 ProN.ttc",
                "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
                "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
            ],
            "Kosugi Maru": [
                "/System/Library/Fonts/ヒラギノ丸ゴ ProN W4.ttc",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            ],
        }

        # Select candidate list
        if font_weight == "bold":
            candidates = font_candidates.get(font_family + " Bold", font_candidates.get("Noto Sans JP Bold", []))
        else:
            candidates = font_candidates.get(font_family, [])
        # Always append default sans candidates as final fallback
        default_candidates = font_candidates["Noto Sans JP"]
        all_candidates = candidates + [c for c in default_candidates if c not in candidates]

        # Try each candidate path
        font = None
        for candidate_path in all_candidates:
            try:
                font = ImageFont.truetype(candidate_path, font_size)
                logger.info(f"[TEXT] Loaded font: {candidate_path}")
                break
            except Exception:
                continue

        if font is None:
            logger.warning(f"[TEXT] No suitable font found, using PIL default")
            font = ImageFont.load_default()

        try:
            # Parse colors
            def hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
                hex_color = hex_color.lstrip('#')
                if len(hex_color) == 3:
                    hex_color = ''.join([c*2 for c in hex_color])
                r = int(hex_color[0:2], 16)
                g = int(hex_color[2:4], 16)
                b = int(hex_color[4:6], 16)
                # Support 8-char hex (RRGGBBAA): embedded alpha overrides the parameter
                if len(hex_color) == 8:
                    alpha = int(hex_color[6:8], 16)
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

            # Determine if background should be drawn
            # Parse bg_color to check for embedded alpha (8-char hex like #00000080)
            bg_rgba_parsed = hex_to_rgba(bg_color, int(alpha * bg_opacity)) if bg_color != "transparent" else None
            has_bg = bg_rgba_parsed is not None and bg_rgba_parsed[3] > 0

            # Create image with transparent background
            img = Image.new('RGBA', (int(img_width), int(img_height)), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            # Always create alpha mask to fix Pillow/font bug on Cloud Run + Noto CJK
            # where draw.text() sets all pixel alphas to 255 regardless of the fill alpha.
            # The mask tracks intended alpha for every pixel.
            alpha_mask = Image.new('L', (int(img_width), int(img_height)), 0)
            mask_draw = ImageDraw.Draw(alpha_mask)

            # Draw background if not transparent and has opacity
            if has_bg:
                bg_alpha = bg_rgba_parsed[3]
                draw.rectangle([(0, 0), (img_width - 1, img_height - 1)], fill=bg_rgba_parsed)
                # Record background alpha in the mask
                mask_draw.rectangle([(0, 0), (img_width - 1, img_height - 1)], fill=bg_alpha)

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
                    for dx in range(-stroke_width, stroke_width + 1):
                        for dy in range(-stroke_width, stroke_width + 1):
                            if dx != 0 or dy != 0:
                                draw.text((x_offset + dx, y_offset + dy), line, font=font, fill=stroke_rgba)
                                mask_draw.text((x_offset + dx, y_offset + dy), line, font=font, fill=alpha)

                # Draw main text
                draw.text((x_offset, y_offset), line, font=font, fill=text_rgba)
                mask_draw.text((x_offset, y_offset), line, font=font, fill=alpha)
                y_offset += line_heights[i]

            # Apply alpha mask to fix transparency on environments where
            # draw.text() fills all pixels with alpha=255 (Cloud Run + Noto CJK)
            img.putalpha(alpha_mask)

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
        export_start_ms: int = 0,
        export_end_ms: int | None = None,
    ) -> str:
        """Build FFmpeg overlay filter for text PNG.

        Args:
            input_idx: FFmpeg input index for the text PNG
            clip: Clip data containing transform, timing
            base_output: Current filter graph output label
            text_idx: Text index for output label
            export_start_ms: Start of export range in ms (clips are offset relative to this)
            export_end_ms: End of export range in ms

        Returns:
            FFmpeg filter string
        """
        transform = clip.get("transform", {})

        # Get position (center offset from canvas center)
        center_x = transform.get("x", 0)
        center_y = transform.get("y", 0)

        # Get timing and adjust for export range
        start_ms = clip.get("start_ms", 0)
        duration_ms = clip.get("duration_ms", 0)
        clip_end_ms = start_ms + duration_ms

        # Adjust timing relative to export_start_ms
        adjusted_start_ms = max(0, start_ms - export_start_ms)
        adjusted_end_ms = clip_end_ms - export_start_ms

        start_s = adjusted_start_ms / 1000
        end_s = adjusted_end_ms / 1000
        enable_expr = self._build_enable_expr(start_s, end_s)

        output_label = f"text{text_idx}"

        # Convert center coords to top-left for FFmpeg overlay
        overlay_x = f"(main_w/2)+({int(center_x)})-(overlay_w/2)"
        overlay_y = f"(main_h/2)+({int(center_y)})-(overlay_h/2)"

        filter_str = (
            f"[{base_output}][{input_idx}:v]overlay="
            f"x={overlay_x}:y={overlay_y}:"
            f"enable='{enable_expr}'"
            f"[{output_label}]"
        )

        logger.info(f"[TEXT] Overlay filter: input={input_idx}, pos=({center_x},{center_y}), enable={enable_expr} (original: {start_ms}-{clip_end_ms}ms)")
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

    def build_final_command(
        self,
        video_path: str,
        audio_path: str,
        output_path: str,
        duration_ms: int,
    ) -> list[str]:
        """Build FFmpeg command for final encode (combine video + audio) without executing it.

        Args:
            video_path: Path to composite video file
            audio_path: Path to mixed audio file
            output_path: Path for final output MP4
            duration_ms: Total duration in milliseconds

        Returns:
            FFmpeg command as list[str]
        """
        duration_s = duration_ms / 1000
        return [
            self.ffmpeg_path,
            "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", settings.render_audio_bitrate,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-t", str(duration_s),
            "-movflags", "+faststart",
            output_path,
        ]

    async def _encode_final(
        self,
        video_path: str,
        audio_path: str,
        output_path: str,
        duration_ms: int,
    ) -> str:
        """Combine video and audio into final output."""
        print(f"[ENCODE FINAL] duration_ms={duration_ms}, duration_s={duration_ms / 1000}", flush=True)

        cmd = self.build_final_command(video_path, audio_path, output_path, duration_ms)

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
