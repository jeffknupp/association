#!/usr/bin/env python3
"""Routing regression check for the query fast path.

The router is the one part of the pipeline with no types and no unit-testable
contract - a prompt change can silently start routing "who leads in points" to
`other`, and nothing in pytest would notice. This runs a fixed question set
through route() only (no SQL, no answer), asserting intent and the slots that
matter, and prints per-question latency.

Needs ollama running with the model loaded, and a built warehouse - the second
override checks the router's player names against the roster. Cheap after the first call: the
router prompt is small enough to stay in the KV cache, so questions after the
first typically land in 1-2s.

Run only ONE of these at a time. Two concurrent runs on a CPU-only box put
ollama into a reload loop that wedges it for minutes - and note that ollama
reloads the model whenever num_ctx changes, so interleaving router calls
(4096) with agent calls (16384) costs a full ~60-80s model load each way.

    python scripts/check_routing.py [--model qwen2.5:3b] [--db-path ./nba.duckdb]

Add a case whenever a shape is ported or a mis-route is found in the wild.
"""

from __future__ import annotations

import argparse
import sys
import time

import duckdb

from association.query.entities import override_invented_players, override_nicknames, restore_dropped_players, scope_from_question
from association.query.models import DEFAULT_ROUTER_MODEL
from association.query.router import route
from association.query.templates import PLAYER_INTENTS, PLAYER_REQUIRED_INTENTS, TEMPLATES
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
    # No team asserted: the model writes "Lakers" or "Los Angeles Lakers" depending on the prompt's length,
    # both resolve to team_id 13, and AGENTS.md says a case asserts what changes the answer, not the encoding.
    ("Top 5 scorers on the Lakers?", "leaderboard", {"stat": "points", "limit": 5}),
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
    # A TEAM's (not a player's) quarter score IS ported - templates.team_quarter_points
    # reads it straight from games.home_linescores/away_linescores, no plays table
    # needed. Confirmed live: before this template and its router exemption existed,
    # this exact question tripped _AGENT_ONLY (any "Nth quarter" text forced "other")
    # and the agent then spent 3 model calls (~150s) on SQL that filtered a
    # nonexistent games.period column, a broken LAG() over play_id, and finally
    # compared home_team_id directly to 'PHI'/'BOS' - the opaque-id-vs-abbreviation
    # mistake its own ALWAYS-ON prompt rule warns against, on every one of those
    # calls, despite the head-to-head and quarter KNOWLEDGE_BASE entries both already
    # being selected for it. See router._is_team_quarter_points for the exemption and
    # tests/query/test_router.py for the coverage that exercises it without ollama.
    ("How many points did the 76ers score in the 4th quarter against Boston this season?", "team_quarter_points", {"period": 4}),
    ("Compare Luka and SGA this season", "player_compare", {}),
    ("Who scores more, Wemby or Jokic?", "player_compare", {"stat": "points"}),
    ("Luka vs Giannis this year", "player_compare", {}),
    ("Show me Wembanyama's shot chart", "shot_chart", {"player": "Victor Wembanyama"}),
    # Confirmed live: routed to player_stat and answered with a points/rebounds/
    # assists stat line, then (once forced to the agent) with an all-shots,
    # all-seasons average mislabeled as current-season three-point distance.
    # "3pt" comes back as shot_value 3 or as the equivalent box-score stat
    # depending on wording; shot_distance reads either, so only the intent is
    # asserted rather than the encoding the router happened to pick.
    ("what was steph curry's avg 3pt shot distance", "shot_distance", {}),
    ("How far away does Wembanyama shoot from?", "shot_distance", {}),
    ("Plot Curry's threes from last season", "shot_chart", {"season": current_season() - 1}),
    # A fingerprint plot and a fingerprint's NUMBERS are different intents over
    # the same table; "plot"/"chart"/"show me" is the whole difference, so both
    # directions are checked.
    ("Plot SGA's netpoints fingerprint", "fingerprint", {"player": "Shai Gilgeous-Alexander"}),
    ("Show me Wembanyama's defensive fingerprint chart", "fingerprint", {"side": "defense"}),
    # Two fingerprints on one radar is still `fingerprint`, not player_compare:
    # only the slot changes. Just the intent is asserted - the router splits two
    # names across `player`/`players` often enough that the template reads both.
    ("Compare SGA and Jokic's fingerprints", "fingerprint", {}),
    ("What were SGA's netpoint stats this season?", "player_netpoints", {"player": "Shai Gilgeous-Alexander"}),
    # The team slot is NOT asserted here. Adding the `fingerprint` intent to the
    # prompt flipped this question's slot from "Lakers" to "Los Angeles Lakers"
    # - reproducibly, and with any wording of the added intent line, since the
    # 3B router is sensitive to the prompt's length as well as its content.
    # Checked against the warehouse before relaxing it: resolve_team maps both
    # strings to team_id 13, and team_record returns the same sentence either
    # way. The season IS asserted, because getting that wrong changes the
    # answer.
    ("What was the Lakers record last season?", "team_record", {"season": current_season() - 1}),
    # Confirmed live: with no such intent this routed to team_record, fell
    # through, and the agent answered that two teams who met four times had
    # never played.
    #
    # Only the intent is asserted: for a city name with no nickname ("Boston"
    # rather than "the Celtics"), qwen2.5:3b reliably splits the two teams
    # across `team` and a one-element `teams` instead of both into `teams` -
    # a slot shape this script can't see past (it checks route() only, never
    # the template). That split used to make head_to_head refuse the question
    # and fall through to the agent's SQL (which then answered 45, not 4) even
    # though the intent above was already correct; templates.head_to_head now
    # treats `team` as a third candidate rather than rejecting it - see
    # test_head_to_head_reads_the_second_team_from_the_team_slot in
    # tests/query/test_templates.py for the coverage that actually exercises it.
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
    # Nicknames for players from the older end of the warehouse. The router
    # does not merely miss these, it INVENTS a player: measured, "The Answer"
    # became 'Klay Thompson', "The Glove" became 'Jayson Tatum', and "VC"
    # became 'Victor Claver' - each a real player who resolves cleanly, so the
    # answer came back confident and about the wrong person. The player slot is
    # asserted here precisely because it is the thing that was wrong, and these
    # pass on entities.override_nicknames, applied above - route() alone still
    # returns the invented name.
    ("Show me The Answer's avg points", "player_stat", {"player": "Allen Iverson", "stat": "points"}),
    ("How many points did The Glove average in 1996?", "player_stat", {"player": "Gary Payton", "season": 1996}),
    ("Show me VC's avg points in 2000", "player_stat", {"player": "Vince Carter", "season": 2000}),
    ("What did AI average in 2005?", "player_stat", {"player": "Allen Iverson", "season": 2005}),
    # A first name one player owns in practice. This one the router already
    # gets right on its own; the case is here so that stops being luck.
    ("Show me luka's avg points", "player_stat", {"player": "Luka Doncic"}),
    # The same invention without a nickname to blame it on: measured live,
    # this came back with 'Jusuf Nurkic' in the second slot and answered with a
    # confident table about him. It passes on
    # entities.override_invented_players, applied above - route() alone still
    # returns Nurkic, which is why the slot is what this case asserts.
    ("compare sga and embiid", "player_compare", {"players": ["Shai Gilgeous-Alexander", "Joel Embiid"]}),
    # Both halves at once: the router put an invented name in a SINGLE player
    # slot for a question naming two, so this was answered with one polygon
    # until entities.restore_dropped_players, applied above. route() alone
    # still returns player='Ben Simmons'.
    ("compare fingerprints for embiid vs jokic in 2026", "fingerprint", {"players": ["Joel Embiid", "Nikola Jokic"], "season": 2026}),
    # The router fills `order` only where ROUTER_PROMPT tells it to - game_log
    # and shot_chart - so these three came back with no order at all (3/3 at
    # temperature 0) and the template drew the whole season under a question
    # about one game. Read out of the question text instead; the fingerprint
    # template honours the slot by refusing.
    ("show me a fingerprint for steph curry's last game in 2026", "fingerprint", {"player": "Stephen Curry", "season": 2026, "order": "recent"}),
    ("fingerprint for curry's first game of 2026", "fingerprint", {"player": "Stephen Curry", "season": 2026, "order": "first"}),
    ("plot jokic's fingerprint for his last game", "fingerprint", {"player": "Nikola Jokic", "order": "recent"}),
    # The other direction, and the reason the patterns allow no filler words:
    # a season question must not grow an order and get narrowed to one game.
    ("show me a fingerprint for steph curry in 2026", "fingerprint", {"player": "Stephen Curry", "season": 2026}),
    # Shapes from real StatMuse queries (see CHANGES.md). Before these existed,
    # each was answered fast and about something else - the player swapped for
    # his own team, the opponent dropped, one season for a career.
    ("jaylen brown last 8 games vs pistons", "game_log", {"player": "Jaylen Brown"}),
    ("Demar derozan gamelog against nuggets", "game_log", {}),
    ("evan mobley avg against bucks", "player_stat", {}),
    ("which team scores the most points per game", "team_leaderboard", {}),
    ("Lowest defensive rating by a team this season", "team_leaderboard", {}),
    ("Knicks pace this season", "team_stat", {}),
    ("what are the celtics playoff odds", "team_outlook", {}),
    ("Nikola Jokic home and away splits", "player_splits", {"split": "home_away"}),
    ("Giannis Antetokounmpo stats by month", "player_splits", {"split": "month"}),
    ("Celtics record without Tatum", "with_without", {"without": ["Tatum"]}),
    # Both names. "Celtics record without Tatum and Brown" came back as 'Tatum'
    # alone, and the answer covered the games without ONE of the two players -
    # a different question, answered fluently. Read from the question text, so
    # ROUTER_PROMPT and ROUTER_SCHEMA are untouched and no other case can move.
    ("Celtics record without Tatum and Brown", "with_without", {"without": ["Tatum", "Brown"]}),
    ("Sixers record when Embiid scores 30 points this season", "record_when", {"threshold": 30}),
    ("lebron vs kawhi head to head", "player_matchup", {}),
    ("lakers longest winning streak this season", "streak", {}),
    ("Diabate career high assists", "single_game_high", {"stat": "assists", "span": "career"}),
    # The model drops the player here and the slot is optional, so nothing
    # downstream restored it: the answer was the league's high, to a question
    # about one man. Read from the question's grammar - see _subject_named_in.
    ("most points curry scored in a game this season", "single_game_high", {"stat": "points", "player": "curry"}),
    ("who has the most threes this season", "leaderboard", {"stat": "threePointFieldGoalsMade"}),
    ("career points leaders", "leaderboard", {"span": "career"}),
    ("Knicks home record this season", "team_record", {"venue": "home"}),
    # From the final corpus run: each was a slot the model put in the wrong place.
    ("kevin durant true shooting percentage career", "player_stat", {"stat": "ts_pct", "span": "career"}),
    ("luka ft log", "game_log", {}),
    ("Jokic career averages", "player_stat", {"span": "career"}),
    ("zach lavine vs nuggets last 8 games home", "game_log", {"venue": "home"}),
    ("how did curry do against the celtics this year", "player_stat", {}),
    # Came back as player_compare with the Celtics as the second "player".
    ("compare curry vs the celtics this season", "player_stat", {"opponent": "Boston Celtics"}),
    # Came back with the Celtics in `team` beside two players, where no
    # template read them: the two whole seasons were compared. With the
    # opponent in place, player_compare refuses it instead.
    ("compare curry and lebron vs the celtics", "player_compare", {"opponent": "Boston Celtics"}),
    ("worst record 2025-26", "team_leaderboard", {"rank": "worst"}),
    ("Longest winning streak in the NBA this season", "streak", {}),
    ("Celtics vs Bulls head to head record", "head_to_head", {}),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    # Defaults to the model the router actually ships with, not the agent's.
    parser.add_argument("--model", default=DEFAULT_ROUTER_MODEL)
    # A warehouse, because the second override checks the router's names
    # against the roster - see entities.override_invented_players.
    parser.add_argument("--db-path", default="./nba.duckdb")
    args = parser.parse_args()

    con = duckdb.connect(args.db_path, read_only=True)
    failures = 0
    for question, want_intent, want_slots in CASES:
        started = time.monotonic()
        got = route(args.model, question)
        elapsed = time.monotonic() - started
        # Assert on the slots a template will actually see, not the router's
        # raw output: nickname overrides are applied between the two, and a
        # case about a nickname would otherwise check the wrong thing.
        if got is not None:
            override_nicknames(question, got.slots)
            if got.intent == "fingerprint":
                restore_dropped_players(con, question, got.slots)
            # The same order agent.py applies them in: this is where a player
            # the router swapped for his own team comes back.
            scope_from_question(con, question, got.slots, reads_player=got.intent in PLAYER_INTENTS, needs_player=got.intent in PLAYER_REQUIRED_INTENTS)
            override_invented_players(con, question, got.slots)
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
