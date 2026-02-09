from collections.abc import AsyncGenerator
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import get_settings
from src.models.base import Base

settings = get_settings()

# Async engine for FastAPI
# Cloud SQL db-f1-micro: max_connections=25
# Cloud Run: maxScale=4 → 4 instances × 5 = 20 connections (within limit)
engine = create_async_engine(
    settings.database_url,
    echo=settings.database_echo,
    future=True,
    pool_size=5,  # Strict 5 per instance (no overflow to stay within 25)
    max_overflow=0,  # No overflow — queue instead of exceeding limit
    pool_pre_ping=True,  # Check connection health before use
    pool_recycle=300,  # Recycle connections after 5 minutes
    pool_timeout=30,  # Wait 30 seconds for connection before timeout
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Sync engine for Celery tasks
# Convert asyncpg URL to psycopg2 URL
sync_database_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")

sync_engine = create_engine(
    sync_database_url,
    echo=settings.database_echo,
    future=True,
    pool_size=1,  # Minimal — sync engine unused in Cloud Run
    max_overflow=0,
    pool_pre_ping=True,
    pool_recycle=1800,
)

sync_session_maker = sessionmaker(
    sync_engine,
    class_=Session,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Initialize database with retry logic for connection failures."""
    import asyncio
    import logging

    logger = logging.getLogger(__name__)
    max_retries = 5
    retry_delay = 2  # seconds

    for attempt in range(max_retries):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                # Run migrations for new columns
                await run_migrations(conn)
            return  # Success
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(
                    f"DB connection attempt {attempt + 1}/{max_retries} failed: {e}. "
                    f"Retrying in {retry_delay} seconds..."
                )
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                logger.error(f"Failed to connect to database after {max_retries} attempts")
                raise


async def run_migrations(conn) -> None:
    """Run manual migrations for columns that create_all doesn't handle."""
    from sqlalchemy import text

    # Migration: Add is_internal column to assets table if it doesn't exist
    await conn.execute(
        text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'assets' AND column_name = 'is_internal'
            ) THEN
                ALTER TABLE assets ADD COLUMN is_internal BOOLEAN DEFAULT FALSE;
            END IF;
        END $$;
    """)
    )

    # Migration: Add video_brief and video_plan JSONB columns to projects table
    await conn.execute(
        text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'projects' AND column_name = 'video_brief'
            ) THEN
                ALTER TABLE projects ADD COLUMN video_brief JSONB;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'projects' AND column_name = 'video_plan'
            ) THEN
                ALTER TABLE projects ADD COLUMN video_plan JSONB;
            END IF;
        END $$;
    """)
    )

    # Migration: Add hash column to assets table for session file fingerprint matching
    await conn.execute(
        text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'assets' AND column_name = 'hash'
            ) THEN
                ALTER TABLE assets ADD COLUMN hash VARCHAR(100);
                CREATE INDEX IF NOT EXISTS idx_assets_hash ON assets(hash) WHERE hash IS NOT NULL;
            END IF;
        END $$;
    """)
    )

    # Migration: Add asset_metadata JSONB column to assets table for session metadata
    await conn.execute(
        text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'assets' AND column_name = 'asset_metadata'
            ) THEN
                ALTER TABLE assets ADD COLUMN asset_metadata JSONB;
            END IF;
        END $$;
    """)
    )

    # Migration: Add thumbnail_storage_key column to assets table
    await conn.execute(
        text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'assets' AND column_name = 'thumbnail_storage_key'
            ) THEN
                ALTER TABLE assets ADD COLUMN thumbnail_storage_key VARCHAR(500);
            END IF;
        END $$;
    """)
    )

    # Migration: Add thumbnail_storage_key column to projects table
    await conn.execute(
        text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'projects' AND column_name = 'thumbnail_storage_key'
            ) THEN
                ALTER TABLE projects ADD COLUMN thumbnail_storage_key VARCHAR(500);
            END IF;
        END $$;
    """)
    )

    # Migration: Create project_members table for collaborative editing
    await conn.execute(
        text("""
        CREATE TABLE IF NOT EXISTS project_members (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role VARCHAR(20) NOT NULL DEFAULT 'editor',
            invited_by UUID REFERENCES users(id) ON DELETE SET NULL,
            invited_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            accepted_at TIMESTAMPTZ,
            UNIQUE(project_id, user_id)
        )
    """)
    )
    await conn.execute(
        text("""
        CREATE INDEX IF NOT EXISTS idx_project_members_project_id ON project_members(project_id)
    """)
    )
    await conn.execute(
        text("""
        CREATE INDEX IF NOT EXISTS idx_project_members_user_id ON project_members(user_id)
    """)
    )

    # Migration: Add version column for optimistic locking
    await conn.execute(
        text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'projects' AND column_name = 'version'
            ) THEN
                ALTER TABLE projects ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
            END IF;
        END $$;
    """)
    )

    # Migration: Add project_version column to project_operations table
    await conn.execute(
        text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'project_operations' AND column_name = 'project_version'
            ) THEN
                ALTER TABLE project_operations ADD COLUMN project_version INTEGER;
                CREATE INDEX IF NOT EXISTS idx_project_operations_project_version
                    ON project_operations(project_id, project_version);
            END IF;
        END $$;
    """)
    )

    # Backfill: Create owner membership for all existing projects
    # Skip if all projects already have memberships
    await conn.execute(
        text("""
        INSERT INTO project_members (id, project_id, user_id, role, invited_at, accepted_at)
        SELECT gen_random_uuid(), p.id, p.user_id, 'owner', p.created_at, p.created_at
        FROM projects p
        WHERE NOT EXISTS (
            SELECT 1 FROM project_members pm WHERE pm.project_id = p.id AND pm.user_id = p.user_id
        )
    """)
    )

    # Migration: Create sequences table
    await conn.execute(
        text("""
        CREATE TABLE IF NOT EXISTS sequences (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            timeline_data JSONB NOT NULL DEFAULT '{}',
            version INTEGER NOT NULL DEFAULT 1,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            locked_by UUID REFERENCES users(id) ON DELETE SET NULL,
            locked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    )
    await conn.execute(
        text("""
        CREATE INDEX IF NOT EXISTS idx_sequences_project_id ON sequences(project_id)
    """)
    )

    # Migration: Add thumbnail_storage_key column to sequences table
    await conn.execute(
        text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'sequences' AND column_name = 'thumbnail_storage_key'
            ) THEN
                ALTER TABLE sequences ADD COLUMN thumbnail_storage_key VARCHAR(500);
            END IF;
        END $$;
    """)
    )

    # Migration: Create sequence_snapshots table
    await conn.execute(
        text("""
        CREATE TABLE IF NOT EXISTS sequence_snapshots (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            sequence_id UUID NOT NULL REFERENCES sequences(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            timeline_data JSONB NOT NULL DEFAULT '{}'::jsonb,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    )
    await conn.execute(
        text("""
        CREATE INDEX IF NOT EXISTS idx_sequence_snapshots_sequence_id ON sequence_snapshots(sequence_id)
    """)
    )

    # Migration: Migrate existing project timeline_data to default sequences
    await conn.execute(
        text("""
        INSERT INTO sequences (id, project_id, name, timeline_data, version, duration_ms, is_default)
        SELECT gen_random_uuid(), id, 'Default', COALESCE(timeline_data, '{}'), version, duration_ms, TRUE
        FROM projects
        WHERE NOT EXISTS (
            SELECT 1 FROM sequences WHERE sequences.project_id = projects.id AND sequences.is_default = TRUE
        )
    """)
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@contextmanager
def get_sync_db() -> Generator[Session, None, None]:
    """Get a synchronous database session for Celery tasks."""
    session = sync_session_maker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
