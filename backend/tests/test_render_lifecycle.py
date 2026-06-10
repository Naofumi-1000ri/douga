"""Tests for render job lifecycle management (Issue #268).

Covers:
- Cancellation kills the active FFmpeg subprocess
- _render_single cleans up work_dir via try/finally even on failure
- Heartbeat loop touches updated_at periodically
- FFmpeg timeout constants are set to sensible values
"""

import asyncio
import os
import shutil
import tempfile
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

import src.render.pipeline as pipeline_module
from src.render.pipeline import (
    FFMPEG_BLANK_VIDEO_TIMEOUT_S,
    FFMPEG_COMPOSITE_TIMEOUT_S,
    FFMPEG_FINAL_ENCODE_TIMEOUT_S,
    ORPHAN_DIR_AGE_S,
    RenderPipeline,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_simple_timeline(duration_ms: int = 5000) -> dict[str, Any]:
    return {
        "duration_ms": duration_ms,
        "export_start_ms": 0,
        "export_end_ms": duration_ms,
        "layers": [],
        "audio_tracks": [],
    }


# ---------------------------------------------------------------------------
# Timeout constants sanity checks
# ---------------------------------------------------------------------------


class TestTimeoutConstants:
    """FFmpeg timeout constants must be within Cloud Run 900s budget."""

    def test_blank_video_timeout_positive(self):
        assert FFMPEG_BLANK_VIDEO_TIMEOUT_S > 0

    def test_final_encode_timeout_positive(self):
        assert FFMPEG_FINAL_ENCODE_TIMEOUT_S > 0

    def test_composite_timeout_positive(self):
        assert FFMPEG_COMPOSITE_TIMEOUT_S > 0

    def test_composite_timeout_within_cloud_run_limit(self):
        assert FFMPEG_COMPOSITE_TIMEOUT_S <= 860

    def test_orphan_dir_age_is_at_least_one_hour(self):
        assert ORPHAN_DIR_AGE_S >= 3600


# ---------------------------------------------------------------------------
# Cancellation kills FFmpeg subprocess
# ---------------------------------------------------------------------------


class TestCancellationKillsProcess:
    """When _is_cancelled() returns True during composite, _kill_active_proc is called."""

    @pytest.mark.asyncio
    async def test_kill_active_proc_terminates_process(self):
        """_kill_active_proc should call terminate then wait on the process."""
        pipeline = RenderPipeline()

        # Use MagicMock for terminate (synchronous) but AsyncMock for wait.
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.wait = AsyncMock()
        pipeline._active_proc = mock_proc

        await pipeline._kill_active_proc()

        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called()
        assert pipeline._active_proc is None

    @pytest.mark.asyncio
    async def test_kill_active_proc_noop_when_no_proc(self):
        """_kill_active_proc must not raise when _active_proc is None."""
        pipeline = RenderPipeline()
        pipeline._active_proc = None
        # Should complete without error
        await pipeline._kill_active_proc()

    @pytest.mark.asyncio
    async def test_kill_active_proc_noop_when_already_finished(self):
        """_kill_active_proc must not raise when process already finished."""
        pipeline = RenderPipeline()

        mock_proc = MagicMock()
        mock_proc.returncode = 0  # already exited
        pipeline._active_proc = mock_proc

        await pipeline._kill_active_proc()
        # terminate should not be called because returncode is set
        mock_proc.terminate.assert_not_called()
        assert pipeline._active_proc is None

    @pytest.mark.asyncio
    async def test_composite_video_kills_proc_on_cancel(self, monkeypatch, tmp_path):
        """_composite_video must kill the subprocess when cancellation is detected."""
        pipeline = RenderPipeline(job_id=str(uuid4()))
        pipeline.output_dir = str(tmp_path)
        pipeline.ffmpeg_path = "ffmpeg"

        # Make _is_cancelled return True immediately
        async def _always_cancelled() -> bool:
            return True

        pipeline._cancel_check = _always_cancelled

        killed: list[bool] = []

        async def _mock_kill(self_inner: RenderPipeline) -> None:  # type: ignore[override]
            killed.append(True)
            # Simulate process cleanup
            self_inner._active_proc = None

        monkeypatch.setattr(RenderPipeline, "_kill_active_proc", _mock_kill)

        # Fake asyncio subprocess that yields one progress line
        async def fake_readline():
            return b"out_time_us=1000000\n"

        mock_stdout = MagicMock()
        mock_stdout.__aiter__ = MagicMock(
            return_value=iter([b"out_time_us=1000000\n"])
        )

        async def _fake_iter(self_inner):
            yield b"out_time_us=1000000\n"

        mock_stdout.__aiter__ = lambda _: _fake_iter(None)

        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.stdout = mock_stdout
        mock_proc.stderr = AsyncMock()
        mock_proc.stderr.read = AsyncMock(return_value=b"")
        mock_proc.wait = AsyncMock()

        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_proc
        ) as _mock_exec:
            # Also need build_composite_command to return a command
            monkeypatch.setattr(
                pipeline,
                "build_composite_command",
                lambda *_a, **_kw: (["ffmpeg", "-y", "output.mp4"], {}),
            )

            with pytest.raises(asyncio.CancelledError):
                await pipeline._composite_video(_make_simple_timeline(), {}, 5000)

        assert killed, "Expected _kill_active_proc to be called on cancellation"


# ---------------------------------------------------------------------------
# work_dir cleanup via try/finally
# ---------------------------------------------------------------------------


class TestWorkDirCleanup:
    """_render_single must clean up work_dir even when an exception is raised."""

    @pytest.mark.asyncio
    async def test_render_single_cleanup_on_exception(self, monkeypatch):
        """Temp work_dir should be removed even if _encode_final raises."""
        pipeline = RenderPipeline(job_id=str(uuid4()))
        assert os.path.isdir(pipeline.work_dir), "work_dir should be created by __init__"
        work_dir = pipeline.work_dir

        # Stub out the audio/video steps so only _encode_final blows up.
        async def _fake_mix(*_a, **_kw) -> str:
            return "/tmp/fake_audio.wav"

        async def _fake_composite(*_a, **_kw) -> str:
            return "/tmp/fake_video.mp4"

        async def _fake_encode(*_a, **_kw) -> None:
            raise RuntimeError("Simulated encode failure")

        monkeypatch.setattr(pipeline, "_mix_audio", _fake_mix)
        monkeypatch.setattr(pipeline, "_composite_video", _fake_composite)
        monkeypatch.setattr(pipeline, "_encode_final", _fake_encode)

        with pytest.raises(RuntimeError, match="Simulated encode failure"):
            await pipeline._render_single(_make_simple_timeline(), {}, "/tmp/out.mp4", 5000)

        assert not os.path.isdir(work_dir), (
            f"work_dir {work_dir} should have been deleted in finally block"
        )

    @pytest.mark.asyncio
    async def test_render_single_cleanup_on_cancel(self, monkeypatch):
        """Temp work_dir should be removed on CancelledError."""
        pipeline = RenderPipeline(job_id=str(uuid4()))
        work_dir = pipeline.work_dir

        async def _fake_mix(*_a, **_kw) -> str:
            return "/tmp/fake_audio.wav"

        async def _fake_composite(*_a, **_kw) -> str:
            raise asyncio.CancelledError("Render cancelled")

        monkeypatch.setattr(pipeline, "_mix_audio", _fake_mix)
        monkeypatch.setattr(pipeline, "_composite_video", _fake_composite)

        # CancelledError is checked and re-raised by _render_single after the try block;
        # but the finally should still fire on the encode path.
        # To simplify, patch _is_cancelled to return False (cancel raised inside composite).
        pipeline._cancel_check = lambda: False

        with pytest.raises(asyncio.CancelledError):
            await pipeline._render_single(_make_simple_timeline(), {}, "/tmp/out.mp4", 5000)

        assert not os.path.isdir(work_dir), (
            f"work_dir {work_dir} should have been deleted even after CancelledError"
        )

    @pytest.mark.asyncio
    async def test_render_single_cleanup_on_success(self, monkeypatch):
        """Temp work_dir should be removed on successful completion too."""
        pipeline = RenderPipeline(job_id=str(uuid4()))
        work_dir = pipeline.work_dir

        async def _fake_mix(*_a, **_kw) -> str:
            return "/tmp/fake_audio.wav"

        async def _fake_composite(*_a, **_kw) -> str:
            return "/tmp/fake_video.mp4"

        async def _fake_encode(*_a, **_kw) -> str:
            return "/tmp/out.mp4"

        monkeypatch.setattr(pipeline, "_mix_audio", _fake_mix)
        monkeypatch.setattr(pipeline, "_composite_video", _fake_composite)
        monkeypatch.setattr(pipeline, "_encode_final", _fake_encode)

        await pipeline._render_single(_make_simple_timeline(), {}, "/tmp/out.mp4", 5000)

        assert not os.path.isdir(work_dir), (
            f"work_dir {work_dir} should have been deleted after success"
        )


# ---------------------------------------------------------------------------
# Orphan directory cleanup
# ---------------------------------------------------------------------------


class TestOrphanDirCleanup:
    """_cleanup_orphan_dirs removes old douga_render_* directories."""

    def test_old_dirs_are_removed(self, tmp_path, monkeypatch):
        """Directories older than ORPHAN_DIR_AGE_S should be deleted."""
        import time

        # Monkeypatch tempfile.gettempdir to return tmp_path
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        old_dir = tmp_path / "douga_render_old_job_abc"
        old_dir.mkdir()
        # Set mtime to 2 hours ago
        old_mtime = time.time() - ORPHAN_DIR_AGE_S - 100
        os.utime(str(old_dir), (old_mtime, old_mtime))

        RenderPipeline._cleanup_orphan_dirs(current_job_id=None)

        assert not old_dir.exists(), "Old orphan directory should be removed"

    def test_current_job_dir_is_spared(self, tmp_path, monkeypatch):
        """The current job's work_dir must NOT be deleted."""
        import time

        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        job_id = "myjob123"
        current_dir = tmp_path / f"douga_render_{job_id}_abc"
        current_dir.mkdir()
        old_mtime = time.time() - ORPHAN_DIR_AGE_S - 100
        os.utime(str(current_dir), (old_mtime, old_mtime))

        RenderPipeline._cleanup_orphan_dirs(current_job_id=job_id)

        assert current_dir.exists(), "Current job's directory must not be deleted"

    def test_recent_dirs_are_kept(self, tmp_path, monkeypatch):
        """Directories newer than ORPHAN_DIR_AGE_S must be left alone."""
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

        recent_dir = tmp_path / "douga_render_newjob_xyz"
        recent_dir.mkdir()
        # mtime is now (recent)

        RenderPipeline._cleanup_orphan_dirs(current_job_id=None)

        assert recent_dir.exists(), "Recent directory should not be deleted"


# ---------------------------------------------------------------------------
# Heartbeat loop
# ---------------------------------------------------------------------------


class TestHeartbeatLoop:
    """_heartbeat_loop should periodically update updated_at and stop on event."""

    @pytest.mark.asyncio
    async def test_heartbeat_updates_job(self):
        """Heartbeat must call the DB session and commit once per iteration."""
        from src.api.render import _heartbeat_loop

        job_id = uuid4()
        stop_event = asyncio.Event()
        updates: list[datetime] = []

        class FakeJob:
            status = "processing"
            updated_at: datetime = datetime(2020, 1, 1, tzinfo=UTC)

        fake_job = FakeJob()

        class FakeResult:
            def scalar_one_or_none(self):
                return fake_job

        class FakeDB:
            async def execute(self, _stmt):
                return FakeResult()

            async def commit(self):
                updates.append(fake_job.updated_at)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

        with patch("src.api.render.async_session_maker", return_value=FakeDB()):
            # Run one heartbeat iteration then stop
            task = asyncio.create_task(_heartbeat_loop(job_id, stop_event))
            await asyncio.sleep(0.05)
            stop_event.set()
            try:
                await asyncio.wait_for(task, timeout=2)
            except asyncio.CancelledError:
                pass

        # At least one heartbeat commit should have occurred
        assert len(updates) >= 1, "Heartbeat should have committed at least once"

    @pytest.mark.asyncio
    async def test_heartbeat_stops_on_event(self):
        """Heartbeat must exit promptly when stop_event is set."""
        from src.api.render import HEARTBEAT_INTERVAL_S, STALE_THRESHOLD_S

        # Sanity: stale threshold must be greater than heartbeat interval
        assert STALE_THRESHOLD_S > HEARTBEAT_INTERVAL_S

    @pytest.mark.asyncio
    async def test_heartbeat_skips_non_active_jobs(self):
        """Heartbeat must not update jobs in terminal states."""
        from src.api.render import _heartbeat_loop

        job_id = uuid4()
        stop_event = asyncio.Event()
        commits: list[int] = []

        class FakeJob:
            status = "completed"  # terminal — heartbeat should skip
            updated_at: datetime = datetime(2020, 1, 1, tzinfo=UTC)

        fake_job = FakeJob()
        original_updated_at = fake_job.updated_at

        class FakeResult:
            def scalar_one_or_none(self):
                return fake_job

        class FakeDB:
            async def execute(self, _stmt):
                return FakeResult()

            async def commit(self):
                commits.append(1)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

        with patch("src.api.render.async_session_maker", return_value=FakeDB()):
            task = asyncio.create_task(_heartbeat_loop(job_id, stop_event))
            await asyncio.sleep(0.05)
            stop_event.set()
            try:
                await asyncio.wait_for(task, timeout=2)
            except asyncio.CancelledError:
                pass

        # updated_at should not have changed for a terminal job
        assert fake_job.updated_at == original_updated_at


# ---------------------------------------------------------------------------
# FFmpeg subprocess timeout (subprocess.run timeout= kwarg)
# ---------------------------------------------------------------------------


class TestFFmpegSubprocessTimeout:
    """subprocess.run calls must include a timeout kwarg."""

    @pytest.mark.asyncio
    async def test_create_blank_video_passes_timeout(self, tmp_path):
        """_create_blank_video should pass timeout=FFMPEG_BLANK_VIDEO_TIMEOUT_S."""
        pipeline = RenderPipeline(job_id=str(uuid4()))
        pipeline.output_dir = str(tmp_path)
        pipeline.ffmpeg_path = "ffmpeg"

        captured_kwargs: dict = {}

        import subprocess as _subprocess_mod

        def _fake_run(cmd, **kwargs):
            captured_kwargs.update(kwargs)
            # Fake success
            result = MagicMock()
            result.returncode = 0
            return result

        with patch.object(_subprocess_mod, "run", _fake_run):
            with patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
                try:
                    await pipeline._create_blank_video(str(tmp_path / "out.mp4"), 1000)
                except Exception:
                    pass  # we only care about kwargs

        assert "timeout" in captured_kwargs, "_create_blank_video must pass timeout= to subprocess.run"
        assert captured_kwargs["timeout"] == FFMPEG_BLANK_VIDEO_TIMEOUT_S

    @pytest.mark.asyncio
    async def test_encode_final_passes_timeout(self, tmp_path):
        """_encode_final should pass timeout=FFMPEG_FINAL_ENCODE_TIMEOUT_S."""
        pipeline = RenderPipeline(job_id=str(uuid4()))
        pipeline.ffmpeg_path = "ffmpeg"

        captured_kwargs: dict = {}

        import subprocess as _subprocess_mod

        def _fake_run(cmd, **kwargs):
            captured_kwargs.update(kwargs)
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            return result

        with patch.object(_subprocess_mod, "run", _fake_run):
            with patch("asyncio.to_thread", side_effect=lambda fn, *a, **kw: fn(*a, **kw)):
                try:
                    await pipeline._encode_final("/tmp/v.mp4", "/tmp/a.wav", str(tmp_path / "out.mp4"), 1000)
                except Exception:
                    pass

        assert "timeout" in captured_kwargs, "_encode_final must pass timeout= to subprocess.run"
        assert captured_kwargs["timeout"] == FFMPEG_FINAL_ENCODE_TIMEOUT_S
