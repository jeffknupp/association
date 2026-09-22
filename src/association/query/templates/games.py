"""Game-level questions: game logs, head-to-head records, a team's or player's quarter or half, and two players' meetings.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

from typing import Any, cast

import duckdb

from association.nba.coverage import COVERAGE, POSTSEASON
from association.nba.franchises import season_name, season_name_sql
from association.nba.season import current_season
from association.nba.season import eastern_date as _eastern_date

from ..conditions import _PLAYER_GAME_TABLES, _cell, _matchup_line, _meetings, _names, _player_games, _Scope, _table, _totals, _unseen_meetings, box_source
from ..entities import Entity, find_players, resolve_team, teammate_names
from ..leaderboard import resolve_metric
from ..metrics import PER_GAME_MIN_GAMES, PER_GAME_MIN_POSTSEASON_GAMES
from ..player_games import _joined, rows_sql
from ..shotchart import SHOT_AVAILABILITY, SHOT_VALUE_SQL, UNSEPARABLE_SHOT_VALUES
from ..team_games import TEAM_GAMES_SQL, TeamNarrowed
from ..team_games import rows_sql as team_rows_sql
from .common import (
    _BOX_SCORES,
    _GAME_LOGS,
    _ISO_DATE,
    HISTORY_COLUMNS,
    PLAYER_STAT_COLUMNS,
    REBUILT_STATS,
    SEASON_TYPE_NAMES,
    STARTER_SIDES,
    THRESHOLD_STAT_COLUMNS,
    MeasureFilter,
    TemplateContext,
    TemplateResult,
    TemplateUnsupported,
    _box_score_notes,
    _career_end,
    _checked_venue,
    _clamp_limit,
    _condition_scope,
    _log_carries_rebuilt,
    _Narrowed,
    _no_games,
    _no_narrowed_games,
    _optional_team,
    _ordinal,
    _period,
    _resolved_player,
    _resolved_team,
    _resolved_teammate,
    _slot_season,
    _Span,
    _span_of,
    _where_in,
    measure_filters,
    scoped_games,
    scoped_player,
    team_games,
)


def _rebuilt_readable(con: duckdb.DuckDBPyConnection, needed: list[str]) -> bool:
    """Whether a log may show rebuilt lines, given the columns it will display.

    Every column shown has to be one a rebuild gets right - see
    :data:`REBUILT_STATS`. Ask for a player's fouls and the whole log falls back
    to fetched lines, because a rebuilt foul is wrong in about one game in six
    and a table gives no room to caveat one column.

    ``minutes`` is exempt rather than a failure: play-by-play cannot recover it,
    so it prints blank on a rebuilt row, which is the truth and is said in a
    note beneath the table.

    A function rather than an expression inline in the query, so the rule can be
    asserted on directly - the inline version could be relaxed with no test
    noticing.

    .. versionadded:: 2.2.0
    """
    return _log_carries_rebuilt(con) and all(_LOG_COLUMNS[h] in REBUILT_STATS for h in needed if _LOG_COLUMNS[h] != "minutes")


DEFAULT_GAME_LOG_LIMIT = 10


# A team's game log now reads association.query.team_games' relation
# (TEAM_GAMES_SQL's `team_games`) instead of its own join over team_box_stats
# and real_games - see that module's docstring for what moving onto it buys
# for free (the phantom 1993 season cannot double a game; a postseason is
# selected by the calendar year it was played in, from the relation's own
# Eastern date). `_TEAM_GAME_LOG_JOIN` is the one extra table a log's own
# SELECT needs beyond `team_games tg` itself: the opponent's OWN-SEASON name,
# which `team_games` does not carry (it has only `opponent_id`).
_TEAM_GAME_LOG_JOIN = " JOIN teams o ON o.team_id = tg.opponent_id"

_TEAM_GAME_LOG_SELECT = (
    "tg.eastern_date, tg.side, "
    f"{season_name_sql('o.team_id', 'tg.season', 'o.display_name')} AS opponent, "
    "tg.team_score, tg.opponent_score, tg.won, "
    "CASE WHEN tg.season_type = 3 THEN year(tg.eastern_date) ELSE tg.season END AS season"
)


# A player's log columns, by header -> player_game_log column. The four in
# _LOG_BASE are always shown, and a named stat adds its own: "luka ft log" and
# "kyle kuzma last 7 games fgm" are real queries, and the log used to show
# points, rebounds and assists whatever was asked.
_LOG_COLUMNS: dict[str, str] = {
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


# Computed rather than stored: header -> (made header, attempted header, key).
_LOG_PERCENTAGES: dict[str, tuple[str, str, str]] = {
    "FG%": ("FGM", "FGA", "fieldGoalPct"),
    "3P%": ("3PM", "3PA", "threePointFieldGoalPct"),
    "FT%": ("FTM", "FTA", "freeThrowPct"),
}


_LOG_BASE = ("MIN", "PTS", "REB", "AST")


GAME_LOG_STAT_COLUMNS: dict[str, tuple[str, ...]] = {
    "points": (),
    "rebounds": (),
    "assists": (),
    "minutes": (),
    "steals": ("STL",),
    "blocks": ("BLK",),
    "turnovers": ("TO",),
    "fouls": ("PF",),
    "plusMinus": ("+/-",),
    "offensiveRebounds": ("OREB",),
    "defensiveRebounds": ("DREB",),
    "fieldGoalsMade": ("FGM", "FGA"),
    "fieldGoalsAttempted": ("FGM", "FGA"),
    "fieldGoalPct": ("FGM", "FGA", "FG%"),
    "threePointFieldGoalsMade": ("3PM", "3PA"),
    "threePointFieldGoalsAttempted": ("3PM", "3PA"),
    "threePointFieldGoalPct": ("3PM", "3PA", "3P%"),
    "freeThrowsMade": ("FTM", "FTA"),
    "freeThrowsAttempted": ("FTM", "FTA"),
    "freeThrowPct": ("FTM", "FTA", "FT%"),
}
"""The columns a named ``stat`` adds to a player's game log, beyond minutes,
points, rebounds and assists. A shooting stat always brings its makes and
attempts, and a percentage is computed from them per game.

.. versionadded:: 2.1.0
"""


def _log_extras(stat: Any) -> tuple[str, ...]:
    """The columns a named stat adds to a player's log.

    ``stat`` is required in ROUTER_SCHEMA, so the model fills it on every
    question, including ones that name no stat at all - text that is not a stat
    name adds nothing. A REAL stat the log has no column for refuses instead:
    "luka ts% log" answered with no TS% in it would be the narrower answer
    passed off as the one asked for."""
    if not isinstance(stat, str) or not stat.strip():
        return ()
    if stat in GAME_LOG_STAT_COLUMNS:
        return GAME_LOG_STAT_COLUMNS[stat]
    if stat in PLAYER_STAT_COLUMNS or stat in HISTORY_COLUMNS or stat in THRESHOLD_STAT_COLUMNS or resolve_metric(stat) is not None:
        raise TemplateUnsupported(f"a game log has no per-game column for {stat!r}")
    return ()


def game_log(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A team's or a player's games. Both orderings are explicit: "first game"
    and "last game" differ only by ORDER BY direction, and LIMIT 1 without one
    returns an arbitrary row rather than either.

    A player's log can be narrowed to an ``opponent``, a ``venue`` and the games
    a teammate missed (``without``), and ``span`` "career" makes it every
    season, so "last 8 games vs the Pistons" reaches back as far as it has to.
    It lists games he played, adds the columns a named stat needs, and ends
    with per-game averages over exactly the games listed. A team's log honors
    the same slots except ``without``, which is with_without's question.

    .. versionchanged:: 2.1.0
       Honors ``opponent``, ``venue``, ``span`` and ``without``. Dates are the
       US Eastern date a game was played on rather than its UTC tip time, for
       ``date`` as well as for display. A player's log leaves out games he did
       not play, shows the columns a named stat needs and ends with an average
       row; a ``threshold`` is refused rather than ignored.

    .. versionchanged:: 2.2.0
       ``without`` takes every teammate the question names and lists a game
       only where none of them played.

    .. versionchanged:: 4.0.1
       A narrowed span left with no games at all - a stat that sends a rebuilt
       log back to its empty ESPN box scores, or a games-in-span guard that
       never widened to a rebuild at all - now says whose box scores are
       empty rather than the wrong-cause "no games found". "Anthony Davis
       turnovers, 2015" no longer reads as though he never played.

    .. versionchanged:: 4.1.0
       A player's log with a defaulted (unnamed) season and no games in it
       now redirects to the seasons he has on record, when there are any,
       rather than a refusal that reads as though his whole career were
       missing - "No 2026 regular season games found for Tim Hardaway" now
       also says he last appears in 2003 and names his 1995-2003 range. A
       season the question named outright keeps the plain refusal.

    .. versionchanged:: 4.3.0
       A ``team`` slot no longer wins outright over a named ``player`` (#147).
       With a player present, his own team narrows nothing and is dropped, a
       different team becomes his opponent, and a name nothing resolves to is
       dropped - an ``opponent`` already named wins over all three, since a
       ``team`` slot beside it is the noise the router routinely fills next to
       an already-correct opponent.
    .. versionchanged:: 4.4.0
       A "last N games" question naming no season type reads both and merges
       them by date, saying in the heading what it found - see
       ``router._route_game_log_recent_span`` and ``_game_log_team``'s and
       ``_game_log_player``'s own ``mixed`` handling.
    """
    con = ctx.con
    season_type = slots.get("season_type") or 2
    limit = _clamp_limit(slots.get("limit"), default=DEFAULT_GAME_LOG_LIMIT)
    asked = slots["limit"] if isinstance(slots.get("limit"), int) and slots["limit"] >= 1 else None
    ascending = slots.get("order") == "first"
    raw_date = slots.get("date")
    date = raw_date if isinstance(raw_date, str) and _ISO_DATE.match(raw_date) else None
    opponent, venue, span, without = slots.get("opponent"), slots.get("venue"), slots.get("span"), slots.get("without")
    game_n = slots.get("game_n")
    measures = _game_log_lines(slots.get("below"), slots.get("above"), slots.get("threshold"))
    # A date names its game outright, so it replaces the season rather than
    # being filtered inside it: the router's season is usually its "current"
    # default, and a date from last season looked for in this one finds nothing.
    season = None if date else slots.get("season")
    span = "career" if date else span
    # "Last N games" naming no season type: router._route_game_log_recent_span
    # only ever sets this beside `order="recent"` and a real `limit`, with no
    # `date`, `span`, `since` or `game_n` - guaranteed there, checked again
    # here rather than trusted blindly.
    mixed = bool(slots.get("season_type_unstated")) and not date and not span and not game_n
    slot_season = _slot_season(slots)

    team_text = slots.get("team")
    if team_text and not slots.get("player"):
        return _game_log_team(
            con,
            team_text,
            slot_season=slot_season,
            span=span,
            season=season,
            season_type=season_type,
            opponent=opponent,
            venue=venue,
            without=without,
            measures=measures,
            game_n=game_n,
            date=date,
            limit=limit,
            ascending=ascending,
            mixed=mixed,
        )
    return _game_log_player(
        con, slots, team_text, slot_season=slot_season, span=span, season=season, opponent=opponent, measures=measures, date=date, limit=limit, ascending=ascending, mixed=mixed, asked=asked
    )


def _game_log_team(
    con: duckdb.DuckDBPyConnection,
    team_text: str,
    *,
    slot_season: int | None,
    span: Any,
    season: int | None,
    season_type: int,
    opponent: Any,
    venue: Any,
    without: Any,
    measures: list[MeasureFilter],
    game_n: Any,
    date: str | None,
    limit: int,
    ascending: bool,
    mixed: bool,
) -> TemplateResult:
    """The team half of :func:`game_log`: resolve the team, refuse what a
    team's log cannot narrow by, and read either one season type or both.

    .. versionadded:: 4.4.0
       Split out of ``game_log`` when reading both season types pushed its
       complexity over the xenon C limit (AGENTS.md, "the way the templates
       and ``route()`` took").
    """
    team = _resolved_team(con, team_text, season=slot_season)
    if isinstance(team, TemplateResult):
        return team
    _team_game_log_refusals(without, measures, game_n)
    if mixed:
        resolved_season = _span_of(span, season, 2, "games").season
        if resolved_season is None:
            raise TemplateUnsupported("a career span has no single season to read both season types within")
        return _team_game_log_mixed(con, team, resolved_season, opponent=opponent, venue=venue, limit=limit)
    scope = _span_of(span, season, season_type, "games")
    narrowed = team_games(con, team, scope, {"venue": venue}, opponent=opponent, date=date)
    if isinstance(narrowed, TemplateResult):
        return narrowed
    return _team_game_log(con, team, scope, narrowed, limit=limit, ascending=ascending)


def _game_log_player(
    con: duckdb.DuckDBPyConnection,
    slots: dict[str, Any],
    team_text: str | None,
    *,
    slot_season: int | None,
    span: Any,
    season: int | None,
    opponent: Any,
    measures: list[MeasureFilter],
    date: str | None,
    limit: int,
    ascending: bool,
    mixed: bool,
    asked: int | None,
) -> TemplateResult:
    """The player half of :func:`game_log`: settle his name (an ordinal season
    and a ``team`` slot beside him included), then read either one season
    type or both - the same split :func:`_game_log_team` makes.

    .. versionadded:: 4.4.0
       Split out of ``game_log`` alongside ``_game_log_team``, for the same
       reason.
    """
    # The span is settled before the name is resolved, because it is what
    # narrows the name. `season` here is the raw slot - None means the current
    # season only once _span_of reads it - and passed through as it was, it
    # narrowed "curry's last 5 games" over every season and asked about all six
    # Currys again. The order those steps run in lives in scoped_player.
    subject = scoped_player(con, slots, "game_log needs a team or a player", table="player_game_log", available=_GAME_LOGS, span=span, season=season)
    if isinstance(subject, TemplateResult):
        return subject
    player, scope = subject
    if team_text:
        # A `team` beside a named `player` used to win outright at the check
        # above and answer the TEAM's log instead of his - see AGENTS.md,
        # "game_log answers a team's log when the question named a player"
        # (#147). Read it against him instead of the league: his own team
        # narrows nothing, a different one is his opponent, and a name
        # nothing resolves to is dropped exactly like an invented player name.
        opponent = _team_slot_for_player(con, player, team_text, season=slot_season, opponent=opponent)
    extras = _log_extras(slots.get("stat"))
    if mixed:
        if scope.season is None:
            raise TemplateUnsupported("a career span has no single season to read both season types within")
        return _player_game_log_mixed(con, player, scope.season, slots, opponent=opponent, measures=measures, extras=extras, limit=limit, asked=asked)
    narrowed = scoped_games(con, player, scope, slots, opponent=opponent, measures=measures, date=date)
    if isinstance(narrowed, TemplateResult):
        return narrowed
    return _player_game_log(con, player, scope, narrowed, extras, limit=limit, asked=asked, ascending=ascending)


def _game_log_lines(below: Any, above: Any, threshold: Any) -> list[MeasureFilter]:
    """The lines a log keeps games under or over - "with less than 15 fga and
    with less than 35 minutes", every one, on the same rows the other
    narrowings filter. A phrase naming no column refuses here, like a slot
    nothing honors."""
    measures = measure_filters(below, above)
    if threshold is not None and not any(line.value == threshold for line in measures):
        # A `threshold` the model set beside no phrase of the question's:
        # "games with 30+ points" would list his last ten games whatever they
        # held, and keeping only the games past that line is a filter this
        # template does not read. One carrying a phrase's own number is that
        # phrase, misread ("less than 15 fga" arrived as threshold 15), and the
        # phrase answers it.
        raise TemplateUnsupported("game_log cannot keep only the games past a threshold")
    return measures


def _team_game_log_refusals(without: Any, measures: list[MeasureFilter], game_n: Any) -> None:
    """What a team's log cannot narrow by: a teammate's absence is
    with_without's question, a line on a box-score stat keeps a PLAYER's
    games - a team's log has no such column - and a game of a series is
    numbered on the player relation only, so far."""
    if without:
        raise TemplateUnsupported("a team's games without one of its players is a with_without question")
    if measures:
        raise TemplateUnsupported("a line on a box-score stat keeps a PLAYER's games; a team's log has no such column")
    if game_n:
        raise TemplateUnsupported("a team's log does not number the games of a series yet")


def _played_for(con: duckdb.DuckDBPyConnection, player: Entity, team: Entity) -> bool:
    """Whether ``player`` has ever suited up for ``team``, anywhere in
    ``player_game_log``.

    The only question this answers is "is this team the SUBJECT, not the
    opponent" - so it deliberately looks across his whole career rather than
    the season in scope: a team slot naming a season he was not on it is still
    not an opponent, and treating it as one would file a real former team as
    though the two had played each other.
    """
    row = con.execute("SELECT 1 FROM player_game_log WHERE athlete_id = ? AND team_id = ? LIMIT 1", [player.id, team.id]).fetchone()
    return row is not None


def _team_slot_for_player(con: duckdb.DuckDBPyConnection, player: Entity, team_text: str, *, season: int | None, opponent: Any) -> Any:
    """What a ``team`` slot means once ``player`` is named - see #147.

    An ``opponent`` already named wins outright: a ``team`` slot beside it is
    the same noise the router routinely fills alongside an already-correct
    opponent, not a second fact to reconcile - measured on the filed corpus
    rows, it is Payton Pritchard's invented "Phoenix Suns" beside a correct
    "Philadelphia 76ers" opponent, and Kobe Bryant's own "Los Angeles Lakers"
    beside a correct "Houston Rockets" one. Comparing the two and refusing
    when they disagreed was tried first and was wrong for exactly this shape:
    "Phoenix Suns" is a real, resolvable team, so a naive conflict check
    refused Pritchard's question rather than answering it.

    With no ``opponent`` already named, his own team narrows nothing, so it is
    dropped; a different, real team is his opponent (the Curry shape); and a
    name nothing resolves to - the router inventing a team the way it
    sometimes invents a player, see AGENTS.md's "the router invents names" -
    or an ambiguous one, is dropped rather than guessed at or asked about: the
    player, not the team, is what the question is about.
    """
    if isinstance(opponent, str) and opponent.strip():
        return opponent
    match resolve_team(con, team_text, season):
        case Entity() as team:
            return opponent if _played_for(con, player, team) else team_text
        case _:
            # NotFound or Ambiguous - dropped either way, since `opponent` is
            # not set here for either to fill.
            return opponent


def _scope(count: int, ascending: bool, date: str | None) -> str:
    if date:
        return f"on {date}"
    if count == 1:
        return "first game" if ascending else "most recent game"
    return f"first {count} games" if ascending else f"last {count} games"


def _game_log_merge_season_types(rows_by_type: dict[int, list[tuple[Any, ...]]], *, limit: int, ascending: bool) -> tuple[list[tuple[Any, ...]], dict[int, int]]:
    """A "last N games" answer with no season type named reads both types
    separately (each a normal, single-type query) and merges here - see
    ``router._route_game_log_recent_span``. Every row's first column is its
    date, which is how a player's and a team's rows both sort; kept newest (or
    oldest, for ``ascending``) first, down to ``limit`` overall.

    Returns the merged rows and how many of the kept ones came from each
    season type - the count a "reasonable default" has to show, per
    AGENTS.md: a user who gets 3 playoff games and 2 regular-season ones has
    to be told that split, not just handed 5 rows headed "last 5 games".

    .. versionadded:: 4.4.0
    """
    tagged = [(row, season_type) for season_type, rows in rows_by_type.items() for row in rows]
    tagged.sort(key=lambda item: item[0][0], reverse=not ascending)
    kept = tagged[:limit]
    counts: dict[int, int] = {}
    for _, season_type in kept:
        counts[season_type] = counts.get(season_type, 0) + 1
    return [row for row, _ in kept], counts


def _game_log_mixed_where(season: int, counts: dict[int, int]) -> str:
    """The clause a mixed-type "last N games" header adds after the count of
    games - "of the 2026 postseason" where every kept game is one type (the
    common case: most teams and players never reach the postseason at all, so
    this reads exactly as it always did), or "(2 regular season and 3
    postseason)" where they are not, naming the default game_log actually
    used the way AGENTS.md's "a reasonable default beats a question" requires.

    .. versionadded:: 4.4.0
    """
    if len(counts) == 1:
        (season_type,) = counts
        return f" of the {_period(season, season_type)}"
    parts = [f"{count} {SEASON_TYPE_NAMES[season_type]}" for season_type, count in sorted(counts.items())]
    return f" ({_joined(parts)})"


def _team_game_log_none(con: duckdb.DuckDBPyConnection, team: Entity, span: _Span, narrowed: TeamNarrowed) -> TemplateResult:
    """Which fact is missing when no row matched: the team's games in that
    span, or the match - so the sentence names the right one. Reads
    ``narrowed`` WITHOUT its narrowing (:meth:`TeamNarrowed.clauses`,
    ``narrowed=False``), the same discipline
    ``common._no_narrowed_games`` uses for a player: the total is the span
    alone, so the sentence can say "none of them" the narrowed way rather
    than restating a count that already excludes them."""
    where, params = narrowed.clauses(narrowed=False)
    season_col = "year(tg.eastern_date)" if span.season_type == 3 else "tg.season"
    found = con.execute(f"{TEAM_GAMES_SQL} SELECT COUNT(*), MIN({season_col}), MAX({season_col}) FROM team_games tg WHERE {where}", params).fetchone()
    total, first, last = found if found else (0, None, None)
    if not total:
        where_period = _period(span.season, span.season_type) if span.season is not None else f"{span.kind}s on record"
        return TemplateResult(data={"team": team.name, "games": []}, answer=f"No {where_period} games found for the {team.name}.")
    on_date = f" on {narrowed.date}" if narrowed.date else ""
    answer = f"The {team.name} played {total:,} games {span.during(first, last, whose='all seasons on record')}, none of them{narrowed.filters()}{on_date}."
    return TemplateResult(data={"team": team.name, "games": []}, answer=answer)


def _team_game_log_games(rows: list[tuple[Any, ...]]) -> tuple[list[dict[str, Any]], int, int, str, list[str]]:
    """Each fetched row (:data:`_TEAM_GAME_LOG_SELECT`'s seven columns) turned
    into a display game, the wins/losses record tallied over exactly the rows
    being shown rather than recounted from it later - that recount is where a
    wins/losses total gets inverted - and the listing's own lines. Shared by
    the single-season-type log and the "last N games" mixed one
    (:func:`_team_game_log_mixed`), which differ only in how they build the
    header.

    .. versionchanged:: 4.4.0
       Split out of ``_team_game_log_rows`` so a mixed-type answer can reuse
       it. Reads ``team_games.won`` (already a nullable boolean, computed once
       in the relation) rather than comparing a raw ``winner_team_id`` to
       ``team_id`` itself.
    """
    games = [{"date": str(r[0]), "home_away": r[1], "opponent": r[2], "team_score": r[3], "opponent_score": r[4], "won": r[5], "season": r[6]} for r in rows]
    wins = sum(1 for g in games if g["won"] is True)
    losses = sum(1 for g in games if g["won"] is False)
    unknown = len(games) - wins - losses
    record = f"{wins}-{losses}" + (f", {unknown} with no recorded result" if unknown else "")
    mark = {True: "W", False: "L", None: "?"}
    lines = [f"  {g['date']}  {mark[g['won']]} {g['team_score']}-{g['opponent_score']}  {'vs' if g['home_away'] == 'home' else 'at'} {g['opponent']}" for g in games]
    return games, wins, losses, record, lines


def _team_game_log_rows(team: Entity, span: _Span, rows: list[tuple[Any, ...]], *, narrowed: str, date: str | None, ascending: bool) -> TemplateResult:
    """The games listing, headed by the single season type ``span`` names."""
    games, wins, losses, record, lines = _team_game_log_games(rows)
    seasons = [g["season"] for g in games]
    if span.season is not None:
        where = f" of the {_period(span.season, span.season_type)}"
    else:
        years = span.years(min(seasons), max(seasons))
        where = f" ({years})" if date else f" (all-time, {years})"
    header = f"{team.name}{narrowed}, {_scope(len(games), ascending, date)}{where} ({record}):"
    return TemplateResult(data={"team": team.name, "wins": wins, "losses": losses, "games": games}, answer="\n".join([header, *lines]))


def _team_game_log(con: duckdb.DuckDBPyConnection, team: Entity, span: _Span, narrowed: TeamNarrowed, *, limit: int, ascending: bool) -> TemplateResult:
    """A team's games in ``span``, narrowed as ``narrowed``
    (:func:`association.query.templates.common.team_games`) already reflects."""
    sql, params = team_rows_sql(narrowed, _TEAM_GAME_LOG_SELECT, order=f"tg.eastern_date {'ASC' if ascending else 'DESC'}", limit=limit, join=_TEAM_GAME_LOG_JOIN)
    rows = con.execute(sql, params).fetchall()
    if not rows:
        # Which fact is missing: the team's games in that span, or the match.
        return _team_game_log_none(con, team, span, narrowed)
    return _team_game_log_rows(team, span, rows, narrowed=narrowed.filters(), date=narrowed.date, ascending=ascending)


def _team_game_log_mixed(con: duckdb.DuckDBPyConnection, team: Entity, season: int, *, opponent: Any, venue: Any, limit: int) -> TemplateResult:
    """A team's "last N games" with no season type named: both types, read
    separately and merged by date - see ``router._route_game_log_recent_span``
    and :func:`_game_log_merge_season_types`. ``without``, a date and a game of
    a series are the team log's other narrowings, and none of them reach
    here: ``without`` already refuses outright for a team
    (:func:`_team_game_log_refusals`), a date fixes one game regardless of its
    type (excluded from the router's signal), and a series game number is the
    player relation's alone - so only an opponent and a venue are left to
    compose.

    .. versionadded:: 4.4.0
    """
    rows_by_type: dict[int, list[tuple[Any, ...]]] = {}
    narrowed_text = ""
    for season_type in (2, 3):
        type_span = _Span(season, season_type)
        narrowed = team_games(con, team, type_span, {"venue": venue}, opponent=opponent)
        if isinstance(narrowed, TemplateResult):
            return narrowed
        narrowed_text = narrowed.filters()
        sql, params = team_rows_sql(narrowed, _TEAM_GAME_LOG_SELECT, order="tg.eastern_date DESC", limit=limit, join=_TEAM_GAME_LOG_JOIN)
        rows_by_type[season_type] = con.execute(sql, params).fetchall()
    rows, counts = _game_log_merge_season_types(rows_by_type, limit=limit, ascending=False)
    if not rows:
        # Both types came back empty, so the missing fact really is "no games
        # in this span" - the same sentence a single-type refusal gives, with
        # no season type to (wrongly) blame it on.
        return TemplateResult(data={"team": team.name, "games": []}, answer=f"No {season} games found for the {team.name}{narrowed_text}.")
    games, wins, losses, record, lines = _team_game_log_games(rows)
    header = f"{team.name}{narrowed_text}, {_scope(len(games), False, None)}{_game_log_mixed_where(season, counts)} ({record}):"
    return TemplateResult(data={"team": team.name, "wins": wins, "losses": losses, "games": games}, answer="\n".join([header, *lines]))


def _pct(made: Any, attempted: Any) -> float | None:
    return 100.0 * made / attempted if made is not None and attempted else None


def _log_key(header: str) -> str:
    return _LOG_PERCENTAGES[header][2] if header in _LOG_PERCENTAGES else _LOG_COLUMNS[header]


def _log_cell(header: str, value: Any, *, average: bool = False) -> str:
    if value is None:
        return "-"
    if header == "+/-":
        return f"{value:+.1f}" if average else f"{int(value):+d}"
    if header in _LOG_PERCENTAGES or average:
        return f"{value:.1f}"
    return str(int(value))


def _aligned(titles: list[str], rows: list[list[str]], left: int) -> list[str]:
    """Rows under titles, the first ``left`` columns left-aligned and the rest
    right-aligned, indented like every other listing here."""
    widths = [max(len(title), *(len(row[i]) for row in rows)) for i, title in enumerate(titles)]

    def _line(cells: list[str]) -> str:
        return ("  " + "  ".join(cell.ljust(width) if i < left else cell.rjust(width) for i, (cell, width) in enumerate(zip(cells, widths, strict=True)))).rstrip()

    return [_line(titles), *(_line(row) for row in rows)]


def _player_game_log_columns(con: duckdb.DuckDBPyConnection, extras: tuple[str, ...]) -> tuple[list[str], list[str], bool]:
    """The columns to show, the raw columns fetched behind them, and whether a
    rebuilt (play-by-play) line can stand in for a missing box score line."""
    headers = list(dict.fromkeys([*_LOG_BASE, *extras]))
    # A percentage is never fetched: it is computed from the made/attempted pair
    # behind it, which is fetched whether or not it is shown.
    needed = list(dict.fromkeys([*(h for h in headers if h in _LOG_COLUMNS), *(c for h in headers if h in _LOG_PERCENTAGES for c in _LOG_PERCENTAGES[h][:2])]))
    # Rebuilt lines are read only when EVERY column shown is one a rebuild gets
    # right (REBUILT_STATS). `minutes` is the exception rather than a failure:
    # play-by-play cannot recover it, so it prints blank, which is the truth.
    # Ask for a player's fouls and the log goes back to fetched lines only,
    # because a rebuilt foul is wrong in one game in six.
    rebuilt = _rebuilt_readable(con, needed)
    return headers, needed, rebuilt


def _player_game_log_rows(rows: list[tuple[Any, ...]], needed: list[str], headers: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Each fetched row turned into a display game (with its percentages
    derived) and the raw made/attempted values behind it, kept for the
    averages below."""
    games: list[dict[str, Any]] = []
    raws: list[dict[str, Any]] = []
    for game_date, season, opponent, home, winner, team_id, is_rebuilt, *values in rows:
        raw = dict(zip(needed, values, strict=True))
        game: dict[str, Any] = {
            "date": _eastern_date(game_date),
            "season": season,
            "opponent": opponent,
            "home_away": "home" if home else "away",
            "result": None if winner is None else ("W" if winner == team_id else "L"),
            "reconstructed": bool(is_rebuilt),
        }
        for h in headers:
            game[_log_key(h)] = _pct(raw[_LOG_PERCENTAGES[h][0]], raw[_LOG_PERCENTAGES[h][1]]) if h in _LOG_PERCENTAGES else raw[h]
        games.append(game)
        raws.append(raw)
    return games, raws


def _player_game_log_averages(headers: list[str], raws: list[dict[str, Any]]) -> dict[str, float | None]:
    """Per-game averages over exactly the rows being shown, made/attempted
    summed rather than a mean of the per-game percentages."""
    averages: dict[str, float | None] = {}
    for h in headers:
        if h in _LOG_PERCENTAGES:
            made_h, attempted_h, key = _LOG_PERCENTAGES[h]
            averages[key] = _pct(sum(r[made_h] or 0 for r in raws), sum(r[attempted_h] or 0 for r in raws))
        else:
            present = [r[h] for r in raws if r[h] is not None]
            averages[_LOG_COLUMNS[h]] = sum(present) / len(present) if present else None
    return averages


def _player_game_log_header(player: Entity, span: _Span, narrowed: _Narrowed, games: list[dict[str, Any]], *, ascending: bool) -> str:
    """The listing's headline: how many games, over what span, filtered how."""
    count = len(games)
    if narrowed.date:
        listed = "game" if count == 1 else "games"
        scope_text = f"{listed} on {narrowed.date}"
    elif count == 1:
        scope_text = "first game" if ascending else "most recent game"
    else:
        scope_text = f"first {count} games" if ascending else f"last {count} games"
    seasons = [g["season"] for g in games]
    if span.season is not None:
        where_text = f" of the {_period(span.season, span.season_type)}"
    elif narrowed.date:
        where_text = f" ({span.years(min(seasons), max(seasons))})"
    else:
        where_text = f" of his career ({span.years(min(seasons), max(seasons))})"
    return f"{player.name}{narrowed.filters(dated=False)}, {scope_text}{where_text}:"


def _player_game_log_table(headers: list[str], games: list[dict[str, Any]], averages: dict[str, float | None]) -> list[str]:
    """The aligned date/opponent/result/stat table, its last row the per-game
    averages."""
    titles = ["date", "opp", "W/L", *headers]
    body = [[g["date"], f"{'vs' if g['home_away'] == 'home' else '@'} {g['opponent']}", g["result"] or "-", *(_log_cell(h, g[_log_key(h)]) for h in headers)] for g in games]
    body.append(["per game", "", "", *(_log_cell(h, averages[_log_key(h)], average=True) for h in headers)])
    return _aligned(titles, body, left=3)


def _player_game_log_notes(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, narrowed: _Narrowed, games: list[dict[str, Any]], *, asked: int | None, rebuilt: bool) -> list[str]:
    """The truncation note (fewer games than asked) followed by the box-score
    coverage notes."""
    count = len(games)
    notes: list[str] = []
    if asked and count < asked and not narrowed.date:
        found_in = "in his box scores" if span.career else f"in the {_period(span.season or current_season(), span.season_type)} - ask about his career to reach earlier seasons"
        notes.append(f"Only {count} game{'s' if count != 1 else ''}{narrowed.filters()} {found_in}.")
    rebuilt_shown = sum(1 for g in games if g["reconstructed"])
    notes += _box_score_notes(con, player, span, narrowed, career_note=not narrowed.date, rebuilt=rebuilt, rebuilt_shown=rebuilt_shown)
    return notes


def _player_game_log_select(needed: list[str], rebuilt: bool) -> str:
    """The columns a player log's row fetch selects, shared by the
    single-season-type log and the mixed "last N games" one
    (:func:`_player_game_log_mixed`), which run this same SELECT once per
    season type."""
    return (
        f"pgl.game_date, pgl.season, pgl.opponent_abbr, g.home_team_id = pgl.team_id, g.winner_team_id, pgl.team_id, "
        f"{'pgl.reconstructed' if rebuilt else 'FALSE'}, {', '.join(f'pgl.{_LOG_COLUMNS[h]}' for h in needed)}"
    )


def _player_game_log(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, narrowed: _Narrowed, extras: tuple[str, ...], *, limit: int, asked: int | None, ascending: bool) -> TemplateResult:
    """The listing, and the per-game averages over exactly the rows in it."""
    headers, needed, rebuilt = _player_game_log_columns(con, extras)
    sql, params = rows_sql(
        narrowed,
        _player_game_log_select(needed, rebuilt),
        order=f"pgl.game_date {'ASC' if ascending else 'DESC'}",
        limit=limit,
        rebuilt=rebuilt,
    )
    rows = con.execute(sql, params).fetchall()
    scope: dict[str, Any] = {
        "player": player.name,
        "season": span.season,
        "span": "career" if span.career and not narrowed.date else None,
        "opponent": narrowed.opponent.name if narrowed.opponent else None,
        "venue": narrowed.venue,
        "without": [mate.name for mate in narrowed.without],
    }
    if not rows:
        message = _no_narrowed_games(con, player, span, narrowed, rebuilt=rebuilt)
        return TemplateResult(data={**scope, "games": [], "message": message}, answer=message)

    games, raws = _player_game_log_rows(rows, needed, headers)
    averages = _player_game_log_averages(headers, raws)
    header = _player_game_log_header(player, span, narrowed, games, ascending=ascending)
    table = _player_game_log_table(headers, games, averages)
    notes = _player_game_log_notes(con, player, span, narrowed, games, asked=asked, rebuilt=rebuilt)
    return TemplateResult(
        data={**scope, "columns": headers, "games": games, "averages": averages},
        answer="\n".join([header, *table, *notes]),
    )


def _player_game_log_mixed_notes(
    con: duckdb.DuckDBPyConnection, player: Entity, per_type: dict[int, tuple[_Span, _Narrowed]], games: list[dict[str, Any]], *, asked: int | None, rebuilt: bool
) -> list[str]:
    """The truncation and box-score notes for a mixed-type log - the same ones
    :func:`_player_game_log_notes` gives a single-type one, read once per
    season type (each type's own empty-box-score count is its own query) and
    de-duplicated, since a rebuilt-line note or a ``without`` note reads
    identically whichever type it came from and would otherwise print twice.

    .. versionadded:: 4.4.0
    """
    count = len(games)
    notes: list[str] = []
    if asked and count < asked:
        _, narrowed = per_type[2]
        notes.append(f"Only {count} game{'s' if count != 1 else ''}{narrowed.filters()} found across the regular season and postseason.")
    rebuilt_shown = sum(1 for g in games if g["reconstructed"])
    seen: set[str] = set()
    for season_type in sorted(per_type):
        span, narrowed = per_type[season_type]
        for note in _box_score_notes(con, player, span, narrowed, career_note=False, rebuilt=rebuilt, rebuilt_shown=rebuilt_shown):
            if note not in seen:
                seen.add(note)
                notes.append(note)
    return notes


def _player_game_log_mixed(
    con: duckdb.DuckDBPyConnection,
    player: Entity,
    season: int,
    slots: dict[str, Any],
    *,
    opponent: Any,
    measures: list[MeasureFilter],
    extras: tuple[str, ...],
    limit: int,
    asked: int | None,
) -> TemplateResult:
    """A player's "last N games" with no season type named: both types, read
    separately and merged by date - the player-log counterpart of
    :func:`_team_game_log_mixed`, for the shape ISSUES.md's "'Last N games'
    means the last N regular-season games..." names ("what did Nikola Jokic do
    in his last 5 games?", answered through the 2026 Finals rather than
    stopping at his last regular-season game). Every other narrowing
    (``opponent``, ``venue``, ``without``, ``split``, a line on a box-score
    stat) resolves the same under either type, since none of them depend on
    which type a game falls in - so each is composed exactly as
    :func:`_narrow_player_games` already does for a single type, once per
    type, rather than taught a new "either type" mode of its own.

    .. versionadded:: 4.4.0
    """
    per_type: dict[int, tuple[_Span, _Narrowed]] = {}
    for season_type in (2, 3):
        type_scope = _Span(season, season_type)
        narrowed = scoped_games(con, player, type_scope, slots, opponent=opponent, measures=measures)
        if isinstance(narrowed, TemplateResult):
            return narrowed
        per_type[season_type] = (type_scope, narrowed)
    headers, needed, rebuilt = _player_game_log_columns(con, extras)
    select = _player_game_log_select(needed, rebuilt)
    rows_by_type: dict[int, list[tuple[Any, ...]]] = {}
    for season_type, (_, narrowed) in per_type.items():
        sql, params = rows_sql(narrowed, select, order="pgl.game_date DESC", limit=limit, rebuilt=rebuilt)
        rows_by_type[season_type] = con.execute(sql, params).fetchall()
    rows, counts = _game_log_merge_season_types(rows_by_type, limit=limit, ascending=False)
    _, narrowed_2 = per_type[2]
    narrowed_text = narrowed_2.filters(dated=False)
    scope: dict[str, Any] = {
        "player": player.name,
        "season": season,
        "span": None,
        "opponent": narrowed_2.opponent.name if narrowed_2.opponent else None,
        "venue": narrowed_2.venue,
        "without": [mate.name for mate in narrowed_2.without],
    }
    if not rows:
        # Both types came back empty, so the missing fact really is "no games
        # this season" - the same sentence a single-type refusal gives, with
        # no season type to (wrongly) blame it on.
        message = f"{player.name} has no games recorded in the {season} season{narrowed_text}."
        return TemplateResult(data={**scope, "games": [], "message": message}, answer=message)
    games, raws = _player_game_log_rows(rows, needed, headers)
    averages = _player_game_log_averages(headers, raws)
    header = f"{player.name}{narrowed_text}, {_scope(len(games), False, None)}{_game_log_mixed_where(season, counts)}:"
    table = _player_game_log_table(headers, games, averages)
    notes = _player_game_log_mixed_notes(con, player, per_type, games, asked=asked, rebuilt=rebuilt)
    return TemplateResult(
        data={**scope, "columns": headers, "games": games, "averages": averages},
        answer="\n".join([header, *table, *notes]),
    )


def _season_games(season: int, season_type: int, alias: str) -> tuple[str, list[Any]]:
    """SQL selecting one season's games from ``games`` (aliased ``alias``), and
    its parameters.

    A postseason is selected by the CALENDAR YEAR it was played in, never by
    its label. ESPN labels every season before 1993-94 by the year it STARTED:
    the postseason games labeled 1990 end on 1991-06-12, the 1991 Finals, so a
    label match answered "the 1991 playoffs" with 1992's. Every postseason is
    played inside the year its season is named for (the 2020 bubble ended in
    October 2020), so the year is exact for all of them - the same choice
    ``team_metrics.games_scope`` makes. The phantom 1993 label is excluded, since
    its games are 1994's and would be counted twice.

    A regular season keeps its label: every regular season a template can reach
    (1994 on) is labeled by the year it ends.
    """
    if season_type == 3:
        phantom = COVERAGE["games"].phantom
        excluded = f" AND {alias}.season NOT IN ({', '.join('?' for _ in phantom)})" if phantom else ""
        return f"CAST(substr({alias}.date, 1, 4) AS INTEGER) = ?{excluded}", [season, *phantom]
    return f"{alias}.season = ?", [season]


def _head_to_head_names(teams_slot: Any, team_slot: Any, opponent_slot: Any) -> list[str]:
    """The team names asked for, merged from `teams`, `team` and `opponent`.

    A city name rather than a nickname ("...play Boston?") makes the router
    split the two teams across `team` and `teams` instead of putting both in
    `teams`. Name resolution is fine either way, so treating `team` as a third
    candidate absorbs the split rather than rejecting an answerable question.
    The router also writes the other side as `opponent` ("Celtics vs Bulls
    head to head" arrives as team + opponent): it is one of the two teams.
    """
    names = [n for n in teams_slot if isinstance(n, str) and n.strip()] if isinstance(teams_slot, list) else []
    if isinstance(team_slot, str) and team_slot.strip() and team_slot not in names:
        names = [team_slot, *names]
    if isinstance(opponent_slot, str) and opponent_slot.strip() and opponent_slot not in names:
        names = [*names, opponent_slot]
    if len(set(names)) < 2:
        raise TemplateUnsupported("head_to_head needs two team names")
    return names


def _head_to_head_teams(con: duckdb.DuckDBPyConnection, names: list[str], season: int | None) -> tuple[Entity, Entity] | TemplateResult:
    """Until two DIFFERENT teams resolve, not the first two names: "Celtics" in
    `team` and "Boston Celtics" in `teams` are one team, and the opponent
    after them is the second."""
    resolved: list[Entity] = []
    for name in names:
        team = _resolved_team(con, name, season=season)
        if isinstance(team, TemplateResult):
            return team
        if team.id not in {t.id for t in resolved}:
            resolved.append(team)
        if len(resolved) == 2:
            break
    if len(resolved) != 2:
        raise TemplateUnsupported("the named teams resolved to the same team")
    return resolved[0], resolved[1]


def _head_to_head_narrowed(
    con: duckdb.DuckDBPyConnection, a: Entity, b: Entity, venue: str | None, date: str | None, season_slot: Any, season_type: int
) -> tuple[TeamNarrowed | TemplateResult, int | None]:
    """``a``'s games against ``b``, from ``a``'s own row of the team relation
    (:func:`association.query.templates.common.team_games`) - which already
    carries both home and away meetings without a venue named, since ``games``
    is home/away-oriented rather than team-perspective and ``a``'s own row
    always exists whichever side it played - and the season the narrowing
    settled on (``None`` once ``date`` has replaced it).

    A date names its game outright, the same way it does in :func:`game_log`:
    the router's season is usually its "current" default, and a date from a
    past season looked for inside this one finds nothing - so with a date
    given, the span is UNBOUNDED (``_Span(None, season_type)``, whose
    ``first`` defaults to 0) rather than "his career": a specific date needs
    no floor at all, the same as the hand-written scope this replaces applied
    none. A venue is always read from ``a``'s side - the team named first,
    the same team ``_head_to_head_names`` puts first for "Celtics vs Bulls" -
    so "Lakers vs Mavs ... home games" narrows to the Lakers' home games, not
    the Mavericks'.
    """
    if date:
        span = _Span(None, season_type)
        return team_games(con, a, span, {"venue": venue}, opponent=b, date=date), None
    # No season named means the CURRENT one, as everywhere else. "All time" is
    # a defensible reading here, but silently answering a different span than
    # the rest of the system is the substitution this design exists to prevent.
    # The answer names the season, so another one is a follow-up away.
    season = season_slot or current_season()
    span = _Span(season, season_type)
    return team_games(con, a, span, {"venue": venue}, opponent=b), season


def head_to_head(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """ "How many times did the 76ers play Boston?" - games between two teams.

    That question was answered "they did not play" (they played four times).
    The agent wrote `home_team_id = 'PHI'` against an opaque numeric VARCHAR
    ('20'), so the filter matched nothing - with the rule against it, and a
    worked WRONG example, in its prompt. Prompting cannot fix that; resolving
    names to ids in code can.

    .. versionchanged:: 4.3.0
       Honors ``venue`` (narrowed to the first-named team's home or road
       games) and ``date`` (one calendar day, replacing the season the same
       way it does in :func:`game_log`) instead of refusing them.

    .. versionchanged:: 4.4.0
       Reads :mod:`association.query.team_games`'s relation
       (:func:`association.query.templates.common.team_games`) instead of its
       own hand-written scope over ``real_games`` - a pure port, the NBA Cup
       final counted as a meeting either way, since a head-to-head count is
       not the win-loss RECORD that excludes it.
    """
    con = ctx.con
    teams_slot = slots.get("teams")
    team_slot = slots.get("team")
    # The router also writes the other side as `opponent` ("Celtics vs Bulls
    # head to head" arrives as team + opponent): it is one of the two teams.
    opponent_slot = slots.get("opponent")
    names = _head_to_head_names(teams_slot, team_slot, opponent_slot)

    teams = _head_to_head_teams(con, names, _slot_season(slots))
    if isinstance(teams, TemplateResult):
        return teams
    a, b = teams
    season_type = slots.get("season_type") or 2
    raw_date = slots.get("date")
    date = raw_date if isinstance(raw_date, str) and _ISO_DATE.match(raw_date) else None
    venue = _checked_venue(slots["venue"]) if slots.get("venue") else None
    narrowed, season = _head_to_head_narrowed(con, a, b, venue, date, slots.get("season"), season_type)
    if isinstance(narrowed, TemplateResult):
        return narrowed
    sql, params = team_rows_sql(narrowed, "tg.won", order="tg.eastern_date")
    rows = con.execute(sql, params).fetchall()

    a_wins = sum(1 for (won,) in rows if won)
    b_wins = sum(1 for (won,) in rows if won is False)
    data = {"teams": [a.name, b.name], "games": len(rows), "wins": {a.name: a_wins, b.name: b_wins}, "venue": venue, "date": date}
    if venue or date:
        answer = _head_to_head_narrowed_phrase(a.name, b.name, len(rows), a_wins, b_wins, venue=venue, date=date, season=season, season_type=season_type)
    else:
        # `_head_to_head_scope` only returns `season=None` once `date` has
        # replaced it, and the branch above already took that case, so this
        # one always has a season - `cast` says that to the type checker.
        answer = _phrase_head_to_head(a.name, b.name, len(rows), a_wins, b_wins, _period(cast(int, season), season_type))
    return TemplateResult(data=data, answer=answer)


def _phrase_head_to_head(a: str, b: str, games: int, a_wins: int, b_wins: int, period: str) -> str:
    if games == 0:
        return f"The warehouse has no {period} games between the {a} and the {b}."
    times = "once" if games == 1 else f"{games} times"
    lead = f"The {a} and the {b} met {times} in the {period}"
    if a_wins == b_wins:
        return f"{lead}, splitting them {a_wins}-{b_wins}."
    leader, trailing = (a, f"{a_wins}-{b_wins}") if a_wins > b_wins else (b, f"{b_wins}-{a_wins}")
    return f"{lead}; the {leader} won the series {trailing}."


def _head_to_head_narrowed_phrase(a: str, b: str, games: int, a_wins: int, b_wins: int, *, venue: str | None, date: str | None, season: int | None, season_type: int) -> str:
    """The head-to-head sentence once ``venue`` or ``date`` has narrowed the
    games, phrased to say what was actually counted rather than reusing the
    plain season sentence (:func:`_phrase_head_to_head`, left untouched) with
    a different number silently attached to it."""
    if date:
        where = f"on {date}"
    else:
        # "Lakers'", not "Lakers's" - most team names end in "s" (Celtics,
        # Warriors, Nets...), and templates/teams.py's own `_possessive` makes
        # the same call inline rather than being imported cross-module.
        possessive = f"{a}'" if a.endswith("s") else f"{a}'s"
        where = f"in the {possessive} {'home' if venue == 'home' else 'road'} games of the {_period(season or current_season(), season_type)}"
    if games == 0:
        return f"The warehouse has no games between the {a} and the {b} {where}."
    times = "once" if games == 1 else f"{games} times"
    lead = f"The {a} and the {b} met {times} {where}"
    if a_wins == b_wins:
        return f"{lead}, splitting them {a_wins}-{b_wins}."
    leader, trailing = (a, f"{a_wins}-{b_wins}") if a_wins > b_wins else (b, f"{b_wins}-{a_wins}")
    return f"{lead}; the {leader} won the series {trailing}."


# The same team-perspective join game_log uses, so which side of a game was
# "this team" is never re-derived from team_id comparisons.
# home_linescores/away_linescores hold the official per-period score for that
# side (index 0 = Q1 ... 4+ = OT1/OT2/...) - no plays table, no LAG(), no
# --include-pbp, unlike the per-PLAYER version of this question.
_TEAM_QUARTER_SQL = f"""
SELECT g.date,
       CASE WHEN tbs.home_away = 'home' THEN g.home_linescores ELSE g.away_linescores END AS own_linescores,
       {season_name_sql("opp.team_id", "tbs.season", "opp.display_name")} AS opponent
FROM team_box_stats tbs
JOIN real_games g ON g.event_id = tbs.event_id AND g.season = tbs.season
JOIN teams opp ON opp.team_id = tbs.opponent_team_id
"""


# Above this many games, a full per-game breakdown is unreadable rather than
# informative - it only fires when no opponent narrows the season down (a
# real head-to-head, regular season or playoffs, is never more than ~7 games).
_QUARTER_BREAKDOWN_LIMIT = 12


def _linescores(raw: Any) -> list[int]:
    """games.home_linescores/away_linescores: '25,32,25,26' -> [25, 32, 25, 26].
    Not every game reaches overtime, so the list can be shorter than a
    requested OT period asks for - callers treat a missing index as "this game
    didn't go there", not as zero points."""
    if not isinstance(raw, str) or not raw.strip():
        return []
    out = []
    for raw_part in raw.split(","):
        part = raw_part.strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                continue
    return out


def _period_label(period: int) -> str:
    if 1 <= period <= 4:
        return f"{_ordinal(period)} quarter"
    ot = period - 4
    return "overtime" if ot == 1 else f"{_ordinal(ot)} overtime"


def team_quarter_points(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A team's total points in ONE quarter/period, optionally narrowed to one
    named opponent.

    A PLAYER's quarter or half is answered by :func:`period_split`, not here -
    see its docstring for why the derivation this one used to say a player's
    score would need (a plays-table ``LAG()``) turned out to be unnecessary.
    This template stays TEAM-only: home_linescores/away_linescores already
    store the official per-period score, so this is a lookup rather than a
    derivation, and a named player still raises below.

    Worth a template because the agent spent ~150s over 3 calls getting it
    wrong: a games.period column that doesn't exist, then home_team_id compared
    to 'PHI' - the id-vs-abbreviation mistake its own always-on rule warns
    against, on every call. A compound shape (quarter math AND a named
    opponent) is what this model fails at even with both rules in its
    prompt.

    .. versionadded:: 1.1.0
    """
    con = ctx.con
    periods, period_label = _period_scope(slots, "team_quarter_points")
    if isinstance(slots.get("player"), str) and slots["player"].strip():
        # A named player's quarter or half is period_split's job, not this
        # template's - see the docstring.
        raise TemplateUnsupported("team_quarter_points cannot answer for a named player")
    _team_quarter_points_check_stat(slots.get("stat"))

    resolved = _team_quarter_points_teams(con, slots.get("team"), slots.get("opponent"), _slot_season(slots))
    if isinstance(resolved, TemplateResult):
        return resolved
    team, opponent = resolved

    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    games = _team_quarter_points_games(con, team, opponent, season, season_type, periods)

    period_str = _period(season, season_type)
    return _team_quarter_points_answer(team, opponent, games, periods=periods, period_label=period_label, period_str=period_str, rank=slots.get("rank"))


def _team_quarter_points_check_stat(stat: Any) -> None:
    """Refuse a question asking for a per-quarter figure this cannot read.

    The linescores hold one number per period - the score - so points is the
    only stat there is here, and a question naming another one is a different
    question. It went unnoticed while "trailblazers stats last 10 games 3
    point average 1st quarter" was refused for naming no team at all; with the
    team restored, the same question would have been answered with the
    Blazers' first-quarter POINTS, which is the fluent wrong answer this
    project keeps producing rather than the refusal it deserves.

    Falls through rather than refusing outright, because the agent does have
    something to read for this one: ``shot_chart`` carries ``team_id``,
    ``period`` and the shot's value, which is how ``period_split`` counts a
    player's. Answering threes per quarter from it, under the same per-season
    accuracy gating, is issue #161 and not this function.
    """
    if stat is None:
        return
    if resolve_metric(stat) not in ("avg_points", "total_points"):
        raise TemplateUnsupported(f"team_quarter_points reads the linescore, which holds only points, not {stat!r}")


def _team_quarter_points_teams(con: duckdb.DuckDBPyConnection, team_text: Any, opponent_text: Any, season: int | None) -> tuple[Entity, Entity | None] | TemplateResult:
    """The team and, if the question named one, the opponent - checked to be a
    different team from the one asked about."""
    team = _resolved_team(con, team_text, season=season)
    if isinstance(team, TemplateResult):
        return team
    opponent: Entity | None = None
    if isinstance(opponent_text, str) and opponent_text.strip():
        resolved_opponent = _resolved_team(con, opponent_text, season=season)
        if isinstance(resolved_opponent, TemplateResult):
            return resolved_opponent
        opponent = resolved_opponent
        if opponent.id == team.id:
            raise TemplateUnsupported("team_quarter_points opponent must differ from the team")
    return team, opponent


def _team_quarter_points_games(con: duckdb.DuckDBPyConnection, team: Entity, opponent: Entity | None, season: int, season_type: int, periods: tuple[int, ...]) -> list[dict[str, Any]]:
    """Each qualifying game's date, opponent and points in the asked-for
    periods - None where the game never reached any of them, not zero.

    A half is the two quarters it holds, summed from the same official
    linescore one quarter is read from, so "most points in a first half" is
    answered by addition rather than by a second source.
    """
    season_clause, season_params = _season_games(season, season_type, "g")
    where = ["tbs.team_id = ?", season_clause, "tbs.season_type = ?"]
    params: list[Any] = [team.id, *season_params, season_type]
    if opponent is not None:
        where.append("tbs.opponent_team_id = ?")
        params.append(opponent.id)
    rows = con.execute(f"{_TEAM_QUARTER_SQL} WHERE {' AND '.join(where)} ORDER BY g.date", params).fetchall()
    games = []
    for date, own_linescores, opp_name in rows:
        scores = _linescores(own_linescores)
        reached = [scores[n - 1] for n in periods if n - 1 < len(scores)]
        points = sum(reached) if reached else None
        games.append({"date": _eastern_date(date), "opponent": opp_name, "points": points})
    return games


def _team_quarter_points_answer(
    team: Entity, opponent: Entity | None, games: list[dict[str, Any]], *, periods: tuple[int, ...], period_label: str, period_str: str, rank: Any = None
) -> TemplateResult:
    """The no-games refusal, the none-reached-that-period refusal, the single
    game a "most/least" question asks for, or the normal per-game breakdown
    and total."""
    vs = f" against the {opponent.name}" if opponent else ""
    opponent_name = opponent.name if opponent else None
    if not games:
        answer = f"The warehouse has no {period_str} games for the {team.name}{vs}."
        return TemplateResult(data={"team": team.name, "opponent": opponent_name, "games": []}, answer=answer)

    played = [g for g in games if g["points"] is not None]
    if not played:
        plural = "game" if len(games) == 1 else "games"
        answer = f"None of the {team.name}'s {len(games)} {period_str} {plural}{vs} went to the {period_label}."
        return TemplateResult(data={"team": team.name, "opponent": opponent_name, "games": games}, answer=answer)

    total = sum(g["points"] for g in played)
    data = {"team": team.name, "opponent": opponent_name, "period": periods[0] if len(periods) == 1 else None, "period_label": period_label, "games": played, "total": total}
    if rank in ("most", "fewest"):
        # "Detroit Pistons most points in a first half this season" asks for
        # ONE game, not the season's average - a single-game extreme, the
        # team counterpart of single_game_high. Ties are named together
        # rather than resolved by whichever row sorted first.
        best = max(g["points"] for g in played) if rank == "most" else min(g["points"] for g in played)
        tied = [g for g in played if g["points"] == best]
        how = "most" if rank == "most" else "fewest"
        where = " and ".join(f"vs the {g['opponent']} on {g['date']}" for g in tied)
        answer = f"The {team.name} scored {best} in the {period_label} {where}, their {how} in the {period_str}{vs}."
        return TemplateResult(data={**data, "rank": rank, "extreme": best, "extreme_games": tied}, answer=answer)
    return TemplateResult(data=data, answer=_phrase_team_quarter_points(team.name, opponent_name, period_label, period_str, played, total))


def _phrase_team_quarter_points(team: str, opponent: str | None, period_label: str, period_str: str, games: list[dict[str, Any]], total: int) -> str:
    if len(games) == 1:
        g = games[0]
        return f"The {team} scored {g['points']} points in the {period_label} against the {g['opponent']} on {g['date']} ({period_str})."
    if len(games) > _QUARTER_BREAKDOWN_LIMIT:
        avg = total / len(games)
        vs = f" against the {opponent}" if opponent else ""
        return f"The {team} scored {total} total points in the {period_label} across {len(games)} {period_str} games{vs}, averaging {avg:.1f} per game."
    header = f"The {team}, {period_label} scoring" + (f" against the {opponent}" if opponent else "")
    header += f", {period_str} ({len(games)} games, {total} total):"
    lines = [f"  {g['date']}  {g['points']}  vs {g['opponent']}" for g in games]
    return "\n".join([header, *lines])


# Per-period points are summed from `shot_chart`, and how closely that sums to
# ESPN's own linescore is a property of the SEASON, not of the method. Measured
# 2026-09-16 over every regular season, one row per team-quarter, against
# `games.home_linescores`/`away_linescores`: the percentage of team-quarters
# where the sum is EXACTLY the official figure. Only the seasons below 99% are
# listed; the other nineteen run 99.2-100.0%.
#
# The two bad ones have known causes rather than being noise. 2002 cannot be
# answered at all - it is `UNSEPARABLE_SHOT_VALUES`, so `SHOT_VALUE_SQL` is
# NULL for 20,534 of its made shots and a sum over them is meaningless (4.9%).
# 2016 is the season `fetch/repairs/reconstructed_box` also singles out: its scoring
# plays carry types no rule can classify, and it reconciles at 76.5%, which is
# one quarter in four.
PERIOD_RECONCILIATION: dict[int, float] = {2003: 93.5, 2004: 95.7, 2005: 95.9, 2006: 95.8, 2013: 93.7, 2016: 76.5}
"""Per-season agreement between summed shot values and ESPN's linescores, for
the seasons under 99%. Read by :func:`period_split` to caveat or refuse.

.. versionadded:: 2.2.0
"""


PERIOD_REFUSE_BELOW = 90.0
"""Below this agreement a period answer is refused rather than caveated.

Set between 2016's 76.5% and 2003's 93.5% deliberately: a season that is right
19 times in 20 is worth answering with a caveat, and one that is wrong in a
quarter of its quarters is not an answer at all.

.. versionadded:: 2.2.0
"""


_HALF_PERIODS: dict[int, tuple[int, ...]] = {1: (1, 2), 2: (3, 4)}


def _period_scope(slots: dict[str, Any], intent: str = "period_split") -> tuple[tuple[int, ...], str]:
    """The periods a question asks for, and how to name them in an answer."""
    half = slots.get("half")
    if isinstance(half, int) and half in _HALF_PERIODS:
        return _HALF_PERIODS[half], f"{_ordinal(half)} half"
    period = slots.get("period")
    if isinstance(period, int) and 1 <= period <= 10:
        return (period,), _period_label(period)
    raise TemplateUnsupported(f"{intent} needs a period 1-10 or a half 1-2, got period={slots.get('period')!r} half={half!r}")


def period_split(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """A named player's points in ONE quarter or half, per game and averaged.

    The counterpart to :func:`team_quarter_points`, which answers a TEAM's
    quarter from the official linescore. A player has no such source, so this
    sums the value of his made shots in that period out of ``shot_chart``.

    **It does not need the plays table, and it does not need `LAG`.**
    ``team_quarter_points`` said for a long time that a player's quarter score
    "needs the plays-table LAG() derivation", and that claim is plausibly why
    this went unwritten: ``shot_chart`` already carries ``athlete_id``,
    ``period``, ``made`` and the shot's value, so the answer is a filtered sum.

    **The value is read through :data:`SHOT_VALUE_SQL`, never guessed from the
    play's prose**, and the difference is the whole accuracy of this template.
    Scored by looking for "three point" in the description, per-period points
    match ESPN's linescores 76.8% of the time, and the error is systematically
    -1: "makes 24-foot running jump shot" is a three that scores as two. Read
    off the shot's own label and position, it is 99.95%. Over a whole game that
    gap hides inside a 98% figure; a quarter holds about ten field goals, so it
    does not.

    Accuracy is a property of the season, and this says so rather than
    averaging it away - see :data:`PERIOD_RECONCILIATION`. 2002 and 2016 are
    refused outright (4.9% and 76.5%); 2003-2006 and 2013 are answered with
    the measured figure attached.

    Only POINTS. Rebounds, assists and the rest are not in ``shot_chart`` at
    all, and deriving them per period from ``plays`` carries its own per-stat
    fidelity (fouls reconstruct at 83%), so a question asking for them is
    refused with that named as the reason rather than answered from a weaker
    source.

    .. versionadded:: 2.2.0

    .. versionchanged:: 4.4.0
       Honors ``below``/``above`` (step 3, C2) - a line on a box-score column
       ("with under 25 minutes") narrows which of the player's games are
       summed for the period, through :func:`common.measure_filters` and
       :func:`common.scoped_games`, the same as every other template on the
       relation.
    """
    con = ctx.con
    periods, period_label = _period_scope(slots)
    stat = slots.get("stat")
    if isinstance(stat, str) and stat.strip() and stat not in ("points", "all"):
        raise TemplateUnsupported(f"period_split answers points only, not {stat!r} - no other stat is recorded per period")
    # Refused here, before any name is resolved, if a line names no column -
    # the same discipline player_stat's own measures follow.
    measures = measure_filters(slots.get("below"), slots.get("above"))

    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    agreement = PERIOD_RECONCILIATION.get(season)
    refusal = _period_split_refusal(season, agreement)
    if refusal is not None:
        return refusal

    # Resolved against the shot table, not the box-score or season-line
    # availabilities `scoped_player`'s other callers use - a player with shots
    # on record for this period is a different (narrower) question from one
    # with a game log, and `scoped_player` takes `available` as a parameter for
    # exactly this reason. `span` is never "career" here - it is not among the
    # slots `period_split` declares in HONORED_SCOPING, so `check_scope` has
    # already refused one before this runs - which is what makes the span
    # `scoped_player` settles the same "current or named" season this read
    # before it, byte for byte.
    subject = scoped_player(con, slots, "no player named", table="player_game_log", available=SHOT_AVAILABILITY, span=slots.get("span"), season=slots.get("season"))
    if isinstance(subject, TemplateResult):
        return subject
    player, span = subject

    opponent = _optional_team(con, slots.get("opponent"), season=_slot_season(slots))
    if isinstance(opponent, TemplateResult):
        return opponent

    venue, started = _period_split_narrowing(slots.get("venue"), slots.get("split"))
    narrowed_rows = _period_split_rows(con, player, span, periods, venue, opponent, slots.get("split"), slots.get("without"), measures)
    if isinstance(narrowed_rows, TemplateResult):
        return narrowed_rows
    rows, narrowed_mates, narrowed_measures = narrowed_rows

    scope = _period(season, season_type)
    vs = f" against the {opponent.name}" if opponent else ""
    at = _period_split_narrowing_said(venue, started, narrowed_mates, narrowed_measures)
    games = [{"date": _eastern_date(d), "opponent": name, "home_away": side, "points": int(pts or 0)} for d, side, name, pts in rows]
    data: dict[str, Any] = {
        "player": player.name,
        "period": period_label,
        "season": season,
        "opponent": opponent.name if opponent else None,
        "venue": venue,
        "started": started,
        "measures": narrowed_measures,
        "games": games,
        "games_played": len(games),
    }
    if not games:
        message = f"No {scope} games found for {player.name}{vs}{at}."
        return TemplateResult(data={**data, "message": message}, answer=message)

    total = sum(g["points"] for g in games)
    average = total / len(games)
    data |= {"total": total, "average": average}
    header = _period_split_header(player, period_label, scope, vs, at, total, average, games, slots, slots.get("order"))
    return TemplateResult(data=data, answer=header + _period_split_caveat(season, agreement))


def period_leaderboard(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """Players ranked by their points in ONE quarter or half, per game.

    The league-wide counterpart to :func:`period_split`, which answers the
    same question about one named player, and it reads the same source the
    same way: a player has no official per-period box score, so the points are
    the value of his made shots in that period out of ``shot_chart``, through
    :data:`association.query.shotchart.SHOT_VALUE_SQL` rather than the play's
    prose. Everything that template's docstring says about why - and about
    which seasons are accurate enough to answer at all - applies here
    unchanged, so the same refusal and the same caveat are used.

    Before this existed, "who has the highest average 1st quarter points this
    season?" and "knicks 1st quarter scoring leaders playoffs" reached
    ``other`` and fell through to the agent: the router sends a period
    question with no named player there, and there was nothing else to send it
    to.

    **The denominator is games PLAYED, not games he scored in**, the mistake
    :func:`_period_split_rows` records: counting only the games with a made
    shot in the period turns every scoreless quarter into a missing row and
    lifts the average. And a game the shot table does not cover at all is left
    out of both, since it would otherwise contribute a confident zero.

    **A per-game average needs a minimum**, or the leader is whoever played
    once and scored eight - the same qualifier every other per-game ranking
    here applies (:data:`association.query.metrics.PER_GAME_MIN_GAMES`, five
    in the postseason), and the answer says which it used.

    .. versionadded:: 4.4.0
    """
    con = ctx.con
    periods, period_label = _period_scope(slots, "period_leaderboard")
    stat = slots.get("stat")
    if isinstance(stat, str) and stat.strip() and stat not in ("points", "all"):
        raise TemplateUnsupported(f"period_leaderboard ranks points only, not {stat!r} - no other stat is recorded per period")

    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    agreement = PERIOD_RECONCILIATION.get(season)
    refusal = _period_split_refusal(season, agreement)
    if refusal is not None:
        return refusal

    team: Entity | None = None
    if isinstance(slots.get("team"), str) and slots["team"].strip():
        resolved = _resolved_team(con, slots["team"], season=_slot_season(slots))
        if isinstance(resolved, TemplateResult):
            return resolved
        team = resolved

    minimum = PER_GAME_MIN_POSTSEASON_GAMES if season_type == POSTSEASON else PER_GAME_MIN_GAMES
    limit = _clamp_limit(slots.get("limit"))
    rows = _period_leaderboard_rows(con, season, season_type, periods, periods_team=team, minimum=minimum, limit=limit)
    return _period_leaderboard_answer(rows, period_label, _period(season, season_type), team, minimum, agreement, season)


def _period_leaderboard_rows(
    con: duckdb.DuckDBPyConnection, season: int, season_type: int, periods: tuple[int, ...], *, periods_team: Entity | None, minimum: int, limit: int
) -> list[tuple[Any, ...]]:
    """(name, games, total, average) per qualifying player, best first.

    Built the way :func:`_period_split_rows` builds one player's rows, for the
    same reasons: the games are the ones each player appeared in, restricted
    to games the shot table covers, with a scoreless period counted as zero.
    """
    box = box_source(con)
    appeared = "(b.minutes IS NOT NULL OR b.reconstructed)" if box.rebuilt else "b.minutes IS NOT NULL"
    marks = ", ".join("?" for _ in periods)
    team_clause = " AND b.team_id = ?" if periods_team is not None else ""
    params: list[Any] = [season, season_type, *periods, season, season_type, season, season_type]
    if periods_team is not None:
        params.append(periods_team.id)
    params += [minimum, limit]
    return con.execute(
        f"""
        WITH scored AS (
            SELECT athlete_id, event_id, SUM({SHOT_VALUE_SQL}) AS points
            FROM shot_chart
            WHERE made AND season = ? AND season_type = ? AND period IN ({marks})
            GROUP BY 1, 2
        ),
        covered AS (SELECT DISTINCT event_id FROM shot_chart WHERE season = ? AND season_type = ?),
        played AS (
            SELECT b.athlete_id, b.event_id
            FROM {box.table} b
            JOIN covered c ON c.event_id = b.event_id
            WHERE b.season = ? AND b.season_type = ? AND NOT b.did_not_play AND {appeared}{team_clause}
        )
        SELECT p.display_name, COUNT(*) AS games, SUM(COALESCE(s.points, 0)) AS total, AVG(COALESCE(s.points, 0)) AS average
        FROM played pl
        LEFT JOIN scored s ON s.athlete_id = pl.athlete_id AND s.event_id = pl.event_id
        JOIN players p ON p.athlete_id = pl.athlete_id
        GROUP BY 1
        HAVING COUNT(*) >= ?
        ORDER BY average DESC, games DESC, 1
        LIMIT ?
        """,
        params,
    ).fetchall()


def _period_leaderboard_answer(rows: list[tuple[Any, ...]], period_label: str, scope: str, team: Entity | None, minimum: int, agreement: float | None, season: int) -> TemplateResult:
    """The ranking as a sentence and a list, naming the qualifier it applied."""
    # "led the Knicks in first-quarter points" and "led the league in ..." are
    # both idiomatic; "No player in the league played ..." is not, so the
    # refusal names the group its own way.
    led = f"the {team.name}" if team is not None else "the league"
    among = f" for the {team.name}" if team is not None else ""
    if not rows:
        message = f"No player{among} played the {minimum} games needed to rank {period_label} scoring in the {scope}."
        return TemplateResult(data={"period": period_label, "season": season, "team": team.name if team else None, "leaders": [], "message": message}, answer=message)
    leaders = [{"player": name, "games": int(games), "points": int(total), "average": round(float(average), 1)} for name, games, total, average in rows]
    top = leaders[0]
    rest = ", ".join(f"{row['player']} ({row['average']})" for row in leaders[1:])
    answer = f"{top['player']} led {led} in {period_label} points per game in the {scope} (minimum {minimum} games), at {top['average']} over {top['games']} games."
    if rest:
        answer += f" Next: {rest}."
    return TemplateResult(
        data={"period": period_label, "season": season, "team": team.name if team else None, "minimum_games": minimum, "leaders": leaders},
        answer=answer + _period_split_caveat(season, agreement),
    )


def _period_split_refusal(season: int, agreement: float | None) -> TemplateResult | None:
    """A refusal for a season whose per-period points do not reliably match
    ESPN's own quarter scores (see :data:`PERIOD_RECONCILIATION`), or None
    for a season trusted at face value."""
    if season in UNSEPARABLE_SHOT_VALUES or (agreement is not None and agreement < PERIOD_REFUSE_BELOW):
        why = UNSEPARABLE_SHOT_VALUES.get(season) or f"its per-period points agree with ESPN's own quarter scores only {agreement:.0f}% of the time"
        message = f"Per-quarter scoring cannot be answered for {season}: {why}."
        return TemplateResult(data={"season": season, "message": message}, answer=message)
    return None


def _period_split_narrowing_said(venue: str | None, started: bool | None, mates: list[str], measures: list[str] | None = None) -> str:
    """What the answer says it narrowed to, after the player and the period.

    Said in the answer, like every other narrowing here: a total over his
    starts, or over the games a teammate missed, headed as though it covered
    every game is the silent narrowing ``check_scope`` exists to stop.

    .. versionchanged:: 4.4.0
       Names a line on a box-score column (``below``/``above``,
       :data:`common.MeasureFilter`), the same way :meth:`Narrowed.filters`
       says one - "with under 5 turnovers".
    """
    said = f" at {'home' if venue == 'home' else 'away'}" if venue else ""
    said += "" if started is None else (" as a starter" if started else " off the bench")
    said += f" without {_joined(mates)}" if mates else ""
    said += f" with {_joined(measures)}" if measures else ""
    return said


def _period_split_narrowing(venue: Any, split: Any) -> tuple[str | None, bool | None]:
    """The venue and the starter/bench half this question narrows to.

    Split out of :func:`period_split` to keep it inside the complexity gate.
    Takes the slot VALUES rather than the slots dict, so `period_split`'s own
    source still names every scoping slot it honors - which is what
    ``test_every_template_honoring_a_scope_slot_actually_reads_it`` reads back
    out of it. Only a NAMED half of the split filters (:data:`common.STARTER_SIDES`).
    """
    checked = venue if venue in ("home", "away") else None
    return checked, (STARTER_SIDES.get(split) if isinstance(split, str) else None)


def _period_split_rows(
    con: duckdb.DuckDBPyConnection,
    player: Entity,
    span: _Span,
    periods: tuple[int, ...],
    venue: str | None,
    opponent: Entity | None,
    split: Any = None,
    without: Any = None,
    measures: list[MeasureFilter] | None = None,
) -> tuple[list[tuple[Any, ...]], list[str], list[str]] | TemplateResult:
    """A player's per-game point total in the wanted periods, one row a game.

    The games come from :func:`common.scoped_games`, the one narrowing every
    template that reads a player's games shares - the season-keyed join, the
    did-not-play and empty-line guard, the teammate tenure rule - so a
    narrowing added there reaches this template too. That is how ``without``
    arrived: "scottie barnes stats 2nd half log without rj" was refused for a
    slot no period template honored, while the relation had answered exactly
    that narrowing for four other templates since the port.

    ``opponent`` is applied by hand afterward rather than through
    ``scoped_games``'s own ``opponent`` parameter: the caller resolves it
    eagerly (:func:`period_split` needs the name for the answer whether or not
    any games are narrowed to it), and ``scoped_games`` expects unresolved
    text to look up itself, on the same pattern :func:`common._narrow_player_games`
    always has.

    Two rules on top of the relation, both about the denominator:

    - The games are the ones he PLAYED, with zero where he did not score in
      the period - not the games that have a made shot. The first version
      counted only the latter, so every scoreless quarter left the
      denominator: "RJ Barrett ... over 46 games, averaging 5.4" was a player
      with 57 games and a true 4.4. The sum was right, which is exactly why it
      read as correct.
    - A played game counts only where the shot table covers that game at all.
      2003's shots cover 986 of its games, and a game with no located shots
      would otherwise contribute a confident zero.

    SHOT_VALUE_SQL names ``shot_chart``'s columns bare, and ``season`` is a
    column of ``games`` too - so the value is summed in a CTE joined to
    ``played`` by ``event_id`` alone, never by a literal ``season``/
    ``season_type`` pair: those bare names can only mean ``shot_chart``'s own
    columns as long as nothing else in scope shares them, which is also what
    lets this run for a career-wide ``played`` set (many seasons) without
    special-casing it.

    Returns the rows, the teammates whose absence narrowed them, and the
    box-score lines they were kept under or over as the answer says them (for
    the answer to name), or the clarifying question the relation asks when a
    teammate's name matches more than one player.

    .. versionchanged:: 4.4.0
       Reads the relation rather than its own copy of the played-game guard,
       and honors ``without`` through it.

    .. versionchanged:: 4.4.0
       Reads ``player``'s games through :func:`common.scoped_games` rather
       than a direct call to :func:`common._narrow_player_games` (step 3, C1).
       Takes the already-settled ``span`` its caller now holds rather than a
       bare ``season``/``season_type`` pair.

    .. versionchanged:: 4.4.0
       Honors ``measures`` (step 3, C2) - a line on a box-score column narrows
       which of the player's games are summed for the period, the same as
       every other template on the relation.

    .. versionchanged:: 4.4.0
       The shot-value CTEs join to ``played`` by ``event_id`` rather than
       filtering ``shot_chart`` by a literal ``season``/``season_type`` pair
       taken from ``span.season`` (step 3, C5) - that literal pair is
       ``NULL`` for a career ``span`` (``span.season`` is unset), and bound as
       SQL it silently matched nothing rather than raising: a career question
       for a player with games on record answered "no games found", the same
       false-cause shape `AGENTS.md` warns about elsewhere. Joining by the
       games the relation already selected removes the literal pair entirely,
       so a multi-season ``played`` set needs no special-casing - this is
       pure refactor for every question already reachable through a single
       named season, and a bug fix for ``span`` "career", which was already
       declared honored (:data:`common.HONORED_SCOPING`) before this.
    """
    narrowed = scoped_games(con, player, span, {"venue": venue, "without": without, "split": split}, opponent=opponent, measures=measures or [])
    if isinstance(narrowed, TemplateResult):
        return narrowed
    rebuilt = box_source(con).rebuilt
    played_sql, played_params = rows_sql(
        narrowed,
        "pgl.event_id AS event_id, g.date AS date, CASE WHEN g.home_team_id = pgl.team_id THEN 'home' ELSE 'away' END AS side, "
        f"(SELECT {season_name_sql('t.team_id', 'g.season', 't.display_name')} FROM teams t WHERE t.team_id = pgl.opponent_team_id) AS opponent",
        order="g.date",
        rebuilt=rebuilt,
    )
    marks = ", ".join("?" for _ in periods)
    rows = con.execute(
        f"""
        WITH played AS ({played_sql}),
        scored AS (
            SELECT sc.event_id, SUM({SHOT_VALUE_SQL}) AS points
            FROM shot_chart sc
            JOIN played p ON p.event_id = sc.event_id
            WHERE sc.athlete_id = ? AND sc.made AND sc.period IN ({marks})
            GROUP BY 1
        ),
        covered AS (SELECT DISTINCT sc.event_id FROM shot_chart sc JOIN played p ON p.event_id = sc.event_id)
        SELECT p.date, p.side, p.opponent, COALESCE(s.points, 0)
        FROM played p
        JOIN covered c ON c.event_id = p.event_id
        LEFT JOIN scored s ON s.event_id = p.event_id
        ORDER BY p.date
        """,
        [*played_params, player.id, *periods],
    ).fetchall()
    return rows, [mate.name for mate in narrowed.without], list(narrowed.measures)


def _period_split_header(player: Entity, period_label: str, scope: str, vs: str, at: str, total: int, average: float, games: list[dict[str, Any]], slots: dict[str, Any], order: Any = None) -> str:
    """The headline sentence: one game's own wording when there is only one,
    the recent-games log appended when ``per_game`` asked for it, or the
    plain season average otherwise."""
    plural = "game" if len(games) == 1 else "games"
    header = f"{player.name} scored {total} points in the {period_label} over {len(games)} {plural} of the {scope}{vs}{at}, averaging {average:.1f}."
    if len(games) == 1:
        g = games[0]
        against = f"the {g['opponent']}" if g["opponent"] else "their opponent"
        header = f"{player.name} scored {total} points in the {period_label} {'vs' if g['home_away'] == 'home' else 'at'} {against} on {g['date']} ({scope})."
    elif slots.get("per_game"):
        # The router sets this when the question said "log", "by game" or "each
        # game". The total and average stay over EVERY game, so the header
        # answers the season; the rows are the most recent games, capped like
        # game_log's, and the line says so rather than letting a ten-row table
        # read as the whole season.
        # `order` picks the END of the season the rows come from, the way it
        # does for game_log. Without reading it, "his first 5 games" showed
        # his last five - a different five games, with nothing saying so.
        count = _clamp_limit(slots.get("limit"), default=DEFAULT_GAME_LOG_LIMIT)
        earliest = order == "first"
        shown = games[:count] if earliest else games[-count:]
        rows_out = [f"  {g['date']}  {'vs' if g['home_away'] == 'home' else '@ '} {g['opponent'] or '?':<24} {g['points']:>3}" for g in (shown if earliest else reversed(shown))]
        label = "every game" if len(shown) == len(games) else f"the {len(shown)} {'earliest' if earliest else 'most recent'}"
        header += f"\n  {period_label} points, {label}:\n" + "\n".join(rows_out)
    return header


def _period_split_caveat(season: int, agreement: float | None) -> str:
    """The note that a season's per-period points are summed from shot data
    rather than an official box score, for a season whose accuracy against
    ESPN's own quarter scores has been measured; empty otherwise."""
    if agreement is None:
        return ""
    return (
        f"\n  (Summed from shot data rather than an official per-quarter box score. In {season} that sum matches ESPN's own "
        f"quarter scores {agreement:.0f}% of the time, so treat a single game as approximate.)"
    )


_DEFAULT_MEETINGS_LOGGED = 5


def player_matchup(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """The games two named players both played, on opposite teams: the
    head-to-head record, each one's averages in those games, and the most
    recent meetings.

    Not ``player_compare``, which sets two players' season lines side by side
    whether or not they ever met. The router sends only log, record and
    head-to-head wordings here ("Andre Drummond vs Al Horford game log"), so
    this does not second-guess which of the two readings was meant. A game in
    which they were teammates is not a meeting, and when every shared game was
    one, the answer says that rather than that they never played.

    .. versionchanged:: 4.3.0
       One player and a team ``opponent`` - "sam hauser v mil", "julius randle
       stats vs blazers with minnestota" - is answered as the player-vs-team
       question it actually is, the same way :func:`game_log` answers it,
       instead of refused. ``router._route_matchup_against_team`` already
       reroutes this shape to ``game_log``/``player_stat`` where it can, but
       only from the router's raw output; it cannot see a player name
       ``entities.scope_from_question`` restores afterward, in ``agent.py``,
       which is how these three still arrived here with one name and an
       opponent. A genuine two-player matchup still refuses ``opponent``,
       since it has no third team to narrow by.

       The same shape arrives with a third name too - "de'aaron fox vs magic
       last five games without wembyanama" carries Fox's actual Spurs
       teammate Wembanyama both as a fabricated second "player" and, typo and
       all, as ``without``; "oubre vs warriors without embiid" keeps a
       garbled "Warriners" in ``players`` beside the ``opponent`` that was
       already resolved correctly from it. Both are the one-player-and-a-team
       question above with a teammate's absence named on top, not a genuine
       three-way question, and :func:`_player_matchup_drop_fabricated_second`
       recognizes each shape - eliminating, never guessing which of two real
       players was meant - and folds it back into the branch above, which
       already reads ``without`` because :func:`game_log` does. A ``without``
       left over on a genuine two-player matchup is refused rather than
       silently dropped, the same reasoning ``opponent`` already gets.

    .. versionadded:: 2.1.0
    """
    con = ctx.con
    players = slots.get("players")
    listed: list[Any] = players if isinstance(players, list) else []
    texts = list(dict.fromkeys(n.strip() for n in [*listed, slots.get("player")] if isinstance(n, str) and n.strip()))
    opponent, without = slots.get("opponent"), slots.get("without")
    if len(texts) == 2 and opponent:
        texts = _player_matchup_drop_fabricated_second(con, texts, without, slots.get("season"), slots.get("span"), slots.get("season_type"))
    if len(texts) == 1 and opponent:
        # One name and a team opponent - not a fabricated second player, an
        # actual player-vs-team question that named itself that way. Answered
        # by the same code game_log uses for "player vs opponent", not
        # reimplemented: it is the same question, however it got routed here.
        # `without` rides along unchanged - game_log already reads it.
        return game_log(ctx, {**slots, "player": texts[0]})
    if len(texts) != 2:
        raise TemplateUnsupported(f"player_matchup needs exactly two players, got {texts!r}")
    if opponent:
        # check_scope lets `opponent` through for the fallback above, so a
        # real two-player matchup that still has one left over has to refuse
        # it itself - it names no third team to narrow the meetings by.
        raise TemplateUnsupported("player_matchup cannot narrow a two-player matchup to one opponent")
    if without:
        # check_scope lets `without` through for the same fallback, so a
        # genuine two-player matchup with one left over has to refuse it
        # itself too - a meeting's OWN teammates are not what either player's
        # box-score row narrows, and nothing here answers which side a name
        # belongs to.
        raise TemplateUnsupported("player_matchup cannot narrow a two-player matchup by a teammate's absence")
    scope = _condition_scope(slots.get("season"), slots.get("span"), slots.get("season_type"), _PLAYER_GAME_TABLES, since=slots.get("since"))
    resolved = _player_matchup_resolve(con, texts, scope)
    if isinstance(resolved, TemplateResult):
        return resolved
    a, b = resolved

    meetings, together = _meetings(con, scope, a.id, b.id)
    unseen = _unseen_meetings(con, scope, a.id, b.id)
    caveat = (
        f" {unseen} game{'' if unseen == 1 else 's'} between their teams while both were playing for them {'has' if unseen == 1 else 'have'} no box score, so a meeting there is not counted."
        if unseen
        else ""
    )
    if not meetings:
        return _player_matchup_no_meetings(con, scope, a, b, together, caveat)

    wins, lines, count, summary = _player_matchup_summary(a, b, meetings)
    shown, log = _player_matchup_log(con, meetings, slots.get("limit"), a, b)
    return _player_matchup_answer(a, b, scope, meetings, wins, lines, count, summary, shown, log, caveat)


def _player_matchup_drop_fabricated_second(con: duckdb.DuckDBPyConnection, texts: list[str], without: Any, season: Any, span: Any, season_type: Any) -> list[str]:
    """``texts`` down to one name when the "second player" was never a second
    player - a one-player-vs-a-team question the router dressed as a
    two-player matchup, the shape ISSUES #34 describes. Two ways that
    happens, both left exactly as ``texts`` when neither applies, which keeps
    a genuine two-player matchup refusing ``opponent``/``without`` exactly as
    it always has:

    - **A name matching no player at all.** "oubre vs warriors without
      embiid" keeps a garbled "Warriners" in ``players`` beside the
      ``opponent`` that ``entities.scope_from_question`` already resolved
      correctly from it - not a second player, noise already captured
      elsewhere. Eliminated outright: a string nothing in the warehouse
      answers to was never naming anybody.
    - **A name duplicating ``without``.** "de'aaron fox vs magic ... without
      wembyanama" carries Fox's own Spurs teammate Victor Wembanyama both as
      the fabricated second "player" (typo-corrected already, the way
      ``players`` always is) and, typo and all, as ``without``. Dropped only
      when :func:`_player_matchup_confirms_teammate` CONFIRMS the two name the
      same person - never on the two merely sharing a team, which would
      silently drop a genuine second player a real comparison had named.

    Tried in both name orders, since nothing here says which of the two texts
    is the real subject and which is the noise.
    """
    without_names = teammate_names(without)
    teammate_span = _span_of(span, season, season_type or 2, "player_game_log")
    for primary, other in ((texts[0], texts[1]), (texts[1], texts[0])):
        other_matches = find_players(con, other, limit=2)
        if not other_matches:
            return [primary]
        if len(other_matches) != 1 or not without_names:
            continue
        primary_matches = find_players(con, primary, limit=2)
        if len(primary_matches) == 1 and _player_matchup_confirms_teammate(con, without_names, primary_matches[0], other_matches[0], teammate_span):
            return [primary]
    return texts


def _player_matchup_confirms_teammate(con: duckdb.DuckDBPyConnection, without_names: list[str], primary: Entity, other: Entity, span: _Span) -> bool:
    """Whether ``without`` names the very player already sitting in
    ``other`` - resolved the same way ``game_log``'s own ``without`` resolves
    a name, typos included, so a fabricated second player is recognized by
    the machinery that already trusts it rather than a fresh fuzzy match
    invented here. A name that is not confirmably the same player is left
    alone, which is what keeps this an elimination and not a guess."""
    for name in without_names:
        mate = _resolved_teammate(con, name, primary, span)
        if isinstance(mate, Entity) and mate.id == other.id:
            return True
        if isinstance(mate, TemplateResult) and other.name in mate.data.get("suggestions", []):
            return True
    return False


def _player_matchup_resolve(con: duckdb.DuckDBPyConnection, texts: list[str], scope: _Scope) -> tuple[Entity, Entity] | TemplateResult:
    """The two named players, resolved against the box scores - refusing a
    question whose two names resolve to the same person."""
    resolved: list[Entity] = []
    for text in texts:
        found = _resolved_player(con, text, available=_BOX_SCORES, season=scope.season, through=_career_end(scope.season))
        if isinstance(found, TemplateResult):
            return found
        resolved.append(found)
    a, b = resolved
    if a.id == b.id:
        raise TemplateUnsupported("the named players resolved to the same person")
    return a, b


def _player_matchup_no_meetings(con: duckdb.DuckDBPyConnection, scope: _Scope, a: Entity, b: Entity, together: int, caveat: str) -> TemplateResult:
    """The refusal for two players who never played against each other in
    scope - naming whichever of them has no games at all, since that is the
    missing fact rather than the matchup itself."""
    for player in (a, b):
        if _totals(con, _player_games(scope, box=box_source(con)), {**scope.params(), "player": player.id})[0] == 0:
            return _no_games(con, player, scope, None)
    teammates = f" - they were teammates in all {together} games they both played" if together else ""
    message = f"{a.name} and {b.name} never played against each other {_where_in(scope)}{teammates}.{caveat}"
    return TemplateResult(data={"players": [a.name, b.name], "meetings": 0, "teammate_games": together}, answer=message)


def _player_matchup_summary(a: Entity, b: Entity, meetings: list[dict[str, Any]]) -> tuple[int, dict[str, dict[str, Any]], int, list[tuple[str, list[str]]]]:
    """The head-to-head record and each player's averages in the meetings, as
    the summary table's rows."""
    wins = sum(1 for m in meetings if m["won"])
    lines = {a.name: _matchup_line([m["a"] for m in meetings]), b.name: _matchup_line([m["b"] for m in meetings])}
    count = len(meetings)
    summary = [("wins", [str(wins), str(count - wins)])] + [
        (header, [_cell(lines[p.name][key]) for p in (a, b)]) for key, header in (("minutes", "minutes"), ("points", "points"), ("rebounds", "rebounds"), ("assists", "assists"), ("fg_pct", "FG%"))
    ]
    return wins, lines, count, summary


def _player_matchup_stat_line(stats: dict[str, Any]) -> str:
    """One player's points/rebounds/assists in one meeting, for the log."""
    return f"{stats['points']}/{stats['rebounds']}/{stats['assists']}"


def _player_matchup_abbr(team_id: str, season: int, abbr: dict[str, str]) -> str:
    """A meeting's team as it was abbreviated THAT season - a 2005 Nets game reads NJ, not BKN."""
    return season_name(team_id, season, abbr[team_id], column="abbreviation")


def _player_matchup_log(con: duckdb.DuckDBPyConnection, meetings: list[dict[str, Any]], limit: Any, a: Entity, b: Entity) -> tuple[list[dict[str, Any]], list[tuple[str, list[str]]]]:
    """The most recent meetings logged: each one's score and both players'
    points/rebounds/assists, with teams abbreviated the way they were that season."""
    shown = meetings[: _clamp_limit(limit, _DEFAULT_MEETINGS_LOGGED)]
    abbr = _names(con, "teams", "team_id", {m["team_id"] for m in shown} | {m["opponent_team_id"] for m in shown}, column="abbreviation")
    log = [
        (
            str(m["day"]),
            [
                f"{_player_matchup_abbr(m['team_id'], m['season'], abbr)} {m['team_score']}-{m['opponent_score']} {_player_matchup_abbr(m['opponent_team_id'], m['season'], abbr)}",
                _player_matchup_stat_line(m["a"]),
                _player_matchup_stat_line(m["b"]),
            ],
        )
        for m in shown
    ]
    return shown, log


def _player_matchup_answer(
    a: Entity,
    b: Entity,
    scope: _Scope,
    meetings: list[dict[str, Any]],
    wins: int,
    lines: dict[str, dict[str, Any]],
    count: int,
    summary: list[tuple[str, list[str]]],
    shown: list[dict[str, Any]],
    log: list[tuple[str, list[str]]],
    caveat: str,
) -> TemplateResult:
    """The head-to-head summary table and the most recent meetings beside it."""
    label = scope.label(min(m["season"] for m in meetings), max(m["season"] for m in meetings))
    title = f"{a.name} vs {b.name}, {label}: {count} meeting{'' if count == 1 else 's'}, {a.name}'s team won {wins}."
    answer = _table(title, [a.name, b.name], summary)
    answer += "\n\n" + _table(f"Most recent {len(shown)} of {count} (points/rebounds/assists):", ["score", a.name, b.name], log)
    answer += f"\n{caveat.strip()}" if caveat else ""
    games = [{"date": str(m["day"]), "won": m["won"], "team_score": m["team_score"], "opponent_score": m["opponent_score"], a.name: m["a"], b.name: m["b"]} for m in shown]
    data = {"players": [a.name, b.name], "span": label, "meetings": count, "wins": {a.name: wins, b.name: count - wins}, "averages": lines, "games": games}
    return TemplateResult(data=data, answer=answer)
