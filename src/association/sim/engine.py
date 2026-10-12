"""The possession state machine: a seeded, deterministic simulation of one
game from two teams' parameters and a league's.

Phase 5 of ``SIMULATION_PLAN.md`` (build-order step 5), on v1's inputs:
:class:`~association.sim.params.PlayerParams` per minute, a
:class:`~association.sim.params.TeamParams` per side with team-level
defense (D6, option 1) and the :class:`~association.sim.params.LeagueParams`
every rate shrinks toward. Fidelity is D10's v1: team fouls and the bonus,
personal fouls and foul-outs, end-of-period last shots; no end-game fouling,
timeouts or shot clock as state, and no possession start-type conditioning
(deferred to the P3 upgrade, though each possession's start type is recorded
on its event). The engine emits :class:`~association.sim.events.SimEvent`
records and box lines and renders nothing (D11); step 7 writes the text.
Pure Python over ``random.Random(seed)`` (D9): the same seed and inputs give
an equal :class:`~association.sim.events.SimGame`, field for field.

**How the per-minute inputs drive a possession.** Who uses a possession is
drawn from the five's usage weights, ``shots_per_minute +
free_throw_trips_per_minute + turnovers_per_minute``; units cancel in the
normalization. What he does with it is his own mix of the three, which is a
probability per USE (a shot, a trip or a turnover; a possession holds one use
plus one more per offensive rebound). The defense moves the turnover and the
foul-draw probabilities through :func:`~association.sim.shrink.combine`
against the league's per-possession ``turnover_rate`` and ``foul_rate``:
since the defense enters only as the odds ratio ``odds(d) / odds(l)``, both
per possession, the user's per-use probability needs no conversion of its
own - the ~1.1 uses a possession holds would scale ``d`` and ``l`` alike.
The estimator must therefore supply ``forced_turnover_rate`` and
``foul_rate`` in the league's unit, per opponent possession. A probability
of exactly 0 or 1 is left alone (:func:`adjusted`): a shrunk rate is never
either, so the rule only lets a hand-written degenerate team stay
degenerate (the tests' team that never shoots, or never misses).

**Time.** The clock is kept in tenths of a second as an integer, so a
team's five always account for exactly the period's seconds and two games
sum alike. A possession's duration is a normal draw with mean
``1440 / pace`` seconds (``2880`` seconds of game over the two teams'
possessions, ``pace`` the mean of their possessions per 48 minutes) and
standard deviation :data:`DURATION_SPREAD` of the mean, clipped to
[:data:`POSSESSION_FLOOR_SECONDS`, :data:`SHOT_CLOCK_SECONDS`]; after an
offensive rebound the put-back draw has mean :data:`PUTBACK_MEAN_SECONDS`
and is capped at :data:`PUTBACK_CLOCK_SECONDS`. The offensive rebounds, the
fouls that restart a possession and the clock a period's last possession
leaves unused all sit on top of that mean, so the simulated pace runs under
the input pace; ``scripts/sim_p5/sanity.py`` measures by how much, and the
P3 upgrade (durations by start type from the possession table) replaces the
draw.

**What `LeagueParams` does not hold yet** is a documented constant here,
for the estimator to supply later: the heave make rate
(:data:`HEAVE_MAKE_RATE`), the share of misses that are blocked
(:data:`BLOCK_SHARE_OF_MISSES`), the share of the defense's non-shooting
fouls that stop play on the defensive end
(:data:`NON_SHOOTING_DEFENSIVE_SHARE`) and the duration distribution's shape
(:data:`DURATION_SPREAD` and the four clock constants).

**Home court.** ``league.home_court`` is points per game. The home side's
make probabilities (field goals and free throws) are multiplied by
``1 + home_court / (2 * E)`` and the away side's by ``1 - home_court / (2 *
E)``, where ``E`` is the league's expected points per team per game from its
own rates (:func:`league_points_per_possession` times ``pace``), so the two
sides sit ``home_court`` points apart to first order.

**Dead balls.** After a foul (before the last free throw of a trip, or at
once for a non-shooting foul that gives none), after a dead-ball turnover
and at the start of every period, each team's
:class:`~association.sim.params.RotationPolicy` is asked for its five and
the engine substitutes for the difference; a made basket is not a dead ball
here. A policy's answer is checked (five distinct rostered players, every
one available) and a wrong one is an :class:`EngineError`, as is a roster
with fewer than five players left after foul-outs: the engine refuses
rather than plays four. :class:`StartersPolicy` is the trivial policy the
plan names, in use until the rotation model (step 6) lands.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from random import Random

from association.sim import shrink
from association.sim.events import BoxLine, Decision, SimEvent, SimGame, TeamTotals
from association.sim.params import HEAVE, ZONES, LeagueParams, PlayerGameState, PlayerParams, RotationContext, RotationPolicy, TeamParams, value_of
from association.sim.plays import OVERTIME_PERIOD_SECONDS, REGULATION_PERIOD_SECONDS
from association.sim.zones import HEAVE_SECONDS

REGULATION_PERIODS = 4
"""Periods before overtime.

.. versionadded:: 6.0.0
"""

MAX_OVERTIMES = 30
"""A guard against degenerate inputs that can never break a tie (two teams
that cannot score), not a rule: the game is refused with an
:class:`EngineError` past this many overtimes. The record is six.

.. versionadded:: 6.0.0
"""

FOUL_OUT = 6
"""Personal fouls that remove a player for the rest of the game.

.. versionadded:: 6.0.0
"""

BONUS_TEAM_FOULS = 5
"""The team foul in a regulation period from which a non-shooting defensive
foul is two free throws. The last-two-minutes rule (the second team foul) is
not modeled in v1.

.. versionadded:: 6.0.0
"""

OVERTIME_BONUS_TEAM_FOULS = 4
"""The team foul in an overtime period from which the penalty applies.

.. versionadded:: 6.0.0
"""


THREE_SHOT_TRIP_SHARE = 0.035
"""The share of shooting-foul trips that are three free throws. Measured on
``sim_plays`` 2024-2026: 683, 860 and 990 ``Free Throw - 1 of 3`` trips
against 23,155, 23,551 and 25,395 ``1 of 2`` - 2.9%, 3.5%, 3.8%. The first
draft drew the trip's zone from the shooter's zone mix and gave a three
from a three-point zone, which made 40% of trips three shots and, with the
estimator's double-counted and-ones, simulated 0.377 free-throw attempts
per field-goal attempt against a real 0.211 in the first replay validation
(2026-10-11). Not yet a ``LeagueParams`` field; the estimator should supply
it.

.. versionadded:: 6.0.0
"""

HEAVE_MAKE_RATE = 0.05
"""The make rate of a heave, a constant until the league record carries one
(the plan excludes heaves from every shooting rate, so no zone's rate
stands in). Order of magnitude from the shot chart's beyond-35-feet shots
under three seconds; not yet measured by the estimator.

.. versionadded:: 6.0.0
"""

BLOCK_SHARE_OF_MISSES = 0.11
"""The share of missed field goals that are blocked, a constant until the
league record carries one: about five blocks a game against about
forty-six misses in recent seasons.

.. versionadded:: 6.0.0
"""

NON_SHOOTING_DEFENSIVE_SHARE = 0.67
"""Of the personal fouls the five defenders commit per minute beyond the
shooting-foul trips the offense draws (its free-throw trips per minute),
the share that are defensive fouls stopping play with the offense keeping
the ball - the rest are and-one, offensive and loose-ball fouls, which the
and-one, turnover and rebound draws already carry. Measured 2026-10-11 on
2024-2026 regular seasons: 7.51 non-shooting personal fouls a team-game
(1.77 of them in the penalty, 3.54 free throws) against 10.01 shooting
fouls (7.65 trips on misses, 2.47 and-ones) and 1.47 offensive; the
simulation at 0.65 produced 6.46 non-shooting fouls a team-game with trips
counted as every two-shot trip, and the share is set to the ratio that
puts it at 7.5 once trips are the shooting-foul trips alone. A constant
until the league record holds it.

.. versionadded:: 6.0.0
"""

PACE_CALIBRATION = 0.956
"""The factor on a possession's mean duration that makes the simulated
possessions per game match the real ones. The mean is derived from the
teams' pace as if every possession were one draw, but offensive-rebound
continuations, foul restarts and a period's unused last seconds add time
on top, so the game runs light: 4.5% in the toy league (step 5's sanity
check), 4.4% on the 2025 replay validation after the free-throw fix
(96.4 possessions a team-game by the box formula against a real 100.8,
``scripts/sim_p8/results/validate_2025_from_2025-01-01_2026-10-11.md``).
96.4 / 100.8 is 0.956. An interim calibration, measured, until P3's
possession table supplies durations by start type.

.. versionadded:: 6.0.0
"""

DURATION_SPREAD = 0.4
"""The standard deviation of a possession's duration as a share of its mean.

.. versionadded:: 6.0.0
"""

POSSESSION_FLOOR_SECONDS = 3.0
"""The shortest possession the duration draw allows (a put-back aside).

.. versionadded:: 6.0.0
"""

SHOT_CLOCK_SECONDS = 24.0
"""The longest possession the duration draw allows: the shot clock, as a cap
on the draw rather than as state (D10 defers the clock to v2).

.. versionadded:: 6.0.0
"""

PUTBACK_MEAN_SECONDS = 5.0
"""The mean duration of the continuation after an offensive rebound.

.. versionadded:: 6.0.0
"""

PUTBACK_CLOCK_SECONDS = 14.0
"""The cap on a continuation after an offensive rebound: the reset clock.

.. versionadded:: 6.0.0
"""

_TENTHS = 10
_CONTINUE = "continue"
_PUTBACK = "putback"
_PERIOD_START = "period_start"
_MADE_SHOT = "made_shot"
_MADE_FREE_THROW = "made_free_throw"
_DEFENSIVE_REBOUND = "defensive_rebound"
_LIVE_TURNOVER = "live_turnover"
_DEAD_BALL = "dead_ball"


class EngineError(Exception):
    """The engine refused to play on: a setup it cannot run (fewer than five
    starters, a starter not on the roster), a rotation policy that broke its
    contract, a roster with fewer than five players available, or a game past
    :data:`MAX_OVERTIMES`.

    .. versionadded:: 6.0.0
    """


class InvariantError(EngineError):
    """A runtime invariant failed in ``debug`` mode: not five a side, a player
    on the floor with six fouls, or a score the events do not add up to.

    .. versionadded:: 6.0.0
    """


@dataclass(frozen=True)
class TeamSetup:
    """One side of a game: the team's parameters, its roster (every player
    the policy may use, in roster order), the five who start and the
    rotation policy that moves the rest.

    .. versionadded:: 6.0.0
    """

    team: TeamParams
    players: Mapping[str, PlayerParams]
    starters: Sequence[str]
    policy: RotationPolicy


@dataclass(frozen=True)
class GameSetup:
    """A game to simulate: the home side, the away side and the league.

    .. versionadded:: 6.0.0
    """

    home: TeamSetup
    away: TeamSetup
    league: LeagueParams


class StartersPolicy:
    """The trivial rotation policy: the five on the floor stay there, and a
    player who can no longer play (fouled out) is replaced by the first
    available player in roster order who is not on the floor, who then stays.
    Starters off the floor and available (which only a caller's lineup can
    arrange) come back before anyone else.

    .. versionadded:: 6.0.0
    """

    def lineup(self, context: RotationContext) -> tuple[str, ...]:
        """Keep the available five; fill a gap from the bench in roster order."""
        keep = [p.athlete_id for p in context.players if p.on_floor and p.available]
        bench = [p.athlete_id for p in context.players if not p.on_floor and p.available]
        return tuple(keep + bench[: 5 - len(keep)])


def adjusted(probability: float, defense: float, league: float) -> float:
    """An offense's probability under a defense: :func:`~association.sim.shrink.combine`,
    except that exactly 0 or exactly 1 is kept (a shrunk rate is never either;
    a hand-written degenerate team stays degenerate).

    .. versionadded:: 6.0.0
    """
    if probability <= 0.0 or probability >= 1.0:
        return probability
    return shrink.combine(probability, defense, league)


def league_points_per_possession(league: LeagueParams) -> float:
    """The points a league-average possession is worth, from the league's
    own rates: a use is a turnover (``turnover_rate``), a trip
    (``foul_rate``; two free throws, three in :data:`THREE_SHOT_TRIP_SHARE`
    of trips, each at ``free_throw_rate``) or a shot (the zone mix, the make rate by zone, the
    and-one's free throw), and a missed shot the offense rebounds
    (``offensive_rebound_rate``) starts another use. Used to size the
    home-court multiplier; an approximation, documented as one.

    .. versionadded:: 6.0.0
    """
    p_shot = max(0.0, 1.0 - league.turnover_rate - league.foul_rate)
    make = sum(league.zone_mix.get(z, 0.0) * league.make_rate.get(z, 0.0) for z in ZONES)
    shot_points = sum(league.zone_mix.get(z, 0.0) * league.make_rate.get(z, 0.0) * value_of(z) for z in ZONES)
    shot_points += make * league.and_one_rate * league.free_throw_rate
    trip_points = (2.0 + THREE_SHOT_TRIP_SHARE) * league.free_throw_rate
    per_use = p_shot * shot_points + league.foul_rate * trip_points
    continued = p_shot * (1.0 - make) * league.offensive_rebound_rate
    return per_use / (1.0 - min(continued, 0.99))


def home_court_multipliers(league: LeagueParams) -> tuple[float, float]:
    """The home side's and the away side's multipliers on their make
    probabilities (see the module docstring): ``(1, 1)`` with no home court.

    .. versionadded:: 6.0.0
    """
    expected = league.pace * league_points_per_possession(league)
    if league.home_court == 0.0 or expected <= 0.0:
        return 1.0, 1.0
    half = league.home_court / (2.0 * expected)
    return 1.0 + half, 1.0 - half


def _clamp01(p: float) -> float:
    return min(max(p, 0.0), 1.0)


def _pick(rng: Random, players: Sequence[_Player], weights: Sequence[float], skip: _Player | None = None) -> _Player | None:
    """One of ``players`` by ``weights``, ``skip`` left out; ``None`` when the
    weights left are all zero."""
    total = sum(weights)
    if skip is not None:
        total -= weights[players.index(skip)]
    if total <= 0.0:
        return None
    draw = rng.random() * total
    for player, weight in zip(players, weights, strict=True):
        if player is skip:
            continue
        draw -= weight
        if draw < 0.0:
            return player
    return next(p for p in reversed(players) if p is not skip)


def _pick_or_any(rng: Random, players: Sequence[_Player], weights: Sequence[float]) -> _Player:
    """One of ``players`` by ``weights``, or uniformly when the weights are all
    zero: the event has happened and someone has to be charged with it (a
    side whose five never foul is still charged the shooting foul the
    offense drew)."""
    chosen = _pick(rng, players, weights)
    return players[int(rng.random() * len(players))] if chosen is None else chosen


class _Player:
    """One player's state for the game: his rates as plain floats against the
    opponent's defense (fixed for the game, so combined once), his box
    counters, his time and his fouls."""

    __slots__ = (
        "and_one",
        "assisted",
        "ast",
        "ast_w",
        "available",
        "blk",
        "blk_w",
        "dreb",
        "dreb_w",
        "entered_at",
        "entered_margin",
        "fga",
        "fgm",
        "foul_w",
        "fouls",
        "fouls_pm",
        "ft",
        "fta",
        "ftm",
        "id",
        "live",
        "make",
        "make_inputs",
        "on_floor",
        "oreb",
        "oreb_w",
        "p_shot",
        "p_to",
        "p_trip",
        "pf",
        "played",
        "plus_minus",
        "points",
        "stl",
        "stl_w",
        "tov",
        "tpa",
        "tpm",
        "trips_pm",
        "usage",
        "zones",
    )

    def __init__(self, params: PlayerParams, defense: TeamParams, league: LeagueParams, multiplier: float) -> None:
        self.id = params.athlete_id
        shots, trips, turnovers = params.shots_per_minute.value, params.free_throw_trips_per_minute.value, params.turnovers_per_minute.value
        self.usage = shots + trips + turnovers
        self.trips_pm = trips
        self.fouls_pm = params.fouls_per_minute.value
        # The user's own mix per use, the turnover and the foul draw moved by the defense (module docstring).
        if self.usage > 0.0:
            p_to = adjusted(turnovers / self.usage, defense.forced_turnover_rate.value, league.turnover_rate)
            p_trip = adjusted(trips / self.usage, defense.foul_rate.value, league.foul_rate)
        else:
            p_to, p_trip = 0.0, 0.0  # a player with no usage shoots if he is somehow chosen (the fallback below)
        scale = max(p_to + p_trip, 1.0)  # the two adjusted probabilities may sum past one; the shot takes the rest
        self.p_to, self.p_trip = p_to / scale, p_trip / scale
        self.p_shot = max(0.0, 1.0 - self.p_to - self.p_trip)
        self.zones = self._zones(params, defense, league)
        self.make = {z: _clamp01(adjusted(params.make_rate[z].value, defense.allowed_make_rate[z].value, league.make_rate[z]) * multiplier) for z in ZONES}
        self.make_inputs = {z: {"player": params.make_rate[z].value, "defense": defense.allowed_make_rate[z].value, "league": league.make_rate[z], "home": multiplier} for z in ZONES}
        self.ft = _clamp01(params.free_throw_rate.value * multiplier)
        self.and_one, self.assisted, self.live = params.and_one_rate.value, params.assisted_rate.value, params.live_ball_share.value
        self.ast_w, self.oreb_w, self.dreb_w = params.assists_per_minute.value, params.offensive_rebounds_per_minute.value, params.defensive_rebounds_per_minute.value
        self.stl_w, self.blk_w, self.foul_w = params.steals_per_minute.value, params.blocks_per_minute.value, params.fouls_per_minute.value
        self.on_floor = False
        self.available = True
        self.entered_at = 0
        self.entered_margin = 0
        self.played = 0
        self.fouls = 0
        self.points = self.fgm = self.fga = self.tpm = self.tpa = self.ftm = self.fta = 0
        self.oreb = self.dreb = self.ast = self.stl = self.blk = self.tov = self.pf = self.plus_minus = 0

    @staticmethod
    def _zones(params: PlayerParams, defense: TeamParams, league: LeagueParams) -> list[tuple[float, str]]:
        """The zone mix against this defense as cumulative probabilities: the
        player's mix over the five zones (an unknown-position category carries
        no zone and is dropped), each zone moved by the defense's allowed mix
        through :func:`adjusted` and the five renormalized; a player with no
        zone at all shoots the league's mix."""
        weights = {z: adjusted(params.zone_mix.probability(z), defense.allowed_zone_mix.probability(z), league.zone_mix[z]) for z in ZONES}
        total = sum(weights.values())
        if total <= 0.0:
            weights = {z: league.zone_mix[z] for z in ZONES}
            total = sum(weights.values())
        cumulative, running = [], 0.0
        for zone in ZONES:
            running += weights[zone] / total
            cumulative.append((running, zone))
        return cumulative

    def zone(self, rng: Random) -> str:
        """A zone drawn from the player's mix against this defense."""
        draw = rng.random()
        for threshold, zone in self.zones:
            if draw < threshold:
                return zone
        return self.zones[-1][1]


class _Side:
    """One team's state for the game: its players, the five on the floor and
    the weights derived from them, its score, fouls and possessions."""

    def __init__(self, setup: TeamSetup, opponent: TeamParams, league: LeagueParams, multiplier: float) -> None:
        self.team_id = setup.team.team_id
        self.pace = setup.team.pace.value
        self.policy = setup.policy
        self.players = {athlete_id: _Player(params, opponent, league, multiplier) for athlete_id, params in setup.players.items()}
        self.roster = list(self.players.values())
        self.score = 0
        self.possessions = 0
        self.team_fouls: list[int] = []
        # The offense's rebound against this side's defense, as the plan writes it: the league's offensive rate
        # moved by the share of misses this defense does NOT rebound, which is that share exactly.
        self.oreb_against = shrink.combine(league.offensive_rebound_rate, 1.0 - opponent.defensive_rebound_rate.value, league.offensive_rebound_rate)
        starters = list(dict.fromkeys(setup.starters))
        if len(starters) != 5 or any(s not in self.players for s in starters):
            raise EngineError(f"team {self.team_id} needs five distinct rostered starters, got {list(setup.starters)}")
        self.floor: list[_Player] = [self.players[s] for s in starters]
        for player in self.floor:
            player.on_floor = True
        self.usage: list[float] = []
        self.fouls_pm = 0.0
        self.trips_pm = 0.0
        self.refresh()

    def refresh(self) -> None:
        """Recompute what depends on the five on the floor."""
        self.usage = [p.usage for p in self.floor]
        self.fouls_pm = sum(p.fouls_pm for p in self.floor)
        self.trips_pm = sum(p.trips_pm for p in self.floor)

    def available(self) -> int:
        """How many of the roster can still play."""
        return sum(1 for p in self.roster if p.available)


class _Game:
    """The state machine. ``play`` runs the game; ``result`` packs it."""

    def __init__(self, setup: GameSetup, seed: int, debug: bool, trace: bool = False) -> None:
        self.rng = Random(seed)
        self.seed = seed
        self.debug = debug
        self.trace: list[Decision] | None = [] if trace else None
        self.league = setup.league
        home_mult, away_mult = home_court_multipliers(setup.league)
        self.home = _Side(setup.home, setup.away.team, setup.league, home_mult)
        self.away = _Side(setup.away, setup.home.team, setup.league, away_mult)
        # Possessions per game-second over the two teams: each team gets `pace` in 48 minutes, so a possession lasts
        # 2880 / (pace_home + pace_away) seconds; the two sides' mean pace, as the plan's "scaled by both teams' pace".
        mean_pace = (self.home.pace + self.away.pace) / 2.0
        if mean_pace <= 0.0:
            raise EngineError("a team's pace must be positive")
        self.mean_duration = 1440.0 * _TENTHS / mean_pace * PACE_CALIBRATION
        self.period = 0
        self.period_length = 0
        self.clock = 0
        self.period_started_at = 0
        self.events: list[SimEvent] = []
        self.event_points = 0

    # -- time ---------------------------------------------------------------

    @property
    def elapsed(self) -> int:
        """Tenths since tip-off."""
        return self.period_started_at + self.period_length - self.clock

    def _advance(self, tenths: int) -> None:
        self.clock -= tenths

    def _duration(self, put_back: bool) -> int:
        """A possession's duration in tenths (module docstring)."""
        mean = PUTBACK_MEAN_SECONDS * _TENTHS if put_back else self.mean_duration
        cap = (PUTBACK_CLOCK_SECONDS if put_back else SHOT_CLOCK_SECONDS) * _TENTHS
        floor = 1.0 if put_back else POSSESSION_FLOOR_SECONDS * _TENTHS
        draw = self.rng.gauss(mean, DURATION_SPREAD * mean)
        tenths = round(min(max(draw, floor), cap))
        if self.trace is not None:
            self._note(
                "duration",
                f"{tenths / _TENTHS:.1f}s",
                {},
                draw / _TENTHS,
                {"mean": mean / _TENTHS, "sd": DURATION_SPREAD * mean / _TENTHS, "floor": floor / _TENTHS, "cap": cap / _TENTHS, "put_back": 1.0 if put_back else 0.0},
            )
        return tenths

    # -- the trace -----------------------------------------------------------------

    def _note(self, kind: str, chosen: str, probabilities: Mapping[str, float], draw: float = float("nan"), inputs: Mapping[str, float] | None = None) -> None:
        """Record a decision (only when tracing); it belongs to the next event emitted."""
        if self.trace is not None:
            self.trace.append(Decision(len(self.events), kind, chosen, draw, dict(probabilities), dict(inputs or {})))

    def _chance(self, kind: str, probability: float, inputs: Mapping[str, float] | None = None) -> bool:
        """A yes/no draw at ``probability``, noted when tracing."""
        draw = self.rng.random()
        hit = draw < probability
        if self.trace is not None:
            self._note(kind, "yes" if hit else "no", {"yes": probability, "no": 1.0 - probability}, draw, inputs)
        return hit

    def _pick_noted(self, kind: str, players: Sequence[_Player], weights: Sequence[float], skip: _Player | None = None, or_any: bool = False) -> _Player | None:
        """A weighted pick, noted when tracing with each candidate's probability."""
        chosen = _pick_or_any(self.rng, players, weights) if or_any else _pick(self.rng, players, weights, skip)
        if self.trace is not None:
            candidates = [(p, w) for p, w in zip(players, weights, strict=True) if p is not skip]
            total = sum(w for _, w in candidates)
            probabilities = {p.id: (w / total if total > 0 else (1.0 / len(candidates) if or_any else 0.0)) for p, w in candidates}
            self._note(kind, chosen.id if chosen is not None else "nobody", probabilities)
        return chosen

    def _zone_noted(self, shooter: _Player) -> str:
        """The shooter's zone draw, noted with his mix against this defense."""
        zone = shooter.zone(self.rng)
        if self.trace is not None:
            probabilities, previous = {}, 0.0
            for threshold, z in shooter.zones:
                probabilities[z] = threshold - previous
                previous = threshold
            self._note("zone", zone, probabilities)
        return zone

    # -- events and the box ---------------------------------------------------

    def _emit(self, kind: str, team: str, actors: Mapping[str, str], zone: str = "", outcome: str = "", points: int = 0) -> None:
        self.event_points += points
        self.events.append(SimEvent(kind, self.period, self.clock / _TENTHS, self.elapsed / _TENTHS, team, actors, zone, outcome, points, self.home.score, self.away.score))

    def _score(self, side: _Side, player: _Player, points: int) -> None:
        side.score += points
        player.points += points

    def _margin(self, side: _Side) -> int:
        other = self.away if side is self.home else self.home
        return side.score - other.score

    def _enter(self, side: _Side, player: _Player) -> None:
        player.on_floor = True
        player.entered_at = self.elapsed
        player.entered_margin = self._margin(side)
        side.floor.append(player)

    def _leave(self, side: _Side, player: _Player) -> None:
        player.on_floor = False
        player.played += self.elapsed - player.entered_at
        player.plus_minus += self._margin(side) - player.entered_margin
        side.floor.remove(player)

    # -- dead balls and the rotation ----------------------------------------------

    def _context(self, side: _Side) -> RotationContext:
        now = self.elapsed
        states = tuple(
            PlayerGameState(p.id, p.on_floor, (p.played + (now - p.entered_at if p.on_floor else 0)) / _TENTHS, ((now - p.entered_at) if p.on_floor else 0) / _TENTHS, p.fouls, p.available)
            for p in side.roster
        )
        return RotationContext(side.team_id, self.period, self.clock / _TENTHS, now / _TENTHS, self._margin(side), states)

    def _dead_ball(self) -> None:
        """Ask both policies for their five and substitute for the difference."""
        for side in (self.home, self.away):
            if side.available() < 5:
                raise EngineError(f"team {side.team_id} has only {side.available()} players available in period {self.period}; the engine does not play four")
            context = self._context(side)
            wanted = side.policy.lineup(context)
            self._validate(side, wanted)
            incoming = [side.players[a] for a in wanted if not side.players[a].on_floor]
            outgoing = [p for p in side.floor if p.id not in wanted]
            if self.trace is not None:
                self._note_rotation(side, context, wanted, incoming, outgoing)
            for out, into in zip(outgoing, incoming, strict=True):
                self._leave(side, out)
                self._enter(side, into)
                self._emit("substitution", side.team_id, {"in": into.id, "out": out.id})
            if incoming:
                side.refresh()

    def _note_rotation(self, side: _Side, context: RotationContext, wanted: Sequence[str], incoming: Sequence[_Player], outgoing: Sequence[_Player]) -> None:
        """The policy's answer at a dead ball, with its scores where it has
        any (``RotationModel.scores``), else the five it named."""
        scores = getattr(side.policy, "scores", None)
        probabilities: Mapping[str, float] = scores(context) if callable(scores) else dict.fromkeys(wanted, 1.0)
        change = f"in {', '.join(p.id for p in incoming)}; out {', '.join(p.id for p in outgoing)}" if incoming else "no change"
        self._note("rotation", f"{side.team_id}: {change}", probabilities, inputs={"margin": float(context.margin), "elapsed": context.elapsed, "period": float(context.period)})

    def _validate(self, side: _Side, wanted: Sequence[str]) -> None:
        if len(wanted) != 5 or len(set(wanted)) != 5:
            raise EngineError(f"team {side.team_id}'s policy returned {list(wanted)}, not five distinct players")
        for athlete_id in wanted:
            player = side.players.get(athlete_id)
            if player is None:
                raise EngineError(f"team {side.team_id}'s policy returned {athlete_id!r}, who is not on the roster")
            if not player.available:
                raise EngineError(f"team {side.team_id}'s policy returned {athlete_id!r}, who has fouled out")

    def _charge_foul(self, defense: _Side, fouler: _Player, fouled: _Player | None, kind: str, zone: str = "") -> None:
        """A personal foul on ``fouler``, a team foul for his side, and his
        exit on the sixth; every path that calls this reaches a dead ball
        before play resumes, which is where the policy replaces him."""
        fouler.fouls += 1
        fouler.pf += 1
        defense.team_fouls[-1] += 1
        actors = {"fouler": fouler.id} if fouled is None else {"fouler": fouler.id, "fouled": fouled.id}
        self._emit("foul", defense.team_id, actors, zone, kind)
        if fouler.fouls >= FOUL_OUT:
            fouler.available = False
            self._emit("foul_out", defense.team_id, {"player": fouler.id})

    def _bonus(self, defense: _Side) -> bool:
        threshold = OVERTIME_BONUS_TEAM_FOULS if self.period > REGULATION_PERIODS else BONUS_TEAM_FOULS
        return defense.team_fouls[-1] >= threshold

    # -- the possession ----------------------------------------------------------

    def _possession(self, offense: _Side, defense: _Side, start: str) -> str:
        """One possession: the start type of the next one, which is the
        other team's. An offensive rebound or a non-shooting foul keeps the
        possession going; a possession that outlasts the clock ends the
        period."""
        offense.possessions += 1
        self._emit("possession", offense.team_id, {}, outcome=start)
        put_back = False
        while True:
            duration = self._duration(put_back)
            if duration >= self.clock:
                return self._last_action(offense, defense)
            fouled = self._non_shooting_foul(offense, defense, duration)
            if fouled in (_CONTINUE, _PUTBACK):
                put_back = fouled == _PUTBACK
                continue
            if fouled is not None:
                return fouled
            self._advance(duration)
            outcome = self._action(offense, defense, heave=False)
            if outcome is None:
                put_back = True
                continue
            return outcome

    def _last_action(self, offense: _Side, defense: _Side) -> str:
        """The possession the period ends in: a heave if it started with
        :data:`HEAVE_SECONDS` or less, a normal action otherwise, at the
        buzzer either way."""
        heave = self.clock <= HEAVE_SECONDS * _TENTHS
        self._note("last_action", "heave" if heave else "normal", {}, inputs={"clock": self.clock / _TENTHS, "heave_under": HEAVE_SECONDS})
        self._advance(self.clock)
        self._action(offense, defense, heave=heave)
        return _PERIOD_START

    def _non_shooting_foul(self, offense: _Side, defense: _Side, duration: int) -> str | None:
        """Maybe a defensive foul before the action: ``None`` when none,
        ``_CONTINUE`` when the offense keeps the ball from the dead ball,
        ``_PUTBACK`` when it rebounded the last bonus free throw, a start
        type when the bonus free throws ended the possession. Its
        probability per possession is the defenders' fouls per minute beyond
        the shooting fouls the five on offense draw, as a share of the team's
        possessions per minute (``pace / 48``), times
        :data:`NON_SHOOTING_DEFENSIVE_SHARE`."""
        per_minute = max(defense.fouls_pm - offense.trips_pm, 0.0)
        probability = per_minute * 48.0 / offense.pace * NON_SHOOTING_DEFENSIVE_SHARE if offense.pace > 0 else 0.0
        if probability <= 0.0 or not self._chance(
            "non_shooting_foul",
            probability,
            {"defense_fouls_per_minute": defense.fouls_pm, "offense_trips_per_minute": offense.trips_pm, "share": NON_SHOOTING_DEFENSIVE_SHARE, "team_fouls": float(defense.team_fouls[-1])},
        ):
            return None
        self._advance(int(self.rng.random() * duration))
        fouler = self._pick_noted("fouler", defense.floor, [p.foul_w for p in defense.floor], or_any=True)
        fouled = self._pick_noted("fouled", offense.floor, offense.usage, or_any=True)
        assert fouler is not None and fouled is not None
        self._charge_foul(defense, fouler, fouled, "non_shooting")
        if self._bonus(defense):
            ended = self._free_throws(offense, defense, fouled, 2)
            return _PUTBACK if ended is None else ended
        self._dead_ball()
        return _CONTINUE

    def _action(self, offense: _Side, defense: _Side, heave: bool) -> str | None:
        """The user and what he does; ``None`` when the possession goes on."""
        user = self._pick_noted("user", offense.floor, offense.usage, or_any=True)
        assert user is not None
        draw = self.rng.random()
        chosen = "turnover" if draw < user.p_to else ("trip" if draw < user.p_to + user.p_trip else "shot")
        self._note("action", chosen, {"shot": user.p_shot, "trip": user.p_trip, "turnover": user.p_to}, draw)
        if chosen == "turnover":
            return self._turnover(offense, defense, user)
        if chosen == "trip":
            return self._trip(offense, defense, user)
        return self._shot(offense, defense, user, heave)

    def _shot(self, offense: _Side, defense: _Side, shooter: _Player, heave: bool) -> str | None:
        zone = HEAVE if heave else self._zone_noted(shooter)
        make = HEAVE_MAKE_RATE if heave else shooter.make[zone]
        points = value_of(zone)
        shooter.fga += 1
        if points == 3:
            shooter.tpa += 1
        if self._chance("make", make, {"heave": 1.0} if heave else shooter.make_inputs[zone]):
            return self._made(offense, defense, shooter, zone, points)
        actors = {"shooter": shooter.id}
        outcome = "missed"
        if self._chance("block", BLOCK_SHARE_OF_MISSES):
            blocker = self._pick_noted("blocker", defense.floor, [p.blk_w for p in defense.floor])
            if blocker is not None:
                blocker.blk += 1
                actors["blocker"] = blocker.id
                outcome = "blocked"
        self._emit("shot", offense.team_id, actors, zone, outcome)
        return self._rebound(offense, defense)

    def _made(self, offense: _Side, defense: _Side, shooter: _Player, zone: str, points: int) -> str | None:
        shooter.fgm += 1
        if points == 3:
            shooter.tpm += 1
        self._score(offense, shooter, points)
        actors = {"shooter": shooter.id}
        if self._chance("assisted", shooter.assisted):
            assister = self._pick_noted("assister", offense.floor, [p.ast_w for p in offense.floor], skip=shooter)
            if assister is not None:
                assister.ast += 1
                actors["assister"] = assister.id
        self._emit("shot", offense.team_id, actors, zone, "made", points)
        if self._chance("and_one", shooter.and_one):
            fouler = self._pick_noted("fouler", defense.floor, [p.foul_w for p in defense.floor], or_any=True)
            assert fouler is not None
            self._charge_foul(defense, fouler, shooter, "and_one", zone)
            return self._free_throws(offense, defense, shooter, 1)
        return _MADE_SHOT

    def _rebound(self, offense: _Side, defense: _Side) -> str | None:
        """After a miss: ``None`` on an offensive rebound (the possession
        continues), the defensive-rebound start type otherwise. A side whose
        five carry no rebound weight takes a team rebound."""
        if self._chance("offensive_rebound", defense.oreb_against, {"league": self.league.offensive_rebound_rate}):
            rebounder = self._pick_noted("rebounder", offense.floor, [p.oreb_w for p in offense.floor])
            if rebounder is not None:
                rebounder.oreb += 1
            self._emit("rebound", offense.team_id, {} if rebounder is None else {"rebounder": rebounder.id}, outcome="offensive")
            return None
        rebounder = self._pick_noted("rebounder", defense.floor, [p.dreb_w for p in defense.floor])
        if rebounder is not None:
            rebounder.dreb += 1
        self._emit("rebound", defense.team_id, {} if rebounder is None else {"rebounder": rebounder.id}, outcome="defensive")
        return _DEFENSIVE_REBOUND

    def _turnover(self, offense: _Side, defense: _Side, user: _Player) -> str:
        user.tov += 1
        if self._chance("live_ball", user.live):
            stealer = self._pick_noted("stealer", defense.floor, [p.stl_w for p in defense.floor])
            actors = {"player": user.id}
            if stealer is not None:
                stealer.stl += 1
                actors["stealer"] = stealer.id
            self._emit("turnover", offense.team_id, actors, outcome="live")
            return _LIVE_TURNOVER
        self._emit("turnover", offense.team_id, {"player": user.id}, outcome="dead")
        self._dead_ball()
        return _DEAD_BALL

    def _trip(self, offense: _Side, defense: _Side, shooter: _Player) -> str | None:
        """A shooting foul on a miss: no attempt is charged; three free throws
        with probability :data:`THREE_SHOT_TRIP_SHARE`, else two. The zone
        recorded on the foul is a three-point zone for a three-shot trip and
        one of the shooter's two-point zones otherwise."""
        count = 3 if self._chance("three_shot_trip", THREE_SHOT_TRIP_SHARE) else 2
        zone = self._trip_zone(shooter, count)
        fouler = self._pick_noted("fouler", defense.floor, [p.foul_w for p in defense.floor], or_any=True)
        assert fouler is not None
        self._charge_foul(defense, fouler, shooter, "shooting", zone)
        return self._free_throws(offense, defense, shooter, count)

    def _trip_zone(self, shooter: _Player, count: int) -> str:
        """The zone a trip is recorded in: a three-point zone for three free
        throws; for two, the shooter's own mix redrawn until it gives a two
        (five tries, then the restricted area)."""
        if count == 3:
            return "above_break_three"
        for _ in range(5):
            zone = shooter.zone(self.rng)
            if value_of(zone) == 2:
                return zone
        return "restricted"

    def _free_throws(self, offense: _Side, defense: _Side, shooter: _Player, count: int) -> str | None:
        """``count`` free throws, the dead ball before the last; a made last
        one hands the ball over, a missed one is a rebound."""
        for attempt in range(1, count + 1):
            if attempt == count:
                self._dead_ball()
            shooter.fta += 1
            if self._chance("free_throw", shooter.ft, {"attempt": float(attempt), "of": float(count)}):
                shooter.ftm += 1
                self._score(offense, shooter, 1)
                self._emit("free_throw", offense.team_id, {"shooter": shooter.id}, outcome="made", points=1)
                if attempt == count:
                    return _MADE_FREE_THROW
            else:
                self._emit("free_throw", offense.team_id, {"shooter": shooter.id}, outcome="missed")
                if attempt == count:
                    return self._rebound(offense, defense)
        raise AssertionError("a trip has at least one free throw")

    # -- periods and the game -------------------------------------------------------

    def _play_period(self, period: int, length_seconds: int, offense: _Side) -> None:
        self.period_started_at = self.elapsed
        self.period = period
        self.period_length = length_seconds * _TENTHS
        self.clock = self.period_length
        self.home.team_fouls.append(0)
        self.away.team_fouls.append(0)
        self._emit("period_start", "", {})
        self._dead_ball()
        defense = self.away if offense is self.home else self.home
        start = _PERIOD_START
        while self.clock > 0:
            start = self._possession(offense, defense, start)
            offense, defense = defense, offense
            if self.debug:
                self._check()
        self._emit("period_end", "", {})

    def _check(self) -> None:
        for side in (self.home, self.away):
            ids = {p.id for p in side.floor}
            if len(side.floor) != 5 or len(ids) != 5:
                raise InvariantError(f"team {side.team_id} has {len(side.floor)} on the floor in period {self.period}")
            if any(p.fouls >= FOUL_OUT for p in side.floor):
                raise InvariantError(f"team {side.team_id} has a fouled-out player on the floor in period {self.period}")
        if self.home.score + self.away.score != self.event_points:
            raise InvariantError(f"the score {self.home.score}-{self.away.score} is not the events' {self.event_points} points")

    def play(self) -> None:
        """Four periods, then overtime until the score differs. The team that
        wins the opening tip (a coin flip) opens periods 1 and 4, the loser
        periods 2 and 3; each overtime opens with its own flip."""
        tip = self.home if self.rng.random() < 0.5 else self.away
        loser = self.away if tip is self.home else self.home
        for period, opener in enumerate((tip, loser, loser, tip), start=1):
            self._play_period(period, REGULATION_PERIOD_SECONDS, opener)
        while self.home.score == self.away.score:
            if self.period - REGULATION_PERIODS >= MAX_OVERTIMES:
                raise EngineError(f"still tied after {MAX_OVERTIMES} overtimes: the inputs cannot break a tie")
            self._play_period(self.period + 1, OVERTIME_PERIOD_SECONDS, self.home if self.rng.random() < 0.5 else self.away)
        for side in (self.home, self.away):
            for player in list(side.floor):
                self._leave(side, player)

    def result(self) -> SimGame:
        """The game as data."""
        lines = tuple(self._line(side, player) for side in (self.home, self.away) for player in side.roster)
        return SimGame(
            self.seed,
            self.home.team_id,
            self.away.team_id,
            self.home.score,
            self.away.score,
            self.period,
            tuple(self.events),
            lines,
            self._totals(self.home, lines),
            self._totals(self.away, lines),
            tuple(self.trace or ()),
        )

    @staticmethod
    def _line(side: _Side, p: _Player) -> BoxLine:
        return BoxLine(p.id, side.team_id, p.played / _TENTHS, p.points, p.fgm, p.fga, p.tpm, p.tpa, p.ftm, p.fta, p.oreb, p.dreb, p.ast, p.stl, p.blk, p.tov, p.pf, p.plus_minus)

    @staticmethod
    def _totals(side: _Side, lines: Sequence[BoxLine]) -> TeamTotals:
        own = [line for line in lines if line.team_id == side.team_id]
        return TeamTotals(
            side.team_id,
            side.score,
            sum(x.fgm for x in own),
            sum(x.fga for x in own),
            sum(x.tpm for x in own),
            sum(x.tpa for x in own),
            sum(x.ftm for x in own),
            sum(x.fta for x in own),
            sum(x.oreb for x in own),
            sum(x.dreb for x in own),
            sum(x.ast for x in own),
            sum(x.stl for x in own),
            sum(x.blk for x in own),
            sum(x.tov for x in own),
            sum(x.pf for x in own),
            side.possessions,
            tuple(side.team_fouls),
            sum(p.played for p in side.roster) / _TENTHS,
        )


def simulate(setup: GameSetup, seed: int, debug: bool = False, trace: bool = False) -> SimGame:
    """Play one game from ``setup`` with ``random.Random(seed)``: the same
    seed and inputs give an equal :class:`~association.sim.events.SimGame`.
    ``debug`` checks the invariants after every possession (five a side, no
    fouled-out player on the floor, the score equal to the events' points)
    and raises :class:`InvariantError` on the first failure. ``trace``
    records every draw as a :class:`~association.sim.events.Decision` on
    ``SimGame.decisions`` - the candidates, their probabilities, the inputs
    and the uniform draw - without changing the game: tracing consumes no
    randomness, so a traced game equals the untraced one event for event.

    .. versionadded:: 6.0.0
    """
    if setup.home.team.team_id == setup.away.team.team_id:
        raise EngineError(f"a team cannot play itself ({setup.home.team.team_id})")
    game = _Game(setup, seed, debug, trace)
    game.play()
    return game.result()
