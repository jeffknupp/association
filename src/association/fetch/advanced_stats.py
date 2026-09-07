"""Player-level advanced stats, computed as DuckDB views over player_box_stats
rather than fetched.

Confirmed live: ESPN's own team season-stats endpoint already carries
effectiveFGPct/trueShootingPct/paceFactor natively, but its player career-stats
endpoint has no equivalent (avgPoints/avgAssists/... and ESPN's own
scoringEfficiency/shootingEfficiency, nothing else) - so only the player side
has a real gap to fill.

Everything here is an exact, closed-form box-score formula (True Shooting %,
Effective FG%, Usage Rate, Hollinger Game Score) - no season/league-wide
baseline is needed to compute it, so there's nothing here that can drift out
of sync with a moving league average. Deliberately NOT included: PER, Win
Shares, BPM, VORP - those require position estimates and league-average
pace/efficiency/replacement-level baselines layered on top of a formula this
size, which is real, correctness-critical complexity a closed-form ratio
doesn't have. Treat that as a known, deliberate gap (like Real Plus-Minus in
the README), not an oversight.

Always built (no opt-in flag) since these views cost nothing beyond a
CREATE VIEW over data already fetched - no extra network request, no extra
storage. That these rows are DERIVED rather than sourced from ESPN directly
is still made visible (the query engine's TABLE_SUMMARY labels them
"COMPUTED", and this module's own name says the same), just not gated
behind a flag someone has to remember to pass.
"""

from __future__ import annotations

import logging

import duckdb

log = logging.getLogger("association.fetch.advanced_stats")

VIEWS = ["player_advanced_stats", "player_season_advanced_stats"]

# Every column the two formulas below reference. Now that build_views() always
# runs (no --advanced-stats opt-in to isolate a bad build to just these two
# views), a minimal/partial player_box_stats (e.g. a `data load --tables`
# subset fixture, or a genuinely incomplete ESPN response) must not crash the
# WHOLE warehouse build - checked up front so a missing column skips just
# these views, the same as player_box_stats being absent entirely already did.
_REQUIRED_COLUMNS = {
    "event_id",
    "season",
    "season_type",
    "team_id",
    "athlete_id",
    "minutes",
    "points",
    "fieldGoalsMade",
    "fieldGoalsAttempted",
    "threePointFieldGoalsMade",
    "freeThrowsMade",
    "freeThrowsAttempted",
    "offensiveRebounds",
    "defensiveRebounds",
    "steals",
    "assists",
    "blocks",
    "fouls",
    "turnovers",
}

# Usage % needs each player's team's totals for the same game. Every team's
# player-minutes always sum to exactly 5x game minutes (5 players on court at
# all times), so team totals can be derived by aggregating player_box_stats
# itself - no join to team_box_stats (or its differently-shaped column names)
# needed.
_TEAM_TOTALS_CTE = """
    team_totals AS (
        SELECT event_id, team_id,
               SUM(minutes) AS team_minutes,
               SUM(fieldGoalsAttempted) AS team_fga,
               SUM(freeThrowsAttempted) AS team_fta,
               SUM(turnovers) AS team_tov
        FROM player_box_stats
        GROUP BY event_id, team_id
    )
"""

_GAME_SCORE_EXPR = """
    pbs.points + 0.4 * pbs.fieldGoalsMade - 0.7 * pbs.fieldGoalsAttempted
        - 0.4 * (pbs.freeThrowsAttempted - pbs.freeThrowsMade)
        + 0.7 * pbs.offensiveRebounds + 0.3 * pbs.defensiveRebounds
        + pbs.steals + 0.7 * pbs.assists + 0.7 * pbs.blocks - 0.4 * pbs.fouls - pbs.turnovers
"""


def build_views(con: duckdb.DuckDBPyConnection, loaded: set[str]) -> None:
    """Create the computed advanced-stat views (TS%, eFG%, usage, game score).

    Skipped with a logged reason when ``player_box_stats`` is absent or missing
    a column the formulas need, rather than failing the whole warehouse build.
    """
    if "player_box_stats" not in loaded:
        log.info("skip advanced stats views (player_box_stats not loaded)")
        return

    existing_columns = {r[0] for r in con.execute("DESCRIBE player_box_stats").fetchall()}
    missing = _REQUIRED_COLUMNS - existing_columns
    if missing:
        log.info("skip advanced stats views (player_box_stats missing columns: %s)", ", ".join(sorted(missing)))
        return

    con.execute(f"""
        CREATE OR REPLACE VIEW player_advanced_stats AS
        WITH {_TEAM_TOTALS_CTE}
        SELECT
            pbs.event_id,
            pbs.season,
            pbs.season_type,
            pbs.team_id,
            pbs.athlete_id,
            CAST(CASE WHEN (pbs.fieldGoalsAttempted + 0.44 * pbs.freeThrowsAttempted) > 0
                 THEN pbs.points / (2.0 * (pbs.fieldGoalsAttempted + 0.44 * pbs.freeThrowsAttempted)) END AS DOUBLE) AS ts_pct,
            CAST(CASE WHEN pbs.fieldGoalsAttempted > 0
                 THEN (pbs.fieldGoalsMade + 0.5 * pbs.threePointFieldGoalsMade) / pbs.fieldGoalsAttempted END AS DOUBLE) AS efg_pct,
            CAST(CASE WHEN pbs.minutes > 0 AND tt.team_minutes > 0
                      AND (tt.team_fga + 0.44 * tt.team_fta + tt.team_tov) > 0
                 THEN 100.0 * ((pbs.fieldGoalsAttempted + 0.44 * pbs.freeThrowsAttempted + pbs.turnovers) * (tt.team_minutes / 5.0))
                      / (pbs.minutes * (tt.team_fga + 0.44 * tt.team_fta + tt.team_tov)) END AS DOUBLE) AS usage_pct,
            CAST({_GAME_SCORE_EXPR} AS DOUBLE) AS game_score
        FROM player_box_stats pbs
        JOIN team_totals tt ON tt.event_id = pbs.event_id AND tt.team_id = pbs.team_id
    """)

    # Season aggregate: rate stats are summed-then-divided (season TS% from
    # season FGA/FTA/points totals), not averaged game-to-game - averaging a
    # ratio across games of wildly different attempt volume overweights low-
    # volume games relative to the standard season-total definition.
    con.execute(f"""
        CREATE OR REPLACE VIEW player_season_advanced_stats AS
        WITH {_TEAM_TOTALS_CTE}
        SELECT
            pbs.season,
            pbs.season_type,
            pbs.athlete_id,
            COUNT(pbs.points) AS games_played,
            CAST(SUM(pbs.points) / NULLIF(2.0 * (SUM(pbs.fieldGoalsAttempted) + 0.44 * SUM(pbs.freeThrowsAttempted)), 0) AS DOUBLE) AS ts_pct,
            CAST((SUM(pbs.fieldGoalsMade) + 0.5 * SUM(pbs.threePointFieldGoalsMade)) / NULLIF(SUM(pbs.fieldGoalsAttempted), 0) AS DOUBLE) AS efg_pct,
            CAST(100.0 * SUM((pbs.fieldGoalsAttempted + 0.44 * pbs.freeThrowsAttempted + pbs.turnovers) * (tt.team_minutes / 5.0))
                / NULLIF(SUM(pbs.minutes * (tt.team_fga + 0.44 * tt.team_fta + tt.team_tov)), 0) AS DOUBLE) AS usage_pct,
            CAST(AVG({_GAME_SCORE_EXPR}) AS DOUBLE) AS avg_game_score
        FROM player_box_stats pbs
        JOIN team_totals tt ON tt.event_id = pbs.event_id AND tt.team_id = pbs.team_id
        GROUP BY pbs.season, pbs.season_type, pbs.athlete_id
    """)
    log.info("advanced stats views built: %s", ", ".join(VIEWS))
