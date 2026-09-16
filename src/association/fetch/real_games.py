"""``real_games``: the rows of ``games`` that are actually games.

ESPN serves three kinds of row alongside the real ones, all of which look like
games, and every one of them was being counted somewhere. They are the
source's rows, not this project's - a full fresh pull on 2026-09-11 reproduced
``games`` exactly, placeholders and all - so there is nothing to re-fetch and
nothing a parser fix would change. What there is to do is read past them, once,
in one place. See ``DATA.md`` ("``games`` carries placeholder, duplicate and
phantom rows") for the catalogue and the evidence.

Measured against the 2026-09-14 warehouse, this table keeps 43,343 of
``games``' 43,494 rows, and the 151 it drops are exactly the rows ``DATA.md``
names:

**Placeholders** - 134 regular-season rows scored 0-0 with no
``winner_team_id`` (1999: 50, 2000: 82, 2001: 1, 2002: 1), 133 of them
involving Chicago. Most sit a few hours before the real game between the same
two teams. No row in the whole table has a score without a winner or a winner
without a score, so "no winner" identifies these exactly.

**Rows naming a team that does not exist** - 23 team-slots over 7 ids: 1202 (7
rows), 75 (6), 1300 (3), 100 (3), 31 (2), 125 (1), 83 (1). ``teams`` holds the
30 CURRENT franchises and **a relocation keeps its ESPN id** - Seattle to
Oklahoma City is 25, Vancouver to Memphis 29, Charlotte to New Orleans 3, New
Jersey to Brooklyn 17 - so requiring both ids to be in ``teams`` drops no
legitimate historical franchise. (31 and 32 are the All-Star teams. ESPN's
athlete gamelog files the All-Star game under the regular season, which is how
they leak in at all.)

**Phantoms that carry a winner** - a date-only stamp, no player box score, in a
season that has box scores. Eleven rows: ``131205075`` (under both the 1993 and
1994 labels), ``150611014`` (filed as MIA-ORL; it is Houston's 1995 Finals Game
3, and Miami has no 1995 postseason at all), ``170429031``, ``170501031``,
``171209083``, ``190612021``, ``200422100``, ``200424100``, ``200505100`` and
``200501028`` (a second copy of a TOR-NY playoff game).

Two things about that third rule are load-bearing, and both were measured
before it was written:

- **A date-only stamp is ``T04:00Z`` or ``T05:00Z``, not just ``T04:00Z``.**
  ESPN stores a game whose tip time it does not know at midnight US Eastern,
  which is 04:00Z under EDT and 05:00Z under EST. Four of the eleven phantoms
  are winter games and carry 05:00Z.
- **"No box score" alone would delete the 1988-1992 archive.** Every one of
  those 595 games is stored date-only and none has a player box score, because
  ESPN publishes none before 1993-94 - and ``coverage.py`` declares the
  postseason answerable from 1989. So the rule fires only where the season and
  season_type have box scores at all, which is what separates a phantom from a
  game that is simply older than the box scores. It also leaves alone the real
  games from 1994 on that have no box score (the whole 1997 ECF, the 1995
  Finals Game 5, 1996 SAC-SEA, 1998 UTAH-HOU and about 30 regular-season
  games): every one of them carries a real tip time, never a date-only stamp.

**Same-day duplicates** are collapsed last, after the three filters. A team
cannot play the same opponent twice on one US Eastern date, so a second row for
that pairing is the same game under a second event id - 2003-01-04 DAL-PHI
102-83 as both ``230104006`` and ``400222658``. The row with a box score wins,
which is what keeps the id the rest of the warehouse is keyed on: the pair
above is 24 player rows against none.

The partition includes ``season``, deliberately. Season 1993 is a phantom
SEASON - ESPN answers ``season=1993`` and ``season=1994`` with the identical
1,185 events - and that is a different fault with a different owner
(:mod:`association.coverage`, and the cross-season ``QUALIFY`` in
:data:`association.query.team_metrics.TEAM_GAMES_SQL`). Collapsing it here
would hide it from both.

Built as a TABLE rather than a view because a view is re-planned per query: the
anti-join against ``player_box_stats`` costs about 75ms, and the query path
reads this list several times per question. It is rebuilt by every
``warehouse.build`` call, including a partial ``data load --tables games``, so
it cannot fall behind ``games``.

.. versionadded:: 2.2.0
"""

from __future__ import annotations

import logging

import duckdb

log: logging.Logger = logging.getLogger("association.fetch.real_games")

# The same fixed five-hour shift as parse._EASTERN_OFFSET, kept equal to it by
# test_the_real_games_shift_matches_the_fetch_path. EST and EDT disagree about
# a tip's calendar date only between midnight and 1am Eastern, and no NBA game
# starts there.
EASTERN_OFFSET_HOURS = 5
"""Hours to subtract from a UTC ``games.date`` to get its US Eastern date.

.. versionadded:: 2.2.0
"""

# Midnight US Eastern under EDT and under EST. ESPN writes one of these when it
# has the date of a game but not its tip time.
DATE_ONLY_STAMPS = ("04:00Z", "05:00Z")
"""The ``games.date`` time-of-day values that mean "date only, no tip time".

.. versionadded:: 2.2.0
"""

# EXISTS over a table that may not be loaded; "false" in its place degrades the
# phantom rule to "never fires", which is the honest reading - with no box
# scores anywhere there is no evidence that a game is missing one.
_GAME_HAS_BOX = "EXISTS (SELECT 1 FROM player_box_stats pb WHERE pb.event_id = g.event_id AND pb.season = g.season)"
_SEASON_HAS_BOX = "EXISTS (SELECT 1 FROM player_box_stats pb WHERE pb.season = listed.season AND pb.season_type = listed.season_type)"


def real_games_sql(*, box_scores: bool) -> str:
    """The ``CREATE OR REPLACE TABLE`` statement behind ``real_games``.

    The table has exactly ``games``' columns, so it is a drop-in replacement
    for it in any query that does not want the junk rows.

    Args:
        box_scores: Whether ``player_box_stats`` is loaded. Without it the
            phantom rule cannot be evaluated and is left out rather than
            guessed at; the placeholder, unknown-team and duplicate rules
            still apply.

    Returns:
        One SQL statement, with no parameters - every value in it is a
        constant from this module.

    .. versionadded:: 2.2.0
    """
    game_has_box = _GAME_HAS_BOX if box_scores else "false"
    season_has_box = _SEASON_HAS_BOX if box_scores else "false"
    stamps = ", ".join(f"'{s}'" for s in DATE_ONLY_STAMPS)
    return f"""
        CREATE OR REPLACE TABLE real_games AS
        WITH listed AS (
            SELECT g.*,
                   CAST(CAST(replace(replace(g.date, 'T', ' '), 'Z', '') AS TIMESTAMP) - INTERVAL {EASTERN_OFFSET_HOURS} HOUR AS DATE) AS eastern_day,
                   {game_has_box} AS has_box
            FROM games g
            WHERE g.winner_team_id IS NOT NULL
              AND g.home_team_id IN (SELECT team_id FROM teams)
              AND g.away_team_id IN (SELECT team_id FROM teams)
        )
        SELECT * EXCLUDE (eastern_day, has_box)
        FROM listed
        WHERE NOT (substr(listed.date, 12) IN ({stamps}) AND NOT listed.has_box AND {season_has_box})
        QUALIFY row_number() OVER (
            PARTITION BY listed.season, listed.season_type, listed.eastern_day,
                         least(listed.home_team_id, listed.away_team_id), greatest(listed.home_team_id, listed.away_team_id)
            ORDER BY listed.has_box DESC, listed.date, listed.event_id
        ) = 1
    """


# The columns the filter reads. A real pull always writes all of them; a tree
# holding one hand-made Parquet file may not, and building over a `games` that
# has no `winner_team_id` would raise from inside the load rather than from the
# query that wanted the column.
_REQUIRED_GAME_COLUMNS = frozenset({"event_id", "season", "season_type", "date", "home_team_id", "away_team_id", "winner_team_id"})


def _real_games_columns(con: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    """The column names of ``table``, which is always a literal from this module."""
    return {str(row[0]) for row in con.execute(f"DESCRIBE {table}").fetchall()}


def build_table(con: duckdb.DuckDBPyConnection, loaded: set[str]) -> None:
    """(Re)build ``real_games`` on ``con``, if the tables it reads are there.

    ``games`` and ``teams`` are required - without ``teams`` there is nothing
    to check a team id against, and a list that skipped that check would be a
    different list under the same name. ``player_box_stats`` is optional; see
    :func:`real_games_sql`.

    Skipped, with a log line, when a required table or column is absent. That
    leaves the table missing rather than built from a filter that could not
    run, so a query for it fails where it is read - which is the loud half of
    the choice. A silently unfiltered list would restore exactly the behavior
    this module removes.

    Args:
        con: An open, writable warehouse connection.
        loaded: The tables that currently exist in it.

    .. versionadded:: 2.2.0
    """
    if not {"games", "teams"} <= loaded:
        log.info("skip real_games (needs games and teams)")
        return
    missing = sorted(_REQUIRED_GAME_COLUMNS - _real_games_columns(con, "games"))
    if missing or "team_id" not in _real_games_columns(con, "teams"):
        log.info("skip real_games (missing columns: %s)", ", ".join(missing) or "teams.team_id")
        return
    con.execute(real_games_sql(box_scores="player_box_stats" in loaded))
    row = con.execute("SELECT count(*), (SELECT count(*) FROM games) FROM real_games").fetchone()
    assert row is not None  # COUNT(*) always returns exactly one row
    log.info("real_games: %d rows (%d dropped as not a game)", row[0], row[1] - row[0])
