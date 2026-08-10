"""Unit tests for evals.pull_voice_samples — the ADR-0016 guards. No
network, no Docker: only argument parsing and path validation are covered,
the parts a review can actually break silently.
"""

from pathlib import Path

import pytest
from evals.pull_voice_samples import (
    _REPO_ROOT,
    RepoPathError,
    _parse_args,
    _parse_message_ids,
    _validate_out_dir,
)


def test_message_ids_is_required(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--out", str(tmp_path)])


def test_out_is_required() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--message-ids", "1042"])


def test_parse_args_reads_both_when_given(tmp_path: Path) -> None:
    args = _parse_args(["--message-ids", "1042,1043", "--out", str(tmp_path)])
    assert args.message_ids == "1042,1043"
    assert args.out == tmp_path


def test_parse_message_ids_splits_and_strips() -> None:
    assert _parse_message_ids(" 1042 , 1043,1044 ") == [1042, 1043, 1044]


def test_parse_message_ids_rejects_an_empty_list() -> None:
    with pytest.raises(ValueError, match="at least one"):
        _parse_message_ids("")


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
