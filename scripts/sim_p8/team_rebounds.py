"""Team rebounds: how often a missed shot or free throw is rebounded by
nobody, and which side keeps the ball, in the real play-by-play.

Reads ``sim.duckdb`` (``sim_plays``, built by ``association sim build``),
regular seasons 2024-2026, and classifies every rebound by the play before
it in order of play: a missed field goal, the last free throw of a trip
(missed), a free throw that was not the last (a dead-ball rebound), or
something else. A team rebound is a rebound row with ``team_play`` true (no
participant). Prints per team-game counts and the shares the engine uses
(``engine.FIELD_GOAL_REBOUNDS`` and ``engine.FREE_THROW_REBOUNDS``).

Run: ``uv run python scripts/sim_p8/team_rebounds.py [sim.duckdb]``
Output committed as ``results/team_rebounds.out``.
"""

from __future__ import annotations

import sys

import duckdb

SEASONS = (2024, 2025, 2026)

CLASSIFY = """
CREATE TEMP TABLE r AS
WITH p AS (
  SELECT event_id, kind, team_play,
         lag(kind) OVER w AS prev_kind, lag(text) OVER w AS prev_text, lag(scoring_play) OVER w AS prev_scoring
  FROM sim_plays WHERE season IN (SELECT unnest(?)) AND season_type = 2
  WINDOW w AS (PARTITION BY event_id ORDER BY seq)
)
SELECT kind, team_play,
  CASE WHEN prev_kind = 'free_throw' THEN
         CASE WHEN regexp_extract(prev_text, '(\\d) of (\\d)', 1) = regexp_extract(prev_text, '(\\d) of (\\d)', 2) THEN 'last free throw' ELSE 'free throw, not the last' END
       WHEN prev_kind = 'shot' THEN CASE WHEN prev_scoring THEN 'made shot' ELSE 'missed shot' END
       ELSE 'other' END AS after
FROM p WHERE kind IN ('offensive_rebound', 'defensive_rebound')
"""


def main(path: str) -> None:
    con = duckdb.connect(path, read_only=True)
    con.execute(CLASSIFY, [list(SEASONS)])
    team_games = 2 * con.execute("SELECT count(DISTINCT event_id) FROM sim_plays WHERE season IN (SELECT unnest(?)) AND season_type = 2", [list(SEASONS)]).fetchone()[0]
    print(f"Regular seasons {SEASONS[0]}-{SEASONS[-1]}: {team_games:,} team-games\n")
    print(f"{'after':26s} {'rebound':10s} {'player':>8s} {'team':>8s}   (per team-game)")
    rows = con.execute("SELECT after, kind, sum(CASE WHEN team_play THEN 0 ELSE 1 END), sum(CASE WHEN team_play THEN 1 ELSE 0 END) FROM r GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
    table: dict[tuple[str, str], tuple[float, float]] = {}
    for after, kind, player, team in rows:
        table[after, kind] = (player / team_games, team / team_games)
        print(f"{after:26s} {kind:10s} {player / team_games:8.3f} {team / team_games:8.3f}")
    print()
    for after in ("missed shot", "last free throw"):
        po, to = table[after, "offensive_rebound"]
        pd, td = table[after, "defensive_rebound"]
        retention = (po + to) / (po + to + pd + td)
        player_share = po / (po + pd)
        print(f"{after}: the offense keeps {retention:.4f} of rebounded misses (player lines alone say {player_share:.4f});")
        print(f"  team share of offensive rebounds {to / (po + to):.4f}, of defensive {td / (pd + td):.4f}; odds of the player-line share {player_share / (1 - player_share):.4f}")
    po_fg, pd_fg = table["missed shot", "offensive_rebound"][0], table["missed shot", "defensive_rebound"][0]
    po_ft, pd_ft = table["last free throw", "offensive_rebound"][0], table["last free throw", "defensive_rebound"][0]
    fg, ft = po_fg / (po_fg + pd_fg), po_ft / (po_ft + pd_ft)
    print(f"\nodds ratio, free-throw miss to field-goal miss, on the player-line offensive share: {(ft / (1 - ft)) / (fg / (1 - fg)):.3f}")
    # The estimator's offensive_rebound_rate is over ALL player rebounds, free throws included; the engine's odds are relative to that.
    both = (po_fg + po_ft) / (po_fg + po_ft + pd_fg + pd_ft)
    odds = both / (1 - both)
    print(f"player-line offensive share over all rebounds (the estimator's rate): {both:.4f}, odds {odds:.4f}")
    print(f"  odds multiplier for a missed field goal {(fg / (1 - fg)) / odds:.3f}, for a missed last free throw {(ft / (1 - ft)) / odds:.3f}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sim.duckdb")
