import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Optional
from uuid import UUID

import firebase_admin
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.config import get_settings
from src.models.api_key import APIKey
from src.models.database import async_session_maker, get_db
from src.models.sequence import Sequence
from src.models.user import User
from src.utils.edit_token import decode_edit_token

settings = get_settings()

# Initialize Firebase Admin SDK
_firebase_app = None


def get_firebase_app() -> firebase_admin.App:
    global _firebase_app
    if _firebase_app is None:
        try:
            _firebase_app = firebase_admin.get_app()
        except ValueError:
            # App not initialized yet
            if settings.firebase_project_id:
                cred = credentials.ApplicationDefault()
                _firebase_app = firebase_admin.initialize_app(
                    cred, {"projectId": settings.firebase_project_id}
                )
            else:
                _firebase_app = firebase_admin.initialize_app()
    return _firebase_app


# Use auto_error=False to allow dev token bypass
security = HTTPBearer(auto_error=False)

# DEV_USER token constant
DEV_TOKEN = "dev-token"

# API Key prefix
API_KEY_PREFIX = "douga_sk_"


def hash_api_key(key: str) -> str:
    """Hash an API key using SHA256."""
    return hashlib.sha256(key.encode()).hexdigest()


async def get_user_by_api_key(
    db: AsyncSession, api_key: str
) -> User | None:
    """Look up a user by their API key.

    Returns None if key is invalid, inactive, or expired.
    Updates last_used_at on successful lookup.
    """
    key_hash = hash_api_key(api_key)

    result = await db.execute(
        select(APIKey)
        .where(APIKey.key_hash == key_hash)
        .where(APIKey.is_active == True)  # noqa: E712
    )
    api_key_record = result.scalar_one_or_none()

    if api_key_record is None:
        return None

    # Check expiration
    if api_key_record.expires_at is not None:
        if api_key_record.expires_at < datetime.now(timezone.utc):
            return None

    # Update last_used_at
    await db.execute(
        update(APIKey)
        .where(APIKey.id == api_key_record.id)
        .values(last_used_at=datetime.now(timezone.utc))
    )

    # Get the user
    user_result = await db.execute(
        select(User).where(User.id == api_key_record.user_id)
    )
    return user_result.scalar_one_or_none()


async def _authenticate_user(
    db: AsyncSession,
    credentials: Optional[HTTPAuthorizationCredentials],
    x_api_key: Optional[str],
) -> User:
    """Core authentication logic shared by all auth dependencies.

    Authentication priority:
    1. X-API-Key header (for MCP/programmatic access)
    2. Authorization: Bearer <token> (Firebase)
    3. dev-token bypass (dev_mode only)
    """
    # Check for API key authentication first
    if x_api_key is not None:
        if not x_api_key.startswith(API_KEY_PREFIX):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key format",
            )

        user = await get_user_by_api_key(db, x_api_key)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired API key",
            )
        return user

    # Check for dev mode bypass
    if settings.dev_mode:
        token = credentials.credentials if credentials else None
        if token == DEV_TOKEN or token is None:
            # Return dev user
            result = await db.execute(
                select(User).where(User.firebase_uid == settings.dev_user_id)
            )
            user = result.scalar_one_or_none()

            if user is None:
                user = User(
                    firebase_uid=settings.dev_user_id,
                    email=settings.dev_user_email,
                    name=settings.dev_user_name,
                    avatar_url=None,
                )
                db.add(user)
                await db.flush()

            return user

    # Require credentials in production
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        # Initialize Firebase if needed
        get_firebase_app()

        # Verify the Firebase token
        decoded_token = firebase_auth.verify_id_token(token)
        firebase_uid = decoded_token["uid"]
        email = decoded_token.get("email", "")
        name = decoded_token.get("name", email.split("@")[0])

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get or create user
    result = await db.execute(select(User).where(User.firebase_uid == firebase_uid))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            firebase_uid=firebase_uid,
            email=email,
            name=name,
            avatar_url=decoded_token.get("picture"),
        )
        db.add(user)
        await db.flush()

    return user


async def get_current_user(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
    x_api_key: Annotated[Optional[str], Header(alias="X-API-Key")] = None,
) -> User:
    """Authenticate user. Holds DB connection for the request lifecycle.

    Use this for short-lived endpoints (CRUD operations).
    For long-running endpoints, use LightweightUser instead.
    """
    return await _authenticate_user(db, credentials, x_api_key)


@dataclass
class AuthenticatedUser:
    """Lightweight user info that doesn't hold a DB connection."""
    id: UUID


async def get_authenticated_user(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
    x_api_key: Annotated[Optional[str], Header(alias="X-API-Key")] = None,
) -> AuthenticatedUser:
    """Authenticate user with a short-lived DB session.

    Unlike CurrentUser, this does NOT hold a DB connection after auth completes.
    Use this for long-running endpoints (thumbnail, waveform, audio extraction)
    to avoid connection pool exhaustion.
    """
    async with async_session_maker() as db:
        user = await _authenticate_user(db, credentials, x_api_key)
        await db.commit()
        return AuthenticatedUser(id=user.id)
    # Session closed here, connection returned to pool


CurrentUser = Annotated[User, Depends(get_current_user)]
LightweightUser = Annotated[AuthenticatedUser, Depends(get_authenticated_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


@dataclass
class EditContext:
    """Bundles project + optional sequence for edit-session-aware endpoints."""

    project: "Project"
    sequence: "Sequence | None" = None

    @property
    def timeline_data(self) -> dict:
        if self.sequence is not None:
            return self.sequence.timeline_data
        return self.project.timeline_data

    def flag_timeline_modified(self) -> None:
        target = self.sequence if self.sequence else self.project
        flag_modified(target, "timeline_data")

    @property
    def version(self) -> int:
        if self.sequence is not None:
            return self.sequence.version
        return self.project.version

    def increment_version(self) -> None:
        target = self.sequence if self.sequence else self.project
        target.version += 1


async def get_edit_context(
    project_id: UUID,
    current_user: User,
    db: "AsyncSession",
    x_edit_session: str | None = None,
) -> EditContext:
    """Resolve EditContext from X-Edit-Session header (read-only, no lock check).

    When no X-Edit-Session token is provided, auto-resolves to the project's
    default sequence so that V1 API (API Key) callers always target sequence data.
    """
    from src.api.access import get_accessible_project

    project = await get_accessible_project(project_id, current_user.id, db)

    # 1. Try X-Edit-Session token first
    if x_edit_session:
        try:
            claims = decode_edit_token(x_edit_session, settings.edit_token_secret)
        except ValueError:
            pass  # Fall through to default sequence
        else:
            if claims["pid"] == str(project_id):
                seq_id = claims["sid"]
                result = await db.execute(
                    select(Sequence).where(
                        Sequence.id == seq_id,
                        Sequence.project_id == project_id,
                    )
                )
                seq = result.scalar_one_or_none()
                if seq:
                    return EditContext(project=project, sequence=seq)

    # 2. Auto-resolve to default sequence
    result = await db.execute(
        select(Sequence).where(
            Sequence.project_id == project_id,
            Sequence.is_default == True,  # noqa: E712
        )
    )
    default_seq = result.scalar_one_or_none()
    if default_seq:
        return EditContext(project=project, sequence=default_seq)

    # 3. Fallback to project only (legacy projects without sequences)
    return EditContext(project=project)


async def get_edit_context_for_write(
    project_id: UUID,
    current_user: User,
    db: "AsyncSession",
    x_edit_session: str | None = None,
) -> EditContext:
    """Resolve EditContext with row-level lock and lock-holder verification.

    When no X-Edit-Session token is provided, auto-resolves to the project's
    default sequence (no lock check for API Key users who don't hold locks).
    """
    from src.api.access import get_accessible_project

    project = await get_accessible_project(project_id, current_user.id, db)

    # 1. Try X-Edit-Session token first
    if x_edit_session:
        try:
            claims = decode_edit_token(x_edit_session, settings.edit_token_secret)
        except ValueError:
            pass  # Fall through to default sequence
        else:
            if claims["pid"] == str(project_id):
                seq_id = claims["sid"]
                result = await db.execute(
                    select(Sequence)
                    .where(
                        Sequence.id == seq_id,
                        Sequence.project_id == project_id,
                    )
                    .with_for_update()
                )
                seq = result.scalar_one_or_none()
                if seq:
                    # Verify the user holds the lock
                    if seq.locked_by != current_user.id:
                        raise HTTPException(
                            status_code=403,
                            detail="You do not hold the lock on this sequence",
                        )
                    return EditContext(project=project, sequence=seq)

    # 2. Auto-resolve to default sequence (no lock check for API key users)
    result = await db.execute(
        select(Sequence)
        .where(
            Sequence.project_id == project_id,
            Sequence.is_default == True,  # noqa: E712
        )
        .with_for_update()
    )
    default_seq = result.scalar_one_or_none()
    if default_seq:
        return EditContext(project=project, sequence=default_seq)

    # 3. Fallback to project only (legacy projects without sequences)
    return EditContext(project=project)
