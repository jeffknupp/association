"""Two teams' meetings: every game between them in a season, a
since-bounded range or every season on record, tallied once, read into a
:class:`~association.query.result.Result` with a
:class:`~association.query.result.Grouped` body - one row per team, its
wins (``ROADMAP-TYPES.md``: ``head_to_head``, "scalar (wins per team)").
Phase 2's slice (iv): the retired ``templates.games.head_to_head``'s read
moved here whole - the two teams named across ``teams``, ``team`` and
``opponent``, the first team's own rows of the team relation narrowed to
the other (:func:`association.query.team_relation.team_games`), executed
as the team compiler's ``rows`` read (:func:`~association.query.compose.team.compile_team_over`) - and its
sentences to the sayer (:func:`association.query.compose.say.say_head_to_head`).

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import replace
from typing import Literal

import duckdb

from association.nba.season import current_season
from association.query import reading
from association.query.coverage import coverage_refusal
from association.query.entities import Entity, resolved_team, slot_season
from association.query.player_relation import ResolvedSpan, span_of, validated_until
from association.query.reading import Cuts, PointShape, Scope, Unsupported, unhonored_scoping
from association.query.result import Grouped, MeetingsFacts, Narrowing, Part, Result, Span, Unanswered
from association.query.team_games import TeamNarrowed
from association.query.team_relation import team_games

from .core import rows_of
from .team import TeamQuery, compile_team_over


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
        raise Unsupported("head_to_head needs two team names")
    return names


def _head_to_head_teams(con: duckdb.DuckDBPyConnection, names: list[str], season: int | None) -> tuple[Entity, Entity] | Unanswered:
    """Until two DIFFERENT teams resolve, not the first two names: "Celtics" in
    `team` and "Boston Celtics" in `teams` are one team, and the opponent
    after them is the second."""
    resolved: list[Entity] = []
    for name in names:
        team = resolved_team(con, name, season=season)
        if isinstance(team, Unanswered):
            return team
        if team.id not in {t.id for t in resolved}:
            resolved.append(team)
        if len(resolved) == 2:
            break
    if len(resolved) != 2:
        raise Unsupported("the named teams resolved to the same team")
    return resolved[0], resolved[1]


def _head_to_head_span_slots(scope: Scope, date: str | None) -> tuple[int | None, int | None, bool]:
    """The ``since``/``until``/``span`` reading and the conflicts that are
    the meetings' own (a date and a since-bounded or career span at once;
    ``since`` and ``career`` together)."""
    since = scope.span.since or None
    until = validated_until(scope.span.until, since)
    career = scope.span.career
    if (since is not None or career or until is not None) and date:
        raise Unsupported("a date and a since-bounded or career span of meetings at once")
    if since is not None and career:
        raise Unsupported(f"since {since} and a career span of meetings at once")
    # `until` needs no separate career check: `validated_until` above already
    # raises for `until` with no `since`, and `career` never carries a `since`.
    return since, until, career


def _head_to_head_narrowed(
    con: duckdb.DuckDBPyConnection, a: Entity, b: Entity, venue: Literal["home", "away"] | None, date: str | None, season_slot: int | None, season_type: int
) -> tuple[TeamNarrowed | Unanswered, int | None]:
    """``a``'s games against ``b``, from ``a``'s own row of the team relation
    (:func:`association.query.team_relation.team_games`) - which already
    carries both home and away meetings without a venue named - and the
    season the narrowing settled on (``None`` once ``date`` has replaced it).

    A date names its game outright: with a date given, the span is UNBOUNDED
    (``ResolvedSpan(None, season_type)``, whose ``first`` defaults to 0)
    rather than "his career" - a specific date needs no floor at all. A venue
    is always read from ``a``'s side - the team named first - so "Lakers vs
    Mavs ... home games" narrows to the Lakers' home games, not the
    Mavericks'.
    """
    if date:
        span = ResolvedSpan(None, season_type)
        return team_games(con, a, span, Scope(cuts=Cuts(venue=venue)), opponent=b, date=date), None
    # No season named means the CURRENT one, as everywhere else. "All time" is
    # a defensible reading here, but silently answering a different span than
    # the rest of the system is the substitution this design exists to prevent.
    # The answer names the season, so another one is a follow-up away.
    season = season_slot or current_season()
    span = ResolvedSpan(season, season_type)
    return team_games(con, a, span, Scope(cuts=Cuts(venue=venue)), opponent=b), season


def _head_to_head_wins(a: Entity, b: Entity, won: list[bool | None]) -> Grouped:
    """The meetings as one row per team: each team's wins (a NULL result is
    neither's)."""
    return Grouped(by="team", rows=({"key": a.name, "wins": sum(1 for w in won if w)}, {"key": b.name, "wins": sum(1 for w in won if w is False)}))


def read_head_to_head(con: duckdb.DuckDBPyConnection, q: TeamQuery, *, stated: frozenset[str]) -> Result | Unanswered | None:
    """``head_to_head``'s point - two teams' meetings - read: the two teams
    from the scope's ``teams``, ``team`` and ``opponent``, every meeting in
    the season (the current one where none is named), on one date, or over a
    since-bounded or whole-career span, from the first team's side. ``None``
    where the scope carries a narrowing the meetings' words do not state
    (``stated``; the planner declines it first); a
    :class:`~association.query.result.Refusal` or :class:`~association.query.result.Clarify` back is the
    relation's own refusal (an ambiguous team, a coverage floor).

    .. versionadded:: 5.0.0
       ``templates.games.head_to_head`` was this, with its sentences.
    """
    scope = q.scope
    if unhonored_scoping("head_to_head", scope, stated):
        return None
    refused = coverage_refusal(PointShape("team_games", "comparison", "opponent"), scope)
    if refused is not None:
        return refused
    names = _head_to_head_names(scope.teams, scope.team, scope.cuts.opponent)
    teams = _head_to_head_teams(con, names, slot_season(scope))
    if isinstance(teams, Unanswered):
        return teams
    a, b = teams
    season_type = scope.span.season_type or 2
    date, venue = scope.cuts.date, scope.cuts.venue
    since, until, career = _head_to_head_span_slots(scope, date)
    if since is not None or career or until is not None:
        return _head_to_head_over_span(con, replace(q, shape="rows"), a, b, venue, season_type, since=since, until=until, career=career)
    narrowed, season = _head_to_head_narrowed(con, a, b, venue, date, scope.span.season, season_type)
    if isinstance(narrowed, Unanswered):
        return narrowed
    won = [row["won"] for row in rows_of(con, compile_team_over(replace(q, shape="rows"), a, ResolvedSpan(season, season_type), narrowed, ascending=True))]
    return Result(
        subject=a.name,
        relation="team",
        span=Span(season=season, season_type=season_type, date=date),
        narrowing=Narrowing(opponent=b.name, venue=venue),
        parts=(Part(body=_head_to_head_wins(a, b, won)),),
        facts=MeetingsFacts(games=len(won)),
    )


def _head_to_head_over_span(
    con: duckdb.DuckDBPyConnection, q: TeamQuery, a: Entity, b: Entity, venue: Literal["home", "away"] | None, season_type: int, *, since: int | None, until: int | None, career: bool
) -> Result | Unanswered:
    """Every meeting in a since-bounded or whole-career span, tallied once,
    with the seasons the games actually came from (``tg.season``, or
    ``year(tg.eastern_date)`` for a postseason, whose label is not the year
    it was played in before 1994) - the span's own words beside them
    (``Span.phrase``, :meth:`~association.query.player_relation.ResolvedSpan.during`)."""
    span = span_of(reading.Span(career=career, since=since, until=until), "games", season_type=season_type)
    narrowed = team_games(con, a, span, Scope(cuts=Cuts(venue=venue)), opponent=b)
    if isinstance(narrowed, Unanswered):
        return narrowed
    # The team compiler's rows read: each meeting's result and the season it
    # was played in (a postseason's by its calendar year), oldest first.
    rows = [(row["won"], row["season"]) for row in rows_of(con, compile_team_over(q, a, span, narrowed, ascending=True))]
    years = [int(yr) for _, yr in rows]
    first, last = (min(years), max(years)) if years else (None, None)
    return Result(
        subject=a.name,
        relation="team",
        span=Span(season_type=season_type, career=True, first=first, last=last, phrase=span.during(first, last, whose="the seasons on record"), since=since, until=until),
        narrowing=Narrowing(opponent=b.name, venue=venue),
        parts=(Part(body=_head_to_head_wins(a, b, [won for won, _ in rows])),),
        facts=MeetingsFacts(games=len(rows), span="career" if career else None),
    )
