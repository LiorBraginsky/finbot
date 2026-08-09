"""Async engine / session factory."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def create_sessionmaker(database_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    # expire_on_commit=False: with async sessions, a post-commit lazy refresh
    # raises instead of silently issuing SQL, so keep committed objects readable.
    return async_sessionmaker(engine, expire_on_commit=False)
