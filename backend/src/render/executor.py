"""Render executor abstraction — ADR-001 (Issue #281).

Provides a pluggable execution back-end for render jobs.  The active mode is
controlled by the ``RENDER_EXECUTION_MODE`` environment variable (default:
``inline``).

Modes
-----
inline (default)
    Runs the render inside the current API instance via
    ``asyncio.create_task``.  Behaviour is 100% backward-compatible with
    the code that existed before this module was introduced.

jobs
    Launches a Cloud Run Jobs execution for each render.  The worker
    container runs ``python -m src.render_worker <job_id>`` and reads the
    job payload from the database.  Requires real GCP infrastructure
    (Cloud Run Job resource) to be present; raises ``RenderExecutorError``
    when the infrastructure is not reachable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


class RenderExecutorError(RuntimeError):
    """Raised when the executor cannot dispatch a render job."""


# ---------------------------------------------------------------------------
# Inline executor (default — backward-compatible)
# ---------------------------------------------------------------------------


class InlineExecutor:
    """Dispatches render jobs as asyncio background tasks in the current process.

    This is functionally equivalent to the original ``asyncio.create_task``
    call in ``src/api/render.py``.  No infrastructure changes are required.
    """

    def dispatch(
        self,
        job_id: UUID,
        background_coro: Any = None,
    ) -> None:
        """Schedule *background_coro* as a fire-and-forget asyncio task.

        Parameters
        ----------
        job_id:
            The ``RenderJob.id`` being dispatched (used for logging only).
        background_coro:
            An awaitable coroutine that performs the render.  It is wrapped
            in ``asyncio.create_task`` so the caller returns immediately.
            Must be provided in inline mode (the default ``None`` exists only
            to keep a uniform signature with :class:`CloudRunJobsExecutor`).
        """
        if background_coro is None:
            raise RenderExecutorError(
                "InlineExecutor.dispatch() requires a background coroutine "
                "(inline mode runs the render in-process)."
            )
        logger.info("[RENDER][inline] Scheduling job %s as asyncio.create_task", job_id)
        asyncio.create_task(background_coro)

    def cancel(self, job_id: UUID, execution_id: str | None) -> None:
        """Cancel a running inline job (cooperative via DB flag only).

        In inline mode the render loop polls ``_check_cancelled(job_id)``
        and will stop at the next checkpoint.  There is no way to forcibly
        terminate the underlying asyncio task from outside — callers must
        mark the DB status as ``'cancelled'`` before calling this method.

        Parameters
        ----------
        job_id:
            The ``RenderJob.id`` to cancel.
        execution_id:
            Ignored in inline mode (no external execution reference).
        """
        logger.info(
            "[RENDER][inline] Cancellation signal acknowledged for job %s "
            "(cooperative: DB flag must be set to 'cancelled' before this call)",
            job_id,
        )


# ---------------------------------------------------------------------------
# Cloud Run Jobs executor
# ---------------------------------------------------------------------------


class CloudRunJobsExecutor:
    """Dispatches render jobs as Cloud Run Jobs executions.

    Each call to :meth:`dispatch` launches a new Cloud Run Jobs *execution*
    for the configured job resource.  The worker container is started with
    ``JOB_ID=<job_id>`` as an environment variable and runs
    ``python -m src.render_worker``.

    Infrastructure requirements (one-time, human setup):
    - Cloud Run Job resource must exist (see ``docs/ops/deploy.md``).
    - The job's service account must have Cloud SQL / GCS / Secret Manager
      access identical to the API service account.

    When the GCP SDK (``google-cloud-run``) is not installed or the API call
    fails, :class:`RenderExecutorError` is raised.  Because
    ``RENDER_EXECUTION_MODE=jobs`` is **not** the default, this error will
    only surface after a deliberate operator action to switch modes.
    """

    def __init__(self, project_id: str, region: str, job_name: str) -> None:
        self._project_id = project_id
        self._region = region
        self._job_name = job_name

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    async def dispatch_async(
        self,
        job_id: UUID,
        *,
        env_overrides: dict[str, str] | None = None,
    ) -> str:
        """Launch a Cloud Run Jobs execution for *job_id*.

        Parameters
        ----------
        job_id:
            The ``RenderJob.id`` to execute.
        env_overrides:
            Additional environment variables to set on the execution
            (merged on top of the job's default env).  ``JOB_ID`` is
            always injected automatically.

        Returns
        -------
        str
            The Cloud Run Execution name (e.g.
            ``projects/.../jobs/.../executions/...``).  Should be stored
            in ``RenderJob.celery_task_id`` as the execution reference.

        Raises
        ------
        RenderExecutorError
            When the GCP SDK is unavailable or the API call fails.
        """
        try:
            from google.cloud import run_v2
        except ImportError as exc:
            raise RenderExecutorError(
                "google-cloud-run SDK not installed. "
                "Run: uv add google-cloud-run  (jobs mode requires this package)."
            ) from exc

        env: dict[str, str] = {"JOB_ID": str(job_id)}
        if env_overrides:
            env.update(env_overrides)

        client = run_v2.JobsAsyncClient()
        job_name = f"projects/{self._project_id}/locations/{self._region}/jobs/{self._job_name}"

        override = run_v2.RunJobRequest.Overrides(
            container_overrides=[
                run_v2.RunJobRequest.Overrides.ContainerOverride(
                    env=[run_v2.EnvVar(name=k, value=v) for k, v in env.items()],
                )
            ]
        )
        request = run_v2.RunJobRequest(name=job_name, overrides=override)

        try:
            operation = await client.run_job(request=request)
            # The long-running operation resolves to an Execution resource.
            execution = await operation.result()
            execution_name: str = execution.name
            logger.info(
                "[RENDER][jobs] Dispatched job %s → execution %s",
                job_id,
                execution_name,
            )
            return execution_name
        except Exception as exc:
            raise RenderExecutorError(
                f"Failed to launch Cloud Run Jobs execution for job {job_id}: {exc}"
            ) from exc

    async def dispatch_and_persist(
        self,
        job_id: UUID,
        *,
        env_overrides: dict[str, str] | None = None,
    ) -> str:
        """Launch a Cloud Run Jobs execution and persist its name to the DB.

        This is the coroutine scheduled by :meth:`dispatch`.  After the
        execution is created, its name is written to
        ``RenderJob.celery_task_id`` so that :meth:`cancel` can later target
        it.  If the job no longer exists or has already been cancelled, the
        execution name is still returned but not persisted.

        Parameters
        ----------
        job_id:
            The ``RenderJob.id`` to dispatch.
        env_overrides:
            Forwarded to :meth:`dispatch_async`.

        Returns
        -------
        str
            The Cloud Run Execution name.

        Raises
        ------
        RenderExecutorError
            When the GCP SDK is unavailable or the API call fails.
        """
        execution_name = await self.dispatch_async(job_id, env_overrides=env_overrides)

        # Persist the execution name so cancel_render can target it.
        # Imported lazily to avoid a circular import at module load time.
        from sqlalchemy import select

        from src.models.database import async_session_maker
        from src.models.render_job import RenderJob

        async with async_session_maker() as db:
            result = await db.execute(select(RenderJob).where(RenderJob.id == job_id))
            job = result.scalar_one_or_none()
            if job is None:
                logger.warning(
                    "[RENDER][jobs] Job %s vanished before execution name could be saved",
                    job_id,
                )
            elif job.status == "cancelled":
                # The user cancelled between enqueue and dispatch; cancel the
                # freshly-created execution immediately.
                logger.info(
                    "[RENDER][jobs] Job %s was cancelled during dispatch — cancelling execution %s",
                    job_id,
                    execution_name,
                )
                job.celery_task_id = execution_name
                await db.commit()
                await self.cancel_execution(job_id, execution_name)
            else:
                job.celery_task_id = execution_name
                await db.commit()
                logger.info("[RENDER][jobs] Saved execution %s to job %s", execution_name, job_id)

        return execution_name

    def dispatch(
        self,
        job_id: UUID,
        background_coro: Any = None,
        *,
        env_overrides: dict[str, str] | None = None,
    ) -> asyncio.Task[str]:
        """Schedule a Cloud Run Jobs execution as an asyncio background task.

        Parameters
        ----------
        job_id:
            The ``RenderJob.id`` to dispatch.
        background_coro:
            Ignored in jobs mode (the render runs in a separate container,
            not in this process).  Accepted to keep the interface uniform
            with :class:`InlineExecutor`.  Callers should pass ``None`` in
            jobs mode to avoid constructing an unused coroutine.
        env_overrides:
            Forwarded to :meth:`dispatch_and_persist`.

        Returns
        -------
        asyncio.Task[str]
            A background task that resolves to the Execution name string and
            persists it to ``RenderJob.celery_task_id`` as a side effect.
        """
        # If a real coroutine was passed (uniform-interface caller), close it
        # so it does not trigger a "coroutine was never awaited" warning.
        if background_coro is not None and asyncio.iscoroutine(background_coro):
            background_coro.close()

        logger.info("[RENDER][jobs] Scheduling Cloud Run Jobs execution for job %s", job_id)
        return asyncio.create_task(self.dispatch_and_persist(job_id, env_overrides=env_overrides))

    async def cancel_execution(self, job_id: UUID, execution_id: str) -> None:
        """Cancel a running Cloud Run Jobs execution.

        Parameters
        ----------
        job_id:
            The ``RenderJob.id`` being cancelled (for logging).
        execution_id:
            The Cloud Run Execution name stored in
            ``RenderJob.celery_task_id``.

        Raises
        ------
        RenderExecutorError
            When the GCP SDK is unavailable or the API call fails.
        """
        try:
            from google.cloud import run_v2
        except ImportError as exc:
            raise RenderExecutorError("google-cloud-run SDK not installed.") from exc

        client = run_v2.ExecutionsAsyncClient()
        request = run_v2.CancelExecutionRequest(name=execution_id)
        try:
            await client.cancel_execution(request=request)
            logger.info(
                "[RENDER][jobs] Cancelled execution %s for job %s",
                execution_id,
                job_id,
            )
        except Exception as exc:
            raise RenderExecutorError(
                f"Failed to cancel execution {execution_id} for job {job_id}: {exc}"
            ) from exc

    def cancel(self, job_id: UUID, execution_id: str | None) -> None:
        """Synchronous wrapper — schedules the async cancel as a background task.

        This is a fire-and-forget; callers should already have updated the DB
        status to ``'cancelled'`` before calling this.
        """
        if not execution_id:
            logger.warning("[RENDER][jobs] Cannot cancel job %s: no execution_id stored", job_id)
            return
        asyncio.create_task(self.cancel_execution(job_id, execution_id))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_render_executor() -> InlineExecutor | CloudRunJobsExecutor:
    """Return the configured render executor.

    Reads ``RENDER_EXECUTION_MODE`` from the application settings.
    Defaults to :class:`InlineExecutor` when the mode is ``"inline"`` or
    unrecognised.

    Returns
    -------
    InlineExecutor | CloudRunJobsExecutor
        The executor instance.
    """
    from src.config import get_settings

    settings = get_settings()
    mode = settings.render_execution_mode

    if mode == "jobs":
        project_id = settings.cloud_run_project_id or settings.gcs_project_id
        if not project_id:
            raise RenderExecutorError(
                "RENDER_EXECUTION_MODE=jobs requires CLOUD_RUN_PROJECT_ID "
                "(or GCS_PROJECT_ID) to be set."
            )
        return CloudRunJobsExecutor(
            project_id=project_id,
            region=settings.cloud_run_region,
            job_name=settings.cloud_run_render_job_name,
        )

    # Default: inline (backward-compatible)
    if mode != "inline":
        logger.warning("Unknown RENDER_EXECUTION_MODE=%r — falling back to 'inline'", mode)
    return InlineExecutor()
