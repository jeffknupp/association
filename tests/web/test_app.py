"""Tests for the HTTP API behind `association web`.

Everything here runs against a stub answerer. The suite is offline and needs no
ollama, which is a guarantee the web layer has to hold as much as any other -
so the thing under test is the API, never the model.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from association.query.answer import Answer, Artifact, Timing
from association.web.app import create_app


class StubAnswerer:
    """An Answerer that records what it was asked and answers instantly."""

    def __init__(self, answer: Answer | None = None) -> None:
        self.answer = answer or _answer("the answer")
        self.asked: list[tuple[str, str]] = []
        self.trace_lines: list[str] = ["  -> (router) intent='leaderboard' slots={}", "  [timing] model inference #1: 1.30s"]
        self.busy = False
        # Never probed for real: the API layer asks the engine, so nothing here
        # reaches ollama and the offline guarantee holds by construction.
        self.ready = True

    def ask(self, question: str, label: str, trace: Callable[[str], None] = lambda line: None) -> Answer:
        self.asked.append((question, label))
        for line in self.trace_lines:
            trace(line)
        return self.answer


def _answer(text: str, **kwargs: Any) -> Answer:
    fields: dict[str, Any] = {
        "question": "q",
        "text": text,
        "answered_by": "fast",
        "timing": Timing(1.4, 1.3, 1, 0.1, 1),
        "intent": "leaderboard",
        "data": {"leaders": ["Jokic"]},
    }
    fields.update(kwargs)
    return Answer(**fields)


def _client(answerer: Any, tmp_path: Path) -> TestClient:
    app = create_app(answerer, db_path=str(tmp_path / "nba.duckdb"), out_dir=tmp_path / "out", model="qwen2.5:7b", router_model="qwen2.5:3b")
    return TestClient(app)


def test_ask_returns_the_whole_answer_not_just_the_text(tmp_path: Path) -> None:
    """The reason Phase 0 existed: a client needs the intent and the data, not
    a sentence it would have to parse."""
    client = _client(StubAnswerer(), tmp_path)
    body = client.post("/api/ask", json={"question": "who leads the league in assists?"}).json()

    assert body["text"] == "the answer"
    assert body["answered_by"] == "fast"
    assert body["intent"] == "leaderboard"
    assert body["data"] == {"leaders": ["Jokic"]}
    assert body["timing"]["total_seconds"] == 1.4


def test_an_agent_answer_reports_no_intent_rather_than_an_empty_one(tmp_path: Path) -> None:
    client = _client(StubAnswerer(_answer("agent answer", answered_by="agent", intent=None, data=None)), tmp_path)
    body = client.post("/api/ask", json={"question": "something odd"}).json()

    assert body["answered_by"] == "agent"
    assert body["intent"] is None
    assert body["data"] is None


def test_an_artifact_is_reported_by_name_not_by_path(tmp_path: Path) -> None:
    """A browser addresses a chart by name. The server's absolute path is its
    own business, and leaking it would tell a page about a filesystem it has no
    way to read."""
    drawn = Artifact("shot_chart", tmp_path / "deep" / "shotchart_curry.html")
    client = _client(StubAnswerer(_answer("Rendered.", artifacts=[drawn])), tmp_path)
    body = client.post("/api/ask", json={"question": "chart curry"}).json()

    assert body["artifacts"] == [{"kind": "shot_chart", "name": "shotchart_curry.html"}]
    assert str(tmp_path) not in json.dumps(body)


def test_the_question_reaches_the_engine_with_a_label_naming_the_request(tmp_path: Path) -> None:
    """The history file's `command:` line. A server that passed nothing would
    write a run of history entries with no way to tell them apart."""
    answerer = StubAnswerer()
    _client(answerer, tmp_path).post("/api/ask", json={"question": "who leads the league in assists?"})

    question, label = answerer.asked[0]
    assert question == "who leads the league in assists?"
    assert "api/ask" in label and "assists" in label


def test_the_stream_reports_progress_and_then_the_answer(tmp_path: Path) -> None:
    """A fall-through takes minutes. The trace is what makes that legible
    rather than indistinguishable from a hang."""
    with _client(StubAnswerer(), tmp_path) as client:
        with client.stream("GET", "/api/ask/stream", params={"question": "q"}) as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            events = _events(response.iter_lines())

    assert [name for name, _ in events] == ["progress", "progress", "answer"]
    assert events[0][1]["line"].startswith("-> (router)")
    assert events[-1][1]["text"] == "the answer"
    assert events[-1][1]["answered_by"] == "fast"


def test_the_stream_reports_an_engine_failure_rather_than_truncating(tmp_path: Path) -> None:
    """The response has already started by the time an answer fails, so raising
    would close the stream with no explanation at all."""

    class Failing(StubAnswerer):
        def ask(self, question: str, label: str, trace: Callable[[str], None] = lambda line: None) -> Answer:
            raise RuntimeError("ollama is not running")

    with _client(Failing(), tmp_path) as client:
        with client.stream("GET", "/api/ask/stream", params={"question": "q"}) as response:
            events = _events(response.iter_lines())

    assert [name for name, _ in events] == ["error"]
    assert events[0][1]["message"] == "RuntimeError: ollama is not running"


def test_a_waiting_request_is_told_it_is_waiting(tmp_path: Path) -> None:
    answerer = StubAnswerer()
    answerer.busy = True
    with _client(answerer, tmp_path) as client:
        with client.stream("GET", "/api/ask/stream", params={"question": "q"}) as response:
            events = _events(response.iter_lines())

    assert events[0][0] == "queued"


def test_health_reports_the_warehouse_it_is_actually_pointed_at(tmp_path: Path) -> None:
    """The commonest way for this to be useless is to be running happily
    against the wrong file."""
    client = _client(StubAnswerer(), tmp_path)
    body = client.get("/api/health").json()

    assert body["warehouse"] == str(tmp_path / "nba.duckdb")
    assert body["warehouse_ready"] is False  # nothing was created
    assert body["seasons"] is None
    assert body["models"] == {"router": "qwen2.5:3b", "agent": "qwen2.5:7b"}
    assert body["ollama_ready"] is True


def test_health_reports_an_unreachable_model_backend(tmp_path: Path) -> None:
    """Worth reporting rather than discovering on the first question, which is
    a 60-second wait before an error."""
    answerer = StubAnswerer()
    answerer.ready = False
    assert _client(answerer, tmp_path).get("/api/health").json()["ollama_ready"] is False


def test_health_reports_the_seasons_a_real_warehouse_holds(tmp_path: Path) -> None:
    import duckdb

    db_path = tmp_path / "nba.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE games (event_id VARCHAR, season INTEGER)")
    con.execute("INSERT INTO games VALUES ('1', 2024), ('2', 2026)")
    con.close()

    body = _client(StubAnswerer(), tmp_path).get("/api/health").json()
    assert body["warehouse_ready"] is True
    assert body["seasons"] == {"first": 2024, "last": 2026, "games": 2}


def test_health_survives_a_warehouse_with_no_games_table(tmp_path: Path) -> None:
    """A database that exists but was never loaded is an ordinary state - the
    file is created before the first `data load` finishes."""
    import duckdb

    duckdb.connect(str(tmp_path / "nba.duckdb")).close()
    body = _client(StubAnswerer(), tmp_path).get("/api/health").json()

    assert body["warehouse_ready"] is True
    assert body["seasons"] is None


def test_the_page_is_served_and_is_self_contained(tmp_path: Path) -> None:
    """One file, no build step, nothing else to survive packaging. An external
    stylesheet or script would be a second asset to lose in a wheel."""
    body = _client(StubAnswerer(), tmp_path).get("/").text

    assert "<title>association</title>" in body
    assert "/api/ask/stream" in body
    assert 'src="' not in body and 'rel="stylesheet"' not in body


def test_two_questions_at_once_are_answered_one_at_a_time(tmp_path: Path) -> None:
    """The measured reason for the lock: ollama keeps one KV cache slot per
    model, so two questions in flight evict each other's prefix and both come
    back slow. Same shape as the fetch path's concurrency tests."""
    from association.web.runner import AgentRunner

    overlaps: list[int] = []
    live = 0
    guard = threading.Lock()

    class Blocking:
        def ask(self, question: str, label: str = "") -> Answer:
            nonlocal live
            with guard:
                live += 1
                overlaps.append(live)
            time.sleep(0.05)
            with guard:
                live -= 1
            return _answer("done")

        trace: Any = None

        def reset_conversation(self) -> None:
            pass

    runner = AgentRunner(Blocking())  # type: ignore[arg-type]  # only .ask, .trace and .reset_conversation are touched
    threads = [threading.Thread(target=lambda: runner.ask("q", label="t")) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert overlaps == [1, 1, 1, 1], overlaps


def _events(lines: Any) -> list[tuple[str, dict[str, Any]]]:
    """Parse an SSE body into (event, payload) pairs."""
    parsed, name = [], ""
    for line in lines:
        if line.startswith("event: "):
            name = line[len("event: ") :]
        elif line.startswith("data: "):
            parsed.append((name, json.loads(line[len("data: ") :])))
    return parsed


@pytest.mark.parametrize("payload", [{}, {"question": None}])
def test_a_malformed_ask_is_rejected_rather_than_asked(payload: dict[str, Any], tmp_path: Path) -> None:
    answerer = StubAnswerer()
    assert _client(answerer, tmp_path).post("/api/ask", json=payload).status_code == 422
    assert answerer.asked == []


def test_every_request_gets_its_own_conversation(tmp_path: Path) -> None:
    """One Agent reused across requests shared ONE history with every browser
    that connected - and `last_question` with it, which the router reads as
    `previous_question`, so a follow-up was resolved against whatever a
    stranger had asked. The docs already said each message is a new question;
    this is what makes that true."""
    from association.web.runner import AgentRunner

    class Recording:
        """Just enough Agent to hold a conversation and be asked to drop it."""

        def __init__(self) -> None:
            self.messages: list[dict[str, Any]] = [{"role": "system", "content": "x"}]
            self.last_question: str | None = None
            self.trace: Any = None

        def reset_conversation(self) -> None:
            self.messages = [{"role": "system", "content": "x"}]
            self.last_question = None

        def ask(self, question: str, label: str = "") -> Answer:
            self.messages.append({"role": "user", "content": question})
            self.last_question = question
            return _answer("done")

    agent = Recording()
    runner = AgentRunner(agent)  # type: ignore[arg-type]  # only the four attributes above are touched

    runner.ask("how many points does luka average", label="a")
    runner.ask("what about jokic", label="b")

    # The second question saw neither the first question nor its answer.
    assert [m["content"] for m in agent.messages] == ["x", "what about jokic"]
    assert agent.last_question == "what about jokic"
