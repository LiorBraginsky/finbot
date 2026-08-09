"""Outer middlewares on ``dp.update``, run for every update whether or not a
handler matches. Registration order in ``main.build_dispatcher`` is execution
order: reject strangers before opening a database session for them.
"""

import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finbot.adapters.telegram.mapping import to_incoming
from finbot.repo import messages, users

logger = logging.getLogger(__name__)

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


def _require_update(event: TelegramObject) -> Update:
    """Narrow TelegramObject to Update for mypy.

    These middlewares are only ever registered on ``dp.update`` (see
    ``main.build_dispatcher``), so this always succeeds in practice; a
    mismatch means a wiring bug and should fail loudly, not silently pass
    through the wrong event shape.
    """
    if not isinstance(event, Update):
        msg = f"expected aiogram.types.Update, got {type(event).__name__}"
        raise TypeError(msg)
    return event


class AllowlistMiddleware(BaseMiddleware):
    """Silently drops updates from anyone not in `allowed`.

    Reads `event.message` and `message.from_user` directly rather than
    `data["event_from_user"]`, so it does not depend on aiogram's internal
    middleware ordering. No reply, no INFO log: silence is the design
    (spec §7) — an "access denied" reply is an invitation to keep poking.
    """

    def __init__(self, allowed: frozenset[int]) -> None:
        self._allowed = allowed

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        update = _require_update(event)
        message = update.message
        if message is None or message.from_user is None:
            return None
        if message.from_user.id not in self._allowed:
            return None

        return await handler(event, data)


class DbSessionMiddleware(BaseMiddleware):
    """Opens one session per update; logs at ERROR then rolls back on failure.

    Per ADR-0011, aiogram has already advanced the getUpdates offset by the
    time a handler raises, so the serialised update logged here is the only
    remaining copy of a failed write. That guarantee only holds if the log
    line is unconditional: logging before the rollback, and suppressing any
    exception the rollback itself raises (e.g. Postgres being down, the exact
    scenario ADR-0011 is about), means a broken connection can never swallow
    the log line or replace the original exception.
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        update = _require_update(event)
        async with self._sessionmaker() as session:
            data["session"] = session
            try:
                return await handler(event, data)
            except Exception:
                logger.exception(
                    "failed to process update_id=%s: %s",
                    update.update_id,
                    update.model_dump_json(exclude_none=True),
                )
                with contextlib.suppress(Exception):
                    await session.rollback()
                raise


class PersistMessageMiddleware(BaseMiddleware):
    """Persists every whitelisted message before any handler runs.

    Commits before calling the handler on purpose: spec §4 step 6, "write to
    the database before replying." Vacuous for `/ping`, load-bearing from
    Stage 1.
    """

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        update = _require_update(event)
        if update.message is None:
            return None

        incoming = to_incoming(update_id=update.update_id, message=update.message)
        if incoming is None:
            logger.info("ignored unsupported content in update_id=%s", update.update_id)
            return None

        session: AsyncSession = data["session"]
        user_id = await users.get_or_create(
            session, incoming.telegram_user_id, incoming.display_name
        )
        row_id = await messages.add_if_new(session, incoming, user_id)
        await session.commit()

        data["message_row_id"] = row_id
        data["is_duplicate"] = row_id is None

        return await handler(event, data)
