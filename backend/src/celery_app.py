"""Celery application configuration."""

from celery import Celery

from src.config import get_settings

settings = get_settings()

celery_app = Celery(
    "douga",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["src.tasks.render_task"],
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Tokyo",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,  # 1 hour max per task
    task_soft_time_limit=3300,  # Soft limit 55 minutes
    worker_prefetch_multiplier=1,  # Process one task at a time
    task_acks_late=True,  # Acknowledge after task completion
    task_reject_on_worker_lost=True,  # Requeue if worker dies
)
