#!/usr/bin/env python3
"""Re-parse NetPoints' name-matched tables, without refetching ESPN.

    python scripts/backfill_netpoints_names.py --dry-run
    python scripts/backfill_netpoints_names.py --data-dir ./data/parquet --db-path ./nba.duckdb

NetPoints publishes a bare display name, not an athlete id (``DATA.md``,
"NetPoints publishes a display name, not a player id"), so every row it writes
is matched by name against the local ``players`` table. Two faults in that
matching were fixed in 4.1.0:

- an unmatched name was dropped rather than kept, leaving 2,190 rows in
  ``net_points_player_game`` and 64,210 in
  ``net_points_player_game_fingerprint`` with a NULL ``athlete_id`` and nothing
  on the row saying who they were (``ISSUES.md`` #22);
- a name shared by two athlete ids was dropped unconditionally, which silently
  cost every player ESPN files under two ids their entire per-game NetPoints
  history - Corey Brewer had 985 box-score rows from 2008 to 2020 and not one
  per-game NetPoints row (``ISSUES.md`` #101).

Both are PARSER fixes, so the Parquet already on disk was written by the old
code and a ``data load`` alone changes nothing: the files have to be fetched and
parsed again. ``association data pull --force`` does that, but it also refetches
every ESPN game summary for the seasons named - about 11,000 requests over the
NetPoints era, some 40 minutes at the default rate limit - none of which this
backfill needs, because the ESPN side is unchanged.

So this script runs the NetPoints steps of a pull and nothing else. It is the
same code path a pull takes - the same ``Pipeline`` fetch methods, the same
``_write_rows``, the same ``warehouse.build`` - so what it produces cannot drift
from what a normal pull would produce. What it deliberately skips:

- ESPN's teams, standings, power index, schedules, game summaries and box
  scores. All unchanged by the fix, and all already on disk.
- ``fetch_net_points``, the season-level flat file behind ``net_points_player``.
  That table is keyed by ESPN's own ``dot_com_id`` rather than matched by name,
  so neither fault touches it. Pass ``--include-season-file`` to refresh it
  anyway.

Prerequisites, checked before anything is fetched rather than discovered as an
empty result: ``teams``, ``games`` and ``players`` must already be on disk,
because the matchers resolve against them. A tree without them needs a normal
pull first.

Re-runnable, and worth re-running: the daily files' markers are bypassed here
(that is the point), and ``AGENTS.md`` records that a backfill over this
endpoint should be repeated until the count stops falling rather than trusted
after one pass.
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
from association.nba.season import current_season

log = logging.getLogger("backfill_netpoints_names")

#: The tables this backfill can change, and the ones worth counting before and
#: after. ``net_points_team_game`` is written by the same daily parser but
#: carries no athlete id, so it has nothing to report.
NAME_MATCHED_TABLES = ("net_points_player_game", "net_points_player_game_fingerprint", "net_points_player_fingerprint")

#: The parquet trees the name matchers read. Absent, the fetch would run and
#: resolve nothing, writing rows no better than the ones it replaced.
REQUIRED_TREES = ("teams", "games", "players")

#: NetPoints' own floor: the bucket answers 403 for every earlier season
#: (``association.nba.coverage``).
FIRST_NET_POINTS_SEASON = 2019


def unmatched_counts(db_path: Path) -> dict[str, tuple[int, int]]:
    """Rows with no ``athlete_id``, and total rows, per name-matched table.

    Read-only and closed before anything writes, so the "before" figures cannot
    be taken from a connection the rebuild then invalidates.
    """
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        present = {row[0] for row in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
        counts: dict[str, tuple[int, int]] = {}
        for table in NAME_MATCHED_TABLES:
            if table not in present:
                continue
            null_ids, total = con.execute(f"SELECT count(*) FILTER (WHERE athlete_id IS NULL), count(*) FROM {table}").fetchone()
            counts[table] = (int(null_ids), int(total))
        return counts
    finally:
        con.close()


def duplicate_id_player_rows(db_path: Path) -> list[tuple[str, int, int]]:
    """Per-game NetPoints rows held by each player ESPN files under two ids.

    The second half of the measurement, and the only one that shows #101 moving.
    A NULL ``athlete_id`` is how the two per-game tables record an unmatched
    name, but ``net_points_player_fingerprint`` DROPS an unmatched row instead,
    so its unmatched count is always 0 and says nothing. What says something is
    whether these eight players have any rows at all: before the fix they had
    none, across every season on file.

    The pairs are found the same way ``Pipeline._name_to_athlete_id`` and
    ``fetch/repairs/duplicate_athletes.py`` find them - two ids sharing one
    team's box score in one game - rather than hardcoded, so this keeps
    reporting on the right players if ESPN files a ninth.
    """
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return [
            (str(name), int(game_rows), int(fingerprint_rows))
            for name, game_rows, fingerprint_rows in con.execute("""
                WITH pairs AS (
                    SELECT display_name, min(athlete_id) AS a, max(athlete_id) AS b
                    FROM players GROUP BY 1 HAVING count(DISTINCT athlete_id) = 2
                ),
                same_person AS (
                    SELECT p.display_name, p.a, p.b FROM pairs p
                    WHERE EXISTS (
                        SELECT 1 FROM player_box_stats x JOIN player_box_stats y
                          ON x.event_id = y.event_id AND x.season = y.season AND x.team_id = y.team_id
                        WHERE x.athlete_id = p.a AND y.athlete_id = p.b
                    )
                )
                SELECT s.display_name,
                       (SELECT count(*) FROM net_points_player_game g WHERE g.athlete_id IN (s.a, s.b)),
                       (SELECT count(*) FROM net_points_player_game_fingerprint f WHERE f.athlete_id IN (s.a, s.b))
                FROM same_person s ORDER BY 1
            """).fetchall()
        ]
    finally:
        con.close()


def report(label: str, counts: dict[str, tuple[int, int]], duplicates: list[tuple[str, int, int]]) -> None:
    """Print both measurements under a heading."""
    log.info("%s:", label)
    if not counts:
        log.info("  (no NetPoints tables in the warehouse yet)")
    for table, (null_ids, total) in counts.items():
        log.info("  %-40s %7s of %7s rows have no athlete_id", table, f"{null_ids:,}", f"{total:,}")
    if duplicates:
        empty = sum(1 for _, game_rows, fingerprint_rows in duplicates if not game_rows and not fingerprint_rows)
        log.info("  players ESPN files under two ids: %s of %s have no per-game NetPoints at all", empty, len(duplicates))
        for name, game_rows, fingerprint_rows in duplicates:
            log.info("    %-22s %6s game rows, %7s fingerprint rows", name, f"{game_rows:,}", f"{fingerprint_rows:,}")


def missing_trees(data_dir: Path) -> list[str]:
    """Which of :data:`REQUIRED_TREES` are not on disk."""
    return [name for name in REQUIRED_TREES if not (data_dir / name).exists()]


def main() -> int:
    """Re-fetch and re-parse the name-matched NetPoints tables, then reload them."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=default_data_dir(), help="Parquet flat-file root")
    parser.add_argument("--db-path", default=default_db_path(), help="DuckDB warehouse file")
    parser.add_argument(
        "--seasons",
        default="",
        help=(
            f"Comma-separated seasons for the season fingerprint (default: {FIRST_NET_POINTS_SEASON} through the current season). "
            "The daily files are not season-scoped: they are fetched for every date with a local game."
        ),
    )
    parser.add_argument("--rate-limit", type=float, default=5.0, help="Max requests/second")
    parser.add_argument("--workers", type=int, default=4, help="How many requests to keep in flight")
    parser.add_argument("--include-season-file", action="store_true", help="Also refresh net_points_player, which is keyed by ESPN's id and unaffected by either fault")
    parser.add_argument("--fetch-only", action="store_true", help="Write Parquet, skip reloading the warehouse")
    parser.add_argument("--dry-run", action="store_true", help="Report what is on disk and what would be fetched, and stop")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    data_dir, db_path = Path(args.data_dir), Path(args.db_path)

    absent = missing_trees(data_dir)
    if absent:
        print(f"error: {data_dir} has no {', '.join(absent)} - the NetPoints matchers resolve against those, so run a normal pull first", file=sys.stderr)
        return 1
    if not db_path.exists():
        print(f"error: no warehouse at {db_path} - this backfill reloads tables into an existing one", file=sys.stderr)
        return 1

    seasons = [int(s) for s in args.seasons.split(",") if s.strip()] if args.seasons else list(range(FIRST_NET_POINTS_SEASON, current_season() + 1))

    report("before", unmatched_counts(db_path), duplicate_id_player_rows(db_path))

    if args.dry_run:
        log.info("would re-fetch:")
        log.info("  the season fingerprint for %s", ", ".join(str(s) for s in seasons))
        log.info("  every daily file for dates with a local game (both objects per date)")
        if args.include_season_file:
            log.info("  the season-level flat file behind net_points_player")
        log.info("nothing was fetched (--dry-run)")
        return 0

    # force=True is the whole point: the per-date markers are what would
    # otherwise skip every file already on disk, which is exactly the set whose
    # rows were written by the old parser.
    pipeline = Pipeline(
        ESPNClient(rate_limit=args.rate_limit),
        data_dir,
        include_net_points_daily=True,
        force=True,
        workers=args.workers,
    )

    if args.include_season_file:
        pipeline.fetch_net_points(seasons)
    for season in seasons:
        pipeline.fetch_net_points_fingerprint(season)
    pipeline.fetch_net_points_daily()

    if not pipeline.written:
        log.info("nothing was written - the source returned no rows, so the warehouse is untouched")
        return 0
    log.info("wrote: %s", ", ".join(sorted(pipeline.written)))

    if args.fetch_only:
        log.info("skipping the reload (--fetch-only); run `association data load --tables %s`", ",".join(sorted(pipeline.written)))
        return 0

    # Only the tables this run wrote, the same subset `data pull` reloads - a
    # full rebuild rescans the whole tree for no reason here.
    warehouse.build(data_dir, db_path, tables=sorted(pipeline.written))
    report("after", unmatched_counts(db_path), duplicate_id_player_rows(db_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
