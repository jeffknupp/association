import duckdb


def connect(threads=4):
    con = duckdb.connect("nba.duckdb", read_only=True)
    con.execute(f"SET threads={threads}")
    con.execute("""
    CREATE TEMP VIEW p AS
    SELECT *,
           str_split(participant_athlete_ids, ',') AS parts,
           CASE WHEN clock LIKE '%:%'
                THEN TRY_CAST(split_part(clock, ':', 1) AS INTEGER) * 60 + TRY_CAST(split_part(clock, ':', 2) AS DOUBLE)
                ELSE TRY_CAST(clock AS DOUBLE) END AS clock_remaining,
           TRY_CAST(play_id AS HUGEINT) AS pid
    FROM plays""")
    return con


def show(title, rows):
    print("==", title)
    for r in rows:
        print("  ", r)
