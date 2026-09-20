"""Tests for the HTTP API behind `association web`.

Everything here runs against a stub answerer. The suite is offline and needs no
ollama, which is a guarantee the web layer has to hold as much as any other -
so the thing under test is the API, never the model.
"""

from __future__ import annotations

import json
import subprocess
import sys
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
    with _client(StubAnswerer(), tmp_path) as client, client.stream("GET", "/api/ask/stream", params={"question": "q"}) as response:
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

    with _client(Failing(), tmp_path) as client, client.stream("GET", "/api/ask/stream", params={"question": "q"}) as response:
        events = _events(response.iter_lines())

    assert [name for name, _ in events] == ["error"]
    assert events[0][1]["message"] == "RuntimeError: ollama is not running"


def test_a_waiting_request_is_told_it_is_waiting(tmp_path: Path) -> None:
    answerer = StubAnswerer()
    answerer.busy = True
    with _client(answerer, tmp_path) as client, client.stream("GET", "/api/ask/stream", params={"question": "q"}) as response:
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
    """A warehouse loaded before `real_games` existed holds `games` alone, and
    the health line falls back to it rather than reporting the warehouse
    empty over one missing view."""
    import duckdb

    db_path = tmp_path / "nba.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("CREATE TABLE games (event_id VARCHAR, season INTEGER)")
    con.execute("INSERT INTO games VALUES ('1', 2024), ('2', 2026)")
    con.close()

    body = _client(StubAnswerer(), tmp_path).get("/api/health").json()
    assert body["warehouse_ready"] is True
    assert body["seasons"] == {"first": 2024, "last": 2026, "games": 2}


def test_health_counts_real_games_rather_than_every_row_in_games(tmp_path: Path) -> None:
    """`games` carries rows that are not games - 151 of 43,504 in the real
    warehouse - so counting it said 43,504 where 43,353 were played. Every
    team template already reads `real_games`; this was the last reader that
    did not."""
    import duckdb

    con = duckdb.connect(str(tmp_path / "nba.duckdb"))
    con.execute("CREATE TABLE games (event_id VARCHAR, season INTEGER)")
    # The third row is the shape real_games drops: a placeholder that is not a
    # game. Counting it is the bug.
    con.execute("INSERT INTO games VALUES ('1', 2024), ('2', 2026), ('3', 2026)")
    con.execute("CREATE VIEW real_games AS SELECT * FROM games WHERE event_id <> '3'")
    con.close()

    body = _client(StubAnswerer(), tmp_path).get("/api/health").json()
    assert body["seasons"] == {"first": 2024, "last": 2026, "games": 2}


def test_health_survives_a_warehouse_with_no_games_table(tmp_path: Path) -> None:
    """A database that exists but was never loaded is an ordinary state - the
    file is created before the first `data load` finishes."""
    import duckdb

    duckdb.connect(str(tmp_path / "nba.duckdb")).close()
    body = _client(StubAnswerer(), tmp_path).get("/api/health").json()

    assert body["warehouse_ready"] is True
    assert body["seasons"] is None


def _build_coverage_warehouse(db_path: Path, seasons: dict[str, list[int]], *, real_games_excludes: tuple[int, ...] = ()) -> None:
    """One table per key in ``seasons``, each holding one row per season
    listed - enough for `SELECT max(season)` to answer without needing real
    box scores. ``real_games_excludes`` adds a `real_games` view over `games`
    that drops those seasons, the same shape `_game_span` reads a warehouse
    built before that view existed does not have."""
    import duckdb

    con = duckdb.connect(str(db_path))
    for table, years in seasons.items():
        con.execute(f"CREATE TABLE {table} (event_id VARCHAR, season INTEGER)")
        for i, year in enumerate(years):
            con.execute(f"INSERT INTO {table} VALUES (?, ?)", [str(i), year])
    if "games" in seasons and real_games_excludes:
        excluded = ", ".join(str(year) for year in real_games_excludes)
        con.execute(f"CREATE VIEW real_games AS SELECT * FROM games WHERE season NOT IN ({excluded})")
    con.close()


def test_coverage_reports_no_tiers_for_a_warehouse_that_is_not_there(tmp_path: Path) -> None:
    """#71: nothing to say about coverage before a warehouse exists at all -
    the same reasoning `/api/health`'s `seasons` field already follows."""
    body = _client(StubAnswerer(), tmp_path).get("/api/coverage").json()
    assert body["tiers"] == []


def test_coverage_tier_first_season_is_the_narrowest_of_its_own_and_earlier_tiers(tmp_path: Path) -> None:
    """The '+ play-by-play' tier's floor is 2002, not 1994, because it also
    carries the box score tables and a question touching both is only as
    answerable as the narrower one - `association.nba.coverage.unavailable`'s
    own reasoning, read from `COVERAGE` rather than restated."""
    db_path = tmp_path / "nba.duckdb"
    _build_coverage_warehouse(
        db_path,
        {
            "games": [1994, 2026],
            "player_box_stats": [1994, 2026],
            "team_box_stats": [1994, 2026],
            "plays": [2002, 2026],
            "shot_chart": [2002, 2026],
        },
    )
    tiers = {t["name"]: t for t in _client(StubAnswerer(), tmp_path).get("/api/coverage").json()["tiers"]}

    assert tiers["box score"]["first_season"] == 1994
    assert tiers["+ play-by-play"]["first_season"] == 2002


def test_coverage_tier_tables_are_cumulative(tmp_path: Path) -> None:
    """`CoverageResponse`'s own docstring promise: each tier's `tables` include
    every earlier tier's, not just what it adds - a question that touches
    play-by-play can touch a box score too, so the reader needs the whole list
    to know what a tier's floor and range are actually bounded by."""
    db_path = tmp_path / "nba.duckdb"
    _build_coverage_warehouse(
        db_path,
        {
            "games": [1994, 2026],
            "player_box_stats": [1994, 2026],
            "team_box_stats": [1994, 2026],
            "plays": [2002, 2026],
            "shot_chart": [2002, 2026],
            "net_points_player": [2019, 2026],
            "net_points_player_game": [2019, 2026],
            "net_points_player_fingerprint": [2019, 2026],
            "net_points_team_game": [2019, 2026],
            "net_points_player_game_fingerprint": [2019, 2026],
        },
    )
    tiers = {t["name"]: t for t in _client(StubAnswerer(), tmp_path).get("/api/coverage").json()["tiers"]}

    assert set(tiers["box score"]["tables"]) == {"games", "player_box_stats", "team_box_stats"}
    assert set(tiers["+ play-by-play"]["tables"]) == {"games", "player_box_stats", "team_box_stats", "plays", "shot_chart"}
    assert set(tiers["+ NetPoints"]["tables"]) == {
        "games",
        "player_box_stats",
        "team_box_stats",
        "plays",
        "shot_chart",
        "net_points_player",
        "net_points_player_game",
        "net_points_player_fingerprint",
        "net_points_team_game",
        "net_points_player_game_fingerprint",
    }
    # 2002 and 2003 are declared partial for `plays`/`shot_chart`, which the
    # "+ NetPoints" tier also carries in its cumulative `tables` - but they are
    # not ITS OWN table, so its own `partial_seasons` must not repeat them.
    assert tiers["+ NetPoints"]["partial_seasons"] == []


def test_coverage_tier_last_season_follows_its_own_narrowest_table(tmp_path: Path) -> None:
    """A tier's range stops where its OWN table stops, even when an earlier
    tier's tables reach further - the same "narrowest wins" rule as the floor,
    applied to how far the data currently reaches rather than to where it is
    permanently allowed to start."""
    db_path = tmp_path / "nba.duckdb"
    _build_coverage_warehouse(
        db_path,
        {
            "games": [1994, 2026],
            "player_box_stats": [1994, 2026],
            "team_box_stats": [1994, 2026],
            "plays": [2002, 2020],  # this pull never fetched play-by-play past 2020
            "shot_chart": [2002, 2026],
        },
    )
    tiers = {t["name"]: t for t in _client(StubAnswerer(), tmp_path).get("/api/coverage").json()["tiers"]}

    assert tiers["box score"]["last_season"] == 2026
    assert tiers["+ play-by-play"]["last_season"] == 2020


def test_coverage_still_names_a_tiers_floor_when_none_of_its_tables_are_loaded(tmp_path: Path) -> None:
    """A floor is a permanent fact about what ESPN publishes, not about what
    this warehouse has pulled - so the NetPoints tier still says 2019 even
    with no NetPoints table loaded at all, while its `last_season` says None
    rather than guessing at data that was never fetched."""
    db_path = tmp_path / "nba.duckdb"
    _build_coverage_warehouse(
        db_path,
        {
            "games": [1994, 2026],
            "player_box_stats": [1994, 2026],
            "team_box_stats": [1994, 2026],
            "plays": [2002, 2026],
            "shot_chart": [2002, 2026],
        },
    )
    tiers = {t["name"]: t for t in _client(StubAnswerer(), tmp_path).get("/api/coverage").json()["tiers"]}

    assert tiers["+ NetPoints"]["first_season"] == 2019
    assert tiers["+ NetPoints"]["last_season"] is None


def test_coverage_surfaces_the_declared_partial_seasons_for_the_tier_that_adds_the_table(tmp_path: Path) -> None:
    """2002's play-by-play and 2002-2003's shot chart are declared partial in
    `COVERAGE`; the endpoint reads that declaration rather than recomputing it
    from row counts, and names which table each note is about."""
    db_path = tmp_path / "nba.duckdb"
    _build_coverage_warehouse(
        db_path,
        {
            "games": [1994, 2026],
            "player_box_stats": [1994, 2026],
            "team_box_stats": [1994, 2026],
            "plays": [2002, 2026],
            "shot_chart": [2002, 2026],
        },
    )
    tier = next(t for t in _client(StubAnswerer(), tmp_path).get("/api/coverage").json()["tiers"] if t["name"] == "+ play-by-play")
    partial = {(p["season"], p["table"]) for p in tier["partial_seasons"]}

    assert (2002, "plays") in partial
    assert (2002, "shot_chart") in partial
    assert (2003, "shot_chart") in partial
    # The box score tier's own tables carry no declared partial season, so
    # nothing here is misattributed to them.
    assert all(table in ("plays", "shot_chart") for _, table in partial)


def test_coverage_does_not_surface_a_partial_season_beyond_what_is_loaded(tmp_path: Path) -> None:
    """`COVERAGE` declares both 2002 and 2003 partial for `shot_chart`, but a
    warehouse whose `shot_chart` stops at 2002 has not loaded 2003 at all - so
    the endpoint must not repeat a partial note for a season outside the
    tier's own `last_season`, the same way it must not repeat one below
    `first_season`."""
    db_path = tmp_path / "nba.duckdb"
    _build_coverage_warehouse(
        db_path,
        {
            "games": [1994, 2026],
            "player_box_stats": [1994, 2026],
            "team_box_stats": [1994, 2026],
            "plays": [2002],
            "shot_chart": [2002],  # this pull never reached 2003
        },
    )
    tier = next(t for t in _client(StubAnswerer(), tmp_path).get("/api/coverage").json()["tiers"] if t["name"] == "+ play-by-play")
    partial = {(p["season"], p["table"]) for p in tier["partial_seasons"]}

    assert tier["last_season"] == 2002
    assert (2002, "shot_chart") in partial
    assert (2003, "shot_chart") not in partial


def test_coverage_surfaces_1993_as_a_phantom_rather_than_offering_it(tmp_path: Path) -> None:
    """1993 is a full, healthy-looking copy of 1994's rows in `games`,
    `player_box_stats` and `team_box_stats` - `COVERAGE` lists it as a phantom
    rather than a real season, and the box score tier's `first_season` (1994)
    already excludes it from the range. `phantom_seasons` says why."""
    db_path = tmp_path / "nba.duckdb"
    _build_coverage_warehouse(
        db_path,
        {
            "games": [1993, 1994, 2026],
            "player_box_stats": [1993, 1994, 2026],
            "team_box_stats": [1993, 1994, 2026],
        },
    )
    tiers = {t["name"]: t for t in _client(StubAnswerer(), tmp_path).get("/api/coverage").json()["tiers"]}

    assert tiers["box score"]["first_season"] == 1994  # 1993 is not offered as a season on its own
    assert tiers["box score"]["phantom_seasons"] == [1993]
    # `games`, `player_box_stats` and `team_box_stats` are also part of the
    # later tiers' cumulative `tables`, but 1993 is not THEIR own phantom, so
    # a later tier must not repeat the box score tier's note.
    assert tiers["+ play-by-play"]["phantom_seasons"] == []
    assert tiers["+ NetPoints"]["phantom_seasons"] == []


def test_coverage_uses_real_games_where_the_warehouse_has_it(tmp_path: Path) -> None:
    """The same fault `test_health_counts_real_games_rather_than_every_row_in_games`
    guards: a placeholder row in `games` for a season with no real game would
    otherwise report that season as covered.

    `player_box_stats` and `team_box_stats` also reach 2027 here, deliberately
    - if they stopped at 2026 the tier's own "narrowest of the tier's tables"
    rule would report 2026 regardless of whether `games` or `real_games` was
    read, and the assertion below would pass even with the fallback deleted."""
    db_path = tmp_path / "nba.duckdb"
    _build_coverage_warehouse(
        db_path,
        {
            "games": [1994, 2026, 2027],  # 2027 is the placeholder real_games drops
            "player_box_stats": [1994, 2027],
            "team_box_stats": [1994, 2027],
        },
        real_games_excludes=(2027,),
    )
    tier = next(t for t in _client(StubAnswerer(), tmp_path).get("/api/coverage").json()["tiers"] if t["name"] == "box score")

    assert tier["last_season"] == 2026


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


def test_importing_the_api_layer_loads_no_model_client() -> None:
    """The runtime half of the "ollama stays out of the API layer" import
    contract. import-linter sees imports statically and has to be told that
    ``AgentRunner.ready`` imports ollama only when it is called; this checks the
    thing that matters in a fresh interpreter - that nothing on the way to
    ``web.app`` loads it. It once did, through a runtime import of ``Agent`` that
    only an annotation needed."""
    probe = "import sys, association.web.app; print('ollama' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "False"


def test_a_disabled_fallthrough_is_a_501_that_says_why(tmp_path: Path) -> None:
    """Development only (--disable-fallthrough): a question no template answers
    is refused with the reason rather than spending minutes on the agent - on
    the plain endpoint as a status, on the stream as its error event."""
    from association.query.answer import FallthroughDisabled

    class Refusing(StubAnswerer):
        def ask(self, question: str, label: str, trace: Callable[[str], None] = lambda line: None) -> Answer:
            raise FallthroughDisabled("no template answered this question and fall-through to the agent is disabled: intent 'other' has no template yet")

    with _client(Refusing(), tmp_path) as client:
        response = client.post("/api/ask", json={"question": "q"})
        assert response.status_code == 501 and "no template yet" in response.json()["detail"]
        with client.stream("GET", "/api/ask/stream", params={"question": "q"}) as stream:
            events = _events(stream.iter_lines())
    assert [name for name, _ in events] == ["error"] and "no template yet" in events[0][1]["message"]
