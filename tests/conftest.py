"""Shared test fixtures: a real, throwaway Postgres for the integration suite.

There is no skipif on Docker availability. If the container cannot start, the
tests that depend on it fail — a skipped test in an auto-merging pipeline is a
green gate that proves nothing.
"""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        url = container.get_connection_url()
        os.environ["DATABASE_URL"] = url

        cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        cfg.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))
        command.upgrade(cfg, "head")

        yield url


@pytest_asyncio.fixture
async def db_session(postgres_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(postgres_url, pool_pre_ping=True)
    session = AsyncSession(engine, expire_on_commit=False)
    try:
        yield session
        await session.execute(text("TRUNCATE messages, users RESTART IDENTITY CASCADE"))
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()
