"""CLAUDE.md rule 2 — money is `numeric`, never `float` — enforced here, in one
place, at the one boundary where it can actually be violated: the JSON wire.

Plain `json.loads` parses a bare number through the C float parser, so
`json.loads('{"a": 1234567.89}')["a"]` is `1234567.8899999999`, not `1234567.89`
— binary floating point cannot represent most decimal fractions exactly.
`parse_float=Decimal` routes the raw digit string straight into `Decimal`
instead, so the value that survives is exactly what was on the wire.

`tests/unit/test_no_float_money.py` walks every other file under
`src/finbot/` and fails if any of them calls `json.loads`/`loads` without
`parse_float=Decimal` — this module is the one file that AST check allow-lists,
because `loads_decimal` is where that keyword actually lives.
"""

import json
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

UAH = "UAH"
MAX_AMOUNT = Decimal("1000000")

_CENTS = Decimal("0.01")


def loads_decimal(text: str) -> Any:
    """json.loads that never produces a float. The only JSON entry point in finbot."""
    return json.loads(text, parse_float=Decimal)


def to_amount(value: Decimal) -> Decimal:
    """Quantize to 2dp, ROUND_HALF_UP. Raises ValueError outside (0, MAX_AMOUNT].

    Quantizing rather than rejecting extra decimal places is deliberate: a model
    returning 33.333 for a three-way split is an artefact, not a problem worth a
    repair call — rejecting it would spend real money to fix a rounding quirk.
    """
    quantized = value.quantize(_CENTS, rounding=ROUND_HALF_UP)
    if quantized <= 0 or quantized > MAX_AMOUNT:
        msg = f"amount {quantized} is outside the allowed range (0, {MAX_AMOUNT}]"
        raise ValueError(msg)
    return quantized
