"""Alembic environment script.

Configured for async SQLAlchemy using the existing DATABASE_URL setting.
Supports both online (migration execution) and offline (SQL generation) modes.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ---------------------------------------------------------------------------
# Alembic Config object
# ---------------------------------------------------------------------------
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Import all ORM models so that Base.metadata is fully populated.
# This is required for autogenerate to detect schema differences.
# ---------------------------------------------------------------------------
# Import src.main to trigger all model registrations as a side-effect.
# We import individual models to avoid triggering FastAPI app startup.
import src.main  # noqa: F401, E402  — registers all ORM models on Base.metadata

from src.models.base import Base  # noqa: E402

target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Indexes that exist in the DB (created by the baseline revision) but are not
# reflected in the ORM metadata because they use raw SQL expressions that
# SQLAlchemy cannot reconstruct accurately (e.g. DESC expression indexes).
# Autogenerate would otherwise flag these as "to be removed" on every run.
# ---------------------------------------------------------------------------
_BASELINE_ONLY_INDEXES: frozenset[str] = frozenset(
    {
        # DESC composite indexes — cannot be represented cleanly in ORM Index()
        # because SQLAlchemy does not emit DESC on expression index columns the
        # same way PostgreSQL stores them, causing false-positive "changed" diffs.
        "idx_project_operations_created_at",
        "idx_sequence_snapshots_seq_auto",
        # ix_project_operations_* are duplicates of idx_ variants above.
        # They exist in the DB (created by baseline) but are not tracked in ORM
        # metadata to avoid double-managing the same logical index.
        "ix_project_operations_project_id",
        "ix_project_operations_operation_type",
        "ix_project_operations_created_at",
        "ix_project_operations_user_id",
        "ix_project_operations_project_version",
    }
)


def _include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    """Filter autogenerate comparisons.

    Skip indexes that live only in the DB baseline and cannot be represented
    faithfully via the ORM metadata (DESC indexes, duplicates, etc.).
    """
    if type_ == "index" and name in _BASELINE_ONLY_INDEXES:
        return False
    return True


# ---------------------------------------------------------------------------
# Read DATABASE_URL from the environment (same as the app).
# Fall back to the value in alembic.ini only if the env var is absent
# (useful for generating offline SQL scripts).
# ---------------------------------------------------------------------------
_db_url = os.environ.get("DATABASE_URL")
if _db_url:
    # Convert async URL to sync for Alembic's synchronous offline mode,
    # but keep the async version for the online (async engine) path.
    config.set_main_option("sqlalchemy.url", _db_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with a URL and not an Engine.
    Useful for generating SQL scripts without a live database.
    """
    url = config.get_main_option("sqlalchemy.url")
    # Swap asyncpg driver for psycopg2 in offline (synchronous) mode
    if url and "asyncpg" in url:
        url = url.replace("postgresql+asyncpg://", "postgresql://")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=False,
        include_object=_include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # compare_server_default is intentionally False: the ORM uses Python-side
        # defaults (e.g. default=False) rather than server_default= declarations
        # for most columns, so enabling this flag produces false positives.
        compare_server_default=False,
        include_object=_include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations."""
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = url

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode using the async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
