"""Integration test for the foreign-currency guard end to end: one incoming
text message naming a foreign currency, one drain tick — real Postgres, a
`FakeLlmClient` given zero scripted responses (so any call to it fails the
test loudly, see `tests/support/fake_llm.py`), no Telegram socket. Mirrors
`tests/integration/test_confirmation_flow.py`'s harness.
"""

import asyncio
import contextlib
from typing import cast

from aiogram import Bot
from aiogram.methods import SendMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from finbot.adapters.telegram.render import FOREIGN_CURRENCY_REPLY
from finbot.adapters.telegram.runner import drain_loop
from finbot.config import Settings
from finbot.core.extraction.currency import FOREIGN_CURRENCY_ERROR
from finbot.core.extraction.ports import LlmClient
from finbot.core.models import IncomingMessage, MessageKind, MessageStatus
from finbot.repo import messages, users
from finbot.repo.engine import json_serializer
from finbot.repo.models import Message
from tests.support.fake_llm import FakeLlmClient
from tests.support.fake_session import FakeSession
from tests.support.updates import ALLOWED_USER_ID, CHAT_ID


def _settings(postgres_url: str) -> Settings:
    return Settings(
        telegram_bot_token="42:TESTTOKEN",
        telegram_allowed_user_ids=str(ALLOWED_USER_ID),
        telegram_chat_id=CHAT_ID,
        database_url=postgres_url,
        openrouter_api_key="sk-or-fake-not-a-real-key",
        model_text="google/gemini-3.5-flash-lite",
    )


async def _seed_pending_message(session: AsyncSession, raw_text: str) -> int:
    incoming = IncomingMessage(
        telegram_update_id=abs(hash(raw_text)) % 1_000_000_000,
        telegram_message_id=1,
        chat_id=CHAT_ID,
        telegram_user_id=ALLOWED_USER_ID,
        display_name="Alice",
        kind=MessageKind.TEXT,
        raw_text=raw_text,
        file_id=None,
    )
    user_id = await users.get_or_create(session, incoming.telegram_user_id, incoming.display_name)
    message_id = await messages.add_if_new(session, incoming, user_id)
    assert message_id is not None
    await session.commit()
    return message_id


async def _run_one_drain_tick(
    *,
    bot: Bot,
    sessionmaker: async_sessionmaker[AsyncSession],
    llm: LlmClient,
    settings: Settings,
) -> None:
    stop = asyncio.Event()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(
            drain_loop(
                bot=bot,
                sessionmaker=sessionmaker,
                llm=llm,
                settings=settings,
                stop=stop,
                idle_seconds=5.0,
            ),
            timeout=1.0,
        )


async def test_foreign_currency_message_never_calls_the_model_and_gets_refused(
    db_session: AsyncSession, postgres_url: str
) -> None:
    bot = Bot(token="42:TESTTOKEN", session=FakeSession())
    message_id = await _seed_pending_message(db_session, "icloud - 10доларів")

    engine = create_async_engine(postgres_url, pool_pre_ping=True, json_serializer=json_serializer)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    # Zero scripted responses: FakeLlmClient.complete() raises AssertionError
    # if it is ever called at all — the mechanical proof that this message
    # cost zero model calls, not just an assertion about call counts.
    llm = FakeLlmClient()
    settings = _settings(postgres_url)

    try:
        await _run_one_drain_tick(bot=bot, sessionmaker=sessionmaker, llm=llm, settings=settings)
    finally:
        await engine.dispose()

    sent = [r for r in cast(FakeSession, bot.session).requests if isinstance(r, SendMessage)]
    assert len(sent) == 1
    assert sent[0].text == FOREIGN_CURRENCY_REPLY

    db_session.expire_all()
    refreshed = await db_session.get(Message, message_id)
    assert refreshed is not None
    assert refreshed.status == MessageStatus.SKIPPED
    assert refreshed.last_error == FOREIGN_CURRENCY_ERROR
