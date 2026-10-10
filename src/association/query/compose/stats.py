"""A player's line over the games a question narrowed to: his per-game
averages against one opponent, at home, as a starter, on one date - read
into a :class:`~association.query.result.Result` whose headline part is a
:class:`~association.query.result.Scalar` (the whole narrowed set reduced
to one line) and, against one opponent, a second part listing the newest
meetings behind it. Phase 2's slice (i), ``player_stat``'s narrowed point:
the retired template's read (``templates.players._box_score_player_stat``,
a hand-written aggregate over the relation) is gone, and the statement run
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
from association.query.reading import Unsupported, unhonored_scoping
from association.query.result import LineFacts, Narrowing, Part, Result, Rows, Scalar, Span, Unanswered
from association.query.season_line import ADVANCED_STATS, MADE_STAT_ATTEMPTS, SHOOTING_STATS, wanted_stats

from .core import _MADE_RATE, Compiled, Query, _compile_rows, compile_query, rows_of

#: A shooting percentage ``player_stat`` names, as the compiler's rate - the
#: ratio of the sums the answer states ("168 of 306").
SHOOTING_RATES: dict[str, str] = {"fieldGoalPct": "fg_pct", "threePointFieldGoalPct": "three_pct", "freeThrowPct": "ft_pct", "twoPointFieldGoalPct": "two_pct"}
"""``player_stat``'s shooting stat -> the compiler's rate measure.

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
    """The measures ``player_stat``'s own narrowed point reads: the shooting
    rate a percentage names, or the stats wanted (the one named, or the
    default line) where they are the point's own - else ``None``, and the
    compiler's sentence answers. A stat with no per-game column is the
    template's own decline, kept: ``None``, as the presenter returned."""
    stat = spelled(q.scope.measure)
    if stat is not None and stat in ADVANCED_STATS:
        return None
    if stat is not None and stat in SHOOTING_STATS:
        return [SHOOTING_RATES[stat]]
    try:
        wanted = wanted_stats(q.scope)
    except Unsupported:
        return None
    return wanted if sorted(q.measures) == sorted(wanted) else None


def read_player_stat(con: duckdb.DuckDBPyConnection, q: Query, *, stated: frozenset[str]) -> Result | Unanswered | None:
    """``player_stat``'s narrowed point - a per-game scalar over a named
    player's box scores, where an opponent, a venue, a date, a starter half
    or a condition sent the read there - read as one line: each stat's
    per-game figure and total (a made count's makes and attempts where it
    is read alone, a percentage's makes and attempts), the games and the
    seasons they span, the remarks the box scores need, and against one
    opponent the newest meetings behind it. The statement is the
    compiler's (the point compiled with the ``line`` aggregate), and the
    meetings its ``rows`` read over the same narrowed games and the same
    rebuilt-line rule, so the games listed are games the average is over.
    ``None`` where the point is not that, or carries a narrowing the
    template's words did not state (``stated``); a
    :class:`~association.query.result.Refusal` or :class:`~association.query.result.Clarify` back is
    the relation's refusal.

    .. versionadded:: 5.0.0
    """
    if q.skeleton != "scalar" or q.aggregate != "per_game" or q.subject != "player" or q.source != "games" or q.predicates:
        return None
    if unhonored_scoping("player_stat", q.scope, stated):
        return None
    measures = _player_stat_measures(q)
    if measures is None:
        return None
    compiled = compile_query(con, replace(q, aggregate="line", measures=measures))
    if compiled.player is None:
        return None
    (line,) = rows_of(con, compiled)
    return _player_stat_result(con, q, compiled, line)


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

    Product decision (2026-09-19): "stats vs X" is the averages over every
    meeting in scope, the game count, and a short footer of the meetings
    themselves - which is what makes "in 1 game" honest, and what a reader
    asking "vs X" is usually after. A per-game log is still ``game_log``'s,
    for a question that says log, each game or last N."""
    assert compiled.player is not None
    meetings = replace(q, skeleton="rows", aggregate="none", measures=[*_MEETING_MEASURES, *compiled.measures], order="date", direction="desc", limit=RECENT_MEETINGS, offset=0)
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
    stat = spelled(q.scope.measure)
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
