"""Reading the season a question names, in code rather than from the model.

"Last season" is arithmetic on a calendar, not language understanding, and it
was the slot the router most reliably dropped: "plot Curry's threes from last
season" and "best true shooting percentage last season" both came back with no
season at all, so the answer silently covered the CURRENT season instead.

This does not replace the model's `season`/`season_ref` slots - it takes
precedence over them when it finds an answer, and defers to them when it does
not, so phrasings it has never seen ("in his rookie year", "two seasons ago")
still route as well as they did before. Measured on the router benchmark,
removing those slots from the schema bought nothing elsewhere, so there is no
reason to give up the fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from association.nba.season import current_season

MONTH_NAMES: tuple[str, ...] = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
"""The months by name, January first - the words a question narrows a
season to a month by (``calendar``), and the label a monthly split prints.
On the reader's side since 2026-10-03: ``calendar`` took it from
``conditions``, which loads the team-games relation, so every reader module
loaded the answer side through one tuple of names.

.. versionadded:: 5.0.0
"""

MIN_SEASON = 1947
"""The earliest year read as a season: the league's first (1946-47).

Deliberately not a data floor. Those live in :mod:`association.nba.coverage`,
where a season below a table's first one is refused, with the reason. At 1990
this dropped every earlier year before the coverage check could see it, and
"who led the league in scoring in 1980" was answered with the current
season's leaders.

.. versionchanged:: 2.1.0
   Lowered from 1990, and shared with the router rather than copied.
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
_SPAN = re.compile(
    r"\b(?P<start>(?:19|20)\d{2})\s*[-/]\s*(?P<end>(?:19|20)?\d{2})\b"
    r"|(?<![\d/.:-])\b(?P<short_start>\d{2})(?P<separator>[-/])(?P<short_end>\d{2})\b(?![-/:%]|\.\d)"
)
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_PREVIOUS = re.compile(r"\b(?:last|previous|prior)\s+(?:season|year)\b|\ba year ago\b")
_CURRENT = re.compile(r"\b(?:this|current)\s+(?:season|year)\b|\bso far\b|\bright now\b|\bto date\b")


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
    (``router._NUMERIC_DATE_RANGE``), and a season read from the same
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
    first, and the router's season ranges read theirs with it ("02-03 to
    06-07", "since 2000-01", "2015-18"), so a span reads the same wherever a
    question writes it.

    .. versionadded:: 5.0.0
    """
    spans = []
    for match in _SPAN.finditer(text):
        years = _season_spans_years(match)
        if years is not None:
            spans.append(SeasonSpan(match.start(), match.end(), *years))
    return spans


def _season_spans_years(match: re.Match[str]) -> tuple[int, int, bool] | None:
    """The first year and the season of one :data:`_SPAN` match, and
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

    .. versionchanged:: 5.0.0
       Reads a season written two digits a side, "23-24" and "23/24"
       (:func:`season_spans`).
    """
    low = text.lower()

    spans = season_spans(low)
    if spans:
        return _in_range(spans[0].season)

    years = [y for y in (_in_range(int(m)) for m in _YEAR.findall(low)) if y is not None]
    if len(set(years)) == 1:
        return years[0]
    if years:
        return None

    if _PREVIOUS.search(low):
        return current_season() - 1
    if _CURRENT.search(low):
        return current_season()
    return None
