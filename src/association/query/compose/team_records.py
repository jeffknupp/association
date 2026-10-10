"""A team's win-loss record: for a season, at home or on the road, against
one team, in a postseason, since a season, in game N of each playoff
series, in one calendar month or on a weekday or holiday, broken out by
month, both season types together, or across every season the warehouse
holds - ``team_record``'s point, read into a
:class:`~association.query.result.Result`. Phase 2's slice (iv): the
retired template (``templates.teams.team_record``) and everything it
reached moved here and into :mod:`~association.query.compose.standings`,
its statements into the relations, its sentences to the sayer
(:func:`association.query.compose.say.say_team_record`,
:func:`~association.query.compose.say.say_team_record_by_month`).

A plain regular season's record (and its home/road split, and a career of
them) is ESPN's standings' (:mod:`~association.query.compose.standings`).
A record against one team, in a postseason, since a season, in game N of
each series, in a calendar narrowing or broken out by month has no
standings column, and is tallied from the team-games relation instead -
the team compiler's ``rows`` read
(:func:`~association.query.compose.team.compile_team_over`) over the
record's own narrowing, which excludes the NBA Cup final from a regular
season (``team_metrics.games_scope``). The by-month table groups those
same rows by month rather than running the compiler's ``grouped`` read by
``month_of_year``: that read joins ``team_box_stats`` and drops a game with
no team box row, which a record counts.

Every answer names the span it covers, because "all-time" here is not the
franchise's history: standings begin with 1987-88, and the game list holds
every regular-season game only from 1993-94.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import duckdb

from association.nba.coverage import unavailable
from association.nba.season import current_season
from association.query import reading
from association.query.calendar import CalendarNarrowing, bare_month
from association.query.conditions import _season_month_order
from association.query.coverage import coverage_refusal, floor_refusal
from association.query.entities import Entity, resolved_team, slot_season
from association.query.notes import Note
from association.query.player_relation import ResolvedSpan, span_of, validated_until
from association.query.reading import PointShape, Situation, Unsupported
from association.query.result import Calendar, Cell, GameOfSeries, Grouped, Narrowing, Part, Refusal, Result, Rows, Scalar, Span, TeamRecordFacts, Unanswered
from association.query.season_line import Statement
from association.query.season_text import MONTH_NAMES
from association.query.team_games import TeamNarrowed, game_list_gaps_sql, season_game_counts_sql
from association.query.team_metrics import FIRST_FULL_REGULAR_SEASON, games_scope
from association.query.team_relation import team_span_clause

from .core import rows_of, values_of
from .standings import read_standings_career, read_standings_season
from .team import TeamQuery, compile_team_over


def _team_record_month_and_split(split: str | None, situation: Situation | None, limit: int | None) -> tuple[str | None, int | None, CalendarNarrowing | None]:
    """The validated ``split``, the bare calendar month a question narrows
    to, and the fuller calendar narrowing (a weekday, a fixed holiday,
    "since <month day>") ``situation`` names when it is not a bare month -
    or the refusal for a ``split`` that is not "month", a ``situation``
    naming no calendar narrowing (an alignment is one here: the standings
    hold no opponent), a non-month narrowing beside a month split, or a
    bare ``limit`` with none of the three ("last 10 games" is a game log's
    question: standings hold only the full season)."""
    if split is not None and split != "month":
        raise Unsupported(f"no split named {split!r}")
    month = bare_month(situation.text if situation is not None else None)
    narrowing = None
    if situation is not None and month is None:
        narrowing = situation.calendar
        if narrowing is None:
            raise Unsupported(f'no calendar narrowing in situation {situation.text!r} - a weekday, a month, a holiday or "since <day>" is read; an age, a conference or a division is not')
        if split == "month":
            raise Unsupported("a month split already covers every month; narrowing it further to one weekday or holiday is not built")
    if limit and split is None and month is None and narrowing is None:
        # Measured over the corpus, a month narrowing or a by-month split
        # never carries a real limit of its own (the router fills a default),
        # so only a bare limit is read as "last N games".
        raise Unsupported("a record over a limited set of games is a game_log question")
    return split, month, narrowing


def _team_record_since(since: int | None, career: bool, season: int | None) -> int | None:
    """The validated ``since``, and the two conflicts that are the record's
    own: ``since`` and a single named season, ``since`` and "career"."""
    if not since:
        return None
    if season is not None:
        raise Unsupported(f"since {since} and the {season} season at once")
    if career:
        raise Unsupported(f"since {since} and a career span at once")
    return since


def _team_record_teams(con: duckdb.DuckDBPyConnection, scope: Any) -> tuple[Entity, Entity | None] | Unanswered:
    """The team a record is for and the opponent it is against, if any - or
    the clarifying question one of the names needs. "celtics vs bulls
    record" can land both teams in ``teams``; the first is the subject."""
    team_text, listed = scope.subject.teams[0] if scope.subject.teams else None, list(scope.subject.teams[1:])
    team = resolved_team(con, team_text, season=slot_season(scope))
    if isinstance(team, Unanswered):
        return team
    opponent_text = scope.cuts.opponent
    if opponent_text and opponent_text.strip():
        found = resolved_team(con, opponent_text, season=slot_season(scope))
        if isinstance(found, Unanswered):
            return found
        if found.id == team.id:
            raise Unsupported("team_record's opponent must differ from the team")
        return team, found
    for text in listed:
        found = resolved_team(con, text, season=slot_season(scope))
        if isinstance(found, Unanswered):
            return found
        if found.id != team.id:
            return team, found
    return team, None


def read_team_record(con: duckdb.DuckDBPyConnection, q: TeamQuery) -> Result | Unanswered | None:
    """``team_record``'s point read: which of its shapes the settled slots
    pick out - a standings season or career, a tally of the team's games, a
    table by month, or both season types together - with the refusals that
    are the record's own. A
    :class:`~association.query.result.Refusal` or :class:`~association.query.result.Clarify` back is a
    refusal (a conference named as a team, a season under the game list's
    floor, the relation's own).

    .. versionadded:: 5.0.0
       ``templates.teams.team_record`` was this, with its sentences.

    .. versionchanged:: 6.0.0
       Takes no ``stated``: the answer side checks the point's cells against
       its shape's row before asking (``compose.plan.cells_unhonored``,
       Phase 3, step 2's closing slice).
    """
    from association.query.calendar import conference_named

    scope = q.scope
    refused = coverage_refusal(PointShape("team_games", "scalar", "record"), scope)
    if refused is not None:
        return refused
    named = conference_named(scope)
    if named is not None:
        return Refusal(kind="conference_named", facts={"named": named}, shown={"unanswerable": named})
    split, month, calendar = _team_record_month_and_split(scope.split, scope.cuts.situation, scope.window.count)
    teams = _team_record_teams(con, scope)
    if isinstance(teams, Unanswered):
        return teams
    team, opponent = teams
    season_type = scope.span.season_type or 2
    career = scope.span.career
    season = scope.span.season
    if career and season is not None:
        # "all-time ... in 2020" is either a slip or a range this cannot read.
        raise Unsupported("a career span and a single season at once")
    since = _team_record_since(scope.span.since, career, season)
    asked = _Asked(team=team, opponent=opponent, month=month, venue=scope.cuts.venue, career=career, season=season, since=since, until=validated_until(scope.span.until, since), calendar=calendar)
    if scope.span.both:
        # "including the playoffs": checked before the game_n/season_type
        # conflict below, which assumes one named type.
        if scope.cuts.game_n:
            raise Unsupported("a game of a playoff series needs one named season type, not both combined")
        if split == "month":
            raise Unsupported("a month split has no combined-season-type form yet")
        return _combined(con, q, asked)
    if scope.cuts.game_n and season_type != 3:
        raise Unsupported(f"game {scope.cuts.game_n} names a game of a playoff series, and this is a regular season question")
    return _route(con, q, replace(asked, game_n=scope.cuts.game_n, split=split), season_type)


@dataclass(frozen=True, kw_only=True)
class _Asked:
    """What a record question settled, beside its season type - carried
    between the route's steps."""

    team: Entity
    opponent: Entity | None
    month: int | None
    venue: str | None
    career: bool
    season: int | None
    since: int | None
    until: int | None
    calendar: CalendarNarrowing | None
    game_n: int | None = None
    split: str | None = None


def _route(con: duckdb.DuckDBPyConnection, q: TeamQuery, asked: _Asked, season_type: int) -> Result | Unanswered:
    """Which shape the settled slots pick out: a table by month, a tally
    since a season, the standings, or a tally of one season or every one."""
    if asked.split == "month":
        if asked.game_n:
            raise Unsupported("a month split has no one-game-of-a-series form yet")
        if asked.since is not None:
            return _by_month_span(con, q, asked, season_type)
        return _by_month(con, q, asked, None if asked.career else (asked.season or current_season()), season_type)
    if asked.since is not None:
        return _games_record(con, q, asked, None, season_type)
    if asked.opponent is None and season_type == 2 and asked.month is None and not asked.game_n and asked.calendar is None:
        if asked.career:
            return read_standings_career(con, asked.team, asked.venue)
        return read_standings_season(con, asked.team, asked.season or current_season(), asked.venue)
    return _games_record(con, q, asked, None if asked.career else (asked.season or current_season()), season_type)


def _record_narrowed(team: Entity, season: int | None, season_type: int, *, since: int | None = None, until: int | None = None) -> tuple[TeamNarrowed, ResolvedSpan]:
    """``team``'s games for a win-loss RECORD tally: ``games_scope``'s clause
    (which excludes the NBA Cup final from a regular season) plus the team,
    or a since-bounded span's own clause - and the span it covers."""
    if since is not None:
        span = span_of(reading.Span(since=since, until=until), "games", season_type=season_type)
        clause, params = team_span_clause(span)
        base = ["tg.team_id = ?", "tg.season_type = ?", clause]
        if season_type == 2:
            base.append("NOT tg.cup_final")
        return TeamNarrowed(base=base, base_params=[team.id, season_type, *params], team=team), span
    scope, params = games_scope(season_type, season)
    return TeamNarrowed(base=["tg.team_id = ?", scope], base_params=[team.id, *params], team=team), ResolvedSpan(season, season_type)


def _record_games(con: duckdb.DuckDBPyConnection, q: TeamQuery, asked: _Asked, season: int | None, season_type: int, *, narrowed_by: bool = True) -> tuple[list[dict[str, Any]], TeamNarrowed]:
    """The team's games in scope, oldest first, against the opponent, in the
    month or calendar narrowing and in game N of each series where named
    (``narrowed_by``; a table by month reads the opponent alone) - and the
    narrowing they were read through, whose phrase names game N and a
    calendar narrowing."""
    narrowed, span = _record_narrowed(asked.team, season, season_type, since=asked.since if narrowed_by else None, until=asked.until if narrowed_by else None)
    if asked.opponent is not None:
        narrowed.narrow("tg.opponent_id = ?", asked.opponent.id)
        # Read by TeamNarrowed.filters()' series-game phrase ("of THE series").
        narrowed.opponent = asked.opponent
    if narrowed_by and asked.month is not None:
        narrowed.narrow("month(tg.eastern_date) = ?", asked.month)
    if narrowed_by and asked.calendar is not None:
        narrowed.narrow_calendar(asked.calendar)
    if narrowed_by and asked.game_n:
        if season_type != 3:
            raise Unsupported(f"game {asked.game_n} names a game of a playoff series, and this is a regular-season question")
        narrowed.narrow_series_game(asked.game_n)
    rows = rows_of(con, compile_team_over(replace(q, shape="rows"), asked.team, span, narrowed, ascending=True))
    games = [_record_game(r) for r in rows]
    for game, row in zip(games, rows, strict=True):
        game["season"] = row["season"]
    return games, narrowed


def _record_game(row: dict[str, Any]) -> dict[str, Any]:
    """One row of the rows read as a record shows the game: a neutral-site
    game is on neither side."""
    venue = "neutral" if row["neutral"] else row["side"]
    return {"date": str(row["day"]), "venue": venue, "team_score": row["team_score"], "opponent_score": row["opponent_score"], "won": bool(row["won"]), "opponent": row["opponent"]}


def _unseasoned(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The games as a record shows them, without the season each was read with."""
    return [{key: value for key, value in g.items() if key != "season"} for g in games]


def _no_games_cause(con: duckdb.DuckDBPyConnection, team: Entity, opponent: Entity | None, season: int | None, season_type: int) -> str | None:
    """Why a tally of one season found nothing: the warehouse holds no games
    that season for anyone (``"season"``), the two teams did not meet
    (``"not_met"``), or the team played none (``"none_played"``); None for a
    span of seasons, which names its narrowing instead."""
    if season is None:
        return None
    scope, params = games_scope(season_type, season)
    counts = values_of(con, Statement(*season_game_counts_sql(scope, params, team.id)))
    league, own = counts[0] if counts else (0, 0)
    if not league:
        return "season"
    return "not_met" if opponent is not None and own else "none_played"


def _game_list_gaps(con: duckdb.DuckDBPyConnection, team: Entity, season_type: int, season: int | None, *, since: int | None, until: int | None, partial: bool = False) -> list[Note]:
    """Seasons where the games tallied for a team do not number its games in
    ``team_season_stats`` - measured: the 2000 and 2001 postseasons hold 15
    of the Lakers' 23 and 10 of their 16 games. Only seasons from 1994,
    where the totals exist, can be checked. ``partial`` says the tally
    covers some of each season's games, so the missing ones may fall
    outside it (ISSUES.md #298)."""
    narrowed, _span = _record_narrowed(team, season, season_type, since=since, until=until)
    floor = max(FIRST_FULL_REGULAR_SEASON, since) if since is not None else FIRST_FULL_REGULAR_SEASON
    rows = values_of(con, Statement(*game_list_gaps_sql(narrowed, team.id, season_type, season=season, since=since, until=until, floor=floor)))
    if not rows:
        return []
    kind = "postseason" if season_type == 3 else "regular-season"
    seasons = [{"season": int(s), "listed": int(listed), "played": int(played)} for s, listed, played in rows]
    return [Note("game_list_disagrees", {"team": team.name, "what": kind, "narrowed": partial, "seasons": seasons})]


def _span_floor(season: int | None, since: int | None, season_type: int) -> list[Note]:
    """The floor a tally over every season names: the game list's first
    postseason, or the first regular season it holds every game of."""
    if season is not None or since is not None:
        return []
    if season_type == 3:
        return [Note("floor", {"table": "games", "first": 1989, "what": "postseason"})]
    return [Note("floor", {"table": "games", "first": FIRST_FULL_REGULAR_SEASON, "what": "regular season"})]


def _cup_finals(con: duckdb.DuckDBPyConnection, q: TeamQuery, asked: _Asked, season: int | None) -> list[dict[str, Any]]:
    """Any NBA Cup final the two teams met in: a regular-season game that
    counts in no standings, so not in the record (``games_scope`` excludes
    it) - but a meeting, which left out without a word would read as a
    missing game. Narrowed by hand, since it asks for exactly the game the
    record's clause excludes."""
    assert asked.opponent is not None
    narrowed = TeamNarrowed(base=["tg.team_id = ?", "tg.season_type = 2", "tg.cup_final"], base_params=[asked.team.id], team=asked.team)
    narrowed.narrow("tg.opponent_id = ?", asked.opponent.id)
    if season is not None:
        narrowed.narrow("tg.season = ?", season)
    elif asked.since is not None:
        narrowed.narrow("tg.season >= ?", asked.since)
        if asked.until is not None:
            narrowed.narrow("tg.season <= ?", asked.until)
    rows = rows_of(con, compile_team_over(replace(q, shape="rows"), asked.team, ResolvedSpan(season, 2), narrowed, ascending=True))
    return [{"date": str(r["day"]), "won": bool(r["won"]), "team_score": r["team_score"], "opponent_score": r["opponent_score"]} for r in rows]


def _games_record(con: duckdb.DuckDBPyConnection, q: TeamQuery, asked: _Asked, season: int | None, season_type: int) -> Result | Unanswered:
    """A record tallied from the game list: against one team, or in a
    postseason, for one season, since a season, or every season it holds,
    optionally in one month or calendar narrowing or in game N of each
    series - with its home/away split, a season's meetings with one team,
    any NBA Cup final they met in, and the seasons the game list and the
    team's own totals disagree on."""
    if season is not None:
        # The game list's floor is checked where the table is read: a record
        # against a team named only in `teams` reaches here under the
        # standings' floor instead.
        refused = floor_refusal(("games",), season, season_type, shown={"team": asked.team.name, "season": season})
        if refused is not None:
            return refused
    games, narrowed = _record_games(con, q, asked, season, season_type)
    seasons = [g["season"] for g in games]
    # One named season is trivially itself; a span's are the seasons its
    # games came from (a postseason's by the calendar year it was played in).
    first, last = (season, season) if season is not None else ((min(seasons), max(seasons)) if seasons else (None, None))
    shown = [g for g in _unseasoned(games) if asked.venue is None or g["venue"] == asked.venue]
    wins = sum(1 for g in shown if g["won"])
    parts = [Part(body=Scalar(games=len(shown), values={"wins": wins, "losses": len(shown) - wins}, how="record")), Part(role="detail", body=Rows(rows=tuple(_unseasoned(games))))]
    notes: list[Note] = []
    facts = TeamRecordFacts()
    if games:
        notes += _games_record_remarks(con, q, asked, season, season_type, games, shown, parts)
    else:
        facts = TeamRecordFacts(none=_no_games_cause(con, asked.team, asked.opponent, season, season_type))
    return Result(
        subject=asked.team.name,
        relation="team",
        span=Span(season=season, season_type=season_type, career=season is None, first=first, last=last, since=asked.since, until=asked.until),
        narrowing=Narrowing(phrase=narrowed.filters(opponent=False), opponent=asked.opponent.name if asked.opponent else None, venue=asked.venue, cells=_record_cells(asked)),
        parts=tuple(parts),
        notes=tuple(notes),
        facts=facts,
    )


def _record_cells(asked: _Asked) -> tuple[Cell, ...]:
    """A tally's calendar cut (a month, or a situation as the reading named
    it) and the game of a series, as the Result's cells."""
    cells: list[Cell] = []
    if asked.month is not None or asked.calendar is not None:
        cells.append(Calendar(month=asked.month, situation=asked.calendar.label if asked.calendar else None))
    if asked.game_n:
        cells.append(GameOfSeries(n=asked.game_n))
    return tuple(cells)


def _games_record_remarks(
    con: duckdb.DuckDBPyConnection, q: TeamQuery, asked: _Asked, season: int | None, season_type: int, games: list[dict[str, Any]], shown: list[dict[str, Any]], parts: list[Part]
) -> list[Note]:
    """A tally's remarks, in the order its answer says them - where the game
    list starts, the neutral-site games a home or road tally leaves out, the
    seasons the list and the team's totals disagree on - and the NBA Cup
    finals a regular season against one team adds as a part."""
    notes = _span_floor(season, asked.since, season_type)
    if asked.venue is not None and len(shown) < len(games):
        neutral = sum(1 for g in games if g["venue"] == "neutral")
        if neutral:
            # Why the home or road tally leaves games out.
            notes.append(Note("definition", {"term": "neutral_site", "games": neutral}))
    if asked.opponent is not None and season_type == 2:
        parts.append(Part(role="detail", body=Rows(rows=tuple(_cup_finals(con, q, asked, season)))))
    # A tally narrowed to some of a season's games (an opponent, a venue, a
    # month, a weekday, a game of a series) may or may not hold the games
    # the list is short by; the note says which claim it can make.
    partial = any(x is not None for x in (asked.opponent, asked.venue, asked.month, asked.calendar, asked.game_n))
    return notes + _game_list_gaps(con, asked.team, season_type, season, since=asked.since, until=asked.until, partial=partial)


def _month_rows(games: list[dict[str, Any]], season: int | None) -> list[dict[str, Any]]:
    """One row per calendar month the games fall in, in season order: its
    games and record, under ``season`` (None for a career table)."""
    by_month: dict[int, list[dict[str, Any]]] = {}
    for g in games:
        by_month.setdefault(int(g["date"][5:7]), []).append(g)
    rows = []
    for month in sorted(by_month, key=_season_month_order):
        entries = by_month[month]
        wins = sum(1 for g in entries if g["won"])
        rows.append({"season": season, "month": MONTH_NAMES[month - 1], "games": len(entries), "wins": wins, "losses": len(entries) - wins})
    return rows


def _by_month(con: duckdb.DuckDBPyConnection, q: TeamQuery, asked: _Asked, season: int | None, season_type: int) -> Result | Unanswered:
    """The team's record broken out by calendar month, for one season or
    every season it holds: the standings have no game-level date to group a
    month from, so the game list is tallied, as for a single month."""
    if season is not None:
        refused = floor_refusal(("games",), season, season_type, shown={"team": asked.team.name, "season": season})
        if refused is not None:
            return refused
    games, _narrowed = _record_games(con, q, asked, season, season_type, narrowed_by=False)
    shown = [g for g in games if asked.venue is None or g["venue"] == asked.venue]
    facts = TeamRecordFacts(none=None if shown else _no_games_cause(con, asked.team, asked.opponent, season, season_type))
    return Result(
        subject=asked.team.name,
        relation="team",
        span=Span(season=season, season_type=season_type, career=season is None),
        narrowing=Narrowing(opponent=asked.opponent.name if asked.opponent else None, venue=asked.venue),
        parts=(Part(body=Grouped(by="month", rows=tuple(_month_rows(shown, season)))),),
        notes=tuple(_span_floor(season, None, season_type)) if shown else (),
        facts=facts,
    )


def _by_month_span(con: duckdb.DuckDBPyConnection, q: TeamQuery, asked: _Asked, season_type: int) -> Result:
    """A by-month record over a since/until-bounded span - "Knicks record by
    month 2024 2025" - as one table per season: merged, a calendar month
    would double-count across the years. A season the game list cannot
    reach is skipped, not refused whole."""
    since = asked.since
    assert since is not None
    last = asked.until if asked.until is not None else current_season()
    rows: list[dict[str, Any]] = []
    skipped: list[int] = []
    for season in range(since, last + 1):
        if unavailable(("games",), season, season_type) is not None:
            skipped.append(season)
            continue
        games, _narrowed = _record_games(con, q, replace(asked, since=None, until=None), season, season_type, narrowed_by=False)
        rows += _month_rows([g for g in games if asked.venue is None or g["venue"] == asked.venue], season)
    # The seasons the game list cannot reach are said, not dropped (ISSUES.md
    # #300: "knicks record by month since 1990" began at 1994 in silence).
    kept = [season for season in range(since, last + 1) if season not in skipped]
    notes = [Note("floor", {"table": "games", "first": kept[0], "earliest": since, "what": "postseason" if season_type == 3 else "regular season"})] if skipped and kept else []
    return Result(
        subject=asked.team.name,
        relation="team",
        span=Span(season_type=season_type, career=True, since=since, until=asked.until),
        narrowing=Narrowing(opponent=asked.opponent.name if asked.opponent else None, venue=asked.venue),
        parts=(Part(body=Grouped(by="month", rows=tuple(rows))),),
        notes=tuple(notes),
        facts=TeamRecordFacts(),
    )


def _half(read: Result | Unanswered) -> tuple[int, int, Any, tuple[Note, ...], str]:
    """One season type's half of a combined record: its wins, losses, first
    season and remarks, and where its neutral-site games are placed - a
    half that was a refusal or a question is no record at all. (Until the
    refusals were typed, a refusal half's worded answer was searched for
    wins and a "Note:" tail; none of the refusals a half can come back
    with carries either.)"""
    if isinstance(read, Unanswered):
        return 0, 0, None, (), ""
    line = read.scalar
    values = line.values if line is not None else {}
    first = read.span.first if read.span.career else (read.span.season if read.span.source == "games" else None)
    # Where the half's own answer places its neutral-site games.
    placed = "tally" if read.span.source == "games" else ("career" if read.span.career else "season")
    return int(values.get("wins") or 0), int(values.get("losses") or 0), first, read.notes, placed


def _without_neutral_site(notes: tuple[Note, ...]) -> tuple[Note, ...]:
    """``notes`` less a neutral-site definition - the one remark a half
    writes about a home/road split."""
    return tuple(each for each in notes if not (each.kind == "definition" and each.facts.get("term") == "neutral_site"))


def _combined(con: duckdb.DuckDBPyConnection, q: TeamQuery, asked: _Asked) -> Result:
    """Both season types together - "including the playoffs" - each read
    through the record's own single-type route, so from exactly the source a
    single-type question about the same span reads, and summed: the
    combined total AND each type's own record and first season, never one
    type alone."""
    halves = [_half(_route(con, q, asked, season_type)) for season_type in (2, 3)]
    (r_wins, r_losses, r_first, r_notes, r_placed), (p_wins, p_losses, p_first, p_notes, p_placed) = halves
    if asked.venue is None:
        # A half's neutral-site count explains a home/road figure the half's
        # own answer shows beside it; the combined answer shows none, so the
        # note would be about a split the reader cannot see (ISSUES.md #336).
        r_notes, p_notes = _without_neutral_site(r_notes), _without_neutral_site(p_notes)
    rows = (
        {"key": 2, "wins": r_wins, "losses": r_losses, "first_season": r_first, "notes": len(r_notes), "placed": r_placed},
        {"key": 3, "wins": p_wins, "losses": p_losses, "first_season": p_first, "notes": len(p_notes), "placed": p_placed},
    )
    total = Scalar(games=r_wins + r_losses + p_wins + p_losses, values={"wins": r_wins + p_wins, "losses": r_losses + p_losses}, how="record")
    return Result(
        subject=asked.team.name,
        relation="team",
        span=Span(season=asked.season, career=asked.season is None),
        narrowing=Narrowing(opponent=asked.opponent.name if asked.opponent else None, venue=asked.venue),
        parts=(Part(body=total), Part(role="detail", body=Grouped(by="season_type", rows=rows))),
        notes=(*r_notes, *p_notes),
    )
