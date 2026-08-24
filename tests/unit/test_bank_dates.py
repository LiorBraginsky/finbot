"""Table-driven tests for finbot.core.extraction.bank_dates.resolve — pure,
no I/O, no clock, no model, no network, no Docker
(docs/plans/stage-2_5-bank-screenshots.md Step 1: "the largest block of this
stage's own verification").

Real weekdays, verified with Python's own `datetime` rather than assumed:
2026-08-22 is a Saturday, 2025-12-27 is a Saturday, 2026-01-05 is a Monday.
"""

from datetime import date

import pytest

from finbot.core.extraction.bank_dates import resolve

# (header, anchor, expected) — the decision-space case table: both relative
# forms in both languages, an absolute header within the year, one crossing a
# year boundary, a weekday mismatch, a weekday-less absolute header, an
# unrecognised header, an empty header, an impossible date, and controls that
# must resolve.
_CASES: tuple[tuple[str, date, date | None], ...] = (
    # --- Relative words, both languages -------------------------------
    ("Сьогодні", date(2026, 8, 24), date(2026, 8, 24)),
    ("Сегодня", date(2026, 8, 24), date(2026, 8, 24)),
    ("Вчора", date(2026, 8, 24), date(2026, 8, 23)),
    ("Вчера", date(2026, 8, 24), date(2026, 8, 23)),
    # Case-insensitivity on the relative words.
    ("сьогодні", date(2026, 8, 24), date(2026, 8, 24)),
    # --- Absolute header, weekday present, resolving within the year --
    # 2026-08-22 is a real Saturday: the header's own weekday agrees.
    ("Сб, 22 серпня", date(2026, 8, 24), date(2026, 8, 22)),
    # Russian form of the same case.
    ("Сб, 22 августа", date(2026, 8, 24), date(2026, 8, 22)),
    # --- Absolute header, weekday present, crossing a year boundary ---
    # Anchor is 2026-01-05; "27 грудня" this year (2026-12-27) is after the
    # anchor, so the most recent occurrence is 2025-12-27 — also a Saturday.
    ("Сб, 27 грудня", date(2026, 1, 5), date(2025, 12, 27)),
    ("Сб, 27 декабря", date(2026, 1, 5), date(2025, 12, 27)),
    # --- Weekday mismatch: the checksum firing ------------------------
    # Same day/month as the resolving case above, but claiming Friday
    # instead of the real Saturday.
    ("Пт, 22 серпня", date(2026, 8, 24), None),
    # --- Absolute header, no weekday: resolves without a checksum -----
    ("22 серпня", date(2026, 8, 24), date(2026, 8, 22)),
    ("22 августа", date(2026, 8, 24), date(2026, 8, 22)),
    # --- Unresolvable headers ------------------------------------------
    ("Якийсь незрозумілий текст", date(2026, 8, 24), None),
    ("", date(2026, 8, 24), None),
    ("   ", date(2026, 8, 24), None),
    # 31 лютого (February 31) does not exist in any year.
    ("31 лютого", date(2026, 8, 24), None),
    ("Вт, 31 лютого", date(2026, 8, 24), None),
    # An unrecognised weekday token makes the whole header unresolvable,
    # even though day/month would otherwise be valid.
    (" Хф, 22 серпня", date(2026, 8, 24), None),
    # A non-numeric day token.
    ("АБ серпня", date(2026, 8, 24), None),
    # A superscript digit: str.isdigit() is True for "²" but int("²") raises
    # ValueError — this is the case that would crash `resolve` outright
    # rather than return None if the day check used isdigit() instead of
    # isdecimal().
    ("² серпня", date(2026, 8, 24), None),
    # --- Controls that must resolve ------------------------------------
    ("Сб, 22 серпня", date(2026, 8, 22), date(2026, 8, 22)),  # anchor == candidate
)


@pytest.mark.parametrize(("header", "anchor", "expected"), _CASES)
def test_resolve(header: str, anchor: date, expected: date | None) -> None:
    assert resolve(header, anchor=anchor) == expected
