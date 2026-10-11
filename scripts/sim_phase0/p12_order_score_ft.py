import sys

sys.path.insert(0, sys.argv[1])
from common import *

con = connect(8)
for name, order in (("pid", "pid"), ("period,clock desc,pid", "period, clock_remaining DESC, pid")):
    show(
        f"score decreases and FT-trip breaks under ORDER BY {name}: season, decreases(all rows), decreases(scoring rows vs previous scoring row), ft '1 of 2' with no '2 of 2' in next 3 rows",
        con.execute(f"""
    WITH o AS (SELECT season, type, scoring_play, home_score, away_score,
                      lag(home_score) OVER w ph, lag(away_score) OVER w pa,
                      lead(type,1) OVER w t1, lead(type,2) OVER w t2, lead(type,3) OVER w t3
               FROM p WINDOW w AS (PARTITION BY season, event_id ORDER BY {order})),
         s AS (SELECT season, home_score, away_score, lag(home_score) OVER w ph, lag(away_score) OVER w pa FROM p WHERE scoring_play WINDOW w AS (PARTITION BY season, event_id ORDER BY {order}))
    SELECT o.season, count(*) FILTER (WHERE o.home_score < o.ph OR o.away_score < o.pa) dec_all,
           (SELECT count(*) FROM s WHERE s.season=o.season AND (s.home_score < s.ph OR s.away_score < s.pa)) dec_scoring,
           count(*) FILTER (WHERE o.type='Free Throw - 1 of 2' AND 'Free Throw - 2 of 2' NOT IN (o.t1, o.t2, o.t3)) ft_break,
           count(*) FILTER (WHERE o.type='Free Throw - 1 of 2') trips
    FROM o WHERE o.season >= 2007 GROUP BY o.season ORDER BY o.season""").fetchall(),
    )
show(
    "under clock order: the remaining scoring-row decreases, sample 8 (2019+)",
    con.execute("""
WITH s AS (SELECT season, event_id, period, clock, pid, type, text, home_score, away_score, lag(home_score) OVER w ph, lag(away_score) OVER w pa, lag(text) OVER w pt, lag(clock) OVER w pc FROM p WHERE scoring_play WINDOW w AS (PARTITION BY season, event_id ORDER BY period, clock_remaining DESC, pid))
SELECT season, event_id, period, pc, pt, ph, pa, clock, text, home_score, away_score FROM s WHERE season >= 2019 AND (home_score < ph OR away_score < pa) ORDER BY random() LIMIT 8""").fetchall(),
)
show(
    "same-clock groups: rows sharing (game, period, clock) where pid order and text logic disagree? -> count of same-clock groups with >1 row, and groups where a rebound precedes its shot by pid (2019+)",
    con.execute("""
WITH g AS (SELECT season, event_id, period, clock_remaining, count(*) n,
             bool_or(type IN ('Offensive Rebound','Defensive Rebound')) has_reb,
             arg_min(type, pid) first_type
           FROM p WHERE season >= 2019 GROUP BY ALL HAVING count(*) > 1)
SELECT season, count(*) groups, count(*) FILTER (WHERE has_reb AND first_type IN ('Offensive Rebound','Defensive Rebound')) rebound_first FROM g GROUP BY season ORDER BY season""").fetchall(),
)
