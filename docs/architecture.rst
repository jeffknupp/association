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
is on disk — no network. Loading each table is one ``CREATE OR REPLACE TABLE
... read_parquet(...)`` statement; after all of them come the load-time
repairs and views, in dependency order, every one of which also reruns on a
partial ``association data load --tables ...``:

* :mod:`association.fetch.repairs.game_repair` puts the teams of a game ESPN serves on
  the wrong sides back where they played (one game so far: 1990 Finals Game
  5). Runs ahead of ``real_games``, which copies ``games``, and of every view
  that reads a winner or a side.
* :mod:`association.fetch.repairs.team_box_repair` rewrites ``team_box_stats`` in
  place, correcting three column-level faults ESPN serves (2018's stats
  shifted under a neighboring column's name, ``turnovers`` zero before 2013,
  2008's rebound columns holding something else).
* :mod:`association.fetch.repairs.season_totals_repair` rewrites ``player_season_stats``
  in place, rebuilding a traded player's combined-season row from his own
  stints where ESPN's career endpoint disagrees with itself.
* :mod:`association.fetch.advanced_stats` builds the computed
  ``player_advanced_stats`` / ``player_season_advanced_stats`` views (true
  shooting %, eFG%, usage rate, game score) — pure closed-form formulas over
  already-fetched columns, so they are always rebuilt when ``player_box_stats``
  is present rather than gated behind a flag.
* :mod:`association.fetch.repairs.reconstructed_box` builds
  ``player_box_stats_reconstructed`` and ``player_box_stats_filled``, rebuilding
  a per-game box line from ``plays`` for the roughly 1,025 Chicago/New Orleans
  games (2013-2018) ESPN serves with every stat zero. Deliberately its own
  views rather than a rewrite of ``player_box_stats`` itself, and deliberately
  absent from :data:`association.query.prompt.KNOWN_TABLES` — a reconstructed
  number sitting in the same column as a fetched one would be indistinguishable
  from it.
* :mod:`association.fetch.repairs.real_games` builds the ``real_games`` table: the rows
  of ``games`` that are actually games, with ESPN's placeholder, duplicate and
  phantom rows read past once rather than re-filtered in every template.

The same pass also creates the ``current_season()`` macro and the
``player_season_stats_deduped`` view that collapses a traded player's multiple
rows and drops the postseason lines ESPN's career endpoint copied from a
regular season.

Rebuilding is idempotent and cheap, so it is the right loop when iterating on
schema or parsing. A **full** rebuild (``tables=None``, i.e. no ``--tables``
flag) builds into ``<db_path>.building`` and only renames it over ``db_path``
once every load, repair and view finishes without raising, so an interrupted
build — OOM kill or otherwise — leaves the previous warehouse untouched rather
than half-replaced; a leftover ``.building`` file is itself evidence of one and
is replaced by the next full build. It also runs with
``preserve_insertion_order=false`` and ``enable_external_file_cache=false``
(see ``AGENTS.md``, "Working on the fetch path", for the two out-of-memory
failures each setting fixes). A **partial** rebuild (``--tables``) writes
``db_path`` in place, since it depends on tables already there that it is not
reloading.

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
Each owns one question shape. The ones that read a player's box scores - game
logs, per-game averages, threshold counts, single-game highs, matchups, streaks,
splits, a record with or without a teammate - compose one shared relation,
:mod:`association.query.player_games`, rather than writing their own joins, so
the season-keyed join, the phantom season, the played-game guard and the
rebuilt-line rule are defined once. A team's games are the same shape over
:mod:`association.query.team_games`, which is where a postseason is selected by
the calendar year it was played in and the NBA Cup final is kept out of a
regular-season record - facts that used to be written three times and were
wrong in two of them.

What a relation *narrows by* is a property of the relation, not of each
template. A template settles its subject through
:func:`association.query.templates.common.scoped_player` (or ``scoped_team``)
and its games through :func:`association.query.templates.common.scoped_games`
(or ``team_games``), and the narrowing - an opponent, a venue, a teammate's
absence, one game of each series, a line on a box-score column, one Eastern
date, a span or a first season, and the newest or oldest *N* as a window cut
after every other filter - is applied there, once. The slots a relation honors
are declared once too (:data:`association.query.templates.common.RELATION_SCOPING`),
and a template that cannot honor one of them says why, per cell
(:data:`association.query.templates.common.RELATION_SCOPING_EXCLUDED`) - a reason
about the answer, never about the code. Two tests read the templates' source
to keep it that way: none of them may declare a scoping set of its own, and
none of them, nor a private step it reaches, may narrow the relation by hand.
Before this, twelve slots were honored on one template and one on another,
over the same relation, and each new slot had to be taught to every template
in turn.

What a template still owns is its skeleton (rows, one aggregate, a grouped
table or a streak), its measure and its sentence. The templates that read
season lines or NetPoints rather than games build their SQL directly. Either
way every correctness rule is in code
rather than in prose: season defaults, traded-player dedup,
minimum-sample floors, the home/away perspective flip, the string
``season_type`` that NetPoints uses, the season each table's data starts in
(:mod:`association.nba.coverage`), and dating a game by its US Eastern day rather
than ESPN's UTC timestamp (:func:`association.nba.season.eastern_date`). They phrase their own answers, so the
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

What an answer is
-----------------

:meth:`association.query.agent.Agent.ask` returns an
:class:`association.query.answer.Answer`, not a string. ``answer.text`` is what
the CLI prints and is the whole of what the CLI ever showed; everything beside
it is what the pipeline had already computed and thrown away.

The important field is ``answered_by``: ``"fast"`` for router → template, and
``"agent"`` for the fall-through. That distinction is not bookkeeping. It is the
single most useful thing a reader can know about an answer's reliability —
whether a template built the sentence from code, or a 7B model wrote the SQL —
and it was previously visible only by watching the trace go past.

``intent`` and ``data`` are populated on the fast path and ``None`` on the
other, because only a template produces them. ``data`` is the same answer as
resolved names and numbers, and it exists so a caller can render the result
itself rather than parse the sentence. ``artifacts`` names the files a question
wrote, which a chart's caller previously had to recover from the middle of the
message it was formatted into.

Both paths reach charts differently, which is why artifacts arrive from two
places: a template renders straight to disk and reports what it wrote, while
the agent renders through a tool whose return value is prose the *model* reads,
so :class:`association.query.toolbox.Toolbox` records the file on the side.

The live trace is a sink rather than a print. :class:`association.query.history.RunHistory`
records every line to the run's history file regardless, and hands it to
``sink`` — stderr by default — when ``verbose``. Nothing in the engine writes
to a terminal on its own any more, which is what lets a caller other than a
terminal forward the trace somewhere else.

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

The tool budget
---------------

The agent's preamble is capped at
:data:`association.query.prompt.PREAMBLE_TOKEN_BUDGET` tokens, checked on every
assembly, because ollama truncates an over-length prompt head-first and in
silence — the original bug kept the tool schemas and discarded the schema
summary and the correctness rules.

The cap makes the tool list a *budget*, not a list. Each tool costs roughly 190
tokens of JSON schema plus a line of prose, and that is charged on every
question whether or not it is relevant: the schemas are a fixed block, unlike
the knowledge-base entries, which are already selected per question. Five tools
and the always-on core leave a few hundred tokens of headroom. A sixth and a
seventh do not fit.

Raising the cap is the smallest lever and the one with the least left in it.
It cannot go above ``AGENT_NUM_CTX // 2`` without the preamble starting to
collide with the conversation, and raising ``AGENT_NUM_CTX`` itself buys room
at roughly a second of CPU prefill per hundred tokens, on the slowest path in
the system.

The three real levers, cheapest first:

#. **Fold renderers into one tool.** ``render_shot_chart`` and
   ``render_fingerprint`` are two ~190-token schemas describing the same verb
   over different nouns. One ``render(kind, player, season, …)`` with an enum of
   kinds costs one schema, and each new chart type after that costs an enum
   value — about five tokens instead of a hundred and ninety. This is the
   change to make first, and it gets cheaper the more chart types exist.

#. **Select tool schemas per question, the way knowledge-base entries already
   are.** :func:`association.query.prompt.select_knowledge` scores entries by
   keyword overlap and includes only what a question needs;
   ``describe_table``/``run_sql`` would stay always-on and the rest would be
   selected the same way. The cost then scales with what a question is *about*
   rather than with how much the system can do. The risk is the inverse of the
   knowledge base's: a missed entry only makes the agent less informed, while a
   missed tool makes a capability unreachable — so anything selected out this
   way must already be covered by the fast path.

#. **Port more shapes to templates.** This does not shrink the preamble; it
   shrinks how much the preamble matters. Every intent with a template is a
   question the agent never sees, and the budget only binds on the fall-through
   path. It is also the only lever that makes answers *faster* rather than
   merely affordable.

What not to do is quietly trim the standing rules or ``TABLE_SUMMARY`` to make
room. Those are the text the original truncation bug destroyed, and nothing in
the test suite can tell that the agent got worse at writing SQL.

Failing loudly
--------------

The recurring failure mode in this system is not an error — it is a fast,
fluent answer to a *different* question. Several mechanisms exist only to stop
that:

* Templates refuse a named stat they cannot provide rather than falling back to
  a default (a default is only safe where the user named nothing).
* Templates declare which scope slots they honor
  (:func:`association.query.templates.check_scope`) - the ones on a relation
  through the relation's single declaration, the rest each for themselves - and
  a question scoped to particular games falls through rather than being
  answered for a season.
* A question about a season a table cannot reach is refused, with the reason
  (:func:`association.query.templates.check_coverage`). The refusal is returned
  as the answer rather than raised, because the agent would query the same
  empty tables and is then free to fill the silence from its own weights.
* Slots the router drops or files in the wrong place are read from the
  question text, and a player name the question does not support is refused
  rather than answered about
  (:func:`association.query.entities.override_invented_players`).
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
live with ``--live``. Every question, from ``query`` or ``association web``, also writes a full trace to
``.history/`` — the command, the routing decision, every tool call, per-call
timings, and the final answer — whether or not ``--verbose`` was passed.
