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
season type (F128/F129's own shape) is answered by :func:`association.query.templates.games.game_log`'s
existing team half directly (its ``_team_game_log_mixed`` already reads both
season types and merges by date) rather than duplicated here; this module's
narrowed reader is one season type at a time.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import duckdb

from association.nba.coverage import unavailable
from association.nba.season import current_season
from association.query.entities import _TEAM_NICKNAMES, Entity
from association.query.reading import Scope
from association.query.team_games import TeamNarrowed
from association.query.team_games import aggregate_sql as team_aggregate_sql
from association.query.templates.common import TemplateResult, TemplateUnsupported, _clamp_limit, _resolved_team, _Span, scoped_team
from association.query.templates.common import team_games as narrow_team_games

from .core import Refused, Unsupported

_WORD = re.compile(r"[a-zA-Z']+")


def team_named_in(con: duckdb.DuckDBPyConnection, question: str) -> str | None:
    """The one team the question itself names, by a whole word of it (or a
    curated nickname) - the team counterpart of
    :func:`association.query.entities.players_named_in`, kept deliberately
    minimal: single words only, since no franchise name has an internal
    ambiguity a span needs to resolve the way a player's first/last name
    does ("Portland Trail Blazers" is found by "blazers" alone; nothing
    named "Trail" collides with it). Never a guess between two candidates -
    only an exact single match counts, and the first match wins, read left
    to right the way a question states its subject first.

    Used to restore a team the router dropped entirely (F127, ISSUES.md:
    "how many 3 pointers have the magic made" routed with no ``team`` slot
    at all) - the same repair :func:`association.query.entities.players_named_in`
    already makes for a dropped player, in :func:`association.query.compose.move.repair`.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       A possessive ("the Sixers' record") names the team as the bare word does.
    """
    for found in _WORD.findall(question.lower()):
        # "the Sixers' record", "the Knicks' last 5 games": the possessive
        # is the question's, not the name's (ISSUES.md #232 - 11 of 277
        # paraphrases read no team at all).
        word = found.removesuffix("'s").rstrip("'")
        if len(word) < 4:
            continue
        nickname = _TEAM_NICKNAMES.get(word)
        if nickname:
            return nickname
        rows = con.execute(
            "SELECT DISTINCT display_name FROM teams WHERE list_contains(regexp_split_to_array(lower(display_name), '[^a-z]+'), ?)",
            [word],
        ).fetchall()
        if len(rows) == 1:
            return str(rows[0][0])
    return None


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
    #: ``"scalar"`` (a sum, this module's own readers) or ``"rows"`` (the
    #: team's games listed - ``game_log``'s team half, said by
    #: :func:`~association.query.compose.present.present_team`).
    shape: str = "scalar"


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

    team: Entity
    span: _Span
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


def _team_narrowed(scope: Scope) -> bool:
    """Whether the question narrows the games at all - the team counterpart
    of ``player_stat``'s own ``_player_stat_reads_box_scores``: an opponent,
    a venue, a date, ``since``/``until``, a game of a series, a calendar
    ``situation``, or a window (``order``/``limit``) all send the read to the
    game-level relation; nothing here means the plain season (or career)
    total.

    .. versionadded:: 4.4.0
    """
    # The fields that narrow the games a team question reads - any of them
    # sends the read to the game-level relation rather than the season line.
    if any((scope.opponent, scope.venue, scope.date, scope.since, scope.until, scope.game_n, scope.situation)):
        return True
    return scope.order is not None or scope.limit is not None


def _resolved_team_subject(con: duckdb.DuckDBPyConnection, scope: Scope) -> Entity:
    """The team a question names, or the refusal wrapped as :class:`~association.query.compose.core.Refused`."""
    team_text = scope.team
    if team_text is None or not team_text.strip():
        raise Unsupported("no team named")
    season: int = scope.season if scope.season is not None else current_season()
    team = _resolved_team(con, team_text, season=season)
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
    return TeamResult(team=team, span=_Span(season, season_type), measure=q.measure, aggregate=q.aggregate, value=value, games=games, from_season_line=True, note=note)


def _team_games_narrowed(con: duckdb.DuckDBPyConnection, q: TeamQuery) -> tuple[TeamNarrowed, Entity, _Span]:
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
    (``templates.games._team_mixed_games``), summed here - so the total is
    over the same games the log shows, and the sentence says how many of
    each type it kept, the default made visible (AGENTS.md, "a reasonable
    default beats a question").

    .. versionadded:: 5.0.0
       Before this, the window sum read one season type alone: "KNICKS point
       differential over the last 7 games" summed seven regular-season games
       (5-2) where the log listed the postseason's (6-1).
    """
    from association.query.templates.games import DEFAULT_GAME_LOG_LIMIT, _game_log_mixed_where, _team_mixed_games

    scope = q.scope
    settled = scoped_team(con, scope, "no team named", span=None, season=scope.season)
    if isinstance(settled, TemplateResult):
        raise Refused(settled)
    team, span = settled
    if span.season is None:
        raise Unsupported("a career span has no single season to read both season types within")
    limit = _clamp_limit(scope.limit, DEFAULT_GAME_LOG_LIMIT)
    mixed = _team_mixed_games(con, team, span.season, opponent=scope.opponent, venue=scope.venue, limit=limit)
    if isinstance(mixed, TemplateResult):
        raise Refused(mixed)
    rows, counts, narrowed_text = mixed
    # _TEAM_GAME_LOG_SELECT's columns: date, side, opponent, team score,
    # opponent score, won, season.
    scores = [(int(r[3]), int(r[4]), r[5]) for r in rows]
    value: float | None
    if not scores:
        value = None
    elif q.measure == "points":
        value = float(sum(own for own, _, _ in scores))
    elif q.measure == "points_allowed":
        value = float(sum(theirs for _, theirs, _ in scores))
    else:
        value = float(sum(own - theirs for own, theirs, _ in scores))
    window = f" over their last {len(rows)} game{'s' if len(rows) != 1 else ''}{_game_log_mixed_where(span.season, counts)}" if rows else ""
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
        if q.scope.since or q.scope.until or q.scope.situation:
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


def _team_coverage_tables(q: TeamQuery) -> tuple[str, ...]:
    """Which table a :class:`TeamQuery` would be built from - the season line
    or the game-level relation - the same split :func:`_team_narrowed`
    already makes, restated as table names for
    :func:`association.nba.coverage.unavailable`/:func:`association.nba.coverage.caveat`
    (#197, ISSUES.md).

    .. versionadded:: 4.4.0
    """
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
       because :func:`~association.query.compose.move.team_read_point`
       settles a team subject before ``record_when``'s own per-intent default
       (:func:`~association.query.compose.adapt._adapt_record_when`, which
       already refuses a team with no player) ever sees the question.
       Declining sends the question back to ``record_when``'s own team
       branch (:func:`~association.query.templates.splits._record_when_team_answer`),
       which answers a threshold record for real.
    """
    if q.scope.threshold is not None:
        raise Unsupported("a threshold names a record above and below a line, not a total - this module has no reader for one")
    if q.shape == "rows":
        # The team's games listed are the log presenter's
        # (compose.present.present_team); reaching here means a narrowing its
        # words do not state, which no sum here answers either.
        raise Unsupported("a team's log narrowed beyond its own words has no reader")
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
