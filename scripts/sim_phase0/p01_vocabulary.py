import sys

import duckdb

con = duckdb.connect("nba.duckdb", read_only=True)
con.execute("SET threads=8")
con.execute("""
CREATE TEMP VIEW p AS
SELECT *,
       str_split(participant_athlete_ids, ',') AS parts,
       CASE WHEN clock LIKE '%:%'
            THEN TRY_CAST(split_part(clock, ':', 1) AS INTEGER) * 60 + TRY_CAST(split_part(clock, ':', 2) AS DOUBLE)
            ELSE TRY_CAST(clock AS DOUBLE) END AS clock_remaining
FROM plays""")
print("== 0.1 vocabulary: type by count over all seasons, with seasons present")
rows = con.execute("""
SELECT type, count(*) n, min(season) s0, max(season) s1, count(DISTINCT season) ns,
       count(*) FILTER (WHERE scoring_play) scoring,
       any_value(text) ex
FROM p GROUP BY type ORDER BY n DESC""").fetchall()
for r in rows:
    print(r)
print()
print("== 0.1b per-season type counts (wide) -> saved")
df = con.execute("SELECT season, type, count(*) n FROM p GROUP BY ALL ORDER BY season, n DESC").fetchdf()
df.to_csv(sys.argv[1] + "/types_by_season.csv", index=False)
print("== clock parse failures")
print(con.execute("SELECT season, count(*) FILTER (WHERE clock_remaining IS NULL) nulls, count(*) n FROM p GROUP BY season ORDER BY season").fetchall())
print(con.execute("SELECT clock, count(*) FROM p WHERE clock_remaining IS NULL GROUP BY clock ORDER BY 2 DESC LIMIT 10").fetchall())
print()
print("== 0.2 ordering")
rows = con.execute("""
WITH o AS (
    SELECT season, event_id, play_id, period, clock_remaining,
           lead(period)          OVER w AS next_period,
           lead(clock_remaining) OVER w AS next_clock
    FROM p
    WINDOW w AS (PARTITION BY season, event_id ORDER BY TRY_CAST(play_id AS HUGEINT))
)
SELECT season,
       count(*)                                                        AS plays,
       count(*) FILTER (WHERE NOT starts_with(play_id, event_id))      AS id_not_prefixed,
       count(*) FILTER (WHERE TRY_CAST(play_id AS HUGEINT) IS NULL)    AS id_not_numeric,
       count(*) FILTER (WHERE next_period < period)                    AS period_inv,
       count(*) FILTER (WHERE next_period = period AND next_clock > clock_remaining + 0.5) AS clock_inv,
       count(DISTINCT event_id) games
FROM o GROUP BY season ORDER BY season""").fetchall()
for r in rows:
    print(r)
print("== play_id shapes")
print(con.execute("SELECT length(play_id) l, length(event_id) le, count(*) FROM p GROUP BY ALL ORDER BY 3 DESC LIMIT 10").fetchall())
print(con.execute("SELECT event_id, play_id FROM p USING SAMPLE 8").fetchall())
