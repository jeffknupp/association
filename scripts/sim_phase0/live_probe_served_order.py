import json
import random
import sys

import duckdb

from association.fetch.client import ESPNClient
from association.fetch.endpoints import summary_url

out = sys.argv[1]
con = duckdb.connect("nba.duckdb", read_only=True)
random.seed(7)
games = []
for season in (2026, 2025, 2024, 2022, 2019, 2016, 2013, 2010):
    ids = [r[0] for r in con.execute("SELECT event_id FROM real_games WHERE season=? AND season_type=2 ORDER BY event_id", [season]).fetchall()]
    games += [(season, e) for e in random.sample(ids, 6 if season >= 2024 else 4)]
c = ESPNClient(rate_limit=4.0)


def clock_s(p):
    v = (p.get("clock") or {}).get("displayValue") or "0"
    return float(v.split(":")[0]) * 60 + float(v.split(":")[1]) if ":" in v else float(v)


tot = {}
for season, eid in games:
    data = c.get_json(summary_url(), params={"event": eid})
    plays = data.get("plays") or []
    json.dump(plays, open(f"{out}/plays_{eid}.json", "w"))
    if not plays:
        print(season, eid, "NO PLAYS")
        continue
    served = [p["id"] for p in plays]
    by_sort = [p["id"] for p in sorted(plays, key=lambda p: (p["period"]["number"], -clock_s(p), int(p["id"])))]
    by_id = sorted(served, key=int)
    inv = sum(1 for a, b in zip(plays, plays[1:]) if a["period"]["number"] == b["period"]["number"] and clock_s(b) > clock_s(a) + 0.5)
    # positions that differ between served and sort
    diff = sum(1 for a, b in zip(served, by_sort) if a != b)
    diff_id = sum(1 for a, b in zip(served, by_id) if a != b)
    has_wall = sum(1 for p in plays if p.get("wallclock"))
    # among same-(period,clock) groups, does served order == id order?
    groups_bad = 0
    i = 0
    while i < len(plays):
        j = i
        key = (plays[i]["period"]["number"], clock_s(plays[i]))
        while j < len(plays) and (plays[j]["period"]["number"], clock_s(plays[j])) == key:
            j += 1
        g = [int(p["id"]) for p in plays[i:j]]
        if g != sorted(g):
            groups_bad += 1
        i = j
    print(
        season,
        eid,
        f"plays={len(plays)} served_clock_inversions={inv} served!=sorted_positions={diff} served!=id_positions={diff_id} sameclock_groups_not_in_id_order={groups_bad} wallclock={has_wall}",
    )
    t = tot.setdefault(season, [0, 0, 0, 0, 0])
    t[0] += len(plays)
    t[1] += inv
    t[2] += diff
    t[3] += diff_id
    t[4] += groups_bad
print("== totals per season: plays, served clock inversions, positions where served != (period,clock desc,id) sort, positions where served != id order, same-clock groups out of id order")
for s, t in sorted(tot.items()):
    print(s, t)
