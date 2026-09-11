"""Games under a condition: the SQL and tables behind five templates.

``player_splits``, ``with_without``, ``record_when``, ``player_matchup`` and
``streak`` in :mod:`association.query.templates` all answer one kind of
question - take a set of games, divide it by something that happened in each,
and report the parts side by side. The templates themselves (slot checks, name
resolution, refusals) live with the others in that module. The SQL they share
lives here only because it is long, and nothing here imports ``templates``, so
the dependency runs one way.

Four rules about the data decide almost everything below. Each was measured
against the warehouse rather than assumed:

- **Played means a box-score row with minutes.** A game a player missed shows
  up three different ways: a row with ``did_not_play`` set (5,530 of them in
  2025-26), no row at all (Jayson Tatum has none for the games his Achilles
  cost him that season), or - through the 2006-2012 box scores - a row with
  ``did_not_play`` false, NULL minutes and every stat zero, beside teammates
  who do have minutes (10,104 in 2010 alone). No row with NULL minutes carries
  a single nonzero stat anywhere in the warehouse. Zero minutes counts as
  played: 31 such rows scored.
- **A game with no box score is unknown, not missed.** From 2013 to 2018 ESPN
  lacks the box score for about one game in eight - 326 team-games a season in
  which every player is listed with NULL minutes. LeBron James played all 82
  games of 2017-18 and has six of these. Read as "did not play", each would be
  a game his team played without him, so :func:`_box_missing` finds them and
  the templates leave them out of both sides of a comparison, end a streak at
  them rather than carry it across, and say how many there were.
- **A game happens on its US Eastern date.** ``games.date`` is a UTC tip
  time, so a 7:30pm Eastern tip lands on the next calendar day, and a split by
  month would put a March 31st game in April. The shift is the same fixed five
  hours ``fetch.parse`` uses to match NetPoints files to games, and it is safe
  for the same reason: EST and EDT disagree about a date only between midnight
  and 1am Eastern, when no NBA game starts.
- **A game with no winner is not a result.** 268 team-games in 1999-2002 are
  0-0 placeholders with no ``winner_team_id``, and 252 of them fall on the same
  Eastern date as that team's real game. Counted, each would be a loss, so
  they are dropped instead.

Season 1993 is a copy of 1994 (see :mod:`association.coverage`), so a span of
several seasons leaves it out rather than counting 1993-94 twice.

.. versionadded:: 2.1.0
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import duckdb

from association.coverage import COVERAGE

# The same fixed shift as fetch.parse._EASTERN_OFFSET, and kept equal to it by
# test_the_eastern_shift_matches_the_fetch_path. Not imported: that module
# belongs to the fetch path, and a query has no business loading it for one
# number.
_EASTERN_OFFSET_HOURS = 5

_SEASON_TYPE_WORDS = {2: "regular season", 3: "postseason"}

# The first season the warehouse names for the year it ends, as season.py
# does. Every earlier one is filed under the year it began.
_FIRST_END_YEAR_SEASON = 1994

# The tables a player's games are read from, and a team's. A player's always
# include team_box_stats, which carries the home/away side of each game.
_PLAYER_GAME_TABLES = ("player_box_stats", "team_box_stats", "games")
_TEAM_GAME_TABLES = ("team_box_stats", "games")

# A team can have two games on one Eastern date only when ESPN's clock is
# wrong (a handful exist), so the raw timestamp and then the id settle the
# order deterministically rather than leaving it to the engine.
_GAME_ORDER = "day, stamp, event_id"


def _eastern_day(column: str) -> str:
    """SQL for the US Eastern calendar date of a ``games.date`` value.

    The column holds '2026-01-01T00:30Z', which DuckDB will not cast to a
    timestamp as it stands - hence the two replaces."""
    return f"CAST(CAST(replace(replace({column}, 'T', ' '), 'Z', '') AS TIMESTAMP) - INTERVAL {_EASTERN_OFFSET_HOURS} HOUR AS DATE)"


def _played(alias: str) -> str:
    """SQL for "this box-score row is a game he actually played" - see the module docstring."""
    return f"(NOT {alias}.did_not_play AND {alias}.minutes IS NOT NULL)"


@dataclass(frozen=True)
class _Scope:
    """The games a question covers: one season, or every season on record.

    ``first`` and ``phantoms`` only matter for the second, and come from
    :data:`association.coverage.COVERAGE`: a span of seasons starts where the
    narrowest table does - the floor ``check_coverage`` refuses a single
    season under - and never counts a phantom season."""

    season: int | None
    season_type: int
    first: int
    phantoms: tuple[int, ...]

    def where(self, alias: str) -> str:
        """SQL restricting rows aliased ``alias`` to these games, with named
        parameters that :meth:`params` supplies."""
        if self.season is not None:
            return f"{alias}.season = $season AND {alias}.season_type = $season_type"
        # Inlined rather than bound: these are ints out of COVERAGE, never a slot.
        excluded = f" AND {alias}.season NOT IN ({', '.join(str(int(p)) for p in self.phantoms)})" if self.phantoms else ""
        return f"{alias}.season >= $first AND {alias}.season_type = $season_type{excluded}"

    def params(self) -> dict[str, Any]:
        """The values :meth:`where` binds - exactly its names, because DuckDB rejects an unused one."""
        if self.season is not None:
            return {"season": self.season, "season_type": self.season_type}
        return {"first": self.first, "season_type": self.season_type}

    @property
    def kind(self) -> str:
        """ "regular season" or "postseason", as an answer names it."""
        return _SEASON_TYPE_WORDS.get(self.season_type, "regular season")

    def label(self, first: Any = None, last: Any = None) -> str:
        """The span in words, for every answer to name. Across seasons it is the
        seasons the rows actually came from, when there were any."""
        if self.season is not None:
            return f"{self.season} {self.kind}"
        if isinstance(first, int) and isinstance(last, int):
            return f"{first} {self.kind}" if first == last else f"{first}-{last} {self.kind}s"
        return f"every {self.kind} on record ({self.first} onward)"

    @property
    def misfiled(self) -> bool:
        """Whether this is one postseason from before 1993-94, which the
        warehouse files under the wrong year: ESPN labels those seasons by the
        year they began, so "1990" holds the April-June 1991 playoffs. Measured
        from the games' own dates, 1988 through 1992 alike; 1993 is the
        phantom copy of 1994. Only the postseason reaches back that far - the
        regular season's floor is already 1994."""
        return self.season_type == 3 and self.season is not None and self.season < _FIRST_END_YEAR_SEASON

    def floor_note(self, first: Any) -> str:
        """A sentence for a multi-season answer whose earliest row sits on the
        coverage floor: the career may have started before the box scores did."""
        if self.season is None and first == self.first:
            return f" Box scores start with the {self.first} {self.kind}; anything earlier is not counted."
        return ""


def _game_scope(season: int | None, season_type: int, tables: Iterable[str]) -> _Scope:
    known = [COVERAGE[t] for t in tables if t in COVERAGE]
    # Never before 1994, even for the team tables' playoff games that reach
    # 1988: those seasons are filed under the wrong year (see _Scope.misfiled).
    first = max(_FIRST_END_YEAR_SEASON, *(c.floor(season_type).season for c in known))
    phantoms = tuple(sorted({p for c in known for p in c.phantom}))
    return _Scope(season, season_type, first, phantoms)


def _team_games(scope: _Scope, extra: str = "") -> str:
    """One row per team per game with a result, from that team's side of it.

    ``games`` is home/away-oriented and joining a team to only one side of it
    silently returns half its games, so the team's own row in team_box_stats
    decides which side it was on - the same join ``game_log`` uses."""
    return f"""
        SELECT tbs.team_id, tbs.season, tbs.event_id, g.date AS stamp, {_eastern_day("g.date")} AS day, tbs.home_away,
               g.winner_team_id = tbs.team_id AS won,
               CASE WHEN tbs.home_away = 'home' THEN g.home_score ELSE g.away_score END AS team_score,
               CASE WHEN tbs.home_away = 'home' THEN g.away_score ELSE g.home_score END AS opponent_score,
               tbs.totalRebounds, tbs.assists, tbs.threePointFieldGoalsMade, tbs.fieldGoalsMade, tbs.fieldGoalsAttempted
        FROM team_box_stats tbs JOIN games g ON g.event_id = tbs.event_id AND g.season = tbs.season
        WHERE g.winner_team_id IS NOT NULL AND {scope.where("tbs")}{extra}"""


def _player_games(scope: _Scope, player: str = "player", extra: str = "") -> str:
    """One row per game a player played, with his team's result and side.

    ``player`` names the bound parameter holding his id, so two of these can
    sit in one query; an empty one reads every player's games."""
    who = f" AND pbs.athlete_id = ${player}" if player else ""
    return f"""
        SELECT pbs.*, g.date AS stamp, {_eastern_day("g.date")} AS day, g.winner_team_id = pbs.team_id AS won, tbs.home_away,
               CASE WHEN tbs.home_away = 'home' THEN g.home_score ELSE g.away_score END AS team_score,
               CASE WHEN tbs.home_away = 'home' THEN g.away_score ELSE g.home_score END AS opponent_score
        FROM player_box_stats pbs
        JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season
        JOIN team_box_stats tbs ON tbs.event_id = pbs.event_id AND tbs.team_id = pbs.team_id AND tbs.season = pbs.season
        WHERE g.winner_team_id IS NOT NULL AND {_played("pbs")} AND {scope.where("pbs")}{who}{extra}"""


def _box_missing(scope: _Scope) -> str:
    """Team-games with a result but no box score: not one player row for that
    team in that game carries minutes. See the module docstring for why these
    are unknown rather than games everybody missed."""
    return f"""
        SELECT tbs.team_id, tbs.opponent_team_id, tbs.season, tbs.event_id, g.date AS stamp, {_eastern_day("g.date")} AS day
        FROM team_box_stats tbs JOIN games g ON g.event_id = tbs.event_id AND g.season = tbs.season
        WHERE g.winner_team_id IS NOT NULL AND {scope.where("tbs")}
          AND NOT EXISTS (
              SELECT 1 FROM player_box_stats q WHERE q.event_id = tbs.event_id AND q.team_id = tbs.team_id AND q.season = tbs.season AND q.minutes IS NOT NULL
          )"""


def _spans(played: str) -> str:
    """Per player, team and season: the first and last day he played for that team."""
    return f"SELECT athlete_id, team_id, season, MIN(day) AS first_day, MAX(day) AS last_day FROM ({played}) GROUP BY ALL"


def _unseen(con: duckdb.DuckDBPyConnection, scope: _Scope, played: str, params: dict[str, Any]) -> int:
    """How many games with no box score fall inside the spells a player was
    playing for a team - games he may well have played, which no count built
    from box scores can include. A lower bound: a missing box before his first
    game of a season, or after his last, is not counted."""
    row = con.execute(
        f"""
        SELECT COUNT(DISTINCT m.event_id) FROM ({_box_missing(scope)}) m
        JOIN ({_spans(played)}) s ON s.team_id = m.team_id AND s.season = m.season AND m.day BETWEEN s.first_day AND s.last_day""",
        params,
    ).fetchone()
    return int(row[0]) if row else 0


def _unseen_note(count: int, whose: str = "his team's") -> str:
    if not count:
        return ""
    return f" The warehouse has no box score for {count} of {whose} games in that span - ESPN lacks about one game in eight from 2013 to 2018 - so any of them he played are not counted."


def _player_streak_rows(scope: _Scope, played: str, value: str) -> str:
    """A player's games in order, for a run to be read over: each one he
    played, with ``value`` and his team's result, and each game with no box
    score inside a spell he was playing in, with both unknown. An unknown row
    never satisfies a condition, so a run ends there instead of being carried
    across a game nobody can check."""
    return f"""
        SELECT p.athlete_id, p.season, p.event_id, p.stamp, p.day, p.won, {value} AS value FROM ({played}) p
        UNION ALL
        SELECT s.athlete_id, m.season, m.event_id, m.stamp, m.day, NULL, NULL
        FROM ({_box_missing(scope)}) m JOIN ({_spans(played)}) s ON s.team_id = m.team_id AND s.season = m.season AND m.day BETWEEN s.first_day AND s.last_day"""


# (data key, column header, aggregate over rows aliased `p` / `t`). Shooting is
# made over attempted across the games, never an average of per-game
# percentages, which weights a 1-for-1 night like a 12-for-20 one.
_PLAYER_LINE: tuple[tuple[str, str, str], ...] = (
    ("minutes", "MIN", "AVG(p.minutes)"),
    ("points", "PTS", "AVG(p.points)"),
    ("rebounds", "REB", "AVG(p.rebounds)"),
    ("assists", "AST", "AVG(p.assists)"),
    ("steals", "STL", "AVG(p.steals)"),
    ("blocks", "BLK", "AVG(p.blocks)"),
    ("turnovers", "TOV", "AVG(p.turnovers)"),
    ("threes", "3PM", "AVG(p.threePointFieldGoalsMade)"),
    ("fg_pct", "FG%", "100.0 * SUM(p.fieldGoalsMade) / NULLIF(SUM(p.fieldGoalsAttempted), 0)"),
)
_TEAM_LINE: tuple[tuple[str, str, str], ...] = (
    ("points", "PTS", "AVG(t.team_score)"),
    ("opponent_points", "OPP", "AVG(t.opponent_score)"),
    ("rebounds", "REB", "AVG(t.totalRebounds)"),
    ("assists", "AST", "AVG(t.assists)"),
    ("threes", "3PM", "AVG(t.threePointFieldGoalsMade)"),
    ("fg_pct", "FG%", "100.0 * SUM(t.fieldGoalsMade) / NULLIF(SUM(t.fieldGoalsAttempted), 0)"),
)

_SPLIT_SQL: dict[str, str] = {
    "home_away": "{a}.home_away",
    "starter_bench": "CASE WHEN {a}.starter THEN 'starter' ELSE 'bench' END",
    "wins_losses": "CASE WHEN {a}.won THEN 'wins' ELSE 'losses' END",
    "month": "CAST(month({a}.day) AS VARCHAR)",
}
# Both halves of a two-way split are always shown, an empty one as zero games:
# "never came off the bench" is an answer, and a missing row reads as a bug.
_SPLIT_GROUPS: dict[str, tuple[str, ...]] = {"home_away": ("home", "away"), "starter_bench": ("starter", "bench"), "wins_losses": ("wins", "losses")}
_GROUP_LABELS = {"home": "Home", "away": "Away", "starter": "Starter", "bench": "Bench", "wins": "Wins", "losses": "Losses"}
_SPLIT_TITLES = {"home_away": "home and away", "starter_bench": "starting and off the bench", "wins_losses": "in wins and losses", "month": "by month"}
_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


def _season_month_order(month: int) -> int:
    """October first: a season runs October to June, and a calendar order would put its end before its start."""
    return (month + 2) % 12


def _split_rows(con: duckdb.DuckDBPyConnection, base: str, params: dict[str, Any], alias: str, line: Sequence[tuple[str, str, str]], split: str) -> list[dict[str, Any]]:
    """Games, record and averages in each group of one split, in reading order."""
    group = _SPLIT_SQL[split].format(a=alias)
    measures = ", ".join(sql for _, _, sql in line)
    found = con.execute(f"WITH {alias} AS ({base}) SELECT {group} AS grp, COUNT(*), COUNT(*) FILTER (WHERE {alias}.won), {measures} FROM {alias} GROUP BY grp", params).fetchall()
    by_group = {str(row[0]): row for row in found if row[0] is not None}
    if split == "month":
        keys = sorted(by_group, key=lambda m: _season_month_order(int(m)))
    else:
        # A value outside the expected pair is shown rather than dropped, so a
        # new ESPN label cannot quietly take games out of the table.
        keys = [*_SPLIT_GROUPS[split], *sorted(k for k in by_group if k not in _SPLIT_GROUPS[split])]
    rows: list[dict[str, Any]] = []
    for key in keys:
        row = by_group.get(key)
        games = int(row[1]) if row else 0
        wins = int(row[2]) if row else 0
        entry: dict[str, Any] = {"group": _MONTH_NAMES[int(key) - 1] if split == "month" else key, "games": games, "wins": wins, "losses": games - wins}
        for index, (name, _, _) in enumerate(line):
            entry[name] = row[3 + index] if row else None
        rows.append(entry)
    return rows


def _totals(con: duckdb.DuckDBPyConnection, base: str, params: dict[str, Any]) -> tuple[int, int | None, int | None]:
    """How many games a base query holds, and the first and last season among them."""
    row = con.execute(f"SELECT COUNT(*), MIN(season), MAX(season) FROM ({base})", params).fetchone()
    return (int(row[0]), row[1], row[2]) if row else (0, None, None)


def _cell(value: Any) -> str:
    """A value in an aligned column: one fixed decimal, since "25" beside "27.7" reads as a different unit."""
    if value is None:
        return "-"
    return f"{value:.1f}" if isinstance(value, float) else str(value)


def _win_pct(wins: int, games: int) -> str:
    """Basketball's convention: .667, not 0.667."""
    if not games:
        return "-"
    text = f"{wins / games:.3f}"
    return text[1:] if text.startswith("0") else text


def _margin(value: Any) -> str:
    return "-" if value is None else f"{value:+.1f}"


def _split_cells(entry: dict[str, Any], line: Sequence[tuple[str, str, str]]) -> list[str]:
    return [str(entry["games"]), f"{entry['wins']}-{entry['losses']}", *(_cell(entry[name]) for name, _, _ in line)]


def _split_label(split: str, entry: dict[str, Any]) -> str:
    return entry["group"] if split == "month" else _GROUP_LABELS.get(entry["group"], str(entry["group"]).title())


def _table(title: str, headers: Sequence[str], rows: Sequence[tuple[str, Sequence[str]]]) -> str:
    """An aligned table under a title line: labels left, values right.

    A row with no cells is a blank separator between groups."""
    filled = [cells for _, cells in rows if cells]
    label_width = max((len(label) for label, _ in rows), default=0)
    widths = [max([len(header), *(len(cells[i]) for cells in filled)]) for i, header in enumerate(headers)]
    lines = [title, (" " * label_width + "  " + "  ".join(h.rjust(w) for h, w in zip(headers, widths, strict=True))).rstrip()]
    for label, cells in rows:
        if not cells:
            lines.append("")
            continue
        lines.append((label.ljust(label_width) + "  " + "  ".join(c.rjust(w) for c, w in zip(cells, widths, strict=True))).rstrip())
    return "\n".join(lines)


def _names(con: duckdb.DuckDBPyConnection, table: str, key: str, ids: Iterable[str], column: str = "display_name") -> dict[str, str]:
    """Names by id, from ``teams`` or ``players``. A missing id keeps its id rather than vanishing.

    ``table``, ``key`` and ``column`` are always literals from this package, never a slot."""
    wanted = sorted({str(i) for i in ids})
    if not wanted:
        return {}
    found = dict(con.execute(f"SELECT {key}, {column} FROM {table} WHERE list_contains($ids, {key})", {"ids": wanted}).fetchall())
    return {i: str(found.get(i, i)) for i in wanted}


# ---------------- with and without a teammate ----------------


@dataclass(frozen=True)
class _Stint:
    """One unbroken spell of a player's on one team, as the box scores show it:
    his first and last row for that team, DNP rows included, since a DNP row is
    the box score saying he was on the roster."""

    team_id: str
    first: date
    last: date


def _stints(con: duckdb.DuckDBPyConnection, athlete_id: str, phantoms: tuple[int, ...]) -> list[_Stint]:
    """Every stint of one player's career, earliest first.

    A stint is a run of his box-score rows (in every season type) for one
    team, and it ends in one of two ways. The next row is for another team:
    LeBron James' two Cleveland spells are two stints, so the Cavaliers' Miami
    years do not count as games they played "without" him. Or a whole season
    passes with no row at all, which the box scores cannot tell from his having
    left and come back: counting the gap would put a player's years abroad on
    his old team's without-him record. That costs a season-long injury (Klay
    Thompson's 2020 and 2021 are outside his Warriors stints), which is the
    safe direction - the answer prints the stint dates, so what was left out
    is on the page, where a wrong count would not be.

    An offseason is not a gap. Tatum's last 2025 row is a May playoff game and
    his first 2026 row is in March, so the Celtics' games in between - the
    Achilles - are inside his stint, where they belong."""
    excluded = f" AND pbs.season NOT IN ({', '.join(str(int(p)) for p in phantoms)})" if phantoms else ""
    rows = con.execute(
        f"""
        WITH r AS (
            SELECT pbs.team_id, pbs.season, pbs.event_id, g.date AS stamp, {_eastern_day("g.date")} AS day
            FROM player_box_stats pbs JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season
            WHERE pbs.athlete_id = $player{excluded}
        ), m AS (
            SELECT r.*, CASE WHEN team_id IS DISTINCT FROM LAG(team_id) OVER w OR season - LAG(season) OVER w > 1 THEN 1 ELSE 0 END AS starts
            FROM r WINDOW w AS (ORDER BY {_GAME_ORDER})
        ), n AS (
            SELECT m.*, SUM(starts) OVER (ORDER BY {_GAME_ORDER} ROWS UNBOUNDED PRECEDING) AS stint FROM m
        )
        SELECT team_id, MIN(day), MAX(day) FROM n GROUP BY team_id, stint ORDER BY 2""",
        {"player": athlete_id},
    ).fetchall()
    return [_Stint(str(team), first, last) for team, first, last in rows]


def _overlaps(ours: Sequence[_Stint], theirs: Sequence[_Stint]) -> list[_Stint]:
    """The spells two players were on the same team at the same time."""
    shared = []
    for a in ours:
        for b in theirs:
            if a.team_id == b.team_id and max(a.first, b.first) <= min(a.last, b.last):
                shared.append(_Stint(a.team_id, max(a.first, b.first), min(a.last, b.last)))
    return sorted(shared, key=lambda s: s.first)


def _within(windows: Sequence[_Stint], team_id: str, day: date) -> bool:
    return any(w.team_id == team_id and w.first <= day <= w.last for w in windows)


def _with_without_games(con: duckdb.DuckDBPyConnection, scope: _Scope, windows: Sequence[_Stint], mate: str, subject: str | None) -> tuple[list[dict[str, Any]], int]:
    """Every game a window's team played inside that window, marked with
    whether the teammate played it, and the subject's line where he did - and,
    separately, how many games inside the windows have no box score, which
    belong on neither side."""
    teams = sorted({w.team_id for w in windows})
    played_by = f"LEFT JOIN player_box_stats s ON s.event_id = t.event_id AND s.season = t.season AND s.team_id = t.team_id AND s.athlete_id = $subject AND {_played('s')}"
    params: dict[str, Any] = {**scope.params(), "teams": teams, "mate": mate}
    if subject is not None:
        params["subject"] = subject
    rows = con.execute(
        f"""
        WITH t AS ({_team_games(scope, " AND list_contains($teams, tbs.team_id)")})
        SELECT t.team_id, t.season, t.day, t.won, t.team_score - t.opponent_score, m.athlete_id IS NOT NULL,
               {"s.minutes, s.points, s.rebounds, s.assists, s.fieldGoalsMade, s.fieldGoalsAttempted" if subject else "NULL, NULL, NULL, NULL, NULL, NULL"},
               EXISTS (
                   SELECT 1 FROM player_box_stats q WHERE q.event_id = t.event_id AND q.team_id = t.team_id AND q.season = t.season AND q.minutes IS NOT NULL
               ) AS box
        FROM t
        LEFT JOIN player_box_stats m ON m.event_id = t.event_id AND m.season = t.season AND m.team_id = t.team_id AND m.athlete_id = $mate AND {_played("m")}
        {played_by if subject else ""}""",
        params,
    ).fetchall()
    keys = ("team_id", "season", "day", "won", "margin", "mate_played", "minutes", "points", "rebounds", "assists", "fgm", "fga")
    inside = [row for row in rows if _within(windows, str(row[0]), row[2])]
    return [dict(zip(keys, row[:-1], strict=True)) for row in inside if row[-1]], sum(1 for row in inside if not row[-1])


def _with_without_group(games: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """The team's record in a set of games, and the subject's averages over the ones he played."""
    wins = sum(1 for g in games if g["won"])
    his = [g for g in games if g["minutes"] is not None]
    fga = sum(g["fga"] or 0 for g in his)

    def average(key: str) -> float | None:
        """His per-game average of ``key`` over the games he played, or None if he played none."""
        return sum(g[key] or 0 for g in his) / len(his) if his else None

    return {
        "games": len(games),
        "wins": wins,
        "losses": len(games) - wins,
        "avg_margin": sum(g["margin"] or 0 for g in games) / len(games) if games else None,
        "player_games": len(his),
        "minutes": average("minutes"),
        "points": average("points"),
        "rebounds": average("rebounds"),
        "assists": average("assists"),
        "fg_pct": 100.0 * sum(g["fgm"] or 0 for g in his) / fga if fga else None,
    }


# ---------------- streaks ----------------


def _longest_runs(
    con: duckdb.DuckDBPyConnection,
    base: str,
    params: dict[str, Any],
    partition: tuple[str, ...],
    hit: str,
    limit: int,
    *,
    best_per_partition: bool,
) -> list[dict[str, Any]]:
    """The longest runs of consecutive rows where ``hit`` holds - gaps and
    islands over games in Eastern-date order.

    Numbering every game and, separately, every game that hit, the difference
    between the two is constant along a run and changes whenever one ends, so
    grouping on it yields the runs. ``partition`` decides what a run may
    cross: a team's is (team, season), a player's is just him. With
    ``best_per_partition`` each partition contributes only its own longest, so
    a league-wide list is not one team's season listed five times.

    ``open`` marks a run still going at the partition's last game."""
    keys = ", ".join(partition)
    pick = "WHERE pick = 1" if best_per_partition else ""
    rows = con.execute(
        f"""
        WITH x AS ({base}),
        n AS (
            SELECT x.*, {hit} AS hit, ROW_NUMBER() OVER (PARTITION BY {keys} ORDER BY {_GAME_ORDER}) AS rn, COUNT(*) OVER (PARTITION BY {keys}) AS total FROM x
        ),
        h AS (SELECT n.*, rn - ROW_NUMBER() OVER (PARTITION BY {keys}, hit ORDER BY {_GAME_ORDER}) AS island FROM n),
        runs AS (
            SELECT {keys}, COUNT(*) AS length, MIN(day) AS first_day, MAX(day) AS last_day, MIN(season) AS first_season, MAX(season) AS last_season,
                   MAX(rn) = MAX(total) AS open
            FROM h WHERE hit GROUP BY {keys}, island
        ),
        ranked AS (SELECT runs.*, ROW_NUMBER() OVER (PARTITION BY {keys} ORDER BY length DESC, first_day) AS pick FROM runs)
        SELECT {keys}, length, first_day, last_day, first_season, last_season, open FROM ranked {pick} ORDER BY length DESC, first_day, {keys} LIMIT $limit""",
        {**params, "limit": limit},
    ).fetchall()
    names = [*partition, "length", "first_day", "last_day", "first_season", "last_season", "open"]
    return [dict(zip(names, row, strict=True)) for row in rows]


# ---------------- two players' meetings ----------------


def _meetings(con: duckdb.DuckDBPyConnection, scope: _Scope, a: str, b: str) -> tuple[list[dict[str, Any]], int]:
    """Games both players played on opposite teams, most recent first, and how
    many games they both played as teammates - the reason "never met" can be
    true of two players who shared a floor for years."""
    params = {**scope.params(), "a": a, "b": b}
    columns = "minutes, points, rebounds, assists, fieldGoalsMade, fieldGoalsAttempted"
    rows = con.execute(
        f"""
        WITH a AS ({_player_games(scope, "a")}), b AS ({_player_games(scope, "b")})
        SELECT a.day, a.won, a.team_score, a.opponent_score, a.team_id, b.team_id,
               {", ".join(f"a.{c}" for c in columns.split(", "))}, {", ".join(f"b.{c}" for c in columns.split(", "))}, a.season
        FROM a JOIN b ON b.event_id = a.event_id AND b.season = a.season AND b.team_id <> a.team_id
        ORDER BY a.day DESC, a.stamp DESC""",
        params,
    ).fetchall()
    together = con.execute(
        f"WITH a AS ({_player_games(scope, 'a')}), b AS ({_player_games(scope, 'b')}) SELECT COUNT(*) FROM a JOIN b ON b.event_id = a.event_id AND b.season = a.season AND b.team_id = a.team_id",
        params,
    ).fetchone()
    stats = [c.strip() for c in columns.split(",")]
    meetings = []
    for row in rows:
        meetings.append(
            {
                "day": row[0],
                "season": row[18],
                "won": bool(row[1]),
                "team_score": row[2],
                "opponent_score": row[3],
                "team_id": str(row[4]),
                "opponent_team_id": str(row[5]),
                "a": dict(zip(stats, row[6:12], strict=True)),
                "b": dict(zip(stats, row[12:18], strict=True)),
            }
        )
    return meetings, int(together[0]) if together else 0


def _unseen_meetings(con: duckdb.DuckDBPyConnection, scope: _Scope, a: str, b: str) -> int:
    """Games between the two players' teams with no box score, inside spells
    both were playing for those teams: meetings that may have happened and that
    no count can include. Either side's missing box hides a meeting, so the
    two players are matched to the game's two teams both ways round."""
    row = con.execute(
        f"""
        SELECT COUNT(DISTINCT m.event_id) FROM ({_box_missing(scope)}) m
        JOIN ({_spans(_player_games(scope, "a"))}) sa ON sa.season = m.season AND m.day BETWEEN sa.first_day AND sa.last_day
        JOIN ({_spans(_player_games(scope, "b"))}) sb ON sb.season = m.season AND m.day BETWEEN sb.first_day AND sb.last_day
        WHERE (sa.team_id = m.team_id AND sb.team_id = m.opponent_team_id) OR (sa.team_id = m.opponent_team_id AND sb.team_id = m.team_id)""",
        {**scope.params(), "a": a, "b": b},
    ).fetchone()
    return int(row[0]) if row else 0


def _matchup_line(lines: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """One player's averages over a set of meetings."""
    count = len(lines)
    fga = sum(line["fieldGoalsAttempted"] or 0 for line in lines)

    def average(key: str) -> float | None:
        """The per-game average of ``key`` over the meetings, or None if there were none."""
        return sum(line[key] or 0 for line in lines) / count if count else None

    return {
        "games": count,
        "minutes": average("minutes"),
        "points": average("points"),
        "rebounds": average("rebounds"),
        "assists": average("assists"),
        "fg_pct": 100.0 * sum(line["fieldGoalsMade"] or 0 for line in lines) / fga if fga else None,
    }
