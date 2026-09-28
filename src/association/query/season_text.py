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
    """One season a question writes as the two calendar years it spans,
    found by :func:`season_spans`: ``first`` is the year it starts in and
    ``season`` the one it ends in, this project's number for it - "2023-24"
    and "23/24" are both ``first`` 2023, ``season`` 2024. ``start`` and
    ``end`` are where the span sits in the text, for a reader that needs
    what is beside it: "to" between two of them, "since" before one.

    .. versionadded:: 5.0.0
    """

    start: int
    end: int
    first: int
    season: int


def season_spans(text: str) -> list[SeasonSpan]:
    """Every season ``text`` writes as a span, in the order it writes them.

    A span of four-digit years reads as it always has: "2000-05" is season
    2005, and a year outside the league's is kept, for the caller to
    refuse. A short one ("23-24", "23/24") is a season only where the second
    year follows the first - "10-12" is a record; in the century that makes
    it one the league could have played - "25-26" is 2025-26, "95-96" is
    1995-96, and "30-31" is none; and, written with a slash, only where it
    is not also a month and its day. "12/13" is December 13 to the router,
    which reads "since 12/13" as that date (``router._NUMERIC_DATE_RANGE``),
    and a season read from the same characters would answer another year's
    games.

    The one definition of a season span: :func:`season_from_text` reads the
    first, and the router's season ranges read theirs with it ("02-03 to
    06-07", "since 2000-01"), so the short form reads the same wherever a
    question writes it.

    .. versionadded:: 5.0.0
    """
    spans = []
    for match in _SPAN.finditer(text):
        years = _season_spans_years(match)
        if years is not None:
            spans.append(SeasonSpan(match.start(), match.end(), *years))
    return spans


def _season_spans_years(match: re.Match[str]) -> tuple[int, int] | None:
    """The first calendar year and the season of one :data:`_SPAN` match,
    or None where a short pair is not a season (:func:`season_spans`)."""
    if match.group("start") is not None:
        first, end = int(match.group("start")), match.group("end")
        if len(end) == 4:
            return first, int(end)
        ending = first // 100 * 100 + int(end)
        return first, ending + 100 if ending <= first else ending  # "1999-00" turns the century
    short_first, short_end = int(match.group("short_start")), int(match.group("short_end"))
    if (short_end - short_first) % 100 != 1:
        return None
    if match.group("separator") == "/" and 1 <= short_first <= 12:
        return None
    first = 2000 + short_first if 2000 + short_first <= current_season() else 1900 + short_first
    return (first, first + 1) if _in_range(first + 1) is not None else None


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
