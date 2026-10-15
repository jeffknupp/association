# Working on `association`

Orientation for agents (and people) making changes here: the gates, the
conventions that are enforced, and the shapes of bug this project keeps
producing - what is *not* obvious from reading the code. It states rules,
each with its reason in a line. The incident behind a rule - what was
measured, when, in which commit - is in `ROADMAP-HISTORY.md`, section
"AGENTS.md's provenance, folded 2026-10-14", under this file's section
names; "(history)" after a rule points there.

- **Design:** `docs/architecture.rst` is the source of truth for how the
  system is designed (the four stages, the parser in front of the
  compiler, deterministic readers instead of better prompting). This file
  does not restate it.
- **Direction:** `ROADMAP.md` - the goal, where it stands, the next steps.
  Its rules bind every change to `query/` ("While the pipeline is
  rebuilt", below). `ROADMAP-2026-09.md` is the archived roadmap that
  "plan item N" and "ROADMAP step N" in code, commits and `CHANGES.md`
  cite. `ROADMAP-HISTORY.md` holds what each step measured, bought and
  cost; its working log gets an entry as each roadmap step lands, because
  the roadmap's status tables are rewritten as the work moves. Read the
  roadmap before starting a spike and keep the spike pointed at it:
  fixable things a measurement turns up go to agents or to `ISSUES.md`,
  not into the spike.
- **What is wrong:** `ISSUES.md`, ranked by priority. Read the entries for
  the area you are about to touch, and add what you find, including
  findings outside your task ("Recording findings").
- **What the source does:** `DATA.md` (ESPN's wrong values, missing games,
  labeling, per-table history). Read it before trusting a column.

## While the pipeline is rebuilt

The rewrite's rules (`ROADMAP.md`, accepted 2026-09-30; the four-stage
pipeline READ -> PLAN -> RUN -> SAY) are **standing rules since decision
D10** (Jeff, 2026-10-14): they bind every change under
`src/association/query/`, whoever makes it. Each is a gate or a test, so a
change that breaks one fails rather than drifts.

**The work is a rewrite, and it is worked like one** (Jeff's standing
instruction, 2026-10-04, to every agent and subagent):

- **Make the large change when it is the right one.** A change that moves
  a whole shape, deletes a module or reshapes a type is the expected size;
  "ambitious" is no reason to shrink it. The test of a change is that it
  is technically right and proved on the populations, not that it is
  small.
- **Do not route around essential complexity.** The work takes as long as
  it takes, and the complexity gates (radon, xenon) are met by splitting
  the hard part into named steps, never by leaving it where it was: a step
  that ports the easy half is how the middle got half-ported.
- **Fix what you can reach, whether or not it is your task** - in its own
  commit, gated like any other. `ISSUES.md` is for what you CANNOT fix as
  part of the work in hand, not for what is inconvenient, out of scope or
  larger than expected (the old twenty-line fix budget is gone).
- **Report every finding and every fix** - to Jeff, or to the lead agent
  that dispatched you - in the change's report, with what was measured.
  Silence about a fix is as bad as silence about a bug: the next decision
  is made on the report.

The rules:

- **New shapes are frozen: a new shape comes only by Jeff's lifting, and
  brings what decision D11 lists.** D11 (2026-10-14) replaced D4's plain
  freeze with "what a new shape brings" (`ROADMAP.md`, under "D4 lifted
  for the season ranking's shapes"); the freeze test's failure message
  still names D4. `tests/query/test_frozen_shapes.py`
  holds the 28 keys the grammar can name (`reading.PointShape`: relation,
  shape, `by`; the 26th to 28th by Jeff's liftings of 2026-10-13 and -14:
  `reading.PLAYERS_COUNTED`, a team's best single game
  `team_games/rows/measure`, `reading.TEAM_SEASON_HISTORY`), the page's 7
  renderers (one per `reading.Shape`) and the 4 scoping declarations left
  in `src`, read from the source by module and name: `RELATION_SCOPING`
  and `RELATION_SCOPING_EXCLUDED` on `query/player_relation.py`,
  `TEAM_RELATION_SCOPING` and `TEAM_RELATION_SCOPING_EXCLUDED` on
  `query/team_relation.py`. None is added, and `templates/`,
  `compose/present.py` and `compose/adapt.py` do not come back as source
  files. A P1 wrong answer is still fixed, in the code that exists. The
  sections below describe the readers and the sayer because they are what
  runs; they are not an invitation to add a shape. (history)
- **What the roadmap is deleting may not grow.** `scripts/check_ratchets.py`
  holds today's violations of each direction by name in
  `scripts/ratchets.json`:
  - `question_outside_the_reader` - a function outside the reader that
    takes the question's text (any parameter holding it): 1,
    `history.RunHistory.write`, the trace writer, which records the
    question and answers nothing.
  - `sql_outside_the_relations` - statements executed, counted per module
    (a listed module could otherwise grow freely): 77 in 12 modules, every
    one a relation's (`player_relation` 18, `season_line` 13,
    `team_seasons` 8, `conditions` 7, `netpoints` 7, `team_relation` 6,
    `player_games` 5, `standings` 4, `entities` 3, `shots` 3,
    `team_games` 2, `connection` 1), none in `compose/`. It counts
    `.execute`, `.executemany`, `.sql`, `.query` and the package's
    string-SQL helpers; a function that runs the SQL it is handed (a
    parameter, or an attribute of one), or hands its parameter on to such
    a function, is a door, counted at its callers, not itself (decision
    D6); and every `core.rows_of`/`core.values_of` call over a statement
    the compiler did not build, in the module making the call. The
    argument's tracing is `_origin`'s docstring in the script. Three
    counts were raised by hand during the P1 sweep, each with Jeff's
    say-so recorded in `ROADMAP.md`'s Phase 5 step 0 row: `season_line`
    12 -> 13 (`6634777`), `team_relation` 5 -> 6 (`eef7625`),
    `team_seasons` 7 -> 8 (`5339bdf`).
  - `regex_outside_the_lexicon` - a module but the lexicon that calls `re`
    or imports it at run time (an import under `TYPE_CHECKING` is none),
    or a module outside the reader that takes a reader's private pattern:
    empty.
  - `con_in_the_reader` - a reader function that takes a DuckDB
    connection, by name or annotation: empty.

  The empty ones are held as empty lists, so a new one fails. The check
  fails on a NEW entry or a count that grew, and on one GONE or a count
  that fell, so the lists only shrink. A NEW failure is fixed in the code
  - pass the Reading, not the question; narrow through the relation's
  shared steps, not a new `execute` - and adding to the list is Jeff's
  call. A GONE failure is the ratchet working: run
  `scripts/check_ratchets.py --shrink` (it only removes and lowers) and
  commit the shorter list with the change. A ratchet that reaches 0 is
  deleted with the thing it held. (history)
- **The reader does not import the answer side, and the answer side never
  reaches the model** (`[tool.importlinter]`). The contracts hold with no
  ignored edge. A new edge fails; an ignore is not the way past it.
  (history)
- **A change to the pipeline is proven on the recorded populations, one
  run per PUSH (decision D12, Jeff, 2026-10-14).** The feed
  (`scripts/stage_snapshots.py run --feed --workers 4`, about 80 seconds
  sharded) on the branch tip against a fresh run of the tip before it,
  `compare`d with nothing ignored but a field the push names, every moved
  answer enumerated in the commits and `CHANGES.md` and attributed to the
  push (a move no commit claims is bisected). The corpus run is the
  lead's, at the merge, per batch of pushes; the unit-test calls only
  where a stage boundary moves; the readings at the agent's option for a
  reader-only change. At every commit: the claims ledger's `report`, the
  ratchets, the grammar-drops test, the three Phase 5 harnesses
  (`~/association-research/stages/unread_meaning.py`,
  `grammar_rows_dropped.py`, `worded_declines.py`; D12 holds them at 0)
  and the gate chain.
  Non-negotiable: one broad measurement of what a user sees per push with
  every move named, and no refusal becoming a fluent wrong answer - and
  "fluent to refusal" is the allowed direction only where the fluent
  answer was wrong, which the offline compare cannot tell; the
  yardstick's key can, which is why a live run closes a phase. (history)

  The four populations, which the tools still measure when asked (the
  rewrite's per-commit rule until D12):
  1. **The corpus**: `scripts/stage_snapshots.py run OUT.jsonl` answers
     the 628 recorded questions through the whole agent with no model (the
     normalizer's recorded replies, the date pinned, DuckDB
     single-threaded, about two minutes) and writes what each stage
     produced: the reading (with the reader's verdict on the point), the
     planned query, the result's values, the answer. `compare` reports the
     first stage each question differs in and exits 1.
  2. **The unit-test calls** across a stage boundary
     (`tests/stage_calls.py`: the parser's two steps, the planner's
     `plan_point` and `refusal_result`, `compose.answer` with the reason
     it declined by, `Agent.ask`): run the suite on each tree with
     `ASSOCIATION_STAGE_CALLS=<dir>` and `compare-calls` the two
     directories. A call is keyed by its position among its test's calls
     and records each Reading whole, so an ADDED call or a Reading field
     added with an empty default moves every later call: align first with
     `~/association-research/stages/calls_aligned.py`.
  3. **The 2,710 readings** (`~/association-research/stages/reader_pop.py`:
     the 628 and the 2,082 outside them, read and planned, no answer;
     `reader_cmp.py` compares two in seconds).
  4. **The feed**: those 2,082 ANSWERED, `run --feed OUT.jsonl`, from
     `~/association-research/stages/feed_recorded.jsonl` (`feed_replies.py`
     writes it) - tree against tree on equal input, not production's
     answer. It is the one that sees an answer move on a question the
     corpus does not hold.

  Three things to hold to:
  - **Run the "before" tree, not a stored file.** The baseline is
    `PYTHONPATH=<before>/src ... run before.jsonl`; the first line of each
    file says which copy of the code it read - check it. Two runs of one
    tree are identical on every population, so ANY difference is the
    change.
  - **Identical means identical.** Everything compares text and all;
    `--values-only` (sentences left out) is only for a change the roadmap
    allows to reword an answer. One permitted move (Jeff, 2026-10-05): a
    decline the user sees becoming a `reading.Cause` the planner says -
    which moves the reading's verdict (`point_declined` ->
    `point_refusal`) and the answer's `answered_by` - is done as ONE
    commit of its own, every moved reading and answer enumerated in the
    commit message and `CHANGES.md` (the population, the count, before
    and after), the sentence identical or the change named. A decline or
    refusal reworded is likewise one enumerated commit.
  - **A boundary that moves is recorded on both sides first.** A step
    that replaces a boundary adds the new one to `tests/stage_calls.py`
    before it deletes the old one.
- **A word the reading does not depend on is counted, and a number among
  them is a bug.** `scripts/claims_ledger.py run` (`run --feed` for the
  feed; about twelve seconds, no model) reads each recorded question whole
  and again with each content word deleted, and reports the words whose
  deletion leaves the reading and the planned query unchanged - measured
  from outside the reader, so the count means the same across a rewrite
  of it. Both populations are HELD in `scripts/ledger_held.json`, as the
  ratchets are; today the corpus holds 681 unread words and 5 questions
  with an unread number (the five "100"s of "per 100 possessions", which
  the rate reads without the number), the feed 2,702 words, 24 numbers and 67
  disagreements, and neither any joined claim. `report` exits 1 on a
  grown count or a question new to a list, and - on a run of the whole
  population - on one that fell or left it, until `report --shrink`
  lowers the file with the change (it only removes and lowers). After a
  reader change, run it on both populations and read the unread numbers
  first. A count grows only by a named overcount exposed, said in the
  commit (Jeff's rulings, 2026-10-10, -11 and -13). (history)

  The Reading states the same list itself: `Reading.unread`, the content
  words no claim covers, by the ledger's own rule in the lexicon
  (`lexicon.content_words`, `CONTENT_STOPWORDS`, `MODEL_STAT_WORDS`; a
  word the model's names or stat account for is left out, as the ledger
  leaves it out). `report` names every question where the two lists
  differ and exits 1 on one: they measure one thing two ways, so a
  disagreement is a finding - a claim over a word the reading does not
  depend on, or a word a stage reads without claiming it. The corpus's
  three are held by question with their reasons (`HELD_DISAGREEMENTS`,
  the subject reading's, which is read once and never probed), the feed's
  in `ledger_held.json`; a held one that agrees now leaves the list with
  the change that cleared it. The other direction found answers: a word
  whose presence makes a rule read LESS ("2024 and 2025", two seasons read
  as none) is read by the deletion and claimed by nothing
  (`span.needed`'s `gives_up`). Joined claims are held by kind
  (`HELD_JOINS`, empty): a new one fails, since a word two rules of one
  tagger read is a bug. The ledger is blind to a word only the model could
  have dropped, and a word a rule matched and did not need ("games" in
  "last 10 games") is unread in both measures.
- **A typed value the grammar's text held is not dropped silently.** A
  line, an opponent, a companion, a window or a date a tagger typed into
  the grammar's text (`lexicon.GrammarText`) that leaves no trace on the
  Reading was dropped by the key's reading (`point.*_under`) before the
  planner could see it - the silent half of a fluent narrower answer.
  `tests/grammar_drops.py` holds the rule and the held list, and
  `tests/query/test_grammar_drops.py` checks every reader case against
  them: a drop is held by rule (`held_by_rule`: a companion who is the
  subject himself) or by question with its reason (`HELD`), and fails
  otherwise; a held question that no longer drops leaves the list with
  the change that cleared it. `~/association-research/stages/grammar_rows_dropped.py`
  measures the same over the 2,710 readings.
- **A caveat, a stated default or a definition is written through
  `query/notes.py`.** `note(kind, text, **facts)` for something about the
  data or a term the answer uses; `decided(kind, text, field=, chose=,
  ...)` for something the question left open and the system chose ("could
  the question have said it differently?"). Both hand the sentence back
  unchanged and, inside `Agent.ask`, record the kind and facts on the
  answer (`Answer.notes`, `Answer.decisions`) and in the snapshot's
  `remarks`. The kinds (21 notes, 8 decisions) and each kind's fact names
  are closed (`NOTE_KINDS`, `DECISION_KINDS`, `FACTS`): a new remark uses
  one, and adding a kind or a fact name is a deliberate edit there. Facts
  are plain values, never a sentence or an object. Wrap at the writer,
  once, not where the sentence is attached. `scripts/stage_snapshots.py
  remarks` lists a remark written and never reaching its answer; `compare`
  holds the remarks identical unless `--ignore-remarks`, which is only for
  a change whose point is to record more.
- **The target types are `ROADMAP-TYPES.md`**, a draft until Jeff has
  reviewed all of it (its "Decided" and "Still open" say which parts). A
  type is declared in code by the phase that first uses it, not before.
- **Every step deletes the path it replaces, in the same change.** No
  dispatcher between an old and a new implementation outlives its slice;
  that is how the middle got half-ported.
- **A test goes with what it tests.** A test whose subject is a structure
  the change deletes (a scoping table, a retired sentence, an
  intermediate slot) is deleted with it, not ported. A test of behavior a
  user can see moves to the stage that owns it, and only if the stage
  snapshots do not already hold the case. The change's report says how
  many tests it deleted and how many it moved.
- **`AGENTS.md` changes with the code.** A change that deletes what a
  section here describes rewrites that section in the same change.

## Before you commit

```bash
uv run pre-commit run --all-files   # all eighteen gates
uv run pytest -q -n auto            # fully offline: no network, no ollama
```

Both must be clean. Everything in `pre-commit` also runs in CI
(`.github/workflows/ci.yml`), so a green local run should mean a green PR.
Gate a commit as ONE `&&` chain (`git add -A && <gates> > log 2>&1 &&
git commit ...`), never `;`: a `;` chain once pushed a red commit.

**While iterating, run `scripts/check_fast.sh`** - every gate but the
Sphinx build, plus the whole suite, in parallel. It leaves out only the
docs build, the gate that catches a malformed docstring, so a commit
touching a docstring runs the full check. Nothing else may be skipped.
Measured on the OVH devbox (24 cores, 2026-10-02; the 8-core times are in
the history):

| command | time |
| --- | --- |
| `scripts/check_fast.sh` | 24s |
| the hooks it runs (all but Sphinx) | 4s |
| `uv run pre-commit run --all-files` | 17s (13s of it Sphinx) |
| `uv run pytest -q` | 87s |
| `uv run pytest -q -n auto` | 20s |

- **A slow test is found, not marked.** There is no `slow` marker. Before
  marking anything slow, find where its time goes (`pytest --durations`;
  time under `setup` means a fixture). DuckDB's Python API binds `?`
  parameters slowly, so rows inserted one statement at a time are the
  usual cost - a multi-row `INSERT` with bound values is no cure. A
  fixture's rows go through `tests/warehouse_rows.py`'s `insert_rows`, one
  Arrow table per table. (history)
- **A fresh worktree needs syncing first.** `uv run` creates the venv but
  installs no extras, so `uv run pytest -q` fails with `Failed to spawn:
  pytest`. Run CI's own line: `uv sync --frozen --extra dev --extra docs
  --extra web`.
- **The interpreter is pinned: `.python-version` says 3.14**, read by `uv`
  here and in CI. Moving the pin is one change: `.python-version`,
  `requires-python`, the ruff, mypy and pyright targets in
  `pyproject.toml`, then `uv lock`. (history)
- **A Python script a gate runs goes through that interpreter by name** -
  `.venv/bin/python scripts/x.py` in a hook, `uv run python scripts/x.py`
  in a workflow - never through its shebang, which CI resolves to its
  runner's own Python. After a push, read the CI run. (history)
- **`scripts/audit_dependencies.sh` is a CI check, not a hook** (pip-audit
  over `uv.lock`, extras included, needs the network; commits work
  offline). It runs in `.github/workflows/audit.yml` on every push and
  weekly. Run it by hand after changing `uv.lock`; an accepted advisory
  goes in its `IGNORED` list with a comment saying why it does not apply.
- **The CHANGES rule is also held per COMMIT**, by a CI job a hook cannot
  replace (`scripts/check_changes_per_commit.py`, `changelog-per-commit`
  in `ci.yml`): the hook reads only the index it runs on, and a rebase
  that folds commits or a skipped hook leaves a commit holding `src/`
  with no entry. Run it on a branch before pushing: `uv run python
  scripts/check_changes_per_commit.py --range origin/master..HEAD`. A
  commit it names is amended or split; a later commit cannot fix it.
  (history)

The local run has been *weaker* than CI three times, never the reverse:

- **`--all-files` means every file git knows about.** An untracked test
  is skipped entirely. `git add` first, then run the gates.
- **Read the exit status, not the output.** `pre-commit run --all-files |
  tail` hides a failure in the first hook (`ruff` is first). Redirect to
  a file and check `$?`.
- **A gate script has to work when run directly**, not only under `uv
  run` (which exports `VIRTUAL_ENV`; CI runs the scripts bare). Test a
  change to one with plain `bash scripts/x.sh`. (history)

What surprises people about the gates:

- **The docs gate rebuilds from scratch (`-E`), after deleting
  `docs/api/generated/`, and then reads the HTML.** An incremental Sphinx
  build skips pages whose sources did not change and re-emits none of
  their warnings, so `-W` passed locally where CI's clean build failed;
  autosummary never removes a stale stub page. `scripts/check_docs_markup.py`
  then fails the build on a docstring field marker printed as text
  (`:rtype:` after a line of prose), valid reStructuredText that `-W`
  cannot see. (history)
- **The hook lints more than CI's ruff step** (every tracked Python file,
  `docs/conf.py` included). Run the hooks, not the CI line, before calling
  lint clean.
- **mypy runs twice**, over `src` and `tests` separately, never as one
  invocation: combined, it resolves `association` two ways and reports
  errors neither root has alone.
- **`pyright --verifytypes` must stay at 100%.**
  `scripts/check_types_complete.sh` installs to a scratch dir first,
  because `--verifytypes` inspects an installed package. New public
  symbols need real annotations - no bare `dict`/`list`/`tuple`.

**`CHANGES.md`.** Any commit touching `src/` touches `CHANGES.md`, under
`## Unreleased`; a hook enforces it. The same hook checks that there is
exactly ONE `## Unreleased` heading (two branches each adding one merge
without a conflict, and `bump_version.py` renames only the first) and that
every released version keeps its own heading (the version in
`pyproject.toml` everywhere, every tag where the clone has tags; CI's
shallow clone has none and says so). (history)

**Adding a gate** follows the shape of the ones there, so local and CI keep
saying the same thing: pin the tool in the `dev` extra (`uv add --optional
dev <tool>`, which updates `uv.lock`), add a `language: system` hook
running `.venv/bin/<tool>` with its settings in `pyproject.toml` (or its
own rc file), add the matching `uv run <tool>` step to `ci.yml`, and update
the count on the `pre-commit` line above. Then break what it guards and
watch it fail. Every finding it reports on arrival is fixed or suppressed
with a written reason in the same commit - a gate that starts red gets
turned off.

## Conventions

- **Line length is 200, and `ruff format` is enforced.** Much of the code
  is prompt text and SQL that reads worse wrapped; the formatter *joins*
  long strings rather than fighting them.
- **A lint suppression carries its reason.** Beyond ruff's defaults the
  lint selects `DTZ`, `BLE`, `RUF`, `PERF`, `C4`, `SIM`, `RET`, `PLW` and
  `PLE` (`pyproject.toml`). A deliberate break says why on the same line:
  `# noqa: BLE001 - ...`. A bare `noqa` gives the next reader nothing to
  judge it by.
- **Dead code is a gate (vulture).** Something only a framework calls (a
  Click command, a FastAPI route, a pydantic field), or public API nothing
  here calls, goes in `vulture_whitelist.py` with a comment naming its real
  caller. Tests count as callers: a function kept alive only by its own
  test is deleted, not kept on that.
- **Import only what is declared (deptry).** A package imported directly
  is named in `pyproject.toml` even when another dependency pulls it in
  (`botocore` with `boto3`, `pydantic` with `fastapi`), since a release
  that stopped bundling it would break the import with nothing here
  changed. A dev or docs tool goes in its extra, never in the core list.
- **No function is more complex than radon grade C** (cyclomatic
  complexity 20), no module worse than C, the average no worse than B - a
  xenon gate. The way past it: split the body into named steps called in
  the original order, each keeping the comment that explains it. A pure
  refactor is proven by a golden comparison, not by the suite alone
  ("Verifying your work").
- **Every module lives in a package; the root of `src/association` holds
  only `__init__.py` and `py.typed`.** Where things go:
  - `cli/` - the `association` command (`commands.py`) and the default
    warehouse and data paths the scripts share (`paths.py`).
  - `nba/` - what both `fetch` and `query` need: seasons and Eastern
    dates, franchise names by season, coverage floors, NetPoints
    categories. It imports nothing else from `association`.
  - `fetch/` - the ESPN and NetPoints clients, parsing, the pipeline and
    the warehouse build; `fetch/repairs/` - load-time repairs of ESPN's
    faults and the tables built beside them.
  - `check/` - the coverage report.
  - `query/` - the reader: `parse.py` (the parser), `normalizer.py` (all
    the model sees), `grammar.py` (the rules that settle what the words
    ask), `lexicon.py` (every word and pattern the reader reads by), the
    seven taggers `span.py`, `window.py`, `cuts.py`, `period.py`,
    `line.py`, `split.py` and `measure.py` (which also holds the measure
    catalog), `calendar.py` (the situation's parsers over the lexicon's
    words), `subject.py`, `point.py`, `reading.py`; `entities.py` and
    `names.py`; the relations, each running its own statements -
    `player_games.py` with its shared steps `player_relation.py`,
    `team_games.py` with `team_relation.py`, `conditions.py`,
    `season_line.py`, `team_seasons.py`, `standings.py`, and the two
    declared relations `netpoints.py` and `shots.py`; the compiler's
    readers and the sayer (`compose/`, which runs only what the compiler
    built or a relation hands it); the coverage declaration
    (`coverage.py`); the renderers; the answering loop (`agent.py`).
  - `web/` - the local web interface.
- **The package layers are a contract.** `cli` > `web` > `query | check`
  > `fetch` > `nba`, `fetch` and `query` independent, the core free of the
  `web` extra's packages (`[tool.importlinter]`). A module that needs to
  sit elsewhere changes the contract, with a reason, never an ignore.
  Inside `query/` more contracts hold the reader apart from the answer
  side ("While the pipeline is rebuilt"); none ignores an import.
- **Call-time imports are absolute.** A relative import inside a function
  resolves against wherever the module lives *when it is called*, so a
  module move breaks it only when that code runs. Write `from
  association.fetch import ...` inside functions. (history)
- **Shell scripts pass shellcheck with every optional check on**
  (`.shellcheckrc`). The one that matters is SC2312: a command
  substitution nested in another command's arguments or a heredoc fails
  without tripping `set -e`, so assign it to a variable first. Braces on
  every variable are style, for consistency.
- **American spelling** ("defense", "offense", "serialize"), enforced by
  codespell (`en-GB_to_en-US`, `[tool.codespell]`). A flagged word that is
  right (a basketball token, a regex stem, a misspelling a test depends
  on) takes `# codespell:ignore <word> - <why>` on its line, or joins
  `ignore-words-list` if it recurs. `CHANGES.md` is skipped: released
  entries quote the spellings that were fixed.
- **Every public module, class and function needs a docstring** - a gate
  of its own, since autodoc renders an undocumented function happily and
  uselessly.
- **Docstrings are published** (`docs/api/index.rst` runs `autosummary`
  over the whole package), so they are reStructuredText (`` `x` `` is a
  *reference*; use ``` ``x`` ``` for a literal) and the docs build runs
  under `-W`: a malformed one fails the gate.
- **Mark public API changes with a version directive.** A new public
  function, class or module gets `.. versionadded:: X.Y.Z` at the end of
  its docstring; a renamed or reshaped one `.. versionchanged:: X.Y.Z`
  saying what moved. Use the version released NEXT, worked out from `git
  tag` and what `## Unreleased` holds, never from memory; while
  `## Unreleased` records a breaking change, the next *major*. The number
  can move under you (another branch's breaking change), so **re-check
  every directive added since the last tag in the pre-release audit**
  (`git grep -n 'version\(added\|changed\):: '` over the diff). Only the
  public surface is marked: internal helpers and the set of question
  shapes are outside the compatibility promise (the preamble of
  `CHANGES.md`). A module-level constant needs an attribute docstring (a
  string literal directly *after* the assignment) for the directive to
  attach. (history)
- **Comments explain *why*,** especially where the code looks odd. Most of
  the odd code here is load-bearing.

## The failure shape this project keeps producing

**A missing or too-narrow shape, never a broken one.** Every query failure
reported here looked the same: the system answered fast and fluently - a
*different question* than the one asked, because nothing covered the real
one and something adjacent matched. So **an answer that looks right is not
evidence**: check that the shape you added is the shape being exercised.

That is why the readers refuse rather than approximate. Each shape's row of
its relation's cell table says which of the relation's cells its words do
not state (`RELATION_SCOPING_EXCLUDED`, `TEAM_RELATION_SCOPING_EXCLUDED`,
keyed by the planned point's `PointShape`); the planner's one check reads
the point's typed cells against it (`compose.plan.cells_unhonored`); a
reader steps aside for a cell its words do not state, so the compiler's
own sentence answers; and the planner refuses a cell the relation cannot
honor at all, or one the shape refuses outright (`cells_declined`) -
instead of silently ignoring a window or a date and answering a
whole-season question for a single-game one.

**A reader on a relation does not declare, or apply, scoping of its own.**
The player-games relation (`query/player_games.py`, its shared steps in
`query/player_relation.py`) and the team-games relation
(`query/team_games.py`, its steps in `query/team_relation.py`) each carry
the narrowing ONCE, as typed cells applied in the shared steps
(`scoped_player`/`scoped_games`, `scoped_team`/`team_games`,
`condition_player`; the span by `span_of`, which resolves the typed
`Reading.span` once for every reader) and declared once (`RELATION_SCOPING`
and `TEAM_RELATION_SCOPING`, the cells each relation carries). The cells,
each declared on its family's type:

- the games' cuts (`reading.Cuts.CELLS`): `opponent`, `tenure` (the player
  relation's alone), `venue`, `date`, `situation` (parsed once into
  `reading.Situation` - a calendar narrowing, an alignment of the opponent,
  or words nothing reads, refused by value), `game_n`, `season_n` (the
  player relation's alone); `round` is a cut of no relation, refused
  wherever it is set;
- the period (`reading.Period.CELLS`: `period`, a quarter or a half), which
  changes what a read SEES of each game rather than which games: the
  relation rebuilds the period's line from the shots and plays
  (`narrow_periods`);
- the lines (`reading.Line.CELLS`): `line`, a stat the games are kept under
  or over (`narrow_measures` through `lines.measure_filters`), and
  `period_line`, a line in a quarter or half (`_apply_period_condition`);
  `opponent_line` and `season_line` (below) are cells of the reading
  (`reading.Filtered.CELLS`). The shape's OWN line (`Line.keyed`: a count's
  "30+ points", a record's "scores 30") is the point's predicate
  (`lines.threshold_line`) and sets no cell;
- the companions (`reading.Companion.CELLS`: `companion`, one cell because
  no table honors or refuses one role without the others; applied by
  `_narrow_player_games`/`condition_player`), and the with/without
  `split`;
- the window (`reading.Window.CELLS`: `window`, an end of the span with its
  count, cut after every other filter by `relation_window`; `ranked_by`,
  the measure a ranking of yes/no games is ordered by, the player
  relation's alone). A bare count is no cell: each reader's own parameter,
  the newest N on the games relations;
- the span (`reading.Span.CELLS`): `career`, `range` (`since`/`until`),
  `both` (both season types in one read);
- `rate` (`Measure.CELLS`), declared in no relation table: a unit narrows no
  games.

A shape's row is one `reading.ShapeCells`: the cells its words do not
state, each with a reason (`unstated`); of those, the ones the planner
refuses outright (`refused`); what it takes beyond its relation's table
(`taken`); where an unstated cell is declined when its reader is the
point's only answer (`declined`); whether it honors one named half of the
starter/bench split only (`sides`); what only a named player's or subject's
games carry (`named_player`, `named_subject`). A shape states its relation's
table and what it takes, less its row's exclusions
(`compose.plan.cells_stated`), and `cells_unhonored` reads the typed cells
against that - for the planner, for the season line's readers and for the
answer side, which steps aside before it asks a reader. No reader takes a
list of its own. Two source-reading tests in `tests/query/test_readers.py`
enforce it (watched to fail): a shape a reader reads with no row, a row
excluding a cell its relation does not carry or without a reason, a reader
taking a list of its own, or a reader writing `pgl.opponent_team_id = ?` or
`g.date >= ? AND g.date < ?` anywhere it reaches, fails the suite.

- **A new scoping dimension is one clause on `Narrowed`** plus a
  warehouse-verified test per reader it turns on - never a slot taught to
  one reader at a time (that is how twelve slots ended up honored on one
  template and one on its neighbor over the same relation).
- **An exclusion's reason is about the answer** ("one game is not a run"),
  never about the code ("not wired"), **and it is a key, never a
  sentence**: a `reading.DeclineReason` member, worded once by the sayer
  (`say.decline_reason_phrase`); a row given a sentence is refused where it
  is built, and a decline's cause carries the key as its `reason` fact.
- **A decline says each cell by what the words asked under it** ("games
  without Joel Embiid", "the last 5 games"): `reading.cell_facts`, phrased
  by `say.cell_phrase`.
- **When adding a reader, prefer refusing to guessing.** `resolve_*` in
  `query/entities.py` never guesses between candidate players; a ranking
  rejects a named player; a team's record rejects a limit.

**The same bug has a mirror image: a refusal that names the wrong cause.** It reads as
honest, so nothing looks wrong ("No NetPoints fingerprint on record for
season 2026" for Maxey, when "Maxey" had resolved to a namesake retired in
1994). Before writing a "no data" message, check which fact is actually
missing - the season, the player or the match: they are different
sentences, and the wrong one sends the reader to look in the wrong place.
(history)

### Names

**A model invents names, and an invented name resolves** - the
worst-behaved version of the shape, because the answer is a correct table
about a real player the question never mentioned. **Any** name a model
supplies may be fiction, so a name is checked against the question before a
reader reads it. `subject.read_subject` reads who the question is about
from its own spans, ONCE per question, in `parse.read_route`;
`subject.settle_subject` settles that reading under the key the grammar's
rules chose (reading no name, asking the warehouse nothing); and
`subject.apply_subject` writes the names onto the Reading's one typed
subject (`reading.Subject` on `Reading.who`) inside the parser's last step
(`parse._reading`), whose `Reading` is all the agent answers from. Do not
add a second `read_subject` call to the live path: three readings of one
question disagreed on 18 of the 628. `Agent.ask` takes a question and
nothing else; a test that needs a particular reading stands in for
`read_route` through `tests/routed.py` (`Routed`, `ask_routed`,
`with_subject`, `reading_of`), and a harness answers recorded QUESTIONS
with their recorded normalizer replies (`scripts/stage_snapshots.py`,
`scripts/preview_answers.py`). (history)

What counts as the question supporting a name (`subject.question_supports`)
is deliberately generous, because the model's expansions are usually the
useful kind: the word itself, a near spelling (Damerau-Levenshtein,
rapidfuzz's, the metric the entity index uses in DuckDB), a nickname, the
initials ("KAT", "SGA"); any ONE word of the name is enough. Three rules
for what follows, the third the one first got wrong:

- **Replace only from what the question itself names, and only when the
  count is exact.** `players_named_in` is strict: a span must equal a
  *whole word* of exactly one player's name (a substring reads "highest
  scoring game" as Jaron Blossomgame, a word boundary reads "with" as Jeff
  Withey, a one-letter span reads the "s" of "Jokic's" as John S.
  Williams). A name the question never held never reaches the subject's
  players (`parse._read_route_players`): it rides on `Reading.misread`, and
  `apply_subject` replaces it from the question's names where the count is
  exact (`Subject.supported`) or reports it for the by-name refusal.
- **Trim a name back to the part the question holds only where that part
  is ambiguous.** "luka", "jokic", "embiid" reach one player each, so the
  model's completion stands; "brown" is ten players, and choosing among
  them is a guess nothing downstream can see, so the parser cuts the
  completion back and lets resolution decide (`parse._as_typed_part`). A
  nickname the question used and a name its own span resolves to are left
  alone; `find_players` applies the nickname table first. The cut runs on
  the model's own spelling, before the reading respells it. (history)
- **When it cannot be repaired, say so by name** - a refusal that names the
  player, not the plain refusal for want of a reading. Refuse only where
  the answer would be about that player (`reading.PLAYER_ASKS`): a stray
  name on a question about two teams' meetings changes no answer. (history:
  the fall-through agent that drew a fingerprint for a player who does not
  exist.)

**A model drops names, not only invents them.** The reading takes the names
from the question itself, so a reply that drops the second name still draws
both, with or without a comparison word; and it reads an ordinary word as
nobody ("plot jokic's fingerprint from his best season" draws no Travis
Best; `tests/query/test_one_writer.py`).

**A name nobody typed correctly is not repaired by fuzzy-matching the
question's leftover words** - measured and rejected: a spurious player in
29 of 51 corpus questions ("season" is one edit from Tari Eason), and it
did not find the one meant. The answer says a player is missing instead
(`subject.compared_but_unmatched`, which takes "vs" and not "compare",
since "compare Jokic's fingerprint to last season" compares seasons).

**Why "embiid" specifically: which name a model substitutes moves between
sessions** (lowercase
"embiid" in a comparison frame came back as two different players on two
days), so there is nothing to special-case - only a reason to check every
name against the question. (history)

**Before saying nothing matched, check whether something nearly did.**
`find_players` requires every token, so one fabricated word buries a
player. `entities.suggest_players` backs a multi-word name off to its
surname (exact matching on one fewer token, not fuzzy), then looks for near
spellings - the only thing that reaches the user's own typo. A suggestion
naming more than `MAX_CLARIFY_CANDIDATES` players is dropped: reading out a
directory is not a suggestion.

**Typos are the entity index's job, never the model's, so one near
spelling is an answer, not a question.** `entities.read_near_spelling`
takes the near-spelling pass's result when it holds exactly ONE player (for
`resolve_player` and the chart resolver alike) and says so in the answer
("'embid' matches no player exactly and was read as Joel Embiid, the only
near spelling on record - spell the name exactly to ask about someone
else."). Still asked: two or more near spellings; the surname back-off
(the given name is somebody else's, so the one player a surname lands on is
as likely wrong as right - only the near-spelling pass, which needs EVERY
word close, defaults); a team's name ("Hawks" is one edit from Spencer
Hawes). It is for a span already given as a name, never for the question's
leftover words. A team's near spelling is read the same way in one place
only: the word a versus word leads, near exactly one team's word
(`entities.near_team`) and no player's whole word or near spelling, kept
as typed and read by `entities.resolved_team`, which says so. (history:
the measured default and ask rates.)

**A reasonable default beats a question, where the default is visible and
can be corrected** (Jeff, 2026-09-21: *"A default that chooses reasonably
but happens to be wrong is better than no answer as long as it can be
easily corrected. If the query returns Dean Wade and I have no way to refer
to Dwyane Wade, that's the only time there's an issue."*). Both conditions
are required: the answer **displays the value it used**, and there is **a
wording that reaches the alternative**, which the answer states. A silent
default is still the worst failure shape. Applied to names: with no season
asked, a name several players share means the one who played the last
season of the span (`entities.resolve_player`), and a full name whose owner
has nothing in the seasons asked yields to the one namesake who does
(`_named_in_full`) - each saying so ("'maxey' was read as Tyrese Maxey, the
only match who played in 2025-26. Marlon Maxey also matches - use the full
name, or name a season he played, to ask about him."), carried by
`entities.collect_name_readings` and attached in `agent.py`, so a reader
called directly does not show it. The sentence is not optional (in 67 of
the 124 shared surnames that resolve, a retired namesake has more games).
It is elimination, not the prominence tiebreak: two namesakes who both
played are asked about. Before extending the rule to another open slot,
check both conditions. (history)

**A question too short to be one is refused unread** (Jeff, 2026-09-29):
fewer than `parse.MIN_QUESTION_WORDS` (three) words is the `too_short`
cause (`parse.too_short`), which `Agent.ask` asks before the normalizer (no
model call) and the planner says (`compose.plan.refusal_result`, answered
`"refused"`) with a generic sentence ("I couldn't understand your question,
'Tatum rec'. Please try re-phrasing it."). Do not add heuristics for longer
nonsense (F097), do not file issues for such questions, and do not grade
them as needing a clarification. (history)

**A best match is only safe where a wrong one is visible.** A chart titled
with the winning name hides the wrong name exactly when the wrong name is
why no plot is drawn. `resolve_chart_player` narrows candidates to those
with a row in the chart's table (`entities.narrow_to_available`) and asks
when more than one survives: it *eliminates* who cannot be the answer and
never chooses between two who can - which is what makes it allowed where
the prominence tiebreak above `PLAYER_NICKNAMES` was measured and rejected.

## Working on the query path

The pipeline is parser -> planner -> a reader over a relation -> the sayer
-> a deterministic answer, and a refusal naming why where nothing has a
reading of the question. There are no templates and no fall-through agent
(measured and retired at 5.0.0; history). Since Phase 5, step 5
(`6a85bc5`) every refusal and decline is a cause the planner says, answered
`answered_by="fast"` - since looking at the question and having something
to say is an answer - including words that ask for no shape (`no_shape`,
"what its words ask has no reader"). Only a question too short to be one
is answered `"refused"` (`too_short`; 2 of the 628, 0 of the 2,082). A
question the yardstick grades as "fell through" is one of these
refusals.

### The reader

**The reader is the parser.** The model only copies names verbatim and
picks a stat (`query/normalizer.py`); `parse.read_route` reads everything
else from the words into the Reading it returns. The parent grammar
(`parse.PARENT_GRAMMAR`) and a child grammar name what the words ask, a
`reading.PointShape` (`Reading.asked`); the grammar's rules settle it
(`grammar.settle`); and the taggers, one per family, read the filters into
the Reading's typed fields, in the order each reads what the ones before it
settled (`parse._tagged`: the split, the lines, the measure, the period,
the cuts, the window, the span last). The filters are the Reading's own
fields (`reading.Filtered`: `who`, `cuts`, `companions`, `lines`,
`measure`, `span`, `split`, `period`, `window`, `minimum`), a frozen base
every planned query inherits too (`Query`, `TeamQuery`, `NetPointsQuery`,
`ShotQuery`, `TeamSeasonQuery`), with the cells declared once on it
(`Filtered.CELLS`, `cells()`): a relation's step narrows by the query
itself, and every test states its reading in the typed fields and a shape
by its `PointShape` key. The populations record the typed Reading
(`stages._reading_record`).

- **Everything but the names and the stat is read from the words, or it is
  silently absent.** A model-era slot key (a season, an order, a limit, a
  period, a companion list, ...) is no argument the parser takes: the words
  are the one source.
- **A filter family is read by ONE tagger, from the lexicon, and claims the
  characters it read.** A tagger's claims are cut to the words it could not
  do without - each word deleted in turn and the tagger asked again over
  its context (`span.needed`) - and the words outside them its reading turns
  on are claimed beside them under the family's name, except a word whose
  presence made it read LESS (`gives_up`: "2024 and 2025" read as no season
  is two unread years). The grammars, the grammar's rules and the point
  reader claim the words their decisions turned on (`span.read_by`). The
  subject reading is read once and never probed: it declares its claims
  where it reads (`subject.subject_claims`). A claim inside another's
  characters folds into it; two that overlap without one holding the other
  join into one named for both (`span.claimed`), and a join fails the
  ledger's report ("A word the reading does not depend on is counted"). The
  content words no claim covers are `Reading.unread`.
- **A tagger reads the words alone, never the key the words ask.** The
  tagger reads every candidate the words hold (`line.LineCandidates`,
  `window.WindowCandidates`, `span.SpanCandidates`, `measure.MeasureWords`,
  the period and the cuts as read); ONE function per family in
  `query/point.py` applies the key (`lines_under`, `measure_under`,
  `period_under`, `cuts_under`, `window_under`, `span_under`, with the key
  sets `THRESHOLD_ASKS`, `PERIOD_ASKS`, `ORDER_ASKS`,
  `LIMIT_COUNTS_SEASONS`), because the key changes what the Reading SAYS (a
  bare "30 points" is a count's own line and nothing on a log). Whether a
  relation honors a cell is the planner's different check. A claim is cut to
  the words the tagger AND the key's reading need together.
- **Patterns live in the lexicon alone.** `query/lexicon.py` holds every
  word and pattern the reader reads by, named, with its reason beside it,
  and nothing that reads the warehouse or the answer side; it is the
  reader's lowest layer. No other module calls or imports `re` at run time
  (the `regex_outside_the_lexicon` ratchet, empty); `subject.py` compiles no
  pattern of its own; and no second copy of a word list exists - one period
  reader (`period.which_period`) for every "first | 1st | q4", one measure
  catalog.
- **The families, by tagger** (the vocabulary to read the code by; each
  tagger writes its typed value on the Reading and claims what it read):
  - *Period* (`query/period.py`): `read_period` reads one quarter or half
    into `reading.Period` (`Reading.period`); `which_period` is the ONE
    reader of which period any words name (the grammar's rules use it to
    choose among the three period keys, `line.read_period_line` for a
    condition's period). "By quarter" reads no period (the breakdown is the
    point's `by`); a period as a condition on which games count is
    `period_line`.
  - *Cuts* (`query/cuts.py`): `read_cuts(question, CutsContext)` reads a
    venue, a situation (`SITUATION`: a weekday, a month, a holiday, "since
    <day>", the opponent's conference or division, or words nothing
    narrows by), one calendar day (`CALENDAR_DATE`, never on a career
    question, where it is a situation), a range of days (`DATE_RANGE`, one
    situation across seasons, handing the span its seasons), a round, a
    game of a series, an ordinal season - over the opponent and absent
    teammates the subject reading read - into `reading.Cuts`, the situation
    parsed once (`situation_of`: a `CalendarNarrowing` or
    `AlignmentNarrowing`, which the relations apply and never re-read).
    `reading.cell_set(scope, cell)` is the one reading of a cell name
    against the filters.
  - *Lines* (`query/line.py`): `read_lines` reads every line on a stat
    ("30+ points", "20+ point 5+ assist games", "scores 30", "fouled out",
    "under 14 fta", "with 25 minutes") with how the words state it
    (`LineCandidate.how`), and `point.lines_under` writes `reading.Line`
    values (`op`, `value`, `as_typed`, `keyed` for the shape's own line,
    `narrows`, `period`, `whose` - the opponents' figure, the
    `opponent_line` cell only a team's record over a line takes - and
    `per`). A shape that IS a line (`point.THRESHOLD_ASKS`) reads a bare
    "N stat" as its own; every other reader only what the words state
    outright (`lexicon.OVER_LEADS`, `LINE_OVER_OR_AT`, `EQUALS_LINE`). On a
    ranking of players' seasons a line is the SEASON figure (`Line.per`,
    the `season_line` cell, `season_line.ranking_statement`), counted by
    `reading.PLAYERS_COUNTED`. `threshold_named` is what the grammar's
    rules ask first (a count with no line is a ranking).
    `read_period_line` runs before the grammar and BLANKS its words to
    spaces, so later claims keep their offsets. Lines are read from the
    WHOLE question, a companion's phrase included, and built from the
    words, never from a model pair.
  - *Measure* (`query/measure.py`, the tagger and the one catalog):
    `read_measure` reads the words and `point.measure_under(asked, words,
    MeasureContext)` the measure - the words over the normalizer's key
    (`measure.named`: `MEASURE_GRAMMAR`, then the longest box-score word),
    then the rules in order (`names_a_stat`, `team_metric_named`, the
    given-up words `lexicon.GIVEN_UP_VERB`, `GIVEN_UP_KEYS`, a rate) - into
    `reading.Measure` (`key`, `as_typed`, `how`, `unit`, `whose`, `side`,
    `shot_value`, `category`, `won`, `beside`). The model's key is CONTEXT:
    the words win where they name a measure. `measure.CATALOG` says how each
    `MeasureKey` is read on each relation and whether it is a yes or a no of
    a game (`yes_no`; the compiler's `BOOLEAN_MEASURES` read from it);
    `measure.ALIASES` holds every spelling, through `key_of`. The answer
    side's lookups (`measures.resolve_metric`, `stat_measure`,
    `stat_column`, `log_extras`, `period_split_measure`, `streak_column`,
    `team_metrics.resolve_team_metric`) take the `Measure` and read the
    catalog's facets, held to `tests/query/measure_spellings.json`.
    `Measure.as_typed` is what a refusal prints. The point's `measures` are
    typed (`measure.measured`); the compilers' names are built once where
    the planner hands the point over (`compose.core.measure_names`,
    `compose.team.team_metric`).
  - *Aggregate*: `reading.Aggregate` (`NONE`, `PER_GAME`, `TOTAL`,
    `COUNT`, `RECORD`, `LINE`) is the PLANNER's (decision D5):
    `compose.plan.aggregate_of` derives it from the point's `read_as` and
    its measure, and its docstring is the table. The point reader stamps
    none.
  - *Window* (`query/window.py`): `read_window(question, WindowContext)`
    and `point.window_under` read which rows are kept and from which end
    (a count and an end, a history's count of SEASONS `Window.of`, one game
    of a player's line, `ORDER_WORDS`, a team ranking's `rank`, a yes/no
    ranking's `by`) into `reading.Window`. A ranking's stated qualifier is
    `reading.Minimum` (`window.read_minimum`, `point.minimum_under`,
    applied where the metric qualifies by its unit,
    `metrics.qualifies_by`, refused by a cause elsewhere).
  - *Span* (`query/span.py`): `read_span(question, SpanContext)` and
    `point.span_under` read the seasons and the season type into
    `reading.Span`, returning `Claim`s (`(start, end, what)`). It runs LAST,
    because its rules read the typed window. The point reader writes no
    span of its own: a span it settles apart from the scope's is
    `Reading.subject_span`, the one typed override.
- **Names arrive as typed.** A typo reaches the entity index, which reads a
  single near spelling as that player and says so; nothing corrects it
  upstream.
- **A name is recognized from the in-memory index, never by a statement.**
  `query/names.py` holds the `players` and `teams` tables as two indexes,
  loaded once per question inside `Agent.ask` (`names.loaded()`); a lookup
  outside a block raises `names.NotLoaded` rather than quietly issuing a
  statement per lookup - every test runs inside one (`tests/conftest.py`),
  and a script that reads names enters its own. The lookups reproduce
  DuckDB's own semantics, checked against it (its `lower`, its word split,
  Damerau-Levenshtein over UTF-8 bytes, RE2's case folding, ILIKE's
  ASCII-only lowering, table order where the SQL had no `ORDER BY`); not
  reproduced: the order of two players sharing a `display_name`, which
  DuckDB left unspecified. A name lookup takes the index
  (`find_players(players, ...)`, `find_teams(teams, ...)`,
  `team_abbreviations(teams)`, `suggest_players(index, ...)`), never a
  connection; a caller on the answer side hands it
  `entities.players_of(con)`, `teams_of(con)` or `names_of(con)`. Do not
  write `FROM players` or `FROM teams` on the reader's path. The reader
  holds no connection at all: `parse.read_route`, `parse._reading`,
  `classify_span` and `subject.read_subject` take a `names.NameIndex`, read
  by `Agent.ask` before the parser runs (`entities.names_of`), with
  `names.ActivePlayers` (the players with a box score in the latest season,
  which narrow a typo near more than five players) read on first use
  through `NameIndex.active`. (history)
- **The subject has one reader and one writer.** Its reader is the subject
  reading (`subject.read_subject`, once per question), which reads and
  decides nothing (under no shape, `None`); `subject.settle_subject` writes
  the key and why with no rule run; the parser's last step reads the
  SETTLED subject and settles nothing. Its writer is
  `subject.apply_subject`: ONE typed value, `reading.Subject(kind, players,
  teams, position)` on `Reading.who`. The kind is the reading's; one player
  is the one a reader reads (`Subject.player`), two or more a comparison
  or a pair; one team the one the read is about or narrows to
  (`Subject.team`). The grammar's rules are HANDED the typed subject and
  the opponent (`grammar.settle(question, asked, subject=, opponent=)`) and
  read the subject the grammar names and the team words through the subject
  reading's own readers (`subject.subject_named_in`,
  `subject.team_words_in`); a name they settle under the key they choose is
  the reading's word, written on the typed subject. A test hands its names
  to the rules typed (`tests/routed.py`'s `staged`, `who=`, `opponent=`).
  The subject's words are the lexicon's. No cell: no relation table
  declares a name, and a position group narrows the league's read through
  `Query.position` (`point._everyone_point`).
- **Who stands beside the subject has one reader: the subject reading.**
  "without X", "with X out", "when X and Y play", "in games X missed", "when
  X scores 20+ points", "vs X" are read by `subject._conditions_read` (and
  `_versus_companions`), each name with its role as a typed
  `reading.Companion` (`player` as spelled, `side` `own` or `opponent`,
  `predicate` `played`, `absent`, `started`, `bench` or `reached`, a
  reached role's `Line`) with a claim on the phrase; ONE writer puts them on
  the Reading (`apply_subject`'s `_apply_companions`, `Reading.companions`,
  on the path that refuses an invented name too). The grammar's rules and
  the taggers are HANDED the typed companions (`grammar.settle(...,
  companions=)`, `parse._tagged`) and read no name themselves. Do not give a
  stage a name regex again: two readers of the same phrases disagreed on
  seven of the 628. (history) Three rules of that reading:
  - **A name is read by its position in the phrase** (`_name_segments`: the
    words after the keyword, split at "and", "or" and commas, ended by a
    word no name holds), not by the model having copied it.
  - **An absence keeps a name nobody resolves, as typed** - refused by that
    name downstream, since dropping it would answer the games he played
    too. A player who PLAYED has to be one the reading found: an ordinary
    word is a name there only when it stands alone ("with green", never
    "with best shooting"), and a near spelling counts only for a word that
    is no ordinary word and is near exactly one player.
  - **"with and without X" is the split over X**, read from its "without".

### What the words ask

**What the words ask comes from the question's own words, never from the
model.** The grammar names a `reading.PointShape` - the relation, the shape
and the `by` of the point the words ask for bare (`Reading.asked`,
`Subject.asked`; `None` for no shape), one key per retired intent
(`reading.PLAYER_LOG`, `PLAYER_LINE`, `PLAYER_RANKING`, ... `TEAM_COACH`); a
sentence says a key in words (`say.asked_as`), the trace and the records by
`reading.key_path` (`relation/shape/by`). The kind is no part of the key: it
is the subject reading's, read before the grammar, which the grammar is
keyed BY. The key is what the words ASK; the planned `PointShape` is what
the point IS (one key reaches up to four planned keys as the typed filters
move it). Every set or table keyed by what the words ask is a set of those
keys or a predicate on one (`reading.PLAYER_ASKS`, `TEAM_ONLY_ASKS`,
`PLAYER_REQUIRED_ASKS`, `SUBJECT_RESTORABLE_ASKS`,
`OWN_TEAM_RESTORABLE_ASKS`, `on_a_declared_relation`,
`point.DEFAULT_POINTS`, `TEAM_SEASON_POINTS`, the key sets of the taggers'
families), and every key is the compiler's.

- **A new shape is a grammar row or a rule, with no prompt edit** (and only
  under D4's lifting): `PARENT_GRAMMAR` names the parent by the subject's
  kind, the grammar's rules assign `grammar.CODE_ASSIGNED_ASKS` from the
  text, and nothing moves on another question. A refusal especially: a
  question nothing can answer needs the model's help least. `coach` is the
  worked case: the word is unmistakable, a bare surname is deliberately not
  matched ("nurse", "rivers"), and the point reader refuses it by a cause
  (`reading.Cause("no_coach_table")`) the planner says (`COACH_REFUSAL`).
  A refusal the reading comes to has no point and so no coverage floor
  (`coverage.sources_for(None, ...)` is empty): a floor's "no data for
  1996" beside it would name a second, wrong cause.
- **The children come where the subject's KIND is known**
  (`subject.KIND_ASSIGNED_ASKS`, `_CHILD_GRAMMARS`): a child of a parent (a
  count of 30+ point games under a log, a history under a line, a streak
  under a team's record) is named by its words AND gated on the kind the
  reading settled, which keeps two teams' meetings off a count and a
  question with no player off a shot distance. The child is named from the
  grammar alone (`subject.child_named`), the grammar's rules run ONCE under
  it (`parse._read_route_staged`), may decline it (a count with no
  threshold is a ranking) and only then run again under the parent, and the
  taggers run once, under the key that stands. Add a case to
  `port_check.py`'s corpus (`~/association-research/intent-shrink/`) and to
  `tests/query/test_subject.py` for each wording a grammar gains.
- **A grammar reads the values the taggers typed, never their words.** What
  a grammar reads is `lexicon.GrammarText`: the question's words with every
  character a tagger typed written as `lexicon.TYPED` (U+FFFC, the same for
  every kind - a pattern sees that something stands there, never what),
  and beside them what was typed at each character, a closed
  `lexicon.TypedKind`: a line's number by how it is stated (`line_plus`,
  `line_at_least`, `line_number`), a line's lead and stat (`line_lead`,
  `line_stat`; `line.LinesNamed`), the opponent by its versus word
  (`opponent_against`, `opponent_versus`, `opponent`;
  `subject.opponent_read`), each companion's phrase (`companion_with`,
  `companion`; `subject.Subject.companion_phrases`), each window of games
  (`window`; `window.WindowCandidates.of_games`), a day written in numbers
  (`date`; `cuts.CutsRead.dated`). The child grammars read each line typed
  whole (`LinesNamed.marked`); the parent grammar and the rules each line's
  NUMBER typed (`numbered`), the rest typed over it (`parse.grammar_text`).
  A value is asked BY VALUE: a parent row is a predicate over the text
  (`lexicon.GrammarRow`: `Words`, `Stands`, `StandsAfter`, `StandsBefore`,
  `AnyOf`, `AllOf`, `NoneOf`) - `parse.PARENT_GRAMMAR` holds the rows, the
  lexicon the words; a pattern that reads a value WHERE it stands among the
  words is a `lexicon.Ask`, a regex whose values are `lexicon.typed(kind,
  ...)` fragments matched against the text spelled by kind
  (`GrammarText.spelled`, built only when the ask is searched; a character
  of that alphabet in the question is written as `TYPED`, so no question can
  spell a value - decision D7). Every typed value keeps each character where
  it stands, and a rule's probe deletes a word with the values in place
  (`GrammarText.without`). A row that reads a number or a versus word reads
  the typed value beside it (`N_PLUS`, `LINE_NUMBER_NO_PLUS`,
  `MEETING_WORDS`), and a new row does the same; the count's child grammar
  reads no bare digits beside its lines, so a number no line is read from
  stays in `Reading.unread`. (history)
- **A refusal names the missing thing, never only the slot.** A shape the
  warehouse has no column for is a cause the READER comes to from the
  words - a kind in `reading.CAUSES` with plain facts, carried on the
  Reading (`point_refusal`, `refused`, `unsupported`, `defaulted`) and said
  by the planner through `say.refusal_phrase` - never a sentence built
  where the question is re-read after the parser. Before writing the
  refusal, check what the source actually serves ("ESPN does not publish
  coaches" was the obvious sentence and is false).

### The planner, the readers and the sayer

**Every shape the words ask is the compiler's, and the answering loop asks
it once.** `agent.py`'s `_run_compiled` hands `compose.answer(ctx,
reading)` the point the parser read once (`Reading.point`) and the
planner's verdict, planned once per question AFTER the parser
(`compose.plan.plan_point`; the parser reads and does not plan). The
planning is ONE value, `compose.plan.Verdict` (kept on `Agent.query`): the
planned query, the refusal it came to unworded (a `result.Refusal`) or the
decline it gave the point up with; what the point is read and said as is
`compose.plan.planned_shape`'s. The compiler runs the planned point and
never reads the question. A caller with a Reading of its own reads the
point into it with `parse.with_point`. Nothing in the package may reach
ollama, and it narrows a relation only through the shared steps.

- **The point declares what it is read and said as: ONE field,
  `Reading.read_as`** (decision D9), a `reading.PointShape` - its `shape`
  (`reading.Shape`: `scalar`, `rows`, `ranking`, `comparison`, `split`,
  `runs`, `chart`), its `by` (what one row is, how a scalar is reduced or
  what rows are ordered by; `""` where no reader takes the point and the
  compiler's own sentence answers) and its `relation`
  (`reading.PointRelation`: `player_games`, `player_periods`,
  `player_seasons`, `team_games`, `team_periods`, `team_seasons`,
  `team_snapshots`, `netpoints`, `shots`). Every `Reading(...)` in
  `query/point.py` names it, by a key's constant where it is one of the
  grammar's keys. The planner plans that key as it stands
  (`plan.point_shape`; its one move: a season-line point re-planned at the
  game level is on `player_games`, held by a test over every reader case)
  and derives the compiler's skeleton and source from it
  (`plan.skeleton_of`; `Query.source` is `"seasons"` on `player_seasons`).
- **The answer side chooses by shape, never by intent or label.**
  `compose.answer` picks the reader from `_ROUTES` by the planned key
  alone; the relation tables' rows and `coverage.SOURCES` are keyed by it.
  No table names a shape by a retired name: the trace and the records name
  a key by `reading.key_path`, a decline says what the words were read as
  asking in the shape's words (`say.asked_as`, framed by
  `say.nothing_answers`), a cause carries the key and never a label (its
  `shape` fact; the sayer's phrase tables are keyed by `PointShape`), and
  no sentence or page prints a retired name. `say()` chooses the sayer from
  the headline body's type and the one field that says what it is
  (`Scalar.how`, `Grouped.by`, `Rows.by`, `Chart.kind`) - never
  `Span.source`, the relation or a facts key.
- **What the reading refuses is a cause, said by the planner.** Where the
  point reader refuses (a ranking by a stat nothing ranks, a shape over a
  line missing its stat, its number or its subject, a matchup without two
  players, ...), the Reading carries a `reading.Cause` - a kind from the
  closed `CAUSES` (63 kinds) and plain facts - and the planner says it
  (`compose.plan.refusal_result`): the reader builds no sentence, and a new
  cause is an entry in `CAUSES` and a phrase in the sayer. It is raised as
  `reading.PointRefused`, a kind of `Unsupported`, so a shared helper the
  answer side also calls (`lines.measure_filters`,
  `measures.streak_column`) stays a decline there. Where the point reader
  hands the question on (another relation's question, a team-only shape, no
  reading of the subject) it is a cause too, said at a decline's place
  rather than the point's (`compose.say.POINT_DECLINES`, read by the
  planner: `period_relations_question`, `team_relations_question`,
  `team_only_question`, `no_subject_reading`; Phase 5, step 5, `7ee992b`).
  A decline the user would see as "Nothing here answers this question"
  becomes a Cause in one enumerated commit.
- **A shape's own decline is a cause too** (`reading.ShapeDeclined`, raised
  by `compose.plan.cells_declined` and `compose._read`; `cannot_honor`,
  `no_reading_of_point`), said by the planner word for word at the
  decline's place - after the coverage floor and the reading's causes - and
  answered `"fast"`. `None` is refused with the decline's sentence after
  the floor and the reading's causes have had their look.
- **What the words come to before any point is the parser's to read and the
  planner's to say:** `Reading.refused` (a championship, both ends of the
  span named at once, `window.contradicting_ends`, a window taken in each
  season, `window.per_season_window`, a fact no table holds,
  `lexicon.ABSENT_FACTS`, a player named where the shape reads none) is said
  before any reader runs; `Reading.unsupported` (a playoff round, an age, a
  quarter's stat nothing rebuilds, ...) only where the answer side
  declined, so a question the compiler answers is answered; and
  `Reading.defaulted` - a word carrying a meaning the reading defaulted
  past: `unread_number`, `unread_stat`, `unread_window`, `unread_date`, by
  the lexicon's `MEANING_*` classes, read in the parser's last step
  (`parse._reading_from_route_defaulted`), claiming its words - only where
  the planning produced a query, so a question that would be answered
  fluently past the word is refused by name. The classes are the taggers'
  own words, under guards the harness shares: a word that restates the
  shape ("record", "stats") is in no class, a leftover of the read
  measure's own words names no other stat (by stem since step 7: the
  "point" of "three points made", `parse._leftover` over
  `lexicon.letter_words`), and a slashed figure is no day. The window and
  date classes still refuse some correct answers ("top scorers", "who may
  lead"); the lead's narrowing of them is in flight. Its harness is
  `~/association-research/stages/unread_meaning.py` (the done table's
  coverage row; it read 2 on the corpus at `3dc37821`, lacking the stem
  guard). Nothing after the parser re-reads the question for any of the
  three. (history)
- **An answer is `Answer`, stamped by the loop.** `answer.Answer` (the
  text, the data, the files), returned by the sayer and the compiler's own
  sentence, is stamped by the answering loop with the question, the path,
  the timing, the decisions and the planned shape, and answered `"fast"`
  with the name readings and the coverage caveat attached - including a
  clarification or a "no match".

**A shape is a reader and a sayer, and nothing else answers it.** What
travels between them is typed:

- **A reader returns `Result | Refusal | Clarify`** (`query/result.py`),
  never a sentence. A refusal found at RUN is a `Refusal(kind, facts)` with
  a kind in the closed `result.RUN_CAUSES` (a fact found in the warehouse),
  disjoint from `reading.CAUSES` (a fact read from the words), held apart
  by a test; a question back is a `Clarify`. The relation modules
  (`no_narrowed_games`, `no_games`, `condition_team_no_games`,
  `entities.clarify`, `coverage.floor_refusal`) return the same types.
  ONE phrase table words every cause, `say.refusal_phrase(kind, facts)`,
  beside `note_phrase` and `decision_phrase`; a new refusal is a kind in
  `RUN_CAUSES` and a phrase there. `Refusal.shown`/`under` say what the page
  shows beside the sentence.
- **A cell the read applied is a typed value, not a key**
  (`Narrowing.cells`, the `result.Cell` union, read by type:
  `narrowing.cell(Role)`, `lines()`, `period`). What the sentence needs
  beside the body is ONE frozen record per shape (`result.Facts`:
  `CountFacts`, `LogFacts`, `LineFacts`, ...), read through
  `say._facts(result, Kind)`, which raises on another shape's record:
  there is no untyped `facts` mapping, and a shape that needs a new fact
  adds a field to its record. A read that found nothing keeps its body's
  shape and gives its reason as `Result.empty`, a `Refusal`.
- **A note is written as data and said once.** The reader builds
  `Note(kind, {...})`; the sayer phrases it (`say.note_phrase`, ONE phrase
  per kind) and records it through `notes.note`, so the answer's remarks are
  the Result's notes; the compiler's own sentence phrases the same kinds
  (`compose.core._box_notes`). `tests/query/test_answer_notes.py` reads a
  `Note(kind, {...})` as a write when it checks every kind is written.
- **The reader executes the compiled statement; the sayer keeps the
  words.** A reader compiles the planned point (`compose.core.compile_query`;
  `compile_over` when it already holds the settled subject) and runs that
  statement, so no statement is written beside the compiler's; the sayer
  builds the heading, the table and the notes from the Result's values and
  takes the Result and nothing else (the import contract "The compiler's
  sentence reads no warehouse" holds `compose/say.py` and
  `compose.sentence` to it).
- **What a relation measures lives on the relation; what a sayer says lives
  in the sayer.** A relation's step writes no sentence: a refusal it finds
  is a typed `result.Refusal` or `result.Clarify`. A statement a reader
  needs that the compiler does not build is its relation's: a relation
  function builds it, runs it through the compiler's one door (a call-time
  import of `compose.core.values_of` or `rows_of`) and hands back rows or
  values, and the reader keeps its words and its Result - no `compose/`
  module executes a statement of its own. The shared steps live with their
  relations under public names, with no private alias kept.

Where each shape is read (the compiler's readers in `compose/`, each over a
relation's statements):

| reader module | reads |
| --- | --- |
| `logs.py` | a player's or a team's log |
| `records.py`, `splits.py`, `stats.py`, `periods.py` | a record over a line (a player's, `read_team_record_when` a team's), splits, a line over narrowed games (a `Scalar` by the compiler's `line` aggregate), a quarter or half |
| `counts.py`, `highs.py` | a count of games over a line; a single game's high |
| `runs.py`, `pairs.py`, `presence.py` | a run (`read_streak`, `read_team_streak`), two players' meetings, a team with and without named teammates |
| `rankings.py`, `seasons.py` | the league's leaders by a season-line metric (`season_line.rank_season_line`); a player's season line, history and comparison (the season line's reads: `season_line.season_row`, `career_row`, `advanced_row`, `history_rows`, `history_total`) |
| `standings.py`, `team_stats.py`, `team_records.py`, `team.py` | a team's record and seasons (`query/standings.py`, `team_seasons`), a team's history (`read_team_history`), the team compiler |
| `netpoints.py`, `shots.py` | the declared relations' readers and their drawing |

- **Two shapes have skeletons of their own.** A streak is the `run` shape:
  the longest runs of consecutive games one predicate holds along
  (`compose.core._compile_run`, in Eastern-date order; a game with no box
  score inside a spell he played ends a run), read into the `Runs` body and
  said by `compose.say.say_streak`. A matchup is the `pair` shape: two
  named players' lines over the games they met in (the pair relation
  `player_games.paired_rows_sql`, `compose.core._resolve_pair`,
  `_compile_pair`), read by `compose.pairs.read_player_matchup` into a
  `Grouped` by `subject` and a `Rows` detail part, said by
  `compose.say.say_player_matchup`; the games the two shared as teammates
  are the pair relation's own read (`conditions._teammate_games`).
- **The declared relations keep their own readers and renderers** (the
  decision "Charts are declared shapes"). NetPoints
  (`reading.on_a_declared_relation` over `DECLARED_RELATIONS`, planned as
  `compose.netpoints.NetPointsQuery`; every statement in
  `query/netpoints.py`): `read_player_netpoints`, and `read_fingerprint`
  into a `Chart` that `compose.netpoints.draw_fingerprint` writes with
  `query/radar.py` BEFORE `compose.say.say_fingerprint` words it - a reader
  names no file that does not exist yet. The "vs" note for a fingerprint
  that drew one polygon is read by the parser (`Reading.left_out`, a
  `reading.LeftOut`) and said as the `name_left_out` decision
  (`say.say_left_out`, attached by `agent._unmatched_fingerprint`). Shots
  (`compose.shots.ShotQuery`; every statement in `query/shots.py`):
  `read_shot_chart` and `read_shot_distance`, over
  `shotchart.SHOT_VALUE_SQL` and the court's `HAS_POSITION_SQL` and
  `SHOT_DISTANCE_SQL`, the chart drawn by `compose.shots.draw_shot_chart`
  over `court.render_court_html` before the sayer.
- **A shape with a default point** (`point.DEFAULT_POINTS`) is declined
  beyond the scoping its row states (`declined="plan"`) by the planner, and
  read and said with nothing after it: a decline is refused, never handed
  to the compilers' own sentences (`compose._read_only`, a route marked
  `only`).
- **A reader says what its row states and steps aside for a narrowing
  beyond it**, so the compiler's own sentence, which states every narrowing
  the relation applied, answers; a narrowing the relation cannot honor at
  all is refused by the planner. A change to a reader is proved on the
  stage snapshots and the unit-test calls. (history)
- **A team can be the subject, not only a narrowing.** `compose/team.py`
  (`TeamQuery`/`TeamResult`/`run_team`, `point.team_read_point`,
  `sentence.team_sentence`) is a second compiler over the team-games
  relation, kept apart on purpose: nothing in it reads or is read by the
  player subject's functions. An UNNARROWED question reads the season's raw
  TOTAL from `team_season_stats` ("how many has it made" is not "how many
  per game"); a NARROWED one sums the relation's game-level columns through
  `scoped_team`/`team_games` - never a hand-written clause - and a box-score
  count over narrowed games sums their own team box rows, saying how many
  had none (`compose.team.team_figure`). A percentage is no total and is
  declined; a figure no team game carries is refused by name
  (`team_figure_unread`). `read_point` tries `team_read_point` before the
  league-wide reading, and nothing after the parser restores a subject
  ("magic" is also Magic Johnson's given name); `team_named_in` restores a
  dropped team from the question's own team word, as `players_named_in`
  does a player.

### The model

- **What the model sees lives in `query/normalizer.py`'s constants,
  alone**: `NORMALIZER_PROMPT`, `NORMALIZER_SCHEMA` (its stat enum
  `NORMALIZER_STATS`), `NORMALIZER_NUM_CTX`. `normalize()` only decodes the
  reply. So a diff to `parse.py` or `grammar.py` cannot change what the
  model returns, and a diff to those constants always can.
- **A prompt and its constrained schema must agree**: a value the prompt
  teaches that the enum lacks can never be emitted, and silently becomes
  something else. `test_the_prompt_and_the_schema_agree`
  (`tests/query/test_normalizer.py`) holds the examples to the enum, and
  `test_every_intent_is_reachable_from_the_reader`
  (`tests/query/test_grammar.py`) holds every shape reachable from
  `PARENT_GRAMMAR`, `CODE_ASSIGNED_ASKS` or `KIND_ASSIGNED_ASKS`. The
  reader's behavior on a wording is a case in
  `tests/query/reader_cases.json`, regenerated by `scripts/reader_cases.py`.
- **A prompt has a token budget, and ollama enforces none**: it truncates
  an over-length prompt silently and head-first.
  `test_the_prompt_leaves_room_for_the_question_and_the_reply` holds the
  prompt (~330 tokens) and `NORMALIZER_NUM_CTX` (2048) together with
  `normalizer.estimate_tokens`; keep such a check beside any prompt this
  project sends. (history)
- **A slot the schema does not require is a slot the decoder may never
  consider, and no prompt wording fixes that** - which is why
  `NORMALIZER_SCHEMA` requires both its fields
  (`test_the_schema_asks_for_the_names_and_the_stat_and_requires_both`).
  The opposite holds too: **a required slot is filled whether or not the
  question asked for it**, so the measure tagger drops the MODEL's key on
  the whole-line readers where the words name no stat
  (`measure.names_a_stat` over `lexicon.STAT_WORDS`; a loose list is safe
  there because a missed word widens a comparison to a line that still
  holds the stat, and nowhere else; a comparison drops the words' key
  too, since its reader refuses a named stat). Of the two ways out, prefer reading the
  value **from the question text** (costs nothing, moves no other slot) to
  making a slot required (which crowds out others). (history)
- **Hash `NORMALIZER_PROMPT` and `NORMALIZER_SCHEMA` before and after a
  change**: if both match, the recorded replies still stand and the stage
  snapshots are the whole check. **Any edit to the model's prompt moves
  what it returns on unrelated questions** (the 3B is that sensitive) (its length as much as its content), so
  after one the recorded replies no longer stand: re-record them and make a
  live run the record, and assert in a case only what changes the
  *answer*, never the encoding the model picked. (history)
- **The recorded replies are this machine's.** The snapshots, the ledger
  and every offline harness put the recorded replies in the model's place,
  so they prove what production does only while the model says the same.
  After a move, an ollama upgrade or a new pull of the model, re-record
  before trusting an offline proof: every recorded question through
  `normalizer.normalize`, one caller, in file order. The SAME question
  asked twice in a row can get two replies (the second from the cached
  prompt); distinct questions in any order repeat exactly. (history)
- **Only one ollama caller at a time.** Two callers on one CPU-only ollama
  corrupt the output silently or wedge it in a reload loop. A live run is
  the only caller: confirm it with `pgrep` first.

### Regression nets and the research repository

- **The parser has three regression nets; add to them whenever you find a
  misreading in the wild.** Cheapest first: `tests/query/test_parser.py`
  and the reader cases, a case per wording a table gains, watched to fail;
  the stage snapshots (all 628 recorded questions - 277 yardstick wordings,
  75 hold-out questions nothing was tuned on, 276 paraphrases - stage by
  stage against the tree before), which see a table change move some other
  wording and say whether it moved in the reading or only in the answer;
  and the yardstick's live run, graded blind, which is the record.
  `~/association-research/yardstick-v2/run_offline_parser.py` predicts a
  live run's graded rows before the model is asked.
- **`~/association-research` is a repository** (private,
  `jeffknupp/association-research`; its own `AGENTS.md` says what is live
  and what is history): the yardstick, the reply files, the baselines and
  each spike's harnesses are versioned there. Commit there when you add a
  run, a baseline or a harness.

## Working on the fetch path

`data pull` is checkpoint-driven and must stay cheap to re-run: a pull over
seasons already on disk makes no request and rebuilds nothing. Three
things keep that true, each easy to break:

- **Write through `Pipeline._write_rows`, never `storage.write_rows`.** It
  records the table in `Pipeline.written`, and the CLI reloads exactly
  those tables; a direct `storage` write is a Parquet file the warehouse
  never loads - no error, a table quietly one pull behind.
- **Anything built from "what this run fetched" merges with disk, never
  replaces it.** A run collects only from the endpoints it called, so a
  pull that skipped everything checkpointed sees almost nothing. (history)
- **Scope league-wide sources to the seasons asked for.** NetPoints is one
  flat file for every season: a pull of 2024 has no business rewriting
  2026's file (that rewrite once made an "everything is complete" run
  rebuild the whole warehouse).

**A change that alters warehouse data is not finished until the warehouse
holds it** - a parser fix, a view, a column, a derived table, a fetch fix.
A fix merged but never loaded looks done and stays wrong in every answer.
Do both halves:

- **The fetch path produces the corrected data by itself.** A fresh
  `association data pull` of an affected season comes out right with no
  manual step; a test pins the correction with a fixture in the shape of
  the real bad row.
- **Backfill what is already on disk and in the warehouse:**
  - built at load time (views and derived tables in `fetch/warehouse.py`):
    `association data load`, or `association data load --tables <names>`;
  - a parser or fetch fix (the Parquet on disk is the old code's): re-fetch
    with `association data pull --seasons <range> --force`, adding
    `--include-pbp` for plays or shots; the pull reloads what it wrote;
  - anything narrower (a list of event ids, one athlete's career): a
    one-off script, **but only if it runs the real code path** - fetch
    through the `Pipeline` methods, write through `_write_rows`, load
    through `warehouse.build` or `association data load` - committed under
    `scripts/` so the backfill can be re-run.

  Never patch a Parquet file or the DuckDB file directly, and never run an
  `UPDATE` against the warehouse: the next `data load` silently undoes it
  and a fresh pull would not reproduce it. Both commands default to
  `./data/parquet` and `./nba.duckdb`, which a worktree lacks: run them
  from the main checkout, or pass `--data-dir` and `--db-path`.
- **Then re-measure** against the rebuilt warehouse with the query that
  found the problem, and update or delete the `ISSUES.md` entry. If you
  cannot run the backfill (no network, no permission to write the
  warehouse), say so in your report, give the exact command, and leave the
  entry open with "fixed in code, not yet backfilled".

**Fetching is latency-bound, and the fetch path is threaded.** `--workers`
(default 4) runs the per-item loops through `Pipeline._map`. Shared state
on `Pipeline` takes `_state_lock` (`written` and `glossary` do), and
anything writing a path two workers might both write needs a unique temp
name (`storage.write_rows` does - two games sharing a player both cache his
bio). `--rate-limit` bounds the request rate across all workers; raising it
alone does nothing. (history)

**A NetPoints date is not an ESPN date.** NetPoints names each daily file
for the US Eastern date of its games; ESPN stores a UTC tip timestamp, and
matching on the UTC day is wrong for some real schedule either way round.
The game's own Eastern date is read off the timestamp
(`NetPointsGameIndex` in `fetch/parse.py`, one exact lookup, since a team
plays at most one game per Eastern date). Load-bearing:

- **The real Eastern clock, written out rather than read from a tz
  database.** A fixed five-hour shift is wrong in the midnight-to-1am hour,
  which is exactly where ESPN stores a game with no tip time.
  `nba/season.py` holds the US daylight-time rules by hand (checked against
  zoneinfo for every day 1976-2039), and **every** stamp-to-date conversion
  goes through `eastern_date`, `eastern_date_sql` or
  `eastern_day_utc_range`. Do not write another `- INTERVAL 5 HOUR`.
- **A date holding two of one team's games resolves to neither** - a team
  cannot play twice in a day, so a duplicate key is ESPN's clock being
  wrong, not a choice.
- **The UTC window survives as a fallback**, in the old order, for games
  stored hours from when they were played. (history)

Check it against the source's own box score: the daily file carries `pts`
beside the NetPoints values (the parser drops it), a free cross-check that
a row landed on the right game; `scripts/check_net_points_games.py` runs it
over a season.

**Filtering a read by season prunes no files.** The trees are read raw,
not hive partitioned (the reason is in the comment above `TABLES`), so
`WHERE season = 2024` over `read_parquet` filters after reading every
file. Count once and group; do not filter in a loop. (history)

**A full `warehouse.build()` is memory-hungry, and needs two settings**
(both set in `_tune`):

- `preserve_insertion_order=false` - without it, loading `plays` dies with
  `could not allocate`. Each table is its own statement, so an OOM leaves
  the earlier tables replaced and the rest silently at their old contents.
  Row order carries no meaning in these tables.
- `enable_external_file_cache=false` - DuckDB keeps the Parquet a statement
  read resident after it ends, accumulating across a build's loads over
  this tree's many small files. That failure is a SIGKILL with nothing in
  the traceback (dmesg explains it): `memory_limit` bounds the cache but
  not the overhead above it. Turning the cache off is the lever that
  works. (history)

`union_by_name=true` is not the thing to drop for speed: it fails outright
on real schema drift (`venue_id` is VARCHAR in the 1993 files and absent in
others).

## Working on the web path

`association web` is a thin layer over the same `Agent` the CLI uses.

- **The server answers one question at a time, and that is not caution.**
  ollama keeps one KV cache slot per model, so two questions in flight
  evict each other's prefix and both come back slow. `AgentRunner` holds a
  `threading.Lock` (not `asyncio.Lock`: everything below is blocking, and
  FastAPI runs `def` endpoints in a threadpool) for the whole of `ask`.
- **The trace sink is swapped under that lock**, the only thing that makes
  swapping it safe; if the serialization goes, that swap goes with it.
- **Nothing in `web/app.py` may import a model client.** `Answerer.ready` is
  a property on the runner so the health check does not reach ollama from
  the API layer, which keeps the web tests offline by construction.
  Enforced twice: import-linter forbids `web.app` reaching `ollama` by any
  chain, and `test_importing_the_api_layer_loads_no_model_client` checks a
  fresh interpreter. A type-only import of the query engine goes under
  `if TYPE_CHECKING:`. The whole suite runs with no network and no ollama,
  and that stays true.
- **Events carry trace lines verbatim.** Do not parse a trace line back
  into fields: everything a client acts on - the path that answered, the
  planned shape (`Answer.shape`, a `reading.PointShape`), the timing, the
  artifacts - is on the `answer` event as values.
- **The page is one self-contained HTML file** with its CSS and JS inline,
  declared in `[tool.setuptools.package-data]`; `serve()` checks it exists
  at startup. Verify packaging by installing the wheel into a fresh venv
  *outside* the repo and loading the page - importing the module proves
  nothing about the HTML.
- **`fastapi`/`uvicorn` are the `web` extra**, so CI syncs `--extra web`,
  and a missing install prints the `pip install 'association-py[web]'` line
  rather than raising ImportError.
- **`GET /api/artifacts/{name}` serves a directory a person owns, and the
  name is checked twice:** an allowlist regex rules out anything shaped
  like a path, and resolving the file and requiring it to sit directly in
  the resolved output directory catches a symlink with an innocent name.
  Keep both, and test the guard directly as well as through the route
  (Starlette 404s most traversal names on its own, so a route-only test
  proves less than it looks).
- **Chart iframes run with scripts off.** `court.py` and `radar.py` emit no
  script and a test asserts it. `allow-same-origin` is load-bearing
  separately: it is how the page reads the chart's height.
- **The page renders an answer by its planned shape, never by a label.**
  `static/index.html`'s `RENDERERS` holds one renderer per `reading.Shape`,
  read off `Answer.shape`; each names the bodies its shape's readers say
  (`bodies`: a draw and the `Answer.data` keys it `needs`) and chooses one
  by the shape's `by`, the relation where two readers of one `by` differ,
  and the compiler's own body (`skeleton`) where no reader took the point
  (`of`); a chart's body is its artifacts' frames (`chartFrame`). An answer
  with no shape is its sentence. A change to a renderer is proven by
  rendering every recorded answer through the page before and after
  (`~/association-research/stages/page_renders.py`, Chromium). (history)
- **The page's JavaScript has one behavioral check, and it is not a
  gate.** `test_renderers.py` parses the renderer table, runs each
  renderer's choice of body in node on every case and syntax-checks the
  script with `node --check`; key events, carets and the clipboard need a
  browser, so `scripts/check_web_ui.py` drives the real page in Chromium
  (`uv run --with playwright python scripts/check_web_ui.py`; playwright is
  deliberately outside the `dev` extra, deptry's ignore says why). Run it
  when you touch the page's script. It needs no ollama, no warehouse and no
  network, and serves `static/` over loopback, not `file://`, because
  `navigator.clipboard` exists only in a secure context.
- **Print the URL with `flush=True`**: stdout is block-buffered off a
  terminal, and with an ephemeral port the URL is the only way to find the
  server.

## Data gotchas

- **A season is named for the year it ends.** 2023-24 is season `2024`
  (`nba/season.py`).
- **"This season" is the latest season with games on record, not the
  calendar's** (the calendar turns over on October 1, weeks before a game).
  `Agent.ask` answers inside `season_on_record(latest_season_on_record(con))`,
  which caps `current_season()`; the fetch path pulls `calendar_season()`,
  uncapped. A harness that must answer the same on any day sets
  `ASSOCIATION_TODAY`: the stage snapshots, every research harness and the
  test suite (`tests/conftest.py`) pin 2026-09-30, and a new harness does
  the same. (history)
- **NetPoints tables disagree about `season_type`.** `net_points_player`
  has its own *string* column (`net_points_season_type`, "Regular
  Season"); `net_points_player_game` and
  `net_points_player_game_fingerprint` use the numeric 2/3;
  `net_points_player_fingerprint` has none. A numeric filter on the string
  column matches nothing, with no error.
- **Only six fingerprint categories partition the total**
  (`FINGERPRINT_PARTITION`: two_pt, three_pt, free_throw, turnover,
  rebound, foul). The other 15 overlap; summing all 21 is meaningless.
- **Do not conclude a source does not publish something from one file's
  schema.** Check a value against the season columns and look at what the
  site's own page fetches: ESPN Analytics publishes two objects per date,
  and field NAMES read as a taxonomy were integer counts. `DATA.md` ("The
  play-type split exists per game as well as per season"). (history)
- **`net_points_player_game_fingerprint` is LONG, the only such table**:
  one row per player per game per `category` (the wide shape would change
  with every category ESPN adds), the categories normalized to the season
  file's column prefixes on the way in (`net_points_category`, over
  `FINGERPRINT_CATEGORIES`). The query side pivots.
- **A single game's fingerprint is drawn in that game's net points, not per
  100 possessions** (a per-100 rate over ~30 possessions turns one corner
  three into a league-leading figure), its percentiles ranked against every
  player-*game* of the season. `Unit` carries the labels with the numbers,
  so a per-game plot is never captioned "per 100 poss".
- **Each table starts in a different year, and the gaps are ESPN's.** A
  question is answerable only as far back as its *narrowest* table, and no
  pull fills them in. `DATA.md` ("Coverage floors") has the table;
  `association/nba/coverage.py` is the enforced copy. Three rules:
  - **Select a postseason by the calendar year it was played in, never by
    label** (`team_relation.team_span_clause`, `team_metrics.games_scope`,
    `check_coverage.py`): before 1993-94 ESPN labels a season by the year
    it STARTED.
  - **Season 1993 is a phantom** (its events are 1994's rows). Treat 1994
    as the earliest real regular season, and **key joins over that era on
    `season` as well as `event_id`**, or every 1993-94 player-game is
    listed twice.
  - **`player_season_stats` reaches back to 1977 but is a survivor
    sample**, so a lookup and a ranking have different floors
    (`first_ranking_season`).
- **Shot coordinates measure y from the rim (`court.HOOP_Y`), and
  `points_attempted = 0` means unlabeled, not zero points.** **Never filter
  `points_attempted` for a shot's value**; read `shotchart.SHOT_VALUE_SQL`,
  which derives it and keeps the per-season refusals and caveats beside it.
  Free throws carry a position through 2018. The method that caught it:
  **where a column has a sibling that restates it, fit against the sibling
  before trusting a constant.**
- **ESPN's career endpoint copies some regular seasons into the
  postseason, so read `player_season_stats_deduped` for postseason lines**
  (the raw table still has them; the view drops a line claiming more than
  28 games or repeating the regular season's games and points exactly).
- **Whole team-seasons of box scores are empty** (Chicago and New Orleans,
  2013-2018: players listed as playing with NULL minutes and every stat 0,
  a NULL team row): a sum over those years is short, and a streak or a
  with/without split cannot tell whether a player sat. **Say how many games
  a per-game answer could not see** (`_empty_box_scores`). Where those games
  are rebuilt from play-by-play, a measure the rebuild never measured (a
  rate over attempts, turnovers, minutes: `core.unread_on_rebuilt`) is read
  over the fetched games alone, carries its own count
  (`"<measure>_games"`), qualifies a ranking on that count, and the answer
  says the games it could not read (a `lines_rebuilt` note). **Do not
  confuse it with the team-box-only fault** (Vancouver 1996: all-NULL team
  rows beside real player rows - per-player answers are fine, team reads
  are not). `DATA.md` has the counts. (history)
- Query connections to DuckDB are **read-only**, as a hard guarantee.

**The floors are enforced, not just documented.** `association/nba/coverage.py`
holds them (`COVERAGE`, one entry per queryable table), and
`query/coverage.py`'s `check_coverage(shape, scope)` refuses a question
whose planned point lands under one. Add an entry whenever a reader reads a
new table, and declare the shape's tables in `coverage.SOURCES`, keyed by
the planned point's `reading.PointShape` (one entry per route in
`compose._ROUTES`, a test holds them equal; resolved per question where the
table depends on the measure; `RELATION_SOURCES` for a point no reader
takes). The answering loop checks the floor of the planned point
(`compose.plan.planned_shape`, on every verdict) over the point's own scope;
a reading with no point has no floor. Load-bearing:

- **It returns the refusal rather than raising it** - the opposite of a
  reader's decline, deliberately: a decline lets the compiler's own sentence
  try, and nothing does better under a floor, where every reader's tables
  are empty.
- **A lookup and a ranking have different floors.** A survivor sample holds
  a star's real line but not a representative pool to rank (1980's holds
  seven players). `first_ranking_season` is the second floor, applied where
  the planned shape is a `ranking`. (history)
- **A missing season and an unrepresentative one need different
  sentences** (`Floor.unrepresentative`): "no data for 1980" beside a real
  1980 line is the false-cause answer the other way round.

A partial season (2002 play-by-play is about half a year) is answered with a
caveat; a *phantom* season (1993) is declared so the checker verifies the
duplication. `scripts/check_coverage.py` verifies every floor against a
built warehouse, as `check_nicknames.py` does the nickname table (these are
claims about the data, and pytest runs offline): run it after editing
`COVERAGE` and after any pull that reaches further back. "Usable" there is
deliberately not "present" - 82 games where a league plays 1,100 is rows,
not a season.

## Verifying your work

The habits that caught real bugs here, roughly by how often they paid:

- **A refactor is proven by a golden comparison, not by the suite alone.**
  Call each function with many inputs (a corpus, the tests' own cases, one
  per branch) against the original code and again after, and diff the full
  results (text, data, exceptions, written files, types as well as values).
  Import each copy explicitly (`PYTHONPATH=<tree>/src
  PYTHONDONTWRITEBYTECODE=1`, print `association.__file__`), and perturb one
  token of the refactored code to prove the comparison can fail. A green
  suite says only that the tested inputs still pass. **On the query path
  the comparison is built:** `scripts/stage_snapshots.py` ("While the
  pipeline is rebuilt") - use it rather than a hand-rolled harness; the
  perturbation is still yours to make.
- **Run the original twice before calling a float difference a
  regression.** DuckDB's parallel `SUM` is not bit-reproducible (up to 185
  of 588 rows differ in the 15th digit between two runs). Compare
  aggregates rounded, or establish the noise first.
- **Read the fixture; do not guess what it contains.**
- **Exit 0 is not proof.** Check the rendered output, the built artifact,
  the actual string. `cmd | tail` reports *tail's* status: pipe to a file
  and read `$?`. (history)
- **Check rendered HTML in a real browser engine** (headless Chromium):
  WeasyPrint does not resolve CSS variables inside SVG, so the charts
  render in the wrong colors there. The `docs/_static` shots are made in
  Chromium.
- **Install and run it, for anything packaging-related**: the wheel and
  sdist go into a fresh venv *outside the repo* and the CLI runs; nothing
  else proves the entry point and `py.typed` survived.
- **Prompt text is load-bearing**: if a refactor touches it, hash the
  prompt constants before and after and compare.
- **A same-size edit inside one second can be served from a stale `.pyc`**
  (the cache is validated on mtime to the second and size). When a script
  rewrites a module and re-runs it - perturbation tests especially - clear
  `__pycache__` or set `PYTHONDONTWRITEBYTECODE=1`.
- **A guard is worth nothing until you have watched it fail.** Perturb what
  it claims to protect and see it catch that. A stale `.pyc` or a
  `SyntaxError` in the harness looks exactly like a green run.
- **Use `scripts/perturb.py` rather than a hand-rolled harness**, and watch
  for the harness reporting CAUGHT for a reason unrelated to the
  perturbation:
  - **a red baseline** - one unrelated failing test makes every
    perturbation exit non-zero;
  - **a filtered run** - `-k` silently skips a test whose name does not
    match: run the whole file, never `-k`;
  - **a retyped anchor** - a hand-escaped anchor that matches zero times
    perturbs nothing: slice anchors out of the file, never retype them, and
    assert the match count before running.
- **Import success is not execution.** `python -c "import ..."` proves the
  module parses and nothing else: call the function, with a fake client if
  need be.
- **A script run from a worktree resolves the INSTALLED package**, the
  main checkout's (a checker once reported "29/29 floors check out" about a
  field that did not exist in the code checked). Run it with
  `PYTHONPATH=<worktree>/src`, and treat a green check from a worktree as
  unproven until you have confirmed which copy it read.
- **Branches built in parallel collide on private helper names,
  silently**: git merges two same-named module-level `def`s without a
  conflict and the later one wins (mypy's `no-redef` and ruff's F811 see it
  only on the merged tree). After merging parallel work, run the gates on
  the merged tree and scan for duplicated top-level names before reading
  anything into either side's green tests. (history)
- **Chain a merge's resolver, its `git add` and the commit with `&&`, never
  `;`**: a resolver that stopped, followed by `;`, landed conflict markers
  that every gate passed (a marker is valid Markdown).
  `scripts/check_conflict_markers.sh` now refuses a tracked file holding a
  start or end marker (a bare `=======` is a Markdown underline).
- **Never rewrite `CLAUDE.md` in place.** It is a symlink to `AGENTS.md`
  that `git ls-files` lists, so a `sed -i` or `perl -pi` over a file list
  replaces the link with a copy (`git status` shows `T CLAUDE.md`) and the
  two drift. Exclude it from bulk edits and edit `AGENTS.md`.
- **`/tmp` is a shared tmpfs.** Several agents' outputs filled it mid-run,
  where a failed write looks like a diff or an unrelated crash. Compress or
  hash large outputs, delete them once compared, and check `df -h /tmp`
  before a large run.
- **Never gate rebased commits with `git rebase --exec` running the suite
  or the hooks.** The rebase exports `GIT_DIR`, and tests that build a
  scratch repository wrote into the real one (its config, a worktree's
  HEAD, its index). `tests/conftest.py` strips every `GIT_*` variable
  before a test (`without_git_variables`), but a hook is not a test: squash
  or reorder with no `--exec`, then run the gate chain once on the tip as a
  plain command. (history)
- **A DuckDB `SET` is per-connection**: assert it on the connection the
  code under test used, not a freshly opened one.

## Saying what you measured

A wrong status report costs more than a wrong patch, because the next
decision is made on it.

- **Name the population, not just the number.** "19 of 2,062 combined rows
  disagree" is checkable; "the combined rows are wrong" is not.
- **Re-measure before repeating a figure from an entry.** `ISSUES.md`
  records what was true when it was written.
- **Say which copy of the code and which warehouse you measured**, and
  never report a reader's behavior from a direct call when the answering
  loop adds something (`agent.py` appends the coverage caveat and the name
  readings). Compare against `real_games`, not `games`, for anything
  counted against `team_season_stats` (`games` holds phantom rows).
- **One concept, one definition.** ruff's F811 and mypy's `no-redef` see a
  name defined twice in one module, not in two. `scripts/check_duplicate_names.py`
  reports cross-module constant collisions against an allowlist of the
  ones already filed, so it fails only on a new one; it is name-only on
  purpose (comparing values buries the finding in coincidence) and cannot
  see one concept under *different* names - that still needs somebody
  reading a grep. (history)

## Recording findings

**Anything that needs follow-up goes in `ISSUES.md`, including what you
were not looking for.** Most of this project's data problems were found in
passing; a finding that is not in the file is one the next agent has to
rediscover, or never does.

- **A fault in the source goes in `DATA.md`; what we do about it goes in
  `ISSUES.md`.** `DATA.md` records what ESPN does, as a fact with evidence;
  it is not a task, gets no GitHub issue and nothing closes it. `ISSUES.md`
  records the fix, the workaround or the caveat, and links back to the
  `DATA.md` section. Establish which half you have before writing ("ESPN
  files the 1990 postseason under 1989" is `DATA.md`; "select a postseason
  by the year it was played" is `ISSUES.md`). A fault nobody has to act on
  yet is a `DATA.md` entry alone.
- **What counts:** a data inconsistency, a bug or wrong-answer risk you did
  not fix, a shape refused or misread, a tooling or gate problem, a wrong
  doc. Not ideas, not unrequested features, and not something you fix in
  the same change - fixing it in the work in hand is the first choice; an
  entry is for what you cannot fix there.
- **Record it before you finish, even out of scope.** Measure first where it
  is cheap (a count and a season beat "looks off"). An entry needs a title,
  the evidence (the query or `file:line`, with numbers), what a user would
  see (a wrong answer, a refusal, nothing), a next step, and a priority.
- **Rank by what a user sees, not by how interesting it is** (the
  definitions are at the top of `ISSUES.md`): a fluent wrong answer outranks
  a refusal, and a refusal outranks a gap nobody asks about.
- **Fixing an issue removes it**, in the same commit as the fix (and in
  `CHANGES.md` if the fix touches `src/`; otherwise the commit message is
  the record). A partial fix rewrites the entry to what remains. The file is
  what is still open, not a history.
- **Subagents record findings too, and their prompt says so**, naming both
  files and the `DATA.md`/`ISSUES.md` split. An agent in its own worktree
  appends to its copy of `ISSUES.md` (two branches adding entries conflict
  on adjacent lines: keep both sides); a read-only agent ends its report
  with "Findings for ISSUES.md", which the dispatcher transcribes. Whoever
  merges re-reads the file and re-ranks it.
- **Merging `ISSUES.md` from parallel branches is three-way, never
  keep-both by side**: a keep-both merge resurrects entries a branch deleted
  and keeps both versions of one a branch rewrote. After the merge,
  cross-check every listed entry against its GitHub state. (history)
- **Open a GitHub issue for each entry you added, once your work is done**
  (not while you are still finding things, and only for new entries):
  `scripts/sync_issues.py` opens one per entry with no `- **GitHub:** #N`
  line, titled by the heading, labeled by priority (`P1: wrong answer`,
  `P2: misleading`, `P3: gap`, `P4: low`, plus `bug` or `enhancement`;
  `--area` adds `data`, `query`, `tooling` or `documentation`), and writes
  the number back. Run it with `--dry-run` first. When a fix removes an
  entry, close its issue in the same breath, naming the commit. An entry's
  heading is its issue's title: renaming one means retitling the issue.

**Dispatching agents.** Beyond asking for findings:

- **Give parallel agents disjoint files, each in its own worktree**, and
  have them prefix new private helpers with the function they came from
  (the silent collisions under "Verifying your work").
- **Base the worktree on pushed master, and have the agent check.** The
  tool branches from the main checkout's HEAD, so push and fast-forward it
  first (`git -C ~/code/association merge --ff-only origin/master`), put
  the SHA in the prompt, and make the agent's first command `git log
  --oneline -1`: a mismatch stops the task before work on the wrong tree.
- **Foreground, with a budget, and a lead who looks.** Every command in the
  foreground (an agent that backgrounds a run and waits for a notification
  stops instead), each with the time it should take (the table under
  "Before you commit"); a command past three times its budget is killed
  and reported, never waited on. The lead reads the agent's transcript on a
  cadence (its output file and mtime), messages an agent silent for fifteen
  minutes, and waits on a process, never on a file appearing (`until [ -s
  out ]` spins to the timeout when the job died first).
- **Tell every agent not to run ollama, `association query`, `association
  web` or a live yardstick run** unless it is the only one doing so.
- **Fix what you can, file only what you cannot, discard nothing.** A
  finding the agent can fix in its work is fixed, in its own commit, with a
  test, and reported; a better approach than the one asked is taken when it
  is technically right and within the agent's files, otherwise stated in
  the report with its measured tradeoff. Silence is the one outcome not
  allowed. (history)
- **Parallelism is for disjoint, bounded, measurable work** - a fix with
  its own tests and a proof to check it against, a relation over its own
  tables. The parser-compiler seam is one agent's at a time: two branches
  on it invalidate each other's measurements and collide on helper names.
- **Re-verify a merged agent's load-bearing measurement yourself, and
  re-read `ISSUES.md` for entries the pair invalidated.** Each branch is
  sound alone and the risk is in the combination (an entry true of one
  branch's tree can be false on the merged one, and the reverse). After
  merging, run the gates on the merged tree, re-measure what the reports
  claim, and re-rank the file. A lane's held-ledger and reader-cases files
  are regenerated on the merged tree, never merged by taking a side. (history)
- **A load-time change is not finished when the branch merges**: the
  dispatcher runs `association data load --tables <names>` serially (never
  several agents against one warehouse), then re-measures and records the
  numbers before closing anything.

## Releasing

`docs/releasing.rst` has the procedure. Short version: describe the change
under `## Unreleased`, then `scripts/bump_version.py minor --tag`, push,
then `scripts/release.sh X.Y.Z`. Nothing before the final step is
irreversible.

- **The distribution is `association-py`; the import package, the
  `association` command and the repository keep the bare name** (PyPI
  refused `association`, 2026-10-09; history). `pyproject.toml`'s `name`,
  `association.__version__`'s metadata lookup, `docs/conf.py` and the web
  extra's install hint read the new name; the wheel is
  `association_py-X.Y.Z-py3-none-any.whl`. The install lines in `README.md`,
  `docs/installation.rst` and `docs/usage.rst` are PyPI's and pin no tag;
  `bump_version.py` still rewrites any tag pin a doc grows. Tags before
  5.0.0 carry the old name, cannot be uploaded and stay on GitHub only.
- **Publishing is Trusted Publishing (OIDC), nothing stored in the
  repository**: the publisher on PyPI is project `association-py`, owner
  `jeffknupp`, repository `association`, workflow `publish.yml`,
  environment `pypi` - the environment name included, since the job runs
  under it and a publisher without it does not match.
- **A changelog section can be larger than an argument or an environment
  variable may hold** (128 KiB), so `release.sh` passes the notes through
  stdin, and the release page carries the section's leading entries with a
  pointer to the rest.
- **Before bumping, check the version directives**: anything added or
  reshaped on the public surface carries a `.. versionadded::` /
  `.. versionchanged::` for the version about to go out. `git diff
  v<previous>..HEAD` over `src/` is the honest way to find them; the API
  pages are generated, so an unmarked change appears with no history rather
  than failing anything.
