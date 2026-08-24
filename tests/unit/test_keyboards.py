"""Unit tests for finbot.adapters.telegram.keyboards. No Docker, no network.

Not present before Stage 2.5 (docs/plans/stage-2_5-bank-screenshots.md, Step
3): `confirmation_keyboard` was previously exercised only indirectly, through
`tests/integration/test_confirmation_flow.py` and `test_callback_flow.py`.
This file is new, added to pin `delete_all_message_id`'s two properties
byte-precisely: text/voice output stays identical to before it existed, and
the `🗑 Видалити все` row survives exactly where Approach D2 needs it to —
including past `MAX_CONFIRMATION_ROWS`, where every per-row button drops.
"""

from datetime import date
from decimal import Decimal

from finbot.adapters.telegram.callbacks import ExpenseAction, MessageAction
from finbot.adapters.telegram.keyboards import MAX_CONFIRMATION_ROWS, confirmation_keyboard
from finbot.adapters.telegram.render import ConfirmationLine

_TODAY = date(2026, 8, 24)


def _line(index: int, *, deleted: bool = False) -> ConfirmationLine:
    return ConfirmationLine(
        index=index,
        expense_id=100 + index,
        item=f"item{index}",
        amount=Decimal("10.00"),
        category_slug="groceries",
        occurred_at=_TODAY,
        deleted=deleted,
    )


def test_three_lines_with_no_delete_all_id_produce_three_edit_delete_rows() -> None:
    lines = [_line(1), _line(2), _line(3)]

    keyboard = confirmation_keyboard(lines)

    assert keyboard is not None
    assert len(keyboard.inline_keyboard) == 3
    assert all(len(row) == 2 for row in keyboard.inline_keyboard)


def test_thirteen_lines_with_no_delete_all_id_return_none_unchanged() -> None:
    """Pins today's behaviour byte for byte: `delete_all_message_id`
    defaults to `None`, so text and voice callers are wholly unaffected by
    Approach D2 — the cap still means *no keyboard at all* for them.
    """
    lines = [_line(i) for i in range(1, MAX_CONFIRMATION_ROWS + 2)]

    assert confirmation_keyboard(lines) is None


def test_three_lines_with_delete_all_id_add_one_more_row() -> None:
    lines = [_line(1), _line(2), _line(3)]

    keyboard = confirmation_keyboard(lines, delete_all_message_id=555)

    assert keyboard is not None
    rows = keyboard.inline_keyboard
    assert len(rows) == 4
    assert all(len(row) == 2 for row in rows[:3])

    delete_all_row = rows[3]
    assert len(delete_all_row) == 1
    assert delete_all_row[0].text == "🗑 Видалити все"
    assert delete_all_row[0].callback_data == MessageAction(action="delall", message_id=555).pack()


def test_thirteen_active_lines_with_delete_all_id_keep_only_the_delete_all_row() -> None:
    """R9: a batch too large for per-row buttons must still be one-tap
    undoable — the cap degrades to *no per-row buttons*, never to *no
    keyboard at all*, once `delete_all_message_id` is given.
    """
    lines = [_line(i) for i in range(1, MAX_CONFIRMATION_ROWS + 2)]

    keyboard = confirmation_keyboard(lines, delete_all_message_id=555)

    assert keyboard is not None
    assert len(keyboard.inline_keyboard) == 1
    row = keyboard.inline_keyboard[0]
    assert len(row) == 1
    assert row[0].callback_data == MessageAction(action="delall", message_id=555).pack()


def test_twelve_active_lines_with_delete_all_id_keep_every_per_row_button() -> None:
    """The boundary itself: exactly `MAX_CONFIRMATION_ROWS` active lines is
    still under the cap, so every ✏️/🗑 pair survives alongside the
    delete-all row.
    """
    lines = [_line(i) for i in range(1, MAX_CONFIRMATION_ROWS + 1)]

    keyboard = confirmation_keyboard(lines, delete_all_message_id=555)

    assert keyboard is not None
    assert len(keyboard.inline_keyboard) == MAX_CONFIRMATION_ROWS + 1
    assert all(len(row) == 2 for row in keyboard.inline_keyboard[:-1])
    assert len(keyboard.inline_keyboard[-1]) == 1


def test_no_active_lines_returns_none_even_with_delete_all_id() -> None:
    """Every sibling already deleted (e.g. right after a `delall` tap
    itself, or a redelivery of one) must show no keyboard at all, exactly
    like today's all-deleted text/voice case — there is nothing left to
    undo.
    """
    lines = [_line(1, deleted=True), _line(2, deleted=True)]

    assert confirmation_keyboard(lines, delete_all_message_id=555) is None


def test_edit_and_delete_buttons_are_unaffected_by_delete_all_id() -> None:
    """The per-row ✏️/🗑 buttons pack the same `ExpenseAction` regardless of
    `delete_all_message_id` — Approach D2 only ever adds a row, never
    changes an existing one.
    """
    lines = [_line(1)]

    keyboard = confirmation_keyboard(lines, delete_all_message_id=555)

    assert keyboard is not None
    edit_button, delete_button = keyboard.inline_keyboard[0]
    assert edit_button.callback_data == ExpenseAction(action="edit", expense_id=101).pack()
    assert delete_button.callback_data == ExpenseAction(action="del", expense_id=101).pack()
