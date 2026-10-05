"""A team's own season, read: its line and where it ranks (``team_stat``),
every team ranked by one metric of the line or the standings
(``team_leaderboard``), and its place in ESPN's power index
(``team_outlook``) - the two team-season relations of ``ROADMAP-TYPES.md``
(``team_seasons``, ``team_snapshots``), beside the team-games relation the
team compiler reads (:mod:`~association.query.compose.team`).

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import duckdb

from association.nba.season import current_season
from association.query.entities import Entity
from association.query.notes import Note
from association.query.reading import Scope, Unsupported
from association.query.result import Grouped, Part, Result, Scalar, Span
from association.query.team_metrics import DEFAULT_TEAM_LINE, TEAM_METRICS, TeamLine, TeamMetric, descending_for, ranked, resolve_team_metric
from association.query.team_seasons import (
    BPI_CHANCES,
    BPI_SNAPSHOT_NAMES,
    snapshot_facts,
    team_lines,
    team_lines_statement,
    team_outlook_chosen,
    team_outlook_row_statement,
    team_outlook_snapshots_statement,
    team_records,
    team_records_statement,
)
from association.query.templates.common import TemplateResult, check_coverage, resolved_team, slot_season, unhonored_scoping
from association.query.templates.teams import conference_refusal

from .core import Refused, values_of


@dataclass(frozen=True, kw_only=True)
class TeamSeasonQuery:
    """A point on a team-season relation: the question's scoping, the
    relation (``team_seasons`` for the line and the standings,
    ``team_snapshots`` for the power index) and the shape (one team's
    ``scalar``, or every team ``grouped`` by the metric ranked). Which
    metric the ``stat`` slot names is the reader's to resolve, as it was the
    retired templates'. The team-season counterpart of
    :class:`~association.query.compose.team.TeamQuery`.

    .. versionadded:: 5.0.0
    """

    #: The question's scoping, read whole by the relation's reader.
    scope: Scope
    relation: Literal["team_seasons", "team_snapshots"] = "team_seasons"
    shape: Literal["scalar", "grouped"] = "scalar"


def team_season_declines(intent: str, scope: Scope, stated: frozenset[str]) -> str | None:
    """Why a team-season intent's reader cannot answer ``scope`` as asked - a
    narrowing its words do not state (``stated``,
    ``compose.present.STATED_SCOPING``), in the retired template's sentence
    (``check_scope``'s) - or None. The first thing each reader checks,
    before the coverage floor, as ``check_scope`` ran before the template.

    .. versionadded:: 5.0.0
    """
    ignored = unhonored_scoping(intent, scope, stated)
    return f"{intent} cannot honor {ignored} - it would answer for a different span than was asked" if ignored else None


def _team_season_subject(con: duckdb.DuckDBPyConnection, intent: str, scope: Scope, stated: frozenset[str]) -> Entity | TemplateResult:
    """The team a one-team team-season read is about, after the checks the
    answering loop and the retired template made first, in their order: a
    narrowing the words do not state (a decline), the coverage floor, a
    conference or division in a team slot, then the team itself (a
    clarifying question, or a decline where none matches)."""
    declined = team_season_declines(intent, scope, stated)
    if declined is not None:
        raise Unsupported(declined)
    refused = check_coverage(intent, scope)
    if refused is not None:
        raise Refused(TemplateResult(data={"message": refused, "season": scope.season}, answer=refused))
    conference = conference_refusal(scope)
    if conference is not None:
        return conference
    return resolved_team(con, scope.team, season=slot_season(scope))


# --- the power index (team_outlook) --------------------------------------------


def read_team_outlook(con: duckdb.DuckDBPyConnection, q: TeamSeasonQuery, *, stated: frozenset[str]) -> Result | TemplateResult:
    """A team's ESPN Basketball Power Index - its rating and where it sits,
    its record and projection, its playoff and title chances, and its
    strength of schedule - from one snapshot of ``team_power_index``, as a
    :class:`~association.query.result.Result`: a
    :class:`~association.query.result.Scalar` of the snapshot's figures for
    the team and a :class:`~association.query.result.Grouped` by ``round``
    of its chances. A season holds one snapshot per season type, each
    covering all 30 teams, and ESPN overwrites rather than keeping a dated
    series; a regular-season question reads the regular-season snapshot
    outright when it holds the team, a postseason one the postseason
    snapshot (:func:`~association.query.team_seasons.team_outlook_chosen`).
    The snapshots the season holds are ``facts["snapshots"]``, so a team
    missing from the one asked for is told which exist rather than that
    there is "no data" (a Result with no parts). A
    :class:`~association.query.templates.common.TemplateResult` back is the
    relation's refusal (a conference named as a team, an ambiguous team).

    ``templates.teams.team_outlook`` was this, with its words; its two
    statements are the relation's (:mod:`association.query.team_seasons`).

    .. versionadded:: 5.0.0
    """
    team = _team_season_subject(con, "team_outlook", q.scope, stated)
    if isinstance(team, TemplateResult):
        return team
    season = q.scope.season or current_season()
    postseason = (q.scope.season_type or 2) == 3
    snapshots = values_of(con, team_outlook_snapshots_statement(team.id, season))
    chosen = team_outlook_chosen(snapshots, postseason)
    span = Span(season=season, season_type=3 if postseason else 2, source="team_snapshots")
    facts: dict[str, Any] = {"postseason": postseason, "snapshots": snapshot_facts(snapshots)}
    if chosen is None:
        return Result(subject=team.name, relation="team", span=span, notes=_team_outlook_missing_notes(team, season, snapshots), facts=facts)
    kind = chosen[0]
    rows = values_of(con, team_outlook_row_statement(season, kind, team.id))
    # The snapshot was chosen because it holds this team.
    assert rows
    return _team_outlook_result(team, season, postseason, chosen, rows[0], snapshots, span, facts)


def _team_outlook_missing_notes(team: Entity, season: int, snapshots: list[tuple[Any, ...]]) -> tuple[Note, ...]:
    """Where no snapshot of the kind asked for holds the team, but another
    does: the hint to ask about the regular season, with the snapshots that
    hold it."""
    holding = [s for s in snapshots if s[3]]
    if not holding:
        return ()
    return (Note("hint", {"team": team.name, "season": season, "what": "regular_season", "snapshots": _team_outlook_described(holding)}),)


def _team_outlook_described(snapshots: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    """The facts each snapshot is described by in a note - its kind, date and
    team count - as the retired template recorded them."""
    return [{key: value for key, value in each.items() if key != "holds"} for each in snapshot_facts(snapshots)]


def _team_outlook_result(team: Entity, season: int, postseason: bool, chosen: tuple[Any, ...], row: tuple[Any, ...], snapshots: list[tuple[Any, ...]], span: Span, facts: dict[str, Any]) -> Result:
    """The chosen snapshot's row for the team, as the Result's parts and
    notes: what is odd about the snapshot (a postseason one standing in for
    a missing regular-season one; a stamp after the season ended), a rating
    the snapshot left empty, and the season's other snapshots."""
    updated, bpi, offense, defense, wins, losses, proj_w, proj_l, *chances_and_sos = row
    kind = chosen[0]
    name = BPI_SNAPSHOT_NAMES.get(kind, f"type-{kind}")
    chances = dict(zip([c for _, c in BPI_CHANCES], chances_and_sos[:4], strict=True))
    sos, sos_rank, higher = chances_and_sos[4], chances_and_sos[5], chances_and_sos[6]
    teams = chosen[2]
    notes: list[Note] = []
    if not postseason and kind == 3:
        notes.append(Note("snapshot", {"what": "postseason_substitute", "season": season, "team": team.name}))
    if str(updated)[:4] > str(season):
        notes.append(Note("snapshot", {"what": "stamped_after_season", "date": str(updated)[:10], "season": season, "snapshot": name}))
    if bpi is None:
        notes.append(Note("value_withheld", {"what": "bpi", "why": "empty in this snapshot", "teams": teams}))
    others = [s for s in snapshots if s[0] != kind]
    if others:
        notes.append(Note("snapshot", {"what": "other_snapshots", "season": season, "snapshots": _team_outlook_described(others)}))
    values = {
        "bpi": bpi,
        "bpi_offense": offense,
        "bpi_defense": defense,
        "higher": higher,
        "wins": wins,
        "losses": losses,
        "projected_wins": proj_w,
        "projected_losses": proj_l,
        "strength_of_schedule": sos,
        "strength_of_schedule_rank": sos_rank,
    }
    played = int(wins) + int(losses) if wins is not None and losses is not None else 0
    rounds = tuple({"key": label, "chance": chances[column]} for label, column in BPI_CHANCES)
    parts = (Part(body=Scalar(games=played, values=values)), Part(role="detail", body=Grouped(by="round", rows=rounds)))
    facts = {**facts, "snapshot": name, "kind": kind, "updated": str(updated), "teams_in_snapshot": teams}
    return Result(subject=team.name, relation="team", span=span, parts=parts, notes=tuple(notes), facts=facts)


# --- a team's season line (team_stat) ----------------------------------------------

#: The metrics counted over possessions, beneath which the formula is said.
_POSSESSION_METRICS = frozenset({"offensive_rating", "defensive_rating", "net_rating", "pace"})


def _team_stat_metric(scope: Scope) -> str | None:
    """The metric the ``stat`` slot names through the whitelist
    (``team_metrics.STAT_ALIASES``), ``None`` for no stat - and a word it does
    not know declined rather than matched to something close."""
    key = resolve_team_metric(scope.stat)
    if key is None and scope.stat and scope.stat.strip():
        raise Unsupported(f"no team metric for stat {scope.stat!r}")
    return key


def read_team_stat(con: duckdb.DuckDBPyConnection, q: TeamSeasonQuery, *, stated: frozenset[str]) -> Result | TemplateResult:
    """One team's season numbers, each with its rank in the league, from the
    team-season relation (:mod:`association.query.team_seasons`), as a
    :class:`~association.query.result.Result` on a span whose ``source`` is
    ``"team_seasons"``: a :class:`~association.query.result.Grouped` by
    ``metric`` - each row a metric's key, value, rank and how many teams it
    ranks among - for the one named (``facts["metric"]``) or for the
    compact line (``team_metrics.DEFAULT_TEAM_LINE``) where none was; a
    record as a :class:`~association.query.result.Scalar` of wins, losses
    and the record's rank. Where nothing can be given, a Result with no
    parts and ``facts["missing"]`` naming which fact is missing: the
    metric's first season (``"metric_season"``), the team's line
    (``"line"``, with whether it played the regular season) or its record
    (``"record"``). A metric needing points allowed that ESPN's game list
    cannot give is ``facts["short"]``: the team the answer names, its
    listed and played games, and how many other teams are short.

    ``templates.teams.team_stat`` was this, with its words; its statements
    are the relation's (``team_metrics.season_table`` and ``record_table``,
    moved whole).

    .. versionadded:: 5.0.0
    """
    scope = q.scope
    team = _team_season_subject(con, "team_stat", scope, stated)
    if isinstance(team, TemplateResult):
        return team
    key = _team_stat_metric(scope)
    season = scope.season or current_season()
    season_type = scope.season_type or 2
    span = Span(season=season, season_type=season_type, source="team_seasons")
    if key is not None and TEAM_METRICS[key].expression is None:
        return _team_stat_record(con, team, key, span)
    if key is not None and season < TEAM_METRICS[key].first_season:
        return Result(subject=team.name, relation="team", span=span, facts={"metric": key, "missing": "metric_season"})
    lines = team_lines(values_of(con, team_lines_statement(season, season_type)), season)
    mine = next((line for line in lines if line.team == team.name), None)
    if mine is None:
        # Which fact is missing decides the sentence: a team that did not
        # reach the postseason is not a team the warehouse lacks numbers for.
        played = season_type == 3 and any(line.team == team.name for line in team_lines(values_of(con, team_lines_statement(season, 2)), season))
        return Result(subject=team.name, relation="team", span=span, facts={"metric": key, "missing": "line", "played_regular_season": played})
    return _team_stat_line(team, key, span, lines, mine)


def _team_stat_record(con: duckdb.DuckDBPyConnection, team: Entity, key: str, span: Span) -> Result:
    """A record metric: the team's record and where it ranks among the
    league's, from the standings (a tally of the games for a postseason)."""
    assert span.season is not None and span.season_type is not None
    records = team_records(values_of(con, team_records_statement(span.season, span.season_type)))
    mine = next((r for r in records if r.team == team.name), None)
    if mine is None:
        return Result(subject=team.name, relation="team", span=span, facts={"metric": key, "missing": "record"})
    rank = next(r for r, t, _ in ranked({r.team: r.win_pct for r in records}, True) if t == team.name)
    line = Scalar(games=mine.wins + mine.losses, values={"wins": mine.wins, "losses": mine.losses, "rank": rank, "of": len(records)})
    return Result(subject=team.name, relation="team", span=span, parts=(Part(body=line),), facts={"metric": key})


def short_of_games(metric_key: str, lines: list[TeamLine], subject: str | None = None) -> dict[str, Any]:
    """Why an opponent-based metric has no value, as plain values: the games
    behind the points allowed do not number the games behind everything
    else. Names the team asked about when it is one of the short ones, the
    first short one otherwise, and how many others are short.

    .. versionadded:: 5.0.0
       ``templates.teams._incomplete_opponents``' facts.
    """
    short = [line for line in lines if line.values.get(metric_key) is None]
    example = next((line for line in short if line.team == subject), short[0])
    return {"team": example.team, "listed": example.listed_games or 0, "games": example.games, "others": len(short) - 1}


def _team_stat_rank(lines: list[TeamLine], name: str, metric: TeamMetric, team: str, value: float | None) -> int | None:
    """Where ``team`` ranks by ``name`` - None where its value is missing, or
    any team's is."""
    if value is None or not all(line.values.get(name) is not None for line in lines):
        return None
    return next(r for r, t, _ in ranked({line.team: line.values[name] or 0.0 for line in lines}, descending_for(metric, "best")) if t == team)


def _team_stat_line(team: Entity, key: str | None, span: Span, lines: list[TeamLine], mine: TeamLine) -> Result:
    """The team's value and league rank for each metric asked for, and the
    notes beneath them: the rating formula (and, for the line, what a rank
    means and a value or rank left out)."""
    wanted = [key] if key else list(DEFAULT_TEAM_LINE)
    rows = tuple({"key": name, "value": mine.values.get(name), "rank": _team_stat_rank(lines, name, TEAM_METRICS[name], team.name, mine.values.get(name)), "of": len(lines)} for name in wanted)
    facts: dict[str, Any] = {"metric": key, "games": mine.games}
    notes: list[Note] = []
    if key is not None:
        if rows[0]["value"] is None:
            facts["short"] = short_of_games(key, lines, team.name)
        elif key in _POSSESSION_METRICS:
            notes.append(Note("definition", {"term": "rating_formula"}))
    else:
        notes += [Note("definition", {"term": "rank_meaning"}), Note("definition", {"term": "rating_formula"})]
        if any(row["value"] is None for row in rows):
            notes.append(Note("value_withheld", {"what": "points_allowed", "why": "game list short for this team", "team": team.name, "season": span.season}))
        elif any(row["rank"] is None for row in rows):
            notes.append(Note("value_withheld", {"what": "rank", "why": "game list short for other teams", "team": team.name, "season": span.season}))
    return Result(subject=team.name, relation="team", span=span, parts=(Part(body=Grouped(by="metric", rows=rows)),), notes=tuple(notes), facts=facts)
