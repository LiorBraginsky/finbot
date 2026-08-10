"""Application configuration, loaded from the environment / .env."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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

    openrouter_api_key: SecretStr
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    model_text: str
    # Unset by default, deliberately: docs/roadmap.md Stage 2 requires the
    # bot to run with voice unconfigured rather than fail at startup — the
    # owner sets this only after running the voice eval. An empty string is
    # what "unset" looks like once `voice_model_candidates` below resolves
    # it, exactly like `model_fallbacks`.
    model_voice: str = ""
    # str, not list[str]: pydantic-settings JSON-decodes any complex-typed
    # field straight from the environment, so a list[str] field would fail
    # to parse "a,b" before any validator of ours ever ran. Comma-splitting
    # by hand in the model_candidates property keeps the failure mode ours.
    model_fallbacks: str = ""
    llm_timeout_seconds: int = 60
    max_extraction_attempts: int = 2
    max_message_attempts: int = 5
    # Voice notes longer than this are refused before any download
    # (docs/roadmap.md Stage 2, spec §7).
    max_voice_seconds: int = 120

    @property
    def allowed_user_ids(self) -> frozenset[int]:
        return frozenset(int(p) for p in self.telegram_allowed_user_ids.split(",") if p.strip())

    @property
    def model_candidates(self) -> tuple[str, ...]:
        """`model_text` plus `model_fallbacks`, blanks stripped, in order —
        the `models` list `llm/openrouter.py` sends on every request.
        """
        fallbacks = [part.strip() for part in self.model_fallbacks.split(",") if part.strip()]
        return (self.model_text, *fallbacks)

    @property
    def voice_model_candidates(self) -> tuple[str, ...]:
        """Empty when `model_voice` is unset — `core.extraction.pipeline`
        reads an empty tuple as "voice is not configured yet" and neither
        downloads nor calls anything (docs/roadmap.md Stage 2), the same way
        `model_candidates` never has to special-case `model_text` being
        required: the difference here is that voice is allowed to be unset
        at all.
        """
        if not self.model_voice.strip():
            return ()
        fallbacks = [part.strip() for part in self.model_fallbacks.split(",") if part.strip()]
        return (self.model_voice, *fallbacks)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @model_validator(mode="after")
    def _require_non_empty_allowlist(self) -> "Settings":
        if not self.allowed_user_ids:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS must contain at least one numeric id")
        return self

    @model_validator(mode="after")
    def _require_resolvable_timezone(self) -> "Settings":
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            msg = f"TIMEZONE {self.timezone!r} does not resolve to a known zone"
            raise ValueError(msg) from exc
        return self

    @model_validator(mode="after")
    def _forbid_free_model_ids(self) -> "Settings":
        # ":free" variants have the worst data policy and are hard
        # rate-limited (see docs/plans/stage-1-text-to-expense.md's Reality
        # check) — failing at startup beats discovering it from a 429 at
        # 2 a.m. or, worse, silently training a provider on household data.
        # voice_model_candidates too: an unset MODEL_VOICE is fine (it's an
        # empty tuple), but a configured one is held to the same ban.
        free_ids = [
            candidate
            for candidate in (*self.model_candidates, *self.voice_model_candidates)
            if candidate.endswith(":free")
        ]
        if free_ids:
            raise ValueError(f"model id(s) {free_ids} end in ':free', which this project bans")
        return self

    @model_validator(mode="after")
    def _require_extraction_attempts_in_range(self) -> "Settings":
        if not 1 <= self.max_extraction_attempts <= 3:
            raise ValueError("MAX_EXTRACTION_ATTEMPTS must be between 1 and 3")
        return self
