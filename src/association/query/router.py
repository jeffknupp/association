"""A small, constrained-decoding intent router that runs BEFORE the tool-calling
agent.

The agent in agent.py asks one model to understand the question AND write
correct SQL, which forces prompt.py's whole schema and rule set resident for
every question - ~10k tokens that ollama truncates head-first and silently, and
that misses the KV prefix cache every iteration because the truncation offset
slides. Measured: ~70s per call, with the schema among the discarded tokens.

This module does only the first job. Its prompt carries no schema, no SQL and
no gotchas - an intent list, its slots and worked examples, ~2,500 tokens
against a 4,096-token window (see :mod:`association.query.router_prompt`, which holds it) - so it
fits, stays cached, and answers in ~1-2s warm. Recognized intents go to a template in
the templates package; everything else falls through to the agent unchanged.

Slot values are advisory: every one of them is re-validated in the templates package
against a whitelist before it reaches SQL. Nothing here is trusted."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import ollama

from association.nba.season import current_season

from .keepalive import KEEP_ALIVE
from .measures import MEASURE_WORDS
from .router_prompt import ROUTER_NUM_CTX, ROUTER_PROMPT, ROUTER_SCHEMA
from .season_text import MIN_SEASON, season_from_text
from .team_metrics import STAT_ALIASES

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

# `[1-4]q` is the mirror of `q[1-4]` and was missing: "Duncan Robison 1q log"
# and "Devin Vassell nba player per game stats 1q" were both answered with a
# whole-game line in the 2026-09-15 feed replay. Same shape as the "4th qtr"
# gap that made these patterns grow abbreviations in the first place.
# `td3s` is here rather than in _SITUATION because it is not a narrowing at
# all, which is worth keeping straight: the feed replay filed "luka td3s home"
# under "condition dropped", but the venue was read correctly and honored -
# the fault is that `td3s` became shot_value 3, so the answer was his points
# per game at home instead of a count of triple-doubles. Triple-doubles exist
# as a leaderboard metric (metrics.triple_doubles, off player_season_stats),
# but nothing counts them for ONE player, and nothing can split them by venue,
# since that season table has no venue dimension - deriving them per game from
# box scores is exactly the agent's job. Spelled out, "triple double" already
# routes correctly; only the abbreviation is unreadable.
_AGENT_ONLY = re.compile(r"\b(?:first|second|third|fourth|1st|2nd|3rd|4th)\s+(?:quarter|qtr|q)\b|\bq[1-4]\b|\b[1-4]q\b|\bqtrs?\b|\bper\s+quarter\b|\bby\s+quarter\b|\btd3s?\b")

# A half is never a quarter. team_quarter_points reads a period number and the
# model maps "first half" onto period 1, which is wrong for a TEAM the same way
# it would be for a player - so half words are kept apart from _AGENT_ONLY,
# whose team exemption applies to quarters only, and instead always route
# through the period_split override below (a named player's half now has a
# template; a team's half still does not - see ISSUES.md #96). "rj barrett 4th
# qtr log" is why both patterns grew abbreviations - it slipped past "quarter"
# and game_log answered with his whole last game.
_HALF_WORDS = re.compile(r"\b(?:first|second|1st|2nd)\s+half\b|\b[12]h\b|\bhalftime\b", re.IGNORECASE)

# A quarter or half question that ranks PLAYERS rather than asking about one:
# "who has the highest average 1st quarter points this season?", "knicks 1st
# quarter scoring leaders playoffs". Before `period_leaderboard` existed these
# reached `other` and fell through, because the override below sends a period
# question with no named player there and had nothing else to send it to.
#
# This wins over the team exemption, and has to: the Knicks question routes to
# team_quarter_points with the team filled and no player, which is exactly the
# shape the exemption protects - and answering it would give the TEAM's first
# quarter where its players' were asked for.
_PERIOD_LEADERS = re.compile(r"\bleaders?\b|\bwho\b|\bwhich\s+player\b|\bleading\s+scorers?\b", re.IGNORECASE)


# A TEAM's quarter score (no player named) is exempted below: linescores answer
# it exactly, via templates.team_quarter_points. A PLAYER's quarter or half is
# templates.period_split's job now - it reads shot_chart rather than the
# fragile plays-table derivation this comment used to warn was needed - and the
# override below sends it there instead of forcing it to the agent.
def _is_team_quarter_points(raw: dict[str, Any]) -> bool:
    return raw.get("intent") == "team_quarter_points" and not (isinstance(raw.get("player"), str) and raw["player"].strip())


# A coach question, which has no answer here and is refused rather than left to
# fall through (templates/teams.py's COACH_REFUSAL says why, and what ESPN
# actually serves). Read from the question's own words for the same reason
# `period_split` is: the word is unmistakable, and a new ROUTER_SCHEMA enum
# value or ROUTER_PROMPT line would move slots on unrelated questions.
#
# Deliberately only the word itself and its inflections. No coach is named
# without it in practice ("nick nurse coaching record all-time"), and matching
# a bare surname would be the substring trap `players_named_in` was written
# against - "nurse" and "rivers" are ordinary words, and Doc Rivers, Nick Nurse
# and Quin Snyder are all real people a player question could name. Checked
# against the warehouse: no player or team has "coach" anywhere in its name, so
# the word cannot collide with a subject.
_COACH_WORDS = re.compile(r"\bcoach(?:es|ed|ing|es'|'s)?\b|\bhead\s+coach\b", re.IGNORECASE)

CODE_ASSIGNED_INTENTS: frozenset[str] = frozenset({"coach", "period_split", "period_leaderboard"})
"""Intents no model can emit, because :func:`route` assigns them from the
question's own text.

Kept out of ``ROUTER_SCHEMA``'s enum and out of ``ROUTER_PROMPT`` on purpose.
Both are load-bearing on every other question: a new enum value changes the
decoding grammar and a new prompt line changes slots on unrelated questions -
adding one reproducibly flipped "What was the Lakers record last season?" from
``team`` "Lakers" to "Los Angeles Lakers". A period is legible from the
question ("1q", "4th qtr", "first half") with no help from the model, so it
costs nothing to read it here and nothing to route on it.

``test_every_ported_template_has_an_intent_in_the_schema`` exempts these, and
the exemption is why this is a named constant rather than a literal in a test:
a template that is unreachable by BOTH routes is dead, and the two lists have
to disagree deliberately rather than by drift.

.. versionadded:: 2.2.0
"""

_ORDINAL_PERIODS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}

# Which quarter or half, in the forms questions actually use. All three shapes
# come from the feed: "1st quarter", "q1"/"1q", and "first half"/"2h".
_WHICH_QUARTER = re.compile(
    r"\b(?P<ordinal>first|second|third|fourth|1st|2nd|3rd|4th)\s+(?:quarter|qtr|q)\b|\bq(?P<qn>[1-4])\b|\b(?P<nq>[1-4])q\b",
    re.IGNORECASE,
)
_WHICH_HALF = re.compile(r"\b(?P<ordinal>first|second|1st|2nd)\s+half\b|\b(?P<hn>[12])h\b", re.IGNORECASE)


def _period_asked(question: str) -> dict[str, int] | None:
    """The period a question names, as ``{"period": n}`` or ``{"half": n}``.

    Read from the text rather than asked of the model, for the reason
    `_validate_side` records: `period` is in ROUTER_SCHEMA but only ever taught
    for a TEAM's quarter score, so on a player's question the model leaves it
    empty. None when the question says "by quarter" or "qtrs" without naming
    one - a breakdown across all four is a different shape, and this template
    answers one period.

    .. versionadded:: 2.2.0
    """
    half = _WHICH_HALF.search(question)
    if half is not None:
        named = half.group("ordinal")
        return {"half": _ORDINAL_PERIODS[named.lower()] if named else int(half.group("hn"))}
    quarter = _WHICH_QUARTER.search(question)
    if quarter is None:
        return None
    named = quarter.group("ordinal")
    if named is not None:
        return {"period": _ORDINAL_PERIODS[named.lower()]}
    return {"period": int(quarter.group("qn") or quarter.group("nq"))}


class RouterUnavailable(RuntimeError):
    """The router model could not be asked at all - ollama is down, or cannot
    serve that model.

    Distinct from :func:`route` returning None, which means the model answered
    with something unusable. Both fall through to the agent, and neither is
    fatal; what differs is the sentence the reader gets, and with
    ``--disable-fallthrough`` that sentence is the entire error.

    .. versionadded:: 4.4.0
    """


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
    # MIN_SEASON is the league's first season, not a data floor (coverage.py
    # refuses those, with the reason), and a season can legitimately be next
    # year's during the autumn rollover. Anything outside is a model slip -
    # confirmed live: "last season" once produced season=20222023 - so it is
    # dropped rather than passed to SQL as a filter that silently matches nothing.
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

# The regular season, named outright - the one further word
# `_route_game_log_recent_span` needs beside `_PLAYOFF_WORDS`: a "last N games"
# question that says "regular season" has stated its type as plainly as one
# that says "playoffs", and must keep meaning only that.
_REGULAR_SEASON_WORDS = re.compile(r"\bregular[- ]season\b", re.IGNORECASE)


# One round or game of the postseason. No table carries a round or a series
# game number, so no template can narrow to one: "tatum stats in the 2024 finals"
# was answered with his whole 2024 postseason, 19 games where the Finals were 5.
# A scoping slot, so every template refuses it rather than widening the question.
_ROUND_WORDS = re.compile(r"\bfinals\b|\b(?:first|second)\s+round\b|\bsemi-?finals?\b", re.IGNORECASE)

# One game of a playoff series, by number: "game 4", "game 7s". Read as a
# number rather than left in `situation` (where "Ayton stats in game 4 playoff
# games" refused), because the relation can find it - the nth game by date
# between two teams in one postseason (player_games.Narrowed.narrow_series_game).
# "game 7" used to be a `round`; it is a game like the others.
_GAME_N = re.compile(r"\bgame\s+([1-7])s?\b", re.IGNORECASE)

# A season named by ordinal: "his 18th season", "15th season played". The model
# reads the ordinal as a year - "his 18th season" came back as season 2018,
# with LeBron dropped entirely, and the answer was the 2018 league leaderboard
# - so a year the question itself does not name goes with it. Which year the
# ordinal IS needs the player, so the templates settle it after resolving him
# (templates.common.settle_ordinal_season).
_SEASON_N = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+season\b", re.IGNORECASE)  # codespell:ignore nd - an ordinal suffix


# A range of seasons rather than one. "since 2020" is every season from the one
# ending in 2020; a decade ("the 2010s") is the seasons ending in it. Stated this
# way, not guessed at, so a template that honors it can print the exact range.
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


# "past two seasons", "last 3 years": a relative window counted back from NOW,
# not a games count and not a season named outright. Read into `since` alone
# (never `until`) because "past N seasons" already ends at the current one -
# _span_of's `since` branch reaches every season from there through whatever
# the table holds, which is exactly "now" since nothing is played later. #140:
# "show tyrese maxey's games against boston in the past two seasons" put the
# "two" in `limit` instead (see _PAST_N_SEASONS_COUNT_WORDS in _names_a_count)
# and answered his last 2 games of his CAREER, not his last two SEASONS - 7
# games measured on player_game_log (3 in 2025, 4 in 2026).
_PAST_N_SEASONS_COUNT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}  # fmt: skip
#: Intents whose ``limit`` counts SEASONS rather than games, so a relative
#: window ("the past 5 years") is that count and not a `since` span.
_LIMIT_COUNTS_SEASONS: frozenset[str] = frozenset({"player_history"})

_PAST_N_SEASONS = re.compile(
    r"\b(?:past|last)\s+(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:seasons?|years?)\b",
    re.IGNORECASE,
)


def _validate_relative_season_span(question: str) -> int | None:
    """The first season "past/last N seasons" (or "...years") reaches, counted
    back from the current one - "past two seasons" from season 2026 is 2025
    and 2026, so `since=2025` alone already names exactly those two."""
    match = _PAST_N_SEASONS.search(question)
    if match is None:
        return None
    word = match.group(1).lower()
    count = _PAST_N_SEASONS_COUNT_WORDS.get(word) or int(word)
    return current_season() - count + 1


def _validate_season_type(question: str) -> int:
    """The season type the question asks about: the postseason only when it
    says so. The model's own slot is not consulted - see _PLAYOFF_WORDS."""
    return SEASON_TYPES["playoffs"] if _PLAYOFF_WORDS.search(question) else SEASON_TYPES["regular"]


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip

# A calendar day written the way people write it: "march 17", "Jan 19",
# "november 11 2019". The leading group is what makes a date a RANGE rather
# than a day - "since January 31st" starts a window and names no single game -
# and those are left for _SITUATION to refuse, since no template honors a
# range of dates.
_CALENDAR_DATE = re.compile(
    r"(?P<range>\b(?:since|after|before|from|through|until)\s+(?:the\s+)?)?"
    r"\b(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<year>(?:19|20)\d\d))?\b",  # codespell:ignore nd - an ordinal suffix
    re.IGNORECASE,
)


def _validate_date(question: str, season: int | None) -> str | None:
    """A calendar day as ``YYYY-MM-DD``, or None if the question names none.

    The year is not in the question and does not need to be, because a season
    fixes it: season Y runs from October of Y-1 through June of Y, so October
    to December belong to ``season - 1`` and January onward to ``season``. That
    is this project's own numbering (:func:`~association.nba.season.current_season`)
    applied to a month, not a guess - "Desmond bane march 17" against season
    2026 is 2026-03-17, and `game_log` answers it with that game.

    Read from the text for the same reason the year and the side of the ball
    are: the model is told to emit `date` only for an exact calendar day and
    routinely does not. Measured, "Desmond bane march 17" arrived with no
    `date` at all and `order="recent"`, and was answered with his most recent
    game - a month later, and the wrong question.

    Three things it will not do, each because the answer would be a guess
    rather than a reading:

    - **A year the question states wins.** "november 11 2019" is the calendar
      day, not November of whatever season 2019 resolves to.
    - **A date that opens a window is not a day.** "since January 31st" names a
      range no template honors; it is left to `_SITUATION` to refuse.
    - **No season, no date.** A career question has no season to fix the year
      on ("lebron on march 17 all time" spans 20 of them), so it refuses
      instead.

    .. versionadded:: 2.2.0
    """
    match = _CALENDAR_DATE.search(question)
    if match is None or match.group("range"):
        return None
    month = _MONTHS[match.group("month")[:3].lower()]
    day = int(match.group("day"))
    stated = match.group("year")
    if stated is not None:
        year = int(stated)
    elif season is not None:
        year = season - 1 if month >= 10 else season
    else:
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None  # "february 31"


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

# The subject of a single-game high or a threshold count, when the model
# drops it. Measured live: "most points curry scored in a game this season"
# comes back as single_game_high with NO player slot, and the answer is the
# league's high - Bam Adebayo's - to a question about one man; "how many times
# has embiid fouled out?" comes back as threshold_count with no player slot
# either, and the answer is the league's leader in 6+-foul games - Karl-
# Anthony Towns - to a question about Joel Embiid (#138). Both slots are
# optional (an empty one means "the league"), so nothing downstream restores
# either, and players_named_in cannot: "curry" is six players and it refuses
# to guess.
#
# So the subject is read from the GRAMMAR rather than from a word list. A word
# scan cannot work here: "best" is Travis Best, "game" is Jaron Blossomgame,
# "high" is Haywood Highsmith and "single" is four players, so scanning would
# hijack "the highest scoring game by a player this year". A name before a
# scoring verb or "fouled out", or carrying a possessive, is a subject; the
# question words are excluded because "who scored the most" names nobody.
_SUBJECT_WORDS = frozenset({"who", "what", "which", "that", "he", "she", "they", "it", "player", "anyone", "someone", "nobody", "team", "one", "the", "and", "any"})
#
# The leading word is optional and captured, the way the count grammars below
# capture one, because a one-word subject is a clarifying question where the
# question wrote the name out: measured over all 261 corpus questions, three
# possessives ("kobe bryant's", "Jaden mcdaniel's", "steve adam's") gave a
# bare surname matching four players each, and "bryant" does not even include
# Kobe. Which word may lead is decided in `_subject_named_in` rather than here,
# against `_COUNT_SUBJECT_WORDS` - the richer list, and the one that matters:
# without it "most points curry scored" would read "points curry" as the name.
#
# "had"/"has" is a subject position too, and a bare one - `_SUBJECT_OF_HAVE`
# below requires a leading "does/did/has/have", so "sixers record when maxey
# had 10+ rebounds" named nobody while the same question with "scored"
# answered. It is deliberately NOT in the alternation above: the words there
# are all scoring verbs and a possessive, where "had" is ordinary enough that
# it is only a subject position when a threshold follows it, which is what the
# lookahead asserts.
_SUBJECT_OF_HIGH = re.compile(
    r"\b(?:([A-Za-z][A-Za-z.'\-]*)\s+)?([A-Za-z][A-Za-z.'\-]{2,})"
    r"(?:'s\b|\s+(?:scored|scores|score|dropped|put\s+up|hung|shot|foul(?:ed|s|ing)?\s+out)|\s+ha[ds]\s+(?=\d))",
    re.IGNORECASE,
)

# None of the above covers a threshold_count named with no verb at all (#148):
# "jamal murray games with 2 threes including playoffs" fell through
# unrestored, the same shape as "Sga games with under 14 fta in his whole
# career", and (by number instead of "with") a form like "murray 30 point
# games" or "murray games of 20+ rebounds" - none of which puts a scoring verb
# or a possessive anywhere near the name. So a second grammar is tried after
# the first, anchored on "games" itself rather than a verb: a name directly
# before "games with"/"games of", or before "<N>[+] <stat> games". It also
# captures ONE more word immediately before that name, to catch a first name -
# "jamal murray games with" resolves uniquely where a bare "murray" is five
# players (Collin Murray-Boyles, Dejounte, Jamal, Keegan, Kris all have a 2026
# box score) and would only trade the league-ranking bug for an unnecessary
# clarifying question. The extra word is kept only when it is not itself one
# of the stopwords below - "which players have games with 30+ points" must not
# read "players have" as a name.
#
# Measured against the full routing corpus
# (`/home/jeff/association-research/statmuse-2026-09/feed_queries.txt`), this
# grammar matches exactly the two real cases above and nothing else -
# "last 10 games of scottie barnes" does not match because the word before
# "games" there is "10", not a letter, and "bam adebayo career games in the
# month of march" does not match because "career" sits directly before
# "games" and is a stopword, not a name (there is no verb-based grammar this
# shape fits either, so it is left unrestored rather than guessed at). The
# extra stopwords below ("career", "has", "many", "postseason", ...) are what
# make that refusal-by-omission work: without them, "bam adebayo career games"
# would read "career" as the player and "how many 40+ points games does
# lebron james have" would read "many" - both a refusal naming the wrong
# cause (AGENTS.md, "the same bug has a mirror image"), not a name the
# question ever offered as the subject. This grammar is kept separate from
# `_SUBJECT_OF_HIGH` rather than folded into it: a shared pattern let a
# trailing possessive ("murray's games of...") get swallowed whole into the
# captured word before the new "games of" alternative even got a chance to
# apply, since the possessive's own `'s\b` branch was no longer the only way
# to succeed. Kept apart, `_SUBJECT_OF_HIGH` matches "murray" via its own
# `'s\b` branch exactly as it always did, and this grammar is never reached
# for that question at all.
_COUNT_SUBJECT_WORDS = _SUBJECT_WORDS | frozenset(
    {
        # A stat's own name sits before "10+ rebound games" in "the most 30+
        # point 10+ rebound games", where it is the first line's noun, not a
        # subject: read as one, "point" resolved to Sir'Dominic Pointer.
        "point",
        "points",
        "pt",
        "pts",
        "rebound",
        "rebounds",
        "reb",
        "rebs",
        "assist",
        "assists",
        "ast",
        "steal",
        "steals",
        "stl",
        "block",
        "blocks",
        "blk",
        "three",
        "threes",
        "double",
        "triple",
        "players",
        "has",
        "have",
        "having",
        "his",
        "her",
        "their",
        "its",
        "these",
        "those",
        "some",
        "many",
        "how",
        "much",
        "several",
        "few",
        "most",
        "all",
        "career",
        "season",
        "postseason",
        "playoff",
        "playoffs",
        "regular",
        "such",
        "no",
        "every",
        "each",
        # Function words that can sit directly before a name and are not part
        # of it. These matter for the LEADING word `_SUBJECT_OF_HIGH` now
        # captures: "sixers record when maxey had 10+ rebounds" read "when
        # maxey" as the name. Rejecting them as a name in their own right is
        # right too - "with" is the word AGENTS.md records reading as Jeff
        # Withey - and it can only make the count grammars more conservative,
        # which is the safe direction for a guess about a person.
        "when",
        "while",
        "if",
        "with",
        "without",
        "vs",
        "versus",
        "against",
        "did",
        "does",
        "do",
        "was",
        "were",
        "is",
        "are",
        "for",
        "by",
        "from",
        "in",
        "on",
        "at",
        "to",
        "of",
        "after",
        "before",
        "during",
        "than",
        "then",
        "but",
        "or",
        "per",
        "game",
        "games",
        "total",
        "least",
        "best",
        "worst",
        "highest",
        "lowest",
    }
)
_COUNT_STAT_WORD = r"(?:point|pt|rebound|reb|assist|ast|steal|stl|block|blk|three)"
_SUBJECT_OF_COUNT = re.compile(
    r"\b(?:([A-Za-z][A-Za-z.'\-]*)\s+)?([A-Za-z][A-Za-z.'\-]{2,})\s+(?:"
    r"games?\s+with\b"
    r"|games?\s+of\b"
    r"|\d+\+?\s*[- ]?\s*" + _COUNT_STAT_WORD + r"s?\s+games?\b"
    r")",
    re.IGNORECASE,
)


# "how many 40+ point games does lebron james have": the subject sits between
# an auxiliary and "have", nowhere near the count. The model dropped LeBron
# from exactly this question (#148's shape, in a third grammar).
_SUBJECT_OF_HAVE = re.compile(r"\b(?:does|did|has|have)\s+(?:([A-Za-z][A-Za-z.'\-]*)\s+)?([A-Za-z][A-Za-z.'\-]{2,})\s+(?:have|had|got|gotten|recorded|posted)\b", re.IGNORECASE)


def _subject_named_in(question: str) -> str | None:
    """The word (or two) a single-game-high or threshold-count question makes
    its subject, or None.

    Returns the question's own words, not a resolved player: resolution
    decides whether they name somebody, and asks when it is ambiguous.
    "curry" then answers "did you mean Seth Curry or Stephen Curry?", which is
    the question asked - where the league's high is not.
    """
    for match in _SUBJECT_OF_HIGH.finditer(question):
        lead, word = match.group(1), match.group(2)
        # The richer list here too, not just for the lead. On the narrow one,
        # "Total points scored by the toronto raptors" returned the subject
        # "points" and "least points scored by the wizards" the same - a stat's
        # own noun read as a person, which is the trap `_COUNT_SUBJECT_WORDS`
        # was written for. Measured over the 261-question corpus, this loses no
        # real name and drops three pieces of junk.
        if word.casefold() in _COUNT_SUBJECT_WORDS:
            continue
        # A first name where the question gave one: "kobe bryant's" reaches
        # Kobe, where a bare "bryant" is four other players and none of them
        # him. Gated on the richer stopword list, so "most points curry
        # scored" still reads "curry" and never "points curry".
        if lead is not None and lead.casefold() not in _COUNT_SUBJECT_WORDS:
            return f"{lead} {word}"
        return word
    for pattern in (_SUBJECT_OF_COUNT, _SUBJECT_OF_HAVE):
        for match in pattern.finditer(question):
            lead, word = match.group(1), match.group(2)
            if word.casefold() in _COUNT_SUBJECT_WORDS:
                continue
            if lead is not None and lead.casefold() not in _COUNT_SUBJECT_WORDS:
                return f"{lead} {word}"
            return word
    return None


_SPAN_WORDS = re.compile(r"\b(?:career|all[- ]time|ever|(?:in|of)\s+(?:nba\s+)?history|of\s+all\s+time)\b", re.IGNORECASE)
# "this postseason" names the current season as surely as "this season" does:
# without it, "maxey's stats for game 4 against the knicks this postseason"
# read as a career question and asked which Maxey.
_SEASON_WORDS = re.compile(r"\b(?:this|last|next)\s+(?:season|year|postseason|playoffs)\b", re.IGNORECASE)

# "all playoff games" / "every playoff game" / "all his playoff games" (#141):
# none of _SPAN_WORDS' words appear in them, so "show a shot chart for steph
# curry in all playoff games" carried no span at all and the season defaulted
# to the latest with data - one postseason drawn and presented as all of them,
# with nothing in the answer saying so. Anchored on a season-TYPE word
# ("playoff", "postseason", "preseason", "regular season") immediately after
# "all"/"every" (and an optional possessive) so it cannot fire on "all star" -
# "star" is not one of them - or on an unrelated "all ... games" ("all the
# games Curry played in March").
_SPAN_ALL_GAMES_WORDS = re.compile(r"\b(?:all|every)\b(?:\s+(?:his|her|their))?\s+(?:playoff|post-?season|pre-?season|regular[- ]season)\s+games?\b", re.IGNORECASE)


def _validate_span(question: str) -> str | None:
    """ "career" when the question asks about more than one season's worth of
    games at once. Measured before this existed: "career points leaders" and
    "Jokic career averages" were both answered with one season, fluently.

    .. versionchanged:: 4.4.0
       Reads "all playoff games" and its variants too - see
       :data:`_SPAN_ALL_GAMES_WORDS` (#141).
    """
    text = question
    if _CAREER_HIGH.search(text) and (season_from_text(question) is not None or _SEASON_WORDS.search(text)):
        text = _CAREER_HIGH.sub(" ", text)
    return "career" if _SPAN_WORDS.search(text) or _SPAN_ALL_GAMES_WORDS.search(text) else None


# The words that end a teammate's name in "without X this season" and the like.
# The question words are here for the same reason the prepositions are: each
# can follow a name, and none of them is one - without them "without Tatum and
# how many wins" reads "how many wins" as a second teammate and refuses a
# question that used to answer.
_NAME_STOPWORDS = frozenset(
    "this last in on since during for vs vs. versus against at when while game games season seasons record stats stat playing played plays from over the a an any his her their "
    "how what who whose why many much did does do is are was were has have had than to of by not no".split()
)

# What separates one name from the next INSIDE the phrase, rather than ending
# it. "or" joins exactly as "and" does - "without Tatum or Brown" is still the
# games neither of them played - and a comma is how a list of three is written.
_NAME_JOINERS = frozenset({"and", "or", "nor", "&", "+", ","})

# A run of name-shaped words, joined by whitespace, commas or ampersands. The
# first word must start with a letter, so "without 20 points" still names
# nobody; the repetition is bounded because an unbounded one would read half a
# sentence as a name.
_NAME_PHRASE = r"[A-Za-z][A-Za-z.'\-]*(?:[\s,&+]+[A-Za-z][A-Za-z.'\-]*){0,8}"
_WITHOUT = re.compile(rf"\bwithout\s+({_NAME_PHRASE})", re.IGNORECASE)
_WITH = re.compile(rf"\bwith\s+({_NAME_PHRASE})", re.IGNORECASE)

# "when both Embiid and Paul George played", "when Embiid and Paul George
# play". The same question as "record WITH X", written the other way, and
# `record_when` is where the model files all of them: four phrasings of it in
# one 2026-09-20 web session were refused because record_when needs a stat and
# a threshold and this names neither (#156). Anchored on a playing verb so
# "record when Embiid SCORES 30 points" - a real record_when question - cannot
# match it.
_WHEN_PLAYED = re.compile(rf"\bwhen\s+(?:both\s+)?({_NAME_PHRASE}?)\s+(?:are\s+playing|is\s+playing|were\s+playing|play|plays|played|suit\s+up|suited\s+up)\b", re.IGNORECASE)

# "record when Embiid with Paul George" names one player on each side of the
# "with", and reading only the side after it answered about Paul George alone
# - the silent narrowing this module exists to stop. Rewritten to the "with A
# and B" form the reader below already handles, rather than parsed twice.
_WHEN_WITH = re.compile(rf"\bwhen\s+(?:both\s+)?({_NAME_PHRASE}?)\s+with\s+({_NAME_PHRASE})", re.IGNORECASE)


def _played_together(question: str) -> list[str]:
    """Every player a record question says played TOGETHER, in order - "with A
    and B", "when both A and B played", "when A with B". Empty when it names
    none, which leaves the question where the model put it."""
    rewritten = _WHEN_WITH.sub(lambda m: f"with {m.group(1)} and {m.group(2)}", question)
    return _names_after(_WITH, rewritten) or _names_after(_WHEN_PLAYED, question)


_NAME_TOKENS = re.compile(r"[A-Za-z][A-Za-z.'\-]*|[,&+]")

# As many words as the old single-name pattern allowed, now per name rather
# than per phrase.
_MAX_NAME_WORDS = 3


def _names_after(pattern: re.Pattern[str], question: str) -> list[str]:
    """Every name the phrase after ``pattern``'s keyword holds, in order.

    Empty when no name follows at all - "without a turnover" names nobody, and
    must not become a teammate called "a".

    This reads ALL of them, and that is the whole point. Reading only the first
    answered "Celtics record without Tatum and Brown" with the games Tatum
    missed: a different question, answered fluently, with nothing in the answer
    saying the second player had been dropped. The templates that honor
    ``without`` require every name (see ``templates.with_without``), so the
    parser must hand them every name or the requirement has nothing to work
    with.

    A name ends at a word that cannot be part of one (:data:`_NAME_STOPWORDS`),
    which ends the whole phrase; a joiner (:data:`_NAME_JOINERS`) ends the name
    and starts the next. A joiner with nothing before it names nobody, so
    "with and without Tatum" reads no "with" name rather than an empty one.
    """
    match = pattern.search(question)
    if match is None:
        return []
    names: list[str] = []
    words: list[str] = []

    def close() -> bool:
        """End the name being read; False when there was none, which ends the phrase."""
        if not words:
            return False
        names.append(" ".join(words))
        words.clear()
        return True

    for token in _NAME_TOKENS.findall(match.group(1)):
        lowered = token.casefold()
        if lowered in _NAME_JOINERS:
            if not close():
                break
            continue
        if lowered in _NAME_STOPWORDS or len(words) >= _MAX_NAME_WORDS:
            break
        words.append(token)
    close()
    return names


# Which split a player_splits question asks for. Exactly one or nothing.
SPLIT_WORDS: dict[str, re.Pattern[str]] = {
    "home_away": re.compile(r"\bhome\b.{0,15}\b(?:away|road)\b|\b(?:away|road)\b.{0,15}\bhome\b", re.IGNORECASE),
    "starter_bench": re.compile(r"\b(?:starter|starting|starts|bench|reserve)\b", re.IGNORECASE),
    "wins_losses": re.compile(r"\bin\s+(?:wins|losses)\b|\bwins\s+(?:vs\.?|versus|and|or)\s+losses\b", re.IGNORECASE),
    "month": re.compile(r"\b(?:by|each|per)\s+month\b|\bmonthly\b", re.IGNORECASE),
}

# One half of the starter/bench split, where the question names a half.
#
# `SPLIT_WORDS["starter_bench"]` matches "starter" or "bench" and records only
# the CATEGORY, which is right for `player_splits` - a splits question wants
# both groups side by side - and useless to a template that has to FILTER.
# "Jrue holiday last 50 games as a starter" is a log of his starts, not a log
# of everything with a starter/bench breakdown, and `TemplateContext` carries
# no question text, so the direction can only be recovered here.
#
# Read from the question for the same reason `_validate_side` reads the side of
# the ball: it costs nothing and cannot move a slot on any other question,
# where teaching `ROUTER_SCHEMA` a new value reproducibly can. A question that
# names BOTH halves ("starting vs coming off the bench") keeps the category, on
# purpose - that IS the splits question.
_STARTER_WORDS = re.compile(r"\b(?:starter|starting|starts|started)\b", re.IGNORECASE)
_BENCH_WORDS = re.compile(r"\b(?:bench|reserve|reserves)\b", re.IGNORECASE)


def _split_side(split: str, question: str) -> str:
    """``split``, narrowed to the half the question names where it names one.

    Returns ``"starter"`` or ``"bench"`` for a question about one half, and the
    original category otherwise - both halves named, or a split that has no
    halves (``month``, ``home_away``, ``wins_losses``).
    """
    if split != "starter_bench":
        return split
    starter, bench = bool(_STARTER_WORDS.search(question)), bool(_BENCH_WORDS.search(question))
    if starter and not bench:
        return "starter"
    if bench and not starter:
        return "bench"
    return split


# Which end of a team ranking was asked for. The four are not two pairs: for a
# stat where lower is better, "fewest turnovers" and "worst in turnovers" sit
# at opposite ends, so the template - which knows the stat - resolves them.
RANK_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("worst", re.compile(r"\bworst\b", re.IGNORECASE)),
    ("best", re.compile(r"\bbest\b", re.IGNORECASE)),
    # "slowest pace" is the fewest possessions, "fastest" the most - without
    # these, "slowest pace" listed the fastest teams first.
    ("fewest", re.compile(r"\b(?:fewest|least|lowest|slowest)\b", re.IGNORECASE)),
    ("most", re.compile(r"\b(?:most|highest|top|leads?|leaders?|fastest)\b", re.IGNORECASE)),
)

# A comparison BELOW a number. No slot says "under", so without this "games
# with under 14 FTA" reached threshold_count as 14 and was answered as 14 or
# MORE - the inverse question. The words after the number are kept: they name
# the stat, and the model's own `stat` beside them is the nearest one it knows
# ("fta" arrived as freeThrowsMade), so the phrase is the only honest carrier.
# `templates.common.measure_filters` reads it and refuses a word it cannot map.
# The words kept after the number stop at a connective or the next comparison,
# so "under 14 fta in his whole career" carries "under 14 fta" and "less than
# 15 fga and with less than 35 minutes" is two phrases, not one.
_BELOW = re.compile(
    r"\b(?:under|fewer\s+than|less\s+than|below|at\s+most|no\s+more\s+than)\s+\d+%?(?:\s+(?!(?:and|or|with|in|for|vs|against|on|at|under|fewer|less|below|no|over|more)\b)[a-z][a-z-]*){0,3}"
    r"|\b\d+\+?\s*(?:minutes|mins?)\s+or\s+less\b",
    re.IGNORECASE,
)

# A comparison AT OR ABOVE a number, on minutes: "with 25 minutes", "20+
# mins", "30 minutes or more". Read out of `_SITUATION` (where "paul reed
# gamelog with 25 minutes" refused) into a slot the relation can filter on.
# Deliberately only minutes: "30+ points" is the model's own `threshold`, and
# the templates that read one (threshold_count, record_when) already carry it.
# Not the "35 minutes" inside "less than 35 minutes", which is `_BELOW`'s.
_ABOVE = re.compile(r"\b(?:with\s+(?:at\s+least\s+)?)?(?<!than\s)(?<!under\s)(?<!below\s)\d+\+?\s*(?:minutes|mins?)\b(?!\s+or\s+less)(?:\s+(?:or\s+more|played))?", re.IGNORECASE)

# Situations a game can be in that no template filters on: the second night of a
# back-to-back, overtime, a calendar month, a conference or division, the
# All-Star break. team_record answered each with the whole season's record.
#
# The second group below was added 2026-09-15 from the 261-query StatMuse feed
# replay, where a narrowing ROUTER_SCHEMA has no slot for was the single largest
# cause of a wrong answer - 14 of 261, more than any other. The words never
# reached `check_scope`, because it can only refuse a slot the router emits, so
# the template answered the un-narrowed question: "lebron james 2 3 pointers
# all-time vs jazz on tuesdays" returned his career average against Utah over 48
# games, with the Tuesday, the threes and the "2" all silently gone.
#
# Read from the question text rather than added to ROUTER_SCHEMA, which is the
# cheap half of this fix and the safe one: a new slot in the schema moves slots
# on unrelated questions (see _validate_side), while a regex here costs no
# prompt tokens and cannot. Setting `situation` is enough on its own - no
# template lists it in HONORED_SCOPING, so `check_scope` refuses and the
# question falls through to the agent, which is the ranking AGENTS.md sets: a
# refusal beats a fluent wrong answer.
#
# Measured against 343 real questions (the 261-query feed plus the 83 routing
# corpus cases): 14 feed queries match and **no corpus case does**, so no
# question that routes correctly today starts refusing.
_SITUATION = re.compile(
    r"\bback[- ]to[- ]backs?\b|\bb2bs?\b|\bsecond\s+night\b|\bovertime\b|"
    r"\bin\s+(?:october|november|december|january|february|march|april|may|june)\b|"
    r"\b(?:east(?:ern)?|west(?:ern)?)\s+conference\b|\bvs\.?\s+the\s+(?:east|west)\b|\bdivision\b|\ball[- ]star\s+break\b|"
    # A day of the week: 8 of the 14, and the most common shape in the feed.
    r"\b(?:mon|tues|wednes|thurs|fri|satur|sun)days?\b|"
    # A calendar holiday. "on christmas" answered with a whole season average.
    r"\b(?:christmas|xmas|thanksgiving|halloween|easter|mlk\s+day|martin\s+luther\s+king|new\s+year'?s)\b|"
    # An age. "most triple doubles before turning 27" answered with this
    # season's triple-double leaders - `players` holds no birth date at all
    # (DATA.md), so this one cannot be answered even in principle.
    r"\b(?:before|after|by)\s+(?:turning|age)\s+\d+\b|\bat\s+age\s+\d+\b|\b\d+\s+years?\s+old\b|"
    # A minutes condition used to be here ("paul reed gamelog with 25 minutes"
    # returned his most recent game); it is `_ABOVE` / `_BELOW` now, slots the
    # relation filters on.
    # A window defined by an event rather than a date.
    r"\bsince\s+(?:returning|coming\s+back|his\s+return|the\s+all[- ]star\s+break)\b|\bsince\s+(?:his\s+)?injury\b|\bafter\s+returning\b|"
    # A calendar day is NOT here: `_validate_date` resolves it to a real date
    # and `game_log` then answers the game that was asked about. What is left
    # here is the date this project cannot turn into one day - a window opened
    # by "since March 1", and a date in a career question, which spans twenty
    # Octobers and so fixes no year. Both refuse.
    r"\b(?:since|after|before|from|through|until)\s+(?:the\s+)?(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+\d{1,2}"
    r"(?:st|nd|rd|th)?\b",  # codespell:ignore nd - an ordinal suffix
    # A season named by ordinal ("his 18th season") used to be here; it is
    # `_SEASON_N` now, settled to a year once the player is known.
    re.IGNORECASE,
)

# Words that name a TEAM stat, beyond the box-score words _STAT_WORDS knows.
_TEAM_STAT_WORDS = re.compile(r"\b(?:pace|ratings?|offen\w*|defen\w*|net|possessions?|record|wins?|losses)\b", re.IGNORECASE)  # codespell:ignore offen - a regex stem

# "vs"/"against", for a game log's last N meetings - see route().
_VERSUS_WORDS = re.compile(r"\b(?:vs\.?|versus|against)\s", re.IGNORECASE)

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

# Every condition a question states as "N+ <stat>", in order. ROUTER_SCHEMA
# carries ONE `threshold`, so a second condition survived only as a `fields`
# entry the template ignores: "who had the most 30+ point 10+ rebound games
# this year?" answered "Luka Doncic ... 30+ points, with 44" where the pair is
# Jokic with 20, and "How many 20+ point 5+ assist games did luka have?"
# answered 441 against 397 (#139). Read as lines on box-score columns, which
# the relation already filters on, rather than as a second threshold slot.
#
# The "+" (or "plus" / "or more") is required, unlike `_THRESHOLD`: without it
# "top 10 rebound leaders" reads as a condition and a leaderboard question
# that answers today would start refusing.
# Which SPELLINGS this grammar accepts beside a threshold, with the regex's own
# alternation built from them (longest first, so "rebounds" is not matched as
# "reb" with a stray "ounds" left over). What each one MEANS is not decided
# here: it is read from `MEASURE_WORDS`, the one definition of what a question
# calls a box-score column, which `templates/common.py` reads too.
#
# `router.py` imports nothing from `templates` on purpose - the stage before
# the templates must not be made to depend on them - which is why this used to
# be a second hand-kept copy that nothing checked for agreement (ISSUES.md
# #164). `association.query.measures` is a leaf module with no imports of its
# own, so reading it costs the router nothing and cannot cycle.
#
# A spelling dropped from MEASURE_WORDS raises KeyError at import rather than
# silently narrowing what this grammar understands.
_THRESHOLD_SPELLINGS = (
    "points", "point", "pts",
    "rebounds", "rebound", "rebs", "reb", "boards",
    "assists", "assist", "asts", "ast",
    "steals", "steal", "stl",
    "blocks", "block", "blk",
    "turnovers", "turnover",
    "threes", "3s",
)  # fmt: skip
_THRESHOLD_WORDS: dict[str, str] = {word: MEASURE_WORDS[word] for word in _THRESHOLD_SPELLINGS}
_THRESHOLD_PAIR = re.compile(
    r"\b(\d{1,3})\s*(?:\+|plus|or\s+more)\s*(" + "|".join(sorted((re.escape(w) for w in _THRESHOLD_WORDS), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

# Rate stats the prompt never lists as a player `stat`, so the model reaches for
# the nearest one it knows. Measured: "kevin durant true shooting percentage
# career" came back as stat='threePointFieldGoalPct' and was answered with his
# 3-point percentage - a different stat, fluently. Named in the question, the
# stat is read from it; a template that has no such stat then refuses.
_ADVANCED_STAT_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ts_pct", re.compile(r"\btrue[- ]shooting\b|\bts\s?%|\bts\s+pct\b", re.IGNORECASE)),
    ("efg_pct", re.compile(r"\beffective\s+(?:field\s+goal|fg)\b|\befg\b", re.IGNORECASE)),
    ("usage_pct", re.compile(r"\busage\b", re.IGNORECASE)),
)
_ADVANCED_STAT_INTENTS = frozenset({"player_stat", "player_compare", "player_history", "leaderboard", "game_log"})

# Hollinger's single-game composite (ISSUES.md #114). ROUTER_SCHEMA has no
# `stat` value for it, and `stat` is the one REQUIRED slot, so the decoder
# fills it with the nearest one it knows: "game score nba leader" arrived at
# `leaderboard` with stat='points' and was answered "Luka Doncic led the
# league in points per game ... at 33.5" - correct about points, and not what
# was asked. Same mechanism as `_validate_side`, same remedy: read it off the
# question text in route() rather than teach ROUTER_SCHEMA a new enum value,
# which would move slots on unrelated questions and cannot be measured
# without ollama.
#
# Anchored to the two-word phrase and not to "score" alone, because "score"
# alone means points everywhere else in basketball - "pacers score", "what
# was the score of the game", "total points scored by the toronto raptors"
# would all be hijacked into a Game Score leaderboard, trading one fluently
# wrong answer for several. The trailing `\b` is what keeps "per game scored
# on fridays" out: "scored" fails the boundary after "score". Verified against
# the 261-question StatMuse feed corpus: the phrase appears in exactly one
# question, and the pattern rejects all three near-miss shapes above.
_GAME_SCORE = re.compile(r"\bgame\s*scores?\b", re.IGNORECASE)

# The two templates that can look this metric up name it differently, so the
# question's own intent decides which spelling to emit - a value correct for
# one is unknown to the other. `leaderboard` reads it through
# `metrics.LEADERBOARD_METRICS`, keyed "avg_game_score" like every other
# per-game average there (avg_points, avg_rebounds); `player_stat` reads it
# through `templates.players.ADVANCED_STATS`, keyed "game_score" with no
# prefix, alongside ts_pct/efg_pct/usage_pct. Left out of every other intent
# in _ADVANCED_STAT_INTENTS on purpose: player_compare, player_history and
# game_log read a player's stat line through PLAYER_STAT_COLUMNS /
# COMPARE_STAT_LINE, never ADVANCED_STATS, so a game_score value there would
# be silently unreadable rather than answered - the same "looks handled, does
# nothing" trap a stray slot leaves everywhere else in this module.
_GAME_SCORE_STAT_BY_INTENT: dict[str, str] = {"leaderboard": "avg_game_score", "player_stat": "game_score"}


def _route_game_score(intent: str, slots: dict[str, Any], question: str) -> None:
    """Hollinger's single-game composite, read from the question text - see
    the comment above :data:`_GAME_SCORE` for why and :data:`_GAME_SCORE_STAT_BY_INTENT`
    for why the value it sets depends on the intent.

    Runs after the rest of ``stat`` resolution so it overrides whatever the
    model or ``_ADVANCED_STAT_WORDS`` guessed, not just fills a gap: "game
    score" contains "score", which ``_named_a_stat`` already treats as naming
    a stat, so the model's wrong guess (typically ``points``) would otherwise
    survive untouched.

    .. versionadded:: 4.3.0
    """
    if intent not in _GAME_SCORE_STAT_BY_INTENT or not _GAME_SCORE.search(question):
        return
    slots["stat"] = _GAME_SCORE_STAT_BY_INTENT[intent]


# Two-point field-goal percentage (ISSUES.md #114). ROUTER_SCHEMA leaves
# `stat` an open string with no enum - see the comment on that field - so the
# model CAN emit "twoPointFieldGoalPct" verbatim, and measured over the
# 2026-09-20 web session it did 3 times out of 10. The other 7 it substituted
# the nearest stat ROUTER_PROMPT actually teaches ("threePointFieldGoalPct,
# fieldGoalPct, freeThrowPct"), which is fieldGoalPct here, and answered
# overall shooting where 2-point shooting was asked - the same substitution
# _ADVANCED_STAT_WORDS exists to stop for ts_pct/efg_pct/usage_pct. Read from
# the question rather than taught to the prompt, for the same reason every
# other entry in this file gives: a prompt edit moves slots on unrelated
# questions and needs ollama to measure; a regex costs nothing and cannot.
#
# "2pt", "2-pt", "2 point", "two point" and "2p", each read against
# percentage/pct/% (optionally with "field goal(s)" in between, the way the
# router's own worked examples phrase the other two percentages) - and
# nothing shorter, so "3 point percentage" and a plain "field goal
# percentage" are never swept in: neither alternative can start matching
# without a literal "2" or "two" immediately before the pt/point token, and
# "20 point" (a threshold, not a shooting split) fails the same way - the "0"
# sits where "pt"/"point" must start.
_TWO_POINT_PCT = re.compile(
    r"\b(?:2[- ]?pts?|2p|2[- ]?points?|two[- ]?points?)\b(?:\s+field\s*goals?)?\s*(?:%|pct\.?|percent(?:age)?)\b",
    re.IGNORECASE,
)
_TWO_POINT_PCT_INTENTS = frozenset({"player_history", "player_stat"})


def _route_two_point_pct(intent: str, slots: dict[str, Any], question: str) -> None:
    """2-point field-goal percentage, read from the question text - see
    :data:`_TWO_POINT_PCT`.

    Overrides whatever the model guessed, the same discipline
    :func:`_route_game_score` uses and for the same reason: "2pt" and
    "percentage" are both words ``_named_a_stat`` already reads as naming a
    stat, so a wrong guess (typically ``fieldGoalPct``) would otherwise
    survive untouched. Scoped to the two templates that can look the stat up
    (``player_history``'s ``HISTORY_COLUMNS`` and ``player_stat``'s
    ``SHOOTING_STATS``, both in ``templates/players.py``) - the same
    discipline ``_GAME_SCORE_STAT_BY_INTENT`` follows for game score, so a
    value lands only where something reads it.

    .. versionadded:: 4.4.0
    """
    if intent not in _TWO_POINT_PCT_INTENTS or not _TWO_POINT_PCT.search(question):
        return
    slots["stat"] = "twoPointFieldGoalPct"


# A leaderboard ranking of shot distance (ISSUES.md #114). No such metric
# exists and none is planned - a shot's distance has no leaderboard-shaped
# rate the way a percentage or a per-game average does. Measured: "who lead
# the league in avg 3 point distance" arrived at `leaderboard` with the
# nearest real metric the model knew (threePointFieldGoalPct) and answered
# Luke Kennard's 3-point PERCENTAGE, 47.8% - a real, fluently wrong number.
# The sibling phrasing with no metric word in it ("...in shot distance for 3
# point shots") arrived with a filler `player: "player"` instead, which
# override_invented_players (agent.py) then refused for naming a player the
# question does not mention - honest-sounding, and also the wrong cause,
# since no leaderboard could answer either question anyway.
_LEADERBOARD_SHOT_DISTANCE = re.compile(
    r"\bshot\s+distance\b|\b(?:3|three)[- ]?points?\s+distance\b|\bdistance\s+for\s+(?:3|three)[- ]?points?\b",
    re.IGNORECASE,
)


def _route_leaderboard_shot_distance(intent: str, slots: dict[str, Any], question: str) -> None:
    """No leaderboard ranks shot distance - see :data:`_LEADERBOARD_SHOT_DISTANCE`.

    Sets `stat` to the sentinel ``"shot_distance"`` - not a real metric name,
    an explicit string `templates.players.leaderboard` checks for by value
    (the two modules agree on the literal rather than sharing a symbol, the
    same way `_GAME_SCORE_STAT_BY_INTENT`'s spellings are agreed rather than
    imported) - so the template can refuse naming the real cause instead of
    resolving to the nearest real metric.

    Also drops any `player` the router filled, filler or real: `leaderboard`
    never reads one for real (a named player is refused separately), and a
    filler value here ("player": "player" on a question that names nobody)
    would otherwise reach `override_invented_players` first and refuse for
    the WRONG cause - "read as a question about player, who the question does
    not mention" - before this refusal, the right one, ever runs.

    .. versionadded:: 4.4.0
    """
    if intent != "leaderboard" or not _LEADERBOARD_SHOT_DISTANCE.search(question):
        return
    slots["stat"] = "shot_distance"
    slots.pop("player", None)


# A game log asked for by name. Measured: "luka ft log" routed to player_stat
# and was answered with a season average.
_LOG_WORDS = re.compile(r"\b(?:game\s*logs?|gamelogs?|logs?)\b|\b(?:each|every|by)\s+game\b", re.IGNORECASE)
_GAMES_WORDS = re.compile(r"\bgames?\b|\blast\b", re.IGNORECASE)

# The thirty team nicknames, and the shorthand a question uses for some. Only to
# tell a team from a player in a slot the model filled: "zach lavine vs nuggets"
# came back as player_matchup with players ['Zach LaVine', 'Denver Nuggets'].
_TEAM_WORD = re.compile(
    r"\b(?:hawks|celtics|nets|hornets|bulls|cavaliers|cavs|mavericks|mavs|nuggets|pistons|warriors|rockets|pacers|clippers|lakers|"
    r"grizzlies|heat|bucks|timberwolves|wolves|pelicans|knicks|thunder|magic|76ers|sixers|suns|blazers|kings|spurs|raptors|jazz|wizards)\b",
    re.IGNORECASE,
)


# A team named by its city or its abbreviation, which is how a question names
# one when it does not use the nickname: "mathurin v det", "sam hauser v mil",
# "pascal vs orlando". These are matched against the WHOLE name and never as a
# last word, and the distinction is load-bearing rather than fussy: three real
# players are surnamed Cleveland, Houston and Washington, so a last-word rule
# over cities turns PJ Washington and Allan Houston into teams. Measured
# against the warehouse, no player name equals a city or an abbreviation, and
# exactly one player name is a single word at all ("Nene"), which matches none
# of these.
#
# Two-letter forms are left out on purpose. ESPN's own table abbreviates four
# teams "no", "ny", "sa" and "gs", and "no" is an English word; questions use
# the three-letter forms, so those are what is listed.
_TEAM_CITY = frozenset(
    {
        "atlanta", "boston", "brooklyn", "charlotte", "chicago", "cleveland", "dallas", "denver", "detroit",
        "golden state", "houston", "indiana", "los angeles", "memphis", "miami", "milwaukee", "minnesota",
        "new orleans", "new york", "oklahoma city", "orlando", "philadelphia", "phoenix", "portland",
        "sacramento", "san antonio", "toronto", "utah", "washington",
    }
)  # fmt: skip
_TEAM_ABBREVIATION = frozenset(
    {
        "atl", "bkn", "bos", "cha", "chi", "cle", "dal", "den", "det", "gsw", "hou", "ind", "lac", "lal",
        "mem", "mia", "mil", "min", "nop", "nyk", "okc", "orl", "phi", "phx", "por", "sac", "sas", "tor",
        "uta", "wsh", "was",
    }
)  # fmt: skip

# A near spelling is NOT matched here, and that was measured rather than
# assumed. The feed misspells three teams inside a `players` slot - "taptors",
# "warriners", "blakers" - and `difflib` at cutoff 0.8 reaches the right team
# for all three. It also reaches a team for **16 real player surnames**:
# Burks -> Bucks, Hawkins -> Hawks, Thornton -> Toronto, Gooden -> Golden,
# Wheat -> Heat, Houstan -> Houston, and ten more. The model puts bare
# surnames in that slot routinely (["Mathurin", "Detroit"], ["Pascal",
# "Orlando"]), so those collisions are live, and turning a player into a team
# is the same fluent wrong answer in the other direction. Three queries is not
# worth sixteen, and the cutoff cannot separate them - "houstan"/"houston" and
# "taptors"/"raptors" are both one edit in seven characters, ratio 0.857. Same
# conclusion `find_players` reached for player names, for the same reason.


def _is_team_name(name: str) -> bool:
    """Whether a name the model put in ``players`` is a team's.

    Three tests, narrowing as they get looser:

    - **Its LAST word is a nickname.** Checked against the warehouse: all 30
      team names end in one and none of 3,101 player names does, while "Magic
      Johnson" holds one as his first name - and a match anywhere in the name
      took him for a team.
    - **The WHOLE name is a city or an abbreviation** ("det", "Orlando"). Never
      the last word, because three players are surnamed Cleveland, Houston and
      Washington.
    A misspelled team is deliberately NOT matched - see the note above
    ``_TEAM_CITY``, where fuzzy matching was measured and rejected because it
    turns 16 real player surnames into teams.

    .. versionchanged:: 2.2.0
       Recognizes a city and an abbreviation. Before this, a team the question
       named any way but by nickname read as a player, and "mathurin v det"
       was routed as a matchup between two players.
    """
    words = name.lower().split()
    if not words:
        return False
    if _TEAM_WORD.fullmatch(words[-1]) is not None:
        return True
    whole = " ".join(words)
    return whole in _TEAM_CITY or whole in _TEAM_ABBREVIATION


# "best record" and "worst record" rank the league; with no team named they are
# team_leaderboard's question. Measured: "worst record 2025-26" came back as
# team_record with team='worst'.
_BEST_WORST_RECORD = re.compile(r"\b(?:best|worst)\s+records?\b", re.IGNORECASE)

# A `team` slot that names the league rather than a team - "all-NBA",
# "all_teams", "worst" - measured on three questions, each of which then
# refused as an unknown team.
_PSEUDO_TEAM = re.compile(r"(?:the\s+)?(?:all[-_ ]?nba|nba|league|all[-_ ]?teams?|teams?|every\s+team|worst|best)", re.IGNORECASE)


# A NetPoints rate asked for by any of its names. In this data the only
# adjusted form of NetPoints is the per-100-possessions rate, so "adjusted"
# has exactly one honest reading; the model reaches for the season total
# whatever the question says ("top 10 in defensive netpoints / 100
# possessions" came back as netpoints_defense, and the two lists disagree
# from the second name down - Holmgren is 2nd by season total and outside
# the top three per 100). "/ 90" is a rate nothing here holds, and refuses
# (#152).
_RATE_WORDS = re.compile(r"\badjusted\b|\bper\s+(?:100\s+)?poss?ess?ions?\b|\bper\s+100\b|/\s*100\b", re.IGNORECASE)
_PER_90 = re.compile(r"\bper\s+90\b|/\s*90\b", re.IGNORECASE)
_NETPOINTS_PER_100: dict[str, str] = {
    "netpoints": "netpoints_per_100",
    "netpoints_total": "netpoints_per_100",
    "netpoints_offense": "netpoints_offense_per_100",
    "netpoints_defense": "netpoints_defense_per_100",
}


def _route_rate(intent: str, slots: dict[str, Any], question: str) -> None:
    """A per-possession rate the question asks a ranking for - see _RATE_WORDS.
    Switches a NetPoints metric to its per-100 variant; any other metric, or a
    per-90 rate, gets a ``rate`` slot no template honors, so check_scope
    refuses rather than ranking the wrong unit."""
    if intent != "leaderboard":
        return
    per_90 = _PER_90.search(question)
    if per_90 is not None:
        slots["rate"] = per_90.group(0).casefold()
        return
    rate = _RATE_WORDS.search(question)
    if rate is None:
        return
    stat = slots.get("stat")
    if isinstance(stat, str) and stat in _NETPOINTS_PER_100:
        slots["stat"] = _NETPOINTS_PER_100[stat]
    elif not (isinstance(stat, str) and stat.endswith("_per_100")):
        slots["rate"] = rate.group(0).casefold()


def _team_metric_in(question: str) -> str | None:
    """The longest team-metric alias the question names ("defensive rating"),
    or None. The model invents team stats ("usage_pct_defense" for "lowest
    defensive rating"), and the question says which one it meant."""
    text = question.casefold()
    for alias in sorted(STAT_ALIASES, key=len, reverse=True):
        if re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", text):
            return alias
    return None


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

#: Intents whose template REFUSES a limit outright, so a filler one costs the
#: answer entirely rather than just widening a list. Deliberately not "every
#: intent that does not honor `order`": a `limit` of 1 is legitimate on a
#: leaderboard ("who leads"), and dropping it there turned a one-row answer
#: into ten for no reason anybody asked for.
_LIMIT_REFUSING_INTENTS: frozenset[str] = frozenset({"player_stat"})


# A number of games named in the question, which makes a `limit` real rather
# than filler: "last 5 games", "his one game", "top 10". Read with the lines on
# a box-score stat taken out first (_names_a_count): the 25 in "gamelog with
# 25 minutes" counts minutes, not games.
# A year is not a count: "Portis vs bulls 2019-20 to 2023-24" names no number
# of games, so a four-digit number and either half of a "2019-20" are left out.
_COUNT_WORDS = re.compile(r"\b(?:(?<![\d-])\d{1,3}(?![\d-])|one|two|three|four|five|ten|last|first|top|only)\b", re.IGNORECASE)


def _names_a_count(question: str) -> bool:
    """Whether the question names a number of games, once the numbers that
    belong to a line on a box-score stat ("under 14 fta", "with 25 minutes"),
    to a game of a series ("game 4"), or to a count of SEASONS rather than
    games ("past two seasons" - see _PAST_N_SEASONS) are set aside."""
    stripped = _GAME_N.sub(" ", _ABOVE.sub(" ", _BELOW.sub(" ", _PAST_N_SEASONS.sub(" ", question))))
    return _COUNT_WORDS.search(stripped) is not None


#: Intents that honor ``order`` only beside a real ``limit`` - a single game at
#: one end of the span - because filling ``order`` alone would hand "his last
#: game" to a log of his last ten. Read by :func:`_route_side_and_order` with
#: :data:`_SINGLE_GAME`; the pair is what makes "last game" one game.
_ORDER_ON_A_SINGLE_GAME: frozenset[str] = frozenset({"player_stat"})
# A possessive names the subject as often as a pronoun does - "steph curry's
# last regular season game" - and without it that question kept a season the
# model misread (#153).
_SINGLE_GAME = re.compile(r"\b(?:his|her|their|the|\w+'s)\s+(last|first|latest|previous|most\s+recent|final|opening|earliest)\s+(?:\w+\s+){0,2}?game\b(?!s)", re.IGNORECASE)

#: Intents where ``order`` narrows to ONE game rather than ordering a list:
#: shot_chart, shot_distance, player_netpoints and fingerprint each resolve it
#: to a single event id, where game_log only sorts. So a filler ``order``
#: costs a whole season here - "a shot chart of steph curry's 2025 season for
#: 3 point shots" drew one game, 7 of 12, where 2025 held hundreds (#153) -
#: and the question has to name a game at one end of the span for it to stand.
_ORDER_IS_ONE_GAME: frozenset[str] = frozenset({"shot_chart", "shot_distance", "player_netpoints", "fingerprint"})


def _names_one_game(question: str) -> bool:
    """Whether the question itself asks for a game at one end of the span -
    "his last game", "first 5 games" - rather than leaving ``order`` to the
    model's own reading."""
    return _SINGLE_GAME.search(question) is not None or any(pattern.search(question) for pattern in ORDER_WORDS.values())


ORDER_INTENTS: frozenset[str] = frozenset({"fingerprint", "game_log", "period_split", "player_netpoints", "shot_chart", "shot_distance"})
"""Intents whose template honors ``order``, so filling it from the question can
only make the answer match what was asked.

The same list as the ``order`` entries in
:data:`association.query.templates.HONORED_SCOPING`, kept separately because a
router that imported the templates would invert the dependency, and guarded by
``test_the_order_intents_are_the_ones_that_honor_order``. Adding ``order``
anywhere else would be worse than leaving it off: ``check_scope`` refuses a
scoping slot the template cannot honor, so a question that answers today would
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

    That mattered because ``fingerprint`` honors ``order`` by refusing: with
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
    r"percentage|efficien\w*|usage|double-doubles?|triple-doubles?|ppg|rpg|apg|spg|bpg|fg|ft|3p|ts|efg)\b",
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


def _route_ask_model(model: str, question: str, previous_question: str | None) -> dict[str, Any] | None:
    """The model's raw slots for one question, or None if it is unreachable or
    replies with something that is not an object carrying an intent."""
    user = f"Q: {question}"
    if previous_question:
        # A follow-up ("what about 2025?") is not self-contained. One line of
        # prior context is enough to resolve it and costs ~15 tokens; the full
        # conversation is not replayed here, since that would defeat the
        # fixed, cache-friendly prefix. What arrives here is
        # `Agent.last_question`, and both shipped callers keep it None - the
        # CLI builds a new Agent per question, and the web server resets it
        # per request (`Agent.reset_conversation`) - so this branch runs only
        # for a caller that keeps one Agent across questions, as the `ai` REPL
        # removed in 2.0.0 did.
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
    except ConnectionError as exc:
        # Not "no usable classification": the model was never asked. Saying so
        # sends the reader to ollama rather than to their own question - with
        # --disable-fallthrough this sentence IS the whole error, and every
        # question gets it, which reads like the question was the problem.
        raise RouterUnavailable(f"ollama is not answering, so the router model {model!r} could not be asked") from exc
    except ollama.ResponseError as exc:
        # A model that is not pulled, a server out of memory, a load already
        # in flight. Each is about the server, and none is about the question.
        raise RouterUnavailable(f"ollama could not serve the router model {model!r}: {exc}") from exc
    except json.JSONDecodeError:
        # This one IS the model replying with something unusable.
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("intent"), str):
        return None
    return raw


def _route_coach_intent(raw: dict[str, Any], question: str) -> bool:
    """True when the question is about a coach, which nothing here can answer.

    Runs before every other stage and stops them: a coach question is refused
    whatever the model made of it, and the slots the model returned are for a
    question that cannot be answered anyway. Reading them would only risk
    a team or player name reaching the refusal.
    """
    if not _COACH_WORDS.search(question):
        return False
    raw["intent"] = "coach"
    return True


def _route_period_intents(raw: dict[str, Any], question: str) -> None:
    """Fouling out, and a quarter or half: intents code assigns from the question's own words."""
    low = question.lower()
    if _FOULED_OUT.search(low):
        raw["intent"] = "threshold_count"
        raw["stat"] = "fouls"
        raw["threshold"] = FOUL_OUT_THRESHOLD
    ranks_players = _PERIOD_LEADERS.search(low) is not None
    if (_AGENT_ONLY.search(low) and (ranks_players or not _is_team_quarter_points(raw))) or _HALF_WORDS.search(low):
        # A named player's quarter or half now HAS a template, so the override
        # sends it there instead of to the agent - but only when the question
        # names one and the period is legible, since `period_split` answers
        # about a player and nothing else. Everything else keeps the old
        # behavior: slots are kept, because the agent sees the conversation
        # rather than the Route, and the log line shows what the model thought.
        asked = _period_asked(question)
        # No second `_is_team_quarter_points` check: it means "this intent, and
        # NO player", so it can never be true here where a player is named. The
        # team's own quarter is already exempted by the outer condition.
        named_player = isinstance(raw.get("player"), str) and raw["player"].strip()
        if asked is not None and not named_player and ranks_players:
            # Players ranked by a quarter or a half - templates.games
            # .period_leaderboard. A team may still be named ("knicks 1st
            # quarter scoring leaders"), where it narrows the ranking to that
            # team's players rather than becoming the subject.
            raw["intent"] = "period_leaderboard"
            raw |= asked
            if not _named_a_stat(question):
                raw.pop("stat", None)
        elif asked is not None and not named_player and isinstance(raw.get("team"), str) and raw["team"].strip():
            # A TEAM's half. A team's QUARTER never reaches here - the
            # exemption above keeps it on its own template - but a half always
            # does, because the model maps "first half" onto period 1 and that
            # is wrong for a team the same way it is for a player. The
            # linescore holds both quarters, so the template sums them.
            raw["intent"] = "team_quarter_points"
            raw |= asked
        elif asked is not None and named_player:
            raw["intent"] = "period_split"
            raw |= asked
            # `stat` is the one REQUIRED slot, so the model fills it whether or
            # not the question named one - measured, "duren v nets 1h gameloh"
            # arrived with stat="none" and "scottie barnes stats 2nd half log"
            # with stat="minutes", and the template refused both as asking for
            # a stat it cannot give. Only a stat the question names is kept,
            # the same rule player_compare follows (see _named_a_stat).
            if not _named_a_stat(question):
                raw.pop("stat", None)
            # A log was asked for, not a season average. Measured, 7 of the 11
            # questions this template answered in its first replay said "log",
            # "by game" or "each game" and got a total and an average.
            if _LOG_WORDS.search(question):
                raw["per_game"] = True
        else:
            raw["intent"] = "other"


def _route_team_and_player_intents(raw: dict[str, Any], question: str) -> None:
    """A team where a player ranking was asked for, a record, a fingerprint with
    no fingerprint words, and a player measured against a team."""
    if raw["intent"] in _PLAYER_RANKING_INTENTS and _TEAM_SUBJECT.search(question):
        # A team ranking has a template; a team's single-game record and a
        # count of team games do not, and answering either with players is the
        # substitution this exists to stop.
        raw["intent"] = "team_leaderboard" if raw["intent"] == "leaderboard" else "other"
    if raw["intent"] == "threshold_count" and _RECORD.search(question):
        raw["intent"] = "record_when"
    if raw["intent"] == "fingerprint" and not _FINGERPRINT_WORDS.search(question):
        raw["intent"] = "shot_chart" if _SHOT_WORDS.search(question) else "other"
    listed = [name for name in raw.get("players") or [] if isinstance(name, str)]
    if raw["intent"] == "player_compare" and sum(map(_is_team_name, listed)) == 1 and len(listed) == 2:
        # One player compared with a team is his games against it. Measured:
        # "compare curry vs the celtics this season" arrived as player_compare
        # with the Celtics in `players`; scope_from_question made them the
        # opponent, which player_compare cannot honor, so the question fell
        # through to the agent while player_stat answers it exactly. Two
        # players and a team stay a comparison, and refuse the opponent.
        raw["intent"] = "player_stat"
    if raw["intent"] == "player_stat" and _LOG_WORDS.search(question):
        raw["intent"] = "game_log"
    _route_matchup_against_team(raw, question, listed)


def _route_matchup_against_team(raw: dict[str, Any], question: str, listed: list[str]) -> None:
    """A ``player_matchup`` whose second "player" is a team."""
    if raw["intent"] == "player_matchup" and any(map(_is_team_name, listed)):
        # One of the "two players" is a team: this is a player's games against
        # it. entities.scope_from_question moves the team to `opponent`.
        raw["intent"] = "game_log" if _LOG_WORDS.search(question) or _GAMES_WORDS.search(question) else "player_stat"
    if raw["intent"] == "player_matchup" and len(listed) < 2 and isinstance(raw.get("player"), str):
        # The same question, arriving in the other shape. The rule above reads
        # `players`, and the model routinely fills the SINGULAR `player` and an
        # `opponent` instead - "keon ellis stats vs trailblazers", "Kd games vs
        # wizards", "De'angelo russell vs pistons". player_matchup needs two
        # players and had one, so eight feed queries fell through to the agent
        # where player_stat and game_log answer them exactly, both honoring
        # `opponent`.
        #
        # An opponent that is NOT a team is left alone: "jay huff game log vs
        # Embiid" really is a matchup between two players, and the model put
        # the second one in `opponent`.
        against = raw.get("opponent") or next(iter(raw.get("teams") or []), None)
        if isinstance(against, str) and _is_team_name(against):
            raw["intent"] = "game_log" if _LOG_WORDS.search(question) or _GAMES_WORDS.search(question) else "player_stat"


def _route_line_and_record_intents(raw: dict[str, Any], question: str) -> bool:
    """A history that is really a line, a record ranking, and a career high.
    Returns whether a history was rerouted to a line."""
    rerouted_to_line = False
    if raw["intent"] == "player_history" and (not _named_a_stat(question) or (_VERSUS_WORDS.search(question) and _TEAM_WORD.search(question))):
        # A season-by-season history of one stat is neither "career averages"
        # (no stat named - the whole line) nor a career against one team.
        # Measured: "Jokic career averages" answered with points by season,
        # "derozan career points vs knicks" refused on its opponent.
        raw["intent"] = "player_stat"
        rerouted_to_line = True
    if raw["intent"] == "team_record" and _BEST_WORST_RECORD.search(question) and not _TEAM_WORD.search(question):
        raw["intent"] = "team_leaderboard"
        raw["stat"] = "record"
        raw.pop("team", None)
    if raw["intent"] == "player_stat" and _CAREER_HIGH.search(question):
        # A career high is one game's total, which player_stat never reports.
        # Measured: "Diabate career high assists" was answered with his assists
        # per game.
        raw["intent"] = "single_game_high"
    return rerouted_to_line


def _route_season_slots(raw: dict[str, Any], question: str) -> dict[str, Any]:
    """The slots, with blanks dropped and the season and season type read the code's way."""
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
    return slots


def _route_threshold(raw: dict[str, Any], slots: dict[str, Any], question: str) -> None:
    """A threshold the model left out, and a count of games that has none."""
    if raw["intent"] in _THRESHOLD_INTENTS and not isinstance(slots.get("threshold"), int):
        # Measured: "Sixers record when Embiid scores 30 points" came back with
        # the intent right and no threshold at all.
        threshold = _threshold_from_text(question)
        if threshold is not None:
            slots["threshold"] = threshold
    if raw["intent"] == "record_when" and not isinstance(slots.get("threshold"), int):
        # A record "when X and Y played" is a with_without question - the
        # games they were all in, beside the ones they were not - and not a
        # record_when one, which divides a season by a NUMBER a player
        # reached. With no threshold there is no number to divide by, and
        # record_when refused all four phrasings the web session asked (#156).
        # A question that does name a threshold keeps its intent, so "Sixers
        # record when Embiid scores 30 points" is untouched.
        together = _played_together(question)
        if together:
            raw["intent"] = "with_without"
            slots["with_player"] = together
    if raw["intent"] == "threshold_count" and not isinstance(slots.get("threshold"), int):
        # A count of games needs a threshold. Without one, "who has the most
        # threes" is a season ranking - measured, it arrived here with none and
        # fell through.
        raw["intent"] = "leaderboard"


def _route_filter_slots(slots: dict[str, Any], question: str) -> tuple[str | None, list[str]]:
    """Span, venue, teammates missing, a ceiling and a situation. Returns the span and the absent teammates."""
    # The scoping slots below are read from the question and never asked of the
    # model: none is in ROUTER_SCHEMA, so adding them changed no grammar and can
    # have moved no other question's routing. A template that cannot honor one
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
    without = _names_after(_WITHOUT, question)
    if without:
        slots["without"] = without
    # Every one, not the first: "less than 15 fga and with less than 35
    # minutes" is two filters, and honoring one of them answers a wider
    # question than was asked. A phrase the relation cannot read refuses there.
    below = [m.group(0).casefold() for m in _BELOW.finditer(question)]
    if below:
        slots["below"] = below
    above = [m.group(0).casefold() for m in _ABOVE.finditer(question)]
    # Only where the question states more than one: a single "30+ points" is
    # the model's own `threshold` and every template that reads one already
    # carries it, so nothing changes for the questions that work today. With
    # two, BOTH become lines - threshold_count reads a line carrying its own
    # threshold's number as that threshold, misread, and filters on all of
    # them (see _threshold_count_lines).
    pairs = [m.group(0).casefold() for m in _THRESHOLD_PAIR.finditer(question)]
    if len(pairs) > 1:
        above += pairs
    if above:
        slots["above"] = above
    situation = _SITUATION.search(question)
    if situation is not None:
        slots["situation"] = situation.group(0).casefold()
    return span, without


def _route_season_range(intent: str, slots: dict[str, Any], question: str) -> None:
    """The seasons a question spans when it names a range rather than one
    year: "since 2020", "the 2010s", "the past two seasons"."""
    # Measured: "most 3 pointers made since 2020" became season=2020 and was
    # answered as "the most games with 0+ 3-pointers in the 2020 regular season".
    seasons = _validate_range(question)
    if seasons is not None:
        slots["since"] = seasons[0]
        if seasons[1] is not None:
            slots["until"] = seasons[1]
        slots.pop("season", None)
    else:
        # "past two seasons" / "last 3 years": a relative window, not the
        # absolute one "since YYYY" or a decade name - see
        # _validate_relative_season_span. No `until`: the window already ends
        # at "now", the same place `since` alone reaches.
        relative_since = _validate_relative_season_span(question)
        if relative_since is not None and intent in _LIMIT_COUNTS_SEASONS:
            # A history's `limit` counts SEASONS, not games, so "the past 5
            # years" IS that limit and needs no span. Setting `since` here
            # instead would hand the template a slot it does not honor, and
            # "show me sga's 2pt percentage for the past 5 years" - which the
            # question below answers - would refuse. Found on the merged tree:
            # #140 (this rule) and #114 (the stat) were each sound alone.
            slots["limit"] = current_season() - relative_since + 1
            slots.pop("season", None)
        elif relative_since is not None:
            slots["since"] = relative_since
            slots.pop("season", None)
            if isinstance(slots.get("limit"), int) and not _names_a_count(question):
                # The model's own count word landed on `limit` instead of the
                # season count it actually modifies (#140): "...in the past
                # two seasons" arrived with limit=2 and, from that alone,
                # answered his last 2 games of his career. `_names_a_count`
                # already looks past _PAST_N_SEASONS's own number here, so
                # this only fires when nothing ELSE in the question names a
                # real count of games ("last 5 games in the past two seasons"
                # keeps its limit).
                slots.pop("limit", None)


def _route_calendar_slots(intent: str, slots: dict[str, Any], question: str, span: str | None) -> None:
    """A calendar day, a playoff round, a range of seasons and a split."""
    # After `span`, which pops the season on a career question. The season that
    # fixes the year is the one a template would use - `slots.get("season") or
    # current_season()`, the same default they all apply - EXCEPT on a career
    # question, which spans twenty Octobers and fixes nothing, so that refuses.
    # Reading the model's absent season as "current" rather than "unknown"
    # matters: it omits `season_ref` often, and "Desmond bane march 17" arrives
    # with no season at all.
    asked_season = slots.get("season") if isinstance(slots.get("season"), int) else None
    fixing_season = None if span == "career" else (asked_season or current_season())
    calendar_day = _validate_date(question, fixing_season)
    if calendar_day is not None:
        slots["date"] = calendar_day
    elif _CALENDAR_DATE.search(question) and "situation" not in slots:
        # A date that named itself but could not be pinned to one day.
        slots["situation"] = _CALENDAR_DATE.search(question).group(0).casefold()  # type: ignore[union-attr]
    playoff_round = _ROUND_WORDS.search(question)
    if playoff_round is not None:
        slots["round"] = playoff_round.group(0).casefold()
    series_game = _GAME_N.search(question)
    if series_game is not None:
        slots["game_n"] = int(series_game.group(1))
    ordinal_season = _SEASON_N.search(question)
    if ordinal_season is not None:
        slots["season_n"] = int(ordinal_season.group(1))
        if season_from_text(question) is None:
            slots.pop("season", None)
    _route_season_range(intent, slots, question)
    # A split is read for every intent, not only player_splits: it is a scoping
    # slot, so the template that answers one honors it and every other refuses.
    # Measured: "Joe Ingles stats when starting vs coming off the bench" was
    # answered with his season minutes, "Giannis stats by month" with his points
    # by season.
    splits = [name for name, pattern in SPLIT_WORDS.items() if pattern.search(question)]
    if len(splits) == 1:
        slots["split"] = _split_side(splits[0], question)


def _route_intent_slots(intent: str, slots: dict[str, Any], question: str, without: list[str]) -> None:
    """Slots only one template reads."""
    # Intent-specific: each means nothing to any other template, so each is
    # only added where one reads it - the same rule `side` follows below.
    if intent == "with_without":
        # The same reader the record_when reroute uses (#156), so the two
        # cannot disagree about who the question named: reading "with" alone
        # here overwrote ['Embiid', 'Paul George'] with ['Paul George'] on
        # "PHI record when Embiid with Paul George".
        with_player = _played_together(question)
        if with_player and not without:
            slots["with_player"] = with_player
    if intent == "player_splits" and slots.get("split") == "home_away":
        slots.pop("venue", None)  # a split over venues is not a filter to one
    if intent in ("team_leaderboard", "team_quarter_points"):
        # For team_quarter_points this is what makes "most points in a first
        # half" one game rather than the season's average - see its answer.
        rank = next((name for name, pattern in RANK_WORDS if pattern.search(question)), None)
        if rank is not None:
            slots["rank"] = rank
    if intent == "streak":
        slots["kind"] = "loss" if _LOSING_STREAK.search(question) else "win"


def _route_line_stat(intent: str, slots: dict[str, Any], question: str, rerouted_to_line: bool) -> None:
    """A required ``stat`` the question never named, a history's season count, and an advanced metric the question did name."""
    # For the two templates whose default is a whole line, which still holds
    # any stat the word list missed. Dropping it for `leaderboard` would leave
    # it with no metric to rank by. Measured on player_stat: "Jokic career
    # averages" arrived with stat='points' and "LeBron James career playoff
    # stats" with stat='career_playoffs'.
    if intent in ("player_compare", "player_stat") and not _named_a_stat(question):
        slots.pop("stat", None)
    if rerouted_to_line:
        # A history's `limit` counted seasons; the line it became has none.
        for key in ("limit", "fields"):
            slots.pop(key, None)
    if intent in _ADVANCED_STAT_INTENTS:
        advanced = next((metric for metric, pattern in _ADVANCED_STAT_WORDS if pattern.search(question)), None)
        if advanced is not None:
            slots["stat"] = advanced


def _route_team_slots(intent: str, slots: dict[str, Any], question: str) -> None:
    """A pseudo-team dropped, and a team's or a streak's ``stat`` kept only where the question names one."""
    team_slot = slots.get("team")
    if isinstance(team_slot, str) and _PSEUDO_TEAM.fullmatch(team_slot.strip()):
        slots.pop("team", None)
    # The same drop for two more intents where the required slot is noise when
    # the question names no stat: "Knicks stats" arrived as stat='points' and
    # narrowed a team's line to one number, and a team's winning streak arrived
    # with a stat and no threshold and was refused.
    if intent == "team_stat" and not (_named_a_stat(question) or _TEAM_STAT_WORDS.search(question)):
        slots.pop("stat", None)
    if intent in ("team_stat", "team_leaderboard"):
        named_metric = _team_metric_in(question)
        if named_metric is not None:
            slots["stat"] = named_metric
    if intent == "streak" and not _named_a_stat(question):
        slots.pop("stat", None)


# A count asked of one player with no season in sight: "how many times has
# embiid fouled out?" is 0 this season and 9 in his career, and only the second
# is the question. Product decision (2026-09-19): an unscoped count by a named
# player reads as his career, and the answer names the scope it used.
_HOW_MANY = re.compile(r"\bhow\s+many\b", re.IGNORECASE)


def _route_record_when_threshold(intent: str, slots: dict[str, Any], question: str) -> None:
    """Read a ``record_when`` question's threshold off its own words.

    Measured live (web session, build ``178c21f-dirty``): "what was the sixers
    record this season when tyrese maxey had 20+ points?" came back with
    ``stat='wins'``. The word "record" is what the model had to file under
    ``stat``, which is required and whose enum has no entry for a won-lost
    record, so the nearest value it knows won - and the template refused with
    "record_when needs a known stat and a positive threshold, got 'wins'/20"
    about a question that states its stat plainly. `stat` is the shape
    ISSUES.md #114 is about, arriving through the required slot again.

    "20+ points" is one fact, so the pair is read as one: a question stating
    exactly one of them sets both halves, and its own words beat the model's
    guess. Two of them ("20+ points and 5+ assists") name a shape
    ``record_when`` has no second threshold for, so they are left alone to
    refuse rather than silently answering about whichever half won.

    ``ROUTER_PROMPT`` and ``ROUTER_SCHEMA`` are untouched, so this can move no
    other question's routing.
    """
    if intent != "record_when":
        return
    pairs = list(_THRESHOLD_PAIR.finditer(question))
    if len(pairs) != 1:
        return
    slots["stat"] = _THRESHOLD_WORDS[pairs[0].group(2).casefold()]
    slots["threshold"] = int(pairs[0].group(1))


# The intents whose missing subject is read back out of the question's grammar.
# `record_when` joined them for a question that cost 583 seconds and produced
# nothing: "what was the sixers record when maxey scored 15+ points?" routed
# correctly, with the team, the stat and the threshold all right and no
# `player` at all, so the template raised "record_when needs a player" and the
# agent spent nearly ten minutes failing to write the join. The name was
# already sitting in the grammar `_SUBJECT_OF_HIGH` reads ("maxey scored"); it
# was simply never asked for here.
#
# Nothing about this widens what counts as a subject. Note the difference from
# the other two: a `record_when` with no player is not a league question but an
# unanswerable one (it is in `PLAYER_REQUIRED_INTENTS`), so restoring the
# subject can only turn a refusal into an answer, never a league ranking into
# one man's.
_SUBJECT_RESTORED_INTENTS = ("single_game_high", "threshold_count", "record_when")


def _route_subject_slots(intent: str, slots: dict[str, Any], question: str) -> None:
    """The last meetings with an opponent across seasons, and a single-game
    high's or a threshold count's missing subject."""
    # "last 8 games vs pistons" with no season named means the last eight
    # meetings, wherever they fall - answered from the current season alone it
    # found four and said so. A season the question names still wins.
    if (
        intent == "game_log"
        and _VERSUS_WORDS.search(question)
        and (isinstance(slots.get("limit"), int) or re.search(r"\blast\b", question, re.IGNORECASE))
        and season_from_text(question) is None
        and not _SEASON_WORDS.search(question)
    ):
        slots["span"] = "career"
        slots.pop("season", None)
    if intent in _SUBJECT_RESTORED_INTENTS and not slots.get("player") and not slots.get("players"):
        # An optional slot the model dropped, restored from the question's own
        # grammar - see _subject_named_in. Only where the template reads one
        # player: a leaderboard with no player IS the league's ranking, and so
        # is a threshold_count with no player - restoring the subject only
        # where the question's own grammar names one (#138) never turns a
        # genuine league question into one about somebody it only appears to
        # name.
        subject = _subject_named_in(question)
        if subject is not None:
            slots["player"] = subject
    if (
        intent == "threshold_count"
        and slots.get("player")
        and _HOW_MANY.search(question)
        and not slots.get("span")
        and not slots.get("season_n")
        and season_from_text(question) is None
        and not _SEASON_WORDS.search(question)
    ):
        # See _HOW_MANY. A season the question names, this one included,
        # still wins; so does an ordinal season, settled later.
        slots["span"] = "career"
        slots.pop("season", None)


def _route_side_and_order(intent: str, slots: dict[str, Any], question: str) -> None:
    """The side of the ball for a fingerprint, and which end of the season was asked for."""
    if intent == "fingerprint":
        side = _validate_side(slots, question)
        if side is None:
            slots.pop("side", None)
        else:
            slots["side"] = side
    # Only for the templates that honor it - see ORDER_INTENTS for why adding
    # it anywhere else would cost an answer rather than sharpen one.
    if intent in _ORDER_ON_A_SINGLE_GAME and (single := _SINGLE_GAME.search(question)):
        # "his last game", "her first game of the season": one game at one end
        # of the span, which player_stat answers by handing the question to
        # game_log. Both slots are set together - an order without the limit
        # would list ten games where one was asked for, and the model emits
        # neither reliably here (it was 0 for 3 on "his last game"). A filler
        # order on a question naming no such game still falls to the branch
        # below and is dropped.
        slots["order"] = "first" if single.group(1).lower() in ("first", "opening", "earliest") else "recent"
        slots["limit"] = 1
    elif intent in ORDER_INTENTS:
        order = _validate_order(slots, question)
        if order is not None and intent in _ORDER_IS_ONE_GAME and not _names_one_game(question):
            # A model `order` on an intent where it means ONE game, on a
            # question naming no such game: filler that costs the season.
            order = None
        if order is None:
            # Only a value the schema cannot emit ever gets dropped here; a
            # valid one the patterns did not recognize is kept - see
            # _validate_order.
            slots.pop("order", None)
        else:
            slots["order"] = order
    elif slots.get("order") and not any(pattern.search(question) for pattern in ORDER_WORDS.values()):
        # An `order` the model added to an intent that cannot honor one, on a
        # question naming no game at either end. Measured: "evan mobley avg
        # against bucks" and "Celtics record without Tatum" both arrived with
        # order='recent' and limit=1, and check_scope refused them. A limit of
        # one rode in with it and goes too; a real one ("top 5") stays.
        slots.pop("order", None)
        if slots.get("limit") == 1:
            slots.pop("limit", None)
    _drop_filler_limit(intent, slots, question)


def _drop_filler_limit(intent: str, slots: dict[str, Any], question: str) -> None:
    """A ``limit`` the model filled on a question that names no number of games."""
    if intent == "game_log" and slots.get("limit") == 1 and _LOG_WORDS.search(question) and not _names_a_count(question) and not _SINGLE_GAME.search(question):
        # A log asked for by name (_LOG_WORDS: "gamelog", "by game"), with a
        # limit of one the question never set: "paul reed gamelog with 25
        # minutes" arrived with order='recent', limit=1 and answered his most
        # recent game where his log was asked. A model `order` on game_log is
        # kept whatever the patterns miss (_validate_order), so the limit is
        # the only thing to drop; a real single game ("his last game") or a
        # count ("last 5 games") keeps it.
        slots.pop("limit", None)
    if _SINGLE_GAME.search(question) and season_from_text(question) is None and not _SEASON_WORDS.search(question) and isinstance(slots.get("season"), int) and slots["season"] != current_season():
        # "show a shot chart of steph curry's last regular season game" came
        # back as season 2025: the model read "last regular season" as the
        # season before this one, and the chart drew a game a year off (#153).
        # A question naming one game at one end of the span, with no year and
        # no season words of its own, means the current season - the default
        # every template already applies. A year the question states, and
        # "last season", both still win.
        slots.pop("season", None)
    if intent in _LIMIT_REFUSING_INTENTS and isinstance(slots.get("limit"), int) and not slots.get("order") and not _names_a_count(question):
        # The same filler, arriving WITHOUT an `order` to carry it in.
        # "westbrook stats as a starter for kings" came back with limit=1 and
        # side='total' on a question that narrows to no number of games at all.
        # A limit on `player_stat` hands the question to game_log (a player's
        # numbers over his last N games IS a log), so a filler one no longer
        # costs the answer - it answers a different question: "Portis vs bulls
        # 2019-20 to 2023-24" arrived with limit=5 and became a three-game log
        # where his averages were asked for. Any count the question does not
        # name goes, not only a 1; a real one ("last 5 games", "top 10") stays.
        slots.pop("limit", None)


def _route_game_log_recent_span(intent: str, slots: dict[str, Any], question: str) -> None:
    """A "last N games" question naming no season type: a signal for
    ``game_log`` to read both season types and take the newest N by date,
    rather than silently defaulting to the regular season the way
    ``_validate_season_type`` already does everywhere else - see ISSUES.md,
    ``"Last N games" means the last N regular-season games...``. "Show me the
    Knicks last 5 games" used to list games through 2026-04-12 while their
    real last five were the 2026 Finals, played weeks later.

    The template sees only slots, not the question, so the signal has to be
    set here - the same discipline ``side`` and ``coach`` follow. Only for
    ``game_log``, and only for the shape the issue names: ``order`` is
    ``"recent"`` beside a real ``limit`` (the pair `_route_side_and_order`
    already settled, filler dropped), and nothing already fixes which games
    are meant - ``game_n`` (one game of a KNOWN playoff series), ``span``
    (a career has no single year to mix two types within), ``since`` (a
    range of seasons) and ``date`` (one calendar day already finds its own
    game, whatever type it was) are each a narrower question than "last N",
    so none of them widen here.

    ``ROUTER_PROMPT`` and ``ROUTER_SCHEMA`` are untouched, so this can move no
    other question's routing.

    .. versionadded:: 4.4.0
    """
    if intent != "game_log" or slots.get("order") != "recent":
        return
    if not isinstance(slots.get("limit"), int) or slots["limit"] < 1:
        return
    if slots.get("game_n") or slots.get("span") or slots.get("since") or slots.get("date"):
        return
    if _PLAYOFF_WORDS.search(question) or _REGULAR_SEASON_WORDS.search(question):
        return
    slots["season_type_unstated"] = True


def route(model: str, question: str, previous_question: str | None = None) -> Route | None:
    """Classify one question. Returns None if the model is unreachable or
    replies with something unparsable - the caller falls through to the full
    agent, so a router failure costs a round trip, never an answer."""
    raw = _route_ask_model(model, question, previous_question)
    if raw is None:
        return None
    # A coach question is refused whatever the model said, and carries no
    # slots, so it short-circuits before any of the stages below run.
    if _route_coach_intent(raw, question):
        return Route(intent=raw["intent"], slots={})
    # The stages run in this order because each reads what the ones before it
    # rewrote: the intents code assigns decide which slots are read, and a
    # threshold the question lacks turns a count back into a ranking before
    # any intent-specific slot is chosen.
    _route_period_intents(raw, question)
    _route_team_and_player_intents(raw, question)
    rerouted_to_line = _route_line_and_record_intents(raw, question)
    slots = _route_season_slots(raw, question)
    _route_threshold(raw, slots, question)
    span, without = _route_filter_slots(slots, question)
    _route_calendar_slots(raw["intent"], slots, question, span)
    _route_intent_slots(raw["intent"], slots, question, without)
    _route_line_stat(raw["intent"], slots, question, rerouted_to_line)
    _route_game_score(raw["intent"], slots, question)
    _route_two_point_pct(raw["intent"], slots, question)
    _route_leaderboard_shot_distance(raw["intent"], slots, question)
    _route_team_slots(raw["intent"], slots, question)
    _route_rate(raw["intent"], slots, question)
    _route_subject_slots(raw["intent"], slots, question)
    _route_record_when_threshold(raw["intent"], slots, question)
    _route_side_and_order(raw["intent"], slots, question)
    _route_game_log_recent_span(raw["intent"], slots, question)
    return Route(intent=raw["intent"], slots=slots)
