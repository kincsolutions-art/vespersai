"""Async engine/session construction for the least-privilege runtime role."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """Runtime engine. Uses the application role URL; never migration credentials."""
    return create_async_engine(str(settings.database_url), pool_pre_ping=True)


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
