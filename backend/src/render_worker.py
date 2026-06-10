"""Cloud Run Jobs worker entry point — ADR-001 (Issue #281).

This module is invoked as ``python -m src.render_worker`` inside a Cloud Run
Jobs container.  It reads the ``JOB_ID`` environment variable (injected by
:class:`src.render.executor.CloudRunJobsExecutor`), loads the render job
from the database, and executes the full render pipeline.

Usage
-----
Inside the container:

    python -m src.render_worker

Or directly (for local testing / debugging):

    JOB_ID=<uuid> uv run python -m src.render_worker

Environment variables
---------------------
JOB_ID (required)
    The UUID of the ``RenderJob`` row to execute.  All render parameters
    (timeline data, project dimensions, etc.) are loaded from the
    ``timeline_snapshot`` and ``render_params`` JSONB columns stored
    at enqueue time by the API endpoint.

All other application settings (DATABASE_URL, GCS_BUCKET_NAME, etc.) are
read from the normal ``src.config.Settings`` mechanism — i.e. from .env or
environment variables injected by Cloud Run.

Exit codes
----------
0   — Render completed successfully.
1   — Fatal error (job not found, DB unreachable, missing snapshot, etc.).
2   — The job was cancelled before the worker could start.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from uuid import UUID

logger = logging.getLogger(__name__)


async def _run_worker(job_id: UUID) -> int:
    """Execute the render for *job_id* and return an exit code."""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from src.api.render import _run_render_background, _update_job_progress
    from src.logging_config import configure_logging
    from src.models.database import async_session_maker, init_db
    from src.models.render_job import RenderJob

    configure_logging()
    logger.info("[WORKER] Starting render worker for job %s", job_id)

    # Initialise DB engine (needed when running outside FastAPI startup)
    await init_db()

    # ------------------------------------------------------------------
    # Load job row
    # ------------------------------------------------------------------
    async with async_session_maker() as db:
        result = await db.execute(select(RenderJob).where(RenderJob.id == job_id))
        job = result.scalar_one_or_none()

        if job is None:
            logger.error("[WORKER] Job %s not found in database", job_id)
            return 1

        if job.status == "cancelled":
            logger.info("[WORKER] Job %s was cancelled before worker started", job_id)
            return 2

        if job.status not in ("queued", "processing"):
            logger.warning(
                "[WORKER] Job %s has unexpected status %s — aborting", job_id, job.status
            )
            return 1

        # Validate timeline snapshot (required for jobs mode)
        timeline_data = job.timeline_snapshot
        render_params = job.render_params or {}

        if not timeline_data:
            logger.error("[WORKER] Job %s has no timeline_snapshot stored", job_id)
            await _update_job_progress(
                job_id, 0, "Failed", "failed", "No timeline_snapshot in job (jobs mode requires it)"
            )
            return 1

        # Snapshot render parameters
        render_duration_ms: int = render_params.get("render_duration_ms", 0)
        audio_only: bool = bool(render_params.get("audio_only", False))
        project_name: str = render_params.get("project_name", "")
        project_width: int = int(render_params.get("project_width", 1920))
        project_height: int = int(render_params.get("project_height", 1080))
        project_fps: int = int(render_params.get("project_fps", 30))
        project_id: UUID = job.project_id

        # Mark as processing (in case it's still queued)
        if job.status == "queued":
            job.status = "processing"
            job.started_at = datetime.now(UTC)
            await db.commit()
            logger.info("[WORKER] Job %s transitioned queued → processing", job_id)

    # ------------------------------------------------------------------
    # Delegate to the shared _run_render_background coroutine
    # ------------------------------------------------------------------
    logger.info(
        "[WORKER] Executing render for job %s (project_id=%s audio_only=%s duration_ms=%d)",
        job_id,
        project_id,
        audio_only,
        render_duration_ms,
    )

    await _run_render_background(
        job_id=job_id,
        project_id=project_id,
        project_name=project_name,
        project_width=project_width,
        project_height=project_height,
        project_fps=project_fps,
        timeline_data=timeline_data,
        duration_ms=render_duration_ms,
        audio_only=audio_only,
    )

    logger.info("[WORKER] Render for job %s completed", job_id)
    return 0


def main() -> None:
    """Entry point invoked by ``python -m src.render_worker``."""
    job_id_str = os.environ.get("JOB_ID", "").strip()
    if not job_id_str:
        print("ERROR: JOB_ID environment variable is required", file=sys.stderr)
        sys.exit(1)

    try:
        job_id = UUID(job_id_str)
    except ValueError:
        print(f"ERROR: JOB_ID={job_id_str!r} is not a valid UUID", file=sys.stderr)
        sys.exit(1)

    exit_code = asyncio.run(_run_worker(job_id))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
