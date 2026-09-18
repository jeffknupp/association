"""Tests for the per-table coverage floors.

These guard the project's standing failure shape from its other side. A
question under a floor does not error - it returns nothing, and nothing is then
phrased as a real answer or as the wrong cause. Both were measured on the
current snapshot before this existed: a 1996 shot chart said "No shots found
for Michael Jordan with the given filters", blaming the filters for ESPN having
no play-by-play that far back, and a 1980 scoring leaderboard confidently
ranked a league of seven players.
"""

from __future__ import annotations

import pytest

from association.nba.coverage import COVERAGE, POSTSEASON, REGULAR_SEASON, caveat, unavailable
from association.query.prompt import KNOWN_TABLES
from association.query.templates import TEMPLATES
from association.query.templates.common import RANKING_INTENTS, TEMPLATE_SOURCES, check_coverage, coverage_caveat


def test_every_covered_table_is_a_real_table() -> None:
    """The keys are table names, and a typo would be a floor that never fires -
    silently restoring exactly the behavior this module removes."""
    assert set(COVERAGE) <= KNOWN_TABLES


def test_every_template_declares_the_tables_it_reads() -> None:
    """TEMPLATE_SOURCES and TEMPLATES are two hand-maintained lists of the same
    intents, the shape that already produced the player_compare bug. A template
    missing here is one no floor can ever refuse."""
    declared = set(TEMPLATE_SOURCES) | {"leaderboard"}  # leaderboard resolves its table per metric
    assert declared == set(TEMPLATES)


def test_every_declared_source_has_a_floor() -> None:
    for intent, tables in TEMPLATE_SOURCES.items():
        for table in tables:
            assert table in COVERAGE, f"{intent} reads {table}, which declares no coverage floor"


def test_a_season_below_the_floor_is_refused() -> None:
    got = check_coverage("shot_chart", {"season": 1996, "season_type": REGULAR_SEASON})
    assert got is not None
    assert "2002" in got and "1996" in got


def test_the_refusal_says_no_pull_would_help() -> None:
    """The distinction that matters to somebody reading it: this is ESPN's gap,
    not a season nobody has fetched yet."""
    got = check_coverage("fingerprint", {"season": 2005, "season_type": REGULAR_SEASON})
    assert got is not None and "no pull would add any" in got


def test_a_covered_season_is_not_refused() -> None:
    assert check_coverage("shot_chart", {"season": 2003, "season_type": REGULAR_SEASON}) is None
    assert check_coverage("leaderboard", {"stat": "points", "season": 1994, "season_type": REGULAR_SEASON}) is None


def test_an_absent_season_slot_means_the_current_season_and_is_never_refused() -> None:
    assert check_coverage("shot_chart", {"season_type": REGULAR_SEASON}) is None


# ---------------- the two floors that are not one ----------------


def test_a_ranking_is_refused_where_a_named_player_is_not() -> None:
    """The sharpest line in the data. player_season_stats holds Michael
    Jordan's real 1990 line, so his own average is answerable from it; the pool
    it would RANK is 217 players against a ~350-player league, and 7 in 1980."""
    assert check_coverage("player_stat", {"season": 1990, "season_type": REGULAR_SEASON}) is None
    assert check_coverage("player_compare", {"season": 1990, "season_type": REGULAR_SEASON}) is None
    assert check_coverage("leaderboard", {"stat": "points", "season": 1990, "season_type": REGULAR_SEASON}) is not None


def test_the_ranking_refusal_does_not_claim_the_data_is_missing() -> None:
    """It is not missing - it is unrepresentative, and saying "no data for
    1980" about a warehouse holding Moses Malone's real 1980 line would be the
    same false-cause answer in the opposite direction."""
    got = check_coverage("leaderboard", {"stat": "points", "season": 1980, "season_type": REGULAR_SEASON})
    assert got is not None
    assert "no data for 1980" not in got
    assert "would not be one" in got and "for a player you name are still available" in got


def test_a_ranking_over_a_table_with_no_survivor_problem_uses_the_plain_floor() -> None:
    """netpoints ranks over net_points_player, which is league-wide from its
    first season - so its refusal is about missing rows, not about the pool."""
    got = check_coverage("leaderboard", {"stat": "netpoints", "season": 2010, "season_type": REGULAR_SEASON})
    assert got is not None and "no pull would add any" in got


def test_the_playoffs_reach_further_back_than_the_regular_season() -> None:
    """`games` holds full 16-team brackets to 1988 while its regular seasons
    before 1994 are one team's schedule. One floor would either refuse real
    playoff data or admit 82 games as a season."""
    assert check_coverage("head_to_head", {"season": 1990, "season_type": POSTSEASON}) is None
    assert check_coverage("head_to_head", {"season": 1990, "season_type": REGULAR_SEASON}) is not None


def test_the_narrowest_table_decides() -> None:
    """A question is answerable only as far back as its narrowest source, and
    that is the one the message must name - telling somebody standings go back
    to 1988 does not explain an empty 1996 shot chart."""
    got = unavailable(("standings", "shot_chart"), 1996, REGULAR_SEASON)
    assert got is not None and got.startswith("Shot charts")


def test_a_table_with_no_declared_floor_is_skipped_rather_than_assumed() -> None:
    assert unavailable(("players",), 1950, REGULAR_SEASON) is None


# ---------------- partial seasons are answered, with a note ----------------


def test_a_half_season_is_answered_with_a_caveat_rather_than_refused() -> None:
    assert check_coverage("shot_chart", {"season": 2002, "season_type": REGULAR_SEASON}) is None
    note = coverage_caveat("shot_chart", {"season": 2002})
    assert note is not None and "part of the year" in note


def test_a_full_season_carries_no_caveat() -> None:
    # 2005, not 2003: measured, 2003 has located shots for only 986 of its 1,190
    # games, and is a partial season itself.
    assert coverage_caveat("shot_chart", {"season": 2005}) is None
    assert caveat(("shot_chart",), 2005) is None


@pytest.mark.parametrize("intent", sorted(RANKING_INTENTS))
def test_ranking_intents_are_all_real_intents(intent: str) -> None:
    assert intent in TEMPLATES


def test_the_first_playoffs_on_record_is_1989_and_the_refusal_says_why() -> None:
    """ESPN's archive files the 1989 playoffs under 1988 and has no 1987-88
    postseason at all, so "the 1988 playoffs" is refused - in the postseason's
    own words, not the regular season's "one team's 82 games"."""
    got = check_coverage("head_to_head", {"season": 1988, "season_type": POSTSEASON})
    assert got is not None and got.startswith("Playoff games only go back to 1989") and "82 games" not in got
    assert check_coverage("head_to_head", {"season": 1989, "season_type": POSTSEASON}) is None


def test_2003_shots_carry_a_partial_season_caveat() -> None:
    assert coverage_caveat("shot_chart", {"season": 2003}) is not None
    assert coverage_caveat("shot_chart", {"season": 2004}) is None


def test_a_postseason_that_stops_early_is_caveated_on_postseason_questions() -> None:
    """ESPN's 2001 playoffs are missing ten games - Games 1-4 of the Final,
    three of the MIL-PHI conference final, two of MIL-CHA and one of LAL-SA -
    and no pull adds them:
    the scoreboard endpoint, which had the whole 2000 Final, answers 23 of
    those days with nothing at all."""
    note = coverage_caveat("head_to_head", {"season": 2001, "season_type": POSTSEASON})
    assert note is not None and "2001 playoffs" in note


def test_the_same_season_carries_no_caveat_for_its_regular_season() -> None:
    """The whole reason postseason_partial is a separate field. 2001's REGULAR
    season is complete (1,190 games), so a note about missing playoff games
    would be a claim about the wrong half of the year - and one shared tuple
    would also exempt that complete season from its own floor check."""
    assert coverage_caveat("head_to_head", {"season": 2001, "season_type": REGULAR_SEASON}) is None
    assert coverage_caveat("head_to_head", {"season": 2001}) is None


def test_the_recovered_2000_postseason_carries_no_caveat() -> None:
    """2000's gap WAS recoverable: its nine missing games are on the scoreboard
    endpoint even though no team's schedule lists them, so they were fetched
    rather than caveated. Declaring it partial would be an apology for data
    that is now there."""
    assert coverage_caveat("head_to_head", {"season": 2000, "season_type": POSTSEASON}) is None


def test_a_postseason_caveat_covers_the_team_box_as_well_as_the_game_list() -> None:
    """The missing games are absent from team_box_stats too, so a question
    built from it must say so rather than rely on `games` happening to be
    listed first in TEMPLATE_SOURCES."""
    assert caveat(("team_box_stats",), 2001, POSTSEASON) is not None
    assert caveat(("team_box_stats",), 2001, REGULAR_SEASON) is None


def test_the_2001_caveat_reaches_the_templates_that_never_read_the_game_list() -> None:
    """`single_game_high` and `threshold_count` read only the player tables, so
    a caveat declared on `games` alone never reached them: Shaquille O'Neal's
    "4 games with 30+ points" was stated as fact over 11 of the 16 playoff
    games he played."""
    for intent in ("single_game_high", "threshold_count"):
        note = coverage_caveat(intent, {"season": 2001, "season_type": POSTSEASON, "player": "Shaquille O'Neal", "stat": "points"})
        assert note is not None and "2001 playoffs" in note, intent
        assert coverage_caveat(intent, {"season": 2001, "season_type": REGULAR_SEASON, "player": "Shaquille O'Neal", "stat": "points"}) is None


def test_the_2001_player_caveat_says_what_a_player_is_missing() -> None:
    """A team's note and a player's are different claims. Telling somebody
    asking for a single-game high that "a series can look shorter than it was"
    sends them to look at the bracket, not at the games missing from the
    player's own line."""
    team = caveat(("team_box_stats",), 2001, POSTSEASON)
    assert team is not None and "Philadelphia's run" in team
    # Every table the player templates read says it in the player's words, and
    # says it alone - the log is declared separately from the box because
    # TEMPLATE_SOURCES is free to list either without the other.
    for table in ("player_box_stats", "player_game_log", "player_season_advanced_stats"):
        player = caveat((table,), 2001, POSTSEASON)
        assert player is not None, table
        assert "40 players" in player, table
        assert "Philadelphia's run" not in player, table
        assert caveat((table,), 2001, REGULAR_SEASON) is None, table


def test_espns_own_2001_season_line_is_complete_and_uncaveated() -> None:
    """The missing games cost the BOX SCORES, not ESPN's per-player season
    totals - it gives Shaquille O'Neal all 16 playoff games. Caveating
    `player_season_stats` would apologize for data that is there, and it is
    the table that proves the box scores are short."""
    assert caveat(("player_season_stats",), 2001, POSTSEASON) is None
    assert caveat(("player_season_stats_deduped",), 2001, POSTSEASON) is None
