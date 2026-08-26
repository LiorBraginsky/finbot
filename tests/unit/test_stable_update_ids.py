"""A rule-enforcing test: no test may derive a `telegram_update_id` from
`hash(...)`.

`hash()` on a `str` — or on a tuple containing one — is randomised per process
by `PYTHONHASHSEED`, so a suite that keys its fixtures on it uses a different
identity space every run. Two tests whose ids collide make the second
`messages.add_if_new` return `None` (the redelivered-update path) and the
failure appears on one run in however many, then vanishes on rerun.

That was not hypothetical: it was found after one full-suite run failed ten
integration tests at once and three consecutive reruns passed. The cause was
never reproduced, which is precisely the problem — and precisely why
`.claude/orchestration.md` forbids tolerating an intermittent gate rather than
rerunning until green.

Listed in `.claude/orchestration.md` -> `## Truth`. Do not delete it because
it looks like an odd test.
"""

import ast
import re
from pathlib import Path

import pytest

from tests.support.ids import stable_update_id

_TESTS_ROOT = Path(__file__).parents[1]
_UPDATE_ID_NAMES = {"telegram_update_id", "update_id"}


def _test_modules() -> list[Path]:
    return sorted(p for p in _TESTS_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def test_the_scan_actually_sees_files() -> None:
    """A guard that scanned zero files would pass vacuously — the exact way
    `test_layering.py` was wrong on its first two review rounds.
    """
    modules = _test_modules()

    assert len(modules) > 30
    assert any(p.name == "test_stable_update_ids.py" for p in modules)


def test_no_test_derives_an_update_id_from_the_builtin_hash() -> None:
    """Walks the AST rather than grepping: `hash(` inside a comment or a
    docstring is harmless, and a call spread over two lines would defeat a
    line-based search.

    The rule is deliberately narrow — `hash()` is fine anywhere else. What is
    forbidden is letting a *per-process-random* value decide a database
    identity that another test might also claim.
    """
    offenders: list[str] = []
    for path in _test_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.keyword) or node.arg not in _UPDATE_ID_NAMES:
                continue
            if any(
                isinstance(inner, ast.Call)
                and isinstance(inner.func, ast.Name)
                and inner.func.id == "hash"
                for inner in ast.walk(node.value)
            ):
                offenders.append(f"{path.relative_to(_TESTS_ROOT)}:{node.value.lineno}")

    assert offenders == [], (
        "derive these from tests.support.ids.stable_update_id instead — "
        f"hash() is randomised per process: {offenders}"
    )


def test_stable_update_id_is_stable_across_processes() -> None:
    """The property the whole rule rests on, checked against a literal rather
    than against another call to the same function: a self-comparison would
    pass even if the implementation went back to `hash()`.
    """
    assert stable_update_id("хліб 50") == 1_862_453_256


def test_stable_update_id_distinguishes_inputs_a_str_cast_would_merge() -> None:
    assert stable_update_id("a", 1) != stable_update_id("a1")
    assert stable_update_id("a", 1) != stable_update_id("a", "1")


@pytest.mark.parametrize("parts", [("хліб",), ("voice", 5), ("item", 12, "extra")])
def test_stable_update_id_is_positive_and_fits_a_telegram_id(parts: tuple[object, ...]) -> None:
    value = stable_update_id(*parts)

    assert value > 0
    assert value < 2**63


def test_no_derived_id_can_collide_with_a_hand_written_literal_one() -> None:
    """Several tests pass small literal ids (`1001`, `2001`, `3001`). The
    offset is what keeps the two spaces disjoint, so adding a literal id can
    never silently shadow a derived one.
    """
    literals = set()
    for path in _test_modules():
        for match in re.finditer(r"callback_update\((\d+)", path.read_text(encoding="utf-8")):
            literals.add(int(match.group(1)))

    assert literals, "expected to find some literal callback update ids"
    assert max(literals) < 1_000_000
