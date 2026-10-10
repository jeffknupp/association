"""A team's own season, read: its line and where it ranks (``team_stat``),
every team ranked by one metric of the line or the standings
(``team_leaderboard``), and its place in ESPN's power index
(``team_outlook``) - the two team-season relations of ``ROADMAP-TYPES.md``
(``team_seasons``, ``team_snapshots``), beside the team-games relation the
team compiler reads (:mod:`~association.query.compose.team`).

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

import duckdb

from association.nba.season import current_season
from association.query.coverage import coverage_refusal
from association.query.entities import Entity, resolved_team, slot_season
from association.query.measure import spelled
from association.query.notes import Note
from association.query.player_relation import validated_until
from association.query.point import TEAM_SEASON_POINTS
from association.query.reading import Scope, Unsupported, _clamp_limit, unhonored_scoping
from association.query.result import Grouped, Narrowing, OutlookFacts, Part, Refusal, Result, Scalar, Span, TeamRankingFacts, TeamStatFacts, Unanswered
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
    team_since_records_statement,
    team_venue_records_statement,
    venue_records,
)

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


def conference_refusal(scope: Scope) -> Unanswered | None:
    """The refusal naming the real cause, where a team slot holds a
    conference or a division rather than a team (``calendar.conference_named``,
    said by the sayer's ``conference_named`` phrase) - None otherwise.

    .. versionadded:: 5.0.0
       ``templates.teams.conference_refusal`` was this.
    """
    from association.query.calendar import conference_named

    named = conference_named(scope)
    return Refusal(kind="conference_named", facts={"named": named}, shown={"unanswerable": named}) if named is not None else None


def team_season_declines(intent: str, scope: Scope, stated: frozenset[str]) -> str | None:
    """Why a team-season intent's reader cannot answer ``scope`` as asked - a
    narrowing its words do not state (``stated``,
    ``compose.plan.STATED_SCOPING``), in the retired template's sentence
    (the scope check's, gone with Phase 2's step 6) - or None. The first
    thing each reader checks, before the coverage floor, as the scope check
    ran before the template.

    .. versionadded:: 5.0.0
    """
    ignored = unhonored_scoping(intent, scope, stated)
    return f"{intent} cannot honor {ignored} - it would answer for a different span than was asked" if ignored else None


def _team_season_subject(con: duckdb.DuckDBPyConnection, intent: str, scope: Scope, stated: frozenset[str]) -> Entity | Unanswered:
    """The team a one-team team-season read is about, after the checks the
    answering loop and the retired template made first, in their order: a
    narrowing the words do not state (a decline), the coverage floor, a
    conference or division in a team slot, then the team itself (a
    clarifying question, or a decline where none matches)."""
    declined = team_season_declines(intent, scope, stated)
    if declined is not None:
        raise Unsupported(declined)
    refused = coverage_refusal(TEAM_SEASON_POINTS[intent], scope)
    if refused is not None:
        raise Refused(refused)
    conference = conference_refusal(scope)
    if conference is not None:
        return conference
    return resolved_team(con, scope.subject.team, season=slot_season(scope))


# --- the power index (team_outlook) --------------------------------------------


def read_team_outlook(con: duckdb.DuckDBPyConnection, q: TeamSeasonQuery, *, stated: frozenset[str]) -> Result | Unanswered:
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
    The snapshots the season holds are its facts'
    (:class:`~association.query.result.OutlookFacts`), so a team missing
    from the one asked for is told which exist rather than that there is
    "no data" (a Result whose projection holds no values). A
    :class:`~association.query.result.Refusal` or :class:`~association.query.result.Clarify` back is the
    relation's refusal (a conference named as a team, an ambiguous team).

    ``templates.teams.team_outlook`` was this, with its words; its two
    statements are the relation's (:mod:`association.query.team_seasons`).

    .. versionadded:: 5.0.0
    """
    team = _team_season_subject(con, "team_outlook", q.scope, stated)
    if isinstance(team, Unanswered):
        return team
    season = q.scope.span.season or current_season()
    postseason = (q.scope.span.season_type or 2) == 3
    snapshots = values_of(con, team_outlook_snapshots_statement(team.id, season))
    chosen = team_outlook_chosen(snapshots, postseason)
    span = Span(season=season, season_type=3 if postseason else 2, source="team_snapshots")
    facts = OutlookFacts(postseason=postseason, snapshots=tuple(snapshot_facts(snapshots)))
    if chosen is None:
        # No snapshot of the kind asked for holds the team: the projection's
        # shape with nothing in it, said by which snapshots there are.
        empty = (Part(body=Scalar(games=0, how="projection")),)
        return Result(subject=team.name, relation="team", span=span, parts=empty, notes=_team_outlook_missing_notes(team, season, snapshots), facts=facts)
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


def _team_outlook_result(team: Entity, season: int, postseason: bool, chosen: tuple[Any, ...], row: tuple[Any, ...], snapshots: list[tuple[Any, ...]], span: Span, facts: OutlookFacts) -> Result:
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
    parts = (Part(body=Scalar(games=played, values=values, how="projection")), Part(role="detail", body=Grouped(by="round", rows=rounds)))
    facts = replace(facts, snapshot=name, kind=kind, updated=str(updated), teams_in_snapshot=teams)
    return Result(subject=team.name, relation="team", span=span, parts=parts, notes=tuple(notes), facts=facts)


# --- a team's season line (team_stat) ----------------------------------------------

#: The metrics counted over possessions, beneath which the formula is said.
_POSSESSION_METRICS = frozenset({"offensive_rating", "defensive_rating", "net_rating", "pace"})


def _team_stat_metric(scope: Scope) -> str | None:
    """The metric the ``stat`` slot names through the whitelist
    (``team_metrics.STAT_ALIASES``), ``None`` for no stat - and a word it does
    not know declined rather than matched to something close."""
    key = resolve_team_metric(scope.measure)
    stat = spelled(scope.measure)
    if key is None and stat and stat.strip():
        raise Unsupported(f"no team metric for stat {stat!r}")
    return key


def read_team_stat(con: duckdb.DuckDBPyConnection, q: TeamSeasonQuery, *, stated: frozenset[str]) -> Result | Unanswered:
    """One team's season numbers, each with its rank in the league, from the
    team-season relation (:mod:`association.query.team_seasons`), as a
    :class:`~association.query.result.Result` on a span whose ``source`` is
    ``"team_seasons"``: a :class:`~association.query.result.Grouped` by
    ``metric`` - each row a metric's key, value, rank and how many teams it
    ranks among - for the one named (its :class:`~association.query.result.TeamStatFacts`' ``metric``) or for the
    compact line (``team_metrics.DEFAULT_TEAM_LINE``) where none was; a
    record as a :class:`~association.query.result.Scalar` of wins, losses
    and the record's rank. Where nothing can be given, a Result with no
    the :class:`~association.query.result.Refusal` naming which fact is
    missing: the metric's first season (``metric_before_first_season``),
    the team's line (``no_team_line``, or ``missed_postseason`` for a team
    that played the regular season) or its record (``no_team_record``). A metric needing points allowed that ESPN's game list
    cannot give is the facts' ``short``: the team the answer names, its
    listed and played games, and how many other teams are short.

    ``templates.teams.team_stat`` was this, with its words; its statements
    are the relation's (``team_metrics.season_table`` and ``record_table``,
    moved whole).

    .. versionadded:: 5.0.0
    """
    scope = q.scope
    team = _team_season_subject(con, "team_stat", scope, stated)
    if isinstance(team, Unanswered):
        return team
    key = _team_stat_metric(scope)
    season = scope.span.season or current_season()
    season_type = scope.span.season_type or 2
    span = Span(season=season, season_type=season_type, source="team_seasons")
    if key is not None and TEAM_METRICS[key].expression is None:
        return _team_stat_record(con, team, key, span)
    if key is not None and season < TEAM_METRICS[key].first_season:
        return Refusal(kind="metric_before_first_season", facts={"metric": key, "season": season}, shown={"season": season})
    lines = team_lines(values_of(con, team_lines_statement(season, season_type)), season)
    mine = next((line for line in lines if line.team == team.name), None)
    if mine is None:
        # Which fact is missing decides the sentence: a team that did not
        # reach the postseason is not a team the warehouse lacks numbers for.
        played = season_type == 3 and any(line.team == team.name for line in team_lines(values_of(con, team_lines_statement(season, 2)), season))
        facts = {"team": team.name, "season": season, "season_type": season_type}
        return Refusal(kind="missed_postseason" if played else "no_team_line", facts=facts, shown={"team": team.name, "season": season, "stats": {}}, under=("headline",))
    return _team_stat_line(team, key, span, lines, mine)


def _team_stat_record(con: duckdb.DuckDBPyConnection, team: Entity, key: str, span: Span) -> Result | Refusal:
    """A record metric: the team's record and where it ranks among the
    league's, from the standings (a tally of the games for a postseason)."""
    assert span.season is not None and span.season_type is not None
    records = team_records(values_of(con, team_records_statement(span.season, span.season_type)))
    mine = next((r for r in records if r.team == team.name), None)
    if mine is None:
        facts = {"team": team.name, "season": span.season, "season_type": span.season_type}
        return Refusal(kind="no_team_record", facts=facts, shown={"team": team.name, "season": span.season}, under=())
    rank = next(r for r, t, _ in ranked({r.team: r.win_pct for r in records}, True) if t == team.name)
    line = Scalar(games=mine.wins + mine.losses, values={"wins": mine.wins, "losses": mine.losses, "rank": rank, "of": len(records)}, how="ranked")
    return Result(subject=team.name, relation="team", span=span, parts=(Part(body=line),), facts=TeamStatFacts(metric=key))


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
    facts = TeamStatFacts(metric=key, games=mine.games)
    notes: list[Note] = []
    if key is not None:
        if rows[0]["value"] is None:
            facts = replace(facts, short=short_of_games(key, lines, team.name))
        elif key in _POSSESSION_METRICS:
            notes.append(Note("definition", {"term": "rating_formula"}))
    else:
        notes += [Note("definition", {"term": "rank_meaning"}), Note("definition", {"term": "rating_formula"})]
        if any(row["value"] is None for row in rows):
            notes.append(Note("value_withheld", {"what": "points_allowed", "why": "game list short for this team", "team": team.name, "season": span.season}))
        elif any(row["rank"] is None for row in rows):
            notes.append(Note("value_withheld", {"what": "rank", "why": "game list short for other teams", "team": team.name, "season": span.season}))
    return Result(subject=team.name, relation="team", span=span, parts=(Part(body=Grouped(by="metric", rows=rows)),), notes=tuple(notes), facts=facts)


# --- every team ranked (team_leaderboard) -----------------------------------------

DEFAULT_TEAM_LEADERBOARD_LIMIT = 10
"""How many teams a ranking lists where the question named no count.

.. versionadded:: 5.0.0
   Moved from ``templates.teams``.
"""


def _team_leaderboard_checks(scope: Scope, stated: frozenset[str]) -> Unanswered | None:
    """What the answering loop and the retired template checked before
    reading, in their order: a narrowing the words do not state (a
    decline), the coverage floor, a conference or division in a team slot."""
    declined = team_season_declines("team_leaderboard", scope, stated)
    if declined is not None:
        raise Unsupported(declined)
    refused = coverage_refusal(TEAM_SEASON_POINTS["team_leaderboard"], scope)
    if refused is not None:
        raise Refused(refused)
    return conference_refusal(scope)


def _team_leaderboard_span(scope: Scope, season: int, season_type: int) -> Span:
    """The ranking's span: one season, or the seasons from ``since`` on
    (through ``until`` where it bounds the other end) - a ``since`` beside a
    named season, and an ``until`` with no ``since`` or before it, declined."""
    since = scope.span.since or None
    if since is not None and scope.span.season:
        raise Unsupported(f"since {since} and the {scope.span.season} season at once")
    until = validated_until(scope.span.until, since)
    return Span(season=season, season_type=season_type, first=since, last=until, source="team_seasons")


def read_team_leaderboard(con: duckdb.DuckDBPyConnection, q: TeamSeasonQuery, *, stated: frozenset[str]) -> Result | Unanswered:
    """Every team ranked by one metric - of the season line
    (``team_metrics.TEAM_METRICS``) or of the standings (a record, home or
    road, or across the seasons from ``since`` on) - as a
    :class:`~association.query.result.Result` on a ``"team_seasons"`` span:
    a :class:`~association.query.result.Grouped` by ``team``,
    ``ranked_by`` the metric, each row the team (``key``), its competition
    ``rank`` (ties share one), the ``value`` it is ranked by, and for a
    record its ``wins`` and ``losses``. The rows are the ones shown: the
    count asked for (ties at the cut kept whole) and, past it, a named
    team's own row (``beyond``); its
    :class:`~association.query.result.TeamRankingFacts` say how many teams
    were ranked and which end comes first, and the venue is the
    narrowing's. Where nothing
    can be ranked, the :class:`~association.query.result.Refusal` naming
    why (no home or road split in that season's standings, the metric's
    first season, a team short of games for points allowed).

    ``templates.teams.team_leaderboard`` was this, with its words; its
    statements are the relation's (:mod:`association.query.team_seasons`).

    .. versionadded:: 5.0.0
    """
    scope = q.scope
    conference = _team_leaderboard_checks(scope, stated)
    if conference is not None:
        return conference
    key = resolve_team_metric(scope.measure)
    if key is None:
        raise Unsupported(f"no team metric for stat {spelled(scope.measure)!r}")
    # `season` still settles to a real year under `since` - unread by the
    # since-bounded read, and here only for the named team's own-season name
    # lookup, which stays "now" either way.
    season = scope.span.season or current_season()
    season_type = scope.span.season_type or 2
    span = _team_leaderboard_span(scope, season, season_type)
    named = resolved_team(con, scope.subject.team, season=slot_season(scope)) if scope.subject.team is not None else None
    if isinstance(named, Unanswered):
        return named
    facts = TeamRankingFacts(metric=key, rank=scope.window.rank, descending=descending_for(TEAM_METRICS[key], scope.window.rank))
    read = _team_leaderboard_values(con, key, span, scope.cuts.venue) if TEAM_METRICS[key].expression is None else _team_leaderboard_metric(con, key, span, scope.cuts.venue)
    if isinstance(read, Refusal):
        return read
    values, rows = read
    return _team_leaderboard_ranked(span, facts, values, rows, _clamp_limit(scope.window.count, default=DEFAULT_TEAM_LEADERBOARD_LIMIT), named, scope.cuts.venue)


def _team_leaderboard_values(con: duckdb.DuckDBPyConnection, key: str, span: Span, venue: str | None) -> tuple[dict[str, float], dict[str, dict[str, Any]]] | Refusal:
    """A record metric's value per team - the win percentage, or its
    complement for the most losses - from the standings (venue-split where
    asked), or a tally of the games across a ``since``-bounded span; or why
    there is none (a :class:`~association.query.result.Refusal`)."""
    assert span.season is not None and span.season_type is not None
    if span.first:
        if venue is not None:
            raise Unsupported("a since-bounded record has no home/road split yet")
        records = team_records(values_of(con, team_since_records_statement(span.season_type, span.first, span.last)))
    elif venue:
        rows = values_of(con, team_venue_records_statement(span.season, span.season_type, venue))
        records = venue_records(rows, span.season_type)
        if rows and not records:
            return Refusal(kind="no_venue_split", facts={"season": span.season}, shown={"season": span.season})
    else:
        records = team_records(values_of(con, team_records_statement(span.season, span.season_type)))
    values = {r.team: (r.win_pct if key == "record" else 1 - r.win_pct) for r in records}
    return values, {r.team: {"wins": r.wins, "losses": r.losses} for r in records}


def _team_leaderboard_metric(con: duckdb.DuckDBPyConnection, key: str, span: Span, venue: str | None) -> tuple[dict[str, float], dict[str, dict[str, Any]]] | Refusal:
    """A season-line metric's value per team, or why there is none: a span of
    seasons and a venue split are declined (team season stats have neither),
    the metric's first season and a team short of games are refused."""
    metric = TEAM_METRICS[key]
    if span.first or span.last:
        raise Unsupported(f"team season stats have no way to sum {metric.label} across a span of seasons yet")
    if venue is not None:
        # Team season stats have no home/road split; team_box_stats does.
        raise Unsupported(f"team season stats have no {venue} split for {metric.label}")
    assert span.season is not None and span.season_type is not None
    if span.season < metric.first_season:
        return Refusal(kind="metric_before_first_season", facts={"metric": key, "season": span.season}, shown={"season": span.season})
    lines = team_lines(values_of(con, team_lines_statement(span.season, span.season_type)), span.season)
    if lines and any(line.values.get(key) is None for line in lines):
        facts = {"metric": key, "season": span.season, "season_type": span.season_type, "short": short_of_games(key, lines)}
        return Refusal(kind="short_of_games", facts=facts, shown={"season": span.season})
    values = {line.team: line.values[key] or 0.0 for line in lines}
    return values, {team: {} for team in values}


def _team_leaderboard_ranked(span: Span, facts: TeamRankingFacts, values: dict[str, float], extra: dict[str, dict[str, Any]], limit: int, named: Entity | None, venue: str | None) -> Result:
    """The ranked rows shown: up to ``limit``, a tie at the cut shown whole -
    "nba team with least playoff wins since 2022" with a limit of 1 listed
    the Nets alone where the Hornets and Wizards share the zero (day5, F100)
    - and a named team's own row past the cut where it would otherwise be
    left out."""
    order = ranked(values, facts.descending)
    shown = order[:limit]
    shown += [row for row in order[limit:] if shown and row[0] == shown[-1][0]]
    beyond = [row for row in order[len(shown) :] if named is not None and row[1] == named.name]
    rows = tuple({"key": team, "rank": rank, "value": value, "beyond": index >= len(shown), **extra[team]} for index, (rank, team, value) in enumerate([*shown, *beyond]))
    notes = (Note("definition", {"term": "rating_formula"}),) if rows and facts.metric in _POSSESSION_METRICS else ()
    body = Grouped(by="team", rows=rows, ranked_by=facts.metric)
    return Result(subject="the league", relation="team", span=span, narrowing=Narrowing(venue=venue), parts=(Part(body=body),), notes=notes, facts=replace(facts, of=len(order)))
