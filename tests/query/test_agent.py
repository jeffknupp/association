"""Regression tests for the answering loop: the fast path's answers, and the
refusal naming why where nothing here reads a question (the tool-calling
fall-through it replaced in 5.0.0 is gone)."""

from pathlib import Path
from typing import Any, NoReturn

import ollama
import pytest
from routed import ask_routed, slots_route

from association.query.agent import Agent
from association.query.reading import Reading


def test_ask_writes_history_file_even_without_verbose(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The whole point of RunHistory: a run's full evidence (command, trace,
    timing, answer) must land on disk regardless of whether --verbose was
    passed - not verbose here, and nothing on stderr, but the history file
    must still exist and hold everything."""
    from association.query.normalizer import Normalized
    from association.query.templates.common import TemplateResult

    history_dir = tmp_path / ".history"
    agent = _agent_with_players(tmp_path, "Joel Embiid")
    agent.history_dir = history_dir
    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: Normalized(["embiid"], "points"))
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None: TemplateResult(data={}, answer="Final answer."))
    result = agent.ask("how many points does embiid average").text

    assert result == "Final answer."
    files = list(history_dir.glob("*.log"))
    assert len(files) == 1
    content = files[0].read_text()
    assert "question: how many points does embiid average" in content
    assert "model inference #1" in content
    assert "router: qwen2.5:3b" in content
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
    # answer, where the older repairs only asked once a slot gave them a name.
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
    # An empty teams table too: the compiler asks whether a name in the
    # player slot is a team's before it resolves it as a player's.
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    for i, name in enumerate(names):
        con.execute("INSERT INTO players VALUES (?, ?)", [str(i), name])
    con.close()
    return Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history")


def test_the_fast_path_replaces_a_player_the_question_never_named(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Measured live: "compare sga and embiid" routed to Jusuf Nurkic in the
    second slot and answered with a confident table about him. The template is
    not reached until the names are the question's."""
    from association.query.templates.common import TemplateResult

    seen: list[str] = []

    def record(ctx: Any, reading: Reading) -> TemplateResult:
        seen.extend(reading.scope.players)
        return TemplateResult(data={}, answer="templated")

    # player_compare is the compiler's (compose.COMPILED_INTENTS): the same Reading reaches compose.answer.
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None: record(ctx, reading))
    ask_routed(_agent_with_players(tmp_path, "Joel Embiid", "Jusuf Nurkic"), "compare sga and embiid", slots_route("player_compare", {"players": ["Shai Gilgeous-Alexander", "Jusuf Nurkic"]}))
    assert seen == ["Shai Gilgeous-Alexander", "Joel Embiid"]


def test_the_fast_path_records_who_the_question_was_read_to_be_about(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The subject reading (query/subject.py) runs on every fast-path answer
    and is recorded as decisions - values on the Answer and a `decisions:`
    section of the history record - beside the repair chain that still
    writes the slots. It writes none itself yet."""
    from association.query.templates.common import TemplateResult

    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None: TemplateResult(data={}, answer="templated"))
    agent = _agent_with_players(tmp_path, "Joel Embiid", "Jusuf Nurkic")
    answer = ask_routed(agent, "compare sga and embiid", slots_route("player_compare", {"players": ["Shai Gilgeous-Alexander", "Jusuf Nurkic"]}))
    stages = [(d.stage, d.field, d.after) for d in answer.decisions]
    assert ("subject", "kind", "pair") in stages
    assert ("subject", "players", ["Shai Gilgeous-Alexander", "Joel Embiid"]) in stages  # in the question's own order; Nurkic, whom it never names, is not there
    record = next(iter((tmp_path / ".history").glob("*.log"))).read_text()
    assert "decisions:\n" in record and '"stage": "subject"' in record and "  -> (decision) subject kind: 'pair'" in record


def test_a_team_only_intent_naming_one_player_refuses_rather_than_answering_the_league(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """yardstick-v2 F111: "alperen şengün alltime record" routed to
    team_leaderboard - no player slot, no team slot - and answered the
    league standings, Sengun never read. The template is not reached at
    all: team_leaderboard has no reading for a named player, so the
    question is refused, naming him, rather than answered about the wrong
    subject. Diacritics ("şengün") are already folded before this runs."""

    reached = False

    def record(ctx: Any, reading: Reading, **_: Any) -> Any:
        nonlocal reached
        reached = True
        raise AssertionError("team_leaderboard should not run at all")

    # The compiler answers team_leaderboard (Phase 2, step 4): it is what must not be reached.
    monkeypatch.setattr("association.query.compose.answer", record)
    answer = ask_routed(_agent_with_players(tmp_path, "Alperen Sengun"), "alperen şengün alltime record", slots_route("team_leaderboard", {"stat": "record", "limit": 1}))
    assert not reached
    assert "Alperen Sengun" in answer.text
    assert "team leaderboard" in answer.text


def test_a_team_only_intent_with_a_team_named_is_unaffected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A genuine team question - a `team` slot resolving to a REAL
    franchise - is answered normally (by the compiler, since Phase 2, step 4), whatever player-shaped words
    happen to also appear. A `teams` table is needed here (unlike
    `_agent_with_players`'s plain one): `_has_a_real_team` looks the team
    slot up against it, and a warehouse missing the table entirely reads as
    "no team found" the same way `entities._team_named` already does."""
    import duckdb

    from association.query.templates.common import TemplateResult

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1', 'Alperen Sengun')")
    con.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('2', 'Houston Rockets', 'HOU')")
    con.close()
    agent = Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history")

    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, **_: TemplateResult(data={}, answer="templated"))
    answer = ask_routed(agent, "alperen şengün rockets record", slots_route("team_leaderboard", {"stat": "record", "team": "Houston Rockets"}))
    assert answer.text == "templated"


def test_a_model_that_could_not_be_asked_is_refused_saying_why(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A reader failure is a refusal, never an answer. What it must not do is
    report it as the model having said something unusable: reported from a
    laptop without the router model pulled, where every question came back
    "the router returned no usable classification" and nothing pointed at
    ollama. The normalizer raises the same error the router did."""
    from association.query.history import RunHistory
    from association.query.router import RouterUnavailable

    agent = _agent(tmp_path)

    def unavailable(*a: Any, **k: Any) -> None:
        raise RouterUnavailable("ollama could not serve the model 'qwen2.5:3b': not found")

    monkeypatch.setattr("association.query.normalizer.normalize", unavailable)
    assert agent._try_fast_path("who leads the league in assists?", RunHistory(False, tmp_path / ".history")) is None
    assert agent.unanswered is not None
    assert "model 'qwen2.5:3b'" in agent.unanswered
    assert "no usable reply" not in agent.unanswered
    answer = agent.ask("who leads the league in assists?")
    assert answer.answered_by == "refused" and "model 'qwen2.5:3b'" in answer.text


def test_a_rerouted_intent_runs_the_path_it_was_rerouted_to(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A team's record in the games a companion reached a line arrives under
    the team's own intent and is rewritten to record_when - the reading's
    one reroute (subject._decide_intent). This pins that the ANSWERING PATH
    moves with the intent, the half that shipped broken once: the handler
    was resolved from the route's intent before the rewrite, so "Embiid
    career record vs boston" logged `head_to_head -> with_without` and then
    ran head_to_head, which refused for wanting two team names. Every
    offline replay passed, because the replay script looked the handler up
    afterwards and the real pipeline before - so only a test on this path
    can catch it. record_when is the compiler's alone now
    (compose.COMPILED_INTENTS): the compiler is asked, under record_when,
    and team_record's template never runs."""
    from association.query.templates.common import TemplateResult, TemplateUnsupported

    paths: list[str] = []

    def team_record(ctx: Any, reading: Reading) -> TemplateResult:
        paths.append("team_record")
        raise TemplateUnsupported("team_record cannot read a player's line")

    def composed(ctx: Any, reading: Reading, trace: Any = None, declined: Any = None, planned: Any = None, ran: Any = None) -> TemplateResult:
        del ran  # the agent's own callback, not this test's record of the paths taken
        paths.append(f"compose {reading.intent}")
        return TemplateResult(data={}, answer="composed")

    import duckdb

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1', 'Tyrese Maxey')")
    con.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('20', 'Philadelphia 76ers', 'PHI')")
    con.close()
    agent = Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history")

    monkeypatch.setattr("association.query.agent.TEMPLATES", {"team_record": team_record})
    monkeypatch.setattr("association.query.compose.answer", composed)
    answer = ask_routed(agent, "sixers record when maxey scored 20+ points", slots_route("team_record", {"team": "Philadelphia 76ers", "stat": "points", "season_type": 2}))
    assert paths == ["compose record_when"]
    assert answer.intent == "record_when" and "composed" in (answer.text or "")


def test_a_player_the_question_cannot_account_for_is_refused_not_passed_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Passing it on was measured and is worse: given one of these the agent
    of the time spent 55 seconds writing a confident fingerprint, percentages
    included, for "Ronaldo Lopes" - who does not exist. Same reasoning as
    check_coverage returning its refusal rather than raising it: the refusal
    is a fast answer naming the player, not the plain refusal for want of a
    reading."""
    from association.query.templates.common import TemplateResult

    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None: TemplateResult(data={}, answer="templated"))
    answer = ask_routed(_agent_with_players(tmp_path, "Joel Embiid", "Jusuf Nurkic"), "compare the two best centers", slots_route("player_compare", {"players": ["Jusuf Nurkic", "Joel Embiid"]}))
    assert "was not answered" in answer.text and "Jusuf Nurkic" in answer.text
    assert answer.answered_by == "fast" and "templated" not in answer.text


def test_a_stray_name_on_a_question_no_template_reads_one_for_changes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """head_to_head never looks at a player slot, so an invented one there
    cannot make the answer about the wrong person - and refusing over it would
    break a question that works."""
    from association.query.templates.common import TemplateResult

    monkeypatch.setattr("association.query.agent.TEMPLATES", {"head_to_head": lambda ctx, slots: TemplateResult(data={}, answer="templated")})
    assert ask_routed(_agent_with_players(tmp_path, "Joel Embiid", "Jusuf Nurkic"), "Lakers vs Celtics record", slots_route("head_to_head", {"player": "Jusuf Nurkic"})).text == "templated"


def test_a_fingerprint_that_lost_a_player_to_a_typo_says_so(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ "generate fingerprints for embiid vs jolic in 2026" drew Joel Embiid
    alone. The typo cannot be repaired, so the half-answer has to be stated -
    one polygon where two were asked for, with nothing saying so, is the
    failure shape this project keeps producing."""
    from association.query.templates.common import TemplateResult

    monkeypatch.setattr("association.query.agent.TEMPLATES", {"fingerprint": lambda ctx, slots: TemplateResult(data={}, answer="Rendered.")})
    answer = ask_routed(_agent_with_players(tmp_path, "Joel Embiid"), "generate fingerprints for embiid vs jolic in 2026", slots_route("fingerprint", {"player": "Joel Embiid"})).text
    assert answer.startswith("Rendered.") and "only one of them matches" in answer


def test_the_fast_path_says_how_it_read_a_name_the_question_left_open(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The reading reaches the ANSWER, which is the whole condition the default
    is allowed under: "maxey" is Tyrese because he is the only Maxey who still
    plays, and somebody who meant Marlon has to be told what to type instead.
    Templates are called with a bare connection in 27 places, so the sentence
    travels by entities.collect_name_readings and is attached here - removing
    that block leaves a right answer about a player nobody said was chosen."""
    from association.query.entities import Entity, _note_name_reading
    from association.query.templates.common import TemplateResult

    def reads_a_name(ctx: Any, reading: Reading) -> TemplateResult:
        _note_name_reading("maxey", Entity("1", "Tyrese Maxey"), [Entity("0", "Marlon Maxey")], 2026, named_in_full=False)
        return TemplateResult(data={}, answer="Tyrese Maxey averaged 28.0 points.")

    # player_stat is the compiler's (compose.COMPILED_INTENTS): the reading
    # travels the same way through agent._try_compose.
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None: reads_a_name(ctx, reading))
    answer = ask_routed(_agent_with_players(tmp_path, "Marlon Maxey", "Tyrese Maxey"), "how many points does maxey average?", slots_route("player_stat", {"player": "maxey"}))
    reading = "('maxey' was read as Tyrese Maxey, the only match who played in 2025-26. Marlon Maxey also matches - use the full name, or name a season he played, to ask about him.)"
    assert answer.text == f"Tyrese Maxey averaged 28.0 points. {reading}"
    # The reading rides in `notes` too, for the web page to show beneath a
    # rendered table (agent._note).
    assert answer.data == {"name_readings": [reading], "notes": [reading]}


def test_a_question_nothing_reads_is_refused_naming_why(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Where no template or compiled reading answers, the answer is a refusal
    that names why - what --disable-fallthrough used to raise, and the only
    answer since the SQL-writing agent went (5.0.0). No model is asked past
    the reader."""
    from association.query.agent import refusal_text
    from association.query.templates import TemplateResult, TemplateUnsupported

    def chat_must_not_run(**kw: Any) -> None:
        raise AssertionError("no model is asked past the normalizer")

    monkeypatch.setattr(ollama, "chat", chat_must_not_run)
    agent = _agent_with_players(tmp_path)

    answer = ask_routed(agent, "who had the most triple-doubles?", slots_route("other", {}))
    assert answer.answered_by == "refused"
    assert answer.text == refusal_text("intent 'other' has no template yet")
    assert answer.intent is None and answer.data is None
    assert agent.unanswered == "intent 'other' has no template yet"

    # A template's own refusal (head_to_head still has one; with_without,
    # which this used, is the compiler's since step (g)).
    def refusing(ctx: Any, reading: Reading) -> TemplateResult:
        raise TemplateUnsupported("head_to_head needs two teams, got []")

    monkeypatch.setattr("association.query.agent.TEMPLATES", {"head_to_head": refusing})
    answer = ask_routed(agent, "a question nothing reads", slots_route("head_to_head", {"team": "Philadelphia 76ers"}))
    assert answer.answered_by == "refused" and "head_to_head: head_to_head needs two teams" in answer.text
    assert agent.unanswered == "head_to_head: head_to_head needs two teams, got []"

    # An intent the compiler alone answers (compose.COMPILED_INTENTS) is
    # refused with the compiler's reason where it has no reading: a log of
    # nobody.
    answer = ask_routed(agent, "a game log", slots_route("game_log", {}))
    assert answer.answered_by == "refused" and "game_log: no player subject" in answer.text
    assert agent.unanswered is not None and agent.unanswered.startswith("game_log: ")
    # And says a cause where the reading came to one, as an answer: a count
    # with no line to count (Phase 2, step 3).
    answer = ask_routed(agent, "how many games", slots_route("threshold_count", {"stat": "points"}))
    assert answer.answered_by == "fast" and answer.text.startswith("A count of games across the league needs the line it counts")
    assert agent.unanswered is None

    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: None)
    answer = agent.ask("what is this question")
    assert answer.answered_by == "refused" and "no usable reply" in answer.text

    # A question a template answers is unaffected.
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"head_to_head": lambda ctx, slots: TemplateResult(data={}, answer="answered")})
    answer = ask_routed(agent, "a question nothing reads", slots_route("head_to_head", {}))
    assert (answer.text, answer.answered_by, agent.unanswered) == ("answered", "fast", None)


# ---------------- the compiled step between a refusal and the refusal naming why ----------------


def _refusing_template(ctx: Any, reading: Reading) -> NoReturn:
    """A template stand-in that always raises TemplateUnsupported, the way
    check_scope or a template's own validation does - the trigger that
    reaches association.query.compose.answer (agent._try_compose)."""
    from association.query.templates.common import TemplateUnsupported

    raise TemplateUnsupported("a test double's refusal, standing in for whatever check_scope or a real template would have raised")


def test_a_templates_refusal_that_compose_answers_is_returned_as_fast_with_the_trace_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The whole point of wiring the compiler in: a scoping refusal that used
    to cost a fall-through is answered here instead, exactly
    like a template's own result - answered_by="fast", the intent kept - with
    a trace line naming the point on the relation it composed, the way
    "-> (router) intent=..." names what was routed."""
    from association.query.templates.common import TemplateResult

    def composed_answer(ctx: Any, reading: Reading, trace: Any = None, declined: Any = None, planned: Any = None, ran: Any = None) -> TemplateResult:
        return TemplateResult(data={"skeleton": "aggregate", "measures": ["points"], "rows": [{"points": 30.0}]}, answer="Joel Embiid has averaged 30.0 points since 2024.")

    monkeypatch.setattr("association.query.agent.TEMPLATES", {"player_stat": _refusing_template})
    monkeypatch.setattr("association.query.compose.answer", composed_answer)

    seen: list[str] = []
    agent = _agent_with_players(tmp_path, "Joel Embiid")
    agent.trace = seen.append
    agent.verbose = True
    answer = ask_routed(agent, "how many points has embiid averaged since 2024?", slots_route("player_stat", {"player": "Joel Embiid", "since": 2024}))

    assert answer.text == "Joel Embiid has averaged 30.0 points since 2024."
    assert answer.answered_by == "fast"
    assert answer.intent == "player_stat"
    assert answer.data == {"skeleton": "aggregate", "measures": ["points"], "rows": [{"points": 30.0}]}
    compose_lines = [line for line in seen if "(compose)" in line]
    assert len(compose_lines) == 1
    assert "intent='player_stat'" in compose_lines[0]
    assert "'skeleton': 'aggregate'" in compose_lines[0] and "'measures': ['points']" in compose_lines[0]
    # The point is what was composed, not the rows it produced.
    assert "rows" not in compose_lines[0]


def test_a_compose_none_is_refused_with_the_templates_reason(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No monkeypatch on compose.answer here: this exercises the real
    compiler (association.query.compose) on an intent that is not a point on
    any relation - a fingerprint is a chart over NetPoints - so it declines
    with None, and the question is refused with the template's own reason."""

    monkeypatch.setattr("association.query.agent.TEMPLATES", {"fingerprint": _refusing_template})

    answer = ask_routed(_agent_with_players(tmp_path, "Joel Embiid"), "show me embiid's fingerprint for 2026", slots_route("fingerprint", {"player": "Joel Embiid", "season": 2026}))

    assert answer.answered_by == "refused"
    assert "fingerprint: a test double's refusal" in answer.text


def test_a_compose_refusal_is_returned_as_the_answer_not_a_fall_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A clarification or a no-match from the compiler is an answer, not a
    refusal for want of a reading: it looked at the question and had
    something to say."""
    from association.query.templates.common import TemplateResult

    def composed_refusal(ctx: Any, reading: Reading, trace: Any = None, declined: Any = None, planned: Any = None, ran: Any = None) -> TemplateResult:
        return TemplateResult(data={"ambiguous": "since"}, answer="I can't tell which span 'the last few' means - a number of games, or a number of seasons?")

    monkeypatch.setattr("association.query.agent.TEMPLATES", {"player_stat": _refusing_template})
    monkeypatch.setattr("association.query.compose.answer", composed_refusal)

    answer = ask_routed(_agent_with_players(tmp_path, "Joel Embiid"), "embiid's line over the last few?", slots_route("player_stat", {"player": "Joel Embiid", "since": 2024}))

    assert answer.text == "I can't tell which span 'the last few' means - a number of games, or a number of seasons?"
    assert answer.answered_by == "fast"


def test_a_composed_answer_carries_the_name_reading_it_noted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The compiler reads a name exactly the way a template does - through
    entities.collect_name_readings - so a default it chose ("maxey" is Tyrese)
    is visible in the answer here too, not only on the direct template path."""
    from association.query.entities import Entity, _note_name_reading
    from association.query.templates.common import TemplateResult

    def composed_with_reading(ctx: Any, reading: Reading, trace: Any = None, declined: Any = None, planned: Any = None, ran: Any = None) -> TemplateResult:
        _note_name_reading("maxey", Entity("1", "Tyrese Maxey"), [Entity("0", "Marlon Maxey")], 2026, named_in_full=False)
        return TemplateResult(data={}, answer="Tyrese Maxey has averaged 28.0 points since 2024.")

    monkeypatch.setattr("association.query.agent.TEMPLATES", {"player_stat": _refusing_template})
    monkeypatch.setattr("association.query.compose.answer", composed_with_reading)

    answer = ask_routed(_agent_with_players(tmp_path, "Marlon Maxey", "Tyrese Maxey"), "how many points has maxey averaged since 2024?", slots_route("player_stat", {"player": "maxey", "since": 2024}))

    reading = "('maxey' was read as Tyrese Maxey, the only match who played in 2025-26. Marlon Maxey also matches - use the full name, or name a season he played, to ask about him.)"
    assert answer.text == f"Tyrese Maxey has averaged 28.0 points since 2024. {reading}"
    assert answer.data == {"name_readings": [reading], "notes": [reading]}


def test_an_unported_intent_is_refused_by_name(tmp_path: Path) -> None:
    """A shape with no template is refused naming the intent, in seconds."""

    answer = ask_routed(_agent(tmp_path), "who had the most triple-doubles?", slots_route("other", {}))
    assert answer.answered_by == "refused" and "intent 'other' has no template yet" in answer.text


def test_a_reader_failure_is_refused_rather_than_erroring(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: None)
    answer = _agent(tmp_path).ask("what is this question")
    assert answer.answered_by == "refused" and "the normalizer returned no usable reply" in answer.text


def test_the_fast_path_carries_out_the_intent_and_data_it_used_to_discard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """TemplateResult.data existed so a caller could render the answer itself,
    and _try_fast_path returned only result.answer, so nothing ever could.
    That is the whole reason Phase 0 of the 2.0 plan exists."""
    from association.query.templates.common import TemplateResult

    # leaderboard is the compiler's (compose.COMPILED_INTENTS): the same Reading reaches compose.answer.
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None: TemplateResult(data={"leaders": ["Jokic"]}, answer="Jokic."))
    answer = ask_routed(_agent_with_players(tmp_path), "who leads the league in scoring?", slots_route("leaderboard", {"stat": "points"}))

    assert answer.text == "Jokic."
    assert answer.answered_by == "fast"
    assert answer.intent == "leaderboard"
    assert answer.data == {"leaders": ["Jokic"]}
    assert answer.question == "who leads the league in scoring?"


def test_a_refused_answer_has_no_intent_or_data_rather_than_an_empty_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Only a template or the compiler produces those. None says so; {} would
    read as "the template ran and found nothing", which is a different claim."""
    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: None)
    answer = _agent(tmp_path).ask("what is this question")

    assert answer.answered_by == "refused"
    assert answer.intent is None
    assert answer.data is None


def test_a_fast_path_answer_carries_the_chart_the_template_wrote(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from association.query.answer import Artifact
    from association.query.templates.common import TemplateResult

    drawn = Artifact("shot_chart", tmp_path / "shotchart_x.html")
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"shot_chart": lambda con, slots: TemplateResult(data={}, answer="Rendered.", artifacts=[drawn])})
    assert ask_routed(_agent(tmp_path), "a chart of x", slots_route("shot_chart", {"player": "x"})).artifacts == [drawn]


def test_the_history_label_comes_from_the_caller_not_the_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`shlex.join(sys.argv)` describes a CLI invocation and nothing else - for
    a server handling many questions it is the same wrong string every time."""
    import duckdb

    db_path = tmp_path / "test.duckdb"
    duckdb.connect(str(db_path)).close()
    history_dir = tmp_path / ".history"

    agent = _agent_with_players(tmp_path)
    agent.history_dir = history_dir
    ask_routed(agent, "a question nothing reads", slots_route("other", {}), label="POST /api/ask")

    assert "command: POST /api/ask" in next(iter(history_dir.glob("*.log"))).read_text()


def test_a_trace_sink_takes_the_place_of_stderr_entirely(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Including the [history] line, which printed unconditionally - a server
    that captured the trace but still wrote that one to its own terminal would
    have gone half-way."""
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
    """ "Gave up after too many tool-call iterations" told a reader nothing
    about their own question, while the agent was still there to give up.
    The reason the templates declined it says which part of the question has
    no answer here yet, and the refusal is that reason and nothing else."""
    from association.query.agent import refusal_text

    agent = _agent_with_players(tmp_path)
    answer = ask_routed(agent, "who had the most triple-doubles?", slots_route("other", {}))
    assert answer.text == refusal_text("intent 'other' has no template yet") == "Nothing here answers this question: intent 'other' has no template yet."


def test_a_shape_nothing_reads_is_refused_with_its_cause(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """After the template refuses and the compiler declines, a shape the
    warehouse has no column for (association.query.refusals) is refused
    with its cause as a fast answer - not with the template's slot. A
    playoff round is the worked case: no template honors the slot, and the
    games carry no round label."""

    monkeypatch.setattr("association.query.agent.TEMPLATES", {"game_log": _refusing_template})
    monkeypatch.setattr("association.query.compose.answer", lambda *a, **k: None)

    agent = _agent_with_players(tmp_path, "Joel Embiid")
    answer = ask_routed(agent, "nba finals game log 2025", slots_route("game_log", {"team": "NBA Finals", "season": 2025, "season_type": 3, "round": "finals"}))

    assert "not labeled by playoff round" in answer.text
    assert answer.answered_by == "fast"
    assert agent.unanswered is None


def test_a_shape_nothing_reads_is_refused_even_where_no_template_exists(tmp_path: Path) -> None:
    """An intent with no template is refused by name; the refusals module
    gets its look first, so "bench points" (routed `other`) is refused with
    its cause rather than with the intent."""

    agent = _agent_with_players(tmp_path, "Joel Embiid")
    answer = ask_routed(agent, "most opponent bench points allowed in the west at home by team this month", slots_route("other", {"stat": "points", "venue": "home"}))

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
    answer = ask_routed(agent, "a question nothing reads", slots_route("other", {}))

    written = [p.name for p in history_dir.glob("*.log")]
    assert answer.history_file is not None and answer.history_file in written
    assert "/" not in answer.history_file


def test_a_compiled_intent_is_read_planned_and_answered_by_the_compiler_alone(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ROADMAP plan item 6, step (d), part 4: the four intents the compiler
    reproduced exactly (compose.COMPILED_INTENTS) are answered from the
    Reading; the trace carries the record ("-> (reading) ...") and no
    template is called, even one registered under the intent. Where the
    compiler declines, the question is refused naming the compiler's
    reason - there is no template behind it any more."""
    from association.query.reading import Scope
    from association.query.templates.common import TemplateResult

    calls: list[str] = []

    def composed_first(ctx: Any, reading: Reading, trace: Any = None, declined: Any = None, planned: Any = None, ran: Any = None) -> TemplateResult:
        calls.append("compose")
        if trace is not None:
            trace(
                Reading(
                    scope=Scope.from_slots({"player": "Joel Embiid", "threshold": 30, "stat": "points"}),
                    shape="scalar",
                    measures=[],
                    aggregate="count",
                    group="none",
                    predicates=[("points", ">=", 30)],
                    intent=reading.intent,
                )
            )
        return TemplateResult(data={"count": 9}, answer="Joel Embiid had 9 games with 30+ points.")

    def never_template(ctx: Any, reading: Reading) -> TemplateResult:
        calls.append("template")
        return TemplateResult(data={}, answer="the template answered")

    recorded = slots_route("threshold_count", {"player": "Joel Embiid", "stat": "points", "threshold": 30})
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"threshold_count": never_template})
    monkeypatch.setattr("association.query.compose.answer", composed_first)

    seen: list[str] = []
    agent = _agent_with_players(tmp_path, "Joel Embiid")
    agent.trace = seen.append
    agent.verbose = True
    answer = ask_routed(agent, "how many 30 point games did embiid have?", recorded)

    assert answer.text == "Joel Embiid had 9 games with 30+ points." and answer.answered_by == "fast" and answer.intent == "threshold_count"
    assert calls == ["compose"]
    reading_lines = [line for line in seen if "(reading)" in line]
    expected = "  -> (reading) relation=player subject=? shape=scalar measures=[] aggregate=count group=none predicates=[('points', '>=', 30)] window=date/desc source=games"
    # The scope prints in the Scope's field order, whatever order the slots came in.
    assert reading_lines == [f"{expected} scope={{'player': 'Joel Embiid', 'stat': 'points', 'threshold': 30}}"]

    # Declining refuses with the compiler's own reason.
    calls.clear()

    def declining(*a: Any, declined: Any = None, **k: Any) -> None:
        calls.append("compose")
        declined("a test double's reason for having no reading")

    monkeypatch.setattr("association.query.compose.answer", declining)
    answer = ask_routed(agent, "how many 30 point games did embiid have?", recorded)
    assert answer.answered_by == "refused" and "a test double's reason" in answer.text
    assert calls == ["compose"]
    assert agent.unanswered == "threshold_count: a test double's reason for having no reading"


def test_the_parser_reads_the_question(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The parser (ROADMAP plan item 6, step c): the model only copies the
    names and picks a stat, and the route comes from the words. The model's
    span is the question's own word, and the name the template gets is the
    index's reading of it - the subject reading, not the model, writes "Joel
    Embiid"."""
    import duckdb

    from association.query.normalizer import Normalized
    from association.query.templates.common import TemplateResult

    seen: dict[str, Any] = {}

    def record(ctx: Any, reading: Reading) -> TemplateResult:
        seen.update(reading.scope.to_slots())
        return TemplateResult(data={}, answer="templated")

    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: Normalized(["embiid"], "points"))
    monkeypatch.setattr("association.query.compose.answer", lambda ctx, reading, trace=None, declined=None, planned=None: record(ctx, reading))
    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1', 'Joel Embiid')")
    con.close()
    agent = Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history")
    answer = agent.ask("how many points does embiid average")
    assert (answer.text, answer.intent) == ("templated", "player_stat")
    assert (seen["player"], seen["stat"]) == ("Joel Embiid", "points")


def test_the_intent_the_parsers_words_assign_reaches_the_answers_decisions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ISSUES.md #258: "how far away does embiid shoot from" is read under
    ``player_stat`` by the grammar and settled as ``shot_distance`` by its
    own words - which only the verbose trace used to say. The move is a
    decision on the answer, as a value, and in the history record."""
    import duckdb

    from association.query.normalizer import Normalized
    from association.query.templates.common import TemplateResult

    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: Normalized(["embiid"], ""))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"shot_distance": lambda ctx, reading: TemplateResult(data={}, answer="templated")})
    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, abbreviation VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1', 'Joel Embiid')")
    con.close()
    answer = Agent(str(db_path), tmp_path / "out", history_dir=tmp_path / ".history").ask("how far away does embiid shoot from?")
    assert (answer.text, answer.intent) == ("templated", "shot_distance")
    moved = [d for d in answer.decisions if (d.stage, d.field) == ("parser", "intent")]
    assert [(d.before, d.after, d.reason) for d in moved] == [("player_stat", "shot_distance", "the words 'how far' name shot_distance")]
    record = next(iter((tmp_path / ".history").glob("*.log"))).read_text()
    assert "  -> (decision) parser intent: 'player_stat' -> 'shot_distance' (the words 'how far' name shot_distance)" in record


def test_a_compiled_intents_refusal_names_the_compilers_own_reason(tmp_path: Path) -> None:
    """ROADMAP plan item 6, step (f): the reason a compiled intent is refused
    is the planner's, read at parse time - "the relation cannot honor
    ['rate']" - and not a template's list consulted afterwards, which named
    a slot even where the compiler had declined for another cause."""

    agent = _agent_with_players(tmp_path, "Joel Embiid")
    answer = ask_routed(agent, "how many 30 point games has embiid had per 36", slots_route("threshold_count", {"player": "Joel Embiid", "stat": "points", "threshold": 30, "rate": "per_36"}))
    assert answer.answered_by == "refused" and "the relation cannot honor" in answer.text
    assert agent.unanswered == "threshold_count: the relation cannot honor ['rate'] - it would answer for a different span than was asked"


def test_a_slot_the_reading_cannot_hold_is_refused_rather_than_crashing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ "stephen curry last 0 games" reads as a window of 0: the typed Scope
    refuses it at the stages' door (``router.settle``), and the question is
    refused the way a template's refusal is - never an uncaught error out of
    ``Agent.ask``. A recorded route holding such a slot fails at its own
    door, ``Route.from_slots``, before the answering loop sees it."""
    from association.query.normalizer import Normalized
    from association.query.reading import ScopeError

    monkeypatch.setattr("association.query.normalizer.normalize", lambda model, question: Normalized(["stephen curry"], ""))
    agent = _agent_with_players(tmp_path, "Stephen Curry")
    answer = agent.ask("stephen curry last 0 games")
    assert answer.answered_by == "refused" and "limit" in answer.text
    assert agent.unanswered is not None and "limit" in agent.unanswered
    with pytest.raises(ScopeError, match="limit"):
        slots_route("game_log", {"player": "Stephen Curry", "limit": 0})


def test_a_short_question_is_refused_before_the_model_is_asked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ "Tatum rec" used to answer his splits; a question of fewer than three
    words is refused with the generic sentence and costs no model call
    (refusals.too_short)."""

    def never(model: str, question: str) -> None:
        raise AssertionError("the normalizer must not be asked a two-word question")

    monkeypatch.setattr("association.query.normalizer.normalize", never)
    agent = _agent_with_players(tmp_path, "Jayson Tatum")
    answer = agent.ask("Tatum rec")
    assert answer.answered_by == "refused" and answer.timing.model_calls == 0
    assert answer.text == "I couldn't understand your question, 'Tatum rec'. Please try re-phrasing it."
    assert agent.unanswered == "fewer than 3 words"


def test_a_question_is_answered_in_the_latest_season_on_record_not_the_calendars(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ISSUES.md P1, 2026-09-30: from October 1 the calendar's season is one
    the warehouse has no games for. ``Agent.ask`` reads the latest season
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
