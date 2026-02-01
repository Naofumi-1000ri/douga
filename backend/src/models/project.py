import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin


class Project(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "projects"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Video settings
    width: Mapped[int] = mapped_column(Integer, default=1920)
    height: Mapped[int] = mapped_column(Integer, default=1080)
    fps: Mapped[int] = mapped_column(Integer, default=30)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    # Timeline data (JSONB for flexibility)
    timeline_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        default=lambda: {
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
        },
    )

    # AI Video production
    video_brief: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    video_plan: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Status
    status: Mapped[str] = mapped_column(String(50), default="draft")
    thumbnail_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # AI Settings
    ai_provider: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)  # "openai" | "gemini" | "anthropic"

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="projects")  # noqa: F821
    assets: Mapped[list["Asset"]] = relationship(  # noqa: F821
        "Asset", back_populates="project", cascade="all, delete-orphan"
    )
    asset_folders: Mapped[list["AssetFolder"]] = relationship(  # noqa: F821
        "AssetFolder", back_populates="project", cascade="all, delete-orphan"
    )
    render_jobs: Mapped[list["RenderJob"]] = relationship(  # noqa: F821
        "RenderJob", back_populates="project", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Project {self.name}>"
