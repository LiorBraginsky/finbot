"""Proves the middleware half of ADR-0013's delivery guarantee: the durable
write really does raise `PersistenceError`, and it propagates out of
`feed_raw_update` rather than being swallowed anywhere on the way out.

No Docker: the sessionmaker points at a connection Postgres never answers
(`127.0.0.1:1` refuses immediately), so the failure is fast and
deterministic. `tests/unit/test_polling_offset.py` proves the other half —
that `run_polling` reacts to `PersistenceError` correctly — without a
network at all. Together the two prove the guarantee end to end.
"""

import pytest
from aiogram import Bot

from finbot.adapters.telegram.errors import PersistenceError
from finbot.adapters.telegram.main import build_dispatcher
from finbot.repo.engine import create_sessionmaker
from tests.support.fake_session import FakeSession
from tests.support.updates import ALLOWED_USER_ID, text_update

_REFUSED_CONNECTION_URL = "postgresql+asyncpg://x:x@127.0.0.1:1/none"


async def test_persistence_error_propagates_out_of_feed_raw_update() -> None:
    sessionmaker = create_sessionmaker(_REFUSED_CONNECTION_URL)
    dp = build_dispatcher(sessionmaker, frozenset({ALLOWED_USER_ID}))
    bot = Bot(token="42:TESTTOKEN", session=FakeSession())
    update = text_update(update_id=1, text="хліб 50")

    with pytest.raises(PersistenceError):
        await dp.feed_raw_update(bot, update)
