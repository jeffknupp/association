"""The standings' reads: ESPN's ``standings`` table, read for one team's
record - one regular season's line, or every season it holds - and the
team's own season totals the standings are checked against
(``team_season_stats``' games played).

Part of the team-season relation (``team_seasons``,
:data:`~association.query.reading.PointRelation`), whose module
(:mod:`association.query.team_seasons`) builds the four statements; this
module runs them, each through the compiler's one door
(:func:`~association.query.compose.core.values_of`), and hands the reader
(:mod:`association.query.compose.standings`) its rows or values - so
``compose/`` executes nothing it did not compile. The standings' floor is
``nba.coverage.COVERAGE["standings"]`` (1988), and a team record over them
declares the table (``coverage.SOURCES``). Phase 4, step 5 (``ROADMAP.md``,
"Every statement is a relation's"): the reader's four reads, moved here.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

from typing import Any

import duckdb

from association.query.team_seasons import games_played, standings_career, standings_first_season, standings_season


def season_row(con: duckdb.DuckDBPyConnection, team_id: str, season: int) -> list[tuple[Any, ...]]:
    """A team's standings row for one season, as
    :func:`~association.query.team_seasons.standings_season` selects it -
    none where the standings hold no row for it.

    .. versionadded:: 6.0.0
       ``compose.standings.read_standings_season``'s read, moved to its
       relation.
    """
    from association.query.compose.core import values_of  # at call time: the compiler imports the relations

    return values_of(con, standings_season(team_id, season))


def career_rows(con: duckdb.DuckDBPyConnection, team_id: str) -> list[tuple[Any, ...]]:
    """Every season a team's standings hold a game for, oldest first: the
    season, wins, losses, and the "Home" and "Road" records
    (:func:`~association.query.team_seasons.standings_career`).

    .. versionadded:: 6.0.0
       ``compose.standings.read_standings_career``'s read, moved to its
       relation.
    """
    from association.query.compose.core import values_of  # at call time, as above

    return values_of(con, standings_career(team_id))


def first_season(con: duckdb.DuckDBPyConnection) -> Any:
    """The first season the standings hold for any team - where a career
    that starts there is the warehouse's start, not the franchise's
    (:func:`~association.query.team_seasons.standings_first_season`).

    .. versionadded:: 6.0.0
       ``compose.standings.read_standings_career``'s read, moved to its
       relation.
    """
    from association.query.compose.core import values_of  # at call time, as above

    return min(r[0] for r in values_of(con, standings_first_season()))


def season_games(con: duckdb.DuckDBPyConnection, team_id: str, seasons: list[int]) -> dict[Any, Any]:
    """A team's games played in each of its regular seasons among
    ``seasons``, by season, from ``team_season_stats``
    (:func:`~association.query.team_seasons.games_played`) - what the
    standings are checked against: ESPN's 2000 standings stop two games
    short for most teams, and nothing in the row says so.

    .. versionadded:: 6.0.0
       ``compose.standings._standings_short``'s read, moved to its relation.
    """
    from association.query.compose.core import values_of  # at call time, as above

    return dict(values_of(con, games_played(team_id, seasons)))
