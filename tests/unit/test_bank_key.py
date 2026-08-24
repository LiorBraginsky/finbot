"""Tests for finbot.core.extraction.bank.bank_txn_key — the dedup key
(Approach C2, docs/plans/stage-2_5-bank-screenshots.md): `date | time |
amount`, with `merchant` deliberately excluded. Pinning that exclusion here
means a future "improvement" that starts hashing the merchant in fails this
test and has to argue with the ADR, not merely change the implementation.
"""

from datetime import date
from decimal import Decimal

from finbot.core.extraction.bank import bank_txn_key, plan_writes
from finbot.core.extraction.schema import BankExtractionResult

_OCCURRED_AT = date(2026, 8, 24)


def test_bank_txn_key_is_deterministic() -> None:
    first = bank_txn_key(occurred_at=_OCCURRED_AT, time="14:32", amount=Decimal("100.00"))
    second = bank_txn_key(occurred_at=_OCCURRED_AT, time="14:32", amount=Decimal("100.00"))
    assert first == second


def test_bank_txn_key_formats_amount_at_two_decimal_places() -> None:
    key = bank_txn_key(occurred_at=_OCCURRED_AT, time="14:32", amount=Decimal("100"))
    assert key == "2026-08-24|14:32|100.00"

    key_with_fraction = bank_txn_key(occurred_at=_OCCURRED_AT, time="14:32", amount=Decimal("43.1"))
    assert key_with_fraction == "2026-08-24|14:32|43.10"


def test_bank_txn_key_treats_none_and_empty_string_time_the_same() -> None:
    with_none = bank_txn_key(occurred_at=_OCCURRED_AT, time=None, amount=Decimal("100.00"))
    with_empty = bank_txn_key(occurred_at=_OCCURRED_AT, time="", amount=Decimal("100.00"))
    assert with_none == with_empty == "2026-08-24||100.00"


def test_bank_txn_key_normalises_an_unpadded_hour_to_match_a_padded_one() -> None:
    # Two reads of the same pixels: MODEL_FALLBACKS means a retry can be
    # served by a different model, and formatting drifts exactly here — a
    # re-send must not mint a second key for the same transaction (ADR-0018
    # §6, load-bearing part of Blocking 2).
    unpadded = bank_txn_key(occurred_at=_OCCURRED_AT, time="9:05", amount=Decimal("100.00"))
    padded = bank_txn_key(occurred_at=_OCCURRED_AT, time="09:05", amount=Decimal("100.00"))
    assert unpadded == padded == "2026-08-24|09:05|100.00"


def test_bank_txn_key_normalises_an_unrecognisable_or_over_long_time_to_empty() -> None:
    # expenses.bank_txn_key is String(64); an unbounded `time` reaching the
    # key verbatim would raise StringDataRightTruncation out of
    # create_bank_row for a long enough string. Normalising anything that
    # does not match H:MM/HH:MM to "" closes that regardless of length.
    over_long = bank_txn_key(occurred_at=_OCCURRED_AT, time="1" * 100, amount=Decimal("100.00"))
    empty = bank_txn_key(occurred_at=_OCCURRED_AT, time="", amount=Decimal("100.00"))
    assert over_long == empty == "2026-08-24||100.00"


def test_bank_txn_key_differs_by_date_time_or_amount() -> None:
    base = bank_txn_key(occurred_at=_OCCURRED_AT, time="14:32", amount=Decimal("100.00"))
    other_date = bank_txn_key(occurred_at=date(2026, 8, 23), time="14:32", amount=Decimal("100.00"))
    other_time = bank_txn_key(occurred_at=_OCCURRED_AT, time="09:00", amount=Decimal("100.00"))
    other_amount = bank_txn_key(occurred_at=_OCCURRED_AT, time="14:32", amount=Decimal("50.00"))
    assert len({base, other_date, other_time, other_amount}) == 4


def _row_at(*, merchant: str) -> dict[str, object]:
    return {
        "date_header": "Сьогодні",
        "time": "14:32",
        "merchant": merchant,
        "amount": Decimal("100"),
        "kind": "expense",
        "category": "groceries",
        "partially_visible": False,
    }


def test_two_rows_differing_only_by_merchant_produce_the_same_key() -> None:
    # Deliberate: OCR variance between two reads of the same pixels would
    # otherwise defeat dedup on merchant text and double-count money — the
    # worst outcome (Approach C, "Key content").
    result = BankExtractionResult.model_validate(
        {
            "is_transaction_feed": True,
            "rows": [_row_at(merchant="Сільпо"), _row_at(merchant="СІЛЬПО #4521")],
        }
    )
    plan = plan_writes(result, anchor=_OCCURRED_AT)
    assert len(plan.writes) == 2
    assert plan.writes[0].key == plan.writes[1].key
