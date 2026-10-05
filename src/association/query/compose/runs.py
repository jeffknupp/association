"""The streak reader: a named player's longest runs of consecutive games one
condition held along, or the league's longest one per player, read into a
:class:`~association.query.result.Result` with a :class:`~association.query.result.Runs`
body (``streak``'s own point on the player relation). Phase 2's slice
(ii): the compiler already read the runs (the ``run`` shape,
:func:`~association.query.compose.core._compile_run`) and the retired
template's words said them through a presenter; the reader executes that
statement, carries the span, the rule and the still-open run as values,
and the sayer (:mod:`association.query.compose.say`) words it. A team's
run of wins or losses, and the league's one per team-season, are the team
relation's (``compose.present._present_team_streak``) until the team
slice.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from typing import Any

import duckdb

from association.nba.season import current_season
from association.query.conditions import _box_missing, _names, _totals, _unseen, box_source
from association.query.entities import Entity
from association.query.measures import streak_column
from association.query.notes import Note
from association.query.player_games import Narrowed, games_subquery, named
from association.query.result import Narrowing, Part, Result, Runs, Span, run_of
from association.query.templates.common import TemplateResult, no_games, optional_team, unhonored_scoping
from association.query.templates.splits import condition_span_label

from .core import Compiled, Query, compile_query, rows_of, run_scope


def read_streak(con: duckdb.DuckDBPyConnection, q: Query, *, stated: frozenset[str]) -> Result | TemplateResult | None:
    """``streak``'s own point on the player relation - the ``run`` shape -
    read as the longest runs the compiled statement finds: a named
    player's longest and any that tie it, over the games the relation
    narrowed to ("longest run of 20+ point games vs Boston" is a run over
    his Boston games only), or the league's longest, one per player. The
    span, the rule ("only games he played count") and the games with no
    box score are read off the same relation the runs were. ``None`` where
    the point is not a run, or carries a narrowing the retired template's
    words did not state (``stated``: ``compose.present.STATED_SCOPING``'s
    set), and the compiler's sentence answers; a
    :class:`~association.query.templates.common.TemplateResult` back is the
    relation's refusal (no games in scope, an ambiguous team).

    .. versionadded:: 5.0.0
    """
    if q.skeleton != "run" or q.source != "games":
        return None
    scope = q.scope
    if unhonored_scoping("streak", scope, stated):
        return None
    team = optional_team(con, scope.team, season=scope.season)
    if isinstance(team, TemplateResult):
        return team
    covered = run_scope(scope, named=q.subject == "player")
    compiled = compile_query(con, q)
    runs = rows_of(con, compiled)
    if compiled.player is not None:
        return _streak_player_result(con, q, covered, compiled.player, compiled.narrowed, team, runs)
    return _streak_league_result(con, q, covered, compiled, runs)


def _streak_facts(q: Query) -> dict[str, Any]:
    """What the sayer words a run with: the stat and its line, or a run of
    wins or losses."""
    scope = q.scope
    return {"stat": scope.stat, "threshold": scope.threshold, "by_stat": streak_column(scope.stat, scope.threshold) is not None, "want_win": scope.kind != "loss"}


def _streak_player_result(con: duckdb.DuckDBPyConnection, q: Query, covered: Any, player: Entity, narrowed: Narrowed, team: Entity | None, rows: list[dict[str, Any]]) -> Result | TemplateResult:
    """A named player's runs, the span his narrowed games cover, and the
    remarks: the rule, a game with no box score ending a run, and the
    longest still going (said only of this season or a career, where "the
    last game on record" is his latest)."""
    box = box_source(con)
    # Named parameters: the played subquery binds beside `covered`'s own
    # $season/$first in the unseen-games read.
    base, params = named(*games_subquery(narrowed, box))
    games, first, last = _totals(con, base, params)
    if not games:
        return no_games(con, player, covered, team)
    runs = tuple(run_of(row) for row in rows)
    notes: list[Note] = []
    if runs:
        # The rule and the unseen games qualify a run; a player with none has nothing for them to qualify.
        notes.append(Note("definition", {"term": "streak_rule", "what": "player_games_played", "across_seasons": covered.season is None}))
        if _unseen(con, covered, base, {**params, **covered.params()}, box):
            notes.append(Note("definition", {"term": "unseen_ends_run"}))
        if runs[0].still_open and (covered.season is None or covered.season == current_season()):
            notes.append(Note("still_open"))
    return Result(
        subject=player.name,
        relation="player",
        span=Span(season=covered.season, season_type=covered.season_type, career=covered.season is None, first=first, last=last, phrase=condition_span_label(covered, q.scope, first, last)),
        narrowing=Narrowing(phrase=narrowed.filters()),
        parts=(Part(body=Runs(runs=runs)),),
        notes=tuple(notes),
        facts=_streak_facts(q),
    )


def _streak_league_result(con: duckdb.DuckDBPyConnection, q: Query, covered: Any, compiled: Compiled, rows: list[dict[str, Any]]) -> Result:
    """The league's runs, one per player under his name, the span searched
    (not the seasons the leaders' runs happen to fall in: "1997-2023" under
    a question about every season reads as a narrower search) and the
    remarks: the rule, and a game with no box score ending a run."""
    names = _names(con, "players", "athlete_id", [row["athlete_id"] for row in rows])
    runs = tuple(run_of(row, names[row["athlete_id"]]) for row in rows)
    notes: list[Note] = []
    if runs:
        notes.append(Note("definition", {"term": "streak_rule", "what": "league_player_games_played", "across_seasons": covered.season is None}))
        if _totals(con, _box_missing(covered, box_source(con)), covered.params())[0]:
            notes.append(Note("definition", {"term": "unseen_ends_run"}))
        if any(run.still_open for run in runs):
            notes.append(Note("still_open"))
    _, first, last = _totals(con, compiled.run_rows or "", compiled.run_params or {})
    return Result(
        subject="the league",
        relation="everyone",
        span=Span(season=covered.season, season_type=covered.season_type, career=covered.season is None, first=first, last=last, phrase=covered.label(first, last), floor=covered.first),
        parts=(Part(body=Runs(runs=runs)),),
        notes=tuple(notes),
        facts=_streak_facts(q),
    )
