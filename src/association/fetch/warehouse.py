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

log: logging.Logger = logging.getLogger("association.fetch.warehouse")

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
    "net_points_player",
    "net_points_player_fingerprint",
    "net_points_team",
    "net_points_player_game",
    "net_points_team_game",
    "stat_glossary",
]


def _has_parquet(dir_path: Path) -> bool:
    return dir_path.exists() and any(dir_path.rglob("*.parquet"))


def _existing_tables(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()
    return {r[0] for r in rows}


def build(data_dir: Path, db_path: Path, tables: list[str] | None = None) -> None:
    """(Re)build the warehouse. tables=None rebuilds every known table; a
    subset only reloads those, leaving any other already-loaded table (and
    tables/views that depend on it) untouched - the `association data load`
    path for refreshing part of a large warehouse without rescanning
    everything. The computed advanced-stats views (player_advanced_stats,
    player_season_advanced_stats) are always (re)built when player_box_stats
    is present - they're pure closed-form formulas over already-fetched
    columns (see fetch/advanced_stats.py), no extra fetch or meaningful
    storage cost, so there's no real reason to make that a decision the
    caller has to opt into every time."""
    if tables is not None:
        unknown = sorted(set(tables) - set(TABLES))
        if unknown:
            raise ValueError(f"Unknown table(s): {', '.join(unknown)}. Known tables: {', '.join(TABLES)}")
    target_tables = TABLES if tables is None else [t for t in TABLES if t in tables]

    data_dir = Path(data_dir)
    con = duckdb.connect(str(db_path))
    try:
        _build_macros(con)
        for table in target_tables:
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

        existing = _existing_tables(con)
        advanced_stats.build_views(con, existing)
        existing = _existing_tables(con)  # refresh so player_game_log can join the views just created
        _build_views(con, existing)
    finally:
        con.close()


def _build_macros(con: duckdb.DuckDBPyConnection) -> None:
    """Schema-level helpers for ad hoc SQL (the query agent's run_sql escape
    hatch, or a human at a duckdb prompt) - the same correctness rules
    get_leaderboard applies in Python for its own fixed set of metrics, baked
    in here instead so a query outside that fixed set doesn't have to
    re-derive them from prose. Depend on no table, so built unconditionally,
    before any table load - safe to call even against an empty database."""
    con.execute(
        """
        CREATE OR REPLACE MACRO current_season() AS (
            CASE WHEN EXTRACT(MONTH FROM CURRENT_DATE) >= 10
                 THEN EXTRACT(YEAR FROM CURRENT_DATE) + 1
                 ELSE EXTRACT(YEAR FROM CURRENT_DATE) END
        )
        """
    )


def _build_views(con: duckdb.DuckDBPyConnection, loaded: set[str]) -> None:
    if "player_season_stats" in loaded:
        con.execute(
            """
            CREATE OR REPLACE VIEW player_season_stats_deduped AS
            SELECT * FROM player_season_stats
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY athlete_id, season, season_type ORDER BY (team_id IS NULL) DESC
            ) = 1
            """
        )

    if not ({"player_box_stats", "players", "games", "teams"} <= loaded):
        return

    # player_advanced_stats may still not exist - e.g. player_box_stats wasn't
    # loaded at all yet, or this is an older database from before advanced
    # stats became unconditional. `loaded` reflects the DB's actual current
    # state, not just this call, so this stays correct across a partial
    # `data load` too.
    has_advanced = "player_advanced_stats" in loaded
    advanced_select = ", pas.ts_pct, pas.efg_pct, pas.usage_pct, pas.game_score" if has_advanced else ""
    advanced_join = (
        "LEFT JOIN player_advanced_stats pas ON pas.event_id = pbs.event_id AND pas.athlete_id = pbs.athlete_id"
        if has_advanced
        else ""
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW player_game_log AS
        SELECT
            pbs.*,
            p.display_name AS player_name,
            g.date AS game_date,
            t.abbreviation AS team_abbr,
            o.abbreviation AS opponent_abbr
            {advanced_select}
        FROM player_box_stats pbs
        LEFT JOIN players p ON p.athlete_id = pbs.athlete_id
        LEFT JOIN games g ON g.event_id = pbs.event_id
        LEFT JOIN teams t ON t.team_id = pbs.team_id
        LEFT JOIN teams o ON o.team_id = pbs.opponent_team_id
        {advanced_join}
        """
    )
