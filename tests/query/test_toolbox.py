"""Regression + sanity tests for the Toolbox the local model calls into."""

import json

import duckdb
import pytest

from association.query.toolbox import Toolbox


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1', 'Stephen Curry'), ('2', 'Klay Thompson')")
    con.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('9', 'Golden State Warriors')")
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
    con.close()
    return str(path)


@pytest.fixture
def toolbox(tmp_path, db_path):
    return Toolbox(db_path, tmp_path / "out")


# ---------------- describe_table ----------------


def test_describe_table_known_table(toolbox):
    result = json.loads(toolbox.describe_table("players"))
    cols = {c["column"] for c in result}
    assert {"athlete_id", "display_name"} <= cols


def test_describe_table_unknown_table_is_rejected(toolbox):
    result = toolbox.describe_table("not_a_real_table")
    assert "Unknown table" in result


# ---------------- run_sql ----------------


def test_run_sql_executes_select(toolbox):
    result = json.loads(toolbox.run_sql("SELECT display_name FROM players ORDER BY display_name"))
    names = [r["display_name"] for r in result["rows"]]
    assert names == ["Klay Thompson", "Stephen Curry"]


@pytest.mark.parametrize("query", ["DELETE FROM players", "DROP TABLE players", "INSERT INTO players VALUES ('3','x')"])
def test_run_sql_rejects_non_select_statements(toolbox, query):
    result = toolbox.run_sql(query)
    assert "only read-only" in result


def test_run_sql_allows_with_clause(toolbox):
    result = json.loads(toolbox.run_sql("WITH x AS (SELECT 1 AS n) SELECT n FROM x"))
    assert result["rows"] == [{"n": 1}]


def test_run_sql_enriches_id_columns_with_names(toolbox):
    """Regression: the model kept surfacing raw athlete_id/team_id numbers in
    its answers instead of names. run_sql now auto-resolves any *_id column to
    a matching *_name field so the model always has a name available."""
    result = json.loads(toolbox.run_sql("SELECT athlete_id FROM players WHERE athlete_id = '1'"))
    assert result["rows"][0]["athlete_name"] == "Stephen Curry"


def test_run_sql_reports_db_error_instead_of_raising(toolbox):
    result = toolbox.run_sql("SELECT * FROM not_a_real_table")
    assert "SQL error" in result


# ---------------- render_shot_chart ----------------


def test_render_shot_chart_nickname_matching(toolbox):
    """Regression: "Steph" is not a substring of "Stephen" (whole-phrase ILIKE
    matching failed for this exact query). Per-token AND matching fixes it."""
    result = toolbox.render_shot_chart(player_name="Steph Curry")
    assert "Rendered shot chart for Stephen Curry" in result


def test_render_shot_chart_no_match(toolbox):
    result = toolbox.render_shot_chart(player_name="Nobody Real")
    assert "No player found" in result


def test_render_shot_chart_event_id_overrides_season(toolbox):
    """Regression: season/season_type must be ignored once event_id is given -
    a wrong guessed season used to silently zero out otherwise-correct results."""
    result = toolbox.render_shot_chart(player_name="Curry", event_id="100", season=1999)
    assert "Rendered shot chart" in result
    assert "2/3" in result


def test_render_shot_chart_made_only_filters(toolbox):
    result = toolbox.render_shot_chart(player_name="Curry", made_only=True)
    assert "2/2" in result


def test_render_shot_chart_shot_value_filters(toolbox):
    result = toolbox.render_shot_chart(player_name="Curry", shot_value=2)
    assert "1/1" in result


def test_render_shot_chart_period_filters(toolbox):
    result = toolbox.render_shot_chart(player_name="Curry", period=2)
    assert "1/1" in result


def test_render_shot_chart_writes_html_file(toolbox, tmp_path):
    toolbox.render_shot_chart(player_name="Curry")
    files = list((tmp_path / "out").glob("*.html"))
    assert len(files) == 1
    assert "<svg" in files[0].read_text()
