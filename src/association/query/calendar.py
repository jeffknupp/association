"""The calendar narrowings a ``situation`` slot can name, read once for both
relations.

The router files the words after a subject that name a circumstance in one
``situation`` slot - "tuesdays", "in october", "christmas", "since january
31st" - alongside things no game table can filter on ("18 year old", "western
conference", "since returning"). Only the calendar ones are a narrowing of a
relation's games: a weekday, a month, a holiday, or every game
from a day of the season on. Everything else parses to ``None`` and the
caller refuses it by name, the way it always did - a value that is not read
is not dropped.

.. versionadded:: 4.4.0
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

from association.query.conditions import _MONTH_NAMES

WEEKDAYS: tuple[str, ...] = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
"""ISO order, Monday first - ``EXTRACT(ISODOW ...)`` numbers them 1-7."""

# The apostrophe a phone keyboard types: 19 of the 2,285 questions in the
# large StatMuse sample carry one, against 23 with a straight one. A holiday
# spelled with it is the same holiday.
_TYPOGRAPHIC_APOSTROPHE = "\u2019"

_MONTH = "(?P<month>january|february|march|april|may|june|july|august|september|october|november|december)"
_WEEKDAY = re.compile(r"^(?:on\s+)?(?P<day>monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?$", re.IGNORECASE)
_IN_MONTH = re.compile(rf"^(?:in\s+)?(?:the\s+month\s+of\s+)?{_MONTH}$", re.IGNORECASE)
_SINCE_DAY = re.compile(rf"^(?:since|from|after)\s+(?:the\s+)?{_MONTH}\s+(?P<num>\d{{1,2}})(?:st|nd|rd|th)?$", re.IGNORECASE)  # codespell:ignore nd - an ordinal suffix
_SINCE_NUMERIC = re.compile(r"^(?:since|from|after)\s+(?:the\s+)?(?P<date>\d{1,2}/\d{1,2}(?:/\d{2}(?:\d{2})?)?)$", re.IGNORECASE)
_HOLIDAY = re.compile(r"^(?:on\s+)?(?P<name>.+?)$", re.IGNORECASE)

#: A ``situation`` word naming a conference or division, mapped to
#: ``(kind, value)`` - ``value`` the way :func:`association.fetch.parse.parse_team_alignment`
#: stores it (``"Eastern Conference"``, ``"Southeast"``), read once here and
#: nowhere else, the same discipline :data:`HOLIDAYS` keeps for a holiday.
#: ``midwest`` answers a season before the 2004-05 realignment split it into
#: three; asked of a later season it narrows to opponents nobody was ever
#: aligned under, which is a real (empty) answer and not an error - the same
#: as naming a division a team never played in.
_ALIGNMENT_NAMES: dict[str, tuple[str, str]] = {
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

_ALIGNMENT_WORD = "|".join(sorted(_ALIGNMENT_NAMES, key=len, reverse=True))
_ALIGNMENT = re.compile(
    rf"^(?:(?:vs\.?|against|in)\s+)?(?:the\s+)?(?P<name>{_ALIGNMENT_WORD})(?:\s+(?:conference|division))?(?:\s+teams?)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CalendarNarrowing:
    """One calendar narrowing: ``kind`` is ``"weekday"`` (``value`` 1-7, ISO),
    ``"month"`` (1-12), ``"day"`` (``(month, day)``), ``"nth_weekday"``
    (``(month, weekday, n)`` - the nth of an ISO weekday in a month, the day a
    holiday that moves falls on: MLK Day is ``(1, 1, 3)``, the third Monday
    of January), ``"since_day"``
    (``(month, day)`` - every game from that day of the season on, in the
    calendar year that day falls in for the season) or ``"since_date"`` (an
    ISO date - every game from that calendar date on, across seasons: "since
    1/26/20"). ``label`` is how an
    answer says it: "on Tuesdays", "in October", "on Christmas Day", "since
    January 31".

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       ``"since_date"``: a "since" written with a year, one cut across seasons.
       ``"nth_weekday"``: a holiday that falls on a weekday of its month
       rather than on a date.
    """

    kind: str
    value: Any
    label: str


# One row per holiday a question can name as a day: its label, the kind and
# value of the narrowing it is, and every spelling read as it. Built into
# HOLIDAYS below, and from there into the router's own reading of the words
# (`HOLIDAY_WORDS`), so a spelling added here is one the router captures.
_HOLIDAY_SPELLINGS: tuple[tuple[str, str, Any, tuple[str, ...]], ...] = (
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

HOLIDAYS: dict[str, CalendarNarrowing] = {spelling: CalendarNarrowing(kind, value, f"on {label}") for label, kind, value, spellings in _HOLIDAY_SPELLINGS for spelling in spellings}
"""Every spelling of a holiday :func:`parse_situation` reads, mapped to the
narrowing it names: a fixed day ("christmas" is December 25) or, for a
holiday that moves, a weekday of its month ("mlk day" is the third Monday
of January). Keys are lowercase, with straight apostrophes.

.. versionchanged:: 5.0.0
   Maps to a :class:`CalendarNarrowing` rather than ``(month, day, label)``,
   so a holiday that moves can be one: MLK Day is the third Monday of January
   rather than January 15, and Thanksgiving is read. Christmas Eve and New
   Year's Eve are their own days, and "new years" and "martin luther king
   day" are read.
"""

UNREAD_HOLIDAYS: tuple[str, ...] = ("easter",)
"""Holidays a question can name that no day is read for. The router captures
them all the same (:data:`HOLIDAY_WORDS`), so the answer refuses the
holiday by name rather than answering the season it was asked to narrow.
Easter follows the church calendar (the Gregorian computus), which no
narrowing here states.

.. versionadded:: 5.0.0
"""


def _holiday_words_spelling(spelling: str) -> str:
    """One holiday spelling as a regex: any run of spaces between its words,
    and a straight or a typographic apostrophe (U+2019, what a phone types)."""
    return r"\s+".join(re.escape(word).replace("'", f"['{_TYPOGRAPHIC_APOSTROPHE}]") for word in spelling.split())


HOLIDAY_WORDS: str = "|".join(_holiday_words_spelling(name) for name in sorted({*HOLIDAYS, *UNREAD_HOLIDAYS}, key=lambda name: (-len(name), name)))
"""Every holiday spelling :data:`HOLIDAYS` reads and :data:`UNREAD_HOLIDAYS`
refuses, as one regex alternation (no group of its own), longest first so
"christmas eve" is not read as "christmas". The router's ``situation``
reading is built from it, so the words it captures and the words this
module reads cannot drift apart: "valentine's day" was a key here that the
router's own list never captured, and so narrowed nothing (#238).

.. versionadded:: 5.0.0
"""


@dataclass(frozen=True)
class AlignmentNarrowing:
    """One conference or division narrowing: ``kind`` is ``"conference"`` or
    ``"division"``, ``value`` is the name as ``team_alignment`` stores it
    (``"Eastern Conference"``, ``"Southeast"``), ``label`` is how an answer
    says it: "against Eastern Conference teams", "against the Southeast
    Division".

    A sibling of :class:`CalendarNarrowing`, read by :func:`parse_alignment`
    rather than :func:`parse_situation` - kept as a second function rather
    than folded into the first because ``team_record``'s own ``situation``
    reading (``templates/teams.py``, over the standings table rather than
    either relation) calls :func:`parse_situation` directly and types its
    result as :class:`CalendarNarrowing` alone; widening that return type
    would hand it a narrowing standings carries no column for. The shared
    relation steps that DO carry a conference or division
    (:func:`association.query.templates.common.scoped_games`,
    :func:`~association.query.templates.common.league_games`,
    :func:`~association.query.templates.common.team_games`) try both readers.

    .. versionadded:: 4.4.0
    """

    kind: str
    value: str
    label: str


def parse_situation(text: Any) -> CalendarNarrowing | None:
    """The calendar narrowing ``text`` names, or None when it names none - an
    age, a conference, a division, a return from injury. A None is refused by
    the caller with the value in the message; it is never treated as "no
    narrowing". A conference or division is read separately, by
    :func:`parse_alignment`.

    .. versionadded:: 4.4.0

    .. versionchanged:: 5.0.0
       Reads a "since" written in numbers: "since 1/26/20" (a ``since_date``)
       and "since 1/26" (a ``since_day``, as "since January 26"). A holiday
       is whatever :data:`HOLIDAYS` maps it to, a typographic apostrophe
       read as a straight one (U+2019, the one a phone types).
    """
    if not isinstance(text, str) or not text.strip():
        return None
    words = " ".join(text.strip().lower().replace(_TYPOGRAPHIC_APOSTROPHE, "'").split())
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
    m = _SINCE_NUMERIC.fullmatch(words)
    if m:
        return _since_numeric(m.group("date"))
    m = _HOLIDAY.fullmatch(words)
    return HOLIDAYS.get(m.group("name")) if m else None


def _since_numeric(text: str) -> CalendarNarrowing | None:
    """ "1/26/20" or "1/26/2020" as every game from that calendar date on
    (``"since_date"``), and "1/26" with no year as ``"since_day"`` - the same
    day-of-the-season reading "since January 26" gets. US month-first order,
    parsed by :func:`time.strptime`; a day no calendar has
    ("2/30/20") names nothing, and is refused by value by the caller."""
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            day = _calendar_day(text, fmt)
        except ValueError:
            continue
        return CalendarNarrowing("since_date", day.isoformat(), f"since {_MONTH_NAMES[day.month - 1]} {day.day}, {day.year}")
    try:
        # A leap year, so February 29 parses; the year itself is never used.
        undated = _calendar_day(f"{text}/2000", "%m/%d/%Y")
    except ValueError:
        return None
    return CalendarNarrowing("since_day", (undated.month, undated.day), f"since {_MONTH_NAMES[undated.month - 1]} {undated.day}")


def _calendar_day(text: str, fmt: str) -> date:
    """``text`` as the calendar day ``fmt`` spells, by :func:`time.strptime` -
    a day the question typed, never a moment, so no clock or zone is read."""
    parsed = time.strptime(text, fmt)
    return date(parsed.tm_year, parsed.tm_mon, parsed.tm_mday)


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
    if narrowing.kind == "nth_weekday":
        # The nth of a weekday falls on days 7n-6 through 7n of its month in
        # every year - the third Monday of January is the one Monday from the
        # 15th to the 21st - so each game's own date says whether it is the
        # holiday, with no calendar computed per season. Checked against the
        # real MLK Days and Thanksgivings (tests/query/test_calendar.py).
        month, weekday, n = narrowing.value
        return (
            f"EXTRACT(MONTH FROM {eastern_date}) = ? AND EXTRACT(ISODOW FROM {eastern_date}) = ? AND EXTRACT(DAY FROM {eastern_date}) BETWEEN ? AND ?",
            [month, weekday, 7 * n - 6, 7 * n],
        )
    if narrowing.kind == "since_day":
        month, day = narrowing.value
        year = f"CASE WHEN ? >= 10 THEN {season} - 1 ELSE {season} END"
        return f"{eastern_date} >= make_date({year}, ?, ?)", [month, month, day]
    if narrowing.kind == "since_date":
        return f"{eastern_date} >= CAST(? AS DATE)", [narrowing.value]
    raise ValueError(f"no calendar narrowing called {narrowing.kind!r}")  # written in code, so a programming error


def parse_alignment(text: Any) -> AlignmentNarrowing | None:
    """The conference or division ``text`` names as an opponent narrowing -
    "vs the west", "against eastern conference teams", "vs southeast
    division", "in the west" - or None when it names neither (an age, "since
    returning", or a conference/division word in a shape not listed here, such
    as a bare "conference" with no side named). A None is refused by the
    caller with the value in the message, the same discipline
    :func:`parse_situation` keeps for a calendar value.

    Deliberately narrow, the same way :data:`HOLIDAYS` is: only the fixed
    vocabulary of :data:`_ALIGNMENT_NAMES`, with an optional leading "vs"/
    "against"/"in", an optional "the", and an optional trailing "conference"/
    "division"/"team(s)" - never a name pulled from free text, since a
    conference or division is a closed set of eleven words and nothing here
    guesses at a twelfth.

    .. versionadded:: 4.4.0
    """
    if not isinstance(text, str) or not text.strip():
        return None
    words = " ".join(text.strip().lower().split())
    m = _ALIGNMENT.fullmatch(words)
    if not m:
        return None
    kind, value = _ALIGNMENT_NAMES[m.group("name")]
    label = f"against {value} teams" if kind == "conference" else f"against the {value} Division"
    return AlignmentNarrowing(kind, value, label)


def alignment_clause(narrowing: AlignmentNarrowing, opponent_team_id: str, season: str) -> tuple[str, list[Any]]:
    """The WHERE clause for ``narrowing`` over a relation whose opponent team
    id is the column expression ``opponent_team_id`` and whose season label is
    ``season`` - both written in code, never question text, the same
    convention :func:`calendar_clause` keeps for a date column.

    Alignment is read per SEASON, not fixed: a team's conference or division
    can move (the 2004-05 realignment split the old Midwest into three), so
    "vs the west" narrows to opponents in the WESTERN CONFERENCE OF THAT
    GAME'S OWN SEASON, not today's - the same reasoning
    :func:`association.query.player_games.Narrowed.narrow_calendar` already
    carries for "since <day>" falling in a season's first or second calendar
    year.

    .. versionadded:: 4.4.0
    """
    column = "conference" if narrowing.kind == "conference" else "division"
    return (
        f"EXISTS (SELECT 1 FROM team_alignment ta WHERE ta.team_id = {opponent_team_id} AND ta.season = {season} AND ta.{column} = ?)",
        [narrowing.value],
    )
