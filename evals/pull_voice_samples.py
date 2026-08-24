"""One-off tool: pull specific, owner-named voice notes out of Telegram to a
path outside this repository, as raw material for hand-building
`evals/golden/voice_v1.jsonl` cases (see that directory's own README).

ADR-0016 narrowly amends ADR-0009 to permit exactly this and nothing more.
Exactly one of two explicit, owner-named selections is required — never
both, never neither, and never an "all voice rows" mode:

- `--file-ids` — a comma-separated list of Telegram `file_id` values the
  owner already has. Opens no database connection at all: `DATABASE_URL`
  is never read on this path. This is the flag to use from a laptop —
  `infra/docker-compose.yml` publishes no port for Postgres (ADR-0002:
  long polling needs no inbound port), so a machine outside the compose
  network cannot resolve `messages.id` to a `file_id` in the first place.
  Get one without database access by running a read-only query *inside*
  the running Postgres container, on the server:

      docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \\
        -c "select id, file_id from messages where kind = 'voice' order by id desc limit 20;"

  then, from wherever `TELEGRAM_BOT_TOKEN` and network access to Telegram
  are available (a laptop is enough — this needs no Postgres and no
  checkout on the server):

      python -m evals.pull_voice_samples --file-ids AwACAgIAAxkBAAI... --out ~/finbot-voice-samples

- `--message-ids` — a comma-separated list of specific `messages.id`
  values, for a machine that does have `DATABASE_URL` *and* network
  access to Postgres (e.g. a temporary SSH tunnel to the server; nothing
  in this deployment exposes that by default — see the paragraph above).
  Resolves each id to a `file_id` through Postgres, then downloads
  exactly as `--file-ids` does:

      python -m evals.pull_voice_samples --message-ids 1042,1043 --out ~/finbot-voice-samples

`--out` is required either way, with no default, and refused — by
comparing the resolved path against the resolved repository root, not by
trusting `.gitignore` — if it names this repository or anything under it.

Downloaded files are named from a short, non-secret discriminator
(`--message-ids`: the message id itself; `--file-ids`: a short hash of the
`file_id`) — never from the raw `file_id`, which is a bearer credential
good for downloading that exact file from Telegram and does not belong in
a filename that might end up somewhere more visible than `--out`.

Everything else ADR-0009 states stays in force: nothing binary is
committed, and this script is owner-invoked by hand, never scheduled.

The `bot` image built from `infra/Dockerfile` does not contain `evals/` —
only `src/`, `alembic.ini` and `migrations/` are copied in — so `docker
compose exec bot python -m evals.pull_voice_samples` does not work either
way; this script is meant to be run from a full checkout, not from inside
the deployed container.
"""

import argparse
import asyncio
import hashlib
import sys
from collections.abc import Sequence
from pathlib import Path

from aiogram import Bot
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select

# `as RepoPathError` (not a bare import) is the same self-re-export idiom
# `convert_to_mp3` uses elsewhere in evals/ (see evals/scoring.py): pyflakes
# would otherwise flag it as unused here, since nothing in this module's own
# body raises or catches it — it exists only for tests/unit/test_pull_voice_
# samples.py, which imports it directly, unchanged, across the move to
# evals.paths.
from evals.paths import REPO_ROOT, ensure_outside_repo
from evals.paths import RepoPathError as RepoPathError
from finbot.core.models import MessageKind
from finbot.repo.engine import create_sessionmaker
from finbot.repo.models import Message

# Re-exported under its original private name for the same reason as
# RepoPathError above.
_REPO_ROOT = REPO_ROOT


class _Settings(BaseSettings):
    """`database_url` is optional here, unlike every other settings class in
    this project: `--file-ids` opens no database connection and must not
    fail to even start on a machine that has no `DATABASE_URL` at all.
    `--message-ids` needs it — `_targets_from_message_ids` below checks
    that explicitly, with a message naming the flag instead of a bare
    `ValidationError`.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: SecretStr
    database_url: str | None = None


def _load_settings(*, env_file: str | Path | None = ".env") -> _Settings:
    return _Settings(_env_file=env_file)


class MissingDatabaseUrlError(RuntimeError):
    """`--message-ids` resolves `messages.id` to `file_id` through Postgres
    and needs `DATABASE_URL`; `--file-ids` does not — see the module
    docstring for how to get a `file_id` without database access.
    """


def _validate_out_dir(out_dir: Path) -> Path:
    """`--out` must resolve outside this repository (ADR-0016) — the check
    itself now lives in `evals.paths.ensure_outside_repo`, shared with the
    Stage 2.5 bank golden set's `--cases`/`--images-dir`, so this is a thin,
    `--out`-flavoured wrapper rather than a second copy of the comparison.
    """
    return ensure_outside_repo(out_dir, flag="--out")


def _parse_message_ids(raw: str) -> list[int]:
    ids = [int(part.strip()) for part in raw.split(",") if part.strip()]
    if not ids:
        msg = "--message-ids must name at least one finbot messages.id value"
        raise ValueError(msg)
    return ids


def _parse_file_ids(raw: str) -> list[str]:
    ids = [part.strip() for part in raw.split(",") if part.strip()]
    if not ids:
        msg = "--file-ids must name at least one Telegram file_id value"
        raise ValueError(msg)
    return ids


def _discriminator(file_id: str) -> str:
    """A short, stable, non-secret stand-in for `file_id` in a filename —
    long enough that two different file_ids collide only by chance (16**10
    possibilities), short enough to type back when matching a downloaded
    file to a `voice_v1.jsonl` case. Never the raw `file_id` itself: that
    string is a capability token good for downloading this exact file from
    Telegram, and does not belong in a filename (see the module docstring).
    """
    return hashlib.sha256(file_id.encode("utf-8")).hexdigest()[:10]


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m evals.pull_voice_samples",
        description="Download explicitly named voice messages to a path outside this repo.",
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--message-ids",
        dest="message_ids",
        default=None,
        help="comma-separated finbot messages.id values to download, e.g. 1042,1043 "
        "— resolved to a file_id through Postgres, so this needs DATABASE_URL "
        "(mutually exclusive with --file-ids)",
    )
    selection.add_argument(
        "--file-ids",
        dest="file_ids",
        default=None,
        help="comma-separated Telegram file_id values to download directly — no "
        "database needed (mutually exclusive with --message-ids); see "
        "evals/golden/voice/README.md for how to get one without DATABASE_URL",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="destination directory; must resolve outside this repository",
    )
    return parser.parse_args(argv)


async def _targets_from_message_ids(raw: str, settings: _Settings) -> list[tuple[str, str]]:
    """Resolves `--message-ids` to `(filename, file_id)` pairs through
    Postgres. Raises `MissingDatabaseUrlError` before opening any
    connection if `DATABASE_URL` is absent, rather than letting SQLAlchemy
    fail on a `None` URL with a far less legible error.
    """
    if settings.database_url is None:
        raise MissingDatabaseUrlError(
            "--message-ids resolves messages.id to a file_id through Postgres and "
            "needs DATABASE_URL, which is not set. Use --file-ids instead if you "
            "only have Telegram file_id values — see evals/golden/voice/README.md."
        )
    message_ids = _parse_message_ids(raw)
    sessionmaker = create_sessionmaker(settings.database_url)
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

    return [(f"{message_id}.oga", file_id) for message_id, file_id in rows if file_id is not None]


def _targets_from_file_ids(raw: str) -> list[tuple[str, str]]:
    """Mirrors `_targets_from_message_ids`'s return shape with no database
    involved at all — every `file_id` the owner named is downloaded, named
    by `_discriminator` rather than the id itself.
    """
    return [(f"{_discriminator(file_id)}.oga", file_id) for file_id in _parse_file_ids(raw)]


async def main(argv: Sequence[str] | None = None, *, env_file: str | Path | None = ".env") -> None:
    args = _parse_args(argv)
    out_dir = _validate_out_dir(args.out)
    settings = _load_settings(env_file=env_file)

    if args.message_ids is not None:
        try:
            targets = await _targets_from_message_ids(args.message_ids, settings)
        except MissingDatabaseUrlError as exc:
            print(str(exc), file=sys.stderr)  # noqa: T201 -- CLI error, not a debug print
            raise SystemExit(1) from exc
    else:
        targets = _targets_from_file_ids(args.file_ids)

    bot = Bot(token=settings.telegram_bot_token.get_secret_value())
    out_dir.mkdir(parents=True, exist_ok=True)
    async with bot.session:
        for filename, file_id in targets:
            destination = out_dir / filename
            await bot.download(file_id, destination=destination)
            print(f"wrote {destination}")  # noqa: T201 -- CLI tool

    print(f"done: {len(targets)} file(s) written to {out_dir}")  # noqa: T201 -- CLI tool


if __name__ == "__main__":
    asyncio.run(main())
