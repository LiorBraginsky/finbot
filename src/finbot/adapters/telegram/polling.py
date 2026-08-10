"""Owns the polling loop and closes ADR-0013's delivery guarantee: an update
is acknowledged — the offset advances — only once `feed` has run for it
without raising `PersistenceError`. `PersistenceError` can only originate
from `PersistMessageMiddleware`'s durable write (`middlewares.py`); every
other failure `feed` might raise is logged and the loop moves on, because
one broken reply must not wedge the household's bot forever.

`feed` is a parameter rather than `dp.feed_update` baked in, which is what
lets `tests/unit/test_polling_offset.py` prove the guarantee with no
Dispatcher, no database and no network — it distinguishes exactly two
exception classes and nothing else. `main.py` passes
`partial(dp.feed_update, bot)`.

Replaces `dp.start_polling`, per the plan's Reality check: aiogram's own
polling loop (`Dispatcher._listen_updates`/`_polling`) advances the offset
right after fetching updates and dispatches handlers as background tasks —
by the time a handler raises, the offset has already moved. There is no
keyword argument that changes this; the loop has to be replaced.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Final

from aiogram import Bot
from aiogram.methods import GetUpdates
from aiogram.types import Update
from aiogram.utils.backoff import Backoff, BackoffConfig

from finbot.adapters.telegram.errors import PersistenceError

logger = logging.getLogger(__name__)

# callback_query added to Stage 0's ["message"]: without it every ✏️/🗑 tap
# is dropped by Telegram before it ever reaches us — no log, no reply (the
# plan's Reality check). tests/unit/test_main.py pins this against
# Dispatcher.resolve_used_update_types() so the two cannot silently diverge.
ALLOWED_UPDATES: Final[list[str]] = ["callback_query", "message"]

DEFAULT_BACKOFF_CONFIG: Final[BackoffConfig] = BackoffConfig(
    min_delay=1.0, max_delay=60.0, factor=2.0, jitter=0.1
)

Feed = Callable[[Update], Awaitable[None]]


async def run_polling(
    *,
    bot: Bot,
    feed: Feed,
    stop: asyncio.Event,
    poll_timeout: int = 25,
    backoff_config: BackoffConfig = DEFAULT_BACKOFF_CONFIG,
) -> None:
    """`backoff_config` is a testability seam, not in the plan's own sketch:
    the production default matches it exactly, but
    `tests/unit/test_polling_offset.py` overrides it with near-zero delays
    so the three exception-handling paths it proves run in milliseconds
    rather than real seconds.
    """
    offset: int | None = None
    backoff = Backoff(backoff_config)
    while not stop.is_set():
        try:
            updates = await bot(
                GetUpdates(offset=offset, timeout=poll_timeout, allowed_updates=ALLOWED_UPDATES),
                request_timeout=poll_timeout + 15,
            )
        except Exception:
            logger.exception("getUpdates failed")
            await backoff.asleep()
            continue
        backoff.reset()

        try:
            for update in updates:
                try:
                    await feed(update)
                except PersistenceError:
                    raise  # abort the batch; offset unchanged below
                except Exception:
                    logger.exception("handler failed for update_id=%s", update.update_id)
        except PersistenceError:
            logger.exception("withholding offset; telegram will redeliver")
            await backoff.asleep()
            continue  # offset NOT advanced

        if updates:
            offset = updates[-1].update_id + 1  # acknowledged only now
