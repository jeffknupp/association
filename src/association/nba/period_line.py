"""A player's line in a quarter or a half, rebuilt from the shots and the
plays: the rules that read a play as a stat, and the statement that sums
them per player per game (:func:`period_line_sql`).

Nothing in ESPN's box score is split by period, so a period's figure is
rebuilt; and the one independent check a rebuilt figure has is its game,
whose box score the line summed over every period must equal. The query
path reads a period through this line (``query.player_games``'s period
relation); the fetch path records, per player-game and per column, whether
that whole-game sum equals the box score
(:mod:`association.fetch.repairs.plays_complete`) - so the rules are defined
once, here, below both.

.. versionadded:: 7.0.0
   Moved here from :mod:`association.query.player_games` (the rules and
   :func:`period_line_sql`) and :mod:`association.query.measures`
   (:data:`PERIOD_COLUMNS`).
"""

from __future__ import annotations

from association.nba.shot_value import SHOT_VALUE_SQL

# The columns a quarter or half rebuilds. The parser reads them (a period
# condition's stat, through `query.measures`), the period relation reads them,
# and the completeness table is built over them.

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

.. versionchanged:: 7.0.0
   Lives here (was ``query.measures.PERIOD_COLUMNS``, which re-exports it).
"""


PERIOD_PLAYS_COLUMNS: frozenset[str] = frozenset({"rebounds", "offensiveRebounds", "defensiveRebounds", "assists", "steals", "blocks", "turnovers", "fouls"})
"""The :data:`PERIOD_COLUMNS` read from ``plays`` rather than ``shot_chart`` -
NULL on a period-narrowed row where the warehouse holds no play-by-play.

.. versionadded:: 5.0.0

.. versionchanged:: 7.0.0
   Lives here (was ``query.player_games.PERIOD_PLAYS_COLUMNS``).
"""


# The period's line (the query path reads a quarter or half as a NARROWING of
# the player-games relation, `query.player_games._period_source`). Nothing in
# ESPN's box score is split by period, so the period's line is rebuilt from
# the shots and the plays - points, field goals and free throws from
# `shot_chart` (points valued through SHOT_VALUE_SQL, never the play's prose -
# see period_split's docstring for why that is the whole accuracy), and
# rebounds, assists, steals, blocks, turnovers and fouls from `plays`, by the
# rules `fetch/repairs/reconstructed_box` measured, with three corrections
# measured here (every player-game 2002-2026 summed over all its
# periods against its own box score):
#
# - a missed end-of-period heave is not a field goal attempt. 2026 alone types
#   them `Heave Jump Shot`, and counting them put 95.8% of 2026's player-games
#   right on attempts; leaving them out, 99.6%.
# - `Traveling` is a turnover whose type does not say so: 1,366 of the 1,369
#   2026 player-games one turnover short held one. 95.2% -> 99.7%.
# - a foul is every `%Foul%` type but technicals, the turnover half of an
#   offensive foul (the foul itself is its own play) and `No Foul`, plus
#   `Offensive Charge`, `Shooting Block` and `Personal Block`, whose types never
#   say "foul": 2015 went from 88.6% to 99.7%, 2026 from 89.8% to 99.8%.
# - a `No Turnover` play whose text says "turnover" is one ("Andre Drummond
#   turnover": 436 of 2018's 439 plays of the type, 104 of 2019's 104 - but
#   not the 23 whose text is "Tyler Zeller no turnover Ray Allen"), and a
#   `Not Available` play whose text says "foul" is one (2018's 89 "personal
#   blocking foul"s), and so is one whose text says "turnover" (2016's 344,
#   "John Wall turnover"). Measured at team level, where one
#   missing play breaks a whole team-game: 2018 turnovers 85.4% -> 93.8%,
#   2019 92.6% -> 95.5%, 2022 95.7% -> 97.5%, 2016 60.6% -> 64.9%; 2018
#   fouls 80.7% -> 83.2%. 2016's 24 `Not Available` "offensive rebound"s are
#   NOT rebounds the box counts: read as rebounds, 2016's team rebounds went
#   from 98.4% to 97.9%.
#
# Assists, steals and blocks belong to the SECOND id in
# `participant_athlete_ids`, as in the rebuild; a foul's second id is the man
# who drew it, and is never credited.
PERIOD_FREE_THROW_SQL = "sc.shot_type ILIKE '%free throw%'"
"""Whether a ``shot_chart`` row (aliased ``sc``) is a free throw, as SQL.

.. versionadded:: 7.0.0
   Was ``player_games._PERIOD_FREE_THROW``.
"""

PERIOD_HEAVE_MISS_SQL = "(sc.shot_type ILIKE 'heave%' AND NOT sc.made)"
"""Whether a ``shot_chart`` row (aliased ``sc``) is a missed end-of-period
heave, which the box score counts as no attempt, as SQL.

.. versionadded:: 7.0.0
   Was ``player_games._PERIOD_HEAVE_MISS``.
"""

PERIOD_TURNOVER_SQL = (
    "((p.type ILIKE '%Turnover%' AND p.type <> 'No Turnover') OR p.type = 'Traveling' "
    "OR (p.type IN ('No Turnover', 'Not Available') AND p.text ILIKE '%turnover%' AND p.text NOT ILIKE '%no turnover%'))"
)
"""Whether a ``plays`` row (aliased ``p``) is a turnover its first
participant committed, as SQL.

.. versionadded:: 7.0.0
   Was ``player_games._PERIOD_TURNOVER``.
"""

PERIOD_FOUL_SQL = (
    "((p.type ILIKE '%Foul%' AND p.type NOT ILIKE '%Technical%' AND p.type NOT ILIKE '%Turnover%' AND p.type <> 'No Foul') "
    "OR p.type IN ('Offensive Charge', 'Shooting Block', 'Personal Block') OR (p.type = 'Not Available' AND p.text ILIKE '%foul%'))"
)
"""Whether a ``plays`` row (aliased ``p``) is a personal foul its first
participant committed, as SQL.

.. versionadded:: 7.0.0
   Was ``player_games._PERIOD_FOUL``.
"""


def period_line_sql(periods: tuple[int, ...] | None, games: str, *, plays: bool = True) -> str:
    """One row per player per game - ``event_id``, ``season``,
    ``athlete_id`` and every :data:`PERIOD_COLUMNS` column - summed over
    ``periods`` (every period, overtime included, when ``None``), for the
    games ``games`` names: SQL selecting ``event_id, season`` pairs, which
    keeps the scan to the narrowed games rather than 14 million plays.

    The periods are written into the SQL as integers, never bound, so the
    statement's parameters stay exactly the ones ``games`` carries. With
    ``plays`` false (a warehouse loaded without play-by-play) only the shot
    columns are read, and every column the plays carry is NULL - unknown,
    never a zero a reader could mistake for a quiet quarter.

    .. versionadded:: 5.0.0

    .. versionchanged:: 7.0.0
       Lives here (was ``query.player_games.period_line_sql``), so the fetch
       path's completeness table sums the same line.
    """
    if periods is not None and not all(isinstance(p, int) and 1 <= p <= 10 for p in periods):
        raise ValueError(f"periods must be integers 1-10, got {periods!r}")
    in_periods = "TRUE" if periods is None else f"{{alias}}.period IN ({', '.join(str(p) for p in periods)})"
    zero = "0 AS offensiveRebounds, 0 AS defensiveRebounds, 0 AS turnovers, 0 AS fouls, 0 AS assists, 0 AS steals, 0 AS blocks"
    no_shots = "0 AS points, 0 AS fieldGoalsMade, 0 AS fieldGoalsAttempted, 0 AS threePointFieldGoalsMade, 0 AS threePointFieldGoalsAttempted, 0 AS freeThrowsMade, 0 AS freeThrowsAttempted"
    shots = f"""
        SELECT sc.event_id, sc.season, sc.athlete_id,
            SUM(CASE WHEN sc.made THEN {SHOT_VALUE_SQL} ELSE 0 END) AS points,
            SUM(CASE WHEN sc.made AND NOT {PERIOD_FREE_THROW_SQL} THEN 1 ELSE 0 END) AS fieldGoalsMade,
            SUM(CASE WHEN NOT {PERIOD_FREE_THROW_SQL} AND NOT {PERIOD_HEAVE_MISS_SQL} THEN 1 ELSE 0 END) AS fieldGoalsAttempted,
            SUM(CASE WHEN sc.made AND {SHOT_VALUE_SQL} = 3 THEN 1 ELSE 0 END) AS threePointFieldGoalsMade,
            SUM(CASE WHEN {SHOT_VALUE_SQL} = 3 AND NOT {PERIOD_HEAVE_MISS_SQL} THEN 1 ELSE 0 END) AS threePointFieldGoalsAttempted,
            SUM(CASE WHEN sc.made AND {PERIOD_FREE_THROW_SQL} THEN 1 ELSE 0 END) AS freeThrowsMade,
            SUM(CASE WHEN {PERIOD_FREE_THROW_SQL} THEN 1 ELSE 0 END) AS freeThrowsAttempted,
            {zero}
        FROM shot_chart sc
        WHERE EXISTS (SELECT 1 FROM _period_keys k WHERE k.event_id = sc.event_id AND k.season = sc.season) AND sc.athlete_id IS NOT NULL AND {in_periods.format(alias="sc")}
        GROUP BY sc.event_id, sc.season, sc.athlete_id"""
    actor = f"""
        SELECT p.event_id, p.season, p.athlete_id, {no_shots},
            SUM(CASE WHEN p.type = 'Offensive Rebound' THEN 1 ELSE 0 END) AS offensiveRebounds,
            SUM(CASE WHEN p.type = 'Defensive Rebound' THEN 1 ELSE 0 END) AS defensiveRebounds,
            SUM(CASE WHEN {PERIOD_TURNOVER_SQL} THEN 1 ELSE 0 END) AS turnovers,
            SUM(CASE WHEN {PERIOD_FOUL_SQL} THEN 1 ELSE 0 END) AS fouls,
            0 AS assists, 0 AS steals, 0 AS blocks
        FROM plays p
        WHERE EXISTS (SELECT 1 FROM _period_keys k WHERE k.event_id = p.event_id AND k.season = p.season) AND p.athlete_id IS NOT NULL AND {in_periods.format(alias="p")}
        GROUP BY p.event_id, p.season, p.athlete_id"""
    second = f"""
        SELECT p.event_id, p.season, str_split(p.participant_athlete_ids, ',')[2] AS athlete_id, {no_shots},
            0 AS offensiveRebounds, 0 AS defensiveRebounds, 0 AS turnovers, 0 AS fouls,
            SUM(CASE WHEN p.text ILIKE '%assists%' THEN 1 ELSE 0 END) AS assists,
            SUM(CASE WHEN p.text ILIKE '%steals%' THEN 1 ELSE 0 END) AS steals,
            SUM(CASE WHEN p.text ILIKE '%blocks%' THEN 1 ELSE 0 END) AS blocks
        FROM plays p
        WHERE EXISTS (SELECT 1 FROM _period_keys k WHERE k.event_id = p.event_id AND k.season = p.season) AND p.participant_athlete_ids LIKE '%,%' AND {in_periods.format(alias="p")}
        GROUP BY 1, 2, 3"""
    sums = ", ".join(f"CAST(SUM({c}) AS BIGINT) AS {c}" for c in PERIOD_COLUMNS if c != "rebounds")
    parts = f"{shots} UNION ALL {actor} UNION ALL {second}"
    if not plays:
        parts = shots.replace(zero, ", ".join(f"CAST(NULL AS BIGINT) AS {c}" for c in PERIOD_PLAYS_COLUMNS if c != "rebounds"))
    return f"""
        WITH _period_keys AS ({games}),
        _period_parts AS ({parts})
        SELECT event_id, season, athlete_id, {sums}, CAST(SUM(offensiveRebounds) + SUM(defensiveRebounds) AS BIGINT) AS rebounds
        FROM _period_parts WHERE athlete_id IS NOT NULL AND athlete_id <> ''
        GROUP BY event_id, season, athlete_id"""


PLAYS_COMPLETE = "plays_complete"
"""The load-time table saying, per played player-game and per
:data:`PERIOD_COLUMNS` column, whether :func:`period_line_sql` summed over
the whole game equals the box score (built by
:mod:`association.fetch.repairs.plays_complete`, read by the period
relation): where it does, every period of that game can be read for that
column; where it does not, some of what he did is missing from the plays.

.. versionadded:: 7.0.0
"""


def complete_column(column: str) -> str:
    """The :data:`PLAYS_COMPLETE` column for a period-line ``column``:
    ``points`` -> ``points_complete``.

    .. versionadded:: 7.0.0
    """
    if column not in PERIOD_COLUMNS:  # written in code: a programming error, not a refusal
        raise ValueError(f"no period-line column {column!r}")
    return f"{column}_complete"
