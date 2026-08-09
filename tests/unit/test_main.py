"""Unit tests for finbot.adapters.telegram.main.build_dispatcher.

No Docker, no network: the sessionmaker's engine is never connected, only
stored by DbSessionMiddleware.
"""

from finbot.adapters.telegram.main import build_dispatcher
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
