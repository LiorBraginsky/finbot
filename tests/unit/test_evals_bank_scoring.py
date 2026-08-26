"""Unit tests for evals.scoring's bank-feed scoring — pure, deterministic,
no network, no model, no Docker. Mirrors tests/unit/test_evals_scoring.py's
style: values in, values out.

`no_false_write` is this stage's reason for existing (docs/plans/stage-2_5
-bank-screenshots.md, "The metric this step exists for"): the tests below
prove it is asymmetric (a missed expense passes; an over-written one does
not) and that it is not redundant with `amount_exact` — a metric that reads
the same raw number and is therefore blind to a row recorded under the
wrong kind.
"""

from datetime import date
from decimal import Decimal

from evals.scoring import (
    BankGoldenCase,
    BankRowExpectation,
    failed_bank_case_score,
    score_bank_case,
)

from finbot.core.extraction.schema import BankExtractionResult, BankRow, BankRowKind

_ANCHOR = date(2026, 8, 24)


def _bank_row(
    *,
    date_header: str = "Сьогодні",
    time: str | None = "14:32",
    merchant: str = "Silpo",
    amount: Decimal = Decimal("10.00"),
    kind: BankRowKind = BankRowKind.EXPENSE,
    category: str = "other",
    partially_visible: bool = False,
) -> BankRow:
    return BankRow.model_validate(
        {
            "date_header": date_header,
            "time": time,
            "merchant": merchant,
            "amount": amount,
            "kind": kind.value,
            "category": category,
            "partially_visible": partially_visible,
        }
    )


def _bank_case(
    *,
    case_id: str = "c",
    anchor_date: date = _ANCHOR,
    is_transaction_feed: bool = True,
    rows: tuple[BankRowExpectation, ...] = (),
) -> BankGoldenCase:
    return BankGoldenCase(
        case_id=case_id,
        image_filename=f"{case_id}.jpeg",
        image_data_url="data:image/jpeg;base64,irrelevant",
        anchor_date=anchor_date,
        is_transaction_feed=is_transaction_feed,
        rows=rows,
    )


def _result(*rows: BankRow, is_transaction_feed: bool = True) -> BankExtractionResult:
    return BankExtractionResult(is_transaction_feed=is_transaction_feed, rows=list(rows))


# --- no_false_write: the metric this stage exists for --------------------


def test_no_false_write_fails_on_a_misclassified_savings_row_while_amount_exact_passes() -> None:
    """The central proof: the raw amount is read correctly — proving
    `amount_exact` is not redundant with `no_false_write`, since it
    structurally cannot see a classification error — yet the money is
    written to `expenses` as spending when the truth says it went to a
    savings jar, which is exactly the asymmetric harm this metric exists to
    catch. `kind_exact`/`written_count_exact` also fail here (they watch
    classification and count directly, by design); `amount_exact` does not,
    which is the whole point.
    """
    case = _bank_case(
        rows=(BankRowExpectation(kind="savings", amount=Decimal("6.35"), partially_visible=False),)
    )
    result = _result(_bank_row(kind=BankRowKind.EXPENSE, amount=Decimal("6.35"), category="other"))

    score = score_bank_case(case, result, cost_usd=None, latency_ms=0)

    assert not score.no_false_write
    assert score.amount_exact
    assert score.feed_ok
    assert score.count_exact
    assert score.category_exact  # vacuous: no expense-labelled ground-truth row exists
    assert score.date_exact  # vacuous, same reason
    assert score.dropped_exact  # vacuous, same reason
    # These two *do* also fail — they watch classification/count directly,
    # a different axis from amount_exact's raw-number reading.
    assert not score.kind_exact
    assert not score.written_count_exact


def test_no_false_write_passes_when_the_model_merely_misses_an_expense() -> None:
    """The named asymmetry: a missed expense (a strict subset of what was
    allowed to be written) is a nuisance, not a money-safety violation.
    """
    case = _bank_case(
        rows=(
            BankRowExpectation(
                kind="expense",
                amount=Decimal("50.00"),
                category="groceries",
                occurred_offset_days=0,
                partially_visible=False,
            ),
        )
    )
    result = _result(is_transaction_feed=True)  # the model saw nothing at all

    score = score_bank_case(case, result, cost_usd=None, latency_ms=0)

    assert score.no_false_write
    assert not score.count_exact
    assert not score.written_count_exact


def test_no_false_write_passes_when_every_written_amount_is_allowed() -> None:
    case = _bank_case(
        rows=(
            BankRowExpectation(
                kind="expense",
                amount=Decimal("50.00"),
                category="groceries",
                occurred_offset_days=0,
                partially_visible=False,
            ),
        )
    )
    result = _result(
        _bank_row(kind=BankRowKind.EXPENSE, amount=Decimal("50.00"), category="groceries")
    )

    score = score_bank_case(case, result, cost_usd=None, latency_ms=0)

    assert score.no_false_write
    assert score.written_count_exact


def test_no_false_write_is_scoreable_when_the_model_miscounts_rows() -> None:
    """Exactly when the positional metrics go blind: two extra own_transfer
    rows appear from nowhere (count_exact fails), but no false expense
    amount was ever written, so the asymmetric check still reads True.
    """
    case = _bank_case(
        rows=(
            BankRowExpectation(
                kind="expense",
                amount=Decimal("50.00"),
                category="groceries",
                occurred_offset_days=0,
                partially_visible=False,
            ),
        )
    )
    result = _result(
        _bank_row(kind=BankRowKind.EXPENSE, amount=Decimal("50.00"), category="groceries"),
        _bank_row(kind=BankRowKind.OWN_TRANSFER, amount=Decimal("1000.00")),
        _bank_row(kind=BankRowKind.SAVINGS, amount=Decimal("6.35")),
    )

    score = score_bank_case(case, result, cost_usd=None, latency_ms=0)

    assert not score.count_exact
    assert score.no_false_write


def test_no_false_write_fails_when_the_same_amount_is_written_twice_but_allowed_once() -> None:
    """Multiplicity matters: writing the same allowed amount an extra time
    is still money that was never spent — Counter subset comparison, not
    plain set inclusion, is what catches this.
    """
    case = _bank_case(
        rows=(
            BankRowExpectation(
                kind="expense",
                amount=Decimal("50.00"),
                category="groceries",
                occurred_offset_days=0,
                partially_visible=False,
            ),
        )
    )
    result = _result(
        _bank_row(kind=BankRowKind.EXPENSE, amount=Decimal("50.00"), category="groceries"),
        _bank_row(kind=BankRowKind.EXPENSE, amount=Decimal("50.00"), category="groceries"),
    )

    score = score_bank_case(case, result, cost_usd=None, latency_ms=0)

    assert not score.no_false_write


# --- dropped_exact -----------------------------------------------------------


def test_dropped_exact_catches_a_cut_off_row_that_was_guessed() -> None:
    """The ground truth says this row is cut off (unreadable, so it must not
    be written); the model instead confidently guesses a full amount and
    writes it — exactly the defect R4 exists to prevent, caught here at the
    per-row diagnostic level rather than only in the aggregate money check.
    """
    case = _bank_case(
        rows=(
            BankRowExpectation(
                kind="expense",
                amount=Decimal("50.00"),
                category="groceries",
                occurred_offset_days=0,
                partially_visible=True,
            ),
        )
    )
    result = _result(
        _bank_row(
            kind=BankRowKind.EXPENSE,
            amount=Decimal("50.00"),
            category="groceries",
            partially_visible=False,
        )
    )

    score = score_bank_case(case, result, cost_usd=None, latency_ms=0)

    assert not score.dropped_exact
    # The model also wrote money the truth says was never legible enough to
    # trust — no_false_write catches that half of the same defect.
    assert not score.no_false_write


def test_dropped_exact_passes_when_a_cut_off_row_is_correctly_left_unwritten() -> None:
    case = _bank_case(
        rows=(
            BankRowExpectation(
                kind="expense",
                amount=Decimal("50.00"),
                category="groceries",
                occurred_offset_days=0,
                partially_visible=True,
            ),
        )
    )
    result = _result(
        _bank_row(
            kind=BankRowKind.EXPENSE,
            amount=Decimal("50.00"),
            category="groceries",
            partially_visible=True,
        )
    )

    score = score_bank_case(case, result, cost_usd=None, latency_ms=0)

    assert score.dropped_exact
    assert score.no_false_write


# --- date_exact: through the production resolver ----------------------------


def test_date_exact_fails_on_a_weekday_mismatch_through_the_production_resolver() -> None:
    """2025-12-27 (anchor 2026-01-05 minus nine days) is a real Saturday
    ("сб") — the model claims Friday ("пт"), so `bank_dates.resolve` itself
    returns `None` and date_exact must fail, even though the amount and
    category were both read correctly.
    """
    anchor = date(2026, 1, 5)
    case = _bank_case(
        anchor_date=anchor,
        rows=(
            BankRowExpectation(
                kind="expense",
                amount=Decimal("10.00"),
                category="other",
                occurred_offset_days=-9,
                partially_visible=False,
            ),
        ),
    )
    result = _result(
        _bank_row(
            date_header="Пт, 27 грудня",
            kind=BankRowKind.EXPENSE,
            amount=Decimal("10.00"),
            category="other",
        )
    )

    score = score_bank_case(case, result, cost_usd=None, latency_ms=0)

    assert not score.date_exact
    assert score.amount_exact
    assert score.category_exact


def test_date_exact_passes_when_the_resolved_date_matches_the_offset() -> None:
    anchor = date(2026, 1, 5)
    case = _bank_case(
        anchor_date=anchor,
        rows=(
            BankRowExpectation(
                kind="expense",
                amount=Decimal("10.00"),
                category="other",
                occurred_offset_days=-9,
                partially_visible=False,
            ),
        ),
    )
    result = _result(
        _bank_row(
            date_header="Сб, 27 грудня",
            kind=BankRowKind.EXPENSE,
            amount=Decimal("10.00"),
            category="other",
        )
    )

    score = score_bank_case(case, result, cost_usd=None, latency_ms=0)

    assert score.date_exact


# --- feed_ok -----------------------------------------------------------------


def test_feed_ok_catches_a_receipt_labelled_as_a_feed() -> None:
    case = _bank_case(is_transaction_feed=False, rows=())
    result = _result(is_transaction_feed=True)

    score = score_bank_case(case, result, cost_usd=None, latency_ms=0)

    assert not score.feed_ok


def test_feed_ok_passes_when_both_agree() -> None:
    case = _bank_case(is_transaction_feed=True, rows=())
    result = _result(is_transaction_feed=True)

    score = score_bank_case(case, result, cost_usd=None, latency_ms=0)

    assert score.feed_ok


# --- count_exact gating -----------------------------------------------------


def test_count_mismatch_fails_every_positional_metric() -> None:
    case = _bank_case(
        rows=(
            BankRowExpectation(
                kind="expense",
                amount=Decimal("10.00"),
                category="other",
                occurred_offset_days=0,
                partially_visible=False,
            ),
        )
    )
    result = _result()  # zero rows: the model saw an empty feed

    score = score_bank_case(case, result, cost_usd=None, latency_ms=0)

    assert not score.count_exact
    assert not score.amount_exact
    assert not score.kind_exact
    assert not score.category_exact
    assert not score.date_exact
    assert not score.dropped_exact


# --- failed_bank_case_score ---------------------------------------------------


def test_failed_bank_case_score_marks_every_metric_false() -> None:
    score = failed_bank_case_score("case-x", cost_usd=None, latency_ms=0)

    assert not score.schema_ok
    assert not score.feed_ok
    assert not score.count_exact
    assert not score.kind_exact
    assert not score.dropped_exact
    assert not score.category_exact
    assert not score.date_exact
    assert not score.written_count_exact
    assert not score.amount_exact
    assert not score.no_false_write
    assert score.case_id == "case-x"


# --- ADR-0020: cash_withdrawal and transfer_out are written kinds ---------


def test_a_correctly_written_transfer_out_is_not_a_false_write() -> None:
    """Under the pre-ADR-0020 definition this exact case scored 0 —
    `allowed_amounts` held `expense` rows only, so a correctly classified
    outgoing transfer counted as money invented. Widening the allowed set is
    the whole point of the rename; this is the test that fails without it.
    """
    case = _bank_case(
        rows=(
            BankRowExpectation(
                kind="transfer_out",
                amount=Decimal("750.25"),
                partially_visible=False,
                occurred_offset_days=0,
            ),
        )
    )
    result = _result(
        _bank_row(kind=BankRowKind.TRANSFER_OUT, amount=Decimal("750.25"), category="gifts")
    )

    score = score_bank_case(case, result, cost_usd=None, latency_ms=0)

    assert score.no_false_write
    assert score.written_count_exact
    assert score.kind_exact
    assert score.date_exact


def test_a_cash_withdrawal_written_as_such_is_not_a_false_write() -> None:
    case = _bank_case(
        rows=(
            BankRowExpectation(
                kind="cash_withdrawal",
                amount=Decimal("2000.00"),
                partially_visible=False,
                occurred_offset_days=0,
            ),
        )
    )
    result = _result(
        _bank_row(
            kind=BankRowKind.CASH_WITHDRAWAL,
            merchant="Зняття готівки в банкоматі",
            amount=Decimal("2000.00"),
        )
    )

    score = score_bank_case(case, result, cost_usd=None, latency_ms=0)

    assert score.no_false_write
    assert score.written_count_exact


def test_a_savings_row_written_as_a_cash_withdrawal_is_still_a_false_write() -> None:
    """The failure the metric guards has not gone away, only moved: money that
    only went into the household's own jar must not reach `expenses` under any
    kind. Widening the allowed set to the written kinds must not widen it to
    the *amounts* of skipped rows.
    """
    case = _bank_case(
        rows=(BankRowExpectation(kind="savings", amount=Decimal("6.35"), partially_visible=False),)
    )
    result = _result(_bank_row(kind=BankRowKind.CASH_WITHDRAWAL, amount=Decimal("6.35")))

    score = score_bank_case(case, result, cost_usd=None, latency_ms=0)

    assert not score.no_false_write


def test_category_exact_ignores_a_written_non_expense_row() -> None:
    """A cash row's category is `FORCED_CATEGORY`'s, not the model's, so
    scoring it would compare the code against itself. The model's own
    `category` on such a row is noise and must not move the metric — in
    either direction.
    """
    case = _bank_case(
        rows=(
            BankRowExpectation(
                kind="cash_withdrawal",
                amount=Decimal("2000.00"),
                partially_visible=False,
                occurred_offset_days=0,
            ),
        )
    )
    nonsense = _result(
        _bank_row(kind=BankRowKind.CASH_WITHDRAWAL, amount=Decimal("2000.00"), category="hookah")
    )

    assert score_bank_case(case, nonsense, cost_usd=None, latency_ms=0).category_exact


def test_a_cut_off_cash_row_must_not_be_written_either() -> None:
    """`dropped_exact` widened with the written kinds: R4's cut-off rule is
    not an expense-only rule, and a guessed amount on a half-visible cash row
    is exactly as wrong as on a purchase.
    """
    case = _bank_case(
        rows=(
            BankRowExpectation(
                kind="cash_withdrawal",
                amount=Decimal("2000.00"),
                partially_visible=True,
                occurred_offset_days=0,
            ),
        )
    )
    obedient = _result(
        _bank_row(
            kind=BankRowKind.CASH_WITHDRAWAL, amount=Decimal("2000.00"), partially_visible=True
        )
    )
    greedy = _result(
        _bank_row(
            kind=BankRowKind.CASH_WITHDRAWAL, amount=Decimal("2000.00"), partially_visible=False
        )
    )

    assert score_bank_case(case, obedient, cost_usd=None, latency_ms=0).dropped_exact
    assert not score_bank_case(case, greedy, cost_usd=None, latency_ms=0).dropped_exact
