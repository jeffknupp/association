"""The shot relation: one player's located shots, ``shot_chart``, and every
statement read over them - built here and executed through the compiler's
one door (:func:`~association.query.compose.core.values_of`), as the season
line's are (:mod:`association.query.season_line`). Phase 4, step 5
(``ROADMAP.md``, "Every statement is a relation's"): the three statements
the shot relation's reader ran (:mod:`association.query.compose.shots`: the
games a narrowing sends a shot read to, a player's average shot distance,
the shots a chart draws) moved here whole, text-identical, with the
parameters each took. The reader keeps its words and the chart its drawing
(``compose.shots.draw_shot_chart``), and runs no statement of its own. Not
every read of ``shot_chart`` is this relation's: the period relations
rebuild a quarter's points from it (:mod:`association.query.player_games`,
:mod:`association.query.team_games`), a join inside their own statements,
and the name resolver reads who has a row (``entities.narrow_to_available``).

What a shot is worth is read by :data:`~association.query.shotchart.SHOT_VALUE_SQL`,
never ``points_attempted`` (0 there means unlabeled, not zero points;
``DATA.md``), and where it is on the floor by the court's own geometry
(:data:`~association.query.court.HAS_POSITION_SQL`,
:data:`~association.query.court.SHOT_DISTANCE_SQL`, with the rim at
``(25, 0)``). Both fragments stay where they are: the value is read by the
player-games relation's period rebuild too
(:mod:`association.query.player_games`) and its per-season refusals and
caveats by the sayer, and the court's fragments are the drawing's geometry
- a fragment is no statement, and one definition each is the rule.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import duckdb

from association.query.conditions import BoxSource
from association.query.court import HAS_POSITION_SQL, SHOT_DISTANCE_SQL
from association.query.player_games import Narrowed, games_subquery
from association.query.player_relation import ResolvedSpan
from association.query.season_line import Statement
from association.query.shotchart import SHOT_VALUE_SQL


def _values(con: duckdb.DuckDBPyConnection, statement: Statement) -> list[tuple[Any, ...]]:
    """``statement`` run through the compiler's one door
    (:func:`~association.query.compose.core.values_of`), for every read this
    relation makes. At call time: the compiler imports the reader that
    imports this module."""
    from association.query.compose.core import values_of

    return values_of(con, statement)


def narrowed_games(con: duckdb.DuckDBPyConnection, narrowed: Narrowed, box: BoxSource) -> list[tuple[Any, ...]]:
    """The games an already-narrowed-and-windowed ``narrowed`` draws a shot
    read from - each one's event id and Eastern day - read through the
    player-games relation's own
    :func:`~association.query.player_games.games_subquery` over ``box``
    (:func:`~association.query.conditions.box_source`), so no narrowing is
    written here: an opponent, a venue, a teammate's absence, a line, a
    date, a window - every cell the relation honors.

    .. versionadded:: 6.0.0
       ``compose.shots._shots_narrowed_rows``' statement, moved whole.
    """
    base_sql, base_params = games_subquery(narrowed, box)
    return _values(con, Statement(f"SELECT event_id, day FROM ({base_sql}) g", list(base_params)))


def _distance_statement(athlete_id: str, span: ResolvedSpan, shot_value: int | None, event_ids: list[str] | None) -> Statement:
    """The distance read: the average distance from the rim, the shots and
    the games they came from. No ``season = ?`` on a career span - the shot
    table holds no rows before 2002, so leaving the column unfiltered sums
    exactly the seasons on record. Free throws are excluded by value, not by
    a missing position: from 2002 to 2018 they carry a fixed one under the
    rim. ``event_ids`` pins the read to the games the relation narrowed to,
    as an IN list even for one."""
    where = ["athlete_id = ?"]
    params: list[Any] = [athlete_id]
    if span.season is not None:
        where.append("season = ?")
        params.append(span.season)
    where += ["season_type = ?", HAS_POSITION_SQL, f"{SHOT_VALUE_SQL} IS DISTINCT FROM 1"]
    params.append(span.season_type)
    if shot_value is not None:
        where.append(f"{SHOT_VALUE_SQL} = ?")
        params.append(shot_value)
    if event_ids is not None:
        where.append(f"event_id IN ({', '.join('?' for _ in event_ids)})")
        params.extend(event_ids)
    return Statement(f"SELECT AVG({SHOT_DISTANCE_SQL}), COUNT(*), COUNT(DISTINCT event_id) FROM shot_chart WHERE {' AND '.join(where)}", params)


def distance(con: duckdb.DuckDBPyConnection, athlete_id: str, span: ResolvedSpan, shot_value: int | None, event_ids: list[str] | None) -> tuple[Any, ...]:
    """One player's average shot distance from the rim over ``span`` - of
    one shot value, or of every shot but free throws - and the shots and the
    games it is over: ``(average, attempts, games)``, the average ``None``
    and the counts 0 where nothing was found. Pinned to ``event_ids`` where
    a narrowing chose the games (:func:`narrowed_games`).

    .. versionadded:: 6.0.0
       ``compose.shots._shot_distance_statement``'s read, moved whole.
    """
    (row,) = _values(con, _distance_statement(athlete_id, span, shot_value, event_ids))
    return row


def _chart_statement(athlete_id: str, season: int | None, season_type: int | None, pinned: Sequence[str] | None, shot_value: int | None) -> Statement:
    """The shots a chart draws: one player's located shots in a season (and
    its type), or in the games pinned. Free throws are left off - from 2002
    to 2018 they carry a fixed position under the rim. Filtered to a shot
    value, a shot NO value can be read for is kept, so it can be counted and
    said rather than dropped where nobody would know."""
    where = ["athlete_id = ?", HAS_POSITION_SQL]
    params: list[Any] = [athlete_id]
    if season is not None:
        where.append("season = ?")
        params.append(season)
    if season_type is not None:
        where.append("season_type = ?")
        params.append(season_type)
    # The games pinned, as an IN list even for one: these ids came FROM the
    # relation, and a bare equality on the game is the shape the relation
    # templates' source check forbids a reader to write.
    if pinned is not None:
        where.append(f"event_id IN ({', '.join('?' for _ in pinned)})")
        params.extend(pinned)
    if shot_value is not None:
        where.append(f"({SHOT_VALUE_SQL} = ? OR {SHOT_VALUE_SQL} IS NULL)")
        params.append(shot_value)
    else:
        where.append(f"{SHOT_VALUE_SQL} IS DISTINCT FROM 1")
    # Ordered, so a chart's bytes are a function of its shots and nothing
    # else (ISSUES.md #254: read with no ORDER BY, a multi-game chart's
    # markup came out in whichever order the scan delivered the rows, and
    # two draws of one chart differed as bytes while identical as sorted
    # fragments). Every column of the key, so the order is total.
    order = "ORDER BY event_id, period, clock, coordinate_x, coordinate_y, made, shot_type"
    return Statement(f"SELECT coordinate_x, coordinate_y, made, shot_type, period, clock, event_id, season, {SHOT_VALUE_SQL} FROM shot_chart WHERE {' AND '.join(where)} {order}", params)


def chart_shots(con: duckdb.DuckDBPyConnection, athlete_id: str, season: int | None, season_type: int | None, pinned: Sequence[str] | None, shot_value: int | None) -> list[tuple[Any, ...]]:
    """The located shots a chart draws, in a total order: each one's
    position (``coordinate_x``, ``coordinate_y``), whether it went in, its
    type, period, clock, game and season, and its value
    (:data:`~association.query.shotchart.SHOT_VALUE_SQL`, ``None`` where
    nothing establishes it) - over a season and its type, or the games
    ``pinned`` (the season and type ``None`` then), of one value or every
    shot but free throws.

    .. versionadded:: 6.0.0
       ``compose.shots._shot_chart_statement``'s read, moved whole.
    """
    return _values(con, _chart_statement(athlete_id, season, season_type, pinned, shot_value))
