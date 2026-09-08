"""Sanity tests for the computed advanced-stats views: hand-verified formula
outputs against a small, fully-controlled box score."""

from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from association.fetch import warehouse


def _write_player_box_stats(data_dir: Path, rows: list[dict]) -> None:
    d = data_dir / "player_box_stats"
    d.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), d / "f.parquet")


# One game, one team: player A takes the whole floor-share, player B is the
# only other player on the team so team totals are easy to hand-check.
_PLAYER_A = {
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
    "threePointFieldGoalsAttempted": 5,
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
_PLAYER_B = {
    "event_id": "1",
    "season": 2024,
    "season_type": 2,
    "team_id": "1",
    "athlete_id": "11",
    "minutes": 204,
    "points": 10,
    "fieldGoalsMade": 4,
    "fieldGoalsAttempted": 10,
    "threePointFieldGoalsMade": 0,
    "threePointFieldGoalsAttempted": 0,
    "freeThrowsMade": 2,
    "freeThrowsAttempted": 2,
    "offensiveRebounds": 2,
    "defensiveRebounds": 6,
    "assists": 3,
    "steals": 0,
    "blocks": 0,
    "fouls": 1,
    "turnovers": 1,
}


def test_advanced_stats_views_always_built(tmp_path: Path) -> None:
    """No --advanced-stats opt-in anymore - these views cost nothing beyond a
    CREATE VIEW over data already fetched, so a plain build (with all the
    columns the formulas need) always produces them."""
    data_dir = tmp_path / "parquet"
    _write_player_box_stats(data_dir, [_PLAYER_A, _PLAYER_B])
    db_path = tmp_path / "test.duckdb"

    warehouse.build(data_dir, db_path)

    con = duckdb.connect(str(db_path))
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    con.close()
    assert "player_advanced_stats" in tables


def test_advanced_stats_views_skipped_when_a_required_column_is_missing(tmp_path: Path) -> None:
    """Regression: build_views() now always runs (no --advanced-stats opt-in
    isolating it), so a minimal/partial player_box_stats must not crash the
    WHOLE warehouse build - it should just skip these two views, the same as
    player_box_stats being absent entirely already did."""
    data_dir = tmp_path / "parquet"
    _write_player_box_stats(data_dir, [{"event_id": "1", "athlete_id": "10", "points": 20}])  # no minutes, FGA, etc.
    db_path = tmp_path / "test.duckdb"

    warehouse.build(data_dir, db_path)  # must not raise

    con = duckdb.connect(str(db_path))
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    con.close()
    assert "player_advanced_stats" not in tables


def test_ts_pct_and_efg_pct_hand_computed(tmp_path: Path) -> None:
    data_dir = tmp_path / "parquet"
    _write_player_box_stats(data_dir, [_PLAYER_A, _PLAYER_B])
    db_path = tmp_path / "test.duckdb"

    warehouse.build(data_dir, db_path)

    con = duckdb.connect(str(db_path))
    row = con.execute("SELECT ts_pct, efg_pct, usage_pct, game_score FROM player_advanced_stats WHERE athlete_id = '10'").fetchone()
    con.close()
    assert row is not None
    ts_pct, efg_pct, usage_pct, game_score = row

    # TS% = PTS / (2 * (FGA + 0.44*FTA)) = 30 / (2 * (20 + 4.4)) = 30 / 48.8
    assert ts_pct == pytest.approx(30 / 48.8)
    # eFG% = (FGM + 0.5*3PM) / FGA = (10 + 1) / 20
    assert efg_pct == pytest.approx(11 / 20)
    # Team totals: minutes=240, FGA=30, FTA=12, TOV=4
    # USG% = 100 * ((20 + 4.4 + 3) * (240/5)) / (36 * (30 + 5.28 + 4))
    expected_usage = 100 * ((20 + 0.44 * 10 + 3) * (240 / 5)) / (36 * (30 + 0.44 * 12 + 4))
    assert usage_pct == pytest.approx(expected_usage)
    # Game score = 30 + 0.4*10 - 0.7*20 - 0.4*(10-8) + 0.7*1 + 0.3*4 + 2 + 0.7*5 + 0.7*1 - 0.4*3 - 3
    expected_gs = 30 + 0.4 * 10 - 0.7 * 20 - 0.4 * (10 - 8) + 0.7 * 1 + 0.3 * 4 + 2 + 0.7 * 5 + 0.7 * 1 - 0.4 * 3 - 3
    assert game_score == pytest.approx(expected_gs)


def test_season_view_aggregates_totals_not_average_of_ratios(tmp_path: Path) -> None:
    """Regression-shaped: a second game with a different attempt volume must
    change season TS% by summing totals first, not by averaging two
    per-game ratios (which would ignore volume)."""
    data_dir = tmp_path / "parquet"
    game_2 = dict(_PLAYER_A, event_id="2", points=6, fieldGoalsMade=2, fieldGoalsAttempted=4, freeThrowsMade=2, freeThrowsAttempted=2)
    game_2_teammate = dict(_PLAYER_B, event_id="2")
    _write_player_box_stats(data_dir, [_PLAYER_A, _PLAYER_B, game_2, game_2_teammate])
    db_path = tmp_path / "test.duckdb"

    warehouse.build(data_dir, db_path)

    con = duckdb.connect(str(db_path))
    row = con.execute("SELECT games_played, ts_pct FROM player_season_advanced_stats WHERE athlete_id = '10'").fetchone()
    con.close()
    assert row is not None
    games_played, ts_pct = row
    assert games_played == 2
    # season TS% = total PTS / (2 * (total FGA + 0.44 * total FTA)) = 36 / (2*(24 + 0.44*12))
    assert ts_pct == pytest.approx(36 / (2 * (24 + 0.44 * 12)))
