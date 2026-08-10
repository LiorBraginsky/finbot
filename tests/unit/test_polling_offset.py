"""Unit tests for finbot.adapters.telegram.polling.run_polling: the three
exception outcomes, not just the happy path (docs/plans/stage-1-text-to-
expense.md's method note) — no Docker, no database, no dispatcher, no
network. `feed` and `bot` are both plain test doubles.
"""

import asyncio
from datetime import UTC, datetime
from typing import cast

import pytest
from aiogram import Bot
from aiogram.methods import GetUpdates
from aiogram.types import Update
from aiogram.utils import backoff as backoff_module
from aiogram.utils.backoff import BackoffConfig

from finbot.adapters.telegram.errors import PersistenceError
from finbot.adapters.telegram.polling import run_polling

# Real seconds would make this suite slow for no reason: the loop's own
# behaviour is under test, not the pacing of aiogram's Backoff.
_FAST_BACKOFF = BackoffConfig(min_delay=0.0, max_delay=0.001, factor=2.0, jitter=0.0)


def _update(update_id: int) -> Update:
    return Update(update_id=update_id)


class _ScriptedBot:
    """Returns one scripted batch of updates per `GetUpdates` call, in
    order, and records the `offset` each call carried. Once every batch is
    exhausted it sets `stop` and returns an empty batch, so a test needs no
    separate mechanism to end the loop.
    """

    def __init__(self, batches: list[list[Update]], stop: asyncio.Event) -> None:
        self._batches = list(batches)
        self._stop = stop
        self.offsets_used: list[int | None] = []

    async def __call__(
        self, method: GetUpdates, request_timeout: int | None = None
    ) -> list[Update]:
        self.offsets_used.append(method.offset)
        if not self._batches:
            self._stop.set()
            return []
        return self._batches.pop(0)


async def test_offset_advances_past_a_fully_successful_batch() -> None:
    stop = asyncio.Event()
    updates = [_update(1), _update(2), _update(3)]
    bot = _ScriptedBot([updates], stop)
    fed: list[int] = []

    async def feed(update: Update) -> None:
        fed.append(update.update_id)

    await run_polling(
        bot=cast(Bot, bot), feed=feed, stop=stop, poll_timeout=0, backoff_config=_FAST_BACKOFF
    )

    assert fed == [1, 2, 3]
    # The *next* GetUpdates call — the one that found nothing left to do and
    # stopped the loop — carries the acknowledged offset.
    assert bot.offsets_used[1] == updates[-1].update_id + 1


async def test_persistence_error_withholds_the_offset_and_never_feeds_the_rest() -> None:
    stop = asyncio.Event()
    updates = [_update(11), _update(12), _update(13)]
    bot = _ScriptedBot([updates], stop)
    fed: list[int] = []

    async def feed(update: Update) -> None:
        if update.update_id == 12:
            raise PersistenceError("durable write failed")
        fed.append(update.update_id)

    await run_polling(
        bot=cast(Bot, bot), feed=feed, stop=stop, poll_timeout=0, backoff_config=_FAST_BACKOFF
    )

    assert fed == [11]  # 12 raised; 13 was never fed
    # The retry carries the *same* offset as the call that fetched the
    # failed batch — nothing was acknowledged.
    assert bot.offsets_used[1] == bot.offsets_used[0]


async def test_a_non_persistence_error_still_advances_the_offset_past_the_batch() -> None:
    stop = asyncio.Event()
    updates = [_update(21), _update(22), _update(23)]
    bot = _ScriptedBot([updates], stop)
    fed: list[int] = []

    async def feed(update: Update) -> None:
        if update.update_id == 22:
            raise ValueError("a handler bug, not a persistence failure")
        fed.append(update.update_id)

    await run_polling(
        bot=cast(Bot, bot), feed=feed, stop=stop, poll_timeout=0, backoff_config=_FAST_BACKOFF
    )

    # 22's own exception is swallowed, but 23 still runs: a handler bug must
    # not wedge the household's bot.
    assert fed == [21, 23]
    assert bot.offsets_used[1] == updates[-1].update_id + 1


async def test_repeated_persistence_errors_ramp_up_the_backoff_not_pinned_at_min_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MAJOR 4: `backoff.reset()` used to run right after every successful
    `getUpdates`, before the batch was ever acknowledged — so a
    `PersistenceError`, however many times in a row, always slept ~1s
    (`min_delay`) instead of ramping up like any other repeated failure.
    Patches `aiogram.utils.backoff`'s own `asyncio.sleep` (not this module's)
    to record the delays `Backoff.asleep()` actually requests, without
    spending real seconds on them.
    """
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(backoff_module.asyncio, "sleep", fake_sleep)

    stop = asyncio.Event()
    updates = [_update(1)]
    calls = {"n": 0}

    class _AlwaysRedeliveredBot:
        async def __call__(
            self, method: GetUpdates, request_timeout: int | None = None
        ) -> list[Update]:
            calls["n"] += 1
            if calls["n"] > 4:
                stop.set()
                return []
            return updates

    async def feed(update: Update) -> None:
        raise PersistenceError("durable write failed")

    # Deterministic: jitter=0.0 makes normalvariate(mu, 0) return mu exactly,
    # so the four delays are exactly 1.0, 2.0, 4.0, 8.0 if (and only if)
    # reset() is never called between them.
    real_shaped_backoff = BackoffConfig(min_delay=1.0, max_delay=60.0, factor=2.0, jitter=0.0)

    await run_polling(
        bot=cast(Bot, _AlwaysRedeliveredBot()),
        feed=feed,
        stop=stop,
        poll_timeout=0,
        backoff_config=real_shaped_backoff,
    )

    assert delays == [1.0, 2.0, 4.0, 8.0]


async def test_stop_mid_poll_returns_promptly_without_waiting_for_getupdates() -> None:
    """MAJOR 6: `stop` must win a race against a `getUpdates` call that has
    not returned yet — `docker stop`'s default grace period (10s) is shorter
    than `poll_timeout` (25s), so without this a SIGTERM mid-poll would make
    `stop.is_set()` true while `run_polling` kept blocking on the long poll
    regardless, turning SIGKILL into the *normal* shutdown path.
    """
    stop = asyncio.Event()
    started = asyncio.Event()

    class _NeverRespondingBot:
        async def __call__(
            self, method: GetUpdates, request_timeout: int | None = None
        ) -> list[Update]:
            started.set()
            await asyncio.sleep(3600)  # longer than any timeout below
            return []  # pragma: no cover -- never reached

    async def feed(update: Update) -> None:
        pass

    task = asyncio.create_task(
        run_polling(bot=cast(Bot, _NeverRespondingBot()), feed=feed, stop=stop, poll_timeout=25)
    )
    await started.wait()
    stop.set()

    # If `stop` did not race the poll, this would hang for 3600s instead.
    await asyncio.wait_for(task, timeout=1.0)


@pytest.fixture(autouse=True)
def _no_real_clock_drift() -> None:
    # Sanity check on _FAST_BACKOFF itself: if this ever regresses to real
    # delays, every test above would still pass, just slowly and silently.
    started = datetime.now(tz=UTC)
    yield
    elapsed = (datetime.now(tz=UTC) - started).total_seconds()
    assert elapsed < 1.0
