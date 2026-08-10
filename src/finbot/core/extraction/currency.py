"""Foreign-currency guard.

Currencies are Stage 1.5 (docs/roadmap.md): the schema has no currency field
and the prompt states every amount is hryvnia, so today's model faithfully
turns "10 dollars" into `10.00` UAH — silently wrong, not merely missing,
which docs/vision.md's "wrong data is worse than missing data" rates as the
worse failure. Detecting a foreign-currency marker in `raw_text` *before*
the model is ever called turns that into a refusal instead of a bad write,
and costs nothing extra: no request goes out at all.

Pure and I/O-free, like the rest of this package — see `text.py`'s own
docstring for why that separation matters.
"""

import re

# Set on `messages.last_error` by the caller when this guard fires, so a
# Stage 1.5 query can find these messages and re-process them instead of
# them being lost (docs/roadmap.md's Stage 1.5).
FOREIGN_CURRENCY_ERROR = "foreign_currency"

# Symbols: unambiguous wherever they appear, attached to a digit or not —
# "$", "€" never mean anything else in this chat.
_SYMBOLS = ("$", "€")

# Exact word forms: a hit only when the marker is not immediately preceded
# or followed by another letter (see _NOT_LETTER_BEFORE/_AFTER below) — a
# digit, punctuation, whitespace or the start/end of the string are all
# fine, which is what lets "10дол" and "10$" match without a space.
#
# "дол" belongs here, not in the stem list below: it is the household's own
# three-letter shorthand ("10дол"), and letting it match as a *prefix* would
# also swallow "долина" (valley), which starts with the same three letters
# and continues with a letter, not a digit or punctuation.
#
# "євро"/"евро" belong here for the same reason from the other direction:
# "євро" is itself a prefix of "європа"/"європейський" (Europe/European), so
# treating it as a stem would misfire on those.
_EXACT_MARKERS = ("usd", "eur", "дол", "євро", "евро")

# Stems: the marker plus further letters still counts, because Ukrainian
# and Russian decline these nouns — "10доларів" is the household's own
# example — and no unrelated word begins with either stem.
_STEM_MARKERS = ("долар", "доллар", "бакс")

# `[^\W\d]` is "a \w character that is not a digit", i.e. a letter or
# underscore — the standard idiom for "letter" that stays Unicode-aware
# (Cyrillic included) without hand-listing an alphabet. Used instead of a
# plain `\b` because `\b` never draws a boundary between two `\w`
# characters, and a digit run followed directly by letters (as in "10дол")
# is exactly the case this guard must still catch.
_NOT_LETTER_BEFORE = r"(?<![^\W\d])"
_NOT_LETTER_AFTER = r"(?![^\W\d])"

_PATTERN = re.compile(
    "|".join(
        [
            *(re.escape(symbol) for symbol in _SYMBOLS),
            *(f"{_NOT_LETTER_BEFORE}{marker}{_NOT_LETTER_AFTER}" for marker in _EXACT_MARKERS),
            *(f"{_NOT_LETTER_BEFORE}{marker}" for marker in _STEM_MARKERS),
        ]
    )
)


def detect_foreign_currency(raw_text: str) -> bool:
    """True if `raw_text` names a currency this project cannot record yet.

    Case-insensitive. Matches a marker as a whole word ("10 доларів"), or
    attached directly to a digit ("10дол", "10$", "$10") — but never as
    part of an unrelated word ("долина").
    """
    return _PATTERN.search(raw_text.lower()) is not None
