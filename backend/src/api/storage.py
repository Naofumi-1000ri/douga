"""Local storage API endpoints for development."""

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse

from src.config import get_settings
from src.services.storage_service import storage_service

settings = get_settings()
router = APIRouter()


@router.put("/upload/{storage_key:path}")
async def upload_file(storage_key: str, request: Request):
    """Handle file upload for local storage."""
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

    storage_service.upload_file_from_bytes(storage_key, body)
    return {"status": "ok", "storage_key": storage_key}


@router.get("/files/{storage_key:path}")
async def get_file(storage_key: str):
    """Serve files from local storage."""
    if not settings.use_local_storage:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Local storage not enabled",
        )

    file_path = storage_service.get_file_path(storage_key)
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
