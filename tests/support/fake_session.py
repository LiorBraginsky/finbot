"""A project-local fake aiogram session.

aiogram ships no public `MockedBot`: `aiogram/test_utils/mocked_bot.py` lives
inside aiogram's own test suite and is not distributed (see the plan's
`## Reality check`). This is the minimal stand-in that lets
`Dispatcher.feed_raw_update` run for real, with no socket opened.
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, cast

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageReplyMarkup,
    EditMessageText,
    GetMe,
    GetUpdates,
    SendMessage,
    TelegramMethod,
)
from aiogram.methods.base import TelegramType
from aiogram.types import Chat, Message, Update, User

_CANNED_USER = User(id=1, is_bot=True, first_name="finbot", username="finbot_test_bot")
_CANNED_CHAT = Chat(id=1, type="private")


class FakeSession(BaseSession):
    """Records every outgoing Telegram API call instead of sending it."""

    def __init__(self, *, scripted_updates: list[list[Update]] | None = None) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        # `SendMessage` returns a canned `Message` with an incrementing
        # `message_id`, so a test can assert `expenses.bot_message_id`
        # linkage without every sent message colliding on id=1.
        self._next_message_id = 1
        # `GetUpdates` pops one scripted batch per call and records the
        # `offset` it was called with (see `.get_updates_offsets_used`) —
        # `tests/integration/test_persistence_error_withholds_offset.py` and
        # any future integration-level polling test read it from here rather
        # than from a second, driftable fake.
        self._scripted_updates: list[list[Update]] = list(scripted_updates or [])
        self.get_updates_offsets_used: list[int | None] = []

    async def close(self) -> None:
        return None

    def _next_canned_message(self) -> Message:
        message = Message(
            message_id=self._next_message_id,
            date=datetime.now(tz=UTC),
            chat=_CANNED_CHAT,
            text="pong",
        )
        self._next_message_id += 1
        return message

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,  # noqa: ASYNC109 -- overrides BaseSession's abstract signature
    ) -> TelegramType:
        self.requests.append(method)
        if isinstance(method, GetMe):
            return cast(TelegramType, _CANNED_USER)
        if isinstance(method, SendMessage):
            return cast(TelegramType, self._next_canned_message())
        if isinstance(method, AnswerCallbackQuery):
            return cast(TelegramType, True)
        if isinstance(method, (EditMessageText, EditMessageReplyMarkup)):
            return cast(TelegramType, self._next_canned_message())
        if isinstance(method, GetUpdates):
            self.get_updates_offsets_used.append(method.offset)
            if not self._scripted_updates:
                return cast(TelegramType, [])
            return cast(TelegramType, self._scripted_updates.pop(0))
        raise AssertionError(f"unexpected Telegram API call: {type(method).__name__}")

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,  # noqa: ASYNC109 -- overrides BaseSession's abstract signature
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        raise NotImplementedError
        yield b""
