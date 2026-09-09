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

**A full `warehouse.build()` is memory-hungry.** It needs
`preserve_insertion_order=false` (set in `_tune`); without it, loading `plays`
from 17,500 files dies at 12.4 GiB. Each table is its own statement, so an OOM
leaves the earlier tables replaced and the rest silently at their old contents
— the build fails loudly, but the *warehouse* does not look broken afterwards.
Row order carries no meaning in any of these tables.

## Data gotchas

- **A season is named for the year it ends.** 2023-24 is season `2024`. See
  `season.py`.
- **NetPoints tables disagree with each other about `season_type`.**
  `net_points_player` uses its own *string* column (`net_points_season_type`,
  e.g. "Regular Season"); `net_points_player_game` uses the normal *numeric*
  2/3; `net_points_player_fingerprint` has no season_type at all. Filtering the
  string column with a numeric matches nothing, with no error.
- **Only six fingerprint categories partition the total**
  (`FINGERPRINT_PARTITION`): two_pt, three_pt, free_throw, turnover, rebound,
  foul. They sum to the season average almost exactly. The other 15 are
  overlapping slices — summing all 21 is meaningless.
- Query connections to DuckDB are **read-only**, as a hard guarantee.

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
