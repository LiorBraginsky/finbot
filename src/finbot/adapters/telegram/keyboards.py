"""Inline keyboards for the confirmation message and the category picker."""

from collections.abc import Mapping, Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from finbot.adapters.telegram.callbacks import ExpenseAction, MessageAction, SetCategory
from finbot.adapters.telegram.render import CATEGORY_LABELS, ConfirmationLine
from finbot.core.categories.catalog import CategorySpec

# Beyond this many rows the confirmation is shown without per-row buttons
# rather than with a keyboard Telegram may reject; only a Stage-4 receipt or
# a bank screenshot with many line items can reach it
# (docs/plans/stage-1-text-to-expense.md 3.5). Not raised for Stage 2.5
# (docs/plans/stage-2_5-bank-screenshots.md, Approach D): Telegram's own hard
# limit on keyboard rows is undocumented, and this constant's own comment
# already hedges against guessing at it.
MAX_CONFIRMATION_ROWS = 12

_CATEGORY_COLUMNS = 3
_DELETE_ALL_LABEL = "🗑 Видалити все"


def confirmation_keyboard(
    lines: Sequence[ConfirmationLine], *, delete_all_message_id: int | None = None
) -> InlineKeyboardMarkup | None:
    """One row of ✏️/🗑 per still-active line, numbered by `line.index` — its
    fixed, original position, never renumbered after a sibling is deleted
    (see `ConfirmationLine`). A deleted line contributes no row: it has
    already lost its buttons.

    `delete_all_message_id`, given only on the bank path (Approach D2), adds
    one more row — `🗑 Видалити все`, packing `messages.id` via
    `MessageAction` — appended after the per-row buttons, or **kept as the
    only row** once `len(active)` exceeds `MAX_CONFIRMATION_ROWS`: a batch of
    real money written unattended must stay one-tap-undoable even when it is
    too large to correct row by row (R9). `None` (text and voice, and every
    existing caller) reproduces today's behaviour byte for byte — the cap
    still returns `None` outright above `MAX_CONFIRMATION_ROWS`, and no row
    is ever added.
    """
    active = [line for line in lines if not line.deleted]
    if not active:
        return None

    delete_all_row = (
        [
            InlineKeyboardButton(
                text=_DELETE_ALL_LABEL,
                callback_data=MessageAction(
                    action="delall", message_id=delete_all_message_id
                ).pack(),
            )
        ]
        if delete_all_message_id is not None
        else None
    )

    if len(active) > MAX_CONFIRMATION_ROWS:
        if delete_all_row is None:
            return None
        return InlineKeyboardMarkup(inline_keyboard=[delete_all_row])

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
    if delete_all_row is not None:
        rows.append(delete_all_row)
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
