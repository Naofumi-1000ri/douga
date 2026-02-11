import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin


class SequenceSnapshot(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "sequence_snapshots"

    sequence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sequences.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    timeline_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Relationships
    sequence: Mapped["Sequence"] = relationship("Sequence", back_populates="snapshots")  # noqa: F821

    def __repr__(self) -> str:
        return f"<SequenceSnapshot {self.name} (sequence={self.sequence_id})>"
