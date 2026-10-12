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
from shapes import asked as _asked
from slots import slots_scope

from association.query import compose, grammar, parse
from association.query.agent import Agent
from association.query.answer import Answer, AnswerContext, Reply
from association.query.compose.core import Query
from association.query.compose.plan import plan, plan_point
from association.query.decisions import Decision
from association.query.entities import names_of
from association.query.line import lines_named
from association.query.names import NameIndex
from association.query.normalizer import Normalized
from association.query.point import default_point
from association.query.reading import Claim, Companion, Line, PointShape, Reading, Subject, asked_label
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
    scope: Reading = field(default_factory=Reading)
    subject: ReadSubject | None = None
    decisions: tuple[Decision, ...] = ()
    claims: tuple[Claim, ...] = ()

    def __getattr__(self, name: str) -> Any:
        """A filter by its name (``who``, ``cuts``, ...), the reading's own."""
        if name.startswith("__") or name == "scope":
            raise AttributeError(name)
        return getattr(self.scope, name)

    @property
    def intent(self) -> str:
        """The key's label, as the trace prints it."""
        return asked_label(self.asked)


def slots_route(intent: str, slots: Mapping[str, Any] | None = None) -> Routed:
    """The reading ``intent`` (a retired name, translated to what the words
    ask: ``shapes.asked``) and ``slots`` name, through the Scope's one door
    (``Scope.from_slots`` raises ``ScopeError`` for a value no field holds).
    It carries no subject: :func:`with_subject` reads one."""
    return Routed(_asked(intent), slots_scope(slots or {}))


def with_subject(con: duckdb.DuckDBPyConnection | NameIndex, question: str, route: Routed) -> Routed:
    """``route`` carrying who ``question`` is about, read as ``read_route``
    reads it - under no key, from the reading's own scope - and settled
    under the reading's key, or under the child the subject's shape names
    for it (``subject.child_named``), which the reading then carries. The
    grammar's rules do not run here: a child they would decline stands."""
    if route.subject is not None:
        return route
    read = read_subject(_index(con), question, None, route.scope)
    # The child grammars read the lines as the lines tagger types them, as
    # the parser hands them (``parse.read_route``).
    named = child_named(read, route.asked, lines_named(question).marked(question), original=question)
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
    return default_point(_asked(intent), slots_scope(dict(slots)))


def default_query(intent: str, slots: Mapping[str, Any]) -> Query:
    """The intent's default point over ``slots``, planned: what the retired
    ``compose.adapt.to_query`` gave (deleted 2026-10-03; tests were its only
    callers)."""
    query = plan(default_reading(intent, slots))
    assert isinstance(query, Query)
    return query


@dataclass(frozen=True)
class Staged:
    """What the grammar's rules and the taggers read for a test's question
    (:func:`staged`): the key the words ask, the typed scope, and the claims
    of every rule and tagger. The router's ``Route`` until Phase 3, step 4,
    slice (b)."""

    asked: PointShape | None
    scope: Reading = field(default_factory=Reading)
    claims: tuple[Claim, ...] = ()

    def __getattr__(self, name: str) -> Any:
        """A filter by its name (``who``, ``cuts``, ...), the reading's own."""
        if name.startswith("__") or name == "scope":
            raise AttributeError(name)
        return getattr(self.scope, name)

    @property
    def intent(self) -> str:
        """The key's label, as the trace prints it."""
        return asked_label(self.asked)


def handed(question: str, slots: Mapping[str, Any]) -> tuple[Subject, str | None]:
    """Who the subject reading hands the grammar's rules for ``question``,
    from the names a test's slot dict carries: the typed subject they name
    (``reading.Subject.from_slots``) and the opponent."""
    names = {key: slots[key] for key in ("player", "players", "team", "teams") if key in slots}
    opponent = slots.get("opponent")
    return Subject.from_slots(names), opponent if isinstance(opponent, str) and opponent.strip() else None


def staged(
    asked: PointShape | str | None,
    question: str | Mapping[str, Any],
    companions: tuple[Companion, ...] | str = (),
    old_companions: tuple[Companion, ...] = (),
    *,
    lines: tuple[Line, ...] = (),
    who: Subject | None = None,
    opponent: str | None = None,
    stat: str | None = None,
    side: str | None = None,
    shot_value: int | None = None,
    fields: tuple[str, ...] = (),
) -> Staged:
    """The grammar's rules then the taggers over ``question``, under the key
    ``asked`` (``router.settle`` until Phase 3, step 4, slice (b)): who the
    subject reading hands the rules (``who``, the ``opponent``) and the
    companions typed, the model's stat (a test's side, shot value and
    columns) the measure tagger's context."""
    if isinstance(asked, str):  # TRANSITIONAL: a retired label and a slot dict, until the last caller is re-seated
        assert isinstance(question, Mapping) and isinstance(companions, str)
        names = {key: question[key] for key in ("player", "players", "team", "teams") if key in question}
        given_opponent = question.get("opponent")
        given_stat = question.get("stat")
        given_fields = question.get("fields")
        return staged(
            _asked(asked),
            companions,
            old_companions,
            lines=lines,
            who=Subject.from_slots(names),
            opponent=given_opponent if isinstance(given_opponent, str) and given_opponent.strip() else None,
            stat=given_stat if isinstance(given_stat, str) else None,
            side=question.get("side"),
            shot_value=question.get("shot_value"),
            fields=tuple(given_fields) if isinstance(given_fields, (list, tuple)) else (),
        )
    assert isinstance(question, str) and not isinstance(companions, str)
    stat = stat if stat is not None and stat.strip() else None
    # The rules read the lines as the parser hands them: each number
    # marked, the typed lines beside it (parse._read_route_staged).
    named_lines = lines_named(question)
    settled = grammar.settle(named_lines.numbered(question), asked, subject=who or Subject(), opponent=opponent, companions=companions, stat=stat, lines=named_lines)
    if settled.coach:
        return Staged(settled.asked, claims=claimed(list(settled.claims)))
    scope, claims = parse._tagged(question, settled, companions, lines=lines, model_stat=stat, side=side, shot_value=shot_value, fields=fields)
    return Staged(settled.asked, scope, claimed([*settled.claims, *claims]))
