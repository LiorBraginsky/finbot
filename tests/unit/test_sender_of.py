"""Table-driven over every `Update` shape that can carry a user, plus the
ones that carry none — see docs/plans/stage-1-text-to-expense.md 3.1's
method note: derive cases from the grammar of `Update`, not from a list of
examples someone happened to think of.

The trap this file exists to catch: `callback_query.message.from_user` is
the BOT that sent the confirmation message, not whoever tapped the button.
`_message(from_user=_BOT)` below puts the bot in exactly that field, so the
trap is present in the test data, not only in prose.
"""

from datetime import UTC, datetime

import pytest
from aiogram.types import CallbackQuery, Chat, InaccessibleMessage, Message, Update, User

from finbot.adapters.telegram.mapping import sender_of

_TAPPER = User(id=111111111, is_bot=False, first_name="Alice")
_BOT = User(id=222222222, is_bot=True, first_name="finbot", username="finbot_bot")


def _chat() -> Chat:
    return Chat(id=-1001111111111, type="supergroup")


def _message(**overrides: object) -> Message:
    base: dict[str, object] = {
        "message_id": 1,
        "date": datetime.now(tz=UTC),
        "chat": _chat(),
        "from_user": _TAPPER,
        "text": "хліб 50",
    }
    base.update(overrides)
    return Message(**base)  # type: ignore[arg-type]


def _callback_update(message: Message | InaccessibleMessage | None, *, update_id: int) -> Update:
    query = CallbackQuery(
        id="cbq-1",
        from_user=_TAPPER,
        chat_instance="chat-instance",
        message=message,
        data="exp:del:1",
    )
    return Update(update_id=update_id, callback_query=query)


@pytest.mark.parametrize(
    ("build_update", "expected_sender_id"),
    [
        pytest.param(
            lambda: Update(update_id=1, message=_message()),
            _TAPPER.id,
            id="text-message-resolves-to-its-sender",
        ),
        pytest.param(
            lambda: _callback_update(_message(from_user=_BOT), update_id=2),
            _TAPPER.id,
            id="callback-on-bot-owned-message-resolves-to-the-tapper-not-the-bot",
        ),
        pytest.param(
            lambda: _callback_update(InaccessibleMessage(chat=_chat(), message_id=1), update_id=3),
            _TAPPER.id,
            id="callback-on-an-inaccessible-message-still-resolves-to-the-tapper",
        ),
        pytest.param(
            lambda: _callback_update(None, update_id=4),
            _TAPPER.id,
            id="callback-with-no-message-at-all-still-resolves-to-the-tapper",
        ),
        pytest.param(
            lambda: Update(update_id=5),
            None,
            id="update-with-neither-message-nor-callback-query-is-none",
        ),
        pytest.param(
            lambda: Update(update_id=6, message=_message(from_user=None)),
            None,
            id="message-with-no-from-user-is-none",
        ),
    ],
)
def test_sender_of_covers_every_update_shape(build_update, expected_sender_id: int | None) -> None:
    resolved = sender_of(build_update())

    if expected_sender_id is None:
        assert resolved is None
    else:
        assert resolved is not None
        assert resolved.id == expected_sender_id


def test_callback_on_bot_owned_message_is_not_the_bot() -> None:
    """The trap, made explicit rather than merely implied by the table above:
    the tapper and `callback_query.message.from_user` really do differ.
    """
    bot_owned_message = _message(from_user=_BOT)
    update = _callback_update(bot_owned_message, update_id=7)

    resolved = sender_of(update)

    assert resolved is not None
    assert resolved.id == _TAPPER.id
    assert update.callback_query is not None  # narrows for mypy
    assert isinstance(update.callback_query.message, Message)
    assert update.callback_query.message.from_user is not None
    assert update.callback_query.message.from_user.id == _BOT.id
    assert resolved.id != update.callback_query.message.from_user.id
