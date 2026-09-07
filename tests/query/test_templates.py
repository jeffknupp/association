"""Tests for the deterministic threshold_count template - including the exact
question that failed three times through the tool-calling agent."""

from typing import Any

import duckdb
import pytest

from association.query.templates import TemplateUnsupported, threshold_count
from association.season import current_season


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE player_box_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, points INTEGER, rebounds INTEGER)")
    c.execute("INSERT INTO players VALUES ('1','Luka Doncic'),('2','Shai Gilgeous-Alexander'),('3','Bench Guy')")
    season = current_season()
    rows: list[tuple[Any, ...]] = []
    rows += [("1", season, 2, 35, 5)] * 4          # Luka: 4 games of 30+
    rows += [("2", season, 2, 31, 4)] * 2          # SGA: 2 games of 30+
    rows += [("1", season, 2, 12, 22)] * 3         # Luka: 3 games of 20+ rebounds
    rows += [("3", season, 2, 40, 1)] * 9          # postseason-only below, so excluded
    rows += [("3", season, 3, 40, 1)] * 9
    rows += [("2", season - 1, 2, 40, 1)] * 7      # previous season, excluded by default
    c.executemany("INSERT INTO player_box_stats VALUES (?,?,?,?,?)", rows)
    return c


def test_answers_the_question_the_agent_kept_getting_wrong(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert result.data["leaders"][0] == {"player": "Bench Guy", "games": 9}
    assert {"player": "Luka Doncic", "games": 4} in result.data["leaders"]


def test_defaults_to_the_current_season(con: duckdb.DuckDBPyConnection) -> None:
    # The old path lost this rule to prompt truncation and answered for 2024.
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert result.data["season"] == current_season()


def test_explicit_season_is_honoured(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 40, "season": current_season() - 1})
    assert result.data["leaders"] == [{"player": "Shai Gilgeous-Alexander", "games": 7}]


def test_restricted_to_regular_season(con: duckdb.DuckDBPyConnection) -> None:
    # Bench Guy has 9 regular-season and 9 postseason 40-point games; only the
    # regular-season ones count.
    result = threshold_count(con, {"stat": "points", "threshold": 40})
    assert result.data["leaders"] == [{"player": "Bench Guy", "games": 9}]


def test_filters_to_a_named_player_on_every_token(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "rebounds", "threshold": 20, "player": "Luka Doncic"})
    assert result.data["leaders"] == [{"player": "Luka Doncic", "games": 3}]


def test_unknown_stat_falls_through_instead_of_reaching_sql(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(TemplateUnsupported):
        threshold_count(con, {"stat": "points); DROP TABLE players; --", "threshold": 30})


def test_missing_threshold_falls_through(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(TemplateUnsupported):
        threshold_count(con, {"stat": "points"})


def test_limit_is_clamped(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 1, "limit": 10_000})
    assert len(result.data["leaders"]) <= 50


def test_empty_result_is_reported_as_empty_not_invented(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 999})
    assert result.data["leaders"] == []


def test_answer_is_deterministic_prose_so_no_model_call_is_needed(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert result.answer == (
        f"Bench Guy had the most games with 30+ points in the {current_season()} regular season, with 9. "
        "Next: Luka Doncic (4), Shai Gilgeous-Alexander (2)."
    )


def test_answer_always_names_the_season_explicitly(con: duckdb.DuckDBPyConnection) -> None:
    # The original failure silently answered for 2024 when the user meant the
    # current season; naming it makes that class of mistake visible.
    assert f"{current_season()} regular season" in (threshold_count(con, {"stat": "points", "threshold": 30}).answer or "")


def test_answer_reports_an_empty_result_honestly(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 999})
    assert result.answer == f"No player had a game with 999+ points in the {current_season()} regular season."


def test_answer_for_a_single_named_player(con: duckdb.DuckDBPyConnection) -> None:
    result = threshold_count(con, {"stat": "rebounds", "threshold": 20, "player": "Luka Doncic"})
    assert result.answer == f"Luka Doncic had 3 games with 20+ rebounds in the {current_season()} regular season."


def test_answer_reports_a_tie_as_a_tie(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("INSERT INTO player_box_stats SELECT '1', season, 2, 35, 5 FROM player_box_stats LIMIT 5")
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert "tied for the most" in (result.answer or "")
