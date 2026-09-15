#!/usr/bin/env python3
"""Backfill the season lines ESPN's career endpoint served with no totals.

    python scripts/backfill_season_totals.py --dry-run
    python scripts/backfill_season_totals.py --data-dir ./data/parquet --db-path ./nba.duckdb

246 rows in `player_season_stats` carry `avgPoints` beside a NULL `points`
(see DATA.md, "The career endpoint drops its totals category, unpredictably
and in part"). The Parquet on disk was written by the old code from a payload
that had no totals in it, so a `data load` alone changes nothing - the
affected files have to be fetched again, through the pull's own repair.

This re-fetches only those files rather than forcing whole seasons: the career
endpoint is keyed by (athlete, season_type), and 107 of them cover all 246
rows against 4,830 untouched. It runs the real code path - `Pipeline.
fetch_player_season_stats`, which writes through `_write_rows` - and then
reloads `player_season_stats` with `warehouse.build`, so nothing here can
drift from what a normal pull produces.

Re-runnable: a file whose line is repaired stops being selected.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import duckdb

from association.fetch import warehouse
from association.fetch.client import ESPNClient
from association.fetch.pipeline import Pipeline

log = logging.getLogger("backfill_season_totals")

NULL_TOTALS_SQL = """
    SELECT DISTINCT athlete_id, season_type
    FROM player_season_stats
    WHERE points IS NULL AND gamesPlayed > 0
    ORDER BY athlete_id, season_type
"""


def affected_files(db_path: Path) -> list[tuple[str, int]]:
    """Every (athlete_id, season_type) career file holding a totals-less line.

    Read-only, and closed before anything writes: a `warehouse.build` on the
    same file needs the write lock this would otherwise hold.
    """
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(NULL_TOTALS_SQL).fetchall()
    finally:
        con.close()
    return [(str(athlete_id), int(season_type)) for athlete_id, season_type in rows]


def null_total_rows(db_path: Path) -> int:
    """How many season lines still have no totals."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute("SELECT count(*) FROM player_season_stats WHERE points IS NULL").fetchone()
    finally:
        con.close()
    return int(row[0]) if row else 0


def main() -> int:
    """Re-fetch the affected career files and reload the table."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default="./data/parquet", help="Parquet flat-file root")
    parser.add_argument("--db-path", default="./nba.duckdb", help="DuckDB warehouse file")
    parser.add_argument("--rate-limit", type=float, default=5.0, help="Max requests/second against ESPN")
    parser.add_argument("--workers", type=int, default=4, help="How many requests to keep in flight")
    parser.add_argument("--dry-run", action="store_true", help="List the files that would be re-fetched, and stop")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db_path, data_dir = Path(args.db_path), Path(args.data_dir)
    if not db_path.exists():
        print(f"error: no warehouse at {db_path}", file=sys.stderr)
        return 1

    files = affected_files(db_path)
    before = null_total_rows(db_path)
    print(f"{before} season lines have no totals, across {len(files)} career files")
    if args.dry_run:
        for athlete_id, season_type in files[:20]:
            print(f"  would re-fetch athlete {athlete_id}, season_type {season_type}")
        if len(files) > 20:
            print(f"  ... and {len(files) - 20} more")
        return 0
    if not files:
        return 0

    pipeline = Pipeline(ESPNClient(rate_limit=args.rate_limit), data_dir, workers=args.workers)
    # force_refresh, not Pipeline(force=True): this must re-fetch exactly these
    # files and leave every other checkpoint in the tree alone.
    pipeline._map(
        lambda item: pipeline.fetch_player_season_stats(item[0], item[1], force_refresh=True),
        files,
        desc="season totals",
    )
    pipeline.write_glossary()

    if "player_season_stats" not in pipeline.written:
        print("nothing was written - leaving the warehouse alone")
        return 0
    warehouse.build(data_dir, db_path, tables=["player_season_stats"])
    after = null_total_rows(db_path)
    print(f"season lines with no totals: {before} -> {after}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
