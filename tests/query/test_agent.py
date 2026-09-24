"""Regression tests for the SQL-as-prose safety net, the thinking-model
context-growth fix in Agent.ask, and the router fast path's fall-through."""

import time
from pathlib import Path
from typing import Any, NoReturn

import ollama
import pytest
from ollama import ChatResponse, Message

from association.query.agent import MAX_AUTO_SQL_RECOVERIES, MAX_ERROR_RECOVERIES, MAX_HISTORY_MESSAGES, MAX_TOOL_ITERATIONS, Agent, _extract_unrun_sql


def test_extract_sql_from_fenced_sql_block() -> None:
    text = "Here is the corrected query:\n```sql\nSELECT * FROM players\n```"
    assert _extract_unrun_sql(text) == "SELECT * FROM players"


def test_extract_sql_from_bare_fence_no_language_tag() -> None:
    text = "```\nWITH x AS (SELECT 1) SELECT * FROM x\n```"
    assert _extract_unrun_sql(text) == "WITH x AS (SELECT 1) SELECT * FROM x"


def test_extract_sql_from_unfenced_bare_query() -> None:
    text = "SELECT display_name FROM players"
    assert _extract_unrun_sql(text) == "SELECT display_name FROM players"


def test_extract_sql_returns_none_for_plain_prose_answer() -> None:
    text = "Domantas Sabonis had the most triple-doubles with 26."
    assert _extract_unrun_sql(text) is None


def test_extract_sql_returns_none_for_non_sql_fenced_code() -> None:
    text = "```python\nprint('hi')\n```"
    assert _extract_unrun_sql(text) is None


def test_extract_sql_handles_empty_and_none() -> None:
    assert _extract_unrun_sql("") is None
    assert _extract_unrun_sql(None) is None  # type: ignore[arg-type]  # deliberately passing None


def test_extract_sql_picks_first_valid_sql_block_among_several() -> None:
    text = "```text\nnot sql\n```\n```sql\nSELECT 1\n```"
    assert _extract_unrun_sql(text) == "SELECT 1"


@pytest.fixture
def think_agent(tmp_path: Path) -> Agent:
    import duckdb

    db_path = tmp_path / "test.duckdb"
    duckdb.connect(str(db_path)).close()
    # fast_path=False: every test on this fixture exercises the tool-calling
    # loop itself, and the router would otherwise consume the first mocked
    # response before the loop ever sees it.
    return Agent("qwen3:8b", str(db_path), tmp_path / "out", think=True, history_dir=tmp_path / ".history", fast_path=False)


def test_ask_strips_thinking_from_history_before_next_call(monkeypatch: pytest.MonkeyPatch, think_agent: Agent) -> None:
    """Regression: a thinking model's own past reasoning was being replayed back
    into its context on every later tool-call round, growing prompt-eval cost
    with each iteration for no benefit - confirmed live (an 8x cut in a
    follow-up iteration's prompt-eval time after stripping it). The model's
    `thinking` field must not survive into a later outgoing request."""
    calls: list[list[dict]] = []

    tool_call = Message.ToolCall(function=Message.ToolCall.Function(name="describe_table", arguments={"table_name": "players"}))
    responses = iter(
        [
            ChatResponse(
                model="qwen3:8b",
                created_at="",
                done=True,
                message=Message(role="assistant", content="", thinking="lots of first-turn reasoning", tool_calls=[tool_call]),
            ),
            ChatResponse(
                model="qwen3:8b",
                created_at="",
                done=True,
                message=Message(role="assistant", content="Final answer.", thinking="second-turn reasoning"),
            ),
        ]
    )

    def fake_chat(**kwargs: Any) -> ChatResponse:
        calls.append(kwargs["messages"])
        return next(responses)

    monkeypatch.setattr(ollama, "chat", fake_chat)
    result = think_agent.ask("some question").text

    assert result == "Final answer."
    assert len(calls) == 2
    second_call_messages = calls[1]
    assistant_msg = next(m for m in second_call_messages if m.get("role") == "assistant")
    assert assistant_msg.get("thinking") is None
    assert "lots of first-turn reasoning" not in str(second_call_messages)


def test_ask_gives_honest_message_when_recovery_cap_exhausted(monkeypatch: pytest.MonkeyPatch, think_agent: Agent) -> None:
    """Regression: once MAX_AUTO_SQL_RECOVERIES auto-recoveries are used up, a
    model that keeps printing SQL instead of calling run_sql used to get its
    raw prose returned as the final answer verbatim - including trailing text
    like "Let's run this corrected query" that never actually ran (confirmed
    live). The final reply must say plainly that it didn't work, not read
    like an action that's still pending."""
    responses = iter(
        [ChatResponse(model="qwen3:8b", created_at="", done=True, message=Message(role="assistant", content="SELECT 1")) for _ in range(MAX_AUTO_SQL_RECOVERIES)]
        + [
            ChatResponse(
                model="qwen3:8b",
                created_at="",
                done=True,
                message=Message(role="assistant", content="Let's run this corrected query:\n```sql\nSELECT 1\n```"),
            )
        ]
    )

    def fake_chat(**kwargs: Any) -> ChatResponse:
        return next(responses)

    monkeypatch.setattr(ollama, "chat", fake_chat)
    result = think_agent.ask("some question").text

    assert "wasn't able to get a working query" in result
    assert "SELECT 1" in result
    assert "Let's run this corrected query" not in result


def test_ask_blocks_fabricated_answer_right_after_tool_error_and_retries(monkeypatch: pytest.MonkeyPatch, think_agent: Agent) -> None:
    """Regression: a real query hit a column-not-found SQL error, and instead of
    retrying with a corrected query, the model finalized with a fabricated answer
    using literal "[Player Name 1]" / "[NetPoints Value]" placeholder text as if
    it were real data (confirmed live). The agent must not return a final answer
    right after an unrecovered tool error - it should nudge a retry instead, and
    a subsequent successful run_sql call should produce the real answer."""
    bad_call = Message.ToolCall(function=Message.ToolCall.Function(name="run_sql", arguments={"query": "SELECT bogus_column FROM players"}))
    good_call = Message.ToolCall(function=Message.ToolCall.Function(name="run_sql", arguments={"query": "SELECT display_name FROM players"}))
    responses = iter(
        [
            ChatResponse(model="qwen3:8b", created_at="", done=True, message=Message(role="assistant", content="", tool_calls=[bad_call])),
            ChatResponse(
                model="qwen3:8b",
                created_at="",
                done=True,
                message=Message(role="assistant", content="1. [Player Name 1] - [Value]\n2. [Player Name 2] - [Value]"),
            ),
            ChatResponse(model="qwen3:8b", created_at="", done=True, message=Message(role="assistant", content="", tool_calls=[good_call])),
            ChatResponse(model="qwen3:8b", created_at="", done=True, message=Message(role="assistant", content="Real answer.")),
        ]
    )

    def fake_chat(**kwargs: Any) -> ChatResponse:
        return next(responses)

    def fake_run_sql(query: str) -> str:
        if "bogus_column" in query:
            return 'SQL error: Binder Error: column "bogus_column" not found'
        return '{"rows": [{"display_name": "Test Player"}], "row_count": 1, "truncated": false}'

    monkeypatch.setattr(ollama, "chat", fake_chat)
    think_agent.dispatch["run_sql"] = fake_run_sql
    result = think_agent.ask("some question").text

    assert result == "Real answer."
    assert "[Player Name 1]" not in result


def test_ask_gives_honest_message_when_error_recovery_cap_exhausted(monkeypatch: pytest.MonkeyPatch, think_agent: Agent) -> None:
    """Regression: if the model keeps finalizing with a fabricated answer after
    an unrecovered tool error past MAX_ERROR_RECOVERIES retries, the final reply
    must say plainly that the query failed, not return the fabricated content."""
    bad_call = Message.ToolCall(function=Message.ToolCall.Function(name="run_sql", arguments={"query": "SELECT bogus_column FROM players"}))
    responses = iter(
        [ChatResponse(model="qwen3:8b", created_at="", done=True, message=Message(role="assistant", content="", tool_calls=[bad_call]))]
        + [
            ChatResponse(
                model="qwen3:8b",
                created_at="",
                done=True,
                message=Message(role="assistant", content=f"Fabricated attempt {i}: [Player Name] - [Value]"),
            )
            for i in range(MAX_ERROR_RECOVERIES + 1)
        ]
    )

    def fake_chat(**kwargs: Any) -> ChatResponse:
        return next(responses)

    def fake_run_sql(query: str) -> str:
        return 'SQL error: Binder Error: column "bogus_column" not found'

    monkeypatch.setattr(ollama, "chat", fake_chat)
    think_agent.dispatch["run_sql"] = fake_run_sql
    result = think_agent.ask("some question").text

    assert "ran into an error" in result
    assert "bogus_column" in result
    assert "[Player Name]" not in result


def test_ask_writes_history_file_even_without_verbose(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The whole point of RunHistory: a run's full evidence (command, trace,
    timing, answer) must land on disk regardless of whether --verbose was
    passed - not_verbose here, and no [thinking]/tool-call output on stderr,
    but the history file must still exist and hold everything."""
    import duckdb

    db_path = tmp_path / "test.duckdb"
    duckdb.connect(str(db_path)).close()
    history_dir = tmp_path / ".history"
    agent = Agent("qwen2.5:7b", str(db_path), tmp_path / "out", history_dir=history_dir)

    def fake_chat(**kwargs: Any) -> ChatResponse:
        return ChatResponse(model="qwen2.5:7b", created_at="", done=True, message=Message(role="assistant", content="Final answer."))

    monkeypatch.setattr(ollama, "chat", fake_chat)
    result = agent.ask("some question").text

    assert result == "Final answer."
    files = list(history_dir.glob("*.log"))
    assert len(files) == 1
    content = files[0].read_text()
    assert "question: some question" in content
    assert "model inference #1" in content
    assert "answer:\nFinal answer." in content


def test_ask_writes_history_file_even_when_it_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A crash mid-run is exactly a "failed run" this exists to leave evidence
    for - the history file must still be written, with the traceback as the
    recorded answer, rather than lost because ask() never returned normally."""
    import duckdb

    db_path = tmp_path / "test.duckdb"
    duckdb.connect(str(db_path)).close()
    history_dir = tmp_path / ".history"
    agent = Agent("qwen2.5:7b", str(db_path), tmp_path / "out", history_dir=history_dir)

    def fake_chat(**kwargs: Any) -> ChatResponse:
        raise RuntimeError("simulated ollama connection failure")

    monkeypatch.setattr(ollama, "chat", fake_chat)
    with pytest.raises(RuntimeError, match="simulated ollama connection failure"):
        agent.ask("some question")

    files = list(history_dir.glob("*.log"))
    assert len(files) == 1
    content = files[0].read_text()
    assert "EXCEPTION" in content
    assert "simulated ollama connection failure" in content


def _agent(tmp_path: Path, **kwargs: Any) -> Agent:
    import duckdb

    db_path = tmp_path / "test.duckdb"
    duckdb.connect(str(db_path)).close()
    return Agent("qwen2.5:7b", str(db_path), tmp_path / "out", history_dir=tmp_path / ".history", **kwargs)


def _agent_with_players(tmp_path: Path, *names: str) -> Agent:
    """An agent over a warehouse holding exactly ``names``, for the checks that
    run against the question's own words."""
    import duckdb

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    for i, name in enumerate(names):
        con.execute("INSERT INTO players VALUES (?, ?)", [str(i), name])
    con.close()
    return Agent("qwen2.5:7b", str(db_path), tmp_path / "out", history_dir=tmp_path / ".history")


def test_the_fast_path_replaces_a_player_the_question_never_named(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Measured live: "compare sga and embiid" routed to Jusuf Nurkic in the
    second slot and answered with a confident table about him. The template is
    not reached until the names are the question's."""
    from association.query.router import Route
    from association.query.templates.common import TemplateResult

    seen: list[str] = []

    def record(ctx: Any, slots: dict[str, Any]) -> TemplateResult:
        seen.extend(slots["players"])
        return TemplateResult(data={}, answer="templated")

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="player_compare", slots={"players": ["Shai Gilgeous-Alexander", "Jusuf Nurkic"]}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"player_compare": record})
    _agent_with_players(tmp_path, "Joel Embiid", "Jusuf Nurkic").ask("compare sga and embiid")
    assert seen == ["Shai Gilgeous-Alexander", "Joel Embiid"]


def test_a_team_only_intent_naming_one_player_refuses_rather_than_answering_the_league(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """yardstick-v2 F111: "alperen şengün alltime record" routed to
    team_leaderboard - no player slot, no team slot - and answered the
    league standings, Sengun never read. The template is not reached at
    all: team_leaderboard has no reading for a named player, so the
    question is refused, naming him, rather than answered about the wrong
    subject. Diacritics ("şengün") are already folded before this runs."""
    from association.query.router import Route

    reached = False

    def record(ctx: Any, slots: dict[str, Any]) -> Any:
        nonlocal reached
        reached = True
        raise AssertionError("team_leaderboard should not run at all")

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="team_leaderboard", slots={"stat": "record", "limit": 1}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"team_leaderboard": record})
    answer = _agent_with_players(tmp_path, "Alperen Sengun").ask("alperen şengün alltime record")
    assert not reached
    assert "Alperen Sengun" in answer.text
    assert "team leaderboard" in answer.text


def test_a_team_only_intent_with_a_team_named_is_unaffected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A genuine team question - a `team` slot resolving to a REAL
    franchise - runs the template normally, whatever player-shaped words
    happen to also appear. A `teams` table is needed here (unlike
    `_agent_with_players`'s plain one): `_has_a_real_team` looks the team
    slot up against it, and a warehouse missing the table entirely reads as
    "no team found" the same way `entities._team_named` already does."""
    import duckdb

    from association.query.router import Route
    from association.query.templates.common import TemplateResult

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1', 'Alperen Sengun')")
    con.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('2', 'Houston Rockets', 'HOU')")
    con.close()
    agent = Agent("qwen2.5:7b", str(db_path), tmp_path / "out", history_dir=tmp_path / ".history")

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="team_leaderboard", slots={"stat": "record", "team": "Houston Rockets"}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"team_leaderboard": lambda ctx, slots: TemplateResult(data={}, answer="templated")})
    answer = agent.ask("alperen şengün rockets record")
    assert answer.text == "templated"


def test_a_router_that_could_not_be_asked_falls_through_saying_why(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The rule is unchanged - a router failure costs a round trip, never an
    answer - so this still falls through. What it must not do is report it as
    the model having said something unusable: reported from a laptop without
    the router model pulled, where every question came back "the router
    returned no usable classification" and nothing pointed at ollama."""
    from association.query.history import RunHistory
    from association.query.router import RouterUnavailable

    agent = _agent(tmp_path)

    def unavailable(*a: Any, **k: Any) -> None:
        raise RouterUnavailable("ollama could not serve the router model 'qwen2.5:3b': not found")

    monkeypatch.setattr("association.query.agent.route", unavailable)
    # The fast path alone: _ask_inner would go on to the real agent, and this
    # suite reaches no ollama.
    assert agent._try_fast_path("who leads the league in assists?", RunHistory(False, tmp_path / ".history")) is None
    assert agent.fell_through is not None
    assert "router model 'qwen2.5:3b'" in agent.fell_through
    assert "no usable classification" not in agent.fell_through


def test_a_rerouted_intent_runs_the_template_it_was_rerouted_to(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A player's record against a team arrives as head_to_head and is rewritten
    to with_without (ISSUES.md #163). This pins that the HANDLER moves with the
    intent, which is the half that shipped broken: the handler was resolved
    from the router's intent before the rewrite, so "Embiid career record vs
    boston" logged `head_to_head -> with_without` and then ran head_to_head,
    which refused for wanting two team names. Every offline replay passed,
    because the replay script looks the handler up afterwards and the real
    pipeline looked it up before - so only a test on this path can catch it."""
    from association.query.router import Route
    from association.query.templates.common import TemplateResult, TemplateUnsupported

    ran: list[str] = []

    def head_to_head(ctx: Any, slots: dict[str, Any]) -> TemplateResult:
        ran.append("head_to_head")
        raise TemplateUnsupported("head_to_head needs two team names")

    def with_without(ctx: Any, slots: dict[str, Any]) -> TemplateResult:
        ran.append("with_without")
        return TemplateResult(data={}, answer="templated")

    import duckdb

    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("INSERT INTO players VALUES ('1', 'Joel Embiid')")
    con.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    con.execute("INSERT INTO teams VALUES ('2', 'Boston Celtics', 'BOS')")
    con.close()
    agent = Agent("qwen2.5:7b", str(db_path), tmp_path / "out", history_dir=tmp_path / ".history")

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="head_to_head", slots={"teams": ["Joel Embiid", "Boston Celtics"], "span": "career"}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"head_to_head": head_to_head, "with_without": with_without})
    answer = agent.ask("Embiid career record vs boston")
    assert ran == ["with_without"]
    assert "templated" in (answer.text or "")


def test_a_player_the_question_cannot_account_for_is_refused_not_passed_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Falling through here was measured and is worse: given one of these the
    agent spent 55 seconds writing a confident fingerprint, percentages
    included, for "Ronaldo Lopes" - who does not exist. Same reasoning as
    check_coverage returning its refusal rather than raising it."""
    from association.query.router import Route
    from association.query.templates.common import TemplateResult

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="player_compare", slots={"players": ["Jusuf Nurkic", "Joel Embiid"]}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"player_compare": lambda ctx, slots: TemplateResult(data={}, answer="templated")})
    monkeypatch.setattr(ollama, "chat", lambda **kw: ChatResponse(model="m", created_at="", done=True, message=Message(role="assistant", content="agent answer")))
    answer = _agent_with_players(tmp_path, "Joel Embiid", "Jusuf Nurkic").ask("compare the two best centers")
    assert "was not answered" in answer.text and "Jusuf Nurkic" in answer.text
    assert "agent answer" not in answer.text and "templated" not in answer.text


def test_a_stray_name_on_a_question_no_template_reads_one_for_changes_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """head_to_head never looks at a player slot, so an invented one there
    cannot make the answer about the wrong person - and refusing over it would
    break a question that works."""
    from association.query.router import Route
    from association.query.templates.common import TemplateResult

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="head_to_head", slots={"player": "Jusuf Nurkic"}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"head_to_head": lambda ctx, slots: TemplateResult(data={}, answer="templated")})
    assert _agent_with_players(tmp_path, "Joel Embiid", "Jusuf Nurkic").ask("Lakers vs Celtics record").text == "templated"


def test_a_fingerprint_keeps_every_player_the_question_named(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ "compare fingerprints for embiid vs jokic in 2026" arrived as a single
    player slot, and one polygon is not half an answer - it is a different
    question, answered without saying so."""
    from association.query.router import Route
    from association.query.templates.common import TemplateResult

    seen: list[str] = []

    def record(ctx: Any, slots: dict[str, Any]) -> TemplateResult:
        seen.extend(slots["players"])
        return TemplateResult(data={}, answer="rendered")

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="fingerprint", slots={"player": "Ben Simmons"}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"fingerprint": record})
    _agent_with_players(tmp_path, "Joel Embiid", "Nikola Jokic", "Ben Simmons").ask("compare fingerprints for embiid vs jokic in 2026")
    assert seen == ["Joel Embiid", "Nikola Jokic"]


def test_a_fingerprint_that_lost_a_player_to_a_typo_says_so(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ "generate fingerprints for embiid vs jolic in 2026" drew Joel Embiid
    alone. The typo cannot be repaired, so the half-answer has to be stated -
    one polygon where two were asked for, with nothing saying so, is the
    failure shape this project keeps producing."""
    from association.query.router import Route
    from association.query.templates.common import TemplateResult

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="fingerprint", slots={"player": "Joel Embiid"}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"fingerprint": lambda ctx, slots: TemplateResult(data={}, answer="Rendered.")})
    answer = _agent_with_players(tmp_path, "Joel Embiid").ask("generate fingerprints for embiid vs jolic in 2026").text
    assert answer.startswith("Rendered.") and "only one of them matches" in answer


def test_the_fast_path_asks_about_a_surname_the_router_completed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """End to end, because the value of this is that the template sees the
    question's own word and asks - not that a helper returned a string."""
    from association.query.router import Route
    from association.query.templates.common import TemplateResult

    seen: list[str] = []

    def record(ctx: Any, slots: dict[str, Any]) -> TemplateResult:
        seen.append(slots["player"])
        return TemplateResult(data={}, answer="answered")

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="player_stat", slots={"player": "Jaylen Brown"}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"player_stat": record})
    _agent_with_players(tmp_path, "Jaylen Brown", "Bobby Brown", "Kwame Brown").ask("how many points does brown average?")
    assert seen == ["Brown"]


def test_the_fast_path_says_how_it_read_a_name_the_question_left_open(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The reading reaches the ANSWER, which is the whole condition the default
    is allowed under: "maxey" is Tyrese because he is the only Maxey who still
    plays, and somebody who meant Marlon has to be told what to type instead.
    Templates are called with a bare connection in 27 places, so the sentence
    travels by entities.collect_name_readings and is attached here - removing
    that block leaves a right answer about a player nobody said was chosen."""
    from association.query.entities import Entity, _note_name_reading
    from association.query.router import Route
    from association.query.templates.common import TemplateResult

    def reads_a_name(ctx: Any, slots: dict[str, Any]) -> TemplateResult:
        _note_name_reading("maxey", Entity("1", "Tyrese Maxey"), [Entity("0", "Marlon Maxey")], 2026, named_in_full=False)
        return TemplateResult(data={}, answer="Tyrese Maxey averaged 28.0 points.")

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="player_stat", slots={"player": "maxey"}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"player_stat": reads_a_name})
    answer = _agent_with_players(tmp_path, "Marlon Maxey", "Tyrese Maxey").ask("how many points does maxey average?")
    reading = "('maxey' was read as Tyrese Maxey, the only match who played in 2025-26. Marlon Maxey also matches - use the full name, or name a season he played, to ask about him.)"
    assert answer.text == f"Tyrese Maxey averaged 28.0 points. {reading}"
    assert answer.data == {"name_readings": [reading]}


def test_fast_path_is_skipped_entirely_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called = False

    def fake_route(*args: Any, **kwargs: Any) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("association.query.agent.route", fake_route)
    monkeypatch.setattr(ollama, "chat", lambda **kw: ChatResponse(model="m", created_at="", done=True, message=Message(role="assistant", content="agent answer")))
    assert _agent(tmp_path, fast_path=False).ask("q").text == "agent answer"
    assert not called


def test_with_fallthrough_disabled_a_question_no_template_answers_is_an_error_naming_why(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Development only: the agent iterates on SQL for minutes and rarely gets
    it right, so a person testing the fast path is told why it gave up
    instead. No model call is spent past the router."""
    from association.query.answer import FallthroughDisabled
    from association.query.router import Route
    from association.query.templates import TemplateResult, TemplateUnsupported

    def chat_must_not_run(**kw: Any) -> None:
        raise AssertionError("the agent must not be asked")

    monkeypatch.setattr(ollama, "chat", chat_must_not_run)
    import duckdb

    # The entity stages between the router and a template read these tables.
    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    con.close()
    agent = Agent("qwen2.5:7b", str(db_path), tmp_path / "out", history_dir=tmp_path / ".history", fallthrough=False)

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="other", slots={}))
    with pytest.raises(FallthroughDisabled, match="intent 'other' has no template yet"):
        agent.ask("who had the most triple-doubles?")

    def refusing(ctx: Any, slots: dict[str, Any]) -> TemplateResult:
        raise TemplateUnsupported("record_when needs a known stat and a positive threshold, got 'wins'/20")

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="record_when", slots={"team": "Philadelphia 76ers"}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"record_when": refusing})
    with pytest.raises(FallthroughDisabled, match="record_when: record_when needs a known stat"):
        agent.ask("sixers record when maxey scored 20+ points")
    assert agent.fell_through == "record_when: record_when needs a known stat and a positive threshold, got 'wins'/20"

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: None)
    with pytest.raises(FallthroughDisabled, match="no usable classification"):
        agent.ask("q")

    # A question a template answers is unaffected.
    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="record_when", slots={}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"record_when": lambda ctx, slots: TemplateResult(data={}, answer="answered")})
    assert agent.ask("q").text == "answered" and agent.fell_through is None


# ---------------- the compiled step between a refusal and the fall-through agent ----------------


def _refusing_template(ctx: Any, slots: dict[str, Any]) -> NoReturn:
    """A template stand-in that always raises TemplateUnsupported, the way
    check_scope or a template's own validation does - the only trigger that
    reaches association.query.compose.answer (agent._try_compose)."""
    from association.query.templates.common import TemplateUnsupported

    raise TemplateUnsupported("a test double's refusal, standing in for whatever check_scope or a real template would have raised")


def test_a_templates_refusal_that_compose_answers_is_returned_as_fast_with_the_trace_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The whole point of wiring the compiler in: a scoping refusal that used
    to cost a fall-through to the slow agent is answered here instead, exactly
    like a template's own result - answered_by="fast", the intent kept - with
    a trace line naming the point on the relation it composed, the way
    "-> (router) intent=..." names what was routed."""
    from association.query.router import Route
    from association.query.templates.common import TemplateResult

    def composed_answer(ctx: Any, intent: str, slots: dict[str, Any], question: str) -> TemplateResult:
        return TemplateResult(data={"skeleton": "aggregate", "measures": ["points"], "rows": [{"points": 30.0}]}, answer="Joel Embiid has averaged 30.0 points since 2024.")

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="player_stat", slots={"player": "Joel Embiid", "since": "2024"}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"player_stat": _refusing_template})
    monkeypatch.setattr("association.query.compose.answer", composed_answer)

    seen: list[str] = []
    agent = _agent_with_players(tmp_path, "Joel Embiid")
    agent.trace = seen.append
    agent.verbose = True
    answer = agent.ask("how many points has embiid averaged since 2024?")

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


def test_a_compose_none_falls_through_to_the_agent_exactly_as_before(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No monkeypatch on compose.answer here: this exercises the real
    compiler (association.query.compose) on an intent that is not a point on
    any relation - a fingerprint is a chart over NetPoints - so it declines
    with None, and the wiring changes nothing: the fast path still falls
    through to the agent, same as before this step existed."""
    from association.query.router import Route

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="fingerprint", slots={"player": "Joel Embiid", "season": 2026}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"fingerprint": _refusing_template})
    monkeypatch.setattr(ollama, "chat", lambda **kw: ChatResponse(model="m", created_at="", done=True, message=Message(role="assistant", content="agent answer")))

    answer = _agent_with_players(tmp_path, "Joel Embiid").ask("show me embiid's fingerprint for 2026")

    assert answer.text == "agent answer"
    assert answer.answered_by == "agent"


def test_a_compose_refusal_is_returned_as_the_answer_not_a_fall_through(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A clarification or a no-match from the compiler is an answer, not a
    fall-through: it looked at the question and had something to say. The
    agent must never be asked."""
    from association.query.router import Route
    from association.query.templates.common import TemplateResult

    def composed_refusal(ctx: Any, intent: str, slots: dict[str, Any], question: str) -> TemplateResult:
        return TemplateResult(data={"ambiguous": "since"}, answer="I can't tell which span 'the last few' means - a number of games, or a number of seasons?")

    def chat_must_not_run(**kw: Any) -> None:
        raise AssertionError("the agent must not be asked - the compiler already answered")

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="player_stat", slots={"player": "Joel Embiid", "since": "the last few"}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"player_stat": _refusing_template})
    monkeypatch.setattr("association.query.compose.answer", composed_refusal)
    monkeypatch.setattr(ollama, "chat", chat_must_not_run)

    answer = _agent_with_players(tmp_path, "Joel Embiid").ask("embiid's line over the last few?")

    assert answer.text == "I can't tell which span 'the last few' means - a number of games, or a number of seasons?"
    assert answer.answered_by == "fast"


def test_a_composed_answer_carries_the_name_reading_it_noted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The compiler reads a name exactly the way a template does - through
    entities.collect_name_readings - so a default it chose ("maxey" is Tyrese)
    is visible in the answer here too, not only on the direct template path."""
    from association.query.entities import Entity, _note_name_reading
    from association.query.router import Route
    from association.query.templates.common import TemplateResult

    def composed_with_reading(ctx: Any, intent: str, slots: dict[str, Any], question: str) -> TemplateResult:
        _note_name_reading("maxey", Entity("1", "Tyrese Maxey"), [Entity("0", "Marlon Maxey")], 2026, named_in_full=False)
        return TemplateResult(data={}, answer="Tyrese Maxey has averaged 28.0 points since 2024.")

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="player_stat", slots={"player": "maxey", "since": "2024"}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"player_stat": _refusing_template})
    monkeypatch.setattr("association.query.compose.answer", composed_with_reading)

    answer = _agent_with_players(tmp_path, "Marlon Maxey", "Tyrese Maxey").ask("how many points has maxey averaged since 2024?")

    reading = "('maxey' was read as Tyrese Maxey, the only match who played in 2025-26. Marlon Maxey also matches - use the full name, or name a season he played, to ask about him.)"
    assert answer.text == f"Tyrese Maxey has averaged 28.0 points since 2024. {reading}"
    assert answer.data == {"name_readings": [reading]}


def test_unported_intent_falls_through_to_the_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A shape with no template yet must reach the old path unchanged - that is
    what makes porting one shape at a time safe."""
    from association.query.router import Route

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="other", slots={}))
    monkeypatch.setattr(ollama, "chat", lambda **kw: ChatResponse(model="m", created_at="", done=True, message=Message(role="assistant", content="agent answer")))
    assert _agent(tmp_path).ask("who had the most triple-doubles?").text == "agent answer"


def test_router_failure_falls_through_rather_than_erroring(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: None)
    monkeypatch.setattr(ollama, "chat", lambda **kw: ChatResponse(model="m", created_at="", done=True, message=Message(role="assistant", content="agent answer")))
    assert _agent(tmp_path).ask("q").text == "agent answer"


def test_fast_path_answer_is_recorded_in_conversation_for_later_followups(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The tool loop never runs on the fast path, but a follow-up that DOES
    fall through still needs to see what was already asked and answered."""
    from association.query.router import Route
    from association.query.templates.common import TemplateResult

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="threshold_count", slots={"stat": "points", "threshold": 30}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"threshold_count": lambda con, slots: TemplateResult(data={"leaders": []}, answer="template answer")})
    # No ollama.chat stub: reaching one would itself be the bug. The router is
    # stubbed out above, and a template answers without a model call.
    # threshold_count is in SUBJECT_RESTORABLE_INTENTS (F093), so scope_from_question
    # reads the question's words for a dropped subject - an empty `players`
    # table, not the fully tableless warehouse _agent gives by default, so
    # that read finds nobody rather than raising a CatalogException.
    agent = _agent_with_players(tmp_path)
    assert agent.ask("most 30+ point games?").text == "template answer"
    assert agent.last_question == "most 30+ point games?"
    assert [m["content"] for m in agent.messages[1:]] == ["most 30+ point games?", "template answer"]


def test_every_advertised_tool_can_actually_be_dispatched(think_agent: Agent) -> None:
    """The tool schemas and the dispatch table are two lists of the same names,
    kept in step by hand. A name in TOOLS with no handler is a KeyError the
    moment the model calls it; a handler the schemas never mention is dead code
    the model cannot reach - which is exactly the state render_fingerprint was
    left in while it did not fit the preamble budget."""
    from association.query.prompt import TOOLS

    advertised = {tool["function"]["name"] for tool in TOOLS}
    assert advertised == set(think_agent.dispatch)


def test_every_tool_schema_names_its_required_parameters(think_agent: Agent) -> None:
    """Under constrained decoding the schema is the whole contract: a required
    parameter the handler does not accept is a TypeError at call time."""
    import inspect

    from association.query.prompt import TOOLS

    for tool in TOOLS:
        function = tool["function"]
        parameters = inspect.signature(think_agent.dispatch[function["name"]]).parameters
        for name in function["parameters"]["properties"]:
            assert name in parameters, f"{function['name']} advertises {name!r}, which its handler does not accept"


def test_the_fast_path_carries_out_the_intent_and_data_it_used_to_discard(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """TemplateResult.data existed so a caller could render the answer itself,
    and _try_fast_path returned only result.answer, so nothing ever could.
    That is the whole reason Phase 0 of the 2.0 plan exists."""
    from association.query.router import Route
    from association.query.templates.common import TemplateResult

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="leaderboard", slots={"stat": "points"}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"leaderboard": lambda con, slots: TemplateResult(data={"leaders": ["Jokic"]}, answer="Jokic.")})
    answer = _agent(tmp_path).ask("who leads the league in scoring?")

    assert answer.text == "Jokic."
    assert answer.answered_by == "fast"
    assert answer.intent == "leaderboard"
    assert answer.data == {"leaders": ["Jokic"]}
    assert answer.question == "who leads the league in scoring?"


def test_an_agent_answer_has_no_intent_or_data_rather_than_an_empty_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Only a template produces those. None says so; {} would read as "the
    template ran and found nothing", which is a different claim."""
    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: None)
    monkeypatch.setattr(ollama, "chat", lambda **kw: ChatResponse(model="m", created_at="", done=True, message=Message(role="assistant", content="agent answer")))
    answer = _agent(tmp_path).ask("q")

    assert answer.answered_by == "agent"
    assert answer.intent is None
    assert answer.data is None


def test_a_fast_path_answer_carries_the_chart_the_template_wrote(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from association.query.answer import Artifact
    from association.query.router import Route
    from association.query.templates.common import TemplateResult

    drawn = Artifact("shot_chart", tmp_path / "shotchart_x.html")
    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="shot_chart", slots={"player": "x"}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"shot_chart": lambda con, slots: TemplateResult(data={}, answer="Rendered.", artifacts=[drawn])})
    assert _agent(tmp_path).ask("chart x").artifacts == [drawn]


def test_an_agent_answer_carries_the_chart_a_tool_call_wrote(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A render tool returns prose to the model, because prose is all the model
    can read - so the file it wrote reaches the caller only because Toolbox
    records it on the side."""
    from association.query.answer import Artifact

    agent = _agent(tmp_path, fast_path=False)
    drawn = Artifact("fingerprint", tmp_path / "fingerprint_x.html")

    def fake_chat(**kwargs: Any) -> ChatResponse:
        agent.toolbox.artifacts.append(drawn)  # what Toolbox._rendered does
        return ChatResponse(model="m", created_at="", done=True, message=Message(role="assistant", content="Drew it."))

    monkeypatch.setattr(ollama, "chat", fake_chat)
    assert agent.ask("plot x").artifacts == [drawn]


def test_one_questions_charts_are_never_reported_as_the_nexts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A Toolbox outlives any single question, so an undrained list would have
    every later answer claiming to have drawn the first one's chart."""
    from association.query.answer import Artifact

    agent = _agent(tmp_path, fast_path=False)
    agent.toolbox.artifacts.append(Artifact("fingerprint", tmp_path / "stale.html"))
    monkeypatch.setattr(ollama, "chat", lambda **kw: ChatResponse(model="m", created_at="", done=True, message=Message(role="assistant", content="answer")))
    assert agent.ask("something else entirely").artifacts == []


def test_the_history_label_comes_from_the_caller_not_the_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`shlex.join(sys.argv)` describes a CLI invocation and nothing else - for
    a server handling many questions it is the same wrong string every time."""
    import duckdb

    db_path = tmp_path / "test.duckdb"
    duckdb.connect(str(db_path)).close()
    history_dir = tmp_path / ".history"
    agent = Agent("qwen2.5:7b", str(db_path), tmp_path / "out", history_dir=history_dir, fast_path=False)
    monkeypatch.setattr(ollama, "chat", lambda **kw: ChatResponse(model="m", created_at="", done=True, message=Message(role="assistant", content="a")))

    agent.ask("q", label="POST /api/ask")

    assert "command: POST /api/ask" in next(iter(history_dir.glob("*.log"))).read_text()


def test_a_trace_sink_takes_the_place_of_stderr_entirely(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Including the [history] line, which printed unconditionally - a server
    that captured the trace but still wrote that one to its own terminal would
    have gone half-way."""
    import duckdb

    db_path = tmp_path / "test.duckdb"
    duckdb.connect(str(db_path)).close()
    seen: list[str] = []
    agent = Agent("qwen2.5:7b", str(db_path), tmp_path / "out", history_dir=tmp_path / ".history", fast_path=False, verbose=True, trace=seen.append)
    monkeypatch.setattr(ollama, "chat", lambda **kw: ChatResponse(model="m", created_at="", done=True, message=Message(role="assistant", content="a")))

    agent.ask("q")

    assert any(line.startswith("[history] ") for line in seen)
    assert any("model inference #1" in line for line in seen)
    assert capsys.readouterr().err == ""


def _record_turn(agent: Agent, question: str, *, tool_calls: int) -> None:
    """Append one finished turn the way _ask_inner does, and trim after it.

    ``tool_calls=0`` is a template answer; anything higher is an agent turn
    with that many tools asked for in one round, which is what makes a turn an
    odd number of messages - see the perturbation below.
    """
    agent._turn_starts.append(len(agent.messages))
    agent.messages.append({"role": "user", "content": question})
    if tool_calls:
        agent.messages.append({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "run_sql", "arguments": {}}} for _ in range(tool_calls)]})
        agent.messages += [{"role": "tool", "content": '{"rows": []}'} for _ in range(tool_calls)]
    agent.messages.append({"role": "assistant", "content": f"answer to {question}"})
    agent._trim_history()


def _orphaned_tool_results(messages: list[dict[str, Any]]) -> list[int]:
    """Indexes of tool results with no assistant tool call above them."""
    orphans = []
    outstanding = False
    for i, message in enumerate(messages):
        if message.get("role") == "assistant":
            outstanding = bool(message.get("tool_calls"))
        elif message.get("role") == "tool" and not outstanding:
            orphans.append(i)
    return orphans


def test_trimming_never_leaves_a_tool_result_without_the_call_that_asked_for_it(tmp_path: Path) -> None:
    """The fixed slice this replaced cut at an offset, which lands inside a
    turn whenever the arithmetic says so: between an assistant message carrying
    tool_calls and the tool results answering them. Ollama accepts that rather
    than rejecting it - measured, it is not the schema error a stricter API
    would raise - so nothing fails and the cost is paid quietly, as a JSON blob
    spending context with no question attached to say what it was for.

    Driven with the same mix as the perturbation below, since a conversation of
    uniform turns never splits either way and would prove nothing.
    """
    agent = _agent(tmp_path)

    for i in range(30):
        _record_turn(agent, f"question {i}", tool_calls=[0, 0, 2][i % 3])

    assert len(agent.messages) <= MAX_HISTORY_MESSAGES
    assert agent.messages[0]["role"] == "system"
    assert _orphaned_tool_results(agent.messages) == []
    # The cut lands on a question, not in the middle of answering one, and the
    # turn just finished survived it whole.
    assert agent.messages[1]["role"] == "user"
    assert agent.messages[-5]["content"] == "question 29"  # user, assistant(2 calls), tool, tool, assistant


def test_the_fixed_slice_this_replaced_would_have_split_a_turn() -> None:
    """The perturbation, kept as a test: cutting at a fixed offset orphans a
    tool result. Without this, a trim that quietly stopped cutting anything at
    all would pass the check above just as well.

    The pattern matters, and measuring it narrowed the bug. A conversation of
    uniform turns never splits: every turn is an even number of messages and
    the offset is odd, so the cut lands in the same place in every turn
    forever. What breaks that is a round where the model asks for TWO tools at
    once, since the loop appends one ``tool`` message per call - an odd turn.
    Mixed that way, a quarter of trims orphan a tool result. Two template
    answers and one two-call agent turn, repeating, is the shortest
    reproducer.
    """
    kinds = ["fast", "fast", "two calls"]
    conversation: list[dict[str, Any]] = [{"role": "system", "content": "x"}]
    split_somewhere = False
    for i in range(15):
        conversation.append({"role": "user", "content": f"question {i}"})
        if kinds[i % len(kinds)] == "two calls":
            conversation.append({"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "run_sql"}}, {"function": {"name": "describe_table"}}]})
            conversation += [{"role": "tool", "content": "{}"}, {"role": "tool", "content": "{}"}]
        conversation.append({"role": "assistant", "content": "answer"})
        if len(conversation) > MAX_HISTORY_MESSAGES:
            old_way = [conversation[0], *conversation[-(MAX_HISTORY_MESSAGES - 1) :]]
            split_somewhere = split_somewhere or bool(_orphaned_tool_results(old_way))

    assert split_somewhere


def test_a_turn_longer_than_the_cap_is_kept_whole_rather_than_beheaded(tmp_path: Path) -> None:
    """A cap is a ceiling on old turns, not a license to drop the question
    being answered. Nothing observed produces a turn this long - MAX_TOOL_
    ITERATIONS bounds it well under the cap - but 'keep whole turns' has to
    mean the current one too, or the fallback quietly deletes it."""
    agent = _agent(tmp_path)
    agent._turn_starts.append(len(agent.messages))
    agent.messages.append({"role": "user", "content": "the long one"})
    for _ in range(MAX_HISTORY_MESSAGES + 10):
        agent.messages.append({"role": "assistant", "content": "thinking"})
    agent._trim_history()

    assert agent.messages[1] == {"role": "user", "content": "the long one"}


def test_resetting_a_conversation_leaves_nothing_of_the_last_one(tmp_path: Path) -> None:
    """What the web server calls between requests. `last_question` matters as
    much as the messages do: route() reads it as `previous_question`."""
    agent = _agent(tmp_path)
    _record_turn(agent, "how many points does luka average", tool_calls=1)
    agent.last_question = "how many points does luka average"

    agent.reset_conversation()

    assert [m["role"] for m in agent.messages] == ["system"]
    assert agent.last_question is None
    assert agent._turn_starts == []


# ---------------- the fall-through agent's budget (#129) ----------------


def _always_tool_calls(calls: list[int], *, sleep: float = 0.0) -> Any:
    """A chat that never finishes: it asks for the same tool every turn, which
    is what a run that never converges looks like from the loop's side."""
    tool_call = Message.ToolCall(function=Message.ToolCall.Function(name="describe_table", arguments={"table_name": "players"}))

    def fake_chat(**kwargs: Any) -> ChatResponse:
        calls.append(1)
        if sleep:
            time.sleep(sleep)
        return ChatResponse(model="m", created_at="", done=True, message=Message(role="assistant", content="", tool_calls=[tool_call]))

    return fake_chat


def test_the_agent_gives_up_on_a_wall_clock_budget_rather_than_on_tool_calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """#129: an iteration cap does not bound the wait, because the cost is per
    model call - measured, 14 of 24 questions never finished and one ran past
    17 minutes. The budget is checked before each call, so the first one
    always runs and a run that has spent its budget stops there."""
    calls: list[int] = []
    monkeypatch.setattr(ollama, "chat", _always_tool_calls(calls, sleep=0.02))
    agent = _agent(tmp_path, fast_path=False, budget_seconds=0.01)
    answer = agent.ask("q")
    assert "gave up" in answer.text and "did not reach an answer within" in answer.text
    # Far short of the iteration cap: the budget stopped it, not the cap.
    assert 1 <= len(calls) < MAX_TOOL_ITERATIONS


def test_a_budget_of_zero_leaves_the_iteration_cap_in_charge(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """0 removes the bound, which is what a caller who wants the old behavior
    passes. The cap then ends the run, and says so in its own words."""
    calls: list[int] = []
    monkeypatch.setattr(ollama, "chat", _always_tool_calls(calls))
    answer = _agent(tmp_path, fast_path=False, budget_seconds=0).ask("q")
    assert len(calls) == MAX_TOOL_ITERATIONS
    assert f"it made {MAX_TOOL_ITERATIONS} tool calls without reaching one" in answer.text


def test_giving_up_names_what_the_fast_path_could_not_answer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ "Gave up after too many tool-call iterations" told a reader nothing
    about their own question. The reason the templates declined it says which
    part of the question has no answer here yet."""
    import duckdb

    from association.query.router import Route

    db_path = tmp_path / "gaveup.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE players (athlete_id VARCHAR, display_name VARCHAR)")
    con.execute("CREATE TABLE teams (team_id VARCHAR, display_name VARCHAR, abbreviation VARCHAR)")
    con.close()
    calls: list[int] = []
    monkeypatch.setattr(ollama, "chat", _always_tool_calls(calls))
    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="other", slots={}))
    agent = Agent("qwen2.5:7b", str(db_path), tmp_path / "out", history_dir=tmp_path / ".history", budget_seconds=0)
    answer = agent.ask("who had the most triple-doubles?")
    assert "No template answered it either: intent 'other' has no template yet" in answer.text
