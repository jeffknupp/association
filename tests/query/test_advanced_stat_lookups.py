"""``player_stat`` answering the stats that are computed rather than served.

True shooting, effective FG%, usage and game score were rankable and not
lookup-able: ``leaderboard`` has ranked the first three since 2.1.0, the router
emits their names correctly, and ``player_stat`` refused them as unknown. The
arithmetic that matters here is the career one - a rate over many seasons is
weighted by its own volume, never averaged - so the fixture is built so that
the weighted answer and the mean of the season figures are different numbers.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from association.query.templates import TemplateContext, TemplateUnsupported, check_coverage
from association.query.templates.common import _ADVANCED_STAT_NAMES, _sources_for
from association.query.templates.players import ADVANCED_STATS, player_stat


@pytest.fixture
def advanced_ctx(tmp_path: Path) -> TemplateContext:
    """Two players, built for the two things a career figure can get wrong.

    Klay Thompson is lopsided on purpose: 100 true-shooting attempts at .500,
    900 at .700 and 50 at .900, so the attempt-weighted career figure (.690)
    and the mean of the season figures (.700) are different numbers, and an
    identical 1993 row stands in for the phantom season.

    Joakim Noah carries the other shape - a 2015 with real games played, no
    attempts and a NULL rate, which is what ESPN's empty 2013-2018 box scores
    leave behind.
    """
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('10','Klay Thompson'),('11','Joakim Noah'),('12','Taj Gibson')")
    con.execute(
        "CREATE TABLE player_season_advanced_stats ("
        "season INTEGER, season_type INTEGER, athlete_id VARCHAR, games_played INTEGER, "
        "field_goals_attempted DOUBLE, true_shooting_attempts DOUBLE, ts_pct DOUBLE, efg_pct DOUBLE, usage_pct DOUBLE, avg_game_score DOUBLE)"
    )
    con.execute(
        "INSERT INTO player_season_advanced_stats VALUES "
        "(2024, 2, '10', 20, 80, 100, 0.500, 0.400, 25.0, 11.0), "
        "(2025, 2, '10', 60, 720, 900, 0.700, 0.600, 31.0, 19.0), "
        # The phantom: 1993 is a copy of 1994 and a career that counted it would
        # count that season twice. Both rows are identical on purpose.
        "(1993, 2, '10', 10, 40, 50, 0.900, 0.900, 40.0, 30.0), "
        "(1994, 2, '10', 10, 40, 50, 0.900, 0.900, 40.0, 30.0), "
        # Noah's 2015 is the shape ESPN's empty 2013-2018 box scores leave: a
        # row with real games played, no attempts and a NULL rate.
        "(2014, 2, '11', 70, 500, 620, 0.550, 0.520, 22.0, 12.0), "
        "(2015, 2, '11', 60, 0, 0, NULL, NULL, NULL, NULL), "
        "(2016, 2, '11', 50, 300, 380, 0.590, 0.560, 20.0, 10.0), "
        # Gibson's empty season is his FIRST and his last, so an unfiltered
        # MIN/MAX would name 2013-2017 for a figure that only covers 2014-2016.
        "(2013, 2, '12', 40, 0, 0, NULL, NULL, NULL, NULL), "
        "(2014, 2, '12', 70, 500, 620, 0.550, 0.520, 22.0, 12.0), "
        "(2016, 2, '12', 50, 300, 380, 0.590, 0.560, 20.0, 10.0), "
        "(2017, 2, '12', 40, 0, 0, NULL, NULL, NULL, NULL)"
    )
    return TemplateContext(con=con, out_dir=tmp_path)


def test_a_career_rate_is_weighted_by_its_own_volume_not_averaged(advanced_ctx: TemplateContext) -> None:
    """Over the three seasons a career reads (1993 is the phantom, excluded),
    (.5 x 100 + .7 x 900 + .9 x 50) / 1,050 = .690. The mean of .500, .700 and
    .900 is .700 - a number no reader could reproduce from the volumes printed
    beside it, and the one an average of averages would give."""
    result = player_stat(advanced_ctx, {"player": "Klay Thompson", "stat": "ts_pct", "span": "career"})
    assert result.data["stats"]["ts_pct"] == pytest.approx(725 / 1050)
    assert ".690 true shooting percentage" in result.answer
    assert ".700" not in result.answer


def test_a_career_rate_excludes_the_phantom_season(advanced_ctx: TemplateContext) -> None:
    """1993's rows are identical copies of 1994's. Counted, they would add 50
    attempts at .900 twice and pull the career figure up; excluded, the career
    covers 1994-2025 and the 1994 row is counted once."""
    result = player_stat(advanced_ctx, {"player": "Klay Thompson", "stat": "ts_pct", "span": "career"})
    assert result.data["seasons"] == [1994, 2025]
    # 1,050 attempts, not 1,100: (.5 x 100 + .7 x 900 + .9 x 50) / 1050.
    assert "on 1,050 true-shooting attempts" in result.answer


def test_efg_is_weighted_by_field_goal_attempts_not_true_shooting_ones(advanced_ctx: TemplateContext) -> None:
    """Each rate takes its OWN denominator. Weighting eFG% by true-shooting
    attempts would give .6857 here; by field-goal attempts it is .6860."""
    result = player_stat(advanced_ctx, {"player": "Klay Thompson", "stat": "efg_pct", "span": "career"})
    assert "field-goal attempts" in result.answer
    assert result.data["stats"]["efg_pct"] == pytest.approx((0.4 * 80 + 0.6 * 720 + 0.9 * 40) / 840)


def test_a_season_figure_is_read_rather_than_recomputed(advanced_ctx: TemplateContext) -> None:
    for stat, expected in (("ts_pct", ".700 true shooting percentage"), ("usage_pct", "31.0 usage rate"), ("game_score", "19.0 game score")):
        answer = player_stat(advanced_ctx, {"player": "Klay Thompson", "stat": stat, "season": 2025}).answer
        assert expected in answer, stat
        assert "in the 2025 regular season" in answer, stat


@pytest.mark.parametrize("stat", ["usage_pct", "game_score"])
def test_a_stat_with_no_volume_column_refuses_a_career_rather_than_averaging(advanced_ctx: TemplateContext, stat: str) -> None:
    """Usage is a rate per possession while on court and game score is a
    per-game composite; neither has a denominator the seasons can be weighted
    by, so a career figure would be the mean of means the test above rejects."""
    with pytest.raises(TemplateUnsupported, match="no career figure"):
        player_stat(advanced_ctx, {"player": "Klay Thompson", "stat": stat, "span": "career"})


def test_a_narrowed_set_of_games_is_refused_not_answered_with_the_season(advanced_ctx: TemplateContext) -> None:
    """The season line is not an answer to a question about one opponent. This
    is the substitution the module's own docstring exists to stop, and the
    refusal names the real cause rather than claiming no data."""
    with pytest.raises(TemplateUnsupported, match="narrowed set of games"):
        player_stat(advanced_ctx, {"player": "Klay Thompson", "stat": "ts_pct", "opponent": "Boston Celtics"})


def test_an_advanced_stat_is_charged_its_own_floor_not_the_season_lines() -> None:
    """``player_season_stats`` reaches 1977 and the computed stats reach 1994.
    Refusing a 1980 true-shooting question in the season line's words would
    name a floor the question does not depend on."""
    assert _sources_for("player_stat", {"player": "Kareem Abdul-Jabbar", "stat": "ts_pct", "season": 1980}) == ("player_season_advanced_stats",)
    refusal = check_coverage("player_stat", {"player": "Kareem Abdul-Jabbar", "stat": "ts_pct", "season": 1980, "season_type": 2})
    assert refusal is not None
    assert "Advanced season stats only go back to 1994" in refusal
    # A stat the season line does hold still reads from the season line.
    assert _sources_for("player_stat", {"player": "Kareem Abdul-Jabbar", "stat": "points", "season": 1980}) == ("player_season_stats_deduped",)


def test_the_two_advanced_stat_vocabularies_agree() -> None:
    """``common`` names these to pick the table and ``players`` to answer from
    it, and they are two hand-maintained lists of one set - the shape that let
    a rankable stat be un-lookup-able in the first place. A name in one and not
    the other is either a stat charged the wrong floor or one refused as
    unknown after the floor let it through."""
    assert set(ADVANCED_STATS) == set(_ADVANCED_STAT_NAMES)


def test_a_career_says_which_seasons_it_could_not_see(advanced_ctx: TemplateContext) -> None:
    """ESPN serves whole team-seasons of empty box scores from 2013 to 2018, so
    a player who spent one on Chicago or New Orleans has a row with real games,
    no attempts and a NULL rate. Counting its games would credit the rate with
    60 games it never saw, and naming the span 2014-2016 without a word about
    the hole is the fluent-but-narrower answer this project refuses."""
    result = player_stat(advanced_ctx, {"player": "Joakim Noah", "stat": "ts_pct", "span": "career"})
    assert "in 120 games" in result.answer, "the empty season's 60 games must not be counted"
    assert "180 games" not in result.answer
    assert result.data["seasons_missing"] == 1
    assert "1 season in that span is not counted" in result.answer
    assert "box scores for it are empty" in result.answer


def test_a_career_with_nothing_missing_says_nothing_about_it(advanced_ctx: TemplateContext) -> None:
    """The note is a caveat, not a disclaimer: a complete career does not carry
    a sentence about seasons that are all present."""
    assert "not counted" not in player_stat(advanced_ctx, {"player": "Klay Thompson", "stat": "ts_pct", "span": "career"}).answer


def test_the_span_named_is_the_span_the_figure_covers(advanced_ctx: TemplateContext) -> None:
    """An empty season at either end of a career would otherwise widen the
    years the answer claims. Gibson has data for 2014 and 2016 only; saying
    "2013-2017" would name two years the rate never saw, which is the same
    fluent overreach as counting their games."""
    result = player_stat(advanced_ctx, {"player": "Taj Gibson", "stat": "ts_pct", "span": "career"})
    assert result.data["seasons"] == [2014, 2016]
    assert "(2014-2016 regular seasons)" in result.answer
    assert "2013" not in result.answer
    assert "2017" not in result.answer
    assert result.data["seasons_missing"] == 2
