"""The matchup reader: the games two named players both played on opposite
teams, read into a :class:`~association.query.result.Result` - the two
players' lines over their meetings as a comparison (a
:class:`~association.query.result.Grouped` body by ``subject``, one row per
player in the question's order) and the newest meetings as a detail part
(:class:`~association.query.result.Rows`) - ``player_matchup``'s own point.
Phase 2's slice (ii): the compiler already read the meetings (the ``pair``
shape, :func:`~association.query.compose.core._compile_pair` over the pair
relation) and the retired template's words said them through a presenter;
the reader executes that statement, and the sayer
(:mod:`association.query.compose.say`) words it.

Where a teammate's absence emptied the meetings, the reader compiles the
same pair twice more - with the absence dropped, and with the teammate
playing - so the answer can say how often the two met at all; the games the
two played as teammates are the pair relation's own read
(:func:`~association.query.conditions._teammate_games`), which no compiled
shape expresses.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import duckdb

from association.nba.franchises import season_name
from association.query.conditions import _PLAYER_GAME_TABLES, MEETING_STATS, _matchup_line, _names, _player_games, _teammate_games, _totals, _unseen_meetings, box_source
from association.query.entities import Entity
from association.query.notes import Note
from association.query.player_relation import condition_scope, no_games
from association.query.reading import Companion, Scope, Unsupported, _clamp_limit
from association.query.result import Grouped, MatchupFacts, Met, Narrowing, Part, Result, Rows, Span, Unanswered, Window

from .core import Compiled, Query, Refused, compile_query, rows_of

MATCHUP_MEETINGS_SHOWN = 5
"""How many of the newest meetings a matchup lists beneath the averages,
where the question named no count.

.. versionadded:: 5.0.0
   ``templates.games._DEFAULT_MEETINGS_LOGGED`` was this.
"""


def _pair_covered(scope: Scope) -> Any:
    """The span both names resolve over: the question's own - or, where a
    date names the game, the career, since a date replaces the season the
    way ``game_log``'s own date does (the season is usually the "current"
    default, and a date from an earlier season looked for in that one finds
    nothing)."""
    if scope.cuts.date:
        return condition_scope(scope.span.over_career(), _PLAYER_GAME_TABLES)
    return condition_scope(scope.span, _PLAYER_GAME_TABLES)


def _pair_meeting(row: dict[str, Any]) -> dict[str, Any]:
    """One row of the pair statement as a meeting: the first player's
    result and scores, both teams, and each player's line under ``a`` and
    ``b``."""
    return {
        "day": row["day"],
        "season": row["season"],
        "won": bool(row["won"]),
        "team_score": row["team_score"],
        "opponent_score": row["opponent_score"],
        "team_id": str(row["team_id"]),
        "opponent_team_id": str(row["opponent_team_id"]),
        "a": {s: row[s] for s in MEETING_STATS},
        "b": {s: row[f"other_{s}"] for s in MEETING_STATS},
    }


def read_player_matchup(con: duckdb.DuckDBPyConnection, q: Query) -> Result | Unanswered | None:
    """``player_matchup``'s own point - the ``pair`` shape - read as the two
    players' lines over the meetings the compiled statement finds, the
    head-to-head record, and the newest meetings, with the games between
    their teams that have no box score as a note. A game in which they were
    teammates is not a meeting, and when every shared game was one, the
    Result says how many. ``None`` where the point is not a pair, and the
    compiler's sentence answers; a :class:`~association.query.result.Refusal` or
    :class:`~association.query.result.Clarify` back is the relation's refusal (a player with no games in the span).

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Takes no ``stated``: the answer side checks the point's cells against
       its shape's row before asking (``compose.plan.cells_unhonored``,
       Phase 3, step 2's closing slice).
    """
    if q.skeleton != "pair" or q.source != "games":
        return None
    scope = q.scope
    covered = _pair_covered(scope)
    compiled = compile_query(con, q)
    a, b = compiled.player, compiled.other
    if a is None or b is None:
        return None
    meetings = [_pair_meeting(row) for row in rows_of(con, compiled)]
    together = _teammate_games(con, compiled.narrowed, b.id)
    unseen = _unseen_meetings(con, covered, a.id, b.id)
    notes = (Note("games_unseen", {"games": unseen, "why": "no_box_score", "what": "meetings"}),) if unseen else ()
    if not meetings:
        return _pair_no_meetings(con, q, covered, compiled, a, b, together, notes)
    return _pair_result(con, scope, covered, compiled, a, b, meetings, together, notes)


def _pair_result(
    con: duckdb.DuckDBPyConnection, scope: Scope, covered: Any, compiled: Compiled, a: Entity, b: Entity, meetings: list[dict[str, Any]], together: int, notes: tuple[Note, ...]
) -> Result:
    """The two lines and the newest meetings, each team abbreviated as it
    was that season (a 2005 Nets game reads NJ, not BKN)."""
    count = len(meetings)
    wins = sum(1 for m in meetings if m["won"])
    lines = (
        {"key": a.name, "wins": wins, **_matchup_line([m["a"] for m in meetings])},
        {"key": b.name, "wins": count - wins, **_matchup_line([m["b"] for m in meetings])},
    )
    shown = meetings[: _clamp_limit(scope.window.count, MATCHUP_MEETINGS_SHOWN)]
    abbr = _names(con, "teams", "team_id", {m["team_id"] for m in shown} | {m["opponent_team_id"] for m in shown}, column="abbreviation")
    rows = tuple(
        {
            **m,
            "team": season_name(m["team_id"], m["season"], abbr[m["team_id"]], column="abbreviation"),
            "opponent": season_name(m["opponent_team_id"], m["season"], abbr[m["opponent_team_id"]], column="abbreviation"),
        }
        for m in shown
    )
    first, last = min(m["season"] for m in meetings), max(m["season"] for m in meetings)
    return Result(
        subject=a.name,
        relation="player",
        span=Span(season=covered.season, season_type=covered.season_type, career=covered.season is None, first=first, last=last, phrase=covered.label(first, last), floor=covered.first),
        narrowing=Narrowing(phrase=compiled.narrowed.filters(), cells=(Met(other=b.name),)),
        window=Window(limit=len(shown), asked=scope.window.count),
        parts=(Part(body=Grouped(by="subject", rows=lines)), Part(role="detail", body=Rows(rows=rows, total_before_window=count))),
        notes=notes,
        facts=MatchupFacts(teammate_games=together),
    )


def _pair_no_meetings(con: duckdb.DuckDBPyConnection, q: Query, covered: Any, compiled: Compiled, a: Entity, b: Entity, together: int, notes: tuple[Note, ...]) -> Result | Unanswered:
    """Two players who never met in scope: whichever of them has no games
    at all is the missing fact, and the relation says so; otherwise the
    Result holds no meetings, how many games they shared as teammates and,
    where a teammate's absence emptied the meetings, how often they met
    without it (:func:`_pair_absence`)."""
    for player in (a, b):
        if _totals(con, _player_games(covered, box=box_source(con)), {**covered.params(), "player": player.id})[0] == 0:
            return no_games(con, player, covered, None)
    return Result(
        subject=a.name,
        relation="player",
        span=Span(season=covered.season, season_type=covered.season_type, career=covered.season is None, phrase=covered.label(), floor=covered.first),
        narrowing=Narrowing(phrase=compiled.narrowed.filters(), cells=(Met(other=b.name),)),
        parts=(Part(body=Grouped(by="subject")),),
        notes=notes,
        facts=MatchupFacts(teammate_games=together, absence=_pair_absence(con, q, compiled, a)),
    )


def _pair_absence(con: duckdb.DuckDBPyConnection, q: Query, compiled: Compiled, a: Entity) -> dict[str, Any] | None:
    """Why a matchup narrowed by a teammate's absence holds no meetings, in
    the numbers that answer the question a reader probably meant: "steph
    curry record vs lebron regular season without kd" (yardstick-v2 F114)
    is "no meetings" because "without Durant" counts only the games inside
    Durant's time as Curry's teammate, and Durant played every Curry-LeBron
    meeting in it - while over their careers the two met many more times.
    The same pair compiled without the absence, and with the teammates
    playing beside the first player: how often, over which seasons, and
    how many of those with them. ``None`` where no absence was named, or
    the two never met at all."""
    absent = compiled.narrowed.without
    if not absent:
        return None
    unconditioned = replace(q.scope, companions=())
    playing = tuple(Companion(player=mate.name, side="own", predicate="played") for mate in absent)
    try:
        everywhere = rows_of(con, compile_query(con, replace(q, scope=unconditioned)))
    except Refused, Unsupported:
        return None
    if not everywhere:
        return None
    try:
        beside = len(rows_of(con, compile_query(con, replace(q, scope=replace(unconditioned, companions=playing)))))
    except Refused, Unsupported:
        beside = 0
    seasons = [row["season"] for row in everywhere]
    return {"met": len(everywhere), "first": min(seasons), "last": max(seasons), "beside": beside, "names": [mate.name for mate in absent], "player": a.name}
