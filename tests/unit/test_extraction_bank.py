"""Unit tests for finbot.core.extraction.bank's request/response transforms
(`build_request`, `parse_content`) — pure, no I/O, no clock. Mirrors
tests/unit/test_extraction_voice.py's structure and coverage; not explicitly
named in docs/plans/stage-2_5-bank-screenshots.md's Step 1 verification list,
added for the same reason `build_request`/`parse_content` are tested for
text and voice — untested request/response plumbing is a real gap, not a
scope expansion.
"""

from datetime import date
from decimal import Decimal

import pytest

from finbot.core.categories.catalog import CATALOG
from finbot.core.extraction.bank import build_request, parse_content
from finbot.core.extraction.common import ExtractionInvalidError


def test_build_request_carries_the_full_model_candidate_tuple() -> None:
    request = build_request(
        image_data_url="data:image/jpeg;base64,YWJj", catalog=CATALOG, models=("a", "b")
    )
    assert request.models == ("a", "b")


def test_build_request_puts_the_image_in_an_image_url_content_part() -> None:
    request = build_request(
        image_data_url="data:image/jpeg;base64,YWJj", catalog=CATALOG, models=("a",)
    )
    user_message = request.messages[-1]
    assert user_message["role"] == "user"
    content = user_message["content"]
    assert content == [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,YWJj"}}]
    assert request.messages[0]["role"] == "system"


def test_build_request_system_message_renders_categories_but_no_date() -> None:
    request = build_request(
        image_data_url="data:image/jpeg;base64,YWJj", catalog=CATALOG, models=("a",)
    )
    system_content = request.messages[0]["content"]
    assert "groceries" in system_content
    # R5/Approach B: the model is never told today's date.
    assert "$today" not in system_content
    assert str(date(2026, 8, 10)) not in system_content


def test_build_request_schema_enum_matches_catalog_order() -> None:
    request = build_request(
        image_data_url="data:image/jpeg;base64,YWJj", catalog=CATALOG, models=("a",)
    )
    category_node = request.json_schema["properties"]["rows"]["items"]["properties"]["category"]
    assert category_node["enum"] == [c.slug for c in CATALOG]


def test_build_request_schema_requires_is_transaction_feed_and_rows() -> None:
    request = build_request(
        image_data_url="data:image/jpeg;base64,YWJj", catalog=CATALOG, models=("a",)
    )
    assert sorted(request.json_schema["required"]) == ["is_transaction_feed", "rows"]


def test_parse_content_parses_a_well_formed_document() -> None:
    content = (
        '{"is_transaction_feed": true, "rows": [{"date_header": "Сьогодні", "time": null, '
        '"merchant": "Silpo", "amount": 193.65, "kind": "expense", "category": "groceries", '
        '"partially_visible": false}]}'
    )
    result = parse_content(content)
    assert result.is_transaction_feed is True
    assert len(result.rows) == 1
    assert result.rows[0].amount == Decimal("193.65")


def test_parse_content_strips_a_json_code_fence() -> None:
    content = '```json\n{"is_transaction_feed": true, "rows": []}\n```'
    result = parse_content(content)
    assert result.rows == []


def test_parse_content_raises_extraction_invalid_on_prose() -> None:
    with pytest.raises(ExtractionInvalidError):
        parse_content("I'm sorry, I cannot help with that.")


def test_parse_content_raises_extraction_invalid_when_is_transaction_feed_is_missing() -> None:
    with pytest.raises(ExtractionInvalidError):
        parse_content('{"rows": []}')


def test_parse_content_preserves_decimal_amount_exactly() -> None:
    content = (
        '{"is_transaction_feed": true, "rows": [{"date_header": "Сьогодні", "time": null, '
        '"merchant": "Silpo", "amount": 3200.89, "kind": "expense", "category": "groceries", '
        '"partially_visible": false}]}'
    )
    result = parse_content(content)
    assert result.rows[0].amount == Decimal("3200.89")
