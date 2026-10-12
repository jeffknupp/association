"""The real-minutes diagnostic: the policy follows the real segments by the
clock, fills for a player who cannot play, and the prepared replay carries
it instead of the rotation model."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import toy

from association.sim import game as matchup
from association.sim import rotation
from association.sim.engine import GameSetup, TeamSetup, simulate
from association.sim.params import PlayerGameState, RotationContext
from association.sim.real_minutes import RealLineups, describe
from association.sim.render import Names

FIVE = ("h1", "h2", "h3", "h4", "h5")
BENCH_FIVE = ("h6", "h7", "h8", "h4", "h5")
SEGMENTS = ((0.0, 300.0, FIVE), (300.0, 720.0, BENCH_FIVE), (720.0, 2880.0, FIVE))


def _context(elapsed: float, unavailable: frozenset[str] = frozenset(), on: tuple[str, ...] = FIVE) -> RotationContext:
    players = tuple(PlayerGameState(a, a in on, 0.0, 0.0, 0, a not in unavailable) for a in ("h1", "h2", "h3", "h4", "h5", "h6", "h7", "h8", "h9", "h10"))
    return RotationContext("home", 1, 720.0 - elapsed, elapsed, 0, players)


def test_the_policy_follows_the_segments_by_the_clock() -> None:
    policy = RealLineups("home", SEGMENTS)
    assert policy.at(0.0) == FIVE and policy.at(299.9) == FIVE and policy.at(300.0) == BENCH_FIVE and policy.at(719.0) == BENCH_FIVE
    assert policy.at(1000.0) == FIVE
    assert policy.at(5000.0) == FIVE  # a simulated overtime the real game did not have keeps the last lineup
    assert policy.lineup(_context(310.0)) == BENCH_FIVE
    assert policy.remaining(0.0)["h1"] == 300.0 + 2160.0 and policy.remaining(0.0)["h6"] == 420.0 and policy.remaining(2000.0)["h1"] == 880.0


def test_a_player_who_cannot_play_is_replaced_by_the_one_with_the_most_real_seconds_to_come() -> None:
    policy = RealLineups("home", SEGMENTS)
    # h1 fouled out at 100 s: the bench player with the most real seconds after 100 s is h6/h7/h8 (420 each); roster order breaks the tie.
    assert policy.lineup(_context(100.0, unavailable=frozenset({"h1"}))) == ("h2", "h3", "h4", "h5", "h6")
    # A short segment (an unresolved one) is filled the same way.
    short = RealLineups("home", ((0.0, 2880.0, ("h1", "h2", "h3")),))
    assert short.lineup(_context(10.0)) == ("h1", "h2", "h3", "h4", "h5")
    with pytest.raises(ValueError, match="no real segments"):
        RealLineups("home", ())


def test_a_prepared_replay_with_policies_uses_them_and_the_minutes_follow() -> None:
    home_players, away_players = toy.roster("home"), toy.roster("away")
    h = tuple(home_players)
    a = tuple(away_players)
    home_segments = ((0.0, 1440.0, h[:5]), (1440.0, 2880.0, h[5:10]))
    away_segments = ((0.0, 2880.0, a[:5]),)
    policies = {"home": RealLineups("home", home_segments), "away": RealLineups("away", away_segments)}
    setup = GameSetup(
        home=TeamSetup(team=toy.team("home", "Home"), players=home_players, starters=h[:5], policy=policies["home"]),
        away=TeamSetup(team=toy.team("away", "Away"), players=away_players, starters=a[:5], policy=policies["away"]),
        league=toy.league(),
    )
    game = simulate(setup, 3)
    seconds = {line.athlete_id: line.minutes * 60 for line in game.lines}
    # Each home starter plays the first half, each reserve the second, give or take a dead ball's wait and overtime.
    extra = 300.0 * game.overtimes
    for athlete in h[:5]:
        assert 1440.0 - 30.0 <= seconds[athlete] <= 1440.0 + 30.0
    for athlete in h[5:10]:
        assert 1440.0 - 30.0 <= seconds[athlete] <= 1440.0 + 30.0 + extra
    assert all(seconds[athlete] >= 2880.0 - 30.0 for athlete in a[:5])
    assert "diagnostic" in describe(list(policies.values())) and "home 2 segments" in describe(list(policies.values()))
    # Prepared carries policies through setup().
    prepared = matchup.Prepared(
        matchup.Roster("home", h, h[:5], "fixture"),
        matchup.Roster("away", a, a[:5], "fixture"),
        toy.league().window,
        toy.league(),
        {"home": toy.team("home", "Home"), "away": toy.team("away", "Away")},
        {**home_players, **away_players},
        {},
        rotation.NEUTRAL_LEAGUE,
        Names(players={}, abbreviations={}, teams={}),
        (),
        policies,
    )
    assert prepared.setup().home.policy is policies["home"]
