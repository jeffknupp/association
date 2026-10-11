"""Who plays when: rotation profiles estimated from ``sim_segments`` and the
rotation policy the engine asks at every dead ball.

Phase 6 of ``SIMULATION_PLAN.md`` (build-order step 6). The engine (step 5)
calls :meth:`RotationModel.lineup` with a :class:`~association.sim.params.RotationContext`
at every dead ball and substitutes for the difference; the model answers from
three estimates, each read ONLY from ``resolved`` segments of the games in a
:class:`~association.sim.params.Window` (every ``real_games`` game strictly
before ``as_of`` in its seasons):

- **A team's rotation profile** (:class:`RotationProfile`, :func:`build_profiles`):
  per player, the probability of being on the floor in each game minute - 48
  one-minute bins and one overtime bin (:data:`MINUTE_BINS`), the share of
  that minute's resolved seconds he was on the floor for, over the games he
  appeared in - his minutes target (mean box minutes in the games he played,
  ``nba.player_box_stats_deduped``; the segments' own minutes where the box
  is empty), the games he appeared in and the games he started.
- **Foul trouble** (:attr:`LeagueRotation.foul_ratio`, :func:`build_league_rotation`),
  pooled league-wide: at the middle of every game minute, each player who
  appeared in the game is sampled - on the floor or not, with his personal
  fouls so far (the four kinds in :data:`PERSONAL_FOUL_KINDS`; a technical is
  not a personal foul) - and the cell ``(fouls, period)`` holds the ratio of
  the samples on the floor to the sum of the players' own profile
  probabilities for that minute (observed over expected), shrunk toward 1.0
  with :data:`FACTOR_STRENGTH` pseudo-samples.
- **Blowouts** (:attr:`LeagueRotation.blowout_ratio`): the same ratio for the
  box score's starters in the second half, by the cell ``(absolute margin
  bucket, minutes remaining bucket)`` (:func:`margin_bucket`,
  :func:`time_bucket`).

The policy (P6.3) scores every available player at a dead ball as
``on_floor_prob(minute) * foul_factor(fouls, period) * freshness *
blowout_factor ** starter_share * pace`` (:meth:`RotationModel.scores`) and
keeps the top five with hysteresis: a player on the floor is replaced only by
one whose score exceeds his by the ratio :attr:`RotationSettings.hysteresis`.
The foul and blowout factors are :meth:`LeagueRotation.foul` and
:meth:`LeagueRotation.blowout`, each relative to its baseline cell and never
above 1.0 (the class docstring says why). Fatigue is a documented function of
the stint (:func:`fatigue`): 1.0 through the first ``fresh_seconds`` of a
stint, then ``exp(-(stint - fresh) / fatigue_tau)``; a player on the bench
recovers from the fatigue he left with toward 1.0 as ``1 - (1 - f) *
exp(-rest / rest_tau)`` (:func:`recovery`), which is why the model remembers
when each player sat down (the one piece of state the contract does not
carry). The pace term is the plan's fifth factor, added because the four
alone could not hold the regulars near their minutes (the first dry run put
three of nine more than two minutes off): ``exp(sign(d) * (d / pace_tau)^2)``
with ``d`` the profile's expected seconds so far less the seconds played, so
a minute's difference moves nothing and five minutes moves everything, and
two equal players do not trade places every dead ball (a linear term made
them alternate on 80-second stints). At the tip the five are the profile's
most likely starters unless the context already names five on the floor
(the engine may seed the box starters). A fouled-out, ejected or
caller-marked player is never returned, and nothing outside the context's
roster is; fewer than five available raises :class:`RotationError`.

**Measured, 2024-2026** (``scripts/sim_p6/measure.py`` against this
checkout's ``sim.duckdb`` built 2026-10-11 from ``nba.duckdb``; 3,944 games,
every one with segments, 99.84-99.93% of seconds resolved;
``scripts/sim_p6/results/measure_2024-2026_2026-10-11.out``):

- **Substitution windows (P6.2).** 230,031 substitutions; the kind of the
  nearest preceding non-substitution play: timeout 25.7%, made free throw
  17.3% (34,599 of the 40,324 free throws are the first of two: the window is
  between the attempts), period end 14.2%, personal foul 12.0%, turnover
  8.6%, offensive rebound 8.1% (ESPN's dead-ball team rebound after a missed
  first free throw), shooting foul 4.8%, defensive rebound 3.7%, review
  2.4%, made shot 1.7%, violation 0.6% - 99.2% cumulative at the violation,
  so :data:`SUBSTITUTION_KINDS` holds those eleven kinds. The rest: missed
  free throw 0.28%, technical 0.23%, missed shot 0.12%, ejection, jump ball,
  offensive and flagrant fouls under 0.1% each.
- **Stints** (a run of consecutive resolved segments a player stays in the
  lineup for, a period break included; 267,316 of 268,044, the 728 touching
  an unresolved segment excluded): mean 7.10 minutes, quantiles 10/25/50/75/90
  = 2.03 / 4.18 / 6.53 / 9.15 / 12.62; box starters 3.08 / 5.17 / 7.32 /
  9.88 / 14.62, bench 1.35 / 3.18 / 5.40 / 8.08 / 10.97; 3.15 stints per
  player who played.
- **Foul trouble** (raw ratio, then the policy factor): 2 fouls in the first
  quarter 0.593 -> 0.60, 3 in the first 0.259 -> 0.26, 3 in the second
  0.346 -> 0.36, 4 in the second 0.207 -> 0.21, 4 in the third 0.618 ->
  0.71, 5 in the third 0.273 -> 0.31, 5 in the fourth 1.015 -> 1.00; every
  cell of a foul count at or below the period number is 0.97-1.15 -> 1.00.
- **Blowouts** (box starters, raw ratio -> factor): in the last three
  minutes a game within five 1.535 -> 1.00, 6-10 1.500 -> 0.98, 11-15 1.166
  -> 0.76, 16-20 0.539 -> 0.35, 21-30 0.216 -> 0.14, past 30 0.175 -> 0.11;
  with 3-6 minutes left 16-20 -> 0.79, 21-30 -> 0.39, past 30 -> 0.15; 6-9
  minutes left 21-30 -> 0.72, past 30 -> 0.34; 9-12 past 30 -> 0.57; the
  third quarter 0.81-1.00.
- **Fouls from the plays agree with the box.** The four personal-foul kinds
  summed per player-game equal the box ``fouls`` on 99.83-99.91% of
  2024-2026 player-games with minutes (28,415 of 28,462 in 2026); with
  technicals counted in, 94.9-96.3%.

**Calibrated** (``scripts/sim_p6/dry_run.py``, 1,000 runs on a team's 2026
profile with its regulars - every player in at least half its games - all
available, a dead ball every 20-40 s, a random-walk margin and fouls at the
league rate; the results beside the measurement): with the default
:class:`RotationSettings`, Boston's 10 regulars (targets summing to 240.8)
have 9 of 10 mean minutes within 2 of the target (the worst +2.1), stint
quantiles 2.77 / 5.19 / 7.07 / 9.47 / 12.68 (at most 1.01 from the real),
3.20 stints per player and 27.0 substitutions a game (the real 2026 median
is 31); Oklahoma City's 11 regulars (targets summing to 257.4, 17 minutes
more than a game holds) 9 of 11 within 2, every one short, and 11 of 11
against the targets scaled to the game's minutes, stint quantiles at most
0.75 from the real. Whoever hands the policy a roster decides the sum: the
policy divides what the game holds in proportion.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import duckdb

from association.sim.database import WAREHOUSE
from association.sim.params import RotationContext, Window
from association.sim.plays import REGULATION_PERIOD_SECONDS
from association.sim.shrink import shrink

MINUTE_BINS = 49
"""48 one-minute bins of regulation plus one bin for every overtime minute.

.. versionadded:: 6.0.0
"""

OVERTIME_BIN = 48
"""The index of the overtime bin in a player's on-floor profile.

.. versionadded:: 6.0.0
"""

REGULATION_SECONDS = 4 * REGULATION_PERIOD_SECONDS
"""2,880: the seconds in a game before overtime.

.. versionadded:: 6.0.0
"""

OVERTIME_PERIOD_KEY = 5
"""The period every overtime pools into in the foul-trouble table.

.. versionadded:: 6.0.0
"""

PERSONAL_FOUL_KINDS: frozenset[str] = frozenset({"foul_personal", "foul_shooting", "foul_offensive", "foul_flagrant"})
"""The play kinds that count as a personal foul toward fouling out. A
technical does not (``foul_technical``), and summed per player-game these four
equal the box score's ``fouls`` on 99.8% of 2026 player-games.

.. versionadded:: 6.0.0
"""

SUBSTITUTION_KINDS: frozenset[str] = frozenset(
    {"timeout", "free_throw", "period_end", "foul_personal", "turnover", "offensive_rebound", "foul_shooting", "defensive_rebound", "review", "shot", "violation"}
)
"""The play kinds after which real substitutions happen, in order of share:
the eleven kinds that precede 99.2% of 2024-2026 substitutions (the module
docstring has the table). The engine opens a substitution window at exactly
these; a made free throw is the trip's window (a substitution between the
attempts), the offensive rebound is ESPN's dead-ball team rebound after a
missed first attempt, and a made shot is a window only with a stoppage the
log did not record, 1.7%.

.. versionadded:: 6.0.0
"""

FACTOR_STRENGTH = 50.0
"""Pseudo-samples a foul-trouble or blowout cell is shrunk toward 1.0 with:
a cell seen fifty times speaks half for itself, one seen five thousand times
speaks for itself.

.. versionadded:: 6.0.0
"""

MARGIN_BUCKETS: tuple[int, ...] = (5, 10, 15, 20, 30)
"""Upper bounds of the absolute-margin buckets of the blowout table: 0-5,
6-10, 11-15, 16-20, 21-30 and above 30 (bucket 5).

.. versionadded:: 6.0.0
"""

TIME_BUCKETS: tuple[int, ...] = (180, 360, 540, 720, 1080, 1440)
"""Upper bounds in seconds remaining in regulation of the blowout table's
time buckets: the last three minutes, 3-6, 6-9, 9-12 (the fourth quarter by
quarters), 12-18 and 18-24 (the third quarter by halves). Earlier than that,
and in overtime, the blowout factor is 1.0.

.. versionadded:: 6.0.0
"""

UNKNOWN_ON_FLOOR = 0.05
"""The on-floor probability of a rostered player the profile has never seen
(a signing after the window): one minute in twenty, so he plays only when the
regulars are tired, in trouble or in a blowout, and the model lists him in
:attr:`RotationModel.unprofiled` so the output can say so.

.. versionadded:: 6.0.0
"""


def margin_bucket(margin: int) -> int:
    """The blowout table's margin bucket of a lead or deficit, by its size:
    0 for a game within five, 5 for one past thirty.

    .. versionadded:: 6.0.0
    """
    size = abs(margin)
    for bucket, bound in enumerate(MARGIN_BUCKETS):
        if size <= bound:
            return bucket
    return len(MARGIN_BUCKETS)


def time_bucket(elapsed: float) -> int | None:
    """The blowout table's time bucket at ``elapsed`` seconds from tip-off:
    0 for the last three minutes of regulation up to 5 for the first six of
    the second half; ``None`` in the first half and in overtime.

    .. versionadded:: 6.0.0
    """
    remaining = REGULATION_SECONDS - elapsed
    if remaining <= 0:
        return None
    for bucket, bound in enumerate(TIME_BUCKETS):
        if remaining <= bound:
            return bucket
    return None


def minute_bin(elapsed: float) -> int:
    """The profile bin at ``elapsed`` seconds: the game minute, or
    :data:`OVERTIME_BIN` past regulation.

    .. versionadded:: 6.0.0
    """
    return min(int(elapsed // 60), OVERTIME_BIN)


def period_key(period: int) -> int:
    """The foul-trouble table's period: 1-4, every overtime as
    :data:`OVERTIME_PERIOD_KEY`.

    .. versionadded:: 6.0.0
    """
    return min(period, OVERTIME_PERIOD_KEY)


@dataclass(frozen=True)
class PlayerProfile:
    """One player's place in his team's rotation over the window: the games
    he appeared in, the games he started, his minutes target (mean box
    minutes when he played) and his on-floor probability per minute bin,
    conditioned on his having played the game.

    .. versionadded:: 6.0.0
    """

    athlete_id: str
    games: int
    started: int
    minutes_target: float
    on_floor: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.on_floor) != MINUTE_BINS:
            raise ValueError(f"a profile holds {MINUTE_BINS} bins, got {len(self.on_floor)}")

    @property
    def starter_share(self) -> float:
        """The share of his games he started."""
        return self.started / self.games if self.games else 0.0

    def expected_seconds(self, elapsed: float) -> float:
        """The seconds the profile expects him to have played by ``elapsed``:
        the bins before it whole, the current one in proportion."""
        whole = minute_bin(elapsed)
        total = 60.0 * sum(self.on_floor[:whole])
        if whole == OVERTIME_BIN:
            return total + (elapsed - REGULATION_SECONDS) * self.on_floor[OVERTIME_BIN]
        return total + (elapsed - 60.0 * whole) * self.on_floor[whole]


@dataclass(frozen=True)
class RotationProfile:
    """A team's rotation over a window: the games the window holds for it
    and a :class:`PlayerProfile` per player who appeared.

    .. versionadded:: 6.0.0
    """

    team_id: str
    window: Window
    games: int
    players: Mapping[str, PlayerProfile]

    def on_floor_prob(self, athlete_id: str, elapsed: float) -> float:
        """The probability a player is on the floor at ``elapsed`` seconds;
        :data:`UNKNOWN_ON_FLOOR` for one the profile has not seen."""
        profile = self.players.get(athlete_id)
        if profile is None:
            return UNKNOWN_ON_FLOOR
        return profile.on_floor[minute_bin(elapsed)]

    def likely_starters(self, roster: Iterable[str] | None = None) -> tuple[str, ...]:
        """The five most likely starters, among ``roster`` when given: by the
        share of games started, then by the first minute's on-floor
        probability, then by athlete id so the order is stable."""
        candidates = list(self.players) if roster is None else [a for a in roster if a in self.players]
        ranked = sorted(candidates, key=lambda a: (-self.players[a].starter_share, -self.players[a].on_floor[0], a))
        return tuple(ranked[:5])


@dataclass(frozen=True)
class LeagueRotation:
    """The league's pooled rotation habits in a window: how foul trouble and
    blowouts move the on-floor probability. Each cell of ``foul_ratio``
    (``(fouls, period)``) and ``blowout_ratio`` (``(margin bucket, time
    bucket)``, the box starters only) is the observed-over-expected ratio of
    the module docstring, shrunk toward 1.0, with its samples beside it.

    The policy's factors are RELATIVE and never above 1.0 (:meth:`foul`,
    :meth:`blowout`): a foul cell against the same period's foul-free cell,
    a blowout cell against the same time bucket's close-game cell. Measured
    2024-2026, the raw ratios carry two things that are not a coach's choice:
    a player with four fouls in the fourth quarter has played most of the game
    (ratio 1.13 where a foul-free player's is 0.89 - fouls go with minutes),
    and the box starters of a close game are on the floor 1.2-1.5 times their
    profile in the second half (the profile averages blowouts in, and a
    sometime starter who starts tonight plays more than his average). Dividing
    by the baseline cell leaves what the plan asked for: 2 fouls in the first
    quarter 0.62, 3 in the first or second 0.41, 4 in the second 0.39 and the
    third 0.73, 5 in the third 0.42 and the fourth 1.0 (coaches keep a
    five-foul player in late); starters at 16-20 down in the last three minutes
    0.35, at 21-30 0.16, past 30 0.14 and 0.18 with 3-6 minutes left.

    .. versionadded:: 6.0.0
    """

    window: Window
    foul_ratio: Mapping[tuple[int, int], float]
    foul_samples: Mapping[tuple[int, int], int]
    blowout_ratio: Mapping[tuple[int, int], float]
    blowout_samples: Mapping[tuple[int, int], int]

    def foul(self, fouls: int, period: int) -> float:
        """The factor for ``fouls`` personal fouls in ``period``, relative to
        a foul-free player in that period and at most 1.0; 1.0 for a cell the
        window never saw."""
        key = period_key(period)
        baseline = self.foul_ratio.get((0, key), 1.0)
        ratio = self.foul_ratio.get((fouls, key))
        if ratio is None or baseline <= 0:
            return 1.0
        return min(1.0, ratio / baseline)

    def blowout(self, margin: int, elapsed: float) -> float:
        """The starters' factor at a lead or deficit of ``margin`` with
        ``elapsed`` seconds gone, relative to a game within five at the same
        time and at most 1.0; 1.0 in the first half, in overtime and for a
        cell the window never saw."""
        bucket = time_bucket(elapsed)
        if bucket is None:
            return 1.0
        baseline = self.blowout_ratio.get((0, bucket), 1.0)
        ratio = self.blowout_ratio.get((margin_bucket(margin), bucket))
        if ratio is None or baseline <= 0:
            return 1.0
        return min(1.0, ratio / baseline)


NEUTRAL_LEAGUE = LeagueRotation(Window((), dt.date.min), {}, {}, {}, {})
"""A league table with no cells: every factor 1.0, for a policy that reads a
profile alone (tests, a dry run of the profile by itself).

.. versionadded:: 6.0.0
"""


def _window_sql(window: Window) -> tuple[str, list[Any]]:
    """The ``games`` CTE: every ``real_games`` game of the window's seasons
    strictly before its ``as_of`` (``date`` is an ISO string with a time part,
    so the day itself sorts after the bare date and is excluded)."""
    seasons = ", ".join("?" for _ in window.seasons)
    sql = f"""
        games AS (
            SELECT g.event_id, g.season, g.home_team_id, g.away_team_id
            FROM {WAREHOUSE}.real_games g
            WHERE g.season IN ({seasons}) AND g.date < ?
        )"""
    return sql, [*window.seasons, window.as_of.isoformat()]


_SIDES_SQL = """
        sides AS (
            SELECT s.event_id, s.season, s.segment_no, s.start_elapsed, s.end_elapsed, s.resolved, g.home_team_id AS team_id, s.home_lineup AS lineup,
                   s.home_points - s.away_points AS margin_points
            FROM sim_segments s JOIN games g USING (event_id, season)
            UNION ALL
            SELECT s.event_id, s.season, s.segment_no, s.start_elapsed, s.end_elapsed, s.resolved, g.away_team_id, s.away_lineup,
                   s.away_points - s.home_points
            FROM sim_segments s JOIN games g USING (event_id, season)
        ),
        appearances AS (
            SELECT DISTINCT event_id, season, team_id, unnest(lineup) AS athlete_id FROM sides
        )"""

_PROFILE_SQL = """
        game_bins AS (
            SELECT event_id, season, bin, sum(seconds) AS seconds FROM (
                SELECT event_id, season, least(bin, 48) AS bin,
                       least(end_elapsed, (bin + 1) * 60) - greatest(start_elapsed, bin * 60) AS seconds
                FROM (SELECT s.event_id, s.season, s.start_elapsed, s.end_elapsed,
                             unnest(range(floor(s.start_elapsed / 60)::BIGINT, ceil(s.end_elapsed / 60)::BIGINT)) AS bin
                      FROM sim_segments s JOIN games g USING (event_id, season) WHERE s.resolved)
            ) GROUP BY ALL
        ),
        player_bins AS (
            SELECT event_id, season, team_id, athlete_id, bin, sum(seconds) AS seconds FROM (
                SELECT event_id, season, team_id, unnest(lineup) AS athlete_id, least(bin, 48) AS bin,
                       least(end_elapsed, (bin + 1) * 60) - greatest(start_elapsed, bin * 60) AS seconds
                FROM (SELECT event_id, season, team_id, lineup, start_elapsed, end_elapsed,
                             unnest(range(floor(start_elapsed / 60)::BIGINT, ceil(end_elapsed / 60)::BIGINT)) AS bin
                      FROM sides WHERE resolved)
            ) GROUP BY ALL
        ),
        on_floor AS (
            SELECT a.team_id, a.athlete_id, gb.bin, sum(coalesce(pb.seconds, 0)) AS on_seconds, sum(gb.seconds) AS seconds
            FROM appearances a
            JOIN game_bins gb USING (event_id, season)
            LEFT JOIN player_bins pb USING (event_id, season, team_id, athlete_id, bin)
            GROUP BY ALL
        ),
        box AS (
            SELECT a.team_id, a.athlete_id,
                   count(*) AS games,
                   count(*) FILTER (WHERE coalesce(b.starter, false)) AS started,
                   avg(b.minutes) FILTER (WHERE b.minutes IS NOT NULL AND b.minutes > 0) AS box_minutes
            FROM appearances a
            LEFT JOIN {warehouse}.player_box_stats_deduped b USING (event_id, season, team_id, athlete_id)
            GROUP BY ALL
        ),
        segment_minutes AS (
            SELECT team_id, athlete_id, sum(on_seconds) / 60.0 AS minutes FROM on_floor GROUP BY ALL
        ),
        team_games AS (
            SELECT team_id, count(DISTINCT event_id) AS games FROM appearances GROUP BY ALL
        )"""


def _profile_rows(con: duckdb.DuckDBPyConnection, window: Window) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """The three result sets a profile is built from: per player and bin the
    seconds on the floor and the resolved seconds; per player the games,
    starts and minutes; per team the games."""
    games_sql, params = _window_sql(window)
    with_clause = f"WITH {games_sql}, {_SIDES_SQL}, {_PROFILE_SQL.format(warehouse=WAREHOUSE)}"
    bins = con.execute(f"{with_clause} SELECT team_id, athlete_id, bin, on_seconds, seconds FROM on_floor ORDER BY team_id, athlete_id, bin", params).fetchall()
    players = con.execute(
        f"""{with_clause}
        SELECT b.team_id, b.athlete_id, b.games, b.started, coalesce(b.box_minutes, sm.minutes / b.games) AS minutes_target
        FROM box b JOIN segment_minutes sm USING (team_id, athlete_id)""",
        params,
    ).fetchall()
    teams = con.execute(f"{with_clause} SELECT team_id, games FROM team_games", params).fetchall()
    return bins, players, teams


def build_profiles(con: duckdb.DuckDBPyConnection, window: Window) -> dict[str, RotationProfile]:
    """Every team's :class:`RotationProfile` over the window, from the
    resolved segments of its games (``con`` from :func:`database.connect
    <association.sim.database.connect>`, the warehouse attached). A minute no
    resolved segment of a player's games covers has probability 0.0.

    .. versionadded:: 6.0.0
    """
    bins, players, teams = _profile_rows(con, window)
    probabilities: dict[tuple[str, str], list[float]] = {}
    for team_id, athlete_id, bin_, on_seconds, seconds in bins:
        row = probabilities.setdefault((team_id, athlete_id), [0.0] * MINUTE_BINS)
        row[int(bin_)] = float(on_seconds) / float(seconds) if seconds else 0.0
    by_team: dict[str, dict[str, PlayerProfile]] = {}
    for team_id, athlete_id, games, started, minutes_target in players:
        on_floor = tuple(probabilities.get((team_id, athlete_id), [0.0] * MINUTE_BINS))
        by_team.setdefault(team_id, {})[athlete_id] = PlayerProfile(athlete_id, int(games), int(started), float(minutes_target or 0.0), on_floor)
    return {team_id: RotationProfile(team_id, window, int(games), by_team.get(team_id, {})) for team_id, games in teams}


_SAMPLES_SQL = """
        instants AS (
            SELECT g.event_id, g.season, unnest(range(0, ceil(m.ends / 60)::BIGINT)) * 60 + 30.0 AS t
            FROM games g JOIN (SELECT event_id, season, max(end_elapsed) AS ends FROM sim_segments GROUP BY ALL) m USING (event_id, season)
        ),
        sides_before AS (
            SELECT *, coalesce(sum(margin_points) OVER (PARTITION BY event_id, season, team_id ORDER BY segment_no ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS margin
            FROM sides
        ),
        at_instant AS (
            SELECT i.event_id, i.season, i.t, sd.team_id, sd.lineup, sd.margin
            FROM instants i
            JOIN sides_before sd ON sd.event_id = i.event_id AND sd.season = i.season AND sd.start_elapsed <= i.t AND i.t < sd.end_elapsed AND sd.resolved
        ),
        fouls AS (
            SELECT event_id, season, athlete_id, list_sort(list(elapsed)) AS times
            FROM sim_plays WHERE kind IN ({foul_kinds}) AND athlete_id IS NOT NULL
            GROUP BY ALL
        ),
        samples AS (
            SELECT ai.event_id, ai.season, ai.team_id, a.athlete_id, ai.t, ai.margin,
                   list_contains(ai.lineup, a.athlete_id) AS on_floor,
                   coalesce(len(list_filter(f.times, x -> x < ai.t)), 0) AS fouls,
                   coalesce(b.starter, false) AS starter,
                   coalesce(p.on_seconds / nullif(p.seconds, 0), 0) AS expected
            FROM at_instant ai
            JOIN appearances a USING (event_id, season, team_id)
            LEFT JOIN fouls f USING (event_id, season, athlete_id)
            LEFT JOIN {warehouse}.player_box_stats_deduped b USING (event_id, season, team_id, athlete_id)
            LEFT JOIN on_floor p ON p.team_id = a.team_id AND p.athlete_id = a.athlete_id AND p.bin = least(floor(ai.t / 60)::BIGINT, 48)
        )"""


def _factor_table(rows: Iterable[tuple[Any, ...]], strength: float) -> tuple[dict[tuple[int, int], float], dict[tuple[int, int], int]]:
    """Each ``(key_a, key_b, observed, expected, samples)`` row as a cell
    shrunk toward 1.0: ``(observed + k) / (expected + k)``."""
    factors: dict[tuple[int, int], float] = {}
    samples: dict[tuple[int, int], int] = {}
    for a, b, observed, expected, n in rows:
        key = (int(a), int(b))
        factors[key] = shrink(float(observed), float(expected), 1.0, strength)
        samples[key] = int(n)
    return factors, samples


def build_league_rotation(con: duckdb.DuckDBPyConnection, window: Window, *, strength: float = FACTOR_STRENGTH) -> LeagueRotation:
    """The league's foul-trouble and blowout tables over the window (the
    module docstring says how each cell is measured). Fouls past five and the
    first half's blowout cells are not estimated: the first is fouling out,
    which ``available`` carries, and the second is 1.0 by construction.

    .. versionadded:: 6.0.0
    """
    games_sql, params = _window_sql(window)
    foul_kinds = ", ".join(f"'{kind}'" for kind in sorted(PERSONAL_FOUL_KINDS))
    with_clause = f"WITH {games_sql}, {_SIDES_SQL}, {_PROFILE_SQL.format(warehouse=WAREHOUSE)}, {_SAMPLES_SQL.format(warehouse=WAREHOUSE, foul_kinds=foul_kinds)}"
    foul_rows = con.execute(
        f"""{with_clause}
        SELECT least(fouls, 5) AS fouls, least(floor(t / 720)::BIGINT + 1, 5) AS period,
               sum(on_floor::INT) AS observed, sum(expected) AS expected, count(*) AS n
        FROM samples WHERE fouls <= 5 GROUP BY ALL ORDER BY ALL""",
        params,
    ).fetchall()
    blowout_rows = con.execute(
        f"""{with_clause}
        SELECT CASE WHEN abs(margin) <= 5 THEN 0 WHEN abs(margin) <= 10 THEN 1 WHEN abs(margin) <= 15 THEN 2 WHEN abs(margin) <= 20 THEN 3 WHEN abs(margin) <= 30 THEN 4 ELSE 5 END AS margin_bucket,
               CASE WHEN 2880 - t <= 180 THEN 0 WHEN 2880 - t <= 360 THEN 1 WHEN 2880 - t <= 540 THEN 2 WHEN 2880 - t <= 720 THEN 3 WHEN 2880 - t <= 1080 THEN 4 ELSE 5 END AS time_bucket,
               sum(on_floor::INT) AS observed, sum(expected) AS expected, count(*) AS n
        FROM samples WHERE starter AND t >= 1440 AND t < 2880 GROUP BY ALL ORDER BY ALL""",
        params,
    ).fetchall()
    foul_ratio, foul_samples = _factor_table(foul_rows, strength)
    blowout_ratio, blowout_samples = _factor_table(blowout_rows, strength)
    return LeagueRotation(window, foul_ratio, foul_samples, blowout_ratio, blowout_samples)


@dataclass(frozen=True)
class RotationSettings:
    """The policy's knobs, each fit by ``scripts/sim_p6/dry_run.py`` so
    simulated stints and minutes match the real ones (the results beside it).

    ``fresh_seconds`` and ``fatigue_tau`` shape :func:`fatigue`, ``rest_tau``
    :func:`recovery`, ``hysteresis`` is the ratio an incoming player's score
    must exceed the outgoing's by, and ``pace_tau`` the seconds of minutes
    deficit that multiply a player's score by ``e`` - quadratic, so half of it
    moves the score a quarter as much (``None`` turns the term off).

    .. versionadded:: 6.0.0
    """

    fresh_seconds: float = 300.0
    fatigue_tau: float = 1000.0
    rest_tau: float = 180.0
    hysteresis: float = 1.7
    pace_tau: float | None = 300.0


def fatigue(stint_seconds: float, settings: RotationSettings) -> float:
    """The fatigue factor after ``stint_seconds`` on the floor without a
    rest: 1.0 through ``fresh_seconds``, then ``exp(-(stint - fresh) /
    fatigue_tau)``.

    .. versionadded:: 6.0.0
    """
    over = stint_seconds - settings.fresh_seconds
    return 1.0 if over <= 0 else math.exp(-over / settings.fatigue_tau)


def recovery(fatigue_at_exit: float, rest_seconds: float, settings: RotationSettings) -> float:
    """The factor of a player who sat down with fatigue ``fatigue_at_exit``
    and has rested ``rest_seconds``: back toward 1.0 as ``1 - (1 - f) *
    exp(-rest / rest_tau)``.

    .. versionadded:: 6.0.0
    """
    return 1.0 - (1.0 - fatigue_at_exit) * math.exp(-rest_seconds / settings.rest_tau)


class RotationError(Exception):
    """The policy cannot name five: fewer than five rostered players are
    available. Raised rather than returning four, per the plan's refusal rule.

    .. versionadded:: 6.0.0
    """


@dataclass
class _Rest:
    """What the model remembers about a player who left the floor: when and
    how tired."""

    left_at: float
    fatigue_at_exit: float


@dataclass
class RotationModel:
    """The rotation policy (``params.RotationPolicy``) for one team in one
    game: the team's profile, the league's tables and the settings. Keep one
    per team per game; the model remembers who sat down when, and starts over
    when the clock it is handed runs backward (a new game).

    .. versionadded:: 6.0.0
    """

    profile: RotationProfile
    league: LeagueRotation = NEUTRAL_LEAGUE
    settings: RotationSettings = field(default_factory=RotationSettings)
    unprofiled: set[str] = field(default_factory=set)
    _rests: dict[str, _Rest] = field(default_factory=dict, repr=False)
    _was_on: frozenset[str] = field(default_factory=frozenset, repr=False)
    _last_stints: dict[str, float] = field(default_factory=dict, repr=False)
    _last_elapsed: float = field(default=-1.0, repr=False)

    def _observe(self, context: RotationContext) -> None:
        """Update the memory from the context: a new game resets it; a player
        who was on the floor last time and is not now sat down then."""
        if context.elapsed < self._last_elapsed:
            self._rests.clear()
            self._was_on = frozenset()
        on_now = frozenset(p.athlete_id for p in context.players if p.on_floor)
        for player in context.players:
            if player.athlete_id in self._was_on and not player.on_floor:
                self._rests[player.athlete_id] = _Rest(self._last_elapsed, fatigue(self._last_stints.get(player.athlete_id, 0.0), self.settings))
            if player.on_floor:
                self._rests.pop(player.athlete_id, None)
        self._last_stints = {p.athlete_id: p.stint_seconds for p in context.players}
        self._was_on = on_now
        self._last_elapsed = context.elapsed
        self.unprofiled.update(p.athlete_id for p in context.players if p.athlete_id not in self.profile.players)

    def _freshness(self, athlete_id: str, on_floor: bool, stint_seconds: float, elapsed: float) -> float:
        if on_floor:
            return fatigue(stint_seconds, self.settings)
        rest = self._rests.get(athlete_id)
        if rest is None:
            return 1.0
        return recovery(rest.fatigue_at_exit, elapsed - rest.left_at, self.settings)

    def _pace(self, athlete_id: str, seconds_played: float, elapsed: float) -> float:
        if self.settings.pace_tau is None:
            return 1.0
        profile = self.profile.players.get(athlete_id)
        expected = profile.expected_seconds(elapsed) if profile is not None else UNKNOWN_ON_FLOOR * elapsed
        deficit = (expected - seconds_played) / self.settings.pace_tau
        return math.exp(max(min(math.copysign(deficit * deficit, deficit), 4.0), -4.0))

    def scores(self, context: RotationContext) -> dict[str, float]:
        """Every available player's score at this dead ball:
        ``on_floor_prob * foul_factor * freshness * blowout_factor ** starter_share * pace``.

        .. versionadded:: 6.0.0
        """
        scored: dict[str, float] = {}
        blowout = self.league.blowout(context.margin, context.elapsed)
        for player in context.players:
            if not player.available:
                continue
            profile = self.profile.players.get(player.athlete_id)
            starter_share = profile.starter_share if profile is not None else 0.0
            score = self.profile.on_floor_prob(player.athlete_id, context.elapsed)
            score *= self.league.foul(player.fouls, context.period)
            score *= self._freshness(player.athlete_id, player.on_floor, player.stint_seconds, context.elapsed)
            score *= blowout**starter_share
            score *= self._pace(player.athlete_id, player.seconds_played, context.elapsed)
            scored[player.athlete_id] = score
        return scored

    def lineup(self, context: RotationContext) -> tuple[str, ...]:
        """The five to have on the floor from this dead ball on (the contract,
        ``params.RotationPolicy.lineup``). At the tip, the profile's likely
        starters among the available unless five are already on the floor;
        otherwise the current five with each replaced only by an available
        bench player whose score exceeds his by the hysteresis ratio.

        .. versionadded:: 6.0.0
        """
        self._observe(context)
        scored = self.scores(context)
        if len(scored) < 5:
            raise RotationError(f"team {context.team_id} has {len(scored)} available players at {context.elapsed:.0f}s; five are needed")
        on_floor = [a for a in context.on_floor() if a in scored]
        if context.elapsed <= 0:
            # The engine may seed the box score's starters; five on the floor at the tip are kept as they are.
            return tuple(sorted(on_floor)) if len(on_floor) == 5 else self._tip(scored)
        return self._with_hysteresis(on_floor, scored)

    def _tip(self, scored: Mapping[str, float]) -> tuple[str, ...]:
        starters = list(self.profile.likely_starters(scored))
        if len(starters) < 5:
            starters.extend(a for a in sorted(scored, key=lambda a: -scored[a]) if a not in starters)
        return tuple(sorted(starters[:5]))

    def _with_hysteresis(self, on_floor: Sequence[str], scored: Mapping[str, float]) -> tuple[str, ...]:
        """Fill any open slot with the best bench player; then swap the
        weakest on the floor for the best on the bench while the bench
        player's score exceeds the weakest's by the hysteresis ratio."""
        chosen = list(on_floor)
        bench = sorted((a for a in scored if a not in chosen), key=lambda a: -scored[a])
        while len(chosen) < 5 and bench:
            chosen.append(bench.pop(0))
        chosen.sort(key=lambda a: scored[a])
        while bench and chosen and scored[bench[0]] > scored[chosen[0]] * self.settings.hysteresis:
            chosen.pop(0)
            chosen.append(bench.pop(0))
            chosen.sort(key=lambda a: scored[a])
        return tuple(sorted(chosen))
