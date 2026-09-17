#!/usr/bin/env python3
"""Re-fetch every power-index snapshot ESPN holds, not just the first page.

    python scripts/backfill_power_index.py --dry-run
    python scripts/backfill_power_index.py --data-dir ./data/parquet --db-path ./nba.duckdb

`fetch_power_index` read one page of a paged collection. ESPN's core API answers
`seasons/<s>/powerindex` with `{count, pageIndex, pageSize: 25, pageCount,
items}`, so every season stored ESPN's first 25 rows and dropped the rest -
2024 kept 25 rows covering 9 of 30 teams. Nothing errored: a short page and a
short dataset look identical, which is why `DATA.md` recorded the missing rows
as ESPN "keeping only postseason teams".

Measured live before this was written, ESPN holds 630 rows over 2017-2026
against the 250 on disk: 30 teams in every snapshot, with preseason+regular in
2017-18, regular only in 2019-21, regular+postseason in 2022, and
regular+postseason+play-in from 2023.

Runs the real code path - `Pipeline.fetch_power_index`, which now reads through
`ESPNClient.get_collection`, writes through `_write_rows`, and reloads
`team_power_index` with `warehouse.build`. `force=True` is required: the fetch
skips a past season whose file already exists, which is every season here.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import duckdb

from association.cli.paths import default_data_dir, default_db_path
from association.fetch import warehouse
from association.fetch.client import ESPNClient
from association.fetch.pipeline import Pipeline

log = logging.getLogger("backfill_power_index")

#: Seasons ESPN publishes a power index for. 2017 is the first.
FIRST_SEASON = 2017


def snapshot_counts(db_path: Path) -> list[tuple[int, int, int]]:
    """(season, rows, distinct teams) per season, read-only."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return [(int(season), int(rows), int(teams)) for season, rows, teams in con.execute("SELECT season, count(*), count(DISTINCT team_id) FROM team_power_index GROUP BY 1 ORDER BY 1").fetchall()]
    finally:
        con.close()


def main() -> int:
    """Re-fetch the affected seasons and reload the table."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=default_data_dir(), help="Parquet flat-file root")
    parser.add_argument("--db-path", default=default_db_path(), help="DuckDB warehouse file")
    parser.add_argument("--first-season", type=int, default=FIRST_SEASON)
    parser.add_argument("--last-season", type=int, default=None, help="Defaults to the newest season on disk")
    parser.add_argument("--rate-limit", type=float, default=5.0)
    parser.add_argument("--dry-run", action="store_true", help="Report what is on disk and stop")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db_path, data_dir = Path(args.db_path), Path(args.data_dir)
    if not db_path.exists():
        print(f"error: no warehouse at {db_path}", file=sys.stderr)
        return 1

    before = snapshot_counts(db_path)
    last = args.last_season or (before[-1][0] if before else args.first_season)
    seasons = list(range(args.first_season, last + 1))
    print("before: " + ", ".join(f"{s}={rows}r/{teams}t" for s, rows, teams in before))
    if args.dry_run:
        print(f"would re-fetch {len(seasons)} seasons: {seasons[0]}-{seasons[-1]}")
        return 0

    # force=True, or every past season short-circuits on its existing file.
    pipeline = Pipeline(ESPNClient(rate_limit=args.rate_limit), data_dir, force=True)
    for season in seasons:
        pipeline.fetch_power_index(season)
    pipeline.write_glossary()

    if "team_power_index" not in pipeline.written:
        print("nothing was written - leaving the warehouse alone")
        return 0
    warehouse.build(data_dir, db_path, tables=sorted(pipeline.written))
    after = snapshot_counts(db_path)
    print("after:  " + ", ".join(f"{s}={rows}r/{teams}t" for s, rows, teams in after))
    print(f"rows: {sum(r for _, r, _ in before)} -> {sum(r for _, r, _ in after)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
