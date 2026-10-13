"""The standings' reads: ESPN's ``standings`` table, read for one team's
record - one regular season's line, or every season it holds - and the
team's own season totals the standings are checked against
(``team_season_stats``' games played).

Part of the team-season relation (``team_seasons``,
:data:`~association.query.reading.PointRelation`), beside its season lines
(:mod:`association.query.team_seasons`): this module builds the four
statements and runs them, each through the compiler's one door
(:func:`~association.query.compose.core.values_of`), and hands the reader
(:mod:`association.query.compose.standings`) its rows or values - so
``compose/`` executes nothing it did not compile. The standings' floor is
``nba.coverage.COVERAGE["standings"]`` (1988), and a team record over them
declares the table (``coverage.SOURCES``). Phase 4, step 5 (``ROADMAP.md``,
"Every statement is a relation's"): the reader's four reads, moved here,
and the four statements they run with them at the step's close (built in
:mod:`association.query.team_seasons` until then).

.. versionadded:: 6.0.0
"""

from __future__ import annotations

from typing import Any

import duckdb

from association.query.season_line import Statement

# --- the statements ---------------------------------------------------------------

_STANDINGS_SEASON_COLUMNS = 'wins, losses, winPercent, streak, playoffSeed, gamesBehind, "Home", "Road", "Last Ten Games", avgPointsFor, avgPointsAgainst, differential'


def standings_season(team_id: str, season: int) -> Statement:
    """A team's standings row for one season: wins, losses, win percentage,
    streak, playoff seed, games behind, the "Home" and "Road" records as
    ESPN writes them ("30-10"), the last ten games, and points for and
    against per game with the differential.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Moved from ``team_seasons`` to the module that runs it.
    """
    return Statement(f"SELECT {_STANDINGS_SEASON_COLUMNS} FROM standings WHERE team_id = ? AND season = ?", [team_id, season])


def standings_career(team_id: str) -> Statement:
    """Every season a team's standings hold a game for, oldest first: the
    season, wins, losses, and the "Home" and "Road" records.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Moved from ``team_seasons`` to the module that runs it.
    """
    return Statement('SELECT season, wins, losses, "Home", "Road" FROM standings WHERE team_id = ? AND wins + losses > 0 ORDER BY season', [team_id])


def standings_first_season() -> Statement:
    """The first season the standings hold for any team - where a career
    that starts there is the warehouse's start, not the franchise's.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Moved from ``team_seasons`` to the module that runs it.
    """
    return Statement("SELECT MIN(season) FROM standings", [])


def games_played(team_id: str, seasons: list[int]) -> Statement:
    """(season, games played) in a team's regular seasons among
    ``seasons``, from ``team_season_stats`` - what the standings are
    checked against: ESPN's 2000 standings stop two games short for most
    teams, and nothing in the row says so.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       Moved from ``team_seasons`` to the module that runs it.
    """
    return Statement(
        f"SELECT season, gamesPlayed FROM team_season_stats WHERE team_id = ? AND season_type = 2 AND season IN ({', '.join('?' for _ in seasons)})",
        [team_id, *seasons],
    )


# --- the reads --------------------------------------------------------------------


def season_row(con: duckdb.DuckDBPyConnection, team_id: str, season: int) -> list[tuple[Any, ...]]:
    """A team's standings row for one season, as
    :func:`standings_season` selects it - none where the standings hold no
    row for it.

    .. versionadded:: 6.0.0
       ``compose.standings.read_standings_season``'s read, moved to its
       relation.
    """
    from association.query.compose.core import values_of  # at call time: the compiler imports the relations

    return values_of(con, standings_season(team_id, season))


def career_rows(con: duckdb.DuckDBPyConnection, team_id: str) -> list[tuple[Any, ...]]:
    """Every season a team's standings hold a game for, oldest first: the
    season, wins, losses, and the "Home" and "Road" records
    (:func:`standings_career`).

    .. versionadded:: 6.0.0
       ``compose.standings.read_standings_career``'s read, moved to its
       relation.
    """
    from association.query.compose.core import values_of  # at call time, as above

    return values_of(con, standings_career(team_id))


def first_season(con: duckdb.DuckDBPyConnection) -> Any:
    """The first season the standings hold for any team - where a career
    that starts there is the warehouse's start, not the franchise's
    (:func:`standings_first_season`).

    .. versionadded:: 6.0.0
       ``compose.standings.read_standings_career``'s read, moved to its
       relation.
    """
    from association.query.compose.core import values_of  # at call time, as above

    return min(r[0] for r in values_of(con, standings_first_season()))


def season_games(con: duckdb.DuckDBPyConnection, team_id: str, seasons: list[int]) -> dict[Any, Any]:
    """A team's games played in each of its regular seasons among
    ``seasons``, by season, from ``team_season_stats``
    (:func:`games_played`) - what the
    standings are checked against: ESPN's 2000 standings stop two games
    short for most teams, and nothing in the row says so.

    .. versionadded:: 6.0.0
       ``compose.standings._standings_short``'s read, moved to its relation.
    """
    from association.query.compose.core import values_of  # at call time, as above

    return dict(values_of(con, games_played(team_id, seasons)))
