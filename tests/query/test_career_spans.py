"""The widened leaderboard, and career spans on leaderboard, threshold_count and
single_game_high.

Each fixture carries the specific data fault its test is about, because every
one of them was found in the real warehouse and each produces a fluent wrong
answer when ignored: a traded player's combined row that is empty, a
postseason that is a copy of the regular season, the 1993 phantom season, an
empty box score, a career that began before the box scores do.
"""

import json
import re
from pathlib import Path
from typing import Any

import duckdb
import pytest

from association.query.leaderboard import resolve_metric
from association.query.metrics import BOX_SCORE_METRIC_NAMES, CORE_METRIC_NAMES, LEADERBOARD_METRICS
from association.query.prompt import TOOLS, build_system_prompt
from association.query.router import ROUTER_PROMPT
from association.query.templates import TemplateContext, TemplateUnsupported, check_scope, leaderboard, single_game_high, threshold_count
from association.season import current_season

_STATS_COLUMNS = (
    "athlete_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, gamesPlayed INTEGER, points INTEGER, avgPoints DOUBLE, "
    "threePointFieldGoalsMade INTEGER, threePointFieldGoalsAttempted INTEGER"
)


def _context(tmp_path: Path, players: list[tuple[str, str]], stats: list[tuple[Any, ...]]) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.executemany("INSERT INTO players VALUES (?,?)", players)
    c.execute(f"CREATE TABLE player_season_stats ({_STATS_COLUMNS})")
    c.executemany("INSERT INTO player_season_stats VALUES (?,?,?,?,?,?,?,?,?)", stats)
    return TemplateContext(con=c, out_dir=tmp_path)


# ---------------- the router's vocabulary, and the agent's budget ----------------


def _router_stat_names() -> list[str]:
    """The stat names ROUTER_PROMPT teaches, read out of the prompt itself."""
    paragraph = ROUTER_PROMPT.split("stat names a box-score category:", 1)[1].split("Set it whenever", 1)[0]
    return [word for word in re.findall(r"[A-Za-z]+", paragraph) if word not in {"or", "a", "shooting", "percentage"}]


def test_every_stat_name_the_router_is_taught_ranks_by_a_metric() -> None:
    """Turnovers, minutes, fouls, the three makes and the three percentages were
    all taught to the router and none had a metric, so every leaderboard
    question naming one fell through to the agent. Read from the prompt so a
    name added there without a metric here fails."""
    names = _router_stat_names()
    assert len(names) == 14, names
    for name in names:
        assert resolve_metric(name) in LEADERBOARD_METRICS, name
        career = resolve_metric(name, career=True)
        assert career in LEADERBOARD_METRICS and LEADERBOARD_METRICS[career].career is not None, name


def test_a_career_reads_a_bare_stat_as_a_total_and_a_season_as_it_always_did() -> None:
    assert resolve_metric("points") == "avg_points"
    assert resolve_metric("points", career=True) == "total_points"
    # A real metric name is never reinterpreted, so a career average stays reachable.
    assert resolve_metric("avg_points", career=True) == "avg_points"
    assert resolve_metric("threePointFieldGoalsMade") == "total_three_pointers_made"


def test_the_new_metrics_stay_out_of_the_agents_preamble() -> None:
    """The preamble spells CORE_METRIC_NAMES out three times and has ~120
    tokens of headroom; these would cost ~200. They are reachable by name."""
    preamble = build_system_prompt("") + json.dumps(TOOLS)
    for name in BOX_SCORE_METRIC_NAMES:
        assert name not in CORE_METRIC_NAMES
        # On word boundaries: "fg_pct" is inside "efg_pct", which is listed.
        assert not re.search(rf"\b{name}\b", preamble), name


# ---------------- season leaderboards ----------------


@pytest.fixture
def season_ctx(tmp_path: Path) -> TemplateContext:
    s = current_season()
    return _context(
        tmp_path,
        [("1", "Sharp Shooter"), ("2", "Low Volume"), ("3", "Volume Scorer"), ("4", "Copy Guy")],
        [
            ("1", s, 2, "5", 70, 1400, 20.0, 117, 245),
            ("2", s, 2, "6", 20, 700, 35.0, 30, 50),  # 60% on 50 attempts: under the qualifier
            ("3", s, 2, "7", 80, 2000, 25.0, 135, 296),
            ("3", s, 3, "7", 10, 300, 30.0, 20, 40),
            ("4", s, 2, "8", 81, 1576, 19.5, 5, 10),
            ("4", s, 3, "8", 81, 1576, 19.5, 5, 10),  # a "postseason" that copies his regular season
        ],
    )


def test_a_shooting_percentage_names_its_qualifier_and_its_volume(season_ctx: TemplateContext) -> None:
    answer = leaderboard(season_ctx, {"stat": "threePointFieldGoalPct"}).answer
    assert answer == (
        f"Sharp Shooter led the league in 3-point percentage in the {current_season()} regular season (minimum 200 3-point attempts), at 47.8% (117 of 245). Next: Volume Scorer (45.6%)."
    )


def test_most_threes_is_a_season_count(season_ctx: TemplateContext) -> None:
    result = leaderboard(season_ctx, {"stat": "threePointFieldGoalsMade"})
    assert result.data["leaders"][0]["display_name"] == "Volume Scorer"
    assert "led the league in 3-pointers made" in result.answer and "at 135." in result.answer


def test_rate_total_ranks_the_season_total_rather_than_the_average(season_ctx: TemplateContext) -> None:
    assert leaderboard(season_ctx, {"stat": "points"}).data["leaders"][0]["display_name"] == "Low Volume"
    result = leaderboard(season_ctx, {"stat": "points", "rate": "total"})
    assert result.data["leaders"][0]["display_name"] == "Volume Scorer"
    assert "in total points" in result.answer and "2,000" in result.answer


def test_a_postseason_copied_from_the_regular_season_is_not_a_postseason(season_ctx: TemplateContext) -> None:
    """Eddy Curry never played a playoff game and has 6,820 playoff points,
    because his regular seasons are stored twice. Unfiltered, this board is his."""
    leaders = leaderboard(season_ctx, {"stat": "points", "rate": "total", "season_type": 3}).data["leaders"]
    assert [row["display_name"] for row in leaders] == ["Volume Scorer"]


# ---------------- career leaderboards ----------------


@pytest.fixture
def career_ctx(tmp_path: Path) -> TemplateContext:
    stats: list[tuple[Any, ...]] = []
    stats += [("10", season, 2, "5", 82, 2000, 24.4, 0, 0) for season in range(2004, 2009)]
    stats += [("10", 2006, 3, "5", 13, 400, 30.8, 0, 0)]
    stats += [("11", season, 2, "4", 82, 2500, 30.5, 0, 0) for season in range(1985, 1990)]
    stats += [("11", 1996, 2, "4", 82, 2491, 30.4, 0, 0), ("11", 1996, 3, "4", 18, 552, 30.7, 0, 0)]
    # Moses Malone's 1976-77: two stints and a combined row that is all NULL.
    stats += [("12", 1977, 2, "10", 80, 1083, 13.5, 0, 0), ("12", 1977, 2, "12", 2, 0, 0.0, 0, 0), ("12", 1977, 2, None, None, None, None, None, None)]
    stats += [("12", 1995, 2, "24", 17, 49, 2.9, 0, 0)]
    # Two stints and a combined row that agrees with them: one season, not two.
    stats += [("13", 2020, 2, "1", 40, 800, 20.0, 0, 0), ("13", 2020, 2, "2", 30, 600, 20.0, 0, 0), ("13", 2020, 2, None, 70, 1400, 20.0, 0, 0)]
    stats += [("14", 2007, 2, "18", 81, 1576, 19.5, 0, 0), ("14", 2007, 3, "18", 81, 1576, 19.5, 0, 0)]
    stats += [("15", 2020, 2, "9", 10, 400, 40.0, 0, 0)]
    players = [("10", "LeBron James"), ("11", "Michael Jordan"), ("12", "Moses Malone"), ("13", "Traded Guy"), ("14", "Eddy Curry"), ("15", "Short Career")]
    return _context(tmp_path, players, stats)


def _ranked(result_leaders: list[dict[str, Any]]) -> list[tuple[str, Any]]:
    return [(row["display_name"], row["value"]) for row in result_leaders]


def test_career_points_are_summed_from_the_stints_and_counted_once(career_ctx: TemplateContext) -> None:
    """Moses Malone's combined 1976-77 row is empty, so a sum over it loses the
    season; a traded player's combined row alongside its stints would count
    his season twice. Summed over the stints, each is right."""
    leaders = leaderboard(career_ctx, {"stat": "points", "span": "career"}).data["leaders"]
    assert _ranked(leaders) == [
        ("Michael Jordan", 14991),
        ("LeBron James", 10000),
        ("Eddy Curry", 1576),
        ("Traded Guy", 1400),
        ("Moses Malone", 1132),
        ("Short Career", 400),
    ]


def test_a_career_list_says_whose_careers_and_that_it_is_not_all_time(career_ctx: TemplateContext) -> None:
    answer = leaderboard(career_ctx, {"stat": "points", "span": "career", "limit": 2}).answer
    assert answer == (
        "Among players active in 1993-94 or later, Michael Jordan leads in career points in the regular season: 14,991, over 492 games (1984-85 through 1995-96). "
        "Next: LeBron James (10,000). Careers that ended before 1993-94 are not in this warehouse, so this is not an all-time list."
    )


def test_a_career_average_is_games_weighted_and_qualified(career_ctx: TemplateContext) -> None:
    result = leaderboard(career_ctx, {"stat": "avg_points", "span": "career"})
    assert [name for name, _ in _ranked(result.data["leaders"])] == ["Michael Jordan", "LeBron James"]  # Short Career's 40.0 is 10 games
    assert result.data["leaders"][0]["value"] == pytest.approx(14991 / 492)
    assert "(minimum 400 games)" in result.answer


def test_a_career_postseason_leaves_out_the_copied_postseasons(career_ctx: TemplateContext) -> None:
    leaders = leaderboard(career_ctx, {"stat": "points", "span": "career", "season_type": 3}).data["leaders"]
    assert _ranked(leaders) == [("Michael Jordan", 552), ("LeBron James", 400)]


@pytest.mark.parametrize(
    "slots",
    [
        {"stat": "points", "span": "career", "season": 2015},  # since 2015? through it? only it?
        {"stat": "points", "span": "career", "team": "Warriors"},  # a franchise's list is a different pool
        {"stat": "points", "span": "career", "fields": ["minutes"]},
        {"stat": "ts_pct", "span": "career"},  # needs team context no season row carries
        {"stat": "points", "span": "decade"},
    ],
)
def test_a_career_leaderboard_refuses_what_it_cannot_answer(career_ctx: TemplateContext, slots: dict[str, Any]) -> None:
    with pytest.raises(TemplateUnsupported):
        leaderboard(career_ctx, slots)


@pytest.mark.parametrize("intent", ["leaderboard", "threshold_count", "single_game_high"])
def test_a_career_span_is_honoured_by_the_ranking_templates(intent: str) -> None:
    check_scope(intent, {"span": "career"})


# ---------------- career counts and highs, from box scores ----------------


@pytest.fixture
def box_ctx(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','LeBron James'),('6','Michael Jordan'),('7','Stephen Curry'),('8','Seth Curry'),('9','Hakeem Olajuwon')")
    c.execute("CREATE TABLE games (event_id VARCHAR, season INTEGER, date VARCHAR)")
    c.executemany(
        "INSERT INTO games VALUES (?,?,?)",
        [
            ("m1", 1995, "1995-03-29T00:30Z"),
            ("h1", 1994, "1994-03-01T01:00Z"),
            ("h1", 1993, "1994-03-01T01:00Z"),  # the phantom: 1993-94's games again, labelled 1993
            ("l1", 2014, "2014-03-04T00:30Z"),  # 7:30pm Eastern on 3 March
            ("l2", 2015, "2015-01-10T01:00Z"),
            ("x1", 2014, "2014-02-01T01:00Z"),
        ],
    )
    c.execute("CREATE TABLE player_box_stats (event_id VARCHAR, season INTEGER, season_type INTEGER, athlete_id VARCHAR, points INTEGER, minutes INTEGER, did_not_play BOOLEAN)")
    c.executemany(
        "INSERT INTO player_box_stats VALUES (?,?,?,?,?,?,?)",
        [
            ("m1", 1995, 2, "6", 55, 40, False),
            ("h1", 1994, 2, "9", 45, 42, False),
            ("h1", 1993, 2, "9", 45, 42, False),
            ("l1", 2014, 2, "1", 61, 40, False),
            ("l2", 2015, 2, "1", 35, 38, False),
            # An empty box score: the game is there, every line in it blank.
            ("x1", 2014, 2, "1", 0, None, False),
            ("x1", 2014, 2, "7", 0, None, False),
            ("x1", 2014, 2, "8", 0, None, True),
        ],
    )
    c.execute(
        "CREATE VIEW player_game_log AS SELECT b.*, p.display_name AS player_name, g.date AS game_date, 'OPP' AS opponent_abbr "
        "FROM player_box_stats b JOIN players p ON p.athlete_id = b.athlete_id LEFT JOIN games g ON g.event_id = b.event_id AND g.season = b.season"
    )
    c.execute("CREATE TABLE player_season_stats (athlete_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, gamesPlayed INTEGER, points INTEGER)")
    c.executemany(
        "INSERT INTO player_season_stats VALUES (?,?,?,?,?,?)",
        [("1", 2004, 2, "5", 79, 1654), ("1", 2015, 2, "5", 69, 1743), ("6", 1985, 2, "4", 82, 2313), ("6", 1995, 2, "4", 17, 457), ("9", 1985, 2, "10", 82, 1712)],
    )
    return TemplateContext(con=c, out_dir=tmp_path)


def test_a_career_count_does_not_count_the_phantom_season_twice(box_ctx: TemplateContext) -> None:
    result = threshold_count(box_ctx, {"stat": "points", "threshold": 40, "span": "career"})
    assert {"player": "Hakeem Olajuwon", "games": 1} in result.data["leaders"]
    assert "since 1993-94" in result.answer and "not all-time counts" in result.answer
    assert "1 game in 2013-14 has an empty box score in this warehouse, so these counts may be low." in result.answer


def test_a_players_career_count_names_the_career_and_the_games_it_could_not_see(box_ctx: TemplateContext) -> None:
    answer = threshold_count(box_ctx, {"stat": "points", "threshold": 30, "span": "career", "player": "LeBron James"}).answer
    assert answer == (
        "LeBron James had 2 games with 30+ points in his regular season career (2003-04 through 2014-15). "
        "1 of LeBron James's games in 2013-14 has an empty box score in this warehouse, so the count may be low."
    )


def test_a_career_that_began_before_the_box_scores_says_so_first(box_ctx: TemplateContext) -> None:
    """Michael Jordan's "career high" from these box scores is 55, not 69. The
    gap is stated before the number, not after it."""
    answer = single_game_high(box_ctx, {"stat": "points", "span": "career", "player": "Michael Jordan"}).answer
    assert answer.startswith("Box scores here begin in 1993-94, and Michael Jordan's regular season career began in 1984-85")
    assert answer.endswith("highest point total in a single game in the regular season since 1993-94 was 55, on 1995-03-28 vs OPP.")
    count = threshold_count(box_ctx, {"stat": "points", "threshold": 50, "span": "career", "player": "Michael Jordan"}).answer
    assert count.startswith("Box scores here begin in 1993-94") and "since 1993-94" in count


def test_a_career_high_is_dated_the_day_it_was_played(box_ctx: TemplateContext) -> None:
    answer = single_game_high(box_ctx, {"stat": "points", "span": "career", "player": "LeBron James"}).answer
    assert answer.startswith("LeBron James's highest point total in a single game in his regular season career (2003-04 through 2014-15) was 61, on 2014-03-03 vs OPP.")


def test_the_leagues_best_game_is_not_called_all_time(box_ctx: TemplateContext) -> None:
    result = single_game_high(box_ctx, {"stat": "points", "span": "career", "limit": 5})
    assert [game["value"] for game in result.data["games"]][:4] == [61, 55, 45, 35]  # Hakeem's 45 once, not twice
    assert "in the regular season since 1993-94: 61, on 2014-03-03" in result.answer
    assert "so this is not an all-time record" in result.answer


def test_an_ambiguous_name_is_asked_about_rather_than_counted(box_ctx: TemplateContext) -> None:
    """It used to be an ILIKE per word: "Curry" counted both and reported the bigger."""
    answer = threshold_count(box_ctx, {"stat": "points", "threshold": 1, "span": "career", "player": "Curry"}).answer
    assert "did you mean Seth Curry or Stephen Curry?" in answer


@pytest.mark.parametrize("template", [threshold_count, single_game_high])
def test_a_career_with_a_season_named_is_refused_rather_than_read(box_ctx: TemplateContext, template: Any) -> None:
    with pytest.raises(TemplateUnsupported, match="since it, or through it"):
        template(box_ctx, {"stat": "points", "threshold": 30, "span": "career", "season": 2024})
