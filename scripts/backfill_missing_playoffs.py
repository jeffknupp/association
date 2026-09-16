#!/usr/bin/env python3
"""Backfill the playoff games ESPN's team schedules never list.

    python scripts/backfill_missing_playoffs.py --dry-run
    python scripts/backfill_missing_playoffs.py --data-dir ./data/parquet --db-path ./nba.duckdb

The 2000 postseason stops on 2000-06-01 and the 2001 on 2001-05-28, because
games are discovered from each team's schedule and ESPN's schedules simply end
(see DATA.md, "The 2000 and 2001 playoffs stop before the Finals"). Missing
from 2000: the whole LAL-IND Final, WCF Games 6-7 and ECF Game 6. From 2001:
Game 5 of the Final.

ESPN's daily scoreboard is a second, independent list and it HAS those games -
`200607013` is Finals Game 1 - and their summaries carry full box scores.
A postseason pull now makes a second discovery pass over the scoreboard once
the schedule's games are on disk, so a fresh pull produces these by itself;
this script is for a tree that was already pulled, whose `_complete` markers
would otherwise skip the season entirely.

It runs the real code path - the same schedule discovery, the same scoreboard
scan and the same `fetch_game` write - so nothing here can drift from what a
normal pull does.
Re-runnable: a game already on disk is skipped by `fetch_game` itself.

**2001 stays incomplete afterwards, and that is ESPN's gap, not a bug here.**
The scoreboard has one of its ~11 missing games; 23 days across the conference
finals and the Final return nothing at all. `coverage.postseason_partial`
declares that season partial so answers say so.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import duckdb

from association.coverage import POSTSEASON
from association.fetch import warehouse
from association.fetch.client import ESPNClient
from association.fetch.pipeline import Pipeline
from association.repo_paths import default_data_dir, default_db_path

log = logging.getLogger("backfill_missing_playoffs")

# The two seasons whose schedules end early. Others are scanned by every pull
# now; naming these keeps the backfill's cost to what it has to be.
AFFECTED_SEASONS = (2000, 2001)


def postseason_counts(db_path: Path, seasons: tuple[int, ...]) -> dict[int, int]:
    """Games held per postseason, read-only and closed before anything writes."""
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        placeholders = ", ".join("?" for _ in seasons)
        rows = con.execute(
            f"SELECT season, count(*) FROM games WHERE season_type = {POSTSEASON} AND season IN ({placeholders}) GROUP BY 1 ORDER BY 1",
            list(seasons),
        ).fetchall()
    finally:
        con.close()
    return {int(season): int(count) for season, count in rows}


def main() -> int:
    """Discover and fetch the missing playoff games, then reload the tables."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=default_data_dir(), help="Parquet flat-file root")
    parser.add_argument("--db-path", default=default_db_path(), help="DuckDB warehouse file")
    parser.add_argument("--seasons", default=",".join(str(s) for s in AFFECTED_SEASONS), help="Comma-separated seasons to scan")
    parser.add_argument("--rate-limit", type=float, default=5.0, help="Max requests/second against ESPN")
    parser.add_argument("--workers", type=int, default=4, help="How many requests to keep in flight")
    parser.add_argument("--dry-run", action="store_true", help="List the games that would be fetched, and stop")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    db_path, data_dir = Path(args.db_path), Path(args.data_dir)
    if not db_path.exists():
        print(f"error: no warehouse at {db_path}", file=sys.stderr)
        return 1
    seasons = tuple(int(s) for s in args.seasons.split(",") if s.strip())

    before = postseason_counts(db_path, seasons)
    pipeline = Pipeline(ESPNClient(rate_limit=args.rate_limit), data_dir, workers=args.workers)
    team_ids = pipeline.team_ids()

    missing: list[tuple[str, int]] = []
    for season in seasons:
        # The same discovery a pull runs, so what this fetches is exactly what
        # a pull would. The scoreboard half is what finds the missing games.
        for event_id in pipeline.event_ids_for(season, POSTSEASON, team_ids):
            if not (data_dir / "games" / f"season={season}" / f"season_type={POSTSEASON}" / f"event_{event_id}.parquet").exists():
                missing.append((event_id, season))

    print(f"postseason games held: {', '.join(f'{s}={before.get(s, 0)}' for s in seasons)}")
    print(f"{len(missing)} game(s) discovered that are not on disk")
    if args.dry_run:
        for event_id, season in missing:
            print(f"  would fetch {event_id} ({season} postseason)")
        return 0
    if not missing:
        return 0

    pipeline._map(lambda item: pipeline.fetch_game(item[0], item[1], POSTSEASON), missing, desc="missing playoff games")
    pipeline.write_glossary()

    if not pipeline.written:
        print("nothing was written - leaving the warehouse alone")
        return 0
    warehouse.build(data_dir, db_path, tables=sorted(pipeline.written))
    after = postseason_counts(db_path, seasons)
    for season in seasons:
        print(f"{season} postseason: {before.get(season, 0)} -> {after.get(season, 0)} games")
    return 0


if __name__ == "__main__":
    sys.exit(main())
