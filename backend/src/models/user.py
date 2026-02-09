from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    firebase_uid: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Relationships
    projects: Mapped[list["Project"]] = relationship(  # noqa: F821
        "Project", back_populates="user", cascade="all, delete-orphan"
    )
    api_keys: Mapped[list["APIKey"]] = relationship(  # noqa: F821
        "APIKey", back_populates="user", cascade="all, delete-orphan"
    )
    memberships: Mapped[list["ProjectMember"]] = relationship(  # noqa: F821
        "ProjectMember", back_populates="user", cascade="all, delete-orphan",
        foreign_keys="ProjectMember.user_id",
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"
