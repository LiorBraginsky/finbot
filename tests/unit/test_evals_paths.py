"""Unit tests for evals.paths.ensure_outside_repo — ADR-0016's invariant,
extracted from `pull_voice_samples.py`'s original private `_validate_out_dir`
so this stage's bank golden-set loader (`evals.scoring.
load_bank_golden_cases`) shares one implementation instead of a second copy
of the same check (docs/plans/stage-2_5-bank-screenshots.md, Approach F).

The case table is derived from the ways a path can sneak past a naive
string-prefix check, not from the one example the plan names: the repo root
itself, a subdirectory of it, a relative path that only resolves inside the
repo once the current working directory is taken into account, and a
symlink whose *target* lives inside the repository even though the link
itself lives elsewhere.

`tests/unit/test_pull_voice_samples.py` is the proof this guard moved rather
than weakened: its own `--out` tests stay green, unchanged, against the
`_validate_out_dir` wrapper this module now backs.
"""

from pathlib import Path

import pytest
from evals.paths import REPO_ROOT, RepoPathError, ensure_outside_repo

# --- The case table: every way a path can resolve inside the repo ---------


def test_ensure_outside_repo_refuses_the_repo_root_itself() -> None:
    with pytest.raises(RepoPathError):
        ensure_outside_repo(REPO_ROOT, flag="--cases")


def test_ensure_outside_repo_refuses_a_subdirectory_of_the_repo() -> None:
    with pytest.raises(RepoPathError):
        ensure_outside_repo(REPO_ROOT / "evals" / "golden", flag="--cases")


def test_ensure_outside_repo_refuses_a_relative_path_that_resolves_inside(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    with pytest.raises(RepoPathError):
        ensure_outside_repo(Path("evals/golden"), flag="--cases")


def test_ensure_outside_repo_refuses_a_symlink_whose_target_is_inside_the_repo(
    tmp_path: Path,
) -> None:
    # The symlink itself lives in tmp_path — genuinely outside the repo — but
    # points at a real directory inside it; resolving must follow the link,
    # not stop at the string the caller passed in.
    symlink = tmp_path / "sneaky-cases-dir"
    symlink.symlink_to(REPO_ROOT / "evals" / "golden", target_is_directory=True)
    with pytest.raises(RepoPathError):
        ensure_outside_repo(symlink, flag="--cases")


# --- The positive control: a genuinely external path is accepted ----------


def test_ensure_outside_repo_accepts_a_path_outside_the_repo(tmp_path: Path) -> None:
    # tmp_path is pytest's own per-test temp directory, guaranteed outside
    # any project checkout.
    resolved = ensure_outside_repo(tmp_path / "bank-samples", flag="--images-dir")
    assert resolved == (tmp_path / "bank-samples").resolve()


# --- The error message names the caller's own flag, not a hard-coded one --


def test_ensure_outside_repo_error_message_names_the_given_flag() -> None:
    with pytest.raises(RepoPathError, match="--images-dir"):
        ensure_outside_repo(REPO_ROOT, flag="--images-dir")


def test_ensure_outside_repo_returns_the_resolved_path() -> None:
    with pytest.raises(RepoPathError) as excinfo:
        ensure_outside_repo(REPO_ROOT / "evals", flag="--cases")
    assert str(REPO_ROOT) in str(excinfo.value)
