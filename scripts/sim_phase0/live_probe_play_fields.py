import json
import sys

import duckdb

from association.fetch.client import ESPNClient
from association.fetch.endpoints import summary_url

out = sys.argv[1]
c = ESPNClient(rate_limit=2.0)
for eid, season in (("401809511", 2026), ("401584959", 2024), ("291109009", 2010)):
    data = c.get_json(summary_url(), params={"event": eid})
    json.dump(data, open(f"{out}/{eid}.json", "w"))
    plays = data.get("plays") or []
    print("=====", eid, season, "plays served:", len(plays))
    if not plays:
        print("keys:", list(data.keys()))
        continue
    print("play keys:", sorted(plays[0].keys()))
    print("sample:", {k: plays[5].get(k) for k in plays[5] if k not in ("participants",)})
    ids = [int(p["id"]) for p in plays]
    seqs = [int(p.get("sequenceNumber", -1)) for p in plays]

    def clock_s(p):
        v = (p.get("clock") or {}).get("displayValue") or "0"
        return float(v.split(":")[0]) * 60 + float(v.split(":")[1]) if ":" in v else float(v)

    clk = [(p["period"]["number"], -clock_s(p)) for p in plays]
    print("served order == id order:", ids == sorted(ids), "| served == sequenceNumber order:", seqs == sorted(seqs), "| seq distinct:", len(set(seqs)) == len(seqs))
    print("served order clock inversions > 0.5s within a period:", sum(1 for a, b in zip(clk, clk[1:]) if a[0] == b[0] and b[1] < a[1] - 0.5))
    if "sequenceNumber" in plays[0]:
        by_id = [p["sequenceNumber"] for p in sorted(plays, key=lambda p: int(p["id"]))]
        print("id order == sequenceNumber order:", by_id == sorted(by_id, key=int))
    con = duckdb.connect("nba.duckdb", read_only=True)
    wh = {r[0] for r in con.execute("SELECT play_id FROM plays WHERE event_id=? AND season=?", [eid, season]).fetchall()}
    live = {p["id"] for p in plays}
    print("warehouse plays:", len(wh), "| ids only live:", len(live - wh), "| ids only warehouse:", len(wh - live))
    for pid in ("401809511347", "401584959267", "401584959268"):
        for i, p in enumerate(plays):
            if p["id"] == pid:
                print(
                    "  anomaly",
                    pid,
                    "served at index",
                    i,
                    "seq",
                    p.get("sequenceNumber"),
                    "wallclock",
                    p.get("wallclock"),
                    "clock",
                    p["clock"]["displayValue"],
                    "| neighbors:",
                    plays[i - 1]["id"],
                    plays[i - 1]["clock"]["displayValue"],
                    "->",
                    plays[i + 1]["id"],
                    plays[i + 1]["clock"]["displayValue"],
                )
