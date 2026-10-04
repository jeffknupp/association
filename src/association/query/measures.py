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

from typing import Any

from association.query.metrics import LEADERBOARD_METRICS
from association.query.reading import Unsupported

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
(``point._move_boolean_count_is_line``). The compiler's SQL for each
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


# The router has one stat vocabulary (the `stat` line of ROUTER_PROMPT) for
# every intent, so each of its names needs a metric here: every one it taught
# that had none - turnovers, minutes, fouls, the three kinds of make, the three
# percentages - fell through to the agent. Keys are casefolded.
#
# Which reading a bare name gets follows the record books. The five a scoring,
# rebounding, assist, steal or block title is decided on are per game, as they
# always were here; a make is a season COUNT ("most threes this season" is the
# 402-three kind of record, not a rate); turnovers, minutes and fouls are per
# game, the way a league leaderboard lists them. Every answer names which it
# ranked, and `rate` "total" asks for the other - see SEASON_TOTAL_OF.
METRIC_ALIASES = {
    "points": "avg_points",
    "rebounds": "avg_rebounds",
    "assists": "avg_assists",
    "steals": "avg_steals",
    "blocks": "avg_blocks",
    "turnovers": "avg_turnovers",
    "minutes": "avg_minutes",
    "fouls": "avg_fouls",
    "threepointfieldgoalsmade": "total_three_pointers_made",
    "fieldgoalsmade": "total_field_goals_made",
    "freethrowsmade": "total_free_throws_made",
    "threepointfieldgoalpct": "three_pt_pct",
    "fieldgoalpct": "fg_pct",
    "freethrowpct": "ft_pct",
    "double_double": "double_doubles",
    "triple_double": "triple_doubles",
    "netpoints": "netpoints_total",
    "true_shooting": "ts_pct",
    "usage": "usage_pct",
}

CAREER_METRIC_ALIASES = {
    "points": "total_points",
    "rebounds": "total_rebounds",
    "assists": "total_assists",
    "steals": "total_steals",
    "blocks": "total_blocks",
    "turnovers": "total_turnovers",
}
"""How a bare stat name reads in a CAREER ranking, where it differs.

A career list is a list of totals: "career points leaders" is the all-time
scoring list LeBron James tops at 43,440, not Michael Jordan's 30.1 a game.
Names absent here read as they do for a season (minutes and fouls have no
career-total metric, so they stay per game).

.. versionadded:: 2.1.0
"""


def resolve_metric(name: str | None, *, career: bool = False) -> str | None:
    """Router slot -> a real metric name, via an EXPLICIT alias table.

    Deliberately not get_close_matches: fuzzy matching is fine for suggesting a
    fix a person can act on, but a template silently ranking by whichever
    metric happened to score highest is exactly the substitution failure this
    architecture exists to prevent. An unmapped name returns None and the
    question is refused.

    With ``career``, a bare box-score name reads as the career TOTAL - see
    ``CAREER_METRIC_ALIASES``. A real metric name is never reinterpreted, so a
    career average stays reachable as ``avg_points``.

    .. versionchanged:: 2.1.0
       Added ``career``, and an alias for every stat name the router is taught.

    .. versionchanged:: 5.0.0
       Lives in ``measures`` with its alias tables (``leaderboard`` re-exports them).
    """
    if not isinstance(name, str):
        return None
    if name in LEADERBOARD_METRICS:
        return name
    key = name.strip().casefold()
    if career and key in CAREER_METRIC_ALIASES:
        return CAREER_METRIC_ALIASES[key]
    return METRIC_ALIASES.get(key)


#: The router's own stat names that are not relation columns, as measures.
MEASURE_ALIASES: dict[str, str] = {
    "ts_pct": "ts_pct",
    "true_shooting": "ts_pct",
    "efg_pct": "efg_pct",
    "usage_pct": "usage_pct",
    "game_score": "game_score",
    "plus_minus": "plusMinus",
    "plusMinus": "plusMinus",
    "threePointFieldGoalPct": "three_pct",
    "three_point_pct": "three_pct",
    "fieldGoalPct": "fg_pct",
    "fg_pct": "fg_pct",
    "freeThrowPct": "ft_pct",
    "points_per_game": "points",
    "rebounds_per_game": "rebounds",
    "assists_per_game": "assists",
    "triple_double": "triple_double",
    "triple_doubles": "triple_double",
    "double_double": "double_double",
    "double_doubles": "double_double",
    "pra": "pra",
    "wins": "won",
}
"""A router ``stat`` value that names a measure this package computes rather
than a stored column, mapped to that measure's name.

.. versionadded:: 4.4.0
"""

#: Words in the question for a measure the router may not have named.
WORD_MEASURES: list[tuple[str, str]] = [
    (r"\bts ?%|\btrue shooting\b", "ts_pct"),
    (r"\befg\b|\beffective field goal", "efg_pct"),
    (r"\bplus[ /-]?minus\b|\+/-", "plusMinus"),
    (r"\bgame score\b", "game_score"),
    (r"\busage\b", "usage_pct"),
    (r"\btriple[ -]?doubles?\b|\btd3s?\b|\btds\b", "triple_double"),
    (r"\bdouble[ -]?doubles?\b|\bdd\b", "double_double"),
    (r"\bfg ?%|\bfg percentage\b|\bfield goal percentage\b", "fg_pct"),
    (r"\b3 ?pt ?%|\b3 point percentage\b|\bthree point percentage\b|\b3p%", "three_pct"),
    (r"\bft ?%|\bfree throw percentage\b", "ft_pct"),
    (r"\bpra\b|\bpts\+reb\+ast\b|points\+rebounds\+assists", "pra"),
    (r"\bfouled out\b|\bfoul(ed)? outs?\b", "fouled_out"),
]
"""``(pattern, measure)`` - a phrase the question carries that names a measure directly.

.. versionadded:: 4.4.0
"""


def stat_measure(stat: str | None) -> str | None:
    """A router ``stat`` as a measure this package knows, through
    :data:`MEASURE_ALIASES` and then :data:`MEASURE_WORDS`.

    .. versionadded:: 5.0.0
    """
    if stat is None or not stat.strip():
        return None
    if stat in MEASURE_ALIASES:
        return MEASURE_ALIASES[stat]
    if stat in GAME_COLUMNS or stat in DERIVED_MEASURES:
        return stat
    return MEASURE_WORDS.get(stat.strip().lower())


STAT_LINE: tuple[str, ...] = ("points", "rebounds", "assists")
"""The season line a bare ``player_stat`` question reads, by name.

.. versionadded:: 5.0.0
"""

SPLIT_LINE: tuple[str, ...] = ("minutes", "points", "rebounds", "assists", "steals", "blocks", "turnovers", "threePointFieldGoalsMade", "fg_pct")
"""The measures a ``player_splits`` read carries, by name.

.. versionadded:: 5.0.0
"""

THRESHOLD_STAT_NAMES: frozenset[str] = frozenset({"turnovers", "fouls", "rebounds", "threePointFieldGoalsMade", "fieldGoalsMade", "points", "freeThrowsMade", "minutes", "steals", "blocks", "assists"})
"""The stats a count or a record over a line reads, by name
(``templates.common.THRESHOLD_STAT_COLUMNS`` says the columns).

.. versionadded:: 5.0.0
"""

PLAYER_STAT_NAMES: frozenset[str] = frozenset({"turnovers", "fouls", "rebounds", "threePointFieldGoalsMade", "fieldGoalsMade", "points", "freeThrowsMade", "minutes", "steals", "blocks", "assists"})
"""The stats the season line reads for one player, by name
(``templates.common.PLAYER_STAT_COLUMNS`` says the columns).

.. versionadded:: 5.0.0
"""

PERIOD_RATE_STATS: dict[str, str] = {
    "fieldGoalPct": "fg_pct",
    "fg_pct": "fg_pct",
    "threePointFieldGoalPct": "three_pct",
    "three_pct": "three_pct",
    "freeThrowPct": "ft_pct",
    "ft_pct": "ft_pct",
}
"""A ``stat`` naming a shooting percentage - the normalizer's spelling or the
compiler's - mapped to its :data:`PERIOD_RATES` key. A two-point percentage,
TS% and eFG% are not here: nothing measures them by period yet, and a
question asking for one is refused naming what is.

.. versionadded:: 5.0.0
"""


def period_split_measure(stat: str | None) -> str:
    """The column a period question measures: points where it names none,
    else the one it names - any column the period's line rebuilds
    (:data:`~association.query.player_games.PERIOD_COLUMNS`), or a shooting
    percentage over two of them (:data:`PERIOD_RATE_STATS`). Anything else
    is refused, naming why: play-by-play records no minutes, plus-minus or
    advanced rate per quarter, and a period answer read from the whole
    game's box would be the wrong question answered fluently.

    .. versionchanged:: 5.0.0
       Reads a field goal, 3-point or free throw percentage.

    .. versionchanged:: 5.0.0
       Lives in ``measures``, on the reader's side (``templates.games._period_split_measure`` is this).
    """
    if stat is None or not stat.strip() or stat == "all":
        return "points"
    if stat in PERIOD_COLUMNS:
        return stat
    if stat in PERIOD_RATE_STATS:
        return PERIOD_RATE_STATS[stat]
    raise Unsupported(
        f"period_split has no per-period {stat!r} - the period's line rebuilds {', '.join(PERIOD_COLUMNS)} from the plays, "
        "and a field goal, 3-point or free throw percentage is a ratio of those; nothing else"
    )


def stat_column(stat: str | None) -> str | None:
    """A router ``stat`` as a relation column - the router's own column names
    (``points``, ``threePointFieldGoalsMade``) or a word :data:`MEASURE_WORDS`
    knows - or ``None``.

    .. versionadded:: 5.0.0
       On the reader's side (``compose.adapt._stat_column`` was this).
    """
    if stat is None or not stat.strip():
        return None
    if stat in GAME_COLUMNS:
        return stat
    return MEASURE_WORDS.get(stat.strip().lower())


GAME_LOG_STAT_COLUMNS: dict[str, tuple[str, ...]] = {
    "points": (),
    "rebounds": (),
    "assists": (),
    "minutes": (),
    "steals": ("STL",),
    "blocks": ("BLK",),
    "turnovers": ("TO",),
    "fouls": ("PF",),
    "plusMinus": ("+/-",),
    "offensiveRebounds": ("OREB",),
    "defensiveRebounds": ("DREB",),
    "fieldGoalsMade": ("FGM", "FGA"),
    "fieldGoalsAttempted": ("FGM", "FGA"),
    "fieldGoalPct": ("FGM", "FGA", "FG%"),
    "threePointFieldGoalsMade": ("3PM", "3PA"),
    "threePointFieldGoalsAttempted": ("3PM", "3PA"),
    "threePointFieldGoalPct": ("3PM", "3PA", "3P%"),
    "freeThrowsMade": ("FTM", "FTA"),
    "freeThrowsAttempted": ("FTM", "FTA"),
    "freeThrowPct": ("FTM", "FTA", "FT%"),
}
"""The columns a named ``stat`` adds to a player's game log, beyond minutes,
points, rebounds and assists. A shooting stat always brings its makes and
attempts, and a percentage is computed from them per game.

.. versionadded:: 2.1.0

.. versionchanged:: 5.0.0
   Lives in ``measures``, on the reader's side.
"""


def log_extras(stat: Any) -> tuple[str, ...]:
    """The columns a named stat adds to a player's log.

    ``stat`` is required in ROUTER_SCHEMA, so the model fills it on every
    question, including ones that name no stat at all - text that is not a stat
    name adds nothing. A REAL stat the log has no column for refuses instead:
    "luka ts% log" answered with no TS% in it would be the narrower answer
    passed off as the one asked for.

    .. versionadded:: 5.0.0
       On the reader's side (``compose.logs._log_extras`` and ``templates.games._log_extras`` were this).
    """
    if not isinstance(stat, str) or not stat.strip():
        return ()
    if stat in GAME_LOG_STAT_COLUMNS:
        return GAME_LOG_STAT_COLUMNS[stat]
    if stat in PLAYER_STAT_NAMES or stat in HISTORY_STATS or stat in THRESHOLD_STAT_NAMES or resolve_metric(stat) is not None:
        raise Unsupported(f"a game log has no per-game column for {stat!r}")
    return ()
