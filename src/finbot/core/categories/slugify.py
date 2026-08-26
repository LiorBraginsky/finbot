"""Turn a model-proposed Ukrainian category label into a stable slug.

The model proposes a **label** — «Освіта» — not a slug, for one reason: the
label is what the household reads in every reply, and asking a model for two
strings that must agree with each other invites them to disagree. The slug is
this module's job, and it exists only because `categories.name` is the
identifier every other part of the system joins on.

Deterministic and total: the same label always yields the same slug, and
every label yields *something* — `_FALLBACK` rather than an empty string,
which would collide with itself. That matters because the slug is how a
second proposal of the same label is recognised as the same category
(ADR-0021) instead of creating a duplicate row: `Освіта` proposed next month
must slugify to exactly what it slugified to today.

Transliteration follows the Ukrainian national standard (KMU 55:2010) closely
enough for an identifier — its positional rules for `зг`, and for `є/ї/й/ю/я`
at the start of a word, are implemented; its apostrophe and soft-sign rules
collapse to "drop", since a slug has no use for them.
"""

import re
from typing import Final

_MAX_LENGTH: Final[int] = 64
_FALLBACK: Final[str] = "custom"

# Position-independent single letters. `ь` and `'` map to nothing.
_TABLE: Final[dict[str, str]] = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "h",
    "ґ": "g",
    "д": "d",
    "е": "e",
    "є": "ie",
    "ж": "zh",
    "з": "z",
    "и": "y",
    "і": "i",
    "ї": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ю": "iu",
    "я": "ia",
    "ь": "",
    "'": "",
    "ʼ": "",
    "’": "",
    # Russian letters the standard does not cover, but which a model reading a
    # mixed-language household's messages will produce. Mapped rather than
    # dropped: dropping them would silently merge two different labels onto
    # one slug.
    "ы": "y",
    "э": "e",
    "ё": "e",
    "ъ": "",
}

# Word-initial forms, per the standard: «Юрій» -> "yurii", not "iurii".
_INITIAL: Final[dict[str, str]] = {"є": "ye", "ї": "yi", "й": "y", "ю": "yu", "я": "ya"}

# `зг` -> "zgh", to keep it distinct from `ж` -> "zh".
_ZGH: Final[str] = "зг"

_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify_category(label: str) -> str:
    """`"Освіта"` -> `"osvita"`. Lowercase ASCII, `_`-separated, never empty.

    Truncated to `categories.name`'s own `String(64)` rather than rejected: a
    model returning an over-long label is an annoyance, not a reason to lose
    the proposal — the same trade-off `ExpenseDraft._clean_item` makes.
    Truncation can in principle collide two very long labels onto one slug;
    the consequence is that the second reuses the first's category, which is
    a far better failure than raising in the middle of writing an expense.
    """
    transliterated: list[str] = []
    lowered = label.casefold()
    index = 0
    at_word_start = True
    while index < len(lowered):
        if lowered.startswith(_ZGH, index):
            transliterated.append("zgh")
            index += len(_ZGH)
            at_word_start = False
            continue
        char = lowered[index]
        index += 1
        if char.isalnum():
            if at_word_start and char in _INITIAL:
                transliterated.append(_INITIAL[char])
            else:
                transliterated.append(_TABLE.get(char, char))
            at_word_start = False
        else:
            transliterated.append(" ")
            at_word_start = True

    slug = _NON_SLUG.sub("_", "".join(transliterated)).strip("_")
    return slug[:_MAX_LENGTH].strip("_") or _FALLBACK
