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
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageReplyMarkup,
    EditMessageText,
    GetFile,
    GetMe,
    GetUpdates,
    SendMessage,
    SetMyCommands,
    TelegramMethod,
)
from aiogram.methods.base import TelegramType
from aiogram.types import Chat, File, InlineKeyboardMarkup, Message, Update, User

# The exact substring finbot.adapters.telegram.handlers._is_not_modified
# matches on — this fake exists to let a test see the real Bot API's
# behaviour for a redelivered, identical edit, not a synthetic stand-in for
# it.
_NOT_MODIFIED_MESSAGE = (
    "Bad Request: message is not modified: specified new message content "
    "and reply markup are exactly the same as a current content and reply "
    "markup of the message"
)

_CANNED_USER = User(id=1, is_bot=True, first_name="finbot", username="finbot_test_bot")
_CANNED_CHAT = Chat(id=1, type="private")


class FakeSession(BaseSession):
    """Records every outgoing Telegram API call instead of sending it."""

    def __init__(
        self,
        *,
        scripted_updates: list[list[Update]] | None = None,
        voice_files: dict[str, bytes] | None = None,
    ) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        # `Bot.download(file_id)` calls `GetFile` first (this fake reuses
        # `file_id` verbatim as the returned `file_path`, since nothing here
        # needs the distinction) and then `stream_content` on the URL that
        # `file_path` builds — keyed by `file_id` so a test scripts one dict,
        # not two. A `file_id` absent from this dict makes both steps fail,
        # for a download-failure test (adapters.telegram.audio).
        self._voice_files: dict[str, bytes] = dict(voice_files or {})
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
        # The last text/markup this fake actually sent for each edited
        # message id — how it reproduces Telegram's real 400 "message is
        # not modified" for a redelivered tap that re-renders identically
        # (finbot.adapters.telegram.handlers._rerender_group / start_edit).
        # Without this, that response never happens here, and the
        # idempotency it forces the handler to handle is never exercised.
        self._last_edit_text: dict[int, str | None] = {}
        self._last_edit_markup: dict[int, InlineKeyboardMarkup | None] = {}

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
        if isinstance(method, SetMyCommands):
            return cast(TelegramType, True)
        if isinstance(method, EditMessageText):
            message_id = method.message_id
            if message_id is not None:
                if (
                    message_id in self._last_edit_text
                    and self._last_edit_text[message_id] == method.text
                ):
                    raise TelegramBadRequest(method, _NOT_MODIFIED_MESSAGE)
                self._last_edit_text[message_id] = method.text
            return cast(TelegramType, self._next_canned_message())
        if isinstance(method, EditMessageReplyMarkup):
            message_id = method.message_id
            if message_id is not None:
                if message_id in self._last_edit_markup and (
                    self._last_edit_markup[message_id] == method.reply_markup
                ):
                    raise TelegramBadRequest(method, _NOT_MODIFIED_MESSAGE)
                self._last_edit_markup[message_id] = method.reply_markup
            return cast(TelegramType, self._next_canned_message())
        if isinstance(method, GetUpdates):
            self.get_updates_offsets_used.append(method.offset)
            if not self._scripted_updates:
                return cast(TelegramType, [])
            return cast(TelegramType, self._scripted_updates.pop(0))
        if isinstance(method, GetFile):
            if method.file_id not in self._voice_files:
                raise TelegramBadRequest(method, "Bad Request: file not found")
            # file_path == file_id: nothing downstream of this fake cares
            # about the distinction, and stream_content below only needs a
            # value it can look `_voice_files` back up by.
            file = File(
                file_id=method.file_id,
                file_unique_id=f"{method.file_id}-unique",
                file_path=method.file_id,
            )
            return cast(TelegramType, file)
        raise AssertionError(f"unexpected Telegram API call: {type(method).__name__}")

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,  # noqa: ASYNC109 -- overrides BaseSession's abstract signature
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        """Backs `Bot.download()` (`adapters.telegram.audio.download_voice`):
        real aiogram builds `url` from `session.api.file_url(token,
        file_path)`, always ending in `file_path` — `GetFile` above hands out
        `file_path == file_id`, so the trailing path segment is exactly the
        key `_voice_files` was scripted with.
        """
        file_id = url.rsplit("/", 1)[-1]
        data = self._voice_files.get(file_id)
        if data is None:
            raise ConnectionError(f"fake download: no such file at {url!r}")
        yield data
