"""ProjectOperation model for operation history and rollback.

Tracks all mutations to a project's timeline for:
- Operation history query
- Rollback support
- Audit trail
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, UUIDMixin


class ProjectOperation(Base, UUIDMixin):
    """Record of a project mutation operation.

    Each write operation (add_clip, move_clip, etc.) creates one record.
    Used for operation history, rollback, and audit.

    Attributes:
        id: Operation ID (UUID)
        project_id: FK to projects table
        operation_type: Type of operation (add_clip, move_clip, batch, semantic, etc.)
        source: Source of operation (api_v1, ai_chat, batch, semantic)

        affected_clips: List of clip IDs affected
        affected_layers: List of layer IDs affected
        affected_audio_clips: List of audio clip IDs affected

        diff: Diff information (changes only, not full snapshot)
        request_summary: Summary of the request (endpoint, method, key_params)
        result_summary: Summary of the result (created_ids, modified_ids, etc.)

        rollback_data: Data needed to reverse this operation
        rollback_available: Whether this operation can be rolled back
        rolled_back: Whether this operation has been rolled back
        rolled_back_at: When rollback occurred
        rolled_back_by: Operation ID that performed the rollback

        success: Whether the operation succeeded
        error_code: Error code if failed
        error_message: Error message if failed

        idempotency_key: Idempotency key from request header
        user_id: User who performed the operation
        created_at: When operation was recorded
    """

    __tablename__ = "project_operations"

    # Foreign key to project
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Operation metadata
    operation_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="api_v1")

    # Affected entities (for efficient querying)
    affected_clips: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    affected_layers: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    affected_audio_clips: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)

    # Diff information (changes only)
    diff: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Request/response summaries
    request_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Rollback support
    rollback_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    rollback_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rolled_back: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Operation result
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Request context
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Project version at time of operation (for collaborative editing)
    project_version: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)

    # Timestamp
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    # Relationships
    project: Mapped["Project"] = relationship(  # noqa: F821
        "Project", back_populates="operations"
    )
    user: Mapped["User | None"] = relationship("User")  # noqa: F821

    def __repr__(self) -> str:
        return f"<ProjectOperation {self.operation_type} {self.id}>"
