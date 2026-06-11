"""Absorb Migration 014: render_jobs additions + legacy index corrections.

Revision ID: 0002_render_jobs_014
Revises: 0001_baseline
Create Date: 2026-06-11

Changes applied on top of 0001_baseline:

  render_jobs (Migration 014 from the legacy run_migrations() series):
    - Widen celery_task_id from VARCHAR(100) to VARCHAR(255) for long Cloud Run
      Execution names (ADR-001, Issue #281 / PR #342).
    - Add timeline_snapshot JSONB column (stores resolved timeline for Cloud Run
      Jobs workers in jobs mode).
    - Add render_params JSONB column (stores audio_only, render_duration_ms, etc.).

  assets (legacy-DB index correction):
    - Drop ix_assets_source_asset_id, a non-partial index created by the old
      create_all path that is absent from 0001_baseline.  On new databases
      (built purely via Alembic) this index does not exist, so the DROP uses
      IF EXISTS.
    - Ensure idx_assets_hash (partial, hash IS NOT NULL) and
      idx_assets_source_asset_id (partial, source_asset_id IS NOT NULL) exist.
      These are already present in 0001_baseline for new databases; for legacy
      databases stamped at 0001_baseline they were not yet applied.  Both
      CREATE INDEX calls use IF NOT EXISTS so this step is always idempotent.

  project_operations:
    - Ensure idx_project_operations_project_version composite index on
      (project_id, project_version) exists.  Same reasoning: present in
      0001_baseline for new databases, absent for stamped legacy databases.
      CREATE INDEX IF NOT EXISTS makes it idempotent.

Downgrade note:
  Reversing this migration restores the three render_jobs columns to their
  pre-014 state (columns dropped, celery_task_id narrowed to VARCHAR(100)).
  Data stored in timeline_snapshot / render_params will be discarded.
  The legacy non-partial ix_assets_source_asset_id index is recreated with
  IF NOT EXISTS so it is safe to run even on databases that already have it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_render_jobs_014"
down_revision: str | Sequence[str] | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # render_jobs — Migration 014 (ADR-001, Issue #281 / PR #342)
    # ------------------------------------------------------------------
    op.alter_column(
        "render_jobs",
        "celery_task_id",
        existing_type=sa.String(100),
        type_=sa.String(255),
        existing_nullable=True,
    )
    op.add_column(
        "render_jobs",
        sa.Column(
            "timeline_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "render_jobs",
        sa.Column(
            "render_params",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    # ------------------------------------------------------------------
    # assets — legacy ix_ index cleanup + ensure partial indexes exist
    # ------------------------------------------------------------------
    # Drop the old non-partial index created by the legacy create_all path.
    # On new databases (built purely from Alembic) this index was never
    # created, so IF EXISTS makes the step idempotent.
    op.execute("DROP INDEX IF EXISTS ix_assets_source_asset_id")

    # Ensure the partial indexes exist.  0001_baseline already creates them
    # for brand-new databases; for legacy databases that were stamped at
    # 0001_baseline without running DDL, they are missing.  IF NOT EXISTS
    # keeps the step idempotent in both cases.
    op.execute("CREATE INDEX IF NOT EXISTS idx_assets_hash ON assets (hash) WHERE hash IS NOT NULL")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_assets_source_asset_id "
        "ON assets (source_asset_id) WHERE source_asset_id IS NOT NULL"
    )

    # ------------------------------------------------------------------
    # project_operations — ensure composite index exists
    # ------------------------------------------------------------------
    # Same reasoning as above: 0001_baseline creates it for new databases;
    # legacy databases need it applied here.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_project_operations_project_version "
        "ON project_operations (project_id, project_version)"
    )


def downgrade() -> None:
    # ------------------------------------------------------------------
    # render_jobs — revert Migration 014
    # ------------------------------------------------------------------
    op.drop_column("render_jobs", "render_params")
    op.drop_column("render_jobs", "timeline_snapshot")
    op.alter_column(
        "render_jobs",
        "celery_task_id",
        existing_type=sa.String(255),
        type_=sa.String(100),
        existing_nullable=True,
    )

    # ------------------------------------------------------------------
    # assets — restore legacy non-partial index
    # ------------------------------------------------------------------
    # idx_assets_hash and idx_assets_source_asset_id (partial) are defined in
    # 0001_baseline so they remain present after this downgrade.  Only the old
    # non-partial ix_assets_source_asset_id (absent from 0001_baseline) needs
    # to be recreated for legacy databases.  IF NOT EXISTS is defensive in case
    # a prior partial downgrade already created it.
    op.execute("CREATE INDEX IF NOT EXISTS ix_assets_source_asset_id ON assets (source_asset_id)")
    # idx_project_operations_project_version is also present in 0001_baseline,
    # so it does not need to be dropped on downgrade.
