"""Hand a test's own route to the stages after the reader, at the reader's
boundary.

Until 5.0.0's last change a test answered a hand-built route through
``Agent.ask(route=...)`` and ``parse.reading_from_route`` read a subject of
its own for it - a second path through the parser that production never
took, and one that read the subject on a different scope than the live one
(``ROADMAP.md``, Phase 1's order from here, step 1). The package has one
path now: ``read_route`` reads the question and its route carries the one
reading of the subject. A test that wants a particular route says so HERE,
by standing in for ``read_route``:

- :func:`slots_route` is the route a slot dict names, with no subject;
- :func:`with_subject` reads the subject the way ``read_route`` does - once,
  before any intent is known - and puts it on the route;
- :func:`ask_routed` answers a question through the whole ``Agent`` with
  ``read_route`` replaced by that route, and no model asked.

And one stage on: :func:`planned_answer` is the compiler's answer to a
Reading a test built, PLANNED first as the answering loop plans it
(``compose.plan.plan_point``) - ``compose.answer`` takes its planning and
never plans (step 2 of the same order).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any
from unittest import mock

import duckdb
from shapes import asked

from association.query import compose
from association.query.agent import Agent
from association.query.answer import Answer, AnswerContext, Reply
from association.query.compose.core import Query
from association.query.compose.plan import plan, plan_point
from association.query.normalizer import Normalized
from association.query.point import default_point
from association.query.reading import Companion, Line, Reading, Scope, Subject
from association.query.router import Named, Route, _settle, settle
from association.query.subject import child_named, read_subject, settle_subject


def slots_route(intent: str, slots: Mapping[str, Any] | None = None) -> Route:
    """The route ``intent`` (a retired name, translated to what the words
    ask: ``shapes.asked``) and ``slots`` name, through the Scope's one door
    (``Scope.from_slots`` raises ``ScopeError`` for a value no field holds).
    It carries no subject: :func:`with_subject` reads one."""
    return Route(asked(intent), Scope.from_slots(slots or {}))


def with_subject(con: duckdb.DuckDBPyConnection, question: str, route: Route) -> Route:
    """``route`` carrying who ``question`` is about, read as ``read_route``
    reads it - under no intent, from the route's own scope - and settled
    under the route's intent, or under the child the subject's shape names
    for it (``subject.child_named``), which the route then carries. The
    stages do not run here: a child the stages would decline stands."""
    if route.subject is not None:
        return route
    read = read_subject(con, question, None, route.scope)
    named = child_named(read, route.asked, question)
    chosen, words = named if named is not None else (route.asked, None)
    return replace(route, asked=chosen, subject=settle_subject(read, chosen, parent=route.asked, words=words))


def ask_routed(agent: Agent, question: str, route: Route, *, label: str = "") -> Answer:
    """``agent``'s answer to ``question`` with ``route`` standing in for
    what the parser would have read. The normalizer is not asked."""

    def read_route(con: duckdb.DuckDBPyConnection, asked: str, names: list[str] | None = None, stat: str = "") -> tuple[Route, Any, str]:
        routed = with_subject(con, asked, route)
        return routed, routed.subject, route.intent

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
#: model's schema): the stages take them typed since Phase 3, step 2.
_NAME_SLOTS = ("player", "players", "team", "teams", "opponent")


def handed(question: str, slots: Mapping[str, Any]) -> Named:
    """What the subject reading hands the stages for ``question``
    (``router.Named``), from the names a test's slot dict carries: the
    typed subject they name (``reading.Subject.from_slots``), the opponent,
    and the grammar's subject and the team words read from the words."""
    names = {key: slots[key] for key in ("player", "players", "team", "teams") if key in slots}
    opponent = slots.get("opponent")
    return Named.of(question, subject=Subject.from_slots(names), opponent=opponent if isinstance(opponent, str) and opponent.strip() else None)


def staged(intent: str, slots: Mapping[str, Any], question: str, companions: tuple[Companion, ...] = (), *, lines: tuple[Line, ...] = ()) -> Route:
    """The stages (``router.settle``) over a test's slot dict, its names
    handed typed (:func:`handed`) and the rest - the model's stat, a test's
    side, shot value and columns - as the slots."""
    return settle(asked(intent), {key: value for key, value in slots.items() if key not in _NAME_SLOTS}, question, companions, lines=lines, handed=handed(question, slots))


def staged_raw(raw: Mapping[str, Any], question: str, companions: tuple[Companion, ...] = ()) -> Route:
    """The stages (``router._settle``) over a raw route a test spells as the
    router's model reply did (the intent and its slots), its names handed
    typed (:func:`handed`)."""
    given = {key: value for key, value in raw.items() if key not in _NAME_SLOTS and key != "intent"}
    return _settle({**given, "asked": asked(raw["intent"])}, question, companions, handed=handed(question, raw))
