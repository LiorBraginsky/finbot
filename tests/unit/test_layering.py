"""Enforces CLAUDE.md rule 3: core/ imports neither adapters/ nor llm/.

The stdlib-only, no-Docker equivalent of a lint rule in a project with no
lint-rules/ directory.
"""

import ast
from pathlib import Path

CORE_ROOT = Path(__file__).parents[2] / "src" / "finbot" / "core"
BANNED_PREFIXES = ("finbot.adapters", "finbot.llm")


def _imported_module_names(tree: ast.Module) -> list[tuple[str, int]]:
    names: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.append((node.module, node.lineno))
    return names


def test_core_does_not_import_adapters_or_llm() -> None:
    violations: list[str] = []
    for path in sorted(CORE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for module_name, lineno in _imported_module_names(tree):
            if module_name.startswith(BANNED_PREFIXES):
                violations.append(f"{path}:{lineno} imports {module_name}")

    assert violations == [], "core/ must not import adapters/ or llm/:\n" + "\n".join(violations)
