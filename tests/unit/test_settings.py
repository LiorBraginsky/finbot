"""Unit tests for finbot.config.Settings. No Docker, no real environment access."""

import pytest
from pydantic import ValidationError

from finbot.config import Settings


def _kwargs(**overrides: str) -> dict[str, str]:
    base = {
        "telegram_bot_token": "42:TESTTOKEN",
        "telegram_allowed_user_ids": "111,222",
        "telegram_chat_id": "-1001111111111",
        "database_url": "postgresql+asyncpg://finbot:pw@localhost:5432/finbot",
    }
    base.update(overrides)
    return base


def test_allowed_user_ids_parses_comma_separated_ids() -> None:
    settings = Settings(**_kwargs())
    assert settings.allowed_user_ids == frozenset({111, 222})


def test_allowed_user_ids_tolerates_surrounding_whitespace() -> None:
    settings = Settings(**_kwargs(telegram_allowed_user_ids=" 111 , 222 "))
    assert settings.allowed_user_ids == frozenset({111, 222})


def test_empty_allowlist_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        Settings(**_kwargs(telegram_allowed_user_ids=""))


def test_non_numeric_allowlist_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        Settings(**_kwargs(telegram_allowed_user_ids="abc"))


def test_unknown_env_keys_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake-not-a-real-key")
    settings = Settings(**_kwargs())
    assert settings.allowed_user_ids == frozenset({111, 222})
