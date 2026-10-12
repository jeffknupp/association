"""Usage as a share of the lineup's uses, against usage per minute.

Reads ``sim.duckdb`` (``sim_actions`` joined to ``sim_segments`` for the five
on the floor) with ``nba.duckdb`` attached, regular seasons 2024-2026. A USE
is a shot, a turnover or the first free throw of a two- or three-shot trip
(an and-one's free throw belongs to its shot). A player's usage share is his
uses over his team's uses while he was on the floor in a resolved segment -
the plan's P4.2 "share of the lineup's possession-ending uses" - whose league
mean is one fifth by construction. Prints the league figures, the players
with the most uses, and how the share compares with the per-minute measure
the engine used before (shots + trips + turnovers per minute, the box
score's), as a ratio to the lineup's.

Run: ``uv run python scripts/sim_p9/usage.py [sim.duckdb] [nba.duckdb]``
Output committed as ``results/usage.out``.
"""

from __future__ import annotations

import sys

import duckdb

SEASONS = (2024, 2025, 2026)

SHARES = """
WITH uses AS (
  SELECT a.event_id, a.season, a.period, a.segment_no, a.team_id, a.athlete_id
  FROM sim_actions a
  WHERE a.season IN (SELECT unnest(?)) AND a.poss_no IS NOT NULL
    AND (a.kind IN ('shot', 'turnover') OR (a.kind = 'free_throw' AND a.ft_kind = 'regular' AND a.ft_number = 1 AND a.ft_of >= 2))
), on_floor AS (
  SELECT u.event_id, u.season, u.team_id, u.athlete_id, unnest(CASE WHEN u.team_id = g.home_team_id THEN s.home_lineup ELSE s.away_lineup END) AS on_id
  FROM uses u
  JOIN nba.real_games g ON g.event_id = u.event_id AND g.season_type = 2
  JOIN sim_segments s ON s.event_id = u.event_id AND s.season = u.season AND s.period = u.period AND s.segment_no = u.segment_no AND s.resolved
)
SELECT on_id AS athlete_id, count(*) FILTER (WHERE athlete_id = on_id) AS uses, count(*) AS lineup_uses
FROM on_floor GROUP BY 1
"""

PER_MINUTE = """
SELECT b.athlete_id, sum(b.minutes) AS minutes, sum(b."fieldGoalsAttempted" + b.turnovers) AS fga_tov, max(p.display_name) AS name
FROM nba.player_box_stats_deduped b JOIN nba.players p ON p.athlete_id = b.athlete_id
WHERE b.season IN (SELECT unnest(?)) AND b.season_type = 2 AND b.minutes IS NOT NULL GROUP BY 1
"""


def main(sim_path: str, warehouse: str) -> None:
    con = duckdb.connect(sim_path, read_only=True)
    con.execute(f"ATTACH '{warehouse}' AS nba (READ_ONLY)")
    con.execute("CREATE TEMP TABLE shares AS " + SHARES, [list(SEASONS)])
    con.execute("CREATE TEMP TABLE pm AS " + PER_MINUTE, [list(SEASONS)])
    uses, lineup = con.execute("SELECT sum(uses), sum(lineup_uses) FROM shares").fetchone()
    print(f"Regular seasons {SEASONS[0]}-{SEASONS[-1]}: {uses:,} uses by a player on the floor of a resolved segment, {lineup:,} on-floor rows; league share {uses / lineup:.4f} (one fifth by construction)")
    print("\nMost uses, with the share of the lineup's uses and the box-score proxy (FGA + TOV per minute, as a share of the lineup's five at the league's mean minutes):")
    rows = con.execute("""
        SELECT s.athlete_id, pm.name, s.uses, s.lineup_uses, s.uses / s.lineup_uses AS share, pm.fga_tov / pm.minutes AS per_minute
        FROM shares s JOIN pm USING (athlete_id) WHERE pm.minutes > 500 ORDER BY s.uses DESC LIMIT 12""").fetchall()
    league_pm = con.execute("SELECT sum(fga_tov) / sum(minutes) FROM pm").fetchone()[0]
    print(f"{'player':28s} {'uses':>7s} {'lineup':>8s} {'share':>7s} {'per-min proxy':>14s}")
    for _, name, u, n, share, pmr in rows:
        print(f"{name:28s} {u:7d} {n:8d} {share:7.3f} {pmr / (5 * league_pm):14.3f}")
    corr, n = con.execute("""
        SELECT corr(s.uses / s.lineup_uses, pm.fga_tov / pm.minutes), count(*) FROM shares s JOIN pm USING (athlete_id) WHERE pm.minutes > 500 AND s.lineup_uses > 500""").fetchone()
    print(f"\nShare against the per-minute proxy over {n} players with 500+ minutes: correlation {corr:.3f}")
    rows = con.execute("""
        SELECT quantile_cont(r, 0.05), quantile_cont(r, 0.25), median(r), quantile_cont(r, 0.75), quantile_cont(r, 0.95) FROM (
          SELECT (s.uses / s.lineup_uses) / (pm.fga_tov / pm.minutes / (5 * ?)) AS r FROM shares s JOIN pm USING (athlete_id) WHERE pm.minutes > 500 AND s.lineup_uses > 500)""", [league_pm]).fetchone()
    print("Ratio share / proxy, quantiles 5-25-50-75-95: " + " ".join(f"{q:.3f}" for q in rows))
    print("A ratio above one is a player whose lineups play slower than the league (his minutes hold fewer uses) or who uses more of them than his minutes say.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sim.duckdb", sys.argv[2] if len(sys.argv) > 2 else "nba.duckdb")
