"""The Reading (query/reading.py) and the planner (compose/plan.py): one record
of what a question asks, planned into the compiler's point without reading
the question again - ROADMAP plan item 6, step (a)."""

from __future__ import annotations

from dataclasses import fields
from typing import Any

import pytest

from association.query.compose.adapt import to_query, to_reading
from association.query.compose.core import Query
from association.query.compose.plan import plan
from association.query.compose.team import TeamQuery
from association.query.reading import Reading, Scope


def test_the_default_point_is_a_reading_and_its_plan_is_the_query_it_always_was() -> None:
    """The adapters build a Reading; ``to_query`` is that Reading planned, so
    every caller of the old contract sees the same Query."""
    slots: dict[str, Any] = {"player": "Brandin Podziemski", "stat": "points", "threshold": 30, "season": 2026}
    reading = to_reading("threshold_count", slots)
    assert isinstance(reading, Reading)
    assert (reading.relation, reading.shape, reading.aggregate, reading.predicates) == ("player", "scalar", "count", [("points", ">=", 30)])
    planned = plan(reading)
    assert isinstance(planned, Query)
    assert planned == to_query("threshold_count", slots)
    assert planned.scope == Scope.from_slots(slots) and planned.scope.to_slots() == slots
    assert planned.skeleton == "scalar" and planned.aggregate == "count"


def test_the_planner_copies_and_never_decides() -> None:
    """Every point field on the Reading lands on the Query under its own
    name (``shape`` -> ``skeleton``, ``relation`` -> ``subject``), and a
    field the Reading left unset is unset on the Query too."""
    reading = Reading(
        scope=Scope.from_slots({"player": "X", "season": 2025}),
        shape="grouped",
        measures=["points"],
        aggregate="per_game",
        group="player",
        predicates=[("won", "=", True)],
        order="measure",
        direction="asc",
        limit=7,
        offset=2,
        minimum_games=20,
        relation="everyone",
        position="C",
        source="games",
    )
    q = plan(reading)
    assert isinstance(q, Query)
    assert q.scope == reading.scope
    assert (q.skeleton, q.measures, q.aggregate, q.group, q.predicates) == ("grouped", ["points"], "per_game", "player", [("won", "=", True)])
    assert (q.order, q.direction, q.limit, q.offset, q.minimum_games, q.subject, q.position, q.source) == ("measure", "asc", 7, 2, 20, "everyone", "C", "games")
    assert q.available is None and q.span is None and q.season is None
    # The two records mirror each other on purpose: a point field added to
    # one and not the other is a decision the planner would be making alone.
    point = {"shape", "measures", "aggregate", "group", "predicates", "order", "direction", "limit", "offset", "minimum_games", "available", "span", "season", "source", "position"}
    assert point <= {f.name for f in fields(Reading)}
    assert {"skeleton", "measures", "aggregate", "group", "predicates", "order", "direction", "limit", "offset", "minimum_games", "available", "span", "season", "source", "position"} <= {
        f.name for f in fields(Query)
    }


def test_a_team_reading_plans_to_the_team_relation() -> None:
    reading = Reading(scope=Scope.from_slots({"team": "Orlando Magic", "season": 2026}), shape="scalar", measures=["threePointFieldGoalsMade"], aggregate="total", relation="team")
    q = plan(reading)
    assert isinstance(q, TeamQuery)
    assert (q.scope, q.measure, q.aggregate) == (Scope(team="Orlando Magic", season=2026), "threePointFieldGoalsMade", "total")


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
    assert line.startswith("relation=player subject=? shape=scalar measures=[] aggregate=count group=none predicates=[('points', '>=', 30)] window=date/desc source=games")
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
    assert (scope.player, scope.without, scope.venue, scope.limit, scope.season_type_unstated) == ("Joel Embiid", ("Tyrese Maxey",), "home", 10, True)
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
    from association.query.reading import _CHECKS, Group
    from association.query.templates.common import RELATION_SCOPING, SCOPING_SLOTS

    names = {f.name for f in fields(Scope)}
    assert set(_CHECKS) == names
    assert set(get_args(Group)) == {"none", *GROUPS}
    assert names >= (SCOPING_SLOTS | RELATION_SCOPING)
