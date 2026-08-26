"""Unit tests for finbot.adapters.telegram.render.render_bank_note. No
Docker, no network — `BankSummary`/`BankPlan` are built by hand here, never
through the pipeline (that's tests/integration/test_bank_flow.py's job).
"""

from datetime import date
from decimal import Decimal

from finbot.adapters.telegram.render import NOT_A_BANK_FEED_REPLY, render_bank_note
from finbot.core.extraction.bank import BankPlan, BankWrite
from finbot.core.extraction.pipeline import BankSummary
from finbot.core.extraction.schema import BankRowKind, ExpenseDraft
from finbot.repo.expenses import ExpenseView

_ANCHOR = date(2026, 8, 24)


def _write(item: str = "Silpo", amount: str = "100.00") -> BankWrite:
    draft = ExpenseDraft(
        item=item, amount=Decimal(amount), category="groceries", occurred_at=_ANCHOR
    )
    return BankWrite(draft=draft, key=f"{_ANCHOR.isoformat()}||{amount}")


def _duplicate(item: str, amount: str, *, occurred_at: date | None = _ANCHOR) -> ExpenseDraft:
    return ExpenseDraft(
        item=item, amount=Decimal(amount), category="groceries", occurred_at=occurred_at
    )


def _collision(item: str, amount: str, *, occurred_at: date = _ANCHOR, id_: int = 1) -> ExpenseView:
    return ExpenseView(
        id=id_,
        item=item,
        amount=Decimal(amount),
        category_slug="dining_out",
        occurred_at=occurred_at,
        deleted=False,
    )


def test_clean_screenshot_reports_only_the_header_and_the_written_count() -> None:
    plan = BankPlan(anchor=_ANCHOR, writes=(_write(),))
    summary = BankSummary(plan=plan)

    note = render_bank_note(summary, anchor=_ANCHOR, written=1)

    assert note == "🧾 Скріншот за 24.08 — дати рахував від цього дня.\nЗаписав: 1 (нижче)."


def test_every_skip_reason_at_once_matches_the_planned_wording_exactly() -> None:
    """Golden test: reproduces `## Chosen approach`'s own worked example in
    the plan, character for character.
    """
    plan = BankPlan(
        anchor=_ANCHOR,
        writes=(_write(), _write(), _write(), _write(), _write()),
        skipped_by_kind={
            BankRowKind.SAVINGS: 2,
            BankRowKind.OWN_TRANSFER: 1,
            BankRowKind.TRANSFER_OUT: 1,
            BankRowKind.INCOME: 1,
        },
        cut_off=1,
        unresolved_date=1,
    )
    summary = BankSummary(
        plan=plan,
        duplicates=(_duplicate("Multiplex", "320.00"),),
        manual_collisions=(_collision("кава", "150.00"),),
    )

    note = render_bank_note(summary, anchor=_ANCHOR, written=4)

    assert note == (
        "🧾 Скріншот за 24.08 — дати рахував від цього дня.\n"
        "Записав: 4 (нижче).\n"
        "Пропустив: скарбничка 2, переказ собі 1, переказ 1, надходження 1.\n"
        "Обрізано на краю: 1 — не вгадував.\n"
        "Вже було: 1.\n"
        "  · Multiplex — 320.00 ₴ за 24.08\n"
        "Не зрозумів дату: 1.\n"
        "⚠️ Можливий дубль: «кава» 150.00 за 24.08 уже записано вручну."
    )


def test_zero_written_rows_omits_the_written_line_and_its_нижче_reference() -> None:
    """Nothing was recorded — everything on the screenshot was a savings
    jar — so there is no confirmation below this note at all, and saying
    "нижче" would point at nothing.
    """
    plan = BankPlan(anchor=_ANCHOR, skipped_by_kind={BankRowKind.SAVINGS: 2})
    summary = BankSummary(plan=plan)

    note = render_bank_note(summary, anchor=_ANCHOR, written=0)

    assert "Записав" not in note
    assert "нижче" not in note
    assert note == ("🧾 Скріншот за 24.08 — дати рахував від цього дня.\nПропустив: скарбничка 2.")


def test_wholly_empty_plan_is_not_a_bank_feed_reply() -> None:
    """Covers both readings at once (see `NOT_A_BANK_FEED_REPLY`'s own
    docstring): `is_transaction_feed: false` and a genuinely empty feed both
    leave `BankPlan`/`BankSummary` with nothing to report.
    """
    summary = BankSummary(plan=BankPlan(anchor=_ANCHOR))

    assert render_bank_note(summary, anchor=_ANCHOR, written=0) == NOT_A_BANK_FEED_REPLY


def test_bad_amount_and_unclassified_each_get_their_own_line() -> None:
    plan = BankPlan(anchor=_ANCHOR, bad_amount=2, unclassified=3)
    summary = BankSummary(plan=plan)

    note = render_bank_note(summary, anchor=_ANCHOR, written=0)

    assert "Не розібрав суму: 2." in note
    assert "Не визначив тип: 3." in note


def test_more_than_five_manual_collisions_are_capped_with_a_count_of_the_rest() -> None:
    collisions = tuple(_collision(f"item{i}", "10.00", id_=i) for i in range(1, 8))
    summary = BankSummary(
        plan=BankPlan(anchor=_ANCHOR, writes=(_write(),)), manual_collisions=collisions
    )

    note = render_bank_note(summary, anchor=_ANCHOR, written=1)
    lines = note.splitlines()
    warning_lines = [line for line in lines if line.startswith("⚠️")]

    assert len(warning_lines) == 5
    assert lines[-1] == "…і ще 2."


def test_note_stays_well_under_telegrams_message_limit_for_a_worst_case_feed() -> None:
    """A 20-row feed with every counter maxed out and 20 manual collisions
    must still fit comfortably under Telegram's 4096-character limit — the
    same bound `transcript_line` protects for voice.
    """
    plan = BankPlan(
        anchor=_ANCHOR,
        writes=tuple(_write() for _ in range(8)),
        skipped_by_kind={
            BankRowKind.SAVINGS: 5,
            BankRowKind.OWN_TRANSFER: 5,
            BankRowKind.TRANSFER_OUT: 5,
            BankRowKind.INCOME: 5,
        },
        cut_off=20,
        unresolved_date=20,
        bad_amount=20,
        unclassified=20,
    )
    collisions = tuple(_collision(f"item{i}", "9999.99", id_=i) for i in range(1, 21))
    duplicates = tuple(_duplicate(f"dup{i}", "9999.99") for i in range(1, 21))
    summary = BankSummary(plan=plan, duplicates=duplicates, manual_collisions=collisions)

    note = render_bank_note(summary, anchor=_ANCHOR, written=8)

    assert len(note) < 1000


def test_a_resent_screenshot_promises_nothing_below_and_names_what_it_suppressed() -> None:
    """The exact live defect this parameter exists for: every planned write
    was rejected by the unique index, so `runner._send_bank_reply` returns
    without sending a confirmation. The note must not say "нижче" — and it
    must name the two rows it suppressed, so a wrongly-suppressed one is
    visible rather than silently absent.

    Pins that `written` is not `len(plan.writes)`: that reading would print
    "Записав: 2 (нижче)" here, pointing at a message that does not exist.
    """
    plan = BankPlan(
        anchor=_ANCHOR,
        writes=(_write(), _write()),
        skipped_by_kind={BankRowKind.SAVINGS: 4, BankRowKind.OWN_TRANSFER: 1},
    )
    summary = BankSummary(
        plan=plan,
        duplicates=(
            _duplicate("Multiplex", "320.00", occurred_at=date(2026, 8, 23)),
            _duplicate("Twitch", "43.19", occurred_at=date(2026, 8, 22)),
        ),
    )

    note = render_bank_note(summary, anchor=date(2026, 8, 25), written=0)

    assert note == (
        "🧾 Скріншот за 25.08 — дати рахував від цього дня.\n"
        "Пропустив: скарбничка 4, переказ собі 1.\n"
        "Вже було: 2.\n"
        "  · Multiplex — 320.00 ₴ за 23.08\n"
        "  · Twitch — 43.19 ₴ за 22.08"
    )
    assert "нижче" not in note


def test_more_than_five_duplicates_are_capped_with_a_count_of_the_rest() -> None:
    duplicates = tuple(_duplicate(f"dup{i}", "10.00") for i in range(1, 9))
    summary = BankSummary(plan=BankPlan(anchor=_ANCHOR), duplicates=duplicates)

    note = render_bank_note(summary, anchor=_ANCHOR, written=0)
    listed = [line for line in note.splitlines() if line.startswith("  · ")]

    assert len(listed) == 6
    assert listed[-1] == "  · …і ще 3."


def test_a_duplicate_without_a_resolved_date_still_gets_a_line() -> None:
    """`ExpenseDraft.occurred_at` is `date | None` on the type even though
    `bank.plan_writes` never emits `None` — the renderer must not crash if
    that contract is ever loosened, so the date clause is conditional.
    """
    summary = BankSummary(
        plan=BankPlan(anchor=_ANCHOR),
        duplicates=(_duplicate("Silpo", "100.00", occurred_at=None),),
    )

    note = render_bank_note(summary, anchor=_ANCHOR, written=0)

    assert "  · Silpo — 100.00 ₴" in note
