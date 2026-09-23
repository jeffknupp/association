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

from association.nba.coverage import COVERAGE, POSTSEASON, REGULAR_SEASON
from association.nba.season import eastern_date_sql
from association.query.calendar import CalendarNarrowing, calendar_clause

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


#: A ``season_type`` value meaning "both the regular season and the
#: postseason at once" - a sentinel outside the two real values a game row
#: carries (``REGULAR_SEASON`` 2, ``POSTSEASON`` 3), never written to a
#: ``season_type`` column itself. Read the same way ``game_log`` already read
#: a "last N games" question naming no season type at all
#: (``router._route_game_log_recent_span``, ``season_type_unstated``), and
#: now also for a question that asks for both outright ("including the
#: playoffs", "regular season and playoffs") - see
#: ``router._BOTH_SEASON_TYPES_WORDS``. :func:`season_type_clause` is the one
#: place this turns into SQL; every other reader of the relation still sees a
#: real ``season_type`` (2 or 3) or this sentinel, never a third meaning.
#:
#: .. versionadded:: 4.4.0
BOTH_SEASON_TYPES: int = 0


def season_type_clause(column: str, season_type: int) -> tuple[str, list[Any]]:
    """SQL restricting ``column`` to one season type, or - for
    :data:`BOTH_SEASON_TYPES` - both of the two real ones, and its parameters.

    The one place a ``season_type`` value becomes SQL, so a new caller that
    wants to honor "including the playoffs" gets the sentinel right by
    construction rather than by copying an ``IN`` list.

    .. versionadded:: 4.4.0
    """
    if season_type == BOTH_SEASON_TYPES:
        return f"{column} IN ({REGULAR_SEASON}, {POSTSEASON})", []
    return f"{column} = ?", [season_type]


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
    #: Each box-score line the games were kept under or over, as the answer
    #: says it: ``"under 14 free throw attempts"``.
    measures: list[str] = field(default_factory=list)
    #: The game of a playoff series the question named ("game 4"), or None.
    series_game: int | None = None
    #: The window: the newest (``"recent"``) or oldest (``"first"``) N of the
    #: narrowed games, or None for all of them. Cut AFTER every row filter and
    #: BEFORE whatever a reader does with the rows, so "30-point games in his
    #: last 10" counts inside the ten and "his average over his last 5 vs
    #: Boston" averages the five Boston games - step 3's one rule that is
    #: about the skeleton rather than the rows.
    window: tuple[str, int] | None = None
    #: The calendar narrowing a ``situation`` slot named - a weekday, a month,
    #: a fixed day, every game from a day of the season on - or None.
    calendar: CalendarNarrowing | None = None

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

    def filters(self, *, dated: bool = True, windowed: bool = False) -> str:
        """What the games were narrowed to, as it follows a name: ``" vs the
        Detroit Pistons at home"``.

        ``windowed`` is opt-in and defaults off: ``.window`` is set by
        :func:`association.query.templates.common.scoped_games` for every
        caller whose slots carry ``order``/``limit`` - including ``game_log``
        and ``player_stat``, which read it for their OWN row-fetching
        (:func:`rows_sql` never consults ``.window``) and already say "last N
        games" their own way. Including the phrase here unconditionally would
        say it a second time in theirs. Only a caller that reads its rows
        through :func:`games_subquery`/:func:`aggregate_sql`/:func:`grouped_sql`
        - where ``.window`` is what actually cut the rows - has a reason to
        ask for it (step 3, C5's ``shot_chart``/``shot_distance``).

        .. versionchanged:: 4.4.0
           Takes ``windowed`` (default ``False``); the window phrase moved
           behind it.
        """
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
        if self.measures:
            parts.append(f"with {_joined(self.measures)}")
        if self.series_game is not None:
            # "of the series" only where one series is in view: an opponent
            # names it. Across a postseason it is game 4 of each series.
            parts.append(f"in game {self.series_game} of {'the' if self.opponent is not None else 'each'} series")
        if self.date and dated:
            parts.append(f"on {self.date}")
        if self.calendar is not None:
            parts.append(self.calendar.label)
        if self.window is not None and windowed:
            order, n = self.window
            parts.append(f"over his {'last' if order == 'recent' else 'first'} {n} game{'s' if n != 1 else ''}")
        return "".join(f" {part}" for part in parts)

    def narrow(self, clause: str, *params: Any) -> None:
        """One more clause over the same rows - how every narrowing composes."""
        self.extra.append(clause)
        self.extra_params.extend(params)

    def narrow_measure(self, column: str, op: str, value: Any, label: str | None = None) -> None:
        """A threshold on a box-score column: ``points >= 30``. ``op`` is one of
        :data:`MEASURE_OPS`; the column is a name from this module's own lists,
        never text from a question. A ``label`` is how the answer says it
        (``"under 14 free throw attempts"``); a threshold the caller words for
        itself passes none."""
        if op not in MEASURE_OPS:  # ops are written in code, so this is a programming error, not a refusal
            raise ValueError(f"no comparison called {op!r}")
        self.narrow(f"pgl.{column} {MEASURE_OPS[op]} ?", value)
        if label:
            self.measures.append(label)

    def narrow_calendar(self, narrowing: CalendarNarrowing) -> None:
        """Only the games on a weekday, in a month, on a fixed day, or from a
        day of the season on - the ``situation`` cell, read by
        :func:`association.query.calendar.parse_situation` and applied by
        :func:`association.query.templates.common.scoped_games`. The date is
        the game's US Eastern day, so "on Tuesdays" is the night the game was
        played, not ESPN's UTC stamp.

        .. versionadded:: 4.4.0
        """
        clause, params = calendar_clause(narrowing, eastern_date_sql("g.date"), "pgl.season")
        self.narrow(clause, *params)
        self.calendar = narrowing

    def narrow_series_game(self, n: int) -> None:
        """Only the ``n``th game of each playoff series: the games between the
        same two teams in one postseason, numbered by date over ``real_games``
        - the series' own games, so a game he sat out still counts toward the
        number, and his ``n``th game played is not mistaken for game ``n``."""
        self.narrow(f"pgl.event_id IN (SELECT event_id FROM ({_SERIES_GAMES}) WHERE game_of_series = ?)", n)
        self.series_game = n


# Every postseason game numbered within its series. A series is the games two
# teams play each other in one postseason; ``real_games`` rather than ``games``
# so a placeholder or a duplicated event does not shift the count.
_SERIES_GAMES = (
    "SELECT s.event_id, ROW_NUMBER() OVER (PARTITION BY s.season, LEAST(s.home_team_id, s.away_team_id), GREATEST(s.home_team_id, s.away_team_id) ORDER BY s.date, s.event_id) AS game_of_series "
    "FROM real_games s WHERE s.season_type = 3"
)

#: The comparisons :meth:`Narrowed.narrow_measure` accepts, mapped to SQL.
MEASURE_OPS: dict[str, str] = {">=": ">=", ">": ">", "<=": "<=", "<": "<", "=": "="}
"""``{">=": ">=", ...}`` - an allowlist, so no question text reaches SQL as an operator.

.. versionadded:: 4.3.0
"""


def league(season_clause: str, season_params: list[Any], season_type: int) -> Narrowed:
    """Every player's games in a span - the read a league-wide count or
    single-game high starts from. Same guards as one player's, without him.

    .. versionchanged:: 4.4.0
       Honors :data:`BOTH_SEASON_TYPES` through :func:`season_type_clause`.
    """
    type_clause, type_params = season_type_clause("pgl.season_type", season_type)
    return Narrowed(base=[type_clause, season_clause, "NOT pgl.did_not_play"], base_params=[*type_params, *season_params])


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


def _windowed(narrowed: Narrowed, *, rebuilt: bool) -> tuple[str, list[Any]]:
    """The FROM ... WHERE of a read: the relation under every row filter, and
    under the window too when one is set - as a subquery cut to the newest or
    oldest N by date, aliased so ``pgl.`` and ``g.`` still name the columns a
    reader wrote against."""
    where, params = narrowed.clauses(rebuilt=rebuilt)
    if narrowed.window is None:
        return f"{_PLAYER_GAMES} WHERE {where}", params
    order, n = narrowed.window
    direction = "DESC" if order == "recent" else "ASC"
    inner = f"SELECT pgl.* {_PLAYER_GAMES} WHERE {where} ORDER BY g.date {direction}, pgl.event_id LIMIT {int(n)}"
    # Re-expose the game columns under their alias so a reader's `g.` works.
    return f"FROM ({inner}) pgl JOIN games g ON g.event_id = pgl.event_id AND g.season = pgl.season", params


def aggregate_sql(narrowed: Narrowed, selects: list[str], *, rebuilt: bool = False) -> tuple[str, list[Any]]:
    """One row of aggregates over the narrowed games: averages, totals, a count,
    a record - over the window, where one is set.

    .. versionchanged:: 4.4.0
       Honors :attr:`Narrowed.window`.
    """
    source, params = _windowed(narrowed, rebuilt=rebuilt)
    return f"SELECT {', '.join(selects)} {source}", params


def games_subquery(narrowed: Narrowed, box: BoxSource) -> tuple[str, list[Any]]:
    """The narrowed games as a subquery with the result columns the condition
    readers wrap: ``won``, ``home_away``, ``team_score``, ``opponent_score``,
    the Eastern ``day`` and the raw ``stamp`` beside every ``pgl`` column.

    This is what :func:`association.query.conditions._player_games` renders
    for the templates that group a player's games by a condition (splits, a
    record above a threshold, a streak, with/without) - the same relation, the
    same guard, positional parameters instead of named ones so a
    :class:`Narrowed` can feed it. ``SELECT * REPLACE`` blanks the columns a
    rebuilt line cannot be trusted for, exactly as that reader does.

    .. versionadded:: 4.4.0

    .. versionchanged:: 4.4.0
       Honors :attr:`Narrowed.window` (step 3, C5), through the same
       :func:`_windowed` reader :func:`aggregate_sql`/:func:`grouped_sql`
       already use - a window is cut, with the played guard already applied,
       before this subquery's own columns are computed on top of it. A no-op
       for every caller that never sets ``window`` (``player_splits``,
       ``streak``, ``with_without``, ``record_when``): ``_windowed`` returns
       the identical ``FROM ... WHERE`` this function built by hand when
       ``window`` is ``None``.
    """
    source, params = _windowed(narrowed, rebuilt=box.rebuilt)
    ungated = [c for c in UNGATED_ON_REBUILD if c in box.columns]
    replacements = ", ".join(column("pgl", c, box) for c in ungated)
    blanked = f" REPLACE ({replacements})" if box.rebuilt and ungated else ""
    sql = f"""
        SELECT pgl.*{blanked}, g.date AS stamp, {eastern_date_sql("g.date")} AS day, g.winner_team_id = pgl.team_id AS won,
               CASE WHEN g.home_team_id = pgl.team_id THEN 'home' ELSE 'away' END AS home_away,
               CASE WHEN g.home_team_id = pgl.team_id THEN g.home_score ELSE g.away_score END AS team_score,
               CASE WHEN g.home_team_id = pgl.team_id THEN g.away_score ELSE g.home_score END AS opponent_score
        {source}"""
    return sql, params


def named(sql: str, params: list[Any], prefix: str = "r") -> tuple[str, dict[str, Any]]:
    """``sql`` with each positional ``?`` renamed ``$<prefix><n>``, and the
    parameters as that dict - for a reader that nests the same subquery more
    than once, or binds names of its own beside it. A ``?`` can appear only
    once in a statement; a name can be reused, and DuckDB will not mix the two
    in one statement. Only the relation's own SQL is rewritten, never a value.

    .. versionadded:: 4.4.0
    """
    parts = sql.split("?")
    if len(parts) - 1 != len(params):
        raise ValueError(f"{len(parts) - 1} placeholders for {len(params)} parameters")
    out = parts[0]
    for i, part in enumerate(parts[1:]):
        out += f"${prefix}{i}" + part
    return out, {f"{prefix}{i}": v for i, v in enumerate(params)}


def grouped_sql(
    narrowed: Narrowed, group_by: str, selects: list[str], *, having: str | None = None, order: str | None = None, limit: int | None = None, rebuilt: bool = False
) -> tuple[str, list[Any]]:
    """Aggregates per group - a ranking of players, a split by venue or month.
    A ``limit`` here is applied after grouping (the top N groups), never to
    the rows: "top 20 by average" and "the last 20 games, averaged" are
    different questions and only the second is a row window."""
    source, params = _windowed(narrowed, rebuilt=rebuilt)
    sql = f"SELECT {', '.join(selects)} {source} GROUP BY {group_by}"
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

    .. versionchanged:: 4.4.0
       Honors :data:`BOTH_SEASON_TYPES` through :func:`season_type_clause`.
    """
    type_clause, type_params = season_type_clause(f"{alias}.season_type", season_type)
    if season is None:
        return f"{alias}.season >= ? AND {type_clause}", [FLOOR, *type_params]
    return f"{alias}.season = ? AND {type_clause}", [season, *type_params]


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
