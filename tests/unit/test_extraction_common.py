"""Unit tests for finbot.core.extraction.common — the request/response
plumbing shared by every modality's build_request/parse_content pair. No
I/O, no clock, no Docker.

`strip_fence` itself is exercised indirectly through both
`core.extraction.text.parse_content` and `core.extraction.voice.parse_content`
(tests/unit/test_extraction_text.py, tests/unit/test_extraction_voice.py) —
this file only covers `build_repair_request`, which neither module's own
test file touches now that it lives here.
"""

from finbot.core.extraction.common import build_repair_request
from finbot.core.extraction.ports import LlmRequest


def test_build_repair_request_appends_assistant_and_user_turns() -> None:
    original = LlmRequest(
        models=("a",),
        messages=({"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}),
        json_schema={"type": "object"},
        schema_name="extraction_result",
    )
    repaired = build_repair_request(original, "bad content", "amount: required field missing")

    assert repaired.messages[:2] == original.messages
    assert repaired.messages[2] == {"role": "assistant", "content": "bad content"}
    assert repaired.messages[3]["role"] == "user"
    assert "amount: required field missing" in repaired.messages[3]["content"]
    assert "No prose, no code fences" in repaired.messages[3]["content"]
    assert repaired.models == original.models
    assert repaired.json_schema == original.json_schema
    assert repaired.schema_name == original.schema_name
