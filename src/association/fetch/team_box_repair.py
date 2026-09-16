"""Team box columns ESPN serves under the wrong name, put back at load time.

Three faults, all ESPN's and all proven to survive a refetch (see ``DATA.md``,
"2018 team box scores hold values under the wrong column names", "The team
box ``turnovers`` column is zero before 2013" and "2008's team rebound columns
hold something other than rebounds"). Neither is a parser bug: the
parser assigns each value to the column ESPN's own ``name`` field gives it
(:func:`association.fetch.parse._assign_stat`), and a clean pull with current
code reproduced ``team_box_stats`` byte for byte.

**2018 is shifted.** Every non-empty 2018 row - both season types - holds
several values under a neighbouring statistic's name. The displacement is a
cycle, and it shows up in the league means as one column holding the next
one's number (2017 mean, then what 2018 stores, then what 2018 comes to hold
once rebuilt):

- ``assists`` 22.545, stored 4.831, rebuilt 22.979
- ``steals`` 7.722, stored 13.689, rebuilt 7.718
- ``blocks`` 4.739, stored 19.985, rebuilt 4.831
- ``turnovers`` 13.439, stored 0.585, rebuilt 13.688
- ``fouls`` 20.101, stored 0.043, rebuilt 19.985

Read the stored figures: 2018's ``assists`` is its real blocks, its
``steals`` is its real turnovers, its ``blocks`` is its real fouls. Per row,
not just on average - ``assists`` equals the player-box block sum in 2,134 of
2,134 regular-season rows and the real assist sum in 1. The percentage columns
move the same way: ``fieldGoalPct`` holds FT% (2,134 of 2,134) and
``freeThrowPct`` holds 3P% (2,134 of 2,134), which ``threePointFieldGoalPct``
also holds, correctly.

**Turnovers are missing before 2013.** ``turnovers`` is 0 in every row up to
2012 (2,204 of 2,204 in 1994, 2,322 of 2,322 in 2000) and ``teamTurnovers``
holds a copy of ``totalTurnovers`` rather than the handful of turnovers charged
to a team rather than a player. ``totalTurnovers`` itself is right in that era:
it runs 0.55 a game above the player-box sum in 1994, which is what the team
turnovers it includes are worth in a season where the column works (0.59 in
2013, 0.59 in 2017). So only two columns are wrong before 2013.

**2008's rebounds are shuffled.** Every non-empty 2008 REGULAR-season row
holds its offensive rebounds under ``defensiveRebounds`` (2,458 of 2,460 rows
equal the player-box offensive sum) and, under ``offensiveRebounds``, the
rebounds credited to the team rather than a player - it means 8.36 a game,
against a ``totalRebounds``-minus-players gap of 8.59 in 2007 and 8.18 in 2009.
``totalRebounds`` then counts the offensive boards twice: it equals the player
rebound sum plus both stored columns in 2,452 of 2,460 rows, which is why it
means 61.54 against 49.64 and 49.47 either side. A refetch of ``271107026`` on
2026-09-16 served the same 8/15/70 for Cleveland, whose players' own rows say
15 offensive and 32 defensive of 47. The 2008 postseason is clean (172 of 172
rows match), so only season type 2 is touched.

All three columns are recoverable. The two splits are player sums, like
assists. ``totalRebounds`` is the player rebound sum plus that stored team
figure - the definition ESPN's total follows in every season around it (see
"The team ``totalRebounds`` column stops including team rebounds in 2022") -
so 2008 stays comparable with 2007 and 2009 instead of dropping its team
rebounds or keeping a double count.

Why a player-box sum is a re-derivation and not an estimate
-----------------------------------------------------------

A team's assists ARE the sum of its players' assists - there is no such thing
as a team assist - and the same holds for steals, blocks, fouls and (individual)
turnovers. ESPN agrees: in the control seasons its own team column equals the
player sum in 2,134 of 2,134 rows (2017) and 2,436 of 2,460 (2019). So this
writes into ``team_box_stats`` rather than sitting in a separate view, unlike
:mod:`association.fetch.reconstructed_box`, whose play-by-play rebuild is an
approximation and is kept visibly apart for exactly that reason. Nothing here
is derived from plays, and nothing here fills a gap ESPN left empty.

What is NOT recoverable, and stays that way
--------------------------------------------

The player box has no flagrant fouls, no technical fouls, no points in the
paint and no team turnovers, so a 2018 column holding one of those cannot be
rebuilt from anything. Those are set to NULL rather than left holding another
statistic's number: a wrong value that reads as a real one is the failure this
project keeps producing, and "we do not know" is the honest column content.
Measured, this costs no answer - nothing in ``src`` reads any of them off
``team_box_stats`` (:mod:`association.query.team_metrics` reads the
same-named columns on ``team_season_stats``, a different table with its own
history) - but the SQL agent can reach them through ``run_sql``, which is
precisely why a NULL is worth more there than 7.7 "flagrant fouls" a game.

``teamTurnovers`` is the one column in 2018's shifted block that nothing
proves wrong: it means 0.596 a game against 0.586 in 2017 and 0.548 in 2019,
and the value now standing in ``turnovers`` (0.585) matches it rather than any
other statistic - the simplest reading is that the team-turnover value was
written to both names. So it is left alone, while ``totalTurnovers``, which
ESPN keeps equal to ``turnovers + teamTurnovers`` in every season and which
therefore means 1.181 a game against a real ~14, is cleared.

An empty team-game stays empty
-------------------------------

Every Chicago and New Orleans game from 2013 to 2018 has an all-NULL
``team_box_stats`` row beside player rows that list everyone as having played
with no minutes and every stat zero (``DATA.md``, "Every Chicago and New
Orleans game from 2013 to 2018 has an empty box score") - 326 of 2018's 2,460
regular-season rows. Summing those player rows yields 0, and writing that 0
into the team row would turn "ESPN has no box score for this game" into "this
team recorded no assists", which is the same fabrication in a new place.

So a row is repaired only when it is non-empty (``fieldGoalsAttempted IS NOT
NULL``, the test :func:`association.query.templates.player_splits` already uses
to caveat a team split) **and** its team-game has at least one player row with
minutes. A row failing either test keeps every column exactly as ESPN served
it: fully corrected or fully untouched, never half of each. That also leaves
alone the 117 all-NULL team rows whose player rows are real (Vancouver 1996 and
Chicago 2000 - ``DATA.md``, "Vancouver 1996 is an empty TEAM box"), which are a
different fault with a different fix, and which a turnover rebuild would
otherwise have given a lone turnover count in an otherwise empty row.

Idempotent, because the repair keys on ``season`` and on emptiness, never on
whether a value "looks wrong" - running it twice writes the same numbers, which
is what lets it run on every build and every partial ``data load``.

.. versionadded:: 2.2.0
"""

from __future__ import annotations

import logging

import duckdb

log: logging.Logger = logging.getLogger("association.fetch.team_box_repair")

SHIFTED_SEASON: int = 2018
"""The one season whose team box row holds values under neighbouring names.

2017 and 2019 come from the same code and the same column order with the right
values, which is what makes this ESPN's fault rather than the parser's.

.. versionadded:: 2.2.0
"""

SWAPPED_REBOUNDS_SEASON: int = 2008
"""The one season whose regular-season team rebound columns are shuffled.

.. versionadded:: 2.2.0
"""

LAST_MISSING_TURNOVER_SEASON: int = 2012
"""The last season whose team box ``turnovers`` column is 0 in every row.

The changeover is exact: 0 in 2,459 of 2,460 rows in 2011 and in 0 of 2,126 in
2013.

.. versionadded:: 2.2.0
"""

REBUILT_COLUMNS: tuple[str, ...] = ("assists", "steals", "blocks", "fouls", "turnovers", "fieldGoalPct", "freeThrowPct")
"""Columns re-derived for :data:`SHIFTED_SEASON` from the row's own fetched numbers.

The first five are summed from ``player_box_stats``; the two percentages are
computed from the made/attempted columns in the same team row, which are right.

.. versionadded:: 2.2.0
"""

CLEARED_COLUMNS: tuple[str, ...] = ("flagrantFouls", "technicalFouls", "totalTechnicalFouls", "totalTurnovers", "pointsInPaint")
"""Columns set to NULL for :data:`SHIFTED_SEASON`: proven wrong, and no source to rebuild them from.

``pointsInPaint`` is -1 in all 2,134 regular-season and all 146 postseason
rows, a sentinel rather than a displaced value.

.. versionadded:: 2.2.0
"""

# ESPN publishes these percentages already rounded to a whole number: its own
# column equals round(100 * made / attempted) in 2,134 of 2,134 rows in 2017
# and 2,460 of 2,460 in 2019, against about half that for truncation. Matching
# the convention keeps 2018 comparable with the seasons either side of it.
_PCT = "CAST(round(100.0 * {made} / NULLIF({attempted}, 0)) AS BIGINT)"

# Summed back to BIGINT: DuckDB's SUM over a BIGINT column returns HUGEINT, and
# a CASE between HUGEINT and the stored BIGINT would silently widen the column.
_PLAYER_TOTALS = """
    tbr_player_totals AS (
        SELECT event_id, season, season_type, team_id,
               CAST(SUM(assists) AS BIGINT) AS p_assists,
               CAST(SUM(steals) AS BIGINT) AS p_steals,
               CAST(SUM(blocks) AS BIGINT) AS p_blocks,
               CAST(SUM(fouls) AS BIGINT) AS p_fouls,
               CAST(SUM(turnovers) AS BIGINT) AS p_turnovers,
               CAST(SUM(rebounds) AS BIGINT) AS p_rebounds,
               CAST(SUM(offensiveRebounds) AS BIGINT) AS p_offensive_rebounds,
               CAST(SUM(defensiveRebounds) AS BIGINT) AS p_defensive_rebounds,
               MAX(minutes) AS p_max_minutes
        FROM player_box_stats
        -- season and season_type as well as event_id: the phantom 1993 season
        -- shares every event id with 1994 (see coverage.py), and grouping on
        -- event_id alone would sum both copies into one team-game.
        GROUP BY event_id, season, season_type, team_id
    )
"""

# Non-empty stored row AND a team-game whose players have minutes. See the
# module docstring: this is what keeps an empty team-game empty.
_REPAIRABLE = "(t.fieldGoalsAttempted IS NOT NULL AND p.p_max_minutes IS NOT NULL)"
_SHIFTED = f"({_REPAIRABLE} AND t.season = {SHIFTED_SEASON})"
_NO_TURNOVERS = f"({_REPAIRABLE} AND t.season <= {LAST_MISSING_TURNOVER_SEASON})"
# Regular season only: the 2008 postseason matches its player sums in 172 of 172 rows.
_REBOUNDS_SWAPPED = f"({_REPAIRABLE} AND t.season = {SWAPPED_REBOUNDS_SEASON} AND t.season_type = 2)"

# Columns the rebuild reads. A partial `data load --tables` subset, or a
# genuinely thin ESPN response, skips the repair with a logged reason rather
# than failing the whole warehouse build - the same contract
# advanced_stats.build_views keeps.
_REQUIRED_PLAYER_COLUMNS = {
    "event_id",
    "season",
    "season_type",
    "team_id",
    "minutes",
    "assists",
    "steals",
    "blocks",
    "fouls",
    "turnovers",
    "rebounds",
    "offensiveRebounds",
    "defensiveRebounds",
}
_REQUIRED_TEAM_COLUMNS = {"event_id", "season", "season_type", "team_id", "fieldGoalsAttempted"}


def _corrections() -> list[tuple[str, str]]:
    """Each repairable column and the SQL that replaces it, stored value first.

    A column absent from the loaded table is dropped by :func:`repair`, so a
    thin fixture repairs what it has instead of nothing."""
    fixes: list[tuple[str, str]] = [
        ("assists", f"CASE WHEN {_SHIFTED} THEN p.p_assists ELSE t.assists END"),
        ("steals", f"CASE WHEN {_SHIFTED} THEN p.p_steals ELSE t.steals END"),
        ("blocks", f"CASE WHEN {_SHIFTED} THEN p.p_blocks ELSE t.blocks END"),
        ("fouls", f"CASE WHEN {_SHIFTED} THEN p.p_fouls ELSE t.fouls END"),
        # The one column both faults touch: displaced in 2018, absent before 2013.
        ("turnovers", f"CASE WHEN {_SHIFTED} OR {_NO_TURNOVERS} THEN p.p_turnovers ELSE t.turnovers END"),
        ("fieldGoalPct", f"CASE WHEN {_SHIFTED} THEN {_PCT.format(made='t.fieldGoalsMade', attempted='t.fieldGoalsAttempted')} ELSE t.fieldGoalPct END"),
        ("freeThrowPct", f"CASE WHEN {_SHIFTED} THEN {_PCT.format(made='t.freeThrowsMade', attempted='t.freeThrowsAttempted')} ELSE t.freeThrowPct END"),
        # 2008: the stored offensiveRebounds is the team-rebound figure, so the
        # real total is the players' rebounds plus it. Every REPLACE expression
        # reads the stored row, so the order of these three does not matter.
        ("offensiveRebounds", f"CASE WHEN {_REBOUNDS_SWAPPED} THEN p.p_offensive_rebounds ELSE t.offensiveRebounds END"),
        ("defensiveRebounds", f"CASE WHEN {_REBOUNDS_SWAPPED} THEN p.p_defensive_rebounds ELSE t.defensiveRebounds END"),
        ("totalRebounds", f"CASE WHEN {_REBOUNDS_SWAPPED} THEN p.p_rebounds + t.offensiveRebounds ELSE t.totalRebounds END"),
        # Before 2013 this holds a copy of totalTurnovers (2,204 of 2,204 rows
        # in 1994), not the ~0.6 team turnovers a game it names. Nothing in the
        # player box can rebuild it, so it says so.
        ("teamTurnovers", f"CASE WHEN {_NO_TURNOVERS} THEN NULL ELSE t.teamTurnovers END"),
    ]
    fixes += [(column, f"CASE WHEN {_SHIFTED} THEN NULL ELSE t.{column} END") for column in CLEARED_COLUMNS]
    return fixes


def repair(con: duckdb.DuckDBPyConnection, loaded: set[str]) -> None:
    """Rewrite ``team_box_stats`` with the three ESPN faults corrected.

    Reads ``player_box_stats`` for the sums, so it is skipped with a logged
    reason when either table is absent or missing a column the rebuild needs,
    rather than failing the whole warehouse build. Safe to run on every build
    and on a partial ``data load``: it is keyed on the season and on whether a
    row is empty, never on whether a value looks wrong, so a second run writes
    the same numbers.

    .. versionadded:: 2.2.0
    """
    for table, required in (("team_box_stats", _REQUIRED_TEAM_COLUMNS), ("player_box_stats", _REQUIRED_PLAYER_COLUMNS)):
        if table not in loaded:
            log.info("skip team box repair (%s not loaded)", table)
            return
        missing = required - {r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()}
        if missing:
            log.info("skip team box repair (%s missing columns: %s)", table, ", ".join(sorted(missing)))
            return

    present = {r[0] for r in con.execute("DESCRIBE team_box_stats").fetchall()}
    fixes = [(column, sql) for column, sql in _corrections() if column in present]
    if not fixes:
        log.info("skip team box repair (none of the affected columns are loaded)")
        return

    # Column names are literals from this module, never a slot. SELECT *
    # REPLACE keeps every other column, and its position, so a column added to
    # the parser later survives without this having to know about it.
    replacements = ", ".join(f"{sql} AS {column}" for column, sql in fixes)
    con.execute(f"""
        CREATE OR REPLACE TABLE team_box_stats AS
        WITH {_PLAYER_TOTALS}
        SELECT t.* REPLACE ({replacements})
        FROM team_box_stats t
        LEFT JOIN tbr_player_totals p
          ON p.event_id = t.event_id AND p.season = t.season AND p.season_type = t.season_type AND p.team_id = t.team_id
    """)
    log.info("team box repair applied: %d columns (%s shifted, %s rebounds, turnovers through %s)", len(fixes), SHIFTED_SEASON, SWAPPED_REBOUNDS_SEASON, LAST_MISSING_TURNOVER_SEASON)
