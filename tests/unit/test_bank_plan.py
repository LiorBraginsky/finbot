"""Tests for finbot.core.extraction.bank.plan_writes — "the most important
function in the stage" (docs/plans/stage-2_5-bank-screenshots.md, R3/R4):
it decides what becomes real money in `expenses`.

The case table is derived from the decision space, not from the plan's own
illustrative examples: the five wire kinds plus the coerced sixth
(`unclassified`), times the four independent exclusion reasons
(`partially_visible`, `amount <= 0`, an unresolvable date,
`is_transaction_feed: false`) — including the cases that prove *priority*:
a non-expense-kind row that also fails every other check must still be
attributed to its kind, never to `cut_off`/`bad_amount`/`unresolved_date`,
and `is_transaction_feed: false` overrides every row regardless of content.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from finbot.core.extraction import bank
from finbot.core.extraction.bank import BankPlan, bank_txn_key, plan_writes
from finbot.core.extraction.schema import BankExtractionResult, BankRowKind

_ANCHOR = date(2026, 8, 24)


def _row(**overrides: Any) -> dict[str, Any]:
    row = {
        "date_header": "Сьогодні",
        "time": "14:32",
        "merchant": "Silpo",
        "amount": Decimal("100"),
        "kind": "expense",
        "category": "groceries",
        "partially_visible": False,
    }
    row.update(overrides)
    return row


def _plan(*rows: dict[str, Any], is_transaction_feed: bool = True) -> BankPlan:
    result = BankExtractionResult.model_validate(
        {"is_transaction_feed": is_transaction_feed, "rows": list(rows)}
    )
    return plan_writes(result, anchor=_ANCHOR)


# --- The success path: a control that must resolve ------------------------


def test_a_clean_expense_row_produces_exactly_one_draft() -> None:
    plan = _plan(_row())
    assert len(plan.writes) == 1
    draft = plan.writes[0].draft
    assert draft.item == "Silpo"
    assert draft.amount == Decimal("100.00")
    assert draft.category == "groceries"
    assert draft.occurred_at == _ANCHOR
    assert plan.cut_off == 0
    assert plan.bad_amount == 0
    assert plan.unresolved_date == 0
    assert plan.unclassified == 0
    assert plan.skipped_by_kind == {}


def test_the_written_key_matches_bank_txn_key_for_the_same_inputs() -> None:
    plan = _plan(_row())
    expected = bank_txn_key(occurred_at=_ANCHOR, time="14:32", amount=Decimal("100.00"))
    assert plan.writes[0].key == expected


def test_anchor_is_carried_through_onto_the_plan() -> None:
    plan = _plan(_row())
    assert plan.anchor == _ANCHOR


# --- Each non-expense kind produces no draft, under its own counter,   ----
# --- taking priority over every other exclusion reason stacked onto it ----

_NON_EXPENSE_KIND_ROWS: tuple[tuple[str, BankRowKind], ...] = (
    ("income", BankRowKind.INCOME),
    ("savings", BankRowKind.SAVINGS),
    ("own_transfer", BankRowKind.OWN_TRANSFER),
    ("transfer_out", BankRowKind.TRANSFER_OUT),
    # Not a schema-valid wire value: BankRow's own validator coerces it.
    ("something-the-model-invented", BankRowKind.UNCLASSIFIED),
)


def test_each_non_expense_kind_produces_no_draft() -> None:
    for wire_kind, _domain_kind in _NON_EXPENSE_KIND_ROWS:
        plan = _plan(_row(kind=wire_kind))
        assert plan.writes == (), wire_kind


def test_kind_exclusion_takes_priority_over_every_other_exclusion_reason() -> None:
    # Every other check would also fail on this row (partially_visible,
    # amount <= 0, an unresolvable date) — kind is still the reason it is
    # excluded, and it is the *only* counter that moves.
    for wire_kind, domain_kind in _NON_EXPENSE_KIND_ROWS:
        row = _row(
            kind=wire_kind,
            partially_visible=True,
            amount=Decimal("-5"),
            date_header="геть незрозуміло",
        )
        plan = _plan(row)
        assert plan.writes == (), wire_kind
        assert plan.cut_off == 0, wire_kind
        assert plan.bad_amount == 0, wire_kind
        assert plan.unresolved_date == 0, wire_kind
        if domain_kind is BankRowKind.UNCLASSIFIED:
            assert plan.unclassified == 1, wire_kind
            assert plan.skipped_by_kind == {}, wire_kind
        else:
            assert plan.unclassified == 0, wire_kind
            assert plan.skipped_by_kind == {domain_kind: 1}, wire_kind


def test_the_write_decision_does_not_depend_on_skipped_kinds_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blocking 3 of the stage-2_5 review: `plan_writes` decides "write" from
    `row.kind is BankRowKind.EXPENSE` alone, never from membership in
    `_SKIPPED_KINDS` — a whitelist, not the blacklist this replaces.
    Shrinking `_SKIPPED_KINDS` to empty and confirming a savings row is
    still excluded pins that: under the blacklist this fix replaced, the
    same shrink would silently fall through to the write path and record a
    savings jar as spending — exactly ADR-0017's failure mode, from a sixth
    wire kind added later and forgotten in `_SKIPPED_KINDS`.
    """
    monkeypatch.setattr(bank, "_SKIPPED_KINDS", frozenset())
    plan = _plan(_row(kind="savings"))
    assert plan.writes == ()
    assert plan.skipped_by_kind == {BankRowKind.SAVINGS: 1}


def test_skipped_by_kind_counts_each_kind_independently_across_rows() -> None:
    plan = _plan(
        _row(kind="savings"),
        _row(kind="savings"),
        _row(kind="own_transfer"),
    )
    assert plan.writes == ()
    assert plan.skipped_by_kind == {BankRowKind.SAVINGS: 2, BankRowKind.OWN_TRANSFER: 1}


# --- The three field-level exclusion reasons, expense kind only -----------


def test_a_partially_visible_expense_row_produces_no_draft() -> None:
    plan = _plan(_row(partially_visible=True))
    assert plan.writes == ()
    assert plan.cut_off == 1
    assert plan.bad_amount == 0
    assert plan.unresolved_date == 0


def test_a_zero_amount_expense_row_produces_no_draft() -> None:
    plan = _plan(_row(amount=Decimal("0")))
    assert plan.writes == ()
    assert plan.bad_amount == 1
    assert plan.cut_off == 0
    assert plan.unresolved_date == 0


def test_a_negative_amount_expense_row_produces_no_draft() -> None:
    plan = _plan(_row(amount=Decimal("-193.65")))
    assert plan.writes == ()
    assert plan.bad_amount == 1


def test_an_amount_over_the_maximum_also_lands_in_bad_amount() -> None:
    # core.money.to_amount enforces the upper bound too; the row-level
    # `amount <= 0` check does not, so this exercises the second guard inside
    # plan_writes and confirms it lands in the same counter, not a new one.
    plan = _plan(_row(amount=Decimal("1000001")))
    assert plan.writes == ()
    assert plan.bad_amount == 1


def test_an_unresolvable_date_header_produces_no_draft() -> None:
    plan = _plan(_row(date_header="цілком незрозумілий текст"))
    assert plan.writes == ()
    assert plan.unresolved_date == 1
    assert plan.cut_off == 0
    assert plan.bad_amount == 0


# --- plan_writes must never raise: every way ExpenseDraft/to_amount can  ---
# --- fail on an otherwise-valid expense row lands in bad_amount, never   ---
# --- escapes (Blocking 1 of the stage-2_5 review) --------------------------


def test_a_blank_merchant_produces_no_draft_instead_of_raising() -> None:
    # Reachable from the prompt's own instruction (extract_bank.v1.md rule
    # 7: leave an unreadable text field empty) — ExpenseDraft._clean_item
    # raises ValueError on an empty `item`, which ValidationError wraps.
    plan = _plan(_row(merchant=""))
    assert plan.writes == ()
    assert plan.bad_amount == 1
    assert plan.cut_off == 0
    assert plan.unresolved_date == 0


def test_a_whitespace_only_merchant_produces_no_draft_instead_of_raising() -> None:
    # Not caught by a bare `if not merchant` check — this is what makes
    # `_clean_item`'s own `.strip()` the thing that must be exercised here.
    plan = _plan(_row(merchant="   "))
    assert plan.writes == ()
    assert plan.bad_amount == 1


def test_an_extreme_amount_produces_no_draft_instead_of_raising_arithmeticerror() -> None:
    # Decimal.quantize raises decimal.InvalidOperation (an ArithmeticError,
    # not a ValueError) for an amount this large — a plain `except
    # ValueError` around to_amount misses it entirely.
    plan = _plan(_row(amount=Decimal("1e30")))
    assert plan.writes == ()
    assert plan.bad_amount == 1


# --- is_transaction_feed: false overrides everything -----------------------


def test_is_transaction_feed_false_produces_no_drafts_and_no_counts_at_all() -> None:
    plan = _plan(
        _row(),  # would otherwise be a perfectly valid expense
        _row(kind="savings"),
        _row(partially_visible=True),
        _row(amount=Decimal("-5")),
        _row(date_header="незрозуміло"),
        is_transaction_feed=False,
    )
    assert plan.writes == ()
    assert plan.skipped_by_kind == {}
    assert plan.cut_off == 0
    assert plan.bad_amount == 0
    assert plan.unresolved_date == 0
    assert plan.unclassified == 0


# --- Multi-row / multi-day ordering ----------------------------------------


def test_a_multi_day_result_produces_drafts_across_two_dates_in_feed_order() -> None:
    plan = _plan(
        _row(merchant="Coffee", date_header="Сьогодні", amount=Decimal("65")),
        _row(merchant="Taxi", date_header="Вчора", amount=Decimal("210")),
    )
    assert len(plan.writes) == 2
    assert plan.writes[0].draft.item == "Coffee"
    assert plan.writes[0].draft.occurred_at == _ANCHOR
    assert plan.writes[1].draft.item == "Taxi"
    assert plan.writes[1].draft.occurred_at == date(2026, 8, 23)


def test_writes_preserve_the_models_row_order_even_when_rows_are_skipped() -> None:
    plan = _plan(
        _row(merchant="First"),
        _row(kind="savings"),
        _row(merchant="Second"),
    )
    assert [write.draft.item for write in plan.writes] == ["First", "Second"]
