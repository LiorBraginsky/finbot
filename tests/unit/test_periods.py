"""Unit tests for finbot.core.reporting.periods.resolve. No Docker, no clock:
`today` is always passed in explicitly.
"""

from datetime import date

import pytest

from finbot.core.reporting.periods import resolve


def test_day_is_today_to_today() -> None:
    assert resolve("day", date(2026, 8, 10)) == (date(2026, 8, 10), date(2026, 8, 10))


def test_week_from_a_monday_spans_only_today() -> None:
    monday = date(2026, 8, 10)
    assert resolve("week", monday) == (monday, monday)


def test_week_from_a_sunday_spans_back_to_mondays_date() -> None:
    sunday = date(2026, 8, 16)
    assert resolve("week", sunday) == (date(2026, 8, 10), sunday)


def test_month_from_the_1st_spans_only_today() -> None:
    first = date(2026, 8, 1)
    assert resolve("month", first) == (first, first)


def test_month_mid_month_spans_back_to_the_1st() -> None:
    mid_month = date(2026, 8, 15)
    assert resolve("month", mid_month) == (date(2026, 8, 1), mid_month)


def test_unknown_period_raises() -> None:
    """`Period` is a closed Literal; a value outside it can only reach here
    past a `cast` or similar type-checker bypass (see handlers.py), so this
    is `assert_never`'s own `AssertionError`, not a hand-rolled one.
    """
    with pytest.raises(AssertionError):
        resolve("year", date(2026, 8, 10))  # type: ignore[arg-type]
