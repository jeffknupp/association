import sys

sys.path.insert(0, sys.argv[1])
from common import *

con = connect()
show(
    "0.3 substitution participant count per season: subs, with 2 ids, with 1 id, with 0 ids, athlete_id null",
    con.execute("""
SELECT season, count(*) subs, count(*) FILTER (WHERE len(parts)=2) two, count(*) FILTER (WHERE len(parts)=1) one, count(*) FILTER (WHERE parts IS NULL) zero,
       count(*) FILTER (WHERE len(parts)>2) more, count(*) FILTER (WHERE team_id IS NULL) no_team
FROM p WHERE type='Substitution' GROUP BY season ORDER BY season""").fetchall(),
)
show(
    "0.3b is the first id the player named first in the text (the one entering)?",
    con.execute("""
SELECT p.season, count(*) subs,
       count(*) FILTER (WHERE starts_with(p.text, pl.display_name)) first_id_named_first,
       count(*) FILTER (WHERE p.text ILIKE '%enters the game for%') enters_for,
       any_value(p.text) ex
FROM p JOIN players pl ON pl.athlete_id = p.parts[1]
WHERE p.type = 'Substitution' GROUP BY p.season ORDER BY p.season""").fetchall(),
)
show(
    "0.3c substitution text shapes (digits/names stripped), top 8",
    con.execute("""
SELECT regexp_replace(regexp_replace(text, '^[^ ]+( [^ ]+)*? (enters|replaces|in for|for)', 'X \\2'), ' .*', ' ...') shape, count(*) FROM p WHERE type='Substitution' GROUP BY shape ORDER BY 2 DESC LIMIT 8""").fetchall(),
)
show("0.3d one-id substitutions: sample", con.execute("SELECT season, text, parts FROM p WHERE type='Substitution' AND len(parts)=1 USING SAMPLE 8").fetchall())
show(
    "0.4 substitution coverage per season: team-games (teams with box rows in real games), team-games with 0 subs, with <5 subs, median subs, p10",
    con.execute("""
WITH tg AS (
  SELECT DISTINCT b.season, b.event_id, b.team_id FROM player_box_stats_deduped b JOIN real_games g USING (event_id, season)
), s AS (
  SELECT season, event_id, team_id, count(*) n FROM p WHERE type='Substitution' GROUP BY ALL
), j AS (
  SELECT tg.season, tg.event_id, tg.team_id, coalesce(s.n, 0) n FROM tg LEFT JOIN s USING (season, event_id, team_id)
), has_plays AS (SELECT DISTINCT season, event_id FROM p)
SELECT j.season, count(*) team_games, count(*) FILTER (WHERE n=0) zero_subs, count(*) FILTER (WHERE n < 5) under5, median(n) med, quantile_cont(n, 0.1) p10,
       count(*) FILTER (WHERE hp.event_id IS NULL) no_plays_at_all
FROM j LEFT JOIN has_plays hp USING (season, event_id) GROUP BY j.season ORDER BY j.season""").fetchall(),
)
show(
    "0.5 bench-possible: for each non-shot type with an athlete, share of touches where the player's NEXT substitution in the period is him ENTERING (so he was on the bench), 2007-2026, types with >= 2000 touches",
    con.execute("""
WITH subs AS (
  SELECT season, event_id, period, pid, parts[1] AS entering, parts[2] AS leaving FROM p WHERE type='Substitution' AND len(parts)=2
), t AS (
  SELECT season, event_id, period, pid, type, athlete_id FROM p WHERE athlete_id IS NOT NULL AND season >= 2007 AND type <> 'Substitution'
), nxt AS (
  SELECT t.season, t.type, t.athlete_id,
    (SELECT CASE WHEN s.entering = t.athlete_id THEN 'in' ELSE 'out' END FROM subs s WHERE s.season=t.season AND s.event_id=t.event_id AND s.period=t.period AND s.pid > t.pid AND (s.entering = t.athlete_id OR s.leaving = t.athlete_id) ORDER BY s.pid LIMIT 1) nxt
  FROM t
)
SELECT type, count(*) n, round(100.0*count(*) FILTER (WHERE nxt='in')/count(*),2) pct_next_is_entry, round(100.0*count(*) FILTER (WHERE nxt='out')/count(*),2) pct_next_is_exit
FROM nxt GROUP BY type HAVING count(*) >= 2000 ORDER BY pct_next_is_entry DESC LIMIT 30""").fetchall(),
)
