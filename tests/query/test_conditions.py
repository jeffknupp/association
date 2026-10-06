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

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import duckdb
import pytest
from routed import planned_answer as compose_answer
from test_templates import player_matchup, player_splits, streak, with_without  # the compiler's, the templates retired (compose.COMPILED_INTENTS)

from association.fetch.repairs import real_games
from association.fetch.repairs.reconstructed_box import _FILLED_COLUMNS as FILLED_COLUMNS
from association.nba.season import current_season
from association.query.answer import AnswerContext, Reply
from association.query.compose.core import Query, run
from association.query.compose.plan import words_stated
from association.query.compose.sentence import sentence
from association.query.conditions import RAW_BOX, UNGATED_ON_REBUILD, box_source
from association.query.coverage import check_coverage
from association.query.parse import with_point
from association.query.player_games import REBUILT_STATS
from association.query.reading import SPLIT_KINDS, Reading, Scope, Unsupported, unhonored_scoping
from association.query.result import Unanswered
from association.query.subject import Subject


def _compiled(intent: str) -> Callable[[AnswerContext, Reading], Reply]:
    """``intent`` answered the way its retired template was called - a
    Reading in, a Reply out, ``Unsupported`` with the
    reason where the compiler has no reading of the point - now that the
    compiler alone answers it (``compose.COMPILED_INTENTS``, ROADMAP plan item
    6, step (d), part 4). No question words, which would move the point:
    these are the intent's own. The subject is the one the slots name, as a
    question naming just them reads."""

    def answered(ctx: AnswerContext, reading: Reading) -> Reply:
        scope = reading.scope
        named = tuple(name for name in (scope.player, *scope.players) if name)
        kind = "pair" if len(named) > 1 else "player" if named else "team" if scope.team else "everyone"
        subject = reading.subject or Subject(kind, players=named, teams=(scope.team,) if scope.team else ())
        why: list[str] = []
        result = compose_answer(ctx, with_point(ctx.con, "", Reading(scope=scope, intent=intent, subject=subject)), declined=why.append)
        if result is None:
            raise Unsupported(why[0] if why else f"the compiler has no reading of this {intent} point")
        return result

    return answered


record_when = _compiled("record_when")

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
def league(tmp_path: Path) -> AnswerContext:
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
    return AnswerContext(con=c, out_dir=tmp_path)


def _slots(**given: Any) -> dict[str, Any]:
    return {"season_type": 2, **given}


def _rows(result: Reply, split: str) -> dict[str, dict[str, Any]]:
    return {row["group"]: row for row in result.data["splits"][split]}


# ---------------- games rebuilt from play-by-play ----------------


@pytest.fixture
def rebuilt_league(league: AnswerContext) -> AnswerContext:
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


def test_a_rebuilt_game_counts_as_a_game_he_played(rebuilt_league: AnswerContext) -> None:
    """The P1 this fixes. A rebuilt line has no minutes, so before the source
    was resolved every such game read as one he missed - and a season with
    nothing but rebuilt games answered "was listed in N box scores but did not
    play in any of them". Tatum played e1, e4, e7 and now e5."""
    result = player_splits(rebuilt_league, Reading.from_slots(_slots(player="Jayson Tatum", split="home_away")))
    assert result.data["games"] == 4
    assert "did not play in any of them" not in (result.answer or "")


def test_a_rebuilt_game_is_no_longer_an_unknown_game(rebuilt_league: AnswerContext) -> None:
    """`_box_missing` has to widen with `_played`, or the same answer both
    counts a game and reports it as one with no box score - the contradiction
    `_empty_box_scores(covered_by_rebuild=...)` exists to stop elsewhere."""
    answer = player_splits(rebuilt_league, Reading.from_slots(_slots(player="Jayson Tatum", split="home_away"))).answer or ""
    # The unseen-games note's own words; the rebuilt game is said by the
    # rebuilt-line note instead, as a game whose box score ESPN lacks.
    assert "The warehouse has no box score" not in answer


def test_a_figure_the_rebuild_gets_wrong_is_left_out_rather_than_averaged_in(rebuilt_league: AnswerContext) -> None:
    """The filled view substitutes more columns than the rebuild was measured
    for. Tatum's two home games are e1 (played: 30 points, 0 turnovers, 1
    three, 30 minutes) and e5 (rebuilt: 26 points, and a deliberately absurd 9
    turnovers and 9 threes).

    Points are inside `REBUILT_STATS`, so both games count. Turnovers and
    threes are not, so the average is taken over the game that carries them -
    the failure this rules out is the quiet one, where 9 is averaged in rather
    than a wrong number appearing on its own."""
    rows = _rows(player_splits(rebuilt_league, Reading.from_slots(_slots(player="Jayson Tatum", split="home_away"))), "home_away")
    home = rows["home"]
    assert home["games"] == 2
    assert home["points"] == pytest.approx(28.0), "the rebuilt 26 IS read"
    assert home["turnovers"] == pytest.approx(0.0), "e1 alone; averaging the rebuilt 9 in would give 4.5"
    assert home["threes"] == pytest.approx(1.0), "e1 alone; averaging the rebuilt 9 in would give 5.0"
    assert home["minutes"] == pytest.approx(30.0), "e1 alone; a rebuilt game has no minutes to count as zero"
    # A rate whose attempts the rebuild never measured is over e1 alone in BOTH
    # sums (core._rate_sql). Until 2026-10-04 the makes ran over both games and
    # the attempts over e1, and this split printed 93.3%.
    assert home["fg_pct"] == pytest.approx(50.0), "e1 alone, numerator and denominator alike"


def test_a_split_says_the_columns_a_rebuilt_line_cannot_fill(rebuilt_league: AnswerContext) -> None:
    """ISSUES.md #327: the split's minutes, turnovers, threes and FG% are over
    e1, e4 and e7 - e5's rebuilt line has none of them - while G and W-L count
    all four games. The table keeps G as the games he played (the heading
    counts them) and the note says which columns are over fewer, and how many
    fewer. Before, the answer said nothing."""
    answer = player_splits(rebuilt_league, Reading.from_slots(_slots(player="Jayson Tatum", split="home_away"))).answer or ""
    assert "1 of these 4 games has no box score from ESPN: its figures are rebuilt from play-by-play, which leaves no minutes, turnovers, 3PM or FG%, so those are read over the other 3." in answer


def _rate_ranking(minimum: int) -> Query:
    """The league's FG% leaders this season with a minimum, as the planner
    hands a position or postseason ranking to the player-games relation."""
    return Query(
        scope=Scope(season_type=2, stat="fg_pct"), skeleton="grouped", subject="everyone", group="player", aggregate="per_game", measures=["fg_pct"], order="measure", minimum_games=minimum, limit=10
    )


def test_a_rate_ranking_qualifies_on_the_games_the_rate_reads(rebuilt_league: AnswerContext) -> None:
    """ISSUES.md #327, the review's Taj Gibson: a rate that skips rebuilt
    lines must not qualify on them. Tatum played four games, e5 rebuilt, so
    his FG% is over three - under a 4-game minimum he is out; Brown played
    six, five of them read, and stays, his read count beside his figure.
    Before, Tatum ranked on "4 G" with a FG% over 3."""
    out = run(rebuilt_league.con, _rate_ranking(4))
    listed = {row["group"]: row for row in out["rows"]}
    assert "Jayson Tatum" not in listed
    assert listed["Jaylen Brown"]["games"] == 6
    assert listed["Jaylen Brown"]["fg_pct_games"] == 5
    assert "Jaylen Brown             6 G  FG% 49.4% (5 G)" in sentence(_rate_ranking(4), out)
    assert out["notes"] == [
        "1 of the listed players' 10 games has no box score from ESPN: its figures are rebuilt from play-by-play, which leaves no FG%,"
        " so a player's FG% is read over his other games, counted beside the figure, and the minimum counts only those."
    ]


def test_a_measure_read_over_every_game_carries_no_count_of_its_own(rebuilt_league: AnswerContext) -> None:
    """The count is said only where it is fewer: a group with no rebuilt
    line (LeBron's) keeps the row it always had, and a measure a rebuild
    fills (points) never carries one."""
    query = replace(_rate_ranking(1), measures=["fg_pct", "points"])
    listed = {row["group"]: row for row in run(rebuilt_league.con, query)["rows"]}
    assert "fg_pct_games" not in listed["LeBron James"]
    assert not any("points_games" in row for row in listed.values())


def test_a_named_players_grouped_read_says_the_rebuilt_games_once(rebuilt_league: AnswerContext) -> None:
    """A named player's compiled split already says his rebuilt games
    (``lines_rebuilt``, "shown"); where a measure could not read them, the
    one sentence says both rather than two saying the same games."""
    query = Query(scope=Scope(player="Jayson Tatum", season_type=2), skeleton="grouped", group="venue", aggregate="per_game", measures=["points", "fg_pct"])
    out = run(rebuilt_league.con, query)
    home = next(row for row in out["rows"] if row["group"] == "home")
    assert (home["games"], home["fg_pct_games"]) == (2, 1)
    rebuilt = [each for each in out["notes"] if "box score from ESPN" in each]
    assert rebuilt == ["1 of these 4 games has no box score from ESPN: its figures are rebuilt from play-by-play, which leaves no FG%, so that is read over the other 3."]


def test_a_teammate_in_a_rebuilt_game_is_not_counted_as_absent(rebuilt_league: AnswerContext) -> None:
    """The other half of the P1: `_with_without_games` (now `presence_games_sql`) asked for minutes too,
    so a teammate who played a rebuilt game read as out and the game was
    counted on the "without" side."""
    result = with_without(rebuilt_league, Reading.from_slots(_slots(team="Boston Celtics", without="Jayson Tatum")))
    played = next(row for row in result.data["groups"] if row["teammate_played"])
    assert played["games"] == 4, "e5 is a game Tatum played, not one he missed"


def test_the_subjects_own_average_counts_his_rebuilt_game(rebuilt_league: AnswerContext) -> None:
    """The Python half of the same fault, and the one no SQL guard covers:
    `_with_without_group` picks his games out of the group in Python, and did
    it with `minutes is not None` - so a rebuilt game passed every query-side
    check and was dropped again on the way to the average.

    Brown played all four of Tatum's games: e1 20, e4 10, e7 12, and e5
    rebuilt at 26. Reading minutes as the proxy drops e5 and answers 3 games
    at 14.0 - which is exactly
    `test_a_player_subject_gets_his_averages_in_each_group` on the un-rebuilt
    fixture, and the reason this needs a test of its own."""
    result = with_without(rebuilt_league, Reading.from_slots(_slots(player="Jaylen Brown", without="Jayson Tatum")))
    groups = {g["teammate_played"]: g for g in result.data["groups"]}
    assert groups[True]["player_games"] == 4, "e5 is his game too"
    assert groups[True]["points"] == pytest.approx(17.0), "68/4; dropping the rebuilt 26 gives 14.0"
    assert groups[True]["minutes"] == pytest.approx(30.0), "the three games that carry minutes, not 22.5 over four"


def test_a_warehouse_without_the_filled_view_reads_as_it_always_did(league: AnswerContext) -> None:
    """The view arrives with a `data load`. An older warehouse has none, and
    every fixture here builds only `player_box_stats` - a query written as
    though the view were always there is a Binder error, not a value change.
    The one rule, for the view and the log alike."""
    assert box_source(league.con) == RAW_BOX
    assert player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", split="home_away"))).data["games"] == 3


def test_record_when_group_sums_every_game_regardless_of_order(league: AnswerContext) -> None:
    """The record-over-a-line reader's grouping (`compose.records._group`,
    which `templates.splits._record_when_group` was until slice (iv)): "every
    game" sums all three groups - reached, fell short, and a blank-stat game,
    which still has a real result even though it counts in neither threshold
    row. Built by hand, in two insertion orders, since the bug this guards
    was exactly a `by_hit` dict whose key depended on which order DuckDB's
    parallel GROUP BY happened to return the groups in (ISSUES.md)."""
    from association.query.compose.records import _group

    del league  # unused; a pure-Python check of the grouping helper alone
    reached_row: dict[str, Any] = {"group": "reached", "games": 5, "wins": 3, "margin": 2.5}
    short_row: dict[str, Any] = {"group": "short", "games": 10, "wins": 4, "margin": -1.0}
    blank_row: dict[str, Any] = {"group": "blank", "games": 2, "wins": 1, "margin": 0.5}
    for found in ([reached_row, short_row, blank_row], [blank_row, reached_row, short_row], [short_row, blank_row, reached_row]):
        by_hit: dict[str, dict[str, Any]] = {str(row["group"]): row for row in found}
        assert _group(by_hit, "reached") == {"games": 5, "wins": 3, "losses": 2, "avg_margin": 2.5}
        assert _group(by_hit, "short") == {"games": 10, "wins": 4, "losses": 6, "avg_margin": -1.0}
        every = _group(by_hit, "reached", "short", "blank")
        assert (every["games"], every["wins"], every["losses"]) == (17, 8, 9)
        assert every["avg_margin"] == pytest.approx((5 * 2.5 + 10 * -1.0 + 2 * 0.5) / 17)


def test_record_when_keeps_a_blank_stat_game_off_both_threshold_rows(rebuilt_league: AnswerContext) -> None:
    """The P1 this fixes (ISSUES.md): `_record_when_answer` used to key its
    threshold groups by `bool(row[0])`, so a blank-stat game (NULL on a
    rebuilt row) collided with the "fell short" group - `bool(None) ==
    bool(False)` - and whichever one DuckDB's parallel GROUP BY happened to
    return last silently won, so the same question answered a different
    "under threshold" row from one asking to the next.

    Tatum's turnovers are 0 in every one of his real games this season (e1 W,
    e4 W, e7 L) and blanked on the rebuilt e5 (a win) - turnovers is one of
    `UNGATED_ON_REBUILD`'s columns, never trusted on a rebuilt row. A
    threshold of 1 turnover puts every real game "under" and leaves e5 in
    neither threshold row, still counted in "all his games" since it has a
    real result."""
    result = record_when(rebuilt_league, Reading.from_slots(_slots(player="Jayson Tatum", stat="turnovers", threshold=1)))
    assert result.data["reached"] == {"games": 0, "wins": 0, "losses": 0, "avg_margin": None}
    assert result.data["fell_short"] == {"games": 3, "wins": 2, "losses": 1, "avg_margin": pytest.approx(1 / 3)}
    assert "Over the 4 games he played" in (result.answer or "")
    assert "1 of his games in that span have no turnovers figure on record, so they are in neither row." in (result.answer or "")


def test_the_ungated_rebuild_columns_are_the_ones_no_template_may_read() -> None:
    """`UNGATED_ON_REBUILD` is a third hand-maintained list beside
    `REBUILT_STATS` and the view's own substitutions, which is the shape this
    project keeps getting bitten by. Derive it and compare."""
    substituted = {warehouse_column for warehouse_column, _ in FILLED_COLUMNS}
    assert set(UNGATED_ON_REBUILD) == substituted - set(REBUILT_STATS)


# ---------------- player_splits ----------------


def test_splits_count_only_games_he_played(league: AnswerContext) -> None:
    """A DNP row, no row and a missing box score are three games Tatum did not
    play in this season as far as any box score says; he played e1, e4, e7."""
    result = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", split="home_away")))
    assert result.data["games"] == 3
    rows = _rows(result, "home_away")
    assert (rows["home"]["games"], rows["away"]["games"]) == (1, 2)
    assert rows["away"]["points"] == pytest.approx(33.0)  # e4 35, e7 31
    assert result.data["headline"] == (result.answer or "").split("\n")[0].rstrip(":")
    assert "Played means he appeared in the game" in " ".join(result.data["notes"])


def test_a_month_is_the_eastern_date_the_game_was_played(league: AnswerContext) -> None:
    """e1 is stored as November 1st UTC and was played on October 31st; e7 is
    December 1st UTC and November 30th Eastern. On the UTC date the split would
    be one game in each of three months."""
    rows = _rows(player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", split="month"))), "month")
    assert [(name, row["games"]) for name, row in rows.items()] == [("October", 1), ("November", 2)]


def test_an_empty_half_of_a_split_is_shown_not_dropped(league: AnswerContext) -> None:
    rows = _rows(player_splits(league, Reading.from_slots(_slots(player="Jaylen Brown", split="starter_bench"))), "starter_bench")
    assert rows["bench"]["games"] == 0 and rows["starter"]["games"] == 5  # e1, e2, e3, e4, e7


def test_no_split_named_shows_all_four(league: AnswerContext) -> None:
    result = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum")))
    assert set(result.data["splits"]) == set(SPLIT_KINDS)
    wins = _rows(result, "wins_losses")
    assert (wins["wins"]["games"], wins["losses"]["games"]) == (2, 1)


def test_splits_say_a_missing_box_score_was_not_counted(league: AnswerContext) -> None:
    answer = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", split="home_away"))).answer
    assert "no box score for 1 of his team's games" in answer


def test_a_career_is_every_season_not_the_current_one(league: AnswerContext) -> None:
    result = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", split="home_away", span="career")))
    assert result.data["games"] == 7
    assert result.data["span"] == f"{S - 1}-{S} regular seasons"


def test_a_team_split_uses_the_teams_own_games(league: AnswerContext) -> None:
    """The placeholder e6 is no game at all; e5's result stands even though its
    box score is missing, and the averages its box would feed say so."""
    result = player_splits(league, Reading.from_slots(_slots(team="Boston Celtics", split="wins_losses")))
    rows = _rows(result, "wins_losses")
    assert (rows["wins"]["games"], rows["losses"]["games"]) == (4, 2)
    assert "missing from 1 of those games' box scores" in result.answer


def test_a_team_splits_rebounds_are_offensive_plus_defensive_not_the_raw_total(league: AnswerContext) -> None:
    """`totalRebounds` stops meaning the same thing across 2021/2022 - DATA.md,
    "The team `totalRebounds` column stops including team rebounds in 2022".
    `_TEAM_LINE` reads offensiveRebounds + defensiveRebounds instead, which is
    what ESPN's own totalRebounds equals in every season from 2022 on. The
    fixture's totalRebounds (40) deliberately differs from offensiveRebounds +
    defensiveRebounds (12 + 23 = 35) the way a real pre-2022 row does, so
    reading the wrong column would show 40.0 here instead."""
    rows = _rows(player_splits(league, Reading.from_slots(_slots(team="Boston Celtics", split="wins_losses"))), "wins_losses")
    assert rows["wins"]["rebounds"] == pytest.approx(35.0)
    assert rows["losses"]["rebounds"] == pytest.approx(35.0)


def test_a_team_has_no_starter_split(league: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        player_splits(league, Reading.from_slots(_slots(team="Boston Celtics", split="starter_bench")))


def test_a_team_subject_refuses_without_rather_than_silently_dropping_it(league: AnswerContext) -> None:
    """`_player_splits_team` reads the team tables directly and has no
    teammate-absence filter (ISSUES.md, "player_splits cannot honor a
    teammate's absence ... when the subject is a team") - "Celtics splits
    without Tatum" used to answer the Celtics' whole season, identical to
    leaving `without` out and with nothing saying so. Refused now, the same
    way a starter/bench split already is for a team."""
    with pytest.raises(Unsupported, match="without"):
        player_splits(league, Reading.from_slots(_slots(team="Boston Celtics", without="Jayson Tatum")))


def test_a_team_split_honors_until(league: AnswerContext) -> None:
    """``until`` (ISSUES.md): before this, `_player_splits_team` never passed
    it to `_span_of`, so bounding the range to just `last` (the fixture's
    ``since=until={S - 1}``) silently answered the same 11-game pool an
    open-ended ``since={S - 1}`` reaches instead - the Celtics' games both
    last season (e0z-e0d, 5 games) and this one (e1-e7, 6 games). Bounded to
    just `last`, only those first 5 count, and the label says the real range
    rather than an open-ended "since"."""
    bounded = player_splits(league, Reading.from_slots(_slots(team="Boston Celtics", split="wins_losses", since=S - 1, until=S - 1)))
    unbounded = player_splits(league, Reading.from_slots(_slots(team="Boston Celtics", split="wins_losses", since=S - 1)))
    assert bounded.data["games"] == 5
    assert unbounded.data["games"] == 11
    bounded_rows, unbounded_rows = _rows(bounded, "wins_losses"), _rows(unbounded, "wins_losses")
    assert (bounded_rows["wins"]["games"], bounded_rows["losses"]["games"]) == (4, 1)
    assert (unbounded_rows["wins"]["games"], unbounded_rows["losses"]["games"]) == (8, 3)
    assert f"from {S - 1} through {S - 1}" in (bounded.answer or "")
    assert f"since {S - 1} ({S - 1}-{S}" in (unbounded.answer or "")


def test_a_team_subject_refuses_a_condition_rather_than_silently_dropping_it(league: AnswerContext) -> None:
    """The same discipline as ``without`` just above (ISSUES.md): a
    ``conditions`` entry names a role - a start, a bench game, a line
    reached - for one specific player, and the team branch has no settled
    player to check it against."""
    with pytest.raises(Unsupported, match="conditions"):
        player_splits(league, Reading.from_slots(_slots(team="Boston Celtics", conditions=[{"player": "Jayson Tatum", "predicate": "started"}])))


def test_an_unknown_split_is_refused(league: AnswerContext) -> None:
    # Refused at the Reading's door (Scope.from_slots) before any template runs.
    with pytest.raises(ValueError, match="split"):
        player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", split="by_weekday")))


def test_a_stat_the_standard_line_does_not_carry_gets_its_own_column(league: AnswerContext) -> None:
    """F159 (ISSUES.md): "Quentin Grimes individual gamelog usage rating
    without joel embiid" showed the standard split columns, which have no
    usage-rate column at all, so the actually-asked-for stat was simply
    missing from an otherwise-correct read. `usage_pct` now reads straight
    off the relation and adds its own column - read directly from
    player_game_log, the way the yardstick key notes it already can be."""
    league.con.execute("ALTER TABLE player_box_stats ADD COLUMN usage_pct DOUBLE")
    # Tatum's three played games this season: e1 (home vs LAL), e4 (away @
    # PHI), e7 (away @ LAL) - see the `league` fixture's own table.
    league.con.execute("UPDATE player_box_stats SET usage_pct = 20.0 WHERE athlete_id = ? AND event_id = 'e1'", [TATUM])
    league.con.execute("UPDATE player_box_stats SET usage_pct = 30.0 WHERE athlete_id = ? AND event_id = 'e4'", [TATUM])
    league.con.execute("UPDATE player_box_stats SET usage_pct = 40.0 WHERE athlete_id = ? AND event_id = 'e7'", [TATUM])
    result = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="usage_pct", split="home_away")))
    assert "USG%" in result.answer
    rows = _rows(result, "home_away")
    assert rows["home"]["usage_pct"] == pytest.approx(20.0)
    assert rows["away"]["usage_pct"] == pytest.approx(35.0)  # mean of 30.0 and 40.0
    # A stat the standard line already carries is neither refused nor given a
    # redundant second column.
    already_shown = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", split="home_away")))
    assert already_shown.answer.count("PTS") == 1


def test_player_splits_refuses_a_stat_it_has_no_column_for(league: AnswerContext) -> None:
    """The other half of F159's fix: a stat neither on the standard line nor
    in SPLIT_EXTRA_STATS is refused by name rather than silently answered
    without it - and a team subject, which has no per-player rate column at
    all, refuses the same stat a player subject can show."""
    # A player's fouls by venue are the compiler's own point now (5.0.0): the
    # splits table has no column for them, and the compiler's sentence shows
    # the stat by group instead of refusing it.
    shown = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="fouls", split="home_away")))
    assert "by venue (fouls" in shown.answer and {r["group"] for r in shown.data["rows"]} == {"home", "away"}
    with pytest.raises(Unsupported, match="usage_pct"):
        player_splits(league, Reading.from_slots(_slots(team="Boston Celtics", stat="usage_pct", split="wins_losses")))


def test_a_player_with_only_dnp_rows_is_told_apart_from_one_with_none(league: AnswerContext) -> None:
    listed = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", team="Boston Celtics", season=S - 2))).answer
    assert listed == f"Jayson Tatum has no games for the Boston Celtics in the {S - 2} regular season in the warehouse."
    # Leave him only e2's DNP row and e5's NULL-minutes one this season.
    league.con.execute("DELETE FROM player_box_stats WHERE athlete_id = ? AND season = ? AND event_id NOT IN ('e2', 'e5')", [TATUM, S])
    sat = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum"))).answer
    assert sat == f"Jayson Tatum was listed in 2 box scores in the {S} regular season but did not play in any of them."


# ---------------- with_without ----------------


def test_with_and_without_is_counted_inside_his_time_on_the_team(league: AnswerContext) -> None:
    """With Tatum: e1 W, e4 W, e7 L. Without: e2 (DNP) L, e3 (no row) W. e5's
    box score is missing, so it is on neither side; the placeholder is not a
    game."""
    result = with_without(league, Reading.from_slots(_slots(team="Boston Celtics", without="Tatum")))
    groups = {g["teammate_played"]: g for g in result.data["groups"]}
    assert (groups[True]["wins"], groups[True]["losses"]) == (2, 1)
    assert (groups[False]["wins"], groups[False]["losses"]) == (1, 1)
    assert "1 game inside that time has no box score" in result.answer
    assert result.answer.splitlines()[2].startswith("Jayson Tatum out")  # a "without" question leads with it


def test_with_and_without_narrows_to_one_opponent_and_says_so(league: AnswerContext) -> None:
    """#163: "Embiid career record vs boston" is his team's record in the games
    it played BOSTON, and there was no way to ask it - `opponent` was honored
    by nothing here, so the question fell through.

    Against the Lakers the Celtics played e1 (W, Tatum), e2 (L, Tatum DNP) and
    e7 (L, Tatum); e5 has no box score and is on neither side. So with Tatum
    1-1 and without him 0-1, where the unnarrowed split is 2-1 and 1-1. The
    opponent is in the TITLE, because a record over one opponent's games headed
    as though it covered every game is the silent narrowing this module exists
    to stop."""
    result = with_without(league, Reading.from_slots(_slots(team="Boston Celtics", without="Tatum", opponent="Lakers")))
    groups = {g["teammate_played"]: g for g in result.data["groups"]}
    assert (groups[True]["wins"], groups[True]["losses"]) == (1, 1)
    assert (groups[False]["wins"], groups[False]["losses"]) == (0, 1)
    assert "vs the Los Angeles Lakers" in result.answer
    # The other opponent is a different pool, not the same numbers.
    sixers = with_without(league, Reading.from_slots(_slots(team="Boston Celtics", without="Tatum", opponent="76ers")))
    by_played = {g["teammate_played"]: g for g in sixers.data["groups"]}
    assert (by_played[True]["wins"], by_played[True]["losses"]) == (1, 0)
    assert (by_played[False]["wins"], by_played[False]["losses"]) == (1, 0)
    # And naming none is the whole season, exactly as before.
    every = with_without(league, Reading.from_slots(_slots(team="Boston Celtics", without="Tatum")))
    assert "vs the" not in every.answer
    assert {g["teammate_played"]: (g["wins"], g["losses"]) for g in every.data["groups"]} == {True: (2, 1), False: (1, 1)}


def test_without_two_teammates_counts_only_the_games_neither_played(league: AnswerContext) -> None:
    """The measured bug: "Celtics record without Tatum and Brown" dropped the
    second name and answered about Tatum alone (1-1 here). With Brown sitting
    out e3 as well, e3 is the only game neither of them played."""
    league.con.execute(
        "INSERT INTO player_box_stats VALUES ('e3', ?, 2, ?, ?, ?, FALSE, FALSE, 10, 4, 2, 1, 0, 0, 0, 0, 2, 4, 0, 1)",
        [S, BOS, PHI, JOURNEYMAN],
    )  # e3 keeps a box score once Brown sits: without it the game counts as one nobody can tell
    league.con.execute("UPDATE player_box_stats SET did_not_play = TRUE, minutes = NULL WHERE athlete_id = ? AND event_id = 'e3'", [BROWN])
    result = with_without(league, Reading.from_slots(_slots(team="Boston Celtics", without=["Tatum", "Jaylen Brown"])))
    groups = {g["teammate_played"]: g for g in result.data["groups"]}
    assert (groups[False]["wins"], groups[False]["losses"]) == (1, 0)  # e3
    assert (groups[True]["wins"], groups[True]["losses"]) == (2, 2)  # e1, e2, e4, e7
    assert result.data["teammates"] == ["Jayson Tatum", "Jaylen Brown"]
    assert "Jayson Tatum and Jaylen Brown out" in result.answer


def test_with_two_teammates_counts_only_the_games_both_played(league: AnswerContext) -> None:
    """The mirror of the same rule: "record when A and B play" is the games
    both of them played, not the games either did."""
    league.con.execute(
        "INSERT INTO player_box_stats VALUES ('e3', ?, 2, ?, ?, ?, FALSE, FALSE, 10, 4, 2, 1, 0, 0, 0, 0, 2, 4, 0, 1)",
        [S, BOS, PHI, JOURNEYMAN],
    )  # e3 keeps a box score once Brown sits: without it the game counts as one nobody can tell
    league.con.execute("UPDATE player_box_stats SET did_not_play = TRUE, minutes = NULL WHERE athlete_id = ? AND event_id = 'e3'", [BROWN])
    result = with_without(league, Reading.from_slots(_slots(team="Boston Celtics", with_player=["Jayson Tatum", "Jaylen Brown"])))
    groups = {g["teammate_played"]: g for g in result.data["groups"]}
    assert (groups[True]["wins"], groups[True]["losses"]) == (2, 1)  # e1, e4, e7
    assert (groups[False]["wins"], groups[False]["losses"]) == (1, 1)  # e2, e3
    assert result.answer.splitlines()[2].startswith("Jayson Tatum and Jaylen Brown played")


def test_a_game_before_he_arrived_is_not_a_game_without_him(league: AnswerContext) -> None:
    """The StatMuse failure: "Nets record without KD all-time" counted decades
    of Nets games before he arrived. e0z is a Celtics win before Tatum's first
    box score; counted, "without" would be 2-1."""
    result = with_without(league, Reading.from_slots(_slots(team="Boston Celtics", without="Tatum", span="career")))
    groups = {g["teammate_played"]: g for g in result.data["groups"]}
    assert (groups[False]["wins"], groups[False]["losses"]) == (1, 1)
    assert (groups[True]["wins"], groups[True]["losses"]) == (5, 2)
    assert result.data["tenure"] == [{"team": "Boston Celtics", "from": f"{S - 2}-10-20", "to": f"{S - 1}-11-30"}]


def test_a_traded_player_is_not_missing_from_his_old_team(league: AnswerContext) -> None:
    """Journeyman left the Celtics last season: this season's Celtics games are
    not games they played without him, and the refusal says why."""
    answer = with_without(league, Reading.from_slots(_slots(team="Boston Celtics", without="Journeyman Guy"))).answer
    # e0a tips at 00:30 UTC on the 10th, which is the evening of the 9th in Boston.
    assert "falls outside the" in answer and f"Boston Celtics {S - 2}-10-20 to {S - 2}-11-09" in answer


def test_a_player_who_left_and_came_back_has_two_spells_not_one(league: AnswerContext) -> None:
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
    result = with_without(league, Reading.from_slots(_slots(team="Boston Celtics", without="Boomerang Guy", span="career")))
    groups = {g["teammate_played"]: g for g in result.data["groups"]}
    assert groups[False]["games"] == 0 and groups[True]["games"] == 2
    assert [t["team"] for t in result.data["tenure"]] == ["Boston Celtics", "Boston Celtics"]


def test_a_teammate_who_never_played_for_the_team_is_refused_by_name(league: AnswerContext) -> None:
    answer = with_without(league, Reading.from_slots(_slots(team="Philadelphia 76ers", without="Tatum"))).answer
    assert answer.startswith("Jayson Tatum never appeared in a box score for the Philadelphia 76ers")


def test_a_player_subject_gets_his_averages_in_each_group(league: AnswerContext) -> None:
    """Brown played every Celtics game with a box score. With Tatum: e1 20, e4
    10, e7 12. Without: e2 25, e3 18."""
    result = with_without(league, Reading.from_slots(_slots(player="Jaylen Brown", without="Jayson Tatum")))
    groups = {g["teammate_played"]: g for g in result.data["groups"]}
    assert groups[True]["player_games"] == 3 and groups[True]["points"] == pytest.approx(14.0)
    assert groups[False]["player_games"] == 2 and groups[False]["points"] == pytest.approx(21.5)
    assert result.data["player"] == "Jaylen Brown"
    assert result.data["headline"] == (result.answer or "").split("\n")[0].rstrip(":")
    assert result.data["notes"]  # at least the "Played means ..." caveat


def test_the_teammate_repeated_in_the_player_slot_is_not_a_subject(league: AnswerContext) -> None:
    result = with_without(league, Reading.from_slots(_slots(team="Boston Celtics", player="Jayson Tatum", without="Tatum")))
    assert result.data["player"] is None and result.data["teammate"] == "Jayson Tatum"


def test_with_without_needs_a_teammate(league: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        with_without(league, Reading.from_slots(_slots(team="Boston Celtics")))


def test_three_names_and_no_without_is_not_guessed(league: AnswerContext) -> None:
    with pytest.raises(Unsupported):
        with_without(league, Reading.from_slots(_slots(team="Boston Celtics", players=["Jayson Tatum", "Jaylen Brown", "Journeyman Guy"])))


# ---------------- record_when ----------------


def test_record_when_divides_his_games_by_the_threshold(league: AnswerContext) -> None:
    """Tatum this season: e1 30 W, e4 35 W, e7 31 L."""
    result = record_when(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=31)))
    assert (result.data["reached"]["wins"], result.data["reached"]["losses"]) == (1, 1)
    assert (result.data["fell_short"]["wins"], result.data["fell_short"]["losses"]) == (1, 0)
    assert result.answer.startswith(f"Boston Celtics record when Jayson Tatum had 31+ points, {S} regular season:")
    assert result.data["headline"] == f"Boston Celtics record when Jayson Tatum had 31+ points, {S} regular season"
    assert result.data["notes"] and result.data["notes"][0].startswith("Over the")


def test_record_when_refuses_what_it_cannot_whitelist(league: AnswerContext) -> None:
    # Each refused by the fact missing (Phase 2, step 3).
    said = [
        record_when(league, Reading.from_slots(_slots(player="Jayson Tatum", **slots))).answer
        for slots in ({"stat": "double_double", "threshold": 1}, {"stat": "points"}, {"stat": "points", "threshold": 0})
    ]
    assert said == [
        "A record in the games over a line cannot be read over 'double_double' - it has no per-game box-score column.",
        "A record in the games over a line needs the number of points each game has to reach, and none was read.",
        "A threshold of 0 counts every game - there is no line there to keep games past.",
    ]
    # A threshold that is not a number never gets as far as the template.
    with pytest.raises(ValueError, match="threshold"):
        Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=True))


# ---------------- record_when, the relation's cells (step 3, C2) ----------------
#
# Tatum's played games this season with a recorded box score: e1 (home, vs
# LAL, 30, W), e4 (away, vs PHI, off the bench, 35, W), e7 (away, vs LAL, 31,
# L) - e2 is a DNP, e3 has no row, e5's box score is blank.


def test_record_when_narrows_by_venue_and_says_so(league: AnswerContext) -> None:
    """Only e1 is a home game; e4 and e7 are on the road."""
    result = record_when(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=25, venue="home")))
    assert (result.data["reached"]["games"], result.data["fell_short"]["games"]) == (1, 0)
    assert result.answer.startswith(f"Boston Celtics record when Jayson Tatum had 25+ points at home, {S} regular season:")


def test_record_when_narrows_by_opponent_and_says_so(league: AnswerContext) -> None:
    """Tatum vs the 76ers, across both seasons on record: e0b (last season,
    20, L), e0c (last season, 25, W), e4 (this season, 35, W)."""
    result = record_when(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=25, opponent="Philadelphia 76ers", span="career")))
    assert (result.data["reached"]["games"], result.data["reached"]["wins"]) == (2, 2)
    assert (result.data["fell_short"]["games"], result.data["fell_short"]["losses"]) == (1, 1)
    assert "vs the Philadelphia 76ers" in result.answer


def test_record_when_narrows_by_a_teammates_absence_and_says_so(league: AnswerContext) -> None:
    """Brown's only box score in Tatum's rookie (last) season is e0z, a game
    Tatum is not even in - so every one of Tatum's last-season games is
    "without" him: e0b (20, L), e0c (25, W), e0a (30, W), e0d (28, W)."""
    result = record_when(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=28, without="Jaylen Brown", season=S - 1)))
    assert (result.data["reached"]["games"], result.data["reached"]["wins"], result.data["reached"]["losses"]) == (2, 2, 0)
    assert (result.data["fell_short"]["games"], result.data["fell_short"]["wins"], result.data["fell_short"]["losses"]) == (2, 1, 1)
    assert "without Jaylen Brown" in result.answer


def test_record_when_narrows_by_a_named_half_of_the_split_and_says_so(league: AnswerContext) -> None:
    """Tatum comes off the bench only once: e4, 35 points, a win."""
    result = record_when(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=30, split="bench")))
    assert (result.data["reached"]["games"], result.data["fell_short"]["games"]) == (1, 0)
    assert "off the bench" in result.answer


def test_record_when_narrows_by_one_game_of_a_playoff_series_and_says_so(league: AnswerContext) -> None:
    """p1 and p2 are a two-game series vs Philadelphia; game 2 is p2 (26 points, by date, not insertion order)."""
    result = record_when(league, Reading.from_slots(_slots(player="Playoff Guy", stat="points", threshold=25, season_type=3, game_n=2)))
    assert (result.data["reached"]["games"], result.data["fell_short"]["games"]) == (1, 0)
    assert "game 2 of each series" in result.answer


def test_record_when_honors_since_and_says_so(league: AnswerContext) -> None:
    """Since last season: every one of Tatum's played games on record, both
    seasons - e0b (20, L), e0c (25, W), e0a (30, W), e0d (28, W), e1 (30, W),
    e4 (35, W), e7 (31, W)."""
    result = record_when(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=25, since=S - 1)))
    assert (result.data["reached"]["games"], result.data["fell_short"]["games"]) == (6, 1)
    assert result.answer.startswith(f"Boston Celtics record when Jayson Tatum had 25+ points, since {S - 1} ({S - 1}-{S} regular seasons):")


def test_record_when_honors_since_and_until_together_and_says_so(league: AnswerContext) -> None:
    """``condition_span_label`` used to read ``since`` only (ISSUES.md), so
    a range with both ends named ("from 2019-20 to 2021-22") was labeled as
    though it were still open-ended - even though the player branch's own
    narrowing (``condition_player``/``scoped_player``) already bounded the
    games correctly; only the LABEL lagged. Bounded to just `last`
    (``since=until={S - 1}``), only Tatum's 4 games that season count -
    e0b (20, L), e0c (25, W), e0a (30, W), e0d (28, W) - against 7 across
    both seasons with no upper bound (the test just above)."""
    bounded = record_when(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=25, since=S - 1, until=S - 1)))
    assert (bounded.data["reached"]["games"], bounded.data["fell_short"]["games"]) == (3, 1)
    assert bounded.answer.startswith(f"Boston Celtics record when Jayson Tatum had 25+ points, from {S - 1} through {S - 1} ({S - 1} regular season):")


def test_record_when_settles_season_n_and_says_so(league: AnswerContext) -> None:
    """Tatum's 1st season on record (player_season_stats_deduped) is last
    season: e0b (20, L), e0c (25, W), e0a (30, W), e0d (28, W)."""
    result = record_when(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=25, season_n=1)))
    assert result.data["span"].startswith(f"in his 1st season ({S - 1}")
    assert (result.data["reached"]["games"], result.data["fell_short"]["games"]) == (3, 1)
    assert "in his 1st season (" in result.answer


def test_record_when_narrows_by_a_box_score_line_and_says_so(league: AnswerContext) -> None:
    """Every played row in this fixture carries exactly 3 assists (`Line`'s own
    comment), so a line every game already satisfies changes no number but
    does say so in the title - proof the filter reaches the query rather than
    being silently ignored, without needing engineered variance."""
    with_line = record_when(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=25, above=["at least 3 assists"])))
    without_line = record_when(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=25)))
    assert with_line.data["reached"] == without_line.data["reached"] and with_line.data["fell_short"] == without_line.data["fell_short"]
    assert "with at least 3 assists" in with_line.answer


def test_record_when_a_box_score_line_narrows_to_no_games(league: AnswerContext) -> None:
    """A line no played row meets admits none at all - the same fixture's
    fixed 3 assists, asked for under. Confirmed: the refusal this reaches is
    `common._no_games`'s box-score-count branch, which says "did not play in
    any of them" - true of games with no box score, false here, since Tatum
    played several; ISSUES.md records the finding."""
    empty = record_when(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=25, below=["under 3 assists"])))
    assert empty.data["games"] == 0
    assert "did not play in any of them" in empty.answer


def test_record_when_cannot_honor_a_relation_cell_without_a_player(league: AnswerContext) -> None:
    """The team branch settles no player, so it still cannot narrow by a
    teammate's absence, a box-score line or anything else only
    condition_player reads - refusing by name rather than silently answering
    the whole team's record. ``opponent``/``venue`` (step 3, C4) and
    ``since``/``game_n`` (step 3, C4b) are NOT in this list any more - see
    test_an_opponent_narrows_a_team_only_threshold_too,
    test_a_venue_narrows_a_team_only_threshold_too and
    test_a_team_only_threshold_honors_since below."""
    with pytest.raises(Unsupported, match=r"record_when cannot honor \['below'\]"):
        record_when(league, Reading.from_slots(_slots(team="Boston Celtics", stat="points", threshold=100, below=["under 3 assists"])))


def test_a_team_only_threshold_honors_since(league: AnswerContext) -> None:
    """``since`` (step 3, C4b): a team-only record now spans more than one
    season instead of refusing - the fixture's Celtics have real games in
    both `last` (e0z-e0d) and this season (e1-e7), and ``since=last`` reaches
    both. The exact tally is covered by the dedicated ``since`` tests in
    ``tests/query/test_team_templates.py``; this just confirms the shape
    answers rather than refuses, and says the span in the heading."""
    result = record_when(league, Reading.from_slots(_slots(team="Boston Celtics", stat="points", threshold=100, since=S - 1)))
    assert result.data["team"] == "Boston Celtics"
    assert result.data["reached"]["games"] + result.data["fell_short"]["games"] > 4
    assert f"since {S - 1}" in (result.answer or "")


def test_a_team_only_threshold_honors_until(league: AnswerContext) -> None:
    """``until`` (ISSUES.md): before this, `_record_when_team_answer` never
    passed it to `_span_of`, so bounding the range to just `last` (the
    fixture's ``since=until={S - 1}``) silently answered the same 11-game
    pool an open-ended ``since={S - 1}`` reaches instead. Bounded to just
    `last` (e0z-e0d), only 5 games count, and the label says the real range
    rather than an open-ended "since"."""
    bounded = record_when(league, Reading.from_slots(_slots(team="Boston Celtics", stat="points", threshold=100, since=S - 1, until=S - 1)))
    unbounded = record_when(league, Reading.from_slots(_slots(team="Boston Celtics", stat="points", threshold=100, since=S - 1)))
    assert bounded.data["reached"]["games"] + bounded.data["fell_short"]["games"] == 5
    assert unbounded.data["reached"]["games"] + unbounded.data["fell_short"]["games"] == 11
    assert f"from {S - 1} through {S - 1}" in (bounded.answer or "")
    assert f"since {S - 1} ({S - 1}-{S}" in (unbounded.answer or "")


def test_a_team_only_threshold_refuses_a_condition(league: AnswerContext) -> None:
    """The team branch settles no player, so a ``conditions`` entry (a
    teammate's start, bench game or line) has no subject to check it
    against - refused by name rather than silently answering the team's
    whole span as though the condition were never named (ISSUES.md)."""
    with pytest.raises(Unsupported, match=r"record_when cannot honor \['conditions'\]"):
        record_when(
            league,
            Reading.from_slots(_slots(team="Boston Celtics", stat="points", threshold=100, conditions=[{"player": "Jayson Tatum", "predicate": "started"}])),
        )


# ---------------- record_when, the team branch (ISSUES.md #144) ----------------
#
# The Celtics' six real games this season (e6 is a 0-0 placeholder with no
# winner, dropped by real_games): e1 110 W, e2 100 L, e3 120 W, e4 99 W,
# e5 101 W (team_box_stats NULL for both sides), e7 105 L. 4-2 overall.


def test_a_team_only_threshold_divides_the_teams_own_games(league: AnswerContext) -> None:
    """No player named at all - "what was the celtics record when they scored
    120 points" (ISSUES.md #144). Reached (>=105): e1 110 W, e3 120 W, e7 105 L
    (2-1). Fell short (<105): e2 100 L, e4 99 W, e5 101 W (2-1) - e5 included,
    since points reads the game's own score and needs no team_box_stats row."""
    result = record_when(league, Reading.from_slots(_slots(team="Boston Celtics", stat="points", threshold=105)))
    assert (result.data["reached"]["wins"], result.data["reached"]["losses"]) == (2, 1)
    assert (result.data["fell_short"]["wins"], result.data["fell_short"]["losses"]) == (2, 1)
    assert result.answer.startswith(f"Boston Celtics record when they had 105+ points, {S} regular season:")
    assert "player" not in result.data


def test_a_team_only_threshold_needs_no_player(league: AnswerContext) -> None:
    """The router's own recorded output for this shape carries no `player`
    slot at all (tests/query/test_router.py,
    test_a_record_when_about_a_team_gains_no_player) - confirming record_when
    itself accepts that shape rather than raising "record_when needs a
    player", the wrong cause for a question that never named anybody."""
    result = record_when(league, Reading.from_slots(_slots(team="Boston Celtics", stat="points", threshold=100)))
    assert result.data["team"] == "Boston Celtics"


def test_a_bare_threshold_names_the_real_missing_thing(league: AnswerContext) -> None:
    """Neither a player nor a team - unanswerable, and the refusal says so
    rather than naming only the player half (the mirror-image bug AGENTS.md
    warns about: a wrong cause reads as honest)."""
    assert record_when(league, Reading.from_slots(_slots(stat="points", threshold=100))).answer == "A record in the games over a line needs a player or a team to read it for, and neither was named."


def test_a_team_rebounds_threshold_reads_oreb_plus_dreb_not_totalrebounds(league: AnswerContext) -> None:
    """The fixture's totalRebounds (40) is deliberately not
    offensiveRebounds + defensiveRebounds (35) - see `_game`'s own comment.
    36 is between them: reading the stale totalRebounds column would put
    every game in the reached bucket; reading oreb+dreb (what _TEAM_LINE
    already trusts, AGENTS.md) puts every game in fell_short instead."""
    result = record_when(league, Reading.from_slots(_slots(team="Boston Celtics", stat="rebounds", threshold=36)))
    assert result.data["reached"]["games"] == 0
    assert result.data["fell_short"]["games"] == 5, "the 5 games with a team_box_stats row; e5's is NULL"


def test_a_team_non_points_threshold_excludes_the_empty_box_game(league: AnswerContext) -> None:
    """e5's team_box_stats row is NULL for both sides (the 2013-2018
    Chicago/New Orleans shape, AGENTS.md "Whole team-seasons of box scores
    are empty") - unlike `points`, `assists` cannot read it, so it is in
    neither row and the answer says one game is missing."""
    result = record_when(league, Reading.from_slots(_slots(team="Boston Celtics", stat="assists", threshold=15)))
    assert result.data["reached"]["games"] == 5
    assert result.data["fell_short"]["games"] == 0
    assert "1 of their games in that span have no assists figure on record" in result.answer


def test_a_team_threshold_refuses_a_stat_with_no_team_figure(league: AnswerContext) -> None:
    """minutes is a real record_when stat for a PLAYER, but a team has no
    minutes total - the refusal names that, not "record_when needs a
    player" (the wrong cause: a team WAS named)."""
    with pytest.raises(Unsupported, match="record_when has no team figure for minutes"):
        record_when(league, Reading.from_slots(_slots(team="Boston Celtics", stat="minutes", threshold=240)))


def test_an_opponent_narrows_a_team_only_threshold_too(league: AnswerContext) -> None:
    """Step 3, C4: the Celtics' four games against the Lakers this season are
    e1 110 W (home), e2 100 L (away), e5 101 W (home), e7 105 L (away).
    Narrowed to LAL and a 105-point threshold: reached (>=105) is e1 (W) and
    e7 (L), 1-1; fell short is e2 (L) and e5 (W), 1-1 - the other two games
    (both vs Philadelphia) excluded from both rows."""
    result = record_when(league, Reading.from_slots(_slots(team="Boston Celtics", stat="points", threshold=105, opponent="Los Angeles Lakers")))
    assert (result.data["reached"]["wins"], result.data["reached"]["losses"]) == (1, 1)
    assert (result.data["fell_short"]["wins"], result.data["fell_short"]["losses"]) == (1, 1)
    assert result.data["reached"]["games"] == 2 and result.data["fell_short"]["games"] == 2
    assert "vs the Los Angeles Lakers" in result.answer


def test_a_venue_narrows_a_team_only_threshold_too(league: AnswerContext) -> None:
    """The Celtics' three home games this season are e1 110 W, e3 120 W, e5
    101 W - all wins, so at a 105-point threshold reached is 2-0 (e1, e3) and
    fell short is 1-0 (e5), with none of their three road games counted."""
    result = record_when(league, Reading.from_slots(_slots(team="Boston Celtics", stat="points", threshold=105, venue="home")))
    assert (result.data["reached"]["games"], result.data["reached"]["wins"]) == (2, 2)
    assert (result.data["fell_short"]["games"], result.data["fell_short"]["wins"]) == (1, 1)
    assert "at home" in result.answer


def test_a_team_only_threshold_says_which_fact_is_missing_for_a_narrowing_with_no_games(old_postseason_and_cup_final: AnswerContext) -> None:
    """``old1`` (1991 postseason) is the Celtics' only playoff game on record
    for that span, and it was against the Lakers - narrowed to the 76ers
    instead, the pool is empty, and which fact is missing is the MATCH, not
    the span: "played 1 games ... none of them vs the Philadelphia 76ers",
    not the false "no games in the 1991 postseason" (`condition_team_no_games`,
    step 3, C4)."""
    result = record_when(old_postseason_and_cup_final, Reading.from_slots(_slots(team="Boston Celtics", stat="points", threshold=10, season=1991, season_type=3, opponent="Philadelphia 76ers")))
    assert result.data["games"] == 0
    assert "played 1 game" in result.answer and "none of them vs the Philadelphia 76ers" in result.answer
    assert "no games with a result" not in result.answer


def test_a_streaks_opponent_and_venue_narrow_a_teams_own_run_too(league: AnswerContext) -> None:
    """Step 3, C4: narrowed to home games only, the Celtics won all three
    (e1, e3, e5) - their whole home slate, in order - so the streak is 3, not
    the 1-game runs their overall 4-2 record (interrupted by two road losses)
    would otherwise show at this same threshold-free "wins" question."""
    home = streak(league, Reading.from_slots(_slots(team="Boston Celtics", kind="win", venue="home")))
    assert home.data["streaks"][0]["length"] == 3
    assert "at home" in (home.answer or "")
    against_phi = streak(league, Reading.from_slots(_slots(team="Boston Celtics", kind="win", opponent="Philadelphia 76ers")))
    assert against_phi.data["streaks"][0]["length"] == 2  # e3, e4 - both wins
    assert "vs the Philadelphia 76ers" in (against_phi.answer or "")


def test_a_league_wide_streak_still_refuses_venue_and_opponent(league: AnswerContext) -> None:
    """Unlike a named team (just above), the league-wide streak (nobody
    named at all) has no single team's rival or home/road split to read -
    `compose.plan._streak_league_cells` refuses by name rather than
    silently narrowing nothing or picking one team to mean."""
    with pytest.raises(Unsupported, match=r"streak cannot honor \['venue'\] without a named team or player"):
        streak(league, Reading.from_slots(_slots(kind="win", venue="home")))
    with pytest.raises(Unsupported, match=r"streak cannot honor \['opponent'\] without a named team or player"):
        streak(league, Reading.from_slots(_slots(kind="win", opponent="Boston Celtics")))


def test_a_league_streak_honors_until(league: AnswerContext) -> None:
    """``until`` (ISSUES.md): before this, `_streak_league_team_branch` never
    passed it to `_span_of` either, so a league-wide search bounded to just
    `last` (the fixture's ``since=until={S - 1}``) silently searched every
    season since instead, surfacing the Celtics' tied 3-game streak this
    season (2025-11-04 to 2025-11-08) alongside their real, in-range one -
    turning a lone leader into a tie the range never asked about."""
    bounded = streak(league, Reading.from_slots(_slots(kind="win", since=S - 1, until=S - 1)))
    unbounded = streak(league, Reading.from_slots(_slots(kind="win", since=S - 1)))
    assert len(bounded.data["streaks"]) == 2
    assert len(unbounded.data["streaks"]) == 4
    assert f"from {S - 1} through {S - 1}" in (bounded.answer or "")
    assert "shared" not in bounded.answer.split("\n")[0]
    assert "shared" in unbounded.answer.split("\n")[0]


def test_a_team_turnovers_threshold_reads_totalturnovers() -> None:
    """DATA.md ("The team box `turnovers` column is zero before 2013")
    establishes `totalTurnovers` as ESPN's right team-turnover figure in
    every era, and the bare `turnovers` column as a different number (the
    player-box sum, repaired in at load time) - not a stricter reading of
    the same fact. record_when's team branch reads the former, through the
    team compiler's own figure (`compose.team.TEAM_BOX_COLUMNS`)."""
    from association.query.compose.records import _record_when_team_stat
    from association.query.compose.team import TEAM_BOX_COLUMNS

    assert TEAM_BOX_COLUMNS["turnovers"] == "tbs.totalTurnovers"
    assert _record_when_team_stat("turnovers", 15) == ("turnovers", 15)


def test_a_named_player_beats_the_team_branch_end_to_end(league: AnswerContext) -> None:
    """The two branches meeting, which is what the pair of changes could break.
    A question naming a player is about HIM even where the subject grammar does
    not fire, and one naming nobody is about the team. Run through
    `scope_from_question` exactly as the pipeline runs it."""
    from association.query.subject import apply_subject, read_subject

    def answered(question: str, **slots: Any) -> str:
        given = Scope.from_slots(_slots(**slots))
        applied = apply_subject(read_subject(league.con, question, "record_when", given), given, intent="record_when")
        return (record_when(league, Reading(scope=applied.scope)).answer or "").splitlines()[0]

    named = answered("celtics record with 20+ points from jayson tatum", stat="points", threshold=20, team="Boston Celtics")
    assert "Jayson Tatum had 20+ points" in named
    team = answered("celtics record when they scored 100 points", stat="points", threshold=100, team="Boston Celtics")
    assert "when they had 100+ points" in team


def test_record_when_still_restores_a_player_the_question_names() -> None:
    """`record_when` stays in PLAYER_REQUIRED_INTENTS even with a team branch,
    and the team branch is why it is safe rather than why it should leave.

    Dropping it was measured and reverted. `subject._apply_restored_player`
    restores only where the question names EXACTLY ONE player, so "what was the
    celtics record when they scored 120 points" - which names none - reaches the
    team branch either way. What the removal cost was the other side: "76ers
    record with 20+ points from tyrese maxey" names him plainly, the subject
    grammar does not fire on that wording, and without the restore it was
    answered "Philadelphia 76ers record when THEY had 20+ points" - a fluent
    answer to a different question, which is the failure shape this project
    keeps producing."""
    from association.query.reading import PLAYER_REQUIRED_INTENTS

    assert "record_when" in PLAYER_REQUIRED_INTENTS


# ---------------- player_matchup ----------------


def test_a_matchup_is_the_games_both_played_on_opposite_teams(league: AnswerContext) -> None:
    """e1 and e7. Not e2 (Tatum DNP), and not e5, where the Celtics' box score
    is missing - which is said."""
    result = player_matchup(league, Reading.from_slots(_slots(players=["LeBron James", "Jayson Tatum"])))
    assert result.data["meetings"] == 2
    assert result.data["wins"] == {"LeBron James": 1, "Jayson Tatum": 1}
    assert result.data["averages"]["Jayson Tatum"]["points"] == pytest.approx(30.5)
    assert "1 game between their teams" in result.answer


def test_teammates_never_met_and_the_answer_says_why(league: AnswerContext) -> None:
    answer = player_matchup(league, Reading.from_slots(_slots(players=["Jayson Tatum", "Jaylen Brown"]))).answer
    assert answer.endswith("they were teammates in all 3 games they both played.")


def test_a_matchup_since_a_season_reaches_back_that_far_and_no_further(league: AnswerContext) -> None:
    """``since`` is a scope on the relation: every season from the one named,
    where the default is this season alone. LeBron and Tatum met twice last
    season and twice this one with both playing."""
    this_season = player_matchup(league, Reading.from_slots(_slots(players=["LeBron James", "Jayson Tatum"]))).data["meetings"]
    since_last = player_matchup(league, Reading.from_slots(_slots(players=["LeBron James", "Jayson Tatum"], since=S - 1))).data["meetings"]
    assert (this_season, since_last) == (2, 4)


def test_a_log_since_a_season_reaches_back_that_far(league: AnswerContext) -> None:
    from test_templates import game_log  # the compiler's, game_log's template retired (compose.COMPILED_INTENTS)

    this_season = game_log(league, Reading.from_slots(_slots(player="Jayson Tatum", limit=50))).data["games"]
    since_last = game_log(league, Reading.from_slots(_slots(player="Jayson Tatum", limit=50, since=S - 1))).data["games"]
    assert len(since_last) > len(this_season)
    assert {g["season"] for g in since_last} == {S - 1, S}


def test_a_matchup_needs_two_different_players(league: AnswerContext) -> None:
    assert player_matchup(league, Reading.from_slots(_slots(players=["Jayson Tatum"]))).answer == "A matchup needs two players, and only Jayson Tatum was read."
    with pytest.raises(Unsupported):
        player_matchup(league, Reading.from_slots(_slots(players=["Jayson Tatum", "Tatum"])))


# ---------------- streak ----------------


def test_a_teams_streak_skips_a_game_that_has_no_result(league: AnswerContext) -> None:
    """e3, e4, e5: three wins with the placeholder e6 sitting between e3 and
    e4. Read as a loss, it would split the run in two."""
    result = streak(league, Reading.from_slots(_slots(team="Boston Celtics", kind="win")))
    assert result.data["streaks"][0] == {"length": 3, "from": f"{S - 1}-11-04", "to": f"{S - 1}-11-08", "open": False}


def test_a_teams_streak_does_not_cross_seasons(league: AnswerContext) -> None:
    """Last season ends on three wins and this one opens with a fourth.
    Counted across the break that is a run of 4; the record book says 3."""
    result = streak(league, Reading.from_slots(_slots(team="Boston Celtics", kind="win", span="career")))
    assert result.data["streaks"][0]["length"] == 3
    assert "counted within one season" in result.answer


def test_a_players_run_skips_missed_games_and_stops_at_unknown_ones(league: AnswerContext) -> None:
    """25+ points: e0c 25, e0a 30, e0d 28, e1 30, (e2 DNP, e3 no row), e4 35,
    then e5 with no box score, then e7 31. Missed games neither extend nor end
    the run; the unknown one ends it. A run of 5 across the season break."""
    result = streak(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=25, span="career")))
    assert result.data["streaks"][0]["length"] == 5
    assert "ends a run" in result.answer


def test_the_league_streak_reports_a_tie_as_a_tie(league: AnswerContext) -> None:
    """25+ points this season: Tatum e1, e4 (then e5 unknown); LeBron e1, e2
    (then 20 in e5)."""
    result = streak(league, Reading.from_slots(_slots(stat="points", threshold=25)))
    assert [s["length"] for s in result.data["streaks"][:2]] == [2, 2]
    assert result.answer.startswith("Jayson Tatum and LeBron James shared the longest")


def test_the_leagues_longest_winning_streak_names_the_team(league: AnswerContext) -> None:
    result = streak(league, Reading.from_slots(_slots(kind="win")))
    assert result.answer.startswith(f"Boston Celtics had the longest winning streak of the {S} regular season: 3 games.")
    assert result.data["headline"] == f"Boston Celtics had the longest winning streak of the {S} regular season: 3 games."
    assert result.data["notes"]  # the "only games he played count" rule, at minimum


def test_a_losing_streak_is_a_run_of_losses(league: AnswerContext) -> None:
    result = streak(league, Reading.from_slots(_slots(team="Philadelphia 76ers", kind="loss")))
    assert result.data["streaks"][0]["length"] == 2
    assert result.data["headline"] == (result.answer or "").split("\n")[0]
    assert result.data["notes"]


def test_a_stat_without_a_threshold_is_not_read_as_a_winning_streak(league: AnswerContext) -> None:
    """ "Most consecutive double-doubles" must not come back as the Celtics'
    best run of wins."""
    # Each refused by the fact missing (Phase 2, step 3).
    said = [
        streak(league, Reading.from_slots(_slots(team="Boston Celtics", **slots))).answer
        for slots in ({"stat": "double_double"}, {"stat": "points"}, {"threshold": 30}, {"stat": "points", "threshold": 0})
    ]
    assert said == [
        "A streak cannot be read over 'double_double' - it has no per-game box-score column.",
        "A streak needs the number of points each game has to reach, and none was read.",
        "A streak of games reaching 30 needs the stat they reach it in, and none was read.",
        "A threshold of 0 counts every game - there is no line there to keep games past.",
    ]
    assert (
        streak(league, Reading.from_slots(_slots(team="Boston Celtics", stat="points", threshold=30))).answer
        == "A team's streak is of wins or losses - a run of games reaching a number of points is read for a player, not a team."
    )


def test_the_models_word_for_a_winning_streak_is_not_a_stat(league: AnswerContext) -> None:
    assert streak(league, Reading.from_slots(_slots(team="Boston Celtics", stat="wins", kind="win"))).data["streaks"][0]["length"] == 3


# ---------------- streak, the relation's cells (step 3, C2) ----------------
#
# The same career run as test_a_players_run_skips_missed_games_and_stops_at_unknown_ones
# (25+ points: e0c, e0a, e0d, e1, e4 - a run of 5, broken by e5's missing box
# score before e7) is the baseline these narrow further.


def test_streak_narrows_by_venue_and_says_so(league: AnswerContext) -> None:
    """Tatum's home games only: e0c, e0a, e0d, e1 - e4 and e7 are on the road,
    and dropping them shortens the career run below the unnarrowed 5."""
    result = streak(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=25, span="career", venue="home")))
    assert result.data["streaks"][0]["length"] == 4
    assert "at home" in result.answer


def test_streak_narrows_by_opponent_and_says_so(league: AnswerContext) -> None:
    """Tatum vs the Lakers only: e0a, e0d, e1 (25+ each) then e7, with e5 -
    a missing box score, unaffected by the opponent narrowing since the
    "unseen" read is not filtered by it - sitting between e1 and e7 and
    ending the run there. A run of 3, not the unnarrowed 5: dropping e4 (vs
    Philadelphia) from the sequence shortens it further still."""
    result = streak(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=25, span="career", opponent="Los Angeles Lakers")))
    assert result.data["streaks"][0]["length"] == 3
    assert "vs the Los Angeles Lakers" in result.answer


def test_a_player_with_no_run_records_no_remark_he_is_not_told(league: AnswerContext) -> None:
    """A named player who never reached the line answers "never had a game
    with 60+ points" and nothing else, so it records no remark: the retired
    template wrote the run's rule ("only games he played count") before it
    knew there was no run, and the remark reached no answer (``compose.runs``
    attaches the rule only to a run; on the real warehouse, "tatum longest
    streak of 25 point games vs the knicks" recorded it and said nothing)."""
    from association.query.notes import collect

    with collect() as remarks:
        result = streak(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=60, span="career")))
    assert result.data["streaks"] == []
    assert "never had a game with 60+ points" in result.answer
    assert remarks.notes == []


def test_a_league_with_no_run_records_no_remark_it_is_not_told(league: AnswerContext) -> None:
    """The league's longest winning streak in a postseason nothing was played
    in answers "No team has a game with a result" and records no remark: the
    team-season rule was written before anyone knew there was no run, and
    reached no answer."""
    from association.query.notes import collect

    with collect() as remarks:
        result = streak(league, Reading.from_slots({"kind": "win", "season": 1990, "season_type": 3}))
    assert result.data["streaks"] == []
    assert "no team has a game" in result.answer.lower()
    assert remarks.notes == []


def test_a_league_run_in_a_postseason_before_1994_is_the_floors_refusal() -> None:
    """The league's stat run over one postseason before 1993-94 - the seasons
    ESPN files under the year they began - never reaches a reader: the
    player box scores' floor refuses it first (``check_coverage``, run
    before any reader), which is why the retired presenter's own refusal of
    the misfiled label (``templates.splits._misfiled_postseason``) went
    with it, unreached."""
    for season in (1988, 1990, 1992, 1993):
        refusal = check_coverage("streak", Scope.from_slots({"stat": "points", "threshold": 30, "season": season, "season_type": 3}))
        assert refusal is not None and "1994" in refusal, season


def test_streak_narrows_by_a_teammates_absence_and_says_so(league: AnswerContext) -> None:
    """Brown's only last-season box score is a game Tatum is not even in, so
    every one of Tatum's last-season games is "without" him - the Celtics'
    longest winning run in games Tatum played that season, without Brown, is
    e0c/e0a/e0d (e0b is a loss first)."""
    result = streak(league, Reading.from_slots(_slots(player="Jayson Tatum", kind="win", without="Jaylen Brown", season=S - 1)))
    assert result.data["streaks"][0]["length"] == 3
    assert "without Jaylen Brown" in result.answer


def test_streak_narrows_by_a_named_half_of_the_split_and_says_so(league: AnswerContext) -> None:
    """Tatum's only bench game is e4, a win - a run of exactly one."""
    result = streak(league, Reading.from_slots(_slots(player="Jayson Tatum", kind="win", split="bench")))
    assert result.data["streaks"][0]["length"] == 1
    assert "off the bench" in result.answer


def test_streak_honors_since_and_says_so(league: AnswerContext) -> None:
    """Since this season alone (S), Tatum's recorded-points games are e1, e4,
    e7 - e5 sits between e4 and e7 with no box score, so it is a run of 2
    (e1, e4), not 3."""
    result = streak(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=25, since=S)))
    assert result.data["streaks"][0]["length"] == 2
    assert result.answer.startswith(f"Jayson Tatum's longest run of consecutive games with 25+ points, since {S} ({S} regular season):")


def test_streak_settles_season_n_and_says_so(league: AnswerContext) -> None:
    """His 2nd season on record is this one (S) - the same games as the
    `since` case above, addressed by ordinal instead."""
    result = streak(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=25, season_n=2)))
    assert result.data["streaks"][0]["length"] == 2
    assert "in his 2nd season (" in result.answer


def test_streak_narrows_by_one_game_of_a_playoff_series_and_says_so(league: AnswerContext) -> None:
    """Playoff Guy's game 2 of his one series is p2 alone - a run of exactly
    one game either way, but scoped to that game and said so."""
    result = streak(league, Reading.from_slots(_slots(player="Playoff Guy", stat="points", threshold=25, season_type=3, game_n=2)))
    assert result.data["streaks"][0]["length"] == 1
    assert "game 2 of each series" in result.answer


def test_streak_narrows_by_a_box_score_line_and_says_so(league: AnswerContext) -> None:
    """Every played row in this fixture carries exactly 3 assists, so a line
    every game already satisfies changes no run but does say so."""
    with_line = streak(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=25, span="career", above=["at least 3 assists"])))
    without_line = streak(league, Reading.from_slots(_slots(player="Jayson Tatum", stat="points", threshold=25, span="career")))
    assert with_line.data["streaks"][0]["length"] == without_line.data["streaks"][0]["length"]
    assert "with at least 3 assists" in with_line.answer


def test_streak_cannot_honor_a_relation_cell_without_a_player(league: AnswerContext) -> None:
    """Team and league streaks settle no player, so neither can narrow by an
    ordinal season - only condition_player reads that, and neither branch
    calls it. ``since`` is NOT in this list any more (step 3, C4b) - see
    test_a_team_streak_honors_since below."""
    with pytest.raises(Unsupported, match=r"streak cannot honor \['season_n'\]"):
        streak(league, Reading.from_slots(_slots(kind="win", season_n=1)))


def test_a_team_streak_honors_since(league: AnswerContext) -> None:
    """``since`` (step 3, C4b): searched across every season from it on
    rather than only the current one - not across the boundary between them,
    since a team's own run is still counted within one season
    (``_longest_runs_sql``' own ``("team_id", "season")`` partition, unchanged by
    this). The fixture's Celtics have a 3-game win streak in BOTH `last`
    (e0c, e0a, e0d) and this season (e3, e4, e5); with `since` bounded to the
    current season alone (the default), only the second would be found. The
    exact tally is covered by the dedicated ``since`` tests in
    ``tests/query/test_team_templates.py``; this confirms the shape answers
    rather than refuses, over the wider span."""
    result = streak(league, Reading.from_slots(_slots(team="Boston Celtics", kind="win", since=S - 1)))
    assert result.data["streaks"]
    assert result.data["streaks"][0]["length"] == 3
    assert f"since {S - 1}" in (result.answer or "")


def test_a_team_streak_honors_until(league: AnswerContext) -> None:
    """``until`` (ISSUES.md): before this, `_streak_team` never passed it to
    `_span_of`, so a run bounded to just `last` (the fixture's
    ``since=until={S - 1}``) silently searched every season since instead,
    surfacing the tied 3-game streak this season also has (e3, e4, e5) as a
    "Matched by" line that a range ending at `last` should never see."""
    bounded = streak(league, Reading.from_slots(_slots(team="Boston Celtics", kind="win", since=S - 1, until=S - 1)))
    unbounded = streak(league, Reading.from_slots(_slots(team="Boston Celtics", kind="win", since=S - 1)))
    assert len(bounded.data["streaks"]) == 1
    assert len(unbounded.data["streaks"]) == 2
    assert f"from {S - 1} through {S - 1}" in (bounded.answer or "")
    assert "Matched by" not in (bounded.answer or "")
    assert "Matched by" in (unbounded.answer or "")


def test_a_team_streak_refuses_a_condition(league: AnswerContext) -> None:
    """Team and league streaks settle no player, so a ``conditions`` entry
    has no subject to check it against - refused by name rather than
    silently narrowing nothing (ISSUES.md)."""
    with pytest.raises(Unsupported, match=r"streak cannot honor \['conditions'\]"):
        streak(league, Reading.from_slots(_slots(team="Boston Celtics", kind="win", conditions=[{"player": "Jayson Tatum", "predicate": "started"}])))


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
def old_postseason_and_cup_final(league: AnswerContext) -> AnswerContext:
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
def test_a_playoff_season_label_no_longer_refuses_and_finds_nothing_with_no_matching_games(league: AnswerContext, season: int) -> None:
    """Before step 3, C4 this refused, naming the wrong year under the ESPN
    label (``_misfiled_postseason``). Now the label is not read at all - the
    postseason is selected by the calendar year the games were actually
    played in (``team_games.py``) - so asking under the OLD label finds
    nothing, when nothing was actually played in that calendar year, rather
    than a claim about which year the label really means."""
    for template, slots in ((streak, {"team": "Boston Celtics", "kind": "win"}), (streak, {"kind": "win"}), (player_splits, {"team": "Boston Celtics"})):
        answer = template(league, Reading.from_slots({**slots, "season": season, "season_type": 3})).answer or ""
        assert "postseason is the" not in answer  # the old misfiled-label refusal
        assert "no games" in answer.lower() or "no team has a game" in answer.lower()


def test_a_postseason_labeled_1990_is_read_as_the_1991_playoffs_by_calendar_year(old_postseason_and_cup_final: AnswerContext) -> None:
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
        empty = template(old_postseason_and_cup_final, Reading.from_slots({**slots, "season": 1990, "season_type": 3}))
        assert empty.data.get("games") == 0, f"{template.__name__} found a game under the 1990 LABEL, which is really 1991"
        found = template(old_postseason_and_cup_final, Reading.from_slots({**slots, "season": 1991, "season_type": 3}))
        assert found.data.get("games") != 0, f"{template.__name__} found no game under 1991, the calendar year old1 was actually played"


def test_a_teams_games_over_a_span_holding_the_cup_final_count_it(old_postseason_and_cup_final: AnswerContext) -> None:
    """The Celtics played 7 regular-season games this season with the Cup
    final (``cup``) added to the 6 ``league`` already holds - a plain game
    list or split is not a win-loss RECORD, so it does not exclude the cup
    final the way ``team_record`` does (``team_games.py``: "a plain game
    list or head-to-head count should not [exclude it]")."""
    result = player_splits(old_postseason_and_cup_final, Reading.from_slots(_slots(team="Boston Celtics", split="wins_losses")))
    assert result.data["games"] == 7


def test_a_player_listed_once_is_told_so_in_the_singular(league: AnswerContext) -> None:
    league.con.execute("DELETE FROM player_box_stats WHERE athlete_id = ? AND season = ? AND event_id <> 'e2'", [TATUM, S])
    assert player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum"))).answer.endswith("box score in the 2026 regular season but did not play in it.".replace("2026", str(S)))


@pytest.fixture
def old_franchises(league: AnswerContext) -> AnswerContext:
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


def test_a_streak_across_seasons_names_each_team_as_it_was_then(old_franchises: AnswerContext) -> None:
    """Every all-seasons team streak used to end "franchises are named as they
    are today", because that is what it did: a 2001 Nets run read Brooklyn
    Nets. Each run lies inside one season, so it is named for it."""
    answer = streak(old_franchises, Reading.from_slots(_slots(kind="win", span="career"))).answer or ""
    assert "New Jersey Nets (2001)" in answer and "Brooklyn" not in answer
    assert "named as they are today" not in answer


def test_a_matchup_log_abbreviates_each_team_for_the_season_of_the_meeting(old_franchises: AnswerContext) -> None:
    """The meeting log read "BKN 100-90 MEM" for a 2001 game in New Jersey
    against Vancouver."""
    answer = player_matchup(old_franchises, Reading.from_slots(_slots(players=["Kenyon Martin", "Pau Gasol"], season=2001))).answer or ""
    assert "NJ 100-90 VAN" in answer and "BKN" not in answer


# ---------------- player_splits: venue and opponent ----------------


def test_a_venue_narrows_a_players_games(league: AnswerContext) -> None:
    """Tatum's three games this season are e1 (home), e4 and e7 (away); a
    venue narrows the games the splits are computed over, not just which
    row of the home/away split is shown.

    The heading names it through ``Narrowed.filters()`` (step 3, C2) rather
    than a phrase this template composed for itself, the way every other
    template on the relation already does - "at home", not "(at home)"."""
    result = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", venue="home")))
    assert result.data["games"] == 1
    assert "Jayson Tatum at home," in (result.answer or "")
    rows = _rows(result, "home_away")
    assert (rows["home"]["games"], rows["away"]["games"]) == (1, 0)


def test_an_opponent_narrows_a_players_games(league: AnswerContext) -> None:
    """Tatum played the Lakers twice: e1 at home (30 points) and e7 on the
    road (31)."""
    result = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", opponent="Los Angeles Lakers")))
    assert result.data["games"] == 2
    assert "Jayson Tatum vs the Los Angeles Lakers," in (result.answer or "")


def test_venue_and_opponent_narrow_together(league: AnswerContext) -> None:
    """Only e7 - Tatum's road game against the Lakers - matches both.
    ``Narrowed.filters()`` names the opponent before the venue - "vs the
    Lakers on the road", not "on the road vs the Lakers" - the order every
    template reading it already uses (player_stat, game_log)."""
    result = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", venue="away", opponent="Los Angeles Lakers")))
    assert result.data["games"] == 1
    assert "vs the Los Angeles Lakers on the road" in (result.answer or "")
    assert "1 game he played" in (result.answer or ""), "not '1 games' - the pluralization a venue/opponent narrowing exposes"


def test_condition_player_reads_a_scope_beside_the_opponent_its_caller_resolved(league: AnswerContext) -> None:
    """The shared step under player_splits, record_when and streak reads the
    typed Scope (ROADMAP plan item 6, step (d)). player_splits resolves the
    opponent before the player; a Scope holds names, so the team it resolved
    goes beside the Scope. That and the Scope's own opponent name both narrow
    Tatum's games to his two against the Lakers (e1, e7)."""
    from dataclasses import replace

    from association.query.conditions import _PLAYER_GAME_TABLES
    from association.query.entities import Entity, resolved_team
    from association.query.player_games import games_subquery
    from association.query.player_relation import condition_player, condition_scope
    from association.query.reading import Scope

    lakers = resolved_team(league.con, "Los Angeles Lakers")
    assert isinstance(lakers, Entity)
    within = condition_scope(None, None, 2, _PLAYER_GAME_TABLES)
    scope = Scope.from_slots(_slots(player="Jayson Tatum"))
    reads = [
        condition_player(league.con, scope, "needs a player", within, opponent=lakers),
        condition_player(league.con, replace(scope, opponent="Los Angeles Lakers"), "needs a player", within),
    ]
    for read in reads:
        assert not isinstance(read, Unanswered)
        player, narrowed = read
        assert (player.name, narrowed.opponent) == ("Jayson Tatum", lakers)
        sql, params = games_subquery(narrowed, box_source(league.con))
        assert league.con.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone() == (2,)


def test_a_blank_opponent_narrows_a_players_splits_to_nothing(league: AnswerContext) -> None:
    """player_splits resolves the opponent before the player and hands
    condition_player the team it found beside a Scope with its own opponent
    cleared (plan item 6, step (d)). A blank name is nobody to the first
    resolution; left in the Scope, the shared step would read it a second time
    and refuse ("no team named") where the answer has always been his splits
    with no opponent narrowing."""
    plain = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum")))
    blank = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", opponent=" ")))
    assert (blank.answer, blank.data) == (plain.answer, plain.data)


def test_a_venue_narrows_a_teams_own_games_too(league: AnswerContext) -> None:
    """The Celtics' three home games this season (e1, e3, e5) are all wins."""
    result = player_splits(league, Reading.from_slots(_slots(team="Boston Celtics", venue="home")))
    assert result.data["games"] == 3
    rows = _rows(result, "wins_losses")
    assert (rows["wins"]["games"], rows["losses"]["games"]) == (3, 0)


def test_an_opponent_narrows_a_teams_own_games_too(league: AnswerContext) -> None:
    """The Celtics played the Lakers four times: e1, e5 at home (both wins) and e2, e7 on the road (both losses)."""
    result = player_splits(league, Reading.from_slots(_slots(team="Boston Celtics", opponent="Los Angeles Lakers")))
    assert result.data["games"] == 4
    rows = _rows(result, "home_away")
    assert (rows["home"]["wins"], rows["away"]["wins"]) == (2, 0)


def test_a_home_away_split_conflicts_with_an_already_narrowed_venue(league: AnswerContext) -> None:
    """Asking to break games out by home/away while also filtering to one of
    the two asks the same axis twice."""
    with pytest.raises(Unsupported):
        player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", split="home_away", venue="home")))


def test_a_real_limit_is_refused_rather_than_answering_the_whole_span(league: AnswerContext) -> None:
    """player_splits has no notion of "his last N games" - answering under
    that framing with the whole span (all 3 of Tatum's games) would be the
    silent substitution this whole module exists to prevent."""
    with pytest.raises(Unsupported):
        player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", venue="home", limit=4)))


def test_a_bare_limit_of_one_is_not_refused(league: AnswerContext) -> None:
    """The router's own filler value elsewhere (router._route_side_and_order
    drops a limit of 1 for the same reason) - and here it changes nothing,
    since the games a venue narrows to are shown in full either way."""
    result = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", venue="home", limit=1)))
    assert result.data["games"] == 1


def test_a_bare_limit_never_windows_a_condition_template(league: AnswerContext) -> None:
    """`scoped_games` reads a bare `limit` as "the newest N" for the templates
    that honor a window (step 3, C5); a split, a record and a run exclude
    `order` by declaration and are read over every game in the span, so the
    router's filler `limit: 1` must not cut them to one game - it did, for
    three recorded questions ("76ers record when Maxey scores 20+": 1-0 over
    1 game instead of 35-28 over 63), until `common.whole_span`. The team
    branches go through the same rule."""
    whole = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum")))
    assert whole.data["games"] > 1
    assert player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", limit=1))).data["games"] == whole.data["games"]
    team_whole = player_splits(league, Reading.from_slots(_slots(team="Boston Celtics")))
    assert team_whole.data["games"] > 1
    assert player_splits(league, Reading.from_slots(_slots(team="Boston Celtics", limit=1))).answer == team_whole.answer


# ---------------- player_splits: step 3, C2 - the relation's own cells ----------------


def test_without_narrows_a_players_splits(league: AnswerContext) -> None:
    """Previously `_player_splits_player` handed `condition_player` a copy of
    `slots` with `"without": None` - so this narrowing reached check_scope's
    declaration and nothing else. Over his career Brown played every one of
    Tatum's three games this season, but none of his four last season (he
    appears without Tatum only in e0z) - so "without Brown" leaves exactly
    those four, and the answer names it."""
    result = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", span="career", without="Jaylen Brown", split="home_away")))
    assert result.data["games"] == 4
    assert result.data["without"] == ["Jaylen Brown"]
    assert "without Jaylen Brown" in (result.answer or "")
    rows = _rows(result, "home_away")
    assert (rows["home"]["games"], rows["away"]["games"]) == (3, 1)  # e0c, e0a, e0d home; e0b away


def test_since_narrows_to_a_range_of_seasons(league: AnswerContext) -> None:
    """A third, older season Tatum played is outside the `since` window, so
    "since {S-1}" differs from both the plain current-season default (3
    games) and a full career that would also count the older one (8)."""
    c = league.con
    _game(c, "e00", f"{S - 2}-11-01T00:30Z", BOS, PHI, 100, 90, [_played(TATUM, BOS, 22)], season=S - 2)
    real_games.build_table(c, {"games", "teams", "player_box_stats"})
    result = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", since=S - 1)))
    assert result.data["games"] == 7  # e0b, e0c, e0a, e0d (S-1) + e1, e4, e7 (S) - not e00 (S-2)
    assert result.data["span"] == f"{S - 1}-{S} regular seasons"


def test_since_and_a_named_season_conflict(league: AnswerContext) -> None:
    """`condition_scope` used to take the `since` branch unconditionally,
    silently dropping a `season` slot named alongside it - the same pairing
    `_span_of` already refuses for game_log and player_stat."""
    with pytest.raises(Unsupported):
        player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", since=S - 1, season=S)))


def test_game_n_narrows_to_one_game_of_each_series(league: AnswerContext) -> None:
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
    result = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", season_type=3, game_n=2)))
    assert result.data["games"] == 1
    assert result.data["series_game"] == 2
    assert "game 2 of each series" in (result.answer or "")
    rows = _rows(result, "wins_losses")
    assert (rows["wins"]["games"], rows["losses"]["games"]) == (1, 0)


def test_a_named_half_of_starter_bench_narrows_the_games(league: AnswerContext) -> None:
    """ "as a starter" is a request to FILTER - the value `_split_side` reads
    out of the question for game_log and the other filtering templates - not
    a request for the starter/bench table over every game. Tatum started e1
    (30) and e7 (31) and came off the bench in e4 (35); split="starter"
    narrows to the two he started, while the CATEGORY shown is still the same
    two-row table it always was, folded back from the half the question
    named (previously this always raised: "starter"/"bench" were not in
    SPLIT_KINDS, so the fold-back in `_player_splits_answer` was dead code)."""
    result = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", split="starter")))
    assert result.data["games"] == 2
    assert result.data["started"] is True
    assert "as a starter" in (result.answer or "")
    rows = _rows(result, "starter_bench")
    assert rows["starter"]["games"] == 2
    assert rows["bench"]["games"] == 0
    assert rows["starter"]["points"] == pytest.approx(30.5)


def test_season_n_settles_to_the_year_once_the_player_is_known(league: AnswerContext) -> None:
    """His 1st season (season_n=1) is last season (S-1, 4 games) - a
    different year from the "now" this template defaults to, so the label
    the answer names has to follow the ordinal the relation settled rather
    than the `_Scope` built (as "now") before the player was known."""
    # The league fixture already holds Tatum's two seasons on record.
    first = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", season_n=1)))
    assert first.data["games"] == 4  # e0b, e0c, e0a, e0d
    assert first.data["span"] == f"{S - 1} regular season"
    # His 2nd season is this one, where the plain "no season named" default already lands.
    second = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", season_n=2)))
    assert second.data["games"] == player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum"))).data["games"] == 3


def test_a_season_n_past_his_career_is_refused_by_name(league: AnswerContext) -> None:
    # The league fixture already holds Tatum's two seasons on record.
    answer = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", season_n=5))).answer or ""
    assert "2 seasons on record" in answer


def test_above_and_below_narrow_which_games_the_splits_cover(league: AnswerContext) -> None:
    """Tatum's three counted games this season score 30, 35 and 31 points
    (e1, e4, e7); "at least 32" keeps e4 alone, "under 32" keeps the other
    two - refused here, before any name is resolved, if the line names no
    column at all (:func:`association.query.lines.measure_filters`)."""
    high = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", above="32 points")))
    assert high.data["games"] == 1
    assert high.data["measures"] == ["at least 32 points"]
    assert "at least 32 points" in (high.answer or "")
    low = player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", below="32 points")))
    assert low.data["games"] == 2
    assert "under 32 points" in (low.answer or "")
    with pytest.raises(Unsupported):
        player_splits(league, Reading.from_slots(_slots(player="Jayson Tatum", above="20 vibes")))


def test_a_matchup_without_a_teammate_narrows_the_first_players_games(league: AnswerContext) -> None:
    """The pair relation on the relation (yardstick-v2 F114 "curry vs lebron
    without kd"): LeBron met Tatum in e1 and e7 this season; Journeyman Guy,
    LeBron's teammate, played e1 and has no line in e7, so "without" him
    leaves e7 alone - and the heading says so."""
    result = player_matchup(league, Reading.from_slots(_slots(players=["LeBron James", "Jayson Tatum"], without=["Journeyman Guy"])))
    assert result.data["meetings"] == 1
    assert "without Journeyman Guy" in result.answer


def test_a_matchup_at_home_is_the_first_players_home_meetings(league: AnswerContext) -> None:
    """A venue narrows the first player's side: e7 is at LAL, e1 at BOS."""
    home = player_matchup(league, Reading.from_slots(_slots(players=["LeBron James", "Jayson Tatum"], venue="home")))
    assert home.data["meetings"] == 1
    assert "at home" in home.answer
    away = player_matchup(league, Reading.from_slots(_slots(players=["Jayson Tatum", "LeBron James"], venue="home")))
    assert away.data["meetings"] == 1


def test_a_matchup_whose_second_player_is_the_absent_teammate_says_so(league: AnswerContext) -> None:
    """A recorded case: "fox vs wembanyama without wembanyama" - with the
    absence honored, the meetings are empty by construction, and "never
    played against each other" would be a false sentence. Said instead."""
    result = player_matchup(league, Reading.from_slots(_slots(players=["LeBron James", "Jayson Tatum"], without=["Jayson Tatum"])))
    assert "both the player" in result.answer and result.data["message"]


# ---------------------------------------------------------------------------
# ROADMAP plan item 3, step A: the player condition `(player, side, predicate)`
# on the relation, read by every relation template through the shared step
# as a `conditions` slot. Brown's regular-season games in the fixture: e1
# (Tatum started, 30), e2 (Tatum DNP), e3 (vs PHI, no Tatum line), e4 (Tatum
# off the bench, 35), e5 (blank for both), e7 (Tatum started, 31); LeBron is
# on the other side in e1, e2, e5, e7.


def _brown_games(league: AnswerContext, *conditions: dict[str, Any]) -> int:
    return player_splits(league, Reading.from_slots(_slots(player="Jaylen Brown", split="home_away", conditions=list(conditions)))).data["games"]


def test_a_teammates_start_bench_and_line_are_conditions(league: AnswerContext) -> None:
    assert _brown_games(league, {"player": "Jayson Tatum", "side": "own", "predicate": "started"}) == 2  # e1, e7
    assert _brown_games(league, {"player": "Jayson Tatum", "side": "own", "predicate": "bench"}) == 1  # e4
    assert _brown_games(league, {"player": "Jayson Tatum", "side": "own", "predicate": "played"}) == 3  # e1, e4, e7
    assert _brown_games(league, {"player": "Jayson Tatum", "side": "own", "predicate": "reached", "stat": "points", "threshold": 31}) == 2  # e4 (35), e7 (31)


def test_an_opponent_side_condition_reads_the_other_teams_box_score(league: AnswerContext) -> None:
    assert _brown_games(league, {"player": "LeBron James", "side": "opponent", "predicate": "played"}) == 3  # e1, e2, e7
    assert _brown_games(league, {"player": "LeBron James", "side": "opponent", "predicate": "absent"}) == 2  # e3, e4 vs PHI
    # ANDed: LeBron on the other side and Tatum starting beside him.
    assert _brown_games(league, {"player": "LeBron James", "side": "opponent", "predicate": "played"}, {"player": "Jayson Tatum", "side": "own", "predicate": "started"}) == 2


def test_the_absent_condition_is_the_without_slot_word_for_word(league: AnswerContext) -> None:
    """The teammate absence every template read before is the own-side
    absent condition: the same games, the same phrase, tenure included."""
    old = player_splits(league, Reading.from_slots(_slots(player="Jaylen Brown", split="home_away", without=["Jayson Tatum"])))
    new = player_splits(league, Reading.from_slots(_slots(player="Jaylen Brown", split="home_away", conditions=[{"player": "Jayson Tatum", "side": "own", "predicate": "absent"}])))
    assert old.data["games"] == new.data["games"] == 2 and old.answer == new.answer  # e2 (DNP), e3 (no line)


def test_a_condition_the_relation_cannot_read_refuses(league: AnswerContext) -> None:
    # A predicate the relation does not read is refused at the Reading's door.
    with pytest.raises(ValueError, match="predicate"):
        _brown_games(league, {"player": "Jayson Tatum", "side": "own", "predicate": "dunked"})
    with pytest.raises(Unsupported, match="reached condition"):
        _brown_games(league, {"player": "Jayson Tatum", "side": "own", "predicate": "reached", "stat": "vibes", "threshold": 3})
    # A history's words state no condition (compose.plan.words_stated):
    # its presenter steps aside, and the compiler's sentence says what it read.
    assert unhonored_scoping("player_history", Scope.from_slots({"player": "Jaylen Brown", "stat": "points", "conditions": [{"player": "Jayson Tatum"}]}), words_stated("player_history")) == [
        "conditions"
    ]


def test_a_matchup_emptied_by_an_absence_says_what_it_counted(league: AnswerContext) -> None:
    """yardstick-v2 F114: "curry record vs lebron without kd" holds no
    meetings because "without" counts only the games the teammate missed
    while on the subject's team - so the answer says how many meetings there
    were in all, how many with the teammate beside him, and why the narrowed
    set is empty. This season Tatum met LeBron in e1 and e7 (e2 he sat), and
    Brown played both; last season Brown missed both meetings, so a career
    read has 2 to show."""
    result = player_matchup(league, Reading.from_slots(_slots(players=["Jayson Tatum", "LeBron James"], without=["Jaylen Brown"], season=S)))
    assert result.data["meetings"] == 0
    assert f"Over {S}-{S} they met 2 times in all, 2 of them with Jaylen Brown playing beside Jayson Tatum" in result.answer and "there were none among their meetings" in result.answer
    # ... and where the narrowed set holds a meeting, there is a matchup to show and no context.
    result = player_matchup(league, Reading.from_slots(_slots(players=["Jayson Tatum", "LeBron James"], without=["Jaylen Brown"], span="career")))
    assert result.data["meetings"] == 2 and "they met" not in result.answer


def test_a_record_when_teammates_start_splits_by_the_start(league: AnswerContext) -> None:
    """ "celtics record when tatum starts" (ROADMAP plan item 3): the split's
    sides are the games he started against the rest, not played against
    out. This season Tatum started e1 (W) and e7 (L), came off the bench in
    e4 (W), sat e2 (L) and has no line in e3 (W)."""
    result = with_without(
        league, Reading.from_slots(_slots(team="Boston Celtics", with_player=["Jayson Tatum"], conditions=[{"player": "Jayson Tatum", "side": "own", "predicate": "started"}], season=S))
    )
    groups = {g["teammate_played"]: (g["games"], g["wins"], g["losses"]) for g in result.data["groups"]}
    assert groups[True] == (2, 1, 1) and groups[False] == (3, 2, 1)
    assert "Jayson Tatum started" in result.answer and "Jayson Tatum did not start" in result.answer
    # The same question with no role is the plain played/out split.
    plain = with_without(league, Reading.from_slots(_slots(team="Boston Celtics", with_player=["Jayson Tatum"], season=S)))
    plain_groups = {g["teammate_played"]: (g["games"], g["wins"], g["losses"]) for g in plain.data["groups"]}
    assert plain_groups[True] == (3, 2, 1) and "Jayson Tatum played" in plain.answer
    # A line as the role: the games he had 30+ points (e1 30, e7 31, e4 35) against the rest.
    lined = with_without(
        league,
        Reading.from_slots(
            _slots(team="Boston Celtics", with_player=["Jayson Tatum"], conditions=[{"player": "Jayson Tatum", "side": "own", "predicate": "reached", "stat": "points", "threshold": 31}], season=S)
        ),
    )
    lined_groups = {g["teammate_played"]: g["games"] for g in lined.data["groups"]}
    assert lined_groups[True] == 2 and "had 31+ points" in lined.answer
