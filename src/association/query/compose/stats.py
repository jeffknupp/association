"""A player's line over the games a question narrowed to: his per-game
averages against one opponent, at home, as a starter, on one date - read
into a :class:`~association.query.result.Result` whose headline part is a
:class:`~association.query.result.Scalar` (the whole narrowed set reduced
to one line) and, against one opponent, a second part listing the newest
meetings behind it - ``PLAYER_LINE``'s narrowed point. The statement run
is the compiler's - the point compiled with the ``line`` aggregate (each
measure per game, the sums a line is said from, the seasons it spans:
:func:`~association.query.compose.core._line_selects`), and the meetings as
its ``rows`` read over the same narrowed games. The sayer
(:mod:`association.query.compose.say`) words it.

The unnarrowed line - a season or a career, from the season line - is
``compose.seasons``' (``read_player_line``).

.. versionadded:: 5.0.0
"""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

import duckdb

from association.nba.season import eastern_date
from association.query.measure import spelled
from association.query.notes import Note
from association.query.player_relation import box_score_notes_read, narrowed_cells, no_narrowed_games
from association.query.reading import Aggregate, Unsupported
from association.query.result import LineFacts, Narrowing, Part, Result, Rows, Scalar, Span, Unanswered
from association.query.season_line import ADVANCED_STATS, FULL_LINE_SHOTS, FULL_LINE_STATS, MADE_STAT_ATTEMPTS, SHOOTING_STATS, full_line_asked, wanted_stats

from .core import _MADE_RATE, Compiled, Query, _compile_rows, compile_query, rows_of

#: A shooting percentage ``PLAYER_LINE`` names, as the compiler's rate - the
#: ratio of the sums the answer states ("168 of 306").
SHOOTING_RATES: dict[str, str] = {"fieldGoalPct": "fg_pct", "threePointFieldGoalPct": "three_pct", "freeThrowPct": "ft_pct", "twoPointFieldGoalPct": "two_pct"}
"""``PLAYER_LINE``'s shooting stat -> the compiler's rate measure.

.. versionadded:: 5.0.0
"""

#: How many of the meetings behind an average against one opponent are listed.
RECENT_MEETINGS = 5
"""The newest meetings an average against one opponent lists beneath it.

.. versionadded:: 5.0.0
"""

#: The figures each listed meeting shows.
_MEETING_MEASURES: tuple[str, ...] = ("points", "rebounds", "assists")


def _player_stat_measures(q: Query) -> list[str] | None:
    """The measures ``PLAYER_LINE``'s own narrowed point reads: the shooting
    rate a percentage names, or the stats wanted (the one named, or the
    default line) where they are the point's own - else ``None``, and the
    compiler's sentence answers. A stat with no per-game column is declined:
    ``None``."""
    stat = spelled(q.measure)
    if stat is not None and stat in ADVANCED_STATS:
        return None
    if stat is not None and stat in SHOOTING_STATS:
        return [SHOOTING_RATES[stat]]
    try:
        wanted = wanted_stats(q)
    except Unsupported:
        return None
    return wanted if sorted(q.measures) == sorted(wanted) else None


def read_player_stat(con: duckdb.DuckDBPyConnection, q: Query) -> Result | Unanswered | None:
    """``PLAYER_LINE``'s narrowed point - a per-game scalar over a named
    player's box scores, where an opponent, a venue, a date, a starter half
    or a condition sent the read there - read as one line: each stat's
    per-game figure and total (a made count's makes and attempts where it
    is read alone, a percentage's makes and attempts), the games and the
    seasons they span, the remarks the box scores need, and against one
    opponent the newest meetings behind it. The statement is the
    compiler's (the point compiled with the ``line`` aggregate), and the
    meetings its ``rows`` read over the same narrowed games and the same
    rebuilt-line rule, so the games listed are games the average is over.
    ``None`` where the point is not that; a
    :class:`~association.query.result.Refusal` or :class:`~association.query.result.Clarify` back is
    the relation's refusal.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Takes no ``stated``: the answer side checks the point's cells against
       its shape's row before asking (``compose.plan.cells_unhonored``,
       Phase 3, step 2's closing slice).
    """
    if q.skeleton != "scalar" or q.aggregate != Aggregate.PER_GAME or q.subject != "player" or q.source != "games" or q.predicates:
        return None
    measures = _player_stat_measures(q)
    if measures is None:
        return None
    compiled = compile_query(con, replace(q, aggregate=Aggregate.LINE, measures=measures))
    if compiled.player is None:
        return None
    (line,) = rows_of(con, compiled)
    result = _player_stat_result(con, q, compiled, line)
    return _with_full_line(con, q, result)


def _with_full_line(con: duckdb.DuckDBPyConnection, q: Query, result: Result) -> Result:
    """The line with the full box line over the same narrowed games beside
    it, as a detail part after the headline, where the words asked the line
    rather than one of its headline figures
    (:func:`~association.query.season_line.full_line_asked`): the point
    compiled again with the ``line`` aggregate over the full line's
    measures. Its own statement, so the sentence's figures are the line's
    as before: the rebuilt-line rule reads a line of rebuildable columns
    over every game and one with minutes or attempts over the fetched games
    alone, and the full line carries its own count."""
    found = result.scalar
    if not full_line_asked(spelled(q.measure)) or found is None or not found.games:
        return result
    compiled = compile_query(con, replace(q, aggregate=Aggregate.LINE, measures=[*FULL_LINE_STATS, *(SHOOTING_RATES[name] for name in FULL_LINE_SHOTS)]))
    (line,) = rows_of(con, compiled)
    sums: dict[str, Any] = {}
    for name in FULL_LINE_SHOTS:
        shot, rate = SHOOTING_STATS[name], SHOOTING_RATES[name]
        sums[shot.made_key], sums[shot.attempted_key] = line[f"{rate}_made"], line[f"{rate}_attempted"]
    full = Scalar(games=int(line["games"] or 0), values={name: line[name] for name in FULL_LINE_STATS}, sums=sums)
    if not full.games:
        # Every game the line is over is rebuilt from play-by-play, which
        # measured none of the minutes or attempts: there is no full line
        # to show beside it.
        return result
    return replace(result, parts=(result.parts[0], Part(role="detail", body=full), *result.parts[1:]))


def _player_stat_sums(compiled: Compiled, line: dict[str, Any]) -> dict[str, Any]:
    """The sums the line is said from: each stat's total, a percentage's
    makes and attempts, and a made count's attempts where it is read alone
    (F051, ISSUES.md) - only where no rebuilt line is among the games, since
    a rebuild's attempts are never read (``UNGATED_ON_REBUILD``) and "43 of
    64" over makes the attempts do not cover is a percentage of nothing."""
    sums = {name.removesuffix("_total"): value for name, value in line.items() if name.endswith("_total")}
    measures = compiled.measures
    if len(measures) == 1 and measures[0] in SHOOTING_RATES.values():
        sums["made"], sums["attempted"] = line[f"{measures[0]}_made"], line[f"{measures[0]}_attempted"]
    if len(measures) == 1 and measures[0] in MADE_STAT_ATTEMPTS and not int(line["rebuilt_shown"] or 0):
        sums[MADE_STAT_ATTEMPTS[measures[0]]] = line[f"{_MADE_RATE[measures[0]]}_attempted"]
    return sums


def _player_stat_meetings(con: duckdb.DuckDBPyConnection, q: Query, compiled: Compiled, games: int) -> Part:
    """The newest meetings behind an average against one opponent, newest
    first: the compiler's ``rows`` read over the same narrowed games and the
    same rebuilt-line rule the line was read under, with the line's own
    measures beside the three each meeting shows.

    A product decision: "stats vs X" is the averages over every
    meeting in scope, the game count, and a short footer of the meetings
    themselves - which is what makes "in 1 game" honest, and what a reader
    asking "vs X" is usually after. A per-game log is still ``PLAYER_LOG``'s,
    for a question that says log, each game or last N."""
    assert compiled.player is not None
    meetings = replace(q, skeleton="rows", aggregate=Aggregate.NONE, measures=[*_MEETING_MEASURES, *compiled.measures], order="date", direction="desc", limit=RECENT_MEETINGS, offset=0)
    found = rows_of(con, _compile_rows(meetings, copy.deepcopy(compiled.narrowed), compiled.rebuilt, compiled.player, compiled.span))
    rows = tuple(
        {
            "date": eastern_date(row["day"]),
            "season": row["season"],
            "opponent": row["opponent"],
            "home_away": "home" if row["home"] else "away",
            "result": None if row["won"] is None else ("W" if row["won"] else "L"),
            **{name: row[name] for name in _MEETING_MEASURES},
        }
        for row in found
    )
    return Part(role="detail", body=Rows(columns=_MEETING_MEASURES, rows=rows, total_before_window=games))


def _player_stat_result(con: duckdb.DuckDBPyConnection, q: Query, compiled: Compiled, line: dict[str, Any]) -> Result:
    """The line, its remarks and the meetings, from the compiled statement's
    one row - :func:`read_player_stat`'s tail."""
    player, span, narrowed = compiled.player, compiled.span, compiled.narrowed
    assert player is not None
    games = int(line["games"] or 0)
    stat = spelled(q.measure)
    facts = LineFacts(stat=stat if stat in SHOOTING_STATS else None, wanted=() if stat in SHOOTING_STATS else tuple(compiled.measures))
    narrowing = Narrowing(
        phrase=narrowed.filters(dated=False),
        opponent=narrowed.opponent.name if narrowed.opponent else None,
        venue=narrowed.venue,
        without=tuple(mate.name for mate in narrowed.without),
        cells=narrowed_cells(narrowed),
    )
    if not games:
        empty = no_narrowed_games(con, player, span, narrowed, rebuilt=compiled.rebuilt)
        return Result(
            subject=player.name,
            relation="player",
            span=Span(season=span.season, season_type=span.season_type, career=span.career, date=narrowed.date),
            narrowing=narrowing,
            parts=(Part(body=Scalar(games=0)),),
            facts=facts,
            empty=empty,
        )
    first, last = line["first_season"], line["last_season"]
    rebuilt_shown = int(line["rebuilt_shown"] or 0)
    notes: tuple[Note, ...] = tuple(box_score_notes_read(con, player, span, narrowed, rebuilt=compiled.rebuilt, rebuilt_shown=rebuilt_shown))
    # One date is one game, and its "span" is the day: the career span the
    # date replaced is how the game was FOUND, not what the answer is about,
    # so it is not said.
    when = f"on {narrowed.date}" if narrowed.date else span.during(first, last)
    scalar = Scalar(games=games, values={m: line[m] for m in compiled.measures}, sums=_player_stat_sums(compiled, line))
    parts = [Part(body=scalar)]
    if narrowed.opponent is not None:
        parts.append(_player_stat_meetings(con, q, compiled, games))
    return Result(
        subject=player.name,
        relation="player",
        span=Span(season=span.season, season_type=span.season_type, career=span.career, date=narrowed.date, first=first, last=last, phrase=when),
        narrowing=narrowing,
        parts=tuple(parts),
        notes=notes,
        facts=facts,
    )
