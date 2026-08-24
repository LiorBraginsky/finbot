"""Tests for finbot.core.extraction.schema's bank-feed shapes — the same
recursive strictness walk `test_extraction_schema.py` already applies to
`text_json_schema`/`voice_json_schema`, applied to `bank_json_schema`
(docs/plans/stage-2_5-bank-screenshots.md Step 1). See that file's own
docstring for why the walk is shape-based rather than field-by-field.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import ValidationError

from finbot.core.categories.catalog import CATALOG
from finbot.core.extraction.schema import (
    BankExtractionResult,
    BankRow,
    BankRowKind,
    bank_json_schema,
)

_SLUGS_IN_ORDER = tuple(c.slug for c in CATALOG)


def _iter_dicts(node: Any) -> Iterator[dict[str, Any]]:
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


def test_bank_schema_has_at_least_two_object_nodes() -> None:
    schema = bank_json_schema(_SLUGS_IN_ORDER)
    object_nodes = list(_iter_object_nodes(schema))
    assert len(object_nodes) >= 2


def test_every_bank_object_node_forbids_additional_properties() -> None:
    schema = bank_json_schema(_SLUGS_IN_ORDER)
    for node in _iter_object_nodes(schema):
        assert node.get("additionalProperties") is False, node


def test_every_bank_object_node_requires_all_of_its_own_properties() -> None:
    schema = bank_json_schema(_SLUGS_IN_ORDER)
    for node in _iter_object_nodes(schema):
        properties = node.get("properties", {})
        required = node.get("required", [])
        assert sorted(required) == sorted(properties), node


def test_bank_schema_contains_no_ref_or_defs_anywhere() -> None:
    schema = bank_json_schema(_SLUGS_IN_ORDER)
    for node in _iter_dicts(schema):
        assert "$ref" not in node, node
        assert "$defs" not in node, node


def test_bank_schema_top_level_requires_is_transaction_feed_and_rows() -> None:
    schema = bank_json_schema(_SLUGS_IN_ORDER)
    assert sorted(schema["required"]) == ["is_transaction_feed", "rows"]


def test_bank_category_enum_is_every_catalog_slug_in_catalog_order() -> None:
    schema = bank_json_schema(_SLUGS_IN_ORDER)
    category_node = schema["properties"]["rows"]["items"]["properties"]["category"]
    assert category_node["enum"] == list(_SLUGS_IN_ORDER)
    assert category_node["enum"][-1] == "other"


def test_bank_kind_enum_is_exactly_the_five_wire_values() -> None:
    schema = bank_json_schema(_SLUGS_IN_ORDER)
    kind_node = schema["properties"]["rows"]["items"]["properties"]["kind"]
    assert kind_node["enum"] == ["expense", "income", "savings", "own_transfer", "transfer_out"]
    assert "unclassified" not in kind_node["enum"]


def test_a_valid_instance_parses_into_bank_extraction_result() -> None:
    instance = {
        "is_transaction_feed": True,
        "rows": [
            {
                "date_header": "Сьогодні",
                "time": "14:32",
                "merchant": "Silpo",
                "amount": 193.65,
                "kind": "expense",
                "category": "groceries",
                "partially_visible": False,
            },
            {
                "date_header": "Сьогодні",
                "time": None,
                "merchant": "Скарбничка",
                "amount": 6.35,
                "kind": "savings",
                "category": "other",
                "partially_visible": False,
            },
        ],
    }
    result = BankExtractionResult.model_validate(instance)
    assert len(result.rows) == 2
    assert result.rows[0].kind == BankRowKind.EXPENSE
    assert result.rows[1].kind == BankRowKind.SAVINGS


def test_an_instance_with_an_extra_key_is_rejected_by_extra_forbid() -> None:
    instance = {"is_transaction_feed": True, "rows": [], "note": "not part of the schema"}
    with pytest.raises(ValidationError):
        BankExtractionResult.model_validate(instance)


def test_a_row_with_an_extra_key_is_also_rejected() -> None:
    instance = {
        "is_transaction_feed": True,
        "rows": [
            {
                "date_header": "Сьогодні",
                "time": None,
                "merchant": "Silpo",
                "amount": 100,
                "kind": "expense",
                "category": "groceries",
                "partially_visible": False,
                "note": "surprise",
            }
        ],
    }
    with pytest.raises(ValidationError):
        BankExtractionResult.model_validate(instance)


def test_an_unknown_kind_coerces_to_unclassified_rather_than_raising() -> None:
    row = BankRow.model_validate(
        {
            "date_header": "Сьогодні",
            "time": None,
            "merchant": "Silpo",
            "amount": 100,
            "kind": "refund",
            "category": "groceries",
            "partially_visible": False,
        }
    )
    assert row.kind == BankRowKind.UNCLASSIFIED


def test_an_unknown_category_slug_coerces_to_other_rather_than_raising() -> None:
    row = BankRow.model_validate(
        {
            "date_header": "Сьогодні",
            "time": None,
            "merchant": "Silpo",
            "amount": 100,
            "kind": "expense",
            "category": "not-a-real-slug",
            "partially_visible": False,
        }
    )
    assert row.category == "other"
