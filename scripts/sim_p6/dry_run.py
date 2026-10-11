"""Calibrate the rotation policy without the engine: a clock with a dead ball every 20-40 s.

    PYTHONPATH=src .venv/bin/python scripts/sim_p6/dry_run.py SIM_DUCKDB NBA_DUCKDB [TEAM_ID] [SEASON] [RUNS] [key=value ...] [--grid]

One real team's profile for one season (Boston, 2026, 1,000 runs by default; the league tables
from the same window). The roster handed to the policy is the team's regulars - every player who appeared in at least
half its games in the window - every one available.
Each run: the clock advances by a uniform 20-40 s between dead balls; the margin takes a random
step at each (0 with probability 0.4, +-1 0.1 each, +-2 0.15 each, +-3 0.05 each: a final-margin
standard deviation near 15, the real one is 13-14); personal fouls arrive at the 2026 league rate
(20.0 per team-game, nba.player_box_stats_deduped) on a random player on the floor, and a sixth
makes him unavailable; a game tied at the buzzer plays overtime. The policy is asked at every
dead ball and must return five distinct rostered ids.

Reported against the plan's targets: each regular's mean minutes within 2 of his box target
(regulars: the roster players who played in at least half the team's games), and the stint
quantiles against the real 2024-2026 ones measured by measure.py (all stints q10/25/50/75/90 =
2.03/4.18/6.53/9.15/12.62 minutes, 3.15 stints per player-game, 26-31 substitutions a team-game).
`key=value` overrides a RotationSettings field (pace_tau=none turns the pace term off); --grid
runs a small grid and prints one summary line per setting.
"""

import datetime as dt
import itertools
import math
import os
import random
import statistics
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from association.sim import database, rotation
from association.sim.params import PlayerGameState, RotationContext, Window

REAL_STINT_QUANTILES = {10: 2.03, 25: 4.18, 50: 6.53, 75: 9.15, 90: 12.62}
REAL_STINTS_PER_PLAYER_GAME = 3.15
FOULS_PER_SECOND = 20.0 / 2880
MARGIN_STEPS = ([0] * 40 + [1] * 10 + [-1] * 10 + [2] * 15 + [-2] * 15 + [3] * 5 + [-3] * 5)


@dataclass
class Player:
    athlete_id: str
    on_floor: bool = False
    seconds_played: float = 0.0
    stint_seconds: float = 0.0
    fouls: int = 0
    available: bool = True


def simulate_game(policy, roster, rng):
    players = {a: Player(a) for a in roster}
    elapsed, margin, period_end, period = 0.0, 0, 2880.0, 1
    stints, substitutions = [], 0

    def context():
        return RotationContext(
            team_id=policy.profile.team_id,
            period=period,
            clock_remaining=period_end - elapsed,
            elapsed=elapsed,
            margin=margin,
            players=tuple(PlayerGameState(p.athlete_id, p.on_floor, p.seconds_played, p.stint_seconds, p.fouls, p.available) for p in players.values()),
        )

    def apply(lineup):
        nonlocal substitutions
        assert len(lineup) == 5 and len(set(lineup)) == 5 and set(lineup) <= set(players), lineup
        for p in players.values():
            if p.on_floor and p.athlete_id not in lineup:
                stints.append(p.stint_seconds / 60)
                p.on_floor, p.stint_seconds = False, 0.0
                substitutions += 1
        for a in lineup:
            if not players[a].on_floor:
                players[a].on_floor = True
                assert players[a].available

    apply(policy.lineup(context()))
    while True:
        step = min(rng.uniform(20, 40), period_end - elapsed)
        for p in players.values():
            if p.on_floor:
                p.seconds_played += step
                p.stint_seconds += step
        elapsed += step
        margin += rng.choice(MARGIN_STEPS)
        if rng.random() < FOULS_PER_SECOND * step:
            fouler = rng.choice([p for p in players.values() if p.on_floor])
            fouler.fouls += 1
            if fouler.fouls >= 6:
                fouler.available = False
        if elapsed >= period_end:
            if margin != 0:
                break
            period_end += 300.0
        period = min(int(elapsed // 720) + 1, 4) if elapsed < 2880 else 5 + int((elapsed - 2880) // 300)
        apply(policy.lineup(context()))
    for p in players.values():
        if p.on_floor:
            stints.append(p.stint_seconds / 60)
    return {a: p.seconds_played / 60 for a, p in players.items()}, stints, substitutions, elapsed


def run(profile, league, roster, settings, runs, seed=1):
    rng = random.Random(seed)
    minutes = {a: [] for a in roster}
    stints, subs, overtime = [], [], 0
    for _ in range(runs):
        policy = rotation.RotationModel(profile, league, settings)
        played, game_stints, substitutions, elapsed = simulate_game(policy, roster, rng)
        for a, m in played.items():
            minutes[a].append(m)
        stints.extend(game_stints)
        subs.append(substitutions)
        overtime += elapsed > 2880
    return minutes, stints, subs, overtime


def quantiles(values):
    ordered = sorted(values)
    return {q: ordered[min(len(ordered) - 1, int(q / 100 * len(ordered)))] for q in REAL_STINT_QUANTILES}


def summary(profile, roster, minutes, stints, subs, runs):
    regulars = [a for a in roster if profile.players[a].games >= profile.games / 2]
    off = {a: statistics.mean(minutes[a]) - profile.players[a].minutes_target for a in regulars}
    within = sum(abs(d) <= 2 for d in off.values())
    q = quantiles(stints)
    qerr = max(abs(q[k] - REAL_STINT_QUANTILES[k]) for k in q)
    per_game = len([s for s in stints]) / (runs * len([a for a in roster if statistics.mean(minutes[a]) > 0]))
    return within, len(regulars), max(abs(d) for d in off.values()), q, qerr, per_game, statistics.mean(subs)


def parse_settings(args):
    settings = rotation.RotationSettings()
    for arg in args:
        if "=" not in arg:
            continue
        key, value = arg.split("=", 1)
        settings = replace(settings, **{key: None if value.lower() == "none" else float(value)})
    return settings


def load(sim_path, nba_path, team_id, season):
    """The team's profile, the league tables, its name and the roster: the regulars, every player who
    appeared in at least half the team's games in the window (a playoff roster is nine deep, a blowout's
    thirteen, and a rest night is nobody's rotation)."""
    window = Window((season,), dt.date(season, 9, 1))
    con = database.connect(sim_path, nba_path, read_only=True)
    profile = rotation.build_profiles(con, window)[team_id]
    league = rotation.build_league_rotation(con, window)
    name = con.execute("SELECT display_name FROM nba.teams WHERE team_id = ?", [team_id]).fetchone()[0]
    roster = sorted((a for a, p in profile.players.items() if p.games >= profile.games / 2), key=lambda a: -profile.players[a].minutes_target)
    return window, profile, league, name, roster


def run_grid(profile, league, roster, settings, runs):
    # GRID="fresh_seconds=240,300;fatigue_tau=450,700;rest_tau=180;hysteresis=1.4,1.7;pace_tau=600,none" (the default below)
    spec = os.environ.get("GRID", "fresh_seconds=240,300,420;fatigue_tau=450,700,1000;rest_tau=180,300;hysteresis=1.4,1.7;pace_tau=600,1200,none")
    axes = []
    for part in spec.split(";"):
        key, values = part.split("=")
        axes.append([(key, None if v == "none" else float(v)) for v in values.split(",")])
    for s in (replace(settings, **dict(combo)) for combo in itertools.product(*axes)):
        minutes, stints, subs, _ = run(profile, league, roster, s, runs)
        within, n, worst, q, qerr, per_game, mean_subs = summary(profile, roster, minutes, stints, subs, runs)
        print(f"fresh={s.fresh_seconds:.0f} tau={s.fatigue_tau:.0f} rest={s.rest_tau:.0f} hyst={s.hysteresis} pace={s.pace_tau}: minutes within 2: {within}/{n} (worst {worst:.1f}); stint q {[round(q[k], 1) for k in sorted(q)]} max err {qerr:.1f}; stints/player-game {per_game:.2f}; subs/game {mean_subs:.1f}")


def report(profile, roster, minutes, stints, subs, overtime, runs, settings):
    print(f"settings: {settings}")
    print(f"overtime in {overtime} of {runs} runs; substitutions per game mean {statistics.mean(subs):.1f} (real 2026 team-game median 31, 2018-2025 26-28)")
    game_minutes = statistics.mean(sum(minutes[a][i] for a in roster) for i in range(runs))
    targets = sum(profile.players[a].minutes_target for a in roster)
    scale = game_minutes / targets
    print(f"a simulated game holds {game_minutes:.1f} player-minutes against the roster's {targets:.1f} of targets: the 'scaled' column is each target times {scale:.3f}")
    print(f"\n{'athlete':>8} {'games':>5} {'start':>5} {'target':>6} {'mean':>6} {'sd':>5} {'diff':>6}  within 2?   scaled {'diff':>5} within 2?")
    scaled_within = 0
    for a in roster:
        p = profile.players[a]
        mean = statistics.mean(minutes[a])
        sd = statistics.pstdev(minutes[a])
        diff = mean - p.minutes_target
        scaled_diff = mean - p.minutes_target * scale
        scaled_within += abs(scaled_diff) <= 2
        print(f"{a:>8} {p.games:>5} {p.started:>5} {p.minutes_target:>6.1f} {mean:>6.1f} {sd:>5.1f} {diff:>+6.1f}  {'yes' if abs(diff) <= 2 else 'NO ':>3}        {p.minutes_target * scale:>6.1f} {scaled_diff:>+5.1f} {'yes' if abs(scaled_diff) <= 2 else 'NO'}")
    within, n, worst, q, qerr, per_game, _ = summary(profile, roster, minutes, stints, subs, runs)
    print(f"\nregulars within 2 minutes of target: {within} of {n}; worst {worst:.2f}; against the scaled targets {scaled_within} of {len(roster)}")
    print("stint quantiles (minutes)   sim: " + " ".join(f"q{k}={q[k]:.2f}" for k in sorted(q)))
    print("                           real: " + " ".join(f"q{k}={REAL_STINT_QUANTILES[k]:.2f}" for k in sorted(q)) + f"   (max |sim - real| {qerr:.2f})")
    print(f"stints per player who played: sim {per_game:.2f}, real {REAL_STINTS_PER_PLAYER_GAME}")
    print(f"stint mean sim {statistics.mean(stints):.2f} real 7.10")


def main():
    sim_path, nba_path = Path(sys.argv[1]), Path(sys.argv[2])
    positional = [a for a in sys.argv[3:] if "=" not in a and not a.startswith("--")]
    team_id = positional[0] if positional else "2"
    season = int(positional[1]) if len(positional) > 1 else 2026
    runs = int(positional[2]) if len(positional) > 2 else 1000
    window, profile, league, name, roster = load(sim_path, nba_path, team_id, season)
    print(f"{name} {window.describe()}: {profile.games} games in the profile; roster = the {len(roster)} regulars (in at least half the games), {runs} runs")
    print(f"sum of the roster's minutes targets: {sum(profile.players[a].minutes_target for a in roster):.1f} (a game has 240 plus overtime)")
    settings = parse_settings(sys.argv[3:])
    if "--grid" in sys.argv:
        run_grid(profile, league, roster, settings, min(runs, 150))
        return
    minutes, stints, subs, overtime = run(profile, league, roster, settings, runs)
    report(profile, roster, minutes, stints, subs, overtime, runs, settings)


if __name__ == "__main__":
    main()
