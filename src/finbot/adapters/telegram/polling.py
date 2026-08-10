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
import contextlib
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


async def _sleep_or_stop(delay: float, stop: asyncio.Event) -> None:
    """`asyncio.sleep(delay)`, but returns as soon as `stop` fires instead of
    waiting the delay out — `drain_loop`'s idle wait already uses exactly
    this idiom. `Backoff.asleep()` has no such option, and `max_delay`
    (60s, `DEFAULT_BACKOFF_CONFIG`) exceeds `stop_grace_period` (45s,
    infra/docker-compose.yml): without this, a SIGTERM landing during a
    maxed-out backoff — precisely the kind of outage where the drain may be
    mid-`llm.complete()` — would still end in SIGKILL rather than a clean
    shutdown.
    """
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=delay)


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
        # Raced against `stop`, not just awaited directly: `docker stop`'s
        # grace period (10s, infra/docker-compose.yml) is shorter than
        # `poll_timeout` (25s), so without this a SIGTERM mid-poll would
        # make `stop.is_set()` true but this call would still block up to
        # `poll_timeout + 15`s — SIGKILL, not graceful shutdown, would be the
        # *normal* shutdown path rather than the exceptional one.
        get_updates_task = asyncio.ensure_future(
            bot(
                GetUpdates(offset=offset, timeout=poll_timeout, allowed_updates=ALLOWED_UPDATES),
                request_timeout=poll_timeout + 15,
            )
        )
        stop_wait_task = asyncio.ensure_future(stop.wait())
        try:
            done, _pending = await asyncio.wait(
                {get_updates_task, stop_wait_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if get_updates_task not in done:
                # stop fired first: abandon the long poll rather than wait
                # it out.
                get_updates_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await get_updates_task
                break

            try:
                updates = get_updates_task.result()
            except Exception:
                logger.exception("getUpdates failed")
                # next(backoff), not backoff.asleep(): the delay still has
                # to be computed (and the backoff's state still advanced)
                # the same way, but the actual wait must be interruptible —
                # see _sleep_or_stop.
                await _sleep_or_stop(next(backoff), stop)
                continue
        finally:
            if not stop_wait_task.done():
                stop_wait_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await stop_wait_task

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
            # backoff.reset() is deliberately *not* called on this path (see
            # below): a repeated withhold must ramp up like any other
            # failure, not stay pinned at min_delay.
            await _sleep_or_stop(next(backoff), stop)
            continue  # offset NOT advanced

        if updates:
            offset = updates[-1].update_id + 1  # acknowledged only now
        # Reset only now, after the batch is fully acknowledged — not right
        # after a successful `getUpdates` (the bug this replaces): resetting
        # earlier meant a PersistenceError, however many times in a row,
        # always slept ~min_delay (Postgres down => ~3600 iterations/hour,
        # each logging a full `update.model_dump_json()`, into the same disk
        # Postgres needs to recover on).
        backoff.reset()
