"""The parameters a simulated game is played from: one record per player, per
team and per league window, every rate carrying the evidence behind it.

Phase 4's types (``SIMULATION_PLAN.md``, P4.2, and decision D15: the first
estimation is from box scores and the shot chart, per minute on the floor,
with team-level defense; the possession table upgrades it later). They are
declared here, before any estimator exists, so the estimator (step 4), the
engine (step 5) and the rotation model (step 6) can be built against one
fixed shape in parallel. Frozen dataclasses with full annotations
(``pyright --verifytypes`` holds the package at 100%).

Three conventions, each a lesson from the main project:

- **A rate remembers its trials and its prior** (:class:`Rate`). The output
  must say when a player was simulated at nearly the league prior
  (``AGENTS.md``: a default must say what it did), which is only possible if
  the rate itself knows how much of it is prior. ``Rate.near_prior`` is that
  test, against :data:`MIN_TRIALS`.
- **Zones are named once** (:data:`ZONES`, :data:`HEAVE`, the unknown-zone
  names). The geometry that assigns them is step 4's ``zones.py``; the names
  are here because every record keys on them.
- **Per minute, not per possession, in v1.** Without the possession table
  the natural unit of a player's usage is his minutes on the floor, and a
  lineup's rates per possession follow from the team's pace. The names say
  so (``..._per_minute``) so a later per-possession estimate cannot be
  mistaken for one.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

ZONES: tuple[str, ...] = ("restricted", "paint", "midrange", "corner_three", "above_break_three")
"""The five zones a shot is modeled in, rim-origin: the restricted area
(under 4 feet), the paint outside it (4 to 14 feet), mid-range (14 feet to
the arc), the corner three and the above-the-break three.

.. versionadded:: 6.0.0
"""

HEAVE = "heave"
"""A three from beyond 35 feet with under 3 seconds on the period clock:
modeled only in end-of-period logic, excluded from every shooting rate.

.. versionadded:: 6.0.0
"""

UNKNOWN_TWO = "unknown_two"
"""A two with no court position: counts toward usage, never toward a zone.

.. versionadded:: 6.0.0
"""

UNKNOWN_THREE = "unknown_three"
"""A three with no court position: counts toward usage, never toward a zone.

.. versionadded:: 6.0.0
"""

THREE_POINT_ZONES: frozenset[str] = frozenset({"corner_three", "above_break_three"})
"""The zones worth three.

.. versionadded:: 6.0.0
"""

MIN_TRIALS = 100
"""Below this many trials a rate is reported as near the league prior and the
output says so (the plan's refusal rule: zero trials refuses by name, few
trials simulates at nearly the prior and tells the reader).

.. versionadded:: 6.0.0
"""


def value_of(zone: str) -> int:
    """Points a made shot in ``zone`` is worth: 3 beyond the arc (the heave
    and the unknown three included), 2 otherwise.

    .. versionadded:: 6.0.0
    """
    return 3 if zone in THREE_POINT_ZONES or zone in (HEAVE, UNKNOWN_THREE) else 2


@dataclass(frozen=True)
class Rate:
    """A shrunk probability or rate with the evidence it came from.

    ``value`` is the posterior mean, ``trials`` the player's own trials,
    ``prior`` the league rate it was shrunk toward and ``strength`` the
    prior's weight in pseudo-trials.

    .. versionadded:: 6.0.0
    """

    value: float
    trials: float
    prior: float
    strength: float

    @property
    def near_prior(self) -> bool:
        """Whether the value is mostly the prior's: fewer than :data:`MIN_TRIALS` trials."""
        return self.trials < MIN_TRIALS

    @property
    def prior_weight(self) -> float:
        """The share of the value that is the prior: ``strength / (trials + strength)``."""
        total = self.trials + self.strength
        return self.strength / total if total > 0 else 1.0


@dataclass(frozen=True)
class Mix:
    """A shrunk categorical distribution with the evidence it came from.

    .. versionadded:: 6.0.0
    """

    probabilities: Mapping[str, float]
    trials: float
    strength: float

    @property
    def near_prior(self) -> bool:
        """Whether the mix is mostly the prior's: fewer than :data:`MIN_TRIALS` trials."""
        return self.trials < MIN_TRIALS

    def probability(self, category: str) -> float:
        """The probability of ``category``, 0 for one the mix lacks."""
        return self.probabilities.get(category, 0.0)


SEASON_DECAY = 0.5
"""The default weight each season back in a window carries relative to the
one after it: the latest season counts in full, the one before it half, the
one before that a quarter. Measured 2026-10-11
(``scripts/sim_p8/season_weights.py``): several league rates trend a point
a season (the offensive-rebound share, threes per attempt), and against the
held-out rates of 2024-2026 from January 1 the mean relative error of an
equal-weighted three-season window is 1.89%, of decays 0.5 / 0.35 / 0.25
1.61% / 1.55% / 1.59%, of the latest season alone 2.09%; 0.5 is in the
flat minimum and keeps the most of a player's sample.

.. versionadded:: 6.0.0
"""


@dataclass(frozen=True)
class Window:
    """Which games a rate was estimated from: those strictly before ``as_of``
    in the seasons listed (decision D5, point-in-time), each season's counts
    weighted ``season_decay`` to the power of its distance from the latest
    season, so the latest counts in full and each season back counts less
    (:data:`SEASON_DECAY` by default; ``1.0`` weights them equally). A replay
    passes the replayed game's date; a "fun" simulation the end of the
    latest season.

    .. versionadded:: 6.0.0
    """

    seasons: tuple[int, ...]
    as_of: dt.date
    season_decay: float = SEASON_DECAY

    def weight(self, season: int) -> float:
        """The weight on ``season``'s counts: ``season_decay ** (latest -
        season)``; ``1.0`` for the latest season."""
        return self.season_decay ** (max(self.seasons) - season)

    def describe(self) -> str:
        """``"2024-2026 through 2026-04-13, weighted 0.25 / 0.5 / 1"``, for
        the output; a one-season or equal-weighted window says nothing of
        weights."""
        first, last = min(self.seasons), max(self.seasons)
        span = str(first) if first == last else f"{first}-{last}"
        text = f"{span} through {self.as_of.isoformat()}"
        if first != last and self.season_decay != 1.0:
            text += ", weighted " + " / ".join(f"{self.weight(s):g}" for s in sorted(set(self.seasons)))
        return text


@dataclass(frozen=True)
class PlayerParams:
    """One player's rates for the engine, estimated per minute on the floor
    (v1) and shrunk toward the league (:class:`LeagueParams`).

    Usage: shots, free-throw trips (shooting fouls drawn) and turnovers per
    minute; the engine normalizes them over the five on the floor to choose
    who uses a possession and how. Shooting: the zone mix given a shot, the
    make rate by zone, the and-one and assisted rates on a make, the
    free-throw rate. Rebounding, assisting, stealing and blocking as weights
    per minute, normalized among the players on the floor when the event
    happens. Fouling per minute for the defense's foul draw.

    .. versionadded:: 6.0.0
    """

    athlete_id: str
    name: str
    team_id: str
    minutes: float  # minutes on the floor in the window, the evidence behind every per-minute rate
    shots_per_minute: Rate
    free_throw_trips_per_minute: Rate
    turnovers_per_minute: Rate
    zone_mix: Mix
    make_rate: Mapping[str, Rate]  # by zone
    and_one_rate: Rate
    assisted_rate: Rate
    free_throw_rate: Rate
    live_ball_share: Rate
    assists_per_minute: Rate
    offensive_rebounds_per_minute: Rate
    defensive_rebounds_per_minute: Rate
    steals_per_minute: Rate
    blocks_per_minute: Rate
    fouls_per_minute: Rate

    @property
    def near_prior(self) -> bool:
        """Whether the player is simulated at nearly the league prior: fewer
        than :data:`MIN_TRIALS` shots in the window. The output says so.

        Read off the zone mix, whose trials are his charted shots: a
        per-minute rate's trials are his MINUTES (the denominator the rate
        is shrunk over), so ``shots_per_minute.trials`` would test minutes,
        not shots.

        .. versionchanged:: 6.0.0
           Reads ``zone_mix.trials``, the shots, where it read
           ``shots_per_minute.trials``, the minutes (step 4, the estimator).
        """
        return self.zone_mix.trials < MIN_TRIALS

    def make_rate_in(self, zone: str) -> Rate:
        """The make rate in ``zone``; a zone the record lacks is an error,
        since every zone in :data:`ZONES` is estimated or shrunk to the prior."""
        return self.make_rate[zone]


@dataclass(frozen=True)
class TeamParams:
    """One team's rates: its pace and, in v1 (decision D6, option 1), its
    team-level defense as the rates it allows.

    .. versionadded:: 6.0.0
    """

    team_id: str
    name: str
    pace: Rate  # possessions per 48 minutes
    allowed_make_rate: Mapping[str, Rate]  # by zone, what opponents shoot against this defense
    forced_turnover_rate: Rate  # opponent turnovers per opponent possession
    foul_rate: Rate  # opponent free-throw trips per opponent possession
    defensive_rebound_rate: Rate  # share of opponents' misses this team rebounds
    allowed_zone_mix: Mix  # where opponents shoot from against this defense


@dataclass(frozen=True)
class LeagueParams:
    """The league's rates in the window - every prior the player and team
    rates shrink toward - and the fitted strength of each prior.

    ``strengths`` is keyed by the rate's field name (``"make_rate.restricted"``
    for a zone's); a strength the window did not fit falls back to
    :data:`DEFAULT_STRENGTH`. ``per_minute`` holds the league's per-minute
    rates by the player field they are the prior for (``"shots_per_minute"``,
    ``"fouls_per_minute"``, ...), which the per-possession fields here do not
    express and the estimator (step 4) fills.

    .. versionadded:: 6.0.0
    """

    window: Window
    pace: float
    make_rate: Mapping[str, float]  # by zone
    zone_mix: Mapping[str, float]
    and_one_rate: float
    assisted_rate: float
    free_throw_rate: float
    live_ball_share: float
    turnover_rate: float  # turnovers per possession
    foul_rate: float  # free-throw trips per possession
    offensive_rebound_rate: float  # share of misses the offense rebounds
    home_court: float  # points per game the home side is better by, applied as an efficiency multiplier
    strengths: Mapping[str, float] = field(default_factory=dict)
    per_minute: Mapping[str, float] = field(default_factory=dict)

    def strength(self, name: str) -> float:
        """The fitted prior strength for ``name``, or :data:`DEFAULT_STRENGTH`."""
        return self.strengths.get(name, DEFAULT_STRENGTH)

    def per_minute_rate(self, name: str) -> float:
        """The league's per-minute rate a player's ``name`` field shrinks toward
        (``"shots_per_minute"``, ``"assists_per_minute"``, ...): the prior for a
        player the window never saw. A name the window did not estimate is an
        error, since no sensible default exists for it.

        .. versionadded:: 6.0.0
        """
        return self.per_minute[name]


@dataclass(frozen=True)
class PlayerGameState:
    """What a rotation policy sees about one player at a dead ball: whether
    he is on the floor, how long he has played in all and in his current
    stint, his personal fouls, and whether he can still play (not fouled
    out, not ejected, not marked unavailable by the caller).

    .. versionadded:: 6.0.0
    """

    athlete_id: str
    on_floor: bool
    seconds_played: float
    stint_seconds: float
    fouls: int
    available: bool


@dataclass(frozen=True)
class RotationContext:
    """One team's situation at a dead ball, handed to its rotation policy:
    the period and clock, the seconds elapsed from tip-off, this team's lead
    (negative when behind) and every rostered player's state.

    .. versionadded:: 6.0.0
    """

    team_id: str
    period: int
    clock_remaining: float
    elapsed: float
    margin: int
    players: tuple[PlayerGameState, ...]

    def on_floor(self) -> tuple[str, ...]:
        """The five on the floor now, in roster order."""
        return tuple(p.athlete_id for p in self.players if p.on_floor)


class RotationPolicy(Protocol):
    """The contract between the engine (step 5) and the rotation model
    (step 6): at every dead ball the engine asks each team's policy for the
    five who should be on the floor, and substitutes for the difference.

    A policy returns exactly five distinct athlete ids from the context's
    ``available`` players. Returning the current five means no change.
    The engine ships a trivial policy (the starters, replaced only on a
    foul-out) so it runs before the rotation model exists; the rotation
    model is the real one.

    .. versionadded:: 6.0.0
    """

    def lineup(self, context: RotationContext) -> tuple[str, ...]:
        """The five to have on the floor from this dead ball on."""
        ...


DEFAULT_STRENGTH = 50.0
"""Pseudo-trials for a prior no window has fitted a strength for: enough to
hold a handful of games near the league, little enough that a season speaks.

.. versionadded:: 6.0.0
"""
