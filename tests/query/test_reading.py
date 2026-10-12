"""The Reading (query/reading.py) and the planner (compose/plan.py): one record
of what a question asks, planned into the compiler's point without reading
the question again - ROADMAP plan item 6, step (a)."""

from __future__ import annotations

from dataclasses import fields, replace

import pytest
from routed import planned_query

from association.query.compose.core import Query
from association.query.compose.plan import plan
from association.query.compose.team import TeamQuery
from association.query.point import default_point
from association.query.reading import GAMES_COUNTED, Companion, Cuts, Line, Measure, Period, Reading, Span, Window
from association.query.reading import Subject as Who


def test_the_default_point_is_a_reading_and_its_plan_is_the_query_it_always_was() -> None:
    """The adapters build a Reading; ``to_query`` is that Reading planned, so
    every caller of the old contract sees the same Query."""
    scope = Reading(
        who=Who(kind="player", players=("Brandin Podziemski",)),
        lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),),
        measure=Measure(key="points", as_typed="points"),
        span=Span(season=2026),
    )
    reading = default_point(GAMES_COUNTED, scope)
    assert isinstance(reading, Reading)
    assert (reading.relation, reading.shape, reading.aggregate, reading.predicates) == ("player", "scalar", "count", [("points", ">=", 30)])
    planned = plan(reading)
    assert isinstance(planned, Query)
    assert planned == planned_query(GAMES_COUNTED, scope)
    assert planned.filters() == scope.filters()
    assert planned.skeleton == "scalar" and planned.aggregate == "count"


def test_the_planner_copies_and_never_decides() -> None:
    """Every point field on the Reading lands on the Query under its own
    name (``shape`` and ``by`` -> ``skeleton``, ``on`` -> ``source``,
    ``relation`` -> ``subject``), and a field the Reading left unset is
    unset on the Query too."""
    reading = Reading(
        **(Reading(who=Who(kind="player", players=("X",)), span=Span(season=2025))).filters(),
        shape="ranking",
        by="player",
        measures=["points"],
        aggregate="per_game",
        group="player",
        predicates=[("won", "=", True)],
        order="measure",
        direction="asc",
        limit=7,
        minimum_games=20,
        relation="everyone",
    )
    # The position group a league-wide point honors is its subject's (Phase 3, step 2).
    reading = replace(reading, **(replace(reading, who=replace(reading.who, position="C"))).filters())
    q = plan(reading)
    assert isinstance(q, Query)
    assert q.filters() == reading.filters()
    assert (q.skeleton, q.measures, q.aggregate, q.group, q.predicates) == ("grouped", ["points"], "per_game", "player", [("won", "=", True)])
    assert (q.order, q.direction, q.limit, q.offset, q.minimum_games, q.subject, q.position, q.source) == ("measure", "asc", 7, 0, 20, "everyone", "C", "games")
    assert q.available is None and q.subject_span is None
    # The two records mirror each other on purpose: a point field added to
    # one and not the other is a decision the planner would be making alone.
    # The point's shape, by and on are the target's vocabulary, from which
    # the planner derives the compiler's skeleton and source (plan.skeleton_of).
    point = {"shape", "by", "on", "measures", "aggregate", "group", "predicates", "order", "direction", "limit", "minimum_games", "available", "subject_span"}
    assert point <= {f.name for f in fields(Reading)}
    # The Query's ``position`` is the point's subject's position group
    # (``Reading.who.position``, a field of the point until Phase 3, step 2).
    assert "position" not in {f.name for f in fields(Reading)} and "position" in {f.name for f in fields(Who)}
    # The Query's ``offset`` is the compiler's own (a pair's newest meetings): the point's was 0 on every reading and went with the window (Phase 3, step 2).
    assert {"skeleton", "measures", "aggregate", "group", "predicates", "order", "direction", "limit", "offset", "minimum_games", "available", "subject_span", "source", "position"} <= {
        f.name for f in fields(Query)
    }


def test_a_team_reading_plans_to_the_team_relation() -> None:
    reading = Reading(
        **(Reading(who=Who(kind="team", teams=("Orlando Magic",)), span=Span(season=2026))).filters(), shape="scalar", measures=["threePointFieldGoalsMade"], aggregate="total", relation="team"
    )
    q = plan(reading)
    assert isinstance(q, TeamQuery)
    assert (q.filters(), q.metric, q.aggregate) == (Reading(who=Who(kind="team", teams=("Orlando Magic",)), span=Span(season=2026)).filters(), "threePointFieldGoalsMade", "total")


def test_the_compilers_points_are_keyword_only() -> None:
    """``Query`` and ``TeamQuery`` are built by naming every field, as
    ``Reading`` is: a positional construction would bind a value to whatever
    field sits in that place, and the two records mirror the Reading's
    fields in a different order."""
    with pytest.raises(TypeError):
        Query(Reading(who=Who(kind="player", players=("Joel Embiid",))), "rows")  # type: ignore[call-arg, arg-type]
    with pytest.raises(TypeError):
        TeamQuery(Reading(who=Who(kind="team", teams=("Orlando Magic",))), "points")  # type: ignore[call-arg, arg-type]
    assert Query(**(Reading(who=Who(kind="player", players=("Joel Embiid",)))).filters(), skeleton="rows").who.player == "Joel Embiid"
    assert TeamQuery(**(Reading(who=Who(kind="team", teams=("Orlando Magic",)))).filters(), metric="points").who.team == "Orlando Magic"


def test_describe_names_every_deciding_field_and_drops_empty_scope() -> None:
    reading = Reading(
        **(Reading(who=Who(kind="player", players=("Joel Embiid",)), span=Span(season=2026))).filters(),
        shape="scalar",
        measures=[],
        aggregate="count",
        group="none",
        predicates=[("points", ">=", 30)],
        order="date",
        direction="desc",
        limit=None,
        asked=GAMES_COUNTED,
    )
    line = reading.describe()
    assert line.startswith("relation=player subject=? shape=scalar by= on=player_games measures=[] aggregate=count group=none predicates=[('points', '>=', 30)] window=date/desc")
    assert "filters={'who': {'kind': 'player', 'players': ['Joel Embiid']}, 'span': {'season': 2026}}" in line


def test_every_group_is_the_compilers_and_every_declared_cell_a_filters_cell() -> None:
    """Hand-kept lists against the ones they must agree with: the Group names
    against compose.core.GROUPS, and every cell the relation tables declare
    against the filters' cells. Until Phase 4, step 0 it held the tests' slot
    door to a check per field too (``tests/slots.py``'s ``_CHECKS``), with the
    typed families and the stated minimum exempt - the door went, and every
    family but the split checks its own values."""
    from typing import get_args

    from association.query.compose.core import GROUPS
    from association.query.player_relation import RELATION_SCOPING
    from association.query.reading import (
        Companion,
        Cuts,
        Filtered,
        Group,
        Line,
        Measure,
        Period,
    )
    from association.query.team_relation import TEAM_RELATION_SCOPING

    names = {f.name for f in fields(Filtered)}
    # `presence` is the team relation's own group (compose.team.compile_team_presence), not a key of the player relation's GROUPS.
    # `period` is the player relation's own, four reads of the same games rather than a GROUP BY (compose.core._compile_by_period).
    # `line` is keyed on the point's own predicate rather than a fixed column (compose.core._line_group).
    assert set(get_args(Group)) == {"none", "presence", "period", "line", *GROUPS}
    # Every cell is a typed value's, but the split's, whose value is a field of its own (Phase 3, step 2; the closing slice named it on Scope.CELLS).
    assert {"split"} == Filtered.CELLS - Span.CELLS - Window.CELLS - Cuts.CELLS - Period.CELLS - Line.CELLS - Companion.CELLS - Measure.CELLS and "split" in names
    assert RELATION_SCOPING <= Filtered.CELLS and TEAM_RELATION_SCOPING <= Filtered.CELLS
    assert Span.CELLS | Window.CELLS | (Cuts.CELLS - {"round"}) <= RELATION_SCOPING and "round" not in RELATION_SCOPING


def test_a_scope_reports_every_cell_it_sets_and_a_decline_says_each_by_what_was_asked() -> None:
    """``Filtered.cells`` is every cell a scope sets, each family's and the
    split's (Phase 3, step 2's closing slice), and ``cell_facts`` what the
    words asked under each, which a decline is said by
    (``say.decline_body``): the slot names each cell was declared under
    before Phase 3, step 2 ("cannot honor ['order']") until the Phase 3
    review's cleanup (b)3 (ISSUES.md #281)."""
    from association.query.compose.say import decline_body
    from association.query.reading import CELL_ORDER, cell_facts, unhonored_cells

    scope = Reading(
        who=Who(kind="player", players=("x",)),
        cuts=Cuts(tenure="MIA", round="finals"),
        companions=(Companion(player="z", predicate="started"), Companion(player="y", predicate="absent")),
        lines=(Line(measure="freeThrowsAttempted", op="<", value=14, as_typed="under 14 fta", narrows=True),),
        measure=Measure(key="points", as_typed="points", how="per_100", unit="per 100 possessions"),
        span=Span(both=True, since=2019, until=2021),
        split="starter_bench",
        period=Period(number=2, half=True),
        window=Window(order="recent", count=5, by="points"),
    )
    assert scope.cells() == {"range", "both", "window", "ranked_by", "tenure", "round", "period", "line", "companion", "rate", "split"}
    assert Reading().cells() == frozenset() and Reading(split="bench").cells() == {"split"}
    assert set(CELL_ORDER) >= scope.cells()
    assert unhonored_cells(scope, frozenset({"range", "companion", "split"})) == ["both", "window", "ranked_by", "tenure", "round", "period", "line", "rate"]
    said = decline_body({"cells": [cell_facts(scope, cell) for cell in unhonored_cells(scope, frozenset())], "why": "why"})
    assert said == (
        "the seasons 2018-19 through 2020-21, the regular season and the playoffs together, the last 5 games, games ranked by points, games for MIA, the finals, "
        "the 2nd half, games with under 14 fta, games z started and games without y, a starter and bench split and a rate per 100 possessions cannot be read here - why"
    )
