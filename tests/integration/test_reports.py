"""Integration tests for `/day` `/week` `/month`, routed through the real
`Dispatcher` — CLAUDE.md rule 5: reports are SQL, and this proves the whole
path from a command to a reply built entirely from `repo/reports.py`, with
no model anywhere near it.

The report handler computes `today` from the wall clock
(`datetime.now(tz=settings.tz)`) rather than taking it as a parameter, since
unlike extraction's prompt-building `today` there is nothing here to replay
deterministically — a report answers "what happened up to right now". Every
date below is therefore computed *relative to the real current date*, never
a literal like `date(2026, 8, 10)`: a hardcoded date is exactly the "clock
bomb that turns green into red next August" the plan's evals section warns
against, and it applies here just as much as to a committed fixture.
"""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.methods import SendMessage
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from finbot.adapters.telegram.main import build_dispatcher
from finbot.adapters.telegram.render import EMPTY_REPORT_REPLY
from finbot.core.models import IncomingMessage, MessageKind
from finbot.repo import categories, expenses, messages, users
from tests.support.fake_session import FakeSession
from tests.support.ids import stable_update_id
from tests.support.updates import ALLOWED_USER_ID, CHAT_ID, text_update


def _today() -> date:
    # Matches the `tz=UTC` the `dispatcher` fixture below builds the report
    # handler with, so this is exactly the `today` it will compute.
    return datetime.now(tz=UTC).date()


@pytest_asyncio.fixture
async def dispatcher(postgres_url: str) -> AsyncIterator[Dispatcher]:
    engine = create_async_engine(postgres_url, pool_pre_ping=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield build_dispatcher(sessionmaker, frozenset({ALLOWED_USER_ID}), ZoneInfo("UTC"))
    finally:
        async with sessionmaker() as session:
            await session.execute(
                text(
                    "TRUNCATE expenses, corrections, extractions, messages RESTART IDENTITY CASCADE"
                )
            )
            await session.execute(text("DELETE FROM users"))
            await session.commit()
        await engine.dispose()


@pytest.fixture
def bot() -> Bot:
    return Bot(token="42:TESTTOKEN", session=FakeSession())


async def _seed(
    session: AsyncSession, *, item: str, amount: Decimal, category_slug: str, occurred_at: date
) -> int:
    incoming = IncomingMessage(
        telegram_update_id=stable_update_id(item, amount, occurred_at),
        telegram_message_id=1,
        chat_id=CHAT_ID,
        telegram_user_id=ALLOWED_USER_ID,
        display_name="Alice",
        kind=MessageKind.TEXT,
        raw_text=item,
        file_id=None,
    )
    user_id = await users.get_or_create(session, incoming.telegram_user_id, incoming.display_name)
    message_id = await messages.add_if_new(session, incoming, user_id)
    assert message_id is not None
    category_ids = await categories.by_slug(session)
    expense_id = await expenses.create(
        session,
        message_id=message_id,
        user_id=user_id,
        category_id=category_ids[category_slug],
        item=item,
        amount=amount,
        occurred_at=occurred_at,
    )
    await messages.mark_done(session, message_id)
    await session.commit()
    return expense_id


def _sent_text(bot: Bot) -> str:
    sent = [r for r in cast(FakeSession, bot.session).requests if isinstance(r, SendMessage)]
    assert len(sent) == 1
    text_value = sent[0].text
    assert text_value is not None
    return text_value


async def test_day_totals_only_todays_spending(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    today = _today()
    await _seed(
        db_session,
        item="хліб",
        amount=Decimal("50.00"),
        category_slug="groceries",
        occurred_at=today,
    )
    await _seed(
        db_session,
        item="таксі",
        amount=Decimal("200.00"),
        category_slug="transport",
        occurred_at=today - timedelta(days=1),
    )

    await dispatcher.feed_raw_update(bot, text_update(update_id=1001, text="/day"))

    reply = _sent_text(bot)
    assert "50.00" in reply
    assert "200.00" not in reply


async def test_week_totals_from_monday_through_today(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    today = _today()
    monday = today - timedelta(days=today.weekday())
    before_this_week = monday - timedelta(days=1)  # last Sunday, always outside [monday, today]

    await _seed(
        db_session,
        item="хліб",
        amount=Decimal("50.00"),
        category_slug="groceries",
        occurred_at=today,
    )
    await _seed(
        db_session,
        item="кава",
        amount=Decimal("65.00"),
        category_slug="dining_out",
        occurred_at=before_this_week,
    )

    await dispatcher.feed_raw_update(bot, text_update(update_id=2001, text="/week"))

    reply = _sent_text(bot)
    assert "50.00" in reply
    assert "65.00" not in reply


async def test_month_totals_from_the_1st_through_today(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    today = _today()
    first_of_month = today.replace(day=1)
    last_month = first_of_month - timedelta(days=1)  # always the prior calendar month

    await _seed(
        db_session,
        item="хліб",
        amount=Decimal("50.00"),
        category_slug="groceries",
        occurred_at=first_of_month,
    )
    await _seed(
        db_session,
        item="оренда",
        amount=Decimal("1000.00"),
        category_slug="housing",
        occurred_at=last_month,
    )

    await dispatcher.feed_raw_update(bot, text_update(update_id=3001, text="/month"))

    reply = _sent_text(bot)
    assert "50.00" in reply
    assert "1000.00" not in reply


async def test_a_soft_deleted_expense_is_excluded(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    expense_id = await _seed(
        db_session,
        item="таксі",
        amount=Decimal("200.00"),
        category_slug="transport",
        occurred_at=_today(),
    )
    await expenses.soft_delete(db_session, expense_id)
    await db_session.commit()

    await dispatcher.feed_raw_update(bot, text_update(update_id=4001, text="/day"))

    reply = _sent_text(bot)
    assert reply == EMPTY_REPORT_REPLY


async def test_an_empty_period_returns_the_empty_state_text(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    await dispatcher.feed_raw_update(bot, text_update(update_id=5001, text="/day"))

    assert _sent_text(bot) == EMPTY_REPORT_REPLY
