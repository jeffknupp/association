"""The calendar narrowings a ``situation`` slot can name, read once for both
relations.

The router files the words after a subject that name a circumstance in one
``situation`` slot - "tuesdays", "in october", "christmas", "since january
31st" - alongside things no game table can filter on ("18 year old", "western
conference", "since returning"). Only the calendar ones are a narrowing of a
relation's games: a weekday, a month, a fixed calendar day, or every game
from a day of the season on. Everything else parses to ``None`` and the
caller refuses it by name, the way it always did - a value that is not read
is not dropped.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from association.query.conditions import _MONTH_NAMES

WEEKDAYS: tuple[str, ...] = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
"""ISO order, Monday first - ``EXTRACT(ISODOW ...)`` numbers them 1-7."""

#: Fixed-date holidays a question names as a day. Thanksgiving moves and is
#: deliberately absent: a date the parser cannot state is one it refuses.
HOLIDAYS: dict[str, tuple[int, int, str]] = {
    "christmas": (12, 25, "Christmas Day"),
    "christmas day": (12, 25, "Christmas Day"),
    "xmas": (12, 25, "Christmas Day"),
    "new year's day": (1, 1, "New Year's Day"),
    "new years day": (1, 1, "New Year's Day"),
    "new year's": (1, 1, "New Year's Day"),
    "halloween": (10, 31, "Halloween"),
    "valentine's day": (2, 14, "Valentine's Day"),
    "valentines day": (2, 14, "Valentine's Day"),
    "mlk day": (1, 15, "MLK Day"),
}

_MONTH = "(?P<month>january|february|march|april|may|june|july|august|september|october|november|december)"
_WEEKDAY = re.compile(r"^(?:on\s+)?(?P<day>monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?$", re.IGNORECASE)
_IN_MONTH = re.compile(rf"^(?:in\s+)?(?:the\s+month\s+of\s+)?{_MONTH}$", re.IGNORECASE)
_SINCE_DAY = re.compile(rf"^(?:since|from|after)\s+(?:the\s+)?{_MONTH}\s+(?P<num>\d{{1,2}})(?:st|nd|rd|th)?$", re.IGNORECASE)  # codespell:ignore nd - an ordinal suffix
_HOLIDAY = re.compile(r"^(?:on\s+)?(?P<name>.+?)$", re.IGNORECASE)


@dataclass(frozen=True)
class CalendarNarrowing:
    """One calendar narrowing: ``kind`` is ``"weekday"`` (``value`` 1-7, ISO),
    ``"month"`` (1-12), ``"day"`` (``(month, day)``) or ``"since_day"``
    (``(month, day)`` - every game from that day of the season on, in the
    calendar year that day falls in for the season). ``label`` is how an
    answer says it: "on Tuesdays", "in October", "on Christmas Day", "since
    January 31".

    .. versionadded:: 4.4.0
    """

    kind: str
    value: Any
    label: str


def parse_situation(text: Any) -> CalendarNarrowing | None:
    """The calendar narrowing ``text`` names, or None when it names none - an
    age, a conference, a division, a return from injury. A None is refused by
    the caller with the value in the message; it is never treated as "no
    narrowing".

    .. versionadded:: 4.4.0
    """
    if not isinstance(text, str) or not text.strip():
        return None
    words = " ".join(text.strip().lower().split())
    m = _WEEKDAY.fullmatch(words)
    if m:
        day = m.group("day")
        return CalendarNarrowing("weekday", WEEKDAYS.index(day) + 1, f"on {day.capitalize()}s")
    m = _IN_MONTH.fullmatch(words)
    if m:
        month = [n.lower() for n in _MONTH_NAMES].index(m.group("month")) + 1
        return CalendarNarrowing("month", month, f"in {_MONTH_NAMES[month - 1]}")
    m = _SINCE_DAY.fullmatch(words)
    if m:
        month = [n.lower() for n in _MONTH_NAMES].index(m.group("month")) + 1
        day = int(m.group("num"))
        if not 1 <= day <= 31:
            return None
        return CalendarNarrowing("since_day", (month, day), f"since {_MONTH_NAMES[month - 1]} {day}")
    m = _HOLIDAY.fullmatch(words)
    if m and m.group("name") in HOLIDAYS:
        month, day, label = HOLIDAYS[m.group("name")]
        return CalendarNarrowing("day", (month, day), f"on {label}")
    return None


def calendar_clause(narrowing: CalendarNarrowing, eastern_date: str, season: str) -> tuple[str, list[Any]]:
    """The WHERE clause for ``narrowing`` over a relation whose game date is
    the Eastern DATE expression ``eastern_date`` and whose season label is
    ``season`` - both column expressions written in code, never question text.

    A "since <day>" is read within each game's own season: October-December
    falls in the season's first calendar year, January on in its second, so
    the clause is built from the game's ``season`` rather than a fixed year.

    .. versionadded:: 4.4.0
    """
    if narrowing.kind == "weekday":
        return f"EXTRACT(ISODOW FROM {eastern_date}) = ?", [narrowing.value]
    if narrowing.kind == "month":
        return f"EXTRACT(MONTH FROM {eastern_date}) = ?", [narrowing.value]
    if narrowing.kind == "day":
        month, day = narrowing.value
        return f"EXTRACT(MONTH FROM {eastern_date}) = ? AND EXTRACT(DAY FROM {eastern_date}) = ?", [month, day]
    if narrowing.kind == "since_day":
        month, day = narrowing.value
        year = f"CASE WHEN ? >= 10 THEN {season} - 1 ELSE {season} END"
        return f"{eastern_date} >= make_date({year}, ?, ?)", [month, month, day]
    raise ValueError(f"no calendar narrowing called {narrowing.kind!r}")  # written in code, so a programming error
