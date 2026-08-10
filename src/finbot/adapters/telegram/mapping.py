"""The only place that knows aiogram's shape.

Translates an aiogram ``Message`` into ``finbot.core.models.IncomingMessage``, or
``None`` when the message is nothing Stage 0 has a schema slot for.
"""

from aiogram.types import Message, Update, User

from finbot.core.models import IncomingMessage, MessageKind


def sender_of(update: Update) -> User | None:
    """Who this update is from, for the allowlist.

    For a callback query this is `callback_query.from_user` — the person who
    TAPPED. `callback_query.message.from_user` is the BOT that sent the
    confirmation, and checking it would reject both household members: the
    Stage-1 trap this function exists to close. Reads the `Update` directly
    rather than `data["event_from_user"]`, keeping Stage 0's rule that
    nothing depends on aiogram's internal middleware registration order.

    `None` for an update with no message and no callback query, or a message
    with no `from_user` (e.g. a channel post) — there is no one to check
    against the allowlist.
    """
    if update.message is not None:
        return update.message.from_user
    if update.callback_query is not None:
        return update.callback_query.from_user
    return None


def to_incoming(update_id: int, message: Message) -> IncomingMessage | None:
    """Map a raw Telegram message to the domain shape, or None to ignore it.

    Voice and photo are checked before text: a captioned photo carries both
    ``photo`` and (as its caption) text-shaped content, and the photo is what
    matters. Anything else — sticker, document, video, service message — has
    no Stage 0 schema slot and is silently ignored, not persisted.
    """
    if message.from_user is None:
        return None

    kind: MessageKind
    raw_text: str | None
    file_id: str | None
    duration_seconds: int | None = None

    if message.voice is not None:
        kind = MessageKind.VOICE
        file_id = message.voice.file_id
        raw_text = message.caption
        duration_seconds = message.voice.duration
    elif message.photo is not None:
        kind = MessageKind.PHOTO
        file_id = message.photo[-1].file_id
        raw_text = message.caption
    elif message.text is not None:
        kind = MessageKind.TEXT
        raw_text = message.text
        file_id = None
    else:
        return None

    return IncomingMessage(
        telegram_update_id=update_id,
        telegram_message_id=message.message_id,
        chat_id=message.chat.id,
        telegram_user_id=message.from_user.id,
        display_name=message.from_user.full_name,
        kind=kind,
        raw_text=raw_text,
        file_id=file_id,
        duration_seconds=duration_seconds,
    )
