"""Celery task for video rendering."""

import os
import shutil
import tempfile
from datetime import datetime, timezone
from uuid import UUID

from celery import states
from google.cloud import storage
from sqlalchemy import select

from src.celery_app import celery_app
from src.config import get_settings
from src.models.asset import Asset
from src.models.database import get_sync_db
from src.models.project import Project
from src.models.render_job import RenderJob
from src.render.pipeline import RenderPipeline

settings = get_settings()


def _get_storage_client() -> storage.Client:
    """Get GCS storage client."""
    if settings.gcs_project_id:
        return storage.Client(project=settings.gcs_project_id)
    return storage.Client()


def _download_asset(client: storage.Client, storage_key: str, local_path: str) -> str:
    """Download an asset from GCS."""
    bucket = client.bucket(settings.gcs_bucket_name)
    blob = bucket.blob(storage_key)
    blob.download_to_filename(local_path)
    return local_path


def _upload_file(client: storage.Client, local_path: str, storage_key: str) -> str:
    """Upload a file to GCS."""
    bucket = client.bucket(settings.gcs_bucket_name)
    blob = bucket.blob(storage_key)
    blob.upload_from_filename(local_path)
    return f"https://storage.googleapis.com/{settings.gcs_bucket_name}/{storage_key}"


def _generate_signed_url(client: storage.Client, storage_key: str, expiration_hours: int = 24) -> str:
    """Generate a signed download URL."""
    from datetime import timedelta
    bucket = client.bucket(settings.gcs_bucket_name)
    blob = bucket.blob(storage_key)
    return blob.generate_signed_url(
        version="v4",
        expiration=timedelta(hours=expiration_hours),
        method="GET",
    )


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def render_video_task(self, render_job_id: str) -> dict:
    """
    Execute video rendering as a Celery task.

    Args:
        render_job_id: UUID of the RenderJob to process

    Returns:
        dict with status and output information
    """
    temp_dir = None

    try:
        with get_sync_db() as db:
            # Load render job
            result = db.execute(
                select(RenderJob).where(RenderJob.id == UUID(render_job_id))
            )
            render_job = result.scalar_one_or_none()

            if render_job is None:
                return {"status": "error", "message": "Render job not found"}

            if render_job.status == "cancelled":
                return {"status": "cancelled", "message": "Job was cancelled"}

            # Update job status
            render_job.status = "processing"
            render_job.started_at = datetime.now(timezone.utc)
            render_job.current_stage = "Loading project"
            db.commit()

            # Update Celery task state
            self.update_state(state="PROGRESS", meta={"progress": 5, "stage": "Loading project"})

            # Load project
            result = db.execute(
                select(Project).where(Project.id == render_job.project_id)
            )
            project = result.scalar_one_or_none()

            if project is None:
                render_job.status = "failed"
                render_job.error_message = "Project not found"
                db.commit()
                return {"status": "error", "message": "Project not found"}

            timeline_data = project.timeline_data
            if not timeline_data:
                render_job.status = "failed"
                render_job.error_message = "No timeline data in project"
                db.commit()
                return {"status": "error", "message": "No timeline data"}

            # Collect all asset IDs from timeline
            asset_ids = set()

            # From audio tracks
            for track in timeline_data.get("audio_tracks", []):
                for clip in track.get("clips", []):
                    if clip.get("asset_id"):
                        asset_ids.add(clip["asset_id"])

            # From video layers
            for layer in timeline_data.get("layers", []):
                for clip in layer.get("clips", []):
                    if clip.get("asset_id"):
                        asset_ids.add(clip["asset_id"])

            if not asset_ids:
                render_job.status = "failed"
                render_job.error_message = "No assets in timeline"
                db.commit()
                return {"status": "error", "message": "No assets in timeline"}

            # Load assets from database
            result = db.execute(
                select(Asset).where(Asset.id.in_([UUID(aid) for aid in asset_ids]))
            )
            assets_db = {str(a.id): a for a in result.scalars().all()}

            # Update progress
            render_job.current_stage = "Downloading assets"
            render_job.progress = 10
            db.commit()
            self.update_state(state="PROGRESS", meta={"progress": 10, "stage": "Downloading assets"})

            # Create temp directory for rendering
            temp_dir = tempfile.mkdtemp(prefix=f"douga_render_{render_job_id}_")
            assets_dir = os.path.join(temp_dir, "assets")
            output_dir = os.path.join(temp_dir, "output")
            os.makedirs(assets_dir, exist_ok=True)
            os.makedirs(output_dir, exist_ok=True)

            # Download assets from GCS
            storage_client = _get_storage_client()
            assets_local: dict[str, str] = {}

            for asset_id, asset in assets_db.items():
                ext = asset.storage_key.rsplit(".", 1)[-1] if "." in asset.storage_key else ""
                local_path = os.path.join(assets_dir, f"{asset_id}.{ext}")
                _download_asset(storage_client, asset.storage_key, local_path)
                assets_local[asset_id] = local_path

            # Update progress
            render_job.current_stage = "Rendering video"
            render_job.progress = 30
            db.commit()
            self.update_state(state="PROGRESS", meta={"progress": 30, "stage": "Rendering video"})

            # Create render pipeline with progress callback
            def progress_callback(progress: int, stage: str):
                # Map pipeline progress (0-100) to our progress (30-90)
                mapped_progress = 30 + int(progress * 0.6)
                render_job.progress = mapped_progress
                render_job.current_stage = stage
                db.commit()
                self.update_state(
                    state="PROGRESS",
                    meta={"progress": mapped_progress, "stage": stage}
                )

            pipeline = RenderPipeline(job_id=render_job_id, project_id=str(project.id))
            pipeline.set_progress_callback(progress_callback)

            # Output path
            output_filename = f"{project.name.replace(' ', '_')}_render.mp4"
            output_path = os.path.join(output_dir, output_filename)

            # Run render pipeline (sync wrapper for async method)
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    pipeline.render(timeline_data, assets_local, output_path)
                )
            finally:
                loop.close()

            # Update progress
            render_job.current_stage = "Uploading output"
            render_job.progress = 90
            db.commit()
            self.update_state(state="PROGRESS", meta={"progress": 90, "stage": "Uploading output"})

            # Upload to GCS
            output_storage_key = f"projects/{project.id}/renders/{render_job.id}/{output_filename}"
            _upload_file(storage_client, output_path, output_storage_key)

            # Generate signed download URL
            download_url = _generate_signed_url(storage_client, output_storage_key)

            # Get output file size
            output_size = os.path.getsize(output_path)

            # Update render job as completed
            render_job.status = "completed"
            render_job.progress = 100
            render_job.current_stage = "Complete"
            render_job.completed_at = datetime.now(timezone.utc)
            render_job.output_key = output_storage_key
            render_job.output_url = download_url
            render_job.output_size = output_size
            db.commit()

            return {
                "status": "completed",
                "output_url": download_url,
                "output_key": output_storage_key,
                "output_size": output_size,
            }

    except Exception as e:
        # Handle failure
        with get_sync_db() as db:
            result = db.execute(
                select(RenderJob).where(RenderJob.id == UUID(render_job_id))
            )
            render_job = result.scalar_one_or_none()

            if render_job:
                render_job.status = "failed"
                render_job.error_message = str(e)
                render_job.retry_count = self.request.retries
                db.commit()

        # Retry on certain errors
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e)

        return {"status": "failed", "error": str(e)}

    finally:
        # Cleanup temp directory
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass  # Ignore cleanup errors
