"""The ``team_game`` relation: one row per team per played game, read one way.

"A team's games" used to be defined three times, and they disagreed. The
correct one is the one this module adopts, built once as ``team_games`` (see
:data:`TEAM_GAMES_SQL`, moved here from :mod:`association.query.team_metrics`,
which re-exports it): from ``real_games`` alone, home and away sides unioned
into a team-perspective row, with two things every reader gets for free
because they live in the relation instead of in each caller:

- **the phantom 1993 season cannot double a game.** ``team_games``' own
  ``played`` step keeps one row per ``(season_type, home_team_id,
  away_team_id, eastern_date)`` - the highest ``season`` label when two agree
  - so a caller never needs its own ``season NOT IN (1993, ...)`` guard the
  way ``association.query.templates.games._season_games`` used to write one
  per call site (removed, step 3, C4b: its one caller, ``team_quarter_points``,
  now takes its games from this relation);
- **a postseason is selected by the calendar year it was played in**, from
  the relation's own :data:`Eastern date <association.nba.season.eastern_date_sql>`
  rather than a raw UTC timestamp - never by ESPN's pre-1993-94 label, which
  files a season under the year it STARTED (`AGENTS.md`, "Select a postseason
  by the calendar year"; :data:`association.query.team_metrics.games_scope`
  already read the relation this way, and now every reader can).

What this module does NOT decide is whether the NBA Cup final counts.
``team_games`` FLAGS it (``cup_final``) rather than dropping it, because a
regular-season RECORD must skip it while a plain game listing or a
head-to-head count should not - the cup final is a real game the two teams
played. :func:`association.query.team_metrics.games_scope` applies the
exclusion for a record; :func:`association.query.templates.common.team_games`
(the shared narrowing step) does not, since a team's plain game list or a
head-to-head count is not a record.

:class:`TeamNarrowed` mirrors :class:`association.query.player_games.Narrowed`:
base clauses (team, season type, span) kept apart from the rest so an empty
answer can say which narrowing emptied it, and the same four readers
(:func:`rows_sql`, :func:`aggregate_sql`, :func:`games_subquery`,
:func:`named`) over the one relation. Entity binding is not here, the same
rule the player relation follows: this module takes ids the entity layer has
already resolved, never a name.

The old ``conditions._team_games``, which scoped by season LABEL rather than
calendar year (the wrong-year fault this module's docstring above describes),
is gone: ``player_splits``, ``record_when`` and ``streak``'s team branches and
``with_without``'s windows were ported onto this relation in step 3, C4, and
``head_to_head`` and ``game_log``'s team half in the same step. Every reader of
a team's games now goes through here.

Step 3, C4b adds the cells a team's games can be narrowed to that C4 left off:
:attr:`TeamNarrowed.window` (the newest or oldest N of the narrowed games, the
team counterpart of :attr:`association.query.player_games.Narrowed.window`)
and :attr:`TeamNarrowed.series_game` (one game of each playoff series, the
team counterpart of :attr:`association.query.player_games.Narrowed.series_game`).
A team's ``since`` (a career that starts partway through) needed no new cell
here at all: :func:`association.query.templates.common._span_of` already
reads it into the ``_Span`` a caller passes as ``team_games``'s own ``span``,
so the relation's ``team_games`` CTE and :func:`association.query.templates.common._team_span_clause`
narrow by it the same way they already narrow a career.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from association.nba.season import eastern_date_sql

from .entities import Entity

# One row per team per game, from that team's side, over every game that was
# actually played - built from `real_games` alone, because it is the only
# table holding who won and it needs no second table to say which side a team
# was on.
#
# `real_games` (fetch/repairs/real_games.py) is the shared filtered list, and
# it is where the 0-0 placeholders, the rows naming a team id no franchise
# has, and the same-day duplicates now go. This used to do two of those three
# for itself: it dropped rows with no winner and same-day duplicates, and kept
# every phantom that carried a winner - which is how the 1995 Finals game
# filed as MIA-ORL gave Orlando an 11th playoff loss and Miami a postseason it
# never played. Building it here meant `conditions` and `head_to_head` each
# had to reach the same conclusion separately, and neither did.
#
# What is left here is the ONE thing `real_games` deliberately does not do:
# collapse season 1993, which is a full copy of 1994 under a second label
# (see coverage.COVERAGE). That is a phantom SEASON rather than a phantom row,
# its two copies differ only in the `season` column, and a postseason is
# selected by the year it was PLAYED, so nothing else would drop the second
# copy. The 1994 label is the one kept - and every reader that groups by
# `(season_type, home, away, eastern_date)` gets this dedup for free, without
# writing its own `season NOT IN (...)` guard.
#
# The NBA Cup final is FLAGGED rather than dropped: it is a
#   season_type 2 game that counts in no standings and no team season totals
#   (the 2026 Knicks' 82 games and 9,549 points in both leave out their 124 in
#   the final), so a regular-season record must skip it, but a record against
#   the team they beat in it should still mention it. It is identified as the
#   last neutral-site regular-season game in Las Vegas each season, which
#   picks out exactly the two teams per season (2024-2026) that `games` holds
#   83 regular-season games for and standings 82.
#
# A game's date is its US Eastern date - games.date is a UTC timestamp, read
# through season.eastern_date_sql, which follows daylight time.
_TEAM_GAMES = f"""
WITH cup_finals AS (
    SELECT arg_max(event_id, date) AS event_id
    FROM real_games
    WHERE season_type = 2 AND neutral_site AND venue_city = 'Las Vegas'
    GROUP BY season
),
listed AS (
    SELECT g.event_id, g.season, g.season_type, g.home_team_id, g.away_team_id, g.home_score, g.away_score, g.winner_team_id,
           coalesce(g.neutral_site, false) AS neutral,
           {eastern_date_sql("g.date")} AS eastern_date,
           g.event_id IN (SELECT event_id FROM cup_finals) AS cup_final
    FROM real_games g
),
played AS (
    SELECT * FROM listed
    QUALIFY row_number() OVER (PARTITION BY season_type, home_team_id, away_team_id, eastern_date ORDER BY season DESC, event_id) = 1
),
team_games AS (
    SELECT season, season_type, eastern_date, event_id, cup_final, neutral, home_team_id AS team_id, away_team_id AS opponent_id, 'home' AS side,
           home_score AS team_score, away_score AS opponent_score, winner_team_id = home_team_id AS won
    FROM played
    UNION ALL
    SELECT season, season_type, eastern_date, event_id, cup_final, neutral, away_team_id AS team_id, home_team_id AS opponent_id, 'away' AS side,
           away_score AS team_score, home_score AS opponent_score, winner_team_id = away_team_id AS won
    FROM played
)
"""

TEAM_GAMES_SQL = _TEAM_GAMES
"""A ``WITH`` clause defining ``team_games``: one row per team per played
game, with ``season``, ``season_type``, ``eastern_date``, ``event_id``,
``cup_final``, ``neutral``, ``team_id``, ``opponent_id``, ``side`` (home/away),
``team_score``, ``opponent_score`` and ``won``.

.. versionadded:: 2.1.0
.. versionchanged:: 4.4.0
   Moved here from :mod:`association.query.team_metrics`, which imports it
   back under the same name - the relation's own readers
   (:func:`rows_sql`, :func:`aggregate_sql`, :func:`games_subquery`) read it as
   ``_TEAM_GAMES``, and every other caller keeps reading it under this name.
"""


@dataclass
class TeamNarrowed:
    """One team's games in a span, and whatever the question narrowed them
    by. Every clause applies to :data:`TEAM_GAMES_SQL`'s ``team_games``;
    the base ones (team, season type, span) are kept apart from the rest so an
    empty answer can say which narrowing emptied it - the team counterpart of
    :class:`association.query.player_games.Narrowed`.

    .. versionadded:: 4.4.0
    """

    base: list[str]
    base_params: list[Any]
    extra: list[str] = field(default_factory=list)
    extra_params: list[Any] = field(default_factory=list)
    team: Entity | None = None
    opponent: Entity | None = None
    venue: str | None = None
    #: The one Eastern date the games were narrowed to, as the answer says it.
    date: str | None = None
    #: The game of a playoff series the question named ("game 4"), or None -
    #: the team counterpart of :attr:`association.query.player_games.Narrowed.series_game`.
    series_game: int | None = None
    #: The window: the newest (``"recent"``) or oldest (``"first"``) N of the
    #: narrowed games, or None for all of them - cut AFTER every row filter,
    #: the team counterpart of :attr:`association.query.player_games.Narrowed.window`.
    window: tuple[str, int] | None = None

    def clauses(self, *, narrowed: bool = True) -> tuple[str, list[Any]]:
        """The WHERE body and its parameters - without the narrowing when
        ``narrowed`` is false, so a caller can ask how many games the base
        span alone holds, before an opponent, a venue or a date is applied."""
        where = list(self.base)
        params = list(self.base_params)
        if narrowed:
            where += self.extra
            params += self.extra_params
        return " AND ".join(where), params

    def filters(self, *, opponent: bool = True, date: bool = True) -> str:
        """What the games were narrowed to, as it follows a name: ``" vs the
        Detroit Pistons at home"``. ``opponent`` is False for a caller that
        already names the opponent its own way (``team_quarter_points`` says
        "against the Pistons", not "vs the Pistons") and wants the rest of the
        narrowing without a second, differently-worded mention of it. ``date``
        is False the same way, for a caller whose sentence already names the
        one game's date its own way (a single-game answer that already prints
        ``g['date']``) and would otherwise say it twice."""
        parts = []
        if self.opponent is not None and opponent:
            parts.append(f"vs the {self.opponent.name}")
        if self.venue:
            parts.append("at home" if self.venue == "home" else "on the road")
        if self.series_game is not None:
            # "of the series" only where one series is in view: an opponent
            # names it. Across a postseason it is game 4 of each series.
            parts.append(f"in game {self.series_game} of {'the' if self.opponent is not None else 'each'} series")
        if self.date and date:
            parts.append(f"on {self.date}")
        if self.window is not None:
            order, n = self.window
            parts.append(f"over their {'last' if order == 'recent' else 'first'} {n} game{'s' if n != 1 else ''}")
        return "".join(f" {part}" for part in parts)

    def narrow(self, clause: str, *params: Any) -> None:
        """One more clause over the same rows - how every narrowing composes."""
        self.extra.append(clause)
        self.extra_params.extend(params)

    def narrow_series_game(self, n: int) -> None:
        """Only the ``n``th game of each playoff series: the games between the
        same two teams in one postseason, numbered by date over ``real_games``
        - the team counterpart of :meth:`association.query.player_games.Narrowed.narrow_series_game`.

        .. versionadded:: 4.4.0
        """
        self.narrow(f"tg.event_id IN (SELECT event_id FROM ({_TEAM_SERIES_GAMES}) WHERE game_of_series = ?)", n)
        self.series_game = n


# Every postseason game numbered within its series, over the two teams that
# played it - the team relation's own copy of
# :data:`association.query.player_games._SERIES_GAMES`. Kept separate rather
# than imported, the same way :func:`named` is its own copy: the two relations
# share no base class, on purpose, so a change to one's clause-composition
# rules cannot silently reach the other.
_TEAM_SERIES_GAMES = (
    "SELECT s.event_id, ROW_NUMBER() OVER (PARTITION BY s.season, LEAST(s.home_team_id, s.away_team_id), GREATEST(s.home_team_id, s.away_team_id) ORDER BY s.date, s.event_id) AS game_of_series "
    "FROM real_games s WHERE s.season_type = 3"
)


def _windowed(narrowed: TeamNarrowed, *, join: str = "") -> tuple[str, list[Any]]:
    """The FROM ... WHERE of a read: the relation under every row filter, and
    under the window too when one is set - as a subquery cut to the newest or
    oldest N by Eastern date, aliased so ``tg.`` still names the columns a
    reader wrote against. ``join`` is any extra table a caller's own SELECT
    needs (see :func:`rows_sql`); it always follows ``tg`` - inside the window
    it has nothing left to filter, since the window has already cut the rows.

    The team counterpart of :func:`association.query.player_games._windowed`.

    .. versionadded:: 4.4.0
    """
    where, params = narrowed.clauses()
    if narrowed.window is None:
        return f"FROM team_games tg{join} WHERE {where}", params
    order, n = narrowed.window
    direction = "DESC" if order == "recent" else "ASC"
    inner = f"SELECT tg.* FROM team_games tg WHERE {where} ORDER BY tg.eastern_date {direction}, tg.event_id LIMIT {int(n)}"
    return f"FROM ({inner}) tg{join}", params


def rows_sql(narrowed: TeamNarrowed, select: str, *, order: str, limit: int | None = None, join: str = "") -> tuple[str, list[Any]]:
    """The rows themselves - a team's game log, a season's games tallied by
    month. ``select`` and ``order`` are column expressions written in code.

    ``join`` is any extra ``FROM`` clause a caller's own ``select`` needs -
    typically ``" JOIN teams o ON o.team_id = tg.opponent_id"``, for the
    opponent's own-season name (:func:`association.nba.franchises.season_name_sql`)
    - since the base FROM is always ``team_games tg`` alone.

    .. versionchanged:: 4.4.0
       Honors :attr:`TeamNarrowed.window`.
    """
    source, params = _windowed(narrowed, join=join)
    sql = f"{_TEAM_GAMES} SELECT {select} {source} ORDER BY {order}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return sql, params


def aggregate_sql(narrowed: TeamNarrowed, selects: list[str], *, join: str = "") -> tuple[str, list[Any]]:
    """One row of aggregates over the narrowed games: a count, a tally, a
    sum of points. See :func:`rows_sql` for ``join``.

    .. versionchanged:: 4.4.0
       Honors :attr:`TeamNarrowed.window`.
    """
    source, params = _windowed(narrowed, join=join)
    return f"{_TEAM_GAMES} SELECT {', '.join(selects)} {source}", params


def games_subquery(narrowed: TeamNarrowed, *, join: str = "") -> tuple[str, list[Any]]:
    """The narrowed games as a subquery holding every ``team_games`` column -
    what a reader that groups a team's games by a condition (a split, a
    streak, a with/without window) wraps. See :func:`rows_sql` for ``join``.

    .. versionchanged:: 4.4.0
       Honors :attr:`TeamNarrowed.window`.
    """
    source, params = _windowed(narrowed, join=join)
    return f"{_TEAM_GAMES} SELECT tg.* {source}", params


def named(sql: str, params: list[Any], prefix: str = "r") -> tuple[str, dict[str, Any]]:
    """``sql`` with each positional ``?`` renamed ``$<prefix><n>``, and the
    parameters as that dict - for a reader that nests the same subquery more
    than once, or binds names of its own beside it. A ``?`` can appear only
    once in a statement; a name can be reused, and DuckDB will not mix the two
    in one statement. Only the relation's own SQL is rewritten, never a value.

    The player relation's own :func:`association.query.player_games.named` in
    every particular but the module it lives in - kept as a separate copy
    rather than a shared import, the same way :class:`TeamNarrowed` is its own
    dataclass rather than a subclass of
    :class:`association.query.player_games.Narrowed`: the two relations share
    no base class, on purpose, so a change to one's clause-composition rules
    cannot silently reach the other.

    .. versionadded:: 4.4.0
    """
    parts = sql.split("?")
    if len(parts) - 1 != len(params):
        raise ValueError(f"{len(parts) - 1} placeholders for {len(params)} parameters")
    out = parts[0]
    for i, part in enumerate(parts[1:]):
        out += f"${prefix}{i}" + part
    return out, {f"{prefix}{i}": v for i, v in enumerate(params)}
