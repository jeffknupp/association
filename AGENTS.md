# Working on `association`

Orientation for agents (and people) making changes here. It covers what is
*not* obvious from reading the code: the gates, the conventions that are
enforced, and the specific shapes of bug this project keeps producing.

For how the system is designed — the three stages, the router/agent split, why
templates instead of better prompting — read `docs/architecture.rst`. That is
the source of truth for design, and this file does not restate it.

Where the work is going - the goal, what each spike bought and why, and the
next steps - is `ROADMAP.md`. Read it before starting a spike, and keep a
spike pointed at it: measurements turn up fixable things, and those go to
agents or to `ISSUES.md`, not into the spike.

What is known to be wrong, missing or unverified is in `ISSUES.md`, ranked by
priority. Read the entries for the area you are about to touch, and add what
you find to it, including findings that are not part of your task (see
"Recording findings").

What the *source* does — ESPN's wrong values, missing games, odd labeling and
per-table history — is catalogued separately in `DATA.md`. `ISSUES.md` is what
we do about it. Read `DATA.md` before trusting a column.

## Before you commit

```bash
uv run pre-commit run --all-files   # all sixteen gates
uv run pytest -q -n auto            # fully offline: no network, no ollama
```

**While iterating, run `scripts/check_fast.sh` instead** - every gate except
the Sphinx build, plus every test except the one marked `slow`, in parallel.
Measured on 8 cores with three agents competing for them:

| command | time |
| --- | --- |
| `scripts/check_fast.sh` | 39s |
| the hooks it runs (all but Sphinx) | 7s |
| `uv run pre-commit run --all-files` | 26s (19s of it Sphinx) |
| `uv run pytest -q` | 156s |
| `uv run pytest -q -n auto` | 80s |
| `uv run pytest -q -n auto -m "not slow"` | 30s |

So the full check above costs about 106s and the iteration check about 39s.
The fast one leaves out exactly two things, and its own header says so: the
docs build, which is the gate that catches a malformed docstring, and the
`slow` marker, which today is one test (the Eastern-date agreement check, 57s
of the suite's 80s). A commit touching a docstring or that rule runs the full
check. Nothing else may be skipped, and `slow` is not a way to make a failing
test quiet - every marked test still runs in CI and in the full local run.

**A fresh worktree needs syncing before either command works at all.**
`uv run` creates the venv on first use but does not install the `dev`, `docs`
or `web` extras, so `uv run pytest -q` fails with `Failed to spawn: pytest`
and the gates never run. Run CI's own line first:
`uv sync --frozen --extra dev --extra docs --extra web`.

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
  The way past it is the one the templates and `route()` took: split the body
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
  - `check/` - the coverage report. `query/` - router (`router.py`, with what
    the model sees in `router_prompt.py`), templates, entities, renderers, the
    agent. `web/` - the local web interface.
- **The package layers are a contract.** `cli` > `web` > `query | check` >
  `fetch` > `nba`, with `fetch` and `query` independent and the core free of
  the `web` extra's packages (`[tool.importlinter]`). A new module that needs
  to sit somewhere else changes the contract, with a reason, rather than an
  ignore.
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
span, since, without, split, game_n, season_n, below/above, and order+limit as
a window cut after every other filter - applied in the shared steps
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
  downstream can see it. The model choosing between ten Browns is still a
  guess nothing can see, so the completion is still undone.
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

- **A template's `TemplateUnsupported` gets one more deterministic try before
  the agent does.** `query/compose` sits between the two: when `check_scope`
  or the template itself raises, `agent.py`'s `_try_compose` offers
  `compose.answer(ctx, intent, slots, question)` the same point on the
  relation the template could not narrow to. A `TemplateResult` back is
  answered exactly like a template's own - `answered_by="fast"`, the intent
  kept, the same name-reading and coverage-caveat attachment - including when
  that result is itself a refusal (a clarification, a "no match"): looking at
  the question and having something to say about it is an answer, not a
  fall-through. `None` falls through to the agent exactly as before this step
  existed. Nothing in the package may reach ollama - it is a compiler, not a
  smaller agent - and it narrows the relation only through the shared steps in
  `templates/common.py`, the same discipline the six relation templates keep
  (see "A template on a relation does not declare, or apply, scoping of its
  own" above).
- **A team can be the subject, not only a narrowing.** `compose/team.py`
  (`TeamQuery`/`TeamResult`/`run_team`, `move.team_move_point`,
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
  box-score join yet. `move_point` tries `team_move_point` on the UNREPAIRED
  slots, before the player-subject `repair()` step: "magic" is also Magic
  Johnson's given name, and `repair()`'s dropped-subject restoration would
  otherwise invent him from a team reference the way `override_invented_players`
  exists to catch for the router - here it is the repair itself doing the
  inventing. `team_named_in` is the same restoration `players_named_in`
  already makes for a dropped player, over team names instead.
- **What the router model sees lives in `query/router_prompt.py`, alone.**
  `ROUTER_PROMPT`, `ROUTER_SCHEMA` and the window they share are there; the
  post-processing of the slots the model returns is in `query/router.py`. So a
  diff that touches only `router.py` cannot move a slot on some unrelated
  question, and a diff to `router_prompt.py` always can.
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
  question. Five tools leave as little as ~100 tokens of headroom in the worst
  case (measured: a question that pulls the maximum three selected
  knowledge-base entries); a sixth does not fit.
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
- **A new intent does not need a prompt edit if the question's own words name
  it.** `CODE_ASSIGNED_INTENTS` is the route for that: `route()` assigns
  `period_split` and `coach` from the text, they are absent from
  `ROUTER_SCHEMA`'s enum and `ROUTER_PROMPT`, and so adding them could not move
  a slot on any other question - proved by hashing both constants before and
  after and by `check_routing.py` coming back with the existing cases
  unchanged. Reach for it before touching the prompt, especially for a
  refusal: a question nothing can answer needs the model's help least. `coach`
  is the worked example - the word is unmistakable, nothing else in the
  warehouse is named it, and a bare surname is deliberately not matched
  ("nurse" and "rivers" are ordinary words, the substring trap
  `players_named_in` exists for). Such a template declares no tables, so it
  goes in `TABLELESS_INTENTS` or the coverage gate fails.
- **Refusing beats falling through wherever the agent has nothing to read.**
  That is `check_coverage`'s reasoning, and it applies past the floors: a coach
  question reached an agent that queried tables with no coach column and was
  then free to fill the silence from its own weights. Before writing the
  refusal, check what the source actually serves - "ESPN does not publish
  coaches" was the obvious sentence and it is false, and a refusal naming the
  wrong cause reads as honest while sending the reader somewhere useless.
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

- **A refactor is proven by a golden comparison, not by the suite alone.** The
  complexity refactor that split `route()`, `parse_game_summary` and the
  templates into steps was checked by calling each function with many inputs
  - the routing corpus's slots, the tests' own cases, one per branch - against
  the original code and again after, and diffing the full results (answer text,
  data, exceptions, written files, types as well as values). Import each copy
  explicitly (`PYTHONPATH=<tree>/src PYTHONDONTWRITEBYTECODE=1`, print
  `association.__file__`), and perturb one token of the refactored code to
  prove the comparison can fail. A green suite says only that the tested
  inputs still pass.
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
- **Never rewrite `CLAUDE.md` in place.** It is a symlink to `AGENTS.md`, and
  `git ls-files` lists it, so a `sed -i` or `perl -pi` over a file list
  replaces the link with a regular copy (`git status` shows `T CLAUDE.md`). The
  two then drift silently. Exclude it from bulk edits and edit `AGENTS.md`.
- **`/tmp` is a shared 7.9G tmpfs.** Four agents' golden-comparison outputs,
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
  report a template's behavior from a direct call when the agent path adds
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
  and not something you fix in the same change.
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
session. Tell every one not to run ollama or `scripts/check_routing.py`
unless it is the only one doing so, for the reason in that script's docstring.

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
