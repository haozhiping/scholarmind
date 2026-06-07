"""
PostgreSQL async engine and session factory.
Used only by the chat_agent service for conversation memory.
"""
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from common.config import settings

DATABASE_URL = (
    f"postgresql+asyncpg://{settings.PG_USER}:{settings.PG_PASSWORD}"
    f"@{settings.PG_HOST}:{settings.PG_PORT}/{settings.PG_DB}"
)

engine = create_async_engine(
    DATABASE_URL,
    pool_size=getattr(settings, "MYSQL_POOL_SIZE", 5),
    max_overflow=10,
    pool_recycle=3600,
    echo=False,
)

_AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def get_pg_session():
    """Async context manager that yields a PG session, commits on exit, rolls back on error."""
    async with _AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
