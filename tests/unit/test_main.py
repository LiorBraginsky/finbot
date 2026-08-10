"""Unit tests for finbot.adapters.telegram.main.build_dispatcher.

No Docker, no network: the sessionmaker's engine is never connected, only
stored by DbSessionMiddleware.
"""

from finbot.adapters.telegram.main import build_dispatcher
from finbot.adapters.telegram.polling import ALLOWED_UPDATES
from finbot.repo.engine import create_sessionmaker


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
