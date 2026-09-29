#!/usr/bin/env python3
"""Warehouse check for team_games.TEAM_PERIOD_AGREEMENT.

A team's period line - its first-quarter rebounds, its second-half turnovers -
is rebuilt from its players' period lines and its own plays
(``team_games.team_period_line_sql``), and its points are read from ESPN's
linescore. The one independent check such a figure has is the game it belongs
to: summed over every period, overtime included, the line has to equal the
team's box score, and the linescore the final score. This sums both, per
season and per column, and compares the share that equals exactly with the
table in the code.

    python scripts/check_team_period_lines.py [--db-path ./nba.duckdb]

Run it after editing ``team_period_line_sql``, ``player_games.period_line_sql``
(the team line sums it) or ``TEAM_PERIOD_AGREEMENT``, and after a pull that
re-fetches play-by-play. It fails when a season the table leaves out (read as
99%+) measures under 99%, or when a listed season moved by more than half a
point either way - both mean the caveat an answer carries is no longer the
measured one. Needs a warehouse, no ollama and no network.
"""

from __future__ import annotations

import argparse
import sys

import duckdb

from association.cli.paths import default_db_path
from association.query.team_games import TEAM_PERIOD_AGREEMENT, TEAM_PERIOD_BOX, TEAM_PERIOD_COLUMNS, team_period_line_sql

# How far a listed figure may drift before the table is stale. A re-fetch of a
# few games moves a season by hundredths; a changed rule moves it by points.
TOLERANCE = 0.5
# A season the table leaves out is claimed to be at least this good.
LISTED_BELOW = 99.0


def _cell(column: str) -> str:
    """The agreement of one column, as a percentage over the season's team-games."""
    if column == "points":
        final = "CASE WHEN g.home_team_id = b.team_id THEN g.home_score ELSE g.away_score END"
        linescores = "CASE WHEN g.home_team_id = b.team_id THEN g.home_linescores ELSE g.away_linescores END"
        summed = f"list_sum(list_filter(list_transform(string_split({linescores}, ','), x -> TRY_CAST(trim(x) AS BIGINT)), x -> x IS NOT NULL))"
        return f"100.0 * AVG(CASE WHEN {summed} = {final} THEN 1 ELSE 0 END) AS points"
    box = TEAM_PERIOD_BOX.get(column, f"b.{column}")
    return f"100.0 * AVG(CASE WHEN COALESCE(l.{column}, 0) = {box} THEN 1 ELSE 0 END) AS {column}"


def measure(con: duckdb.DuckDBPyConnection) -> dict[int, dict[str, float]]:
    """Per season, per column, the percentage of team-games (with a team box
    score, in a game the shot table covers) whose summed period figures equal
    the box score - and, for points, whose linescore sums to the final score."""
    line = team_period_line_sql(None, "SELECT DISTINCT event_id, season FROM shot_chart")
    cells = ", ".join(_cell(c) for c in TEAM_PERIOD_COLUMNS)
    rows = con.execute(
        f"""
        WITH l AS ({line}),
        b AS (
            SELECT b.* FROM team_box_stats b
            WHERE b.fieldGoalsAttempted IS NOT NULL
              AND b.event_id IN (SELECT DISTINCT cov.event_id FROM shot_chart cov WHERE cov.season = b.season)
        )
        SELECT b.season, COUNT(*), {cells}
        FROM b JOIN real_games g ON g.event_id = b.event_id AND g.season = b.season
        LEFT JOIN l ON l.event_id = b.event_id AND l.season = b.season AND l.team_id = b.team_id
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    print(f"{sum(int(row[1]) for row in rows):,} team-games measured")
    return {int(row[0]): dict(zip(TEAM_PERIOD_COLUMNS, (float(v) for v in row[2:]), strict=True)) for row in rows}


def problems(measured: dict[int, dict[str, float]]) -> list[str]:
    """Every cell where the code's table and the warehouse disagree."""
    found = []
    for season, by_column in measured.items():
        for column, pct in by_column.items():
            listed = TEAM_PERIOD_AGREEMENT.get(column, {}).get(season)
            if listed is None and pct < LISTED_BELOW:
                found.append(f"{season} {column}: measured {pct:.1f}%, not listed (read as {LISTED_BELOW:.0f}%+)")
            elif listed is not None and abs(listed - pct) > TOLERANCE:
                found.append(f"{season} {column}: listed {listed:.1f}%, measured {pct:.1f}%")
    return found


def main() -> int:
    """Measure, compare, and print what disagrees."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db-path", default=str(default_db_path()))
    parser.add_argument("--print-table", action="store_true", help="print the measured table in the shape TEAM_PERIOD_AGREEMENT takes")
    args = parser.parse_args()
    con = duckdb.connect(args.db_path, read_only=True)
    measured = measure(con)
    if args.print_table:
        for column in TEAM_PERIOD_COLUMNS:
            weak = {season: round(by[column], 1) for season, by in measured.items() if by[column] < LISTED_BELOW}
            print(f'    "{column}": {weak},')
    found = problems(measured)
    for line in found:
        print(line)
    cells = sum(len(v) for v in measured.values())
    print(f"{cells - len(found)}/{cells} season-columns agree with TEAM_PERIOD_AGREEMENT")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
