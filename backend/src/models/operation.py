"""ProjectOperation model for operation history and rollback.

Tracks all mutations to a project's timeline for:
- Operation history query
- Rollback support
- Audit trail
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, UUIDMixin

if TYPE_CHECKING:
    from src.models.project import Project
    from src.models.user import User


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
    __table_args__ = (
        # GIN indexes for efficient JSONB array contains queries (@> operator)
        Index(
            "idx_project_operations_affected_clips",
            "affected_clips",
            postgresql_using="gin",
        ),
        Index(
            "idx_project_operations_affected_layers",
            "affected_layers",
            postgresql_using="gin",
        ),
        Index(
            "idx_project_operations_affected_audio_clips",
            "affected_audio_clips",
            postgresql_using="gin",
        ),
        # B-tree indexes for common filter columns (named with idx_ prefix to
        # match the legacy run_migrations() names in the existing production DB)
        Index("idx_project_operations_project_id", "project_id"),
        Index("idx_project_operations_operation_type", "operation_type"),
        Index("idx_project_operations_user_id", "user_id"),
        # Composite index for project version lookups
        Index("idx_project_operations_project_version", "project_id", "project_version"),
        # Partial UNIQUE index for idempotency enforcement scoped by user
        Index(
            "idx_project_operations_idempotency_key_unique",
            "user_id",
            "idempotency_key",
            unique=True,
            postgresql_where="idempotency_key IS NOT NULL",
        ),
    )

    # Foreign key to project
    # Note: ix_project_operations_project_id is a duplicate of idx_project_operations_project_id
    # (both exist in the DB). We track only the idx_ variant in ORM metadata.
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Operation metadata
    # Note: ix_project_operations_operation_type duplicates idx_project_operations_operation_type.
    operation_type: Mapped[str] = mapped_column(String(50), nullable=False)
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

    # Idempotency replay: persisted response for cross-instance dedup
    response_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Request context
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Note: ix_project_operations_user_id is tracked as a BASELINE_ONLY_INDEX
    # duplicate of idx_project_operations_user_id (declared in __table_args__).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Project version at time of operation (for collaborative editing)
    # Note: ix_project_operations_project_version is a BASELINE_ONLY_INDEX.
    project_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Timestamp — ix_project_operations_created_at is a BASELINE_ONLY_INDEX.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    project: Mapped["Project"] = relationship(  # noqa: F821
        "Project", back_populates="operations"
    )
    user: Mapped["User | None"] = relationship("User")  # noqa: F821

    def __repr__(self) -> str:
        return f"<ProjectOperation {self.operation_type} {self.id}>"
