"""Tests for finbot.core.categories.slugify — pure, no I/O.

The property that matters is not "pretty output": it is that the function is
**deterministic and total**, because the slug is how a second proposal of the
same label is recognised as the same category rather than creating a
duplicate row (ADR-0021).
"""

import pytest

from finbot.core.categories.slugify import slugify_category


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Освіта", "osvita"),
        ("Освіта і курси", "osvita_i_kursy"),
        ("Краса і здоровʼя", "krasa_i_zdorovia"),
        ("Штрафи", "shtrafy"),
        ("Ремонт авто", "remont_avto"),
        # Word-initial forms differ from mid-word ones (KMU 55:2010).
        ("Юрист", "yuryst"),
        ("Їжа", "yizha"),
        ("Ялинка", "yalynka"),
        # `зг` must not collapse onto `ж`.
        ("Згода", "zghoda"),
        ("Жовтень", "zhovten"),
        # Russian input a mixed-language household produces. `и` goes through
        # the Ukrainian rule (`y`, not `i`) — one table, not two: the slug is
        # an opaque identifier, and a second table would only add a way for
        # the same label to slugify differently depending on how the code
        # guessed at the language.
        ("Обучение", "obuchenye"),
        # Already-Latin labels pass through, lowercased.
        ("PayPal", "paypal"),
        ("Co-Working", "co_working"),
        # Punctuation and runs of separators collapse to one underscore.
        ("Дім / побут", "dim_pobut"),
        ("  Кава  ", "kava"),
    ],
)
def test_known_labels_slugify_as_expected(label: str, expected: str) -> None:
    assert slugify_category(label) == expected


def test_zgh_stays_distinct_from_zh() -> None:
    """The one place the standard's positional rule earns its complexity: two
    different Ukrainian labels must not share a slug, or the second would
    silently reuse the first's category.
    """
    assert slugify_category("Згода") != slugify_category("Жода")


@pytest.mark.parametrize("label", ["", "   ", "!!!", "—", "🗂", "···"])
def test_a_label_with_no_usable_characters_still_yields_a_slug(label: str) -> None:
    """Total, not partial. An empty slug would violate `categories.name`'s NOT
    NULL and — worse — collide with every other empty one, merging unrelated
    proposals into a single category.
    """
    assert slugify_category(label) == "custom"


def test_the_slug_fits_the_column_it_is_stored_in() -> None:
    """`categories.name` is `String(64)`. A long label is truncated, never
    rejected: losing the proposal is worse than an ugly identifier.
    """
    slug = slugify_category("Щось надзвичайно довге " * 20)

    assert len(slug) <= 64
    assert not slug.endswith("_")


def test_slugify_is_deterministic_across_calls() -> None:
    """Stated explicitly because the whole reuse mechanism rests on it: the
    same label proposed next month must resolve to the same existing row.
    """
    assert slugify_category("Освіта") == slugify_category("Освіта")
    assert slugify_category("освіта") == slugify_category("ОСВІТА")


def test_a_slug_never_contains_anything_a_prompt_or_url_would_have_to_escape() -> None:
    labels = ["Освіта", "Дім / побут", "Кава & чай", "50% знижка", "Co-Working"]

    for label in labels:
        slug = slugify_category(label)
        assert slug.replace("_", "").isalnum(), slug
        assert slug.lower() == slug, slug
