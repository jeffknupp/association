#!/usr/bin/env python3
"""Warehouse check for player_games.PERIOD_AGREEMENT.

A period's line - a player's first-quarter rebounds, his second-half assists -
is rebuilt from the shots and the plays (``player_games.period_line_sql``),
because nothing ESPN serves splits a box score by period. The one independent
check such a figure has is the game it belongs to: summed over every period,
overtime included, it has to equal the player's box score. This sums it, per
season and per column, and compares the share that equals exactly with the
table in the code.

    python scripts/check_period_lines.py [--db-path ./nba.duckdb]

Run it after editing ``period_line_sql`` or ``PERIOD_AGREEMENT``, and after a
pull that re-fetches play-by-play. It fails when a season the table leaves out
(read as 99%+) measures under 99%, or when a listed season moved by more than
half a point either way - both mean the caveat an answer carries is no longer
the measured one. Needs a warehouse, no ollama and no network.
"""

from __future__ import annotations

import argparse
import sys

import duckdb

from association.cli.paths import default_db_path
from association.query.player_games import PERIOD_AGREEMENT, PERIOD_COLUMNS, period_line_sql

# How far a listed figure may drift before the table is stale. A re-fetch of a
# few games moves a season by hundredths; a changed rule moves it by points.
TOLERANCE = 0.5
# A season the table leaves out is claimed to be at least this good.
LISTED_BELOW = 99.0


def measure(con: duckdb.DuckDBPyConnection) -> dict[int, dict[str, float]]:
    """Per season, per column, the percentage of played player-games (in a game
    the shot table covers) whose summed period lines equal the box score."""
    line = period_line_sql(None, "SELECT DISTINCT event_id, season FROM shot_chart")
    cells = ", ".join(f"100.0 * AVG(CASE WHEN COALESCE(l.{c}, 0) = b.{c} THEN 1 ELSE 0 END) AS {c}" for c in PERIOD_COLUMNS)
    rows = con.execute(
        f"""
        WITH l AS ({line}),
        b AS (
            SELECT b.* FROM player_box_stats b
            WHERE b.minutes IS NOT NULL AND NOT b.did_not_play
              AND b.event_id IN (SELECT DISTINCT cov.event_id FROM shot_chart cov WHERE cov.season = b.season)
        )
        SELECT b.season, {cells}
        FROM b LEFT JOIN l ON l.event_id = b.event_id AND l.season = b.season AND l.athlete_id = b.athlete_id
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    return {int(row[0]): dict(zip(PERIOD_COLUMNS, (float(v) for v in row[1:]), strict=True)) for row in rows}


def problems(measured: dict[int, dict[str, float]]) -> list[str]:
    """Every cell where the code's table and the warehouse disagree."""
    found = []
    for season, by_column in measured.items():
        for column, pct in by_column.items():
            listed = PERIOD_AGREEMENT.get(column, {}).get(season)
            if listed is None and pct < LISTED_BELOW:
                found.append(f"{season} {column}: measured {pct:.1f}%, not listed (read as {LISTED_BELOW:.0f}%+)")
            elif listed is not None and abs(listed - pct) > TOLERANCE:
                found.append(f"{season} {column}: listed {listed:.1f}%, measured {pct:.1f}%")
    return found


def main() -> int:
    """Measure, compare, and print what disagrees."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-path", default=str(default_db_path()))
    args = parser.parse_args()
    con = duckdb.connect(args.db_path, read_only=True)
    measured = measure(con)
    found = problems(measured)
    for line in found:
        print(line)
    cells = sum(len(v) for v in measured.values())
    print(f"{cells - len(found)}/{cells} season-columns agree with PERIOD_AGREEMENT")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
