"""Application configuration, loaded from the environment / .env."""

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    telegram_bot_token: SecretStr
    telegram_allowed_user_ids: str
    telegram_chat_id: int
    database_url: str
    timezone: str = "Europe/Kyiv"

    @property
    def allowed_user_ids(self) -> frozenset[int]:
        return frozenset(int(p) for p in self.telegram_allowed_user_ids.split(",") if p.strip())

    @model_validator(mode="after")
    def _require_non_empty_allowlist(self) -> "Settings":
        if not self.allowed_user_ids:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS must contain at least one numeric id")
        return self
