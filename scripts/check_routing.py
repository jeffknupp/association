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

from association.cli.paths import default_db_path
from association.nba.season import current_season
from association.query.entities import override_invented_players, override_nicknames, restore_dropped_players, scope_from_question
from association.query.models import DEFAULT_ROUTER_MODEL
from association.query.router import RouterUnavailable, route
from association.query.templates import TEMPLATES
from association.query.templates.common import PLAYER_INTENTS, PLAYER_REQUIRED_INTENTS

# (question, expected intent, expected slots). A list-valued expectation is a
# SUBSET check: dropping a field the user asked for is a bug, while the router
# throwing in an extra one is only noise. A frozenset-valued one accepts ANY of
# its spellings, for a slot whose value the model may write either way without
# changing the answer - AGENTS.md's rule that a case asserts what changes the
# answer, never the encoding the model happened to pick. Measured: "how many
# times has embiid fouled out?" arrives with player='Joel Embiid' when the
# model fills it and 'embiid' when _subject_named_in restores it from the
# question, and both resolve to the same person. Add "known_gap": True to a case the
# router reliably gets wrong in a way that is visible rather than silent - it
# is reported but not counted as a failure, so a real regression still stands
# out.
CASES: list[tuple[str, str, dict]] = [
    ("Who had the most 30+ point games this season?", "threshold_count", {"stat": "points", "threshold": 30}),
    ("Most games with 20+ rebounds this year", "threshold_count", {"stat": "rebounds", "threshold": 20}),
    ("Most games with 15+ assists in 2024?", "threshold_count", {"stat": "assists", "threshold": 15, "season": 2024}),
    # No limit asserted: 10 is already the template's default.
    ("Who were the top 10 in netpoints/100 possesions?", "leaderboard", {"stat": "netpoints_per_100"}),  # codespell:ignore possesions - the misspelling is the test: users type it
    # #152: the rate by any of its names, on each side of the ball. The model
    # reaches for the season total; route() switches it to the per-100 variant.
    ("who were the top 10 in defensive netpoints / 100 possesions?", "leaderboard", {"stat": "netpoints_defense_per_100"}),  # codespell:ignore possesions - as typed
    ("who were the top 10 in adjusted defensive netpoints", "leaderboard", {"stat": "netpoints_defense_per_100"}),
    ("who were the top 10 players in offensive netpoints per 100 possessions", "leaderboard", {"stat": "netpoints_offense_per_100"}),
    ("who led the league in adjusted netpoints?", "leaderboard", {"stat": "netpoints_per_100"}),
    ("who were the top 10 in defensive netpoints / 90", "leaderboard", {"rate": "/ 90"}),
    # #153: a filler `order` narrows a chart to one game, and "last regular
    # season game" came back as the season before this one.
    ("show a shot chart of steph curry's 2025 season for 3 point shots", "shot_chart", {"player": "Stephen Curry", "season": 2025, "shot_value": 3}),
    ("show a shot chart of steph curry's last regular season game", "shot_chart", {"player": "Stephen Curry", "order": "recent"}),
    # #141: "all playoff games" carried no span at all - none of the "career" /
    # "all-time" / "ever" / "in history" words is in it - so the season
    # defaulted to the latest with data and one postseason was drawn and
    # presented as all of them, with nothing saying so.
    ("show a shot chart for steph curry in all playoff games", "shot_chart", {"player": "Stephen Curry", "season_type": 3, "span": "career"}),
    # #139: both conditions, as lines on box-score columns.
    ("who had the most 30+ point 10+ rebound games this year?", "threshold_count", {"stat": "points", "threshold": 30, "above": ["30+ point", "10+ rebound"]}),
    ("How many 20+ point 5+ assist games did luka have?", "threshold_count", {"player": "Luka Doncic", "above": ["20+ point", "5+ assist"]}),
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
    # ISSUES.md #170: this used to be pinned to "other" (nothing answered a
    # PLAYER's quarter against a named team), and stayed pinned after
    # period_split shipped for the plain "his 3rd quarter" shape because the
    # model, faced with a team word and no player it trusted enough to name,
    # answered as if the TEAM were the subject - team_quarter_points, which
    # has no player column, for a question about one man. Read from the
    # question's own grammar now (router._route_period_intents), the same
    # reader threshold_count and single_game_high use for their own dropped
    # subject. Only the period is asserted - see the 76ers case below for why.
    ("How many points did Jokic score in the 3rd quarter against Boston?", "period_split", {"period": 3}),
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
    # template honors the slot by refusing.
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
    # #138: the same drop, for threshold_count. "how many times has embiid
    # fouled out?" arrived with no player at all and answered the league's
    # leader in 6+-foul games (Karl-Anthony Towns) to a question about Joel
    # Embiid, who has 0 such games in the 2026 season it defaulted to.
    ("how many times has embiid fouled out?", "threshold_count", {"stat": "fouls", "threshold": 6, "player": frozenset({"embiid", "Joel Embiid"})}),
    # #148: the same drop again, for a threshold_count named with no verb at
    # all - "NAME games with ...". "jamal murray games with 2 threes including
    # playoffs" arrived with no player and answered the league's leader in
    # 2+-three-pointer games (Julian Champagnie), to a question about Murray,
    # who has 58 postseason games and 426 career games with 2+ threes made.
    # The full name is kept, not just "murray": the bare surname is five
    # players who all have a 2026 box score.
    #
    # yardstick-v2 F160: "including playoffs" ALSO used to read as
    # season_type=3 - PLAYOFFS ONLY - because _validate_season_type consulted
    # _PLAYOFF_WORDS alone, and "including playoffs" contains the word
    # "playoffs". That silently dropped the regular season the question asked
    # to keep (3 postseason games shown, 56 regular-season ones never read).
    # _BOTH_SEASON_TYPES_WORDS now catches this shape and reuses
    # season_type_unstated - the same "read both" meaning game_log's "last N
    # games" reader already carries - which threshold_count now honors too
    # (player_relation_season_type, in one combined `season_type IN (2, 3)`
    # read rather than a merge).
    (
        "jamal murray games with 2 threes including playoffs",
        "threshold_count",
        {"stat": "threePointFieldGoalsMade", "threshold": 2, "player": "jamal murray", "season_type_unstated": True},
    ),
    # yardstick-v2 F156: the same misreading on a career game_log - "stats vs
    # 76ers at home including playoffs" answered only the 7 playoff meetings,
    # then falsely told the reader "Only 7 games ... in his box scores" (a
    # claim about games it never read: 9 regular-season meetings existed).
    (
        # `opponent` not asserted: the model spells it "76ers" or "Philadelphia
        # 76ers" and both resolve to the same team - see the 76ers case above.
        "Payton Pritchard stats vs 76ers at home including playoffs game log",
        "game_log",
        {"player": "Payton Pritchard", "venue": "home", "season_type_unstated": True},
    ),
    # yardstick-v2 F116: the same misreading on a TEAM question (team_record).
    # Only the router's own slot is asserted here - team_record does not yet
    # honor season_type_unstated (check_scope now refuses it, correctly,
    # rather than silently answering the playoff road record alone as though
    # it covered "all-time ... including playoffs"); the template side is the
    # team agent's, not ported here.
    (
        "warriors all-time record including playoff record at away",
        "team_record",
        {"team": "Golden State Warriors", "venue": "away", "season_type_unstated": True},
    ),
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
    # ISSUES.md #172: the model filed "least" correctly into `rank` and, a
    # second time, into `team` - no franchise is named "least", so
    # `team_leaderboard` refused "no team matching 'least'" over a cause the
    # question never gave, on a question `since` (C4b) already lets it answer
    # in full. `team` is not asserted absent here (a subset check cannot say
    # that), but `rank` and `since` together are the two halves of the answer
    # that refusal was blocking.
    ("nba team with least playoff wins since 2022", "team_leaderboard", {"rank": "fewest", "since": 2022}),
    ("Longest winning streak in the NBA this season", "streak", {}),
    ("Celtics vs Bulls head to head record", "head_to_head", {}),
    # No table here holds a coach, so `route()` assigns this intent from the
    # question's own words and the template refuses, naming the real cause.
    # The point of the case is that the WORD decides it: whatever the model
    # replies with is overridden, so this should never move - and it is
    # asserted with no slots, because a coach question carries none.
    ("nick nurse coaching record all-time nba in december on the road", "coach", {}),
    ("who coached the bulls in 1996", "coach", {}),
    # #140: "past two seasons" put its "two" in `limit` instead of reading a
    # season span, and "show tyrese maxey's games against boston in the past
    # two seasons" answered his last 2 games of his CAREER (span="career") -
    # 2 games where 7 were asked for (3 vs BOS in 2025, 4 in 2026, measured on
    # player_game_log). `since` now reaches the window on its own; `limit` is
    # not asserted here because it must be ABSENT, which this checker's
    # subset comparison cannot express - see
    # test_a_past_n_seasons_count_word_does_not_become_a_limit in
    # tests/query/test_router.py for that half.
    ("show tyrese maxey's games against boston in the past two seasons", "game_log", {"player": "Tyrese Maxey", "opponent": "Boston Celtics", "since": current_season() - 1}),
    # yardstick-v2 F045: `_validate_range` used to read only an open "since
    # 2020" or a decade, so "2019-20 to 2023-24" fell back to the model's own
    # single-season slot and answered 3 games of one season where 16
    # regular-season games across five were asked for. Worse, `until` - the
    # range's OTHER end - was declared nowhere and honored nowhere even once
    # `since` itself was read right, so a CLOSED range answered as an open
    # one (AGENTS.md's own worst-failure-shape example). Both slots now come
    # from the router; `until` is honored on the player relation
    # (`_Span`/`player_games.Narrowed`) the same way `since` already is.
    ("Portis vs bulls 2019-20 to 2023-24", "player_stat", {"player": "Bobby Portis", "opponent": "Chicago Bulls", "since": 2020, "until": 2024}),
    # yardstick-v2 F103/F095: the same range parsing, on the team-side
    # questions the yardstick found it on. Only the router's own slots are
    # asserted here - `until` on the TEAM relation is the team agent's own
    # slot contract (`TeamNarrowed`/`TEAM_RELATION_SCOPING`), not ported here.
    ("Best record from 2010-11 to 2018-19 nba", "team_leaderboard", {"since": 2011, "until": 2019}),
    ("knicks record by month 2024 2025", "team_record", {"team": "New York Knicks", "since": 2024, "until": 2025}),
    # ISSUES.md #114: `stat` has no enum in ROUTER_SCHEMA, so a 2-point
    # percentage question routinely arrived at fieldGoalPct (the nearest stat
    # ROUTER_PROMPT actually teaches) and answered OVERALL shooting instead.
    # route() overrides it from the question text regardless of what the
    # model guessed.
    ("show me sga's 2pt percentage for the past 5 years", "player_history", {"stat": "twoPointFieldGoalPct"}),
    ("lebron's 2-pt percentage over the last 10 years", "player_history", {"stat": "twoPointFieldGoalPct"}),
    ("sga 2pt percentage this season", "player_stat", {"stat": "twoPointFieldGoalPct"}),
    # A plain field-goal or a 3-point question is left alone - only "2"/"two"
    # beside "pt"/"point" triggers the override.
    ("sga's field goal percentage this season", "player_stat", {"stat": "fieldGoalPct"}),
    # ISSUES.md #114: "who lead the league in avg 3 point distance" resolved
    # `stat` to the nearest real metric (threePointFieldGoalPct) and answered
    # a PERCENTAGE; the "shot distance" phrasing arrived with a filler
    # `player` slot and was refused for naming a player the question does not
    # mention - the wrong cause, since no leaderboard metric ranks distance
    # either way. Both now carry the sentinel `stat` `leaderboard`
    # (templates/players.py) refuses on by name, with no `player` slot left
    # for override_invented_players to misread.
    ("who lead the league in avg 3 point distance", "leaderboard", {"stat": "shot_distance"}),
    ("who lead the league in shot distance for 3 point shots", "leaderboard", {"stat": "shot_distance"}),
    # #156: a record "when X and Y played" is with_without's question - the
    # games they were all in beside the ones they were not - and record_when
    # needs a number to divide by, which none of these names.
    ("show me the 76ers record when both Embiid and Paul George played", "with_without", {"with_player": ["Embiid", "Paul George"]}),
    ("PHI record when Embiid with Paul George", "with_without", {"with_player": ["Embiid", "Paul George"]}),
    # A quarter or half that ranks PLAYERS is period_leaderboard, not the
    # team's own quarter and not a fall-through: both of these reached `other`
    # and the agent before it existed.
    ("who has the highest average 1st quarter points this season?", "period_leaderboard", {"period": 1}),
    ("knicks 1st quarter scoring leaders playoffs", "period_leaderboard", {"period": 1}),
    # A TEAM's half, which used to fall through: the model maps "first half"
    # onto period 1, and the linescore holds both quarters.
    ("Detroit Pistons most points in a first half this season", "team_quarter_points", {"half": 1, "rank": "most"}),
    ("Celtics 2nd half scoring this season", "team_quarter_points", {"half": 2}),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    # Defaults to the model the router actually ships with, not the agent's.
    parser.add_argument("--model", default=DEFAULT_ROUTER_MODEL)
    # A warehouse, because the second override checks the router's names
    # against the roster - see entities.override_invented_players.
    parser.add_argument("--db-path", default=default_db_path())
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
            if isinstance(want, list):
                missed = not set(want) <= set(actual or [])
            elif isinstance(want, frozenset):
                # Any of these spellings is right - see the CASES header.
                missed = actual not in want
            else:
                missed = actual != want
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
    try:
        sys.exit(main())
    except RouterUnavailable as exc:
        # Every case would fail the same way and none of them would be about
        # routing, so say it once. Caught here rather than around the call in
        # main() to keep that function under the complexity gate.
        print(f"cannot run: {exc}")
        sys.exit(1)
