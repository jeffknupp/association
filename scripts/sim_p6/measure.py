"""Phase 6's measurements against the real sim.duckdb and nba.duckdb, read-only.

    PYTHONPATH=src .venv/bin/python scripts/sim_p6/measure.py SIM_DUCKDB NBA_DUCKDB [FIRST_SEASON LAST_SEASON]

Prints, for the window (2024-2026 by default, every game through the end of the last season):
the games and resolved share; P6.2's substitution windows (the kind of the nearest preceding
non-substitution play, with the 99% cutoff, and the free-throw attempt the window sits on);
the real stint-length quantiles (a stint is a run of consecutive resolved segments a player
stays in the lineup for, a period break included); the four-kind foul count against the box;
the foul-trouble and blowout tables as `rotation.build_league_rotation` measures them (raw
observed/expected ratio, samples, and the policy's relative capped factor); and one team's
profile summary. Output is committed under results/ with the date.
"""

import datetime as dt
import sys
import time
from pathlib import Path

from association.sim import database, rotation
from association.sim.params import Window

sim_path, nba_path = Path(sys.argv[1]), Path(sys.argv[2])
first = int(sys.argv[3]) if len(sys.argv) > 3 else 2024
last = int(sys.argv[4]) if len(sys.argv) > 4 else 2026
window = Window(tuple(range(first, last + 1)), dt.date(last, 9, 1))
con = database.connect(sim_path, nba_path, read_only=True)
seasons = ", ".join(str(s) for s in window.seasons)
print(f"window {window.describe()}; sim.duckdb meta {database.read_meta(con)}")

print("\n== games and resolution ==")
for row in con.execute(f"""
    SELECT g.season, count(DISTINCT g.event_id) AS games, count(DISTINCT s.event_id) AS with_segments,
           round(100.0 * sum(CASE WHEN s.resolved THEN s.end_elapsed - s.start_elapsed END) / sum(s.end_elapsed - s.start_elapsed), 2) AS resolved_pct
    FROM nba.real_games g LEFT JOIN sim_segments s USING (event_id, season)
    WHERE g.season IN ({seasons}) AND g.date < '{window.as_of.isoformat()}' GROUP BY 1 ORDER BY 1""").fetchall():
    print(row)

print("\n== P6.2 substitution windows: the nearest preceding non-substitution kind ==")
t0 = time.time()
rows = con.execute(f"""
    WITH p AS (
        SELECT season, event_id, seq, kind, type, scoring_play FROM sim_plays sp
        WHERE season IN ({seasons}) AND EXISTS (SELECT 1 FROM nba.real_games g WHERE g.event_id = sp.event_id AND g.season = sp.season AND g.date < '{window.as_of.isoformat()}')
    ),
    prev AS (
        SELECT s.season, s.event_id, s.seq,
               (SELECT q.kind || CASE WHEN q.kind IN ('shot', 'free_throw') THEN (CASE WHEN q.scoring_play THEN '_made' ELSE '_missed' END) ELSE '' END
                FROM p q WHERE q.season = s.season AND q.event_id = s.event_id AND q.seq < s.seq AND q.kind <> 'substitution' ORDER BY q.seq DESC LIMIT 1) AS prev_kind,
               (SELECT q.type FROM p q WHERE q.season = s.season AND q.event_id = s.event_id AND q.seq < s.seq AND q.kind <> 'substitution' ORDER BY q.seq DESC LIMIT 1) AS prev_type
        FROM p s WHERE s.kind = 'substitution'
    )
    SELECT prev_kind, count(*) AS n, round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct,
           round(100.0 * sum(count(*)) OVER (ORDER BY count(*) DESC ROWS UNBOUNDED PRECEDING) / sum(count(*)) OVER (), 2) AS cumulative
    FROM prev GROUP BY 1 ORDER BY n DESC""").fetchall()
total = sum(r[1] for r in rows)
print(f"{total} substitutions")
for r in rows:
    print(f"  {str(r[0]):>22} {r[1]:>7} {r[2]:>6}% cumulative {r[3]:>6}%")
kinds_99 = []
for r in rows:
    kinds_99.append((r[0] or "").split("_made")[0].split("_missed")[0])
    if r[3] >= 99.0:
        break
print("the 99% list by kind:", sorted(set(kinds_99)))
print("\nfree-throw types preceding a substitution (which attempt the window sits on):")
for r in con.execute(f"""
    WITH p AS (
        SELECT season, event_id, seq, kind, type FROM sim_plays WHERE season IN ({seasons})
    ),
    prev AS (
        SELECT (SELECT q.type FROM p q WHERE q.season = s.season AND q.event_id = s.event_id AND q.seq < s.seq AND q.kind <> 'substitution' ORDER BY q.seq DESC LIMIT 1) AS prev_type
        FROM p s WHERE s.kind = 'substitution'
    )
    SELECT prev_type, count(*) AS n FROM prev WHERE prev_type LIKE 'Free Throw%' GROUP BY 1 ORDER BY n DESC""").fetchall():
    print(f"  {r[0]:>32} {r[1]:>7}")
print(f"({time.time() - t0:.1f}s)")

print("\n== real stint lengths (minutes), consecutive resolved segments a player stays on ==")
t0 = time.time()
stint_sql = f"""
    WITH games AS (
        SELECT event_id, season, home_team_id, away_team_id FROM nba.real_games WHERE season IN ({seasons}) AND date < '{window.as_of.isoformat()}'
    ),
    sides AS (
        SELECT s.event_id, s.season, s.segment_no, s.start_elapsed, s.end_elapsed, s.resolved, g.home_team_id AS team_id, s.home_lineup AS lineup FROM sim_segments s JOIN games g USING (event_id, season)
        UNION ALL
        SELECT s.event_id, s.season, s.segment_no, s.start_elapsed, s.end_elapsed, s.resolved, g.away_team_id, s.away_lineup FROM sim_segments s JOIN games g USING (event_id, season)
    ),
    presence AS (
        SELECT sd.event_id, sd.season, sd.team_id, sd.segment_no, sd.start_elapsed, sd.end_elapsed, sd.resolved, a.athlete_id,
               list_contains(sd.lineup, a.athlete_id) AS on_floor
        FROM sides sd JOIN (SELECT DISTINCT event_id, season, team_id, unnest(lineup) AS athlete_id FROM sides) a USING (event_id, season, team_id)
    ),
    lagged AS (
        SELECT *, coalesce(lag(on_floor) OVER w, false) AS was_on
        FROM presence WINDOW w AS (PARTITION BY event_id, season, team_id, athlete_id ORDER BY segment_no)
    ),
    runs AS (
        SELECT *, sum(CASE WHEN on_floor AND was_on THEN 0 ELSE 1 END) OVER w AS run_no
        FROM lagged WINDOW w AS (PARTITION BY event_id, season, team_id, athlete_id ORDER BY segment_no)
    ),
    stints AS (
        SELECT event_id, season, team_id, athlete_id, run_no, (max(end_elapsed) - min(start_elapsed)) / 60.0 AS minutes,
               bool_and(resolved) AS clean, min(start_elapsed) AS started, max(end_elapsed) AS ended
        FROM runs WHERE on_floor GROUP BY ALL
    ),
    box AS (SELECT event_id, season, team_id, athlete_id, starter FROM nba.player_box_stats_deduped)
    SELECT {{select}} FROM stints s LEFT JOIN box b USING (event_id, season, team_id, athlete_id) WHERE s.clean {{where}}
"""
quantiles = "count(*) AS n, round(avg(minutes), 2) AS mean, " + ", ".join(f"round(quantile_cont(minutes, {q}), 2) AS q{int(q * 100)}" for q in (0.1, 0.25, 0.5, 0.75, 0.9))
for label, where in (("all stints", ""), ("box starters", "AND b.starter"), ("bench", "AND NOT coalesce(b.starter, false)"), ("stints that end at the final buzzer excluded", "AND s.ended < 2880")):
    row = con.execute(stint_sql.format(select=quantiles, where=where)).fetchone()
    print(f"  {label:>44}: n={row[0]:>7} mean={row[1]:>5} q10={row[2]:>5} q25={row[3]:>5} q50={row[4]:>5} q75={row[5]:>5} q90={row[6]:>5}")
row = con.execute(stint_sql.format(select="count(*)", where="")).fetchone()
unclean = con.execute(stint_sql.replace("WHERE s.clean", "WHERE NOT s.clean").format(select="count(*)", where="")).fetchone()
print(f"  stints with an unresolved segment, excluded: {unclean[0]} of {row[0] + unclean[0]}")
print("  stints per player-game (players who played):", con.execute(stint_sql.format(select="round(count(*) * 1.0 / count(DISTINCT (event_id, season, team_id, athlete_id)), 3)", where="")).fetchone()[0])
print(f"({time.time() - t0:.1f}s)")

print("\n== the four personal-foul kinds against the box's fouls, per player-game with minutes ==")
for season in window.seasons:
    row = con.execute(f"""
        WITH f AS (SELECT event_id, season, athlete_id, count(*) n FROM sim_plays WHERE season = {season} AND kind IN ('foul_personal', 'foul_shooting', 'foul_offensive', 'foul_flagrant') AND athlete_id IS NOT NULL GROUP BY ALL),
             ft AS (SELECT event_id, season, athlete_id, count(*) n FROM sim_plays WHERE season = {season} AND kind IN ('foul_personal', 'foul_shooting', 'foul_offensive', 'foul_flagrant', 'foul_technical') AND athlete_id IS NOT NULL GROUP BY ALL)
        SELECT count(*), sum((coalesce(f.n, 0) = b.fouls)::INT), sum((coalesce(ft.n, 0) = b.fouls)::INT)
        FROM nba.player_box_stats_deduped b LEFT JOIN f USING (event_id, season, athlete_id) LEFT JOIN ft USING (event_id, season, athlete_id)
        WHERE b.season = {season} AND b.minutes > 0 AND b.fouls IS NOT NULL""").fetchone()
    print(f"  {season}: {row[0]} player-games, four kinds equal the box on {row[1]} ({100 * row[1] / row[0]:.2f}%), with technicals {row[2]} ({100 * row[2] / row[0]:.2f}%)")

print("\n== league tables (rotation.build_league_rotation) ==")
t0 = time.time()
league = rotation.build_league_rotation(con, window)
print(f"({time.time() - t0:.1f}s)")
print("foul trouble: (fouls, period) raw observed/expected ratio [samples] -> policy factor (relative to 0 fouls, capped at 1)")
for period in range(1, 6):
    cells = []
    for fouls in range(6):
        key = (fouls, period)
        if key in league.foul_ratio:
            cells.append(f"{fouls}f {league.foul_ratio[key]:.3f} [{league.foul_samples[key]:>6}] -> {league.foul(fouls, period):.2f}")
    print(f"  period {period}: " + " | ".join(cells))
print("blowouts, box starters in the second half: (|margin| bucket, time bucket) raw ratio [samples] -> policy factor (relative to within five, capped at 1)")
margin_labels = ["0-5", "6-10", "11-15", "16-20", "21-30", "31+"]
time_labels = ["0-3 min left", "3-6", "6-9", "9-12", "12-18", "18-24"]
for tb in range(6):
    cells = []
    for mb in range(6):
        key = (mb, tb)
        if key in league.blowout_ratio:
            elapsed = 2880 - (rotation.TIME_BUCKETS[tb] - 1)
            margin = rotation.MARGIN_BUCKETS[mb] if mb < 5 else 40
            cells.append(f"{margin_labels[mb]:>5} {league.blowout_ratio[key]:.3f} [{league.blowout_samples[key]:>5}] -> {league.blowout(margin, elapsed):.2f}")
    print(f"  {time_labels[tb]:>12}: " + " | ".join(cells))

print("\n== profiles (rotation.build_profiles) ==")
t0 = time.time()
profiles = rotation.build_profiles(con, window)
print(f"({time.time() - t0:.1f}s) {len(profiles)} teams")
names = dict(con.execute("SELECT team_id, display_name FROM nba.teams").fetchall())
for team_id in sorted(profiles, key=lambda t: names.get(t, t))[:30]:
    prof = profiles[team_id]
    regulars = [p for p in prof.players.values() if p.games >= prof.games / 2]
    off = max(abs(60 * sum(p.on_floor[:48]) / 60 - p.minutes_target) for p in regulars) if regulars else 0
    print(f"  {names.get(team_id, team_id):>24}: {prof.games} games, {len(prof.players)} players, {len(regulars)} in half the games or more; sum of regulars' minutes targets {sum(p.minutes_target for p in regulars):.1f}; the profile's regulation minutes vs the box target differ by at most {off:.2f}")
boston = profiles["2"]
print(f"\nBoston {window.describe()}: starters {boston.likely_starters()}")
for p in sorted(boston.players.values(), key=lambda p: -p.minutes_target):
    print(f"  {p.athlete_id:>8} games {p.games:>3} started {p.started:>3} target {p.minutes_target:>5.1f} profile-minutes {sum(p.on_floor[:48]):>5.1f} OT {p.on_floor[48]:.2f} by six minutes {[round(x, 2) for x in p.on_floor[::6]]}")
