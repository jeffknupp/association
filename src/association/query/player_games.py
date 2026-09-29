"""The ``player_game`` relation: one row per player per game, read one way.

Every template that answers from box scores used to write its own FROM, its
own "did he play" guard and its own season clause - three definitions of the
same relation that had already drifted (one read ``real_games``, two read
``games`` with the phantom excluded by name; one blanked the columns a rebuilt
line cannot be trusted for, the others never read them). This module is the
one definition. The rules it carries are the ones `DATA.md` and `AGENTS.md`
record, applied on every read:

- **the phantom 1993 season is excluded by name** in a career span, and every
  join to ``games`` is keyed on ``season`` as well as ``event_id`` (the 1993
  label shares its event ids with 1994);
- **a row is a game played** only when it is not a did-not-play entry and
  carries minutes - or a line rebuilt from play-by-play, where every column
  read is one a rebuild gets right (:data:`REBUILT_STATS`);
- **a teammate's absence** is measured inside the teammate's own tenure on
  the team (:func:`_tenure_clause`), never over games he was somewhere else.

One kind of read deliberately steps outside the guard: counting the games it
drops. An answer built from box scores says how many games it could not see
(the empty 2013-18 Bulls and Pelicans box scores, a rebuilt line held back
for a stat the rebuild gets wrong), and that count has to include exactly the
rows the guard excludes. :func:`scope_without_guard` is the span clause for
those reads, and its name is the reminder. The team-level counterpart - a
team-game with no box score at all, inside a spell a player was on the team -
is a read of ``team_box_stats``, not of this relation, and lives beside the
other team reads in :mod:`association.query.conditions` (``_box_missing``).

Narrowing composes: an opponent, a venue, a starter/bench half, a teammate's
absence, a threshold on a stat, a month - each is one more clause over the
same rows, so a new one reaches every template that reads the relation at
once. Entity binding is not here: this module takes ids the entity layer has
already resolved, and never a name.

.. versionadded:: 4.3.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import duckdb

from association.nba.coverage import COVERAGE, POSTSEASON, REGULAR_SEASON
from association.nba.season import eastern_date_sql
from association.query.calendar import AlignmentNarrowing, CalendarNarrowing, alignment_clause, calendar_clause

from .conditions import UNGATED_ON_REBUILD, BoxSource
from .entities import Entity
from .shotchart import SHOT_VALUE_SQL

# A player-game ESPN lists as played but records no minutes for. Every such row
# in the warehouse carries no stats either (checked, 1994-2026), and they are of
# two kinds. In 2006-2012 they are ~10,000 a season of appearances nobody made,
# which ESPN's own games-played counts mostly leave out (dropping them makes 378
# of 445 players' 2009 counts agree, against 30). In 2013-2018 they are whole
# team box scores ESPN left empty - ~13% of team-games, which is why summing
# those seasons' box scores gives 87% of the season totals. Averaged in, either
# kind reads as a game of zeros, so they are left out and the answer says how
# many.
FLOOR: int = COVERAGE["player_box_stats"].first_season
"""The first season with box scores (1994); a career of them starts here.

.. versionadded:: 4.3.0
"""

_RECORDED = "pgl.minutes IS NOT NULL"


#: A ``season_type`` value meaning "both the regular season and the
#: postseason at once" - a sentinel outside the two real values a game row
#: carries (``REGULAR_SEASON`` 2, ``POSTSEASON`` 3), never written to a
#: ``season_type`` column itself. Read the same way ``game_log`` already read
#: a "last N games" question naming no season type at all
#: (``router._route_game_log_recent_span``, ``season_type_unstated``), and
#: now also for a question that asks for both outright ("including the
#: playoffs", "regular season and playoffs") - see
#: ``router._BOTH_SEASON_TYPES_WORDS``. :func:`season_type_clause` is the one
#: place this turns into SQL; every other reader of the relation still sees a
#: real ``season_type`` (2 or 3) or this sentinel, never a third meaning.
#:
#: .. versionadded:: 4.4.0
BOTH_SEASON_TYPES: int = 0


def season_type_clause(column: str, season_type: int) -> tuple[str, list[Any]]:
    """SQL restricting ``column`` to one season type, or - for
    :data:`BOTH_SEASON_TYPES` - both of the two real ones, and its parameters.

    The one place a ``season_type`` value becomes SQL, so a new caller that
    wants to honor "including the playoffs" gets the sentinel right by
    construction rather than by copying an ``IN`` list.

    .. versionadded:: 4.4.0
    """
    if season_type == BOTH_SEASON_TYPES:
        return f"{column} IN ({REGULAR_SEASON}, {POSTSEASON})", []
    return f"{column} = ?", [season_type]


#: The stats a rebuilt box line may be read for, and the reason the list is
#: short.
#:
#: Where ESPN serves an empty box score, ``player_box_stats_filled`` carries a
#: line rebuilt from play-by-play (see
#: :mod:`association.fetch.repairs.reconstructed_box`). Measured per player-game against
#: the 22,646 games of the 2015 regular season whose real box score survived,
#: the mean absolute error of a rebuilt figure is:
#:
#: ===================== ========= ==============
#: Stat                  Exact     Mean abs error
#: ===================== ========= ==============
#: ``freeThrowsMade``    100.0%    0.0003
#: ``blocks``            99.8%     0.002
#: ``rebounds``          99.6%     0.004
#: ``assists``           99.6%     0.004
#: ``fieldGoalsMade``    99.6%     0.005
#: ``steals``            99.1%     0.009
#: ``points``            98.3%     0.021
#: ``turnovers``         92.5%     0.080
#: ``fouls``             83.3%     0.181
#: ===================== ========= ==============
#:
#: ``turnovers`` and ``fouls`` are left out: an order of magnitude worse than
#: the rest, and a foul is wrong in one game in six. The rest are wrong by
#: hundredths of a point per game, which is why this is a PER-GAME list only.
#:
#: **A rebuilt SEASON total is a different matter and is not read anywhere.**
#: A season is exact only when the net error over every game is zero, so it
#: lands exactly right about half the time - and the error scales with games
#: played: over the Chicago and New Orleans player-seasons, a total that is
#: right averages 31.6 games and one that is wrong averages 55.6. 2016 is worse
#: still (18.9% exact, mean -16.8 points) because 180 of its scoring plays carry
#: ``type = 'Not Available'``.
#:
#: .. versionadded:: 2.2.0
REBUILT_STATS: frozenset[str] = frozenset({"points", "rebounds", "assists", "steals", "blocks", "fieldGoalsMade", "freeThrowsMade"})


# A rebuilt line has NULL minutes - play-by-play cannot recover them - so
# `_RECORDED` hides it from every reader by default. This is the opt-in.
_RECORDED_OR_REBUILT = "(pgl.minutes IS NOT NULL OR pgl.reconstructed)"


def _log_carries_rebuilt(con: duckdb.DuckDBPyConnection) -> bool:
    """Whether ``player_game_log`` has the ``reconstructed`` flag.

    Checked rather than assumed, for the reason `AGENTS.md` records under "A
    warehouse built before a view change is not detected": the column arrives
    with a `data load`, and a query written as though it were always there
    raises a Binder error against any older warehouse. Fixtures that build a
    minimal log get the same answer, and keep their old behavior.
    """
    try:
        return any(row[0] == "reconstructed" for row in con.execute("DESCRIBE player_game_log").fetchall())
    except duckdb.CatalogException:
        return False


# One join serves venue and result both: games.home_team_id agrees with
# team_box_stats.home_away on every row (checked, all 87,008 as of the warehouse
# this was last verified against - the count grows with every pull). Keyed on
# season too, since the phantom 1993 shares its event ids with 1994.
_PLAYER_GAMES = "FROM player_game_log pgl JOIN games g ON g.event_id = pgl.event_id AND g.season = pgl.season"


# The period relation (ROADMAP plan item 4): a player's quarter or half as a
# NARROWING of this relation rather than a template of its own. Nothing in
# ESPN's box score is split by period, so the period's line is rebuilt from
# the shots and the plays - points, field goals and free throws from
# `shot_chart` (points valued through SHOT_VALUE_SQL, never the play's prose -
# see period_split's docstring for why that is the whole accuracy), and
# rebounds, assists, steals, blocks, turnovers and fouls from `plays`, by the
# rules `fetch/repairs/reconstructed_box` measured, with three corrections
# measured here (2026-09-29, every player-game 2002-2026 summed over all its
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
#
# Assists, steals and blocks belong to the SECOND id in
# `participant_athlete_ids`, as in the rebuild; a foul's second id is the man
# who drew it, and is never credited.
_PERIOD_SHOT_VALUE = SHOT_VALUE_SQL
_PERIOD_FREE_THROW = "sc.shot_type ILIKE '%free throw%'"
_PERIOD_HEAVE_MISS = "(sc.shot_type ILIKE 'heave%' AND NOT sc.made)"
_PERIOD_TURNOVER = "((p.type ILIKE '%Turnover%' AND p.type <> 'No Turnover') OR p.type = 'Traveling')"
_PERIOD_FOUL = (
    "((p.type ILIKE '%Foul%' AND p.type NOT ILIKE '%Technical%' AND p.type NOT ILIKE '%Turnover%' AND p.type <> 'No Foul') OR p.type IN ('Offensive Charge', 'Shooting Block', 'Personal Block'))"
)

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

PERIOD_PLAYS_COLUMNS: frozenset[str] = frozenset({"rebounds", "offensiveRebounds", "defensiveRebounds", "assists", "steals", "blocks", "turnovers", "fouls"})
"""The :data:`PERIOD_COLUMNS` read from ``plays`` rather than ``shot_chart`` -
NULL on a period-narrowed row where the warehouse holds no play-by-play.

.. versionadded:: 5.0.0
"""

PERIOD_BLANKED: tuple[str, ...] = ("plusMinus", "ts_pct", "efg_pct", "usage_pct", "game_score")
"""The columns play-by-play cannot restrict to a period, blanked (NULL) on a
period-narrowed row rather than left holding the whole game's figure under a
heading that says "first quarter". A rate a reader wants over a period is
computed from the rebuilt makes and attempts, never read from these.

``minutes`` cannot be restricted either, and is the one exception: it keeps
the whole game's figure because the played guard reads it. A reader showing
a period's line leaves it out, and a question asking for a period's minutes
is refused (it is not in :data:`PERIOD_COLUMNS`).

.. versionadded:: 5.0.0
"""


# Measured 2026-09-29 by `period_line_sql` over EVERY period of each game,
# against the game's own box score: 628,103 player-games in 2002-2026 (both
# season types; played, and in a game the shot table covers). Nothing in the
# source splits a box score by period, so this is the only independent check a
# non-points period figure has - a play credited to the wrong period would
# still sum right, but a play mis-typed or mis-credited, which is what actually
# goes wrong, does not. Only the seasons under 99% are listed; the rest run
# 99.0-100.0%. The worst cells have causes, not noise: before 2006 a play's
# second participant (the assister, the stealer, the blocker) is mostly
# absent, so 2002-2005 assists land right about 31% of the time; 2002 is half
# a season of play-by-play with no shot values at all (UNSEPARABLE_SHOT_VALUES);
# 2016's scoring plays carry the `Not Available` type `reconstructed_box`
# records. Points are ALSO checked per period against ESPN's own linescores
# (`templates.games.PERIOD_RECONCILIATION`), which is the stricter test and the
# one a points answer is caveated by.
PERIOD_AGREEMENT: dict[str, dict[int, float]] = {
    "points": {2002: 27.2, 2003: 93.7, 2004: 98.2, 2005: 97.7, 2006: 97.6, 2013: 97.4, 2016: 90.4},
    "fieldGoalsMade": {2002: 92.3, 2003: 94.5, 2004: 98.6, 2005: 98.1, 2006: 98.1, 2013: 97.9, 2016: 90.4},
    "fieldGoalsAttempted": {2002: 89.9, 2003: 91.7, 2004: 96.6, 2005: 95.6, 2006: 96.0, 2013: 97.1, 2016: 89.3, 2017: 98.6},
    "threePointFieldGoalsMade": {2002: 97.5, 2003: 98.7},
    "threePointFieldGoalsAttempted": {2002: 95.1, 2003: 97.3},
    "freeThrowsMade": {2002: 95.8, 2003: 97.2},
    "freeThrowsAttempted": {2002: 92.4, 2003: 96.1, 2004: 98.5, 2005: 95.9, 2006: 95.4},
    "rebounds": {2002: 92.3, 2003: 93.7, 2004: 97.6, 2005: 96.9, 2006: 97.4, 2013: 97.7, 2017: 95.9, 2018: 98.5, 2019: 98.4},
    "offensiveRebounds": {2002: 76.9, 2003: 96.5, 2004: 98.5, 2005: 97.9, 2006: 98.4, 2013: 98.9, 2017: 98.7},
    "defensiveRebounds": {2002: 74.6, 2003: 95.1, 2004: 98.8, 2005: 98.4, 2006: 98.5, 2013: 98.6, 2017: 97.0},
    "assists": {2002: 30.3, 2003: 30.3, 2004: 31.2, 2005: 31.8, 2006: 87.8, 2013: 98.6, 2016: 93.5, 2017: 98.9},
    "steals": {2002: 51.7, 2003: 51.6, 2004: 50.9, 2005: 53.3, 2006: 91.5, 2013: 98.7, 2017: 97.4, 2018: 98.5},
    "blocks": {2002: 68.9, 2003: 69.3, 2004: 69.0, 2005: 69.9, 2006: 94.5, 2017: 98.7},
    "turnovers": {2002: 89.5, 2003: 95.0, 2004: 87.3, 2005: 94.1, 2006: 96.1, 2007: 98.3, 2008: 98.7, 2013: 98.8, 2016: 93.6, 2018: 98.3},
    "fouls": {2002: 92.9, 2003: 94.6, 2004: 98.4, 2005: 97.6, 2006: 96.8, 2013: 99.0, 2018: 97.6},
}
"""Per :data:`PERIOD_COLUMNS` column, per season, the percentage of
player-games whose period lines, summed over the whole game, equal the box
score exactly - for the seasons under 99% only. A period answer reading a
column in a listed season says so, and refuses under 90%;
``scripts/check_period_lines.py`` re-measures it against a built warehouse.

.. versionadded:: 5.0.0
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
    """
    if periods is not None and not all(isinstance(p, int) and 1 <= p <= 10 for p in periods):
        raise ValueError(f"periods must be integers 1-10, got {periods!r}")
    in_periods = "TRUE" if periods is None else f"{{alias}}.period IN ({', '.join(str(p) for p in periods)})"
    zero = "0 AS offensiveRebounds, 0 AS defensiveRebounds, 0 AS turnovers, 0 AS fouls, 0 AS assists, 0 AS steals, 0 AS blocks"
    no_shots = "0 AS points, 0 AS fieldGoalsMade, 0 AS fieldGoalsAttempted, 0 AS threePointFieldGoalsMade, 0 AS threePointFieldGoalsAttempted, 0 AS freeThrowsMade, 0 AS freeThrowsAttempted"
    shots = f"""
        SELECT sc.event_id, sc.season, sc.athlete_id,
            SUM(CASE WHEN sc.made THEN {_PERIOD_SHOT_VALUE} ELSE 0 END) AS points,
            SUM(CASE WHEN sc.made AND NOT {_PERIOD_FREE_THROW} THEN 1 ELSE 0 END) AS fieldGoalsMade,
            SUM(CASE WHEN NOT {_PERIOD_FREE_THROW} AND NOT {_PERIOD_HEAVE_MISS} THEN 1 ELSE 0 END) AS fieldGoalsAttempted,
            SUM(CASE WHEN sc.made AND {_PERIOD_SHOT_VALUE} = 3 THEN 1 ELSE 0 END) AS threePointFieldGoalsMade,
            SUM(CASE WHEN {_PERIOD_SHOT_VALUE} = 3 AND NOT {_PERIOD_HEAVE_MISS} THEN 1 ELSE 0 END) AS threePointFieldGoalsAttempted,
            SUM(CASE WHEN sc.made AND {_PERIOD_FREE_THROW} THEN 1 ELSE 0 END) AS freeThrowsMade,
            SUM(CASE WHEN {_PERIOD_FREE_THROW} THEN 1 ELSE 0 END) AS freeThrowsAttempted,
            {zero}
        FROM shot_chart sc
        WHERE EXISTS (SELECT 1 FROM _period_keys k WHERE k.event_id = sc.event_id AND k.season = sc.season) AND sc.athlete_id IS NOT NULL AND {in_periods.format(alias="sc")}
        GROUP BY sc.event_id, sc.season, sc.athlete_id"""
    actor = f"""
        SELECT p.event_id, p.season, p.athlete_id, {no_shots},
            SUM(CASE WHEN p.type = 'Offensive Rebound' THEN 1 ELSE 0 END) AS offensiveRebounds,
            SUM(CASE WHEN p.type = 'Defensive Rebound' THEN 1 ELSE 0 END) AS defensiveRebounds,
            SUM(CASE WHEN {_PERIOD_TURNOVER} THEN 1 ELSE 0 END) AS turnovers,
            SUM(CASE WHEN {_PERIOD_FOUL} THEN 1 ELSE 0 END) AS fouls,
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


def _period_source(narrowed: Narrowed) -> tuple[str, list[Any]]:
    """The FROM of a period-narrowed read and the parameters it binds ahead
    of the WHERE: the relation with each :data:`PERIOD_COLUMNS` column
    replaced by the period's own figure and each :data:`PERIOD_BLANKED`
    column blanked, re-aliased ``pgl`` beside ``g`` so every
    clause and every reader's SQL is unchanged.

    The replacement happens BEFORE the row filters, so a line on a stat
    ("games with 10+ first-quarter points") is a line on the period, as the
    question means it. ``minutes`` alone keeps the whole game's figure: the
    played guard reads it, and play-by-play has no period minutes to put in
    its place - a reader showing a period line leaves it out, and a question
    asking for a period's minutes is refused before this is read.

    Only games the shot table covers are kept - a game with no located shots
    would otherwise contribute a confident zero (``_period_split_rows``'s own
    rule, now the relation's). The period line is summed over the games the
    base clauses (player, span, season type) select, never all 14 million
    plays.
    """
    base = " AND ".join(narrowed.base) or "TRUE"
    keys = f"SELECT DISTINCT pgl.event_id, pgl.season {_PLAYER_GAMES} WHERE {base}"
    line = period_line_sql(narrowed.periods, keys, plays=narrowed.period_plays)
    present = narrowed.period_log_columns

    def _period_figure(c: str) -> str:
        # A plays column stays NULL where the warehouse has no plays:
        # unknown, not a quiet quarter.
        return f"COALESCE(l.{c}, 0)" if narrowed.period_plays or c not in PERIOD_PLAYS_COLUMNS else f"l.{c}"

    # A column the log holds is REPLACEd in place; one it lacks (an older
    # warehouse, a fixture) is added, so a reader's `pgl.<column>` always binds.
    held = [c for c in PERIOD_COLUMNS if present is None or c in present]
    added = [c for c in PERIOD_COLUMNS if c not in held]
    blanked = [c for c in PERIOD_BLANKED if present is None or c in present]
    replaced = ", ".join([*(f"{_period_figure(c)} AS {c}" for c in held), *(f"NULL AS {c}" for c in blanked)])
    star = f"pgl.* REPLACE ({replaced})" if replaced else "pgl.*"
    extra = "".join(f", {_period_figure(c)} AS {c}" for c in added)
    covered = "pgl.event_id IN (SELECT DISTINCT cov.event_id FROM shot_chart cov WHERE cov.season = pgl.season)"
    sql = (
        f"FROM (WITH _period_line AS ({line}) "
        f"SELECT {star}{extra} FROM player_game_log pgl "
        "LEFT JOIN _period_line l ON l.event_id = pgl.event_id AND l.season = pgl.season AND l.athlete_id = pgl.athlete_id "
        f"WHERE {covered}) pgl JOIN games g ON g.event_id = pgl.event_id AND g.season = pgl.season"
    )
    return sql, list(narrowed.base_params)


def _source(narrowed: Narrowed) -> tuple[str, list[Any]]:
    """The relation's FROM for ``narrowed`` and the parameters it binds ahead
    of the WHERE - the plain one, or the period's (:func:`_period_source`)."""
    if narrowed.periods is not None:
        return _period_source(narrowed)
    return _PLAYER_GAMES, []


def _teammate_played(box: BoxSource) -> str:
    """SQL for "this teammate played that game", over the given box source.

    A game rebuilt from play-by-play has no minutes, so against the stored
    table every teammate in a rebuilt game reads as absent and the game counts
    as one played "without" him. Measured before this took a source: "Anthony
    Davis without Eric Gordon, 2015" listed games Gordon played in - he played
    48 of Davis's 68 that season.

    .. versionadded:: 2.2.0
    """
    appeared = "(m.minutes IS NOT NULL OR m.reconstructed)" if box.rebuilt else "m.minutes IS NOT NULL"
    return f"EXISTS (SELECT 1 FROM {box.table} m WHERE m.athlete_id = ? AND m.event_id = pgl.event_id AND m.season = pgl.season AND NOT m.did_not_play AND {appeared})"


# An open end of a stint, as a string that sorts before or after any date.
_OPEN_START, _OPEN_END = "0000", "9999"


def _joined(names: list[str], word: str = "and") -> str:
    """ "A", "A and B", "A, B and C" - a list of names as a sentence holds it."""
    if len(names) <= 1:
        return names[0] if names else ""
    return f"{', '.join(names[:-1])} {word} {names[-1]}"


#: The predicates a :class:`Condition` can state about its player in a game.
CONDITION_PREDICATES: frozenset[str] = frozenset({"played", "absent", "started", "bench", "reached"})
"""Every value :attr:`Condition.predicate` takes.

.. versionadded:: 5.0.0
"""


@dataclass
class Condition:
    """A player condition on a game: ``player`` on the subject's ``side``
    (``"own"`` - a teammate - or ``"opponent"``) with a ``predicate`` that
    held in that game - he ``played``, was ``absent``, ``started``, came off
    the ``bench``, or ``reached`` a line (``column op value``, said as
    ``label``). "Without Durant", "when Embiid and Paul George play", "when
    Maxey scores 20+", "vs LeBron" are each one of these; a list of them is
    ANDed. ROADMAP plan item 3: the one shape ``Narrowed.without`` (own,
    absent), the starter half (the subject's own ``started``), record_when's
    threshold (own, reached) and the pair relation's second player
    (opponent, played) were special cases of.

    ``tenure`` bounds an OWN-side absence to the games inside the player's
    time on the subject's team (:func:`_teammate_stints`): "Nets record
    without KD" must not count the decades before he arrived.

    .. versionadded:: 5.0.0
    """

    player: Entity
    side: str
    predicate: str
    line: tuple[str, str, int, str] | None = None
    tenure: tuple[str, list[Any]] | None = None

    def phrase(self) -> str:
        """How the answer says this condition after the subject's name."""
        name = self.player.name
        if self.predicate == "absent":
            return f"without {name}" + ("" if self.side == "own" else " on the other side")
        if self.predicate == "played":
            return f"with {name}" if self.side == "own" else f"vs {name}"
        if self.predicate == "started":
            return f"with {name} starting"
        if self.predicate == "bench":
            return f"with {name} off the bench"
        label = self.line[3] if self.line else "the line"
        return f"in games {name} had {label}"


def condition_clause(condition: Condition, box: BoxSource) -> tuple[list[str], list[Any]]:
    """The WHERE clauses (ANDed) and their parameters for one
    :class:`Condition` over the box source ``box`` - one EXISTS over the
    player's own row in the same game, on the stated side, with the
    predicate; an absence is its negation, bounded by the tenure clause
    where one is carried.

    .. versionadded:: 5.0.0
    """
    if condition.predicate == "absent" and condition.tenure is not None:
        # Word for word the clause pair `Narrowed.without` has always added:
        # the tenure first, then the negated appearance - the same rows.
        tenure, tenure_params = condition.tenure
        return [tenure, f"NOT {_teammate_played(box)}"], [*tenure_params, condition.player.id]
    appeared = "(m.minutes IS NOT NULL OR m.reconstructed)" if box.rebuilt else "m.minutes IS NOT NULL"
    side = "m.team_id = pgl.team_id" if condition.side == "own" else "m.team_id = pgl.opponent_team_id"
    inner = [f"m.athlete_id = ? AND m.event_id = pgl.event_id AND m.season = pgl.season AND NOT m.did_not_play AND {appeared} AND {side}"]
    params: list[Any] = [condition.player.id]
    if condition.predicate == "started":
        inner.append("m.starter")
    elif condition.predicate == "bench":
        inner.append("NOT m.starter")
    elif condition.predicate == "reached":
        assert condition.line is not None, "a reached condition carries its line"
        column, op, value, _ = condition.line
        inner.append(f"m.{column} {op} ?")
        params.append(value)
    exists = f"EXISTS (SELECT 1 FROM {box.table} m WHERE {' AND '.join(inner)})"
    return [f"NOT {exists}" if condition.predicate == "absent" else exists], params


@dataclass
class Narrowed:
    """One player's games in a span, and whatever the question narrowed them
    by. Every clause applies to ``_PLAYER_GAMES``; the base ones (player, season
    type, span, played) are kept apart from the rest so an empty answer can say
    which narrowing emptied it."""

    base: list[str]
    base_params: list[Any]
    extra: list[str] = field(default_factory=list)
    extra_params: list[Any] = field(default_factory=list)
    opponent: Entity | None = None
    #: The player's OWN team the question narrowed to - "for the Miami Heat",
    #: read only where the question names one and the router left no team
    #: slot at all (yardstick-v2 F166). Distinct from ``opponent``: this keeps
    #: only the games he played FOR this team, not games against it.
    team: Entity | None = None
    venue: str | None = None
    #: True for a log of starts, False for one off the bench, None when the
    #: question named neither half.
    started: bool | None = None
    #: Every player condition the question stated (:class:`Condition`), all
    #: of them at once: "without Tatum and Brown" is the games NEITHER
    #: played, so a dropped one would answer a wider question. ``without``
    #: and ``tenure`` below are views over the own-side absences, for the
    #: readers that phrase and count them.
    conditions: list[Condition] = field(default_factory=list)
    date: str | None = None
    #: Each box-score line the games were kept under or over, as the answer
    #: says it: ``"under 14 free throw attempts"``.
    measures: list[str] = field(default_factory=list)
    #: The game of a playoff series the question named ("game 4"), or None.
    series_game: int | None = None
    #: The window: the newest (``"recent"``) or oldest (``"first"``) N of the
    #: narrowed games, or None for all of them. Cut AFTER every row filter and
    #: BEFORE whatever a reader does with the rows, so "30-point games in his
    #: last 10" counts inside the ten and "his average over his last 5 vs
    #: Boston" averages the five Boston games - step 3's one rule that is
    #: about the skeleton rather than the rows.
    window: tuple[str, int] | None = None
    #: The ordinal season a league-wide read was narrowed to - each player's
    #: Nth regular season ("most points in a 15th season") - or None.
    ordinal: int | None = None
    #: The calendar narrowing a ``situation`` slot named - a weekday, a month,
    #: a fixed day, every game from a day of the season on - or None.
    calendar: CalendarNarrowing | None = None
    #: The conference or division a ``situation`` slot named the OPPONENT to
    #: be in - "vs the west", "against the southeast division" - or None. The
    #: same slot as ``calendar`` and mutually exclusive with it: a value is
    #: read as one or the other, never both, by :func:`association.query.calendar.parse_situation`/
    #: :func:`~association.query.calendar.parse_alignment` in that order.
    alignment: AlignmentNarrowing | None = None
    #: The periods a quarter or a half narrowed each game to - ``(1,)``,
    #: ``(3, 4)`` - or None for the whole game. Set by :meth:`narrow_periods`;
    #: every read then sees the period's own line (:func:`period_line_sql`).
    periods: tuple[int, ...] | None = None
    #: How the answer names those periods: ``"1st quarter"``, ``"2nd half"``.
    period_label: str | None = None
    #: Whether the warehouse holds ``plays``, which every period column but
    #: the shot ones is read from (:data:`PERIOD_PLAYS_COLUMNS`).
    period_plays: bool = True
    #: The columns ``player_game_log`` actually has, where the caller looked -
    #: an older warehouse (or a fixture) lacks the advanced ones, and a
    #: REPLACE naming a missing column fails to bind.
    period_log_columns: frozenset[str] | None = None

    @property
    def without(self) -> list[Entity]:
        """The teammates whose absence the games were narrowed to - the
        own-side ``absent`` conditions' players, in order.

        .. versionchanged:: 5.0.0
           A view over :attr:`conditions`, not a field of its own.
        """
        return [c.player for c in self.conditions if c.side == "own" and c.predicate == "absent"]

    @property
    def tenure(self) -> list[tuple[str, list[Any]]]:
        """Each absent teammate's tenure clause, aligned with :attr:`without`.

        .. versionchanged:: 5.0.0
           A view over :attr:`conditions`.
        """
        return [c.tenure or ("TRUE", []) for c in self.conditions if c.side == "own" and c.predicate == "absent"]

    def add_condition(self, condition: Condition, box: BoxSource) -> None:
        """Narrow to the games ``condition`` held in - its clauses joined to
        ``extra`` - unless the same player is already held under the same
        predicate and side (the same man named twice narrows nothing).

        .. versionadded:: 5.0.0
        """
        if any(c.player.id == condition.player.id and c.predicate == condition.predicate and c.side == condition.side for c in self.conditions):
            return
        clauses, params = condition_clause(condition, box)
        self.conditions.append(condition)
        self.extra += clauses
        self.extra_params += params

    def clauses(self, *, narrowed: bool = True, recorded: bool = True, rebuilt: bool = False) -> tuple[str, list[Any]]:
        """The WHERE body and its parameters - without the narrowing when
        ``narrowed`` is false, and over the empty lines when ``recorded`` is.

        ``rebuilt`` widens what counts as a game he played to include a line
        rebuilt from play-by-play. The NEGATION uses the same widened guard, so
        "games not counted" stays the complement of "games counted" - otherwise
        a rebuilt game would be both listed and reported as skipped.
        """
        guard = _RECORDED_OR_REBUILT if rebuilt else _RECORDED
        where = [*self.base, guard if recorded else f"NOT ({guard})"]
        params = list(self.base_params)
        if narrowed:
            where += self.extra
            params += self.extra_params
        return " AND ".join(where), params

    def _situation_phrase(self) -> str | None:
        """The phrase a ``situation`` slot named, or None - the calendar one
        (``self.calendar``) or the conference/division one (``self.alignment``),
        whichever :meth:`narrow_calendar`/:meth:`narrow_alignment` set. The two
        are mutually exclusive, since a value is read as one or the other
        (never both) by :func:`association.query.calendar.parse_situation`/
        :func:`~association.query.calendar.parse_alignment` in that order -
        pulled out of :meth:`filters` to keep its own branch count down.

        .. versionadded:: 4.4.0
        """
        if self.calendar is not None:
            return self.calendar.label
        if self.alignment is not None:
            return self.alignment.label
        return None

    def _condition_parts(self) -> list[str]:
        """The absent teammates as one phrase ("without A and B"), then every
        other condition's own - a list so :meth:`filters` adds no branch."""
        parts = [f"without {_joined([mate.name for mate in self.without])}"] if self.without else []
        return parts + [c.phrase() for c in self.conditions if not (c.side == "own" and c.predicate == "absent")]

    def _ordinal_parts(self) -> list[str]:
        """``["in their 15th season"]`` for a league read narrowed to each
        player's Nth season, else nothing - a list so :meth:`filters` adds no
        branch for it."""
        if self.ordinal is None:
            return []
        n = self.ordinal
        suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")  # codespell:ignore nd - an ordinal suffix
        return [f"in their {n}{suffix} season"]

    def filters(self, *, dated: bool = True, windowed: bool = False) -> str:
        """What the games were narrowed to, as it follows a name: ``" vs the
        Detroit Pistons at home"``.

        ``windowed`` is opt-in and defaults off: ``.window`` is set by
        :func:`association.query.templates.common.scoped_games` for every
        caller whose slots carry ``order``/``limit`` - including ``game_log``
        and ``player_stat``, which read it for their OWN row-fetching
        (:func:`rows_sql` never consults ``.window``) and already say "last N
        games" their own way. Including the phrase here unconditionally would
        say it a second time in theirs. Only a caller that reads its rows
        through :func:`games_subquery`/:func:`aggregate_sql`/:func:`grouped_sql`
        - where ``.window`` is what actually cut the rows - has a reason to
        ask for it (step 3, C5's ``shot_chart``/``shot_distance``).

        .. versionchanged:: 4.4.0
           Takes ``windowed`` (default ``False``); the window phrase moved
           behind it.
        """
        parts = [f"in the {self.period_label}"] if self.period_label else []
        if self.team is not None:
            parts.append(f"with the {self.team.name}")
        if self.opponent is not None:
            parts.append(f"vs the {self.opponent.name}")
        if self.venue:
            parts.append("at home" if self.venue == "home" else "on the road")
        if self.started is not None:
            # Said outright, like every other narrowing here. A log of 50
            # starts headed only "last 50 games" is the silent narrowing this
            # module exists to stop - it reads as his last 50 games played.
            parts.append("as a starter" if self.started else "off the bench")
        parts.extend(self._condition_parts())
        if self.measures:
            parts.append(f"with {_joined(self.measures)}")
        parts.extend(self._ordinal_parts())
        if self.series_game is not None:
            # "of the series" only where one series is in view: an opponent
            # names it. Across a postseason it is game 4 of each series.
            parts.append(f"in game {self.series_game} of {'the' if self.opponent is not None else 'each'} series")
        if self.date and dated:
            parts.append(f"on {self.date}")
        situation = self._situation_phrase()
        if situation:
            parts.append(situation)
        if self.window is not None and windowed:
            order, n = self.window
            parts.append(f"over his {'last' if order == 'recent' else 'first'} {n} game{'s' if n != 1 else ''}")
        return "".join(f" {part}" for part in parts)

    def narrow(self, clause: str, *params: Any) -> None:
        """One more clause over the same rows - how every narrowing composes."""
        self.extra.append(clause)
        self.extra_params.extend(params)

    def narrow_measure(self, column: str, op: str, value: Any, label: str | None = None) -> None:
        """A threshold on a box-score column: ``points >= 30``. ``op`` is one of
        :data:`MEASURE_OPS`; the column is a name from this module's own lists,
        never text from a question. A ``label`` is how the answer says it
        (``"under 14 free throw attempts"``); a threshold the caller words for
        itself passes none."""
        if op not in MEASURE_OPS:  # ops are written in code, so this is a programming error, not a refusal
            raise ValueError(f"no comparison called {op!r}")
        self.narrow(f"pgl.{column} {MEASURE_OPS[op]} ?", value)
        if label:
            self.measures.append(label)

    def narrow_calendar(self, narrowing: CalendarNarrowing) -> None:
        """Only the games on a weekday, in a month, on a fixed day, or from a
        day of the season on - the ``situation`` cell, read by
        :func:`association.query.calendar.parse_situation` and applied by
        :func:`association.query.templates.common.scoped_games`. The date is
        the game's US Eastern day, so "on Tuesdays" is the night the game was
        played, not ESPN's UTC stamp.

        .. versionadded:: 4.4.0
        """
        clause, params = calendar_clause(narrowing, eastern_date_sql("g.date"), "pgl.season")
        self.narrow(clause, *params)
        self.calendar = narrowing

    def narrow_alignment(self, narrowing: AlignmentNarrowing) -> None:
        """Only the games against an opponent in this conference or division,
        for that game's own season - the ``situation`` cell's other half,
        read by :func:`association.query.calendar.parse_alignment` and
        applied by :func:`association.query.templates.common.scoped_games`/
        :func:`~association.query.templates.common.league_games`.
        ``pgl.opponent_team_id`` is the column :func:`league_games` already
        narrows by name against; this reads it against every team in the
        conference or division instead of one.

        .. versionadded:: 4.4.0
        """
        clause, params = alignment_clause(narrowing, "pgl.opponent_team_id", "pgl.season")
        self.narrow(clause, *params)
        self.alignment = narrowing

    def narrow_periods(self, periods: tuple[int, ...], label: str, *, plays: bool = True, log_columns: frozenset[str] | None = None) -> None:
        """Only ``periods`` of each game - a quarter, a half, an overtime -
        read from the line :func:`period_line_sql` rebuilds, over the games
        the shot table covers. The row filters still read the whole game
        (see :func:`_period_source`); what a reader SELECTs is the period's.

        .. versionadded:: 5.0.0
        """
        period_line_sql(periods, "SELECT 1")  # validates the periods before anything is read
        self.periods = tuple(periods)
        self.period_label = label
        self.period_plays = plays
        self.period_log_columns = log_columns

    def narrow_series_game(self, n: int) -> None:
        """Only the ``n``th game of each playoff series: the games between the
        same two teams in one postseason, numbered by date over ``real_games``
        - the series' own games, so a game he sat out still counts toward the
        number, and his ``n``th game played is not mistaken for game ``n``."""
        self.narrow(f"pgl.event_id IN (SELECT event_id FROM ({_SERIES_GAMES}) WHERE game_of_series = ?)", n)
        self.series_game = n


# Every postseason game numbered within its series. A series is the games two
# teams play each other in one postseason; ``real_games`` rather than ``games``
# so a placeholder or a duplicated event does not shift the count.
_SERIES_GAMES = (
    "SELECT s.event_id, ROW_NUMBER() OVER (PARTITION BY s.season, LEAST(s.home_team_id, s.away_team_id), GREATEST(s.home_team_id, s.away_team_id) ORDER BY s.date, s.event_id) AS game_of_series "
    "FROM real_games s WHERE s.season_type = 3"
)

#: The comparisons :meth:`Narrowed.narrow_measure` accepts, mapped to SQL.
MEASURE_OPS: dict[str, str] = {">=": ">=", ">": ">", "<=": "<=", "<": "<", "=": "="}
"""``{">=": ">=", ...}`` - an allowlist, so no question text reaches SQL as an operator.

.. versionadded:: 4.3.0
"""


def league(season_clause: str, season_params: list[Any], season_type: int) -> Narrowed:
    """Every player's games in a span - the read a league-wide count or
    single-game high starts from. Same guards as one player's, without him.

    .. versionchanged:: 4.4.0
       Honors :data:`BOTH_SEASON_TYPES` through :func:`season_type_clause`.
    """
    type_clause, type_params = season_type_clause("pgl.season_type", season_type)
    return Narrowed(base=[type_clause, season_clause, "NOT pgl.did_not_play"], base_params=[*type_params, *season_params])


#: The tiebreak every :func:`rows_sql` read falls back on after its caller's
#: own order: the game, then the player, so two rows a caller's order leaves
#: equal - two centers in the same game, on the same date, in a league-wide
#: log - still come out in the same order every run rather than DuckDB's
#: parallel scan order, which is not stable. Appended once here rather than
#: repeated (or forgotten) in each of :func:`rows_sql`'s callers, per
#: ISSUES.md "A game log's same-date rows come out in an unstable order".
#:
#: .. versionadded:: 5.0.0
ROWS_TIEBREAK = "pgl.event_id, pgl.player_name"


def rows_sql(narrowed: Narrowed, select: str, *, order: str, limit: int | None = None, offset: int = 0, rebuilt: bool = False) -> tuple[str, list[Any]]:
    """The rows themselves - a log, a single game, the top game by a stat.
    ``select`` and ``order`` are column expressions written in code, ordered
    after by :data:`ROWS_TIEBREAK` so no caller has to add its own.

    .. versionchanged:: 5.0.0
       Appends :data:`ROWS_TIEBREAK` after ``order``.
    """
    where, params = narrowed.clauses(rebuilt=rebuilt)
    source, ahead = _source(narrowed)
    params = [*ahead, *params]
    sql = f"SELECT {select} {source} WHERE {where} ORDER BY {order}, {ROWS_TIEBREAK}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    if offset:
        sql += f" OFFSET {int(offset)}"
    return sql, params


def _windowed(narrowed: Narrowed, *, rebuilt: bool) -> tuple[str, list[Any]]:
    """The FROM ... WHERE of a read: the relation under every row filter, and
    under the window too when one is set - as a subquery cut to the newest or
    oldest N by date, aliased so ``pgl.`` and ``g.`` still name the columns a
    reader wrote against."""
    where, params = narrowed.clauses(rebuilt=rebuilt)
    source, ahead = _source(narrowed)
    params = [*ahead, *params]
    if narrowed.window is None:
        return f"{source} WHERE {where}", params
    order, n = narrowed.window
    direction = "DESC" if order == "recent" else "ASC"
    inner = f"SELECT pgl.* {source} WHERE {where} ORDER BY g.date {direction}, pgl.event_id LIMIT {int(n)}"
    # Re-expose the game columns under their alias so a reader's `g.` works.
    return f"FROM ({inner}) pgl JOIN games g ON g.event_id = pgl.event_id AND g.season = pgl.season", params


def aggregate_sql(narrowed: Narrowed, selects: list[str], *, rebuilt: bool = False) -> tuple[str, list[Any]]:
    """One row of aggregates over the narrowed games: averages, totals, a count,
    a record - over the window, where one is set.

    .. versionchanged:: 4.4.0
       Honors :attr:`Narrowed.window`.
    """
    source, params = _windowed(narrowed, rebuilt=rebuilt)
    return f"SELECT {', '.join(selects)} {source}", params


def games_subquery(narrowed: Narrowed, box: BoxSource) -> tuple[str, list[Any]]:
    """The narrowed games as a subquery with the result columns the condition
    readers wrap: ``won``, ``home_away``, ``team_score``, ``opponent_score``,
    the Eastern ``day`` and the raw ``stamp`` beside every ``pgl`` column.

    This is what :func:`association.query.conditions._player_games` renders
    for the templates that group a player's games by a condition (splits, a
    record above a threshold, a streak, with/without) - the same relation, the
    same guard, positional parameters instead of named ones so a
    :class:`Narrowed` can feed it. ``SELECT * REPLACE`` blanks the columns a
    rebuilt line cannot be trusted for, exactly as that reader does.

    .. versionadded:: 4.4.0

    .. versionchanged:: 4.4.0
       Honors :attr:`Narrowed.window` (step 3, C5), through the same
       :func:`_windowed` reader :func:`aggregate_sql`/:func:`grouped_sql`
       already use - a window is cut, with the played guard already applied,
       before this subquery's own columns are computed on top of it. A no-op
       for every caller that never sets ``window`` (``player_splits``,
       ``streak``, ``with_without``, ``record_when``): ``_windowed`` returns
       the identical ``FROM ... WHERE`` this function built by hand when
       ``window`` is ``None``.
    """
    source, params = _windowed(narrowed, rebuilt=box.rebuilt)
    ungated = [c for c in UNGATED_ON_REBUILD if c in box.columns]
    replacements = ", ".join(column("pgl", c, box) for c in ungated)
    blanked = f" REPLACE ({replacements})" if box.rebuilt and ungated else ""
    sql = f"""
        SELECT pgl.*{blanked}, g.date AS stamp, {eastern_date_sql("g.date")} AS day, g.winner_team_id = pgl.team_id AS won,
               CASE WHEN g.home_team_id = pgl.team_id THEN 'home' ELSE 'away' END AS home_away,
               CASE WHEN g.home_team_id = pgl.team_id THEN g.home_score ELSE g.away_score END AS team_score,
               CASE WHEN g.home_team_id = pgl.team_id THEN g.away_score ELSE g.home_score END AS opponent_score
        {source}"""
    return sql, params


def named(sql: str, params: list[Any], prefix: str = "r") -> tuple[str, dict[str, Any]]:
    """``sql`` with each positional ``?`` renamed ``$<prefix><n>``, and the
    parameters as that dict - for a reader that nests the same subquery more
    than once, or binds names of its own beside it. A ``?`` can appear only
    once in a statement; a name can be reused, and DuckDB will not mix the two
    in one statement. Only the relation's own SQL is rewritten, never a value.

    .. versionadded:: 4.4.0
    """
    parts = sql.split("?")
    if len(parts) - 1 != len(params):
        raise ValueError(f"{len(parts) - 1} placeholders for {len(params)} parameters")
    out = parts[0]
    for i, part in enumerate(parts[1:]):
        out += f"${prefix}{i}" + part
    return out, {f"{prefix}{i}": v for i, v in enumerate(params)}


def grouped_sql(
    narrowed: Narrowed, group_by: str, selects: list[str], *, having: str | None = None, order: str | None = None, limit: int | None = None, rebuilt: bool = False
) -> tuple[str, list[Any]]:
    """Aggregates per group - a ranking of players, a split by venue or month.
    A ``limit`` here is applied after grouping (the top N groups), never to
    the rows: "top 20 by average" and "the last 20 games, averaged" are
    different questions and only the second is a row window."""
    source, params = _windowed(narrowed, rebuilt=rebuilt)
    sql = f"SELECT {', '.join(selects)} {source} GROUP BY {group_by}"
    if having:
        sql += f" HAVING {having}"
    if order:
        sql += f" ORDER BY {order}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return sql, params


#: The two halves of the starter/bench split, as `router._split_side` narrows
#: them when a question names one. ``starter_bench`` itself is NOT here: that is
#: the category, and a question naming both halves is asking for a splits table
#: rather than a filtered set of games.
STARTER_SIDES: dict[str, bool] = {"starter": True, "bench": False}
"""Which value of ``player_game_log.starter`` each named half of the split means.

.. versionadded:: 4.3.0
"""


def column(alias: str, name: str, box: BoxSource) -> str:
    """A box-score column as a reader may trust it: blanked on a rebuilt row
    when it is one the rebuild fills but was never measured for
    (:data:`association.query.conditions.UNGATED_ON_REBUILD`), so an average is
    taken over the games that carry the figure rather than over a wrong one."""
    if box.rebuilt and name in UNGATED_ON_REBUILD and name in box.columns:
        return f"CASE WHEN {alias}.reconstructed THEN NULL ELSE {alias}.{name} END AS {name}"
    return f"{alias}.{name}"


def paired_rows_sql(narrowed: Narrowed, other_id: str, select: str, *, teammates: bool = False, order: str | None = "g.date DESC, pgl.event_id", rebuilt: bool = False) -> tuple[str, list[Any]]:
    """The pair relation: games the narrowed player and ``other`` both played,
    on opposite teams - or the same, with ``teammates`` - with the other's line
    readable as ``other.<column>``. A matchup is this and nothing more: two
    reads of the relation joined on the event, both under the played guard,
    which is why "never met" can be true of two men who shared a floor for
    years (their games are teammates' games, not meetings)."""
    if narrowed.periods is not None:
        # A meeting's period line would need the OTHER man's period line
        # too; nothing reads one yet, so this refuses rather than pairing a
        # period with a whole game.
        raise ValueError("the pair relation has no period reading")
    where, params = narrowed.clauses(rebuilt=rebuilt)
    appeared = "(other.minutes IS NOT NULL OR other.reconstructed)" if rebuilt else "other.minutes IS NOT NULL"
    side = "other.team_id = pgl.team_id" if teammates else "other.team_id <> pgl.team_id"
    sql = (
        f"SELECT {select} {_PLAYER_GAMES} JOIN player_game_log other ON other.event_id = pgl.event_id AND other.season = pgl.season "
        f"AND other.athlete_id = ? AND {side} AND NOT other.did_not_play AND {appeared} WHERE {where}"
    )
    if order:
        sql += f" ORDER BY {order}"
    return sql, [other_id, *params]


def scope_without_guard(alias: str, season: int | None, season_type: int) -> tuple[str, list[Any]]:
    """The WHERE clause for one season of box scores, or for a career of them,
    with NO played guard - for counting the rows the guard drops, never for
    answering from them.

    A career starts at the box scores' floor, and that floor is also what keeps
    1993 out: ESPN answers season=1993 with the same games as 1994 (coverage's
    phantom), so a career counted from 1993 counts every 1993-94 game twice -
    26,350 duplicate player-games.

    .. versionchanged:: 4.4.0
       Honors :data:`BOTH_SEASON_TYPES` through :func:`season_type_clause`.
    """
    type_clause, type_params = season_type_clause(f"{alias}.season_type", season_type)
    if season is None:
        return f"{alias}.season >= ? AND {type_clause}", [FLOOR, *type_params]
    return f"{alias}.season = ? AND {type_clause}", [season, *type_params]


def _teammate_stints(con: duckdb.DuckDBPyConnection, athlete_id: str) -> list[tuple[int, str, str, str]]:
    """When a player was on each team: ``(season, team_id, start, end)``, with
    ``start``/``end`` compared against ``games.date``.

    There is no roster table, so this is read off his own box-score rows, and
    the one fact that makes that hard is that an injured player mostly has NO
    row: Stephen Curry's 2026 is 43 rows, none of them did-not-play, for an
    82-game Warriors season. So a stint runs from his first row for a team to
    his last, and then:

    - it is open at the start of the season when he ended the previous one on
      that team (or has no earlier rows at all) - LeBron James's first 2026 row
      is 2025-11-19, and the Lakers' 14 games before it were played without
      him;
    - it is open at the end when no later row that season is for another team,
      so an injury that ends a season still counts.

    A mid-season arrival from another team is not extended backwards: Seth
    Curry's first 2026 Warriors row is 2025-12-03, and their October games were
    not played "without" somebody who was in Charlotte's plans. The gap between
    a traded player's last game for one team and his first for the next belongs
    to neither, which undercounts rather than guesses."""
    phantom = COVERAGE["player_game_log"].phantom
    excluded = f" AND season NOT IN ({', '.join('?' for _ in phantom)})" if phantom else ""
    rows = con.execute(
        f"SELECT season, team_id, MIN(game_date), MAX(game_date) FROM player_game_log WHERE athlete_id = ?{excluded} GROUP BY season, team_id ORDER BY season, MIN(game_date)",
        [athlete_id, *phantom],
    ).fetchall()
    by_season: dict[int, list[tuple[str, str, str]]] = {}
    for season, team_id, first, last in rows:
        by_season.setdefault(int(season), []).append((str(team_id), str(first), str(last)))
    stints: list[tuple[int, str, str, str]] = []
    carried: str | None = None  # the team he ended the previous season on
    for season in sorted(by_season):
        spells = by_season[season]
        final = max(range(len(spells)), key=lambda i: spells[i][2])
        for index, (team_id, first, last) in enumerate(spells):
            start = _OPEN_START if index == 0 and carried in (None, team_id) else first
            end = _OPEN_END if index == final else last
            stints.append((season, team_id, start, end))
        carried = spells[final][0]
    return stints


def _tenure_clause(con: duckdb.DuckDBPyConnection, mate: Entity, season: int | None) -> tuple[str, list[Any]]:
    """SQL keeping the games ``pgl`` played on a team ``mate`` was on at the
    time - see _teammate_stints for how "was on" is read. ``season`` is the one
    season asked about, or None for a career."""
    stints = [s for s in _teammate_stints(con, mate.id) if season is None or s[0] == season]
    if not stints:
        return "FALSE", []
    rows = ", ".join("(?, ?, ?, ?)" for _ in stints)
    return (
        f"EXISTS (SELECT 1 FROM (VALUES {rows}) AS stint(season, team_id, start_date, end_date) "
        "WHERE stint.season = pgl.season AND stint.team_id = pgl.team_id AND pgl.game_date BETWEEN stint.start_date AND stint.end_date)"
    ), [value for stint in stints for value in stint]
