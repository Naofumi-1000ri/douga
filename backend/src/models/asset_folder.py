import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin


class AssetFolder(Base, UUIDMixin, TimestampMixin):
    """Folder for organizing assets within a project."""

    __tablename__ = "asset_folders"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="asset_folders")  # noqa: F821
    assets: Mapped[list["Asset"]] = relationship("Asset", back_populates="folder")  # noqa: F821

    def __repr__(self) -> str:
        return f"<AssetFolder {self.name}>"
