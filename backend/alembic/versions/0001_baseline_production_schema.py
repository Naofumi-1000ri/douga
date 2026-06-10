"""Baseline production schema.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-06-11

This revision represents the complete schema that was previously managed by
``run_migrations()`` + ``Base.metadata.create_all`` in src/models/database.py.

On **new (empty) databases**: running ``alembic upgrade head`` applies the full
schema from scratch.

On **existing production databases**: the schema is already in place.  Do NOT
run upgrade — instead, stamp the database to mark this revision as applied:

    alembic stamp 0001_baseline

This tells Alembic "everything up to this revision is already done" without
executing any DDL.

After stamping, future schema changes should be added as new Alembic revisions
(``alembic revision --autogenerate -m "description"``) and applied with
``alembic upgrade head``.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_baseline"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the full baseline schema from scratch.

    This is a no-op on production (use ``alembic stamp 0001_baseline`` instead).
    On new/test databases this builds the complete schema.
    """
    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("firebase_uid", sa.String(128), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("avatar_url", sa.String(2048), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    # Note: ix_users_email is NOT created here — SQLAlchemy's unique=True on the
    # email column creates a UniqueConstraint (users_email_key) automatically.
    # Creating a separate ix_users_email index would be redundant.
    op.create_index("ix_users_firebase_uid", "users", ["firebase_uid"], unique=True)

    # ------------------------------------------------------------------
    # projects
    # ------------------------------------------------------------------
    op.create_table(
        "projects",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("fps", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("timeline_data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("video_brief", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("video_plan", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("thumbnail_url", sa.String(500), nullable=True),
        sa.Column("thumbnail_storage_key", sa.String(500), nullable=True),
        sa.Column("ai_api_key", sa.String(500), nullable=True),
        sa.Column("ai_provider", sa.String(50), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_user_id", "projects", ["user_id"])

    # ------------------------------------------------------------------
    # asset_folders
    # ------------------------------------------------------------------
    op.create_table(
        "asset_folders",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_asset_folders_project_id", "asset_folders", ["project_id"])

    # ------------------------------------------------------------------
    # assets
    # ------------------------------------------------------------------
    op.create_table(
        "assets",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("subtype", sa.String(50), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("storage_url", sa.String(1000), nullable=False),
        sa.Column("thumbnail_url", sa.String(1000), nullable=True),
        sa.Column("thumbnail_storage_key", sa.String(500), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("sample_rate", sa.Integer(), nullable=True),
        sa.Column("channels", sa.Integer(), nullable=True),
        sa.Column("has_alpha", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("chroma_key_color", sa.String(20), nullable=True),
        sa.Column("hash", sa.String(100), nullable=True),
        sa.Column("is_internal", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("asset_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["folder_id"], ["asset_folders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_asset_id"], ["assets.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_assets_project_id", "assets", ["project_id"])
    op.create_index("ix_assets_type", "assets", ["type"])
    op.create_index("ix_assets_folder_id", "assets", ["folder_id"])
    op.create_index(
        "idx_assets_project_name_type_unique",
        "assets",
        ["project_id", "name", "type"],
        unique=True,
    )
    op.create_index(
        "idx_assets_hash",
        "assets",
        ["hash"],
        postgresql_where=sa.text("hash IS NOT NULL"),
    )
    op.create_index(
        "idx_assets_source_asset_id",
        "assets",
        ["source_asset_id"],
        postgresql_where=sa.text("source_asset_id IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # api_keys
    # ------------------------------------------------------------------
    op.create_table(
        "api_keys",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_prefix", sa.String(20), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])

    # ------------------------------------------------------------------
    # render_jobs
    # ------------------------------------------------------------------
    op.create_table(
        "render_jobs",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("current_stage", sa.String(100), nullable=True),
        sa.Column("output_key", sa.Text(), nullable=True),
        sa.Column("output_url", sa.Text(), nullable=True),
        sa.Column("output_size", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("celery_task_id", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_render_jobs_project_id", "render_jobs", ["project_id"])
    op.create_index("ix_render_jobs_status", "render_jobs", ["status"])

    # ------------------------------------------------------------------
    # project_operations
    # ------------------------------------------------------------------
    op.create_table(
        "project_operations",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_type", sa.String(50), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("affected_clips", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("affected_layers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("affected_audio_clips", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("diff", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("request_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("result_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rollback_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rollback_available", sa.Boolean(), nullable=False),
        sa.Column("rolled_back", sa.Boolean(), nullable=False),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rolled_back_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("response_status_code", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("idempotency_key", sa.String(100), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_project_operations_project_id", "project_operations", ["project_id"]
    )
    op.create_index(
        "ix_project_operations_operation_type", "project_operations", ["operation_type"]
    )
    op.create_index(
        "ix_project_operations_created_at", "project_operations", ["created_at"]
    )
    op.create_index(
        "ix_project_operations_user_id", "project_operations", ["user_id"]
    )
    op.create_index(
        "ix_project_operations_project_version", "project_operations", ["project_version"]
    )
    # GIN indexes for JSONB array contains queries
    op.create_index(
        "idx_project_operations_affected_clips",
        "project_operations",
        ["affected_clips"],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_project_operations_affected_layers",
        "project_operations",
        ["affected_layers"],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_project_operations_affected_audio_clips",
        "project_operations",
        ["affected_audio_clips"],
        postgresql_using="gin",
    )
    # Additional named indexes matching run_migrations() names
    op.create_index(
        "idx_project_operations_project_id", "project_operations", ["project_id"]
    )
    op.create_index(
        "idx_project_operations_operation_type", "project_operations", ["operation_type"]
    )
    op.create_index(
        "idx_project_operations_created_at",
        "project_operations",
        [sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_project_operations_user_id", "project_operations", ["user_id"]
    )
    op.create_index(
        "idx_project_operations_project_version",
        "project_operations",
        ["project_id", "project_version"],
    )
    # Partial UNIQUE index for idempotency enforcement scoped by user
    op.create_index(
        "idx_project_operations_idempotency_key_unique",
        "project_operations",
        ["user_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    # ------------------------------------------------------------------
    # project_members
    # ------------------------------------------------------------------
    op.create_table(
        "project_members",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="editor"),
        sa.Column("invited_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "invited_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(["invited_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_member"),
    )
    op.create_index("idx_project_members_project_id", "project_members", ["project_id"])
    op.create_index("idx_project_members_user_id", "project_members", ["user_id"])
    op.create_index(
        "ix_project_members_project_id", "project_members", ["project_id"]
    )
    op.create_index("ix_project_members_user_id", "project_members", ["user_id"])

    # ------------------------------------------------------------------
    # sequences
    # ------------------------------------------------------------------
    op.create_table(
        "sequences",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "timeline_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("locked_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("thumbnail_storage_key", sa.String(500), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["locked_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_sequences_project_id", "sequences", ["project_id"])
    op.create_index("ix_sequences_project_id", "sequences", ["project_id"])
    op.create_index(
        "idx_sequences_project_id_is_default",
        "sequences",
        ["project_id", "is_default"],
        postgresql_where=sa.text("is_default = TRUE"),
    )

    # ------------------------------------------------------------------
    # sequence_snapshots
    # ------------------------------------------------------------------
    op.create_table(
        "sequence_snapshots",
        sa.Column("sequence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "timeline_data",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_auto", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["sequence_id"], ["sequences.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_sequence_snapshots_sequence_id", "sequence_snapshots", ["sequence_id"]
    )
    op.create_index(
        "ix_sequence_snapshots_sequence_id", "sequence_snapshots", ["sequence_id"]
    )
    op.create_index(
        "idx_sequence_snapshots_seq_auto",
        "sequence_snapshots",
        ["sequence_id", "is_auto", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    """Drop all tables in reverse dependency order."""
    op.drop_table("sequence_snapshots")
    op.drop_table("sequences")
    op.drop_table("project_members")
    op.drop_table("project_operations")
    op.drop_table("render_jobs")
    op.drop_table("api_keys")
    op.drop_table("assets")
    op.drop_table("asset_folders")
    op.drop_table("projects")
    op.drop_table("users")
