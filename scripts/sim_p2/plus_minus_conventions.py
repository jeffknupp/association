"""Which same-clock convention reproduces ESPN's plusMinus? Rebuild 2025's segments three ways."""
import sys, duckdb
from itertools import groupby
from association.sim import segments
from association.sim.segments import Play

root = sys.argv[1]
def connect():
    con = duckdb.connect(f"{root}/variant.duckdb")
    con.execute("ATTACH IF NOT EXISTS 'nba.duckdb' AS nba (READ_ONLY)")
    con.execute("ATTACH IF NOT EXISTS 'sim.duckdb' AS realsim (READ_ONLY)")
    con.execute("CREATE OR REPLACE VIEW sim_plays AS SELECT * FROM realsim.sim_plays WHERE season = 2025")
    return con

original = segments._season_plays
def make(order):
    def season_plays(con, season):
        rows = con.execute("SELECT event_id, seq, period, elapsed, kind, bench_possible, team_id, participants, text, points FROM sim_plays WHERE season = ? ORDER BY event_id, seq", [season]).fetchall()
        for event_id, group in groupby(rows, key=lambda r: r[0]):
            plays = [Play(r[1], r[2], float(r[3]), r[4], bool(r[5]), r[6], tuple(r[7] or ()), r[8] or "", r[9]) for r in group]
            if order == "subs_first":
                plays.sort(key=lambda p: (p.period, p.elapsed, 0 if p.kind == "substitution" else 1, p.seq))
            elif order == "subs_last":
                plays.sort(key=lambda p: (p.period, p.elapsed, 1 if p.kind == "substitution" else 0, p.seq))
            elif order == "subs_last_not_opening":
                opening = {p.period: min(q.elapsed for q in plays if q.period == p.period) for p in plays}
                plays.sort(key=lambda p: (p.period, p.elapsed, 1 if p.kind == "substitution" and p.elapsed > opening[p.period] else 0, p.seq))
            elif order == "subs_after_free_throws":
                ft_clocks = {(p.period, p.elapsed) for p in plays if p.kind == "free_throw"}
                plays.sort(key=lambda p: (p.period, p.elapsed, 1 if p.kind == "substitution" and (p.period, p.elapsed) in ft_clocks else 0, p.seq))
            # seq must stay monotone for the walk's segment bookkeeping: renumber
            plays = [Play(i + 1, p.period, p.elapsed, p.kind, p.bench_possible, p.team_id, p.participants, p.text, p.points) for i, p in enumerate(plays)]
            yield event_id, plays
    return season_plays

for order in ("subs_last_not_opening", "subs_after_free_throws"):
    segments._season_plays = make(order)
    con = connect()
    (r,) = segments.build_segments(con, [2025])
    print(f"{order:>10}: minutes within 1 {100*r.minutes_share:.2f}%  plus-minus exact {100*r.plus_minus_share:.2f}%  resolved {100*r.resolved_share:.2f}%  phantoms {r.phantom_touches}")
    con.close()
