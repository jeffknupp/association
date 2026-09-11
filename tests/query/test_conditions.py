"""Tests for the game-condition templates: player_splits, with_without,
record_when, player_matchup and streak.

One small league, built by hand so every number asserted below can be counted
off the fixture rather than guessed. It holds each of the data shapes those
templates have to get right, measured in the real warehouse first:

- a DNP row, a player with no row at all, and a NULL-minutes row beside
  teammates who have minutes - three ways of missing a game;
- a game whose box score is missing entirely (every Celtic has NULL
  minutes), which is unknown rather than missed;
- a 0-0 placeholder with no winner, which is not a result;
- games tipping after 7pm Eastern, whose UTC date is the next day;
- a player traded between seasons, and a Celtics game from before Tatum's
  first box score.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest

from association.query.templates import (
    SPLIT_KINDS,
    TemplateContext,
    TemplateResult,
    TemplateUnsupported,
    check_coverage,
    player_matchup,
    player_splits,
    record_when,
    streak,
    with_without,
)
from association.season import current_season

S = current_season()
BOS, LAL, PHI = "2", "13", "20"
TATUM, BROWN, LEBRON, EMBIID, JOURNEYMAN = "10", "11", "20", "30", "40"

# (athlete, team, minutes, points, starter, did_not_play). Every other stat is
# fixed: 5 rebounds, 3 assists, one 3-pointer, and points//2 of points made.
Line = tuple[str, str, Any, int, bool, bool]


def _played(athlete: str, team: str, points: int, starter: bool = True, minutes: int = 30) -> Line:
    return (athlete, team, minutes, points, starter, False)


def _dnp(athlete: str, team: str) -> Line:
    return (athlete, team, None, 0, False, True)


def _blank(athlete: str, team: str) -> Line:
    """The 2006-2018 shape: not flagged DNP, but NULL minutes and zeros."""
    return (athlete, team, None, 0, False, False)


def _game(
    c: duckdb.DuckDBPyConnection,
    event: str,
    date: str,
    home: str,
    away: str,
    home_score: int,
    away_score: int,
    lines: list[Line],
    season: int = S,
    winner: str | None = "auto",
    team_box: bool = True,
) -> None:
    decided = (home if home_score > away_score else away) if winner == "auto" else winner
    c.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?)", [event, season, 2, date, home, away, home_score, away_score, decided])
    for team, opponent, side in ((home, away, "home"), (away, home, "away")):
        stats = [40, 20, 10, 40, 85] if team_box else [None] * 5
        c.execute("INSERT INTO team_box_stats VALUES (?,?,?,?,?,?,?,?,?,?,?)", [event, season, 2, team, opponent, side, *stats])
    for athlete, team, minutes, points, starter, dnp in lines:
        opponent = away if team == home else home
        c.execute(
            "INSERT INTO player_box_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [event, season, 2, team, opponent, athlete, starter, dnp, minutes, points, 0 if minutes is None else 5, 0 if minutes is None else 3, 0, 0, 0]
            + [0 if minutes is None else 1, points // 2, points, 0, 0],
        )


@pytest.fixture
def league(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season BIGINT, season_type BIGINT, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score BIGINT, away_score BIGINT, winner_team_id VARCHAR)"
    )
    c.execute(
        "CREATE TABLE team_box_stats (event_id VARCHAR, season BIGINT, season_type BIGINT, team_id VARCHAR, opponent_team_id VARCHAR, home_away VARCHAR, "
        "totalRebounds BIGINT, assists BIGINT, threePointFieldGoalsMade BIGINT, fieldGoalsMade BIGINT, fieldGoalsAttempted BIGINT)"
    )
    c.execute(
        "CREATE TABLE player_box_stats (event_id VARCHAR, season BIGINT, season_type BIGINT, team_id VARCHAR, opponent_team_id VARCHAR, athlete_id VARCHAR, "
        "starter BOOLEAN, did_not_play BOOLEAN, minutes BIGINT, points BIGINT, rebounds BIGINT, assists BIGINT, steals BIGINT, blocks BIGINT, turnovers BIGINT, "
        "threePointFieldGoalsMade BIGINT, fieldGoalsMade BIGINT, fieldGoalsAttempted BIGINT, freeThrowsMade BIGINT, fouls BIGINT)"
    )
    c.execute(
        "INSERT INTO players VALUES (?, 'Jayson Tatum'), (?, 'Jaylen Brown'), (?, 'LeBron James'), (?, 'Joel Embiid'), (?, 'Journeyman Guy')",
        [TATUM, BROWN, LEBRON, EMBIID, JOURNEYMAN],
    )
    c.execute("INSERT INTO teams VALUES (?, 'BOS', 'Boston Celtics'), (?, 'LAL', 'Los Angeles Lakers'), (?, 'PHI', 'Philadelphia 76ers')", [BOS, LAL, PHI])

    last = S - 1
    # Last season. e0z comes before Tatum's first box score, so it is never a
    # game the Celtics played "without" him. The Celtics end the season on
    # three straight wins (e0c, e0a, e0d) and open this one with a fourth.
    _game(c, "e0z", f"{last - 1}-10-15T23:30Z", BOS, PHI, 100, 90, [_played(BROWN, BOS, 20)], season=last)
    _game(c, "e0b", f"{last - 1}-10-20T23:30Z", PHI, BOS, 110, 100, [_played(TATUM, BOS, 20), _played(JOURNEYMAN, BOS, 8), _played(EMBIID, PHI, 35)], season=last)
    _game(c, "e0c", f"{last - 1}-10-25T23:30Z", BOS, PHI, 105, 95, [_played(TATUM, BOS, 25), _played(EMBIID, PHI, 20)], season=last)
    _game(c, "e0a", f"{last - 1}-11-10T00:30Z", BOS, LAL, 100, 90, [_played(TATUM, BOS, 30), _played(JOURNEYMAN, BOS, 10), _played(LEBRON, LAL, 25)], season=last)
    _game(c, "e0d", f"{last - 1}-11-20T00:30Z", BOS, LAL, 101, 99, [_played(TATUM, BOS, 28), _played(LEBRON, LAL, 22)], season=last)

    # This season. Journeyman has moved to the Lakers.
    # e1 tips 8:30pm Eastern on October 31st: its UTC date is November 1st.
    _game(c, "e1", f"{S - 1}-11-01T00:30Z", BOS, LAL, 110, 100, [_played(TATUM, BOS, 30), _played(BROWN, BOS, 20), _played(LEBRON, LAL, 28), _played(JOURNEYMAN, LAL, 5, False)])
    # Tatum listed DNP.
    _game(c, "e2", f"{S - 1}-11-03T00:30Z", LAL, BOS, 105, 100, [_dnp(TATUM, BOS), _played(BROWN, BOS, 25), _played(LEBRON, LAL, 30), _played(JOURNEYMAN, LAL, 12)])
    # Tatum not in the box score at all.
    _game(c, "e3", f"{S - 1}-11-05T00:30Z", BOS, PHI, 120, 100, [_played(BROWN, BOS, 18), _played(EMBIID, PHI, 22)])
    # A 0-0 placeholder with no winner, between two Celtics wins.
    _game(c, "e6", f"{S - 1}-11-06T17:00Z", BOS, PHI, 0, 0, [], winner=None)
    _game(c, "e4", f"{S - 1}-11-07T00:30Z", PHI, BOS, 98, 99, [_played(TATUM, BOS, 35, False), _played(BROWN, BOS, 10), _played(EMBIID, PHI, 40)])
    # The Celtics' box score is missing: both of theirs have NULL minutes.
    _game(c, "e5", f"{S - 1}-11-09T00:30Z", BOS, LAL, 101, 99, [_blank(TATUM, BOS), _blank(BROWN, BOS), _played(LEBRON, LAL, 20)], team_box=False)
    # 8pm Eastern on November 30th. Journeyman has the NULL-minutes shape
    # beside teammates who played: a game he did not play.
    _game(c, "e7", f"{S - 1}-12-01T01:00Z", LAL, BOS, 115, 105, [_played(TATUM, BOS, 31), _played(BROWN, BOS, 12), _played(LEBRON, LAL, 33), _blank(JOURNEYMAN, LAL)])
    return TemplateContext(con=c, out_dir=tmp_path)


def _slots(**given: Any) -> dict[str, Any]:
    return {"season_type": 2, **given}


def _rows(result: TemplateResult, split: str) -> dict[str, dict[str, Any]]:
    return {row["group"]: row for row in result.data["splits"][split]}


# ---------------- player_splits ----------------


def test_splits_count_only_games_he_played(league: TemplateContext) -> None:
    """A DNP row, no row and a missing box score are three games Tatum did not
    play in this season as far as any box score says; he played e1, e4, e7."""
    result = player_splits(league, _slots(player="Jayson Tatum", split="home_away"))
    assert result.data["games"] == 3
    rows = _rows(result, "home_away")
    assert (rows["home"]["games"], rows["away"]["games"]) == (1, 2)
    assert rows["away"]["points"] == pytest.approx(33.0)  # e4 35, e7 31


def test_a_month_is_the_eastern_date_the_game_was_played(league: TemplateContext) -> None:
    """e1 is stored as November 1st UTC and was played on October 31st; e7 is
    December 1st UTC and November 30th Eastern. On the UTC date the split would
    be one game in each of three months."""
    rows = _rows(player_splits(league, _slots(player="Jayson Tatum", split="month")), "month")
    assert [(name, row["games"]) for name, row in rows.items()] == [("October", 1), ("November", 2)]


def test_an_empty_half_of_a_split_is_shown_not_dropped(league: TemplateContext) -> None:
    rows = _rows(player_splits(league, _slots(player="Jaylen Brown", split="starter_bench")), "starter_bench")
    assert rows["bench"]["games"] == 0 and rows["starter"]["games"] == 5  # e1, e2, e3, e4, e7


def test_no_split_named_shows_all_four(league: TemplateContext) -> None:
    result = player_splits(league, _slots(player="Jayson Tatum"))
    assert set(result.data["splits"]) == set(SPLIT_KINDS)
    wins = _rows(result, "wins_losses")
    assert (wins["wins"]["games"], wins["losses"]["games"]) == (2, 1)


def test_splits_say_a_missing_box_score_was_not_counted(league: TemplateContext) -> None:
    answer = player_splits(league, _slots(player="Jayson Tatum", split="home_away")).answer
    assert "no box score for 1 of his team's games" in answer


def test_a_career_is_every_season_not_the_current_one(league: TemplateContext) -> None:
    result = player_splits(league, _slots(player="Jayson Tatum", split="home_away", span="career"))
    assert result.data["games"] == 7
    assert result.data["span"] == f"{S - 1}-{S} regular seasons"


def test_a_team_split_uses_the_teams_own_games(league: TemplateContext) -> None:
    """The placeholder e6 is no game at all; e5's result stands even though its
    box score is missing, and the averages its box would feed say so."""
    result = player_splits(league, _slots(team="Boston Celtics", split="wins_losses"))
    rows = _rows(result, "wins_losses")
    assert (rows["wins"]["games"], rows["losses"]["games"]) == (4, 2)
    assert "missing from 1 of those games' box scores" in result.answer


def test_a_team_has_no_starter_split(league: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_splits(league, _slots(team="Boston Celtics", split="starter_bench"))


def test_an_unknown_split_is_refused(league: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_splits(league, _slots(player="Jayson Tatum", split="by_weekday"))


def test_a_player_with_only_dnp_rows_is_told_apart_from_one_with_none(league: TemplateContext) -> None:
    listed = player_splits(league, _slots(player="Jayson Tatum", team="Boston Celtics", season=S - 2)).answer
    assert listed == f"Jayson Tatum has no games for the Boston Celtics in the {S - 2} regular season in the warehouse."
    # Leave him only e2's DNP row and e5's NULL-minutes one this season.
    league.con.execute("DELETE FROM player_box_stats WHERE athlete_id = ? AND season = ? AND event_id NOT IN ('e2', 'e5')", [TATUM, S])
    sat = player_splits(league, _slots(player="Jayson Tatum")).answer
    assert sat == f"Jayson Tatum was listed in 2 box scores in the {S} regular season but did not play in any of them."


# ---------------- with_without ----------------


def test_with_and_without_is_counted_inside_his_time_on_the_team(league: TemplateContext) -> None:
    """With Tatum: e1 W, e4 W, e7 L. Without: e2 (DNP) L, e3 (no row) W. e5's
    box score is missing, so it is on neither side; the placeholder is not a
    game."""
    result = with_without(league, _slots(team="Boston Celtics", without="Tatum"))
    groups = {g["teammate_played"]: g for g in result.data["groups"]}
    assert (groups[True]["wins"], groups[True]["losses"]) == (2, 1)
    assert (groups[False]["wins"], groups[False]["losses"]) == (1, 1)
    assert "1 game inside that time has no box score" in result.answer
    assert result.answer.splitlines()[2].startswith("Jayson Tatum out")  # a "without" question leads with it


def test_without_two_teammates_counts_only_the_games_neither_played(league: TemplateContext) -> None:
    """The measured bug: "Celtics record without Tatum and Brown" dropped the
    second name and answered about Tatum alone (1-1 here). With Brown sitting
    out e3 as well, e3 is the only game neither of them played."""
    league.con.execute(
        "INSERT INTO player_box_stats VALUES ('e3', ?, 2, ?, ?, ?, FALSE, FALSE, 10, 4, 2, 1, 0, 0, 0, 0, 2, 4, 0, 1)",
        [S, BOS, PHI, JOURNEYMAN],
    )  # e3 keeps a box score once Brown sits: without it the game counts as one nobody can tell
    league.con.execute("UPDATE player_box_stats SET did_not_play = TRUE, minutes = NULL WHERE athlete_id = ? AND event_id = 'e3'", [BROWN])
    result = with_without(league, _slots(team="Boston Celtics", without=["Tatum", "Jaylen Brown"]))
    groups = {g["teammate_played"]: g for g in result.data["groups"]}
    assert (groups[False]["wins"], groups[False]["losses"]) == (1, 0)  # e3
    assert (groups[True]["wins"], groups[True]["losses"]) == (2, 2)  # e1, e2, e4, e7
    assert result.data["teammates"] == ["Jayson Tatum", "Jaylen Brown"]
    assert "Jayson Tatum and Jaylen Brown out" in result.answer


def test_with_two_teammates_counts_only_the_games_both_played(league: TemplateContext) -> None:
    """The mirror of the same rule: "record when A and B play" is the games
    both of them played, not the games either did."""
    league.con.execute(
        "INSERT INTO player_box_stats VALUES ('e3', ?, 2, ?, ?, ?, FALSE, FALSE, 10, 4, 2, 1, 0, 0, 0, 0, 2, 4, 0, 1)",
        [S, BOS, PHI, JOURNEYMAN],
    )  # e3 keeps a box score once Brown sits: without it the game counts as one nobody can tell
    league.con.execute("UPDATE player_box_stats SET did_not_play = TRUE, minutes = NULL WHERE athlete_id = ? AND event_id = 'e3'", [BROWN])
    result = with_without(league, _slots(team="Boston Celtics", with_player=["Jayson Tatum", "Jaylen Brown"]))
    groups = {g["teammate_played"]: g for g in result.data["groups"]}
    assert (groups[True]["wins"], groups[True]["losses"]) == (2, 1)  # e1, e4, e7
    assert (groups[False]["wins"], groups[False]["losses"]) == (1, 1)  # e2, e3
    assert result.answer.splitlines()[2].startswith("Jayson Tatum and Jaylen Brown played")


def test_a_game_before_he_arrived_is_not_a_game_without_him(league: TemplateContext) -> None:
    """The StatMuse failure: "Nets record without KD all-time" counted decades
    of Nets games before he arrived. e0z is a Celtics win before Tatum's first
    box score; counted, "without" would be 2-1."""
    result = with_without(league, _slots(team="Boston Celtics", without="Tatum", span="career"))
    groups = {g["teammate_played"]: g for g in result.data["groups"]}
    assert (groups[False]["wins"], groups[False]["losses"]) == (1, 1)
    assert (groups[True]["wins"], groups[True]["losses"]) == (5, 2)
    assert result.data["tenure"] == [{"team": "Boston Celtics", "from": f"{S - 2}-10-20", "to": f"{S - 1}-11-30"}]


def test_a_traded_player_is_not_missing_from_his_old_team(league: TemplateContext) -> None:
    """Journeyman left the Celtics last season: this season's Celtics games are
    not games they played without him, and the refusal says why."""
    answer = with_without(league, _slots(team="Boston Celtics", without="Journeyman Guy")).answer
    # e0a tips at 00:30 UTC on the 10th, which is the evening of the 9th in Boston.
    assert "falls outside the" in answer and f"Boston Celtics {S - 2}-10-20 to {S - 2}-11-09" in answer


def test_a_player_who_left_and_came_back_has_two_spells_not_one(league: TemplateContext) -> None:
    """LeBron James' two Cleveland spells are two stints: the Cavaliers' Miami
    years are not games they played without him. Here a Celtic plays e0z, is a
    Laker in e0a, and is a Celtic again in e3. Read as one spell, every Celtics
    game between e0z and e3 would be a game "without" him."""
    league.con.execute("INSERT INTO players VALUES ('50', 'Boomerang Guy')")
    for event, team, opponent in (("e0z", BOS, PHI), ("e0a", LAL, BOS), ("e3", BOS, PHI)):
        season = S if event == "e3" else S - 1
        league.con.execute(
            "INSERT INTO player_box_stats VALUES (?,?,2,?,?,'50',TRUE,FALSE,20,10,5,3,0,0,0,1,5,10,0,0)",
            [event, season, team, opponent],
        )
    result = with_without(league, _slots(team="Boston Celtics", without="Boomerang Guy", span="career"))
    groups = {g["teammate_played"]: g for g in result.data["groups"]}
    assert groups[False]["games"] == 0 and groups[True]["games"] == 2
    assert [t["team"] for t in result.data["tenure"]] == ["Boston Celtics", "Boston Celtics"]


def test_a_teammate_who_never_played_for_the_team_is_refused_by_name(league: TemplateContext) -> None:
    answer = with_without(league, _slots(team="Philadelphia 76ers", without="Tatum")).answer
    assert answer.startswith("Jayson Tatum never appeared in a box score for the Philadelphia 76ers")


def test_a_player_subject_gets_his_averages_in_each_group(league: TemplateContext) -> None:
    """Brown played every Celtics game with a box score. With Tatum: e1 20, e4
    10, e7 12. Without: e2 25, e3 18."""
    result = with_without(league, _slots(player="Jaylen Brown", without="Jayson Tatum"))
    groups = {g["teammate_played"]: g for g in result.data["groups"]}
    assert groups[True]["player_games"] == 3 and groups[True]["points"] == pytest.approx(14.0)
    assert groups[False]["player_games"] == 2 and groups[False]["points"] == pytest.approx(21.5)
    assert result.data["player"] == "Jaylen Brown"


def test_the_teammate_repeated_in_the_player_slot_is_not_a_subject(league: TemplateContext) -> None:
    result = with_without(league, _slots(team="Boston Celtics", player="Jayson Tatum", without="Tatum"))
    assert result.data["player"] is None and result.data["teammate"] == "Jayson Tatum"


def test_with_without_needs_a_teammate(league: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        with_without(league, _slots(team="Boston Celtics"))


def test_three_names_and_no_without_is_not_guessed(league: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        with_without(league, _slots(team="Boston Celtics", players=["Jayson Tatum", "Jaylen Brown", "Journeyman Guy"]))


# ---------------- record_when ----------------


def test_record_when_divides_his_games_by_the_threshold(league: TemplateContext) -> None:
    """Tatum this season: e1 30 W, e4 35 W, e7 31 L."""
    result = record_when(league, _slots(player="Jayson Tatum", stat="points", threshold=31))
    assert (result.data["reached"]["wins"], result.data["reached"]["losses"]) == (1, 1)
    assert (result.data["fell_short"]["wins"], result.data["fell_short"]["losses"]) == (1, 0)
    assert result.answer.startswith(f"Boston Celtics record when Jayson Tatum had 31+ points, {S} regular season:")


def test_record_when_refuses_what_it_cannot_whitelist(league: TemplateContext) -> None:
    for slots in ({"stat": "double_double", "threshold": 1}, {"stat": "points"}, {"stat": "points", "threshold": 0}, {"stat": "points", "threshold": True}):
        with pytest.raises(TemplateUnsupported):
            record_when(league, _slots(player="Jayson Tatum", **slots))


# ---------------- player_matchup ----------------


def test_a_matchup_is_the_games_both_played_on_opposite_teams(league: TemplateContext) -> None:
    """e1 and e7. Not e2 (Tatum DNP), and not e5, where the Celtics' box score
    is missing - which is said."""
    result = player_matchup(league, _slots(players=["LeBron James", "Jayson Tatum"]))
    assert result.data["meetings"] == 2
    assert result.data["wins"] == {"LeBron James": 1, "Jayson Tatum": 1}
    assert result.data["averages"]["Jayson Tatum"]["points"] == pytest.approx(30.5)
    assert "1 game between their teams" in result.answer


def test_teammates_never_met_and_the_answer_says_why(league: TemplateContext) -> None:
    answer = player_matchup(league, _slots(players=["Jayson Tatum", "Jaylen Brown"])).answer
    assert answer.endswith("they were teammates in all 3 games they both played.")


def test_a_matchup_needs_two_different_players(league: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_matchup(league, _slots(players=["Jayson Tatum"]))
    with pytest.raises(TemplateUnsupported):
        player_matchup(league, _slots(players=["Jayson Tatum", "Tatum"]))


# ---------------- streak ----------------


def test_a_teams_streak_skips_a_game_that_has_no_result(league: TemplateContext) -> None:
    """e3, e4, e5: three wins with the placeholder e6 sitting between e3 and
    e4. Read as a loss, it would split the run in two."""
    result = streak(league, _slots(team="Boston Celtics", kind="win"))
    assert result.data["streaks"][0] == {"length": 3, "from": f"{S - 1}-11-04", "to": f"{S - 1}-11-08", "open": False}


def test_a_teams_streak_does_not_cross_seasons(league: TemplateContext) -> None:
    """Last season ends on three wins and this one opens with a fourth.
    Counted across the break that is a run of 4; the record book says 3."""
    result = streak(league, _slots(team="Boston Celtics", kind="win", span="career"))
    assert result.data["streaks"][0]["length"] == 3
    assert "counted within one season" in result.answer


def test_a_players_run_skips_missed_games_and_stops_at_unknown_ones(league: TemplateContext) -> None:
    """25+ points: e0c 25, e0a 30, e0d 28, e1 30, (e2 DNP, e3 no row), e4 35,
    then e5 with no box score, then e7 31. Missed games neither extend nor end
    the run; the unknown one ends it. A run of 5 across the season break."""
    result = streak(league, _slots(player="Jayson Tatum", stat="points", threshold=25, span="career"))
    assert result.data["streaks"][0]["length"] == 5
    assert "ends a run" in result.answer


def test_the_league_streak_reports_a_tie_as_a_tie(league: TemplateContext) -> None:
    """25+ points this season: Tatum e1, e4 (then e5 unknown); LeBron e1, e2
    (then 20 in e5)."""
    result = streak(league, _slots(stat="points", threshold=25))
    assert [s["length"] for s in result.data["streaks"][:2]] == [2, 2]
    assert result.answer.startswith("Jayson Tatum and LeBron James shared the longest")


def test_the_leagues_longest_winning_streak_names_the_team(league: TemplateContext) -> None:
    result = streak(league, _slots(kind="win"))
    assert result.answer.startswith(f"Boston Celtics had the longest winning streak of the {S} regular season: 3 games.")


def test_a_losing_streak_is_a_run_of_losses(league: TemplateContext) -> None:
    result = streak(league, _slots(team="Philadelphia 76ers", kind="loss"))
    assert result.data["streaks"][0]["length"] == 2


def test_a_stat_without_a_threshold_is_not_read_as_a_winning_streak(league: TemplateContext) -> None:
    """ "Most consecutive double-doubles" must not come back as the Celtics'
    best run of wins."""
    for slots in ({"stat": "double_double"}, {"stat": "points"}, {"threshold": 30}, {"stat": "points", "threshold": 0}):
        with pytest.raises(TemplateUnsupported):
            streak(league, _slots(team="Boston Celtics", **slots))
    with pytest.raises(TemplateUnsupported):
        streak(league, _slots(team="Boston Celtics", stat="points", threshold=30))


def test_the_models_word_for_a_winning_streak_is_not_a_stat(league: TemplateContext) -> None:
    assert streak(league, _slots(team="Boston Celtics", stat="wins", kind="win")).data["streaks"][0]["length"] == 3


# ---------------- what the checks around the templates see ----------------


def test_the_split_kinds_are_the_ones_the_router_reads() -> None:
    """Two hand-maintained lists of the same names - the shape that produced
    the player_compare bug."""
    from association.query.router import SPLIT_WORDS

    assert SPLIT_KINDS == tuple(SPLIT_WORDS)


def test_the_eastern_shift_matches_the_fetch_path() -> None:
    from association.fetch.parse import _EASTERN_OFFSET
    from association.query.conditions import _EASTERN_OFFSET_HOURS

    assert _EASTERN_OFFSET.total_seconds() == _EASTERN_OFFSET_HOURS * 3600


def test_a_teams_streak_or_split_is_floored_by_the_team_tables_alone() -> None:
    """Team games reach back to 1988 in the postseason, player box scores to
    1994. A 1990 playoff question about a team is answerable, and refusing it
    with a sentence about player box scores would name the wrong cause."""
    assert check_coverage("streak", {"season": 1990, "season_type": 3, "team": "Chicago Bulls"}) is None
    assert check_coverage("player_splits", {"season": 1990, "season_type": 3, "team": "Chicago Bulls"}) is None
    refused = check_coverage("streak", {"season": 1990, "season_type": 3, "player": "Michael Jordan", "stat": "points", "threshold": 30})
    assert refused is not None and refused.startswith("Player box scores")
    refused = check_coverage("with_without", {"season": 1990, "season_type": 3, "team": "Chicago Bulls", "without": "Jordan"})
    assert refused is not None and refused.startswith("Player box scores")


def test_a_span_of_seasons_starts_where_seasons_are_named_for_their_end() -> None:
    """The team tables' postseason floor is 1988, but ESPN files 1988-1993 under
    the year each season began, so a span of seasons starts at 1994 and never
    counts the phantom 1993."""
    from association.query.conditions import _TEAM_GAME_TABLES, _game_scope

    scope = _game_scope(None, 3, _TEAM_GAME_TABLES)
    assert scope.first == 1994 and "NOT IN (1993)" in scope.where("t")


@pytest.mark.parametrize("season", [1990, 1993])
def test_a_playoff_season_filed_under_its_first_year_is_refused(league: TemplateContext, season: int) -> None:
    """The warehouse's "1990" postseason is April to June 1991. Answered as
    asked, "Bulls 1990 playoffs" describes the wrong year under the right label."""
    for template, slots in ((streak, {"team": "Boston Celtics", "kind": "win"}), (streak, {"kind": "win"}), (player_splits, {"team": "Boston Celtics"})):
        answer = template(league, {**slots, "season": season, "season_type": 3}).answer
        assert f"its {season} postseason is the {season + 1} playoffs" in answer


def test_a_player_listed_once_is_told_so_in_the_singular(league: TemplateContext) -> None:
    league.con.execute("DELETE FROM player_box_stats WHERE athlete_id = ? AND season = ? AND event_id <> 'e2'", [TATUM, S])
    assert player_splits(league, _slots(player="Jayson Tatum")).answer.endswith("box score in the 2026 regular season but did not play in it.".replace("2026", str(S)))
