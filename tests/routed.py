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

from association.query import compose
from association.query.agent import Agent
from association.query.answer import Answer
from association.query.compose.plan import plan_point
from association.query.normalizer import Normalized
from association.query.reading import Reading, Scope
from association.query.router import Route
from association.query.subject import child_named, read_subject, settle_subject
from association.query.templates.common import TemplateContext, TemplateResult


def slots_route(intent: str, slots: Mapping[str, Any] | None = None) -> Route:
    """The route ``intent`` and ``slots`` name, through the Scope's one door
    (``Scope.from_slots`` raises ``ScopeError`` for a value no field holds).
    It carries no subject: :func:`with_subject` reads one."""
    return Route(intent, Scope.from_slots(slots or {}))


def with_subject(con: duckdb.DuckDBPyConnection, question: str, route: Route) -> Route:
    """``route`` carrying who ``question`` is about, read as ``read_route``
    reads it - under no intent, from the route's own scope - and settled
    under the route's intent, or under the child the subject's shape names
    for it (``subject.child_named``), which the route then carries. The
    stages do not run here: a child the stages would decline stands."""
    if route.subject is not None:
        return route
    read = read_subject(con, question, "other", route.scope)
    named = child_named(read, route.intent, question)
    intent, words = named if named is not None else (route.intent, None)
    return replace(route, intent=intent, subject=settle_subject(read, intent, parent=route.intent, words=words))


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


def planned_answer(ctx: TemplateContext, reading: Reading, *, declined: Callable[[str], None] | None = None) -> TemplateResult | None:
    """``compose.answer`` for ``reading``, planned here as ``Agent.ask``
    plans a question's Reading: once, before the compiler is handed it."""
    return compose.answer(ctx, reading, planned=plan_point(reading), declined=declined)
