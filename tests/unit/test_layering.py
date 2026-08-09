"""Enforces CLAUDE.md rule 3: core/ imports neither adapters/ nor llm/.

The stdlib-only, no-Docker equivalent of a lint rule in a project with no
lint-rules/ directory.
"""

import ast
from pathlib import Path

import pytest

CORE_ROOT = Path(__file__).parents[2] / "src" / "finbot" / "core"
BANNED_PREFIXES = ("finbot.adapters", "finbot.llm")
BANNED_RELATIVE_ROOTS = ("adapters", "llm")


def _violations_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(BANNED_PREFIXES):
                    violations.append(f"{path}:{node.lineno} imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                if node.module is not None:
                    # "from ..adapters.telegram import mapping" -> node.module ==
                    # "adapters.telegram". The dots never resolve to "finbot." here,
                    # so this can't be caught by the absolute-prefix check below and
                    # needs its own root-segment test.
                    if node.module.split(".")[0] in BANNED_RELATIVE_ROOTS:
                        violations.append(f"{path}:{node.lineno} imports {node.module} (relative)")
                else:
                    # "from .. import adapters" -> node.module is None and the
                    # banned package name is one of the imported names instead.
                    for alias in node.names:
                        if alias.name in BANNED_RELATIVE_ROOTS:
                            violations.append(
                                f"{path}:{node.lineno} imports {alias.name} (relative)"
                            )
            elif node.module is not None and node.module.startswith(BANNED_PREFIXES):
                violations.append(f"{path}:{node.lineno} imports {node.module}")
    return violations


def test_core_does_not_import_adapters_or_llm() -> None:
    assert CORE_ROOT.is_dir(), f"expected {CORE_ROOT} to exist; layering check has nothing to scan"

    paths = sorted(CORE_ROOT.rglob("*.py"))
    assert paths, f"no .py files found under {CORE_ROOT}; layering check would pass vacuously"

    violations: list[str] = []
    for path in paths:
        violations.extend(_violations_in(path))

    assert violations == [], "core/ must not import adapters/ or llm/:\n" + "\n".join(violations)


@pytest.mark.parametrize(
    ("source", "should_flag"),
    [
        pytest.param(
            "from ..adapters.telegram import mapping\n", True, id="relative-dotted-adapters"
        ),
        pytest.param("from .. import adapters\n", True, id="relative-bare-adapters"),
        pytest.param("from .. import llm\n", True, id="relative-bare-llm"),
        pytest.param("from ...llm import client\n", True, id="relative-dotted-llm-level3"),
        pytest.param("import finbot.adapters.telegram\n", True, id="absolute-plain-import"),
        pytest.param("from finbot.llm import client\n", True, id="absolute-from-import"),
        pytest.param("from finbot.core.models import X\n", False, id="control-absolute-core"),
    ],
)
def test_violations_in_covers_every_import_shape(
    tmp_path: Path, source: str, should_flag: bool
) -> None:
    """Enumerates the input space instead of sampling it.

    Each row is one AST shape `ast.ImportFrom`/`ast.Import` can take for a
    banned target: dotted vs. bare relative import, absolute `import` vs.
    `from ... import`, and one control case (an absolute import of an
    unrelated `finbot.core` name) that must NOT be flagged. The bare-relative
    form (`from .. import adapters`) is the one a prior fix missed: it has
    `node.module is None`, so the banned name lives in `node.names` instead.
    """
    scratch = tmp_path / "scratch.py"
    scratch.write_text(source)

    violations = _violations_in(scratch)

    assert bool(violations) is should_flag, f"{source!r} -> {violations!r}"
