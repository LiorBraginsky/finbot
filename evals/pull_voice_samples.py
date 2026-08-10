"""One-off tool: pull specific, owner-named voice notes out of Postgres to a
path outside this repository, as raw material for hand-building
`evals/golden/voice_v1.jsonl` cases (see that directory's own README).

ADR-0016 narrowly amends ADR-0009 to permit exactly this and nothing more:

- `--message-ids` is required, with no default and no "all voice rows"
  mode — a comma-separated list of specific `messages.id` values the owner
  chose deliberately.
- `--out` is required, with no default, and refused if it resolves inside
  this repository.

Everything else ADR-0009 states stays in force: nothing binary is
committed, and this script is owner-invoked by hand, never scheduled.

Needs `TELEGRAM_BOT_TOKEN` and `DATABASE_URL` (`.env` or exported) and
network access to both Postgres and Telegram — run on the server, or
anywhere with both.

    python -m evals.pull_voice_samples --message-ids 1042,1043 --out ~/finbot-voice-samples
"""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from aiogram import Bot
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select

from finbot.core.models import MessageKind
from finbot.repo.engine import create_sessionmaker
from finbot.repo.models import Message

# This file's own parent directory (evals/)'s parent is the repo root —
# resolved, not assumed, so a symlinked or relative --out can't slip past
# the check in _validate_out_dir below.
_REPO_ROOT = Path(__file__).resolve().parents[1]


class _Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: SecretStr
    database_url: str


class RepoPathError(ValueError):
    """`--out` resolves inside this repository — ADR-0016 forbids that."""


def _validate_out_dir(out_dir: Path) -> Path:
    """Refuses a destination this repository's own `git add` could ever
    reach — checked against the resolved path, not trusted to the caller's
    own `.gitignore` discipline (ADR-0016's whole point).
    """
    resolved = out_dir.expanduser().resolve()
    if resolved == _REPO_ROOT or _REPO_ROOT in resolved.parents:
        raise RepoPathError(
            f"--out ({resolved}) is inside this repository ({_REPO_ROOT}); "
            "ADR-0016 requires voice samples to be written outside it"
        )
    return resolved


def _parse_message_ids(raw: str) -> list[int]:
    ids = [int(part.strip()) for part in raw.split(",") if part.strip()]
    if not ids:
        msg = "--message-ids must name at least one finbot messages.id value"
        raise ValueError(msg)
    return ids


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evals.pull_voice_samples",
        description="Download explicitly named voice messages to a path outside this repo.",
    )
    parser.add_argument(
        "--message-ids",
        required=True,
        dest="message_ids",
        help="comma-separated finbot messages.id values to download, e.g. 1042,1043 "
        "— never 'all', by design (ADR-0016)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="destination directory; must resolve outside this repository",
    )
    return parser.parse_args(argv)


async def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    message_ids = _parse_message_ids(args.message_ids)
    out_dir = _validate_out_dir(args.out)

    settings = _Settings()
    sessionmaker = create_sessionmaker(settings.database_url)
    bot = Bot(token=settings.telegram_bot_token.get_secret_value())

    out_dir.mkdir(parents=True, exist_ok=True)
    async with sessionmaker() as session:
        rows = (
            await session.execute(
                select(Message.id, Message.file_id).where(
                    Message.id.in_(message_ids), Message.kind == MessageKind.VOICE
                )
            )
        ).all()

    found_ids = {row.id for row in rows}
    missing_ids = sorted(set(message_ids) - found_ids)
    if missing_ids:
        print(f"no voice message found for id(s): {missing_ids}")  # noqa: T201 -- CLI tool

    async with bot.session:
        for message_id, file_id in rows:
            if file_id is None:
                continue
            destination = out_dir / f"{message_id}.oga"
            await bot.download(file_id, destination=destination)
            print(f"wrote {destination}")  # noqa: T201 -- CLI tool

    print(f"done: {len(rows)} file(s) written to {out_dir}")  # noqa: T201 -- CLI tool


if __name__ == "__main__":
    asyncio.run(main())
