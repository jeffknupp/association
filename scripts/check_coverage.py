#!/usr/bin/env python3
"""Warehouse check for coverage.COVERAGE.

The floors are hand-written and their correctness is a claim about the data,
not about the code: each one says the season a table becomes usable, and only a
built warehouse can confirm it. pytest runs offline against fixtures, so it
lives here, next to check_nicknames.py, and needs a warehouse but no ollama.

    python scripts/check_coverage.py [--db-path ./nba.duckdb]

Run it after editing COVERAGE, and after any pull that reaches further back
than the last one. A failure is one of three things: a floor that is now too
high (the warehouse holds a usable season the code refuses), a floor that is
too low (the season it admits is empty or a fraction of a league), or a table
that has gained or lost its season column.

"Usable" is deliberately not "present". Half the seasons this catches DO have
rows - 82 games where a league plays 1,100, or 7 players where a league has
350 - and admitting them is the failure this whole module exists to stop. The
test is therefore a share of the same table's own median season, not a
non-zero count.
"""

from __future__ import annotations

import argparse
import sys

import duckdb

from association.coverage import COVERAGE, POSTSEASON, REGULAR_SEASON, Coverage

# A season holding less than this share of the table's median season is a
# fragment, not a season. Generous on purpose: a real lockout season (1999's 50
# games, 2012's 66) must pass, while one team's 82 games out of 1,100 and a
# half-year of play-by-play must not.
USABLE_SHARE = 0.55


def _season_type_column(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return "season_type" in {row[0] for row in con.execute(f"DESCRIBE {table}").fetchall()}


def _counts(con: duckdb.DuckDBPyConnection, table: str, season_type: int | None) -> dict[int, int]:
    """Rows per season, for one season type or for the whole table."""
    where = "" if season_type is None else f" WHERE season_type = {season_type}"
    return {int(s): n for s, n in con.execute(f"SELECT season, count(*) FROM {table}{where} GROUP BY 1").fetchall()}


def _check_floor(table: str, label: str, floor: int, counts: dict[int, int], coverage: Coverage) -> list[str]:
    if not counts:
        return [f"{table} ({label}): no rows at all, but a floor of {floor} is declared"]
    ordered = sorted(counts.values())
    median = ordered[len(ordered) // 2]
    threshold = median * USABLE_SHARE
    problems = []

    at_floor = counts.get(floor, 0)
    if floor in coverage.partial:
        if at_floor >= threshold:
            problems.append(f"{table} ({label}): {floor} is declared partial but holds {at_floor:,} rows, {at_floor / median:.0%} of the median season")
    elif coverage.first_ranking_season is not None:
        # A per-player career table is SUPPOSED to be sparse at its floor - its
        # early seasons hold only the careers that reached the box-score era.
        # Share-of-median is the wrong test for it; that claim is the ranking
        # floor's, and it is checked separately against the box-score pool.
        if at_floor == 0:
            problems.append(f"{table} ({label}): floor is {floor}, which holds no rows at all")
    elif at_floor < threshold:
        problems.append(f"{table} ({label}): floor is {floor}, which holds {at_floor:,} rows - {at_floor / median:.0%} of the median season, so it is a fragment")

    # Too high: a usable season sitting below the floor is one the code refuses
    # for no reason. Seasons already declared partial or phantom are exempt -
    # the first is admitted with a caveat, and the second is excluded because
    # of WHICH season it is rather than how much of it there is.
    exempt = set(coverage.partial) | set(coverage.phantom)
    below = sorted(s for s, n in counts.items() if s < floor and n >= threshold and s not in exempt and (coverage.first_ranking_season is None or s >= coverage.first_season))
    if below:
        problems.append(f"{table} ({label}): floor is {floor}, but {below} are already usable ({', '.join(f'{s}={counts[s]:,}' for s in below)})")
    return problems


def _check_phantom(table: str, coverage: Coverage, counts: dict[int, int]) -> list[str]:
    """A phantom season must actually duplicate the season above it.

    Checked rather than trusted, because the claim is what excuses a
    full-looking season from the floor. If ESPN ever starts answering
    season=1993 with the real 1992-93, this is what says so.
    """
    problems = []
    for season in coverage.phantom:
        here, above = counts.get(season, 0), counts.get(season + 1, 0)
        if here == 0:
            problems.append(f"{table}: {season} is declared a phantom of {season + 1} but holds no rows")
        elif here != above:
            problems.append(f"{table}: {season} is declared a phantom of {season + 1} but holds {here:,} rows against {above:,} - no longer a duplicate")
    return problems


def main() -> int:
    """Check every declared floor against a built warehouse."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path", default="./nba.duckdb")
    args = parser.parse_args()

    con = duckdb.connect(args.db_path, read_only=True)
    known = {row[0] for row in con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'").fetchall()}

    problems: list[str] = []
    checked = 0
    for table, coverage in sorted(COVERAGE.items()):
        if table not in known:
            problems.append(f"{table}: declared in COVERAGE but not in {args.db_path}")
            continue
        if "season" not in {row[0] for row in con.execute(f"DESCRIBE {table}").fetchall()}:
            problems.append(f"{table}: declared in COVERAGE but has no season column")
            continue
        split = _season_type_column(con, table)
        # The postseason floor is only checked where one is declared; every
        # other table's floor is a claim about the whole table.
        if split and coverage.postseason_first_season is not None:
            regular = _counts(con, table, REGULAR_SEASON)
            problems += _check_floor(table, "regular", coverage.first_season, regular, coverage)
            problems += _check_floor(table, "postseason", coverage.postseason_first_season, _counts(con, table, POSTSEASON), coverage)
            checked += 2
            whole = regular
        else:
            whole = _counts(con, table, None)
            problems += _check_floor(table, "all", coverage.first_season, whole, coverage)
            checked += 1
        if coverage.phantom:
            problems += _check_phantom(table, coverage, whole)
            checked += len(coverage.phantom)

        # The ranking floor is a claim about the POOL, not the row count: the
        # season it names is the first whose player pool matches what the
        # box-score tables hold for the same year.
        if coverage.first_ranking_season is not None:
            first = coverage.first_ranking_season
            pool = con.execute(f"SELECT count(DISTINCT athlete_id) FROM {table} WHERE season = ?", [first]).fetchone()[0]
            box = con.execute("SELECT count(DISTINCT athlete_id) FROM player_box_stats WHERE season = ?", [first]).fetchone()[0]
            checked += 1
            if box == 0:
                problems.append(f"{table}: ranking floor {first} has no box-score pool to compare against")
            elif pool < box * 0.9:
                problems.append(f"{table}: ranking floor is {first}, where the pool is {pool} against {box} in player_box_stats - still a survivor sample")

    for problem in problems:
        print(f"FAIL  {problem}")
    print(f"{checked - len(problems)}/{checked} coverage floors check out against {args.db_path}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
