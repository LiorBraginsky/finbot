"""The calendar the model is never given (docs/plans/stage-2_5-bank-screenshots.md,
Approach B, R5). `render_bank_prompt` substitutes no `$today`, so a bank-feed
row's `date_header` is transcribed verbatim, never resolved, by the model —
resolving it into an actual `date` happens here, in pure code with its own
exhaustive, table-driven test and no model, no network and no Docker.

`resolve` handles three header shapes:

- A relative word (`Сьогодні`/`Сегодня`, `Вчора`/`Вчера`) resolved against the
  caller's `anchor` — `message.created_at` in the household's timezone
  (Approach B: the arrival anchor, not `now()` at drain time, so a whole feed
  shares one reference point).
- An absolute header, optionally preceded by a weekday abbreviation:
  `"[<weekday>, ]<day> <month-genitive>"`, in Ukrainian or Russian. No year is
  ever printed, so the year is inferred as the most recent occurrence at or
  before `anchor` — a screenshot shows the past, never the future.
- Anything else (empty, unrecognised, or a calendar date that does not exist
  in any year, such as "31 лютого") resolves to `None`.

**The weekday cross-check is the whole point of keeping the header verbatim
instead of asking the model for a date directly.** A bank app prints the
weekday redundantly with the day/month, so when a weekday is present, the
resolved date's own weekday must agree with it — a mismatch means the model
misread something (an OCR slip on the day, a wrong month) and returns `None`
rather than silently landing a transaction on the wrong day, possibly a wrong
year. An absolute header printed with no weekday at all resolves without a
checksum: safe, because "most recent occurrence at or before anchor" is only
wrong for a screenshot of a feed more than a year old.
"""

from datetime import date, timedelta

# Both languages' relative-day words, lower-cased for a case-insensitive
# comparison against `header.casefold()`.
_TODAY_WORDS: frozenset[str] = frozenset({"сьогодні", "сегодня"})
_YESTERDAY_WORDS: frozenset[str] = frozenset({"вчора", "вчера"})

# Weekday abbreviations, Ukrainian and Russian, mapped to `date.weekday()`'s
# own numbering (Monday=0 ... Sunday=6). "сб" (субота/суббота) is identical
# in both languages; Ukrainian Sunday ("нд", неділя) and Russian Sunday
# ("вс", воскресенье) differ, so both are listed.
_WEEKDAYS: dict[str, int] = {
    "пн": 0,
    "вт": 1,
    "ср": 2,
    "чт": 3,
    "пт": 4,
    "сб": 5,
    "нд": 6,
    "вс": 6,
}

# Month names in the genitive case, as a Ukrainian or Russian date header
# prints them ("22 серпня", "27 декабря") — never the nominative form.
_MONTHS: dict[str, int] = {
    "січня": 1,
    "лютого": 2,
    "березня": 3,
    "квітня": 4,
    "травня": 5,
    "червня": 6,
    "липня": 7,
    "серпня": 8,
    "вересня": 9,
    "жовтня": 10,
    "листопада": 11,
    "грудня": 12,
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


def _most_recent_at_or_before(anchor: date, *, month: int, day: int) -> date | None:
    """The most recent `(month, day)` at or before `anchor`, trying `anchor`'s
    own year first and then the year before. `None` when `(month, day)` is
    not a valid calendar date in either year — which, for a combination
    invalid in every year (`day=31, month=2`), is every year that could ever
    be tried.
    """
    try:
        candidate = date(anchor.year, month, day)
    except ValueError:
        pass
    else:
        if candidate <= anchor:
            return candidate
    try:
        return date(anchor.year - 1, month, day)
    except ValueError:
        return None


def resolve(header: str, *, anchor: date) -> date | None:
    """Resolve a verbatim bank-feed `date_header` to a concrete date, or
    `None` when it cannot be resolved deterministically — the caller (`bank.
    plan_writes`) treats `None` exactly like any other reason not to write a
    row (R4): counted, reported, never guessed.
    """
    stripped = header.strip()
    if not stripped:
        return None

    folded = stripped.casefold()
    if folded in _TODAY_WORDS:
        return anchor
    if folded in _YESTERDAY_WORDS:
        return anchor - timedelta(days=1)

    tokens = stripped.replace(",", " ").split()
    weekday_expected: int | None = None
    if len(tokens) == 3:
        weekday_token, day_token, month_token = tokens
        weekday_expected = _WEEKDAYS.get(weekday_token.casefold())
        if weekday_expected is None:
            return None
    elif len(tokens) == 2:
        day_token, month_token = tokens
    else:
        return None

    # `str.isdigit()` accepts characters `int()` rejects — a superscript
    # "²" is `isdigit() == True` but `isdecimal() == False`, and `int()`
    # raises ValueError on it. `isdecimal()` is the check that actually
    # guarantees `int(day_token)` below cannot raise.
    if not day_token.isdecimal():
        return None
    day = int(day_token)

    month = _MONTHS.get(month_token.casefold())
    if month is None:
        return None

    candidate = _most_recent_at_or_before(anchor, month=month, day=day)
    if candidate is None:
        return None

    if weekday_expected is not None and candidate.weekday() != weekday_expected:
        return None

    return candidate
