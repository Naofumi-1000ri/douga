from typing import Annotated, Optional

import firebase_admin
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models.database import get_db
from src.models.user import User

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


async def get_current_user(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Verify Firebase token and get or create user.

    In dev_mode, accepts 'dev-token' to bypass Firebase auth.
    """
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


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]
