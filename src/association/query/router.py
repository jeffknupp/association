"""A small, constrained-decoding intent router that runs BEFORE the tool-calling
agent.

The agent in agent.py asks one model to understand the question AND write
correct SQL, which forces prompt.py's whole schema and rule set resident for
every question - ~10k tokens that ollama truncates head-first and silently, and
that misses the KV prefix cache every iteration because the truncation offset
slides. Measured: ~70s per call, with the schema among the discarded tokens.

This module does only the first job. Its prompt carries no schema, no SQL and
no gotchas - an intent list and a few examples, ~430 tokens - so it fits, stays
cached, and answers in ~1-2s warm. Recognized intents go to a template in
templates.py; everything else falls through to the agent unchanged.

Slot values are advisory: every one of them is re-validated in templates.py
against a whitelist before it reaches SQL. Nothing here is trusted."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import ollama

from association.season import current_season

from .keepalive import KEEP_ALIVE
from .season_text import season_from_text

# Small enough to stay in ollama's prefix cache across calls, which is what
# makes the fast path fast - see the module docstring. Keep additions terse:
# one intent line plus one example is ~40 tokens, versus the ~400 a
# KNOWLEDGE_BASE entry costs on EVERY call in the old design.
ROUTER_PROMPT = """You classify NBA statistics questions into a query intent and its slots.
Reply with JSON only.

intent must be one of:
  leaderboard      - rank players by a SEASON stat: a per-game average or a
                     season total ("top 5 scorers", "who leads in assists")
  single_game_high - the highest single-GAME total, and which game it was
                     ("most assists in a single game", "highest scoring game",
                     "career high this season") - never use leaderboard for
                     these, a season average is a different question
  threshold_count  - count a player's games meeting a per-game threshold
                     ("most 30+ point games", "most games with 20+ rebounds")
  player_stat      - one named player's numbers for ONE season ("how many points
                     did Curry average", "what are Jokic's numbers") - set player
  player_netpoints - one named player's NetPoints and play-type fingerprint
                     ("SGA's netpoint stats", "Jokic NetPoints breakdown")
  player_history   - one named player's stat across SEVERAL seasons ("3pt% over
                     the past 4 seasons", "Jokic's scoring by year") - set
                     player, stat, and limit to the number of seasons
  game_log         - list a player's or team's games, or one specific game
                     ("Lakers last 5 games", "Curry's first game of the season")
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
  shot_distance    - how FAR a player's shots were ("average 3pt shot distance",
                     "how far away does Curry shoot from")
  player_compare   - two or more named players side by side ("Luka vs SGA",
                     "compare Curry and Lillard") - set players, not player
  other            - anything else, including a named PLAYER's per-quarter
                     scoring (team_quarter_points is only for a TEAM's) and
                     shot distances

stat names a box-score category: points, rebounds, assists, steals, blocks,
turnovers, minutes, fouls, threePointFieldGoalsMade, fieldGoalsMade, freeThrowsMade,
or a shooting percentage: threePointFieldGoalPct, fieldGoalPct, freeThrowPct.
Set it whenever the question names one - for threshold_count, leaderboard and
player_stat alike. Omit it only when the question asks for overall numbers.

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
Q: Who had the most 30+ point games this season?
{"intent":"threshold_count","stat":"points","threshold":30,"season_ref":"current"}
Q: Most games with 20+ rebounds in 2024?
{"intent":"threshold_count","stat":"rebounds","threshold":20,"season":2024}
Q: Who had the most assists in a single game and how many did he have?
{"intent":"single_game_high","stat":"assists","season_ref":"current"}
Q: What was the highest scoring game by a player this year?
{"intent":"single_game_high","stat":"points","season_ref":"current"}
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
Q: What was Klay Thompson's 3pt percentage over the past 4 seasons?
{"intent":"player_history","player":"Klay Thompson","stat":"threePointFieldGoalPct","limit":4}
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
Q: What was Steph Curry's avg 3pt shot distance?
{"intent":"shot_distance","player":"Stephen Curry","shot_value":3,"season_ref":"current"}
Q: Create a shot chart of Steph Curry's last regular season game
{"intent":"shot_chart","player":"Stephen Curry","order":"recent","season_ref":"current"}
Q: Show me Wembanyama's shot chart
{"intent":"shot_chart","player":"Victor Wembanyama","season_ref":"current"}
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
                "single_game_high",
                "threshold_count",
                "player_stat",
                "player_history",
                "player_netpoints",
                "player_compare",
                "game_log",
                "team_record",
                "head_to_head",
                "team_quarter_points",
                "shot_chart",
                "shot_distance",
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

ROUTER_NUM_CTX = 4096  # the router prompt is ~430 tokens; this leaves ample headroom and still fits
"""The router's context window.

.. versionchanged:: 1.2.0
   Renamed from ``NUM_CTX``, which collided with the agent's own window.
"""

# ESPN's earliest season in this warehouse, and a season can legitimately be
# next year's during the autumn rollover - anything outside this is a model
# slip (confirmed live: "last season" once produced season=20222023), so it is
# dropped rather than passed to SQL as a filter that silently matches nothing.
MIN_SEASON = 1990

# The model picks a word; the numeric season_type every table uses is looked up
# here. Without this slot a playoff question silently answers for the regular
# season - the same "answered an easier question and said nothing" failure the
# standing rules were written to prevent.
SEASON_TYPES = {"regular": 2, "playoffs": 3}

# Questions no template can answer, recognized from the text rather than left
# to the model. Deliberately tiny: not a rules engine, just subjects that read
# like a supported shape ("Steph Curry's average X") while asking for something
# no template computes, so a near-miss template absorbs them confidently.
# Shot distance was the first entry and left by earning a template - a subject
# belongs here only until one covers it.
# "Fouling out" is six personal fouls - an NBA rule, not something a 3B knows.
# It got the shape right (threshold_count) but emitted stat "fouls committed"
# and threshold 1; the template refused, and the question then hung in the agent
# until aborted at 95s. Normalized here so the rule lives in one place.
_FOULED_OUT = re.compile(r"\bfoul(?:ed|s|ing)?\s+out\b")
FOUL_OUT_THRESHOLD = 6

_AGENT_ONLY = re.compile(r"\b(?:first|second|third|fourth|1st|2nd|3rd|4th)\s+quarter\b|\bper\s+quarter\b|\bby\s+quarter\b")


# A TEAM's quarter score (no player named) is exempted below: linescores answer
# it exactly, via templates.team_quarter_points. A PLAYER's still has no
# template - it needs the fragile plays-table derivation - and stays forced to
# the agent.
def _is_team_quarter_points(raw: dict[str, Any]) -> bool:
    return raw.get("intent") == "team_quarter_points" and not (isinstance(raw.get("player"), str) and raw["player"].strip())


@dataclass
class Route:
    """`slots` holds only values that survived validation - a dropped slot is
    absent, never a sentinel, so a template's own default applies normally."""

    intent: str
    slots: dict[str, Any] = field(default_factory=dict)


def _validate_season(slots: dict[str, Any], question: str = "") -> int | None:
    """Resolve the season the code's way, not the model's. season_ref is
    deliberately an enum the model can only pick from, because relative-date
    arithmetic ("last season") is arithmetic, not language - it belongs here,
    next to current_season(), not in a prompt."""
    # The question text first: it is the source, and the model drops this slot
    # often enough that deferring to it silently answered for the wrong season.
    from_text = season_from_text(question)
    if from_text is not None:
        return from_text
    season = slots.get("season")
    if isinstance(season, int) and MIN_SEASON <= season <= current_season() + 1:
        return season
    ref = slots.get("season_ref")
    if ref == "previous":
        return current_season() - 1
    if ref == "current":
        return current_season()
    return None


def route(model: str, question: str, previous_question: str | None = None) -> Route | None:
    """Classify one question. Returns None if the model is unreachable or
    replies with something unparseable - the caller falls through to the full
    agent, so a router failure costs a round trip, never an answer."""
    user = f"Q: {question}"
    if previous_question:
        # The `ai` REPL gets real follow-ups ("what about 2025?") that are not
        # self-contained. One line of prior context is enough to resolve them
        # and costs ~15 tokens; the full conversation is not replayed here,
        # since that would defeat the fixed, cache-friendly prefix.
        user = f"(previous question, for context only: {previous_question})\n{user}"
    try:
        response = ollama.chat(
            model=model,
            messages=[{"role": "system", "content": ROUTER_PROMPT}, {"role": "user", "content": user}],
            format=ROUTER_SCHEMA,
            keep_alive=KEEP_ALIVE,
            options={"num_ctx": ROUTER_NUM_CTX, "temperature": 0},
        )
        raw = json.loads(response.message.content or "{}")
    except (ollama.ResponseError, json.JSONDecodeError, ConnectionError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("intent"), str):
        return None
    low = question.lower()
    if _FOULED_OUT.search(low):
        raw["intent"] = "threshold_count"
        raw["stat"] = "fouls"
        raw["threshold"] = FOUL_OUT_THRESHOLD
    if _AGENT_ONLY.search(low) and not _is_team_quarter_points(raw):
        # Slots are kept: the agent sees the conversation, not the Route, but
        # the log line shows what the model thought before the override.
        raw["intent"] = "other"

    # A blank string is how the model says "no value" for a required slot;
    # dropping it here keeps every template's `slots.get(...) or default`
    # working and keeps the logged Route readable.
    slots = {k: v for k, v in raw.items() if k != "intent" and not (isinstance(v, str) and not v.strip())}
    resolved_season = _validate_season(slots, question)
    slots.pop("season_ref", None)
    if resolved_season is None:
        slots.pop("season", None)
    else:
        slots["season"] = resolved_season
    requested_type = slots.get("season_type")
    slots["season_type"] = SEASON_TYPES.get(requested_type, 2) if isinstance(requested_type, str) else 2
    return Route(intent=raw["intent"], slots=slots)
