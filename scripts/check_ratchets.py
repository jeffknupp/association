#!/usr/bin/env python3
"""Hold the query package to the direction ``ROADMAP.md`` set: nothing the
roadmap deletes may grow while it is being deleted.

    python scripts/check_ratchets.py            # the gate
    python scripts/check_ratchets.py --counts   # where each ratchet stands
    python scripts/check_ratchets.py --shrink   # drop entries the code no longer has

The roadmap's contracts cannot be switched on while the code still breaks
them, so each is held as a RATCHET: today's violations are listed, by name,
in ``scripts/ratchets.json``, and this fails on

- a violation that is not listed - new code going the way the roadmap is
  leaving - or a counted one that grew. Fix it; adding to the list is a
  decision for a person, written into the commit that does it.
- a listed violation the code no longer has, or a count that fell - the list
  must shrink with the code, or the room it leaves gets used again.
  ``--shrink`` removes and lowers, and never adds or raises.

The ratchets, each named for the contract it holds (``ROADMAP.md``, "The
target"):

``question_outside_the_reader``
    A function that takes the question's text (a parameter named
    ``question``) outside the modules that read it and the answering loop
    that hands it to them. Contract 1: the text stays in the reader.
``sql_outside_the_relations``
    How many statements each module executes (calls of ``.execute`` or
    ``.sql``), by module. Contract 3: one place builds and runs SQL; a
    sayer cannot read the warehouse. Counted, not listed, since 2026-10-02:
    a listed module could grow statements freely, and nothing said so.
``private_template_imports``
    A private name the compiler imports from ``templates/`` - the template
    bodies the compiled intents still answer through. They reach zero when
    ``templates/`` is gone.
``regex_outside_the_reader``
    A module that imports ``re`` outside the reader. Contract 6: regexes
    live in the lexicon.
Decision D4 (new shapes are frozen) is held beside these by a test,
``tests/query/test_frozen_shapes.py``, since the intents are values the
package computes and this script reads source only - it imports nothing, so
it says the same thing run bare, under ``uv run`` and in CI.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import sys
from collections.abc import Callable

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUERY = ROOT / "src" / "association" / "query"
RATCHETS = pathlib.Path(__file__).resolve().parent / "ratchets.json"

#: The modules that read the question's text today (ROADMAP's READ stage).
READER = frozenset({"normalizer", "parse", "router", "subject", "season_text", "point", "lines"})
#: The answering loop: it is handed the question and hands it to the reader.
ENTRY = frozenset({"agent"})


def _modules() -> dict[str, ast.Module]:
    """Every module of the query package, by its dotted name inside it."""
    found: dict[str, ast.Module] = {}
    for path in sorted(QUERY.rglob("*.py")):
        parts = path.relative_to(QUERY).with_suffix("").parts
        name = ".".join(part for part in parts if part != "__init__") or "__init__"
        found[name] = ast.parse(path.read_text())
    return found


def _functions(tree: ast.Module) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Every function in a module with its qualified name, nested ones included."""
    found: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.append((prefix + child.name, child))
                visit(child, prefix + child.name + ".")
            elif isinstance(child, ast.ClassDef):
                visit(child, prefix + child.name + ".")
            else:
                visit(child, prefix)

    visit(tree, "")
    return found


def _parameters(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """A function's parameter names, in order."""
    arguments = function.args
    return [arg.arg for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)]


def question_outside_the_reader(modules: dict[str, ast.Module]) -> set[str]:
    """``module:function`` for each function outside the reader and the
    answering loop with a parameter that names the question (``question``,
    or any name holding it - ``question_text``, ``the_question``: a
    renamed parameter passed the exact match until 2026-10-03)."""
    found: set[str] = set()
    for name, tree in modules.items():
        if name in READER | ENTRY:
            continue
        for qualified, function in _functions(tree):
            if any("question" in parameter for parameter in _parameters(function)):
                found.add(f"{name}:{qualified}")
    return found


#: The connection methods that run a statement, and the helpers of this
#: package that run one for their caller (``entities._read_table`` takes
#: the SQL as a string): a statement through a helper counted as none
#: until 2026-10-03.
_EXECUTORS = frozenset({"execute", "executemany", "sql", "query", "_read_table"})


def sql_outside_the_relations(modules: dict[str, ast.Module]) -> dict[str, int]:
    """How many statements each module runs - ``.execute``, ``.executemany``,
    ``.sql`` and ``.query`` calls, and calls to the package's own
    string-SQL helpers (:data:`_EXECUTORS`) - for the modules that run any."""

    def runs(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        callee = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else None
        return callee in _EXECUTORS

    counts = {name: sum(1 for node in ast.walk(tree) if runs(node)) for name, tree in modules.items()}
    return {name: count for name, count in counts.items() if count}


def con_in_the_reader(modules: dict[str, ast.Module]) -> set[str]:
    """``module:function`` for each reader function that takes a DuckDB
    connection (a parameter named ``con``): the reader reads names from
    the in-memory index and should need none. Listed so it only shrinks;
    the point reader's ``con`` was carried and never used."""
    found: set[str] = set()
    for name, tree in modules.items():
        if name not in READER:
            continue
        for qualified, function in _functions(tree):
            if "con" in _parameters(function):
                found.add(f"{name}:{qualified}")
    return found


def _template_modules_bound(tree: ast.Module) -> set[str]:
    """The local names a module binds to a template module, imported whole
    (``from ..templates import splits as _m``, ``import ...templates.splits as x``)."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("templates"):
            bound.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            bound.update(alias.asname or alias.name.split(".")[-1] for alias in node.names if "templates." in alias.name)
    return bound


def private_template_imports(modules: dict[str, ast.Module]) -> set[str]:
    """``module:name`` for each private name a compiler module takes from a
    template module: imported by name, or read off a template module it
    imported whole (``from ..templates import splits as _m; _m._x`` passed
    until 2026-10-03)."""
    found: set[str] = set()
    for name, tree in modules.items():
        if name != "compose" and not name.startswith("compose."):
            continue
        bound = _template_modules_bound(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and "templates" in (node.module or ""):
                found.update(f"{name}:{alias.name}" for alias in node.names if alias.name.startswith("_"))
            elif isinstance(node, ast.Attribute) and node.attr.startswith("_") and isinstance(node.value, ast.Name) and node.value.id in bound:
                found.add(f"{name}:{node.attr}")
    return found


def _imports_re(node: ast.AST) -> bool:
    """Whether ``node`` imports the ``re`` module: a plain import, or
    ``importlib.import_module("re")``/``__import__("re")`` (which passed
    until 2026-10-03)."""
    if isinstance(node, ast.Import):
        return any(alias.name == "re" for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return node.module == "re"
    if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "re":
        callee = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else None
        return callee in {"import_module", "__import__"}
    return False


def regex_outside_the_reader(modules: dict[str, ast.Module]) -> set[str]:
    """Each module outside the reader that imports ``re``, or takes a
    private name (a compiled pattern, say) from a reader module."""
    found: set[str] = set()
    for name, tree in modules.items():
        if name in READER:
            continue
        for node in ast.walk(tree):
            if _imports_re(node):
                found.add(name)
            if isinstance(node, ast.ImportFrom) and (node.module or "").rsplit(".", 1)[-1] in READER and any(alias.name.startswith("_") for alias in node.names):
                found.add(f"{name}:{','.join(alias.name for alias in node.names if alias.name.startswith('_'))}")
    return found


CHECKS: dict[str, Callable[[dict[str, ast.Module]], set[str] | dict[str, int]]] = {
    "question_outside_the_reader": question_outside_the_reader,
    "sql_outside_the_relations": sql_outside_the_relations,
    "private_template_imports": private_template_imports,
    "regex_outside_the_reader": regex_outside_the_reader,
    "con_in_the_reader": con_in_the_reader,
}

Standing = list[str] | dict[str, int]
"""Where one ratchet stands: the violations by name, or a count per name."""


def measure() -> dict[str, Standing]:
    """Where every ratchet stands in this tree."""
    modules = _modules()
    return {name: dict(sorted(found.items())) if isinstance(found, dict) else sorted(found) for name, found in ((name, check(modules)) for name, check in CHECKS.items())}


def _counts(standing: Standing | None) -> dict[str, int]:
    """A standing as counts: a listed name counts one."""
    if standing is None:
        return {}
    return dict(standing) if isinstance(standing, dict) else dict.fromkeys(standing, 1)


def compare(allowed: dict[str, Standing], found: dict[str, Standing]) -> list[str]:
    """One line per violation not listed or counted higher than listed, and
    per listed violation gone or counted lower."""
    problems: list[str] = []
    for name in CHECKS:
        listed, now = _counts(allowed.get(name)), _counts(found[name])
        counted = isinstance(found[name], dict)
        for entry in sorted(now.keys() | listed.keys()):
            before, after = listed.get(entry, 0), now.get(entry, 0)
            where = f"{entry} ({before} -> {after})" if counted and before and after else entry
            if after > before:
                problems.append(f"{name}: NEW {where} - the roadmap is deleting this shape; do not add to it")
            elif after < before:
                problems.append(f"{name}: GONE {where} - remove it from scripts/ratchets.json (--shrink) so it cannot come back")
    return problems


def shrink(allowed: dict[str, Standing], found: dict[str, Standing]) -> dict[str, Standing]:
    """``allowed`` with what the code no longer has removed and every count
    lowered to the code's; nothing added, nothing raised."""
    shrunk: dict[str, Standing] = {}
    for name in CHECKS:
        listed, now = _counts(allowed.get(name)), _counts(found[name])
        kept = {entry: min(count, now[entry]) for entry, count in listed.items() if entry in now}
        shrunk[name] = dict(sorted(kept.items())) if isinstance(found[name], dict) else sorted(kept)
    return shrunk


def main() -> int:
    """Run the gate, print the counts, or shrink the list."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--counts", action="store_true", help="print where each ratchet stands")
    parser.add_argument("--shrink", action="store_true", help="drop listed violations the code no longer has; never adds one")
    args = parser.parse_args()
    allowed: dict[str, Standing] = json.loads(RATCHETS.read_text()) if RATCHETS.exists() else {}
    found = measure()
    if args.shrink:
        allowed = shrink(allowed, found)
        RATCHETS.write_text(json.dumps(allowed, indent=1) + "\n")
    if args.counts:
        for name in CHECKS:
            print(f"{name}: {sum(_counts(found[name]).values())} (listed {sum(_counts(allowed.get(name)).values())})")
    problems = compare(allowed, found)
    for problem in problems:
        print(problem)
    if problems:
        print(f"\n{len(problems)} ratchet violation(s). See scripts/check_ratchets.py's header and ROADMAP.md, 'The target'.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
