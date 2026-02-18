"""AI Video production API endpoints.

Routes under /api/ai-video/projects/{project_id}/...
"""

import asyncio
import logging
import os
import shutil
import tempfile
import time
import uuid as uuid_mod
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from src.api.access import get_accessible_project
from src.api.deps import CurrentUser, DbSession, LightweightUser
from src.models.asset import Asset
from src.models.database import async_session_maker
from src.models.project import Project
from src.schemas.ai_video import (
    AssetCatalogEntry,
    AssetCatalogResponse,
    AssetCatalogSummary,
    BatchUploadResponse,
    BatchUploadResult,
    GeneratePlanRequest,
    LayoutRequest,
    PlanApplyResponse,
    ReclassifyAssetRequest,
    RunAllResponse,
    RunAllSkillResult,
    SkillResponse,
    TranscriptionResponse,
    UpdatePlanRequest,
    VideoBrief,
    VideoPlan,
)
from src.services.asset_classifier import classify_asset
from src.services.audio_extractor import extract_audio_from_gcs
from src.services.chroma_key_sampler import sample_chroma_key_color
from src.services.plan_to_timeline import plan_to_timeline
from src.services.click_detector import detect_clicks
from src.services.smart_sync_service import compute_smart_cut, compute_smart_sync
from src.services.storage_service import get_storage_service
from src.schemas.quality_check import CheckRequest, CheckResponse
from src.services.quality_checker import QualityChecker

logger = logging.getLogger(__name__)
router = APIRouter()


# =============================================================================
# Helpers
# =============================================================================


async def _get_project(project_id: UUID, user_id: UUID, db) -> Project:
    """Get project with access check (ownership or membership)."""
    return await get_accessible_project(project_id, user_id, db)


def _get_mime_type(filename: str) -> str:
    """Infer MIME type from filename extension."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime_map = {
        "mp4": "video/mp4",
        "mov": "video/quicktime",
        "avi": "video/x-msvideo",
        "webm": "video/webm",
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "aac": "audio/aac",
        "ogg": "audio/ogg",
        "m4a": "audio/mp4",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
    }
    return mime_map.get(ext, "application/octet-stream")


# =============================================================================
# Video Production Capabilities (Workflow Guide for AI)
# =============================================================================


@router.get("/capabilities")
async def get_video_capabilities(
    current_user: LightweightUser,
):
    """Return the full AI video production workflow and skill specs.

    This endpoint tells the AI what tools are available, what order to run them,
    and what each skill does. Response is static and can be cached by clients.
    """
    return JSONResponse(
        content={
            "version": "1.0.0",
            "workflow": [
                {
                    "step": 1,
                    "endpoint": "POST /api/ai-video/projects/{id}/assets/batch-upload",
                    "description": "Upload all assets (video, audio, images). Metadata is probed synchronously — duration, dimensions, chroma key color, and thumbnails are available immediately.",
                },
                {
                    "step": 2,
                    "endpoint": "GET /api/ai-video/projects/{id}/asset-catalog",
                    "description": "Review enriched asset catalog. Verify all assets have correct type/subtype. Use PUT .../reclassify to fix misclassifications.",
                },
                {
                    "step": 3,
                    "endpoint": "POST /api/ai-video/projects/{id}/plan/generate",
                    "description": "Generate a VideoPlan from a brief + asset catalog. GPT-4o creates a structured plan with sections and element placements.",
                },
                {
                    "step": 4,
                    "endpoint": "POST /api/ai-video/projects/{id}/plan/apply",
                    "description": "Convert plan to timeline_data. Deterministic conversion + auto audio extraction from avatar videos + chroma key auto-apply.",
                },
                {
                    "step": 5,
                    "endpoint": "POST /api/ai-video/projects/{id}/skills/trim-silence",
                    "description": "Trim leading/trailing silence from narration. Also trims linked avatar clips via group_id.",
                    "shortcut": "POST /api/ai-video/projects/{id}/skills/run-all",
                    "shortcut_note": "Steps 5-10 can be replaced by a single run-all call.",
                },
                {
                    "step": 6,
                    "endpoint": "POST /api/ai-video/projects/{id}/skills/add-telop",
                    "description": "Transcribe narration (Whisper STT) and place text clips on text layer. Stores transcription in metadata for other skills.",
                },
                {
                    "step": 7,
                    "endpoint": "POST /api/ai-video/projects/{id}/skills/layout",
                    "description": "Apply layout transforms. Optional JSON body: {avatar_position, avatar_size, screen_position}. Defaults: bottom-right/pip/fullscreen.",
                },
                {
                    "step": 8,
                    "endpoint": "POST /api/ai-video/projects/{id}/skills/sync-content",
                    "description": "Sync operation screen to narration timing. Speech segments at moderate speed, silence gaps at accelerated speed.",
                },
                {
                    "step": 9,
                    "endpoint": "POST /api/ai-video/projects/{id}/skills/click-highlight",
                    "description": "Detect clicks in operation screen video and add highlight rectangle shapes to effects layer.",
                },
                {
                    "step": 10,
                    "endpoint": "POST /api/ai-video/projects/{id}/skills/avatar-dodge",
                    "description": "Add dodge keyframes to avatar when click highlights overlap. Avatar moves out of the way with 100ms transition.",
                },
                {
                    "step": 11,
                    "endpoint": "GET /api/ai/v1/projects/{id}/timeline-overview",
                    "description": "Review the full timeline: all clips with asset names, gaps, overlaps, and warnings.",
                },
                {
                    "step": 12,
                    "endpoint": "POST /api/preview/projects/{id}/sample-event-points",
                    "description": "Get preview frames at key moments to verify the result visually.",
                },
                {
                    "step": 13,
                    "endpoint": "POST /api/ai-video/projects/{id}/check",
                    "description": "Quality check: validates structure, plan-vs-actual, narration sync, and material gaps. Returns scores and auto-fix recommendations.",
                },
            ],
            "convenience_endpoints": [
                {
                    "endpoint": "POST /api/ai-video/projects/{id}/skills/run-all",
                    "description": "Run all 6 skills in correct dependency order (steps 5-10) in one call. Stops on first failure.",
                },
                {
                    "endpoint": "GET /api/ai-video/projects/{id}/assets/{asset_id}/transcription",
                    "description": "Get STT transcription for an asset. Available after add-telop skill has run.",
                },
            ],
            "skills": [
                {
                    "name": "trim-silence",
                    "description": "Trim leading/trailing silence from narration audio clips. Also trims linked avatar video clips via group_id.",
                    "prerequisites": ["plan/apply"],
                    "idempotent": True,
                },
                {
                    "name": "add-telop",
                    "description": "Transcribe narration via Whisper STT and create text clips on the text layer. Stores transcription in timeline metadata.",
                    "prerequisites": ["plan/apply"],
                    "idempotent": True,
                },
                {
                    "name": "layout",
                    "description": "Apply layout transforms to avatar, screen, and slide clips.",
                    "prerequisites": ["plan/apply"],
                    "idempotent": True,
                    "accepts_body": True,
                    "parameters": {
                        "avatar_position": {
                            "type": "string",
                            "enum": ["bottom-right", "bottom-left", "top-right", "top-left", "center-right", "center-left"],
                            "default": "bottom-right",
                        },
                        "avatar_size": {
                            "type": "string",
                            "enum": ["pip", "medium", "large", "fullscreen"],
                            "default": "pip",
                        },
                        "screen_position": {
                            "type": "string",
                            "enum": ["fullscreen", "left-half", "right-half"],
                            "default": "fullscreen",
                        },
                    },
                },
                {
                    "name": "sync-content",
                    "description": "Variable-speed sync of operation screen to narration. Speech at base_speed, gaps at 2.5x base_speed. Requires add-telop first.",
                    "prerequisites": ["plan/apply", "add-telop"],
                    "idempotent": True,
                },
                {
                    "name": "click-highlight",
                    "description": "Detect localized visual changes (clicks) in operation screen and add highlight rectangles to effects layer.",
                    "prerequisites": ["plan/apply"],
                    "idempotent": True,
                },
                {
                    "name": "avatar-dodge",
                    "description": "Move avatar out of the way when click highlights overlap its position. Adds 100ms dodge keyframes.",
                    "prerequisites": ["plan/apply", "click-highlight"],
                    "idempotent": True,
                },
            ],
            "skill_execution_order": [
                "trim-silence",
                "add-telop",
                "layout",
                "sync-content",
                "click-highlight",
                "avatar-dodge",
            ],
            "skill_dependency_graph": {
                "trim-silence": [],
                "add-telop": [],
                "layout": [],
                "sync-content": ["add-telop"],
                "click-highlight": [],
                "avatar-dodge": ["click-highlight"],
            },
            "asset_types": [
                {
                    "type": "video",
                    "subtype": "avatar",
                    "description": "Green-screen avatar video. Auto-detected chroma key color. Audio is auto-extracted as narration.",
                },
                {
                    "type": "video",
                    "subtype": "screen",
                    "description": "Operation screen capture. Used in content layer. Click detection and variable-speed sync applied.",
                },
                {
                    "type": "video",
                    "subtype": "background",
                    "description": "Background video loop for L1 layer.",
                },
                {
                    "type": "audio",
                    "subtype": "narration",
                    "description": "Narration audio. Used for STT, silence trimming, and content sync.",
                },
                {
                    "type": "audio",
                    "subtype": "bgm",
                    "description": "Background music. Auto-ducking available.",
                },
                {
                    "type": "audio",
                    "subtype": "se",
                    "description": "Sound effects. Short audio clips.",
                },
                {
                    "type": "image",
                    "subtype": "slide",
                    "description": "Slide images for content layer.",
                },
                {
                    "type": "image",
                    "subtype": "background",
                    "description": "Background images for L1 layer.",
                },
            ],
            "preview_endpoints": [
                {
                    "endpoint": "POST /api/preview/projects/{id}/sample-frame",
                    "description": "Render a single preview frame at a given time_ms.",
                },
                {
                    "endpoint": "POST /api/preview/projects/{id}/sample-event-points",
                    "description": "Auto-select key moments and render preview frames.",
                },
                {
                    "endpoint": "POST /api/preview/projects/{id}/validate-composition",
                    "description": "Validate composition (missing assets, overlaps, etc.).",
                },
                {
                    "endpoint": "POST /api/ai-video/projects/{id}/check",
                    "description": "Comprehensive quality check with scores (0-100) and fix recommendations. Levels: quick, standard, deep.",
                },
            ],
            "output_spec": {
                "resolution": "1920x1080",
                "fps": 30,
                "video_codec": "H.264",
                "audio_codec": "AAC",
                "container": "MP4",
                "standard": "Udemy recommended format",
            },
        },
        headers={"Cache-Control": "public, max-age=86400"},
    )


# =============================================================================
# Batch Upload
# =============================================================================


@router.post(
    "/projects/{project_id}/assets/batch-upload",
    response_model=BatchUploadResponse,
)
async def batch_upload_assets(
    project_id: UUID,
    current_user: LightweightUser,
    files: list[UploadFile] = File(...),
) -> BatchUploadResponse:
    """Upload multiple files at once with automatic classification.

    Accepts multipart form data with multiple files.
    Each file is uploaded to storage and classified automatically.
    Metadata (duration, dimensions, chroma key, thumbnail) is probed synchronously
    so that all data is available immediately after upload.
    Files are processed concurrently (up to 3 at a time) for performance.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    # Verify project access once (not per-file)
    async with async_session_maker() as db:
        await get_accessible_project(project_id, current_user.id, db)

    storage = get_storage_service()
    semaphore = asyncio.Semaphore(3)

    async def _process_one(upload_file: UploadFile) -> BatchUploadResult:
        filename = upload_file.filename or f"unnamed_{uuid_mod.uuid4()}"
        storage_key = ""
        async with semaphore:
            try:
                # Stream to tempfile to avoid holding full content in memory
                content = await upload_file.read()
                file_size = len(content)
                mime_type = upload_file.content_type or _get_mime_type(filename)

                # Upload to storage
                ext = filename.rsplit(".", 1)[-1] if "." in filename else ""
                asset_uuid = uuid_mod.uuid4()
                storage_key = f"projects/{project_id}/assets/{asset_uuid}.{ext}"
                storage.upload_file_from_bytes(storage_key, content)
                storage_url = storage.get_public_url(storage_key)

                # Synchronous probe for media files
                duration_ms = None
                width = None
                height = None
                has_audio = None
                chroma_color = None
                thumbnail_storage_key = None

                if mime_type.startswith(("video/", "audio/")):
                    try:
                        duration_ms, width, height, has_audio = await _probe_media(
                            content, filename
                        )
                    except Exception as e:
                        logger.warning("Probe failed for %s: %s", filename, e)

                # Classify with real metadata (not NULL)
                classification = classify_asset(
                    filename=filename,
                    mime_type=mime_type,
                    duration_ms=duration_ms,
                    has_audio=has_audio,
                    width=width,
                    height=height,
                )

                # Chroma key sampling for avatar videos
                if classification.subtype == "avatar" and mime_type.startswith("video/"):
                    try:
                        chroma_color = await _sample_chroma_key_sync(content, filename)
                    except Exception as e:
                        logger.warning("Chroma sampling failed for %s: %s", filename, e)

                # Thumbnail generation for video files
                if mime_type.startswith("video/"):
                    try:
                        thumbnail_storage_key = await _generate_thumbnail_sync(
                            content, filename, project_id, asset_uuid
                        )
                    except Exception as e:
                        logger.warning("Thumbnail generation failed for %s: %s", filename, e)

                # Save to DB
                async with async_session_maker() as db:
                    asset = Asset(
                        project_id=project_id,
                        name=filename,
                        type=classification.type,
                        subtype=classification.subtype,
                        storage_key=storage_key,
                        storage_url=storage_url,
                        file_size=file_size,
                        mime_type=mime_type,
                        duration_ms=duration_ms,
                        width=width,
                        height=height,
                        chroma_key_color=chroma_color,
                        thumbnail_storage_key=thumbnail_storage_key,
                    )
                    db.add(asset)
                    await db.commit()
                    await db.refresh(asset)

                    return BatchUploadResult(
                        filename=filename,
                        asset_id=asset.id,
                        type=classification.type,
                        subtype=classification.subtype,
                        confidence=classification.confidence,
                        duration_ms=duration_ms,
                        width=width,
                        height=height,
                        chroma_key_color=chroma_color,
                        has_thumbnail=thumbnail_storage_key is not None,
                    )

            except Exception as e:
                logger.error("Failed to upload %s: %s", filename, e)
                # Clean up orphaned GCS object
                if storage_key:
                    try:
                        storage.delete_file(storage_key)
                    except Exception:
                        logger.warning("Failed to clean up GCS object: %s", storage_key)
                return BatchUploadResult(
                    filename=filename,
                    asset_id=None,
                    type="unknown",
                    subtype="other",
                    confidence=0.0,
                    error=str(e),
                )

    # Process all files concurrently (semaphore limits to 3 at a time)
    batch_results = await asyncio.gather(*[_process_one(f) for f in files])
    results = list(batch_results)
    success = sum(1 for r in results if r.error is None)
    failed = sum(1 for r in results if r.error is not None)

    return BatchUploadResponse(
        project_id=project_id,
        results=results,
        total=len(files),
        success=success,
        failed=failed,
    )


async def _probe_media(
    content: bytes, filename: str
) -> tuple[int | None, int | None, int | None, bool | None]:
    """Probe media file for metadata using FFprobe.

    Returns: (duration_ms, width, height, has_audio)
    """
    from src.utils.media_info import get_media_info

    with tempfile.NamedTemporaryFile(
        suffix=Path(filename).suffix, delete=True
    ) as tmp:
        tmp.write(content)
        tmp.flush()

        info = await asyncio.to_thread(get_media_info, tmp.name)

    return (
        info.get("duration_ms"),
        info.get("width"),
        info.get("height"),
        info.get("has_audio"),
    )


async def _sample_chroma_key_sync(content: bytes, filename: str) -> str | None:
    """Synchronously sample chroma key color from video bytes.

    Returns hex color string or None if no valid chroma key detected.
    """
    with tempfile.NamedTemporaryFile(
        suffix=Path(filename).suffix, delete=True
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        return await asyncio.to_thread(sample_chroma_key_color, tmp.name)


async def _generate_thumbnail_sync(
    content: bytes,
    filename: str,
    project_id: UUID,
    asset_uuid: uuid_mod.UUID,
) -> str | None:
    """Synchronously generate a thumbnail from video bytes, upload to GCS.

    Returns the storage key of the uploaded thumbnail, or None on failure.
    """
    import subprocess

    from src.config import get_settings

    settings = get_settings()
    storage = get_storage_service()

    with tempfile.NamedTemporaryFile(
        suffix=Path(filename).suffix, delete=True, prefix="thumb_src_"
    ) as tmp_video:
        tmp_video.write(content)
        tmp_video.flush()

        with tempfile.NamedTemporaryFile(
            suffix=".jpg", delete=True, prefix="thumb_out_"
        ) as tmp_thumb:
            cmd = [
                settings.ffmpeg_path,
                "-ss", "0.5",
                "-i", tmp_video.name,
                "-frames:v", "1",
                "-vf", "scale=320:-1",
                "-q:v", "5",
                "-y",
                tmp_thumb.name,
            ]
            result = await asyncio.to_thread(
                subprocess.run, cmd, capture_output=True, text=True, timeout=15
            )
            if result.returncode != 0:
                logger.warning("Thumbnail ffmpeg failed: %s", result.stderr[:200])
                return None

            thumb_key = f"projects/{project_id}/thumbnails/{asset_uuid}.jpg"
            with open(tmp_thumb.name, "rb") as f:
                thumb_bytes = f.read()
            if len(thumb_bytes) < 100:
                return None
            storage.upload_file_from_bytes(thumb_key, thumb_bytes)
            return thumb_key


# =============================================================================
# Asset Catalog
# =============================================================================


@router.get(
    "/projects/{project_id}/asset-catalog",
    response_model=AssetCatalogResponse,
)
async def get_asset_catalog(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> AssetCatalogResponse:
    """Get an AI-oriented asset catalog for the project.

    Returns a concise summary of all assets suitable for AI plan generation.
    """
    await _get_project(project_id, current_user.id, db)

    result = await db.execute(
        select(Asset)
        .where(Asset.project_id == project_id)
        .where(Asset.is_internal == False)  # noqa: E712
        .order_by(Asset.created_at)
    )
    assets = result.scalars().all()

    entries = []
    by_type: dict[str, int] = {}
    by_subtype: dict[str, int] = {}
    total_video_duration = 0
    total_audio_duration = 0

    for asset in assets:
        file_size_mb = round(asset.file_size / (1024 * 1024), 1) if asset.file_size else None
        entries.append(AssetCatalogEntry(
            id=asset.id,
            name=asset.name,
            type=asset.type,
            subtype=asset.subtype,
            duration_ms=asset.duration_ms,
            width=asset.width,
            height=asset.height,
            has_audio=None,  # Not stored in Asset model
            file_size_mb=file_size_mb,
            chroma_key_color=asset.chroma_key_color,
            has_thumbnail=asset.thumbnail_storage_key is not None,
        ))

        by_type[asset.type] = by_type.get(asset.type, 0) + 1
        by_subtype[asset.subtype] = by_subtype.get(asset.subtype, 0) + 1

        if asset.type == "video" and asset.duration_ms:
            total_video_duration += asset.duration_ms
        elif asset.type == "audio" and asset.duration_ms:
            total_audio_duration += asset.duration_ms

    summary = AssetCatalogSummary(
        total=len(entries),
        by_type=by_type,
        by_subtype=by_subtype,
        total_video_duration_ms=total_video_duration,
        total_audio_duration_ms=total_audio_duration,
    )

    return AssetCatalogResponse(
        project_id=project_id,
        assets=entries,
        summary=summary,
    )


# =============================================================================
# Reclassify Asset
# =============================================================================


@router.put(
    "/projects/{project_id}/assets/{asset_id}/reclassify",
)
async def reclassify_asset(
    project_id: UUID,
    asset_id: UUID,
    body: ReclassifyAssetRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> dict:
    """Manually reclassify an asset's type and subtype."""
    await _get_project(project_id, current_user.id, db)

    result = await db.execute(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.project_id == project_id,
        )
    )
    asset = result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    asset.type = body.type
    asset.subtype = body.subtype
    await db.flush()

    return {"status": "ok", "asset_id": str(asset_id), "type": body.type, "subtype": body.subtype}


# =============================================================================
# Asset Transcription
# =============================================================================


@router.get(
    "/projects/{project_id}/assets/{asset_id}/transcription",
    response_model=TranscriptionResponse,
)
async def get_asset_transcription(
    project_id: UUID,
    asset_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> TranscriptionResponse:
    """Get the transcription data for a narration asset.

    Transcription is stored in asset_metadata.transcription by the add-telop skill.
    Returns segments with text, timestamps, and timeline positions.
    """
    await _get_project(project_id, current_user.id, db)

    result = await db.execute(
        select(Asset).where(
            Asset.id == asset_id,
            Asset.project_id == project_id,
        )
    )
    asset = result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found")

    metadata = asset.asset_metadata or {}
    transcription = metadata.get("transcription")
    if transcription is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No transcription found. Run the add-telop skill first.",
        )

    # Build full_text from segments
    segments = transcription.get("segments", [])
    full_text = " ".join(s.get("text", "") for s in segments) if segments else None

    return TranscriptionResponse(
        asset_id=str(asset_id),
        asset_name=asset.name,
        segments=segments,
        total_segments=transcription.get("total_segments", len(segments)),
        full_text=full_text,
        language=transcription.get("language"),
    )


# =============================================================================
# Plan Generation
# =============================================================================


@router.post(
    "/projects/{project_id}/plan/generate",
)
async def generate_plan(
    project_id: UUID,
    body: GeneratePlanRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> dict:
    """Generate a VideoPlan from brief + asset catalog using AI.

    Stores the brief and generated plan on the project.
    """
    from src.services.ai_video_service import generate_video_plan

    project = await _get_project(project_id, current_user.id, db)

    # Build catalog
    result = await db.execute(
        select(Asset)
        .where(Asset.project_id == project_id)
        .where(Asset.is_internal == False)  # noqa: E712
        .order_by(Asset.created_at)
    )
    assets = result.scalars().all()

    entries = []
    by_type: dict[str, int] = {}
    by_subtype: dict[str, int] = {}
    total_video_duration = 0
    total_audio_duration = 0

    for asset in assets:
        file_size_mb = round(asset.file_size / (1024 * 1024), 1) if asset.file_size else None
        entries.append(AssetCatalogEntry(
            id=asset.id,
            name=asset.name,
            type=asset.type,
            subtype=asset.subtype,
            duration_ms=asset.duration_ms,
            width=asset.width,
            height=asset.height,
            file_size_mb=file_size_mb,
        ))
        by_type[asset.type] = by_type.get(asset.type, 0) + 1
        by_subtype[asset.subtype] = by_subtype.get(asset.subtype, 0) + 1
        if asset.type == "video" and asset.duration_ms:
            total_video_duration += asset.duration_ms
        elif asset.type == "audio" and asset.duration_ms:
            total_audio_duration += asset.duration_ms

    catalog = AssetCatalogResponse(
        project_id=project_id,
        assets=entries,
        summary=AssetCatalogSummary(
            total=len(entries),
            by_type=by_type,
            by_subtype=by_subtype,
            total_video_duration_ms=total_video_duration,
            total_audio_duration_ms=total_audio_duration,
        ),
    )

    # Generate plan via AI
    plan = await generate_video_plan(brief=body.brief, catalog=catalog)

    # Store brief and plan on project
    project.video_brief = body.brief.model_dump()
    project.video_plan = plan.model_dump()
    flag_modified(project, "video_brief")
    flag_modified(project, "video_plan")
    await db.flush()

    return plan.model_dump()


@router.get("/projects/{project_id}/plan")
async def get_plan(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> dict:
    """Get the current video plan for a project."""
    project = await _get_project(project_id, current_user.id, db)

    if project.video_plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No video plan exists for this project",
        )

    return project.video_plan


@router.put("/projects/{project_id}/plan")
async def update_plan(
    project_id: UUID,
    body: UpdatePlanRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> dict:
    """Update/replace the video plan for a project."""
    project = await _get_project(project_id, current_user.id, db)

    project.video_plan = body.plan.model_dump()
    flag_modified(project, "video_plan")
    await db.flush()

    return project.video_plan


# =============================================================================
# Apply Plan → Timeline
# =============================================================================


async def _apply_chroma_key_to_avatars(
    timeline_data: dict,
    project_id: UUID,
    db,
) -> None:
    """Apply chroma key settings to avatar video clips (without audio extraction).

    Used when the plan already placed narration clips so we skip audio extraction
    but still need to enable chroma key on avatar clips.
    """
    # Collect unique asset_ids from all video layer clips
    clip_asset_ids: set[str] = set()
    for layer in timeline_data["layers"]:
        for clip in layer["clips"]:
            aid = clip.get("asset_id")
            if aid:
                clip_asset_ids.add(aid)

    if not clip_asset_ids:
        return

    result = await db.execute(
        select(Asset).where(
            Asset.id.in_([UUID(aid) for aid in clip_asset_ids]),
            Asset.type == "video",
            Asset.subtype == "avatar",
        )
    )
    avatar_assets = {str(a.id): a for a in result.scalars().all()}
    if not avatar_assets:
        return

    for layer in timeline_data["layers"]:
        for clip in layer["clips"]:
            aid = clip.get("asset_id")
            if not aid or aid not in avatar_assets:
                continue
            video_asset = avatar_assets[aid]
            if video_asset.chroma_key_color:
                clip.setdefault("effects", {})["chroma_key"] = {
                    "enabled": True,
                    "color": video_asset.chroma_key_color,
                    "similarity": 0.4,
                    "blend": 0.1,
                }


async def _enrich_timeline_audio(
    timeline_data: dict,
    project_id: UUID,
    db,
) -> None:
    """Auto-extract audio from avatar video clips and add to narration track.

    For each avatar video clip in the timeline layers:
    1. Extract audio (or reuse existing) via FFmpeg
    2. Create audio asset in DB
    3. Add audio clip to narration track, linked via group_id

    Modifies timeline_data in-place.
    """
    # Find narration track
    narration_track = next(
        (t for t in timeline_data["audio_tracks"] if t["type"] == "narration"),
        None,
    )
    if narration_track is None:
        return

    # Skip audio extraction if the plan already placed narration clips
    existing_narration = narration_track.get("clips", [])
    if existing_narration:
        logger.info(
            "[ENRICH_AUDIO] Narration track already has %d clips from plan, "
            "skipping auto-extraction. Will still apply chroma key to avatar clips.",
            len(existing_narration),
        )
        # Still apply chroma key to avatar clips even if we skip audio extraction
        await _apply_chroma_key_to_avatars(timeline_data, project_id, db)
        return

    # Collect unique asset_ids from all video layer clips
    clip_asset_ids: set[str] = set()
    for layer in timeline_data["layers"]:
        for clip in layer["clips"]:
            aid = clip.get("asset_id")
            if aid:
                clip_asset_ids.add(aid)

    if not clip_asset_ids:
        return

    # Query assets to find avatar videos
    result = await db.execute(
        select(Asset).where(
            Asset.id.in_([UUID(aid) for aid in clip_asset_ids]),
            Asset.type == "video",
            Asset.subtype == "avatar",
        )
    )
    avatar_assets = {str(a.id): a for a in result.scalars().all()}
    if not avatar_assets:
        return

    storage = get_storage_service()
    audio_asset_map: dict[str, Asset] = {}  # video_asset_id -> audio_asset

    for video_asset_id, video_asset in avatar_assets.items():
        # Naming convention: {video_name}.mp3
        video_name = video_asset.name
        audio_name = (
            video_name.rsplit(".", 1)[0] + ".mp3"
            if "." in video_name
            else video_name + ".mp3"
        )

        # Check if audio already extracted
        existing = await db.execute(
            select(Asset).where(
                Asset.project_id == project_id,
                Asset.name == audio_name,
                Asset.type == "audio",
            ).limit(1)
        )
        audio_asset = existing.scalar_one_or_none()

        if not audio_asset:
            try:
                audio_key, file_size = await extract_audio_from_gcs(
                    storage_service=storage,
                    source_key=video_asset.storage_key,
                    project_id=str(project_id),
                    output_filename=audio_name,
                )
                audio_url = storage.get_public_url(audio_key)
                audio_asset = Asset(
                    project_id=project_id,
                    name=audio_name,
                    type="audio",
                    subtype="narration",
                    storage_key=audio_key,
                    storage_url=audio_url,
                    file_size=file_size,
                    mime_type="audio/mpeg",
                    duration_ms=video_asset.duration_ms,
                    sample_rate=44100,
                    channels=2,
                )
                db.add(audio_asset)
                await db.flush()
                await db.refresh(audio_asset)
            except Exception:
                logger.warning(
                    "Failed to extract audio from %s", video_asset.name,
                    exc_info=True,
                )
                continue

        audio_asset_map[video_asset_id] = audio_asset

    # Enrich each avatar video clip: audio extraction + chroma key auto-apply
    for layer in timeline_data["layers"]:
        for clip in layer["clips"]:
            aid = clip.get("asset_id")
            if not aid or aid not in avatar_assets:
                continue

            video_asset = avatar_assets[aid]

            # Always apply sampled chroma key color (overrides plan defaults)
            if video_asset.chroma_key_color:
                clip.setdefault("effects", {})["chroma_key"] = {
                    "enabled": True,
                    "color": video_asset.chroma_key_color,
                    "similarity": 0.4,
                    "blend": 0.1,
                }

            # Add linked audio clip to narration track
            if aid in audio_asset_map:
                audio_asset = audio_asset_map[aid]
                group_id = str(uuid_mod.uuid4())
                clip["group_id"] = group_id

                narration_track["clips"].append({
                    "id": str(uuid_mod.uuid4()),
                    "asset_id": str(audio_asset.id),
                    "start_ms": clip["start_ms"],
                    "duration_ms": clip["duration_ms"],
                    "in_point_ms": 0,
                    "out_point_ms": audio_asset.duration_ms,
                    "volume": 1.0,
                    "fade_in_ms": 0,
                    "fade_out_ms": 0,
                    "effects": {
                        "opacity": 1.0,
                        "blend_mode": "normal",
                        "chroma_key": None,
                    },
                    "group_id": group_id,
                })


async def _smart_sync_operation_screen(
    timeline_data: dict,
    project_id: UUID,
    db,
) -> None:
    """Smart-cut operation screen: cut idle segments, fit to narration duration.

    Preconditions (checked inside, returns silently if not met):
    - _enrich_timeline_audio() already called (narration track has audio)
    - Content layer has operation screen clip(s)

    Processing:
    1. Download operation video from storage
    2. Analyze video activity (frame differencing) to find idle segments
    3. Cut idle segments and mildly speed-up remaining active parts
    4. Split content-layer operation screen clip into sub-clips
    5. Fallback to STT-based smart sync if activity analysis fails
    """
    # Find narration track with clips
    narration_track = next(
        (t for t in timeline_data["audio_tracks"]
         if t["type"] == "narration" and t.get("clips")),
        None,
    )
    if not narration_track or not narration_track["clips"]:
        logger.info("[SMART_CUT] No narration clips found, skipping")
        return

    # Find content layer with clips
    content_layer = next(
        (l for l in timeline_data["layers"]
         if l["type"] == "content" and l.get("clips")),
        None,
    )
    if not content_layer or not content_layer["clips"]:
        logger.info("[SMART_CUT] No content layer clips found, skipping")
        return

    # Get narration duration from the first narration audio clip
    narration_clip = narration_track["clips"][0]
    narration_asset_id = narration_clip.get("asset_id")
    if not narration_asset_id:
        return

    result = await db.execute(
        select(Asset).where(Asset.id == UUID(narration_asset_id))
    )
    narration_asset = result.scalar_one_or_none()
    if not narration_asset:
        logger.warning("[SMART_CUT] Narration asset not found: %s", narration_asset_id)
        return

    narration_duration_ms = narration_asset.duration_ms or (narration_clip.get("duration_ms") or 0)
    if narration_duration_ms <= 0:
        return

    # Process each content clip
    storage = get_storage_service()
    new_content_clips: list[dict] = []

    for content_clip in list(content_layer["clips"]):
        clip_asset_id = content_clip.get("asset_id")
        if not clip_asset_id:
            new_content_clips.append(content_clip)
            continue

        # Look up source asset to get its full duration and storage key
        result = await db.execute(
            select(Asset).where(Asset.id == UUID(clip_asset_id))
        )
        content_asset = result.scalar_one_or_none()
        if not content_asset or not content_asset.duration_ms or not content_asset.storage_key:
            new_content_clips.append(content_clip)
            continue

        operation_duration_ms = content_asset.duration_ms

        # Skip if operation is shorter than narration — no adjustment needed
        if operation_duration_ms <= narration_duration_ms:
            logger.info(
                "[SMART_CUT] Operation (%dms) <= narration (%dms), skipping clip",
                operation_duration_ms,
                narration_duration_ms,
            )
            new_content_clips.append(content_clip)
            continue

        # Primary: Smart Cut — download operation video, analyze activity, cut idle
        segments = None
        tmp_video_path = None
        try:
            ext = Path(content_asset.name).suffix or ".mp4"
            tmp_video = tempfile.NamedTemporaryFile(
                suffix=ext, delete=False, prefix="smart_cut_op_"
            )
            tmp_video.close()
            tmp_video_path = tmp_video.name

            await storage.download_file(content_asset.storage_key, tmp_video_path)

            segments = await compute_smart_cut(
                operation_video_path=tmp_video_path,
                operation_duration_ms=operation_duration_ms,
                narration_duration_ms=narration_duration_ms,
            )
            logger.info("[SMART_CUT] Activity-based cut produced %d segments", len(segments))
        except Exception:
            logger.warning(
                "[SMART_CUT] Activity analysis failed, trying STT-based fallback",
                exc_info=True,
            )
        finally:
            if tmp_video_path:
                Path(tmp_video_path).unlink(missing_ok=True)

        # Fallback: STT-based smart sync (if smart cut failed)
        if not segments and narration_asset.storage_key:
            tmp_audio_path = None
            try:
                tmp_audio = tempfile.NamedTemporaryFile(
                    suffix=".mp3", delete=False, prefix="smart_sync_"
                )
                tmp_audio.close()
                tmp_audio_path = tmp_audio.name
                await storage.download_file(narration_asset.storage_key, tmp_audio_path)

                segments = await compute_smart_sync(
                    narration_audio_path=tmp_audio_path,
                    operation_duration_ms=operation_duration_ms,
                    narration_duration_ms=narration_duration_ms,
                )
                logger.info("[SMART_CUT] STT fallback produced %d segments", len(segments))
            except Exception:
                logger.warning(
                    "[SMART_CUT] STT fallback also failed, keeping original clip",
                    exc_info=True,
                )
            finally:
                if tmp_audio_path:
                    Path(tmp_audio_path).unlink(missing_ok=True)

        if not segments:
            new_content_clips.append(content_clip)
            continue

        # Split original content clip into sub-clips
        original_start_ms = content_clip.get("start_ms") or 0
        original_transform = content_clip.get("transform", {})
        original_effects = content_clip.get("effects", {})
        original_transitions = {
            "transition_in": content_clip.get("transition_in", {"type": "none", "duration_ms": 0}),
            "transition_out": content_clip.get("transition_out", {"type": "none", "duration_ms": 0}),
        }

        for i, seg in enumerate(segments):
            sub_clip = {
                "id": str(uuid_mod.uuid4()),
                "asset_id": clip_asset_id,
                "start_ms": original_start_ms + seg.timeline_start_ms,
                "duration_ms": seg.timeline_duration_ms,
                "in_point_ms": seg.source_start_ms,
                "out_point_ms": seg.source_end_ms,
                "speed": seg.speed,
                "transform": dict(original_transform),
                "effects": dict(original_effects),
                # Only apply transitions at the edges of the full clip
                "transition_in": original_transitions["transition_in"] if i == 0 else {"type": "none", "duration_ms": 0},
                "transition_out": original_transitions["transition_out"] if i == len(segments) - 1 else {"type": "none", "duration_ms": 0},
            }
            new_content_clips.append(sub_clip)

        logger.info(
            "[SMART_CUT] Split content clip into %d sub-clips (op=%dms → nar=%dms)",
            len(segments),
            operation_duration_ms,
            narration_duration_ms,
        )

    # Replace content layer clips
    content_layer["clips"] = new_content_clips


async def _add_click_highlights(
    timeline_data: dict,
    project_id: UUID,
    db,
) -> None:
    """Detect clicks in the operation screen video and add highlight overlays.

    For each content layer clip, detects localized visual changes (clicks)
    and adds highlight metadata that the render pipeline draws as boxes.

    Modifies timeline_data in-place.
    """
    content_layer = next(
        (l for l in timeline_data["layers"]
         if l["type"] == "content" and l.get("clips")),
        None,
    )
    if not content_layer or not content_layer["clips"]:
        return

    storage = get_storage_service()

    # Collect unique content asset IDs
    asset_ids = set()
    for clip in content_layer["clips"]:
        aid = clip.get("asset_id")
        if aid:
            asset_ids.add(aid)

    if not asset_ids:
        return

    # For each unique asset, detect clicks once
    for asset_id_str in asset_ids:
        result = await db.execute(
            select(Asset).where(Asset.id == UUID(asset_id_str))
        )
        asset = result.scalar_one_or_none()
        if not asset or not asset.storage_key:
            continue

        # Effective duration: prefer asset metadata, fallback to first matching clip
        effective_dur = asset.duration_ms
        if not effective_dur:
            for c in content_layer["clips"]:
                if c.get("asset_id") == asset_id_str:
                    effective_dur = c.get("duration_ms", 0)
                    break
        if not effective_dur:
            continue

        # Download video to temp
        tmp_video_path = None
        try:
            ext = Path(asset.name).suffix or ".mp4"
            tmp_video = tempfile.NamedTemporaryFile(
                suffix=ext, delete=False, prefix="click_detect_"
            )
            tmp_video.close()
            tmp_video_path = tmp_video.name

            await storage.download_file(asset.storage_key, tmp_video_path)

            click_events = await detect_clicks(
                video_path=tmp_video_path,
                total_duration_ms=effective_dur,
            )
        except Exception:
            logger.warning(
                "[CLICK_HIGHLIGHT] Click detection failed for %s",
                asset.name,
                exc_info=True,
            )
            continue
        finally:
            if tmp_video_path:
                Path(tmp_video_path).unlink(missing_ok=True)

        if not click_events:
            logger.info("[CLICK_HIGHLIGHT] No clicks detected in %s", asset.name)
            continue

        logger.info(
            "[CLICK_HIGHLIGHT] Detected %d clicks in %s",
            len(click_events),
            asset.name,
        )

        # Map each click to the sub-clip that contains its timestamp
        for clip in content_layer["clips"]:
            if clip.get("asset_id") != asset_id_str:
                continue

            in_point_ms = clip.get("in_point_ms") or 0
            out_point_ms = clip.get("out_point_ms") or (in_point_ms + (clip.get("duration_ms") or 0))
            clip_speed = clip.get("speed") or 1.0

            highlights = []
            for event in click_events:
                if in_point_ms <= event.source_ms < out_point_ms:
                    # Local time within clip (after trim + speed adjustment)
                    local_time_ms = int(
                        (event.source_ms - in_point_ms) / clip_speed
                    )
                    # Normalized coordinates
                    x_norm = event.x / event.frame_width if event.frame_width else 0.5
                    y_norm = event.y / event.frame_height if event.frame_height else 0.5
                    w_norm = event.width / event.frame_width if event.frame_width else 0.1
                    h_norm = event.height / event.frame_height if event.frame_height else 0.08

                    highlights.append({
                        "time_ms": local_time_ms,
                        "duration_ms": 1500,  # Show highlight for 1.5s
                        "x_norm": round(x_norm, 4),
                        "y_norm": round(y_norm, 4),
                        "w_norm": round(max(w_norm, 0.05), 4),  # Min 5% width
                        "h_norm": round(max(h_norm, 0.04), 4),  # Min 4% height
                        "color": "FF6600",
                        "thickness": 4,
                    })

            if highlights:
                clip["highlights"] = highlights
                logger.info(
                    "[CLICK_HIGHLIGHT] Added %d highlights to clip at %dms",
                    len(highlights),
                    clip.get("start_ms") or 0,
                )


async def _add_avatar_dodge_keyframes(
    timeline_data: dict,
) -> None:
    """Add keyframes to move avatar when click highlights overlap it.

    For each click highlight in the content layer, checks if it overlaps
    the avatar's position. If so, adds keyframes to the avatar clip:
    1. Move avatar out of the way just before the highlight appears
    2. Return avatar to original position after the highlight disappears

    Modifies timeline_data in-place.
    """
    content_layer = next(
        (l for l in timeline_data["layers"]
         if l["type"] == "content" and l.get("clips")),
        None,
    )
    avatar_layer = next(
        (l for l in timeline_data["layers"]
         if l["type"] == "avatar" and l.get("clips")),
        None,
    )

    if not content_layer or not avatar_layer:
        return

    avatar_clips = avatar_layer.get("clips", [])
    if not avatar_clips:
        return

    # The avatar is typically positioned at bottom-right
    # Collect all highlights with their absolute timeline positions
    highlights_on_timeline: list[dict] = []

    for clip in content_layer["clips"]:
        highlights = clip.get("highlights", [])
        if not highlights:
            continue

        clip_start_ms = clip.get("start_ms") or 0
        for hl in highlights:
            abs_time_ms = clip_start_ms + (hl.get("time_ms") or 0)
            hl_duration_ms = hl.get("duration_ms") or 1500
            highlights_on_timeline.append({
                "start_ms": abs_time_ms,
                "end_ms": abs_time_ms + hl_duration_ms,
                "x_norm": hl.get("x_norm", 0.5),
                "y_norm": hl.get("y_norm", 0.5),
                "w_norm": hl.get("w_norm", 0.1),
                "h_norm": hl.get("h_norm", 0.08),
            })

    if not highlights_on_timeline:
        return

    # Check each avatar clip for overlaps
    for avatar_clip in avatar_clips:
        avatar_start = avatar_clip.get("start_ms") or 0
        avatar_end = avatar_start + (avatar_clip.get("duration_ms") or 0)
        avatar_transform = avatar_clip.get("transform") or {}

        # Avatar position (center-relative, in pixels from center of canvas)
        avatar_x = avatar_transform.get("x", 0)
        avatar_y = avatar_transform.get("y", 0)
        avatar_scale = avatar_transform.get("scale", 1.0)

        # Estimate avatar size in normalized coords (at 1920x1080, avatar ~500px wide)
        # The avatar is overlaid with center at (canvas_center + x, canvas_center + y)
        # Convert to normalized: avatar_center_norm = (960 + x) / 1920
        canvas_w = 1920
        canvas_h = 1080
        avatar_center_x_norm = (canvas_w / 2 + avatar_x) / canvas_w
        avatar_center_y_norm = (canvas_h / 2 + avatar_y) / canvas_h
        # Rough avatar extent (normalized)
        avatar_w_norm = 0.3 * avatar_scale  # ~500px / 1920
        avatar_h_norm = 0.6 * avatar_scale  # ~650px / 1080

        avatar_left = avatar_center_x_norm - avatar_w_norm / 2
        avatar_right = avatar_center_x_norm + avatar_w_norm / 2
        avatar_top = avatar_center_y_norm - avatar_h_norm / 2
        avatar_bottom = avatar_center_y_norm + avatar_h_norm / 2

        keyframes: list[dict] = []
        original_x = avatar_x
        original_y = avatar_y
        avatar_rotation = avatar_transform.get("rotation", 0)

        def _make_kf(time_ms: int, x: float) -> dict:
            """Build a keyframe in the frontend-expected grouped format."""
            return {
                "time_ms": time_ms,
                "transform": {
                    "x": x,
                    "y": original_y,
                    "scale": avatar_scale,
                    "rotation": avatar_rotation,
                },
            }

        for hl in highlights_on_timeline:
            # Check time overlap
            if hl["end_ms"] <= avatar_start or hl["start_ms"] >= avatar_end:
                continue

            # Check spatial overlap (highlight bbox vs avatar bbox)
            hl_left = hl["x_norm"] - hl["w_norm"] / 2
            hl_right = hl["x_norm"] + hl["w_norm"] / 2
            hl_top = hl["y_norm"] - hl["h_norm"] / 2
            hl_bottom = hl["y_norm"] + hl["h_norm"] / 2

            overlaps = (
                hl_left < avatar_right
                and hl_right > avatar_left
                and hl_top < avatar_bottom
                and hl_bottom > avatar_top
            )

            if not overlaps:
                continue

            # Determine dodge direction: move avatar to the opposite side
            # If avatar is on the right, move left; if on left, move right
            if avatar_center_x_norm > 0.5:
                dodge_x = -abs(original_x) - 200  # Move to left side
            else:
                dodge_x = abs(original_x) + 200  # Move to right side

            # Keyframe timing (relative to avatar clip start)
            hl_rel_start = max(0, hl["start_ms"] - avatar_start)
            hl_rel_end = min(
                avatar_clip.get("duration_ms") or 0,
                hl["end_ms"] - avatar_start,
            )

            # Add keyframes:
            # 1. Start dodge 300ms before highlight
            dodge_start = max(0, hl_rel_start - 300)
            # 2. Hold dodge position during highlight
            # 3. Return 300ms after highlight
            dodge_end = min(
                avatar_clip.get("duration_ms") or 0,
                hl_rel_end + 300,
            )

            # Keyframe: move to dodge position
            keyframes.append(_make_kf(dodge_start, dodge_x))
            # Keyframe: return to original
            keyframes.append(_make_kf(dodge_end, original_x))

        if keyframes:
            # Sort keyframes by time and deduplicate
            keyframes.sort(key=lambda k: k["time_ms"])
            # Add initial position keyframe at 0ms if not present
            if keyframes[0]["time_ms"] > 0:
                keyframes.insert(0, _make_kf(0, original_x))

            avatar_clip.setdefault("keyframes", []).extend(keyframes)
            logger.info(
                "[AVATAR_DODGE] Added %d dodge keyframes to avatar clip at %dms",
                len(keyframes),
                avatar_start,
            )


@router.post(
    "/projects/{project_id}/plan/apply",
    response_model=PlanApplyResponse,
)
async def apply_plan(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> PlanApplyResponse:
    """Apply the current video plan to generate timeline_data.

    This is a deterministic conversion (no AI) plus audio enrichment and
    smart-sync speed adjustment for operation screen clips.
    Overwrites the existing timeline_data.
    """
    project = await _get_project(project_id, current_user.id, db)

    if project.video_plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No video plan to apply",
        )

    plan = VideoPlan.model_validate(project.video_plan)

    # Mark plan as applied
    plan_data = project.video_plan
    plan_data["status"] = "applied"
    project.video_plan = plan_data
    flag_modified(project, "video_plan")

    # Convert plan to timeline
    timeline_data = plan_to_timeline(plan)

    # Auto-extract audio from avatar videos and add to narration track
    await _enrich_timeline_audio(timeline_data, project_id, db)

    # NOTE: Smart sync, click highlights, avatar dodge are now separate skills.
    # Run them sequentially via /skills/{name} endpoints after apply_plan.

    # Update project
    project.timeline_data = timeline_data
    project.duration_ms = timeline_data["duration_ms"]
    flag_modified(project, "timeline_data")

    await db.flush()

    # Count results
    layers_populated = sum(
        1 for layer in timeline_data["layers"] if layer["clips"]
    )
    audio_clips_added = sum(
        len(track["clips"]) for track in timeline_data["audio_tracks"]
    )

    return PlanApplyResponse(
        project_id=project_id,
        duration_ms=timeline_data["duration_ms"],
        layers_populated=layers_populated,
        audio_clips_added=audio_clips_added,
    )


# =============================================================================
# Skill Helpers
# =============================================================================


def _recalculate_duration(timeline_data: dict) -> None:
    """Recalculate duration_ms from all clips."""
    max_time = 0
    for layer in timeline_data.get("layers", []):
        for clip in layer.get("clips", []):
            end = clip["start_ms"] + clip["duration_ms"]
            max_time = max(max_time, end)
    for track in timeline_data.get("audio_tracks", []):
        for clip in track.get("clips", []):
            end = clip["start_ms"] + clip["duration_ms"]
            max_time = max(max_time, end)
    timeline_data["duration_ms"] = max_time


def _find_layer(timeline_data: dict, layer_type: str) -> dict | None:
    return next((l for l in timeline_data["layers"] if l["type"] == layer_type), None)


def _find_track(timeline_data: dict, track_type: str) -> dict | None:
    return next((t for t in timeline_data["audio_tracks"] if t["type"] == track_type), None)


# =============================================================================
# Skill 1: trim-silence
# =============================================================================


@router.post(
    "/projects/{project_id}/skills/trim-silence",
    response_model=SkillResponse,
)
async def skill_trim_silence(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> SkillResponse:
    """Trim leading/trailing silence from narration clips.

    Downloads narration audio, runs FFmpeg silencedetect to find silence
    regions, then trims in_point_ms/out_point_ms. Also trims linked avatar
    clips via group_id.
    """
    t0 = time.monotonic()
    project = await _get_project(project_id, current_user.id, db)

    if not project.timeline_data:
        raise HTTPException(status_code=404, detail="No timeline data. Run apply_plan first.")

    timeline_data = dict(project.timeline_data)
    narration_track = _find_track(timeline_data, "narration")
    if not narration_track or not narration_track.get("clips"):
        return SkillResponse(
            project_id=project_id, skill="trim-silence", success=True,
            message="No narration clips found, skipping.",
            changes={"trimmed": 0}, duration_ms=0,
        )

    from src.services.transcription_service import TranscriptionService

    storage = get_storage_service()
    svc = TranscriptionService(min_silence_duration_ms=200)
    trimmed_count = 0
    changes: dict[str, list] = {"trimmed_clips": []}

    for narr_clip in narration_track["clips"]:
        asset_id = narr_clip.get("asset_id")
        if not asset_id:
            continue

        result = await db.execute(select(Asset).where(Asset.id == UUID(asset_id)))
        asset = result.scalar_one_or_none()
        if not asset or not asset.storage_key:
            continue

        # Effective duration: use asset.duration_ms if available, else clip's duration
        clip_dur_ms = narr_clip.get("duration_ms") or 0
        effective_dur = asset.duration_ms or ((narr_clip.get("in_point_ms") or 0) + clip_dur_ms) or clip_dur_ms
        if not effective_dur:
            continue

        # Download audio to temp file
        tmp_path = None
        try:
            ext = Path(asset.name).suffix or ".mp3"
            tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False, prefix="trim_")
            tmp.close()
            tmp_path = tmp.name
            await storage.download_file(asset.storage_key, tmp_path)

            # Probe real file duration when asset.duration_ms is missing
            real_dur = effective_dur
            if not asset.duration_ms:
                try:
                    from src.utils.media_info import get_media_info
                    info = await asyncio.to_thread(get_media_info, tmp_path)
                    probed = info.get("duration_ms")
                    if probed and probed > 0:
                        real_dur = probed
                        logger.info("[TRIM_SILENCE] Probed real duration for %s: %dms (effective_dur was %dms)",
                                    asset.name, real_dur, effective_dur)
                except Exception:
                    logger.warning("[TRIM_SILENCE] FFprobe failed for %s, using effective_dur", asset.name)

            silences = svc.detect_silences_ffmpeg(tmp_path)
        except Exception:
            logger.warning("[TRIM_SILENCE] Silence detection failed for %s", asset.name, exc_info=True)
            continue
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

        if not silences:
            continue

        # Use real file duration for silence boundary calculations
        # (FFmpeg returns boundaries relative to actual file, not clip)
        leading_trim = silences[0].end_ms if silences[0].start_ms == 0 else 0
        trailing_trim = (real_dur - silences[-1].start_ms) if silences[-1].end_ms >= real_dur - 50 else 0

        if leading_trim < 100 and trailing_trim < 100:
            continue

        # Cap leading_trim to effective_dur to prevent broken clips
        if leading_trim >= effective_dur:
            logger.warning("[TRIM_SILENCE] Leading silence %dms >= effective_dur %dms for %s, skipping",
                           leading_trim, effective_dur, asset.name)
            continue

        # Apply trim to narration clip — respect the clip's planned duration
        new_in = leading_trim if leading_trim >= 100 else 0

        # Determine the clip's original end point (plan may set a shorter range)
        original_out = narr_clip.get("out_point_ms")
        if original_out is None:
            original_out = (narr_clip.get("in_point_ms") or 0) + (narr_clip.get("duration_ms") or effective_dur)
        original_out = min(original_out, effective_dur)

        # Only apply trailing trim if clip originally extended to near real asset end
        if trailing_trim >= 100 and original_out >= real_dur - 50:
            new_out = max(real_dur - trailing_trim, new_in)
        else:
            new_out = original_out

        new_dur = max(new_out - new_in, 0)

        narr_clip["in_point_ms"] = new_in
        narr_clip["out_point_ms"] = new_out
        narr_clip["duration_ms"] = new_dur
        trimmed_count += 1
        changes["trimmed_clips"].append({
            "clip_id": narr_clip["id"],
            "leading_trim_ms": new_in,
            "trailing_trim_ms": trailing_trim if trailing_trim >= 100 else 0,
        })

        # Also trim linked avatar clip via group_id
        group_id = narr_clip.get("group_id")
        if group_id:
            for layer in timeline_data.get("layers", []):
                for clip in layer.get("clips", []):
                    if clip.get("group_id") == group_id:
                        clip["in_point_ms"] = new_in
                        clip["out_point_ms"] = new_out
                        clip["duration_ms"] = new_dur

    _recalculate_duration(timeline_data)
    project.timeline_data = timeline_data
    project.duration_ms = timeline_data["duration_ms"]
    flag_modified(project, "timeline_data")
    await db.flush()

    elapsed = int((time.monotonic() - t0) * 1000)
    return SkillResponse(
        project_id=project_id, skill="trim-silence", success=True,
        message=f"Trimmed {trimmed_count} narration clip(s).",
        changes=changes, duration_ms=elapsed,
    )


# =============================================================================
# Skill 1.5: add-telop
# =============================================================================


@router.post(
    "/projects/{project_id}/skills/add-telop",
    response_model=SkillResponse,
)
async def skill_add_telop(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> SkillResponse:
    """Transcribe narration audio and place text clips on the text layer.

    Downloads narration audio, runs STT (Whisper), and creates a text clip
    for each speech segment. The transcription is also stored in
    timeline_data["metadata"]["transcription"] for other skills to reference.

    Idempotent: removes all group_id="ai-telop" clips before re-creating.
    """
    t0 = time.monotonic()
    project = await _get_project(project_id, current_user.id, db)

    if not project.timeline_data:
        raise HTTPException(status_code=404, detail="No timeline data. Run apply_plan first.")

    timeline_data = dict(project.timeline_data)
    narration_track = _find_track(timeline_data, "narration")
    if not narration_track or not narration_track.get("clips"):
        return SkillResponse(
            project_id=project_id, skill="add-telop", success=True,
            message="No narration clips found, skipping.",
            changes={"telops_added": 0}, duration_ms=0,
        )

    # Idempotent cleanup: remove existing telop text clips
    text_layer = _find_layer(timeline_data, "text")
    if not text_layer:
        elapsed = int((time.monotonic() - t0) * 1000)
        return SkillResponse(
            project_id=project_id, skill="add-telop", success=True,
            message="No text layer found.",
            changes={"telops_added": 0}, duration_ms=elapsed,
        )
    text_layer["clips"] = [
        c for c in text_layer.get("clips", [])
        if c.get("group_id") != "ai-telop"
    ]

    from src.services.transcription_service import TranscriptionService

    storage = get_storage_service()
    svc = TranscriptionService()
    total_telops = 0
    all_segments_data: list[dict] = []

    # Track covered timeline ranges to avoid duplicate telop clips when
    # multiple overlapping narration clips reference the same asset.
    _covered_ranges: list[tuple[int, int]] = []  # (start_ms, end_ms)

    def _is_covered(start: int, end: int) -> bool:
        """Check if a timeline range is already substantially covered."""
        for cs, ce in _covered_ranges:
            # Consider covered if >50% overlap with an existing range
            overlap = max(0, min(end, ce) - max(start, cs))
            if overlap > (end - start) * 0.5:
                return True
        return False

    for narr_clip in narration_track["clips"]:
        asset_id = narr_clip.get("asset_id")
        if not asset_id:
            continue

        result = await db.execute(select(Asset).where(Asset.id == UUID(asset_id)))
        asset = result.scalar_one_or_none()
        if not asset or not asset.storage_key:
            continue

        # Download audio
        tmp_path = None
        try:
            ext = Path(asset.name).suffix or ".mp3"
            tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False, prefix="telop_")
            tmp.close()
            tmp_path = tmp.name
            await storage.download_file(asset.storage_key, tmp_path)

            transcription = svc.transcribe(
                tmp_path, language="ja",
                detect_silences=True, detect_fillers=False, detect_repetitions=False,
            )

            # Detect silence regions for precise telop boundary trimming
            # (Whisper segments are back-to-back; FFmpeg gives actual silence)
            svc_sil = TranscriptionService(min_silence_duration_ms=200)
            silence_regions = svc_sil.detect_silences_ffmpeg(tmp_path)
        except Exception:
            logger.warning("[ADD_TELOP] Transcription failed for %s", asset.name, exc_info=True)
            continue
        finally:
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

        if not transcription.segments:
            continue

        # Clip trim boundaries — use clip's timeline duration as fallback
        # when asset.duration_ms is None (pre-P0 assets without probed metadata)
        clip_start_ms = narr_clip.get("start_ms") or 0
        in_point_ms = narr_clip.get("in_point_ms") or 0
        clip_dur_ms = narr_clip.get("duration_ms") or 0
        effective_asset_dur = asset.duration_ms or (in_point_ms + clip_dur_ms) or clip_dur_ms
        out_point_ms = narr_clip.get("out_point_ms") or effective_asset_dur

        # Create text clips from speech segments
        for seg in transcription.segments:
            if seg.cut:
                continue  # Skip silence/fillers

            # Only include segments within the trimmed range
            if seg.end_ms <= in_point_ms or seg.start_ms >= out_point_ms:
                continue

            # Clamp to trim boundaries
            seg_start = max(seg.start_ms, in_point_ms)
            seg_end = min(seg.end_ms, out_point_ms)

            # Trim boundaries using FFmpeg silence data:
            # shrink start forward past any overlapping silence
            for sil in silence_regions:
                if sil.start_ms <= seg_start < sil.end_ms:
                    seg_start = sil.end_ms
            # shrink end backward before any overlapping silence
            for sil in silence_regions:
                if sil.start_ms < seg_end <= sil.end_ms:
                    seg_end = sil.start_ms

            # Convert source time → timeline time
            timeline_start = clip_start_ms + (seg_start - in_point_ms)
            timeline_dur = seg_end - seg_start

            if timeline_dur < 100:
                continue  # Skip very short segments

            # De-duplicate: skip if this timeline range is already covered
            # (happens with overlapping narration clips using the same asset)
            if _is_covered(timeline_start, timeline_start + timeline_dur):
                continue

            text_clip = {
                "id": str(uuid_mod.uuid4()),
                "asset_id": None,
                "start_ms": timeline_start,
                "duration_ms": timeline_dur,
                "text_content": seg.text.strip(),
                "text_style": {
                    "fontSize": 36,
                    "fontWeight": "bold",
                    "color": "#FFFFFF",
                    "textAlign": "center",
                    "strokeColor": "#000000",
                    "strokeWidth": 2,
                    "backgroundColor": "#000000",
                    "backgroundOpacity": 0.6,
                },
                "transform": {
                    "x": 0,
                    "y": 400,  # Bottom area (center-relative: 540 - 400 = 140px from bottom)
                    "scale": 1.0,
                    "rotation": 0,
                },
                "effects": {
                    "opacity": 1.0,
                    "blend_mode": "normal",
                    "chroma_key": None,
                },
                "group_id": "ai-telop",
            }
            text_layer["clips"].append(text_clip)
            _covered_ranges.append((timeline_start, timeline_start + timeline_dur))
            total_telops += 1

            # Collect for metadata
            all_segments_data.append({
                "text": seg.text.strip(),
                "start_ms": seg.start_ms,
                "end_ms": seg.end_ms,
                "timeline_start_ms": timeline_start,
                "timeline_duration_ms": timeline_dur,
            })

    # Store transcription metadata for other skills
    transcription_data = {
        "segments": all_segments_data,
        "total_segments": len(all_segments_data),
    }
    timeline_data.setdefault("metadata", {})["transcription"] = transcription_data

    # Persist transcription to narration asset's metadata for API retrieval
    for narr_clip in narration_track["clips"]:
        asset_id = narr_clip.get("asset_id")
        if asset_id and all_segments_data:
            result = await db.execute(select(Asset).where(Asset.id == UUID(asset_id)))
            narr_asset = result.scalar_one_or_none()
            if narr_asset:
                meta = dict(narr_asset.asset_metadata or {})
                meta["transcription"] = transcription_data
                narr_asset.asset_metadata = meta
                flag_modified(narr_asset, "asset_metadata")

    _recalculate_duration(timeline_data)
    project.timeline_data = timeline_data
    project.duration_ms = timeline_data["duration_ms"]
    flag_modified(project, "timeline_data")
    await db.flush()

    elapsed = int((time.monotonic() - t0) * 1000)
    return SkillResponse(
        project_id=project_id, skill="add-telop", success=True,
        message=f"Added {total_telops} telop text clip(s) to text layer.",
        changes={"telops_added": total_telops, "segments": len(all_segments_data)},
        duration_ms=elapsed,
    )


# =============================================================================
# Skill 2: layout
# =============================================================================


_AVATAR_POSITIONS: dict[str, dict[str, dict[str, float]]] = {
    # position → size → {x, y, scale}  (1920x1080 canvas)
    "bottom-right": {
        "pip": {"x": 400, "y": 250, "scale": 0.25},
        "medium": {"x": 300, "y": 180, "scale": 0.4},
        "large": {"x": 200, "y": 100, "scale": 0.6},
        "fullscreen": {"x": 0, "y": 0, "scale": 1.0},
    },
    "bottom-left": {
        "pip": {"x": -400, "y": 250, "scale": 0.25},
        "medium": {"x": -300, "y": 180, "scale": 0.4},
        "large": {"x": -200, "y": 100, "scale": 0.6},
        "fullscreen": {"x": 0, "y": 0, "scale": 1.0},
    },
    "top-right": {
        "pip": {"x": 400, "y": -250, "scale": 0.25},
        "medium": {"x": 300, "y": -180, "scale": 0.4},
        "large": {"x": 200, "y": -100, "scale": 0.6},
        "fullscreen": {"x": 0, "y": 0, "scale": 1.0},
    },
    "top-left": {
        "pip": {"x": -400, "y": -250, "scale": 0.25},
        "medium": {"x": -300, "y": -180, "scale": 0.4},
        "large": {"x": -200, "y": -100, "scale": 0.6},
        "fullscreen": {"x": 0, "y": 0, "scale": 1.0},
    },
    "center-right": {
        "pip": {"x": 400, "y": 0, "scale": 0.25},
        "medium": {"x": 300, "y": 0, "scale": 0.4},
        "large": {"x": 200, "y": 0, "scale": 0.6},
        "fullscreen": {"x": 0, "y": 0, "scale": 1.0},
    },
    "center-left": {
        "pip": {"x": -400, "y": 0, "scale": 0.25},
        "medium": {"x": -300, "y": 0, "scale": 0.4},
        "large": {"x": -200, "y": 0, "scale": 0.6},
        "fullscreen": {"x": 0, "y": 0, "scale": 1.0},
    },
}

_SCREEN_POSITIONS: dict[str, dict[str, float]] = {
    "fullscreen": {"x": 0, "y": 0, "scale": 1.0},
    "left-half": {"x": -480, "y": 0, "scale": 0.5},
    "right-half": {"x": 480, "y": 0, "scale": 0.5},
}


@router.post(
    "/projects/{project_id}/skills/layout",
    response_model=SkillResponse,
)
async def skill_layout(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    layout_config: LayoutRequest | None = None,
) -> SkillResponse:
    """Apply layout transforms to clips based on asset subtype.

    Accepts optional LayoutRequest body to customize positions:
    - avatar_position: bottom-right, bottom-left, top-right, top-left, center-right, center-left
    - avatar_size: pip (small overlay), medium, large, fullscreen
    - screen_position: fullscreen, left-half, right-half

    Defaults to bottom-right/pip avatar + fullscreen screen if no body provided.
    Idempotent: always overwrites transforms.
    """
    t0 = time.monotonic()
    config = layout_config or LayoutRequest()
    project = await _get_project(project_id, current_user.id, db)

    if not project.timeline_data:
        raise HTTPException(status_code=404, detail="No timeline data. Run apply_plan first.")

    timeline_data = dict(project.timeline_data)

    # Resolve avatar transform from lookup table
    # Pydantic validates config fields are valid Literal values, so direct lookup is safe.
    avatar_pos_table = _AVATAR_POSITIONS[config.avatar_position]
    avatar_transform = avatar_pos_table[config.avatar_size]

    # Resolve screen transform from lookup table
    screen_transform = _SCREEN_POSITIONS[config.screen_position]

    # Collect all asset_ids to fetch subtypes and chroma_key_color
    asset_ids: set[str] = set()
    for layer in timeline_data.get("layers", []):
        for clip in layer.get("clips", []):
            aid = clip.get("asset_id")
            if aid:
                asset_ids.add(aid)

    # Fetch asset metadata
    asset_map: dict[str, Asset] = {}
    if asset_ids:
        result = await db.execute(
            select(Asset).where(Asset.id.in_([UUID(a) for a in asset_ids]))
        )
        asset_map = {str(a.id): a for a in result.scalars().all()}

    has_avatar = any(
        a.subtype == "avatar" for a in asset_map.values()
    )

    laid_out = 0
    changes: dict[str, list] = {"layouts": [], "config": []}
    changes["config"].append({
        "avatar_position": config.avatar_position,
        "avatar_size": config.avatar_size,
        "screen_position": config.screen_position,
    })

    for layer in timeline_data.get("layers", []):
        for clip in layer.get("clips", []):
            aid = clip.get("asset_id")
            if not aid or aid not in asset_map:
                continue

            asset = asset_map[aid]

            if asset.subtype == "screen":
                clip["transform"] = {**screen_transform, "rotation": 0}
                laid_out += 1
                changes["layouts"].append({
                    "clip_id": clip["id"],
                    "layout": f"screen_{config.screen_position}",
                })

            elif asset.subtype == "avatar":
                clip["transform"] = {**avatar_transform, "rotation": 0}
                # Apply chroma_key from asset
                if asset.chroma_key_color:
                    clip.setdefault("effects", {})["chroma_key"] = {
                        "enabled": True,
                        "color": asset.chroma_key_color,
                        "similarity": 0.4,
                        "blend": 0.1,
                    }
                laid_out += 1
                changes["layouts"].append({
                    "clip_id": clip["id"],
                    "layout": f"avatar_{config.avatar_position}_{config.avatar_size}",
                })

            elif asset.subtype == "slide":
                clip["transform"] = {"x": 0, "y": 0, "scale": 1.0, "rotation": 0}
                laid_out += 1
                changes["layouts"].append({"clip_id": clip["id"], "layout": "slide_fullscreen"})

    _recalculate_duration(timeline_data)
    project.timeline_data = timeline_data
    project.duration_ms = timeline_data["duration_ms"]
    flag_modified(project, "timeline_data")
    await db.flush()

    elapsed = int((time.monotonic() - t0) * 1000)
    return SkillResponse(
        project_id=project_id, skill="layout", success=True,
        message=f"Applied layout to {laid_out} clip(s). avatar={config.avatar_position}/{config.avatar_size}, screen={config.screen_position}",
        changes=changes, duration_ms=elapsed,
    )


# =============================================================================
# Skill 3: sync-content
# =============================================================================


@router.post(
    "/projects/{project_id}/skills/sync-content",
    response_model=SkillResponse,
)
async def skill_sync_content(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> SkillResponse:
    """Sync operation screen clips to narration timing with variable speed.

    Covers the full narration duration — speech segments play at moderate
    speed, gap (silence) segments play at accelerated speed (fast-forward).

    Algorithm:
      base_speed = source_duration / (speech_total + GAP_MULTIPLIER * gap_total)
      gap_speed  = GAP_MULTIPLIER * base_speed
    Both clamped to [0.5, 3.0].

    Idempotent: saves original clips in metadata and restores before
    re-processing.
    """
    import copy

    GAP_SPEED_MULTIPLIER = 2.5  # gaps play 2.5x faster than speech

    t0 = time.monotonic()
    project = await _get_project(project_id, current_user.id, db)

    if not project.timeline_data:
        raise HTTPException(status_code=404, detail="No timeline data. Run apply_plan first.")

    timeline_data = dict(project.timeline_data)
    metadata = timeline_data.setdefault("metadata", {})

    # --- Gather telop clips (group_id="ai-telop") from text layer ---
    text_layer = _find_layer(timeline_data, "text")
    telop_clips = []
    if text_layer:
        telop_clips = sorted(
            [c for c in text_layer.get("clips", []) if c.get("group_id") == "ai-telop"],
            key=lambda c: c["start_ms"],
        )

    if not telop_clips:
        elapsed = int((time.monotonic() - t0) * 1000)
        return SkillResponse(
            project_id=project_id, skill="sync-content", success=True,
            message="No telop clips found. Run add-telop first.",
            changes={"sub_clips": 0}, duration_ms=elapsed,
        )

    # --- Find content layer ---
    content_layer = _find_layer(timeline_data, "content")
    if not content_layer or not content_layer.get("clips"):
        elapsed = int((time.monotonic() - t0) * 1000)
        return SkillResponse(
            project_id=project_id, skill="sync-content", success=True,
            message="No content layer clips found, skipping.",
            changes={"sub_clips": 0}, duration_ms=elapsed,
        )

    # --- Idempotency: restore original clips if previously saved ---
    if "original_content_clips" in metadata:
        content_layer["clips"] = copy.deepcopy(metadata["original_content_clips"])

    # Save original clips for future re-runs
    metadata["original_content_clips"] = copy.deepcopy(content_layer["clips"])

    # --- Determine full narration timeline range ---
    narration_track = _find_track(timeline_data, "narration")
    if not narration_track or not narration_track.get("clips"):
        elapsed = int((time.monotonic() - t0) * 1000)
        return SkillResponse(
            project_id=project_id, skill="sync-content", success=True,
            message="No narration clips found, skipping.",
            changes={"sub_clips": 0}, duration_ms=elapsed,
        )

    narr_clips = narration_track["clips"]
    timeline_start = min(c["start_ms"] for c in narr_clips)
    timeline_end = max(c["start_ms"] + c["duration_ms"] for c in narr_clips)

    # --- Build intervals: speech (telop) + gap ---
    # Step 1: Merge overlapping telop ranges.  When multiple narration clips
    # reference the same asset, add-telop produces overlapping telop clips.
    # We merge them so that each timeline moment belongs to at most one
    # speech interval, preventing overlapping sub-clips downstream.
    raw_speech: list[tuple[int, int]] = []
    for telop in telop_clips:
        t_start = telop["start_ms"]
        t_end = t_start + telop["duration_ms"]
        raw_speech.append((t_start, t_end))
    raw_speech.sort()

    merged_speech: list[tuple[int, int]] = []
    for s, e in raw_speech:
        if merged_speech and s <= merged_speech[-1][1]:
            # Overlapping or adjacent — extend
            merged_speech[-1] = (merged_speech[-1][0], max(merged_speech[-1][1], e))
        else:
            merged_speech.append((s, e))

    # Step 2: Build non-overlapping speech/gap intervals from merged ranges
    intervals: list[tuple[str, int, int]] = []  # (type, start, end)
    pos = timeline_start
    for sp_start, sp_end in merged_speech:
        if sp_start > pos:
            intervals.append(("gap", pos, sp_start))
        intervals.append(("speech", sp_start, sp_end))
        pos = sp_end
    if pos < timeline_end:
        intervals.append(("gap", pos, timeline_end))

    total_speech_dur = sum(e - s for typ, s, e in intervals if typ == "speech")
    total_gap_dur = sum(e - s for typ, s, e in intervals if typ == "gap")

    # --- Process each content clip ---
    new_content_clips: list[dict] = []
    total_sub_clips = 0

    for content_clip in list(content_layer["clips"]):
        clip_asset_id = content_clip.get("asset_id")
        if not clip_asset_id:
            new_content_clips.append(content_clip)
            continue

        # Look up source asset for its full duration
        result = await db.execute(
            select(Asset).where(Asset.id == UUID(clip_asset_id))
        )
        content_asset = result.scalar_one_or_none()
        if not content_asset or not content_asset.duration_ms:
            new_content_clips.append(content_clip)
            continue

        source_duration_ms = content_asset.duration_ms
        original_transform = content_clip.get("transform", {})
        original_effects = content_clip.get("effects", {})

        # Calculate base_speed and gap_speed
        denominator = total_speech_dur + GAP_SPEED_MULTIPLIER * total_gap_dur
        if denominator <= 0:
            new_content_clips.append(content_clip)
            continue

        base_speed = source_duration_ms / denominator
        gap_speed = GAP_SPEED_MULTIPLIER * base_speed

        # Clamp speeds
        base_speed = max(0.5, min(3.0, base_speed))
        gap_speed = max(0.5, min(3.0, gap_speed))

        source_offset_ms = 0
        for typ, iv_start, iv_end in intervals:
            iv_dur = iv_end - iv_start
            if iv_dur <= 0:
                continue

            speed = gap_speed if typ == "gap" else base_speed
            actual_share_ms = int(round(speed * iv_dur))

            sub_clip = {
                "id": str(uuid_mod.uuid4()),
                "asset_id": clip_asset_id,
                "start_ms": iv_start,
                "duration_ms": iv_dur,
                "in_point_ms": source_offset_ms,
                "out_point_ms": source_offset_ms + actual_share_ms,
                "speed": round(speed, 3),
                "transform": dict(original_transform),
                "effects": dict(original_effects),
                "transition_in": {"type": "none", "duration_ms": 0},
                "transition_out": {"type": "none", "duration_ms": 0},
                "group_id": "ai-content-sync",
            }
            new_content_clips.append(sub_clip)
            total_sub_clips += 1
            source_offset_ms += actual_share_ms

        logger.info(
            "[SYNC_CONTENT] %d intervals (speech=%.2fx, gap=%.2fx), "
            "source=%dms, speech=%dms, gap=%dms",
            len(intervals), base_speed, gap_speed,
            source_duration_ms, total_speech_dur, total_gap_dur,
        )

    content_layer["clips"] = new_content_clips

    _recalculate_duration(timeline_data)
    project.timeline_data = timeline_data
    project.duration_ms = timeline_data["duration_ms"]
    flag_modified(project, "timeline_data")
    await db.flush()

    elapsed = int((time.monotonic() - t0) * 1000)
    return SkillResponse(
        project_id=project_id, skill="sync-content", success=True,
        message=(
            f"Created {total_sub_clips} sub-clip(s) "
            f"(speech {base_speed:.2f}x, gap {gap_speed:.2f}x)."
            if total_sub_clips > 0 else "No sub-clips created."
        ),
        changes={
            "sub_clips": total_sub_clips,
            "intervals": len(intervals),
            "speech_speed": round(base_speed, 3) if total_sub_clips > 0 else None,
            "gap_speed": round(gap_speed, 3) if total_sub_clips > 0 else None,
        },
        duration_ms=elapsed,
    )


# =============================================================================
# Skill 4: click-highlight
# =============================================================================


@router.post(
    "/projects/{project_id}/skills/click-highlight",
    response_model=SkillResponse,
)
async def skill_click_highlight(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> SkillResponse:
    """Detect clicks in operation screen and add highlight shapes to effects layer.

    Each click becomes a rectangle shape clip on the effects layer with
    group_id="ai-click-highlight" for idempotent cleanup.

    Idempotent: removes all ai-click-highlight clips before re-detecting.
    """
    t0 = time.monotonic()
    project = await _get_project(project_id, current_user.id, db)

    if not project.timeline_data:
        raise HTTPException(status_code=404, detail="No timeline data. Run apply_plan first.")

    timeline_data = dict(project.timeline_data)

    # Idempotent cleanup: remove existing click highlight shapes
    effects_layer = _find_layer(timeline_data, "effects")
    if effects_layer:
        effects_layer["clips"] = [
            c for c in effects_layer.get("clips", [])
            if c.get("group_id") != "ai-click-highlight"
        ]
    else:
        # No effects layer — skip
        elapsed = int((time.monotonic() - t0) * 1000)
        return SkillResponse(
            project_id=project_id, skill="click-highlight", success=True,
            message="No effects layer found.", changes={"highlights_added": 0},
            duration_ms=elapsed,
        )

    content_layer = _find_layer(timeline_data, "content")
    if not content_layer or not content_layer.get("clips"):
        elapsed = int((time.monotonic() - t0) * 1000)
        return SkillResponse(
            project_id=project_id, skill="click-highlight", success=True,
            message="No content clips found.", changes={"highlights_added": 0},
            duration_ms=elapsed,
        )

    storage = get_storage_service()
    total_highlights = 0

    # Collect unique content asset IDs
    asset_ids = set()
    for clip in content_layer["clips"]:
        aid = clip.get("asset_id")
        if aid:
            asset_ids.add(aid)

    for asset_id_str in asset_ids:
        result = await db.execute(select(Asset).where(Asset.id == UUID(asset_id_str)))
        asset = result.scalar_one_or_none()
        if not asset or not asset.storage_key:
            continue

        effective_dur = asset.duration_ms
        if not effective_dur:
            for c in content_layer["clips"]:
                if c.get("asset_id") == asset_id_str:
                    effective_dur = c.get("duration_ms", 0)
                    break
        if not effective_dur:
            continue

        tmp_video_path = None
        try:
            ext = Path(asset.name).suffix or ".mp4"
            tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False, prefix="click_hl_")
            tmp.close()
            tmp_video_path = tmp.name
            await storage.download_file(asset.storage_key, tmp_video_path)

            click_events = await detect_clicks(
                video_path=tmp_video_path,
                total_duration_ms=effective_dur,
            )
        except Exception:
            logger.warning("[CLICK_HL] Detection failed for %s", asset.name, exc_info=True)
            continue
        finally:
            if tmp_video_path:
                Path(tmp_video_path).unlink(missing_ok=True)

        if not click_events:
            continue

        logger.info("[CLICK_HL] Detected %d clicks in %s", len(click_events), asset.name)

        # Map clicks to sub-clips and compute absolute timeline positions
        for clip in content_layer["clips"]:
            if clip.get("asset_id") != asset_id_str:
                continue

            in_pt = clip.get("in_point_ms") or 0
            out_pt = clip.get("out_point_ms") or (in_pt + (clip.get("duration_ms") or 0))
            clip_speed = clip.get("speed") or 1.0
            clip_start = clip.get("start_ms") or 0

            for event in click_events:
                if not (in_pt <= event.source_ms < out_pt):
                    continue

                # Absolute timeline position
                local_ms = int((event.source_ms - in_pt) / clip_speed)
                abs_ms = clip_start + local_ms

                # Normalized → pixel coordinates (center-relative)
                x_norm = event.x / event.frame_width if event.frame_width else 0.5
                y_norm = event.y / event.frame_height if event.frame_height else 0.5
                w_norm = event.width / event.frame_width if event.frame_width else 0.1
                h_norm = event.height / event.frame_height if event.frame_height else 0.08

                shape_w = max(w_norm * 1920, 96)
                shape_h = max(h_norm * 1080, 43)

                shape_clip = {
                    "id": str(uuid_mod.uuid4()),
                    "asset_id": None,
                    "start_ms": abs_ms,
                    "duration_ms": 1500,
                    "transform": {
                        "x": (x_norm - 0.5) * 1920,
                        "y": (y_norm - 0.5) * 1080,
                        "width": shape_w,
                        "height": shape_h,
                        "scale": 1.0,
                        "rotation": 0,
                    },
                    "shape": {
                        "type": "rectangle",
                        "width": shape_w,
                        "height": shape_h,
                        "fillColor": "transparent",
                        "strokeColor": "#FF6600",
                        "strokeWidth": 4,
                        "filled": False,
                    },
                    "effects": {
                        "opacity": 1.0,
                        "blend_mode": "normal",
                        "chroma_key": None,
                    },
                    "group_id": "ai-click-highlight",
                }
                effects_layer["clips"].append(shape_clip)
                total_highlights += 1

    _recalculate_duration(timeline_data)
    project.timeline_data = timeline_data
    project.duration_ms = timeline_data["duration_ms"]
    flag_modified(project, "timeline_data")
    await db.flush()

    elapsed = int((time.monotonic() - t0) * 1000)
    return SkillResponse(
        project_id=project_id, skill="click-highlight", success=True,
        message=f"Added {total_highlights} click highlight shape(s) to effects layer.",
        changes={"highlights_added": total_highlights},
        duration_ms=elapsed,
    )


# =============================================================================
# Skill 5: avatar-dodge
# =============================================================================


@router.post(
    "/projects/{project_id}/skills/avatar-dodge",
    response_model=SkillResponse,
)
async def skill_avatar_dodge(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> SkillResponse:
    """Add fast dodge keyframes to avatar when click highlights overlap.

    Reads shape clips from effects layer (group_id="ai-click-highlight"),
    checks time+space overlap with avatar clips, and adds 100ms dodge
    keyframes to move avatar out of the way.

    Idempotent: overwrites avatar keyframes each run.
    """
    t0 = time.monotonic()
    project = await _get_project(project_id, current_user.id, db)

    if not project.timeline_data:
        raise HTTPException(status_code=404, detail="No timeline data. Run apply_plan first.")

    timeline_data = dict(project.timeline_data)

    effects_layer = _find_layer(timeline_data, "effects")
    avatar_layer = _find_layer(timeline_data, "avatar")

    if not effects_layer or not avatar_layer:
        elapsed = int((time.monotonic() - t0) * 1000)
        return SkillResponse(
            project_id=project_id, skill="avatar-dodge", success=True,
            message="No effects or avatar layer found.",
            changes={"dodges_added": 0}, duration_ms=elapsed,
        )

    # Collect click-highlight shapes
    highlight_shapes = [
        c for c in effects_layer.get("clips", [])
        if c.get("group_id") == "ai-click-highlight"
    ]
    avatar_clips = avatar_layer.get("clips", [])

    if not highlight_shapes or not avatar_clips:
        elapsed = int((time.monotonic() - t0) * 1000)
        return SkillResponse(
            project_id=project_id, skill="avatar-dodge", success=True,
            message="No highlights or avatar clips to process.",
            changes={"dodges_added": 0}, duration_ms=elapsed,
        )

    canvas_w, canvas_h = 1920, 1080
    dodge_count = 0

    for avatar_clip in avatar_clips:
        avatar_start = avatar_clip.get("start_ms") or 0
        avatar_dur = avatar_clip.get("duration_ms") or 0
        avatar_end = avatar_start + avatar_dur
        avatar_tf = avatar_clip.get("transform") or {}

        original_x = avatar_tf.get("x", 0)
        original_y = avatar_tf.get("y", 0)
        avatar_scale = avatar_tf.get("scale", 1.0)
        avatar_rotation = avatar_tf.get("rotation", 0)

        # Estimate avatar bounding box in normalized coords
        avatar_cx_norm = (canvas_w / 2 + original_x) / canvas_w
        avatar_cy_norm = (canvas_h / 2 + original_y) / canvas_h
        avatar_w_norm = 0.3 * avatar_scale
        avatar_h_norm = 0.6 * avatar_scale

        avatar_left = avatar_cx_norm - avatar_w_norm / 2
        avatar_right = avatar_cx_norm + avatar_w_norm / 2
        avatar_top = avatar_cy_norm - avatar_h_norm / 2
        avatar_bottom = avatar_cy_norm + avatar_h_norm / 2

        def _make_kf(time_ms: int, x: float) -> dict:
            return {
                "time_ms": time_ms,
                "transform": {
                    "x": x,
                    "y": original_y,
                    "scale": avatar_scale,
                    "rotation": avatar_rotation,
                },
            }

        keyframes: list[dict] = []

        for hl in highlight_shapes:
            hl_start = hl.get("start_ms", 0)
            hl_end = hl_start + hl.get("duration_ms", 1500)

            # Time overlap check
            if hl_end <= avatar_start or hl_start >= avatar_end:
                continue

            # Spatial overlap check — convert shape transform to normalized
            hl_tf = hl.get("transform", {})
            hl_cx_norm = (canvas_w / 2 + hl_tf.get("x", 0)) / canvas_w
            hl_cy_norm = (canvas_h / 2 + hl_tf.get("y", 0)) / canvas_h
            hl_w_norm = hl_tf.get("width", 96) / canvas_w
            hl_h_norm = hl_tf.get("height", 43) / canvas_h

            hl_left = hl_cx_norm - hl_w_norm / 2
            hl_right = hl_cx_norm + hl_w_norm / 2
            hl_top = hl_cy_norm - hl_h_norm / 2
            hl_bottom = hl_cy_norm + hl_h_norm / 2

            overlaps = (
                hl_left < avatar_right
                and hl_right > avatar_left
                and hl_top < avatar_bottom
                and hl_bottom > avatar_top
            )
            if not overlaps:
                continue

            # Determine dodge direction
            if avatar_cx_norm > 0.5:
                dodge_x = original_x - 250  # Move left
            else:
                dodge_x = original_x + 250  # Move right

            # Relative to avatar clip start
            rel_start = max(0, hl_start - avatar_start)
            rel_end = min(avatar_dur, hl_end - avatar_start)

            # 100ms transition ("シュッ")
            dodge_start = max(0, rel_start - 100)
            dodge_end = min(avatar_dur, rel_end + 100)

            keyframes.append(_make_kf(dodge_start, dodge_x))
            keyframes.append(_make_kf(dodge_end, original_x))
            dodge_count += 1

        if keyframes:
            keyframes.sort(key=lambda k: k["time_ms"])
            if keyframes[0]["time_ms"] > 0:
                keyframes.insert(0, _make_kf(0, original_x))
            # Idempotent: overwrite
            avatar_clip["keyframes"] = keyframes

    _recalculate_duration(timeline_data)
    project.timeline_data = timeline_data
    project.duration_ms = timeline_data["duration_ms"]
    flag_modified(project, "timeline_data")
    await db.flush()

    elapsed = int((time.monotonic() - t0) * 1000)
    return SkillResponse(
        project_id=project_id, skill="avatar-dodge", success=True,
        message=f"Added {dodge_count} dodge keyframe pair(s) to avatar.",
        changes={"dodges_added": dodge_count},
        duration_ms=elapsed,
    )


# =============================================================================
# Skill: run-all (convenience — runs all skills in correct order)
# =============================================================================


@router.post(
    "/projects/{project_id}/skills/run-all",
    response_model=RunAllResponse,
)
async def skill_run_all(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
    layout_config: LayoutRequest | None = None,
) -> RunAllResponse:
    """Run all 6 skills in the correct dependency order.

    Execution order:
    1. trim-silence (no deps)
    2. add-telop (no deps)
    3. layout (no deps, parallel-safe with 1-2)
    4. sync-content (depends on add-telop)
    5. click-highlight (no deps)
    6. avatar-dodge (depends on click-highlight)

    Accepts optional layout_config body to pass through to the layout skill.
    Stops on first failure and reports which skill failed.
    """
    t0 = time.monotonic()

    skill_funcs = [
        ("trim-silence", skill_trim_silence),
        ("add-telop", skill_add_telop),
        ("layout", skill_layout),
        ("sync-content", skill_sync_content),
        ("click-highlight", skill_click_highlight),
        ("avatar-dodge", skill_avatar_dodge),
    ]

    results: list[RunAllSkillResult] = []
    failed_at: str | None = None

    for skill_name, skill_func in skill_funcs:
        t0_skill = time.monotonic()
        try:
            if skill_name == "layout" and layout_config is not None:
                resp: SkillResponse = await skill_func(
                    project_id=project_id,
                    current_user=current_user,
                    db=db,
                    layout_config=layout_config,
                )
            else:
                resp: SkillResponse = await skill_func(
                    project_id=project_id,
                    current_user=current_user,
                    db=db,
                )
            results.append(RunAllSkillResult(
                skill=skill_name,
                success=resp.success,
                message=resp.message,
                duration_ms=resp.duration_ms,
                changes=resp.changes,
            ))
            if not resp.success:
                failed_at = skill_name
                break
        except Exception as e:
            elapsed = int((time.monotonic() - t0_skill) * 1000)
            detail = e.detail if hasattr(e, "detail") else str(e)
            logger.error("run-all: skill %s failed: %s", skill_name, detail, exc_info=True)
            results.append(RunAllSkillResult(
                skill=skill_name,
                success=False,
                message=f"Skill {skill_name} failed: {detail}",
                duration_ms=elapsed,
            ))
            failed_at = skill_name
            break

    total_elapsed = int((time.monotonic() - t0) * 1000)
    return RunAllResponse(
        project_id=project_id,
        success=failed_at is None,
        total_duration_ms=total_elapsed,
        results=results,
        failed_at=failed_at,
    )


# =============================================================================
# Quality Check
# =============================================================================


@router.post(
    "/projects/{project_id}/check",
    response_model=CheckResponse,
)
async def check_quality(
    project_id: UUID,
    request: CheckRequest,
    current_user: LightweightUser,
    db: DbSession,
) -> CheckResponse:
    """Run quality check on the project timeline.

    Combines structural validation, plan-vs-actual comparison,
    narration-content sync check, and material gap detection.

    Levels:
    - quick: Structure checks only (~1s)
    - standard: Structure + visual sampling (~15s)
    - deep: All checks including semantic and director's eye (~30s)
    """
    project = await _get_project(project_id, current_user.id, db)
    timeline = project.timeline_data
    if not timeline:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No timeline data in project",
        )

    # Gather known asset IDs and name map
    result = await db.execute(
        select(Asset).where(Asset.project_id == project_id)
    )
    assets_db = {str(a.id): a for a in result.scalars().all()}
    asset_ids = set(assets_db.keys())
    asset_name_map = {aid: a.name for aid, a in assets_db.items()}

    # Visual sampling for standard/deep levels
    visual_sampling_skipped = False
    visual_samples: list[dict] = []
    if request.check_level in ("standard", "deep"):
        temp_dir = tempfile.mkdtemp(prefix="douga_check_")
        try:
            # Download assets
            storage = get_storage_service()
            assets_local: dict[str, str] = {}
            assets_dir = os.path.join(temp_dir, "assets")
            os.makedirs(assets_dir, exist_ok=True)

            for aid, asset in assets_db.items():
                ext = asset.storage_key.rsplit(".", 1)[-1] if "." in asset.storage_key else ""
                local_path = os.path.join(assets_dir, f"{aid}.{ext}")
                await storage.download_file(asset.storage_key, local_path)
                assets_local[aid] = local_path

            # Sample frames at even intervals
            duration_ms = timeline.get("duration_ms", 0)
            if duration_ms > 0:
                from src.services.frame_sampler import FrameSampler

                sampler = FrameSampler(
                    timeline_data=timeline,
                    assets=assets_local,
                    project_width=project.width,
                    project_height=project.height,
                    project_fps=project.fps,
                    asset_name_map=asset_name_map,
                )

                n_samples = min(request.max_visual_samples, 20)
                step = duration_ms // (n_samples + 1)
                for i in range(1, n_samples + 1):
                    t = step * i
                    try:
                        frame = await sampler.sample_frame(
                            time_ms=t,
                            resolution=request.resolution,
                        )
                        visual_samples.append(frame)
                    except Exception as e:
                        logger.warning(f"Visual sample at {t}ms failed: {e}")
        except Exception as e:
            logger.warning(f"Visual sampling failed: {e}")
            visual_sampling_skipped = True
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # Run quality checker
    checker = QualityChecker(
        timeline_data=timeline,
        video_plan=project.video_plan,
        asset_ids=asset_ids,
        asset_name_map=asset_name_map,
        project_width=project.width,
        project_height=project.height,
        visual_sample_results=visual_samples,
    )

    response = checker.run(request)
    response.visual_sampling_skipped = visual_sampling_skipped
    return response
