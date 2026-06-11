import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from src.models.project import Project


class RenderJob(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "render_jobs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Status: queued, processing, completed, failed, cancelled
    status: Mapped[str] = mapped_column(String(50), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    current_stage: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Output
    output_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Timing
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Executor tracking (formerly celery_task_id).
    # In jobs mode: stores the Cloud Run Jobs Execution name (e.g.
    # "projects/.../jobs/.../executions/...") so cancellations can target it.
    # In inline mode: unused (kept for schema backward compatibility).
    celery_task_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Error handling
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    # Timeline snapshot — persisted for Cloud Run Jobs workers.
    # Stores the fully-resolved timeline_data dict (post-normalisation) along
    # with auxiliary render parameters, so the worker container can reconstruct
    # the render without re-fetching the sequence or re-running normalisation.
    # NULL in inline mode (the data is passed in-process, no snapshot needed).
    timeline_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Render parameters snapshot (audio_only, render_duration_ms, etc.)
    # Stored alongside timeline_snapshot for jobs mode.
    render_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="render_jobs")  # noqa: F821

    def __repr__(self) -> str:
        return f"<RenderJob {self.id} ({self.status})>"
