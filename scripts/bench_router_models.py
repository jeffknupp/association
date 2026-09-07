#!/usr/bin/env python3
"""Benchmark candidate models on the real routing task.

Reuses check_routing.py's CASES, so this measures the actual job rather than a
proxy. Unlike the check, `known_gap` cases are SCORED here: a better model may
not have the gap, and hiding it would defeat the comparison.

    python scripts/bench_router_models.py qwen2.5:3b qwen2.5:7b llama3.2:3b

Measured on an 8-core CPU box (AMD EPYC 9645, no GPU, 16GB RAM), n=30, before
a CPUQuota was applied:

    model            size    intent   intent+slots   median      p90
    qwen2.5:7b       4.7GB   30/30       29/30       2.00s    3.10s
    llama3.1:8b      4.9GB   30/30       28/30       1.72s    2.29s
    phi4-mini        2.5GB   29/30       29/30       1.42s    2.03s
    gemma3:4b        3.3GB   29/30       28/30       1.17s    1.70s
    qwen2.5:3b       1.9GB   29/30       29/30       1.12s    1.35s
    llama3.2:3b      2.0GB   29/30       28/30       1.05s    1.53s
    granite3.3:2b    1.5GB   28/30       28/30       1.00s    1.28s
    qwen2.5:1.5b     1.0GB   29/30       27/30       0.93s    1.13s
    qwen3:4b         2.5GB      -            -      ~20s         -   (thinking)

Everything from 1.5B to 8B lands in 28-30/30, because constrained decoding does
the structural work and the model only classifies and fills slots. That is not
a 7B-sized job, which is why the router defaults to qwen2.5:3b. At n=30 a
one-case difference is inside the noise - treat the 3B/4B tier as tied and pick
on size and speed.

Thinking models are disqualified on latency, not accuracy: qwen3:4b spent ~20s
per question reasoning before emitting the same tiny JSON object.
"""
import json
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, "scripts")
from check_routing import CASES

from association.query.router import ROUTER_PROMPT, ROUTER_SCHEMA, SEASON_TYPES, _validate_season


def route_with(model, question):
    payload = {"model": model, "stream": False, "format": ROUTER_SCHEMA,
               "options": {"num_ctx": 4096, "temperature": 0},
               "messages": [{"role": "system", "content": ROUTER_PROMPT},
                            {"role": "user", "content": "Q: " + question}]}
    req = urllib.request.Request("http://localhost:11434/api/chat",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    t = time.monotonic()
    raw = json.load(urllib.request.urlopen(req, timeout=300))["message"]["content"]
    elapsed = time.monotonic() - t
    obj = json.loads(raw)
    slots = {k: v for k, v in obj.items() if k != "intent" and not (isinstance(v, str) and not v.strip())}
    season = _validate_season(slots)
    slots.pop("season_ref", None)
    if season is None:
        slots.pop("season", None)
    else:
        slots["season"] = season
    st = slots.get("season_type")
    slots["season_type"] = SEASON_TYPES.get(st, 2) if isinstance(st, str) else 2
    return obj.get("intent"), slots, elapsed


def score(model):
    intent_ok = slot_ok = 0
    times, errors = [], 0
    for question, want_intent, want_slots in CASES:
        try:
            intent, slots, elapsed = route_with(model, question)
        except Exception:
            errors += 1
            continue
        times.append(elapsed)
        if intent == want_intent:
            intent_ok += 1
        wrong = False
        for key, want in want_slots.items():
            if key == "known_gap":
                continue
            actual = slots.get(key)
            if (not set(want) <= set(actual or [])) if isinstance(want, list) else actual != want:
                wrong = True
        if intent == want_intent and not wrong:
            slot_ok += 1
    n = len(CASES)
    return {
        "model": model, "intent": f"{intent_ok}/{n}", "full": f"{slot_ok}/{n}",
        "intent_pct": 100 * intent_ok / n, "full_pct": 100 * slot_ok / n,
        "median_s": statistics.median(times) if times else float("nan"),
        "p90_s": (sorted(times)[int(len(times) * 0.9)] if len(times) > 2 else max(times, default=float("nan"))),
        "errors": errors,
    }


if __name__ == "__main__":
    print(f"{'model':22} {'intent':>8} {'intent+slots':>13} {'median':>8} {'p90':>8}  errors", flush=True)
    results = []
    for model in sys.argv[1:]:
        r = score(model)
        results.append(r)
        print(f"{r['model']:22} {r['intent']:>8} {r['full']:>13} {r['median_s']:7.2f}s {r['p90_s']:7.2f}s  {r['errors']}", flush=True)
    if len(sys.argv) > 1:
        print("\nNote: a one-case difference at n=30 is inside the noise.", flush=True)
