import tempfile
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
) -> list[AssetResponse]:
    """List all assets for a project."""
    await verify_project_access(project_id, current_user.id, db)

    result = await db.execute(
        select(Asset)
        .where(Asset.project_id == project_id)
        .order_by(Asset.created_at.desc())
    )
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

    # Create new audio asset
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
    )
    db.add(audio_asset)
    await db.flush()
    await db.refresh(audio_asset)

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
        storage.download_file(asset.storage_key, tmp_file.name)

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
