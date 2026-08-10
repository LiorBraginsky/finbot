"""Integration tests for the drain loop's confirmation: one incoming text
message, a fake model response, one drain tick — real Postgres, a fake LLM
client, no Telegram socket (mirrors
`tests/integration/test_extraction_pipeline.py`'s harness, one layer up).

`drain_loop` runs under an outer `asyncio.wait_for` shorter than its own
`idle_seconds`: after the one pending message is claimed and processed, the
loop finds nothing left, sleeps, and is cancelled there — by which point
every assertion below has already been committed.
"""

import asyncio
import contextlib
import json
from pathlib import Path
from typing import cast

import pytest
from aiogram import Bot
from aiogram.methods import SendMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from finbot.adapters.telegram.render import NO_EXPENSE_REPLY
from finbot.adapters.telegram.runner import drain_loop
from finbot.config import Settings
from finbot.core.extraction.ports import LlmClient
from finbot.core.models import IncomingMessage, MessageKind
from finbot.repo import messages, users
from finbot.repo.engine import json_serializer
from finbot.repo.models import Expense
from tests.support.fake_llm import FakeLlmClient
from tests.support.fake_session import FakeSession
from tests.support.updates import ALLOWED_USER_ID, CHAT_ID

_FIXTURES_DIR = Path(__file__).parents[1] / "fixtures" / "openrouter"


def _settings(postgres_url: str) -> Settings:
    return Settings(
        telegram_bot_token="42:TESTTOKEN",
        telegram_allowed_user_ids=str(ALLOWED_USER_ID),
        telegram_chat_id=CHAT_ID,
        database_url=postgres_url,
        openrouter_api_key="sk-or-fake-not-a-real-key",
        model_text="google/gemini-3.5-flash-lite",
    )


def _three_item_response_body() -> str:
    """No checked-in fixture has three items (Step 2 shipped `ok_two_items`
    for that shape's own test); building the body inline, the same way
    `test_extraction_pipeline.py`'s own `_response_body` helper does, avoids
    adding a fixture file outside this step's list.
    """
    content = {
        "expenses": [
            {"item": "хліб", "amount": 50, "category": "groceries", "occurred_at": None},
            {"item": "таксі", "amount": 200, "category": "transport", "occurred_at": None},
            {"item": "кава", "amount": 65, "category": "dining_out", "occurred_at": None},
        ]
    }
    assistant_message = {"role": "assistant", "content": json.dumps(content)}
    return json.dumps(
        {
            "id": "gen-fixture-three-items",
            "model": "google/gemini-3.5-flash-lite",
            "object": "chat.completion",
            "created": 1754800100,
            "choices": [{"index": 0, "message": assistant_message}],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "cost": 0.0003,
            },
        }
    )


def _load_fixture(name: str) -> str:
    return (_FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8")


async def _seed_pending_message(session: AsyncSession, raw_text: str) -> None:
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


@pytest.fixture
def bot() -> Bot:
    return Bot(token="42:TESTTOKEN", session=FakeSession())


async def test_three_expenses_produce_one_numbered_confirmation_sharing_a_bot_message_id(
    bot: Bot, db_session: AsyncSession, postgres_url: str
) -> None:
    await _seed_pending_message(db_session, "хліб 50, таксі 200, кава 65")

    engine = create_async_engine(postgres_url, pool_pre_ping=True, json_serializer=json_serializer)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    llm = FakeLlmClient(_three_item_response_body())
    settings = _settings(postgres_url)

    try:
        await _run_one_drain_tick(bot=bot, sessionmaker=sessionmaker, llm=llm, settings=settings)
    finally:
        await engine.dispose()

    sent = [r for r in cast(FakeSession, bot.session).requests if isinstance(r, SendMessage)]
    assert len(sent) == 1
    text = sent[0].text
    assert text is not None
    assert "1." in text
    assert "2." in text
    assert "3." in text
    assert "Разом" in text

    assert sent[0].reply_markup is not None
    rows = sent[0].reply_markup.inline_keyboard
    assert len(rows) == 3
    assert all(len(row) == 2 for row in rows)

    db_session.expire_all()
    expense_rows = (await db_session.execute(select(Expense).order_by(Expense.id))).scalars().all()
    assert len(expense_rows) == 3
    # `sent[0]` is the outgoing `SendMessage` request, which carries no
    # `message_id` — that only exists on Telegram's *response*.
    # `FakeSession._next_canned_message` hands out ids starting at 1, and
    # this test's session sends exactly one message, so 1 is what
    # `set_bot_message_id` was called with.
    bot_message_ids = {row.bot_message_id for row in expense_rows}
    assert bot_message_ids == {1}


async def test_zero_expenses_sends_the_clarification_reply_with_no_keyboard(
    bot: Bot, db_session: AsyncSession, postgres_url: str
) -> None:
    await _seed_pending_message(db_session, "просто повідомлення без суми")

    engine = create_async_engine(postgres_url, pool_pre_ping=True, json_serializer=json_serializer)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    llm = FakeLlmClient(_load_fixture("ok_empty"))
    settings = _settings(postgres_url)

    try:
        await _run_one_drain_tick(bot=bot, sessionmaker=sessionmaker, llm=llm, settings=settings)
    finally:
        await engine.dispose()

    sent = [r for r in cast(FakeSession, bot.session).requests if isinstance(r, SendMessage)]
    assert len(sent) == 1
    assert sent[0].text == NO_EXPENSE_REPLY
    assert sent[0].reply_markup is None

    db_session.expire_all()
    expense_rows = (await db_session.execute(select(Expense))).scalars().all()
    assert expense_rows == []
