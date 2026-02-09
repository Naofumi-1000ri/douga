"""Centralized project access control for collaborative editing."""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.project import Project
from src.models.project_member import ProjectMember


async def get_accessible_project(
    project_id: UUID,
    user_id: UUID,
    db: AsyncSession,
    require_role: str | None = None,
) -> Project:
    """Get a project if the user has access.

    Access is granted if:
    1. The user is the project owner (project.user_id == user_id), OR
    2. The user is an accepted member of the project

    Args:
        project_id: The project to access
        user_id: The user requesting access
        db: Database session
        require_role: If set, require this specific role (e.g., "owner")

    Returns:
        The Project if accessible

    Raises:
        HTTPException 404: If project not found or user has no access
        HTTPException 403: If user lacks required role
    """
    # Get the project
    result = await db.execute(
        select(Project).where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # Check if user is the legacy owner
    if project.user_id == user_id:
        return project

    # If owner role is required and user is not the owner, deny
    if require_role == "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the project owner can perform this action",
        )

    # Check membership
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
            ProjectMember.accepted_at.isnot(None),
        )
    )
    member = result.scalar_one_or_none()

    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    return project


async def list_accessible_project_ids(
    user_id: UUID,
    db: AsyncSession,
) -> list[UUID]:
    """Return all project IDs the user can access (owned + accepted memberships)."""
    result = await db.execute(
        select(ProjectMember.project_id).where(
            ProjectMember.user_id == user_id,
            ProjectMember.accepted_at.isnot(None),
        )
    )
    return [row[0] for row in result.all()]
