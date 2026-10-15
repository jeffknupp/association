"""The Reading (query/reading.py) and the planner (compose/plan.py): one record
of what a question asks, planned into the compiler's point without reading
the question again."""

from __future__ import annotations

from dataclasses import fields, replace
from typing import Any

import pytest
from routed import planned_query

from association.query.compose.core import Query, measure_names
from association.query.compose.plan import aggregate_of, plan
from association.query.compose.team import TeamQuery, team_metric
from association.query.point import default_point
from association.query.reading import GAMES_COUNTED, Aggregate, Companion, Cuts, DeclineReason, Line, Measure, Period, PointShape, Reading, Span, Unsupported, Window
from association.query.reading import Subject as Who


def test_the_default_point_is_a_reading_and_its_plan_is_the_planned_query() -> None:
    """A key's default point (``point.default_point``) is a Reading, and the
    planner plans it into the same Query the tests' ``planned_query`` builds
    for that key and scope, the scope's filters unchanged."""
    scope = Reading(
        who=Who(kind="player", players=("Brandin Podziemski",)),
        lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),),
        measure=Measure(key="points", as_typed="points"),
        span=Span(season=2026),
    )
    reading = default_point(GAMES_COUNTED, scope)
    assert isinstance(reading, Reading)
    assert (reading.relation, reading.read_as.shape, aggregate_of(reading), reading.predicates) == ("player", "scalar", "count", [("points", ">=", 30)])
    planned = plan(reading)
    assert isinstance(planned, Query)
    assert planned == planned_query(GAMES_COUNTED, scope)
    assert planned.filters() == scope.filters()
    assert planned.skeleton == "scalar" and planned.aggregate == "count"


def test_the_planner_copies_and_never_decides() -> None:
    """Every point field on the Reading lands on the Query under its own
    name (``read_as`` -> ``skeleton`` and ``source``,
    ``relation`` -> ``subject``), and a field the Reading left unset is
    unset on the Query too."""
    reading = Reading(
        **(Reading(who=Who(kind="player", players=("X",)), span=Span(season=2025))).filters(),
        read_as=PointShape("player_games", "ranking", "player"),
        measures=(Measure(key="points"),),
        group="player",
        predicates=[("points", ">=", 10)],
        order="measure",
        direction="asc",
        limit=7,
        minimum_games=20,
        relation="everyone",
    )
    # The position group a league-wide point honors is its subject's.
    reading = replace(reading, **(replace(reading, who=replace(reading.who, position="C"))).filters())
    q = plan(reading)
    assert isinstance(q, Query)
    assert q.filters() == reading.filters()
    assert (q.skeleton, q.measures, q.aggregate, q.group, q.predicates) == ("grouped", ["points"], "per_game", "player", [("points", ">=", 10)])
    assert (q.order, q.direction, q.limit, q.offset, q.minimum_games, q.subject, q.position, q.source) == ("measure", "asc", 7, 0, 20, "everyone", "C", "games")
    assert q.available is None and q.subject_span is None
    # The two records mirror each other on purpose: a point field added to
    # one and not the other is a decision the planner would be making alone.
    # What the point is read as (its shape, by and relation, one field) is
    # the target's vocabulary, from which the planner derives the compiler's
    # skeleton and source (plan.skeleton_of), and the aggregate with the
    # measure (plan.aggregate_of).
    assert "aggregate" not in {f.name for f in fields(Reading)}
    point = {"read_as", "measures", "group", "predicates", "order", "direction", "limit", "minimum_games", "available", "subject_span"}
    assert point <= {f.name for f in fields(Reading)}
    # The Query's ``position`` is the point's subject's position group
    # (``Reading.who.position``), no field of the point.
    assert "position" not in {f.name for f in fields(Reading)} and "position" in {f.name for f in fields(Who)}
    # The Query's ``offset`` is the compiler's own (a pair's newest meetings), no field of the point.
    assert {"skeleton", "measures", "aggregate", "group", "predicates", "order", "direction", "limit", "offset", "minimum_games", "available", "subject_span", "source", "position"} <= {
        f.name for f in fields(Query)
    }


def test_a_team_reading_plans_to_the_team_relation() -> None:
    reading = Reading(
        **(Reading(who=Who(kind="team", teams=("Orlando Magic",)), span=Span(season=2026))).filters(),
        read_as=PointShape("team_games", "scalar"),
        measures=(Measure(key="threePointFieldGoalsMade"),),
        relation="team",
    )
    q = plan(reading)
    assert isinstance(q, TeamQuery)
    assert (q.filters(), q.metric, q.aggregate) == (Reading(who=Who(kind="team", teams=("Orlando Magic",)), span=Span(season=2026)).filters(), "threePointFieldGoalsMade", "total")


def test_the_planner_names_the_points_typed_measures_once() -> None:
    """The point states its measures typed and the
    compilers' names are built where the planner hands it over, once:
    a rate by its derived name, a game won as the flag the player compiler
    reads, and the team compiler's own names for what a team gave up and its
    margin; a measure the player-games relation has no name for is refused,
    never dropped."""
    player = Reading(who=Who(kind="player", players=("X",)), span=Span(season=2025)).filters()
    reading = Reading(**player, read_as=PointShape("player_games", "scalar", "line"), measures=(Measure(key="fieldGoalPct"), Measure(key="points"), Measure(key="wins")))
    q = plan(reading)
    assert isinstance(q, Query) and q.measures == ["fg_pct", "points", "won"]
    assert measure_names(()) == []
    with pytest.raises(Unsupported):
        measure_names((Measure(key="netpoints"),))
    team = Reading(who=Who(kind="team", teams=("Orlando Magic",)), span=Span(season=2026)).filters()
    for measure, metric in (
        (Measure(key="points", whose="opponent"), "points_allowed"),
        (Measure(key="point_differential"), "differential"),
        (Measure(key="wins"), "won"),
        (Measure(key="record"), "record"),
    ):
        assert team_metric(measure) == metric
        q_team = plan(Reading(**team, read_as=PointShape("team_games", "scalar"), measures=(measure,), relation="team"))
        assert isinstance(q_team, TeamQuery) and q_team.metric == metric


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
        read_as=PointShape("player_games", "scalar"),
        measures=(),
        group="none",
        predicates=[("points", ">=", 30)],
        order="date",
        direction="desc",
        limit=None,
        asked=GAMES_COUNTED,
    )
    line = reading.describe()
    assert line.startswith("relation=player subject=? shape=scalar by= on=player_games measures=[] group=none predicates=[('points', '>=', 30)] window=date/desc")
    assert "filters={'who': {'kind': 'player', 'players': ['Joel Embiid']}, 'span': {'season': 2026}}" in line


def test_every_group_is_the_compilers_and_every_declared_cell_a_filters_cell() -> None:
    """Hand-kept lists against the ones they must agree with: the Group names
    against compose.core.GROUPS, and every cell the relation tables declare
    against the filters' cells; every family but the split checks its own
    values."""
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
    # Every cell is a typed value's, but the split's, whose value is a field of its own.
    assert {
        "split"
    } == Filtered.CELLS - Span.CELLS - Window.CELLS - Cuts.CELLS - Period.CELLS - Line.CELLS - Line.SEASON_CELLS - Line.OPPONENT_CELLS - Companion.CELLS - Measure.CELLS and "split" in names
    assert RELATION_SCOPING <= Filtered.CELLS and TEAM_RELATION_SCOPING <= Filtered.CELLS
    assert Span.CELLS | Window.CELLS | (Cuts.CELLS - {"round"}) <= RELATION_SCOPING and "round" not in RELATION_SCOPING


def test_a_scope_reports_every_cell_it_sets_and_a_decline_says_each_by_what_was_asked() -> None:
    """``Filtered.cells`` is every cell a scope sets, each family's and the
    split's, and ``cell_facts`` what the words asked under each, which a
    decline is said by (``say.decline_body``), never by a cell's name
    (#281)."""
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
    said = decline_body({"cells": [cell_facts(scope, cell) for cell in unhonored_cells(scope, frozenset())], "reason": DeclineReason.BEYOND_WORDS})
    assert said == (
        "the seasons 2018-19 through 2020-21, the regular season and the playoffs together, the last 5 games, games ranked by points, games for MIA, the finals, "
        "the 2nd half, games with under 14 fta, games z started and games without y, a starter and bench split and a rate per 100 possessions cannot be read here - "
        "it would answer for a different span than was asked"
    )


_TRIPLE = Measure(key="triple_double", as_typed="triple doubles")
_GAMES_PLAYED = Measure(key="games_played", as_typed="games played")
_POINTS_TOTAL = Measure(key="points", as_typed="points", how="total")


@pytest.mark.parametrize(
    ("read_as", "fields_", "aggregate"),
    [
        # Rows and charts reduce nothing; the declared relations' points carry none.
        (PointShape("player_games", "rows", "date"), {}, Aggregate.NONE),
        (PointShape("netpoints", "chart", "fingerprint"), {}, Aggregate.NONE),
        (PointShape("shots", "scalar", "distance"), {}, Aggregate.NONE),
        # A team's own season: the team compiler's sum where the season reader declines.
        (PointShape("team_seasons", "scalar", "line"), {}, Aggregate.TOTAL),
        (PointShape("team_snapshots", "scalar", "projection"), {}, Aggregate.TOTAL),
        # The season line is per game, a ranking of it and its count of players too.
        (PointShape("player_seasons", "ranking", "player"), {"measure": _TRIPLE}, Aggregate.PER_GAME),
        (PointShape("player_seasons", "scalar", "count"), {}, Aggregate.PER_GAME),
        (PointShape("player_periods", "split", "period"), {}, Aggregate.PER_GAME),
        (PointShape("player_periods", "ranking", "player"), {}, Aggregate.PER_GAME),
        # Runs: a team's wins counted, a player's predicate held along.
        (PointShape("team_games", "runs", "won"), {}, Aggregate.COUNT),
        (PointShape("player_games", "runs", "line"), {"predicates": [("won", "=", True)]}, Aggregate.NONE),
        # Comparisons.
        (PointShape("team_games", "comparison", "opponent"), {}, Aggregate.RECORD),
        (PointShape("player_games", "comparison", "subject"), {}, Aggregate.PER_GAME),
        (PointShape("player_games", "comparison", "met"), {}, Aggregate.NONE),
        # Splits: a line's record, or its count of a yes or a no.
        (PointShape("player_games", "split", "line"), {"predicates": [("points", ">=", 30)]}, Aggregate.RECORD),
        (PointShape("player_games", "split", "line"), {"measure": Measure(key="points"), "predicates": [("won", "=", True)]}, Aggregate.COUNT),
        (PointShape("player_games", "split", "line"), {"measure": Measure(key="wins")}, Aggregate.COUNT),
        (PointShape("player_games", "split", "splits"), {}, Aggregate.RECORD),
        (PointShape("team_games", "split", "presence"), {}, Aggregate.RECORD),
        (PointShape("team_games", "split", "season"), {}, Aggregate.PER_GAME),
        # Scalars: a line per game or its count of a yes or a no; the rest name their own.
        (PointShape("player_games", "scalar", "line"), {"measure": Measure(key="points")}, Aggregate.PER_GAME),
        (PointShape("player_games", "scalar", "line"), {"measure": _TRIPLE, "predicates": [("triple_double", "=", True)]}, Aggregate.COUNT),
        (PointShape("player_games", "scalar", "count"), {}, Aggregate.COUNT),
        (PointShape("team_games", "scalar", "record"), {}, Aggregate.RECORD),
        (PointShape("team_periods", "scalar", "total"), {}, Aggregate.TOTAL),
        (PointShape("team_games", "scalar", ""), {"measures": (Measure(key="points"),)}, Aggregate.TOTAL),
        (PointShape("player_games", "scalar", ""), {}, Aggregate.COUNT),
        # Rankings on the games: a count, a total asked, per game otherwise.
        (PointShape("player_games", "ranking", "count"), {"measures": (Measure(key="points"),)}, Aggregate.COUNT),
        (PointShape("player_games", "ranking", "player"), {"measure": _GAMES_PLAYED}, Aggregate.COUNT),
        (PointShape("player_games", "ranking", "player"), {"predicates": [("points", ">=", 30)]}, Aggregate.COUNT),
        (PointShape("player_games", "ranking", "player"), {"measure": _POINTS_TOTAL, "measures": (Measure(key="points"),)}, Aggregate.TOTAL),
        (PointShape("player_games", "ranking", "player"), {"measure": Measure(key="points"), "measures": (Measure(key="points"),)}, Aggregate.PER_GAME),
    ],
)
def test_the_planner_derives_the_aggregate_from_what_the_point_is_read_as_and_its_measure(read_as: PointShape, fields_: dict[str, Any], aggregate: Aggregate) -> None:
    """The aggregate is the planner's (decision D5): derived from the
    point's declared shape and its measure - the catalog's yes-or-no facet,
    a yes or a no the point keeps its games by, how the measure is asked -
    one rule per row of ``compose.plan.aggregate_of``'s table."""
    point = Reading(read_as=read_as, **fields_)
    assert aggregate_of(point) == aggregate


def test_the_yes_or_no_measures_are_the_catalogs() -> None:
    """The one facet the aggregate reads beside the shape, on the catalog
    (``MeasureSpec.yes_no``), and the compiler's boolean measures read from
    it: a condition a game holds or not."""
    from association.query.measure import CATALOG, yes_no
    from association.query.measures import BOOLEAN_MEASURES

    assert {key for key, spec in CATALOG.items() if spec.yes_no} == {"double_double", "triple_double", "fouled_out", "wins", "games_played"}
    assert {"double_double", "triple_double", "fouled_out", "won"} == BOOLEAN_MEASURES
    assert yes_no(Measure(key="wins")) and yes_no("won") and not yes_no(Measure(key="points")) and not yes_no(None)
