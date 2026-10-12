"""The Reading (query/reading.py) and the planner (compose/plan.py): one record
of what a question asks, planned into the compiler's point without reading
the question again - ROADMAP plan item 6, step (a)."""

from __future__ import annotations

from dataclasses import fields, replace
from typing import Any

import pytest
from routed import default_query, default_reading
from slots import slots_scope

from association.query.compose.core import Query
from association.query.compose.plan import plan
from association.query.compose.team import TeamQuery
from association.query.reading import GAMES_COUNTED, Reading, Span, Window
from association.query.reading import Subject as Who


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
    assert planned.filters() == slots_scope(slots).filters()
    assert planned.skeleton == "scalar" and planned.aggregate == "count"


def test_the_planner_copies_and_never_decides() -> None:
    """Every point field on the Reading lands on the Query under its own
    name (``shape`` and ``by`` -> ``skeleton``, ``on`` -> ``source``,
    ``relation`` -> ``subject``), and a field the Reading left unset is
    unset on the Query too."""
    reading = Reading(
        **(slots_scope({"player": "X", "season": 2025})).filters(),
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
    reading = Reading(**(slots_scope({"team": "Orlando Magic", "season": 2026})).filters(), shape="scalar", measures=["threePointFieldGoalsMade"], aggregate="total", relation="team")
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
        **(slots_scope({"player": "Joel Embiid", "season": 2026, "opponent": None, "without": []})).filters(),
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


def test_a_slot_dict_types_through_the_door() -> None:
    """The tests' slot door (``tests/slots.py``, ``Scope.from_slots`` until
    Phase 3, step 4) types a slot dict, empty slots (None, "", [], False)
    read as absent - the reading ``check_scope`` already took of a falsy
    slot. Nothing projects back: the slot-era ``to_slots`` went with slice (c)."""
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
    scope = slots_scope({**slots, "team": None, "players": [], "stat": "", "per_game": False})
    assert (scope.who.player, [c.player for c in scope.companions if c.absent], scope.cuts.venue, scope.window.count, scope.span.both) == ("Joel Embiid", ["Tyrese Maxey"], "home", 10, True)
    assert (scope.cuts.opponent, scope.span.season, scope.span.season_type, scope.window.order) == ("Boston Celtics", 2026, 2, "recent")


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
        slots_scope(slots)


def test_every_scope_field_is_checked_and_every_group_is_the_compilers() -> None:
    """Two hand-kept lists against the ones they must agree with: a check per
    Scope field (a field without one would take any value), the Group names
    against compose.core.GROUPS, and every scoping slot the templates declare
    against the Scope's fields."""
    from typing import get_args

    from slots import _CHECKS, _COMPANION_SLOT_NAMES, _CUT_SLOT_KEYS, _LINE_SLOT_NAMES, _MEASURE_SLOT_NAMES, _PERIOD_SLOT_NAMES, _SPAN_SLOT_NAMES, _SUBJECT_SLOT_NAMES, _WINDOW_SLOT_NAMES

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
    # The subject's four slot names, the span's six, the window's four, the cuts' eight, the period's two, the lines' four and the
    # companions' three pass the door into the typed values (Phase 3, step 2). A stated minimum was born typed (ISSUES.md #377):
    # no slot ever carried it, and the door takes none.
    assert (
        set(_CHECKS)
        == (names - {"who", "span", "window", "cuts", "period", "lines", "companions", "measure", "minimum"})
        | set(_SUBJECT_SLOT_NAMES)
        | _MEASURE_SLOT_NAMES
        | _SPAN_SLOT_NAMES
        | _WINDOW_SLOT_NAMES
        | _CUT_SLOT_KEYS
        | _PERIOD_SLOT_NAMES
        | _LINE_SLOT_NAMES
        | _COMPANION_SLOT_NAMES
    )
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

    scope = slots_scope(
        {
            "player": "x",
            "since": 2019,
            "until": 2021,
            "season_type_unstated": True,
            "order": "recent",
            "limit": 5,
            "ranked_by": "points",
            "own_team": "MIA",
            "round": "finals",
            "half": 2,
            "below": ["under 14 fta"],
            "without": ["y"],
            "conditions": [{"player": "z", "predicate": "started"}],
            "stat": "points",
            "rate": "per 100 possessions",
            "split": "starter_bench",
        }
    )
    assert scope.cells() == {"range", "both", "window", "ranked_by", "tenure", "round", "period", "line", "companion", "rate", "split"}
    assert Reading().cells() == frozenset() and slots_scope({"split": "bench"}).cells() == {"split"}
    assert set(CELL_ORDER) >= scope.cells()
    assert unhonored_cells(scope, frozenset({"range", "companion", "split"})) == ["both", "window", "ranked_by", "tenure", "round", "period", "line", "rate"]
    said = decline_body({"cells": [cell_facts(scope, cell) for cell in unhonored_cells(scope, frozenset())], "why": "why"})
    assert said == (
        "the seasons 2018-19 through 2020-21, the regular season and the playoffs together, the last 5 games, games ranked by points, games for MIA, the finals, "
        "the 2nd half, games with under 14 fta, games z started and games without y, a starter and bench split and a rate per 100 possessions cannot be read here - why"
    )


def test_a_period_condition_round_trips_through_the_slot_door() -> None:
    """A quarter or half as a condition on which games count (ROADMAP step 2,
    #275) is one typed line in a period (Phase 3, step 2): a stat, a
    number, the comparison ("at least" by default, "exactly" for a bare
    number) and exactly one of a period or a half. A slot dict in that shape
    reads and writes back the same; one naming both a period and a half, or
    neither, or a key nothing types, is refused at the door."""
    from association.query.reading import Line, Period, ScopeError

    scope = slots_scope({"period_condition": {"stat": "threePointFieldGoalsMade", "threshold": 1, "period": 1}})
    assert scope.lines == (Line(measure="threePointFieldGoalsMade", op=">=", value=1, period=Period(number=1)),)
    exact = slots_scope({"period_condition": {"stat": "points", "threshold": 10, "op": "=", "half": 1}})
    assert exact.lines == (Line(measure="points", op="=", value=10, period=Period(number=1, half=True)),)
    for bad in (
        {"stat": "points", "threshold": 1},
        {"stat": "points", "threshold": 1, "period": 1, "half": 1},
        {"stat": "points", "threshold": 1, "period": 1, "op": "<"},
        {"stat": "points", "period": 1},
    ):
        with pytest.raises(ScopeError):
            slots_scope({"period_condition": bad})
