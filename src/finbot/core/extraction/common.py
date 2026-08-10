"""Request/response plumbing shared by every extraction modality
(`core/extraction/text.py`, `core/extraction/voice.py`): stripping an
optional ```json fence, the exception raised when a model's content does not
parse, and building a repair turn. None of this is modality-specific — the
modality-specific parts are only the prompt, the schema, and what happens
once a valid result comes back (docs/roadmap.md Stage 2) — so keeping it
here is what stops `voice.py` from growing its own, subtly different, copy
of any of it.
"""

import re

from finbot.core.extraction.ports import LlmRequest

# Some providers wrap a `response_format` payload in a ```json fence anyway.
# DOTALL so the fence can wrap a multi-line document; anchored so a fence
# has to span the whole trimmed string, not merely appear somewhere in it.
_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL)


class ExtractionInvalidError(Exception):
    """`content` did not parse into the modality's result shape.

    The message is short enough to paste into the repair prompt verbatim —
    see `build_repair_request`.
    """


def strip_fence(content: str) -> str:
    stripped = content.strip()
    match = _FENCE_RE.match(stripped)
    return match.group(1).strip() if match else stripped


def build_repair_request(previous: LlmRequest, bad_content: str, error: str) -> LlmRequest:
    """Append the bad assistant turn and a repair instruction, keeping
    everything else — model candidates, schema — identical to `previous`.
    Generic across modalities: it never looks inside `previous.messages`
    beyond appending to them, so text and voice share this one copy.
    """
    assistant_message = {"role": "assistant", "content": bad_content}
    repair_message = {
        "role": "user",
        "content": (
            f"The previous reply did not match the schema: {error}\n"
            "Return only a JSON document matching the schema. No prose, no code fences."
        ),
    }
    return LlmRequest(
        models=previous.models,
        messages=(*previous.messages, assistant_message, repair_message),
        json_schema=previous.json_schema,
        schema_name=previous.schema_name,
    )
