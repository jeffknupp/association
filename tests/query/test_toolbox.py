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
    con.execute(
        "CREATE TABLE net_points_player_fingerprint (athlete_id VARCHAR, season INTEGER, team_id VARCHAR, "
        "games INTEGER, minutes DOUBLE, rim_o_net_pts DOUBLE, rim_d_net_pts DOUBLE, turnover_o_net_pts DOUBLE)"
    )
    con.execute(
        "INSERT INTO net_points_player_fingerprint VALUES "
        "('1', 2026, '9', 55, 1900.0, 50.0, -5.0, 3.0), "  # Curry
        "('2', 2026, '9', 30, 800.0, 10.0, -2.0, 1.0)"  # Klay
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


def test_get_leaderboard_unknown_metric_suggests_closest_match(toolbox: Toolbox) -> None:
    """Regression: a real run guessed metric='points' instead of the actual
    enum value 'avg_points', which sent the model on an unrelated multi-turn
    detour that eventually recovered but dropped the team/fields it had
    originally asked for. A close-match suggestion should make this a
    one-turn fix instead."""
    result = toolbox.get_leaderboard(metric="points")
    assert "Did you mean 'avg_points'?" in result


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
    # the season default moved into leaderboard.run_leaderboard, which toolbox now wraps
    monkeypatch.setattr("association.query.leaderboard.current_season", lambda: 2026)
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


def test_get_leaderboard_fingerprint_metric_has_no_season_type(toolbox: Toolbox) -> None:
    """Regression: a real query for a NetPoints fingerprint category (e.g. rim
    scoring) never used net_points_player_fingerprint at all - it isn't
    exposed as a get_leaderboard metric, so the model fell back to an
    unrelated per-game NetPoints leaderboard. This table also has no
    season_type column at all (unlike every other metric), so get_leaderboard
    must skip that filter entirely rather than trying to apply one."""
    result = json.loads(toolbox.get_leaderboard(metric="rim_o_net_pts", season=2026))
    names = [r["display_name"] for r in result["rows"]]
    assert names[0] == "Stephen Curry"
    assert result["season_type"] is None  # not applicable for this metric
    assert result["min_sample_applied"] is None  # no default floor for a fingerprint total


def test_get_leaderboard_fingerprint_metric_accepts_explicit_min_sample(toolbox: Toolbox) -> None:
    result = json.loads(toolbox.get_leaderboard(metric="rim_o_net_pts", season=2026, min_sample=1000))
    names = [r["display_name"] for r in result["rows"]]
    assert names == ["Stephen Curry"]  # Klay's 800 minutes no longer qualifies
    assert result["min_sample_applied"] == 1000


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
    """usage_pct/ts_pct/efg_pct need player_advanced_stats, which needs a
    warehouse rebuild after player_box_stats was fetched - if that view is
    missing, say so instead of a bare DuckDB error the model has no way to
    act on."""
    result = toolbox.get_leaderboard(metric="ts_pct", season=2026)
    assert "requires: warehouse rebuilt with `association data load`" in result


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


def test_double_and_triple_doubles_are_registered_metrics() -> None:
    from association.query.leaderboard import resolve_metric
    from association.query.metrics import LEADERBOARD_METRICS

    assert resolve_metric("triple_double") == "triple_doubles"
    assert resolve_metric("double_double") == "double_doubles"
    assert LEADERBOARD_METRICS["triple_doubles"].column == "tripleDouble"
    assert LEADERBOARD_METRICS["double_doubles"].dedup_traded is True


# ---------------- result size bounding ----------------


@pytest.fixture
def seeded(tmp_path: Path, db_path: str) -> Toolbox:
    """A Toolbox over a db with bulky fixture tables. They have to be created
    before Toolbox opens the file, since it connects read-only by design."""
    con = duckdb.connect(db_path)
    con.execute("CREATE TABLE wide AS SELECT i AS id, repeat('x', 400) AS padding FROM range(300) t(i)")
    con.execute("CREATE TABLE narrow AS SELECT i AS id FROM range(300) t(i)")
    con.execute("CREATE TABLE huge AS SELECT repeat('z', 40000) AS blob, 1 AS keep")
    con.execute("INSERT INTO players (athlete_id, display_name) SELECT 'x' || i, 'P' || i FROM range(200) t(i)")
    con.execute(
        "INSERT INTO player_season_stats (athlete_id, season, season_type, avgPoints, gamesPlayed) "
        "SELECT 'x' || i, 2026, 2, i, 40 FROM range(200) t(i)"
    )
    con.close()
    return Toolbox(db_path, tmp_path / "out")


def test_run_sql_bounds_a_wide_result_by_tokens_not_rows(seeded: Toolbox) -> None:
    """A 200-row cap does not bound what comes BACK: measured, `SELECT * FROM
    player_game_log LIMIT 200` serialized to ~44,000 tokens - nearly three
    times the whole context window, from one tool call. Over num_ctx ollama
    cuts the prompt head-first and silently, discarding the system prompt."""
    from association.query.toolbox import MAX_RESULT_TOKENS, _estimate_tokens

    result = seeded.run_sql("SELECT * FROM wide")
    assert _estimate_tokens(result) <= MAX_RESULT_TOKENS
    payload = json.loads(result)
    assert payload["truncated"] is True
    assert payload["row_count"] < 300
    assert "dropped to fit the context window" in payload["note"]


def test_run_sql_returns_a_small_result_untouched(toolbox: Toolbox) -> None:
    payload = json.loads(toolbox.run_sql("SELECT athlete_id FROM players LIMIT 2"))
    assert payload["truncated"] is False
    assert "note" not in payload


def test_run_sql_reports_the_dropped_count_so_the_model_can_narrow(seeded: Toolbox) -> None:
    payload = json.loads(seeded.run_sql("SELECT * FROM wide"))
    assert f"{200 - payload['row_count']} more row(s)" in payload["note"]
    assert "aggregate in SQL" in payload["note"]


def test_run_sql_explains_when_even_one_row_is_too_large(seeded: Toolbox) -> None:
    # A very wide SELECT * - listing the columns is what the model needs to
    # write a narrower query, so return that rather than a silent empty result.
    payload = json.loads(seeded.run_sql("SELECT * FROM huge"))
    assert payload["rows"] == [] and payload["truncated"] is True
    assert "too large to return" in payload["note"] and "blob" in payload["note"]


def test_run_sql_still_flags_the_row_cap_when_everything_fits(seeded: Toolbox) -> None:
    payload = json.loads(seeded.run_sql("SELECT id FROM narrow"))
    assert payload["truncated"] is True and "row cap" in payload["note"]


def test_get_leaderboard_limit_is_clamped(seeded: Toolbox) -> None:
    from association.query.leaderboard import MAX_LIMIT

    result = json.loads(seeded.get_leaderboard(metric="avg_points", season=2026, min_sample=1, limit=5000))
    assert result["row_count"] <= MAX_LIMIT


def test_run_sql_flags_an_id_compared_to_an_abbreviation(toolbox: Toolbox) -> None:
    """Confirmed live: the agent wrote `home_team_id = 'PHI'` (ids are '20'),
    got zero, and reported "the 76ers did not play against the Celtics" - they
    played four times. The rule against this was in its prompt verbatim, with
    that exact wrong form as a worked example."""
    payload = json.loads(toolbox.run_sql("SELECT COUNT(*) AS c FROM shot_chart WHERE team_id = 'PHI'"))
    assert "can NEVER match" in payload["warning"]
    assert "team_id" in payload["warning"] and "PHI" in payload["warning"]


def test_the_id_warning_fires_on_a_zero_count_not_just_an_empty_result(toolbox: Toolbox) -> None:
    # The failing query was a COUNT(*), which returns one row containing 0
    # rather than no rows at all.
    payload = json.loads(toolbox.run_sql("SELECT COUNT(*) AS c FROM shot_chart WHERE team_id = 'PHI'"))
    assert payload["row_count"] == 1 and payload["rows"][0]["c"] == 0
    assert "warning" in payload


def test_a_correct_id_filter_is_not_flagged(toolbox: Toolbox) -> None:
    assert "warning" not in json.loads(toolbox.run_sql("SELECT * FROM shot_chart WHERE team_id = '9'"))


def test_a_genuinely_empty_result_is_not_flagged(toolbox: Toolbox) -> None:
    payload = json.loads(toolbox.run_sql("SELECT * FROM shot_chart WHERE period = 99"))
    assert payload["row_count"] == 0 and "warning" not in payload


def test_a_name_filter_on_a_name_column_is_not_flagged(toolbox: Toolbox) -> None:
    assert "warning" not in json.loads(toolbox.run_sql("SELECT * FROM players WHERE display_name = 'Stephen Curry'"))
