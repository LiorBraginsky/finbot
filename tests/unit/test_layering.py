"""Enforces CLAUDE.md rule 3: core/ imports neither adapters/ nor llm/.

The stdlib-only, no-Docker equivalent of a lint rule in a project with no
lint-rules/ directory.

An import node can bind a banned target in exactly five shapes: (1) a dotted
`ast.Import` alias, (2)/(3) `ast.ImportFrom` at module level with the target
in `.module` or hidden in `.names`, and (4)/(5) the same split again once the
import is relative (`.level > 0`). `_violations_in` below is organised around
that split, not around any particular set of example imports, because a
table built from examples only ever proves what the examples happened to
cover. `importlib.import_module("finbot.adapters...")` and `__import__(...)`
are `ast.Call` nodes, not import nodes, and are out of reach for any AST
walker — a documented limit of this technique, not a hole: reaching for
either to cross this boundary is deliberate circumvention, not an accident.
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
            # Shape 1: "import finbot.adapters.telegram" -> alias.name carries
            # the full dotted path.
            for alias in node.names:
                if alias.name.startswith(BANNED_PREFIXES):
                    violations.append(f"{path}:{node.lineno} imports {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                if node.module is not None:
                    # Shape 4: "from ..adapters.telegram import mapping" ->
                    # node.module == "adapters.telegram". The dots never
                    # resolve to "finbot." here, so this can't be caught by
                    # the absolute check below and needs its own
                    # root-segment test.
                    if node.module.split(".")[0] in BANNED_RELATIVE_ROOTS:
                        violations.append(f"{path}:{node.lineno} imports {node.module} (relative)")
                else:
                    # Shape 5: "from .. import adapters" -> node.module is
                    # None and the banned package name is one of the
                    # imported names instead.
                    for alias in node.names:
                        if alias.name in BANNED_RELATIVE_ROOTS:
                            violations.append(
                                f"{path}:{node.lineno} imports {alias.name} (relative)"
                            )
            elif node.module is not None:
                # Shapes 2 and 3: "from finbot.adapters import X" binds the
                # target directly in node.module; "from finbot import
                # adapters" binds the identical target one level down,
                # through node.names, with node.module == "finbot" only.
                # Testing what each name would actually resolve to —
                # node.module itself, and node.module + "." + each imported
                # name — catches both without flagging "from finbot import
                # core", where neither resolved string starts with a banned
                # prefix.
                targets = [node.module, *(f"{node.module}.{alias.name}" for alias in node.names)]
                if any(target.startswith(BANNED_PREFIXES) for target in targets):
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
        # Shape 1: ast.Import, dotted alias.
        pytest.param("import finbot.adapters.telegram\n", True, id="shape1-absolute-plain-import"),
        # Shape 2: ImportFrom level=0, target in .module.
        pytest.param("from finbot.llm import client\n", True, id="shape2-absolute-from-import"),
        pytest.param(
            "from finbot.core.models import X\n", False, id="shape2-control-absolute-module"
        ),
        # Shape 3: ImportFrom level=0, target hidden in .names — the hole
        # this round closed. "from finbot import adapters" is the shape this
        # exclusively-absolute-import codebase would actually write.
        pytest.param("from finbot import adapters\n", True, id="shape3-absolute-bare-adapters"),
        pytest.param("from finbot import llm as l\n", True, id="shape3-absolute-bare-llm-aliased"),
        pytest.param("from finbot import core\n", False, id="shape3-control-absolute-bare"),
        # Shape 4: ImportFrom level>0, target in .module.
        pytest.param(
            "from ..adapters.telegram import mapping\n", True, id="shape4-relative-dotted-adapters"
        ),
        pytest.param("from ...llm import client\n", True, id="shape4-relative-dotted-llm-level3"),
        # Shape 5: ImportFrom level>0, target hidden in .names.
        pytest.param("from .. import adapters\n", True, id="shape5-relative-bare-adapters"),
        pytest.param("from .. import llm\n", True, id="shape5-relative-bare-llm"),
    ],
)
def test_violations_in_covers_every_import_shape(
    tmp_path: Path, source: str, should_flag: bool
) -> None:
    """Enumerates the AST grammar instead of sampling example imports.

    An import node can bind a banned target in exactly five shapes (see the
    module docstring); each is represented here by at least one violating
    case, and the two level=0 shapes each carry their own control case
    (`from finbot import core` / `from finbot.core.models import X`) that
    must NOT be flagged, so a broadened check is demonstrated not to
    over-fire as well as to close the hole. Building the table from the
    grammar rather than from a list of previously-found examples is the
    point: a table built from examples only ever proves what the examples
    happened to cover, which is exactly how shape 3 stayed open through two
    prior rounds on this file.
    """
    scratch = tmp_path / "scratch.py"
    scratch.write_text(source)

    violations = _violations_in(scratch)

    assert bool(violations) is should_flag, f"{source!r} -> {violations!r}"
