"""The period, typed (Phase 3, step 2's fourth slice): the one tagger that
reads what a read SEES of each game (:func:`association.query.period.read_period`,
over :func:`~association.query.period.which_period`), the characters it
claims, the typed value's door and projection
(:class:`association.query.reading.Period`), the relations' one resolution
of it, the cell the tables declare, and the behavioral check contract 4
asks for: applying the cell on each relation changes what it reads.
"""

from __future__ import annotations

from typing import Any

import duckdb
import pytest
from routed import staged as settle
from shapes import asked, stated, unhonored
from test_period_relation import SEASON, con, team_con  # noqa: F401 - the two relation fixtures, imported by name

from association.query.compose.plan import cells_stated
from association.query.line import read_period_line
from association.query.period import PERIOD_ASKS, PeriodContext, PeriodRead, read_period, which_period
from association.query.player_games import Narrowed, rows_sql
from association.query.player_relation import RELATION_SCOPING, RELATION_SCOPING_EXCLUDED, apply_period
from association.query.reading import Claim, Line, Period, PointShape, Scope, ScopeError, cell_set, period_narrowing, unhonored_cells
from association.query.team_games import TeamNarrowed
from association.query.team_games import rows_sql as team_rows_sql
from association.query.team_relation import TEAM_RELATION_SCOPING, TEAM_RELATION_SCOPING_EXCLUDED, _team_games_apply_period


def _read(question: str, intent: str = "period_split") -> Period | None:
    return read_period(question, PeriodContext(asked=asked(intent))).period


# ---------------- the tagger: words to a Period ----------------


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("rj barrett 4th qtr log", Period(number=4)),
        ("kd q4 points last game", Period(number=4)),
        ("harrison barnes 1st q stats", Period(number=1)),
        ("Duncan Robison 1q log", Period(number=1)),
        ("first-quarter rebounds for jokic", Period(number=1)),
        ("jokic third quarter points", Period(number=3)),
        ("tatum first half stats", Period(number=1, half=True)),
        ("Ivey 2h pts log", Period(number=2, half=True)),
        ("Brunson 1st half log vs Celtics 23-24 season", Period(number=1, half=True)),
        # "by quarter" is the breakdown, the point's shape: no one period.
        ("jokic points by quarter", None),
        ("nba players points by quarter average", None),
        ("jokic stats", None),
        # A ranking by a half-and-a-quarter wording reads the half: a half is never a quarter.
        ("most 1st quarter points in the second half of the season", Period(number=2, half=True)),
    ],
)
def test_the_words_read_as_one_period(question: str, expected: Period | None) -> None:
    assert _read(question) == expected


def test_the_value_is_kept_under_a_reader_that_takes_one_and_dropped_elsewhere() -> None:
    """Measured on the 2,710 readings: the stages wrote the slot under the
    three period intents alone, and six readings naming a quarter carry
    none (their subject unread, the point reader declining them). The tagger
    keeps that, and claims nothing where it writes nothing."""
    assert {asked(name) for name in ("period_split", "period_leaderboard", "team_quarter_points")} == PERIOD_ASKS
    for shape in PERIOD_ASKS:
        assert read_period("Damian Lillard first quarter game log", PeriodContext(asked=shape)) == PeriodRead(Period(number=1), (Claim(15, 28, "period"),))
    assert read_period("Damian L first quarter game log", PeriodContext(asked=asked("other"))) == PeriodRead(None, ())
    assert read_period("jokic game log", PeriodContext(asked=asked("period_split"))) == PeriodRead(None, ())


def test_which_period_is_the_one_reader_the_condition_reader_and_the_stage_share() -> None:
    """No second copy of "first | second | 1st | 2nd": a period as a condition
    on which games count reads its period through the same function."""
    assert which_period("first quarter") == (Period(number=1), Claim(0, 13, "period"))
    assert which_period("2h") == (Period(number=2, half=True), Claim(0, 2, "period"))
    assert which_period("by quarter") is None
    condition = read_period_line("vj edgecombe three points made per game after making one three in first quarter")
    assert condition is not None and condition[0] == Line(measure="threePointFieldGoalsMade", op="=", value=1, period=Period(number=1), as_typed="after making one three in first quarter")
    half = read_period_line("lebron points in games where he scored 10+ points in the first half")
    assert half is not None and half[0] == Line(measure="points", op=">=", value=10, period=Period(number=1, half=True), as_typed="in games where he scored 10+ points in the first half")


def test_the_tagger_claims_the_characters_it_read_once_and_they_ride_the_route() -> None:
    route = settle("period_split", {"player": "Tyrese Maxey"}, "tyrese maxey first half games this season")
    assert route.scope.period == Period(number=1, half=True)
    assert [c for c in route.claims if c.what == "period"] == [Claim(13, 23, "period")]
    assert "tyrese maxey first half games this season"[13:23] == "first half"
    # The window's "last game" and the period's "second half" are two claims, neither inside the other.
    route = settle("period_split", {"player": "Miles Bridges"}, "miles bridges stats vs raptors second half last game")
    assert route.scope.period == Period(number=2, half=True)
    assert {c.what for c in route.claims} >= {"period", "window"}


def test_a_model_era_period_slot_is_dropped_at_the_stages_door_and_the_words_read() -> None:
    """A route's ``period``/``half`` keys (a test's payload; a settled route
    run again) are the words' to read: "Celtics 2nd half scoring" with the
    model's ``period: 2`` is the second HALF, not the second quarter."""
    route = settle("team_quarter_points", {"team": "Boston Celtics", "period": 2}, "Celtics 2nd half scoring this season")
    assert route.scope.period == Period(number=2, half=True)
    route = settle("team_quarter_points", {"team": "Boston Celtics", "period": 2}, "Celtics scoring this season")
    assert route.scope.period is None


# ---------------- the typed value: door, projection, cells ----------------


def test_the_value_is_one_quarter_or_one_half_and_says_so_when_it_is_neither() -> None:
    assert Period(number=5).narrowing() == ((5,), "overtime")
    assert Period(number=6).narrowing() == ((6,), "2nd overtime")
    assert Period(number=1, half=True).narrowing() == ((1, 2), "1st half")
    assert Period(number=0).narrowing() is None and Period(number=11).narrowing() is None
    with pytest.raises(ScopeError):
        Period(number=3, half=True)
    with pytest.raises(ScopeError):
        Period(number="1", half=False)  # type: ignore[arg-type]


@pytest.mark.parametrize("slots", [{"period": 1}, {"period": 4}, {"half": 1}, {"half": 2}, {"period": 5}])
def test_the_two_slots_round_trip_through_the_period(slots: dict[str, Any]) -> None:
    scope = Scope.from_slots(slots)
    assert scope.period is not None and scope.period.to_slots() == slots
    assert scope.to_slots() == slots
    assert Scope.from_slots({"period": scope.period}) == scope


def test_the_projection_keeps_the_slot_era_shape_in_its_place() -> None:
    projected = Scope.from_slots({"half": 2, "split": "starter"}).projected()
    keys = list(projected)
    assert keys.index("split") < keys.index("period") < keys.index("half") < keys.index("period_condition") < keys.index("venue")
    assert (projected["period"], projected["half"]) == (None, 2)
    assert Scope.from_slots({"period": 3}).projected()["period"] == 3
    assert Scope().projected()["period"] is None and Scope().projected()["half"] is None


def test_a_typed_period_and_a_slot_at_once_or_both_slots_or_a_bad_value_is_refused_at_the_door() -> None:
    with pytest.raises(ScopeError):
        Scope.from_slots({"period": Period(number=1), "half": 2})
    with pytest.raises(ScopeError):
        Scope.from_slots({"period": 1, "half": 1})
    with pytest.raises(ScopeError):
        Scope.from_slots({"half": 3})
    with pytest.raises(ScopeError):
        Scope.from_slots({"period": "first"})


def test_the_cell_its_decline_name_and_what_a_reader_leaves_unhonored() -> None:
    assert {"period"} == Period.CELLS and Period(number=2).cells() == {"period"}
    assert Period(number=2).unhonored(frozenset()) == ["period"] and Period(number=2, half=True).unhonored(frozenset()) == ["half"]
    assert Period(number=2, half=True).unhonored(frozenset({"period"})) == []
    assert cell_set(Scope.from_slots({"half": 1}), "period") and not cell_set(Scope(), "period")
    assert unhonored_cells(Scope.from_slots({"half": 1}), frozenset()) == ["half"]
    assert unhonored_cells(Scope.from_slots({"period": 4}), frozenset()) == ["period"]
    assert unhonored_cells(Scope.from_slots({"period": 4}), frozenset({"period"})) == []
    # A reader whose words never said a quarter steps aside for one, under the slot's name.
    assert unhonored("game_log", Scope.from_slots({"player": "x", "half": 2})) == ["half"]
    assert unhonored("period_split", Scope.from_slots({"player": "x", "half": 2})) == []


def test_the_relation_tables_declare_the_period_once() -> None:
    assert Period.CELLS <= RELATION_SCOPING and Period.CELLS <= TEAM_RELATION_SCOPING
    for table in (RELATION_SCOPING_EXCLUDED, TEAM_RELATION_SCOPING_EXCLUDED):
        for shape, row in table.items():
            assert "half" not in row.unstated, shape
            if "period" in row.unstated:
                assert row.unstated["period"].strip(), shape
    assert stated("period_leaderboard") & Period.CELLS == {"period"} and stated("game_log") & Period.CELLS == set()
    assert cells_stated(PointShape("player_periods", "ranking", "player")) & Period.CELLS == {"period"}
    assert cells_stated(PointShape("team_periods", "scalar", "total")) & Period.CELLS == {"period"}
    from association.query import compose

    for shape in compose._ROUTES:
        assert "half" not in cells_stated(shape), shape


# ---------------- contract 4: applying the cell changes what each relation reads ----------------


def _player_points(con: duckdb.DuckDBPyConnection, scope: Scope) -> list[tuple[Any, ...]]:  # noqa: F811 - the fixture
    narrowed = Narrowed(base=["pgl.athlete_id = ?", "pgl.season = ?"], base_params=["1", SEASON])
    apply_period(con, narrowed, scope)
    sql, params = rows_sql(narrowed, "pgl.event_id, pgl.points", order="g.date")
    return con.execute(sql, params).fetchall()


def test_the_period_cell_changes_what_the_player_relation_reads(con: duckdb.DuckDBPyConnection) -> None:  # noqa: F811 - the fixture
    assert _player_points(con, Scope()) == [("g1", 40), ("g2", 40)], "the whole game's line"
    assert _player_points(con, Scope(period=Period(number=1))) == [("g1", 4), ("g2", 0)]
    assert _player_points(con, Scope(period=Period(number=1, half=True))) != _player_points(con, Scope(period=Period(number=1)))
    assert period_narrowing(Scope(period=Period(number=1, half=True))) == ((1, 2), "1st half")


def _team_score(team_con: duckdb.DuckDBPyConnection, scope: Scope) -> list[tuple[Any, ...]]:  # noqa: F811 - the fixture
    narrowed = TeamNarrowed(base=["tg.team_id = ?", "tg.season_type = ?", "tg.season = ?"], base_params=["9", 2, SEASON])
    _team_games_apply_period(team_con, narrowed, scope)
    sql, params = team_rows_sql(narrowed, "tg.event_id, tg.team_score", order="tg.eastern_date")
    return team_con.execute(sql, params).fetchall()


def test_the_period_cell_changes_what_the_team_relation_reads(team_con: duckdb.DuckDBPyConnection) -> None:  # noqa: F811 - the fixture
    whole = _team_score(team_con, Scope())
    half = _team_score(team_con, Scope(period=Period(number=1, half=True)))
    assert [row[0] for row in whole] == [row[0] for row in half] == ["g1", "g2"], "the same games, each seen as its first half"
    assert half == [("g1", 50), ("g2", 50)] and whole != half
