"""Raw Telegram Bot API update payloads, for feeding straight through
`Dispatcher.feed_raw_update`.

All ids below are fabricated (see `CLAUDE.md` rule 4): no real Telegram user
id, chat id or bot token appears here or anywhere else in this repository.
"""

from datetime import UTC, datetime
from typing import Any

ALLOWED_USER_ID = 111111111
STRANGER_USER_ID = 999999999
CHAT_ID = -1001111111111


def _base_message(update_id: int, message_id: int, user_id: int) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "date": int(datetime.now(tz=UTC).timestamp()),
            "chat": {"id": CHAT_ID, "type": "supergroup"},
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
        },
    }


def text_update(
    update_id: int,
    text: str,
    *,
    message_id: int = 1,
    user_id: int = ALLOWED_USER_ID,
) -> dict[str, Any]:
    update = _base_message(update_id, message_id, user_id)
    update["message"]["text"] = text
    return update


def voice_update(
    update_id: int,
    *,
    message_id: int = 1,
    user_id: int = ALLOWED_USER_ID,
    file_id: str = "voice-file-id",
    duration: int = 3,
) -> dict[str, Any]:
    update = _base_message(update_id, message_id, user_id)
    update["message"]["voice"] = {
        "file_id": file_id,
        "file_unique_id": f"{file_id}-unique",
        "duration": duration,
    }
    return update


def sticker_update(
    update_id: int,
    *,
    message_id: int = 1,
    user_id: int = ALLOWED_USER_ID,
) -> dict[str, Any]:
    update = _base_message(update_id, message_id, user_id)
    update["message"]["sticker"] = {
        "file_id": "sticker-file-id",
        "file_unique_id": "sticker-unique-id",
        "type": "regular",
        "width": 512,
        "height": 512,
        "is_animated": False,
        "is_video": False,
    }
    return update
