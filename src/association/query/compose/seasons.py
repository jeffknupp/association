"""The season line's readers: a player's unnarrowed line (a season, a
career, or a computed advanced stat), his stat season by season, and two or
more players' lines side by side - read from the season-line relation
(:mod:`association.query.season_line`) into a
:class:`~association.query.result.Result`. Phase 2's slice (iii)
(``ROADMAP.md``): the retired templates' reads
(``templates.players._player_stat_season_line``, ``_player_history_read``,
``_player_compare_lines``, called by three presenters) are gone, and their
statements are the relation's, built there and executed through the
compiler's one door (:func:`~association.query.compose.core.values_of`).
The sayer (:mod:`association.query.compose.say`) words each.

Each answer is two or more statements (a line and the season redirect, a
history and its career total, one line and one NetPoints row per player),
and none of them is a compiled
:class:`~association.query.compose.core.Query`; the stage snapshot records
the planned point, as it does for every reader.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from typing import Any

import duckdb

from association.nba.season import current_season
from association.query.answer import Reply
from association.query.entities import Entity
from association.query.measures import stat_measure
from association.query.notes import Note
from association.query.reading import Unsupported
from association.query.result import Decided, Grouped, Part, Result, Scalar, Span
from association.query.season_line import (
    COMPARE_STAT_LINE,
    MAX_COMPARED_PLAYERS,
    NETPOINTS_COMPARE_ROWS,
    advanced_span,
    advanced_statement,
    career_statement,
    compared_player,
    history_seasons,
    history_statement,
    history_subject,
    history_through,
    history_total_statement,
    line_columns,
    line_subject,
    netpoints_statement,
    season_redirect,
    season_statement,
)
from association.query.templates.common import HISTORY_COLUMNS, PLAYER_STAT_COLUMNS, SEASON_TYPE_NAMES, ResolvedSpan, season_phrase, unhonored_scoping
from association.query.templates.players import ADVANCED_STATS, SHOOTING_STATS, wanted_stats

from .core import Query, values_of


def _first(rows: list[tuple[Any, ...]]) -> tuple[Any, ...] | None:
    """A statement's first row, as ``fetchone`` gave the retired reads."""
    return rows[0] if rows else None


# --- a player's line -----------------------------------------------------------


def player_line_reads(q: Query, stated: frozenset[str]) -> bool:
    """Whether ``q`` is ``player_stat``'s own unnarrowed point on the season
    line: a per-game scalar for one named player, narrowed only by what the
    retired template's words state, and the router's own stat - the
    default point's measures, or the question's word for that same stat
    ("3pt percentage" is three_pct, which the router filed
    threePointFieldGoalPct). A measure the words moved in is a point the
    season line does not say.

    .. versionadded:: 5.0.0
    """
    if q.skeleton != "scalar" or q.aggregate != "per_game" or q.subject != "player" or q.predicates or q.source != "seasons":
        return False
    if unhonored_scoping("player_stat", q.scope, stated):
        return False
    # At call time: the point reader imports this package for the adapter
    # still here, so a module-level import would cycle.
    from association.query.point import default_point

    try:
        own = default_point("player_stat", q.scope)
    except Unsupported:
        return False
    named = stat_measure(q.scope.stat)
    return own.source == "seasons" and (q.measures == own.measures or (named is not None and q.measures == [named]))


def read_player_line(con: duckdb.DuckDBPyConnection, q: Query, *, stated: frozenset[str]) -> Result | Reply | None:
    """``player_stat``'s unnarrowed point - one season's line or a career,
    from the season line, or a computed advanced stat from
    ``player_season_advanced_stats`` - as a
    :class:`~association.query.result.Scalar` on a span whose ``source`` is
    ``"seasons"``. ``None`` where the point is not that
    (:func:`player_line_reads`); a
    :class:`~association.query.answer.Reply` back is the
    relation's refusal (an ambiguous name).

    Raises ``Unsupported`` for a stat with no per-game column
    ("avg_shot_distance"), naming the stat rather than "a line the season
    line's reader did not say" (the games relation has no column for it
    either), and for an advanced stat with no career figure.

    .. versionadded:: 5.0.0
    """
    if not player_line_reads(q, stated):
        return None
    scope = q.scope
    stat = scope.stat
    if not (stat is not None and (stat in ADVANCED_STATS or stat in SHOOTING_STATS)):
        wanted_stats(scope)
    subject = line_subject(con, scope)
    if isinstance(subject, Reply):
        return subject
    player, span = subject
    # Before the ESPN-served columns, because these carry their own table,
    # their own floor and their own career arithmetic - and because
    # wanted_stats would otherwise refuse them as unknown, which is how
    # "kevin durant true shooting percentage career" fell through while the
    # leaderboard ranked the same stat happily.
    if stat is not None and stat in ADVANCED_STATS:
        return _player_line_advanced(con, player, span, stat)
    shooting = SHOOTING_STATS.get(stat) if stat is not None else None
    wanted = [] if shooting else wanted_stats(scope)
    facts = {"stat": stat if shooting else None, "wanted": wanted}
    if span.career:
        return _player_line_career(con, player, span, wanted, shooting, facts)
    return _player_line_season(con, player, span, scope.season_type or 2, wanted, shooting, facts)


def _player_line_season(con: duckdb.DuckDBPyConnection, player: Entity, span: ResolvedSpan, season_type: int, wanted: list[str], shooting: Any, facts: dict[str, Any]) -> Result:
    """One season's line, read from the deduplicated season table. The
    scalar holds each wanted stat's per-game figure (``values``), its total
    where the stat has a total column, a made count's attempts and a
    percentage's makes and attempts (``sums``), all as stored. A season with
    no row is an empty scalar - the table holds no row of zero games (0 of
    25,168, 2026-10-05) - with the seasons the player IS on record for as a
    decision where the season was defaulted rather than named."""
    season = span.season or current_season()
    columns, attempted_col = line_columns(wanted, shooting)
    row = _first(values_of(con, season_statement(player.id, columns, season, season_type)))
    line_span = Span(season=season, season_type=season_type, phrase=span.during(), source="seasons")
    facts = {**facts, "season_n": span.ordinal}
    if row is None:
        return Result(subject=player.name, relation="player", span=line_span, parts=(Part(body=Scalar(games=0)),), decisions=_player_line_redirect(con, player, span, season_type), facts=facts)
    found = dict(zip(columns, row, strict=True))
    sums: dict[str, Any] = {}
    for name in wanted:
        total = PLAYER_STAT_COLUMNS[name][1]
        if total:
            sums[name] = found[total]
    if attempted_col:
        sums[attempted_col] = found[attempted_col]
    if shooting:
        sums["made"], sums["attempted"] = found[shooting.made], found[shooting.attempted]
    scalar = Scalar(games=found["gamesPlayed"], values={name: found[PLAYER_STAT_COLUMNS[name][0]] for name in wanted}, sums=sums)
    return Result(subject=player.name, relation="player", span=line_span, parts=(Part(body=scalar),), facts=facts)


def _player_line_redirect(con: duckdb.DuckDBPyConnection, player: Entity, span: ResolvedSpan, season_type: int) -> tuple[Decided, ...]:
    """Where the season was defaulted to "now", not asked for, and the
    player has no line in it: the seasons he IS on record for. A retired
    player's "now" is empty and the plain refusal, read alone, sounds like
    his whole career is missing (issue #18); this redirects to what the
    warehouse actually holds for him rather than guessing a season or
    falling back to a career summary that can badly misrepresent him
    (Iverson's last season is 13.8 ppg against a 26.7 career average). A
    season the question named outright keeps the refusal plain, because it
    is the correct answer."""
    if not span.defaulted:
        return ()
    on_record = season_redirect(con, player.id, season_type, "player_season_stats_deduped")
    if on_record is None:
        return ()
    first, last = on_record
    kind = SEASON_TYPE_NAMES.get(season_type, "regular season")
    return (Decided(kind="season_redirected", field="season", chose=None, why="the season read by default holds nothing for him", facts={"first": first, "last": last, "what": kind}),)


def _player_line_career(con: duckdb.DuckDBPyConnection, player: Entity, span: ResolvedSpan, wanted: list[str], shooting: Any, facts: dict[str, Any]) -> Result:
    """A career line summed from the season table
    (:func:`~association.query.season_line.career_statement`): the games,
    each wanted stat's career per-game figure (``values``) and its total
    where there is one, a made count's attempts, a percentage's makes and
    attempts (``sums``), as summed - the sayer rounds them as the retired
    template did. No season on record is an empty scalar."""
    statement, attempted_col = career_statement(player.id, span.season_type, wanted, shooting)
    row = _first(values_of(con, statement))
    if row is None or not row[0]:
        return Result(subject=player.name, relation="player", span=Span(season_type=span.season_type, career=True, source="seasons"), parts=(Part(body=Scalar(games=0)),), facts=facts)
    games, first, last, seasons = row[:4]
    values: dict[str, Any] = {}
    sums: dict[str, Any] = {}
    if shooting:
        sums["made"], sums["attempted"] = row[4], row[5]
    else:
        for index, name in enumerate(wanted):
            values[name] = row[4 + 2 * index]
            amount = row[5 + 2 * index]
            if PLAYER_STAT_COLUMNS[name][1] and amount is not None:
                sums[name] = amount
        attempted = row[4 + 2 * len(wanted)] if attempted_col else None
        if attempted_col and attempted is not None:
            sums[attempted_col] = attempted
    scalar = Scalar(games=int(games), values=values, sums=sums)
    line_span = Span(season_type=span.season_type, career=True, first=first, last=last, source="seasons")
    return Result(subject=player.name, relation="player", span=line_span, parts=(Part(body=scalar),), facts={**facts, "season_count": seasons})


def _player_line_advanced(con: duckdb.DuckDBPyConnection, player: Entity, span: ResolvedSpan, stat: str) -> Result:
    """One player's computed advanced stat, for a season or a career,
    weighted by its own volume over a career
    (:func:`~association.query.season_line.advanced_statement`): the figure
    (``values``), its volume and how many seasons it could not see
    (``sums``), the games, and a note where seasons are missing. A usage
    rate or a game score has no volume to weight a career by, so it is
    refused rather than reported as a mean of means. Nothing on record is
    an empty scalar. The season line's alone: over a narrowed set of games
    the line's reader declines an advanced stat (``compose.stats``)."""
    spec = ADVANCED_STATS[stat]
    if span.career and spec.weight is None:
        raise Unsupported(f"{spec.label} has no career figure - it has no volume column to weight the seasons by, so a career would be a mean of means")
    span = advanced_span(span)
    row = _first(values_of(con, advanced_statement(player.id, span, spec)))
    facts: dict[str, Any] = {"stat": stat, "wanted": []}
    if row is None or row[0] is None:
        empty = Span(season=span.season, season_type=span.season_type, career=span.career, phrase=span.during(), source="seasons")
        return Result(subject=player.name, relation="player", span=empty, parts=(Part(body=Scalar(games=0)),), facts=facts)
    value, volume, games, first, last, seasons, missing = row
    notes = (Note("seasons_missing", {"seasons": int(missing), "why": "empty_box_score", "stat": spec.column, "label": spec.label}),) if missing else ()
    scalar = Scalar(games=games, values={stat: value}, sums={"volume": volume, "seasons_missing": int(missing)})
    line_span = Span(season=span.season, season_type=span.season_type, career=span.career, first=first, last=last, phrase=span.during(first, last), source="seasons")
    return Result(subject=player.name, relation="player", span=line_span, parts=(Part(body=scalar),), notes=notes, facts={**facts, "season_count": seasons})


# --- a stat season by season ----------------------------------------------------------


def player_history_reads(q: Query, stated: frozenset[str]) -> bool:
    """Whether ``q`` is ``player_history``'s own point on the season line:
    a history by season of the router's own stat, one with a per-season
    column, narrowed only by what the retired template's words state. A
    stat with no per-season column or a measure the question's words moved
    in is the game-level reading's.

    .. versionadded:: 5.0.0
    """
    stat = q.scope.stat
    if q.source != "seasons" or q.group != "season" or stat is None or stat not in HISTORY_COLUMNS:
        return False
    if stat_measure(stat) not in (None, *q.measures[:1]):
        return False
    return not unhonored_scoping("player_history", q.scope, stated)


def read_player_history(con: duckdb.DuckDBPyConnection, q: Query, *, stated: frozenset[str]) -> Result | Reply | None:
    """``player_history``'s own point - the stat season by season from the
    season line, newest first, the default four or the count asked for, and
    under a career every season with the career line beneath - as a
    :class:`~association.query.result.Grouped` by ``season`` (one row per
    season: its games and the stat's columns, by their stable keys) and,
    for a career, a summary part: the makes and attempts summed across the
    seasons shown for a percentage (never a mean of means, F041), or the
    career total for a count. ``None`` where the point is not that
    (:func:`player_history_reads`); a
    :class:`~association.query.answer.Reply` back is the
    relation's refusal.

    .. versionadded:: 5.0.0
    """
    if not player_history_reads(q, stated):
        return None
    scope = q.scope
    player = history_subject(con, scope)
    if isinstance(player, Reply):
        return player
    stat = scope.stat
    assert stat is not None
    latest = history_through(scope)
    career = scope.span == "career"
    season_type = scope.season_type or 2
    columns = HISTORY_COLUMNS[stat][1]
    keys = ["key", "games"] + [k for _, _, k in columns]
    rows = tuple(dict(zip(keys, r, strict=True)) for r in values_of(con, history_statement(player.id, stat, season_type, latest, history_seasons(scope))))
    parts = [Part(body=Grouped(by="season", rows=rows))]
    # Only for a career: the default four seasons is already a window a
    # reader chose, and a combined figure over a window nobody asked to see
    # summed would be the substitution this module exists to stop.
    if career and rows:
        summary = _player_history_career(con, player, stat, season_type, latest, rows)
        if summary is not None:
            parts.append(Part(role="summary", body=summary))
    span = Span(season_type=season_type, career=career, source="seasons")
    return Result(subject=player.name, relation="player", span=span, parts=tuple(parts), facts={"stat": stat})


def _player_history_career(con: duckdb.DuckDBPyConnection, player: Entity, stat: str, season_type: int, latest: int, rows: tuple[dict[str, Any], ...]) -> Scalar | None:
    """The career line beneath a career's history: for a shooting column
    (a percentage, its makes and its attempts), the makes and attempts
    summed across every season shown, games-weighted by construction; for a
    counting column, the career total
    (:func:`~association.query.season_line.history_total_statement`).
    ``None`` with no attempts, or no total."""
    columns = HISTORY_COLUMNS[stat][1]
    games = sum(row["games"] or 0 for row in rows)
    if len(columns) == 3:
        made_key, attempted_key = columns[1][2], columns[2][2]
        made = sum(row.get(made_key) or 0 for row in rows)
        attempted = sum(row.get(attempted_key) or 0 for row in rows)
        return Scalar(games=games, sums={"made": made, "attempted": attempted}) if attempted else None
    total = _first(values_of(con, history_total_statement(player.id, stat, season_type, latest)))
    if total is None or total[0] is None:
        return None
    return Scalar(games=games, sums={"total": total[0]})


# --- two or more players' lines -------------------------------------------------------


def player_compare_reads(q: Query, stated: frozenset[str]) -> bool:
    """Whether ``q`` is ``player_compare``'s own point: a pair's lines on
    the season line, grouped by player, no line on a column, no measure of
    the words' own and no narrowing its words do not state.

    .. versionadded:: 5.0.0
    """
    if q.subject != "player" or q.source != "seasons" or q.skeleton != "grouped" or q.group != "player" or q.predicates or q.measures:
        return False
    return not unhonored_scoping("player_compare", q.scope, stated)


def read_player_compare(con: duckdb.DuckDBPyConnection, q: Query, *, stated: frozenset[str]) -> Result | Reply | None:
    """``player_compare``'s own point - two or more named players' season
    lines side by side - as a :class:`~association.query.result.Grouped` by
    ``subject`` (one row per player in the question's order: ``key`` his
    name, ``games`` and each stat's per-game figure; a player with no line
    that season is his name alone), and where the NetPoints table exists a
    detail part of each player's NetPoints summary, the same shape. The
    agent wrote correct SQL but expanded "SGA" to '%Scottie G. Allen%' and
    compared Luka Doncic to Luka Garza; each name is resolved by lookup, for
    the season compared. ``None`` where the point is not that
    (:func:`player_compare_reads`); a
    :class:`~association.query.answer.Reply` back is the
    relation's refusal (an unknown or ambiguous name). Raises
    ``Unsupported`` where the names resolve to one person, and for
    a stat with no per-game column.

    .. versionadded:: 5.0.0
    """
    if not player_compare_reads(q, stated):
        return None
    scope = q.scope
    season = scope.season or current_season()
    resolved: list[Entity] = []
    for name in scope.players[:MAX_COMPARED_PLAYERS]:
        player = compared_player(con, name, season)
        if isinstance(player, Reply):
            return player
        if player.id not in {p.id for p in resolved}:
            resolved.append(player)
    if len(resolved) < 2:
        raise Unsupported("the named players resolved to the same person")
    season_type = scope.season_type or 2
    wanted = wanted_stats(scope, COMPARE_STAT_LINE)
    lines = _player_compare_lines(con, resolved, wanted, season, season_type)
    parts = [Part(body=Grouped(by="subject", rows=tuple(lines.values())))]
    netpoints = _player_compare_netpoints(con, resolved, season, season_type)
    if netpoints is not None:
        parts.append(Part(role="detail", body=Grouped(by="subject", rows=tuple(netpoints.values()))))
    missing = [name for name, line in lines.items() if "games" not in line]
    notes = (Note("no_data_for", {"names": missing, "period": season_phrase(season, season_type)}),) if missing else ()
    return Result(subject=" vs ".join(lines), relation="player", span=Span(season=season, season_type=season_type, source="seasons"), parts=tuple(parts), notes=notes, facts={"wanted": wanted})


def _player_compare_lines(con: duckdb.DuckDBPyConnection, players: list[Entity], wanted: list[str], season: int, season_type: int) -> dict[str, dict[str, Any]]:
    """Each player's line for the season, by name - two players sharing a
    name are one entry, the later one's, as the retired table keyed them."""
    columns = ["gamesPlayed"] + [PLAYER_STAT_COLUMNS[name][0] for name in wanted]
    lines: dict[str, dict[str, Any]] = {}
    for player in players:
        row = _first(values_of(con, season_statement(player.id, columns, season, season_type)))
        lines[player.name] = {"key": player.name, **({"games": row[0], **dict(zip(wanted, row[1:], strict=True))} if row else {})}
    return lines


def _player_compare_netpoints(con: duckdb.DuckDBPyConnection, players: list[Entity], season: int, season_type: int) -> dict[str, dict[str, Any]] | None:
    """Each player's NetPoints summary, by name - his name alone where he
    has no row - or ``None`` without the table at all.

    NetPoints starts in 2019 and is a separate opt-in fetch, so this is
    supplementary rather than required: a player with no row contributes
    nothing and a season with no rows at all drops the section. That is
    also why ``net_points_player`` is deliberately NOT among the comparison's
    TEMPLATE_SOURCES - listing it would put a 2019 coverage floor on every
    comparison and refuse the 1994-2018 ones outright."""
    found: dict[str, dict[str, Any]] = {}
    for player in players:
        try:
            row = _first(values_of(con, netpoints_statement(player.id, season, season_type)))
        except duckdb.Error:
            # The table only exists if the NetPoints fetch was run. A
            # comparison is complete without it - so this drops the section
            # rather than failing the answer.
            return None
        found[player.name] = {"key": player.name, **(dict(zip([column for _, column in NETPOINTS_COMPARE_ROWS], row, strict=True)) if row else {})}
    return found
