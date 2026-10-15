"""Tests of the answering loop: the fast path's answers, and the refusal
naming why where nothing here reads a question."""

from pathlib import Path
from typing import Any

import ollama
import pytest
from routed import Routed, ask_routed

from association.query.agent import Agent
from association.query.compose.plan import planned_shape
from association.query.reading import (
    GAMES_COUNTED,
    LINE_RECORD,
    NETPOINTS_FINGERPRINT,
    PLAYER_COMPARISON,
    PLAYER_LINE,
    PLAYER_LOG,
    PLAYER_RANKING,
    SHOT_CHART,
    SHOT_DISTANCE,
    TEAM_MEETINGS,
    TEAM_RANKING,
    TEAM_RECORD,
    Cuts,
    Line,
    Measure,
    PointShape,
    Reading,
    Span,
    Window,
    key_path,
)
from association.query.reading import Subject as Who


def test_ask_writes_history_file_even_without_verbose(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The whole point of RunHistory: a run's full evidence (command, trace,
    timing, answer) must land on disk regardless of whether --verbose was
    passed - not verbose here, and nothing on stderr, but the history file
    must still exist and hold everything."""
    from association.query.answer import Answer
    from association.query.normalizer import Normalized

    history_dir = tmp_path / ".history"
    agent = _agent_with_players(tmp_path, "Joel Embiid")
    agent.history_dir = history_dir
    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: Normalized(["embiid"], "points"))
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None, declined_by=None: Answer(data={}, text="Final answer."))
    result = agent.ask("how many points does embiid average").text

    assert result == "Final answer."
    files = list(history_dir.glob("*.log"))
    assert len(files) == 1
    content = files[0].read_text()
    assert "question: how many points does embiid average" in content
    assert "model inference #1" in content
    assert "model: qwen2.5:3b" in content
    assert "answer:\nFinal answer." in content


def test_ask_writes_history_file_even_when_it_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A crash mid-run is exactly a "failed run" this exists to leave evidence
    for - the history file must still be written, with the traceback as the
    recorded answer, rather than lost because ask() never returned normally."""
    import duckdb

    db_path = tmp_path / "test.duckdb"
    duckdb.connect(str(db_path)).close()
    history_dir = tmp_path / ".history"
    agent = Agent(str(db_path), tmp_path / "out", history_dir=history_dir)

    def failing(model: str, question: str) -> None:
        raise RuntimeError("simulated ollama connection failure")

    monkeypatch.setattr("association.query.normalizer.normalize", failing)
    with pytest.raises(RuntimeError, match="simulated ollama connection failure"):
        agent.ask("some question about jokic")

    files = list(history_dir.glob("*.log"))
    assert len(files) == 1
    content = files[0].read_text()
    assert "EXCEPTION" in content
    assert "simulated ollama connection failure" in content


def _agent(tmp_path: Path, **kwargs: Any) -> Agent:
    import duckdb

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    # The two tables every warehouse has, empty: the subject reading
    # (query/subject.py) asks them who the question names on every fast-path
    # answer.
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.close()
    return Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history", **kwargs)


def _agent_with_players(tmp_path: Path, *names: str) -> Agent:
    """An agent over a warehouse holding exactly ``names``, for the checks that
    run against the question's own words."""
    import duckdb

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    # An empty teams table too: the compiler asks whether a player's name
    # is a team's before it resolves it as a player's.
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    for i, name in enumerate(names):
        con.execute("INSERT INTO players VALUES (?, ?)", [str(i), name])
    con.close()
    return Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history")


def test_the_fast_path_replaces_a_player_the_question_never_named(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A name the model invents is replaced by the one the question holds
    before the answer side is reached: "compare sga and embiid", with Jusuf
    Nurkic second in the model's reply, reaches ``compose.answer`` naming
    the two players the question names."""
    from association.query.answer import Answer

    seen: list[str] = []

    def record(ctx: Any, reading: Reading) -> Answer:
        seen.extend(reading.who.players)
        return Answer(data={}, text="templated")

    # A comparison of players is the compiler's: the Reading reaches compose.answer.
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None, declined_by=None: record(ctx, reading))
    ask_routed(
        _agent_with_players(tmp_path, "Joel Embiid", "Jusuf Nurkic"),
        "compare sga and embiid",
        Routed(PLAYER_COMPARISON, Reading(who=Who(kind="pair", players=("Shai Gilgeous-Alexander", "Jusuf Nurkic")))),
    )
    assert seen == ["Shai Gilgeous-Alexander", "Joel Embiid"]


def test_the_fast_path_records_who_the_question_was_read_to_be_about(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The subject reading (query/subject.py) runs on every fast-path answer
    and is recorded as decisions - values on the Answer and a ``decisions:``
    section of the history record."""
    from association.query.answer import Answer

    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None, declined_by=None: Answer(data={}, text="templated"))
    agent = _agent_with_players(tmp_path, "Joel Embiid", "Jusuf Nurkic")
    answer = ask_routed(agent, "compare sga and embiid", Routed(PLAYER_COMPARISON, Reading(who=Who(kind="pair", players=("Shai Gilgeous-Alexander", "Jusuf Nurkic")))))
    stages = [(d.stage, d.field, d.after) for d in answer.decisions]
    assert ("subject", "kind", "pair") in stages
    assert ("subject", "players", ["Shai Gilgeous-Alexander", "Joel Embiid"]) in stages  # in the question's own order; Nurkic, whom it never names, is not there
    record = next(iter((tmp_path / ".history").glob("*.log"))).read_text()
    assert "decisions:\n" in record and '"stage": "subject"' in record and "  -> (decision) subject kind: 'pair'" in record


def test_a_team_only_shape_naming_one_player_refuses_rather_than_answering_the_league(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """yardstick-v2 F111: "alperen şengün alltime record", read as a ranking
    of teams with no team named, is refused naming the player rather than
    answered with the league's standings: a ranking of teams has no reading
    for a named player, so ``compose.answer`` is never reached. Diacritics
    ("şengün") are folded before this runs."""

    reached = False

    def record(ctx: Any, reading: Reading, **_: Any) -> Any:
        nonlocal reached
        reached = True
        raise AssertionError("a ranking of teams should not be read at all")

    # The compiler answers a ranking of teams: it is what must not be reached.
    monkeypatch.setattr("association.query.compose.answer", record)
    answer = ask_routed(
        _agent_with_players(tmp_path, "Alperen Sengun"), "alperen şengün alltime record", Routed(TEAM_RANKING, Reading(measure=Measure(key="record", as_typed="record"), window=Window(count=1)))
    )
    assert not reached
    assert "Alperen Sengun" in answer.text
    assert "a ranking of teams has no reading for one" in answer.text


def test_a_team_only_shape_with_a_team_named_is_unaffected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A genuine team question - a team on the Reading's ``who`` resolving to
    a REAL franchise - is answered normally, by the compiler, whatever
    player-shaped words also appear. A ``teams`` table holding the team is
    needed here: ``subject._has_a_real_team`` looks the team up in the name
    index read from it, and a warehouse missing the table entirely reads as
    "no team found" the same way ``entities._team_named`` does."""
    import duckdb

    from association.query.answer import Answer

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1', 'Alperen Sengun')")
    con.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('2', 'Houston Rockets', 'HOU')")
    con.close()
    agent = Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history")

    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, **_: Answer(data={}, text="templated"))
    answer = ask_routed(agent, "alperen şengün rockets record", Routed(TEAM_RANKING, Reading(who=Who(kind="team", teams=("Houston Rockets",)), measure=Measure(key="record", as_typed="record"))))
    assert answer.text == "templated"


def test_a_model_that_could_not_be_asked_is_refused_saying_why(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A reader failure is a refusal, never an answer, and it names the model
    ollama could not serve rather than reporting a reply the model never
    gave ("no usable reply")."""
    from association.query.history import RunHistory
    from association.query.normalizer import ModelUnavailable

    agent = _agent(tmp_path)

    def unavailable(*a: Any, **k: Any) -> None:
        raise ModelUnavailable("ollama could not serve the model 'qwen2.5:3b': not found")

    monkeypatch.setattr("association.query.normalizer.normalize", unavailable)
    assert agent._try_fast_path("who leads the league in assists?", RunHistory(False, tmp_path / ".history")) is None
    assert agent.unanswered is not None
    assert "model 'qwen2.5:3b'" in agent.unanswered
    assert "no usable reply" not in agent.unanswered
    answer = agent.ask("who leads the league in assists?")
    assert answer.answered_by == "refused" and "model 'qwen2.5:3b'" in answer.text


def test_a_team_record_rewritten_to_a_record_over_a_line_is_answered_as_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A team's record in the games a companion reached a line arrives as a
    team's record and is rewritten to a record over a line - the reading's
    one rewrite of the key (``subject._apply_intent``). The ANSWERING PATH
    moves with the key: ``compose.answer`` is asked under the record over a
    line, never under the team's record. Only a test on this path sees a
    reader chosen before the rewrite, since an offline replay looks the
    reader up after it."""
    from association.query.answer import Answer

    paths: list[str] = []

    def composed(ctx: Any, reading: Reading, trace: Any = None, declined: Any = None, planned: Any = None, ran: Any = None, declined_by: Any = None) -> Answer:
        del ran  # the agent's own callback, not this test's record of the paths taken
        paths.append(f"compose {key_path(reading.asked)}")
        return Answer(data={}, text="composed")

    import duckdb

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1', 'Tyrese Maxey')")
    con.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('20', 'Philadelphia 76ers', 'PHI')")
    con.close()
    agent = Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history")

    monkeypatch.setattr("association.query.compose.answer", composed)
    answer = ask_routed(
        agent,
        "sixers record when maxey scored 20+ points",
        Routed(TEAM_RECORD, Reading(who=Who(kind="team", teams=("Philadelphia 76ers",)), measure=Measure(key="points", as_typed="points"), span=Span(season_type=2))),
    )
    assert paths == ["compose player_games/split/line"]
    assert agent.reading is not None and agent.reading.asked == LINE_RECORD and "composed" in (answer.text or "")


def test_a_player_the_question_cannot_account_for_is_refused_not_passed_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A player the model names that the question cannot account for is
    never passed on to a reader, since an invented name resolves to a real
    player nobody asked about. Same reasoning as check_coverage returning its
    refusal rather than raising it: the refusal is a fast answer naming the
    player, not the plain refusal for want of a reading."""
    from association.query.answer import Answer

    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None, declined_by=None: Answer(data={}, text="templated"))
    answer = ask_routed(
        _agent_with_players(tmp_path, "Joel Embiid", "Jusuf Nurkic"), "compare the two best centers", Routed(PLAYER_COMPARISON, Reading(who=Who(kind="pair", players=("Jusuf Nurkic", "Joel Embiid"))))
    )
    assert "was not answered" in answer.text and "Jusuf Nurkic" in answer.text
    assert answer.answered_by == "fast" and "templated" not in answer.text


def test_a_stray_name_on_a_shape_that_reads_no_player_changes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Two teams' meetings read no player, so an invented one on the Reading
    cannot make the answer about the wrong person - and refusing over it would
    break a question that works."""
    from association.query.answer import Answer

    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None, ran=None, declined_by=None: Answer(data={}, text="templated"))
    assert (
        ask_routed(_agent_with_players(tmp_path, "Joel Embiid", "Jusuf Nurkic"), "Lakers vs Celtics record", Routed(TEAM_MEETINGS, Reading(who=Who(kind="player", players=("Jusuf Nurkic",))))).text
        == "templated"
    )


def test_a_fingerprint_that_lost_a_player_to_a_typo_says_so(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ "generate fingerprints for embiid vs jolic in 2026" draws Joel Embiid
    alone. The typo cannot be repaired, so the half-answer is stated -
    one polygon where two were asked for, with nothing saying so, is the
    failure shape this project keeps producing."""
    from association.query.answer import Answer

    # The note is attached to what compose.answer hands back
    # (agent._unmatched_fingerprint).
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None, declined_by=None: Answer(data={}, text="Rendered."))
    answer = ask_routed(
        _agent_with_players(tmp_path, "Joel Embiid"), "generate fingerprints for embiid vs jolic in 2026", Routed(NETPOINTS_FINGERPRINT, Reading(who=Who(kind="player", players=("Joel Embiid",))))
    ).text
    assert answer.startswith("Rendered.") and "only one of them matches" in answer


def test_the_fast_path_says_how_it_read_a_name_the_question_left_open(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The reading reaches the ANSWER, which is the whole condition the default
    is allowed under: "maxey" is Tyrese because he is the only Maxey who still
    plays, and somebody who meant Marlon has to be told what to type instead.
    A reader is called with a bare connection, so the sentence travels by
    ``entities.collect_name_readings`` and is attached by the answering loop -
    without it, a right answer about a player nobody said was chosen."""
    from association.query.answer import Answer
    from association.query.entities import Entity, _note_name_reading

    def reads_a_name(ctx: Any, reading: Reading) -> Answer:
        _note_name_reading("maxey", Entity("1", "Tyrese Maxey"), [Entity("0", "Marlon Maxey")], 2026, named_in_full=False)
        return Answer(data={}, text="Tyrese Maxey averaged 28.0 points.")

    # The name reading travels from compose.answer through agent._try_compose.
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None, declined_by=None: reads_a_name(ctx, reading))
    answer = ask_routed(_agent_with_players(tmp_path, "Marlon Maxey", "Tyrese Maxey"), "how many points does maxey average?", Routed(PLAYER_LINE, Reading(who=Who(kind="player", players=("maxey",)))))
    reading = "('maxey' was read as Tyrese Maxey, the only match who played in 2025-26. Marlon Maxey also matches - use the full name, or name a season he played, to ask about him.)"
    assert answer.text == f"Tyrese Maxey averaged 28.0 points. {reading}"
    # The reading rides in `notes` too, for the web page to show beneath a
    # rendered table (agent._note).
    assert answer.data == {"name_readings": [reading], "notes": [reading]}


def test_a_question_nothing_reads_is_refused_naming_why(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Where no reader answers, the answer is a refusal that names why. No
    model is asked past the normalizer."""
    from association.query.agent import refusal_text
    from association.query.answer import Answer

    def chat_must_not_run(**kw: Any) -> None:
        raise AssertionError("no model is asked past the normalizer")

    monkeypatch.setattr(ollama, "chat", chat_must_not_run)
    agent = _agent_with_players(tmp_path)

    answer = ask_routed(agent, "who had the most triple-doubles?", Routed(None))
    # The no_shape cause, said by the planner.
    assert answer.answered_by == "fast" and agent.unanswered is None
    assert answer.text == refusal_text("what its words ask has no reader")
    assert answer.shape is None and answer.data == {"message": answer.text}

    # A reader's own refusal while reading, with its reason: a fingerprint of
    # nobody, a cause the planner says naming what the read needs a name of.
    answer = ask_routed(agent, "a question nothing reads", Routed(NETPOINTS_FINGERPRINT))
    assert answer.answered_by == "fast" and agent.unanswered is None
    assert answer.text == "Nothing here answers this question as a NetPoints fingerprint: it needs a player's name, and none was read."

    # A shape with no reading of the question is refused with the point
    # reader's cause, in the decline's sentence: a log of nobody.
    answer = ask_routed(agent, "a game log", Routed(PLAYER_LOG))
    assert answer.answered_by == "fast" and agent.unanswered is None
    assert answer.text == "Nothing here answers this question as a game log: no player subject and no ranking or position-group reading of the question."
    # And says a cause where the reading came to one, as an answer: a count
    # with no line to count.
    answer = ask_routed(agent, "how many games", Routed(GAMES_COUNTED, Reading(measure=Measure(key="points", as_typed="points"))))
    assert answer.answered_by == "fast" and answer.text.startswith("A count of games across the league needs the line it counts")
    assert agent.unanswered is None

    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: None)
    answer = agent.ask("what is this question")
    assert answer.answered_by == "refused" and "no usable reply" in answer.text

    # A question its reader answers is unaffected.
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None, declined_by=None: Answer(data={}, text="answered"))
    answer = ask_routed(agent, "a question nothing reads", Routed(NETPOINTS_FINGERPRINT))
    assert (answer.text, answer.answered_by, agent.unanswered) == ("answered", "fast", None)


# ---------------- the compiler's answers and the refusals naming why ----------------


def test_a_composed_answer_is_returned_as_fast_with_the_trace_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The compiler's answer is answered_by="fast", with a trace line naming
    the key the words asked, the planned shape and the point on the relation
    it composed, the way "-> (parser) asked=..." names what the words ask
    (each a key, ``relation/shape/by``)."""
    from association.query.answer import Answer

    def composed_answer(ctx: Any, reading: Reading, trace: Any = None, declined: Any = None, planned: Any = None, ran: Any = None, declined_by: Any = None) -> Answer:
        return Answer(data={"skeleton": "aggregate", "measures": ["points"], "rows": [{"points": 30.0}]}, text="Joel Embiid has averaged 30.0 points since 2024.")

    monkeypatch.setattr("association.query.compose.answer", composed_answer)

    seen: list[str] = []
    agent = _agent_with_players(tmp_path, "Joel Embiid")
    agent.trace = seen.append
    agent.verbose = True
    answer = ask_routed(agent, "how many points has embiid averaged since 2024?", Routed(PLAYER_LINE, Reading(who=Who(kind="player", players=("Joel Embiid",)), span=Span(since=2024))))

    assert answer.text == "Joel Embiid has averaged 30.0 points since 2024."
    assert answer.answered_by == "fast"
    assert agent.reading is not None and agent.reading.asked == PLAYER_LINE
    assert answer.data == {"skeleton": "aggregate", "measures": ["points"], "rows": [{"points": 30.0}]}
    compose_lines = [line for line in seen if "(compose)" in line]
    assert len(compose_lines) == 1
    assert "asked=player_seasons/scalar/line shape=player_games/scalar/line" in compose_lines[0]
    assert any("-> (parser) asked=player_seasons/scalar/line kind=" in line for line in seen), seen
    assert "'skeleton': 'aggregate'" in compose_lines[0] and "'measures': ['points']" in compose_lines[0]
    # The point is what was composed, not the rows it produced.
    assert "rows" not in compose_lines[0]


def test_a_clarification_from_the_compiler_is_returned_as_a_fast_answer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A clarification or a no-match from the compiler is an answer, not a
    refusal for want of a reading: it looked at the question and had
    something to say."""
    from association.query.answer import Answer

    def composed_refusal(ctx: Any, reading: Reading, trace: Any = None, declined: Any = None, planned: Any = None, ran: Any = None, declined_by: Any = None) -> Answer:
        return Answer(data={"ambiguous": "since"}, text="I can't tell which span 'the last few' means - a number of games, or a number of seasons?")

    monkeypatch.setattr("association.query.compose.answer", composed_refusal)

    answer = ask_routed(
        _agent_with_players(tmp_path, "Joel Embiid"), "embiid's line over the last few?", Routed(PLAYER_LINE, Reading(who=Who(kind="player", players=("Joel Embiid",)), span=Span(since=2024)))
    )

    assert answer.text == "I can't tell which span 'the last few' means - a number of games, or a number of seasons?"
    assert answer.answered_by == "fast"


def test_a_composed_answer_carries_the_name_reading_it_noted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The compiler reads a name through ``entities.collect_name_readings``,
    so a default it chose ("maxey" is Tyrese) is visible in the answer."""
    from association.query.answer import Answer
    from association.query.entities import Entity, _note_name_reading

    def composed_with_reading(ctx: Any, reading: Reading, trace: Any = None, declined: Any = None, planned: Any = None, ran: Any = None, declined_by: Any = None) -> Answer:
        _note_name_reading("maxey", Entity("1", "Tyrese Maxey"), [Entity("0", "Marlon Maxey")], 2026, named_in_full=False)
        return Answer(data={}, text="Tyrese Maxey has averaged 28.0 points since 2024.")

    monkeypatch.setattr("association.query.compose.answer", composed_with_reading)

    answer = ask_routed(
        _agent_with_players(tmp_path, "Marlon Maxey", "Tyrese Maxey"),
        "how many points has maxey averaged since 2024?",
        Routed(PLAYER_LINE, Reading(who=Who(kind="player", players=("maxey",)), span=Span(since=2024))),
    )

    reading = "('maxey' was read as Tyrese Maxey, the only match who played in 2025-26. Marlon Maxey also matches - use the full name, or name a season he played, to ask about him.)"
    assert answer.text == f"Tyrese Maxey has averaged 28.0 points since 2024. {reading}"
    assert answer.data == {"name_readings": [reading], "notes": [reading]}


def test_words_that_ask_for_no_shape_are_refused_saying_so(tmp_path: Path) -> None:
    """Words that name no shape a reader reads are refused saying so, in
    seconds."""

    answer = ask_routed(_agent(tmp_path), "who had the most triple-doubles?", Routed(None))
    # The no_shape cause, said by the planner.
    assert answer.answered_by == "fast" and answer.text == "Nothing here answers this question: what its words ask has no reader."


def test_a_reader_failure_is_refused_rather_than_erroring(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: None)
    answer = _agent(tmp_path).ask("what is this question")
    assert answer.answered_by == "refused" and "the normalizer returned no usable reply" in answer.text


def test_the_fast_path_carries_out_the_shape_and_the_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """An answer carries the planned shape and the sayer's data beside its
    text, so a caller can render the answer itself."""
    from association.query.answer import Answer

    # A ranking of players is the compiler's: the Reading reaches compose.answer.
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None, declined_by=None: Answer(data={"leaders": ["Jokic"]}, text="Jokic."))
    answer = ask_routed(_agent_with_players(tmp_path), "who leads the league in scoring?", Routed(PLAYER_RANKING, Reading(measure=Measure(key="points", as_typed="points"))))

    assert answer.text == "Jokic."
    assert answer.answered_by == "fast"
    assert answer.shape == PLAYER_RANKING
    assert answer.data == {"leaders": ["Jokic"]}
    assert answer.question == "who leads the league in scoring?"


def test_an_answer_carries_the_shape_the_answer_side_chose_its_reader_by(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The page renders an answer's data by the planned point's shape: the
    one the answer side chose its reader by (``planned_shape`` over
    ``Agent.query``), carried out beside the data and named in the trace's
    (compose) line - never re-derived from the question."""
    from association.query.answer import Answer

    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None, declined_by=None: Answer(data={"leaders": ["Jokic"]}, text="Jokic."))
    seen: list[str] = []
    agent = _agent_with_players(tmp_path)
    agent.trace = seen.append
    agent.verbose = True
    answer = ask_routed(agent, "who leads the league in scoring?", Routed(PLAYER_RANKING, Reading(measure=Measure(key="points", as_typed="points"))))

    assert agent.reading is not None and agent.query is not None
    assert answer.shape == planned_shape(agent.reading, agent.query) == PointShape("player_seasons", "ranking", "player")
    assert any("-> (compose)" in line and "shape=player_seasons/ranking/player" in line for line in seen), seen


def test_a_refused_answer_has_no_shape_or_data_rather_than_an_empty_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Only a reader or the compiler produces those. None says so; {} would
    read as "a reader ran and found nothing", which is a different claim."""
    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: None)
    answer = _agent(tmp_path).ask("what is this question")

    assert answer.answered_by == "refused"
    assert answer.shape is None
    assert answer.data is None


def test_a_fast_path_answer_carries_the_chart_its_reader_drew(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from association.query.answer import Answer, Artifact

    drawn = Artifact("shot_chart", tmp_path / "shotchart_x.html")
    # A shot chart is the shot relation's reader's (compose.shots): its answer stands in.
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, **_: Answer(data={}, text="Rendered.", artifacts=[drawn]))
    assert ask_routed(_agent(tmp_path), "a chart of x", Routed(SHOT_CHART, Reading(who=Who(kind="player", players=("x",))))).artifacts == [drawn]


def test_the_history_label_comes_from_the_caller_not_the_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`shlex.join(sys.argv)` describes a CLI invocation and nothing else - for
    a server handling many questions it is the same wrong string every time."""
    import duckdb

    db_path = tmp_path / "test.duckdb"
    duckdb.connect(str(db_path)).close()
    history_dir = tmp_path / ".history"

    agent = _agent_with_players(tmp_path)
    agent.history_dir = history_dir
    ask_routed(agent, "a question nothing reads", Routed(None), label="POST /api/ask")

    assert "command: POST /api/ask" in next(iter(history_dir.glob("*.log"))).read_text()


def test_a_trace_sink_takes_the_place_of_stderr_entirely(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Including the [history] line: a server that captures the trace sees
    nothing of it on its own terminal."""
    import duckdb

    db_path = tmp_path / "test.duckdb"
    duckdb.connect(str(db_path)).close()
    seen: list[str] = []
    agent = Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history", verbose=True, trace=seen.append)
    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: None)

    agent.ask("what is this question")

    assert any(line.startswith("[history] ") for line in seen)
    assert any("model inference #1" in line for line in seen)
    assert any(line.startswith("  -> (refused) ") for line in seen)
    assert capsys.readouterr().err == ""


def test_the_refusal_names_what_the_fast_path_could_not_answer(tmp_path: Path) -> None:
    """The refusal says which part of the question has no answer here, and
    is that reason and nothing else."""
    from association.query.agent import refusal_text

    agent = _agent_with_players(tmp_path)
    answer = ask_routed(agent, "who had the most triple-doubles?", Routed(None))
    assert answer.text == refusal_text("what its words ask has no reader") == "Nothing here answers this question: what its words ask has no reader."


def test_a_shape_nothing_reads_is_refused_with_its_cause(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """After the compiler declines, a shape the warehouse has no column for
    (``Reading.unsupported``, the parser's) is refused with its cause as a
    fast answer, in the planner's sentence - not with the cut. A playoff
    round is the worked case: no relation honors the ``round`` cut, and the
    games carry no round label."""

    monkeypatch.setattr("association.query.compose.answer", lambda *a, **k: None)

    agent = _agent_with_players(tmp_path, "Joel Embiid")
    answer = ask_routed(agent, "nba finals game log 2025", Routed(PLAYER_LOG, Reading(who=Who(kind="team", teams=("NBA Finals",)), cuts=Cuts(round="finals"), span=Span(season=2025, season_type=3))))

    assert "not labeled by playoff round" in answer.text
    assert answer.answered_by == "fast"
    assert agent.unanswered is None


def test_words_asking_no_shape_are_refused_by_what_they_name_that_nothing_reads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Words asking no shape are refused saying so; what the words name that
    nothing reads (``Reading.unsupported``) is said first, so "bench points"
    is refused with its cause rather than with the missing shape. The parser
    reads it from the words (the measure the words name)."""
    from association.query.normalizer import Normalized

    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: Normalized([], "points"))
    agent = _agent_with_players(tmp_path, "Joel Embiid")
    answer = agent.ask("most opponent bench points allowed in the west at home by team this month")

    assert "Bench points are not read yet" in answer.text
    assert answer.answered_by == "fast"


def test_an_answer_names_the_history_file_it_was_recorded_to(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`Agent.ask` sets `Answer.history_file` to the record's basename in the
    same step that writes it, so a caller (the web runner's note feature)
    reads it as a value and never parses the `[history] ...` trace line."""
    import duckdb

    db_path = tmp_path / "test.duckdb"
    duckdb.connect(str(db_path)).close()
    history_dir = tmp_path / ".history"

    agent = _agent_with_players(tmp_path)
    agent.history_dir = history_dir
    agent.trace = lambda line: None
    answer = ask_routed(agent, "a question nothing reads", Routed(None))

    written = [p.name for p in history_dir.glob("*.log")]
    assert answer.history_file is not None and answer.history_file in written
    assert "/" not in answer.history_file


def test_a_reading_is_planned_and_answered_by_the_compiler_alone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Every shape the words ask is answered by the compiler from the
    Reading; the trace carries the record ("-> (reading) ..."). Where the
    compiler declines, the question is refused naming the compiler's
    reason."""
    from association.query.answer import Answer

    calls: list[str] = []

    def composed_first(ctx: Any, reading: Reading, trace: Any = None, declined: Any = None, planned: Any = None, ran: Any = None, declined_by: Any = None) -> Answer:
        calls.append("compose")
        if trace is not None:
            trace(
                Reading(
                    **(
                        Reading(
                            who=Who(kind="player", players=("Joel Embiid",)),
                            lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),),
                            measure=Measure(key="points", as_typed="points"),
                        )
                    ).filters(),
                    read_as=PointShape("player_games", "scalar"),
                    measures=(),
                    group="none",
                    predicates=[("points", ">=", 30)],
                    asked=reading.asked,
                )
            )
        return Answer(data={"count": 9}, text="Joel Embiid had 9 games with 30+ points.")

    recorded = Routed(
        GAMES_COUNTED,
        Reading(who=Who(kind="player", players=("Joel Embiid",)), lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),), measure=Measure(key="points", as_typed="points")),
    )
    monkeypatch.setattr("association.query.compose.answer", composed_first)

    seen: list[str] = []
    agent = _agent_with_players(tmp_path, "Joel Embiid")
    agent.trace = seen.append
    agent.verbose = True
    answer = ask_routed(agent, "how many 30 point games did embiid have?", recorded)

    assert answer.text == "Joel Embiid had 9 games with 30+ points." and answer.answered_by == "fast" and agent.reading is not None and agent.reading.asked == GAMES_COUNTED
    assert calls == ["compose"]
    reading_lines = [line for line in seen if "(reading)" in line]
    expected = "  -> (reading) relation=player subject=? shape=scalar by= on=player_games measures=[] group=none predicates=[('points', '>=', 30)] window=date/desc"
    # The filters print in the Reading's field order, each typed value as the fields it sets, whatever order they were set in.
    who, lines = "'who': {'kind': 'player', 'players': ['Joel Embiid']}", "'lines': [{'measure': 'points', 'value': 30, 'as_typed': '30 points', 'keyed': True}]"
    filters = f"{{{who}, {lines}, 'measure': {{'key': 'points', 'as_typed': 'points'}}}}"
    assert reading_lines == [f"{expected} filters={filters}"]

    # Declining refuses with the compiler's own reason.
    calls.clear()

    def declining(*a: Any, declined: Any = None, **k: Any) -> None:
        calls.append("compose")
        declined("a test double's reason for having no reading")

    monkeypatch.setattr("association.query.compose.answer", declining)
    answer = ask_routed(agent, "how many 30 point games did embiid have?", recorded)
    assert answer.answered_by == "refused" and "a test double's reason" in answer.text
    assert calls == ["compose"]
    assert (agent.unanswered, agent.unanswered_as) == ("a test double's reason for having no reading", GAMES_COUNTED)


def test_the_parser_reads_the_question(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The parser reads the question: the model only copies the names and
    picks a stat, and what the words ask comes from the words. The model's
    span is the question's own word, and the name the answer side gets is
    the name index's reading of it - the subject reading, not the model,
    writes "Joel Embiid"."""
    import duckdb

    from association.query.answer import Answer
    from association.query.normalizer import Normalized

    seen: dict[str, Any] = {}

    def record(ctx: Any, reading: Reading) -> Answer:
        seen.update({"player": reading.who.player, "stat": reading.measure.as_typed if reading.measure is not None else None})
        return Answer(data={}, text="templated")

    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: Normalized(["embiid"], "points"))
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None, declined_by=None: record(ctx, reading))
    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1', 'Joel Embiid')")
    con.close()
    agent = Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history")
    answer = agent.ask("how many points does embiid average")
    assert answer.text == "templated" and agent.reading is not None and agent.reading.asked == PLAYER_LINE
    assert (seen["player"], seen["stat"]) == ("Joel Embiid", "points")


def test_the_key_the_parsers_words_assign_reaches_the_answers_decisions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """#258: "how far away does embiid shoot from" is read as a player's line
    by the grammar and settled as a shot distance by its own words. The move
    is a decision on the answer, as a value, and in the history record."""
    import duckdb

    from association.query.answer import Answer
    from association.query.normalizer import Normalized

    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: Normalized(["embiid"], ""))
    # A shot distance is the shot relation's reader's (compose.shots): its answer stands in.
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, **_: Answer(data={}, text="templated"))
    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1', 'Joel Embiid')")
    con.close()
    agent = Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history")
    answer = agent.ask("how far away does embiid shoot from?")
    assert answer.text == "templated" and agent.reading is not None and agent.reading.asked == SHOT_DISTANCE
    moved = [d for d in answer.decisions if (d.stage, d.field) == ("parser", "asked")]
    assert [(d.before, d.after, d.reason) for d in moved] == [("player_seasons/scalar/line", "shots/scalar/distance", "the words 'how far' name shots/scalar/distance")]
    record = next(iter((tmp_path / ".history").glob("*.log"))).read_text()
    assert "  -> (decision) parser asked: 'player_seasons/scalar/line' -> 'shots/scalar/distance' (the words 'how far' name shots/scalar/distance)" in record


def test_a_refusal_names_the_planners_own_reason(tmp_path: Path) -> None:
    """The reason a reading is refused is the planner's own cause - here the
    ``rate`` cell, which the relation cannot honor, with its unit - and not
    a list of cells consulted after it declined."""

    agent = _agent_with_players(tmp_path, "Joel Embiid")
    answer = ask_routed(
        agent,
        "how many 30 point games has embiid had per 36",
        Routed(
            GAMES_COUNTED,
            Reading(
                who=Who(kind="player", players=("Joel Embiid",)),
                lines=(Line(measure="points", value=30, as_typed="30 points", keyed=True),),
                measure=Measure(key="points", as_typed="points", unit="per_36"),
            ),
        ),
    )
    # A cause the planner says, answered as a refusal that names why.
    assert answer.answered_by == "fast" and agent.unanswered is None
    assert answer.text == "Nothing here answers this question as a count of games over a line: a rate per 36 cannot be read here - it would answer for a different span than was asked."
    assert answer.data is not None and answer.data["reason"] == "beyond_words" and answer.data["cells"] == [{"cell": "rate", "unit": "per_36"}]


def test_a_window_the_reading_cannot_hold_is_refused_rather_than_crashing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ "stephen curry last 0 games" reads as a window of 0, which the typed
    window refuses, and the question is refused - never an uncaught error
    out of ``Agent.ask``. A reading a test builds by hand with such a window fails
    at the typed value itself (``reading.Window``), before the answering
    loop sees it."""
    from association.query.normalizer import Normalized
    from association.query.reading import ScopeError

    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: Normalized(["stephen curry"], ""))
    agent = _agent_with_players(tmp_path, "Stephen Curry")
    answer = agent.ask("stephen curry last 0 games")
    assert answer.answered_by == "refused" and "limit" in answer.text
    assert agent.unanswered is not None and "limit" in agent.unanswered
    with pytest.raises(ScopeError, match="limit"):
        Window(count=0)


def test_a_short_question_is_refused_before_the_model_is_asked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A question of fewer than three words ("Tatum rec") is refused with
    the generic sentence and costs no model call
    (the parser's first verdict, parse.too_short, said by the planner)."""

    def never(model: str, question: str) -> None:
        raise AssertionError("the normalizer must not be asked a two-word question")

    monkeypatch.setattr("association.query.normalizer.normalize", never)
    agent = _agent_with_players(tmp_path, "Jayson Tatum")
    answer = agent.ask("Tatum rec")
    assert answer.answered_by == "refused" and answer.timing.model_calls == 0
    assert answer.text == "I couldn't understand your question, 'Tatum rec'. Please try re-phrasing it."
    assert agent.unanswered == "fewer than 3 words"


def test_a_question_is_answered_in_the_latest_season_on_record_not_the_calendars(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """From October 1 the calendar's season is one the warehouse has no
    games for. ``Agent.ask`` reads the latest season
    with a played regular-season or postseason game (a preseason-only or
    unplayed season does not count) and answers inside it; the trace says
    so; and once the question is answered the calendar's season stands
    again."""
    import duckdb

    from association.nba.season import TODAY_ENV, current_season
    from association.query.connection import latest_season_on_record

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE games (event_id VARCHAR, season INTEGER, season_type INTEGER, winner_team_id VARCHAR)")
    # 2026 played; 2027 holds a preseason game and an unplayed regular-season one.
    con.execute("INSERT INTO games VALUES ('a', 2025, 2, '1'), ('b', 2026, 2, '1'), ('c', 2026, 3, '2'), ('d', 2027, 1, '1'), ('e', 2027, 2, NULL)")
    assert latest_season_on_record(con) == 2026
    con.close()
    assert latest_season_on_record(duckdb.connect(":memory:")) is None

    monkeypatch.setenv(TODAY_ENV, "2026-10-05")
    lines: list[str] = []
    agent = Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history", trace=lines.append, verbose=True)
    seen: list[int] = []

    def inner(question: str, history: Any, route: Any = None) -> Any:
        seen.append(current_season())
        from association.query.answer import Answer, Timing

        return Answer(question=question, text="ok", answered_by="refused", timing=Timing(total_seconds=0.0, model_seconds=0.0, model_calls=0, tool_seconds=0.0, tool_calls=0))

    monkeypatch.setattr(agent, "_ask_inner", inner)
    agent.ask("how many points does luka average")
    assert seen == [2026], "the question's default season is the latest with games, not the calendar's 2027"
    assert current_season() == 2027, "outside the question the calendar's season stands"
    assert any("default season: 2026 (the calendar's 2027 has no games on record)" in line for line in lines)


def test_the_point_readers_decline_by_a_cause_waits_for_what_the_words_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The point reader's declines are causes, said at a decline's place -
    so what the words name that nothing reads still speaks first ("2013 nba
    finals" is refused by its playoff round, not by the log's missing
    player)."""
    from association.query.normalizer import Normalized
    from association.query.reading import Cause

    reading = Reading(
        asked=PLAYER_LOG,
        point_refusal=Cause(kind="no_subject_reading", facts={"shape": PLAYER_LOG}),
        unsupported=(Cause(kind="playoff_round", facts={"shape": PLAYER_LOG, "round": "finals"}),),
    )
    monkeypatch.setattr("association.query.parse.read_route", lambda *args, **kwargs: reading)
    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: Normalized([], ""))
    answer = _agent_with_players(tmp_path).ask("2013 nba finals game log")
    assert answer.answered_by == "fast" and answer.text.startswith("The games are not labeled by playoff round, so 'finals' cannot pick them out yet.")
