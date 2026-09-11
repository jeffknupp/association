"""Tests for the deterministic threshold_count template - including the exact
question that failed three times through the tool-calling agent."""

from pathlib import Path
from typing import Any

import duckdb
import pytest

from association.query import shotchart
from association.query.entities import MAX_CANDIDATES
from association.query.metrics import LEADERBOARD_METRICS, PER_GAME_MIN_GAMES, PER_GAME_MIN_POSTSEASON_GAMES
from association.query.templates import (
    HONORED_SCOPING,
    SCOPING_SLOTS,
    TemplateContext,
    TemplateUnsupported,
    check_scope,
    fingerprint,
    game_log,
    head_to_head,
    leaderboard,
    player_compare,
    player_history,
    player_netpoints,
    player_stat,
    shot_chart,
    shot_distance,
    single_game_high,
    team_quarter_points,
    team_record,
    threshold_count,
)
from association.season import current_season


@pytest.fixture
def con(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    # event_id/minutes/did_not_play are what an empty box score is detected by;
    # the defaults make every game here a real one.
    c.execute(
        "CREATE TABLE player_box_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, points INTEGER, rebounds INTEGER, "
        "event_id VARCHAR, minutes INTEGER DEFAULT 30, did_not_play BOOLEAN DEFAULT FALSE)"
    )
    c.execute("INSERT INTO players VALUES ('1','Luka Doncic'),('2','Shai Gilgeous-Alexander'),('3','Bench Guy')")
    season = current_season()
    rows: list[tuple[Any, ...]] = []
    rows += [("1", season, 2, 35, 5)] * 4  # Luka: 4 games of 30+
    rows += [("2", season, 2, 31, 4)] * 2  # SGA: 2 games of 30+
    rows += [("1", season, 2, 12, 22)] * 3  # Luka: 3 games of 20+ rebounds
    rows += [("3", season, 2, 40, 1)] * 9  # postseason-only below, so excluded
    rows += [("3", season, 3, 40, 1)] * 9
    rows += [("2", season - 1, 2, 40, 1)] * 7  # previous season, excluded by default
    c.executemany("INSERT INTO player_box_stats (athlete_id, season, season_type, points, rebounds) VALUES (?,?,?,?,?)", rows)
    return TemplateContext(con=c, out_dir=tmp_path)


def test_answers_the_question_the_agent_kept_getting_wrong(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert result.data["leaders"][0] == {"player": "Bench Guy", "games": 9}
    assert {"player": "Luka Doncic", "games": 4} in result.data["leaders"]


def test_defaults_to_the_current_season(con: TemplateContext) -> None:
    # The old path lost this rule to prompt truncation and answered for 2024.
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert result.data["season"] == current_season()


def test_explicit_season_is_honoured(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 40, "season": current_season() - 1})
    assert result.data["leaders"] == [{"player": "Shai Gilgeous-Alexander", "games": 7}]


def test_restricted_to_regular_season(con: TemplateContext) -> None:
    # Bench Guy has 9 regular-season and 9 postseason 40-point games; only the
    # regular-season ones count.
    result = threshold_count(con, {"stat": "points", "threshold": 40})
    assert result.data["leaders"] == [{"player": "Bench Guy", "games": 9}]


def test_filters_to_a_named_player_on_every_token(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "rebounds", "threshold": 20, "player": "Luka Doncic"})
    assert result.data["leaders"] == [{"player": "Luka Doncic", "games": 3}]


def test_unknown_stat_falls_through_instead_of_reaching_sql(con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        threshold_count(con, {"stat": "points); DROP TABLE players; --", "threshold": 30})


def test_missing_threshold_falls_through(con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        threshold_count(con, {"stat": "points"})


def test_limit_is_clamped(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 1, "limit": 10_000})
    assert len(result.data["leaders"]) <= 50


def test_empty_result_is_reported_as_empty_not_invented(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 999})
    assert result.data["leaders"] == []


def test_answer_is_deterministic_prose_so_no_model_call_is_needed(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert result.answer == (f"Bench Guy had the most games with 30+ points in the {current_season()} regular season, with 9. Next: Luka Doncic (4), Shai Gilgeous-Alexander (2).")


def test_answer_always_names_the_season_explicitly(con: TemplateContext) -> None:
    # The original failure silently answered for 2024 when the user meant the
    # current season; naming it makes that class of mistake visible.
    assert f"{current_season()} regular season" in (threshold_count(con, {"stat": "points", "threshold": 30}).answer or "")


def test_answer_reports_an_empty_result_honestly(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 999})
    assert result.answer == f"No player had a game with 999+ points in the {current_season()} regular season."


def test_answer_for_a_single_named_player(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "rebounds", "threshold": 20, "player": "Luka Doncic"})
    assert result.answer == f"Luka Doncic had 3 games with 20+ rebounds in the {current_season()} regular season."


@pytest.fixture
def currys(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('7','Stephen Curry'),('8','Seth Curry')")
    c.execute(
        "CREATE TABLE player_box_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, points INTEGER, event_id VARCHAR, minutes INTEGER DEFAULT 30, did_not_play BOOLEAN DEFAULT FALSE)"
    )
    season = current_season()
    rows: list[tuple[Any, ...]] = []
    rows += [("7", season, 2, 35)] * 3 + [("8", season, 2, 31)] * 5  # both have 30-point games
    rows += [("7", season - 1, 2, 32)] * 2  # only Stephen played
    rows += [("7", season - 2, 2, 12)] * 4 + [("8", season - 2, 2, 30)]  # both played, only Seth reached 30
    c.executemany("INSERT INTO player_box_stats (athlete_id, season, season_type, points) VALUES (?,?,?,?)", rows)
    return TemplateContext(con=c, out_dir=tmp_path)


@pytest.mark.parametrize("back", [0, 2, 3])
def test_a_name_two_players_could_answer_to_is_asked_about(currys: TemplateContext, back: int) -> None:
    """Asked, not counted for whichever Curry leads. Two seasons back only Seth
    has a qualifying game, but both played: narrowing on the answer rather than
    on who played would be the prominence tiebreak by another route. Three back
    neither played, and the question is asked about both, as it always was."""
    result = threshold_count(currys, {"stat": "points", "threshold": 30, "player": "Curry", "season": current_season() - back})
    assert result.data == {"ambiguous": "Curry", "candidates": ["Seth Curry", "Stephen Curry"]}


def test_a_name_only_one_player_with_games_that_season_answers_to_is_answered(currys: TemplateContext) -> None:
    season = current_season() - 1
    result = threshold_count(currys, {"stat": "points", "threshold": 30, "player": "Curry", "season": season})
    assert result.answer == f"Stephen Curry had 2 games with 30+ points in the {season} regular season."


def test_a_name_at_the_candidate_cap_is_asked_about_rather_than_narrowed(tmp_path: Path) -> None:
    """find_players stops at MAX_CANDIDATES, alphabetically, so narrowing that
    list can leave one survivor while a player past the cut played too - the
    first Jones here, where the last one also has games. Measured on the real
    warehouse, "Williams" in 2023 answered Alondes Williams."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.executemany("INSERT INTO players VALUES (?,?)", [(str(i), f"{chr(ord('A') + i)}ay Jones") for i in range(MAX_CANDIDATES + 1)])
    c.execute(
        "CREATE TABLE player_box_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, points INTEGER, event_id VARCHAR, minutes INTEGER DEFAULT 30, did_not_play BOOLEAN DEFAULT FALSE)"
    )
    played = [("0", current_season(), 2, 30), (str(MAX_CANDIDATES), current_season(), 2, 30)]  # the first Jones, and the one the cap cuts off
    c.executemany("INSERT INTO player_box_stats (athlete_id, season, season_type, points) VALUES (?,?,?,?)", played)
    result = threshold_count(TemplateContext(con=c, out_dir=tmp_path), {"stat": "points", "threshold": 30, "player": "Jones"})
    assert result.data.get("ambiguous") == "Jones", result.answer


def test_answer_reports_a_tie_as_a_tie(con: TemplateContext) -> None:
    con.con.execute("INSERT INTO player_box_stats (athlete_id, season, season_type, points, rebounds) SELECT '1', season, 2, 35, 5 FROM player_box_stats LIMIT 5")
    result = threshold_count(con, {"stat": "points", "threshold": 30})
    assert "tied for the most" in (result.answer or "")


# ---------------- leaderboard ----------------


@pytest.fixture
def lb_con(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE player_season_stats (athlete_id VARCHAR, team_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER, avgPoints DOUBLE)")
    c.execute("INSERT INTO players VALUES ('1','Luka Doncic'),('2','Stephen Curry')")
    c.execute("INSERT INTO teams VALUES ('9','GS','Golden State Warriors'),('6','DAL','Dallas Mavericks')")
    s = current_season()
    c.execute("INSERT INTO player_season_stats VALUES ('1','6',?,2,70,33.5),('2','9',?,2,68,27.1),('2','9',?,3,10,31.0)", [s, s, s])
    return TemplateContext(con=c, out_dir=tmp_path)


def test_leaderboard_maps_a_plain_stat_slot_onto_a_real_metric(lb_con: TemplateContext) -> None:
    result = leaderboard(lb_con, {"stat": "points"})
    assert result.data["leaders"][0]["display_name"] == "Luka Doncic"


def test_leaderboard_phrases_its_own_answer(lb_con: TemplateContext) -> None:
    result = leaderboard(lb_con, {"stat": "points", "limit": 2})
    assert result.answer == (f"Luka Doncic led the league in points per game in the {current_season()} regular season (minimum 20 games), at 33.5. Next: Stephen Curry (27.1).")


def test_leaderboard_names_the_team_when_filtered(lb_con: TemplateContext) -> None:
    result = leaderboard(lb_con, {"stat": "points", "team": "Warriors"})
    assert "led the Golden State Warriors" in (result.answer or "")


def test_leaderboard_honours_playoffs(lb_con: TemplateContext) -> None:
    result = leaderboard(lb_con, {"stat": "points", "season_type": 3})
    assert "postseason" in (result.answer or "") and result.data["leaders"][0]["value"] == 31.0


def test_leaderboard_unmapped_stat_falls_through_rather_than_fuzzy_matching(lb_con: TemplateContext) -> None:
    # Deliberately NOT get_close_matches: silently ranking by whichever metric
    # scored highest is the substitution failure this design exists to prevent.
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "clutchness"})


def test_leaderboard_ambiguous_team_falls_through_rather_than_picking_one(lb_con: TemplateContext) -> None:
    lb_con.con.execute("INSERT INTO teams VALUES ('12','LAC','LA Clippers'),('13','LAL','Los Angeles Lakers')")
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "points", "team": "LA"})


def test_leaderboard_unknown_team_falls_through(lb_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "points", "team": "Not A Team"})


def test_threshold_count_honours_playoffs(con: TemplateContext) -> None:
    result = threshold_count(con, {"stat": "points", "threshold": 40, "season_type": 3})
    assert "postseason" in (result.answer or "")
    assert result.data["leaders"] == [{"player": "Bench Guy", "games": 9}]


@pytest.fixture
def shooting_ctx(tmp_path: Path) -> TemplateContext:
    """Real 2025 rows. On a 20-game qualifier, Kai Jones's 109 shots led the
    league in true shooting at .804, with Patrick Baldwin Jr.'s 35 third; in
    the playoffs, the same qualifier kept Thomas Bryant's 33 shots and dropped
    Jarrett Allen, who shot .792 on 61 in nine games."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Kai Jones'),('2','Patrick Baldwin Jr.'),('3','Jarrett Allen'),('4','Nikola Jokic'),('5','Isaiah Joe'),('6','Thomas Bryant')")
    c.execute(
        "CREATE TABLE player_season_advanced_stats (season INTEGER, season_type INTEGER, athlete_id VARCHAR, games_played BIGINT, "
        "field_goals_attempted BIGINT, true_shooting_attempts DOUBLE, ts_pct DOUBLE, efg_pct DOUBLE)"
    )
    c.executemany(
        "INSERT INTO player_season_advanced_stats VALUES (2025,?,?,?,?,?,?,?)",
        [
            (2, "1", 40, 109, 123.08, 0.804, 0.803),
            (2, "2", 24, 35, 36.76, 0.721, 0.729),
            (2, "3", 82, 640, 761.88, 0.724, 0.706),
            (2, "4", 70, 1364, 1562.44, 0.663, 0.627),
            (3, "3", 9, 61, 76.40, 0.792, 0.721),
            (3, "5", 21, 73, 79.16, 0.676, 0.651),
            (3, "6", 20, 33, 39.16, 0.664, 0.621),
            (3, "4", 14, 268, 312.44, 0.587, 0.539),
        ],
    )
    return TemplateContext(con=c, out_dir=tmp_path)


def test_true_shooting_says_which_qualifier_it_ranked_under(shooting_ctx: TemplateContext) -> None:
    result = leaderboard(shooting_ctx, {"stat": "true_shooting", "season": 2025})
    assert result.answer == "Jarrett Allen led the league in true shooting % in the 2025 regular season (minimum 550 true-shooting attempts), at 0.724. Next: Nikola Jokic (0.663)."


@pytest.mark.parametrize(("stat", "qualifier"), [("true_shooting", "550 true-shooting attempts"), ("efg_pct", "480 field-goal attempts")])
def test_a_shooting_percentage_qualifies_on_attempts_not_games(shooting_ctx: TemplateContext, stat: str, qualifier: str) -> None:
    """Both low-volume players have the games; neither has the shots."""
    result = leaderboard(shooting_ctx, {"stat": stat, "season": 2025})
    assert [r["display_name"] for r in result.data["leaders"]] == ["Jarrett Allen", "Nikola Jokic"]
    assert f"2025 regular season (minimum {qualifier})," in (result.answer or "")


@pytest.mark.parametrize(("stat", "qualifier"), [("true_shooting", "67 true-shooting attempts"), ("efg_pct", "59 field-goal attempts")])
def test_a_shooting_percentage_scales_its_qualifier_for_the_postseason(shooting_ctx: TemplateContext, stat: str, qualifier: str) -> None:
    """The season floor is more than one player reached in the whole 2025
    postseason, so it cannot carry over, and games cannot stand in for it."""
    result = leaderboard(shooting_ctx, {"stat": stat, "season": 2025, "season_type": 3})
    assert [r["display_name"] for r in result.data["leaders"]] == ["Jarrett Allen", "Isaiah Joe", "Nikola Jokic"]
    assert f"2025 postseason (minimum {qualifier})," in (result.answer or "")


def test_an_empty_board_says_what_nobody_met(shooting_ctx: TemplateContext) -> None:
    result = leaderboard(shooting_ctx, {"stat": "true_shooting", "season": 2024})
    assert result.answer == "No players qualified for true shooting % in the league in the 2024 regular season (minimum 550 true-shooting attempts)."


@pytest.fixture
def games_ctx(tmp_path: Path) -> TemplateContext:
    """Real rows in the two shapes that had no games qualifier.

    Danny Fortson played 6 games in 2000-01 and averaged 16.3 rebounds, which
    led the league; Dikembe Mutombo, who actually led it, averaged 13.5 over
    79. In the 2023 playoffs Kawhi Leonard played 2 games at 34.5 points and
    led the postseason; Devin Booker led it at 33.7 over 17.
    """
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Danny Fortson'),('2','Dikembe Mutombo'),('3','Ben Wallace'),('4','Kawhi Leonard'),('5','Devin Booker'),('6','Anthony Edwards')")
    c.execute(
        "CREATE TABLE player_season_stats (athlete_id VARCHAR, team_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER, "
        "avgRebounds DOUBLE, totalRebounds INTEGER, avgPoints DOUBLE, points INTEGER)"
    )
    c.executemany(
        "INSERT INTO player_season_stats VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("1", "5", 2001, 2, 6, 16.3, 98, 11.3, 68),
            ("2", "6", 2001, 2, 79, 13.5, 1071, 11.7, 926),
            ("3", "7", 2001, 2, 80, 13.2, 1052, 6.4, 511),
            ("4", "8", 2023, 3, 2, 5.5, 11, 34.5, 69),
            ("5", "9", 2023, 3, 17, 4.7, 80, 33.7, 573),
            ("6", "10", 2023, 3, 5, 6.0, 30, 31.6, 158),
        ],
    )
    return TemplateContext(con=c, out_dir=tmp_path)


def test_a_per_game_leaderboard_qualifies_on_games(games_ctx: TemplateContext) -> None:
    """Regression: "who led the league in rebounding in 2001" answered Danny
    Fortson, on 6 games. Measured over 1994-2026 against the warehouse, two
    regular-season boards (2000 and 2001 rebounding) and 15 postseason ones
    were led from under the floors the newer per-game metrics already used."""
    result = leaderboard(games_ctx, {"stat": "rebounds", "season": 2001})
    assert [r["display_name"] for r in result.data["leaders"]] == ["Dikembe Mutombo", "Ben Wallace"]
    assert result.data["min_sample"] == PER_GAME_MIN_GAMES


def test_a_per_game_leaderboard_names_the_qualifier_it_applied(games_ctx: TemplateContext) -> None:
    """A floor nobody is told about is why "why isn't Fortson here?" has no
    answer - and the leader's own 79 games are the reason he is."""
    answer = leaderboard(games_ctx, {"stat": "rebounds", "season": 2001}).answer
    assert answer == "Dikembe Mutombo led the league in rebounds per game in the 2001 regular season (minimum 20 games), at 13.5. Next: Ben Wallace (13.2)."


def test_a_per_game_leaderboard_scales_its_qualifier_for_the_postseason(games_ctx: TemplateContext) -> None:
    """20 games is more than a title run, so the season floor cannot carry
    over; 5 is more than a first-round sweep. Kawhi Leonard's 2 games led 2023
    playoff scoring, and Anthony Edwards's 5 still qualify."""
    result = leaderboard(games_ctx, {"stat": "points", "season": 2023, "season_type": 3})
    assert [r["display_name"] for r in result.data["leaders"]] == ["Devin Booker", "Anthony Edwards"]
    assert result.data["min_sample"] == PER_GAME_MIN_POSTSEASON_GAMES
    assert "(minimum 5 games)" in (result.answer or "")


def test_every_per_game_metric_carries_both_games_qualifiers() -> None:
    """The bug was one omission in a list, so the guard is over the whole list
    rather than the three stats it was reported for. A per-game average with
    no floor is led by whoever played fewest games. Counting metrics are
    deliberately not here: a season total or a double-double count needs
    volume to rank at all, and measured over 1994-2026 no season total, and no
    double-double count, was ever led from under these floors."""
    unqualified = [
        name
        for name, spec in LEADERBOARD_METRICS.items()
        if spec.table == "player_season_stats" and spec.column.startswith("avg") and (spec.default_min_sample != PER_GAME_MIN_GAMES or spec.postseason_min_sample != PER_GAME_MIN_POSTSEASON_GAMES)
    ]
    assert unqualified == []


# ---------------- player_stat ----------------


@pytest.fixture
def ps_con(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, "
        "gamesPlayed INTEGER, avgPoints DOUBLE, points INTEGER, avgRebounds DOUBLE, avgAssists DOUBLE, assists INTEGER, "
        "avgSteals DOUBLE, avgBlocks DOUBLE, avgTurnovers DOUBLE, avgFouls DOUBLE, avgMinutes DOUBLE, fouls INTEGER)"
    )
    c.execute("INSERT INTO players VALUES ('1','Luka Doncic'),('2','Luka Garza'),('3','Nikola Jokic'),('4','Stephen Curry'),('5','Seth Curry')")
    s = current_season()
    c.execute("INSERT INTO player_season_stats_deduped VALUES ('1',?,2,64,33.5,2143,7.7,8.3,531,1.4,0.5,4.0,2.1,37.5,134)", [s])
    c.execute("INSERT INTO player_season_stats_deduped VALUES ('3',?,2,65,27.7,1799,12.9,10.7,697,1.3,0.9,3.6,2.5,34.6,163)", [s])
    # player_compare reports a NetPoints summary under the box-score line. Only
    # Luka has a row, so the "some players have none" path is exercised too.
    c.execute(
        "CREATE TABLE net_points_player (athlete_id VARCHAR, season INTEGER, net_points_season_type VARCHAR, overall_per_100_poss DOUBLE, offense_per_100_poss DOUBLE, defense_per_100_poss DOUBLE)"
    )
    c.execute("INSERT INTO net_points_player VALUES ('1',?,'Regular Season',5.87,5.20,0.67)", [s])
    return TemplateContext(con=c, out_dir=tmp_path)


def test_player_stat_reports_one_named_stat_with_its_total(ps_con: TemplateContext) -> None:
    result = player_stat(ps_con, {"player": "Luka Doncic", "stat": "points"})
    assert result.answer == (f"Luka Doncic averaged 33.5 points per game in 64 games in the {current_season()} regular season. That is 2,143 in total.")


def test_player_stat_with_no_stat_gives_a_stat_line(ps_con: TemplateContext) -> None:
    result = player_stat(ps_con, {"player": "Nikola Jokic"})
    assert result.answer == (f"Nikola Jokic averaged 27.7 points, 12.9 rebounds and 10.7 assists per game in 65 games in the {current_season()} regular season.")


def test_player_stat_asks_instead_of_guessing_between_players(ps_con: TemplateContext) -> None:
    """Neither guess nor fall through: the template knows exactly what is
    ambiguous, so it says so in ~1.5s instead of handing the agent a problem
    it would spend minutes guessing at."""
    result = player_stat(ps_con, {"player": "Curry", "stat": "points"})
    assert result.answer == "'Curry' matches more than one player - did you mean Seth Curry or Stephen Curry?"
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry"]


def test_player_stat_unknown_player_falls_through(ps_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_stat(ps_con, {"player": "Nobody At All"})


def test_player_stat_missing_player_slot_falls_through(ps_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_stat(ps_con, {"stat": "points"})


def test_player_stat_reports_a_missing_season_honestly(ps_con: TemplateContext) -> None:
    result = player_stat(ps_con, {"player": "Luka Doncic", "season": 1999})
    assert result.answer == "Luka Doncic has no 1999 regular season numbers in the warehouse."


def test_player_stat_never_reports_a_total_as_a_per_game_number(ps_con: TemplateContext) -> None:
    # Regression on phrasing: the total used to be inlined as "33.5 points
    # (2143 total) per game", which states something false.
    answer = player_stat(ps_con, {"player": "Luka Doncic", "stat": "points"}).answer or ""
    assert "(2,143 total) per game" not in answer and "2143" not in answer


# ---------------- a surname, narrowed to the season asked about ----------------


@pytest.fixture
def curry_ctx(tmp_path: Path) -> TemplateContext:
    """The six Currys the warehouse really holds. "How did curry do against the
    celtics this year" asked about five of them - Dell, Eddy, JamesOn, Michael
    and Seth - and hid Stephen behind "(1 others also match)", because the list
    was sorted by name and cut at five. Four of the five it named never played
    in the season being asked about.

    Each has a season line in a year he really played; only Seth and Stephen
    have one in the current season, as in the warehouse."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Dell Curry'),('2','Eddy Curry'),('3','JamesOn Curry'),('4','Michael Curry'),('5','Seth Curry'),('6','Stephen Curry')")
    c.execute(
        "CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, "
        "gamesPlayed INTEGER, avgPoints DOUBLE, points INTEGER, avgRebounds DOUBLE, avgAssists DOUBLE, assists INTEGER)"
    )
    s = current_season()
    c.executemany(
        "INSERT INTO player_season_stats_deduped VALUES (?,?,2,60,?,?,3.0,4.0,240)",
        [("1", 2000, 10.0, 600), ("2", 2005, 13.0, 780), ("3", 2010, 1.0, 3), ("4", 2000, 5.0, 300), ("5", 2014, 5.0, 300), ("5", s, 8.0, 480), ("6", 2010, 17.5, 1400), ("6", s, 26.6, 1600)],
    )
    c.execute(
        "CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, game_date VARCHAR, "
        "opponent_abbr VARCHAR, minutes DOUBLE, points INTEGER, rebounds INTEGER, assists INTEGER)"
    )
    c.execute("INSERT INTO player_game_log VALUES ('6',?,2,'2026-01-02','BOS',34.0,31,5,6)", [s])
    return TemplateContext(con=c, out_dir=tmp_path)


def test_a_surname_asks_only_about_the_players_who_played_that_season(curry_ctx: TemplateContext) -> None:
    """The reported answer, verbatim, was "did you mean Dell Curry, Eddy Curry,
    JamesOn Curry, Michael Curry or Seth Curry (1 others also match)?" - the
    player almost certainly meant was the one it left out. Seth and Stephen
    both played this season, so it still asks; it just asks about them."""
    result = player_stat(curry_ctx, {"player": "Curry", "season": current_season()})
    assert result.answer == "'Curry' matches more than one player - did you mean Seth Curry or Stephen Curry?"
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry"]


def test_a_surname_resolves_when_only_one_candidate_played_that_season(curry_ctx: TemplateContext) -> None:
    """Elimination, not preference: the other five have no row to answer
    from, so there is nothing left to choose between."""
    curry_ctx.con.execute("DELETE FROM player_season_stats_deduped WHERE athlete_id = '5'")
    answer = player_stat(curry_ctx, {"player": "Curry", "stat": "points"}).answer
    assert answer == f"Stephen Curry averaged 26.6 points per game in 60 games in the {current_season()} regular season. That is 1,600 in total."


def test_narrowing_that_eliminates_everybody_still_asks_about_everybody(curry_ctx: TemplateContext) -> None:
    """No Curry played in 1990, so no answer to "which one?" has numbers - but
    picking one because the season is empty would be a guess, so the question
    is asked exactly as it was before narrowing existed."""
    result = player_stat(curry_ctx, {"player": "Curry", "season": 1990})
    assert result.data["candidates"] == ["Dell Curry", "Eddy Curry", "JamesOn Curry", "Michael Curry", "Seth Curry", "Stephen Curry"]


def test_a_name_given_in_full_is_never_narrowed_away(curry_ctx: TemplateContext) -> None:
    """ "Gary Payton" also matches Gary Payton II, and only the son has a line
    this season - as in the warehouse, where the father's last is 2007. A
    question naming the father in full is about him, and its answer is that he
    has no numbers, not his son's line because the son is the Payton who does.

    Not "Dell Curry": that name matches one player, so it never reaches the
    exact-match rule this is about - and a perturbation removing the rule
    passed against it."""
    curry_ctx.con.execute("INSERT INTO players VALUES ('7','Gary Payton'),('8','Gary Payton II')")
    curry_ctx.con.execute("INSERT INTO player_season_stats_deduped VALUES ('7',2007,2,70,5.0,350,2.0,3.0,210),('8',?,2,60,7.0,420,3.0,2.0,120)", [current_season()])
    answer = player_stat(curry_ctx, {"player": "Gary Payton", "season": current_season()}).answer
    assert answer == f"Gary Payton has no {current_season()} regular season numbers in the warehouse."


def test_a_history_narrows_over_every_season_it_could_read(curry_ctx: TemplateContext) -> None:
    """A history anchored at 2005 reads each player's last seasons up to 2005,
    wherever they fall, so only a player with nothing that early is out: Seth,
    Stephen and JamesOn. Narrowing to 2005 alone would also drop Dell and
    Michael, whose histories through 2005 are real answers - Eddy, who played
    in it, is only named first."""
    result = player_history(curry_ctx, {"player": "Curry", "stat": "points", "season": 2005})
    assert result.data["candidates"] == ["Eddy Curry", "Dell Curry", "Michael Curry"]


def test_a_history_names_whoever_reached_its_last_season_first(curry_ctx: TemplateContext) -> None:
    """ "Curry's scoring over the last 4 seasons" keeps all six Currys - each
    has seasons through 2026 to answer with - and so hid Stephen behind the
    cap exactly as the one-season question did. The two who played in the
    season the history ends at are named first, and never counted away."""
    result = player_history(curry_ctx, {"player": "Curry", "stat": "points", "limit": 4})
    assert result.answer == "'Curry' matches more than one player - did you mean Seth Curry, Stephen Curry, Dell Curry, Eddy Curry or JamesOn Curry (1 other also matches)?"


def test_a_career_keeps_every_curry_but_names_the_active_ones_first(curry_ctx: TemplateContext) -> None:
    """A career question has every season in scope, so Dell's career is as real
    an answer as Stephen's and nobody is eliminated. Narrowed like one season,
    it would have been; left unordered, the cap would hide Stephen again."""
    result = player_stat(curry_ctx, {"player": "Curry", "span": "career"})
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry", "Dell Curry", "Eddy Curry", "JamesOn Curry", "Michael Curry"]
    assert result.answer == "'Curry' matches more than one player - did you mean Seth Curry, Stephen Curry, Dell Curry, Eddy Curry or JamesOn Curry (1 other also matches)?"


def test_a_game_log_narrows_to_whoever_has_games_that_season(curry_ctx: TemplateContext) -> None:
    """No season named means the current one. Dell has games in 2000 and Seth
    and Stephen this season, so only the two of them are asked about.

    Measured on the warehouse, not in a fixture: after game_log learned spans,
    its `season` became the raw slot, and passing that through narrowed
    "curry's last 5 games" over every season - the clarification named Dell,
    Eddy, JamesOn, Michael and Seth, and hid Stephen again."""
    curry_ctx.con.execute("INSERT INTO player_game_log VALUES ('1',2000,2,'2000-01-02','BOS',30.0,12,2,3),('5',?,2,'2026-01-03','BOS',20.0,8,1,2)", [current_season()])
    result = game_log(curry_ctx, {"player": "Curry", "limit": 5, "order": "recent"})
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry"]


def test_a_without_teammate_is_found_past_the_first_page_of_matches() -> None:
    """ "Without williams" is 62 players by name. The teammate narrowing read
    only find_players' first page of ten, so a teammate who sorted eleventh was
    reported as nobody's teammate at all."""
    from association.query.entities import Entity
    from association.query.templates import _resolved_teammate, _Span

    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    given = ["Alan", "Brandon", "Cody", "Deron", "Eric", "Frank", "Grant", "Hank", "Ike", "Jalen", "Kenrich"]
    c.executemany("INSERT INTO players VALUES (?, ?)", [(str(i), f"{name} Williams") for i, name in enumerate(given)])
    c.execute("INSERT INTO players VALUES ('k', 'Klay Thompson')")
    c.execute("CREATE TABLE player_box_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR)")
    c.execute("INSERT INTO player_box_stats VALUES ('k', 2026, 2, '9'), ('10', 2026, 2, '9'), ('0', 2026, 2, '5')")
    got = _resolved_teammate(c, "Williams", Entity(id="k", name="Klay Thompson"), _Span(2026, 2))
    assert got == Entity(id="10", name="Kenrich Williams")


def test_a_career_high_names_the_currys_playing_now_first(curry_ctx: TemplateContext) -> None:
    """single_game_high reads a career as every season on record, so Dell stays
    a candidate - but left unanchored, the list was in name order and the cap
    would cut Stephen behind the retired Currys."""
    curry_ctx.con.execute("INSERT INTO player_game_log VALUES ('1',2000,2,'2000-01-02','BOS',30.0,12,2,3),('5',?,2,'2026-01-03','BOS',20.0,8,1,2)", [current_season()])
    result = single_game_high(curry_ctx, {"stat": "points", "player": "Curry", "span": "career"})
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry", "Dell Curry"]


def test_netpoints_narrows_against_either_of_the_tables_it_reads(curry_ctx: TemplateContext) -> None:
    """player_netpoints answers from the season totals OR the fingerprint, and
    the two disagree about who they hold - 63 player-seasons are in the first
    only and 8 in the second only. A candidate with a row in either one has an
    answer, so neither table alone may eliminate him."""
    curry_ctx.con.execute("CREATE TABLE net_points_player (athlete_id VARCHAR, season INTEGER)")
    curry_ctx.con.execute("CREATE TABLE net_points_player_fingerprint (athlete_id VARCHAR, season INTEGER)")
    curry_ctx.con.execute("INSERT INTO net_points_player VALUES ('6', ?)", [current_season()])
    curry_ctx.con.execute("INSERT INTO net_points_player_fingerprint VALUES ('5', ?)", [current_season()])
    result = player_netpoints(curry_ctx, {"player": "Curry"})
    assert result.data["candidates"] == ["Seth Curry", "Stephen Curry"]


def test_netpoints_without_its_tables_asks_as_it_always_did(curry_ctx: TemplateContext) -> None:
    """NetPoints is an opt-in fetch, so its tables may not exist at all. With
    nothing to narrow against, an ambiguous name gets the question it always
    got, rather than a catalog error out of the resolver."""
    result = player_netpoints(curry_ctx, {"player": "Curry"})
    assert len(result.data["candidates"]) == 6


def test_leaderboard_handles_triple_doubles_as_a_metric_not_a_recount(lb_con: TemplateContext) -> None:
    """ESPN precomputes doubleDouble/tripleDouble as a season count, so "most
    triple-doubles" is a leaderboard rather than a per-game threshold recount."""
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN tripleDouble INTEGER")
    lb_con.con.execute("UPDATE player_season_stats SET tripleDouble = 34 WHERE athlete_id = '1'")
    lb_con.con.execute("UPDATE player_season_stats SET tripleDouble = 2 WHERE athlete_id = '2'")
    result = leaderboard(lb_con, {"stat": "triple_double", "limit": 2})
    assert result.answer == (f"Luka Doncic led the league in triple-doubles in the {current_season()} regular season, at 34. Next: Stephen Curry (2).")


# ---------------- team_record / game_log ----------------


@pytest.fixture
def gl_con(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE standings (team_id VARCHAR, season INTEGER, wins DOUBLE, losses DOUBLE, winPercent DOUBLE, streak DOUBLE, playoffSeed DOUBLE)")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, "
        "home_team_id VARCHAR, away_team_id VARCHAR, home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)"
    )
    c.execute("CREATE TABLE team_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, home_away VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('18','NY','New York Knicks'),('2','BOS','Boston Celtics')")
    s = current_season()
    c.execute("INSERT INTO standings VALUES ('18',?,53.0,29.0,0.646,3.0,4.0)", [s])
    c.execute(
        "INSERT INTO games VALUES ('e1',?,2,'2026-04-10T22:00Z','18','2',112,95,'18'),('e2',?,2,'2026-04-12T22:00Z','2','18',110,96,'2')",
        [s, s],
    )
    c.execute("INSERT INTO team_box_stats VALUES ('e1',?,2,'18','2','home'),('e2',?,2,'18','2','away')", [s, s])
    return TemplateContext(con=c, out_dir=tmp_path)


@pytest.fixture
def tq_con(tmp_path: Path) -> TemplateContext:
    """games.home_linescores/away_linescores plus the team_box_stats rows for
    BOTH sides of each game (team_quarter_points needs a team's own
    perspective row regardless of which side of the matchup it queries from,
    unlike gl_con, which only ever queries the Knicks)."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, "
        "home_team_id VARCHAR, away_team_id VARCHAR, home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR, "
        "home_linescores VARCHAR, away_linescores VARCHAR)"
    )
    c.execute("CREATE TABLE team_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, home_away VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('18','NY','New York Knicks'),('2','BOS','Boston Celtics'),('5','LAL','Los Angeles Lakers')")
    s = current_season()
    c.execute(
        "INSERT INTO games VALUES "
        "('e1',?,2,'2026-04-10T22:00Z','18','2',112,95,'18','30,25,28,29','20,25,25,25'),"
        "('e2',?,2,'2026-04-12T22:00Z','2','18',110,96,'2','25,30,25,30','20,20,28,28'),"
        "('e3',?,2,'2026-04-14T22:00Z','18','5',108,100,'18','10,32,36,30','25,25,25,25')",
        [s, s, s],
    )
    c.execute(
        "INSERT INTO team_box_stats VALUES "
        "('e1',?,2,'18','2','home'),('e1',?,2,'2','18','away'),"
        "('e2',?,2,'18','2','away'),('e2',?,2,'2','18','home'),"
        "('e3',?,2,'18','5','home'),('e3',?,2,'5','18','away')",
        [s, s, s, s, s, s],
    )
    return TemplateContext(con=c, out_dir=tmp_path)


# team_record's own tests are in test_team_templates.py, over a fixture with
# the standings columns it reads.


def test_game_log_includes_home_and_away_games(gl_con: TemplateContext) -> None:
    """Regression: joining games by home_team_id only silently returns a
    team's home games and drops every away game, with no error."""
    games = game_log(gl_con, {"team": "Knicks"}).data["games"]
    assert {g["home_away"] for g in games} == {"home", "away"}


def test_game_log_reports_each_games_own_score_from_that_teams_side(gl_con: TemplateContext) -> None:
    by_date = {g["date"]: g for g in game_log(gl_con, {"team": "Knicks"}).data["games"]}
    assert (by_date["2026-04-10"]["team_score"], by_date["2026-04-10"]["opponent_score"]) == (112, 95)
    # Away game: the Knicks' score is the AWAY score, not the home one.
    assert (by_date["2026-04-12"]["team_score"], by_date["2026-04-12"]["opponent_score"]) == (96, 110)


def test_game_log_tallies_the_record_over_exactly_the_rows_shown(gl_con: TemplateContext) -> None:
    result = game_log(gl_con, {"team": "Knicks"})
    assert result.data["wins"] == 1
    assert "(1-1)" in (result.answer or "")


def test_game_log_order_first_is_ascending(gl_con: TemplateContext) -> None:
    # LIMIT 1 without an explicit ORDER BY returns an arbitrary row, not the
    # earliest one.
    first = game_log(gl_con, {"team": "Knicks", "order": "first", "limit": 1}).data["games"]
    assert first[0]["date"] == "2026-04-10"
    recent = game_log(gl_con, {"team": "Knicks", "limit": 1}).data["games"]
    assert recent[0]["date"] == "2026-04-12"


def test_game_log_filters_an_exact_calendar_date(gl_con: TemplateContext) -> None:
    # games.date is a full ISO timestamp, so `= 'YYYY-MM-DD'` is valid SQL that
    # silently matches nothing.
    games = game_log(gl_con, {"team": "Knicks", "date": "2026-04-12"}).data["games"]
    assert [g["date"] for g in games] == ["2026-04-12"]


def test_game_log_ignores_a_malformed_date_rather_than_matching_nothing(gl_con: TemplateContext) -> None:
    games = game_log(gl_con, {"team": "Knicks", "date": "April 12"}).data["games"]
    assert len(games) == 2


def test_game_log_ambiguous_team_asks(gl_con: TemplateContext) -> None:
    gl_con.con.execute("INSERT INTO teams VALUES ('12','LAC','LA Clippers'),('13','LAL','Los Angeles Lakers')")
    assert "did you mean" in (game_log(gl_con, {"team": "LA"}).answer or "")


def test_game_log_without_team_or_player_falls_through(gl_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        game_log(gl_con, {"limit": 5})


def test_team_record_refuses_a_limited_set_rather_than_reporting_the_full_season(gl_con: TemplateContext) -> None:
    """ "How did they do in their last 10?" answered with the full-season record
    is a silent substitution - game_log tallies over exactly the games shown."""
    with pytest.raises(TemplateUnsupported):
        team_record(gl_con, {"team": "Knicks", "limit": 10})


# ---------------- shot_chart ----------------


@pytest.fixture
def sc_ctx(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE shot_chart (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, "
        "period INTEGER, clock VARCHAR, made BOOLEAN, shot_type VARCHAR, coordinate_x INTEGER, coordinate_y INTEGER, points_attempted INTEGER, description VARCHAR)"
    )
    c.execute("INSERT INTO players VALUES ('1','Stephen Curry')")
    # Two labeled threes from the top of the arc, 26 feet from the rim at
    # (25, 0) - a real three's position, so the label and the line agree.
    c.executemany(
        "INSERT INTO shot_chart VALUES ('1',?,2,'e1',1,'10:00',?,'Jump Shot',25,26,3,'26-foot three point jumper')",
        [(current_season(), True), (current_season(), False)],
    )
    return TemplateContext(con=c, out_dir=tmp_path / "out")


def test_shot_chart_writes_a_file_and_reports_its_path(sc_ctx: TemplateContext) -> None:
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": current_season()})
    assert "Rendered shot chart for Stephen Curry" in (result.answer or "")
    assert list((sc_ctx.out_dir).glob("*.html"))


def test_shot_chart_narrows_a_surname_to_whoever_took_shots_that_season(sc_ctx: TemplateContext) -> None:
    """Seth exists and Stephen has the shots, so "Curry" is not a question this
    season - only one of them can have produced the chart being asked for."""
    sc_ctx.con.execute("INSERT INTO players VALUES ('2','Seth Curry')")
    result = shot_chart(sc_ctx, {"player": "Curry", "season": current_season()})
    assert "Rendered shot chart for Stephen Curry" in (result.answer or "")


def test_shot_chart_asks_which_player_when_both_took_shots(sc_ctx: TemplateContext) -> None:
    """And when narrowing cannot separate them it asks, rather than drawing
    whichever sorts first and titling the plot with the wrong Curry."""
    sc_ctx.con.execute("INSERT INTO players VALUES ('2','Seth Curry')")
    sc_ctx.con.execute(f"INSERT INTO shot_chart VALUES ('2',{current_season()},2,'e2',1,'9:00',TRUE,'Jump Shot',25,26,3,'26-foot three point jumper')")
    result = shot_chart(sc_ctx, {"player": "Curry", "season": current_season()})
    assert result.answer == "'Curry' matches more than one player - did you mean Seth Curry or Stephen Curry?"
    assert not list(sc_ctx.out_dir.glob("*.html"))


def test_shot_chart_reports_no_matching_shots_rather_than_falling_through(sc_ctx: TemplateContext) -> None:
    # The agent has no better source for a chart than the table just queried,
    # so an empty result is the answer, not a reason to spend minutes.
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": 1999})
    assert "No shots found" in (result.answer or "")


def test_shot_chart_ignores_a_nonsense_shot_value(sc_ctx: TemplateContext) -> None:
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": current_season(), "shot_value": 0})
    assert "Rendered shot chart" in (result.answer or "")


def test_shot_chart_without_a_player_falls_through(sc_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        shot_chart(sc_ctx, {"season": current_season()})


def test_a_scoped_chart_uses_the_same_player_it_looked_the_game_up_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The chart and the game it is scoped to must be about ONE player.

    shot_chart used to resolve the name twice - once to find the event_id and
    again inside the renderer - agreeing only by convention. Two independent
    resolutions can pick differently, and the result would be a chart titled for
    one Curry scoped to a game the other one played: wrong, and invisible, since
    the plot looks perfectly normal.

    Both Currys exist and both played this season; only Stephen took a shot in
    it, which is what narrows the name to him. The scoping game must then be
    his, not Seth's.
    """
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE shot_chart (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, "
        "period INTEGER, clock VARCHAR, made BOOLEAN, shot_type VARCHAR, coordinate_x INTEGER, coordinate_y INTEGER, points_attempted INTEGER, description VARCHAR)"
    )
    c.execute("CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, game_date VARCHAR)")
    # Two players both matching "Curry", each with their own game. Seth's game
    # is the LATER one, so an unnarrowed "most recent Curry game" would scope to
    # it - and only Stephen has a shot this season, so the chart must be his.
    c.execute("INSERT INTO players VALUES ('1','Seth Curry'), ('2','Stephen Curry')")
    c.executemany(
        "INSERT INTO player_game_log VALUES (?,?,2,?,?)",
        [("1", current_season(), "seth_game", "2026-01-03"), ("2", current_season(), "steph_game", "2026-01-02")],
    )
    c.executemany(
        "INSERT INTO shot_chart VALUES (?,?,2,?,1,'10:00',true,'Jump Shot',25,26,3,'26-foot three point jumper')",
        [("1", current_season() - 1, "seth_old_game"), ("2", current_season(), "steph_game")],
    )
    ctx = TemplateContext(con=c, out_dir=tmp_path / "out")

    calls = []
    real = shotchart.find_players

    def counting(con: Any, text: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(text)
        return real(con, text, *args, **kwargs)

    monkeypatch.setattr(shotchart, "find_players", counting)
    result = shot_chart(ctx, {"player": "Curry", "order": "recent", "season": current_season()})

    # The load-bearing assertion: the name is resolved ONCE. Two resolutions
    # agree only as long as both spell the tie-break the same way, which is a
    # convention, not a guarantee.
    assert len(calls) == 1, f"player resolved {len(calls)} times: {calls}"

    answer = result.answer or ""
    assert "Stephen Curry" in answer
    written = [f.name for f in ctx.out_dir.glob("*.html")]
    assert len(written) == 1
    # The scoping event_id lands in the filename; it must be the charted
    # player's game, not the other candidate's.
    assert "seth_game" not in written[0] and "steph_game" in written[0]


def test_shot_chart_defaults_an_unspecified_season_to_the_current_one(sc_ctx: TemplateContext) -> None:
    """Passing None through charted a player's entire career in one plot
    (confirmed live: 3,665 Curry attempts across every season)."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2019,2,'e9',1,'5:00',TRUE,'Jump Shot',10,10,2,'makes 18-foot jumper')")
    answer = shot_chart(sc_ctx, {"player": "Stephen Curry"}).answer or ""
    # Only this season's two shots, not the 2019 one as well.
    assert "1/2 made" in answer and str(current_season()) in answer


# ---------------- player_compare ----------------


def test_player_compare_puts_players_side_by_side(ps_con: TemplateContext) -> None:
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"]}).answer or ""
    assert "Luka Doncic vs Nikola Jokic" in answer
    assert "points" in answer and "33.5" in answer and "27.7" in answer


def test_player_compare_defaults_to_the_whole_stat_line(ps_con: TemplateContext) -> None:
    """A comparison asks which player is better, and three counting stats
    cannot answer that - they omit both halves of the defensive line and
    everything a player gives back. A table costs nothing per row, so the rows
    a comparison turns on are all there by default. player_stat is unchanged:
    "how many points did Luka average" wants the number it asked for."""
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"]}).answer or ""
    for label in ("points", "rebounds", "assists", "steals", "blocks", "turnovers", "fouls", "minutes"):
        assert f"\n{label}" in answer, label
    assert "4.0" in answer and "3.6" in answer  # turnovers, which the old line omitted


def test_player_compare_shows_the_netpoints_summary(ps_con: TemplateContext) -> None:
    """Per 100 possessions, not season totals: a comparison is exactly the
    question totals answer badly, since they mostly rank by playing time."""
    result = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"]})
    answer = result.answer or ""
    assert "net pts/100" in answer and "offense" in answer and "defense" in answer
    assert "+5.87" in answer and "+5.20" in answer and "+0.67" in answer
    assert result.data["netpoints"]["Luka Doncic"]["overall_per_100_poss"] == 5.87


def test_a_player_with_no_netpoints_row_is_blank_rather_than_zero(ps_con: TemplateContext) -> None:
    """Only Luka has a row in the fixture. Drawing Jokic as +0.00 would read as
    "contributed nothing" rather than "not on record"."""
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"]}).answer or ""
    netpoints_row = next(line for line in answer.splitlines() if line.startswith("net pts/100"))
    assert "+5.87" in netpoints_row and "+0.00" not in netpoints_row and netpoints_row.rstrip().endswith("-")


def test_player_compare_omits_netpoints_entirely_when_nobody_has_any(ps_con: TemplateContext) -> None:
    """An empty NetPoints block under two 1990s players would read as "both
    contributed nothing" rather than "this season predates the data"."""
    ps_con.con.execute("DELETE FROM net_points_player")
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"]}).answer or ""
    assert "net pts/100" not in answer
    assert "33.5" in answer  # the rest of the comparison is unaffected


def test_player_compare_survives_a_warehouse_with_no_netpoints_table(ps_con: TemplateContext) -> None:
    """NetPoints is a separate opt-in fetch, so the table may not exist at all.
    Unlike per-game NetPoints, which has nothing else to say, a comparison is
    complete without it - so this drops the section instead of falling through
    to an agent that has no better source."""
    ps_con.con.execute("DROP TABLE net_points_player")
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"]}).answer or ""
    assert "net pts/100" not in answer and "33.5" in answer


def test_a_named_stat_still_narrows_the_comparison(ps_con: TemplateContext) -> None:
    """ "Who scores more" gets scoring, not a wall."""
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"], "stat": "points"}).answer or ""
    assert "\npoints" in answer
    assert "\nrebounds" not in answer and "\nsteals" not in answer


def test_a_comparison_is_not_refused_before_netpoints_begins(ps_con: TemplateContext) -> None:
    """The NetPoints table is deliberately absent from player_compare's
    TEMPLATE_SOURCES: listing it would put a 2019 coverage floor on every
    comparison and refuse the 1994-2018 ones outright."""
    from association.query.templates import check_coverage

    assert check_coverage("player_compare", {"season": 2005, "season_type": 2}) is None


def test_player_compare_uses_a_table_not_prose(ps_con: TemplateContext) -> None:
    """The agent's prose version of this stated that a player with 0.4 steals
    led one with 1.6. A table cannot make that mistake."""
    lines = (player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"]}).answer or "").splitlines()
    assert len(lines) >= 5 and lines[1].strip().startswith("Luka Doncic")


def test_player_compare_aligns_decimals_consistently(ps_con: TemplateContext) -> None:
    ps_con.con.execute("UPDATE player_season_stats_deduped SET avgPoints = 25.0 WHERE athlete_id = '3'")
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"], "stat": "points"}).answer or ""
    assert "25.0" in answer  # not a trailing-zero-stripped "25" beside "33.5"


def test_player_compare_resolves_a_nickname(ps_con: TemplateContext) -> None:
    ps_con.con.execute("INSERT INTO players VALUES ('9','Shai Gilgeous-Alexander')")
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "SGA"]}).answer or ""
    assert "Shai Gilgeous-Alexander" in answer


def test_player_compare_asks_rather_than_guessing_an_ambiguous_name(ps_con: TemplateContext) -> None:
    answer = player_compare(ps_con, {"players": ["Curry", "Nikola Jokic"]}).answer or ""
    assert "did you mean Seth Curry or Stephen Curry?" in answer


def test_no_template_outside_player_intents_reads_a_player_slot() -> None:
    """PLAYER_INTENTS decides whether a name the question does not support is
    refused or ignored, so a template drifting into reading a player slot
    without being listed would answer about somebody the question never named.
    Read out of the source rather than trusted, the way TEMPLATE_SOURCES is
    checked against TEMPLATES."""
    import inspect

    from association.query.templates import PLAYER_INTENTS, TEMPLATES

    for intent, handler in TEMPLATES.items():
        source = inspect.getsource(handler)
        reads = 'slots.get("player' in source or 'slots["player' in source
        assert reads == (intent in PLAYER_INTENTS), f"{intent} reads a player slot: {reads}, listed: {intent in PLAYER_INTENTS}"


def test_player_compare_suggests_the_player_a_fabricated_name_meant(ps_con: TemplateContext) -> None:
    """The router answered "compare sga and embid" with 'Jemel Embiid' - the
    surname corrected, the given name invented - and every token has to match,
    so a name the warehouse holds was buried by one made-up word."""
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Jemel Jokic"]}).answer or ""
    assert answer == "No player found matching 'Jemel Jokic' - did you mean Nikola Jokic?"


def test_player_compare_answers_a_near_miss_rather_than_falling_through(ps_con: TemplateContext) -> None:
    """Handled here for the same reason ambiguity is: the agent would resolve
    the same name against the same table, more slowly."""
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Nikoal Jokic"]}).answer or ""
    assert "did you mean Nikola Jokic?" in answer


def test_a_name_with_nothing_near_it_still_falls_through(ps_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_compare(ps_con, {"players": ["Luka Doncic", "Asdf Qwerty"]})


def test_player_compare_needs_two_distinct_players(ps_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_compare(ps_con, {"players": ["Luka Doncic"]})
    with pytest.raises(TemplateUnsupported):
        player_compare(ps_con, {"players": ["Luka Doncic", "Luka Doncic"]})
    with pytest.raises(TemplateUnsupported):
        player_compare(ps_con, {"player": "Luka Doncic"})


def test_player_compare_reports_a_player_with_no_rows_rather_than_dropping_them(ps_con: TemplateContext) -> None:
    ps_con.con.execute("INSERT INTO players VALUES ('9','Shai Gilgeous-Alexander')")
    answer = player_compare(ps_con, {"players": ["Luka Doncic", "Shai Gilgeous-Alexander"]}).answer or ""
    assert "has no" in answer and "Shai Gilgeous-Alexander" in answer


def test_player_compare_is_capped(ps_con: TemplateContext) -> None:
    from association.query.templates import MAX_COMPARED_PLAYERS

    ps_con.con.execute("INSERT INTO players VALUES ('9','A A'),('10','B B'),('11','C C'),('12','D D')")
    result = player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic", "A A", "B B", "C C", "D D"]})
    assert len(result.data["players"]) <= MAX_COMPARED_PLAYERS


def test_shot_chart_reads_threes_from_either_slot(sc_ctx: TemplateContext) -> None:
    """ "Curry's threes" comes back as shot_value 3 or as the equivalent
    box-score stat depending on wording; both mean the same thing."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e2',1,'9:00',TRUE,'Layup',5,5,2,'makes layup')", [current_season()])
    by_value = shot_chart(sc_ctx, {"player": "Stephen Curry", "shot_value": 3}).answer or ""
    by_stat = shot_chart(sc_ctx, {"player": "Stephen Curry", "stat": "threePointFieldGoalsMade"}).answer or ""
    assert "1/2 made" in by_value and "1/2 made" in by_stat


def test_leaderboard_includes_requested_extra_fields(lb_con: TemplateContext) -> None:
    """Regression: "top N in X alongside their points per game" was answered
    without the second half, and without saying so."""
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    lb_con.con.execute("UPDATE player_season_stats SET avgRebounds = 7.7 WHERE athlete_id = '1'")
    result = leaderboard(lb_con, {"stat": "points", "fields": ["rebounds"]})
    assert result.data["fields"] == ["rebounds"]
    assert "rebounds" in (result.answer or "") and "7.7" in (result.answer or "")


def test_leaderboard_with_fields_renders_a_table(lb_con: TemplateContext) -> None:
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    answer = leaderboard(lb_con, {"stat": "points", "fields": ["rebounds"]}).answer or ""
    assert len(answer.splitlines()) >= 3


def test_leaderboard_without_fields_stays_a_sentence(lb_con: TemplateContext) -> None:
    answer = leaderboard(lb_con, {"stat": "points"}).answer or ""
    assert len(answer.splitlines()) == 1 and "led the league" in answer


def test_leaderboard_unknown_field_falls_through_rather_than_being_dropped(lb_con: TemplateContext) -> None:
    # Silently ignoring it would answer a narrower question than was asked.
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "points", "fields": ["clutchness"]})


def test_leaderboard_table_keeps_the_metrics_own_precision(lb_con: TemplateContext) -> None:
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    lb_con.con.execute("UPDATE player_season_stats SET avgPoints = 9.91 WHERE athlete_id = '1'")
    answer = leaderboard(lb_con, {"stat": "points", "fields": ["rebounds"]}).answer or ""
    assert "9.91" in answer  # not rounded to 9.9 by the table's field formatting


def test_leaderboard_table_names_the_qualifying_minimum(lb_con: TemplateContext) -> None:
    """ "Why isn't X on this list?" should have a visible answer."""
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    answer = leaderboard(lb_con, {"stat": "points", "fields": ["rebounds"], "limit": 2}).answer or ""
    assert "minimum" not in answer or "games" in answer or "minutes" in answer


def test_leaderboard_tolerates_a_repeated_field(lb_con: TemplateContext) -> None:
    """Confirmed live: the router returned ["points","minutes","minutes"]. A
    repeat is a harmless slip, not a reason to fall through to the agent."""
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    result = leaderboard(lb_con, {"stat": "points", "fields": ["rebounds", "rebounds"]})
    assert result.data["fields"] == ["rebounds"]
    assert (result.answer or "").splitlines()[1].count("rebounds") == 1


def test_leaderboard_drops_a_field_that_restates_the_ranked_metric(lb_con: TemplateContext) -> None:
    """Confirmed live: asked for "top scorers with their rebounds", the router
    also returned "points", which rendered the same 33.5 twice under two
    different headings."""
    lb_con.con.execute("ALTER TABLE player_season_stats ADD COLUMN avgRebounds DOUBLE")
    result = leaderboard(lb_con, {"stat": "points", "fields": ["rebounds", "points"]})
    assert result.data["fields"] == ["rebounds"]


# ---------------- single_game_high ----------------


@pytest.fixture
def sgh_ctx(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, player_name VARCHAR, game_date VARCHAR, opponent_abbr VARCHAR, assists INTEGER, points INTEGER)")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Ryan Nembhard'),('2','Nikola Jokic')")
    # Read only to count empty box scores; none here.
    c.execute("CREATE TABLE player_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, athlete_id VARCHAR, minutes INTEGER, did_not_play BOOLEAN)")
    s = current_season()
    c.executemany(
        "INSERT INTO player_game_log VALUES (?,?,?,?,?,?,?,?)",
        [
            ("1", s, 2, "Ryan Nembhard", "2026-04-13T00:30Z", "CHI", 23, 8),
            ("2", s, 2, "Nikola Jokic", "2026-03-26T02:00Z", "DAL", 19, 30),
            ("2", s, 2, "Nikola Jokic", "2026-01-02T02:00Z", "UTA", 11, 40),
            ("1", s, 3, "Ryan Nembhard", "2026-05-01T00:30Z", "BOS", 30, 5),
            ("2", s - 1, 2, "Nikola Jokic", "2025-03-26T02:00Z", "DAL", 25, 30),
        ],
    )
    return TemplateContext(con=c, out_dir=tmp_path)


def test_single_game_high_answers_the_question_a_leaderboard_answered_wrongly(sgh_ctx: TemplateContext) -> None:
    """Confirmed live: with no such intent, "who had the most assists in a
    single game" was answered "Nikola Jokic led the league in assists per game,
    at 10.7" in 1.76s. The real answer was Ryan Nembhard with 23."""
    answer = single_game_high(sgh_ctx, {"stat": "assists"}).answer or ""
    assert answer.startswith("Ryan Nembhard had the most assists in a single game")
    # Stored as 2026-04-13T00:30Z: 8:30pm Eastern on the 12th, the day it was played.
    assert "23" in answer and "2026-04-12" in answer and "CHI" in answer


def test_single_game_high_is_a_maximum_not_an_average(sgh_ctx: TemplateContext) -> None:
    games = single_game_high(sgh_ctx, {"stat": "assists"}).data["games"]
    assert games[0]["value"] == 23  # not Jokic's 15.0 average across his two games


def test_single_game_high_defaults_to_the_regular_season(sgh_ctx: TemplateContext) -> None:
    # Nembhard's 30-assist game is postseason and must not win the default.
    assert single_game_high(sgh_ctx, {"stat": "assists"}).data["games"][0]["value"] == 23
    assert single_game_high(sgh_ctx, {"stat": "assists", "season_type": 3}).data["games"][0]["value"] == 30


def test_single_game_high_defaults_to_the_current_season(sgh_ctx: TemplateContext) -> None:
    assert single_game_high(sgh_ctx, {"stat": "assists"}).data["season"] == current_season()
    assert single_game_high(sgh_ctx, {"stat": "assists", "season": current_season() - 1}).data["games"][0]["value"] == 25


def test_single_game_high_for_a_named_player(sgh_ctx: TemplateContext) -> None:
    answer = single_game_high(sgh_ctx, {"stat": "assists", "player": "Nikola Jokic"}).answer or ""
    assert answer == f"Nikola Jokic's highest assist total in a single game in the {current_season()} regular season was 19, on 2026-03-25 vs DAL."


def test_single_game_high_reports_a_tie_as_a_tie(sgh_ctx: TemplateContext) -> None:
    sgh_ctx.con.execute("UPDATE player_game_log SET assists = 23 WHERE player_name = 'Nikola Jokic' AND opponent_abbr = 'DAL' AND season_type = 2")
    assert "tied for the most" in (single_game_high(sgh_ctx, {"stat": "assists"}).answer or "")


def test_single_game_high_unknown_stat_falls_through(sgh_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        single_game_high(sgh_ctx, {"stat": "assists); DROP TABLE players; --"})


def test_single_game_high_ambiguous_player_asks(sgh_ctx: TemplateContext) -> None:
    # Jovic needs a game this season to be a candidate at all. A name is
    # narrowed to the players with a row where the answer is read from, and
    # with Jokic alone in this season's log, "Nikola" is answered about him.
    sgh_ctx.con.execute("INSERT INTO players VALUES ('3','Nikola Jovic')")
    sgh_ctx.con.execute("INSERT INTO player_game_log VALUES ('3',?,2,'Nikola Jovic','2026-02-01T00:30Z','BOS',4,12)", [current_season()])
    assert "did you mean" in (single_game_high(sgh_ctx, {"stat": "assists", "player": "Nikola"}).answer or "")


def test_single_game_high_reports_an_empty_season_honestly(sgh_ctx: TemplateContext) -> None:
    assert "no 1999 regular season games" in (single_game_high(sgh_ctx, {"stat": "assists", "season": 1999}).answer or "")


# ---------------- head_to_head ----------------


def test_head_to_head_counts_games_in_both_directions(gl_con: TemplateContext) -> None:
    """Regression: `games` is home/away-oriented, and the agent's version also
    compared team_id to an abbreviation, so it reported that two teams who met
    four times had never played."""
    result = head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "season": current_season()})
    assert result.data["games"] == 2  # one home, one away


def test_head_to_head_reports_the_series_record(gl_con: TemplateContext) -> None:
    answer = head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "season": current_season()}).answer or ""
    assert "met 2 times" in answer and "splitting them 1-1" in answer


def test_head_to_head_applies_the_season_to_the_whole_matchup(gl_con: TemplateContext) -> None:
    # `A OR B AND season = ...` binds the season to one side only; the template
    # parenthesizes the matchup so the filter covers both orderings.
    assert head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "season": 1999}).data["games"] == 0


def test_head_to_head_reports_no_meetings_honestly(gl_con: TemplateContext) -> None:
    assert "no" in (head_to_head(gl_con, {"teams": ["Knicks", "Celtics"], "season": 1999}).answer or "").lower()


def test_head_to_head_defaults_to_the_current_season(gl_con: TemplateContext) -> None:
    """Not all-time: answering a different span than every other template,
    silently, is the substitution this design exists to prevent."""
    gl_con.con.execute("INSERT INTO games VALUES ('e9',?,2,'2020-01-01T00:00Z','18','2',100,90,'18')", [current_season() - 3])
    result = head_to_head(gl_con, {"teams": ["Knicks", "Celtics"]})
    assert result.data["games"] == 2
    assert f"{current_season()} regular season" in (result.answer or "")


def test_head_to_head_needs_two_distinct_teams(gl_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        head_to_head(gl_con, {"teams": ["Knicks"]})
    with pytest.raises(TemplateUnsupported):
        head_to_head(gl_con, {"teams": ["Knicks", "New York Knicks"]})


def test_head_to_head_asks_on_an_ambiguous_team(gl_con: TemplateContext) -> None:
    gl_con.con.execute("INSERT INTO teams VALUES ('12','LAC','LA Clippers'),('13','LAL','Los Angeles Lakers')")
    assert "did you mean" in (head_to_head(gl_con, {"teams": ["LA", "Celtics"]}).answer or "")


def test_head_to_head_reads_the_second_team_from_the_team_slot(gl_con: TemplateContext) -> None:
    """Regression: "how many times did the Knicks play Boston?" - a city name
    with no nickname - routes the router to split the two teams across `team`
    and a one-element `teams`, rather than both into `teams` as its prompt
    asks (confirmed live: qwen2.5:3b, "...play Boston?" vs. "...play the
    Celtics?"). `team` resolves the city name on its own; the miss was this
    slot split, not name resolution, so a real one-vs-one matchup should still
    answer rather than fall through to the agent."""
    result = head_to_head(gl_con, {"team": "Knicks", "teams": ["Celtics"], "season": current_season()})
    assert result.data["games"] == 2


def test_head_to_head_ignores_a_team_slot_that_only_restates_teams(gl_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        head_to_head(gl_con, {"team": "Knicks", "teams": ["Knicks"]})


def test_team_quarter_points_reads_each_games_own_side_of_linescores(tq_con: TemplateContext) -> None:
    """Regression: "how many points did the 76ers score in the 4th quarter
    against Boston this season?" forced the router's _AGENT_ONLY override to
    the agent, which spent 3 model calls (~150s) filtering a nonexistent
    games.period column, then a broken LAG() over play_id, then comparing
    home_team_id directly to an abbreviation - the opaque-id mistake its own
    prompt warns against. games.home_linescores/away_linescores already store
    the exact per-period score for each side; this just has to read the right
    one for each game (home vs away), not always the same column."""
    result = team_quarter_points(tq_con, {"team": "Knicks", "period": 1, "season": current_season()})
    assert result.data["total"] == 60  # 30 (home in e1) + 20 (away in e2) + 10 (home in e3)
    assert len(result.data["games"]) == 3


def test_team_quarter_points_filters_to_a_named_opponent(tq_con: TemplateContext) -> None:
    result = team_quarter_points(tq_con, {"team": "Knicks", "opponent": "Celtics", "period": 4, "season": current_season()})
    assert result.data["total"] == 57  # 29 (e1) + 28 (e2) - e3 (vs Lakers) excluded
    assert len(result.data["games"]) == 2
    assert "Boston Celtics" in (result.answer or "")


def test_team_quarter_points_reports_no_games_honestly(tq_con: TemplateContext) -> None:
    # The Lakers and Celtics never played each other in this fixture (only
    # each played the Knicks) - an empty result must say so, not answer 0.
    result = team_quarter_points(tq_con, {"team": "Lakers", "opponent": "Celtics", "period": 1, "season": current_season()})
    assert result.data["games"] == []
    assert "no" in (result.answer or "").lower()


def test_team_quarter_points_reports_a_period_no_game_reached(tq_con: TemplateContext) -> None:
    # Every game in the fixture has exactly 4 quarters on record - asking for
    # a 5th (overtime) must say none of the games went there, not silently
    # answer 0 or crash on a short list index.
    result = team_quarter_points(tq_con, {"team": "Knicks", "opponent": "Celtics", "period": 5, "season": current_season()})
    assert "overtime" in (result.answer or "").lower()
    assert "total" not in result.data


def test_team_quarter_points_refuses_a_named_player(tq_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_quarter_points(tq_con, {"team": "Knicks", "period": 4, "player": "Jalen Brunson"})


def test_team_quarter_points_refuses_a_missing_period(tq_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_quarter_points(tq_con, {"team": "Knicks"})


def test_team_quarter_points_refuses_the_team_as_its_own_opponent(tq_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        team_quarter_points(tq_con, {"team": "Knicks", "opponent": "New York Knicks", "period": 1})


def test_team_quarter_points_phrases_a_single_game_directly(tq_con: TemplateContext) -> None:
    result = team_quarter_points(tq_con, {"team": "Knicks", "opponent": "Lakers", "period": 1, "season": current_season()})
    assert result.data["total"] == 10
    assert "2026-04-14" in (result.answer or "") or str(current_season()) in (result.answer or "")


def test_team_quarter_points_summarizes_rather_than_tables_many_games(tq_con: TemplateContext, monkeypatch: pytest.MonkeyPatch) -> None:
    # A whole-season, no-opponent question can span dozens of games - listing
    # every one is unreadable, so above a small cap this reports the total
    # and average instead of a per-game breakdown.
    monkeypatch.setattr("association.query.templates._QUARTER_BREAKDOWN_LIMIT", 1)
    result = team_quarter_points(tq_con, {"team": "Knicks", "period": 1, "season": current_season()})
    answer = result.answer or ""
    assert "averaging" in answer
    assert "2026-04-10" not in answer


def test_player_stat_refuses_a_named_stat_it_cannot_provide(ps_con: TemplateContext) -> None:
    """Confirmed live: an unsupported stat fell back to the default stat line,
    so "avg 3pt shot distance" was answered with points/rebounds/assists."""
    with pytest.raises(TemplateUnsupported):
        player_stat(ps_con, {"player": "Luka Doncic", "stat": "shot_distance"})


def test_player_stat_still_defaults_when_no_stat_was_named(ps_con: TemplateContext) -> None:
    assert "points" in (player_stat(ps_con, {"player": "Luka Doncic"}).answer or "")
    assert "points" in (player_stat(ps_con, {"player": "Luka Doncic", "stat": ""}).answer or "")


def test_player_compare_refuses_a_named_stat_it_cannot_provide(ps_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_compare(ps_con, {"players": ["Luka Doncic", "Nikola Jokic"], "stat": "shot_distance"})


def test_player_stat_supports_the_shooting_stats_the_router_emits(ps_con: TemplateContext) -> None:
    ps_con.con.execute("ALTER TABLE player_season_stats_deduped ADD COLUMN avgThreePointFieldGoalsMade DOUBLE")
    ps_con.con.execute("ALTER TABLE player_season_stats_deduped ADD COLUMN threePointFieldGoalsMade INTEGER")
    ps_con.con.execute("UPDATE player_season_stats_deduped SET avgThreePointFieldGoalsMade = 4.4, threePointFieldGoalsMade = 282 WHERE athlete_id = '1'")
    answer = player_stat(ps_con, {"player": "Luka Doncic", "stat": "threePointFieldGoalsMade"}).answer or ""
    assert "4.4 3-pointers" in answer and "282 in total" in answer


# ---------------- shot_distance ----------------


def test_shot_distance_filters_to_the_shot_value_asked_for(sc_ctx: TemplateContext) -> None:
    """Confirmed live: the agent wrote the right distance formula, then dropped
    both the 3-point filter and the season filter and reported an all-shots,
    all-seasons average of 16.94 as a current-season three-point distance."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e5',1,'1:00',TRUE,'Layup',25,1,2,'makes layup')", [current_season()])
    threes = shot_distance(sc_ctx, {"player": "Stephen Curry", "shot_value": 3})
    everything = shot_distance(sc_ctx, {"player": "Stephen Curry"})
    assert threes.data["attempts"] == 2 and everything.data["attempts"] == 3
    assert threes.data["avg_feet"] > everything.data["avg_feet"]


def test_shot_distance_measures_from_the_rim(sc_ctx: TemplateContext) -> None:
    """The rim is at (25, 0) in ESPN's coordinates - y is measured from it, not
    from the baseline - so the fixture's shot at (25, 26) is 26 feet out. The
    old frame put the rim at (25, 5.25) and made it 20.75, which is how Stephen
    Curry's 2026 threes came out at 23.6 feet, inside the line."""
    result = shot_distance(sc_ctx, {"player": "Stephen Curry", "shot_value": 3})
    assert result.data["avg_feet"] == pytest.approx(26.0)


def test_shot_distance_counts_threes_espn_left_unlabeled(sc_ctx: TemplateContext) -> None:
    """``points_attempted`` is 0 for an unlabeled shot, not a zero-point one,
    and 96% of 2022 is unlabeled: filtered on it, "Curry's threes in 2022"
    averaged 38 of his 751 attempts, every one a miss. The description names
    one of these as a three and the position names the other."""
    sc_ctx.con.executemany(
        "INSERT INTO shot_chart VALUES ('1',?,2,'e7',1,'2:00',?,'Jump Shot',?,?,0,?)",
        [(current_season(), True, 25, 25, "makes 25-foot three point jumper"), (current_season(), False, 2, 3, "misses 23-foot step back jumpshot")],
    )
    assert shot_distance(sc_ctx, {"player": "Stephen Curry", "shot_value": 3}).data["attempts"] == 4


def test_shot_distance_leaves_out_free_throws_that_carry_a_position(sc_ctx: TemplateContext) -> None:
    """From 2002 to 2018 every free throw has a position under the rim, so
    "has coordinates" does not exclude them - they averaged in as zero-foot
    shots."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e1',1,'3:00',TRUE,'Free Throw - 1 of 2',25,0,1,'makes free throw 1 of 2')", [current_season()])
    result = shot_distance(sc_ctx, {"player": "Stephen Curry"})
    assert result.data["attempts"] == 2
    assert result.data["avg_feet"] == pytest.approx(26.0)


def test_shot_distance_leaves_out_shots_with_no_position(sc_ctx: TemplateContext) -> None:
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e1',1,'4:00',TRUE,'Layup Shot',0,0,2,'makes layup')", [current_season()])
    assert shot_distance(sc_ctx, {"player": "Stephen Curry"}).data["attempts"] == 2


def test_shot_distance_refuses_threes_in_a_season_that_cannot_tell_them_apart(sc_ctx: TemplateContext) -> None:
    """2002 has no labels and descriptions that miss threes, so answering from
    what it has would be a narrower answer with nothing saying so. The refusal
    names that cause - not an absence of data, since the same season still
    answers for all of a player's shots."""
    sc_ctx.con.executemany(
        "INSERT INTO shot_chart VALUES ('1',2002,2,'e8',1,'5:00',TRUE,'Jump Shot',?,?,0,?)",
        [(25, 25, "made 25 ft Three Point Jumper."), (47, 0, "made Jumper.")],
    )
    refused = shot_distance(sc_ctx, {"player": "Stephen Curry", "season": 2002, "shot_value": 3})
    assert "avg_feet" not in refused.data
    assert (refused.answer or "").startswith(shotchart.UNSEPARABLE_SHOT_VALUES[2002])
    assert shot_distance(sc_ctx, {"player": "Stephen Curry", "season": 2002}).data["attempts"] == 2


def test_shot_distance_says_when_a_seasons_shot_values_are_derived(sc_ctx: TemplateContext) -> None:
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2022,2,'e9',1,'6:00',TRUE,'Jump Shot',25,26,0,'makes 26-foot three point jumper')")
    threes = shot_distance(sc_ctx, {"player": "Stephen Curry", "season": 2022, "shot_value": 3})
    assert threes.data["attempts"] == 1
    assert shotchart.DERIVED_SHOT_VALUES[2022] in (threes.answer or "")
    # A question about all shots does not rest on the derivation, so says nothing.
    assert "Note" not in (shot_distance(sc_ctx, {"player": "Stephen Curry", "season": 2022}).answer or "")


def test_shot_chart_draws_threes_espn_left_unlabeled(sc_ctx: TemplateContext) -> None:
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e7',1,'2:00',TRUE,'Jump Shot',2,3,0,'makes 23-foot step back jumpshot')", [current_season()])
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": current_season(), "shot_value": 3})
    assert "(2/3 made" in (result.answer or "")


def test_shot_chart_refuses_threes_in_a_season_that_cannot_tell_them_apart(sc_ctx: TemplateContext) -> None:
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2002,2,'e8',1,'5:00',TRUE,'Jump Shot',25,25,0,'made 25 ft Three Point Jumper.')")
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": 2002, "shot_value": 3})
    assert (result.answer or "").startswith(shotchart.UNSEPARABLE_SHOT_VALUES[2002])
    assert not list(sc_ctx.out_dir.glob("*.html"))


def test_shot_chart_leaves_free_throws_off_the_court(sc_ctx: TemplateContext) -> None:
    """A 2002-2018 free throw has a position under the rim, and was drawn
    there as a shot."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e1',1,'3:00',TRUE,'Free Throw - 1 of 2',25,0,1,'makes free throw 1 of 2')", [current_season()])
    assert "(1/2 made" in (shot_chart(sc_ctx, {"player": "Stephen Curry", "season": current_season()}).answer or "")


def test_a_free_throw_chart_is_refused_rather_than_drawn(sc_ctx: TemplateContext) -> None:
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": current_season(), "shot_value": 1})
    assert "no free-throw chart" in (result.answer or "")
    assert not list(sc_ctx.out_dir.glob("*.html"))


def test_a_career_chart_says_which_shots_it_left_out(sc_ctx: TemplateContext) -> None:
    """One unseparable season is refused outright. Across seasons the chart
    draws what can be told apart and says what it could not, rather than
    dropping it where nobody would know."""
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2002,2,'e8',1,'5:00',FALSE,'Jump Shot',47,0,0,'missed Jumper.')")
    rendered = shotchart.render_shot_chart(sc_ctx.con, sc_ctx.out_dir, "Stephen Curry", shot_value=3)
    assert "(1/2 made" in rendered.message
    assert "left out 1 shot that cannot be told apart as twos or threes" in rendered.message


def test_shot_distance_scopes_to_the_current_season_by_default(sc_ctx: TemplateContext) -> None:
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',2019,2,'e6',1,'1:00',TRUE,'Jump Shot',25,40,3,'40-foot three point jumper')")
    assert shot_distance(sc_ctx, {"player": "Stephen Curry", "shot_value": 3}).data["attempts"] == 2


def test_shot_distance_reads_the_shot_value_from_either_slot(sc_ctx: TemplateContext) -> None:
    by_stat = shot_distance(sc_ctx, {"player": "Stephen Curry", "stat": "threePointFieldGoalsMade"})
    assert by_stat.data["shot_value"] == 3


def test_shot_distance_declines_free_throws(sc_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        shot_distance(sc_ctx, {"player": "Stephen Curry", "shot_value": 1})


def test_shot_distance_reports_no_coordinates_honestly(sc_ctx: TemplateContext) -> None:
    assert "No " in (shot_distance(sc_ctx, {"player": "Stephen Curry", "season": 1999}).answer or "")


def test_shot_distance_without_a_player_falls_through(sc_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        shot_distance(sc_ctx, {"shot_value": 3})


# ---------------- player_history ----------------


def test_player_history_spans_several_seasons(ps_con: TemplateContext) -> None:
    """Every other template answers about one season, so a multi-season
    question had nowhere to go and was absorbed by leaderboard."""
    s = current_season()
    ps_con.con.execute("INSERT INTO player_season_stats_deduped (athlete_id, season, season_type, gamesPlayed, avgPoints) VALUES ('1',?,2,70,30.0),('1',?,2,72,28.0)", [s - 1, s - 2])
    result = player_history(ps_con, {"player": "Luka Doncic", "stat": "points", "limit": 3})
    assert [row["season"] for row in result.data["seasons"]] == [s, s - 1, s - 2]


def test_player_history_defaults_to_four_seasons(ps_con: TemplateContext) -> None:
    from association.query.templates import DEFAULT_HISTORY_SEASONS

    s = current_season()
    for offset in range(1, 8):
        ps_con.con.execute("INSERT INTO player_season_stats_deduped (athlete_id, season, season_type, gamesPlayed, avgPoints) VALUES ('1',?,2,70,20.0)", [s - offset])
    result = player_history(ps_con, {"player": "Luka Doncic", "stat": "points"})
    assert len(result.data["seasons"]) == DEFAULT_HISTORY_SEASONS


def test_player_history_reports_a_percentage_with_its_volume(ps_con: TemplateContext) -> None:
    # A percentage without makes/attempts is the thing people immediately ask
    # "out of how many?" about.
    for col, typ in [("threePointFieldGoalPct", "DOUBLE"), ("threePointFieldGoalsMade", "INTEGER"), ("threePointFieldGoalsAttempted", "INTEGER")]:
        ps_con.con.execute(f"ALTER TABLE player_season_stats_deduped ADD COLUMN {col} {typ}")
    ps_con.con.execute("UPDATE player_season_stats_deduped SET threePointFieldGoalPct=38.3, threePointFieldGoalsMade=202, threePointFieldGoalsAttempted=527 WHERE athlete_id='1'")
    answer = player_history(ps_con, {"player": "Luka Doncic", "stat": "threePointFieldGoalPct"}).answer or ""
    assert "3PT%" in answer and "3PM" in answer and "3PA" in answer
    assert "38.3" in answer and "202" in answer and "527" in answer


def test_player_history_refuses_a_stat_it_has_no_history_for(ps_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_history(ps_con, {"player": "Luka Doncic", "stat": "shot_distance"})
    with pytest.raises(TemplateUnsupported):
        player_history(ps_con, {"player": "Luka Doncic"})


def test_player_history_asks_on_an_ambiguous_player(ps_con: TemplateContext) -> None:
    assert "did you mean" in (player_history(ps_con, {"player": "Curry", "stat": "points"}).answer or "")


def test_leaderboard_refuses_when_a_player_is_named(lb_con: TemplateContext) -> None:
    """A leaderboard ranks the league or a team, never one named person.
    Confirmed live: it answered a question about Klay Thompson with the
    league's true-shooting leaders, Klay silently dropped."""
    with pytest.raises(TemplateUnsupported):
        leaderboard(lb_con, {"stat": "points", "player": "Klay Thompson"})


# ---------------- player_netpoints ----------------


@pytest.fixture
def np_ctx(tmp_path: Path) -> TemplateContext:
    from association.net_points_categories import FINGERPRINT_CATEGORIES

    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Shai Gilgeous-Alexander')")
    c.execute(
        "CREATE TABLE net_points_player (athlete_id VARCHAR, season INTEGER, net_points_season_type VARCHAR, "
        "overall DOUBLE, offense DOUBLE, defense DOUBLE, overall_per_100_poss DOUBLE, total_minutes DOUBLE, games INTEGER)"
    )
    c.execute("INSERT INTO net_points_player VALUES ('1',?,'Regular Season',468.33,403.9,64.43,9.91,2259,68)", [current_season()])
    cats = list(FINGERPRINT_CATEGORIES.values())
    cols = ", ".join(f"{cat}_{side}_net_pts DOUBLE" for cat in cats for side in ("o", "d", "t"))
    c.execute(f"CREATE TABLE net_points_player_fingerprint (athlete_id VARCHAR, season INTEGER, total_poss DOUBLE, {cols})")
    values = ", ".join("1.0" for _ in cats for _ in range(3))
    c.execute(f"INSERT INTO net_points_player_fingerprint VALUES ('1', {current_season()}, 4725, {values})")
    c.execute("UPDATE net_points_player_fingerprint SET two_pt_t_net_pts = 250.9, two_pt_o_net_pts = 251.3")
    return TemplateContext(con=c, out_dir=tmp_path)


def test_player_netpoints_answers_about_the_named_player(np_ctx: TemplateContext) -> None:
    """Confirmed live: this fell through and the agent answered "Nikola Jokic
    leads the team in NetPoints", with SGA dropped entirely."""
    answer = player_netpoints(np_ctx, {"player": "Shai Gilgeous-Alexander"}).answer or ""
    assert answer.startswith("Shai Gilgeous-Alexander")
    assert "468.3" in answer and "403.9" in answer and "64.4" in answer


def test_player_netpoints_includes_the_play_type_fingerprint(np_ctx: TemplateContext) -> None:
    result = player_netpoints(np_ctx, {"player": "SGA"})
    categories = {row["category"] for row in result.data["fingerprint"]}
    assert "two pt" in categories and "driving" in categories
    assert "total" not in categories  # the summary, reported on the headline


def test_player_netpoints_orders_the_fingerprint_by_magnitude(np_ctx: TemplateContext) -> None:
    rows = player_netpoints(np_ctx, {"player": "SGA"}).data["fingerprint"]
    assert rows[0]["category"] == "two pt"


def test_player_netpoints_uses_the_string_season_type(np_ctx: TemplateContext) -> None:
    """net_points_player has its OWN string season_type; filtering it with the
    numeric one every other table uses silently matches nothing."""
    assert player_netpoints(np_ctx, {"player": "SGA"}).data["headline"] is not None


def test_player_netpoints_reports_a_missing_season_honestly(np_ctx: TemplateContext) -> None:
    assert "no 1999 regular season NetPoints" in (player_netpoints(np_ctx, {"player": "SGA", "season": 1999}).answer or "")


def test_player_netpoints_without_a_player_falls_through(np_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_netpoints(np_ctx, {})


def test_player_netpoints_fingerprint_defaults_to_per_100_possessions(np_ctx: TemplateContext) -> None:
    """Season totals mostly rank by playing time; per 100 possessions is the
    unit that compares players, which is what the fingerprint is for."""
    result = player_netpoints(np_ctx, {"player": "SGA"})
    two_pt = next(r for r in result.data["fingerprint"] if r["category"] == "two pt")
    assert two_pt["total"] == pytest.approx(250.9 / 4725 * 100, rel=1e-3)
    assert two_pt["total_season_total"] == 250.9  # the raw total is still available
    assert "per 100 possessions" in (result.answer or "")


def test_player_netpoints_rate_total_reports_season_totals(np_ctx: TemplateContext) -> None:
    result = player_netpoints(np_ctx, {"player": "SGA", "rate": "total"})
    two_pt = next(r for r in result.data["fingerprint"] if r["category"] == "two pt")
    assert two_pt["total"] == 250.9
    assert "season totals" in (result.answer or "")


def test_player_netpoints_falls_back_to_totals_without_a_possession_count(np_ctx: TemplateContext) -> None:
    # No possession count: report totals and say so, rather than dividing by
    # nothing or showing an unlabeled unit.
    np_ctx.con.execute("UPDATE net_points_player_fingerprint SET total_poss = NULL")
    result = player_netpoints(np_ctx, {"player": "SGA"})
    assert next(r for r in result.data["fingerprint"] if r["category"] == "two pt")["total"] == 250.9
    assert "season totals" in (result.answer or "")


def test_player_netpoints_gives_defense_its_own_section(np_ctx: TemplateContext) -> None:
    """A single table sorted by total renders the defensive profile invisible:
    for SGA, `turnover` carries the largest defensive value of any play type
    and lands 15th of 21 by total, below categories whose defense is ~0."""
    np_ctx.con.execute("UPDATE net_points_player_fingerprint SET turnover_d_net_pts = 170.9, turnover_t_net_pts = 15.7")
    answer = player_netpoints(np_ctx, {"player": "SGA"}).answer or ""
    assert "Offense," in answer and "Defense," in answer
    defense_section = answer.split("Defense,")[1]
    assert defense_section.strip().splitlines()[1].strip().startswith("turnover")


def test_player_netpoints_sorts_each_section_by_its_own_side(np_ctx: TemplateContext) -> None:
    np_ctx.con.execute("UPDATE net_points_player_fingerprint SET turnover_d_net_pts = 170.9, turnover_o_net_pts = 0.5")
    answer = player_netpoints(np_ctx, {"player": "SGA"}).answer or ""
    offense_section = answer.split("Offense,")[1].split("Defense,")[0]
    assert not offense_section.strip().splitlines()[1].strip().startswith("turnover")


def test_player_netpoints_warns_that_the_detail_slices_overlap(np_ctx: TemplateContext) -> None:
    # A driving layup at the rim counts in driving, layup AND rim, so the
    # detail rows are not additive - unlike the six partition categories.
    assert "do not add up" in (player_netpoints(np_ctx, {"player": "SGA"}).answer or "")


def test_the_six_core_categories_partition_the_total(np_ctx: TemplateContext) -> None:
    """two_pt, three_pt, free_throw, turnover, rebound and foul sum exactly to
    the offensive and defensive totals - verified against the separately stored
    net_points_player.offense/.defense for every top-minutes player in 2026,
    max deviation 0.005. The other categories are overlapping slices."""
    from association.query.templates import FINGERPRINT_PARTITION

    assert set(FINGERPRINT_PARTITION) == {"two_pt", "three_pt", "free_throw", "turnover", "rebound", "foul"}
    answer = player_netpoints(np_ctx, {"player": "SGA", "rate": "total"}).answer or ""
    offense = answer.split("Offense,")[1].split("Defense,")[0]
    listed = [ln.split()[-1] for ln in offense.strip().splitlines()[1:] if ln.strip() and not ln.strip().startswith("-")]
    assert pytest.approx(sum(float(v) for v in listed[:-1]), rel=1e-6) == float(listed[-1])


def test_overlapping_play_types_are_kept_out_of_the_summing_column(np_ctx: TemplateContext) -> None:
    answer = player_netpoints(np_ctx, {"player": "SGA"}).answer or ""
    offense = answer.split("Offense,")[1].split("Defense,")[0]
    assert "rim" not in offense and "layup" not in offense  # detail, not partition
    assert "do not add up" in answer and "rim" in answer.split("Play-type detail")[1]


def test_shot_chart_scopes_to_a_single_game_when_order_is_set(sc_ctx: TemplateContext) -> None:
    """Confirmed live: "a shot chart of Curry's last regular season game"
    charted the whole season - 803 attempts instead of that game's 14."""
    sc_ctx.con.execute("CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, game_date VARCHAR)")
    sc_ctx.con.execute(
        "INSERT INTO player_game_log VALUES ('1',?,2,'e1','2026-01-01T00:00Z'),('1',?,2,'eLast','2026-04-13T00:30Z')",
        [current_season(), current_season()],
    )
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'eLast',1,'2:00',TRUE,'Jump Shot',25,26,3,'26-foot three point jumper')", [current_season()])
    answer = shot_chart(sc_ctx, {"player": "Stephen Curry", "order": "recent"}).answer or ""
    assert "1/1 made" in answer  # only the one shot from the last game
    assert "eLast" in answer


def test_shot_chart_order_first_picks_the_earliest_game(sc_ctx: TemplateContext) -> None:
    sc_ctx.con.execute("CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, game_date VARCHAR)")
    sc_ctx.con.execute(
        "INSERT INTO player_game_log VALUES ('1',?,2,'e1','2026-01-01T00:00Z'),('1',?,2,'eLast','2026-04-13T00:30Z')",
        [current_season(), current_season()],
    )
    assert "e1" in (shot_chart(sc_ctx, {"player": "Stephen Curry", "order": "first"}).answer or "")


def test_shot_chart_without_order_still_covers_the_season(sc_ctx: TemplateContext) -> None:
    # Both of the fixture's shots (one made, one missed), not one game's worth.
    assert "1/2 made" in (shot_chart(sc_ctx, {"player": "Stephen Curry"}).answer or "")


def test_threshold_count_supports_fouls(con: TemplateContext) -> None:
    con.con.execute("ALTER TABLE player_box_stats ADD COLUMN fouls INTEGER")
    con.con.execute("UPDATE player_box_stats SET fouls = 6 WHERE athlete_id = '1'")
    result = threshold_count(con, {"stat": "fouls", "threshold": 6})
    assert "6+ fouls" in (result.answer or "")
    assert result.data["leaders"][0]["player"] == "Luka Doncic"


def _add_per_game_netpoints(ctx: TemplateContext) -> None:
    ctx.con.execute("CREATE TABLE games (event_id VARCHAR, date VARCHAR)")
    ctx.con.execute("INSERT INTO games VALUES ('eFirst','2025-10-22T00:00Z'),('eLast','2026-04-13T00:30Z')")
    ctx.con.execute(
        "CREATE TABLE net_points_player_game (event_id VARCHAR, season INTEGER, season_type INTEGER, athlete_id VARCHAR, "
        "o_net_pts DOUBLE, d_net_pts DOUBLE, t_net_pts DOUBLE, o_usage DOUBLE, d_usage DOUBLE, "
        "o_poss DOUBLE, d_poss DOUBLE, t_poss DOUBLE, o_wpa DOUBLE, d_wpa DOUBLE, t_wpa DOUBLE)"
    )
    ctx.con.execute(
        "INSERT INTO net_points_player_game VALUES ('eFirst',?,2,'1',0.8,1.4,2.2,0.2,0.2,16,15,31,0.1,0.1,0.215),('eLast',?,2,'1',2.4,3.9,6.3,0.2,0.2,17,11,28,0.1,0.1,0.190)",
        [current_season(), current_season()],
    )


def test_player_netpoints_scopes_to_one_game_when_order_is_set(np_ctx: TemplateContext) -> None:
    """Confirmed live: "netpoints from his last regular season game" returned
    the whole season - 43 games - because nothing scoped it."""
    _add_per_game_netpoints(np_ctx)
    result = player_netpoints(np_ctx, {"player": "SGA", "order": "recent"})
    # eLast tips at 2026-04-13T00:30Z - 8:30pm Eastern on the 12th, the day it
    # was played. The UTC day this used to assert was the bug.
    assert result.data["game"]["date"] == "2026-04-12"
    answer = result.answer or ""
    assert "6.3 total" in answer and "most recent" in answer
    assert "Offense," not in answer  # not the season breakdown


def test_player_netpoints_order_first_picks_the_earliest_game(np_ctx: TemplateContext) -> None:
    _add_per_game_netpoints(np_ctx)
    # 2025-10-22T00:00Z is 8pm Eastern on October 21st.
    assert player_netpoints(np_ctx, {"player": "SGA", "order": "first"}).data["game"]["date"] == "2025-10-21"


def test_single_game_netpoints_points_at_the_fingerprint_for_the_split(np_ctx: TemplateContext) -> None:
    """This template answers one game's NetPoints TOTAL and has no play-type
    split of its own. Saying nothing would read as the split not existing -
    which is what it used to say, back when it did not."""
    _add_per_game_netpoints(np_ctx)
    assert "Ask for a fingerprint of that game" in (player_netpoints(np_ctx, {"player": "SGA", "order": "recent"}).answer or "")


def test_player_netpoints_without_order_still_gives_the_season(np_ctx: TemplateContext) -> None:
    _add_per_game_netpoints(np_ctx)
    assert "Offense," in (player_netpoints(np_ctx, {"player": "SGA"}).answer or "")


def test_single_game_netpoints_falls_through_without_the_optin_table(np_ctx: TemplateContext) -> None:
    # net_points_player_game only exists if fetched with
    # --include-net-points-daily; say so rather than answering for the season.
    with pytest.raises(TemplateUnsupported):
        player_netpoints(np_ctx, {"player": "SGA", "order": "recent"})


# ---------------- scoping guard ----------------


def test_scope_guard_blocks_a_template_that_would_ignore_a_game_scope() -> None:
    """Three live failures were slots the router extracted CORRECTLY and the
    template silently dropped: a chart of "his last game" drew the whole season,
    NetPoints for "his last game" reported all 43, and a record "over their last
    10" would have covered the full season. The routing check cannot catch
    those - routing was right every time."""
    for intent, slots in [
        ("threshold_count", {"date": "2026-04-12"}),
        ("team_record", {"order": "recent"}),
        ("leaderboard", {"order": "first"}),
        ("player_stat", {"date": "2026-04-12"}),
    ]:
        with pytest.raises(TemplateUnsupported, match="different span"):
            check_scope(intent, slots)


def test_scope_guard_allows_templates_that_honor_the_slot() -> None:
    check_scope("game_log", {"order": "recent", "date": "2026-04-12"})
    check_scope("shot_chart", {"order": "recent"})
    check_scope("shot_distance", {"order": "first"})
    check_scope("player_netpoints", {"order": "recent"})


def test_scope_guard_ignores_absent_or_empty_slots() -> None:
    check_scope("leaderboard", {})
    check_scope("leaderboard", {"order": None, "date": ""})


def test_every_template_honoring_a_scope_slot_actually_reads_it() -> None:
    # Guards against the list drifting from the code it describes.
    import inspect

    from association.query import templates as module

    for intent, honored in HONORED_SCOPING.items():
        source = inspect.getsource(module.TEMPLATES[intent])
        for slot in honored:
            assert f'"{slot}"' in source, f"{intent} claims to honor {slot} but never reads it"


def test_shot_distance_scopes_to_one_game(sc_ctx: TemplateContext) -> None:
    sc_ctx.con.execute("CREATE TABLE player_game_log (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, game_date VARCHAR)")
    sc_ctx.con.execute("INSERT INTO player_game_log VALUES ('1',?,2,'e1','2026-04-13T00:30Z')", [current_season()])
    # A second game whose shots must NOT be counted.
    sc_ctx.con.execute("INSERT INTO player_game_log VALUES ('1',?,2,'e2','2026-01-01T00:00Z')", [current_season()])
    sc_ctx.con.execute("INSERT INTO shot_chart VALUES ('1',?,2,'e2',1,'2:00',TRUE,'Jump Shot',25,40,3,'40-foot three point jumper')", [current_season()])
    answer = shot_distance(sc_ctx, {"player": "Stephen Curry", "order": "recent"}).answer or ""
    # e1 tips at 2026-04-13T00:30Z - 8:30pm Eastern on the 12th, the day it was
    # played. The UTC day this used to assert was the bug.
    assert "most recent game (2026-04-12)" in answer
    assert "2 attempts" in answer  # the fixture's two shots in e1, not the third in e2


# ---------------- fingerprint ----------------


@pytest.fixture
def fp_ctx(tmp_path: Path) -> TemplateContext:
    from association.net_points_categories import FINGERPRINT_CATEGORIES

    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Shai Gilgeous-Alexander'),('2','Bench Guy')")
    # `total` included: it is not a spoke, but it is the headline above the plot.
    categories = list(FINGERPRINT_CATEGORIES.values())
    columns = ", ".join(f"{category}_{side}_net_pts DOUBLE" for category in categories for side in ("o", "d", "t"))
    c.execute(f"CREATE TABLE net_points_player_fingerprint (athlete_id VARCHAR, season INTEGER, minutes DOUBLE, total_poss DOUBLE, {columns})")
    values = ", ".join(["1.0"] * (3 * len(categories)))
    for athlete_id, minutes in (("1", 2000.0), ("2", 1200.0)):
        c.execute(f"INSERT INTO net_points_player_fingerprint VALUES ('{athlete_id}', {current_season()}, {minutes}, 4000.0, {values})")

    # The per-game half: long, not wide, and needing games (for the date a
    # game is picked by) and net_points_player_game (for the possessions the
    # percentile pool is floored on).
    c.execute("CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR)")
    c.execute("CREATE TABLE net_points_player_game (event_id VARCHAR, athlete_id VARCHAR, season INTEGER, season_type INTEGER, t_poss DOUBLE)")
    game_fingerprint_columns = "event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, athlete_id VARCHAR, category VARCHAR"
    c.execute(f"CREATE TABLE net_points_player_game_fingerprint ({game_fingerprint_columns}, o_net_pts DOUBLE, d_net_pts DOUBLE, t_net_pts DOUBLE)")
    for event_id, date in (("g1", "2026-01-05"), ("g2", "2026-03-20")):
        c.execute("INSERT INTO games VALUES (?, ?, 2, ?)", [event_id, current_season(), date])
        for athlete_id in ("1", "2"):
            c.execute("INSERT INTO net_points_player_game VALUES (?, ?, ?, 2, 60.0)", [event_id, athlete_id, current_season()])
            for category in categories:
                # g2 is the bigger game, so "most recent" and "first" differ in
                # the numbers as well as in the date.
                size = 2.0 if event_id == "g2" else 1.0
                c.execute(
                    "INSERT INTO net_points_player_game_fingerprint VALUES (?, ?, 2, '9', ?, ?, ?, ?, ?)",
                    [event_id, current_season(), athlete_id, category, size, size, size * 2],
                )
    return TemplateContext(con=c, out_dir=tmp_path)


def test_fingerprint_writes_a_file_and_reports_its_path(fp_ctx: TemplateContext) -> None:
    result = fingerprint(fp_ctx, {"player": "Shai Gilgeous-Alexander"})
    assert "Rendered NetPoints fingerprint (total) for Shai Gilgeous-Alexander" in result.answer
    assert list(fp_ctx.out_dir.glob("*.html"))


def test_fingerprint_honors_the_side_slot(fp_ctx: TemplateContext) -> None:
    assert fingerprint(fp_ctx, {"player": "Shai", "side": "defense"}).data["side"] == "defense"


def test_fingerprint_ignores_a_nonsense_side(fp_ctx: TemplateContext) -> None:
    assert fingerprint(fp_ctx, {"player": "Shai", "side": "sideways"}).data["side"] == "total"


def test_fingerprint_defaults_to_the_current_season(fp_ctx: TemplateContext) -> None:
    assert fingerprint(fp_ctx, {"player": "Shai"}).data["season"] == current_season()


def test_fingerprint_draws_the_game_that_was_asked_for(fp_ctx: TemplateContext) -> None:
    """The whole point of the scoping: "his last game" draws that game, and the
    plot is titled with its date rather than with the season - so a reader can
    tell which game they are looking at."""
    result = fingerprint(fp_ctx, {"player": "Shai", "order": "recent"})

    assert result.data["scope"] == "game"
    assert "2026-03-20" in result.answer
    assert list(fp_ctx.out_dir.glob("*_recent_game_*.html"))


def test_the_other_end_of_the_season_draws_a_different_game(fp_ctx: TemplateContext) -> None:
    result = fingerprint(fp_ctx, {"player": "Shai", "order": "first"})

    assert "2026-01-05" in result.answer


def test_a_game_plot_is_not_captioned_as_a_per_100_rate(fp_ctx: TemplateContext) -> None:
    """A single game's numbers are that game's net points - a per-100 rate over
    ~30 possessions turns one made three into a league-leading season figure.
    The caption has to say which quantity is on the page, or the plot is the
    right shape under the wrong claim."""
    fingerprint(fp_ctx, {"player": "Shai", "order": "recent", "scale": "value"})
    page = next(fp_ctx.out_dir.glob("*_recent_game_*.html")).read_text()

    assert "per 100 poss" not in page
    assert "in this game" in page


def test_a_season_plot_still_says_per_100(fp_ctx: TemplateContext) -> None:
    """The other half of the check above: a unit label that stopped appearing
    anywhere would pass it."""
    fingerprint(fp_ctx, {"player": "Shai"})
    page = next(p for p in fp_ctx.out_dir.glob("*.html") if "_game_" not in p.name).read_text()

    assert "per 100 poss" in page


def test_a_fingerprint_for_a_particular_date_still_says_it_cannot(fp_ctx: TemplateContext) -> None:
    """`date` is a different question from `order`: the router gives a calendar
    date and the loader picks a player's first or last game. Answering one with
    the other is exactly the substitution this template exists to refuse."""
    answer = fingerprint(fp_ctx, {"player": "Shai", "date": "2026-01-02"}).answer

    assert "not yet for a particular date" in answer
    assert not list(fp_ctx.out_dir.glob("*.html"))


def test_fingerprint_declares_the_game_scoping_it_handles(fp_ctx: TemplateContext) -> None:
    # It handles them by refusing; check_scope must therefore NOT strip the
    # request out from under it and fall through to an agent with no better source.
    check_scope("fingerprint", {"player": "Shai", "order": "recent", "date": "2026-01-02"})
    # The game-scoping pair specifically - SCOPING_SLOTS also holds opponent,
    # venue, span and without, none of which a fingerprint can narrow to.
    assert {"order", "date"} <= HONORED_SCOPING["fingerprint"] <= SCOPING_SLOTS


def test_fingerprint_without_a_player_falls_through(fp_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        fingerprint(fp_ctx, {})


def test_fingerprint_reports_an_unknown_player_rather_than_falling_through(fp_ctx: TemplateContext) -> None:
    assert "No player found" in fingerprint(fp_ctx, {"player": "Nobody At All"}).answer


def test_fingerprint_reports_a_season_with_no_data_rather_than_falling_through(fp_ctx: TemplateContext) -> None:
    # The agent has no better source than the table this just read.
    assert "no NetPoints fingerprint data for season 1999" in fingerprint(fp_ctx, {"player": "Shai", "season": 1999}).answer


def test_fingerprint_plots_two_players_on_one_radar(fp_ctx: TemplateContext) -> None:
    """Two polygons on shared axes IS the comparison, so "compare their
    fingerprints" needs no second intent - only the `players` slot."""
    result = fingerprint(fp_ctx, {"players": ["Shai Gilgeous-Alexander", "Bench Guy"]})
    assert result.data["players"] == ["Shai Gilgeous-Alexander", "Bench Guy"]
    assert "Shai Gilgeous-Alexander vs Bench Guy" in result.answer


def test_fingerprint_does_not_compare_a_player_with_himself(fp_ctx: TemplateContext) -> None:
    # Two spellings of one name drew one polygon over itself and called it a
    # comparison.
    assert fingerprint(fp_ctx, {"players": ["Shai Gilgeous-Alexander", "Shai"]}).data["players"] == ["Shai Gilgeous-Alexander"]


def test_a_chart_template_reports_the_file_it_wrote_as_an_artifact(sc_ctx: TemplateContext) -> None:
    """The path used to exist only inside the message, so showing the chart
    meant parsing a sentence. It is a value now, and this is what says so."""
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": current_season()})
    assert [a.kind for a in result.artifacts] == ["shot_chart"]
    assert result.artifacts[0].path.exists()
    assert result.artifacts[0].name.endswith(".html")
    assert result.data["path"] == str(result.artifacts[0].path)


def test_a_chart_template_that_drew_nothing_reports_no_artifact(sc_ctx: TemplateContext) -> None:
    """ "No shots found" is a real answer, not a failure - but there is no file,
    and claiming one would give a caller a path that does not exist."""
    result = shot_chart(sc_ctx, {"player": "Stephen Curry", "season": 1999})
    assert result.artifacts == []
    assert result.data["path"] is None


def test_the_fingerprint_template_reports_the_file_it_wrote_as_an_artifact(fp_ctx: TemplateContext) -> None:
    result = fingerprint(fp_ctx, {"player": "Shai Gilgeous-Alexander"})
    assert [a.kind for a in result.artifacts] == ["fingerprint"]
    assert result.artifacts[0].path.exists()


def test_templates_that_write_nothing_report_no_artifacts(lb_con: TemplateContext) -> None:
    """The default has to be empty, not unset: a caller iterates artifacts on
    every answer, and a None here would be an AttributeError on the common path."""
    assert leaderboard(lb_con, {"stat": "points"}).artifacts == []


@pytest.mark.parametrize(
    ("intent", "slots"),
    [
        # game_log answers the real queries behind these now ("jaylen brown last
        # 8 games vs pistons", "Podziemski game log without curry"), so the
        # refusal is checked on templates that still cannot narrow that way.
        ("shot_distance", {"player": "Jaylen Brown", "opponent": "Detroit Pistons"}),
        ("player_netpoints", {"player": "Brandin Podziemski", "without": "curry"}),
        ("player_history", {"player": "Nikola Jokic", "stat": "points", "opponent": "Boston Celtics"}),
    ],
)
def test_scope_guard_refuses_what_the_question_text_narrowed_to(intent: str, slots: dict[str, Any]) -> None:
    """The real StatMuse queries behind these slots were each answered for
    every opponent, every venue, one season, or every game respectively."""
    with pytest.raises(TemplateUnsupported, match="different span"):
        check_scope(intent, slots)


def test_scope_guard_lets_through_what_the_player_templates_now_honor() -> None:
    check_scope("game_log", {"player": "Jaylen Brown", "opponent": "Detroit Pistons", "venue": "home", "span": "career", "without": "x", "order": "recent"})
    check_scope("player_stat", {"player": "Evan Mobley", "opponent": "Milwaukee Bucks", "venue": "away", "span": "career", "without": "x"})
    check_scope("player_history", {"player": "Nikola Jokic", "span": "career"})


def test_team_quarter_points_still_honours_the_opponent_it_always_read() -> None:
    check_scope("team_quarter_points", {"team": "Philadelphia 76ers", "period": 4, "opponent": "Boston Celtics"})


def test_no_template_narrows_to_a_playoff_round() -> None:
    """Nothing in the warehouse records a round or a series game number."""
    assert not any("round" in honored for honored in HONORED_SCOPING.values())
    with pytest.raises(TemplateUnsupported, match="different span"):
        check_scope("player_stat", {"player": "Jayson Tatum", "round": "finals"})


def test_a_zero_threshold_is_refused_rather_than_counting_every_game(con: TemplateContext) -> None:
    """Measured: "most 3 pointers made since 2020" arrived as threshold 0."""
    with pytest.raises(TemplateUnsupported, match="counts every game"):
        threshold_count(con, {"stat": "points", "threshold": 0})


@pytest.mark.parametrize(("intent", "slots"), [("player_stat", {"player": "Joe Ingles", "split": "starter_bench"}), ("leaderboard", {"stat": "points", "since": 2020})])
def test_a_split_or_a_range_is_refused_where_nothing_honours_it(intent: str, slots: dict[str, Any]) -> None:
    with pytest.raises(TemplateUnsupported, match="different span"):
        check_scope(intent, slots)


# ---------------- one player's games: game_log and player_stat, narrowed ----------------

_BOX_COLUMNS = (
    "event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, athlete_id VARCHAR, did_not_play BOOLEAN, "
    "minutes INTEGER, points INTEGER, rebounds INTEGER, assists INTEGER, steals INTEGER, blocks INTEGER, turnovers INTEGER, fouls INTEGER, plusMinus INTEGER, "
    "fieldGoalsMade INTEGER, fieldGoalsAttempted INTEGER, threePointFieldGoalsMade INTEGER, threePointFieldGoalsAttempted INTEGER, "
    "freeThrowsMade INTEGER, freeThrowsAttempted INTEGER, offensiveRebounds INTEGER, defensiveRebounds INTEGER"
)


def _box(
    event: str, season: int, team: str, opponent: str, athlete: str, *, dnp: bool = False, minutes: int | None = 30, pts: int = 0, reb: int = 0, ast: int = 0, ftm: int = 0, fta: int = 0
) -> tuple[Any, ...]:
    """One player_box_stats row. ``minutes=None`` is the empty line ESPN leaves
    (listed as played, no minutes, every stat zero); ``dnp`` is a did-not-play
    entry, whose stats are NULL."""
    if dnp:
        return (event, season, 2, team, opponent, athlete, True, *([None] * 17))
    return (event, season, 2, team, opponent, athlete, False, minutes, pts, reb, ast, 1, 0, 2, 3, 0, pts // 2, pts, 0, 0, ftm, fta, 0, 0)


@pytest.fixture
def pg_ctx(tmp_path: Path) -> TemplateContext:
    """A Warriors season in miniature, built to exercise every narrowing. ``s``
    is the current season.

    ====  =====  ==============  ====================  =============
    game  season  matchup         tip (UTC)             Eastern date
    ====  =====  ==============  ====================  =============
    e1    s      BOS at GS       {s-1}-11-02T00:30Z    {s-1}-11-01
    e2    s      GS at DET       {s-1}-12-02T00:30Z    {s-1}-12-01
    e3    s      DET at GS       {s}-01-10T20:00Z      {s}-01-10
    e4    s      GS at LAL       {s}-02-10T03:00Z      {s}-02-09
    e6    s      BOS at GS       {s}-03-01T00:30Z      {s}-02-28
    e5    s-1    DET at GS       {s-1}-03-01T00:30Z    {s-1}-02-28
    e7    s-1    DET at BOS      {s-1}-02-01T00:30Z
    e8    1995   CHI at DET, and its phantom copy under 1993
    ====  =====  ==============  ====================  =============

    Podziemski plays e1, e2, e3 and e5, is DNP in e4 and has an empty line in
    e6. Stephen Curry plays e1 and e5, is DNP in e3, and has no row in e2 or e4.
    Seth Curry was a Celtic last season (e7) and joins the Warriors at e3.
    Kuminga ended last season a Warrior (e5) and first appears this one at e3.
    """
    s = current_season()
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute(
        "INSERT INTO players VALUES ('10','Brandin Podziemski'),('11','Stephen Curry'),('12','Seth Curry'),('13','Jaylen Brown'),('14','Dell Curry'),('15','Jonathan Kuminga'),('20','Michael Jordan')"
    )
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('1','GS','Golden State Warriors'),('2','BOS','Boston Celtics'),('3','DET','Detroit Pistons'),('4','LAL','Los Angeles Lakers'),('5','CHI','Chicago Bulls')")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)"
    )
    c.executemany(
        "INSERT INTO games VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?)",
        [
            ("e1", s, f"{s - 1}-11-02T00:30Z", "1", "2", 110, 100, "1"),
            ("e2", s, f"{s - 1}-12-02T00:30Z", "3", "1", 99, 105, "1"),
            ("e3", s, f"{s}-01-10T20:00Z", "1", "3", 101, 120, "3"),
            ("e4", s, f"{s}-02-10T03:00Z", "4", "1", 100, 90, "4"),
            ("e6", s, f"{s}-03-01T00:30Z", "1", "2", 100, 95, "1"),
            ("e5", s - 1, f"{s - 1}-03-01T00:30Z", "1", "3", 100, 90, "1"),
            ("e7", s - 1, f"{s - 1}-02-01T00:30Z", "2", "3", 100, 90, "2"),
            ("e8", 1995, "1995-03-20T00:30Z", "3", "5", 90, 100, "5"),
            ("e8", 1993, "1995-03-20T00:30Z", "3", "5", 90, 100, "5"),
        ],
    )
    c.execute(f"CREATE TABLE player_box_stats ({_BOX_COLUMNS})")
    c.executemany(
        f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(24))})",
        [
            _box("e1", s, "1", "2", "10", pts=10, reb=5, ast=3, ftm=2, fta=3),
            _box("e2", s, "1", "3", "10", minutes=32, pts=20, reb=7, ast=5, ftm=4, fta=4),
            _box("e3", s, "1", "3", "10", minutes=28, pts=15, reb=4, ast=6, ftm=1, fta=2),
            _box("e4", s, "1", "4", "10", dnp=True),
            _box("e6", s, "1", "2", "10", minutes=None),
            _box("e5", s - 1, "1", "3", "10", minutes=20, pts=8, reb=2, ast=1),
            _box("e1", s, "1", "2", "11", pts=30),
            _box("e3", s, "1", "3", "11", dnp=True),
            _box("e6", s, "1", "2", "11", minutes=None),
            _box("e5", s - 1, "1", "3", "11", pts=25),
            _box("e7", s - 1, "2", "3", "12", pts=12),
            _box("e3", s, "1", "3", "12", pts=5),
            _box("e4", s, "1", "4", "12", pts=7),
            _box("e5", s - 1, "1", "3", "15", pts=10),
            _box("e3", s, "1", "3", "15", pts=9),
            _box("e1", s, "2", "1", "13", pts=25),
            _box("e7", s - 1, "2", "3", "13", pts=30),
            _box("e8", 1995, "5", "3", "20", minutes=40, pts=40),
            _box("e8", 1993, "5", "3", "20", minutes=40, pts=40),
        ],
    )
    # The warehouse's own view, joins and all - keyed on season as well as
    # event_id, so the phantom 1993 row joins its own games row only.
    c.execute(
        "CREATE VIEW player_game_log AS SELECT pbs.*, p.display_name AS player_name, g.date AS game_date, t.abbreviation AS team_abbr, o.abbreviation AS opponent_abbr "
        "FROM player_box_stats pbs LEFT JOIN players p ON p.athlete_id = pbs.athlete_id LEFT JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season "
        "LEFT JOIN teams t ON t.team_id = pbs.team_id LEFT JOIN teams o ON o.team_id = pbs.opponent_team_id"
    )
    c.execute(
        "CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER, avgPoints DOUBLE, points INTEGER, "
        "avgRebounds DOUBLE, totalRebounds INTEGER, avgAssists DOUBLE, assists INTEGER, avgMinutes DOUBLE, threePointFieldGoalsMade INTEGER, threePointFieldGoalsAttempted INTEGER)"
    )
    c.executemany(
        "INSERT INTO player_season_stats_deduped VALUES (?, ?, 2, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("10", s, 70, 14.0, 980, 5.0, 350, 4.0, 280, 30.0, 100, 280),
            ("10", s - 1, 30, 8.0, 240, 3.0, 90, 2.0, 60, 20.0, 20, 60),
            ("20", 1990, 82, 33.6, 2753, 6.9, 565, 6.3, 519, 39.0, 92, 245),
        ],
    )
    return TemplateContext(con=c, out_dir=tmp_path)


def test_game_log_lists_only_the_games_against_the_named_opponent(pg_ctx: TemplateContext) -> None:
    """Before the contract commit, "jaylen brown last 8 games vs pistons" listed
    the Celtics' last eight games against anybody."""
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons"})
    assert [g["opponent"] for g in result.data["games"]] == ["DET", "DET"]
    assert result.answer.startswith(f"Brandin Podziemski vs the Detroit Pistons, last 2 games of the {current_season()} regular season:")


def test_a_career_log_against_an_opponent_crosses_seasons(pg_ctx: TemplateContext) -> None:
    s = current_season()
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons", "span": "career", "limit": 8})
    assert [g["season"] for g in result.data["games"]] == [s, s, s - 1]
    assert f"last 3 games of his career ({s - 1}-{s} regular seasons):" in result.answer
    assert "Only 3 games vs the Detroit Pistons in his box scores." in result.answer


def test_a_short_season_log_says_how_many_there_were_and_where_the_rest_are(pg_ctx: TemplateContext) -> None:
    answer = game_log(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons", "limit": 8}).answer
    assert f"Only 2 games vs the Detroit Pistons in the {current_season()} regular season - ask about his career to reach earlier seasons." in answer


def test_game_log_honors_venue(pg_ctx: TemplateContext) -> None:
    home = game_log(pg_ctx, {"player": "Brandin Podziemski", "venue": "home"}).data["games"]
    away = game_log(pg_ctx, {"player": "Brandin Podziemski", "venue": "away"}).data["games"]
    assert [g["home_away"] for g in home] == ["home", "home"]
    assert [g["opponent"] for g in away] == ["DET"]


def test_game_log_lists_only_games_he_played_and_says_what_it_left_out(pg_ctx: TemplateContext) -> None:
    """The DNP in e4 is not a game he played; the empty line in e6 is not a
    game anybody can average. The empty line is counted in the answer, since in
    2013-2018 about one team-game in eight looks like it."""
    result = game_log(pg_ctx, {"player": "Brandin Podziemski"})
    assert len(result.data["games"]) == 3
    assert "Not counted: 1 game in this span whose box score lists him with no minutes and no stats." in result.answer


def test_game_log_averages_exactly_the_games_it_lists(pg_ctx: TemplateContext) -> None:
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "limit": 2})
    assert [g["points"] for g in result.data["games"]] == [15, 20]
    assert result.data["averages"]["points"] == pytest.approx(17.5)
    average_row = next(line for line in result.answer.splitlines() if line.strip().startswith("per game"))
    assert "17.5" in average_row


def test_a_named_stat_adds_its_columns_to_the_log(pg_ctx: TemplateContext) -> None:
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "stat": "freeThrowPct"})
    titles = result.answer.splitlines()[1].split()
    assert titles[-3:] == ["FTM", "FTA", "FT%"]
    # Over the listed games: 2 of 3, 4 of 4, 1 of 2 - a total, not a mean of percentages.
    assert result.data["averages"]["freeThrowPct"] == pytest.approx(100 * 7 / 9)


def test_a_real_stat_the_log_cannot_show_is_refused_rather_than_dropped(pg_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        game_log(pg_ctx, {"player": "Brandin Podziemski", "stat": "ts_pct"})
    # Not a stat at all - the required slot filled with something - adds nothing.
    assert game_log(pg_ctx, {"player": "Brandin Podziemski", "stat": "game log"}).data["columns"] == ["MIN", "PTS", "REB", "AST"]


def test_game_log_dates_are_the_eastern_day_the_game_was_played(pg_ctx: TemplateContext) -> None:
    """e2 tipped at 00:30 UTC on the 2nd - 7:30pm Eastern on the 1st. Matching
    the UTC day found it under the wrong date and missed it under the right one."""
    s = current_season()
    games = game_log(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons"}).data["games"]
    assert games[-1]["date"] == f"{s - 1}-12-01"
    assert [g["date"] for g in game_log(pg_ctx, {"player": "Brandin Podziemski", "date": f"{s - 1}-12-01"}).data["games"]] == [f"{s - 1}-12-01"]
    assert game_log(pg_ctx, {"player": "Brandin Podziemski", "date": f"{s - 1}-12-02"}).data["games"] == []


def test_a_date_finds_its_game_whatever_season_the_router_assumed(pg_ctx: TemplateContext) -> None:
    s = current_season()
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "date": f"{s - 1}-02-28", "season": s})
    assert [g["season"] for g in result.data["games"]] == [s - 1]


def test_without_counts_a_did_not_play_entry_and_a_missing_row_alike(pg_ctx: TemplateContext) -> None:
    """An injured player mostly has no row at all: Stephen Curry's 2026 is 43
    rows for an 82-game Warriors season, none of them did-not-play."""
    s = current_season()
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "without": "Stephen Curry"})
    assert sorted(g["date"] for g in result.data["games"]) == [f"{s - 1}-12-01", f"{s}-01-10"]
    assert "a did-not-play entry, or no line in the box score at all" in result.answer


def test_without_two_teammates_means_neither_of_them_played(pg_ctx: TemplateContext) -> None:
    """ "Celtics record without Tatum and Brown" read only the first name, so
    the answer covered the games without ONE of them. Podziemski played e1, e2
    and e3 this season: Curry played e1 and missed e2 and e3, Kuminga played e3
    and missed e1 and e2. Only e2 was played without both."""
    s = current_season()
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "without": ["Stephen Curry", "Jonathan Kuminga"]})
    assert [g["date"] for g in result.data["games"]] == [f"{s - 1}-12-01"]
    assert result.data["without"] == ["Stephen Curry", "Jonathan Kuminga"]
    assert "without Stephen Curry and Jonathan Kuminga" in result.answer
    # The one-name question is the same question it always was.
    assert len(game_log(pg_ctx, {"player": "Brandin Podziemski", "without": ["Stephen Curry"]}).data["games"]) == 2


def test_without_asks_between_two_teammates_who_share_a_name(pg_ctx: TemplateContext) -> None:
    answer = game_log(pg_ctx, {"player": "Brandin Podziemski", "without": "curry"}).answer
    assert answer == "'curry' matches more than one player - did you mean Seth Curry or Stephen Curry?"


def test_without_narrows_a_shared_name_to_that_seasons_teammates(pg_ctx: TemplateContext) -> None:
    # Last season Seth was a Celtic and Dell was long retired: only one Curry could be meant.
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "without": "curry", "season": current_season() - 1})
    assert result.data["without"] == ["Stephen Curry"]


def test_a_mid_season_arrival_is_not_missing_from_the_games_before_he_came(pg_ctx: TemplateContext) -> None:
    """Seth Curry joins at e3. The Warriors' games before that were not played
    "without" a man who was on another team's books."""
    answer = game_log(pg_ctx, {"player": "Brandin Podziemski", "without": "Seth Curry"}).answer
    assert answer == f"Brandin Podziemski played 3 games in the {current_season()} regular season, none of them without Seth Curry."


def test_a_teammate_carried_over_is_on_the_team_before_his_first_game(pg_ctx: TemplateContext) -> None:
    """LeBron James's first 2026 row is 2025-11-19; the Lakers' games before it
    were played without him. Kuminga here is the same shape."""
    result = game_log(pg_ctx, {"player": "Brandin Podziemski", "without": "Jonathan Kuminga"})
    assert [g["opponent"] for g in result.data["games"]] == ["DET", "BOS"]


def test_without_somebody_who_was_never_a_teammate_says_so(pg_ctx: TemplateContext) -> None:
    answer = game_log(pg_ctx, {"player": "Brandin Podziemski", "without": "Jaylen Brown"}).answer
    assert answer == f"Jaylen Brown was not Brandin Podziemski's teammate in any of his 3 games in the {current_season()} regular season."


def test_no_games_against_an_opponent_is_not_no_games(pg_ctx: TemplateContext) -> None:
    """The refusal names the missing fact: he has games, none of them against
    that team. "No games found for him" would send the reader to the wrong place."""
    answer = game_log(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Los Angeles Lakers"}).answer
    assert answer == f"Brandin Podziemski played 3 games in the {current_season()} regular season, none of them vs the Los Angeles Lakers."


def test_a_season_with_no_games_at_all_says_that(pg_ctx: TemplateContext) -> None:
    s = current_season() - 5
    assert game_log(pg_ctx, {"player": "Brandin Podziemski", "season": s}).answer == f"No {s} regular season games found for Brandin Podziemski."


def test_a_career_never_counts_the_phantom_season_twice(pg_ctx: TemplateContext) -> None:
    """1993 is a full copy of 1993-94 under another label; a career that read it
    would list every one of those games twice."""
    result = game_log(pg_ctx, {"player": "Michael Jordan", "span": "career"})
    assert [g["season"] for g in result.data["games"]] == [1995]
    assert "Box scores begin with the 1993-94 season, so his 1990-1993 seasons are not counted." in result.answer


@pytest.mark.parametrize("template", [game_log, player_stat])
def test_a_career_and_a_named_season_at_once_is_refused(pg_ctx: TemplateContext, template: Any) -> None:
    with pytest.raises(TemplateUnsupported):
        template(pg_ctx, {"player": "Brandin Podziemski", "span": "career", "season": current_season()})


def test_game_log_refuses_a_threshold_rather_than_ignoring_it(pg_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        game_log(pg_ctx, {"player": "Brandin Podziemski", "stat": "fieldGoalsAttempted", "threshold": 15})


def test_player_stat_averages_the_games_against_an_opponent(pg_ctx: TemplateContext) -> None:
    """ "evan mobley avg against bucks" refused before this - and before the
    refusal, it was answered with his whole season."""
    result = player_stat(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Detroit Pistons", "stat": "points"})
    assert result.answer == f"Brandin Podziemski averaged 17.5 points per game in 2 games vs the Detroit Pistons in the {current_season()} regular season. That is 35 in total."


def test_player_stat_honors_venue_and_a_teammates_absence(pg_ctx: TemplateContext) -> None:
    assert player_stat(pg_ctx, {"player": "Brandin Podziemski", "venue": "home", "stat": "points"}).data["stats"]["avgPoints"] == 12.5
    without = player_stat(pg_ctx, {"player": "Brandin Podziemski", "without": "Stephen Curry", "stat": "points"}).data["stats"]
    assert (without["gamesPlayed"], without["avgPoints"]) == (2, 17.5)


def test_player_stat_without_two_teammates_averages_only_the_games_neither_played(pg_ctx: TemplateContext) -> None:
    """e2 is the one game Podziemski played without both of them - 20 points.
    Read as a question about Curry alone it was 2 games and 17.5."""
    result = player_stat(pg_ctx, {"player": "Brandin Podziemski", "without": ["Stephen Curry", "Jonathan Kuminga"], "stat": "points"})
    assert (result.data["stats"]["gamesPlayed"], result.data["stats"]["avgPoints"]) == (1, 20.0)
    assert "without Stephen Curry and Jonathan Kuminga" in result.answer


def test_player_stat_says_one_game_not_one_games(pg_ctx: TemplateContext) -> None:
    answer = player_stat(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Boston Celtics", "stat": "points"}).answer
    assert "per game in 1 game vs the Boston Celtics" in answer


def test_player_stat_career_is_totals_over_games_not_an_average_of_averages(pg_ctx: TemplateContext) -> None:
    """(980 + 240) / (70 + 30) = 12.2. The mean of the two season averages would
    be 11.0 - a number no reader could reproduce from the career line."""
    s = current_season()
    result = player_stat(pg_ctx, {"player": "Brandin Podziemski", "span": "career"})
    assert result.data["stats"]["avgPoints"] == 12.2
    assert f"in 100 games over his career (2 regular seasons, {s - 1}-{s})" in result.answer


def test_player_stat_answers_a_shooting_percentage_with_its_makes_and_attempts(pg_ctx: TemplateContext) -> None:
    """ "What is Jokic's 3 point percentage this season" fell through to the agent."""
    s = current_season()
    season = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "threePointFieldGoalPct"}).answer
    assert season == f"Brandin Podziemski shot 35.7% on 3-pointers (100 of 280) in 70 games in the {s} regular season."
    career = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "threePointFieldGoalPct", "span": "career"}).answer
    assert "35.3% on 3-pointers (120 of 340)" in career
    against = player_stat(pg_ctx, {"player": "Brandin Podziemski", "stat": "freeThrowPct", "opponent": "Detroit Pistons"}).answer
    assert "83.3% on free throws (5 of 6) in 2 games vs the Detroit Pistons" in against


def test_player_stat_refuses_a_limit_rather_than_answering_the_season(pg_ctx: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_stat(pg_ctx, {"player": "Brandin Podziemski", "limit": 10})


def test_player_stat_names_the_real_cause_when_nothing_matches(pg_ctx: TemplateContext) -> None:
    answer = player_stat(pg_ctx, {"player": "Brandin Podziemski", "opponent": "Los Angeles Lakers"}).answer
    assert answer == f"Brandin Podziemski played 3 games in the {current_season()} regular season, none of them vs the Los Angeles Lakers."


def test_a_narrowed_question_carries_the_box_score_floor() -> None:
    """Jordan's 1990 season line is real and answerable; his 1990 line against
    one opponent needs box scores, which start in 1993-94."""
    from association.query.templates import check_coverage

    assert check_coverage("player_stat", {"player": "Michael Jordan", "season": 1990, "season_type": 2}) is None
    assert check_coverage("player_stat", {"player": "Michael Jordan", "season": 1990, "season_type": 2, "opponent": "New York Knicks"}) is not None
    assert check_coverage("game_log", {"player": "Michael Jordan", "season": 1990, "season_type": 2}) is not None


def test_player_history_career_is_every_season(ps_con: TemplateContext) -> None:
    """ "Jokic's scoring by year, career" came back as the default four seasons,
    under a heading that did not say how many it had left out."""
    s = current_season()
    for offset in range(1, 8):
        ps_con.con.execute("INSERT INTO player_season_stats_deduped (athlete_id, season, season_type, gamesPlayed, avgPoints) VALUES ('1',?,2,70,20.0)", [s - offset])
    result = player_history(ps_con, {"player": "Luka Doncic", "stat": "points", "span": "career", "limit": 4})
    assert len(result.data["seasons"]) == 8
    assert result.answer.startswith(f"Luka Doncic, points per game by regular season, career, {s - 7}-{s} (most recent first):")
    four = player_history(ps_con, {"player": "Luka Doncic", "stat": "points"}).answer
    assert four.startswith(f"Luka Doncic, points per game by regular season, {s - 3}-{s} (most recent first):")


def test_team_game_log_honors_opponent_and_venue(gl_con: TemplateContext) -> None:
    home = game_log(gl_con, {"team": "Knicks", "venue": "home"})
    assert [g["date"] for g in home.data["games"]] == ["2026-04-10"]
    assert home.answer.startswith(f"New York Knicks at home, most recent game of the {current_season()} regular season (1-0):")
    assert len(game_log(gl_con, {"team": "Knicks", "opponent": "Boston Celtics"}).data["games"]) == 2


def test_team_game_log_says_when_it_never_met_the_opponent(gl_con: TemplateContext) -> None:
    gl_con.con.execute("INSERT INTO teams VALUES ('13','LAL','Los Angeles Lakers')")
    answer = game_log(gl_con, {"team": "Knicks", "opponent": "Los Angeles Lakers"}).answer
    assert answer == f"The New York Knicks played 2 games in the {current_season()} regular season, none of them vs the Los Angeles Lakers."


def test_team_game_log_leaves_without_to_with_without(gl_con: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        game_log(gl_con, {"team": "Knicks", "without": "Jalen Brunson"})


def test_team_game_log_career_says_all_time(gl_con: TemplateContext) -> None:
    result = game_log(gl_con, {"team": "Knicks", "span": "career"})
    assert len(result.data["games"]) == 2
    assert f"(all-time, {current_season()} regular season) (1-1):" in result.answer


def test_team_game_log_is_not_doubled_by_a_phantom_season(gl_con: TemplateContext) -> None:
    """The phantom 1993 shares every event id with 1994, and a join on event_id
    alone listed each of those games twice - 164 rows for the Celtics' 82."""
    gl_con.con.execute("INSERT INTO games VALUES ('e1',?,2,'2026-04-10T22:00Z','18','2',112,95,'18')", [current_season() - 1])
    assert len(game_log(gl_con, {"team": "Knicks"}).data["games"]) == 2


def test_team_game_log_does_not_call_a_missing_result_a_loss(gl_con: TemplateContext) -> None:
    """134 games since 1994 have no winner recorded - "not the winner" is not the
    same fact as "lost"."""
    gl_con.con.execute("UPDATE games SET winner_team_id = NULL WHERE event_id = 'e2'")
    result = game_log(gl_con, {"team": "Knicks"})
    assert (result.data["wins"], result.data["losses"]) == (1, 0)
    assert "(1-0, 1 with no recorded result):" in result.answer


# ---------------- playoffs before 1993-94, by the year they were played ----------------


@pytest.fixture
def playoff_ctx(tmp_path: Path) -> TemplateContext:
    """ESPN's labels before 1993-94: the postseason games labelled 1990 are the
    1991 Finals, and the phantom 1993 is a copy of 1994's games."""
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('4','Chicago Bulls','CHI'),('13','Los Angeles Lakers','LAL')")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR, home_linescores VARCHAR, away_linescores VARCHAR)"
    )
    rows = [
        ("f1", 1990, 3, "1991-06-03T01:00Z", "4", "13", 91, 93, "13", "20,25,20,26", "25,20,24,24"),
        ("f2", 1990, 3, "1991-06-13T01:00Z", "13", "4", 101, 108, "4", "25,25,25,26", "27,27,27,27"),
        ("x1", 1993, 3, "1994-05-01T01:00Z", "4", "13", 100, 90, "4", "25,25,25,25", "20,20,25,25"),
        ("x1", 1994, 3, "1994-05-01T01:00Z", "4", "13", 100, 90, "4", "25,25,25,25", "20,20,25,25"),
    ]
    c.executemany("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.execute("CREATE TABLE team_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, home_away VARCHAR)")
    boxes: list[tuple[Any, ...]] = []
    for event, season, season_type, _date, home, away, *_rest in rows:
        boxes += [(event, season, season_type, home, away, "home"), (event, season, season_type, away, home, "away")]
    c.executemany("INSERT INTO team_box_stats VALUES (?,?,?,?,?,?)", boxes)
    return TemplateContext(con=c, out_dir=tmp_path)


def test_an_early_playoffs_is_found_by_the_year_it_was_played(playoff_ctx: TemplateContext) -> None:
    """ESPN labels the 1991 Finals season 1990. Matched by label, "the 1991
    playoffs" found 1992's, and "the 1990 playoffs" found 1991's."""
    both = ["Chicago Bulls", "Los Angeles Lakers"]
    assert head_to_head(playoff_ctx, {"teams": both, "season": 1991, "season_type": 3}).data["games"] == 2
    assert head_to_head(playoff_ctx, {"teams": both, "season": 1990, "season_type": 3}).data["games"] == 0


def test_the_phantom_season_does_not_double_a_playoffs(playoff_ctx: TemplateContext) -> None:
    assert head_to_head(playoff_ctx, {"teams": ["Chicago Bulls", "Los Angeles Lakers"], "season": 1994, "season_type": 3}).data["games"] == 1


def test_team_quarter_points_finds_an_early_playoffs_by_its_year(playoff_ctx: TemplateContext) -> None:
    got = team_quarter_points(playoff_ctx, {"team": "Chicago Bulls", "period": 1, "season": 1991, "season_type": 3})
    assert [g["points"] for g in got.data["games"]] == [20, 27]


def test_a_team_log_labels_an_early_playoffs_by_its_year(playoff_ctx: TemplateContext) -> None:
    got = game_log(playoff_ctx, {"team": "Chicago Bulls", "season": 1991, "season_type": 3})
    assert [g["season"] for g in got.data["games"]] == [1991, 1991]


def test_head_to_head_takes_the_opponent_as_the_other_team(playoff_ctx: TemplateContext) -> None:
    """ "Celtics vs Bulls head to head record" arrived as team + opponent and was refused."""
    check_scope("head_to_head", {"team": "Chicago Bulls", "opponent": "Los Angeles Lakers"})
    got = head_to_head(playoff_ctx, {"team": "Chicago Bulls", "opponent": "Los Angeles Lakers", "season": 1991, "season_type": 3})
    assert got.data["games"] == 2


def test_head_to_head_reads_past_a_team_named_twice(playoff_ctx: TemplateContext) -> None:
    """The first two names are one team; the opponent after them is the second."""
    slots = {"team": "Chicago Bulls", "teams": ["Bulls"], "opponent": "Los Angeles Lakers", "season": 1991, "season_type": 3}
    assert head_to_head(playoff_ctx, slots).data["games"] == 2
