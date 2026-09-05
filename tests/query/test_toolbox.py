"""Regression + sanity tests for the Toolbox the local model calls into."""

import json
from pathlib import Path

import duckdb
import pytest

from association.query.toolbox import Toolbox


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1', 'Stephen Curry'), ('2', 'Klay Thompson')")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('9', 'GS', 'Golden State Warriors'), ('20', 'ATL', 'Atlanta Hawks')")
    con.execute(
        "CREATE TABLE shot_chart (event_id VARCHAR, athlete_id VARCHAR, team_id VARCHAR, "
        "period INTEGER, clock VARCHAR, made BOOLEAN, shot_type VARCHAR, "
        "coordinate_x INTEGER, coordinate_y INTEGER, points_attempted INTEGER)"
    )
    con.execute(
        "INSERT INTO shot_chart VALUES "
        "('100', '1', '9', 1, '10:00', true, 'Jump Shot', 25, 20, 3), "
        "('100', '1', '9', 1, '9:00', false, 'Jump Shot', 24, 22, 3), "
        "('100', '1', '9', 2, '8:00', true, 'Layup', 25, 5, 2)"
    )
    con.execute(
        "CREATE TABLE player_season_advanced_stats (season INTEGER, season_type INTEGER, "
        "athlete_id VARCHAR, games_played INTEGER, usage_pct DOUBLE)"
    )
    con.execute(
        "INSERT INTO player_season_advanced_stats VALUES "
        "(2026, 2, '1', 60, 30.0), "  # Curry: sustained role, real leader once qualified
        "(2026, 2, '2', 3, 90.0)"  # Klay: tiny sample, extreme value - must be excluded by default
    )
    con.execute(
        "CREATE TABLE player_season_stats (season INTEGER, season_type INTEGER, athlete_id VARCHAR, "
        "team_id VARCHAR, gamesPlayed INTEGER, avgPoints DOUBLE, avgRebounds DOUBLE, avgAssists DOUBLE, "
        "avgSteals DOUBLE, avgBlocks DOUBLE, avgMinutes DOUBLE)"
    )
    con.execute(
        "INSERT INTO player_season_stats VALUES "
        "(2026, 2, '1', '9', 55, 28.5, 5.5, 6.0, 1.0, 0.4, 34.0), "  # Curry, one team all season
        "(2026, 2, '2', '9', 30, 15.0, 3.0, 2.0, 0.8, 0.3, 28.0), "  # Klay, team stint 1
        "(2026, 2, '2', '20', 20, 12.0, 2.5, 1.5, 0.6, 0.2, 25.0), "  # Klay, team stint 2 (traded)
        "(2026, 2, '2', NULL, 50, 13.8, 2.8, 1.8, 0.7, 0.25, 27.0)"  # Klay, combined row - survives dedup
    )
    con.execute(
        "CREATE TABLE net_points_player (athlete_id VARCHAR, season INTEGER, net_points_season_type VARCHAR, "
        "overall DOUBLE, overall_per_100_poss DOUBLE, total_minutes INTEGER)"
    )
    con.execute(
        "INSERT INTO net_points_player VALUES "
        "('1', 2026, 'Regular Season', 300.0, 9.9, 2200), "  # Curry: high total, high rate, real sample
        "('2', 2026, 'Regular Season', 50.0, 15.0, 40)"  # Klay: tiny minutes, extreme rate - excluded by default
    )
    con.close()
    return str(path)


@pytest.fixture
def toolbox(tmp_path: Path, db_path: str) -> Toolbox:
    return Toolbox(db_path, tmp_path / "out")


# ---------------- describe_table ----------------


def test_describe_table_known_table(toolbox: Toolbox) -> None:
    result = json.loads(toolbox.describe_table("players"))
    cols = {c["column"] for c in result}
    assert {"athlete_id", "display_name"} <= cols


def test_describe_table_unknown_table_is_rejected(toolbox: Toolbox) -> None:
    result = toolbox.describe_table("not_a_real_table")
    assert "Unknown table" in result


# ---------------- run_sql ----------------


def test_run_sql_executes_select(toolbox: Toolbox) -> None:
    result = json.loads(toolbox.run_sql("SELECT display_name FROM players ORDER BY display_name"))
    names = [r["display_name"] for r in result["rows"]]
    assert names == ["Klay Thompson", "Stephen Curry"]


@pytest.mark.parametrize("query", ["DELETE FROM players", "DROP TABLE players", "INSERT INTO players VALUES ('3','x')"])
def test_run_sql_rejects_non_select_statements(toolbox: Toolbox, query: str) -> None:
    result = toolbox.run_sql(query)
    assert "only read-only" in result


def test_run_sql_allows_with_clause(toolbox: Toolbox) -> None:
    result = json.loads(toolbox.run_sql("WITH x AS (SELECT 1 AS n) SELECT n FROM x"))
    assert result["rows"] == [{"n": 1}]


def test_run_sql_enriches_id_columns_with_names(toolbox: Toolbox) -> None:
    """Regression: the model kept surfacing raw athlete_id/team_id numbers in
    its answers instead of names. run_sql now auto-resolves any *_id column to
    a matching *_name field so the model always has a name available."""
    result = json.loads(toolbox.run_sql("SELECT athlete_id FROM players WHERE athlete_id = '1'"))
    assert result["rows"][0]["athlete_name"] == "Stephen Curry"


def test_run_sql_reports_db_error_instead_of_raising(toolbox: Toolbox) -> None:
    result = toolbox.run_sql("SELECT * FROM not_a_real_table")
    assert "SQL error" in result


# ---------------- get_leaderboard ----------------


def test_get_leaderboard_unknown_metric_is_rejected(toolbox: Toolbox) -> None:
    result = toolbox.get_leaderboard(metric="not_a_real_metric")
    assert "unknown metric" in result


def test_get_leaderboard_invalid_season_type_is_rejected(toolbox: Toolbox) -> None:
    result = toolbox.get_leaderboard(metric="usage_pct", season_type=7)
    assert "season_type must be" in result


def test_get_leaderboard_applies_default_minimum_sample(toolbox: Toolbox) -> None:
    """Regression: a real query for top usage rate with no minimum surfaced a
    3-game stint at 90% ahead of a real, sustained 30% leader (confirmed live
    with Izaiah Brockington). Klay's 3-game/90% row must not outrank or even
    appear ahead of Curry's real, qualified 30% once the default floor applies."""
    result = json.loads(toolbox.get_leaderboard(metric="usage_pct", season=2026))
    names = [r["display_name"] for r in result["rows"]]
    assert names == ["Stephen Curry"]
    assert result["min_sample_applied"] == 20


def test_get_leaderboard_min_sample_override_widens_the_pool(toolbox: Toolbox) -> None:
    result = json.loads(toolbox.get_leaderboard(metric="usage_pct", season=2026, min_sample=1))
    names = [r["display_name"] for r in result["rows"]]
    assert names == ["Klay Thompson", "Stephen Curry"]  # Klay's 90% now qualifies and ranks first
    assert result["min_sample_applied"] == 1


def test_get_leaderboard_defaults_to_current_season(monkeypatch: pytest.MonkeyPatch, toolbox: Toolbox) -> None:
    monkeypatch.setattr("association.query.toolbox.current_season", lambda: 2026)
    result = json.loads(toolbox.get_leaderboard(metric="usage_pct"))
    assert result["season"] == 2026
    assert [r["display_name"] for r in result["rows"]] == ["Stephen Curry"]


def test_get_leaderboard_dedups_traded_player_to_one_combined_row(toolbox: Toolbox) -> None:
    """Regression: player_season_stats gives a traded player one row per team
    stint plus one combined row (team_id IS NULL) - without dedup a traded
    player is double/triple-counted. Only the combined row should survive."""
    result = json.loads(toolbox.get_leaderboard(metric="avg_points", season=2026, min_sample=1))
    klay_rows = [r for r in result["rows"] if r["display_name"] == "Klay Thompson"]
    assert len(klay_rows) == 1
    assert klay_rows[0]["value"] == 13.8


def test_get_leaderboard_netpoints_total_translates_season_type_to_string(toolbox: Toolbox) -> None:
    """net_points_player uses its own string net_points_season_type column, not
    the numeric season_type every other table uses - the tool must translate
    the model's normal integer season_type (2) to 'Regular Season' itself."""
    result = json.loads(toolbox.get_leaderboard(metric="netpoints_total", season=2026, season_type=2))
    names = [r["display_name"] for r in result["rows"]]
    assert "Stephen Curry" in names
    assert "Klay Thompson" in names  # no minimum sample for a season total


def test_get_leaderboard_netpoints_per_100_applies_default_minimum_minutes(toolbox: Toolbox) -> None:
    """Regression: NetPoints per-100-possessions is a rate too - a live rerun of
    the same question with --think produced a leaderboard dominated by tiny-
    minutes players once this column was used unqualified. Klay's 40 minutes
    must not qualify at the 500-minute default."""
    result = json.loads(toolbox.get_leaderboard(metric="netpoints_per_100", season=2026))
    names = [r["display_name"] for r in result["rows"]]
    assert names == ["Stephen Curry"]
    assert result["min_sample_applied"] == 500


def test_get_leaderboard_fields_adds_box_score_columns(toolbox: Toolbox) -> None:
    result = json.loads(toolbox.get_leaderboard(metric="usage_pct", season=2026, fields=["points", "rebounds"]))
    row = result["rows"][0]
    assert row["display_name"] == "Stephen Curry"
    assert row["points"] == 28.5


def test_get_leaderboard_unknown_field_is_rejected(toolbox: Toolbox) -> None:
    result = toolbox.get_leaderboard(metric="usage_pct", fields=["not_a_real_field"])
    assert "unknown field" in result


def test_get_leaderboard_team_filter_by_abbreviation(toolbox: Toolbox) -> None:
    """Both Curry and Klay played for the Warriors that season (Klay's first
    of two stints) - both should show up, ranked by their season value."""
    result = json.loads(toolbox.get_leaderboard(metric="avg_points", season=2026, min_sample=1, team="Warriors"))
    names = [r["display_name"] for r in result["rows"]]
    assert names == ["Stephen Curry", "Klay Thompson"]


def test_get_leaderboard_team_filter_includes_traded_player_who_stopped_there(toolbox: Toolbox) -> None:
    """A traded player's DEDUPED/combined row has team_id IS NULL (see the
    dedup test above) - filtering on that column directly would wrongly
    exclude them from every team's roster even though they really did play
    for one of their stint teams. Klay's team_id='20' stint means he must
    still show up (with his combined season row's value) when filtered to
    that team, not be silently dropped."""
    result = json.loads(toolbox.get_leaderboard(metric="avg_points", season=2026, min_sample=1, team="20"))
    names = [r["display_name"] for r in result["rows"]]
    assert names == ["Klay Thompson"]
    assert result["rows"][0]["value"] == 13.8


def test_get_leaderboard_unknown_team_is_rejected(toolbox: Toolbox) -> None:
    result = toolbox.get_leaderboard(metric="avg_points", team="Not A Real Team")
    assert "no team found" in result


def test_get_leaderboard_missing_table_reports_requires_hint(toolbox: Toolbox) -> None:
    """usage_pct/ts_pct/efg_pct only exist if the warehouse was built with
    --advanced-stats - if that view is missing, say so instead of a bare
    DuckDB error the model has no way to act on."""
    result = toolbox.get_leaderboard(metric="ts_pct", season=2026)
    assert "requires: warehouse built with --advanced-stats" in result


# ---------------- render_shot_chart ----------------


def test_render_shot_chart_nickname_matching(toolbox: Toolbox) -> None:
    """Regression: "Steph" is not a substring of "Stephen" (whole-phrase ILIKE
    matching failed for this exact query). Per-token AND matching fixes it."""
    result = toolbox.render_shot_chart(player_name="Steph Curry")
    assert "Rendered shot chart for Stephen Curry" in result


def test_render_shot_chart_no_match(toolbox: Toolbox) -> None:
    result = toolbox.render_shot_chart(player_name="Nobody Real")
    assert "No player found" in result


def test_render_shot_chart_event_id_overrides_season(toolbox: Toolbox) -> None:
    """Regression: season/season_type must be ignored once event_id is given -
    a wrong guessed season used to silently zero out otherwise-correct results."""
    result = toolbox.render_shot_chart(player_name="Curry", event_id="100", season=1999)
    assert "Rendered shot chart" in result
    assert "2/3" in result


def test_render_shot_chart_made_only_filters(toolbox: Toolbox) -> None:
    result = toolbox.render_shot_chart(player_name="Curry", made_only=True)
    assert "2/2" in result


def test_render_shot_chart_shot_value_filters(toolbox: Toolbox) -> None:
    result = toolbox.render_shot_chart(player_name="Curry", shot_value=2)
    assert "1/1" in result


def test_render_shot_chart_period_filters(toolbox: Toolbox) -> None:
    result = toolbox.render_shot_chart(player_name="Curry", period=2)
    assert "1/1" in result


def test_render_shot_chart_writes_html_file(toolbox: Toolbox, tmp_path: Path) -> None:
    toolbox.render_shot_chart(player_name="Curry")
    files = list((tmp_path / "out").glob("*.html"))
    assert len(files) == 1
    assert "<svg" in files[0].read_text()
