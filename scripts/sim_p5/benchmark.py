"""The engine's performance budget (SIMULATION_PLAN.md, P5): 10,000 simulations of one matchup under 60 seconds on one core.

    uv run python scripts/sim_p5/benchmark.py [--games N] [--profile]

Runs the toy matchup (tests/sim/toy.py) seeds 0..N-1 in one process and prints the wall time; --profile also
runs 500 games under cProfile and prints the hottest functions by own time, which is where a miss would be looked at.
"""
import argparse
import cProfile
import platform
import pstats
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "sim"))
import toy  # noqa: E402

from association.sim.engine import simulate  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=10_000)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    setup = toy.setup()
    print(f"python {platform.python_version()} on {platform.machine()}; {args.games} games of the toy matchup, one process")
    start = time.perf_counter()
    scores = [simulate(setup, seed) for seed in range(args.games)]
    took = time.perf_counter() - start
    print(f"{args.games} games in {took:.2f} s: {took / args.games * 1000:.3f} ms a game ({args.games / took:,.0f} a second)")
    print(f"budget: 60 s for 10,000 -> {'MET' if took / args.games * 10_000 < 60 else 'MISSED'} ({took / args.games * 10_000:.1f} s for 10,000)")
    home_wins = sum(1 for g in scores if g.home_score > g.away_score)
    print(f"home won {home_wins} of {args.games}; mean score {sum(g.home_score for g in scores) / args.games:.1f}-{sum(g.away_score for g in scores) / args.games:.1f}")
    if args.profile:
        profiler = cProfile.Profile()
        profiler.enable()
        for seed in range(500):
            simulate(setup, seed)
        profiler.disable()
        pstats.Stats(profiler).sort_stats("tottime").print_stats(15)


if __name__ == "__main__":
    main()
