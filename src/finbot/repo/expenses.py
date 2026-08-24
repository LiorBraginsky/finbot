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

from sqlalchemy import select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
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


async def create_bank_row(
    session: AsyncSession,
    *,
    message_id: int,
    user_id: int,
    category_id: int,
    item: str,
    amount: Decimal,
    occurred_at: date,
    bank_txn_key: str,
) -> int | None:
    """Insert one bank-feed row, or do nothing if `(user_id, bank_txn_key)`
    already exists — the database-enforced half of Approach C2's dedup
    guarantee (`uq_expenses_user_bank_txn_key`, `repo.models.Expense`).

    Returns the new row's id, or `None` when the insert conflicted — that
    `None` *is* the "already recorded" counter (R8), exactly like
    `repo.messages.add_if_new`'s own `None` return for a redelivered update.
    Never a `SELECT`-then-filter: ADR-0012's reasoning is that this project
    tests against a real Postgres precisely so a unique index can be relied
    on directly, not re-derived in application code.

    Same columns and values as `create()` otherwise (UAH, `fx_rate=1`,
    `fx_rate_date=occurred_at`) — a bank row is truth after the model's
    classification exactly like a text or voice one, and Stage 1.5's FX
    change applies to both the same way.
    """
    stmt = (
        insert(Expense)
        .values(
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
            bank_txn_key=bank_txn_key,
        )
        .on_conflict_do_nothing(index_elements=[Expense.user_id, Expense.bank_txn_key])
        .returning(Expense.id)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def manual_duplicate_candidates(
    session: AsyncSession, pairs: Sequence[tuple[date, Decimal]], *, user_id: int
) -> list[ExpenseView]:
    """Non-deleted, non-bank expenses (`bank_txn_key IS NULL`) belonging to
    `user_id` whose `(occurred_at, amount)` matches one of `pairs` — the
    screenshot<->manual collision Approach C2's key cannot itself catch,
    since a manually typed expense carries no key at all. R7: named in the
    reply, never merged and never suppressed — both rows keep existing, and
    the human resolves it with 🗑. `pairs` is expected to be the handful of
    rows one screenshot actually wrote, not a manual duplicate scan over the
    whole table.

    Scoped to `user_id` for the same reason `bank_txn_key`'s own uniqueness
    is per user (ADR-0018 §6): without it, one household member's screenshot
    reports the *other* member's manually typed expense as a possible
    duplicate, which is not a collision either of them can actually resolve
    — a coincidence of two different people's spending, not a duplicate.
    """
    if not pairs:
        return []
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
        .where(
            Expense.user_id == user_id,
            Expense.deleted_at.is_(None),
            Expense.bank_txn_key.is_(None),
            tuple_(Expense.occurred_at, Expense.amount).in_(pairs),
        )
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
