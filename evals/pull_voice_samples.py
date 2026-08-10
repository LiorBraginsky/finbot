"""One-off tool: pull the owner's own voice notes out of Postgres into
`evals/golden/voice/`, as raw material for hand-building
`evals/golden/voice_v1.jsonl` cases (see that directory's own README).

Reads `messages` for `kind='voice'`, downloads each `file_id` through the
Telegram Bot API, and writes it to `evals/golden/voice/<message_id>.oga` —
the original OGG/Opus Telegram sent, never converted, so a case built from
one of these exercises the exact same `adapters.telegram.audio` conversion
path production does. Skips a message id already present on disk, so it is
safe to re-run.

Needs `TELEGRAM_BOT_TOKEN` and `DATABASE_URL` (`.env` or exported) and
network access to both Postgres and Telegram — run on the server, or
anywhere with both. Not part of the bot: nothing in `src/finbot/` imports
this, and it is not covered by CLAUDE.md rule 3's layering (it is a script,
not a layer).

    python -m evals.pull_voice_samples
"""

import asyncio
from pathlib import Path

from aiogram import Bot
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select

from finbot.core.models import MessageKind
from finbot.repo.engine import create_sessionmaker
from finbot.repo.models import Message

_OUTPUT_DIR = Path(__file__).parent / "golden" / "voice"


class _Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: SecretStr
    database_url: str


async def main() -> None:
    settings = _Settings()
    sessionmaker = create_sessionmaker(settings.database_url)
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(Message.id, Message.file_id).where(Message.kind == MessageKind.VOICE)
            )
        ).all()

    print(f"{len(rows)} voice message(s) found in messages")  # noqa: T201 -- CLI tool
    written = 0
    async with bot.session:
        for message_id, file_id in rows:
            if file_id is None:
                continue
            destination = _OUTPUT_DIR / f"{message_id}.oga"
            if destination.exists():
                continue
            await bot.download(file_id, destination=destination)
            written += 1
            print(f"wrote {destination}")  # noqa: T201 -- CLI tool

    print(f"done: {written} new file(s) written to {_OUTPUT_DIR}")  # noqa: T201 -- CLI tool


if __name__ == "__main__":
    asyncio.run(main())
