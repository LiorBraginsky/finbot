"""Tests for finbot.core.extraction.schema — the derivation is tested, not
trusted (docs/plans/stage-1-text-to-expense.md 2.4).

`pydantic.BaseModel.model_json_schema()` is not usable as-is for OpenRouter's
strict `json_schema` response format: it emits `$defs`/`$ref` for nested
models, omits `additionalProperties: false`, and renders `Decimal` as
`anyOf[{"type": "number"}, {"type": "string"}]`. `text_json_schema()` is
therefore hand-built, and this file proves the two properties that make a
strict schema actually strict, by walking the *shape* of the emitted schema
rather than by asserting on the specific fields `ExpenseDraft` happens to
have today — a table built from today's fields would stay green after a
schema change that reintroduced exactly the bug it exists to catch.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import ValidationError

from finbot.core.categories.catalog import CATALOG
from finbot.core.extraction.schema import ExtractionResult, text_json_schema

_SLUGS_IN_ORDER = tuple(c.slug for c in CATALOG)


def _iter_dicts(node: Any) -> Iterator[dict[str, Any]]:
    """Recursively yield every dict anywhere in a JSON-Schema-shaped tree,
    regardless of nesting depth or key name — a schema-shape walk, not a
    field-by-field one.
    """
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_dicts(item)


def _iter_object_nodes(node: Any) -> Iterator[dict[str, Any]]:
    for candidate in _iter_dicts(node):
        if candidate.get("type") == "object":
            yield candidate


def test_schema_has_at_least_two_object_nodes() -> None:
    # A vacuous "no object node violated the rule" pass would prove nothing;
    # pin that the walk actually finds the top-level document and the nested
    # expense item, so the assertions below are known to exercise something.
    schema = text_json_schema(_SLUGS_IN_ORDER)
    object_nodes = list(_iter_object_nodes(schema))
    assert len(object_nodes) >= 2


def test_every_object_node_forbids_additional_properties() -> None:
    schema = text_json_schema(_SLUGS_IN_ORDER)
    for node in _iter_object_nodes(schema):
        assert node.get("additionalProperties") is False, node


def test_every_object_node_requires_all_of_its_own_properties() -> None:
    schema = text_json_schema(_SLUGS_IN_ORDER)
    for node in _iter_object_nodes(schema):
        properties = node.get("properties", {})
        required = node.get("required", [])
        assert sorted(required) == sorted(properties), node


def test_schema_contains_no_ref_or_defs_anywhere() -> None:
    schema = text_json_schema(_SLUGS_IN_ORDER)
    for node in _iter_dicts(schema):
        assert "$ref" not in node, node
        assert "$defs" not in node, node


def test_category_enum_is_every_catalog_slug_in_catalog_order() -> None:
    schema = text_json_schema(_SLUGS_IN_ORDER)
    category_node = schema["properties"]["expenses"]["items"]["properties"]["category"]
    assert category_node["enum"] == list(_SLUGS_IN_ORDER)
    assert category_node["enum"][-1] == "other"


def test_a_valid_instance_parses_into_extraction_result() -> None:
    instance = {
        "expenses": [
            {"item": "хліб", "amount": 50, "category": "groceries", "occurred_at": None},
            {"item": "таксі", "amount": 200, "category": "transport", "occurred_at": "2026-08-09"},
        ]
    }
    result = ExtractionResult.model_validate(instance)
    assert len(result.expenses) == 2
    assert result.expenses[0].category == "groceries"


def test_an_instance_with_an_extra_key_is_rejected_by_extra_forbid() -> None:
    instance = {"expenses": [], "note": "not part of the schema"}
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(instance)


def test_an_expense_with_an_extra_key_is_also_rejected() -> None:
    instance = {
        "expenses": [
            {
                "item": "хліб",
                "amount": 50,
                "category": "groceries",
                "occurred_at": None,
                "note": "surprise",
            }
        ]
    }
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(instance)
