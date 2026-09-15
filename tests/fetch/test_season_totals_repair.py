"""Tests for the load-time repair of a traded player's combined season row.

Every fixture below is the shape ESPN really serves. The Murdock row is his
1995-96 line verbatim - a combined row that is a byte-copy of his 9-game
Vancouver stint, beside a 64-game Milwaukee stint it claims to include - and
the all-NULL row is the shape all six of the 1977-1983 combined rows have.
"""

from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from association.fetch import warehouse

# Every column the repair reads, so a fixture only has to name what it changes.
_STINT: dict[str, Any] = {
    "athlete_id": "1",
    "season": 1996,
    "season_type": 2,
    "team_id": "29",
    "position": "PG",
    "gamesPlayed": 64,
    "gamesStarted": 60,
    "avgMinutes": 23.1,
    "avgPoints": 9.1,
    "fieldGoalPct": 42.2,
    "fieldGoalsMade": 220,
    "fieldGoalsAttempted": 521,
    "threePointFieldGoalsMade": 30,
    "threePointFieldGoalsAttempted": 90,
    "freeThrowsMade": 115,
    "freeThrowsAttempted": 150,
    "freeThrowPct": 76.7,
    "totalRebounds": 150,
    "assists": 240,
    "steals": 110,
    "blocks": 10,
    "fouls": 150,
    "turnovers": 100,
    "points": 585,
    "assistTurnoverRatio": 2.4,
    "stealTurnoverRatio": 1.1,
    "scoringEfficiency": 1.123,
    "shootingEfficiency": 0.46,
}


def _rows(*overrides: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**_STINT, **o} for o in overrides]


def _build(tmp_path: Path, rows: list[dict[str, Any]]) -> duckdb.DuckDBPyConnection:
    data_dir = tmp_path / "parquet"
    players = data_dir / "players"
    players.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([{"athlete_id": "1", "display_name": "Eric Murdock"}]), players / "f.parquet")
    d = data_dir / "player_season_stats"
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), d / "f.parquet")
    warehouse.build(data_dir, tmp_path / "test.duckdb")
    return duckdb.connect(str(tmp_path / "test.duckdb"))


def _combined(con: duckdb.DuckDBPyConnection, column: str = "gamesPlayed") -> Any:
    row = con.execute(f'SELECT "{column}" FROM player_season_stats WHERE team_id IS NULL').fetchone()
    return row[0] if row else None


def test_a_combined_row_that_copies_one_stint_is_rebuilt_from_all_of_them(tmp_path: Path) -> None:
    """The P1 this fixes. Murdock's combined row IS his 9-game Vancouver stint,
    so "Eric Murdock 1995-96 stats" answered 9 games at 6.9 a game for a season
    he played 73 games of. Summing the stints is a re-derivation, not an
    estimate: a season's games are the games he played."""
    con = _build(
        tmp_path,
        _rows(
            {"team_id": "29", "gamesPlayed": 64, "points": 585},
            {"team_id": "15", "gamesPlayed": 9, "points": 62, "fieldGoalsMade": 24, "fieldGoalsAttempted": 66, "assists": 58, "turnovers": 20, "steals": 10, "threePointFieldGoalsMade": 4},
            # The combined row, a byte-copy of the Vancouver stint.
            {"team_id": None, "gamesPlayed": 9, "points": 62, "fieldGoalsMade": 24, "fieldGoalsAttempted": 66, "assists": 58, "turnovers": 20, "steals": 10, "threePointFieldGoalsMade": 4},
        ),
    )
    assert _combined(con, "gamesPlayed") == 73
    assert _combined(con, "points") == 647
    assert _combined(con, "avgPoints") == 8.9
    con.close()


def test_an_all_null_combined_row_is_rebuilt_too(tmp_path: Path) -> None:
    """The six 1977-1983 rows. The per-season endpoint answers 404 for every
    one of them, so nothing at fetch time can fill them - but their stints are
    right there."""
    con = _build(
        tmp_path,
        _rows(
            {"season": 1977, "team_id": "10", "gamesPlayed": 80, "points": 1083},
            {"season": 1977, "team_id": "12", "gamesPlayed": 2, "points": 0},
            {"season": 1977, "team_id": None, "gamesPlayed": None, "points": None, "avgPoints": None, "fieldGoalsMade": None, "fieldGoalsAttempted": None},
        ),
    )
    assert _combined(con, "gamesPlayed") == 82
    assert _combined(con, "points") == 1083
    con.close()


def test_a_combined_row_that_already_agrees_is_left_exactly_alone(tmp_path: Path) -> None:
    """The guard that keeps this from rewriting 2,043 healthy rows. ESPN's own
    figure is kept wherever it equals the sum - including averages this module
    would otherwise recompute and round differently."""
    con = _build(
        tmp_path,
        _rows(
            {"team_id": "29", "gamesPlayed": 40, "points": 600, "fieldGoalsAttempted": 500, "fieldGoalsMade": 240},
            {"team_id": "20", "gamesPlayed": 30, "points": 360, "fieldGoalsAttempted": 300, "fieldGoalsMade": 140},
            {"team_id": None, "gamesPlayed": 70, "points": 960, "avgPoints": 13.8, "avgMinutes": 27.5, "fieldGoalsAttempted": 800, "fieldGoalsMade": 380},
        ),
    )
    assert _combined(con, "avgPoints") == 13.8
    # avgMinutes survives on a row this module did not touch - it is only
    # NULLed where the row is rebuilt and the figure cannot be derived.
    assert _combined(con, "avgMinutes") == 27.5
    con.close()


def test_a_rebuilt_row_leaves_minutes_null_rather_than_approximating_them(tmp_path: Path) -> None:
    """avgMinutes is the one average with no season total behind it. Two
    approximations were fitted against the warehouse and both were rejected (a
    games-weighted mean of the stints is right 81% of the time, box-score
    minutes 61%), so a rebuilt row says it does not know."""
    con = _build(
        tmp_path,
        _rows(
            {"team_id": "29", "gamesPlayed": 64, "points": 585, "avgMinutes": 23.1},
            {"team_id": "15", "gamesPlayed": 9, "points": 62, "avgMinutes": 21.4},
            {"team_id": None, "gamesPlayed": 9, "points": 62, "avgMinutes": 21.4},
        ),
    )
    assert _combined(con, "gamesPlayed") == 73
    assert _combined(con, "avgMinutes") is None
    con.close()


def test_a_ratio_over_zero_turnovers_is_null_not_infinity(tmp_path: Path) -> None:
    """DuckDB returns inf for x/0 in floating point, and ESPN has no convention
    of its own to match - of its zero-turnover rows, 765 carry inf, 641 carry
    0.0 and 332 carry NULL. Writing inf into a column that otherwise holds a
    real number is the fluent-and-false shape this project keeps producing."""
    con = _build(
        tmp_path,
        _rows(
            {"team_id": "29", "gamesPlayed": 5, "points": 10, "assists": 4, "turnovers": 0, "steals": 2},
            {"team_id": "15", "gamesPlayed": 3, "points": 6, "assists": 2, "turnovers": 0, "steals": 1},
            {"team_id": None, "gamesPlayed": 3, "points": 6, "assists": 2, "turnovers": 0, "steals": 1},
        ),
    )
    assert _combined(con, "gamesPlayed") == 8
    assert _combined(con, "assistTurnoverRatio") is None
    assert _combined(con, "stealTurnoverRatio") is None
    con.close()


def test_the_repair_writes_the_same_numbers_on_a_second_build(tmp_path: Path) -> None:
    """Keyed on whether a combined row disagrees with its stints, never on a
    list of athletes or on a value looking wrong - so a rebuilt row is not
    rebuilt again from itself. A repair that is not idempotent silently drifts
    every time `data load` runs."""
    rows = _rows(
        {"team_id": "29", "gamesPlayed": 64, "points": 585},
        {"team_id": "15", "gamesPlayed": 9, "points": 62},
        {"team_id": None, "gamesPlayed": 9, "points": 62},
    )
    data_dir = tmp_path / "parquet"
    players = data_dir / "players"
    players.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([{"athlete_id": "1", "display_name": "Eric Murdock"}]), players / "f.parquet")
    d = data_dir / "player_season_stats"
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), d / "f.parquet")
    db_path = tmp_path / "test.duckdb"
    warehouse.build(data_dir, db_path)
    warehouse.build(data_dir, db_path)
    con = duckdb.connect(str(db_path))
    assert con.execute("SELECT gamesPlayed, points FROM player_season_stats WHERE team_id IS NULL").fetchall() == [(73, 647)]
    con.close()


def test_the_deduped_view_serves_the_repaired_row(tmp_path: Path) -> None:
    """The whole point of repairing in place rather than in a view: every
    reader sees it. player_season_stats_deduped prefers the combined row, so
    before this it served the 9-game copy to player_stat, player_compare and
    player_history alike."""
    con = _build(
        tmp_path,
        _rows(
            {"team_id": "29", "gamesPlayed": 64, "points": 585},
            {"team_id": "15", "gamesPlayed": 9, "points": 62},
            {"team_id": None, "gamesPlayed": 9, "points": 62},
        ),
    )
    assert con.execute("SELECT gamesPlayed, points FROM player_season_stats_deduped").fetchall() == [(73, 647)]
    con.close()
