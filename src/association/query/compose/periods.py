"""The period reader: a named player's figure in one quarter or half, game by
game, or his four quarters side by side, read into a
:class:`~association.query.result.Result` - ``period_split``'s own point.
Phase 2's slice (i) (``ROADMAP.md``, "Phase 2, the expected steps", step 1,
sub-step (e)): the retired template's per-period read
(``templates.games._period_split_from``, which the presenter
``compose.present._present_period_split`` called over the compiler's
settled player until 2026-10-04) and its by-quarter wording
(``_period_split_by_quarter_from``) replaced by the compiled statement and a
sayer (:mod:`association.query.compose.say`).

Two shapes, two statements, both the compiler's:

- **One quarter or half** - the point compiled as a ``rows`` read of every
  game in the span in date order, the period's whole line
  (:data:`~association.query.player_games.PERIOD_COLUMNS`) and the
  opponent's name as its measures: the relation applies the period
  (``player_games._period_source``), so every figure is the period's own.
  Measured before it replaced the template's read
  (``~/association-research/stages/period_split_measure.py``): the same
  games, dates, sides, opponents and figures on 18 of the 18 recorded
  questions the template answered, once the compiler gained the
  ``opponent_name`` measure (the template named the team where the
  compiler's fixed columns carry its abbreviation).
- **The four quarters** - the planned ``grouped`` read by ``period``
  (:func:`~association.query.compose.core._compile_by_period`), executed as
  it was by the presenter.

The order of the template's refusals is kept: a season whose per-period
figures cannot be trusted is refused before any name is resolved
(:func:`~association.query.player_games.period_distrust`), and again off
the game a date names once it is found.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import duckdb

from association.nba.season import current_season, eastern_date
from association.query.measures import period_split_measure
from association.query.notes import Note
from association.query.player_games import PERIOD_COLUMNS, PERIOD_RATES, REGULATION_QUARTERS, Narrowed, period_agreement_notes, period_columns, period_distrust, period_rate
from association.query.reading import STARTER_SIDES, Scope, period_narrowing
from association.query.result import Grouped, Narrowing, Part, Result, Rows, Span
from association.query.templates.common import TemplateResult, TemplateUnsupported, measure_filters, relation_window, scoped_games, span_of, unhonored_scoping

from .core import Compiled, Query, compile_over, compile_query, rows_of
from .say import say_period_refusal, say_period_unread

#: What the per-period log reads beside the fixed columns: the period's whole
#: line, and the opponent as the game's own season named it.
_PERIOD_LOG_MEASURES: list[str] = [*PERIOD_COLUMNS, "opponent_name"]


def read_period_split(con: duckdb.DuckDBPyConnection, q: Query, *, stated: frozenset[str]) -> Result | TemplateResult | None:
    """``period_split``'s own point - a named player's games read as one
    quarter's or half's line, or his four quarters side by side - read into
    a Result over the compiled statement. ``None`` where the point is not
    that (a predicate, another skeleton, a measure the point did not plan)
    or carries a narrowing the template's words did not state (``stated``:
    ``compose.present.STATED_SCOPING``'s set), and the compiler's own
    sentence answers; a
    :class:`~association.query.templates.common.TemplateResult` back is a
    refusal - a season whose figures cannot be trusted, a column this
    warehouse cannot rebuild, the relation's own.

    .. versionadded:: 5.0.0
    """
    if q.subject != "player" or q.predicates or unhonored_scoping("period_split", q.scope, stated):
        return None
    if q.skeleton == "grouped" and q.group == "period":
        return _period_by_quarter(con, q) if q.aggregate == "per_game" else None
    if q.skeleton != "rows" or q.order != "date" or q.group != "none":
        return None
    return _period_log(con, q)


def _period_untrusted(season: int, measure: str) -> TemplateResult | None:
    """The refusal for a season whose per-period ``measure`` cannot be
    trusted, or None."""
    distrust = period_distrust(season, measure)
    return say_period_refusal(distrust) if distrust is not None else None


def _period_where(scope: Scope, narrowed: Narrowed) -> dict[str, Any]:
    """What the answer says it narrowed to, after the player and the period,
    as values: the venue and the starter/bench half (only a NAMED half
    filters), the lines on a box-score column, the game of a series, and
    the relation's phrases for a teammate's role and a calendar or
    conference narrowing (the teammates absent are the Narrowing's)."""
    situation = narrowed._situation_phrase()
    return {
        "venue": scope.venue,
        "started": STARTER_SIDES.get(scope.split) if scope.split is not None else None,
        "measures": list(narrowed.measures),
        "series_game": narrowed.series_game,
        # A teammate's role and a calendar or a conference, in the relation's
        # own words (``Narrowed.filters``): both narrow which games count,
        # and the period sentence did not say them (ISSUES.md #301) - "jokic
        # first quarter points in january" answered one January game as
        # though it were his season.
        "also": [c.phrase() for c in narrowed.conditions if not (c.side == "own" and c.predicate == "absent")] + ([situation] if situation else []),
    }


def _period_narrowing(narrowed: Narrowed, venue: str | None) -> Narrowing:
    return Narrowing(opponent=narrowed.opponent.name if narrowed.opponent else None, venue=venue, without=tuple(mate.name for mate in narrowed.without))


# --- one quarter or half, game by game ----------------------------------------------------


def _period_log(con: duckdb.DuckDBPyConnection, q: Query) -> Result | TemplateResult | None:
    """A named player's figure in ONE quarter or half, per game and over
    them all - read over every game of the span (the heading totals and
    averages them; the log beneath shows the newest N), in date order."""
    scope = q.scope
    asked = period_narrowing(scope)
    if asked is None:
        raise TemplateUnsupported(f"period_split needs a period 1-10 or a half 1-2, got period={scope.period!r} half={scope.half!r}")
    measure = period_split_measure(scope.stat)
    if q.measures != [measure]:
        return None
    if scope.date is None:
        refused = _period_untrusted(scope.season or current_season(), measure)
        if refused is not None:
            return refused
    read = replace(q, measures=_PERIOD_LOG_MEASURES, limit=None, offset=0, direction="asc")
    compiled = compile_query(con, read)
    if compiled.player is None:
        return None
    rows = rows_of(con, compiled)
    season = scope.season or current_season()
    if scope.date is not None and rows:
        # The season a date's game actually falls in, read off the row itself
        # rather than the slot: an explicit year in the question ("... on
        # november 11 2019") can name a date the season slot disagrees with.
        season = int(rows[0]["season"])
        refused = _period_untrusted(season, measure)
        if refused is not None:
            return refused
    where = _period_where(scope, compiled.narrowed)
    if not rows:
        redirected = _period_redirect(con, read, compiled, asked[1], measure)
        if redirected is not None:
            return redirected
    return _period_log_result(compiled, scope, rows, season, asked[1], measure, where)


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
    compiled: Compiled, scope: Scope, rows: list[dict[str, Any]], season: int, period_label: str, measure: str, where: dict[str, Any], *, fallback: dict[str, Any] | None = None
) -> Result | TemplateResult:
    """The games read, the figures over them and the caveats - or, with no
    games, the Result the sayer says "no games found" from."""
    assert compiled.player is not None
    games = _period_games(rows, measure)
    if games and _period_unread(games, measure):
        return say_period_unread(measure)
    season_type = scope.season_type or 2
    notes: list[Note] = []
    if games:
        notes = period_agreement_notes(season, measure, full_line=scope.per_game and scope.stat is None and len(games) > 1)
    facts: dict[str, Any] = {
        "period": period_label,
        "stat": measure,
        **where,
        "per_game": scope.per_game,
        "full_line": scope.stat is None,
        "order": scope.order,
        "limit": scope.limit,
        "fallback": fallback,
    }
    body = Rows(columns=PERIOD_COLUMNS, rows=tuple(games), summary=_period_figures(games, measure) if games else {})
    return Result(
        subject=compiled.player.name,
        relation="player",
        span=Span(season=season, season_type=season_type, date=None if fallback else scope.date),
        narrowing=_period_narrowing(compiled.narrowed, where["venue"]),
        parts=(Part(body=body),),
        notes=tuple(notes),
        facts=facts,
    )


def _period_redirect(con: duckdb.DuckDBPyConnection, read: Query, compiled: Compiled, period_label: str, measure: str) -> Result | TemplateResult | None:
    """ "Last N games" with no season named is the newest N over his whole
    CAREER, not "this (defaulted) season alone" - the reading a bare
    ``limit`` gets everywhere else on the player relation
    (:func:`~association.query.templates.common.relation_window`). A
    question asking for a career split outright is refused before this
    (``RELATION_SCOPING_EXCLUDED["period_split"]``: the accuracy caveat is
    measured per season); this is a defaulted, empty ONE-season read
    finding nothing at all.

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
    scope, player, span = read.scope, compiled.player, compiled.span
    assert player is not None
    window = relation_window(scope)
    if not span.defaulted or window is None or window[0] != "recent":
        return None
    career = span_of("career", None, span.season_type, "player_game_log")
    narrowed = scoped_games(con, player, career, scope, opponent=compiled.narrowed.opponent, measures=measure_filters(scope.below, scope.above))
    if isinstance(narrowed, TemplateResult):
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
    fallback = {"chose": season, "before": span.season, "games": len(rows), "season_type": span.season_type}
    return _period_log_result(widened, scope, rows, season, period_label, measure, where, fallback=fallback)


# --- the four quarters side by side ------------------------------------------------------


def _period_by_quarter(con: duckdb.DuckDBPyConnection, q: Query) -> Result | TemplateResult | None:
    """A named player's four quarters side by side (#162): his per-game
    figure and total in each quarter over the same games - the compiler's
    ``grouped``-by-``period`` read, executed."""
    scope = q.scope
    measure = period_split_measure(scope.stat)
    if q.measures != [measure]:
        return None
    if scope.date is None:
        refused = _period_untrusted(scope.season or current_season(), measure)
        if refused is not None:
            return refused
    compiled = compile_query(con, q)
    if compiled.player is None:
        return None
    return _period_quarters_result(compiled, scope, rows_of(con, compiled), measure)


def _period_quarters_result(compiled: Compiled, scope: Scope, rows: list[dict[str, Any]], measure: str) -> Result | TemplateResult:
    """The four quarters from the compiled statement's rows, with the
    remarks - :func:`_period_by_quarter`'s tail."""
    assert compiled.player is not None
    by_quarter = {int(row["group"]): row for row in rows}
    games = max((int(row["games"]) for row in rows), default=0)
    if games and any(row.get(measure) is None and row.get(f"{measure}_total") is None and measure not in PERIOD_RATES for row in by_quarter.values()):
        return say_period_unread(measure)
    season = _period_quarters_season(scope, rows, measure) if games else scope.season or current_season()
    if isinstance(season, TemplateResult):
        return season
    narrowed = compiled.narrowed
    facts: dict[str, Any] = {"stat": measure, **_period_where(scope, narrowed), "date": scope.date, "window": list(narrowed.window) if narrowed.window is not None else None, "games": games}
    quarters = tuple(_period_quarter(by_quarter.get(quarter, {}), quarter, measure) for quarter in REGULATION_QUARTERS) if games else ()
    notes = [Note("definition", {"term": "overtime_excluded"}), *period_agreement_notes(season, measure)] if games else []
    return Result(
        subject=compiled.player.name,
        relation="player",
        span=Span(season=season, season_type=scope.season_type or 2),
        narrowing=_period_narrowing(narrowed, scope.venue),
        parts=(Part(body=Grouped(by="period", rows=quarters)),),
        notes=tuple(notes),
        facts=facts,
    )


def _period_quarters_season(scope: Scope, rows: list[dict[str, Any]], measure: str) -> int | TemplateResult:
    """The season the four quarters are labeled and caveated by: the one
    asked, or - for a date - the season the dated game falls in, read off
    the rows as the one-quarter read does, and refused off it where its
    figures cannot be trusted. Labeled "2026 regular season on 2014-11-01"
    and caveated by 2026's agreement until 2026-10-04 (ISSUES.md #302)."""
    if scope.date is None:
        return scope.season or current_season()
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
