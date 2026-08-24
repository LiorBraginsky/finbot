"""Unit tests for evals.pull_voice_samples — the ADR-0016 guards, plus the
`--file-ids`/`--message-ids` split that lets the tool run without database
access. No real network and no Docker: `--file-ids` is exercised end to end
against `tests/support/fake_session.py`'s `FakeSession`, the same fake
`tests/unit/test_audio.py` uses for `Bot.download` — no sockets opened, and
`--message-ids`'s database path is only ever reached through a
`create_sessionmaker` stand-in that fails the test if it is ever called.
"""

from pathlib import Path

import pytest
from aiogram import Bot
from evals.pull_voice_samples import (
    _REPO_ROOT,
    MissingDatabaseUrlError,
    RepoPathError,
    _discriminator,
    _parse_args,
    _parse_file_ids,
    _parse_message_ids,
    _Settings,
    _targets_from_message_ids,
    _validate_out_dir,
    main,
)

from tests.support.fake_session import FakeSession

_NO_ENV_FILE = "/nonexistent/pull-voice-samples-test.env"


def test_a_selection_flag_is_required_when_neither_is_given(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--out", str(tmp_path)])


def test_message_ids_and_file_ids_together_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--message-ids", "1042", "--file-ids", "abc", "--out", str(tmp_path)])


def test_out_is_required() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--message-ids", "1042"])


def test_parse_args_reads_message_ids_and_out_when_given(tmp_path: Path) -> None:
    args = _parse_args(["--message-ids", "1042,1043", "--out", str(tmp_path)])
    assert args.message_ids == "1042,1043"
    assert args.file_ids is None
    assert args.out == tmp_path


def test_parse_args_reads_file_ids_and_out_when_given(tmp_path: Path) -> None:
    args = _parse_args(["--file-ids", "AAA,BBB", "--out", str(tmp_path)])
    assert args.file_ids == "AAA,BBB"
    assert args.message_ids is None
    assert args.out == tmp_path


def test_parse_message_ids_splits_and_strips() -> None:
    assert _parse_message_ids(" 1042 , 1043,1044 ") == [1042, 1043, 1044]


def test_parse_message_ids_rejects_an_empty_list() -> None:
    with pytest.raises(ValueError, match="at least one"):
        _parse_message_ids("")


def test_parse_file_ids_splits_and_strips() -> None:
    assert _parse_file_ids(" AAA , BBB,CCC ") == ["AAA", "BBB", "CCC"]


def test_parse_file_ids_rejects_an_empty_list() -> None:
    with pytest.raises(ValueError, match="at least one"):
        _parse_file_ids("")


def test_discriminator_never_contains_the_raw_file_id() -> None:
    file_id = "AwACAgIAAxkBAAI-super-secret-capability-token"

    discriminator = _discriminator(file_id)

    assert file_id not in discriminator
    assert discriminator == _discriminator(file_id)  # stable across calls
    assert discriminator != _discriminator(file_id + "x")  # sensitive to the input


def test_validate_out_dir_refuses_the_repo_root() -> None:
    with pytest.raises(RepoPathError):
        _validate_out_dir(_REPO_ROOT)


def test_validate_out_dir_refuses_a_path_inside_the_repo() -> None:
    with pytest.raises(RepoPathError):
        _validate_out_dir(_REPO_ROOT / "evals" / "golden" / "voice")


def test_validate_out_dir_refuses_a_relative_path_that_resolves_inside(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(_REPO_ROOT)
    with pytest.raises(RepoPathError):
        _validate_out_dir(Path("evals/golden/voice"))


def test_validate_out_dir_accepts_a_path_outside_the_repo(tmp_path: Path) -> None:
    # tmp_path is pytest's own per-test temp directory, guaranteed outside
    # any project checkout.
    resolved = _validate_out_dir(tmp_path / "voice-samples")
    assert resolved == (tmp_path / "voice-samples").resolve()


# --- DATABASE_URL is required only on the --message-ids path ----------------


async def test_targets_from_message_ids_fails_clearly_without_database_url() -> None:
    # database_url=None is explicit, not relied on as the field's own
    # default: an earlier test in the same session (tests/conftest.py's
    # postgres_url fixture) sets DATABASE_URL in os.environ and never
    # unsets it, and an explicit init kwarg is the one thing pydantic-
    # settings lets override that leak.
    settings = _Settings(
        _env_file=_NO_ENV_FILE, telegram_bot_token="42:TESTTOKEN", database_url=None
    )

    with pytest.raises(MissingDatabaseUrlError, match="DATABASE_URL"):
        await _targets_from_message_ids("1042", settings)


async def test_main_with_message_ids_and_no_database_url_fails_with_a_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A clear, targeted error printed to stderr and a `SystemExit(1)` —
    never the raw `pydantic.ValidationError` traceback `_Settings` used to
    raise when `database_url` had no default at all.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "42:TESTTOKEN")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        await main(["--message-ids", "1042", "--out", str(tmp_path)], env_file=_NO_ENV_FILE)

    assert excinfo.value.code == 1
    assert "DATABASE_URL" in capsys.readouterr().err


def _written_filenames(directory: Path) -> list[str]:
    """A plain, synchronous read of what `main()` wrote — kept out of the
    `async def` tests below for the same reason `evals.run._save_raw` is
    kept out of `run_case` (ASYNC240): a `pathlib.Path` method blocks the
    event loop, real or fake.
    """
    return sorted(path.name for path in directory.iterdir())


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


async def test_file_ids_never_opens_a_database_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`create_sessionmaker` is replaced with a stand-in that fails the test
    the moment it is called — the strongest assertion available that
    `--file-ids` never even tries to open Postgres, not merely that it
    tolerates `DATABASE_URL` being absent.
    """

    def _explode(url: str) -> None:
        raise AssertionError(f"create_sessionmaker must not be called for --file-ids (url={url!r})")

    monkeypatch.setattr("evals.pull_voice_samples.create_sessionmaker", _explode)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "42:TESTTOKEN")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    voice_files = {"file-a": b"aa-bytes", "file-b": b"bb-bytes"}
    monkeypatch.setattr(
        "evals.pull_voice_samples.Bot",
        lambda *, token: Bot(token="42:TESTTOKEN", session=FakeSession(voice_files=voice_files)),
    )

    await main(["--file-ids", "file-a,file-b", "--out", str(tmp_path)], env_file=_NO_ENV_FILE)

    written = _written_filenames(tmp_path)
    assert written == sorted(f"{_discriminator(file_id)}.oga" for file_id in voice_files)
    assert _read_bytes(tmp_path / f"{_discriminator('file-a')}.oga") == b"aa-bytes"
    assert _read_bytes(tmp_path / f"{_discriminator('file-b')}.oga") == b"bb-bytes"


async def test_file_ids_filenames_never_contain_a_raw_file_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "42:TESTTOKEN")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "evals.pull_voice_samples.Bot",
        lambda *, token: Bot(
            token="42:TESTTOKEN",
            session=FakeSession(voice_files={"a-raw-file-id-token": b"bytes"}),
        ),
    )

    await main(["--file-ids", "a-raw-file-id-token", "--out", str(tmp_path)], env_file=_NO_ENV_FILE)

    names = _written_filenames(tmp_path)
    assert names == [f"{_discriminator('a-raw-file-id-token')}.oga"]
    assert not any("a-raw-file-id-token" in name for name in names)
