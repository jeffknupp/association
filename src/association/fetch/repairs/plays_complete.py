"""How far a player's play-by-play falls from his box score, game by game and
stat by stat: the ``plays_complete`` table.

A quarter's or a half's line is rebuilt from the shots and the plays
(:func:`association.nba.period_line.period_line_sql`), because nothing ESPN
serves splits a box score by period. Its one independent check is the game
it belongs to: summed over every period, overtime included, the rebuilt line
must equal the player's box score. Where it does, nothing he did in that
game is missing from the plays; where it does not, some of it is, and no
period of that game can be read exactly for that stat - and by how much it
falls short (or over) is a bound on every period's figure in that game.

That is a fact about ONE player's game, and the period reads used to judge a
whole SEASON instead (``PERIOD_RECONCILIATION``, retired 2026-10-23): 2016's
per-period points agree with ESPN's quarter scores in 76.5% of team-quarters,
under the 90% the reads refuse below, so every 2016 game was left out. But
2016's fault is rows ABSENT at the source (``DATA.md``, "A team's
play-by-play does not add up to its box score in six seasons"), and it
falls on some players' games and not others: measured 2026-10-23, 90.4% of
2016's played player-games are complete on points, 99.3% on rebounds and
93.5% on assists. Where every player of a team is complete on points, the
team-quarters the period line sums agree with the linescore in 99.98% of
2016's (4,544 of them; 2015: 99.99%, 2017: 100.0%) - a complete game reads
as well as any season's.

One row per played player-game (``minutes`` recorded, not ``did_not_play``)
in a game the shot table covers, keyed ``event_id``, ``season``,
``athlete_id``, with ``season_type`` and one BIGINT per
:data:`~association.nba.period_line.PERIOD_COLUMNS` column, named by
:func:`~association.nba.period_line.gap_column` (``points_gap``): the box
score's figure less the period line's summed over the whole game - 0 where
they agree. A game with no row - one the shot table does
not cover, or a line rebuilt from the plays because ESPN's box score is
empty (:mod:`association.fetch.repairs.reconstructed_box`, whose figures are
the plays' own and so check nothing) - is unknown, never complete.

Built over ``player_game_log`` (the box line every reader reads, a person
ESPN lists twice merged), at load time, beside the views it reads, so a
fresh ``data pull`` produces it with no manual step and a ``data load``
rebuilds it.

.. versionadded:: 7.0.0
"""

from __future__ import annotations

import logging

import duckdb

from association.nba.period_line import PERIOD_COLUMNS, PLAYS_COMPLETE, gap_column, period_line_sql

log: logging.Logger = logging.getLogger("association.fetch.repairs.plays_complete")

REQUIRED: frozenset[str] = frozenset({"player_game_log", "shot_chart", "plays"})
"""The tables and views the completeness table is built from: the box line,
and the shots and plays the period line sums.

.. versionadded:: 7.0.0
"""


def plays_complete_sql() -> str:
    """The statement that builds the table: the period line summed over
    every period of every covered game, against each played player-game's
    box figures.

    A column the line holds no figure for (a player with no shot or play in
    the game) is a zero, compared as one: a box score of 0 points and no
    shot is complete.

    .. versionadded:: 7.0.0

    .. versionchanged:: 7.0.0
       Each column is the signed gap (``points_gap``), where it was whether
       the two agree (``points_complete``): the period reads say how far a
       figure may be off.
    """
    line = period_line_sql(None, "SELECT DISTINCT event_id, season FROM shot_chart")
    flags = ",\n            ".join(f"CAST(b.{c} - COALESCE(l.{c}, 0) AS BIGINT) AS {gap_column(c)}" for c in PERIOD_COLUMNS)
    return f"""
        WITH l AS ({line})
        SELECT b.event_id, b.season, b.season_type, b.athlete_id,
            {flags}
        FROM player_game_log b
        LEFT JOIN l ON l.event_id = b.event_id AND l.season = b.season AND l.athlete_id = b.athlete_id
        WHERE b.minutes IS NOT NULL AND NOT b.did_not_play
          AND b.event_id IN (SELECT DISTINCT cov.event_id FROM shot_chart cov WHERE cov.season = b.season)
    """


def build_table(con: duckdb.DuckDBPyConnection, loaded: set[str]) -> None:
    """(Re)build ``plays_complete`` on ``con``, if the tables it reads are
    there (:data:`REQUIRED`).

    Skipped, with a log line, when one is absent, which leaves the table
    missing: a period read over a warehouse without it treats every game as
    unknown, so a season it would otherwise refuse stays refused.

    Args:
        con: An open, writable warehouse connection.
        loaded: The tables and views that currently exist in it.

    .. versionadded:: 7.0.0
    """
    if not loaded >= REQUIRED:
        log.info("skip %s (needs %s)", PLAYS_COMPLETE, ", ".join(sorted(REQUIRED - loaded)))
        return
    con.execute(f"CREATE OR REPLACE TABLE {PLAYS_COMPLETE} AS {plays_complete_sql()}")
    count_row = con.execute(f"SELECT count(*) FROM {PLAYS_COMPLETE}").fetchone()
    assert count_row is not None  # COUNT(*) always returns exactly one row
    log.info("%s: %d rows", PLAYS_COMPLETE, count_row[0])
