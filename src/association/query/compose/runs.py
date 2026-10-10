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
compiler's ``run`` shape (:func:`~association.query.compose.team.compile_team_run`),
read by :func:`read_team_streak` into the same ``Runs`` body - one reader
per relation, one sayer for every subject (Phase 2's slice (iv): the
presenter ``compose.present._present_team_streak`` until then).

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import duckdb

from association.nba.franchises import season_name
from association.nba.season import current_season
from association.query.conditions import _box_missing, _names, _totals, _unseen, box_source, condition_span_label
from association.query.entities import Entity, optional_team
from association.query.lines import threshold_of
from association.query.measures import streak_column
from association.query.notes import Note
from association.query.player_games import Narrowed, games_subquery, named
from association.query.player_relation import no_games
from association.query.reading import unhonored_scoping
from association.query.result import Line, Narrowing, Part, Result, Run, Runs, Span, Unanswered, run_of
from association.query.team_relation import condition_team_no_games, team_span_label

from .core import Compiled, Query, compile_query, rows_of, run_scope
from .team import TeamCompiled, TeamQuery, compile_team_range, compile_team_run, team_coverage_refusal


def _settled_open(runs: tuple[Run, ...]) -> tuple[Run, ...]:
    """The runs with ``still_open`` meaning what the answer says of it: a run
    that reached its partition's last game is still going only where that
    partition is the season on record now (ISSUES.md #297). Before, the
    flag was the SQL's alone - the run reached the last game - so a run that
    ended with a finished season, or at a retired player's last game, was
    listed "(still going)" wherever the sayer did not guard it."""
    now = current_season()
    return tuple(replace(run, still_open=run.still_open and run.last_season == now) for run in runs)


def read_streak(con: duckdb.DuckDBPyConnection, q: Query, *, stated: frozenset[str]) -> Result | Unanswered | None:
    """``streak``'s own point on the player relation - the ``run`` shape -
    read as the longest runs the compiled statement finds: a named
    player's longest and any that tie it, over the games the relation
    narrowed to ("longest run of 20+ point games vs Boston" is a run over
    his Boston games only), or the league's longest, one per player. The
    span, the rule ("only games he played count") and the games with no
    box score are read off the same relation the runs were. ``None`` where
    the point is not a run, or carries a narrowing the retired template's
    words did not state (``stated``: ``compose.plan.STATED_SCOPING``'s
    set), and the compiler's sentence answers; a
    :class:`~association.query.result.Refusal` or :class:`~association.query.result.Clarify` back is the
    relation's refusal (no games in scope, an ambiguous team).

    .. versionadded:: 5.0.0
    """
    if q.skeleton != "run" or q.source != "games":
        return None
    scope = q.scope
    if unhonored_scoping("streak", scope, stated):
        return None
    team = optional_team(con, scope.team, season=scope.span.season)
    if isinstance(team, Unanswered):
        return team
    covered = run_scope(scope, named=q.subject == "player")
    compiled = compile_query(con, q)
    runs = rows_of(con, compiled)
    if compiled.player is not None:
        return _streak_player_result(con, q, covered, compiled.player, compiled.narrowed, team, runs)
    return _streak_league_result(con, q, covered, compiled, runs)


def _streak_runs(q: Query, runs: tuple[Run, ...]) -> Runs:
    """The runs, with what they held along: the stat's line, or a run of
    wins or losses."""
    scope = q.scope
    threshold = threshold_of(scope)
    line = Line(column=scope.stat, value=threshold) if streak_column(scope.stat, threshold) is not None else None
    return Runs(runs=runs, line=line, won=scope.kind != "loss")


def _streak_player_result(con: duckdb.DuckDBPyConnection, q: Query, covered: Any, player: Entity, narrowed: Narrowed, team: Entity | None, rows: list[dict[str, Any]]) -> Result | Unanswered:
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
    runs = _settled_open(tuple(run_of(row) for row in rows))
    notes: list[Note] = []
    if runs:
        # The rule and the unseen games qualify a run; a player with none has nothing for them to qualify.
        notes.append(Note("definition", {"term": "streak_rule", "what": "player_games_played", "across_seasons": covered.season is None}))
        if _unseen(con, covered, base, {**params, **covered.params()}, box):
            notes.append(Note("definition", {"term": "unseen_ends_run"}))
        if runs[0].still_open:
            notes.append(Note("still_open"))
    return Result(
        subject=player.name,
        relation="player",
        span=Span(season=covered.season, season_type=covered.season_type, career=covered.season is None, first=first, last=last, phrase=condition_span_label(covered, q.scope, first, last)),
        narrowing=Narrowing(phrase=narrowed.filters()),
        parts=(Part(body=_streak_runs(q, runs)),),
        notes=tuple(notes),
    )


def _streak_league_result(con: duckdb.DuckDBPyConnection, q: Query, covered: Any, compiled: Compiled, rows: list[dict[str, Any]]) -> Result:
    """The league's runs, one per player under his name, the span searched
    (not the seasons the leaders' runs happen to fall in: "1997-2023" under
    a question about every season reads as a narrower search) and the
    remarks: the rule, and a game with no box score ending a run."""
    names = _names(con, "players", "athlete_id", [row["athlete_id"] for row in rows])
    runs = _settled_open(tuple(run_of(row, names[row["athlete_id"]]) for row in rows))
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
        parts=(Part(body=_streak_runs(q, runs)),),
        notes=tuple(notes),
    )


def read_team_streak(con: duckdb.DuckDBPyConnection, q: TeamQuery, *, stated: frozenset[str]) -> Result | Unanswered | None:
    """``streak``'s point on the team relation - the ``run`` shape - read
    as the runs the team compiler's statement finds
    (:func:`~association.query.compose.team.compile_team_run`): a named
    team's longest run of wins or losses within a season and any that tie
    it, over its games narrowed to an opponent or a venue where the
    question named them, or the league's longest, one per team-season, each
    under the team as it was named that season. The span is the seasons the
    games came from (:func:`~association.query.compose.team.compile_team_range`).
    ``None`` where the point is not a run or carries a narrowing the retired
    template's words did not state (``stated``), and the compiler's
    sentence answers; a
    :class:`~association.query.result.Refusal` or :class:`~association.query.result.Clarify` back is the
    relation's refusal (a coverage floor, a named team with no games in the
    span or none matching its narrowing).

    .. versionadded:: 5.0.0
    """
    if q.shape != "run":
        return None
    if unhonored_scoping("streak", q.scope, stated):
        return None
    refused = team_coverage_refusal(q)
    if refused is not None:
        return refused
    compiled = compile_team_run(con, q)
    (found,) = rows_of(con, compile_team_range(compiled))
    games = int(found["games"])
    if compiled.team is not None and not games:
        # Which fact is missing: the team's games in the span at all, or the
        # match to an opponent/venue narrowing.
        return condition_team_no_games(con, compiled.team, compiled.span, compiled.narrowed)
    rows = rows_of(con, compiled) if games else []
    if compiled.team is not None:
        return _streak_team_result(q, compiled, found, rows)
    return _streak_league_teams_result(con, q, compiled, found, rows)


def _streak_team_runs(q: TeamQuery, runs: tuple[Run, ...]) -> Runs:
    """A team's runs of wins or losses: no stat and no line."""
    return Runs(runs=runs, won=q.scope.kind != "loss")


def _streak_team_result(q: TeamQuery, compiled: TeamCompiled, found: dict[str, Any], rows: list[dict[str, Any]]) -> Result:
    """A named team's runs and the remarks: the rule (a run is counted
    within one season), and the longest still going - said only of this
    season or every season, where "the last game on record" is its latest."""
    team, span = compiled.team, compiled.span
    assert team is not None
    first, last = found["first_season"], found["last_season"]
    runs = _settled_open(tuple(run_of(row) for row in rows))
    notes: list[Note] = []
    if runs:
        notes.append(Note("definition", {"term": "streak_rule", "what": "team_within_season"}))
        if runs[0].still_open:
            notes.append(Note("still_open"))
    return Result(
        subject=team.name,
        relation="team",
        span=Span(season=span.season, season_type=span.season_type, career=span.season is None, first=first, last=last, phrase=team_span_label(span, first, last)),
        narrowing=Narrowing(phrase=compiled.narrowed.filters()),
        parts=(Part(body=_streak_team_runs(q, runs)),),
        notes=tuple(notes),
    )


def _streak_league_teams_result(con: duckdb.DuckDBPyConnection, q: TeamQuery, compiled: TeamCompiled, found: dict[str, Any], rows: list[dict[str, Any]]) -> Result:
    """The league's runs, one per team-season, each under the team as it was
    named that season (with the season where the span covers several), and
    the rule beneath them."""
    span = compiled.span
    names = _names(con, "teams", "team_id", [row["team_id"] for row in rows])
    # Every run lies inside one season, so each is named as its team was
    # that season. This used to add "franchises are named as they are
    # today" to every all-seasons answer, which is what it was.
    runs = _settled_open(tuple(run_of(row, season_name(row["team_id"], int(row["season"]), names[row["team_id"]]) + (f" ({row['season']})" if span.season is None else "")) for row in rows))
    notes: list[Note] = []
    if runs:
        # The rule qualifies a run: with none, the answer says so and nothing beneath it.
        notes.append(Note("definition", {"term": "streak_rule", "what": "league_team_within_season"}))
        if any(run.still_open for run in runs):
            notes.append(Note("still_open"))
    first, last = found["first_season"], found["last_season"]
    return Result(
        subject="the league",
        relation="everyone",
        span=Span(season=span.season, season_type=span.season_type, career=span.season is None, first=first, last=last, phrase=team_span_label(span, first, last), floor=span.first),
        parts=(Part(body=_streak_team_runs(q, runs)),),
        notes=tuple(notes),
    )
