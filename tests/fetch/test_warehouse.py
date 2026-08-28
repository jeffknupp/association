"""Regression + sanity tests for the DuckDB warehouse builder."""

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from association.fetch import warehouse


def test_build_ignores_directory_names_uses_embedded_columns(tmp_path):
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


def test_build_merges_files_with_differing_column_types(tmp_path):
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


def test_build_skips_tables_with_no_parquet_files(tmp_path):
    data_dir = tmp_path / "parquet"
    data_dir.mkdir()
    db_path = tmp_path / "empty.duckdb"
    warehouse.build(data_dir, db_path)  # must not raise

    con = duckdb.connect(str(db_path))
    tables = con.execute("SHOW TABLES").fetchall()
    con.close()
    assert tables == []


def test_build_creates_player_game_log_view_when_dependencies_present(tmp_path):
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
