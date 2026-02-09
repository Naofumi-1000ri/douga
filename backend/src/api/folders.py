"""API endpoints for asset folders."""

import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from src.api.access import get_accessible_project
from src.api.deps import CurrentUser, DbSession
from src.models.asset import Asset
from src.models.asset_folder import AssetFolder
from src.models.project import Project
from src.schemas.asset_folder import (
    AssetFolderCreate,
    AssetFolderResponse,
    AssetFolderUpdate,
)
from src.services.event_manager import event_manager

logger = logging.getLogger(__name__)

router = APIRouter()


async def verify_project_access(
    project_id: UUID,
    user_id: UUID,
    db: DbSession,
) -> Project:
    """Verify user has access to the project.

    Delegates to centralized access control which checks ownership
    and project membership.
    """
    return await get_accessible_project(project_id, user_id, db)


@router.get("/projects/{project_id}/folders", response_model=list[AssetFolderResponse])
async def list_folders(
    project_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> list[AssetFolderResponse]:
    """List all folders for a project."""
    await verify_project_access(project_id, current_user.id, db)

    result = await db.execute(
        select(AssetFolder)
        .where(AssetFolder.project_id == project_id)
        .order_by(AssetFolder.name)
    )
    folders = result.scalars().all()

    return [AssetFolderResponse.model_validate(f) for f in folders]


@router.post(
    "/projects/{project_id}/folders",
    response_model=AssetFolderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_folder(
    project_id: UUID,
    folder_data: AssetFolderCreate,
    current_user: CurrentUser,
    db: DbSession,
) -> AssetFolderResponse:
    """Create a new folder."""
    await verify_project_access(project_id, current_user.id, db)

    # Check for duplicate folder name
    result = await db.execute(
        select(AssetFolder).where(
            AssetFolder.project_id == project_id,
            AssetFolder.name == folder_data.name,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Folder with this name already exists",
        )

    folder = AssetFolder(
        project_id=project_id,
        name=folder_data.name,
    )
    db.add(folder)
    await db.flush()
    await db.refresh(folder)

    # Note: No Firestore event needed for folder operations
    # They don't affect timeline and are managed client-side

    return AssetFolderResponse.model_validate(folder)


@router.patch(
    "/projects/{project_id}/folders/{folder_id}",
    response_model=AssetFolderResponse,
)
async def update_folder(
    project_id: UUID,
    folder_id: UUID,
    folder_data: AssetFolderUpdate,
    current_user: CurrentUser,
    db: DbSession,
) -> AssetFolderResponse:
    """Update a folder's name."""
    await verify_project_access(project_id, current_user.id, db)

    result = await db.execute(
        select(AssetFolder).where(
            AssetFolder.id == folder_id,
            AssetFolder.project_id == project_id,
        )
    )
    folder = result.scalar_one_or_none()

    if folder is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Folder not found",
        )

    # Check for duplicate name (excluding current folder)
    result = await db.execute(
        select(AssetFolder).where(
            AssetFolder.project_id == project_id,
            AssetFolder.name == folder_data.name,
            AssetFolder.id != folder_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Folder with this name already exists",
        )

    folder.name = folder_data.name
    await db.flush()
    await db.refresh(folder)

    return AssetFolderResponse.model_validate(folder)


@router.delete(
    "/projects/{project_id}/folders/{folder_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_folder(
    project_id: UUID,
    folder_id: UUID,
    current_user: CurrentUser,
    db: DbSession,
) -> None:
    """Delete a folder. Assets in the folder are moved to root (folder_id=null)."""
    await verify_project_access(project_id, current_user.id, db)

    result = await db.execute(
        select(AssetFolder).where(
            AssetFolder.id == folder_id,
            AssetFolder.project_id == project_id,
        )
    )
    folder = result.scalar_one_or_none()

    if folder is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Folder not found",
        )

    # Move all assets in this folder to root
    result = await db.execute(
        select(Asset).where(Asset.folder_id == folder_id)
    )
    assets = result.scalars().all()
    for asset in assets:
        asset.folder_id = None

    await db.delete(folder)
