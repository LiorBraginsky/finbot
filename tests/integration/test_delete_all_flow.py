"""Integration tests for the `🗑 Видалити все` callback (docs/plans/
stage-2_5-bank-screenshots.md, Step 3, Approach D2), routed through the real
`Dispatcher` exactly like `tests/integration/test_callback_flow.py`'s ✏️/🗑
tests: `FakeSession` stands in for the transport; `build_dispatcher` is the
same factory `main.py` calls.
"""

from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from typing import cast

import pytest
import pytest_asyncio
from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageText
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.adapters.telegram.callbacks import MessageAction
from finbot.adapters.telegram.main import build_dispatcher
from finbot.core.models import IncomingMessage, MessageKind
from finbot.repo import categories, expenses, messages, users
from finbot.repo.engine import create_sessionmaker
from finbot.repo.models import Correction, Expense
from tests.support.fake_session import FakeSession
from tests.support.updates import ALLOWED_USER_ID, STRANGER_USER_ID, callback_update

_OCCURRED_AT = date(2026, 8, 24)


@pytest_asyncio.fixture
async def dispatcher(postgres_url: str) -> AsyncIterator[Dispatcher]:
    sessionmaker = create_sessionmaker(postgres_url)
    yield build_dispatcher(sessionmaker, frozenset({ALLOWED_USER_ID}))


@pytest.fixture
def bot() -> Bot:
    return Bot(token="42:TESTTOKEN", session=FakeSession())


async def _seed_bank_expenses(
    session: AsyncSession, *, rows: int = 3
) -> tuple[int, list[int], int]:
    """A `done`, PHOTO-kind `messages` row with `rows` bank expenses hanging
    off it, each under its own dedup key — the shape one drained screenshot
    actually leaves behind (Step 2's `repo.expenses.create_bank_row`).
    Returns `(message_id, expense_ids, user_id)`.
    """
    incoming = IncomingMessage(
        telegram_update_id=1_000_000 + rows,
        telegram_message_id=1,
        chat_id=-1001111111111,
        telegram_user_id=ALLOWED_USER_ID,
        display_name="Alice",
        kind=MessageKind.PHOTO,
        raw_text=None,
        file_id="bank-photo-1",
    )
    user_id = await users.get_or_create(session, incoming.telegram_user_id, incoming.display_name)
    message_id = await messages.add_if_new(session, incoming, user_id)
    assert message_id is not None
    category_ids = await categories.by_slug(session)

    expense_ids: list[int] = []
    for i in range(rows):
        amount = Decimal(f"{100 + i}.00")
        expense_id = await expenses.create_bank_row(
            session,
            message_id=message_id,
            user_id=user_id,
            category_id=category_ids["groceries"],
            item=f"merchant{i}",
            amount=amount,
            occurred_at=_OCCURRED_AT,
            bank_txn_key=f"{_OCCURRED_AT.isoformat()}||{amount:.2f}",
        )
        assert expense_id is not None
        expense_ids.append(expense_id)

    await messages.mark_done(session, message_id)
    await session.commit()
    return message_id, expense_ids, user_id


async def test_delete_all_callback_soft_deletes_every_sibling_and_records_one_correction_each(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    message_id, expense_ids, user_id = await _seed_bank_expenses(db_session, rows=3)
    update = callback_update(6001, MessageAction(action="delall", message_id=message_id).pack())

    await dispatcher.feed_raw_update(bot, update)

    requests = cast(FakeSession, bot.session).requests
    answers = [r for r in requests if isinstance(r, AnswerCallbackQuery)]
    assert len(answers) == 1
    assert answers[0].text == "Видалив усе"

    edits = [r for r in requests if isinstance(r, EditMessageText)]
    assert len(edits) == 1
    # Struck through, no keyboard: every sibling is gone, so there is
    # nothing left to undo (keyboards.confirmation_keyboard returns None
    # once every line is deleted).
    assert edits[0].reply_markup is None

    db_session.expire_all()
    for expense_id in expense_ids:
        expense = await db_session.get(Expense, expense_id)
        assert expense is not None
        assert expense.deleted_at is not None

    corrections = (
        (await db_session.execute(select(Correction).where(Correction.expense_id.in_(expense_ids))))
        .scalars()
        .all()
    )
    assert len(corrections) == 3
    assert {c.corrected_by for c in corrections} == {user_id}
    assert all(c.before == {"deleted_at": None} for c in corrections)


async def test_redelivered_delete_all_callback_is_idempotent(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    message_id, expense_ids, _user_id = await _seed_bank_expenses(db_session, rows=2)
    update = callback_update(6002, MessageAction(action="delall", message_id=message_id).pack())

    await dispatcher.feed_raw_update(bot, update)
    await dispatcher.feed_raw_update(bot, update)  # exact redelivery, same update_id

    requests = cast(FakeSession, bot.session).requests
    answers = [r for r in requests if isinstance(r, AnswerCallbackQuery)]
    # Both taps are answered — a redelivered callback must never leave the
    # client's "loading" spinner running (mirrors test_callback_flow.py's
    # own redelivered-🗑 test).
    assert len(answers) == 2
    assert [a.text for a in answers] == ["Видалив усе", "Видалив усе"]

    db_session.expire_all()
    corrections = (
        (await db_session.execute(select(Correction).where(Correction.expense_id.in_(expense_ids))))
        .scalars()
        .all()
    )
    # The second tap finds every sibling already deleted and writes nothing
    # further — one correction per row, not two.
    assert len(corrections) == 2


async def test_stranger_delete_all_callback_makes_no_api_call_and_no_db_change(
    dispatcher: Dispatcher, bot: Bot, db_session: AsyncSession
) -> None:
    message_id, expense_ids, _user_id = await _seed_bank_expenses(db_session, rows=2)
    update = callback_update(
        6003,
        MessageAction(action="delall", message_id=message_id).pack(),
        user_id=STRANGER_USER_ID,
    )

    await dispatcher.feed_raw_update(bot, update)

    assert cast(FakeSession, bot.session).requests == []

    db_session.expire_all()
    for expense_id in expense_ids:
        expense = await db_session.get(Expense, expense_id)
        assert expense is not None
        assert expense.deleted_at is None
