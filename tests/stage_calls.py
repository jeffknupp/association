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

The boundaries are the ones the stages meet at today: the parser
(``read_route``, which returns the Reading, and its last step ``_reading``), the planner (``compose.plan:plan_point``, and the sentence it says a
reader's refusal in, ``refusal_result``), the compiler's entry point and
``Agent.ask``. The planner joined on 2026-10-05 (ISSUES.md #331): the
refusals that were template calls until Phase 2 are said in ``compose.plan``
now, and 40 tests that assert one - through the tests' ``plan_point`` and
``compose.answer`` pair, or ``refusal_result`` called alone - recorded
nothing, since a decline reaches ``compose:answer`` only as a ``None`` with
its reason handed to a callback no record read. A boundary's ``declined``
callback is heard since, and the reasons it was handed are the record's
``declined``. Every template handler
was one until the last template went (Phase 2, step 5): what replaced them is
the readers and the sayer behind ``compose:answer``, recorded since Phase 2's
first slice, so dropping the template boundary in step 6 took no call out of
the population - ``templates.TEMPLATES`` was already empty.
A phase that moves a boundary adds the new one to :data:`BOUNDARIES` before
it deletes the old, so both trees record the same calls.

Phase 4, step 4 folds two records into their neighbors (the planning into
the Query-or-Cause, the sayer's reply into the ``Answer``), and two
boundaries carry them: ``compose:answer`` takes the planning (``planned=``)
and returns what the sayer said, and ``refusal_result`` returns what the
sayer said. Both are recorded by what crosses them, in the form the stage
snapshot holds, since 2026-10-12 - the planning as the snapshot's ``query``
record (:func:`_planned_argument`) and what was said as its sentence, its
data and its files (:func:`_said`) - so the record is the same before the
fold and after it.
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

#: ``module:function`` for each boundary recorded, beside ``Agent.ask``. ``compose:answer`` lost its
#: ``ran`` callback in Phase 2, step 4 (2026-10-05): nothing moved across the
#: boundary - an argument was dropped, and a callback is never written into
#: a record (:func:`_is_value`), so the two trees' calls compared identical.
#: ``compose.plan:plan_point`` and ``refusal_result`` joined on 2026-10-05:
#: a planned point is recorded as the stage snapshot's ``query`` record
#: (:func:`_planned`), the query, the refusal said or the decline's reason.
#: ``refusal_result`` is recorded inside ``plan_point`` too (its call comes
#: first, as it returns first), since a test that calls it alone is the
#: only record of the sentence it asserts.
#: ``parse:_reading``, the parser's last step over what the words were read
#: as, joined on 2026-10-10 (Phase 3, step 4, slice (b)) before
#: ``reading_from_route`` folded into ``read_route`` and left the
#: boundaries: both trees record the Reading it builds from the same
#: arguments, the tree with the Route and the one without it, and
#: ``read_route`` returns that Reading since.
BOUNDARIES: tuple[str, ...] = (
    "association.query.parse:read_route",
    "association.query.parse:_reading",
    "association.query.compose.plan:plan_point",
    "association.query.compose.plan:refusal_result",
    "association.query.compose:answer",
)

# pytest's per-test directories, wherever the machine keeps them: two runs
# never share one, and a chart's path is part of what a reader returns.
# The test's own directory goes too: pytest cuts its name to 30 characters
# and numbers the collisions in the order the workers reach them, so two
# tests with one long prefix swap "..._na0" and "..._na1" between runs
# (seen 2026-10-02, the one call of 1,388 that differed for no change).
_TMP = re.compile(r"/[^\s\"']*?pytest-of-[^/\s\"']+/pytest-\d+/(popen-gw\d+/)?[^/\s\"']+")

_calls: Counter[str] = Counter()


def _plain(value: Any) -> Any:
    """``stages.plain``, with each per-test directory masked."""
    from association.query.stages import plain

    return json.loads(_TMP.sub("<tmp>", json.dumps(plain(value))))


def _is_value(arg: Any) -> bool:
    """Whether an argument is part of what was asked - not the connection,
    the name index the parser takes in its place (since Phase 3, step 4,
    slice (b): the warehouse's names, as the connection was the warehouse),
    the answer context, a callback or the Agent a method was called on,
    which no comparison can read (an object ``stages.plain`` does not know
    prints its address, which no two runs share)."""
    import duckdb

    from association.query.answer import AnswerContext
    from association.query.names import NameIndex, PlayerIndex, TeamIndex
    from association.query.stages import plain

    if isinstance(arg, (duckdb.DuckDBPyConnection, NameIndex, PlayerIndex, TeamIndex, AnswerContext)) or callable(arg):
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


def _recording(
    out: Path,
    name: str,
    function: Callable[..., Any],
    shown: Callable[[Any, tuple[Any, ...]], Any],
    argued: Callable[[tuple[Any, ...], dict[str, Any]], dict[str, Any]] | None = None,
) -> Callable[..., Any]:
    """``function``, with every call's arguments and outcome written to
    ``out``; ``argued`` names how a boundary's keyword arguments are
    recorded where one is a record the stage snapshot holds in its own form
    (:func:`_planned_argument`)."""

    @functools.wraps(function)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # A decline's reason reaches its caller only through the
        # ``declined`` callback (``compose.answer``'s ``None``): heard here
        # too, so the record says why, where the callback itself is never
        # written (:func:`_is_value`).
        heard: list[str] = []
        caller_declined = kwargs.get("declined")
        if callable(caller_declined):

            def declined(reason: str) -> None:
                heard.append(reason)
                caller_declined(reason)

            kwargs["declined"] = declined
        recorded = kwargs if argued is None else argued(args, kwargs)
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            _record(out, name, args, recorded, {"raised": f"{type(exc).__name__}: {exc}", **_declines(heard)})
            raise
        _record(out, name, args, recorded, {"returned": _plain(shown(result, args)), **_declines(heard)})
        return result

    return wrapper


def _declines(heard: list[str]) -> dict[str, Any]:
    """The reasons a call declined with, masked as every value is; nothing
    when it declined nothing, so a record without a decline reads as it
    did before the reasons were heard."""
    return {"declined": _plain(heard)} if heard else {}


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


def _planned(planned: Any, args: tuple[Any, ...]) -> Any:
    """A planning as the stage snapshot records it (``stages.read_stages``'s
    ``query``): the planned query with its relation and scope, the refusal's
    values and sentence, or the decline's reason - plain values, where the
    ``Planned`` itself holds a ``Query`` whose record is the snapshot's."""
    from association.query.stages import read_stages

    return read_stages(args[0], planned=planned)["query"]


def _planned_argument(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """``compose.answer``'s keyword arguments as recorded: the planning it is
    handed (``planned=``) as the stage snapshot's ``query`` record of it over
    the Reading it is handed beside it (:func:`_planned`) - the planned
    query, the refusal said or the decline's reason - so the record is the
    same whatever type carries the planning (Phase 4, step 4 folds the
    ``Planned`` record into the Query-or-Cause)."""
    if kwargs.get("planned") is None:
        return kwargs
    return {**kwargs, "planned": _planned(kwargs["planned"], args[1:])}


def _said(said: Any, _args: tuple[Any, ...]) -> Any:
    """What the sayer said, as recorded: its sentence (``answer``), its data
    and its files - the said part of an answer, whatever record carries it
    (Phase 4, step 4 folds the sayer's ``Reply`` into the ``Answer``); None
    where the answer side declined."""
    if said is None:
        return None
    return {"answer": said.answer, "data": said.data, "artifacts": said.artifacts}


def install(directory: Path) -> None:
    """Wrap every boundary so each call is recorded under ``directory``.
    Called once, from ``conftest.py``, before any test module is imported -
    a test that imports a boundary by name then gets the recording one."""
    import association
    from association.query import agent as agent_module
    from association.query.stages import snapshot

    directory.mkdir(parents=True, exist_ok=True)
    out = directory / f"calls-{os.getpid()}.jsonl"
    # Which copy of the code this run read: a suite run from a worktree
    # resolves the installed package unless PYTHONPATH says otherwise, and
    # a tree compared with itself proves nothing.
    out.write_text(json.dumps({"meta": {"code": str(Path(association.__file__).resolve())}}) + "\n")

    def as_returned(result: Any, _args: tuple[Any, ...]) -> Any:
        return result

    shown: dict[str, Callable[[Any, tuple[Any, ...]], Any]] = {
        "association.query.compose.plan:plan_point": _planned,
        "association.query.compose.plan:refusal_result": _said,
        "association.query.compose:answer": _said,
    }
    argued = {"association.query.compose:answer": _planned_argument}
    for boundary in BOUNDARIES:
        module_name, function_name = boundary.split(":")
        original = getattr(importlib.import_module(module_name), function_name)
        _rebind(original, _recording(out, boundary, original, shown.get(boundary, as_returned), argued.get(boundary)))

    def as_snapshot(answer: Any, args: tuple[Any, ...]) -> Any:
        return snapshot(args[0].reading, answer, planned=args[0].planned, unanswered=args[0].unanswered, unsaid=args[0].unsaid)

    ask = agent_module.Agent.ask
    agent_module.Agent.ask = _recording(out, "Agent.ask", ask, as_snapshot)  # type: ignore[method-assign]
