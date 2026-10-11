"""``sim_plays``: every play of every real game, classified, ordered and timed.

Phase 1 of ``SIMULATION_PLAN.md``. One row per row of ``nba.plays`` for the
games ``nba.real_games`` holds from :data:`SIM_FLOOR_SEASON` on, with what
every later phase needs and the warehouse does not carry:

- **``seq``, the order of play.** ``ORDER BY period, clock_remaining DESC,
  play_id::HUGEINT`` (decision D3). Numeric ``play_id`` order is NOT the
  order of play: a substitution can carry an id a hundred sequence numbers
  later than the dead ball it happened at. Measured on 38 live games, this
  sort reproduces ESPN's own served order exactly from 2019 and, on the
  measure that matters for lineups - a touch by a player the substitutions
  say is on the bench - beats id order in every season (2026: 0.731% ->
  0.017% of touches). Section 0b of the plan has the tables.
- **``clock_remaining`` and ``elapsed``**, seconds. The clock parses on
  every row of every season (``M:SS``, ``MM:SS``, ``SS.s``). ``elapsed`` is
  seconds from tip-off: 720 a quarter, 300 an overtime.
- **``period_start`` and ``period_end``**, synthesized from the ``period``
  column as the first and last row of each period in ``seq``. ESPN's
  ``Start Period`` marker exists only through 2012 and its ``End Game`` row
  is missing from about a fifth of 2013-2017 games, so neither is relied on.
- **``kind``**, from :mod:`association.sim.vocab`, with the four
  text-dependent types resolved row by row (:data:`TEXT_RESOLUTION_SQL`);
  ``NULL`` for a type the vocabulary does not know, which the build counts
  and fails on above ``vocab.UNMAPPED_LIMIT``. ``team_play`` is true where
  the row has no athlete - a team rebound, a shot-clock turnover - so a team
  rebound keeps its side as its kind and says it is nobody's as a flag.
- **``shot_value`` and ``points``.** A shot's value comes from the shared
  ``query.shotchart.SHOT_VALUE_SQL`` over ``nba.shot_chart`` (never from
  ``points_attempted``, which is 0 where ESPN left the label off: ``DATA.md``).
  A play's points are its own: 1 for a made free throw, the value for a made
  shot, 0 otherwise - never a difference of the running score, which is stale
  on 1-5% of non-scoring rows from 2013 (0b, 0.8). The one exception is a
  scoring play no shot or free-throw type names (``unknown_score``: 2016's
  1,334 ``Not Available`` rows, a few dozen mislabeled rebounds 2013-2017,
  none from 2018), whose points are the score's step from the previous
  scoring row where that step is 1-3 and NULL otherwise.

The running score is kept as ESPN served it (``home_score``, ``away_score``)
for the per-game sibling check only: :class:`SeasonReport` counts the games
whose max over the rows reaches the final and whose points by team sum to it.

Acceptance, measured on this checkout's warehouse (``nba.duckdb`` copied
2026-09-24) by ``association sim build`` on 2026-10-11, 2007-2026 in 19
seconds (``scripts/sim_p1/results/build_2026-10-11.out``). Every season maps
every play (0 unmapped of 11,878,125). Games whose running score reaches the
final: 97.8% (2007) to 99.8% (2025), 99.2-99.8% from 2019. Games whose
points by team, summed from the plays themselves, equal the final:

- 2019 1,307 of 1,312; 2020 1,141 of 1,142; 2021 1,165 of 1,165; 2023
  1,308 of 1,314; 2024 1,306 of 1,313; 2025 1,313 of 1,315; 2026 1,313 of
  1,316 - 99.5-100%, and in six of those eight seasons MORE games than the
  running score reaches, which is why points come from the play.
- 2022: 1,255 of 1,317 (95.3%). 96% of 2022's shots carry no value label
  and the value is read from the position (``DATA.md``), which misreads a
  two for a three in about one game in twenty. The 62 games are excluded
  from estimation by the check, not patched.
- 2013: 904 of 1,314 and 2016: 657 of 1,315 - the seasons whose plays type
  made shots as misses (``DATA.md``, "A team's play-by-play does not add up
  to its box score in six seasons"). Both are below the default estimation
  window and flagged per season (decision D4).
- 2007-2012: 98.5-99.9%; 2014-2015, 2017-2018: 97.4-98.6%.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import duckdb

from association.query.shotchart import SHOT_VALUE_SQL
from association.sim import vocab
from association.sim.database import WAREHOUSE

log: logging.Logger = logging.getLogger("association.sim.plays")

SIM_FLOOR_SEASON = 2007
"""The first season ``sim_plays`` is built from (decision D4). 2002-2003 play
logs are truncated, and 2004-2006 lack the second participant on most plays
and the ``starter`` flag on hundreds of team-games.

.. versionadded:: 6.0.0
"""

REGULATION_PERIOD_SECONDS = 720
"""A quarter, in seconds."""

OVERTIME_PERIOD_SECONDS = 300
"""An overtime period, in seconds."""


class UnmappedPlays(Exception):
    """Raised when a season's plays map to no kind more often than the limit
    allows: ESPN has added a type, and the vocabulary needs it.

    .. versionadded:: 6.0.0
    """


@dataclass(frozen=True)
class SeasonReport:
    """What one season's build kept, and how it checks against its siblings.

    ``games`` are the real games with plays; ``games_reaching_final`` those
    whose running score reaches ``real_games``' final; ``points_match_final``
    those whose points by team, summed over the plays themselves, equal it.

    .. versionadded:: 6.0.0
    """

    season: int
    games: int
    plays: int
    unmapped: int
    games_reaching_final: int
    points_match_final: int

    @property
    def unmapped_share(self) -> float:
        """Unmapped plays as a share of the season's plays."""
        return self.unmapped / self.plays if self.plays else 0.0


CLOCK_SQL = """CASE WHEN p.clock LIKE '%:%'
     THEN TRY_CAST(split_part(p.clock, ':', 1) AS INTEGER) * 60 + TRY_CAST(split_part(p.clock, ':', 2) AS DOUBLE)
     ELSE TRY_CAST(p.clock AS DOUBLE) END"""
"""Seconds left in the period, from ESPN's ``M:SS`` / ``SS.s`` display clock.

.. versionadded:: 6.0.0
"""

TEXT_RESOLUTION_SQL = """CASE
    WHEN k.kind IS NULL THEN NULL
    WHEN p.type = 'Rebound' THEN CASE WHEN p.text ILIKE '%offensive%' THEN 'offensive_rebound'
                                      WHEN p.text ILIKE '%defensive%' THEN 'defensive_rebound'
                                      ELSE 'rebound' END
    WHEN p.type = 'No Turnover' THEN CASE WHEN p.text ILIKE '%turnover%' AND p.text NOT ILIKE '%no turnover%' THEN 'turnover' ELSE 'ignore' END
    WHEN p.type = 'Not Available' THEN CASE WHEN p.text ILIKE '%foul%' THEN 'foul_personal'
                                            WHEN p.text ILIKE '%turnover%' THEN 'turnover'
                                            WHEN p.text ILIKE '%offensive rebound%' THEN 'offensive_rebound'
                                            WHEN p.text ILIKE '%defensive rebound%' THEN 'defensive_rebound'
                                            WHEN p.scoring_play THEN 'unknown_score'
                                            ELSE 'ignore' END
    WHEN p.scoring_play AND k.kind NOT IN ('shot', 'free_throw') THEN 'unknown_score'
    ELSE k.kind END"""
"""The row's kind: the vocabulary's, with the four text-dependent types read
from their text (``vocab.TEXT_RESOLVED``) and a scoring play of any other
kind demoted to ``unknown_score``.

.. versionadded:: 6.0.0
"""


def _season_filter(seasons: Sequence[int] | None) -> str:
    if not seasons:
        return f"p.season >= {SIM_FLOOR_SEASON}"
    below = [s for s in seasons if s < SIM_FLOOR_SEASON]
    if below:
        raise ValueError(f"seasons below the floor ({SIM_FLOOR_SEASON}): {sorted(below)}")
    return "p.season IN (" + ", ".join(str(int(s)) for s in sorted(set(seasons))) + ")"


def _create_kinds(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE OR REPLACE TABLE sim_play_kinds (type VARCHAR PRIMARY KEY, kind VARCHAR NOT NULL, bench_possible BOOLEAN NOT NULL, live_ball BOOLEAN)")
    con.executemany(
        "INSERT INTO sim_play_kinds VALUES (?, ?, ?, ?)",
        [(t, k.kind, k.bench_possible, k.live_ball) for t, k in vocab.PLAY_KINDS.items()],
    )


def _create_plays(con: duckdb.DuckDBPyConnection, seasons: Sequence[int] | None) -> None:
    con.execute(f"""
        CREATE OR REPLACE TABLE sim_plays AS
        WITH shots AS (
            SELECT play_id, season, {SHOT_VALUE_SQL} AS shot_value
            FROM {WAREHOUSE}.shot_chart
        ),
        parsed AS (
            SELECT p.event_id, p.season, p.season_type, p.play_id,
                   TRY_CAST(p.play_id AS HUGEINT) AS pid,
                   p.period,
                   {CLOCK_SQL} AS clock_remaining,
                   p.team_id, p.athlete_id,
                   str_split(p.participant_athlete_ids, ',') AS participants,
                   p.type,
                   {TEXT_RESOLUTION_SQL} AS kind,
                   coalesce(k.bench_possible, false) AS bench_possible,
                   k.live_ball,
                   p.athlete_id IS NULL AND p.team_id IS NOT NULL AS team_play,
                   p.text, p.scoring_play, p.home_score, p.away_score,
                   s.shot_value
            FROM {WAREHOUSE}.plays p
            JOIN {WAREHOUSE}.real_games g ON g.event_id = p.event_id AND g.season = p.season
            LEFT JOIN sim_play_kinds k ON k.type = p.type
            LEFT JOIN shots s ON s.play_id = p.play_id AND s.season = p.season
            WHERE {_season_filter(seasons)}
        ),
        ordered AS (
            SELECT *,
                   row_number() OVER (PARTITION BY season, event_id ORDER BY period, clock_remaining DESC, pid) AS seq,
                   row_number() OVER (PARTITION BY season, event_id, period ORDER BY period, clock_remaining DESC, pid) = 1 AS period_start,
                   row_number() OVER (PARTITION BY season, event_id, period ORDER BY clock_remaining ASC, pid DESC) = 1 AS period_end,
                   CASE WHEN period <= 4 THEN (period - 1) * {REGULATION_PERIOD_SECONDS} + ({REGULATION_PERIOD_SECONDS} - clock_remaining)
                        ELSE 4 * {REGULATION_PERIOD_SECONDS} + (period - 5) * {OVERTIME_PERIOD_SECONDS} + ({OVERTIME_PERIOD_SECONDS} - clock_remaining) END AS elapsed
            FROM parsed
        ),
        scored AS (
            -- The previous SCORING row's score, for the one kind whose points
            -- nothing else gives (unknown_score). Every other kind's points
            -- come from the play itself.
            SELECT *,
                   last_value(CASE WHEN scoring_play THEN home_score END IGNORE NULLS) OVER w AS prev_home,
                   last_value(CASE WHEN scoring_play THEN away_score END IGNORE NULLS) OVER w AS prev_away
            FROM ordered
            WINDOW w AS (PARTITION BY season, event_id ORDER BY seq ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)
        )
        SELECT event_id, season, season_type, play_id, seq, period, clock_remaining, elapsed, period_start, period_end,
               team_id, athlete_id, participants, type, kind, bench_possible, live_ball, team_play,
               text, scoring_play, home_score, away_score, shot_value,
               CASE WHEN NOT coalesce(scoring_play, false) THEN 0
                    WHEN kind = 'free_throw' THEN 1
                    WHEN kind = 'shot' THEN coalesce(shot_value, 2)
                    WHEN kind = 'unknown_score' AND (home_score - coalesce(prev_home, 0)) + (away_score - coalesce(prev_away, 0)) BETWEEN 1 AND 3
                         THEN (home_score - coalesce(prev_home, 0)) + (away_score - coalesce(prev_away, 0))
                    ELSE NULL END AS points
        FROM scored
        ORDER BY season, event_id, seq
    """)


def _report(con: duckdb.DuckDBPyConnection) -> list[SeasonReport]:
    rows = con.execute(f"""
        WITH per_game AS (
            SELECT sp.season, sp.event_id,
                   count(*) AS plays,
                   count(*) FILTER (WHERE sp.kind IS NULL) AS unmapped,
                   max(sp.home_score) = g.home_score AND max(sp.away_score) = g.away_score AS reaches_final,
                   coalesce(sum(sp.points) FILTER (WHERE sp.team_id = g.home_team_id), 0) = g.home_score
                   AND coalesce(sum(sp.points) FILTER (WHERE sp.team_id = g.away_team_id), 0) = g.away_score AS points_match
            FROM sim_plays sp
            JOIN {WAREHOUSE}.real_games g ON g.event_id = sp.event_id AND g.season = sp.season
            GROUP BY sp.season, sp.event_id, g.home_score, g.away_score, g.home_team_id, g.away_team_id
        )
        SELECT season, count(*), sum(plays), sum(unmapped),
               count(*) FILTER (WHERE reaches_final), count(*) FILTER (WHERE points_match)
        FROM per_game GROUP BY season ORDER BY season
    """).fetchall()
    return [SeasonReport(int(s), int(g), int(p), int(u), int(r), int(m)) for s, g, p, u, r, m in rows]


def build_plays(con: duckdb.DuckDBPyConnection, seasons: Sequence[int] | None = None, *, unmapped_limit: float = vocab.UNMAPPED_LIMIT) -> list[SeasonReport]:
    """Build ``sim_play_kinds`` and ``sim_plays`` for ``seasons`` (every season
    from the floor when ``None``) and return one :class:`SeasonReport` per
    season built.

    Fails with :class:`UnmappedPlays` when any season's unmapped share is
    above ``unmapped_limit`` - the table is left in place so the unmapped
    types can be read (``SELECT type, count(*) FROM sim_plays WHERE kind IS
    NULL GROUP BY 1``), since that is what fixes it. A season below
    :data:`SIM_FLOOR_SEASON` is refused outright.

    .. versionadded:: 6.0.0
    """
    _create_kinds(con)
    _create_plays(con, seasons)
    reports = _report(con)
    for r in reports:
        log.info("sim_plays %d: %d games, %d plays, %d unmapped, %d reach the final, %d sum to it", r.season, r.games, r.plays, r.unmapped, r.games_reaching_final, r.points_match_final)
    over = [r for r in reports if r.unmapped_share > unmapped_limit]
    if over:
        worst = max(over, key=lambda r: r.unmapped_share)
        unmapped = con.execute("SELECT type, count(*) FROM sim_plays WHERE kind IS NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10").fetchall()
        raise UnmappedPlays(f"{worst.unmapped} of {worst.plays} plays in {worst.season} ({worst.unmapped_share:.3%}) map to no kind, over the {unmapped_limit:.3%} limit; unmapped types: {unmapped}")
    return reports
