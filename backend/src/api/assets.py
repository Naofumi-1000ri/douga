import asyncio
import hashlib
import json
import logging
import re
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from src.api.deps import CurrentUser, DbSession, LightweightUser
from src.models.asset import Asset
from src.models.database import async_session_maker
from src.models.project import Project
from src.schemas.asset import (
    AssetCreate,
    AssetReference,
    AssetResponse,
    AssetUploadUrl,
    RenameRequest,
    SessionSaveRequest,
)
from src.services.audio_extractor import extract_audio_from_gcs
from src.services.chroma_key_sampler import sample_chroma_key_color
from src.services.preview_service import PreviewService
from src.services.storage_service import get_storage_service

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
    # Generate thumbnail URL with priority: thumbnail_storage_key > thumbnail_url
    thumbnail_url = None
    if asset.thumbnail_storage_key:
        try:
            thumbnail_url = storage.generate_download_url(
                storage_key=asset.thumbnail_storage_key,
                expires_minutes=60,
            )
        except Exception:
            pass  # Fall back to thumbnail_url on error
    if thumbnail_url is None and asset.thumbnail_url:
        thumbnail_url = asset.thumbnail_url  # Backward compatibility

    # Manually construct response to avoid SQLAlchemy metadata attribute conflict
    response = AssetResponse(
        id=asset.id,
        project_id=asset.project_id,
        name=asset.name,
        type=asset.type,
        subtype=asset.subtype,
        storage_key=asset.storage_key,
        storage_url=asset.storage_url,
        thumbnail_url=thumbnail_url,
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


async def _generate_video_thumbnail_background(
    project_id: UUID, asset_id: UUID, video_storage_key: str
) -> None:
    """Background task: generate thumbnail from video at frame 0 and save to GCS."""
    try:
        storage = get_storage_service()
        preview_service = PreviewService()

        # Download video to temp file
        with tempfile.TemporaryDirectory() as tmp_dir:
            video_path = Path(tmp_dir) / "video.mp4"
            thumb_path = Path(tmp_dir) / "thumb.jpg"

            await storage.download_file(video_storage_key, str(video_path))

            # Generate thumbnail at time 0 (first frame)
            preview_service.generate_thumbnail(
                str(video_path),
                str(thumb_path),
                time_ms=0,
                width=320,
                height=180,
            )

            # Upload thumbnail to GCS
            thumb_storage_key = f"thumbnails/{project_id}/{asset_id}/0.jpg"
            await storage.upload_file(str(thumb_path), thumb_storage_key, "image/jpeg")

        # Update asset's thumbnail_storage_key in DB
        async with async_session_maker() as session:
            result = await session.execute(
                select(Asset).where(Asset.id == asset_id)
            )
            asset = result.scalar_one_or_none()
            if asset is not None:
                asset.thumbnail_storage_key = thumb_storage_key
                await session.commit()
                logger.info(
                    "Generated thumbnail for video asset %s: %s",
                    asset_id,
                    thumb_storage_key,
                )
    except Exception:
        logger.exception(
            "Background thumbnail generation failed for asset %s", asset_id
        )


async def _generate_waveform_background(
    project_id: UUID,
    asset_id: UUID,
    audio_storage_key: str,
) -> None:
    """Background task: generate waveform data and save to GCS.

    Pre-generates waveform at 10 samples/second and stores as JSON in GCS.
    This enables instant waveform display without FFmpeg processing on each request.
    """
    try:
        storage = get_storage_service()
        preview_service = PreviewService()

        logger.info("Starting waveform generation for asset %s", asset_id)

        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = Path(tmp_dir) / "audio.tmp"
            await storage.download_file(audio_storage_key, str(audio_path))

            # Generate waveform at 10 samples/second
            waveform = await asyncio.to_thread(
                preview_service.generate_waveform,
                str(audio_path),
                samples_per_second=10.0,
            )

            # Save as JSON to GCS
            waveform_key = f"waveforms/{project_id}/{asset_id}.json"
            waveform_data = json.dumps({
                "peaks": waveform.peaks,
                "duration_ms": waveform.duration_ms,
                "sample_rate": waveform.sample_rate,
            })

            await asyncio.to_thread(
                storage.upload_file_content,
                waveform_data.encode("utf-8"),
                waveform_key,
                "application/json",
            )

            logger.info(
                "Waveform generated for asset %s: %d peaks",
                asset_id,
                len(waveform.peaks),
            )
    except Exception:
        logger.exception("Background waveform generation failed for asset %s", asset_id)


async def _generate_grid_thumbnails_background(
    project_id: UUID,
    asset_id: UUID,
    video_storage_key: str,
    duration_ms: int | None,
) -> None:
    """Background task: generate thumbnails at 1-second intervals for the entire video.

    These grid thumbnails enable instant display when timeline zoom changes,
    because the frontend can snap to the nearest 1-second position and fetch
    from GCS directly without needing FFmpeg processing.

    Grid thumbnails are stored as: thumbnails/{project_id}/{asset_id}/grid_{time_ms}.jpg
    """
    if not duration_ms or duration_ms <= 0:
        logger.warning(
            "Cannot generate grid thumbnails for asset %s: no duration", asset_id
        )
        return

    try:
        storage = get_storage_service()
        preview_service = PreviewService()

        # Generate signed URL for video (FFmpeg can stream directly)
        video_url = await asyncio.to_thread(
            storage.generate_download_url, video_storage_key, 30  # 30 min expiration
        )

        # Calculate all 1-second intervals
        interval_ms = 1000
        times_ms = list(range(0, duration_ms, interval_ms))

        # Limit concurrent FFmpeg processes
        semaphore = asyncio.Semaphore(2)

        # Fixed size for grid thumbnails (optimized for timeline display)
        grid_width = 160
        grid_height = 90

        logger.info(
            "Starting grid thumbnail generation for asset %s: %d thumbnails",
            asset_id,
            len(times_ms),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            async def generate_one(time_ms: int) -> bool:
                """Generate a single grid thumbnail."""
                async with semaphore:
                    thumb_key = f"thumbnails/{project_id}/{asset_id}/grid_{time_ms}.jpg"

                    # Skip if already exists
                    if await asyncio.to_thread(storage.file_exists, thumb_key):
                        return True

                    # Clamp time_ms to avoid extracting frame past video end
                    actual_time_ms = time_ms
                    if time_ms >= duration_ms - 100:
                        actual_time_ms = max(0, duration_ms - 500)

                    thumb_path = Path(tmp_dir) / f"grid_{time_ms}.jpg"

                    try:
                        await asyncio.to_thread(
                            preview_service.generate_thumbnail,
                            video_url,
                            str(thumb_path),
                            actual_time_ms,
                            grid_width,
                            grid_height,
                        )
                        await storage.upload_file(
                            str(thumb_path), thumb_key, "image/jpeg"
                        )
                        # Clean up temp file
                        thumb_path.unlink(missing_ok=True)
                        # Yield to event loop so other requests can be processed
                        await asyncio.sleep(0.1)
                        return True
                    except Exception as e:
                        logger.warning(
                            "Failed to generate grid thumbnail at %dms for asset %s: %s",
                            time_ms,
                            asset_id,
                            e,
                        )
                        return False

            # Run all thumbnail generations in parallel
            results = await asyncio.gather(*[generate_one(t) for t in times_ms])
            success_count = sum(1 for r in results if r)

            logger.info(
                "Completed grid thumbnail generation for asset %s: %d/%d successful",
                asset_id,
                success_count,
                len(times_ms),
            )

    except Exception:
        logger.exception(
            "Background grid thumbnail generation failed for asset %s", asset_id
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

        # Delete old thumbnail if exists (will be regenerated)
        if existing_asset.thumbnail_storage_key:
            try:
                storage.delete_file(existing_asset.thumbnail_storage_key)
            except Exception:
                pass
            existing_asset.thumbnail_storage_key = None

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

        # Schedule background thumbnail generation for replaced video assets
        if existing_asset.type == "video":
            background_tasks.add_task(
                _generate_video_thumbnail_background,
                project_id,
                existing_asset.id,
                existing_asset.storage_key,
            )
            # Generate grid thumbnails at 1-second intervals
            background_tasks.add_task(
                _generate_grid_thumbnails_background,
                project_id,
                existing_asset.id,
                existing_asset.storage_key,
                existing_asset.duration_ms,
            )

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

    # Schedule background tasks for video assets
    if asset.type == "video":
        # Generate thumbnail for all video assets
        background_tasks.add_task(
            _generate_video_thumbnail_background,
            project_id,
            asset.id,
            asset.storage_key,
        )
        # Generate grid thumbnails at 1-second intervals for fast timeline display
        background_tasks.add_task(
            _generate_grid_thumbnails_background,
            project_id,
            asset.id,
            asset.storage_key,
            asset.duration_ms,
        )
        # Generate waveform for audio track
        background_tasks.add_task(
            _generate_waveform_background,
            project_id,
            asset.id,
            asset.storage_key,
        )
        # Sample chroma key for avatar videos without a color set
        if asset.subtype == "avatar" and not asset.chroma_key_color:
            background_tasks.add_task(
                _sample_chroma_key_background, asset.id, asset.storage_key
            )

    # Schedule waveform generation for audio assets
    if asset.type == "audio":
        background_tasks.add_task(
            _generate_waveform_background,
            project_id,
            asset.id,
            asset.storage_key,
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
    samples: int | None = None,
    samples_per_second: float = 10.0,
    current_user: LightweightUser = None,
) -> WaveformResponse:
    """Get waveform data for audio visualization.

    First checks for pre-generated waveform in GCS (fast!).
    Falls back to on-demand generation if not available.

    Args:
        project_id: Project ID
        asset_id: Asset ID
        samples: Number of peak samples (overrides samples_per_second if set)
        samples_per_second: Samples per second of audio (default 10)

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

    storage = get_storage_service()

    # Try to get pre-generated waveform from GCS (fast path)
    waveform_key = f"waveforms/{project_id}/{asset_id}.json"
    try:
        waveform_json = await asyncio.to_thread(storage.download_file_content, waveform_key)
        if waveform_json:
            data = json.loads(waveform_json.decode("utf-8"))
            return WaveformResponse(
                peaks=data["peaks"],
                duration_ms=data["duration_ms"],
                sample_rate=data.get("sample_rate", 44100),
            )
    except Exception:
        # Pre-generated waveform not found, fall back to on-demand generation
        pass

    # Fallback: generate on-demand (slow)
    asset_storage_key = asset.storage_key
    preview_service = PreviewService()

    with tempfile.NamedTemporaryFile(suffix=".tmp", delete=True) as tmp_file:
        await storage.download_file(asset_storage_key, tmp_file.name)

        try:
            waveform = await asyncio.to_thread(
                preview_service.generate_waveform,
                tmp_file.name,
                samples=samples,
                samples_per_second=samples_per_second,
            )
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


class BatchThumbnailRequest(BaseModel):
    """Request model for batch thumbnail generation."""

    times_ms: list[int]
    width: int = 160
    height: int = 90


class BatchThumbnailResponse(BaseModel):
    """Response model for batch thumbnail generation."""

    thumbnails: list[ThumbnailResponse]
    width: int
    height: int


class GridThumbnailsResponse(BaseModel):
    """Response model for grid thumbnails (pre-generated at 1-second intervals)."""

    thumbnails: dict[int, str]  # time_ms -> signed URL
    interval_ms: int = 1000
    duration_ms: int
    width: int = 160
    height: int = 90


@router.get(
    "/projects/{project_id}/assets/{asset_id}/grid-thumbnails",
    response_model=GridThumbnailsResponse,
)
async def get_grid_thumbnails(
    project_id: UUID,
    asset_id: UUID,
    times: str | None = None,  # Comma-separated list of time_ms values (e.g., "0,5000,10000")
    current_user: LightweightUser = None,
) -> GridThumbnailsResponse:
    """Get pre-generated grid thumbnails for a video asset.

    Grid thumbnails are generated at 1-second intervals during upload.
    This endpoint returns signed URLs for grid thumbnails.

    Args:
        project_id: Project ID
        asset_id: Asset ID
        times: Optional comma-separated list of time_ms values to fetch.
               If provided, only returns URLs for those specific times (fast!).
               If not provided, returns all available thumbnails (slower).

    Returns:
        GridThumbnailsResponse with map of time_ms -> signed URL
    """
    # Short-lived DB session
    asset = await _get_asset_short_lived(project_id, asset_id, current_user.id)

    if asset.type != "video":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Asset must be video type",
        )

    duration_ms = asset.duration_ms or 0
    storage = get_storage_service()

    # If specific times requested, generate URLs directly (fast path)
    if times:
        requested_times = [int(t.strip()) for t in times.split(",") if t.strip()]

        async def generate_url_for_time(time_ms: int) -> tuple[int, str | None]:
            file_key = f"thumbnails/{project_id}/{asset_id}/grid_{time_ms}.jpg"
            try:
                url = await asyncio.to_thread(storage.generate_download_url, file_key, 60)
                return (time_ms, url)
            except Exception:
                return (time_ms, None)

        results = await asyncio.gather(*[generate_url_for_time(t) for t in requested_times])
        thumbnails = {t: url for t, url in results if url is not None}

        return GridThumbnailsResponse(
            thumbnails=thumbnails,
            interval_ms=1000,
            duration_ms=duration_ms,
        )

    # Full list mode: list all files and generate URLs
    grid_prefix = f"thumbnails/{project_id}/{asset_id}/grid_"
    existing_files = await asyncio.to_thread(storage.list_files, grid_prefix)

    # Parse time_ms from filenames
    file_info: list[tuple[int, str]] = []
    for file_key in existing_files:
        # Extract time_ms from "thumbnails/.../grid_{time_ms}.jpg"
        try:
            filename = file_key.split("/")[-1]  # "grid_1000.jpg"
            time_str = filename.replace("grid_", "").replace(".jpg", "")
            time_ms = int(time_str)
            file_info.append((time_ms, file_key))
        except (ValueError, IndexError):
            continue

    # Generate all signed URLs in parallel
    async def generate_url(file_key: str) -> str:
        return await asyncio.to_thread(storage.generate_download_url, file_key, 60)

    urls = await asyncio.gather(*[generate_url(fk) for _, fk in file_info])

    # Build result map
    thumbnails: dict[int, str] = {
        time_ms: url for (time_ms, _), url in zip(file_info, urls)
    }

    return GridThumbnailsResponse(
        thumbnails=thumbnails,
        interval_ms=1000,
        duration_ms=duration_ms,
    )


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


@router.post(
    "/projects/{project_id}/assets/{asset_id}/thumbnails/batch",
    response_model=BatchThumbnailResponse,
)
async def get_batch_thumbnails(
    project_id: UUID,
    asset_id: UUID,
    request: BatchThumbnailRequest,
    current_user: LightweightUser = None,
) -> BatchThumbnailResponse:
    """Get multiple thumbnail images from a video at specific time positions in a single request.

    This is more efficient than making multiple single thumbnail requests because:
    1. Only one DB query is needed
    2. The video is downloaded only once
    3. Fewer HTTP round-trips
    4. GCS existence check is batched via list_files
    5. FFmpeg thumbnail generation runs in parallel (up to 4 concurrent)

    Args:
        project_id: Project ID
        asset_id: Asset ID
        request: BatchThumbnailRequest with times_ms, width, height

    Returns:
        BatchThumbnailResponse with list of thumbnails
    """
    # Limit number of thumbnails per request to prevent abuse
    MAX_THUMBNAILS = 30
    if len(request.times_ms) > MAX_THUMBNAILS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Maximum {MAX_THUMBNAILS} thumbnails per request",
        )

    if len(request.times_ms) == 0:
        return BatchThumbnailResponse(
            thumbnails=[],
            width=request.width,
            height=request.height,
        )

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

    # 1. Batch existence check: list all thumbnails for this asset at once
    thumb_prefix = f"thumbnails/{project_id}/{asset_id}/"
    existing_files = await asyncio.to_thread(storage.list_files, thumb_prefix)
    existing_set = set(existing_files)

    # 2. Separate cached vs uncached thumbnails
    cached_results: list[tuple[int, str]] = []  # (time_ms, thumb_key)
    uncached_times: list[int] = []

    for time_ms in request.times_ms:
        thumb_key = f"thumbnails/{project_id}/{asset_id}/{time_ms}_{request.width}x{request.height}.jpg"
        if thumb_key in existing_set:
            cached_results.append((time_ms, thumb_key))
        else:
            uncached_times.append(time_ms)

    # If all thumbnails are cached, generate signed URLs and return early
    if not uncached_times:
        thumbnails: list[ThumbnailResponse] = []
        for time_ms, thumb_key in cached_results:
            url = await asyncio.to_thread(
                storage.generate_download_url, thumb_key, 60
            )
            thumbnails.append(ThumbnailResponse(
                url=url,
                time_ms=time_ms,
                width=request.width,
                height=request.height,
            ))
        thumbnails.sort(key=lambda t: t.time_ms)
        return BatchThumbnailResponse(
            thumbnails=thumbnails,
            width=request.width,
            height=request.height,
        )

    # 3. Generate signed URL for video (FFmpeg can read directly from URL - no download needed!)
    video_url = await asyncio.to_thread(
        storage.generate_download_url, asset_storage_key, 15  # 15 min expiration
    )

    # 4. Generate uncached thumbnails in parallel with Semaphore (limit to 4 concurrent)
    # Each FFmpeg call streams from the signed URL and seeks to the target frame
    semaphore = asyncio.Semaphore(4)

    with tempfile.TemporaryDirectory() as tmp_dir:
        async def generate_one(time_ms: int) -> tuple[int, str] | None:
            """Generate a single thumbnail and upload to storage."""
            async with semaphore:
                # Clamp time_ms to avoid extracting frame at or past the video end
                actual_time_ms = time_ms
                if asset_duration_ms and time_ms >= asset_duration_ms - 100:
                    actual_time_ms = max(0, asset_duration_ms - 500)

                thumb_filename = f"thumb_{time_ms}.jpg"
                thumb_path = Path(tmp_dir) / thumb_filename
                thumb_key = f"thumbnails/{project_id}/{asset_id}/{time_ms}_{request.width}x{request.height}.jpg"

                try:
                    # FFmpeg is synchronous, offload to thread pool
                    # Pass signed URL directly - FFmpeg will stream and seek efficiently
                    await asyncio.to_thread(
                        preview_service.generate_thumbnail,
                        video_url,  # Direct URL instead of local file
                        str(thumb_path),
                        actual_time_ms,
                        request.width,
                        request.height,
                    )
                    await storage.upload_file(str(thumb_path), thumb_key, "image/jpeg")
                    return (time_ms, thumb_key)
                except Exception as e:
                    logger.warning(f"Failed to generate thumbnail at {time_ms}ms: {e}")
                    return None

        # Run all thumbnail generations in parallel
        results = await asyncio.gather(*[generate_one(t) for t in uncached_times])

        # Collect successful results
        for result in results:
            if result is not None:
                cached_results.append(result)

    # 5. Generate signed URLs for all thumbnails
    thumbnails: list[ThumbnailResponse] = []
    for time_ms, thumb_key in cached_results:
        url = await asyncio.to_thread(
            storage.generate_download_url, thumb_key, 60
        )
        thumbnails.append(ThumbnailResponse(
            url=url,
            time_ms=time_ms,
            width=request.width,
            height=request.height,
        ))

    # Sort thumbnails by time_ms to match request order
    thumbnails.sort(key=lambda t: t.time_ms)

    return BatchThumbnailResponse(
        thumbnails=thumbnails,
        width=request.width,
        height=request.height,
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
        except TimeoutError:
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

        # Check for duplicate name and find next available number
        base_name = safe_name
        counter = 1
        while True:
            result = await db.execute(
                select(Asset).where(
                    Asset.project_id == project_id,
                    Asset.name == safe_name,
                    Asset.type == "session",
                ).limit(1)
            )
            existing = result.scalar_one_or_none()
            if not existing:
                break
            # Name exists, try with next number
            safe_name = f"{base_name}_{counter}"
            counter += 1

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
    except TimeoutError:
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
        with open(tmp.name, encoding='utf-8') as f:
            session_data = json.load(f)

    return session_data


@router.patch(
    "/projects/{project_id}/assets/{asset_id}/rename",
    response_model=AssetResponse,
)
async def rename_asset(
    project_id: UUID,
    asset_id: UUID,
    rename_data: RenameRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> AssetResponse:
    """
    Rename an asset.

    - Sanitizes the new name
    - For session assets, also renames the GCS file (updates storage_key and storage_url)
    - Updates the DB name
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

    # Sanitize new name
    new_name = sanitize_session_name(rename_data.name)
    if not new_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid name after sanitization",
        )

    # Check if name already taken (for same type in same project)
    if new_name != asset.name:
        result = await db.execute(
            select(Asset).where(
                Asset.project_id == project_id,
                Asset.name == new_name,
                Asset.type == asset.type,
                Asset.id != asset_id,
            ).limit(1)
        )
        existing = result.scalar_one_or_none()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Asset with name '{new_name}' already exists",
            )

    storage = get_storage_service()

    # For session assets, rename the GCS file
    if asset.type == "session" and asset.storage_key and new_name != asset.name:
        old_key = asset.storage_key
        new_key = f"sessions/{project_id}/{new_name}.json"

        try:
            # Copy to new location
            storage.copy_file(old_key, new_key)
            # Delete old file
            storage.delete_file(old_key)
            # Update asset references
            asset.storage_key = new_key
            asset.storage_url = storage.get_public_url(new_key)
        except Exception as e:
            logger.warning(f"Failed to rename GCS file for asset {asset_id}: {e}")
            # Continue with DB rename even if GCS rename fails

    # Update name in DB
    asset.name = new_name
    await db.flush()
    await db.refresh(asset)

    return _asset_to_response_with_signed_url(asset, storage)


class RegenerateGridThumbnailsResponse(BaseModel):
    """Response model for grid thumbnail regeneration."""

    asset_id: str
    status: str
    message: str


@router.post(
    "/projects/{project_id}/assets/{asset_id}/regenerate-grid-thumbnails",
    response_model=RegenerateGridThumbnailsResponse,
)
async def regenerate_grid_thumbnails(
    project_id: UUID,
    asset_id: UUID,
) -> RegenerateGridThumbnailsResponse:
    """Regenerate grid thumbnails for an existing video asset.

    This is useful for migrating existing videos to use the new grid thumbnail system.
    Grid thumbnails are generated at 1-second intervals and stored in GCS.

    Runs synchronously to avoid Cloud Run BackgroundTask interruption.

    NOTE: This endpoint is temporarily unauthenticated for migration purposes.
    """
    # Short-lived DB session (no auth check for migration)
    async with async_session_maker() as db:
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

    if asset.type != "video":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Asset must be video type",
        )

    if not asset.duration_ms:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Asset has no duration information",
        )

    # Run synchronously (Cloud Run kills BackgroundTasks on scale-down)
    await _generate_grid_thumbnails_background(
        project_id,
        asset_id,
        asset.storage_key,
        asset.duration_ms,
    )

    thumbnail_count = (asset.duration_ms // 1000) + 1
    return RegenerateGridThumbnailsResponse(
        asset_id=str(asset_id),
        status="completed",
        message=f"Grid thumbnail generation completed. Generated ~{thumbnail_count} thumbnails.",
    )


class RegenerateWaveformResponse(BaseModel):
    """Response model for waveform regeneration."""

    asset_id: str
    status: str
    message: str


@router.post(
    "/projects/{project_id}/assets/{asset_id}/regenerate-waveform",
    response_model=RegenerateWaveformResponse,
)
async def regenerate_waveform(
    project_id: UUID,
    asset_id: UUID,
    background_tasks: BackgroundTasks,
) -> RegenerateWaveformResponse:
    """Regenerate waveform data for an existing audio/video asset.

    This is useful for migrating existing assets to use the new pre-generated waveform system.
    Waveform data is generated at 10 samples/second and stored as JSON in GCS.

    The generation happens in the background, so this endpoint returns immediately.

    NOTE: This endpoint is temporarily unauthenticated for migration purposes.
    """
    # Short-lived DB session (no auth check for migration)
    async with async_session_maker() as db:
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

    if asset.type not in ("audio", "video"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Asset must be audio or video type",
        )

    # Schedule background task
    background_tasks.add_task(
        _generate_waveform_background,
        project_id,
        asset_id,
        asset.storage_key,
    )

    return RegenerateWaveformResponse(
        asset_id=str(asset_id),
        status="started",
        message="Waveform generation started in background.",
    )
