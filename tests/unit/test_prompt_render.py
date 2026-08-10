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
from finbot.prompts import PROMPT_VERSION_TEXT, load, render_text_prompt


def test_prompt_version_text_matches_the_shipped_file() -> None:
    assert PROMPT_VERSION_TEXT == "extract_text.v1"
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
