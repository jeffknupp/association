"""This project's season-numbering convention (the year a season ENDS)
applied to today's real date - shared between the fetch pipeline (deciding
whether a season-aggregate endpoint is still "live" and needs refreshing)
and the query engine (defaulting an unspecified season to the current one),
so neither package has to depend on the other for it."""

from __future__ import annotations

from datetime import date, datetime, timedelta


def current_season() -> int:
    """A season starting in October of year Y is season Y+1, otherwise it's
    the current year (e.g. season=2026 for the 2025-26 season, from October
    2025 through the following September)."""
    today = date.today()
    return today.year + 1 if today.month >= 10 else today.year


# The day a game was played is its US Eastern date. ESPN stores a tip as UTC, a
# day ahead for any evening game, so a naive date is wrong for most of them. A
# fixed five-hour shift rather than a time zone: EST and EDT disagree only about
# a tip between midnight and 1am Eastern, which no NBA game has - see
# fetch.parse._EASTERN_OFFSET for the measurement.
_EASTERN_SHIFT = timedelta(hours=5)


def eastern_date(stamp: object) -> str:
    """The calendar day a game was played, from its stored UTC timestamp:
    ``"2026-04-13T00:30Z"`` becomes ``"2026-04-12"``. Anything that is not a
    timestamp comes back as its first ten characters.

    One definition for every answer that dates a game. Before it, the query
    package held its own copies and four answers printed the UTC day instead.

    .. versionadded:: 2.1.0
    """
    text = str(stamp)
    if "T" not in text:
        return text[:10]
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10]
    return (moment - _EASTERN_SHIFT).date().isoformat()
