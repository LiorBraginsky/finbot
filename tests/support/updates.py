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


def photo_update(
    update_id: int,
    *,
    message_id: int = 1,
    user_id: int = ALLOWED_USER_ID,
    file_id: str = "photo-file-id",
) -> dict[str, Any]:
    update = _base_message(update_id, message_id, user_id)
    update["message"]["photo"] = [
        {"file_id": file_id, "file_unique_id": f"{file_id}-unique", "width": 1280, "height": 1280}
    ]
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


# The bot's own fabricated identity, for the confirmation message a
# callback_update's `message` field represents. Distinct from ALLOWED_USER_ID
# and STRANGER_USER_ID so a test can assert nobody confused the two.
BOT_USER_ID = 555555555


def callback_update(
    update_id: int,
    data: str,
    *,
    user_id: int = ALLOWED_USER_ID,
    bot_message_id: int = 1,
) -> dict[str, Any]:
    """A `callback_query` update: a household member tapping a button on a
    confirmation message the bot itself sent.

    `message.from` is the **bot** (`BOT_USER_ID`), never `user_id` — this is
    the Stage-1 trap, present in the fixture rather than only in prose: code
    that reads `callback_query.message.from_user` instead of
    `callback_query.from_user` would resolve every tap as coming from the
    bot, not from either household member.
    """
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cbq-{update_id}",
            "from": {"id": user_id, "is_bot": False, "first_name": "Alice"},
            "message": {
                "message_id": bot_message_id,
                "date": int(datetime.now(tz=UTC).timestamp()),
                "chat": {"id": CHAT_ID, "type": "supergroup"},
                "from": {"id": BOT_USER_ID, "is_bot": True, "first_name": "finbot"},
                "text": "✅ ...",
            },
            "chat_instance": "chat-instance-1",
            "data": data,
        },
    }
