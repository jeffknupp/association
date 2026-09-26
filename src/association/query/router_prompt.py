"""What the router model is told: its system prompt, the JSON schema its reply is
decoded against, and the context window both have to fit in.

Kept apart from :mod:`association.query.router`, which calls the model and then
post-processes the slots it returns, because the two change for different
reasons and at different risk. Any edit to this text moves slots on unrelated
questions (AGENTS.md, "Any edit to ROUTER_PROMPT moves slots on unrelated
questions"): hash :data:`ROUTER_PROMPT` and :data:`ROUTER_SCHEMA` before and
after a change here and re-run ``scripts/check_routing.py``. An edit to
``router.py`` alone cannot change what the model sees.

.. versionadded:: 3.0.0
   Split out of :mod:`association.query.router`.
"""

from __future__ import annotations

from typing import Any

# Small enough to stay in ollama's prefix cache across calls, which is what
# makes the fast path fast - see the module docstring. Keep additions terse:
# one intent line plus one example is ~40 tokens, versus the ~400 a
# KNOWLEDGE_BASE entry costs on EVERY call in the old design.
ROUTER_PROMPT = """You classify NBA statistics questions into a query intent and its slots.
Reply with JSON only.

intent must be one of:
  leaderboard      - rank players by a SEASON stat: a per-game average or a
                     season total ("top 5 scorers", "who leads in assists")
  player_stat      - one named player's numbers for ONE season ("how many points
                     did Curry average", "what are Jokic's numbers") - set player
  player_netpoints - one named player's NetPoints and play-type fingerprint
                     ("SGA's netpoint stats", "Jokic NetPoints breakdown")
  game_log         - list a player's or team's games, or one specific game
                     ("Lakers last 5 games", "Curry's first game of the season")
                     - set opponent for games against one named team
  team_record      - one team's win/loss record for a season
  head_to_head     - games between TWO named teams ("how many times did the
                     76ers play Boston", "Lakers vs Celtics record") - set teams
  team_quarter_points - a named TEAM's total points in ONE quarter/period this
                     season, optionally against one named opponent - NEVER for
                     a named PLAYER ("how many points did the 76ers score in
                     the 4th quarter", "Celtics 3rd quarter scoring against
                     the Lakers") - set team, period (1-4 for Q1-Q4, 5+ for
                     OT1/OT2/...), and opponent when a second team is named
  shot_chart       - render/plot/visualize a player's shots
  fingerprint      - render/plot/visualize a player's NetPoints FINGERPRINT: the
                     play-type radar ("plot SGA's fingerprint", "show me
                     Wembanyama's defensive fingerprint chart") - set player,
                     and set side to "offense", "defense" or "total". Set
                     players instead of player to plot two on one radar
                     ("compare SGA and Jokic's fingerprints")
  player_compare   - two or more named players side by side ("Luka vs SGA",
                     "compare Curry and Lillard") - set players, not player
  team_stat        - one TEAM's numbers ("Celtics points per game") - set team
  team_leaderboard - rank TEAMS by a stat ("best defense", "best record")
  team_outlook     - a team's BPI, playoff or title odds, projections - set team
  with_without     - a record or stats with or without a teammate
  player_matchup   - games two named players played AGAINST each other
  other            - anything else, including a named PLAYER's per-quarter
                     scoring (team_quarter_points is only for a TEAM's)

stat names a box-score category: points, rebounds, assists, steals, blocks,
turnovers, minutes, fouls, threePointFieldGoalsMade, fieldGoalsMade, freeThrowsMade,
or a shooting percentage: threePointFieldGoalPct, fieldGoalPct, freeThrowPct.
Set it whenever the question names one - for leaderboard and player_stat
alike. Omit it only when the question asks for overall numbers.

NetPoints for ONE named player is player_netpoints, not leaderboard - a
leaderboard ranks the league. Its fingerprint is reported per 100 possessions;
set rate to "total" only if the question asks for season totals. For leaderboard, stat may instead be
double_double, triple_double, or a rate
or rating metric: ts_pct, efg_pct, usage_pct, netpoints, netpoints_per_100,
netpoints_offense, netpoints_defense, or a NetPoints play-type category like
rim_o_net_pts / driving_o_net_pts.

Set season to the 4-digit year whenever the question names one. Otherwise set
season_ref: "previous" for "last season"/"last year", "current" for anything else.
For leaderboard, set fields to the extra per-game box-score columns the
question also asks to see ("top 10 in NetPoints with their points and minutes"
-> fields ["points","minutes"]); omit it when only the ranked metric is asked
for. Set season_type to "playoffs" for a playoff/postseason question, otherwise
omit it. Set team when the question names one. For game_log and shot_chart always set order when the question is about
specific games: "first" ONLY for the earliest/opening game(s) of a season,
"recent" for the latest, the most recent, or "the last N games". A shot_chart
with order covers that ONE game rather than the whole season. Set date as YYYY-MM-DD only
when the question names an exact calendar day.

Examples:
Q: Who were the top 10 in netpoints/100 possessions?
{"intent":"leaderboard","stat":"netpoints_per_100","limit":10}
Q: How many points did Jokic score in the 3rd quarter against Boston?
{"intent":"other"}
Q: How many points did the 76ers score in the 4th quarter against Boston this season?
{"intent":"team_quarter_points","team":"Philadelphia 76ers","period":4,"opponent":"Boston Celtics","season_ref":"current"}
Q: Which player had the most triple-doubles?
{"intent":"leaderboard","stat":"triple_double","limit":1}
Q: Compare Luka and SGA this season
{"intent":"player_compare","players":["Luka Doncic","Shai Gilgeous-Alexander"],"season_ref":"current"}
Q: Who scores more, Wemby or Jokic?
{"intent":"player_compare","players":["Victor Wembanyama","Nikola Jokic"],"stat":"points"}
Q: What were SGA's netpoint stats this season?
{"intent":"player_netpoints","player":"Shai Gilgeous-Alexander","season_ref":"current"}
Q: How many points did Luka Doncic average in 2024?
{"intent":"player_stat","player":"Luka Doncic","stat":"points","season":2024}
Q: What are Jokic's numbers this season?
{"intent":"player_stat","player":"Nikola Jokic","season_ref":"current"}
Q: How many times did the 76ers play Boston?
{"intent":"head_to_head","teams":["Philadelphia 76ers","Boston Celtics"]}
Q: What was the Lakers record last season?
{"intent":"team_record","team":"Lakers","season_ref":"previous"}
Q: Show me the Knicks last 5 games
{"intent":"game_log","team":"New York Knicks","order":"recent","limit":5}
Q: How did the Celtics do in their last 10 games?
{"intent":"game_log","team":"Boston Celtics","order":"recent","limit":10}
Q: What was Curry's first game of the season?
{"intent":"game_log","player":"Stephen Curry","order":"first","limit":1}
Q: Top 5 scorers on the Lakers?
{"intent":"leaderboard","stat":"points","team":"Lakers","limit":5}
Q: Top 10 in NetPoints per 100 possessions with their points and minutes
{"intent":"leaderboard","stat":"netpoints_per_100","fields":["points","minutes"],"limit":10}
Q: Who led the playoffs in rebounding?
{"intent":"leaderboard","stat":"rebounds","season_type":"playoffs","limit":1}
Q: Show me Steph Curry's threes from last season
{"intent":"shot_chart","player":"Stephen Curry","shot_value":3,"season_ref":"previous"}
Q: Create a shot chart of Steph Curry's last regular season game
{"intent":"shot_chart","player":"Stephen Curry","order":"recent","season_ref":"current"}
Q: Show me Wembanyama's shot chart
{"intent":"shot_chart","player":"Victor Wembanyama","season_ref":"current"}
Q: Plot SGA's netpoints fingerprint
{"intent":"fingerprint","player":"Shai Gilgeous-Alexander","side":"total","season_ref":"current"}
Q: Show me Wembanyama's defensive fingerprint chart
{"intent":"fingerprint","player":"Victor Wembanyama","side":"defense","season_ref":"current"}
Q: Compare SGA and Jokic's fingerprints
{"intent":"fingerprint","players":["Shai Gilgeous-Alexander","Nikola Jokic"],"side":"total","season_ref":"current"}
Q: What were SGA's netpoints by play type?
{"intent":"player_netpoints","player":"Shai Gilgeous-Alexander","season_ref":"current"}
Q: jaylen brown last 8 games vs pistons
{"intent":"game_log","player":"Jaylen Brown","opponent":"Detroit Pistons","order":"recent","limit":8}
Q: Celtics record without Tatum
{"intent":"with_without","team":"Boston Celtics","season_ref":"current"}
Q: lebron vs kawhi head to head
{"intent":"player_matchup","players":["LeBron James","Kawhi Leonard"]}
"""

# Passed as ollama's `format`, so decoding is CONSTRAINED to a well-formed
# object rather than merely asked for one. That removes the malformed-tool-call
# failure mode outright - the class agent.py's _extract_unrun_sql and
# fabrication guard can only catch after the fact.
ROUTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": [
                "leaderboard",
                "player_stat",
                "player_netpoints",
                "player_compare",
                "game_log",
                "team_record",
                "head_to_head",
                "team_quarter_points",
                "shot_chart",
                "fingerprint",
                "team_stat",
                "team_leaderboard",
                "team_outlook",
                "with_without",
                "player_matchup",
                "other",
            ],
        },
        "stat": {"type": "string"},
        "threshold": {"type": "integer"},
        "player": {"type": "string"},
        # maxItems is not cosmetic. An unbounded array under constrained decoding
        # lets the grammar permit "one more item" forever, and the model takes
        # that offer: confirmed live, it emitted ["points","minutes","minutes"]
        # on one question and then hung for over five minutes on the next,
        # because at ~10 tok/s on CPU a looping array is a stall, not a typo.
        # Bound every array slot.
        "players": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        "team": {"type": "string"},
        "teams": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
        # team_quarter_points only. 1-4 for Q1-Q4, 5+ for OT1/OT2/... - the same
        # convention render_shot_chart's own `period` tool parameter already uses.
        "period": {"type": "integer"},
        "opponent": {"type": "string"},
        "season": {"type": "integer"},
        "season_ref": {"type": "string", "enum": ["current", "previous"]},
        "season_type": {"type": "string", "enum": ["regular", "playoffs"]},
        "order": {"type": "string", "enum": ["recent", "first"]},
        "rate": {"type": "string", "enum": ["per_100", "total"]},
        # fingerprint only: which of the three stored columns per play-type
        # category to draw.
        "side": {"type": "string", "enum": ["offense", "defense", "total"]},
        "date": {"type": "string"},
        "limit": {"type": "integer"},
        "shot_value": {"type": "integer"},
        "fields": {
            "type": "array",
            "items": {"type": "string", "enum": ["points", "rebounds", "assists", "steals", "blocks", "minutes"]},
            "maxItems": 4,
        },
    },
    "additionalProperties": False,
    # `stat` is required not because every intent has one, but because a
    # constrained decoder only reliably CONSIDERS a slot it must emit: optional,
    # it was dropped even for a question appearing verbatim as a worked example
    # above, and rewording the prompt did not fix it. Required, a question with
    # no stat emits `""`, which the blank-slot pruning below drops. Prompt
    # wording persuades; the schema decides.
    #
    # Only `stat`, though. Requiring `season_ref` too was measured and reverted:
    # it fixed one dropped season but crowded out others, replacing a named year
    # with the current one - worse than the miss it was meant to fix. Require
    # the one slot that pays for itself, not every slot you wish were filled.
    "required": ["intent", "stat"],
}

ROUTER_NUM_CTX = 4096
"""The router's context window.

The prompt is ~2,000 tokens of it (7,852 characters at the ~4 characters a
token measured for the agent's prompt; a comment here said "~430" for a long
time after the intent list outgrew it, and it was ~2,500 until the seven
kind-assigned intents left it in 4.5.0 - ``subject.KIND_ASSIGNED_INTENTS``). The rest holds the question, the
chat template and a reply of under 100 tokens of JSON. ollama truncates an
over-length prompt head-first and silently, which for this prompt means the
instructions go first and the examples stay, so
:data:`ROUTER_PROMPT_TOKEN_BUDGET` keeps the prompt clear of the window.

.. versionchanged:: 1.2.0
   Renamed from ``NUM_CTX``, which collided with the agent's own window.

.. versionchanged:: 3.0.0
   Moved from :mod:`association.query.router`, with :data:`ROUTER_PROMPT`,
   :data:`ROUTER_SCHEMA` and :data:`ROUTER_PROMPT_TOKEN_BUDGET`.
"""

# Three quarters of the window for the system prompt plus the user line, the
# same shape as prompt.PREAMBLE_TOKEN_BUDGET for the agent: a prompt past this
# is a bug, not a knob. The agent enforces its budget per question because its
# prompt is assembled per question; this one is a constant, so a test
# (test_the_router_prompt_leaves_room_for_the_question_and_the_reply) is the
# guard, and it fails the moment an added intent line pushes the prompt over.
# Then shorten the prompt, or raise ROUTER_NUM_CTX and this together.
ROUTER_PROMPT_TOKEN_BUDGET = ROUTER_NUM_CTX * 3 // 4
"""What the router's prompt and a long question may cost together, in tokens.

.. versionadded:: 2.2.0
"""
