"""P4.1 acceptance: the share of charted field goals per zone and the make rate per zone,
per season 2019-2026, with the checks behind the zone rule (value against geometry, positions,
heaves, and-ones, free-throw trips against FTA/2).

    PYTHONPATH=src .venv/bin/python scripts/sim_p4/zone_table.py SIM_DUCKDB NBA_DUCKDB
"""
import sys, time
import duckdb
from association.sim import zones
from association.sim.params import ZONES, HEAVE, UNKNOWN_TWO, UNKNOWN_THREE

sim_path, nba_path = sys.argv[1], sys.argv[2]
con = duckdb.connect(sim_path, read_only=True)
con.execute(f"ATTACH '{nba_path}' AS nba (READ_ONLY)")
con.execute("SET threads = 8")

ZONE = zones.zone_sql("sp.shot_value", "sp.clock_remaining", "sc.shot_type")
NAMES = [*ZONES, HEAVE, UNKNOWN_TWO, UNKNOWN_THREE]

t = time.time()
rows = con.execute(f"""
    SELECT sc.season, {ZONE} AS zone, count(*) AS shots, count(*) FILTER (WHERE sc.made) AS made
    FROM nba.shot_chart sc
    JOIN sim_plays sp ON sp.play_id = sc.play_id AND sp.season = sc.season
    WHERE sp.kind = 'shot' AND sc.season BETWEEN 2019 AND 2026
    GROUP BY 1, 2 ORDER BY 1, 2
""").fetchall()
print(f"zone table over every charted field goal of a real game, 2019-2026 ({time.time() - t:.1f}s)")
by_season = {}
for season, zone, shots, made in rows:
    by_season.setdefault(season, {})[zone] = (shots, made)
print("season  " + "".join(f"{n:>19}" for n in NAMES) + "   no zone   shots")
for season, zone_counts in sorted(by_season.items()):
    total = sum(s for s, _ in zone_counts.values())
    share = "".join(f"{100 * zone_counts.get(n, (0, 0))[0] / total:>18.2f}%" for n in NAMES)
    print(f"{season} share{share}{zone_counts.get(None, (0, 0))[0]:>10}{total:>8}")
    made = "".join((f"{100 * m / s:>18.1f}%" if s else f"{'-':>19}") for s, m in (zone_counts.get(n, (0, 0)) for n in NAMES))
    print(f"{season} made {made}")

print()
print("value against geometry (positioned field goals): labeled 2 or 3 on the other side of the arc from the coordinates; unlabeled; no position")
for r in con.execute(f"""
    SELECT sc.season, count(*),
           count(*) FILTER (WHERE sc.points_attempted IN (2, 3) AND {zones.HAS_POSITION_SQL} AND sc.points_attempted <> (CASE WHEN {zones.BEYOND_THE_ARC_SQL} THEN 3 ELSE 2 END)) AS disagree,
           count(*) FILTER (WHERE sc.points_attempted NOT IN (2, 3)) AS unlabeled,
           count(*) FILTER (WHERE NOT {zones.HAS_POSITION_SQL}) AS no_position,
           count(*) FILTER (WHERE sp.type = 'Heave Jump Shot') AS typed_heaves,
           count(*) FILTER (WHERE sp.type = 'Heave Jump Shot' AND NOT ({zones.HAS_POSITION_SQL} AND {zones.SHOT_DISTANCE_SQL} >= 35 AND sp.clock_remaining < 3)) AS typed_heaves_the_geometry_misses,
           count(*) FILTER (WHERE sp.type <> 'Heave Jump Shot' AND {zones.HAS_POSITION_SQL} AND {zones.SHOT_DISTANCE_SQL} >= 35 AND sp.clock_remaining < 3) AS geometric_heaves_untyped
    FROM nba.shot_chart sc JOIN sim_plays sp ON sp.play_id = sc.play_id AND sp.season = sc.season
    WHERE sp.kind = 'shot' AND sc.season BETWEEN 2019 AND 2026 GROUP BY 1 ORDER BY 1
""").fetchall():
    print("  ", r)

print()
print("the Python rule against the SQL rule on every distinct (x, y, value, clock < 3, type is heave) the window holds")
rows = con.execute(f"""
    SELECT sc.coordinate_x, sc.coordinate_y, sp.shot_value, CASE WHEN sp.clock_remaining < 3 THEN 1.0 ELSE 100.0 END AS clock, sc.shot_type = 'Heave Jump Shot' AS heave, {ZONE} AS zone, count(*)
    FROM nba.shot_chart sc JOIN sim_plays sp ON sp.play_id = sc.play_id AND sp.season = sc.season
    WHERE sp.kind = 'shot' AND sc.season BETWEEN 2019 AND 2026
    GROUP BY ALL
""").fetchall()
disagreements = [(r, zones.zone_of(r[0], r[1], r[2], r[3], "Heave Jump Shot" if r[4] else "Jump Shot")) for r in rows]
bad = [(r, z) for r, z in disagreements if z != r[5]]
print(f"   {len(rows)} distinct points, {len(bad)} where zone_of and zone_sql disagree", bad[:5])

print()
print("and-ones: Free Throw - 1 of 1 rows, and those preceded in the period by a made shot by the same player at the same clock")
for r in con.execute("""
    WITH ft AS (SELECT DISTINCT season, event_id, period, athlete_id, clock_remaining FROM sim_plays WHERE type = 'Free Throw - 1 of 1' AND season BETWEEN 2019 AND 2026),
         made AS (SELECT sp.season, count(*) AS makes, count(*) FILTER (WHERE ft.athlete_id IS NOT NULL) AS and_ones
                  FROM sim_plays sp LEFT JOIN ft ON ft.season = sp.season AND ft.event_id = sp.event_id AND ft.period = sp.period AND ft.athlete_id = sp.athlete_id AND ft.clock_remaining = sp.clock_remaining
                  WHERE sp.kind = 'shot' AND sp.scoring_play AND sp.season BETWEEN 2019 AND 2026 GROUP BY 1)
    SELECT m.season, m.makes, m.and_ones, (SELECT count(*) FROM sim_plays WHERE type = 'Free Throw - 1 of 1' AND season = m.season) AS one_of_ones
    FROM made m ORDER BY 1
""").fetchall():
    print("  ", r)

print()
print("free-throw trips: first attempts of a trip (any 'Free Throw - ... 1 of N', technicals excluded) against FTA/2 and shooting fouls, per season")
for r in con.execute("""
    SELECT sp.season,
           count(*) FILTER (WHERE sp.type LIKE 'Free Throw - %1 of %') AS trips,
           count(*) FILTER (WHERE sp.type LIKE 'Free Throw - 1 of %') AS plain_trips,
           count(*) FILTER (WHERE sp.kind = 'free_throw' AND sp.type NOT LIKE '%Technical%') / 2.0 AS fta_over_2,
           count(*) FILTER (WHERE sp.kind = 'foul_shooting') AS shooting_fouls,
           count(*) FILTER (WHERE sp.kind = 'foul_shooting' AND len(sp.participants) >= 2) AS shooting_fouls_naming_the_shooter
    FROM sim_plays sp WHERE sp.season BETWEEN 2019 AND 2026 AND sp.kind IN ('free_throw', 'foul_shooting') GROUP BY 1 ORDER BY 1
""").fetchall():
    print("  ", r)
