"""The ``situation`` slot's two readers: a calendar narrowing
(:func:`association.query.calendar.parse_situation`) and - K3-2 - a
conference or division one (:func:`~association.query.calendar.parse_alignment`).
Neither had a dedicated test module before this; each relation's own use of
them is covered end to end in ``tests/query/test_templates.py`` and
``tests/query/test_player_games.py``, so these stay at the level of what a
value parses to and what SQL it produces."""

from __future__ import annotations

import duckdb
import pytest

from association.query.calendar import AlignmentNarrowing, CalendarNarrowing, alignment_clause, calendar_clause, parse_alignment, parse_situation


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
    ],
)
def test_parse_situation_reads_the_calendar_shapes(text: str, kind: str, value: object) -> None:
    narrowing = parse_situation(text)
    assert narrowing is not None
    assert (narrowing.kind, narrowing.value) == (kind, value)


@pytest.mark.parametrize("text", ["18 year old", "western conference", "since returning", "before turning 27", "", "   ", None, 42])
def test_parse_situation_returns_none_for_anything_else(text: object) -> None:
    assert parse_situation(text) is None


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
