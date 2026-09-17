"""This project's season-numbering convention (the year a season ENDS)
applied to today's real date - shared between the fetch pipeline (deciding
whether a season-aggregate endpoint is still "live" and needs refreshing)
and the query engine (defaulting an unspecified season to the current one),
so neither package has to depend on the other for it.

.. versionchanged:: 3.0.0
   Moved from ``association.season``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


def current_season() -> int:
    """A season starting in October of year Y is season Y+1, otherwise it's
    the current year (e.g. season=2026 for the 2025-26 season, from October
    2025 through the following September)."""
    today = date.today()  # noqa: DTZ011 - the machine's own calendar date is the one meant: a season turns over in October wherever you are
    return today.year + 1 if today.month >= 10 else today.year


# The day a game was played is its US Eastern date. ESPN stores a tip as UTC, a
# day ahead for any evening game, so a naive date is wrong for most of them.
#
# This used to be a fixed five-hour shift, on the reasoning that EST and EDT
# disagree about a date only between midnight and 1am Eastern and no game tips
# then. True of tips, and false of the stamps that are not tips: when ESPN has a
# game's date but no tip time it stores MIDNIGHT EASTERN - 05:00Z in winter and
# 04:00Z in summer - and the fixed shift moved every summer one to the day
# before. 379 games in 1988-1992 (the whole 1989-1992 postseason among them)
# and 12 in the 2000-2001 postseason printed a day early; the Pistons' 1990
# title clincher on 14 June read 13 June. 2026 has ten 04:00Z stamps too, and
# those are real 11pm tips - in November-March, under EST, where 04:00Z is
# 11pm. So the rule is the real Eastern clock, not an era cutoff.
#
# The US daylight-saving rules are written out here rather than read from a tz
# database, which a machine running a pull may not have (Windows ships none).
# Every day from 1970 to 2040 is checked against zoneinfo by a test.
_DST_RULES: tuple[tuple[int, tuple[int, int], tuple[int, int]], ...] = (
    # (first year the rule applies, first Sunday on or after (month, day) that
    # starts DST, first Sunday on or after (month, day) that ends it)
    (2007, (3, 8), (11, 1)),  # second Sunday of March to first Sunday of November
    (1987, (4, 1), (10, 25)),  # first Sunday of April to last Sunday of October
    (1967, (4, 24), (10, 25)),  # last Sunday of April to last Sunday of October
)

EASTERN_STANDARD_OFFSET_HOURS: int = 5
"""Hours US Eastern Standard Time is behind UTC; daylight time is one fewer.

.. versionadded:: 2.2.0
"""


def _sunday_on_or_after(day: date) -> date:
    return day + timedelta(days=(6 - day.weekday()) % 7)


def eastern_utc_offset_hours(day: date) -> int:
    """Hours US Eastern time is behind UTC at the start of the Eastern calendar
    date ``day``: 4 under daylight time, 5 under standard time.

    Exact for every instant of a UTC date as well, which is what lets
    :func:`eastern_date` pass the UTC date in: the clocks change at 2am local,
    06:00Z or 07:00Z, and only a stamp between 04:00Z and 04:59Z has an Eastern
    date that depends on the answer. (1974 and 1975's year-round daylight time
    is not modeled; nothing in the warehouse is dated then.)

    .. versionadded:: 2.2.0
    """
    for first_year, (start_month, start_day), (end_month, end_day) in _DST_RULES:
        if day.year >= first_year:
            start = _sunday_on_or_after(date(day.year, start_month, start_day))
            end = _sunday_on_or_after(date(day.year, end_month, end_day))
            return EASTERN_STANDARD_OFFSET_HOURS - 1 if start < day <= end else EASTERN_STANDARD_OFFSET_HOURS
    return EASTERN_STANDARD_OFFSET_HOURS


def eastern_date(stamp: object) -> str:
    """The calendar day a game was played, from its stored UTC timestamp:
    ``"2026-04-13T00:30Z"`` becomes ``"2026-04-12"``. Anything that is not a
    timestamp comes back as its first ten characters.

    One definition for every answer that dates a game. Before it, the query
    package held its own copies and four answers printed the UTC day instead.
    :func:`eastern_date_sql` is the same rule for a query to filter or group on.

    .. versionadded:: 2.1.0
    .. versionchanged:: 2.2.0
       Follows daylight time, so a date-only stamp (midnight Eastern, stored as
       04:00Z in summer) is dated the day it names rather than the day before.
    """
    text = str(stamp)
    if "T" not in text:
        return text[:10]
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10]
    return (moment - timedelta(hours=eastern_utc_offset_hours(moment.date()))).date().isoformat()


def _dst_sql(day: str) -> str:
    """SQL true when US daylight time is in force at the start of the DATE ``day``."""

    def _sunday(month: int, dom: int) -> str:
        first = f"make_date(year({day}), {month}, {dom})"
        return f"({first} + CAST((7 - dayofweek({first})) % 7 AS INTEGER))"

    branches = " ".join(f"WHEN year({day}) >= {first_year} THEN {day} > {_sunday(*start)} AND {day} <= {_sunday(*end)}" for first_year, start, end in _DST_RULES)
    return f"(CASE {branches} ELSE false END)"


def eastern_date_sql(column: str) -> str:
    """SQL for the US Eastern calendar DATE of a ``games.date``-shaped column
    (``'2026-01-01T00:30Z'``) - :func:`eastern_date`'s rule, for a query.

    ``column`` is interpolated, so it must be a column reference written in
    code, never a value from a question.

    .. versionadded:: 2.2.0
    """
    moment = f"CAST(replace(replace({column}, 'T', ' '), 'Z', '') AS TIMESTAMP)"
    offset = f"CASE WHEN {_dst_sql(f'CAST(substr({column}, 1, 10) AS DATE)')} THEN {EASTERN_STANDARD_OFFSET_HOURS - 1} ELSE {EASTERN_STANDARD_OFFSET_HOURS} END"
    return f"CAST({moment} - to_hours({offset}) AS DATE)"


def eastern_day_utc_range(day: str) -> tuple[str, str]:
    """The half-open range of ``games.date`` values whose Eastern date is
    ``day`` (``"YYYY-MM-DD"``), as strings: stamps of that one fixed shape
    compare correctly as text, so the range stays a plain indexable filter.

    .. versionadded:: 2.2.0
    """
    first = date.fromisoformat(day)
    following = first + timedelta(days=1)
    start = datetime(first.year, first.month, first.day, tzinfo=timezone.utc) + timedelta(hours=eastern_utc_offset_hours(first))
    end = datetime(following.year, following.month, following.day, tzinfo=timezone.utc) + timedelta(hours=eastern_utc_offset_hours(following))
    return start.strftime("%Y-%m-%dT%H:%MZ"), end.strftime("%Y-%m-%dT%H:%MZ")
