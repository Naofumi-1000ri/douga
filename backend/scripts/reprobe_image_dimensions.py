#!/usr/bin/env python3
"""Re-probe width/height for image assets where both are null (Issue #321).

This script scans all image assets that have null width AND null height,
downloads each file from storage, and updates the DB with probed dimensions.
Supports --dry-run for safe inspection before executing.

Usage:
    # Dry run (prints what would be updated, no DB writes)
    cd backend && uv run python scripts/reprobe_image_dimensions.py --dry-run

    # Limit to a specific project
    cd backend && uv run python scripts/reprobe_image_dimensions.py --dry-run --project-id <uuid>

    # Execute (updates DB)
    cd backend && uv run python scripts/reprobe_image_dimensions.py

Requires DATABASE_URL and storage credentials in the environment,
same as the API server.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import tempfile
from pathlib import Path
from uuid import UUID

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Ensure the backend src package is importable when run from the backend/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def reprobe_image_dimensions(
    dry_run: bool = True,
    project_id: UUID | None = None,
    batch_size: int = 50,
) -> None:
    """Re-probe image assets with null width/height and update the DB.

    Args:
        dry_run: If True, only log what would be done; do not write to DB.
        project_id: Limit to assets belonging to this project UUID.
        batch_size: Number of assets to process in each DB query batch.
    """
    from sqlalchemy import and_, select, update

    from src.models.asset import Asset
    from src.models.database import async_session_maker
    from src.services.storage_service import get_storage_service
    from src.utils.media_info import get_media_info

    storage = get_storage_service()

    # Build base query for image assets with null width/height
    conditions = [
        Asset.type == "image",
        Asset.width == None,  # noqa: E711
        Asset.height == None,  # noqa: E711
    ]
    if project_id is not None:
        conditions.append(Asset.project_id == project_id)

    async with async_session_maker() as db:
        result = await db.execute(
            select(Asset).where(and_(*conditions)).order_by(Asset.created_at.desc())
        )
        assets = result.scalars().all()

    total = len(assets)
    logger.info(
        "Found %d image asset(s) with null width/height%s",
        total,
        f" in project {project_id}" if project_id else "",
    )

    if total == 0:
        logger.info("Nothing to reprobe.")
        return

    updated = 0
    skipped = 0
    failed = 0

    suffix_map = {
        ".png": ".png",
        ".jpg": ".jpg",
        ".jpeg": ".jpeg",
        ".gif": ".gif",
        ".webp": ".webp",
    }

    for i, asset in enumerate(assets, start=1):
        logger.info(
            "[%d/%d] asset_id=%s name=%r storage_key=%r",
            i,
            total,
            asset.id,
            asset.name,
            asset.storage_key,
        )

        if not asset.storage_key:
            logger.warning("  -> Skipped (no storage_key)")
            skipped += 1
            continue

        ext = "." + asset.storage_key.rsplit(".", 1)[-1].lower() if "." in asset.storage_key else ""
        suffix = suffix_map.get(ext, ".png")

        width: int | None = None
        height: int | None = None

        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp_path = tmp.name

            try:
                await storage.download_file(asset.storage_key, tmp_path)

                # Primary: ffprobe
                info = await asyncio.to_thread(get_media_info, tmp_path)
                width = info.get("width")
                height = info.get("height")

                # Fallback: PIL
                if not width or not height:
                    try:
                        from PIL import Image as PILImage

                        img = await asyncio.to_thread(PILImage.open, tmp_path)
                        width, height = img.size
                        img.close()
                        logger.info("  -> PIL fallback: %sx%s", width, height)
                    except Exception as pil_err:
                        logger.debug("  -> PIL also failed: %s", pil_err)
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        except Exception as exc:
            logger.error("  -> Download/probe failed: %s", exc)
            failed += 1
            continue

        if not width or not height:
            logger.warning("  -> No dimensions obtained; skipping update")
            skipped += 1
            continue

        logger.info("  -> Probed dimensions: %sx%s", width, height)

        if dry_run:
            logger.info("  -> [DRY RUN] Would update asset %s: width=%s height=%s", asset.id, width, height)
            updated += 1
            continue

        # Write to DB
        try:
            async with async_session_maker() as db:
                await db.execute(
                    update(Asset)
                    .where(Asset.id == asset.id)
                    .values(width=width, height=height)
                )
                await db.commit()
            logger.info("  -> Updated asset %s: width=%s height=%s", asset.id, width, height)
            updated += 1
        except Exception as exc:
            logger.error("  -> DB update failed for asset %s: %s", asset.id, exc)
            failed += 1

    logger.info(
        "Done. total=%d updated=%d skipped=%d failed=%d%s",
        total,
        updated,
        skipped,
        failed,
        " (DRY RUN — no DB writes)" if dry_run else "",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-probe width/height for image assets where both are null (Issue #321)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print what would be updated without writing to the database.",
    )
    parser.add_argument(
        "--project-id",
        type=str,
        default=None,
        help="Limit reprobe to assets belonging to this project UUID.",
    )
    args = parser.parse_args()

    project_id: UUID | None = None
    if args.project_id:
        try:
            project_id = UUID(args.project_id)
        except ValueError:
            logger.error("Invalid project_id UUID: %r", args.project_id)
            sys.exit(1)

    if args.dry_run:
        logger.info("=== DRY RUN MODE — no database writes will occur ===")

    asyncio.run(
        reprobe_image_dimensions(
            dry_run=args.dry_run,
            project_id=project_id,
        )
    )


if __name__ == "__main__":
    main()
