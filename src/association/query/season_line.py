"""The season line: a second relation beside the player-games relation
(:mod:`association.query.player_games`) - ``player_season_stats_deduped``,
one row per player per season (a traded player's stints already folded into
one), reaching back to 1976-77 where the box scores start in 1993-94 - and
``player_season_advanced_stats`` beside it, the computed stats summed from
the box scores season by season, with the season-shaped NetPoints tables a
ranking also reads.

This module holds what the season line's readers
(:mod:`association.query.compose.seasons`,
:mod:`association.query.compose.rankings`) share and nothing that words an
answer: the subject settled over it, the span a read covers, and each
statement a read runs - a season's line, a career's summed line, an
advanced stat's season or weighted career, a stat season by season and the
career total beneath it, the NetPoints summary a comparison shows, a
season's or a career's ranking with the rules it is ranked under (the
qualifier, the traded-player dedup, the postseason copy, the career pool),
and the seasons a player is on record for (the defaulted-season redirect,
the first and last season the compiler and two charts read). Every
statement is built here and executed through the compiler's one door
(:func:`~association.query.compose.core.values_of`, or
:func:`~association.query.compose.core.rows_of` where a ranking reads its
rows by column name): by a reader, or by the ranking and the redirect
here, which run more than one statement and take the door at call time,
since the compiler imports this module. Phase 2's slice (iii)
(``ROADMAP.md``): the statements are the retired templates'
(``templates.players._season_row``, ``_career_player_stat``,
``_player_stat_advanced``, ``_player_history_read``,
``_player_history_career_count``, ``_compare_netpoints``), moved whole;
since step 4 the ranking's (``query/leaderboard.py``, folded in whole, its
own execution gone) and the season-range reads
(``templates.common.season_redirect``,
``templates.players.seasons_on_record``, a shot chart's and a NetPoints
answer's own) are here too.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from difflib import get_close_matches
from typing import Any

import duckdb

from association.nba.coverage import COVERAGE, POSTSEASON, REGULAR_SEASON
from association.nba.franchises import season_name_sql
from association.nba.season import current_season
from association.query.answer import Reply
from association.query.entities import Ambiguous, Availability, Entity, NotFound, resolve_team
from association.query.metrics import EXTRA_FIELD_COLUMNS, LEADERBOARD_METRICS, SEASON_TYPE_LABELS, CareerAggregate, LeaderboardMetric
from association.query.player_games import season_type_clause

# The most rows a ranking lists: the reader's one cap on a question's count
# (``reading.MAX_LIMIT``, which ``_clamp_limit`` applies before a ranking is
# asked for). Applied to the SQL LIMIT itself as well, so a direct caller's
# count cannot fetch more. It was a cap of its own, 100, for the retired
# agent's model-supplied ``limit``; with the agent gone the ranking reader's
# clamped count (``compose.rankings``) is the only one that reaches here.
from association.query.reading import MAX_LIMIT, Scope
from association.query.result import Grouped
from association.query.templates.common import HISTORY_COLUMNS, PLAYER_STAT_COLUMNS, ResolvedSpan, resolved_player, scoped_player, span_of
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


def line_subject(con: duckdb.DuckDBPyConnection, scope: Scope) -> tuple[Entity, ResolvedSpan] | Reply:
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


def history_subject(con: duckdb.DuckDBPyConnection, scope: Scope) -> Entity | Reply:
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


def compared_player(con: duckdb.DuckDBPyConnection, name: str, season: int) -> Entity | Reply:
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


def _values(con: duckdb.DuckDBPyConnection, statement: Statement) -> list[tuple[Any, ...]]:
    """``statement`` run through the compiler's one door
    (:func:`~association.query.compose.core.values_of`), for the reads this
    module runs itself. At call time: the compiler imports this module."""
    from association.query.compose.core import values_of

    return values_of(con, statement)


def _records(con: duckdb.DuckDBPyConnection, statement: Statement) -> list[dict[str, Any]]:
    """``statement``'s rows by column name, as the SELECT names them, through
    the same door (:func:`~association.query.compose.core.rows_of`) - a
    ranking's rows, which its reader reads by name."""
    from association.query.compose.core import rows_of

    return rows_of(con, statement)


def _season_range(rows: list[tuple[Any, ...]]) -> tuple[int, int] | None:
    """A ``MIN(season), MAX(season)`` statement's answer: the two seasons, or
    None with nothing on record."""
    row = rows[0] if rows else None
    if row is None or row[0] is None:
        return None
    return int(row[0]), int(row[1])


def redirect_statement(athlete_id: str, season_type: int | str, table: str, *, athlete_column: str = "athlete_id", season_type_column: str = "season_type") -> Statement:
    """The first and last season ``athlete_id`` has a row in ``table`` for
    ``season_type`` (:func:`season_redirect`'s statement).

    .. versionadded:: 5.0.0
    """
    return Statement(f"SELECT MIN(season), MAX(season) FROM {table} WHERE {athlete_column} = ? AND {season_type_column} = ?", [athlete_id, season_type])


def season_redirect(
    con: duckdb.DuckDBPyConnection, athlete_id: str, season_type: int | str, table: str, *, athlete_column: str = "athlete_id", season_type_column: str = "season_type"
) -> tuple[int, int] | None:
    """The first and last season ``athlete_id`` has a row in ``table`` for
    ``season_type`` - or None with nothing on record there at all.

    Backs the redirect a defaulted season's empty refusal gives (issue #18): a
    retired player's question that names no season used to be answered as
    though the current one had been asked outright - a true statement about
    the wrong year, the mirror-image refusal AGENTS.md warns against. Reading
    the player's own range lets the answer redirect instead of guessing which
    season, career, or nothing was meant. ``season_type_column`` names a
    table that keeps its own (``net_points_player``'s string
    ``net_points_season_type``, read with the label, not the number).

    .. versionchanged:: 5.0.0
       Public (``_season_redirect`` until then): the single-game high's
       reader takes it (``compose.highs``). Moved here from
       ``templates.common`` with ``season_type_column``, so a NetPoints
       answer's redirect is this read and not one of its own.
    """
    return _season_range(_values(con, redirect_statement(athlete_id, season_type, table, athlete_column=athlete_column, season_type_column=season_type_column)))


def seasons_on_record(con: duckdb.DuckDBPyConnection, athlete_id: str, season_type: int) -> tuple[Any, Any]:
    """A player's first and last season in the per-player season table, which
    reaches back to 1976-77 - before any box score here. A postseason copied
    from the regular season is not a postseason on record.

    .. versionchanged:: 4.4.0
       Honors :data:`~association.query.player_games.BOTH_SEASON_TYPES`
       through :func:`~association.query.player_games.season_type_clause`,
       rather than an equality that a sentinel outside (2, 3) could never
       match.

    .. versionchanged:: 5.0.0
       Public (``_seasons_on_record`` until then): the compiler and the
       count's and the high's readers take it. Moved here from
       ``templates.players``.
    """
    copy = f" AND {not_a_postseason_copy(('points',))}" if season_type == POSTSEASON else ""
    type_clause, type_params = season_type_clause("t.season_type", season_type)
    rows = _values(con, Statement(f"SELECT MIN(t.season), MAX(t.season) FROM player_season_stats t WHERE t.athlete_id = ? AND {type_clause}{copy}", [athlete_id, *type_params]))
    return (rows[0][0], rows[0][1]) if rows else (None, None)


def seasons_played(con: duckdb.DuckDBPyConnection, athlete_id: str, season_type: int) -> tuple[int, int] | None:
    """The first and last season ``athlete_id`` actually played
    ``season_type`` (a season line with games in it), or None with nothing
    on record for that season type - what a career shot chart measures the
    2002 shot floor against.

    .. versionadded:: 5.0.0
       ``templates.shots._career_shot_span``'s statement, moved here.
    """
    return _season_range(
        _values(con, Statement("SELECT MIN(season), MAX(season) FROM player_season_stats_deduped WHERE athlete_id = ? AND season_type = ? AND gamesPlayed > 0", [athlete_id, season_type]))
    )


# The ranking: a season's or a career's leaders by one metric, with every
# correctness rule applied here rather than left to a reader to re-derive -
# the season defaults to the CURRENT one rather than whichever happens to
# have data loaded; rate and percentage metrics get a qualifying minimum
# sample, since a garbage-time cameo otherwise tops the board; a traded
# player is deduplicated to one row; a postseason row that is really a copy
# of the regular season is dropped. `team` and `fields` are deliberately
# narrow (a resolved team filter and a fixed whitelist of extra box-score
# columns) rather than a free-text filter/column passthrough. A career
# ranking is a separate statement, because its pool is a different thing and
# has to be reported as one: every player whose career reached 1993-94, not
# every player who ever played. Until step 4 this was query/leaderboard.py,
# which executed its own statements.

# What a `scales_with_schedule` floor was calibrated against - see
# `metrics.LeaderboardMetric.scales_with_schedule` and `default_min_sample`
# below. Not `nba.season`'s business: this is the schedule length ts_pct's 550
# and efg_pct's 480 were measured against (ISSUES.md #13), not a fact about
# any one season.
SCHEDULE_BASE_GAMES = 82


class LeaderboardError(Exception):
    """Carries a message naming what the ranking could not do - an unknown
    metric with the nearest real one, a season type out of range, a team
    matching nothing - which the template answers with or refuses on."""


@dataclass
class LeaderboardResult:
    """A ranked leaderboard plus the qualifiers that produced it.

    ``min_sample_applied`` and ``min_sample_column`` are part of the answer, not
    bookkeeping: they are what makes "why is this player missing?" answerable.
    """

    metric: str
    label: str
    season: int
    season_type: int | None
    min_sample_applied: int | None
    min_sample_column: str | None
    team: str | None
    team_name: str | None
    rows: list[dict[str, Any]]
    #: Each row's athlete id, aligned by index with ``rows`` - kept OUTSIDE the
    #: row dicts rather than as one more key in them, so it never rides along
    #: into an answer's ``data`` (which carries ``rows`` verbatim for the
    #: page to render) the way a raw id leaking into an answer was already a
    #: fixed regression once. A caller that needs a fact the ranked table itself
    #: does not carry - each player's team, for a leaderboard that asked to
    #: see it (F017, ISSUES.md) - looks it up by these ids.
    #:
    #: .. versionadded:: 4.4.0
    athlete_ids: list[str | None] = field(default_factory=list)


@dataclass
class CareerLeaderboardResult:
    """A career leaderboard plus the pool and the qualifier that produced it.

    ``pool_first_season`` is part of the answer for the same reason the
    qualifier is. The season table is fetched per player over a whole career,
    and players are discovered from box scores, which begin in 1993-94. So the
    pool is every player whose career reached that season, counted in full, and
    nobody whose career ended before it. An answer that does not say so is
    presenting a list without Kareem Abdul-Jabbar as the all-time one.

    Each row carries ``value``, ``games``, and the ``first_season`` and
    ``last_season`` of the career summed; a percentage's rows also carry the
    summed makes and attempts under their column names.

    .. versionadded:: 2.1.0
    """

    metric: str
    label: str
    season_type: int
    min_sample_applied: int | None
    min_sample_column: str | None
    pool_first_season: int
    rows: list[dict[str, Any]]


SEASON_TOTAL_OF = {
    "avg_points": "total_points",
    "avg_rebounds": "total_rebounds",
    "avg_assists": "total_assists",
    "avg_steals": "total_steals",
    "avg_blocks": "total_blocks",
    "avg_turnovers": "total_turnovers",
    "avg_three_pointers_made": "total_three_pointers_made",
}
"""Per-game metric -> its season-total counterpart, for a question whose
``rate`` slot says "total".

.. versionadded:: 2.1.0
"""


def not_a_postseason_copy(columns: tuple[str, ...], alias: str = "t") -> str:
    """SQL that is true unless ``alias``'s postseason row copies the same
    player's regular season.

    436 of the 7,941 postseason rows in ``player_season_stats`` are exactly that.
    Eddy Curry never played a playoff game, and has 527 "playoff games" and
    6,820 playoff points, because each of his regular seasons is stored a second
    time as a postseason - enough to put him second on a career playoff scoring
    list. Since 1993-94 these copies are exactly the postseason rows with no
    postseason box score behind them (checked: every such row is one, and they
    are the only such rows), which is what makes matching on the stat line
    identification rather than a guess. Matching on games plus ``columns`` finds
    the same 436 rows that matching the whole line does.

    .. versionadded:: 2.1.0
    """
    same = " AND ".join(f"rs.{column} IS NOT DISTINCT FROM {alias}.{column}" for column in ("gamesPlayed", *columns))
    return (
        f"NOT EXISTS (SELECT 1 FROM player_season_stats rs WHERE rs.athlete_id = {alias}.athlete_id AND rs.season = {alias}.season "
        f"AND rs.season_type = {REGULAR_SEASON} AND rs.team_id IS NOT DISTINCT FROM {alias}.team_id AND {same})"
    )


def _value_sql(spec: LeaderboardMetric) -> str:
    if spec.ratio:
        made, attempted = spec.ratio
        return f"CAST(t.{made} AS DOUBLE) / NULLIF(t.{attempted}, 0)"
    return f"t.{spec.column}"


def _value_columns(spec: LeaderboardMetric) -> tuple[str, ...]:
    return spec.ratio if spec.ratio else (spec.column,)


def _team_games_for_season(con: duckdb.DuckDBPyConnection, season: int) -> int | None:
    """How many regular-season games a team typically played that season, read
    from the warehouse rather than a hardcoded per-season table.

    The MEDIAN of each team's own ``real_games`` count (home starts plus away
    starts), rounded to the nearest game. Not ``games``, which carries 151 rows
    that are not games (AGENTS.md, "Working on the query path" -> "Saying what
    you measured").

    MEDIAN over MAX or a fixed per-season constant because a shortened season
    is not always uniform across teams: 2020's ``real_games`` counts range
    64-75 (the pandemic hiatus, then an 8-game bubble restart for 22 of the 30
    teams; the other 8 never resumed), while 2021's 72-game season gave every
    team exactly 72. The median reads 72 for both - one number, not three, and
    the same number a person would call "the 2020 season" and "the 2021
    season" by. A full 82-game season still reads 82 despite the two Cup-final
    teams' extra counted game (DATA.md, "The NBA Cup final is stored as a
    regular-season game") landing in the tails rather than the middle, so a
    normal season's floor is untouched by this function existing at all.

    Returns ``None`` on anything that stops the query - most commonly
    ``real_games`` not existing, true of every fixture in this test suite that
    predates this function and is not this fix's to update - so a caller falls
    back to the unscaled floor rather than raising.

    .. versionadded:: 4.1.0
    """
    try:
        rows = _values(con, schedule_statement(season))
    except duckdb.Error:
        # Missing real_games (an older warehouse, or a fixture built before
        # this floor scaled) - degrade to the flat, unscaled floor rather than
        # taking the whole leaderboard down over a schedule-length lookup.
        return None
    row = rows[0] if rows else None
    if row is None or row[0] is None:
        return None
    return math.floor(row[0] + 0.5)


def schedule_statement(season: int) -> Statement:
    """How many regular-season games each team played in ``season``, the
    median of them (:func:`default_min_sample`'s schedule length).

    .. versionadded:: 5.0.0
       ``leaderboard._team_games_for_season``'s statement.
    """
    return Statement(
        "WITH per_team AS ("
        "  SELECT home_team_id AS team_id, COUNT(*) AS n FROM real_games WHERE season = ? AND season_type = ? GROUP BY 1"
        "  UNION ALL"
        "  SELECT away_team_id AS team_id, COUNT(*) AS n FROM real_games WHERE season = ? AND season_type = ? GROUP BY 1"
        "), totals AS (SELECT team_id, SUM(n) AS games FROM per_team GROUP BY 1)"
        "SELECT MEDIAN(games) FROM totals",
        [season, REGULAR_SEASON, season, REGULAR_SEASON],
    )


def _scale_min_sample(base: int, team_games: int) -> int:
    """Scale an 82-game-season floor to a season whose teams played
    ``team_games`` games instead, rounding half up.

    Half up, not half to even or truncated, because a qualifier exists to
    exclude a small sample on purpose: at the one boundary where the scaled
    value lands exactly on a half-game, the deliberate choice is the stricter
    (higher) floor, not the more permissive one. Away from that boundary the
    rounding direction is not a choice at all - it is nearest, same as it
    would be by any other rule.

    .. versionadded:: 4.1.0
    """
    return math.floor(base * team_games / SCHEDULE_BASE_GAMES + 0.5)


def default_min_sample(
    spec: LeaderboardMetric,
    season_type: int,
    con: duckdb.DuckDBPyConnection | None = None,
    season: int | None = None,
) -> int | None:
    """The qualifier a season ranking applies when the question gives none.

    A postseason floor is its own constant (see
    ``LeaderboardMetric.postseason_min_sample``), never scaled by this
    function. A regular-season floor with ``scales_with_schedule`` set is
    scaled to the season's own team-game count when ``con`` and ``season`` are
    given; without them - or without a usable ``real_games`` for that season -
    it falls back to the flat, 82-game-calibrated value, exactly as it read
    before ``scales_with_schedule`` existed.

    .. versionadded:: 2.1.0

    .. versionchanged:: 4.1.0
       Added ``con`` and ``season``, and the scaling itself - see
       ``LeaderboardMetric.scales_with_schedule`` and ISSUES.md #13.
    """
    if season_type == POSTSEASON and spec.postseason_min_sample is not None:
        return spec.postseason_min_sample
    base = spec.default_min_sample
    if base is None or not spec.scales_with_schedule or season_type != REGULAR_SEASON or con is None or season is None:
        return base
    team_games = _team_games_for_season(con, season)
    return base if team_games is None else _scale_min_sample(base, team_games)


def _run_leaderboard_validate(metric: str, season_type: int, fields: list[str] | None) -> LeaderboardMetric:
    """The three input checks ``run_leaderboard`` raises on, in the order they
    are validated: an unknown metric (with a close-match suggestion), an
    invalid ``season_type``, and any ``fields`` entry outside the known set."""
    spec = LEADERBOARD_METRICS.get(metric)
    if spec is None:
        # A close-match suggestion (e.g. "points" -> "avg_points") keeps a
        # wrong guess a one-turn fix - confirmed live, without it a wrong
        # metric name sent the model on an unrelated multi-turn detour that
        # eventually recovered but dropped the team/fields it had originally
        # been asked for.
        suggestion = get_close_matches(metric, LEADERBOARD_METRICS, n=1)
        hint = f" Did you mean {suggestion[0]!r}?" if suggestion else ""
        raise LeaderboardError(f"Error: unknown metric {metric!r}.{hint} Known metrics: {sorted(LEADERBOARD_METRICS)}")
    if season_type not in SEASON_TYPE_LABELS:
        raise LeaderboardError(f"Error: season_type must be 1 (preseason), 2 (regular season), or 3 (postseason) - got {season_type!r}.")
    unknown_fields = [f for f in fields or [] if f not in EXTRA_FIELD_COLUMNS]
    if unknown_fields:
        raise LeaderboardError(f"Error: unknown field(s) {unknown_fields}. Known fields: {sorted(EXTRA_FIELD_COLUMNS)}")
    return spec


def _run_leaderboard_team(con: duckdb.DuckDBPyConnection, team: str) -> tuple[str, str]:
    """Resolve a team filter to its id and display name, or raise - called only
    when ``team`` is not None, so the caller's own None case never reaches this."""
    match resolve_team(con, team):
        case Entity() as resolved:
            return resolved.id, resolved.name
        case Ambiguous(candidates=candidates):
            raise LeaderboardError(f"Error: {team!r} matches more than one team: {candidates}. Be more specific.")
        case NotFound():
            raise LeaderboardError(f"Error: no team found matching {team!r}.")


def _run_leaderboard_select(spec: LeaderboardMetric, fields: list[str] | None, resolved_season: int, season_type: int) -> tuple[list[str], str, list[Any]]:
    """SELECT columns and FROM clause, plus the params the optional ``fields``
    join needs.

    A box-score join, keyed on (athlete_id, season, season_type) and deduped
    to the season-combined row (same pattern as a traded player's
    season-total row), is needed for `fields` regardless of which table the
    ranked metric itself lives in - player_season_stats is the one table
    every metric can join to this way.
    """
    # athlete_id rides along unconditionally - it costs nothing (it is already
    # the join key) and it is what lets a caller look up something the ranked
    # table itself does not carry, such as each player's team (F017, ISSUES.md).
    select_cols = ["p.display_name AS display_name", "p.athlete_id AS athlete_id", f"{_value_sql(spec)} AS value"]
    select_cols.extend(f"t.{col}" for col in spec.extra_columns)
    select_cols.extend(f"box.{EXTRA_FIELD_COLUMNS[f]} AS {f}" for f in fields or [])
    from_clause = f"FROM {spec.table} t JOIN players p ON p.athlete_id = t.{spec.id_column}"
    params: list[Any] = []
    if fields:
        from_clause += (
            " LEFT JOIN (SELECT * FROM player_season_stats WHERE season = ? AND season_type = ? "
            "QUALIFY ROW_NUMBER() OVER (PARTITION BY athlete_id ORDER BY (team_id IS NULL) DESC) = 1) box "
            f"ON box.athlete_id = t.{spec.id_column}"
        )
        params.extend([resolved_season, season_type])
    return select_cols, from_clause, params


def _run_leaderboard_where(
    spec: LeaderboardMetric,
    resolved_season: int,
    season_type: int,
    season_type_value: int | str,
    effective_min_sample: int | None,
    resolved_team_id: str | None,
    metric: str,
) -> tuple[list[str], list[Any]]:
    """The WHERE clause and its params: season, season_type, the postseason-copy
    exclusion, the minimum-sample qualifier, and an optional team filter - in
    the order ``run_leaderboard`` applies them."""
    where = [f"t.{spec.season_column} = ?"]
    params: list[Any] = [resolved_season]
    if spec.has_season_type:
        where.append(f"t.{spec.season_type_column} = ?")
        params.append(season_type_value)
    if spec.table == "player_season_stats" and season_type == POSTSEASON:
        where.append(not_a_postseason_copy(_value_columns(spec)))
    if effective_min_sample is not None:
        if spec.min_sample_column is None:
            raise LeaderboardError(f"Error: metric {metric!r} has no minimum-sample column to apply min_sample to.")
        where.append(f"t.{spec.min_sample_column} >= ?")
        params.append(effective_min_sample)
    if resolved_team_id is not None:
        # A per-stint EXISTS check, not the deduped `box` join above - a traded
        # player's deduped/combined row has team_id IS NULL, which would
        # wrongly exclude them from every team's roster even though they really
        # did play for one of their stint teams that season.
        where.append(f"EXISTS (SELECT 1 FROM player_season_stats pss WHERE pss.athlete_id = t.{spec.id_column} AND pss.season = ? AND pss.season_type = ? AND pss.team_id = ?)")
        params.extend([resolved_season, season_type, resolved_team_id])
    return where, params


def ranking_statement(spec: LeaderboardMetric, metric: str, season: int, season_type: int, *, fields: list[str] | None, min_sample: int | None, team_id: str | None, limit: int) -> Statement:
    """A season's ranking by ``spec`` (:func:`run_leaderboard`'s statement):
    each player's name, id and figure, the metric's own extra columns and the
    box-score ``fields`` asked for, over ``season`` and ``season_type`` under
    the qualifier ``min_sample``, a team's players only where ``team_id`` is
    given, a traded player once, highest first, at most ``limit`` (never past
    :data:`~association.query.reading.MAX_LIMIT`).

    Raises:
        LeaderboardError: a qualifier asked of a metric with no column to
            apply it to.

    .. versionadded:: 5.0.0
       ``leaderboard.run_leaderboard``'s statement.
    """
    season_type_value: int | str = SEASON_TYPE_LABELS[season_type] if spec.season_type_is_string else season_type
    select_cols, from_clause, select_params = _run_leaderboard_select(spec, fields, season, season_type)
    where, where_params = _run_leaderboard_where(spec, season, season_type, season_type_value, min_sample, team_id, metric)
    params = select_params + where_params
    qualify = "QUALIFY ROW_NUMBER() OVER (PARTITION BY t.athlete_id ORDER BY (t.team_id IS NULL) DESC) = 1" if spec.dedup_traded else ""
    sql = f"SELECT {', '.join(select_cols)} {from_clause} WHERE {' AND '.join(where)} {qualify} ORDER BY value DESC NULLS LAST, display_name LIMIT ?"
    params.append(max(1, min(limit, MAX_LIMIT)))
    return Statement(sql, params)


def run_leaderboard(
    con: duckdb.DuckDBPyConnection,
    metric: str,
    season: int | None = None,
    season_type: int = 2,
    min_sample: int | None = None,
    team: str | None = None,
    fields: list[str] | None = None,
    limit: int = 10,
) -> LeaderboardResult:
    """Rank players by one known metric, with every correctness rule applied.

    Returns:
        The ranked rows, with the season and the qualifier that produced them.

    Raises:
        LeaderboardError: with a message written for the model to read and act
            on - an unknown metric (with a close-match suggestion), an
            ambiguous team, or a table needing a warehouse flag that was not
            used.

    .. versionchanged:: 2.1.0
       A percentage ranks makes over attempts rather than ESPN's rounded
       column; a postseason can carry its own qualifier; a postseason row that
       copies the regular season is excluded.

    .. versionchanged:: 4.1.0
       A ``scales_with_schedule`` metric's default floor is scaled to the
       season's own team-game count in a shortened season (ISSUES.md #13),
       and ``min_sample_applied`` on the result always names the number
       actually applied, scaled or not.
    """
    spec = _run_leaderboard_validate(metric, season_type, fields)

    resolved_season = season if season is not None else current_season()
    effective_min_sample = min_sample if min_sample is not None else default_min_sample(spec, season_type, con, resolved_season)

    resolved_team_id: str | None = None
    resolved_team_name: str | None = None
    if team is not None:
        resolved_team_id, resolved_team_name = _run_leaderboard_team(con, team)

    statement = ranking_statement(spec, metric, resolved_season, season_type, fields=fields, min_sample=effective_min_sample, team_id=resolved_team_id, limit=limit)
    try:
        row_dicts = _records(con, statement)
    except Exception as exc:  # e.g. the table needs a warehouse flag that wasn't used
        raise LeaderboardError(f"SQL error: {exc}" + (f" (requires: {spec.requires})" if spec.requires else "")) from exc

    # athlete_id is popped back OUT of each row dict here - it rides the query
    # only to be resolvable, never as a key of `rows` itself (see
    # LeaderboardResult.athlete_ids' own docstring for why).
    athlete_ids = [row_dict.pop("athlete_id", None) for row_dict in row_dicts]
    return LeaderboardResult(
        metric=metric,
        label=spec.label,
        season=resolved_season,
        season_type=season_type if spec.has_season_type else None,
        min_sample_applied=effective_min_sample,
        min_sample_column=spec.min_sample_column,
        team=team,
        team_name=resolved_team_name,
        rows=row_dicts,
        athlete_ids=athlete_ids,
    )


def _career_value_sql(career: CareerAggregate) -> str:
    # The denominator counts only seasons whose numerator is on record: 222
    # season rows carry games but NULL stats, and counting their games would
    # quietly lower every average they touch.
    has_value = f"t.{career.numerator} IS NOT NULL"
    if career.weighted:
        return f"SUM(t.{career.numerator} * t.gamesPlayed) / NULLIF(SUM(t.gamesPlayed) FILTER (WHERE {has_value}), 0)"
    if career.denominator:
        return f"CAST(SUM(t.{career.numerator}) AS DOUBLE) / NULLIF(SUM(t.{career.denominator}) FILTER (WHERE {has_value}), 0)"
    return f"SUM(t.{career.numerator})"


def run_career_leaderboard(
    con: duckdb.DuckDBPyConnection,
    metric: str,
    season_type: int = REGULAR_SEASON,
    min_sample: int | None = None,
    limit: int = 10,
) -> CareerLeaderboardResult:
    """Rank players by one metric summed over their whole careers.

    Totals are summed; a per-game value is the career total over career games,
    and a percentage career makes over career attempts - never an average of
    season averages, which weights a 10-game season like an 82-game one. Both
    carry a career qualifier (see :class:`~association.query.metrics.CareerAggregate`).

    The sum runs over per-team season rows, so a traded player's season counts
    once, through its stints, and never through the combined row - see
    :class:`~association.query.metrics.CareerAggregate` for why that row cannot
    be trusted.

    Returns:
        The ranked careers, with the pool's first season and the qualifier.

    Raises:
        LeaderboardError: an unknown metric, a metric with no career form, or a
            season type other than regular season or postseason.

    .. versionadded:: 2.1.0
    """
    spec = LEADERBOARD_METRICS.get(metric)
    if spec is None:
        raise LeaderboardError(f"Error: unknown metric {metric!r}. Known metrics: {sorted(LEADERBOARD_METRICS)}")
    career = spec.career
    if career is None:
        raise LeaderboardError(f"Error: {metric!r} has no career ranking - it needs team context the season rows do not carry, or its data starts too recently to span a career.")
    if season_type not in (REGULAR_SEASON, POSTSEASON):
        raise LeaderboardError(f"Error: a career ranking covers the regular season (2) or the postseason (3) - got {season_type!r}.")
    qualifier = min_sample if min_sample is not None else (career.postseason_min_sample if season_type == POSTSEASON else career.min_sample)
    statement = career_ranking_statement(spec, career, metric, season_type, qualifier=qualifier, limit=limit)
    try:
        rows = _records(con, statement)
    except Exception as exc:
        raise LeaderboardError(f"SQL error: {exc}") from exc

    coverage = COVERAGE[spec.table]
    return CareerLeaderboardResult(
        metric=metric,
        label=spec.label,
        season_type=season_type,
        min_sample_applied=qualifier,
        min_sample_column=spec.min_sample_column,
        pool_first_season=coverage.first_ranking_season or coverage.first_season,
        rows=rows,
    )


def career_ranking_statement(spec: LeaderboardMetric, career: CareerAggregate, metric: str, season_type: int, *, qualifier: int | None, limit: int) -> Statement:
    """A career ranking by ``spec`` (:func:`run_career_leaderboard`'s
    statement): each player's name, career figure (``career``'s aggregate),
    games and first and last season, a percentage's summed makes and
    attempts, over the per-team season rows of ``season_type``, under the
    career qualifier, highest first, at most ``limit``.

    Raises:
        LeaderboardError: a qualifier asked of a metric with no column to
            apply it to.

    .. versionadded:: 5.0.0
       ``leaderboard.run_career_leaderboard``'s statement.
    """
    has_value = f"t.{career.numerator} IS NOT NULL"
    value = _career_value_sql(career)
    select_cols = [
        "p.display_name AS display_name",
        f"{value} AS value",
        "SUM(t.gamesPlayed) AS games",
        "MIN(t.season) AS first_season",
        "MAX(t.season) AS last_season",
    ]
    if spec.ratio:
        select_cols.extend(f"SUM(t.{column}) FILTER (WHERE {has_value}) AS {column}" for column in spec.ratio)

    # team_id IS NOT NULL keeps the per-team rows and drops the combined one. A
    # player who was never traded has exactly one row a season, with his team
    # on it - checked: no season is a lone combined row.
    where = ["t.season_type = ?", "t.team_id IS NOT NULL"]
    params: list[Any] = [season_type]
    if season_type == POSTSEASON:
        where.append(not_a_postseason_copy(tuple(c for c in (career.numerator, career.denominator) if c)))
    having = [f"{value} IS NOT NULL"]
    if qualifier is not None:
        if spec.min_sample_column is None:
            raise LeaderboardError(f"Error: metric {metric!r} has no minimum-sample column to apply min_sample to.")
        having.append(f"SUM(t.{spec.min_sample_column}) FILTER (WHERE {has_value}) >= ?")
        params.append(qualifier)
    params.append(max(1, min(limit, MAX_LIMIT)))

    sql = (
        f"SELECT {', '.join(select_cols)} FROM {spec.table} t JOIN players p ON p.athlete_id = t.{spec.id_column} "
        f"WHERE {' AND '.join(where)} GROUP BY t.{spec.id_column}, p.display_name HAVING {' AND '.join(having)} ORDER BY value DESC, display_name LIMIT ?"
    )
    return Statement(sql, params)


MIN_SAMPLE_LABELS: dict[str, str] = {
    "total_minutes": "minutes",
    "gamesPlayed": "games",
    "games_played": "games",
    "minutes": "minutes",
    "fieldGoalsAttempted": "field-goal attempts",
    "threePointFieldGoalsAttempted": "3-point attempts",
    "freeThrowsAttempted": "free-throw attempts",
    "field_goals_attempted": "field-goal attempts",
    "true_shooting_attempts": "true-shooting attempts",
}
"""A qualifying column as a reader names it - its real name ("total_minutes",
"gamesPlayed") is not something to put in front of one. The ``of`` of a
ranking's ``minimum`` decision.

.. versionadded:: 5.0.0
   Moved from ``templates.players`` with the leaderboard's words.
"""


def most_recent_teams(con: duckdb.DuckDBPyConnection, athlete_ids: list[str], season: int, season_type: int) -> tuple[dict[str, str], bool]:
    """Each athlete's team for a ranking that asked to see it (F017,
    ISSUES.md): the team he played his most recent game for that season and
    season type - read off ``player_game_log``, the one table that orders a
    traded player's stints by date, unlike the ranked table itself (whose own
    "combined" row for a traded player has no single team at all). Team names
    are read for the season asked about (:func:`~association.nba.franchises.season_name_sql`),
    since a franchise's own name can differ by season.

    Returns each athlete's team by id, and whether ANY of them played for more
    than one team that season - the fact behind the "most recent team" note.

    .. versionadded:: 5.0.0
       Moved from ``templates.players._leaderboard_team_names``, unchanged.
    """
    rows = _records(con, recent_teams_statement(athlete_ids, season, season_type))
    names = {row["athlete_id"]: row["team"] for row in rows}
    return names, any(row["traded"] for row in rows)


def recent_teams_statement(athlete_ids: list[str], season: int, season_type: int) -> Statement:
    """Each of ``athlete_ids``' most recent team that season and season type,
    named for that season, and whether he played for more than one
    (:func:`most_recent_teams`' statement).

    .. versionadded:: 5.0.0
    """
    placeholders = ", ".join("?" for _ in athlete_ids)
    return Statement(
        f"""
        SELECT athlete_id, team, traded FROM (
            SELECT pgl.athlete_id AS athlete_id,
                   {season_name_sql("pgl.team_id", "pgl.season", "t.display_name")} AS team,
                   COUNT(DISTINCT pgl.team_id) OVER (PARTITION BY pgl.athlete_id) > 1 AS traded,
                   ROW_NUMBER() OVER (PARTITION BY pgl.athlete_id ORDER BY pgl.game_date DESC) AS rn
            FROM player_game_log pgl JOIN teams t ON t.team_id = pgl.team_id
            WHERE pgl.season = ? AND pgl.season_type = ? AND pgl.athlete_id IN ({placeholders})
        ) WHERE rn = 1
        """,
        [season, season_type, *athlete_ids],
    )


@dataclass(frozen=True)
class SeasonLineRanking:
    """The season line ranked, as the reader takes it: the result object the
    statement produced (its season, qualifier, team filter, career pool) and
    its rows as a :class:`~association.query.result.Grouped` body by
    ``player`` - a row per player, ``key`` his name, ``rank`` (tied values
    share one), and ``values`` by measure: the ranked figure under the
    metric's name (``ranked_by``), then every other column the row carries in
    the statement's order (a percentage's makes and attempts, a career's
    games and seasons, the "also" columns asked for, the team). ``traded``
    says whether a shown player played for more than one team that season,
    where the team column was asked for.

    .. versionadded:: 5.0.0
    """

    result: LeaderboardResult | CareerLeaderboardResult
    body: Grouped
    traded: bool = False


def rank_season_line(
    con: duckdb.DuckDBPyConnection,
    metric: str,
    *,
    career: bool,
    season: int | None,
    season_type: int,
    team: str | None,
    fields: list[str],
    limit: int,
) -> SeasonLineRanking:
    """The season line's ranking, the one door the reader calls
    (``compose.rankings.read_leaderboard``): a season's ranking
    (:func:`run_leaderboard`, with its default season, qualifier, traded-player
    dedup and postseason-copy exclusion) or a career's
    (:func:`run_career_leaderboard`, over its own pool), with each shown
    player's most recent team looked up where ``fields`` asks for ``"team"``
    (:func:`most_recent_teams`). ``fields`` beside ``"team"`` are the box-score
    columns :data:`~association.query.metrics.EXTRA_FIELD_COLUMNS` names; a
    career ranking takes none, which its caller refuses before asking.

    Raises:
        LeaderboardError: whatever the ranking raises, and a team column asked
            of a metric with no season type to look a team up by.

    .. versionadded:: 5.0.0
    """
    if career:
        found_career = run_career_leaderboard(con, metric, season_type=season_type, limit=limit)
        return SeasonLineRanking(result=found_career, body=_ranking_body(metric, found_career.rows))
    show_team = "team" in fields
    box_fields = [f for f in fields if f != "team"]
    found = run_leaderboard(con, metric, season=season, season_type=season_type, team=team, fields=box_fields or None, limit=limit)
    traded = False
    if show_team:
        if found.season_type is None:
            # A metric with no season_type (a fingerprint-shaped one) has nothing
            # for player_game_log's own season_type column to join on - refused
            # rather than silently dropping the team column nobody asked to lose.
            raise LeaderboardError(f"{found.label} has no season type to look a team up by")
        traded = _ranking_show_teams(con, found)
    return SeasonLineRanking(result=found, body=_ranking_body(metric, found.rows), traded=traded)


def _ranking_show_teams(con: duckdb.DuckDBPyConnection, result: LeaderboardResult) -> bool:
    """Each shown row's team, set on it last (F017, ISSUES.md), and whether
    anyone shown played for more than one team that season."""
    assert result.season_type is not None  # the caller already refused this case
    ids = [athlete_id for athlete_id in result.athlete_ids if athlete_id is not None]
    if not ids:
        for row in result.rows:
            row["team"] = "-"
        return False
    names, traded = most_recent_teams(con, ids, result.season, result.season_type)
    for athlete_id, row in zip(result.athlete_ids, result.rows, strict=True):
        row["team"] = names.get(athlete_id, "-") if athlete_id is not None else "-"
    return traded


def _ranking_body(metric: str, rows: list[dict[str, Any]]) -> Grouped:
    """A ranking's rows as a :class:`~association.query.result.Grouped` body:
    the order the statement gave (the figure, highest first, then the name),
    and a competition rank - two players on the same figure share a rank, and
    the next takes the place after both."""
    ranked: list[dict[str, Any]] = []
    rank = 0
    previous: Any = object()
    for place, row in enumerate(rows, start=1):
        value = row.get("value")
        if place == 1 or value != previous:
            rank = place
        previous = value
        values = {metric: value, **{key: cell for key, cell in row.items() if key not in ("display_name", "value")}}
        ranked.append({"key": row["display_name"], "rank": rank, "values": values})
    return Grouped(by="player", ranked_by=metric, rows=tuple(ranked))
