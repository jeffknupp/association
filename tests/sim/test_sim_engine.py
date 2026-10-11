"""The engine: deterministic, its invariants over hundreds of seeds, the
degenerate teams the plan names, foul-outs, the starters policy and
overtime - all from the toy parameters in ``toy.py``, offline."""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import toy

from association.sim import engine, shrink
from association.sim.engine import EngineError, GameSetup, InvariantError, StartersPolicy, TeamSetup, simulate
from association.sim.events import EVENT_KINDS, SimGame
from association.sim.params import HEAVE, PlayerGameState, RotationContext
from association.sim.plays import OVERTIME_PERIOD_SECONDS, REGULATION_PERIOD_SECONDS


def _points_by_player(game: SimGame) -> dict[str, int]:
    points: dict[str, int] = defaultdict(int)
    for event in game.events:
        if event.points:
            points[event.actors["shooter"]] += event.points
    return points


def _game_seconds(game: SimGame) -> int:
    return 4 * REGULATION_PERIOD_SECONDS + game.overtimes * OVERTIME_PERIOD_SECONDS


def _check_invariants(game: SimGame) -> None:
    """The plan's invariants, read off the finished game (the ones that need
    the running state are ``debug``'s)."""
    assert {e.kind for e in game.events} <= EVENT_KINDS
    # The score is the sum of the scoring events' points, and the running score ends at the final.
    assert game.home_score + game.away_score == sum(e.points for e in game.events)
    assert (game.events[-1].home_score, game.events[-1].away_score) == (game.home_score, game.away_score)
    assert game.home.points == game.home_score
    assert game.away.points == game.away_score
    # Team minutes sum to 240, plus 25 per overtime, and the box lines agree.
    for totals in (game.home, game.away):
        assert totals.seconds == 5 * _game_seconds(game)
        assert sum(line.seconds for line in game.lines if line.team_id == totals.team_id) == pytest.approx(totals.seconds)
    # Each player's box points are his events' points.
    by_player = _points_by_player(game)
    for line in game.lines:
        assert line.points == by_player.get(line.athlete_id, 0)
        assert line.points == 2 * (line.fgm - line.tpm) + 3 * line.tpm + line.ftm
        assert line.pf <= engine.FOUL_OUT
    assert sum(line.plus_minus for line in game.lines if line.team_id == game.home_team) == 5 * (game.home_score - game.away_score)
    # Possession alternates within a period (an offensive rebound continues a possession and opens no new one).
    previous: dict[int, str] = {}
    for event in game.events:
        if event.kind == "possession":
            assert previous.get(event.period) != event.team
            previous[event.period] = event.team
    assert game.periods == max(e.period for e in game.events)
    assert game.home_score != game.away_score


def test_the_same_seed_gives_the_same_game() -> None:
    setup = toy.setup()
    first, second = simulate(setup, 7), simulate(setup, 7)
    assert first == second
    assert first.events == second.events
    assert simulate(setup, 8) != first


def test_the_invariants_hold_over_five_hundred_seeds() -> None:
    setup = toy.setup()
    overtimes = 0
    for seed in range(500):
        game = simulate(setup, seed, debug=True)
        _check_invariants(game)
        overtimes += game.overtimes
    assert overtimes > 0, "no overtime in 500 games would be a surprise"


def test_the_box_lines_hold_every_count_and_a_bench_player_has_zeros() -> None:
    game = simulate(toy.setup(), 3)
    assert (game.home_team, game.away_team, game.seed) == ("home", "away", 3)
    starter = next(line for line in game.lines if line.athlete_id == "home-1")
    assert starter.minutes == pytest.approx(48 + 5 * game.overtimes) or starter.pf == engine.FOUL_OUT
    assert starter.fga >= starter.fgm >= 0 and starter.tpa >= starter.tpm and starter.fta >= starter.ftm
    reserve = next(line for line in game.lines if line.athlete_id == "home-10")
    assert (reserve.seconds, reserve.points, reserve.fga, reserve.oreb, reserve.dreb, reserve.ast, reserve.stl, reserve.blk, reserve.tov, reserve.plus_minus) == (0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    totals = game.home
    assert (totals.fgm, totals.fga, totals.tpm, totals.tpa, totals.ftm, totals.fta) == tuple(
        sum(getattr(line, f) for line in game.lines if line.team_id == "home") for f in ("fgm", "fga", "tpm", "tpa", "ftm", "fta")
    )
    assert (totals.oreb, totals.dreb, totals.ast, totals.stl, totals.blk, totals.tov, totals.pf) == tuple(
        sum(getattr(line, f) for line in game.lines if line.team_id == "home") for f in ("oreb", "dreb", "ast", "stl", "blk", "tov", "pf")
    )
    assert len(totals.team_fouls) == game.periods
    assert totals.possessions == sum(1 for e in game.events if e.kind == "possession" and e.team == "home")


def test_a_team_that_never_misses_scores_two_or_three_every_possession() -> None:
    setup = GameSetup(home=toy.with_players("home", toy.perfect_shooters("home")), away=toy.with_players("away", toy.roster("away", fouls=0.0)), league=toy.league(home_court=0.0))
    game = simulate(setup, 11, debug=True)
    scored: dict[int, int] = defaultdict(int)
    possession = -1
    heaves: set[int] = set()
    for event in game.events:
        if event.kind == "possession" and event.team == "home":
            possession += 1
            scored[possession] = 0
        elif event.team == "home" and event.kind in ("shot", "free_throw", "turnover"):
            assert event.kind == "shot", "a perfect team takes no free throw and commits no turnover"
            scored[possession] += event.points
            if event.zone == HEAVE:
                heaves.add(possession)
            else:
                assert event.outcome == "made"
    assert all(scored[p] in (2, 3) for p in scored if p not in heaves)
    assert game.home.fga == game.home.fgm + sum(1 for e in game.events if e.kind == "shot" and e.zone == HEAVE and e.outcome != "made")
    assert game.home.tov == 0 and game.home.fta == 0


def test_a_team_whose_every_use_is_a_turnover_never_shoots() -> None:
    # The home side never fouls either, so no bonus free throw gives the away side a shot of any kind.
    setup = GameSetup(home=toy.with_players("home", toy.roster("home", fouls=0.0)), away=toy.with_players("away", toy.never_shoots("away")), league=toy.league())
    game = simulate(setup, 5, debug=True)
    assert not [e for e in game.events if e.kind in ("shot", "free_throw") and e.team == "away"]
    assert game.away.fga == 0 and game.away.fta == 0 and game.away_score == 0
    assert game.away.tov == game.away.possessions > 80
    assert game.home_score > 0


def test_a_fouled_out_player_leaves_and_never_comes_back() -> None:
    setup = GameSetup(home=toy.with_players("home", toy.foul_prone("home")), away=toy.side("away"), league=toy.league())
    game = simulate(setup, 2, debug=True)
    outs = [e for e in game.events if e.kind == "foul_out"]
    assert len(outs) >= 3, "a team fouling 0.18 a minute each should lose its starters"
    for out in outs:
        gone = out.actors["player"]
        later = game.events[game.events.index(out) + 1 :]
        assert not [e for e in later if gone in e.actors.values() and e.kind != "substitution"], gone
        assert not [e for e in later if e.kind == "substitution" and e.actors["in"] == gone]
        assert next(line for line in game.lines if line.athlete_id == gone).pf == engine.FOUL_OUT
        # The substitution that replaced him is the engine's next move at that dead ball.
        replacement = next(e for e in later if e.kind == "substitution" and e.actors["out"] == gone)
        assert replacement.team == out.team
    assert max(line.pf for line in game.lines) == engine.FOUL_OUT
    _check_invariants(game)


def test_a_roster_with_fewer_than_five_left_is_refused_not_played_short() -> None:
    six = dict(list(toy.foul_prone("home", fouls=2.0).items())[:6])
    setup = GameSetup(home=toy.with_players("home", six), away=toy.side("away"), league=toy.league())
    with pytest.raises(EngineError, match="only 4 players available"):
        simulate(setup, 1)


def test_the_starters_policy_keeps_the_starters_absent_foul_outs() -> None:
    home, away = (toy.roster(team, fouls=0.0, trips=0.0, and_one=0.0) for team in ("home", "away"))
    setup = GameSetup(home=toy.with_players("home", home), away=toy.with_players("away", away), league=toy.league())
    game = simulate(setup, 4)
    assert not [e for e in game.events if e.kind in ("substitution", "foul_out", "foul")]
    for line in game.lines:
        expected = _game_seconds(game) if line.athlete_id in ("home-1", "home-2", "home-3", "home-4", "home-5", "away-1", "away-2", "away-3", "away-4", "away-5") else 0
        assert line.seconds == expected
    assert game.home.pf == 0 and game.away.pf == 0


def test_the_starters_policy_fills_a_gap_from_the_bench_in_roster_order() -> None:
    states = tuple(PlayerGameState(f"p{i}", on_floor=i < 5, seconds_played=0.0, stint_seconds=0.0, fouls=6 if i == 2 else 0, available=i != 2) for i in range(8))
    context = RotationContext("t", 2, 300.0, 420.0, -3, states)
    assert StartersPolicy().lineup(context) == ("p0", "p1", "p3", "p4", "p5")
    assert context.on_floor() == ("p0", "p1", "p2", "p3", "p4")
    whole = RotationContext("t", 1, 720.0, 0.0, 0, tuple(PlayerGameState(f"p{i}", i < 5, 0.0, 0.0, 0, True) for i in range(8)))
    assert StartersPolicy().lineup(whole) == ("p0", "p1", "p2", "p3", "p4")


def test_overtime_is_played_until_the_score_differs() -> None:
    setup = GameSetup(home=toy.with_players("home", toy.low_scoring("home")), away=toy.with_players("away", toy.low_scoring("away")), league=toy.league(home_court=0.0))
    games = [simulate(setup, seed, debug=True) for seed in range(40)]
    overtime = [g for g in games if g.overtimes]
    assert overtime, "forty games between teams scoring four points apiece should tie at least once"
    for game in overtime:
        assert game.home_score != game.away_score
        starts = [e for e in game.events if e.kind == "period_start"]
        assert len(starts) == game.periods
        assert [e.clock_remaining for e in starts] == [720.0] * 4 + [300.0] * game.overtimes
        assert game.home.seconds == 5 * (4 * 720 + 300 * game.overtimes)
        _check_invariants(game)


def test_a_game_the_inputs_cannot_decide_is_refused() -> None:
    setup = GameSetup(home=toy.with_players("home", toy.never_shoots("home")), away=toy.with_players("away", toy.never_shoots("away")), league=toy.league())
    with pytest.raises(EngineError, match="still tied after 30 overtimes"):
        simulate(setup, 1)


class _FourMen:
    def lineup(self, context: RotationContext) -> tuple[str, ...]:
        return context.on_floor()[:4]


class _KeepsTheFouledOut:
    def lineup(self, context: RotationContext) -> tuple[str, ...]:
        return context.on_floor()


class _Stranger:
    def lineup(self, context: RotationContext) -> tuple[str, ...]:
        return (*context.on_floor()[:4], "nobody")


def test_a_policy_that_breaks_its_contract_is_an_error() -> None:
    players = toy.roster("home")
    for policy, message in ((_FourMen(), "not five distinct"), (_Stranger(), "not on the roster")):
        home = TeamSetup(team=toy.team("home", "Home"), players=players, starters=tuple(players)[:5], policy=policy)
        with pytest.raises(EngineError, match=message):
            simulate(GameSetup(home=home, away=toy.side("away"), league=toy.league()), 1)
    prone = toy.foul_prone("home")
    home = TeamSetup(team=toy.team("home", "Home"), players=prone, starters=tuple(prone)[:5], policy=_KeepsTheFouledOut())
    with pytest.raises(EngineError, match="has fouled out"):
        simulate(GameSetup(home=home, away=toy.side("away"), league=toy.league()), 2)


def test_a_setup_the_engine_cannot_run_is_refused() -> None:
    players = toy.roster("home")
    with pytest.raises(EngineError, match="five distinct rostered starters"):
        simulate(GameSetup(home=TeamSetup(toy.team("home", "Home"), players, ("home-1", "home-1", "home-2", "home-3", "home-4"), StartersPolicy()), away=toy.side("away"), league=toy.league()), 1)
    with pytest.raises(EngineError, match="five distinct rostered starters"):
        simulate(GameSetup(home=TeamSetup(toy.team("home", "Home"), players, ("home-1", "home-2", "home-3", "home-4", "zz"), StartersPolicy()), away=toy.side("away"), league=toy.league()), 1)
    with pytest.raises(EngineError, match="cannot play itself"):
        simulate(GameSetup(home=toy.side("home"), away=toy.side("home"), league=toy.league()), 1)


def test_debug_mode_catches_a_broken_invariant(monkeypatch: pytest.MonkeyPatch) -> None:
    def _no_score(self: engine._Game, side: engine._Side, player: engine._Player, points: int) -> None:
        player.points += points  # the side's score is never moved, so the events' points outrun it

    monkeypatch.setattr(engine._Game, "_score", _no_score)
    with pytest.raises(InvariantError, match="not the events'"):
        simulate(toy.setup(), 1, debug=True)


def test_adjusted_keeps_a_degenerate_probability_and_combines_the_rest() -> None:
    assert engine.adjusted(0.0, 0.2, 0.1) == 0.0
    assert engine.adjusted(1.0, 0.2, 0.1) == 1.0
    assert engine.adjusted(0.4, 0.2, 0.1) == shrink.combine(0.4, 0.2, 0.1)
    assert engine.adjusted(0.4, 0.1, 0.1) == pytest.approx(0.4)


def test_home_court_is_a_pair_of_multipliers_sized_by_the_leagues_own_points() -> None:
    assert engine.home_court_multipliers(toy.league(home_court=0.0)) == (1.0, 1.0)
    ppp = engine.league_points_per_possession(toy.league())
    assert 1.0 < ppp < 1.25
    home, away = engine.home_court_multipliers(toy.league(home_court=2.5))
    assert home > 1.0 > away
    assert home - 1.0 == pytest.approx(1.0 - away)
    assert home - 1.0 == pytest.approx(2.5 / (2 * toy.PACE * ppp))


def test_home_court_shows_in_the_margin_over_many_games() -> None:
    level = toy.league(home_court=0.0, pace=toy.PACE)
    tilted = toy.league(home_court=12.0, pace=toy.PACE)
    home, away = toy.with_players("home", toy.roster("home")), toy.with_players("away", toy.roster("away"))
    flat = sum(g.home_score - g.away_score for g in (simulate(GameSetup(home, away, level), s) for s in range(300))) / 300
    high = sum(g.home_score - g.away_score for g in (simulate(GameSetup(home, away, tilted), s) for s in range(300))) / 300
    assert high - flat > 6, (flat, high)


def test_events_carry_the_actors_and_zones_a_renderer_needs() -> None:
    game = simulate(toy.setup(), 9)
    kinds = Counter(e.kind for e in game.events)
    assert kinds["period_start"] == kinds["period_end"] == game.periods
    for event in game.events:
        if event.kind == "shot":
            assert "shooter" in event.actors and event.zone and event.outcome in ("made", "missed", "blocked")
            assert (event.points > 0) == (event.outcome == "made")
            if event.outcome == "blocked":
                assert "blocker" in event.actors
        elif event.kind == "free_throw":
            assert "shooter" in event.actors and event.outcome in ("made", "missed") and event.points == (event.outcome == "made")
        elif event.kind == "foul":
            assert "fouler" in event.actors and event.outcome in ("shooting", "and_one", "non_shooting")
            assert bool(event.zone) == (event.outcome != "non_shooting")
        elif event.kind == "turnover":
            assert "player" in event.actors and event.outcome in ("live", "dead")
            if "stealer" in event.actors:
                assert event.outcome == "live"
        elif event.kind == "rebound":
            assert event.outcome in ("offensive", "defensive")
        elif event.kind == "substitution":
            assert set(event.actors) == {"in", "out"}
    assert {e.outcome for e in game.events if e.kind == "possession"} <= {"period_start", "made_shot", "made_free_throw", "defensive_rebound", "live_turnover", "dead_ball"}
    assists = sum(1 for e in game.events if e.kind == "shot" and "assister" in e.actors)
    assert assists == game.home.ast + game.away.ast > 0
    assert sum(1 for e in game.events if e.kind == "shot" and e.outcome == "blocked") == game.home.blk + game.away.blk > 0
