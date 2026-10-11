import sys

sys.path.insert(0, sys.argv[1])
from common import *

con = connect(12)
con.execute("""CREATE TEMP TABLE a AS
  SELECT season, event_id, period, pid, clock_remaining, type, parts,
         row_number() OVER (PARTITION BY season, event_id ORDER BY pid) r_id,
         row_number() OVER (PARTITION BY season, event_id ORDER BY period, clock_remaining DESC, pid) r_clk
  FROM p WHERE season >= 2004 AND period <= 4 AND parts IS NOT NULL""")
con.execute("""CREATE TEMP TABLE ev AS
  SELECT season, event_id, period, r_id, r_clk, parts[1] athlete_id, 'in' state FROM a WHERE type='Substitution' AND len(parts)=2
  UNION ALL
  SELECT season, event_id, period, r_id, r_clk, parts[2] athlete_id, 'out' state FROM a WHERE type='Substitution' AND len(parts)=2
  UNION ALL
  SELECT season, event_id, period, r_id, r_clk, unnest(parts) athlete_id, NULL state FROM a
  WHERE type <> 'Substitution' AND type NOT LIKE '%Timeout%' AND type NOT LIKE '%Time Out%' AND type NOT LIKE '%Technical%' AND type NOT LIKE 'Coach%' AND type NOT LIKE 'Ref-%'
    AND type NOT IN ('Ejection','No Ejection','Not Available','End Period','End Game','Start Period','Challenge','Delay of Game')""")
for name, r in (("pid order", "r_id"), ("period, clock desc, pid order", "r_clk")):
    rows = con.execute(f"""
    WITH s AS (
      SELECT season, event_id, period, athlete_id, state, {r} r,
             last_value(state IGNORE NULLS) OVER (PARTITION BY season, event_id, period, athlete_id ORDER BY {r} ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) prev_state,
             first_value(state IGNORE NULLS) OVER (PARTITION BY season, event_id, period, athlete_id ORDER BY {r} ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) first_sub_state
      FROM ev)
    SELECT season,
           count(*) FILTER (WHERE state IS NULL) touches,
           count(*) FILTER (WHERE state IS NULL AND prev_state = 'out') phantom_after_exit,
           count(*) FILTER (WHERE state IS NULL AND prev_state IS NULL AND first_sub_state = 'in') touch_before_first_entry,
           round(100.0 * count(*) FILTER (WHERE state IS NULL AND (prev_state = 'out' OR (prev_state IS NULL AND first_sub_state = 'in'))) / count(*) FILTER (WHERE state IS NULL), 3) pct_contradicted,
           count(*) FILTER (WHERE state = 'in' AND prev_state = 'in') double_entry,
           count(*) FILTER (WHERE state = 'out' AND prev_state = 'out') double_exit
    FROM s GROUP BY season ORDER BY season""").fetchall()
    show(f"touches contradicting the substitutions under {name}: season, touches, after an exit (phantom), before a first entry, pct contradicted, in-after-in, out-after-out", rows)
