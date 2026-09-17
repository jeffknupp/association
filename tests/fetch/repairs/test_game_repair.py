"""Tests for putting a game's teams back on the sides they played.

The fixture row is ``100614008`` as ESPN serves it, refetched 2026-09-16:
Detroit (8) at home scoring 90, Portland (22) away scoring 92 and winning. The
real game was Detroit's 92-90 title-clinching win at Portland.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from association.fetch import warehouse
from association.fetch.repairs import game_repair


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE games (event_id VARCHAR, season BIGINT, home_team_id VARCHAR, away_team_id VARCHAR, home_score BIGINT, away_score BIGINT, winner_team_id VARCHAR)")
    c.executemany(
        "INSERT INTO games VALUES (?,?,?,?,?,?,?)",
        [
            ("100614008", 1989, "8", "22", 90, 92, "22"),
            # The Game 4 before it is right, and must stay so.
            ("100612022", 1989, "22", "8", 109, 112, "8"),
        ],
    )
    c.execute("CREATE TABLE team_box_stats (event_id VARCHAR, team_id VARCHAR, home_away VARCHAR, assists BIGINT)")
    c.executemany(
        "INSERT INTO team_box_stats VALUES (?,?,?,?)",
        [("100614008", "8", "home", None), ("100614008", "22", "away", None), ("100612022", "22", "home", None), ("100612022", "8", "away", None)],
    )
    return c


def _games(c: duckdb.DuckDBPyConnection) -> dict[str, tuple[str, str, int, int, str]]:
    return {r[0]: tuple(r[1:]) for r in c.execute("SELECT event_id, home_team_id, away_team_id, home_score, away_score, winner_team_id FROM games").fetchall()}


def _sides(c: duckdb.DuckDBPyConnection) -> dict[tuple[str, str], str]:
    return {(r[0], r[1]): r[2] for r in c.execute("SELECT event_id, team_id, home_away FROM team_box_stats").fetchall()}


def test_the_1990_clincher_is_detroits_win_at_portland(con: duckdb.DuckDBPyConnection) -> None:
    game_repair.repair(con, {"games", "team_box_stats"})
    games = _games(con)
    assert games["100614008"] == ("22", "8", 90, 92, "8")
    assert games["100612022"] == ("22", "8", 109, 112, "8")
    sides = _sides(con)
    assert (sides[("100614008", "8")], sides[("100614008", "22")]) == ("away", "home")
    assert (sides[("100612022", "22")], sides[("100612022", "8")]) == ("home", "away")


def test_the_repair_is_idempotent(con: duckdb.DuckDBPyConnection) -> None:
    """Guarded on the stored value, so a second run - every partial load -
    does not swap the game back."""
    game_repair.repair(con, {"games", "team_box_stats"})
    game_repair.repair(con, {"games", "team_box_stats"})
    assert _games(con)["100614008"] == ("22", "8", 90, 92, "8")
    assert _sides(con)[("100614008", "8")] == "away"


def test_a_game_espn_has_corrected_is_left_alone(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("DELETE FROM games WHERE event_id = '100614008'")
    con.execute("INSERT INTO games VALUES ('100614008', 1989, '22', '8', 90, 92, '8')")
    game_repair.repair(con, {"games"})
    assert _games(con)["100614008"] == ("22", "8", 90, 92, "8")


def test_a_row_that_differs_from_what_espn_served_on_either_side_is_left_alone(con: duckdb.DuckDBPyConnection) -> None:
    """The guard is the exact pair ESPN serves. A row ESPN has changed in any
    way is no longer the row the evidence was about, so it is not swapped."""
    for home, away in (("1", "22"), ("8", "1")):
        con.execute("DELETE FROM games WHERE event_id = '100614008'")
        con.execute("INSERT INTO games VALUES ('100614008', 1989, ?, ?, 90, 92, '22')", [home, away])
        game_repair.repair(con, {"games"})
        assert _games(con)["100614008"] == (home, away, 90, 92, "22")


def test_a_partial_load_of_games_alone_does_not_fail(con: duckdb.DuckDBPyConnection) -> None:
    game_repair.repair(con, {"games"})
    assert _games(con)["100614008"] == ("22", "8", 90, 92, "8")
    # team_box_stats was not in the loaded set, so it is untouched.
    assert _sides(con)[("100614008", "8")] == "home"


def test_a_warehouse_build_repairs_the_game_before_real_games_copies_it(tmp_path: Path) -> None:
    """The repair is only worth anything if the build runs it, and runs it
    ahead of real_games, which every template reads instead of games."""
    data_dir = tmp_path / "parquet"
    games = [
        {"event_id": "100614008", "season": 1989, "season_type": 3, "date": "1990-06-14T04:00Z", "home_team_id": "8", "away_team_id": "22", "home_score": 90, "away_score": 92, "winner_team_id": "22"}
    ]
    teams = [{"team_id": "8", "display_name": "Detroit Pistons"}, {"team_id": "22", "display_name": "Portland Trail Blazers"}]
    for table, rows in (("games", games), ("teams", teams)):
        (data_dir / table).mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist(rows), data_dir / table / "f.parquet")
    db_path = tmp_path / "test.duckdb"
    warehouse.build(data_dir, db_path)
    c = duckdb.connect(str(db_path))
    got = c.execute("SELECT home_team_id, away_team_id, winner_team_id FROM real_games WHERE event_id = '100614008'").fetchall()
    c.close()
    assert got == [("22", "8", "8")]
