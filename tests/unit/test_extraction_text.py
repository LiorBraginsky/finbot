"""Unit tests for finbot.core.extraction.text — pure request/response
transforms, no I/O, no clock. No Docker required.
"""

from datetime import date
from decimal import Decimal

import pytest

from finbot.core.categories.catalog import CATALOG
from finbot.core.extraction.text import (
    ExtractionInvalidError,
    build_request,
    parse_content,
    resolve_dates,
)


def test_build_request_carries_the_full_model_candidate_tuple() -> None:
    request = build_request(
        raw_text="хліб 50", today=date(2026, 8, 10), catalog=CATALOG, models=("a", "b")
    )
    assert request.models == ("a", "b")


def test_build_request_puts_the_raw_text_in_a_user_message() -> None:
    request = build_request(
        raw_text="хліб 50", today=date(2026, 8, 10), catalog=CATALOG, models=("a",)
    )
    assert request.messages[-1] == {"role": "user", "content": "хліб 50"}
    assert request.messages[0]["role"] == "system"


def test_build_request_system_message_renders_today_and_categories() -> None:
    request = build_request(
        raw_text="хліб 50", today=date(2026, 8, 10), catalog=CATALOG, models=("a",)
    )
    system_content = request.messages[0]["content"]
    assert "2026-08-10" in system_content
    assert "groceries" in system_content


def test_build_request_schema_enum_matches_catalog_order() -> None:
    request = build_request(
        raw_text="хліб 50", today=date(2026, 8, 10), catalog=CATALOG, models=("a",)
    )
    category_node = request.json_schema["properties"]["expenses"]["items"]["properties"]["category"]
    assert category_node["enum"] == [c.slug for c in CATALOG]


def test_parse_content_parses_a_well_formed_document() -> None:
    content = (
        '{"expenses": [{"item": "хліб", "amount": 50, "category": "groceries", '
        '"occurred_at": null}]}'
    )
    result = parse_content(content)
    assert len(result.expenses) == 1
    assert result.expenses[0].amount == Decimal("50.00")


def test_parse_content_strips_a_json_code_fence() -> None:
    content = '```json\n{"expenses": []}\n```'
    result = parse_content(content)
    assert result.expenses == []


def test_parse_content_strips_a_bare_code_fence() -> None:
    content = '```\n{"expenses": []}\n```'
    result = parse_content(content)
    assert result.expenses == []


def test_parse_content_raises_extraction_invalid_on_prose() -> None:
    with pytest.raises(ExtractionInvalidError):
        parse_content("I'm sorry, I cannot help with that.")


def test_parse_content_raises_extraction_invalid_on_schema_violation() -> None:
    with pytest.raises(ExtractionInvalidError):
        parse_content('{"expenses": [{"item": "хліб"}]}')  # missing amount/category


def test_parse_content_preserves_decimal_amount_exactly() -> None:
    # The exact regression rule 2 exists to prevent: plain json.loads would
    # turn 3200.89 into 3200.8899999999999 before Decimal ever sees it.
    content = (
        '{"expenses": [{"item": "хліб", "amount": 3200.89, "category": "groceries", '
        '"occurred_at": null}]}'
    )
    result = parse_content(content)
    assert result.expenses[0].amount == Decimal("3200.89")


def test_resolve_dates_fills_null_with_today() -> None:
    content = (
        '{"expenses": [{"item": "хліб", "amount": 50, "category": "groceries", '
        '"occurred_at": null}]}'
    )
    result = parse_content(content)
    resolved = resolve_dates(result, date(2026, 8, 10))
    assert resolved.expenses[0].occurred_at == date(2026, 8, 10)


def test_resolve_dates_clamps_a_future_date_to_today() -> None:
    content = (
        '{"expenses": [{"item": "хліб", "amount": 50, "category": "groceries", '
        '"occurred_at": "2026-08-11"}]}'
    )
    result = parse_content(content)
    resolved = resolve_dates(result, date(2026, 8, 10))
    assert resolved.expenses[0].occurred_at == date(2026, 8, 10)


def test_resolve_dates_leaves_a_past_date_untouched() -> None:
    content = (
        '{"expenses": [{"item": "хліб", "amount": 50, "category": "groceries", '
        '"occurred_at": "2026-08-01"}]}'
    )
    result = parse_content(content)
    resolved = resolve_dates(result, date(2026, 8, 10))
    assert resolved.expenses[0].occurred_at == date(2026, 8, 1)
