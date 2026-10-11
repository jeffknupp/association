import sys

sys.path.insert(0, sys.argv[1])
from common import *

con = connect()
show(
    "0.6a team rebounds (rebound type, no athlete) per season; and 'Rebound' generic",
    con.execute("""
SELECT season, count(*) FILTER (WHERE type='Defensive Rebound' AND athlete_id IS NULL) team_dreb, count(*) FILTER (WHERE type='Offensive Rebound' AND athlete_id IS NULL) team_oreb,
  count(*) FILTER (WHERE type IN ('Defensive Rebound','Offensive Rebound') AND athlete_id IS NULL AND team_id IS NULL) team_reb_no_team,
  count(*) FILTER (WHERE type='Rebound') generic_reb
FROM p GROUP BY season ORDER BY season""").fetchall(),
)
show(
    "0.6b steals: turnovers with a second participant per season, and text saying steal",
    con.execute("""
SELECT season, count(*) tov, count(*) FILTER (WHERE len(parts)>=2) with_second, count(*) FILTER (WHERE text ILIKE '%steal%' OR text ILIKE '%stolen%') text_steal
FROM p WHERE type ILIKE '%Turnover%' OR type='Traveling' GROUP BY season ORDER BY season""").fetchall(),
)
show(
    "0.6c period markers per season: games, games with Start Period, End Period rows, End Game rows, games whose first play (by pid) is a jump ball type",
    con.execute("""
WITH f AS (SELECT season, event_id, arg_min(type, pid) first_type FROM p GROUP BY ALL)
SELECT p.season, count(DISTINCT p.event_id) games,
  count(DISTINCT p.event_id) FILTER (WHERE p.type='Start Period') with_start,
  count(*) FILTER (WHERE p.type='End Period') end_period_rows,
  count(DISTINCT p.event_id) FILTER (WHERE p.type='End Game') with_end_game,
  (SELECT count(*) FROM f WHERE f.season=p.season AND f.first_type IN ('Jump Ball','Jumpball')) first_is_jumpball
FROM p GROUP BY p.season ORDER BY p.season""").fetchall(),
)
show(
    "0.6d shooting foul vs free throw adjacency: shooting fouls per season and FT trips (1 of N rows)",
    con.execute("""
SELECT season, count(*) FILTER (WHERE type='Shooting Foul') sf, count(*) FILTER (WHERE type LIKE 'Free Throw - 1 of%') ft_trips, count(*) FILTER (WHERE type='Free Throw - Technical') tech_ft
FROM p GROUP BY season ORDER BY season""").fetchall(),
)
show(
    "0.7 starter flag: per season, real games, games where BOTH teams have exactly 5 starters, team-games with 5, with 0, other; plus CHI/NOP 2013-18 subset (team ids 4 and 3)",
    con.execute("""
WITH t AS (
  SELECT b.season, b.event_id, b.team_id, count(*) FILTER (WHERE b.starter) nstart
  FROM player_box_stats_deduped b JOIN real_games g USING (event_id, season) GROUP BY ALL
), g AS (SELECT season, event_id, bool_and(nstart=5) both5, count(*) teams FROM t GROUP BY ALL)
SELECT t.season, (SELECT count(*) FROM g WHERE g.season=t.season) games, (SELECT count(*) FROM g WHERE g.season=t.season AND both5) both5,
  count(*) team_games, count(*) FILTER (WHERE nstart=5) five, count(*) FILTER (WHERE nstart=0) zero, count(*) FILTER (WHERE nstart NOT IN (0,5)) other,
  count(*) FILTER (WHERE team_id IN ('4','3') AND season BETWEEN 2013 AND 2018) chi_nop, count(*) FILTER (WHERE team_id IN ('4','3') AND season BETWEEN 2013 AND 2018 AND nstart=5) chi_nop_five
FROM t GROUP BY t.season ORDER BY t.season""").fetchall(),
)
show(
    "0.8 score sanity: last play's score vs real_games final, per season (games with plays)",
    con.execute("""
WITH last AS (SELECT season, event_id, arg_max(home_score, pid) hs, arg_max(away_score, pid) aws, max(home_score) mhs, max(away_score) mas FROM p GROUP BY ALL)
SELECT l.season, count(*) games, count(*) FILTER (WHERE l.hs=g.home_score AND l.aws=g.away_score) last_matches, count(*) FILTER (WHERE l.mhs=g.home_score AND l.mas=g.away_score) max_matches
FROM last l JOIN real_games g USING (season, event_id) GROUP BY l.season ORDER BY l.season""").fetchall(),
)
show(
    "0.8b score monotone in pid order: plays where score decreases",
    con.execute("""
WITH o AS (SELECT season, home_score, away_score, lag(home_score) OVER w ph, lag(away_score) OVER w pa FROM p WINDOW w AS (PARTITION BY season, event_id ORDER BY pid))
SELECT season, count(*) FILTER (WHERE home_score < ph OR away_score < pa) decreases FROM o GROUP BY season ORDER BY season""").fetchall(),
)
show(
    "0.8c scoring plays whose score delta is not 1/2/3, or non-scoring plays with a delta, per season",
    con.execute("""
WITH o AS (SELECT season, type, scoring_play, (home_score - lag(home_score) OVER w) + (away_score - lag(away_score) OVER w) d FROM p WINDOW w AS (PARTITION BY season, event_id ORDER BY pid))
SELECT season, count(*) FILTER (WHERE scoring_play AND d NOT IN (1,2,3)) scoring_odd_delta, count(*) FILTER (WHERE NOT scoring_play AND d <> 0) nonscoring_with_delta, count(*) FILTER (WHERE scoring_play) scoring FROM o GROUP BY season ORDER BY season""").fetchall(),
)
show(
    "0.8d games with plays vs real_games per season, and periods per game vs linescores length",
    con.execute("""
WITH pp AS (SELECT season, event_id, max(period) periods FROM p GROUP BY ALL)
SELECT g.season, count(*) real_games, count(pp.event_id) with_plays,
  count(*) FILTER (WHERE pp.periods = len(str_split(g.home_linescores, ','))) periods_match
FROM real_games g LEFT JOIN pp USING (season, event_id) WHERE g.season >= 2002 GROUP BY g.season ORDER BY g.season""").fetchall(),
)
