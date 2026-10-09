"""The sayer takes a Result and nothing else (``compose.say``), and the game
log's reader returns one (``compose.logs``): Phase 2's first slice. The
sentences here are the retired template's, word for word - the sayer is
held to them with no warehouse behind it."""

from __future__ import annotations

from association.query.compose.say import mixed_where, note_phrase, say, say_player_log, say_team_log
from association.query.notes import Note, collect
from association.query.result import CountFacts, Line, LogFacts, Narrowing, Part, RankingFacts, RecordFacts, Refusal, Result, Rows, Span, Window


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
    empty = Refusal(kind="no_game_on_date", facts={"player": "Tyrese Maxey", "kind": "regular season", "date": "2026-04-11", "narrowing": ""})
    said = say(_player_result(parts=(Part(body=Rows()),), notes=(), empty=empty))
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
        facts=LogFacts(stat="pointsDifference"),
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
                    of=Line(column="points", value=20),
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
        facts=RecordFacts(teams=("Philadelphia 76ers",)),
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
        narrowing=Narrowing(cells=(Line(column="points", value=50),)),
        facts=CountFacts(stat="points", box_scores_from=1994, empty_box_scores=0),
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
        parts=(Part(body=Grouped(by="player", ranked_by="games", rows=rows)),),
        notes=(Note("lines_rebuilt", {"games": 1, "total": 4, "whose": "Luka Doncic", "what": "counted"}),),
        narrowing=Narrowing(cells=(Line(column="points", value=30), Line(column="turnovers", op="<", value=5, label="under 5 turnovers"))),
        facts=CountFacts(stat="points", box_scores_from=1994, empty_box_scores=0),
    )
    assert say(result).answer == (
        "Luka Doncic had the most games with 30+ points and under 5 turnovers in the 2026 regular season, with 4. Next: Shai Gilgeous-Alexander (2). "
        "1 of Luka Doncic's 4 games has no box score from ESPN - that figure is rebuilt from play-by-play, so treat the count as close rather than exact."
    )


def test_a_single_game_high_is_said_with_its_floor_its_tie_and_its_redirect() -> None:
    """The league's high over a career: a tie said as a tie, then the floor
    a league career is under. A named player's defaulted season that held
    nothing: the plain "no games", then where he IS on record, as a
    decision on the same line and in ``data["notes"]``."""
    from association.query.result import Decided

    tied = (
        {"player": "A Guard", "value": 23, "date": "2026-04-12", "opponent": "CHI", "reconstructed": False},
        {"player": "B Guard", "value": 23, "date": "2026-03-01", "opponent": "", "reconstructed": False},
    )
    league = Result(
        subject="every player",
        relation="everyone",
        span=Span(season=None, season_type=2, career=True),
        parts=(Part(body=Rows(columns=("assists",), rows=tied, by="assists")),),
        notes=(Note("floor", {"table": "box_scores", "first": 1994, "what": "league_record"}),),
        facts=CountFacts(stat="assists", box_scores_from=1994, empty_box_scores=0),
    )
    assert say(league).answer == (
        "A Guard and B Guard tied for the most assists in a single game in the regular season since 1993-94, with 23 each."
        " Box scores begin in 1993-94, so this is not an all-time record: earlier games are not in this warehouse."
    )
    # ISSUES.md #262: a player tied with himself ("Stephen Curry and Stephen
    # Curry tied for the most 3-pointers") is named once, with his games.
    twice = (
        {"player": "A Guard", "value": 12, "date": "2025-02-27", "opponent": "ORL", "reconstructed": False},
        {"player": "A Guard", "value": 12, "date": "2025-04-01", "opponent": "MEM", "reconstructed": False},
        {"player": "B Guard", "value": 10, "date": "2025-03-01", "opponent": "", "reconstructed": False},
    )  # the sayer's label is the stat's own; "assists" keeps the case about the tie
    alone = Result(
        subject="every player",
        relation="everyone",
        span=Span(season=2025, season_type=2),
        parts=(Part(body=Rows(columns=("assists",), rows=twice, by="assists")),),
        facts=CountFacts(stat="assists", box_scores_from=1994, empty_box_scores=0),
    )
    assert say(alone).answer == "A Guard had the most assists in a single game in the 2025 regular season: 12, twice - on 2025-02-27 vs ORL and on 2025-04-01 vs MEM. Next: B Guard (10)."
    beside = Result(
        subject="every player",
        relation="everyone",
        span=Span(season=2025, season_type=2),
        parts=(Part(body=Rows(columns=("assists",), rows=(*twice[:2], {"player": "B Guard", "value": 12, "date": "2025-03-01", "opponent": "", "reconstructed": False}), by="assists")),),
        facts=CountFacts(stat="assists", box_scores_from=1994, empty_box_scores=0),
    )
    assert say(beside).answer == "A Guard (twice) and B Guard tied for the most assists in a single game in the 2025 regular season, with 12 each."
    redirect = Decided(kind="season_redirected", field="season", chose=None, why="the season read by default holds nothing for him", facts={"first": 1990, "last": 1999, "what": "regular season"})
    retired = Result(
        subject="Old Timer",
        relation="player",
        span=Span(season=2026, season_type=2),
        parts=(Part(body=Rows(columns=("points",), rows=(), by="points")),),
        decisions=(redirect,),
        facts=CountFacts(stat="points", box_scores_from=1994, empty_box_scores=0),
    )
    with collect() as collected:
        said = say(retired)
    assert said.answer == "Old Timer has no 2026 regular season games in the warehouse. He last appears in 1999. The warehouse holds his 1990-1999 regular seasons; name one, or ask for his career."
    assert said.data["notes"] == ["He last appears in 1999. The warehouse holds his 1990-1999 regular seasons; name one, or ask for his career."]
    assert said.data["question_shape"] == "most points in a single game, Old Timer, 2026 regular season"
    assert [each.kind for each in collected.decisions] == ["season_redirected"]


def test_a_stat_ranking_and_a_count_ranking_are_told_apart_by_the_body() -> None:
    """Two rankings by ``player``: the league's count over a line ranks by
    ``games``, a season-line ranking by its metric (``Grouped.ranked_by``).
    The ranking is said with the qualifier it already was (a ``minimum``
    decision), its percentage with the makes and attempts behind it, and the
    most-recent-team remark on its own line, recorded once each."""
    from association.query.result import Decided, Grouped

    rows = (
        {"key": "Nikola Jokic", "rank": 1, "values": {"ts_pct": 0.6612, "points": 2071, "true_shooting_attempts": 1566, "team": "Denver Nuggets"}},
        {"key": "Shai Gilgeous-Alexander", "rank": 2, "values": {"ts_pct": 0.6421, "points": 2484, "true_shooting_attempts": 1934, "team": "Oklahoma City Thunder"}},
    )
    result = Result(
        subject="every player",
        relation="everyone",
        span=Span(season=2026, season_type=2),
        parts=(Part(body=Grouped(by="player", ranked_by="ts_pct", rows=rows)),),
        notes=(Note("definition", {"term": "most_recent_team"}),),
        decisions=(Decided(kind="minimum", field="minimum", chose=550, facts={"of": "true-shooting attempts", "column": "true_shooting_attempts"}),),
        facts=RankingFacts(label="true shooting %", ratio=("points", "true_shooting_attempts"), fields=("team",)),
    )
    with collect() as remarks:
        said = say(result)
    lines = said.answer.split("\n")
    assert lines[0] == "true shooting %, the league, 2026 regular season (minimum 550 true-shooting attempts):"
    assert lines[2].split() == ["Nikola", "Jokic", "66.1%", "Denver", "Nuggets"]
    assert lines[-1] == "Team is each player's most recent team that season."
    assert said.data["leaders"][0] == {"display_name": "Nikola Jokic", "value": 0.6612, "points": 2071, "true_shooting_attempts": 1566, "team": "Denver Nuggets"}
    assert said.data["min_sample"] == 550 and said.data["notes"] == ["Team is each player's most recent team that season."]
    assert [n.kind for n in remarks.notes] == ["definition"] and [d.kind for d in remarks.decisions] == ["minimum"]
    # Without the team column, a sentence naming the leader, and its makes over attempts.
    sentence = say(Result(**{**result.__dict__, "notes": (), "facts": RankingFacts(label="true shooting %", ratio=("points", "true_shooting_attempts"))})).answer
    assert sentence == (
        "Nikola Jokic led the league in true shooting % in the 2026 regular season (minimum 550 true-shooting attempts), at 66.1% (2,071 of 1,566). Next: Shai Gilgeous-Alexander (64.2%)."
    )


def test_a_career_ranking_says_its_pool_every_time() -> None:
    """A career list is never all-time: the pool's floor is a note on the
    season line, said after the names - and with nobody qualified, after
    the empty sentence."""
    from association.query.result import Grouped

    rows = ({"key": "LeBron James", "rank": 1, "values": {"total_points": 43440, "games": 1622, "first_season": 2004, "last_season": 2026}},)
    result = Result(
        subject="every player",
        relation="everyone",
        span=Span(season_type=2, career=True, first=1994, source="seasons"),
        parts=(Part(body=Grouped(by="player", ranked_by="total_points", rows=rows)),),
        notes=(Note("floor", {"table": "season_line", "first": 1994, "what": "career_pool"}),),
        facts=RankingFacts(label="total points"),
    )
    assert say(result).answer == (
        "Among players active in 1993-94 or later, LeBron James leads in career points in the regular season: 43,440, over 1,622 games (2003-04 through 2025-26). "
        "Careers that ended before 1993-94 are not in this warehouse, so this is not an all-time list."
    )
    empty = say(Result(**{**result.__dict__, "parts": (Part(body=Grouped(by="player", ranked_by="total_points")),)}))
    assert empty.answer == "No player qualified for career points in the regular season. Careers that ended before 1993-94 are not in this warehouse, so this is not an all-time list."
    assert empty.data["leaders"] == [] and empty.data["pool_first_season"] == 1994


def test_a_reads_causes_and_a_readings_are_two_closed_sets_said_by_one_table() -> None:
    """A cause found at RUN (a fact in the warehouse) and a cause found at
    READ (the words alone) are disjoint closed sets, so a kind says which
    stage refused; one :class:`Refusal` type carries either, and the sayer
    has a phrase for every kind a read can refuse by - an unknown kind is
    refused at construction, never said as nothing."""
    import importlib

    import pytest

    from association.query.reading import CAUSES
    from association.query.result import RUN_CAUSES

    # The package's ``say`` is the function; the module holds the tables.
    sayer = importlib.import_module("association.query.compose.say")
    assert not RUN_CAUSES & CAUSES
    assert set(sayer._RUN_PHRASES) | set(sayer._FINGERPRINT_PHRASES) == RUN_CAUSES
    with pytest.raises(ValueError, match="not a cause"):
        Refusal(kind="no_such_cause")


def test_a_refusal_is_said_from_its_facts_beside_the_pages_values() -> None:
    """The reader hands the cause and its facts; the sentence is the sayer's,
    and the page reads it under the keys the Refusal names, beside the
    values it shows - none, for a relation's "no games" whose page held
    only the counts."""
    from association.query.result import Clarify

    shown = {"team": "Boston Celtics", "span": "2026 regular season", "games": 0}
    said = say(Refusal(kind="no_team_games", facts={"team": "Boston Celtics", "where": "in the 2026 regular season"}, shown=shown, under=()))
    assert said.answer == "The warehouse has no games with a result for the Boston Celtics in the 2026 regular season." and said.data == shown
    listed = say(Refusal(kind="listed_not_played", facts={"player": "Joel Embiid", "team": None, "where": "in the 2026 regular season", "games": 1}))
    assert listed.answer == "Joel Embiid was listed in 1 box score in the 2026 regular season but did not play in it." and listed.data == {"message": listed.answer}
    asked = say(Clarify(asked="Curry", candidates=("Seth Curry", "Stephen Curry")))
    assert asked.answer == "'Curry' matches more than one player - did you mean Seth Curry or Stephen Curry?"
    assert asked.data == {"ambiguous": "Curry", "candidates": ["Seth Curry", "Stephen Curry"]}
    near = say(Clarify(asked="embid", candidates=("Joel Embiid",), why="near_spelling"))
    assert near.answer == "No player found matching 'embid' - did you mean Joel Embiid?" and near.data == {"unmatched": "embid", "suggestions": ["Joel Embiid"]}


def test_the_cells_a_read_applied_are_typed_and_read_by_type() -> None:
    """What ``Result.facts`` carried as untyped keys - a period, a date, the
    lines, a role, a series game - is a typed cell on the narrowing, read
    by its type; and a shape's record is its own, so a sayer handed
    another shape's is a bug that raises, never an answer said from
    missing keys."""
    import importlib

    import pytest

    from association.query.result import GameOfSeries, LineFacts, OnDate, Period, Role

    narrowing = Narrowing(cells=(Period(label="1st quarter", periods=(1,)), Role(started=True), Line(column="points", value=30, label="30+ points"), GameOfSeries(n=4), OnDate(day="2026-04-11")))
    assert narrowing.period == Period(label="1st quarter", periods=(1,))
    assert narrowing.cell(Role) == Role(started=True) and narrowing.cell(GameOfSeries) == GameOfSeries(n=4)
    assert [line.label for line in narrowing.lines()] == ["30+ points"]
    assert Narrowing().period is None and Narrowing().cell(OnDate) is None
    sayer = importlib.import_module("association.query.compose.say")
    with pytest.raises(TypeError, match="LogFacts sayer was handed LineFacts"):
        sayer._facts(Result(subject="New York Knicks", relation="team", parts=(Part(body=Rows()),), facts=LineFacts()), LogFacts)


def test_the_answer_side_chooses_by_shape_and_never_by_intent() -> None:
    """``compose.answer`` picks a reader by the planned point's shape
    (``plan.PointShape``) and ``say`` a sayer by the body - neither reads an
    intent (the Phase 2 review's cleanup (b)6). Every reader's shape has its
    words' scoping, every scalar's reduction a sayer, and neither module's
    code names ``intent``: a branch on it would be the dispatch this
    replaced, back."""
    import ast
    import importlib
    import typing
    from pathlib import Path

    from association.query.compose.plan import SHAPE_WORDS, STATED_SCOPING
    from association.query.result import Scalar

    compose = importlib.import_module("association.query.compose")
    sayer = importlib.import_module("association.query.compose.say")
    assert set(compose._ROUTES) == set(SHAPE_WORDS) == set(STATED_SCOPING)
    assert set(sayer._SCALAR_SAYERS) == set(typing.get_args(typing.get_type_hints(Scalar)["how"]))
    for module in (compose, sayer):
        tree = ast.parse(Path(module.__file__ or "").read_text())
        named = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)} | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        arguments = {arg.arg for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) for arg in node.args.args}
        assert "intent" not in named and "intent" not in arguments, module.__name__
