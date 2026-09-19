"""The ``player_game`` relation: one row per player per game, read one way.

Every template that answers from box scores used to write its own FROM, its
own "did he play" guard and its own season clause - three definitions of the
same relation that had already drifted (one read ``real_games``, two read
``games`` with the phantom excluded by name; one blanked the columns a rebuilt
line cannot be trusted for, the others never read them). This module is the
one definition. The rules it carries are the ones `DATA.md` and `AGENTS.md`
record, applied on every read:

- **the phantom 1993 season is excluded by name** in a career span, and every
  join to ``games`` is keyed on ``season`` as well as ``event_id`` (the 1993
  label shares its event ids with 1994);
- **a row is a game played** only when it is not a did-not-play entry and
  carries minutes - or a line rebuilt from play-by-play, where every column
  read is one a rebuild gets right (:data:`REBUILT_STATS`);
- **a teammate's absence** is measured inside the teammate's own tenure on
  the team (:func:`_tenure_clause`), never over games he was somewhere else.

One kind of read deliberately steps outside the guard: counting the games it
drops. An answer built from box scores says how many games it could not see
(the empty 2013-18 Bulls and Pelicans box scores, a rebuilt line held back
for a stat the rebuild gets wrong), and that count has to include exactly the
rows the guard excludes. :func:`scope_without_guard` is the span clause for
those reads, and its name is the reminder. The team-level counterpart - a
team-game with no box score at all, inside a spell a player was on the team -
is a read of ``team_box_stats``, not of this relation, and lives beside the
other team reads in :mod:`association.query.conditions` (``_box_missing``).

Narrowing composes: an opponent, a venue, a starter/bench half, a teammate's
absence, a threshold on a stat, a month - each is one more clause over the
same rows, so a new one reaches every template that reads the relation at
once. Entity binding is not here: this module takes ids the entity layer has
already resolved, and never a name.

.. versionadded:: 4.3.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import duckdb

from association.nba.coverage import COVERAGE

from .conditions import UNGATED_ON_REBUILD, BoxSource
from .entities import Entity

# A player-game ESPN lists as played but records no minutes for. Every such row
# in the warehouse carries no stats either (checked, 1994-2026), and they are of
# two kinds. In 2006-2012 they are ~10,000 a season of appearances nobody made,
# which ESPN's own games-played counts mostly leave out (dropping them makes 378
# of 445 players' 2009 counts agree, against 30). In 2013-2018 they are whole
# team box scores ESPN left empty - ~13% of team-games, which is why summing
# those seasons' box scores gives 87% of the season totals. Averaged in, either
# kind reads as a game of zeros, so they are left out and the answer says how
# many.
FLOOR: int = COVERAGE["player_box_stats"].first_season
"""The first season with box scores (1994); a career of them starts here.

.. versionadded:: 4.3.0
"""

_RECORDED = "pgl.minutes IS NOT NULL"


#: The stats a rebuilt box line may be read for, and the reason the list is
#: short.
#:
#: Where ESPN serves an empty box score, ``player_box_stats_filled`` carries a
#: line rebuilt from play-by-play (see
#: :mod:`association.fetch.repairs.reconstructed_box`). Measured per player-game against
#: the 22,646 games of the 2015 regular season whose real box score survived,
#: the mean absolute error of a rebuilt figure is:
#:
#: ===================== ========= ==============
#: Stat                  Exact     Mean abs error
#: ===================== ========= ==============
#: ``freeThrowsMade``    100.0%    0.0003
#: ``blocks``            99.8%     0.002
#: ``rebounds``          99.6%     0.004
#: ``assists``           99.6%     0.004
#: ``fieldGoalsMade``    99.6%     0.005
#: ``steals``            99.1%     0.009
#: ``points``            98.3%     0.021
#: ``turnovers``         92.5%     0.080
#: ``fouls``             83.3%     0.181
#: ===================== ========= ==============
#:
#: ``turnovers`` and ``fouls`` are left out: an order of magnitude worse than
#: the rest, and a foul is wrong in one game in six. The rest are wrong by
#: hundredths of a point per game, which is why this is a PER-GAME list only.
#:
#: **A rebuilt SEASON total is a different matter and is not read anywhere.**
#: A season is exact only when the net error over every game is zero, so it
#: lands exactly right about half the time - and the error scales with games
#: played: over the Chicago and New Orleans player-seasons, a total that is
#: right averages 31.6 games and one that is wrong averages 55.6. 2016 is worse
#: still (18.9% exact, mean -16.8 points) because 180 of its scoring plays carry
#: ``type = 'Not Available'``.
#:
#: .. versionadded:: 2.2.0
REBUILT_STATS: frozenset[str] = frozenset({"points", "rebounds", "assists", "steals", "blocks", "fieldGoalsMade", "freeThrowsMade"})


# A rebuilt line has NULL minutes - play-by-play cannot recover them - so
# `_RECORDED` hides it from every reader by default. This is the opt-in.
_RECORDED_OR_REBUILT = "(pgl.minutes IS NOT NULL OR pgl.reconstructed)"


def _log_carries_rebuilt(con: duckdb.DuckDBPyConnection) -> bool:
    """Whether ``player_game_log`` has the ``reconstructed`` flag.

    Checked rather than assumed, for the reason `AGENTS.md` records under "A
    warehouse built before a view change is not detected": the column arrives
    with a `data load`, and a query written as though it were always there
    raises a Binder error against any older warehouse. Fixtures that build a
    minimal log get the same answer, and keep their old behavior.
    """
    try:
        return any(row[0] == "reconstructed" for row in con.execute("DESCRIBE player_game_log").fetchall())
    except duckdb.CatalogException:
        return False


# One join serves venue and result both: games.home_team_id agrees with
# team_box_stats.home_away on every row (checked, all 87,008 as of the warehouse
# this was last verified against - the count grows with every pull). Keyed on
# season too, since the phantom 1993 shares its event ids with 1994.
_PLAYER_GAMES = "FROM player_game_log pgl JOIN games g ON g.event_id = pgl.event_id AND g.season = pgl.season"


def _teammate_played(box: BoxSource) -> str:
    """SQL for "this teammate played that game", over the given box source.

    A game rebuilt from play-by-play has no minutes, so against the stored
    table every teammate in a rebuilt game reads as absent and the game counts
    as one played "without" him. Measured before this took a source: "Anthony
    Davis without Eric Gordon, 2015" listed games Gordon played in - he played
    48 of Davis's 68 that season.

    .. versionadded:: 2.2.0
    """
    appeared = "(m.minutes IS NOT NULL OR m.reconstructed)" if box.rebuilt else "m.minutes IS NOT NULL"
    return f"EXISTS (SELECT 1 FROM {box.table} m WHERE m.athlete_id = ? AND m.event_id = pgl.event_id AND m.season = pgl.season AND NOT m.did_not_play AND {appeared})"


# An open end of a stint, as a string that sorts before or after any date.
_OPEN_START, _OPEN_END = "0000", "9999"


def _joined(names: list[str], word: str = "and") -> str:
    """ "A", "A and B", "A, B and C" - a list of names as a sentence holds it."""
    if len(names) <= 1:
        return names[0] if names else ""
    return f"{', '.join(names[:-1])} {word} {names[-1]}"


@dataclass
class Narrowed:
    """One player's games in a span, and whatever the question narrowed them
    by. Every clause applies to ``_PLAYER_GAMES``; the base ones (player, season
    type, span, played) are kept apart from the rest so an empty answer can say
    which narrowing emptied it."""

    base: list[str]
    base_params: list[Any]
    extra: list[str] = field(default_factory=list)
    extra_params: list[Any] = field(default_factory=list)
    opponent: Entity | None = None
    venue: str | None = None
    #: True for a log of starts, False for one off the bench, None when the
    #: question named neither half.
    started: bool | None = None
    # Every teammate the question named, with that teammate's tenure clause
    # beside it. All of them at once: a game is "without" them only when none
    # of them played it, so a dropped name would answer a wider question.
    without: list[Entity] = field(default_factory=list)
    tenure: list[tuple[str, list[Any]]] = field(default_factory=list)
    date: str | None = None

    def clauses(self, *, narrowed: bool = True, recorded: bool = True, rebuilt: bool = False) -> tuple[str, list[Any]]:
        """The WHERE body and its parameters - without the narrowing when
        ``narrowed`` is false, and over the empty lines when ``recorded`` is.

        ``rebuilt`` widens what counts as a game he played to include a line
        rebuilt from play-by-play. The NEGATION uses the same widened guard, so
        "games not counted" stays the complement of "games counted" - otherwise
        a rebuilt game would be both listed and reported as skipped.
        """
        guard = _RECORDED_OR_REBUILT if rebuilt else _RECORDED
        where = [*self.base, guard if recorded else f"NOT ({guard})"]
        params = list(self.base_params)
        if narrowed:
            where += self.extra
            params += self.extra_params
        return " AND ".join(where), params

    def filters(self, *, dated: bool = True) -> str:
        """What the games were narrowed to, as it follows a name: ``" vs the
        Detroit Pistons at home"``."""
        parts = []
        if self.opponent is not None:
            parts.append(f"vs the {self.opponent.name}")
        if self.venue:
            parts.append("at home" if self.venue == "home" else "on the road")
        if self.started is not None:
            # Said outright, like every other narrowing here. A log of 50
            # starts headed only "last 50 games" is the silent narrowing this
            # module exists to stop - it reads as his last 50 games played.
            parts.append("as a starter" if self.started else "off the bench")
        if self.without:
            parts.append(f"without {_joined([mate.name for mate in self.without])}")
        if self.date and dated:
            parts.append(f"on {self.date}")
        return "".join(f" {part}" for part in parts)

    def narrow(self, clause: str, *params: Any) -> None:
        """One more clause over the same rows - how every narrowing composes."""
        self.extra.append(clause)
        self.extra_params.extend(params)

    def narrow_measure(self, column: str, op: str, value: Any) -> None:
        """A threshold on a box-score column: ``points >= 30``. ``op`` is one of
        :data:`MEASURE_OPS`; the column is a name from this module's own lists,
        never text from a question."""
        if op not in MEASURE_OPS:  # ops are written in code, so this is a programming error, not a refusal
            raise ValueError(f"no comparison called {op!r}")
        self.narrow(f"pgl.{column} {MEASURE_OPS[op]} ?", value)


#: The comparisons :meth:`Narrowed.narrow_measure` accepts, mapped to SQL.
MEASURE_OPS: dict[str, str] = {">=": ">=", ">": ">", "<=": "<=", "<": "<", "=": "="}
"""``{">=": ">=", ...}`` - an allowlist, so no question text reaches SQL as an operator.

.. versionadded:: 4.3.0
"""


def league(season_clause: str, season_params: list[Any], season_type: int) -> Narrowed:
    """Every player's games in a span - the read a league-wide count or
    single-game high starts from. Same guards as one player's, without him."""
    return Narrowed(base=["pgl.season_type = ?", season_clause, "NOT pgl.did_not_play"], base_params=[season_type, *season_params])


def rows_sql(narrowed: Narrowed, select: str, *, order: str, limit: int | None = None, offset: int = 0, rebuilt: bool = False) -> tuple[str, list[Any]]:
    """The rows themselves - a log, a single game, the top game by a stat.
    ``select`` and ``order`` are column expressions written in code."""
    where, params = narrowed.clauses(rebuilt=rebuilt)
    sql = f"SELECT {select} {_PLAYER_GAMES} WHERE {where} ORDER BY {order}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    if offset:
        sql += f" OFFSET {int(offset)}"
    return sql, params


def aggregate_sql(narrowed: Narrowed, selects: list[str], *, rebuilt: bool = False) -> tuple[str, list[Any]]:
    """One row of aggregates over the narrowed games: averages, totals, a count, a record."""
    where, params = narrowed.clauses(rebuilt=rebuilt)
    return f"SELECT {', '.join(selects)} {_PLAYER_GAMES} WHERE {where}", params


def grouped_sql(
    narrowed: Narrowed, group_by: str, selects: list[str], *, having: str | None = None, order: str | None = None, limit: int | None = None, rebuilt: bool = False
) -> tuple[str, list[Any]]:
    """Aggregates per group - a ranking of players, a split by venue or month.
    A ``limit`` here is applied after grouping (the top N groups), never to
    the rows: "top 20 by average" and "the last 20 games, averaged" are
    different questions and only the second is a row window."""
    where, params = narrowed.clauses(rebuilt=rebuilt)
    sql = f"SELECT {', '.join(selects)} {_PLAYER_GAMES} WHERE {where} GROUP BY {group_by}"
    if having:
        sql += f" HAVING {having}"
    if order:
        sql += f" ORDER BY {order}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return sql, params


#: The two halves of the starter/bench split, as `router._split_side` narrows
#: them when a question names one. ``starter_bench`` itself is NOT here: that is
#: the category, and a question naming both halves is asking for a splits table
#: rather than a filtered set of games.
STARTER_SIDES: dict[str, bool] = {"starter": True, "bench": False}
"""Which value of ``player_game_log.starter`` each named half of the split means.

.. versionadded:: 4.3.0
"""


def column(alias: str, name: str, box: BoxSource) -> str:
    """A box-score column as a reader may trust it: blanked on a rebuilt row
    when it is one the rebuild fills but was never measured for
    (:data:`association.query.conditions.UNGATED_ON_REBUILD`), so an average is
    taken over the games that carry the figure rather than over a wrong one."""
    if box.rebuilt and name in UNGATED_ON_REBUILD and name in box.columns:
        return f"CASE WHEN {alias}.reconstructed THEN NULL ELSE {alias}.{name} END AS {name}"
    return f"{alias}.{name}"


def paired_rows_sql(narrowed: Narrowed, other_id: str, select: str, *, teammates: bool = False, order: str | None = "g.date DESC, pgl.event_id", rebuilt: bool = False) -> tuple[str, list[Any]]:
    """The pair relation: games the narrowed player and ``other`` both played,
    on opposite teams - or the same, with ``teammates`` - with the other's line
    readable as ``other.<column>``. A matchup is this and nothing more: two
    reads of the relation joined on the event, both under the played guard,
    which is why "never met" can be true of two men who shared a floor for
    years (their games are teammates' games, not meetings)."""
    where, params = narrowed.clauses(rebuilt=rebuilt)
    appeared = "(other.minutes IS NOT NULL OR other.reconstructed)" if rebuilt else "other.minutes IS NOT NULL"
    side = "other.team_id = pgl.team_id" if teammates else "other.team_id <> pgl.team_id"
    sql = (
        f"SELECT {select} {_PLAYER_GAMES} JOIN player_game_log other ON other.event_id = pgl.event_id AND other.season = pgl.season "
        f"AND other.athlete_id = ? AND {side} AND NOT other.did_not_play AND {appeared} WHERE {where}"
    )
    if order:
        sql += f" ORDER BY {order}"
    return sql, [other_id, *params]


def scope_without_guard(alias: str, season: int | None, season_type: int) -> tuple[str, list[Any]]:
    """The WHERE clause for one season of box scores, or for a career of them,
    with NO played guard - for counting the rows the guard drops, never for
    answering from them.

    A career starts at the box scores' floor, and that floor is also what keeps
    1993 out: ESPN answers season=1993 with the same games as 1994 (coverage's
    phantom), so a career counted from 1993 counts every 1993-94 game twice -
    26,350 duplicate player-games.
    """
    if season is None:
        return f"{alias}.season >= ? AND {alias}.season_type = ?", [FLOOR, season_type]
    return f"{alias}.season = ? AND {alias}.season_type = ?", [season, season_type]


def _teammate_stints(con: duckdb.DuckDBPyConnection, athlete_id: str) -> list[tuple[int, str, str, str]]:
    """When a player was on each team: ``(season, team_id, start, end)``, with
    ``start``/``end`` compared against ``games.date``.

    There is no roster table, so this is read off his own box-score rows, and
    the one fact that makes that hard is that an injured player mostly has NO
    row: Stephen Curry's 2026 is 43 rows, none of them did-not-play, for an
    82-game Warriors season. So a stint runs from his first row for a team to
    his last, and then:

    - it is open at the start of the season when he ended the previous one on
      that team (or has no earlier rows at all) - LeBron James's first 2026 row
      is 2025-11-19, and the Lakers' 14 games before it were played without
      him;
    - it is open at the end when no later row that season is for another team,
      so an injury that ends a season still counts.

    A mid-season arrival from another team is not extended backwards: Seth
    Curry's first 2026 Warriors row is 2025-12-03, and their October games were
    not played "without" somebody who was in Charlotte's plans. The gap between
    a traded player's last game for one team and his first for the next belongs
    to neither, which undercounts rather than guesses."""
    phantom = COVERAGE["player_game_log"].phantom
    excluded = f" AND season NOT IN ({', '.join('?' for _ in phantom)})" if phantom else ""
    rows = con.execute(
        f"SELECT season, team_id, MIN(game_date), MAX(game_date) FROM player_game_log WHERE athlete_id = ?{excluded} GROUP BY season, team_id ORDER BY season, MIN(game_date)",
        [athlete_id, *phantom],
    ).fetchall()
    by_season: dict[int, list[tuple[str, str, str]]] = {}
    for season, team_id, first, last in rows:
        by_season.setdefault(int(season), []).append((str(team_id), str(first), str(last)))
    stints: list[tuple[int, str, str, str]] = []
    carried: str | None = None  # the team he ended the previous season on
    for season in sorted(by_season):
        spells = by_season[season]
        final = max(range(len(spells)), key=lambda i: spells[i][2])
        for index, (team_id, first, last) in enumerate(spells):
            start = _OPEN_START if index == 0 and carried in (None, team_id) else first
            end = _OPEN_END if index == final else last
            stints.append((season, team_id, start, end))
        carried = spells[final][0]
    return stints


def _tenure_clause(con: duckdb.DuckDBPyConnection, mate: Entity, season: int | None) -> tuple[str, list[Any]]:
    """SQL keeping the games ``pgl`` played on a team ``mate`` was on at the
    time - see _teammate_stints for how "was on" is read. ``season`` is the one
    season asked about, or None for a career."""
    stints = [s for s in _teammate_stints(con, mate.id) if season is None or s[0] == season]
    if not stints:
        return "FALSE", []
    rows = ", ".join("(?, ?, ?, ?)" for _ in stints)
    return (
        f"EXISTS (SELECT 1 FROM (VALUES {rows}) AS stint(season, team_id, start_date, end_date) "
        "WHERE stint.season = pgl.season AND stint.team_id = pgl.team_id AND pgl.game_date BETWEEN stint.start_date AND stint.end_date)"
    ), [value for stint in stints for value in stint]
