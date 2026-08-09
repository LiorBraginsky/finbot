"""Enforces CLAUDE.md rule 3: core/ imports neither adapters/ nor llm/.

The stdlib-only, no-Docker equivalent of a lint rule in a project with no
lint-rules/ directory.
"""

import ast
from pathlib import Path

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
            if node.module is None:
                continue
            if node.level > 0:
                # Relative import from inside core/, e.g. "from ..adapters.telegram
                # import mapping" -> node.module == "adapters.telegram". The dots
                # never resolve to "finbot." here, so this can't be caught by the
                # absolute-prefix check below and needs its own root-segment test.
                if node.module.split(".")[0] in BANNED_RELATIVE_ROOTS:
                    violations.append(f"{path}:{node.lineno} imports {node.module} (relative)")
            elif node.module.startswith(BANNED_PREFIXES):
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
