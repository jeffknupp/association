"""Which weighting of a window's GAMES by age predicts the league's rates
best: the season weights of ``scripts/sim_p8/season_weights.py`` taken to
days, for the within-season drift the season weights cannot reach (the
offensive-rebound share was 0.247 before January 1 of 2025 and 0.255 after).

For each target season T in 2024-2026 the training data are the regular-
season games of T-2, T-1 and T before January 1 of T, each weighted
``0.5 ** (age_days / half_life)`` with age counted from January 1 of T, and
the test data are T's games from January 1 (the validation harness's
held-out period). The error is the relative error of the weighted rate
against the held-out one, averaged over nine league rates and the three
targets. Player box lines (`player_box_stats_deduped`) joined to
`real_games`. A half-life of 365 days with age counted in whole seasons is
the committed season decay of 0.5.

Run: ``uv run python scripts/sim_p9/game_weights.py [nba.duckdb]``
Output committed as ``results/game_weights.out``.
"""

from __future__ import annotations

import sys

import duckdb

TARGETS = (2024, 2025, 2026)
HALF_LIVES = (None, 730.0, 365.0, 240.0, 180.0, 120.0, 90.0, 60.0)
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

GAMES = """
SELECT g.season, CAST(left(g.date, 10) AS DATE) AS day,
       sum(b."offensiveRebounds") AS oreb, sum(b."defensiveRebounds") AS dreb, sum(b."fieldGoalsAttempted") AS fga, sum(b."fieldGoalsMade") AS fgm,
       sum(b."threePointFieldGoalsAttempted") AS tpa, sum(b."threePointFieldGoalsMade") AS tpm, sum(b."freeThrowsAttempted") AS fta, sum(b."freeThrowsMade") AS ftm,
       sum(b.turnovers) AS tov, sum(b.points) AS pts, sum(b.minutes) AS minutes
FROM player_box_stats_deduped b JOIN real_games g ON g.event_id = b.event_id
WHERE g.season BETWEEN ? AND ? AND g.season_type = 2 AND b.minutes IS NOT NULL
GROUP BY 1, 2
"""


def main(path: str) -> None:
    con = duckdb.connect(path, read_only=True)
    con.execute("CREATE TEMP TABLE days AS " + GAMES, [TARGETS[0] - 2, TARGETS[-1]])
    print("Held-out league rates (regular season from January 1) and the game-weighted estimate from the data before it")
    print(f"{'target':8s}{'rate':14s}{'actual':>9s}" + "".join(f"{'equal' if h is None else int(h):>9}" for h in HALF_LIVES) + "   (half-life, days)")
    errors: dict[float | None, list[float]] = {h: [] for h in HALF_LIVES}
    for target in TARGETS:
        for name, (num, den) in RATES.items():
            actual_num, actual_den = con.execute(f"SELECT sum({num}), sum({den}) FROM days WHERE season = ? AND day >= make_date(?, 1, 1)", [target, target]).fetchone()
            actual = float(actual_num) / float(actual_den)
            row = f"{target:<8d}{name:14s}{actual:9.4f}"
            for h in HALF_LIVES:
                weight = "1.0" if h is None else f"power(0.5, datediff('day', day, make_date({target}, 1, 1)) / {h})"
                est_num, est_den = con.execute(
                    f"SELECT sum({weight} * ({num})), sum({weight} * ({den})) FROM days WHERE season BETWEEN ? AND ? AND day < make_date(?, 1, 1)", [target - 2, target, target]
                ).fetchone()
                est = float(est_num) / float(est_den)
                errors[h].append(abs(est - actual) / actual)
                row += f"{est:9.4f}"
            print(row)
    print("\nmean relative error over the nine rates and three targets, by half-life:")
    for h in HALF_LIVES:
        label = "equal weights" if h is None else f"{int(h):>4d} days"
        print(f"  {label:14s}: {100 * sum(errors[h]) / len(errors[h]):.3f}%   (worst rate {100 * max(errors[h]):.2f}%)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "nba.duckdb")
