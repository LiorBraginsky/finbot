"""The load-bearing test of Stage 0: feed raw updates through the real
`Dispatcher` (allowlist -> db session -> persistence -> handlers) with a fake
transport session, and assert what ended up in Postgres and what would have
gone out over the wire.

No socket is opened: `FakeSession` (tests/support/fake_session.py) stands in
for aiogram's HTTP session, and `Dispatcher.feed_raw_update` is the same
entry point `dp.start_polling` drives in production. `build_dispatcher` is
the same factory `main.py` calls, so this cannot drift from production
wiring — see the plan's Approach B.
"""

from collections.abc import AsyncIterator
from typing import cast

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from finbot.adapters.telegram.main import build_dispatcher
from finbot.core.models import MessageKind, MessageStatus
from finbot.repo.models import Message, User
from tests.support.fake_session import FakeSession
from tests.support.updates import (
    ALLOWED_USER_ID,
    CHAT_ID,
    STRANGER_USER_ID,
    sticker_update,
    text_update,
    voice_update,
)


@pytest_asyncio.fixture
async def dispatcher(postgres_url: str) -> AsyncIterator[Dispatcher]:
    engine = create_async_engine(postgres_url, pool_pre_ping=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield build_dispatcher(sessionmaker, frozenset({ALLOWED_USER_ID}))
    finally:
        async with sessionmaker() as session:
            # DELETE, not TRUNCATE, for users: see tests/conftest.py's
            # db_session fixture for why TRUNCATE ... CASCADE here would
            # silently wipe the migration-seeded `categories` table too, via
            # its nullable `created_by` FK to `users.id`.
            await session.execute(text("TRUNCATE messages RESTART IDENTITY CASCADE"))
            await session.execute(text("DELETE FROM users"))
            await session.commit()
        await engine.dispose()


@pytest.fixture
def bot() -> Bot:
    return Bot(token="42:TESTTOKEN", session=FakeSession())


async def test_update_is_persisted_exactly_once_on_redelivery(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    update = text_update(update_id=1001, text="bread 50")

    await dispatcher.feed_raw_update(bot, update)

    row = (await db_session.execute(select(Message))).scalar_one()
    assert row.telegram_update_id == 1001
    assert row.telegram_message_id == update["message"]["message_id"]
    assert row.chat_id == CHAT_ID
    assert row.kind == MessageKind.TEXT
    assert row.raw_text == "bread 50"
    # Plain text, not a command: the inbox lane owns it (ADR-0013) — the
    # drain loop, not this handler, will claim and reply to it.
    assert row.status == MessageStatus.PENDING

    # The byte-identical payload again — same dict, same update_id.
    await dispatcher.feed_raw_update(bot, update)

    message_count = await db_session.scalar(select(func.count()).select_from(Message))
    user_count = await db_session.scalar(select(func.count()).select_from(User))
    assert message_count == 1
    assert user_count == 1


async def test_stranger_is_ignored_silently(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    update = text_update(update_id=2001, text="anything", user_id=STRANGER_USER_ID)

    await dispatcher.feed_raw_update(bot, update)

    message_count = await db_session.scalar(select(func.count()).select_from(Message))
    user_count = await db_session.scalar(select(func.count()).select_from(User))
    assert message_count == 0
    assert user_count == 0
    assert cast(FakeSession, bot.session).requests == []


async def test_ping_replies_pong_and_is_persisted(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    update = text_update(update_id=3001, text="/ping")

    await dispatcher.feed_raw_update(bot, update)

    sent = [r for r in cast(FakeSession, bot.session).requests if isinstance(r, SendMessage)]
    assert len(sent) == 1
    assert sent[0].text == "pong"

    row = (await db_session.execute(select(Message))).scalar_one()
    assert row.raw_text == "/ping"
    # A command, never sent to the model: persisted per ADR-0006 for the
    # provenance record, but the inbox lane must skip straight past it.
    assert row.status == MessageStatus.SKIPPED


async def test_day_command_is_persisted_as_skipped_never_pending(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    update = text_update(update_id=3501, text="/day")

    await dispatcher.feed_raw_update(bot, update)

    row = (await db_session.execute(select(Message))).scalar_one()
    assert row.raw_text == "/day"
    assert row.status == MessageStatus.SKIPPED


async def test_voice_message_stores_file_id(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    update = voice_update(update_id=4001, file_id="voice-file-id")

    await dispatcher.feed_raw_update(bot, update)

    row = (await db_session.execute(select(Message))).scalar_one()
    assert row.kind == MessageKind.VOICE
    assert row.file_id == "voice-file-id"
    assert row.raw_text is None


async def test_unsupported_content_is_not_persisted(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    update = sticker_update(update_id=5001)

    await dispatcher.feed_raw_update(bot, update)

    message_count = await db_session.scalar(select(func.count()).select_from(Message))
    assert message_count == 0
    assert cast(FakeSession, bot.session).requests == []
