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

from association.season import current_season

MIN_SEASON = 1947
"""The earliest year read as a season: the league's first (1946-47).

Deliberately not a data floor. Those live in :mod:`association.coverage`,
where a season below a table's first one is refused, with the reason. At 1990
this dropped every earlier year before the coverage check could see it, and
"who led the league in scoring in 1980" was answered with the current
season's leaders.

.. versionchanged:: 2.1.0
   Lowered from 1990, and shared with the router rather than copied.
"""

# "2023-24" and "2023-2024" both mean the season ENDING in 2024 - ESPN's
# convention, and the form a hyphen-blind year regex gets exactly one year
# wrong. Matched before the plain-year pass so the span wins.
_SPAN = re.compile(r"\b((?:19|20)\d{2})\s*[-/]\s*((?:19|20)?\d{2})\b")
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_PREVIOUS = re.compile(r"\b(?:last|previous|prior)\s+(?:season|year)\b|\ba year ago\b")
_CURRENT = re.compile(r"\b(?:this|current)\s+(?:season|year)\b|\bso far\b|\bright now\b|\bto date\b")


def _in_range(year: int) -> int | None:
    return year if MIN_SEASON <= year <= current_season() + 1 else None


def season_from_text(text: str) -> int | None:
    """The season a question names, or None if it names none (or names more
    than one - "compare 2023 and 2024" is not this function's call to make)."""
    low = text.lower()

    span = _SPAN.search(low)
    if span:
        start, end = span.group(1), span.group(2)
        if len(end) == 4:
            return _in_range(int(end))
        ending = (int(start) // 100) * 100 + int(end)
        if ending <= int(start):
            ending += 100  # a 1999-00 style rollover
        return _in_range(ending)

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
