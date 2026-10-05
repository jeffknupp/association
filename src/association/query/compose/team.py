"""The team as a subject in compose: a :class:`TeamQuery` over the
team-games relation (:mod:`association.query.team_games`), for a question
whose grammatical subject is a team and names no player - "how many 3
pointers have the magic made this season", "total points scored by the
toronto raptors in the last 10 games", "knicks point differential over the
last 7 games". A player named makes a team a NARROWING of him, never the
subject - that stays :mod:`association.query.compose.core`'s question,
unchanged.

Kept apart from ``core.py``'s player compiler on purpose: nothing here is
imported by, or imports from, its player-subject functions
(:func:`~association.query.compose.core.compile_query`,
:func:`~association.query.compose.core._resolve_named`, ...), so every K1/K2
rule measured for the player subject stays exactly as it was - the same
reason :class:`association.query.team_games.TeamNarrowed` is its own
dataclass rather than a subclass of
:class:`association.query.player_games.Narrowed`.

Two readers, mirroring the shape ``player_stat`` already keeps between a
season line and box scores:

- **Unnarrowed** ("how many 3-pointers have the Magic made this season") -
  the season's own TOTAL, read straight from ``team_season_stats``
  (:data:`SEASON_MEASURES`) - the same table :func:`association.query.templates.teams.team_stat`
  reads, but its raw total rather than the per-game average
  ``team_metrics.TEAM_METRICS`` carries. "This season" answered with a
  per-game figure is the wrong-shape bug this module exists to fix (F127,
  ISSUES.md): 11.7 threes a game is the right number for a different
  question than "how many has he made".
- **Narrowed** (an opponent, a venue, a date, ``since``/``until``, a game of
  a series, a calendar ``situation``, or an ``order``/``limit`` window) -
  summed straight from the team-games relation's own game-level columns
  (:data:`GAME_MEASURES`: points scored, points allowed, differential),
  through :func:`association.query.templates.common.scoped_team` and
  :func:`association.query.templates.common.team_games` - the same shared
  steps every other team template narrows through, never a hand-written
  clause here.

What this module does NOT do, on purpose, because it needs a join
``team_games`` does not have yet (ISSUES.md): a box-score count (3-pointers
made, rebounds, ...) narrowed to a window or an opponent - "3-pointers made
by the Magic over their last 10 games" - refuses (``Unsupported``) rather than
silently answering the season instead. A "last N games" question naming no
season type (F128/F129's own shape) is answered by :func:`association.query.templates.games.team_game_log`'s
existing team half directly (its ``_team_game_log_mixed`` already reads both
season types and merges by date) rather than duplicated here; this module's
narrowed reader is one season type at a time.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import duckdb

from association.nba.coverage import unavailable
from association.nba.franchises import season_name_sql
from association.nba.season import current_season
from association.query.conditions import _PLAYER_GAME_TABLES, _TEAM_LINE, _longest_runs_sql
from association.query.entities import Entity
from association.query.entities import team_named_in as team_named_in
from association.query.reading import DEFAULT_STREAK_LIMIT, Scope, _clamp_limit
from association.query.team_games import TeamNarrowed
from association.query.team_games import aggregate_sql as team_aggregate_sql
from association.query.team_games import grouped_sql as team_grouped_sql
from association.query.team_games import named as team_named
from association.query.team_games import rows_sql as team_rows_sql
from association.query.templates.common import ResolvedSpan, TemplateResult, TemplateUnsupported, resolved_team, scoped_team, span_of, whole_span
from association.query.templates.common import team_games as narrow_team_games
from association.query.templates.splits import _TEAM_STREAK_SELECT, PresenceSplit, _streak_league_team_narrowed, _with_without_read

from .core import Refused, Unsupported, rows_of

#: A team measure name -> the team-games relation column expression it
#: reads, for a NARROWED read. Only game-outcome figures live on
#: :data:`association.query.team_games.TEAM_GAMES_SQL` itself; a box-score
#: count needs a join this relation does not carry yet.
GAME_MEASURES: dict[str, str] = {
    "points": "tg.team_score",
    "points_allowed": "tg.opponent_score",
    "differential": "(tg.team_score - tg.opponent_score)",
}
"""A team measure name, mapped to its game-level column expression.

.. versionadded:: 4.4.0
"""

#: A team measure name -> its ``team_season_stats`` column, for an
#: UNNARROWED read - the season's own raw total. A whitelist, like
#: ``THRESHOLD_STAT_COLUMNS`` keeps for a player's box columns: the router's
#: ``stat`` slot is model-generated text, and this is the only place it can
#: reach SQL.
SEASON_MEASURES: dict[str, str] = {
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "steals": "steals",
    "blocks": "blocks",
    "turnovers": "turnovers",
    "threePointFieldGoalsMade": "threePointFieldGoalsMade",
    "fieldGoalsMade": "fieldGoalsMade",
    "freeThrowsMade": "freeThrowsMade",
    "fouls": "fouls",
}
"""A team measure name, mapped to its season-total column.

.. versionadded:: 4.4.0
"""


#: A team's games listed (the ``rows`` shape): each game's Eastern date, the
#: side it was played on, the opponent as that season named it, both scores,
#: the result, and the season - a postseason's by the calendar year it was
#: played in (``AGENTS.md``, "Select a postseason by the calendar year").
TEAM_ROW_COLUMNS: tuple[str, ...] = (
    "tg.eastern_date AS day",
    "tg.side",
    f"{season_name_sql('o.team_id', 'tg.season', 'o.display_name')} AS opponent",
    "tg.team_score",
    "tg.opponent_score",
    "tg.won",
    "CASE WHEN tg.season_type = 3 THEN year(tg.eastern_date) ELSE tg.season END AS season",
)
"""The columns a team's ``rows`` read selects, each by name.

.. versionadded:: 5.0.0
"""

#: The join the ``rows`` shape's opponent name needs.
_TEAM_ROWS_JOIN = " JOIN teams o ON o.team_id = tg.opponent_id"

#: The ``grouped`` shape's groups: a key over the subquery ``t``
#: (:func:`association.query.team_games.grouped_sql`), labeled as a team's
#: splits show them - the player compiler's group names
#: (:data:`~association.query.compose.core.GROUPS`) where it has one.
TEAM_GROUPS: dict[str, str] = {
    "venue": "t.home_away",
    "won": "CASE WHEN t.won THEN 'wins' ELSE 'losses' END",
    "month_of_year": "CAST(month(t.day) AS VARCHAR)",
}
"""A team ``grouped`` read's group -> the key its games are divided by.

.. versionadded:: 5.0.0
"""

#: What each game of a ``grouped`` read carries: the relation's own result
#: and scores, and the box-score columns the line averages, over
#: ``team_box_stats`` - which the relation does not carry. An inner join, so
#: a game with no team box row is in no group, as the splits always read it.
_TEAM_GROUPED_COLUMNS: tuple[str, ...] = (
    "tg.season",
    "tg.eastern_date AS day",
    "tg.side AS home_away",
    "tg.won",
    "tg.team_score",
    "tg.opponent_score",
    "tbs.offensiveRebounds",
    "tbs.defensiveRebounds",
    "tbs.assists",
    "tbs.threePointFieldGoalsMade",
    "tbs.fieldGoalsMade",
    "tbs.fieldGoalsAttempted",
)
_TEAM_GROUPED_JOIN = " JOIN team_box_stats tbs ON tbs.event_id = tg.event_id AND tbs.season = tg.season AND tbs.team_id = tg.team_id"

TEAM_LINE_MEASURES: dict[str, str] = {("threePointFieldGoalsMade" if name == "threes" else name): sql for name, _, sql in _TEAM_LINE}
"""A team's per-game line in a ``grouped`` read, by measure name -> its
aggregate over ``t`` (``conditions._TEAM_LINE``'s: points, points allowed,
rebounds, assists, threes - under the box column's name, as the player
compiler reads it - and FG%).

.. versionadded:: 5.0.0
"""


@dataclass(kw_only=True)
class TeamQuery:
    """A point over the team-games relation, for a team as the subject - the
    team counterpart of :class:`~association.query.compose.core.Query`, kept
    as its own dataclass rather than a third mode of it: the two relations
    share no columns and no reader. Every construction names its fields.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Holds the typed :class:`~association.query.reading.Scope` as ``scope``
       in place of the ``slots`` dict, and every field is keyword-only.

    .. versionchanged:: 5.0.0
       ``shape``: a ``"rows"`` point is the team's game log (ROADMAP plan
       item 6, step (g)).
    """

    #: The question's scoping, forwarded whole to the relation.
    scope: Scope
    #: A key of :data:`GAME_MEASURES` and/or :data:`SEASON_MEASURES`.
    measure: str = "points"
    #: ``"total"`` (the only aggregate this module computes today) or
    #: ``"record"`` (wins/losses, over the narrowed or season games).
    aggregate: str = "total"
    #: ``"scalar"`` (a sum, this module's own readers), ``"rows"`` (the
    #: team's games listed - ``game_log``'s team half), ``"grouped"`` (its
    #: splits - ``player_splits``' team half) or ``"run"`` (its longest run
    #: of wins or losses - ``streak``'s team half, read here by
    #: :func:`_compile_team_run`; the league's with no team named), the
    #: first two compiled by :func:`compile_team_over` and read by
    #: ``compose.logs``/``compose.splits``.
    shape: str = "scalar"
    #: A ``"grouped"`` shape's group: ``"none"`` for the team's own splits
    #: (``player_splits``' team half, which compiles one read per kind by a
    #: key of :data:`TEAM_GROUPS`), or ``"presence"`` - the team's games
    #: divided by whether named teammates played (``with_without``'s retired
    #: template, read by :func:`_compile_team_presence`).
    group: str = "none"


@dataclass
class TeamCompiled:
    """A :class:`TeamQuery` turned into SQL over the team-games relation: the
    statement, its parameters and what the relation settled - the team, the
    span and every narrowing. The team counterpart of
    :class:`~association.query.compose.core.Compiled`, executed through the
    same one door (:func:`~association.query.compose.core.rows_of`).

    .. versionadded:: 5.0.0
    """

    sql: str
    params: list[Any] | dict[str, Any]
    team: Entity | None
    span: ResolvedSpan
    narrowed: TeamNarrowed
    #: A ``run`` read's games in date order, with their own names bound in
    #: ``run_params`` - for the span's count and seasons
    #: (:func:`compile_team_range`); ``None`` otherwise.
    run_rows: str | None = None
    run_params: dict[str, Any] | None = None


@dataclass
class TeamResult:
    """A :class:`TeamQuery` answered: the team, the span it covers, and the
    number(s) - kept close to what :func:`association.query.compose.sentence.team_sentence`
    needs to phrase it, the same role :class:`~association.query.compose.core.Compiled`
    plays for the player subject.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       No ``coverage_note``: the agent appends
       :func:`~association.query.templates.common.coverage_caveat` to a
       composed answer as it does to a template's, and this carrying one too
       printed ESPN's 2001-playoffs note twice.
    """

    #: The team - or ``None`` for the league's longest run, which has no one
    #: team (the ``run`` shape with no team named).
    team: Entity | None
    span: ResolvedSpan
    measure: str
    aggregate: str
    value: float | None
    games: int
    wins: int | None = None
    losses: int | None = None
    narrowed_text: str = ""
    from_season_line: bool = False
    #: A note appended past the number - the season-total reader's own
    #: postseason addendum (F127: "...and added 78 more in the playoffs").
    note: str = ""
    #: The ``run`` shape's runs (:func:`~association.query.conditions._longest_runs_sql`'
    #: rows), longest first; empty for every other shape.
    runs: list[dict[str, Any]] = field(default_factory=list)
    #: The first and last season the ``run`` shape's games actually came
    #: from (a postseason's by the calendar year it was played in), for the
    #: span's label; ``None`` otherwise.
    first_season: int | None = None
    last_season: int | None = None
    #: The ``presence`` group's read (:func:`_compile_team_presence`): the
    #: games inside the teammates' time on the team, each marked with who
    #: held the condition; ``None`` for every other shape.
    presence: PresenceSplit | None = None


def _team_narrowed(scope: Scope) -> bool:
    """Whether the question narrows the games at all - the team counterpart
    of ``player_stat``'s own ``_player_stat_reads_box_scores``: an opponent,
    a venue, a date, ``since``/``until``, a game of a series, a calendar
    ``situation``, or a window (``order``/``limit``) all send the read to the
    game-level relation; nothing here means the plain season (or career)
    total.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       A quarter or half narrows too (the period relation's team half).
    """
    # The fields that narrow the games a team question reads - any of them
    # sends the read to the game-level relation rather than the season line.
    # A quarter or half is one too: read as the season line, "the magic's
    # first-quarter threes" would be their whole season's.
    if any((scope.opponent, scope.venue, scope.date, scope.since, scope.until, scope.game_n, scope.situation)):
        return True
    if scope.period is not None or scope.half is not None:
        return True
    return scope.order is not None or scope.limit is not None


def _resolved_team_subject(con: duckdb.DuckDBPyConnection, scope: Scope) -> Entity:
    """The team a question names, or the refusal wrapped as :class:`~association.query.compose.core.Refused`."""
    team_text = scope.team
    if team_text is None or not team_text.strip():
        raise Unsupported("no team named")
    season: int = scope.season if scope.season is not None else current_season()
    team = resolved_team(con, team_text, season=season)
    if isinstance(team, TemplateResult):
        raise Refused(team)
    return team


def _season_total(con: duckdb.DuckDBPyConnection, team: Entity, season: int, season_type: int, column: str) -> tuple[float, int] | None:
    """``column``'s value and the games it covers, from one team-season row -
    or ``None`` where the warehouse has no such row."""
    row = con.execute(f"SELECT {column}, gamesPlayed FROM team_season_stats WHERE team_id = ? AND season = ? AND season_type = ?", [team.id, season, season_type]).fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0]), int(row[1] or 0)


def _team_season_note(con: duckdb.DuckDBPyConnection, team: Entity, season: int, column: str) -> str:
    """A postseason addendum for an unnarrowed regular-season total - "and
    added 78 more in a 7-game playoff run" (F127) - said because leaving a
    finished postseason unmentioned under a "so far" question reads as though
    it does not exist, the same reasoning ``team_record``'s own cup-final
    mention already carries. Empty where the team has no postseason total on
    record for the year.

    .. versionadded:: 4.4.0
    """
    post = _season_total(con, team, season, 3, column)
    if post is None:
        return ""
    value, games = post
    return f" They added {value:,.0f} more over a {games}-game playoff run."


def _compile_team_season(con: duckdb.DuckDBPyConnection, q: TeamQuery, team: Entity) -> TeamResult:
    """The unnarrowed reader: the season's own raw total, straight from
    ``team_season_stats`` - F127's shape."""
    if q.measure not in SEASON_MEASURES:
        raise Unsupported(f"no season total on record for {q.measure!r}")
    season_type = q.scope.season_type or 2
    season: int = q.scope.season if q.scope.season is not None else current_season()
    column = SEASON_MEASURES[q.measure]
    found = _season_total(con, team, season, season_type, column)
    if found is None:
        message = f"The warehouse has no {season} team totals for the {team.name}."
        raise Refused(TemplateResult(data={"team": team.name, "season": season}, answer=message))
    value, games = found
    note = _team_season_note(con, team, season, column) if season_type == 2 else ""
    return TeamResult(team=team, span=ResolvedSpan(season, season_type), measure=q.measure, aggregate=q.aggregate, value=value, games=games, from_season_line=True, note=note)


def _team_games_narrowed(con: duckdb.DuckDBPyConnection, q: TeamQuery) -> tuple[TeamNarrowed, Entity, ResolvedSpan]:
    """``team``'s games, narrowed exactly as :func:`association.query.templates.games.team_quarter_points`
    narrows its own - through :func:`~association.query.templates.common.scoped_team`
    and :func:`~association.query.templates.common.team_games`, never a
    clause written here. Resolves the team itself too (rather than reusing
    :func:`_resolved_team_subject`'s separate lookup), so the name and the
    span it is read against always come from the one call that settles both
    together - the same order every other team template keeps."""
    scope = q.scope
    # The shared steps read the slot dict until they take the Scope.
    settled = scoped_team(con, scope, "no team named", span=scope.span, season=scope.season)
    if isinstance(settled, TemplateResult):
        raise Refused(settled)
    team, span = settled
    date = scope.date if scope.date is not None and len(scope.date) == 10 else None
    narrowed = narrow_team_games(con, team, span, scope, opponent=scope.opponent, date=date)
    if isinstance(narrowed, TemplateResult):
        raise Refused(narrowed)
    return narrowed, team, span


def _team_mixed(scope: Scope) -> bool:
    """Whether a window read spans both season types: "last N games" naming
    no season type (``season_type_unstated``), with no date, career or game
    of a series fixing one - the same test the team log makes."""
    return scope.season_type_unstated and not scope.date and not scope.span and not scope.game_n


def _compile_team_games_mixed(con: duckdb.DuckDBPyConnection, q: TeamQuery) -> TeamResult:
    """The window sum over BOTH season types, for a "last N games" question
    naming neither: the games the team log lists for it
    (``compose.logs._team_mixed_rows``), summed here - so the total is
    over the same games the log shows, and the sentence says how many of
    each type it kept, the default made visible (AGENTS.md, "a reasonable
    default beats a question").

    .. versionadded:: 5.0.0
       Before this, the window sum read one season type alone: "KNICKS point
       differential over the last 7 games" summed seven regular-season games
       (5-2) where the log listed the postseason's (6-1).
    """
    from association.query.compose.logs import _team_mixed_rows
    from association.query.compose.say import mixed_where
    from association.query.reading import DEFAULT_GAME_LOG_LIMIT

    scope = q.scope
    settled = scoped_team(con, scope, "no team named", span=None, season=scope.season)
    if isinstance(settled, TemplateResult):
        raise Refused(settled)
    team, span = settled
    if span.season is None:
        raise Unsupported("a career span has no single season to read both season types within")
    limit = _clamp_limit(scope.limit, DEFAULT_GAME_LOG_LIMIT)
    mixed = _team_mixed_rows(con, team, span.season, opponent=scope.opponent, venue=scope.venue, limit=limit)
    if isinstance(mixed, TemplateResult):
        raise Refused(mixed)
    rows, counts, narrowed_text = mixed
    scores = [(int(r["team_score"]), int(r["opponent_score"]), r["won"]) for r in rows]
    value: float | None
    if not scores:
        value = None
    elif q.measure == "points":
        value = float(sum(own for own, _, _ in scores))
    elif q.measure == "points_allowed":
        value = float(sum(theirs for _, theirs, _ in scores))
    else:
        value = float(sum(own - theirs for own, theirs, _ in scores))
    window = f" over their last {len(rows)} game{'s' if len(rows) != 1 else ''}{mixed_where(span.season, counts)}" if rows else ""
    return TeamResult(
        team=team,
        span=span,
        measure=q.measure,
        aggregate=q.aggregate,
        value=value,
        games=len(rows),
        wins=sum(1 for _, _, won in scores if won is True),
        losses=sum(1 for _, _, won in scores if won is False),
        narrowed_text=narrowed_text + window,
    )


def _compile_team_games_total(con: duckdb.DuckDBPyConnection, q: TeamQuery) -> TeamResult:
    """The narrowed reader: a sum over the team-games relation's own
    columns - F128 (points) and F129 (differential)'s shape. One season type
    at a time, except a "last N games" question naming none, which sums the
    games the team log lists for it, both types merged by date
    (:func:`_compile_team_games_mixed`).

    .. versionchanged:: 5.0.0
       Reads both season types for ``season_type_unstated``; one type alone
       before, so the sum disagreed with the log's own games.
    """
    if q.measure not in GAME_MEASURES:
        raise Unsupported(f"{q.measure!r} needs a box-score join the team relation does not have yet for a narrowed read")
    if _team_mixed(q.scope):
        if q.scope.since or q.scope.until or q.scope.situation or q.scope.period is not None or q.scope.half is not None:
            # The both-types read is a plain window (an opponent and a venue
            # at most, as the log's is); a range of seasons or a calendar
            # would be dropped from it silently, so it is refused instead.
            raise Unsupported("a window over both season types is read for a plain 'last N games' only")
        return _compile_team_games_mixed(con, q)
    narrowed, team, span = _team_games_narrowed(con, q)
    column = GAME_MEASURES[q.measure]
    selects = [f"SUM({column}) AS total", "COUNT(*) AS games", "SUM(tg.won::INT) AS wins", "SUM((NOT tg.won)::INT) AS losses"]
    sql, params = team_aggregate_sql(narrowed, selects)
    row = con.execute(sql, params).fetchone()
    total, games, wins, losses = row if row else (None, 0, 0, 0)
    return TeamResult(
        team=team,
        span=span,
        measure=q.measure,
        aggregate=q.aggregate,
        value=total,
        games=int(games or 0),
        wins=int(wins or 0),
        losses=int(losses or 0),
        narrowed_text=narrowed.filters(),
    )


def compile_team_run(con: duckdb.DuckDBPyConnection, q: TeamQuery) -> TeamCompiled:
    """The ``run`` shape as SQL: a named team's longest run of wins or
    losses (``scope.kind``) within a season, over the team-games relation
    narrowed exactly as every team template narrows it
    (:func:`_team_games_narrowed`) and read over every game in the span
    (``whole_span``) - or, with no team named, each team-season's own
    longest, the league's list. The runs are
    :func:`~association.query.conditions._longest_runs_sql`'s, partitioned
    by ``(team_id, season)``: a team's run is counted within one season, as
    the record book counts them. The games the runs are read over are kept
    as ``run_rows`` for :func:`compile_team_range`. ``streak``'s retired
    team and league branches (ROADMAP plan item 6, step (g)).

    .. versionadded:: 5.0.0
    """
    scope = q.scope
    team: Entity | None
    if scope.team and scope.team.strip():
        narrowed, team, span = _team_games_narrowed(con, q)
        whole_span(narrowed)
        limit, best = 3, False
    else:
        team = None
        span = span_of(scope.span, scope.season, scope.season_type or 2, "games", since=scope.since, until=scope.until)
        narrowed = _streak_league_team_narrowed(span)
        limit, best = _clamp_limit(scope.limit, DEFAULT_STREAK_LIMIT), True
    base, params = team_named(*team_aggregate_sql(narrowed, list(_TEAM_STREAK_SELECT)))
    sql = _longest_runs_sql(base, ("team_id", "season"), "x.won = $want", best_per_partition=best)
    return TeamCompiled(sql, {**params, "want": scope.kind != "loss", "limit": limit}, team, span, narrowed, run_rows=base, run_params=params)


def compile_team_range(compiled: TeamCompiled) -> TeamCompiled:
    """How many games a ``run`` read is over, and the first and last season
    among them - a postseason's by the calendar year it was played in
    (``year(day)``), never ESPN's pre-1993-94 label, which the span's own
    label has to name. Over the same games as the runs (``run_rows``).

    .. versionadded:: 5.0.0
    """
    assert compiled.run_rows is not None and compiled.run_params is not None
    season = "year(day)" if compiled.span.season_type == 3 else "season"
    sql = f"SELECT COUNT(*) AS games, MIN({season}) AS first_season, MAX({season}) AS last_season FROM ({compiled.run_rows})"
    return TeamCompiled(sql, compiled.run_params, compiled.team, compiled.span, compiled.narrowed)


def _compile_team_run(con: duckdb.DuckDBPyConnection, q: TeamQuery) -> TeamResult:
    """The ``run`` shape read for the compiler's own sentence (a point the
    streak's reader, ``compose.runs.read_team_streak``, does not say):
    :func:`compile_team_run` and :func:`compile_team_range`, executed.

    .. versionadded:: 5.0.0
    """
    compiled = compile_team_run(con, q)
    (found,) = rows_of(con, compile_team_range(compiled))
    games = int(found["games"])
    runs = rows_of(con, compiled) if games else []
    return TeamResult(
        team=compiled.team,
        span=compiled.span,
        measure=q.measure,
        aggregate=q.aggregate,
        value=None,
        games=games,
        narrowed_text=compiled.narrowed.filters(),
        runs=runs,
        first_season=found["first_season"],
        last_season=found["last_season"],
    )


def _compile_team_presence(con: duckdb.DuckDBPyConnection, q: TeamQuery) -> TeamResult:
    """The ``presence`` group: a team's games inside the named teammates'
    time on the team, each marked with how many of them held their
    condition (played, started, came off the bench, reached a line) and
    the subject's line where a player is named - ``with_without``'s retired
    template (ROADMAP plan item 6, step (g)), read through
    :func:`~association.query.templates.splits._with_without_read` over
    the team relation narrowed to the windows' teams and the opponent
    (:func:`~association.query.conditions._with_without_games`). A reading
    that gives the question up - which player was meant, a teammate with no
    box score, a time together outside the span - is the answer, raised as
    :class:`~association.query.compose.core.Refused`.

    .. versionadded:: 5.0.0
    """
    scope = q.scope
    split = _with_without_read(con, scope)
    if isinstance(split, TemplateResult):
        raise Refused(split)
    span = span_of(scope.span, scope.season, scope.season_type or 2, "games")
    return TeamResult(
        team=split.team,
        span=span,
        measure=q.measure,
        aggregate=q.aggregate,
        value=None,
        games=len(split.games),
        wins=sum(1 for g in split.games if g["won"]),
        losses=sum(1 for g in split.games if not g["won"]),
        narrowed_text=f" vs the {split.against.name}" if split.against is not None else "",
        presence=split,
    )


def _compile_team_rows(narrowed: TeamNarrowed, team: Entity | None, span: ResolvedSpan, *, limit: int | None, ascending: bool) -> TeamCompiled:
    """A ``rows`` read: the team's games in Eastern-date order with
    :data:`TEAM_ROW_COLUMNS`, the newest (or oldest) ``limit`` of them."""
    # A rows read applies its own limit, so the relation's window is not
    # what cut these rows - the player compiler's rows rule (core._compile_rows).
    narrowed.window = None
    sql, params = team_rows_sql(narrowed, ", ".join(TEAM_ROW_COLUMNS), order=f"tg.eastern_date {'ASC' if ascending else 'DESC'}", limit=limit, join=_TEAM_ROWS_JOIN)
    return TeamCompiled(sql, params, team, span, narrowed)


def _compile_team_grouped(q: TeamQuery, narrowed: TeamNarrowed, team: Entity | None, span: ResolvedSpan) -> TeamCompiled:
    """A ``grouped`` read: the team's games divided by one of
    :data:`TEAM_GROUPS`, each group's record and per-game line
    (:data:`TEAM_LINE_MEASURES`) - and, beside them, the first and last
    season the group's games came from (a postseason's by the calendar year
    it was played in) and how many of them have no team box score, which
    the line is averaged without. Read over every game in the span, as a
    split is (``whole_span``)."""
    if q.group not in TEAM_GROUPS:
        raise Unsupported(f"no team grouping {q.group!r}")
    whole_span(narrowed)
    season = "year(t.day)" if span.season_type == 3 else "t.season"
    selects = [
        "COUNT(*) AS games",
        "COUNT(*) FILTER (WHERE t.won) AS wins",
        *(f'{sql} AS "{name}"' for name, sql in TEAM_LINE_MEASURES.items()),
        f"MIN({season}) AS first_season",
        f"MAX({season}) AS last_season",
        "COUNT(*) FILTER (WHERE t.fieldGoalsAttempted IS NULL) AS blank",
    ]
    sql, params = team_grouped_sql(narrowed, list(_TEAM_GROUPED_COLUMNS), TEAM_GROUPS[q.group], selects, join=_TEAM_GROUPED_JOIN)
    return TeamCompiled(sql, params, team, span, narrowed)


def compile_team_over(q: TeamQuery, team: Entity | None, span: ResolvedSpan, narrowed: TeamNarrowed, *, limit: int | None = None, ascending: bool = False) -> TeamCompiled:
    """``q`` as SQL over a team already settled - the team counterpart of
    :func:`~association.query.compose.core.compile_over`, for a reader that
    settles the team, the span and the narrowed games through the shared
    steps (:func:`~association.query.templates.common.team_games`) and reads
    them under one shape: ``rows`` (the team's games listed, ``limit`` of
    them, oldest first where ``ascending``) or ``grouped`` by a key of
    :data:`TEAM_GROUPS`. Executed through
    :func:`~association.query.compose.core.rows_of`.

    .. versionadded:: 5.0.0
    """
    if q.shape == "rows":
        return _compile_team_rows(narrowed, team, span, limit=limit, ascending=ascending)
    if q.shape == "grouped":
        return _compile_team_grouped(q, narrowed, team, span)
    raise Unsupported(f"no team compile for the {q.shape!r} shape")


def _team_coverage_tables(q: TeamQuery) -> tuple[str, ...]:
    """Which table a :class:`TeamQuery` would be built from - the season line
    or the game-level relation - the same split :func:`_team_narrowed`
    already makes, restated as table names for
    :func:`association.nba.coverage.unavailable`/:func:`association.nba.coverage.caveat`
    (#197, ISSUES.md).

    .. versionadded:: 4.4.0
    """
    if q.shape == "run":
        # A run of results reads the games' own winner column, and nothing
        # from the season line.
        return ("games",)
    if q.group == "presence":
        # A teammate's presence is read off the box scores (his row, or
        # none), so the split reaches only as far as they do.
        return _PLAYER_GAME_TABLES
    return ("games",) if _team_narrowed(q.scope) else ("team_season_stats",)


def team_coverage_refusal(q: TeamQuery) -> TemplateResult | None:
    """Why this team question's season is out of reach, or ``None`` - the
    team subject's counterpart of
    :func:`~association.query.templates.common.check_coverage` (#197,
    ISSUES.md: compose read no coverage floor at all). Checked before the
    team itself is even resolved, the same order ``check_coverage`` runs in
    ahead of every relation template.

    .. versionadded:: 4.4.0
    """
    # No season named means the current one, which every table covers - the
    # same guard check_coverage applies before charging a floor.
    season = q.scope.season
    if season is None:
        return None
    season_type = q.scope.season_type or 2
    message = unavailable(_team_coverage_tables(q), season, season_type)
    if message is None:
        return None
    return TemplateResult(data={"season": season}, answer=message)


def run_team(con: duckdb.DuckDBPyConnection, q: TeamQuery) -> TeamResult:
    """``q`` answered: the unnarrowed season total, or a narrowed sum over
    the team-games relation - whichever the question's own slots ask for.
    Raises :class:`~association.query.compose.core.Refused` when
    :func:`team_coverage_refusal` finds the season out of reach - checked
    first, the same order ``check_coverage`` runs in ahead of every
    template.

    .. versionadded:: 4.4.0

    .. versionchanged:: 4.4.0
       Checks the coverage floor first, and carries a partial-season caveat
       on the result (#197, ISSUES.md).

    .. versionchanged:: 5.0.0
       Carries no partial-season caveat: the agent appends the same note to
       every composed answer, and it printed twice.

    .. versionchanged:: 5.0.0
       Declines a point whose scope carries a ``threshold`` (ISSUES.md #144),
       rather than silently answering the season or narrowed-window total
       with the threshold dropped. "What was the celtics record when they
       scored 120 points" reaches here with a team, no player and a
       ``threshold`` - a record above and below a line, which neither reader
       this module has (a season sum, or a window sum) can represent - only
       because :func:`~association.query.point.team_read_point`
       settles a team subject before ``record_when``'s own per-intent default
       (``point._default_record_when``, which
       already refuses a team with no player) ever sees the question.
       Declining sends the question back to ``record_when``'s own team
       branch (:func:`~association.query.templates.splits._record_when_team_answer`),
       which answers a threshold record for real.
    """
    if q.shape == "run" or q.group == "presence":
        refusal = team_coverage_refusal(q)
        if refusal is not None:
            raise Refused(refusal)
        try:
            return _compile_team_run(con, q) if q.shape == "run" else _compile_team_presence(con, q)
        except TemplateUnsupported as exc:
            raise Unsupported(f"relation: {exc}") from exc
    if q.scope.threshold is not None:
        raise Unsupported("a threshold names a record above and below a line, not a total - this module has no reader for one")
    if q.shape in ("rows", "grouped"):
        # The team's games listed, or split, are the presenters'
        # (compose.present.present_team); reaching here means a narrowing
        # their words do not state, which no sum here answers either.
        raise Unsupported("a team's log or splits narrowed beyond their own words has no reader")
    refusal = team_coverage_refusal(q)
    if refusal is not None:
        raise Refused(refusal)
    try:
        if _team_narrowed(q.scope):
            result = _compile_team_games_total(con, q)
        else:
            team = _resolved_team_subject(con, q.scope)
            result = _compile_team_season(con, q, team)
    except TemplateUnsupported as exc:
        raise Unsupported(f"relation: {exc}") from exc
    return result
