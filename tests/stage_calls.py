"""Record every call the suite makes across a stage boundary, for comparing
two trees (ROADMAP.md, Phase 0: the second population a pipeline change is
proven on - the recorded questions found one divergent shape where the unit
tests' own calls found five).

Off unless ``ASSOCIATION_STAGE_CALLS`` names a directory:

    ASSOCIATION_STAGE_CALLS=/scratch/before uv run pytest -q -n auto
    ASSOCIATION_STAGE_CALLS=/scratch/after  uv run pytest -q -n auto    # the other tree
    uv run python scripts/stage_snapshots.py compare-calls /scratch/before /scratch/after

Each worker writes ``calls-<pid>.jsonl``: one line per call, keyed by the
test that made it and its position among that test's calls, holding the
boundary's name, the arguments that are values (a question, a Reading - not
a connection or a callback) and what came back, or what was raised.

The boundaries are the ones the stages meet at today: the parser's two
steps, the compiler's entry point, every template handler and ``Agent.ask``.
A phase that moves a boundary adds the new one to :data:`BOUNDARIES` before
it deletes the old, so both trees record the same calls.
"""

from __future__ import annotations

import functools
import importlib
import json
import os
import re
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: ``module:function`` for each boundary recorded, beside every handler in
#: ``templates.TEMPLATES`` and ``Agent.ask``.
BOUNDARIES: tuple[str, ...] = (
    "association.query.parse:read_route",
    "association.query.parse:reading_from_route",
    "association.query.compose:answer",
)

# pytest's per-test directories, wherever the machine keeps them: two runs
# never share one, and a chart's path is part of what a template returns.
_TMP = re.compile(r"/[^\s\"']*?pytest-of-[^/\s\"']+/pytest-\d+/(popen-gw\d+/)?")

_calls: Counter[str] = Counter()


def _plain(value: Any) -> Any:
    """``stages.plain``, with each per-test directory masked."""
    from association.query.stages import plain

    return json.loads(_TMP.sub("<tmp>/", json.dumps(plain(value))))


def _is_value(arg: Any) -> bool:
    """Whether an argument is part of what was asked - not the connection,
    the template context, a callback or the Agent a method was called on,
    which no comparison can read (an object ``stages.plain`` does not know
    prints its address, which no two runs share)."""
    import duckdb

    from association.query.stages import plain
    from association.query.templates.common import TemplateContext

    if isinstance(arg, (duckdb.DuckDBPyConnection, TemplateContext)) or callable(arg):
        return False
    shown = plain(arg)
    return not (isinstance(shown, dict) and set(shown) == {"unknown", "repr"})


def _record(out: Path, name: str, args: tuple[Any, ...], kwargs: dict[str, Any], outcome: dict[str, Any]) -> None:
    """Append one call's line, keyed by the running test and the call's
    position among that test's calls."""
    test = os.environ.get("PYTEST_CURRENT_TEST", "(outside a test)").split(" (")[0]
    _calls[test] += 1
    row = {
        "test": test,
        "call": _calls[test],
        "boundary": name,
        "args": [_plain(arg) for arg in args if _is_value(arg)],
        "kwargs": {key: _plain(value) for key, value in kwargs.items() if _is_value(value)},
        **outcome,
    }
    with out.open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def _recording(out: Path, name: str, function: Callable[..., Any], shown: Callable[[Any, tuple[Any, ...]], Any]) -> Callable[..., Any]:
    """``function``, with every call's arguments and outcome written to ``out``."""

    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            _record(out, name, args, kwargs, {"raised": f"{type(exc).__name__}: {exc}"})
            raise
        _record(out, name, args, kwargs, {"returned": _plain(shown(result, args))})
        return result

    return wrapper


def _rebind(original: Any, replacement: Any) -> None:
    """Point every module-level name bound to ``original`` at ``replacement``:
    a caller that imported the function by name at import time holds its own
    reference, and would otherwise go unrecorded."""
    for module_name, module in list(sys.modules.items()):
        if module is None or not module_name.startswith("association"):
            continue
        for attribute, value in list(vars(module).items()):
            if value is original:
                setattr(module, attribute, replacement)


def install(directory: Path) -> None:
    """Wrap every boundary so each call is recorded under ``directory``.
    Called once, from ``conftest.py``, before any test module is imported -
    a test that imports a boundary by name then gets the recording one."""
    import association
    from association.query import agent as agent_module
    from association.query.stages import snapshot
    from association.query.templates import TEMPLATES

    directory.mkdir(parents=True, exist_ok=True)
    out = directory / f"calls-{os.getpid()}.jsonl"
    # Which copy of the code this run read: a suite run from a worktree
    # resolves the installed package unless PYTHONPATH says otherwise, and
    # a tree compared with itself proves nothing.
    out.write_text(json.dumps({"meta": {"code": str(Path(association.__file__).resolve())}}) + "\n")

    def as_returned(result: Any, _args: tuple[Any, ...]) -> Any:
        return result

    for boundary in BOUNDARIES:
        module_name, function_name = boundary.split(":")
        original = getattr(importlib.import_module(module_name), function_name)
        _rebind(original, _recording(out, boundary, original, as_returned))
    for intent, handler in list(TEMPLATES.items()):
        wrapped = _recording(out, f"template:{intent}", handler, as_returned)
        _rebind(handler, wrapped)
        TEMPLATES[intent] = wrapped

    def as_snapshot(answer: Any, args: tuple[Any, ...]) -> Any:
        return snapshot(args[0].reading, answer, unanswered=args[0].unanswered, unsaid=args[0].unsaid)

    ask = agent_module.Agent.ask
    agent_module.Agent.ask = _recording(out, "Agent.ask", ask, as_snapshot)  # type: ignore[method-assign]
