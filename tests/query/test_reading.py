"""The Reading (query/reading.py) and the planner (compose/plan.py): one record
of what a question asks, planned into the compiler's point without reading
the question again - ROADMAP plan item 6, step (a)."""

from __future__ import annotations

from dataclasses import fields
from typing import Any

import pytest
from routed import default_query, default_reading

from association.query.compose.core import Query
from association.query.compose.plan import plan
from association.query.compose.team import TeamQuery
from association.query.reading import Reading, Scope, Span, Window


def test_the_default_point_is_a_reading_and_its_plan_is_the_query_it_always_was() -> None:
    """The adapters build a Reading; ``to_query`` is that Reading planned, so
    every caller of the old contract sees the same Query."""
    slots: dict[str, Any] = {"player": "Brandin Podziemski", "stat": "points", "threshold": 30, "season": 2026}
    reading = default_reading("threshold_count", slots)
    assert isinstance(reading, Reading)
    assert (reading.relation, reading.shape, reading.aggregate, reading.predicates) == ("player", "scalar", "count", [("points", ">=", 30)])
    planned = plan(reading)
    assert isinstance(planned, Query)
    assert planned == default_query("threshold_count", slots)
    assert planned.scope == Scope.from_slots(slots) and planned.scope.to_slots() == slots
    assert planned.skeleton == "scalar" and planned.aggregate == "count"


def test_the_planner_copies_and_never_decides() -> None:
    """Every point field on the Reading lands on the Query under its own
    name (``shape`` and ``by`` -> ``skeleton``, ``on`` -> ``source``,
    ``relation`` -> ``subject``), and a field the Reading left unset is
    unset on the Query too."""
    reading = Reading(
        scope=Scope.from_slots({"player": "X", "season": 2025}),
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
        position="C",
    )
    q = plan(reading)
    assert isinstance(q, Query)
    assert q.scope == reading.scope
    assert (q.skeleton, q.measures, q.aggregate, q.group, q.predicates) == ("grouped", ["points"], "per_game", "player", [("won", "=", True)])
    assert (q.order, q.direction, q.limit, q.offset, q.minimum_games, q.subject, q.position, q.source) == ("measure", "asc", 7, 0, 20, "everyone", "C", "games")
    assert q.available is None and q.subject_span is None
    # The two records mirror each other on purpose: a point field added to
    # one and not the other is a decision the planner would be making alone.
    # The point's shape, by and on are the target's vocabulary, from which
    # the planner derives the compiler's skeleton and source (plan.skeleton_of).
    point = {"shape", "by", "on", "measures", "aggregate", "group", "predicates", "order", "direction", "limit", "minimum_games", "available", "subject_span", "position"}
    assert point <= {f.name for f in fields(Reading)}
    # The Query's ``offset`` is the compiler's own (a pair's newest meetings): the point's was 0 on every reading and went with the window (Phase 3, step 2).
    assert {"skeleton", "measures", "aggregate", "group", "predicates", "order", "direction", "limit", "offset", "minimum_games", "available", "subject_span", "source", "position"} <= {
        f.name for f in fields(Query)
    }


def test_a_team_reading_plans_to_the_team_relation() -> None:
    reading = Reading(scope=Scope.from_slots({"team": "Orlando Magic", "season": 2026}), shape="scalar", measures=["threePointFieldGoalsMade"], aggregate="total", relation="team")
    q = plan(reading)
    assert isinstance(q, TeamQuery)
    assert (q.scope, q.measure, q.aggregate) == (Scope(team="Orlando Magic", span=Span(season=2026)), "threePointFieldGoalsMade", "total")


def test_the_compilers_points_are_keyword_only() -> None:
    """``Query`` and ``TeamQuery`` are built by naming every field, as
    ``Reading`` is: a positional construction would bind a value to whatever
    field sits in that place, and the two records mirror the Reading's
    fields in a different order."""
    with pytest.raises(TypeError):
        Query(Scope(player="Joel Embiid"), "rows")  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        TeamQuery(Scope(team="Orlando Magic"), "points")  # type: ignore[call-arg]
    assert Query(scope=Scope(player="Joel Embiid"), skeleton="rows").scope.player == "Joel Embiid"
    assert TeamQuery(scope=Scope(team="Orlando Magic"), measure="points").scope.team == "Orlando Magic"


def test_describe_names_every_deciding_field_and_drops_empty_scope() -> None:
    reading = Reading(
        scope=Scope.from_slots({"player": "Joel Embiid", "season": 2026, "opponent": None, "without": []}),
        shape="scalar",
        measures=[],
        aggregate="count",
        group="none",
        predicates=[("points", ">=", 30)],
        order="date",
        direction="desc",
        limit=None,
        intent="threshold_count",
    )
    line = reading.describe()
    assert line.startswith("relation=player subject=? shape=scalar by= on=player_games measures=[] aggregate=count group=none predicates=[('points', '>=', 30)] window=date/desc")
    assert "scope={'player': 'Joel Embiid', 'season': 2026}" in line


def test_a_slot_dict_round_trips_through_the_scope() -> None:
    """``Scope.from_slots`` types a slot dict and ``to_slots`` gives it back,
    empty slots (None, "", [], False) read as absent - the reading
    ``check_scope`` already takes of a falsy slot."""
    slots: dict[str, Any] = {
        "player": "Joel Embiid",
        "opponent": "Boston Celtics",
        "season": 2026,
        "season_type": 2,
        "without": ["Tyrese Maxey"],
        "venue": "home",
        "order": "recent",
        "limit": 10,
        "season_type_unstated": True,
    }
    scope = Scope.from_slots({**slots, "team": None, "players": [], "stat": "", "per_game": False})
    assert (scope.player, scope.without, scope.cuts.venue, scope.window.count, scope.span.both) == ("Joel Embiid", ("Tyrese Maxey",), "home", 10, True)
    assert scope.to_slots() == slots


@pytest.mark.parametrize(
    ("slots", "match"),
    [
        ({"sesaon": 2026}, "no scope field"),
        ({"season": "2026"}, "whole number"),
        ({"season": True}, "whole number"),
        ({"players": ["Joel Embiid", 7]}, "list of text"),
        ({"venue": "neutral"}, "one of"),
        ({"season_type": 1}, "one of"),
        ({"limit": 0}, "below 1"),
        ({"span": "decade"}, "one of"),
        ({"date": "last night"}, "calendar day"),
        ({"date": "2026-02-30"}, "calendar day"),
        ({"conditions": [{"player": "Jayson Tatum", "predicate": "dunked"}]}, "predicate"),
        ({"conditions": [{"side": "own"}]}, "names no player"),
    ],
)
def test_a_slot_nothing_types_is_refused_out_loud(slots: dict[str, Any], match: str) -> None:
    """A slot the scope could only drop is a narrowing the answer would
    silently leave out - this project's worst failure shape - so a key or a
    value nothing here types raises instead."""
    with pytest.raises(ValueError, match=match):
        Scope.from_slots(slots)


def test_every_scope_field_is_checked_and_every_group_is_the_compilers() -> None:
    """Two hand-kept lists against the ones they must agree with: a check per
    Scope field (a field without one would take any value), the Group names
    against compose.core.GROUPS, and every scoping slot the templates declare
    against the Scope's fields."""
    from typing import get_args

    from association.query.compose.core import GROUPS
    from association.query.player_relation import RELATION_SCOPING
    from association.query.reading import _CHECKS, _CUT_SLOT_KEYS, _SPAN_SLOT_NAMES, _WINDOW_SLOT_NAMES, SCOPING_SLOTS, Cuts, Group

    names = {f.name for f in fields(Scope)}
    # The span's six slot names, the window's four and the cuts' eight pass the door into the typed values (Phase 3, step 2).
    assert set(_CHECKS) == (names - {"span", "window", "cuts"}) | _SPAN_SLOT_NAMES | _WINDOW_SLOT_NAMES | _CUT_SLOT_KEYS
    # `presence` is the team relation's own group (compose.team.compile_team_presence), not a key of the player relation's GROUPS.
    # `period` is the player relation's own, four reads of the same games rather than a GROUP BY (compose.core._compile_by_period).
    # `line` is keyed on the point's own predicate rather than a fixed column (compose.core._line_group).
    assert set(get_args(Group)) == {"none", "presence", "period", "line", *GROUPS}
    # The span's three cells, the window's two and the cuts' seven are the typed values', not fields of their own (Phase 3, step 2).
    assert names >= SCOPING_SLOTS and names >= RELATION_SCOPING - Span.CELLS - Window.CELLS - Cuts.CELLS
    assert Span.CELLS | Window.CELLS | (Cuts.CELLS - {"round"}) <= RELATION_SCOPING and "round" not in RELATION_SCOPING


def test_a_period_condition_round_trips_through_the_slot_door() -> None:
    """A quarter or half as a condition on which games count (ROADMAP step 2,
    #275) is one typed record: a stat, a threshold, the comparison ("at
    least" by default, "exactly" for a bare number) and exactly one of a
    period or a half. A slot dict in that shape reads and writes back the
    same; one naming both a period and a half, or neither, or a key nothing
    types, is refused at the door."""
    from association.query.reading import PeriodCondition, ScopeError

    scope = Scope.from_slots({"period_condition": {"stat": "threePointFieldGoalsMade", "threshold": 1, "period": 1}})
    assert scope.period_condition == PeriodCondition(stat="threePointFieldGoalsMade", threshold=1, op=">=", period=1)
    assert scope.to_slots()["period_condition"] == {"stat": "threePointFieldGoalsMade", "threshold": 1, "op": ">=", "period": 1}
    exact = Scope.from_slots({"period_condition": {"stat": "points", "threshold": 10, "op": "=", "half": 1}})
    assert exact.period_condition == PeriodCondition(stat="points", threshold=10, op="=", half=1)
    assert Scope.from_slots(exact.to_slots()) == exact
    for bad in (
        {"stat": "points", "threshold": 1},
        {"stat": "points", "threshold": 1, "period": 1, "half": 1},
        {"stat": "points", "threshold": 1, "period": 1, "op": "<"},
        {"stat": "points", "period": 1},
    ):
        with pytest.raises(ScopeError):
            Scope.from_slots({"period_condition": bad})
