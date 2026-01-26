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
# Limit pool size to avoid exhausting Cloud SQL connections
# Cloud SQL Basic tier has ~25 max connections
engine = create_async_engine(
    settings.database_url,
    echo=settings.database_echo,
    future=True,
    pool_size=5,  # Base connections per instance (Cloud SQL Basic ~25 max)
    max_overflow=5,  # Extra connections allowed (total 10 per instance)
    pool_pre_ping=True,  # Check connection health before use
    pool_recycle=300,  # Recycle connections after 5 minutes
    pool_timeout=10,  # Wait 10 seconds for connection before timeout
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
    pool_size=2,
    max_overflow=1,
    pool_pre_ping=True,
    pool_recycle=1800,
)

sync_session_maker = sessionmaker(
    sync_engine,
    class_=Session,
    expire_on_commit=False,
)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Run migrations for new columns
        await run_migrations(conn)


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
