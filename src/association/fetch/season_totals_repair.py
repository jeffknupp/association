"""A traded player's combined season line, rebuilt from his own stints.

ESPN's career endpoint returns one row per team stint plus a combined
(team-less) row for the whole season. 19 of the 2,062 combined rows disagree
with the stints they claim to combine, and everything downstream *prefers* the
combined row - ``player_season_stats_deduped`` and the leaderboard's
``dedup_traded`` both keep it and drop the stints - so the wrong line is the
one every answer is built from. See ``DATA.md`` ("Traded players' combined
season rows disagree with their own stints").

Measured against the 2026-09-15 warehouse, the 19 are two faults:

- **13 copy a single stint.** Eric Murdock's 1995-96 combined row reads 9 games
  and 62 points - his Vancouver stint exactly - against 73 and 647 across both
  teams. Twelve are from 1996 and one is Jevon Carter 2023, who loses a
  one-game stint.
- **6 are entirely NULL**, all from 1977-1983: Moses Malone 1977, James Edwards
  1978 and 1983, Bill Laimbeer 1982, Danny Schayes 1983 and Sleepy Floyd 1983.
  The per-season endpoint answers 404 for every one of them, which is why
  :meth:`association.fetch.pipeline.Pipeline._repair_season_totals` cannot fill
  them at fetch time.

(The entry in ``ISSUES.md`` said 26. Seven of those were combined rows whose
totals were NULL because a *stint's* totals were NULL, and that repair now
happens at fetch time - so 19 is what is left for this module to do.)

Why this rewrites the table instead of adding a view
-----------------------------------------------------

The same reasoning as :mod:`association.fetch.team_box_repair`: a season's
total IS the sum of its stints - a player's games and points do not exist
anywhere but in the games he played - so this is a re-derivation, not an
estimate, and every reader should see it. A view would fix
``player_season_stats_deduped`` and leave ``leaderboard.py``'s own
``QUALIFY`` reading the broken row out of the stored table, which is the
two-hand-maintained-copies shape this project keeps getting bitten by.

What is derived, and what is refused
-------------------------------------

Every formula below was fitted against the whole warehouse before it was used,
because a constant that looks right is this project's standing failure. On
15,573 rows with the components present, **zero** disagree with:

- the 15 ``avg*`` columns = their own total / ``gamesPlayed`` (1dp)
- ``fieldGoalPct`` / ``threePointFieldGoalPct`` / ``freeThrowPct``
  = 100 * made / attempted (1dp)
- ``scoringEfficiency`` = ``points`` / ``fieldGoalsAttempted`` (3dp)
- ``shootingEfficiency`` = effective FG%, (FGM + 0.5 * 3PM) / FGA (2dp)
- ``assistTurnoverRatio`` / ``stealTurnoverRatio`` = the obvious quotient (1dp)

Two columns are deliberately NOT derived:

- **``avgMinutes`` is set to NULL.** It is the one average with no season total
  behind it (``metrics.py`` already records this: "minutes has none"), so it
  can only be approximated. Both approximations were fitted and both were
  rejected: a games-weighted mean of the stints' own averages reproduces
  ESPN's healthy combined figure in 1,382 of 1,706 rows (81%, and the misses
  are systematic 0.1 shortfalls from re-weighting values already rounded), and
  summing ``player_box_stats.minutes`` does worse still at 61%. A figure that
  is wrong one time in five, printed beside figures that are exact, is the
  fluent-and-false answer this project exists to stop. The 6 all-NULL rows
  predate the box scores entirely and never had minutes to recover.
- **A ratio over zero turnovers is NULL**, not ``inf`` and not ``0``. ESPN
  itself is inconsistent here - of its own zero-turnover rows, 765 carry
  ``inf``, 641 carry ``0.0`` and 332 carry NULL - so there is no convention to
  match, and "we do not know" beats inheriting a coin flip. One of the 19 rows
  needs this.

``position`` is carried from the stored combined row: it is the player's, not
an aggregate of anything.

.. versionadded:: 2.2.0
"""

from __future__ import annotations

import logging

import duckdb

log: logging.Logger = logging.getLogger("association.fetch.season_totals_repair")

#: Season totals that are simply the sum of the stints'.
TOTAL_COLUMNS: tuple[str, ...] = (
    "gamesPlayed",
    "gamesStarted",
    "fieldGoalsMade",
    "fieldGoalsAttempted",
    "threePointFieldGoalsMade",
    "threePointFieldGoalsAttempted",
    "freeThrowsMade",
    "freeThrowsAttempted",
    "offensiveRebounds",
    "defensiveRebounds",
    "totalRebounds",
    "assists",
    "blocks",
    "steals",
    "fouls",
    "turnovers",
    "points",
    "doubleDouble",
    "tripleDouble",
    "disqualifications",
    "ejections",
    "technicalFouls",
    "flagrantFouls",
)
"""The columns a season total is the sum of its stints'.

.. versionadded:: 2.2.0
"""

#: Each per-game average and the total it is derived from.
AVERAGE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("avgFieldGoalsMade", "fieldGoalsMade"),
    ("avgFieldGoalsAttempted", "fieldGoalsAttempted"),
    ("avgThreePointFieldGoalsMade", "threePointFieldGoalsMade"),
    ("avgThreePointFieldGoalsAttempted", "threePointFieldGoalsAttempted"),
    ("avgFreeThrowsMade", "freeThrowsMade"),
    ("avgFreeThrowsAttempted", "freeThrowsAttempted"),
    ("avgOffensiveRebounds", "offensiveRebounds"),
    ("avgDefensiveRebounds", "defensiveRebounds"),
    ("avgRebounds", "totalRebounds"),
    ("avgAssists", "assists"),
    ("avgBlocks", "blocks"),
    ("avgSteals", "steals"),
    ("avgFouls", "fouls"),
    ("avgTurnovers", "turnovers"),
    ("avgPoints", "points"),
)
"""Each per-game average beside the season total it is computed from.

.. versionadded:: 2.2.0
"""

#: Each shooting percentage and the (made, attempted) pair behind it.
PCT_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("fieldGoalPct", "fieldGoalsMade", "fieldGoalsAttempted"),
    ("threePointFieldGoalPct", "threePointFieldGoalsMade", "threePointFieldGoalsAttempted"),
    ("freeThrowPct", "freeThrowsMade", "freeThrowsAttempted"),
)
"""Each percentage beside the made and attempted columns it is computed from.

.. versionadded:: 2.2.0
"""

# Everything this module needs to identify a broken row and rebuild it. A
# column missing from a thin fixture means the repair is skipped rather than
# raising, the same contract team_box_repair has.
_REQUIRED_COLUMNS = frozenset({"athlete_id", "season", "season_type", "team_id", "gamesPlayed", "points"})

# The stints, summed. Keyed on season_type as well as season: a player's
# postseason is its own line, and the phantom 1993 season shares its rows with
# 1994 (see coverage.py), so grouping loosely would merge two seasons into one.
_STINT_TOTALS = """
    str_stint_totals AS (
        SELECT athlete_id, season, season_type, COUNT(*) AS stints,
               {sums}
        FROM player_season_stats
        WHERE team_id IS NOT NULL
        GROUP BY athlete_id, season, season_type
    )
"""

# A combined row that disagrees with the stints it claims to combine. Keyed on
# the disagreement rather than on a list of athletes, so a second run writes
# the same numbers and a newly-pulled bad row is caught without a code change.
# IS DISTINCT FROM so an all-NULL combined row counts as disagreeing.
_BROKEN = "(t.team_id IS NULL AND s.stints IS NOT NULL AND (t.gamesPlayed IS DISTINCT FROM s.s_gamesPlayed OR t.points IS DISTINCT FROM s.s_points))"

# Guards division by a zero or NULL denominator. DuckDB returns inf for x/0 in
# floating point, which would be written into a column ESPN fills with a real
# number - see the module docstring on why NULL is the honest content.
_OVER = "CASE WHEN COALESCE({denominator}, 0) = 0 THEN NULL ELSE {expression} END"


def _replacements(present: set[str]) -> list[tuple[str, str]]:
    """Each repairable column and the SQL that replaces it, stored value first.

    A column absent from the loaded table is dropped, so a thin fixture repairs
    what it has instead of failing.
    """
    fixes: list[tuple[str, str]] = []
    for column in TOTAL_COLUMNS:
        fixes.append((column, f"CASE WHEN {_BROKEN} THEN s.s_{column} ELSE t.{column} END"))
    for column, total in AVERAGE_COLUMNS:
        per_game = _OVER.format(denominator="s.s_gamesPlayed", expression=f"round(CAST(s.s_{total} AS DOUBLE) / s.s_gamesPlayed, 1)")
        fixes.append((column, f"CASE WHEN {_BROKEN} THEN {per_game} ELSE t.{column} END"))
    for column, made, attempted in PCT_COLUMNS:
        pct = _OVER.format(denominator=f"s.s_{attempted}", expression=f"round(100.0 * s.s_{made} / s.s_{attempted}, 1)")
        fixes.append((column, f"CASE WHEN {_BROKEN} THEN {pct} ELSE t.{column} END"))
    ratios = (
        ("assistTurnoverRatio", "s.s_turnovers", "round(CAST(s.s_assists AS DOUBLE) / s.s_turnovers, 1)"),
        ("stealTurnoverRatio", "s.s_turnovers", "round(CAST(s.s_steals AS DOUBLE) / s.s_turnovers, 1)"),
        ("scoringEfficiency", "s.s_fieldGoalsAttempted", "round(CAST(s.s_points AS DOUBLE) / s.s_fieldGoalsAttempted, 3)"),
        ("shootingEfficiency", "s.s_fieldGoalsAttempted", "round((s.s_fieldGoalsMade + 0.5 * s.s_threePointFieldGoalsMade) / CAST(s.s_fieldGoalsAttempted AS DOUBLE), 2)"),
    )
    for column, denominator, expression in ratios:
        guarded = _OVER.format(denominator=denominator, expression=expression)
        fixes.append((column, f"CASE WHEN {_BROKEN} THEN {guarded} ELSE t.{column} END"))
    # The one average with no total behind it. See the module docstring: both
    # approximations were fitted against the warehouse and both were rejected.
    fixes.append(("avgMinutes", f"CASE WHEN {_BROKEN} THEN NULL ELSE t.avgMinutes END"))
    return [(column, sql) for column, sql in fixes if column in present]


def repair(con: duckdb.DuckDBPyConnection, loaded: set[str]) -> None:
    """Rewrite ``player_season_stats`` with broken combined rows rebuilt.

    Skipped with a logged reason when the table is absent or missing a column
    the rebuild is keyed on, rather than failing the whole warehouse build.
    Safe to run on every build and on a partial ``data load``: it is keyed on
    whether a combined row disagrees with its own stints, so a second run
    writes the same numbers.

    .. versionadded:: 2.2.0
    """
    if "player_season_stats" not in loaded:
        log.info("skip season totals repair (player_season_stats not loaded)")
        return
    present = {row[0] for row in con.execute("DESCRIBE player_season_stats").fetchall()}
    missing = _REQUIRED_COLUMNS - present
    if missing:
        log.info("skip season totals repair (missing columns: %s)", ", ".join(sorted(missing)))
        return

    summed = [column for column in TOTAL_COLUMNS if column in present]
    sums = ", ".join(f"SUM({column}) AS s_{column}" for column in summed)
    fixes = _replacements(present)
    if not fixes:
        log.info("skip season totals repair (none of the affected columns are loaded)")
        return

    broken = con.execute(f"""
        WITH {_STINT_TOTALS.format(sums=sums)}
        SELECT COUNT(*) FROM player_season_stats t
        LEFT JOIN str_stint_totals s USING (athlete_id, season, season_type)
        WHERE {_BROKEN}
    """).fetchone()

    # Column names are literals from this module, never a slot. SELECT * REPLACE
    # keeps every other column, and its position, so a column added to the
    # parser later survives without this having to know about it.
    replacements = ", ".join(f"{sql} AS {column}" for column, sql in fixes)
    con.execute(f"""
        CREATE OR REPLACE TABLE player_season_stats AS
        WITH {_STINT_TOTALS.format(sums=sums)}
        SELECT t.* REPLACE ({replacements})
        FROM player_season_stats t
        LEFT JOIN str_stint_totals s USING (athlete_id, season, season_type)
    """)
    log.info("season totals repair applied: %d combined rows rebuilt from their stints", broken[0] if broken else 0)
