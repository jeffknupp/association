"""Decision D23's mechanism, on D13's class and D25's three: the other
reading of a default the words left open, offered as a ready question - the asker's own words with
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
from association.query.reading import Alternative, Reading, Span

CASES = load_cases()


@pytest.fixture(scope="module")
def con() -> duckdb.DuckDBPyConnection:
    return warehouse()


def _alternatives(con: duckdb.DuckDBPyConnection, question: str, names: list[str] | None = None, stat: str = "", kinds: tuple[str, ...] = ("span", "latest", "round")) -> list[tuple[str, str]]:
    """The alternatives of ``kinds`` the parser writes for ``question``, by
    kind and wording - the span's by default, the season type's beside them
    tested on its own."""
    return [(each.kind, each.question) for each in read(con, question, names, stat).alternatives if each.kind in kinds]


def test_a_career_the_words_imply_offers_the_current_season(con: duckdb.DuckDBPyConnection) -> None:
    """D13's career, with the wording its sentence names: "in 2026"."""
    assert _alternatives(con, "stephen curry 50 point games", ["stephen curry"], "points") == [("span", "stephen curry 50 point games in 2026")]


def test_a_figure_against_an_opponent_offers_the_latest_meeting_too(con: duckdb.DuckDBPyConnection) -> None:
    """One player's figure over every meeting with a team: the last meeting
    is the other reasonable reading (the yardstick's F028, whose key reads
    it), the punctuation kept at the end."""
    assert _alternatives(con, "How many points did Jokic score in the 3rd quarter against Boston?", ["Jokic", "Boston"], "points") == [
        ("span", "How many points did Jokic score in the 3rd quarter against Boston in 2026?"),
        ("latest", "How many points did Jokic score in the 3rd quarter against Boston in his last game?"),
    ]
    # Both season types are not offered beside it: the latest meeting reads both (decision D14), so its question
    # would change the other's slot (decision D25's guard).
    assert [each.kind for each in read(con, "How many points did Jokic score in the 3rd quarter against Boston?", ["Jokic", "Boston"], "points").alternatives] == ["span", "latest"]
    # A whole line against a team names no figure: the season alone.
    assert _alternatives(con, "Dennis Schroeder vs. grizzlies", ["Dennis Schroeder", "grizzlies"]) == [("span", "Dennis Schroeder vs. grizzlies in 2026")]
    # Nor a game's high, or a log asked by name: no latest meeting is the
    # other reading of either.
    assert [kind for kind, _ in _alternatives(con, "luka highest scoring games vs bucks", ["luka", "bucks"], "points")] == ["span"]
    assert [kind for kind, _ in _alternatives(con, "harrison barnes 1st quarter points each game vs magic", ["harrison barnes", "magic"], "points")] == ["span"]


def test_a_season_read_by_default_offers_the_career(con: duckdb.DuckDBPyConnection) -> None:
    """The current season read where no season is named: his career, or
    every season on record for anyone else."""
    assert _alternatives(con, "what was steph curry's avg 3pt shot distance", ["steph curry"], "") == [("span", "what was steph curry's avg 3pt shot distance over his career")]
    assert _alternatives(con, "how many times did the 76ers play boston?", ["76ers", "boston"]) == [("span", "how many times did the 76ers play boston of all time?")]


def test_a_round_or_a_game_of_a_series_read_in_the_season_offers_the_career(con: duckdb.DuckDBPyConnection) -> None:
    """ "game 4" and "the finals" narrow the games, not the seasons: the
    season's and his career's are the two readings (ISSUES.md #519, decision
    D25: the yardstick's F150 and the feed sample's FS189, whose keys read
    the career)."""
    assert _alternatives(con, "Ayton stats in game 4 playoff games", ["Ayton"]) == [("round", "Ayton stats in game 4 playoff games over his career")]
    assert _alternatives(con, "jimmy butler nba finals stats", ["jimmy butler"]) == [("round", "jimmy butler nba finals stats over his career")]
    [career] = read(con, "jimmy butler nba finals stats", ["jimmy butler"]).alternatives
    assert career.span == Span(season_type=3, career=True)


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

    def spans(reading: Reading) -> list[str]:
        return [each.kind for each in reading.alternatives if each.kind != "season_type"]

    assert spans(read_with(frozenset())) == []
    assert spans(read_with(frozenset({gasol}))) == []
    everyone = frozenset(each.id for name in players for each in find_players(index.players, name, limit=None))
    assert spans(read_with(everyone)) == ["span"]


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
    same = Alternative(kind="span", span=Span(season=2026, season_type=2), question="stephen curry 50 point games")
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
    assert [each.question for each in answer.alternatives] == ["stephen curry 50 point games in 2026", "stephen curry 50 point games including the playoffs"]
    assert answer.text.endswith('(No season was named, so this reads his career - add one, "in 2026", to ask about that season alone.)')
    [implied] = [each for each in answer.decisions if each.kind == "career_implied"]
    assert implied.facts["asks"] == ["stephen curry 50 point games in 2026"]
    line = agent.ask("stephen curry ppg")
    assert [each.question for each in line.alternatives] == ["stephen curry ppg over his career", "stephen curry ppg including the playoffs"]
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
    assert agent.reading is not None and [each.kind for each in agent.reading.alternatives] == ["span", "season_type"]
    assert agent.reading.alternatives[0].span.career
    assert reading_of_it.alternatives == ()


def test_a_career_the_shapes_reader_does_not_state_is_not_offered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ranking of teams states no career (its row's ``TEAM_RANKING_CAREER``):
    "of all time" reads the career and plans, and the reader would decline
    it - "every season of a career cannot be read here" - so it is not
    offered."""
    agent = _agent(tmp_path, monkeypatch, [])
    answer = agent.ask("which team scores the most points per game")
    assert agent.reading is not None and [each.question for each in agent.reading.alternatives if each.kind == "span"] == ["which team scores the most points per game of all time"]
    assert answer.alternatives == ()


def test_the_cli_prints_each_alternative_beneath_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI's form of the page's buttons: one "Also:" line each."""
    alternative = Alternative(kind="span", span=Span(season_type=2, career=True), question="stephen curry ppg over his career")

    class Fake:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def ask(self, question: str, label: str = "") -> Answer:
            return Answer(text="Stephen Curry averaged 26.6 points per game in the 2026 regular season.", alternatives=(alternative,))

    monkeypatch.setattr("association.query.agent.Agent", Fake)
    result = CliRunner().invoke(cli, ["query", "stephen curry ppg"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[-1] == "Also: stephen curry ppg over his career"


def test_the_regular_season_read_by_default_offers_both_season_types(con: duckdb.DuckDBPyConnection) -> None:
    """No season type named and the regular season read alone: "including
    the playoffs" (ISSUES.md #231, decision D25; the yardstick's F062,
    whose key counts 31 games of both types), the span's alternative beside
    it changing the span alone - two slots, two questions, never their
    product."""
    question = "vj edgecombe three points made per game after making one three in first quarter"
    alternatives = read(con, question, ["vj edgecombe"], "threePointFieldGoalsMade").alternatives
    assert [(each.kind, each.question) for each in alternatives] == [("span", f"{question} over his career"), ("season_type", f"{question} including the playoffs")]
    assert alternatives[1].span == Span(season_type=2, both=True)
    # Over D13's career too (the yardstick's F059: the key's 38 games, 9 of them playoff games).
    [both] = _alternatives(con, "Kd vs clippers 2h at home gamelog", ["Kd", "clippers"], kinds=("season_type",))
    assert both == ("season_type", "Kd vs clippers 2h at home gamelog including the playoffs")


@pytest.mark.parametrize(
    ("question", "names"),
    [
        # A season type named: the regular season, the postseason, or both.
        ("jaylen brown vs knicks last season regular season stats", ["jaylen brown"]),
        ("lebron playoff stats in 2016", ["lebron"]),
        ("jokic stats in 2024 including the playoffs", ["jokic"]),
        # A window of the newest games reads both already (decision D14).
        ("jokic last 5 games", ["jokic"]),
    ],
)
def test_a_season_type_the_words_settled_offers_nothing(con: duckdb.DuckDBPyConnection, question: str, names: list[str]) -> None:
    assert _alternatives(con, question, names, kinds=("season_type",)) == []


def test_last_season_in_the_off_season_offers_the_season_just_finished(con: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch) -> None:
    """On the day after a season ends a fan's "last season" is the season
    just finished, the convention's the one before (decision D25; the feed
    sample's FS302, a rookie whose only season is 2026): offered with the
    season written out where "last season" stood. During a season "last
    season" is only the one before, and nothing is offered."""
    [finished] = read(con, "egor vs tor last season", ["egor", "tor"]).alternatives[:1]
    assert (finished.kind, finished.question) == ("last_season", "egor vs tor 2025-26 season")
    assert finished.span == Span(season=2026, season_type=2)
    assert _alternatives(con, "nba last season steals leader", kinds=("last_season",)) == [("last_season", "nba 2025-26 season steals leader")]
    # A year written out is no relative season.
    assert _alternatives(con, "jokic stats in 2025", ["jokic"], kinds=("last_season",)) == []
    monkeypatch.setenv("ASSOCIATION_TODAY", "2026-01-15")
    assert _alternatives(con, "egor vs tor last season", ["egor", "tor"], kinds=("last_season",)) == []


def test_each_alternative_changes_its_own_slot_alone(con: duckdb.DuckDBPyConnection) -> None:
    """Decision D25's guard against a product: an alternative's span differs
    from the reading's in its own slot only - the seasons, the career or the
    season type - over every reader case."""
    for case in CASES:
        reading = read(con, case["question"], case["names"] or None, case["stat"])
        for each in reading.alternatives:
            moved = {name for name in ("season", "career", "both", "since", "until", "season_type") if getattr(each.span, name) != getattr(reading.span, name)}
            allowed = {"season_type": {"both"}, "last_season": {"season"}, "round": {"career"}, "latest": {"both"}, "span": {"season", "career"}}[each.kind]
            assert moved and moved <= allowed, (case["question"], each)
        kinds = [each.kind for each in reading.alternatives]
        assert not ("latest" in kinds and "season_type" in kinds), case["question"]
