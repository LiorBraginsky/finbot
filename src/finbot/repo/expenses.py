"""Persistence for finbot.repo.models.Expense — the truth after corrections
(ADR-0006).

None of these functions commit; the caller decides the transaction boundary.

Not in Step 3's own file list (docs/plans/stage-1-text-to-expense.md lists
only `repo/{reports,corrections}.py` as new), but the callback handlers
cannot soft-delete, re-category or group siblings without it — an omission
in the plan's file list, not a decision to route those queries elsewhere.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.repo.models import Category, Expense


@dataclass(frozen=True)
class ExpenseView:
    """One expense as the Telegram adapter needs to render it: the category
    *slug*, not its numeric id, so `adapters/telegram/render.py` never has
    to know about `categories.id`.
    """

    id: int
    item: str
    amount: Decimal
    category_slug: str
    occurred_at: date
    deleted: bool


async def create(
    session: AsyncSession,
    *,
    message_id: int,
    user_id: int,
    category_id: int,
    item: str,
    amount: Decimal,
    occurred_at: date,
) -> int:
    """Insert one `expenses` row, in UAH, and return its id.

    `amount_uah = amount`, `fx_rate = 1`, `fx_rate_date = occurred_at`:
    Stage 1.5 changes these *values* once FX conversion lands, never these
    columns (docs/plans/stage-1-text-to-expense.md).
    """
    expense = Expense(
        message_id=message_id,
        user_id=user_id,
        category_id=category_id,
        item=item,
        amount=amount,
        currency="UAH",
        amount_uah=amount,
        fx_rate=Decimal("1"),
        fx_rate_date=occurred_at,
        occurred_at=occurred_at,
    )
    session.add(expense)
    await session.flush()
    return expense.id


async def get(session: AsyncSession, expense_id: int) -> Expense | None:
    return await session.get(Expense, expense_id)


async def siblings(session: AsyncSession, message_id: int) -> list[ExpenseView]:
    """Every expense that came from one message, oldest first — the order
    the model returned them in, and therefore the order the original
    confirmation numbered them. Handlers group by `message_id`, never
    `bot_message_id` — see handlers.py's note on why.
    """
    stmt = (
        select(
            Expense.id,
            Expense.item,
            Expense.amount,
            Category.name.label("category_slug"),
            Expense.occurred_at,
            Expense.deleted_at,
        )
        .join(Category, Category.id == Expense.category_id)
        .where(Expense.message_id == message_id)
        .order_by(Expense.id)
    )
    rows = (await session.execute(stmt)).all()
    return [
        ExpenseView(
            id=row.id,
            item=row.item,
            amount=row.amount,
            category_slug=row.category_slug,
            occurred_at=row.occurred_at,
            deleted=row.deleted_at is not None,
        )
        for row in rows
    ]


async def soft_delete(session: AsyncSession, expense_id: int) -> None:
    """Sets `deleted_at`. Idempotent: deleting an already-deleted expense is
    a no-op `UPDATE`, which is what makes a redelivered 🗑 tap harmless.
    """
    await session.execute(
        update(Expense)
        .where(Expense.id == expense_id, Expense.deleted_at.is_(None))
        .values(deleted_at=datetime.now(UTC))
    )


async def set_category(session: AsyncSession, expense_id: int, category_id: int) -> None:
    await session.execute(
        update(Expense).where(Expense.id == expense_id).values(category_id=category_id)
    )


async def set_bot_message_id(
    session: AsyncSession, expense_ids: Sequence[int], bot_message_id: int
) -> None:
    """Stamps every expense from one processing round with the confirmation
    message that describes it — ADR-0007: write, then reply, so a crash
    before this runs loses only provenance, never the expense itself.
    """
    if not expense_ids:
        return
    await session.execute(
        update(Expense).where(Expense.id.in_(expense_ids)).values(bot_message_id=bot_message_id)
    )
