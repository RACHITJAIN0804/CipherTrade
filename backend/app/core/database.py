"""
Async SQLAlchemy 2.x database engine and session factory.

Production (PostgreSQL):
    Uses asyncpg driver; DATABASE_URL must start with
    ``postgresql+asyncpg://...``

Tests (SQLite):
    Tests override the engine with an in-process aiosqlite database so
    no PostgreSQL instance is required to run the test suite.
"""
from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""
    pass


def _make_engine(database_url: str | None = None):
    url = database_url or get_settings().database_url
    # SQLite needs different connect_args
    if url.startswith("sqlite"):
        return create_async_engine(url, connect_args={"check_same_thread": False})
    return create_async_engine(url, pool_pre_ping=True)


def _make_session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


# Module-level singletons — initialised lazily on first use so that
# importing this module at test-collection time does NOT try to load
# asyncpg before the test fixtures have patched the engine.
_engine = None
_session_factory = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = _make_engine()
    return _engine


def _get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = _make_session_factory(_get_engine())
    return _session_factory


# Convenience aliases kept for backwards-compatibility with code that
# imports these names directly.  They resolve lazily on first access.
class _LazyEngine:
    def __getattr__(self, name):
        return getattr(_get_engine(), name)


class _LazySessionLocal:
    def __call__(self, *args, **kwargs):
        return _get_session_factory()(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(_get_session_factory(), name)


engine = _LazyEngine()          # type: ignore[assignment]
AsyncSessionLocal = _LazySessionLocal()  # type: ignore[assignment]


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an async DB session."""
    async with _get_session_factory()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
