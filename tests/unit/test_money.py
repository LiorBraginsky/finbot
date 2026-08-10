"""CLAUDE.md rule 2, in one place: money is `numeric`, never `float` — even
transiently while parsing JSON. See also test_no_float_money.py, which enforces
the same rule at the AST level across the whole `src/finbot/` tree.
"""

from decimal import Decimal

import pytest

from finbot.core.money import MAX_AMOUNT, loads_decimal, to_amount


def test_loads_decimal_parses_numbers_as_decimal_not_float() -> None:
    parsed = loads_decimal('{"a": 1234567.89}')
    assert parsed["a"] == Decimal("1234567.89")
    assert isinstance(parsed["a"], Decimal)


def test_plain_json_loads_would_have_lost_precision() -> None:
    # The exact failure rule 2 exists to prevent: this is why loads_decimal exists.
    import json

    assert json.loads('{"a": 1234567.89}')["a"] == 1234567.8899999999


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("33.335"), Decimal("33.34")),
        (Decimal("33.334"), Decimal("33.33")),
        (Decimal("50"), Decimal("50.00")),
        (Decimal("0.01"), Decimal("0.01")),
    ],
)
def test_to_amount_quantizes_to_two_decimal_places_round_half_up(
    value: Decimal, expected: Decimal
) -> None:
    assert to_amount(value) == expected


@pytest.mark.parametrize("value", [Decimal(0), Decimal(-1), MAX_AMOUNT * 10])
def test_to_amount_rejects_amounts_outside_range(value: Decimal) -> None:
    with pytest.raises(ValueError, match="amount"):
        to_amount(value)


def test_to_amount_accepts_max_amount_itself() -> None:
    assert to_amount(MAX_AMOUNT) == MAX_AMOUNT
