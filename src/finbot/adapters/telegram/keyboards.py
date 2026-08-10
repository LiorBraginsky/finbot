"""Inline keyboards for the confirmation message and the category picker."""

from collections.abc import Mapping, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from finbot.adapters.telegram.callbacks import ExpenseAction, SetCategory
from finbot.adapters.telegram.render import CATEGORY_LABELS, ConfirmationLine
from finbot.core.categories.catalog import CategorySpec

# Beyond this many rows the confirmation is shown without buttons rather than
# with a keyboard Telegram may reject; only a Stage-4 receipt with many line
# items can reach it (docs/plans/stage-1-text-to-expense.md 3.5).
MAX_CONFIRMATION_ROWS = 12

_CATEGORY_COLUMNS = 3


def confirmation_keyboard(lines: Sequence[ConfirmationLine]) -> InlineKeyboardMarkup | None:
    """One row of ✏️/🗑 per still-active line, numbered by `line.index` — its
    fixed, original position, never renumbered after a sibling is deleted
    (see `ConfirmationLine`). A deleted line contributes no row: it has
    already lost its buttons.
    """
    active = [line for line in lines if not line.deleted]
    if len(active) > MAX_CONFIRMATION_ROWS:
        return None
    if not active:
        return None

    rows = [
        [
            InlineKeyboardButton(
                text=f"✏️ {line.index}",
                callback_data=ExpenseAction(action="edit", expense_id=line.expense_id).pack(),
            ),
            InlineKeyboardButton(
                text=f"🗑 {line.index}",
                callback_data=ExpenseAction(action="del", expense_id=line.expense_id).pack(),
            ),
        ]
        for line in active
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def category_keyboard(
    expense_id: int,
    catalog: Sequence[CategorySpec],
    category_ids: Mapping[str, int],
) -> InlineKeyboardMarkup:
    """The one-tap category picker (Approach D): three buttons per row, plus
    a `← Назад` row that returns to the confirmation.
    """
    buttons = [
        InlineKeyboardButton(
            text=f"{category.emoji} {CATEGORY_LABELS[category.slug]}",
            callback_data=SetCategory(
                expense_id=expense_id, category_id=category_ids[category.slug]
            ).pack(),
        )
        for category in catalog
    ]
    rows = [
        buttons[start : start + _CATEGORY_COLUMNS]
        for start in range(0, len(buttons), _CATEGORY_COLUMNS)
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="← Назад",
                callback_data=ExpenseAction(action="back", expense_id=expense_id).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
