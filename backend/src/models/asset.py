import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin


class Asset(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "assets"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Type: video, audio, image
    type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Subtype: avatar, background, slide, narration, bgm, se, effect
    subtype: Mapped[str] = mapped_column(String(50), nullable=False)

    # Storage
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Media metadata
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)

    # Audio specific
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channels: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Processing flags
    has_alpha: Mapped[bool] = mapped_column(Boolean, default=False)
    chroma_key_color: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Internal asset flag (e.g., extracted audio from video - not shown to user)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="assets")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Asset {self.name} ({self.type}/{self.subtype})>"
