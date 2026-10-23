"""Decision D23's pilot, on D13's class: the other reading of a span the
words left open, offered as a ready question - the asker's own words with
the span stated (``Reading.alternatives``, written by the parser's last
step) - and offered only where the parser reads that wording to exactly
that alternative (``Alternative.reached_by``, which the answering loop
asks before it puts one on the Answer).

The guard is the point: a button that re-asks the question the answer
already answered is worse than none, so every alternative the reader cases
write is read again here and must reach what it says.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import duckdb
import pytest
from click.testing import CliRunner
from reader_cases import load_cases, read, warehouse

from association.cli.commands import cli
from association.query.answer import Answer
from association.query.compose.plan import PlannedQuery, plan_point
from association.query.entities import find_players, names_of
from association.query.names import ActivePlayers
from association.query.normalizer import Normalized
from association.query.parse import read_route
from association.query.reading import Alternative, Reading

CASES = load_cases()


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    return warehouse()


def _alternatives(con: duckdb.DuckDBPyConnection, question: str, names: list[str] | None = None, stat: str = "") -> list[tuple[str, str, str]]:
    return [(each.read, each.instead, each.question) for each in read(con, question, names, stat).alternatives]


def test_a_career_the_words_imply_offers_the_current_season(con: duckdb.DuckDBPyConnection) -> None:
    """D13's career, with the wording its sentence names: "in 2026"."""
    assert _alternatives(con, "stephen curry 50 point games", ["stephen curry"], "points") == [("career", "season", "stephen curry 50 point games in 2026")]


def test_a_figure_against_an_opponent_offers_the_latest_meeting_too(con: duckdb.DuckDBPyConnection) -> None:
    """One player's figure over every meeting with a team: the last meeting
    is the other reasonable reading (the yardstick's F028, whose key reads
    it), the punctuation kept at the end."""
    assert _alternatives(con, "How many points did Jokic score in the 3rd quarter against Boston?", ["Jokic", "Boston"], "points") == [
        ("career", "season", "How many points did Jokic score in the 3rd quarter against Boston in 2026?"),
        ("career", "latest", "How many points did Jokic score in the 3rd quarter against Boston in his last game?"),
    ]
    # A whole line against a team names no figure: the season alone.
    assert _alternatives(con, "Dennis Schroeder vs. grizzlies", ["Dennis Schroeder", "grizzlies"]) == [("career", "season", "Dennis Schroeder vs. grizzlies in 2026")]


def test_a_season_read_by_default_offers_the_career(con: duckdb.DuckDBPyConnection) -> None:
    """The current season read where no season is named: his career, or
    every season on record for anyone else."""
    assert _alternatives(con, "what was steph curry's avg 3pt shot distance", ["steph curry"], "") == [("season", "career", "what was steph curry's avg 3pt shot distance over his career")]
    assert _alternatives(con, "how many times did the 76ers play boston?", ["76ers", "boston"]) == [("season", "career", "how many times did the 76ers play boston of all time?")]


def test_a_game_of_a_series_read_in_the_season_offers_the_career(con: duckdb.DuckDBPyConnection) -> None:
    """ "game 4" narrows the games, not the seasons: the season's game 4s and
    his career's are the two readings (the yardstick's F150, whose key reads
    the career)."""
    assert _alternatives(con, "Ayton stats in game 4 playoff games", ["Ayton"]) == [("season", "career", "Ayton stats in game 4 playoff games over his career")]


def test_a_player_with_no_game_this_season_is_offered_no_season(con: duckdb.DuckDBPyConnection) -> None:
    """The season would say only that he has no games in it (the feed
    sample's FS168, two retired players' meetings): neither is offered where
    a player named has no box score in the latest season, and the season is
    where one does."""
    question, players = "lamarcus aldridge vs marc gasol", ["lamarcus aldridge", "marc gasol"]
    index = names_of(con)
    gasol = find_players(index.players, "marc gasol", limit=None)[0].id

    def read_with(active: frozenset[str]) -> Reading:
        return read_route(replace(index, read_active=lambda: ActivePlayers(season=2026, ids=active)), question, players, "")

    assert read_with(frozenset()).alternatives == ()
    assert read_with(frozenset({gasol})).alternatives == ()
    everyone = frozenset(each.id for name in players for each in find_players(index.players, name, limit=None))
    assert [each.instead for each in read_with(everyone).alternatives] == ["season"]


@pytest.mark.parametrize(
    ("question", "names", "stat"),
    [
        # A season, a career, a range named: the words settled it.
        ("stephen curry 50 point games in 2024", ["stephen curry"], "points"),
        ("what was steph curry's avg 3pt shot distance this season", ["steph curry"], ""),
        # "this postseason", which the span reads as the default season's type.
        ("show maxey's stats for game 4 against the knicks this postseason", ["maxey"], ""),
        ("steph curry career 3pt shot distance", ["steph curry"], ""),
        ("how many 20+ point games did SGA have in the past two seasons?", ["SGA"], "points"),
        # A window of the newest games is no season's.
        ("jokic last 5 games", ["jokic"], ""),
        # A day named.
        ("jokic stats on 2026-01-05", ["jokic"], ""),
    ],
)
def test_a_span_the_words_settled_offers_nothing(con: duckdb.DuckDBPyConnection, question: str, names: list[str], stat: str) -> None:
    assert _alternatives(con, question, names, stat) == []


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_every_alternative_a_case_writes_reaches_what_it_says(con: duckdb.DuckDBPyConnection, case: dict[str, Any]) -> None:
    """The guard: each wording the parser writes for a reader case, read
    again with the case's own names and stat, reads the alternative's span
    over every other filter as it stood - never the default again (245
    wordings over the 578 cases when written)."""
    reading = read(con, case["question"], case["names"] or None, case["stat"])
    for each in reading.alternatives:
        again = read(con, each.question, case["names"] or None, case["stat"])
        assert each.reached_by(reading, again, reading.asked, again.asked), f"{each.question!r} reads {again.span} {again.window}"


def test_a_wording_that_reads_the_default_again_is_no_alternative(con: duckdb.DuckDBPyConnection) -> None:
    """The guard fails a wording that leaves the span where it was, or moves
    another filter beside it."""
    reading = read(con, "stephen curry 50 point games", ["stephen curry"], "points")
    same = Alternative(kind="span", read="career", instead="season", season=2026, question="stephen curry 50 point games")
    again = read(con, same.question, ["stephen curry"], "points")
    assert not same.reached_by(reading, again, reading.asked, again.asked)
    elsewhere = read(con, "stephen curry 50 point games vs boston in 2026", ["stephen curry", "boston"], "points")
    assert not same.reached_by(reading, elsewhere, reading.asked, elsewhere.asked)
    offered = reading.alternatives[0]
    right = read(con, offered.question, ["stephen curry"], "points")
    assert offered.reached_by(reading, right, reading.asked, right.asked) and isinstance(plan_point(right), PlannedQuery)


def _agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: list[str], stat: str = "") -> Any:
    """An agent over the case warehouse's names, the model's reply fixed and
    the answer side's sentence too: what is offered is the answering loop's."""
    from association.query.agent import Agent

    db_path = tmp_path / "names.duckdb"
    file = duckdb.connect(str(db_path))
    source = warehouse()
    for table in ("players", "teams"):
        rows = source.execute(f"SELECT * FROM {table}").fetchall()
        columns = source.execute(f"DESCRIBE {table}").fetchall()
        file.execute(f"CREATE TABLE {table} ({', '.join(f'{c[0]} {c[1]}' for c in columns)})")
        file.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' for _ in columns)})", rows)
    file.close()

    def composed(ctx: Any, reading: Reading, trace: Any = None, declined: Any = None, planned: Any = None, ran: Any = None, declined_by: Any = None) -> Answer:
        return Answer(data={}, text="Read in the 2026 regular season.")

    monkeypatch.setattr("association.query.compose.answer", composed)
    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, asked: Normalized(list(names), stat))
    return Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history")


def test_the_answer_offers_what_its_wording_reaches_and_records_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The answering loop reads each wording again and offers it; the D13
    sentence keeps the text's place, its decision recording the questions
    offered, and a season default the heading names records its own."""
    agent = _agent(tmp_path, monkeypatch, ["stephen curry"], "points")
    answer = agent.ask("stephen curry 50 point games")
    assert [each.question for each in answer.alternatives] == ["stephen curry 50 point games in 2026"]
    assert answer.text.endswith('(No season was named, so this reads his career - add one, "in 2026", to ask about that season alone.)')
    [implied] = [each for each in answer.decisions if each.kind == "career_implied"]
    assert implied.facts["asks"] == ["stephen curry 50 point games in 2026"]
    line = agent.ask("stephen curry ppg")
    assert [each.question for each in line.alternatives] == ["stephen curry ppg over his career"]
    [default] = [each for each in line.decisions if each.kind == "season_default"]
    assert default.facts == {"season_type": 2, "asks": ["stephen curry ppg over his career"]} and default.instead_of == ("career",)
    assert not agent.unsaid


def test_a_wording_nothing_here_answers_is_not_offered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two players' lines over their careers are declined by the planner (a
    career is no cell the comparison honors): the career's wording reads the
    career, and is still not offered - a button to a refusal is no
    alternative."""
    agent = _agent(tmp_path, monkeypatch, ["stephen curry", "lebron james"])
    reading_of_it = agent.ask("compare stephen curry and lebron james")
    assert agent.reading is not None and [each.instead for each in agent.reading.alternatives] == ["career"]
    assert reading_of_it.alternatives == ()


def test_the_cli_prints_each_alternative_beneath_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI's form of the page's buttons: one "Also:" line each."""
    alternative = Alternative(kind="span", read="season", instead="career", season=2026, question="stephen curry ppg over his career")

    class Fake:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def ask(self, question: str, label: str = "") -> Answer:
            return Answer(text="Stephen Curry averaged 26.6 points per game in the 2026 regular season.", alternatives=(alternative,))

    monkeypatch.setattr("association.query.agent.Agent", Fake)
    result = CliRunner().invoke(cli, ["query", "stephen curry ppg"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-1] == "Also: stephen curry ppg over his career"
