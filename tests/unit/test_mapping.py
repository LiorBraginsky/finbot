"""Unit tests for finbot.adapters.telegram.mapping. No Docker, no network."""

from datetime import UTC, datetime

from aiogram.types import Chat, Message, PhotoSize, Sticker, User, Voice

from finbot.adapters.telegram.mapping import to_incoming
from finbot.core.models import MessageKind

CHAT_ID = -1001111111111
TELEGRAM_USER_ID = 111111111


def _chat() -> Chat:
    return Chat(id=CHAT_ID, type="supergroup")


def _user() -> User:
    return User(id=TELEGRAM_USER_ID, is_bot=False, first_name="Alice")


def _message(**overrides: object) -> Message:
    base: dict[str, object] = {
        "message_id": 1,
        "date": datetime.now(tz=UTC),
        "chat": _chat(),
        "from_user": _user(),
    }
    base.update(overrides)
    return Message(**base)  # type: ignore[arg-type]


def test_message_without_from_user_maps_to_none() -> None:
    message = _message(from_user=None, text="/ping")
    assert to_incoming(update_id=1, message=message) is None


def test_voice_message_maps_to_voice_kind_with_file_id_and_caption() -> None:
    voice = Voice(file_id="voice-file-id", file_unique_id="voice-unique-id", duration=3)
    message = _message(voice=voice, caption="groceries")

    incoming = to_incoming(update_id=1001, message=message)

    assert incoming is not None
    assert incoming.kind == MessageKind.VOICE
    assert incoming.file_id == "voice-file-id"
    assert incoming.raw_text == "groceries"


def test_photo_message_maps_to_photo_kind_using_largest_rendition() -> None:
    small = PhotoSize(file_id="small", file_unique_id="small-unique", width=90, height=90)
    large = PhotoSize(file_id="large", file_unique_id="large-unique", width=1280, height=1280)
    message = _message(photo=[small, large], caption="receipt")

    incoming = to_incoming(update_id=1002, message=message)

    assert incoming is not None
    assert incoming.kind == MessageKind.PHOTO
    assert incoming.file_id == "large"
    assert incoming.raw_text == "receipt"


def test_captioned_photo_is_photo_kind_not_text() -> None:
    photo = PhotoSize(file_id="only", file_unique_id="only-unique", width=100, height=100)
    message = _message(photo=[photo], caption="bread 50", text=None)

    incoming = to_incoming(update_id=1003, message=message)

    assert incoming is not None
    assert incoming.kind == MessageKind.PHOTO


def test_text_message_maps_to_text_kind() -> None:
    message = _message(text="bread 50")

    incoming = to_incoming(update_id=1004, message=message)

    assert incoming is not None
    assert incoming.kind == MessageKind.TEXT
    assert incoming.raw_text == "bread 50"
    assert incoming.file_id is None


def test_unsupported_content_maps_to_none() -> None:
    sticker = Sticker(
        file_id="sticker-file-id",
        file_unique_id="sticker-unique-id",
        type="regular",
        width=512,
        height=512,
        is_animated=False,
        is_video=False,
    )
    message = _message(sticker=sticker)

    assert to_incoming(update_id=1005, message=message) is None


def test_incoming_message_carries_ids_display_name_and_chat_id() -> None:
    message = _message(text="bread 50", message_id=42)

    incoming = to_incoming(update_id=1006, message=message)

    assert incoming is not None
    assert incoming.telegram_update_id == 1006
    assert incoming.telegram_message_id == 42
    assert incoming.chat_id == CHAT_ID
    assert incoming.telegram_user_id == TELEGRAM_USER_ID
    assert incoming.display_name == "Alice"
