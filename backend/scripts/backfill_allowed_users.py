#!/usr/bin/env python3
"""Backfill allowed_users on Firestore project_updates documents (#286 C-2).

Firestore security rules (frontend/firestore.rules) restrict project_updates /
project_presence reads to UIDs listed in the document's allowed_users array.
Projects created BEFORE this change have no allowed_users field, so their
realtime updates would be blocked once the new rules are deployed.

This script scans all projects in the database, collects each project's
owner + accepted members' firebase_uid, and writes the allowed_users array
to the corresponding Firestore project_updates/{project_id} document via
event_manager.set_allowed_users().

Run AFTER the backend code deploy and BEFORE `firebase deploy --only firestore:rules`:

    # Dry run (prints what would be written, no Firestore writes)
    cd backend && uv run python scripts/backfill_allowed_users.py --dry-run

    # Execute
    cd backend && uv run python scripts/backfill_allowed_users.py

Requires DATABASE_URL and Firebase Admin credentials
(GOOGLE_APPLICATION_CREDENTIALS or Cloud Run default credentials) in the
environment, same as the API server.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Allow running as `python scripts/backfill_allowed_users.py` from backend/
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select  # noqa: E402

# Import the api package first to fully initialize it; importing
# src.services.event_manager directly would trigger a circular import
# (event_manager -> src.api.deps -> src.api.__init__ -> projects -> event_manager).
import src.api  # noqa: E402, F401
from src.models.database import async_session_maker  # noqa: E402
from src.models.project import Project  # noqa: E402
from src.models.project_member import ProjectMember  # noqa: E402
from src.models.user import User  # noqa: E402
from src.services.event_manager import event_manager  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_allowed_users")


async def collect_project_allowed_users() -> dict[str, list[str]]:
    """Return {project_id: [firebase_uid, ...]} for all projects.

    Includes the owner and all accepted members (same logic as
    members._refresh_firestore_allowed_users).
    """
    result: dict[str, list[str]] = {}

    async with async_session_maker() as db:
        # Owner UID per project
        rows = (
            await db.execute(
                select(Project.id, User.firebase_uid).join(User, Project.user_id == User.id)
            )
        ).all()
        for project_id, owner_uid in rows:
            result[str(project_id)] = [owner_uid]

        # Accepted members per project
        member_rows = (
            await db.execute(
                select(ProjectMember.project_id, User.firebase_uid)
                .join(User, ProjectMember.user_id == User.id)
                .where(ProjectMember.accepted_at.isnot(None))
            )
        ).all()
        for project_id, member_uid in member_rows:
            key = str(project_id)
            if key in result and member_uid not in result[key]:
                result[key].append(member_uid)

    return result


async def main(dry_run: bool) -> int:
    projects = await collect_project_allowed_users()
    logger.info("Found %d projects to backfill", len(projects))

    failures = 0
    for project_id, uids in projects.items():
        if dry_run:
            logger.info("[DRY-RUN] %s -> allowed_users=%s", project_id, uids)
            continue

        try:
            await event_manager.set_allowed_users(project_id=project_id, firebase_uids=uids)
            logger.info("OK %s (%d UIDs)", project_id, len(uids))
        except Exception:
            # set_allowed_users swallows most errors internally; this catch is
            # defence-in-depth for unexpected failures (e.g. credential errors).
            logger.exception("FAILED %s", project_id)
            failures += 1

    if dry_run:
        logger.info("[DRY-RUN] No Firestore writes performed.")
        return 0

    if failures:
        logger.error("%d project(s) failed — re-run the script to retry.", failures)
        return 1

    logger.info("Backfill complete: %d projects updated.", len(projects))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be written without touching Firestore",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(dry_run=args.dry_run)))
