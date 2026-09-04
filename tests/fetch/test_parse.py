"""Regression + sanity tests for association.fetch.parse.

Fixtures below are minimal, hand-crafted JSON matching the real ESPN response
shapes (confirmed live against the actual API) - not full captured payloads,
which would be large and brittle. Several of these tests exist specifically
because a real question against real data produced wrong/crashing output; see
each docstring for what broke.
"""

import json
from typing import Any

import pytest

from association.fetch import parse

# ---------------- _num ----------------


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        ("", None),
        ("--", None),
        ("-", None),
        ("N/A", None),
        ("44", 44),
        ("44.5", 44.5),
        ("-6", -6),
        ("High", "High"),  # genuinely textual stat value - must pass through, not vanish
        (123, 123),
        (12.0, 12.0),
    ],
)
def test_num(value: Any, expected: Any) -> None:
    assert parse._num(value) == expected


def test_num_empty_string_regression() -> None:
    """Regression: pyarrow.lib.ArrowInvalid: Could not convert '' with type str:
    tried to convert to double. ESPN returns value=None + displayValue="" for
    stats it hasn't computed yet (e.g. early-season BPI projections); this used
    to leak "" into what pyarrow treats as a double column, crashing the write
    once mixed with real numeric rows in the same file."""
    assert parse._num("") is None


# ---------------- _assign_stat ----------------


def test_assign_stat_compound_made_attempted() -> None:
    row: dict[str, Any] = {}
    parse._assign_stat(row, "fieldGoalsMade-fieldGoalsAttempted", "44-88")
    assert row == {"fieldGoalsMade": 44, "fieldGoalsAttempted": 88}


def test_assign_stat_simple() -> None:
    row: dict[str, Any] = {}
    parse._assign_stat(row, "points", "18")
    assert row == {"points": 18}


def test_assign_stat_negative_value_not_mistaken_for_compound() -> None:
    row: dict[str, Any] = {}
    parse._assign_stat(row, "plusMinus", "-6")
    assert row == {"plusMinus": -6}


def test_assign_stat_ignores_missing_name() -> None:
    row: dict[str, Any] = {}
    parse._assign_stat(row, "", "5")
    parse._assign_stat(row, None, "5")
    assert row == {}


# ---------------- parse_teams ----------------


def test_parse_teams() -> None:
    data = {
        "sports": [
            {
                "leagues": [
                    {
                        "teams": [
                            {
                                "team": {
                                    "id": "1",
                                    "uid": "u1",
                                    "abbreviation": "BOS",
                                    "location": "Boston",
                                    "name": "Celtics",
                                    "nickname": "Celtics",
                                    "displayName": "Boston Celtics",
                                    "shortDisplayName": "Celtics",
                                    "slug": "boston-celtics",
                                    "color": "007A33",
                                    "alternateColor": "BA9653",
                                    "logos": [{"href": "http://x/logo.png"}],
                                    "isActive": True,
                                    "isAllStar": False,
                                }
                            }
                        ]
                    }
                ]
            }
        ]
    }
    rows = parse.parse_teams(data)
    assert len(rows) == 1
    row = rows[0]
    assert row["team_id"] == "1"
    assert row["abbreviation"] == "BOS"
    assert row["display_name"] == "Boston Celtics"
    assert row["logo_url"] == "http://x/logo.png"
    assert row["is_active"] is True


def test_parse_teams_handles_missing_data() -> None:
    assert parse.parse_teams(None) == []
    assert parse.parse_teams({}) == []


# ---------------- parse_schedule_event_ids ----------------


def test_parse_schedule_event_ids() -> None:
    data = {"events": [{"id": "1"}, {"id": "2"}, {}]}
    assert parse.parse_schedule_event_ids(data) == ["1", "2"]


def test_parse_schedule_event_ids_handles_missing_data() -> None:
    assert parse.parse_schedule_event_ids(None) == []
    assert parse.parse_schedule_event_ids({}) == []


# ---------------- parse_game_summary ----------------


def _make_game_data(status_name: str = "STATUS_FINAL", completed: bool = True, state: str = "post") -> dict:
    return {
        "header": {
            "id": "401585183",
            "competitions": [
                {
                    "date": "2024-01-15T18:10Z",
                    "neutralSite": False,
                    "conferenceCompetition": False,
                    "status": {"type": {"name": status_name, "completed": completed, "state": state}},
                    "competitors": [
                        {
                            "homeAway": "home",
                            "winner": completed,
                            "score": 116,
                            "team": {"id": "20"},
                            "linescores": [{"displayValue": "30"}, {"displayValue": "28"}],
                        },
                        {
                            "homeAway": "away",
                            "winner": False,
                            "score": 107,
                            "team": {"id": "10"},
                            "linescores": [{"displayValue": "25"}, {"displayValue": "30"}],
                        },
                    ],
                }
            ],
        },
        "gameInfo": {
            "attendance": 19000,
            "venue": {"id": "1", "fullName": "Test Arena", "address": {"city": "Testville", "state": "TS"}},
        },
        "boxscore": {
            "teams": [
                {
                    "team": {"id": "20"},
                    "homeAway": "home",
                    "statistics": [{"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "40-80"}],
                },
                {
                    "team": {"id": "10"},
                    "homeAway": "away",
                    "statistics": [{"name": "fieldGoalsMade-fieldGoalsAttempted", "displayValue": "38-85"}],
                },
            ],
            "players": [
                {
                    "team": {"id": "20"},
                    "statistics": [
                        {
                            "keys": ["minutes", "points", "fieldGoalsMade-fieldGoalsAttempted"],
                            "labels": ["MIN", "PTS", "FG"],
                            "descriptions": ["Minutes", "Points", "FG Made-Attempted"],
                            "athletes": [
                                {
                                    "athlete": {
                                        "id": "3155526",
                                        "displayName": "Test Player",
                                        "shortName": "T. Player",
                                        "jersey": "9",
                                        "position": {"abbreviation": "F", "name": "Forward"},
                                        "headshot": {"href": "http://x/img.png"},
                                    },
                                    "starter": True,
                                    "didNotPlay": False,
                                    "reason": None,
                                    "ejected": False,
                                    "stats": ["31", "18", "7-9"],
                                }
                            ],
                        }
                    ],
                }
            ],
        },
        "plays": [
            {
                "id": "1",
                "period": {"number": 1},
                "clock": {"displayValue": "12:00"},
                "team": {"id": "20"},
                "participants": [{"athlete": {"id": "3155526"}}],
                "type": {"text": "Jump Shot"},
                "text": "Test Player makes 3-pt jumper",
                "homeScore": 3,
                "awayScore": 0,
                "scoringPlay": True,
                "shootingPlay": True,
                "pointsAttempted": 3,
                "coordinate": {"x": 25, "y": 20},
            },
            {
                "id": "2",
                "period": {"number": 1},
                "clock": {"displayValue": "11:40"},
                "team": {"id": "20"},
                "participants": [{"athlete": {"id": "3155526"}}],
                "type": {"text": "Free Throw - 1 of 2"},
                "text": "Test Player makes free throw",
                "homeScore": 4,
                "awayScore": 0,
                "scoringPlay": True,
                "shootingPlay": True,
                "pointsAttempted": 1,
                # ESPN's sentinel "no real coordinate" value for free throws:
                "coordinate": {"x": -214748340, "y": -214748365},
            },
        ],
        "winprobability": [{"playId": "1", "homeWinPercentage": 0.6, "tiePercentage": 0.0}],
    }


def test_parse_game_summary_completed_game() -> None:
    data = _make_game_data()
    result = parse.parse_game_summary(data, season=2024, season_type=2)
    game = result["game"]

    assert game["event_id"] == "401585183"
    assert game["status_completed"] is True
    assert game["status_state"] == "post"
    assert game["home_team_id"] == "20"
    assert game["away_team_id"] == "10"
    assert game["winner_team_id"] == "20"
    assert game["home_score"] == 116
    assert game["home_linescores"] == "30,28"
    assert game["venue_name"] == "Test Arena"

    assert len(result["team_box"]) == 2
    home_team_box = next(t for t in result["team_box"] if t["team_id"] == "20")
    assert home_team_box["fieldGoalsMade"] == 40
    assert home_team_box["fieldGoalsAttempted"] == 80
    assert home_team_box["opponent_team_id"] == "10"

    assert len(result["player_box"]) == 1
    pb = result["player_box"][0]
    assert pb["athlete_id"] == "3155526"
    assert pb["minutes"] == 31
    assert pb["points"] == 18
    assert pb["fieldGoalsMade"] == 7
    assert pb["fieldGoalsAttempted"] == 9
    assert pb["starter"] is True

    assert result["players_seen"]["3155526"]["display_name"] == "Test Player"


def test_parse_game_summary_free_throw_sentinel_coordinate_nulled() -> None:
    """Regression: free throws (and some other plays) carry an ESPN sentinel
    "no real coordinate" value instead of a court position or null. Left
    unfiltered, this produced garbage shot-chart plots and skewed distance
    stats. Real shot coordinates must survive; sentinel ones must become None."""
    data = _make_game_data()
    shots = parse.parse_game_summary(data, 2024, 2)["shot_chart"]
    assert len(shots) == 2

    real_shot = next(s for s in shots if s["play_id"] == "1")
    assert real_shot["coordinate_x"] == 25
    assert real_shot["coordinate_y"] == 20

    ft_shot = next(s for s in shots if s["play_id"] == "2")
    assert ft_shot["coordinate_x"] is None
    assert ft_shot["coordinate_y"] is None


def test_parse_game_summary_postponed_game_not_completed_but_state_post() -> None:
    """Regression: a postponed/cancelled game has completed=False but
    state='post' (permanently settled, not pending) - distinct from a
    still-upcoming game (state='pre'). Conflating the two meant a season could
    never be marked complete, and the same postponed game got re-fetched on
    every single pull run forever."""
    data = _make_game_data(status_name="STATUS_POSTPONED", completed=False, state="post")
    game = parse.parse_game_summary(data, 2024, 2)["game"]
    assert game["status_completed"] is False
    assert game["status_state"] == "post"


def test_parse_game_summary_pending_game() -> None:
    data = _make_game_data(status_name="STATUS_SCHEDULED", completed=False, state="pre")
    game = parse.parse_game_summary(data, 2024, 2)["game"]
    assert game["status_completed"] is False
    assert game["status_state"] == "pre"


def test_parse_game_summary_handles_missing_data() -> None:
    result = parse.parse_game_summary(None, 2024, 2)
    assert result["game"] is None
    assert result["player_box"] == []
    assert result["shot_chart"] == []


# ---------------- parse_standings / parse_team_season_stats / parse_power_index ----------------
# All three share the same "value or displayValue" extraction that used to leak
# empty-string placeholders straight into what should be numeric columns.


def test_parse_standings_empty_stat_value_becomes_none() -> None:
    data = {
        "children": [
            {
                "standings": {
                    "entries": [
                        {
                            "team": {"id": "13"},
                            "stats": [
                                {"name": "wins", "value": 10.0, "displayValue": "10"},
                                {"name": "winpct", "value": None, "displayValue": ""},
                            ],
                        }
                    ]
                }
            }
        ]
    }
    rows, glossary = parse.parse_standings(data, season=2021)
    assert len(rows) == 1
    assert rows[0]["wins"] == 10.0
    assert rows[0]["winpct"] is None
    assert any(g["stat_key"] == "wins" for g in glossary)


def test_parse_standings_dedupes_across_nested_levels() -> None:
    """Standings entries appear at both conference and division level for the
    same teams - only the first (more complete) occurrence should be kept."""
    data = {
        "children": [
            {
                "standings": {"entries": [{"team": {"id": "1"}, "stats": [{"name": "wins", "value": 50.0}]}]},
                "children": [
                    {"standings": {"entries": [{"team": {"id": "1"}, "stats": [{"name": "wins", "value": 999.0}]}]}}
                ],
            }
        ]
    }
    rows, _ = parse.parse_standings(data, season=2021)
    assert len(rows) == 1
    assert rows[0]["wins"] == 50.0


def test_parse_standings_handles_missing_data() -> None:
    rows, glossary = parse.parse_standings(None, season=2021)
    assert rows == []
    assert glossary == []


def test_parse_team_season_stats_empty_value_becomes_none() -> None:
    data = {
        "splits": {
            "categories": [
                {
                    "stats": [
                        {"name": "blocks", "value": 5.0, "displayValue": "5"},
                        {"name": "sosRemaining", "value": None, "displayValue": ""},
                    ]
                }
            ]
        }
    }
    row, glossary = parse.parse_team_season_stats(data, 2021, 2, "13")
    assert row["blocks"] == 5.0
    assert row["sosRemaining"] is None
    assert row["season"] == 2021
    assert row["team_id"] == "13"


def test_parse_team_season_stats_handles_missing_data() -> None:
    row, glossary = parse.parse_team_season_stats(None, 2021, 2, "13")
    assert row is None
    assert glossary == []


def test_parse_power_index_empty_value_becomes_none_and_team_id_extracted_from_ref() -> None:
    data = {
        "items": [
            {
                "team": {"$ref": "http://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/seasons/2021/teams/13?lang=en&region=us"},
                "season": 2021,
                "seasonType": 2,
                "lastUpdated": "2020-12-22T20:58Z",
                "stats": [
                    {"name": "bpi", "value": 6.173, "displayValue": "6.173"},
                    {"name": "winpct", "value": None, "displayValue": ""},
                ],
            }
        ]
    }
    rows, glossary = parse.parse_power_index(data)
    assert len(rows) == 1
    assert rows[0]["team_id"] == "13"
    assert rows[0]["bpi"] == 6.173
    assert rows[0]["winpct"] is None


def test_parse_power_index_handles_missing_data() -> None:
    rows, glossary = parse.parse_power_index(None)
    assert rows == []
    assert glossary == []


def test_parse_player_career_stats_handles_missing_data() -> None:
    rows, glossary = parse.parse_player_career_stats(None, "1", 2)
    assert rows == []
    assert glossary == []


def test_parse_player_career_stats_splits_compound_and_groups_by_season_team() -> None:
    data = {
        "categories": [
            {
                "name": "averages",
                "names": ["gamesPlayed", "avgFieldGoalsMade-avgFieldGoalsAttempted"],
                "displayNames": ["GP", "FG"],
                "descriptions": ["Games Played", "Field Goals"],
                "statistics": [
                    {"season": {"year": 2024}, "teamId": "10", "position": "F", "stats": ["70", "5.0-10.0"]}
                ],
            }
        ]
    }
    rows, glossary = parse.parse_player_career_stats(data, "3155526", season_type=2)
    assert len(rows) == 1
    row = rows[0]
    assert row["athlete_id"] == "3155526"
    assert row["season"] == 2024
    assert row["team_id"] == "10"
    assert row["gamesPlayed"] == 70
    assert row["avgFieldGoalsMade"] == 5.0
    assert row["avgFieldGoalsAttempted"] == 10.0


def test_parse_net_points_player_converts_start_year_to_end_year_season() -> None:
    """NetPoints labels a season by the year it starts (2025 = the 2025-26
    season); this project's convention (used everywhere else) is the year it
    ends - confirmed live against ESPN's own games-played count for the same
    player-season. Off-by-one here would silently misfile every row."""
    data = [
        {
            "dot_com_id": 3975,
            "position": "G",
            "draftYear": 2009,
            "full_nm": "Stephen Curry",
            "tm": "GSW",
            "overall": 121.61,
            "offense": 125.13,
            "defense": -3.52,
            "min_season": 2025,
            "max_season": 2025,
            "seasonType": "Regular Season",
            "net_pts_games": 43,
        }
    ]
    rows = parse.parse_net_points_player(data, team_abbr_to_id={"GS": "9"})
    assert len(rows) == 1
    row = rows[0]
    assert row["athlete_id"] == 3975
    assert row["season"] == 2026
    assert row["net_points_season_type"] == "Regular Season"
    assert row["team_id"] == "9"
    assert row["games"] == 43
    assert row["overall"] == 121.61


def test_parse_net_points_player_translates_mismatched_team_abbreviations() -> None:
    """NetPoints uses its own short codes for several franchises that don't
    match ESPN's own (e.g. GSW vs ESPN's GS, SAN vs ESPN's SA) - confirmed
    live by cross-checking every team. Left untranslated, team_id silently
    ends up None for these teams."""
    data = [{"dot_com_id": 1, "tm": "GSW", "min_season": 2024, "seasonType": "Regular Season", "net_pts_games": 1}]
    rows = parse.parse_net_points_player(data, team_abbr_to_id={"GS": "9"})
    assert rows[0]["team_id"] == "9"


def test_parse_net_points_player_unmapped_team_leaves_team_id_none() -> None:
    data = [{"dot_com_id": 1, "tm": "ZZZ", "min_season": 2024, "seasonType": "Regular Season", "net_pts_games": 1}]
    rows = parse.parse_net_points_player(data, team_abbr_to_id={"GS": "9"})
    assert rows[0]["team_id"] is None


def test_parse_net_points_player_handles_missing_data() -> None:
    assert parse.parse_net_points_player(None, {}) == []


def test_parse_net_points_team_transposes_pandas_orient_columns_json() -> None:
    """team_nba.json nests each stat block as a JSON string in pandas'
    orient="columns" shape ({"col": {"0": v, "1": v}, ...}), not a plain list
    of row dicts - this has to be transposed before it's usable."""
    team4f = json.dumps(
        {
            "teamId": {"0": "GSW", "1": "BOS"},
            "Side": {"0": "Total", "1": "Total"},
            "team_score": {"0": 118.5, "1": 112.0},
            "FB": {"0": -3.5, "1": 1.2},
            "FG2": {"0": 3.6, "1": -1.0},
            "FG3": {"0": 3.9, "1": 0.5},
            "FT": {"0": -6.4, "1": 0.1},
            "Putback": {"0": -3.2, "1": 0.0},
            "REB": {"0": -3.2, "1": 1.1},
            "TOV": {"0": -3.4, "1": -0.2},
            "Total": {"0": -8.0, "1": 1.7},
            "season": {"0": 2025, "1": 2025},
        }
    )
    data = {"team4f": team4f, "bpi_stats": "{}", "plyr_stats": "{}", "team_stats": "{}"}
    rows = parse.parse_net_points_team(data, team_abbr_to_id={"GS": "9", "BOS": "2"})
    assert len(rows) == 2
    assert rows[0]["team_id"] == "9"
    assert rows[0]["season"] == 2026
    assert rows[0]["side"] == "Total"
    assert rows[0]["avg_team_score"] == 118.5
    assert rows[0]["fast_break"] == -3.5
    assert rows[0]["total"] == -8.0
    assert rows[1]["team_id"] == "2"


def test_parse_net_points_team_handles_missing_data() -> None:
    assert parse.parse_net_points_team(None, {}) == []
    assert parse.parse_net_points_team({}, {}) == []
