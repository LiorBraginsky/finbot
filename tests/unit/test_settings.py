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
        "openrouter_api_key": "sk-or-fake-not-a-real-key",
        "model_text": "google/gemini-3.5-flash-lite",
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
    monkeypatch.setenv("MODEL_VOICE", "some/voice-model")
    settings = Settings(**_kwargs())
    assert settings.allowed_user_ids == frozenset({111, 222})


def test_model_candidates_strips_blanks_and_whitespace() -> None:
    settings = Settings(**_kwargs(model_fallbacks="a, b ,"))
    assert settings.model_candidates == ("google/gemini-3.5-flash-lite", "a", "b")


def test_model_candidates_is_just_model_text_when_no_fallbacks() -> None:
    settings = Settings(**_kwargs())
    assert settings.model_candidates == ("google/gemini-3.5-flash-lite",)


def test_free_model_text_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        Settings(**_kwargs(model_text="qwen/qwen3.7-flash:free"))


def test_free_model_fallback_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        Settings(**_kwargs(model_fallbacks="qwen/qwen3.7-flash:free"))


def test_unresolvable_timezone_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        Settings(**_kwargs(timezone="Nowhere/Nope"))


def test_tz_resolves_the_configured_timezone() -> None:
    settings = Settings(**_kwargs(timezone="Europe/Kyiv"))
    assert str(settings.tz) == "Europe/Kyiv"


def test_max_extraction_attempts_out_of_range_raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        Settings(**_kwargs(max_extraction_attempts="4"))
    with pytest.raises(ValidationError):
        Settings(**_kwargs(max_extraction_attempts="0"))
