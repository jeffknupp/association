"""The record-over-a-line reader: a player's team's record in the games he
reached a line, the games he fell short, and all of them, read into a
:class:`~association.query.result.Result` with a :class:`~association.query.result.Grouped`
body (``record_when``'s own point). Phase 2's slice (i): the retired
template's read (``templates.splits._record_when_query``) moved here whole
over the compiler's settled player and games, its remarks as kinds and
facts; the sayer (:mod:`association.query.compose.say`) words it. The team
branch - a team's record above and below its OWN line - is still
``compose.present.present_team``'s until the team slice.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import duckdb

from association.nba.franchises import season_name
from association.query.conditions import _PLAYER_GAME_TABLES, _names, _unseen, box_source
from association.query.notes import Note
from association.query.player_games import games_subquery
from association.query.reading import Scope
from association.query.result import Grouped, Narrowing, Part, Result, Span
from association.query.templates.common import THRESHOLD_STAT_COLUMNS, TemplateResult, condition_scope, no_games, optional_team, unhonored_scoping
from association.query.templates.splits import _condition_span_label

from .core import Query, compile_query


def _group(by_hit: dict[bool | None, Any], hit: bool | None) -> dict[str, Any]:
    """The record in the games the threshold was reached (True), fell short of
    with a known value (False), or every game regardless of whether the value
    is known at all (None) - a blank-stat game still has a real result, so
    "every game" counts it too."""
    rows = [by_hit[h] for h in ((hit,) if hit is not None else (True, False, None)) if h in by_hit]
    games = sum(int(r[1]) for r in rows)
    wins = sum(int(r[2]) for r in rows)
    margin = sum((r[3] or 0) * int(r[1]) for r in rows) / games if games else None
    return {"games": games, "wins": wins, "losses": games - wins, "avg_margin": margin}


def _own_line(q: Query, stated: frozenset[str]) -> tuple[str, str, int] | None:
    """The stat, its column and the threshold where ``q`` is ``record_when``'s
    own point - a scalar record on the player relation with that one
    predicate, narrowed only by what the template's words state - else None."""
    scope = q.scope
    if unhonored_scoping("record_when", scope, stated):
        return None
    stat = scope.stat
    column = THRESHOLD_STAT_COLUMNS.get(stat) if stat is not None else None
    threshold = scope.threshold
    if stat is None or column is None or threshold is None or threshold < 1:
        return None
    if q.skeleton != "scalar" or q.aggregate != "record" or q.subject != "player" or q.predicates != [(column, ">=", threshold)] or [m for m in q.measures if m != column]:
        return None
    return stat, column, threshold


def read_record_when(con: duckdb.DuckDBPyConnection, q: Query, *, stated: frozenset[str]) -> Result | TemplateResult | None:
    """``record_when``'s own point - a scalar record on the player relation
    with the one predicate ``column >= threshold`` - read as the three-row
    record (reached, fell short, all his games) with the teams' names, the
    span the heading says and the remarks: the games with no box score, a
    blank stat on a rebuilt row, the pool he played, the coverage floor.
    ``None`` where the point is not that, or carries a narrowing the
    template's words did not state (``stated``: ``compose.present.STATED_SCOPING``'s
    set), and the compiler's sentence answers; a
    :class:`~association.query.templates.common.TemplateResult` back is the
    relation's refusal (no games in scope, an ambiguous team).

    .. versionadded:: 5.0.0
    """
    scope = q.scope
    line = _own_line(q, stated)
    if line is None:
        return None
    stat, column, threshold = line
    # The template's own scope - it names the span in the heading, the floor
    # note and the unseen-games count - read off the same slots the same way.
    covered = condition_scope(scope.season, "career" if scope.season_n else scope.span, scope.season_type, _PLAYER_GAME_TABLES, since=scope.since)
    compiled = compile_query(con, replace(q, predicates=[], measures=[]))
    if compiled.player is None:
        return None
    team = optional_team(con, scope.team, season=scope.season)
    if isinstance(team, TemplateResult):
        return team
    player, narrowed = compiled.player, compiled.narrowed
    base, params = games_subquery(narrowed, box_source(con))
    found = con.execute(
        f"WITH p AS ({base}) SELECT p.{column} >= ?, COUNT(*), COUNT(*) FILTER (WHERE p.won), AVG(p.team_score - p.opponent_score), "
        "MIN(p.season), MAX(p.season), list(DISTINCT p.team_id) FROM p GROUP BY 1",
        [*params, threshold],
    ).fetchall()
    if not found:
        return no_games(con, player, covered, team)
    team_ids = {str(t) for row in found for t in row[6]}
    # One season names each team as it was then. A career groups every season
    # of an id together, so it keeps today's name rather than picking one era.
    names = {team_id: season_name(team_id, covered.season, name) for team_id, name in _names(con, "teams", "team_id", team_ids).items()}
    return _record_result(con, scope, covered, player.name, narrowed.filters(), stat, threshold, found, names, base, params)


def _record_result(
    con: duckdb.DuckDBPyConnection, scope: Scope, covered: Any, player: str, narrowing: str, stat: str, threshold: int, found: list[Any], names: dict[str, str], base: str, params: Any
) -> Result:
    """The three rows and the remarks, from the grouped query's rows -
    :func:`read_record_when`'s tail, split out for the complexity gate."""
    by_hit: dict[bool | None, Any] = {row[0]: row for row in found}
    first, last = min(r[4] for r in found), max(r[5] for r in found)
    groups = Grouped(
        by="threshold",
        rows=(
            {"key": "reached", **_group(by_hit, True)},
            {"key": "short", **_group(by_hit, False)},
            {"key": "all", **_group(by_hit, None)},
        ),
    )
    # The remarks, in the order the template wrote them: the unseen games
    # and the blank stat (its caveat), the pool, the floor.
    notes: list[Note] = []
    unseen = _unseen(con, covered, base, params, box_source(con))
    if unseen:
        notes.append(Note("games_unseen", {"games": unseen, "why": "no_box_score", "whose": "his team's"}))
    blank = by_hit.get(None)
    if blank is not None and int(blank[1]):
        notes.append(Note("stat_blank", {"games": int(blank[1]), "stat": stat, "whose": "player"}))
    notes.append(Note("definition", {"term": "pool", "games": groups.rows[2]["games"], "what": "games_he_played"}))
    if covered.season is None and first == covered.first:
        notes.append(Note("floor", {"table": "box_scores", "first": covered.first, "what": covered.kind}))
    return Result(
        subject=player,
        relation="player",
        span=Span(season=covered.season, season_type=covered.season_type, career=covered.season is None, first=first, last=last, phrase=_condition_span_label(covered, scope, first, last)),
        narrowing=Narrowing(phrase=narrowing),
        parts=(Part(body=groups),),
        notes=tuple(notes),
        facts={"stat": stat, "threshold": threshold, "teams": sorted(names.values())},
    )
