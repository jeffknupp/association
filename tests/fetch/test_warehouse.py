"""Regression + sanity tests for the DuckDB warehouse builder."""

from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from association.fetch import warehouse


def test_build_ignores_directory_names_uses_embedded_columns(tmp_path: Path) -> None:
    """Regression: files live under season=X/season_type=Y directories, and
    each row ALSO embeds its own season/season_type columns. Reading with
    hive_partitioning=True made duckdb infer a SECOND copy of those columns
    from the directory names (typed int32) that collided with the embedded
    data columns (typed int64), raising ArrowTypeError. Only one copy, from
    the embedded data, should ever end up in the table."""
    data_dir = tmp_path / "parquet"
    d = data_dir / "games" / "season=2024" / "season_type=2"
    d.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([{"event_id": "1", "season": 2024, "season_type": 2}]), d / "f.parquet")

    db_path = tmp_path / "test.duckdb"
    warehouse.build(data_dir, db_path)

    con = duckdb.connect(str(db_path))
    cols = [r[0] for r in con.execute("DESCRIBE games").fetchall()]
    row = con.execute("SELECT season, season_type FROM games").fetchone()
    con.close()

    assert cols.count("season") == 1
    assert cols.count("season_type") == 1
    assert row == (2024, 2)


def test_build_merges_files_with_differing_column_types(tmp_path: Path) -> None:
    """Regression: an early-season power-index snapshot had every stat as
    None (ESPN hadn't computed them yet), so that file's column typed as
    null; a later, fully-populated season's file typed the same column as
    double. union_by_name must merge these without raising."""
    data_dir = tmp_path / "parquet"
    d1 = data_dir / "team_power_index" / "season=2021"
    d2 = data_dir / "team_power_index" / "season=2024"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([{"season": 2021, "team_id": "1", "winpct": None}]), d1 / "power_index.parquet")
    pq.write_table(pa.Table.from_pylist([{"season": 2024, "team_id": "1", "winpct": 0.75}]), d2 / "power_index.parquet")

    db_path = tmp_path / "test.duckdb"
    warehouse.build(data_dir, db_path)

    con = duckdb.connect(str(db_path))
    rows = con.execute("SELECT season, winpct FROM team_power_index ORDER BY season").fetchall()
    con.close()
    assert rows == [(2021, None), (2024, 0.75)]


def test_build_skips_tables_with_no_parquet_files(tmp_path: Path) -> None:
    data_dir = tmp_path / "parquet"
    data_dir.mkdir()
    db_path = tmp_path / "empty.duckdb"
    warehouse.build(data_dir, db_path)  # must not raise

    con = duckdb.connect(str(db_path))
    tables = con.execute("SHOW TABLES").fetchall()
    con.close()
    assert tables == []


def test_build_creates_player_game_log_view_when_dependencies_present(tmp_path: Path) -> None:
    data_dir = tmp_path / "parquet"
    fixtures = {
        "teams": {"team_id": "1", "abbreviation": "BOS"},
        "players": {"athlete_id": "10", "display_name": "Test Player"},
        "games": {"event_id": "100", "date": "2024-01-01"},
        "player_box_stats": {"event_id": "100", "athlete_id": "10", "team_id": "1", "opponent_team_id": "1", "points": 20},
    }
    for table, row in fixtures.items():
        d = data_dir / table
        d.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist([row]), d / "f.parquet")

    db_path = tmp_path / "test.duckdb"
    warehouse.build(data_dir, db_path)

    con = duckdb.connect(str(db_path))
    result = con.execute("SELECT player_name, points FROM player_game_log").fetchall()
    con.close()
    assert result == [("Test Player", 20)]


def _write_table_fixture(data_dir: Path, table: str, row: dict) -> None:
    d = data_dir / table
    d.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist([row]), d / "f.parquet")


_ADVANCED_STATS_PLAYER_BOX_ROW = {
    "event_id": "100", "season": 2024, "season_type": 2, "team_id": "1", "opponent_team_id": "1", "athlete_id": "10",
    "minutes": 36, "points": 30, "fieldGoalsMade": 10, "fieldGoalsAttempted": 20,
    "threePointFieldGoalsMade": 2, "freeThrowsMade": 8, "freeThrowsAttempted": 10,
    "offensiveRebounds": 1, "defensiveRebounds": 4, "assists": 5, "steals": 2, "blocks": 1,
    "fouls": 3, "turnovers": 3,
}


def test_player_game_log_gains_advanced_columns_when_built_with_advanced_stats(tmp_path: Path) -> None:
    """Regression: a model asking for a single game's ts_pct naturally tried
    player_game_log first (the documented per-game convenience view) and got
    told the column didn't exist there, even though --advanced-stats had been
    used - player_game_log never joined player_advanced_stats at all."""
    data_dir = tmp_path / "parquet"
    fixtures: dict[str, dict] = {
        "teams": {"team_id": "1", "abbreviation": "BOS"},
        "players": {"athlete_id": "10", "display_name": "Test Player"},
        "games": {"event_id": "100", "date": "2024-01-01"},
        "player_box_stats": _ADVANCED_STATS_PLAYER_BOX_ROW,
    }
    for table, row in fixtures.items():
        _write_table_fixture(data_dir, table, row)
    db_path = tmp_path / "test.duckdb"

    warehouse.build(data_dir, db_path, include_advanced_stats=True)

    con = duckdb.connect(str(db_path))
    result_row = con.execute("SELECT player_name, ts_pct FROM player_game_log WHERE event_id = '100'").fetchone()
    con.close()
    assert result_row is not None
    assert result_row[0] == "Test Player"
    assert result_row[1] is not None


def test_player_game_log_has_no_advanced_columns_without_the_flag(tmp_path: Path) -> None:
    data_dir = tmp_path / "parquet"
    fixtures: dict[str, dict] = {
        "teams": {"team_id": "1", "abbreviation": "BOS"},
        "players": {"athlete_id": "10", "display_name": "Test Player"},
        "games": {"event_id": "100", "date": "2024-01-01"},
        "player_box_stats": _ADVANCED_STATS_PLAYER_BOX_ROW,
    }
    for table, row in fixtures.items():
        _write_table_fixture(data_dir, table, row)
    db_path = tmp_path / "test.duckdb"

    warehouse.build(data_dir, db_path)  # no --advanced-stats

    con = duckdb.connect(str(db_path))
    cols = {r[0] for r in con.execute("DESCRIBE player_game_log").fetchall()}
    con.close()
    assert "ts_pct" not in cols


def test_build_with_tables_subset_only_loads_requested_tables(tmp_path: Path) -> None:
    data_dir = tmp_path / "parquet"
    _write_table_fixture(data_dir, "teams", {"team_id": "1", "abbreviation": "BOS"})
    _write_table_fixture(data_dir, "games", {"event_id": "100", "date": "2024-01-01"})
    db_path = tmp_path / "test.duckdb"

    warehouse.build(data_dir, db_path, tables=["teams"])

    con = duckdb.connect(str(db_path))
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    con.close()
    assert tables == {"teams"}


def test_build_with_unknown_table_raises(tmp_path: Path) -> None:
    data_dir = tmp_path / "parquet"
    db_path = tmp_path / "test.duckdb"
    with pytest.raises(ValueError, match="not_a_real_table"):
        warehouse.build(data_dir, db_path, tables=["not_a_real_table"])


def test_partial_reload_still_builds_views_from_previously_loaded_tables(tmp_path: Path) -> None:
    """Regression-shaped: reloading just player_box_stats after teams/players/
    games were already loaded in a prior full build must still (re)build
    player_game_log - the view's dependency check has to look at what's
    actually in the DB, not just what this particular call loaded."""
    data_dir = tmp_path / "parquet"
    fixtures: dict[str, dict] = {
        "teams": {"team_id": "1", "abbreviation": "BOS"},
        "players": {"athlete_id": "10", "display_name": "Test Player"},
        "games": {"event_id": "100", "date": "2024-01-01"},
        "player_box_stats": {"event_id": "100", "athlete_id": "10", "team_id": "1", "opponent_team_id": "1", "points": 20},
    }
    for table, row in fixtures.items():
        _write_table_fixture(data_dir, table, row)
    db_path = tmp_path / "test.duckdb"

    warehouse.build(data_dir, db_path)  # full build first

    # Now overwrite just player_box_stats on disk and reload only that table.
    (data_dir / "player_box_stats" / "f.parquet").unlink()
    _write_table_fixture(data_dir, "player_box_stats", dict(fixtures["player_box_stats"], points=99))
    warehouse.build(data_dir, db_path, tables=["player_box_stats"])

    con = duckdb.connect(str(db_path))
    result = con.execute("SELECT player_name, points FROM player_game_log").fetchall()
    con.close()
    assert result == [("Test Player", 99)]


def test_advanced_stats_flag_false_leaves_existing_views_alone(tmp_path: Path) -> None:
    """A partial reload that doesn't pass --advanced-stats shouldn't silently
    tear down advanced-stats views a prior build already created."""
    data_dir = tmp_path / "parquet"
    _write_table_fixture(
        data_dir,
        "player_box_stats",
        {
            "event_id": "1", "season": 2024, "season_type": 2, "team_id": "1", "athlete_id": "10",
            "minutes": 36, "points": 30, "fieldGoalsMade": 10, "fieldGoalsAttempted": 20,
            "threePointFieldGoalsMade": 2, "freeThrowsMade": 8, "freeThrowsAttempted": 10,
            "offensiveRebounds": 1, "defensiveRebounds": 4, "assists": 5, "steals": 2, "blocks": 1,
            "fouls": 3, "turnovers": 3,
        },
    )
    db_path = tmp_path / "test.duckdb"

    warehouse.build(data_dir, db_path, include_advanced_stats=True)
    warehouse.build(data_dir, db_path, tables=["player_box_stats"])  # no --advanced-stats this time

    con = duckdb.connect(str(db_path))
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    con.close()
    assert "player_advanced_stats" in tables
