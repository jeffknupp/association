"""Game-level questions: game logs, head-to-head records, a team's or player's quarter or half, and two players' meetings.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal, cast

import duckdb

from association.nba.coverage import POSTSEASON
from association.nba.franchises import season_name, season_name_sql
from association.nba.season import current_season
from association.nba.season import eastern_date as _eastern_date
from association.query.reading import DEFAULT_GAME_LOG_LIMIT as DEFAULT_GAME_LOG_LIMIT
from association.query.reading import ConditionSpec, Reading, Scope, Split

from ..conditions import _PLAYER_GAME_TABLES, _cell, _matchup_line, _meetings, _names, _player_games, _Scope, _table, _totals, _unseen_meetings, box_source
from ..entities import Entity, resolve_team
from ..leaderboard import resolve_metric
from ..metrics import PER_GAME_MIN_GAMES, PER_GAME_MIN_POSTSEASON_GAMES
from ..notes import decided, note
from ..player_games import PERIOD_AGREEMENT, PERIOD_COLUMNS, PERIOD_PLAYS_COLUMNS, REGULATION_QUARTERS, _joined, grouped_sql, rows_sql
from ..shotchart import UNSEPARABLE_SHOT_VALUES
from ..team_games import TEAM_PERIOD_AGREEMENT, TEAM_PERIOD_COLUMNS, TeamNarrowed
from ..team_games import games_subquery as team_games_subquery
from ..team_games import rows_sql as team_rows_sql
from .common import (
    STARTER_SIDES,
    STAT_LABELS,
    MeasureFilter,
    TemplateContext,
    TemplateResult,
    TemplateUnsupported,
    _clamp_limit,
    _condition_scope,
    _Narrowed,
    _no_games,
    _period,
    _player_relation_season_type,
    _relation_window,
    _resolved_team,
    _slot_season,
    _Span,
    _span_of,
    _validated_until,
    _where_in,
    league_games,
    measure_filters,
    period_narrowing,
    scoped_games,
    scoped_team,
    team_games,
    whole_span,
)


def _played_for(con: duckdb.DuckDBPyConnection, player: Entity, team: Entity) -> bool:
    """Whether ``player`` has ever suited up for ``team``, anywhere in
    ``player_game_log``.

    The only question this answers is "is this team the SUBJECT, not the
    opponent" - so it deliberately looks across his whole career rather than
    the season in scope: a team slot naming a season he was not on it is still
    not an opponent, and treating it as one would file a real former team as
    though the two had played each other.
    """
    row = con.execute("SELECT 1 FROM player_game_log WHERE athlete_id = ? AND team_id = ? LIMIT 1", [player.id, team.id]).fetchone()
    return row is not None


def _team_slot_for_player(con: duckdb.DuckDBPyConnection, player: Entity, team_text: str, *, season: int | None, opponent: Any) -> Any:
    """What a ``team`` slot means once ``player`` is named - see #147.

    An ``opponent`` already named wins outright: a ``team`` slot beside it is
    the same noise the router routinely fills alongside an already-correct
    opponent, not a second fact to reconcile - measured on the filed corpus
    rows, it is Payton Pritchard's invented "Phoenix Suns" beside a correct
    "Philadelphia 76ers" opponent, and Kobe Bryant's own "Los Angeles Lakers"
    beside a correct "Houston Rockets" one. Comparing the two and refusing
    when they disagreed was tried first and was wrong for exactly this shape:
    "Phoenix Suns" is a real, resolvable team, so a naive conflict check
    refused Pritchard's question rather than answering it.

    With no ``opponent`` already named, his own team narrows nothing, so it is
    dropped; a different, real team is his opponent (the Curry shape); and a
    name nothing resolves to - the router inventing a team the way it
    sometimes invents a player, see AGENTS.md's "the router invents names" -
    or an ambiguous one, is dropped rather than guessed at or asked about: the
    player, not the team, is what the question is about.
    """
    if isinstance(opponent, str) and opponent.strip():
        return opponent
    match resolve_team(con, team_text, season):
        case Entity() as team:
            return opponent if _played_for(con, player, team) else team_text
        case _:
            # NotFound or Ambiguous - dropped either way, since `opponent` is
            # not set here for either to fill.
            return opponent


def _head_to_head_names(teams_slot: tuple[str, ...], team_slot: str | None, opponent_slot: str | None) -> list[str]:
    """The team names asked for, merged from `teams`, `team` and `opponent`.

    A city name rather than a nickname ("...play Boston?") makes the router
    split the two teams across `team` and `teams` instead of putting both in
    `teams`. Name resolution is fine either way, so treating `team` as a third
    candidate absorbs the split rather than rejecting an answerable question.
    The router also writes the other side as `opponent` ("Celtics vs Bulls
    head to head" arrives as team + opponent): it is one of the two teams.
    """
    names = [n for n in teams_slot if n.strip()]
    if team_slot is not None and team_slot.strip() and team_slot not in names:
        names = [team_slot, *names]
    if opponent_slot is not None and opponent_slot.strip() and opponent_slot not in names:
        names = [*names, opponent_slot]
    if len(set(names)) < 2:
        raise TemplateUnsupported("head_to_head needs two team names")
    return names


def _head_to_head_teams(con: duckdb.DuckDBPyConnection, names: list[str], season: int | None) -> tuple[Entity, Entity] | TemplateResult:
    """Until two DIFFERENT teams resolve, not the first two names: "Celtics" in
    `team` and "Boston Celtics" in `teams` are one team, and the opponent
    after them is the second."""
    resolved: list[Entity] = []
    for name in names:
        team = _resolved_team(con, name, season=season)
        if isinstance(team, TemplateResult):
            return team
        if team.id not in {t.id for t in resolved}:
            resolved.append(team)
        if len(resolved) == 2:
            break
    if len(resolved) != 2:
        raise TemplateUnsupported("the named teams resolved to the same team")
    return resolved[0], resolved[1]


def _head_to_head_narrowed(
    con: duckdb.DuckDBPyConnection, a: Entity, b: Entity, venue: Literal["home", "away"] | None, date: str | None, season_slot: int | None, season_type: int
) -> tuple[TeamNarrowed | TemplateResult, int | None]:
    """``a``'s games against ``b``, from ``a``'s own row of the team relation
    (:func:`association.query.templates.common.team_games`) - which already
    carries both home and away meetings without a venue named, since ``games``
    is home/away-oriented rather than team-perspective and ``a``'s own row
    always exists whichever side it played - and the season the narrowing
    settled on (``None`` once ``date`` has replaced it).

    A date names its game outright, the same way it does in :func:`game_log`:
    the router's season is usually its "current" default, and a date from a
    past season looked for inside this one finds nothing - so with a date
    given, the span is UNBOUNDED (``_Span(None, season_type)``, whose
    ``first`` defaults to 0) rather than "his career": a specific date needs
    no floor at all, the same as the hand-written scope this replaces applied
    none. A venue is always read from ``a``'s side - the team named first,
    the same team ``_head_to_head_names`` puts first for "Celtics vs Bulls" -
    so "Lakers vs Mavs ... home games" narrows to the Lakers' home games, not
    the Mavericks'.
    """
    if date:
        span = _Span(None, season_type)
        return team_games(con, a, span, Scope(venue=venue), opponent=b, date=date), None
    # No season named means the CURRENT one, as everywhere else. "All time" is
    # a defensible reading here, but silently answering a different span than
    # the rest of the system is the substitution this design exists to prevent.
    # The answer names the season, so another one is a follow-up away.
    season = season_slot or current_season()
    span = _Span(season, season_type)
    return team_games(con, a, span, Scope(venue=venue), opponent=b), season


def _head_to_head_span_slots(scope: Scope, date: str | None) -> tuple[int | None, int | None, bool]:
    """``head_to_head``'s ``since``/``until``/``span`` reading and the
    conflicts that are its own (a date and a since-bounded or career span at
    once; ``since`` and ``career`` together) - pulled out of ``head_to_head``
    itself so that function stays inside the complexity gate, the same move
    ``_team_record_month_and_split`` already makes for ``team_record``.

    .. versionadded:: 4.4.0
    """
    since = scope.since or None
    until = _validated_until(scope.until, since)
    career = scope.span == "career"
    if (since is not None or career or until is not None) and date:
        raise TemplateUnsupported("a date and a since-bounded or career span of meetings at once")
    if since is not None and career:
        raise TemplateUnsupported(f"since {since} and a career span of meetings at once")
    # `until` needs no separate career check: `_validated_until` above already
    # raises for `until` with no `since`, and `career` never carries a `since`.
    return since, until, career


def head_to_head(ctx: TemplateContext, reading: Reading) -> TemplateResult:
    """ "How many times did the 76ers play Boston?" - games between two teams.

    That question was answered "they did not play" (they played four times).
    The agent wrote `home_team_id = 'PHI'` against an opaque numeric VARCHAR
    ('20'), so the filter matched nothing - with the rule against it, and a
    worked WRONG example, in its prompt. Prompting cannot fix that; resolving
    names to ids in code can.

    .. versionchanged:: 4.3.0
       Honors ``venue`` (narrowed to the first-named team's home or road
       games) and ``date`` (one calendar day, replacing the season the same
       way it does in :func:`game_log`) instead of refusing them.

    .. versionchanged:: 4.4.0
       Reads :mod:`association.query.team_games`'s relation
       (:func:`association.query.templates.common.team_games`) instead of its
       own hand-written scope over ``real_games`` - a pure port, the NBA Cup
       final counted as a meeting either way, since a head-to-head count is
       not the win-loss RECORD that excludes it. Also honors ``since`` (every
       meeting since a season) and ``span`` "career" (every meeting on
       record) - step 3, team cells: the same since-bounded or whole-career
       ``_Span`` :func:`association.query.templates.teams.team_record` reads,
       over every meeting in it rather than one season. Neither combines with
       ``date``, which already names one game outright.

    .. versionchanged:: 4.4.0
       Honors ``until`` beside ``since`` (step 3, K1): "meetings from 2011 to
       2019" reads a bounded range rather than an open-ended "since 2011".
    """
    scope = reading.scope
    con = ctx.con
    # The router also writes the other side as `opponent` ("Celtics vs Bulls
    # head to head" arrives as team + opponent): it is one of the two teams.
    names = _head_to_head_names(scope.teams, scope.team, scope.opponent)

    teams = _head_to_head_teams(con, names, _slot_season(scope))
    if isinstance(teams, TemplateResult):
        return teams
    a, b = teams
    season_type = scope.season_type or 2
    date = scope.date
    # Home or away and nothing else: the Scope's own type (it was checked
    # here, against the slot dict, before the Reading was typed).
    venue = scope.venue
    since, until, career = _head_to_head_span_slots(scope, date)
    if since is not None or career or until is not None:
        return _head_to_head_span_result(con, a, b, venue, season_type, since=since, until=until, career=career)

    narrowed, season = _head_to_head_narrowed(con, a, b, venue, date, scope.season, season_type)
    if isinstance(narrowed, TemplateResult):
        return narrowed
    sql, params = team_rows_sql(narrowed, "tg.won", order="tg.eastern_date")
    rows = con.execute(sql, params).fetchall()

    a_wins = sum(1 for (won,) in rows if won)
    b_wins = sum(1 for (won,) in rows if won is False)
    if venue or date:
        answer = _head_to_head_narrowed_phrase(a.name, b.name, len(rows), a_wins, b_wins, venue=venue, date=date, season=season, season_type=season_type)
    else:
        # `_head_to_head_scope` only returns `season=None` once `date` has
        # replaced it, and the branch above already took that case, so this
        # one always has a season - `cast` says that to the type checker.
        answer = _phrase_head_to_head(a.name, b.name, len(rows), a_wins, b_wins, _period(cast(int, season), season_type))
    data = {"teams": [a.name, b.name], "games": len(rows), "wins": {a.name: a_wins, b.name: b_wins}, "venue": venue, "date": date, "headline": answer}
    return TemplateResult(data=data, answer=answer)


def _head_to_head_span_result(
    con: duckdb.DuckDBPyConnection, a: Entity, b: Entity, venue: Literal["home", "away"] | None, season_type: int, *, since: int | None, until: int | None = None, career: bool
) -> TemplateResult:
    """``head_to_head``'s answer for a since-bounded or whole-career span
    (step 3, team cells): every meeting between ``a`` and ``b`` in the span,
    tallied once - read through :func:`association.query.templates.common.team_games`
    the same way the single-season path is, over the same since-bounded or
    whole-career ``_Span`` :func:`association.query.templates.teams._record_narrowed`
    reads for ``team_record``. The per-game season is read alongside the
    result (``tg.season``, or ``year(tg.eastern_date)`` for a postseason,
    where the label is not the year it was played in before 1994) so the
    sentence can say which seasons the games actually came from, not only
    what the question asked for.

    .. versionadded:: 4.4.0

    .. versionchanged:: 4.4.0
       Honors ``until`` beside ``since`` (step 3, K1).
    """
    span = _span_of("career" if career else None, None, season_type, "games", since=since, until=until)
    narrowed = team_games(con, a, span, Scope(venue=venue), opponent=b)
    if isinstance(narrowed, TemplateResult):
        return narrowed
    select = f"tg.won, {'year(tg.eastern_date)' if season_type == 3 else 'tg.season'}"
    sql, params = team_rows_sql(narrowed, select, order="tg.eastern_date")
    rows = con.execute(sql, params).fetchall()
    a_wins = sum(1 for won, _ in rows if won)
    b_wins = sum(1 for won, _ in rows if won is False)
    years = [int(yr) for _, yr in rows]
    first, last = (min(years), max(years)) if years else (None, None)
    if venue:
        answer = _head_to_head_narrowed_phrase(a.name, b.name, len(rows), a_wins, b_wins, venue=venue, date=None, season=None, season_type=season_type, since=since, until=until, career=career)
    else:
        answer = _head_to_head_span_phrase(a.name, b.name, len(rows), a_wins, b_wins, span, first, last)
    data = {
        "teams": [a.name, b.name],
        "games": len(rows),
        "wins": {a.name: a_wins, b.name: b_wins},
        "venue": venue,
        "date": None,
        "since": since,
        "until": until,
        "span": "career" if career else None,
        "headline": answer,
    }
    return TemplateResult(data=data, answer=answer)


def _phrase_head_to_head(a: str, b: str, games: int, a_wins: int, b_wins: int, period: str) -> str:
    if games == 0:
        return f"The warehouse has no {period} games between the {a} and the {b}."
    times = "once" if games == 1 else f"{games} times"
    lead = f"The {a} and the {b} met {times} in the {period}"
    if a_wins == b_wins:
        return f"{lead}, splitting them {a_wins}-{b_wins}."
    leader, trailing = (a, f"{a_wins}-{b_wins}") if a_wins > b_wins else (b, f"{b_wins}-{a_wins}")
    return f"{lead}; the {leader} won the series {trailing}."


def _head_to_head_span_phrase(a: str, b: str, games: int, a_wins: int, b_wins: int, span: _Span, first: int | None, last: int | None) -> str:
    """The head-to-head sentence for a since-bounded or whole-career span
    (step 3, team cells) - every meeting in the span, tallied once, the
    counterpart of :func:`_phrase_head_to_head` for one season. ``first``/
    ``last`` are the actual seasons the games came from (``None`` when there
    are none), read off the rows themselves rather than the span's own
    (possibly coverage-clamped) floor, so the parenthetical always says what
    was actually found. Uses "lead the series" rather than "won the series"
    for an unfinished, ongoing span - the single-season phrase's wording
    reads as a settled result, which a since-bounded or career tally is not.

    .. versionadded:: 4.4.0

    .. versionchanged:: 4.4.0
       Says "from 2011 through 2019" rather than "since 2011" once ``until``
       bounds the span (step 3, K1), via ``span``'s own ``during()``/``since``
       wording.
    """
    if games == 0:
        if span.since is not None and span.until is not None:
            when = f"from {span.since} through {span.until}"
        elif span.since is not None:
            when = f"since {span.since}"
        else:
            when = "on record"
        return f"The {a} and the {b} have not played each other {when}."
    when = span.during(first, last, whose="the seasons on record")
    times = "once" if games == 1 else f"{games} times"
    lead = f"The {a} and the {b} have met {times} {when}"
    if a_wins == b_wins:
        return f"{lead}, splitting them {a_wins}-{b_wins}."
    leader, trailing = (a, f"{a_wins}-{b_wins}") if a_wins > b_wins else (b, f"{b_wins}-{a_wins}")
    return f"{lead}; the {leader} lead the all-time series {trailing}."


def _head_to_head_narrowed_phrase(
    a: str,
    b: str,
    games: int,
    a_wins: int,
    b_wins: int,
    *,
    venue: str | None,
    date: str | None,
    season: int | None,
    season_type: int,
    since: int | None = None,
    until: int | None = None,
    career: bool = False,
) -> str:
    """The head-to-head sentence once ``venue`` or ``date`` has narrowed the
    games, phrased to say what was actually counted rather than reusing the
    plain season sentence (:func:`_phrase_head_to_head`, left untouched) with
    a different number silently attached to it.

    .. versionchanged:: 4.4.0
       Takes ``since``/``career`` too (step 3, team cells), for a venue
       narrowed to a since-bounded or whole-career span rather than one
       season - unused (both default to the values that reproduce the old
       phrase exactly) by every caller that predates them.

    .. versionchanged:: 4.4.0
       Takes ``until`` beside ``since`` (step 3, K1).
    """
    if date:
        where = f"on {date}"
    else:
        # "Lakers'", not "Lakers's" - most team names end in "s" (Celtics,
        # Warriors, Nets...), and templates/teams.py's own `_possessive` makes
        # the same call inline rather than being imported cross-module.
        possessive = f"{a}'" if a.endswith("s") else f"{a}'s"
        if since is not None and until is not None:
            scope = f"from {since} through {until}"
        elif since is not None:
            scope = f"since {since}"
        elif career:
            scope = "on record"
        else:
            scope = f"of the {_period(season or current_season(), season_type)}"
        where = f"in the {possessive} {'home' if venue == 'home' else 'road'} games {scope}"
    if games == 0:
        return f"The warehouse has no games between the {a} and the {b} {where}."
    times = "once" if games == 1 else f"{games} times"
    lead = f"The {a} and the {b} met {times} {where}"
    if a_wins == b_wins:
        return f"{lead}, splitting them {a_wins}-{b_wins}."
    leader, trailing = (a, f"{a_wins}-{b_wins}") if a_wins > b_wins else (b, f"{b_wins}-{a_wins}")
    return f"{lead}; the {leader} won the series {trailing}."


# Above this many games, a full per-game breakdown is unreadable rather than
# informative - it only fires when no opponent narrows the season down (a
# real head-to-head, regular season or playoffs, is never more than ~7 games).
_QUARTER_BREAKDOWN_LIMIT = 12


def team_quarter_points(ctx: TemplateContext, reading: Reading) -> TemplateResult:
    """A team's total points in ONE quarter/period, narrowed to an opponent,
    a venue, one Eastern date, one game of each playoff series, a window of
    its newest or oldest N games, or a career that starts partway through -
    the full team-games relation.

    A PLAYER's quarter or half is answered by :func:`period_split`, not here -
    see its docstring for why the derivation this one used to say a player's
    score would need (a plays-table ``LAG()``) turned out to be unnecessary.
    This template stays TEAM-only: home_linescores/away_linescores already
    store the official per-period score, so this is a lookup rather than a
    derivation, and a named player still raises below.

    Worth a template because the agent spent ~150s over 3 calls getting it
    wrong: a games.period column that doesn't exist, then home_team_id compared
    to 'PHI' - the id-vs-abbreviation mistake its own always-on rule warns
    against, on every call. A compound shape (quarter math AND a named
    opponent) is what this model fails at even with both rules in its
    prompt.

    .. versionadded:: 1.1.0

    .. versionchanged:: 4.4.0
       Reads its games from :mod:`association.query.team_games` (step 3, C4b)
       through :func:`common.scoped_team`/:func:`common.team_games`, instead of
       a hand-written join over ``team_box_stats``: "show sixers first quarter
       scoring for their last 10 games" and "trailblazers stats last 10 games
       3 point average 1st quarter" (ISSUES.md) both refused for want of
       ``order``/``limit``, which the relation now supplies as a window cut
       before the linescores are summed. ``venue``, ``date``, ``since``,
       ``span`` and ``game_n`` are honored the same way, for free, and the
       answer says which of them narrowed the games it counted. A side effect:
       this no longer depends on ``team_box_stats`` at all (only on
       ``real_games``' own linescores), so a season with a real game list but
       an empty team box score - the empty 2013-2018 Chicago and New Orleans
       seasons, AGENTS.md's "Whole team-seasons of box scores are empty" - is
       no longer silently invisible to it; not measured further here.

    .. versionchanged:: 4.4.0
       Honors ``until`` beside ``since`` and ``situation`` (step 3, K1: a
       weekday, a month, a fixed holiday, or "since <month day>") - the same
       two cells step 3, K1 adds to :data:`common.TEAM_RELATION_SCOPING`,
       reached the same "for free" way through :func:`common.team_games`.

    .. versionchanged:: 5.0.0
       Answers any stat a team's period line holds, not only points (the
       period relation's team half, ISSUES.md #161): the quarter or half is a
       narrowing of the team relation (``TeamNarrowed.narrow_periods``), whose
       points are still the linescore's and whose other columns are rebuilt
       from the plays (:func:`association.query.team_games.team_period_line_sql`),
       each season's figure caveated or refused by
       :data:`association.query.team_games.TEAM_PERIOD_AGREEMENT`. A stat
       nothing splits by period (minutes, plus-minus, points in the paint) is
       refused, naming it.
    """
    scope = reading.scope
    con = ctx.con
    periods, period_label = _period_scope(scope, "team_quarter_points")
    if scope.player is not None and scope.player.strip():
        # A named player's quarter or half is period_split's job, not this
        # template's - see the docstring.
        raise TemplateUnsupported("team_quarter_points cannot answer for a named player")
    measure = _team_quarter_points_measure(scope.stat)
    if isinstance(measure, TemplateResult):
        return measure

    date = scope.date
    settled = _team_quarter_points_team_and_span(con, scope, date)
    if isinstance(settled, TemplateResult):
        return settled
    team, span = settled
    # Named explicitly (rather than handing the scope on whole) so every cell
    # this template honors is visible in its own body, not only inside the
    # shared step - `venue`, `game_n`, `situation` (step 3, K1) and the
    # `order`/`limit` window all narrow through `team_games` itself, which
    # reads them off exactly these fields (see its own docstring).
    # The quarter or half is the relation's narrowing too (`period`/`half`):
    # every read of `narrowed` sees that part of each game.
    narrowing = Scope(venue=scope.venue, game_n=scope.game_n, situation=scope.situation, order=scope.order, limit=scope.limit, period=scope.period, half=scope.half)
    narrowed = team_games(con, team, span, narrowing, opponent=scope.opponent, date=date)
    if isinstance(narrowed, TemplateResult):
        return narrowed

    games, seasons = _team_quarter_points_games(con, narrowed, measure)
    first, last = (min(seasons), max(seasons)) if seasons else (None, None)
    period_str = _team_quarter_points_period_str(span, bool(narrowed.date), first, last)
    read = _team_quarter_points_rebuilt(team, games, seasons, measure, period_label)
    if isinstance(read, TemplateResult):
        return read
    games, note = read
    result = _team_quarter_points_answer(
        team,
        narrowed.opponent,
        games,
        periods=periods,
        period_label=period_label,
        period_str=period_str,
        rank=scope.rank,
        extra=narrowed.filters(opponent=False, period=False),
        dateless_extra=narrowed.filters(opponent=False, date=False, period=False),
        measure=measure,
    )
    return _team_quarter_points_noted(result, note)


def _team_quarter_points_team_and_span(con: duckdb.DuckDBPyConnection, scope: Scope, date: str | None) -> tuple[Entity, _Span] | TemplateResult:
    """The team and the seasons its games come from - through
    :func:`common.scoped_team`, the same order every other team template
    settles them in, unless a ``date`` already names one game outright: the
    router's season is usually its "current" default, and a date from a past
    season looked for inside this one finds nothing, so a named date reads
    every season on record instead - the same reasoning ``game_log`` and
    ``head_to_head`` apply to a dated question. ``since``/``until`` (step 3,
    K1) are named explicitly, for the same reason ``narrowing`` is built by
    hand in the caller."""
    if date:
        team = _resolved_team(con, scope.team, season=_slot_season(scope))
        if isinstance(team, TemplateResult):
            return team
        return team, _Span(None, scope.season_type or 2)
    scoped = Scope(team=scope.team, season=scope.season, season_type=scope.season_type, since=scope.since, until=scope.until)
    return scoped_team(con, scoped, "team_quarter_points needs a team", span=scope.span, season=scope.season)


def _team_quarter_points_measure(stat: Any) -> str | TemplateResult:
    """The column a team's period question measures - points where it names
    none (the linescore's), else any column the team's period line rebuilds
    (:data:`~association.query.team_games.TEAM_PERIOD_COLUMNS`) - or the
    refusal, naming the stat, for one nothing splits by period.

    Answering a stat it cannot read with the team's POINTS was the fluent
    wrong answer this used to guard against by falling through ("trailblazers
    stats last 10 games 3 point average 1st quarter"); now the threes are on
    the line, a shooting percentage is a ratio of two of its columns
    (:data:`PERIOD_RATE_STATS`), and what is left - minutes, plus-minus,
    points in the paint - is refused here rather than handed to an agent
    that has no per-quarter source for it either.

    .. versionchanged:: 5.0.0
       Reads a field goal, 3-point or free throw percentage.
    """
    if stat is None or not str(stat).strip() or stat == "all" or resolve_metric(stat) in ("avg_points", "total_points"):
        return "points"
    if stat in TEAM_PERIOD_COLUMNS:
        return str(stat)
    if stat in PERIOD_RATE_STATS:
        return PERIOD_RATE_STATS[stat]
    held = _joined(["points", *(_period_split_noun(c, 2) for c in TEAM_PERIOD_COLUMNS if c != "points"), "the field goal, 3-point and free throw percentages from them"])
    label = f"{STAT_LABELS[stat]}s" if stat in STAT_LABELS else str(stat)
    message = f"A team's {label} by quarter is not on record: ESPN's linescore holds only the score, and play-by-play rebuilds only {held} - not {stat}."
    return TemplateResult(data={"stat": stat, "message": message}, answer=message)


def _team_quarter_points_rebuilt(team: Entity, games: list[dict[str, Any]], seasons: list[int], measure: str, period_label: str) -> tuple[list[dict[str, Any]], str] | TemplateResult:
    """For a column rebuilt from the plays: the games that can be read and a
    note on the ones that cannot, or the refusal.

    A game with no play-by-play (before 2002, or one ESPN serves none for)
    holds NULL, never a zero: it is left out and counted in the note, and
    when no game has any the question is refused on that cause. A season
    whose team-games rebuild this column right under 90% of the time
    (:data:`~association.query.team_games.TEAM_PERIOD_AGREEMENT`) refuses the
    whole question, naming the season - the same rule a player's period line
    keeps. Points are the linescore's, and pass through untouched.
    """
    if measure == "points":
        return games, ""
    columns = _period_split_columns(measure)
    reached = [(g, season) for g, season in zip(games, seasons, strict=True) if g["points"] is not None]
    known = [(g, season) for g, season in reached if all(g[column] is not None for column in columns)]
    # A rate is as weak as the weaker of the two columns it divides.
    weak = {season: _team_quarter_points_weakest(columns, season) for _, season in known}
    refusal = _team_quarter_points_rebuilt_refusal(team, measure, period_label, weak, unread=bool(reached) and not known)
    if refusal is not None:
        return refusal
    unreached = [g for g in games if g["points"] is None]
    return [g for g, _ in known] + unreached, _team_quarter_points_rebuilt_note(measure, weak, missing=len(reached) - len(known), reached=len(reached))


def _team_quarter_points_weakest(columns: tuple[str, ...], season: int) -> float | None:
    """The lowest measured agreement among ``columns`` in ``season``
    (:data:`~association.query.team_games.TEAM_PERIOD_AGREEMENT`), or None
    where none is listed - a season under 99% for any column a rate divides
    is what its caveat or refusal reports."""
    listed = [TEAM_PERIOD_AGREEMENT[column][season] for column in columns if season in TEAM_PERIOD_AGREEMENT.get(column, {})]
    return min(listed) if listed else None


def _team_quarter_points_rebuilt_refusal(team: Entity, measure: str, period_label: str, weak: dict[int, float | None], *, unread: bool) -> TemplateResult | None:
    """Why a rebuilt column cannot be answered at all - no game with any
    play-by-play, or a season it rebuilds right under 90% of the time - or
    None."""
    noun = _period_split_noun(measure, 2)
    columns_noun = _period_split_columns_noun(measure)
    if unread:
        message = f"No play-by-play is on record for these {team.name} games, and a team's {columns_noun} by quarter are rebuilt from it (it starts in 2002)."
        return TemplateResult(data={"team": team.name, "message": message}, answer=message)
    refused = sorted(season for season, pct in weak.items() if pct is not None and pct < PERIOD_REFUSE_BELOW)
    if not refused:
        return None
    said = ", ".join(f"{season} ({weak[season]:.0f}%)" for season in refused)
    message = f"A team's {period_label} {noun} cannot be answered for {said}: rebuilt from play-by-play, a team-game's {columns_noun} match its box score that seldom."
    return TemplateResult(data={"team": team.name, "seasons": refused, "message": message}, answer=message)


def _team_quarter_points_rebuilt_note(measure: str, weak: dict[int, float | None], *, missing: int, reached: int) -> str:
    """The notes a rebuilt column's answer carries: the games with no
    play-by-play left out, and each listed season's measured agreement."""
    noun = _period_split_columns_noun(measure)
    notes = [note("games_unseen", f"{missing} of the {reached} games have no play-by-play and are not counted.", games=missing, of=reached, why="no_play_by_play")] if missing else []
    listed = sorted((season, pct) for season, pct in weak.items() if pct is not None)
    if listed:
        said = ", ".join(f"{pct:.1f}% of the time in {season}" for season, pct in listed)
        sentence = f"Rebuilt from play-by-play rather than an official per-quarter box score: a team-game's {noun} rebuilt this way match its box score {said} - treat a single game as approximate."
        seasons, pcts = [season for season, _ in listed], [pct for _, pct in listed]
        notes.append(note("rebuilt_agreement", sentence, seasons=seasons, pct=pcts, columns=list(_period_split_columns(measure)), stat=measure, what="team_period_rebuilt"))
    return " ".join(f"({n})" for n in notes)


def _team_quarter_points_noted(result: TemplateResult, note: str) -> TemplateResult:
    """``result`` with ``note`` (the rebuilt column's caveat) on its own line."""
    if not note or not result.answer:
        return result
    data = dict(result.data)
    data["notes"] = [*data.get("notes", []), note]
    return TemplateResult(data=data, answer=f"{result.answer}\n  {note}")


def _team_quarter_points_games(con: duckdb.DuckDBPyConnection, narrowed: TeamNarrowed, measure: str = "points") -> tuple[list[dict[str, Any]], list[int]]:
    """Each qualifying game's date, opponent and points in the asked-for
    periods - None where the game never reached any of them, not zero - and
    ``measure`` beside them when it is another column, all read from the
    period-narrowed relation's own rows (``TeamNarrowed.narrow_periods``):
    its ``points`` are the linescore's, each side's own, and a half is the two
    quarters it holds summed from it, so "most points in a first half" is
    answered by addition rather than by a second source.

    Also returns each game's season, the way
    :func:`~association.query.templates.splits._team_season_range` reads it
    for the condition templates' team branches - a postseason by the calendar
    year it was played in, never the label - so a career or since-bounded
    answer can say the real span its games came from, and a rebuilt column
    can be checked season by season.
    """
    # Every alias here is one `_TEAM_GAMES` does not already use anywhere
    # inside itself (`tg`, `g`) - `base` is a COMPLETE statement
    # (`team_games_subquery` embeds `_TEAM_GAMES`'s own `WITH` clause, whose
    # `listed` CTE reads `FROM real_games g` and whose last CTE's own SELECT
    # reads `FROM team_games tg`), and DuckDB does not keep an alias reused at
    # an outer level of the same statement apart from an inner one already
    # live several lines inside a nested `WITH` - reusing either name (tried
    # both) resolved the CTE's OWN `g.neutral_site` against this query's outer
    # join instead of its own local `real_games g`, and raised a
    # BinderException nowhere near any line this function wrote. Every other
    # reader of a `base` this shape wraps it under a name the base does not
    # already use internally (`_record_when_team_base` uses `t`, not `tg`).
    if narrowed.periods is None or (measure not in TEAM_PERIOD_COLUMNS and measure not in PERIOD_RATES):  # both set by the caller, in code
        raise ValueError(f"a period-narrowed read of a period column, got periods={narrowed.periods!r} measure={measure!r}")
    base, params = team_games_subquery(narrowed)
    columns = _period_split_columns(measure)
    sql = f"""
        SELECT qp.eastern_date, qp.points, {", ".join(f"qp.{column}" for column in columns)},
               {season_name_sql("qp.opponent_id", "qp.season", "ot.display_name")} AS opponent,
               CASE WHEN qp.season_type = 3 THEN year(qp.eastern_date) ELSE qp.season END AS season_year
        FROM ({base}) qp
        JOIN teams ot ON ot.team_id = qp.opponent_id
        ORDER BY qp.eastern_date
    """
    rows = con.execute(sql, params).fetchall()
    games: list[dict[str, Any]] = []
    seasons: list[int] = []
    for date, points, *values, opp_name, season_year in rows:
        game = {"date": str(date), "opponent": opp_name, "points": None if points is None else int(points)}
        for column, value in zip(columns, values, strict=True):
            if column != "points":
                game[column] = None if value is None else int(value)
        if measure in PERIOD_RATES:
            # The game's own percentage, None where nothing was attempted
            # (or nothing was rebuilt - the caller reads the columns).
            game[measure] = _period_split_rate([game], measure)[2] if all(game[column] is not None for column in columns) else None
        games.append(game)
        seasons.append(int(season_year))
    return games, seasons


def _team_quarter_points_period_str(span: _Span, dated: bool, first: Any, last: Any) -> str:
    """The span as a bare noun phrase - "2026 regular season", "since 2022
    (2022-2026 regular seasons)", "from 2022 through 2024 (2022-2024 regular
    seasons)" (step 3, K1's ``until``) or "their career (1994-2026 regular
    seasons)" - the way it always followed a name or a game count in this
    template's answers, now extended past the one season it used to be.
    Empty for a dated question: one Eastern date already says which game, and
    a bare span here would say "(their career (...))" beside a date that has
    already narrowed it to one.

    .. versionchanged:: 4.4.0
       Says "from X through Y" once ``until`` bounds the span (step 3, K1),
       via ``span.during()`` rather than its own hand-written "since" phrase.
    """
    if dated:
        return ""
    if span.season is not None:
        return _period(span.season, span.season_type)
    years = span.years(first, last) if isinstance(first, int) and isinstance(last, int) else f"{span.kind}s"
    if span.since is not None:
        bound = f"from {span.since} through {span.until}" if span.until is not None else f"since {span.since}"
        return f"{bound} ({years})"
    return f"their career ({years})"


def _team_quarter_points_answer(
    team: Entity,
    opponent: Entity | None,
    games: list[dict[str, Any]],
    *,
    periods: tuple[int, ...],
    period_label: str,
    period_str: str,
    rank: Any = None,
    extra: str = "",
    dateless_extra: str | None = None,
    measure: str = "points",
) -> TemplateResult:
    """The no-games refusal, the none-reached-that-period refusal, the single
    game a "most/least" question asks for, or the normal per-game breakdown
    and total - of ``measure``, points unless the question named another
    column of the period's line.

    ``extra`` is any narrowing beyond ``opponent``/``period_str`` the caller
    already has as a phrase - a venue, a date, one game of a series, or a
    window of recent games (:meth:`~association.query.team_games.TeamNarrowed.filters`,
    opponent left out since this function already names it its own way) -
    defaulted to nothing so a caller that resolved only a team and an
    opponent, as this always could, need not pass it. ``dateless_extra`` is
    the same phrase with a narrowed DATE left out too, for the one shape here
    that already prints a game's date its own way (a single game, or the
    tied-extreme sentence, both of which read a date off the game itself) -
    defaulted to ``extra`` so a caller with no date to duplicate need not pass
    it either.

    .. versionchanged:: 4.4.0
       Takes ``extra`` and ``dateless_extra`` (step 3, C4b).

    .. versionchanged:: 5.0.0
       Takes ``measure`` - a column of the period's line, or a shooting
       percentage over two of them (:data:`PERIOD_RATES`), said through
       :func:`_team_quarter_points_rate_answer`.
    """
    if dateless_extra is None:
        dateless_extra = extra
    vs = f" against the {opponent.name}" if opponent else ""
    opponent_name = opponent.name if opponent else None
    if not games:
        answer = f"The warehouse has no {period_str} games for the {team.name}{vs}{extra}."
        return TemplateResult(data={"team": team.name, "opponent": opponent_name, "games": [], "headline": answer}, answer=answer)

    played = [g for g in games if g["points"] is not None]
    if not played:
        plural = "game" if len(games) == 1 else "games"
        answer = f"None of the {team.name}'s {len(games)} {period_str} {plural}{vs}{extra} went to the {period_label}."
        return TemplateResult(data={"team": team.name, "opponent": opponent_name, "games": games, "headline": answer}, answer=answer)

    if measure in PERIOD_RATES:
        return _team_quarter_points_rate_answer(
            team, opponent_name, played, periods=periods, period_label=period_label, period_str=period_str, rank=rank, extra=extra, dateless_extra=dateless_extra, measure=measure
        )
    total = sum(g[measure] for g in played)
    data = {"team": team.name, "opponent": opponent_name, "period": periods[0] if len(periods) == 1 else None, "period_label": period_label, "games": played, "total": total}
    if measure != "points":
        data["stat"] = measure
    if rank in ("most", "fewest"):
        return _team_quarter_points_extreme(team, played, data, rank=rank, measure=measure, period_label=period_label, period_str=period_str, after=f"{vs}{dateless_extra}")
    if measure == "points":
        answer = _phrase_team_quarter_points(team.name, opponent_name, period_label, period_str, extra, dateless_extra, played, total)
    else:
        answer = _phrase_team_quarter_points_stat(team.name, opponent_name, period_label, period_str, extra, dateless_extra, played, measure)
    return TemplateResult(data={**data, "average": round(total / len(played), 2), "headline": answer.split("\n")[0].rstrip(":")}, answer=answer)


def _team_quarter_points_rate_answer(
    team: Entity,
    opponent_name: str | None,
    played: list[dict[str, Any]],
    *,
    periods: tuple[int, ...],
    period_label: str,
    period_str: str,
    rank: Any,
    extra: str,
    dateless_extra: str,
    measure: str,
) -> TemplateResult:
    """A team's shooting percentage in the period, over the games that
    reached it: its makes over its attempts, the ratio of the two sums
    (:func:`_period_split_rate`), one game said its own way and many listed
    beneath the figure as made-attempted. A "most"/"fewest" question is
    refused rather than ranked: one quarter's best percentage is whichever
    game went 2 for 2, and the extreme sentence would name it as a record.

    .. versionadded:: 5.0.0
    """
    made, attempted, pct = _period_split_rate(played, measure)
    word = _PERIOD_RATE_WORDS[measure]
    data: dict[str, Any] = {
        "team": team.name,
        "opponent": opponent_name,
        "period": periods[0] if len(periods) == 1 else None,
        "period_label": period_label,
        "games": played,
        "stat": measure,
        "total": made,
        "attempted": attempted,
        "average": pct,
    }
    if rank in ("most", "fewest"):
        message = (
            f"The {team.name}'s best or worst single {period_label} by {word} is not ranked: over a few attempts the extreme is whichever game went 2 for 2. "
            f"Ask for their {word} in the {period_label} over the span instead."
        )
        return TemplateResult(data={**data, "message": message, "headline": message}, answer=message)
    vs = f" against the {opponent_name}" if opponent_name else ""
    did = _period_split_rate_said(made, attempted, pct, measure)
    if len(played) == 1:
        g = played[0]
        paren = f" ({period_str})" if period_str else ""
        answer = f"The {team.name} {did} in the {period_label} against the {g['opponent']} on {g['date']}{dateless_extra}{paren}."
        return TemplateResult(data={**data, "headline": answer}, answer=answer)
    header = f"The {team.name} {did} in the {period_label} across {len(played)} {period_str} games{vs}{extra}"
    if len(played) > _QUARTER_BREAKDOWN_LIMIT:
        return TemplateResult(data={**data, "headline": header}, answer=header + ".")
    made_column, attempted_column = PERIOD_RATES[measure]
    lines = [f"  {g['date']}  {g[made_column]}-{g[attempted_column]}  {'-' if g[measure] is None else f'{g[measure]:.1f}%'}  vs {g['opponent']}" for g in played]
    return TemplateResult(data={**data, "headline": header}, answer="\n".join([header + ":", *lines]))


def _team_quarter_points_extreme(team: Entity, played: list[dict[str, Any]], data: dict[str, Any], *, rank: str, measure: str, period_label: str, period_str: str, after: str) -> TemplateResult:
    """The single game a "most/least" question asks for.

    "Detroit Pistons most points in a first half this season" asks for ONE
    game, not the season's average - a single-game extreme, the team
    counterpart of single_game_high. Ties are named together rather than
    resolved by whichever row sorted first.
    """
    best = max(g[measure] for g in played) if rank == "most" else min(g[measure] for g in played)
    tied = [g for g in played if g[measure] == best]
    how = "most" if rank == "most" else "fewest"
    where = " and ".join(f"vs the {g['opponent']} on {g['date']}" for g in tied)
    figure = f"scored {best}" if measure == "points" else f"had {best} {_period_split_noun(measure, best)}"
    answer = f"The {team.name} {figure} in the {period_label} {where}, their {how} in the {period_str}{after}."
    return TemplateResult(data={**data, "rank": rank, "extreme": best, "extreme_games": tied, "headline": answer}, answer=answer)


def _phrase_team_quarter_points(team: str, opponent: str | None, period_label: str, period_str: str, extra: str, dateless_extra: str, games: list[dict[str, Any]], total: int) -> str:
    if len(games) == 1:
        g = games[0]
        paren = f" ({period_str})" if period_str else ""
        return f"The {team} scored {g['points']} points in the {period_label} against the {g['opponent']} on {g['date']}{dateless_extra}{paren}."
    if len(games) > _QUARTER_BREAKDOWN_LIMIT:
        avg = total / len(games)
        vs = f" against the {opponent}" if opponent else ""
        return f"The {team} scored {total} total points in the {period_label} across {len(games)} {period_str} games{vs}{extra}, averaging {avg:.1f} per game."
    header = f"The {team}, {period_label} scoring" + (f" against the {opponent}" if opponent else "")
    header += f"{extra}, {period_str} ({len(games)} games, {total} total):"
    lines = [f"  {g['date']}  {g['points']}  vs {g['opponent']}" for g in games]
    return "\n".join([header, *lines])


def _phrase_team_quarter_points_stat(team: str, opponent: str | None, period_label: str, period_str: str, extra: str, dateless_extra: str, games: list[dict[str, Any]], measure: str) -> str:
    """:func:`_phrase_team_quarter_points` for a column other than points:
    the one game, the total and average over many, or each game listed with
    the average beside the total - the average leads, since "3 point average
    1st quarter" asks for it."""
    total = sum(g[measure] for g in games)
    noun = _period_split_noun(measure, 2)
    vs = f" against the {opponent}" if opponent else ""
    if len(games) == 1:
        g = games[0]
        paren = f" ({period_str})" if period_str else ""
        return f"The {team} had {g[measure]} {_period_split_noun(measure, g[measure])} in the {period_label} against the {g['opponent']} on {g['date']}{dateless_extra}{paren}."
    avg = total / len(games)
    if len(games) > _QUARTER_BREAKDOWN_LIMIT:
        return f"The {team} averaged {avg:.1f} {noun} in the {period_label} across {len(games)} {period_str} games{vs}{extra} ({total} in all)."
    header = f"The {team} averaged {avg:.1f} {noun} in the {period_label}{vs}{extra}, {period_str} ({len(games)} games, {total} in all):"
    lines = [f"  {g['date']}  {g[measure]}  vs {g['opponent']}" for g in games]
    return "\n".join([header, *lines])


# Per-period points are summed from `shot_chart`, and how closely that sums to
# ESPN's own linescore is a property of the SEASON, not of the method. Measured
# 2026-09-16 over every regular season, one row per team-quarter, against
# `games.home_linescores`/`away_linescores`: the percentage of team-quarters
# where the sum is EXACTLY the official figure. Only the seasons below 99% are
# listed; the other nineteen run 99.2-100.0%.
#
# The two bad ones have known causes rather than being noise. 2002 cannot be
# answered at all - it is `UNSEPARABLE_SHOT_VALUES`, so `SHOT_VALUE_SQL` is
# NULL for 20,534 of its made shots and a sum over them is meaningless (4.9%).
# 2016 is the season `fetch/repairs/reconstructed_box` also singles out: its scoring
# plays carry types no rule can classify, and it reconciles at 76.5%, which is
# one quarter in four.
PERIOD_RECONCILIATION: dict[int, float] = {2003: 93.5, 2004: 95.7, 2005: 95.9, 2006: 95.8, 2013: 93.7, 2016: 76.5}
"""Per-season agreement between summed shot values and ESPN's linescores, for
the seasons under 99%. Read by :func:`period_split` to caveat or refuse.

.. versionadded:: 2.2.0
"""


PERIOD_REFUSE_BELOW = 90.0
"""Below this agreement a period answer is refused rather than caveated.

Set between 2016's 76.5% and 2003's 93.5% deliberately: a season that is right
19 times in 20 is worth answering with a caveat, and one that is wrong in a
quarter of its quarters is not an answer at all.

.. versionadded:: 2.2.0
"""


def _period_scope(scope: Scope, intent: str = "period_split") -> tuple[tuple[int, ...], str]:
    """The periods a question asks for, and how to name them in an answer -
    the relation's own reading (:func:`common.period_narrowing`), refused here
    when there is none."""
    asked = period_narrowing(scope)
    if asked is None:
        raise TemplateUnsupported(f"{intent} needs a period 1-10 or a half 1-2, got period={scope.period!r} half={scope.half!r}")
    return asked


def _period_split_reconciliation_refusal(season: int, measure: str = "points") -> TemplateResult | None:
    """:func:`_period_split_refusal` for ``season``, over
    :data:`PERIOD_RECONCILIATION` - the one line :func:`period_split` runs
    twice: once up front for a named season (before any name is resolved,
    same as always), and again, only with a ``date``, once the game it names
    is found and its real season known.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       A rate is refused where either of the columns it divides is.
    """
    for column in _period_split_columns(measure):
        refusal = _period_split_column_refusal(season, column)
        if refusal is not None:
            return refusal
    return None


def _period_split_column_refusal(season: int, column: str) -> TemplateResult | None:
    """:func:`_period_split_reconciliation_refusal` for ONE period-line
    column - a rate asks it of both of its columns."""
    if column == "points":
        return _period_split_refusal(season, PERIOD_RECONCILIATION.get(season))
    agreement = PERIOD_AGREEMENT.get(column, {}).get(season)
    if agreement is not None and agreement < PERIOD_REFUSE_BELOW:
        noun = _period_split_noun(column, 2)
        message = f"Per-quarter {noun} cannot be answered for {season}: rebuilt from play-by-play, a game's {noun} match its box score only {agreement:.0f}% of the time."
        return TemplateResult(data={"season": season, "message": message}, answer=message)
    if column in _PERIOD_SPLIT_SHOT_VALUED and season in UNSEPARABLE_SHOT_VALUES:
        return _period_split_refusal(season, None)
    return None


def _period_split_from(con: duckdb.DuckDBPyConnection, scope: Scope, player: Entity, span: _Span, narrowed: _Narrowed, periods: tuple[int, ...], period_label: str, measure: str) -> TemplateResult:
    """A named player's points in ONE quarter or half, per game and averaged
    - ``period_split``'s answer, over a player, span and narrowing already
    settled through the relation's shared steps. The retired template's own
    body (5.0.0); its caller is the compiler's presenter
    (:func:`~association.query.compose.present._present_period_split`),
    which makes the template's early refusals first - a period the scope
    lacks (:func:`_period_scope`), a column the period's line does not
    rebuild (:func:`_period_split_measure`), a season whose per-period
    figures cannot be trusted (:func:`_period_split_reconciliation_refusal`)
    - and settles the player over the shot table
    (:data:`~association.query.shotchart.SHOT_AVAILABILITY`, the same
    availability the template resolved against).

    The counterpart to :func:`team_quarter_points`, which answers a TEAM's
    quarter from the official linescore. A player has no such source, so this
    sums the value of his made shots in that period out of ``shot_chart``.

    **It does not need the plays table, and it does not need `LAG`.**
    ``team_quarter_points`` said for a long time that a player's quarter score
    "needs the plays-table LAG() derivation", and that claim is plausibly why
    this went unwritten: ``shot_chart`` already carries ``athlete_id``,
    ``period``, ``made`` and the shot's value, so the answer is a filtered sum.

    **The value is read through :data:`SHOT_VALUE_SQL`, never guessed from the
    play's prose**, and the difference is the whole accuracy of this template.
    Scored by looking for "three point" in the description, per-period points
    match ESPN's linescores 76.8% of the time, and the error is systematically
    -1: "makes 24-foot running jump shot" is a three that scores as two. Read
    off the shot's own label and position, it is 99.95%. Over a whole game that
    gap hides inside a 98% figure; a quarter holds about ten field goals, so it
    does not.

    Accuracy is a property of the season, and this says so rather than
    averaging it away - see :data:`PERIOD_RECONCILIATION`. 2002 and 2016 are
    refused outright (4.9% and 76.5%); 2003-2006 and 2013 are answered with
    the measured figure attached.

    Points by default; any other column the period's line rebuilds
    (:data:`~association.query.player_games.PERIOD_COLUMNS`) in its own word,
    with its own measured agreement. Minutes, plus-minus and the rates are
    not in the plays at all, and a question asking for them is refused with
    that named as the reason rather than answered from the whole game's box.

    ``span`` "career", ``since`` and ``until`` are refused before this runs
    (:data:`common.RELATION_SCOPING_EXCLUDED`): the accuracy caveat this
    exists to attach is a property of one season, not of a sum across many,
    and the header names one season.

    .. versionadded:: 5.0.0
       ``period_split``'s body from 2.2.0, over a settled narrowing.
    """
    date = scope.date
    season, season_type = scope.season or current_season(), scope.season_type or 2
    opponent = narrowed.opponent
    venue, started = _period_split_narrowing(scope.venue, scope.split)
    rows = _period_split_rows_from(con, narrowed)
    narrowed_mates, narrowed_measures, series_game = [mate.name for mate in narrowed.without], list(narrowed.measures), narrowed.series_game

    if date is not None and rows:
        # The season a date's game actually falls in, read off the row itself
        # rather than the slot: an explicit year in the question ("... on
        # november 11 2019") can name a date the season slot disagrees with.
        season = int(rows[0][1])
        refusal = _period_split_reconciliation_refusal(season, measure)
        if refusal is not None:
            return refusal

    season_label = _period(season, season_type)
    vs = f" against the {opponent.name}" if opponent else ""
    at = _period_split_narrowing_said(venue, started, narrowed_mates, narrowed_measures, date, series_game=series_game, one_series=opponent is not None)
    games = _period_split_games(rows, measure)
    data: dict[str, Any] = {
        "player": player.name,
        "period": period_label,
        "stat": measure,
        "season": season,
        "opponent": opponent.name if opponent else None,
        "venue": venue,
        "started": started,
        "measures": narrowed_measures,
        "games": games,
        "games_played": len(games),
    }
    if not games:
        return _period_split_empty(con, player, span, periods, period_label, scope, opponent, measure_filters(scope.below, scope.above), venue, started, data, season_label, vs, at)

    unknown = _period_split_unread(games, measure)
    if unknown is not None:
        return unknown
    figures = _period_split_figures(games, measure)
    data |= figures
    header = _period_split_header(player, period_label, season_label, vs, at, figures["total"], figures["average"], games, scope, scope.order, measure=measure)
    caveat = _period_split_measure_caveat(season, measure, full_line=scope.per_game and scope.stat is None and len(games) > 1)
    return _period_split_result(data, header, caveat)


def period_leaderboard(ctx: TemplateContext, reading: Reading) -> TemplateResult:
    """Players ranked by a stat in ONE quarter or half, per game - or, with
    no period named, by their points in each of the four quarters at once.

    The league-wide counterpart to :func:`period_split`, and since plan item
    4 it reads the same relation the same way: every player's games in the
    season (:func:`common.league_games`, narrowed to a team's roster when one
    is named), with the question's quarter or half applied by the relation
    itself, so each column is the period's own figure rebuilt from the shots
    and plays (:func:`~association.query.player_games.period_line_sql`).

    Before this existed, "who has the highest average 1st quarter points this
    season?" and "knicks 1st quarter scoring leaders playoffs" reached
    ``other`` and fell through to the agent.

    **The denominator is games PLAYED, not games he scored in** - the
    relation's own rule: a scoreless quarter is a zero over a game he played,
    and a game the shot table does not cover is no game at all.

    **A per-game average needs a minimum**, or the leader is whoever played
    once and scored eight - the same qualifier every other per-game ranking
    here applies (:data:`association.query.metrics.PER_GAME_MIN_GAMES`, five
    in the postseason), and the answer says which it used.

    **One season, and its measured accuracy.** :data:`PERIOD_RECONCILIATION`
    (points) and :data:`~association.query.player_games.PERIOD_AGREEMENT`
    (every other column) are measured per season, so a ranking is of one
    season, refused where the stat's season agrees under
    :data:`PERIOD_REFUSE_BELOW` and caveated where it is under 99%. An
    opponent and a venue narrow the pool (the relation applies them), with
    a qualifier that is a share of the games the narrowing leaves
    (:func:`_period_leaderboard_minimum`, #185); a range of seasons and a
    single date are refused first (:data:`common.HONORED_SCOPING`): the
    accuracy is measured per season, and one game ranks nothing per game.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Reads the period relation (plan item 4): ranks any column the period's
       line rebuilds, not points alone, and answers "points by quarter" (no
       period named) as each qualifying player's four quarters side by side,
       ranked by the four together (yardstick-v2 F048).
    """
    scope = reading.scope
    con = ctx.con
    try:
        measure = _period_split_measure(scope.stat)
    except TemplateUnsupported as exc:
        raise TemplateUnsupported(f"period_leaderboard ranks only what the period's line rebuilds - {exc}") from exc
    if measure in PERIOD_RATES:
        # A games-played qualifier says nothing about attempts, and a
        # percentage over a handful of them ranks noise: the leader in
        # first-quarter free throw percentage would be whoever went 3 for 3.
        # The season ranking (`_leaderboard_ranking`) sets its rate
        # qualifiers by attempts; none is measured for a quarter's.
        word = _PERIOD_RATE_WORDS[measure]
        message = (
            f"Players are not ranked by {word} in a quarter or half: the per-game qualifier every period ranking uses says nothing about attempts, "
            f"and a percentage over a few of them ranks noise. Ask for one player's {word} in that period."
        )
        return TemplateResult(data={"stat": measure, "message": message}, answer=message)
    season = scope.season or current_season()
    season_type = scope.season_type or 2
    refusal = _period_split_reconciliation_refusal(season, measure)
    if refusal is not None:
        return refusal
    span = _span_of(None, season, season_type, "player_game_log")
    minimum = PER_GAME_MIN_POSTSEASON_GAMES if season_type == POSTSEASON else PER_GAME_MIN_GAMES
    asked = period_narrowing(scope)
    if asked is None:
        return _period_leaderboard_by_quarter(con, span, scope, minimum)
    narrowed = league_games(con, span, scope, position=None)
    if isinstance(narrowed, TemplateResult):
        return narrowed
    if not narrowed.period_plays and measure in PERIOD_PLAYS_COLUMNS:
        message = f"Per-quarter {_period_split_noun(measure, 2)} cannot be ranked here: they are rebuilt from play-by-play, and this warehouse holds none."
        return TemplateResult(data={"message": message}, answer=message)
    limit = _clamp_limit(scope.limit)
    minimum, qualifier = _period_leaderboard_minimum(con, narrowed, scope, minimum)
    rows = _period_leaderboard_rows(con, narrowed, measure, minimum=minimum, limit=limit)
    _periods, period_label = asked
    return _period_leaderboard_answer(rows, period_label, _period(season, season_type), narrowed.team, minimum, season, measure, where=_period_leaderboard_where(narrowed), qualifier=qualifier)


def _period_leaderboard_where(narrowed: _Narrowed) -> str:
    """The opponent and the venue a period ranking's pool was narrowed to,
    as the headline says them after the season - ``" vs the Boston Celtics
    at home"`` - and nothing else: the team a ranking is OF is said as "led
    the Knicks", its own way."""
    said = f" vs the {narrowed.opponent.name}" if narrowed.opponent is not None else ""
    if narrowed.venue:
        said += " at home" if narrowed.venue == "home" else " on the road"
    return said


def _period_leaderboard_minimum(con: duckdb.DuckDBPyConnection, narrowed: _Narrowed, scope: Scope, default: int) -> tuple[int, str]:
    """The games a player needs to rank, and how the answer says it. A whole
    season's ranking keeps the per-game minimum every ranking here applies
    (``default``: :data:`~association.query.metrics.PER_GAME_MIN_GAMES`,
    five in the postseason). A pool narrowed to an opponent or a venue
    (#185) keeps a SHARE of the games the narrowing leaves instead - half
    of the most anyone played in it, never more than the season's own
    minimum and never under one - since against one opponent nobody plays
    twenty, and the answer names both the minimum and what it is half of.
    Jeff's call, 2026-09-30: a share of the narrowed games.

    .. versionadded:: 5.0.0
    """
    if not (scope.opponent or scope.venue):
        return default, ""
    sql, params = grouped_sql(narrowed, "pgl.athlete_id", ["COUNT(*) AS games"], order="games DESC", limit=1, rebuilt=box_source(con).rebuilt)
    row = con.execute(sql, params).fetchone()
    most = int(row[0]) if row else 0
    share = max(1, -(-most // 2))
    if share >= default:
        # A venue leaves half a season, where the season's own minimum
        # still applies and is said as itself.
        return default, ""
    return share, f", half of the {most} anyone played"


def _period_leaderboard_minimum_said(minimum: int, qualifier: str) -> str:
    """The minimum a period ranking applied, as its headline says it inside
    the parentheses - "minimum 20 games", or with ``qualifier`` (what a
    narrowed pool's minimum is half of, :func:`_period_leaderboard_minimum`)."""
    said = f"minimum {minimum} games{qualifier}"
    return decided("minimum", said, field="minimum", chose=minimum, why="half_of_most" if qualifier else "per_game_minimum", of="games")


def _period_leaderboard_rows(con: duckdb.DuckDBPyConnection, narrowed: _Narrowed, measure: str, *, minimum: int, limit: int) -> list[tuple[Any, ...]]:
    """(name, games, total, average) per qualifying player, best first -
    grouped over the period-narrowed relation, whose played guard and
    covered-games rule are the denominator."""
    sql, params = grouped_sql(
        narrowed,
        "pgl.athlete_id, pgl.player_name",
        ["pgl.player_name", "COUNT(*) AS games", f"SUM(pgl.{measure}) AS total", f"AVG(pgl.{measure}) AS average"],
        having=f"COUNT(*) >= {int(minimum)}",
        order="average DESC, games DESC, pgl.player_name",
        limit=limit,
        rebuilt=box_source(con).rebuilt,
    )
    return con.execute(sql, params).fetchall()


def _period_leaderboard_answer(
    rows: list[tuple[Any, ...]], period_label: str, scope: str, team: Entity | None, minimum: int, season: int, measure: str = "points", *, where: str = "", qualifier: str = ""
) -> TemplateResult:
    """The ranking as a sentence and a list, naming the qualifier it applied
    - ``where`` the opponent and venue the pool was narrowed to, ``qualifier``
    what a narrowed pool's minimum is a share of (:func:`_period_leaderboard_minimum`).

    .. versionchanged:: 5.0.0
       Takes ``where`` and ``qualifier`` (#185).
    """
    # "led the Knicks in first-quarter points" and "led the league in ..." are
    # both idiomatic; "No player in the league played ..." is not, so the
    # refusal names the group its own way.
    led = f"the {team.name}" if team is not None else "the league"
    among = f" for the {team.name}" if team is not None else ""
    noun = _period_split_noun(measure, 2)
    if not rows:
        message = f"No player{among} played the {minimum} games needed to rank {period_label} {'scoring' if measure == 'points' else noun} in the {scope}{where}."
        return TemplateResult(data={"period": period_label, "season": season, "team": team.name if team else None, "narrowing": where.strip(), "leaders": [], "message": message}, answer=message)
    leaders = [{"player": name, "games": int(games), measure: int(total), "average": round(float(average), 1)} for name, games, total, average in rows]
    top = leaders[0]
    rest = ", ".join(f"{row['player']} ({row['average']})" for row in leaders[1:])
    least = _period_leaderboard_minimum_said(minimum, qualifier)
    headline = f"{top['player']} led {led} in {period_label} {noun} per game in the {scope}{where} ({least}), at {top['average']} over {top['games']} games."
    answer = headline
    if rest:
        answer += f" Next: {rest}."
    caveat = _period_split_measure_caveat(season, measure)
    return TemplateResult(
        data={
            "period": period_label,
            "stat": measure,
            "season": season,
            "team": team.name if team else None,
            "narrowing": where.strip(),
            "minimum_games": minimum,
            "leaders": leaders,
            "headline": headline,
            "notes": [caveat.strip()] if caveat else [],
        },
        answer=answer + caveat,
    )


_PERIOD_LEADERBOARD_BY_QUARTER_LIMIT = 10


def _period_leaderboard_by_quarter(con: duckdb.DuckDBPyConnection, span: _Span, scope: Scope, minimum: int) -> TemplateResult:
    """Points by quarter, per game, for every qualifying player: one read of
    the relation per quarter (each narrowed by the relation itself), joined
    by player, ranked by the four averages together - which is his points
    per game in regulation. Overtime is no quarter and is left out, and the
    answer says so. yardstick-v2 F048, "nba playerspoints by quarter
    average", fell through: the router had one period or nothing."""
    per_quarter: dict[str, dict[int, tuple[int, float]]] = {}
    names: dict[str, str] = {}
    team: Entity | None = None
    box = box_source(con)
    qualifier = ""
    where = ""
    for quarter in REGULATION_QUARTERS:
        narrowed = league_games(con, span, replace(scope, period=quarter, half=None), position=None)
        if isinstance(narrowed, TemplateResult):
            return narrowed
        team = narrowed.team
        if quarter == REGULATION_QUARTERS[0]:
            # The pool is the same games in every quarter, so the share is
            # read once (#185: half of the most anyone played vs an opponent
            # or at a venue).
            minimum, qualifier = _period_leaderboard_minimum(con, narrowed, scope, minimum)
            where = _period_leaderboard_where(narrowed)
        sql, params = grouped_sql(
            narrowed, "pgl.athlete_id, pgl.player_name", ["pgl.athlete_id", "pgl.player_name", "COUNT(*)", "AVG(pgl.points)"], having=f"COUNT(*) >= {int(minimum)}", rebuilt=box.rebuilt
        )
        for athlete, name, games, average in con.execute(sql, params).fetchall():
            names[athlete] = name
            per_quarter.setdefault(athlete, {})[quarter] = (int(games), float(average))
    whole = {athlete: quarters for athlete, quarters in per_quarter.items() if len(quarters) == len(REGULATION_QUARTERS)}
    ranked = sorted(whole, key=lambda athlete: (-sum(avg for _, avg in whole[athlete].values()), names[athlete]))
    limit = _clamp_limit(scope.limit, default=_PERIOD_LEADERBOARD_BY_QUARTER_LIMIT)
    season_label = _period(span.season or current_season(), span.season_type)
    among = f" for the {team.name}" if team is not None else ""
    if not ranked:
        message = f"No player{among} played the {minimum} games needed to rank points by quarter in the {season_label}{where}."
        return TemplateResult(data={"season": span.season, "team": team.name if team else None, "narrowing": where.strip(), "leaders": [], "message": message}, answer=message)
    leaders = [
        {"player": names[a], "games": whole[a][1][0], **{f"q{q}": round(whole[a][q][1], 2) for q in REGULATION_QUARTERS}, "total": round(sum(avg for _, avg in whole[a].values()), 2)}
        for a in ranked[:limit]
    ]
    headline = f"Points per game by quarter{among} in the {season_label}{where} ({_period_leaderboard_minimum_said(minimum, qualifier)}), ranked by the four quarters together:"
    table = [f"  {'player':<26} {'G':>3} {'Q1':>6} {'Q2':>6} {'Q3':>6} {'Q4':>6} {'total':>6}"]
    table += [f"  {row['player']:<26} {row['games']:>3} {row['q1']:>6.2f} {row['q2']:>6.2f} {row['q3']:>6.2f} {row['q4']:>6.2f} {row['total']:>6.2f}" for row in leaders]
    overtime = note("definition", "Overtime is no quarter and is not counted.", term="overtime_excluded")
    shown = decided("cut", f"{len(ranked)} players qualified; the top {len(leaders)} are shown.", field="limit", chose=len(leaders), before=scope.limit, total=len(ranked))
    notes = [f"{overtime} {shown}"]
    caveat = _period_split_measure_caveat(span.season or current_season(), "points")
    answer = "\n".join([headline, *table, *notes]) + caveat
    return TemplateResult(
        data={
            "season": span.season,
            "team": team.name if team else None,
            "narrowing": where.strip(),
            "minimum_games": minimum,
            "leaders": leaders,
            "qualified": len(ranked),
            "headline": headline.rstrip(":"),
            "notes": [*notes, *([caveat.strip()] if caveat else [])],
        },
        answer=answer,
    )


def _period_split_by_quarter_from(scope: Scope, player: Entity, narrowed: _Narrowed, rows: list[dict[str, Any]], measure: str) -> TemplateResult:
    """A named player's four quarters side by side - "Jokic points by
    quarter" (#162), the counterpart of :func:`_period_leaderboard_by_quarter`
    for one man: his per-game figure and total in each quarter, over the
    same games, and the four together (his figure in regulation). ``rows``
    are the compiler's grouped-by-period read
    (:func:`~association.query.compose.core._compile_by_period`): one a
    quarter, with ``games``, the measure's per-game figure and its sums
    (``<m>_total``, or a rate's ``<m>_made``/``<m>_attempted``). The games
    are the same in every quarter - a quarter he did nothing in is a zero
    over a game he played - so the count is said once. Overtime is no
    quarter and is not counted, and the answer says so; the season's
    measured accuracy is attached as every period answer's is.

    .. versionadded:: 5.0.0
    """
    season, season_type = scope.season or current_season(), scope.season_type or 2
    opponent = narrowed.opponent
    venue, started = _period_split_narrowing(scope.venue, scope.split)
    season_label = _period(season, season_type)
    vs = f" against the {opponent.name}" if opponent else ""
    at = _period_split_narrowing_said(venue, started, [mate.name for mate in narrowed.without], list(narrowed.measures), scope.date, series_game=narrowed.series_game, one_series=opponent is not None)
    if narrowed.window is not None:
        # The compiler's window cut these games (the same N in every
        # quarter), so the answer says so - `Narrowed.filters(windowed=True)`'s
        # own phrase.
        order, n = narrowed.window
        at += f" over his {'last' if order == 'recent' else 'first'} {n} game{'s' if n != 1 else ''}"
    by_quarter = {int(r["group"]): r for r in rows}
    games = max((int(r["games"]) for r in rows), default=0)
    data: dict[str, Any] = {
        "player": player.name,
        "stat": measure,
        "season": season,
        "opponent": opponent.name if opponent else None,
        "venue": venue,
        "started": started,
        "measures": list(narrowed.measures),
        "games_played": games,
        "quarters": [],
    }
    if not games:
        message = f"No {season_label} games found for {player.name}{vs}{at}."
        return TemplateResult(data={**data, "message": message, "headline": message}, answer=message)
    if any(by_quarter[q].get(measure) is None and by_quarter[q].get(f"{measure}_total") is None and measure not in PERIOD_RATES for q in by_quarter):
        message = f"Per-quarter {_period_split_columns_noun(measure)} cannot be answered here: they are rebuilt from play-by-play, and this warehouse holds none."
        return TemplateResult(data={"message": message}, answer=message)
    quarters = [_period_split_quarter_entry(by_quarter.get(quarter, {}), quarter, measure) for quarter in REGULATION_QUARTERS]
    data["quarters"] = quarters
    header, table = _period_split_by_quarter_table(player, measure, season_label, vs, at, games, quarters)
    overtime = note("definition", "Overtime is no quarter and is not counted.", term="overtime_excluded")
    caveat = _period_split_measure_caveat(season, measure)
    data |= {"headline": header.rstrip(":"), "notes": [overtime, *([caveat.strip()] if caveat else [])]}
    return TemplateResult(data=data, answer="\n".join([header, *table, f"  {overtime}"]) + caveat)


def _period_split_quarter_entry(row: dict[str, Any], quarter: int, measure: str) -> dict[str, Any]:
    """One quarter of a by-quarter answer's data: its games, per-game
    figure and total - or, for a rate, its makes, attempts and percentage."""
    entry: dict[str, Any] = {"quarter": quarter, "games": int(row.get("games") or 0)}
    if measure in PERIOD_RATES:
        made, attempted = int(row.get(f"{measure}_made") or 0), int(row.get(f"{measure}_attempted") or 0)
        return {**entry, "made": made, "attempted": attempted, "pct": (made * 100.0 / attempted) if attempted else None}
    average = row.get(measure)
    return {**entry, "average": None if average is None else float(average), "total": int(row.get(f"{measure}_total") or 0)}


def _period_split_by_quarter_table(player: Entity, measure: str, season_label: str, vs: str, at: str, games: int, quarters: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """The by-quarter answer's header and its two-row table: per game and
    total (a rate: percentage and made-attempted) in each quarter, then in
    regulation - the four together."""

    def _cells(values: list[str]) -> str:
        return "".join(f"{value:>9}" for value in values[:-1]) + f"{values[-1]:>12}"

    heads = _cells([f"Q{q['quarter']}" for q in quarters] + ["regulation"])
    if measure in PERIOD_RATES:
        made, attempted = sum(q["made"] for q in quarters), sum(q["attempted"] for q in quarters)
        pct_row = _cells([_period_split_pct_cell(q["pct"]) for q in quarters] + [_period_split_pct_cell(made * 100.0 / attempted if attempted else None)])
        made_row = _cells([f"{q['made']}-{q['attempted']}" for q in quarters] + [f"{made}-{attempted}"])
        header = f"{player.name}, {_PERIOD_RATE_WORDS[measure]} by quarter in the {season_label}{vs}{at} ({games} games):"
        return header, [f"  {'':<10}{heads}", f"  {'percentage':<10}{pct_row}", f"  {'made-att':<10}{made_row}"]
    noun = "points" if measure == "points" else _period_split_noun(measure, 2)
    per_game = _cells([_period_split_avg_cell(q["average"]) for q in quarters] + [_period_split_avg_cell(sum(q["average"] or 0.0 for q in quarters))])
    totals = _cells([str(q["total"]) for q in quarters] + [str(sum(q["total"] for q in quarters))])
    header = f"{player.name}, {noun} per game by quarter in the {season_label}{vs}{at} ({games} games):"
    return header, [f"  {'':<10}{heads}", f"  {'per game':<10}{per_game}", f"  {'total':<10}{totals}"]


def _period_split_pct_cell(pct: float | None) -> str:
    """ "35.7%", or "-" where nothing was attempted."""
    return "-" if pct is None else f"{pct:.1f}%"


def _period_split_avg_cell(average: float | None) -> str:
    """ "12.0", or "-" where the column was not rebuilt."""
    return "-" if average is None else f"{average:.1f}"


def _period_split_refusal(season: int, agreement: float | None) -> TemplateResult | None:
    """A refusal for a season whose per-period points do not reliably match
    ESPN's own quarter scores (see :data:`PERIOD_RECONCILIATION`), or None
    for a season trusted at face value."""
    if season in UNSEPARABLE_SHOT_VALUES or (agreement is not None and agreement < PERIOD_REFUSE_BELOW):
        why = UNSEPARABLE_SHOT_VALUES.get(season) or f"its per-period points agree with ESPN's own quarter scores only {agreement:.0f}% of the time"
        message = f"Per-quarter scoring cannot be answered for {season}: {why}."
        return TemplateResult(data={"season": season, "message": message}, answer=message)
    return None


def _period_split_narrowing_said(
    venue: str | None, started: bool | None, mates: list[str], measures: list[str] | None = None, date: str | None = None, *, series_game: int | None = None, one_series: bool = False
) -> str:
    """What the answer says it narrowed to, after the player and the period.

    Said in the answer, like every other narrowing here: a total over his
    starts, or over the games a teammate missed, headed as though it covered
    every game is the silent narrowing ``check_scope`` exists to stop.

    .. versionchanged:: 4.4.0
       Names a line on a box-score column (``below``/``above``,
       :data:`common.MeasureFilter`), the same way :meth:`Narrowed.filters`
       says one - "with under 5 turnovers".

    .. versionchanged:: 4.4.0
       Names a single ``date`` (step 3, C5) - reached only by the "no games
       found" refusal, since a real game already says its own date in
       :func:`_period_split_header`'s one-game branch.
    """
    said = f" at {'home' if venue == 'home' else 'away'}" if venue else ""
    said += "" if started is None else (" as a starter" if started else " off the bench")
    said += f" without {_joined(mates)}" if mates else ""
    said += f" with {_joined(measures)}" if measures else ""
    said += f" on {date}" if date else ""
    # The same words Narrowed.filters() uses: one opponent makes it "the"
    # series, a whole postseason "each".
    said += f" in game {series_game} of {'the' if one_series else 'each'} series" if series_game is not None else ""
    return said


def _period_split_narrowing(venue: Literal["home", "away"] | None, split: Split | None) -> tuple[str | None, bool | None]:
    """The venue and the starter/bench half this question narrows to.

    Split out of :func:`period_split` to keep it inside the complexity gate.
    Takes the slot VALUES rather than the Scope, so `period_split`'s own
    source still names every scoping slot it honors - which is what
    ``test_every_template_honoring_a_scope_slot_actually_reads_it`` reads back
    out of it. Only a NAMED half of the split filters (:data:`common.STARTER_SIDES`).
    """
    return venue, (STARTER_SIDES.get(split) if split is not None else None)


def _period_split_rows(
    con: duckdb.DuckDBPyConnection,
    player: Entity,
    span: _Span,
    periods: tuple[int, ...],
    scope: Scope,
    opponent: Entity | None,
    measures: list[MeasureFilter] | None = None,
    date: str | None = None,
    limit: int | None = None,
) -> tuple[list[tuple[Any, ...]], list[str], list[str], int | None] | TemplateResult:
    """A player's per-game point total in the wanted periods, one row a game.

    The games come from :func:`common.scoped_games`, the one narrowing every
    template that reads a player's games shares - the season-keyed join, the
    did-not-play and empty-line guard, the teammate tenure rule - so a
    narrowing added there reaches this template too. That is how ``without``
    arrived: "scottie barnes stats 2nd half log without rj" was refused for a
    slot no period template honored, while the relation had answered exactly
    that narrowing for four other templates since the port.

    ``opponent`` is applied by hand afterward rather than through
    ``scoped_games``'s own ``opponent`` parameter: the caller resolves it
    eagerly (:func:`period_split` needs the name for the answer whether or not
    any games are narrowed to it), and ``scoped_games`` expects unresolved
    text to look up itself, on the same pattern :func:`common._narrow_player_games`
    always has.

    Two rules on top of the relation, both about the denominator:

    - The games are the ones he PLAYED, with zero where he did not score in
      the period - not the games that have a made shot. The first version
      counted only the latter, so every scoreless quarter left the
      denominator: "RJ Barrett ... over 46 games, averaging 5.4" was a player
      with 57 games and a true 4.4. The sum was right, which is exactly why it
      read as correct.
    - A played game counts only where the shot table covers that game at all.
      2003's shots cover 986 of its games, and a game with no located shots
      would otherwise contribute a confident zero.

    SHOT_VALUE_SQL names ``shot_chart``'s columns bare, and ``season`` is a
    column of ``games`` too - so the value is summed in a CTE joined to
    ``played`` by ``event_id`` alone, never by a literal ``season``/
    ``season_type`` pair: those bare names can only mean ``shot_chart``'s own
    columns as long as nothing else in scope shares them, which is also what
    lets this run for a career-wide ``played`` set (many seasons) or a single
    date (``span.season`` is unset either way) without special-casing one.

    Returns the rows - each carrying the game's own ``season`` beside its
    date, so a caller with a ``date`` rather than a named season can read the
    season the game actually falls in off the row instead of guessing - the
    teammates whose absence narrowed them, and the box-score lines they were
    kept under or over as the answer says them (for the answer to name), or
    the clarifying question the relation asks when a teammate's name matches
    more than one player.

    .. versionchanged:: 4.4.0
       Reads the relation rather than its own copy of the played-game guard,
       and honors ``without`` through it.

    .. versionchanged:: 4.4.0
       Reads ``player``'s games through :func:`common.scoped_games` rather
       than a direct call to :func:`common._narrow_player_games` (step 3, C1).
       Takes the already-settled ``span`` its caller now holds rather than a
       bare ``season``/``season_type`` pair.

    .. versionchanged:: 4.4.0
       Honors ``measures`` (step 3, C2) - a line on a box-score column narrows
       which of the player's games are summed for the period, the same as
       every other template on the relation.

    .. versionchanged:: 4.4.0
       Honors ``date`` (step 3, C5), and the shot-value CTEs join to
       ``played`` by ``event_id`` rather than filtering ``shot_chart`` by a
       literal ``season``/``season_type`` - that literal pair came from
       ``span``, which is unset (``None``) for a date or a career, and bound
       as SQL ``NULL`` it silently matched nothing rather than raising: a
       career question answered "no games found" for a player with thousands
       on record, the same false-cause shape `AGENTS.md` warns about
       elsewhere. Joining by the games the relation already selected removes
       the literal pair entirely, so it needs no fixing up for either shape.

    .. versionchanged:: 4.4.0
       Takes ``limit`` - the newest N games, by date, rather than every game
       of ``span``. ``None`` (the default) is every caller except
       :func:`_period_split_cross_season_redirect`: every OTHER caller reads
       one already-settled season in full (a bare ``order``/``limit`` on
       ``period_split`` is a DISPLAY cap applied afterward, in
       :func:`_period_split_header`, not a row window - see
       ``scoped_games``'s own note on why ``.window`` is a no-op here), and
       changing that default would move the answer for every one of them.
    """
    # The question's own scope, whole - not one built here. A dict built
    # here carried venue, without and split and nothing else, so `game_n` was
    # declared honored and never reached the relation (ISSUES.md, closed).
    narrowed = scoped_games(con, player, span, scope, opponent=opponent, measures=measures or [], date=date)
    if isinstance(narrowed, TemplateResult):
        return narrowed
    rows = _period_split_rows_from(con, narrowed, limit=limit)
    return rows, [mate.name for mate in narrowed.without], list(narrowed.measures), narrowed.series_game


def _period_split_rows_from(con: duckdb.DuckDBPyConnection, narrowed: _Narrowed, limit: int | None = None) -> list[tuple[Any, ...]]:
    """:func:`_period_split_rows`'s read, over a narrowing already settled:
    one row a game, the period's whole line beside the date, the side and
    the opponent, in date order. The relation carries the period
    (``scoped_games`` applied the question's quarter or half), so every
    column read here is already the period's own figure, over the games the
    shot table covers - the denominator rules live in
    ``player_games._period_source``. ``limit`` is the newest N by date.

    .. versionadded:: 5.0.0
    """
    rebuilt = box_source(con).rebuilt
    line = ", ".join(f"pgl.{column}" for column in PERIOD_COLUMNS)
    sql, params = rows_sql(
        narrowed,
        "g.date AS date, pgl.season AS game_season, CASE WHEN g.home_team_id = pgl.team_id THEN 'home' ELSE 'away' END AS side, "
        f"(SELECT {season_name_sql('t.team_id', 'g.season', 't.display_name')} FROM teams t WHERE t.team_id = pgl.opponent_team_id) AS opponent, {line}",
        order="g.date DESC" if limit else "g.date",
        limit=limit,
        rebuilt=rebuilt,
    )
    fetched = con.execute(sql, params).fetchall()
    return sorted(((d, game_season, side, name, dict(zip(PERIOD_COLUMNS, figures, strict=True))) for d, game_season, side, name, *figures in fetched), key=lambda row: row[0])


def _period_split_empty(
    con: duckdb.DuckDBPyConnection,
    player: Entity,
    span: _Span,
    periods: tuple[int, ...],
    period_label: str,
    scope: Scope,
    opponent: Entity | None,
    measures: list[MeasureFilter] | None,
    venue: str | None,
    started: bool | None,
    data: dict[str, Any],
    season_label: str,
    vs: str,
    at: str,
) -> TemplateResult:
    """:func:`period_split`'s own "no games" branch - split out to keep that
    function inside the complexity gate. Tries
    :func:`_period_split_cross_season_redirect` first (yardstick-v2 F050);
    the plain refusal, unchanged, is what it falls back to."""
    redirect = _period_split_cross_season_redirect(con, player, span, periods, period_label, scope, opponent, measures, venue, started)
    if redirect is not None:
        return redirect
    message = f"No {season_label} games found for {player.name}{vs}{at}."
    return TemplateResult(data={**data, "message": message, "headline": message}, answer=message)


def _period_split_cross_season_redirect(
    con: duckdb.DuckDBPyConnection,
    player: Entity,
    span: _Span,
    periods: tuple[int, ...],
    period_label: str,
    scope: Scope,
    opponent: Entity | None,
    measures: list[MeasureFilter] | None,
    venue: str | None,
    started: bool | None,
) -> TemplateResult | None:
    """ "Last N games" with no season named is the newest N over his whole
    CAREER, not "this (defaulted) season alone" - the same reading a bare
    ``limit`` already gets everywhere else on the player relation
    (``common._relation_window``). ``period_split`` cannot simply widen
    ``span`` to "career" through the normal slot path
    (``common.RELATION_SCOPING_EXCLUDED["period_split"]``: the accuracy
    caveat is measured per season, so summing across several would mix
    accuracy levels or drop the caveat) - but that refusal is about a
    QUESTION asking for a career split outright, and this is a defaulted,
    empty ONE-season read finding nothing at all.

    yardstick-v2 F050: "zach collins first quarter stats last 5 games as a
    starter" answered "No 2026 regular season games found for Zach Collins
    as a starter" - true of the box scores it read, and about the wrong
    year: he made zero 2025-26 starts, and his real last 5 starts are all in
    March 2025. Retries the SAME narrowing over his whole career, windowed
    to the newest N by date - the one caller of :func:`_period_split_rows`'s
    own ``limit`` parameter, since every other reads one already-settled
    season in full.

    Only when the season was never named at all (``span.defaulted``) and the
    question asked for a window (``order``/a bare ``limit``,
    :func:`common._relation_window`) - a question that DID name a season
    keeps the plain "no games" refusal, because that is the correct answer.
    Returns the found games ONLY when they land in exactly one season:
    ``PERIOD_RECONCILIATION``'s own caveat is measured per season, so a
    window straddling two would need two different caveats (or none), which
    is not built - None falls back to the refusal that was already about to
    be given, no worse than before this existed.

    .. versionadded:: 4.4.0
    """
    window = _relation_window(scope)
    if not span.defaulted or window is None or window[0] != "recent":
        return None
    _, count = window
    career = _span_of("career", None, span.season_type, "player_game_log")
    widened = _period_split_rows(con, player, career, periods, scope, opponent, measures, limit=count)
    if isinstance(widened, TemplateResult) or not widened[0]:
        return None
    rows, narrowed_mates, narrowed_measures, series_game = widened
    seasons = {int(row[1]) for row in rows}
    if len(seasons) != 1:
        return None
    (season,) = seasons
    measure = _period_split_measure(scope.stat)
    refusal = _period_split_reconciliation_refusal(season, measure)
    if refusal is not None:
        return refusal
    season_label = _period(season, span.season_type)
    vs = f" against the {opponent.name}" if opponent else ""
    at = _period_split_narrowing_said(venue, started, narrowed_mates, narrowed_measures, None, series_game=series_game, one_series=opponent is not None)
    games = _period_split_games(rows, measure)
    if _period_split_unread(games, measure) is not None:
        return None
    figures = _period_split_figures(games, measure)
    data = {
        "player": player.name,
        "period": period_label,
        "stat": measure,
        "season": season,
        "opponent": opponent.name if opponent else None,
        "venue": venue,
        "started": started,
        "measures": narrowed_measures,
        "games": games,
        "games_played": len(games),
        **figures,
    }
    header = _period_split_header(player, period_label, season_label, vs, at, figures["total"], figures["average"], games, scope, scope.order, measure=measure)
    said = f"No games this season, so these are his most recent {len(games)}{at}, from the {season_label}."
    redirect_note = decided("season_fallback", said, field="season", chose=season, before=span.season, games=len(games), season_type=span.season_type)
    caveat = _period_split_measure_caveat(season, measure, full_line=scope.per_game and scope.stat is None and len(games) > 1)
    return _period_split_result(data, header, caveat, extra_note=redirect_note)


def _period_split_header(
    player: Entity, period_label: str, season_label: str, vs: str, at: str, total: int, average: float | None, games: list[dict[str, Any]], scope: Scope, order: Any = None, *, measure: str = "points"
) -> str:
    """The headline sentence: one game's own wording when there is only one,
    the recent-games log appended when ``per_game`` asked for it, or the
    plain season average otherwise.

    .. versionchanged:: 5.0.0
       Takes ``measure`` - any :data:`~association.query.player_games.PERIOD_COLUMNS`
       column, said in its own word ("had 12 rebounds") - and a log that
       named no stat lists the period's whole line, the way a game log lists
       a game's. A rate (:data:`PERIOD_RATES`) is said as its makes over
       its attempts ("shot 4 of 7 (57.1%) on 3-pointers"), with no average
       beside it: the percentage is the average.
    """
    plural = "game" if len(games) == 1 else "games"
    if measure in PERIOD_RATES:
        made, attempted, pct = _period_split_rate(games, measure)
        did = _period_split_rate_said(made, attempted, pct, measure)
        header = f"{player.name} {did} in the {period_label} over {len(games)} {plural} of the {season_label}{vs}{at}."
    else:
        did = f"scored {total} points" if measure == "points" else f"had {total} {_period_split_noun(measure, total)}"
        header = f"{player.name} {did} in the {period_label} over {len(games)} {plural} of the {season_label}{vs}{at}, averaging {average:.1f}."
    if len(games) == 1:
        g = games[0]
        against = f"the {g['opponent']}" if g["opponent"] else "their opponent"
        header = f"{player.name} {did} in the {period_label} {'vs' if g['home_away'] == 'home' else 'at'} {against} on {g['date']} ({season_label})."
    elif scope.per_game:
        # The router sets this when the question said "log", "by game" or "each
        # game". The total and average stay over EVERY game, so the header
        # answers the season; the rows are the most recent games, capped like
        # game_log's, and the line says so rather than letting a ten-row table
        # read as the whole season.
        # `order` picks the END of the season the rows come from, the way it
        # does for game_log. Without reading it, "his first 5 games" showed
        # his last five - a different five games, with nothing saying so.
        count = _clamp_limit(scope.limit, default=DEFAULT_GAME_LOG_LIMIT)
        earliest = order == "first"
        shown = games[:count] if earliest else games[-count:]
        label = "every game" if len(shown) == len(games) else f"the {len(shown)} {'earliest' if earliest else 'most recent'}"
        header += "\n" + _period_split_log(shown if earliest else list(reversed(shown)), period_label, label, measure, full_line=scope.stat is None)
    return header


# The period's whole line as a log shows it: the column, and its heading.
_PERIOD_SPLIT_LOG_LINE: tuple[tuple[str, str], ...] = (
    ("points", "PTS"),
    ("rebounds", "REB"),
    ("assists", "AST"),
    ("steals", "STL"),
    ("blocks", "BLK"),
    ("turnovers", "TO"),
    ("fouls", "PF"),
)


def _period_split_log(games: list[dict[str, Any]], period_label: str, label: str, measure: str, *, full_line: bool) -> str:
    """The log beneath a period answer: one column (the stat asked about),
    or - where no stat was named - the period's whole line, with its field
    goals and free throws as made-attempted, the way a box score prints them.
    A column the warehouse cannot rebuild (no play-by-play) prints "-"."""

    def _period_cell(value: Any) -> str:
        return "-" if value is None else str(value)

    if measure in PERIOD_RATES:
        made_column, attempted_column = PERIOD_RATES[measure]
        rows = []
        for g in games:
            pct = "-" if g[measure] is None else f"{g[measure]:.1f}%"
            rows.append(f"  {g['date']}  {'vs' if g['home_away'] == 'home' else '@ '} {g['opponent'] or '?':<24} {_period_cell(g[made_column])}-{_period_cell(g[attempted_column]):<4} {pct:>6}")
        return f"  {period_label} {_PERIOD_RATE_SHOTS[measure]} made-attempted, {label}:\n" + "\n".join(rows)
    if not full_line:
        rows = [f"  {g['date']}  {'vs' if g['home_away'] == 'home' else '@ '} {g['opponent'] or '?':<24} {_period_cell(g[measure]):>3}" for g in games]
        return f"  {period_label} {_period_split_noun(measure, 2)}, {label}:\n" + "\n".join(rows)
    heading = f"  {'date':<10}  {'':<27} {'FG':>5} {'FT':>5} " + " ".join(f"{name:>3}" for _, name in _PERIOD_SPLIT_LOG_LINE)
    rows = []
    for g in games:
        line = g["line"]
        fg = f"{_period_cell(line['fieldGoalsMade'])}-{_period_cell(line['fieldGoalsAttempted'])}"
        ft = f"{_period_cell(line['freeThrowsMade'])}-{_period_cell(line['freeThrowsAttempted'])}"
        figures = " ".join(f"{_period_cell(line[column]):>3}" for column, _ in _PERIOD_SPLIT_LOG_LINE)
        rows.append(f"  {g['date']}  {'vs' if g['home_away'] == 'home' else '@ '} {g['opponent'] or '?':<24} {fg:>5} {ft:>5} {figures}")
    return f"  {period_label} line, {label}:\n{heading}\n" + "\n".join(rows)


def _period_split_noun(measure: str, n: int) -> str:
    """``"rebound"``/``"rebounds"`` - the word a period answer says a column
    in; a rate's own name ("free throw percentage"), which has no plural."""
    if measure in _PERIOD_RATE_WORDS:
        return _PERIOD_RATE_WORDS[measure]
    word = STAT_LABELS.get(measure) or _PERIOD_SPLIT_WORDS.get(measure, measure)
    return word if n == 1 else f"{word}s"


# The columns whose figure needs a shot's VALUE (two or three), which 2002's
# shots do not carry (UNSEPARABLE_SHOT_VALUES) - refused there like points.
_PERIOD_SPLIT_SHOT_VALUED = frozenset({"threePointFieldGoalsMade", "threePointFieldGoalsAttempted"})


_PERIOD_SPLIT_WORDS: dict[str, str] = {
    "fieldGoalsAttempted": "field goal attempt",
    "threePointFieldGoalsAttempted": "3-point attempt",
    "freeThrowsAttempted": "free throw attempt",
    "offensiveRebounds": "offensive rebound",
    "defensiveRebounds": "defensive rebound",
}

PERIOD_RATES: dict[str, tuple[str, str]] = {
    "fg_pct": ("fieldGoalsMade", "fieldGoalsAttempted"),
    "three_pct": ("threePointFieldGoalsMade", "threePointFieldGoalsAttempted"),
    "ft_pct": ("freeThrowsMade", "freeThrowsAttempted"),
}
"""A shooting percentage a period answer computes from the period's own makes
and attempts - the ratio of the two sums over the games, never a mean of
per-game rates - keyed by the compiler's own measure name
(:data:`association.query.compose.core.RATES`), so the point the adapter plans
with one compiles as it is. The makes and the attempts are both on the
period's line (:data:`~association.query.player_games.PERIOD_COLUMNS`) and on
the team's (:data:`~association.query.team_games.TEAM_PERIOD_COLUMNS`), which
is what makes a period's percentage a ratio away rather than a new read.

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

# How a rate is said: the percentage's own name, and what was shot.
_PERIOD_RATE_WORDS: dict[str, str] = {"fg_pct": "field goal percentage", "three_pct": "3-point percentage", "ft_pct": "free throw percentage"}
_PERIOD_RATE_SHOTS: dict[str, str] = {"fg_pct": "field goals", "three_pct": "3-pointers", "ft_pct": "free throws"}


def _period_split_columns(measure: str) -> tuple[str, ...]:
    """The period-line columns ``measure`` is read from: itself, or a rate's
    makes and attempts - so a caveat or a refusal about a rate is about
    both of the columns it divides."""
    return PERIOD_RATES.get(measure, (measure,))


def _period_split_columns_noun(measure: str) -> str:
    """ "rebounds", or for a rate the two columns it divides - "free throws
    and free throw attempts" - for a caveat that is about how those columns
    were rebuilt rather than about the percentage."""
    return " and ".join(_period_split_noun(column, 2) for column in _period_split_columns(measure))


def _period_split_rate(games: list[dict[str, Any]], measure: str) -> tuple[int, int, float | None]:
    """A rate's makes, attempts and percentage over ``games`` - the ratio
    of the sums, and None where nothing was attempted (no percentage is
    a fact about zero attempts, not a zero)."""
    made_column, attempted_column = PERIOD_RATES[measure]
    made = sum(int(g[made_column]) for g in games)
    attempted = sum(int(g[attempted_column]) for g in games)
    return made, attempted, (made * 100.0 / attempted if attempted else None)


def _period_split_rate_said(made: int, attempted: int, pct: float | None, measure: str) -> str:
    """ "shot 4 of 7 (57.1%) on 3-pointers", or "attempted no free throws"."""
    shots = _PERIOD_RATE_SHOTS[measure]
    if pct is None:
        return f"attempted no {shots}"
    return f"shot {made} of {attempted} ({pct:.1f}%) on {shots}"


def _period_split_figures(games: list[dict[str, Any]], measure: str) -> dict[str, Any]:
    """What a period answer's data carries for its measure over ``games``:
    a column's ``total`` and per-game ``average``; a rate's makes as
    ``total``, its ``attempted``, and the percentage as ``average`` (None
    with no attempts)."""
    if measure in PERIOD_RATES:
        made, attempted, pct = _period_split_rate(games, measure)
        return {"total": made, "attempted": attempted, "average": pct}
    total = sum(g[measure] for g in games)
    return {"total": total, "average": total / len(games)}


def _period_split_measure(stat: str | None) -> str:
    """The column a period question measures: points where it names none,
    else the one it names - any column the period's line rebuilds
    (:data:`~association.query.player_games.PERIOD_COLUMNS`), or a shooting
    percentage over two of them (:data:`PERIOD_RATE_STATS`). Anything else
    is refused, naming why: play-by-play records no minutes, plus-minus or
    advanced rate per quarter, and a period answer read from the whole
    game's box would be the wrong question answered fluently.

    .. versionchanged:: 5.0.0
       Reads a field goal, 3-point or free throw percentage.
    """
    if stat is None or not stat.strip() or stat == "all":
        return "points"
    if stat in PERIOD_COLUMNS:
        return stat
    if stat in PERIOD_RATE_STATS:
        return PERIOD_RATE_STATS[stat]
    raise TemplateUnsupported(
        f"period_split has no per-period {stat!r} - the period's line rebuilds {', '.join(PERIOD_COLUMNS)} from the plays, "
        "and a field goal, 3-point or free throw percentage is a ratio of those; nothing else"
    )


def _period_split_unread(games: list[dict[str, Any]], measure: str) -> TemplateResult | None:
    """A refusal where ``measure`` could not be rebuilt at all - a warehouse
    loaded without play-by-play leaves every plays column NULL, and summing
    NULLs as zeros would answer "no rebounds" for a man who had ten."""
    if any(g[column] is None for g in games for column in _period_split_columns(measure)):
        message = f"Per-quarter {_period_split_columns_noun(measure)} cannot be answered here: they are rebuilt from play-by-play, and this warehouse holds none."
        return TemplateResult(data={"message": message}, answer=message)
    return None


def _period_split_games(rows: list[tuple[Any, ...]], measure: str = "points") -> list[dict[str, Any]]:
    """The games as a period answer's data carries them: the date, the
    opponent, home or away, every rebuilt column at the top level (``points``,
    ``rebounds`` ...) and the whole ``line`` beside them - and, for a rate,
    the game's own percentage under the rate's name (None where nothing was
    attempted).

    .. versionchanged:: 5.0.0
       Takes ``measure``, for a rate's per-game figure.
    """
    games = [{"date": _eastern_date(d), "opponent": name, "home_away": side, **line, "line": line} for d, _game_season, side, name, line in rows]
    if measure in PERIOD_RATES:
        for g in games:
            g[measure] = _period_split_rate([g], measure)[2] if all(g[column] is not None for column in PERIOD_RATES[measure]) else None
    return games


def _period_split_caveat(season: int, agreement: float | None) -> str:
    """The note that a season's per-period points are summed from shot data
    rather than an official box score, for a season whose accuracy against
    ESPN's own quarter scores has been measured; empty otherwise."""
    if agreement is None:
        return ""
    said = (
        f"\n  (Summed from shot data rather than an official per-quarter box score. In {season} that sum matches ESPN's own "
        f"quarter scores {agreement:.0f}% of the time, so treat a single game as approximate.)"
    )
    return note("rebuilt_agreement", said, season=season, pct=agreement, columns=["points"], what="period_points_from_shots")


def _period_split_measure_caveat(season: int, measure: str, *, full_line: bool = False) -> str:
    """The caveat a period answer carries for ``measure`` in ``season``:
    points by their per-period agreement with ESPN's own quarter scores
    (:data:`PERIOD_RECONCILIATION`); any other column by how often a game's
    rebuilt figure, summed over its periods, equals its box score
    (:data:`~association.query.player_games.PERIOD_AGREEMENT`). A whole line
    names every column the season holds under 99%."""
    if measure == "points" and not full_line:
        return _period_split_caveat(season, PERIOD_RECONCILIATION.get(season))
    columns = [column for column, _ in _PERIOD_SPLIT_LOG_LINE] if full_line else list(_period_split_columns(measure))
    weak = [(column, PERIOD_AGREEMENT[column][season]) for column in columns if season in PERIOD_AGREEMENT.get(column, {})]
    points = _period_split_caveat(season, PERIOD_RECONCILIATION.get(season)) if full_line else ""
    if not weak:
        return points
    said = ", ".join(f"{_period_split_noun(column, 2)} {pct:.0f}%" for column, pct in weak)
    rebuilt = (
        f"\n  (Rebuilt from play-by-play rather than an official per-quarter box score. In {season} a game's figures rebuilt this way match its box score "
        f"this often: {said} - treat a single game as approximate.)"
    )
    return points + note("rebuilt_agreement", rebuilt, season=season, columns=[column for column, _ in weak], pct=[pct for _, pct in weak], what="period_rebuilt")


def _period_split_result(data: dict[str, Any], header: str, caveat: str, *, extra_note: str | None = None) -> TemplateResult:
    """``period_split``'s own return, wherever it lands: ``headline`` is the
    header's own first line (it grows a per-game table of its own for a
    "log"/"by game" question - see ``_period_split_header``'s ``per_game``
    branch - so only the first line is the sentence), and ``notes`` carries
    ``extra_note`` (the cross-season redirect, when the caller has one) and
    the reconciliation caveat, each its own line, exactly as the answer text
    already joins them. Split out of the two callers to keep each under the
    complexity gate.

    .. versionadded:: 4.4.0
    """
    data["headline"] = header.split("\n")[0]
    data["notes"] = [*([extra_note] if extra_note else []), *([caveat.strip()] if caveat else [])]
    body = header + (f"\n{extra_note}" if extra_note else "") + caveat
    return TemplateResult(data=data, answer=body)


_DEFAULT_MEETINGS_LOGGED = 5


def _player_matchup_from(con: duckdb.DuckDBPyConnection, scope: Scope, covered: _Scope, a: Entity, b: Entity, narrowed: _Narrowed, meetings: list[dict[str, Any]], together: int) -> TemplateResult:
    """The games two named players both played, on opposite teams: the
    head-to-head record, each one's averages in those games, and the most
    recent meetings - over the meetings the compiler's ``pair`` shape read
    (``compose.core._compile_pair``, ROADMAP plan item 6, step (g)) and the
    games they played as teammates. A game in which they were teammates is
    not a meeting, and when every shared game was one, the answer says that
    rather than that they never played. The retired template's docstring
    (``player_matchup``, 2.1.0-5.0.0) carries the shape's history: a genuine
    two-player matchup narrows the first player's games through the shared
    ``scoped_games`` step, so a teammate's absence, a venue, a date, a
    starter half and a calendar are honored and stated; ``opponent`` is
    refused (two players' meetings have no third team to narrow by), and a
    player against a team is that player's own question, never a matchup.

    .. versionadded:: 5.0.0
    """
    unseen = _unseen_meetings(con, covered, a.id, b.id)
    said = (
        f" {unseen} game{'' if unseen == 1 else 's'} between their teams while both were playing for them {'has' if unseen == 1 else 'have'} no box score, so a meeting there is not counted."
        if unseen
        else ""
    )
    caveat = note("games_unseen", said, games=unseen, why="no_box_score", what="meetings")
    if not meetings:
        return _player_matchup_no_meetings(con, covered, a, b, together, caveat + _player_matchup_absence_context(con, a, b, scope, narrowed), narrowed.filters())

    wins, lines, count, summary = _player_matchup_summary(a, b, meetings)
    shown, log = _player_matchup_log(con, meetings, scope.limit, a, b)
    return _player_matchup_answer(a, b, covered, meetings, wins, lines, count, summary, shown, log, caveat, narrowed.filters())


def _player_matchup_covered(scope: Scope) -> _Scope:
    """The span both names resolve over: the question's own - or, where a
    date names the game, the career, since a date replaces the season the
    way ``game_log``'s own date does (the season is usually the "current"
    default, and a date from an earlier season looked for in that one finds
    nothing)."""
    if scope.date:
        return _condition_scope(None, "career", scope.season_type, _PLAYER_GAME_TABLES, since=scope.since)
    return _condition_scope(scope.season, scope.span, scope.season_type, _PLAYER_GAME_TABLES, since=scope.since)


def _player_matchup_narrowed(con: duckdb.DuckDBPyConnection, a: Entity, b: Entity, scope: Scope) -> _Narrowed | TemplateResult:
    """The first player's games under every row-level narrowing the question
    carries - a teammate's absence ("curry vs lebron without kd", yardstick-v2
    F114), a venue, a date, a starter half, a calendar - read through the
    shared step so the pair relation honors what every other template on the
    relation honors, and says it. The window (``limit``) is the matchup's
    own: the newest N meetings are shown BENEATH averages over all of them,
    never a cut on the averages. A second player who is also the absent
    teammate ("fox vs wembanyama without wembanyama") is a question with no
    games in it, said so rather than answered "never met" (found by the
    golden the day ``without`` landed on the pair relation).

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Hands ``scoped_games`` the date: "curry vs lebron on 2025-04-03" used
       to answer every meeting of the season, the date reaching every other
       relation cell here but never the one-day filter. A date names its
       game, so it replaces the season here too, the way ``game_log``'s does.
    """
    span = _span_of("career" if scope.date else scope.span, None if scope.date else scope.season, _player_relation_season_type(scope), "player_game_log", since=scope.since, until=scope.until)
    narrowed = scoped_games(con, a, span, replace(scope, limit=None, order=None, opponent=None), opponent=None, measures=measure_filters(scope.below, scope.above), date=scope.date)
    if isinstance(narrowed, TemplateResult):
        return narrowed
    narrowed = whole_span(narrowed)
    if any(absent.id == b.id for absent in narrowed.without):
        message = f"{b.name} is both the player {a.name} is matched against and the teammate named as absent - no game can be both. Name the opponent team, or drop 'without'."
        return TemplateResult(data={"players": [a.name, b.name], "message": message}, answer=message)
    return narrowed


def _player_matchup_absence_context(con: duckdb.DuckDBPyConnection, a: Entity, b: Entity, scope: Scope, narrowed: _Narrowed) -> str:
    """Why a matchup narrowed by a teammate's absence holds no meetings, in
    the numbers that answer the question a reader probably meant: "steph
    curry record vs lebron regular season without kd" (yardstick-v2 F114)
    is "no meetings" because "without Durant" counts only the games inside
    Durant's time as Curry's teammate (2017-2019), and Durant played every
    Curry-LeBron meeting in it - while over their careers the two met many
    more times, most of them with Durant on neither team. Both figures are
    said, so the reader has the other reading without re-asking. Empty
    where no absence was named.

    .. versionadded:: 5.0.0
    """
    if not narrowed.without:
        return ""
    unconditioned = replace(scope, without=(), conditions=())
    everywhere = _player_matchup_narrowed(con, a, b, unconditioned)
    if isinstance(everywhere, TemplateResult):
        return ""
    all_meetings, _ = _meetings(con, everywhere, b.id)
    if not all_meetings:
        return ""
    names = _joined([mate.name for mate in narrowed.without])
    playing = tuple(ConditionSpec(player=mate.name, side="own", predicate="played") for mate in narrowed.without)
    beside = _player_matchup_narrowed(con, a, b, replace(unconditioned, conditions=playing))
    with_them = len(_meetings(con, beside, b.id)[0]) if not isinstance(beside, TemplateResult) else 0
    first, last = min(m["season"] for m in all_meetings), max(m["season"] for m in all_meetings)
    return (
        f" Over {first}-{last} they met {len(all_meetings)} time{'s' if len(all_meetings) != 1 else ''} in all, {with_them} of them with {names} playing beside {a.name}; "
        f"'without {names}' counts only the games he missed while on {a.name}'s team, and there were none among their meetings."
    )


def _player_matchup_no_meetings(con: duckdb.DuckDBPyConnection, scope: _Scope, a: Entity, b: Entity, together: int, caveat: str, narrowing: str = "") -> TemplateResult:
    """The refusal for two players who never played against each other in
    scope - naming whichever of them has no games at all, since that is the
    missing fact rather than the matchup itself. ``narrowing`` is what the
    first player's games were narrowed to (" without Jamal Murray"): with one,
    the sentence says the meetings are missing from THOSE games, since
    "never played against each other" would be false of two players who
    met whenever the teammate was there (found by the golden the day the
    narrowing landed: Jokic and Embiid "never met" without Murray)."""
    for player in (a, b):
        if _totals(con, _player_games(scope, box=box_source(con)), {**scope.params(), "player": player.id})[0] == 0:
            return _no_games(con, player, scope, None)
    teammates = f" - they were teammates in all {together} games they both played" if together else ""
    if narrowing:
        message = f"No meetings between {a.name} and {b.name} in {a.name}'s games{narrowing} {_where_in(scope)}{teammates}.{caveat}"
    else:
        message = f"{a.name} and {b.name} never played against each other {_where_in(scope)}{teammates}.{caveat}"
    return TemplateResult(data={"players": [a.name, b.name], "meetings": 0, "teammate_games": together, "headline": message}, answer=message)


def _player_matchup_summary(a: Entity, b: Entity, meetings: list[dict[str, Any]]) -> tuple[int, dict[str, dict[str, Any]], int, list[tuple[str, list[str]]]]:
    """The head-to-head record and each player's averages in the meetings, as
    the summary table's rows."""
    wins = sum(1 for m in meetings if m["won"])
    lines = {a.name: _matchup_line([m["a"] for m in meetings]), b.name: _matchup_line([m["b"] for m in meetings])}
    count = len(meetings)
    summary = [("wins", [str(wins), str(count - wins)])] + [
        (header, [_cell(lines[p.name][key]) for p in (a, b)]) for key, header in (("minutes", "minutes"), ("points", "points"), ("rebounds", "rebounds"), ("assists", "assists"), ("fg_pct", "FG%"))
    ]
    return wins, lines, count, summary


def _player_matchup_stat_line(stats: dict[str, Any]) -> str:
    """One player's points/rebounds/assists in one meeting, for the log."""
    return f"{stats['points']}/{stats['rebounds']}/{stats['assists']}"


def _player_matchup_abbr(team_id: str, season: int, abbr: dict[str, str]) -> str:
    """A meeting's team as it was abbreviated THAT season - a 2005 Nets game reads NJ, not BKN."""
    return season_name(team_id, season, abbr[team_id], column="abbreviation")


def _player_matchup_log(con: duckdb.DuckDBPyConnection, meetings: list[dict[str, Any]], limit: Any, a: Entity, b: Entity) -> tuple[list[dict[str, Any]], list[tuple[str, list[str]]]]:
    """The most recent meetings logged: each one's score and both players'
    points/rebounds/assists, with teams abbreviated the way they were that season."""
    shown = meetings[: _clamp_limit(limit, _DEFAULT_MEETINGS_LOGGED)]
    abbr = _names(con, "teams", "team_id", {m["team_id"] for m in shown} | {m["opponent_team_id"] for m in shown}, column="abbreviation")
    log = [
        (
            str(m["day"]),
            [
                f"{_player_matchup_abbr(m['team_id'], m['season'], abbr)} {m['team_score']}-{m['opponent_score']} {_player_matchup_abbr(m['opponent_team_id'], m['season'], abbr)}",
                _player_matchup_stat_line(m["a"]),
                _player_matchup_stat_line(m["b"]),
            ],
        )
        for m in shown
    ]
    return shown, log


def _player_matchup_answer(
    a: Entity,
    b: Entity,
    scope: _Scope,
    meetings: list[dict[str, Any]],
    wins: int,
    lines: dict[str, dict[str, Any]],
    count: int,
    summary: list[tuple[str, list[str]]],
    shown: list[dict[str, Any]],
    log: list[tuple[str, list[str]]],
    caveat: str,
    narrowing: str = "",
) -> TemplateResult:
    """The head-to-head summary table and the most recent meetings beside it.
    ``narrowing`` is what the first player's games were narrowed to, as the
    relation says it (" without Kevin Durant", " at home")."""
    label = scope.label(min(m["season"] for m in meetings), max(m["season"] for m in meetings))
    title = f"{a.name} vs {b.name}{narrowing}, {label}: {count} meeting{'' if count == 1 else 's'}, {a.name}'s team won {wins}."
    answer = _table(title, [a.name, b.name], summary)
    answer += "\n\n" + _table(f"Most recent {len(shown)} of {count} (points/rebounds/assists):", ["score", a.name, b.name], log)
    answer += f"\n{caveat.strip()}" if caveat else ""
    games = [{"date": str(m["day"]), "won": m["won"], "team_score": m["team_score"], "opponent_score": m["opponent_score"], a.name: m["a"], b.name: m["b"]} for m in shown]
    data = {
        "players": [a.name, b.name],
        "span": label,
        "meetings": count,
        "wins": {a.name: wins, b.name: count - wins},
        "averages": lines,
        "games": games,
        "headline": title,
        "notes": [caveat.strip()] if caveat else [],
    }
    return TemplateResult(data=data, answer=answer)
