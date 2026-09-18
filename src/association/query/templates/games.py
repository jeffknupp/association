"""Game-level questions: game logs, head-to-head records, a team's or player's quarter or half, and two players' meetings.

.. versionadded:: 3.0.0
   Split out of the former ``association.query.templates`` module.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import duckdb

from association.nba.coverage import COVERAGE
from association.nba.franchises import season_name, season_name_sql
from association.nba.season import current_season, eastern_day_utc_range
from association.nba.season import eastern_date as _eastern_date

from ..conditions import _PLAYER_GAME_TABLES, _cell, _matchup_line, _meetings, _names, _player_games, _Scope, _table, _totals, _unseen_meetings, box_source
from ..entities import Entity
from ..leaderboard import resolve_metric
from ..shotchart import SHOT_AVAILABILITY, SHOT_VALUE_SQL, UNSEPARABLE_SHOT_VALUES
from .common import (
    _BOX_SCORES,
    _GAME_LOGS,
    _PLAYER_GAMES,
    HISTORY_COLUMNS,
    PLAYER_STAT_COLUMNS,
    REBUILT_STATS,
    THRESHOLD_STAT_COLUMNS,
    TemplateContext,
    TemplateResult,
    TemplateUnsupported,
    _box_score_notes,
    _career_end,
    _checked_venue,
    _clamp_limit,
    _condition_scope,
    _log_carries_rebuilt,
    _narrow_player_games,
    _Narrowed,
    _no_games,
    _no_narrowed_games,
    _optional_team,
    _ordinal,
    _period,
    _resolved_player,
    _resolved_team,
    _slot_season,
    _Span,
    _span_of,
    _where_in,
)


def _eastern_day(day: str) -> tuple[str, str]:
    """The half-open range of ``games.date`` values that tip on the Eastern
    date ``day``. Timestamps of one fixed shape compare correctly as strings.

    ``LIKE 'YYYY-MM-DD%'`` matched the UTC date instead, so asking for a game on
    the 15th found the one played the evening of the 14th, and missed its own
    whenever it tipped after 7pm. The range follows daylight time
    (:func:`association.nba.season.eastern_day_utc_range`), so a summer date-only
    stamp - midnight Eastern, 04:00Z - is found on the day it names."""
    datetime.strptime(day, "%Y-%m-%d")  # noqa: DTZ007 - the same ValueError on a malformed day as before; the value is discarded
    return eastern_day_utc_range(day)


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


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# games is home/away-oriented, not team-perspective: joining team_id to only
# home_team_id silently returns that team's HOME games, with no error.
# team_box_stats carries the team-perspective row (team_id, opponent_team_id,
# home_away); who WON lives only on games.winner_team_id. team_score /
# opponent_score are derived from home_away rather than read raw, since raw
# home_score/away_score needs a per-row guess that goes backwards sometimes.
#
# Joined on season as well as event_id: the phantom 1993 shares every event id
# with 1994, so a 1994 log keyed on event_id alone listed each game twice.
#
# Read from `real_games`, the one filtered list (fetch/repairs/real_games.py): this
# join used to be over `games`, on the belief that joining team_box_stats
# excluded the junk rows by itself. It does not - EVERY games row has a
# team_box_stats row, phantoms and 0-0 placeholders included - so a 1999 or
# 2000 Bulls log listed placeholder games. The winner is still returned raw
# rather than compared, since a NULL there and a loss are different facts.
_TEAM_GAMES_SQL = f"""
SELECT g.date,
       tbs.home_away,
       {season_name_sql("opp.team_id", "tbs.season", "opp.display_name")} AS opponent,
       CASE WHEN tbs.home_away = 'home' THEN g.home_score ELSE g.away_score END AS team_score,
       CASE WHEN tbs.home_away = 'home' THEN g.away_score ELSE g.home_score END AS opponent_score,
       g.winner_team_id,
       tbs.team_id,
       CASE WHEN tbs.season_type = 3 THEN CAST(substr(g.date, 1, 4) AS INTEGER) ELSE tbs.season END AS season
FROM team_box_stats tbs
JOIN real_games g ON g.event_id = tbs.event_id AND g.season = tbs.season
JOIN teams opp ON opp.team_id = tbs.opponent_team_id
"""


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

    .. versionchanged:: 4.0.2
       A player's log with a defaulted (unnamed) season and no games in it
       now redirects to the seasons he has on record, when there are any,
       rather than a refusal that reads as though his whole career were
       missing - "No 2026 regular season games found for Tim Hardaway" now
       also says he last appears in 2003 and names his 1995-2003 range. A
       season the question named outright keeps the plain refusal.
    """
    con = ctx.con
    season_type = slots.get("season_type") or 2
    limit = _clamp_limit(slots.get("limit"), default=DEFAULT_GAME_LOG_LIMIT)
    asked = slots["limit"] if isinstance(slots.get("limit"), int) and slots["limit"] >= 1 else None
    ascending = slots.get("order") == "first"
    raw_date = slots.get("date")
    date = raw_date if isinstance(raw_date, str) and _ISO_DATE.match(raw_date) else None
    opponent, venue, span, without = slots.get("opponent"), slots.get("venue"), slots.get("span"), slots.get("without")
    if slots.get("threshold") is not None:
        # "mikal bridges game log with less than 15 fga" would list his last
        # ten games whatever they held; keeping only the games past a line is a
        # filter this template does not have.
        raise TemplateUnsupported("game_log cannot keep only the games past a threshold")
    # A date names its game outright, so it replaces the season rather than
    # being filtered inside it: the router's season is usually its "current"
    # default, and a date from last season looked for in this one finds nothing.
    season = None if date else slots.get("season")
    span = "career" if date else span

    if slots.get("team"):
        team = _resolved_team(con, slots.get("team"), season=_slot_season(slots))
        if isinstance(team, TemplateResult):
            return team
        if without:
            raise TemplateUnsupported("a team's games without one of its players is a with_without question")
        scope = _span_of(span, season, season_type, "games")
        return _team_game_log(con, team, scope, opponent=opponent, venue=venue, date=date, limit=limit, ascending=ascending)

    # The span is settled before the name is resolved, because it is what
    # narrows the name. `season` here is the raw slot - None means the current
    # season only once _span_of reads it - and passed through as it was, it
    # narrowed "curry's last 5 games" over every season and asked about all six
    # Currys again.
    scope = _span_of(span, season, season_type, "player_game_log")
    player = _resolved_player(con, slots.get("player"), "game_log needs a team or a player", available=_GAME_LOGS, season=scope.season, through=_career_end(scope.season))
    if isinstance(player, TemplateResult):
        return player
    extras = _log_extras(slots.get("stat"))
    narrowed = _narrow_player_games(con, player, scope, opponent=opponent, venue=venue, without=without)
    if isinstance(narrowed, TemplateResult):
        return narrowed
    if date:
        start, end = _eastern_day(date)
        narrowed.extra.append("g.date >= ? AND g.date < ?")
        narrowed.extra_params += [start, end]
        narrowed.date = date
    return _player_game_log(con, player, scope, narrowed, extras, limit=limit, asked=asked, ascending=ascending)


def _scope(count: int, ascending: bool, date: str | None) -> str:
    if date:
        return f"on {date}"
    if count == 1:
        return "first game" if ascending else "most recent game"
    return f"first {count} games" if ascending else f"last {count} games"


# The season a team's game belongs to, by the project's convention: a
# postseason by the calendar year it was played in, since ESPN labels every
# season before 1993-94 by the year it started (see _season_games).
_TEAM_SEASON = "CASE WHEN tbs.season_type = 3 THEN CAST(substr(g.date, 1, 4) AS INTEGER) ELSE tbs.season END"


def _postseason_scope(span: _Span) -> tuple[str, list[Any]]:
    """_Span.clause for a postseason over ``games`` (aliased ``g``): one
    playoffs by the calendar year it was played in, or every playoffs from
    ``span.first`` on - never by label, for the reason _season_games gives.
    Labeled, a career of playoff games dropped the 1989 playoffs (stored as
    1988) and printed the 1991 run as "1990"."""
    if span.season is not None:
        return _season_games(span.season, 3, "g")
    phantom = COVERAGE["games"].phantom
    excluded = f" AND g.season NOT IN ({', '.join('?' for _ in phantom)})" if phantom else ""
    return f"CAST(substr(g.date, 1, 4) AS INTEGER) >= ?{excluded}", [span.first, *phantom]


def _team_game_log_filters(con: duckdb.DuckDBPyConnection, team: Entity, span: _Span, *, opponent: Any, venue: Any, date: str | None) -> tuple[list[str], list[Any], str] | TemplateResult:
    """The extra WHERE clauses for an opponent, a venue and a date where the
    question named them, plus the narrowing phrase for the header and the
    empty-result sentence. Returns a `TemplateResult` early if a named
    opponent cannot be resolved, or is the team itself."""
    extra: list[str] = []
    extra_params: list[Any] = []
    filters: list[str] = []
    if opponent:
        rival = _resolved_team(con, opponent, season=span.season)
        if isinstance(rival, TemplateResult):
            return rival
        if rival.id == team.id:
            raise TemplateUnsupported("a team cannot be its own opponent")
        extra.append("tbs.opponent_team_id = ?")
        extra_params.append(rival.id)
        filters.append(f"vs the {rival.name}")
    if venue:
        checked = _checked_venue(venue)
        extra.append("tbs.home_away = ?")
        extra_params.append(checked)
        filters.append("at home" if checked == "home" else "on the road")
    if date:
        start, end = _eastern_day(date)
        extra.append("g.date >= ? AND g.date < ?")
        extra_params += [start, end]
    return extra, extra_params, "".join(f" {f}" for f in filters)


def _team_game_log_none(con: duckdb.DuckDBPyConnection, team: Entity, span: _Span, base: list[str], base_params: list[Any], *, narrowed: str, date: str | None) -> TemplateResult:
    """Which fact is missing when no row matched: the team's games in that
    span, or the match - so the sentence names the right one."""
    found = con.execute(
        f"SELECT COUNT(*), MIN({_TEAM_SEASON}), MAX({_TEAM_SEASON}) FROM team_box_stats tbs JOIN real_games g ON g.event_id = tbs.event_id AND g.season = tbs.season WHERE {' AND '.join(base)}",
        base_params,
    ).fetchone()
    total, first, last = found if found else (0, None, None)
    if not total:
        where = _period(span.season, span.season_type) if span.season is not None else f"{span.kind}s on record"
        return TemplateResult(data={"team": team.name, "games": []}, answer=f"No {where} games found for the {team.name}.")
    on_date = f" on {date}" if date else ""
    answer = f"The {team.name} played {total:,} games {span.during(first, last, whose='all seasons on record')}, none of them{narrowed}{on_date}."
    return TemplateResult(data={"team": team.name, "games": []}, answer=answer)


def _team_game_log_rows(team: Entity, span: _Span, rows: list[tuple[Any, ...]], *, narrowed: str, date: str | None, ascending: bool) -> TemplateResult:
    """The games listing, and the wins/losses record tallied over exactly the
    rows being shown rather than recounted from it later - that recount is
    where a wins/losses total gets inverted."""
    games = [
        {"date": _eastern_date(r[0]), "home_away": r[1], "opponent": r[2], "team_score": r[3], "opponent_score": r[4], "won": None if r[5] is None else r[5] == r[6], "season": r[7]} for r in rows
    ]
    wins = sum(1 for g in games if g["won"] is True)
    losses = sum(1 for g in games if g["won"] is False)
    unknown = len(games) - wins - losses
    record = f"{wins}-{losses}" + (f", {unknown} with no recorded result" if unknown else "")
    seasons = [g["season"] for g in games]
    if span.season is not None:
        where = f" of the {_period(span.season, span.season_type)}"
    else:
        years = span.years(min(seasons), max(seasons))
        where = f" ({years})" if date else f" (all-time, {years})"
    header = f"{team.name}{narrowed}, {_scope(len(games), ascending, date)}{where} ({record}):"
    mark = {True: "W", False: "L", None: "?"}
    lines = [f"  {g['date']}  {mark[g['won']]} {g['team_score']}-{g['opponent_score']}  {'vs' if g['home_away'] == 'home' else 'at'} {g['opponent']}" for g in games]
    return TemplateResult(data={"team": team.name, "wins": wins, "losses": losses, "games": games}, answer="\n".join([header, *lines]))


def _team_game_log(con: duckdb.DuckDBPyConnection, team: Entity, span: _Span, *, opponent: Any, venue: Any, date: str | None, limit: int, ascending: bool) -> TemplateResult:
    """A team's games in ``span``, narrowed to an opponent, a venue and a date
    where the question named them."""
    # A postseason by the calendar year it was played in - see _season_games.
    clause, params = _postseason_scope(span) if span.season_type == 3 else span.clause("tbs.season")
    base, base_params = ["tbs.team_id = ?", "tbs.season_type = ?", clause], [team.id, span.season_type, *params]
    filtered = _team_game_log_filters(con, team, span, opponent=opponent, venue=venue, date=date)
    if isinstance(filtered, TemplateResult):
        return filtered
    extra, extra_params, narrowed = filtered
    rows = con.execute(
        f"{_TEAM_GAMES_SQL} WHERE {' AND '.join(base + extra)} ORDER BY g.date {'ASC' if ascending else 'DESC'} LIMIT ?",
        [*base_params, *extra_params, limit],
    ).fetchall()
    if not rows:
        # Which fact is missing: the team's games in that span, or the match.
        return _team_game_log_none(con, team, span, base, base_params, narrowed=narrowed, date=date)
    return _team_game_log_rows(team, span, rows, narrowed=narrowed, date=date, ascending=ascending)


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


def _player_game_log(con: duckdb.DuckDBPyConnection, player: Entity, span: _Span, narrowed: _Narrowed, extras: tuple[str, ...], *, limit: int, asked: int | None, ascending: bool) -> TemplateResult:
    """The listing, and the per-game averages over exactly the rows in it."""
    headers, needed, rebuilt = _player_game_log_columns(con, extras)
    where, params = narrowed.clauses(rebuilt=rebuilt)
    rows = con.execute(
        f"SELECT pgl.game_date, pgl.season, pgl.opponent_abbr, g.home_team_id = pgl.team_id, g.winner_team_id, pgl.team_id, "
        f"{'pgl.reconstructed' if rebuilt else 'FALSE'}, "
        f"{', '.join(f'pgl.{_LOG_COLUMNS[h]}' for h in needed)} {_PLAYER_GAMES} WHERE {where} ORDER BY pgl.game_date {'ASC' if ascending else 'DESC'} LIMIT ?",
        [*params, limit],
    ).fetchall()
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


def head_to_head(ctx: TemplateContext, slots: dict[str, Any]) -> TemplateResult:
    """ "How many times did the 76ers play Boston?" - games between two teams.

    That question was answered "they did not play" (they played four times).
    The agent wrote `home_team_id = 'PHI'` against an opaque numeric VARCHAR
    ('20'), so the filter matched nothing - with the rule against it, and a
    worked WRONG example, in its prompt. Prompting cannot fix that; resolving
    names to ids in code can."""
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
    # No season named means the CURRENT one, as everywhere else. "All time" is
    # a defensible reading here, but silently answering a different span than
    # the rest of the system is the substitution this design exists to prevent.
    # The answer names the season, so another one is a follow-up away.
    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    # Both orderings, since `games` is home/away-oriented rather than
    # team-perspective, and the season filter parenthesized around the whole
    # matchup - `A OR B AND season = ...` applies the season to one side only.
    season_clause, season_params = _season_games(season, season_type, "g")
    where = [
        "((g.home_team_id = ? AND g.away_team_id = ?) OR (g.home_team_id = ? AND g.away_team_id = ?))",
        "g.season_type = ?",
        season_clause,
    ]
    params: list[Any] = [a.id, b.id, b.id, a.id, season_type, *season_params]
    rows = con.execute(
        f"SELECT g.date, g.home_team_id, g.home_score, g.away_score, g.winner_team_id FROM real_games g WHERE {' AND '.join(where)} ORDER BY g.date",
        params,
    ).fetchall()

    a_wins = sum(1 for r in rows if r[4] == a.id)
    b_wins = sum(1 for r in rows if r[4] == b.id)
    period = _period(season, season_type)
    return TemplateResult(
        data={"teams": [a.name, b.name], "games": len(rows), "wins": {a.name: a_wins, b.name: b_wins}},
        answer=_phrase_head_to_head(a.name, b.name, len(rows), a_wins, b_wins, period),
    )


def _phrase_head_to_head(a: str, b: str, games: int, a_wins: int, b_wins: int, period: str) -> str:
    if games == 0:
        return f"The warehouse has no {period} games between the {a} and the {b}."
    times = "once" if games == 1 else f"{games} times"
    lead = f"The {a} and the {b} met {times} in the {period}"
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
    period = slots.get("period")
    if not isinstance(period, int) or not 1 <= period <= 10:
        raise TemplateUnsupported(f"team_quarter_points needs an integer period 1-10, got {period!r}")
    if isinstance(slots.get("player"), str) and slots["player"].strip():
        # A named player's quarter or half is period_split's job, not this
        # template's - see the docstring.
        raise TemplateUnsupported("team_quarter_points cannot answer for a named player")

    resolved = _team_quarter_points_teams(con, slots.get("team"), slots.get("opponent"), _slot_season(slots))
    if isinstance(resolved, TemplateResult):
        return resolved
    team, opponent = resolved

    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    games = _team_quarter_points_games(con, team, opponent, season, season_type, period)

    period_label = _period_label(period)
    period_str = _period(season, season_type)
    return _team_quarter_points_answer(team, opponent, games, period=period, period_label=period_label, period_str=period_str)


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


def _team_quarter_points_games(con: duckdb.DuckDBPyConnection, team: Entity, opponent: Entity | None, season: int, season_type: int, period: int) -> list[dict[str, Any]]:
    """Each qualifying game's date, opponent and points in the asked-for
    period - None where the game never reached it, not zero."""
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
        points = scores[period - 1] if period - 1 < len(scores) else None
        games.append({"date": _eastern_date(date), "opponent": opp_name, "points": points})
    return games


def _team_quarter_points_answer(team: Entity, opponent: Entity | None, games: list[dict[str, Any]], *, period: int, period_label: str, period_str: str) -> TemplateResult:
    """The no-games refusal, the none-reached-that-period refusal, or the
    normal per-game breakdown and total."""
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
    data = {"team": team.name, "opponent": opponent_name, "period": period, "games": played, "total": total}
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


def _period_scope(slots: dict[str, Any]) -> tuple[tuple[int, ...], str]:
    """The periods a question asks for, and how to name them in an answer."""
    half = slots.get("half")
    if isinstance(half, int) and half in _HALF_PERIODS:
        return _HALF_PERIODS[half], f"{_ordinal(half)} half"
    period = slots.get("period")
    if isinstance(period, int) and 1 <= period <= 10:
        return (period,), _period_label(period)
    raise TemplateUnsupported(f"period_split needs a period 1-10 or a half 1-2, got period={slots.get('period')!r} half={half!r}")


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
    """
    con = ctx.con
    periods, period_label = _period_scope(slots)
    stat = slots.get("stat")
    if isinstance(stat, str) and stat.strip() and stat not in ("points", "all"):
        raise TemplateUnsupported(f"period_split answers points only, not {stat!r} - no other stat is recorded per period")

    season = slots.get("season") or current_season()
    season_type = slots.get("season_type") or 2
    agreement = PERIOD_RECONCILIATION.get(season)
    refusal = _period_split_refusal(season, agreement)
    if refusal is not None:
        return refusal

    player = _resolved_player(con, slots.get("player"), available=SHOT_AVAILABILITY, season=season)
    if isinstance(player, TemplateResult):
        return player

    opponent = _optional_team(con, slots.get("opponent"), season=_slot_season(slots))
    if isinstance(opponent, TemplateResult):
        return opponent

    venue = slots.get("venue") if slots.get("venue") in ("home", "away") else None
    rows = _period_split_rows(con, player, season, season_type, periods, venue, opponent)

    scope = _period(season, season_type)
    vs = f" against the {opponent.name}" if opponent else ""
    at = f" at {'home' if venue == 'home' else 'away'}" if venue else ""
    games = [{"date": _eastern_date(d), "opponent": name, "home_away": side, "points": int(pts or 0)} for d, side, name, pts in rows]
    data: dict[str, Any] = {
        "player": player.name,
        "period": period_label,
        "season": season,
        "opponent": opponent.name if opponent else None,
        "venue": venue,
        "games": games,
        "games_played": len(games),
    }
    if not games:
        message = f"No {scope} games found for {player.name}{vs}{at}."
        return TemplateResult(data={**data, "message": message}, answer=message)

    total = sum(g["points"] for g in games)
    average = total / len(games)
    data |= {"total": total, "average": average}
    header = _period_split_header(player, period_label, scope, vs, at, total, average, games, slots)
    return TemplateResult(data=data, answer=header + _period_split_caveat(season, agreement))


def _period_split_refusal(season: int, agreement: float | None) -> TemplateResult | None:
    """A refusal for a season whose per-period points do not reliably match
    ESPN's own quarter scores (see :data:`PERIOD_RECONCILIATION`), or None
    for a season trusted at face value."""
    if season in UNSEPARABLE_SHOT_VALUES or (agreement is not None and agreement < PERIOD_REFUSE_BELOW):
        why = UNSEPARABLE_SHOT_VALUES.get(season) or f"its per-period points agree with ESPN's own quarter scores only {agreement:.0f}% of the time"
        message = f"Per-quarter scoring cannot be answered for {season}: {why}."
        return TemplateResult(data={"season": season, "message": message}, answer=message)
    return None


def _period_split_rows(con: duckdb.DuckDBPyConnection, player: Entity, season: int, season_type: int, periods: tuple[int, ...], venue: str | None, opponent: Entity | None) -> list[tuple[Any, ...]]:
    """A player's per-game point total in the wanted periods, one row a game.

    The games are the ones he PLAYED, with zero where he did not score in the
    period - not the games that have a made shot. The first version counted
    only the latter, so every scoreless quarter left the denominator: "RJ
    Barrett ... over 46 games, averaging 5.4" was a player with 57 games and
    a true 4.4. The sum was right, which is exactly why it read as correct.

    A played game counts only where the shot table covers that game at all.
    2003's shots cover 986 of its games, and a game with no located shots
    would otherwise contribute a confident zero.

    SHOT_VALUE_SQL names `shot_chart`'s columns bare, and `season` is a column
    of `games` too - so the value is summed in a CTE over `shot_chart` alone,
    where those names can only mean one thing.
    """
    box = box_source(con)
    appeared = "(b.minutes IS NOT NULL OR b.reconstructed)" if box.rebuilt else "b.minutes IS NOT NULL"
    marks = ", ".join("?" for _ in periods)
    params: list[Any] = [player.id, season, season_type, *periods, player.id, season, season_type]
    where: list[str] = []
    if venue is not None:
        where.append("(CASE WHEN g.home_team_id = b.team_id THEN 'home' ELSE 'away' END) = ?")
        params.append(venue)
    if opponent is not None:
        where.append("(CASE WHEN g.home_team_id = b.team_id THEN g.away_team_id ELSE g.home_team_id END) = ?")
        params.append(opponent.id)
    return con.execute(
        f"""
        WITH scored AS (
            SELECT event_id, SUM({SHOT_VALUE_SQL}) AS points
            FROM shot_chart
            WHERE athlete_id = ? AND made AND season = ? AND season_type = ? AND period IN ({marks})
            GROUP BY 1
        )
        SELECT g.date,
               CASE WHEN g.home_team_id = b.team_id THEN 'home' ELSE 'away' END AS side,
               {season_name_sql("t.team_id", "g.season", "t.display_name")},
               COALESCE(s.points, 0)
        FROM {box.table} b
        JOIN games g ON g.event_id = b.event_id AND g.season = b.season
        LEFT JOIN scored s ON s.event_id = b.event_id
        LEFT JOIN teams t ON t.team_id = CASE WHEN g.home_team_id = b.team_id THEN g.away_team_id ELSE g.home_team_id END
        WHERE b.athlete_id = ? AND b.season = ? AND b.season_type = ? AND NOT b.did_not_play AND {appeared}
          AND EXISTS (SELECT 1 FROM shot_chart x WHERE x.event_id = b.event_id)
          {"".join(" AND " + w for w in where)}
        ORDER BY g.date
        """,
        params,
    ).fetchall()


def _period_split_header(player: Entity, period_label: str, scope: str, vs: str, at: str, total: int, average: float, games: list[dict[str, Any]], slots: dict[str, Any]) -> str:
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
        shown = games[-_clamp_limit(slots.get("limit"), default=DEFAULT_GAME_LOG_LIMIT) :]
        rows_out = [f"  {g['date']}  {'vs' if g['home_away'] == 'home' else '@ '} {g['opponent'] or '?':<24} {g['points']:>3}" for g in reversed(shown)]
        label = "every game" if len(shown) == len(games) else f"the {len(shown)} most recent"
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

    .. versionadded:: 2.1.0
    """
    con = ctx.con
    players = slots.get("players")
    listed: list[Any] = players if isinstance(players, list) else []
    texts = list(dict.fromkeys(n.strip() for n in [*listed, slots.get("player")] if isinstance(n, str) and n.strip()))
    if len(texts) != 2:
        raise TemplateUnsupported(f"player_matchup needs exactly two players, got {texts!r}")
    scope = _condition_scope(slots.get("season"), slots.get("span"), slots.get("season_type"), _PLAYER_GAME_TABLES)
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
