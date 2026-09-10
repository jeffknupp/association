# Working on `association`

Orientation for agents (and people) making changes here. It covers what is
*not* obvious from reading the code: the gates, the conventions that are
enforced, and the specific shapes of bug this project keeps producing.

For how the system is designed — the three stages, the router/agent split, why
templates instead of better prompting — read `docs/architecture.rst`. That is
the source of truth for design, and this file does not restate it.

## Before you commit

```bash
uv run pre-commit run --all-files   # all eight gates
uv run pytest -q                    # fully offline: no network, no ollama
```

Both must be clean. Everything in `pre-commit` also runs in CI
(`.github/workflows/ci.yml`), so a green local run means a green PR.

That equivalence is not automatic, and it has broken three times. All three
were the local run being *weaker* than CI, never the reverse, so the failure
mode is always the same: green locally, red on the PR.

- **`--all-files` means every file git knows about, not every file on disk.**
  A new, untracked test is skipped entirely, so the gates pass while saying
  nothing about it. `git add` first, then run them. This has cost two CI
  round trips, on a line-length error and a formatting one.
- **Read the exit status, not the output.** `pre-commit run --all-files | tail`
  hides a failure in the *first* hook - and `ruff` is first. Redirect to a file
  and check `$?`.
- **A gate script has to work when run directly**, not only through `uv run`.
  `pre-commit` runs its hooks under `uv run`, which exports `VIRTUAL_ENV`; CI
  invokes the same scripts bare. `scripts/check_types_complete.sh` resolved
  imports out of the active venv under the first and not the second, so it
  passed locally and failed in CI on the same commit. Test a change to one with
  plain `bash scripts/x.sh`.

Two things about the gates surprise people:

- **mypy runs twice**, over `src` and `tests` separately, never as one
  invocation. Combined, mypy resolves the `association` package two different
  ways (source-rooted `src/` vs. the editable install `tests/` imports) and
  reports errors that do not occur when each root is checked alone.
- **`pyright --verifytypes` must stay at 100%.** `scripts/check_types_complete.sh`
  installs to a scratch dir first, because `--verifytypes` inspects an
  *installed* package and the editable install resolves through an import hook
  pyright cannot follow. New public symbols need real annotations — no bare
  `dict`/`list`/`tuple`.

Any commit touching `src/` must also touch `CHANGES.md`; a hook enforces it.
Add to the `## Unreleased` section.

## Conventions

- **Line length is 200, and `ruff format` is enforced.** This is deliberate:
  much of the code is prompt text and SQL that reads worse wrapped. The
  formatter inherits the same setting, so it *joins* long strings rather than
  fighting them.
- **American spelling.** "defense", "offense", "serialize". British spellings
  have drifted in before and were removed wholesale in `c09d6f7`.
- **Every public module, class and function needs a docstring** — a separate
  gate from the Sphinx build, because autodoc renders an undocumented function
  perfectly happily, just uselessly.
- **Docstrings are published.** `docs/api/index.rst` runs `autosummary` over the
  whole package recursively, so every public module gets a page without anyone
  adding it. Two consequences: docstrings are reStructuredText, not plain text
  (`` `x` `` is a *reference*, not code — use ``` ``x`` ``` for a literal), and
  the docs build runs under `-W`, so a malformed one fails the gate.
- **Mark public API changes with a version directive.** A new public function,
  class or module gets `.. versionadded:: X.Y.Z` at the end of its docstring; a
  renamed or reshaped one gets `.. versionchanged:: X.Y.Z` saying what moved.
  Use the version being released next, not the current one. Only the public
  surface is worth marking — internal helpers and the template/intent set are
  explicitly outside the compatibility promise (see the preamble in
  `CHANGES.md`). Module-level constants need an attribute docstring (a string
  literal directly *after* the assignment) for the directive to attach.
- Comments explain *why*, especially where the code looks odd. Most of the odd
  code here is load-bearing.

## The failure shape this project keeps producing

**A missing or too-narrow shape, never a broken one.** Every query failure
reported during development looked like this: the system answered fast and
fluently — but answered a *different question* than the one asked, because no
template covered the real one and something adjacent matched instead.

The consequence for how you work: **an answer that looks right is not
evidence.** Check that the shape you added is the shape being exercised. This
is why templates now refuse rather than approximate — see `check_scope()` and
`HONORED_SCOPING` in `query/templates.py`, which make a template declare which
scoping slots it honors and raise on the rest, instead of silently ignoring
`order` or `date` and returning a whole-season answer to a single-game
question.

When adding a template, prefer refusing to guessing. `resolve_*` in
`query/entities.py` never guesses between candidate players; `leaderboard`
rejects a named `player`; `team_record` rejects `limit`.

**The same bug has a mirror image: a refusal that names the wrong cause.** It
reads as honest, so nothing looks wrong. "Show me a fingerprint for Maxey"
answered `No NetPoints fingerprint on record for season 2026` — a claim about
league-wide coverage, and false; that season holds 566 players and Tyrese
Maxey is one of them. The real cause was that "Maxey" had resolved to Marlon
Maxey, who retired in 1994. Before writing a "no data" message, check which
fact is actually missing: the season, the player, or the match. They are
different sentences, and the wrong one sends the reader to look in the wrong
place.

**The router invents names, and an invented name resolves.** This is the
worst-behaved version of the shape above, because nothing about the answer
looks wrong. "Compare sga and embiid" routed to
`['Shai Gilgeous-Alexander', 'Jusuf Nurkic']` and produced a correct table of
two real players, one of whom the question never mentioned - every check after
the router passed, because "Jusuf Nurkic" is a real person who resolves
cleanly. The nickname version of this was already known
(`override_nicknames`: "The Answer" became Klay Thompson); the general version
is that **any** router-supplied name may be fiction.

So a name is checked against the question before a template reads it -
`entities.override_invented_players`. What counts as the question supporting a
name is deliberately generous, because the router's expansions are usually the
useful kind: the word itself, a near spelling of it (the router silently
corrects typos), a nickname, or the initials ("KAT", "SGA"). Any ONE word of
the name is enough, since half a name is how a question normally carries one -
what this catches is a name with no half in the question at all. Three rules
about what happens next, and the third is the one that was got wrong first:

- **Replace only from what the question itself names, and only when the count
  is exact** - the same discipline `override_nicknames` uses. `players_named_in`
  is strict about what naming somebody means: a span must equal a *whole word*
  of exactly one player's name. Substring matching reads "the highest scoring
  game" as naming Jaron Blossomgame; word-boundary matching reads "with" as
  naming Jeff Withey; and allowing a one-letter span makes the possessive left
  behind by "Jokic's" name John S. Williams, who is in nine of the routing
  corpus's questions.
- **Trim a name back to the part the question holds only where that part is
  ambiguous.** Expanding half a name is usually the router doing its job:
  "luka", "jokic" and "embiid" each reach exactly one player, so undoing the
  completion would only cost the question its answer. But "who is better,
  tatum or brown" routed to `Jaylen Brown`, and "brown" is ten players - that
  completion is the prominence tiebreak measured and rejected above
  `PLAYER_NICKNAMES`, arriving through the model's guess where nothing
  downstream can see it. A bare surname is the canonical thing this project
  asks about, and it stopped asking the moment the router began completing it.
  `undo_name_completion` cuts those back and lets normal resolution decide;
  `find_players` applies the nickname table first, so a shorthand the curated
  list holds ("luka", "steph curry") still resolves rather than asking.
  Measured over the routing corpus, no slot moves.
- **When it cannot be repaired, say so - do not hand it to the agent.** This
  one shipped wrong first, on the reasoning that the agent at least reads the
  question. Measured, that is far worse: "compare fingerprints for embiid vs
  jokic in 2026" fell through and the agent spent 55 seconds writing a
  confident fingerprint for **"Ronaldo Lopes"**, a player who does not exist,
  with play-type percentages attached. It is the lesson `check_coverage`
  already carries - an agent with nothing to find fills the silence from its
  own weights - and it needs saying twice, because falling through *feels* like
  the humble option. Refuse only where the template would actually be about
  that player (`PLAYER_INTENTS`, checked against the templates' own source): a
  stray name on a `head_to_head` question changes no answer, and refusing over
  it would break a question that works.

**The router also drops names, not only invents them.** "Compare fingerprints
for embiid vs jokic" arrived as a single `player` slot, so the answer was one
polygon where two were asked for - a narrower question, answered without saying
so. `restore_dropped_players` puts them back, and only for `fingerprint`, where
two polygons on shared axes IS the comparison. The same move on `player_stat`
would turn a question about one player into a question about two.

Restoring is gated on the question saying it compares something, because
`players_named_in` is strict but not infallible: "best" is Travis Best and
"boston" is Brandon Boston Jr., so "plot jokic's fingerprint from his best
season" names two players by its rules and drew Travis Best a polygon until
that gate existed. The gate costs the questions that compare without saying so
("plot jokic and embiid fingerprints"), which lose the second name exactly as
they always did.

What none of this can do is repair a name nobody typed correctly. "embiid vs
jolic" loses Jokic, and **fuzzy-matching the question's leftover words to find
him was measured and rejected**: it produces a spurious player in 29 of 51
corpus questions ("season" is one edit from Tari Eason, "most" from Quinten
Post, "what" from Dejuan Wheat) and does not even find Jokic. So the answer
says a player is missing instead - `compared_but_unmatched`, which takes "vs"
and not "compare", since "compare Jokic's fingerprint to last season" compares
seasons. Stating the gap is the whole difference between a narrower answer and
a wrong one.

**Why "embiid" specifically.** Worth recording as a shape rather than a name.
Measured against qwen2.5:3b at temperature 0: lowercase `embiid` in a
comparison or fingerprint framing returns Ben Simmons 5/5 (earlier, Jusuf
Nurkic) - deterministically, not as noise. `Embiid`, `Joel Embiid` and
`joel embiid` all resolve correctly, and so does lowercase `embiid` in
"how many points does embiid average". So it is not that the model lacks the
name: it is a rare-token surname, uncapitalized, in a frame whose training data
is dominated by one famous pairing. Every other surname tried in the same slot
(jokic, luka, wemby, giannis, tatum, curry) is correct. Which player it
substitutes moves between sessions - Jusuf Nurkic one day, Ben Simmons the
next - so there is nothing here to special-case, only a reason to check every
name against the question.

**Before saying nothing matched, check whether something nearly did.** The
other half of the same bug: "compare sga and embid" routed to `'Jemel Embiid'`,
and since `find_players` requires every token to match, one fabricated word
buried a player the warehouse holds. `entities.suggest_players` backs a
multi-word name off to its surname - exact matching on one fewer token, not
fuzzy - and then looks for near spellings, which is the only thing that reaches
the user's own typo ("embid" is not a substring of "Embiid", so no ILIKE finds
it). Two things keep it honest: it never substitutes, it only asks, and a
suggestion naming more than `MAX_CLARIFY_CANDIDATES` players is dropped
entirely, because a name near 25 players narrowed nothing and reading out a
directory is not a suggestion.

**A best match is only safe where a wrong one is visible.** Charts resolved
names best-match on the reasoning that the plot is titled with the name that
won — sound, until the wrong name is *why* no plot gets drawn, which is
exactly when the safeguard disappears. `resolve_chart_player` now narrows
candidates to those with a row in the table the chart is drawn from
(`entities.narrow_to_available`) and asks when more than one survives. Note
the shape of that narrowing: it *eliminates* candidates who cannot be the
answer, and never chooses between two who can. That is what makes it allowed
where the prominence tiebreak above `PLAYER_NICKNAMES` was measured and
rejected.

## Working on the query path

The pipeline is router → template → deterministic answer, with the agent as
fall-through. A question the router cannot classify falls through to the
slower SQL-writing agent; that is by design, not a bug.

- **Router prompt and JSON schema must agree.** An intent described in
  `ROUTER_PROMPT` but missing from `ROUTER_SCHEMA`'s enum can never be emitted
  under constrained decoding, so it silently routes elsewhere. This happened
  with `player_compare`. Two tests now guard it —
  `test_every_intent_the_prompt_describes_is_emittable` and
  `test_every_ported_template_has_an_intent_in_the_schema`.
- **The preamble has a hard token budget.** `PREAMBLE_TOKEN_BUDGET = 6400`
  against `AGENT_NUM_CTX = 16384`, enforced by raising `PreambleTooLarge`. This
  exists because ollama truncates an over-length prompt *silently and
  head-first*: the original bug was a 10,295-token preamble against
  `NUM_CTX = 8192`, which discarded the schema and correctness rules while
  keeping the tool descriptions.

  Treat the tool list as a **budget, not a list**. Each tool costs ~190 tokens
  of JSON schema, charged on every question whether or not it is relevant —
  unlike knowledge-base entries, which `select_knowledge` already filters per
  question. Five tools leave ~220 tokens of headroom; a sixth does not fit.
  `docs/architecture.rst` ("The tool budget") has the levers, cheapest first.
  Do not buy room by trimming `TABLE_SUMMARY` or the standing rules: that is
  the text the original truncation bug destroyed, and no gate can tell that the
  agent got worse at writing SQL.
- **Tool schemas and the dispatch table must agree**, the same way the router
  prompt and schema must. They are two hand-maintained lists of the same names:
  a name in `TOOLS` with no handler is a `KeyError` the first time the model
  calls it, and a handler no schema mentions is a capability the model cannot
  reach — `render_fingerprint` sat in exactly that state while it did not fit
  the budget. Guarded by
  `test_every_advertised_tool_can_actually_be_dispatched` and
  `test_every_tool_schema_names_its_required_parameters`.
- **A slot the schema does not require is a slot the decoder may never
  consider, and no prompt wording fixes that.** `ROUTER_SCHEMA` already records
  this for `stat`; `side` proved it again. "Show me Wembanyama's defensive
  fingerprint chart" appears in `ROUTER_PROMPT` verbatim as a worked example
  with `{"side":"defense"}` beside it, and still emitted `stat="defensive"`
  with no `side` at all — 6/6 at temperature 0. `stat` is required, so the
  adjective is spent there first. The whole fingerprint got drawn where its
  defensive half was asked for.

  The same slot has a second, opposite failure: **a required slot is one the
  decoder fills whether or not the question asked for it.** `stat` came back as
  `'points'` on "compare sga and embiid" 12 times out of 12, which narrowed
  `player_compare` to one average and undid the whole-line default it exists
  for. `route()` drops it for that intent only. Note why the word list can be
  loose there and could not be anywhere else: for a comparison, a missed word
  widens the answer to a line that still holds the stat asked about, while
  `leaderboard` with no stat has nothing to rank by.

  Two ways out, and prefer the second. Making the slot *required* works (that
  is why `stat` is) but was measured and reverted for `season_ref`, because
  requiring more slots crowds out others. Reading the value **from the question
  text** in `route()` costs nothing and cannot move any other slot: that is
  what `_validate_season` does for the year and `_validate_side` now does for
  the side of the ball. Hash `ROUTER_PROMPT` and `ROUTER_SCHEMA` before and
  after to prove the model's input is unchanged — if both hashes match, no
  other question's routing can have moved, and `check_routing.py` should come
  back line-for-line identical apart from the case you fixed.
- **Any edit to `ROUTER_PROMPT` moves slots on unrelated questions.** Adding the
  `fingerprint` intent line reproducibly flipped "What was the Lakers record
  last season?" from `team` `"Lakers"` to `"Los Angeles Lakers"` — with *any*
  wording of the added line, including a two-line one, so it is the prompt's
  length as much as its content. The 3B router is that sensitive. Two
  consequences: re-run `scripts/check_routing.py` after any prompt edit, and
  assert in a case only what changes the *answer* (both those strings resolve
  to team_id 13 and produce an identical sentence), never the encoding the
  model happened to pick.
- **Add a case to `scripts/check_routing.py`** whenever you port a shape or
  find a mis-route in the wild. It is the only regression net for routing —
  pytest cannot catch a prompt change that starts routing questions to `other`.
  Read its module docstring before running it: **only one instance at a time**,
  or a CPU-only ollama goes into a reload loop that wedges it for minutes.

## Working on the fetch path

`data pull` is checkpoint-driven and must stay cheap to re-run: a pull over
seasons already on disk makes no request and rebuilds nothing (0.4s against
126,000 Parquet files). Three things keep that true, and each is easy to break.

- **Write through `Pipeline._write_rows`, never `storage.write_rows`.** It
  records the table (derived from the path) in `Pipeline.written`, and the CLI
  reloads exactly those tables into DuckDB. A new fetch method that calls
  `storage` directly writes a Parquet file the warehouse never loads — no
  error, just a table that is quietly one pull behind.
- **Anything built from "what this run fetched" must merge with disk, not
  replace it.** A run only collects from the endpoints it actually called, so a
  pull that skipped everything checkpointed sees almost nothing. The stat
  glossary was written this way and a single current-season pull cut it from
  140 keys to 94, losing exactly the box-score entries nothing would re-derive.
- **Scope league-wide sources to the seasons asked for.** NetPoints is one flat
  file covering every season, so there is no per-season request to skip — but
  there is the whole download to skip, and a pull of 2024 has no business
  rewriting 2026's file. It used to, and that one rewrite is what made an
  "everything is already complete" run rebuild the entire warehouse.

**Fetching is latency-bound, and the fetch path is threaded.** Profiling a live
pull put 96% of the main thread inside one curl call, at 5% CPU and zero bytes
read from disk; ESPN answers a cold game summary in 250-400ms against a 12ms
round trip. `--workers` (default 4) runs the per-item loops through
`Pipeline._map`. Two consequences for anything you add there: shared state on
`Pipeline` needs `_state_lock` (`written` and `glossary` already do), and
anything writing a path two workers might both write needs a unique temp name —
`storage.write_rows` handles that, but only because two games sharing a player
both cache that player's bio, and a shared `.tmp` let them interleave into one
file that then got renamed into place looking perfectly normal. `--rate-limit`
still bounds the request rate across all workers; raising it alone does nothing,
because the limiter never had to sleep in the first place.

**Filtering a read by season prunes no files.** The trees are laid out under
`season=X/season_type=Y` directories but read raw, not hive partitioned (the
reason is in the comment above `TABLES` — the directory names would collide
with the embedded columns), so `WHERE season = 2024` over `read_parquet` is a
filter applied *after* reading all 40,558 `games` files, not a way to read
fewer. `data check` counted one season/season_type per query on that
assumption and took 7.5 minutes for what one grouped scan does in 3 seconds.
Count once and group; do not filter in a loop.

**A full `warehouse.build()` is memory-hungry.** It needs
`preserve_insertion_order=false` (set in `_tune`); without it, loading `plays`
from 17,500 files dies at 12.4 GiB. Each table is its own statement, so an OOM
leaves the earlier tables replaced and the rest silently at their old contents
— the build fails loudly, but the *warehouse* does not look broken afterwards.
Row order carries no meaning in any of these tables.

It also needs `enable_external_file_cache=false` (same place). DuckDB keeps the
Parquet a statement read resident after that statement ends, and the cache
accumulates across the 18 loads a build runs on one connection. It is sized for
re-reading a few large files; this tree is the opposite shape — 208,000 small
ones — and it charges far more per file than a file holds. `games` alone (40,558
files, 320 MiB on disk) parked 5.6 GiB in it; a full build under a 6 GiB cap was
killed on that third table, and with the cache off the same build peaks at 3.0
GiB and is no slower.

**The two failures look nothing alike, and only one of them is DuckDB's.** The
insertion-order one raises `could not allocate ... (12.4 GiB/12.4 GiB used)`.
The file-cache one is a SIGKILL with nothing in the traceback: DuckDB was
accounting for 6.5 GiB of a 12.4 GiB budget when the kernel killed the process,
because `memory_limit` defaults to 80% of RAM and RSS runs ~2 GiB above what the
buffer manager tracks. So on a 16 GiB machine the limit is only reached well
past the point the process dies — dmesg is the only place that failure is
explained, and lowering `memory_limit` bounds the cache but not the overhead
above it (measured: 4 GiB limit, 6.7 GiB RSS). Turning the cache off is the
lever that works.

`union_by_name=true` is not the thing to reach for here even though it is what
makes many files expensive: dropping it fails outright on real schema drift
(`venue_id` is VARCHAR in the 1993 files and absent in others).

## Working on the web path

`association web` is a thin layer over the same `Agent` the CLI uses. Almost
everything about it is constrained by things measured elsewhere in this file.

- **The server answers one question at a time, and that is not caution.**
  ollama keeps a single KV cache slot per model, so two questions in flight
  evict each other's prefix and both come back slow (1.3s becomes 11.2s).
  `AgentRunner` holds a `threading.Lock` for the whole of `ask`. A
  `threading.Lock` and not `asyncio.Lock`: everything below is blocking, and
  FastAPI runs `def` endpoints in a threadpool already.
- **The trace sink is swapped under that lock**, which is the only thing that
  makes swapping it safe. If the serialization ever goes, that swap goes with
  it.
- **Nothing in `web/app.py` may import a model client.** `Answerer.ready` is a
  property on the runner precisely so the health check does not reach ollama
  from the API layer — that is what keeps the web tests offline by
  construction rather than by discipline. The whole suite still runs with no
  network and no ollama, and that has to stay true.
- **Events carry trace lines verbatim.** Do not parse `"-> (router) intent=..."`
  back into structured fields. Everything a client acts on — which path
  answered, the intent, the timing, the artifacts — is on the `answer` event,
  as values, because Phase 0 put it there. Parsing prose back out is the exact
  move that phase removed.
- **The page is one self-contained HTML file** with its CSS and JS inline, like
  the chart renderers' output. That is one asset to survive packaging, declared
  in `[tool.setuptools.package-data]`, and `serve()` checks it exists at
  startup rather than serving a 404 on the first request. Verify packaging by
  installing the wheel into a fresh venv *outside* the repo and loading the
  page — importing the module proves nothing about the HTML.
- **`fastapi`/`uvicorn` are the `web` extra**, so CI syncs `--extra web` and a
  missing install must print the `pip install 'association[web]'` line rather
  than raising ImportError.
- **`GET /api/artifacts/{name}` serves a directory a person owns.** The name is
  checked twice, and the two checks stop different things: an allowlist regex
  rules out anything shaped like a path, and resolving the file and requiring
  it to sit directly in the resolved output directory catches a symlink whose
  *name* is perfectly innocent. Keep both. Test the guard directly as well as
  through the route - measured, with the guard removed most traversal names
  still 404 because Starlette never matches a path parameter containing a
  separator, so a route-only test proves less than it looks like it does.
- **Chart iframes run with scripts off.** `court.py` and `radar.py` emit no
  script and a test asserts they still do not; if one ever needs to, the frame
  stops working and that test says why. `allow-same-origin` is load-bearing
  separately - it is how the page reads the chart's height to size the frame.
- **Print the URL with `flush=True`.** stdout is block-buffered when it is not
  a terminal, and with an ephemeral port that URL is the only way to find the
  server at all.

## Data gotchas

- **A season is named for the year it ends.** 2023-24 is season `2024`. See
  `season.py`.
- **NetPoints tables disagree with each other about `season_type`.**
  `net_points_player` uses its own *string* column (`net_points_season_type`,
  e.g. "Regular Season"); `net_points_player_game` and
  `net_points_player_game_fingerprint` use the normal *numeric* 2/3;
  `net_points_player_fingerprint` has no season_type at all. Filtering the
  string column with a numeric matches nothing, with no error.
- **Only six fingerprint categories partition the total**
  (`FINGERPRINT_PARTITION`): two_pt, three_pt, free_throw, turnover, rebound,
  foul. They sum to the season average almost exactly. The other 15 are
  overlapping slices — summing all 21 is meaningless.
- **The play-type split exists per game as well as per season, in a second
  file, and it was missed for months.** ESPN Analytics publishes two objects
  per date: `NBA/netpts/<season>/<date>.json`, which the pull already read, and
  `NBA/netpts/<season>/<date>_player.json`, which it did not. The first carries
  57 fields per player-game with exactly three NetPoints values among them
  (offense, defense, total) and counting stats for the rest; the second is long
  format — one row per player per game per action type, 31 types covering every
  category the season file holds plus nine it does not (`atb`, `bank`, `dunk`,
  `grenade`, and the dead-ball ones).

  Worth recording as a *method* failure rather than a data note. The season
  file's absence of a game id was read as "the source does not publish this per
  game", and the daily file's `assister` / `putback` / `corner`-shaped field
  NAMES were read as the taxonomy — but their VALUES are integer counts
  (`pts: 36`, `assister: 4`), not net points. Checking one file's schema and
  one file's field names, without checking a value against the season columns
  or looking at what the site's own page fetches, produced a confident and
  wrong claim about what exists. The site's per-game awards ("Facilitator" for
  net points passing, "Corner Pocket" for corner 3s) were the visible evidence
  against it the whole time.
- **`net_points_player_game_fingerprint` is LONG, and the only such table
  here.** One row per player per game per `category`, rather than the season
  file's 66 columns. Deliberate: the wide shape would be 93 columns and would
  change again the next time ESPN adds a category, and the categories are
  normalized to the season file's own column prefixes on the way in
  (`net_points_category`, over the existing `FINGERPRINT_CATEGORIES` map) so
  one skill list drives both tables. The query side pivots.
- **A single game's fingerprint is drawn in that game's net points, not per
  100 possessions.** Over ~30 possessions a per-100 rate turns one made corner
  three into a league-leading season figure. Its percentiles are ranked against
  every player-*game* in the season rather than against season rates, since a
  season average is the mean of games like the one being drawn and nearly any
  decent game would land in the 99th percentile against it. `Unit` carries the
  labels with the numbers so a per-game plot is never captioned "per 100 poss".
- **Each table starts in a different year, and the gaps are ESPN's, not ours.**
  A question is only answerable as far back as its *narrowest* table, and there
  is no pull that fills these in — verified live against three endpoints (the
  game summary, `core/.../plays`, and the athlete gamelog), all of which return
  empty for the years below, so `data check` reporting zeros there is correct.

  | Table | Usable from | What is before it |
  | --- | --- | --- |
  | `standings` | 1988 | — league-wide and real all the way back (23 teams in 1988, 27 by 1990) |
  | `games` (postseason) | 1988 | — full 16-team brackets all the way back |
  | `games` (regular), `player_box_stats`, `team_box_stats`, `team_season_stats` | **1994** | one team's 82 games per season, and nothing at all for 1989-90 |
  | `plays`, `shot_chart` | 2003 (2002 is ~half) | nothing |
  | `team_power_index` | 2017 | nothing |
  | `win_probability` | 2018 | nothing |
  | NetPoints (all five tables) | 2019 | the bucket answers 403 |

  Two traps in that table. **`shot_chart` is derived from `plays`** — both come
  out of the same game summary, so there is no separate shot source to fetch
  for 2002 and earlier. And **season 1993 is a phantom**: ESPN answers
  `season=1993` and `season=1994` with the identical 1,185 events (1993-11-06
  to 1994-06-23), so the warehouse holds the 1993-94 season under both labels.
  It is the only duplicated pair in the warehouse — every other season's event
  ids are disjoint — so treat 1994 as the earliest real regular season and
  1993 as a copy of it, not as evidence of a fetch bug. It is confined to
  `games` and what is derived from it; `standings` comes from a different
  endpoint and its 1993 rows are genuinely the 1992-93 season.

  **`player_season_stats` looks like an exception and is not.** It reaches back
  to 1977, because it is fetched per player over a whole career once that
  player is discovered — and players are discovered from box scores, which
  start in 1994. So the deep history is only the handful of careers that lasted
  into 1993-94: 5 players in 1977, 240 in 1988, 668 in 1994. It is a survivor
  sample, not league-wide coverage, and a leaderboard over it before ~1994 is
  measuring who played longest.
- Query connections to DuckDB are **read-only**, as a hard guarantee.

**Those floors are enforced, not just documented.** `association/coverage.py`
holds them as a table — `COVERAGE`, one entry per queryable table — and
`templates.check_coverage()` refuses a question that lands under one. Add an
entry whenever a template reads a new table, and declare the template's tables
in `TEMPLATE_SOURCES`; a template missing from it is one no floor can refuse.
Three things about that module are load-bearing:

- **It returns the refusal rather than raising it.** That is the opposite of
  `check_scope()`, and deliberate: `check_scope` raises so the question falls
  through to an agent that may do better, and nothing does better here. The
  agent would query the same empty tables, more slowly, and is then free to
  fill the silence from its own weights.
- **A lookup and a ranking have different floors.** `player_season_stats` holds
  Michael Jordan's real 1990 line, so his own average is answerable from it;
  ranking that season is not, because the pool is 217 players against a
  ~350-player league. In 1980 the pool is *seven* — and before this existed,
  "who led the league in scoring in 1980" answered "Moses Malone, at 25.8.
  Next: Bill Cartwright (21.7)". Kareem, Bird and Erving are not in `players`
  at all. `first_ranking_season` is that second floor, and `RANKING_INTENTS`
  says which templates it applies to.
- **A missing season and an unrepresentative one need different sentences.**
  Saying "there is no data for 1980" about a warehouse holding Moses Malone's
  real 1980 line is the same false-cause answer in the other direction, which
  is why `Floor.unrepresentative` exists.

Seasons that exist but only partly (2002 play-by-play is ~half a year) are
answered with a caveat instead, and a *phantom* season — 1993, whose rows
duplicate 1994 — is declared as such so the checker can verify the duplication
rather than read a full-looking season as a floor set too high.

`scripts/check_coverage.py` verifies every floor against a built warehouse,
the same way `check_nicknames.py` does for the nickname table: these are claims
about the data, and pytest runs offline. Run it after editing `COVERAGE` and
after any pull that reaches further back than the last one. "Usable" there is
deliberately not "present" — 82 games where a league plays 1,100 is rows, not
a season.

## Verifying your work

The habits that caught real bugs here, in rough order of how often they paid:

- **Read the fixture, do not guess what it contains.** Several wrong test
  assertions came from assuming a shot count or a made/attempted split.
- **Exit 0 is not proof.** A Sphinx build passed `-W` with the version variable
  silently deleted, because `release` is optional. Check the rendered output,
  the built artifact, the actual string — not the return code. And beware that
  `cmd | tail` reports *tail's* status: a failing `data load` read as exit 0
  that way, twice, before it was piped to a file instead.
- **Check rendered HTML output in a real browser engine.** The chart pages
  paint from CSS variables, and WeasyPrint does not resolve them inside SVG —
  it drew the fingerprint's blue polygon gray and its group-colored labels
  black, which would have shipped as a wrong screenshot. A headless Chromium
  renders them correctly (the `docs/_static` shots are made that way).
- **Install and run it, for anything packaging-related.** The wheel and sdist
  are verified by installing into a fresh venv *outside the repo* and running
  the CLI; nothing else proves the entry point and `py.typed` survived.
- Prompt text is load-bearing. If a refactor touches it, hash the prompt
  constants before and after and compare.
- **A same-size edit inside one second can be served from a stale `.pyc`.**
  Python validates its bytecode cache on `(mtime_to_the_second, size)`, so
  rewriting `partial=(2002,)` to `partial=(2005,)` and re-running immediately
  gets the OLD module. This cost a real debugging detour: a checker was
  "failing" on a source file that was already correct. When a script rewrites a
  module and re-runs it — perturbation tests especially — clear `__pycache__`
  or set `PYTHONDONTWRITEBYTECODE=1`.
- **A guard is worth nothing until you have watched it fail.** Every check
  added here was confirmed by perturbing what it claims to protect and seeing
  it catch that: the coverage floors by moving each one, the router's `side`
  slot by removing the hook. Two "passing" perturbations in this session were
  actually a stale `.pyc` and a `SyntaxError` in the harness — both of which
  look exactly like a green run from the outside.
- **A DuckDB `SET` is per-connection.** A test asserting one has to observe it
  on the connection the code under test used; a freshly opened connection
  reports the default and the assertion looks like a real failure.

## Releasing

`docs/releasing.rst` has the procedure. Short version: describe the change
under `## Unreleased`, then `scripts/bump_version.py minor --tag`, push, then
`scripts/release.sh X.Y.Z`. Nothing before the final step is irreversible.

**The PyPI upload currently fails, and that is expected.** Trusted publishing
answers `invalid-publisher` because no publisher is registered for this
repository on PyPI, pending an account-access issue — it is not a workflow bug
and not something to "fix" by adding a token or making the job tolerate
failure. The `build` job still runs the whole gate suite and attaches the wheel
and sdist to the GitHub release, which is where releases live for now
(`README.md` and `docs/installation.rst` say so, in notes written to be deleted
in one commit). Every version tagged so far is still uploadable under its own
number once the account is back.

Before bumping, check that anything added or reshaped on the public surface
carries a `.. versionadded::` / `.. versionchanged::` for the version about to
go out. `git diff v<previous>..HEAD` over `src/` is the honest way to find them;
the API pages are generated, so an unmarked change simply appears with no
history rather than failing anything.
