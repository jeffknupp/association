"""This project's season-numbering convention (the year a season ENDS)
applied to today's real date - shared between the fetch pipeline (deciding
whether a season-aggregate endpoint is still "live" and needs refreshing)
and the query engine (defaulting an unspecified season to the current one),
so neither package has to depend on the other for it."""

from __future__ import annotations

from datetime import date


def current_season() -> int:
    """A season starting in October of year Y is season Y+1, otherwise it's
    the current year (e.g. season=2026 for the 2025-26 season, from October
    2025 through the following September)."""
    today = date.today()
    return today.year + 1 if today.month >= 10 else today.year
