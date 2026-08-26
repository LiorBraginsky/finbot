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

from finbot.repo.engine import json_serializer as finbot_json_serializer

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


@pytest.fixture(scope="session", autouse=True)
def _openrouter_base_url_guard() -> Iterator[None]:
    """Point every test at the discard port, unconditionally.

    Nothing in this suite should ever construct a real `OpenRouterClient` —
    `FakeLlmClient` stands in everywhere — but if a future change did, this
    makes the mistake fail in milliseconds with a connection error instead
    of reaching a real provider and spending real money. Not a blanket
    socket ban: testcontainers talks to the Docker socket over HTTP, and
    Postgres itself needs a real TCP connection.
    """
    previous = os.environ.get("OPENROUTER_BASE_URL")
    os.environ["OPENROUTER_BASE_URL"] = "http://127.0.0.1:9"
    yield
    if previous is None:
        os.environ.pop("OPENROUTER_BASE_URL", None)
    else:
        os.environ["OPENROUTER_BASE_URL"] = previous


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
    #
    # json_serializer=finbot_json_serializer: without it, a JSONB column
    # holding a Decimal (extractions.raw_response, corrections.before/after)
    # raises "Object of type Decimal is not JSON serializable" on flush —
    # this test engine must serialize exactly like the production one in
    # finbot.repo.engine.create_sessionmaker, or the integration suite would
    # pass while production failed on the very data rule 2 exists to cover.
    engine = create_async_engine(
        postgres_url, pool_pre_ping=True, json_serializer=finbot_json_serializer
    )
    session = AsyncSession(engine, expire_on_commit=False)
    try:
        # The *seeded* rows of `categories` are deliberately never truncated:
        # they are inserted once by migrations 0002/0005/0006, and every
        # expense row is FK-bound to them — wiping them here would break
        # every FK and every later test. Owner-created categories are a
        # different matter: a `suggested`/`active` row a test caused the model
        # to propose (ADR-0021) must not leak into the next test, which is
        # what `DELETE FROM categories WHERE NOT is_system` removes. It runs
        # *before* `DELETE FROM users`, because `categories.created_by`
        # references it.
        #
        # `users` is deleted, not truncated, and the reason is worth keeping:
        # `categories.created_by` is a nullable FK to `users.id`, and
        # TRUNCATE's FK check is structural — Postgres refuses to truncate
        # `users` at all unless every table with an FK pointing at it is
        # truncated too, and CASCADE "solves" that by silently wiping
        # `categories` as a side effect (verified: it does, even though
        # `categories` is never named). DELETE's FK check is value-based, so
        # it succeeds as long as no row actually references what is being
        # deleted.
        #
        # That last clause used to read "true here, since Stage 1 never sets
        # `created_by`". ADR-0021 sets it — approving a proposed category
        # records who approved it — so the assumption expired and this
        # fixture started failing with a ForeignKeyViolationError. The
        # category delete above is what makes the clause true again, this time
        # by construction rather than by nothing having exercised it yet.
        #
        # The cost of DELETE over TRUNCATE is that `users.id` no longer resets
        # to 1 between tests; nothing in the suite depends on that, only on
        # counts and on IDs it captured itself.
        await session.execute(
            text("TRUNCATE expenses, corrections, extractions, messages RESTART IDENTITY CASCADE")
        )
        await session.execute(text("DELETE FROM categories WHERE NOT is_system"))
        await session.execute(text("DELETE FROM users"))
        await session.commit()
        yield session
    finally:
        await session.close()
        await engine.dispose()
