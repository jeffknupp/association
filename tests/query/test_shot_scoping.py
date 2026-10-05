"""``shot_chart``/``shot_distance`` read through the player-games relation
(step 3, C5): every narrowing besides ``order``/``span`` - an opponent, a
venue, a teammate's absence, a starter/bench half, one game of a series, a
line on a box-score column, one Eastern date, ``since``, a calendar
``situation`` and a companion's role (``conditions``) - and ``order``
honoring ``limit`` as a WINDOW of games rather than always exactly one.

One small fixture, built by hand so every count asserted below can be
counted off it rather than guessed - the same discipline
``test_conditions.py``'s own fixture docstring states. Every scenario here was
checked against the real warehouse first (`shot_chart`, `player_game_log`,
`player_box_stats` over Stephen Curry's 2026 season) before being shrunk to a
handful of rows; see the C5 report for the exact counts measured there.

.. versionchanged:: 5.0.0
   Adds ``situation`` and ``conditions`` (ROADMAP plan item 6, step (d)
   follow-up): both joined ``RELATION_SCOPING`` after this file's own tests
   were written, and ``_shots_other_narrowing`` (``templates/shots.py``) kept
   its own hand-written list rather than reading the relation's declaration,
   so a shot chart or distance narrowed only by one of the two silently drew
   the whole season - "stephen curry shot chart on tuesdays" answered the
   same 374/803 (46.6%) as no narrowing at all.
"""

from __future__ import annotations

from datetime import date as _date
from pathlib import Path
from typing import Any

import duckdb
import pytest
from test_templates import shot_chart, shot_distance  # the shot relation's reader (compose.shots), its templates retired

from association.fetch.repairs import real_games
from association.nba.season import current_season
from association.query.answer import AnswerContext
from association.query.reading import Reading, Unsupported

SEASON = current_season()

_BOX_COLUMNS = "event_id VARCHAR, season INTEGER, season_type INTEGER, team_id VARCHAR, opponent_team_id VARCHAR, athlete_id VARCHAR, starter BOOLEAN, did_not_play BOOLEAN, minutes INTEGER"


def _box(event: str, season: int, athlete: str, *, team: str = "9", opponent: str, starter: bool = True, dnp: bool = False, minutes: int | None = 35, season_type: int = 2) -> tuple[Any, ...]:
    """One ``player_box_stats`` row - a did-not-play entry carries no minutes."""
    if dnp:
        return (event, season, season_type, team, opponent, athlete, starter, True, None)
    return (event, season, season_type, team, opponent, athlete, starter, False, minutes)


def _shot(event: str, season: int, athlete: str, made: bool, *, season_type: int = 2) -> tuple[Any, ...]:
    """One ``shot_chart`` row: a labeled three from the top of the arc, 26 feet
    from the rim at (25, 0) - a real three's position, so the label and the
    line agree, the same shape :mod:`tests.query.test_shot_values` pins."""
    return (athlete, season, season_type, event, 1, "10:00", made, "Jump Shot", 25, 26, 3, "26-foot three point jumper")


@pytest.fixture
def shots_ctx(tmp_path: Path) -> AnswerContext:
    """A Warriors season in miniature, mirroring the real Golden State/Stephen
    Curry shape measured against the warehouse before this was shrunk:

    ====  ===========  ===========  ==========  =================================
    game  season       opponent     venue       notes
    ====  ===========  ===========  ==========  =================================
    e0    SEASON-1     Lakers (13)  home        the "since" span's earlier season
    e1    SEASON       Lakers (13)  home        Curry starts, 2 shots (1 made)
    e2    SEASON       Celtics (5)  away        Curry starts, 20 minutes (< 30)
    e3    SEASON       Lakers (13)  home        Curry starts, 1 shot (made)
    e4    SEASON       Celtics (5)  home        Klay Thompson (2) sits (DNP)
    e5    SEASON       Lakers (13)  home        Curry off the BENCH, Klay sits
    e6    SEASON       Lakers (13)  home        Curry starts (latest Lakers game)
    p1-p4 SEASON       Lakers (13)  postseason  a 4-game series, numbered by date
    ====  ===========  ===========  ==========  =================================

    Klay Thompson (athlete ``2``) starts e1-e3 and sits e4-e5, so "without
    Klay" is the two games he sat. Stephen Curry is athlete ``1`` throughout;
    the Lakers are team ``13``, the Celtics team ``5``, the Warriors team ``9``.
    """
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO players VALUES ('1','Stephen Curry'),('2','Klay Thompson')")
    c.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    c.execute("INSERT INTO teams VALUES ('9','GS','Golden State Warriors'),('13','LAL','Los Angeles Lakers'),('5','BOS','Boston Celtics')")
    c.execute(
        "CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, date VARCHAR, home_team_id VARCHAR, away_team_id VARCHAR, "
        "home_score INTEGER, away_score INTEGER, winner_team_id VARCHAR)"
    )
    # Every tip at 23:00Z, which is evening Eastern the SAME calendar day
    # (23:00 UTC - 4/5h never crosses midnight), so a `date` slot can be
    # compared straight against the UTC date here with no Eastern-rollover
    # arithmetic to get wrong.
    c.executemany(
        "INSERT INTO games VALUES (?,?,2,?,?,?,110,100,?)",
        [
            ("e0", SEASON - 1, f"{SEASON - 1}-11-01T23:00Z", "9", "13", "9"),
            ("e1", SEASON, f"{SEASON}-11-01T23:00Z", "9", "13", "9"),
            ("e2", SEASON, f"{SEASON}-11-03T23:00Z", "5", "9", "9"),
            ("e3", SEASON, f"{SEASON}-11-05T23:00Z", "9", "13", "9"),
            ("e4", SEASON, f"{SEASON}-11-07T23:00Z", "9", "5", "9"),
            ("e5", SEASON, f"{SEASON}-11-09T23:00Z", "9", "13", "9"),
            ("e6", SEASON, f"{SEASON}-11-11T23:00Z", "9", "13", "9"),
        ],
    )
    c.executemany(
        "INSERT INTO games VALUES (?,?,3,?,?,?,110,100,?)",
        [
            ("p1", SEASON, f"{SEASON}-04-20T23:00Z", "9", "13", "9"),
            ("p2", SEASON, f"{SEASON}-04-22T23:00Z", "13", "9", "13"),
            ("p3", SEASON, f"{SEASON}-04-24T23:00Z", "9", "13", "9"),
            ("p4", SEASON, f"{SEASON}-04-26T23:00Z", "13", "9", "13"),
        ],
    )
    c.execute(f"CREATE TABLE player_box_stats ({_BOX_COLUMNS})")
    c.executemany(
        f"INSERT INTO player_box_stats VALUES ({', '.join('?' for _ in range(9))})",
        [
            _box("e0", SEASON - 1, "1", opponent="13"),
            _box("e1", SEASON, "1", opponent="13"),
            _box("e2", SEASON, "1", opponent="5", minutes=20),
            _box("e3", SEASON, "1", opponent="13"),
            _box("e4", SEASON, "1", opponent="5"),
            _box("e5", SEASON, "1", opponent="13", starter=False),
            _box("e6", SEASON, "1", opponent="13"),
            _box("e1", SEASON, "2", opponent="13"),
            _box("e2", SEASON, "2", opponent="5"),
            _box("e3", SEASON, "2", opponent="13"),
            _box("e4", SEASON, "2", opponent="5", dnp=True),
            _box("e5", SEASON, "2", opponent="13", dnp=True),
            _box("e6", SEASON, "2", opponent="13"),  # Klay is back for e6, so "without Klay" is only e4 and e5
            _box("p1", SEASON, "1", opponent="13", season_type=3),
            _box("p2", SEASON, "1", opponent="13", season_type=3),
            _box("p3", SEASON, "1", opponent="13", season_type=3),
            _box("p4", SEASON, "1", opponent="13", season_type=3),
        ],
    )
    c.execute(
        "CREATE TABLE shot_chart (athlete_id VARCHAR, season INTEGER, season_type INTEGER, event_id VARCHAR, "
        "period INTEGER, clock VARCHAR, made BOOLEAN, shot_type VARCHAR, coordinate_x INTEGER, coordinate_y INTEGER, points_attempted INTEGER, description VARCHAR)"
    )
    c.executemany(
        "INSERT INTO shot_chart VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            _shot("e0", SEASON - 1, "1", True),
            _shot("e1", SEASON, "1", True),
            _shot("e1", SEASON, "1", False),
            _shot("e2", SEASON, "1", True),
            _shot("e3", SEASON, "1", True),
            _shot("e4", SEASON, "1", True),
            _shot("e4", SEASON, "1", True),
            _shot("e5", SEASON, "1", True),
            _shot("e6", SEASON, "1", True),
            _shot("e6", SEASON, "1", False),
            _shot("p1", SEASON, "1", True, season_type=3),
            _shot("p2", SEASON, "1", True, season_type=3),
            _shot("p2", SEASON, "1", True, season_type=3),
            _shot("p3", SEASON, "1", False, season_type=3),
            _shot("p4", SEASON, "1", True, season_type=3),
        ],
    )
    c.execute("CREATE TABLE player_season_stats_deduped (athlete_id VARCHAR, season INTEGER, season_type INTEGER, gamesPlayed INTEGER)")
    c.executemany("INSERT INTO player_season_stats_deduped VALUES (?, ?, 2, ?)", [("1", SEASON - 1, 1), ("1", SEASON, 6)])
    # The warehouse's own view, joins and all - `opponent_abbr` is read by
    # `game_label` (a single-game chart's own subtitle/message), not by the
    # relation itself.
    c.execute(
        "CREATE VIEW player_game_log AS SELECT pbs.*, g.date AS game_date, o.abbreviation AS opponent_abbr "
        "FROM player_box_stats pbs JOIN games g ON g.event_id = pbs.event_id AND g.season = pbs.season "
        "LEFT JOIN teams o ON o.team_id = pbs.opponent_team_id"
    )
    real_games.build_table(c, {"games", "teams"})
    return AnswerContext(con=c, out_dir=tmp_path / "out")


# ---------------- shot_chart: each newly-honored cell ----------------


def test_shot_chart_honors_opponent(shots_ctx: AnswerContext) -> None:
    """Lakers games (e1, e3, e5, e6): 2+1+1+2 = 6 attempts, 4 made - e2's and
    e4's shots (vs the Celtics) are left out."""
    answer = shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "opponent": "Los Angeles Lakers"})).answer or ""
    assert "4/6 made" in answer


def test_shot_chart_honors_venue(shots_ctx: AnswerContext) -> None:
    """Every game but e2 is at home: 9 total attempts less e2's 1 = 8, and 7
    made less e2's 1 make = 6."""
    answer = shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "venue": "home"})).answer or ""
    assert "6/8 made" in answer


def test_shot_chart_honors_without(shots_ctx: AnswerContext) -> None:
    """Klay Thompson sits e4 and e5 - 2 + 1 = 3 attempts, all three made."""
    answer = shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "without": "Klay Thompson"})).answer or ""
    assert "3/3 made" in answer


def test_shot_chart_honors_a_named_half_of_the_starter_bench_split(shots_ctx: AnswerContext) -> None:
    """Curry starts every game but e5, where he came off the bench - one shot,
    made."""
    bench = shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "split": "bench"})).answer or ""
    assert "1/1 made" in bench
    starts = shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "split": "starter"})).answer or ""
    assert "6/8 made" in starts  # every attempt but e5's (9 total, 7 made, less e5's 1/1)


def test_shot_chart_honors_one_eastern_date(shots_ctx: AnswerContext) -> None:
    """e3 alone (the Lakers game the fixture's own docstring tips at
    ``{SEASON}-11-05T23:00Z``, the same Eastern day)."""
    answer = shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "date": f"{SEASON}-11-05"})).answer or ""
    assert "1/1 made" in answer
    assert "e3" in answer  # the game-label fallback names the bare event id with no `games` row for it to describe further


def test_shot_chart_honors_a_line_on_a_box_score_column(shots_ctx: AnswerContext) -> None:
    """e2 alone has under 30 minutes - its one made shot."""
    answer = shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "below": "under 30 minutes"})).answer or ""
    assert "1/1 made" in answer


def test_shot_chart_honors_since(shots_ctx: AnswerContext) -> None:
    """``since`` SEASON-1 draws e0 (1 made) plus every SEASON attempt (7 made
    of 9) - 10 attempts, 8 made."""
    answer = shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "since": SEASON - 1})).answer or ""
    assert "8/10 made" in answer


def test_shot_chart_honors_game_n_of_a_series(shots_ctx: AnswerContext) -> None:
    """Game 2 of the Lakers series (p2, the second by date) - 2 attempts, both
    made."""
    answer = shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "season_type": 3, "game_n": 2})).answer or ""
    assert "2/2 made" in answer


def test_shot_chart_honors_season_n(shots_ctx: AnswerContext) -> None:
    """His 1st season on record here is SEASON-1 (e0's one made shot)."""
    answer = shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season_n": 1})).answer or ""
    assert "1/1 made" in answer


def test_shot_chart_honors_limit_as_a_window_not_a_single_game(shots_ctx: AnswerContext) -> None:
    """The step's own finding: "last two games" used to chart the whole
    season. Windowed to the 2 newest (e5, e6): 1 + 2 = 3 attempts, 2 made."""
    whole_season = shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "order": "recent", "limit": 2}))
    assert "2/3 made" in (whole_season.answer or "")
    assert "over his last 2 games" in (whole_season.answer or "")


def test_shot_chart_honors_limit_with_no_order_slot_at_all(shots_ctx: AnswerContext) -> None:
    """The router's own traces for this exact question never emit `order` -
    only `limit` - so the fix has to read a bare `limit` as "recent" or it
    would not reach the real question. See `_shots_order`."""
    answer = shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "limit": 2})).answer or ""
    assert "2/3 made" in answer


def test_shot_chart_windows_a_narrowed_set_together(shots_ctx: AnswerContext) -> None:
    """ "His last 2 Lakers games" - narrowed to Lakers (e1, e3, e5, e6), THEN
    windowed to the 2 newest of those (e5, e6): 1 + 2 = 3 attempts, 2 made -
    a different answer from an unqualified "last 2 games" (test above), which
    reads e5 and e6 too only because they happen to be the two most recent
    games of ANY opponent."""
    answer = shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "opponent": "Los Angeles Lakers", "order": "recent", "limit": 2})).answer or ""
    assert "2/3 made" in answer
    assert "over his last 2 games" in answer
    assert "Los Angeles Lakers" in answer


def test_shot_chart_still_refuses_a_career_span_with_an_order(shots_ctx: AnswerContext) -> None:
    """Unchanged by this step: "his last game" picks one game inside one
    season, where a career asks for every one of them."""
    with pytest.raises(Unsupported, match="career span"):
        shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "span": "career", "order": "recent"}))


def test_shot_chart_reports_no_games_rather_than_no_shots_when_narrowing_matches_nothing(shots_ctx: AnswerContext) -> None:
    """A combination Curry never met in the fixture's span - he only comes off
    the bench against the Lakers (e5), never the Celtics - so the fact
    missing is which games, not which shots, and the relation's own
    `_no_narrowed_games` answers rather than a bare "no shots found"."""
    answer = shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "opponent": "Boston Celtics", "split": "bench"})).answer or ""
    assert "played" in answer and "none of them" in answer


def test_shot_chart_honors_situation(shots_ctx: AnswerContext) -> None:
    """The bug itself: "on tuesdays" used to answer the same 7/9 whole-season
    shots as no narrowing at all (measured live against the real warehouse:
    "stephen curry shot chart on tuesdays" answered 374/803, identical to no
    narrowing). e1-e6 are each 2 days apart, so - 2 and 7 sharing no common
    factor - their six Eastern days fall on six different weekdays; e3's
    ({SEASON}-11-05) is the only one of the six on its weekday, and e3 alone
    is 1 attempt, made."""
    weekday = _date(SEASON, 11, 5).strftime("%A").lower()
    answer = shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "situation": f"{weekday}s"})).answer or ""
    assert "1/1 made" in answer


def test_shot_chart_honors_conditions(shots_ctx: AnswerContext) -> None:
    """The same bug, for a companion's role: "when draymond green starts"
    used to answer the whole season too. Klay Thompson (athlete 2) starts
    e1, e2, e3 and e6 (he sits e4 and e5) - 2 + 1 + 1 + 2 = 6 attempts, 4
    made."""
    conditions = [{"player": "Klay Thompson", "side": "own", "predicate": "started"}]
    answer = shot_chart(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "conditions": conditions})).answer or ""
    assert "4/6 made" in answer


# ---------------- shot_distance: the same mechanism, a representative subset ----------------


def test_shot_distance_honors_opponent(shots_ctx: AnswerContext) -> None:
    answer = shot_distance(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "opponent": "Los Angeles Lakers"})).answer or ""
    assert "over 6 attempts" in answer


def test_shot_distance_honors_since(shots_ctx: AnswerContext) -> None:
    answer = shot_distance(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "since": SEASON - 1})).answer or ""
    assert "over 10 attempts" in answer


def test_shot_distance_honors_limit_as_a_window(shots_ctx: AnswerContext) -> None:
    answer = shot_distance(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "order": "recent", "limit": 2})).answer or ""
    assert "over 3 attempts" in answer
    assert "over his last 2 games" in answer


def test_shot_distance_still_refuses_a_career_span_with_an_order(shots_ctx: AnswerContext) -> None:
    with pytest.raises(Unsupported, match="career span"):
        shot_distance(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "span": "career", "order": "first"}))


def test_shot_distance_single_game_note_is_unchanged_by_the_relation_port(shots_ctx: AnswerContext) -> None:
    """The exact single-game phrasing golden tests pin in `test_templates.py`
    - "most recent game (date)" - still comes from the bare `player_game_log`
    lookup (`_shots_ordered_games`), not the relation, when `order` is the
    only scoping given."""
    answer = shot_distance(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "order": "recent"})).answer or ""
    assert f"most recent game ({SEASON}-11-11)" in answer


def test_shot_distance_honors_situation(shots_ctx: AnswerContext) -> None:
    """The same bug as `shot_chart`'s: "on tuesdays" answered the whole
    season's 803-attempt (real warehouse) average regardless. e3's weekday
    (see `test_shot_chart_honors_situation`) is one attempt."""
    weekday = _date(SEASON, 11, 5).strftime("%A").lower()
    answer = shot_distance(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "situation": f"{weekday}s"})).answer or ""
    assert "over 1 attempts" in answer


def test_shot_distance_honors_conditions(shots_ctx: AnswerContext) -> None:
    """Klay Thompson starting (e1, e2, e3, e6): 2 + 1 + 1 + 2 = 6 attempts."""
    conditions = [{"player": "Klay Thompson", "side": "own", "predicate": "started"}]
    answer = shot_distance(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "conditions": conditions})).answer or ""
    assert "over 6 attempts" in answer


def test_shot_distance_situations_one_game_note_names_the_situation(shots_ctx: AnswerContext) -> None:
    """The other half of the fix: `_shot_distance_games`' one-game note used
    `_shots_other_narrowing` to decide whether a single game it found came
    from JUST a window (`order`/`limit` alone - "in his most recent game
    (date)") or from a real narrowing that happens to leave one game (the
    general phrase, naming what narrowed it). With `situation` missing from
    that check, a single game a calendar narrowing (not a window) found still
    took the window's own phrasing - silently dropping the reason, exactly
    the shape AGENTS.md warns a coverage caveat or a narrowing note can fail
    in. e3's weekday (the same single game `test_shot_chart_honors_situation`
    finds) has no `order`/`limit` at all, so the note must name the weekday,
    never "most recent game"."""
    weekday = _date(SEASON, 11, 5).strftime("%A").lower()
    answer = shot_distance(shots_ctx, Reading.from_slots({"player": "Stephen Curry", "season": SEASON, "situation": f"{weekday}s"})).answer or ""
    assert f"on {weekday.capitalize()}s" in answer
    assert "most recent game" not in answer
