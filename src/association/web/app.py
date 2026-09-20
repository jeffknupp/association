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

from ..nba.coverage import COVERAGE
from ..query.answer import Answer, FallthroughDisabled
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


class PartialSeasonResponse(BaseModel):
    """One season a tier's range includes but does not fully cover.

    ``table`` names which table the note describes: two tables in one tier can
    be partial for unrelated reasons (2002's play-by-play is half a year,
    2003's shot chart is missing locations for about 200 games), and a note
    naming one while the season sits under the other's problem is the
    false-cause answer :mod:`association.nba.coverage` exists to stop.

    .. versionadded:: 4.1.0
    """

    season: int
    table: str
    note: str


class TierResponse(BaseModel):
    """What one coverage tier answers, and where it stops being whole.

    ``first_season`` and ``last_season`` bound the range this tier can be
    asked about at all; a season inside that range but named in
    ``partial_seasons`` is answerable with a caveat, not silently whole. A
    season below ``first_season`` - 1993 among them, ESPN's phantom copy of
    1994 - is not offered at all, which is why it is surfaced separately in
    ``phantom_seasons`` rather than folded into the range.

    .. versionadded:: 4.1.0
    """

    name: str
    tables: list[str]
    first_season: int | None
    last_season: int | None
    partial_seasons: list[PartialSeasonResponse]
    phantom_seasons: list[int]


class CoverageResponse(BaseModel):
    """What the warehouse actually holds, grouped into the three tiers a
    question can land in: box score, box score plus play-by-play, and both of
    those plus NetPoints.

    Built from :data:`association.nba.coverage.COVERAGE` - the same enforced
    floors :func:`association.nba.coverage.unavailable` refuses a question
    against - rather than recomputed from row counts, so this page and a
    template's own refusal cannot say two different things about the same
    season. ``tiers[i].tables`` is cumulative: the play-by-play tier's tables
    include the box score tier's, because a question that touches
    play-by-play can touch box scores too, and its floor is the narrowest
    among all of them.

    .. versionadded:: 4.1.0
    """

    tiers: list[TierResponse]


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


def _game_span(con: duckdb.DuckDBPyConnection) -> tuple[Any, ...] | None:
    """First season, last season and game count, counted from ``real_games``
    where the warehouse has it and ``games`` otherwise.

    ``games`` carries rows that are not games - 151 of its 43,504 today: 134
    placeholders, 23 team-slots naming an id no franchise has, phantom rows and
    a duplicate (``DATA.md``, "``games`` carries placeholder, duplicate and
    phantom rows"). Counting them made the page say 43,504 where 43,353 were
    played. Every team template already reads ``real_games``; this is the last
    reader that did not.

    The fallback is not defensive noise: ``real_games`` is built at load time,
    so a warehouse loaded before it existed has ``games`` alone, and a health
    check is exactly the wrong place to report a warehouse as empty because one
    view is missing. Asked of the catalog rather than by catching an error per
    table, which is the same question ``conditions.box_source`` asks about the
    filled box - and it keeps a real failure (a corrupt file, a lock) raising
    where the caller already handles it, instead of being swallowed as "that
    table is absent".
    """
    present = {row[0] for row in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    table = "real_games" if "real_games" in present else "games"
    return con.execute(f"SELECT min(season), max(season), count(*) FROM {table}").fetchone()


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
        row = _game_span(con)
    except duckdb.Error:
        return None  # no games table yet - a warehouse that exists but was never loaded
    finally:
        con.close()
    if row is None or row[0] is None:
        return None
    return {"first": int(row[0]), "last": int(row[1]), "games": int(row[2])}


# The three tiers #71 asks for, each naming only the tables IT adds - "tables"
# in the wire response is the cumulative list, built by summing these in
# order, because a question that reaches play-by-play can touch a box score
# too and its floor is the narrowest of every table involved. The tables
# named here are exactly #71's three bullets: box score is `games`,
# `player_box_stats`, `team_box_stats`; play-by-play adds `plays` and
# `shot_chart`; NetPoints adds its five tables.
_COVERAGE_TIERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("box score", ("games", "player_box_stats", "team_box_stats")),
    ("+ play-by-play", ("plays", "shot_chart")),
    (
        "+ NetPoints",
        ("net_points_player", "net_points_player_game", "net_points_player_fingerprint", "net_points_team_game", "net_points_player_game_fingerprint"),
    ),
)


def _cumulative_tier_tables(upto: int) -> tuple[str, ...]:
    """Every table this tier and each one before it names, in order."""
    tables: list[str] = []
    for _, own in _COVERAGE_TIERS[: upto + 1]:
        tables.extend(own)
    return tuple(tables)


def _tier_first_season(tables: tuple[str, ...]) -> int | None:
    """The narrowest declared floor among ``tables`` - the same reasoning
    :func:`association.nba.coverage.unavailable` uses, read from the enforced
    ``COVERAGE`` table rather than restated here. A table `COVERAGE` does not
    list (there is none among the ones this module names) is skipped rather
    than assumed to have no floor."""
    floors = [COVERAGE[t].floor().season for t in tables if t in COVERAGE]
    return max(floors) if floors else None


def _tier_last_season(con: duckdb.DuckDBPyConnection, present: set[str], tables: tuple[str, ...]) -> int | None:
    """How far this tier's data actually reaches - the one figure `COVERAGE`
    does not carry, since a floor is a permanent fact about ESPN and this is a
    fact about what is currently loaded. Reads ``real_games`` in place of
    `games` where the warehouse has it, for the same reason :func:`_game_span`
    does. None if any of ``tables`` is missing: a tier whose own table is not
    loaded cannot say where it stops."""
    lasts = []
    for table in tables:
        source = "real_games" if table == "games" and "real_games" in present else table
        if source not in present:
            return None
        row = con.execute(f"SELECT max(season) FROM {source}").fetchone()
        if row is None or row[0] is None:
            return None
        lasts.append(int(row[0]))
    return min(lasts) if lasts else None


def _tier_partial_seasons(own_tables: tuple[str, ...], first: int | None, last: int | None) -> list[PartialSeasonResponse]:
    """The seasons within this tier's own range that `COVERAGE` marks partial
    for one of ``own_tables`` - only the tables this tier itself adds, so a
    season already flagged partial by an earlier tier is not repeated here."""
    if first is None or last is None:
        return []
    notes = [PartialSeasonResponse(season=season, table=table, note=note) for table in own_tables if table in COVERAGE for season, note in COVERAGE[table].partial.items() if first <= season <= last]
    return sorted(notes, key=lambda p: (p.season, p.table))


def _tier_phantom_seasons(own_tables: tuple[str, ...]) -> list[int]:
    """The seasons `COVERAGE` marks as a phantom copy of another season, for
    one of ``own_tables`` - 1993 among the box score tables, whose rows
    duplicate 1994's. Already excluded from the tier's range by its
    ``first_season``; listed here so the page can say why rather than just
    skip it silently."""
    seasons = {season for table in own_tables if table in COVERAGE for season in COVERAGE[table].phantom}
    return sorted(seasons)


def _coverage_tiers(con: duckdb.DuckDBPyConnection) -> list[TierResponse]:
    """The three tiers, each bounded by ``COVERAGE``'s declared floors and by
    what this warehouse actually has loaded."""
    present = {row[0] for row in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    tiers = []
    for i, (name, own_tables) in enumerate(_COVERAGE_TIERS):
        tables = _cumulative_tier_tables(i)
        first = _tier_first_season(tables)
        last = _tier_last_season(con, present, tables)
        tiers.append(
            TierResponse(
                name=name,
                tables=list(tables),
                first_season=first,
                last_season=last,
                partial_seasons=_tier_partial_seasons(own_tables, first, last),
                phantom_seasons=_tier_phantom_seasons(own_tables),
            )
        )
    return tiers


def _warehouse_coverage(db_path: str) -> list[TierResponse]:
    """The tiers for the warehouse at ``db_path``, or an empty list if it is
    not there or not yet loaded.

    Its own short-lived connection, for the same reason :func:`_warehouse_seasons`
    keeps one: it must never compete with the Agent's connection for however
    long a question takes.
    """
    if not Path(db_path).exists():
        return []
    try:
        con = connect_read_only(db_path)
    except duckdb.Error:
        return []
    try:
        return _coverage_tiers(con)
    except duckdb.Error:
        return []  # a table COVERAGE names is not loaded yet
    finally:
        con.close()


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

    @app.get("/api/coverage")
    def coverage() -> CoverageResponse:
        """What the warehouse actually holds, in the three tiers #71 asks for
        rather than the single misleading min/max `/api/health` reports."""
        return CoverageResponse(tiers=_warehouse_coverage(db_path))

    @app.post("/api/ask")
    def ask(request: AskRequest) -> AnswerResponse:
        """Answer one question, waiting for any question ahead of it.

        With fall-through disabled (``--disable-fallthrough``, development
        only), a question no template answers is a 501 whose detail says why
        the fast path gave it up, rather than minutes of the agent.
        """
        try:
            return as_response(answerer.ask(request.question, label=f"POST /api/ask {request.question!r}"))
        except FallthroughDisabled as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from None

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
        except Exception as exc:  # noqa: BLE001 - see below
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
