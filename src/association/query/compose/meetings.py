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
from typing import Any, Literal

import duckdb

from association.nba.season import current_season
from association.query import reading
from association.query.coverage import coverage_refusal
from association.query.entities import Entity, resolved_team, slot_season
from association.query.notes import Note
from association.query.player_relation import ResolvedSpan, span_of, validated_until
from association.query.reading import Cuts, Filtered, PointShape, Unsupported
from association.query.result import Grouped, MeetingsFacts, Narrowing, Part, Refusal, Result, Span, Unanswered, Window
from association.query.team_games import TeamNarrowed
from association.query.team_relation import team_games

from .core import rows_of
from .team import TeamQuery, compile_team_over, team_figure


def _head_to_head_names(teams: tuple[str, ...], opponent_slot: str | None) -> list[str]:
    """The team names asked for: the subject's teams (one, or a list of
    two) and the opponent the games are against.

    The parser names two teams meeting as the first team and the games'
    opponent ("Celtics vs Bulls head to head" reads as the Celtics against
    the Bulls): the opponent is one of the two teams. A slot dict that
    listed both, or split them across ``team`` and ``teams``, holds them
    in the typed subject's ``teams`` (:class:`~association.query.reading.Subject`).
    """
    names = list(dict.fromkeys(teams))
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


def _head_to_head_span_slots(scope: Filtered, date: str | None) -> tuple[int | None, int | None, bool]:
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
        return team_games(con, a, span, TeamQuery(cuts=Cuts(venue=venue)), opponent=b, date=date), None
    # No season named means the CURRENT one, as everywhere else. "All time" is
    # a defensible reading here, but silently answering a different span than
    # the rest of the system is the substitution this design exists to prevent.
    # The answer names the season, so another one is a follow-up away.
    season = season_slot or current_season()
    span = ResolvedSpan(season, season_type)
    return team_games(con, a, span, TeamQuery(cuts=Cuts(venue=venue)), opponent=b), season


def _head_to_head_wins(a: Entity, b: Entity, won: list[bool | None], figures: tuple[dict[str, int], dict[str, int]] = ({}, {})) -> Grouped:
    """The meetings as one row per team: each team's wins (a NULL result is
    neither's), and its figure over them where one was asked."""
    return Grouped(by="team", rows=({"key": a.name, "wins": sum(1 for w in won if w), **figures[0]}, {"key": b.name, "wins": sum(1 for w in won if w is False), **figures[1]}))


#: What two teams' meetings say with no figure beside the wins: the series
#: itself, its record.
_MEETINGS_OWN: frozenset[str] = frozenset({"wins", "losses", "record", "games_played"})


def _head_to_head_figure_of(rows: list[dict[str, Any]]) -> dict[str, int]:
    """One team's box-score figure over the meetings it has one for: the
    total, a percentage's attempts, and how many games it covers."""
    counted = [row for row in rows if row["figure"] is not None]
    figure = {"figure": sum(int(row["figure"]) for row in counted), "figure_games": len(counted)}
    if any(row["attempts"] is not None for row in rows):
        figure["attempts"] = sum(int(row["attempts"] or 0) for row in counted)
    return figure


def _head_to_head_figures(
    con: duckdb.DuckDBPyConnection, q: TeamQuery, a: Entity, span: ResolvedSpan, narrowed: TeamNarrowed, rows: list[dict[str, Any]]
) -> tuple[str | None, tuple[dict[str, int], dict[str, int]]]:
    """The figure the question named beside the meetings (ISSUES.md #326:
    "knicks vs celtics total points" answered the series record alone) -
    the catalog key and each team's total over the games: points from the
    meetings' own scores, a box-score figure from each side's box row
    (:func:`~association.query.compose.team.team_figure`, the team's row and
    then the opponent's). ``(None, ...)`` where the measure is the series
    itself or none was named; a figure no game carries raises
    :class:`~association.query.reading.Unsupported` for the caller's
    refusal."""
    measure = q.measure
    if measure is None or measure.key is None or measure.key in _MEETINGS_OWN:
        return None, ({}, {})
    if measure.key in ("points", "point_differential", "plusMinus"):
        games = len(rows)
        return "points", (
            {"figure": sum(int(row["team_score"]) for row in rows), "figure_games": games},
            {"figure": sum(int(row["opponent_score"]) for row in rows), "figure_games": games},
        )
    if team_figure(measure) is None:
        return None, ({}, {})
    sides = []
    for whose in ("own", "opponent"):
        figured = rows_of(con, compile_team_over(replace(q, shape="rows"), a, span, narrowed, ascending=True, figure=replace(measure, whose=whose)))
        sides.append(_head_to_head_figure_of(figured))
    return measure.key, (sides[0], sides[1])


def _head_to_head_unread(q: TeamQuery, a: Entity) -> Refusal | None:
    """A figure no game of a team carries, asked beside two teams' meetings,
    refused by name rather than answered as the series alone."""
    measure = q.measure
    if measure is None or measure.key is None or measure.key in _MEETINGS_OWN:
        return None
    try:
        team_figure(measure)
    except Unsupported:
        return Refusal(kind="team_figure_unread", facts={"team": a.name, "stat": measure.key, "what": "meetings"}, shown={"team": a.name}, under=())
    return None


def read_head_to_head(con: duckdb.DuckDBPyConnection, q: TeamQuery) -> Result | Unanswered | None:
    """``head_to_head``'s point - two teams' meetings - read: the two teams
    from the scope's ``teams``, ``team`` and ``opponent``, every meeting in
    the season (the current one where none is named), on one date, or over a
    since-bounded or whole-career span, from the first team's side. A
    :class:`~association.query.result.Refusal` or :class:`~association.query.result.Clarify` back is the
    relation's own refusal (an ambiguous team, a coverage floor).

    .. versionadded:: 5.0.0
       ``templates.games.head_to_head`` was this, with its sentences.

    .. versionchanged:: 6.0.0
       Takes no ``stated``: the answer side checks the point's cells against
       its shape's row before asking (``compose.plan.cells_unhonored``,
       Phase 3, step 2's closing slice).
    """
    scope = q
    refused = coverage_refusal(PointShape("team_games", "comparison", "opponent"), scope)
    if refused is not None:
        return refused
    names = _head_to_head_names(scope.who.teams, scope.cuts.opponent)
    teams = _head_to_head_teams(con, names, slot_season(scope))
    if isinstance(teams, Unanswered):
        return teams
    a, b = teams
    unread = _head_to_head_unread(q, a)
    if unread is not None:
        return unread
    season_type = scope.span.season_type or 2
    date, venue = scope.cuts.date, scope.cuts.venue
    since, until, career = _head_to_head_span_slots(scope, date)
    window = _head_to_head_window(scope)
    if window is not None and date is None and scope.span.season is None and not career and since is None and until is None:
        # "last 10 home games" with no season named: the newest N meetings on
        # record, as the team log reads "last 10 games vs the Nets" (Phase 5,
        # step 7, the yardstick's F108; the cell was declined since #352).
        career = True
    if since is not None or career or until is not None:
        return _head_to_head_over_span(con, replace(q, shape="rows"), a, b, venue, season_type, since=since, until=until, career=career, window=window)
    narrowed, season = _head_to_head_narrowed(con, a, b, venue, date, scope.span.season, season_type)
    if isinstance(narrowed, Unanswered):
        return narrowed
    span = ResolvedSpan(season, season_type)
    rows = rows_of(con, compile_team_over(replace(q, shape="rows"), a, span, narrowed, ascending=True))
    total = len(rows)
    rows = _head_to_head_kept(rows, window)
    won = [row["won"] for row in rows]
    key, figures = _head_to_head_figures(con, q, a, span, narrowed, rows)
    return Result(
        subject=a.name,
        relation="team",
        span=Span(season=season, season_type=season_type, date=date),
        narrowing=Narrowing(opponent=b.name, venue=venue),
        window=_head_to_head_result_window(window, len(rows)),
        parts=(Part(body=_head_to_head_wins(a, b, won, figures)),),
        facts=MeetingsFacts(games=len(won), figure=key, of=total if window is not None else None),
        notes=_head_to_head_notes(key, figures, len(won)),
    )


def _head_to_head_window(scope: Filtered) -> tuple[int, bool] | None:
    """The window of meetings the words ask for - its count and whether it
    is the newest (``"recent"``, or a bare count) rather than the oldest
    (``"first"``) - or None where the typed window names no count of
    games (a count of seasons is the span's)."""
    window = scope.window
    if window.count is None or window.of != "games":
        return None
    return window.count, window.order != "first"


def _head_to_head_kept(rows: list[dict[str, Any]], window: tuple[int, bool] | None) -> list[dict[str, Any]]:
    """The meetings the window keeps of ``rows`` (read oldest first): the
    newest N or the oldest N, in the same order - a slice of the one read,
    so the tally runs no second statement."""
    if window is None:
        return rows
    count, newest = window
    return rows[-count:] if newest else rows[:count]


def _head_to_head_result_window(window: tuple[int, bool] | None, kept: int) -> Window | None:
    """The window the read applied, for the sayer and the record."""
    if window is None:
        return None
    count, newest = window
    return Window(limit=kept, asked=count, ascending=not newest)


def _head_to_head_notes(key: str | None, figures: tuple[dict[str, int], dict[str, int]], games: int) -> tuple[Note, ...]:
    """The meetings a box-score figure could not be read in - either side's
    box row missing - said beside the totals, which are over the rest."""
    if key is None or key == "points":
        return ()
    blank = games - min(side.get("figure_games", games) for side in figures)
    return (Note("stat_blank", {"games": blank, "stat": key, "whose": "team_log"}),) if 0 < blank < games else ()


def _head_to_head_over_span(
    con: duckdb.DuckDBPyConnection,
    q: TeamQuery,
    a: Entity,
    b: Entity,
    venue: Literal["home", "away"] | None,
    season_type: int,
    *,
    since: int | None,
    until: int | None,
    career: bool,
    window: tuple[int, bool] | None = None,
) -> Result | Unanswered:
    """Every meeting in a since-bounded or whole-career span, tallied once,
    with the seasons the games actually came from (``tg.season``, or
    ``year(tg.eastern_date)`` for a postseason, whose label is not the year
    it was played in before 1994) - the span's own words beside them
    (``Span.phrase``, :meth:`~association.query.player_relation.ResolvedSpan.during`)."""
    span = span_of(reading.Span(career=career, since=since, until=until), "games", season_type=season_type)
    narrowed = team_games(con, a, span, TeamQuery(cuts=Cuts(venue=venue)), opponent=b)
    if isinstance(narrowed, Unanswered):
        return narrowed
    # The team compiler's rows read: each meeting's result and the season it
    # was played in (a postseason's by its calendar year), oldest first.
    read = rows_of(con, compile_team_over(q, a, span, narrowed, ascending=True))
    total = len(read)
    read = _head_to_head_kept(read, window)
    rows = [(row["won"], row["season"]) for row in read]
    key, figures = _head_to_head_figures(con, q, a, span, narrowed, read)
    years = [int(yr) for _, yr in rows]
    first, last = (min(years), max(years)) if years else (None, None)
    return Result(
        subject=a.name,
        relation="team",
        span=Span(season_type=season_type, career=True, first=first, last=last, phrase=span.during(first, last, whose="the seasons on record"), since=since, until=until),
        narrowing=Narrowing(opponent=b.name, venue=venue),
        window=_head_to_head_result_window(window, len(rows)),
        parts=(Part(body=_head_to_head_wins(a, b, [won for won, _ in rows], figures)),),
        facts=MeetingsFacts(games=len(rows), span="career" if career else None, figure=key, of=total if window is not None else None),
        notes=_head_to_head_notes(key, figures, len(rows)),
    )
