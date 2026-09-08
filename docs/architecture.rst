Architecture
============

Three independent stages, each usable on its own: **fetch** writes Parquet,
**load** builds a DuckDB warehouse from it, and **query** answers questions
against that warehouse. Nothing later in the chain needs the network.

.. code-block:: text

   ESPN endpoints ──► fetch/ ──► data/parquet/ ──► warehouse ──► nba.duckdb
                                                                     │
                                     question ──► router ──► template┤
                                                     │               │
                                                     └──► agent ─────┘

Fetch
-----

:mod:`association.fetch.pipeline` drives the whole pull.
:class:`association.fetch.client.ESPNClient` handles transport — rate limiting,
retries, and TLS fingerprinting via ``curl_cffi``, because ESPN's CDN rejects
plain ``requests``/``httpx``. :mod:`association.fetch.parse` flattens each
JSON response into tabular rows, and :mod:`association.fetch.storage` writes
Parquet plus completion markers.

Those markers are what make a pull **resumable**: a season/season-type marked
complete is skipped entirely on the next run, so re-running is cheap and
interrupting is safe. ``--force`` overrides that, deliberately.

Load
----

:mod:`association.fetch.warehouse` builds ``nba.duckdb`` from whatever Parquet
is on disk — no network. It also creates the derived pieces the query engine
relies on: the ``current_season()`` macro, the ``player_season_stats_deduped``
view that collapses a traded player's multiple rows, and the computed advanced
stats in :mod:`association.fetch.advanced_stats`.

Rebuilding is idempotent and cheap, so it is the right loop when iterating on
schema or parsing.

Query: a router in front of an agent
------------------------------------

The query engine's design is the product of one measurement. A single model was
originally asked to do two jobs at once — understand the question *and* write
correct SQL for it — which forced the entire schema and every correctness rule
to be resident for every question. That preamble reached **10,295 tokens
against a context window of 8,192**. ollama truncates head-first and silently,
so only 4,098 tokens ever reached the model, and what it discarded was the
schema summary and most of the correctness rules. Questions took minutes and
answered the wrong thing.

So the two jobs are now separate.

**The router** (:mod:`association.query.router`) does only the language half:
it classifies a question into an intent and slots, under a JSON schema passed
as ollama's ``format``, so decoding is *constrained* rather than merely
prompted. Its prompt carries no schema and no SQL, which keeps it small enough
to stay in the KV cache — typically one model call of 1–2 seconds.

**Templates** (:mod:`association.query.templates`) do the deterministic half.
Each owns one question shape and builds its own SQL, with every correctness
rule in code rather than in prose: season defaults, traded-player dedup,
minimum-sample floors, the home/away perspective flip, the string
``season_type`` that NetPoints uses. They phrase their own answers, so the
common case is a single model call end to end.

**The agent** (:mod:`association.query.agent`) is the fall-through for
questions no template covers. It still writes SQL by hand with the tools in
:mod:`association.query.toolbox`, and its preamble is assembled per question
(:func:`association.query.prompt.build_system_prompt`) so it carries only the
knowledge-base entries that question needs.

Why templates rather than better prompting
------------------------------------------

The case against prompt-only correctness is empirical. Asked how many times two
teams had played, the agent wrote ``home_team_id = 'PHI'`` — team ids are
opaque all-digit strings, so the filter matched nothing, and it reported that
the teams had never met. The rule against exactly that was in its prompt,
verbatim, with that exact wrong form spelled out as a worked example.

A template cannot make that mistake, because resolving a name to an id is code
that runs the same way every time.

Two models
----------

Routing and SQL generation want different models. Benchmarked over the routing
cases in ``scripts/check_routing.py``, every model from 1.5B to 8B landed within
a case or two of the rest — constrained decoding does the structural work, so the
model only classifies and fills slots. The router therefore runs a 3B (``--router-model``)
while the agent keeps a 7B (``--model``) for hand-written SQL. Reproduce with
``scripts/bench_router_models.py``.

Thinking models are disqualified on latency rather than accuracy: qwen3:4b
spent about 20 seconds per question reasoning before emitting the same small
JSON object that qwen2.5:3b produces in about one.

Failing loudly
--------------

The recurring failure mode in this system is not an error — it is a fast,
fluent answer to a *different* question. Several mechanisms exist only to stop
that:

* Templates refuse a named stat they cannot provide rather than falling back to
  a default (a default is only safe where the user named nothing).
* Templates declare which scope slots they honor
  (:func:`association.query.templates.check_scope`); a question scoped to
  particular games falls through rather than being answered for a season.
* :func:`association.query.prompt.build_system_prompt` raises rather than
  handing ollama a prompt it would quietly truncate.
* :meth:`association.query.toolbox.Toolbox.run_sql` bounds results by tokens
  rather than rows, and flags an ``*_id`` compared to a non-numeric literal —
  a filter that can never match.
* Every answer names the season and scope it used, so a substitution is visible
  rather than silent.

Auditing
--------

:mod:`association.check.report` compares what is on disk against what the
endpoints say should exist, per season and season type, and can cross-check
live with ``--live``. Every ``query``/``ai`` run also writes a full trace to
``.history/`` — the command, the routing decision, every tool call, per-call
timings, and the final answer — whether or not ``--verbose`` was passed.
