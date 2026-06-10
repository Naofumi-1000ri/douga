"""Local storage API endpoints for development.

Authentication policy for GET /files/{storage_key:path}:
- dev_mode=True:  unauthenticated access is allowed so that <img src> / <video src>
  references work in the browser without custom fetch logic (browsers cannot send
  Authorization headers for media elements).
- dev_mode=False: authentication is required; unauthenticated requests receive 401.

This avoids breaking frontend asset rendering in development while guarding against
exposure when use_local_storage=True leaks into a production-like environment.

PUT /upload/{storage_key:path} always requires authentication regardless of dev_mode.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.api.deps import CurrentUser, _authenticate_user
from src.config import get_settings
from src.models.database import get_db
from src.services.storage_service import storage_service

settings = get_settings()
router = APIRouter()

_security = HTTPBearer(auto_error=False)


@router.put("/upload/{storage_key:path}")
async def upload_file(
    storage_key: str,
    request: Request,
    _current_user: CurrentUser,
) -> dict[str, str]:
    """Handle file upload for local storage.

    Authentication is always required to prevent unauthenticated writes.
    """
    if not settings.use_local_storage:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Local storage not enabled",
        )

    body = await request.body()
    if not body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No file data provided",
        )

    try:
        storage_service.upload_file_from_bytes(storage_key, body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return {"status": "ok", "storage_key": storage_key}


@router.get("/files/{storage_key:path}")
async def get_file(
    storage_key: str,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_security)] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> FileResponse:
    """Serve files from local storage.

    See module docstring for the authentication policy.
    """
    if not settings.use_local_storage:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Local storage not enabled",
        )

    # Enforce authentication when not in dev_mode so that unauthenticated
    # browsers/crawlers cannot read arbitrary files even if use_local_storage
    # is accidentally left True in a non-dev deployment.
    if not settings.dev_mode:
        async for db in get_db():
            await _authenticate_user(db, credentials, x_api_key)
            break

    try:
        file_path = storage_service.get_file_path(storage_key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )

    # Determine media type from extension
    ext = file_path.suffix.lower()
    media_types = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".aac": "audio/aac",
    }
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=file_path.name,
    )
