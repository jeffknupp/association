"""How much game-to-game tempo the simulator lacks.

The simulated total's spread within a game runs under the real totals'
(17.2 against 19.5 on the 2025 replay): a real game's pace moves with the
game - blowouts, foul-heavy nights, overtime - and the engine's durations
vary only by the draw. This script sizes the missing piece from a
validation run's JSON and one replayed matchup:

- the spread of real possessions per team-game across the held-out games;
- the spread of the SIMULATED mean possessions across those games (the
  teams' tempo factors, what the engine already varies);
- the spread of possessions across the 200 simulations of one matchup (the
  draws' own noise, what the engine already varies within a game);

and prints the game-level tempo spread the three imply, as a share of the
mean: ``sqrt(real^2 - means^2 - within^2) / mean``.

Run: ``uv run python scripts/sim_p9/tempo.py <validation.json> [event_id]``
Output committed as ``results/tempo.out``.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import statistics as st
import sys
from pathlib import Path

from association.cli.paths import default_db_path, default_sim_path
from association.sim import database
from association.sim import game as matchup


def possessions(line: dict[str, float]) -> float:
    return line["fga"] - line["oreb"] + line["tov"] + 0.44 * line["fta"]


def main(path: str, event_id: str | None) -> None:
    games = json.load(open(path))
    real = [(possessions(g["real_home"]) + possessions(g["real_away"])) / 2 for g in games]
    simulated = [(possessions(g["sim_home"]) + possessions(g["sim_away"])) / 2 for g in games]
    print(f"{path}: {len(games)} games")
    print(f"real possessions per team-game: mean {st.mean(real):.2f}, sd across games {st.stdev(real):.2f}")
    print(f"simulated mean possessions per team-game: sd across games {st.stdev(simulated):.2f} (the teams' tempo factors)")
    con = database.connect(Path(default_sim_path()), Path(default_db_path()), read_only=True)
    try:
        event = event_id or games[0]["event_id"]
        game = matchup.real_game(con, event)
        prepared = matchup.prepare_replay(con, game)
        runs = matchup.run(prepared, 200, 7)
    finally:
        con.close()
    within = [(g.home.possessions + g.away.possessions) / 2 for g in runs]
    totals = [g.home_score + g.away_score for g in runs]
    print(f"one matchup ({event}, {game.date}) over 200 simulations: possessions sd {st.stdev(within):.2f} (mean {st.mean(within):.1f}), total sd {st.stdev(totals):.2f}")
    excess = st.variance(real) - st.variance(simulated) - st.variance(within)
    tempo = math.sqrt(max(excess, 0.0)) / st.mean(real)
    print(f"\nimplied game-level tempo spread: sqrt({st.variance(real):.1f} - {st.variance(simulated):.1f} - {st.variance(within):.1f}) / {st.mean(real):.1f} = {tempo:.3f} of the mean")
    print("(a per-game factor on every duration draw with this standard deviation would give the real spread of possessions)")
    print(f"generated {dt.date.today().isoformat()}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
