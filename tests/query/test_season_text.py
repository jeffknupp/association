"""Tests for reading a season out of the question text.

This exists because the router reliably dropped the season slot: "plot Curry's
threes from last season" came back with no season, so the answer silently
covered the current season instead.
"""

import pytest

from association.query.season_text import season_from_text
from association.season import current_season


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
