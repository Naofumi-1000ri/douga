import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin


# Default timeline data matching Project.timeline_data default
def _default_timeline_data() -> dict:
    return {
        "version": "1.0",
        "duration_ms": 0,
        "layers": [
            {"id": str(uuid.uuid4()), "name": "Text", "type": "text", "order": 4, "visible": True, "locked": False, "clips": []},
            {"id": str(uuid.uuid4()), "name": "Effects", "type": "effects", "order": 3, "visible": True, "locked": False, "clips": []},
            {"id": str(uuid.uuid4()), "name": "Avatar", "type": "avatar", "order": 2, "visible": True, "locked": False, "clips": []},
            {"id": str(uuid.uuid4()), "name": "Content", "type": "content", "order": 1, "visible": True, "locked": False, "clips": []},
            {"id": str(uuid.uuid4()), "name": "Background", "type": "background", "order": 0, "visible": True, "locked": False, "clips": []},
        ],
        "audio_tracks": [
            {"id": str(uuid.uuid4()), "name": "Narration", "type": "narration", "volume": 1.0, "muted": False, "clips": []},
            {"id": str(uuid.uuid4()), "name": "BGM", "type": "bgm", "volume": 0.3, "muted": False, "ducking": {"enabled": True, "duck_to": 0.1, "attack_ms": 200, "release_ms": 500}, "clips": []},
            {"id": str(uuid.uuid4()), "name": "SE", "type": "se", "volume": 0.8, "muted": False, "clips": []},
        ],
    }


class Sequence(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "sequences"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    timeline_data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=_default_timeline_data)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    locked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    thumbnail_storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="sequences")  # noqa: F821
    lock_holder: Mapped["User"] = relationship("User", foreign_keys=[locked_by])  # noqa: F821
    snapshots: Mapped[list["SequenceSnapshot"]] = relationship(  # noqa: F821
        "SequenceSnapshot", back_populates="sequence", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Sequence {self.name} (project={self.project_id})>"
