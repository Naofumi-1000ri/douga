#!/usr/bin/env python3
"""One-shot migration script: encrypt all plaintext ``project.ai_api_key`` values.

Usage
-----
Dry-run (no writes):

    DATABASE_URL=postgresql+asyncpg://... AI_KEY_ENCRYPTION_KEY=<key> \\
        uv run python scripts/encrypt_ai_keys.py --dry-run

Live run:

    DATABASE_URL=postgresql+asyncpg://... AI_KEY_ENCRYPTION_KEY=<key> \\
        uv run python scripts/encrypt_ai_keys.py

The script is idempotent: rows that already carry an ``enc:v1:`` value are
skipped.  Run it again at any time without risk of double-encryption.

Prerequisites
-------------
- ``AI_KEY_ENCRYPTION_KEY`` must be set (32-byte base64).
- ``DATABASE_URL`` must point to the target database.
  Cloud SQL Proxy (or a tunnel) is recommended for production.
- The running user must have ``UPDATE`` permission on ``projects``.

Safety
------
- Runs each row update inside a single atomic transaction.  On error the
  entire transaction rolls back.
- Prints a per-row summary so you can verify the result before committing.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


async def main(dry_run: bool) -> int:
    # Validate encryption key before touching the database.
    from src.utils.field_encryption import _load_key, encrypt_field, is_encrypted  # noqa: PLC0415

    key = _load_key()
    if key is None:
        logger.error(
            "AI_KEY_ENCRYPTION_KEY is not set or invalid. "
            "Set it before running the migration."
        )
        return 1

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        logger.error("DATABASE_URL is not set.")
        return 1

    # Replace asyncpg DSN with psycopg2 for sync bulk ops (simpler for scripts).
    # We use SQLAlchemy async for consistency with the rest of the codebase.
    from sqlalchemy import select, update  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: PLC0415

    engine = create_async_engine(database_url, echo=False)

    encrypted_count = 0
    skipped_count = 0
    error_count = 0

    # Manual transaction control (no `async with session.begin()`):
    # SQLAlchemy autobegins a transaction on first execute; we explicitly
    # commit or roll back at the end.  This avoids the ambiguity of calling
    # rollback() inside a `begin()` context manager whose __aexit__ would
    # then attempt a commit on an inactive transaction.
    async with AsyncSession(engine) as session:
        try:
            # Import here to avoid module-level DB initialisation side effects.
            from src.models.project import Project  # noqa: PLC0415

            result = await session.execute(
                select(Project.id, Project.ai_api_key).where(Project.ai_api_key.isnot(None))
            )
            rows = result.all()

            logger.info("Found %d projects with ai_api_key set.", len(rows))

            for project_id, current_value in rows:
                if is_encrypted(current_value):
                    logger.info("  SKIP  %s — already encrypted", project_id)
                    skipped_count += 1
                    continue

                new_value = encrypt_field(current_value)
                if new_value == current_value:
                    # encrypt_field returns plaintext unchanged if key is unavailable —
                    # should not happen here but guard anyway.
                    logger.warning(
                        "  WARN  %s — encrypt_field returned unchanged value (key issue?)",
                        project_id,
                    )
                    error_count += 1
                    continue

                logger.info("  ENCRYPT  %s", project_id)
                encrypted_count += 1

                if not dry_run:
                    await session.execute(
                        update(Project)
                        .where(Project.id == project_id)
                        .values(ai_api_key=new_value)
                    )

            if dry_run:
                logger.info(
                    "\nDRY-RUN summary: would encrypt=%d, skip=%d, errors=%d",
                    encrypted_count,
                    skipped_count,
                    error_count,
                )
                # Nothing was written; roll back explicitly for clarity.
                await session.rollback()
            else:
                await session.commit()
                logger.info(
                    "\nMigration complete: encrypted=%d, skipped=%d, errors=%d",
                    encrypted_count,
                    skipped_count,
                    error_count,
                )
        except Exception:
            await session.rollback()
            raise

    await engine.dispose()
    return 0 if error_count == 0 else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Encrypt all plaintext ai_api_key values.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without writing to the database.",
    )
    args = parser.parse_args()

    exit_code = asyncio.run(main(dry_run=args.dry_run))
    sys.exit(exit_code)
