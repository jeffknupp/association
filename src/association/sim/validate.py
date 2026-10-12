"""The replay harness: every real game of a held-out set replayed from the
rates before its day, and the simulation scored against what happened.

Phase 8 of ``SIMULATION_PLAN.md``. This is the simulator's yardstick: the
first run sets the tolerances, and every later change is accepted only if
no metric here gets worse beyond noise. Nothing in it is a unit test - it
reads the real warehouse and takes minutes - so ``association sim validate``
runs it by hand and the report is committed under ``scripts/sim_p8/results/``.

For each game in the held-out set (a season's regular-season games from a
date), the harness builds the replay as ``association sim replay`` does
(:func:`association.sim.game.prepare_replay`: the players who appeared, the
box five, the window ending on the game's own Eastern date, so nothing it
predicts is in its rates), simulates ``n`` games, and records:

- the simulated home win probability beside THREE baselines - the home-win
  rate of the window's games before the held-out set, a team-strength
  logistic fit on the two teams' point-in-time net ratings
  (:func:`fit_strength_model`, fitted on the window's earlier games), and
  ESPN's own pre-game win probability (the first ``win_probability`` sample,
  2018 on) - scored by the Brier score (:func:`brier`);
- the probability-integral-transform of the real margin among the simulated
  margins (:func:`pit`): flat across ten bins when the spread is right, a U
  when it is too narrow;
- the real and simulated totals, their mean error and spread;
- the league's rates over the set - pace, points per 100 possessions, 3PA
  and FTA per FGA, offensive-rebound and turnover rates - simulated against
  real, from the same box formula on both sides;
- each player's real points, rebounds and assists against the simulation's
  80% interval, and his minutes against the simulated mean; and the
  ACCURACY of the player lines - the simulated mean's absolute error and
  the CRPS of the simulated distribution (:func:`crps`) for points,
  rebounds, assists and minutes - by tier of simulated minutes
  (:data:`PLAYER_TIERS`) and for the model's expected top scorer on each
  side.

The two teams' point-in-time net ratings are the mean margins over their
earlier games in the window, so the strength model, like the simulator,
knows nothing of the game it predicts. Games where either team has fewer
than :data:`MIN_RATED_GAMES` earlier games get no strength prediction and
are scored on the other baselines alone.

**Speed.** A game costs about 1.2 s at 200 simulations on one core (the
engine, half of it the rotation model scoring lineups at dead balls; the
day's shared estimation is a sixth). :func:`validate` replays game DAYS in
parallel with ``jobs`` worker processes, each on its own read-only
connection from ``connect``; a day is the unit because its games share one
window's estimates. Every game's seeds are ``seed .. seed + n - 1`` whatever
the worker, so the result is identical for any ``jobs``, and the games are
reported in date order either way.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any

import duckdb

from association.nba.season import eastern_date_sql
from association.sim import game as matchup
from association.sim.database import WAREHOUSE
from association.sim.engine import EngineError
from association.sim.events import SimGame
from association.sim.rotation import RotationError

log: logging.Logger = logging.getLogger("association.sim.validate")

MIN_RATED_GAMES = 10
"""Earlier games a team needs in the window before its net rating is used
by the strength baseline.

.. versionadded:: 6.0.0
"""

PIT_BINS = 10
"""How many bins the margin PIT histogram has.

.. versionadded:: 6.0.0
"""

INTERVAL = (0.1, 0.9)
"""The player interval whose coverage is measured: 80%, between these
quantiles of the simulated lines.

.. versionadded:: 6.0.0
"""


def brier(probabilities: Sequence[float], outcomes: Sequence[bool]) -> float:
    """The mean squared error of probabilities against 0/1 outcomes; 0.25 is
    a coin, lower is better.

    .. versionadded:: 6.0.0
    """
    if not probabilities:
        return float("nan")
    return sum((p - (1.0 if y else 0.0)) ** 2 for p, y in zip(probabilities, outcomes, strict=True)) / len(probabilities)


def pit(actual: float, simulated: Sequence[float]) -> float:
    """Where the real value falls among the simulated ones: the share below
    it plus half the share equal to it (so a discrete margin is not pushed
    to one side), in ``[0, 1]``.

    .. versionadded:: 6.0.0
    """
    if not simulated:
        return float("nan")
    below = sum(1 for s in simulated if s < actual)
    equal = sum(1 for s in simulated if s == actual)
    return (below + 0.5 * equal) / len(simulated)


def pit_histogram(pits: Iterable[float], bins: int = PIT_BINS) -> list[float]:
    """The share of PIT values in each of ``bins`` equal bins (a value of
    exactly 1 lands in the last).

    .. versionadded:: 6.0.0
    """
    values = [p for p in pits if not math.isnan(p)]
    counts = [0] * bins
    for p in values:
        counts[min(int(p * bins), bins - 1)] += 1
    return [c / len(values) if values else 0.0 for c in counts]


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def fit_strength_model(differences: Sequence[float], home_wins: Sequence[bool], iterations: int = 50) -> tuple[float, float]:
    """Fit ``p(home win) = sigmoid(a + b * (home rating - away rating))`` by
    Newton's method; returns ``(a, b)``. With nothing to fit, the home-rate
    intercept alone (``b = 0``).

    .. versionadded:: 6.0.0
    """
    if not differences:
        return 0.0, 0.0
    a, b = 0.0, 0.0
    ys = [1.0 if y else 0.0 for y in home_wins]
    for _ in range(iterations):
        g_a = g_b = h_aa = h_ab = h_bb = 0.0
        for x, y in zip(differences, ys, strict=True):
            p = _sigmoid(a + b * x)
            g_a += p - y
            g_b += (p - y) * x
            w = p * (1 - p)
            h_aa += w
            h_ab += w * x
            h_bb += w * x * x
        det = h_aa * h_bb - h_ab * h_ab
        if det <= 1e-12:
            break
        step_a = (h_bb * g_a - h_ab * g_b) / det
        step_b = (h_aa * g_b - h_ab * g_a) / det
        a, b = a - step_a, b - step_b
        if abs(step_a) < 1e-9 and abs(step_b) < 1e-9:
            break
    return a, b


@dataclass(frozen=True)
class TeamLine:
    """A team-game's box counts, real or simulated (means over the run).

    .. versionadded:: 6.0.0
    """

    points: float
    fga: float
    tpa: float
    fta: float
    oreb: float
    dreb: float
    tov: float

    @property
    def possessions(self) -> float:
        """The box formula: ``FGA - OREB + TOV + 0.44 FTA``."""
        return self.fga - self.oreb + self.tov + 0.44 * self.fta


@dataclass(frozen=True)
class PlayerCheck:
    """One player in one game: what he did against what the simulation gave
    him (the interval bounds and the mean minutes).

    .. versionadded:: 6.0.0
    """

    athlete_id: str
    team_id: str
    minutes: float
    points: int
    rebounds: int
    assists: int
    sim_games: int
    sim_minutes: float
    points_low: float
    points_high: float
    rebounds_low: float
    rebounds_high: float
    assists_low: float
    assists_high: float
    # Accuracy (added 2026-10-12): the simulated mean of each line, and the CRPS of the simulated distribution against the real line.
    points_mean: float = float("nan")
    rebounds_mean: float = float("nan")
    assists_mean: float = float("nan")
    points_crps: float = float("nan")
    rebounds_crps: float = float("nan")
    assists_crps: float = float("nan")
    minutes_crps: float = float("nan")


@dataclass(frozen=True)
class GameCheck:
    """One held-out game: the real result, the simulation's probabilities and
    distributions, the three baselines where available, both sides' lines
    and every player's check.

    .. versionadded:: 6.0.0
    """

    event_id: str
    date: dt.date
    home: str
    away: str
    home_score: int
    away_score: int
    n: int
    p_sim: float
    p_home_rate: float
    p_strength: float | None
    p_espn: float | None
    margin_mean: float
    margin_sd: float
    margin_pit: float
    total_mean: float
    total_sd: float
    real_home: TeamLine
    real_away: TeamLine
    sim_home: TeamLine
    sim_away: TeamLine
    players: tuple[PlayerCheck, ...]
    seconds: float

    @property
    def home_won(self) -> bool:
        """Whether the home side really won."""
        return self.home_score > self.away_score

    @property
    def margin(self) -> int:
        """The real margin, home minus away."""
        return self.home_score - self.away_score


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def _sd(values: Sequence[float]) -> float:
    if len(values) < 2:
        return float("nan")
    m = _mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _team_line_of(games: Sequence[SimGame], home: bool) -> TeamLine:
    totals = [g.home if home else g.away for g in games]
    n = len(totals)
    return TeamLine(
        points=sum(t.points for t in totals) / n,
        fga=sum(t.fga for t in totals) / n,
        tpa=sum(t.tpa for t in totals) / n,
        fta=sum(t.fta for t in totals) / n,
        oreb=sum(t.oreb for t in totals) / n,
        dreb=sum(t.dreb for t in totals) / n,
        tov=sum(t.tov for t in totals) / n,
    )


def _real_lines(con: duckdb.DuckDBPyConnection, real: matchup.RealGame) -> tuple[Mapping[str, TeamLine], list[tuple[str, str, float, int, int, int]]]:
    rows = con.execute(
        f"""
        SELECT team_id, athlete_id, minutes, points, "offensiveRebounds", "defensiveRebounds", assists, "fieldGoalsAttempted", "threePointFieldGoalsAttempted", "freeThrowsAttempted", turnovers
        FROM {WAREHOUSE}.player_box_stats_deduped WHERE event_id = ? AND season = ? AND minutes IS NOT NULL ORDER BY team_id, athlete_id
        """,
        [real.event_id, real.season],
    ).fetchall()
    sums: dict[str, list[float]] = {}
    players: list[tuple[str, str, float, int, int, int]] = []
    for team, athlete, minutes, pts, oreb, dreb, ast, fga, tpa, fta, tov in rows:
        acc = sums.setdefault(str(team), [0.0] * 7)
        for i, v in enumerate((pts, fga, tpa, fta, oreb, dreb, tov)):
            acc[i] += float(v or 0)
        players.append((str(team), str(athlete), float(minutes), int(pts or 0), int((oreb or 0) + (dreb or 0)), int(ast or 0)))
    return {t: TeamLine(*acc) for t, acc in sums.items()}, players


def crps(samples: Sequence[float], value: float) -> float:
    """The continuous ranked probability score of an empirical distribution
    against the value that happened: ``mean |x - value| - mean |x - x'| / 2``
    over the samples (and their pairs), in the stat's own unit; lower is
    better, and a point forecast's CRPS is its absolute error. An empty
    sample scores ``nan``.

    .. versionadded:: 6.0.0
    """
    n = len(samples)
    if n == 0:
        return float("nan")
    ordered = sorted(samples)
    spread = sum((2 * i - n + 1) * x for i, x in enumerate(ordered)) * 2.0 / (n * n)  # mean |x - x'| over all ordered pairs
    return sum(abs(x - value) for x in ordered) / n - spread / 2.0


def _player_checks(games: Sequence[SimGame], real_players: Sequence[tuple[str, str, float, int, int, int]]) -> tuple[PlayerCheck, ...]:
    by_player: dict[str, list[tuple[float, float, float, float]]] = {}
    for g in games:
        for line in g.lines:
            if line.seconds > 0:
                by_player.setdefault(line.athlete_id, []).append((line.minutes, float(line.points), float(line.oreb + line.dreb), float(line.ast)))
    lo, hi = INTERVAL
    checks = []
    for team, athlete, minutes, pts, reb, ast in real_players:
        sims = by_player.get(athlete, [])
        columns = [[s[i] for s in sims] for i in range(4)]
        qp, qr, qa = (matchup.quantiles(columns[i], (lo, hi)) for i in (1, 2, 3))
        checks.append(
            PlayerCheck(
                athlete,
                team,
                minutes,
                pts,
                reb,
                ast,
                len(sims),
                _mean(columns[0]) if sims else 0.0,
                qp["p10"],
                qp["p90"],
                qr["p10"],
                qr["p90"],
                qa["p10"],
                qa["p90"],
                points_mean=_mean(columns[1]),
                rebounds_mean=_mean(columns[2]),
                assists_mean=_mean(columns[3]),
                points_crps=crps(columns[1], float(pts)),
                rebounds_crps=crps(columns[2], float(reb)),
                assists_crps=crps(columns[3], float(ast)),
                minutes_crps=crps(columns[0], minutes),
            )
        )
    return tuple(checks)


def check_game(con: duckdb.DuckDBPyConnection, real: matchup.RealGame, games: Sequence[SimGame], baselines: Mapping[str, float | None], seconds: float) -> GameCheck:
    """Score one replay against the real game.

    .. versionadded:: 6.0.0
    """
    margins = [float(g.home_score - g.away_score) for g in games]
    totals = [float(g.home_score + g.away_score) for g in games]
    real_lines, real_players = _real_lines(con, real)
    empty = TeamLine(0, 0, 0, 0, 0, 0, 0)
    return GameCheck(
        event_id=real.event_id,
        date=real.date,
        home=real.home,
        away=real.away,
        home_score=real.home_score,
        away_score=real.away_score,
        n=len(games),
        p_sim=sum(1 for g in games if g.home_score > g.away_score) / len(games),
        p_home_rate=float(baselines.get("home_rate") or 0.5),
        p_strength=baselines.get("strength"),
        p_espn=baselines.get("espn"),
        margin_mean=_mean(margins),
        margin_sd=_sd(margins),
        margin_pit=pit(float(real.home_score - real.away_score), margins),
        total_mean=_mean(totals),
        total_sd=_sd(totals),
        real_home=real_lines.get(real.home, empty),
        real_away=real_lines.get(real.away, empty),
        sim_home=_team_line_of(games, True),
        sim_away=_team_line_of(games, False),
        players=_player_checks(games, real_players),
        seconds=seconds,
    )


# -- the held-out set and the baselines ----------------------------------------


def held_out_days(con: duckdb.DuckDBPyConnection, season: int, start: dt.date, limit: int | None = None) -> list[list[str]]:
    """The regular-season games of ``season`` on or after ``start`` (Eastern),
    oldest first and grouped by Eastern game day, the first ``limit`` games
    of them when given.

    .. versionadded:: 6.0.0
    """
    rows = con.execute(
        f"SELECT event_id, {eastern_date_sql('date')} AS day FROM {WAREHOUSE}.real_games WHERE season = ? AND season_type = 2 AND {eastern_date_sql('date')} >= ? ORDER BY day, event_id",
        [season, start],
    ).fetchall()
    kept = rows[:limit] if limit else rows
    days: list[list[str]] = []
    last: object = None
    for event_id, day in kept:
        if day != last:
            days.append([])
            last = day
        days[-1].append(str(event_id))
    return days


def held_out(con: duckdb.DuckDBPyConnection, season: int, start: dt.date, limit: int | None = None) -> list[str]:
    """The regular-season games of ``season`` on or after ``start`` (Eastern),
    oldest first, the first ``limit`` of them when given.

    .. versionadded:: 6.0.0
    """
    return [event_id for day in held_out_days(con, season, start, limit) for event_id in day]


@dataclass(frozen=True)
class Baselines:
    """The three baselines' inputs for a held-out set: the home-win rate of
    the window's earlier games, the fitted strength model, each game's two
    point-in-time net ratings, and ESPN's pre-game probabilities.

    .. versionadded:: 6.0.0
    """

    home_rate: float
    training_games: int
    strength_a: float
    strength_b: float
    ratings: Mapping[str, tuple[float | None, float | None]]
    espn: Mapping[str, float]

    def for_game(self, event_id: str) -> dict[str, float | None]:
        """The three probabilities for one game (``None`` where unavailable)."""
        home, away = self.ratings.get(event_id, (None, None))
        strength = _sigmoid(self.strength_a + self.strength_b * (home - away)) if home is not None and away is not None else None
        return {"home_rate": self.home_rate, "strength": strength, "espn": self.espn.get(event_id)}


_RATINGS_SQL = f"""
    WITH g AS (
        SELECT event_id, {eastern_date_sql("date")} AS day, home_team_id, away_team_id, home_score, away_score
        FROM {WAREHOUSE}.real_games WHERE season IN ({{seasons}}) AND season_type = 2
    ),
    sides AS (
        SELECT event_id, day, home_team_id AS team_id, home_score - away_score AS margin FROM g
        UNION ALL SELECT event_id, day, away_team_id, away_score - home_score FROM g
    ),
    rated AS (
        SELECT event_id, team_id,
               avg(margin) OVER (PARTITION BY team_id ORDER BY day, event_id ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS net_before,
               count(*) OVER (PARTITION BY team_id ORDER BY day, event_id ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS games_before
        FROM sides
    )
    SELECT g.event_id, g.day, g.home_score > g.away_score, h.net_before, h.games_before, a.net_before, a.games_before
    FROM g JOIN rated h ON h.event_id = g.event_id AND h.team_id = g.home_team_id
           JOIN rated a ON a.event_id = g.event_id AND a.team_id = g.away_team_id
"""


def _rating_of(net: float | None, games: int) -> float | None:
    return float(net) if net is not None and games >= MIN_RATED_GAMES else None


def baselines_for(con: duckdb.DuckDBPyConnection, season: int, start: dt.date) -> Baselines:
    """Build the baselines: the window is the :data:`association.sim.game.WINDOW_SEASONS`
    seasons ending in ``season``; training games are the window's
    regular-season games before ``start``; each game's team ratings are the
    mean margins of that team's earlier window games (``None`` under
    :data:`MIN_RATED_GAMES`).

    .. versionadded:: 6.0.0
    """
    seasons = ", ".join(str(s) for s in range(season - matchup.WINDOW_SEASONS + 1, season + 1))
    rows = con.execute(_RATINGS_SQL.format(seasons=seasons)).fetchall()
    ratings = {str(r[0]): (_rating_of(r[3], r[4]), _rating_of(r[5], r[6])) for r in rows}
    training = [r for r in rows if r[1] < start]
    home_rate = sum(1 for r in training if r[2]) / len(training) if training else 0.5
    fit = [(h - a, bool(r[2])) for r in training for h, a in [ratings[str(r[0])]] if h is not None and a is not None]
    a_fit, b_fit = fit_strength_model([x for x, _ in fit], [y for _, y in fit])
    espn_rows = con.execute(
        f"SELECT event_id, arg_min(home_win_pct, TRY_CAST(play_id AS HUGEINT)) FROM {WAREHOUSE}.win_probability WHERE season = ? GROUP BY event_id",
        [season],
    ).fetchall()
    espn = {str(e): float(p) for e, p in espn_rows if p is not None}
    return Baselines(home_rate, len(training), a_fit, b_fit, ratings, espn)


# -- the run --------------------------------------------------------------------


@dataclass
class Validation:
    """A whole run: its settings, the baselines and every game's check.

    .. versionadded:: 6.0.0
    """

    season: int
    start: dt.date
    n: int
    seed: int
    baselines: Baselines
    games: list[GameCheck] = field(default_factory=list)
    refused: list[tuple[str, str]] = field(default_factory=list)
    real_minutes: bool = False  # the rotation was the real game's lineups (a diagnostic), not the model


Connect = Callable[[], duckdb.DuckDBPyConnection]
"""A factory for a worker's own read-only connection (``functools.partial``
of :func:`association.sim.database.connect` with the two paths): it must
pickle, since the workers are processes.

.. versionadded:: 6.0.0
"""


@dataclass(frozen=True)
class _DayResult:
    """One game day replayed: its checks, its refusals and its progress lines."""

    checks: list[GameCheck]
    refused: list[tuple[str, str]]
    lines: list[str]


def _replay_day(con: duckdb.DuckDBPyConnection, event_ids: Sequence[str], n: int, seed: int, baselines: Baselines, offset: int, real_minutes: bool = False) -> _DayResult:
    """Replay one day's games on ``con``: the estimates and rotations are
    built once for the day's window (:func:`association.sim.game.shared_for`)
    and shared. ``offset`` numbers the progress lines across days."""
    result = _DayResult([], [], [])
    shared: matchup.Shared | None = None
    for i, event_id in enumerate(event_ids):
        began = time.perf_counter()
        real = matchup.real_game(con, event_id)
        window = matchup.window_for(con, real.season, real.date)
        if shared is None or shared.window != window:
            shared = matchup.shared_for(con, window)
        try:
            prepared = matchup.prepare_replay(con, real, shared, real_minutes=real_minutes)
            games = matchup.run(prepared, n, seed)
        except (matchup.SimulationRefused, EngineError, RotationError) as exc:
            # A replay's roster is the real one, which can run short after
            # foul-outs; the engine refuses to play four, and so does the harness.
            result.refused.append((event_id, f"{type(exc).__name__}: {exc}"))
            continue
        result.checks.append(check_game(con, real, games, baselines.for_game(event_id), time.perf_counter() - began))
        result.lines.append(f"{offset + i + 1}: {event_id} {real.date} in {time.perf_counter() - began:.1f}s")
    return result


WORKER_THREADS = 2
"""DuckDB threads per worker connection: a connection takes a thread per
core by default, and sixteen workers on a 24-core machine then run through
384 of them for queries that are small and many.

.. versionadded:: 6.0.0
"""


def _replay_day_in_worker(connect: Connect, event_ids: Sequence[str], n: int, seed: int, baselines: Baselines, offset: int, real_minutes: bool = False) -> _DayResult:
    """:func:`_replay_day` on a connection of the worker's own, with
    :data:`WORKER_THREADS` DuckDB threads."""
    con = connect()
    try:
        con.execute(f"SET threads TO {WORKER_THREADS}")
        return _replay_day(con, event_ids, n, seed, baselines, offset, real_minutes)
    finally:
        con.close()


def _absorb(run: Validation, result: _DayResult, progress: Callable[[str], None] | None) -> None:
    run.games.extend(result.checks)
    run.refused.extend(result.refused)
    if progress is not None:
        for line in result.lines:
            progress(line)


def validate(
    con: duckdb.DuckDBPyConnection,
    season: int,
    start: dt.date,
    n: int,
    seed: int,
    limit: int | None = None,
    progress: Callable[[str], None] | None = None,
    jobs: int = 1,
    connect: Connect | None = None,
    real_minutes: bool = False,
) -> Validation:
    """Replay the held-out set and score it. The league-wide estimates and
    rotations are built once per game day (:func:`association.sim.game.shared_for`)
    and shared by that day's games. With ``jobs`` above one the days are
    replayed by that many worker processes, each opening its own connection
    with ``connect`` (required then); the result is the same for any
    ``jobs``, games in date order, and the progress lines arrive a day at a
    time as days complete. With ``real_minutes`` every replay follows the
    real game's lineups (:mod:`association.sim.real_minutes`): a diagnostic
    of the rotation model's share of the error, which the report says.

    .. versionadded:: 6.0.0
    """
    if jobs > 1 and connect is None:
        raise ValueError("jobs above one need a connect factory for the workers' own connections")
    run = Validation(season, start, n, seed, baselines_for(con, season, start), real_minutes=real_minutes)
    days = held_out_days(con, season, start, limit)
    offsets = [sum(len(d) for d in days[:k]) for k in range(len(days))]
    if jobs <= 1 or connect is None or len(days) <= 1:
        for day, offset in zip(days, offsets, strict=True):
            _absorb(run, _replay_day(con, day, n, seed, run.baselines, offset, real_minutes), progress)
        return run
    results: dict[int, _DayResult] = {}
    with ProcessPoolExecutor(max_workers=min(jobs, len(days))) as pool:
        futures = {pool.submit(_replay_day_in_worker, connect, day, n, seed, run.baselines, offset, real_minutes): k for k, (day, offset) in enumerate(zip(days, offsets, strict=True))}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
            if progress is not None:
                for line in results[futures[future]].lines:
                    progress(line)
    for k in sorted(results):
        _absorb(run, results[k], None)
    return run


# -- the report -----------------------------------------------------------------


def _league_rates(lines: Sequence[TeamLine]) -> dict[str, float]:
    poss = sum(line.possessions for line in lines)
    fga = sum(line.fga for line in lines)
    oreb = sum(line.oreb for line in lines)
    dreb = sum(line.dreb for line in lines)
    return {
        "pace": poss / len(lines) if lines else float("nan"),
        "points_per_100": 100 * sum(line.points for line in lines) / poss if poss else float("nan"),
        "tpa_per_fga": sum(line.tpa for line in lines) / fga if fga else float("nan"),
        "fta_per_fga": sum(line.fta for line in lines) / fga if fga else float("nan"),
        "oreb_rate": oreb / (oreb + dreb) if oreb + dreb else float("nan"),
        "tov_per_possession": sum(line.tov for line in lines) / poss if poss else float("nan"),
    }


def _coverage(players: Sequence[PlayerCheck], stat: str) -> float:
    lows = [getattr(p, f"{stat}_low") for p in players]
    highs = [getattr(p, f"{stat}_high") for p in players]
    actual = [getattr(p, stat) for p in players]
    inside = sum(1 for a, lo, hi in zip(actual, lows, highs, strict=True) if lo <= a <= hi)
    return inside / len(players) if players else float("nan")


def _win_metrics(games: Sequence[GameCheck]) -> dict[str, Any]:
    wins = [g.home_won for g in games]
    with_strength = [g for g in games if g.p_strength is not None]
    with_espn = [g for g in games if g.p_espn is not None]
    return {
        "home_win_rate_real": _mean([1.0 if w else 0.0 for w in wins]),
        "brier_sim": brier([g.p_sim for g in games], wins),
        "brier_home_rate": brier([g.p_home_rate for g in games], wins),
        "brier_strength": brier([g.p_strength or 0.0 for g in with_strength], [g.home_won for g in with_strength]),
        "brier_strength_games": len(with_strength),
        "brier_sim_on_strength_games": brier([g.p_sim for g in with_strength], [g.home_won for g in with_strength]),
        "brier_espn": brier([g.p_espn or 0.0 for g in with_espn], [g.home_won for g in with_espn]),
        "brier_espn_games": len(with_espn),
        "brier_sim_on_espn_games": brier([g.p_sim for g in with_espn], [g.home_won for g in with_espn]),
    }


def _score_metrics(games: Sequence[GameCheck]) -> dict[str, Any]:
    return {
        "margin_pit": pit_histogram(g.margin_pit for g in games),
        "margin_mean_error": _mean([g.margin_mean - g.margin for g in games]),
        "margin_real_sd": _sd([float(g.margin) for g in games]),
        "margin_sim_sd": _mean([g.margin_sd for g in games]),
        "total_mean_error": _mean([g.total_mean - (g.home_score + g.away_score) for g in games]),
        "total_real_sd": _sd([float(g.home_score + g.away_score) for g in games]),
        "total_sim_sd": _mean([g.total_sd for g in games]),
        "league_real": _league_rates([line for g in games for line in (g.real_home, g.real_away)]),
        "league_sim": _league_rates([line for g in games for line in (g.sim_home, g.sim_away)]),
    }


PLAYER_TIERS: tuple[tuple[str, Callable[[PlayerCheck], bool]], ...] = (
    ("30+ simulated minutes", lambda p: p.sim_minutes >= 30.0),
    ("15-30 simulated minutes", lambda p: 15.0 <= p.sim_minutes < 30.0),
    ("under 15 simulated minutes", lambda p: p.sim_minutes < 15.0),
)
"""The player tiers the accuracy is reported by: by SIMULATED minutes, so
the tiers are the model's own expectation and not the outcome (a tier by
real points would select on what is being scored).

.. versionadded:: 6.0.0
"""


def _accuracy(players: Sequence[PlayerCheck], tier: str) -> dict[str, Any]:
    """Mean absolute error of the simulated mean and mean CRPS, per stat, over ``players``."""
    out: dict[str, Any] = {"tier": tier, "n": len(players), "coverage_points": _coverage(players, "points")}
    for stat in ("points", "rebounds", "assists"):
        out[f"{stat}_mae"] = _mean([abs(getattr(p, f"{stat}_mean") - getattr(p, stat)) for p in players if not math.isnan(getattr(p, f"{stat}_mean"))])
        out[f"{stat}_crps"] = _mean([getattr(p, f"{stat}_crps") for p in players if not math.isnan(getattr(p, f"{stat}_crps"))])
    out["minutes_mae"] = _mean([abs(p.sim_minutes - p.minutes) for p in players])
    out["minutes_crps"] = _mean([p.minutes_crps for p in players if not math.isnan(p.minutes_crps)])
    return out


def _top_scorers(games: Sequence[GameCheck]) -> list[PlayerCheck]:
    """Each side's expected top scorer in each game, by the simulated mean."""
    tops = []
    for g in games:
        for team in (g.home, g.away):
            side = [p for p in g.players if p.team_id == team and p.sim_games > 0 and not math.isnan(p.points_mean)]
            if side:
                tops.append(max(side, key=lambda p: p.points_mean))
    return tops


def _player_metrics(games: Sequence[GameCheck]) -> dict[str, Any]:
    players = [p for g in games for p in g.players if p.sim_games > 0]
    accuracy = _accuracy(players, "all")
    tiers = [_accuracy([p for p in players if member(p)], tier) for tier, member in PLAYER_TIERS]
    tiers.append(_accuracy(_top_scorers(games), "the model's top scorer on each side"))
    return {
        "player_games": len(players),
        "coverage_points": _coverage(players, "points"),
        "coverage_rebounds": _coverage(players, "rebounds"),
        "coverage_assists": _coverage(players, "assists"),
        "minutes_mean_error": _mean([p.sim_minutes - p.minutes for p in players]),
        "minutes_abs_error": _mean([abs(p.sim_minutes - p.minutes) for p in players]),
        **{k: v for k, v in accuracy.items() if k not in ("tier", "n", "coverage_points")},
        "tiers": tiers,
    }


def metrics(run: Validation) -> dict[str, Any]:
    """Every headline number of a run, as plain data.

    .. versionadded:: 6.0.0
    """
    games = run.games
    return {
        "games": len(games),
        "refused": len(run.refused),
        **_win_metrics(games),
        **_score_metrics(games),
        **_player_metrics(games),
        "seconds_per_game": _mean([g.seconds for g in games]),
    }


_ACCURACY_KEYS = ("points_mae", "points_crps", "rebounds_mae", "rebounds_crps", "assists_mae", "assists_crps", "minutes_mae", "minutes_crps")


def _all_tier(m: Mapping[str, Any]) -> dict[str, Any]:
    return {"tier": "all", "n": m["player_games"], "coverage_points": m["coverage_points"], **{k: m[k] for k in _ACCURACY_KEYS}}


def _tier_row(t: Mapping[str, Any]) -> str:
    cells = [t["tier"], str(t["n"]), f"{t['points_mae']:.2f}", f"{t['points_crps']:.2f}", f"{t['coverage_points']:.3f}"]
    cells += [f"{t[k]:.2f}" for k in ("rebounds_mae", "rebounds_crps", "assists_mae", "assists_crps", "minutes_mae", "minutes_crps")]
    return "| " + " | ".join(cells) + " |"


def report(run: Validation, m: Mapping[str, Any] | None = None) -> str:
    """The run as Markdown: the settings, the Brier table, the margin PIT,
    totals, league rates, player coverage and the refusals.

    .. versionadded:: 6.0.0
    """
    m = m or metrics(run)
    b = run.baselines
    lines = [
        f"# Replay validation: {run.season} regular season from {run.start.isoformat()}",
        "",
        f"{m['games']} games replayed, {run.n} simulations each from seed {run.seed}; {m['refused']} refused. {m['seconds_per_game']:.1f} s a game.",
        f"Baselines fitted on {b.training_games} earlier window games: home-win rate {b.home_rate:.3f}; strength model p = sigmoid({b.strength_a:.3f} + {b.strength_b:.4f} * net-rating difference).",
        *(["**Rotation: the real game's lineups, followed by the clock** - a diagnostic of the rotation model's share of the error, not a forecast (minutes are given)."] if run.real_minutes else []),
        "",
        "## Win probability (Brier score, lower is better; 0.25 is a coin)",
        "",
        "| model | games | Brier |",
        "| --- | --- | --- |",
        f"| simulation | {m['games']} | {m['brier_sim']:.4f} |",
        f"| home-win rate | {m['games']} | {m['brier_home_rate']:.4f} |",
        f"| team strength (net rating) | {m['brier_strength_games']} | {m['brier_strength']:.4f} (simulation on the same games {m['brier_sim_on_strength_games']:.4f}) |",
        f"| ESPN pre-game | {m['brier_espn_games']} | {m['brier_espn']:.4f} (simulation on the same games {m['brier_sim_on_espn_games']:.4f}) |",
        "",
        f"Real home-win rate in the set: {m['home_win_rate_real']:.3f}.",
        "",
        "## Margin and total",
        "",
        f"Margin (home minus away): mean error {m['margin_mean_error']:+.2f}; real sd {m['margin_real_sd']:.1f}, simulated sd {m['margin_sim_sd']:.1f}.",
        f"Total: mean error {m['total_mean_error']:+.2f}; real sd {m['total_real_sd']:.1f}, simulated sd {m['total_sim_sd']:.1f}.",
        "",
        "Margin PIT histogram (share per decile; flat is right, a U means the spread is too narrow):",
        "",
        "| " + " | ".join(f"{i * 10}-{(i + 1) * 10}" for i in range(PIT_BINS)) + " |",
        "| " + " | ".join("---" for _ in range(PIT_BINS)) + " |",
        "| " + " | ".join(f"{v:.3f}" for v in m["margin_pit"]) + " |",
        "",
        "## League rates over the set (per team-game)",
        "",
        "| rate | real | simulated |",
        "| --- | --- | --- |",
    ]
    labels = {
        "pace": "possessions (box formula)",
        "points_per_100": "points per 100 possessions",
        "tpa_per_fga": "3PA per FGA",
        "fta_per_fga": "FTA per FGA",
        "oreb_rate": "offensive rebound rate",
        "tov_per_possession": "turnovers per possession",
    }
    for key, label in labels.items():
        lines.append(f"| {label} | {m['league_real'][key]:.3f} | {m['league_sim'][key]:.3f} |")
    lines += [
        "",
        "## Players",
        "",
        f"{m['player_games']} player-games. 80% interval coverage: points {m['coverage_points']:.3f}, rebounds {m['coverage_rebounds']:.3f}, "
        f"assists {m['coverage_assists']:.3f} (close to 0.800 is right).",
        f"Minutes: simulated mean minus real, mean {m['minutes_mean_error']:+.2f}, mean absolute {m['minutes_abs_error']:.2f}.",
        "",
        "Accuracy: the simulated mean's mean absolute error against the real line, and the CRPS of the whole simulated distribution "
        "(lower is better; a point forecast's CRPS is its absolute error), by tier of SIMULATED minutes:",
        "",
        "| tier | player-games | points MAE | points CRPS | points coverage | rebounds MAE | rebounds CRPS | assists MAE | assists CRPS | minutes MAE | minutes CRPS |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        *(_tier_row(t) for t in [_all_tier(m), *m["tiers"]]),
    ]
    if run.refused:
        lines += ["", "## Refused", ""] + [f"- {e}: {why}" for e, why in run.refused]
    return "\n".join(lines) + "\n"


def games_json(run: Validation) -> list[dict[str, Any]]:
    """Every game's check as plain data, for comparing two runs.

    .. versionadded:: 6.0.0
    """
    out = []
    for g in run.games:
        d = asdict(g)
        d["date"] = g.date.isoformat()
        out.append(d)
    return out
