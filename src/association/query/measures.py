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


# The measures, by name - what the point reader reads a question's words into
# and the compiler computes. The names are closed here, and the SQL that
# computes each lives where it runs (compose.core.DERIVED, compose.team's
# GAME_MEASURES and SEASON_MEASURES, templates.common.HISTORY_COLUMNS), keyed
# by exactly these names: tests/query/test_measures.py holds the two sides
# together. Moved here from those modules on 2026-10-02 (ROADMAP.md, Phase 1,
# the read_point move, step 2), where the reader imported the SQL to learn a
# name; each re-exports its old name.

GAME_COLUMNS: frozenset[str] = frozenset(MEASURE_WORDS.values()) | frozenset(
    {"plusMinus", "offensiveRebounds", "defensiveRebounds", "ts_pct", "efg_pct", "usage_pct", "game_score", "threePointFieldGoalsMade", "threePointFieldGoalsAttempted"}
)
"""Every column name a per-game measure may take straight from the game log.

.. versionadded:: 5.0.0
"""

DERIVED_MEASURES: frozenset[str] = frozenset({"pra", "fg_pct", "three_pct", "ft_pct", "double_double", "triple_double", "won", "home", "fouled_out"})
"""Measures computed from columns per game, by name; the SQL is
:data:`association.query.compose.core.DERIVED`'s.

.. versionadded:: 5.0.0
"""

DERIVED_LINES: dict[str, tuple[str, int]] = {"fouled_out": ("fouls", 6)}
"""A derived measure that IS a line on one column - ``fouled_out`` is six
fouls - so a count of it and a count over that line are one question
(``compose.move._move_boolean_count_is_line``). The compiler's SQL for each
says the same thing, and the test checks it.

.. versionadded:: 5.0.0
"""

BOOLEAN_MEASURES: frozenset[str] = frozenset({"triple_double", "double_double", "won", "fouled_out"})
"""Measures read as a per-game condition rather than a counted quantity.

.. versionadded:: 4.4.0

.. versionchanged:: 5.0.0
   Defined here; ``compose.core`` re-exports it.
"""

LINE: tuple[str, ...] = ("minutes", "points", "rebounds", "assists")
"""The default line a game log or single-game read carries.

.. versionadded:: 4.4.0

.. versionchanged:: 5.0.0
   Defined here; ``compose.core`` re-exports it.
"""

TEAM_GAME_MEASURES: frozenset[str] = frozenset({"points", "points_allowed", "differential"})
"""A team's game-level measures, by name (:data:`association.query.compose.team.GAME_MEASURES` computes them).

.. versionadded:: 5.0.0
"""

TEAM_SEASON_MEASURES: frozenset[str] = frozenset({"points", "rebounds", "assists", "steals", "blocks", "turnovers", "threePointFieldGoalsMade", "fieldGoalsMade", "freeThrowsMade", "fouls"})
"""A team's season-total measures, by name (:data:`association.query.compose.team.SEASON_MEASURES` reads them).

.. versionadded:: 5.0.0
"""

HISTORY_STATS: frozenset[str] = frozenset(
    {"threePointFieldGoalPct", "fieldGoalPct", "freeThrowPct", "twoPointFieldGoalPct", "points", "rebounds", "assists", "steals", "blocks", "minutes", "threePointFieldGoalsMade"}
)
"""The stats a season-by-season history exists for, by name
(:data:`association.query.templates.common.HISTORY_COLUMNS` says the columns and labels).

.. versionadded:: 5.0.0
"""
