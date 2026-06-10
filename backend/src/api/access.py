"""Centralized project access control for collaborative editing.

Role hierarchy (lowest → highest privilege):
  viewer  — read-only access; cannot modify the project or its content
  editor  — can read and write (timeline edits, asset uploads, sequences, AI, etc.)
  owner   — full control including project settings (ai_api_key, ai_provider) and
             member management

Backward-compatibility guarantee:
  Existing ProjectMember rows only ever contain role="editor" (the database
  default — model default and the only value members.py has historically
  written) or, in principle, "owner"/"viewer" going forward.  Every value
  present in existing data ranks at or above its intended level, so no
  existing member loses access from the fail-closed default below.

Fail-closed policy for unknown roles (#261 review finding C):
  Any role value NOT present in _ROLE_RANK is treated as *viewer*
  (read-only).  If a future migration introduces a new role (e.g.
  "commenter") before this API layer learns about it, the safe failure mode
  is to deny writes — not to silently grant them.  When adding a new role
  value to the database, _ROLE_RANK MUST be updated in the same change.

The ``require_role`` parameter uses a *minimum-required-role* semantics:
  - "editor"  → viewer is denied; editor and owner are allowed
  - "owner"   → only the project owner (project.user_id) is allowed
                 (members with role="owner" in the members table are NOT included;
                  that column is reserved for future use and not currently assigned)
"""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.project import Project
from src.models.project_member import ProjectMember

# Ordered role hierarchy: index 0 is least privileged.
# WARNING: fail-closed — when introducing a new role value in the database,
# add it here in the same change.  Unknown roles fall back to viewer rank
# (read-only); a missing entry demotes that role instead of granting writes.
_ROLE_RANK: dict[str, int] = {
    "viewer": 0,
    "editor": 1,
    "owner": 2,
}
# Fail-closed: unknown roles get viewer-level (read-only) access.
# Existing DB rows only contain "editor" (default) so no current member is
# affected by this default.
_DEFAULT_ROLE_RANK = _ROLE_RANK["viewer"]


def _role_rank(role: str) -> int:
    return _ROLE_RANK.get(role, _DEFAULT_ROLE_RANK)


async def get_accessible_project(
    project_id: UUID,
    user_id: UUID,
    db: AsyncSession,
    require_role: str | None = None,
) -> Project:
    """Get a project if the user has access.

    Access is granted if:
    1. The user is the project owner (project.user_id == user_id), OR
    2. The user is an accepted member of the project with sufficient role

    Args:
        project_id: The project to access
        user_id: The user requesting access
        db: Database session
        require_role: Minimum role required.  Supported values:
            - None / "viewer": any authenticated member may access
            - "editor": viewer members are denied (write operations)
            - "owner": only the project creator (project.user_id) is allowed

    Returns:
        The Project if accessible

    Raises:
        HTTPException 404: If project not found or user has no access
        HTTPException 403: If user lacks required role
    """
    # Get the project
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # The project creator is always the owner — they can do everything.
    if project.user_id == user_id:
        return project

    # Owner-only operations are restricted to the project creator.
    if require_role == "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the project owner can perform this action",
        )

    # Check membership
    member_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
            ProjectMember.accepted_at.isnot(None),
        )
    )
    member: ProjectMember | None = member_result.scalar_one_or_none()

    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # Enforce role minimum for write operations.
    if require_role is not None and require_role != "viewer":
        required_rank = _role_rank(require_role)
        member_rank = _role_rank(member.role)
        if member_rank < required_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires '{require_role}' access or higher "
                f"(your role: '{member.role}')",
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
