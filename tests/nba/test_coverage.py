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

from shapes import key

from association.nba.coverage import COVERAGE, KNOWN_TABLES, POSTSEASON, REGULAR_SEASON, caveat, unavailable
from association.query.coverage import RELATION_SOURCES, SOURCES, check_coverage, coverage_caveat, sources_for
from association.query.reading import PointRelation, PointShape, Scope


def test_every_covered_table_is_a_real_table() -> None:
    """The keys are table names, and a typo would be a floor that never fires -
    silently restoring exactly the behavior this module removes."""
    assert set(COVERAGE) <= KNOWN_TABLES


def test_every_routed_shape_declares_the_tables_it_reads() -> None:
    """SOURCES and the shapes the answer side routes (``compose._ROUTES``,
    whose readers' answers check these floors) are two hand-maintained
    lists of the same keys, the shape that already produced the
    player_compare bug. A shape missing here is one no floor can ever
    refuse; a point no reader takes falls to its relation's own tables,
    declared for every relation a point can be on."""
    import typing

    from association.query import compose

    assert set(SOURCES) == set(compose._ROUTES)
    assert set(RELATION_SOURCES) == set(typing.get_args(PointRelation))


def test_a_reading_with_no_point_is_neither_refused_nor_caveated() -> None:
    """A refusal the reading comes to (`coach`, a championship) reads
    nothing, so a coverage floor has no purchase on it. Appending "there is
    no data for 1996" to a sentence that already explains what is missing
    would name a second, wrong cause."""
    assert sources_for(None, {"season": 1996}) == ()
    assert check_coverage(None, {"season": 1996, "season_type": REGULAR_SEASON}) is None
    assert coverage_caveat(None, {"season": 2015}) is None


def test_every_declared_source_has_a_floor() -> None:
    probes = (Scope(), Scope(stat="ts_pct"), Scope(stat="points"), Scope(stat="record"), Scope(season_type=3), Scope(opponent="Boston Celtics"), Scope(stat="netpoints"))
    for shape, declared in SOURCES.items():
        tables = {table for probe in probes for table in (declared(probe) if callable(declared) else declared)}
        for table in tables:
            assert table in COVERAGE, f"{shape} reads {table}, which declares no coverage floor"
    for relation, own in RELATION_SOURCES.items():
        for table in own:
            assert table in COVERAGE, f"{relation} reads {table}, which declares no coverage floor"


def test_a_season_below_the_floor_is_refused() -> None:
    got = check_coverage(key("shot_chart"), {"season": 1996, "season_type": REGULAR_SEASON})
    assert got is not None
    assert "2002" in got and "1996" in got


def test_the_refusal_says_no_pull_would_help() -> None:
    """The distinction that matters to somebody reading it: this is ESPN's gap,
    not a season nobody has fetched yet."""
    got = check_coverage(key("fingerprint"), {"season": 2005, "season_type": REGULAR_SEASON})
    assert got is not None and "no pull would add any" in got


def test_a_covered_season_is_not_refused() -> None:
    assert check_coverage(key("shot_chart"), {"season": 2003, "season_type": REGULAR_SEASON}) is None
    assert check_coverage(key("leaderboard"), {"stat": "points", "season": 1994, "season_type": REGULAR_SEASON}) is None


def test_an_absent_season_slot_means_the_current_season_and_is_never_refused() -> None:
    assert check_coverage(key("shot_chart"), {"season_type": REGULAR_SEASON}) is None


# ---------------- the two floors that are not one ----------------


def test_a_ranking_is_refused_where_a_named_player_is_not() -> None:
    """The sharpest line in the data. player_season_stats holds Michael
    Jordan's real 1990 line, so his own average is answerable from it; the pool
    it would RANK is 217 players against a ~350-player league, and 7 in 1980."""
    assert check_coverage(PointShape("player_seasons", "scalar", "line"), {"season": 1990, "season_type": REGULAR_SEASON}) is None
    assert check_coverage(key("player_compare"), {"season": 1990, "season_type": REGULAR_SEASON}) is None
    assert check_coverage(key("leaderboard"), {"stat": "points", "season": 1990, "season_type": REGULAR_SEASON}) is not None
    # The ranking's floor is the shape's: a named player's count of 30-point
    # games over the same tables is refused in the box scores' words.
    refused = check_coverage(key("threshold_count"), {"player": "Michael Jordan", "stat": "points", "threshold": 30, "season": 1990, "season_type": REGULAR_SEASON})
    assert refused is not None and refused.startswith("Player game logs")


def test_the_floor_follows_the_relation_the_point_reader_named() -> None:
    """ISSUES.md #212 (closed by Phase 3, step 1): the floor read four slots
    of its own to tell a season-line question from a box-score one, and the
    point reader a longer list, so "Jordan's points in 1990 on tuesdays"
    (a calendar narrowing, which only the reader knew sends the read to the
    box scores) was checked against the season line's 1977 floor and
    answered "no 1990 games found" - the wrong cause. The floor is keyed by
    the point now, so the two cannot disagree."""
    from association.query.compose.plan import point_shape
    from association.query.point import default_point

    for narrowing in ({"situation": "on tuesdays"}, {"since": 1989}, {"game_n": 3}):
        point = default_point("player_stat", Scope.from_slots({"player": "Michael Jordan", "stat": "points", "season": 1990, "season_type": REGULAR_SEASON, **narrowing}))
        assert point.on == "player_games", narrowing
        refused = check_coverage(point_shape(point), point.scope)
        assert refused is not None and refused.startswith("Player game logs only go back to 1994"), narrowing
    unnarrowed = default_point("player_stat", Scope.from_slots({"player": "Michael Jordan", "stat": "points", "season": 1990, "season_type": REGULAR_SEASON}))
    assert unnarrowed.on == "player_seasons" and check_coverage(point_shape(unnarrowed), unnarrowed.scope) is None


def test_a_game_level_ranking_is_floored_by_the_box_scores_it_reads() -> None:
    """A league ranking over a line the season line has no metric for
    ("how many players averaged 30 ppg", a count of games over a line) is
    the planner's game-level ranking, read from the box scores: its floor
    is theirs (1994), named in their words. Held to the metric's table -
    none - it answered "No games for every player in the 1986 regular
    season", the wrong cause."""
    game_level = PointShape("player_games", "ranking", "player")
    assert sources_for(game_level, {"stat": "points", "threshold": 30, "season": 1986}) == ("player_game_log", "player_box_stats", "games")
    refused = check_coverage(game_level, {"stat": "points", "threshold": 30, "season": 1986, "season_type": REGULAR_SEASON})
    assert refused is not None and refused.startswith("Player game logs only go back to 1994")
    assert check_coverage(game_level, {"stat": "points", "threshold": 30, "season": 1994, "season_type": REGULAR_SEASON}) is None


def test_the_ranking_refusal_does_not_claim_the_data_is_missing() -> None:
    """It is not missing - it is unrepresentative, and saying "no data for
    1980" about a warehouse holding Moses Malone's real 1980 line would be the
    same false-cause answer in the opposite direction."""
    got = check_coverage(key("leaderboard"), {"stat": "points", "season": 1980, "season_type": REGULAR_SEASON})
    assert got is not None
    assert "no data for 1980" not in got
    assert "would not be one" in got and "for a player you name are still available" in got


def test_a_ranking_over_a_table_with_no_survivor_problem_uses_the_plain_floor() -> None:
    """netpoints ranks over net_points_player, which is league-wide from its
    first season - so its refusal is about missing rows, not about the pool."""
    got = check_coverage(key("leaderboard"), {"stat": "netpoints", "season": 2010, "season_type": REGULAR_SEASON})
    assert got is not None and "no pull would add any" in got


def test_the_playoffs_reach_further_back_than_the_regular_season() -> None:
    """`games` holds full 16-team brackets to 1988 while its regular seasons
    before 1994 are one team's schedule. One floor would either refuse real
    playoff data or admit 82 games as a season."""
    assert check_coverage(key("head_to_head"), {"season": 1990, "season_type": POSTSEASON}) is None
    assert check_coverage(key("head_to_head"), {"season": 1990, "season_type": REGULAR_SEASON}) is not None


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
    assert check_coverage(key("shot_chart"), {"season": 2002, "season_type": REGULAR_SEASON}) is None
    note = coverage_caveat(key("shot_chart"), {"season": 2002})
    assert note is not None and "part of the year" in note


def test_a_full_season_carries_no_caveat() -> None:
    # 2005, not 2003: measured, 2003 has located shots for only 986 of its 1,190
    # games, and is a partial season itself.
    assert coverage_caveat(key("shot_chart"), {"season": 2005}) is None
    assert caveat(("shot_chart",), 2005) is None


def test_the_first_playoffs_on_record_is_1989_and_the_refusal_says_why() -> None:
    """ESPN's archive files the 1989 playoffs under 1988 and has no 1987-88
    postseason at all, so "the 1988 playoffs" is refused - in the postseason's
    own words, not the regular season's "one team's 82 games"."""
    got = check_coverage(key("head_to_head"), {"season": 1988, "season_type": POSTSEASON})
    assert got is not None and got.startswith("Playoff games only go back to 1989") and "82 games" not in got
    assert check_coverage(key("head_to_head"), {"season": 1989, "season_type": POSTSEASON}) is None


def test_2003_shots_carry_a_partial_season_caveat() -> None:
    assert coverage_caveat(key("shot_chart"), {"season": 2003}) is not None
    assert coverage_caveat(key("shot_chart"), {"season": 2004}) is None


def test_a_postseason_that_stops_early_is_caveated_on_postseason_questions() -> None:
    """ESPN's 2001 playoffs are missing ten games - Games 1-4 of the Final,
    three of the MIL-PHI conference final, two of MIL-CHA and one of LAL-SA -
    and no pull adds them:
    the scoreboard endpoint, which had the whole 2000 Final, answers 23 of
    those days with nothing at all."""
    note = coverage_caveat(key("head_to_head"), {"season": 2001, "season_type": POSTSEASON})
    assert note is not None and "2001 playoffs" in note


def test_the_same_season_carries_no_caveat_for_its_regular_season() -> None:
    """The whole reason postseason_partial is a separate field. 2001's REGULAR
    season is complete (1,190 games), so a note about missing playoff games
    would be a claim about the wrong half of the year - and one shared tuple
    would also exempt that complete season from its own floor check."""
    assert coverage_caveat(key("head_to_head"), {"season": 2001, "season_type": REGULAR_SEASON}) is None
    assert coverage_caveat(key("head_to_head"), {"season": 2001}) is None


def test_the_recovered_2000_postseason_carries_no_caveat() -> None:
    """2000's gap WAS recoverable: its nine missing games are on the scoreboard
    endpoint even though no team's schedule lists them, so they were fetched
    rather than caveated. Declaring it partial would be an apology for data
    that is now there."""
    assert coverage_caveat(key("head_to_head"), {"season": 2000, "season_type": POSTSEASON}) is None


def test_a_postseason_caveat_covers_the_team_box_as_well_as_the_game_list() -> None:
    """The missing games are absent from team_box_stats too, so a question
    built from it must say so rather than rely on `games` happening to be
    listed first in SOURCES."""
    assert caveat(("team_box_stats",), 2001, POSTSEASON) is not None
    assert caveat(("team_box_stats",), 2001, REGULAR_SEASON) is None


def test_the_2001_caveat_reaches_the_templates_that_never_read_the_game_list() -> None:
    """`single_game_high` and `threshold_count` read only the player tables, so
    a caveat declared on `games` alone never reached them: Shaquille O'Neal's
    "4 games with 30+ points" was stated as fact over 11 of the 16 playoff
    games he played."""
    for intent in ("single_game_high", "threshold_count"):
        note = coverage_caveat(key(intent), {"season": 2001, "season_type": POSTSEASON, "player": "Shaquille O'Neal", "stat": "points"})
        assert note is not None and "2001 playoffs" in note, intent
        assert coverage_caveat(key(intent), {"season": 2001, "season_type": REGULAR_SEASON, "player": "Shaquille O'Neal", "stat": "points"}) is None


def test_the_2001_player_caveat_says_what_a_player_is_missing() -> None:
    """A team's note and a player's are different claims. Telling somebody
    asking for a single-game high that "a series can look shorter than it was"
    sends them to look at the bracket, not at the games missing from the
    player's own line."""
    team = caveat(("team_box_stats",), 2001, POSTSEASON)
    assert team is not None and "Philadelphia's run" in team
    # Every table the player templates read says it in the player's words, and
    # says it alone - the log is declared separately from the box because
    # SOURCES is free to list either without the other.
    for table in ("player_box_stats", "player_game_log", "player_season_advanced_stats"):
        player = caveat((table,), 2001, POSTSEASON)
        assert player is not None, table
        assert "40 players" in player, table
        assert "Philadelphia's run" not in player, table
        assert caveat((table,), 2001, REGULAR_SEASON) is None, table


def test_each_partial_season_carries_its_own_sentence() -> None:
    """Two unrelated faults hit `team_box_stats`' postseasons - five games of
    1997 served with an empty box, ten games of 2001 absent from ESPN
    altogether - so one shared note would name both in an answer about either.
    That is the wrong-cause noise this module exists to stop."""
    ninety_seven = caveat(("team_box_stats",), 1997, POSTSEASON)
    two_thousand_one = caveat(("team_box_stats",), 2001, POSTSEASON)
    assert ninety_seven is not None and two_thousand_one is not None
    assert "Chicago-Miami" in ninety_seven and "2001" not in ninety_seven
    assert "2001 playoffs" in two_thousand_one and "Chicago-Miami" not in two_thousand_one
    # Same for the two partial shot seasons, whose game counts differ.
    assert "509 of 2002's" in (caveat(("shot_chart",), 2002) or "")
    assert "986 of 2003's" in (caveat(("shot_chart",), 2003) or "")


def test_an_empty_playoff_box_score_is_caveated_from_1995_to_1998() -> None:
    """ESPN lists these eight games and serves each one a box score with no
    player lines: probed live 2026-09-17, all eight return a `boxscore` with
    zero athlete lines where control games in the same seasons return 24.
    Michael Jordan's 1997 postseason reads 14 games against ESPN's own 19."""
    for season in (1995, 1996, 1997, 1998):
        note = coverage_caveat(key("threshold_count"), {"season": season, "season_type": POSTSEASON, "player": "Michael Jordan", "stat": "points"})
        assert note is not None and "empty box score" in note, season
        # The regular seasons of those years are whole.
        assert coverage_caveat(key("threshold_count"), {"season": season, "season_type": REGULAR_SEASON, "player": "Michael Jordan", "stat": "points"}) is None, season
    assert coverage_caveat(key("threshold_count"), {"season": 1999, "season_type": POSTSEASON, "player": "Michael Jordan", "stat": "points"}) is None


def test_the_game_list_is_not_caveated_for_an_empty_box_score() -> None:
    """The eight games are all IN `games`, with scores and a winner - it is
    only their box scores that are empty. So a playoff game count or a
    head-to-head record over them is right, and caveating it would apologize
    for data that is there. 2001 is the opposite case: those games are absent
    from `games` too."""
    assert caveat(("games",), 1997, POSTSEASON) is None
    assert coverage_caveat(key("head_to_head"), {"season": 1997, "season_type": POSTSEASON}) is None
    assert caveat(("games",), 2001, POSTSEASON) is not None


def test_a_2013_to_2018_shooting_board_says_who_is_missing_from_it() -> None:
    """The advanced table is summed from the stored box scores, which ESPN
    serves zeroed for every Chicago and New Orleans game in those seasons, so
    21-33 players a season clear the qualifying floor by ESPN's own season
    totals and fall under it here. The board dropped them silently."""
    for season in (2013, 2015, 2018):
        note = coverage_caveat(key("leaderboard"), {"season": season, "stat": "ts_pct"})
        assert note is not None and "missing from this ranking" in note, season
    for season in (2012, 2019):
        assert coverage_caveat(key("leaderboard"), {"season": season, "stat": "ts_pct"}) is None, season


def test_the_shooting_caveats_ranking_sentence_is_left_off_a_lookup() -> None:
    """ISSUES.md #293: "21 to 33 players ... are missing from this ranking
    entirely" was appended to one player's own rate. The note's lookup half
    stands alone off a ranking."""
    ranked = caveat(("player_season_advanced_stats",), 2015)
    looked_up = caveat(("player_season_advanced_stats",), 2015, ranking=False)
    assert ranked is not None and looked_up is not None
    assert ranked.endswith("so they are missing from this ranking entirely.") and "missing from this ranking" not in looked_up
    assert looked_up.endswith("so the attempts are short for anyone who played in one - not just those two rosters.")
    assert ranked.startswith(looked_up[:-1])


def test_the_shooting_caveat_does_not_reach_a_board_ranked_from_espns_own_totals() -> None:
    """`player_season_stats` is fetched per player and is complete for these
    seasons - Anthony Davis has all 1,656 of his 2015 points there. A points
    board reads it, so caveating one would apologize for data that is right,
    and it is the table the shortfall is measured against."""
    for season in (2013, 2015, 2018):
        assert coverage_caveat(key("leaderboard"), {"season": season, "stat": "points"}) is None, season
    assert caveat(("player_season_stats",), 2015) is None


def test_espns_own_2001_season_line_is_complete_and_uncaveated() -> None:
    """The missing games cost the BOX SCORES, not ESPN's per-player season
    totals - it gives Shaquille O'Neal all 16 playoff games. Caveating
    `player_season_stats` would apologize for data that is there, and it is
    the table that proves the box scores are short."""
    assert caveat(("player_season_stats",), 2001, POSTSEASON) is None
    assert caveat(("player_season_stats_deduped",), 2001, POSTSEASON) is None
