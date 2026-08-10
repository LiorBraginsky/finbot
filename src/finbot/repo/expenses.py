"""Persistence for finbot.repo.models.Expense — the truth after corrections
(ADR-0006).

None of these functions commit; the caller decides the transaction boundary.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from finbot.repo.models import Expense


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
