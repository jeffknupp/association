"""The words a question uses for a box-score column.

One definition, read by two stages that may not import each other:
``query/lines.py`` turns a phrase into a line on a column
("under 14 fta"), and ``query/router.py`` reads the stat beside a threshold
("20+ points"). `router.py` imports nothing from the answer side on purpose -
the stage before it must not be made to depend on it - so before this
module existed the router kept its own copy, and nothing checked that the two
agreed (`ISSUES.md` #164).

.. versionadded:: 4.4.0
"""

from __future__ import annotations

# The words a question calls a column by are the lexicon's (Phase 3, step 2,
# the line); re-exported here under the name every column list is built from.
from association.query.lexicon import MEASURE_WORDS

# The words a question uses for a TEAM metric are the lexicon's
# (lexicon.TEAM_METRIC_WORDS, Phase 3, step 2: the measure); the flat table
# is re-exported here under the name team_metrics keys its catalog by.
from association.query.lexicon import STAT_ALIASES as STAT_ALIASES
from association.query.measure import column_name, measure_name, metric_name
from association.query.reading import Cause, Measure, PointRefused

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


TEAM_PERIOD_COLUMNS: tuple[str, ...] = (
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
"""The columns a period-narrowed team read carries, under the player line's
names: ``points`` from the linescore, the rest rebuilt by
:func:`~association.query.team_games.team_period_line_sql` (where the comment above it says how each is measured).

.. versionadded:: 5.0.0

.. versionchanged:: 6.0.0
   The reader's vocabulary, moved here from ``team_games`` (which re-exports
   it): the parser reads it to refuse a team's quarter of a stat nothing
   rebuilds.
"""


# The measures, by name - what the point reader reads a question's words into
# and the compiler computes. The names are closed here, and the SQL that
# computes each lives where it runs (compose.core.DERIVED, compose.team's
# GAME_MEASURES and SEASON_MEASURES, season_line.HISTORY_COLUMNS), keyed
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

DERIVED_MEASURES: frozenset[str] = frozenset({"pra", "fg_pct", "three_pct", "ft_pct", "double_double", "triple_double", "won", "home", "fouled_out", "margin", "two_pct", "opponent_name"})
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

LABEL_MEASURES: frozenset[str] = frozenset({"opponent_name"})
"""Measures that name something about a game rather than count it - listed
beside a game's figures, never averaged, summed, counted or read as a stat
a question asks about.

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
(:data:`association.query.season_line.HISTORY_COLUMNS` says the columns and labels).

.. versionadded:: 5.0.0
"""


def resolve_metric(measure: Measure | None, *, career: bool = False) -> str | None:
    """The ranking metric ``measure`` names, through the catalog
    (:func:`~association.query.measure.metric_name`): a bare box-score name
    reads as the record books do (a scoring title is per game, a make a
    season count), as the career TOTAL with ``career``, and a form asked
    for outright (per game, a total, a NetPoints rate) by that form. An
    unmapped measure returns None and the question is refused - never
    matched to whichever metric happened to score highest, which is the
    substitution this architecture exists to prevent.

    .. versionchanged:: 2.1.0
       Added ``career``, and an alias for every stat name the router is taught.

    .. versionchanged:: 5.0.0
       Lives in ``measures`` with its alias tables (``leaderboard`` re-exports them).

    .. versionchanged:: 6.0.0
       Takes the typed :class:`~association.query.reading.Measure`; the
       alias tables it read (the metric and career-metric aliases) are the
       catalog's facets (Phase 3, step 2).
    """
    return metric_name(measure, career=career)


def stat_measure(measure: Measure | None) -> str | None:
    """The compiler's name for ``measure`` over the games relation - a
    game-log column or a derived measure - through the catalog
    (:func:`~association.query.measure.measure_name`).

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Takes the typed :class:`~association.query.reading.Measure`; the
       measure alias table it read is the catalog's facet.
    """
    return measure_name(measure)


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
(``player_games.THRESHOLD_STAT_COLUMNS`` says the columns).

.. versionadded:: 5.0.0
"""

STREAK_RESULT_STATS: frozenset[str] = frozenset({"win", "wins", "winning", "loss", "losses", "losing", "streak", "streaks", "record", "games", "winning streak", "losing streak"})
"""What the model tends to put in the required ``stat`` slot for "longest
winning streak". Anything else is a stat, and a stat with no threshold is
refused rather than read as a win streak - "most consecutive double-doubles"
must not come back as the Lakers' best run of wins.

.. versionadded:: 5.0.0
   On the reader's side (``templates.splits._RESULT_STATS`` was this).
"""


def streak_column(measure: Measure | None, threshold: int | None) -> str | None:
    """The per-game column a streak holds a line on (``None`` for a run of
    wins or losses), read from the question's measure and threshold. Raises
    :class:`~association.query.reading.PointRefused` (by the missing fact) for a named stat with no
    per-game column, or a stat/threshold pair that only half-names a
    condition - "most consecutive double-doubles" must not come back as a
    win streak.

    .. versionadded:: 5.0.0
       On the reader's side (``templates.splits._streak_kind`` was this).

    .. versionchanged:: 6.0.0
       Takes the typed :class:`~association.query.reading.Measure`.
    """
    stat = measure.as_typed if measure is not None else None
    column = measure.key if measure is not None and measure.key in THRESHOLD_STAT_NAMES and measure.whose == "own" else None
    named_stat = stat is not None and bool(stat.strip()) and stat.strip().casefold() not in STREAK_RESULT_STATS
    if named_stat and column is None:
        raise PointRefused(Cause(kind="unknown_stat", facts={"intent": "streak", "stat": stat}), f"no per-game column for stat {stat!r}")
    message = f"a streak of a stat needs both a known stat and a positive threshold, got {stat!r}/{threshold!r}"
    if column is not None and threshold is None:
        raise PointRefused(Cause(kind="needs_threshold", facts={"intent": "streak", "stat": column}), message)
    if column is None and threshold is not None:
        raise PointRefused(Cause(kind="threshold_needs_stat", facts={"intent": "streak", "threshold": threshold}), message)
    if threshold is not None and threshold < 1:
        raise PointRefused(Cause(kind="threshold_counts_every_game", facts={"intent": "streak", "threshold": threshold}), message)
    return column


PLAYER_STAT_NAMES: frozenset[str] = frozenset({"turnovers", "fouls", "rebounds", "threePointFieldGoalsMade", "fieldGoalsMade", "points", "freeThrowsMade", "minutes", "steals", "blocks", "assists"})
"""The stats the season line reads for one player, by name
(``season_line.PLAYER_STAT_COLUMNS`` says the columns).

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
compiler's - mapped to its :data:`~association.query.player_games.PERIOD_RATES` key. A two-point percentage,
TS% and eFG% are not here: nothing measures them by period yet, and a
question asking for one is refused naming what is.

.. versionadded:: 5.0.0
"""


def period_split_measure(measure: Measure | None) -> str:
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
       Lives in ``measures``, on the reader's side (``templates.games._period_split_measure`` was this).

    .. versionchanged:: 6.0.0
       Takes the typed :class:`~association.query.reading.Measure`, read by its key.
    """
    if measure is None or measure.as_typed is None or measure.as_typed == "all":
        # "all" is a model-era filler for "the whole line" (a test's payload), read as points.
        return "points"
    key = measure.key if measure.whose == "own" else None
    if key in PERIOD_COLUMNS:
        return key
    if key in PERIOD_RATE_STATS:
        return PERIOD_RATE_STATS[key]
    raise PointRefused(
        Cause(kind="no_period_stat", facts={"stat": measure.as_typed}),
        f"period_split has no per-period {measure.as_typed!r} - the period's line rebuilds {', '.join(PERIOD_COLUMNS)} from the plays, "
        "and a field goal, 3-point or free throw percentage is a ratio of those; nothing else",
    )


def stat_column(measure: Measure | None) -> str | None:
    """The stored game-log column ``measure`` is read from, through the
    catalog (:func:`~association.query.measure.column_name`) - or None.

    .. versionadded:: 5.0.0
       On the reader's side (``compose.adapt._stat_column`` was this).

    .. versionchanged:: 6.0.0
       Takes the typed :class:`~association.query.reading.Measure`.
    """
    return column_name(measure)


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


def log_extras(measure: Measure | None) -> tuple[str, ...]:
    """The columns a named measure adds to a player's log.

    ``stat`` was required in ROUTER_SCHEMA, so the model filled it on every
    question, including ones that name no stat at all - a measure no column
    list holds adds nothing. A REAL stat the log has no column for refuses instead:
    "luka ts% log" answered with no TS% in it would be the narrower answer
    passed off as the one asked for.

    .. versionadded:: 5.0.0
       On the reader's side (``compose.logs._log_extras`` and ``templates.games._log_extras`` were this).

    .. versionchanged:: 6.0.0
       Takes the typed :class:`~association.query.reading.Measure`, read by its key.
    """
    if measure is None or measure.key is None or measure.whose != "own":
        return ()
    key = measure.key
    if key in GAME_LOG_STAT_COLUMNS:
        return GAME_LOG_STAT_COLUMNS[key]
    if key in PLAYER_STAT_NAMES or key in HISTORY_STATS or key in THRESHOLD_STAT_NAMES or resolve_metric(measure) is not None:
        raise PointRefused(Cause(kind="unknown_stat", facts={"intent": "game_log", "stat": measure.as_typed}), f"a game log has no per-game column for {measure.as_typed!r}")
    return ()
