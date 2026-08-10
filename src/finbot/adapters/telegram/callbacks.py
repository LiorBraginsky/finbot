"""`CallbackData` factories for the confirmation's inline buttons.

Both prefixes keep every packed value well inside Telegram's 64-byte
`callback_data` limit — a BigInteger id and a two-letter prefix leave more
than enough room.
"""

from typing import Literal

from aiogram.filters.callback_data import CallbackData


class ExpenseAction(CallbackData, prefix="exp"):
    """Packs as `exp:edit:1234` / `exp:del:1234` / `exp:back:1234`.

    `"back"` is not in the plan's own snippet (docs/plans/stage-1-text-to-
    expense.md 3.3): the category picker's `← Назад` button (3.5) needs
    *some* callback to return to the confirmation, and reusing this factory
    — rather than inventing a fourth `CallbackData` type for one button —
    keeps the picker's "go back" and the confirmation's "delete" sharing one
    packed shape.
    """

    action: Literal["edit", "del", "back"]
    expense_id: int


class SetCategory(CallbackData, prefix="cat"):
    """Packs as `cat:1234:7`."""

    expense_id: int
    category_id: int
