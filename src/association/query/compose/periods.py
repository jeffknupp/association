"""The period reader: a named player's figure in one quarter or half, game by
game, or his four quarters side by side, read into a
:class:`~association.query.result.Result` - ``PERIOD_LOG``'s own point -
from the compiled statement, worded by the sayer
(:mod:`association.query.compose.say`).

Two shapes, two statements, both the compiler's:

- **One quarter or half** - the point compiled as a ``rows`` read of every
  game in the span in date order, the period's whole line
  (:data:`~association.query.player_games.PERIOD_COLUMNS`) and the
  opponent's name as its measures: the relation applies the period
  (``player_games._period_source``), so every figure is the period's own;
  the ``opponent_name`` measure names the team where the compiler's fixed
  columns carry its abbreviation.
- **The four quarters** - the planned ``grouped`` read by ``period``
  (:func:`~association.query.compose.core._compile_by_period`).

The refusals come in order: a season whose per-period
figures cannot be trusted is refused before any name is resolved
(:func:`~association.query.player_games.period_distrust`), and again off
the game a date names once it is found.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import duckdb

from association.nba.coverage import POSTSEASON
from association.nba.season import current_season, eastern_date
from association.query import reading
from association.query.conditions import box_source
from association.query.coverage import coverage_refusal
from association.query.entities import Entity, resolved_team, slot_season
from association.query.lines import measure_filters
from association.query.measure import keyed, spelled, spelled_whose
from association.query.measures import PERIOD_RATE_STATS, period_split_measure, resolve_metric
from association.query.metrics import PER_GAME_MIN_GAMES, PER_GAME_MIN_POSTSEASON_GAMES
from association.query.notes import Note
from association.query.player_games import (
    BOTH_SEASON_TYPES,
    PERIOD_COLUMNS,
    PERIOD_LOG_COLUMNS,
    PERIOD_PLAYS_COLUMNS,
    PERIOD_RATES,
    PERIOD_REFUSE_BELOW,
    REGULATION_QUARTERS,
    Narrowed,
    most_games,
    period_agreement_across,
    period_agreement_notes,
    period_columns,
    period_distrust,
    period_quarter,
    period_ranking,
    period_rate,
)
from association.query.player_relation import ResolvedSpan, league_games, relation_window, scoped_games, span_of, without_notes
from association.query.reading import DEFAULT_GAME_LOG_LIMIT, STARTER_SIDES, Cuts, Filtered, Measure, PointShape, Unsupported, _clamp_limit, period_narrowing
from association.query.result import (
    Cell,
    Decided,
    GameOfSeries,
    Grouped,
    Line,
    Narrowing,
    Part,
    Period,
    PeriodFacts,
    PeriodRankingFacts,
    Refusal,
    Result,
    Role,
    Rows,
    Scalar,
    Span,
    TeamPeriodFacts,
    Unanswered,
    Window,
)
from association.query.season_text import season_phrase
from association.query.team_games import TEAM_PERIOD_AGREEMENT, TEAM_PERIOD_COLUMNS, TeamNarrowed, period_games
from association.query.team_relation import scoped_team, team_games

from .core import Compiled, Query, compile_over, compile_query, rows_of
from .team import TeamQuery

#: What the per-period log reads beside the fixed columns: the period's whole
#: line, the opponent as the game's own season named it, and the game's
#: season type, which a read over both types counts each of.
_PERIOD_LOG_MEASURES: list[str] = [*PERIOD_COLUMNS, "opponent_name", "season_type"]


def read_period_split(con: duckdb.DuckDBPyConnection, q: Query) -> Result | Unanswered | None:
    """``PERIOD_LOG``'s own point - a named player's games read as one
    quarter's or half's line, or his four quarters side by side - read into
    a Result over the compiled statement. ``None`` where the point is not
    that (a predicate, another skeleton, a measure the point did not plan),
    and the compiler's own sentence answers; a
    :class:`~association.query.result.Refusal` or :class:`~association.query.result.Clarify` back is a
    refusal - a season whose figures cannot be trusted, a column this
    warehouse cannot rebuild, the relation's own.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Takes no ``stated``: the answer side checks the point's cells against
       its shape's row before asking (``compose.plan.cells_unhonored``,
       Phase 3, step 2's closing slice).
    """
    if q.subject != "player" or q.predicates:
        return None
    if q.skeleton == "grouped" and q.group == "period":
        return _period_by_quarter(con, q) if q.aggregate == "per_game" else None
    if q.skeleton != "rows" or q.order != "date" or q.group != "none":
        return None
    return _period_log(con, q)


def _period_untrusted(season: int, measure: str) -> Unanswered | None:
    """The refusal for a season whose per-period ``measure`` cannot be
    trusted, or None."""
    distrust = period_distrust(season, measure)
    return Refusal(kind="period_untrusted", facts=distrust, shown={"season": distrust["season"]}) if distrust is not None else None


def _period_where(scope: Filtered, narrowed: Narrowed) -> tuple[tuple[Cell, ...], tuple[str, ...]]:
    """What the answer says it narrowed to, after the player and the period,
    as cells - the starter/bench half (only a NAMED half filters), the lines
    on a box-score column, the game of a series - and the relation's
    phrases for a teammate's role and a calendar or conference narrowing
    (the venue and the teammates absent are the Narrowing's)."""
    situation = narrowed._situation_phrase()
    started = STARTER_SIDES.get(scope.split) if scope.split is not None else None
    cells: list[Cell] = [Role(started=started)] if started is not None else []
    cells += [Line(column=column, op=op, value=value, label=label) for column, op, value, label in narrowed.lines]
    if narrowed.series_game is not None:
        cells.append(GameOfSeries(n=narrowed.series_game))
    # A teammate's role and a calendar or a conference, in the relation's
    # own words (``Narrowed.filters``): both narrow which games count,
    # and the period sentence did not say them (ISSUES.md #301) - "jokic
    # first quarter points in january" answered one January game as
    # though it were his season.
    also = [c.phrase() for c in narrowed.conditions if not (c.side == "own" and c.predicate == "absent")] + ([situation] if situation else [])
    if narrowed.team is not None:
        # His own team the games were cut to ("jrue holiday 1st half with
        # celtics vs knicks"), in the relation's words: unsaid, the figure
        # read as every game of his against the Knicks.
        also.insert(0, f"with the {narrowed.team.name}")
    return tuple(cells), tuple(also)


def _period_narrowing(narrowed: Narrowed, venue: str | None, cells: tuple[Cell, ...]) -> Narrowing:
    return Narrowing(opponent=narrowed.opponent.name if narrowed.opponent else None, venue=venue, without=tuple(mate.name for mate in narrowed.without), cells=cells)


# --- one quarter or half, game by game ----------------------------------------------------


def _period_log(con: duckdb.DuckDBPyConnection, q: Query) -> Result | Unanswered | None:
    """A named player's figure in ONE quarter or half, per game and over
    them all - read over every game of the span (the heading totals and
    averages them; the log beneath shows the newest N), in date order."""
    scope = q
    asked = period_narrowing(scope)
    if asked is None:
        period = scope.period
        quarter = period.number if period is not None and not period.half else None
        half = period.number if period is not None and period.half else None
        raise Unsupported(f"a player's quarter or half needs a period 1-10 or a half 1-2, got period={quarter!r} half={half!r}")
    measure = period_split_measure(scope.measure)
    if q.measures != [measure]:
        return None
    across = _period_across_seasons(scope)
    if scope.cuts.date is None and not across:
        refused = _period_untrusted(scope.span.season or current_season(), measure)
        if refused is not None:
            return refused
    read = replace(q, measures=_PERIOD_LOG_MEASURES, limit=None, offset=0, direction="asc")
    asked_games = _period_games_asked(scope)
    # A count of games the words asked ("in his last 4 games") is the games
    # the figure is over, cut by the compiler's own limit from the end the
    # words named and put back in date order; read over the whole span, it
    # answered all 64 of Cade Cunningham's 2026 games under a log of the
    # last 4 (ISSUES.md #473). With no count asked the figure is the span's
    # and the log beneath it the newest few.
    compiled = compile_query(con, read if asked_games is None else replace(read, limit=asked_games[1], direction="desc" if asked_games[0] == "recent" else "asc"))
    if compiled.player is None:
        return None
    settled = _period_log_rows(scope, sorted(rows_of(con, compiled), key=lambda row: row["day"]), measure, across=across)
    if isinstance(settled, Unanswered):
        return settled
    rows, season, left_out = settled
    where = _period_where(scope, compiled.narrowed)
    if not rows:
        redirected = _period_redirect(con, read, compiled, asked[1], measure)
        if redirected is not None:
            return redirected
    return _period_log_result(con, compiled, scope, rows, season, asked[1], measure, where, cut=asked_games is not None, left_out=left_out)


def _period_log_rows(scope: Filtered, rows: list[dict[str, Any]], measure: str, *, across: bool) -> tuple[list[dict[str, Any]], int, list[Note]] | Unanswered:
    """The games a period log reads, the season it is labeled and caveated
    by where it is one, and the note on the games left out of a read across
    seasons - or the refusal of a season that cannot be trusted."""
    season = scope.span.season or current_season()
    if scope.cuts.date is not None and rows:
        # The season a date's game actually falls in, read off the row itself
        # rather than the slot: an explicit year in the question ("... on
        # november 11 2019") can name a date the season slot disagrees with.
        season = int(rows[0]["season"])
        refused = _period_untrusted(season, measure)
        return refused if refused is not None else (rows, season, [])
    if across:
        trusted = _period_trusted(rows, measure)
        if isinstance(trusted, Unanswered):
            return trusted
        return trusted[0], season, trusted[1]
    return rows, season, []


def _period_across_seasons(scope: Filtered) -> bool:
    """Whether the read covers more than one season - a career or a range
    (decisions D13, D14: "kyrie Irving second half stats vs denver" is his
    career against Denver) - so that each season it touches is caveated, or
    left out where it cannot be trusted, rather than one season refusing or
    labeling the whole."""
    return scope.cuts.date is None and (scope.span.career or scope.span.since is not None)


def _period_trusted(rows: list[dict[str, Any]], measure: str) -> tuple[list[dict[str, Any]], list[Note]] | Unanswered:
    """A read across seasons, less the games of any season whose per-period
    ``measure`` cannot be trusted (:func:`~association.query.player_games.period_distrust`),
    with the note saying how many were left out and from which seasons - or
    the refusal naming the first such season, where every game is in one."""
    distrust = {season: facts for season in sorted({int(row["season"]) for row in rows}) if (facts := period_distrust(season, measure)) is not None}
    if not distrust:
        return rows, []
    kept = [row for row in rows if int(row["season"]) not in distrust]
    if not kept:
        first = distrust[min(distrust)]
        return Refusal(kind="period_untrusted", facts=first, shown={"season": first["season"]})
    return kept, [Note("games_unseen", {"games": len(rows) - len(kept), "why": "period_untrusted", "seasons": sorted(distrust)})]


def _period_games_asked(scope: Filtered) -> tuple[str, int] | None:
    """The games a count the words asked keeps - which end and how many
    (:func:`~association.query.player_relation.relation_window`) - or
    ``None`` where the words asked no count of games."""
    if scope.window.count is None or scope.window.of != "games":
        return None
    return relation_window(scope)


def _period_games(rows: list[dict[str, Any]], measure: str) -> list[dict[str, Any]]:
    """The compiled statement's rows as the answer's games: the date, the
    opponent, home or away, every rebuilt column at the top level and the
    whole ``line`` beside them - and, for a rate, the game's own percentage
    under the rate's name (None where nothing was attempted)."""
    games: list[dict[str, Any]] = []
    for row in rows:
        line = {column: row[column] for column in PERIOD_COLUMNS}
        games.append({"date": eastern_date(row["day"]), "opponent": row["opponent_name"], "home_away": "home" if row["home"] else "away", **line, "line": line})
    if measure in PERIOD_RATES:
        for g in games:
            g[measure] = period_rate([g], measure)[2] if all(g[column] is not None for column in PERIOD_RATES[measure]) else None
    return games


def _period_figures(games: list[dict[str, Any]], measure: str) -> dict[str, Any]:
    """The figure the heading states over ``games``: a column's ``total``
    and per-game ``average``; a rate's makes as ``total``, its
    ``attempted``, and the percentage as ``average`` (None with no
    attempts)."""
    if measure in PERIOD_RATES:
        made, attempted, pct = period_rate(games, measure)
        return {"total": made, "attempted": attempted, "average": pct}
    total = sum(g[measure] for g in games)
    return {"total": total, "average": total / len(games)}


def _period_unread(games: list[dict[str, Any]], measure: str) -> bool:
    """Whether ``measure`` could not be rebuilt at all - a warehouse loaded
    without play-by-play leaves every plays column NULL, and summing NULLs
    as zeros would answer "no rebounds" for a man who had ten."""
    return any(g[column] is None for g in games for column in period_columns(measure))


def _period_log_result(
    con: duckdb.DuckDBPyConnection,
    compiled: Compiled,
    scope: Filtered,
    rows: list[dict[str, Any]],
    season: int,
    period_label: str,
    measure: str,
    where: tuple[tuple[Cell, ...], tuple[str, ...]],
    *,
    cut: bool,
    fallback: Decided | None = None,
    left_out: list[Note] | None = None,
) -> Result | Unanswered:
    """The games read, the figures over them and the caveats - or, with no
    games, the Result the sayer says "no games found" from. ``fallback`` is
    the redirect's decision (:class:`~association.query.result.Decided`),
    carried on ``Result.decisions``; ``cut``, whether the games are the
    count the words asked; ``left_out``, the note on a read across seasons
    that left an untrusted season's games out. A read over both season
    types counts the games of each (``Rows.by_season_type``), and a read
    across seasons is caveated by every season it holds and labeled by its
    first and last (decisions D13, D14)."""
    assert compiled.player is not None
    games = _period_games(rows, measure)
    if games and _period_unread(games, measure):
        return Refusal(kind="period_unread", facts={"measure": measure})
    about, seasons = _period_log_span(compiled.span, scope, rows, season, fallback=fallback)
    season_type = about.season_type
    # A log asked for (the measure's per-game `how`), and whether a stat was named at all.
    per_game = scope.measure is not None and scope.measure.how == "per_game"
    named = spelled(scope.measure)
    notes = _period_log_notes(con, compiled, seasons, measure, full_line=named is None, left_out=left_out or []) if games else []
    cells, also = where
    facts = PeriodFacts(stat=measure, per_game=per_game, full_line=named is None, also=also, cut=cut)
    # The log beneath a per-game figure: the newest N, or the first N.
    window = Window(limit=_clamp_limit(scope.window.count, default=DEFAULT_GAME_LOG_LIMIT), asked=scope.window.count, ascending=scope.window.order == "first")
    by_type = _period_by_type(rows) if season_type == BOTH_SEASON_TYPES else {}
    body = Rows(columns=PERIOD_COLUMNS, rows=tuple(games), summary=_period_figures(games, measure) if games else {}, by_season_type=by_type)
    full = _period_full_line(games) if games and named is None else None
    return Result(
        subject=compiled.player.name,
        relation="player",
        span=about,
        narrowing=_period_narrowing(compiled.narrowed, scope.cuts.venue, (Period(label=period_label), *cells)),
        window=window,
        parts=(Part(body=body), *((Part(role="detail", body=full),) if full is not None else ())),
        notes=tuple(notes),
        decisions=(fallback,) if fallback is not None else (),
        facts=facts,
    )


def _period_log_span(span: ResolvedSpan | None, scope: Filtered, rows: list[dict[str, Any]], season: int, *, fallback: Decided | None) -> tuple[Span, list[int]]:
    """The span a period log is labeled by and the seasons it is caveated
    by: across seasons, every season its games are from (decisions D13,
    D14); else the one season - the redirect's, where ``fallback`` moved it,
    in its own type."""
    if fallback is None and span is not None and span.season is None and scope.cuts.date is None:
        seasons = sorted({int(row["season"]) for row in rows})
        return _period_across_span(span, seasons, span.season_type), seasons
    season_type = span.season_type if span is not None and fallback is None else scope.span.season_type or 2
    return Span(season=season, season_type=season_type, date=None if fallback else scope.cuts.date), [season]


def _period_log_notes(con: duckdb.DuckDBPyConnection, compiled: Compiled, seasons: list[int], measure: str, *, full_line: bool, left_out: list[Note]) -> list[Note]:
    """A period log's remarks: the agreement caveat of the seasons the games
    are from, said once (measured per season; a whole line's names every column, since
    the line is shown beneath the figure where no stat was named,
    ``_period_full_line``), the games a read across seasons left out, and
    what "without" was taken to mean (decision D15)."""
    assert compiled.player is not None
    notes = period_agreement_across(seasons, measure, full_line=full_line) + left_out
    if compiled.narrowed.without and compiled.span is not None:
        notes += without_notes(con, compiled.player, compiled.span, compiled.narrowed)
    return notes


def _period_across_span(span: ResolvedSpan, seasons: list[int], season_type: int) -> Span:
    """The span of a read across seasons (decisions D13, D14): a career or a
    range, labeled by the first and last season its games are from."""
    first, last = (seasons[0], seasons[-1]) if seasons else (None, None)
    return Span(season=None, season_type=season_type, career=span.since is None, first=first, last=last, years=span.years(first, last), since=span.since, until=span.until)


def _period_by_type(rows: list[dict[str, Any]]) -> dict[int, int]:
    """How many of the games are of each season type, for a read over both
    (the heading says the split, as the bare log's does)."""
    counts: dict[int, int] = {}
    for row in rows:
        counts[int(row["season_type"])] = counts.get(int(row["season_type"]), 0) + 1
    return dict(sorted(counts.items()))


def _period_full_line(games: list[dict[str, Any]]) -> Scalar:
    """The period's whole line over ``games``, said beneath its figure where
    the words named no stat (ISSUES.md #471: "lebron james 4th qtr stats
    2026" said his 4th-quarter points alone): each
    :data:`~association.query.player_games.PERIOD_LOG_COLUMNS` column per
    game (``values``; ``None`` where a game's could not be rebuilt), each
    shooting split's makes and attempts summed (``sums``, by the season
    line's stable keys, which are the period line's own columns)."""

    def summed(column: str) -> int | None:
        """The column summed over the games, or ``None`` where a game's
        could not be rebuilt."""
        figures = [g[column] for g in games]
        return None if any(figure is None for figure in figures) else sum(figures)

    values = {column: None if (total := summed(column)) is None else total / len(games) for column in PERIOD_LOG_COLUMNS}
    sums = {column: summed(column) for rate in PERIOD_RATES.values() for column in rate}
    return Scalar(games=len(games), values=values, sums=sums)


def _period_redirect(con: duckdb.DuckDBPyConnection, read: Query, compiled: Compiled, period_label: str, measure: str) -> Result | Unanswered | None:
    """ "Last N games" with no season named is the newest N over his whole
    CAREER, not "this (defaulted) season alone" - the reading a bare
    ``limit`` gets everywhere else on the player relation
    (:func:`~association.query.player_relation.relation_window`). A
    question whose words read a career is read across its seasons before
    this (decision D13, :func:`_period_across_seasons`); this is a
    defaulted, empty ONE-season read finding nothing at all.

    yardstick-v2 F050: "zach collins first quarter stats last 5 games as a
    starter" answered "No 2026 regular season games found for Zach Collins
    as a starter" - true of the box scores it read, and about the wrong
    year: he made zero 2025-26 starts, and his real last 5 starts are all in
    March 2025. Read again over his career with the SAME narrowing, as the
    compiler's statement over that span (:func:`~association.query.compose.core.compile_over`),
    windowed to the newest N by date.

    Only when the season was never named (``span.defaulted``) and the
    question asked for a window; the found games are answered ONLY when
    they land in exactly one season, since the caveat is measured per
    season - ``None`` falls back to the plain "no games" answer.
    """
    scope, player, span = read, compiled.player, compiled.span
    assert player is not None
    window = relation_window(scope)
    if not span.defaulted or window is None or window[0] != "recent":
        return None
    career = span_of(reading.Span(career=True), "player_game_log", season_type=span.season_type)
    narrowed = scoped_games(con, player, career, scope, opponent=compiled.narrowed.opponent, measures=measure_filters(scope))
    if isinstance(narrowed, Unanswered):
        return None
    widened = compile_over(con, replace(read, limit=window[1], direction="desc"), player, career, narrowed)
    rows = sorted(rows_of(con, widened), key=lambda row: row["day"])
    seasons = {int(row["season"]) for row in rows}
    if len(seasons) != 1:
        return None
    (season,) = seasons
    refused = _period_untrusted(season, measure)
    if refused is not None:
        return refused
    if _period_unread(_period_games(rows, measure), measure):
        return None
    where = _period_where(scope, widened.narrowed)
    fallback = Decided(kind="season_fallback", field="season", chose=season, before=span.season, facts={"games": len(rows), "season_type": span.season_type})
    return _period_log_result(con, widened, scope, rows, season, period_label, measure, where, cut=True, fallback=fallback)


# --- the four quarters side by side ------------------------------------------------------


def _period_by_quarter(con: duckdb.DuckDBPyConnection, q: Query) -> Result | Unanswered | None:
    """A named player's four quarters side by side (#162): his per-game
    figure and total in each quarter over the same games - the compiler's
    ``grouped``-by-``period`` read, executed."""
    scope = q
    measure = period_split_measure(scope.measure)
    if q.measures != [measure]:
        return None
    across = _period_across_seasons(scope)
    if scope.cuts.date is None and not across:
        refused = _period_untrusted(scope.span.season or current_season(), measure)
        if refused is not None:
            return refused
    compiled = compile_query(con, q)
    if compiled.player is None:
        return None
    rows = rows_of(con, compiled)
    seasons = sorted({int(season) for row in rows for season in row.get("seasons") or []}) if across else None
    for season in seasons or []:
        # One table over every season, so a season that cannot be trusted
        # refuses the whole, named, rather than being summed in.
        refused = _period_untrusted(season, measure)
        if refused is not None:
            return refused
    return _period_quarters_result(compiled, scope, rows, measure, seasons)


def _period_quarters_result(compiled: Compiled, scope: Filtered, rows: list[dict[str, Any]], measure: str, seasons: list[int] | None = None) -> Result | Unanswered:
    """The four quarters from the compiled statement's rows, with the
    remarks - :func:`_period_by_quarter`'s tail. ``seasons`` are the seasons
    a read across them holds (decision D13), each caveated, the span
    labeled by its first and last."""
    assert compiled.player is not None
    by_quarter = {int(row["group"]): row for row in rows}
    games = max((int(row["games"]) for row in rows), default=0)
    if games and any(row.get(measure) is None and row.get(f"{measure}_total") is None and measure not in PERIOD_RATES for row in by_quarter.values()):
        return Refusal(kind="period_unread", facts={"measure": measure})
    season = _period_quarters_season(scope, rows, measure) if games and seasons is None else scope.span.season or current_season()
    if isinstance(season, Unanswered):
        return season
    narrowed = compiled.narrowed
    cells, also = _period_where(scope, narrowed)
    facts = PeriodFacts(stat=measure, also=also, games=games)
    # The compiler's window cut these games (the same N in every quarter).
    window = Window(limit=narrowed.window[1], ascending=narrowed.window[0] == "first") if narrowed.window is not None else None
    quarters = tuple(_period_quarter(by_quarter.get(quarter, {}), quarter, measure) for quarter in REGULATION_QUARTERS) if games else ()
    notes = _period_quarters_notes(seasons if seasons is not None else [season], measure) if games else []
    about = _period_quarters_span(compiled.span, scope, seasons, season)
    return Result(
        subject=compiled.player.name,
        relation="player",
        span=about,
        narrowing=_period_narrowing(narrowed, scope.cuts.venue, cells),
        window=window,
        parts=(Part(body=Grouped(by="period", rows=quarters)),),
        notes=tuple(notes),
        facts=facts,
    )


def _period_quarters_span(span: ResolvedSpan | None, scope: Filtered, seasons: list[int] | None, season: int) -> Span:
    """The span the four quarters are labeled by: across ``seasons`` (a
    career or a range, decision D13), or the one season."""
    if seasons is not None and span is not None:
        return _period_across_span(span, seasons, span.season_type)
    return Span(season=season, season_type=scope.span.season_type or 2, date=scope.cuts.date)


def _period_quarters_notes(seasons: list[int], measure: str) -> list[Note]:
    """The four quarters' remarks: that overtime is no quarter, and the
    agreement caveat of the seasons the games are from, said once."""
    return [Note("definition", {"term": "overtime_excluded"}), *period_agreement_across(seasons, measure)]


def _period_quarters_season(scope: Filtered, rows: list[dict[str, Any]], measure: str) -> int | Unanswered:
    """The season the four quarters are labeled and caveated by: the one
    asked, or - for a date - the season the dated game falls in, read off
    the rows as the one-quarter read does, and refused off it where its
    figures cannot be trusted - never "2026 regular season on 2014-11-01",
    caveated by 2026's agreement (ISSUES.md #302)."""
    if scope.cuts.date is None:
        return scope.span.season or current_season()
    season = int(min(row["first_season"] for row in rows if row["first_season"] is not None))
    refused = _period_untrusted(season, measure)
    return refused if refused is not None else season


def _period_quarter(row: dict[str, Any], quarter: int, measure: str) -> dict[str, Any]:
    """One quarter of the four: its games, per-game figure and total - or,
    for a rate, its makes, attempts and percentage."""
    entry: dict[str, Any] = {"quarter": quarter, "games": int(row.get("games") or 0)}
    if measure in PERIOD_RATES:
        made, attempted = int(row.get(f"{measure}_made") or 0), int(row.get(f"{measure}_attempted") or 0)
        return {**entry, "made": made, "attempted": attempted, "pct": (made * 100.0 / attempted) if attempted else None}
    average = row.get(measure)
    return {**entry, "average": None if average is None else float(average), "total": int(row.get(f"{measure}_total") or 0)}


# --- a team's quarter or half ------------------------------------------------------------


def _team_quarter_points_measure(asked: Measure | None) -> tuple[str, bool] | Unanswered:
    """The column a team's period question measures, and whether it is the
    opponent's figure ("points allowed in the 4th quarter", "opp rebounds":
    the other side's linescore and rebuilt line in the same games, ISSUES.md
    #427) - points where it names none (the linescore's), else any column
    the team's period line rebuilds
    (:data:`~association.query.team_games.TEAM_PERIOD_COLUMNS`), or a
    shooting percentage over two of them - or the refusal, naming the stat,
    for one nothing splits by period: answering it with the team's POINTS
    was the fluent wrong answer this guards against ("trailblazers stats
    last 10 games 3 point average 1st quarter")."""
    if asked is not None and asked.whose == "opponent":
        # The same column, read from the other side of each game.
        theirs = _team_quarter_points_measure(replace(asked, whose="own"))
        if not isinstance(theirs, tuple):
            # Refused for the stat, named as theirs below.
            named = spelled_whose(asked)
            return Refusal(kind="team_period_unknown", facts={"stat": named}, shown={"stat": named})
        return theirs[0], True
    own = _team_quarter_points_own(asked)
    return own if isinstance(own, Unanswered) else (own, False)


def _team_quarter_points_own(asked: Measure | None) -> str | Unanswered:
    """The column of the team's own figure a period question measures, or
    the refusal naming the stat."""
    stat = spelled(asked)
    if stat is None or stat == "all" or resolve_metric(asked) in ("avg_points", "total_points"):
        return "points"
    key = keyed(asked)
    if key in TEAM_PERIOD_COLUMNS:
        return key
    if key in PERIOD_RATE_STATS:
        return PERIOD_RATE_STATS[key]
    # The opponent's figure is named as theirs ("opp points in the 1st
    # quarter"): "not points" beside a list that holds points said nothing.
    named = spelled_whose(asked)
    return Refusal(kind="team_period_unknown", facts={"stat": named}, shown={"stat": named})


def _team_quarter_points_team_and_span(con: duckdb.DuckDBPyConnection, scope: Filtered) -> tuple[Entity, ResolvedSpan] | Unanswered:
    """The team and the seasons its games come from - through
    :func:`~association.query.team_relation.scoped_team`, the order every
    team read settles them in, unless a ``date`` already names one game
    outright: a date from a past season looked for inside the defaulted
    current season finds nothing, so a named date reads every
    season on record instead."""
    if scope.cuts.date:
        team = resolved_team(con, scope.who.team, season=slot_season(scope))
        if isinstance(team, Unanswered):
            return team
        return team, ResolvedSpan(None, scope.span.season_type or 2)
    scoped = TeamQuery(who=reading.Subject(kind="team", teams=(scope.who.team,)) if scope.who.team is not None else reading.Subject(), span=scope.span)
    settled = scoped_team(con, scoped, "team")
    if isinstance(settled, Unanswered) or not scope.span.both or scope.cuts.game_n:
        return settled
    # Both season types in one read: "last 10 games" are the last ten the
    # team played, the playoffs among them (decision D14), each season's
    # rebuilt column caveated by its own agreement.
    team, span = settled
    return team, replace(span, season_type=BOTH_SEASON_TYPES)


def _team_quarter_points_games(con: duckdb.DuckDBPyConnection, narrowed: TeamNarrowed, measure: str) -> tuple[list[dict[str, Any]], list[int]]:
    """Each qualifying game's date, opponent and points in the asked-for
    periods - None where the game never reached any of them, not zero - and
    ``measure`` beside them when it is another column, read from the
    period-narrowed relation's own rows (:func:`~association.query.team_games.period_games_sql`),
    with each game's season (a postseason by the calendar year it was
    played in) so a career answer names its real span and a rebuilt column
    is checked season by season."""
    if measure not in TEAM_PERIOD_COLUMNS and measure not in PERIOD_RATES:  # set by the caller, in code
        raise ValueError(f"a period-narrowed read of a period column, got measure={measure!r}")
    columns = period_columns(measure)
    # The period line's own statement, the relation's: the team compiler's
    # rows read selects whole games' columns, not a period's.
    rows = period_games(con, narrowed, columns)
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


def _team_quarter_points_weakest(columns: tuple[str, ...], season: int) -> float | None:
    """The lowest measured agreement among ``columns`` in ``season``
    (:data:`~association.query.team_games.TEAM_PERIOD_AGREEMENT`), or None
    where none is listed."""
    listed = [TEAM_PERIOD_AGREEMENT[column][season] for column in columns if season in TEAM_PERIOD_AGREEMENT.get(column, {})]
    return min(listed) if listed else None


def _team_quarter_points_rebuilt(team: Entity, games: list[dict[str, Any]], seasons: list[int], measure: str, period_label: str) -> tuple[list[dict[str, Any]], list[Note]] | Unanswered:
    """For a column rebuilt from the plays: the games that can be read and
    the notes on the ones that cannot, or the refusal. A game with no
    play-by-play holds NULL, never a zero: it is left out and counted, and
    when no game has any the question is refused on that cause; a season
    whose team-games rebuild this column right under
    :data:`~association.query.player_games.PERIOD_REFUSE_BELOW` refuses the
    whole question, naming the season. Points are the linescore's, and pass
    through untouched."""
    if measure == "points":
        return games, []
    columns = period_columns(measure)
    reached = [(g, season) for g, season in zip(games, seasons, strict=True) if g["points"] is not None]
    known = [(g, season) for g, season in reached if all(g[column] is not None for column in columns)]
    # A rate is as weak as the weaker of the two columns it divides.
    weak = {season: _team_quarter_points_weakest(columns, season) for _, season in known}
    if reached and not known:
        return Refusal(kind="team_period_unread", facts={"team": team.name, "measure": measure}, shown={"team": team.name})
    listed = sorted((season, pct) for season, pct in weak.items() if pct is not None)
    refused = [(season, pct) for season, pct in listed if pct < PERIOD_REFUSE_BELOW]
    if refused:
        return Refusal(
            kind="team_period_untrusted",
            facts={"team": team.name, "measure": measure, "period": period_label, "weak": [list(each) for each in refused]},
            shown={"team": team.name, "seasons": [season for season, _ in refused]},
        )
    unreached = [g for g in games if g["points"] is None]
    return [g for g, _ in known] + unreached, _team_quarter_points_notes(measure, listed, missing=len(reached) - len(known), reached=len(reached))


def _team_quarter_points_notes(measure: str, listed: list[tuple[int, float]], *, missing: int, reached: int) -> list[Note]:
    """A rebuilt column's notes: the games with no play-by-play left out,
    and each listed season's measured agreement."""
    notes = [Note("games_unseen", {"games": missing, "of": reached, "why": "no_play_by_play"})] if missing else []
    if listed:
        facts = {"seasons": [season for season, _ in listed], "pct": [pct for _, pct in listed], "columns": list(period_columns(measure)), "stat": measure, "what": "team_period_rebuilt"}
        notes.append(Note("rebuilt_agreement", facts))
    return notes


def _team_quarter_points_span_words(span: ResolvedSpan, dated: bool, first: Any, last: Any) -> str:
    """The span as a bare noun phrase - "2026 regular season", "since 2022
    (2022-2026 regular seasons)", "from 2022 through 2024 (...)" or "their
    career (1994-2026 regular seasons)" - carried as the span's words
    (``Span.phrase``); empty for a dated question, whose date already says
    which game."""
    if dated:
        return ""
    if span.season is not None:
        return season_phrase(span.season, span.season_type)
    years = span.years(first, last) if isinstance(first, int) and isinstance(last, int) else f"{span.kind}s"
    if span.since is not None:
        bound = f"from {span.since} through {span.until}" if span.until is not None else f"since {span.since}"
        return f"{bound} ({years})"
    return f"their career ({years})"


def _team_quarter_points_line(games: list[dict[str, Any]], measure: str) -> Scalar:
    """The games that reached the period, reduced: the total and the average
    of ``measure``, or a rate's makes over its attempts; the extreme a
    "most"/"fewest" question names."""
    played = [g for g in games if g["points"] is not None]
    if not played:
        return Scalar(games=0)
    if measure in PERIOD_RATES:
        made, attempted, pct = period_rate(played, measure)
        return Scalar(games=len(played), values={measure: pct}, sums={"made": made, "attempted": attempted})
    total = sum(g[measure] for g in played)
    return Scalar(games=len(played), values={measure: round(total / len(played), 2), "most": max(g[measure] for g in played), "fewest": min(g[measure] for g in played)}, sums={measure: total})


def read_team_quarter_points(con: duckdb.DuckDBPyConnection, q: TeamQuery) -> Result | Unanswered | None:
    """``TEAM_PERIOD_TOTAL``' point - a team's figure in ONE quarter or
    half, game by game and over them all, narrowed by any of the team
    relation's cells - read into a Result: the line over the games that
    reached the period (a :class:`~association.query.result.Scalar`) and the
    games beneath it (a detail :class:`~association.query.result.Rows`).
    Points are the linescore's; any other column of the period's line is
    rebuilt from the plays, each season caveated or refused by its measured
    agreement. A
    :class:`~association.query.result.Refusal` or :class:`~association.query.result.Clarify` back is a
    refusal - a stat nothing splits by period, no play-by-play, a season
    rebuilt too seldom right, the relation's own.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Takes no ``stated``: the answer side checks the point's cells against
       its shape's row before asking (``compose.plan.cells_unhonored``,
       Phase 3, step 2's closing slice).
    """
    scope = q
    refused = coverage_refusal(PointShape("team_periods", "scalar", "total"), scope)
    if refused is not None:
        return refused
    asked = period_narrowing(scope)
    if asked is None:
        period = scope.period
        quarter = period.number if period is not None and not period.half else None
        half = period.number if period is not None and period.half else None
        raise Unsupported(f"a team's quarter or half needs a period 1-10 or a half 1-2, got period={quarter!r} half={half!r}")
    periods, period_label = asked
    if scope.who.player is not None:
        # A named player's quarter or half is period_split's.
        raise Unsupported("a team's quarter or half cannot answer for a named player")
    measured = _team_quarter_points_measure(scope.measure)
    if isinstance(measured, Unanswered):
        return measured
    measure, theirs = measured
    settled = _team_quarter_points_team_and_span(con, scope)
    if isinstance(settled, Unanswered):
        return settled
    team, span = settled
    # Every cell the shape reads, named: the quarter or half is the
    # relation's narrowing too, so every read of `narrowed` sees that part
    # of each game.
    narrowing = TeamQuery(cuts=Cuts(venue=scope.cuts.venue, game_n=scope.cuts.game_n, round=scope.cuts.round, situation=scope.cuts.situation), window=scope.window, period=scope.period)
    narrowed = team_games(con, team, span, narrowing, opponent=scope.cuts.opponent, date=scope.cuts.date)
    if isinstance(narrowed, Unanswered):
        return narrowed
    if theirs:
        narrowed.period_whose = "opponent"
    games, seasons = _team_quarter_points_games(con, narrowed, measure)
    first, last = (min(seasons), max(seasons)) if seasons else (None, None)
    read = _team_quarter_points_rebuilt(team, games, seasons, measure, period_label)
    if isinstance(read, Unanswered):
        return read
    shown, notes = read
    return Result(
        subject=team.name,
        relation="team",
        span=Span(season=span.season, season_type=span.season_type, career=span.season is None, first=first, last=last, phrase=_team_quarter_points_span_words(span, bool(narrowed.date), first, last)),
        narrowing=Narrowing(
            phrase=narrowed.filters(opponent=False, period=False),
            opponent=narrowed.opponent.name if narrowed.opponent else None,
            venue=scope.cuts.venue,
            cells=(Period(label=period_label, periods=tuple(periods)),),
        ),
        parts=(Part(body=_team_quarter_points_line(shown, measure)), Part(role="detail", body=Rows(rows=tuple(shown)))),
        notes=tuple(notes),
        facts=TeamPeriodFacts(measure=measure, rank=scope.window.rank, dateless=narrowed.filters(opponent=False, date=False, period=False), whose="opponent" if theirs else "own"),
    )


# --- the league's ranking by a quarter or half ---------------------------------------------

#: How many players the points-by-quarter table shows where the question named no count.
_PERIOD_LEADERBOARD_BY_QUARTER_LIMIT = 10


def _period_leaderboard_minimum(con: duckdb.DuckDBPyConnection, narrowed: Narrowed, scope: Filtered, default: int) -> tuple[int, int | None]:
    """The games a player needs to rank, and - where the pool is narrowed to
    an opponent or a venue and the season's minimum is too many - the most
    anyone played in it, which the minimum is half of (#185, Jeff's call:
    a share of the narrowed games, never over the season's own
    minimum and never under one)."""
    if not (scope.cuts.opponent or scope.cuts.venue):
        return default, None
    most = most_games(con, narrowed, rebuilt=box_source(con).rebuilt)
    share = max(1, -(-most // 2))
    if share >= default:
        # A venue leaves half a season, where the season's own minimum
        # still applies and is said as itself.
        return default, None
    return share, most


def _period_leaderboard_decided(minimum: int, most: int | None) -> Decided:
    """The minimum a period ranking applied, as the decision it is."""
    return Decided(kind="minimum", field="minimum", chose=minimum, why="half_of_most" if most is not None else "per_game_minimum", facts={"of": "games"})


def _period_leaderboard_narrowing(narrowed: Narrowed, period: str | None) -> Narrowing:
    """The opponent and the venue the pool was narrowed to (the team a
    ranking is OF is said its own way), and the period."""
    return Narrowing(opponent=narrowed.opponent.name if narrowed.opponent is not None else None, venue=narrowed.venue, cells=(Period(label=period),) if period is not None else ())


def read_period_leaderboard(con: duckdb.DuckDBPyConnection, q: Query) -> Result | Unanswered | None:
    """``PERIOD_RANKING``'s point - players ranked by a stat in ONE
    quarter or half, per game, or with no period named by their points in
    each of the four quarters at once - read over every player's games in
    one season (:func:`~association.query.player_relation.league_games`,
    narrowed to a team's roster when one is named), the period applied by
    the relation itself. The denominator is games PLAYED; a per-game
    average needs a minimum (the season's per-game qualifier, or half of
    the most anyone played in a pool narrowed to an opponent or a venue);
    the season's measured accuracy caveats or refuses it. A
    :class:`~association.query.result.Refusal` or :class:`~association.query.result.Clarify` back is a
    refusal.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Takes no ``stated``: the answer side checks the point's cells against
       its shape's row before asking (``compose.plan.cells_unhonored``,
       Phase 3, step 2's closing slice).
    """
    scope = q
    try:
        measure = period_split_measure(scope.measure)
    except Unsupported as exc:
        raise Unsupported(f"a ranking by a quarter or half ranks only what the period's line rebuilds - {exc}") from exc
    if measure in PERIOD_RATES:
        # A games-played qualifier says nothing about attempts, and a
        # percentage over a handful of them ranks noise.
        return Refusal(kind="period_rank_rate", facts={"measure": measure}, shown={"stat": measure})
    season = scope.span.season or current_season()
    season_type = scope.span.season_type or 2
    distrust = period_distrust(season, measure)
    if distrust is not None:
        return Refusal(kind="period_untrusted", facts=distrust, shown={"season": distrust["season"]})
    span = span_of(reading.Span(season=season), "player_game_log", season_type=season_type)
    minimum = PER_GAME_MIN_POSTSEASON_GAMES if season_type == POSTSEASON else PER_GAME_MIN_GAMES
    asked = period_narrowing(scope)
    if asked is None:
        return _period_leaderboard_by_quarter(con, span, scope, minimum)
    narrowed = league_games(con, span, scope, position=None)
    if isinstance(narrowed, Unanswered):
        return narrowed
    if not narrowed.period_plays and measure in PERIOD_PLAYS_COLUMNS:
        return Refusal(kind="period_rank_unread", facts={"measure": measure})
    minimum, most = _period_leaderboard_minimum(con, narrowed, scope, minimum)
    rows = period_ranking(con, narrowed, measure, minimum=minimum, limit=_clamp_limit(scope.window.count), rebuilt=box_source(con).rebuilt)
    leaders = tuple({"player": name, "games": int(games), measure: int(total), "average": round(float(average), 1)} for name, games, total, average in rows)
    return Result(
        subject=narrowed.team.name if narrowed.team is not None else "",
        relation="everyone",
        span=Span(season=season, season_type=season_type),
        narrowing=_period_leaderboard_narrowing(narrowed, asked[1]),
        parts=(Part(body=Grouped(by="player", rows=leaders, ranked_by=measure)),),
        notes=tuple(period_agreement_notes(season, measure)),
        decisions=(_period_leaderboard_decided(minimum, most),),
        facts=PeriodRankingFacts(measure=measure, minimum=minimum, most=most),
    )


def _period_leaderboard_by_quarter(con: duckdb.DuckDBPyConnection, span: ResolvedSpan, scope: Filtered, minimum: int) -> Result | Unanswered:
    """Points by quarter, per game, for every qualifying player: one read of
    the relation per quarter (each narrowed by the relation itself), joined
    by player, ranked by the four averages together - his points per game
    in regulation. Overtime is no quarter and is left out, and the answer
    says so (yardstick-v2 F048, "nba playerspoints by quarter average")."""
    per_quarter: dict[str, dict[int, tuple[int, float]]] = {}
    names: dict[str, str] = {}
    rebuilt = box_source(con).rebuilt
    most: int | None = None
    narrowing = Narrowing()
    team = None
    for quarter in REGULATION_QUARTERS:
        narrowed = league_games(con, span, replace(scope, period=reading.Period(number=quarter)), position=None)
        if isinstance(narrowed, Unanswered):
            return narrowed
        team = narrowed.team
        if quarter == REGULATION_QUARTERS[0]:
            # The pool is the same games in every quarter, so the share is
            # read once (#185).
            minimum, most = _period_leaderboard_minimum(con, narrowed, scope, minimum)
            narrowing = _period_leaderboard_narrowing(narrowed, None)
        for athlete, name, games, average in period_quarter(con, narrowed, minimum=minimum, rebuilt=rebuilt):
            names[athlete] = name
            per_quarter.setdefault(athlete, {})[quarter] = (int(games), float(average))
    whole = {athlete: quarters for athlete, quarters in per_quarter.items() if len(quarters) == len(REGULATION_QUARTERS)}
    ranked = sorted(whole, key=lambda athlete: (-sum(avg for _, avg in whole[athlete].values()), names[athlete]))
    limit = _clamp_limit(scope.window.count, default=_PERIOD_LEADERBOARD_BY_QUARTER_LIMIT)
    leaders = tuple(
        {"player": names[a], "games": whole[a][1][0], **{f"q{q}": round(whole[a][q][1], 2) for q in REGULATION_QUARTERS}, "total": round(sum(avg for _, avg in whole[a].values()), 2)}
        for a in ranked[:limit]
    )
    season = span.season or current_season()
    return Result(
        subject=team.name if team is not None else "",
        relation="everyone",
        span=Span(season=span.season, season_type=span.season_type),
        narrowing=narrowing,
        parts=(Part(body=Grouped(by="player", rows=leaders, ranked_by="quarters")),),
        notes=(Note("definition", {"term": "overtime_excluded"}), *period_agreement_notes(season, "points")),
        decisions=(
            _period_leaderboard_decided(minimum, most),
            Decided(kind="cut", field="limit", chose=len(leaders), before=scope.window.count, facts={"total": len(ranked)}),
        ),
        facts=PeriodRankingFacts(measure="points", minimum=minimum, most=most, qualified=len(ranked)),
    )
