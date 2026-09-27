"""An intent's own default point, said the way that intent's template says it.

The compiler answers a point on the player-games relation with one generic
sentence per skeleton (:mod:`~association.query.compose.sentence`). Where the
point a question compiled to IS an intent's own default point - the one its
template answers - the answer has to read exactly as the template's does,
text and ``data`` both (plan item 2, step 2a: presentation parity), or folding
that template into the compiler would move every answer it gives.

So this module reads nothing of its own and phrases nothing of its own. The
subject, the span and every narrowing are the compiler's
(:func:`~association.query.compose.core.compile_query`, through the shared
steps in :mod:`association.query.templates.common`); the numbers are either
the compiler's own rows (``single_game_high``, ``threshold_count``, whose
templates have no reader separate from their orchestration) or the template's
own reader over the compiler's narrowing (``game_log``'s
``_player_game_log`` and ``player_stat``'s ``_box_score_player_stat``, which
take a settled narrowing already); and every sentence, caveat and ``data`` key
comes from the template's own phrasing helpers - one definition each, never a
second copy here.

A presenter returns ``None`` for a point that is not the intent's own (the
question's words moved it, or it carries something the template would have
refused), and the compiler's generic sentence answers instead, as before.

.. versionadded:: 4.5.0
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import duckdb

from association.nba.season import eastern_date
from association.query.conditions import _PLAYER_GAME_TABLES
from association.query.player_games import REBUILT_STATS
from association.query.reading import Scope
from association.query.templates.common import (
    HISTORY_COLUMNS,
    STAT_LABELS,
    THRESHOLD_STAT_COLUMNS,
    TemplateResult,
    TemplateUnsupported,
    _condition_scope,
    _optional_team,
    _player_relation_season_type,
    _Span,
    check_scope,
)
from association.query.templates.games import _game_log_lines, _log_extras, _player_game_log, _player_game_log_mixed
from association.query.templates.players import (
    ADVANCED_STATS,
    SHOOTING_STATS,
    _box_score_player_stat,
    _empty_box_scores,
    _game_span,
    _phrase_threshold_count,
    _player_history_read,
    _player_history_subject,
    _player_stat_season_line,
    _player_stat_season_line_subject,
    _rebuilt_in_scope,
    _single_game_high_answer,
    _single_game_high_redirect,
    _single_game_high_result_data,
    _threshold_count_lines,
    _threshold_count_notes,
    _wanted_stats,
)
from association.query.templates.splits import _record_when_answer, _record_when_query

from .adapt import DEFAULT_GAME_LOG_LIMIT, _clamp, _to_reading_scope
from .core import LINE, Query, Unsupported, compile_query, run
from .move import _stat_measure

#: A presenter: the connection and the compiled point (its scope the intent's
#: slots, typed), to the template's own answer - or ``None`` where the point
#: is not the intent's own. The templates' own helpers below still take the
#: slot dict, handed ``q.scope.to_slots()`` until they read the Scope.
Presenter = Callable[[duckdb.DuckDBPyConnection, Query], TemplateResult | None]


def _stat_column(scope: Scope) -> str | None:
    """The router's ``stat`` as the box-score column the count and single-game
    templates whitelist (:data:`~association.query.templates.common.THRESHOLD_STAT_COLUMNS`)."""
    return THRESHOLD_STAT_COLUMNS.get(scope.stat) if scope.stat is not None else None


def _present_game_log(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """``game_log``'s own listing, over the compiler's settled player, span
    and narrowing: its columns (``_log_extras``), its rebuilt-line rule, its
    heading, table, averages and notes (``templates.games._player_game_log``).

    Only a named player's log in date order with no measure the question's
    words added beyond the ones the log shows for the router's ``stat`` - a
    threshold the log would refuse (``_game_log_lines``), a stat it has no
    column for (``_log_extras`` - the usage rate of #222), or a measure the
    words moved in are the compiler's own point, answered by its own sentence."""
    if q.skeleton != "rows" or q.order != "date" or q.subject != "player" or q.predicates or q.group != "none":
        return None
    scope = q.scope
    try:
        extras = _log_extras(scope.stat)
        _game_log_lines(scope.below, scope.above, scope.threshold)
    except TemplateUnsupported:
        return None
    # The router's own stat, which the log shows as its extra columns; a
    # measure the question's words moved in instead is the compiler's point.
    if [m for m in q.measures if m not in LINE] not in ([], [_stat_measure(scope.stat)]):
        return None
    compiled = compile_query(con, q)
    if compiled.player is None:
        return None
    limit = _clamp(scope.limit, DEFAULT_GAME_LOG_LIMIT)
    asked = scope.limit if scope.limit is not None and scope.limit >= 1 else None
    if scope.season_type_unstated and not compiled.narrowed.date and not scope.span and not scope.game_n:
        # "His last N games" naming no season type: the template reads each
        # type on its own and merges them by date, saying how many of each
        # it kept (``_player_game_log_mixed``) - over the season the
        # compiler settled, and the opponent it resolved.
        if compiled.span.season is None:
            return None
        return _player_game_log_mixed(
            con,
            compiled.player,
            compiled.span.season,
            scope,
            opponent=compiled.narrowed.opponent,
            measures=_game_log_lines(scope.below, scope.above, scope.threshold),
            extras=extras,
            limit=limit,
            asked=asked,
        )
    return _player_game_log(con, compiled.player, compiled.span, compiled.narrowed, extras, limit=limit, asked=asked, ascending=q.direction == "asc")


def _present_record_when(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """``record_when``'s own table - his team's record in the games he
    reached the line, the games he fell short, and all of them, with the
    margin, the teams' names and the unseen-games note - read by the
    template's own ``_record_when_query``/``_record_when_answer`` over the
    compiler's settled player and his games (the line itself taken off, since
    the table groups by it rather than keeping only one side)."""
    scope = q.scope
    stat = scope.stat
    column = _stat_column(scope)
    threshold = scope.threshold
    if stat is None or column is None or threshold is None or threshold < 1:
        return None
    if q.skeleton != "scalar" or q.aggregate != "record" or q.subject != "player" or q.predicates != [(column, ">=", threshold)] or [m for m in q.measures if m != column]:
        return None
    # The template's own scope - it names the span in the heading, the floor
    # note and the unseen-games count - read off the same slots the same way.
    covered = _condition_scope(scope.season, "career" if scope.season_n else scope.span, scope.season_type, _PLAYER_GAME_TABLES, since=scope.since)
    compiled = compile_query(con, replace(q, predicates=[], measures=[]))
    if compiled.player is None:
        return None
    team = _optional_team(con, scope.team, season=scope.season)
    if isinstance(team, TemplateResult):
        return team
    found = _record_when_query(con, covered, compiled.player, team, column, threshold, compiled.narrowed)
    if isinstance(found, TemplateResult):
        return found
    rows, names, base, params = found
    return _record_when_answer(con, covered, compiled.player, stat, threshold, rows, names, base, params, compiled.narrowed, scope)


def _present_player_stat(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """``player_stat``'s own narrowed average (``templates.players._box_score_player_stat``)
    over the compiler's settled player, span and narrowing. An advanced rate
    reads its own table and is left to the compiler's sentence. An
    unnarrowed line is the season-line source's (:func:`_present_player_stat_season_line`)."""
    if q.skeleton != "scalar" or q.aggregate != "per_game" or q.subject != "player" or q.predicates:
        return None
    if q.source == "seasons":
        return _present_player_stat_season_line(con, q)
    stat = q.scope.stat
    if stat is not None and stat in ADVANCED_STATS:
        return None
    shooting = SHOOTING_STATS.get(stat) if stat is not None else None
    try:
        wanted = [] if shooting else _wanted_stats(q.scope)
    except TemplateUnsupported:
        return None
    if not shooting and sorted(q.measures) != sorted(wanted):
        return None
    compiled = compile_query(con, q)
    if compiled.player is None:
        return None
    return _box_score_player_stat(con, compiled.player, compiled.span, compiled.narrowed, wanted, shooting)


def _present_player_stat_season_line(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """``player_stat``'s unnarrowed line - a season or a career, read from
    the season line (``player_season_stats_deduped``) - over the player and
    span the template settles for it (``_player_stat_season_line_subject``)
    and read by its own reader (``_player_stat_season_line``): the second
    relation, never re-derived from box scores.

    Only where the question's own words left the router's stat alone (the
    adapter's own measures): a measure the words moved in is a point the
    season line does not say, and the compiler declines it as before."""
    scope = q.scope
    stat = scope.stat
    try:
        own = _to_reading_scope("player_stat", scope)
    except Unsupported:
        return None
    # The router's own stat, whichever way the point carries it: the
    # adapter's measures, or the question's word for that same stat ("3pt
    # percentage" is three_pct, which the router filed threePointFieldGoalPct).
    stat_measure = _stat_measure(stat)
    if own.source != "seasons" or (q.measures != own.measures and (stat_measure is None or q.measures != [stat_measure])):
        return None
    if not (stat is not None and (stat in ADVANCED_STATS or stat in SHOOTING_STATS)):
        try:
            _wanted_stats(scope)
        except TemplateUnsupported:
            return None
    subject = _player_stat_season_line_subject(con, scope)
    if isinstance(subject, TemplateResult):
        return subject
    return _player_stat_season_line(con, *subject, scope)


def _present_player_history(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """``player_history``'s own table - the stat season by season from the
    season line, newest first, the default four or the count asked for, and
    the career line under a career - over the player the template settles
    (``_player_history_subject``) and read by its own reader
    (``_player_history_read``).

    Only where the point is a season-line history of the router's own stat:
    a stat with no per-season column, a span the template refuses, or a
    measure the question's words moved in is the game-level reading's
    (``move.games_reading``), answered by the compiler's sentence."""
    scope = q.scope
    stat = scope.stat
    if q.source != "seasons" or q.group != "season" or stat is None or stat not in HISTORY_COLUMNS:
        return None
    if scope.span not in (None, "career"):
        return None
    if _stat_measure(stat) not in (None, *q.measures[:1]):
        return None
    player = _player_history_subject(con, scope)
    if isinstance(player, TemplateResult):
        return player
    return _player_history_read(con, player, scope)


def _count_season(scope: Scope, span: _Span) -> tuple[int | None, int, int | None]:
    """The season a count or a single-game high covers (``None`` for a
    career), the season type named in its sentence, and the ordinal that
    named the season, if one did - read off the compiler's settled span,
    since that is the span the rows were counted over."""
    return span.season, _player_relation_season_type(scope), span.ordinal


def _present_single_game_high(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """``single_game_high``'s own sentence and ``data`` over the compiler's
    top games by the stat: the leader, the date and opponent, "Next: ..."
    for the league, and the template's span, empty-box-score, withheld-stat,
    rebuilt-line and defaulted-season notes, each from its own helper."""
    scope = q.scope
    stat = scope.stat
    column = _stat_column(scope)
    if stat is None or column is None or q.skeleton != "rows" or q.order != "measure" or q.direction != "desc" or q.predicates or q.position or not q.measures or q.measures[0] != column:
        return None
    out = run(con, q)
    span: _Span = out["span"]
    player = out["entity"]
    season, season_type, _ = _count_season(scope, span)
    career = season is None
    defaulted = not career and not scope.season
    from_rebuilt = bool(out["rebuilt"])
    label = STAT_LABELS.get(stat, stat)
    name = player.name if player is not None else None
    games = [
        {"player": r.get("player") or name, "value": r[column], "date": eastern_date(r["day"]), "opponent": r["opponent"], "reconstructed": bool(r["reconstructed"])}
        for r in out["rows"]
        if r[column] is not None
    ]
    game_span = _game_span(con, season, season_type, player)
    shape = f"most {label}s in a single game" + (f", {name}" if name else "") + f", {game_span.caption}"
    player_id = player.id if player is not None else None
    empty = _empty_box_scores(con, season, season_type, player_id, covered_by_rebuild=from_rebuilt)
    withheld = 0 if games or column in REBUILT_STATS else _rebuilt_in_scope(con, season, season_type, player_id)
    headline = _single_game_high_answer(games, label, game_span, name, empty=empty, withheld=withheld)
    redirect = _single_game_high_redirect(con, defaulted, player, games, empty, withheld, season_type)
    data = {"question_shape": shape, "season": season, "span": "career" if career else None, "stat": stat, "games": games, "empty_box_scores": empty[0]}
    return TemplateResult(data=_single_game_high_result_data(data, headline, redirect), answer=headline + redirect)


def _threshold_count_is_own_point(q: Query, column: str) -> bool:
    """Whether ``q`` counts exactly the games ``threshold_count`` counts: the
    router's own stat at its own threshold (or a below/above line carrying
    that threshold instead, which the template reads as the count), for one
    player or grouped by player over the league - nothing the question's
    words added."""
    threshold = q.scope.threshold
    counted = [(column, ">=", threshold)] if threshold is not None else []
    if q.predicates not in (counted, []) or q.position:
        return False
    if q.subject == "player":
        return q.skeleton == "scalar" and q.aggregate == "count"
    return q.skeleton == "grouped" and q.aggregate == "count" and q.group == "player"


def _present_threshold_count(con: duckdb.DuckDBPyConnection, q: Query) -> TemplateResult | None:
    """``threshold_count``'s own sentence and ``data`` over the compiler's
    count - one player's, or the league's by player - with the template's
    span, empty-box-score, withheld-stat and rebuilt-line notes, each from
    its own helper."""
    scope = q.scope
    stat = scope.stat
    column = _stat_column(scope)
    threshold = scope.threshold
    if stat is None or column is None or threshold is None or threshold < 1 or not _threshold_count_is_own_point(q, column):
        return None
    try:
        _, _, scope_text = _threshold_count_lines(stat, threshold, scope.below, scope.above)
    except TemplateUnsupported:
        return None
    out = run(con, q)
    span: _Span = out["span"]
    player = out["entity"]
    season, season_type, ordinal = _count_season(scope, span)
    rows = _present_threshold_count_rows(q, out)
    player_name = player.name if player is not None else None
    game_span = _game_span(con, season, season_type, player, ordinal=ordinal)
    empty = _empty_box_scores(con, season, season_type, player.id if player is not None else None, covered_by_rebuild=bool(out["rebuilt"]))
    phrase = game_span.preface + _phrase_threshold_count(rows, scope_text, game_span.when, player_name)
    notes = _threshold_count_notes(con, (season, season_type), player, rows, column, STAT_LABELS.get(stat, stat), game_span, empty)
    data = {
        "question_shape": f"games with {scope_text}, {game_span.caption}",
        "season": season,
        "span": "career" if season is None else None,
        "leaders": [{"player": name, "games": games} for name, games, _ in rows],
        "empty_box_scores": empty[0],
        "rebuilt_games": rows[0][2] if rows else 0,
        # The trailing "Next: ..." restates the table in prose - not the headline.
        "headline": phrase.split(" Next: ")[0],
        "notes": notes,
    }
    return TemplateResult(data=data, answer=" ".join([phrase, *notes]))


def _present_threshold_count_rows(q: Query, out: dict[str, Any]) -> list[tuple[Any, int, int]]:
    """The compiler's count as ``threshold_count``'s own rows - ``(name,
    qualifying games, rebuilt games among them)``, most first: one row for a
    named player with any, none for one with none, one per player for the
    league."""
    player = out["entity"]
    if q.subject == "player":
        games = int(out["rows"][0].get("games") or 0) if out["rows"] else 0
        return [(player.name, games, out["rebuilt_by_row"][0] if out["rebuilt_by_row"] else 0)] if games and player is not None else []
    return [(r["group"], int(r["games"]), rebuilt) for r, rebuilt in zip(out["rows"], out["rebuilt_by_row"], strict=True)]


#: Intent -> the presenter for its own default point.
PRESENTERS: dict[str, Presenter] = {
    "game_log": _present_game_log,
    "player_stat": _present_player_stat,
    "single_game_high": _present_single_game_high,
    "threshold_count": _present_threshold_count,
    "record_when": _present_record_when,
    "player_history": _present_player_history,
}
"""The intents whose own default point the compiler answers in that intent's
template's words - see the module docstring.

.. versionadded:: 4.5.0
"""


def present(con: duckdb.DuckDBPyConnection, intent: str, q: Query) -> TemplateResult | None:
    """``q`` answered as ``intent``'s template answers its own default point,
    or ``None`` where ``q`` is not that point (or the intent has no presenter)
    and the compiler's own sentence should answer instead. The intent's slots
    are ``q``'s own scope.

    .. versionadded:: 4.5.0
    """
    presenter = PRESENTERS.get(intent)
    if presenter is None:
        return None
    try:
        # Only where the template itself would take these slots: a scoping
        # slot it refuses (a league-wide ordinal season on single_game_high,
        # an opponent on threshold_count) is exactly a point that is NOT its
        # own, and the compiler's sentence says what was read.
        check_scope(intent, q.scope)
    except TemplateUnsupported:
        return None
    try:
        return presenter(con, q)
    except TemplateUnsupported as exc:
        # The relation refusing a slot while the point was settled - the
        # same outcome core.run gives the compiler's own sentence.
        raise Unsupported(f"relation: {exc}") from exc
