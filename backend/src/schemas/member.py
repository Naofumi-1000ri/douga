from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class InviteMemberRequest(BaseModel):
    email: str  # Use str not EmailStr to avoid extra dep


class MemberResponse(BaseModel):
    id: UUID
    project_id: UUID
    user_id: UUID
    role: str
    email: str
    name: str
    avatar_url: str | None = None
    invited_at: datetime
    accepted_at: datetime | None = None

    class Config:
        from_attributes = True


class InvitationResponse(BaseModel):
    id: UUID
    project_id: UUID
    project_name: str
    role: str
    invited_by_name: str | None = None
    invited_at: datetime
