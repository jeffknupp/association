"""Per-player on-court defense (decision D6, option 2): is there signal?

Reads ``sim.duckdb`` with ``nba.duckdb`` attached, regular seasons 2024-2026.
For every opponent possession a player defended (he was in his team's
lineup of the segment the possession started in, resolved segments only),
counts what the opponents did: shots by zone and makes, turnovers, two- and
three-shot trips, and the rebounds of their misses. Prints, per factor, the
league rate, the number of defenders with 2,000+ possessions, the standard
deviation of their raw rates and the standard deviation sampling noise alone
would give at their possession counts; the excess is the signal the
shrinkage keeps. Also the reliability of each factor between the odd and
even games of a player-season, the split the shrinkage strength is fitted
on.

Run: ``uv run python scripts/sim_p9/defense.py [sim.duckdb] [nba.duckdb]``
Output committed as ``results/defense.out``.
"""

from __future__ import annotations

import math
import sys

import duckdb

SEASONS = (2024, 2025, 2026)

ON_DEFENSE = """
CREATE TEMP TABLE defended AS
WITH poss AS (
  SELECT p.event_id, p.season, p.period, p.poss_no, p.offense_team_id, p.segment_no,
         CASE WHEN p.offense_team_id = g.home_team_id THEN s.away_lineup ELSE s.home_lineup END AS defenders
  FROM sim_possessions p
  JOIN nba.real_games g ON g.event_id = p.event_id AND g.season_type = 2
  JOIN sim_segments s ON s.event_id = p.event_id AND s.season = p.season AND s.period = p.period AND s.segment_no = p.segment_no AND s.resolved
  WHERE p.season IN (SELECT unnest(?)) AND p.poss_no IS NOT NULL
), facts AS (
  SELECT a.event_id, a.season, a.period, a.poss_no,
         count(*) FILTER (WHERE a.kind = 'turnover') AS tov,
         count(*) FILTER (WHERE a.kind = 'free_throw' AND a.ft_kind = 'regular' AND a.ft_number = 1 AND a.ft_of >= 2) AS trips,
         count(*) FILTER (WHERE a.kind = 'shot') AS fga, count(*) FILTER (WHERE a.kind = 'shot' AND a.made) AS fgm,
         count(*) FILTER (WHERE a.kind = 'shot' AND a.zone = 'restricted') AS rim_att, count(*) FILTER (WHERE a.kind = 'shot' AND a.zone = 'restricted' AND a.made) AS rim_made,
         count(*) FILTER (WHERE a.kind = 'shot' AND a.shot_value = 3) AS three_att, count(*) FILTER (WHERE a.kind = 'shot' AND a.shot_value = 3 AND a.made) AS three_made,
         count(*) FILTER (WHERE a.kind = 'offensive_rebound' AND NOT a.dead_ball) AS oreb, count(*) FILTER (WHERE a.kind = 'defensive_rebound') AS dreb
  FROM sim_actions a WHERE a.season IN (SELECT unnest(?)) AND a.poss_no IS NOT NULL GROUP BY 1, 2, 3, 4
)
SELECT unnest(p.defenders) AS defender, p.season, p.event_id, f.*
FROM poss p JOIN facts f USING (event_id, season, period, poss_no)
"""

FACTORS = {
    "opp turnovers / possession": ("tov", "1"),
    "opp trips / possession": ("trips", "1"),
    "opp FG% allowed": ("fgm", "fga"),
    "opp rim FG% allowed": ("rim_made", "rim_att"),
    "opp 3P% allowed": ("three_made", "three_att"),
    "opp OREB share of misses": ("oreb", "oreb + dreb"),
}


def main(sim_path: str, warehouse: str) -> None:
    con = duckdb.connect(sim_path, read_only=True)
    con.execute(f"ATTACH '{warehouse}' AS nba (READ_ONLY)")
    con.execute(ON_DEFENSE, [list(SEASONS), list(SEASONS)])
    n_rows, n_players = con.execute("SELECT count(*), count(DISTINCT defender) FROM defended").fetchone()
    print(f"Regular seasons {SEASONS[0]}-{SEASONS[-1]}: {n_rows:,} defender-possessions, {n_players} defenders\n")
    print(f"{'factor':28s} {'league':>8s} {'n(2000+)':>9s} {'sd raw':>8s} {'sd noise':>9s} {'signal sd':>10s} {'odd/even r':>11s}")
    for name, (num, den) in FACTORS.items():
        league_num, league_den = con.execute(f"SELECT sum({num}), sum({den}) FROM defended").fetchone()
        league = league_num / league_den
        rows = con.execute(f"SELECT sum({num}), sum({den}) FROM defended GROUP BY defender HAVING count(*) >= 2000").fetchall()
        rates = [a / b for a, b in rows if b > 0]
        sd_raw = (sum((r - sum(rates) / len(rates)) ** 2 for r in rates) / (len(rates) - 1)) ** 0.5
        noise = (sum(league * (1 - league) / b for _, b in rows if b > 0) / len(rows)) ** 0.5
        signal = math.sqrt(max(sd_raw**2 - noise**2, 0.0))
        # odd/even reliability within player-seasons
        halves = con.execute(
            f"""SELECT sum({num}) FILTER (WHERE parity = 0) / sum({den}) FILTER (WHERE parity = 0), sum({num}) FILTER (WHERE parity = 1) / sum({den}) FILTER (WHERE parity = 1)
                FROM (SELECT d.*, dense_rank() OVER (PARTITION BY defender, season ORDER BY event_id) % 2 AS parity FROM defended d)
                GROUP BY defender, season HAVING count(*) >= 1500"""
        ).fetchall()
        pairs = [(a, b) for a, b in halves if a is not None and b is not None]
        ma, mb = sum(a for a, _ in pairs) / len(pairs), sum(b for _, b in pairs) / len(pairs)
        cov = sum((a - ma) * (b - mb) for a, b in pairs)
        r = cov / math.sqrt(sum((a - ma) ** 2 for a, _ in pairs) * sum((b - mb) ** 2 for _, b in pairs))
        print(f"{name:28s} {league:8.4f} {len(rates):9d} {sd_raw:8.4f} {noise:9.4f} {signal:10.4f} {r:11.3f}")
    print("\nsignal sd is sqrt(sd raw^2 - sd noise^2): the spread of true on-court rates among regulars; odd/even r is the correlation of a player-season's two halves (1500+ possessions each).")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sim.duckdb", sys.argv[2] if len(sys.argv) > 2 else "nba.duckdb")
