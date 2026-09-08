"""Regression tests for the SQL-as-prose safety net, the thinking-model
context-growth fix in Agent.ask, and the router fast path's fall-through."""

from pathlib import Path
from typing import Any

import ollama
import pytest
from ollama import ChatResponse, Message

from association.query.agent import MAX_AUTO_SQL_RECOVERIES, MAX_ERROR_RECOVERIES, Agent, _extract_unrun_sql


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
    result = think_agent.ask("some question")

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
    result = think_agent.ask("some question")

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
    result = think_agent.ask("some question")

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
    result = think_agent.ask("some question")

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
    result = agent.ask("some question")

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


def test_fast_path_is_skipped_entirely_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called = False

    def fake_route(*args: Any, **kwargs: Any) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("association.query.agent.route", fake_route)
    monkeypatch.setattr(ollama, "chat", lambda **kw: ChatResponse(model="m", created_at="", done=True, message=Message(role="assistant", content="agent answer")))
    assert _agent(tmp_path, fast_path=False).ask("q") == "agent answer"
    assert not called


def test_unported_intent_falls_through_to_the_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A shape with no template yet must reach the old path unchanged - that is
    what makes porting one shape at a time safe."""
    from association.query.router import Route

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="other", slots={}))
    monkeypatch.setattr(ollama, "chat", lambda **kw: ChatResponse(model="m", created_at="", done=True, message=Message(role="assistant", content="agent answer")))
    assert _agent(tmp_path).ask("who had the most triple-doubles?") == "agent answer"


def test_router_failure_falls_through_rather_than_erroring(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: None)
    monkeypatch.setattr(ollama, "chat", lambda **kw: ChatResponse(model="m", created_at="", done=True, message=Message(role="assistant", content="agent answer")))
    assert _agent(tmp_path).ask("q") == "agent answer"


def test_fast_path_answer_is_recorded_in_conversation_for_later_followups(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The tool loop never runs on the fast path, but a follow-up that DOES
    fall through still needs to see what was already asked and answered."""
    from association.query.router import Route
    from association.query.templates import TemplateResult

    monkeypatch.setattr("association.query.agent.route", lambda *a, **k: Route(intent="threshold_count", slots={"stat": "points", "threshold": 30}))
    monkeypatch.setattr("association.query.agent.TEMPLATES", {"threshold_count": lambda con, slots: TemplateResult(data={"leaders": []}, answer="template answer")})
    # No ollama.chat stub: reaching one would itself be the bug. The router is
    # stubbed out above, and a template answers without a model call.
    agent = _agent(tmp_path)
    assert agent.ask("most 30+ point games?") == "template answer"
    assert agent.last_question == "most 30+ point games?"
    assert [m["content"] for m in agent.messages[1:]] == ["most 30+ point games?", "template answer"]
