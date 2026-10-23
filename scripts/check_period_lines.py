#!/usr/bin/env python3
"""Warehouse check for the completeness table a player's period reads are
bounded by (``plays_complete``).

A period's line - a player's first-quarter rebounds, his second-half assists -
is rebuilt from the shots and the plays (``nba.period_line.period_line_sql``),
because nothing ESPN serves splits a box score by period. The one independent
check such a figure has is the game it belongs to: summed over every period,
overtime included, it has to equal the player's box score, and the gap between
them bounds every period's figure in that game. The load-time table
``plays_complete`` holds that gap per player-game and column
(``fetch.repairs.plays_complete``); this measures it afresh from the plays and
the box scores and fails where the stored table disagrees - a table built
before the rules or the plays it sums last changed.

    python scripts/check_period_lines.py [--db-path ./nba.duckdb] [--print-table]

Run it after editing ``period_line_sql``, and after a pull that re-fetches
play-by-play (``association data load`` rebuilds the table). ``--print-table``
prints, per season and column, the share of player-games whose plays do not
add up - what ``player_games.PERIOD_MISSING_ABOVE`` is read against. Needs a
warehouse, no ollama and no network.
"""

from __future__ import annotations

import argparse
import sys

import duckdb

from association.cli.paths import default_db_path
from association.fetch.repairs.plays_complete import plays_complete_sql
from association.nba.period_line import PERIOD_COLUMNS, PLAYS_COMPLETE, gap_column


def stale(con: duckdb.DuckDBPyConnection) -> tuple[int, int]:
    """How many player-games the fresh measurement and the stored table
    disagree on (a row in one and not the other, or any column's gap), and
    how many the stored table holds."""
    gaps = ", ".join(gap_column(c) for c in PERIOD_COLUMNS)
    keys = "event_id, season, athlete_id"
    row = con.execute(
        f"""
        WITH fresh AS ({plays_complete_sql()}),
        stored AS (SELECT {keys}, {gaps} FROM {PLAYS_COMPLETE}),
        measured AS (SELECT {keys}, {gaps} FROM fresh)
        SELECT (SELECT COUNT(*) FROM (SELECT * FROM measured EXCEPT ALL SELECT * FROM stored))
             + (SELECT COUNT(*) FROM (SELECT * FROM stored EXCEPT ALL SELECT * FROM measured)),
               (SELECT COUNT(*) FROM stored)
        """
    ).fetchone()
    assert row is not None  # two scalar subqueries always return one row
    return int(row[0]), int(row[1])


def table(con: duckdb.DuckDBPyConnection) -> list[tuple[int, list[float]]]:
    """Per season, per column, the percentage of player-games whose plays do
    not add up to the box score."""
    cells = ", ".join(f"100.0 * AVG(CASE WHEN {gap_column(c)} <> 0 THEN 1 ELSE 0 END)" for c in PERIOD_COLUMNS)
    return [(int(row[0]), [float(v) for v in row[1:]]) for row in con.execute(f"SELECT season, {cells} FROM {PLAYS_COMPLETE} GROUP BY 1 ORDER BY 1").fetchall()]


def main() -> int:
    """Measure, compare, and print what disagrees."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-path", default=str(default_db_path()))
    parser.add_argument("--print-table", action="store_true", help="print the share of player-games whose plays do not add up, per season and column")
    args = parser.parse_args()
    con = duckdb.connect(args.db_path, read_only=True)
    held = {row[0] for row in con.execute(f"SELECT column_name FROM (DESCRIBE {PLAYS_COMPLETE})").fetchall()}
    if not {gap_column(c) for c in PERIOD_COLUMNS} <= held:
        print(f"{PLAYS_COMPLETE} holds no gap columns (built before 7.0.0): rebuild it with `association data load --tables teams` (any load rebuilds it)")
        return 1
    if args.print_table:
        print("season " + " ".join(PERIOD_COLUMNS))
        for season, shares in table(con):
            print(season, " ".join(f"{share:.1f}" for share in shares))
    differ, held = stale(con)
    print(f"{held - min(differ, held):,}/{held:,} player-games of {PLAYS_COMPLETE} agree with a fresh measurement")
    if differ:
        print(f"{differ:,} rows differ: rebuild it with `association data load --tables teams` (any load rebuilds it)")
    return 1 if differ else 0


if __name__ == "__main__":
    sys.exit(main())
