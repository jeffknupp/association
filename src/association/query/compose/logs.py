"""The game log's reader: a player's or a team's games listed, read into a
:class:`~association.query.result.Result` - rows, the count the window cut
them from, the per-row figures summed over exactly the rows shown, and the
remarks as kinds and facts. The sayer (:mod:`association.query.compose.say`)
words it. Phase 2's first slice (``ROADMAP.md``, "Phase 2, the expected
steps", step 0): the body of the retired ``game_log`` template, which the
presenter ``compose.present._present_game_log`` called over the compiler's
settled player and narrowing until 2026-10-03, moved here whole and split
into what reads and what says - numbers identical by construction, the
sentence reproduced word for word.

Two reads, as the template had: one season type
(:func:`_player_log`, :func:`_team_log`), and "last N games" naming no
season type, where each type is read on its own and the rows merged by
date (:func:`_player_log_mixed`, :func:`_team_log_mixed`), saying how many
of each type were kept.

.. versionadded:: 5.0.0
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import duckdb

from association.nba.season import current_season
from association.nba.season import eastern_date as _eastern_date
from association.query.entities import resolved_team, slot_season
from association.query.lines import MeasureFilter, measure_filters
from association.query.measures import log_extras, stat_measure
from association.query.notes import Note
from association.query.player_games import Narrowed, aggregate_sql
from association.query.player_relation import ResolvedSpan, box_score_notes_read, no_narrowed_games, scoped_games, span_of, whole_span
from association.query.reading import DEFAULT_GAME_LOG_LIMIT, Scope, Unsupported, _clamp_limit, unhonored_scoping
from association.query.result import Narrowing, Part, Refusal, Result, Rows, Span, Unanswered, Window
from association.query.team_games import TEAM_GAMES_SQL, TeamNarrowed
from association.query.team_relation import team_games

from .core import LINE, Compiled, Query, compile_over, compile_query, rows_of
from .team import TeamQuery, compile_team_over

# A player's log columns, by header -> player_game_log column. The four in
# _LOG_BASE are always shown, and a named stat adds its own: "luka ft log" and
# "kyle kuzma last 7 games fgm" are real queries, and the log used to show
# points, rebounds and assists whatever was asked.
LOG_COLUMNS: dict[str, str] = {
    "MIN": "minutes",
    "PTS": "points",
    "REB": "rebounds",
    "AST": "assists",
    "STL": "steals",
    "BLK": "blocks",
    "TO": "turnovers",
    "PF": "fouls",
    "+/-": "plusMinus",
    "OREB": "offensiveRebounds",
    "DREB": "defensiveRebounds",
    "FGM": "fieldGoalsMade",
    "FGA": "fieldGoalsAttempted",
    "3PM": "threePointFieldGoalsMade",
    "3PA": "threePointFieldGoalsAttempted",
    "FTM": "freeThrowsMade",
    "FTA": "freeThrowsAttempted",
}
"""A player log's column headers and the relation column behind each.

.. versionadded:: 5.0.0
"""

# Computed rather than stored: header -> (made header, attempted header, key).
LOG_PERCENTAGES: dict[str, tuple[str, str, str]] = {
    "FG%": ("FGM", "FGA", "fieldGoalPct"),
    "3P%": ("3PM", "3PA", "threePointFieldGoalPct"),
    "FT%": ("FTM", "FTA", "freeThrowPct"),
}
"""A player log's computed percentage headers: the made and attempted
headers behind each, and the key the row carries it under.

.. versionadded:: 5.0.0
"""

_LOG_BASE = ("MIN", "PTS", "REB", "AST")


def log_key(header: str) -> str:
    """The key a log row carries ``header``'s value under.

    .. versionadded:: 5.0.0
    """
    return LOG_PERCENTAGES[header][2] if header in LOG_PERCENTAGES else LOG_COLUMNS[header]


def _game_log_lines(below: Any, above: Any, threshold: Any) -> list[MeasureFilter]:
    """The lines a log keeps games under or over - the ``below``/``above``
    phrases (:func:`~association.query.lines.measure_filters`).
    A bare ``threshold`` beside them is the retired template's own refusal
    (it never read one), unless it is one of those phrases' own number,
    which the model files twice."""
    measures = measure_filters(below, above)
    if threshold is not None and not any(line.value == threshold for line in measures):
        raise Unsupported("game_log does not read a threshold - ask for games with at least N of a stat")
    return measures


def _pct(made: Any, attempted: Any) -> float | None:
    return 100.0 * made / attempted if made is not None and attempted else None


def _merge_season_types[R](rows_by_type: dict[int, list[R]], *, date_of: Callable[[R], Any], limit: int, ascending: bool) -> tuple[list[R], dict[int, int]]:
    """A "last N games" answer with no season type named reads both types
    separately (each a normal, single-type query) and merges here, by each
    row's date (``date_of``: the compiled row's ``day`` for a player, the
    first column for a team); kept newest (or oldest, for ``ascending``)
    first, down to ``limit`` overall. Returns the merged rows and how many
    of the kept ones came from each season type - the count a "reasonable
    default" has to show, per AGENTS.md: a user who gets 3 playoff games and
    2 regular-season ones has to be told that split."""
    tagged = [(row, season_type) for season_type, rows in rows_by_type.items() for row in rows]
    tagged.sort(key=lambda item: date_of(item[0]), reverse=not ascending)
    kept = tagged[:limit]
    counts: dict[int, int] = {}
    for _, season_type in kept:
        counts[season_type] = counts.get(season_type, 0) + 1
    return [row for row, _ in kept], counts


# --- the player's log ------------------------------------------------------


def _player_log_columns(extras: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """The columns to show and the relation columns fetched behind them."""
    headers = list(dict.fromkeys([*_LOG_BASE, *extras]))
    # A percentage is never fetched: it is computed from the made/attempted pair
    # behind it, which is fetched whether or not it is shown.
    needed = list(dict.fromkeys([*(h for h in headers if h in LOG_COLUMNS), *(c for h in headers if h in LOG_PERCENTAGES for c in LOG_PERCENTAGES[h][:2])]))
    return headers, needed


def _player_log_rows(rows: list[dict[str, Any]], needed: list[str], headers: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Each row the compiled statement returned (:func:`~association.query.compose.core._row_select`'s
    fixed columns, then the measures by name) turned into a display game
    (with its percentages derived) and the raw made/attempted values behind
    it, kept for the averages."""
    games: list[dict[str, Any]] = []
    raws: list[dict[str, Any]] = []
    for row in rows:
        raw = {h: row[LOG_COLUMNS[h]] for h in needed}
        won = row["won"]
        game: dict[str, Any] = {
            "date": _eastern_date(row["day"]),
            "season": row["season"],
            "opponent": row["opponent"],
            "home_away": "home" if row["home"] else "away",
            "result": None if won is None else ("W" if won else "L"),
            "reconstructed": bool(row["reconstructed"]),
        }
        for h in headers:
            game[log_key(h)] = _pct(raw[LOG_PERCENTAGES[h][0]], raw[LOG_PERCENTAGES[h][1]]) if h in LOG_PERCENTAGES else raw[h]
        games.append(game)
        raws.append(raw)
    return games, raws


def _player_log_averages(headers: list[str], raws: list[dict[str, Any]]) -> dict[str, float | None]:
    """Per-game averages over exactly the rows being shown, made/attempted
    summed rather than a mean of the per-game percentages."""
    averages: dict[str, float | None] = {}
    for h in headers:
        if h in LOG_PERCENTAGES:
            made_h, attempted_h, key = LOG_PERCENTAGES[h]
            averages[key] = _pct(sum(r[made_h] or 0 for r in raws), sum(r[attempted_h] or 0 for r in raws))
        else:
            present = [r[h] for r in raws if r[h] is not None]
            averages[LOG_COLUMNS[h]] = sum(present) / len(present) if present else None
    return averages


def _player_log_total(con: duckdb.DuckDBPyConnection, narrowed: Narrowed, *, rebuilt: bool) -> int:
    """How many of the player's games match every narrowing the question
    carries, before the window (``order``/``limit``) cuts them to the rows
    listed - what the heading says "of how many" with (F149). Read through
    :func:`~association.query.player_games.aggregate_sql` over
    :func:`~association.query.player_relation.whole_span`, never a
    hand-written ``COUNT(*)``."""
    sql, params = aggregate_sql(whole_span(narrowed), ["COUNT(*)"], rebuilt=rebuilt)
    row = con.execute(sql, params).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _player_narrowing(narrowed: Narrowed) -> Narrowing:
    return Narrowing(
        phrase=narrowed.filters(dated=False),
        opponent=narrowed.opponent.name if narrowed.opponent else None,
        venue=narrowed.venue,
        without=tuple(mate.name for mate in narrowed.without),
    )


def _player_log(con: duckdb.DuckDBPyConnection, compiled: Compiled, headers: list[str], needed: list[str], *, asked: int | None, limit: int, ascending: bool) -> Result:
    """The listing over one season type - the compiled statement's rows -
    and the per-game averages over exactly the rows in it."""
    player, span, narrowed = compiled.player, compiled.span, compiled.narrowed
    assert player is not None
    rows = rows_of(con, compiled)
    about = Span(season=span.season, season_type=span.season_type, career=span.career and not narrowed.date, date=narrowed.date)
    narrowing = _player_narrowing(narrowed)
    window = Window(limit=limit, asked=asked, ascending=ascending)
    if not rows:
        empty = no_narrowed_games(con, player, span, narrowed, rebuilt=compiled.rebuilt)
        return Result(subject=player.name, relation="player", span=about, narrowing=narrowing, window=window, empty=empty)
    games, raws = _player_log_rows(rows, needed, headers)
    averages = _player_log_averages(headers, raws)
    total = _player_log_total(con, narrowed, rebuilt=compiled.rebuilt)
    seasons = [g["season"] for g in games]
    about = Span(season=span.season, season_type=span.season_type, career=about.career, date=narrowed.date, first=min(seasons), last=max(seasons), years=span.years(min(seasons), max(seasons)))
    notes: list[Note] = []
    count = len(games)
    if asked and count < asked and not narrowed.date:
        notes.append(Note("window_short", {"found": count, "asked": asked, "season": None if span.career else span.season or current_season(), "season_type": span.season_type}))
    rebuilt_shown = sum(1 for g in games if g["reconstructed"])
    notes += box_score_notes_read(con, player, span, narrowed, career_note=not narrowed.date, rebuilt=compiled.rebuilt, rebuilt_shown=rebuilt_shown)
    body = Rows(columns=tuple(headers), rows=tuple(games), total_before_window=total, summary=averages)
    return Result(subject=player.name, relation="player", span=about, narrowing=narrowing, window=window, parts=(Part(body=body),), notes=tuple(notes))


def _player_log_mixed(con: duckdb.DuckDBPyConnection, q: Query, compiled: Compiled, headers: list[str], needed: list[str], *, asked: int | None, limit: int) -> Result | Unanswered:
    """A player's "last N games" with no season type named: both types, read
    separately and merged by date. Every other narrowing resolves the same
    under either type, so each type is narrowed exactly as
    :func:`~association.query.player_relation.scoped_games` already does
    for a single type, once per type, over the player and the season the
    first compile settled, and compiled over that
    (:func:`~association.query.compose.core.compile_over`). The notes are
    read once per type and de-duplicated, since a rebuilt-line note or a
    ``without`` note reads identically whichever type it came from."""
    player, season, scope = compiled.player, compiled.span.season, q.scope
    assert player is not None and season is not None
    measures = measure_filters(scope.below, scope.above)
    per_type: dict[int, Compiled] = {}
    for season_type in (2, 3):
        type_span = ResolvedSpan(season, season_type)
        narrowed = scoped_games(con, player, type_span, scope, opponent=compiled.narrowed.opponent, measures=measures)
        if isinstance(narrowed, Unanswered):
            return narrowed
        per_type[season_type] = compile_over(con, q, player, type_span, narrowed)
    rows_by_type = {season_type: rows_of(con, each) for season_type, each in per_type.items()}
    rows, counts = _merge_season_types(rows_by_type, date_of=lambda row: row["day"], limit=limit, ascending=False)
    narrowing = _player_narrowing(per_type[2].narrowed)
    about = Span(season=season)
    window = Window(limit=limit, asked=asked, ascending=False)
    if not rows:
        # Both types came back empty, so the missing fact really is "no games
        # this season" - the same sentence a single-type refusal gives, with
        # no season type to (wrongly) blame it on.
        empty = Refusal(kind="no_games_in_season", facts={"player": player.name, "season": season, "narrowing": narrowing.phrase})
        return Result(subject=player.name, relation="player", span=about, narrowing=narrowing, window=window, facts={"mixed": True}, empty=empty)
    games, raws = _player_log_rows(rows, needed, headers)
    averages = _player_log_averages(headers, raws)
    notes: list[Note] = []
    count = len(games)
    if asked and count < asked:
        notes.append(Note("window_short", {"found": count, "asked": asked, "season_type": [2, 3]}))
    rebuilt_shown = sum(1 for g in games if g["reconstructed"])
    seen: list[Note] = []
    for season_type in sorted(per_type):
        each = per_type[season_type]
        for note in box_score_notes_read(con, player, each.span, each.narrowed, career_note=False, rebuilt=each.rebuilt, rebuilt_shown=rebuilt_shown):
            if note not in seen:
                seen.append(note)
    notes += seen
    body = Rows(columns=tuple(headers), rows=tuple(games), summary=averages, by_season_type=counts)
    return Result(subject=player.name, relation="player", span=about, narrowing=narrowing, window=window, parts=(Part(body=body),), notes=tuple(notes), facts={"mixed": True})


def read_player_log(con: duckdb.DuckDBPyConnection, q: Query, *, stated: frozenset[str]) -> Result | Unanswered | None:
    """A named player's log: the compiled statement of the planned point,
    its measures the log's columns (the four of the line and the named
    stat's own), executed over the compiler's settled player, span and
    narrowing - or ``None`` where the log's own words do not say the point
    and the compiler's sentence answers instead: a point that is not rows
    in date order with no predicate, a stat the log has no column for
    (:func:`~association.query.measures.log_extras`), a bare
    threshold, or a measure the question's words moved in beyond the log's
    own columns, or a narrowing beyond what the log's words state
    (``stated``: ``compose.plan.STATED_SCOPING``'s set for the intent -
    the log's own, or the season line's for a ``player_stat`` window the
    retired template handed to the log). A
    :class:`~association.query.result.Refusal` or :class:`~association.query.result.Clarify` back is the
    relation's own refusal (an ambiguous name), as before.

    .. versionadded:: 5.0.0

    .. versionchanged:: 5.0.0
       Executes the compiled statement (Phase 2, step 1's merge) where it
       read the rows through its own ``rows_sql`` call beside it; measured
       equal on every recorded player log first.
    """
    if q.skeleton != "rows" or q.order != "date" or q.subject != "player" or q.predicates or q.group != "none":
        return None
    if unhonored_scoping("game_log", q.scope, stated):
        return None
    scope = q.scope
    try:
        extras = log_extras(scope.stat)
        _game_log_lines(scope.below, scope.above, scope.threshold)
    except Unsupported:
        return None
    # The router's own stat, which the log shows as its extra columns; a
    # measure the question's words moved in instead is the compiler's point.
    if [m for m in q.measures if m not in LINE] not in ([], [stat_measure(scope.stat)]):
        return None
    headers, needed = _player_log_columns(extras)
    limit = _clamp_limit(scope.limit, DEFAULT_GAME_LOG_LIMIT)
    # The log's point, with the columns it shows as the measures: the rebuilt
    # rule (core._rebuilt_for) is read over the columns shown, as the log's own was.
    log_point = replace(q, measures=[LOG_COLUMNS[h] for h in needed], limit=limit)
    compiled = compile_query(con, log_point)
    if compiled.player is None:
        return None
    asked = scope.limit
    if scope.season_type_unstated and not compiled.narrowed.date and not scope.span and not scope.game_n:
        # "His last N games" naming no season type: each type on its own,
        # merged by date, over the season the compiler settled.
        if compiled.span.season is None:
            return None
        return _player_log_mixed(con, log_point, compiled, headers, needed, asked=asked, limit=limit)
    return _player_log(con, compiled, headers, needed, asked=asked, limit=limit, ascending=q.direction == "asc")


# --- the team's log --------------------------------------------------------


def _team_log_refusals(without: Any, measures: list[MeasureFilter], game_n: Any) -> None:
    """What a team's log cannot narrow by: a teammate's absence is
    with_without's question, a line on a box-score stat keeps a PLAYER's
    games - a team's log has no such column - and a game of a series is
    numbered on the player relation only, so far."""
    if without:
        raise Unsupported("a team's games without one of its players is a with_without question")
    if measures:
        raise Unsupported("a line on a box-score stat keeps a PLAYER's games; a team's log has no such column")
    if game_n:
        raise Unsupported("a team's log does not number the games of a series yet")


def _team_log_games(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each row of the team compiler's ``rows`` read
    (:data:`~association.query.compose.team.TEAM_ROW_COLUMNS`) as a display
    game. Reads ``team_games.won`` (a nullable boolean, computed once in the
    relation) rather than comparing a raw ``winner_team_id``."""
    return [{"date": str(r["day"]), "home_away": r["side"], **{key: r[key] for key in ("opponent", "team_score", "opponent_score", "won", "season")}} for r in rows]


def _team_log_summary(games: list[dict[str, Any]]) -> dict[str, int]:
    """The wins/losses record tallied over exactly the rows being shown
    rather than recounted later - that recount is where a wins/losses total
    gets inverted - and the games with no recorded result."""
    wins = sum(1 for g in games if g["won"] is True)
    losses = sum(1 for g in games if g["won"] is False)
    return {"wins": wins, "losses": losses, "unknown": len(games) - wins - losses}


def _team_log_none(con: duckdb.DuckDBPyConnection, team_name: str, span: ResolvedSpan, narrowed: TeamNarrowed) -> Refusal:
    """Which fact is missing when no row matched: the team's games in that
    span, or the match - so the cause names the right one. Reads
    ``narrowed`` WITHOUT its narrowing, the discipline
    :func:`~association.query.player_relation.no_narrowed_games` uses for a
    player."""
    where, params = narrowed.clauses(narrowed=False)
    season_col = "year(tg.eastern_date)" if span.season_type == 3 else "tg.season"
    found = con.execute(f"{TEAM_GAMES_SQL} SELECT COUNT(*), MIN({season_col}), MAX({season_col}) FROM team_games tg WHERE {where}", params).fetchone()
    total, first, last = found if found else (0, None, None)
    if not total:
        from association.query.season_text import season_phrase

        where_period = season_phrase(span.season, span.season_type) if span.season is not None else f"{span.kind}s on record"
        return Refusal(kind="no_team_games_in", facts={"team": team_name, "span": where_period, "narrowing": ""})
    on_date = f" on {narrowed.date}" if narrowed.date else ""
    return Refusal(
        kind="team_none_matched", facts={"team": team_name, "games": total, "during": span.during(first, last, whose="all seasons on record"), "narrowing": f"{narrowed.filters()}{on_date}"}
    )


def _team_log(con: duckdb.DuckDBPyConnection, q: TeamQuery, team: Any, span: ResolvedSpan, narrowed: TeamNarrowed, *, limit: int, ascending: bool, stat: Any) -> Result:
    """A team's games in ``span``, narrowed as ``narrowed`` already reflects:
    the team compiler's ``rows`` read over the settled team
    (:func:`~association.query.compose.team.compile_team_over`)."""
    team_name = team.name
    rows = rows_of(con, compile_team_over(replace(q, shape="rows"), team, span, narrowed, limit=limit, ascending=ascending))
    narrowing = Narrowing(phrase=narrowed.filters(), opponent=narrowed.opponent.name if narrowed.opponent else None, venue=narrowed.venue)
    window = Window(limit=limit, ascending=ascending)
    if not rows:
        about = Span(season=span.season, season_type=span.season_type, career=span.season is None, date=narrowed.date)
        return Result(subject=team_name, relation="team", span=about, narrowing=narrowing, window=window, facts={"stat": stat}, empty=_team_log_none(con, team_name, span, narrowed))
    games = _team_log_games(rows)
    seasons = [g["season"] for g in games]
    about = Span(season=span.season, season_type=span.season_type, career=span.season is None, date=narrowed.date, first=min(seasons), last=max(seasons), years=span.years(min(seasons), max(seasons)))
    body = Rows(rows=tuple(games), summary=_team_log_summary(games))
    return Result(subject=team_name, relation="team", span=about, narrowing=narrowing, window=window, parts=(Part(body=body),), facts={"stat": stat})


def _team_mixed_rows(con: duckdb.DuckDBPyConnection, team: Any, season: int, *, opponent: Any, venue: Any, limit: int) -> tuple[list[dict[str, Any]], dict[int, int], str] | Unanswered:
    """A team's newest ``limit`` games of ``season`` over BOTH season types -
    each type narrowed on its own through
    :func:`~association.query.team_relation.team_games`, compiled as the
    team compiler's ``rows`` read over the settled team
    (:func:`~association.query.compose.team.compile_team_over`) and merged
    by date - with how many of the kept games each type gave and the
    narrowing's own phrase. Shared with the team compiler's window sum
    (``compose.team``), which summed one type alone before it."""
    rows_by_type: dict[int, list[dict[str, Any]]] = {}
    narrowed_text = ""
    point = TeamQuery(scope=Scope(venue=venue), shape="rows")
    for season_type in (2, 3):
        type_span = ResolvedSpan(season, season_type)
        narrowed = team_games(con, team, type_span, Scope(venue=venue), opponent=opponent)
        if isinstance(narrowed, Unanswered):
            return narrowed
        narrowed_text = narrowed.filters()
        rows_by_type[season_type] = rows_of(con, compile_team_over(point, team, type_span, narrowed, limit=limit))
    rows, counts = _merge_season_types(rows_by_type, date_of=lambda row: row["day"], limit=limit, ascending=False)
    return rows, counts, narrowed_text


def _team_log_mixed(con: duckdb.DuckDBPyConnection, team_name: str, team: Any, season: int, *, opponent: Any, venue: Any, limit: int, stat: Any) -> Result | Unanswered:
    """A team's "last N games" with no season type named: both types, read
    separately and merged by date. Only an opponent and a venue reach here:
    ``without`` refuses outright for a team, a date fixes one game
    regardless of its type, and a series game number is the player
    relation's alone."""
    mixed = _team_mixed_rows(con, team, season, opponent=opponent, venue=venue, limit=limit)
    if isinstance(mixed, Unanswered):
        return mixed
    rows, counts, narrowed_text = mixed
    narrowing = Narrowing(phrase=narrowed_text, opponent=opponent, venue=venue)
    window = Window(limit=limit, ascending=False)
    about = Span(season=season)
    if not rows:
        return Result(
            subject=team_name,
            relation="team",
            span=about,
            narrowing=narrowing,
            window=window,
            facts={"stat": stat, "mixed": True},
            empty=Refusal(kind="no_team_games_in", facts={"team": team_name, "span": str(season), "narrowing": narrowed_text}),
        )
    games = _team_log_games(rows)
    body = Rows(rows=tuple(games), summary=_team_log_summary(games), by_season_type=counts)
    return Result(subject=team_name, relation="team", span=about, narrowing=narrowing, window=window, parts=(Part(body=body),), facts={"stat": stat, "mixed": True})


def read_team_log(con: duckdb.DuckDBPyConnection, q: TeamQuery, *, stated: frozenset[str]) -> Result | Unanswered | None:
    """A team's games listed, over the team relation the team compiler
    planned. ``None`` where the log's words do not state a narrowing the
    scope carries (``stated``, the log's set in
    ``compose.plan.STATED_SCOPING``), so the team compiler's own
    sentence answers; a :class:`~association.query.result.Refusal` or
    :class:`~association.query.result.Clarify` back is the relation's own refusal (no such team, a coverage floor).
    Both orderings are explicit: "first game" and "last game" differ only
    by ORDER BY direction, and LIMIT 1 without one returns an arbitrary row.

    .. versionadded:: 5.0.0
    """
    from association.query.coverage import coverage_refusal

    scope = q.scope
    if unhonored_scoping("game_log", scope, stated):
        return None
    refused = coverage_refusal("game_log", scope)
    if refused is not None:
        return refused
    season_type = scope.season_type or 2
    limit = _clamp_limit(scope.limit, default=DEFAULT_GAME_LOG_LIMIT)
    ascending = scope.order == "first"
    date = scope.date
    opponent, venue, span, without = scope.opponent, scope.venue, scope.span, scope.without
    game_n = scope.game_n
    measures = _game_log_lines(scope.below, scope.above, scope.threshold)
    # A date names its game outright, so it replaces the season rather than
    # being filtered inside it.
    season = None if date else scope.season
    span = "career" if date else span
    mixed = scope.season_type_unstated and not date and not span and not game_n
    team_text = scope.team
    if not team_text or scope.player:
        raise Unsupported("game_log needs a team or a player")
    team = resolved_team(con, team_text, season=slot_season(scope))
    if isinstance(team, Unanswered):
        return team
    _team_log_refusals(without, measures, game_n)
    if mixed and (scope.since or scope.until or scope.situation):
        # The both-types read is a plain window (an opponent and a venue at
        # most); a range of seasons or a calendar narrowing is refused, as
        # the team compiler's window sum refuses the same read.
        raise Unsupported("a window over both season types is read for a plain 'last N games' only")
    if mixed:
        resolved_season = span_of(span, season, 2, "games").season
        if resolved_season is None:
            raise Unsupported("a career span has no single season to read both season types within")
        return _team_log_mixed(con, team.name, team, resolved_season, opponent=opponent, venue=venue, limit=limit, stat=scope.stat)
    seasons = span_of(span, season, season_type, "games", since=scope.since, until=scope.until)
    narrowed = team_games(con, team, seasons, Scope(venue=venue), opponent=opponent, date=date)
    if isinstance(narrowed, Unanswered):
        return narrowed
    return _team_log(con, q, team, seasons, narrowed, limit=limit, ascending=ascending, stat=scope.stat)
