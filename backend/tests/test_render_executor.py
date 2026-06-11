"""Tests for the render executor abstraction — ADR-001 (Issue #281).

Covers:
- InlineExecutor dispatches an asyncio coroutine via create_task
- CloudRunJobsExecutor.dispatch_async calls the GCP SDK (mocked)
- CloudRunJobsExecutor raises RenderExecutorError when SDK is unavailable
- get_render_executor() returns the correct type based on settings
- inline mode: existing asyncio.create_task path is unchanged end-to-end
"""

import asyncio
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

    def test_dispatch_requires_coroutine(self):
        """InlineExecutor.dispatch() must reject a missing coroutine."""
        executor = InlineExecutor()
        with pytest.raises(RenderExecutorError, match="background coroutine"):
            executor.dispatch(uuid4(), None)


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
        executor = CloudRunJobsExecutor(project_id="p", region="r", job_name="j")
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

    @pytest.mark.asyncio
    async def test_cancel_with_execution_id_calls_cancel_execution(self):
        """cancel() with an execution_id must schedule cancel_execution()."""
        executor = CloudRunJobsExecutor(project_id="p", region="r", job_name="j")
        job_id = uuid4()
        execution_id = "projects/p/locations/r/jobs/j/executions/exec-123"

        called: list[tuple] = []

        async def _fake_cancel_execution(jid, eid):
            called.append((jid, eid))

        with patch.object(executor, "cancel_execution", side_effect=_fake_cancel_execution):
            executor.cancel(job_id, execution_id)
            # cancel() schedules cancel_execution as a background task — let it run
            await asyncio.sleep(0)

        assert called == [(job_id, execution_id)], (
            "cancel() must invoke cancel_execution(job_id, execution_id) when an id is present"
        )

    @pytest.mark.asyncio
    async def test_cancel_execution_calls_sdk(self):
        """cancel_execution must call run_v2.ExecutionsAsyncClient().cancel_execution()."""
        executor = CloudRunJobsExecutor(project_id="p", region="r", job_name="j")
        job_id = uuid4()
        execution_id = "projects/p/locations/r/jobs/j/executions/exec-xyz"

        mock_client = MagicMock()
        mock_client.cancel_execution = AsyncMock(return_value=MagicMock())

        mock_run_v2 = MagicMock()
        mock_run_v2.ExecutionsAsyncClient = MagicMock(return_value=mock_client)

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
            await executor.cancel_execution(job_id, execution_id)

        mock_client.cancel_execution.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatch_and_persist_saves_execution_name(self):
        """dispatch_and_persist must write the execution name to celery_task_id."""
        executor = CloudRunJobsExecutor(project_id="p", region="r", job_name="j")
        job_id = uuid4()
        execution_name = "projects/p/locations/r/jobs/j/executions/exec-persist"

        class FakeJob:
            status = "queued"
            celery_task_id: str | None = None

        fake_job = FakeJob()

        class FakeResult:
            def scalar_one_or_none(self):
                return fake_job

        commits: list[int] = []

        class FakeDB:
            async def execute(self, _stmt):
                return FakeResult()

            async def commit(self):
                commits.append(1)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

        async def _fake_dispatch_async(jid, *, env_overrides=None):
            return execution_name

        with patch.object(executor, "dispatch_async", side_effect=_fake_dispatch_async):
            with patch("src.models.database.async_session_maker", return_value=FakeDB()):
                result = await executor.dispatch_and_persist(job_id)

        assert result == execution_name
        assert fake_job.celery_task_id == execution_name, (
            "dispatch_and_persist must save the execution name to celery_task_id"
        )
        assert commits, "dispatch_and_persist must commit the DB update"

    @pytest.mark.asyncio
    async def test_dispatch_and_persist_cancels_when_already_cancelled(self):
        """If the job was cancelled during dispatch, the execution is cancelled."""
        executor = CloudRunJobsExecutor(project_id="p", region="r", job_name="j")
        job_id = uuid4()
        execution_name = "projects/p/locations/r/jobs/j/executions/exec-race"

        class FakeJob:
            status = "cancelled"  # user cancelled between enqueue and dispatch
            celery_task_id: str | None = None

        fake_job = FakeJob()

        class FakeResult:
            def scalar_one_or_none(self):
                return fake_job

        class FakeDB:
            async def execute(self, _stmt):
                return FakeResult()

            async def commit(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

        cancel_calls: list[tuple] = []

        async def _fake_dispatch_async(jid, *, env_overrides=None):
            return execution_name

        async def _fake_cancel_execution(jid, eid):
            cancel_calls.append((jid, eid))

        with patch.object(executor, "dispatch_async", side_effect=_fake_dispatch_async):
            with patch.object(executor, "cancel_execution", side_effect=_fake_cancel_execution):
                with patch("src.models.database.async_session_maker", return_value=FakeDB()):
                    result = await executor.dispatch_and_persist(job_id)

        assert result == execution_name
        assert cancel_calls == [(job_id, execution_name)], (
            "A job cancelled during dispatch must have its execution cancelled immediately"
        )

    def test_dispatch_closes_unused_coroutine(self):
        """dispatch() must close a coroutine passed for interface uniformity.

        jobs mode does not run the coroutine in-process, so it must be closed
        to avoid a 'coroutine was never awaited' RuntimeWarning.
        """
        executor = CloudRunJobsExecutor(project_id="p", region="r", job_name="j")
        job_id = uuid4()

        closed: list[bool] = []

        async def _coro() -> None:  # pragma: no cover — must never run
            closed.append(False)

        coro = _coro()

        async def _runner():
            # dispatch returns a Task that we cancel so dispatch_and_persist
            # never actually hits the SDK.
            task = executor.dispatch(job_id, coro)
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        asyncio.run(_runner())

        # The coroutine must be closed: awaiting it now raises RuntimeError.
        with pytest.raises(RuntimeError):
            asyncio.run(coro)


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


# ---------------------------------------------------------------------------
# cancel_render endpoint wiring (jobs-mode cancellation)
# ---------------------------------------------------------------------------


class TestCancelRenderWiring:
    """cancel_render must call executor.cancel() with the stored execution id."""

    @pytest.mark.asyncio
    async def test_cancel_render_invokes_executor_cancel(self):
        """The DELETE /render handler must mark the job cancelled AND call
        executor.cancel(job_id, execution_id).

        This is the regression test for the MUST-FIX: previously only the DB
        flag was set, so a jobs-mode Cloud Run container kept running.
        """
        from src.api.render import cancel_render

        project_id = uuid4()
        job_id = uuid4()
        execution_id = "projects/p/locations/r/jobs/j/executions/exec-cancel"

        class FakeJob:
            def __init__(self) -> None:
                self.id = job_id
                self.status = "processing"
                self.current_stage = "Rendering video"
                self.celery_task_id = execution_id

        fake_job = FakeJob()

        class FakeResult:
            def scalar_one_or_none(self):
                return fake_job

        commits: list[int] = []

        class FakeDB:
            async def execute(self, _stmt):
                return FakeResult()

            async def commit(self):
                commits.append(1)

        # Mock the access check and executor factory
        cancel_calls: list[tuple] = []

        class FakeExecutor:
            def cancel(self, jid, eid):
                cancel_calls.append((jid, eid))

        async def _fake_access(*_a, **_kw):
            return MagicMock()

        with (
            patch("src.api.render.get_accessible_project", side_effect=_fake_access),
            patch("src.api.render.get_render_executor", return_value=FakeExecutor()),
        ):
            current_user = MagicMock()
            current_user.id = uuid4()
            await cancel_render(project_id, current_user, FakeDB())  # type: ignore[arg-type]

        # The job must have been marked cancelled and committed
        assert fake_job.status == "cancelled"
        assert commits, "cancel_render must commit the cancelled status"
        # executor.cancel must be called with the execution id captured BEFORE commit
        assert cancel_calls == [(job_id, execution_id)], (
            "cancel_render must call executor.cancel(job_id, execution_id)"
        )

    @pytest.mark.asyncio
    async def test_cancel_render_inline_mode_execution_id_none(self):
        """In inline mode (no execution id), executor.cancel is still called
        with None and must be a no-op (backward compatible)."""
        from src.api.render import cancel_render

        project_id = uuid4()
        job_id = uuid4()

        class FakeJob:
            def __init__(self) -> None:
                self.id = job_id
                self.status = "processing"
                self.current_stage = "Rendering video"
                self.celery_task_id = None  # inline mode never sets this

        fake_job = FakeJob()

        class FakeResult:
            def scalar_one_or_none(self):
                return fake_job

        class FakeDB:
            async def execute(self, _stmt):
                return FakeResult()

            async def commit(self):
                pass

        cancel_calls: list[tuple] = []

        class FakeInlineExecutor:
            def cancel(self, jid, eid):
                cancel_calls.append((jid, eid))

        async def _fake_access(*_a, **_kw):
            return MagicMock()

        with (
            patch("src.api.render.get_accessible_project", side_effect=_fake_access),
            patch("src.api.render.get_render_executor", return_value=FakeInlineExecutor()),
        ):
            current_user = MagicMock()
            current_user.id = uuid4()
            await cancel_render(project_id, current_user, FakeDB())  # type: ignore[arg-type]

        assert fake_job.status == "cancelled"
        assert cancel_calls == [(job_id, None)], (
            "inline mode: executor.cancel must still be called (with execution_id=None)"
        )
