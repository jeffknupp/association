#!/usr/bin/env python3
"""Backfill ESPN's playoff series fields onto the postseason games on disk.

    python scripts/backfill_series_games.py --dry-run
    python scripts/backfill_series_games.py --seasons 1989-2026 --data-dir ./data/parquet --db-path ./nba.duckdb

``games.series_game`` (ESPN's count of a game within its playoff series, the
summary's playoff series wins summed) and ``games.series_note`` (the
summary's note naming the round, "West 1st Round - Game 1", from 2009) are
read by the fetch path since they were added; a tree pulled before then has
neither, and its ``_complete`` markers skip every season a plain pull would
revisit. This re-fetches each postseason game already on disk - its summary
only, the play-by-play left as it is - and reloads what it wrote, so
``real_games`` takes ESPN's own numbers. Nothing a user sees depends on it:
the place-by-date numbering ``real_games`` falls back on agrees with ESPN's
count on all 3,006 postseason games its scoreboards list (``DATA.md``, "The
playoff round and the series' game number"); it is what lets
``scripts/check_playoff_rounds.py`` hold the bracket to ESPN's labels.

It runs the real code path - ``Pipeline.fetch_game`` with ``force``, the
same summary parse and the same write - and reloads through
``warehouse.build``. About 3,000 requests: ten minutes at the default rate.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from association.cli.paths import default_data_dir, default_db_path
from association.fetch import warehouse
from association.fetch.client import ESPNClient
from association.fetch.pipeline import Pipeline
from association.nba.coverage import POSTSEASON


def _seasons(text: str) -> list[int]:
    """``"1989-2026"`` or ``"2016,2025"`` as the seasons it names."""
    seasons: list[int] = []
    for part in text.split(","):
        first, _, last = part.strip().partition("-")
        seasons.extend(range(int(first), int(last or first) + 1))
    return seasons


def postseason_games_on_disk(data_dir: Path, seasons: list[int]) -> list[tuple[str, int]]:
    """Each postseason game a pull wrote, as ``(event_id, season)``."""
    games: list[tuple[str, int]] = []
    for season in seasons:
        folder = data_dir / "games" / f"season={season}" / f"season_type={POSTSEASON}"
        games.extend((path.stem.removeprefix("event_"), season) for path in sorted(folder.glob("event_*.parquet")))
    return games


def main() -> int:
    """Re-fetch the postseason summaries on disk, then reload the tables."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=default_data_dir(), help="Parquet flat-file root")
    parser.add_argument("--db-path", default=default_db_path(), help="DuckDB warehouse file")
    parser.add_argument("--seasons", default="1989-2026", help="Seasons to backfill: a range, a list, or both")
    parser.add_argument("--rate-limit", type=float, default=5.0, help="Max requests/second against ESPN")
    parser.add_argument("--workers", type=int, default=4, help="How many requests to keep in flight")
    parser.add_argument("--dry-run", action="store_true", help="Count the games that would be re-fetched, and stop")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    data_dir, db_path = Path(args.data_dir), Path(args.db_path)
    games = postseason_games_on_disk(data_dir, _seasons(args.seasons))
    print(f"{len(games)} postseason game(s) on disk to re-fetch")
    if args.dry_run or not games:
        return 0
    pipeline = Pipeline(ESPNClient(rate_limit=args.rate_limit), data_dir, force=True, workers=args.workers)
    pipeline._map(lambda item: pipeline.fetch_game(item[0], item[1], POSTSEASON), games, desc="postseason summaries")
    pipeline.write_glossary()
    if not pipeline.written:
        print("nothing was written - leaving the warehouse alone")
        return 0
    warehouse.build(data_dir, db_path, tables=sorted(pipeline.written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
