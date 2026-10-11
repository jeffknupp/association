import sys

sys.path.insert(0, sys.argv[1])
from common import *

con = connect(6)
show(
    "stale scores: non-scoring rows whose score differs from the previous SCORING row's score (pid order), per season; and by row type 2013+ top 10",
    con.execute("""
WITH o AS (
  SELECT season, type, scoring_play, home_score, away_score,
         last_value(CASE WHEN scoring_play THEN home_score END IGNORE NULLS) OVER w lh,
         last_value(CASE WHEN scoring_play THEN away_score END IGNORE NULLS) OVER w la
  FROM p WINDOW w AS (PARTITION BY season, event_id ORDER BY pid ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING))
SELECT season, count(*) FILTER (WHERE NOT scoring_play AND lh IS NOT NULL) nonscoring, count(*) FILTER (WHERE NOT scoring_play AND lh IS NOT NULL AND (home_score <> lh OR away_score <> la)) stale,
  count(*) FILTER (WHERE NOT scoring_play AND lh IS NOT NULL AND (home_score < lh OR away_score < la)) stale_behind, count(*) FILTER (WHERE NOT scoring_play AND lh IS NOT NULL AND (home_score > lh OR away_score > la)) stale_ahead
FROM o GROUP BY season ORDER BY season""").fetchall(),
)
show(
    "stale by type 2013+",
    con.execute("""
WITH o AS (
  SELECT season, type, scoring_play, home_score, away_score,
         last_value(CASE WHEN scoring_play THEN home_score END IGNORE NULLS) OVER w lh,
         last_value(CASE WHEN scoring_play THEN away_score END IGNORE NULLS) OVER w la
  FROM p WHERE season >= 2013 WINDOW w AS (PARTITION BY season, event_id ORDER BY pid ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING))
SELECT type, count(*) n, count(*) FILTER (WHERE home_score <> lh OR away_score <> la) stale, round(100.0*count(*) FILTER (WHERE home_score <> lh OR away_score <> la)/count(*),1) pct FROM o WHERE NOT scoring_play AND lh IS NOT NULL GROUP BY type ORDER BY stale DESC LIMIT 10""").fetchall(),
)
show(
    "game 401809511 (2026) period 2 around clock 8:15, pid order",
    con.execute("""
SELECT pid, period, clock, type, text, home_score, away_score, scoring_play FROM p WHERE event_id='401809511' AND season=2026 AND period=2 AND clock_remaining BETWEEN 470 AND 520 ORDER BY pid""").fetchall(),
)
show(
    "game 401584959 (2024) period 2 around clock 9:36",
    con.execute("""
SELECT pid, period, clock, type, text, home_score, away_score, scoring_play FROM p WHERE event_id='401584959' AND season=2024 AND period=2 AND clock_remaining BETWEEN 560 AND 600 ORDER BY pid""").fetchall(),
)
show(
    "0.5' bench-possible via DNP: touches by players with did_not_play or NULL minutes in the box, by type (2007+, n>=200)",
    con.execute("""
WITH t AS (SELECT p.season, p.type, p.athlete_id, b.did_not_play, b.minutes FROM p JOIN player_box_stats_deduped b ON b.event_id=p.event_id AND b.season=p.season AND b.athlete_id=p.athlete_id WHERE p.season >= 2007 AND p.athlete_id IS NOT NULL)
SELECT type, count(*) n, count(*) FILTER (WHERE did_not_play OR minutes IS NULL OR minutes = 0) dnp_or_no_minutes, round(100.0*count(*) FILTER (WHERE did_not_play OR minutes IS NULL OR minutes = 0)/count(*),2) pct FROM t GROUP BY type HAVING count(*) >= 200 ORDER BY pct DESC LIMIT 20""").fetchall(),
)
show(
    "0.3b' two-id subs where the text does not start with the first id's display name: sample",
    con.execute("""
SELECT p.season, p.text, pl.display_name, pl2.display_name FROM p JOIN players pl ON pl.athlete_id = p.parts[1] LEFT JOIN players pl2 ON pl2.athlete_id = p.parts[2] WHERE p.type='Substitution' AND len(p.parts)=2 AND NOT starts_with(p.text, pl.display_name) ORDER BY random() LIMIT 10""").fetchall(),
)
show(
    "0.3b'' ... and how many of those START with the SECOND id's name (reversed order)?",
    con.execute("""
SELECT p.season, count(*) n, count(*) FILTER (WHERE starts_with(p.text, pl2.display_name)) starts_with_second FROM p JOIN players pl ON pl.athlete_id = p.parts[1] LEFT JOIN players pl2 ON pl2.athlete_id = p.parts[2] WHERE p.type='Substitution' AND len(p.parts)=2 AND NOT starts_with(p.text, pl.display_name) GROUP BY p.season ORDER BY p.season""").fetchall(),
)
show(
    "free throw trips: 'Free Throw - 1 of 2' not followed (pid order, same team, same clock) by '2 of 2' within 3 rows, 2007+",
    con.execute("""
WITH o AS (SELECT season, event_id, type, team_id, clock_remaining, lead(type,1) OVER w t1, lead(type,2) OVER w t2, lead(type,3) OVER w t3 FROM p WINDOW w AS (PARTITION BY season, event_id ORDER BY pid))
SELECT season, count(*) trips, count(*) FILTER (WHERE 'Free Throw - 2 of 2' NOT IN (t1, t2, t3)) second_missing FROM o WHERE type='Free Throw - 1 of 2' AND season >= 2007 GROUP BY season ORDER BY season""").fetchall(),
)
