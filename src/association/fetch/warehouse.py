"""Builds/refreshes the DuckDB analytics warehouse from the Parquet flat-file tree.

Idempotent and cheap - safe to re-run any time after a fetch. Each table is
loaded straight from its slice of the Parquet tree; union_by_name handles
minor schema drift between files (e.g. a stat that's absent in some games).
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

from . import advanced_stats

log = logging.getLogger("association.fetch.warehouse")

# Every row already embeds its own season/season_type/team_id columns (set in
# association.fetch.parse), so we read raw - not hive-partitioned - to avoid
# duckdb inferring a second, differently-typed copy of those columns from the
# season=X/season_type=Y directory names used purely for file organization.
# ("_complete" holds pipeline completion markers, not data - never a table.)

TABLES = [
    "teams",
    "players",
    "games",
    "player_box_stats",
    "team_box_stats",
    "player_season_stats",
    "team_season_stats",
    "standings",
    "team_power_index",
    "plays",
    "shot_chart",
    "win_probability",
    "stat_glossary",
]


def _has_parquet(dir_path: Path) -> bool:
    return dir_path.exists() and any(dir_path.rglob("*.parquet"))


def build(data_dir: Path, db_path: Path, include_advanced_stats: bool = False) -> None:
    data_dir = Path(data_dir)
    con = duckdb.connect(str(db_path))
    try:
        loaded = set()
        for table in TABLES:
            table_dir = data_dir / table
            if not _has_parquet(table_dir):
                log.info("skip %s (no parquet files yet)", table)
                continue
            glob = str(table_dir / "**" / "*.parquet")
            con.execute(
                f"CREATE OR REPLACE TABLE {table} AS "
                "SELECT * FROM read_parquet(?, hive_partitioning=false, union_by_name=true)",
                [glob],
            )
            count_row = con.execute(f"SELECT count(*) FROM {table}").fetchone()
            assert count_row is not None  # COUNT(*) always returns exactly one row
            count = count_row[0]
            log.info("%s: %d rows", table, count)
            loaded.add(table)

        _build_views(con, loaded)
        if include_advanced_stats:
            advanced_stats.build_views(con, loaded)
        else:
            advanced_stats.drop_views(con)
    finally:
        con.close()


def _build_views(con: duckdb.DuckDBPyConnection, loaded: set[str]) -> None:
    if {"player_box_stats", "players", "games", "teams"} <= loaded:
        con.execute(
            """
            CREATE OR REPLACE VIEW player_game_log AS
            SELECT
                pbs.*,
                p.display_name AS player_name,
                g.date AS game_date,
                t.abbreviation AS team_abbr,
                o.abbreviation AS opponent_abbr
            FROM player_box_stats pbs
            LEFT JOIN players p ON p.athlete_id = pbs.athlete_id
            LEFT JOIN games g ON g.event_id = pbs.event_id
            LEFT JOIN teams t ON t.team_id = pbs.team_id
            LEFT JOIN teams o ON o.team_id = pbs.opponent_team_id
            """
        )
