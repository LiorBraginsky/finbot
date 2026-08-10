"""Enforces CLAUDE.md rule 2 at the one place it can be broken silently: the
JSON wire. `finbot.core.money.loads_decimal` is `json.loads(text,
parse_float=Decimal)`; any other call to `json.loads` (or a bare `loads`
imported from `json`) parses numbers through the C float parser first, and a
`Decimal` built from a float afterwards is already lossy — the damage is done
before anyone gets a chance to fix it. The stdlib-only, no-Docker equivalent of
a lint rule in a project with no `lint-rules/` directory, in the spirit of
`test_layering.py`.

A call can resolve to json's `loads` in exactly two independent ways, and each
way can itself be aliased, which is the grammar this file's table is built
from rather than from a list of remembered examples:

1. **Attribute access on the module**, bound by `import json` or
   `import json as <alias>` — `json.loads(...)` / `<alias>.loads(...)`.
2. **A bare name bound directly to the function**, via
   `from json import loads` or `from json import loads as <alias>` —
   `loads(...)` / `<alias>(...)`.

Within either shape, `parse_float` can be supplied positionally-impossible
(the real `json.loads` signature makes it keyword-only), so only its presence
as a *keyword* argument — regardless of other keywords present, and
regardless of whether the mandatory `text` argument is positional or
`s=...` keyword — clears a call. Two control shapes prove the resolution is
by binding, not by substring match on the name `loads`: a module that is not
`json` but happens to expose a `.loads` attribute (`pickle.loads`), and a
locally defined function literally named `loads` that has nothing to do with
`json`. `loads_decimal` itself — the production name this rule exists to
protect — is a third: its name merely *contains* "loads".
"""

import ast
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).parents[2] / "src" / "finbot"
ALLOWED_FILES = frozenset({SRC_ROOT / "core" / "money.py"})


def _violations_in(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))

    json_module_names: set[str] = set()  # local names bound to the json module
    loads_names: set[str] = set()  # local names bound directly to json.loads

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "json":
                    json_module_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "json" and node.level == 0:
            for alias in node.names:
                if alias.name == "loads":
                    loads_names.add(alias.asname or alias.name)

    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        is_json_loads_call = False
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "loads":
            if isinstance(func.value, ast.Name) and func.value.id in json_module_names:
                is_json_loads_call = True
        elif isinstance(func, ast.Name) and func.id in loads_names:
            is_json_loads_call = True

        if not is_json_loads_call:
            continue

        has_parse_float = any(kw.arg == "parse_float" for kw in node.keywords)
        if not has_parse_float:
            violations.append(f"{path}:{node.lineno} calls json.loads without parse_float=Decimal")

    return violations


def test_json_loads_always_uses_parse_float_decimal() -> None:
    assert SRC_ROOT.is_dir(), f"expected {SRC_ROOT} to exist; this check has nothing to scan"

    paths = sorted(p for p in SRC_ROOT.rglob("*.py") if p not in ALLOWED_FILES)
    assert paths, f"no .py files found under {SRC_ROOT}; this check would pass vacuously"

    violations: list[str] = []
    for path in paths:
        violations.extend(_violations_in(path))

    assert violations == [], (
        "json.loads must always be called with parse_float=Decimal (CLAUDE.md "
        "rule 2) — use finbot.core.money.loads_decimal instead:\n" + "\n".join(violations)
    )


@pytest.mark.parametrize(
    ("source", "should_flag"),
    [
        # Shape 1: attribute access via `import json`.
        pytest.param("import json\njson.loads(x)\n", True, id="attr-plain-import-no-parse-float"),
        pytest.param(
            "import json\nimport decimal\njson.loads(x, parse_float=decimal.Decimal)\n",
            False,
            id="attr-plain-import-with-parse-float",
        ),
        # Shape 1 aliased: `import json as j`.
        pytest.param(
            "import json as j\nj.loads(x)\n", True, id="attr-aliased-import-no-parse-float"
        ),
        pytest.param(
            "import json as j\nj.loads(x, parse_float=Decimal)\n",
            False,
            id="attr-aliased-import-with-parse-float",
        ),
        # Shape 1 control: a module that is not json but also has `.loads`.
        # `pickle` is chosen only because its `.loads` name collides with
        # json's; this source string is fed to ast.parse only, never
        # executed, so no untrusted pickle data is ever deserialized here.
        pytest.param(
            "import pickle\npickle.loads(x)\n", False, id="attr-control-non-json-module-loads"
        ),
        # Shape 2: bare name via `from json import loads`.
        pytest.param(
            "from json import loads\nloads(x)\n", True, id="bare-from-import-no-parse-float"
        ),
        pytest.param(
            "from json import loads\nloads(x, parse_float=Decimal)\n",
            False,
            id="bare-from-import-with-parse-float",
        ),
        # Shape 2 aliased: `from json import loads as lj`.
        pytest.param(
            "from json import loads as lj\nlj(x)\n",
            True,
            id="bare-aliased-from-import-no-parse-float",
        ),
        pytest.param(
            "from json import loads as lj\nlj(x, parse_float=Decimal)\n",
            False,
            id="bare-aliased-from-import-with-parse-float",
        ),
        # Shape 2 control: a locally defined `loads`, unrelated to json.
        pytest.param(
            "def loads(x):\n    return x\n\n\nloads(x)\n",
            False,
            id="bare-control-locally-defined-loads",
        ),
        # Keyword-only argument grammar: `text` passed as `s=...`, other
        # keywords present, but parse_float absent either way.
        pytest.param("import json\njson.loads(s=x)\n", True, id="kwarg-only-text-no-parse-float"),
        pytest.param(
            "import json\njson.loads(x, cls=Foo)\n",
            True,
            id="kwarg-other-keyword-present-no-parse-float",
        ),
        # Control: json.dumps is a different function entirely.
        pytest.param("import json\njson.dumps(x)\n", False, id="control-json-dumps"),
        # Control: the production helper's name merely contains "loads".
        pytest.param(
            "from finbot.core.money import loads_decimal\nloads_decimal(x)\n",
            False,
            id="control-loads-decimal-name-substring-trap",
        ),
    ],
)
def test_violations_in_covers_every_json_loads_call_shape(
    tmp_path: Path, source: str, should_flag: bool
) -> None:
    """Enumerates the AST grammar instead of sampling example calls.

    Every shape a call resolving to json's `loads` can take is represented
    here (see the module docstring), each with a violating and — where the
    shape allows it — a clean case, plus three control cases that must NOT be
    flagged: a same-named attribute on an unrelated module, a locally defined
    function that happens to share the bare name `loads`, and a call to the
    production helper `loads_decimal`, whose name only contains "loads" as a
    substring. A table built from remembered examples only ever proves what
    those examples happened to cover.
    """
    scratch = tmp_path / "scratch.py"
    scratch.write_text(source)

    violations = _violations_in(scratch)

    assert bool(violations) is should_flag, f"{source!r} -> {violations!r}"
