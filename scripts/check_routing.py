#!/usr/bin/env python3
"""Routing regression check for the query fast path.

The router is the one part of the pipeline with no types and no unit-testable
contract - a prompt change can silently start routing "who leads in points" to
`other`, and nothing in pytest would notice. This runs a fixed question set
through route() only (no SQL, no answer), asserting intent and the slots that
matter, and prints per-question latency.

Needs ollama running with the model loaded. Cheap after the first call: the
router prompt is small enough to stay in the KV cache, so questions after the
first typically land in 1-2s.

    python scripts/check_routing.py [--model qwen2.5:7b]

Add a case whenever a shape is ported or a mis-route is found in the wild.
"""

from __future__ import annotations

import argparse
import sys
import time

from association.query.router import route
from association.query.templates import TEMPLATES
from association.season import current_season

# (question, expected intent, expected subset of slots)
CASES: list[tuple[str, str, dict]] = [
    ("Who had the most 30+ point games this season?", "threshold_count", {"stat": "points", "threshold": 30}),
    ("Most games with 20+ rebounds this year", "threshold_count", {"stat": "rebounds", "threshold": 20}),
    ("Most games with 15+ assists in 2024?", "threshold_count", {"stat": "assists", "threshold": 15, "season": 2024}),
    ("Who were the top 10 in netpoints/100 possesions?", "leaderboard", {"stat": "netpoints_per_100", "limit": 10}),
    ("Who leads the league in assists?", "leaderboard", {"stat": "assists"}),
    ("Top 5 scorers on the Lakers?", "leaderboard", {"stat": "points", "team": "Lakers", "limit": 5}),
    ("Who led the playoffs in rebounding?", "leaderboard", {"stat": "rebounds", "season_type": 3}),
    ("Best true shooting percentage last season?", "leaderboard", {"season": current_season() - 1}),
    ("How many points did Luka Doncic average in 2024?", "player_stat", {"player": "Luka Doncic", "stat": "points", "season": 2024}),
    ("What are Jokic's numbers this season?", "player_stat", {"player": "Nikola Jokic"}),
    ("How many rebounds is Wembanyama averaging?", "player_stat", {"stat": "rebounds"}),
    # Not ported - these must fall through, NOT be answered by a near-miss template.
    ("Which player had the most triple-doubles?", "other", {}),
    ("How many points did Jokic score in the 3rd quarter against Boston?", "other", {}),
    ("Compare Luka and SGA this season", "other", {}),
    ("Show me Wembanyama's shot chart", "shot_chart", {}),
    ("What was the Lakers record last season?", "team_record", {}),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen2.5:7b")
    args = parser.parse_args()

    failures = 0
    for question, want_intent, want_slots in CASES:
        started = time.monotonic()
        got = route(args.model, question)
        elapsed = time.monotonic() - started
        if got is None:
            print(f"FAIL  {elapsed:5.2f}s  {question}\n        router returned nothing")
            failures += 1
            continue
        wrong = {k: (v, got.slots.get(k)) for k, v in want_slots.items() if got.slots.get(k) != v}
        ok = got.intent == want_intent and not wrong
        path = "fast" if got.intent in TEMPLATES else "agent"
        print(f"{'ok  ' if ok else 'FAIL'}  {elapsed:5.2f}s  [{path:5s}] {got.intent:16s} {question}")
        if not ok:
            failures += 1
            if got.intent != want_intent:
                print(f"        intent: wanted {want_intent!r}, got {got.intent!r}")
            for key, (wanted, actual) in wrong.items():
                print(f"        slot {key}: wanted {wanted!r}, got {actual!r}")
    print(f"\n{len(CASES) - failures}/{len(CASES)} routed as expected")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
