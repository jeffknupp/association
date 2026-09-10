"""The HTTP API, and the page that talks to it.

Two endpoints do the work. ``POST /api/ask`` answers a question and returns the
whole :class:`association.query.answer.Answer`; ``GET /api/ask/stream`` answers
the same question and reports progress as it goes. The stream exists because
the two query paths differ by two orders of magnitude - a template answers in
1-2 seconds, while a fall-through to the tool-calling agent has been measured
at 113 seconds for its first inference alone. A spinner is a fine interface for
the first and a broken one for the second.

.. versionadded:: 2.0.0
"""

from __future__ import annotations

import json
import queue
import re
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ..query.answer import Answer
from ..query.toolbox import connect_read_only
from .runner import Answerer

# The page is one self-contained file, inlining its own CSS and JavaScript, for
# the same reason a rendered shot chart is: nothing to mount, nothing to get
# left out of a wheel, and no build step. There is exactly one static asset to
# survive packaging, and `association web` loads it on startup, so a missing one
# fails immediately rather than as a blank page.
INDEX_HTML: Path = Path(__file__).parent / "static" / "index.html"
"""The single-page app served at ``/``.

.. versionadded:: 2.0.0
"""


class AskRequest(BaseModel):
    """One question. No history: 2.0 answers every message independently.

    .. versionadded:: 2.0.0
    """

    question: str


class ArtifactResponse(BaseModel):
    """A file an answer wrote - a chart, today.

    .. versionadded:: 2.0.0
    """

    kind: str
    name: str


class TimingResponse(BaseModel):
    """Where the answer's wall time went.

    .. versionadded:: 2.0.0
    """

    total_seconds: float
    model_seconds: float
    model_calls: int
    tool_seconds: float
    tool_calls: int


class AnswerResponse(BaseModel):
    """An answered question.

    ``intent`` and ``data`` are null when ``answered_by`` is ``"agent"``: only a
    template produces them. A client must handle that rather than assume a
    shape.

    .. versionadded:: 2.0.0
    """

    question: str
    text: str
    answered_by: str
    intent: str | None
    data: dict[str, Any] | None
    artifacts: list[ArtifactResponse]
    timing: TimingResponse


class HealthResponse(BaseModel):
    """What this server is pointed at, and whether it can answer anything.

    Reported rather than assumed: the commonest way for this to be useless is
    to be running happily against the wrong warehouse.

    .. versionadded:: 2.0.0
    """

    warehouse: str
    warehouse_ready: bool
    output_dir: str
    seasons: dict[str, int] | None
    models: dict[str, str]
    ollama_ready: bool
    busy: bool


def as_response(answer: Answer) -> AnswerResponse:
    """The wire form of an answer.

    .. versionadded:: 2.0.0
    """
    return AnswerResponse(
        question=answer.question,
        text=answer.text,
        answered_by=answer.answered_by,
        intent=answer.intent,
        data=answer.data,
        artifacts=[ArtifactResponse(kind=a.kind, name=a.name) for a in answer.artifacts],
        timing=TimingResponse(
            total_seconds=answer.timing.total_seconds,
            model_seconds=answer.timing.model_seconds,
            model_calls=answer.timing.model_calls,
            tool_seconds=answer.timing.tool_seconds,
            tool_calls=answer.timing.tool_calls,
        ),
    )


def sse(event: str, payload: dict[str, Any]) -> str:
    """One server-sent event. Newlines inside the payload would end the event
    early, which is why it is JSON on a single line rather than raw text.

    .. versionadded:: 2.0.0
    """
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.html$")
"""What an artifact may be named to be served.

An allowlist, not a denylist of the tricks: it admits exactly the names the
renderers produce (``shotchart_stephen_curry_401811054.html``) and nothing that
could describe a path - no separator, no ``%``, so no percent-encoded one
either, and no leading dot. ``..`` cannot match it because a bare ``..`` has no
``.html`` suffix and ``../x.html`` contains a separator.

.. versionadded:: 2.0.0
"""


def artifact_path(out_dir: Path, name: str) -> Path | None:
    """The file to serve for ``name``, or None if there is not one.

    Two independent checks, because they stop different things. The name has to
    match :data:`ARTIFACT_NAME`, which rules out anything shaped like a path.
    Then the *resolved* file has to sit directly in the resolved output
    directory - which is what catches a symlink whose name is perfectly
    innocent and whose target is not. Neither check subsumes the other.

    .. versionadded:: 2.0.0
    """
    if not ARTIFACT_NAME.match(name):
        return None
    root = out_dir.resolve()
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        return None
    return path


def _warehouse_seasons(db_path: str) -> dict[str, int] | None:
    """First season, last season and game count, or None if the warehouse is
    not there or holds no games yet.

    Its own short-lived connection, deliberately: the Agent's connection is
    busy for as long as a question takes, and a health check that could block
    behind a 113-second answer would report exactly the wrong thing at exactly
    the wrong moment. Opened through :func:`association.query.toolbox.connect_read_only`
    like every other connection on the query side - this one only ever runs the
    fixed query below, but a second way to open the warehouse is a second place
    for the next one to be opened wrongly.
    """
    if not Path(db_path).exists():
        return None
    try:
        con = connect_read_only(db_path)
    except duckdb.Error:
        return None
    try:
        row = con.execute("SELECT min(season), max(season), count(*) FROM games").fetchone()
    except duckdb.Error:
        return None  # no games table yet - a warehouse that exists but was never loaded
    finally:
        con.close()
    if row is None or row[0] is None:
        return None
    return {"first": int(row[0]), "last": int(row[1]), "games": int(row[2])}


def create_app(answerer: Answerer, db_path: str, out_dir: Path, model: str, router_model: str) -> FastAPI:
    """Build the app around an already-constructed engine.

    The engine is passed in rather than built here so the tests can supply a
    stub: the suite is offline, and an Agent wants ollama and a warehouse.

    .. versionadded:: 2.0.0
    """
    app = FastAPI(title="association", description="Ask questions about the local NBA warehouse.", version="2.0.0")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """The app shell."""
        return FileResponse(INDEX_HTML, media_type="text/html")

    @app.get("/api/health")
    def health() -> HealthResponse:
        """What this server is pointed at, and whether it can answer."""
        return HealthResponse(
            warehouse=str(db_path),
            warehouse_ready=Path(db_path).exists(),
            output_dir=str(out_dir),
            seasons=_warehouse_seasons(db_path),
            models={"router": router_model, "agent": model},
            ollama_ready=answerer.ready,
            busy=answerer.busy,
        )

    @app.post("/api/ask")
    def ask(request: AskRequest) -> AnswerResponse:
        """Answer one question, waiting for any question ahead of it."""
        return as_response(answerer.ask(request.question, label=f"POST /api/ask {request.question!r}"))

    @app.get("/api/artifacts/{name}")
    def artifact(name: str) -> FileResponse:
        """One rendered chart, by the name an answer reported.

        Serves out of the same directory the CLI writes to, so a chart made at
        the terminal is viewable here and vice versa - and so anything else
        that happens to be sitting in that directory is reachable too, which is
        why `artifact_path` is strict about what counts as a name.
        """
        path = artifact_path(out_dir, name)
        if path is None:
            # 404 rather than 403 for a rejected name: a different status for
            # "not allowed" than for "not there" would answer the question the
            # probing was asking.
            raise HTTPException(status_code=404, detail="no such artifact")
        return FileResponse(path, media_type="text/html")

    @app.get("/api/ask/stream")
    def ask_stream(question: str) -> StreamingResponse:
        """The same answer, with the trace as it happens.

        A `progress` event carries one trace line verbatim rather than a parsed
        `routed`/`tool` event. The engine's trace lines are prose, and turning
        prose back into structure is the exact move this codebase removed from
        the chart renderers; the structured facts a client acts on - which path
        answered, the intent, the timing - are all on the final `answer` event.
        """
        return StreamingResponse(
            _stream(answerer, question),
            media_type="text/event-stream",
            # nginx and friends buffer a streaming response into uselessness by
            # default, which turns progress reporting back into a long silence.
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def _stream(answerer: Answerer, question: str) -> Iterator[str]:
    """Answer in a worker thread, yielding events as the trace arrives.

    A thread and a queue rather than async, because everything below this is
    blocking: ollama's client and DuckDB both are, and neither takes a
    cancellation token. See the plan's Risks - closing the tab does not stop
    the inference already running, and this does not pretend otherwise.
    """
    events: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()

    if answerer.busy:
        yield sse("queued", {"question": question})

    def work() -> None:
        """Answer, in a thread, putting everything on the queue."""
        try:
            answer = answerer.ask(question, label=f"GET /api/ask/stream {question!r}", trace=lambda line: events.put(("progress", {"line": line.strip()})))
            events.put(("answer", as_response(answer).model_dump()))
        except Exception as exc:
            # Reported to the browser rather than raised: the response has
            # already started, so raising here would truncate the stream with
            # no explanation. The history file has the traceback.
            events.put(("error", {"message": f"{type(exc).__name__}: {exc}"}))
        finally:
            events.put(("done", {}))

    threading.Thread(target=work, daemon=True, name="association-answer").start()
    while True:
        event, payload = events.get()
        if event == "done":
            return
        yield sse(event, payload)
