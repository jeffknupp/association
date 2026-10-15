"""The team-season relation's reads, called over the team fixture warehouse
(``test_team_readers.team_ctx``): each statement the team-season readers
(``compose.team_stats``) and the team compiler's season total
(``compose.team``) take is built AND run on the relation
(:mod:`association.query.team_seasons`), and handed back read (Phase 4,
step 5: the readers ran each statement themselves until then).
"""

from __future__ import annotations

import pytest
from test_team_readers import S, team_ctx  # noqa: F401 - the fixture, by name

from association.query.answer import AnswerContext
from association.query.team_metrics import TeamRecord
from association.query.team_seasons import (
    read_team_lines,
    read_team_outlook_rows,
    read_team_outlook_snapshots,
    read_team_records,
    read_team_season_total,
    read_team_since_records,
    read_team_venue_rows,
)


def test_team_lines_are_every_teams_line_for_the_season_type_asked(team_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    lines = read_team_lines(team_ctx.con, S, 2)
    assert [line.team for line in lines] == ["Boston Celtics", "New York Knicks", "Oklahoma City Thunder", "San Antonio Spurs", "Washington Wizards"]
    knicks = next(line for line in lines if line.team == "New York Knicks")
    # Three games listed, the Cup final not among them, as the line's own three.
    assert (knicks.games, knicks.listed_games) == (3, 3)
    assert knicks.values["defensive_rating"] == pytest.approx(100 * 343 / (270 - 30 + 42 + 0.44 * 60))
    assert [line.team for line in read_team_lines(team_ctx.con, S, 3)] == ["Boston Celtics", "New York Knicks"]


def test_team_records_are_the_standings_or_a_postseasons_tally(team_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    regular = read_team_records(team_ctx.con, S, 2)
    assert TeamRecord(team="New York Knicks", wins=53, losses=29) in regular
    assert len(regular) == 5
    assert read_team_records(team_ctx.con, S, 3) == [TeamRecord(team="Boston Celtics", wins=1, losses=3), TeamRecord(team="New York Knicks", wins=3, losses=1)]


def test_since_records_keep_a_team_with_no_games(team_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    records = {record.team: (record.wins, record.losses) for record in read_team_since_records(team_ctx.con, 3, S)}
    assert records["New York Knicks"] == (3, 1)
    assert records["Charlotte Hornets"] == (0, 0)
    assert records["Oklahoma City Thunder"] == (0, 0)
    assert len(records) == 6
    # The regular season is its own tally: the Thunder's two wins.
    assert {record.team: (record.wins, record.losses) for record in read_team_since_records(team_ctx.con, 2, S)}["Oklahoma City Thunder"] == (2, 0)


def test_venue_rows_are_the_standings_strings_or_a_postseasons_tally(team_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    assert ("New York Knicks", "30-10") in read_team_venue_rows(team_ctx.con, S, 2, "home")
    assert ("New York Knicks", "22-19") in read_team_venue_rows(team_ctx.con, S, 2, "away")
    assert read_team_venue_rows(team_ctx.con, S, 3, "home") == [("Boston Celtics", 0, 2), ("New York Knicks", 1, 1)]


def test_outlook_snapshots_and_a_teams_row(team_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    snapshots = read_team_outlook_snapshots(team_ctx.con, "18", S)
    assert [(kind, teams, holds) for kind, _stamp, teams, holds in snapshots] == [(5, 3, True), (3, 3, True)]
    assert [holds for *_rest, holds in read_team_outlook_snapshots(team_ctx.con, "2", S)] == [False, False]
    rows = read_team_outlook_rows(team_ctx.con, S, 5, "18")
    assert len(rows) == 1
    # Its rating, and the two teams in the snapshot rated above it.
    assert (rows[0][1], rows[0][-1]) == (7.574, 2)
    assert read_team_outlook_rows(team_ctx.con, S, 2, "18") == []


def test_a_season_total_is_the_column_and_its_games_or_none(team_ctx: AnswerContext) -> None:  # noqa: F811 - the fixture
    assert read_team_season_total(team_ctx.con, "18", S, 2, "points") == (378.0, 3)
    assert read_team_season_total(team_ctx.con, "18", S, 3, "points") == (434.0, 4)
    assert read_team_season_total(team_ctx.con, "30", S, 2, "points") is None
