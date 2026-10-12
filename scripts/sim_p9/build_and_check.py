"""Build sim_plays, sim_segments and sim_possessions/sim_actions from a warehouse and print the Phase 3 checks.

    uv run python scripts/sim_p9/build_and_check.py NBA_DUCKDB SIM_DUCKDB [FIRST_SEASON]

Prints the three check tables `association sim build` prints, then the facts behind
possessions.py's docstring: possessions a team-game by season (all games and regulation-only),
the end-type and start-type mix per team-game, what the implied changes look like
(the closed possession's last action against the new one's first) and the build's timing.
The output is committed beside this script as results/build_<date>.out.
"""

import sys
import time
from pathlib import Path

from association.sim import database, plays, possessions, segments

warehouse, sim_path = Path(sys.argv[1]), Path(sys.argv[2])
first = int(sys.argv[3]) if len(sys.argv) > 3 else None
seasons = list(range(first, 2027)) if first else None

con = database.connect(sim_path, warehouse)
t0 = time.time()
play_reports = plays.build_plays(con, seasons)
t1 = time.time()
segment_reports = segments.build_segments(con, seasons)
t2 = time.time()
reports = possessions.build_possessions(con, seasons)
t3 = time.time()
database.write_meta(con, seasons=[r.season for r in play_reports], warehouse_path=warehouse)

print(f"warehouse {warehouse}; sim {sim_path}; seasons {play_reports[0].season}-{play_reports[-1].season}")
print(f"timing: plays {t1 - t0:.0f}s, segments {t2 - t1:.0f}s, possessions {t3 - t2:.0f}s")
print()
print("sim_possessions (points vs the final; possessions vs the box formula FGA - OREB + TOV + 0.44 FTA over player lines; player stats from actions vs the box, exact share of player-games)")
print(
    f"{'season':>6} {'games':>6} {'excl':>5} {'poss/tg':>8} {'score ok':>9} {'box diff':>9} {'signed':>7} {'periods':>8} "
    f"{'FGA':>7} {'FTA':>7} {'OREB':>7} {'DREB':>7} {'AST':>7} {'TOV':>7} {'and-1':>6} {'lone 1/1':>8} {'implied':>7} {'jump':>5} {'off foul':>8} {'sides':>6} {'team ids':>8} {'no-miss reb':>11} {'dropped':>7} {'teamless':>8}"
)
for r in reports:
    players = " ".join(f"{100 * r.player_share(stat):>6.2f}%" for stat in ("fga", "fta", "oreb", "dreb", "ast", "tov"))
    print(
        f"{r.season:>6} {r.games:>6} {r.games_excluded:>5} {r.possessions_per_team_game:>8.1f} {100 * r.score_share:>8.2f}% {r.box_mean_abs_diff:>9.2f} {r.box_mean_diff:>+7.2f} "
        f"{100 * r.within_periods_share:>7.2f}% {players} {r.and_ones:>6} {r.lone_one_of_ones:>8} {r.implied:>7} {r.jump_ball_changes:>5} {r.offensive_fouls:>8} {r.side_disagreements:>6} {r.team_rebound_ids:>8} "
        f"{r.rebounds_without_miss:>11} {r.dropped_empty:>7} {r.teamless:>8}"
    )

print()
print("possessions a team-game: all games kept, regulation-only games, and NBA.com-style per 48 team-minutes (possessions / (segments seconds / 2880))")
for season, all_tg, reg_tg, per48 in con.execute("""
    WITH g AS (SELECT season, event_id, count(*) AS n, max(period) AS periods FROM sim_possessions GROUP BY ALL),
    secs AS (SELECT season, event_id, sum(end_elapsed - start_elapsed) AS seconds FROM sim_segments GROUP BY ALL)
    SELECT g.season, round(avg(n) / 2, 2), round(avg(n) FILTER (WHERE periods = 4) / 2, 2), round(sum(n) / 2 / (sum(seconds) / 2880), 2)
    FROM g JOIN secs USING (season, event_id) GROUP BY g.season ORDER BY g.season
""").fetchall():
    print(f"  {season}  {all_tg:>7}  {reg_tg:>7}  {per48:>7}")

print()
print("end types per team-game (2019-2026)")
rows = con.execute("""
    WITH tg AS (SELECT season, 2 * count(DISTINCT event_id) AS n FROM sim_possessions GROUP BY season)
    SELECT p.season, p.end_type, round(count(*) * 1.0 / tg.n, 2) FROM sim_possessions p JOIN tg USING (season) WHERE p.season >= 2019 GROUP BY p.season, p.end_type, tg.n ORDER BY 1, 3 DESC
""").fetchall()
for season in sorted({r[0] for r in rows}):
    print(f"  {season}: " + ", ".join(f"{e} {v}" for s, e, v in rows if s == season))
print("start types per team-game (2019-2026)")
rows = con.execute("""
    WITH tg AS (SELECT season, 2 * count(DISTINCT event_id) AS n FROM sim_possessions GROUP BY season)
    SELECT p.season, p.start_type, round(count(*) * 1.0 / tg.n, 2) FROM sim_possessions p JOIN tg USING (season) WHERE p.season >= 2019 GROUP BY p.season, p.start_type, tg.n ORDER BY 1, 3 DESC
""").fetchall()
for season in sorted({r[0] for r in rows}):
    print(f"  {season}: " + ", ".join(f"{e} {v}" for s, e, v in rows if s == season))

print()
print("implied changes 2019-2026: the closed possession's last action -> the new possession's first action (top 12)")
for r in con.execute("""
    WITH ends AS (
        SELECT p.season, p.event_id, p.poss_no, p.actions,
               (SELECT a.kind FROM sim_actions a WHERE a.event_id = p.event_id AND a.season = p.season AND a.poss_no = p.poss_no ORDER BY a.seq DESC LIMIT 1) AS last_kind,
               (SELECT a.kind FROM sim_actions a WHERE a.event_id = p.event_id AND a.season = p.season AND a.poss_no = p.poss_no + 1 ORDER BY a.seq LIMIT 1) AS next_kind
        FROM sim_possessions p WHERE p.end_type = 'implied' AND p.season >= 2019)
    SELECT coalesce(last_kind, '(no action)'), next_kind, count(*) FROM ends GROUP BY ALL ORDER BY 3 DESC LIMIT 12
""").fetchall():
    print(f"  {r[0]:>20} -> {r[1]:<18} {r[2]}")

print()
print(
    "actions 2019-2026 per team-game: shots, free throws (regular / flagrant+clear path / technical), rebounds player / team live / team dead, turnovers player / team, fouls, jump balls, violations"
)
for r in con.execute("""
    WITH tg AS (SELECT season, 2 * count(DISTINCT event_id) AS n FROM sim_possessions GROUP BY season)
    SELECT a.season,
           round(count(*) FILTER (WHERE kind = 'shot') * 1.0 / tg.n, 2),
           round(count(*) FILTER (WHERE kind = 'free_throw' AND ft_kind = 'regular') * 1.0 / tg.n, 2),
           round(count(*) FILTER (WHERE kind = 'free_throw' AND ft_kind IN ('flagrant', 'clear_path')) * 1.0 / tg.n, 2),
           round(count(*) FILTER (WHERE kind = 'free_throw' AND ft_kind = 'technical') * 1.0 / tg.n, 2),
           round(count(*) FILTER (WHERE kind IN ('offensive_rebound', 'defensive_rebound') AND athlete_id IS NOT NULL) * 1.0 / tg.n, 2),
           round(count(*) FILTER (WHERE kind IN ('offensive_rebound', 'defensive_rebound') AND athlete_id IS NULL AND NOT dead_ball) * 1.0 / tg.n, 2),
           round(count(*) FILTER (WHERE kind IN ('offensive_rebound', 'defensive_rebound') AND dead_ball) * 1.0 / tg.n, 2),
           round(count(*) FILTER (WHERE kind = 'turnover' AND athlete_id IS NOT NULL) * 1.0 / tg.n, 2),
           round(count(*) FILTER (WHERE kind = 'turnover' AND athlete_id IS NULL) * 1.0 / tg.n, 2),
           round(count(*) FILTER (WHERE kind LIKE 'foul_%') * 1.0 / tg.n, 2),
           round(count(*) FILTER (WHERE kind = 'jump_ball') * 1.0 / tg.n, 2),
           round(count(*) FILTER (WHERE kind = 'violation') * 1.0 / tg.n, 2)
    FROM sim_actions a JOIN tg USING (season) WHERE a.season >= 2019 GROUP BY a.season, tg.n ORDER BY a.season
""").fetchall():
    print("  " + "  ".join(f"{v:>6}" for v in r))

print()
print("actions whose segment is unknown (segment_no NULL), by season:", con.execute("SELECT season, count(*) FILTER (WHERE segment_no IS NULL) FROM sim_actions GROUP BY 1 ORDER BY 1").fetchall())
print("possessions whose offense is unknown, by season:", con.execute("SELECT season, count(*) FROM sim_possessions WHERE offense_team_id IS NULL GROUP BY 1 ORDER BY 1").fetchall())
con.close()
