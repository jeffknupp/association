"""The leaderboard metric registry, and the schedule-length scaling of its
shooting-percentage floors (ISSUES.md #13).

``ts_pct``, ``efg_pct`` and ``fg_pct`` qualify on attempts, calibrated against
an 82-game season - flat before this fix, so a shortened season's qualifier
was quietly stricter than the published rule it claimed to apply. Each test
here pins one piece of that: which metrics carry the new
``scales_with_schedule`` flag, that ``leaderboard.default_min_sample`` scales
their floor from the warehouse's own ``real_games`` rather than a hardcoded
per-season table, that a fixture with no ``real_games`` (most of this test
suite, predating this fix) degrades to the old flat floor instead of raising,
and that the rounding rule is the deliberate one - half up.
"""

from pathlib import Path

import duckdb
import pytest

from association.nba.coverage import POSTSEASON, REGULAR_SEASON
from association.query.leaderboard import (
    SCHEDULE_BASE_GAMES,
    _scale_min_sample,
    _team_games_for_season,
    default_min_sample,
    run_leaderboard,
)
from association.query.metrics import LEADERBOARD_METRICS


def test_scales_with_schedule_is_set_for_exactly_ts_efg_fg() -> None:
    """The three floors ISSUES.md #13 measured (550, 480, 400) - and no
    other metric, including the two built from the same ``_percentage``
    helper (``three_pt_pct``, ``ft_pct``) that share the identical flaw but
    were not measured, so they stay flat as a deliberate follow-up rather
    than a guess."""
    scaled = {name for name, spec in LEADERBOARD_METRICS.items() if spec.scales_with_schedule}
    assert scaled == {"ts_pct", "efg_pct", "fg_pct"}


def test_scale_min_sample_matches_the_82_game_floor_at_82_games() -> None:
    """Scaling by a full schedule is the identity - the whole point of basing
    it on the warehouse's own team-game count rather than a per-season table
    is that a normal season is untouched."""
    for base in (550, 480, 400):
        assert _scale_min_sample(base, SCHEDULE_BASE_GAMES) == base


def test_scale_min_sample_rounds_half_up() -> None:
    """41 * 41 / 82 is exactly 20.5 - the one boundary case, and the
    deliberate rule sends it to 21, the stricter (higher) floor, not 20."""
    assert 41 * 41 / SCHEDULE_BASE_GAMES == 20.5
    assert _scale_min_sample(41, 41) == 21


def test_scale_min_sample_matches_measured_2020_and_2021_and_2012_floors() -> None:
    """Pinned against the live warehouse (measured 2026-09-18, read-only):
    2020 and 2021 both play a median 72 games/team, 2012's lockout season 66.
    These are the exact scaled floors the fix produces - a regression here is
    a regression in the sentence a user reads."""
    assert _scale_min_sample(550, 72) == 483  # ts_pct, 2020 and 2021
    assert _scale_min_sample(480, 72) == 421  # efg_pct, 2020 and 2021
    assert _scale_min_sample(400, 72) == 351  # fg_pct, 2020 and 2021
    assert _scale_min_sample(550, 66) == 443  # ts_pct, 2012
    assert _scale_min_sample(480, 66) == 386  # efg_pct, 2012
    assert _scale_min_sample(400, 66) == 322  # fg_pct, 2012


@pytest.fixture
def real_games_con() -> duckdb.DuckDBPyConnection:
    """A bare ``real_games`` table shaped like the warehouse's - only the
    columns ``_team_games_for_season`` reads."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE real_games (event_id VARCHAR, season INTEGER, season_type INTEGER, home_team_id VARCHAR, away_team_id VARCHAR)")
    return c


def _play_out(con: duckdb.DuckDBPyConnection, season: int, season_type: int, team_games: dict[str, int]) -> None:
    """Insert enough ``real_games`` rows that each team in ``team_games`` has
    exactly that many games that season - as home games, so one row per game
    is enough (only one side of each row needs counting for these tests)."""
    rows = [(f"{season}-{team}-{i}", season, season_type, team, "OPP") for team, n in team_games.items() for i in range(n)]
    con.executemany("INSERT INTO real_games VALUES (?, ?, ?, ?, ?)", rows)


def test_team_games_for_season_reads_the_median_not_the_max(real_games_con: duckdb.DuckDBPyConnection) -> None:
    """2020's own shape: most teams (the bubble's 22) landed on 72, a
    minority (the 8 left out) on fewer, so the season's own median is 72 - not
    the max (75), which would treat the exception as the rule, and not a
    hardcoded 2020 -> 72 table, which is what this function exists to avoid."""
    _play_out(real_games_con, 2020, REGULAR_SEASON, {"1": 64, "2": 65, "3": 66, "4": 67, "5": 71, "6": 72, "7": 72, "8": 73, "9": 75})
    assert _team_games_for_season(real_games_con, 2020) == 72


def test_team_games_for_season_ignores_the_cup_final_outlier(real_games_con: duckdb.DuckDBPyConnection) -> None:
    """DATA.md, "The NBA Cup final is stored as a regular-season game": the
    two finalists get one extra counted game. A median over 30 teams keeps
    that in the tail rather than moving the season's own typical length."""
    team_games = dict.fromkeys((str(i) for i in range(1, 29)), 82) | {"29": 83, "30": 83}
    _play_out(real_games_con, 2025, REGULAR_SEASON, team_games)
    assert _team_games_for_season(real_games_con, 2025) == 82


def test_team_games_for_season_without_real_games_returns_none() -> None:
    """No ``real_games`` table at all - true of most fixtures in this suite,
    which predate this fix and are not this fix's to update (test_templates.py
    in particular pins the flat 550/480 qualifier against a fixture with no
    such table). The function degrades to ``None`` rather than raising, so a
    caller can fall back to the flat floor."""
    c = duckdb.connect(":memory:")
    assert _team_games_for_season(c, 2020) is None


def test_default_min_sample_scales_a_shortened_regular_season(real_games_con: duckdb.DuckDBPyConnection) -> None:
    _play_out(real_games_con, 2020, REGULAR_SEASON, dict.fromkeys((str(i) for i in range(1, 31)), 72))
    spec = LEADERBOARD_METRICS["ts_pct"]
    assert default_min_sample(spec, REGULAR_SEASON, real_games_con, 2020) == 483


def test_default_min_sample_leaves_a_full_season_unscaled(real_games_con: duckdb.DuckDBPyConnection) -> None:
    _play_out(real_games_con, 2025, REGULAR_SEASON, dict.fromkeys((str(i) for i in range(1, 31)), 82))
    spec = LEADERBOARD_METRICS["ts_pct"]
    assert default_min_sample(spec, REGULAR_SEASON, real_games_con, 2025) == 550


def test_default_min_sample_falls_back_to_the_flat_floor_without_real_games() -> None:
    """The compatibility case: a caller (or a test fixture) with no
    ``real_games`` table, or no ``con``/``season`` at all, gets the exact
    flat value this metric always returned - so this fix cannot move a board
    it was never measured against."""
    c = duckdb.connect(":memory:")
    spec = LEADERBOARD_METRICS["ts_pct"]
    assert default_min_sample(spec, REGULAR_SEASON, c, 2020) == 550
    assert default_min_sample(spec, REGULAR_SEASON) == 550
    assert default_min_sample(spec, REGULAR_SEASON, None, 2020) == 550


def test_default_min_sample_never_scales_a_non_scaling_metric(real_games_con: duckdb.DuckDBPyConnection) -> None:
    """``avg_points`` qualifies on games played, which already reads the
    real column - scaling it too would be a second, disagreeing definition of
    the same qualifier."""
    _play_out(real_games_con, 2020, REGULAR_SEASON, dict.fromkeys((str(i) for i in range(1, 31)), 72))
    spec = LEADERBOARD_METRICS["avg_points"]
    assert default_min_sample(spec, REGULAR_SEASON, real_games_con, 2020) == spec.default_min_sample == 20


def test_default_min_sample_never_scales_the_postseason(real_games_con: duckdb.DuckDBPyConnection) -> None:
    """A postseason floor is its own flat constant (calibrated to 10 games,
    not a fraction of 82) - the schedule-length lookup is never even run for
    it, so an oddly shaped postseason table cannot move it either."""
    _play_out(real_games_con, 2020, POSTSEASON, {"1": 4, "2": 21})
    spec = LEADERBOARD_METRICS["ts_pct"]
    assert default_min_sample(spec, POSTSEASON, real_games_con, 2020) == spec.postseason_min_sample == 67


@pytest.fixture
def shooting_ctx(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    """A shortened-season board where one player would be excluded by the
    flat 550-attempt floor but qualifies once it is scaled to a 72-game
    season's own 483 - the end-to-end proof that the fix changes what a user
    reads, not only what a unit test reads."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','High Volume'),('2','Just Short Of 550')")
    c.execute(
        "CREATE TABLE player_season_advanced_stats (season INTEGER, season_type INTEGER, athlete_id VARCHAR, games_played BIGINT, "
        "field_goals_attempted BIGINT, true_shooting_attempts DOUBLE, ts_pct DOUBLE, efg_pct DOUBLE)"
    )
    c.executemany(
        "INSERT INTO player_season_advanced_stats VALUES (2020,2,?,?,?,?,?,?)",
        [
            ("1", 72, 600, 600.0, 0.60, 0.58),
            ("2", 70, 500, 500.0, 0.70, 0.68),  # under 550, over the scaled 483
        ],
    )
    c.execute("CREATE TABLE real_games (event_id VARCHAR, season INTEGER, season_type INTEGER, home_team_id VARCHAR, away_team_id VARCHAR)")
    _play_out(c, 2020, REGULAR_SEASON, dict.fromkeys((str(i) for i in range(1, 31)), 72))
    return c


def test_run_leaderboard_admits_a_player_the_flat_floor_would_have_excluded(shooting_ctx: duckdb.DuckDBPyConnection) -> None:
    result = run_leaderboard(shooting_ctx, "ts_pct", season=2020)
    assert result.min_sample_applied == 483
    assert [r["display_name"] for r in result.rows] == ["Just Short Of 550", "High Volume"]


def test_run_leaderboard_min_sample_applied_carries_the_scaled_number_not_the_flat_one(shooting_ctx: duckdb.DuckDBPyConnection) -> None:
    """``min_sample_applied`` is what the answer text quotes - ISSUES.md #13's
    "keep min_sample_applied honest about the scaled number". A caller reading
    550 off the result while 483 was actually applied is the exact silent
    substitution the fix exists to remove."""
    result = run_leaderboard(shooting_ctx, "ts_pct", season=2020)
    assert result.min_sample_applied != 550
    assert result.min_sample_applied == 483


def test_run_leaderboard_an_explicit_min_sample_overrides_scaling(shooting_ctx: duckdb.DuckDBPyConnection) -> None:
    """A question-supplied ``min_sample`` is still absolute, exactly as
    before this fix - scaling only fills in the default."""
    result = run_leaderboard(shooting_ctx, "ts_pct", season=2020, min_sample=550)
    assert result.min_sample_applied == 550
    assert [r["display_name"] for r in result.rows] == ["High Volume"]
