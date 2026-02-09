"""Members API for project collaboration."""

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import CurrentUser, DbSession
from src.models.project import Project
from src.models.project_member import ProjectMember
from src.models.user import User
from src.schemas.member import InvitationResponse, InviteMemberRequest, MemberResponse

logger = logging.getLogger(__name__)

router = APIRouter()


async def _require_project_member(
    project_id: UUID, user_id: UUID, db: AsyncSession, require_role: str | None = None
) -> tuple[Project, ProjectMember | None]:
    """Verify access. Returns (project, member_record_or_None)."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Owner always has access
    if project.user_id == user_id:
        if require_role and require_role != "owner":
            pass  # owner can do anything
        return project, None

    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
            ProjectMember.accepted_at.isnot(None),
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    if require_role == "owner" and member.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required")

    return project, member


@router.get("/projects/{project_id}/members", response_model=list[MemberResponse])
async def list_members(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> list[MemberResponse]:
    """List all members of a project."""
    await _require_project_member(project_id, current_user.id, db)

    result = await db.execute(
        select(ProjectMember, User)
        .join(User, ProjectMember.user_id == User.id)
        .where(ProjectMember.project_id == project_id)
        .order_by(ProjectMember.invited_at)
    )
    rows = result.all()

    return [
        MemberResponse(
            id=member.id,
            project_id=member.project_id,
            user_id=member.user_id,
            role=member.role,
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            invited_at=member.invited_at,
            accepted_at=member.accepted_at,
        )
        for member, user in rows
    ]


@router.post("/projects/{project_id}/members", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
async def invite_member(
    project_id: UUID,
    request: InviteMemberRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> MemberResponse:
    """Invite a user to a project by email. Owner only."""
    project, _ = await _require_project_member(project_id, current_user.id, db, require_role="owner")

    # Find the user by email
    result = await db.execute(select(User).where(User.email == request.email))
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. They must have an account first.",
        )

    # Can't invite yourself
    if target_user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot invite yourself",
        )

    # Check existing membership
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == target_user.id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member or has a pending invitation",
        )

    member = ProjectMember(
        project_id=project_id,
        user_id=target_user.id,
        role="editor",
        invited_by=current_user.id,
    )
    db.add(member)
    await db.flush()
    await db.refresh(member)

    logger.info(f"User {current_user.email} invited {target_user.email} to project {project_id}")

    return MemberResponse(
        id=member.id,
        project_id=member.project_id,
        user_id=member.user_id,
        role=member.role,
        email=target_user.email,
        name=target_user.name,
        avatar_url=target_user.avatar_url,
        invited_at=member.invited_at,
        accepted_at=member.accepted_at,
    )


@router.delete("/projects/{project_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    project_id: UUID,
    member_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """Remove a member. Owner can remove anyone; members can remove themselves."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.id == member_id,
            ProjectMember.project_id == project_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    # Can't remove the owner membership
    if member.role == "owner":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove the project owner",
        )

    is_owner = project.user_id == current_user.id
    is_self = member.user_id == current_user.id

    if not is_owner and not is_self:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the owner can remove other members",
        )

    await db.delete(member)


@router.post("/projects/{project_id}/members/{member_id}/accept", response_model=MemberResponse)
async def accept_invitation(
    project_id: UUID,
    member_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> MemberResponse:
    """Accept a pending invitation. Only the invited user can accept."""
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.id == member_id,
            ProjectMember.project_id == project_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation not found")

    if member.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only accept your own invitations")

    if member.accepted_at is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Already accepted")

    member.accepted_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(member)

    # Get user info for response
    result = await db.execute(select(User).where(User.id == member.user_id))
    user = result.scalar_one()

    logger.info(f"User {current_user.email} accepted invitation to project {project_id}")

    return MemberResponse(
        id=member.id,
        project_id=member.project_id,
        user_id=member.user_id,
        role=member.role,
        email=user.email,
        name=user.name,
        avatar_url=user.avatar_url,
        invited_at=member.invited_at,
        accepted_at=member.accepted_at,
    )


@router.get("/members/invitations", response_model=list[InvitationResponse])
async def list_invitations(
    current_user: CurrentUser,
    db: DbSession,
) -> list[InvitationResponse]:
    """List pending invitations for the current user."""
    result = await db.execute(
        select(ProjectMember, Project, User)
        .join(Project, ProjectMember.project_id == Project.id)
        .outerjoin(User, ProjectMember.invited_by == User.id)
        .where(
            ProjectMember.user_id == current_user.id,
            ProjectMember.accepted_at.is_(None),
        )
        .order_by(ProjectMember.invited_at.desc())
    )
    rows = result.all()

    return [
        InvitationResponse(
            id=member.id,
            project_id=member.project_id,
            project_name=project.name,
            role=member.role,
            invited_by_name=inviter.name if inviter else None,
            invited_at=member.invited_at,
        )
        for member, project, inviter in rows
    ]
