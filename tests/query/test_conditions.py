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

from association.fetch.repairs import real_games
from association.fetch.repairs.reconstructed_box import _FILLED_COLUMNS as FILLED_COLUMNS
from association.nba.season import current_season
from association.query.conditions import RAW_BOX, UNGATED_ON_REBUILD, box_source
from association.query.templates.common import REBUILT_STATS, TemplateContext, TemplateResult, TemplateUnsupported, check_coverage
from association.query.templates.games import player_matchup
from association.query.templates.splits import SPLIT_KINDS, player_splits, record_when, streak, with_without

S = current_season()
BOS, LAL, PHI = "2", "13", "20"
TATUM, BROWN, LEBRON, EMBIID, JOURNEYMAN = "10", "11", "20", "30", "40"
# Postseason-only, for `game_n`: kept off Tatum/Brown/Embiid so a series added
# there does not stretch a stint `with_without`'s own tests assert an exact
# end date for (test_a_game_before_he_arrived_is_not_a_game_without_him).
PLAYOFF_GUY = "90"

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
    season_type: int = 2,
    neutral_site: bool = False,
    venue_city: str = "Boston",
) -> None:
    decided = (home if home_score > away_score else away) if winner == "auto" else winner
    c.execute("INSERT INTO games VALUES (?,?,?,?,?,?,?,?,?,?,?)", [event, season, season_type, date, home, away, home_score, away_score, decided, neutral_site, venue_city])
    for team, opponent, side in ((home, away, "home"), (away, home, "away")):
        # totalRebounds (40) is deliberately NOT offensiveRebounds +
        # defensiveRebounds (12 + 23 = 35) - the way a real pre-2022 row
        # differs, with a few rebounds ESPN credits to no player. Reading the
        # stale column would show 40.0; see test_conditions.py's rebounds test.
        stats = [40, 12, 23, 20, 10, 40, 85] if team_box else [None] * 7
        c.execute("INSERT INTO team_box_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [event, season, season_type, team, opponent, side, *stats])
    for athlete, team, minutes, points, starter, dnp in lines:
        opponent = away if team == home else home
        c.execute(
            "INSERT INTO player_box_stats VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                event,
                season,
                season_type,
                team,
                opponent,
                athlete,
                starter,
                dnp,
                minutes,
                points,
                0 if minutes is None else 5,
                0 if minutes is None else 3,
                0,
                0,
                0,
                0 if minutes is None else 1,
                points // 2,
                points,
                0,
                0,
            ],
        )


@pytest.fixture
def league(tmp_path: Path) -> TemplateContext:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season BIGINT, season_type BIGINT, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score BIGINT, away_score BIGINT, winner_team_id VARCHAR, neutral_site BOOLEAN, venue_city VARCHAR)"
    )
    c.execute(
        "CREATE TABLE team_box_stats (event_id VARCHAR, season BIGINT, season_type BIGINT, team_id VARCHAR, opponent_team_id VARCHAR, home_away VARCHAR, "
        "totalRebounds BIGINT, offensiveRebounds BIGINT, defensiveRebounds BIGINT, assists BIGINT, threePointFieldGoalsMade BIGINT, fieldGoalsMade BIGINT, fieldGoalsAttempted BIGINT)"
    )
    c.execute(
        "CREATE TABLE player_box_stats (event_id VARCHAR, season BIGINT, season_type BIGINT, team_id VARCHAR, opponent_team_id VARCHAR, athlete_id VARCHAR, "
        "starter BOOLEAN, did_not_play BOOLEAN, minutes BIGINT, points BIGINT, rebounds BIGINT, assists BIGINT, steals BIGINT, blocks BIGINT, turnovers BIGINT, "
        "threePointFieldGoalsMade BIGINT, fieldGoalsMade BIGINT, fieldGoalsAttempted BIGINT, freeThrowsMade BIGINT, fouls BIGINT)"
    )
    c.execute(
        "INSERT INTO players VALUES (?, 'Jayson Tatum'), (?, 'Jaylen Brown'), (?, 'LeBron James'), (?, 'Joel Embiid'), (?, 'Journeyman Guy'), (?, 'Playoff Guy')",
        [TATUM, BROWN, LEBRON, EMBIID, JOURNEYMAN, PLAYOFF_GUY],
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
    # A two-game postseason series vs Philadelphia, for `game_n`: p1 first by
    # date, p2 second - numbered over real_games, not insertion order.
    _game(c, "p1", f"{S}-04-20T23:30Z", BOS, PHI, 100, 90, [_played(PLAYOFF_GUY, BOS, 22), _played(EMBIID, PHI, 18)], season_type=3)
    _game(c, "p2", f"{S}-04-23T23:30Z", PHI, BOS, 95, 105, [_played(PLAYOFF_GUY, BOS, 26), _played(EMBIID, PHI, 20)], season_type=3)
    # The shared filtered list every query here reads; e6 is dropped by it.
    real_games.build_table(c, {"games", "teams", "player_box_stats"})
    # For `season_n`: Tatum's two regular seasons on record, last season and
    # this one - settle_ordinal_season (common.py) reads only these columns.
    c.execute("CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER)")
    c.execute("INSERT INTO player_season_stats_deduped VALUES (?, ?, 2), (?, ?, 2)", [TATUM, last, TATUM, S])
    # The log view the player-games relation reads, in the warehouse's shape.
    c.execute(
        "CREATE VIEW player_game_log AS SELECT pbs.*, p.display_name AS player_name, g.date AS game_date, t.abbreviation AS team_abbr, o.abbreviation AS opponent_abbr "
        "FROM player_box_stats pbs LEFT JOIN players p ON p.athlete_id = pbs.athlete_id LEFT JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season "
        "LEFT JOIN teams t ON t.team_id = pbs.team_id LEFT JOIN teams o ON o.team_id = pbs.opponent_team_id"
    )
    return TemplateContext(con=c, out_dir=tmp_path)


def _slots(**given: Any) -> dict[str, Any]:
    return {"season_type": 2, **given}


def _rows(result: TemplateResult, split: str) -> dict[str, dict[str, Any]]:
    return {row["group"]: row for row in result.data["splits"][split]}


# ---------------- games rebuilt from play-by-play ----------------


@pytest.fixture
def rebuilt_league(league: TemplateContext) -> TemplateContext:
    """The same league, with e5 rebuilt from its play-by-play.

    e5 is already the shape ESPN really serves for every Chicago and New
    Orleans game from 2013 to 2018: both Celtics rows present, not flagged DNP,
    with NULL minutes and zeros. `player_box_stats_filled` substitutes figures
    rebuilt from the plays and flags exactly those rows, and a rebuilt row
    still has no minutes - play-by-play cannot recover them.

    Tatum and Brown both played e5. Their rebuilt lines carry points, rebounds
    and assists (which a rebuild gets right) and also turnovers, 3-pointers and
    field-goal attempts (which it does not, and which nothing may read).
    """
    c = league.con
    rebuilt = "(pbs.event_id = 'e5' AND pbs.team_id = '2')"
    c.execute(f"""
        CREATE VIEW player_box_stats_filled AS
        SELECT pbs.* REPLACE (
                 CASE WHEN {rebuilt} THEN 26 ELSE pbs.points END AS points,
                 CASE WHEN {rebuilt} THEN 7 ELSE pbs.rebounds END AS rebounds,
                 CASE WHEN {rebuilt} THEN 4 ELSE pbs.assists END AS assists,
                 CASE WHEN {rebuilt} THEN 9 ELSE pbs.turnovers END AS turnovers,
                 CASE WHEN {rebuilt} THEN 9 ELSE pbs.threePointFieldGoalsMade END AS threePointFieldGoalsMade,
                 CASE WHEN {rebuilt} THEN 13 ELSE pbs.fieldGoalsMade END AS fieldGoalsMade,
                 CASE WHEN {rebuilt} THEN 99 ELSE pbs.fieldGoalsAttempted END AS fieldGoalsAttempted),
               {rebuilt} AS reconstructed
        FROM player_box_stats pbs""")
    # The log view the relation reads is built over the FILLED table in the
    # warehouse (fetch/warehouse.py picks player_box_stats_filled when it exists),
    # so this fixture's must be too, or a rebuilt row is invisible to the read.
    c.execute("DROP VIEW player_game_log")
    c.execute(
        "CREATE VIEW player_game_log AS SELECT pbs.*, p.display_name AS player_name, g.date AS game_date, t.abbreviation AS team_abbr, o.abbreviation AS opponent_abbr "
        "FROM player_box_stats_filled pbs LEFT JOIN players p ON p.athlete_id = pbs.athlete_id LEFT JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season "
        "LEFT JOIN teams t ON t.team_id = pbs.team_id LEFT JOIN teams o ON o.team_id = pbs.opponent_team_id"
    )
    return league


def test_a_rebuilt_game_counts_as_a_game_he_played(rebuilt_league: TemplateContext) -> None:
    """The P1 this fixes. A rebuilt line has no minutes, so before the source
    was resolved every such game read as one he missed - and a season with
    nothing but rebuilt games answered "was listed in N box scores but did not
    play in any of them". Tatum played e1, e4, e7 and now e5."""
    result = player_splits(rebuilt_league, _slots(player="Jayson Tatum", split="home_away"))
    assert result.data["games"] == 4
    assert "did not play in any of them" not in (result.answer or "")


def test_a_rebuilt_game_is_no_longer_an_unknown_game(rebuilt_league: TemplateContext) -> None:
    """`_box_missing` has to widen with `_played`, or the same answer both
    counts a game and reports it as one with no box score - the contradiction
    `_empty_box_scores(covered_by_rebuild=...)` exists to stop elsewhere."""
    answer = player_splits(rebuilt_league, _slots(player="Jayson Tatum", split="home_away")).answer or ""
    assert "no box score" not in answer


def test_a_figure_the_rebuild_gets_wrong_is_left_out_rather_than_averaged_in(rebuilt_league: TemplateContext) -> None:
    """The filled view substitutes more columns than the rebuild was measured
    for. Tatum's two home games are e1 (played: 30 points, 0 turnovers, 1
    three, 30 minutes) and e5 (rebuilt: 26 points, and a deliberately absurd 9
    turnovers and 9 threes).

    Points are inside `REBUILT_STATS`, so both games count. Turnovers and
    threes are not, so the average is taken over the game that carries them -
    the failure this rules out is the quiet one, where 9 is averaged in rather
    than a wrong number appearing on its own."""
    rows = _rows(player_splits(rebuilt_league, _slots(player="Jayson Tatum", split="home_away")), "home_away")
    home = rows["home"]
    assert home["games"] == 2
    assert home["points"] == pytest.approx(28.0), "the rebuilt 26 IS read"
    assert home["turnovers"] == pytest.approx(0.0), "e1 alone; averaging the rebuilt 9 in would give 4.5"
    assert home["threes"] == pytest.approx(1.0), "e1 alone; averaging the rebuilt 9 in would give 5.0"
    assert home["minutes"] == pytest.approx(30.0), "e1 alone; a rebuilt game has no minutes to count as zero"


def test_a_teammate_in_a_rebuilt_game_is_not_counted_as_absent(rebuilt_league: TemplateContext) -> None:
    """The other half of the P1: `_with_without_games` asked for minutes too,
    so a teammate who played a rebuilt game read as out and the game was
    counted on the "without" side."""
    result = with_without(rebuilt_league, _slots(team="Boston Celtics", without="Jayson Tatum"))
    played = next(row for row in result.data["groups"] if row["teammate_played"])
    assert played["games"] == 4, "e5 is a game Tatum played, not one he missed"


def test_the_subjects_own_average_counts_his_rebuilt_game(rebuilt_league: TemplateContext) -> None:
    """The Python half of the same fault, and the one no SQL guard covers:
    `_with_without_group` picks his games out of the group in Python, and did
    it with `minutes is not None` - so a rebuilt game passed every query-side
    check and was dropped again on the way to the average.

    Brown played all four of Tatum's games: e1 20, e4 10, e7 12, and e5
    rebuilt at 26. Reading minutes as the proxy drops e5 and answers 3 games
    at 14.0 - which is exactly
    `test_a_player_subject_gets_his_averages_in_each_group` on the un-rebuilt
    fixture, and the reason this needs a test of its own."""
    result = with_without(rebuilt_league, _slots(player="Jaylen Brown", without="Jayson Tatum"))
    groups = {g["teammate_played"]: g for g in result.data["groups"]}
    assert groups[True]["player_games"] == 4, "e5 is his game too"
    assert groups[True]["points"] == pytest.approx(17.0), "68/4; dropping the rebuilt 26 gives 14.0"
    assert groups[True]["minutes"] == pytest.approx(30.0), "the three games that carry minutes, not 22.5 over four"


def test_a_warehouse_without_the_filled_view_reads_as_it_always_did(league: TemplateContext) -> None:
    """The view arrives with a `data load`. An older warehouse has none, and
    every fixture here builds only `player_box_stats` - a query written as
    though the view were always there is a Binder error, not a value change.
    Same escape as `_log_carries_rebuilt`."""
    assert box_source(league.con) == RAW_BOX
    assert player_splits(league, _slots(player="Jayson Tatum", split="home_away")).data["games"] == 3


def test_the_ungated_rebuild_columns_are_the_ones_no_template_may_read() -> None:
    """`UNGATED_ON_REBUILD` is a third hand-maintained list beside
    `REBUILT_STATS` and the view's own substitutions, which is the shape this
    project keeps getting bitten by. Derive it and compare."""
    substituted = {warehouse_column for warehouse_column, _ in FILLED_COLUMNS}
    assert set(UNGATED_ON_REBUILD) == substituted - set(REBUILT_STATS)


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


def test_a_team_splits_rebounds_are_offensive_plus_defensive_not_the_raw_total(league: TemplateContext) -> None:
    """`totalRebounds` stops meaning the same thing across 2021/2022 - DATA.md,
    "The team `totalRebounds` column stops including team rebounds in 2022".
    `_TEAM_LINE` reads offensiveRebounds + defensiveRebounds instead, which is
    what ESPN's own totalRebounds equals in every season from 2022 on. The
    fixture's totalRebounds (40) deliberately differs from offensiveRebounds +
    defensiveRebounds (12 + 23 = 35) the way a real pre-2022 row does, so
    reading the wrong column would show 40.0 here instead."""
    rows = _rows(player_splits(league, _slots(team="Boston Celtics", split="wins_losses")), "wins_losses")
    assert rows["wins"]["rebounds"] == pytest.approx(35.0)
    assert rows["losses"]["rebounds"] == pytest.approx(35.0)


def test_a_team_has_no_starter_split(league: TemplateContext) -> None:
    with pytest.raises(TemplateUnsupported):
        player_splits(league, _slots(team="Boston Celtics", split="starter_bench"))


def test_a_team_subject_refuses_without_rather_than_silently_dropping_it(league: TemplateContext) -> None:
    """`_player_splits_team` reads the team tables directly and has no
    teammate-absence filter (ISSUES.md, "player_splits cannot honor a
    teammate's absence ... when the subject is a team") - "Celtics splits
    without Tatum" used to answer the Celtics' whole season, identical to
    leaving `without` out and with nothing saying so. Refused now, the same
    way a starter/bench split already is for a team."""
    with pytest.raises(TemplateUnsupported, match="without"):
        player_splits(league, _slots(team="Boston Celtics", without="Jayson Tatum"))


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


def test_with_and_without_narrows_to_one_opponent_and_says_so(league: TemplateContext) -> None:
    """#163: "Embiid career record vs boston" is his team's record in the games
    it played BOSTON, and there was no way to ask it - `opponent` was honored
    by nothing here, so the question fell through.

    Against the Lakers the Celtics played e1 (W, Tatum), e2 (L, Tatum DNP) and
    e7 (L, Tatum); e5 has no box score and is on neither side. So with Tatum
    1-1 and without him 0-1, where the unnarrowed split is 2-1 and 1-1. The
    opponent is in the TITLE, because a record over one opponent's games headed
    as though it covered every game is the silent narrowing this module exists
    to stop."""
    result = with_without(league, _slots(team="Boston Celtics", without="Tatum", opponent="Lakers"))
    groups = {g["teammate_played"]: g for g in result.data["groups"]}
    assert (groups[True]["wins"], groups[True]["losses"]) == (1, 1)
    assert (groups[False]["wins"], groups[False]["losses"]) == (0, 1)
    assert "vs the Los Angeles Lakers" in result.answer
    # The other opponent is a different pool, not the same numbers.
    sixers = with_without(league, _slots(team="Boston Celtics", without="Tatum", opponent="76ers"))
    by_played = {g["teammate_played"]: g for g in sixers.data["groups"]}
    assert (by_played[True]["wins"], by_played[True]["losses"]) == (1, 0)
    assert (by_played[False]["wins"], by_played[False]["losses"]) == (1, 0)
    # And naming none is the whole season, exactly as before.
    every = with_without(league, _slots(team="Boston Celtics", without="Tatum"))
    assert "vs the" not in every.answer
    assert {g["teammate_played"]: (g["wins"], g["losses"]) for g in every.data["groups"]} == {True: (2, 1), False: (1, 1)}


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


# ---------------- record_when, the relation's cells (step 3, C2) ----------------
#
# Tatum's played games this season with a recorded box score: e1 (home, vs
# LAL, 30, W), e4 (away, vs PHI, off the bench, 35, W), e7 (away, vs LAL, 31,
# L) - e2 is a DNP, e3 has no row, e5's box score is blank.


def test_record_when_narrows_by_venue_and_says_so(league: TemplateContext) -> None:
    """Only e1 is a home game; e4 and e7 are on the road."""
    result = record_when(league, _slots(player="Jayson Tatum", stat="points", threshold=25, venue="home"))
    assert (result.data["reached"]["games"], result.data["fell_short"]["games"]) == (1, 0)
    assert result.answer.startswith(f"Boston Celtics record when Jayson Tatum had 25+ points at home, {S} regular season:")


def test_record_when_narrows_by_opponent_and_says_so(league: TemplateContext) -> None:
    """Tatum vs the 76ers, across both seasons on record: e0b (last season,
    20, L), e0c (last season, 25, W), e4 (this season, 35, W)."""
    result = record_when(league, _slots(player="Jayson Tatum", stat="points", threshold=25, opponent="Philadelphia 76ers", span="career"))
    assert (result.data["reached"]["games"], result.data["reached"]["wins"]) == (2, 2)
    assert (result.data["fell_short"]["games"], result.data["fell_short"]["losses"]) == (1, 1)
    assert "vs the Philadelphia 76ers" in result.answer


def test_record_when_narrows_by_a_teammates_absence_and_says_so(league: TemplateContext) -> None:
    """Brown's only box score in Tatum's rookie (last) season is e0z, a game
    Tatum is not even in - so every one of Tatum's last-season games is
    "without" him: e0b (20, L), e0c (25, W), e0a (30, W), e0d (28, W)."""
    result = record_when(league, _slots(player="Jayson Tatum", stat="points", threshold=28, without="Jaylen Brown", season=S - 1))
    assert (result.data["reached"]["games"], result.data["reached"]["wins"], result.data["reached"]["losses"]) == (2, 2, 0)
    assert (result.data["fell_short"]["games"], result.data["fell_short"]["wins"], result.data["fell_short"]["losses"]) == (2, 1, 1)
    assert "without Jaylen Brown" in result.answer


def test_record_when_narrows_by_a_named_half_of_the_split_and_says_so(league: TemplateContext) -> None:
    """Tatum comes off the bench only once: e4, 35 points, a win."""
    result = record_when(league, _slots(player="Jayson Tatum", stat="points", threshold=30, split="bench"))
    assert (result.data["reached"]["games"], result.data["fell_short"]["games"]) == (1, 0)
    assert "off the bench" in result.answer


def test_record_when_narrows_by_one_game_of_a_playoff_series_and_says_so(league: TemplateContext) -> None:
    """p1 and p2 are a two-game series vs Philadelphia; game 2 is p2 (26 points, by date, not insertion order)."""
    result = record_when(league, _slots(player="Playoff Guy", stat="points", threshold=25, season_type=3, game_n=2))
    assert (result.data["reached"]["games"], result.data["fell_short"]["games"]) == (1, 0)
    assert "game 2 of each series" in result.answer


def test_record_when_honors_since_and_says_so(league: TemplateContext) -> None:
    """Since last season: every one of Tatum's played games on record, both
    seasons - e0b (20, L), e0c (25, W), e0a (30, W), e0d (28, W), e1 (30, W),
    e4 (35, W), e7 (31, W)."""
    result = record_when(league, _slots(player="Jayson Tatum", stat="points", threshold=25, since=S - 1))
    assert (result.data["reached"]["games"], result.data["fell_short"]["games"]) == (6, 1)
    assert result.answer.startswith(f"Boston Celtics record when Jayson Tatum had 25+ points, since {S - 1} ({S - 1}-{S} regular seasons):")


def test_record_when_settles_season_n_and_says_so(league: TemplateContext) -> None:
    """Tatum's 1st season on record (player_season_stats_deduped) is last
    season: e0b (20, L), e0c (25, W), e0a (30, W), e0d (28, W)."""
    result = record_when(league, _slots(player="Jayson Tatum", stat="points", threshold=25, season_n=1))
    assert result.data["span"].startswith(f"in his 1st season ({S - 1}")
    assert (result.data["reached"]["games"], result.data["fell_short"]["games"]) == (3, 1)
    assert "in his 1st season (" in result.answer


def test_record_when_narrows_by_a_box_score_line_and_says_so(league: TemplateContext) -> None:
    """Every played row in this fixture carries exactly 3 assists (`Line`'s own
    comment), so a line every game already satisfies changes no number but
    does say so in the title - proof the filter reaches the query rather than
    being silently ignored, without needing engineered variance."""
    with_line = record_when(league, _slots(player="Jayson Tatum", stat="points", threshold=25, above=["at least 3 assists"]))
    without_line = record_when(league, _slots(player="Jayson Tatum", stat="points", threshold=25))
    assert with_line.data["reached"] == without_line.data["reached"] and with_line.data["fell_short"] == without_line.data["fell_short"]
    assert "with at least 3 assists" in with_line.answer


def test_record_when_a_box_score_line_narrows_to_no_games(league: TemplateContext) -> None:
    """A line no played row meets admits none at all - the same fixture's
    fixed 3 assists, asked for under. Confirmed: the refusal this reaches is
    `common._no_games`'s box-score-count branch, which says "did not play in
    any of them" - true of games with no box score, false here, since Tatum
    played several; ISSUES.md records the finding."""
    empty = record_when(league, _slots(player="Jayson Tatum", stat="points", threshold=25, below=["under 3 assists"]))
    assert empty.data["games"] == 0
    assert "did not play in any of them" in empty.answer


def test_record_when_cannot_honor_a_relation_cell_without_a_player(league: TemplateContext) -> None:
    """The team branch settles no player, so it still cannot narrow by a
    teammate's absence, a box-score line or anything else only
    condition_player reads - refusing by name rather than silently answering
    the whole team's record. ``opponent``/``venue`` (step 3, C4) and
    ``since``/``game_n`` (step 3, C4b) are NOT in this list any more - see
    test_an_opponent_narrows_a_team_only_threshold_too,
    test_a_venue_narrows_a_team_only_threshold_too and
    test_a_team_only_threshold_honors_since below."""
    with pytest.raises(TemplateUnsupported, match=r"record_when cannot honor \['below'\]"):
        record_when(league, _slots(team="Boston Celtics", stat="points", threshold=100, below=["under 3 assists"]))


def test_a_team_only_threshold_honors_since(league: TemplateContext) -> None:
    """``since`` (step 3, C4b): a team-only record now spans more than one
    season instead of refusing - the fixture's Celtics have real games in
    both `last` (e0z-e0d) and this season (e1-e7), and ``since=last`` reaches
    both. The exact tally is covered by the dedicated ``since`` tests in
    ``tests/query/test_team_templates.py``; this just confirms the shape
    answers rather than refuses, and says the span in the heading."""
    result = record_when(league, _slots(team="Boston Celtics", stat="points", threshold=100, since=S - 1))
    assert result.data["team"] == "Boston Celtics"
    assert result.data["reached"]["games"] + result.data["fell_short"]["games"] > 4
    assert f"since {S - 1}" in (result.answer or "")


# ---------------- record_when, the team branch (ISSUES.md #144) ----------------
#
# The Celtics' six real games this season (e6 is a 0-0 placeholder with no
# winner, dropped by real_games): e1 110 W, e2 100 L, e3 120 W, e4 99 W,
# e5 101 W (team_box_stats NULL for both sides), e7 105 L. 4-2 overall.


def test_a_team_only_threshold_divides_the_teams_own_games(league: TemplateContext) -> None:
    """No player named at all - "what was the celtics record when they scored
    120 points" (ISSUES.md #144). Reached (>=105): e1 110 W, e3 120 W, e7 105 L
    (2-1). Fell short (<105): e2 100 L, e4 99 W, e5 101 W (2-1) - e5 included,
    since points reads the game's own score and needs no team_box_stats row."""
    result = record_when(league, _slots(team="Boston Celtics", stat="points", threshold=105))
    assert (result.data["reached"]["wins"], result.data["reached"]["losses"]) == (2, 1)
    assert (result.data["fell_short"]["wins"], result.data["fell_short"]["losses"]) == (2, 1)
    assert result.answer.startswith(f"Boston Celtics record when they had 105+ points, {S} regular season:")
    assert "player" not in result.data


def test_a_team_only_threshold_needs_no_player(league: TemplateContext) -> None:
    """The router's own recorded output for this shape carries no `player`
    slot at all (tests/query/test_router.py,
    test_a_record_when_about_a_team_gains_no_player) - confirming record_when
    itself accepts that shape rather than raising "record_when needs a
    player", the wrong cause for a question that never named anybody."""
    result = record_when(league, _slots(team="Boston Celtics", stat="points", threshold=100))
    assert result.data["team"] == "Boston Celtics"


def test_a_bare_threshold_names_the_real_missing_thing(league: TemplateContext) -> None:
    """Neither a player nor a team - unanswerable, and the refusal says so
    rather than naming only the player half (the mirror-image bug AGENTS.md
    warns about: a wrong cause reads as honest)."""
    with pytest.raises(TemplateUnsupported, match="record_when needs a player or a team"):
        record_when(league, _slots(stat="points", threshold=100))


def test_a_team_rebounds_threshold_reads_oreb_plus_dreb_not_totalrebounds(league: TemplateContext) -> None:
    """The fixture's totalRebounds (40) is deliberately not
    offensiveRebounds + defensiveRebounds (35) - see `_game`'s own comment.
    36 is between them: reading the stale totalRebounds column would put
    every game in the reached bucket; reading oreb+dreb (what _TEAM_LINE
    already trusts, AGENTS.md) puts every game in fell_short instead."""
    result = record_when(league, _slots(team="Boston Celtics", stat="rebounds", threshold=36))
    assert result.data["reached"]["games"] == 0
    assert result.data["fell_short"]["games"] == 5, "the 5 games with a team_box_stats row; e5's is NULL"


def test_a_team_non_points_threshold_excludes_the_empty_box_game(league: TemplateContext) -> None:
    """e5's team_box_stats row is NULL for both sides (the 2013-2018
    Chicago/New Orleans shape, AGENTS.md "Whole team-seasons of box scores
    are empty") - unlike `points`, `assists` cannot read it, so it is in
    neither row and the answer says one game is missing."""
    result = record_when(league, _slots(team="Boston Celtics", stat="assists", threshold=15))
    assert result.data["reached"]["games"] == 5
    assert result.data["fell_short"]["games"] == 0
    assert "1 of their games in that span have no assists figure on record" in result.answer


def test_a_team_threshold_refuses_a_stat_with_no_team_figure(league: TemplateContext) -> None:
    """minutes is a real record_when stat for a PLAYER, but a team has no
    minutes total - the refusal names that, not "record_when needs a
    player" (the wrong cause: a team WAS named)."""
    with pytest.raises(TemplateUnsupported, match="record_when has no team figure for minutes"):
        record_when(league, _slots(team="Boston Celtics", stat="minutes", threshold=240))


def test_an_opponent_narrows_a_team_only_threshold_too(league: TemplateContext) -> None:
    """Step 3, C4: the Celtics' four games against the Lakers this season are
    e1 110 W (home), e2 100 L (away), e5 101 W (home), e7 105 L (away).
    Narrowed to LAL and a 105-point threshold: reached (>=105) is e1 (W) and
    e7 (L), 1-1; fell short is e2 (L) and e5 (W), 1-1 - the other two games
    (both vs Philadelphia) excluded from both rows."""
    result = record_when(league, _slots(team="Boston Celtics", stat="points", threshold=105, opponent="Los Angeles Lakers"))
    assert (result.data["reached"]["wins"], result.data["reached"]["losses"]) == (1, 1)
    assert (result.data["fell_short"]["wins"], result.data["fell_short"]["losses"]) == (1, 1)
    assert result.data["reached"]["games"] == 2 and result.data["fell_short"]["games"] == 2
    assert "vs the Los Angeles Lakers" in result.answer


def test_a_venue_narrows_a_team_only_threshold_too(league: TemplateContext) -> None:
    """The Celtics' three home games this season are e1 110 W, e3 120 W, e5
    101 W - all wins, so at a 105-point threshold reached is 2-0 (e1, e3) and
    fell short is 1-0 (e5), with none of their three road games counted."""
    result = record_when(league, _slots(team="Boston Celtics", stat="points", threshold=105, venue="home"))
    assert (result.data["reached"]["games"], result.data["reached"]["wins"]) == (2, 2)
    assert (result.data["fell_short"]["games"], result.data["fell_short"]["wins"]) == (1, 1)
    assert "at home" in result.answer


def test_a_team_only_threshold_says_which_fact_is_missing_for_a_narrowing_with_no_games(old_postseason_and_cup_final: TemplateContext) -> None:
    """``old1`` (1991 postseason) is the Celtics' only playoff game on record
    for that span, and it was against the Lakers - narrowed to the 76ers
    instead, the pool is empty, and which fact is missing is the MATCH, not
    the span: "played 1 games ... none of them vs the Philadelphia 76ers",
    not the false "no games in the 1991 postseason" (`_condition_team_no_games`,
    step 3, C4)."""
    result = record_when(old_postseason_and_cup_final, _slots(team="Boston Celtics", stat="points", threshold=10, season=1991, season_type=3, opponent="Philadelphia 76ers"))
    assert result.data["games"] == 0
    assert "played 1 game" in result.answer and "none of them vs the Philadelphia 76ers" in result.answer
    assert "no games with a result" not in result.answer


def test_a_streaks_opponent_and_venue_narrow_a_teams_own_run_too(league: TemplateContext) -> None:
    """Step 3, C4: narrowed to home games only, the Celtics won all three
    (e1, e3, e5) - their whole home slate, in order - so the streak is 3, not
    the 1-game runs their overall 4-2 record (interrupted by two road losses)
    would otherwise show at this same threshold-free "wins" question."""
    home = streak(league, _slots(team="Boston Celtics", kind="win", venue="home"))
    assert home.data["streaks"][0]["length"] == 3
    assert "at home" in (home.answer or "")
    against_phi = streak(league, _slots(team="Boston Celtics", kind="win", opponent="Philadelphia 76ers"))
    assert against_phi.data["streaks"][0]["length"] == 2  # e3, e4 - both wins
    assert "vs the Philadelphia 76ers" in (against_phi.answer or "")


def test_a_league_wide_streak_still_refuses_venue_and_opponent(league: TemplateContext) -> None:
    """Unlike a named team (just above), the league-wide streak (nobody
    named at all) has no single team's rival or home/road split to read -
    `_streak_league_needs_named_subject` refuses by name rather than
    silently narrowing nothing or picking one team to mean."""
    with pytest.raises(TemplateUnsupported, match=r"streak cannot honor \['venue'\] without a named team or player"):
        streak(league, _slots(kind="win", venue="home"))
    with pytest.raises(TemplateUnsupported, match=r"streak cannot honor \['opponent'\] without a named team or player"):
        streak(league, _slots(kind="win", opponent="Boston Celtics"))


def test_a_team_turnovers_threshold_reads_totalturnovers() -> None:
    """DATA.md ("The team box `turnovers` column is zero before 2013")
    establishes `totalTurnovers` as ESPN's right team-turnover figure in
    every era, and the bare `turnovers` column as a different number (the
    player-box sum, repaired in at load time) - not a stricter reading of
    the same fact. record_when's team branch reads the former."""
    from association.query.templates.splits import _RECORD_WHEN_TEAM_STAT_COLUMNS, _record_when_team_stat

    assert _RECORD_WHEN_TEAM_STAT_COLUMNS["turnovers"] == "tbs.totalTurnovers"
    assert _record_when_team_stat("turnovers", 15) == ("tbs.totalTurnovers", 15)


def test_a_named_player_beats_the_team_branch_end_to_end(league: TemplateContext) -> None:
    """The two branches meeting, which is what the pair of changes could break.
    A question naming a player is about HIM even where the subject grammar does
    not fire, and one naming nobody is about the team. Run through
    `scope_from_question` exactly as the pipeline runs it."""
    from association.query.entities import scope_from_question
    from association.query.templates.common import PLAYER_INTENTS, PLAYER_REQUIRED_INTENTS

    def answered(question: str, **slots: Any) -> str:
        given = _slots(**slots)
        scope_from_question(league.con, question, given, reads_player="record_when" in PLAYER_INTENTS, needs_player="record_when" in PLAYER_REQUIRED_INTENTS)
        return (record_when(league, given).answer or "").splitlines()[0]

    named = answered("celtics record with 20+ points from jayson tatum", stat="points", threshold=20, team="Boston Celtics")
    assert "Jayson Tatum had 20+ points" in named
    team = answered("celtics record when they scored 100 points", stat="points", threshold=100, team="Boston Celtics")
    assert "when they had 100+ points" in team


def test_record_when_still_restores_a_player_the_question_names() -> None:
    """`record_when` stays in PLAYER_REQUIRED_INTENTS even with a team branch,
    and the team branch is why it is safe rather than why it should leave.

    Dropping it was measured and reverted. `_scope_from_question_restore_player`
    restores only where the question names EXACTLY ONE player, so "what was the
    celtics record when they scored 120 points" - which names none - reaches the
    team branch either way. What the removal cost was the other side: "76ers
    record with 20+ points from tyrese maxey" names him plainly, the subject
    grammar does not fire on that wording, and without the restore it was
    answered "Philadelphia 76ers record when THEY had 20+ points" - a fluent
    answer to a different question, which is the failure shape this project
    keeps producing."""
    from association.query.templates.common import PLAYER_REQUIRED_INTENTS

    assert "record_when" in PLAYER_REQUIRED_INTENTS


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


def test_a_matchup_since_a_season_reaches_back_that_far_and_no_further(league: TemplateContext) -> None:
    """``since`` is a scope on the relation: every season from the one named,
    where the default is this season alone. LeBron and Tatum met twice last
    season and twice this one with both playing."""
    this_season = player_matchup(league, _slots(players=["LeBron James", "Jayson Tatum"])).data["meetings"]
    since_last = player_matchup(league, _slots(players=["LeBron James", "Jayson Tatum"], since=S - 1)).data["meetings"]
    assert (this_season, since_last) == (2, 4)


def test_a_log_since_a_season_reaches_back_that_far(league: TemplateContext) -> None:
    from association.query.templates import game_log

    this_season = game_log(league, _slots(player="Jayson Tatum", limit=50)).data["games"]
    since_last = game_log(league, _slots(player="Jayson Tatum", limit=50, since=S - 1)).data["games"]
    assert len(since_last) > len(this_season)
    assert {g["season"] for g in since_last} == {S - 1, S}


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


# ---------------- streak, the relation's cells (step 3, C2) ----------------
#
# The same career run as test_a_players_run_skips_missed_games_and_stops_at_unknown_ones
# (25+ points: e0c, e0a, e0d, e1, e4 - a run of 5, broken by e5's missing box
# score before e7) is the baseline these narrow further.


def test_streak_narrows_by_venue_and_says_so(league: TemplateContext) -> None:
    """Tatum's home games only: e0c, e0a, e0d, e1 - e4 and e7 are on the road,
    and dropping them shortens the career run below the unnarrowed 5."""
    result = streak(league, _slots(player="Jayson Tatum", stat="points", threshold=25, span="career", venue="home"))
    assert result.data["streaks"][0]["length"] == 4
    assert "at home" in result.answer


def test_streak_narrows_by_opponent_and_says_so(league: TemplateContext) -> None:
    """Tatum vs the Lakers only: e0a, e0d, e1 (25+ each) then e7, with e5 -
    a missing box score, unaffected by the opponent narrowing since the
    "unseen" read is not filtered by it - sitting between e1 and e7 and
    ending the run there. A run of 3, not the unnarrowed 5: dropping e4 (vs
    Philadelphia) from the sequence shortens it further still."""
    result = streak(league, _slots(player="Jayson Tatum", stat="points", threshold=25, span="career", opponent="Los Angeles Lakers"))
    assert result.data["streaks"][0]["length"] == 3
    assert "vs the Los Angeles Lakers" in result.answer


def test_streak_narrows_by_a_teammates_absence_and_says_so(league: TemplateContext) -> None:
    """Brown's only last-season box score is a game Tatum is not even in, so
    every one of Tatum's last-season games is "without" him - the Celtics'
    longest winning run in games Tatum played that season, without Brown, is
    e0c/e0a/e0d (e0b is a loss first)."""
    result = streak(league, _slots(player="Jayson Tatum", kind="win", without="Jaylen Brown", season=S - 1))
    assert result.data["streaks"][0]["length"] == 3
    assert "without Jaylen Brown" in result.answer


def test_streak_narrows_by_a_named_half_of_the_split_and_says_so(league: TemplateContext) -> None:
    """Tatum's only bench game is e4, a win - a run of exactly one."""
    result = streak(league, _slots(player="Jayson Tatum", kind="win", split="bench"))
    assert result.data["streaks"][0]["length"] == 1
    assert "off the bench" in result.answer


def test_streak_honors_since_and_says_so(league: TemplateContext) -> None:
    """Since this season alone (S), Tatum's recorded-points games are e1, e4,
    e7 - e5 sits between e4 and e7 with no box score, so it is a run of 2
    (e1, e4), not 3."""
    result = streak(league, _slots(player="Jayson Tatum", stat="points", threshold=25, since=S))
    assert result.data["streaks"][0]["length"] == 2
    assert result.answer.startswith(f"Jayson Tatum's longest run of consecutive games with 25+ points, since {S} ({S} regular season):")


def test_streak_settles_season_n_and_says_so(league: TemplateContext) -> None:
    """His 2nd season on record is this one (S) - the same games as the
    `since` case above, addressed by ordinal instead."""
    result = streak(league, _slots(player="Jayson Tatum", stat="points", threshold=25, season_n=2))
    assert result.data["streaks"][0]["length"] == 2
    assert "in his 2nd season (" in result.answer


def test_streak_narrows_by_one_game_of_a_playoff_series_and_says_so(league: TemplateContext) -> None:
    """Playoff Guy's game 2 of his one series is p2 alone - a run of exactly
    one game either way, but scoped to that game and said so."""
    result = streak(league, _slots(player="Playoff Guy", stat="points", threshold=25, season_type=3, game_n=2))
    assert result.data["streaks"][0]["length"] == 1
    assert "game 2 of each series" in result.answer


def test_streak_narrows_by_a_box_score_line_and_says_so(league: TemplateContext) -> None:
    """Every played row in this fixture carries exactly 3 assists, so a line
    every game already satisfies changes no run but does say so."""
    with_line = streak(league, _slots(player="Jayson Tatum", stat="points", threshold=25, span="career", above=["at least 3 assists"]))
    without_line = streak(league, _slots(player="Jayson Tatum", stat="points", threshold=25, span="career"))
    assert with_line.data["streaks"][0]["length"] == without_line.data["streaks"][0]["length"]
    assert "with at least 3 assists" in with_line.answer


def test_streak_cannot_honor_a_relation_cell_without_a_player(league: TemplateContext) -> None:
    """Team and league streaks settle no player, so neither can narrow by an
    ordinal season - only condition_player reads that, and neither branch
    calls it. ``since`` is NOT in this list any more (step 3, C4b) - see
    test_a_team_streak_honors_since below."""
    with pytest.raises(TemplateUnsupported, match=r"streak cannot honor \['season_n'\]"):
        streak(league, _slots(kind="win", season_n=1))


def test_a_team_streak_honors_since(league: TemplateContext) -> None:
    """``since`` (step 3, C4b): searched across every season from it on
    rather than only the current one - not across the boundary between them,
    since a team's own run is still counted within one season
    (``_longest_runs``' own ``("team_id", "season")`` partition, unchanged by
    this). The fixture's Celtics have a 3-game win streak in BOTH `last`
    (e0c, e0a, e0d) and this season (e3, e4, e5); with `since` bounded to the
    current season alone (the default), only the second would be found. The
    exact tally is covered by the dedicated ``since`` tests in
    ``tests/query/test_team_templates.py``; this confirms the shape answers
    rather than refuses, over the wider span."""
    result = streak(league, _slots(team="Boston Celtics", kind="win", since=S - 1))
    assert result.data["streaks"]
    assert result.data["streaks"][0]["length"] == 3
    assert f"since {S - 1}" in (result.answer or "")


# ---------------- what the checks around the templates see ----------------


def test_the_split_kinds_are_the_ones_the_router_reads() -> None:
    """Two hand-maintained lists of the same names - the shape that produced
    the player_compare bug."""
    from association.query.router import SPLIT_WORDS

    assert tuple(SPLIT_WORDS) == SPLIT_KINDS


def test_every_query_eastern_date_is_the_shared_rule() -> None:
    """Six copies of a five-hour shift lived here once; a daylight-time rule
    copied six times is six places for it to drift."""
    from association.nba.season import eastern_date_sql
    from association.query.conditions import _eastern_day
    from association.query.team_metrics import TEAM_GAMES_SQL

    assert _eastern_day("g.date") == eastern_date_sql("g.date")
    assert eastern_date_sql("g.date") in TEAM_GAMES_SQL


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


@pytest.fixture
def old_postseason_and_cup_final(league: TemplateContext) -> TemplateContext:
    """``league``, plus a postseason game labeled 1990 but played in 1991 -
    the wrong-year ESPN label ``team_games.py``'s calendar-year read corrects
    (step 3, C4) - and a neutral-site Las Vegas game, the NBA Cup final: a
    real game that counts in no standings but IS a real game the two teams
    played (``team_games.py``: "the NBA Cup final is flagged rather than
    dropped")."""
    c = league.con
    _game(c, "old1", "1991-05-01T23:30Z", BOS, LAL, 100, 90, [_played(TATUM, BOS, 22)], season=1990, season_type=3)
    _game(c, "cup", f"{S - 1}-12-14T01:30Z", BOS, LAL, 97, 95, [_played(TATUM, BOS, 24), _played(LEBRON, LAL, 30)], neutral_site=True, venue_city="Las Vegas")
    real_games.build_table(c, {"games", "teams", "player_box_stats"})
    return league


@pytest.mark.parametrize("season", [1990, 1993])
def test_a_playoff_season_label_no_longer_refuses_and_finds_nothing_with_no_matching_games(league: TemplateContext, season: int) -> None:
    """Before step 3, C4 this refused, naming the wrong year under the ESPN
    label (``_misfiled_postseason``). Now the label is not read at all - the
    postseason is selected by the calendar year the games were actually
    played in (``team_games.py``) - so asking under the OLD label finds
    nothing, when nothing was actually played in that calendar year, rather
    than a claim about which year the label really means."""
    for template, slots in ((streak, {"team": "Boston Celtics", "kind": "win"}), (streak, {"kind": "win"}), (player_splits, {"team": "Boston Celtics"})):
        answer = template(league, {**slots, "season": season, "season_type": 3}).answer or ""
        assert "postseason is the" not in answer  # the old misfiled-label refusal
        assert "no games" in answer.lower() or "no team has a game" in answer.lower()


def test_a_postseason_labeled_1990_is_read_as_the_1991_playoffs_by_calendar_year(old_postseason_and_cup_final: TemplateContext) -> None:
    """``old1`` is labeled season 1990 but was played 1991-05-01 - the
    Celtics' only playoff win on record for that stretch. Asking for the 1990
    LABEL finds nothing (nothing was played in calendar year 1990); asking
    for 1991, the year it was actually played, finds it - for all three team
    branches this step ports (step 3, C4)."""
    for template, slots in (
        (streak, {"team": "Boston Celtics", "kind": "win"}),
        (player_splits, {"team": "Boston Celtics"}),
        (record_when, {"team": "Boston Celtics", "stat": "points", "threshold": 50}),
    ):
        empty = template(old_postseason_and_cup_final, {**slots, "season": 1990, "season_type": 3})
        assert empty.data.get("games") == 0, f"{template.__name__} found a game under the 1990 LABEL, which is really 1991"
        found = template(old_postseason_and_cup_final, {**slots, "season": 1991, "season_type": 3})
        assert found.data.get("games") != 0, f"{template.__name__} found no game under 1991, the calendar year old1 was actually played"


def test_a_teams_games_over_a_span_holding_the_cup_final_count_it(old_postseason_and_cup_final: TemplateContext) -> None:
    """The Celtics played 7 regular-season games this season with the Cup
    final (``cup``) added to the 6 ``league`` already holds - a plain game
    list or split is not a win-loss RECORD, so it does not exclude the cup
    final the way ``team_record`` does (``team_games.py``: "a plain game
    list or head-to-head count should not [exclude it]")."""
    result = player_splits(old_postseason_and_cup_final, _slots(team="Boston Celtics", split="wins_losses"))
    assert result.data["games"] == 7


def test_a_player_listed_once_is_told_so_in_the_singular(league: TemplateContext) -> None:
    league.con.execute("DELETE FROM player_box_stats WHERE athlete_id = ? AND season = ? AND event_id <> 'e2'", [TATUM, S])
    assert player_splits(league, _slots(player="Jayson Tatum")).answer.endswith("box score in the 2026 regular season but did not play in it.".replace("2026", str(S)))


@pytest.fixture
def old_franchises(league: TemplateContext) -> TemplateContext:
    """The league, plus six 2001 meetings of two franchises that have since been
    renamed or moved: the New Jersey Nets (id 17, today's Brooklyn) beating the
    Vancouver Grizzlies (id 29, today's Memphis). `teams` holds only today's
    names, which is ESPN's."""
    c = league.con
    c.execute("INSERT INTO teams VALUES ('17','BKN','Brooklyn Nets'),('29','MEM','Memphis Grizzlies')")
    c.execute("INSERT INTO players VALUES ('70','Kenyon Martin'),('71','Pau Gasol')")
    for i in range(6):
        _game(c, f"v{i}", f"2001-01-1{i}T00:30Z", "17", "29", 100, 90, [_played("70", "17", 20), _played("71", "29", 15)], season=2001)
    real_games.build_table(c, {"games", "teams", "player_box_stats"})
    return league


def test_a_streak_across_seasons_names_each_team_as_it_was_then(old_franchises: TemplateContext) -> None:
    """Every all-seasons team streak used to end "franchises are named as they
    are today", because that is what it did: a 2001 Nets run read Brooklyn
    Nets. Each run lies inside one season, so it is named for it."""
    answer = streak(old_franchises, _slots(kind="win", span="career")).answer or ""
    assert "New Jersey Nets (2001)" in answer and "Brooklyn" not in answer
    assert "named as they are today" not in answer


def test_a_matchup_log_abbreviates_each_team_for_the_season_of_the_meeting(old_franchises: TemplateContext) -> None:
    """The meeting log read "BKN 100-90 MEM" for a 2001 game in New Jersey
    against Vancouver."""
    answer = player_matchup(old_franchises, _slots(players=["Kenyon Martin", "Pau Gasol"], season=2001)).answer or ""
    assert "NJ 100-90 VAN" in answer and "BKN" not in answer


# ---------------- player_splits: venue and opponent ----------------


def test_a_venue_narrows_a_players_games(league: TemplateContext) -> None:
    """Tatum's three games this season are e1 (home), e4 and e7 (away); a
    venue narrows the games the splits are computed over, not just which
    row of the home/away split is shown.

    The heading names it through ``Narrowed.filters()`` (step 3, C2) rather
    than a phrase this template composed for itself, the way every other
    template on the relation already does - "at home", not "(at home)"."""
    result = player_splits(league, _slots(player="Jayson Tatum", venue="home"))
    assert result.data["games"] == 1
    assert "Jayson Tatum at home," in (result.answer or "")
    rows = _rows(result, "home_away")
    assert (rows["home"]["games"], rows["away"]["games"]) == (1, 0)


def test_an_opponent_narrows_a_players_games(league: TemplateContext) -> None:
    """Tatum played the Lakers twice: e1 at home (30 points) and e7 on the
    road (31)."""
    result = player_splits(league, _slots(player="Jayson Tatum", opponent="Los Angeles Lakers"))
    assert result.data["games"] == 2
    assert "Jayson Tatum vs the Los Angeles Lakers," in (result.answer or "")


def test_venue_and_opponent_narrow_together(league: TemplateContext) -> None:
    """Only e7 - Tatum's road game against the Lakers - matches both.
    ``Narrowed.filters()`` names the opponent before the venue - "vs the
    Lakers on the road", not "on the road vs the Lakers" - the order every
    template reading it already uses (player_stat, game_log)."""
    result = player_splits(league, _slots(player="Jayson Tatum", venue="away", opponent="Los Angeles Lakers"))
    assert result.data["games"] == 1
    assert "vs the Los Angeles Lakers on the road" in (result.answer or "")
    assert "1 game he played" in (result.answer or ""), "not '1 games' - the pluralization a venue/opponent narrowing exposes"


def test_a_venue_narrows_a_teams_own_games_too(league: TemplateContext) -> None:
    """The Celtics' three home games this season (e1, e3, e5) are all wins."""
    result = player_splits(league, _slots(team="Boston Celtics", venue="home"))
    assert result.data["games"] == 3
    rows = _rows(result, "wins_losses")
    assert (rows["wins"]["games"], rows["losses"]["games"]) == (3, 0)


def test_an_opponent_narrows_a_teams_own_games_too(league: TemplateContext) -> None:
    """The Celtics played the Lakers four times: e1, e5 at home (both wins) and e2, e7 on the road (both losses)."""
    result = player_splits(league, _slots(team="Boston Celtics", opponent="Los Angeles Lakers"))
    assert result.data["games"] == 4
    rows = _rows(result, "home_away")
    assert (rows["home"]["wins"], rows["away"]["wins"]) == (2, 0)


def test_a_home_away_split_conflicts_with_an_already_narrowed_venue(league: TemplateContext) -> None:
    """Asking to break games out by home/away while also filtering to one of
    the two asks the same axis twice."""
    with pytest.raises(TemplateUnsupported):
        player_splits(league, _slots(player="Jayson Tatum", split="home_away", venue="home"))


def test_a_real_limit_is_refused_rather_than_answering_the_whole_span(league: TemplateContext) -> None:
    """player_splits has no notion of "his last N games" - answering under
    that framing with the whole span (all 3 of Tatum's games) would be the
    silent substitution this whole module exists to prevent."""
    with pytest.raises(TemplateUnsupported):
        player_splits(league, _slots(player="Jayson Tatum", venue="home", limit=4))


def test_a_bare_limit_of_one_is_not_refused(league: TemplateContext) -> None:
    """The router's own filler value elsewhere (router._route_side_and_order
    drops a limit of 1 for the same reason) - and here it changes nothing,
    since the games a venue narrows to are shown in full either way."""
    result = player_splits(league, _slots(player="Jayson Tatum", venue="home", limit=1))
    assert result.data["games"] == 1


# ---------------- player_splits: step 3, C2 - the relation's own cells ----------------


def test_without_narrows_a_players_splits(league: TemplateContext) -> None:
    """Previously `_player_splits_player` handed `condition_player` a copy of
    `slots` with `"without": None` - so this narrowing reached check_scope's
    declaration and nothing else. Over his career Brown played every one of
    Tatum's three games this season, but none of his four last season (he
    appears without Tatum only in e0z) - so "without Brown" leaves exactly
    those four, and the answer names it."""
    result = player_splits(league, _slots(player="Jayson Tatum", span="career", without="Jaylen Brown", split="home_away"))
    assert result.data["games"] == 4
    assert result.data["without"] == ["Jaylen Brown"]
    assert "without Jaylen Brown" in (result.answer or "")
    rows = _rows(result, "home_away")
    assert (rows["home"]["games"], rows["away"]["games"]) == (3, 1)  # e0c, e0a, e0d home; e0b away


def test_since_narrows_to_a_range_of_seasons(league: TemplateContext) -> None:
    """A third, older season Tatum played is outside the `since` window, so
    "since {S-1}" differs from both the plain current-season default (3
    games) and a full career that would also count the older one (8)."""
    c = league.con
    _game(c, "e00", f"{S - 2}-11-01T00:30Z", BOS, PHI, 100, 90, [_played(TATUM, BOS, 22)], season=S - 2)
    real_games.build_table(c, {"games", "teams", "player_box_stats"})
    result = player_splits(league, _slots(player="Jayson Tatum", since=S - 1))
    assert result.data["games"] == 7  # e0b, e0c, e0a, e0d (S-1) + e1, e4, e7 (S) - not e00 (S-2)
    assert result.data["span"] == f"{S - 1}-{S} regular seasons"


def test_since_and_a_named_season_conflict(league: TemplateContext) -> None:
    """`_condition_scope` used to take the `since` branch unconditionally,
    silently dropping a `season` slot named alongside it - the same pairing
    `_span_of` already refuses for game_log and player_stat."""
    with pytest.raises(TemplateUnsupported):
        player_splits(league, _slots(player="Jayson Tatum", since=S - 1, season=S))


def test_game_n_narrows_to_one_game_of_each_series(league: TemplateContext) -> None:
    """A three-game series vs the Lakers; game_n=2 is q2, which Tatum's
    Celtics won 105-95 on the road (25 points)."""
    c = league.con
    for event, date, home, away, home_score, away_score, points in (
        # q1-q3, not p1-p3: the fixture already holds a p1/p2 series (PLAYOFF_GUY's),
        # and a second insert under the same ids would list game 2 twice.
        # And vs the Lakers, not Philadelphia: the fixture's p1/p2 are a
        # BOS-PHI series in the same postseason, and a series is numbered
        # per opponent pair - game 2 of BOS-PHI would be its p2.
        ("q1", f"{S}-05-20T23:30Z", BOS, LAL, 100, 90, 20),
        ("q2", f"{S}-05-22T23:30Z", LAL, BOS, 95, 105, 25),
        ("q3", f"{S}-05-24T23:30Z", BOS, LAL, 110, 100, 30),
    ):
        c.execute("INSERT INTO games VALUES (?,?,3,?,?,?,?,?,?,?,?)", [event, S, date, home, away, home_score, away_score, home if home_score > away_score else away, False, "Boston"])
        for team, opponent, side in ((home, away, "home"), (away, home, "away")):
            c.execute("INSERT INTO team_box_stats VALUES (?,?,3,?,?,?,40,12,23,20,10,40,85)", [event, S, team, opponent, side])
        c.execute(
            "INSERT INTO player_box_stats VALUES (?,?,3,?,?,?,TRUE,FALSE,30,?,5,3,0,0,0,1,?,?,0,0)",
            [event, S, BOS, LAL, TATUM, points, points // 2, points],
        )
    real_games.build_table(c, {"games", "teams", "player_box_stats"})
    result = player_splits(league, _slots(player="Jayson Tatum", season_type=3, game_n=2))
    assert result.data["games"] == 1
    assert result.data["series_game"] == 2
    assert "game 2 of each series" in (result.answer or "")
    rows = _rows(result, "wins_losses")
    assert (rows["wins"]["games"], rows["losses"]["games"]) == (1, 0)


def test_a_named_half_of_starter_bench_narrows_the_games(league: TemplateContext) -> None:
    """ "as a starter" is a request to FILTER - the value `_split_side` reads
    out of the question for game_log and the other filtering templates - not
    a request for the starter/bench table over every game. Tatum started e1
    (30) and e7 (31) and came off the bench in e4 (35); split="starter"
    narrows to the two he started, while the CATEGORY shown is still the same
    two-row table it always was, folded back from the half the question
    named (previously this always raised: "starter"/"bench" were not in
    SPLIT_KINDS, so the fold-back in `_player_splits_answer` was dead code)."""
    result = player_splits(league, _slots(player="Jayson Tatum", split="starter"))
    assert result.data["games"] == 2
    assert result.data["started"] is True
    assert "as a starter" in (result.answer or "")
    rows = _rows(result, "starter_bench")
    assert rows["starter"]["games"] == 2
    assert rows["bench"]["games"] == 0
    assert rows["starter"]["points"] == pytest.approx(30.5)


def test_season_n_settles_to_the_year_once_the_player_is_known(league: TemplateContext) -> None:
    """His 1st season (season_n=1) is last season (S-1, 4 games) - a
    different year from the "now" this template defaults to, so the label
    the answer names has to follow the ordinal the relation settled rather
    than the `_Scope` built (as "now") before the player was known."""
    # The league fixture already holds Tatum's two seasons on record.
    first = player_splits(league, _slots(player="Jayson Tatum", season_n=1))
    assert first.data["games"] == 4  # e0b, e0c, e0a, e0d
    assert first.data["span"] == f"{S - 1} regular season"
    # His 2nd season is this one, where the plain "no season named" default already lands.
    second = player_splits(league, _slots(player="Jayson Tatum", season_n=2))
    assert second.data["games"] == player_splits(league, _slots(player="Jayson Tatum")).data["games"] == 3


def test_a_season_n_past_his_career_is_refused_by_name(league: TemplateContext) -> None:
    # The league fixture already holds Tatum's two seasons on record.
    answer = player_splits(league, _slots(player="Jayson Tatum", season_n=5)).answer or ""
    assert "2 seasons on record" in answer


def test_above_and_below_narrow_which_games_the_splits_cover(league: TemplateContext) -> None:
    """Tatum's three counted games this season score 30, 35 and 31 points
    (e1, e4, e7); "at least 32" keeps e4 alone, "under 32" keeps the other
    two - refused here, before any name is resolved, if the line names no
    column at all (:func:`association.query.templates.common.measure_filters`)."""
    high = player_splits(league, _slots(player="Jayson Tatum", above="32 points"))
    assert high.data["games"] == 1
    assert high.data["measures"] == ["at least 32 points"]
    assert "at least 32 points" in (high.answer or "")
    low = player_splits(league, _slots(player="Jayson Tatum", below="32 points"))
    assert low.data["games"] == 2
    assert "under 32 points" in (low.answer or "")
    with pytest.raises(TemplateUnsupported):
        player_splits(league, _slots(player="Jayson Tatum", above="20 vibes"))
