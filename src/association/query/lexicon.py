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
:func:`season_from_text`). The other families' words follow, one slice
each (``ROADMAP.md``, "Phase 3, the expected steps", step 2): the window,
the games' cuts, the period, the line and the companions, the subject's
own. Until each moves, its patterns stay in :mod:`association.query.router`
and :mod:`association.query.subject`.

.. versionadded:: 6.0.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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

# A calendar day written the way people write it: "march 17", "Jan 19",
# "november 11 2019". The leading group is what makes a date a RANGE rather
# than a day - "since January 31st" starts a window and names no single game -
# and those are a `situation`, which the calendar reading narrows by. The
# calendar family's words; here since Phase 3, step 2, because a range opened
# on a date WITH a year is also where the span's range starts.
CALENDAR_DATE = re.compile(
    r"(?P<range>\b(?:since|after|before|from|through|until)\s+(?:the\s+)?)?"
    r"\b(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(?P<year>(?:19|20)\d\d))?\b",  # codespell:ignore nd - an ordinal suffix
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
