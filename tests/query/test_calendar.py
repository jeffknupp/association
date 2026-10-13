"""The ``situation`` slot's two readers: a calendar narrowing
(:func:`association.query.calendar.parse_situation`) and - K3-2 - a
conference or division one (:func:`~association.query.calendar.parse_alignment`).
Neither had a dedicated test module before this; each relation's own use of
them is covered end to end in ``tests/query/test_templates.py`` and
``tests/query/test_player_games.py``, so these stay at the level of what a
value parses to and what SQL it produces."""

from __future__ import annotations

import re
from datetime import date

import duckdb
import pytest

from association.query.calendar import (
    HOLIDAYS,
    AlignmentNarrowing,
    CalendarNarrowing,
    alignment_clause,
    calendar_clause,
    date_range,
    parse_alignment,
    parse_situation,
    season_of_day,
)
from association.query.lexicon import DATE_RANGE, HOLIDAY_WORDS, UNREAD_HOLIDAYS


@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [
        ("Tuesdays", "weekday", 2),
        ("on tuesday", "weekday", 2),
        ("in october", "month", 10),
        ("October", "month", 10),
        ("christmas", "day", (12, 25)),
        ("Christmas Day", "day", (12, 25)),
        ("since january 31st", "since_day", (1, 31)),
        ("from January 31", "since_day", (1, 31)),
        # Written in numbers (yardstick-v2 F110, "... since 1/26/20 vs spurs"):
        # with a year, every game from that calendar date on; without one, the
        # same day-of-the-season reading the month name gets.
        ("since 1/26/20", "since_date", "2020-01-26"),
        ("from 12/25/2019", "since_date", "2019-12-25"),
        ("since 1/26/98", "since_date", "1998-01-26"),
        ("since 1/26", "since_day", (1, 26)),
        ("after 2/29", "since_day", (2, 29)),
        # #238: a holiday is its own day - MLK Day the third Monday of
        # January (not January 15), each Eve the day before, not the day.
        ("mlk day", "nth_weekday", (1, 1, 3)),
        ("on MLK Day", "nth_weekday", (1, 1, 3)),
        ("martin luther king day", "nth_weekday", (1, 1, 3)),
        ("thanksgiving", "nth_weekday", (11, 4, 4)),
        ("christmas eve", "day", (12, 24)),
        ("new year's eve", "day", (12, 31)),
        ("new years", "day", (1, 1)),
        ("valentine's day", "day", (2, 14)),
    ],
)
def test_parse_situation_reads_the_calendar_shapes(text: str, kind: str, value: object) -> None:
    narrowing = parse_situation(text)
    assert narrowing is not None
    assert (narrowing.kind, narrowing.value) == (kind, value)


@pytest.mark.parametrize("text", ["18 year old", "western conference", "since returning", "before turning 27", "since 2/30/20", "since 13/1/20", "easter", "", "   ", None, 42])
def test_parse_situation_returns_none_for_anything_else(text: object) -> None:
    assert parse_situation(text) is None


# Published dates, from the federal calendar - not derived by the rule being checked.
_MLK_DAYS = ("1996-01-15", "2001-01-15", "2018-01-15", "2019-01-21", "2020-01-20", "2021-01-18", "2022-01-17", "2023-01-16", "2024-01-15", "2025-01-20", "2026-01-19")
_THANKSGIVINGS = ("2010-11-25", "2023-11-23", "2024-11-28", "2025-11-27")


def _calendar_days(con: duckdb.DuckDBPyConnection, first: str, last: str) -> None:
    """A table ``g`` of every calendar day from ``first`` to ``last``, with the season each falls in."""
    con.execute(
        "CREATE TABLE g AS SELECT CAST(d AS DATE) AS eastern_date, CAST(year(d) + CASE WHEN month(d) >= 10 THEN 1 ELSE 0 END AS INTEGER) AS season "
        "FROM range(CAST(? AS DATE), CAST(? AS DATE) + INTERVAL 1 DAY, INTERVAL 1 DAY) AS t(d)",
        [first, last],
    )


def test_mlk_day_is_the_third_monday_of_january_in_every_season() -> None:
    """ "on mlk day" used to match every January 15, which is MLK Day in 5 of
    the 33 seasons 1994-2026 (#238). Every day from 1988 to 2040, through the
    clause the relations run: exactly one a season, a Monday, the one the
    published calendar names, and the day the ISSUES.md entry's own
    expression computes - the two rules, written independently, agree."""
    con = duckdb.connect(":memory:")
    _calendar_days(con, "1988-01-01", "2040-12-31")
    sql, params = calendar_clause(HOLIDAYS["mlk day"], "g.eastern_date", "g.season")
    days = [str(r[0]) for r in con.execute(f"SELECT eastern_date FROM g WHERE {sql} ORDER BY 1", params).fetchall()]
    assert len(days) == 2040 - 1988 + 1
    assert set(_MLK_DAYS) <= set(days)
    assert "2026-01-15" not in days and "2025-01-15" not in days
    third_mondays = [str(r[0]) for r in con.execute("SELECT make_date(y, 1, 15) + CAST((8 - isodow(make_date(y, 1, 15))) % 7 AS INTEGER) FROM range(1988, 2041) AS t(y) ORDER BY 1").fetchall()]
    assert days == third_mondays


def test_thanksgiving_is_the_fourth_thursday_of_november() -> None:
    con = duckdb.connect(":memory:")
    _calendar_days(con, "1988-01-01", "2040-12-31")
    sql, params = calendar_clause(HOLIDAYS["thanksgiving"], "g.eastern_date", "g.season")
    days = [str(r[0]) for r in con.execute(f"SELECT eastern_date FROM g WHERE {sql} ORDER BY 1", params).fetchall()]
    assert len(days) == 2040 - 1988 + 1
    assert set(_THANKSGIVINGS) <= set(days)
    assert all(date.fromisoformat(day).isoweekday() == 4 and 22 <= date.fromisoformat(day).day <= 28 for day in days)


@pytest.mark.parametrize(("spelling", "day"), [("christmas eve", "2025-12-24"), ("new year's eve", "2025-12-31"), ("new year's", "2026-01-01"), ("valentine's day", "2026-02-14")])
def test_a_fixed_holiday_is_its_own_day_and_not_the_next(spelling: str, day: str) -> None:
    """ "christmas eve" was captured as "christmas" and "new year's eve" as
    "new year's", each answering the day after the one asked about."""
    con = duckdb.connect(":memory:")
    _calendar_days(con, "2025-10-01", "2026-06-30")
    sql, params = calendar_clause(HOLIDAYS[spelling], "g.eastern_date", "g.season")
    assert [str(r[0]) for r in con.execute(f"SELECT eastern_date FROM g WHERE {sql}", params).fetchall()] == [day]


def test_holiday_words_hold_every_spelling_longest_first() -> None:
    """What the router captures is built from this: every spelling read or
    refused, and an Eve before its day. A typographic apostrophe is the
    parser's to fold before any of it reads the question (ISSUES.md #259,
    ``tests/query/test_parser.py``)."""
    words = re.compile(rf"\b(?:{HOLIDAY_WORDS})\b", re.IGNORECASE)
    for spelling in (*HOLIDAYS, *UNREAD_HOLIDAYS):
        assert words.fullmatch(spelling), spelling
    for text, captured in [
        ("on christmas eve", "christmas eve"),
        ("on new year's eve", "new year's eve"),
        ("on martin luther king jr. day", "martin luther king"),
    ]:
        found = words.search(text)
        assert found is not None and found.group(0) == captured


@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [
        ("vs the west", "conference", "Western Conference"),
        ("against eastern conference teams", "conference", "Eastern Conference"),
        ("vs southeast division", "division", "Southeast"),
        ("in the west", "conference", "Western Conference"),
        ("east", "conference", "Eastern Conference"),
        ("western", "conference", "Western Conference"),
        ("Southeast Division", "division", "Southeast"),
        ("vs. the Pacific Division", "division", "Pacific"),
        ("midwest", "division", "Midwest"),  # answers a pre-2004-05 season; a later one narrows to nobody, not an error
    ],
)
def test_parse_alignment_reads_a_conference_or_division(text: str, kind: str, value: str) -> None:
    narrowing = parse_alignment(text)
    assert narrowing is not None
    assert (narrowing.kind, narrowing.value) == (kind, value)


@pytest.mark.parametrize("text", ["18 year old", "since returning", "the Central Division these days", "conference", "division", "", "   ", None, 42])
def test_parse_alignment_returns_none_for_anything_else(text: object) -> None:
    assert parse_alignment(text) is None


def test_the_two_readers_never_both_match() -> None:
    """A value is a calendar narrowing or a conference/division one, never
    both - the discipline `_apply_situation` (`templates/common.py`) relies
    on to try one reader and then the other, in order, exactly once."""
    calendar_values = ["monday", "tuesdays", "in march", "christmas", "since february 1st"]
    alignment_values = ["vs the west", "against eastern conference teams", "vs southeast division", "midwest"]
    for text in calendar_values:
        assert parse_situation(text) is not None
        assert parse_alignment(text) is None
    for text in alignment_values:
        assert parse_situation(text) is None
        assert parse_alignment(text) is not None


def test_calendar_clause_reads_a_since_day_within_the_games_own_season() -> None:
    narrowing = CalendarNarrowing("since_day", (12, 1), "since December 1")
    sql, params = calendar_clause(narrowing, "g.eastern_date", "g.season")
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE g (eastern_date DATE, season INTEGER)")
    con.executemany("INSERT INTO g VALUES (?, ?)", [("2025-12-15", 2026), ("2026-01-15", 2026), ("2025-11-15", 2026)])
    rows = con.execute(f"SELECT eastern_date FROM g WHERE {sql}", params).fetchall()
    assert sorted(str(r[0]) for r in rows) == ["2025-12-15", "2026-01-15"]


def test_calendar_clause_reads_a_since_date_across_seasons() -> None:
    """A dated "since" is one calendar cut, whatever season each game is in -
    unlike a since_day, which repeats inside every season."""
    narrowing = CalendarNarrowing("since_date", "2020-01-26", "since January 26, 2020")
    sql, params = calendar_clause(narrowing, "g.eastern_date", "g.season")
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE g (eastern_date DATE, season INTEGER)")
    con.executemany("INSERT INTO g VALUES (?, ?)", [("2020-01-25", 2020), ("2020-01-26", 2020), ("2020-10-11", 2020), ("2025-12-16", 2026), ("2021-01-20", 2021)])
    rows = con.execute(f"SELECT eastern_date FROM g WHERE {sql}", params).fetchall()
    assert sorted(str(r[0]) for r in rows) == ["2020-01-26", "2020-10-11", "2021-01-20", "2025-12-16"]


@pytest.mark.parametrize(
    ("text", "first", "last"),
    [
        # ISSUES.md #345: a range of calendar days with their years.
        ("from january 1 2015 to june 2 2021", "2015-01-01", "2021-06-02"),
        ("between october 10 2016 and february 19 2017", "2016-10-10", "2017-02-19"),
        ("from may 10th 2019 to april 30th 2023", "2019-05-10", "2023-04-30"),
        ("from december 1st 2020 to november 28th 2021", "2020-12-01", "2021-11-28"),
        # The first day's year left to the last day's, or the year before it.
        ("feb 28th to april 14th 2026", "2026-02-28", "2026-04-14"),
        ("from december 1 to january 31 2021", "2020-12-01", "2021-01-31"),
    ],
)
def test_parse_situation_reads_a_range_of_calendar_days_across_seasons(text: str, first: str, last: str) -> None:
    narrowing = parse_situation(text)
    assert narrowing is not None and narrowing.kind == "date_range" and narrowing.value == (first, last)
    assert narrowing.label.startswith("from ") and " to " in narrowing.label


@pytest.mark.parametrize(
    "text",
    [
        "jokic march 3 and april 4 2024",  # two days, "and" with no "between"
        "from february 31 2020 to march 3 2020",  # a day no calendar has
        "from june 2 2021 to january 1 2015",  # ends before it starts
    ],
)
def test_a_range_of_days_that_names_no_range_is_none(text: str) -> None:
    match = DATE_RANGE.search(text)
    assert match is not None and date_range(match.groupdict()) is None


@pytest.mark.parametrize(
    ("text", "kind", "value"),
    [
        # ISSUES.md #353: a worded day with its year is that day on, across
        # seasons, as the numeric form is; a month written short is read.
        ("since january 31st 2020", "since_date", "2020-01-31"),
        ("since january 31st, 2015", "since_date", "2015-01-31"),
        ("after dec 11 2023", "since_date", "2023-12-11"),
        ("after dec 11", "since_day", (12, 11)),
        # ISSUES.md #323: a "since" written as an ISO date.
        ("since 2020-01-26", "since_date", "2020-01-26"),
    ],
)
def test_parse_situation_reads_a_worded_since_with_its_year_across_seasons(text: str, kind: str, value: object) -> None:
    narrowing = parse_situation(text)
    assert narrowing is not None and (narrowing.kind, narrowing.value) == (kind, value)


def test_a_day_falls_in_the_season_its_month_belongs_to() -> None:
    assert season_of_day(date(2020, 12, 1)) == 2021
    assert season_of_day(date(2021, 6, 2)) == 2021
    assert season_of_day(date(2015, 10, 1)) == 2016


def test_calendar_clause_reads_a_date_range_across_seasons() -> None:
    """A range of calendar days is one cut, from the first day through the
    last, whatever season each game is in (ISSUES.md #345)."""
    narrowing = CalendarNarrowing("date_range", ("2020-01-26", "2021-01-20"), "from January 26, 2020 to January 20, 2021")
    sql, params = calendar_clause(narrowing, "g.eastern_date", "g.season")
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE g (eastern_date DATE, season INTEGER)")
    con.executemany("INSERT INTO g VALUES (?, ?)", [("2020-01-25", 2020), ("2020-01-26", 2020), ("2020-10-11", 2020), ("2025-12-16", 2026), ("2021-01-20", 2021), ("2021-01-21", 2021)])
    rows = con.execute(f"SELECT eastern_date FROM g WHERE {sql}", params).fetchall()
    assert sorted(str(r[0]) for r in rows) == ["2020-01-26", "2020-10-11", "2021-01-20"]


def test_calendar_clause_rejects_an_unwritten_kind() -> None:
    with pytest.raises(ValueError, match="no calendar narrowing"):
        calendar_clause(CalendarNarrowing("conference", "Eastern Conference", "x"), "g.eastern_date", "g.season")


def test_alignment_clause_reads_the_opponents_row_for_that_season() -> None:
    narrowing = AlignmentNarrowing("division", "Southeast", "against the Southeast Division")
    sql, params = alignment_clause(narrowing, "tg.opponent_id", "tg.season")
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE team_alignment (season INTEGER, team_id VARCHAR, conference VARCHAR, division VARCHAR)")
    con.executemany(
        "INSERT INTO team_alignment VALUES (?, ?, ?, ?)",
        [(2026, "ATL", "Eastern Conference", "Southeast"), (2004, "ATL", "Eastern Conference", "Central")],  # pre-realignment: not Southeast yet
    )
    con.execute("CREATE TABLE tg (event_id VARCHAR, opponent_id VARCHAR, season INTEGER)")
    con.executemany("INSERT INTO tg VALUES (?, ?, ?)", [("g1", "ATL", 2026), ("g2", "ATL", 2004)])
    rows = con.execute(f"SELECT event_id FROM tg WHERE {sql}", params).fetchall()
    assert [r[0] for r in rows] == ["g1"]
    assert params == ["Southeast"]
