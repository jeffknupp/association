"""The reader's vocabulary: every pattern a tagger reads the question's words
by, named, with the reason each is shaped as it is kept beside it - and
nothing that reads the warehouse or the answer side (``ROADMAP.md``,
contract 6: "regexes only in the lexicon"; contract 1: the lowest layer of
the reader, ``say > relations > plan > read > lexicon``).

Phase 3, step 2 moved the span family's words here first - the seasons a
question names and the season type it reads: a season written out or
relative ("2023-24", "last season"), a career ("all time", "since he
joined the league"), a range ("since 2015", "the 2010s", "from 2019-20 to
2023-24", "the past two seasons"), the postseason and both season types
("including the playoffs") - with the two readers of a season span that
every other reader of a year goes through (:func:`season_spans`,
:func:`season_from_text`). The window's followed (the second slice: a count of games
or seasons and the end they are taken from, which end of a ranking, the
measure a ranking of games is ordered by - :data:`WINDOW_GRAMMAR`,
:data:`ORDER_WORDS`, :data:`SINGLE_GAME`, :data:`RANK_WORDS`, :data:`RANKED_BOOLEAN_GAMES`, and the family's intent
guards). The other families' words follow, one slice each (``ROADMAP.md``,
"Phase 3, the expected steps", step 2): the games' cuts, the period, the
line and the companions, the subject's own. Until each moves, its patterns
stay in :mod:`association.query.router` and :mod:`association.query.subject`.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from association.nba.season import current_season

MIN_SEASON = 1947
"""The earliest year read as a season: the league's first (1946-47).

Deliberately not a data floor. Those live in :mod:`association.nba.coverage`,
where a season below a table's first one is refused, with the reason. At 1990
this dropped every earlier year before the coverage check could see it, and
"who led the league in scoring in 1980" was answered with the current
season's leaders.

.. versionchanged:: 2.1.0
   Lowered from 1990, and shared with the router rather than copied.

.. versionchanged:: 6.0.0
   In the lexicon (``season_text.MIN_SEASON`` until Phase 3, step 2).
"""

# "2023-24", "2023-2024" and "2023/24" all mean the season ENDING in 2024 -
# ESPN's convention, and the form a hyphen-blind year regex gets exactly one
# year wrong. Matched before the plain-year pass so the span wins.
#
# Two digits a side ("23-24", "23/24") is the same season written short, and
# "points per game leaders for the 23-24 nba season" was answered for the
# current season while it went unread (#168). Short numbers joined by a
# hyphen can as well be a record or a score ("10-12"), so the short form is a
# season only where the second year follows the first (`season_spans`).
# Measured over the 3,083 distinct questions of the research corpora (both
# StatMuse samples and the recorded yardstick corpus): 29 short pairs have
# the second year follow the first, and every one names a season; none of the
# other 5 names one ("kobe bryant 00-02" is a range, "Lebron 21-13" a line).
# The boundaries keep out a date's pieces ("11/29/24" holds "29/24"), a clock
# ("1:23-24"), a decimal and a percentage.
SEASON_SPAN = re.compile(
    r"\b(?P<start>(?:19|20)\d{2})\s*[-/]\s*(?P<end>(?:19|20)?\d{2})\b"
    r"|(?<![\d/.:-])\b(?P<short_start>\d{2})(?P<separator>[-/])(?P<short_end>\d{2})\b(?![-/:%]|\.\d)"
)
"""A season written as the two calendar years it spans, or a range of
seasons written as its first and last (:func:`season_spans`).

.. versionadded:: 6.0.0
"""
YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
"""A calendar-shaped year, read as the season ending in it (:func:`season_from_text`).

.. versionadded:: 6.0.0
"""
PREVIOUS_SEASON = re.compile(r"\b(?:last|previous|prior)\s+(?:season|year)\b|\ba year ago\b")
"""The season before the current one, named relatively.

.. versionadded:: 6.0.0
"""
CURRENT_SEASON = re.compile(r"\b(?:this|current)\s+(?:season|year)\b|\bso far\b|\bright now\b|\bto date\b")
"""The current season, named relatively.

.. versionadded:: 6.0.0
"""

# "this postseason" names the current season as surely as "this season" does:
# without it, "maxey's stats for game 4 against the knicks this postseason"
# read as a career question and asked which Maxey. Read as a guard - a season
# word that stops a career being implied - rather than as the season itself,
# which `season_from_text` reads; "next season" and "this postseason" name
# no season the warehouse holds a year for.
SEASON_WORDS = re.compile(r"\b(?:this|last|next)\s+(?:season|year|postseason|playoffs)\b", re.IGNORECASE)
"""A season named relatively, in any of its words - the guard that keeps a
count, a pair's record or a single game from being read over a career.

.. versionadded:: 6.0.0
"""

# The postseason, named in the question. The model sets season_type="playoffs"
# on questions that never mention them - measured at temperature 0, "Sga record
# 36 plus points" and "lebron vs kawhi 2015" both came back as playoff questions,
# and "tatum stats in the 2024 finals" came back as a regular-season one. Read
# from the text for the same reason the year is: the question is the source,
# and the model is wrong in both directions. "Title" and "championship" are left
# out on purpose - "title odds" is a regular-season projection.
PLAYOFF_WORDS = re.compile(r"\b(?:playoffs?|post-?season|finals|elimination|game\s+(?:7|seven))\b", re.IGNORECASE)
"""The postseason, named outright.

.. versionadded:: 6.0.0
"""

# The regular season, named outright - the one further word a "last N games"
# question needs beside PLAYOFF_WORDS: one that says "regular season" has
# stated its type as plainly as one that says "playoffs", and must keep
# meaning only that.
REGULAR_SEASON_WORDS = re.compile(r"\bregular[- ]season\b", re.IGNORECASE)
"""The regular season, named outright.

.. versionadded:: 6.0.0
"""

# Both season types at once, named outright: "including the playoffs",
# "including postseason", "regular season and playoffs", "playoffs included".
# Read APART from PLAYOFF_WORDS, which used to be consulted alone -
# "including playoffs" contains the word "playoffs", so the type read as the
# postseason alone, silently dropping the regular season the question asked
# to KEEP: "warriors all-time record including playoff record at away"
# answered only the playoff road record (51-52), and "Payton Prichard stats vs
# 76ers at home including playoffs game log" answered 7 playoff meetings and
# then told the reader "Only 7 games ... in his box scores" - a false claim
# about games (the 9 regular-season meetings) that were never read at all, not
# a true count of what was found.
BOTH_SEASON_TYPES_WORDS = re.compile(
    r"\bincluding\s+(?:the\s+)?(?:playoffs?|post-?season)\b"
    r"|\b(?:playoffs?|post-?season)\s+included\b"
    r"|\bregular\s+season\s+and\s+(?:the\s+)?(?:playoffs?|post-?season)\b"
    r"|\b(?:playoffs?|post-?season)\s+and\s+regular\s+season\b",
    re.IGNORECASE,
)
"""Both season types, asked for together.

.. versionadded:: 6.0.0
"""

# A whole career rather than one season. Measured before this existed: "career
# points leaders" and "Jokic career averages" were both answered with one
# season, fluently. "Career high" is the exception: with a season named
# ("career high this season") it means that season's best, and it was a worked
# example of single_game_high in the retired router's prompt - CAREER_HIGH is
# blanked out before these are read where a season is named beside it.
CAREER_WORDS = re.compile(r"\b(?:career|all[- ]?time|ever|(?:in|of)\s+(?:nba\s+)?history|of\s+all\s+time)\b", re.IGNORECASE)
"""Every season on record, named.

.. versionadded:: 6.0.0
"""
CAREER_HIGH = re.compile(r"\bcareer[- ]highs?\b", re.IGNORECASE)
"""A career high - the one "career" that names a single game's best, not a span.

.. versionadded:: 6.0.0
"""
# "since he/she joined the league", "since entering the league": the same
# "every season" reading CAREER_WORDS' own "career" gets, in words that do
# not contain it - yardstick-v2 F031, "Show me luka's avg assists since he
# joined the league", used to answer one season (whichever the router's
# season default happened to be) where the question asked for his whole
# career. Anchored on "the league" so it cannot fire on "since he joined the
# team" (#147's own team question) or "since he joined the Mavericks".
CAREER_JOINED_LEAGUE_WORDS = re.compile(r"\bsince\s+(?:he|she|they)\s+(?:joined|entered)\s+the\s+league\b|\bsince\s+(?:joining|entering)\s+the\s+league\b", re.IGNORECASE)
"""A career, named as the time since the player joined the league.

.. versionadded:: 6.0.0
"""
# "all playoff games" / "every playoff game" / "all his playoff games" (#141):
# none of CAREER_WORDS' words appear in them, so "show a shot chart for steph
# curry in all playoff games" carried no span at all and the season defaulted
# to the latest with data - one postseason drawn and presented as all of them,
# with nothing in the answer saying so. Anchored on a season-TYPE word
# ("playoff", "postseason", "preseason", "regular season") immediately after
# "all"/"every" (and an optional possessive) so it cannot fire on "all star" -
# "star" is not one of them - or on an unrelated "all ... games" ("all the
# games Curry played in March").
CAREER_ALL_GAMES_WORDS = re.compile(r"\b(?:all|every)\b(?:\s+(?:his|her|their))?\s+(?:playoff|post-?season|pre-?season|regular[- ]season)\s+games?\b", re.IGNORECASE)
"""A career, named as every game of one season type.

.. versionadded:: 6.0.0
"""

# A range of seasons rather than one. "since 2020" is every season from the one
# ending in 2020; a decade ("the 2010s") is the seasons ending in it. Stated this
# way, not guessed at, so a reader that honors it can print the exact range.
SINCE_YEAR = re.compile(r"\bsince\s+(?:the\s+)?((?:19|20)\d\d)\b", re.IGNORECASE)
"""An open range from a bare year: "since 2020".

.. versionadded:: 6.0.0
"""
# "since 2000-01" / "since 2000-2001" / "since 00-01": a season written as a
# span after "since", which is the season ENDING in the second year
# (nba/season.py) - SINCE_YEAR alone read its leading "2000" and started a
# season early (#207). What a span is, is `season_spans`' to say - the one
# definition, so "00-01" reads here as it does alone - and the span must be
# one season's two years: a range ("since 2019-2024", "since 2000-05") is
# read before this, as the range it writes.
SINCE_SPAN = re.compile(r"\bsince\s+(?:the\s+)?\Z", re.IGNORECASE)
"""The "since" before a season written as a span (matched up to the span's start).

.. versionadded:: 6.0.0
"""
DECADE = re.compile(r"\b(?:the\s+)?((?:19|20)\d)0'?s\b", re.IGNORECASE)
"""A decade: the ten seasons ending in it.

.. versionadded:: 6.0.0
"""

# A CLOSED range - both ends named - rather than the open "since 2020" above.
# `until` was declared nowhere and honored nowhere until this existed
# (AGENTS.md's own worst-failure-shape example: "best 3 point shooters of the
# 2010s" answered 2010 through now), so every form here fills BOTH ends.
#
# "2019-20 to 2023-24" / "from 2010-11 to 2018-19" / "02-03 to 06-07": a
# season written as a span on each side of "to"/"through" - the words this
# matches BETWEEN two spans `season_spans` found, so the short form ("kobe
# bryant playoff stats from 02-03 to 06-07") is a range here exactly where
# it is a season alone, and not the first season of the two.
RANGE_TO_WORDS = re.compile(r"\s+(?:to|through)\s+", re.IGNORECASE)
"""The joining words of a range written as two season spans.

.. versionadded:: 6.0.0
"""
# "between 2020 and 2024": bare calendar-shaped years, read as season NUMBERS
# (the same reading SINCE_YEAR's own bare year gets), not calendar years.
RANGE_BETWEEN = re.compile(r"\bbetween\s+((?:19|20)\d\d)\s+and\s+((?:19|20)\d\d)\b", re.IGNORECASE)
"""A closed range between two bare years.

.. versionadded:: 6.0.0
"""
# "2020-2024", "2015-18", "00-02": one span whose years do not follow each
# other is a range of seasons, both ends season numbers - read by
# `season_spans` (`SeasonSpan.is_range`), the one definition of a span.
# CONSECUTIVE years are not a range at all: "the 2023-2024 season" is how
# people write ONE season (2024) with both digits spelled out, exactly as
# "2023-24" means; a regex of its own here once re-matched that text as
# since=2023/until=2024, silently overwriting a right answer with a wrong one
# a step later. "2024-2026" is Jeff's own yardstick wording ("how many 20+
# point games did SGA have 2024-2026?"); "2015-18" read as 2018 alone, and
# "00-02" as nothing, until the span said range (#261).
# "knicks record by month 2024 2025": two bare, ADJACENT season numbers with
# nothing joining them - only when the second is exactly one more than the
# first, so this reads as a range and not two unrelated years mentioned in
# passing.
RANGE_BARE_YEARS = re.compile(r"\b((?:19|20)\d\d)\s+((?:19|20)\d\d)\b")
"""Two bare years with nothing between them.

.. versionadded:: 6.0.0
"""
# "from 2002 to 2004", "2009 through 2023": two bare years joined by "to" or
# "through" are the closed range between them, both ends season numbers - the
# reading "between 2013 and 2018" and "2013-2018" already get. Until Phase 3,
# step 3 neither year was read (ISSUES.md #345): the question's subject
# reading took two years as no season named, the span read no range, and "most
# three pointers from 2002 to 2004" answered the 2026 leaders. Found by the
# Reading's own unread words disagreeing with the claims ledger: deleting
# either year left the other read as the season, so the ledger counted both
# as read while nothing read either. A second year that opens a season span
# ("tmac 2001 to 2001-02") is that span's, not a bare year.
RANGE_YEARS_TO = re.compile(r"\b(?:from\s+)?((?:19|20)\d\d)\s+(?:to|through|thru)\s+((?:19|20)\d\d)\b(?![-/]\d)", re.IGNORECASE)
"""A closed range written as two bare years joined by "to" or "through".

.. versionadded:: 6.0.0
"""
# "2024 and 2025 Knicks record by month" (ISSUES.md #289): two ADJACENT bare
# years joined by "and" are both seasons - the range of the two, as "2024
# 2025" is - while "2020 and 2024" names two seasons that are no range, and
# stays unread rather than read as the three between them.
RANGE_YEARS_AND = re.compile(r"\b((?:19|20)\d\d)\s+and\s+((?:19|20)\d\d)\b", re.IGNORECASE)
"""Two bare years joined by "and" (a range only where they are adjacent).

.. versionadded:: 6.0.0
"""

# "past two seasons", "last 3 years": a relative window counted back from NOW,
# not a games count and not a season named outright. Read as a range's first
# season alone (never a last) because "past N seasons" already ends at the
# current one - the relation's `since` reaches every season from there
# through whatever the table holds, which is exactly "now" since nothing is
# played later. #140: "show tyrese maxey's games against boston in the past
# two seasons" put the "two" in `limit` instead and answered his last 2 games
# of his CAREER, not his last two SEASONS - 7 games measured on
# player_game_log (3 in 2025, 4 in 2026).
PAST_N_SEASONS_COUNT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}  # fmt: skip
"""The count words a relative span of seasons is written with.

.. versionadded:: 6.0.0
"""
_SEASON_COUNT = r"\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten"
N_SEASONS = rf"(?:{_SEASON_COUNT})\s+(?:seasons?|years?)"
"""The fragment "N seasons"/"N years", shared by :data:`PAST_N_SEASONS` and
the subject grammar's history row ("over the past 4 seasons").

.. versionadded:: 6.0.0
"""
PAST_N_SEASONS = re.compile(rf"\b(?:past|last)\s+({_SEASON_COUNT})\s+(?:seasons?|years?)\b", re.IGNORECASE)
"""A relative span of seasons counted back from the current one.

.. versionadded:: 6.0.0
"""

# ---------------------------------------------------------------------------
# The window family (Phase 3, step 2's second slice): which rows a read keeps
# and from which end - "last 10 games", "his first game", "top 5", a count
# of seasons for a history, which end of a team ranking, and the measure a
# ranking of games is ordered by. Read by one tagger, ``query/window.py``.
# ---------------------------------------------------------------------------

# A count of games written in words. Read in code rather than with a package:
# word2number is unmaintained, and text2num's rewrite of the whole question
# would move every other reading. Until these existed the number was read
# three times, and "last twelve games" read as a season line while "last 12
# games" read the log (the package review, 2026-09-27).
_ONES = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_TEENS = ("ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_TENS = ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
COUNT_NUMBERS: dict[str, int] = {
    **{word: n for n, word in enumerate(_ONES, 1)},
    **{word: n for n, word in enumerate(_TEENS, 10)},
    **{word: n * 10 for n, word in enumerate(_TENS, 2)},
    **{f"{ten} {one}": t * 10 + o for t, ten in enumerate(_TENS, 2) for o, one in enumerate(_ONES, 1)},
    "hundred": 100,
    "a hundred": 100,
}
"""A count of games written in words, one through a hundred.

.. versionadded:: 6.0.0
   ``parse._NUMBERS`` until Phase 3, step 2.
"""
COUNT = r"(\d{1,3}|" + "|".join(re.escape(w).replace(r"\ ", r"[\s-]+") for w in sorted(COUNT_NUMBERS, key=len, reverse=True)) + ")"
"""The fragment a count of games is written as: up to three digits, or a
number word (``COUNT_NUMBERS``), the longest first. Shared by the window
grammar and the subject grammar's log row ("last N games").

.. versionadded:: 6.0.0
   ``parse._COUNT`` until Phase 3, step 2.
"""

COUNT_WORD = re.compile(COUNT, re.IGNORECASE)
"""One count, whole (:data:`COUNT` as a pattern of its own).

.. versionadded:: 6.0.0
"""


def count_of(word: str) -> int:
    """The number ``word`` writes - digits, or one of :data:`COUNT_NUMBERS`
    with any spacing or hyphen between its parts ("twenty-five").

    .. versionadded:: 6.0.0
       ``parse._count`` until Phase 3, step 2.
    """
    return int(word) if word.isdigit() else COUNT_NUMBERS[" ".join(word.lower().replace("-", " ").split())]


# The window grammar: the count and the end of the rows a question asks for,
# read from its own words ("last 10 games", "top 5", "his last game") where
# the router's model used to fill them in. Rows are tried in order and the
# first match wins; a row's limit of 0 means "the number in the words".
# Deliberately no "who led the league in ..." -> 1: a ranking with no limit
# already leads with the one asked about and adds "Next: ..." (leaderboard,
# threshold_count, single_game_high), and a limit of 1 cost those answers
# their runners-up (the lead's offline run of the agent, 2026-09-27) - though
# the router's references hold it.
WINDOW_GRAMMAR: tuple[tuple[re.Pattern[str], str | None, int], ...] = (
    (
        re.compile(
            rf"\b(last|past|previous|most recent|latest|final)\s+{COUNT}\s+((home|road|away|regular[- ]season|playoff|postseason)\s+){{0,2}}(games?|outings?|contests?|starts?)\b", re.IGNORECASE
        ),
        "recent",
        0,
    ),
    # A bare count closing the question is games: "magic vs nets last 10".
    (re.compile(rf"\b(last|past|previous)\s+{COUNT}\s*[?.!]*\s*\Z", re.IGNORECASE), "recent", 0),
    (re.compile(rf"\bfirst\s+{COUNT}\s+games?\b", re.IGNORECASE), "first", 0),
    (re.compile(r"\b(last|most recent|latest|final)\s+(regular[- ]season\s+|postseason\s+|playoff\s+)?game\b", re.IGNORECASE), "recent", 1),
    (re.compile(r"\b(first|opening)\s+(regular[- ]season\s+|postseason\s+|playoff\s+)?game\b|\b(season\s+)?opener\b", re.IGNORECASE), "first", 1),
    # "top 5" / "bottom 5" name a count and no end of the span: which end a
    # ranking reads from is its own rank word's, and a games relation reads
    # a bare count as the newest N (``player_relation.relation_window``).
    (re.compile(rf"\b(top|bottom)\s+{COUNT}\b", re.IGNORECASE), None, 0),
)
"""The window grammar: ``(pattern, order, limit)`` rows, the first match
winning; a ``limit`` of 0 is the count the words hold.

.. versionadded:: 5.0.0

.. versionchanged:: 6.0.0
   In the lexicon, compiled (``parse.WINDOW_GRAMMAR`` until Phase 3, step 2).
"""

# Which end of the span a window reads from, said with "games": the two
# forms the router's model emitted (``recent``, ``first``). Tighter than the
# grammar's rows (no venue or season type between the count and "games": "his
# last home game" is a phrasing they miss), which is why a reader of ORDER
# alone - the window on an intent whose reader honors one, filled where the
# grammar read none - uses them, and the words that keep an intent off a
# split ("over the last 7 games" is a log, not a with/without) read them too.
ORDER_WORDS: dict[str, re.Pattern[str]] = {
    "recent": re.compile(r"\b(?:last|latest|previous|most\s+recent)\s+(?:\d+\s+)?games?\b", re.IGNORECASE),
    "first": re.compile(r"\b(?:first|opening|earliest)\s+(?:\d+\s+)?games?\b", re.IGNORECASE),
}
"""Which end of the span a window of games reads from, by name.

.. versionadded:: 2.1.0

.. versionchanged:: 6.0.0
   In the lexicon (``router.ORDER_WORDS`` until Phase 3, step 2).
"""

# One game at one end of the span, named as his: "his last game", "steph
# curry's last regular season game", "the first game of the season". A
# possessive names the subject as often as a pronoun does, and without it
# that question kept a season the model misread (#153). Group 1 is the word
# that says which end.
SINGLE_GAME = re.compile(r"\b(?:his|her|their|the|\w+'s)\s+(last|first|latest|previous|most\s+recent|final|opening|earliest)\s+(?:\w+\s+){0,2}?game\b(?!s)", re.IGNORECASE)
"""One game at one end of the span, named as the subject's own.

.. versionadded:: 6.0.0
   ``router._SINGLE_GAME`` until Phase 3, step 2.
"""

# Which end of a team ranking was asked for. The four are not two pairs: for
# a stat where lower is better, "fewest turnovers" and "worst in turnovers"
# sit at opposite ends, so the reader - which knows the stat - resolves them.
# Tried in this order, the first match winning.
RANK_WORDS: tuple[tuple[Literal["most", "fewest", "best", "worst"], re.Pattern[str]], ...] = (
    ("worst", re.compile(r"\bworst\b", re.IGNORECASE)),
    ("best", re.compile(r"\bbest\b", re.IGNORECASE)),
    # "slowest pace" is the fewest possessions, "fastest" the most - without
    # these, "slowest pace" listed the fastest teams first.
    ("fewest", re.compile(r"\b(?:fewest|least|lowest|slowest)\b", re.IGNORECASE)),
    # "most recent" is the window's end, never the ranking's: "the first
    # quarter scores for the Sixers' most recent 10 games" answered their
    # highest first quarter of the ten.
    ("most", re.compile(r"\b(?:most(?!\s+recent\b)|highest|top|leads?|leaders?|fastest)\b", re.IGNORECASE)),
)
"""Which end of a team ranking was asked for, by name, in the order tried.

.. versionadded:: 6.0.0
   ``router.RANK_WORDS`` until Phase 3, step 2.
"""

# A ranking of the GAMES that satisfy a yes/no stat (a triple-double, a
# double-double, fouling out) by another measure: "players with the highest
# scoring triple doubles" (yardstick-v2 F124) carries the SAME names and stat
# as "most triple doubles", which a count per player answers rightly. The
# word that tells the two apart ("scoring", "biggest") is the window's
# ``by`` - the measure the qualifying games are ranked by - and the measure
# word itself is RANKED_BY_WORD's, points where none is named.
RANKED_BOOLEAN_GAMES = re.compile(
    r"\b(?:highest[- ]scoring|biggest|largest|best[- ]scoring)\b|\b(?:most|highest|fewest|lowest)\s+(?:points?|rebounds?|assists?|steals?|blocks?|minutes?)\s+in\s+(?:a|an|any|one)\b",
    re.IGNORECASE,
)
"""The games over a yes/no stat ranked by another measure, named.

.. versionadded:: 6.0.0
   ``router._RANKED_BOOLEAN_GAMES`` until Phase 3, step 2.
"""
RANKED_BY_WORD = re.compile(r"\b(scoring|points?|rebounds?|assists?|steals?|blocks?|minutes?)\b", re.IGNORECASE)
"""The measure a ranking of games is ordered by, as the question words it.

.. versionadded:: 6.0.0
   ``router._RANKED_BY_WORD`` until Phase 3, step 2.
"""

# The window family's guard words, read by the stages that choose an intent
# (never a value): "games" or "last" beside a player's name is his log and
# not his line; "who ... the most", "top 10", "leaders" asked with no player
# named is the league's ranking; "most"/"highest"/"top"/"best" beside a
# quarter ranks players unless a team is named; a log word or a window
# ("last 10") beside two teams is one team's games, not their meetings.
GAMES_WORDS = re.compile(r"\bgames?\b|\blast\b", re.IGNORECASE)
"""A player's games rather than his line: "games", or "last".

.. versionadded:: 6.0.0
   ``router._GAMES_WORDS`` until Phase 3, step 2.
"""
WHO_RANKS = re.compile(r"\bwho\b.{0,30}\b(?:most|fewest|highest|lowest|best|worst|leads?|led)\b|\btop\s+\d+\b|\bleaders?\b", re.IGNORECASE)
"""A ranking asked of the league - "who attempted the most", "who leads", "top 10".

.. versionadded:: 6.0.0
   ``router._WHO_RANKS`` until Phase 3, step 2.
"""
PERIOD_TOP = re.compile(r"\b(?:most|highest|top|best)\b", re.IGNORECASE)
"""A rank word beside a quarter or half: players ranked by it, unless a
team is named ("the Pistons' most points in a first half" is the team's
single best half).

.. versionadded:: 6.0.0
   ``router._PERIOD_TOP`` until Phase 3, step 2.
"""
LAST_WORD = re.compile(r"\blast\b", re.IGNORECASE)
"""The bare word "last": the one guard of this family the span tagger reads
("last 8 games vs pistons" is every meeting across the seasons, whether or
not a count was read beside it).

.. versionadded:: 6.0.0
   ``span._LAST`` until the window's slice.
"""
LOG_OR_WINDOW_WORDS = re.compile(r"\b(log|gamelog|game log|last \d+|past \d+|first \d+)\b", re.IGNORECASE)
"""A log word or a window beside two teams: one team's games, not their meetings.

.. versionadded:: 6.0.0
   ``parse._LOG_OR_WINDOW_WORDS`` until Phase 3, step 2.
"""


# ---------------------------------------------------------------------------
# The games' cuts (Phase 3, step 2's third slice): which games of a span a
# read sees - a venue, one calendar day, a circumstance (a weekday, a month,
# a holiday, "since <day>", the conference or division the opponent is in,
# or words nothing narrows by), a playoff round, a game of a series, an
# ordinal season. Read by one tagger, ``query/cuts.py``; the opponent and
# the tenure are the subject reading's words (``subject.py``'s "vs" and
# "for" readers), not this family's. The calendar module's own readers of a
# situation value (``calendar.parse_situation``, ``parse_alignment``,
# ``bare_month``) read the patterns below too, so the words the tagger
# captures and the words the relations narrow by cannot drift apart.
# ---------------------------------------------------------------------------

# The one table of month words: the number each names, keyed by its first
# three letters, which every spelling read here shares ("sept" included).
# Until this slice the months were written out in the router (twice) and in
# the calendar module (ISSUES.md #244); the sayer's capitalized names stay
# `season_text.MONTH_NAMES`.
MONTH_NUMBERS: dict[str, int] = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
"""Each month's number, by the first three letters of its name.

.. versionadded:: 6.0.0
"""
#: A month written in full or abbreviated ("march", "mar", "sept"), as the
#: group ``month`` of the date patterns.
_MONTH_WORD = r"(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
#: A month written in full, as the situation readers take it.
_MONTH_NAME = r"(?P<month>january|february|march|april|may|june|july|august|september|october|november|december)"
#: The season's months, in full: the ones "in <month>" is read for.
_SEASON_MONTHS = r"october|november|december|january|february|march|april|may|june"


def month_number(word: str) -> int:
    """The number of the month ``word`` names, however it was spelled
    ("March", "mar", "Sept.").

    .. versionadded:: 6.0.0
    """
    return MONTH_NUMBERS[word[:3].lower()]


# A calendar day written the way people write it: "march 17", "Jan 19",
# "november 11 2019". The leading group is what makes a date a RANGE rather
# than a day - "since January 31st" starts a window and names no single game -
# and those are a `situation`, which the calendar reading narrows by. A range
# opened on a date WITH a year is also where the span's range starts (the
# cuts tagger hands the year to the span's).
CALENDAR_DATE = re.compile(
    r"(?P<range>\b(?:since|after|before|from|through|until)\s+(?:the\s+)?)?"
    + r"\b"
    + _MONTH_WORD
    + r"\.?\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<year>(?:19|20)\d\d))?\b",  # codespell:ignore nd - an ordinal suffix
    re.IGNORECASE,
)
"""A calendar day, or a range opened on one.

.. versionadded:: 6.0.0
"""
# The same range written in numbers: "since 1/26/20", "from 12/25/2019".
# Only after a range word, so a shooting line ("7/14") or the 50/40/90 club is
# never a date; it becomes a `situation`, which the calendar reading narrows by
# (`calendar.parse_situation`) or refuses by value - never a narrowing dropped.
# yardstick-v2 F110, "towns home rec including playoffs since 1/26/20 vs spurs",
# read without it answered his whole career at home against San Antonio.
NUMERIC_DATE_RANGE = re.compile(r"\b(?:since|after|from)\s+(?:the\s+)?(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])(?:/(?P<year>(?:19|20)?\d\d))?\b", re.IGNORECASE)
"""A range opened on a date written in numbers.

.. versionadded:: 6.0.0
"""

# Where a game was played. "Far away" and "fade away" are shot descriptions,
# not venues - "How far away does Wembanyama shoot from?" is a routing case.
# Both at once is a SPLIT ("home and away splits"), not a cut, so the tagger
# sets nothing then.
VENUE_HOME = re.compile(r"\bhome\b(?!\s+runs?)", re.IGNORECASE)
"""The games at home.

.. versionadded:: 6.0.0
   ``router._HOME`` until Phase 3, step 2.
"""
VENUE_AWAY = re.compile(r"(?<!far )(?<!fade )\b(?:away|road)\b", re.IGNORECASE)
"""The games on the road.

.. versionadded:: 6.0.0
   ``router._AWAY`` until Phase 3, step 2.
"""

# One round of the postseason. No table carries a round, so no reader can
# narrow to one: "tatum stats in the 2024 finals" was answered with his whole
# 2024 postseason, 19 games where the Finals were 5. A cut, so every reader
# refuses it rather than widening the question.
ROUND_WORDS = re.compile(r"\bfinals\b|\b(?:first|second)\s+round\b|\bsemi-?finals?\b", re.IGNORECASE)
"""A playoff round, as worded.

.. versionadded:: 6.0.0
   ``router._ROUND_WORDS`` until Phase 3, step 2.
"""

# One game of a playoff series, by number: "game 4", "game 7s". Read as a
# number rather than left in the situation (where "Ayton stats in game 4
# playoff games" refused), because the relation can find it - the nth game by
# date between two teams in one postseason (Narrowed.narrow_series_game).
# "game 7" used to be a round; it is a game like the others.
SERIES_GAME = re.compile(r"\bgame\s+([1-7])s?\b", re.IGNORECASE)
"""One game of each playoff series, by its number.

.. versionadded:: 6.0.0
   ``router._GAME_N`` until Phase 3, step 2.
"""

# A season named by ordinal: "his 18th season", "15th season played". The
# model read the ordinal as a year - "his 18th season" came back as season
# 2018, with LeBron dropped entirely, and the answer was the 2018 league
# leaderboard - so a year the question itself does not name goes with it.
# Which year the ordinal IS needs the player, so the relation settles it after
# resolving him (player_relation.settle_ordinal_season).
ORDINAL_SEASON = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+season\b", re.IGNORECASE)  # codespell:ignore nd - an ordinal suffix
"""A season named by its place in a career.

.. versionadded:: 6.0.0
   ``router._SEASON_N`` until Phase 3, step 2.
"""

# One row per holiday a question can name as a day: its label, the kind and
# value of the narrowing it is (`calendar.CalendarNarrowing`'s: a fixed
# `day` as (month, day), or the `nth_weekday` of a month as (month, ISO
# weekday, n) for one that moves), and every spelling read as it. The
# calendar module builds its `HOLIDAYS` table from this, and the situation
# pattern below captures every spelling, so a spelling added here is one the
# tagger captures and the relations read.
HOLIDAY_SPELLINGS: tuple[tuple[str, str, tuple[int, ...], tuple[str, ...]], ...] = (
    ("Christmas Day", "day", (12, 25), ("christmas", "christmas day", "xmas")),
    # Its own day, not Christmas: "christmas eve" was captured as "christmas"
    # and answered December 25 (#238).
    ("Christmas Eve", "day", (12, 24), ("christmas eve", "xmas eve")),
    ("New Year's Day", "day", (1, 1), ("new year's", "new years", "new year's day", "new years day")),
    ("New Year's Eve", "day", (12, 31), ("new year's eve", "new years eve")),
    ("Halloween", "day", (10, 31), ("halloween",)),
    ("Valentine's Day", "day", (2, 14), ("valentine's day", "valentines day")),
    # The third Monday of January, not January 15: that is MLK Day in 5 of
    # the 33 seasons 1994-2026 (2026's was January 19), and "on mlk day"
    # answered every January 15 (#238). A federal holiday from 1986, before
    # the first game on record. "martin luther king" alone is read too, so
    # "martin luther king jr. day" is MLK Day and not a narrowing dropped.
    ("MLK Day", "nth_weekday", (1, 1, 3), ("mlk day", "martin luther king day", "martin luther king")),
    # The fourth Thursday of November - it moves the way MLK Day does, and
    # was refused only while no moving date could be stated.
    ("Thanksgiving", "nth_weekday", (11, 4, 4), ("thanksgiving", "thanksgiving day")),
)
"""Every holiday a question can name as a day: its label, the kind and value
of the narrowing it is, and every spelling read as it (lowercase, straight
apostrophes).

.. versionadded:: 6.0.0
   ``calendar._HOLIDAY_SPELLINGS`` until Phase 3, step 2.
"""
UNREAD_HOLIDAYS: tuple[str, ...] = ("easter",)
"""Holidays a question can name that no day is read for. The tagger
captures them all the same (:data:`HOLIDAY_WORDS`), so the answer refuses
the holiday by name rather than answering the season it was asked to
narrow. Easter follows the church calendar (the Gregorian computus), which
no narrowing here states.

.. versionadded:: 6.0.0
   ``calendar.UNREAD_HOLIDAYS`` until Phase 3, step 2.
"""


def _holiday_words_spelling(spelling: str) -> str:
    """One holiday spelling as a regex: any run of spaces between its words."""
    return r"\s+".join(re.escape(word) for word in spelling.split())


HOLIDAY_WORDS: str = "|".join(
    _holiday_words_spelling(name) for name in sorted({*(spelling for _, _, _, spellings in HOLIDAY_SPELLINGS for spelling in spellings), *UNREAD_HOLIDAYS}, key=lambda name: (-len(name), name))
)
"""Every holiday spelling read (:data:`HOLIDAY_SPELLINGS`) or refused
(:data:`UNREAD_HOLIDAYS`), as one regex alternation (no group of its own),
longest first so "christmas eve" is not read as "christmas". The situation
pattern is built from it, so the words the tagger captures and the words
the calendar reads cannot drift apart: "valentine's day" was a key the
router's own list never captured, and so narrowed nothing (#238).

.. versionadded:: 6.0.0
   ``calendar.HOLIDAY_WORDS`` until Phase 3, step 2.
"""

#: The conference and division words a situation names the OPPONENT to be
#: in, mapped to ``(kind, value)`` - ``value`` the way
#: ``fetch.parse.parse_team_alignment`` stores it ("Eastern Conference",
#: "Southeast"). ``midwest`` answers a season before the 2004-05 realignment
#: split it into three; asked of a later season it narrows to opponents
#: nobody was ever aligned under, which is a real (empty) answer and not an
#: error - the same as naming a division a team never played in.
ALIGNMENT_NAMES: dict[str, tuple[str, str]] = {
    "east": ("conference", "Eastern Conference"),
    "eastern": ("conference", "Eastern Conference"),
    "west": ("conference", "Western Conference"),
    "western": ("conference", "Western Conference"),
    "atlantic": ("division", "Atlantic"),
    "central": ("division", "Central"),
    "southeast": ("division", "Southeast"),
    "northwest": ("division", "Northwest"),
    "southwest": ("division", "Southwest"),
    "pacific": ("division", "Pacific"),
    "midwest": ("division", "Midwest"),
}
"""Each conference or division word, to the kind and the stored name.

.. versionadded:: 6.0.0
   ``calendar._ALIGNMENT_NAMES`` until Phase 3, step 2.
"""
_ALIGNMENT_WORD = "|".join(sorted(ALIGNMENT_NAMES, key=len, reverse=True))
# Deliberately narrow, the same way the holidays are: only the fixed
# vocabulary above, with an optional leading "vs"/"against"/"in", an optional
# "the", and an optional trailing "conference"/"division"/"team(s)" - never a
# name pulled from free text, since a conference or division is a closed set
# of eleven words and nothing here guesses at a twelfth.
ALIGNMENT = re.compile(rf"^(?:(?:vs\.?|against|in)\s+)?(?:the\s+)?(?P<name>{_ALIGNMENT_WORD})(?:\s+(?:conference|division))?(?:\s+teams?)?$", re.IGNORECASE)
"""A whole situation value naming the opponent's conference or division.

.. versionadded:: 6.0.0
   ``calendar._ALIGNMENT`` until Phase 3, step 2.
"""

# The words after a subject that name a circumstance, filed in one
# `situation` value - "tuesdays", "in october", "christmas", "since january
# 31st" - alongside things no game table can filter on ("18 year old",
# "since returning"). Setting it is enough on its own: a reader whose words
# do not state it steps aside, the planner refuses what a relation cannot
# honor, and a value the calendar cannot read is refused by the relation BY
# VALUE - the ranking AGENTS.md sets: a refusal beats a fluent wrong answer.
# Measured against 343 real questions when it was written (the 261-query feed
# plus the 83 routing corpus cases): 14 feed queries match and no corpus case
# does, so no question that routed correctly started refusing.
SITUATION = re.compile(
    r"\bback[- ]to[- ]backs?\b|\bb2bs?\b|\bsecond\s+night\b|\bovertime\b|"
    # "in the month of march" as well as "in march" (F096) - the calendar
    # reader (SITUATION_MONTH) already takes both.
    rf"\bin\s+(?:the\s+month\s+of\s+)?(?:{_SEASON_MONTHS})\b|"
    # A conference or division, kept WITH its name and its "vs"/"against"/"in"
    # so `calendar.parse_alignment` reads it whole: "vs southeast division"
    # used to be captured as the word "division" alone (#213), and the
    # relation - which answers the phrase - refused the bare word. The bare
    # forms stay as the last resort, still refused honestly by name.
    r"\b(?:vs\.?|against|in)\s+(?:the\s+)?(?:east(?:ern)?|west(?:ern)?|atlantic|central|southeast|northwest|southwest|pacific|midwest)(?:\s+(?:conference|division))?(?:\s+teams?)?\b|"
    r"\b(?:atlantic|central|southeast|northwest|southwest|pacific|midwest)\s+division\b|"
    r"\b(?:east(?:ern)?|west(?:ern)?)\s+conference\b|\bdivision\b|\ball[- ]star\s+break\b|"
    # A day of the week: 8 of the 14, and the most common shape in the feed.
    r"\b(?:mon|tues|wednes|thurs|fri|satur|sun)days?\b|"
    # A calendar holiday. "on christmas" answered with a whole season average.
    rf"\b(?:{HOLIDAY_WORDS})\b|"
    # An age. "most triple doubles before turning 27" answered with this
    # season's triple-double leaders - `players` holds no birth date at all
    # (DATA.md), so this one cannot be answered even in principle.
    r"\b(?:before|after|by)\s+(?:turning|age)\s+\d+\b|\bat\s+age\s+\d+\b|\b\d+\s+years?\s+old\b|"
    # A minutes condition used to be here ("paul reed gamelog with 25 minutes"
    # returned his most recent game); it is a line (`below`/`above`) now, a
    # slot the relation filters on.
    # A window defined by an event rather than a date.
    r"\bsince\s+(?:returning|coming\s+back|his\s+return|the\s+all[- ]star\s+break)\b|\bsince\s+(?:his\s+)?injury\b|\bafter\s+returning\b|"
    # A calendar day is NOT here: the tagger resolves it to a real date
    # (CALENDAR_DATE) and `game_log` then answers the game that was asked
    # about. What is left here is the date this project cannot turn into one
    # day - a window opened by "since March 1", and a date in a career
    # question, which spans twenty Octobers and so fixes no year. Both refuse.
    r"\b(?:since|after|before|from|through|until)\s+(?:the\s+)?(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+\d{1,2}"
    r"(?:st|nd|rd|th)?\b",  # codespell:ignore nd - an ordinal suffix
    # A season named by ordinal ("his 18th season") used to be here; it is
    # ORDINAL_SEASON now, settled to a year once the player is known.
    re.IGNORECASE,
)
"""A circumstance the games are under, as the question worded it.

.. versionadded:: 6.0.0
   ``router._SITUATION`` until Phase 3, step 2.
"""

# The calendar module's readers of a situation VALUE, each anchored to the
# whole value: a weekday, a month ("in march", "the month of march"), every
# game from a day of the season on, the same in numbers, and a bare month
# (the one shape a team's record reads off the standings by month).
SITUATION_WEEKDAY = re.compile(r"^(?:on\s+)?(?P<day>monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?$", re.IGNORECASE)
"""A situation value that is a weekday.

.. versionadded:: 6.0.0
   ``calendar._WEEKDAY`` until Phase 3, step 2.
"""
SITUATION_MONTH = re.compile(rf"^(?:in\s+)?(?:the\s+month\s+of\s+)?{_MONTH_NAME}$", re.IGNORECASE)
"""A situation value that is a month.

.. versionadded:: 6.0.0
   ``calendar._IN_MONTH`` until Phase 3, step 2.
"""
SITUATION_SINCE_DAY = re.compile(rf"^(?:since|from|after)\s+(?:the\s+)?{_MONTH_NAME}\s+(?P<num>\d{{1,2}})(?:st|nd|rd|th)?$", re.IGNORECASE)  # codespell:ignore nd - an ordinal suffix
"""A situation value opening a range on a day of each season.

.. versionadded:: 6.0.0
   ``calendar._SINCE_DAY`` until Phase 3, step 2.
"""
SITUATION_SINCE_NUMERIC = re.compile(r"^(?:since|from|after)\s+(?:the\s+)?(?P<date>\d{1,2}/\d{1,2}(?:/\d{2}(?:\d{2})?)?)$", re.IGNORECASE)
"""A situation value opening a range on a date written in numbers.

.. versionadded:: 6.0.0
   ``calendar._SINCE_NUMERIC`` until Phase 3, step 2.
"""
BARE_MONTH = re.compile(rf"^in {_MONTH_NAME}$", re.IGNORECASE)
"""A situation value of exactly "in <month>" - anchored to that shape, so
"since january 31st" (a window) is not mistaken for one.

.. versionadded:: 6.0.0
   ``calendar._BARE_MONTH`` until Phase 3, step 2.
"""

# Conference and division words where a TEAM belongs - the team or opponent
# slot. As an opponent narrowing ("vs the west") they are read above
# (ALIGNMENT, over `team_alignment`); as the team a record or a line is
# about ("who leads the east"), nothing reads a conference's own standings
# or leaders yet (ISSUES.md #25). So a team slot naming one is refused by
# name; resolved as a team it would match nothing and be refused for the
# wrong cause.
CONFERENCE_WORDS = re.compile(r"\b(?:conferences?|divisions?|east(?:ern)?|west(?:ern)?|atlantic|central|southeast|northwest|pacific|southwest)\b", re.IGNORECASE)
"""A conference or division word in a team's name slot.

.. versionadded:: 6.0.0
   ``calendar._CONFERENCE_WORDS`` until Phase 3, step 2.
"""
# How a situation nothing reads is told apart for the refusal's facts
# (`reading.Cause("non_calendar_situation")`, its `reads_as`): an age, or a
# conference or division phrase in a shape the alignment reader does not
# take ("the Central Division these days").
AGE_WORDS = re.compile(r"\b(?:\d+\s+years?\s+old|(?:before|after|by|at)\s+(?:turning|age)\s+\d+|age\s+\d+)\b", re.IGNORECASE)
"""An age, which no table holds a birth date for.

.. versionadded:: 6.0.0
   ``parse._AGE`` until Phase 3, step 2.
"""
CONFERENCE_OR_DIVISION = re.compile(r"\b(?:east(?:ern)?|west(?:ern)?|conference|division|atlantic|central|southeast|northwest|pacific|southwest)\b", re.IGNORECASE)
"""A conference or division word anywhere in a situation.

.. versionadded:: 6.0.0
   ``parse._CONFERENCE_OR_DIVISION`` until Phase 3, step 2.
"""
NAME_LIST_SPLIT = re.compile(r",|\band\b|&")
"""Where a list of names written as one string splits ("Anthony Black,
Franz Wagner"): the cuts tagger reads an opponent that is the absent
teammates again by it.

.. versionadded:: 6.0.0
"""


def _in_range(year: int) -> int | None:
    return year if MIN_SEASON <= year <= current_season() + 1 else None


@dataclass(frozen=True)
class SeasonSpan:
    """One season a question writes as the two calendar years it spans, or
    several written as their first and last, found by :func:`season_spans`.
    For one season ``first`` is the year it starts in and ``season`` the one
    it ends in, this project's number for it - "2023-24" and "23/24" are
    both ``first`` 2023, ``season`` 2024. For a range (``is_range``: the
    second year does not follow the first) both are season numbers, its
    first and its last, the way "2020-2024" has always been read - "2015-18"
    is ``first`` 2015, ``season`` 2018, and "00-02" 2000 and 2002. ``start``
    and ``end`` are where the span sits in the text, for a reader that needs
    what is beside it: "to" between two of them, "since" before one.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       In the lexicon (``season_text.SeasonSpan`` until Phase 3, step 2).
    """

    start: int
    end: int
    first: int
    season: int
    is_range: bool = False


def season_spans(text: str) -> list[SeasonSpan]:
    """Every season ``text`` writes as a span, in the order it writes them.

    Where the second year follows the first, the span is one season, named
    for the year it ends in: "2023-24", "2023-2024", "1999-00", and the
    short "23-24" and "23/24". A short one is a season only in the century
    that makes it one the league could have played - "25-26" is 2025-26,
    "95-96" is 1995-96, and "30-31" is none - and, written with a slash,
    only where it is not also a month and its day. "12/13" is December 13
    to the router, which reads "since 12/13" as that date
    (:data:`NUMERIC_DATE_RANGE`), and a season read from the same
    characters would answer another year's games.

    Where it does not follow, the span is a range of seasons
    (``is_range``), both ends read as season numbers the way "2020-2024"
    always was: "2015-18" is 2015 through 2018, and "00-02" 2000 through
    2002 - where "2015-18" read as 2018 alone answered one postseason for
    "curry playoff stats 2015-18", and "00-02" read as nothing answered the
    current season (ISSUES.md #261). A range must end in the league's
    seasons, and read no date's pieces ("2001-03-15"). A short one is joined
    by a hyphen and ascends as written: a pair that descends is a line or a
    record ("Lebron 21-13", "the 67-15 lakers"), which reading the century
    backwards would make a range of fifty seasons, and a slash is a date or
    a shooting line. An ascending record or line ("10-12") reads as a range
    - measured over the 3,084 distinct research-corpus questions, the 4
    short pairs that ascend and do not follow are all ranges ("kobe bryant
    00-02") - and the answer states the seasons it read. A four-digit span
    whose years do not follow and that is no range ("2015-12") is kept as
    the season it ends in, for the caller to refuse, as any year outside the
    league's is.

    The one definition of a season span: :func:`season_from_text` reads the
    first, and the span tagger's ranges read theirs with it ("02-03 to
    06-07", "since 2000-01", "2015-18"), so a span reads the same wherever a
    question writes it.

    .. versionadded:: 5.0.0

    .. versionchanged:: 6.0.0
       In the lexicon (``season_text.season_spans`` until Phase 3, step 2).
    """
    spans = []
    for match in SEASON_SPAN.finditer(text):
        years = _season_spans_years(match)
        if years is not None:
            spans.append(SeasonSpan(match.start(), match.end(), *years))
    return spans


def _season_spans_years(match: re.Match[str]) -> tuple[int, int, bool] | None:
    """The first year and the season of one :data:`SEASON_SPAN` match, and
    whether it is a range, or None where a short pair is neither a season
    nor a range (:func:`season_spans`)."""
    if match.group("start") is not None:
        first, end = int(match.group("start")), match.group("end")
        if len(end) == 4:
            # Two years written out are a range in either order, as the
            # router's "2020-2024" reader took them; a year outside the
            # league's is the caller's to refuse, as it always was.
            last = int(end)
            return (min(first, last), max(first, last), True) if abs(last - first) > 1 else (first, last, False)
        ending = first // 100 * 100 + int(end)
        ending = ending + 100 if ending <= first else ending  # "1999-00" turns the century
        return first, ending, ending != first + 1 and _season_spans_is_range(match, first, ending)
    short_first, short_end = int(match.group("short_start")), int(match.group("short_end"))
    first = 2000 + short_first if 2000 + short_first <= current_season() else 1900 + short_first
    if (short_end - short_first) % 100 != 1:
        last = first // 100 * 100 + short_end
        ascends = match.group("separator") == "-" and short_end > short_first
        return (first, last, True) if ascends and _season_spans_is_range(match, first, last) else None
    if match.group("separator") == "/" and 1 <= short_first <= 12:
        return None
    return (first, first + 1, False) if _in_range(first + 1) is not None else None


# A span with a date's next piece after it ("2001-03-15") is a date.
_SEASON_SPANS_DATE_TAIL = re.compile(r"[-/]\d")


def _season_spans_is_range(match: re.Match[str], first: int, last: int) -> bool:
    """Whether a span whose years do not follow is a range of seasons: it
    ascends, both ends are the league's, and no date's pieces run on."""
    return first < last and _in_range(first) is not None and _in_range(last) is not None and not _SEASON_SPANS_DATE_TAIL.match(match.string, match.end())


def season_from_text(text: str) -> int | None:
    """The season a question names, or None if it names none (or names more
    than one - "compare 2023 and 2024" is not this function's call to make).
    :func:`season_named` reads the same, with the characters it read.

    .. versionchanged:: 5.0.0
       Reads a season written two digits a side, "23-24" and "23/24"
       (:func:`season_spans`).

    .. versionchanged:: 6.0.0
       In the lexicon (``season_text.season_from_text`` until Phase 3, step 2).
    """
    named = season_named(text)
    return None if named is None else named[0]


def season_named(text: str) -> tuple[int, tuple[tuple[int, int], ...]] | None:
    """The season :func:`season_from_text` reads, with the characters it
    read it from: one span, every occurrence of the one year, or the
    relative words. None where the words name no season, or two.

    .. versionadded:: 6.0.0
    """
    low = text.lower()

    spans = season_spans(low)
    if spans:
        season = _in_range(spans[0].season)
        return None if season is None else (season, ((spans[0].start, spans[0].end),))

    years = [(y, m) for m in YEAR.finditer(low) for y in (_in_range(int(m.group(1))),) if y is not None]
    if len({y for y, _ in years}) == 1:
        return years[0][0], tuple((m.start(), m.end()) for _, m in years)
    if years:
        return None

    previous = PREVIOUS_SEASON.search(low)
    if previous:
        return current_season() - 1, ((previous.start(), previous.end()),)
    current = CURRENT_SEASON.search(low)
    if current:
        return current_season(), ((current.start(), current.end()),)
    return None


# ---------------------------------------------------------------------------
# The period family (Phase 3, step 2's fourth slice): what a read SEES of
# each game - one quarter or one half - read by one tagger,
# ``query/period.py`` (``which_period`` names the one, ``read_period`` keeps
# it under a reader that takes one). The rest of the family's words choose
# the period intents (``router._route_period_intents``: a quarter or half
# named at all, a ranking of players by one, "by quarter" as a breakdown,
# a period as a CONDITION on which games count) or name a cause
# (``parse._unsupported_period_as_condition``), and read from here too, so
# the family's words are declared once. A period used as a condition
# ("after making one three in the first quarter") is the line slice's
# value (``parse.read_period_condition``), read by PERIOD_CONDITION below
# and a `which_period` of its period.
# ---------------------------------------------------------------------------

ORDINAL_PERIODS: dict[str, int] = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}
"""The ordinal a quarter or half is named by, to its number.

.. versionadded:: 6.0.0
   ``router._ORDINAL_PERIODS`` until Phase 3, step 2.
"""

# Which quarter or half, in the forms questions actually use. All three
# shapes come from the feed: "1st quarter", "q1"/"1q", and "first half"/"2h"
# - and the hyphenated adjective, "first-quarter rebounds", which read no
# period. A half is never a quarter: the model mapped "first half" onto
# period 1, which is wrong for a team the same way it is for a player, so
# the half is read first and apart (``period.which_period``).
WHICH_QUARTER = re.compile(
    r"\b(?P<ordinal>first|second|third|fourth|1st|2nd|3rd|4th)[\s-]+(?:quarter|qtr|q)\b|\bq(?P<qn>[1-4])\b|\b(?P<nq>[1-4])q\b",
    re.IGNORECASE,
)
"""Which quarter the words name, by ordinal or by number.

.. versionadded:: 6.0.0
   ``router._WHICH_QUARTER`` until Phase 3, step 2.
"""
WHICH_HALF = re.compile(r"\b(?P<ordinal>first|second|1st|2nd)[\s-]+half\b|\b(?P<hn>[12])h\b", re.IGNORECASE)
"""Which half the words name, by ordinal or by number.

.. versionadded:: 6.0.0
   ``router._WHICH_HALF`` until Phase 3, step 2.
"""

# The words that name a quarter at all, which the intent stage reads
# (period_split, period_leaderboard, team_quarter_points). Called
# `_AGENT_ONLY` until 5.0.0, for the tool-calling agent a quarter question
# was once sent to. `[1-4]q` is the mirror of `q[1-4]` and was missing:
# "Duncan Robison 1q log" and "Devin Vassell nba player per game stats 1q"
# were both answered with a whole-game line in the 2026-09-15 feed replay -
# the same shape as the "4th qtr" gap that made these patterns grow
# abbreviations in the first place. `td3s` used to be here too, sending
# "luka td3s home" to the agent because nothing counted one player's
# triple-doubles; the compiler does now, and the stages read the word.
QUARTER_WORDS = re.compile(r"\b(?:first|second|third|fourth|1st|2nd|3rd|4th)[\s-]+(?:quarter|qtr|q)\b|\bq[1-4]\b|\b[1-4]q\b|\bqtrs?\b|\bper\s+quarter\b|\bby\s+quarter\b|\b(?:each|every)\s+quarter\b")
"""A quarter named at all - one, or every one ("by quarter"). Read over the
lowercased question, as the stage reads it.

.. versionadded:: 6.0.0
   ``router._QUARTER_WORDS`` until Phase 3, step 2.
"""
# A half is never a quarter. team_quarter_points reads a period number and
# the model mapped "first half" onto period 1, which is wrong for a TEAM the
# same way it would be for a player - so half words are kept apart from
# QUARTER_WORDS, whose team exemption applies to quarters only, and always
# route through the intent stage's override. "rj barrett 4th qtr log" is
# why both patterns grew abbreviations - it slipped past "quarter" and
# game_log answered with his whole last game.
HALF_WORDS = re.compile(r"\b(?:first|second|1st|2nd)[\s-]+half\b|\b[12]h\b|\bhalftime\b", re.IGNORECASE)
"""A half named at all.

.. versionadded:: 6.0.0
   ``router._HALF_WORDS`` until Phase 3, step 2.
"""

# A quarter or half question that ranks PLAYERS rather than asking about
# one: "who has the highest average 1st quarter points this season?",
# "knicks 1st quarter scoring leaders playoffs". Before `period_leaderboard`
# existed these reached `other` and fell through. This wins over the team
# exemption, and has to: the Knicks question routes to team_quarter_points
# with the team filled and no player, which is exactly the shape the
# exemption protects - and answering it would give the TEAM's first quarter
# where its players' were asked for. "player" alone ranks too (yardstick-v2
# F049, "hornets average 1st quarter points player": the team's own quarter
# answered where its best player's was asked). Only ever read where no
# player is named - a named player's quarter is period_split before this is
# looked at - so "player" cannot pull a named player's question into a
# ranking.
PERIOD_LEADERS = re.compile(r"\bleaders?\b|\bwho\b|\bwhich\s+player\b|\bleading\s+scorers?\b|\bplayers?\b", re.IGNORECASE)
"""Players ranked by a quarter or half, rather than one asked about.

.. versionadded:: 6.0.0
   ``router._PERIOD_LEADERS`` until Phase 3, step 2.
"""
# A named player's period "games" with no stat named is his games listed,
# the way "log" is (yardstick-v2 F060, "Rudy gobert first half games this
# season": the key lists his 76 first halves, and a total answered it).
PERIOD_GAMES_WORDS = re.compile(r"\bgames\b", re.IGNORECASE)
"""A named player's period games, listed.

.. versionadded:: 6.0.0
   ``router._PERIOD_GAMES_WORDS`` until Phase 3, step 2.
"""
# A period used as a CONDITION on the games rather than the part of each
# game measured: "vj edgecombe three points made per game after making one
# three in first quarter" (yardstick-v2 F062) asks his WHOLE-game threes
# over the games whose first quarter held one. Once period_split read any
# stat, it answered his first-quarter threes instead - fluent, and a
# different question. A period as a condition is the line slice's value;
# a wording it cannot read is kept off the period shapes and refused by
# name (``parse._unsupported_period_as_condition``).
PERIOD_AS_CONDITION = re.compile(
    r"\bafter\s+(?:making|scoring|hitting|getting|having|recording|grabbing)\b|\bin\s+games?\s+(?:where|when|in\s+which)\b|\bif\s+(?:he|she|they)\b",
    re.IGNORECASE,
)
"""The words that make a period a condition on which games count.

.. versionadded:: 6.0.0
   ``router._PERIOD_AS_CONDITION`` until Phase 3, step 2.
"""
# Every quarter at once, side by side (yardstick-v2 F048, "nba playerspoints
# by quarter average", which fell through for want of one period).
# period_leaderboard with no period is the league's table; period_split with
# no period is a NAMED player's (#162, ROADMAP step 2). A "by quarter"
# question reads NO period value: the breakdown is the point's shape.
BY_QUARTER = re.compile(r"\b(?:by|per|each|every)\s+(?:quarter|qtr)s?\b|\bquarter\s+by\s+quarter\b", re.IGNORECASE)
"""Every quarter at once - a breakdown, which names no one period.

.. versionadded:: 6.0.0
   ``router._BY_QUARTER`` until Phase 3, step 2.
"""
PERIOD_WORD = re.compile(r"\b(?:1st|2nd|3rd|4th|first|second|third|fourth)\s+(?:quarter|half)\b|\bq[1-4]\b|\b[1-4]q\b|\b[12]h\b|\b(?:quarter|half)\b", re.IGNORECASE)
"""A quarter or half word anywhere, in any form - the ``period_as_condition``
cause's second half, beside :data:`PERIOD_AS_CONDITION`.

.. versionadded:: 6.0.0
   ``parse._PERIOD_WORD`` until Phase 3, step 2.
"""
# The point reader's guard: a league-wide or a team's read that names a
# quarter, a half, an overtime or a period is the period relation's
# question, not the compiler's own - "least points by the Wizards in the
# first half" once ranked players by their whole game.
PERIOD_GUARD = re.compile(r"\b(quarter|qtr|half|period|overtime|\d(?:st|nd|rd|th) q|q[1-4]|[1-4]q|[12]h)\b", re.I)  # codespell:ignore nd - an ordinal suffix
"""Any period word, for the point reader's "the period relation's question" guard.

.. versionadded:: 6.0.0
   ``point._PERIOD`` until Phase 3, step 2.
"""
PERIOD_INTENT_WORDS = r"\b(1st|2nd|3rd|4th|first|second|third|fourth)[\s-](quarter|half)|\bquarter\b|\b[1-4]h\b|\b[1-4]q\b|\bovertime\b|\bclutch\b"
"""The words the parser's grammar names a period parent by
(``parse.PARENT_GRAMMAR``: a team's quarter, the league's ranking by one, a
player's), as the grammar's row holds them - a fragment, since the grammar
compiles its rows itself.

.. versionadded:: 6.0.0
"""
# A quarter or half used as a CONDITION on which games count: the verb, the
# number, the stat and the period, in the shapes questions use - "after
# making one three in first quarter" (yardstick-v2 F062), "in games where he
# scored 10+ points in the first half", "when he makes a three in the 4th".
PERIOD_CONDITION = re.compile(
    r"\b(?:after\s+|(?:in|for|over)\s+(?:the\s+)?games?\s+(?:(?:where|when|in\s+which)\s+)?(?:(?:he|she|they)\s+)?|when(?:ever)?\s+(?:he|she|they)\s+|if\s+(?:he|she|they)\s+)"
    r"(?:making|scoring|hitting|getting|having|recording|grabbing|made|scored|hit|got|had|recorded|grabbed|makes|scores|hits|gets|has|records|grabs)\s+"
    r"(?P<least>at\s+least\s+)?(?P<n>\d{1,3}|an?|one|two|three|four|five|six|seven|eight|nine|ten)\s*(?P<more>\+|\s+or\s+more)?\s+"
    r"(?P<stat>[a-z0-9 -]+?)\s+in\s+(?:the\s+)?(?P<period>(?:first|second|third|fourth|1st|2nd|3rd|4th)[\s-]+(?:quarter|half)|q[1-4]|[1-4]q|[12]h)\b",
    re.IGNORECASE,
)
"""A line on a stat in one quarter or half, conditioning which games count
(``parse.read_period_condition``, the line slice's reader).

.. versionadded:: 6.0.0
   ``parse._PERIOD_CONDITION`` until Phase 3, step 2.
"""


# --- The line and the companions (Phase 3, step 2's fifth slice) ---------------------

MEASURE_WORDS: dict[str, str] = {
    "points": "points",
    "point": "points",
    "pts": "points",
    "pt": "points",
    "rebounds": "rebounds",
    "rebound": "rebounds",
    "reb": "rebounds",
    "rebs": "rebounds",
    "boards": "rebounds",
    "assists": "assists",
    "assist": "assists",
    "ast": "assists",
    "asts": "assists",
    "steals": "steals",
    "steal": "steals",
    "stl": "steals",
    "blocks": "blocks",
    "block": "blocks",
    "blk": "blocks",
    "turnovers": "turnovers",
    "turnover": "turnovers",
    "tov": "turnovers",
    "to": "turnovers",
    "fouls": "fouls",
    "foul": "fouls",
    "pf": "fouls",
    "minutes": "minutes",
    "minute": "minutes",
    "mins": "minutes",
    "min": "minutes",
    "fga": "fieldGoalsAttempted",
    "field goal attempts": "fieldGoalsAttempted",
    "shots": "fieldGoalsAttempted",
    "shot attempts": "fieldGoalsAttempted",
    "fgm": "fieldGoalsMade",
    "field goals": "fieldGoalsMade",
    "field goals made": "fieldGoalsMade",
    "fta": "freeThrowsAttempted",
    "free throw attempts": "freeThrowsAttempted",
    "free throws attempted": "freeThrowsAttempted",
    "ftm": "freeThrowsMade",
    "free throws": "freeThrowsMade",
    "free throws made": "freeThrowsMade",
    "3pa": "threePointFieldGoalsAttempted",
    "three point attempts": "threePointFieldGoalsAttempted",
    "threes attempted": "threePointFieldGoalsAttempted",
    "3pm": "threePointFieldGoalsMade",
    "3s": "threePointFieldGoalsMade",
    "threes": "threePointFieldGoalsMade",
    "3 pointers": "threePointFieldGoalsMade",
    "three pointers": "threePointFieldGoalsMade",
    "threes made": "threePointFieldGoalsMade",
    "oreb": "offensiveRebounds",
    "offensive rebounds": "offensiveRebounds",
    "dreb": "defensiveRebounds",
    "defensive rebounds": "defensiveRebounds",
}
"""What a question calls a box-score column, for a line it asks games to be
kept under or over: the question's words after the number, casefolded, to
the ``player_game_log`` column each names - never question text. The one
definition of what a question calls a column, which the threshold grammar
(:data:`THRESHOLD`, :data:`THRESHOLD_PAIR`), a below/above phrase's reader
(``lines.measure_filters``) and the point reader all read by.

.. versionadded:: 6.0.0
   In the lexicon (``measures.MEASURE_WORDS`` until Phase 3, step 2, the
   line; ``measures`` re-exports it under the same name).
"""

# --- A line on a stat: the five carriers' words --------------------------------------

#: Which SPELLINGS the threshold grammar accepts beside a number, with the
#: regex's own alternation built from them (longest first, so "rebounds" is
#: not matched as "reb" with a stray "ounds" left over). What each one MEANS
#: is read from :data:`MEASURE_WORDS`, the one definition of what a question
#: calls a box-score column: a spelling dropped from it raises ``KeyError``
#: at import rather than silently narrowing what the grammar understands.
#: Minutes are not among them on purpose: "30+ minutes" is :data:`ABOVE`'s
#: phrase, read on every reader, where a threshold is a count's, a
#: record's, a streak's and a high's own line.
THRESHOLD_SPELLINGS: tuple[str, ...] = (
    "points", "point", "pts", "pt",
    "rebounds", "rebound", "rebs", "reb", "boards",
    "assists", "assist", "asts", "ast",
    "steals", "steal", "stl",
    "blocks", "block", "blk",
    "turnovers", "turnover",
    "threes", "3s",
)  # fmt: skip
"""The spellings the threshold grammar reads a line's stat by.

.. versionadded:: 6.0.0
   ``router._THRESHOLD_SPELLINGS`` until Phase 3, step 2.
"""
THRESHOLD_WORDS: dict[str, str] = {word: MEASURE_WORDS[word] for word in THRESHOLD_SPELLINGS}
"""Each threshold spelling to the column it names (:data:`MEASURE_WORDS`).

.. versionadded:: 6.0.0
   ``router._THRESHOLD_WORDS`` until Phase 3, step 2.
"""
_THRESHOLD_ALTERNATION = "|".join(sorted((re.escape(w) for w in THRESHOLD_WORDS), key=len, reverse=True))
# Every "N+ <stat>" pair, in order: "20+ points", "36-plus points", "30 or
# more rebounds". The "+" (or "plus" / "or more") is required: without it
# "top 10 rebound leaders" would read as a line and a ranking question that
# answers today would start refusing. Two or more of them are lines on the
# same game ("20+ point 5+ assist games", #139), which the relation narrows
# by together; one alone is the threshold the shape's own line is read as.
THRESHOLD_PAIR = re.compile(rf"\b(\d{{1,3}})[\s-]*(?:\+|plus|or\s+more)[\s-]*({_THRESHOLD_ALTERNATION})\b", re.IGNORECASE)
"""A line at or above a number with its stat word, the plus required.

.. versionadded:: 6.0.0
   ``router._THRESHOLD_PAIR`` until Phase 3, step 2.
"""
# The same spellings with the "+" optional - a line stated as a bare number
# ("30 pt games", "15 reb"), read only under the readers whose shape is a
# line (a count, a record, a streak, a high: ``line.THRESHOLD_INTENTS``),
# where a bare "30 pt games" is a line and not a ranking. Built from the one
# list, so "30 pt games" reads the 30 the way "30+ pt games" does. A "3
# point" or "3 pt" names the shot, not a line of three
# (:func:`threshold_pairs` leaves it out).
THRESHOLD = re.compile(rf"\b(\d{{1,3}})[\s-]*(?:\+|plus|or\s+more)?[\s-]*({_THRESHOLD_ALTERNATION})\b", re.IGNORECASE)
"""A line on a stat, the plus optional - the subject's, or a companion's
inside his phrase (``subject._condition_role`` reads it through this too).

.. versionadded:: 6.0.0
   ``router._THRESHOLD`` and ``subject._CONDITION_THRESHOLD``, one pattern
   twice, until Phase 3, step 2.
"""
# A number after a scoring verb is a line on points, stat word or not:
# "celtics record when jayson tatum scores 30" read no threshold, and was
# refused as a question about a team named Jayson Tatum. Not where a stat
# word follows the number ("scored 30 points" is THRESHOLD's; "scored 3
# threes" is not points), nor a rate ("scores 30 a game" is an average, not
# a line), a percentage, a decimal or a year.
SCORED = re.compile(
    r"\bscor(?:e|es|ed|ing)\s+(\d{1,3})(?![\d.,%])(?:\s*\+|[\s-]*plus\b|\s+or\s+more\b)?"
    r"(?![\s-]*(?:%|percent\b|a\s+game\b|a\s+night\b|per\s+game\b|on\s+average\b|ppg\b|" + "|".join(sorted((re.escape(w) + r"\b" for w in MEASURE_WORDS), key=len, reverse=True)) + r"))",
    re.IGNORECASE,
)
"""A line on points stated by a scoring verb and a bare number.

.. versionadded:: 6.0.0
   ``router._SCORED`` until Phase 3, step 2.
"""
# A comparison BELOW a number. No slot ever said "under", so without this
# "games with under 14 FTA" reached the count as 14 and was answered as 14
# or MORE - the inverse question. The words after the number are kept: they
# name the stat (the model's own `stat` beside them is the nearest one it
# knows: "fta" arrived as freeThrowsMade), and ``lines.measure_filters``
# refuses a word it cannot map. The words kept after the number stop at a
# connective or the next comparison, so "under 14 fta in his whole career"
# carries "under 14 fta" and "less than 15 fga and with less than 35 minutes"
# is two lines, not one.
BELOW = re.compile(
    r"\b(?:under|fewer\s+than|less\s+than|below|at\s+most|no\s+more\s+than)\s+\d+%?(?:\s+(?!(?:and|or|with|in|for|vs|against|on|at|under|fewer|less|below|no|over|more)\b)[a-z][a-z-]*){0,3}"
    r"|\b\d+\+?\s*(?:minutes|mins?)\s+or\s+less\b",
    re.IGNORECASE,
)
"""A line a game is kept under, as worded.

.. versionadded:: 6.0.0
   ``router._BELOW`` until Phase 3, step 2.
"""
# A comparison AT OR ABOVE a number, on minutes: "with 25 minutes", "20+
# mins", "30 minutes or more" - read out of the situation words (where "paul
# reed gamelog with 25 minutes" refused) into a line the relation applies.
# Deliberately only minutes: "30+ points" is the threshold grammar's, and
# the readers of a line already carry it. Not the "35 minutes" inside "less
# than 35 minutes", which is BELOW's.
ABOVE = re.compile(r"\b(?:with\s+(?:at\s+least\s+)?)?(?<!than\s)(?<!under\s)(?<!below\s)\d+\+?\s*(?:minutes|mins?)\b(?!\s+or\s+less)(?:\s+(?:or\s+more|played))?", re.IGNORECASE)
"""A line of minutes a game is kept at or over, as worded.

.. versionadded:: 6.0.0
   ``router._ABOVE`` until Phase 3, step 2.
"""
# The number and the words after it in a below/above phrase; the leading
# words ("under", "at most", "with") say which way the line faces.
MEASURE_PHRASE = re.compile(r"^(?P<lead>.*?)\b(?P<n>\d+)\+?%?\s*(?P<words>.*)$")
"""A below/above phrase split into its lead, its number and its stat words.

.. versionadded:: 6.0.0
   ``lines._MEASURE_PHRASE`` until Phase 3, step 2.
"""
AT_MOST_LEADS: tuple[str, ...] = ("at most", "no more than")
"""The leads of a below phrase that read as "at or under" ("at most 5
turnovers"); every other lead of :data:`BELOW` ("under", "fewer than",
"less than", "below") reads as under.

.. versionadded:: 6.0.0
"""
# "Fouling out" is six personal fouls - an NBA rule, not something a 3B
# knows. The model got the shape right (a count) and emitted stat "fouls
# committed" and threshold 1; the count refused, and the question then
# hung for 95 seconds. One rule, in one place: the line is fouls >= 6.
FOULED_OUT = re.compile(r"\bfoul(?:ed|s|ing)?\s+out\b", re.IGNORECASE)
"""Fouling out, by its words.

.. versionadded:: 6.0.0
   ``router._FOULED_OUT`` until Phase 3, step 2.
"""
FOUL_OUT_THRESHOLD = 6
"""The fouls that foul a player out: the line :data:`FOULED_OUT` names.

.. versionadded:: 6.0.0
   ``router.FOUL_OUT_THRESHOLD`` until Phase 3, step 2.
"""
# "td3" is a triple-double, and the model reads its "3" as a shot value:
# "luka td3s home" came back as `other` with stat threePointFieldGoalsMade
# and shot_value 3 (yardstick-v2 F098), and before that as his points per
# game at home.
TRIPLE_DOUBLE_ABBREVIATION = re.compile(r"\btd3s?\b", re.IGNORECASE)
"""The "td3" spelling of a triple-double.

.. versionadded:: 6.0.0
   ``router._TRIPLE_DOUBLE_ABBREVIATION`` until Phase 3, step 2.
"""
# A player's games won or lost: "how many playoff games has embiid won" is
# his team's record in the games he played, with no line at all.
GAMES_WON = re.compile(r"\bgames\b.{0,20}\b(won|lost|wins?|los[es]+)\b", re.IGNORECASE)
"""Games won or lost, by their words, the result word in the group.

.. versionadded:: 6.0.0
   ``router._GAMES_WON`` until Phase 3, step 2.
"""
WHEN_REACHES = re.compile(
    r"\bwhen\s+(?:[a-z][\w'.-]*\s+){1,3}?(?:scores?|scored|has|had|gets?|got|puts?\s+up|drops?|dropped|grabs?|grabbed|dishes|dished|records?|recorded|makes?|made|hits?)\b",
    re.IGNORECASE,
)
"""A "<team> when <player> reaches N" clause: "when <someone> scores/has/gets".

.. versionadded:: 6.0.0
   ``router._WHEN_REACHES`` until Phase 3, step 2.
"""
# A line in one quarter or half, conditioning which games count: the number
# as a word ("one three", "a three"), and the singular a line's words take
# after "one" - the columns the period's line rebuilds (MEASURE_WORDS holds
# the plurals and the abbreviations).
CONDITION_NUMBERS: dict[str, int] = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
"""A period line's number as a word.

.. versionadded:: 6.0.0
   ``parse._CONDITION_NUMBERS`` until Phase 3, step 2.
"""
CONDITION_STAT_WORDS: dict[str, str] = {
    "three": "threePointFieldGoalsMade",
    "3": "threePointFieldGoalsMade",
    "three pointer": "threePointFieldGoalsMade",
    "3 pointer": "threePointFieldGoalsMade",
    "3-pointer": "threePointFieldGoalsMade",
    "three-pointer": "threePointFieldGoalsMade",
    "triple": "threePointFieldGoalsMade",
    "point": "points",
    "rebound": "rebounds",
    "board": "rebounds",
    "assist": "assists",
    "steal": "steals",
    "block": "blocks",
    "turnover": "turnovers",
    "foul": "fouls",
    "free throw": "freeThrowsMade",
    "field goal": "fieldGoalsMade",
    "basket": "fieldGoalsMade",
    "shot": "fieldGoalsMade",
}
"""A period line's stat in the singular, to the column the period's line rebuilds.

.. versionadded:: 6.0.0
   ``parse._CONDITION_STAT_WORDS`` until Phase 3, step 2.
"""

# --- The companions: who stands beside the subject -------------------------------------

# The words that end a teammate's name in "without X this season" and the
# like. The question words are here for the same reason the prepositions
# are: each can follow a name, and none of them is one - without them
# "without Tatum and how many wins" reads "how many wins" as a second
# teammate and refuses a question that used to answer. The absence words end
# one too: "with draymond green out" read "draymond green out" as the name,
# and the answer asked whether Bo or Travis Outlaw was meant ("hurt" is not
# one: it is Matt Hurt's name).
NAME_STOPWORDS: frozenset[str] = frozenset(
    "this last in on since during for vs vs. versus against at when while game games season seasons record stats stat playing played plays from over the a an any his her their "
    "how what who whose why many much did does do is are was were has have had than to of by not no "
    "out injured sidelined resting rested rests sitting sits sat missing misses missed absent inactive dnp "
    "doesn't didn't don't isn't wasn't aren't weren't doesnt didnt dont isnt wasnt arent werent".split()  # codespell:ignore doesnt,didnt,isnt,wasnt,arent,werent - typed without the apostrophe
)
"""The words no companion's name holds.

.. versionadded:: 6.0.0
   ``router._NAME_STOPWORDS`` until Phase 3, step 2.
"""
# What a phrase naming a player says about one who sat the games out: "with
# Embiid out", "when Tatum is injured", "when Embiid doesn't play", "in games
# Brown missed". One list, a regex fragment with no groups of its own, read
# for the names it follows (ABSENT_NAMED) and by the subject reading for the
# role (CONDITION_ABSENT), so the two cannot disagree about who sat.
ABSENCE_WORDS = (
    r"(?:out|injured|sidelined|inactive|absent|missing|dnp|rest(?:s|ed|ing)|sits?(?:\s+out)?|sitting(?:\s+out)?|sat(?:\s+out)?|miss(?:es|ed)"
    r"|(?:does|do|did)\s*n[o']?t\s+play|(?:is|are|was|were)\s*n[o']?t\s+playing|not\s+playing)"
)
"""The words that say a player sat the games out - a fragment, no groups.

.. versionadded:: 6.0.0
   ``router._ABSENCE_WORDS`` until Phase 3, step 2.
"""
# "with Embiid out", "when Tatum and Brown are injured", "in games Brown
# missed": a player named before an absence word sat those games out - the
# same teammates "without" names, written the other way round. The stages
# read only that the question HAS this phrase (it is what makes a record a
# with/without split); WHO it names is the subject reading's. The names are
# runs of words that are no stop word, so the absence word must follow them
# directly - "with Embiid playing and Maxey out" names nobody absent rather
# than Embiid - and a pronoun names nobody ("when he is out" is the
# subject's own absence).
_NAME_WORD = r"(?!(?:" + "|".join(sorted((re.escape(w) for w in NAME_STOPWORDS), key=len, reverse=True)) + r"|he|she|they|him|them|it|we|you)(?![A-Za-z.'\-]))[A-Za-z][A-Za-z.'\-]*"
ABSENT_NAMED = re.compile(
    rf"\b(?:with|when|while|in\s+(?:the\s+)?games?(?:\s+(?:that|where|in\s+which))?)\s+(?:both\s+)?({_NAME_WORD}(?:[\s,&+]+{_NAME_WORD}){{0,8}})\s+(?:(?:is|are|was|were)\s+)?{ABSENCE_WORDS}(?![A-Za-z])",
    re.IGNORECASE,
)
"""A player named before an absence word.

.. versionadded:: 6.0.0
   ``router._ABSENT_NAMED`` until Phase 3, step 2.
"""
# A companion's phrase: what follows "without" / "with" / "when" / "while",
# up to the next scoping word. Loose on purpose - the names it holds are
# still checked against the players the question names. "excluding" is
# "without" reworded and "featuring" is "with"; a question word ends the
# phrase, so a fronted "Without Kevin Durant, what is Steph Curry's record"
# names Durant alone, not Curry with him. "with and without Tatum" is the
# split over Tatum, read from its "without": the "with" names nobody.
COMPANION = re.compile(
    r"\b(without|excluding|with(?!\s+(?:and|or)\s+without\b)|featuring|when|while)\s+"
    r"((?:(?!\b(?:vs\.?|versus|against|in|for|this|last|the|what|who|how|which|where)\b)[\w'.,+-]+\s*){1,9})",
    re.IGNORECASE,
)
"""A companion phrase: the keyword, and the words after it a name is read from.

.. versionadded:: 6.0.0
   ``subject._COMPANION`` until Phase 3, step 2.
"""
COMPARED_WITH = re.compile(r"\b(?:compare|compared|comparing|contrast|contrasted|contrasting)\b[^,;?]{0,40}?\bwith\b", re.IGNORECASE)
"""A "with" that follows a compare verb closely ("compare luka with sga")
joins the two subjects; it is not a companion phrase (ISSUES.md #233).

.. versionadded:: 6.0.0
   ``subject._COMPARED_WITH`` until Phase 3, step 2.
"""
# "in games Embiid started", "in the games Brown missed": the role stated
# after the games it narrows, with no "when" or "with" before the name -
# "maxey points in games embiid started" compared the two players, since
# nothing read Embiid as anything but a second subject. The name (one to
# three words) must be followed directly by what he did in those games -
# started, came off, played, scored or had a line, sat out - so "in games
# against Boston" and "in games he started" (the subject's own) name no
# companion here.
COMPANION_STOP = r"vs\.?|versus|against|in|for|this|last|the|what|who|how|which|where|with|without|when|while"
"""The words that end a companion phrase - a fragment.

.. versionadded:: 6.0.0
   ``subject._COMPANION_STOP`` until Phase 3, step 2.
"""
COMPANION_IN_GAMES = re.compile(
    r"\b(in\s+(?:the\s+)?games?(?:\s+(?:that|where|in\s+which))?)\s+"
    rf"((?:(?!\b(?:{COMPANION_STOP}|he|she|they|his|her|their)\b)[\w'.-]+\s+){{1,3}}"
    rf"(?:start(?:s|ed)?|(?:comes?|came)\s+off|play(?:s|ed)?|scor(?:e|es|ed)|had|has|got|{ABSENCE_WORDS})(?![A-Za-z])"
    rf"(?:\s+(?!(?:{COMPANION_STOP})\b)[\w'.,+-]+){{0,4}})",
    re.IGNORECASE,
)
"""A companion named after the games his role narrows: "in games X started".

.. versionadded:: 6.0.0
   ``subject._COMPANION_IN_GAMES`` until Phase 3, step 2.
"""
# A player after a versus word is on the OTHER side of the subject's games -
# a condition (ROADMAP step 3: "most points by curry vs lebron"), never a
# second subject - wherever the words ask for the subject's games.
VERSUS_PHRASE = re.compile(rf"\b(vs\.?|versus|against|v\.?)\s+((?:(?!\b(?:{COMPANION_STOP})\b)[\w'.,+-]+\s*){{1,9}})", re.IGNORECASE)
"""The words after a versus word, a name on the other side is read from.

.. versionadded:: 6.0.0
   ``subject._VERSUS_PHRASE`` until Phase 3, step 2.
"""
# What a companion phrase says the player DID in the games asked about: a
# start, the bench ("off" alone too: the phrase stops at "the", a stop word,
# so "with tatum off the bench" reaches the role reader as "tatum off"), an
# absence (the absence words, and "hurt", which the name reader cannot end
# a name at - it is Matt Hurt's - and a role reader, which never cuts a
# name, can take).
CONDITION_STARTED = re.compile(r"\bstart(?:s|ed|ing)?\b|\bin the starting lineup\b", re.IGNORECASE)
"""A start, by its words.

.. versionadded:: 6.0.0
   ``subject._CONDITION_STARTED`` until Phase 3, step 2.
"""
CONDITION_BENCH = re.compile(r"\bbench\b|\boff\b|\bas a reserve\b", re.IGNORECASE)
"""The bench, by its words.

.. versionadded:: 6.0.0
   ``subject._CONDITION_BENCH`` until Phase 3, step 2.
"""
CONDITION_ABSENT = re.compile(rf"\b(?:{ABSENCE_WORDS}|hurt)(?![A-Za-z])", re.IGNORECASE)
"""An absence, by its words (:data:`ABSENCE_WORDS`, and "hurt").

.. versionadded:: 6.0.0
   ``subject._CONDITION_ABSENT`` until Phase 3, step 2.
"""
NAME_PIECES = re.compile(r"[^\s,&+]+|[,&+]")
"""A companion phrase's words and joiners, one piece each.

.. versionadded:: 6.0.0
   ``subject._NAME_PIECES`` until Phase 3, step 2.
"""
NAME_SHAPED = re.compile(r"[A-Za-z][A-Za-z.'\-]*")
"""A piece shaped like a word of a name.

.. versionadded:: 6.0.0
   ``subject._NAME_SHAPED`` until Phase 3, step 2.
"""
NAME_JOINERS: frozenset[str] = frozenset({"and", "or", "nor", "&", "+", ","})
"""The joiners between two names in one phrase.

.. versionadded:: 6.0.0
   ``subject._NAME_JOINERS`` until Phase 3, step 2.
"""


def threshold_pairs(text: str) -> list[re.Match[str]]:
    """Every line :data:`THRESHOLD` reads in ``text``, in order, that is a
    line and not a shot type - "3 point" and "3 pt" name the shot, not three
    points - and holds a number of one or more.

    .. versionadded:: 6.0.0
    """
    found: list[re.Match[str]] = []
    for match in THRESHOLD.finditer(text):
        number = int(match.group(1))
        if number == 3 and match.group(2).casefold().startswith(("point", "pt")):
            continue
        if number >= 1:
            found.append(match)
    return found


def scored_threshold(text: str) -> re.Match[str] | None:
    """The line a scoring verb states with no stat word after it ("scores
    30", "scored 40+"), which is points (:data:`SCORED`), holding a number
    of one or more - or None. A reader of it reads it only where the text
    states no line WITH its stat word (:func:`threshold_pairs`), which wins.

    .. versionadded:: 6.0.0
       ``router._threshold_from_text_scored`` until Phase 3, step 2, which
       returned the number and made the pairs' check itself.
    """
    match = SCORED.search(text)
    return match if match is not None and int(match.group(1)) >= 1 else None


# --- The measure: what a question asks about ------------------------------------------------
#
# Phase 3, step 2's sixth slice. Every pattern the measure's writers read by,
# moved here from the router's stages, the parser's measure grammar, the
# point reader's word tables and the metrics' word table, each with its
# reason; the one tagger reads them (``query/measure.py``).

MEASURE_GRAMMAR: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), key)
    for pattern, key in (
        # (the words, the measure as typed) - first match wins; the netpoints family before its parts.
        (
            r"\b(defensive|def|defense)\b.{0,20}\b(net ?po?i?nts?|netpts)\b.{0,30}\b(per 100|/ ?100|adjusted|per possession)\b|\b(net ?po?i?nts?"
            r"|netpts)\b.{0,20}\b(defensive|def|defense)\b.{0,30}\b(per 100|/ ?100|adjusted|per possession)\b"
            r"|\badjusted\b.{0,12}\bdefensive\b.{0,12}\b(net ?po?i?nts?|netpts)\b|\bdefensive\b.{0,12}\b(netpts|net ?po?i?nts?)\s*/\s*100\b",
            "netpoints_defense_per_100",
        ),
        (
            r"\b(offensive|off|offense)\b.{0,20}\b(net ?po?i?nts?|netpts)\b.{0,30}\b(per 100|/ ?100|adjusted|per possession)\b|\b(net ?po?i?nts?"
            r"|netpts)\b.{0,20}\b(offensive|off|offense)\b.{0,30}\b(per 100|/ ?100|adjusted|per possession)\b"
            r"|\badjusted\b.{0,12}\boffensive\b.{0,12}\b(net ?po?i?nts?|netpts)\b",
            "netpoints_offense_per_100",
        ),
        (r"\b(defensive|def|defense)\b.{0,20}\b(net ?po?i?nts?|netpts)\b|\b(net ?po?i?nts?|netpts)\b.{0,20}\b(defensive|def|defense)\b", "netpoints_defense"),
        (r"\b(offensive|off|offense)\b.{0,20}\b(net ?po?i?nts?|netpts)\b|\b(net ?po?i?nts?|netpts)\b.{0,20}\b(offensive|off|offense)\b", "netpoints_offense"),
        (r"\b(net ?po?i?nts?|netpts)\b.{0,30}\b(per 100|/ ?100|adjusted|per possession)\b|\badjusted\b.{0,12}\b(net ?po?i?nts?|netpts)\b", "netpoints_per_100"),
        (r"\b(net ?po?i?nts?|netpts)\b", "netpoints"),
        (r"\bpoints? differential\b|\bpoint diff\b|\bdifferential\b", "points_differential"),
        (r"\bplus[ /-]?minus\b|\+/-", "plus_minus"),
        (r"\bts ?%|\btrue shooting\b", "ts_pct"),
        (r"\befg\b|\beffective field goal", "efg_pct"),
        (r"\busage\b|\busg\b", "usage_pct"),
        (r"\bgame score\b", "game_score"),
        (r"\btriple[ -]?doubles?\b|\btd3s?\b|\btds\b", "triple_double"),
        (r"\bdouble[ -]?doubles?\b|\bdd\b", "double_double"),
        # "%" is not a word character, so it takes no \b: "who had the highest
        # 3pt % this season" read as 3-pointers made while it did.
        (r"\b(3|three)[ -]?(pt|point|pointer)s?\b.{0,12}(\bpercentage\b|\bpct\b|%)|\b3p%|\b3pt%", "threePointFieldGoalPct"),
        (r"\b(2|two)[ -]?(pt|point|pointer)s?\b.{0,12}(\bpercentage\b|\bpct\b|%)|\b2p%|\b2pt%", "twoPointFieldGoalPct"),
        (r"\bfg ?%|\bfg percentage\b|\bfield goal percentage\b", "fieldGoalPct"),
        (r"\bft ?%|\bfree throw percentage\b", "freeThrowPct"),
        # What a team gives up is the opponent's line, never its own: "rebounds
        # allowed per team" read as the teams' own rebounds and was answered with
        # them, best first. Points allowed is a team metric; the rest name a key
        # no reader ranks, which refuses rather than answering the team's own.
        (r"\b(points?|pts)\b.{0,12}\b(allowed|given up|conceded)\b|\b(allowed|gave up|conceded)\b.{0,12}\b(points?|pts)\b|\bopponents?'? (points|ppg)\b", "points allowed"),
        (r"\b(rebounds?|boards)\b.{0,12}\b(allowed|given up|conceded)\b|\b(allowed|gave up|conceded)\b.{0,12}\b(rebounds?|boards)\b", "rebounds allowed"),
        (r"\bassists?\b.{0,12}\b(allowed|given up|conceded)\b", "assists allowed"),
        (r"\b(threes|3s|(3|three)[ -]?(pt|point|pointer)s?)\b.{0,12}\b(allowed|given up|conceded)\b", "threes allowed"),
        # A three is its own column: "3 point stats", "three points made" and
        # "3-point average" are threes made (a percentage is read above), never
        # the "point" in them read as points.
        # Both asked for: the made line already says "585 of 1,727", and no
        # per-game line reads the attempted column alone.
        (r"(?=.*\b(3|three)[ -]?(pt|point|pointer)s?\b)(?=.*\b(attempts?|attempted|tries|3pa)\b)(?=.*\b(made|makes|mad|hit)\b)", "threePointFieldGoalsMade"),
        (r"\b(3|three)[ -]?(pt|point|pointer)s?\b.{0,12}\b(attempts?|attempted|tries)\b|\b3pa\b", "threePointFieldGoalsAttempted"),
        (r"\b(3|three)[ -]?(pt|point|pointer)s?\b(?!.{0,20}\b(distance|range|shots?)\b)", "threePointFieldGoalsMade"),
        (r"\bscorers?\b|\bscores\b|\bscoring\b", "points"),
    )
)
"""The measure grammar: the stat a question names in its own words, read
before the normalizer's key so a phrase the closed vocabulary holds never
depends on the model - the NetPoints family above all (the 3B misses most
of it), then the derived rates and the words :data:`MEASURE_WORDS` does
not hold. Each key is the spelling the stages wrote (the ``stat`` slot's),
which the catalog resolves (``measure.key_of``).

.. versionadded:: 6.0.0
   ``parse.MEASURE_GRAMMAR`` until Phase 3, step 2.
"""

MEASURE_ORDINARY_WORDS: frozenset[str] = frozenset({"to", "min"})
"""Abbreviations in :data:`MEASURE_WORDS` that are also ordinary words
("compared to other teams" is no turnover count): read only where the
question writes them in capitals ("TO", "MIN").

.. versionadded:: 6.0.0
   ``parse._MEASURE_ORDINARY_WORDS`` until Phase 3, step 2.
"""

MEASURE_WORD_SPLIT = re.compile(r"[^a-z0-9%/+]+")
"""What splits a casefolded question into the words :data:`MEASURE_WORDS`
is matched on - every character but a letter, a digit, "%", "/" and "+".

.. versionadded:: 6.0.0
"""

MEASURE_WORD_AT: dict[str, re.Pattern[str]] = {word: re.compile("(?<![a-z0-9%/+])" + re.escape(word) + "(?![a-z0-9%/+])") for word in MEASURE_WORDS}
"""Each box-score word with the pattern that finds it whole in the casefolded
question - where the measure tagger's claim of it starts and ends.

.. versionadded:: 6.0.0
"""

MEASURE_TOKEN = re.compile(r"[A-Za-z0-9%/+]+")
"""One token of the question as typed, capitals kept - how an abbreviation
that is also an ordinary word ("TO", "MIN") is told from the word.

.. versionadded:: 6.0.0
"""

# The words a question uses when it is actually asking about one stat, as
# opposed to asking who is better. Loose on purpose, and safe because of
# where it is used: a word it misses widens a comparison or a team's line
# to the whole line, which still holds the stat asked about; a word it
# matches too eagerly leaves the model's key where it was. It is read
# beyond the comparison - a quarter's line and its ranking, a team's line,
# a streak - and there a word it misses DOES move a number: "vj edgecombe
# 2nd half plus minus" lost its stat and answered his second-half points
# (found 2026-10-09, Phase 3, step 0). Plus-minus is a stat the question
# names, and reads as one; a stat word added here is a number kept.
STAT_WORDS = re.compile(
    r"\b(points?|scor\w*|pts?|rebound\w*|boards|reb|assist\w*|passing|dimes|ast|steal\w*|stl|block\w*|blk|"
    r"turnover\w*|giveaways?|fouls?|minutes?|mins?|shoot\w*|shots?|three\w*|3pt|3-point\w*|field goals?|free throws?|"
    r"percentage|efficien\w*|usage|double-doubles?|triple-doubles?|td3s?|ppg|rpg|apg|spg|bpg|fg|ft|3p|ts|efg|plus[ /-]?minus)\b|\+/-",
    re.IGNORECASE,
)
"""Whether the question asked about a particular stat at all - the test
the model's required key is dropped by where the words name none.

.. versionadded:: 6.0.0
   ``router._STAT_WORDS`` until Phase 3, step 2.
"""

TEAM_STAT_WORDS = re.compile(r"\b(?:pace|ratings?|offen\w*|defen\w*|net|possessions?|record|wins?|losses)\b", re.IGNORECASE)  # codespell:ignore offen - a regex stem
"""A team's own measure named beside :data:`STAT_WORDS`' box-score words:
pace, a rating, possessions, the record.

.. versionadded:: 6.0.0
   ``router._TEAM_STAT_WORDS`` until Phase 3, step 2.
"""

# Rate stats the prompt never lists as a player `stat`, so the model reaches
# for the nearest one it knows. Measured: "kevin durant true shooting
# percentage career" came back as stat='threePointFieldGoalPct' and was
# answered with his 3-point percentage - a different stat, fluently. Named in
# the question, the stat is read from it; a reader that has no such stat
# then refuses.
ADVANCED_STAT_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ts_pct", re.compile(r"\btrue[- ]shooting\b|\bts\s?%|\bts\s+pct\b", re.IGNORECASE)),
    ("efg_pct", re.compile(r"\beffective\s+(?:field\s+goal|fg)\b|\befg\b", re.IGNORECASE)),
    ("usage_pct", re.compile(r"\busage\b", re.IGNORECASE)),
)
"""``(key, pattern)`` - a computed rate named in the words, read over the
model's key on the readers that look one up.

.. versionadded:: 6.0.0
   ``router._ADVANCED_STAT_WORDS`` until Phase 3, step 2.
"""

# Hollinger's single-game composite (ISSUES.md #114). Anchored to the
# two-word phrase and not to "score" alone, because "score" alone means
# points everywhere else in basketball - "pacers score", "what was the score
# of the game" would be hijacked into a Game Score ranking. The trailing \b
# keeps "per game scored on fridays" out. Verified against the 261-question
# StatMuse feed corpus: the phrase appears in exactly one question.
GAME_SCORE = re.compile(r"\bgame\s*scores?\b", re.IGNORECASE)
"""Game score, by its two-word name.

.. versionadded:: 6.0.0
   ``router._GAME_SCORE`` until Phase 3, step 2.
"""

# Two-point field-goal percentage (ISSUES.md #114): "2pt", "2-pt", "2 point",
# "two point" and "2p", each read against percentage/pct/% (optionally with
# "field goal(s)" between) - and nothing shorter, so "3 point percentage"
# and a plain "field goal percentage" are never swept in, and "20 point" (a
# threshold) fails the same way - the "0" sits where "pt"/"point" must start.
TWO_POINT_PCT = re.compile(r"\b(?:2[- ]?pts?|2p|2[- ]?points?|two[- ]?points?)\b(?:\s+field\s*goals?)?\s*(?:%|pct\.?|percent(?:age)?)\b", re.IGNORECASE)
"""A 2-point percentage, named.

.. versionadded:: 6.0.0
   ``router._TWO_POINT_PCT`` until Phase 3, step 2.
"""

# A ranking of shot distance (ISSUES.md #114): no metric ranks it, and none is
# planned. "who lead the league in avg 3 point distance" arrived with the
# nearest real metric the model knew and answered a 3-point PERCENTAGE.
SHOT_DISTANCE_RANKED = re.compile(r"\bshot\s+distance\b|\b(?:3|three)[- ]?points?\s+distance\b|\bdistance\s+for\s+(?:3|three)[- ]?points?\b", re.IGNORECASE)
"""Shot distance named as a thing to rank by.

.. versionadded:: 6.0.0
   ``router._LEADERBOARD_SHOT_DISTANCE`` until Phase 3, step 2.
"""

ATTEMPTED = re.compile(r"\battempt(?:ed|s)?\b|\bfga\b|\b3pa\b|\bfta\b|\bshots?\s+taken\b", re.IGNORECASE)
"""The attempts named beside a shot ("who attempted the most three pointers"
filed the made column).

.. versionadded:: 6.0.0
   ``router._ATTEMPTED`` until Phase 3, step 2.
"""

# "mad" too: "show embiid's 3pt attempts and 3pts mad for his career" asks
# for both, which the made line reports ("585 of 1,727").
MADE = re.compile(r"\b(?:made|mad|makes?|hit|hits)\b", re.IGNORECASE)
"""The makes named beside a shot - with the attempts, both are asked for and the made line answers.

.. versionadded:: 6.0.0
"""

# Which shots a distance or a chart is about, from the question's own words:
# "avg 3pt shot distance" reads its 3 here. Exactly one value named counts;
# "twos and threes" is neither.
SHOT_VALUE_WORDS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (3, re.compile(r"\b(?:3|three)[- ]?(?:pt|pts|point(?:er)?s?)\b|\bthrees\b|\b3s\b", re.IGNORECASE)),
    (2, re.compile(r"\b(?:2|two)[- ]?(?:pt|pts|point(?:er)?s?)\b|\btwos\b", re.IGNORECASE)),
    (1, re.compile(r"\bfree[- ]throws?\b|\bfts?\b", re.IGNORECASE)),
)
"""``(value, pattern)`` - a shot value named.

.. versionadded:: 6.0.0
   ``router._SHOT_VALUE_WORDS`` until Phase 3, step 2.
"""

# The side of the ball a fingerprint or a NetPoints rating asked for,
# matched whole so "offensive" and "defensive" count but a player named
# Offenberg would not.
SIDE_WORDS: dict[str, re.Pattern[str]] = {
    "offense": re.compile(r"\boffens(?:e|ive)\b", re.IGNORECASE),
    "defense": re.compile(r"\bdefens(?:e|ive)\b", re.IGNORECASE),
}
"""The side words, by side.

.. versionadded:: 6.0.0
   ``router.SIDE_WORDS`` until Phase 3, step 2.
"""

# A NetPoints rate asked for by any of its names. In this data the only
# adjusted form of NetPoints is the per-100-possessions rate, so "adjusted"
# has exactly one honest reading; "/ 90" is a rate nothing here holds, and
# refuses (#152).
RATE_WORDS = re.compile(r"\badjusted\b|\bper\s+(?:100\s+)?poss?ess?ions?\b|\bper\s+100\b|/\s*100\b", re.IGNORECASE)
"""A per-possession rate named.

.. versionadded:: 6.0.0
   ``router._RATE_WORDS`` until Phase 3, step 2.
"""

PER_90 = re.compile(r"\bper\s+90\b|/\s*90\b", re.IGNORECASE)
"""A per-90 rate named - a football unit, held in nothing here.

.. versionadded:: 6.0.0
   ``router._PER_90`` until Phase 3, step 2.
"""

#: A team's season TOTAL asked for by "how many ... made/scored/have" or
#: "total", with no per-game word beside it (:data:`PER_GAME_WORDS`).
TEAM_TOTAL = re.compile(r"\bhow\s+many\b.{0,60}\b(?:made|scored|have|has|had|hit|grabbed|dished)\b|\btotal\b", re.IGNORECASE)
"""A season total asked of a team's own stat.

.. versionadded:: 6.0.0
   ``router._TEAM_TOTAL`` until Phase 3, step 2.
"""

PER_GAME_WORDS = re.compile(r"\bper\s+game\b|\bppg\b|\brpg\b|\bapg\b|\baverages?\b|\bavg\b", re.IGNORECASE)
"""A per-game figure asked for outright.

.. versionadded:: 6.0.0
   ``router._PER_GAME_WORDS`` until Phase 3, step 2.
"""

# A team metric's word qualified as the OTHER side's: "rebounds allowed per
# team" is what a team gives up, and reading it as the alias `rebounds`
# answered the teams' own rebounds, best first (yardstick-v2 F101). Not
# "against": "rebounds against the knicks" is the team's own.
GIVEN_UP = re.compile(r"\s+(?:allowed|given\s+up|conceded)\b")
"""The words that make a measure the opponent's, after its name.

.. versionadded:: 6.0.0
   ``router._GIVEN_UP`` until Phase 3, step 2.
"""

LOSING_STREAK = re.compile(r"\blos(?:ing|s|e)\s+streaks?\b|\bstraight\s+losses\b|\blosses\s+in\s+a\s+row\b|\bskid\b", re.IGNORECASE)
"""A run of losses, named.

.. versionadded:: 6.0.0
   ``router._LOSING_STREAK`` until Phase 3, step 2.
"""

# Columns asked for beside a ranking - "top 5 scorers with their rebounds and
# assists", "... and the team they play for" (yardstick-v2 F017).
FIELDS_AFTER = re.compile(r"\b(?:with|alongside|and|plus|including)\s+(?:their|his|the)\s+(?P<rest>.+)$", re.IGNORECASE)
"""The words after which a ranking names the columns to show beside it.

.. versionadded:: 6.0.0
   ``parse._FIELDS_AFTER`` until Phase 3, step 2.
"""

TEAM_FIELD_WORDS = re.compile(r"\bteams?\s+(?:they|he)\s+plays?(?:ed)?\s+for\b|\b(?:with|and|plus)\s+(?:the|their)\s+teams?\b|\btheir\s+current\s+teams?\b", re.IGNORECASE)
"""The words that ask a ranking to show each player's team beside him
("with the team they play for", F017) - read as the ``team`` column, and
set aside by the compiler's league-wide guard, which otherwise reads any
team word as a team's own question.

.. versionadded:: 6.0.0
   ``metrics.TEAM_FIELD_WORDS`` until Phase 3, step 2.
"""

#: Words in the question for a measure the model may not have named - the
#: point reader's table, which moves a point by the words (a measure beyond
#: a reader's list, a boolean measure as a condition).
WORD_MEASURES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), name)
    for pattern, name in (
        (r"\bts ?%|\btrue shooting\b", "ts_pct"),
        (r"\befg\b|\beffective field goal", "efg_pct"),
        (r"\bplus[ /-]?minus\b|\+/-", "plusMinus"),
        (r"\bgame score\b", "game_score"),
        (r"\busage\b", "usage_pct"),
        (r"\btriple[ -]?doubles?\b|\btd3s?\b|\btds\b", "triple_double"),
        (r"\bdouble[ -]?doubles?\b|\bdd\b", "double_double"),
        (r"\bfg ?%|\bfg percentage\b|\bfield goal percentage\b", "fg_pct"),
        (r"\b3 ?pt ?%|\b3 point percentage\b|\bthree point percentage\b|\b3p%", "three_pct"),
        (r"\bft ?%|\bfree throw percentage\b", "ft_pct"),
        (r"\bpra\b|\bpts\+reb\+ast\b|points\+rebounds\+assists", "pra"),
        (r"\bfouled out\b|\bfoul(ed)? outs?\b", "fouled_out"),
    )
)
"""``(pattern, measure)`` - a phrase naming one of the compiler's measures directly.

.. versionadded:: 6.0.0
   ``measures.WORD_MEASURES`` until Phase 3, step 2.
"""

#: A word in the question naming a team's own measure directly - the team
#: counterpart of :data:`WORD_MEASURES`, over the team compiler's game and
#: season measures rather than the player relation's columns.
TEAM_WORD_MEASURES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), name)
    for pattern, name in (
        (r"\bpoint(?:s)? differential\b|\bpoint diff\b|\bdifferential\b", "differential"),
        (r"\bpoints? allowed\b|\bopponent'?s? points\b", "points_allowed"),
        (r"\b(?:3|three)[- ]?point(?:er)?s?\b(?:.{0,10}\bmade\b)?", "threePointFieldGoalsMade"),
        (r"\btotal points\b|\bpoints scored\b|\bhow many points\b", "points"),
        (r"\brebounds\b", "rebounds"),
        (r"\bassists\b", "assists"),
        (r"\bsteals\b", "steals"),
        (r"\bblocks\b", "blocks"),
        (r"\bturnovers\b", "turnovers"),
    )
)
"""``(pattern, measure)`` - a phrase naming a team's game-level or season-total measure.

.. versionadded:: 6.0.0
   ``point._TEAM_WORD_MEASURES`` until Phase 3, step 2.
"""

#: A word in the question naming what a "highest/biggest ... triple-double"
#: ranking of GAMES orders by - the plain box-score words
#: :data:`WORD_MEASURES` does not carry.
BOOLEAN_RANK_WORDS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), name)
    for pattern, name in (
        (r"\bscoring\b|\bpoints?\b|\bpts\b", "points"),
        (r"\brebounds?\b|\bboards?\b", "rebounds"),
        (r"\bassists?\b", "assists"),
        (r"\bsteals?\b", "steals"),
        (r"\bblocks?\b", "blocks"),
    )
)
"""``(pattern, measure)`` - the measure a ranking of boolean games is ordered by.

.. versionadded:: 6.0.0
   ``point._BOOLEAN_RANK_WORDS`` until Phase 3, step 2.
"""

# The words a question uses for a TEAM metric, by the metric's key - the
# team-season relation's vocabulary (``team_metrics.TEAM_METRICS``), read
# by the tagger as the longest alias the words hold. Moved here from
# `measures._ALIASES` (Phase 3, step 2), where the router imported it; the
# catalog keys each alias to the measure it names (``measure.ALIASES``).
TEAM_METRIC_WORDS: dict[str, tuple[str, ...]] = {
    "record": ("record", "records", "wins", "win", "win pct", "win percentage", "winning percentage", "win percent", "standings", "win loss", "win loss record"),
    "losses": ("losses", "loss", "losing record"),
    "points": ("points", "point", "ppg", "points per game", "scoring", "points scored", "avg points"),
    "opponent_points": (
        "opponent points",
        "opponent points per game",
        "opponent ppg",
        "opp points",
        "opp ppg",
        "points allowed",
        "points allowed per game",
        "points against",
        "points given up",
    ),
    "point_differential": ("point differential", "differential", "point diff", "margin", "point margin", "scoring margin", "margin of victory", "plus minus"),
    "pace": ("pace", "pace factor", "possessions", "possessions per game"),
    "offensive_rating": ("offensive rating", "off rating", "ortg", "offensive efficiency", "offensive rtg"),
    "defensive_rating": ("defensive rating", "def rating", "drtg", "defensive efficiency", "defensive rtg", "defense", "defensive"),
    "net_rating": ("net rating", "net efficiency", "net rtg", "nrtg"),
    "field_goal_pct": ("field goal pct", "field goal percentage", "fg pct", "fg%", "fg percentage", "field goal %", "shooting percentage"),
    "three_point_pct": (
        "three point field goal pct",
        "three point field goal percentage",
        "three point pct",
        "three point percentage",
        "3pt pct",
        "3pt%",
        "3pt percentage",
        "3 point pct",
        "3 point percentage",
        "3p%",
        "3p pct",
        "three point shooting",
    ),
    "free_throw_pct": ("free throw pct", "free throw percentage", "ft pct", "ft%", "free throw %"),
    "true_shooting_pct": ("ts pct", "ts%", "true shooting", "true shooting pct", "true shooting percentage"),
    "effective_fg_pct": ("efg pct", "efg%", "efg", "effective field goal pct", "effective field goal percentage", "effective fg pct"),
    "rebounds": ("rebounds", "rebound", "rpg", "total rebounds", "rebounding", "boards", "avg rebounds"),
    "offensive_rebounds": ("offensive rebounds", "offensive rebound", "oreb", "offensive boards"),
    "defensive_rebounds": ("defensive rebounds", "defensive rebound", "dreb", "defensive boards"),
    "assists": ("assists", "assist", "apg", "dimes", "avg assists"),
    "turnovers": ("turnovers", "turnover", "tov", "giveaways", "total turnovers"),
    "steals": ("steals", "steal", "spg"),
    "blocks": ("blocks", "block", "bpg", "blocked shots"),
    "fouls": ("fouls", "foul", "personal fouls"),
    "three_pointers_made": (
        "three point field goals made",
        "threes",
        "threes made",
        "three pointers",
        "three pointers made",
        "3 pointers",
        "3 pointers made",
        "3pm",
        "3pt made",
        "made threes",
    ),
    "three_pointers_attempted": ("three point field goals attempted", "three point attempts", "threes attempted", "3 point attempts", "3pa", "3pt attempts"),
    "field_goals_made": ("field goals made", "field goals", "fgm"),
    "free_throws_made": ("free throws made", "free throws", "ftm"),
    "free_throws_attempted": ("free throws attempted", "free throw attempts", "fta"),
    "points_in_paint": ("points in the paint", "points in paint", "paint points"),
    "fast_break_points": ("fast break points", "fastbreak points", "fast break"),
}
"""Team metric key -> the words a question names it by.

.. versionadded:: 6.0.0
   ``measures._ALIASES`` until Phase 3, step 2.
"""

STAT_ALIASES: dict[str, str] = {alias: key for key, aliases in TEAM_METRIC_WORDS.items() for alias in aliases}
"""Normalized slot text -> ``team_metrics.TEAM_METRICS`` key.

.. versionadded:: 6.0.0
   ``measures.STAT_ALIASES`` until Phase 3, step 2 (``measures`` and ``team_metrics`` re-export it).
"""

TEAM_METRIC_NAMED: tuple[tuple[str, re.Pattern[str]], ...] = tuple((alias, re.compile("(?<![a-z0-9])" + re.escape(alias) + "(?![a-z0-9])")) for alias in sorted(STAT_ALIASES, key=len, reverse=True))
"""Each team-metric alias with the pattern that finds it whole in the
casefolded question, longest alias first: the LONGEST alias the question
holds anywhere names the metric ("turnover percentage" beside "record"
reads turnovers), as the stages read it - the model invents team stats
("usage_pct_defense" for "lowest defensive rating"), and the question says
which one it meant.

.. versionadded:: 6.0.0
   ``router._team_metric_in``'s search until Phase 3, step 2.
"""

CAMEL_CASE_BREAK = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
"""Where a camelCase key breaks into words (``threePointFieldGoalsMade``).

.. versionadded:: 6.0.0
   ``team_metrics.normalize_stat``'s pattern until Phase 3, step 2.
"""

HOW_MANY_GAMES = re.compile(r"\bhow\s+many\b", re.IGNORECASE)
"""A count asked outright ("how many") - beside a games key, a line's count of games rather than a log of one.

.. versionadded:: 6.0.0
   ``router._HOW_MANY`` until Phase 3, step 2 (the router keeps its own for the span's context).
"""

LOG_WORDS = re.compile(r"\b(?:game\s*logs?|gamelogs?|logs?)\b|\b(?:each|every|by)\s+game\b", re.IGNORECASE)
"""A game log asked for by name ("luka ft log" routed to a season average once).

.. versionadded:: 6.0.0
   ``router._LOG_WORDS`` until Phase 3, step 2 (the router reads it for the intent too).
"""

WORD = re.compile(r"[a-z]+")
"""A run of letters: the words a casefolded phrase is read as.

.. versionadded:: 6.0.0
"""


# --- The subject's own: who a question is about (Phase 3, step 2's seventh slice) ---------
#
# The words the subject reading reads WHO by - a position group, a team's
# word, a city or an abbreviation, a singular nickname, the versus and "for"
# phrases a team is read after, the grammar a dropped subject is read back
# from, and where a name ends - each with its reason beside it. Read by
# ONE reader, :func:`association.query.subject.read_subject` (and the
# helpers it calls), which claims the characters it read; until this slice
# they lived in :mod:`association.query.subject`, :mod:`association.query.router`
# and :mod:`association.query.reading`.

POSITION_WORDS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), code)
    for pattern, code in (
        (r"\bcenters?\b", "C"),
        (r"\bpoint guards?\b", "PG"),
        (r"\bshooting guards?\b", "SG"),
        (r"\bpower forwards?\b", "PF"),
        (r"\bsmall forwards?\b", "SF"),
        (r"\bforwards?\b", "F"),
        (r"\bguards?\b", "G"),
    )
)
"""``(pattern, position group)`` - the words a position-group question uses,
in the order they are tried: the first that matches names the group, so
"shooting guard" is ``SG`` before it is ``G`` (the longer phrase is listed
first wherever two overlap).

.. versionadded:: 4.4.0

.. versionchanged:: 6.0.0
   In the lexicon, compiled (``reading.POSITIONS`` until Phase 3, step 2,
   as pattern strings; ``query/point.py`` and ``compose/move.py`` before).
"""

POSITION_GROUPS: frozenset[str] = frozenset(code for _pattern, code in POSITION_WORDS)
"""Every position group a subject's position words read as - ESPN's own
letters, each the key of the roster positions it reaches on the league's
read (``player_relation.POSITION_CODES``, which a test holds to it).

.. versionadded:: 6.0.0
"""

# A line at or above a number, however it is written: "30+", "36-plus",
# "30 or more", "at least 2" (the paraphrases' spellings, parser-greenfield) -
# a fragment the child grammars below are built from.
N_PLUS = r"(?:\d{1,3}[\s-]*(?:\+|plus\b|or more\b)|\bat least \d{1,3})"
"""A line at or above a number, as a fragment with no groups.

.. versionadded:: 6.0.0
   ``subject._N_PLUS`` until Phase 3, step 2.
"""

# The child intents the question's own words assign, gated on the subject's
# kind (``subject._CHILD_GRAMMARS`` holds the table: which kinds and which
# parents each applies under, in precedence order). The words only, here.
#
# "per game" is an average, never one game: "the highest points per game
# average" is a season ranking. "single game" is one game with an article or
# without: "this season's single game with the most assists" and "most 3
# pointers made in single game 24-25" answered the season's leaders (ISSUES.md
# #260) - "single games", a plural, is not one.
CHILD_SINGLE_GAME_HIGH = re.compile(r"\bin (?:a|one) (?:single )?(?:game|match|contest|outing)\b|\bsingle[- ]game\b|\bcareer[- ]high\b|\bhighest\b.{0,60}(?<!per )\bgame\b", re.IGNORECASE)
"""The words that name a single game's high.

.. versionadded:: 6.0.0
   ``subject._CHILD_GRAMMARS``' own pattern until Phase 3, step 2.
"""
CHILD_SHOT_DISTANCE = re.compile(r"\bhow far\b|\bdistance\b", re.IGNORECASE)
"""The words that name a shot distance.

.. versionadded:: 6.0.0
   ``subject._CHILD_GRAMMARS``' own pattern until Phase 3, step 2.
"""
CHILD_STREAK = re.compile(r"\bstreaks?\b|\bwin ?streak\b|\bstraight (?:games|wins|losses)\b|\bin a row\b|\bconsecutive\b", re.IGNORECASE)
"""The words that name a run of games.

.. versionadded:: 6.0.0
   ``subject._CHILD_GRAMMARS``' own pattern until Phase 3, step 2.
"""
CHILD_RECORD_WHEN_LINE = re.compile(
    rf"\brecord\b.*\b(?:when|with)\b.*{N_PLUS}|\brecord\b.*{N_PLUS}|\brecord\b.*\b(?:when|with)\b.*\b(?:scored|scores|had|has)\b.*\d+|{N_PLUS}\s*\w*.*\brecord\b", re.IGNORECASE
)
"""A record over a line: "record when he scored 30+".

.. versionadded:: 6.0.0
   ``subject._CHILD_GRAMMARS``' own pattern until Phase 3, step 2.
"""
# A player's games won or lost: his team's record in the games he played,
# which is record_when's read with no threshold ("how many playoff games has
# embiid won?").
CHILD_RECORD_WHEN_GAMES_WON = re.compile(r"\bhow many\b.{0,40}\bgames\b.{0,20}\b(?:won|lost|win|lose)\b", re.IGNORECASE)
"""A player's games won or lost, asked as a count.

.. versionadded:: 6.0.0
   ``subject._CHILD_GRAMMARS``' own pattern until Phase 3, step 2.
"""
CHILD_THRESHOLD_COUNT = re.compile(
    rf"\b(?:how many|most|fewest)\b.*\b(?:games?|times)\b.*{N_PLUS}|\b(?:how many|most|fewest)\b.*{N_PLUS}.*\bgames?\b|\bhow many (?:times|occasions)\b"
    r"|\bgames? (?:with|where|in which)\b.*\b\d+\s+\w+"
    r"|\b\d{1,3}[\s-]*(?:pts?|points?|rebs?|rebounds?|asts?|assists?|steals?|blocks?|threes|3s)[\s-]+games?\b"
    # The paraphrases' shapes (parser-greenfield, step b): "which games had 15 or more assists", "the highest number of
    # 30+ point games", "how many games did he score 30 points or more in".
    rf"|\b(?:which|what) games?\b.*{N_PLUS}|\bnumber of\b.*{N_PLUS}.*\bgames?\b|\bhow many\b.*\bgames?\b.*\b\d{{1,3}}\s+\w+\s+or more\b",
    re.IGNORECASE,
)
"""A count of games over a line.

.. versionadded:: 6.0.0
   ``subject._CHILD_GRAMMARS``' own pattern until Phase 3, step 2.
"""
CHILD_PLAYER_HISTORY = re.compile(
    rf"\b(?:over|for|in|during) the (?:past|last) {N_SEASONS}\b|\b(?:last|past) {N_SEASONS}\b|\bby (?:season|year)\b|\b(?:each|every) (?:season|year)\b"
    r"|\bseason[- ](?:by|over)[- ]season\b|\byear[- ](?:by|over)[- ]year\b|\bfrom (?:year|season) to (?:year|season)\b",
    re.IGNORECASE,
)
"""A player's line season by season.

.. versionadded:: 6.0.0
   ``subject._CHILD_GRAMMARS``' own pattern until Phase 3, step 2.
"""
CHILD_PLAYER_SPLITS = re.compile(r"\bsplits?\b|\bby month\b|\bhome and away\b|\bhome/away\b|\bhome vs\.? away\b|\bmonthly\b", re.IGNORECASE)
"""A player's splits.

.. versionadded:: 6.0.0
   ``subject._CHILD_GRAMMARS``' own pattern until Phase 3, step 2.
"""

# The thirty team nicknames, and the shorthand a question uses for some. Only to
# tell a team from a player in a slot the model filled: "zach lavine vs nuggets"
# came back as player_matchup with players ['Zach LaVine', 'Denver Nuggets'].
TEAM_NICKNAME = re.compile(
    r"\b(?:hawks|celtics|nets|hornets|bulls|cavaliers|cavs|mavericks|mavs|nuggets|pistons|warriors|rockets|pacers|clippers|lakers|"
    r"grizzlies|heat|bucks|timberwolves|wolves|pelicans|knicks|thunder|magic|76ers|sixers|suns|blazers|kings|spurs|raptors|jazz|wizards)\b",
    re.IGNORECASE,
)
"""A team's nickname (or its shorthand: "cavs", "mavs", "wolves", "sixers") as a whole word.

.. versionadded:: 6.0.0
   ``router._TEAM_WORD`` until Phase 3, step 2.
"""

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
#
# A near spelling is NOT matched, and that was measured rather than assumed.
# The feed misspells three teams inside a `players` slot - "taptors",
# "warriners", "blakers" - and `difflib` at cutoff 0.8 reaches the right team
# for all three. It also reaches a team for **16 real player surnames**: Burks
# -> Bucks, Hawkins -> Hawks, Thornton -> Toronto, Gooden -> Golden, Wheat ->
# Heat, Houstan -> Houston, and ten more. Three queries is not worth sixteen,
# and the cutoff cannot separate them - "houstan"/"houston" and
# "taptors"/"raptors" are both one edit in seven characters, ratio 0.857.
TEAM_CITIES: frozenset[str] = frozenset(
    {
        "atlanta", "boston", "brooklyn", "charlotte", "chicago", "cleveland", "dallas", "denver", "detroit",
        "golden state", "houston", "indiana", "los angeles", "memphis", "miami", "milwaukee", "minnesota",
        "new orleans", "new york", "oklahoma city", "orlando", "philadelphia", "phoenix", "portland",
        "sacramento", "san antonio", "toronto", "utah", "washington",
    }
)  # fmt: skip
"""A team's city, matched as a whole name only.

.. versionadded:: 6.0.0
   ``router._TEAM_CITY`` until Phase 3, step 2.
"""
TEAM_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "atl", "bkn", "bos", "cha", "chi", "cle", "dal", "den", "det", "gsw", "hou", "ind", "lac", "lal",
        "mem", "mia", "mil", "min", "nop", "nyk", "okc", "orl", "phi", "phx", "por", "sac", "sas", "tor",
        "uta", "wsh", "was",
    }
)  # fmt: skip
"""A team's three-letter abbreviation, matched as a whole name only.

.. versionadded:: 6.0.0
   ``router._TEAM_ABBREVIATION`` until Phase 3, step 2.
"""

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
SUBJECT_WORDS: frozenset[str] = frozenset({"who", "what", "which", "that", "he", "she", "they", "it", "player", "anyone", "someone", "nobody", "team", "one", "the", "and", "any"})
"""The words a subject-of grammar never reads as a name: the question words and pronouns.

.. versionadded:: 6.0.0
   ``router._SUBJECT_WORDS`` until Phase 3, step 2.
"""
NOT_A_SUBJECT: frozenset[str] = SUBJECT_WORDS | frozenset(
    {
        # A stat's own name sits before "10+ rebound games" in "the most 30+
        # point 10+ rebound games", where it is the first line's noun, not a
        # subject: read as one, "point" resolved to Sir'Dominic Pointer.
        "point", "points", "pt", "pts", "rebound", "rebounds", "reb", "rebs", "assist", "assists", "ast",
        "steal", "steals", "stl", "block", "blocks", "blk", "three", "threes", "double", "triple", "players",
        "has", "have", "having", "his", "her", "their", "its", "these", "those", "some", "many", "how", "much",
        "several", "few", "most", "all", "career", "season", "postseason", "playoff", "playoffs", "regular",
        "such", "no", "every", "each",
        # Function words that can sit directly before a name and are not part
        # of it. These matter for the LEADING word SUBJECT_OF_HIGH captures:
        # "sixers record when maxey had 10+ rebounds" read "when maxey" as the
        # name. Rejecting them as a name in their own right is right too -
        # "with" is the word AGENTS.md records reading as Jeff Withey - and it
        # can only make the count grammars more conservative, which is the
        # safe direction for a guess about a person.
        "when", "while", "if", "with", "without", "vs", "versus", "against", "did", "does", "do", "was", "were",
        "is", "are", "for", "by", "from", "in", "on", "at", "to", "of", "after", "before", "during", "than",
        "then", "but", "or", "per",
        # "single game with the most assists" is one game, not a player named
        # Single: under "X games with" it read "single" as the subject of a
        # single-game high, and asked which Singleton was meant (#260).
        "single", "game", "games", "total", "least", "best", "worst", "highest", "lowest",
        # A period is never the one who scored in it: "display the first
        # quarter scores for the Sixers' most recent 10 games" read "first
        # quarter" as the player who scores, and the 76ers' quarter was
        # answered "No player found matching 'first quarter'".
        "quarter", "quarters", "qtr", "half", "halves", "period", "overtime",
    }
)  # fmt: skip
"""The words the subject-of grammars never read as a name or as its leading word.

Measured against the full routing corpus
(``~/association-research/statmuse-2026-09/feed_queries.txt``): the count
grammar matches exactly the two real cases it was written for and nothing
else - "last 10 games of scottie barnes" does not match because the word
before "games" is "10", and "bam adebayo career games in the month of march"
reads "career" as no name. Without the stat and function words, "bam
adebayo career games" would read "career" as the player and "how many 40+
points games does lebron james have" would read "many" - each a refusal
naming the wrong cause.

.. versionadded:: 6.0.0
   ``router._COUNT_SUBJECT_WORDS`` until Phase 3, step 2.
"""
# The leading word is optional and captured, the way the count grammars below
# capture one, because a one-word subject is a clarifying question where the
# question wrote the name out: measured over all 261 corpus questions, three
# possessives ("kobe bryant's", "Jaden mcdaniel's", "steve adam's") gave a
# bare surname matching four players each, and "bryant" does not even include
# Kobe. Which word may lead is decided by the reader
# (``subject.subject_named_in``) against NOT_A_SUBJECT.
#
# "had"/"has" is a subject position too, and a bare one - SUBJECT_OF_HAVE
# requires a leading "does/did/has/have", so "sixers record when maxey had 10+
# rebounds" named nobody while the same question with "scored" answered. It is
# deliberately NOT in the alternation: the words there are all scoring verbs
# and a possessive, where "had" is ordinary enough that it is only a subject
# position when a threshold follows it, which is what the lookahead asserts.
SUBJECT_OF_HIGH = re.compile(
    r"\b(?:([A-Za-z][A-Za-z.'\-]*)\s+)?([A-Za-z][A-Za-z.'\-]{2,})"
    r"(?:'s\b|\s+(?:scored|scores|score|dropped|put\s+up|hung|shot|foul(?:ed|s|ing)?\s+out)|\s+ha[ds]\s+(?=\d))",
    re.IGNORECASE,
)
"""A name before a scoring verb, "fouled out" or "had <N>", or with a possessive.

.. versionadded:: 6.0.0
   ``router._SUBJECT_OF_HIGH`` until Phase 3, step 2.
"""
# None of SUBJECT_OF_HIGH covers a threshold_count named with no verb at all
# (#148): "jamal murray games with 2 threes including playoffs" fell through
# unrestored, the same shape as "Sga games with under 14 fta in his whole
# career", and (by number instead of "with") a form like "murray 30 point
# games" - none of which puts a scoring verb or a possessive anywhere near the
# name. So a second grammar is tried after the first, anchored on "games"
# itself: a name directly before "games with"/"games of", or before "<N>[+]
# <stat> games", with ONE more word before it for a first name ("jamal murray
# games with" reaches Jamal where a bare "murray" is five players). Kept apart
# from SUBJECT_OF_HIGH: a shared pattern let a trailing possessive ("murray's
# games of...") be swallowed whole into the captured word before the "games
# of" alternative applied.
SUBJECT_OF_COUNT = re.compile(
    r"\b(?:([A-Za-z][A-Za-z.'\-]*)\s+)?([A-Za-z][A-Za-z.'\-]{2,})\s+(?:"
    r"games?\s+with\b"
    r"|games?\s+of\b"
    r"|\d+\+?\s*[- ]?\s*(?:point|pt|rebound|reb|assist|ast|steal|stl|block|blk|three)s?\s+games?\b"
    # "bam adebayo career games in the month of march" (yardstick-v2 F096):
    # the model dropped Bam, and none of the shapes above follows a name
    # with "career games".
    r"|career\s+games?\b"
    r")",
    re.IGNORECASE,
)
"""A name before "games with", "games of", "<N> <stat> games" or "career games".

.. versionadded:: 6.0.0
   ``router._SUBJECT_OF_COUNT`` until Phase 3, step 2.
"""
# "how many 40+ point games does lebron james have": the subject sits between
# an auxiliary and "have", nowhere near the count. The model dropped LeBron
# from exactly this question (#148's shape, in a third grammar).
SUBJECT_OF_HAVE = re.compile(r"\b(?:does|did|has|have)\s+(?:([A-Za-z][A-Za-z.'\-]*)\s+)?([A-Za-z][A-Za-z.'\-]{2,})\s+(?:have|had|got|gotten|recorded|posted)\b", re.IGNORECASE)
"""A name between an auxiliary and "have"/"had".

.. versionadded:: 6.0.0
   ``router._SUBJECT_OF_HAVE`` until Phase 3, step 2.
"""

# A router ``player`` that is no name at all: what the model files as the
# player once nothing in its prompt shows a count or a ranking with none ("most
# 30+ point games", "most", "Most Player in 15th Season Played"). No player's
# name holds a digit, a plus sign, a rank word or the word "player"; anchored
# to the START for the rank words, since a real name can end in one ("Travis
# Best" - the trap AGENTS.md records) and none begins so.
NO_NAME_HAS = re.compile(r"\d|\+|^(?:most|fewest|least|top|best|worst|highest|lowest)\b|\bplayers?\b", re.IGNORECASE)
"""What a span holds that no player's name does.

.. versionadded:: 6.0.0
   ``subject._NO_NAME_HAS`` until Phase 3, step 2.
"""
# A player after a versus word is on the OTHER side of the subject's games -
# a condition ("most points by curry vs lebron"), never a second subject -
# wherever the words ask for the subject's GAMES rather than the pair's
# summary: a high, a count, a record, a log, a streak, a history, splits. A
# bare "curry vs lebron" or "curry stats vs lebron" stays the pair.
GAMES_NOT_SUMMARY = re.compile(r"\b(?:game ?logs?|gamelogs?|logs?|each game|by game|game by game|box scores?|most|highest|fewest|lowest|best|worst)\b", re.IGNORECASE)
"""The words that ask for the subject's games - a log, a high, a low - rather than a pair's summary.

.. versionadded:: 6.0.0
   ``subject._GAMES_NOT_SUMMARY`` until Phase 3, step 2.
"""
# "vs", "versus", "against" or "v" and whatever follows. Whether what follows
# is a team is decided against the teams table, not here: "lebron vs kawhi" is
# two players and must stay a comparison.
AGAINST_PHRASE = re.compile(r"\b(?:vs\.?|versus|against|v\.?)\s+(?:the\s+)?(.+)", re.IGNORECASE)
"""A versus word and everything after it, a team set against the subject is read from.

.. versionadded:: 6.0.0
   ``subject._AGAINST`` until Phase 3, step 2.
"""
# "for", "with the" and whatever follows - a player's OWN team, unlike
# AGAINST_PHRASE's opponent. Loose on purpose: a false match ("stats for this
# season") tries "this season" against the teams table and simply fails to
# find one, which costs nothing - the lookup is the real gate. "with" alone is
# not read here: "westbrook stats with the clippers" and "westbrook stats vs
# the clippers" mean different things, but a bare "with" also introduces a
# teammate ("stats with steph curry on the floor"), so only "with the" - which
# a teammate's name never takes - is read as this shape.
FOR_TEAM_PHRASE = re.compile(r"\bfor\s+(?:the\s+)?(.+)|\bwith\s+the\s+(.+)", re.IGNORECASE)
"""A "for"/"with the" and everything after it, a player's own team is read from.

.. versionadded:: 6.0.0
   ``subject._FOR_TEAM`` until Phase 3, step 2.
"""
# "X vs Y", the one structural signal that two SUBJECTS were meant - tighter
# than a compare word on purpose: "compare Jokic's fingerprint to last season"
# compares seasons. Matched whole so a surname containing "vs" does not count.
VERSUS_WORD = re.compile(r"\b(?:vs\.?|versus)\b", re.IGNORECASE)
"""A versus word, whole.

.. versionadded:: 6.0.0
   ``subject._VERSUS`` until Phase 3, step 2.
"""
TEAM_SPELLING_RUN = re.compile(r"[a-zA-Z0-9']+")
"""A run of letters, digits and apostrophes: a word a team may be spelled by
with a digit in it ("76ers"), read whole before its letters are.

.. versionadded:: 6.0.0
"""
LETTER_RUN = re.compile(r"[a-zA-Z']+")
"""A run of letters and apostrophes - the words a team's name is read from.

.. versionadded:: 6.0.0
   ``subject._LETTER_RUN`` until Phase 3, step 2.
"""
WORD_RUN = re.compile(r"\w+")
"""A run of word characters: where a whole word starts and ends (the
boundaries ``\\b`` reads), for a reader that asks whether a word stands whole.

.. versionadded:: 6.0.0
"""
LONG_LETTER_RUN = re.compile(r"[A-Za-z]{4,}")
"""A run of four letters or more: the words of a team's name worth looking for in a question.

.. versionadded:: 6.0.0
   ``router._team_slot_named_in_text``'s pattern until Phase 3, step 2.
"""
PLAYER_NOUN = re.compile(r"\b(?:player|players)\b", re.IGNORECASE)
"""The word "player(s)": a team named beside it is its players as a group ("a Hawks player").

.. versionadded:: 6.0.0
   ``subject._decide``'s pattern until Phase 3, step 2.
"""
TEAM_NOUN = re.compile(r"\bteam\b", re.IGNORECASE)
"""The word "team": beside "player", the team is the subject after all.

.. versionadded:: 6.0.0
   ``subject._decide``'s pattern until Phase 3, step 2.
"""


def whole_phrases(phrases: list[str] | tuple[str, ...]) -> re.Pattern[str]:
    """One pattern finding any of ``phrases`` as a whole phrase, longest
    first so "greek freak" wins over a hypothetical "greek", ignoring case.
    Word boundaries are spelled as lookarounds rather than ``\\b`` because
    several nicknames end in a non-word character ("a.i."), where ``\\b``
    asserts the opposite of what is wanted. The nicknames' reader builds its
    pattern with it (``subject.nicknames_in``), over the curated table the
    names module keeps.

    .. versionadded:: 6.0.0
       ``subject._NICKNAME_RE``'s construction until Phase 3, step 2.
    """
    return re.compile(r"(?<![\w])(" + "|".join(re.escape(k) for k in sorted(phrases, key=len, reverse=True)) + r")(?![\w])", re.IGNORECASE)


MAX_NAME_WORDS = 3
"""A name is never longer than three words once a hyphenated one is split
into its halves ("karl anthony towns"): the longest span the readers of a
player's name try (``subject.players_named_in``, the anchored span a model's
name is read back from) and the most words a companion's name read by
position holds ("Tatum, Brown and Holiday this season" ends each at a
joiner, "a turnover" at a word no name holds).

.. versionadded:: 6.0.0
   ``subject._SPAN_MAX_WORDS`` and ``subject._MAX_NAME_WORDS`` until Phase
   3, step 2: two names for one cap.
"""

#: The singular of a team's nickname names the team: "a hawk player", "a
#: laker". ``entities._TEAM_NICKNAMES`` holds the plural shorthands
#: ("sixers", "mavs"); these are the singulars, plus the one-word spellings
#: the roster table's split cannot find.
TEAM_SINGULARS: dict[str, str] = {
    "hawk": "Atlanta Hawks",
    "celtic": "Boston Celtics",
    "net": "Brooklyn Nets",
    "hornet": "Charlotte Hornets",
    "bull": "Chicago Bulls",
    "cavalier": "Cleveland Cavaliers",
    "maverick": "Dallas Mavericks",
    "nugget": "Denver Nuggets",
    "piston": "Detroit Pistons",
    "warrior": "Golden State Warriors",
    "rocket": "Houston Rockets",
    "pacer": "Indiana Pacers",
    "clipper": "Los Angeles Clippers",
    "laker": "Los Angeles Lakers",
    "grizzly": "Memphis Grizzlies",
    "buck": "Milwaukee Bucks",
    "timberwolf": "Minnesota Timberwolves",
    "pelican": "New Orleans Pelicans",
    "knick": "New York Knicks",
    "sixer": "Philadelphia 76ers",
    "sun": "Phoenix Suns",
    "king": "Sacramento Kings",
    "spur": "San Antonio Spurs",
    "raptor": "Toronto Raptors",
    "wizard": "Washington Wizards",
    "trailblazers": "Portland Trail Blazers",
    "blazers": "Portland Trail Blazers",
    "okc": "Oklahoma City Thunder",
    # The one name written with a digit: the letter split every name is read
    # by makes "76ers" the word "ers", and no team was read from it - "giannis
    # stats vs 76ers this season" answered his whole season.
    "76ers": "Philadelphia 76ers",
}
"""A team's singular nickname, or a one-word spelling, mapped to its name.

.. versionadded:: 4.4.0

.. versionchanged:: 6.0.0
   In the lexicon (``subject.TEAM_SINGULARS`` until Phase 3, step 2).
"""

MONTH_ABBREVIATIONS: frozenset[str] = frozenset({"jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"})
"""The month abbreviations, ordinary words beside the shipped word list's
(``subject._dictionary``): "jan" is no player however many are named Jan.

.. versionadded:: 6.0.0
   ``subject._MONTH_ABBREVIATIONS`` until Phase 3, step 2.
"""

FILLER_PLAYER_WORDS: frozenset[str] = frozenset({"player", "players", "a player", "any player"})
"""What the model writes as the player when the question names nobody -
filler, not a name ("Most points in 15th season played" arrived as
``player: "player"``, yardstick-v2 F099). The subject reading reads none of
them as a player.

.. versionadded:: 5.0.0

.. versionchanged:: 6.0.0
   In the lexicon (``reading.FILLER_PLAYER_WORDS`` until Phase 3, step 2).
"""

NEVER_A_NAME: frozenset[str] = frozenset(
    {"west", "east", "western", "eastern", "someone", "somebody", "anyone", "anybody", "everyone", "everybody", "nobody", "no one", "who", "whoever", "player", "players"}
)
"""Spans the normalizer may emit that name nobody here: a conference ("vs
west" - David, Delonte, Doug and Mario West are players, so the index alone
reads it as one) and the indefinite pronouns ("someone" is one near
spelling from Simone Fontecchio, and a single near spelling defaults).

.. versionadded:: 6.0.0
   ``parse._NEVER_A_NAME`` until Phase 3, step 2.
"""

COMMON_WORDS_THAT_NAME_TEAMS: frozenset[str] = frozenset({"was", "min", "me"})
"""Ordinary English words that collide with a real team's abbreviation,
found the way :data:`COMMON_WORDS_THAT_NAME_PLAYERS` was - measured against
the full routing corpus. A team is matched by its abbreviation with no
length floor (``abbreviation ILIKE ?``), so a bare three-letter word run
through it can resolve to a team nobody meant: "was" is the Washington
Wizards ("What was the highest scoring game by a player this year?"), "min"
the Minnesota Timberwolves (a box-score "20+ min"), and "me" the Memphis
Grizzlies ("show kat's average points for me" read Memphis as his own team,
and answered that he never played for them - plan item 6, step (d), part
3c). Unlike the player list, this one is checked against the SPAN tried
rather than the team's own name, because a team's matched span is often not
a word of its display name at all.

.. versionadded:: 4.4.0

.. versionchanged:: 6.0.0
   In the lexicon (``subject._COMMON_WORDS_THAT_NAME_TEAMS`` until Phase 3, step 2).
"""

COMMON_WORDS_THAT_NAME_PLAYERS: frozenset[str] = frozenset({"best", "head"})
"""Ordinary English words that are also a player's whole surname - measured
against the full routing corpus (the routing check's cases, retired in
5.0.0, plus ``~/association-research/statmuse-2026-09/feed_queries.txt``,
380 questions): "Best true shooting percentage last season?" and "Best
record from 2010-11 to 2018-19 nba" both named Travis Best, and "Celtics vs
Bulls head to head record" named Luther Head - three team or league
questions with no player intended. The narrowest gate that removes them,
grown from what a corpus measurement finds rather than a general dictionary.

.. versionadded:: 4.4.0

.. versionchanged:: 6.0.0
   In the lexicon (``subject._COMMON_WORDS_THAT_NAME_PLAYERS`` until Phase 3, step 2).
"""

# Two teams meeting (the parser's ``_two_teams``, ISSUES.md #235): a team
# subject set against a second team, with a meeting word between them - "vs",
# "played", "head to head", "beat". A window over two teams meeting is still
# their meetings when a record is asked for ("lakers vs mavs record last 10
# home games"); a log word never is.
MEETING_WORDS = re.compile(r"\b(vs\.?|versus|against|play(?:ed|s)?|meet|met|head.to.head|matchup|face[ds]?|beat(?:en)?)\b", re.IGNORECASE)
"""A word that sets two teams meeting.

.. versionadded:: 6.0.0
   ``parse._MEETING`` until Phase 3, step 2.
"""
TWO_TEAMS_LOG_WORDS = re.compile(r"\b(log|gamelog|game log)\b", re.IGNORECASE)
"""A log word: one team's games, never two teams' meetings.

.. versionadded:: 6.0.0
   ``parse._TWO_TEAMS_LOG_WORDS`` until Phase 3, step 2.
"""
TWO_TEAMS_RECORD_WORDS = re.compile(r"\b(record|rec|w-?l|win.loss)\b", re.IGNORECASE)
"""A record word: over a window, still the two teams' meetings.

.. versionadded:: 6.0.0
   ``parse._TWO_TEAMS_RECORD_WORDS`` until Phase 3, step 2.
"""
AS_TYPED_WORD = re.compile(r"[\w'.-]+")
"""A word as the question types it - apostrophes, dots and hyphens kept - a
name the model copied is matched back to (``parse._as_typed``).

.. versionadded:: 6.0.0
   ``parse._AS_TYPED_WORD`` until Phase 3, step 2.
"""

# The words a reading has to account for (Phase 3, step 3). Until then this
# rule was ``scripts/claims_ledger.py``'s alone, the ledger's measure of the
# words whose deletion leaves the reading and the planned query unchanged;
# the parser reads it too now, so the Reading states the words nothing
# claimed (``Reading.unread``) by the same rule the ledger counts them by,
# and the ledger is the check from outside that the two agree.
CONTENT_TOKEN = re.compile(r"\S+")
"""A word as the content rule counts one: a run of anything but white space
(:func:`content_tokens`).

.. versionadded:: 6.0.0
   ``scripts/claims_ledger.py``'s ``_TOKEN`` until Phase 3, step 3.
"""
CONTENT_EDGE = re.compile(r"^[^\w%+']+|[^\w%+']+$")
"""The punctuation a counted word is read without, at either end: a comma,
a question mark, a quote - never a percent sign, a plus or an apostrophe,
which are part of "ts%", "30+" and "curry's".

.. versionadded:: 6.0.0
   ``scripts/claims_ledger.py``'s ``_EDGE`` until Phase 3, step 3.
"""
POSSESSIVE_END = re.compile("['\u2019]s$")
"""A possessive ending, typed with either apostrophe (:func:`without_possessive`).

.. versionadded:: 6.0.0
"""
CONTENT_STOPWORDS: frozenset[str] = frozenset(
    """a an the of in on at to for from by with and or is are was were be been do does did has have had his her their its he she they it
    this that these those what who which how many much me my i show give tell list get find display create generate please can you us nba s vs versus""".split()
) | {"whats", "whos"}  # codespell:ignore whats,whos - the question words as typed with no apostrophe
"""Words that carry no narrowing of their own, which no reading has to
account for. Deliberately short: a word left off this list is counted when
nothing reads it, and a word wrongly on it hides.

.. versionadded:: 6.0.0
   ``scripts/claims_ledger.py``'s ``STOPWORDS`` until Phase 3, step 3.
"""
MODEL_STAT_WORDS: dict[str, re.Pattern[str]] = {
    key: re.compile(words)
    for key, words in {
        "points": r"points?|pts|ppg|scor\w*",
        "rebounds": r"rebounds?|reb|rebs|rpg|boards?",
        "assists": r"assists?|ast|asts|apg|dimes?",
        "steals": r"steals?|stl|stls|spg",
        "blocks": r"blocks?|blk|blks|bpg",
        "turnovers": r"turnovers?|tov|tovs|to",
        "fouls": r"fouls?|pf|pfs",
        "minutes": r"minutes?|mins?|mpg",
        "fieldGoalsMade": r"field|goals?|fgm|fg|made|makes?",
        "fieldGoalsAttempted": r"field|goals?|fga|attempts?|attempted|shots?",
        "fieldGoalPct": r"field|goals?|fg%?|percentage|percent|pct|shooting|%",
        "threePointFieldGoalsMade": r"threes?|3s|3'?s|3pm|3pts?|3-?pt|3-?pointers?|three-?pointers?|pointers?|triples|made|makes?|3|three|point",
        "threePointFieldGoalsAttempted": r"threes?|3s|3pa|3pts?|3-?pt|3-?pointers?|three-?pointers?|pointers?|attempts?|attempted|3|three|point",
        "threePointFieldGoalPct": r"threes?|3s|3p%?|3pt%?|3-?point|three-?point|percentage|percent|pct|shooting|%|3|three|point",
        "twoPointFieldGoalPct": r"twos?|2s|2p%?|2pt%?|2-?pt|2-?point|two-?point|percentage|percent|pct|%|2|two|point",
        "freeThrowsMade": r"free|throws?|ftm|ft|fts|made|makes?",
        "freeThrowsAttempted": r"free|throws?|fta|ft|fts|attempts?|attempted",
        "freeThrowPct": r"free|throws?|ft%?|percentage|percent|pct|%",
        "offensiveRebounds": r"offensive|rebounds?|oreb|orebs|boards?",
        "defensiveRebounds": r"defensive|rebounds?|dreb|drebs|boards?",
        "ts_pct": r"true|shooting|ts%?|percentage|percent|pct|%",
        "efg_pct": r"effective|efg%?|field|goals?|percentage|percent|pct|%",
        "usage_pct": r"usage|usg%?|rate|percentage|pct|%",
        "double_double": r"double-?doubles?|doubles?|dd2?s?",
        "triple_double": r"triple-?doubles?|triples?|doubles?|td3?s?",
        "netpoints": r"net|points?|netpoints?",
        "netpoints_per_100": r"net|points?|netpoints?|per|100|possessions?",
        "netpoints_offense": r"net|points?|netpoints?|offensive|offense",
        "netpoints_defense": r"net|points?|netpoints?|defensive|defense",
        "netpoints_offense_per_100": r"net|points?|netpoints?|offensive|offense|per|100|possessions?",
        "netpoints_defense_per_100": r"net|points?|netpoints?|defensive|defense|per|100|possessions?",
        "wins": r"wins?|won|winning",
        "losses": r"loss(es)?|lost|losing",
        "record": r"record|w-l|wins?|loss(es)?",
        "games_played": r"games?|played|gp",
        "shot_distance": r"shots?|distance|feet|ft|far",
        "points_allowed": r"points?|allowed|allow|opponents?|against|defense",
        "point_differential": r"points?|differential|diff|margin|\+/-|plus-?minus",
    }.items()
}
"""The words that name each stat key the model may pick - what the model's
reply accounts for in the question (:func:`read_by_the_model`). A key
missing here accounts for no word.

.. versionadded:: 6.0.0
   ``scripts/claims_ledger.py``'s ``STAT_WORDS`` until Phase 3, step 3.
"""


def content_tokens(question: str) -> list[tuple[int, int, str]]:
    """Each white-space-separated word of ``question`` as ``(start, end,
    the word lowercased with its leading and trailing punctuation
    dropped)`` - ``start`` and ``end`` the whole word's characters,
    punctuation included.

    .. versionadded:: 6.0.0
       ``scripts/claims_ledger.py``'s ``tokens`` until Phase 3, step 3.
    """
    return [(found.start(), found.end(), CONTENT_EDGE.sub("", found.group()).lower()) for found in CONTENT_TOKEN.finditer(question)]


def without_possessive(word: str) -> str:
    """``word`` without its possessive ending, typed with either apostrophe.

    .. versionadded:: 6.0.0
       ``scripts/claims_ledger.py``'s ``_possessive`` until Phase 3, step 3.
    """
    return POSSESSIVE_END.sub("", word)


def read_by_the_model(word: str, names: tuple[str, ...] | list[str], stat: str) -> bool:
    """Whether the model's reply accounts for ``word``: it is a word of one
    of ``names`` (the names the model copied out of the question, and the
    names the reading settled on), or it names the ``stat`` the model
    picked (:data:`MODEL_STAT_WORDS`). A word the model read is not one the
    reader has to claim: the reply still holds the name after the word is
    gone, so nothing could tell whether the reader read it.

    .. versionadded:: 6.0.0
       ``scripts/claims_ledger.py``'s own until Phase 3, step 3.
    """
    bare = without_possessive(word)
    for name in names:
        if any(bare == without_possessive(CONTENT_EDGE.sub("", part).lower()) for part in name.split()):
            return True
    pattern = MODEL_STAT_WORDS.get(stat)
    return bool(pattern and pattern.fullmatch(bare))


def content_words(question: str, names: tuple[str, ...] | list[str], stat: str) -> list[tuple[int, int, str]]:
    """The words of ``question`` a reading has to account for
    (:func:`content_tokens`): not a function word (:data:`CONTENT_STOPWORDS`),
    and not read by the model (:func:`read_by_the_model`, over ``names`` and
    ``stat``).

    .. versionadded:: 6.0.0
       ``scripts/claims_ledger.py``'s own until Phase 3, step 3.
    """
    return [(start, end, word) for start, end, word in content_tokens(question) if word and without_possessive(word) not in CONTENT_STOPWORDS and not read_by_the_model(word, names, stat)]


CONTENT_SPACES = re.compile(r"\s+")
"""A run of white space, closed to one space where a word was deleted
(:func:`without_word`).

.. versionadded:: 6.0.0
"""


def without_word(question: str, start: int, end: int) -> str:
    """``question`` with the word at ``start``-``end`` deleted and the gap
    it left closed - the claims ledger's probe, and the one a rule asks
    itself whether it needed a word by (:func:`~association.query.span.needed`).

    .. versionadded:: 6.0.0
       ``scripts/claims_ledger.py``'s ``without`` until Phase 3, step 3.
    """
    return CONTENT_SPACES.sub(" ", question[:start] + " " + question[end:]).strip()


def unread_words(question: str, claimed: list[tuple[int, int]], names: tuple[str, ...] | list[str], stat: str) -> tuple[str, ...]:
    """The content words of ``question`` (:func:`content_words`) no stretch
    of ``claimed`` touches - each ``(start, end)`` the characters a reader
    rule consumed - in the question's order: what the reading did not read.

    .. versionadded:: 6.0.0
    """
    return tuple(word for start, end, word in content_words(question, names, stat) if not any(first < end and last > start for first, last in claimed))
