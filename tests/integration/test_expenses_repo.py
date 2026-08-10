"""Rule 2 (`CLAUDE.md`), executable: `numeric` money round-trips through
Postgres as `Decimal`, never `float`. The Stage-1 analogue of Stage 0's enum
round-trip test (`test_message_repo.py::test_kind_round_trips_as_lowercase_value`).

Requires a real Postgres (see tests/conftest.py). No skipif on Docker
availability.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.core.models import IncomingMessage, MessageKind
from finbot.repo import messages, users
from finbot.repo.models import Category, Expense


async def _seed_user_message_and_category(session: AsyncSession) -> tuple[int, int, int]:
    user_id = await users.get_or_create(session, telegram_user_id=333333333, display_name="Bob")
    message = IncomingMessage(
        telegram_update_id=9001,
        telegram_message_id=1,
        chat_id=-1001111111111,
        telegram_user_id=333333333,
        display_name="Bob",
        kind=MessageKind.TEXT,
        raw_text="хліб 1234567.89",
        file_id=None,
    )
    message_id = await messages.add_if_new(session, message, user_id)
    assert message_id is not None

    category_id = await session.scalar(select(Category.id).where(Category.name == "other"))
    assert category_id is not None

    await session.commit()
    return user_id, message_id, category_id


async def test_amount_round_trips_as_decimal_not_float(db_session: AsyncSession) -> None:
    user_id, message_id, category_id = await _seed_user_message_and_category(db_session)

    expense = Expense(
        message_id=message_id,
        user_id=user_id,
        category_id=category_id,
        item="хліб",
        amount=Decimal("1234567.89"),
        currency="UAH",
        amount_uah=Decimal("1234567.89"),
        fx_rate=Decimal("1"),
        fx_rate_date=date(2026, 8, 10),
        occurred_at=date(2026, 8, 10),
    )
    db_session.add(expense)
    await db_session.commit()

    row = await db_session.scalar(select(Expense).where(Expense.id == expense.id))
    assert row is not None
    assert isinstance(row.amount, Decimal)
    assert row.amount == Decimal("1234567.89")


async def test_currency_round_trips_as_exactly_uah(db_session: AsyncSession) -> None:
    user_id, message_id, category_id = await _seed_user_message_and_category(db_session)

    expense = Expense(
        message_id=message_id,
        user_id=user_id,
        category_id=category_id,
        item="таксі",
        amount=Decimal("200.00"),
        currency="UAH",
        amount_uah=Decimal("200.00"),
        fx_rate=Decimal("1"),
        fx_rate_date=date(2026, 8, 10),
        occurred_at=date(2026, 8, 10),
    )
    db_session.add(expense)
    await db_session.commit()

    # Raw SQL, deliberately: CHAR(3) blank-pads shorter values, and a padded
    # comparison against a Python str is a classic silent failure — assert
    # what is actually stored on disk, not what the ORM decodes it back into.
    raw_currency = await db_session.scalar(
        text("SELECT currency FROM expenses WHERE id = :id").bindparams(id=expense.id)
    )
    assert raw_currency == "UAH"


async def test_message_status_round_trips_as_lowercase_pending(db_session: AsyncSession) -> None:
    _, message_id, _ = await _seed_user_message_and_category(db_session)

    # Raw SQL, deliberately: the ORM column would decode the stored VARCHAR
    # back into a MessageStatus member before we could see it. This asserts
    # what is actually stored on disk (see values_callable on the Enum).
    raw_status = await db_session.scalar(
        text("SELECT status FROM messages WHERE id = :id").bindparams(id=message_id)
    )
    assert raw_status == "pending"
