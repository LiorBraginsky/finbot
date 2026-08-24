"""ADR-0016's invariant — a golden-set loader for a private modality must
refuse a path inside this repository — extracted from
`pull_voice_samples.py`'s original private `_REPO_ROOT`/`_validate_out_dir`
so a second loader (`evals.scoring.load_bank_golden_cases`, Stage 2.5's bank
screenshots, Approach F) shares one implementation instead of a second copy
of the same check. `pull_voice_samples.py` now imports from here too — see
that module for the ADR-0016 story this guard originates from.

The guard: resolve the given path (following symlinks, expanding `~`,
collapsing `..`) and compare it against the resolved repository root, never
trust the caller's own `.gitignore` discipline or an unresolved string
prefix. That single comparison is what refuses all of: the repository root
itself, any subdirectory of it, a relative path that resolves inside it from
the current working directory, and a symlink whose *target* lives inside the
repository even though the link itself does not.
"""

from pathlib import Path

# This file's own parent directory (evals/)'s parent is the repo root —
# resolved, not assumed, so a symlinked or relative path can't slip past
# ensure_outside_repo below.
REPO_ROOT: Path = Path(__file__).resolve().parents[1]


class RepoPathError(ValueError):
    """A path meant to hold private data (voice samples, ADR-0016; a bank
    golden set's cases and images, docs/plans/stage-2_5-bank-screenshots.md
    Approach F) resolves inside this repository.
    """


def ensure_outside_repo(path: Path, *, flag: str) -> Path:
    """Refuses a destination this repository's own `git add` could ever
    reach — checked against the resolved path, never trusted to the
    caller's own `.gitignore` discipline. `flag` names the CLI option in the
    error message (`--out`, `--cases`, `--images-dir`, ...) so the caller
    sees exactly which argument to fix, and returns the resolved path so
    every caller shares the one resolution as well as the one check.
    """
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise RepoPathError(
            f"{flag} ({resolved}) is inside this repository ({REPO_ROOT}); "
            "private data must be written and read outside it"
        )
    return resolved
