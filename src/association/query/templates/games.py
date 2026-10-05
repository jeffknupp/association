"""Game-level questions: game logs, head-to-head records, a team's or player's quarter or half, and two players' meetings.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal, cast

import duckdb

from association.nba.coverage import POSTSEASON
from association.nba.franchises import season_name_sql
from association.nba.season import current_season
from association.query.measures import PERIOD_RATE_STATS as PERIOD_RATE_STATS
from association.query.measures import period_split_measure
from association.query.reading import DEFAULT_GAME_LOG_LIMIT as DEFAULT_GAME_LOG_LIMIT
from association.query.reading import Reading, Scope

from ..conditions import box_source
from ..entities import Entity, resolve_team
from ..measures import resolve_metric
from ..metrics import PER_GAME_MIN_GAMES, PER_GAME_MIN_POSTSEASON_GAMES
from ..notes import Note, decided, note
from ..player_games import (
    PERIOD_PLAYS_COLUMNS,
    PERIOD_RATES,
    PERIOD_REFUSE_BELOW,
    REGULATION_QUARTERS,
    _joined,
    grouped_sql,
    period_agreement_notes,
    period_columns,
    period_distrust,
    period_rate,
)
from ..team_games import TEAM_PERIOD_AGREEMENT, TEAM_PERIOD_COLUMNS, TeamNarrowed
from ..team_games import games_subquery as team_games_subquery
from ..team_games import rows_sql as team_rows_sql
from .common import (
    STAT_LABELS,
    TemplateContext,
    TemplateResult,
    TemplateUnsupported,
    _clamp_limit,
    _Narrowed,
    _period,
    _resolved_team,
    _slot_season,
    _Span,
    _span_of,
    _validated_until,
    league_games,
    period_narrowing,
    scoped_team,
    team_games,
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
    from association.query.compose.say import period_noun  # the sayer's words; a call-time import, since compose imports this module

    if stat is None or not str(stat).strip() or stat == "all" or resolve_metric(stat) in ("avg_points", "total_points"):
        return "points"
    if stat in TEAM_PERIOD_COLUMNS:
        return str(stat)
    if stat in PERIOD_RATE_STATS:
        return PERIOD_RATE_STATS[stat]
    held = _joined(["points", *(period_noun(c, 2) for c in TEAM_PERIOD_COLUMNS if c != "points"), "the field goal, 3-point and free throw percentages from them"])
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
    columns = period_columns(measure)
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
    from association.query.compose.say import period_columns_noun, period_noun  # the sayer's words; a call-time import, since compose imports this module

    noun = period_noun(measure, 2)
    columns_noun = period_columns_noun(measure)
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
    from association.query.compose.say import period_columns_noun  # the sayer's words; a call-time import, since compose imports this module

    noun = period_columns_noun(measure)
    notes = [note("games_unseen", f"{missing} of the {reached} games have no play-by-play and are not counted.", games=missing, of=reached, why="no_play_by_play")] if missing else []
    listed = sorted((season, pct) for season, pct in weak.items() if pct is not None)
    if listed:
        said = ", ".join(f"{pct:.1f}% of the time in {season}" for season, pct in listed)
        sentence = f"Rebuilt from play-by-play rather than an official per-quarter box score: a team-game's {noun} rebuilt this way match its box score {said} - treat a single game as approximate."
        seasons, pcts = [season for season, _ in listed], [pct for _, pct in listed]
        notes.append(note("rebuilt_agreement", sentence, seasons=seasons, pct=pcts, columns=list(period_columns(measure)), stat=measure, what="team_period_rebuilt"))
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
    columns = period_columns(measure)
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
            game[measure] = period_rate([game], measure)[2] if all(game[column] is not None for column in columns) else None
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
       percentage over two of them (:data:`~association.query.player_games.PERIOD_RATES`), said through
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
    (:func:`~association.query.player_games.period_rate`), one game said its own way and many listed
    beneath the figure as made-attempted. A "most"/"fewest" question is
    refused rather than ranked: one quarter's best percentage is whichever
    game went 2 for 2, and the extreme sentence would name it as a record.

    .. versionadded:: 5.0.0
    """
    from association.query.compose.say import PERIOD_RATE_WORDS, period_rate_said  # the sayer's words; a call-time import, since compose imports this module

    made, attempted, pct = period_rate(played, measure)
    word = PERIOD_RATE_WORDS[measure]
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
    did = period_rate_said(made, attempted, pct, measure)
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
    from association.query.compose.say import period_noun  # the sayer's words; a call-time import, since compose imports this module

    best = max(g[measure] for g in played) if rank == "most" else min(g[measure] for g in played)
    tied = [g for g in played if g[measure] == best]
    how = "most" if rank == "most" else "fewest"
    where = " and ".join(f"vs the {g['opponent']} on {g['date']}" for g in tied)
    figure = f"scored {best}" if measure == "points" else f"had {best} {period_noun(measure, best)}"
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
    from association.query.compose.say import period_noun  # the sayer's words; a call-time import, since compose imports this module

    total = sum(g[measure] for g in games)
    noun = period_noun(measure, 2)
    vs = f" against the {opponent}" if opponent else ""
    if len(games) == 1:
        g = games[0]
        paren = f" ({period_str})" if period_str else ""
        return f"The {team} had {g[measure]} {period_noun(measure, g[measure])} in the {period_label} against the {g['opponent']} on {g['date']}{dateless_extra}{paren}."
    avg = total / len(games)
    if len(games) > _QUARTER_BREAKDOWN_LIMIT:
        return f"The {team} averaged {avg:.1f} {noun} in the {period_label} across {len(games)} {period_str} games{vs}{extra} ({total} in all)."
    header = f"The {team} averaged {avg:.1f} {noun} in the {period_label}{vs}{extra}, {period_str} ({len(games)} games, {total} in all):"
    lines = [f"  {g['date']}  {g[measure]}  vs {g['opponent']}" for g in games]
    return "\n".join([header, *lines])


def _period_scope(scope: Scope, intent: str = "period_split") -> tuple[tuple[int, ...], str]:
    """The periods a question asks for, and how to name them in an answer -
    the relation's own reading (:func:`common.period_narrowing`), refused here
    when there is none."""
    asked = period_narrowing(scope)
    if asked is None:
        raise TemplateUnsupported(f"{intent} needs a period 1-10 or a half 1-2, got period={scope.period!r} half={scope.half!r}")
    return asked


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

    **One season, and its measured accuracy.** :data:`~association.query.player_games.PERIOD_RECONCILIATION`
    (points) and :data:`~association.query.player_games.PERIOD_AGREEMENT`
    (every other column) are measured per season, so a ranking is of one
    season, refused where the stat's season agrees under
    :data:`~association.query.player_games.PERIOD_REFUSE_BELOW` and caveated where it is under 99%. An
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
    from association.query.compose.say import PERIOD_RATE_WORDS, period_noun, say_period_refusal  # the sayer's words; a call-time import, since compose imports this module

    scope = reading.scope
    con = ctx.con
    try:
        measure = period_split_measure(scope.stat)
    except TemplateUnsupported as exc:
        raise TemplateUnsupported(f"period_leaderboard ranks only what the period's line rebuilds - {exc}") from exc
    if measure in PERIOD_RATES:
        # A games-played qualifier says nothing about attempts, and a
        # percentage over a handful of them ranks noise: the leader in
        # first-quarter free throw percentage would be whoever went 3 for 3.
        # The season ranking (`_leaderboard_ranking`) sets its rate
        # qualifiers by attempts; none is measured for a quarter's.
        word = PERIOD_RATE_WORDS[measure]
        message = (
            f"Players are not ranked by {word} in a quarter or half: the per-game qualifier every period ranking uses says nothing about attempts, "
            f"and a percentage over a few of them ranks noise. Ask for one player's {word} in that period."
        )
        return TemplateResult(data={"stat": measure, "message": message}, answer=message)
    season = scope.season or current_season()
    season_type = scope.season_type or 2
    distrust = period_distrust(season, measure)
    if distrust is not None:
        return say_period_refusal(distrust)
    span = _span_of(None, season, season_type, "player_game_log")
    minimum = PER_GAME_MIN_POSTSEASON_GAMES if season_type == POSTSEASON else PER_GAME_MIN_GAMES
    asked = period_narrowing(scope)
    if asked is None:
        return _period_leaderboard_by_quarter(con, span, scope, minimum)
    narrowed = league_games(con, span, scope, position=None)
    if isinstance(narrowed, TemplateResult):
        return narrowed
    if not narrowed.period_plays and measure in PERIOD_PLAYS_COLUMNS:
        message = f"Per-quarter {period_noun(measure, 2)} cannot be ranked here: they are rebuilt from play-by-play, and this warehouse holds none."
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
    from association.query.compose.say import period_caveat, period_noun  # the sayer's words; a call-time import, since compose imports this module

    # "led the Knicks in first-quarter points" and "led the league in ..." are
    # both idiomatic; "No player in the league played ..." is not, so the
    # refusal names the group its own way.
    led = f"the {team.name}" if team is not None else "the league"
    among = f" for the {team.name}" if team is not None else ""
    noun = period_noun(measure, 2)
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
    caveat = period_caveat(period_agreement_notes(season, measure))
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
    from association.query.compose.say import note_phrase, period_caveat  # the sayer's words; a call-time import, since compose imports this module

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
    overtime_excluded = Note("definition", {"term": "overtime_excluded"})
    overtime = note(overtime_excluded.kind, note_phrase(overtime_excluded), **overtime_excluded.facts)
    shown = decided("cut", f"{len(ranked)} players qualified; the top {len(leaders)} are shown.", field="limit", chose=len(leaders), before=scope.limit, total=len(ranked))
    notes = [f"{overtime} {shown}"]
    caveat = period_caveat(period_agreement_notes(span.season or current_season(), "points"))
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
