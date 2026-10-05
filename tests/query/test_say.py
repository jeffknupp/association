"""The sayer takes a Result and nothing else (``compose.say``), and the game
log's reader returns one (``compose.logs``): Phase 2's first slice. The
sentences here are the retired template's, word for word - the sayer is
held to them with no warehouse behind it."""

from __future__ import annotations

from association.query.compose.say import mixed_where, note_phrase, say, say_player_log, say_team_log
from association.query.notes import Note, collect
from association.query.result import Narrowing, Part, Result, Rows, Span, Window


def _player_result(**changes: object) -> Result:
    games = (
        {"date": "2026-04-10", "season": 2026, "opponent": "BOS", "home_away": "home", "result": "W", "reconstructed": False, "minutes": 34, "points": 30, "rebounds": 5, "assists": 7},
        {"date": "2026-04-08", "season": 2026, "opponent": "NYK", "home_away": "away", "result": "L", "reconstructed": True, "minutes": None, "points": 22, "rebounds": 4, "assists": 9},
    )
    base = Result(
        subject="Tyrese Maxey",
        relation="player",
        span=Span(season=2026, season_type=2, first=2026, last=2026, years="2026 regular season"),
        narrowing=Narrowing(phrase=" vs the Boston Celtics", opponent="Boston Celtics"),
        window=Window(limit=10, asked=10, ascending=False),
        parts=(Part(body=Rows(columns=("MIN", "PTS", "REB", "AST"), rows=games, total_before_window=7, summary={"minutes": 34.0, "points": 26.0, "rebounds": 4.5, "assists": 8.0})),),
        notes=(Note("window_short", {"found": 2, "asked": 10, "season": 2026, "season_type": 2}), Note("lines_rebuilt", {"games": 1, "what": "shown"})),
    )
    return Result(**{**base.__dict__, **changes})


def test_a_players_log_is_said_in_the_retired_templates_words() -> None:
    """The heading names the cut ("last 2 of 7 games"), the table is aligned
    with the per-game row last, and each note follows in its one phrase."""
    said = say_player_log(_player_result())
    lines = said.answer.split("\n")
    assert lines[0] == "Tyrese Maxey vs the Boston Celtics, last 2 of 7 games of the 2026 regular season:"
    assert lines[1].split() == ["date", "opp", "W/L", "MIN", "PTS", "REB", "AST"]
    assert lines[2].split() == ["2026-04-10", "vs", "BOS", "W", "34", "30", "5", "7"]
    assert lines[3].split() == ["2026-04-08", "@", "NYK", "L", "-", "22", "4", "9"]
    assert lines[4].split() == ["per", "game", "34.0", "26.0", "4.5", "8.0"]
    assert lines[5] == "Only 2 games vs the Boston Celtics in the 2026 regular season - ask about his career to reach earlier seasons."
    assert lines[6] == "1 of these game has no box score from ESPN: its figures are rebuilt from play-by-play, and minutes cannot be recovered at all."
    assert said.data["player"] == "Tyrese Maxey" and said.data["qualifying_games"] == 7 and said.data["headline"] == lines[0].rstrip(":")
    assert said.data["notes"] == lines[5:7] and said.data["columns"] == ["MIN", "PTS", "REB", "AST"]


def test_saying_a_result_records_each_note_once_under_its_kind() -> None:
    """The sayer records what it says (``notes.note``), so the answer's
    remarks are the Result's notes - the check contract 5 rests on."""
    with collect() as collected:
        say(_player_result())
    assert [(each.kind, dict(each.facts)) for each in collected.notes] == [
        ("window_short", {"found": 2, "asked": 10, "season": 2026, "season_type": 2}),
        ("lines_rebuilt", {"games": 1, "what": "shown"}),
    ]


def test_a_log_with_no_rows_says_why() -> None:
    said = say(_player_result(parts=(), notes=(), empty="No regular season game on 2026-04-11 found for Tyrese Maxey."))
    assert said.answer == "No regular season game on 2026-04-11 found for Tyrese Maxey." and said.data["games"] == [] and said.data["message"] == said.answer


def test_each_note_kind_has_one_phrase() -> None:
    assert note_phrase(Note("window_short", {"found": 3, "asked": 5, "season_type": [2, 3]}), narrowing=" at home") == "Only 3 games at home found across the regular season and postseason."
    assert note_phrase(Note("window_short", {"found": 1, "asked": 5, "season": None, "season_type": 2})) == "Only 1 game in his box scores."
    assert note_phrase(Note("definition", {"term": "without", "names": ["Joel Embiid", "Paul George"]})).startswith("Without Joel Embiid and Paul George means games neither of them played")
    assert note_phrase(Note("games_unseen", {"games": 2, "why": "empty_box_score"})) == "Not counted: 2 games in this span whose box score lists him with no minutes and no stats."
    assert note_phrase(Note("floor", {"table": "box_scores", "first": 1994, "earliest": 1990})) == "Box scores begin with the 1993-94 season, so his 1990-1993 seasons are not counted."
    assert mixed_where(2026, {3: 5}) == " of the 2026 postseason" and mixed_where(2026, {2: 2, 3: 3}) == " (2 regular season and 3 postseason)"


def test_a_teams_log_is_said_with_its_record_and_the_total_asked_for() -> None:
    games = (
        {"date": "2026-04-10", "home_away": "home", "opponent": "Boston Celtics", "team_score": 110, "opponent_score": 100, "won": True, "season": 2026},
        {"date": "2026-04-08", "home_away": "away", "opponent": "Brooklyn Nets", "team_score": 95, "opponent_score": 99, "won": False, "season": 2026},
        {"date": "2026-04-06", "home_away": "home", "opponent": "Miami Heat", "team_score": 101, "opponent_score": 101, "won": None, "season": 2026},
    )
    result = Result(
        subject="New York Knicks",
        relation="team",
        span=Span(season=2026, season_type=2, first=2026, last=2026),
        window=Window(limit=3, ascending=False),
        parts=(Part(body=Rows(rows=games, summary={"wins": 1, "losses": 1, "unknown": 1})),),
        facts={"stat": "pointsDifference"},
    )
    said = say_team_log(result)
    lines = said.answer.split("\n")
    assert lines[0] == "New York Knicks, last 3 games of the 2026 regular season (1-1, 1 with no recorded result):"
    assert lines[1] == "  2026-04-10  W 110-100  vs Boston Celtics" and lines[3] == "  2026-04-06  ? 101-101  vs Miami Heat"
    assert lines[4] == "  Point differential: +6 (+2.00 per game)."
    assert said.data["wins"] == 1 and said.data["losses"] == 1 and said.data["differential"] == 6 and said.data["differential_per_game"] == 2.0


def test_a_record_over_a_line_is_said_in_the_retired_templates_words() -> None:
    """The three-row table under its heading, the pool and caveats glued in
    the template's order (pool, floor, unseen, blank) while the notes are
    recorded in the Result's (the template wrote its caveats first)."""
    from association.query.result import Grouped

    result = Result(
        subject="Tyrese Maxey",
        relation="player",
        span=Span(career=True, first=2022, last=2026, phrase="since 2022 (2022-2026 regular seasons)"),
        narrowing=Narrowing(phrase=" vs the Boston Celtics"),
        parts=(
            Part(
                body=Grouped(
                    by="threshold",
                    rows=(
                        {"key": "reached", "games": 4, "wins": 3, "losses": 1, "avg_margin": 6.25},
                        {"key": "short", "games": 6, "wins": 2, "losses": 4, "avg_margin": -3.5},
                        {"key": "all", "games": 11, "wins": 5, "losses": 6, "avg_margin": 0.0},
                    ),
                )
            ),
        ),
        notes=(
            Note("games_unseen", {"games": 2, "why": "no_box_score", "whose": "his team's"}),
            Note("stat_blank", {"games": 1, "stat": "points", "whose": "player"}),
            Note("definition", {"term": "pool", "games": 11, "what": "games_he_played"}),
            Note("floor", {"table": "box_scores", "first": 2022, "what": "regular season"}),
        ),
        facts={"stat": "points", "threshold": 20, "teams": ["Philadelphia 76ers"]},
    )
    with collect() as collected:
        said = say(result)
    lines = said.answer.split("\n")
    assert lines[0] == "Philadelphia 76ers record when Tyrese Maxey had 20+ points vs the Boston Celtics, since 2022 (2022-2026 regular seasons):"
    assert lines[2].split() == ["20+", "points", "4", "3-1", ".750", "+6.2"]
    assert lines[4].split() == ["all", "his", "games", "11", "5-6", ".455", "+0.0"]
    assert lines[5] == (
        "Over the 11 games he played; a game he missed is in neither row. Box scores start with the 2022 regular season; anything earlier is not counted."
        " The warehouse has no box score for 2 of his team's games in that span - ESPN lacks about one game in eight from 2013 to 2018 - so any of them he played are not counted."
        " 1 of his games in that span have no points figure on record, so they are in neither row."
    )
    assert [each.kind for each in collected.notes] == ["games_unseen", "stat_blank", "definition", "floor"]
    assert said.data["reached"] == {"games": 4, "wins": 3, "losses": 1, "avg_margin": 6.25} and said.data["notes"] == [lines[5]]


def test_a_count_over_a_line_is_said_in_the_retired_templates_words() -> None:
    """A named player's count: his career's floor said first, the count,
    then the leader's rebuilt games - every counted game rebuilt is said
    outright, not "2 of those 2"."""
    from association.query.compose.say import say_threshold_count
    from association.query.result import Scalar

    result = Result(
        subject="Michael Jordan",
        relation="player",
        span=Span(season=None, season_type=2, career=True, first=1985, last=2003),
        parts=(Part(body=Scalar(games=2, sums={"rebuilt": 2}, how="count")),),
        notes=(
            Note("floor", {"table": "box_scores", "first": 1994, "earliest": 1985, "whose": "Michael Jordan", "season_type": 2, "what": "career_began_earlier"}),
            Note("lines_rebuilt", {"games": 2, "total": 2, "whose": None, "what": "counted"}),
        ),
        facts={"stat": "points", "counted": 50, "lines": [], "ordinal": None, "box_scores_from": 1994, "empty_box_scores": 0},
    )
    with collect() as collected:
        said = say(result)
    assert said.answer == (
        "Box scores here begin in 1993-94, and Michael Jordan's regular season career began in 1984-85, so his whole career is not in them. "
        "Michael Jordan had 2 games with 50+ points in the regular season since 1993-94. "
        "None of those 2 games has a box score from ESPN - those figures are rebuilt from play-by-play, so treat the count as close rather than exact."
    )
    assert said.data["question_shape"] == "games with 50+ points, regular season since 1993-94"
    assert said.data["rebuilt_games"] == 2
    assert [each.kind for each in collected.notes] == ["floor", "lines_rebuilt"]
    assert say_threshold_count(result).answer == said.answer


def test_a_league_count_names_the_leaders_and_the_rest() -> None:
    """The league's count by player: the leader, "Next: ...", and a leader's
    rebuilt games said by his name."""
    from association.query.result import Grouped

    rows = ({"key": "Luka Doncic", "games": 4, "rebuilt": 1}, {"key": "Shai Gilgeous-Alexander", "games": 2, "rebuilt": 0})
    result = Result(
        subject="every player",
        relation="everyone",
        span=Span(season=2026, season_type=2),
        parts=(Part(body=Grouped(by="player", rows=rows)),),
        notes=(Note("lines_rebuilt", {"games": 1, "total": 4, "whose": "Luka Doncic", "what": "counted"}),),
        facts={"stat": "points", "counted": 30, "lines": ["under 5 turnovers"], "ordinal": None, "box_scores_from": 1994, "empty_box_scores": 0},
    )
    assert say(result).answer == (
        "Luka Doncic had the most games with 30+ points and under 5 turnovers in the 2026 regular season, with 4. Next: Shai Gilgeous-Alexander (2). "
        "1 of Luka Doncic's 4 games has no box score from ESPN - that figure is rebuilt from play-by-play, so treat the count as close rather than exact."
    )
