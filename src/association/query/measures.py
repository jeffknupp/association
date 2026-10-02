"""The words a question uses for a box-score column.

One definition, read by two stages that may not import each other:
``query/templates/common.py`` turns a phrase into a line on a column
("under 14 fta"), and ``query/router.py`` reads the stat beside a threshold
("20+ points"). `router.py` imports nothing from `templates` on purpose - the
stage before the templates must not be made to depend on them - so before this
module existed the router kept its own copy, and nothing checked that the two
agreed (`ISSUES.md` #164).

.. versionadded:: 4.4.0
"""

from __future__ import annotations

#: What a question calls a box-score column, for a line it asks games to be
#: kept under or over. Keys are the question's words after the number,
#: casefolded; values are `player_game_log` columns and never question text.
MEASURE_WORDS: dict[str, str] = {
    "points": "points",
    "point": "points",
    "pts": "points",
    "pt": "points",
    "rebounds": "rebounds",
    "rebound": "rebounds",
    "reb": "rebounds",
    "rebs": "rebounds",
    "boards": "rebounds",
    "assists": "assists",
    "assist": "assists",
    "ast": "assists",
    "asts": "assists",
    "steals": "steals",
    "steal": "steals",
    "stl": "steals",
    "blocks": "blocks",
    "block": "blocks",
    "blk": "blocks",
    "turnovers": "turnovers",
    "turnover": "turnovers",
    "tov": "turnovers",
    "to": "turnovers",
    "fouls": "fouls",
    "foul": "fouls",
    "pf": "fouls",
    "minutes": "minutes",
    "minute": "minutes",
    "mins": "minutes",
    "min": "minutes",
    "fga": "fieldGoalsAttempted",
    "field goal attempts": "fieldGoalsAttempted",
    "shots": "fieldGoalsAttempted",
    "shot attempts": "fieldGoalsAttempted",
    "fgm": "fieldGoalsMade",
    "field goals": "fieldGoalsMade",
    "field goals made": "fieldGoalsMade",
    "fta": "freeThrowsAttempted",
    "free throw attempts": "freeThrowsAttempted",
    "free throws attempted": "freeThrowsAttempted",
    "ftm": "freeThrowsMade",
    "free throws": "freeThrowsMade",
    "free throws made": "freeThrowsMade",
    "3pa": "threePointFieldGoalsAttempted",
    "three point attempts": "threePointFieldGoalsAttempted",
    "threes attempted": "threePointFieldGoalsAttempted",
    "3pm": "threePointFieldGoalsMade",
    "3s": "threePointFieldGoalsMade",
    "threes": "threePointFieldGoalsMade",
    "3 pointers": "threePointFieldGoalsMade",
    "three pointers": "threePointFieldGoalsMade",
    "threes made": "threePointFieldGoalsMade",
    "oreb": "offensiveRebounds",
    "offensive rebounds": "offensiveRebounds",
    "dreb": "defensiveRebounds",
    "defensive rebounds": "defensiveRebounds",
}


# The words a question uses for a TEAM metric - moved here from
# team_metrics.py on 2026-10-02, where the catalog that ranks the metrics
# held them and the router imported the catalog to read a word (ROADMAP.md,
# Phase 1: the reader's imports of the answer side). team_metrics re-exports
# STAT_ALIASES under its old name and still keys its catalog by the same
# names.

_ALIASES: dict[str, tuple[str, ...]] = {
    "record": ("record", "records", "wins", "win", "win pct", "win percentage", "winning percentage", "win percent", "standings", "win loss", "win loss record"),
    "losses": ("losses", "loss", "losing record"),
    "points": ("points", "point", "ppg", "points per game", "scoring", "points scored", "avg points"),
    "opponent_points": (
        "opponent points",
        "opponent points per game",
        "opponent ppg",
        "opp points",
        "opp ppg",
        "points allowed",
        "points allowed per game",
        "points against",
        "points given up",
    ),
    "point_differential": ("point differential", "differential", "point diff", "margin", "point margin", "scoring margin", "margin of victory", "plus minus"),
    "pace": ("pace", "pace factor", "possessions", "possessions per game"),
    "offensive_rating": ("offensive rating", "off rating", "ortg", "offensive efficiency", "offensive rtg"),
    "defensive_rating": ("defensive rating", "def rating", "drtg", "defensive efficiency", "defensive rtg", "defense", "defensive"),
    "net_rating": ("net rating", "net efficiency", "net rtg", "nrtg"),
    "field_goal_pct": ("field goal pct", "field goal percentage", "fg pct", "fg%", "fg percentage", "field goal %", "shooting percentage"),
    "three_point_pct": (
        "three point field goal pct",
        "three point field goal percentage",
        "three point pct",
        "three point percentage",
        "3pt pct",
        "3pt%",
        "3pt percentage",
        "3 point pct",
        "3 point percentage",
        "3p%",
        "3p pct",
        "three point shooting",
    ),
    "free_throw_pct": ("free throw pct", "free throw percentage", "ft pct", "ft%", "free throw %"),
    "true_shooting_pct": ("ts pct", "ts%", "true shooting", "true shooting pct", "true shooting percentage"),
    "effective_fg_pct": ("efg pct", "efg%", "efg", "effective field goal pct", "effective field goal percentage", "effective fg pct"),
    "rebounds": ("rebounds", "rebound", "rpg", "total rebounds", "rebounding", "boards", "avg rebounds"),
    "offensive_rebounds": ("offensive rebounds", "offensive rebound", "oreb", "offensive boards"),
    "defensive_rebounds": ("defensive rebounds", "defensive rebound", "dreb", "defensive boards"),
    "assists": ("assists", "assist", "apg", "dimes", "avg assists"),
    "turnovers": ("turnovers", "turnover", "tov", "giveaways", "total turnovers"),
    "steals": ("steals", "steal", "spg"),
    "blocks": ("blocks", "block", "bpg", "blocked shots"),
    "fouls": ("fouls", "foul", "personal fouls"),
    "three_pointers_made": (
        "three point field goals made",
        "threes",
        "threes made",
        "three pointers",
        "three pointers made",
        "3 pointers",
        "3 pointers made",
        "3pm",
        "3pt made",
        "made threes",
    ),
    "three_pointers_attempted": ("three point field goals attempted", "three point attempts", "threes attempted", "3 point attempts", "3pa", "3pt attempts"),
    "field_goals_made": ("field goals made", "field goals", "fgm"),
    "free_throws_made": ("free throws made", "free throws", "ftm"),
    "free_throws_attempted": ("free throws attempted", "free throw attempts", "fta"),
    "points_in_paint": ("points in the paint", "points in paint", "paint points"),
    "fast_break_points": ("fast break points", "fastbreak points", "fast break"),
}

STAT_ALIASES: dict[str, str] = {alias: key for key, aliases in _ALIASES.items() for alias in aliases}
"""Normalized slot text -> :data:`TEAM_METRICS` key.

.. versionadded:: 2.1.0
"""


# The columns a quarter or half rebuilds - the period relation's own list,
# moved here from player_games.py on 2026-10-02 because the parser reads it
# to check a period condition's stat (ROADMAP.md, Phase 1); player_games
# re-exports it under its old name.

PERIOD_COLUMNS: tuple[str, ...] = (
    "points",
    "fieldGoalsMade",
    "fieldGoalsAttempted",
    "threePointFieldGoalsMade",
    "threePointFieldGoalsAttempted",
    "freeThrowsMade",
    "freeThrowsAttempted",
    "rebounds",
    "offensiveRebounds",
    "defensiveRebounds",
    "assists",
    "steals",
    "blocks",
    "turnovers",
    "fouls",
)
"""The box-score columns a period narrowing rebuilds from the shots and plays,
under their ``player_game_log`` names - the columns a reader of a narrowed
relation sees restricted to the period.

.. versionadded:: 5.0.0
"""
