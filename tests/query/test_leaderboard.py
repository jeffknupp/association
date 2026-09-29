"""Regression + sanity tests for run_leaderboard and the shot-chart renderer.

Ported from the retired agent toolbox's tests (5.0.0): the tool faces are
gone, and these call the same functions the templates do. ``lb`` returns what
the tool returned - the result as a dict, or a LeaderboardError's message -
so every assertion reads as it did.
"""

import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from association.query.connection import connect_read_only
from association.query.leaderboard import LeaderboardError, run_leaderboard
from association.query.shotchart import render_shot_chart


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
        "coordinate_x INTEGER, coordinate_y INTEGER, points_attempted INTEGER, season INTEGER, description VARCHAR)"
    )
    con.execute(
        "INSERT INTO shot_chart VALUES "
        "('100', '1', '9', 1, '10:00', true, 'Jump Shot', 25, 26, 3, 2026, 'Stephen Curry makes 26-foot three point jumper'), "
        "('100', '1', '9', 1, '9:00', false, 'Jump Shot', 24, 25, 3, 2026, 'Stephen Curry misses 25-foot three point jumper'), "
        "('100', '1', '9', 2, '8:00', true, 'Layup', 25, 1, 2, 2026, 'Stephen Curry makes layup')"
    )
    con.execute("CREATE TABLE player_season_advanced_stats (season INTEGER, season_type INTEGER, athlete_id VARCHAR, games_played INTEGER, usage_pct DOUBLE)")
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
    con.execute("CREATE TABLE net_points_player (athlete_id VARCHAR, season INTEGER, net_points_season_type VARCHAR, overall DOUBLE, overall_per_100_poss DOUBLE, total_minutes INTEGER)")
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
    # Event 100 (the one render_shot_chart tests scope to with event_id="100")
    # named as a real game, so a single-game chart can name it by date,
    # opponent and result rather than by its bare id - ISSUES.md #155.
    con.execute("CREATE TABLE games (event_id VARCHAR, season INTEGER, home_team_id VARCHAR, away_team_id VARCHAR, home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)")
    con.execute("INSERT INTO games VALUES ('100', 2026, '9', '20', 120, 110, '9')")
    con.execute("CREATE TABLE player_game_log (athlete_id VARCHAR, event_id VARCHAR, season INTEGER, team_id VARCHAR, opponent_abbr VARCHAR, game_date VARCHAR)")
    con.execute("INSERT INTO player_game_log VALUES ('1', '100', 2026, '9', 'ATL', '2026-01-02T00:30Z')")
    con.close()
    return str(path)


@pytest.fixture
def con(db_path: str) -> duckdb.DuckDBPyConnection:
    return connect_read_only(db_path)


def lb(con: duckdb.DuckDBPyConnection, **kwargs: Any) -> Any:
    """run_leaderboard's result in the shape the retired tool returned it: a
    dict (through JSON, so a Decimal or a date reads as the page would read
    it), or the error's message."""
    try:
        result = run_leaderboard(con, **kwargs)
    except LeaderboardError as exc:
        return str(exc)
    return json.loads(
        json.dumps(
            {
                "metric": result.metric,
                "label": result.label,
                "season": result.season,
                "season_type": result.season_type,
                "min_sample_applied": result.min_sample_applied,
                "min_sample_column": result.min_sample_column,
                "team": result.team,
                "rows": result.rows,
                "row_count": len(result.rows),
            },
            default=str,
        )
    )


def render(con: duckdb.DuckDBPyConnection, out_dir: Path, **kwargs: Any) -> str:
    return render_shot_chart(con, out_dir, **kwargs).message


# ---------------- get_leaderboard ----------------


def test_get_leaderboard_unknown_metric_is_rejected(con: duckdb.DuckDBPyConnection) -> None:
    result = lb(con, metric="not_a_real_metric")
    assert "unknown metric" in result


def test_get_leaderboard_unknown_metric_suggests_closest_match(con: duckdb.DuckDBPyConnection) -> None:
    """Regression: a real run guessed metric='points' instead of the actual
    enum value 'avg_points', which sent the model on an unrelated multi-turn
    detour that eventually recovered but dropped the team/fields it had
    originally asked for. A close-match suggestion should make this a
    one-turn fix instead."""
    result = lb(con, metric="points")
    assert "Did you mean 'avg_points'?" in result


def test_get_leaderboard_invalid_season_type_is_rejected(con: duckdb.DuckDBPyConnection) -> None:
    result = lb(con, metric="usage_pct", season_type=7)
    assert "season_type must be" in result


def test_get_leaderboard_applies_default_minimum_sample(con: duckdb.DuckDBPyConnection) -> None:
    """Regression: a real query for top usage rate with no minimum surfaced a
    3-game stint at 90% ahead of a real, sustained 30% leader (confirmed live
    with Izaiah Brockington). Klay's 3-game/90% row must not outrank or even
    appear ahead of Curry's real, qualified 30% once the default floor applies."""
    result = lb(con, metric="usage_pct", season=2026)
    names = [r["display_name"] for r in result["rows"]]
    assert names == ["Stephen Curry"]
    assert result["min_sample_applied"] == 20
    assert result["min_sample_column"] == "games_played"  # the unit: ts_pct's floor is attempts


def test_get_leaderboard_min_sample_override_widens_the_pool(con: duckdb.DuckDBPyConnection) -> None:
    result = lb(con, metric="usage_pct", season=2026, min_sample=1)
    names = [r["display_name"] for r in result["rows"]]
    assert names == ["Klay Thompson", "Stephen Curry"]  # Klay's 90% now qualifies and ranks first
    assert result["min_sample_applied"] == 1


def test_get_leaderboard_defaults_to_current_season(monkeypatch: pytest.MonkeyPatch, con: duckdb.DuckDBPyConnection) -> None:
    # the season default lives in leaderboard.run_leaderboard
    monkeypatch.setattr("association.query.leaderboard.current_season", lambda: 2026)
    result = lb(con, metric="usage_pct")
    assert result["season"] == 2026
    assert [r["display_name"] for r in result["rows"]] == ["Stephen Curry"]


def test_get_leaderboard_dedups_traded_player_to_one_combined_row(con: duckdb.DuckDBPyConnection) -> None:
    """Regression: player_season_stats gives a traded player one row per team
    stint plus one combined row (team_id IS NULL) - without dedup a traded
    player is double/triple-counted. Only the combined row should survive."""
    result = lb(con, metric="avg_points", season=2026, min_sample=1)
    klay_rows = [r for r in result["rows"] if r["display_name"] == "Klay Thompson"]
    assert len(klay_rows) == 1
    assert klay_rows[0]["value"] == 13.8


def test_get_leaderboard_netpoints_total_translates_season_type_to_string(con: duckdb.DuckDBPyConnection) -> None:
    """net_points_player uses its own string net_points_season_type column, not
    the numeric season_type every other table uses - the tool must translate
    the model's normal integer season_type (2) to 'Regular Season' itself."""
    result = lb(con, metric="netpoints_total", season=2026, season_type=2)
    names = [r["display_name"] for r in result["rows"]]
    assert "Stephen Curry" in names
    assert "Klay Thompson" in names  # no minimum sample for a season total


def test_get_leaderboard_netpoints_per_100_applies_default_minimum_minutes(con: duckdb.DuckDBPyConnection) -> None:
    """Regression: NetPoints per-100-possessions is a rate too - a live rerun of
    the same question with --think produced a leaderboard dominated by tiny-
    minutes players once this column was used unqualified. Klay's 40 minutes
    must not qualify at the 500-minute default."""
    result = lb(con, metric="netpoints_per_100", season=2026)
    names = [r["display_name"] for r in result["rows"]]
    assert names == ["Stephen Curry"]
    assert result["min_sample_applied"] == 500


def test_get_leaderboard_fingerprint_metric_has_no_season_type(con: duckdb.DuckDBPyConnection) -> None:
    """Regression: a real query for a NetPoints fingerprint category (e.g. rim
    scoring) never used net_points_player_fingerprint at all - it isn't
    exposed as a get_leaderboard metric, so the model fell back to an
    unrelated per-game NetPoints leaderboard. This table also has no
    season_type column at all (unlike every other metric), so get_leaderboard
    must skip that filter entirely rather than trying to apply one."""
    result = lb(con, metric="rim_o_net_pts", season=2026)
    names = [r["display_name"] for r in result["rows"]]
    assert names[0] == "Stephen Curry"
    assert result["season_type"] is None  # not applicable for this metric
    assert result["min_sample_applied"] is None  # no default floor for a fingerprint total


def test_get_leaderboard_fingerprint_metric_accepts_explicit_min_sample(con: duckdb.DuckDBPyConnection) -> None:
    result = lb(con, metric="rim_o_net_pts", season=2026, min_sample=1000)
    names = [r["display_name"] for r in result["rows"]]
    assert names == ["Stephen Curry"]  # Klay's 800 minutes no longer qualifies
    assert result["min_sample_applied"] == 1000


def test_get_leaderboard_fields_adds_box_score_columns(con: duckdb.DuckDBPyConnection) -> None:
    result = lb(con, metric="usage_pct", season=2026, fields=["points", "rebounds"])
    row = result["rows"][0]
    assert row["display_name"] == "Stephen Curry"
    assert row["points"] == 28.5


def test_get_leaderboard_unknown_field_is_rejected(con: duckdb.DuckDBPyConnection) -> None:
    result = lb(con, metric="usage_pct", fields=["not_a_real_field"])
    assert "unknown field" in result


def test_get_leaderboard_team_filter_by_abbreviation(con: duckdb.DuckDBPyConnection) -> None:
    """Both Curry and Klay played for the Warriors that season (Klay's first
    of two stints) - both should show up, ranked by their season value."""
    result = lb(con, metric="avg_points", season=2026, min_sample=1, team="Warriors")
    names = [r["display_name"] for r in result["rows"]]
    assert names == ["Stephen Curry", "Klay Thompson"]


def test_get_leaderboard_team_filter_includes_traded_player_who_stopped_there(con: duckdb.DuckDBPyConnection) -> None:
    """A traded player's DEDUPED/combined row has team_id IS NULL (see the
    dedup test above) - filtering on that column directly would wrongly
    exclude them from every team's roster even though they really did play
    for one of their stint teams. Klay's team_id='20' stint means he must
    still show up (with his combined season row's value) when filtered to
    that team, not be silently dropped."""
    result = lb(con, metric="avg_points", season=2026, min_sample=1, team="20")
    names = [r["display_name"] for r in result["rows"]]
    assert names == ["Klay Thompson"]
    assert result["rows"][0]["value"] == 13.8


def test_get_leaderboard_unknown_team_is_rejected(con: duckdb.DuckDBPyConnection) -> None:
    result = lb(con, metric="avg_points", team="Not A Real Team")
    assert "no team found" in result


def test_get_leaderboard_missing_table_reports_requires_hint(con: duckdb.DuckDBPyConnection) -> None:
    """usage_pct/ts_pct/efg_pct need player_advanced_stats, which needs a
    warehouse rebuild after player_box_stats was fetched - if that view is
    missing, say so instead of a bare DuckDB error the model has no way to
    act on."""
    result = lb(con, metric="ts_pct", season=2026)
    assert "requires: warehouse rebuilt with `association data load`" in result


# ---------------- render_shot_chart ----------------


def test_render_shot_chart_nickname_matching(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """Regression: "Steph" is not a substring of "Stephen" (whole-phrase ILIKE
    matching failed for this exact query). Per-token AND matching fixes it."""
    result = render(con, tmp_path / "out", player_name="Steph Curry")
    assert "Rendered shot chart for Stephen Curry" in result


def test_render_shot_chart_says_how_it_read_a_near_spelling(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """The agent calls this as a tool, with nobody above it collecting name
    readings - so the entry point says it itself, or the default is silent."""
    result = render(con, tmp_path / "out", player_name="Stephen Cury")
    assert "Rendered shot chart for Stephen Curry" in result
    assert result.endswith("('Stephen Cury' matches no player exactly and was read as Stephen Curry, the only near spelling on record - spell the name exactly to ask about someone else.)")


def test_render_shot_chart_no_match(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    result = render(con, tmp_path / "out", player_name="Nobody Real")
    assert "No player found" in result


def test_render_shot_chart_event_id_overrides_season(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """Regression: season/season_type must be ignored once event_id is given -
    a wrong guessed season used to silently zero out otherwise-correct results."""
    result = render(con, tmp_path / "out", player_name="Curry", event_id="100", season=1999)
    assert "Rendered shot chart" in result
    assert "2/3" in result


def test_render_shot_chart_made_only_filters(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    result = render(con, tmp_path / "out", player_name="Curry", made_only=True)
    assert "2/2" in result


def test_render_shot_chart_shot_value_filters(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    result = render(con, tmp_path / "out", player_name="Curry", shot_value=2)
    assert "1/1" in result


def test_render_shot_chart_period_filters(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    result = render(con, tmp_path / "out", player_name="Curry", period=2)
    assert "1/1" in result


def test_render_shot_chart_writes_html_file(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    render(con, tmp_path / "out", player_name="Curry")
    files = list((tmp_path / "out").glob("*.html"))
    assert len(files) == 1
    assert "<svg" in files[0].read_text()


def test_render_shot_chart_names_the_game_not_just_its_id(con: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
    """ISSUES.md #155: a single-game chart used to name the game only by its
    event id, on the page and in the answer - "game 100" said nothing about
    which game that was. It now reads the date, opponent and result off
    `games`/`player_game_log`, both in the message and in the page itself."""
    result = render(con, tmp_path / "out", player_name="Curry", event_id="100")
    assert "2026-01-01 vs ATL, W 120-110" in result
    assert "game 100" not in result

    files = list((tmp_path / "out").glob("shotchart_stephen_curry_100.html"))
    assert len(files) == 1
    html = files[0].read_text()
    assert "2026-01-01 vs ATL, W 120-110" in html
    # The filename keeps the bare event id - only the reader-facing text names
    # the game in full.
    assert "shotchart_stephen_curry_100.html" in str(files[0])


def test_double_and_triple_doubles_are_registered_metrics() -> None:
    from association.query.leaderboard import resolve_metric
    from association.query.metrics import LEADERBOARD_METRICS

    assert resolve_metric("triple_double") == "triple_doubles"
    assert resolve_metric("double_double") == "double_doubles"
    assert LEADERBOARD_METRICS["triple_doubles"].column == "tripleDouble"
    assert LEADERBOARD_METRICS["double_doubles"].dedup_traded is True


@pytest.fixture
def seeded(db_path: str) -> duckdb.DuckDBPyConnection:
    """A connection over a db with bulky fixture tables. They have to be
    created before the read-only connection opens the file."""
    con = duckdb.connect(db_path)
    con.execute("CREATE TABLE wide AS SELECT i AS id, repeat('x', 400) AS padding FROM range(300) t(i)")
    con.execute("CREATE TABLE narrow AS SELECT i AS id FROM range(300) t(i)")
    con.execute("CREATE TABLE huge AS SELECT repeat('z', 40000) AS blob, 1 AS keep")
    con.execute("INSERT INTO players (athlete_id, display_name) SELECT 'x' || i, 'P' || i FROM range(200) t(i)")
    con.execute("INSERT INTO player_season_stats (athlete_id, season, season_type, avgPoints, gamesPlayed) SELECT 'x' || i, 2026, 2, i, 40 FROM range(200) t(i)")
    con.close()
    return connect_read_only(db_path)


def test_get_leaderboard_limit_is_clamped(seeded: duckdb.DuckDBPyConnection) -> None:
    from association.query.leaderboard import MAX_LIMIT

    result = lb(seeded, metric="avg_points", season=2026, min_sample=1, limit=5000)
    assert result["row_count"] <= MAX_LIMIT
