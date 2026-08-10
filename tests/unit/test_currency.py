"""Unit tests for finbot.core.extraction.currency.detect_foreign_currency —
pure, no I/O, no Docker required.
"""

import pytest

from finbot.core.extraction.currency import detect_foreign_currency

# (raw_text, expected) — the case table the Stage-1.5-guard step asked for:
# real owner examples, every marker in the required list, and the negatives
# that must not fire.
_CASES: tuple[tuple[str, bool], ...] = (
    # Real production examples that were silently mis-recorded.
    ("icloud - 10дол", True),
    ("icloud - 10доларів", True),
    ("icloud - 10$", True),
    # Symbols, attached to a digit or not.
    ("10$", True),
    ("$10", True),
    ("€10", True),
    ("10€", True),
    ("заплатив $", True),
    # usd / eur, case-insensitive, whole word or attached to a digit.
    ("10 USD", True),
    ("10usd", True),
    ("10 eur", True),
    ("10EUR", True),
    # Ukrainian/Russian word forms, exact and declined.
    ("10 дол", True),
    ("ДОЛ 10", True),
    ("долар", True),
    ("10 доларів", True),
    ("доллар", True),
    ("20 долларов", True),
    ("бакс", True),
    ("20 баксів", True),
    ("20баксів", True),
    ("євро", True),
    ("20 євро", True),
    ("евро", True),
    ("20 евро", True),
    # Plain hryvnia text: nothing here should fire.
    ("хліб 50, таксі 200", False),
    ("кава 65", False),
    ("", False),
    # Negatives that must NOT match despite sharing a prefix with a marker.
    ("поїхали в долину", False),  # долина (valley) starts with "дол"
    ("відпустка в Європі", False),  # європа/європі starts with "євро"
    ("європейський союз", False),
    ("евробонди подорожчали", False),  # "евро" continues with a letter
)


@pytest.mark.parametrize(("raw_text", "expected"), _CASES)
def test_detect_foreign_currency(raw_text: str, expected: bool) -> None:
    assert detect_foreign_currency(raw_text) is expected
