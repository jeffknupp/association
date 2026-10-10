"""Hand a test's own reading of the words to the parser's last step, at
the reader's boundary.

Until 5.0.0's last change a test answered a hand-built route through
``Agent.ask(route=...)`` and ``parse.reading_from_route`` read a subject of
its own for it - a second path through the parser that production never
took, and one that read the subject on a different scope than the live one
(``ROADMAP.md``, Phase 1's order from here, step 1). The package has one
path: ``read_route`` reads the question into the Reading, and since Phase 3,
step 4, slice (b) no route is carried between the words and the Reading.
A test that wants a particular reading of the words - a key and a scope of
its own - says so HERE, and hands it to the parser's last step
(``parse._reading``), standing in for the words:

- :func:`slots_route` is the reading a slot dict names, with no subject
  (:class:`Routed`);
- :func:`with_subject` reads the subject the way ``read_route`` does - once,
  before any key is settled - and puts it on the reading;
- :func:`reading_of` is the Reading the parser's last step writes from it;
- :func:`ask_routed` answers a question through the whole ``Agent`` with
  ``read_route`` replaced by that Reading, and no model asked;
- :func:`staged` and :func:`staged_raw` are the grammar's rules and the
  taggers over a test's slot dict or model-era reply (the router's stages
  until the slice; :class:`Staged`).

And one stage on: :func:`planned_answer` is the compiler's answer to a
Reading a test built, PLANNED first as the answering loop plans it
(``compose.plan.plan_point``) - ``compose.answer`` takes its planning and
never plans (step 2 of the same order).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any
from unittest import mock

import duckdb
from shapes import asked

from association.query import compose, grammar, parse
from association.query.agent import Agent
from association.query.answer import Answer, AnswerContext, Reply
from association.query.compose.core import Query
from association.query.compose.plan import plan, plan_point
from association.query.decisions import Decision
from association.query.entities import names_of
from association.query.names import NameIndex
from association.query.normalizer import Normalized
from association.query.point import default_point
from association.query.reading import PRESENCE_SPLIT, Claim, Companion, Line, PointShape, Reading, Scope, Subject, asked_label
from association.query.span import claimed
from association.query.subject import Subject as ReadSubject
from association.query.subject import child_named, read_subject, settle_subject


def _index(con: duckdb.DuckDBPyConnection | NameIndex) -> NameIndex:
    """The name index the reader takes, for a test holding a connection."""
    return con if isinstance(con, NameIndex) else names_of(con)


@dataclass(frozen=True)
class Routed:
    """A test's own reading of a question's words, as the parser's last step
    takes it (``parse._reading``): the key the words ask, the typed scope,
    who the question is about (read by :func:`with_subject`), and the
    decisions and claims the reading made. The ``router.Route`` it replaces
    carried the same into ``parse.reading_from_route`` until Phase 3, step
    4, slice (b)."""

    asked: PointShape | None
    scope: Scope = field(default_factory=Scope)
    subject: ReadSubject | None = None
    decisions: tuple[Decision, ...] = ()
    claims: tuple[Claim, ...] = ()

    @property
    def intent(self) -> str:
        """The key's label, as the trace prints it."""
        return asked_label(self.asked)

    @property
    def slots(self) -> dict[str, Any]:
        """The scope as a slot dict, the shape a test asserts on."""
        return self.scope.to_slots(split_by_presence=self.asked == PRESENCE_SPLIT)


def slots_route(intent: str, slots: Mapping[str, Any] | None = None) -> Routed:
    """The reading ``intent`` (a retired name, translated to what the words
    ask: ``shapes.asked``) and ``slots`` name, through the Scope's one door
    (``Scope.from_slots`` raises ``ScopeError`` for a value no field holds).
    It carries no subject: :func:`with_subject` reads one."""
    return Routed(asked(intent), Scope.from_slots(slots or {}))


def with_subject(con: duckdb.DuckDBPyConnection | NameIndex, question: str, route: Routed) -> Routed:
    """``route`` carrying who ``question`` is about, read as ``read_route``
    reads it - under no key, from the reading's own scope - and settled
    under the reading's key, or under the child the subject's shape names
    for it (``subject.child_named``), which the reading then carries. The
    grammar's rules do not run here: a child they would decline stands."""
    if route.subject is not None:
        return route
    read = read_subject(_index(con), question, None, route.scope)
    named = child_named(read, route.asked, question)
    chosen, words = named if named is not None else (route.asked, None)
    return replace(route, asked=chosen, subject=settle_subject(read, chosen, parent=route.asked, words=words))


def reading_of(con: duckdb.DuckDBPyConnection | NameIndex, question: str, route: Routed) -> Reading:
    """The Reading the parser's last step writes from ``route`` - which has
    to carry who the question is about (:func:`with_subject`), as
    ``reading_from_route`` required of a route."""
    if route.subject is None:
        raise ValueError("reading_of needs the reading's subject - who the question is about, as read_route reads it")
    return parse._reading(_index(con), question, route.asked, route.scope, route.subject, decisions=route.decisions, claims=route.claims, model_names=(), model_stat="")


def ask_routed(agent: Agent, question: str, route: Routed, *, label: str = "") -> Answer:
    """``agent``'s answer to ``question`` with ``route`` standing in for
    what the parser would have read from the words. The normalizer is not
    asked."""

    def read_route(index: NameIndex, asked: str, names: list[str] | None = None, stat: str = "") -> Reading:
        return reading_of(index, asked, with_subject(index, asked, route))

    with (
        mock.patch("association.query.normalizer.normalize", lambda model, asked: Normalized([], "")),
        mock.patch("association.query.parse.read_route", read_route),
    ):
        return agent.ask(question, label=label)


def planned_answer(ctx: AnswerContext, reading: Reading, *, declined: Callable[[str], None] | None = None) -> Reply | None:
    """``compose.answer`` for ``reading``, planned here as ``Agent.ask``
    plans a question's Reading: once, before the compiler is handed it."""
    return compose.answer(ctx, reading, planned=plan_point(reading), declined=declined)


def default_reading(intent: str, slots: Mapping[str, Any]) -> Reading:
    """The intent's default point over ``slots``, read: what the retired
    ``compose.adapt.to_reading`` gave (deleted with the last adapter in
    Phase 2, step 3; tests were its only callers) -
    :func:`~association.query.point.default_point` over the typed scope."""
    return default_point(asked(intent), Scope.from_slots(dict(slots)))


def default_query(intent: str, slots: Mapping[str, Any]) -> Query:
    """The intent's default point over ``slots``, planned: what the retired
    ``compose.adapt.to_query`` gave (deleted 2026-10-03; tests were its only
    callers)."""
    query = plan(default_reading(intent, slots))
    assert isinstance(query, Query)
    return query


#: The slot names a test's payload still carries names under (the router
#: model's schema): the grammar's rules take them typed since Phase 3, step 2.
_NAME_SLOTS = ("player", "players", "team", "teams", "opponent")

#: The measure's model-era slots a payload may carry: the measure tagger's
#: context (the model's key; a test's side, shot value and columns).
_MEASURE_CONTEXT = ("stat", "side", "shot_value", "fields")

#: The span, window, cuts, period, line and companion families' model-era
#: keys a payload may still carry: every one is read from the words by its
#: tagger, so none reaches the reading (the router dropped them at the
#: stages' door, ``router._MODEL_SPAN_KEYS``, until Phase 3, step 4).
_MODEL_KEYS = frozenset(
    {
        "season",
        "season_ref",
        "season_type",
        "season_type_unstated",
        "span",
        "since",
        "until",
        "order",
        "limit",
        "rank",
        "ranked_by",
        "date",
        "venue",
        "situation",
        "round",
        "game_n",
        "season_n",
        "own_team",
        "period",
        "half",
        "threshold",
        "above",
        "below",
        "period_condition",
        "with_player",
        "without",
        "conditions",
    }
)


@dataclass(frozen=True)
class Staged:
    """What the grammar's rules and the taggers read for a test's question
    (:func:`staged`): the key the words ask, the typed scope, and the claims
    of every rule and tagger. The router's ``Route`` until Phase 3, step 4,
    slice (b)."""

    asked: PointShape | None
    scope: Scope = field(default_factory=Scope)
    claims: tuple[Claim, ...] = ()

    @property
    def intent(self) -> str:
        """The key's label, as the trace prints it."""
        return asked_label(self.asked)

    @property
    def slots(self) -> dict[str, Any]:
        """The scope as a slot dict, the shape a test asserts on."""
        return self.scope.to_slots(split_by_presence=self.asked == PRESENCE_SPLIT)


def handed(question: str, slots: Mapping[str, Any]) -> tuple[Subject, str | None]:
    """Who the subject reading hands the grammar's rules for ``question``,
    from the names a test's slot dict carries: the typed subject they name
    (``reading.Subject.from_slots``) and the opponent."""
    names = {key: slots[key] for key in ("player", "players", "team", "teams") if key in slots}
    opponent = slots.get("opponent")
    return Subject.from_slots(names), opponent if isinstance(opponent, str) and opponent.strip() else None


def _staged(key: PointShape | None, given: Mapping[str, Any], question: str, companions: tuple[Companion, ...], lines: tuple[Line, ...]) -> Staged:
    """The grammar's rules then the taggers, over a test's payload: its
    names handed typed, its measure context the tagger's, its model-era
    keys dropped as the words are the one source; any other key is a
    payload this cannot read, said out loud."""
    left = sorted(set(given) - set(_NAME_SLOTS) - set(_MEASURE_CONTEXT) - _MODEL_KEYS - {"intent"})
    if left:
        raise ValueError(f"a payload key the reader does not read: {left}")
    subject, opponent = handed(question, given)
    stat = given.get("stat")
    stat = stat if isinstance(stat, str) and stat.strip() else None
    settled = grammar.settle(question, key, subject=subject, opponent=opponent, companions=companions, stat=stat)
    if settled.coach:
        return Staged(settled.asked, claims=claimed(list(settled.claims)))
    fields = given.get("fields")
    side = given.get("side")
    scope, claims = parse._tagged(
        question,
        settled,
        companions,
        lines=lines,
        model_stat=stat,
        side=side if isinstance(side, str) else None,
        shot_value=given.get("shot_value"),
        fields=tuple(fields) if isinstance(fields, (list, tuple)) else (),
    )
    return Staged(settled.asked, scope, claimed([*settled.claims, *claims]))


def staged(intent: str, slots: Mapping[str, Any], question: str, companions: tuple[Companion, ...] = (), *, lines: tuple[Line, ...] = ()) -> Staged:
    """The grammar's rules and the taggers over a test's slot dict, under
    ``intent`` (``router.settle`` until Phase 3, step 4, slice (b)): its
    names handed typed (:func:`handed`), the model's stat (a test's side,
    shot value and columns) the measure tagger's context, every other
    model-era slot ignored, as ``settle`` ignored it."""
    given = {key: value for key, value in slots.items() if key in _NAME_SLOTS or key in _MEASURE_CONTEXT}
    return _staged(asked(intent), given, question, companions, lines)


def staged_raw(raw: Mapping[str, Any], question: str, companions: tuple[Companion, ...] = ()) -> Staged:
    """The grammar's rules and the taggers over a raw reply a test spells as
    the router's model did (the intent and its slots), its names handed
    typed (:func:`handed`) - ``router._settle`` until Phase 3, step 4,
    slice (b)."""
    return _staged(asked(raw["intent"]), raw, question, companions, ())
