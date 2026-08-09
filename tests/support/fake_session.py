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
from aiogram.methods import GetMe, SendMessage, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import Chat, Message, User

_CANNED_USER = User(id=1, is_bot=True, first_name="finbot", username="finbot_test_bot")
_CANNED_MESSAGE = Message(
    message_id=1,
    date=datetime.now(tz=UTC),
    chat=Chat(id=1, type="private"),
    text="pong",
)


class FakeSession(BaseSession):
    """Records every outgoing Telegram API call instead of sending it."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []

    async def close(self) -> None:
        return None

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
            return cast(TelegramType, _CANNED_MESSAGE)
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
