"""One question's four records - reading, query, result, answer - as plain
values, and the comparison of two such records.

A change to the pipeline is proven by writing each
stage's output per question on both trees and comparing them stage by
stage, so a difference names the stage it first appears in instead of only
the sentence it ends as. :func:`snapshot` is the record; :func:`differences`
is the comparison, and ``scripts/stage_snapshots.py`` runs both over a
recorded corpus with no model.

The record is what the stages hold today:

``reading``
    What the parser settled (:func:`~association.query.parse.read_route`):
    the key the words asked, the typed filters, who the question is about,
    the names it could not place, each decision it made, the point read
    onto it and the point reader's verdict
    (:attr:`~association.query.reading.Reading.point_verdict`) - the typed
    Reading.
``query``
    What the planner built from that point
    (:func:`~association.query.compose.plan.plan_point`, the Query-or-Cause):
    the :class:`~association.query.compose.core.Query` or
    :class:`~association.query.compose.team.TeamQuery` the compiler runs,
    or why there is none - the refusal, as the sayer words it, or the
    decline's sentence.
``result``
    The answer's structured values (:attr:`Answer.data <association.query.answer.Answer.data>`).
``answer``
    The text, which path answered, and what was drawn.

Nothing here reads the warehouse, the question's words or a model.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from association.query.reading import Reading, Unsupported
from association.query.result import Clarify, Refusal

if TYPE_CHECKING:
    from association.query.answer import Answer
    from association.query.compose.plan import Verdict
    from association.query.reading import Filtered

STAGES: tuple[str, ...] = ("reading", "query", "result", "answer")
"""The stages a snapshot holds, in the order a question passes through them.

.. versionadded:: 5.0.0
"""

FLOAT_TOLERANCE = 1e-9
"""How far two floats may sit apart, relative and absolute, and still be the
same number to :func:`differences`. DuckDB's parallel ``SUM`` moves the 15th
digit between runs (AGENTS.md, "Run the original twice"); a snapshot run is
single-threaded, and this is the margin left over.

.. versionadded:: 5.0.0
"""

WORDING: dict[str, frozenset[str]] = {
    "reading": frozenset(),
    "query": frozenset(),
    "result": frozenset({"headline", "message", "notes", "name_readings", "narrowing", "question_shape"}),
    "answer": frozenset({"text"}),
}
"""Per stage, the top-level keys that hold sentences rather than values -
what :func:`differences` leaves out when ``wording`` is False, for a change
that is allowed to reword an answer but not to move a number (ROADMAP,
decision D1). ``notes`` is listed here because a rewording check cannot
see a caveat that vanished from the sentence; a caveat carried as a typed
note is compared by its kind instead.

.. versionadded:: 5.0.0
"""

# What a Reading's subject carries that is about HOW it was read rather
# than what was read: the evidence prose and the claims, positions in the
# text, which the Reading's own ``unread`` stands for.
# The companions' phrases are positions too; the model's names
# the question supports (``supported``) restate the names the record's
# input already holds (#373).
_SUBJECT_LEFT_OUT = frozenset({"evidence", "claims", "companion_phrases", "supported"})

# The point's own fields - the algebra as the reader read it - in the order
# the trace prints them (``reading.POINT_FIELDS``, the fields the point
# reader writes beside the filters), recorded where the point was read; the
# filters are recorded once, the point's (``_filters_record``), since the
# point reader writes them onto the Reading it reads.
# ``shape``, ``by`` and ``on`` are the point reader's declaration, one
# field (``Reading.read_as``, D9) recorded by its three parts under the
# names they are compared by (``_DECLARED``), so records compare across
# trees; the compiler's skeleton and source are derived by the planner and
# recorded on the query record. ``subject_span`` is the
# one typed override of the span the subject is settled in.
_POINT_FIELDS = ("relation", "on", "shape", "by", "measures", "group", "predicates", "order", "direction", "limit", "minimum_games", "available", "subject_span")


#: The point's declaration (``Reading.read_as``, one ``PointShape``, D9) by
#: the record names of its three parts.
_DECLARED = {"on": "relation", "shape": "shape", "by": "by"}


def _point_field(point: Reading, name: str) -> Any:
    """``point``'s field ``name`` as the record names it: ``on``, ``shape``
    and ``by`` are the parts of what it is read as."""
    if name in _DECLARED:
        return getattr(point.read_as, _DECLARED[name])
    return getattr(point, name)


def plain(value: Any, *, mask: Mapping[str, str] | None = None) -> Any:
    """``value`` as JSON-ready values, the same on every run: a dataclass as
    a dict of its fields, a tuple as a list, a set sorted, a date in ISO
    form, a path as its string. ``mask`` replaces substrings in every string
    (a scratch directory with a fixed word), so two runs that wrote their
    charts to different places still compare equal. A value of a type this
    does not know is kept as ``{"unknown": type name, "repr": ...}`` rather
    than dropped, so it shows up in a comparison instead of hiding in one.

    .. versionadded:: 5.0.0
    """
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Enum):
        # A closed enum's member (``reading.Aggregate``) as its value: the
        # text the field held before it was typed, so the record reads the same.
        return plain(value.value, mask=mask)
    if isinstance(value, str):
        return _masked(value, mask)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return _masked(str(value), mask)
    if is_dataclass(value) and not isinstance(value, type):
        return _plain_record(value, mask)
    if isinstance(value, Mapping):
        return {str(key): plain(each, mask=mask) for key, each in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(each, mask=mask) for each in value]
    if isinstance(value, (set, frozenset)):
        return sorted((plain(each, mask=mask) for each in value), key=repr)
    item = getattr(value, "item", None)
    if callable(item):
        # A numpy scalar DuckDB handed back: its Python value.
        return plain(item(), mask=mask)
    return {"unknown": type(value).__name__, "repr": _masked(repr(value), mask)}


def _plain_record(value: Any, mask: Mapping[str, str] | None) -> dict[str, Any]:
    """A dataclass as a dict of its fields: every typed value is recorded
    as its own fields."""
    if isinstance(value, Reading):
        # A Reading's declaration by its three parts, so records compare
        # across trees.
        record: dict[str, Any] = {}
        for f in fields(value):
            if f.name == "read_as":
                record.update({name: plain(getattr(value.read_as, part), mask=mask) for name, part in (("shape", "shape"), ("by", "by"), ("on", "relation"))})
            else:
                record[f.name] = plain(getattr(value, f.name), mask=mask)
        return record
    return {f.name: plain(getattr(value, f.name), mask=mask) for f in fields(value)}


def _filters_record(filtered: Filtered, mask: Mapping[str, str] | None) -> dict[str, Any]:
    """The ten typed filters by field name (``who``, ``cuts``, ``companions``,
    ``lines``, ``measure``, ``span``, ``split``, ``period``, ``window``, and
    ``minimum`` since ISSUES.md #377) as the reader typed them - the record
    of a Reading and of its point."""
    return {name: plain(value, mask=mask) for name, value in filtered.filters().items()}


def _masked(text: str, mask: Mapping[str, str] | None) -> str:
    """``text`` with each of ``mask``'s substrings replaced."""
    for found, shown in (mask or {}).items():
        text = text.replace(found, shown)
    return text


def _reading_record(reading: Reading, mask: Mapping[str, str] | None) -> dict[str, Any]:
    """What the parser settled, without the question's text or the prose
    about how it was read."""
    subject = None
    if reading.subject is not None:
        # The subject reading's own record: the typed subject's fields and
        # the parser's beside them, the evidence prose and the claims left out.
        subject = {f.name: plain(getattr(reading.subject, f.name), mask=mask) for f in fields(reading.subject) if f.name not in _SUBJECT_LEFT_OUT}
    point = None
    if reading.has_point:
        # The point's own fields, which the point reader wrote beside the
        # filters (the filters are the point's, recorded once, above).
        point = {name: plain(_point_field(reading, name), mask=mask) for name in _POINT_FIELDS}
    return {
        # The key the words asked (``Reading.asked``).
        "asked": plain(reading.asked, mask=mask),
        # Who the read is about, the games' cuts, the companions, the lines,
        # the measure, the span, the split, the period and the window as the
        # reader typed them - the typed Reading. The typed subject
        # (``Reading.who``) is ``who``: ``subject``
        # is the subject reading's own record of it, the parser's fields beside.
        **_filters_record(reading, mask),
        # The content words no claim covers: what the
        # claims ledger checks from outside, word for word.
        "unread": list(reading.unread),
        "subject": subject,
        "misread": list(reading.misread),
        "decisions": [plain(decision.as_dict(), mask=mask) for decision in reading.decisions],
        "point": point,
        # The point reader's verdict: "read", why it declined, or the cause
        # it refuses by - recorded so a
        # decline that moves between the reader and the planner with the
        # same sentence is a difference in this stage, not only in the next.
        "point_verdict": plain(reading.point_verdict, mask=mask),
        # What the words come to before any point, and what they name that
        # nothing reads: recorded where the Reading holds one.
        **({"refused": plain(reading.refused, mask=mask)} if reading.refused is not None else {}),
        **({"unsupported": plain(reading.unsupported, mask=mask)} if reading.unsupported else {}),
        **({"left_out": plain(reading.left_out, mask=mask)} if reading.left_out is not None else {}),
        **({"defaulted": plain(reading.defaulted, mask=mask)} if reading.defaulted is not None else {}),
        # The other readings of a defaulted span, each a ready question
        # (decision D23): recorded where the Reading holds one.
        **({"alternatives": plain(reading.alternatives, mask=mask)} if reading.alternatives else {}),
    }


def _query_record(reading: Reading, planned: Verdict, mask: Mapping[str, str] | None) -> dict[str, Any]:
    """What the planner built, or why there is none: a refusal the reading
    or the planner came to - recorded as the sayer words it, its page's
    values and its sentence - or the reason one of them declined.
    ``planned`` is the caller's planning of the question, the Query-or-Cause
    (:func:`~association.query.compose.plan.plan_point`); nothing here
    plans, and the point is the reading's record, so a planner that builds
    something else from it is seen here. The planned query is the one the
    compiler runs (a season-line point its reader does not read is planned
    as the game-level one), and a refusal is worded here by the sayer that
    words it in the answer."""
    if isinstance(planned, (Refusal, Clarify)):
        # Worded at call time: the sayer is the answer side's, which this
        # module otherwise stays clear of.
        from association.query.compose.say import say

        said = say(planned)
        return {"refused": plain(said.data, mask=mask), "said": _masked(said.text, mask)}
    if isinstance(planned, Unsupported) or not reading.has_point:
        return {"declined": str(planned) if isinstance(planned, Unsupported) else None}
    # A TeamQuery has no ``subject``: the team IS the relation. Read by
    # shape rather than imported, so this module stays clear of the compiler.
    return {"relation": getattr(planned, "subject", "team"), **plain(planned, mask=mask)}


def read_stages(reading: Reading, *, planned: Verdict, mask: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The two records a Reading holds - ``reading`` and ``query`` - as
    :func:`snapshot` writes them, for a caller that stops before the answer
    (``scripts/claims_ledger.py``, which asks which of a question's words
    the reading depends on), with its own planning of the Reading.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       ``planned`` is the Query-or-Cause
       (:data:`~association.query.compose.plan.Verdict`), where it was the
       ``Planned`` record (Phase 4, step 4).
    """
    return {"reading": _reading_record(reading, mask), "query": _query_record(reading, planned, mask)}


def snapshot(
    reading: Reading | None,
    answer: Answer,
    *,
    planned: Verdict | None = None,
    unanswered: str | None = None,
    unsaid: list[str] | None = None,
    mask: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """One question's four records as JSON-ready values. ``reading`` is None
    where the question was refused before anything read it (too short, a
    normalizer that could not be reached); ``unanswered`` is the reason a
    question nothing reads was given up with
    (:attr:`Agent.unanswered <association.query.agent.Agent.unanswered>`);
    ``planned`` the answering loop's planning of the question, the
    Query-or-Cause (:attr:`Agent.query <association.query.agent.Agent.query>`),
    whose query is the one the compiler runs; ``unsaid`` the kinds of the remarks written and not said
    (:attr:`Agent.unsaid <association.query.agent.Agent.unsaid>`); ``mask``
    is :func:`plain`'s. The record also holds ``remarks``: the answer's
    notes and the decisions it states.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       ``planned`` is the Query-or-Cause
       (:data:`~association.query.compose.plan.Verdict`), where it was the
       ``Planned`` record (Phase 4, step 4).
    """
    stated = [decision for decision in answer.decisions if decision.kind]
    return {
        "question": answer.question,
        # Beside the four stages, not one of them: what the answer said
        # about its data and its own choices, as kinds and facts
        # (query/notes.py). Compared whenever both runs hold it.
        "remarks": {
            "notes": [plain(each.as_dict(), mask=mask) for each in answer.notes],
            "decisions": [plain(decision.as_dict(), mask=mask) for decision in stated],
            "unsaid": list(unsaid or ()),
        },
        **(read_stages(reading, planned=_planned_for(reading, planned), mask=mask) if reading is not None else {"reading": None, "query": None}),
        "result": plain(answer.data, mask=mask),
        "answer": {
            "text": _masked(answer.text, mask),
            "answered_by": answer.answered_by,
            # The planned point's shape, which the page renders by.
            "shape": plain(answer.shape, mask=mask),
            "artifacts": [artifact.kind for artifact in answer.artifacts],
            "unanswered": unanswered,
            # The alternatives offered beside the answer - the page's
            # buttons - recorded where it offers one.
            **({"alternatives": plain(answer.alternatives, mask=mask)} if answer.alternatives else {}),
        },
    }


def _planned_for(reading: Reading, planned: Verdict | None) -> Verdict:
    """``planned``, which a snapshot of a question that was read must be
    handed: planning it here instead was a second planner path, taken by
    whoever forgot."""
    if planned is None:
        raise ValueError("a snapshot of a Reading needs its planning (Agent.query, or compose.plan.plan_point(reading))")
    return planned


@dataclass(frozen=True)
class Difference:
    """One place two snapshots of a question disagree: the ``stage``, the
    ``path`` inside it (``games[3].points``), the two values, and ``kind`` -
    ``"changed"``, ``"missing"`` (only before), ``"added"`` (only after),
    ``"type"`` (an int where a float was, a list where a dict was) or
    ``"reordered"`` (a list holding the same items in another order, which
    is how two rows tied on a ranking's sort key differ between runs).

    .. versionadded:: 5.0.0
    """

    stage: str
    path: str
    kind: str
    before: Any
    after: Any

    def line(self) -> str:
        """The one-line form a report prints."""
        return f"{self.stage}: {self.path or '(whole)'} {self.kind}: {_short(self.before)} -> {_short(self.after)}"


def _short(value: Any) -> str:
    """A value cut to what fits on a report line."""
    text = repr(value)
    return text if len(text) <= 160 else text[:157] + "..."


def differences(before: Mapping[str, Any], after: Mapping[str, Any], *, tolerance: float = FLOAT_TOLERANCE, wording: bool = True) -> list[Difference]:
    """Every place two snapshots of one question disagree, stage by stage,
    in stage order - so the first one names the stage a change entered at.
    Floats within ``tolerance`` are the same number; an int and a float are
    not the same value (``5`` against ``5.0`` is a ``"type"`` difference). With
    ``wording`` False the sentences (:data:`WORDING`) are left out.

    .. versionadded:: 5.0.0
    """
    found: list[Difference] = []
    for stage in STAGES:
        left, right = before.get(stage), after.get(stage)
        if not wording:
            left, right = _without_wording(stage, left), _without_wording(stage, right)
        _walk(stage, "", left, right, tolerance, found)
    return found


def value_differences(label: str, before: Any, after: Any, *, tolerance: float = FLOAT_TOLERANCE) -> list[Difference]:
    """Every place two plain values disagree, by :func:`differences`' rules,
    each named under ``label`` in place of a stage - for a record that is
    not a whole snapshot (one call a unit test made across a stage boundary).

    .. versionadded:: 5.0.0
    """
    found: list[Difference] = []
    _walk(label, "", before, after, tolerance, found)
    return found


def _without_wording(stage: str, record: Any) -> Any:
    """A stage's record without the keys that hold sentences."""
    if not isinstance(record, dict):
        return record
    return {key: value for key, value in record.items() if key not in WORDING[stage]}


def _same_number(left: float, right: float, tolerance: float) -> bool:
    """Two floats within the tolerance, a NaN equal to a NaN."""
    if math.isnan(left) or math.isnan(right):
        return math.isnan(left) and math.isnan(right)
    return math.isclose(left, right, rel_tol=tolerance, abs_tol=tolerance)


def _walk(stage: str, path: str, left: Any, right: Any, tolerance: float, found: list[Difference]) -> None:
    """Append every difference between ``left`` and ``right`` under ``path``."""
    if type(left) is not type(right):
        found.append(Difference(stage, path, "type" if left is not None and right is not None else "changed", left, right))
    elif isinstance(left, dict):
        for key in sorted(left.keys() | right.keys()):
            below = f"{path}.{key}" if path else key
            if key not in right:
                found.append(Difference(stage, below, "missing", left[key], None))
            elif key not in left:
                found.append(Difference(stage, below, "added", None, right[key]))
            else:
                _walk(stage, below, left[key], right[key], tolerance, found)
    elif isinstance(left, list):
        _walk_list(stage, path, left, right, tolerance, found)
    elif isinstance(left, float):
        if not _same_number(left, right, tolerance):
            found.append(Difference(stage, path, "changed", left, right))
    elif left != right:
        found.append(Difference(stage, path, "changed", left, right))


def _walk_list(stage: str, path: str, left: list[Any], right: list[Any], tolerance: float, found: list[Difference]) -> None:
    """Two lists, item by item - or, where they hold the same items in
    another order, the one ``"reordered"`` difference that says so."""
    within: list[Difference] = []
    for index in range(min(len(left), len(right))):
        _walk(stage, f"{path}[{index}]", left[index], right[index], tolerance, within)
    if len(left) == len(right) and within and _same_items(left, right, tolerance):
        found.append(Difference(stage, path, "reordered", left, right))
        return
    found.extend(within)
    found.extend(Difference(stage, f"{path}[{index}]", "missing", left[index], None) for index in range(len(right), len(left)))
    found.extend(Difference(stage, f"{path}[{index}]", "added", None, right[index]) for index in range(len(left), len(right)))


def _same_items(left: list[Any], right: list[Any], tolerance: float) -> bool:
    """Whether ``right`` is ``left`` in another order: each item of one
    paired with a distinct, equal item of the other."""
    unpaired = list(right)
    for item in left:
        for index, other in enumerate(unpaired):
            probe: list[Difference] = []
            _walk("", "", item, other, tolerance, probe)
            if not probe:
                del unpaired[index]
                break
        else:
            return False
    return True
