"""Bot entrypoint.

``build_dispatcher`` is the point: this module and the integration tests both
call it, so the object graph under test cannot drift from the one that runs
in production.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finbot.adapters.telegram.handlers import build_router
from finbot.adapters.telegram.middlewares import (
    AllowlistMiddleware,
    DbSessionMiddleware,
    PersistMessageMiddleware,
)
from finbot.config import Settings
from finbot.repo.engine import create_sessionmaker


def build_dispatcher(
    sessionmaker: async_sessionmaker[AsyncSession],
    allowed_user_ids: frozenset[int],
) -> Dispatcher:
    """Wire allowlist -> db session -> persistence -> handlers, in that order.

    Registration order is execution order: reject strangers before opening a
    database session for them.
    """
    dp = Dispatcher()
    dp.update.outer_middleware(AllowlistMiddleware(allowed_user_ids))
    dp.update.outer_middleware(DbSessionMiddleware(sessionmaker))
    dp.update.outer_middleware(PersistMessageMiddleware())
    dp.include_router(build_router())
    return dp


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings()
    dp = build_dispatcher(create_sessionmaker(settings.database_url), settings.allowed_user_ids)
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    await dp.start_polling(bot, allowed_updates=["message"])


if __name__ == "__main__":
    asyncio.run(main())
