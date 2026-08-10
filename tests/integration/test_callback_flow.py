"""Integration tests for the confirmation's inline buttons, routed through
the real `Dispatcher` — the Stage-0 bug class made permanent as a test
(docs/plans/stage-1-text-to-expense.md's Reality check): a `callback_query`
used to die at the very first middleware, before any handler or database
write, with no log and no reply. `FakeSession` stands in for the transport;
`build_dispatcher` is the same factory `main.py` calls.
"""

from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from typing import cast

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, EditMessageText
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from finbot.adapters.telegram.callbacks import ExpenseAction, SetCategory
from finbot.adapters.telegram.main import build_dispatcher
from finbot.core.models import IncomingMessage, MessageKind
from finbot.repo import categories, expenses, messages, users
from finbot.repo.models import Correction, Expense
from tests.support.fake_session import FakeSession
from tests.support.updates import ALLOWED_USER_ID, STRANGER_USER_ID, callback_update

_OCCURRED_AT = date(2026, 8, 10)


@pytest_asyncio.fixture
async def dispatcher(postgres_url: str) -> AsyncIterator[Dispatcher]:
    engine = create_async_engine(postgres_url, pool_pre_ping=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield build_dispatcher(sessionmaker, frozenset({ALLOWED_USER_ID}))
    finally:
        await engine.dispose()


@pytest.fixture
def bot() -> Bot:
    return Bot(token="42:TESTTOKEN", session=FakeSession())


async def _seed_expense(
    session: AsyncSession, *, item: str, amount: Decimal, category_slug: str
) -> tuple[int, int, int]:
    """Inserts a user, a `done` message and one expense; returns
    `(message_id, expense_id, user_id)`. `user_id` is `users.id`, the
    internal PK `corrections.corrected_by` is a foreign key to — never the
    raw Telegram user id `ALLOWED_USER_ID` carries. Bypasses extraction
    entirely — this file tests the callback handlers, not the pipeline.
    """
    incoming = IncomingMessage(
        telegram_update_id=abs(hash((item, amount))) % 1_000_000_000,
        telegram_message_id=1,
        chat_id=-1001111111111,
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
        occurred_at=_OCCURRED_AT,
    )
    await messages.mark_done(session, message_id)
    await session.commit()
    return message_id, expense_id, user_id


async def test_delete_callback_soft_deletes_and_records_one_correction(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    _message_id, expense_id, user_id = await _seed_expense(
        db_session, item="таксі", amount=Decimal("200.00"), category_slug="transport"
    )
    update = callback_update(1001, ExpenseAction(action="del", expense_id=expense_id).pack())

    await dispatcher.feed_raw_update(bot, update)

    requests = cast(FakeSession, bot.session).requests
    assert sum(isinstance(r, AnswerCallbackQuery) for r in requests) == 1
    assert sum(isinstance(r, EditMessageText) for r in requests) == 1

    db_session.expire_all()
    expense = await db_session.get(Expense, expense_id)
    assert expense is not None
    assert expense.deleted_at is not None

    corrections = (
        (await db_session.execute(select(Correction).where(Correction.expense_id == expense_id)))
        .scalars()
        .all()
    )
    assert len(corrections) == 1
    assert corrections[0].corrected_by == user_id


async def test_edit_callback_shows_the_thirteen_category_buttons(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    _message_id, expense_id, _user_id = await _seed_expense(
        db_session, item="хліб", amount=Decimal("50.00"), category_slug="groceries"
    )
    update = callback_update(2001, ExpenseAction(action="edit", expense_id=expense_id).pack())

    await dispatcher.feed_raw_update(bot, update)

    requests = cast(FakeSession, bot.session).requests
    assert sum(isinstance(r, AnswerCallbackQuery) for r in requests) == 1
    edits = [r for r in requests if isinstance(r, EditMessageReplyMarkup)]
    assert len(edits) == 1
    assert edits[0].reply_markup is not None

    buttons = [button for row in edits[0].reply_markup.inline_keyboard for button in row]
    category_buttons = [
        b for b in buttons if b.callback_data and b.callback_data.startswith("cat:")
    ]
    back_buttons = [b for b in buttons if b.callback_data == f"exp:back:{expense_id}"]
    assert len(category_buttons) == 13
    assert len(back_buttons) == 1


async def test_set_category_callback_changes_category_and_records_one_correction(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    _message_id, expense_id, user_id = await _seed_expense(
        db_session, item="хліб", amount=Decimal("50.00"), category_slug="groceries"
    )
    category_ids = await categories.by_slug(db_session)
    new_category_id = category_ids["household"]
    update = callback_update(
        3001, SetCategory(expense_id=expense_id, category_id=new_category_id).pack()
    )

    await dispatcher.feed_raw_update(bot, update)

    requests = cast(FakeSession, bot.session).requests
    assert sum(isinstance(r, AnswerCallbackQuery) for r in requests) == 1
    assert sum(isinstance(r, EditMessageText) for r in requests) == 1

    db_session.expire_all()
    expense = await db_session.get(Expense, expense_id)
    assert expense is not None
    assert expense.category_id == new_category_id

    corrections = (
        (await db_session.execute(select(Correction).where(Correction.expense_id == expense_id)))
        .scalars()
        .all()
    )
    assert len(corrections) == 1
    assert corrections[0].corrected_by == user_id


async def test_stranger_callback_makes_no_api_call_and_no_db_change(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    _message_id, expense_id, _user_id = await _seed_expense(
        db_session, item="таксі", amount=Decimal("200.00"), category_slug="transport"
    )
    update = callback_update(
        4001,
        ExpenseAction(action="del", expense_id=expense_id).pack(),
        user_id=STRANGER_USER_ID,
    )

    await dispatcher.feed_raw_update(bot, update)

    assert cast(FakeSession, bot.session).requests == []

    db_session.expire_all()
    expense = await db_session.get(Expense, expense_id)
    assert expense is not None
    assert expense.deleted_at is None

    corrections = (
        (await db_session.execute(select(Correction).where(Correction.expense_id == expense_id)))
        .scalars()
        .all()
    )
    assert corrections == []


async def test_redelivered_delete_callback_is_idempotent(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    _message_id, expense_id, _user_id = await _seed_expense(
        db_session, item="таксі", amount=Decimal("200.00"), category_slug="transport"
    )
    update = callback_update(5001, ExpenseAction(action="del", expense_id=expense_id).pack())

    await dispatcher.feed_raw_update(bot, update)
    await dispatcher.feed_raw_update(bot, update)  # exact redelivery, same update_id

    requests = cast(FakeSession, bot.session).requests
    # Both taps are answered — a redelivered callback must never leave the
    # client's "loading" spinner running.
    assert sum(isinstance(r, AnswerCallbackQuery) for r in requests) == 2

    db_session.expire_all()
    expense = await db_session.get(Expense, expense_id)
    assert expense is not None
    assert expense.deleted_at is not None

    corrections = (
        (await db_session.execute(select(Correction).where(Correction.expense_id == expense_id)))
        .scalars()
        .all()
    )
    assert len(corrections) == 1
