import sys

sys.path.insert(0, sys.argv[1])
from common import *

con = connect()
NONACTION = "('Substitution','Full Timeout','Short Timeout','Official Time Out','OfficialTVTimeOut','End Period','End Game','Start Period','Challenge','No Timeout') OR type LIKE 'Coach''s Challenge%' OR type LIKE 'Ref-Initiated%' OR type LIKE '%Technical%' OR type = 'Not Available'"
con.execute(f"""CREATE TEMP TABLE o AS
    SELECT season, event_id, pid, type, period, clock_remaining, text,
           lead(type) OVER w AS next_type, lead(period) OVER w AS next_period, lead(clock_remaining) OVER w AS next_clock, lead(text) OVER w AS next_text,
           NOT (type IN {NONACTION}) AS is_action
    FROM p WINDOW w AS (PARTITION BY season, event_id ORDER BY pid)""")
con.execute("ALTER TABLE o ADD COLUMN next_is_action BOOLEAN")
con.execute(f"UPDATE o SET next_is_action = NOT (next_type IN {NONACTION.replace('type', 'next_type')})")
show(
    "0.2b inversions among ACTION plays only (both sides action): season, plays, action_inv_0.5s, action_inv_3s, pct_0.5s, games_with_any",
    con.execute("""
SELECT season, count(*) plays,
  count(*) FILTER (WHERE is_action AND next_is_action AND next_period = period AND next_clock > clock_remaining + 0.5) inv05,
  count(*) FILTER (WHERE is_action AND next_is_action AND next_period = period AND next_clock > clock_remaining + 3) inv3,
  round(100.0 * count(*) FILTER (WHERE is_action AND next_is_action AND next_period = period AND next_clock > clock_remaining + 0.5) / count(*), 3) pct05,
  count(DISTINCT event_id) FILTER (WHERE is_action AND next_is_action AND next_period = period AND next_clock > clock_remaining + 0.5) games_any
FROM o GROUP BY season ORDER BY season""").fetchall(),
)
show(
    "0.2c inversions where the NEXT play is a substitution (stale sub clock), per season",
    con.execute("""
SELECT season, count(*) FILTER (WHERE next_type='Substitution' AND next_period = period AND next_clock > clock_remaining + 0.5) sub_inv,
       count(*) FILTER (WHERE next_type='Substitution') subs FROM o GROUP BY season ORDER BY season""").fetchall(),
)
show(
    "0.2d plays logged AFTER 'End Period' but in the same period (tail out of order): season, n, games",
    con.execute("""
WITH e AS (SELECT season, event_id, period, pid AS end_pid FROM p WHERE type = 'End Period')
SELECT p.season, count(*) n, count(DISTINCT p.event_id) games FROM p JOIN e USING (season, event_id, period) WHERE p.pid > e.end_pid AND p.type NOT IN ('End Game','End Period') GROUP BY p.season ORDER BY p.season""").fetchall(),
)
show(
    "0.2e action inversion pairs, top 20 type pairs",
    con.execute("""
SELECT type, next_type, count(*) n FROM o WHERE is_action AND next_is_action AND next_period = period AND next_clock > clock_remaining + 0.5 GROUP BY ALL ORDER BY n DESC LIMIT 20""").fetchall(),
)
show(
    "0.2f sample of 12 action inversions > 3s, 2024-2026",
    con.execute("""
SELECT season, event_id, period, clock_remaining, type, text, next_clock, next_type, next_text FROM o WHERE is_action AND next_is_action AND next_period = period AND next_clock > clock_remaining + 3 AND season >= 2024 USING SAMPLE 12""").fetchall(),
)
show(
    "0.2g sample of 8 action inversions > 3s, 2005-2012",
    con.execute("""
SELECT season, event_id, period, clock_remaining, type, text, next_clock, next_type, next_text FROM o WHERE is_action AND next_is_action AND next_period = period AND next_clock > clock_remaining + 3 AND season BETWEEN 2005 AND 2012 USING SAMPLE 8""").fetchall(),
)
show(
    "0.2h period inversions: sample 8",
    con.execute("SELECT season, event_id, period, clock_remaining, type, text, next_period, next_clock, next_type FROM o WHERE next_period < period USING SAMPLE 8").fetchall(),
)
show(
    "0.2i alternative ordering (period, clock desc, pid): how often does a Defensive/Offensive Rebound NOT directly follow a miss/FT/jumpball/rebound under each ordering (2019-2026)",
    con.execute("""
WITH a AS (
 SELECT season, event_id, type, scoring_play,
   lag(type) OVER (PARTITION BY season, event_id ORDER BY pid) prev_id,
   lag(type) OVER (PARTITION BY season, event_id ORDER BY period, clock_remaining DESC, pid) prev_clk
 FROM p WHERE season >= 2019 AND type NOT IN ('Substitution','Full Timeout','Short Timeout','Official Time Out','End Period','End Game','Challenge') AND type NOT LIKE 'Coach''s%' AND type NOT LIKE 'Ref-%' AND type NOT LIKE '%Technical%'
)
SELECT season, count(*) FILTER (WHERE type IN ('Defensive Rebound','Offensive Rebound')) rebs,
  count(*) FILTER (WHERE type IN ('Defensive Rebound','Offensive Rebound') AND NOT (prev_id LIKE 'Free Throw%' OR prev_id LIKE '%Rebound%' OR prev_id LIKE '%Shot%' OR prev_id LIKE '%Layup%' OR prev_id LIKE '%Dunk%' OR prev_id LIKE '%Hook%' OR prev_id LIKE '%Tip%' OR prev_id LIKE '%Finger Roll%' OR prev_id LIKE 'Jump%' OR prev_id = 'Not Available')) bad_id_order,
  count(*) FILTER (WHERE type IN ('Defensive Rebound','Offensive Rebound') AND NOT (prev_clk LIKE 'Free Throw%' OR prev_clk LIKE '%Rebound%' OR prev_clk LIKE '%Shot%' OR prev_clk LIKE '%Layup%' OR prev_clk LIKE '%Dunk%' OR prev_clk LIKE '%Hook%' OR prev_clk LIKE '%Tip%' OR prev_clk LIKE '%Finger Roll%' OR prev_clk LIKE 'Jump%' OR prev_clk = 'Not Available')) bad_clock_order
FROM a GROUP BY season ORDER BY season""").fetchall(),
)
