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
from testcontainers.community.postgres import PostgresContainer

# Ryuk, testcontainers' resource-reaper sidecar, has its own startup race, not
# an environment flake: Reaper._create_instance() (testcontainers/core/
# container.py) calls .waiting_for(...) only *after* .start() has already
# returned, so start() never actually waits for Ryuk's "Started!" log line,
# and the very next line asks Docker for Ryuk's port mapping before Docker
# has always finished registering it — intermittently raising
# "ConnectionError: Port mapping for container ... and port 8080 is not
# available". Confirmed from a full traceback rooted in exactly
# Reaper.get_instance() -> _create_instance() -> get_exposed_port(8080), not
# assumed. It recurred 1-in-3 to 1-in-5 runs against this unchanged suite.
# Disabling Ryuk removes the racing container entirely; TESTCONTAINERS_
# RYUK_DISABLED is read lazily on the first container start and cached for
# the process (see testcontainers.core.config.TestcontainersConfiguration.
# ryuk_disabled), not at import time, so setting it here — after the import,
# before any fixture runs — is early enough.
# Cost: Ryuk is what force-removes this session's throwaway Postgres
# container if the test process is killed hard (SIGKILL, OOM, power loss).
# Without it, a hard kill leaks that container; every other exit path (pass,
# fail, Ctrl-C) still runs `with PostgresContainer(...)`'s __exit__, which
# calls .stop() itself.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")


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
    # Truncate before yielding, not after: pytest throws into this generator at
    # the yield point on a failing test, which would skip a post-yield truncate
    # inside this try and leave rows for the next test to trip over. Truncating
    # first makes each test's starting state independent of how the previous
    # one ended, including a previous truncate that never ran at all.
    engine = create_async_engine(postgres_url, pool_pre_ping=True)
    session = AsyncSession(engine, expire_on_commit=False)
    try:
        # `categories` is deliberately absent: it is seeded once by
        # migrations/versions/0002_stage1_expenses.py, and every expense row
        # is FK-bound to it — truncating it here would break every FK and
        # every later test, for a table stage 1 populates and Stage 5 evolves
        # in place rather than reseeding per test.
        #
        # `users` is truncated separately, with DELETE rather than TRUNCATE,
        # for the same reason: `categories.created_by` is a nullable FK to
        # `users.id`, and TRUNCATE's FK check is structural — Postgres refuses
        # to truncate `users` at all unless every table with an FK pointing at
        # it is truncated too, and CASCADE "solves" that by silently wiping
        # `categories` as a side effect (verified: it does, even though
        # `categories` is never named). DELETE's FK check is value-based
        # instead, so it succeeds without CASCADE as long as no row actually
        # references what is being deleted — true here, since Stage 1 never
        # sets `created_by` (Stage 5 does). The cost is that `users.id` no
        # longer resets to 1 between tests; nothing in the suite depends on
        # that, only on counts and on IDs it captured itself.
        await session.execute(
            text("TRUNCATE expenses, corrections, extractions, messages RESTART IDENTITY CASCADE")
        )
        await session.execute(text("DELETE FROM users"))
        await session.commit()
        yield session
    finally:
        await session.close()
        await engine.dispose()
