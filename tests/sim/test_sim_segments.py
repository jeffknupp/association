"""The lineup walk on a hand-built game: period starters from the box and
from the first-touch rule, the fill rule, substitutions that agree and
disagree with the lineup, a one-id substitution, a bench technical, points
attributed by sequence, and the sibling checks against the box score.

Home team H starts h1-h5 with h6, h7 on the bench; away team A starts a1-a5
with a6 on the bench. Every clock below is a quarter clock; the fixture has
two quarters so the fill rule is exercised at the start of the second.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pytest

from association.sim import database, plays, segments

EVENT = "501"
SEASON = 2025


def _play(pid: int, period: int, clock: str, team: str | None, athlete: str | None, parts: str | None, type_: str, text: str, home: int, away: int, scoring: bool = False) -> dict[str, Any]:
    return {
        "event_id": EVENT,
        "play_id": f"{EVENT}{pid}",
        "season": SEASON,
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


_PLAYS = [
    # First quarter. h5 never touches the ball in it and is still on the floor at the end.
    _play(1, 1, "12:00", "H", "h1", "h1,a1,h2", "Jumpball", "h1 vs. a1 (h2 gains possession)", 0, 0),
    _play(2, 1, "11:30", "H", "h2", "h2,h1", "Jump Shot", "h2 makes 20-foot jumper (h1 assists)", 2, 0, True),
    _play(3, 1, "11:00", "A", "a1", "a1", "Layup Shot", "a1 misses layup", 2, 0),
    _play(31, 1, "10:59", "A", "a4", "a4", "Offensive Rebound", "a4 offensive rebound", 2, 0),
    _play(32, 1, "10:58", "A", "a5", "a5", "Jump Shot", "a5 misses 18-foot jumper", 2, 0),
    _play(4, 1, "10:57", "H", "h3", "h3", "Defensive Rebound", "h3 defensive rebound", 2, 0),
    # A bench technical: a9 is on nobody's floor and must not be read as present.
    _play(5, 1, "10:50", "A", "a9", "a9", "Technical Foul", "a9 technical foul", 2, 0),
    _play(6, 1, "10:50", "H", "h4", "h4", "Free Throw - Technical", "h4 makes technical free throw", 3, 0, True),
    # A normal substitution at a dead ball, then play on.
    _play(7, 1, "10:00", "A", "a6", "a6,a2", "Substitution", "a6 enters the game for a2", 3, 0),
    _play(8, 1, "9:30", "A", "a6", "a6", "Jump Shot", "a6 makes 15-foot jumper", 3, 2, True),
    # Two substitutions at one clock: the zero-length segment between them is dropped.
    _play(9, 1, "6:00", "H", "h6", "h6,h1", "Substitution", "h6 enters the game for h1", 3, 2),
    _play(10, 1, "6:00", "H", "h7", "h7,h2", "Substitution", "h7 enters the game for h2", 3, 2),
    # A shooting foul and a free-throw trip with a substitution between the attempts: the
    # second free throw is still credited to the five on the floor before the substitution
    # (ESPN's plus-minus convention, measured); h1 is on for the rest of the quarter.
    _play(11, 1, "3:00", "A", "a3", "a3,h7", "Shooting Foul", "a3 shooting foul (h7 draws the foul)", 3, 2),
    _play(12, 1, "3:00", "H", "h7", "h7", "Free Throw - 1 of 2", "h7 makes free throw 1 of 2", 4, 2, True),
    _play(13, 1, "3:00", "H", "h1", "h1,h6", "Substitution", "h1 enters the game for h6", 4, 2),
    _play(14, 1, "3:00", "H", "h7", "h7", "Free Throw - 2 of 2", "h7 makes free throw 2 of 2", 5, 2, True),
    _play(15, 1, "0.0", None, None, None, "End Period", "End of the 1st Quarter", 5, 2),
    # Second quarter. H's five: h1, h3, h4, h5, h7 closed the first; h5 and h4 never touch
    # the ball in the second, so the fill rule carries them. A's five: a1, a3, a4, a5, a6.
    _play(16, 2, "11:40", "H", "h7", "h7,h3", "Jump Shot", "h7 makes 10-foot jumper (h3 assists)", 7, 2, True),
    _play(17, 2, "11:20", "A", "a1", "a1", "Jump Shot", "a1 misses 20-foot jumper", 7, 2),
    _play(18, 2, "11:19", "H", "h1", "h1", "Defensive Rebound", "h1 defensive rebound", 7, 2),
    # A substitution whose leaver is NOT on the floor: a2 sat down in the first quarter.
    _play(19, 2, "9:00", "A", "a2", "a2,a2", "Substitution", "a2 enters the game for a2", 7, 2),
    _play(20, 2, "8:00", "A", "a3", "a3", "Lost Ball Turnover", "a3 lost ball turnover", 7, 2),
    # A one-id substitution for H: the entrant is unknown.
    _play(21, 2, "7:00", "H", "h7", "h7", "Substitution", "enters the game for h7", 7, 2),
    _play(22, 2, "6:00", "A", "a4", "a4", "Jump Shot", "a4 makes 20-foot jumper", 7, 4, True),
    _play(23, 2, "0.0", None, None, None, "End Period", "End of the 2nd Quarter", 7, 4),
    _play(24, 2, "0.0", None, None, None, "End Game", "End of Game", 7, 4),
]

# The box score: minutes and plus-minus as the segments should reproduce them for the
# first quarter (the second has an unresolved segment for each team, so the checks only
# read a fully resolved game; see the test that builds one).
_BOX = [
    {"event_id": EVENT, "season": SEASON, "season_type": 2, "team_id": team, "athlete_id": athlete, "starter": starter, "minutes": minutes, "plusMinus": pm, "did_not_play": minutes is None}
    for team, athlete, starter, minutes, pm in (
        ("H", "h1", True, 9, 1),
        ("H", "h2", True, 6, 1),
        ("H", "h3", True, 12, 3),
        ("H", "h4", True, 12, 3),
        ("H", "h5", True, 12, 3),
        ("H", "h6", False, 3, 2),
        ("H", "h7", False, 6, 2),
        ("A", "a1", True, 12, -3),
        ("A", "a2", True, 2, -3),
        ("A", "a3", True, 12, -3),
        ("A", "a4", True, 12, -3),
        ("A", "a5", True, 12, -3),
        ("A", "a6", False, 10, 0),
        ("A", "a9", False, None, None),
    )
]

_GAMES = [{"event_id": EVENT, "season": SEASON, "season_type": 2, "home_team_id": "H", "away_team_id": "A", "home_score": 7, "away_score": 4}]


def _warehouse(path: Path, play_rows: list[dict[str, Any]]) -> None:
    wh = duckdb.connect(str(path))
    for name, rows in (("plays", play_rows), ("shot_chart", []), ("real_games", _GAMES), ("player_box_stats_deduped", _BOX)):
        if rows:
            wh.register("rows", pa.Table.from_pylist(rows))
            wh.execute(f"CREATE TABLE {name} AS SELECT * FROM rows")
            wh.unregister("rows")
        else:
            wh.execute(
                f"CREATE TABLE {name} (event_id VARCHAR, play_id VARCHAR, season BIGINT, season_type BIGINT, shot_type VARCHAR, "
                "points_attempted BIGINT, coordinate_x BIGINT, coordinate_y BIGINT, description VARCHAR, made BOOLEAN)"
            )
    wh.close()


@pytest.fixture
def con(tmp_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    _warehouse(tmp_path / "nba.duckdb", _PLAYS)
    c = database.connect(tmp_path / "sim.duckdb", tmp_path / "nba.duckdb")
    plays.build_plays(c)
    yield c
    c.close()


@pytest.fixture
def first_quarter_only(tmp_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """The same game ending after the first quarter, every segment resolved,
    with the box score it should reproduce."""
    first = [p for p in _PLAYS if p["period"] == 1]
    first.append(_play(99, 1, "0.0", None, None, None, "End Game", "End of Game", 5, 2))
    _warehouse(tmp_path / "nba.duckdb", first)
    c = database.connect(tmp_path / "sim.duckdb", tmp_path / "nba.duckdb")
    plays.build_plays(c)
    yield c
    c.close()


def _segments(con: duckdb.DuckDBPyConnection) -> list[tuple[Any, ...]]:
    return con.execute("SELECT period, start_elapsed, end_elapsed, home_lineup, away_lineup, home_points, away_points, carried, resolved FROM sim_segments ORDER BY segment_no").fetchall()


def test_the_first_quarter_walk(con: duckdb.DuckDBPyConnection) -> None:
    segments.build_segments(con)
    first = [s for s in _segments(con) if s[0] == 1]
    # Segments: tip to a6's entry at 10:00; to the double substitution at 6:00; to h1's
    # return at 3:00 (between the free throws); to the end of the quarter.
    assert [(s[1], s[2]) for s in first] == [(0.0, 120.0), (120.0, 360.0), (360.0, 540.0), (540.0, 720.0)]
    assert first[0][3] == ["h1", "h2", "h3", "h4", "h5"] and first[0][4] == ["a1", "a2", "a3", "a4", "a5"]
    assert first[1][4] == ["a1", "a3", "a4", "a5", "a6"]
    assert first[2][3] == ["h3", "h4", "h5", "h6", "h7"]
    assert first[3][3] == ["h1", "h3", "h4", "h5", "h7"]
    # Points: the technical free throw and the first basket in the opening segment, a6's
    # jumper in the second, BOTH free throws to the five on the floor before h1's return.
    assert [(s[5], s[6]) for s in first] == [(3, 0), (0, 2), (2, 0), (0, 0)]
    assert all(s[8] for s in first)


def test_the_second_quarter_opens_by_touch_and_fill_and_goes_unresolved_where_the_rows_contradict(con: duckdb.DuckDBPyConnection) -> None:
    segments.build_segments(con)
    second = [s for s in _segments(con) if s[0] == 2]
    assert second[0][3] == ["h1", "h3", "h4", "h5", "h7"]  # h4 and h5 carried from the close of the first
    assert second[0][4] == ["a1", "a3", "a4", "a5", "a6"]
    assert second[0][7] is True  # carried
    assert second[0][8] is True  # resolved until a2's impossible substitution at 9:00
    assert second[1][8] is False  # A unresolved from there; H still fine until its one-id substitution
    rows = con.execute("SELECT team_id, source, resolved, reason, one_id_subs, inconsistent_subs FROM sim_team_periods WHERE period = 2 ORDER BY team_id").fetchall()
    assert rows == [("A", "carried", False, "inconsistent_substitution", 0, 1), ("H", "carried", False, "one_id_substitution", 1, 0)]


def test_period_one_uses_the_box_five_and_records_the_rules_agreement(con: duckdb.DuckDBPyConnection) -> None:
    segments.build_segments(con)
    rows = con.execute("SELECT team_id, source, touch_count, agrees_with_box FROM sim_team_periods WHERE period = 1 ORDER BY team_id").fetchall()
    # A's five all appear in the quarter (a2 leaves by substitution, which counts); H's h5 never touches,
    # so the rule found four and is not compared.
    assert rows == [("A", "box", 5, True), ("H", "box", 4, None)]


def test_a_bench_technical_is_not_a_touch_and_phantoms_are_counted_not_repaired(con: duckdb.DuckDBPyConnection) -> None:
    segments.build_segments(con)
    phantoms = con.execute("SELECT sum(phantom_touches) FROM sim_team_periods").fetchone()
    assert phantoms == (0,)
    assert "a9" not in {a for (lineup,) in con.execute("SELECT away_lineup FROM sim_segments").fetchall() for a in lineup}


def test_the_sibling_checks_read_a_fully_resolved_game(first_quarter_only: duckdb.DuckDBPyConnection) -> None:
    (report,) = segments.build_segments(first_quarter_only)
    assert report.season == SEASON and report.games == 1
    assert report.resolved_share == 1.0
    assert (report.starters_compared, report.starters_agree) == (1, 1)
    # Thirteen players have minutes; every one is within a minute of the segments'
    # seconds and has the exact plus-minus the fixture's box score was written with.
    assert report.player_games == 13
    assert report.minutes_within_1 == 13
    assert report.plus_minus_exact == 13


def test_the_unresolved_game_is_left_out_of_the_player_checks(con: duckdb.DuckDBPyConnection) -> None:
    (report,) = segments.build_segments(con)
    assert report.player_games == 0
    assert 0 < report.resolved_share < 1
    assert (report.one_id_subs, report.inconsistent_subs) == (1, 1)


def test_period_end_elapsed() -> None:
    assert segments.period_end_elapsed(4) == 2880
    assert segments.period_end_elapsed(5) == 3180
    assert segments.period_end_elapsed(0) == 0
