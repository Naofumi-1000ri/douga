import asyncio
import hashlib
import json
import logging
import re
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from src.api.deps import CurrentUser, DbSession, LightweightUser
from src.models.asset import Asset
from src.models.database import async_session_maker
from src.models.project import Project
from src.schemas.asset import (
    AssetCreate,
    AssetResponse,
    AssetUploadUrl,
    SessionSaveRequest,
    AssetReference,
    Fingerprint,
)
from src.services.chroma_key_sampler import sample_chroma_key_color
from src.services.storage_service import get_storage_service
from src.services.audio_extractor import extract_audio_from_gcs
from src.services.preview_service import PreviewService
from src.services.event_manager import event_manager

logger = logging.getLogger(__name__)

router = APIRouter()


async def verify_project_access(
    project_id: UUID,
    user_id: UUID,
    db: DbSession,
) -> Project:
    """Verify user has access to the project."""
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.user_id == user_id,
        )
    )
    project = result.scalar_one_or_none()

    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )
    return project


async def _get_asset_short_lived(
    project_id: UUID,
    asset_id: UUID,
    user_id: UUID,
) -> Asset:
    """Get asset with project access check using a short-lived DB session.

    The session is closed after this function returns, releasing the DB connection
    back to the pool. The returned Asset object is detached but its attributes
    remain accessible (expire_on_commit=False).
    """
    async with async_session_maker() as db:
        # Verify project access
        result = await db.execute(
            select(Project).where(
                Project.id == project_id,
                Project.user_id == user_id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        # Get asset
        result = await db.execute(
            select(Asset).where(
                Asset.id == asset_id,
                Asset.project_id == project_id,
            )
        )
        asset = result.scalar_one_or_none()
        if asset is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset not found",
            )

        return asset
    # Session closed, connection returned to pool


def _asset_to_response_with_signed_url(asset: Asset, storage: any) -> AssetResponse:
    """Convert asset to response with signed URL instead of direct storage URL."""
    # Manually construct response to avoid SQLAlchemy metadata attribute conflict
    response = AssetResponse(
        id=asset.id,
        project_id=asset.project_id,
        name=asset.name,
        type=asset.type,
        subtype=asset.subtype,
        storage_key=asset.storage_key,
        storage_url=asset.storage_url,
        thumbnail_url=asset.thumbnail_url,
        duration_ms=asset.duration_ms,
        width=asset.width,
        height=asset.height,
        file_size=asset.file_size,
        mime_type=asset.mime_type,
        sample_rate=asset.sample_rate,
        channels=asset.channels,
        has_alpha=asset.has_alpha,
        chroma_key_color=asset.chroma_key_color,
        hash=asset.hash,
        is_internal=asset.is_internal,
        folder_id=asset.folder_id,
        created_at=asset.created_at,
        metadata=asset.asset_metadata,  # Map asset_metadata -> metadata
    )
    # Replace storage_url with signed URL (15 min expiration)
    if asset.storage_key:
        try:
            response.storage_url = storage.generate_download_url(
                storage_key=asset.storage_key,
                expires_minutes=15,
            )
        except Exception:
            pass  # Keep original URL on error
    return response


@router.get("/projects/{project_id}/assets", response_model=list[AssetResponse])
async def list_assets(
    project_id: UUID,
    current_user: LightweightUser,
    include_internal: bool = False,
) -> list[AssetResponse]:
    """List all assets for a project.

    Uses LightweightUser + short-lived DB session to avoid holding a DB connection
    during signed URL generation (blocking GCS API calls for each asset).

    Args:
        project_id: Project ID
        include_internal: If True, include internal assets (e.g., extracted audio).
                         Default is False to hide internal assets from users.
    """
    # Short-lived DB session: released before signed URL generation
    async with async_session_maker() as db:
        result = await db.execute(
            select(Project).where(
                Project.id == project_id,
                Project.user_id == current_user.id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        query = select(Asset).where(Asset.project_id == project_id)

        # Filter out internal assets by default
        if not include_internal:
            query = query.where(Asset.is_internal == False)  # noqa: E712

        query = query.order_by(Asset.created_at.desc())
        result = await db.execute(query)
        assets = result.scalars().all()
    # DB session closed here — connection returned to pool

    # Generate signed URLs without holding DB connection.
    # Run in thread to avoid blocking the async event loop (GCS SDK is sync).
    storage = get_storage_service()
    responses = await asyncio.to_thread(
        lambda: [_asset_to_response_with_signed_url(a, storage) for a in assets]
    )
    return responses


@router.post("/projects/{project_id}/assets/upload-url", response_model=AssetUploadUrl)
async def get_upload_url(
    project_id: UUID,
    filename: str,
    content_type: str,
    current_user: CurrentUser,
    db: DbSession,
) -> AssetUploadUrl:
    """Get a pre-signed URL for uploading an asset."""
    await verify_project_access(project_id, current_user.id, db)

    storage = get_storage_service()
    upload_url, storage_key, expires_at = storage.generate_upload_url(
        project_id=str(project_id),
        filename=filename,
        content_type=content_type,
    )

    return AssetUploadUrl(
        upload_url=upload_url,
        storage_key=storage_key,
        expires_at=expires_at,
    )


async def _sample_chroma_key_background(asset_id: UUID, storage_key: str) -> None:
    """Background task: sample chroma key color from an avatar video asset."""
    try:
        storage = get_storage_service()
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=True) as tmp:
            await storage.download_file(storage_key, tmp.name)
            color = await asyncio.to_thread(sample_chroma_key_color, tmp.name)

        if color is None:
            logger.debug("No chroma key color detected for asset %s", asset_id)
            return

        async with async_session_maker() as session:
            result = await session.execute(
                select(Asset).where(Asset.id == asset_id)
            )
            asset = result.scalar_one_or_none()
            if asset is not None:
                asset.chroma_key_color = color
                await session.commit()
                logger.info(
                    "Set chroma_key_color=%s for asset %s", color, asset_id
                )
    except Exception:
        logger.exception("Background chroma key sampling failed for asset %s", asset_id)


@router.post(
    "/projects/{project_id}/assets",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_asset(
    project_id: UUID,
    asset_data: AssetCreate,
    current_user: CurrentUser,
    db: DbSession,
    background_tasks: BackgroundTasks,
) -> AssetResponse:
    """Register an uploaded asset in the database."""
    await verify_project_access(project_id, current_user.id, db)

    # Check for duplicate assets by name and type in the same project
    result = await db.execute(
        select(Asset).where(
            Asset.project_id == project_id,
            Asset.name == asset_data.name,
            Asset.type == asset_data.type,
        ).limit(1)
    )
    existing_asset = result.scalar_one_or_none()

    if existing_asset:
        # Update existing asset with new file (replace behavior)
        storage = get_storage_service()

        # Delete old file from storage if different
        if existing_asset.storage_key and existing_asset.storage_key != asset_data.storage_key:
            try:
                storage.delete_file(existing_asset.storage_key)
            except Exception:
                pass  # Ignore deletion errors

        # Update all fields with new data
        existing_asset.storage_key = asset_data.storage_key
        existing_asset.storage_url = asset_data.storage_url
        existing_asset.file_size = asset_data.file_size
        existing_asset.mime_type = asset_data.mime_type
        existing_asset.duration_ms = asset_data.duration_ms
        existing_asset.width = asset_data.width
        existing_asset.height = asset_data.height
        existing_asset.sample_rate = asset_data.sample_rate
        existing_asset.channels = asset_data.channels

        await db.flush()
        await db.refresh(existing_asset)
        return _asset_to_response_with_signed_url(existing_asset, storage)

    asset = Asset(
        project_id=project_id,
        name=asset_data.name,
        type=asset_data.type,
        subtype=asset_data.subtype,
        storage_key=asset_data.storage_key,
        storage_url=asset_data.storage_url,
        file_size=asset_data.file_size,
        mime_type=asset_data.mime_type,
        duration_ms=asset_data.duration_ms,
        width=asset_data.width,
        height=asset_data.height,
        sample_rate=asset_data.sample_rate,
        channels=asset_data.channels,
        has_alpha=asset_data.has_alpha,
        chroma_key_color=asset_data.chroma_key_color,
    )
    db.add(asset)
    await db.flush()
    await db.refresh(asset)

    # Schedule background chroma key sampling for avatar videos without a color set
    if (
        asset.type == "video"
        and asset.subtype == "avatar"
        and not asset.chroma_key_color
    ):
        background_tasks.add_task(
            _sample_chroma_key_background, asset.id, asset.storage_key
        )

    storage = get_storage_service()
    return _asset_to_response_with_signed_url(asset, storage)


@router.get("/projects/{project_id}/assets/{asset_id}", response_model=AssetResponse)
async def get_asset(
    project_id: UUID,
    asset_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> AssetResponse:
    """Get an asset by ID."""
    await verify_project_access(project_id, current_user.id, db)

    result = await db.execute(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.project_id == project_id,
        )
    )
    asset = result.scalar_one_or_none()

    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    storage = get_storage_service()
    return _asset_to_response_with_signed_url(asset, storage)


@router.delete(
    "/projects/{project_id}/assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_asset(
    project_id: UUID,
    asset_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """Delete an asset."""
    await verify_project_access(project_id, current_user.id, db)

    result = await db.execute(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.project_id == project_id,
        )
    )
    asset = result.scalar_one_or_none()

    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    # Delete from storage
    storage = get_storage_service()
    storage.delete_file(asset.storage_key)

    await db.delete(asset)


@router.post(
    "/projects/{project_id}/assets/{asset_id}/extract-audio",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def extract_audio(
    project_id: UUID,
    asset_id: UUID,
    current_user: LightweightUser,
) -> AssetResponse:
    """Extract audio from a video asset and create a new audio asset.

    Uses LightweightUser + short-lived DB sessions to avoid holding a DB connection
    during long-running FFmpeg processing.

    Naming convention: `{video_name}.mp3` to prevent duplicate extractions.
    If audio already exists, returns the existing asset.
    Audio is saved as a regular asset (is_internal=False) for reusability.
    """
    # Short-lived DB session: get source asset + check for existing audio
    async with async_session_maker() as db:
        # Verify project access
        result = await db.execute(
            select(Project).where(
                Project.id == project_id,
                Project.user_id == current_user.id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        # Get source video asset
        result = await db.execute(
            select(Asset).where(
                Asset.id == asset_id,
                Asset.project_id == project_id,
            )
        )
        source_asset = result.scalar_one_or_none()

        if source_asset is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Asset not found",
            )

        if source_asset.type != "video":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Asset must be a video to extract audio",
            )

        # Check if audio asset already exists
        # e.g. "セクション2_3_VRoid Studio.mp4" → "セクション2_3_VRoid Studio.mp3"
        video_name = source_asset.name
        audio_name = video_name.rsplit(".", 1)[0] + ".mp3" if "." in video_name else video_name + ".mp3"
        existing_result = await db.execute(
            select(Asset).where(
                Asset.project_id == project_id,
                Asset.name == audio_name,
                Asset.type == "audio",
            ).limit(1)
        )
        existing_audio = existing_result.scalar_one_or_none()

        if existing_audio:
            storage = get_storage_service()
            return _asset_to_response_with_signed_url(existing_audio, storage)

        # Save data needed for extraction
        source_storage_key = source_asset.storage_key
        source_duration_ms = source_asset.duration_ms
    # Session closed, connection returned to pool

    # Long-running FFmpeg operation — no DB connection held
    storage = get_storage_service()
    try:
        audio_key, file_size = await extract_audio_from_gcs(
            storage_service=storage,
            source_key=source_storage_key,
            project_id=str(project_id),
            output_filename=audio_name,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to extract audio: {str(e)}",
        )

    storage_url = storage.get_public_url(audio_key)

    # Short-lived DB session: create the audio asset
    async with async_session_maker() as db:
        audio_asset = Asset(
            project_id=project_id,
            name=audio_name,
            type="audio",
            subtype="narration",
            storage_key=audio_key,
            storage_url=storage_url,
            file_size=file_size,
            mime_type="audio/mpeg",
            duration_ms=source_duration_ms,
            sample_rate=44100,
            channels=2,
            is_internal=False,
        )
        db.add(audio_asset)
        await db.commit()
        await db.refresh(audio_asset)

        return _asset_to_response_with_signed_url(audio_asset, storage)


# Response models for preview endpoints
class WaveformResponse(BaseModel):
    """Response model for waveform data."""

    peaks: list[float]
    duration_ms: int
    sample_rate: int = 44100


class SignedUrlResponse(BaseModel):
    """Response model for signed URL."""

    url: str
    expires_in_seconds: int


@router.get(
    "/projects/{project_id}/assets/{asset_id}/waveform",
    response_model=WaveformResponse,
)
async def get_waveform(
    project_id: UUID,
    asset_id: UUID,
    samples: int = 200,
    current_user: LightweightUser = None,
) -> WaveformResponse:
    """Get waveform data for audio visualization.

    Uses LightweightUser + short-lived DB session to avoid holding a DB connection
    during long-running file operations (download, FFmpeg waveform generation).

    Args:
        project_id: Project ID
        asset_id: Asset ID
        samples: Number of peak samples to return (default 200)

    Returns:
        WaveformResponse with peaks, duration, and sample rate
    """
    # Short-lived DB session: connection returned to pool after this call
    asset = await _get_asset_short_lived(project_id, asset_id, current_user.id)

    if asset.type not in ("audio", "video"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Asset must be audio or video type",
        )

    asset_storage_key = asset.storage_key

    # All DB connections released — long-running operations below
    storage = get_storage_service()
    preview_service = PreviewService()

    with tempfile.NamedTemporaryFile(suffix=".tmp", delete=True) as tmp_file:
        await storage.download_file(asset_storage_key, tmp_file.name)

        try:
            waveform = preview_service.generate_waveform(tmp_file.name, samples=samples)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

    return WaveformResponse(
        peaks=waveform.peaks,
        duration_ms=waveform.duration_ms,
        sample_rate=waveform.sample_rate,
    )


class ThumbnailResponse(BaseModel):
    """Response model for video thumbnail."""

    url: str
    time_ms: int
    width: int
    height: int


@router.get(
    "/projects/{project_id}/assets/{asset_id}/thumbnail",
    response_model=ThumbnailResponse,
)
async def get_thumbnail(
    project_id: UUID,
    asset_id: UUID,
    time_ms: int = 0,
    width: int = 160,
    height: int = 90,
    current_user: LightweightUser = None,
) -> ThumbnailResponse:
    """Get a thumbnail image from a video at a specific time position.

    Uses LightweightUser + short-lived DB session to avoid holding a DB connection
    during long-running file operations (download, FFmpeg, upload).

    Args:
        project_id: Project ID
        asset_id: Asset ID
        time_ms: Time position in milliseconds (default 0)
        width: Thumbnail width (default 160)
        height: Thumbnail height (default 90)

    Returns:
        ThumbnailResponse with signed URL to the thumbnail image
    """
    # Short-lived DB session: connection returned to pool after this call
    asset = await _get_asset_short_lived(project_id, asset_id, current_user.id)

    if asset.type != "video":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Asset must be video type",
        )

    asset_storage_key = asset.storage_key
    asset_duration_ms = asset.duration_ms

    # All DB connections released — long-running operations below
    storage = get_storage_service()
    preview_service = PreviewService()

    # Clamp time_ms to avoid extracting frame at or past the video end
    actual_time_ms = time_ms
    if asset_duration_ms and time_ms >= asset_duration_ms - 100:
        actual_time_ms = max(0, asset_duration_ms - 500)

    # Generate a unique key for this thumbnail (use original time_ms for cache key)
    thumb_key = f"thumbnails/{project_id}/{asset_id}/{time_ms}_{width}x{height}.jpg"

    # Check if thumbnail already exists in storage
    if storage.file_exists(thumb_key):
        existing_url = storage.generate_download_url(thumb_key, expires_minutes=60)
        return ThumbnailResponse(
            url=existing_url,
            time_ms=time_ms,
            width=width,
            height=height,
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        video_path = Path(tmp_dir) / "video.mp4"
        await storage.download_file(asset_storage_key, str(video_path))

        thumb_path = Path(tmp_dir) / "thumb.jpg"
        try:
            preview_service.generate_thumbnail(
                str(video_path),
                str(thumb_path),
                time_ms=actual_time_ms,
                width=width,
                height=height,
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate thumbnail: {str(e)}",
            )

        await storage.upload_file(str(thumb_path), thumb_key, "image/jpeg")

    thumb_url = storage.generate_download_url(thumb_key, expires_minutes=60)

    return ThumbnailResponse(
        url=thumb_url,
        time_ms=time_ms,
        width=width,
        height=height,
    )


@router.get(
    "/projects/{project_id}/assets/{asset_id}/signed-url",
    response_model=SignedUrlResponse,
)
async def get_signed_url(
    project_id: UUID,
    asset_id: UUID,
    expiration_minutes: int = 15,
    current_user: CurrentUser = None,
    db: DbSession = None,
) -> SignedUrlResponse:
    """Get a signed URL for streaming/downloading an asset.

    Args:
        project_id: Project ID
        asset_id: Asset ID
        expiration_minutes: URL expiration time in minutes (default 15)

    Returns:
        SignedUrlResponse with URL and expiration info
    """
    await verify_project_access(project_id, current_user.id, db)

    # Get asset
    result = await db.execute(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.project_id == project_id,
        )
    )
    asset = result.scalar_one_or_none()

    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    # Generate signed URL using storage service
    storage = get_storage_service()
    try:
        signed_url = storage.generate_download_url(
            storage_key=asset.storage_key,
            expires_minutes=expiration_minutes,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate signed URL: {str(e)}",
        )

    return SignedUrlResponse(
        url=signed_url,
        expires_in_seconds=expiration_minutes * 60,
    )


class AssetMoveToFolder(BaseModel):
    """Request model for moving an asset to a folder."""

    folder_id: UUID | None = None


@router.patch(
    "/projects/{project_id}/assets/{asset_id}/folder",
    response_model=AssetResponse,
)
async def move_asset_to_folder(
    project_id: UUID,
    asset_id: UUID,
    move_data: AssetMoveToFolder,
    current_user: CurrentUser,
    db: DbSession,
) -> AssetResponse:
    """Move an asset to a folder or to root (folder_id=null)."""
    await verify_project_access(project_id, current_user.id, db)

    # Get asset
    result = await db.execute(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.project_id == project_id,
        )
    )
    asset = result.scalar_one_or_none()

    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found",
        )

    # Verify folder exists if provided
    if move_data.folder_id is not None:
        from src.models.asset_folder import AssetFolder

        result = await db.execute(
            select(AssetFolder).where(
                AssetFolder.id == move_data.folder_id,
                AssetFolder.project_id == project_id,
            )
        )
        folder = result.scalar_one_or_none()
        if folder is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Folder not found",
            )

    asset.folder_id = move_data.folder_id
    await db.flush()
    await db.refresh(asset)

    # Note: No Firestore event for folder moves - doesn't affect timeline

    storage = get_storage_service()
    return _asset_to_response_with_signed_url(asset, storage)


# === Session Management ===

APP_VERSION = "0.1.0"  # App version for session metadata


def sanitize_session_name(name: str) -> str:
    """Sanitize session name for safe file storage."""
    # Trim whitespace
    sanitized = name.strip()
    # Remove/replace dangerous characters
    sanitized = re.sub(r'[/\\<>:"|?*]', '_', sanitized)
    # Collapse multiple underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    # Remove leading/trailing underscores
    sanitized = sanitized.strip('_')
    # Limit length
    sanitized = sanitized[:100]
    # Default name if empty
    return sanitized or "session"


async def calculate_file_hash(storage_key: str) -> str:
    """Calculate SHA-256 hash of a file in storage."""
    storage = get_storage_service()

    with tempfile.NamedTemporaryFile(delete=True) as tmp:
        await storage.download_file(storage_key, tmp.name)
        sha256 = hashlib.sha256()
        with open(tmp.name, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                sha256.update(chunk)
        return f"sha256:{sha256.hexdigest()}"


async def calculate_hashes_for_session(
    asset_references: list[AssetReference],
    project_assets: list[Asset],
    project_id: str,
) -> list[AssetReference]:
    """
    Calculate hashes for assets without hash and update both DB and references.
    """
    # Create lookup map
    asset_map = {str(a.id): a for a in project_assets}

    updated_refs = []
    for ref in asset_references:
        # Skip if hash already exists
        if ref.fingerprint.hash is not None:
            updated_refs.append(ref)
            continue

        asset = asset_map.get(ref.id)
        if not asset or not asset.storage_key:
            updated_refs.append(ref)
            continue

        try:
            # Calculate hash with timeout
            hash_value = await asyncio.wait_for(
                calculate_file_hash(asset.storage_key),
                timeout=30.0  # 30 seconds per file
            )

            # Update DB
            async with async_session_maker() as db:
                result = await db.execute(
                    select(Asset).where(Asset.id == asset.id)
                )
                db_asset = result.scalar_one_or_none()
                if db_asset:
                    db_asset.hash = hash_value
                    await db.commit()

            # Update reference
            ref.fingerprint.hash = hash_value
            logger.info(f"Calculated hash for asset {ref.id}: {hash_value[:20]}...")
        except asyncio.TimeoutError:
            logger.warning(f"Hash calculation timed out for asset {ref.id}")
        except Exception as e:
            logger.warning(f"Hash calculation failed for {ref.id}: {e}")

        updated_refs.append(ref)

    return updated_refs


@router.post(
    "/projects/{project_id}/sessions",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
)
async def save_session(
    project_id: UUID,
    request: SessionSaveRequest,
    current_user: LightweightUser,
) -> AssetResponse:
    """
    Save a session as an asset.

    - Sanitizes session name
    - Appends UUID suffix if name already exists
    - Calculates missing hashes for referenced assets
    - Sets server-side metadata (created_at, app_version)
    - Stores session JSON in GCS
    """
    # Sanitize name
    safe_name = sanitize_session_name(request.session_name)

    # Short-lived session for project access + duplicate check
    async with async_session_maker() as db:
        # Verify project access
        result = await db.execute(
            select(Project).where(
                Project.id == project_id,
                Project.user_id == current_user.id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        # Check for duplicate name
        result = await db.execute(
            select(Asset).where(
                Asset.project_id == project_id,
                Asset.name == safe_name,
                Asset.type == "session",
            ).limit(1)
        )
        existing = result.scalar_one_or_none()
        if existing:
            # Append short UUID to avoid collision
            short_uuid = str(uuid4())[:8]
            safe_name = f"{safe_name}_{short_uuid}"

        # Get all project assets for hash calculation
        result = await db.execute(
            select(Asset).where(Asset.project_id == project_id)
        )
        project_assets = list(result.scalars().all())

    # Calculate hashes for assets without hash (timeout per file)
    session_data = request.session_data
    try:
        session_data.asset_references = await asyncio.wait_for(
            calculate_hashes_for_session(
                session_data.asset_references,
                project_assets,
                str(project_id)
            ),
            timeout=60.0  # 60 seconds total timeout
        )
    except asyncio.TimeoutError:
        logger.warning(f"Hash calculation timed out for session {safe_name}")

    # Set server-side metadata
    session_data.created_at = datetime.utcnow().isoformat() + "Z"
    session_data.app_version = APP_VERSION
    session_data.schema_version = "1.0"

    # Convert to JSON
    json_content = json.dumps(session_data.model_dump(), ensure_ascii=False, indent=2)
    json_bytes = json_content.encode('utf-8')

    # Upload to GCS
    storage = get_storage_service()
    storage_key = f"sessions/{project_id}/{safe_name}.json"

    # Upload using bytes
    storage.upload_file_from_bytes(
        storage_key=storage_key,
        data=json_bytes,
        content_type="application/json",
    )
    storage_url = storage.get_public_url(storage_key)

    # Create asset record with metadata stored in DB
    async with async_session_maker() as db:
        asset = Asset(
            project_id=project_id,
            name=safe_name,
            type="session",
            subtype="other",
            storage_key=storage_key,
            storage_url=storage_url,
            file_size=len(json_bytes),
            mime_type="application/json",
            asset_metadata={
                "app_version": session_data.app_version,
                "created_at": session_data.created_at,
            },
        )
        db.add(asset)
        await db.commit()
        await db.refresh(asset)

        # Return with signed URL (metadata is now in DB, _asset_to_response_with_signed_url will map it)
        return _asset_to_response_with_signed_url(asset, storage)


@router.get(
    "/projects/{project_id}/sessions/{session_id}",
)
async def get_session(
    project_id: UUID,
    session_id: UUID,
    current_user: LightweightUser,
) -> dict:
    """
    Get session data by ID.

    Returns the full session JSON content including timeline_data and asset_references.
    """
    # Short-lived session for asset lookup
    async with async_session_maker() as db:
        # Verify project access
        result = await db.execute(
            select(Project).where(
                Project.id == project_id,
                Project.user_id == current_user.id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found",
            )

        # Get session asset
        result = await db.execute(
            select(Asset).where(
                Asset.id == session_id,
                Asset.project_id == project_id,
                Asset.type == "session",
            )
        )
        session_asset = result.scalar_one_or_none()

        if session_asset is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            )

        storage_key = session_asset.storage_key

    # Download and parse session JSON
    storage = get_storage_service()
    with tempfile.NamedTemporaryFile(suffix=".json", delete=True) as tmp:
        await storage.download_file(storage_key, tmp.name)
        with open(tmp.name, 'r', encoding='utf-8') as f:
            session_data = json.load(f)

    return session_data
