"""Which weighting of a window's seasons predicts the league's rates best.

The window a game is estimated from is the three seasons ending in its own
(`game.WINDOW_SEASONS`), read point-in-time. Several league rates trend a
point a season (the offensive-rebound share, the share of threes), so an
equal-weighted window lags them. This script scores season weights
``decay ** (latest - season)`` on the league's own rates: for each target
season T in 2024-2026 the training data are seasons T-2 and T-1 whole and T
up to January 1, the test data T from January 1 (the validation harness's
held-out period), and the error is the relative error of the weighted rate
against the held-out one, averaged over the rates and the targets. Player
box lines (`player_box_stats_deduped`) joined to `real_games`, regular
seasons.

Run: ``uv run python scripts/sim_p8/season_weights.py [nba.duckdb]``
Output committed as ``results/season_weights.out``.
"""

from __future__ import annotations

import sys

import duckdb

TARGETS = (2024, 2025, 2026)
DECAYS = (1.0, 0.7, 0.5, 0.35, 0.25, 0.1, 0.0)
# name -> (numerator SQL, denominator SQL) over a season part's summed box columns
RATES = {
    "oreb share": ("oreb", "oreb + dreb"),
    "3PA / FGA": ("tpa", "fga"),
    "FTA / FGA": ("fta", "fga"),
    "3P%": ("tpm", "tpa"),
    "2P%": ("fgm - tpm", "fga - tpa"),
    "FT%": ("ftm", "fta"),
    "TOV / poss": ("tov", "fga - oreb + tov + 0.44 * fta"),
    "pts / poss": ("pts", "fga - oreb + tov + 0.44 * fta"),
    "poss / 48 min": ("fga - oreb + tov + 0.44 * fta", "minutes / 240.0"),
}

PARTS = """
SELECT g.season, CASE WHEN CAST(left(g.date, 10) AS DATE) < make_date(g.season, 1, 1) THEN 'early' ELSE 'late' END AS part,
       sum(b."offensiveRebounds") AS oreb, sum(b."defensiveRebounds") AS dreb, sum(b."fieldGoalsAttempted") AS fga, sum(b."fieldGoalsMade") AS fgm,
       sum(b."threePointFieldGoalsAttempted") AS tpa, sum(b."threePointFieldGoalsMade") AS tpm, sum(b."freeThrowsAttempted") AS fta, sum(b."freeThrowsMade") AS ftm,
       sum(b.turnovers) AS tov, sum(b.points) AS pts, sum(b.minutes) AS minutes
FROM player_box_stats_deduped b JOIN real_games g ON g.event_id = b.event_id
WHERE g.season BETWEEN ? AND ? AND g.season_type = 2 AND b.minutes IS NOT NULL
GROUP BY 1, 2
"""


def main(path: str) -> None:
    con = duckdb.connect(path, read_only=True)
    con.execute("CREATE TEMP TABLE parts AS " + PARTS, [TARGETS[0] - 2, TARGETS[-1]])
    sums: dict[tuple[int, str], dict[str, tuple[float, float]]] = {}
    for season, part in con.execute("SELECT season, part FROM parts").fetchall():
        sums[season, part] = {
            name: tuple(float(v) for v in con.execute(f"SELECT {num}, {den} FROM parts WHERE season = ? AND part = ?", [season, part]).fetchone() or ()) for name, (num, den) in RATES.items()
        }
    print("Held-out league rates (regular season from January 1) and the weighted estimate from the data before it")
    print(f"{'target':8s}{'rate':14s}{'actual':>9s}" + "".join(f"{d:>9.2f}" for d in DECAYS) + "   (decay)")
    errors = {d: [] for d in DECAYS}
    for target in TARGETS:
        for name in RATES:
            an, ad = sums[target, "late"][name]
            actual = an / ad
            row = f"{target:<8d}{name:14s}{actual:9.4f}"
            for d in DECAYS:
                num = den = 0.0
                for season, part in ((target - 2, "early"), (target - 2, "late"), (target - 1, "early"), (target - 1, "late"), (target, "early")):
                    w = d ** (target - season) if d > 0 else float(season == target)
                    n, m = sums[season, part][name]
                    num += w * n
                    den += w * m
                est = num / den
                errors[d].append(abs(est - actual) / actual)
                row += f"{est:9.4f}"
            print(row)
    print()
    print("mean relative error over the nine rates and three targets, by decay:")
    for d in DECAYS:
        print(f"  decay {d:4.2f}: {100 * sum(errors[d]) / len(errors[d]):.3f}%   (worst rate {100 * max(errors[d]):.2f}%)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "nba.duckdb")
