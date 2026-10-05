"""The record-over-a-line reader: a player's team's record in the games he
reached a line, the games he fell short, and all of them, read into a
:class:`~association.query.result.Result` with a :class:`~association.query.result.Grouped`
body (``record_when``'s own point). Phase 2's slice (i): the retired
template's read (``templates.splits._record_when_query``) moved here, then
replaced by the compiled statement - the point as a grouped read by the
line, the compiler's ``line`` group - its remarks as kinds and facts; the
sayer (:mod:`association.query.compose.say`) words it. The team
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
from association.query.templates.splits import condition_span_label

from .core import Compiled, Query, compile_query, rows_of


def _group(by_hit: dict[str, dict[str, Any]], *keys: str) -> dict[str, Any]:
    """The record over the groups named - ``reached``; ``short``; or all
    three, since a blank-stat game still has a real result and "every game"
    counts it too. A loss is a game not won, as the template counted them."""
    rows = [by_hit[k] for k in keys if k in by_hit]
    games = sum(int(r["games"]) for r in rows)
    wins = sum(int(r["wins"]) for r in rows)
    margin = sum((r["margin"] or 0) * int(r["games"]) for r in rows) / games if games else None
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
    The statement is the compiler's: the point compiled as a ``grouped``
    read by the ``line`` (:func:`~association.query.compose.core._line_group`
    - the predicate as the key the games are divided by, the margin as the
    measure), one statement over the relation's narrowed games. ``None``
    where the point is not that, or carries a narrowing the template's
    words did not state (``stated``: ``compose.present.STATED_SCOPING``'s
    set), and the compiler's sentence answers; a
    :class:`~association.query.templates.common.TemplateResult` back is the
    relation's refusal (no games in scope, an ambiguous team).

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Executes the compiled statement (Phase 2, step 1's merge); until
       then it ran the retired template's own grouped statement beside it.
    """
    scope = q.scope
    line = _own_line(q, stated)
    if line is None:
        return None
    stat, _column, threshold = line
    # The template's own scope - it names the span in the heading, the floor
    # note and the unseen-games count - read off the same slots the same way.
    covered = condition_scope(scope.season, "career" if scope.season_n else scope.span, scope.season_type, _PLAYER_GAME_TABLES, since=scope.since)
    compiled = compile_query(con, replace(q, skeleton="grouped", group="line", measures=["margin"]))
    if compiled.player is None:
        return None
    team = optional_team(con, scope.team, season=scope.season)
    if isinstance(team, TemplateResult):
        return team
    found = rows_of(con, compiled)
    if not found:
        return no_games(con, compiled.player, covered, team)
    team_ids = {str(t) for row in found for t in row["team_ids"]}
    # One season names each team as it was then. A career groups every season
    # of an id together, so it keeps today's name rather than picking one era.
    names = {team_id: season_name(team_id, covered.season, name) for team_id, name in _names(con, "teams", "team_id", team_ids).items()}
    return _record_result(con, scope, covered, compiled, stat, threshold, found, names)


def _record_result(con: duckdb.DuckDBPyConnection, scope: Scope, covered: Any, compiled: Compiled, stat: str, threshold: int, found: list[dict[str, Any]], names: dict[str, str]) -> Result:
    """The three rows and the remarks, from the grouped statement's rows -
    :func:`read_record_when`'s tail, split out for the complexity gate."""
    assert compiled.player is not None
    by_hit = {row["group"]: row for row in found}
    first, last = min(r["first_season"] for r in found), max(r["last_season"] for r in found)
    groups = Grouped(
        by="threshold",
        rows=(
            {"key": "reached", **_group(by_hit, "reached")},
            {"key": "short", **_group(by_hit, "short")},
            {"key": "all", **_group(by_hit, "reached", "short", "blank")},
        ),
    )
    # The remarks, in the order the template wrote them: the unseen games
    # and the blank stat (its caveat), the pool, the floor.
    notes: list[Note] = []
    box = box_source(con)
    base, params = games_subquery(compiled.narrowed, box)
    unseen = _unseen(con, covered, base, params, box)
    if unseen:
        notes.append(Note("games_unseen", {"games": unseen, "why": "no_box_score", "whose": "his team's"}))
    blank = by_hit.get("blank")
    if blank is not None and int(blank["games"]):
        notes.append(Note("stat_blank", {"games": int(blank["games"]), "stat": stat, "whose": "player"}))
    notes.append(Note("definition", {"term": "pool", "games": groups.rows[2]["games"], "what": "games_he_played"}))
    if covered.season is None and first == covered.first:
        notes.append(Note("floor", {"table": "box_scores", "first": covered.first, "what": covered.kind}))
    return Result(
        subject=compiled.player.name,
        relation="player",
        span=Span(season=covered.season, season_type=covered.season_type, career=covered.season is None, first=first, last=last, phrase=condition_span_label(covered, scope, first, last)),
        narrowing=Narrowing(phrase=compiled.narrowed.filters()),
        parts=(Part(body=groups),),
        notes=tuple(notes),
        facts={"stat": stat, "threshold": threshold, "teams": sorted(names.values())},
    )
