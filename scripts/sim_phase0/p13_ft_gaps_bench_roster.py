import sys

sys.path.insert(0, sys.argv[1])
from common import *

con = connect(6)
show(
    "under clock order, 2007-2012: the row whose clock is too early (its successor's score is lower) by type, with the successor's type, top 10",
    con.execute("""
WITH o AS (SELECT season, type, home_score, away_score, lag(home_score) OVER w ph, lag(away_score) OVER w pa, lag(type) OVER w ptype FROM p WHERE season BETWEEN 2007 AND 2012 WINDOW w AS (PARTITION BY season, event_id ORDER BY period, clock_remaining DESC, pid))
SELECT ptype AS stale_clock_row, type AS next_row, count(*) n FROM o WHERE home_score < ph OR away_score < pa GROUP BY ALL ORDER BY n DESC LIMIT 10""").fetchall(),
)
show(
    "same, 2019-2026",
    con.execute("""
WITH o AS (SELECT season, type, home_score, away_score, lag(home_score) OVER w ph, lag(away_score) OVER w pa, lag(type) OVER w ptype FROM p WHERE season >= 2019 WINDOW w AS (PARTITION BY season, event_id ORDER BY period, clock_remaining DESC, pid))
SELECT ptype AS stale_clock_row, type AS next_row, count(*) n FROM o WHERE home_score < ph OR away_score < pa GROUP BY ALL ORDER BY n DESC LIMIT 10""").fetchall(),
)
show(
    "free throw trips under clock order, 2019+: rows between '1 of 2' and the next '2 of 2' by the same team (gap distribution)",
    con.execute("""
WITH o AS (SELECT season, event_id, team_id, type, row_number() OVER w rn FROM p WHERE season >= 2019 WINDOW w AS (PARTITION BY season, event_id ORDER BY period, clock_remaining DESC, pid)),
 f AS (SELECT a.season, a.event_id, a.rn r1, (SELECT min(b.rn) FROM o b WHERE b.season=a.season AND b.event_id=a.event_id AND b.team_id=a.team_id AND b.type='Free Throw - 2 of 2' AND b.rn > a.rn) r2 FROM o a WHERE a.type='Free Throw - 1 of 2')
SELECT CASE WHEN r2 IS NULL THEN 'none' WHEN r2-r1 = 1 THEN '1' WHEN r2-r1 <= 3 THEN '2-3' WHEN r2-r1 <= 8 THEN '4-8' ELSE '>8' END gap, count(*) FROM f GROUP BY gap ORDER BY gap""").fetchall(),
)
show(
    "types found between a '1 of 2' and its '2 of 2' (gap 2-8), 2019+",
    con.execute("""
WITH o AS (SELECT season, event_id, team_id, type, row_number() OVER w rn FROM p WHERE season >= 2019 WINDOW w AS (PARTITION BY season, event_id ORDER BY period, clock_remaining DESC, pid)),
 f AS (SELECT a.season, a.event_id, a.rn r1, (SELECT min(b.rn) FROM o b WHERE b.season=a.season AND b.event_id=a.event_id AND b.team_id=a.team_id AND b.type='Free Throw - 2 of 2' AND b.rn > a.rn) r2 FROM o a WHERE a.type='Free Throw - 1 of 2')
SELECT o.type, count(*) n FROM f JOIN o ON o.season=f.season AND o.event_id=f.event_id AND o.rn > f.r1 AND o.rn < f.r2 WHERE f.r2 - f.r1 BETWEEN 2 AND 8 GROUP BY o.type ORDER BY n DESC LIMIT 10""").fetchall(),
)
show(
    "0.5'' bench-possible via DNP, excluding team-games with an empty box (max minutes NULL), 2007+: types by share of touches from a player with no minutes",
    con.execute("""
WITH ok AS (SELECT event_id, season, team_id FROM player_box_stats_deduped GROUP BY ALL HAVING max(minutes) IS NOT NULL),
 t AS (SELECT p.season, p.type, b.did_not_play, b.minutes FROM p JOIN player_box_stats_deduped b ON b.event_id=p.event_id AND b.season=p.season AND b.athlete_id=p.athlete_id JOIN ok ON ok.event_id=b.event_id AND ok.season=b.season AND ok.team_id=b.team_id WHERE p.season >= 2007 AND p.athlete_id IS NOT NULL)
SELECT type, count(*) n, count(*) FILTER (WHERE did_not_play OR minutes IS NULL OR minutes = 0) bench, round(100.0*count(*) FILTER (WHERE did_not_play OR minutes IS NULL OR minutes = 0)/count(*),2) pct FROM t GROUP BY type HAVING count(*) >= 100 ORDER BY pct DESC LIMIT 16""").fetchall(),
)
show(
    "0.6e scoring plays that are neither a shot by the reconstructed_box rule nor a free throw, per season, 2013+",
    con.execute("""
SELECT season, type, count(*) n FROM p WHERE scoring_play AND NOT (
    (type ILIKE '%Shot%' OR type ILIKE '%Layup%' OR type ILIKE '%Dunk%' OR type ILIKE '%Jumper%' OR type ILIKE '%Hook%' OR type ILIKE '%Tip%' OR type ILIKE '%Finger Roll%')
     AND type NOT ILIKE 'Free Throw%' AND type NOT ILIKE '%Turnover%' AND type NOT ILIKE '%Foul%' AND type NOT ILIKE '%Block%') AND type NOT ILIKE 'Free Throw%' AND season >= 2013
GROUP BY ALL HAVING n >= 5 ORDER BY season, n DESC""").fetchall(),
)
show(
    "0.6f substitutions whose entering or leaving player is not on the deduped box roster of that team-game, 2019+",
    con.execute("""
WITH s AS (SELECT season, event_id, team_id, parts[1] a, parts[2] b FROM p WHERE type='Substitution' AND len(parts)=2 AND season >= 2019),
 r AS (SELECT season, event_id, team_id, athlete_id FROM player_box_stats_deduped)
SELECT s.season, count(*) subs, count(*) FILTER (WHERE ra.athlete_id IS NULL) entering_not_on_roster, count(*) FILTER (WHERE rb.athlete_id IS NULL) leaving_not_on_roster, count(*) FILTER (WHERE s.team_id IS NULL) no_team
FROM s LEFT JOIN r ra ON ra.season=s.season AND ra.event_id=s.event_id AND ra.team_id=s.team_id AND ra.athlete_id=s.a
       LEFT JOIN r rb ON rb.season=s.season AND rb.event_id=s.event_id AND rb.team_id=s.team_id AND rb.athlete_id=s.b
GROUP BY s.season ORDER BY s.season""").fetchall(),
)
