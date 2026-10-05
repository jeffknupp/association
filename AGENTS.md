# Working on `association`

Orientation for agents (and people) making changes here. It covers what is
*not* obvious from reading the code: the gates, the conventions that are
enforced, and the specific shapes of bug this project keeps producing.

For how the system is designed — the three stages, the parser in front of the
templates and the compiler, why templates instead of better prompting — read
`docs/architecture.rst`. That is
the source of truth for design, and this file does not restate it.

Where the work is going - the goal, where it stands, and the next steps in
order - is `ROADMAP.md` (accepted 2026-09-30: the four-stage pipeline, READ
-> PLAN -> RUN -> SAY, and the phases that reach it). **Its rules bind every
change to `query/` now, not only the roadmap's own work: "While the pipeline
is rebuilt", below.** The roadmap before it is `ROADMAP-2026-09.md`,
archived verbatim: "plan item N" and "ROADMAP step N" in code, commits and
`CHANGES.md` cite that file. What each earlier spike measured, bought and
cost, as written at the time, is `ROADMAP-HISTORY.md`; its last section,
"The rewrite's working log", gets an entry as each step of the current
roadmap lands (what it measured, what moved, what it cost), because the
roadmap's own status tables are rewritten as the work moves. Read the roadmap before
starting a spike, and keep a spike pointed at it: measurements turn up
fixable things, and those go to agents or to `ISSUES.md`, not into the
spike.

What is known to be wrong, missing or unverified is in `ISSUES.md`, ranked by
priority. Read the entries for the area you are about to touch, and add what
you find to it, including findings that are not part of your task (see
"Recording findings").

What the *source* does — ESPN's wrong values, missing games, odd labeling and
per-table history — is catalogued separately in `DATA.md`. `ISSUES.md` is what
we do about it. Read `DATA.md` before trusting a column.

## While the pipeline is rebuilt

`ROADMAP.md` replaces the query path's half-ported middle (templates,
presenters, adapters, six scoping declarations) with four stages. Until its
Phase 4 these rules apply to every change under `src/association/query/`,
whoever makes it. Each is a gate or a test, so a change that breaks one
fails rather than drifts.

**This is a rewrite of how the data is used, and it is worked like one.**
Jeff's standing instruction, 2026-10-04, to every agent and subagent on
this tree:

- **Make the large change when it is the right one.** The pipeline is
  being replaced, not patched: a change that moves a whole shape, deletes
  a module or reshapes a type is the expected size here, and "ambitious"
  is not a reason to shrink it. The test of a change is that it is
  technically right and proved on the populations above, not that it is
  small.
- **Do not route around essential complexity.** Where the problem is
  complicated - a shape shared by three intents, a read with thirty
  helpers - the work takes as long as it takes, and the gates (radon,
  xenon) are met by splitting it into named steps, never by leaving the
  hard part where it was. A step that avoids the difficult half and ports
  the easy one is how the middle got half-ported.
- **Fix what you can reach, whether or not it is your task.** An agent
  that can make an immediate improvement in the code it is passing through
  makes it, in its own commit, gated like any other. A bug found is fixed
  as part of the work in hand wherever that is possible; `ISSUES.md` is
  for what you CANNOT fix as part of the work you are doing - not for what
  would be inconvenient, out of scope or larger than you expected. The
  twenty-line budget under "Dispatching agents" is gone for the same
  reason.
- **Report every such finding and fix** - to Jeff, or to the lead agent
  that dispatched you - in the change's report, with what was measured.
  Silence about a fix is as bad as silence about a bug: the next decision
  is made on the report.

- **New shapes are frozen** (decision D4). No new intent, template,
  presenter, scoping table or per-intent renderer.
  `tests/query/test_frozen_shapes.py` holds the 25 intents the reader can
  name, and since 2026-10-02 the 12 templates, the presenters (12 until
  2026-10-03; the intents holding one are 8 since 2026-10-04, as the game
  log, `record_when`, `player_splits` and `period_split` went to readers and
  sayers, 6 once `threshold_count` and `single_game_high` did, 4 once
  `streak` and `player_matchup` did, 3 once `leaderboard` did, and none
  once the season line's `player_stat`, `player_history` and
  `player_compare` did, 2026-10-05: `PRESENTERS` and `present()` are gone)
  and the team-only one, the adapters
  (10; 5 since 2026-10-04, 3 once `threshold_count`'s and
  `single_game_high`'s default points went to the reader, 1 -
  `with_without`'s - once the streak's and the matchup's did, and none
  since 2026-10-05: `compose/adapt.py` is deleted and every default point
  is `point.DEFAULT_POINTS`'s, so that freeze retired), the 14
  scoping declarations (by module
  and name, read from the source; 12 until 2026-10-03, when the two tables
  of cells a reader refuses that its name pattern missed joined) and the
  page's 22 renderers: each
  retires with its slice, none is added. A P1 wrong answer is
  still fixed, in the code that exists. The sections below still describe
  how templates, intents and presenters work, because they are what runs;
  they are not an invitation to add one.
- **What the roadmap is deleting may not grow.** `scripts/check_ratchets.py`
  lists today's violations of each direction by name
  (`scripts/ratchets.json`): a function outside the reader that takes the
  question's text (any parameter holding it, not only one named
  `question`), how many statements each module executes (105 in 19
  modules; counted per module since 2026-10-02, because a listed module
  could grow statements freely; `.execute`, `.executemany`, `.sql`,
  `.query` and the package's own string-SQL helpers since 2026-10-03,
  when a statement through `entities._read_table` counted as none), a
  private name `compose/` takes from `templates/` (imported by name or
  read off a module imported whole), a module outside the reader that
  imports `re` by any route or takes a reader's private pattern, and a
  reader function that takes a DuckDB connection (24). It fails
  on a NEW one or a count that grew, and on a listed one that is GONE or a
  count that fell, so the lists only shrink. A NEW failure is fixed in the
  code - pass the Reading, not the question; narrow through the relation's
  shared steps, not a new `execute` - and adding to the list is Jeff's
  call. A GONE failure is the ratchet working: run
  `scripts/check_ratchets.py --shrink` (it only removes and lowers) and
  commit the shorter list with the change.
- **The reader does not import the answer side, and the answer side never
  reaches the model** (`[tool.importlinter]`). The reader's imports of
  `compose`, `templates` and the relations were listed by name, and the
  list is empty since 2026-10-05: the contract holds with no ignored edge
  (nine when it was written; the last, `point -> compose.adapt`, went
  with the last adapter, whose default point the reader now reads itself,
  `point.DEFAULT_POINTS` - the rest were vocabulary and intent sets that
  now live on the reader's side: `entities.team_named_in`, `reading`'s
  intent sets, limits, `ordinal_word` and `Unsupported`,
  `measures.STAT_ALIASES`, `PERIOD_COLUMNS`, the metric and measure
  aliases and `resolve_metric`/`stat_measure`). A new one fails; an
  ignore is not the way past it.
- **A change to the pipeline is proven stage by stage, on two
  populations.** `scripts/stage_snapshots.py run OUT.jsonl` answers the 628
  recorded questions through the whole agent with no model (the
  normalizer's recorded replies, the date pinned, DuckDB single-threaded,
  about two minutes) and writes what each stage produced: the reading
  (with the reader's own verdict on the point: declined, or the cause it
  refuses by), the query the compiler ran (the planned one, except where
  the season line's presenter declined and `games_reading` re-read it:
  3 of 628), the result's values, the answer. `compare` reports
  the first stage each question differs in and exits 1. The second
  population is every call the unit tests make across a stage boundary:
  run the suite on each tree with `ASSOCIATION_STAGE_CALLS=<dir>` and
  `compare-calls` the two directories. Three things to hold to:
  - **Run the "before" tree, not a stored file.** The baseline is
    `PYTHONPATH=<before>/src ... run before.jsonl`, and the first line of
    each file says which copy of the code it read; check it. Measured
    2026-09-30: two runs of one tree are identical on both populations
    (628 of 628 questions, text included; 1,363 of 1,363 calls), so ANY
    difference is the change.
  - **Identical means identical.** `--values-only` leaves the sentences
    out, and is only for a change the roadmap allows to reword an answer
    (a Phase 2 sayer). Everything else compares text and all. One
    permitted move, Jeff's rule (2026-10-05): a decline the user sees
    ("Nothing here answers this question: ...", `answered_by="refused"`)
    becoming a `reading.Cause` the planner says - which changes the
    reading's verdict (`point_declined` -> `point_refusal`) and the
    answer's `answered_by` - is done as ONE commit of its own, with every
    moved reading and answer enumerated in the commit message and
    `CHANGES.md` (the population, the count, before and after), the
    sentence identical or the change named. A Cause is what Phase 3
    reads; a decline is a dead end the user gets no help from.
  - **A boundary that moves is recorded on both sides first.** A phase
    that replaces `compose.answer` adds the new boundary to
    `tests/stage_calls.py` before it deletes the old one.
- **A word the reading does not depend on is counted, and a number among
  them is a bug.** `scripts/claims_ledger.py run` reads each recorded
  question whole and again with each content word deleted (no model, about
  twelve seconds since names come from the index), and reports the words whose deletion leaves the reading
  and the planned query unchanged. It measures from outside the reader, so
  the count means the same before and after the reader is replaced.
  Baseline: 690 of 2,409 content words in 391 of 628 questions, most of
  them words that restate the shape ("average", "record", "stats"),
  measured on the replies re-recorded on the OVH devbox (2026-10-02;
  below). It read 683 at `42e95d3`: seven words ("points" in "when maxey
  scored 20+ points", "shot" in "shot distance") counted as read only
  because the parser's decision records named the slots a second stage
  run had moved; with the stages run once there is no second run to
  list, and the reading and the query are the same without them. On the old box's replies it was 679 of 2,415 at
  `011091f` and 684 once the one reader of companions no longer needed
  the verb in "when Embiid and Paul George play". Nine questions have an unread NUMBER, and three of
  those were fluent answers to a broader question (`ISSUES.md`). After a
  reader change, run it and read the unread numbers first; the count may
  not grow. It is blind to a word only the model could have dropped, and
  it overcounts a word a rule matched and did not need ("games" in "last
  10 games").
- **A caveat, a stated default or a definition is written through
  `query/notes.py`.** `note(kind, text, **facts)` for something about the
  data or a term the answer uses, `decided(kind, text, field=, chose=, ...)`
  for something the question left open and the system chose ("could the
  question have said it differently?"). Both hand the sentence back
  unchanged and, inside `Agent.ask`, record the kind and facts on the
  answer (`Answer.notes`, `Answer.decisions`) and in the stage snapshot's
  `remarks`. The 28 kinds and each kind's fact names are closed
  (`NOTE_KINDS`, `DECISION_KINDS`, `FACTS`): a new remark uses one, and
  adding a kind or a fact name is a deliberate edit there. Facts are plain
  values, never a sentence or an object. Wrap at the writer, once, not
  where the sentence is attached. `scripts/stage_snapshots.py remarks`
  lists a remark that was written and never reached its answer;
  `compare` holds the remarks identical unless `--ignore-remarks` is
  given, which is only for a change whose point is to record more.
- **The target types are `ROADMAP-TYPES.md`**, a draft until Jeff has
  reviewed all of it (its "Decided" and "Still open" say which parts):
  the Reading's typed filters, the Measure, the Query, the Result, seven
  shapes, decisions apart from notes. A type is declared in code by the
  phase that first uses it, not before.
- **Every step deletes the path it replaces, in the same change.** No
  dispatcher between an old and a new implementation outlives its slice;
  that is how the middle got half-ported.
- **A test goes with what it tests.** A test whose subject is a structure
  the change deletes (a presenter, a scoping table, a template's exact
  sentence, the stages' intermediate slots) is deleted with it, not ported.
  A test of behavior a user can see moves to the stage that owns the
  behavior, and only if the stage snapshots do not already hold the case.
  The change's report says how many tests it deleted and how many it moved.
- **`AGENTS.md` changes with the code.** A slice that deletes what a
  section here describes rewrites that section in the same change; the
  roadmap does not leave it for the end.

## Before you commit

```bash
uv run pre-commit run --all-files   # all eighteen gates
uv run pytest -q -n auto            # fully offline: no network, no ollama
```

**While iterating, run `scripts/check_fast.sh` instead** - every gate except
the Sphinx build, plus the whole test suite, in parallel. Measured on 24 cores
with nothing else running (2026-10-02, the OVH devbox, Python 3.14; on the
8-core box before it the same five were 42s, 6s, 29s, 184s and 34s):

| command | time |
| --- | --- |
| `scripts/check_fast.sh` | 24s |
| the hooks it runs (all but Sphinx) | 4s |
| `uv run pre-commit run --all-files` | 17s (13s of it Sphinx) |
| `uv run pytest -q` | 87s |
| `uv run pytest -q -n auto` | 20s |

So the full check above costs about 37s and the iteration check about 24s.
The fast one leaves out exactly one thing, and its own header says so: the
docs build, which is the gate that catches a malformed docstring. A commit
touching a docstring runs the full check. Nothing else may be skipped.

There is no `slow` marker any more. It covered one test, the Eastern-date
agreement check, which spent 60 of the suite's 68 seconds inserting 140,256
rows through DuckDB's `executemany` - one statement per row. Registered as a
single Arrow table, the same rows take half a second. Before marking a test
slow, find out where its time goes.

**A fresh worktree needs syncing before either command works at all.**
`uv run` creates the venv on first use but does not install the `dev`, `docs`
or `web` extras, so `uv run pytest -q` fails with `Failed to spawn: pytest`
and the gates never run. Run CI's own line first:
`uv sync --frozen --extra dev --extra docs --extra web`.

**The interpreter is pinned: `.python-version` says 3.14, and `uv` reads it
here and in CI.** Until 2026-10-02 nothing pinned it, `requires-python` said
`>=3.10`, CI ran on its runner's own 3.12 and a new devbox took 3.14 - where
one test was red with nothing wrong in the tree, because `names.sql_lower`
leaned on the interpreter's Unicode tables. Moving the pin is one change:
`.python-version`, `requires-python`, and the ruff, mypy and pyright targets
in `pyproject.toml`, then `uv lock`. **A Python script a gate runs goes
through that interpreter by name** - `.venv/bin/python scripts/x.py` in a
hook, `uv run python scripts/x.py` in a workflow - never through its
shebang: the day the pin landed, three scripts CI ran bare were parsed by
the runner's own 3.12, one held a 3.14-only `except A, B:`, and CI was red
for three pushes while every local gate passed (the hooks run under `uv
run`, which puts the venv first on `PATH`). After a push, read the CI run.

Both must be clean. Everything in `pre-commit` also runs in CI
(`.github/workflows/ci.yml`), so a green local run means a green PR.

One CI check is deliberately *not* a hook: `scripts/audit_dependencies.sh`
(pip-audit over `uv.lock`, extras included) needs the network, and commits
here work offline. It runs in `.github/workflows/audit.yml` on every push and
weekly, so an advisory against an unchanged lock still turns up. Run it by hand
after changing `uv.lock`. An accepted advisory goes in its `IGNORED` list with
a comment saying why it does not apply.

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

Some things about the gates surprise people:

- **The docs gate rebuilds from scratch (`-E`) and then reads the HTML.**
  An incremental Sphinx build re-reads a page only when a source it knows
  about changed, and a function patched in `docs/conf.py` is not one: the
  `:rtype:` shim there was invisible to an incremental build (18 of 40 pages
  kept the literal line, 0 in a fresh build), and a page not re-read also
  re-emits none of its warnings, so `-W` passed locally where CI's clean build
  would not. `-E` costs 11s against 2.7s, and buys a gate that says the same
  thing here as in CI. `scripts/check_docs_markup.py` then fails the build on
  a docstring field marker printed as text (`:rtype:` after a line of prose),
  which is valid reStructuredText and so nothing `-W` can see.
- **The docs gate deletes `docs/api/generated/` before building.** autosummary
  writes a stub page per module and never removes one, so after a module moved
  the stale stub still named the old path: autodoc failed to import it locally,
  while CI, which starts with no stubs, passed. The directory is gitignored and
  regenerated every build.
- **The hook lints more than CI's ruff step.** CI runs `ruff check src tests
  scripts`; the pre-commit hook lints every tracked Python file, `docs/conf.py`
  included. Run the hooks, not the CI line, before calling lint clean.
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

**There must be exactly one `## Unreleased` heading, and the same hook now
checks that too.** Two parallel branches each adding one merge *without a
conflict* - git sees an insert in two places, not a clash - and the changelog
ends up with two Unreleased sections. `bump_version.py` renames the first and
silently leaves the second behind, so the next release ships a changelog with an
orphaned section in the middle of its history. Nothing caught this: the older
rule only asked that the file was touched. Found by reading the file after a
merge, which is not a gate; now it is one.

**Every released version keeps its own heading, checked by the same hook.** A
fix for an entry that landed inside the last release's section renamed
`## 4.3.0` to `## Unreleased` instead of adding a heading above it, and 4.4.0's
notes then held all of 4.3.0's too. The hook checks the heading for the version
in `pyproject.toml` everywhere and for every tag where the clone has tags (CI's
shallow clone has none, and says so).

**Adding a gate** follows the shape of the ones already there, so local and CI
keep saying the same thing: pin the tool in the `dev` extra (`uv add --optional
dev <tool>`, which also updates `uv.lock`), add a `language: system` hook that
runs `.venv/bin/<tool>` with its settings in `pyproject.toml` (or the tool's own
rc file), add the matching `uv run <tool>` step to `ci.yml`, and update the
count on the `pre-commit` line above. Then break what it guards and watch it
fail (see "Verifying your work"). Every finding it reports on arrival is fixed
or suppressed with a written reason in the same commit - a gate that starts red
gets turned off.

## Conventions

- **Line length is 200, and `ruff format` is enforced.** This is deliberate:
  much of the code is prompt text and SQL that reads worse wrapped. The
  formatter inherits the same setting, so it *joins* long strings rather than
  fighting them.
- **A lint suppression carries its reason.** Beyond ruff's defaults the lint
  selects `DTZ`, `BLE`, `RUF`, `PERF`, `C4`, `SIM`, `RET`, `PLW` and `PLE`
  (`pyproject.toml`). The few places that break one on purpose - a blind
  `except` at a boundary that must not die, the machine's local date in
  `current_season` - say why on the same line: `# noqa: BLE001 - ...`. A bare
  `noqa` gives the next reader nothing to judge it by.
- **Dead code is a gate (vulture).** Something only a framework calls - a
  Click command, a FastAPI route, a pydantic field - or public API nothing here
  calls goes in `vulture_whitelist.py` with a comment naming its real caller.
  Tests count as callers, so a function kept alive only by its own test passes
  the gate; delete it rather than relying on that.
- **Import only what is declared (deptry).** A package imported directly
  must be named in `pyproject.toml`, even when another dependency already
  pulls it in - `botocore` arrives with `boto3` and `pydantic` with `fastapi`,
  and both are declared anyway, because a release of either that stopped
  bundling them would otherwise break at import time with nothing in this repo
  having changed. A dev or docs tool goes in its extra, never in the core list.
- **No function is more complex than radon grade C** (cyclomatic complexity
  20), no module worse than C, and the average no worse than B - a xenon gate.
  The way past it is the one the templates and the router's stages took: split the body
  into named steps called in the original order, each step keeping the comment
  that explains it. A pure refactor here is proven by a golden comparison, not
  by the suite alone (see "Verifying your work").
- **Every module lives in a package; the root of `src/association` holds only
  `__init__.py` and `py.typed`.** Modules had drifted to the root because both
  `fetch` and `query` needed them; that is what `nba/` is for. Where things go:
  - `cli/` - the `association` command (`commands.py`) and the default
    warehouse and data paths the scripts share (`paths.py`).
  - `nba/` - what both `fetch` and `query` need to know: seasons and Eastern
    dates, franchise names by season, coverage floors, NetPoints categories.
    It imports nothing else from `association`.
  - `fetch/` - ESPN and NetPoints clients, parsing, the pipeline and the
    warehouse build; `fetch/repairs/` - load-time repairs of ESPN's faults and
    the tables built beside them.
  - `check/` - the coverage report. `query/` - the reader (`parse.py`, with
    what the model sees in `normalizer.py` and the stages it runs in
    `router.py`), templates, entities, renderers, the answering loop
    (`agent.py`). `web/` - the local web interface.
- **The package layers are a contract.** `cli` > `web` > `query | check` >
  `fetch` > `nba`, with `fetch` and `query` independent and the core free of
  the `web` extra's packages (`[tool.importlinter]`). A new module that needs
  to sit somewhere else changes the contract, with a reason, rather than an
  ignore. Inside `query/` three more contracts hold the reader apart from
  the answer side ("While the pipeline is rebuilt"); their `ignore_imports`
  are a list of what is left to cut, not a place to add.
- **Call-time imports are absolute.** A `from .fetch import warehouse` inside a
  function resolves against wherever the module lives *when it is called*:
  moving `cli.py` into `cli/` turned seven of them into imports of
  `association.cli.fetch`, which fails only when the command runs - the import
  of the module itself was fine. Write `from association.fetch import ...`
  inside functions.
- **Shell scripts pass shellcheck with every optional check on**
  (`.shellcheckrc`). The one that matters is SC2312: a command substitution
  nested in another command's arguments, or in a heredoc, fails without
  tripping `set -e`, so assign it to a variable first. Braces on every
  variable are style, taken for consistency.
- **American spelling.** "defense", "offense", "serialize". British spellings
  drifted back in twice after being removed wholesale in `c09d6f7`, so
  codespell now enforces it (`en-GB_to_en-US`, `[tool.codespell]`). A word it
  flags that is right - a basketball token, a regex stem, a misspelling a test
  depends on - takes `# codespell:ignore <word> - <why>` on its line, or joins
  `ignore-words-list` if it recurs. `CHANGES.md` is skipped: released entries
  quote the spellings that were fixed.
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
  Use the version being released next, not the current one - and work out
  which that is from `git tag` and what `## Unreleased` already holds, not from
  memory: 2.2.0 went out with directives naming 2.3.0 and 2.1.1, versions nobody
  released, which a pre-release audit had to correct. While `## Unreleased`
  records a breaking change, a new directive says the next *major* version -
  and note that the number can move under you, so **re-check every directive
  added since the last tag as part of the pre-release audit**
  (`git grep -n 'version\(added\|changed\):: '` over the diff). Three agents
  working in parallel were told 3.1.0, correctly at the time; a breaking change
  in a fourth branch made the release 4.0.0 and six directives had to be
  rewritten before the bump. An agent cannot know a number that another
  branch has not settled yet. Only the public
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
`HONORED_SCOPING` in `query/templates/common.py`, which make a template declare which
scoping slots it honors and raise on the rest, instead of silently ignoring
`order` or `date` and returning a whole-season answer to a single-game
question.

**A template on a relation does not declare, or apply, scoping of its own.**
The player-games relation (`query/player_games.py`) and the team-games relation
(`query/team_games.py`) each carry the narrowing once - opponent, venue, date,
span, since, without, split, game_n, season_n, below/above, a quarter or half
(`period`/`half`, which changes what a read SEES of each game rather than which
games), and order+limit as a window cut after every other filter - applied in
the shared steps
(`scoped_player`/`scoped_games`, `scoped_team`/`team_games`,
`condition_player`) and declared once (`RELATION_SCOPING`, with a reasoned
per-cell `RELATION_SCOPING_EXCLUDED`; `HONORED_SCOPING` entries for those
templates are `_relation_scoping(intent)`). Two source-reading tests in
`tests/query/test_templates.py` enforce it, and they were watched to fail: a
template on the relation that lists its own frozenset, or that writes
`pgl.opponent_team_id = ?` or `g.date >= ? AND g.date < ?` anywhere it reaches,
fails the suite. So a new scoping dimension is one clause on `Narrowed` plus a
warehouse-verified test per template it turns on - never a slot taught to one
template at a time, which is how twelve slots ended up honored on `game_log`
and one on `single_game_high` over the same relation. An exclusion's reason is
about the answer ("one game is not a run"), never about the code ("not wired").

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
cleanly. The nickname version of this was known first (the router filled "The
Answer" in as Klay Thompson); the general version is that **any** name a model
supplies may be fiction - the router's then, the normalizer's now.

So a name is checked against the question before a template reads it:
`subject.read_subject` reads who the question is about from its own spans -
ONCE per question, in `parse.read_route`, which carries that reading on the
route (`Route.subject`); the parser's child step and its last step settle it
under the intent the stages chose (`subject.settle_subject`, which reads no
name and asks the warehouse nothing) - and `subject.apply_subject` writes
those names into the scope (until 5.0.0 this was
`entities.override_invented_players`) inside the parser's last step,
`parse.reading_from_route`, whose `Reading` is all the agent answers from.
A route with no subject is refused there (`ValueError`), not read for
again: until 2026-10-02 a recorded route replayed through
`Agent.ask(route=...)` had its subject read in that last step, on the
settled scope instead of the one `read_route` reads from, and the two paths
disagreed on 9 of 12 wordings naming a franchise's old name. `Agent.ask`
takes a question and nothing else; a test that needs a particular route
stands in for `read_route` (`tests/routed.py`: `ask_routed`,
`with_subject`), and a harness answers recorded QUESTIONS with their
recorded normalizer replies, as `scripts/stage_snapshots.py` and
`scripts/preview_answers.py` do. Do not
add a second `read_subject` call to the live path: three readings of one
question is what this replaced, and they disagreed on 18 of the 628 recorded
questions. What counts as the question
supporting a name (`subject.question_supports`) is deliberately generous, because the router's expansions are usually the
useful kind: the word itself, a near spelling of it (the router silently
corrects typos; measured as Damerau-Levenshtein, rapidfuzz's, the metric the
entity index uses in DuckDB - a swapped pair of letters is one edit, and a
second metric here once refused "jokci stats" that the index read as Jokic),
a nickname, or the initials ("KAT", "SGA"). Any ONE word of
the name is enough, since half a name is how a question normally carries one -
what this catches is a name with no half in the question at all. Three rules
about what happens next, and the third is the one that was got wrong first:

- **Replace only from what the question itself names, and only when the count
  is exact.** `players_named_in`
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
  downstream can see it. The model choosing between ten Browns is still a
  guess nothing can see, so the completion is still undone. The parser cuts
  a model's completion back to the part the question holds and lets normal
  resolution decide (`parse._as_typed_part`); a nickname the question used
  ("steph curry") and a name the question's own span resolves to are left
  alone, and `find_players` applies the nickname table first, so "luka" still
  resolves rather than asking. The cut runs on the model's own spelling,
  before the reading respells it: run after the reading instead, as the
  router-era `entities.undo_name_completion` was, it read a typo'd "Bam
  Adeyebu" - corrected to Bam Adebayo - as a question holding only "Bam",
  and asked which Bam (ROADMAP plan item 6, step (d), part 3c).
- **When it cannot be repaired, say so by name.** This one shipped wrong
  first, on the reasoning that the fall-through agent of the time at least
  read the question. Measured, that was far worse: "compare fingerprints for
  embiid vs jokic in 2026" fell through and the agent spent 55 seconds
  writing a confident fingerprint for **"Ronaldo Lopes"**, a player who does
  not exist, with play-type percentages attached - the lesson
  `check_coverage` already carries, an agent with nothing to find fills the
  silence from its own weights, and the measurement that retired the agent
  (5.0.0). Without it the difference is between a refusal that names the
  player and the plain refusal for want of a reading, which names only the
  slot. Refuse only where the template would actually be about
  that player (`PLAYER_INTENTS`, checked against the templates' own source): a
  stray name on a `head_to_head` question changes no answer, and refusing over
  it would break a question that works.

**A model drops names, not only invents them.** "Compare fingerprints for
embiid vs jokic" arrived from the router as a single `player` slot, so the
answer was one polygon where two were asked for - a narrower question,
answered without saying so. The parser's reading takes the names from the
question itself (`subject.read_subject`), so a normalizer reply that drops the
second name still draws both, with or without a comparison word; and it reads
an ordinary word as nobody, so "plot jokic's fingerprint from his best season"
does not draw Travis Best a polygon, though "best" is his whole surname by
`players_named_in`'s rules (`tests/query/test_one_writer.py`). The router-era
`restore_dropped_players` put a dropped name back only for a question that
said it compared something, for exactly that reason, and so lost the second
name of "plot jokic and embiid fingerprints".

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
it). A suggestion naming more than `MAX_CLARIFY_CANDIDATES` players is dropped
entirely, because a name near 25 players narrowed nothing and reading out a
directory is not a suggestion.

**Typos are the entity index's job, never the model's**, so one near spelling
is an answer, not a question. Once names reach resolution as the question typed
them (ROADMAP plan item 6, step (c)), every typo would otherwise become a "did
you mean". `entities.read_near_spelling` takes the near-spelling pass's result
when it holds exactly ONE player, for `resolve_player` and the chart resolver
alike, and says so under the rule below: "('embid' matches no player exactly
and was read as Joel Embiid, the only near spelling on record - spell the name
exactly to ask about someone else.)" Three things still ask. Two or more near
spellings ("jolic": Jokic or Jovic). The surname back-off: in "Jemel Embiid" or
"Larry Bird" the given name is somebody else's, and the one player a surname
lands on is as likely to be the wrong man (Larry Bird is not in `players`;
Jabari Bird is) as the right one - only the near-spelling pass, which needs
EVERY word close, defaults. And a team's name ("Hawks" is one edit from Spencer
Hawes). It is for a span already given as a name slot, never for the
question's leftover words ("season" is one edit from Tari Eason, above). Measured: of 10,602 one-letter
drops, doubles and swaps of the 591 2026 players' surnames, 3,597 default -
every one to the player misspelled - and 2,731 still ask. The cost is a name
that is right but not on record reading as one that is: of 36 famous pre-1994
names absent from `players`, "Willis Reed" reads as Willie Reed and "Bernard
King" as Gerard King, each saying so.

**A reasonable default beats a question, where the default is visible and can
be corrected.** Jeff's rule, 2026-09-21, and it supersedes the older stance
here that a bare surname always asks: *"A default that chooses reasonably but
happens to be wrong is better than no answer as long as it can be easily
corrected. If the query returns Dean Wade and I have no way to refer to Dwyane
Wade, that's the only time there's an issue."* Two conditions, both required:
the answer **displays the value it used**, and there is **a wording that
reaches the alternative** - which the answer states. A silent default is still
this project's worst failure shape; this is about defaults that say what they
did.

The first application is names. With no season asked about, a name several
players share means the one who played the last season of the span - "Maxey" is
Tyrese, and "show maxey's games against boston in the past two seasons" no
longer asks about Marlon, who retired in 1994 (`entities.resolve_player`). A
name given in full yields the same way when its owner has nothing in the seasons
asked about and exactly one namesake does: "Jabari Smith" for 2026 is Jabari
Smith Jr., where it used to answer "no 2026 games" about the father
(`_named_in_full`). Both say so in the answer - "('maxey' was read as Tyrese
Maxey, the only match who played in 2025-26. Marlon Maxey also matches - use the
full name, or name a season he played, to ask about him.)" - carried by
`entities.collect_name_readings` and attached in `agent.py`, so a template called
directly does not show it. The sentence is not optional: of 391 surnames two or
more players share, 124 now resolve, and in 67 of those a retired namesake has
more games on record than the active one ("wade" is Dean Wade, "pippen" is
Scotty Pippen Jr.). It is still elimination and not the prominence tiebreak:
two namesakes who both played ("brown", "curry") are asked about, and nothing
ranks them. Before extending the rule to another open slot, check both
conditions - a default with no wording that reaches the alternative is the case
that is not allowed.

**A question too short to be one is refused unread.** Jeff's rule,
2026-09-29: short or nonsensical questions are refused with a generic
sentence ("I couldn't understand your question, 'Tatum rec'. Please try
re-phrasing it."), and no effort is spent on them - most of the StatMuse
feed's two-word rows are a user hitting enter before the question was typed,
and guessing at "Tatum rec" answered his splits. `refusals.too_short` (fewer
than `MIN_QUESTION_WORDS`, three) runs in `Agent.ask` before the normalizer,
so a short question costs no model call.
Measured before the line was chosen: 215 of the large feed's 2,285 questions
have one or two words, almost all bare names, and in the yardstick only
"Tatum rec" has under three while every three-word question answers. Do not
add heuristics for longer nonsense (F097), do not file issues for such
questions, and do not grade them as needing a clarification.

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

The pipeline is parser → template or compiler → deterministic answer, and a
refusal naming why where nothing has a reading of the question. There is no
fall-through: until 5.0.0 a question the fast path could not answer went to
a tool-calling agent that wrote SQL by hand, and measured (ISSUES.md #129,
24 questions at production defaults) it answered 1 in 23, did not finish 61%
of the time, and was wrong five times in six where it finished - so it is
gone, with its prompt, its tools and its budget. `Agent.ask` (`agent.py`)
answers `answered_by="refused"` with the reason the parser, the template or
the compiler gave the question up with (`agent.refusal_text`), in the same
second; a template's or the compiler's own refusal (a clarification, a "no
match", a shape nothing reads named by its cause) is `"fast"`, since looking
at the question and having something to say is an answer. A question the
yardstick grades as "fell through" is one of these refusals now.

**The reader is the parser** (ROADMAP plan item 6; the router's model
classification went in step (d)). The model only copies names verbatim and
picks a stat (`query/normalizer.py`); `parse.read_route` reads everything else
from the words and hands the rest of the path a route in the shape the
router's model used to fill (`router.Route`), and the stages in
`query/router.py` settle its slots into the typed `Scope` the Route carries (`router.settle`), as they settled the
model's. Two things follow, and both matter when you add a shape:

- **A slot the router's model used to fill has to be read from the words,**
  or it is silently absent: `fields` ("with their rebounds and
  assists"), a team's quarter, the window ("last 10 games" - read before the
  stages, which decide the season type beside it). The hold-out comparison
  that found those (`~/association-research/yardstick-v2/holdout_compare.py`:
  the recorded corpus's questions outside day10, both readers, no model) is
  the check to rerun after a table change, beside the yardstick.
- **Names arrive as typed.** A typo reaches the entity index, which reads a
  single near spelling as that player and says so
  (`entities.read_near_spelling`); nothing corrects it upstream any more.
- **A name is recognized from the in-memory index, never by a statement.**
  `query/names.py` holds the `players` (3,101) and `teams` (30) tables as
  two indexes, loaded once per question inside `Agent.ask`
  (`names.loaded()`; a lookup outside a block raises `names.NotLoaded`, so
  a caller that forgot one fails instead of quietly issuing a statement per
  lookup - every test runs inside one, `tests/conftest.py`, and a script
  that reads names enters its own). Every lookup in
  `entities.py` that read only those tables is answered from them, with
  DuckDB's own semantics reproduced and checked against it: its `lower`,
  its split on every non-ASCII-lowercase letter, Damerau-Levenshtein over
  UTF-8 bytes, RE2's case folding, ILIKE that lowers ASCII only on an
  all-ASCII column, table order where the SQL had no `ORDER BY`. A new name
  lookup goes through `entities._player_index` / `_team_index`; do not
  write `FROM players` or `FROM teams` on the reader's path again. The
  reader issued 38.5 statements a question before this and issues the two
  loads now. One thing is not reproduced: the order of two players who
  share a `display_name` (21 names), which DuckDB's sort left unspecified;
  the index uses table order.
- **Who stands beside the subject has one reader: the subject reading.**
  "without X", "with X out", "when X and Y play", "in games X missed" are
  read by `subject._conditions`, each name with its role, and the stages
  are HANDED those names (`router.Beside`, built by `subject.beside`): they
  write `without` and `with_player` from it and decide a with/without split
  from it, and read no name themselves. Until 5.0.0 the stages had readers
  of their own for the same phrases; the two disagreed on seven of the 628
  recorded questions, and "When Embiid plays with Paul George, what is the
  PHI record?" answered for Embiid alone (#310). Do not give a stage a
  name regex again. Three rules of that reading, each measured against the
  old one on wordings outside the corpus before the stage readers went:
  - **A name is read by its position in the phrase** (`_name_segments`:
    the words after the keyword, split at "and", "or" and commas, ended by
    a word no name holds), so it does not depend on the model having copied
    it. "Tatum, Brown and Holiday" is three names though "brown" and
    "holiday" are ordinary words ten players share.
  - **An absence keeps a name nobody resolves, as typed.** "without zzyzx"
    is refused by that name downstream; dropping it would answer the games
    he played too. A player who PLAYED has to be one the reading found: the
    words after "with" and "when" are often no name ("with less than 15
    fga"), an ordinary word is a name there only when it stands alone
    ("with green", never "with best shooting"), and a near spelling counts
    only for a word that is no ordinary word and is near exactly one player.
  - **"with and without X" is the split over X**, read from its "without".

- **A shape Phase 2 has ported is a reader and a sayer, and nothing else
  answers it.** The game log is the first (2026-10-03, `ROADMAP.md`,
  "Phase 2, the expected steps", step 0): `compose/logs.py` reads a
  player's or a team's log into a `Result` (`query/result.py` - the rows,
  the count the window cut them from, the per-row summary, the remarks as
  `Note(kind, facts)`), and `compose/say.py` words it, taking the Result and
  nothing else (the import contract "The compiler's sentence reads no
  warehouse" holds it, with `compose.sentence`). `compose.answer` reads a
  rows-shaped `game_log` (and the `player_stat` window the retired template
  handed to the log) through them before any presenter runs, and so, since,
  a player's record over a line, his splits, his line over the games a
  narrowing sent the read to and his quarter or half (`compose/records.py`,
  `splits.py`, `stats.py`, `periods.py`; `stats.py`'s a `Scalar` body read
  by the compiler's `line` aggregate - each measure per game beside the
  sums the line is said from - and `periods.py`'s a `rows` read of the
  period's line or a `grouped` read by `period`), a count of games over
  a line and a single game's high, a named player's or the league's
  (`compose/counts.py`: a `Scalar` with `how="count"`, or a `Grouped`
  ranking by `player`; `compose/highs.py`: a `Rows` body ranked by the
  measure, `Rows.by`; both over the planned point compiled and executed -
  what the retired words added beyond those rows, the span's own seasons,
  the floor, the empty box scores, the withheld stat, the rebuilt games,
  are values and notes on the Result, and the high's redirect for a
  defaulted season is a `Decided`), and a player's or the league's
  longest run and two players' meetings (`compose/runs.py`,
  `compose/pairs.py`; the next paragraph), the league's leaders by a
  season-line metric (`compose/rankings.py`, since 2026-10-05: a `Grouped`
  ranking by `player` whose `ranked_by` is the metric - a count of games
  over a line ranks by `"games"`, which is how the sayer tells the two
  apart - read through `leaderboard.rank_season_line`, which keeps the
  dedup, the qualifier and the career pool exactly; the qualifier is the
  `minimum` decision it was, the career pool a `floor` note), and a
  player's unnarrowed line, his stat season by season and two or more
  players' lines side by side (`compose/seasons.py`, since 2026-10-05,
  over the SEASON LINE - `query/season_line.py`: one row per player per
  season in `player_season_stats_deduped`, and the advanced stats beside
  it. That module settles the subject and builds each statement the
  retired templates ran, moved whole; `compose.core.values_of` executes
  them through the compiler's one `execute`. The line is a `Scalar` and
  the history a `Grouped` by `season` with a career's `summary` part, the
  comparison a `Grouped` by `subject`, each on a `Span` whose `source` is
  `"seasons"`, which is how `say()` tells them from the games relation's
  shapes). No player shape has a presenter now. Three rules the slice set, which every later slice follows:
  - **A note is written as data and said once.** The reader builds
    `Note("window_short", {found, asked, ...})`; the sayer phrases it
    (`say.note_phrase`, ONE phrase per kind) and records it through
    `notes.note`, so the answer's remarks are the Result's notes. The
    templates that still write sentences phrase the same kinds through
    `note_phrase` too (`templates.common._box_score_notes`, over
    `box_score_notes_read`); `tests/query/test_answer_notes.py` reads a
    `Note(kind, {...})` as a write when it checks every kind is written.
  - **The reader executes the compiled statement; the sayer keeps the
    words.** `read_player_log` compiles the planned point with the log's
    columns as its measures (`compose.core.compile_query`) and runs that
    statement - since 2026-10-04; until then it ran the template's own
    `rows_sql` call beside the compiled one, which is the shape step 1's
    merge, sub-step (g), removes from each ported reader in turn. A reader
    that already holds the settled subject and reads it again (the log's
    "last N games" over each season type) compiles through
    `compile_over`, the second half of `compile_query`, so no statement is
    written beside the compiler's. `say_player_log` builds the heading, the
    aligned table and the notes from the Result's values. What the Result
    still carries as words, on purpose and to be cut as the sayers take it
    over: the narrowing's phrase (`Narrowing.phrase`, the relation's
    `filters()`), the span's `years`, and the "no games" sentence
    (`Result.empty`, from the shared `_no_narrowed_games`).
  - **What the relation measures lives on the relation; what a sayer says
    lives in the sayer - also where a template still shares it.** A
    player's quarter or half (`compose/periods.py`, 2026-10-04) took its
    data to `query/player_games.py` (`PERIOD_RECONCILIATION`,
    `PERIOD_RATES`, `period_distrust` - why a season is refused, as facts
    - and `period_agreement_notes` - its caveats, as notes) and its words
    to `compose/say.py` (`period_noun`, `period_caveat`,
    `say_period_refusal`); `period_leaderboard` and `team_quarter_points`,
    which stay until slice (iv), read the same data and take the same
    words through a call-time import, since `compose` imports the template
    modules. No private alias is kept for a template.
  - **Proved identical, text and all.** 628 of 628 recorded questions and
    1,391 of 1,391 unit-test calls; the slice rewords nothing. The
    private-template-import ratchet GREW by the shared steps the moved body
    calls (`_Span`, `_span_of`, `_resolved_team`, `_slot_season`,
    `_no_narrowed_games`, `_log_carries_rebuilt`, `_period`,
    `_season_name`), listed with Jeff's say-so: they are the relation's
    steps mis-homed in `templates.common`, and moving them to the relation
    modules shrinks the list for every slice at once (step 1's first item).
- **Two shapes have skeletons of their own, and are readers and sayers since
  Phase 2's step 2.** A streak is the `run` shape - the longest runs of
  consecutive games one predicate holds along, `compose.core._compile_run`
  over the relation's games in Eastern-date order (a game with no box
  score inside a spell he played ends a run rather than being carried
  across) - read by `compose.runs.read_streak` into the `Runs` body
  (`result.Run`: owner, length, first and last day and season, still
  open) and said by `compose.say.say_streak`. A matchup is the `pair`
  shape - two named players' lines over the games they met in, the pair
  relation `player_games.paired_rows_sql` over the first player's
  narrowed games, `compose.core._resolve_pair` and `_compile_pair` - read
  by `compose.pairs.read_player_matchup` into a `Grouped` body by
  `subject` (the comparison: one row per player, the question's order,
  with the head-to-head wins) and a `Rows` detail part (the newest
  meetings), said by `compose.say.say_player_matchup`; where a teammate's
  absence emptied the meetings, the reader compiles the same pair without
  it, and with the teammate playing, to say how often they met. The games
  the two shared as teammates are the pair relation's own read
  (`conditions._teammate_games`), which no compiled shape expresses.
- **A template's `TemplateUnsupported` gets one more deterministic try before
  the refusal.** `query/compose` sits between the two: when `check_scope`
  or the template itself raises, `agent.py`'s `_try_compose` offers
  `compose.answer(ctx, reading)` the same point on the relation the
  template could not narrow to - the point the parser read from the
  question's words once (`Reading.point`, `parse.reading_from_route`), so
  the compiler plans and runs it and never reads the question itself.
  There is no slot door: a caller with a Reading of its own (a test handing
  the compiler a subject it built) reads the point into it with
  `parse.with_point`. Where the point reader refuses (a ranking by shot
  distance, by a stat nothing ranks, under a floor in a unit nothing
  applies) the Reading carries a `reading.Cause` - a kind from the closed
  `CAUSES` and plain facts - and the planner says it
  (`compose.plan.refusal_result`): the reader builds no sentence, and a
  new cause is an entry there and a sentence here. A `TemplateResult` back is
  answered exactly like a template's own - `answered_by="fast"`, the intent
  kept, the same name-reading and coverage-caveat attachment - including when
  that result is itself a refusal (a clarification, a "no match"): looking at
  the question and having something to say about it is an answer. `None`
  is refused with the template's own reason. Nothing in the package may
  reach ollama - it is a compiler, not a model - and it narrows the relation
  only through the shared steps in
  `templates/common.py`, the same discipline the relation templates keep
  (see "A template on a relation does not declare, or apply, scoping of its
  own" above). Thirteen intents have no entry in `TEMPLATES`
  (`compose.COMPILED_INTENTS`: `threshold_count`, `single_game_high`,
  `record_when`, `player_history`, `game_log`, `player_stat`,
  `player_splits`, `leaderboard`, `period_split`, `player_compare`,
  `streak`, `player_matchup`, `with_without`). **"The compiler answers
  them" means the compiler plans them; most are still read and worded by
  the retired template's body.** Measured over the 277 yardstick questions
  (2026-09-30, both roadmap reviews): of 205 answers by these intents the
  compiler's own SQL read 45 and its own sentence worded 16; 55 compiled a
  query and discarded it for the template body's read; 94 never compiled
  one (the season line, which the compiler has no model of). So when you
  trace one of these, do not assume `compose.core` produced the numbers:
  find the presenter (`compose/present.py`) and follow it into
  `templates/`. `ROADMAP.md`, Phase 2, removes that detour slice by slice.
  The presenters' routes, as they stand
  (a team's streak
  through the `run` shape on the team relation
  (`compose.team._compile_team_run`), said by `compose.say.say_one_run` and
  `say_run_listing`, the words a player's and the league's streak are said
  with too; a
  with/without split through the team relation's `presence` group - a
  team's games inside named teammates' time on the team, each marked with
  who held the condition, `compose.team._compile_team_presence` over
  `templates.splits._with_without_read` and the relation cell
  `conditions._with_without_games`, said by `_with_without_said`), and
  where it has no reading
  the question is refused with the compiler's reason
  (`agent._run_compiled`). A presenter says what its retired template's
  words state (`compose.present.STATED_SCOPING`) and steps aside for a
  narrowing beyond them, so the compiler's own sentence, which states every
  narrowing the relation applied, answers; a narrowing the relation cannot
  honor at all is refused by the planner, which the answering loop runs
  once per question AFTER the parser has read the point
  (`compose.plan.plan_point`, kept on `Agent.planned` and handed to
  `compose.answer`; the parser reads and does not plan) - the refusal
  names the planner's reason, never a template's list. Retiring a template
  this way is measured first:
  every call its unit tests make, and every recorded question it answers,
  answered both ways and compared - the recorded questions alone showed one
  shape the template still carried; the unit tests showed five.
- **A team can be the subject, not only a narrowing.** `compose/team.py`
  (`TeamQuery`/`TeamResult`/`run_team`, `point.team_read_point`,
  `sentence.team_sentence`) is a second, separate compiler beside `core.py`'s
  player one, over the team-games relation instead - "how many 3-pointers
  have the Magic made", "total points scored by the Raptors in the last 10
  games". Kept as its own module on purpose: nothing in it is read by, or
  reads from, the player-subject functions, so every rule measured for the
  player subject stays exactly as it was. Two readers, the team counterpart
  of `player_stat`'s own season-line-vs-box-scores split: an UNNARROWED
  question reads the season's raw TOTAL straight from `team_season_stats`
  (never the per-game average `team_stat` gives - "how many has it made" is a
  different question from "how many per game"), and a NARROWED one (an
  opponent, a venue, a date, `since`/`until`, a game of a series, a calendar
  `situation`, or an `order`/`limit` window) sums the team-games relation's
  own game-level columns (points, points allowed, differential) through
  `scoped_team`/`team_games`, the same shared steps every team template
  narrows through - never a hand-written clause here either. A box-score
  count (3-pointers made, not a game-outcome figure) narrowed to a window
  refuses rather than answering the season instead, since the relation has no
  box-score join yet. `read_point` tries `team_read_point` before the
  league-wide reading, and nothing after the parser restores a subject:
  "magic" is also Magic Johnson's given name, and the compiler's own
  dropped-subject restoration (`repair()`, gone with plan item 6, step (e))
  once invented him from a team reference - the shape `subject.apply_subject`
  exists to catch. `team_named_in` restores a dropped team from the
  question's own team word, the way `players_named_in` does a player.
- **What the model sees lives in `query/normalizer.py`'s constants, alone.**
  `NORMALIZER_PROMPT`, `NORMALIZER_SCHEMA` (its stat enum is
  `NORMALIZER_STATS`) and `NORMALIZER_NUM_CTX` are the model's whole input;
  `normalize()` only decodes the reply, and the parser and the stages read
  everything after it. So a diff to `parse.py` or `router.py` cannot change
  what the model returns for an unrelated question, and a diff to those
  constants always can.
- **A prompt and its constrained schema must agree.** A value a prompt
  teaches that the schema's enum lacks can never be emitted under
  constrained decoding, so it silently becomes something else: the router's
  `player_compare` was described in its prompt and missing from its schema's
  enum, and every comparison routed to `player_stat`.
  `test_the_prompt_and_the_schema_agree` (`tests/query/test_normalizer.py`)
  holds the normalizer's examples to its enum; the other half of what the
  router's pair of tests guarded - that every template is reachable - is
  `test_every_template_is_reachable_from_the_reader`
  (`tests/query/test_router.py`): the parser's `PARENT_GRAMMAR`, the stages'
  `CODE_ASSIGNED_INTENTS` or the subject reading's `KIND_ASSIGNED_INTENTS`.
- **A prompt has a token budget, and ollama enforces none.** ollama
  truncates an over-length prompt *silently and head-first*: the retired
  agent's original bug was a 10,295-token preamble against `NUM_CTX = 8192`,
  which discarded the schema and correctness rules while keeping the tool
  descriptions, and nothing said so. The normalizer's prompt is ~330 tokens
  against `NORMALIZER_NUM_CTX = 2048`, and
  `test_the_normalizers_window_holds_its_prompt_and_a_long_question`
  (`tests/query/test_normalizer.py`) holds the two together with
  `normalizer.estimate_tokens`; keep that check beside any prompt this
  project sends.
- **A slot the schema does not require is a slot the decoder may never
  consider, and no prompt wording fixes that.** The router's schema recorded
  this for `stat`; `side` proved it again. "Show me Wembanyama's defensive
  fingerprint chart" appeared in the router's prompt verbatim as a worked
  example with `{"side":"defense"}` beside it, and still emitted
  `stat="defensive"` with no `side` at all — 6/6 at temperature 0. `stat` was
  required, so the adjective was spent there first, and the whole fingerprint
  got drawn where its defensive half was asked for. That is why
  `NORMALIZER_SCHEMA` requires both of its fields
  (`test_the_schema_asks_for_the_names_and_the_stat_and_requires_both`).

  The same slot has a second, opposite failure: **a required slot is one the
  decoder fills whether or not the question asked for it.** `stat` came back as
  `'points'` on "compare sga and embiid" 12 times out of 12, which narrowed
  `player_compare` to one average and undid the whole-line default it exists
  for - and the normalizer's `stat` is required too. The stages drop it for
  that intent only (`router._named_a_stat`). Note why the word list can be
  loose there and could not be anywhere else: for a comparison, a missed word
  widens the answer to a line that still holds the stat asked about, while
  `leaderboard` with no stat has nothing to rank by.

  Two ways out, and prefer the second. Making a slot *required* works (that
  is why `stat` is) but was measured and reverted for the router's
  `season_ref`, because requiring more slots crowds out others. Reading the
  value **from the question text** costs nothing and cannot move any other
  slot: that is what the parser does for every slot but the names and the
  stat, and what `_validate_season` and `_validate_side` did for the
  router. Hash `NORMALIZER_PROMPT` and `NORMALIZER_SCHEMA` before and after a
  change to prove the model's input is unchanged - if both hashes match, the
  normalizer's recorded replies still stand and the offline rehearsal (below)
  is the whole check.
- **Any edit to the model's prompt moves what it returns on unrelated
  questions.** Measured on the router's prompt: adding the `fingerprint`
  intent line reproducibly flipped "What was the Lakers record last season?"
  from `team` `"Lakers"` to `"Los Angeles Lakers"` — with *any* wording of the
  added line, including a two-line one, so it is the prompt's length as much
  as its content. The 3B is that sensitive, and the normalizer runs the same
  3B. Two consequences: after an edit to `NORMALIZER_PROMPT` or
  `NORMALIZER_SCHEMA` the recorded replies no longer stand - re-record them,
  and make a live run the record - and assert in a case only what changes the
  *answer* (both those strings resolve to team_id 13 and produce an identical
  sentence), never the encoding the model happened to pick.
- **An intent comes from the question's own words, never from the model.**
  The parser's `PARENT_GRAMMAR` names the parent by the subject's kind, and
  the stages assign `CODE_ASSIGNED_INTENTS` (`period_split`, `coach`) from
  the text, so a new intent is a grammar row or a stage, with no prompt edit
  and nothing to move on another question. A refusal especially: a question
  nothing can answer needs the model's help least. `coach` is the worked
  example - the word is unmistakable, nothing else in the warehouse is named
  it, and a bare surname is deliberately not matched ("nurse" and "rivers"
  are ordinary words, the substring trap `players_named_in` exists for). Such
  a template declares no tables, so it goes in `TABLELESS_INTENTS` or the
  coverage gate fails.

  The children come one step later, where the subject's KIND is known:
  `subject.KIND_ASSIGNED_INTENTS` (`_CHILD_GRAMMARS`). A child of a parent
  the grammar names - a count of 30+ point games under `game_log`, a history
  over the past 4 seasons under `player_stat`, a streak under `team_record` -
  is named by its words AND gated on the kind the reading settled, which is
  what keeps "how many times did the 76ers play boston" (two teams) off
  `threshold_count` and "who lead the league in avg 3 point distance" (no
  player) off `shot_distance`. Measured when the seven left the router's
  prompt: 0 false positives over 261 recorded questions of other intents.
  The child is named from the grammar alone (`subject.child_named`), and
  the stages run ONCE, under it (`parse._read_route_staged`); they may
  decline it (a count with no threshold is a ranking), and only then run
  again under the parent - 4 of the 628 recorded questions, every one a
  history read as a line. Until 2026-10-02 they ran under the parent,
  again under each child to see whether it held, and once more under the
  one that did (1.7 runs a question, up to four), and the subject reading
  ran them too, under "other", before the parser ran them at all.
  `subject.read_subject` reads and decides nothing; `subject.settle_subject`
  writes the intent and why with no stage run; the Route carries the
  SETTLED subject, and `parse.reading_from_route` settles nothing. Add a
  case to `port_check.py`'s corpus (`~/association-research/intent-shrink/`)
  and to `tests/query/test_subject.py` for each wording a grammar gains.
- **A refusal names the missing thing, never only the slot.**
  `check_coverage`'s reasoning, and it applies past the floors: a coach
  question once reached an agent that queried tables with no coach column and
  was then free to fill the silence from its own weights; now it would be
  refused for its intent, which tells the reader nothing. `query/refusals.py`
  is where a shape the warehouse has no column for gets its cause. Before
  writing the refusal, check what the source actually serves - "ESPN does not publish
  coaches" was the obvious sentence and it is false, and a refusal naming the
  wrong cause reads as honest while sending the reader somewhere useless.
- **The parser has three regression nets; add to them whenever you find a
  misreading in the wild.** Cheapest first: `tests/query/test_parser.py`, a
  case per wording a table gains, watched to fail; the stage snapshots
  (`scripts/stage_snapshots.py`, "While the pipeline is rebuilt"), all 628
  recorded questions - the 277 yardstick wordings, the 75 hold-out
  questions nothing was tuned on and the 276 paraphrases - through the
  whole agent with the normalizer's recorded replies and no model, compared
  stage by stage against the tree before the change; and the yardstick's
  live run, graded blind, which is the record. Pytest cannot see a table
  change that moves some other wording - that is what the snapshots are
  for, and they say whether it moved in the reading or only in the answer.
  They replace the offline rehearsal and the hold-out comparison
  (`~/association-research/yardstick-v2/run_offline_parser.py`,
  `cmp_routes.py`, `holdout_compare.py`), which compared the answer's text
  and the route's trace line; `run_offline_parser.py` is still what
  predicts a live run's graded rows before the model is asked.
- **The recorded replies are this machine's, and `~/association-research`
  is a repository.** The stage snapshots, the ledger and every offline
  harness put the normalizer's RECORDED replies in the model's place, so
  they prove what production does only while the model still says the
  same. It did not survive the move to the OVH devbox (ollama 0.33.3 to
  0.35.0, another CPU): 53 of 628 replies differed, though the parser
  absorbed all but one answer and the live run matched parser22 word for
  word. The three reply files were re-recorded here on 2026-10-02 and the
  baselines taken again. After a move, an ollama upgrade or a new pull of
  the model, re-record before trusting an offline proof: every recorded
  question through `normalizer.normalize`, one caller, in file order.
  One thing is not a difference: the SAME question asked twice in a row
  can get two replies (5 of 20 tried), because the second is answered
  from the cached prompt; distinct questions in any order repeat exactly
  (276 of 276 between two orders). The yardstick, the reply files, the
  baselines and each spike's harnesses are versioned in
  `~/association-research` (private, `jeffknupp/association-research`;
  its own `AGENTS.md` says what is live and what is history): commit
  there when you add a run, a baseline or a harness.
- **Only one ollama caller at a time.** Two callers on one CPU-only ollama
  instance corrupted the router's output silently (ISSUES.md #171) or wedged
  it in a reload loop for minutes, and the normalizer runs the same model. A
  live run is the only caller: confirm it with `pgrep` first.

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

**A change that alters warehouse data is not finished until the warehouse
holds it.** This covers a parser fix, a new or reshaped view, a new column, a
corrected derived table, and a fetch fix. A fix merged but never loaded looks
done in the code and stays wrong in every answer. The view fixes in `220f8aa`
were in the code for hours, and reached the warehouse only when someone ran a
separate `data load`. Do both halves:

- **The fetch path must produce the corrected data by itself.** A fresh
  `association data pull` of an affected season has to come out right with no
  manual step. Add a test that pins the correction, with a fixture in the shape
  of the real bad row.
- **Backfill what is already on disk and in the warehouse.** Which command
  depends on where the fix lives:
  - **Anything built at load time** (views and derived tables in
    `fetch/warehouse.py`): run `association data load`, or
    `association data load --tables <names>` for a subset. It rebuilds from the
    Parquet already on disk.
  - **A parser or fetch fix:** the Parquet on disk was written by the old code,
    so a load alone changes nothing. Re-fetch the affected seasons with
    `association data pull --seasons <range> --force`, adding `--include-pbp`
    when plays or shots are involved. The pull reloads the tables it wrote.
  - **Anything narrower** (a list of event ids, one athlete's career): a
    one-off script is fine, **but only if it runs the real code path**. It must
    fetch through the `Pipeline` fetch methods, write through `_write_rows`,
    and load through `warehouse.build` or `association data load`. Commit it
    under `scripts/` so the backfill can be re-run.

  Never patch a Parquet file or the DuckDB file directly, and never run an
  `UPDATE` against the warehouse. The next `data load` rebuilds from Parquet
  and silently undoes it, and a fresh pull would not reproduce it.

  Both commands default to `./data/parquet` and `./nba.duckdb`, relative to
  the current directory, and a worktree has neither. Run them from the main
  checkout, or pass `--data-dir` and `--db-path` pointing at its files.
- **Then re-measure** against the rebuilt warehouse, with the query that found
  the problem. Update or delete the `ISSUES.md` entry to match. If you cannot
  run the backfill yourself (no network, or no permission to write the
  warehouse), say so in your report. Give the exact command, and leave the
  `ISSUES.md` entry open with "fixed in code, not yet backfilled".

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

**A NetPoints date is not an ESPN date, and matching them on the calendar day
is a guess that mostly works.** NetPoints names each daily file for the US
Eastern date the games were played on; ESPN stores a UTC tip timestamp, which
rolls over to the next day for anything after 7pm Eastern. Matching on the UTC
date meant choosing between `date + 1` and `date`, and *both orderings are
wrong for some real schedule*: `date`-first steals a back-to-back's first
night, and `date + 1`-first — what shipped — steals the *next* night, after
which that next night's own file claims the same game again through the other
half of the rule. 505 doubly-claimed team-games since 2019, 611 disagreeing
`(event_id, athlete_id)` pairs in 2026's `net_points_player_game` alone, and
no error anywhere: the row count looked right and every event_id was a real
game the player really played in.

The fix is to stop guessing and read the game's own Eastern date off the
timestamp — `NetPointsGameIndex` in `fetch/parse.py`, one exact lookup, since
a team plays at most one game per Eastern date. Three things about it are
load-bearing:

- **The real Eastern clock, written out rather than read from a tz database.**
  This was a fixed five-hour shift, on the reasoning that EST and EDT disagree
  about a date only in the midnight-to-1am hour and no game tips then. True of
  tips - and false of the stamps that are not tips. ESPN stores a game with no
  tip time as *midnight* Eastern (`04:00Z` in summer), exactly the hour the
  shift gets wrong, and 391 games, the whole 1989-1992 postseason among them,
  printed a day early. `nba/season.py` holds the US daylight-time rules by hand
  (so a machine with no tz database still dates games), a test checks them
  against zoneinfo for every day 1976-2039, and **every** stamp-to-date
  conversion goes through `eastern_date`, `eastern_date_sql` or
  `eastern_day_utc_range`. Do not write another `- INTERVAL 5 HOUR`.
- **A date holding two of one team's games resolves to neither.** A team
  cannot play twice in a day, so a duplicate key is ESPN's clock being wrong,
  not a choice — and the dict this replaced silently kept whichever row it
  read last, shadowing 126 `(team, UTC date)` keys in the NetPoints era.
- **The UTC window survives as a fallback**, in the old order. Four games in
  late February 2020 are stored hours from when they were played (Detroit at
  Portland, a 6pm Pacific tip, is recorded as `2020-02-24T12:00Z`), so their
  Eastern date is meaningless while their stored *date* is still right.

The way to check any of this is the source's own box score. The daily file
carries `pts` beside the NetPoints values and the parser deliberately drops it
— which makes it a free, independent cross-check that a row landed on the
right game: NetPoints' 2025-10-25 file gives Ryan Kalkbrenner 14 points and
its 2025-10-26 file gives him 4, and `player_box_stats` says which game is
which. `scripts/check_net_points_games.py` runs that over a whole season.

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
  construction rather than by discipline. This is enforced now, twice:
  import-linter forbids `web.app` reaching `ollama` by any chain, and
  `test_importing_the_api_layer_loads_no_model_client` checks a fresh
  interpreter. The rule was already broken when the contract was written -
  `web/runner.py` imported `Agent` at module level for one annotation, which
  loaded ollama - so a type-only import of the query engine goes under
  `if TYPE_CHECKING:`. The whole suite still runs with no
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
- **The page's JavaScript has one behavioral check, and it is not a gate.**
  pytest can only read `static/index.html` as text: `test_renderers.py` parses
  the renderer table out of it and syntax-checks the script with
  `node --check`. Anything that is a key event, a caret position or a
  clipboard needs a browser, so `scripts/check_web_ui.py` drives the real page
  in Chromium with real key presses - `uv run --with playwright python
  scripts/check_web_ui.py`. It is deliberately outside the gates: playwright
  is not in the `dev` extra (deptry has a per-rule ignore saying why), its
  browser is a large download, and the gates here run offline in seconds. Run
  it when you touch the page's script, the way `check_coverage.py` is run
  after editing a floor. It needs no ollama, no warehouse and no network - the
  API is stubbed in the page - and it serves `static/` over a loopback address
  rather than a `file://` URL, because `navigator.clipboard` exists only in a
  secure context.
- **Print the URL with `flush=True`.** stdout is block-buffered when it is not
  a terminal, and with an ephemeral port that URL is the only way to find the
  server at all.

## Data gotchas

- **A season is named for the year it ends.** 2023-24 is season `2024`. See
  `nba/season.py`.
- **"This season" is the latest season with games on record, not the
  calendar's.** The calendar turns over on October 1 and the first game is
  weeks later: read from the calendar alone, every unstated season from
  2026-10-01 was 2027, a season with no games. `Agent.ask` answers inside
  `season_on_record(latest_season_on_record(con))`, which caps
  `current_season()`; the fetch path pulls `calendar_season()`, uncapped. A
  harness that must answer the same on any day sets `ASSOCIATION_TODAY`:
  the stage snapshots and every research harness under
  `~/association-research` pin 2026-09-30 (the day the recorded corpus's
  answers were graded as of), and a new harness does the same. The test
  suite pins it too (`tests/conftest.py`): fifteen tests encode the
  2025-26 season and went red under an October date with nothing wrong.
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
- **Do not conclude a source does not publish something from one file's
  schema.** ESPN Analytics publishes *two* objects per date, and the per-game
  play-type one went unread for months because the season file had no game id
  and the daily file's `assister` / `putback` / `corner`-shaped field NAMES
  were read as the taxonomy — but their VALUES are integer counts
  (`pts: 36`, `assister: 4`), not net points. Checking one file's schema and
  one file's field names, without checking a value against the season columns
  or looking at what the site's own page fetches, produced a confident and
  wrong claim about what exists. `DATA.md` ("The play-type split exists per
  game as well as per season") has what the two files actually hold.
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
  is no pull that fills these in. **`DATA.md` ("Coverage floors") has the table
  and what is before each floor**; `association/nba/coverage.py` is the enforced
  copy. Three rules to carry while writing code:
  - **Select a postseason by the calendar year it was played in, never by
    label** (`templates._season_games`, `team_metrics.games_scope`,
    `check_coverage.py`). Before 1993-94 ESPN labels a season by the year it
    STARTED, so matched by label "the 1991 playoffs" answered 1992's.
  - **Season 1993 is a phantom** — its 1,185 events are the identical rows
    ESPN returns for 1994. Treat 1994 as the earliest real regular season, and
    **key joins over that era on `season` as well as `event_id`**, or every
    1993-94 player-game is listed twice over.
  - **`player_season_stats` reaches back to 1977 but is a survivor sample**, so
    a lookup and a ranking have different floors (`first_ranking_season`).
- **Shot coordinates measure y from the rim, not the baseline, and
  `points_attempted = 0` means unlabeled, not zero points.** The rim is at
  `(25, 0)` (`court.HOOP_Y`). **Never filter `points_attempted` for a shot's
  value**; read `shotchart.SHOT_VALUE_SQL`, which derives it where ESPN left it
  0 and keeps the per-season refusals and caveats beside it. Free throws carry
  a position through 2018, so "has coordinates" does not exclude them. The
  numbers and the seasons are in `DATA.md`; the method that caught it is worth
  keeping: **where a column has a sibling that restates it (a described
  distance, a box score's attempts), fit against the sibling before trusting a
  constant.**
- **ESPN's career endpoint copies some regular seasons into the postseason**,
  so **read `player_season_stats_deduped` for postseason lines; the raw table
  still has them.** The view drops a postseason line claiming more than 28
  games (four best-of-seven rounds is the most a run can hold) or repeating
  that season's regular-season games and points exactly. `DATA.md` has the
  evidence and the counts.
- **Whole team-seasons of box scores are empty, and they are not scattered
  games.** Every Chicago and New Orleans game from 2013 to 2018 but two lists
  each player as having played with NULL minutes and every stat 0, beside a
  NULL team box row — 1,025 events, both tables empty. Anything summing
  `player_box_stats` over 2013-2018 is about 87% of ESPN's own season totals,
  and a streak or a with/without split cannot tell whether a player sat those
  games out. **Say how many games a per-game answer could not see** — that is
  what `_empty_box_scores` is for. `DATA.md` has the counts and the seasons.

  **Do not confuse that with the team-box-only fault**, which looks similar and
  is not. Vancouver 1996 has an all-NULL `team_box_stats` row for every game
  beside **real player rows with real minutes** — so a per-player answer is
  fine and only team-level reads are affected. An earlier note here called
  Vancouver 1996 "the same shape" as Chicago and New Orleans; it was measured
  on `team_box_stats` alone and is wrong. `player_box_stats` holds 785
  Vancouver rows with minutes, 12 a game, which is the league-normal roster
  size that season. Chicago 2000 and 1999 were briefly grouped in with
  Vancouver under this same fault; re-measured, their all-NULL team rows sit
  on zero `real_games` events — placeholder rows `real_games` already drops —
  so those two seasons are not this fault at all.
- Query connections to DuckDB are **read-only**, as a hard guarantee.

**Those floors are enforced, not just documented.** `association/nba/coverage.py`
holds them as a table — `COVERAGE`, one entry per queryable table — and
`templates.check_coverage()` refuses a question that lands under one. Add an
entry whenever a template reads a new table, and declare the template's tables
in `TEMPLATE_SOURCES`; a template missing from it is one no floor can refuse.
Three things about that module are load-bearing:

- **It returns the refusal rather than raising it.** That is the opposite of
  `check_scope()`, and deliberate: `check_scope` raises so the compiler gets
  its turn at the same point, and may do better. Nothing does better here: a
  season under the floor is empty for every reader, and (while the agent
  existed) an agent handed it queried the same empty tables, more slowly,
  and was then free to fill the silence from its own weights.
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

- **A refactor is proven by a golden comparison, not by the suite alone.** The
  complexity refactor that split the router's `route()` (its stages are
  `router._settle`'s now), `parse_game_summary` and the
  templates into steps was checked by calling each function with many inputs
  - the routing corpus's slots, the tests' own cases, one per branch - against
  the original code and again after, and diffing the full results (answer text,
  data, exceptions, written files, types as well as values). Import each copy
  explicitly (`PYTHONPATH=<tree>/src PYTHONDONTWRITEBYTECODE=1`, print
  `association.__file__`), and perturb one token of the refactored code to
  prove the comparison can fail. A green suite says only that the tested
  inputs still pass. **On the query path that comparison is built:**
  `scripts/stage_snapshots.py` ("While the pipeline is rebuilt") runs both
  populations, pins what moves and names the stage a difference entered
  at. Use it rather than a hand-rolled harness; the perturbation is still
  yours to make.
- **Run the original twice before calling a float difference a regression.**
  DuckDB's parallel `SUM` is not bit-reproducible: the same query over
  `player_season_advanced_stats` twice returns up to 185 of 588 rows differing
  in the 15th digit. Compare aggregates rounded, or establish the noise first.

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
- **Use `scripts/perturb.py` rather than a hand-rolled harness.** The bullet
  above is easy to honor in letter while missing what actually breaks: the
  harness reporting CAUGHT for a reason unrelated to the perturbation. All
  three of these happened in one session, on top of the `.pyc` and `SyntaxError`
  already recorded:
  - **A red baseline.** One unrelated failing test makes *every* perturbation
    exit non-zero, so nine of them read CAUGHT and none of them meant it.
  - **A filtered run.** `-k "rebuilt"` does not match a test named
    `..._rebuild_counted`; three guards sat out a whole sweep that was then
    reported on. Run the whole file. Never `-k`.
  - **A retyped anchor.** Hand-escaping apostrophes matched zero times, the
    edit did nothing, and the green suite read as MISSED - a weak guard - when
    nothing had been perturbed at all. Slice anchors out of the file, never
    retype them, and assert the match count before running.
- **Import success is not execution.** A `NameError` in a new code path fired
  on every call and no test caught it, because the check run was
  `python -c "import association.fetch.pipeline"` - which proves the module
  parses and nothing else. Call the function, with a fake client if need be.
- **A script run from a worktree resolves the INSTALLED package.**
  `scripts/check_coverage.py` reported "29/29 floors check out" about a field
  that did not exist in the code being checked, because it imported
  `association` from the main checkout. Same shape as the
  `check_types_complete.sh` note under "Before you commit". Run them with
  `PYTHONPATH=<worktree>/src`, and treat a green check from a worktree as
  unproven until you have confirmed which copy it read.
- **Branches built in parallel collide on private helper names, silently.**
  Four templates were built on separate branches at once, and five times two of
  them had independently written a module-level helper with the same name:
  `_eastern_date`, `_stints`, `_no_games` twice, `_pct`, `_season_name`. Git
  merged each without a conflict, the later `def` replaced the earlier, and
  the first branch's callers got the other branch's function - 14 failing tests
  after one merge, 28 after another, green on both branches before it. mypy's
  `no-redef` and ruff's F811 catch it, but only when the gates run on the
  merged tree. After merging parallel work, scan for duplicated top-level
  names before reading anything into either side's green tests.
- **Chain a merge's resolver, its `git add` and the commit with `&&`, never
  `;`.** A resolver script that asserted and stopped, followed by `;`, let a
  merge commit land with `<<<<<<< HEAD` inside `CHANGES.md` and `ISSUES.md` -
  twice in one week, on merges of parallel agents' branches - and every gate
  passed, because a marker is valid Markdown. `scripts/check_conflict_markers.sh`
  now refuses any tracked file holding a start or end marker (a bare
  `=======` is a Markdown underline, so only those two).
- **Never rewrite `CLAUDE.md` in place.** It is a symlink to `AGENTS.md`, and
  `git ls-files` lists it, so a `sed -i` or `perl -pi` over a file list
  replaces the link with a regular copy (`git status` shows `T CLAUDE.md`). The
  two then drift silently. Exclude it from bulk edits and edit `AGENTS.md`.
- **`/tmp` is a shared tmpfs** (32G on the OVH devbox; 7.9G on the box
  before it, where this happened). Four agents' golden-comparison outputs,
  plus older sessions' scratch directories, filled it to 100% mid-run, where a
  failed write looks like a diff or a crash unrelated to the change. Compress
  or hash large outputs, delete them once compared, and check `df -h /tmp`
  before a large run.
- **A DuckDB `SET` is per-connection.** A test asserting one has to observe it
  on the connection the code under test used; a freshly opened connection
  reports the default and the assertion looks like a real failure.

## Saying what you measured

A wrong status report costs more than a wrong patch, because the next decision
is made on it. Three rules, each of which was broken in the session that
prompted them:

- **Name the population, not just the number.** "19 of 2,062 combined rows
  disagree" is checkable; "the combined rows are wrong" is not, and an entry
  that says 26 when the number is 19 sends the next agent looking for seven
  rows that are already fixed.
- **Re-measure before repeating a figure from an entry.** `ISSUES.md` records
  what was true when it was written. Two of its counts had already been fixed
  by other work in the same week.
- **Say which copy of the code and which warehouse you measured**, and never
  report a template's behavior from a direct call when the answering loop adds
  something - `agent.py` appends the coverage caveat, so a template called
  directly looks like it is missing one. Compare against `real_games` rather
  than `games` for anything counted against `team_season_stats`; two "new
  findings" in one session were known phantom rows seen through the unfiltered
  table.

**One concept, one definition.** Ruff's F811 and mypy's `no-redef` catch a name
defined twice in one module and are blind to the same name in two - so
`MAX_LIMIT` is 100 in `query/leaderboard.py` and 50 in `query/templates/common.py`,
and the NBA's five-hour Eastern offset was once declared six times under five
names.
`scripts/check_duplicate_names.py` reports cross-module constant collisions
against an allowlist of the ones already filed, so it fails only on a new one.
It is deliberately name-only: comparing values pairs `DEFAULT_LIMIT` with
`EASTERN_OFFSET_HOURS` and buries the real finding in coincidence. It also
cannot see a concept duplicated under *different* names, which is most of those
six Eastern offsets - that still needs somebody reading a grep.

## Recording findings

**Anything that needs follow-up goes in `ISSUES.md`, including what you were
not looking for.** Most of this project's data problems were found in passing:
the empty 2013-18 box scores turned up while building a streak template, and
the missing 2000 and 2001 playoff games while checking coverage floors. Each
then lived only in a chat report. A finding that is not in the file is one the
next agent has to rediscover from scratch, or never does.

- **A fault in the source goes in `DATA.md`; what we do about it goes in
  `ISSUES.md`.** They are two halves of one finding and they are not
  interchangeable. `DATA.md` records what ESPN does — the wrong value, the
  missing games, the odd label — as a fact with evidence, and it is not a task:
  it gets no GitHub issue and nothing closes it, because nothing we write
  changes what the source serves. `ISSUES.md` records what we do about it — the
  fix, the workaround, the caveat — and that entry links back to the `DATA.md`
  section it comes from. Establish which half you have before you write it up:
  "ESPN files the 1990 postseason under 1989" is `DATA.md`, "select a postseason
  by the year it was played" is `ISSUES.md`. A fault nobody has to act on yet is
  a `DATA.md` entry alone.
- **What counts.** A data inconsistency (numbers that disagree with a sibling
  column, with the source, or with reality), a bug or wrong-answer risk you did
  not fix, a question shape that is refused or mis-routed, a tooling or gate
  problem, a doc that is wrong. Not ideas, not features nobody has asked for,
  and not something you fix in the same change - and fixing it as part of
  the work in hand is the first choice ("While the pipeline is rebuilt",
  the rewrite's stance): an entry is for what you cannot fix there.
- **Record it before you finish, even when it is out of scope.** Measure first
  where it is cheap: a count and a season beat "looks off". An entry needs a
  title, the evidence (the query or `file:line`, with numbers), what a user
  would see (a wrong answer, a refusal, or nothing), a next step, and a
  priority.
- **Rank by what a user sees, not by how interesting it is.** The priority
  definitions are at the top of `ISSUES.md`. A wrong answer delivered fluently
  outranks a refusal, and a refusal outranks a gap nobody asks about. That is
  the ordering of the failure shape at the top of this file.
- **Fixing an issue removes it.** Delete the entry in the same commit as the
  fix. If the fix touches `src/`, also say what changed in `CHANGES.md` (the
  rule under "Before you commit"); otherwise the commit message is the record.
  If the fix is partial, rewrite the entry to what remains. The file is the list
  of what is still open, not a history.
- **Subagents record findings too, and their prompt has to say so**, in both
  files. An agent reports what it was asked about and nothing else, so every
  prompt that dispatches one must ask for incidental findings, and must say
  that a fault in the source goes in `DATA.md` while what we do about it goes
  in `ISSUES.md`, linking to the `DATA.md` entry. Two cases:
  - An agent working in its own worktree appends to its copy of `ISSUES.md`,
    and the entries merge with the rest of its branch. Two branches adding
    entries conflict on adjacent lines; keep both sides.
  - A read-only agent (research, audits) ends its report with a "Findings for
    ISSUES.md" section, and the agent that dispatched it transcribes them.

  Either way, whoever merges the work re-reads the file afterwards and
  re-ranks it.
- **Open a GitHub issue for each entry you added, once your work is done.**
  `ISSUES.md` is the source of truth; the issues mirror it so they can be
  assigned, searched and notified on. Do it at the end, not while you are still
  finding things, and only for entries that are new. `scripts/sync_issues.py`
  does it: it opens one issue per entry that has no `- **GitHub:** #N` line,
  titles it with the entry's heading, uses the entry text as the body, labels
  it by priority (`P1: wrong answer`, `P2: misleading`, `P3: gap`, `P4: low`,
  plus `bug` or `enhancement`), and writes the number back into the entry. Pass
  `--area` to add one of `data`, `query`, `tooling` or `documentation`. Run it
  with `--dry-run` first. When a fix removes an entry, close its issue in the
  same breath and name the commit. An entry's heading *is* its issue's title,
  so renaming a heading (a spelling fix renamed #56's) means retitling the
  issue too.

**Dispatching agents.** Beyond asking for findings (above): give parallel
agents disjoint files, each in its own worktree, and have them prefix new
private helpers with the function they came from (the collisions under
"Verifying your work" are what happens otherwise). Tell them to run the tests
and hooks in the foreground: an agent that backgrounds a run and waits for a
notification stops instead, and has to be resumed by hand - two did in one
session. Tell every one not to run ollama, `association query` or a live
yardstick run unless it is the only one doing so - one ollama caller at a time
(see "Working on the query path").

Four rules from the sessions where agents cost more than they saved (Jeff,
2026-09-28: worktrees behind master, stalls the lead could not see, better
ideas discarded to stay in scope, sweeps filing as many entries as they
closed):

- **Base the worktree on pushed master, and have the agent check.** The
  tool branches a worktree from the main checkout's HEAD, so a lead working
  on its own branch hands out a stale base unless it pushes and
  fast-forwards that checkout first
  (`git -C ~/code/association merge --ff-only origin/master`). Put the SHA
  in the prompt and make the agent's first command `git log --oneline -1`:
  a mismatch stops the task before any work is done on the wrong tree.
- **Foreground, with a budget, and a lead who looks.** Every command in the
  foreground, each with the time it should take (the table under "Before
  you commit"; the rehearsal is about four minutes) - a command past three
  times its budget is killed and reported, never waited on. The lead reads
  the agent's transcript on a cadence (the task's output file and its
  mtime) and messages an agent silent for fifteen minutes, rather than
  waiting for a notification a stalled agent never sends. The lead's own
  waits are on a process, never on a file appearing:
  `until [ -s out ]; do sleep; done` spins to the timeout when the job died
  before writing it.
- **Fix what you can, file only what you cannot, discard nothing.**
  "Recording findings" was read as "stay in your lane", and bug sweeps
  filed as many entries as they closed. A finding the agent can fix as part
  of its work is fixed, in its own commit, with a test, and reported; only
  what it cannot fix there is filed (until 2026-10-04 this had a
  twenty-line budget; Jeff removed it - "While the pipeline is rebuilt").
  A better approach than the one asked for is treated the same way: taken
  when it is technically right and within the agent's files, otherwise
  stated in the report with its measured tradeoff. Silence is the one
  outcome not allowed.
- **Parallelism is for disjoint, bounded, measurable work** - a fix with its
  own tests and a rehearsal to check it against, a relation over its own
  tables. The parser-compiler seam is one agent's at a time: two branches
  on it invalidate each other's measurements (the next paragraph) and
  collide on helper names.

**Re-verify a merged agent's load-bearing measurement yourself, and re-read
`ISSUES.md` for entries the pair invalidated.** Each branch is sound alone and
the risk is in the combination. In one session an agent filed a P2 saying a
caveat would go quiet on rebounds, true of the code it could see; a second
agent had meanwhile changed that column's read, so measured on the merged tree
the two counts agreed exactly and the P2 did not exist. The reverse also
happens: a rebuild that fills a column only helps because another branch
pointed the reader at it. So after merging, run the gates on the merged tree,
re-measure whatever the reports claim, and re-rank the file - the entries an
agent writes are about the tree it had.

A load-time change is not finished when the branch merges. Have the dispatcher
run `association data load --tables <names>` serially (never three agents
against one warehouse), then re-measure and record the numbers in the entry
before closing anything.

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
