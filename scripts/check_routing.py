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

Run only ONE of these at a time. Two concurrent runs on a CPU-only box put
ollama into a reload loop that wedges it for minutes - and note that ollama
reloads the model whenever num_ctx changes, so interleaving router calls
(4096) with agent calls (16384) costs a full ~60-80s model load each way.

    python scripts/check_routing.py [--model qwen2.5:3b]

Add a case whenever a shape is ported or a mis-route is found in the wild.
"""

from __future__ import annotations

import argparse
import sys
import time

from association.query.agent import DEFAULT_ROUTER_MODEL
from association.query.router import route
from association.query.templates import TEMPLATES
from association.season import current_season

# (question, expected intent, expected slots). A list-valued expectation is a
# SUBSET check: dropping a field the user asked for is a bug, while the router
# throwing in an extra one is only noise. Add "known_gap": True to a case the
# router reliably gets wrong in a way that is visible rather than silent - it
# is reported but not counted as a failure, so a real regression still stands
# out.
CASES: list[tuple[str, str, dict]] = [
    ("Who had the most 30+ point games this season?", "threshold_count", {"stat": "points", "threshold": 30}),
    ("Most games with 20+ rebounds this year", "threshold_count", {"stat": "rebounds", "threshold": 20}),
    ("Most games with 15+ assists in 2024?", "threshold_count", {"stat": "assists", "threshold": 15, "season": 2024}),
    # No limit asserted: 10 is already the template's default.
    ("Who were the top 10 in netpoints/100 possesions?", "leaderboard", {"stat": "netpoints_per_100"}),
    # A single-game maximum is NOT a season ranking. Confirmed live: with no
    # such intent, "who had the most assists in a single game" was answered
    # "Nikola Jokic led the league in assists per game, at 10.7" in 1.76s -
    # a missing shape produces a confident answer to a different question.
    ("who had the most assists in a single game and how many did he have", "single_game_high", {"stat": "assists"}),
    ("What was the highest scoring game by a player this year?", "single_game_high", {"stat": "points"}),
    ("Most rebounds Jokic has had in one game?", "single_game_high", {"stat": "rebounds"}),
    # No season asserted: an absent season already means the current one in
    # every template, so its absence does not change the answer. Assert slots
    # that change the answer, not slots that merely restate a default.
    ("Who leads the league in assists?", "leaderboard", {"stat": "assists"}),
    ("Top 5 scorers on the Lakers?", "leaderboard", {"stat": "points", "team": "Lakers", "limit": 5}),
    ("Who led the playoffs in rebounding?", "leaderboard", {"stat": "rebounds", "season_type": 3}),
    (
        "Top 10 in NetPoints per 100 possessions with their points and minutes",
        "leaderboard",
        # No limit asserted: 10 is already the template's default, and
        # asserting a slot that restates a default only makes the check brittle.
        {"stat": "netpoints_per_100"},
    ),
    ("Top 5 scorers with their rebounds and assists", "leaderboard", {"fields": ["rebounds", "assists"]}),
    # Was a known_gap until the season came out of the question text in code
    # rather than the model's slot - see query/season_text.py.
    ("Best true shooting percentage last season?", "leaderboard", {"season": current_season() - 1}),
    ("How many points did Luka Doncic average in 2024?", "player_stat", {"player": "Luka Doncic", "stat": "points", "season": 2024}),
    ("What are Jokic's numbers this season?", "player_stat", {"player": "Nikola Jokic"}),
    # Confirmed live: with no multi-season shape this routed to leaderboard,
    # dropped the player entirely, and returned the league's true-shooting
    # leaders for 2020.
    (
        "what was klay thompson's 3pt percentage over the past 4 seasons (with attempts/makes)",
        "player_history",
        {"player": "Klay Thompson", "stat": "threePointFieldGoalPct", "limit": 4},
    ),
    ("Jokic's scoring over the last 3 seasons", "player_history", {"stat": "points"}),
    # Confirmed live: fell through to the agent, which called get_leaderboard
    # for the league, dropped SGA, and answered "Nikola Jokic leads the team".
    ("what were SGA's netpoint stats this season", "player_netpoints", {}),
    ("Show me Wembanyama's NetPoints breakdown", "player_netpoints", {}),
    ("How many rebounds is Wembanyama averaging?", "player_stat", {"stat": "rebounds"}),
    ("Which player had the most triple-doubles?", "leaderboard", {"stat": "triple_double"}),
    ("Most double-doubles this season?", "leaderboard", {"stat": "double_double"}),
    # Not ported - these must fall through, NOT be answered by a near-miss template.
    ("How many points did Jokic score in the 3rd quarter against Boston?", "other", {}),
    ("Compare Luka and SGA this season", "player_compare", {}),
    ("Who scores more, Wemby or Jokic?", "player_compare", {"stat": "points"}),
    ("Luka vs Giannis this year", "player_compare", {}),
    ("Show me Wembanyama's shot chart", "shot_chart", {"player": "Victor Wembanyama"}),
    # Confirmed live: routed to player_stat and answered with a points/rebounds/
    # assists stat line, then (once forced to the agent) with an all-shots,
    # all-seasons average mislabelled as current-season three-point distance.
    # "3pt" comes back as shot_value 3 or as the equivalent box-score stat
    # depending on wording; shot_distance reads either, so only the intent is
    # asserted rather than the encoding the router happened to pick.
    ("what was steph curry's avg 3pt shot distance", "shot_distance", {}),
    ("How far away does Wembanyama shoot from?", "shot_distance", {}),
    ("Plot Curry's threes from last season", "shot_chart", {"season": current_season() - 1}),
    ("What was the Lakers record last season?", "team_record", {"team": "Lakers", "season": current_season() - 1}),
    # Confirmed live: with no such intent this routed to team_record, fell
    # through, and the agent answered that two teams who met four times had
    # never played.
    ("how many times did the 76ers play boston?", "head_to_head", {}),
    ("Lakers vs Celtics record this season", "head_to_head", {}),
    # "last N games" means most recent, not earliest - confirmed live, the
    # router got this backwards and answered with October games.
    ("Show me the Knicks last 5 games", "game_log", {"team": "New York Knicks", "order": "recent", "limit": 5}),
    ("What were the Bulls last 3 games?", "game_log", {"order": "recent"}),
    ("What was Curry's first game of the season?", "game_log", {"order": "first"}),
    ("Lakers opening game of the season", "game_log", {"order": "first"}),
    # The router often expands a nickname to the full name ("Celtics" ->
    # "Boston Celtics"); both resolve, so only the intent is asserted here.
    ("How did the Celtics do in their last 10 games?", "game_log", {}),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    # Defaults to the model the router actually ships with, not the agent's.
    parser.add_argument("--model", default=DEFAULT_ROUTER_MODEL)
    args = parser.parse_args()

    failures = 0
    for question, want_intent, want_slots in CASES:
        started = time.monotonic()
        got = route(args.model, question)
        elapsed = time.monotonic() - started
        if got is None:
            print(f"FAIL  {elapsed:5.2f}s  {question}\n        router returned nothing", flush=True)
            failures += 1
            continue
        known_gap = bool(want_slots.get("known_gap"))
        wrong = {}
        for key, want in want_slots.items():
            if key == "known_gap":
                continue
            actual = got.slots.get(key)
            missed = not set(want) <= set(actual or []) if isinstance(want, list) else actual != want
            if missed:
                wrong[key] = (want, actual)
        ok = got.intent == want_intent and not wrong
        path = "fast" if got.intent in TEMPLATES else "agent"
        status = "ok  " if ok else ("GAP " if known_gap else "FAIL")
        print(f"{status}  {elapsed:5.2f}s  [{path:5s}] {got.intent:16s} {question}", flush=True)
        if not ok and not known_gap:
            failures += 1
            if got.intent != want_intent:
                print(f"        intent: wanted {want_intent!r}, got {got.intent!r}", flush=True)
            for key, (wanted, actual) in wrong.items():
                print(f"        slot {key}: wanted {wanted!r}, got {actual!r}", flush=True)
    print(f"\n{len(CASES) - failures}/{len(CASES)} routed as expected")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
