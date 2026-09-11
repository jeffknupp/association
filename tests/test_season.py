"""Tests for season naming and dating."""

from association.season import eastern_date


def test_a_game_is_dated_by_the_day_it_was_played() -> None:
    """An evening tip is stored as the next UTC day; four answers printed that."""
    assert eastern_date("2026-04-13T00:30Z") == "2026-04-12"
    assert eastern_date("2026-04-12T22:00Z") == "2026-04-12"
    assert eastern_date("2026-04-12") == "2026-04-12"
