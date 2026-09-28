"""Tests for reading a season out of the question text.

This exists because the router reliably dropped the season slot: "plot Curry's
threes from last season" came back with no season, so the answer silently
covered the current season instead.
"""

import pytest

from association.nba.season import current_season
from association.query.season_text import season_from_text, season_spans


@pytest.mark.parametrize(
    "question",
    [
        "Plot Curry's threes from last season",
        "Best true shooting percentage last season?",
        "What was the Lakers record last season?",
        "Who led the league in assists a year ago?",
        "top scorers the previous season",
    ],
)
def test_last_season_resolves_to_the_previous_season(question: str) -> None:
    assert season_from_text(question) == current_season() - 1


@pytest.mark.parametrize(
    "question",
    [
        "Who had the most 30+ point games this season?",
        "Most games with 20+ rebounds this year",
        "How many points is Jokic averaging so far?",
        "the current season",
    ],
)
def test_this_season_resolves_to_the_current_season(question: str) -> None:
    assert season_from_text(question) == current_season()


def test_an_explicit_year_wins() -> None:
    assert season_from_text("Most games with 15+ assists in 2024?") == 2024


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("the 2023-24 season", 2024),
        ("the 2023-2024 season", 2024),
        ("stats for 2023 - 24", 2024),
        ("the 1999-00 season", 2000),
    ],
)
def test_a_season_span_means_the_year_it_ends(question: str, expected: int) -> None:
    # ESPN's convention, and the form a hyphen-blind year regex gets wrong.
    assert season_from_text(question) == expected


def test_two_different_years_is_not_ours_to_decide() -> None:
    assert season_from_text("Compare 2023 and 2024") is None


def test_the_same_year_twice_is_still_that_year() -> None:
    assert season_from_text("in 2024, who led 2024 in scoring?") == 2024


def test_no_season_mentioned_returns_none() -> None:
    for question in ("Who leads the league in assists?", "Show me the Knicks last 5 games", "Lakers opening game of the season"):
        assert season_from_text(question) is None


def test_out_of_range_years_are_ignored() -> None:
    assert season_from_text("stats from 1804") is None
    assert season_from_text(f"in {current_season() + 5}") is None


def test_a_numeric_threshold_is_not_a_season() -> None:
    assert season_from_text("Who had the most 30+ point games?") is None
    assert season_from_text("most games with 20+ rebounds") is None


def test_last_n_games_is_not_last_season() -> None:
    # "last 5 games" must not trip the "last ... " pattern.
    assert season_from_text("Show me the Knicks last 5 games") is None


def test_a_year_before_the_data_starts_is_still_read() -> None:
    """The floor here is the league's first season, not the warehouse's. A year below a table's floor has to
    reach coverage.py to be refused with the reason. At 1990 it was dropped, and "who led the league in scoring
    in 1980" was answered with the current season's leaders."""
    assert season_from_text("who led the league in scoring in 1980") == 1980
    assert season_from_text("Bulls record in 1985") == 1985
    assert season_from_text("stats from 1946") is None


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        # #168: every one of these was answered for another season while the
        # short form went unread.
        ("points per game leaders for the 23-24 nba season", 2024),
        ("average PJ Washington last 29 games 23/24 regular season", 2024),
        ("nba leaders in plus minus in 25-26", 2026),
        ("kobe bryant 02-03", 2003),
        ("the 99-00 season", 2000),
        ("stats for 95-96", 1996),
        ("the '23-24 season", 2024),
        ("most bench points by team in 22/23 season nba", 2023),
        # The four-digit form still reads the same.
        ("the 2023-24 season", 2024),
        ("lakers 2023/24", 2024),
    ],
)
def test_a_two_digit_season_span_means_the_year_it_ends(question: str, expected: int) -> None:
    assert season_from_text(question) == expected


def test_a_two_digit_span_takes_the_century_the_league_could_have_played(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "25-26" is 2025-26 and "95-96" 1995-96: the 2000s up to the next
    season, the 1900s past it - and nothing at all before the league's first
    season, so a line like "30-31" names none. Pinned to the 2025-26 season,
    since which century is which moves with the calendar."""
    monkeypatch.setattr("association.query.season_text.current_season", lambda: 2026)
    assert season_from_text("stats for 25-26") == 2026
    assert season_from_text("stats for 26-27") == 2027  # next season: announced schedules are asked about
    assert season_from_text("stats for 27-28") is None  # 1927-28, before the league
    assert season_from_text("stats for 46-47") == 1947
    assert season_from_text("stats for 45-46") is None
    assert season_from_text("shooting 30-31") is None
    assert season_from_text("stats for 99-00") == 2000


@pytest.mark.parametrize(
    "question",
    [
        "celtics record 10-12",  # a record: the second number does not follow the first
        "Lebron 21-13",
        "kobe bryant 00-02",  # a range of seasons, not one season
        "won 102-101",
        "shooting 23.5-24",  # a decimal
        "1:23-24",  # a clock
        "23-24% from three",  # a percentage
        "11/29/24 Al Horford",  # a date's pieces: "29/24" is not a season
        "4/23/24",
        "23-24-25",
    ],
)
def test_a_two_digit_pair_that_is_not_one_season_is_not_read_as_one(question: str) -> None:
    assert season_from_text(question) is None


@pytest.mark.parametrize("question", ["most triple doubles since 12/13", "lebron on 10/11", "since 01/02", "from 11/12"])
def test_a_slash_pair_that_is_also_a_calendar_day_is_the_day(question: str) -> None:
    """ "12/13" is December 13 as much as 2012-13, and the router reads "since
    12/13" as the date (`router._NUMERIC_DATE_RANGE`): a season read from the
    same characters would answer another year's games. A hyphen, or a first
    number no month has, is still a season."""
    assert season_from_text(question) is None


def test_a_hyphen_or_a_first_number_no_month_has_is_still_a_season() -> None:
    assert season_from_text("most triple doubles since 12-13") == 2013
    assert season_from_text("most triple doubles since 22/23") == 2023


def test_season_spans_finds_each_span_where_it_is_written() -> None:
    """The router reads a range and a "since" from where the spans sit: "to"
    between two of them, "since" before one."""
    text = "kobe bryant playoff stats from 02-03 to 2006-07"
    spans = season_spans(text)
    assert [(text[s.start : s.end], s.first, s.season) for s in spans] == [("02-03", 2002, 2003), ("2006-07", 2006, 2007)]
    assert season_from_text(text) == 2003  # the first, as a four-digit range always read; the router reads the range
