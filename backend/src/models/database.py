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
sync_database_url = settings.database_url.replace(
    "postgresql+asyncpg://", "postgresql+psycopg2://"
)

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
    await conn.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'assets' AND column_name = 'is_internal'
            ) THEN
                ALTER TABLE assets ADD COLUMN is_internal BOOLEAN DEFAULT FALSE;
            END IF;
        END $$;
    """))

    # Migration: Add video_brief and video_plan JSONB columns to projects table
    await conn.execute(text("""
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
    """))

    # Migration: Add hash column to assets table for session file fingerprint matching
    await conn.execute(text("""
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
    """))

    # Migration: Add asset_metadata JSONB column to assets table for session metadata
    await conn.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'assets' AND column_name = 'asset_metadata'
            ) THEN
                ALTER TABLE assets ADD COLUMN asset_metadata JSONB;
            END IF;
        END $$;
    """))

    # Migration: Add thumbnail_storage_key column to assets table
    await conn.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'assets' AND column_name = 'thumbnail_storage_key'
            ) THEN
                ALTER TABLE assets ADD COLUMN thumbnail_storage_key VARCHAR(500);
            END IF;
        END $$;
    """))

    # Migration: Add thumbnail_storage_key column to projects table
    await conn.execute(text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'projects' AND column_name = 'thumbnail_storage_key'
            ) THEN
                ALTER TABLE projects ADD COLUMN thumbnail_storage_key VARCHAR(500);
            END IF;
        END $$;
    """))


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
