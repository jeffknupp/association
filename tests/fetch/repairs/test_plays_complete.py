"""Tests for the table saying where a player's play-by-play adds up to his box
score (``plays_complete``).

The shape under test is the one ESPN really serves for 2016 (``DATA.md``, "A
team's play-by-play does not add up to its box score in six seasons"): a made
shot's row is ABSENT from the plays - Patrick Patterson's line in Toronto at
New York, 2016-02-22 (``400827996``), is 4 points on 2 of 6, and the shot
chart holds one make and four misses - while a teammate's line in the same
game (Bismack Biyombo's 4 points on 2 of 3, 3 rebounds) is all there. The
box score says which, stat by stat.
"""

from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from association.fetch import warehouse
from association.nba.period_line import PERIOD_COLUMNS, PLAYS_COMPLETE, gap_column

_EVENT = "400827996"
_UNCOVERED = "400827997"


def _box(athlete: str, *, minutes: int | None = 20, event: str = _EVENT, dnp: bool = False, **stats: int) -> dict[str, Any]:
    """One ``player_box_stats`` row of Toronto's, every stat 0 but ``stats``."""
    row: dict[str, Any] = {
        "event_id": event,
        "season": 2016,
        "season_type": 2,
        "team_id": "28",
        "opponent_team_id": "18",
        "athlete_id": athlete,
        "minutes": minutes,
        "starter": False,
        "did_not_play": dnp,
        "ejected": False,
        "plusMinus": 0,
    }
    row.update(dict.fromkeys(PERIOD_COLUMNS, 0))
    row.update(stats)
    return row


def _shot(athlete: str, n: int, *, made: bool, period: int = 1) -> dict[str, Any]:
    """A two-point jump shot in the shot table."""
    return {
        "event_id": _EVENT,
        "play_id": f"s{athlete}{n}",
        "season": 2016,
        "season_type": 2,
        "athlete_id": athlete,
        "participant_athlete_ids": athlete,
        "team_id": "28",
        "period": period,
        "made": made,
        "shot_type": "Jump Shot",
        "points_attempted": 2,
        "coordinate_x": 25,
        "coordinate_y": 10,
        "description": f"{athlete} {'makes' if made else 'misses'} 12-foot jumper",
    }


def _rebound(athlete: str, n: int) -> dict[str, Any]:
    """A defensive rebound in the plays."""
    return {
        "event_id": _EVENT,
        "play_id": f"r{athlete}{n}",
        "season": 2016,
        "season_type": 2,
        "period": 2,
        "team_id": "28",
        "athlete_id": athlete,
        "participant_athlete_ids": athlete,
        "type": "Defensive Rebound",
        "text": f"{athlete} defensive rebound",
        "scoring_play": False,
    }


# "patterson": 4 points on 2 of 6 in the box; one make is absent from the
# shots, so his points, makes and attempts fall short. His 6 rebounds are all
# there. "biyombo": 4 on 2 of 3 and 3 rebounds, every row there. "powell":
# 0 on 0 of 0 in 5 minutes, no row at all - complete, a zero being a zero.
# "dnp" did not play; "empty" is a line ESPN served empty (no minutes), whose
# figures nothing checks; "elsewhere" played a game the shot table does not
# cover.
_BOX_ROWS = [
    _box("patterson", points=4, fieldGoalsMade=2, fieldGoalsAttempted=6, rebounds=6, defensiveRebounds=6),
    _box("biyombo", points=4, fieldGoalsMade=2, fieldGoalsAttempted=3, rebounds=3, defensiveRebounds=3),
    _box("powell", minutes=5),
    _box("dnp", minutes=None, dnp=True),
    _box("empty", minutes=None),
    _box("elsewhere", event=_UNCOVERED, points=10, fieldGoalsMade=5, fieldGoalsAttempted=9),
]
_SHOT_ROWS = [
    _shot("patterson", 1, made=True),
    *(_shot("patterson", n, made=False) for n in range(2, 6)),
    _shot("biyombo", 1, made=True),
    _shot("biyombo", 2, made=True, period=3),
    _shot("biyombo", 3, made=False),
]
_PLAY_ROWS = [*(_rebound("patterson", n) for n in range(6)), *(_rebound("biyombo", n) for n in range(3))]


def _write(data_dir: Path, table: str, rows: list[dict[str, Any]]) -> None:
    d = data_dir / table
    d.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), d / "f.parquet")


def _build(tmp_path: Path, *, with_shots: bool = True, plays: list[dict[str, Any]] | None = None) -> duckdb.DuckDBPyConnection:
    data_dir = tmp_path / "parquet"
    _write(data_dir, "teams", [{"team_id": "28", "abbreviation": "TOR"}, {"team_id": "18", "abbreviation": "NY"}])
    _write(data_dir, "players", [{"athlete_id": row["athlete_id"], "display_name": row["athlete_id"]} for row in _BOX_ROWS])
    _write(data_dir, "games", [{"event_id": event, "season": 2016, "date": "2016-02-23T00:30Z"} for event in (_EVENT, _UNCOVERED)])
    _write(data_dir, "player_box_stats", _BOX_ROWS)
    _write(data_dir, "plays", _PLAY_ROWS if plays is None else plays)
    if with_shots:
        _write(data_dir, "shot_chart", _SHOT_ROWS)
    db_path = tmp_path / "test.duckdb"
    warehouse.build(data_dir, db_path)
    return duckdb.connect(str(db_path))


def _flags(con: duckdb.DuckDBPyConnection, athlete: str) -> dict[str, Any] | None:
    cur = con.execute(f"SELECT * FROM {PLAYS_COMPLETE} WHERE athlete_id = ?", [athlete])
    names = [d[0] for d in cur.description]
    got = cur.fetchone()
    return dict(zip(names, got, strict=True)) if got else None


def test_a_made_shot_absent_from_the_plays_leaves_his_points_makes_and_attempts_short(tmp_path: Path) -> None:
    """The box score less the plays: one make of two points absent, so his
    points are 2 short, his makes 1 and his attempts 1 (the shot chart holds
    5 of his 6 attempts)."""
    con = _build(tmp_path)
    patterson = _flags(con, "patterson")
    con.close()
    assert patterson is not None
    assert (patterson["points_gap"], patterson["fieldGoalsMade_gap"], patterson["fieldGoalsAttempted_gap"]) == (2, 1, 1)
    # What the plays do hold of him adds up: the absent row was a make.
    assert patterson["rebounds_gap"] == 0
    assert patterson["defensiveRebounds_gap"] == 0
    assert patterson["threePointFieldGoalsMade_gap"] == 0
    assert patterson["freeThrowsMade_gap"] == 0


def test_plays_holding_more_than_the_box_score_are_a_negative_gap(tmp_path: Path) -> None:
    """The other direction: a rebound the plays credit him that the box score
    does not (Biyombo's three rebounds in the box, four in the plays) is a
    gap of -1 - the period figure may be over, not short."""
    con = _build(tmp_path, plays=[*_PLAY_ROWS, _rebound("biyombo", 9)])
    biyombo = _flags(con, "biyombo")
    con.close()
    assert biyombo is not None and biyombo["rebounds_gap"] == -1 and biyombo["points_gap"] == 0


def test_a_teammate_whose_rows_are_all_there_is_complete_in_the_same_game(tmp_path: Path) -> None:
    """The fault falls on a player's game, not on the game or the season:
    the period reads count his and leave the other's out."""
    con = _build(tmp_path)
    biyombo = _flags(con, "biyombo")
    con.close()
    assert biyombo is not None
    assert all(biyombo[gap_column(c)] == 0 for c in PERIOD_COLUMNS)


def test_a_player_with_no_shot_and_no_play_is_complete_on_his_zeros(tmp_path: Path) -> None:
    con = _build(tmp_path)
    powell = _flags(con, "powell")
    con.close()
    assert powell is not None
    assert all(powell[gap_column(c)] == 0 for c in PERIOD_COLUMNS)


def test_only_played_lines_in_covered_games_have_a_row(tmp_path: Path) -> None:
    """A line ESPN served empty is rebuilt FROM the plays, so comparing the two
    checks nothing; a game the shot table does not cover has no period line.
    Both are unknown - no row - never complete."""
    con = _build(tmp_path)
    athletes = {row[0] for row in con.execute(f"SELECT athlete_id FROM {PLAYS_COMPLETE}").fetchall()}
    con.close()
    assert athletes == {"patterson", "biyombo", "powell"}


def test_the_table_is_skipped_without_the_shot_table(tmp_path: Path) -> None:
    """A partial warehouse must not fail the whole build over this table."""
    con = _build(tmp_path, with_shots=False)
    tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    con.close()
    assert PLAYS_COMPLETE not in tables
    assert "player_game_log" in tables


def test_a_column_the_period_line_does_not_read_is_refused() -> None:
    with pytest.raises(ValueError, match="minutes"):
        gap_column("minutes")
