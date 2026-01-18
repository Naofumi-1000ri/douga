import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from src.api.deps import CurrentUser, DbSession
from src.models.asset import Asset
from src.models.project import Project
from src.schemas.asset import AssetCreate, AssetResponse, AssetUploadUrl
from src.services.storage_service import get_storage_service
from src.services.audio_extractor import extract_audio_from_gcs
from src.services.preview_service import PreviewService, WaveformData

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


@router.get("/projects/{project_id}/assets", response_model=list[AssetResponse])
async def list_assets(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    include_internal: bool = False,
) -> list[AssetResponse]:
    """List all assets for a project.

    Args:
        project_id: Project ID
        include_internal: If True, include internal assets (e.g., extracted audio).
                         Default is False to hide internal assets from users.
    """
    await verify_project_access(project_id, current_user.id, db)

    query = select(Asset).where(Asset.project_id == project_id)

    # Filter out internal assets by default
    if not include_internal:
        query = query.where(Asset.is_internal == False)  # noqa: E712

    query = query.order_by(Asset.created_at.desc())
    result = await db.execute(query)
    assets = result.scalars().all()
    return [AssetResponse.model_validate(a) for a in assets]


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
) -> AssetResponse:
    """Register an uploaded asset in the database."""
    await verify_project_access(project_id, current_user.id, db)

    # Check for duplicate assets by name and type in the same project
    result = await db.execute(
        select(Asset).where(
            Asset.project_id == project_id,
            Asset.name == asset_data.name,
            Asset.type == asset_data.type,
        )
    )
    existing_asset = result.scalar_one_or_none()

    if existing_asset:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Asset with name '{asset_data.name}' already exists in this project",
        )

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
    return AssetResponse.model_validate(asset)


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

    return AssetResponse.model_validate(asset)


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
    current_user: CurrentUser,
    db: DbSession,
) -> AssetResponse:
    """Extract audio from a video asset and create a new audio asset."""
    await verify_project_access(project_id, current_user.id, db)

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

    # Extract audio
    storage = get_storage_service()
    try:
        audio_key, file_size = await extract_audio_from_gcs(
            storage_service=storage,
            source_key=source_asset.storage_key,
            project_id=str(project_id),
            output_filename=source_asset.name.rsplit(".", 1)[0] + ".mp3",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to extract audio: {str(e)}",
        )

    # Get storage URL
    storage_url = storage.get_public_url(audio_key)

    # Create new audio asset (marked as internal - not shown to user)
    audio_asset = Asset(
        project_id=project_id,
        name=source_asset.name.rsplit(".", 1)[0] + ".mp3",
        type="audio",
        subtype="narration",
        storage_key=audio_key,
        storage_url=storage_url,
        file_size=file_size,
        mime_type="audio/mpeg",
        duration_ms=source_asset.duration_ms,
        sample_rate=44100,
        channels=2,
        is_internal=True,  # Hide from asset library
    )
    db.add(audio_asset)
    await db.flush()
    await db.refresh(audio_asset)

    # Commit explicitly to ensure asset is persisted before returning
    # This prevents race condition where frontend gets asset_id but DB commit fails
    await db.commit()

    return AssetResponse.model_validate(audio_asset)


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
    current_user: CurrentUser = None,
    db: DbSession = None,
) -> WaveformResponse:
    """Get waveform data for audio visualization.

    Args:
        project_id: Project ID
        asset_id: Asset ID
        samples: Number of peak samples to return (default 200)

    Returns:
        WaveformResponse with peaks, duration, and sample rate
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

    # Asset must be audio or video
    if asset.type not in ("audio", "video"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Asset must be audio or video type",
        )

    # Download file temporarily and generate waveform
    storage = get_storage_service()
    preview_service = PreviewService()

    with tempfile.NamedTemporaryFile(suffix=".tmp", delete=True) as tmp_file:
        # Download from GCS
        await storage.download_file(asset.storage_key, tmp_file.name)

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
    current_user: CurrentUser = None,
    db: DbSession = None,
) -> ThumbnailResponse:
    """Get a thumbnail image from a video at a specific time position.

    Args:
        project_id: Project ID
        asset_id: Asset ID
        time_ms: Time position in milliseconds (default 0)
        width: Thumbnail width (default 160)
        height: Thumbnail height (default 90)

    Returns:
        ThumbnailResponse with signed URL to the thumbnail image
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

    # Asset must be video
    if asset.type != "video":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Asset must be video type",
        )

    storage = get_storage_service()
    preview_service = PreviewService()

    # Generate a unique key for this thumbnail
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
        # Download video
        video_path = Path(tmp_dir) / "video.mp4"
        await storage.download_file(asset.storage_key, str(video_path))

        # Generate thumbnail
        thumb_path = Path(tmp_dir) / "thumb.jpg"
        try:
            preview_service.generate_thumbnail(
                str(video_path),
                str(thumb_path),
                time_ms=time_ms,
                width=width,
                height=height,
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to generate thumbnail: {str(e)}",
            )

        # Upload thumbnail to storage
        await storage.upload_file(str(thumb_path), thumb_key, "image/jpeg")

    # Generate signed URL for the thumbnail
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
