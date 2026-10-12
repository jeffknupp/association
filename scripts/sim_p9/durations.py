"""Possession durations by start type, for the engine's duration draw.

Reads ``sim.duckdb`` (``sim_possessions`` and ``sim_actions``), regular
seasons 2024-2026. The engine's draw is the time from a possession's start
(or from an offensive rebound, or from a non-shooting foul that keeps the
ball) to the next USE - a shot, a turnover, a shooting foul, a non-shooting
foul, an offensive foul - so that is what is measured here, not the whole
possession: per start type a histogram in whole seconds (24 and more in one
bin), its mean, standard deviation and the share at 24 or more; the same
after an offensive rebound (the put-back continuation) and after a
non-shooting foul with no free throws; the time a missed shot or a missed
free throw spends in the air and the scramble before its rebound, which the
engine must also spend (its first draft moved to the rebound at once and
ran 112 possessions a team-game against 101); and the spread of team
means, to judge whether one multiplier per team is enough.

Run: ``uv run python scripts/sim_p9/durations.py [sim.duckdb]``
Output committed as ``results/durations.out``.
"""

from __future__ import annotations

import sys

import duckdb

SEASONS = (2024, 2025, 2026)
USES = ("shot", "turnover", "foul_shooting", "foul_personal", "foul_offensive", "foul_flagrant")

FIRST_USE = """
CREATE TEMP TABLE first_use AS
WITH a AS (
  SELECT a.event_id, a.season, a.period, a.poss_no, a.seq, a.elapsed, a.kind, a.team_id, a.dead_ball,
         p.start_type, p.start_elapsed, p.offense_team_id
  FROM sim_actions a JOIN sim_possessions p USING (event_id, season, period, poss_no)
  WHERE a.season IN (SELECT unnest(?)) AND p.poss_no IS NOT NULL
), uses AS (
  SELECT *, row_number() OVER (PARTITION BY event_id, season, period, poss_no ORDER BY seq) AS use_no
  FROM a WHERE kind IN (SELECT unnest(?)) AND NOT dead_ball
)
SELECT start_type, elapsed - start_elapsed AS seconds FROM uses WHERE use_no = 1
"""

CONTINUATIONS = """
CREATE TEMP TABLE continuation AS
WITH a AS (
  SELECT a.event_id, a.season, a.period, a.poss_no, a.seq, a.elapsed, a.kind, a.team_id, a.dead_ball, a.team_play, a.made, p.offense_team_id,
         lead(a.kind) OVER w AS next_kind, lead(a.elapsed) OVER w AS next_elapsed, lead(a.dead_ball) OVER w AS next_dead
  FROM sim_actions a JOIN sim_possessions p USING (event_id, season, period, poss_no)
  WHERE a.season IN (SELECT unnest(?)) AND p.poss_no IS NOT NULL
  WINDOW w AS (PARTITION BY a.event_id, a.season, a.period, a.poss_no ORDER BY a.seq)
)
SELECT CASE WHEN kind = 'offensive_rebound' THEN 'putback' ELSE 'after_foul' END AS start_type, next_elapsed - elapsed AS seconds
FROM a
WHERE ((kind = 'offensive_rebound' AND NOT dead_ball) OR (kind = 'foul_personal' AND team_id <> offense_team_id))
  AND next_kind IN (SELECT unnest(?)) AND NOT next_dead
UNION ALL
SELECT CASE WHEN kind = 'shot' THEN 'rebound' ELSE 'free_throw_rebound' END, next_elapsed - elapsed
FROM a
WHERE kind IN ('shot', 'free_throw') AND NOT made AND next_kind IN ('offensive_rebound', 'defensive_rebound') AND NOT next_dead
"""


def histogram(con: duckdb.DuckDBPyConnection, table: str, start_type: str) -> tuple[list[float], float, float, int]:
    rows = con.execute(f"SELECT least(greatest(round(seconds), 0), 24)::INTEGER AS s, count(*) FROM {table} WHERE start_type = ? GROUP BY 1 ORDER BY 1", [start_type]).fetchall()
    counts = [0] * 25
    for s, c in rows:
        counts[int(s)] += c
    n = sum(counts)
    mean, sd = con.execute(f"SELECT avg(seconds), stddev(seconds) FROM {table} WHERE start_type = ?", [start_type]).fetchone()
    return [c / n for c in counts], float(mean), float(sd or 0.0), n


def main(path: str) -> None:
    con = duckdb.connect(path, read_only=True)
    con.execute(FIRST_USE, [list(SEASONS), list(USES)])
    con.execute(CONTINUATIONS, [list(SEASONS), list(USES)])
    print(f"Time to the first use, regular seasons {SEASONS[0]}-{SEASONS[-1]} (seconds; the last bin is 24 and more)\n")
    print(f"{'start type':22s} {'n':>8s} {'mean':>6s} {'sd':>6s} {'>=24':>6s}  histogram shares by whole second 0..24")
    for table, kinds in (("first_use", ["period_start", "after_made", "after_ft", "after_dreb", "after_live_turnover", "after_dead_turnover", "after_other"]), ("continuation", ["putback", "after_foul", "rebound", "free_throw_rebound"])):
        for start_type in kinds:
            shares, mean, sd, n = histogram(con, table, start_type)
            print(f"{start_type:22s} {n:8d} {mean:6.2f} {sd:6.2f} {shares[24]:6.3f}  " + " ".join(f"{s:.3f}" for s in shares))
    print("\nTeam spread of the mean time to the first use (all start types but period_start), per team-season:")
    rows = con.execute("""
        WITH a AS (
          SELECT p.season, p.offense_team_id AS team, a.elapsed - p.start_elapsed AS seconds,
                 row_number() OVER (PARTITION BY a.event_id, a.season, a.period, a.poss_no ORDER BY a.seq) AS use_no
          FROM sim_actions a JOIN sim_possessions p USING (event_id, season, period, poss_no)
          WHERE a.season IN (SELECT unnest(?)) AND p.poss_no IS NOT NULL AND p.start_type <> 'period_start' AND a.kind IN (SELECT unnest(?)) AND NOT a.dead_ball
        )
        SELECT season, min(m), quantile_cont(m, 0.1), median(m), quantile_cont(m, 0.9), max(m) FROM (SELECT season, team, avg(seconds) AS m FROM a WHERE use_no = 1 GROUP BY 1, 2) GROUP BY 1 ORDER BY 1
    """, [list(SEASONS), list(USES)]).fetchall()
    for season, lo, p10, med, p90, hi in rows:
        print(f"  {season}: min {lo:.2f}  p10 {p10:.2f}  median {med:.2f}  p90 {p90:.2f}  max {hi:.2f}")
    print("\nFor comparison the engine drew a Gaussian with mean 1440 / pace x 0.980 (14.1 s at pace 100), sd 40% of it, clipped to 3-24 s, for every start type; put-backs mean 5 s, clipped to 1-14 s.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sim.duckdb")
