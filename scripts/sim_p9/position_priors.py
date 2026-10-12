"""Would a position-group prior beat the league prior? (decision D7)

For every player-season of 2024-2026 with box minutes in both its odd and
its even games (the split the shrinkage strength is fitted on), the odd
half's counts are shrunk toward (a) the league mean and (b) the mean of the
player's position group - ``players.position_abbr`` folded to G / F / C
(PG, SG to G; SF, PF, GF to F) - each with its own best strength, and the
shrunk rate is scored against the even half's raw rate, weighted by the even
half's trials. The table prints, per rate, the best strength and the
held-out error of each prior, the error's ratio, and the same ratio over the
player-seasons under 300 minutes, where a prior matters most. Below one, the
position prior predicts better.

Run: ``uv run python scripts/sim_p9/position_priors.py [nba.duckdb]``
Output committed as ``results/position_priors.out``.
"""

from __future__ import annotations

import math
import sys

import duckdb

SEASONS = (2024, 2025, 2026)
GROUP = {"PG": "G", "SG": "G", "G": "G", "SF": "F", "PF": "F", "F": "F", "GF": "F", "C": "C"}
RATES = {
    "shots / minute": ("fga", "minutes"),
    "3PA / FGA": ("tpa", "fga"),
    "FG%": ("fgm", "fga"),
    "3P%": ("tpm", "tpa"),
    "FT%": ("ftm", "fta"),
    "OREB / minute": ("oreb", "minutes"),
    "DREB / minute": ("dreb", "minutes"),
    "AST / minute": ("ast", "minutes"),
    "STL / minute": ("stl", "minutes"),
    "BLK / minute": ("blk", "minutes"),
    "PF / minute": ("pf", "minutes"),
}

HALVES = """
WITH games AS (
  SELECT b.athlete_id, b.season, b.event_id, b.minutes, b."fieldGoalsAttempted" AS fga, b."fieldGoalsMade" AS fgm, b."threePointFieldGoalsAttempted" AS tpa,
         b."threePointFieldGoalsMade" AS tpm, b."freeThrowsAttempted" AS fta, b."freeThrowsMade" AS ftm, b."offensiveRebounds" AS oreb, b."defensiveRebounds" AS dreb,
         b.assists AS ast, b.steals AS stl, b.blocks AS blk, b.fouls AS pf,
         row_number() OVER (PARTITION BY b.athlete_id, b.season ORDER BY g.date, b.event_id) % 2 AS parity
  FROM player_box_stats_deduped b JOIN real_games g ON g.event_id = b.event_id
  WHERE b.season IN (SELECT unnest(?)) AND b.season_type = 2 AND b.minutes IS NOT NULL
)
SELECT g.athlete_id, g.season, g.parity, coalesce(p.position_abbr, 'NA') AS position,
       sum(minutes) AS minutes, sum(fga) AS fga, sum(fgm) AS fgm, sum(tpa) AS tpa, sum(tpm) AS tpm, sum(fta) AS fta, sum(ftm) AS ftm,
       sum(oreb) AS oreb, sum(dreb) AS dreb, sum(ast) AS ast, sum(stl) AS stl, sum(blk) AS blk, sum(pf) AS pf
FROM games g LEFT JOIN players p ON p.athlete_id = g.athlete_id
GROUP BY 1, 2, 3, 4
"""


def shrink(s: float, n: float, mu: float, k: float) -> float:
    return (s + k * mu) / (n + k)


def best_strength(rows: list[tuple[float, float, float, float, float]]) -> tuple[float, float]:
    """``(k, loss)`` minimizing the weighted squared error of the shrunk odd
    rate against the even rate; rows are (odd_s, odd_n, even_rate, even_n, mu)."""

    def loss(log_k: float) -> float:
        k = math.exp(log_k)
        return sum(ne * (shrink(so, no, mu, k) - re) ** 2 for so, no, re, ne, mu in rows)

    lo, hi = math.log(1.0), math.log(1e6)
    phi = (math.sqrt(5) - 1) / 2
    a, b = hi - phi * (hi - lo), lo + phi * (hi - lo)
    fa, fb = loss(a), loss(b)
    for _ in range(60):
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi - phi * (hi - lo)
            fa = loss(a)
        else:
            lo, a, fa = a, b, fb
            b = lo + phi * (hi - lo)
            fb = loss(b)
    k = math.exp((lo + hi) / 2)
    return k, loss(math.log(k))


Half = dict[str, float]
Pair = tuple[Half, Half]


def read_pairs(con: duckdb.DuckDBPyConnection) -> list[Pair]:
    """Each player-season's odd half beside its even half."""
    rows = con.execute(HALVES, [list(SEASONS)]).fetchall()
    cols = ["athlete_id", "season", "parity", "position", "minutes", "fga", "fgm", "tpa", "tpm", "fta", "ftm", "oreb", "dreb", "ast", "stl", "blk", "pf"]
    halves: dict[tuple[str, int], dict[int, Half]] = {}
    for row in rows:
        r = dict(zip(cols, row, strict=True))
        halves.setdefault((r["athlete_id"], int(r["season"])), {})[int(r["parity"])] = {k: float(v) if k not in ("athlete_id", "position") else v for k, v in r.items()}
    return [(h[0], h[1]) for h in halves.values() if 0 in h and 1 in h]


def group_means(pairs: list[Pair], num: str, den: str) -> dict[str, float]:
    """The rate's mean over the league and over each position group."""
    totals: dict[str, list[float]] = {"league": [0.0, 0.0]}
    for odd, even in pairs:
        for key in ("league", GROUP.get(odd["position"], "NA")):
            totals.setdefault(key, [0.0, 0.0])
            totals[key][0] += odd[num] + even[num]
            totals[key][1] += odd[den] + even[den]
    return {k: (v[0] / v[1] if v[1] else 0.0) for k, v in totals.items()}


def score(pairs: list[Pair], num: str, den: str) -> tuple[float, float, float, float, float]:
    """``(league k, league loss, position k, position loss, ratio under 300 minutes)``."""
    mean = group_means(pairs, num, den)
    league_rows, position_rows, small = [], [], []
    for odd, even in pairs:
        if even[den] <= 0:
            continue
        mu_p = mean.get(GROUP.get(odd["position"], "NA"), mean["league"])
        league_rows.append((odd[num], odd[den], even[num] / even[den], even[den], mean["league"]))
        position_rows.append((odd[num], odd[den], even[num] / even[den], even[den], mu_p))
        small.append(odd["minutes"] + even["minutes"] < 300)
    kl, ll = best_strength(league_rows)
    kp, lp = best_strength(position_rows)
    small_l = [r for r, is_small in zip(league_rows, small, strict=True) if is_small]
    small_p = [r for r, is_small in zip(position_rows, small, strict=True) if is_small]
    ratio_small = best_strength(small_p)[1] / best_strength(small_l)[1] if small_l else float("nan")
    return kl, ll, kp, lp, ratio_small


def main(path: str) -> None:
    con = duckdb.connect(path, read_only=True)
    pairs = read_pairs(con)
    groups = ", ".join(f"{g} {sum(1 for o, _ in pairs if GROUP.get(o['position'], 'NA') == g)}" for g in ("G", "F", "C", "NA"))
    print(f"Regular seasons {SEASONS[0]}-{SEASONS[-1]}: {len(pairs)} player-seasons with both halves; groups {groups}")
    print(f"\n{'rate':16s} {'league k':>9s} {'loss':>9s} {'position k':>11s} {'loss':>9s} {'ratio':>7s} {'ratio <300 min':>15s}")
    for name, (num, den) in RATES.items():
        kl, ll, kp, lp, ratio_small = score(pairs, num, den)
        print(f"{name:16s} {kl:9.0f} {ll:9.1f} {kp:11.0f} {lp:9.1f} {lp / ll:7.3f} {ratio_small:15.3f}")
    print("\nratio = position prior's held-out loss / league prior's at each one's best strength; under one, the position prior predicts the other half better.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "nba.duckdb")
