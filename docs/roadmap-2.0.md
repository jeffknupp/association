# Plan: 2.0, a web interface

A local web UI for asking questions — a conversational-looking app over the
existing query pipeline — served by `association web` and backed by an HTTP API.

This is a plan, not a description of what exists. Nothing in it is built yet.
`architecture.rst` remains the source of truth for the system as it is today.

## Scope

**In, for 2.0:**

- `association web`, which serves a local app until you quit it.
- A chat-shaped UI: a message list, an input box, an answer per message.
- Answers from the existing pipeline — the same text `association query`
  prints today, with the fast path handling the common shapes.
- Each message is an independent query. No conversation memory.
- An HTTP API underneath, usable on its own.
- HTML renderings of answers, per intent, replacing the fixed-width text where
  a table or a chart says it better.
- Shot charts and fingerprints rendered inline in the conversation.

**Out, deliberately:**

- Any hosted or multi-user deployment. This binds to localhost and has no
  authentication, because it has no business being reachable.
- The `ai` REPL, which this replaces. It is already gone from master (see
  below); the web UI is what an interactive session looks like from 2.0 on.
- Conversation memory (see [After 2.0](#after-20)).
- Writing to the warehouse. Query connections stay read-only, as they are now.
- Replacing the CLI. `query`, `ai` and `data` keep working unchanged.

## Why this is a major version

The changelog preamble defines a major bump as "something that used to work
stops working", over the CLI surface and the documented Python API. Three
things have to change shape, and they are the reason the version number moves
rather than the fact that there is a new feature.

These are all **done** as of Phase 0 below; they are recorded here as the
reason the version number moves.

**`Agent.ask` returned prose and threw the rest away.** `_try_fast_path` got a
`TemplateResult` carrying both `answer` (the phrasing) and `data` (the resolved
names and numbers, no ids, no schema) and returns only `result.answer`. `data`
exists precisely so a caller can render the answer itself, and nothing could
reach it. It returns a structured result now: the text, the intent that
produced it, the structured data, and any artifacts written.

**The two renderers disagreed about what they return.**
`fingerprint.render_for_players` returned `(message, path)`;
`shotchart.render_for_player` returned only a message string with the path
formatted into the middle of it. Inline rendering needs the path, and parsing
it back out of a sentence is not a plan. All four render entry points return
the same `RenderResult` now.

**`Agent.ask` assumed it was a CLI.** It labeled its history entry with
`shlex.join(sys.argv)` and printed `[history] ...` straight to stderr; the trace
lines in `RunHistory.log` went to stderr when `verbose`. A server needs those as
events it can forward to a browser, and needs to say what the request was
rather than what the process's argv happened to be. `Agent` takes a `trace`
callback and `ask` takes a `label`; `RunHistory` takes a `sink`.

**The `ai` subcommand is gone.** The web UI replaces it, so keeping an
interactive terminal REPL alive would mean maintaining two interactive
front-ends with different capabilities against the same engine. Removed already,
along with `query/repl.py` and `Agent.reset`, which nothing else called. Between
now and Phase 1 there is no interactive mode on master — `association query`
answers one question per invocation, as it always has.

None of these are large changes. They are, however, changes to documented
public API, so they are 2.0 and they should land as one deliberate reshaping
rather than being smuggled in.

## Constraints this system imposes

These are not preferences. Each one has already cost something in this project,
and the design has to take them as given.

**One model, one KV cache slot.** ollama keeps a single cache slot per model,
and interleaving a call with a different system prompt evicts the previous
prefix. This is measured: three consecutive router calls run 11.6s / 1.3s /
1.7s, but slipping one narration call in between puts the next back to 11.2s.
Two browser tabs asking questions simultaneously would do exactly that to each
other. **The server must serialize model calls** — one in flight, others queued,
with the UI told it is waiting rather than left to guess.

**The two paths differ by two orders of magnitude.** A fast-path answer is one
router call: typically 1-2s. A fall-through to the agent is a 6,000-token
prefill plus a tool loop — measured at 113s for the first inference alone on a
CPU-only box. A UI that shows a spinner for both is unusable for the second.
**Progress has to stream**, and the trace the agent already produces
(`routed`, each tool call, each timing) is exactly the material for it.

**The fast path is the product.** Most questions should never reach the agent.
This is already true and stays true; the web UI's job is to make the fast
path's structured output visible, not to lean harder on the model.

**Tests are offline.** `uv run pytest -q` makes no network calls and needs no
ollama. Every web test must hold that line: the API layer gets tested against a
stub answerer, never a live model.

**The preamble budget still binds.** Nothing in the web work should add agent
tools. See "The tool budget" in `architecture.rst`.

## Architecture

```
browser ──HTTP──> association.web.app (FastAPI)
                        │
                        │  one at a time (asyncio.Lock / single worker)
                        ▼
                  association.query.agent.Agent
                        │
              router → template → answer      (fast path, 1-2s)
                        └→ tool-calling agent  (fall-through, 30-120s)
                        │
                        ▼
                  DuckDB (read-only)   +   query_output/*.html
```

One process. One `Agent`, constructed at startup and reused, so the DuckDB
connection and the ollama keep-alive are not re-established per request. The
`Agent`'s conversation state is not used: 2.0 answers each question
independently, which is both the requested behavior and the reason the server
can be stateless.

### Why FastAPI

| Option | Verdict |
|---|---|
| **FastAPI** | **Chosen.** Typed request/response models fit a codebase already at 100% `pyright --verifytypes`. `def` endpoints run in a threadpool automatically, which is what blocking DuckDB and ollama calls need. SSE via `StreamingResponse` with no extra dependency. Static files built in. OpenAPI for free, which makes the API self-documenting for anything else that wants it. |
| Flask | Simpler, and genuinely sync-native, which suits blocking work. But streaming is clumsier, there is no typed schema layer, and the dependency count is not meaningfully lower once you add the pieces back. |
| Starlette alone | What FastAPI is built on. Saves pydantic and buys hand-written validation. Not worth it. |
| Litestar / Quart / aiohttp | No advantage here that pays for the smaller ecosystem. |
| **GraphQL** (Strawberry/Ariadne) | **Rejected.** GraphQL earns its complexity when clients need to shape queries over a graph of related resources. This API has one verb — ask a question — and a handful of reads. It would add a schema layer, a resolver layer and a client runtime to express `POST /ask`. |
| WebSockets | Considered for streaming. SSE is one-way, which is all progress reporting needs, and survives proxies and reloads more simply. Revisit only if the UI grows a genuinely bidirectional need (cancel is the candidate — see Risks). |

`fastapi` and `uvicorn` go in an optional extra, `association[web]`, so the core
install stays at seven dependencies. `association web` without it must fail with
the install command, not a traceback. Imports stay inside the command, as every
other CLI command already does, so `association --help` does not pay for them.

### API surface

```
GET  /                     the app shell (static)
POST /api/ask              {"question": "..."} -> Answer
GET  /api/ask/stream       SSE: the same, with progress events
GET  /api/health           warehouse path, seasons loaded, models, readiness
GET  /api/artifacts/{name} a rendered chart, from the output directory
```

`Answer` is the structured result the refactor above produces:

```json
{
  "question": "who leads the league in assists?",
  "text": "Nikola Jokic led the league in assists per game ...",
  "answered_by": "fast",
  "intent": "leaderboard",
  "data": {"season": 2026, "leaders": []},
  "artifacts": [{"kind": "shot_chart", "name": "shotchart_stephen_curry.html"}],
  "timing": {"total": 1.42, "model": 1.39, "calls": 1}
}
```

`answered_by` is `fast` or `agent`; `intent` and `data` are null when the agent
answered, since only a template produces them. (The plan first called this
field `path`, which reads badly next to an artifact's file path.)

SSE events, in order: `queued` (only if something else is running), `routed`
(intent and slots), `tool` (name, elapsed) zero or more times, then `answer` or
`error`. These map one-to-one onto what `RunHistory` already records, which is
why the refactor should give `RunHistory` a sink rather than hardcoding stderr.

## Phases

Each phase is shippable and independently useful. Phase 0 is the only one that
breaks anything.

### Phase 0 — make the answer a value, not a print — **done**

No web code. Reshape the query API so a caller other than a terminal can use it.

- `Agent.ask` returns an `Answer` (text, `answered_by`, intent, data, artifacts,
  timing) instead of `str`. `cli.query` prints `answer.text`.
- `_try_fast_path` stops discarding `TemplateResult.data`.
- Both renderers return the same result shape, carrying the written path.
- `RunHistory` takes an optional callback for its trace lines; stderr echoing
  becomes one implementation of it, not the only one.
- `Agent.ask` takes the label for its history entry instead of reading `sys.argv`.

*Done when:* the CLI's output is byte-identical to 1.6.0 for a sample of
questions across every intent, and `Answer.data` is populated for each. The
byte-identical check is the point — this phase must be invisible from outside.

*Verified:* ten questions covering `leaderboard`, `head_to_head`,
`player_history`, `threshold_count`, `team_record`, `player_compare`,
`shot_chart`, `fingerprint`, `shot_distance` and `game_log`, run against a
1.6.0 worktree and against this branch on the same warehouse — byte-identical
on all ten, `data` populated on all ten, and both chart questions reporting the
file they wrote as an artifact.

Two things changed from the plan as written. `Answer.answered_by` carries the
`"fast"`/`"agent"` distinction rather than a field named `path`, which sat too
close to `Artifact.path` to keep straight. And the two *tool-level* renderers
(`render_shot_chart`, `render_fingerprint`) return the same `RenderResult` as
the two lower-level ones rather than staying strings: the agent reaches charts
only through those, so leaving them as prose would have meant the agent path
could never report an artifact.

### Phase 1 — `association web`, text answers

The MVP: the thing described in the request.

- `association web`, serving until interrupted. **No default port**: it binds
  an ephemeral one and prints the full URL — `http://127.0.0.1:54312` — which
  every modern terminal turns into a link. A fixed default would collide with
  whatever else is running and would have to be explained; a printed URL never
  does. `--port` stays available for anyone who wants to bookmark one.
- `--db-path` and `--out-dir` are startup flags, with the same defaults the
  `data` subcommands use (`./nba.duckdb`, `./query_output`). They are
  configuration for the process, not something a request can change.
- Chat UI: message list, input, answers as monospace text (what the CLI prints).
- `POST /api/ask` and the SSE stream; one query in flight, others queued.
- Each message is a fresh query — no history sent to the server.
- The UI says which path answered and how long it took. This is not decoration:
  the project's rule is that every answer names its scope, and "a template
  answered this deterministically" versus "a 7B model wrote SQL for this" is
  the single most useful thing a reader can know about an answer's reliability.

There is no way to turn the fast path off from the UI, and no plan to add one.
The fast path *is* the product; the fall-through is a frustrating but bearable
wait that the UI's job is to make legible, not to offer as a mode.

*Done when:* every question in `scripts/check_routing.py` can be asked in the
browser and returns the same text the CLI returns, and a deliberate
fall-through question streams progress rather than appearing to hang.

### Phase 2 — HTML answers per intent

`Answer.data` is already structured. Render it.

- A renderer per intent, keyed off `Answer.intent`, falling back to the text.
  `leaderboard` and `player_compare` become real tables; `player_history` a
  small sparkline; `team_record` a compact record card.
- The text stays in the payload and stays the fallback. Phrasing lives in the
  templates, in Python, once — the browser does not re-derive sentences.

*Done when:* an intent with no renderer still answers correctly, in text.

### Phase 3 — charts inline

- `GET /api/artifacts/{name}` serves from the agent's output directory, with the
  name validated against that directory (no traversal, no absolute paths).
- Shot charts and fingerprints render inline in the conversation.
- Decide between embedding the existing standalone HTML in an `<iframe>` (zero
  change to the renderers, hard to theme with the page) and extracting an
  SVG-fragment function the page can inline (better integration, a real
  refactor of `court.py` and `radar.py`). **Recommend the iframe first** — it is
  one route and no risk to two modules that are currently correct — and revisit
  once the UI's look is settled.

*Done when:* asking for a chart in the browser shows the chart, and the same
question via the CLI still writes the same standalone file.

### After 2.0

- **Conversation memory.** The router already accepts `previous_question`, and
  the REPL already uses it; the agent already keeps `messages`. Turning it on is
  small. It is out of 2.0 because it changes what a question *means* — "what
  about last year?" is a different failure surface, and it deserves its own
  routing cases rather than being a footnote to a UI release.
- **Export a conversation** (the transcript, or a single answer, as HTML).
- **Saved questions.**

## Testing

- `fastapi.testclient.TestClient` against the app with the answerer stubbed.
  No ollama, no network — the existing pytest guarantee holds unchanged.
- The queueing rule gets a real test: two concurrent requests, an answerer that
  records overlap, and an assertion that it never sees two at once. This is the
  same shape as the `_map` concurrency tests added in 1.6.0.
- Artifact serving gets a path-traversal test (`../`, absolute paths, symlinks).
- One end-to-end check stays manual and documented: `association web`, ask three
  questions, see the chart. Browser automation is not worth its weight here.

## Risks

**Cancelling a slow answer.** A 113s agent question with no cancel is the worst
UX in the plan. Server-side cancellation is genuinely hard: the ollama call is
blocking and does not take a cancellation token. Mitigation, in order: stream
progress so the wait is legible; check a per-request cancelled flag between tool
iterations (bounded by one inference, not the whole run); document that closing
the tab does not stop the current inference. Do not pretend to cancel.

**Making a template look like a chatbot.** The UI is conversational in *shape*,
and the answers are deterministic template output. That is a feature, and the UI
should not obscure it — no typing animation, no "thinking..." theatre, and an
explicit marker of which path answered. Overselling it invites exactly the trust
the fall-through path has not earned.

**Static assets in the wheel.** Anything shipped has to survive packaging, which
this project has been bitten by before. The release check must install the wheel
into a fresh venv outside the repo and load the page, not just import the module.

**Scope creep into a framework.** No build step, no bundler, no SPA framework
unless Phase 2 proves it necessary. Plain HTML, CSS and a small amount of
JavaScript, matching how `court.py` and `radar.py` already produce pages.

## Settled, and why

These were open when the plan was first written. They are not any more.

1. **No default port.** Bind an ephemeral port and print the full URL for the
   terminal to linkify. Nothing to remember, nothing to collide with.
2. **`ai` does not survive.** The web UI is its replacement, not a companion to
   it. Removed already rather than left to rot until 2.0 ships.
3. **`--no-fast-path` is not exposed.** The fast path is the product. The
   fall-through is a wait to be made legible, not a mode to offer.
4. **One warehouse per process.** `--db-path` is a startup flag matching the
   `data` subcommands' defaults. No runtime switching.
