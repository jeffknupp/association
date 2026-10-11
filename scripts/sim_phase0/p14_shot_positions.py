import sys

sys.path.insert(0, sys.argv[1])
from common import *

con = connect(6)
show(
    "shot chart 2015+: field goals (not FT), with a position, beyond the arc share, per season",
    con.execute("""
SELECT season, count(*) fg, count(*) FILTER (WHERE coordinate_x IS NOT NULL AND NOT (coordinate_x = 0 AND coordinate_y = 0)) with_pos,
  round(100.0*count(*) FILTER (WHERE coordinate_x IS NOT NULL AND NOT (coordinate_x = 0 AND coordinate_y = 0))/count(*),2) pct_pos,
  count(*) FILTER (WHERE points_attempted = 0) unlabeled_value,
  count(*) FILTER (WHERE shot_type = 'Heave Jump Shot') heaves
FROM shot_chart WHERE season >= 2015 AND shot_type NOT ILIKE 'Free Throw%' GROUP BY season ORDER BY season""").fetchall(),
)
show(
    "shot_chart vs plays: shooting plays in shot_chart per season vs plays matching the positive shot rule (2019+)",
    con.execute("""
SELECT p.season, count(*) FILTER (WHERE (p.type ILIKE '%Shot%' OR p.type ILIKE '%Layup%' OR p.type ILIKE '%Dunk%' OR p.type ILIKE '%Jumper%' OR p.type ILIKE '%Hook%' OR p.type ILIKE '%Tip%' OR p.type ILIKE '%Finger Roll%')
     AND p.type NOT ILIKE 'Free Throw%' AND p.type NOT ILIKE '%Turnover%' AND p.type NOT ILIKE '%Foul%' AND p.type NOT ILIKE '%Block%') rule_shots,
  (SELECT count(*) FROM shot_chart s WHERE s.season=p.season AND s.shot_type NOT ILIKE 'Free Throw%') chart_fg,
  count(*) FILTER (WHERE s.play_id IS NOT NULL AND NOT ((p.type ILIKE '%Shot%' OR p.type ILIKE '%Layup%' OR p.type ILIKE '%Dunk%' OR p.type ILIKE '%Jumper%' OR p.type ILIKE '%Hook%' OR p.type ILIKE '%Tip%' OR p.type ILIKE '%Finger Roll%') AND p.type NOT ILIKE 'Free Throw%' AND p.type NOT ILIKE '%Turnover%' AND p.type NOT ILIKE '%Foul%' AND p.type NOT ILIKE '%Block%') AND s.shot_type NOT ILIKE 'Free Throw%') charted_not_rule
FROM p LEFT JOIN shot_chart s ON s.play_id=p.play_id AND s.season=p.season WHERE p.season >= 2019 GROUP BY p.season ORDER BY p.season""").fetchall(),
)
show(
    "possession-boundary inputs 2019+: and-one shape - made FG followed at the same clock by a shooting foul and FT 1 of 1 by the shooter's team (count), vs 'Free Throw - 1 of 1' total",
    con.execute("""
WITH o AS (SELECT season, team_id, type, scoring_play, clock_remaining, period, lead(type,1) OVER w t1, lead(type,2) OVER w t2, lead(clock_remaining,2) OVER w c2 FROM p WHERE season >= 2019 WINDOW w AS (PARTITION BY season, event_id ORDER BY period, clock_remaining DESC, pid))
SELECT season, count(*) FILTER (WHERE type='Free Throw - 1 of 1') ft_1of1,
  count(*) FILTER (WHERE scoring_play AND type NOT LIKE 'Free Throw%' AND t1='Shooting Foul' AND t2='Free Throw - 1 of 1' AND c2 = clock_remaining) andone_adjacent
FROM o GROUP BY season ORDER BY season""").fetchall(),
)
