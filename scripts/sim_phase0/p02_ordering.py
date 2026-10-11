import sys

import duckdb

S = sys.argv[1]
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
con.execute(f"COPY (SELECT season, type, count(*) n FROM p GROUP BY ALL ORDER BY season, n DESC) TO '{S}/types_by_season.csv'")
print("== clock parse failures per season (nulls, n)")
for r in con.execute(
    "SELECT season, count(*) FILTER (WHERE clock_remaining IS NULL) n_null, count(*) FILTER (WHERE clock IS NULL) raw_null, count(*) n FROM p GROUP BY season ORDER BY season"
).fetchall():
    print(r)
print(con.execute("SELECT clock, count(*) FROM p WHERE clock_remaining IS NULL GROUP BY clock ORDER BY 2 DESC LIMIT 10").fetchall())
print("== clock formats")
print(con.execute("SELECT regexp_replace(clock, '[0-9]', '9', 'g') fmt, count(*), min(season), max(season) FROM p GROUP BY fmt ORDER BY 2 DESC LIMIT 12").fetchall())
print()
print("== 0.2 ordering by numeric play_id: season, plays, id_not_prefixed, id_not_numeric, period_inv, clock_inv, games")
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
       count(*) FILTER (WHERE next_period = period AND next_clock > clock_remaining + 5) AS clock_inv_5s,
       count(DISTINCT event_id) games
FROM o GROUP BY season ORDER BY season""").fetchall()
for r in rows:
    print(r)
print("== play_id shapes (len play_id, len event_id, n)")
print(con.execute("SELECT length(play_id) l, length(event_id) le, count(*) FROM p GROUP BY ALL ORDER BY 3 DESC LIMIT 10").fetchall())
print(con.execute("SELECT event_id, play_id FROM p USING SAMPLE 8").fetchall())
print("== duplicate play_id within a game")
print(con.execute("SELECT season, count(*) FROM (SELECT season, event_id, play_id, count(*) c FROM p GROUP BY ALL HAVING c > 1) GROUP BY season ORDER BY season").fetchall())
print("== clock inversions by the kind of play that precedes the jump (top 15) and by the kind at the jump")
print(
    con.execute("""
WITH o AS (
    SELECT season, event_id, type, period, clock_remaining,
           lead(type) OVER w AS next_type,
           lead(period) OVER w AS next_period,
           lead(clock_remaining) OVER w AS next_clock
    FROM p WINDOW w AS (PARTITION BY season, event_id ORDER BY TRY_CAST(play_id AS HUGEINT))
)
SELECT type, next_type, count(*) n FROM o WHERE next_period = period AND next_clock > clock_remaining + 0.5 GROUP BY ALL ORDER BY n DESC LIMIT 15""").fetchall()
)
