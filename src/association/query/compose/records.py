"""The record-over-a-line reader: a player's team's record in the games he
reached a line, the games he fell short, and all of them, read into a
:class:`~association.query.result.Result` with a :class:`~association.query.result.Grouped`
body (``record_when``'s own point). Phase 2's slice (i): the retired
template's read (``templates.splits._record_when_query``) moved here, then
replaced by the compiled statement - the point as a grouped read by the
line, the compiler's ``line`` group - its remarks as kinds and facts; the
sayer (:mod:`association.query.compose.say`) words it. The team
branch - a team's record above and below its OWN line - is
:func:`read_team_record_when`, over the team compiler's ``line`` statement
(:func:`~association.query.compose.team.compile_team_line`), since the
team slice (the presenter ``compose.present.present_team`` until then).

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import duckdb

from association.nba.franchises import season_name
from association.query.answer import Reply
from association.query.conditions import _PLAYER_GAME_TABLES, _names, _unseen, box_source
from association.query.notes import Note
from association.query.player_games import games_subquery
from association.query.reading import Scope, Unsupported
from association.query.result import Grouped, Narrowing, Part, Result, Span
from association.query.team_games import TeamNarrowed
from association.query.templates.common import STAT_LABELS, THRESHOLD_STAT_COLUMNS, check_coverage, condition_scope, no_games, optional_team, span_of, team_games, unhonored_scoping, whole_span
from association.query.templates.splits import condition_needs_player_refusal, condition_span_label, condition_team_no_games, team_span_label, team_where_in

from .core import Compiled, Query, compile_query, rows_of
from .team import TEAM_BOX_COLUMNS, TeamQuery, compile_team_count, compile_team_line


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


def read_record_when(con: duckdb.DuckDBPyConnection, q: Query, *, stated: frozenset[str]) -> Result | Reply | None:
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
    words did not state (``stated``: ``compose.plan.STATED_SCOPING``'s
    set), and the compiler's sentence answers; a
    :class:`~association.query.answer.Reply` back is the
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
    if isinstance(team, Reply):
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


def read_team_record_when(con: duckdb.DuckDBPyConnection, q: TeamQuery, *, stated: frozenset[str]) -> Result | Reply | None:
    """``record_when``'s team half: a team's record when its OWN figure for
    a stat reached a line, fell short of it, and over every game with a
    result, reached when the question names no player at all ("what was the
    celtics record when they scored 120 points" - ISSUES.md #144). The
    games narrowed through the shared steps, over every game in the span;
    the record the team compiler's ``line`` statement
    (:func:`~association.query.compose.team.compile_team_line`). ``None``
    where the point carries no line or a narrowing the words do not state
    (``stated``); a
    :class:`~association.query.answer.Reply` back is the
    relation's refusal (the coverage floor, no such team, no games in the
    span or none matching its narrowing, none with a figure for the stat).
    A player-only cell, an unknown stat or one a team has no figure for is
    declined by name (``Unsupported``), never answered as another
    question.

    .. versionadded:: 5.0.0
    """
    scope = q.scope
    if scope.threshold is None or unhonored_scoping("record_when", scope, stated):
        return None
    refused = check_coverage("record_when", scope)
    if refused is not None:
        return Reply(data={"message": refused, "season": scope.season}, answer=refused)
    condition_needs_player_refusal("record_when", scope)
    team = optional_team(con, scope.team, season=scope.season)
    if isinstance(team, Reply):
        return team
    if team is None:
        raise Unsupported("record_when needs a player or a team")
    stat, threshold = _record_when_team_stat(scope.stat, scope.threshold)
    span = span_of(scope.span, scope.season, scope.season_type or 2, "games", since=scope.since, until=scope.until)
    narrowed = team_games(con, team, span, scope, opponent=scope.opponent)
    if isinstance(narrowed, Reply):
        return narrowed
    # A split, a record, a run: read over every game in the span (common.whole_span).
    whole_span(narrowed)
    # The narrowed pool BEFORE any stat availability is checked, so a team
    # with real games in this span/narrowing but none carrying the stat is
    # told apart from a team with no games matching the narrowing at all.
    (matched,) = rows_of(con, compile_team_count(narrowed, team, span))
    if not matched["games"]:
        return condition_team_no_games(con, team, span, narrowed)
    found = rows_of(con, compile_team_line(narrowed, team, span, stat, threshold))
    if not found:
        return _record_when_team_no_stat(team, span, narrowed, stat, int(matched["games"]))
    return _record_when_team_result(con, span, team, narrowed, stat, threshold, found)


def _record_when_team_stat(stat: str | None, threshold: int | None) -> tuple[str, int]:
    """The stat a team's line is read on, and the threshold narrowed to
    ``int`` - or the decline, which names the real cause rather than the
    player branch's "needs a player" (AGENTS.md, "the same bug has a mirror
    image"): an unknown stat or a bad threshold reads as the player branch's
    own, and a stat that is only ever a PLAYER's (a team has no minutes
    total) says so by name."""
    if threshold is None or threshold < 1 or stat is None or stat not in THRESHOLD_STAT_COLUMNS:
        raise Unsupported(f"record_when needs a known stat and a positive threshold, got {stat!r}/{threshold!r}")
    if stat not in TEAM_BOX_COLUMNS:
        # The only whitelisted player stat with no team figure.
        raise Unsupported(f"record_when has no team figure for {STAT_LABELS.get(stat, stat)}s - a team has no minutes total")
    return stat, threshold


def _record_when_team_no_stat(team: Any, span: Any, narrowed: TeamNarrowed, stat: str, games: int) -> Reply:
    """The team played ``games`` games under this narrowing, but not one of
    them carries a figure for the stat - the empty 2013-2018 team boxes,
    reached through a line rather than a plain average. Distinct from
    ``condition_team_no_games``, which says there are no narrowed games at
    all."""
    unit = f"{STAT_LABELS.get(stat, stat)}s"
    which = "it" if games == 1 else "any of them"
    message = f"The warehouse has {games} game{'' if games == 1 else 's'} with a result for the {team.name}{narrowed.filters()} {team_where_in(span)}, but no {unit} figure on record for {which}."
    return Reply(data={"team": team.name, "span": team_span_label(span), "games": 0}, answer=message)


def _record_when_team_result(con: duckdb.DuckDBPyConnection, span: Any, team: Any, narrowed: TeamNarrowed, stat: str, threshold: int, found: list[dict[str, Any]]) -> Result:
    """The three rows - reached, fell short, every game with a figure - and
    the remarks in the order the template wrote them: the games with no
    figure for the stat (never for ``points``, which reads the score), the
    pool, the box-score floor."""
    by_hit = {("reached" if row["reached"] else "short"): row for row in found}
    first, last = min(r["first_season"] for r in found), max(r["last_season"] for r in found)
    groups = Grouped(
        by="threshold",
        rows=({"key": "reached", **_group(by_hit, "reached")}, {"key": "short", **_group(by_hit, "short")}, {"key": "all", **_group(by_hit, "reached", "short")}),
    )
    notes: list[Note] = []
    if stat != "points":
        (blank,) = rows_of(con, compile_team_count(narrowed, team, span, blank=stat))
        if blank["games"]:
            notes.append(Note("stat_blank", {"games": int(blank["games"]), "stat": stat, "whose": "team"}))
    notes.append(Note("definition", {"term": "pool", "games": groups.rows[2]["games"], "what": "games_with_a_result"}))
    if span.season is None and span.since is None and first == span.first:
        notes.append(Note("floor", {"table": "box_scores", "first": span.first, "what": span.kind}))
    return Result(
        subject=team.name,
        relation="team",
        span=Span(season=span.season, season_type=span.season_type, career=span.season is None, first=first, last=last, phrase=team_span_label(span, first, last)),
        narrowing=Narrowing(phrase=narrowed.filters()),
        parts=(Part(body=groups),),
        notes=tuple(notes),
        facts={"stat": stat, "threshold": threshold},
    )
