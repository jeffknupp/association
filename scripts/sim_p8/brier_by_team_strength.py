"""The validation's Brier scores with the worst teams' games left out.

A season with several teams tanking (2024-25 for the 2025 draft) puts games
in the held-out set whose rosters and effort the estimator's window never
saw. This script reads a validation run's per-game JSON, ranks the season's
teams by regular-season record (``nba.real_games``), and prints the Brier
of the simulation, the strength model and ESPN over the whole set and over
the games with no bottom-k team on either side, k = 4, 6, 8, so the loss of
accuracy on those games can be seen rather than guessed.

Run: ``uv run python scripts/sim_p8/brier_by_team_strength.py <run.json> [nba.duckdb]``
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict

import duckdb


def records(con: duckdb.DuckDBPyConnection, season: int) -> list[tuple[str, float]]:
    wins: dict[str, int] = defaultdict(int)
    games: dict[str, int] = defaultdict(int)
    for home, away, winner in con.execute("SELECT home_team_id, away_team_id, winner_team_id FROM real_games WHERE season = ? AND season_type = 2", [season]).fetchall():
        for t in (home, away):
            games[str(t)] += 1
        if winner is not None:
            wins[str(winner)] += 1
    return sorted(((t, wins[t] / games[t]) for t in games), key=lambda tv: tv[1])


def brier(rows: list[dict], key: str) -> float:
    scored = [(g[key], 1.0 if g["home_score"] > g["away_score"] else 0.0) for g in rows if g.get(key) is not None]
    return sum((p - y) ** 2 for p, y in scored) / len(scored)


def main(path: str, db: str) -> None:
    rows = json.load(open(path))
    season = int(rows[0]["date"][:4]) if rows[0]["date"][5:7] < "08" else int(rows[0]["date"][:4]) + 1
    con = duckdb.connect(db, read_only=True)
    names = dict(con.execute("SELECT team_id::VARCHAR, abbreviation FROM teams").fetchall())
    ranked = records(con, season)
    print(f"{path}: {len(rows)} games, season {season}")
    print("worst eight by record: " + ", ".join(f"{names.get(t, t)} {w:.3f}" for t, w in ranked[:8]))
    print(f"\n{'games':>22s} {'n':>5s} {'simulation':>11s} {'strength':>9s} {'ESPN':>7s} {'home rate':>10s}")
    for k in (0, 4, 6, 8):
        bottom = {t for t, _ in ranked[:k]}
        kept = [g for g in rows if g["home"] not in bottom and g["away"] not in bottom]
        label = "all" if k == 0 else f"no bottom-{k} team"
        print(f"{label:>22s} {len(kept):5d} {brier(kept, 'p_sim'):11.4f} {brier(kept, 'p_strength'):9.4f} {brier(kept, 'p_espn'):7.4f} {brier(kept, 'p_home_rate'):10.4f}")
    only = [g for g in rows if g["home"] in {t for t, _ in ranked[:8]} or g["away"] in {t for t, _ in ranked[:8]}]
    print(f"{'a bottom-8 team plays':>22s} {len(only):5d} {brier(only, 'p_sim'):11.4f} {brier(only, 'p_strength'):9.4f} {brier(only, 'p_espn'):7.4f} {brier(only, 'p_home_rate'):10.4f}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "nba.duckdb")
