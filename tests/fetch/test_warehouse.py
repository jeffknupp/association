"""Regression + sanity tests for the DuckDB warehouse builder."""

from pathlib import Path
from typing import Any

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


def test_build_creates_current_season_macro_unconditionally(tmp_path: Path) -> None:
    """current_season() is schema-level, not tied to any table - a raw run_sql
    query should be able to call it even against a freshly-created, otherwise
    empty warehouse, the same as before any table has ever been loaded."""
    import datetime

    data_dir = tmp_path / "parquet"
    data_dir.mkdir()
    db_path = tmp_path / "empty.duckdb"
    warehouse.build(data_dir, db_path)

    con = duckdb.connect(str(db_path))
    result = con.execute("SELECT current_season()").fetchone()
    con.close()

    today = datetime.date.today()
    expected = today.year + 1 if today.month >= 10 else today.year
    assert result == (expected,)


def test_build_creates_player_season_stats_deduped_view(tmp_path: Path) -> None:
    """Regression-shaped: a traded player has one player_season_stats row per
    team stint plus a combined row (team_id IS NULL) - ad hoc SQL that forgets
    the QUALIFY dedup pattern double/triple-counts them. This view bakes that
    pattern in once so run_sql doesn't have to re-derive it per query."""
    data_dir = tmp_path / "parquet"
    _write_table_fixture(data_dir, "players", {"athlete_id": "1", "display_name": "Traded Player"})
    d = data_dir / "player_season_stats"
    d.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"season": 2026, "season_type": 2, "athlete_id": "1", "team_id": "9", "avgPoints": 15.0},
                {"season": 2026, "season_type": 2, "athlete_id": "1", "team_id": "20", "avgPoints": 12.0},
                {"season": 2026, "season_type": 2, "athlete_id": "1", "team_id": None, "avgPoints": 13.8},
            ]
        ),
        d / "f.parquet",
    )
    db_path = tmp_path / "test.duckdb"
    warehouse.build(data_dir, db_path)

    con = duckdb.connect(str(db_path))
    rows = con.execute("SELECT athlete_id, avgPoints FROM player_season_stats_deduped").fetchall()
    con.close()
    assert rows == [("1", 13.8)]


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
    "event_id": "100",
    "season": 2024,
    "season_type": 2,
    "team_id": "1",
    "opponent_team_id": "1",
    "athlete_id": "10",
    "minutes": 36,
    "points": 30,
    "fieldGoalsMade": 10,
    "fieldGoalsAttempted": 20,
    "threePointFieldGoalsMade": 2,
    "freeThrowsMade": 8,
    "freeThrowsAttempted": 10,
    "offensiveRebounds": 1,
    "defensiveRebounds": 4,
    "assists": 5,
    "steals": 2,
    "blocks": 1,
    "fouls": 3,
    "turnovers": 3,
}


def test_player_game_log_gains_advanced_columns(tmp_path: Path) -> None:
    """Regression: a model asking for a single game's ts_pct naturally tried
    player_game_log first (the documented per-game convenience view) and got
    told the column didn't exist there - player_game_log never joined
    player_advanced_stats at all. No --advanced-stats opt-in anymore - these
    columns are there on a plain build whenever player_box_stats has what
    the formulas need."""
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

    warehouse.build(data_dir, db_path)

    con = duckdb.connect(str(db_path))
    result_row = con.execute("SELECT player_name, ts_pct FROM player_game_log WHERE event_id = '100'").fetchone()
    con.close()
    assert result_row is not None
    assert result_row[0] == "Test Player"
    assert result_row[1] is not None


def test_player_game_log_has_no_advanced_columns_when_box_stats_incomplete(tmp_path: Path) -> None:
    """player_advanced_stats is skipped (not crashed) when player_box_stats is
    missing a column the formulas need - player_game_log's LEFT JOIN to it
    should correctly reflect that by not gaining the advanced columns."""
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

    warehouse.build(data_dir, db_path)

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


def test_advanced_stats_views_survive_a_partial_reload(tmp_path: Path) -> None:
    """A partial `--tables player_box_stats` reload should still (re)build the
    advanced-stats views rather than tearing them down or leaving them stale -
    no flag to forget to repeat anymore, they're just always kept current."""
    data_dir = tmp_path / "parquet"
    _write_table_fixture(
        data_dir,
        "player_box_stats",
        {
            "event_id": "1",
            "season": 2024,
            "season_type": 2,
            "team_id": "1",
            "athlete_id": "10",
            "minutes": 36,
            "points": 30,
            "fieldGoalsMade": 10,
            "fieldGoalsAttempted": 20,
            "threePointFieldGoalsMade": 2,
            "freeThrowsMade": 8,
            "freeThrowsAttempted": 10,
            "offensiveRebounds": 1,
            "defensiveRebounds": 4,
            "assists": 5,
            "steals": 2,
            "blocks": 1,
            "fouls": 3,
            "turnovers": 3,
        },
    )
    db_path = tmp_path / "test.duckdb"

    warehouse.build(data_dir, db_path)
    warehouse.build(data_dir, db_path, tables=["player_box_stats"])  # partial reload

    con = duckdb.connect(str(db_path))
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    con.close()
    assert "player_advanced_stats" in tables


def test_build_loads_net_points_tables(tmp_path: Path) -> None:
    data_dir = tmp_path / "parquet"
    d1 = data_dir / "net_points_player" / "season=2026"
    d2 = data_dir / "net_points_team" / "season=2026"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist([{"athlete_id": 1, "season": 2026, "net_points_season_type": "Regular Season", "overall": 1.5}]),
        d1 / "regular_season.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist([{"team_id": "1", "season": 2026, "side": "Total", "total": 2.5}]),
        d2 / "net_points_team.parquet",
    )

    db_path = tmp_path / "test.duckdb"
    warehouse.build(data_dir, db_path)

    con = duckdb.connect(str(db_path))
    player_row = con.execute("SELECT overall FROM net_points_player").fetchone()
    team_row = con.execute("SELECT total FROM net_points_team").fetchone()
    con.close()
    assert player_row == (1.5,)
    assert team_row == (2.5,)


def test_build_loads_net_points_daily_tables(tmp_path: Path) -> None:
    data_dir = tmp_path / "parquet"
    d1 = data_dir / "net_points_player_game" / "season=2026"
    d2 = data_dir / "net_points_team_game" / "season=2026"
    d1.mkdir(parents=True)
    d2.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist([{"event_id": "1", "season": 2026, "season_type": 2, "athlete_id": "9", "t_net_pts": 3.0}]),
        d1 / "date=2026-04-12.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist([{"event_id": "1", "season": 2026, "season_type": 2, "team_id": "18", "net_pts_2pt": 1.0}]),
        d2 / "date=2026-04-12.parquet",
    )

    db_path = tmp_path / "test.duckdb"
    warehouse.build(data_dir, db_path)

    con = duckdb.connect(str(db_path))
    player_row = con.execute("SELECT t_net_pts FROM net_points_player_game").fetchone()
    team_row = con.execute("SELECT net_pts_2pt FROM net_points_team_game").fetchone()
    con.close()
    assert player_row == (3.0,)
    assert team_row == (1.0,)


def test_build_loads_through_a_connection_that_does_not_preserve_insertion_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A full rebuild of `plays` from 17,500 Parquet files died with "could not
    allocate block of size 32.0 KiB (12.4 GiB/12.4 GiB used)" until this was
    turned off - and because each table is its own statement, the rebuild
    aborted with the earlier tables already replaced and the rest left at their
    old contents. Row order carries no meaning in any of these tables.

    Asserted on the connection `build` itself loads through: the setting is
    per-session, so a fresh connection opened afterwards would report the
    default no matter what the build did.
    """
    data_dir = tmp_path / "parquet"
    d = data_dir / "teams"
    d.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([{"team_id": "1", "abbreviation": "BOS"}]), d / "teams.parquet")

    seen: list[Any] = []
    real_tune = warehouse._tune

    def spy(con: duckdb.DuckDBPyConnection) -> None:
        real_tune(con)
        seen.append(con.execute("SELECT current_setting('preserve_insertion_order')").fetchone())

    monkeypatch.setattr(warehouse, "_tune", spy)
    warehouse.build(data_dir, tmp_path / "w.duckdb")

    assert seen == [(False,)]
