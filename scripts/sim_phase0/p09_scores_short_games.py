import sys

sys.path.insert(0, sys.argv[1])
from common import *

con = connect(6)
con.execute("""CREATE TEMP TABLE o AS
  SELECT season, event_id, pid, period, clock_remaining, type, text, scoring_play, home_score, away_score,
         lag(home_score) OVER w ph, lag(away_score) OVER w pa, lag(type) OVER w ptype, lead(home_score) OVER w nh, lead(away_score) OVER w na
  FROM p WINDOW w AS (PARTITION BY season, event_id ORDER BY pid)""")
show(
    "0.8e score DECREASES by the type of the row carrying the lower score, 2013+, top 15",
    con.execute("""
SELECT type, count(*) n FROM o WHERE season >= 2013 AND (home_score < ph OR away_score < pa) GROUP BY type ORDER BY n DESC LIMIT 15""").fetchall(),
)
show(
    "0.8f ... and does the NEXT row's score recover to >= the previous (a stale score on one row) vs stay low (a real reorder)?",
    con.execute("""
SELECT season, count(*) decreases, count(*) FILTER (WHERE nh >= ph AND na >= pa) recovers_next, count(*) FILTER (WHERE type IN ('Substitution','Full Timeout','Short Timeout','Official Time Out','End Period','End Game') OR type LIKE 'Coach%' OR type LIKE 'Ref-%') on_nonaction
FROM o WHERE season >= 2013 AND (home_score < ph OR away_score < pa) GROUP BY season ORDER BY season""").fetchall(),
)
show(
    "0.8g sample 10 decreases 2024+",
    con.execute("""
SELECT season, event_id, period, clock_remaining, ptype, type, text, ph, pa, home_score, away_score, nh, na FROM o WHERE season >= 2024 AND (home_score < ph OR away_score < pa) ORDER BY random() LIMIT 10""").fetchall(),
)
show(
    "0.8h scoring plays with odd deltas: delta distribution 2013+ (d, n)",
    con.execute("""
SELECT (home_score - ph) + (away_score - pa) d, count(*) FROM o WHERE season >= 2013 AND scoring_play GROUP BY d ORDER BY 2 DESC LIMIT 8""").fetchall(),
)
show(
    "0.8i scoring plays: does the score delta on the SCORING ROW ITSELF equal the shot value, computing delta against the previous SCORING row instead (2013+)?",
    con.execute("""
WITH s AS (SELECT season, type, text, home_score, away_score, lag(home_score) OVER w ph, lag(away_score) OVER w pa FROM p WHERE scoring_play WINDOW w AS (PARTITION BY season, event_id ORDER BY pid))
SELECT season, count(*) n, count(*) FILTER (WHERE (home_score-ph)+(away_score-pa) IN (1,2,3)) ok, count(*) FILTER (WHERE (home_score-ph)+(away_score-pa) < 0) neg, count(*) FILTER (WHERE (home_score-ph)+(away_score-pa) = 0) zero
FROM s WHERE ph IS NOT NULL GROUP BY season ORDER BY season""").fetchall(),
)
show(
    "0.8j games whose plays do not reach the final score, 2002-2008: season, games, short, mean points short, games where the LAST period present < periods in linescore, games with no End Game",
    con.execute("""
WITH m AS (SELECT season, event_id, max(home_score) mh, max(away_score) ma, max(period) mp, bool_or(type='End Game') eg, count(*) n FROM p GROUP BY ALL)
SELECT m.season, count(*) games, count(*) FILTER (WHERE mh <> g.home_score OR ma <> g.away_score) short,
  round(avg((g.home_score - mh) + (g.away_score - ma)) FILTER (WHERE mh <> g.home_score OR ma <> g.away_score), 1) mean_pts_short,
  count(*) FILTER (WHERE mp < len(str_split(g.home_linescores, ','))) fewer_periods, count(*) FILTER (WHERE NOT eg) no_end_game,
  round(avg(n)) mean_plays
FROM m JOIN real_games g USING (season, event_id) WHERE m.season <= 2008 GROUP BY m.season ORDER BY m.season""").fetchall(),
)
show(
    "0.8k 2003-2006 short games: distribution of how many points short (bucket, n)",
    con.execute("""
WITH m AS (SELECT season, event_id, max(home_score) mh, max(away_score) ma FROM p GROUP BY ALL)
SELECT season, CASE WHEN d <= 2 THEN 'a<=2' WHEN d <= 10 THEN 'b<=10' WHEN d <= 40 THEN 'c<=40' ELSE 'd>40' END bucket, count(*) FROM (SELECT m.season, (g.home_score - mh) + (g.away_score - ma) d FROM m JOIN real_games g USING (season, event_id) WHERE m.season BETWEEN 2003 AND 2006 AND (mh <> g.home_score OR ma <> g.away_score)) GROUP BY ALL ORDER BY 1, 2""").fetchall(),
)
show(
    "0.2f' sample action inversions > 3s, 2024+",
    con.execute("""
SELECT season, event_id, period, clock_remaining, type, text, nc, ntype, ntext FROM (
  SELECT *, lead(clock_remaining) OVER w nc, lead(type) OVER w ntype, lead(text) OVER w ntext, lead(period) OVER w np FROM o WINDOW w AS (PARTITION BY season, event_id ORDER BY pid))
WHERE season >= 2024 AND np = period AND nc > clock_remaining + 3 AND type NOT IN ('Substitution','Full Timeout','Short Timeout','End Period','End Game') AND ntype NOT IN ('Substitution','Full Timeout','Short Timeout','End Period','End Game') AND type NOT LIKE 'Coach%' AND ntype NOT LIKE 'Coach%' AND ntype NOT LIKE '%Technical%' AND type NOT LIKE '%Technical%'
ORDER BY random() LIMIT 10""").fetchall(),
)
show(
    "0.2h' period inversions sample",
    con.execute("""
SELECT season, event_id, period, clock_remaining, type, text, np, nc, ntype FROM (SELECT *, lead(clock_remaining) OVER w nc, lead(type) OVER w ntype, lead(period) OVER w np FROM o WINDOW w AS (PARTITION BY season, event_id ORDER BY pid)) WHERE np < period ORDER BY random() LIMIT 8""").fetchall(),
)
show(
    "0.3d' one-id substitutions sample 2018-2020 and their text",
    con.execute(
        "SELECT season, event_id, period, clock, text, participant_athlete_ids FROM plays WHERE type='Substitution' AND participant_athlete_ids NOT LIKE '%,%' AND season BETWEEN 2018 AND 2020 ORDER BY random() LIMIT 8"
    ).fetchall(),
)
show(
    "0.3e one-id subs: does the text name two players (contains ' for ')?",
    con.execute(
        "SELECT season, count(*) n, count(*) FILTER (WHERE text ILIKE '% for %') names_two FROM plays WHERE type='Substitution' AND participant_athlete_ids NOT LIKE '%,%' GROUP BY season HAVING n > 50 ORDER BY season"
    ).fetchall(),
)
