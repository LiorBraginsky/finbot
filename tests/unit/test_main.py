"""Unit tests for finbot.adapters.telegram.main.

No Docker, no network: build_dispatcher's sessionmaker's engine is never
connected, only stored by DbSessionMiddleware; register_commands is proven
against `FakeSession` (tests/support/fake_session.py), the same fake the
integration suite uses for a real `Bot` with no socket opened.
"""

from typing import cast

from aiogram import Bot
from aiogram.methods import SetMyCommands
from aiogram.types import BotCommandScopeAllGroupChats

from finbot.adapters.telegram.main import (
    BOT_COMMANDS,
    build_dispatcher,
    register_commands,
)
from finbot.adapters.telegram.polling import ALLOWED_UPDATES
from finbot.repo.engine import create_sessionmaker
from tests.support.fake_session import FakeSession


def test_outer_middlewares_run_allowlist_then_db_session_then_persist() -> None:
    """Pins registration order, not just its effect.

    build_dispatcher's docstring says registration order is execution order:
    reject strangers before opening a database session for them, and open the
    session before persisting. A behavioural test like
    test_stranger_is_ignored_silently would still pass if AllowlistMiddleware
    and DbSessionMiddleware were swapped, because AllowlistMiddleware alone
    stops a stranger from reaching a handler either way. This asserts the
    order directly, by type name, on the actual dp.update.outer_middleware
    chain the production entrypoint builds.
    """
    sessionmaker = create_sessionmaker("postgresql+asyncpg://user:pw@localhost/db")
    dp = build_dispatcher(sessionmaker, frozenset({1}))

    ours = {"AllowlistMiddleware", "DbSessionMiddleware", "PersistMessageMiddleware"}
    order = [
        type(middleware).__name__
        for middleware in dp.update.outer_middleware
        if type(middleware).__name__ in ours
    ]

    assert order == ["AllowlistMiddleware", "DbSessionMiddleware", "PersistMessageMiddleware"]


def test_allowed_updates_matches_registered_handlers() -> None:
    """ALLOWED_UPDATES is what we ask Telegram for; resolve_used_update_types()
    is what the router can actually handle. Stage 0 shipped ["message"] while
    Stage 1 needs callback_query — every ✏️ tap would have been dropped by
    Telegram before reaching us, with no log and no reply. This equality is
    what makes that class of bug unmergeable. If they ever diverge
    legitimately, change this test deliberately and say why in the journal.
    """
    sessionmaker = create_sessionmaker("postgresql+asyncpg://user:pw@localhost/db")
    dp = build_dispatcher(sessionmaker, frozenset({1}))

    assert sorted(ALLOWED_UPDATES) == dp.resolve_used_update_types()


def test_no_global_error_handler_is_registered() -> None:
    """A dp.errors handler would make aiogram's outermost ErrorsMiddleware
    swallow PersistenceError instead of re-raising it, silently voiding
    ADR-0013's delivery guarantee. Handle failures inside handlers instead.
    """
    sessionmaker = create_sessionmaker("postgresql+asyncpg://user:pw@localhost/db")
    dp = build_dispatcher(sessionmaker, frozenset({1}))

    assert dp.errors.handlers == []


async def test_register_commands_sets_the_menu_scoped_to_group_chats() -> None:
    """`BotCommandScopeAllGroupChats`, not the default scope: this bot only
    ever runs in the household's group (docs/vision.md), and the default
    scope also covers private chats it is never added to.
    """
    bot = Bot(token="42:TESTTOKEN", session=FakeSession())

    await register_commands(bot)

    sent = [r for r in cast(FakeSession, bot.session).requests if isinstance(r, SetMyCommands)]
    assert len(sent) == 1
    assert sent[0].commands == BOT_COMMANDS
    assert isinstance(sent[0].scope, BotCommandScopeAllGroupChats)


def test_bot_commands_matches_the_day_week_month_help_menu_exactly() -> None:
    """Pins the exact command/description pairs the plan specifies — a
    change here is a deliberate menu edit, not an accidental typo.
    """
    assert [(c.command, c.description) for c in BOT_COMMANDS] == [
        ("day", "витрати за сьогодні"),
        ("week", "за тиждень"),
        ("month", "за місяць"),
        ("help", "що я вмію"),
    ]
