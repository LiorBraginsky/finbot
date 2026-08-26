"""Unit tests for finbot.prompts: versioned loading and Template rendering.

`string.Template.substitute` (not `str.format`) is load-bearing: the prompt
text contains no braces today, but the schema example previously discussed in
the plan does, and `str.format` would choke on any brace that ever lands in
the Markdown. `substitute` (not `safe_substitute`) is chosen so a missing
placeholder raises loudly rather than being silently left as `$placeholder`
in a live prompt.
"""

from datetime import date

import pytest

from finbot.core.categories.catalog import CATALOG
from finbot.prompts import (
    PROMPT_VERSION_BANK,
    PROMPT_VERSION_TEXT,
    PROMPT_VERSION_VOICE,
    load,
    render_bank_prompt,
    render_text_prompt,
    render_voice_prompt,
)


def test_prompt_version_text_matches_the_shipped_file() -> None:
    assert PROMPT_VERSION_TEXT == "extract_text.v2"
    # load() must not raise: the file backing this version must exist.
    assert "Categories" in load(PROMPT_VERSION_TEXT)


def test_render_text_prompt_substitutes_today_and_weekday() -> None:
    rendered = render_text_prompt(today=date(2026, 8, 10), catalog=CATALOG)
    assert "2026-08-10" in rendered
    assert "Monday" in rendered  # 2026-08-10 is a Monday


def test_render_text_prompt_lists_every_catalog_slug_with_its_emoji() -> None:
    rendered = render_text_prompt(today=date(2026, 8, 10), catalog=CATALOG)
    for category in CATALOG:
        assert category.slug in rendered
        assert category.emoji in rendered
        assert category.description in rendered


def test_render_text_prompt_leaves_no_placeholder_unfilled() -> None:
    rendered = render_text_prompt(today=date(2026, 8, 10), catalog=CATALOG)
    assert "$today" not in rendered
    assert "$weekday" not in rendered
    assert "$categories" not in rendered


def test_load_raises_for_an_unknown_version() -> None:
    with pytest.raises(FileNotFoundError):
        load("extract_text.v999")


def test_prompt_version_voice_matches_the_shipped_file() -> None:
    assert PROMPT_VERSION_VOICE == "extract_voice.v1"
    assert "Categories" in load(PROMPT_VERSION_VOICE)


def test_render_voice_prompt_substitutes_today_and_weekday() -> None:
    rendered = render_voice_prompt(today=date(2026, 8, 10), catalog=CATALOG)
    assert "2026-08-10" in rendered
    assert "Monday" in rendered  # 2026-08-10 is a Monday


def test_render_voice_prompt_lists_every_catalog_slug_with_its_emoji() -> None:
    rendered = render_voice_prompt(today=date(2026, 8, 10), catalog=CATALOG)
    for category in CATALOG:
        assert category.slug in rendered
        assert category.emoji in rendered
        assert category.description in rendered


def test_render_voice_prompt_leaves_no_placeholder_unfilled() -> None:
    rendered = render_voice_prompt(today=date(2026, 8, 10), catalog=CATALOG)
    assert "$today" not in rendered
    assert "$weekday" not in rendered
    assert "$categories" not in rendered


def test_render_voice_prompt_mentions_transcribing_before_extracting() -> None:
    rendered = render_voice_prompt(today=date(2026, 8, 10), catalog=CATALOG)
    assert "transcribe" in rendered.lower()


def test_prompt_version_bank_matches_the_shipped_file() -> None:
    assert PROMPT_VERSION_BANK == "extract_bank.v3"
    assert "Categories" in load(PROMPT_VERSION_BANK)


def test_render_bank_prompt_lists_every_catalog_slug_with_its_emoji() -> None:
    rendered = render_bank_prompt(catalog=CATALOG)
    for category in CATALOG:
        assert category.slug in rendered
        assert category.emoji in rendered
        assert category.description in rendered


def test_render_bank_prompt_leaves_no_categories_placeholder_unfilled() -> None:
    rendered = render_bank_prompt(catalog=CATALOG)
    assert "$categories" not in rendered


def test_render_bank_prompt_template_contains_no_today_or_weekday_placeholder() -> None:
    # R5/Approach B: the model is never told today's date, so it cannot
    # resolve one — pinned against the raw template file, not only the
    # rendered output, so a future edit that reintroduces `$today` fails here
    # even before anyone calls render_bank_prompt with today's date at hand.
    template = load(PROMPT_VERSION_BANK)
    assert "$today" not in template
    assert "$weekday" not in template


def test_render_bank_prompt_mentions_a_bank_transaction_feed() -> None:
    rendered = render_bank_prompt(catalog=CATALOG)
    assert "bank" in rendered.lower()


def test_only_the_text_and_bank_prompts_ask_for_a_category_proposal() -> None:
    """ADR-0021's measured trade-off, pinned so it cannot be "tidied up" into
    consistency: the voice prompt deliberately never mentions
    `suggested_category`, because asking for it there cost `transcript_ok`
    two cases in ten while gaining nothing the other two channels do not
    already give. The wire schema still carries the field on all three (strict
    mode requires it) — the model simply returns `null` for voice.
    """
    assert "suggested_category" in load(PROMPT_VERSION_TEXT)
    assert "suggested_category" in load(PROMPT_VERSION_BANK)
    assert "suggested_category" not in load(PROMPT_VERSION_VOICE)
