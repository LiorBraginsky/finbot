"""Bot entrypoint.

``build_dispatcher`` is the point: this module and the integration tests both
call it, so the object graph under test cannot drift from the one that runs
in production.
"""

import asyncio
import contextlib
import logging
import signal
from functools import partial
from typing import Final
from zoneinfo import ZoneInfo

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from finbot.adapters.telegram.handlers import build_router
from finbot.adapters.telegram.middlewares import (
    AllowlistMiddleware,
    DbSessionMiddleware,
    PersistMessageMiddleware,
)
from finbot.adapters.telegram.polling import run_polling
from finbot.adapters.telegram.runner import drain_loop
from finbot.config import Settings
from finbot.llm.openrouter import OpenRouterClient
from finbot.repo import messages
from finbot.repo.engine import create_sessionmaker

logger = logging.getLogger(__name__)

# Matches Settings.timezone's own default: build_dispatcher's tz keyword
# exists so tests/unit/test_main.py can keep calling it with only the two
# positional arguments Stage 0 already used, with no Settings object in
# sight.
_DEFAULT_TZ = ZoneInfo("Europe/Kyiv")

# /ping is deliberately absent: it is a debugging aid, not something either
# household member needs in the menu. `/help` documents itself, so its own
# description is short — the full text lives in `render.HELP_TEXT`.
BOT_COMMANDS: Final[list[BotCommand]] = [
    BotCommand(command="day", description="витрати за сьогодні"),
    BotCommand(command="week", description="за тиждень"),
    BotCommand(command="month", description="за місяць"),
    BotCommand(command="help", description="що я вмію"),
]


async def register_commands(bot: Bot) -> None:
    """Scoped to `AllGroupChats`, not the default scope: this bot only ever
    runs in the household's group (docs/vision.md), and the default scope
    also covers private chats the bot is never added to.
    """
    await bot.set_my_commands(commands=BOT_COMMANDS, scope=BotCommandScopeAllGroupChats())


def build_dispatcher(
    sessionmaker: async_sessionmaker[AsyncSession],
    allowed_user_ids: frozenset[int],
    tz: ZoneInfo = _DEFAULT_TZ,
) -> Dispatcher:
    """Wire allowlist -> db session -> persistence -> handlers, in that order.

    Registration order is execution order: reject strangers before opening a
    database session for them.

    No `dp.errors` handler is registered anywhere, and none ever should be:
    aiogram's `ErrorsMiddleware` sits outermost on `dp.update` and re-raises
    an exception only while no error handler exists (verified in the
    installed source — see the plan's Reality check). Registering one would
    make it swallow `PersistenceError` instead, silently voiding ADR-0013's
    delivery guarantee. `tests/unit/test_main.py::
    test_no_global_error_handler_is_registered` forbids it mechanically.
    """
    dp = Dispatcher()
    dp.update.outer_middleware(AllowlistMiddleware(allowed_user_ids))
    dp.update.outer_middleware(DbSessionMiddleware(sessionmaker))
    dp.update.outer_middleware(PersistMessageMiddleware())
    dp.include_router(build_router(tz))
    return dp


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings()
    sessionmaker = create_sessionmaker(settings.database_url)

    async with sessionmaker() as session:
        reset_count = await messages.reset_processing(session)
        await session.commit()
    if reset_count:
        logger.warning("reset %d message(s) stuck in 'processing' from a previous run", reset_count)

    async with aiohttp.ClientSession() as http:
        llm = OpenRouterClient(
            session=http,
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            timeout_seconds=settings.llm_timeout_seconds,
        )
        bot = Bot(token=settings.telegram_bot_token.get_secret_value())
        await register_commands(bot)
        dp = build_dispatcher(sessionmaker, settings.allowed_user_ids, settings.tz)
        stop = asyncio.Event()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                # Not implemented on Windows; irrelevant for this project's
                # only deployment target (docker compose on Linux, ADR-0002).
                loop.add_signal_handler(sig, stop.set)

        async with asyncio.TaskGroup() as tg:
            tg.create_task(
                run_polling(bot=bot, feed=partial(dp.feed_update, bot), stop=stop),
                name="polling",
            )
            tg.create_task(
                drain_loop(
                    bot=bot, sessionmaker=sessionmaker, llm=llm, settings=settings, stop=stop
                ),
                name="drain",
            )


if __name__ == "__main__":
    asyncio.run(main())
