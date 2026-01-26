import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from src.api.deps import API_KEY_PREFIX, CurrentUser, DbSession, hash_api_key
from src.models.api_key import APIKey
from src.schemas.auth import APIKeyCreate, APIKeyCreated, APIKeyResponse
from src.schemas.user import UserResponse

router = APIRouter()


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: CurrentUser) -> UserResponse:
    """Get current authenticated user info."""
    return UserResponse.model_validate(current_user)


# =============================================================================
# API Key Management
# =============================================================================


def generate_api_key() -> str:
    """Generate a new API key with prefix."""
    # Generate 32 random bytes, encode as hex (64 chars)
    random_part = secrets.token_hex(32)
    return f"{API_KEY_PREFIX}{random_part}"


@router.post("/api-keys", response_model=APIKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    request: APIKeyCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> APIKeyCreated:
    """Create a new API key.

    The key is only shown once in the response. Store it securely.
    """
    # Generate the key
    raw_key = generate_api_key()
    key_hash = hash_api_key(raw_key)
    key_prefix = raw_key[:16]  # "douga_sk_" + first 7 chars of random

    # Calculate expiration if specified
    expires_at = None
    if request.expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=request.expires_in_days)

    # Create the API key record
    api_key = APIKey(
        user_id=current_user.id,
        name=request.name,
        key_prefix=key_prefix,
        key_hash=key_hash,
        is_active=True,
        expires_at=expires_at,
    )
    db.add(api_key)
    await db.flush()

    return APIKeyCreated(
        id=api_key.id,
        name=api_key.name,
        key=raw_key,  # Only time the raw key is returned
        key_prefix=key_prefix,
        created_at=api_key.created_at,
        expires_at=api_key.expires_at,
    )


@router.get("/api-keys", response_model=list[APIKeyResponse])
async def list_api_keys(
    current_user: CurrentUser,
    db: DbSession,
) -> list[APIKeyResponse]:
    """List all API keys for the current user."""
    result = await db.execute(
        select(APIKey)
        .where(APIKey.user_id == current_user.id)
        .order_by(APIKey.created_at.desc())
    )
    keys = result.scalars().all()
    return [APIKeyResponse.model_validate(k) for k in keys]


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_api_key(
    key_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """Delete (deactivate) an API key.

    The key is marked as inactive rather than deleted, preserving audit history.
    """
    result = await db.execute(
        select(APIKey)
        .where(APIKey.id == key_id)
        .where(APIKey.user_id == current_user.id)
    )
    api_key = result.scalar_one_or_none()

    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    api_key.is_active = False
    await db.flush()
