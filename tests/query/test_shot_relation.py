"""The shot relation's reads (:mod:`association.query.shots`), each over a
fixture warehouse: the games a narrowing sends a shot read to, a player's
average shot distance, and the shots a chart draws. Phase 4, step 5 moved
the statements here from ``compose.shots``; these hold what each returns,
apart from the reader that says it.
"""

from __future__ import annotations

import math
from typing import Any

import duckdb
import pytest
from test_shot_scoping import SEASON, shots_ctx  # noqa: F401 - the shot readers' own fixture warehouse, used here by name

from association.query.answer import AnswerContext
from association.query.conditions import box_source
from association.query.entities import Entity
from association.query.player_games import Narrowed
from association.query.player_relation import ResolvedSpan, scoped_games, span_of
from association.query.reading import Cuts, Reading, Span
from association.query.shots import chart_shots, distance, narrowed_games


def _shot(event: str, x: int, y: int, made: bool, points: int, shot_type: str = "Jump Shot", *, season: int = 2026, season_type: int = 2, clock: str = "10:00") -> tuple[Any, ...]:
    """One ``shot_chart`` row of player ``1``: its position, whether it went
    in and the value ESPN labeled (0 = unlabeled)."""
    return ("1", season, season_type, event, 1, clock, made, shot_type, x, y, points, "jumper")


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    """A season of located shots: two games, a three 26 feet out, twos at 10
    and 20 feet, a free throw (left off every read), a shot with no position
    and a postseason shot."""
    c = duckdb.connect(":memory:")
    c.execute(
        "CREATE TABLE shot_chart (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, "
        "period INTEGER, clock VARCHAR, made BOOLEAN, shot_type VARCHAR, coordinate_x INTEGER, coordinate_y INTEGER, points_attempted INTEGER, description VARCHAR)"
    )
    c.executemany(
        "INSERT INTO shot_chart VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            _shot("g2", 25, 26, True, 3, clock="09:00"),
            _shot("g1", 25, 10, False, 2),
            _shot("g1", 25, 20, True, 2, clock="08:00"),
            _shot("g1", 25, 15, True, 1, "Free Throw - 1 of 2"),
            _shot("g2", 0, 0, True, 2),
            _shot("p1", 25, 26, True, 3, season_type=3),
        ],
    )
    return c


def _span(season: int | None, season_type: int = 2) -> ResolvedSpan:
    """The span a shot read is over: one season, or a career (``None``)."""
    return ResolvedSpan(season=season, season_type=season_type)


def test_a_distance_averages_the_located_shots_but_free_throws(con: duckdb.DuckDBPyConnection) -> None:
    """26, 10 and 20 feet over two games: the free throw and the shot with
    no position are no distance, and the postseason shot is another span."""
    average, attempts, games = distance(con, "1", _span(2026), None, None)
    assert (math.isclose(average, 56 / 3), attempts, games) == (True, 3, 2)


def test_a_distance_reads_one_shot_value_and_the_games_pinned(con: duckdb.DuckDBPyConnection) -> None:
    """Threes alone, and twos in the one game pinned."""
    assert distance(con, "1", _span(2026), 3, None) == (26.0, 1, 1)
    assert distance(con, "1", _span(2026), 2, None) == (15.0, 2, 1)
    assert distance(con, "1", _span(2026), 2, ["g1"]) == (15.0, 2, 1)
    assert distance(con, "1", _span(2025), None, None) == (None, 0, 0)


def test_a_career_distance_reads_every_season_of_the_type(con: duckdb.DuckDBPyConnection) -> None:
    """No season filter on a career: the postseason's one three is all of
    its type on record."""
    assert distance(con, "1", _span(None, 3), None, None) == (26.0, 1, 1)


def test_a_chart_reads_the_located_shots_in_a_total_order(con: duckdb.DuckDBPyConnection) -> None:
    """Every located shot but the free throw, by game, period and clock,
    each with its value last."""
    rows = chart_shots(con, "1", 2026, 2, None, None)
    assert [(r[6], r[5], r[8]) for r in rows] == [("g1", "08:00", 2), ("g1", "10:00", 2), ("g2", "09:00", 3)]


def test_a_chart_reads_one_value_or_the_games_pinned(con: duckdb.DuckDBPyConnection) -> None:
    """Threes alone, twos alone; the games pinned with no season named."""
    assert [r[6] for r in chart_shots(con, "1", 2026, 2, None, 3)] == ["g2"]
    assert [(r[6], r[8]) for r in chart_shots(con, "1", 2026, 2, None, 2)] == [("g1", 2), ("g1", 2)]
    assert [r[6] for r in chart_shots(con, "1", None, None, ("p1", "g1"), None)] == ["g1", "g1", "p1"]


def test_the_games_a_narrowing_sends_a_shot_read_to(shots_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture, by name
    """Curry's Lakers games in the season (e1, e3, e5, e6), each with its
    Eastern day, read through the player-games relation's own reader."""
    con = shots_ctx.con
    reading = Reading(cuts=Cuts(opponent="Los Angeles Lakers"), span=Span(season=SEASON))
    narrowed = scoped_games(con, Entity(id="1", name="Stephen Curry"), span_of(reading.span, "player_game_log"), reading, opponent="Los Angeles Lakers", measures=[])
    assert isinstance(narrowed, Narrowed)
    rows = narrowed_games(con, narrowed, box_source(con))
    assert sorted((event, str(day)) for event, day in rows) == [(e, f"{SEASON}-11-{d}") for e, d in (("e1", "01"), ("e3", "05"), ("e5", "09"), ("e6", "11"))]
