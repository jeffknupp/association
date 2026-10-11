"""Hand-written parameters for the engine's tests and scripts: a toy league
near 2025-26's rates, two ten-man rosters of three archetypes, and the
degenerate teams the plan's tests name.

Every number here is a round figure chosen by hand, not an estimate; the
estimator (step 4) replaces them. They are near the league so the sanity
check (``scripts/sim_p5/sanity.py``) is readable against the real league,
and the functions take overrides so a test can make one thing extreme.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Any

from association.sim.engine import GameSetup, StartersPolicy, TeamSetup
from association.sim.params import ZONES, LeagueParams, Mix, PlayerParams, Rate, TeamParams, Window

MAKE_RATE: Mapping[str, float] = {"restricted": 0.65, "paint": 0.42, "midrange": 0.41, "corner_three": 0.39, "above_break_three": 0.355}
ZONE_MIX: Mapping[str, float] = {"restricted": 0.32, "paint": 0.16, "midrange": 0.12, "corner_three": 0.09, "above_break_three": 0.31}
PACE = 99.0
TURNOVER_RATE = 0.135
FOUL_RATE = 0.10
OREB_RATE = 0.25
FREE_THROW_RATE = 0.78

# Per player-minute, league average: about 88 field-goal attempts, 10 trips, 13.5 turnovers, 26 assists, 10.5
# offensive and 34 defensive rebounds, 7.5 steals, 5 blocks and 19 fouls a team-game, over 240 player-minutes.
USAGE_PER_MINUTE = {"shots": 0.367, "trips": 0.042, "turnovers": 0.056}
WEIGHTS_PER_MINUTE = {"assists": 0.108, "oreb": 0.044, "dreb": 0.14, "steals": 0.031, "blocks": 0.021, "fouls": 0.08}

# Archetype by roster slot: who plays where in the toy roster of ten.
ARCHETYPES = ("guard", "guard", "wing", "wing", "big", "guard", "wing", "big", "guard", "big")
ARCHETYPE_ZONE_MIX: Mapping[str, Mapping[str, float]] = {
    "guard": {"restricted": 0.22, "paint": 0.14, "midrange": 0.14, "corner_three": 0.08, "above_break_three": 0.42},
    "wing": {"restricted": 0.30, "paint": 0.15, "midrange": 0.12, "corner_three": 0.12, "above_break_three": 0.31},
    "big": {"restricted": 0.58, "paint": 0.22, "midrange": 0.08, "corner_three": 0.03, "above_break_three": 0.09},
}
ARCHETYPE_SCALE: Mapping[str, Mapping[str, float]] = {
    # Multipliers on the league's per-minute weights by archetype; the roster's ten average near one.
    "guard": {"assists": 1.6, "oreb": 0.5, "dreb": 0.7, "steals": 1.3, "blocks": 0.4, "fouls": 0.9},
    "wing": {"assists": 0.9, "oreb": 0.9, "dreb": 1.0, "steals": 1.0, "blocks": 0.8, "fouls": 1.0},
    "big": {"assists": 0.5, "oreb": 1.9, "dreb": 1.5, "steals": 0.6, "blocks": 2.2, "fouls": 1.2},
}


def rate(value: float, trials: float = 1000.0) -> Rate:
    """A rate with plenty of trials and itself as prior: a toy is not shrunk."""
    return Rate(value=value, trials=trials, prior=value, strength=100.0)


def mix(probabilities: Mapping[str, float]) -> Mix:
    """A mix with plenty of trials."""
    return Mix(probabilities=dict(probabilities), trials=1000.0, strength=100.0)


def league(home_court: float = 2.5, pace: float = PACE) -> LeagueParams:
    """The toy league, near 2025-26."""
    return LeagueParams(
        window=Window((2026,), dt.date(2026, 4, 13)),
        pace=pace,
        make_rate=dict(MAKE_RATE),
        zone_mix=dict(ZONE_MIX),
        and_one_rate=0.04,
        assisted_rate=0.60,
        free_throw_rate=FREE_THROW_RATE,
        live_ball_share=0.55,
        turnover_rate=TURNOVER_RATE,
        foul_rate=FOUL_RATE,
        offensive_rebound_rate=OREB_RATE,
        home_court=home_court,
    )


def team(team_id: str, name: str, pace: float = PACE, defense: float = 1.0, forced_turnovers: float = TURNOVER_RATE, fouls: float = FOUL_RATE, dreb: float = 1 - OREB_RATE) -> TeamParams:
    """A team whose defense allows the league's rates times ``defense``
    (under one is a better defense), with the league's zone mix."""
    return TeamParams(
        team_id=team_id,
        name=name,
        pace=rate(pace),
        allowed_make_rate={z: rate(MAKE_RATE[z] * defense) for z in ZONES},
        forced_turnover_rate=rate(forced_turnovers),
        foul_rate=rate(fouls),
        defensive_rebound_rate=rate(dreb),
        allowed_zone_mix=mix(ZONE_MIX),
    )


def player(
    athlete_id: str,
    team_id: str,
    archetype: str = "wing",
    usage: float = 1.0,
    shooting: float = 1.0,
    *,
    shots: float | None = None,
    trips: float | None = None,
    turnovers: float | None = None,
    make_rate: Mapping[str, float] | None = None,
    and_one: float = 0.04,
    assisted: float = 0.60,
    free_throws: float = FREE_THROW_RATE,
    live_ball: float = 0.55,
    fouls: float | None = None,
    zone_mix: Mapping[str, float] | None = None,
) -> PlayerParams:
    """One toy player: the league's per-minute figures scaled by ``usage``
    (his share of the ball) and his archetype, the make rates by
    ``shooting``; any keyword overrides the figure outright."""
    scale = ARCHETYPE_SCALE[archetype]
    return PlayerParams(
        athlete_id=athlete_id,
        name=f"Player {athlete_id}",
        team_id=team_id,
        minutes=1500.0,
        shots_per_minute=rate(USAGE_PER_MINUTE["shots"] * usage if shots is None else shots),
        free_throw_trips_per_minute=rate(USAGE_PER_MINUTE["trips"] * usage if trips is None else trips),
        turnovers_per_minute=rate(USAGE_PER_MINUTE["turnovers"] * usage if turnovers is None else turnovers),
        zone_mix=mix(ARCHETYPE_ZONE_MIX[archetype] if zone_mix is None else zone_mix),
        make_rate={z: rate(MAKE_RATE[z] * shooting) for z in ZONES} if make_rate is None else {z: rate(make_rate[z]) for z in ZONES},
        and_one_rate=rate(and_one),
        assisted_rate=rate(assisted),
        free_throw_rate=rate(free_throws),
        live_ball_share=rate(live_ball),
        assists_per_minute=rate(WEIGHTS_PER_MINUTE["assists"] * scale["assists"]),
        offensive_rebounds_per_minute=rate(WEIGHTS_PER_MINUTE["oreb"] * scale["oreb"]),
        defensive_rebounds_per_minute=rate(WEIGHTS_PER_MINUTE["dreb"] * scale["dreb"]),
        steals_per_minute=rate(WEIGHTS_PER_MINUTE["steals"] * scale["steals"]),
        blocks_per_minute=rate(WEIGHTS_PER_MINUTE["blocks"] * scale["blocks"]),
        fouls_per_minute=rate(WEIGHTS_PER_MINUTE["fouls"] * scale["fouls"] if fouls is None else fouls),
    )


# The ten's share of the ball: two stars, three starters, five reserves; the starting five average about 1.1.
USAGE_BY_SLOT = (1.5, 1.2, 1.0, 0.9, 0.9, 0.9, 0.8, 0.7, 0.7, 0.6)


def roster(team_id: str, size: int = 10, **overrides: Any) -> dict[str, PlayerParams]:
    """``size`` toy players for ``team_id`` (``"<team_id>-1"`` and so on),
    the first five the starters, with ``overrides`` passed to every
    :func:`player`."""
    return {f"{team_id}-{i + 1}": player(f"{team_id}-{i + 1}", team_id, ARCHETYPES[i % len(ARCHETYPES)], USAGE_BY_SLOT[i % len(USAGE_BY_SLOT)], **overrides) for i in range(size)}


def side(team_id: str, players: Mapping[str, PlayerParams] | None = None, team_params: TeamParams | None = None) -> TeamSetup:
    """A side with the starters policy."""
    players = roster(team_id) if players is None else players
    return TeamSetup(team=team(team_id, team_id.title()) if team_params is None else team_params, players=players, starters=tuple(players)[:5], policy=StartersPolicy())


def setup(home_court: float = 2.5) -> GameSetup:
    """The toy matchup: the home side a slightly better defense, the away side a quicker pace."""
    home = side("home", team_params=team("home", "Home", defense=0.97))
    away = side("away", team_params=team("away", "Away", pace=PACE + 2))
    return GameSetup(home=home, away=away, league=league(home_court))


def perfect_shooters(team_id: str) -> dict[str, PlayerParams]:
    """A team that never misses, never turns it over, never draws a foul and
    never fouls - every possession of theirs is a made two or three."""
    return roster(team_id, trips=0.0, turnovers=0.0, make_rate=dict.fromkeys(ZONES, 1.0), and_one=0.0, fouls=0.0)


def never_shoots(team_id: str) -> dict[str, PlayerParams]:
    """A team whose every use is a turnover, and which never fouls (so two of
    them can never score: no bonus free throws)."""
    return roster(team_id, shots=0.0, trips=0.0, turnovers=0.1, fouls=0.0)


def low_scoring(team_id: str) -> dict[str, PlayerParams]:
    """A team that scores about four points a game, so ties and overtime are common."""
    return roster(team_id, shots=0.01, trips=0.0, turnovers=0.1, fouls=0.0)


def foul_prone(team_id: str, fouls: float = 0.18) -> dict[str, PlayerParams]:
    """A team whose players foul out in about 33 minutes on the floor: the five starters go in the second half and the bench finishes."""
    return roster(team_id, fouls=fouls)


def with_players(team_id: str, players: Mapping[str, PlayerParams]) -> TeamSetup:
    """A side built from ``players`` under the toy team parameters."""
    return side(team_id, players)
