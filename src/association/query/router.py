"""A small, constrained-decoding intent router that runs BEFORE the tool-calling
agent.

The agent in agent.py asks one local model to do two jobs at once: understand
the question AND write correct SQL for it. That forces the entire schema and
every correctness rule in prompt.py to be resident for every question - ~10k
tokens of preamble, which does not fit in NUM_CTX, is silently truncated
head-first by ollama, and (because the truncation offset slides as the
conversation grows) misses the KV prefix cache on every iteration. Measured on
an 8-core CPU box: ~70s per model call, every call, with the schema itself
among the tokens thrown away.

This module does only the first job. Its prompt carries no schema, no SQL and
no gotchas - just an intent list and a few examples, ~430 tokens - so it fits,
stays cached, and answers in ~1-2s warm. Recognized intents are handed to a
deterministic template in templates.py; everything else falls through to the
existing agent unchanged.

Slot values are advisory: every one of them is re-validated in templates.py
against a whitelist before it reaches SQL. Nothing here is trusted."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import ollama

from association.season import current_season

# Small enough to stay in ollama's prefix cache across calls, which is what
# makes the fast path fast - see the module docstring. Keep additions terse:
# one intent line plus one example is ~40 tokens, versus the ~400 a
# KNOWLEDGE_BASE entry costs on EVERY call in the old design.
ROUTER_PROMPT = """You classify NBA statistics questions into a query intent and its slots.
Reply with JSON only.

intent must be one of:
  leaderboard      - rank players by a season stat ("top 5 scorers", "who leads in assists")
  threshold_count  - count a player's games meeting a per-game threshold
                     ("most 30+ point games", "most games with 20+ rebounds")
  player_stat      - one named player's season numbers ("how many points did Curry
                     average", "what are Jokic's numbers") - always set player
  game_log         - list a player's or team's games ("Lakers games in January")
  team_record      - a team's win/loss record
  shot_chart       - render/plot/visualize a player's shots
  other            - anything else, including double-doubles and triple-doubles,
                     per-quarter scoring, shot distances, and any comparison of
                     two or more named players

stat names a box-score category: points, rebounds, assists, steals, blocks,
turnovers, minutes, threePointFieldGoalsMade, fieldGoalsMade, freeThrowsMade.
Set it whenever the question names one - for threshold_count, leaderboard and
player_stat alike. Omit it only when the question asks for overall numbers.

For leaderboard, stat may instead be a rate or rating metric: ts_pct, efg_pct,
usage_pct, netpoints, netpoints_per_100, netpoints_offense, netpoints_defense,
or a NetPoints play-type category like rim_o_net_pts / driving_o_net_pts.

Set season ONLY when the question names an explicit 4-digit year. For "this
season" / "last season" / "this year" set season_ref instead, and omit season.
Set season_type to "playoffs" for a playoff/postseason question, otherwise
omit it. Set team when the question names one.

Examples:
Q: Who had the most 30+ point games this season?
{"intent":"threshold_count","stat":"points","threshold":30,"season_ref":"current"}
Q: Most games with 20+ rebounds in 2024?
{"intent":"threshold_count","stat":"rebounds","threshold":20,"season":2024}
Q: Who were the top 10 in netpoints/100 possessions?
{"intent":"leaderboard","stat":"netpoints_per_100","limit":10}
Q: Which player had the most triple-doubles?
{"intent":"other"}
Q: How many points did Luka Doncic average in 2024?
{"intent":"player_stat","player":"Luka Doncic","stat":"points","season":2024}
Q: What are Jokic's numbers this season?
{"intent":"player_stat","player":"Nikola Jokic","season_ref":"current"}
Q: Top 5 scorers on the Lakers?
{"intent":"leaderboard","stat":"points","team":"Lakers","limit":5}
Q: Who led the playoffs in rebounding?
{"intent":"leaderboard","stat":"rebounds","season_type":"playoffs","limit":1}
Q: Show me Steph Curry's threes from last season
{"intent":"shot_chart","player":"Stephen Curry","shot_value":3,"season_ref":"previous"}
"""

# A JSON schema passed as ollama's `format`, so decoding is CONSTRAINED to a
# well-formed object rather than merely asked for one. This removes the
# malformed/hallucinated tool-call failure mode outright - a class the old
# design could only defend against after the fact (see agent.py's
# _extract_unrun_sql and the pending_error fabrication guard), and one no
# amount of prompting reliably fixes on a small local model.
ROUTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["leaderboard", "threshold_count", "player_stat", "game_log", "team_record", "shot_chart", "other"],
        },
        "stat": {"type": "string"},
        "threshold": {"type": "integer"},
        "player": {"type": "string"},
        "team": {"type": "string"},
        "season": {"type": "integer"},
        "season_ref": {"type": "string", "enum": ["current", "previous"]},
        "season_type": {"type": "string", "enum": ["regular", "playoffs"]},
        "limit": {"type": "integer"},
        "shot_value": {"type": "integer"},
    },
    "additionalProperties": False,
    # `stat` is required, not because every intent has one, but because a
    # constrained decoder only reliably CONSIDERS a slot it is required to
    # emit. Confirmed live: with `stat` optional, "how many points did Luka
    # Doncic average in 2024?" came back without it even though that exact
    # question is a worked example in the prompt above - and reworking the
    # prompt did not fix it. Required, the same question emits
    # `"stat":"points"`, and a question with no stat emits `""`, which the
    # blank-slot pruning below drops. Prompt wording persuades; the schema
    # decides.
    "required": ["intent", "stat"],
}

NUM_CTX = 4096  # the router prompt is ~430 tokens; this leaves ample headroom and still fits

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


@dataclass
class Route:
    """`slots` holds only values that survived validation - a dropped slot is
    absent, never a sentinel, so a template's own default applies normally."""

    intent: str
    slots: dict[str, Any] = field(default_factory=dict)


def _validate_season(slots: dict[str, Any]) -> int | None:
    """Resolve the season the code's way, not the model's. season_ref is
    deliberately an enum the model can only pick from, because relative-date
    arithmetic ("last season") is arithmetic, not language - it belongs here,
    next to current_season(), not in a prompt."""
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
            options={"num_ctx": NUM_CTX, "temperature": 0},
        )
        raw = json.loads(response.message.content or "{}")
    except (ollama.ResponseError, json.JSONDecodeError, ConnectionError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("intent"), str):
        return None

    # A blank string is how the model says "no value" for a required slot;
    # dropping it here keeps every template's `slots.get(...) or default`
    # working and keeps the logged Route readable.
    slots = {k: v for k, v in raw.items() if k != "intent" and not (isinstance(v, str) and not v.strip())}
    resolved_season = _validate_season(slots)
    slots.pop("season_ref", None)
    if resolved_season is None:
        slots.pop("season", None)
    else:
        slots["season"] = resolved_season
    requested_type = slots.get("season_type")
    slots["season_type"] = SEASON_TYPES.get(requested_type, 2) if isinstance(requested_type, str) else 2
    return Route(intent=raw["intent"], slots=slots)
