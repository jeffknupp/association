"""The Reading (query/reading.py) and the planner (compose/plan.py): one record
of what a question asks, planned into the compiler's point without reading
the question again - ROADMAP plan item 6, step (a)."""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from association.query.compose.adapt import to_query, to_reading
from association.query.compose.core import Query
from association.query.compose.plan import plan
from association.query.compose.team import TeamQuery
from association.query.reading import Reading


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
    assert planned.slots == slots and planned.skeleton == "scalar" and planned.aggregate == "count"


def test_the_planner_copies_and_never_decides() -> None:
    """Every point field on the Reading lands on the Query under its own
    name (``shape`` -> ``skeleton``, ``relation`` -> ``subject``), and a
    field the Reading left unset is unset on the Query too."""
    reading = Reading({"player": "X", "season": 2025}, "grouped", ["points"], "per_game", "player", [("won", "=", True)], "measure", "asc", 7, 2, 20, relation="everyone", position="C", source="games")
    q = plan(reading)
    assert isinstance(q, Query)
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
    reading = Reading({"team": "Orlando Magic", "season": 2026}, "scalar", ["threePointFieldGoalsMade"], "total", relation="team")
    q = plan(reading)
    assert isinstance(q, TeamQuery)
    assert (q.slots, q.measure, q.aggregate) == ({"team": "Orlando Magic", "season": 2026}, "threePointFieldGoalsMade", "total")


def test_describe_names_every_deciding_field_and_drops_empty_scope() -> None:
    reading = Reading({"player": "Joel Embiid", "season": 2026, "opponent": None, "without": []}, "scalar", [], "count", "none", [("points", ">=", 30)], "date", "desc", None, intent="threshold_count")
    line = reading.describe()
    assert line.startswith("relation=player subject=? shape=scalar measures=[] aggregate=count group=none predicates=[('points', '>=', 30)] window=date/desc source=games")
    assert "scope={'player': 'Joel Embiid', 'season': 2026}" in line
