"""The first sanity check of the possession loop: 1,000 games of the toy matchup (tests/sim/toy.py), the per-team
per-game averages beside what the toy inputs imply, so a loop that quietly bends a rate shows here before any
estimate goes in.

    uv run python scripts/sim_p5/sanity.py [--games N]

"Implied" figures are what the inputs say with no loop at all: the pace is the two teams' mean; turnovers and
free-throw trips per possession are the starting fives' usage-weighted mix (the defense here is league-average, so
combine() leaves them alone); the three-point share is the fives' usage-weighted zone mix; the offensive-rebound
share is the league's. A simulated figure that sits off its implied one is explained in the README or is a bug.
"""
import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests" / "sim"))
import toy  # noqa: E402

from association.sim.engine import simulate  # noqa: E402
from association.sim.params import THREE_POINT_ZONES  # noqa: E402


def implied(setup):
    """What the inputs say, per side, before any possession is played."""
    out = {}
    for label, side, other in (("home", setup.home, setup.away), ("away", setup.away, setup.home)):
        five = [side.players[s] for s in side.starters]
        usage = [p.shots_per_minute.value + p.free_throw_trips_per_minute.value + p.turnovers_per_minute.value for p in five]
        total = sum(usage)
        out[label] = {
            "pace": (side.team.pace.value + other.team.pace.value) / 2,
            "tov_per_poss": sum(p.turnovers_per_minute.value for p in five) / total,
            "trips_per_poss": sum(p.free_throw_trips_per_minute.value for p in five) / total,
            "three_share": sum(u * sum(p.zone_mix.probability(z) for z in THREE_POINT_ZONES) for u, p in zip(usage, five)) / total,
            "oreb_share": setup.league.offensive_rebound_rate,
            "usage_per_minute": total,
        }
    return out


def per_game(games, label, want):
    """(name, simulated mean, implied or None) for one side over the games."""
    own = [g.home if label == "home" else g.away for g in games]
    opp = [g.away if label == "home" else g.home for g in games]
    minutes = [t.seconds / 60 / 5 for t in own]

    def mean(f):
        return statistics.mean(f(t, o, m) for t, o, m in zip(own, opp, minutes))

    return [
        ("points", mean(lambda t, o, m: t.points), None),
        ("possessions per 48", mean(lambda t, o, m: t.possessions * 48 / m), want["pace"]),
        ("FGA per 48", mean(lambda t, o, m: t.fga * 48 / m), None),
        ("FGA per possession", mean(lambda t, o, m: t.fga / t.possessions), None),
        ("3PA share of FGA", mean(lambda t, o, m: t.tpa / t.fga), want["three_share"]),
        ("FTA per 48", mean(lambda t, o, m: t.fta * 48 / m), None),
        ("TOV per possession", mean(lambda t, o, m: t.tov / t.possessions), want["tov_per_poss"]),
        ("OREB share of own misses", mean(lambda t, o, m: t.oreb / (t.oreb + o.dreb)), want["oreb_share"]),
        ("AST per 48", mean(lambda t, o, m: t.ast * 48 / m), None),
        ("STL per 48", mean(lambda t, o, m: t.stl * 48 / m), None),
        ("BLK per 48", mean(lambda t, o, m: t.blk * 48 / m), None),
        ("PF per 48", mean(lambda t, o, m: t.pf * 48 / m), None),
        ("points per possession", mean(lambda t, o, m: t.points / t.possessions), None),
    ]


def print_table(home_rows, away_rows):
    print(f"{'per team-game':<28}{'home sim':>10}{'home in':>10}{'away sim':>10}{'away in':>10}")
    for (name, home_sim, home_in), (_, away_sim, away_in) in zip(home_rows, away_rows):
        cells = [f"{home_sim:>10.3f}", f"{home_in:>10.3f}" if home_in is not None else f"{'-':>10}", f"{away_sim:>10.3f}", f"{away_in:>10.3f}" if away_in is not None else f"{'-':>10}"]
        print(f"{name:<28}" + "".join(cells))


def fouls_per_possession(games):
    """(shooting fouls drawn, non-shooting defensive fouls drawn) per possession, over both sides."""
    drawn = []
    for g in games:
        for team, poss in (("home", g.home.possessions), ("away", g.away.possessions)):
            against = [e for e in g.events if e.kind == "foul" and e.team != team]
            shooting = sum(1 for e in against if e.outcome in ("shooting", "and_one"))
            drawn.append((shooting / poss, (len(against) - shooting) / poss))
    return statistics.mean(d[0] for d in drawn), statistics.mean(d[1] for d in drawn)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=1000)
    args = parser.parse_args()
    setup = toy.setup()
    games = [simulate(setup, seed) for seed in range(args.games)]
    want = implied(setup)
    print(f"{args.games} games of the toy matchup; league pace {setup.league.pace}, home court {setup.league.home_court} points")
    print(f"home won {sum(1 for g in games if g.home_score > g.away_score)}; overtime in {sum(1 for g in games if g.overtimes)}; "
          f"mean margin {statistics.mean(g.home_score - g.away_score for g in games):+.2f} (home court implies about +{setup.league.home_court:.1f})")
    print()
    print_table(per_game(games, "home", want["home"]), per_game(games, "away", want["away"]))
    shooting, non_shooting = fouls_per_possession(games)
    print()
    print(f"shooting fouls drawn per possession (and-ones included): {shooting:.3f} "
          f"(implied trips {want['home']['trips_per_poss']:.3f} home, {want['away']['trips_per_poss']:.3f} away, plus and-ones)")
    print(f"non-shooting defensive fouls per possession: {non_shooting:.3f} (engine constant NON_SHOOTING_DEFENSIVE_SHARE)")
    print(f"starting five usage per minute: home {want['home']['usage_per_minute']:.3f}, away {want['away']['usage_per_minute']:.3f}; "
          f"team possessions per minute at the mean pace: {want['home']['pace'] / 48:.3f}")


if __name__ == "__main__":
    main()
