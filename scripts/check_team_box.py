#!/usr/bin/env python3
"""Warehouse check for the team-box repair in fetch/team_box_repair.py.

The repair is a claim about the data, not about the code: that 2018's team
columns now hold their own statistic and that the turnover columns work before
2013. pytest proves the SQL does what it says against a five-row fixture; only
a built warehouse can say the real seasons came out right. So this lives here,
next to check_coverage.py, and needs a warehouse but no ollama.

    python scripts/check_team_box.py [--db-path ./nba.duckdb]

Run it after `association data load`, and after any pull that rewrote
team_box_stats. A failure is one of four things: a season the repair missed
(the load has not been run yet), a season it should not have touched, a cleared
column that still holds a number, or - the one that matters most - an empty
team-game that has gained a fabricated zero.
"""

from __future__ import annotations

import argparse
import sys

import duckdb

from association.fetch.team_box_repair import CLEARED_COLUMNS, LAST_MISSING_TURNOVER_SEASON, SHIFTED_SEASON
from association.repo_paths import default_db_path

# Team columns that are the sum of the game's player rows, and the player
# column each sums. ESPN's own team column equals the sum in 2,134 of 2,134
# rows in 2017, so this is its convention and not ours.
SUMMED = {"assists": "assists", "steals": "steals", "blocks": "blocks", "fouls": "fouls", "turnovers": "turnovers"}

# Seasons checked for having been left alone. Either side of the shifted one,
# and one from the era the turnover rebuild covers is checked separately.
CONTROL_SEASONS = (2017, 2019)

# ESPN's own team line and its player rows disagree in a handful of games a
# season (DATA.md, "Team box scores disagree slightly with player-box sums"),
# so agreement is checked as a share rather than as every row.
AGREEMENT = 0.99

_NON_EMPTY = "t.fieldGoalsAttempted IS NOT NULL AND p.p_minutes IS NOT NULL"
_PLAYER_TOTALS = f"""
    p AS (
        SELECT event_id, season, season_type, team_id, MAX(minutes) AS p_minutes,
               {", ".join(f"SUM({source}) AS p_{team}" for team, source in SUMMED.items())}
        FROM player_box_stats GROUP BY event_id, season, season_type, team_id
    )
"""
_JOIN = "JOIN p ON p.event_id = t.event_id AND p.season = t.season AND p.season_type = t.season_type AND p.team_id = t.team_id"


def _agreement(con: duckdb.DuckDBPyConnection, season: int, column: str) -> tuple[int, int]:
    """Rows where a team column equals its player sum, out of the non-empty rows of one season."""
    row = con.execute(
        f"""WITH {_PLAYER_TOTALS}
            SELECT COUNT(*), COUNT(*) FILTER (WHERE t.{column} = p.p_{column})
            FROM team_box_stats t {_JOIN} WHERE t.season = ? AND {_NON_EMPTY}""",
        [season],
    ).fetchone()
    return (int(row[0]), int(row[1])) if row else (0, 0)


def _check_rebuilt(con: duckdb.DuckDBPyConnection) -> list[str]:
    """The shifted season's summed columns now hold their own statistic."""
    problems = []
    for column in SUMMED:
        total, agree = _agreement(con, SHIFTED_SEASON, column)
        if total == 0:
            return [f"{SHIFTED_SEASON}: no non-empty team box rows to check"]
        if agree < total:
            problems.append(f"{SHIFTED_SEASON} {column}: matches the player sum in {agree:,} of {total:,} rows - the repair has not been loaded")
    return problems


def _check_cleared(con: duckdb.DuckDBPyConnection) -> list[str]:
    """The shifted season's unrecoverable columns are NULL, in every row."""
    problems = []
    for column in CLEARED_COLUMNS:
        row = con.execute(f"SELECT COUNT(*) FROM team_box_stats WHERE season = ? AND {column} IS NOT NULL", [SHIFTED_SEASON]).fetchone()
        left = int(row[0]) if row else 0
        if left:
            problems.append(f"{SHIFTED_SEASON} {column}: {left:,} rows still hold a value, and nothing can rebuild this column")
    return problems


def _check_turnovers(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Before 2013, `turnovers` is the player sum and `teamTurnovers` is NULL."""
    problems = []
    row = con.execute(
        f"""WITH {_PLAYER_TOTALS}
            SELECT COUNT(*), COUNT(*) FILTER (WHERE t.turnovers = p.p_turnovers), COUNT(*) FILTER (WHERE t.teamTurnovers IS NOT NULL)
            FROM team_box_stats t {_JOIN} WHERE t.season BETWEEN 1994 AND ? AND {_NON_EMPTY}""",
        [LAST_MISSING_TURNOVER_SEASON],
    ).fetchone()
    total, agree, kept = (int(row[0]), int(row[1]), int(row[2])) if row else (0, 0, 0)
    if total == 0:
        return [f"1994-{LAST_MISSING_TURNOVER_SEASON}: no non-empty team box rows to check"]
    if agree < total:
        problems.append(f"1994-{LAST_MISSING_TURNOVER_SEASON} turnovers: matches the player sum in {agree:,} of {total:,} rows - the repair has not been loaded")
    if kept:
        problems.append(f"1994-{LAST_MISSING_TURNOVER_SEASON} teamTurnovers: {kept:,} rows still hold the copy of totalTurnovers")
    return problems


def _check_controls(con: duckdb.DuckDBPyConnection) -> list[str]:
    """A season either side of the shifted one still agrees with its player
    rows to within the handful of games ESPN itself disagrees on - proof the
    repair did not reach a season that never needed it."""
    problems = []
    for season in CONTROL_SEASONS:
        for column in SUMMED:
            total, agree = _agreement(con, season, column)
            if total and agree < total * AGREEMENT:
                problems.append(f"{season} {column}: agrees with the player sum in {agree:,} of {total:,} rows, under {AGREEMENT:.0%}")
    return problems


def _check_empties(con: duckdb.DuckDBPyConnection) -> list[str]:
    """The one that matters most: a team-game ESPN has no box score for must
    still be empty. 1,025 events from 2013 to 2018 have an all-NULL team row
    beside player rows that are all zeros, and a sum of those is a zero that
    reads as a real performance."""
    row = con.execute(
        """WITH e AS (SELECT event_id, season, season_type, team_id FROM player_box_stats GROUP BY ALL HAVING MAX(minutes) IS NULL)
           SELECT COUNT(*) FROM team_box_stats t JOIN e ON e.event_id = t.event_id AND e.season = t.season AND e.season_type = t.season_type AND e.team_id = t.team_id
           WHERE t.assists IS NOT NULL OR t.turnovers IS NOT NULL OR t.fouls IS NOT NULL"""
    ).fetchone()
    filled = int(row[0]) if row else 0
    return [f"empty team-games: {filled:,} now hold a value where ESPN has no box score at all"] if filled else []


def main() -> int:
    """Check the repair against a built warehouse."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", default=default_db_path())
    args = parser.parse_args()

    con = duckdb.connect(args.db_path, read_only=True)
    checks = (_check_rebuilt, _check_cleared, _check_turnovers, _check_controls, _check_empties)
    # Scored per check, not per line: _check_rebuilt and _check_cleared report
    # one problem per column, so counting lines against checks prints a
    # negative "score" on a warehouse that has not been backfilled yet.
    failed = 0
    for check in checks:
        problems = check(con)
        failed += 1 if problems else 0
        for problem in problems:
            print(f"FAIL  {problem}")
    print(f"{len(checks) - failed}/{len(checks)} team box checks pass against {args.db_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
