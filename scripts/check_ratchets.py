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
    How many statements each module executes, by module: calls of
    ``.execute``, ``.executemany``, ``.sql`` and ``.query``, of
    ``entities._read_table`` (since 2026-10-03), and - since 2026-10-05,
    when Phase 2's review found 35 hand-written statements moved whole
    behind the compiler's door, where the count was blind to them - each
    ``core.rows_of``/``core.values_of`` call whose statement is not one the
    compiler built (``compile_*``), counted in the module that makes the
    call. A function that only passes on a statement it is handed (a
    ``Statement`` parameter, or ``Statement(sql, ...)`` over a parameter:
    ``season_line._values``, ``fingerprint._values``) is a door itself, and
    its callers are counted instead. How the argument is traced, and what
    counts when it cannot be, is :func:`_origin`. Contract 3: one place
    builds and runs SQL; a sayer cannot read the warehouse. Counted, not
    listed, since 2026-10-02: a listed module could grow statements freely,
    and nothing said so. 80 statements in 20 modules at the redefinition
    (38 in 11 before it; the list was regenerated once, by hand, with
    Jeff's say-so).
``regex_outside_the_reader``
    A module that imports ``re`` outside the reader. Contract 6: regexes
    live in the lexicon.
A fifth, ``private_template_imports`` (a private name the compiler took from
``templates/``), went to zero and was deleted with ``templates/`` itself
(Phase 2, step 6, 2026-10-05): with no template module left there is no
shape for it to hold.
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


#: The compiler's one door (``compose.core``): ``rows_of`` and ``values_of``
#: run whatever statement they are handed - a point the compiler built, or a
#: ``Statement`` written by hand and moved whole behind the door (Phase 2's
#: slices (iii)-(v)). Only the second kind counts (:func:`_door_sites`).
_DOORS = frozenset({"rows_of", "values_of"})

Function = ast.FunctionDef | ast.AsyncFunctionDef


def _callee(node: ast.Call) -> str | None:
    """The called name: ``f`` of ``f(...)`` and of ``x.f(...)``."""
    return node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else None


def _calls_by_function(tree: ast.Module) -> list[tuple[Function | None, ast.Call]]:
    """Every call in a module with its innermost enclosing function (``None``
    at module level)."""
    found: list[tuple[Function | None, ast.Call]] = []

    def visit(node: ast.AST, function: Function | None) -> None:
        for child in ast.iter_child_nodes(node):
            inner = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else function
            if isinstance(child, ast.Call):
                found.append((inner, child))
            visit(child, inner)

    visit(tree, None)
    return found


def _names_in(target: ast.AST) -> set[str]:
    """The names an assignment or loop target binds (``a``, ``a, b``)."""
    return {node.id for node in ast.walk(target) if isinstance(node, ast.Name)}


def _bindings(function: Function, name: str) -> list[ast.AST | str]:
    """What ``name`` is bound to inside ``function``: the value of each
    assignment to it (``"compiled"`` for one annotated ``Compiled`` or
    ``TeamCompiled``, or a table of them), and the iterable of each loop or
    comprehension that binds it."""
    found: list[ast.AST | str] = []
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and any(name in _names_in(target) for target in node.targets):
            found.append(node.value)
        elif isinstance(node, ast.AnnAssign) and name in _names_in(node.target):
            found.append("compiled" if "Compiled" in ast.unparse(node.annotation) else node.value or "statement")
        elif isinstance(node, (ast.For, ast.comprehension)) and name in _names_in(node.target):
            iterable = node.iter
            # `for k, v in table.items()` reads `table`.
            if isinstance(iterable, ast.Call) and isinstance(iterable.func, ast.Attribute) and iterable.func.attr in {"items", "values"}:
                iterable = iterable.func.value
            found.append(iterable)
    return found


def _origin(expr: ast.AST | str, function: Function | None, depth: int = 0) -> str:
    """Where a statement handed to the door comes from, read from the
    argument's expression inside its function:

    - ``"compiled"``: a call of a compiler (a callee whose name holds
      ``compile``: ``compile_query``, ``compile_over``, ``compile_team_*``,
      ``_compile_rows``...), a name bound to one (assigned from it, looped
      over or indexed out of a table of them, or annotated
      ``Compiled``/``TeamCompiled``), or a parameter annotated
      ``Compiled``/``TeamCompiled``;
    - ``"handed"``: a parameter of any other type, or ``Statement(...)``
      built from one - the function is itself a door, and its callers are
      counted instead (:func:`_door_sites`);
    - ``"statement"``: anything else - ``Statement(...)``, a
      statement-building function's result (``season_statement(...)``), or
      what this cannot trace. Unknown counts: a statement is the default.
    """
    if isinstance(expr, str):
        return expr
    if depth > 8:
        return "statement"
    if isinstance(expr, ast.Call):
        callee = _callee(expr) or ""
        if "compile" in callee:
            return "compiled"
        if callee == "Statement" and expr.args and function is not None:
            return "handed" if _origin(expr.args[0], function, depth + 1) == "handed" else "statement"
        return "statement"
    if isinstance(expr, ast.Subscript):
        # One entry of a table: `per_type[season_type]` is what `per_type` holds.
        return _origin(expr.value, function, depth + 1)
    if not isinstance(expr, ast.Name) or function is None:
        return "statement"
    arguments = function.args
    for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs):
        if argument.arg == expr.id:
            annotation = ast.unparse(argument.annotation) if argument.annotation is not None else ""
            return "compiled" if "Compiled" in annotation else "handed"
    origins = {_origin(bound, function, depth + 1) for bound in _bindings(function, expr.id)}
    if origins == {"compiled"}:
        return "compiled"
    return "handed" if "handed" in origins else "statement"


def _imported_doors(tree: ast.Module, doors: dict[str, set[str]]) -> set[str]:
    """The doors (:func:`_door_sites`) another module defines that ``tree``
    imports by name."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            source = (node.module or "").rsplit(".", 1)[-1]
            found |= {alias.name for alias in node.names for module, local in doors.items() if module.rsplit(".", 1)[-1] == source and alias.name in local}
    return found


def _door_calls(tree: ast.Module, doors: set[str]) -> list[tuple[Function | None, str]]:
    """Each call in a module of a door in ``doors`` (or of ``rows_of``/
    ``values_of``), with its function and where its statement comes from."""
    return [(function, _origin(call.args[1], function)) for function, call in _calls_by_function(tree) if (_callee(call) in _DOORS or _callee(call) in doors) and len(call.args) >= 2]


def _door_sites(modules: dict[str, ast.Module]) -> dict[str, int]:
    """How many hand-built statements each module runs through a door: a
    call of ``rows_of``/``values_of`` - or of a function that hands its own
    parameter to one, such as ``season_line._values`` - whose statement is
    not the compiler's (:func:`_origin`). The door's own body is not a site
    when it only passes on what it was handed; its callers are. Doors are
    found until no new one turns up, so a door's own wrapper is one too."""
    doors: dict[str, set[str]] = {name: set() for name in modules}
    while True:
        counts: dict[str, int] = {}
        new = False
        for name, tree in modules.items():
            for function, origin in _door_calls(tree, doors[name] | _imported_doors(tree, doors)):
                if origin == "statement":
                    counts[name] = counts.get(name, 0) + 1
                elif origin == "handed" and function is not None and function.name not in doors[name]:
                    doors[name].add(function.name)
                    new = True
        if not new:
            return counts


def sql_outside_the_relations(modules: dict[str, ast.Module]) -> dict[str, int]:
    """How many statements each module runs, for the modules that run any:
    ``.execute``, ``.executemany``, ``.sql`` and ``.query`` calls, calls to
    the package's own string-SQL helpers (:data:`_EXECUTORS`), and - since
    2026-10-05 - each hand-built statement run through the compiler's door
    (:func:`_door_sites`), counted in the module whose call hands it over."""

    def runs(node: ast.AST) -> bool:
        return isinstance(node, ast.Call) and _callee(node) in _EXECUTORS

    counts = {name: sum(1 for node in ast.walk(tree) if runs(node)) for name, tree in modules.items()}
    for name, count in _door_sites(modules).items():
        counts[name] += count
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
