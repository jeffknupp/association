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
  leaving. Fix it; adding it to the list is a decision for a person, written
  into the commit that does it.
- a listed violation the code no longer has - the list must shrink with the
  code, or the room it leaves gets used again. ``--shrink`` removes them and
  never adds one.

The ratchets, each named for the contract it holds (``ROADMAP.md``, "The
target"):

``question_outside_the_reader``
    A function that takes the question's text (a parameter named
    ``question``) outside the modules that read it and the answering loop
    that hands it to them. Contract 1: the text stays in the reader.
``sql_outside_the_relations``
    A module that executes SQL (calls ``.execute`` or ``.sql``). Contract
    3: one place builds and runs SQL; a sayer cannot read the warehouse.
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

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUERY = ROOT / "src" / "association" / "query"
RATCHETS = pathlib.Path(__file__).resolve().parent / "ratchets.json"

#: The modules that read the question's text today (ROADMAP's READ stage).
READER = frozenset({"normalizer", "parse", "router", "subject", "season_text"})
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


def question_outside_the_reader(modules: dict[str, ast.Module]) -> set[str]:
    """``module:function`` for each function outside the reader and the
    answering loop with a parameter named ``question``."""
    found: set[str] = set()
    for name, tree in modules.items():
        if name in READER | ENTRY:
            continue
        for qualified, function in _functions(tree):
            arguments = function.args
            if any(arg.arg == "question" for arg in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)):
                found.add(f"{name}:{qualified}")
    return found


def sql_outside_the_relations(modules: dict[str, ast.Module]) -> set[str]:
    """Each module that calls ``.execute(...)`` or ``.sql(...)``."""
    return {name for name, tree in modules.items() if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"execute", "sql"} for node in ast.walk(tree))}


def private_template_imports(modules: dict[str, ast.Module]) -> set[str]:
    """``module:name`` for each private name a compiler module imports from
    a template module."""
    found: set[str] = set()
    for name, tree in modules.items():
        if name != "compose" and not name.startswith("compose."):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and "templates" in (node.module or ""):
                found.update(f"{name}:{alias.name}" for alias in node.names if alias.name.startswith("_"))
    return found


def regex_outside_the_reader(modules: dict[str, ast.Module]) -> set[str]:
    """Each module outside the reader that imports ``re``."""
    found: set[str] = set()
    for name, tree in modules.items():
        if name in READER:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Import) and any(alias.name == "re" for alias in node.names)) or (isinstance(node, ast.ImportFrom) and node.module == "re"):
                found.add(name)
    return found


CHECKS = {
    "question_outside_the_reader": question_outside_the_reader,
    "sql_outside_the_relations": sql_outside_the_relations,
    "private_template_imports": private_template_imports,
    "regex_outside_the_reader": regex_outside_the_reader,
}


def measure() -> dict[str, list[str]]:
    """Where every ratchet stands in this tree."""
    modules = _modules()
    return {name: sorted(check(modules)) for name, check in CHECKS.items()}


def compare(allowed: dict[str, list[str]], found: dict[str, list[str]]) -> list[str]:
    """One line per violation not listed and per listed violation gone."""
    problems: list[str] = []
    for name in CHECKS:
        listed, now = set(allowed.get(name, [])), set(found[name])
        problems.extend(f"{name}: NEW {entry} - the roadmap is deleting this shape; do not add to it" for entry in sorted(now - listed))
        problems.extend(f"{name}: GONE {entry} - remove it from scripts/ratchets.json (--shrink) so it cannot come back" for entry in sorted(listed - now))
    return problems


def main() -> int:
    """Run the gate, print the counts, or shrink the list."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--counts", action="store_true", help="print where each ratchet stands")
    parser.add_argument("--shrink", action="store_true", help="drop listed violations the code no longer has; never adds one")
    args = parser.parse_args()
    allowed: dict[str, list[str]] = json.loads(RATCHETS.read_text()) if RATCHETS.exists() else {}
    found = measure()
    if args.shrink:
        shrunk = {name: sorted(set(allowed.get(name, [])) & set(found[name])) for name in CHECKS}
        RATCHETS.write_text(json.dumps(shrunk, indent=1) + "\n")
        allowed = shrunk
    if args.counts:
        for name in CHECKS:
            print(f"{name}: {len(found[name])} (listed {len(allowed.get(name, []))})")
    problems = compare(allowed, found)
    for problem in problems:
        print(problem)
    if problems:
        print(f"\n{len(problems)} ratchet violation(s). See scripts/check_ratchets.py's header and ROADMAP.md, 'The target'.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
