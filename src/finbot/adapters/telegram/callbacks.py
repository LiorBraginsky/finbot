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


class MessageAction(CallbackData, prefix="msg"):
    """Packs as `msg:delall:1234` — `1234` is `messages.id`, the internal
    inbox row a bank screenshot became (docs/plans/stage-2_5-bank-
    screenshots.md, Step 3), never `bot_message_id`: `expenses.message_id`
    is the FK `repo.expenses.siblings` groups by (see `handlers.py`'s note
    on why), so `🗑 Видалити все` can act on every row from one screenshot
    regardless of how many times its confirmation has been re-rendered
    since.

    A second prefix rather than a fourth action on `ExpenseAction`: this
    button carries a `message_id`, not an `expense_id` — packing both shapes
    into one `CallbackData` would make every existing `exp:` tap parse an
    `expense_id` that, for this action, would not exist.
    """

    action: Literal["delall"]
    message_id: int
