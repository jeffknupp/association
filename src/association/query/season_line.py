"""The season line: a second relation beside the player-games relation
(:mod:`association.query.player_games`) - ``player_season_stats_deduped``,
one row per player per season (a traded player's stints already folded into
one), reaching back to 1976-77 where the box scores start in 1993-94 - and
``player_season_advanced_stats`` beside it, the computed stats summed from
the box scores season by season.

This module holds what the season line's readers
(:mod:`association.query.compose.seasons`) share and nothing that words an
answer: the subject settled over it, the span a read covers, and each
statement a read runs - a season's line, a career's summed line, an
advanced stat's season or weighted career, a stat season by season and the
career total beneath it, and the NetPoints summary a comparison shows -
built here and executed through the compiler's one door
(:func:`~association.query.compose.core.values_of`), never by a reader of
its own. Phase 2's slice (iii) (``ROADMAP.md``): the statements are the
retired templates' (``templates.players._season_row``,
``_career_player_stat``, ``_player_stat_advanced``,
``_player_history_read``, ``_player_history_career_count``,
``_compare_netpoints``), moved whole.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import duckdb

from association.nba.season import current_season
from association.query.entities import Availability, Entity
from association.query.metrics import SEASON_TYPE_LABELS
from association.query.reading import Scope
from association.query.templates.common import HISTORY_COLUMNS, PLAYER_STAT_COLUMNS, ResolvedSpan, TemplateResult, resolved_player, scoped_player, span_of
from association.query.templates.players import MADE_STAT_ATTEMPTS, _AdvancedStat, _ShootingStat

SEASON_LINES = Availability("player_season_stats_deduped")
"""Where a season-line answer is read from, for narrowing an ambiguous name
to the candidates with a row there - so a candidate it eliminates is one
whose answer would have been empty.

.. versionadded:: 5.0.0
   ``templates.players._SEASON_LINES`` was this.
"""

DEFAULT_HISTORY_SEASONS = 4
"""How many seasons a history shows where the question named no count.

.. versionadded:: 5.0.0
   From ``templates.players``.
"""

MAX_HISTORY_SEASONS = 20
"""The most seasons a history's count may ask for; a larger one is the
default four.

.. versionadded:: 5.0.0
   From ``templates.players``.
"""

MAX_COMPARED_PLAYERS = 4
"""The most players a comparison reads; names past it are not read.

.. versionadded:: 5.0.0
   From ``templates.players``.
"""

# A comparison's default line is longer than a single player's, because the two
# answers are read differently. "How many points did Luka average" wants the
# number it asked for; "compare Luka and SGA" is asking which of them is
# better, and three counting stats cannot answer that - they leave out both
# halves of the defensive line and everything a player gives back. Prose is
# already refused here for the same reason (a table, compose.say); a table costs
# nothing per extra row, so the rows a comparison actually turns on are all
# present by default.
#
# A NAMED stat still narrows to that one. Somebody asking "who scores more"
# gets scoring, not a wall.
COMPARE_STAT_LINE = ("points", "rebounds", "assists", "steals", "blocks", "turnovers", "fouls", "minutes")
"""The stats a comparison shows where none was named.

.. versionadded:: 5.0.0
   From ``templates.players``.
"""

# The NetPoints summary rows, in the order player_netpoints reports them:
# label -> the net_points_player column it reads. Per 100 possessions, not
# season totals, because a comparison is exactly the question totals answer
# badly - they mostly rank by playing time. The same choice fingerprint.py
# makes, and for the same reason.
NETPOINTS_COMPARE_ROWS: tuple[tuple[str, str], ...] = (
    ("net pts/100", "overall_per_100_poss"),
    ("  offense", "offense_per_100_poss"),
    ("  defense", "defense_per_100_poss"),
)
"""A comparison's NetPoints rows: the label the table prints, and the column read.

.. versionadded:: 5.0.0
   From ``templates.players``.
"""

# A career per-game figure is the career total over career games, never an
# average of season averages. Rebounds' total column is named differently from
# the per-stat key; minutes have none, and are weighted by games instead.
CAREER_TOTALS: dict[str, str | None] = {
    "points": "points",
    "rebounds": "totalRebounds",
    "assists": "assists",
    "steals": "steals",
    "blocks": "blocks",
    "turnovers": "turnovers",
    "minutes": None,
    "fouls": "fouls",
    "threePointFieldGoalsMade": "threePointFieldGoalsMade",
    "fieldGoalsMade": "fieldGoalsMade",
    "freeThrowsMade": "freeThrowsMade",
}
"""Each stat's season-total column, which a career sums (``None``: no total;
summed as the average times the games).

.. versionadded:: 5.0.0
   ``templates.players._CAREER_TOTALS`` was this.
"""


@dataclass(frozen=True)
class Statement:
    """One statement the season line runs: its SQL and its parameters,
    executed by the compiler's door
    (:func:`~association.query.compose.core.values_of`) and read by
    position, since two of its columns may share a name (an advanced
    stat's season read selects ``season`` twice).

    .. versionadded:: 5.0.0
    """

    sql: str
    params: list[Any]


def line_subject(con: duckdb.DuckDBPyConnection, scope: Scope) -> tuple[Entity, ResolvedSpan] | TemplateResult:
    """The player and span an unnarrowed ``player_stat`` reads the season
    line over - the span first, since it is what narrows an ambiguous name
    (:func:`~association.query.templates.common.scoped_player`) - or the
    clarifying question.

    .. versionadded:: 5.0.0
       ``templates.players._player_stat_season_line_subject`` was this.
    """
    return scoped_player(con, scope, "player_stat needs a player name", table="player_season_stats_deduped", available=SEASON_LINES, span=scope.span, season=scope.season)


def history_through(scope: Scope) -> int:
    """The last season a history reads: the one named, or the current one.
    A named season anchors the range's END rather than replacing it, so
    "3pt% over the 4 seasons through 2024" still spans four rows.

    .. versionadded:: 5.0.0
    """
    return scope.season or current_season()


def history_subject(con: duckdb.DuckDBPyConnection, scope: Scope) -> Entity | TemplateResult:
    """The player a history is about, narrowed over every season the
    history could read, not the last N: the read takes each player's last
    seasons up to :func:`history_through` wherever they fall, so a player
    who retired a decade earlier still has an answer. Not
    :func:`line_subject`'s settling, on purpose: a history has no one
    season to narrow a name by, and the two resolve an ambiguous name
    differently.

    .. versionadded:: 5.0.0
       ``templates.players._player_history_subject`` was this.
    """
    return resolved_player(con, scope.player, "player_history needs a player name", available=SEASON_LINES, through=history_through(scope))


def compared_player(con: duckdb.DuckDBPyConnection, name: str, season: int) -> Entity | TemplateResult:
    """One of a comparison's names, resolved for the season compared.

    .. versionadded:: 5.0.0
    """
    return resolved_player(con, name, available=SEASON_LINES, season=season)


def line_columns(wanted: list[str], shooting: _ShootingStat | None) -> tuple[list[str], str | None]:
    """The columns one season's line selects - the games, each wanted
    stat's per-game figure and total, a single made count's attempts, a
    percentage's makes and attempts - and that made count's attempted
    column, where there is one.

    .. versionadded:: 5.0.0
    """
    columns = ["gamesPlayed"]
    for name in wanted:
        per_game, total, _ = PLAYER_STAT_COLUMNS[name]
        columns.append(per_game)
        if total:
            columns.append(total)
    # A single made-count stat ("3-pointers made") brings its attempted
    # sibling along, the same "out of how many?" discipline SHOOTING_STATS
    # keeps for a percentage (F051, ISSUES.md) - never for a multi-stat line,
    # where a bare made-count is one entry among several and asking "out of
    # how many" of only one of them would read as singling it out.
    attempted_col = MADE_STAT_ATTEMPTS.get(wanted[0]) if len(wanted) == 1 else None
    if attempted_col:
        columns.append(attempted_col)
    if shooting:
        columns += [shooting.made, shooting.attempted]
    return columns, attempted_col


def season_statement(athlete_id: str, columns: list[str], season: int, season_type: int) -> Statement:
    """One player's season line: ``columns`` (from ``PLAYER_STAT_COLUMNS``
    or ``HISTORY_COLUMNS``, never from a slot) of the deduplicated season
    table, which has already collapsed a traded player's per-stint rows into
    one, so no reader has to remember to.

    .. versionadded:: 5.0.0
       ``templates.players._season_row``'s statement.
    """
    return Statement(f"SELECT {', '.join(columns)} FROM player_season_stats_deduped WHERE athlete_id = ? AND season = ? AND season_type = ?", [athlete_id, season, season_type])


def career_statement(athlete_id: str, season_type: int, wanted: list[str], shooting: _ShootingStat | None) -> tuple[Statement, str | None]:
    """A career line summed from the season table: the games, the first and
    last season and how many, then per wanted stat its per-game figure and
    its total, then a single made count's attempts, then a percentage's
    makes and attempts. A season whose total is missing falls back to its
    average times its games, and each per-game figure is divided by the
    games that actually carry that stat. Returns the made count's attempted
    column beside it, where there is one.

    .. versionadded:: 5.0.0
       ``templates.players._career_player_stat``'s statement.
    """
    selects = ["SUM(gamesPlayed)", "MIN(season)", "MAX(season)", "COUNT(*)"]
    for stat in wanted:
        per_game = PLAYER_STAT_COLUMNS[stat][0]
        total = CAREER_TOTALS[stat]
        amount = f"COALESCE({total}, {per_game} * gamesPlayed)" if total else f"{per_game} * gamesPlayed"
        selects += [f"SUM({amount}) / SUM(CASE WHEN {amount} IS NOT NULL THEN gamesPlayed END)", f"SUM({amount})"]
    # A single made-count stat's attempted total, never a multi-stat line's
    # (F051, ISSUES.md) - see line_columns.
    attempted_col = MADE_STAT_ATTEMPTS.get(wanted[0]) if len(wanted) == 1 else None
    if attempted_col:
        selects.append(f"SUM({attempted_col})")
    if shooting:
        made, attempted = shooting.made, shooting.attempted
        selects += [f"SUM(CASE WHEN {attempted} IS NOT NULL THEN {made} END)", f"SUM({attempted})"]
    sql = f"SELECT {', '.join(selects)} FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? AND gamesPlayed > 0"
    return Statement(sql, [athlete_id, season_type]), attempted_col


def advanced_span(span: ResolvedSpan) -> ResolvedSpan:
    """The span an advanced stat is read over: a career rebuilt against
    ``player_season_advanced_stats``, which starts in 1994 and has 1993 as
    a phantom copy of it - a shorter reach than the season line - so a
    career excludes the phantom by name; one season as it is.

    .. versionadded:: 5.0.0
    """
    return span_of("career", span.season, span.season_type, "player_season_advanced_stats") if span.career else span


def advanced_statement(athlete_id: str, span: ResolvedSpan, spec: _AdvancedStat) -> Statement:
    """One player's computed advanced stat over ``span``
    (:func:`advanced_span`): the figure, its volume, the games, the first
    and last season, how many seasons carry it and how many do not.

    A career is weighted by the stat's own denominator, and every figure
    but the last is filtered to the seasons that actually carry the stat,
    while the last counts the ones that do not. A season ESPN served empty
    has a row here with 0 attempts and a NULL rate
    (``player_season_advanced_stats`` is summed from the STORED box scores,
    and 2013-2018 has whole team-seasons of empty ones) - so an unfiltered
    SUM(games_played) counts games the rate never saw, and an unfiltered
    MIN/MAX names a span the answer does not cover. Jimmy Butler is the
    worked example: 2013, 2014, 2015 and 2017 are empty, and a career
    "2012-2026" over 824 games was really 12 seasons over 534.

    .. versionadded:: 5.0.0
       ``templates.players._player_stat_advanced``'s statement.
    """
    where, params = span.clause("season")
    if span.career:
        assert spec.weight is not None
        have = f"{spec.column} IS NOT NULL"
        selects = (
            f"SUM({spec.column} * {spec.weight}) / NULLIF(SUM({spec.weight}), 0), SUM({spec.weight}), "
            f"SUM(games_played) FILTER (WHERE {have}), MIN(season) FILTER (WHERE {have}), MAX(season) FILTER (WHERE {have}), "
            f"COUNT(*) FILTER (WHERE {have}), COUNT(*) FILTER (WHERE NOT {have})"
        )
    else:
        selects = f"{spec.column}, NULL, games_played, season, season, 1, 0"
    return Statement(f"SELECT {selects} FROM player_season_advanced_stats WHERE athlete_id = ? AND season_type = ? AND {where}", [athlete_id, span.season_type, *params])


def history_seasons(scope: Scope) -> int | None:
    """How many seasons a history reads: ``None`` for a career - every
    season, however many: not the default four, and not a count the model
    put in ``limit``, which the router asks it for on this intent whether or
    not the question gave one - else the count asked for, up to
    :data:`MAX_HISTORY_SEASONS`, or :data:`DEFAULT_HISTORY_SEASONS`.

    .. versionadded:: 5.0.0
    """
    if scope.span == "career":
        return None
    limit = scope.limit
    return limit if limit is not None and limit <= MAX_HISTORY_SEASONS else DEFAULT_HISTORY_SEASONS


def history_statement(athlete_id: str, stat: str, season_type: int, latest: int, seasons: int | None) -> Statement:
    """A stat season by season from the season line, newest first: each
    season, its games and the stat's columns (``HISTORY_COLUMNS``), every
    season up to ``latest`` for a career (``seasons`` ``None``) or the last
    ``seasons`` of them.

    .. versionadded:: 5.0.0
       ``templates.players._player_history_read``'s statement.
    """
    columns = HISTORY_COLUMNS[stat][1]
    bound = "" if seasons is None else " LIMIT ?"
    return Statement(
        f"SELECT season, gamesPlayed, {', '.join(c for c, _, _ in columns)} FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? AND season <= ? ORDER BY season DESC{bound}",
        [athlete_id, season_type, latest, *([] if seasons is None else [seasons])],
    )


def history_total_statement(athlete_id: str, stat: str, season_type: int, latest: int) -> Statement:
    """The plain career total behind a per-season counting column - the
    stored season total where the table has one, ``avg * gamesPlayed`` where
    it does not (minutes), summed exactly the way a career line sums one
    (:func:`career_statement`; F041, ISSUES.md).

    .. versionadded:: 5.0.0
       ``templates.players._player_history_career_count``'s statement.
    """
    per_game_col = HISTORY_COLUMNS[stat][1][0][0]
    total_col = CAREER_TOTALS.get(stat)
    amount = f"COALESCE({total_col}, {per_game_col} * gamesPlayed)" if total_col else f"{per_game_col} * gamesPlayed"
    return Statement(f"SELECT SUM({amount}) FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? AND season <= ? AND gamesPlayed > 0", [athlete_id, season_type, latest])


def netpoints_statement(athlete_id: str, season: int, season_type: int) -> Statement:
    """One player's NetPoints summary for a comparison
    (:data:`NETPOINTS_COMPARE_ROWS`' columns). ``net_points_player`` uses
    its OWN string season_type; filtering it with the numeric one every
    other table uses silently matches nothing. The table exists only where
    the NetPoints fetch was run, so its absence is the caller's to catch.

    .. versionadded:: 5.0.0
       ``templates.players._compare_netpoints``' statement.
    """
    label = SEASON_TYPE_LABELS.get(season_type, "Regular Season")
    selected = ", ".join(column for _, column in NETPOINTS_COMPARE_ROWS)
    return Statement(f"SELECT {selected} FROM net_points_player WHERE athlete_id = ? AND season = ? AND net_points_season_type = ?", [athlete_id, season, label])
