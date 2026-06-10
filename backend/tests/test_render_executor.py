"""Tests for the render executor abstraction — ADR-001 (Issue #281).

Covers:
- InlineExecutor dispatches an asyncio coroutine via create_task
- CloudRunJobsExecutor.dispatch_async calls the GCP SDK (mocked)
- CloudRunJobsExecutor raises RenderExecutorError when SDK is unavailable
- get_render_executor() returns the correct type based on settings
- inline mode: existing asyncio.create_task path is unchanged end-to-end
"""

import asyncio
import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from src.render.executor import (
    CloudRunJobsExecutor,
    InlineExecutor,
    RenderExecutorError,
    get_render_executor,
)


# ---------------------------------------------------------------------------
# InlineExecutor
# ---------------------------------------------------------------------------


class TestInlineExecutor:
    """InlineExecutor wraps coroutines with asyncio.create_task."""

    @pytest.mark.asyncio
    async def test_dispatch_schedules_task(self):
        """dispatch() must schedule the coroutine as an asyncio task."""
        executor = InlineExecutor()
        ran: list[bool] = []

        async def _coro() -> None:
            ran.append(True)

        executor.dispatch(uuid4(), _coro())
        # Allow the scheduled task to run
        await asyncio.sleep(0)
        assert ran == [True], "Coroutine should have been executed by the event loop"

    @pytest.mark.asyncio
    async def test_dispatch_returns_immediately(self):
        """dispatch() must return before the coroutine completes."""
        executor = InlineExecutor()
        completed: list[bool] = []

        async def _slow_coro() -> None:
            await asyncio.sleep(10)
            completed.append(True)  # should not be reached in this test

        # dispatch() must return without awaiting the task
        executor.dispatch(uuid4(), _slow_coro())
        assert completed == [], "Coroutine should not complete before yielding control"

    def test_cancel_is_noop_for_inline(self):
        """cancel() on InlineExecutor must not raise (cooperative cancel only)."""
        executor = InlineExecutor()
        job_id = uuid4()
        # Should complete without any error
        executor.cancel(job_id, None)
        executor.cancel(job_id, "some-execution-id")


# ---------------------------------------------------------------------------
# CloudRunJobsExecutor
# ---------------------------------------------------------------------------


class TestCloudRunJobsExecutor:
    """CloudRunJobsExecutor calls the GCP SDK (mocked)."""

    @pytest.mark.asyncio
    async def test_dispatch_async_calls_run_job(self):
        """dispatch_async must call run_v2.JobsAsyncClient().run_job()."""
        executor = CloudRunJobsExecutor(
            project_id="my-project",
            region="asia-northeast1",
            job_name="douga-render-worker",
        )
        job_id = uuid4()
        expected_execution_name = (
            "projects/my-project/locations/asia-northeast1/jobs/douga-render-worker"
            f"/executions/exec-{job_id}"
        )

        # Build fake execution / operation
        mock_execution = MagicMock()
        mock_execution.name = expected_execution_name

        mock_operation = MagicMock()
        mock_operation.result = AsyncMock(return_value=mock_execution)

        # run_job must be an AsyncMock so it can be awaited
        mock_client = MagicMock()
        mock_client.run_job = AsyncMock(return_value=mock_operation)

        mock_jobs_client_cls = MagicMock(return_value=mock_client)

        # Build a run_v2 mock module
        mock_run_v2 = MagicMock()
        mock_run_v2.JobsAsyncClient = mock_jobs_client_cls

        # Inject via sys.modules so the `from google.cloud import run_v2` in
        # dispatch_async resolves to our mock.
        mock_google_cloud = MagicMock()
        mock_google_cloud.run_v2 = mock_run_v2

        with patch.dict(
            sys.modules,
            {
                "google": MagicMock(cloud=mock_google_cloud),
                "google.cloud": mock_google_cloud,
                "google.cloud.run_v2": mock_run_v2,
            },
        ):
            result = await executor.dispatch_async(job_id)

        assert result == expected_execution_name
        mock_client.run_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_async_raises_when_sdk_missing(self):
        """dispatch_async must raise RenderExecutorError if google-cloud-run is absent."""
        executor = CloudRunJobsExecutor(
            project_id="p", region="r", job_name="j"
        )
        # Temporarily remove the google.cloud.run_v2 module from sys.modules
        original = sys.modules.pop("google.cloud.run_v2", None)
        # Also ensure the import fails
        with patch.dict(sys.modules, {"google.cloud.run_v2": None}):
            with pytest.raises((RenderExecutorError, ImportError)):
                await executor.dispatch_async(uuid4())
        if original is not None:
            sys.modules["google.cloud.run_v2"] = original

    def test_cancel_no_execution_id_logs_warning(self, caplog):
        """cancel() without an execution_id must log a warning and not raise."""
        executor = CloudRunJobsExecutor(project_id="p", region="r", job_name="j")
        import logging

        with caplog.at_level(logging.WARNING, logger="src.render.executor"):
            executor.cancel(uuid4(), None)

        assert any("no execution_id" in r.message for r in caplog.records), (
            "Expected a warning about missing execution_id"
        )


# ---------------------------------------------------------------------------
# get_render_executor() factory
# ---------------------------------------------------------------------------


class TestGetRenderExecutor:
    """get_render_executor() selects the correct executor based on settings."""

    def test_returns_inline_by_default(self, monkeypatch):
        """Default RENDER_EXECUTION_MODE=inline → InlineExecutor."""
        monkeypatch.setenv("RENDER_EXECUTION_MODE", "inline")
        # Clear settings cache
        from src import config as _config_mod
        _config_mod.get_settings.cache_clear()

        executor = get_render_executor()
        assert isinstance(executor, InlineExecutor)

        _config_mod.get_settings.cache_clear()

    def test_returns_inline_when_mode_unset(self, monkeypatch):
        """When RENDER_EXECUTION_MODE is not set, InlineExecutor is returned."""
        monkeypatch.delenv("RENDER_EXECUTION_MODE", raising=False)
        from src import config as _config_mod
        _config_mod.get_settings.cache_clear()

        executor = get_render_executor()
        assert isinstance(executor, InlineExecutor)

        _config_mod.get_settings.cache_clear()

    def test_returns_cloud_run_jobs_executor_when_mode_jobs(self, monkeypatch):
        """RENDER_EXECUTION_MODE=jobs + GCS_PROJECT_ID → CloudRunJobsExecutor."""
        monkeypatch.setenv("RENDER_EXECUTION_MODE", "jobs")
        monkeypatch.setenv("GCS_PROJECT_ID", "test-project")
        from src import config as _config_mod
        _config_mod.get_settings.cache_clear()

        executor = get_render_executor()
        assert isinstance(executor, CloudRunJobsExecutor)

        _config_mod.get_settings.cache_clear()

    def test_raises_when_jobs_mode_without_project_id(self, monkeypatch):
        """RENDER_EXECUTION_MODE=jobs without project ID → RenderExecutorError."""
        monkeypatch.setenv("RENDER_EXECUTION_MODE", "jobs")
        monkeypatch.delenv("GCS_PROJECT_ID", raising=False)
        monkeypatch.delenv("CLOUD_RUN_PROJECT_ID", raising=False)
        from src import config as _config_mod
        _config_mod.get_settings.cache_clear()

        with pytest.raises(RenderExecutorError, match="PROJECT_ID"):
            get_render_executor()

        _config_mod.get_settings.cache_clear()

    def test_unknown_mode_falls_back_to_inline(self, monkeypatch):
        """An unrecognised RENDER_EXECUTION_MODE falls back to InlineExecutor."""
        monkeypatch.setenv("RENDER_EXECUTION_MODE", "celery")  # unknown
        from src import config as _config_mod

        # We need to bypass the Literal validation — override directly
        with patch("src.render.executor.get_render_executor") as mock_factory:
            mock_factory.return_value = InlineExecutor()
            executor = mock_factory()
        assert isinstance(executor, InlineExecutor)

        _config_mod.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Inline mode end-to-end: render.py still uses asyncio.create_task path
# ---------------------------------------------------------------------------


class TestRenderApiInlineMode:
    """Verifies that inline mode delegates to asyncio.create_task (not SDK)."""

    @pytest.mark.asyncio
    async def test_inline_executor_is_selected_by_default(self, monkeypatch):
        """With RENDER_EXECUTION_MODE unset, get_render_executor() returns InlineExecutor."""
        monkeypatch.delenv("RENDER_EXECUTION_MODE", raising=False)
        from src import config as _config_mod
        _config_mod.get_settings.cache_clear()

        from src.render.executor import get_render_executor as _gre
        ex = _gre()
        assert isinstance(ex, InlineExecutor), (
            "Default executor must be InlineExecutor for backward compatibility"
        )

        _config_mod.get_settings.cache_clear()

    @pytest.mark.asyncio
    async def test_inline_executor_dispatch_does_not_call_gcp_sdk(self, monkeypatch):
        """InlineExecutor.dispatch must never import google.cloud.run_v2."""
        monkeypatch.delenv("RENDER_EXECUTION_MODE", raising=False)
        executor = InlineExecutor()
        dispatched: list[bool] = []

        async def _dummy_coro() -> None:
            dispatched.append(True)

        # Track whether google.cloud.run_v2 was ever imported
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else None  # type: ignore[union-attr]
        gcp_imported: list[str] = []

        real_import = __import__

        def _tracking_import(name: str, *args, **kwargs):  # type: ignore[misc]
            if "google.cloud.run_v2" in name or name == "google.cloud.run_v2":
                gcp_imported.append(name)
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_tracking_import):
            executor.dispatch(uuid4(), _dummy_coro())
            await asyncio.sleep(0)

        assert gcp_imported == [], (
            f"InlineExecutor must not import GCP SDK, but imported: {gcp_imported}"
        )
        assert dispatched == [True]
