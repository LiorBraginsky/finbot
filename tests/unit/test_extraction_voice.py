"""Unit tests for finbot.core.extraction.voice — pure request/response
transforms, no I/O, no clock. No Docker required. Mirrors
tests/unit/test_extraction_text.py's structure and coverage.
"""

from datetime import date
from decimal import Decimal

import pytest

from finbot.core.categories.catalog import CATALOG
from finbot.core.extraction.common import ExtractionInvalidError
from finbot.core.extraction.voice import build_request, parse_content, resolve_dates


def test_build_request_carries_the_full_model_candidate_tuple() -> None:
    request = build_request(
        audio_base64="YWJj", today=date(2026, 8, 10), catalog=CATALOG, models=("a", "b")
    )
    assert request.models == ("a", "b")


def test_build_request_puts_the_audio_in_an_input_audio_content_part() -> None:
    request = build_request(
        audio_base64="YWJj", today=date(2026, 8, 10), catalog=CATALOG, models=("a",)
    )
    user_message = request.messages[-1]
    assert user_message["role"] == "user"
    content = user_message["content"]
    assert content == [{"type": "input_audio", "input_audio": {"data": "YWJj", "format": "mp3"}}]
    assert request.messages[0]["role"] == "system"


def test_build_request_system_message_renders_today_and_categories() -> None:
    request = build_request(
        audio_base64="YWJj", today=date(2026, 8, 10), catalog=CATALOG, models=("a",)
    )
    system_content = request.messages[0]["content"]
    assert "2026-08-10" in system_content
    assert "groceries" in system_content


def test_build_request_schema_enum_matches_catalog_order() -> None:
    request = build_request(
        audio_base64="YWJj", today=date(2026, 8, 10), catalog=CATALOG, models=("a",)
    )
    category_node = request.json_schema["properties"]["expenses"]["items"]["properties"]["category"]
    assert category_node["enum"] == [c.slug for c in CATALOG]


def test_build_request_schema_requires_transcript() -> None:
    request = build_request(
        audio_base64="YWJj", today=date(2026, 8, 10), catalog=CATALOG, models=("a",)
    )
    assert "transcript" in request.json_schema["required"]


def test_parse_content_parses_a_well_formed_document() -> None:
    content = (
        '{"transcript": "хліб пʼятдесят", "expenses": [{"item": "хліб", "amount": 50, '
        '"category": "groceries", "occurred_at": null}]}'
    )
    result = parse_content(content)
    assert result.transcript == "хліб пʼятдесят"
    assert len(result.expenses) == 1
    assert result.expenses[0].amount == Decimal("50.00")


def test_parse_content_strips_a_json_code_fence() -> None:
    content = '```json\n{"transcript": "", "expenses": []}\n```'
    result = parse_content(content)
    assert result.expenses == []


def test_parse_content_raises_extraction_invalid_on_prose() -> None:
    with pytest.raises(ExtractionInvalidError):
        parse_content("I'm sorry, I cannot help with that.")


def test_parse_content_raises_extraction_invalid_when_transcript_is_missing() -> None:
    with pytest.raises(ExtractionInvalidError):
        parse_content('{"expenses": []}')


def test_parse_content_preserves_decimal_amount_exactly() -> None:
    content = (
        '{"transcript": "х", "expenses": [{"item": "хліб", "amount": 3200.89, '
        '"category": "groceries", "occurred_at": null}]}'
    )
    result = parse_content(content)
    assert result.expenses[0].amount == Decimal("3200.89")


def test_resolve_dates_fills_null_with_today_and_keeps_transcript() -> None:
    content = (
        '{"transcript": "хліб", "expenses": [{"item": "хліб", "amount": 50, '
        '"category": "groceries", "occurred_at": null}]}'
    )
    result = parse_content(content)
    resolved = resolve_dates(result, date(2026, 8, 10))
    assert resolved.transcript == "хліб"
    assert resolved.expenses[0].occurred_at == date(2026, 8, 10)


def test_resolve_dates_clamps_a_future_date_to_today() -> None:
    content = (
        '{"transcript": "х", "expenses": [{"item": "хліб", "amount": 50, '
        '"category": "groceries", "occurred_at": "2026-08-11"}]}'
    )
    result = parse_content(content)
    resolved = resolve_dates(result, date(2026, 8, 10))
    assert resolved.expenses[0].occurred_at == date(2026, 8, 10)
