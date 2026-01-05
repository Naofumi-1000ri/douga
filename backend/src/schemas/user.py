from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    firebase_uid: str
    email: EmailStr
    name: str
    avatar_url: str | None = None


class UserResponse(BaseModel):
    id: UUID
    firebase_uid: str
    email: str
    name: str
    avatar_url: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
