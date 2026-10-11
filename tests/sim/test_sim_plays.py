"""``sim_plays`` on a hand-built warehouse: the order of play, the synthesized
period boundaries, the kinds, the points and the per-season report.

The fixture game is shaped like the real anomalies Phase 0 found
(``SIMULATION_PLAN.md``, 0b): a substitution whose id is far later than the
dead ball it happened at, ids whose string order is wrong ("40110" before
"4012"), a dead-ball team rebound inside a free-throw trip, a scoring
``Not Available`` row, an overtime period, and one type the vocabulary has
never seen.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pytest

from association.sim import database, plays


def _play(
    pid: str, period: int, clock: str, team: str | None, athlete: str | None, parts: str | None, type_: str, text: str, home: int, away: int, scoring: bool, event: str = "401", season: int = 2025
) -> dict[str, Any]:
    return {
        "event_id": event,
        "play_id": pid,
        "season": season,
        "season_type": 2,
        "period": period,
        "clock": clock,
        "team_id": team,
        "athlete_id": athlete,
        "participant_athlete_ids": parts,
        "type": type_,
        "text": text,
        "home_score": home,
        "away_score": away,
        "scoring_play": scoring,
    }


# Home team H, away team A. Listed in NUMERIC id order, which is not the
# order of play: 40150 (a substitution at the 11:00 dead ball) and 401140 (a
# scoring play at 3:00 of overtime) belong earlier than their ids say.
_PLAYS = [
    _play("4011", 1, "12:00", "H", "h1", "h1,a1,h2", "Jumpball", "h1 vs. a1 (h2 gains possession)", 0, 0, False),
    _play("4012", 1, "11:40", "H", "h2", "h2,h1", "Jump Shot", "h2 makes 25-foot three point jumper (h1 assists)", 3, 0, True),
    _play("4013", 1, "11:20", "A", "a1", "a1", "Layup Shot", "a1 misses layup", 3, 0, False),
    _play("4014", 1, "11:19", "H", "h3", "h3", "Defensive Rebound", "h3 defensive rebound", 3, 0, False),
    _play("4015", 1, "11:00", "A", "a2", "a2,h3", "Shooting Foul", "a2 shooting foul (h3 draws the foul)", 3, 0, False),
    _play("4017", 1, "11:00", "H", "h3", "h3", "Free Throw - 1 of 2", "h3 misses free throw 1 of 2", 3, 0, False),
    _play("4018", 1, "11:00", "H", None, None, "Offensive Rebound", "H offensive team rebound", 3, 0, False),
    _play("40110", 1, "11:00", "H", "h3", "h3", "Free Throw - 2 of 2", "h3 makes free throw 2 of 2", 4, 0, True),
    _play("40111", 1, "10:40", "A", "a4", "a4,h1", "Bad Pass\nTurnover", "a4 bad pass (h1 steals)", 4, 0, False),
    _play("40112", 1, "0.0", None, None, None, "End Period", "End of the 1st Quarter", 4, 0, False),
    _play("40113", 2, "5:00", "A", "a1", "a1", "Mystery Play", "a1 does something new", 4, 0, False),
    _play("40114", 2, "42.3", "H", "h1", "h1", "Traveling", "h1 traveling", 4, 0, False),
    _play("40115", 2, "30.0", "A", "a9", "a9", "Technical Foul", "a9 technical foul", 4, 0, False),
    _play("40116", 2, "0.0", None, None, None, "End Period", "End of the 2nd Quarter", 4, 0, False),
    _play("40117", 5, "4:30", "A", "a1", "a1", "Dunk Shot", "a1 makes dunk", 4, 2, True),
    _play("40118", 5, "0.0", None, None, None, "End Period", "End of OT", 6, 2, False),
    _play("40119", 5, "0.0", None, None, None, "End Game", "End of Game", 6, 2, False),
    _play("40150", 1, "11:00", "A", "a4", "a4,a2", "Substitution", "a4 enters the game for a2", 4, 0, False),
    _play("401140", 5, "3:00", "H", None, None, "Not Available", "(03:00) [H] ", 6, 2, True),
    # A 2005 game: below the floor, never built.
    _play("3001", 1, "12:00", "H", "h1", "h1", "Jump Shot", "h1 makes jumper", 2, 0, True, event="300", season=2005),
    # A game real_games does not hold: a placeholder, never built.
    _play("9991", 1, "12:00", "H", "h1", "h1", "Jump Shot", "h1 makes jumper", 2, 0, True, event="999"),
]

_ORDER_OF_PLAY = ["4011", "4012", "4013", "4014", "4015", "4017", "4018", "40110", "40150", "40111", "40112", "40113", "40114", "40115", "40116", "40117", "401140", "40118", "40119"]


def _shot(pid: str, points_attempted: int, x: int, y: int, description: str, made: bool) -> dict[str, Any]:
    return {
        "event_id": "401",
        "play_id": pid,
        "season": 2025,
        "season_type": 2,
        "shot_type": "Jump Shot",
        "points_attempted": points_attempted,
        "coordinate_x": x,
        "coordinate_y": y,
        "description": description,
        "made": made,
    }


# The miss is unlabeled (points_attempted 0, as ESPN leaves it) and sits two
# feet from the rim, so its value comes from the geometry.
_SHOTS = [
    _shot("4012", 3, 2, 25, "h2 makes 25-foot three point jumper (h1 assists)", True),
    _shot("4013", 0, 25, 2, "a1 misses layup", False),
    _shot("40117", 2, 25, 1, "a1 makes dunk", True),
]

_GAMES = [
    {"event_id": "401", "season": 2025, "season_type": 2, "home_team_id": "H", "away_team_id": "A", "home_score": 6, "away_score": 2},
    {"event_id": "300", "season": 2005, "season_type": 2, "home_team_id": "H", "away_team_id": "A", "home_score": 2, "away_score": 0},
]


@pytest.fixture
def sim_con(tmp_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    warehouse_path = tmp_path / "nba.duckdb"
    wh = duckdb.connect(str(warehouse_path))
    for name, rows in (("plays", _PLAYS), ("shot_chart", _SHOTS), ("real_games", _GAMES)):
        wh.register("rows", pa.Table.from_pylist(rows))
        wh.execute(f"CREATE TABLE {name} AS SELECT * FROM rows")
        wh.unregister("rows")
    wh.close()
    con = database.connect(tmp_path / "sim.duckdb", warehouse_path)
    yield con
    con.close()


def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[tuple[Any, ...]]:
    return con.execute(sql).fetchall()


def test_the_order_of_play_is_period_then_clock_then_numeric_id(sim_con: duckdb.DuckDBPyConnection) -> None:
    plays.build_plays(sim_con, unmapped_limit=1.0)
    assert [r[0] for r in _rows(sim_con, "SELECT play_id FROM sim_plays ORDER BY seq")] == _ORDER_OF_PLAY


def test_period_boundaries_are_synthesized_and_elapsed_counts_overtime(sim_con: duckdb.DuckDBPyConnection) -> None:
    plays.build_plays(sim_con, unmapped_limit=1.0)
    starts = {r[0] for r in _rows(sim_con, "SELECT play_id FROM sim_plays WHERE period_start")}
    ends = {r[0] for r in _rows(sim_con, "SELECT play_id FROM sim_plays WHERE period_end")}
    assert starts == {"4011", "40113", "40117"}
    assert ends == {"40112", "40116", "40119"}
    elapsed = dict(_rows(sim_con, "SELECT play_id, elapsed FROM sim_plays"))
    assert elapsed["40117"] == 2880 + 30  # 4:30 left in the first overtime
    assert elapsed["40114"] == pytest.approx(720 + (720 - 42.3))  # a clock in seconds alone
    assert elapsed["4011"] == 0


def test_kinds_flags_and_the_text_resolved_rows(sim_con: duckdb.DuckDBPyConnection) -> None:
    plays.build_plays(sim_con, unmapped_limit=1.0)
    by_id = {r[0]: r[1:] for r in _rows(sim_con, "SELECT play_id, kind, bench_possible, live_ball, team_play FROM sim_plays")}
    assert by_id["4011"][0] == "jump_ball"
    assert by_id["4018"] == ("offensive_rebound", False, None, True)  # a team rebound keeps its side, as nobody's
    assert by_id["40111"] == ("turnover", False, True, False)
    assert by_id["40114"] == ("turnover", False, False, False)  # Traveling
    assert by_id["40115"] == ("foul_technical", True, None, False)
    assert by_id["40113"][0] is None  # the type the vocabulary has never seen
    assert by_id["401140"][0] == "unknown_score"  # a scoring Not Available row
    assert _rows(sim_con, "SELECT count(*) FROM sim_play_kinds")[0][0] == len(plays.vocab.PLAY_KINDS)


def test_points_come_from_the_play_and_sum_to_the_final(sim_con: duckdb.DuckDBPyConnection) -> None:
    (report,) = plays.build_plays(sim_con, unmapped_limit=1.0)
    points = dict(_rows(sim_con, "SELECT play_id, points FROM sim_plays"))
    values = dict(_rows(sim_con, "SELECT play_id, shot_value FROM sim_plays"))
    assert (points["4012"], values["4012"]) == (3, 3)
    assert (points["4013"], values["4013"]) == (0, 2)  # a miss worth two by geometry, scoring nothing
    assert points["40110"] == 1 and points["4017"] == 0
    assert points["40117"] == 2
    assert points["401140"] == 2  # the one kind whose points are the score's step
    assert points["40150"] == 0
    assert report == plays.SeasonReport(season=2025, games=1, plays=19, unmapped=1, games_reaching_final=1, points_match_final=1)
    assert report.unmapped_share == pytest.approx(1 / 19)


def test_games_below_the_floor_or_outside_real_games_are_not_built(sim_con: duckdb.DuckDBPyConnection) -> None:
    plays.build_plays(sim_con, unmapped_limit=1.0)
    assert {r[0] for r in _rows(sim_con, "SELECT DISTINCT event_id FROM sim_plays")} == {"401"}
    with pytest.raises(ValueError, match="below the floor"):
        plays.build_plays(sim_con, seasons=[2005], unmapped_limit=1.0)


def test_too_many_unmapped_plays_fail_the_build_and_name_the_types(sim_con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(plays.UnmappedPlays, match="Mystery Play"):
        plays.build_plays(sim_con)
    # The table is left in place so the unmapped types can be read.
    assert _rows(sim_con, "SELECT count(*) FROM sim_plays WHERE kind IS NULL")[0][0] == 1


def test_meta_records_the_build(sim_con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    assert database.read_meta(sim_con) == {}
    database.write_meta(sim_con, seasons=[2025, 2024], warehouse_path=tmp_path / "nba.duckdb")
    meta = database.read_meta(sim_con)
    assert meta["schema_version"] == str(database.SIM_SCHEMA_VERSION)
    assert meta["seasons"] == "2024,2025"
    assert meta["warehouse"].endswith("nba.duckdb")


def test_the_warehouse_is_attached_read_only(sim_con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(duckdb.Error):
        sim_con.execute("DELETE FROM nba.plays")


def test_connect_refuses_a_missing_warehouse(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        database.connect(tmp_path / "sim.duckdb", tmp_path / "nowhere.duckdb")
