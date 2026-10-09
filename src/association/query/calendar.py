"""The calendar narrowings a ``situation`` can name, read once for both
relations.

The cuts tagger files the words after a subject that name a circumstance in
one ``situation`` value - "tuesdays", "in october", "christmas", "since
january 31st" - alongside things no game table can filter on ("18 year old",
"western conference", "since returning"). Only the calendar ones are a
narrowing of a relation's games: a weekday, a month, a holiday, or every
game from a day of the season on. Everything else parses to ``None`` and the
caller refuses it by name, the way it always did - a value that is not read
is not dropped. The words are the lexicon's (:mod:`association.query.lexicon`,
Phase 3, step 2): this module reads them and builds the clauses, and holds
no pattern of its own.

.. versionadded:: 4.4.0

.. versionchanged:: 6.0.0
   Every pattern here moved to the lexicon (``SITUATION_WEEKDAY``,
   ``SITUATION_MONTH``, ``SITUATION_SINCE_DAY``, ``SITUATION_SINCE_NUMERIC``,
   ``ALIGNMENT``, ``ALIGNMENT_NAMES``, ``BARE_MONTH``, ``CONFERENCE_WORDS``,
   ``HOLIDAY_SPELLINGS``, ``HOLIDAY_WORDS``, ``UNREAD_HOLIDAYS``), so the
   module imports no regex engine.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from association.query import lexicon
from association.query.season_text import MONTH_NAMES as _MONTH_NAMES

if TYPE_CHECKING:
    from association.query.reading import Scope

WEEKDAYS: tuple[str, ...] = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
"""ISO order, Monday first - ``EXTRACT(ISODOW ...)`` numbers them 1-7."""

# A holiday spelled with the apostrophe a phone keyboard types ("new year\u2019s
# eve") is the same holiday, and reaches this module as a straight one: the
# parser folds it where a question enters (parse.read_route and
# parse.reading_from_route, ISSUES.md #259), so no reader here keeps a
# second, local fold.


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


HOLIDAYS: dict[str, CalendarNarrowing] = {spelling: CalendarNarrowing(kind, value, f"on {label}") for label, kind, value, spellings in lexicon.HOLIDAY_SPELLINGS for spelling in spellings}
"""Every spelling of a holiday :func:`parse_situation` reads, mapped to the
narrowing it names: a fixed day ("christmas" is December 25) or, for a
holiday that moves, a weekday of its month ("mlk day" is the third Monday
of January). Keys are lowercase, with straight apostrophes. Built from the
lexicon's one table of spellings (:data:`~association.query.lexicon.HOLIDAY_SPELLINGS`),
which the situation pattern captures from too, so a spelling added there is
one the tagger captures and this reads.

.. versionchanged:: 5.0.0
   Maps to a :class:`CalendarNarrowing` rather than ``(month, day, label)``,
   so a holiday that moves can be one: MLK Day is the third Monday of January
   rather than January 15, and Thanksgiving is read. Christmas Eve and New
   Year's Eve are their own days, and "new years" and "martin luther king
   day" are read.

.. versionchanged:: 6.0.0
   The spellings are the lexicon's (``HOLIDAY_SPELLINGS``), as are
   ``HOLIDAY_WORDS`` and ``UNREAD_HOLIDAYS``.
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
    (:func:`association.query.player_relation.scoped_games`,
    :func:`~association.query.player_relation.league_games`,
    :func:`~association.query.team_relation.team_games`) try both readers.

    .. versionadded:: 4.4.0
    """

    kind: str
    value: str
    label: str


def bare_month(situation: str | None) -> int | None:
    """The calendar month a ``situation`` of exactly "in <month>" names
    ("in october" -> 10), or None for anything else - a weekday, a holiday,
    "since <day>", or no month at all (:data:`~association.query.lexicon.BARE_MONTH`,
    anchored to that exact shape, so "since january 31st" - a window - is not
    mistaken for one). A team's record filters a bare month by the game's
    own Eastern date; anything else is a fuller calendar narrowing
    (:func:`parse_situation`).

    .. versionadded:: 5.0.0
       ``templates.teams._team_record_month`` was this.
    """
    if situation is None:
        return None
    match = lexicon.BARE_MONTH.fullmatch(situation.strip())
    if match is None:
        return None
    return lexicon.month_number(match.group("month"))


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
       is whatever :data:`HOLIDAYS` maps it to. A typographic apostrophe
       reaches it straight, folded where the question entered the parser
       (:func:`association.query.parse.read_route`).
    """
    if not isinstance(text, str) or not text.strip():
        return None
    words = " ".join(text.strip().lower().split())
    m = lexicon.SITUATION_WEEKDAY.fullmatch(words)
    if m:
        day = m.group("day")
        return CalendarNarrowing("weekday", WEEKDAYS.index(day) + 1, f"on {day.capitalize()}s")
    m = lexicon.SITUATION_MONTH.fullmatch(words)
    if m:
        month = lexicon.month_number(m.group("month"))
        return CalendarNarrowing("month", month, f"in {_MONTH_NAMES[month - 1]}")
    m = lexicon.SITUATION_SINCE_DAY.fullmatch(words)
    if m:
        month = lexicon.month_number(m.group("month"))
        day = int(m.group("num"))
        if not 1 <= day <= 31:
            return None
        return CalendarNarrowing("since_day", (month, day), f"since {_MONTH_NAMES[month - 1]} {day}")
    m = lexicon.SITUATION_SINCE_NUMERIC.fullmatch(words)
    if m:
        return _since_numeric(m.group("date"))
    # A holiday, with or without "on" before it.
    return HOLIDAYS.get(words.removeprefix("on ") if words.startswith("on ") else words)


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
    vocabulary of :data:`~association.query.lexicon.ALIGNMENT_NAMES`, in
    the shape :data:`~association.query.lexicon.ALIGNMENT` reads - never a
    name pulled from free text, since a conference or division is a closed
    set of eleven words and nothing here guesses at a twelfth.

    .. versionadded:: 4.4.0
    """
    if not isinstance(text, str) or not text.strip():
        return None
    words = " ".join(text.strip().lower().split())
    m = lexicon.ALIGNMENT.fullmatch(words)
    if not m:
        return None
    kind, value = lexicon.ALIGNMENT_NAMES[m.group("name")]
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


def conference_named(scope: Scope) -> str | None:
    """The team slot (``team``, the cuts' ``opponent`` or one of ``teams``)
    that holds a conference or a division rather than a team
    (:data:`~association.query.lexicon.CONFERENCE_WORDS`: as the team a
    record or a line is about, "who leads the east", nothing reads a
    conference's own standings yet, ISSUES.md #25), or None - a team's
    record is refused naming it (``compose.say.say_conference_refusal``);
    resolved as a team it would match nothing and be refused for the wrong
    cause.

    .. versionadded:: 5.0.0
       ``templates.teams._conference_refusal``'s reading.

    .. versionchanged:: 6.0.0
       Lives here, beside :func:`parse_alignment`, which reads the same
       words as an opponent narrowing: it was ``refusals.conference_named``,
       and ``refusals`` is gone (Phase 3, step 0).
    """
    return next((c for c in (scope.team, scope.cuts.opponent, *scope.teams) if c and lexicon.CONFERENCE_WORDS.search(c)), None)
