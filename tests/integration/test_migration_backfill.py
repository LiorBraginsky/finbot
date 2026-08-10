"""Integration test for the Stage 1 migration's backfill of pre-existing
`messages` rows (CRITICAL 1 of the Stage 1 review): every row that predates
the `status` column must land on `skipped`, never the column's own
server_default `pending`, or the drain loop claims Stage 0's production
backlog on the very first `alembic upgrade head` — real messages sent days
ago, billed to the model, and written as expenses dated today.

Deliberately not the session-scoped `postgres_url` fixture from
`conftest.py`: that fixture is already migrated to `head` before any test in
the suite could observe the pre-0002 state this backfill exists to fix. A
fresh, dedicated container gives `0001`'s own `messages` table — no `status`
column yet — to insert a pre-existing row into, exactly like a message
Stage 0 persisted before Stage 1 ever ran. No skipif on Docker availability,
matching the rest of this suite (ADR-0012): if Docker is absent, this test
fails rather than silently proving nothing.

A plain (non-async) test function on purpose: `alembic.command.upgrade`
drives `migrations/env.py`, which calls `asyncio.run(...)` itself — that
raises "asyncio.run() cannot be called from a running event loop" the
instant it is invoked from inside a test coroutine pytest-asyncio is already
driving. The row insert and the final read each get their own short-lived
event loop via `asyncio.run`, with no loop left running in between.
"""

import asyncio
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer

_REPO_ROOT = Path(__file__).parents[2]


def _alembic_config() -> Config:
    # DATABASE_URL, not a parameter here: migrations/env.py reads it from the
    # environment (see its own comment on why), so the caller sets that
    # before invoking any `command.*` against this Config.
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    return cfg


async def _insert_pre_existing_message(url: str) -> int:
    """A message exactly like one Stage 0 already persisted: inserted while
    the schema is still at revision 0001, so `messages` has no `status`
    column at all yet.
    """
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            user_id = (
                await conn.execute(
                    text(
                        "INSERT INTO users (telegram_user_id, display_name) "
                        "VALUES (:telegram_user_id, :display_name) RETURNING id"
                    ),
                    {"telegram_user_id": 555555555, "display_name": "Stage0 User"},
                )
            ).scalar_one()
            return (
                await conn.execute(
                    text(
                        "INSERT INTO messages "
                        "(telegram_update_id, telegram_message_id, chat_id, user_id, "
                        "kind, raw_text) "
                        "VALUES (:update_id, 1, :chat_id, :user_id, 'text', '/ping') "
                        "RETURNING id"
                    ),
                    {"update_id": 123456789, "chat_id": -1001111111111, "user_id": user_id},
                )
            ).scalar_one()
    finally:
        await engine.dispose()


async def _read_status(url: str, message_id: int) -> str:
    engine = create_async_engine(url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            return str(
                (
                    await conn.execute(
                        text("SELECT status FROM messages WHERE id = :id"), {"id": message_id}
                    )
                ).scalar_one()
            )
    finally:
        await engine.dispose()


def test_a_message_predating_the_status_column_is_backfilled_to_skipped() -> None:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        url = container.get_connection_url()
        previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        try:
            cfg = _alembic_config()
            command.upgrade(cfg, "0001")  # users + messages, no status column yet

            message_id = asyncio.run(_insert_pre_existing_message(url))

            command.upgrade(cfg, "head")  # 0002 adds `status` and must backfill it

            status = asyncio.run(_read_status(url, message_id))
        finally:
            if previous_database_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_database_url

    assert status == "skipped"
