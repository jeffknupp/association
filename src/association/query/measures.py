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
