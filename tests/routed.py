"""Hand a test's own reading of the words to the parser's last step, at
the reader's boundary.

The package has one path: ``parse.read_route`` reads the question into the
Reading. A test that wants a particular reading of the words - a key and a
scope of its own - says so HERE, and hands it to the parser's last step
(``parse._reading``), standing in for the words:

- :class:`Routed` is that reading - the key the words ask and the typed
  filters, with no subject until :func:`with_subject` reads one;
- :func:`with_subject` reads the subject the way ``read_route`` does - once,
  before any key is settled - and puts it on the reading;
- :func:`reading_of` is the Reading the parser's last step writes from it;
- :func:`ask_routed` answers a question through the whole ``Agent`` with
  ``read_route`` replaced by that Reading, and no model asked;
- :func:`staged` is the grammar's rules and the taggers over a test's
  question, under a key, with the names typed (:class:`Staged`).

And one stage on: :func:`planned_answer` is the compiler's answer to a
Reading a test built, PLANNED first as the answering loop plans it
(``compose.plan.plan_point``) - ``compose.answer`` takes its planning and
never plans - and :func:`planned_query` the query the default point of a
key over a test's reading plans to; :func:`answered_as` is the answer side
for one key, called with a Reading a test states in its typed filters.
What the answer side says is an :class:`~association.query.answer.Answer`
the answering loop has not stamped yet, whose data is always there: a test
reads it as :class:`Said` (:func:`said`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, cast
from unittest import mock

import duckdb

from association.query import compose, grammar, parse
from association.query.agent import Agent
from association.query.answer import Answer, AnswerContext, Artifact
from association.query.compose.core import Query
from association.query.compose.plan import plan, plan_point
from association.query.decisions import Decision
from association.query.entities import names_of
from association.query.line import lines_named
from association.query.measure import key_of
from association.query.names import NameIndex
from association.query.normalizer import Normalized
from association.query.point import default_point
from association.query.reading import PLAYER_RANKING, Claim, Companion, Line, Measure, PointShape, Reading, Subject, SubjectKind, Unsupported
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
    decisions and claims the reading made."""

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
    to carry who the question is about (:func:`with_subject`)."""
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


class Said(Protocol):
    """What the answer side said, as a test reads it: the sayer's (or the
    compiler's own sentence's) :class:`~association.query.answer.Answer`,
    before the answering loop stamps it, whose ``data`` is always there -
    only a refused answer, which the answering loop alone builds, has none."""

    @property
    def text(self) -> str:
        """The sentence."""
        ...

    @property
    def data(self) -> dict[str, Any]:
        """The same answer as values."""
        ...

    @property
    def artifacts(self) -> list[Artifact]:
        """The files it wrote."""
        ...


def said(answer: Answer) -> Said:
    """``answer``, which the answer side said, read as :class:`Said`: its
    data asserted there."""
    assert answer.data is not None, answer
    return cast("Said", answer)


def planned_answer(ctx: AnswerContext, reading: Reading, *, declined: Callable[[str], None] | None = None) -> Said | None:
    """``compose.answer`` for ``reading``, planned here as ``Agent.ask``
    plans a question's Reading: once, before the compiler is handed it."""
    answered = compose.answer(ctx, reading, planned=plan_point(reading), declined=declined)
    return None if answered is None else said(answered)


def answered_as(shape: PointShape | None) -> Callable[[AnswerContext, Reading], Said]:
    """The answer side for the key ``shape``, called the way a test states a
    reading: a Reading in - the typed filters alone, with no question words,
    which would move the point - and what was said out (:class:`Said`).
    The subject is the one the reading's names make, as a question naming
    just them reads (a ranking within a team reads the team's players,
    never its own figure); the point is planned as the answering loop plans
    it (:func:`planned_answer`). ``Unsupported`` carries the decline's
    reason where nothing answers the point."""

    def answered(ctx: AnswerContext, reading: Reading) -> Said:
        named = reading.who.players
        kind: SubjectKind = "pair" if len(named) > 1 else "player" if named else "team" if reading.who.team else "everyone"
        if kind == "team" and shape == PLAYER_RANKING:
            kind = "team_players"
        subject = reading.subject or ReadSubject(kind, players=named, teams=(reading.who.team,) if reading.who.team else ())
        why: list[str] = []
        result = planned_answer(ctx, parse.with_point("", Reading(**reading.filters(), asked=shape, subject=subject)), declined=why.append)
        if result is None:
            raise Unsupported(why[0] if why else f"the compiler has no reading of this {shape} point")
        return result

    return answered


@dataclass(frozen=True)
class Staged:
    """What the grammar's rules and the taggers read for a test's question
    (:func:`staged`): the key the words ask, the typed scope, and the claims
    of every rule and tagger."""

    asked: PointShape | None
    scope: Reading = field(default_factory=Reading)
    claims: tuple[Claim, ...] = ()

    def __getattr__(self, name: str) -> Any:
        """A filter by its name (``who``, ``cuts``, ...), the reading's own."""
        if name.startswith("__") or name == "scope":
            raise AttributeError(name)
        return getattr(self.scope, name)


def staged(
    asked: PointShape | None,
    question: str,
    companions: tuple[Companion, ...] = (),
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
    ``asked`` (``grammar.settle``, then ``parse._tagged``): who the subject
    reading hands the rules (``who``, the ``opponent``) and the companions,
    typed, and the model's stat (a test's side, shot value and columns) the
    measure tagger's context."""
    stat = stat if stat is not None and stat.strip() else None
    # The rules read the lines as the parser hands them: each number
    # marked, the typed lines beside it (parse._read_route_staged).
    named_lines = lines_named(question)
    settled = grammar.settle(named_lines.numbered(question), asked, subject=who or Subject(), opponent=opponent, companions=companions, stat=stat, lines=named_lines)
    if settled.coach:
        return Staged(settled.asked, claims=claimed(list(settled.claims)))
    scope, claims, _ = parse._tagged(question, settled, companions, lines=lines, model_stat=stat, side=side, shot_value=shot_value, fields=fields)
    return Staged(settled.asked, scope, claimed([*settled.claims, *claims]))


def planned_query(asked: PointShape, scope: Reading) -> Query:
    """The default point of the key ``asked`` over ``scope``
    (:func:`~association.query.point.default_point`), planned
    (``compose.plan.plan``) - a test's way to the compiler's query for a
    reading it states."""
    query = plan(default_point(asked, scope))
    assert isinstance(query, Query)
    return query


def measure_of(spelling: str, **fields: object) -> Measure:
    """The :class:`~association.query.reading.Measure` ``spelling`` names,
    through the measure catalog (``measure.key_of``) - a test's way to build
    one from a key or an alias; ``fields`` are the Measure's other fields."""
    if not spelling.strip():
        return Measure(**fields)  # type: ignore[arg-type]
    key, whose, side, how, category = key_of(spelling)
    values: dict[str, object] = {"key": key, "as_typed": spelling, "whose": whose, "side": side, "how": how, "category": category, **fields}
    return Measure(**values)  # type: ignore[arg-type]
