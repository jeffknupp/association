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
  shot_distance    - how FAR a player's shots were ("average 3pt shot distance",
                     "how far away does Curry shoot from")
  player_compare   - two or more named players side by side ("Luka vs SGA",
                     "compare Curry and Lillard") - set players, not player
  team_stat        - one TEAM's numbers ("Celtics points per game") - set team
  team_leaderboard - rank TEAMS by a stat ("best defense", "best record")
  team_outlook     - a team's BPI, playoff or title odds, projections - set team
  player_splits    - one player's home/away, starter/bench, wins/losses or monthly splits
  with_without     - a record or stats with or without a teammate
  record_when      - a team's record in games a player reached a stat threshold
  player_matchup   - games two named players played AGAINST each other
  streak           - longest winning/losing streak, or straight games with N+ of a stat
  other            - anything else, including a named PLAYER's per-quarter
                     scoring (team_quarter_points is only for a TEAM's)

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
Q: Nikola Jokic home and away splits
{"intent":"player_splits","player":"Nikola Jokic","season_ref":"current"}
Q: Celtics record without Tatum
{"intent":"with_without","team":"Boston Celtics","season_ref":"current"}
Q: lebron vs kawhi head to head
{"intent":"player_matchup","players":["LeBron James","Kawhi Leonard"]}
Q: Lakers longest winning streak this season
{"intent":"streak","team":"Los Angeles Lakers","season_ref":"current"}
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
                "fingerprint",
                "shot_distance",
                "team_stat",
                "team_leaderboard",
                "team_outlook",
                "player_splits",
                "with_without",
                "record_when",
                "player_matchup",
                "streak",
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

_AGENT_ONLY = re.compile(r"\b(?:first|second|third|fourth|1st|2nd|3rd|4th)\s+(?:quarter|qtr|q)\b|\bq[1-4]\b|\bqtrs?\b|\bper\s+quarter\b|\bby\s+quarter\b")

# A half is never a quarter, so no template answers one - not even for a TEAM,
# where team_quarter_points reads a period number and the model maps "first half"
# onto period 1. Kept apart from _AGENT_ONLY for that reason: the team exemption
# below applies to quarters only. "rj barrett 4th qtr log" is why both patterns
# grew abbreviations - it slipped past "quarter" and game_log answered with his
# whole last game.
_HALF_WORDS = re.compile(r"\b(?:first|second|1st|2nd)\s+half\b|\b[12]h\b|\bhalftime\b", re.IGNORECASE)


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


# The side of the ball a fingerprint asked for, recognized from the text. The
# words are matched whole so "offensive" and "defensive" count but a player
# named Offenberg would not.
SIDE_WORDS: dict[str, re.Pattern[str]] = {
    "offense": re.compile(r"\boffens(?:e|ive)\b", re.IGNORECASE),
    "defense": re.compile(r"\bdefens(?:e|ive)\b", re.IGNORECASE),
}

# Kept in step with ROUTER_SCHEMA's own enum by
# test_the_side_values_match_the_router_schema - two hand-maintained lists of
# the same thing is the shape that already produced the player_compare bug.
SIDE_VALUES = ("offense", "defense", "total")


def _validate_side(slots: dict[str, Any], question: str) -> str | None:
    """Which half of a fingerprint was asked for, the question first.

    Same reasoning as :func:`_validate_season` reading the year out of the
    text: the question is the source, and this slot is dropped often enough
    that deferring to the model silently answers a broader question than the
    one asked - the whole radar where its defensive half was wanted.

    Why it is dropped is worth writing down, because no prompt wording fixes
    it. `stat` is the one REQUIRED slot (see ROUTER_SCHEMA), and a constrained
    decoder fills what it must before what it may: on "Show me Wembanyama's
    defensive fingerprint chart" the model spends the adjective on
    stat="defensive" and then omits `side` entirely. Measured 6/6 at
    temperature 0, and that question appears verbatim as a worked example in
    ROUTER_PROMPT with the right answer next to it, so it is not a wording the
    prompt failed to cover.
    """
    named = [side for side, pattern in SIDE_WORDS.items() if pattern.search(question)]
    # Exactly one, or nothing. A question naming both halves is asking for the
    # whole radar, which is what leaving this unset already means.
    if len(named) == 1:
        return named[0]
    side = slots.get("side")
    return side if isinstance(side, str) and side in SIDE_VALUES else None


# The postseason, named in the question. The model sets season_type="playoffs"
# on questions that never mention them - measured at temperature 0, "Sga record
# 36 plus points" and "lebron vs kawhi 2015" both came back as playoff questions,
# and "tatum stats in the 2024 finals" came back as a regular-season one. Read
# from the text for the same reason the year is: the question is the source,
# and the model is wrong in both directions. "Title" and "championship" are left
# out on purpose - "title odds" is a regular-season projection.
_PLAYOFF_WORDS = re.compile(r"\b(?:playoffs?|post-?season|finals|elimination|game\s+(?:7|seven))\b", re.IGNORECASE)


# One round or game of the postseason. No table carries a round or a series
# game number, so no template can narrow to one: "tatum stats in the 2024 finals"
# was answered with his whole 2024 postseason, 19 games where the Finals were 5.
# A scoping slot, so every template refuses it rather than widening the question.
_ROUND_WORDS = re.compile(r"\bfinals\b|\b(?:first|second)\s+round\b|\bsemi-?finals?\b|\bgame\s+(?:7|seven)s?\b", re.IGNORECASE)


# A range of seasons rather than one. "since 2020" is every season from the one
# ending in 2020; a decade ("the 2010s") is the seasons ending in it. Stated this
# way, not guessed at, so a template that honours it can print the exact range.
_SINCE = re.compile(r"\bsince\s+(?:the\s+)?((?:19|20)\d\d)\b", re.IGNORECASE)
_DECADE = re.compile(r"\b(?:the\s+)?((?:19|20)\d)0'?s\b", re.IGNORECASE)

# "record" asked with a counting intent means wins and losses, not a count of
# games. Measured: "Sixers record when Embiid scores 30 points this season" came
# back as threshold_count and was answered with the league's 30-point games,
# Embiid dropped.
_RECORD = re.compile(r"\brecord\b", re.IGNORECASE)


def _validate_range(question: str) -> tuple[int, int | None] | None:
    """The first and last season a range covers - (first, None) for an open one."""
    since = _SINCE.search(question)
    if since is not None:
        return int(since.group(1)), None
    decade = _DECADE.search(question)
    if decade is not None:
        first = int(decade.group(1) + "0")
        return first, first + 9
    return None


def _validate_season_type(question: str) -> int:
    """The season type the question asks about: the postseason only when it
    says so. The model's own slot is not consulted - see _PLAYOFF_WORDS."""
    return SEASON_TYPES["playoffs"] if _PLAYOFF_WORDS.search(question) else SEASON_TYPES["regular"]


# Where a game was played. "Far away" and "fade away" are shot descriptions,
# not venues - "How far away does Wembanyama shoot from?" is a routing case.
_HOME = re.compile(r"\bhome\b(?!\s+runs?)", re.IGNORECASE)
_AWAY = re.compile(r"(?<!far )(?<!fade )\b(?:away|road)\b", re.IGNORECASE)


def _validate_venue(question: str) -> str | None:
    """ "home" or "away" when the question restricts itself to one of them.

    Both at once is a SPLIT ("home and away splits"), not a filter, so it sets
    nothing here - see _validate_split. A template that cannot restrict to a
    venue refuses one rather than answering the whole season: "Knicks home
    record this season" was answered 53-29, their overall record.
    """
    home, away = bool(_HOME.search(question)), bool(_AWAY.search(question))
    if home == away:
        return None
    return "home" if home else "away"


# A whole career rather than one season. "Career high" is the exception: with a
# season named ("career high this season") it means that season's best, and it
# is a worked example of single_game_high in ROUTER_PROMPT.
_CAREER_HIGH = re.compile(r"\bcareer[- ]highs?\b", re.IGNORECASE)
_SPAN_WORDS = re.compile(r"\b(?:career|all[- ]time|ever|(?:in|of)\s+(?:nba\s+)?history|of\s+all\s+time)\b", re.IGNORECASE)
_SEASON_WORDS = re.compile(r"\b(?:this|last|next)\s+(?:season|year)\b", re.IGNORECASE)


def _validate_span(question: str) -> str | None:
    """ "career" when the question asks about more than one season's worth of
    games at once. Measured before this existed: "career points leaders" and
    "Jokic career averages" were both answered with one season, fluently."""
    text = question
    if _CAREER_HIGH.search(text) and (season_from_text(question) is not None or _SEASON_WORDS.search(text)):
        text = _CAREER_HIGH.sub(" ", text)
    return "career" if _SPAN_WORDS.search(text) else None


# The words that end a teammate's name in "without X this season" and the like.
_NAME_STOPWORDS = frozenset(
    "this last in on since during for vs vs. versus against at and when while game games season seasons record stats stat playing played plays from over the a an any his her their".split()
)
_WITHOUT = re.compile(r"\bwithout\s+([A-Za-z][A-Za-z.'\-]*(?:\s+[A-Za-z][A-Za-z.'\-]*){0,2})", re.IGNORECASE)
_WITH = re.compile(r"\bwith\s+([A-Za-z][A-Za-z.'\-]*(?:\s+[A-Za-z][A-Za-z.'\-]*){0,2})", re.IGNORECASE)


def _name_after(pattern: re.Pattern[str], question: str) -> str | None:
    """The name following ``pattern``'s keyword, up to the first word that
    cannot be part of one. None when no name follows at all - "without a
    turnover" names nobody, and must not become a teammate called "a"."""
    match = pattern.search(question)
    if match is None:
        return None
    words: list[str] = []
    for word in match.group(1).split():
        if word.casefold() in _NAME_STOPWORDS:
            break
        words.append(word)
    return " ".join(words) or None


# Which split a player_splits question asks for. Exactly one or nothing.
SPLIT_WORDS: dict[str, re.Pattern[str]] = {
    "home_away": re.compile(r"\bhome\b.{0,15}\b(?:away|road)\b|\b(?:away|road)\b.{0,15}\bhome\b", re.IGNORECASE),
    "starter_bench": re.compile(r"\b(?:starter|starting|starts|bench|reserve)\b", re.IGNORECASE),
    "wins_losses": re.compile(r"\bin\s+(?:wins|losses)\b|\bwins\s+(?:vs\.?|versus|and|or)\s+losses\b", re.IGNORECASE),
    "month": re.compile(r"\b(?:by|each|per)\s+month\b|\bmonthly\b", re.IGNORECASE),
}

# Which end of a team ranking was asked for. The four are not two pairs: for a
# stat where lower is better, "fewest turnovers" and "worst in turnovers" sit
# at opposite ends, so the template - which knows the stat - resolves them.
RANK_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("worst", re.compile(r"\bworst\b", re.IGNORECASE)),
    ("best", re.compile(r"\bbest\b", re.IGNORECASE)),
    ("fewest", re.compile(r"\b(?:fewest|least|lowest)\b", re.IGNORECASE)),
    ("most", re.compile(r"\b(?:most|highest|top|leads?|leaders?)\b", re.IGNORECASE)),
)

_LOSING_STREAK = re.compile(r"\blos(?:ing|s|e)\s+streaks?\b|\bstraight\s+losses\b|\blosses\s+in\s+a\s+row\b|\bskid\b", re.IGNORECASE)

# A ranking of TEAMS, asked with a player-ranking intent. Measured: "which team
# scores the most points per game" came back as `leaderboard` and was answered
# with the players' scoring leaders - a table of real, correct numbers about the
# wrong kind of thing.
_TEAM_SUBJECT = re.compile(
    r"\b(?:which|what)\s+teams?\b|\bby\s+(?:a\s+)?teams?\b|\bper\s+team\b|\bteams?\s+(?:with\s+the|that|leaders|rankings?)\b",
    re.IGNORECASE,
)
_PLAYER_RANKING_INTENTS = frozenset({"leaderboard", "single_game_high", "threshold_count"})

# A fingerprint is one named artifact, and a question that never names it is not
# asking for one. Measured: "Plot Curry's threes from last season" came back as
# `fingerprint` under two different prompt revisions, having routed correctly
# only while the prompt happened to be a particular length.
_FINGERPRINT_WORDS = re.compile(r"\bfinger\s?prints?\b|\bradar\b|\bnet\s?points?\b|\bplay[- ]types?\b", re.IGNORECASE)
_SHOT_WORDS = re.compile(r"\bshots?\b|\bthrees\b|\b3s\b|\b(?:3|three)[- ]?pointers?\b|\bjumpers?\b|\blayups?\b|\bdunks?\b|\bchart\b", re.IGNORECASE)

# A per-game threshold, stated in the question ("scores 30 points", "36 plus
# points", "40 point games"). "3 point" is a shot type, not a threshold of three.
_THRESHOLD = re.compile(r"\b(\d{1,3})\s*(?:\+|plus|or\s+more)?\s*(points?|pts|rebounds?|boards|assists?|steals?|blocks?|turnovers?|threes|3s)\b", re.IGNORECASE)
_THRESHOLD_INTENTS = frozenset({"threshold_count", "record_when", "streak"})


def _threshold_from_text(question: str) -> int | None:
    """The first per-game threshold the question states, or None."""
    for match in _THRESHOLD.finditer(question):
        number = int(match.group(1))
        if number == 3 and match.group(2).casefold().startswith("point"):
            continue
        if number >= 1:
            return number
    return None


# How a question names one end of a season's games. Deliberately tight - the
# ordinal word has to sit directly on "game(s)", optionally across a count
# ("last 5 games") - because a miss costs nothing and a false positive would
# narrow a question that asked for a whole season. "Last season's best game"
# is the shape that rules out allowing filler words in between.
ORDER_WORDS: dict[str, re.Pattern[str]] = {
    "recent": re.compile(r"\b(?:last|latest|previous|most\s+recent)\s+(?:\d+\s+)?games?\b", re.IGNORECASE),
    "first": re.compile(r"\b(?:first|opening|earliest)\s+(?:\d+\s+)?games?\b", re.IGNORECASE),
}

ORDER_INTENTS: frozenset[str] = frozenset({"fingerprint", "game_log", "player_netpoints", "shot_chart", "shot_distance"})
"""Intents whose template honours ``order``, so filling it from the question can
only make the answer match what was asked.

The same list as the ``order`` entries in
:data:`association.query.templates.HONORED_SCOPING`, kept separately because a
router that imported the templates would invert the dependency, and guarded by
``test_the_order_intents_are_the_ones_that_honour_order``. Adding ``order``
anywhere else would be worse than leaving it off: ``check_scope`` refuses a
scoping slot the template cannot honour, so a question that answers today would
start falling through to the agent instead.

.. versionadded:: 2.1.0
"""


def _validate_order(slots: dict[str, Any], question: str) -> str | None:
    """Which end of the season was asked for, the question first.

    The third slot to need this, after ``season`` and ``side``, and dropped for
    the same structural reason rather than a wording one: ROUTER_PROMPT
    instructs ``order`` for ``game_log`` and ``shot_chart`` only, so a
    fingerprint question carries no instruction to fill it. Measured at
    temperature 0, "show me a fingerprint for steph curry's last game in 2026"
    came back with no ``order`` 3/3, and so did "his first game of 2026" and
    "for his last game" - while "his MOST RECENT game", the prompt's own
    wording, came back with it 3/3. The prompt is where the model learned the
    phrase, not the concept.

    That mattered because ``fingerprint`` honours ``order`` by refusing: with
    the slot missing there was nothing to refuse, so a question about one game
    was answered with the whole season's radar, titled with the season and
    saying nothing about the difference.

    Only ever fills a slot the model left empty - never overwrites one, and
    never removes one. The patterns here are tighter than the model's reading
    of the question ("his last home game" is a phrasing they miss), so
    outranking it would trade one silent narrowing for another.
    """
    order = slots.get("order")
    if isinstance(order, str) and order in ORDER_WORDS:
        return order
    named = [name for name, pattern in ORDER_WORDS.items() if pattern.search(question)]
    return named[0] if len(named) == 1 else None


# The words a question uses when it is actually asking about one stat, as
# opposed to asking who is better. Loose on purpose, and safe because of where
# it is used: see :func:`_named_a_stat`.
_STAT_WORDS = re.compile(
    r"\b(points?|scor\w*|pts|rebound\w*|boards|reb|assist\w*|passing|dimes|ast|steal\w*|stl|block\w*|blk|"
    r"turnover\w*|giveaways?|fouls?|minutes?|mins?|shoot\w*|shots?|three\w*|3pt|3-point\w*|field goals?|free throws?|"
    r"percentage|efficien\w*|usage|double-doubles?|triple-doubles?)\b",
    re.IGNORECASE,
)


def _named_a_stat(question: str) -> bool:
    """Whether the question asked about a particular stat at all.

    ``stat`` is the one REQUIRED slot in ``ROUTER_SCHEMA``, so the model fills
    it on every question whether or not the question named one: "compare sga
    and embiid" comes back with ``stat='points'`` 12 times out of 12. For
    ``player_compare`` that slot is not a detail - it collapses the whole line
    the template exists to show back to the single average it used to print.

    The same fix as ``_validate_side``, and forgiving in both directions
    *because it is used for one intent only*. A word this misses widens a
    comparison to the full line, which still holds the stat asked about; a word
    it matches too eagerly leaves the behavior exactly as it was. Neither can
    produce a wrong number, which is why the list may be loose here and could
    not be if `leaderboard` read it.
    """
    return bool(_STAT_WORDS.search(question))


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
    if (_AGENT_ONLY.search(low) and not _is_team_quarter_points(raw)) or _HALF_WORDS.search(low):
        # Slots are kept: the agent sees the conversation, not the Route, but
        # the log line shows what the model thought before the override.
        raw["intent"] = "other"
    if raw["intent"] in _PLAYER_RANKING_INTENTS and _TEAM_SUBJECT.search(question):
        # A team ranking has a template; a team's single-game record and a
        # count of team games do not, and answering either with players is the
        # substitution this exists to stop.
        raw["intent"] = "team_leaderboard" if raw["intent"] == "leaderboard" else "other"
    if raw["intent"] == "threshold_count" and _RECORD.search(question):
        raw["intent"] = "record_when"
    if raw["intent"] == "fingerprint" and not _FINGERPRINT_WORDS.search(question):
        raw["intent"] = "shot_chart" if _SHOT_WORDS.search(question) else "other"
    if raw["intent"] == "player_stat" and _CAREER_HIGH.search(question):
        # A career high is one game's total, which player_stat never reports.
        # Measured: "Diabate career high assists" was answered with his assists
        # per game.
        raw["intent"] = "single_game_high"

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
    slots["season_type"] = _validate_season_type(question)
    if raw["intent"] in _THRESHOLD_INTENTS and not isinstance(slots.get("threshold"), int):
        # Measured: "Sixers record when Embiid scores 30 points" came back with
        # the intent right and no threshold at all.
        threshold = _threshold_from_text(question)
        if threshold is not None:
            slots["threshold"] = threshold
    if raw["intent"] == "threshold_count" and not isinstance(slots.get("threshold"), int):
        # A count of games needs a threshold. Without one, "who has the most
        # threes" is a season ranking - measured, it arrived here with none and
        # fell through.
        raw["intent"] = "leaderboard"
    # The scoping slots below are read from the question and never asked of the
    # model: none is in ROUTER_SCHEMA, so adding them changed no grammar and can
    # have moved no other question's routing. A template that cannot honour one
    # refuses it (templates.check_scope) rather than answering a broader question.
    span = _validate_span(question)
    if span is not None:
        slots["span"] = span
        # A career is every season. A year the MODEL filled in ("current", by
        # default) would narrow it back to one; a year the question named is kept.
        if season_from_text(question) is None:
            slots.pop("season", None)
    venue = _validate_venue(question)
    if venue is not None:
        slots["venue"] = venue
    without = _name_after(_WITHOUT, question)
    if without is not None:
        slots["without"] = without
    playoff_round = _ROUND_WORDS.search(question)
    if playoff_round is not None:
        slots["round"] = playoff_round.group(0).casefold()
    # Measured: "most 3 pointers made since 2020" became season=2020 and was
    # answered as "the most games with 0+ 3-pointers in the 2020 regular season".
    seasons = _validate_range(question)
    if seasons is not None:
        slots["since"] = seasons[0]
        if seasons[1] is not None:
            slots["until"] = seasons[1]
        slots.pop("season", None)
    # A split is read for every intent, not only player_splits: it is a scoping
    # slot, so the template that answers one honours it and every other refuses.
    # Measured: "Joe Ingles stats when starting vs coming off the bench" was
    # answered with his season minutes, "Giannis stats by month" with his points
    # by season.
    splits = [name for name, pattern in SPLIT_WORDS.items() if pattern.search(question)]
    if len(splits) == 1:
        slots["split"] = splits[0]
    # Intent-specific: each means nothing to any other template, so each is
    # only added where one reads it - the same rule `side` follows below.
    if raw["intent"] == "with_without":
        with_player = _name_after(_WITH, question)
        if with_player is not None and without is None:
            slots["with_player"] = with_player
    if raw["intent"] == "player_splits" and slots.get("split") == "home_away":
        slots.pop("venue", None)  # a split over venues is not a filter to one
    if raw["intent"] == "team_leaderboard":
        rank = next((name for name, pattern in RANK_WORDS if pattern.search(question)), None)
        if rank is not None:
            slots["rank"] = rank
    if raw["intent"] == "streak":
        slots["kind"] = "loss" if _LOSING_STREAK.search(question) else "win"
    # fingerprint only - `side` means nothing to any other template, and adding
    # it elsewhere would put a slot in the trace that nothing reads.
    # player_compare only: every other template either needs the stat or
    # ignores it, and dropping it for `leaderboard` would leave it with no
    # metric to rank by.
    if raw["intent"] == "player_compare" and not _named_a_stat(question):
        slots.pop("stat", None)
    if raw["intent"] == "fingerprint":
        side = _validate_side(slots, question)
        if side is None:
            slots.pop("side", None)
        else:
            slots["side"] = side
    # Only for the templates that honour it - see ORDER_INTENTS for why adding
    # it anywhere else would cost an answer rather than sharpen one.
    if raw["intent"] in ORDER_INTENTS:
        order = _validate_order(slots, question)
        if order is None:
            # Only a value the schema cannot emit ever gets dropped here; a
            # valid one the patterns did not recognize is kept - see
            # _validate_order.
            slots.pop("order", None)
        else:
            slots["order"] = order
    return Route(intent=raw["intent"], slots=slots)
