"""Tests for the names a franchise played under, in both the Python and SQL forms."""

import itertools

import duckdb
import pytest

from association.franchises import FRANCHISE_ERAS, era_of, season_name, season_name_sql


@pytest.mark.parametrize(
    ("team_id", "season", "current", "column", "want"),
    [
        ("17", 2005, "Brooklyn Nets", "display_name", "New Jersey Nets"),
        ("17", 2013, "Brooklyn Nets", "display_name", "Brooklyn Nets"),
        ("17", 2012, "BKN", "abbreviation", "NJ"),
        ("3", 2000, "New Orleans Pelicans", "display_name", "Charlotte Hornets"),
        ("3", 2008, "New Orleans Pelicans", "display_name", "New Orleans Hornets"),
        ("30", 2010, "Charlotte Hornets", "display_name", "Charlotte Bobcats"),
        ("25", 2008, "Oklahoma City Thunder", "display_name", "Seattle SuperSonics"),
        ("25", 2009, "OKC", "abbreviation", "OKC"),
        ("29", 1997, "MEM", "abbreviation", "VAN"),
        ("27", 1996, "Washington Wizards", "display_name", "Washington Bullets"),
        # A franchise that was never renamed is left exactly as it is.
        ("2", 1995, "Boston Celtics", "display_name", "Boston Celtics"),
    ],
)
def test_a_team_is_named_as_it_was_that_season(team_id: str, season: int, current: str, column: str, want: str) -> None:
    assert season_name(team_id, season, current, column) == want


def test_an_id_is_renamed_only_when_it_is_that_franchise() -> None:
    """Keyed on the id alone, a table that files another team under "3" had its
    Detroit Pistons renamed to the New Orleans Pelicans. The current value is
    the guard: it must be what the franchise is called today."""
    assert season_name("3", 2008, "Detroit Pistons") == "Detroit Pistons"
    assert season_name("17", 2005, "NJN", "abbreviation") == "NJN"


def test_the_sql_form_agrees_with_the_python_form_on_every_era_boundary() -> None:
    """Two hand-maintained renderings of one table is the shape that drifts, so
    they are checked against each other at every season either side of every
    boundary, in both columns, with the guard both passing and failing."""
    today = {era.team_id: era for era in FRANCHISE_ERAS if era.last_season is None}
    cases = []
    for era in FRANCHISE_ERAS:
        now = today[era.team_id]
        for season in {era.first_season - 1, era.first_season, *([era.last_season, era.last_season + 1] if era.last_season else [2030])}:
            for column, value in (("display_name", now.name), ("abbreviation", now.abbreviation)):
                cases += [(era.team_id, season, value, column), (era.team_id, season, "Somebody Else", column)]
    con = duckdb.connect()
    for team_id, season, current, column in cases:
        got = con.execute(f"SELECT {season_name_sql('t', 's', 'c', column)} FROM (SELECT ? AS t, ? AS s, ? AS c)", [team_id, season, current]).fetchone()
        assert got is not None and got[0] == season_name(team_id, season, current, column), (team_id, season, current, column)


def test_every_franchise_has_exactly_one_name_today_and_no_overlapping_eras() -> None:
    """A season covered by two eras of one id would name it arbitrarily, and an
    id with no current era could never be guarded."""
    for team_id in {era.team_id for era in FRANCHISE_ERAS}:
        eras = sorted((e for e in FRANCHISE_ERAS if e.team_id == team_id), key=lambda e: e.first_season)
        assert sum(e.last_season is None for e in eras) == 1, team_id
        for before, after in itertools.pairwise(eras):
            assert before.last_season is not None and before.last_season < after.first_season, team_id
    assert era_of("17", 2012) is not None and era_of("17", 2012).name == "New Jersey Nets"  # type: ignore[union-attr]
