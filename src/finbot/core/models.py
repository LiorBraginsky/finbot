"""Core domain types.

This module must not import ``finbot.repo``, ``finbot.adapters`` or ``finbot.llm``,
aiogram or SQLAlchemy — see ``CLAUDE.md`` rule 3 and ``tests/unit/test_layering.py``.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class MessageKind(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    PHOTO = "photo"


class MessageStatus(StrEnum):
    """The inbox status machine (ADR-0013): `pending` messages are claimed by
    the drain loop, `processing` while a claim is held, `done`/`failed` are
    terminal, `skipped` is for commands and other content never sent to a
    model.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class ExtractionStatus(StrEnum):
    """Exactly spec §5's three values for `extractions.status`."""

    OK = "ok"
    INVALID_JSON = "invalid_json"
    FAILED = "failed"


class IncomingMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    telegram_update_id: int
    telegram_message_id: int
    chat_id: int
    telegram_user_id: int
    display_name: str
    kind: MessageKind
    raw_text: str | None = None
    file_id: str | None = None
