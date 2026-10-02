# Roadmap

**Status: ACCEPTED 2026-09-30, after two independent reviews; Phase 0 in
progress** (see "Phase 0, as it stands"). The reviews corrected the
measurements below and changed the phases. Jeff's decisions are recorded
under "Decisions"; one is still open (the season type a question that names
none reads) and does not block Phase 0.

The previous roadmap is `ROADMAP-2026-09.md` (archived verbatim; code and
commits that cite "plan item N" or "ROADMAP step N" mean that file).
`ROADMAP-HISTORY.md` is the log before it. `ISSUES.md` is what is wrong now.

## The goal

Unchanged: **a correct answer to every reasonable question of at least mild
complexity, or an honest refusal that names what is missing - fast, from the
warehouse, never from a model's weights.** Refuse rather than approximate. A
stated, correctable default beats a question.

Added, and the subject of this roadmap: **a pipeline a person or an agent
can trace in one sitting.** The question's text is read once, into one typed
record. That record is read once, by one planner. One place builds SQL. One
place writes words.

## Where it stands (measured 2026-09-30, `b818823`)

**The score is good.** Live parser22: 167 of 175 questions (95.4%), 156 of
166 families (94.0%); 1 wrong, 4 partial, 3 refused for want of a reading.
Eleven of the twelve open P1s in `ISSUES.md` are reader faults; none is a
wrong number from the answer side.

**The architecture is half-ported.** Both reviewers replayed parser22's 277
questions through the real agent with every presenter, compile and SQL
execution instrumented (the replay matched the live run on all 277).

Of the 205 answers by a "compiled" intent:

| What produced the answer | Answers |
| --- | --- |
| The compiler's SQL, the compiler's sentence | 16 |
| The compiler's SQL, a retained template helper's words (`threshold_count` 15, `single_game_high` 8, `player_matchup` 5, team streak 1) | 29 |
| SQL compiled, then discarded; the template body reads and words (`game_log` 18, `record_when` 11, `period_split` 9, `player_splits` 9, narrowed `player_stat` 8) | 55 |
| Nothing compiled; the template body reads and words (`leaderboard` 29, `player_history` 19, season-line `player_stat` 19, `player_compare` 14, team log 7, `with_without` 6) | 94 |
| Refused or clarified before any read | 11 |

So the compiler reads 45 of 205 and words 16. Eighty-one answers, 30% of
everything answered, read the **season line**, a relation the compiler has
no model of. Of the 67 answers by a live template, 39 are charts.

The rest of the picture:

| What | Measured |
| --- | --- |
| `query/` | 33,556 lines; grew 4,748 in the five days since v4.4.0 while nine templates "retired" |
| `templates/` | 12,231 lines, 401 more than at v4.4.0 |
| private names `compose/` imports from `templates/` | 91 |
| the subject is read | 3 times per question |
| the router's stages run | 1 to 4 times per question (mean 1.7), speculatively, inside the subject reading |
| the planner runs | twice per compiled answer, once of them inside the parser |
| functions that take the question string | 168, in 10 modules; `Subject.question` carries the text on a record |
| SQL statements per question to recognize names | about 100 (97% of all SQL issued) |
| occurrences of `intent` | 567, of which 361 are on the READ side, where it is context the stages branch on |
| sets keyed by intent, across the package and the web page | about 25 |
| declarations of what a reader honors | 6, plus about 170 hand-written refusals in 11 modules |
| distinct `data` shapes an answer carries | 25, with 22 per-intent renderers on the web page |
| tests bound to structures this roadmap deletes | about 1,170 of 2,386 |
| lines the 628 recorded questions reach | 61.6% (router 65%, `templates/splits` 44%, `team_games` 31%) |

**What the knot is** (the reviews' diagnosis, which replaces mine):

1. **Two vocabularies for one question.** The slot bag (`Scope`, 39 fields,
   born as the router model's JSON schema) and the compiler's algebra are
   both carried on the `Reading` and translated both ways: `adapt`, `move`,
   `present` and the scoping tables are translation code. A presenter
   pattern-matches the planned query back to the intent's default point and
   calls the template on the slots - the algebra is a detour, not the
   representation the answer is computed from.
2. **The reader depends on the answer side.** `subject.py` asks whether the
   downstream template honors a cell before writing it; `parse.py` runs the
   planner. What is read depends on what will answer, so "read" is not a
   stage.
3. **`intent` is context inside the reader,** not a label on the answer:
   the stages branch on it and are run speculatively to choose it.
4. **There is no common result.** Twenty-five data shapes, each read and
   worded by its own code.

**What is good and must survive:** the shared narrowing steps and
`Narrowed`; the allow-list scoping tests; coverage floors (missing versus
unrepresentative, the ranking floor, phantom 1993); the name-safety rules
(whole-word matching, the misread refusal, single-near-spelling defaults);
every caveat that makes a default visible (empty box scores, rebuilt
lines, name readings, the stated season type); `metrics.py`'s measure
catalog; the rehearsal and hold-out discipline.

## The target

```
question, names, stat
   |  READ   the only code that sees the question's text; no warehouse SQL
   v
Reading      subject, measure, shape, typed filters, window, span - names as
             typed, and the words nothing claimed
   |  PLAN   resolves names, checks cells against the relation's table,
   v         picks the relation; or refuses with a cause
Query        resolved entities, one relation, one shape, typed cells
   |  RUN    the only code that builds or executes SQL
   v
Result       rows, games counted, cells applied, typed notes and defaults,
             or a refusal cause
   |  SAY    takes a Result and no connection; one sayer per shape
   v
Answer
```

Contracts, each enforced mechanically:

1. **The text stays in the reader.** No record reachable from Reading,
   Query or Result holds the question; only the read stage imports the
   normalizer. Enforced by import-linter layers inside `query/`
   (`say > relations > plan > read > lexicon`), already a gate here.
2. **Each word is read once.** Every reader rule claims the span it
   consumed; overlapping claims fail; content words nothing claimed are
   recorded on the Reading. The subject, the stages and the planner each
   run once per question.
3. **A sayer cannot read the warehouse.** `say/` does not import duckdb;
   SQL executes only in `relations/`. Names are recognized from an
   in-memory index, not by query.
4. **One table of cells per relation:** how each narrowing is applied, how
   it is said, or why it is refused. It is the planner's allow list. A
   behavioral test per (relation, cell) shows that applying the cell
   changes the result. No second declaration, no refusal on trust.
5. **Everything the result carries is said.** A check over the whole
   corpus that every applied cell, note and default in a Result was
   rendered. This is what stops a reworded answer from silently losing a
   caveat.
6. **One representation, one vocabulary.** A closed measure type; one
   condition type for "a line on a stat" (five carriers today); regexes
   only in the lexicon; no slot-era field beside the algebra.

## The phases, in order

**Proof, for every phase.** Per question, each stage's output is written
as a snapshot (reading, query, a canonical projection of the result,
answer) and compared across trees, stage by stage. The projection holds
entities, measure, values and rows, the games counted, the span, the cells
applied, typed notes and the refusal cause - not text. Compared at a stated
float tolerance, tie-insensitive, single-threaded, with the date pinned and
artifact paths masked, after running the baseline twice to learn the noise.
Two populations, both required: the 628 recorded questions (which already
include the 75 hold-out), and every call the unit tests make - the second
found five divergent shapes where the first found one. A live run closes
each phase: an answer whose projection did not move inherits its grade, a
moved one is graded against the key, and each new sayer is reviewed once.

**Phase 0 - Make it measurable.** No behavior change.
- Pin the inputs: `today` is injectable (`ASSOCIATION_TODAY`, done
  2026-09-30 with the rollover fix, and pinned for the test suite); pin
  it in every research harness.
- The per-stage snapshots and the projection comparison, watched to fail.
  The research harnesses move onto them, off route replay and the trace
  line, before anything they depend on is deleted.
- The claims ledger as instrumentation: the count of unclaimed content
  words over the corpus becomes a baseline. Measured from outside the
  reader, so the number means the same after the reader is replaced: a
  content word is unread when deleting it leaves the reading and the
  planned query unchanged (`scripts/claims_ledger.py`).
- The target types written down and reviewed: the Reading's typed filters,
  the Result, the measure type, and the closed set of shapes. Shapes are
  named here, before any sayer is written, so "shape" does not become
  intent renamed.
- The import-linter layers and the ratchets, with today's violations
  allowlisted.
- The trace line and the stale docstrings corrected.

**Phase 0, closed 2026-10-01.**

| Item | State |
| --- | --- |
| Inputs pinned | done: the snapshots, the ledger, the suite and twelve research harnesses pin `ASSOCIATION_TODAY` |
| Stage snapshots, both populations | done: `scripts/stage_snapshots.py`, `tests/stage_calls.py`. Two runs of one tree are identical (628 of 628 questions, text included; 1,363 of 1,363 unit-test calls); a perturbed token fails each at the stage it was made in |
| Research harnesses off the trace line | done for the rehearsal, the hold-out and the scorer (it reads the run it scores and refuses on an ungraded moved answer); the golden harness and the preview script still replay routes (#288, before Phase 3) |
| Unread content words | done: `scripts/claims_ledger.py`. Baseline at `011091f`: 679 of 2,415 content words (28.1%) in 390 of the 628 questions do not move the reading when deleted - mostly words that restate the shape ("average", "record", "stats", "game"). Nine questions have an unread NUMBER (eleven before two stat spellings were taught to the instrument); three of those are wrong answers, filed. Unchanged after Phase 1's first two steps |
| Import contracts and ratchets | done: three contracts (the reader's nine imports of the answer side listed), `scripts/check_ratchets.py` (50 functions outside the reader take the question, 21 modules execute SQL, 91 private template imports, 9 modules outside the reader import `re`), the 25 intents frozen by a test |
| Trace line, stale docstrings | done (#284) |
| Target types | `ROADMAP-TYPES.md`, draft 2, partly decided 2026-10-01 (three grouped shapes; decisions apart from notes; no `pair`). It lists what must be decided before each phase; Phase 1 needs nothing from it |
| Every remark given a kind where it is written | done: `query/notes.py`, 28 closed kinds with closed fact names, 98 writers; 180 of the 628 recorded answers carry 234 typed remarks, every stage identical to before; `stage_snapshots.py remarks` names two answers that drop a remark they wrote (filed) |
| Five numbers that are only in an answer's text (#309) | open: each moves into `data` before the Phase 2 slice that rewords its answer |

**Phase 1 - The read stage becomes a stage.** Small, and before the answer
side, because it removes the coupling everything else trips on.
- The subject is read once (the three passes already agree on 269 of 277
  questions; the eight differ for three known reasons).
- The stages run once; no speculative run to choose a child.
- The planner runs once, outside the parser. The reader always writes the
  condition it read; the planner refuses it.
- The reader's imports from the answer side are cut.
- Names are recognized from an in-memory index of the 3,101 players and 30
  teams.
Proved by identical Reading and Query snapshots.

**Phase 1, as it stands (2026-10-01).**

| Item | State |
| --- | --- |
| The planner runs once, outside the parser | done: `compose.plan.plan_point`, run by `Agent.ask` (`Agent.planned`) and handed to `compose.answer`; `parse.with_point` only reads. 628 of 628 questions identical in every stage; 8 of 1,378 unit-test calls differ, each the Reading now holding a point the planner later declines |
| The subject is read once | done: `parse.read_route` reads it, `Route.subject` carries it, `subject.settle_subject` settles it under the stages' intent with no name read again. Before, every question read it three times and 18 of 628 disagreed between passes. After: no answer, query, scope or intent moves on either population (628 questions; 1,383 unit-test calls). The subject RECORD differs on 5 questions (a companion named as typed, "kd", where the third pass had re-read the resolved name; a model's filler name now recorded; a bogus player "first quarter" no longer in it), and 185 decisions say what the one reading rested on. The corpus run takes about 220s where it took about 285s |
| A companion's name has one reader | done 2026-10-01: the stages' readers of "with X" and "without X" are gone; `subject._conditions` reads every name by its position in the phrase and the stages take them as `router.Beside`. Four recorded answers moved, each to its graded sibling wording's answer: two that named fewer teammates than the question (#310) and two refusals. 14 more differ only in a name written as the known player rather than as typed; 610 of 628 identical. Of 1,386 unit-test calls, 26 differ, all in those names. 25 wordings outside the corpus, with and without the model's names, keep the same count of names and the same intent as before |
| The stages run once | open |
| The reader always writes the condition it read | open |
| The reader's imports of the answer side | 8 of 9 left (`[tool.importlinter]`) |
| Names from an in-memory index | done 2026-10-01: `query/names.py` (an Opus agent, then the three team lookups it was told to leave). Every `players`/`teams` lookup is answered from two indexes loaded once per question inside `Agent.ask` (`names.loaded`). Reader statements over the 277 yardstick questions: 10,658 before, 554 after - the two loads. A differential check of 478,304 calls against the old code on the real warehouse found no difference except the order of two players who share one `display_name` (21 such names; the first candidate never differs). 628 of 628 recorded questions and 1,386 of 1,386 unit-test calls identical |

**Phase 2 - The answer side, in slices.** Each slice ends with its
presenter, template body, scoping rows, adapter branch and web renderer
deleted in the same change. Method: split each body into a reader that
returns a Result and a sayer that takes one - numbers identical by
construction - then merge readers where measured equal.
- (i) **Compiled and discarded** (55 answers): execute the compiled SQL.
  `game_log`, narrowed `player_stat`, `player_splits`, `record_when`,
  `period_split`.
- (ii) **Compiler-read, template-worded** (29): the wording moves into
  sayers and the notes into the Result. `threshold_count`,
  `single_game_high`, `player_matchup`, streaks.
- (iii) **The season-line relation** (81): `leaderboard`, `player_history`,
  `player_compare`, unnarrowed `player_stat`. The existing readers and the
  measure catalog are moved, not re-derived: their floors, traded-player
  dedup and qualifiers are what stopped "Moses Malone led 1980". The
  riskiest slice of the answer side.
- (iv) **Team shapes** (about 40): the team log, with/without, the team
  templates, the period ranking. Standings and projections are a
  team-season relation.
- (v) **Charts** (39): see the open decision.
Exit: `templates/`, `present.py`, `adapt.py`, `check_scope` and every
scoping table but the per-relation cell tables are gone.

**Phase 3 - One reader.** A typed reader with claimed spans is built
beside the old one and run in shadow: its Reading is projected to the old
shape and diffed on the whole corpus and on every reader test. The router's
stages become taggers. Reader tests are re-seated as question-to-Reading
snapshots before `router.py` is deleted. Nothing is deleted "by coverage":
the corpus reaches 65% of the router's lines, and the rest is mostly
guards for recorded failure shapes. Unclaimed words go to the trace, are
measured, and only then considered as a refusal trigger. Exit: `router.py`,
`Route`, `intent` in the reader, and the reader's regexes outside the
lexicon are gone.

**Phase 4 - Close out.** `Answer.intent` removed and the web page's
renderers keyed by shape (22 become one per shape); the browser check run;
`docs/architecture.rst` rewritten. `AGENTS.md` is updated with each slice,
not here: agents work from it throughout.

## How a note survives a rewording

A note today is a sentence: a string appended to the answer and to
`data["notes"]`. Under D1 a sentence cannot be compared, so "numbers
identical" would pass while a caveat vanished - the silent narrower answer,
reintroduced by the proof itself. So a note stops being a sentence:

- **A note is data on the Result:** a kind and its facts.
  `empty_box_scores(games=5)`, `rebuilt_lines(games=3)`,
  `name_reading(typed="maxey", read="Tyrese Maxey", also=["Marlon Maxey"])`,
  `season_type_default(read="regular season")`,
  `coverage_partial(table="plays", season=2002)`,
  `redirected_season(asked=2026, answered=2025)`. Phase 0 gives every note
  the code emits today a kind, at the place it is emitted.
- **"Identical" means the same kinds with the same facts,** compared in
  the result's projection, before and after each change. The sentence is
  not compared.
- **Each kind has one phrase,** in the sayer. Rewording a note is editing
  that one phrase.
- **Contract 5 closes the loop:** over the whole corpus, every note on a
  Result must have been rendered into the answer. A sayer that drops one
  fails the check even though every number matches.

The same holds for the narrowings applied (each cell has one phrase) and
for refusals (a cause, then a phrase).

## What "done" means

| Measure | Today | Done |
| --- | --- | --- |
| `TEMPLATES` | 12 entries | 0, or only declared chart shapes |
| `present.py`, `adapt.py`, `check_scope`, `HONORED_SCOPING`, `STATED_SCOPING` | present | deleted |
| private imports from `templates/` into `compose/` | 91 | 0 |
| compiled answers whose read is the compiler's SQL | 45 of 205 | every answer |
| subject readings per question | 3 (1 since 2026-10-01) | 1 |
| stage runs per question | mean 1.7 | 1 |
| planner runs per question | 2 (1 since 2026-10-01) | 1 |
| functions outside the reader that take the question | 168 | 0 |
| modules that execute SQL | 20 (19 listed by the ratchet since 2026-10-01: `parse` and `subject` no longer do) | `relations/` only |
| SQL statements the reader issues per question | about 100 (38.5 once the subject was read once; 2 since 2026-10-01, the index's two loads) | 0 |
| records between question and answer | 8 | 4 |
| result shapes, and web renderers | 25 and 22 | one per shape |
| unread content words over the corpus (`scripts/claims_ledger.py`) | 684 of 2,415 (679 before the companions' one reader, which no longer needs "play" to see who played); 9 questions with an unread number | not grown; no unread number |
| stage snapshots, questions and unit-test calls | baseline | identical at the stated tolerance |
| tests bound to deleted structures | about 1,170 | 0: deleted, or re-seated at a stage |
| score | 167/175, 156/166 | not lower |

Line counts are reported against 33,556 at each phase, not gated.

## Decisions (Jeff, 2026-09-30)

- **D1. Wording may change - yes.** Numbers and games proven identical;
  one sayer per shape, reviewed once.
- **D2. Answer side before the reader - yes.** Kept, with the small
  read-stage decoupling (Phase 1) placed before it on both reviewers'
  advice.
- **D3. `intent` goes entirely - kept after review.** Neither the trace
  nor the web API needs it; the page renders by shape. One reviewer would
  have kept a family name as a row of data for a readable trace; the small
  closed set of shapes named in Phase 0 gives the trace its label without
  a second concept.
- **D4. New shapes are frozen - yes.** P1 wrong answers are still fixed.
- **Charts are declared shapes.** The four chart answers (39 on
  parser22) keep their own readers and renderers, each declared as a
  shape with its relation. Porting them onto shots and NetPoints relations
  is revisited after Phase 3.
- **Notes stay identical; their wording does not have to.** See "How a
  note survives a rewording" below.
- **An unclaimed word is measured first.** Trace and a corpus count in
  Phase 0; a visible note or a refusal is decided from the numbers.
- **A new shape may be built once its family's slice has landed,** through
  the new stages only.
- **Tests are re-seated at a stage boundary, and retired wherever
  possible.** About 1,170 tests are bound to structures this roadmap
  deletes. A test whose subject is a deleted structure (a presenter, a
  scoping table, a template's exact sentence, the stages' intermediate
  slots) is deleted with it, not ported. A test of behavior a user can see
  moves to the stage that owns the behavior, and only if the stage
  snapshots do not already hold the case. Each slice reports the tests it
  deleted and the tests it moved.
- **The default season is the latest one with games on record.** From
  2026-10-01 the calendar's season (2027) has no games; fixed the day
  before (`nba.season.season_on_record`, entered by `Agent.ask`). The
  answer names the season, as before.

## Decision still open (Jeff)

**The season type a question that names none reads.** Today: the regular
season, said in the answer, except a "last N games" window, which reads
both. The rule left on the table by the last roadmap: **records, lists and
counts read both season types; averages read the regular season.**
Evidence since: the keys of two parser22 rows combine both types where
the question named none - F161, a list (the key's twelfth game is a 2021
playoff game), and F062, an AVERAGE (the key's 31 games are 28
regular-season and 3 playoff), which the rule on the table would still
answer from the regular season alone; and "show maxey's games against
boston in the past two seasons" lists 7 regular-season games and leaves
out 7 playoff meetings, saying so. Both are graded correct today because
the default is stated.

## What still fails (parser22)

Not worked on until its slice lands, unless it is a wrong answer: a team
ranking that cannot take a date or a career (F104, F125); a career count
whose heading names one season (F096); two active players named Curry
(F002, F003); "without kd" meaning every game Durant was not a teammate
(F114); "rebounds allowed per team" (F101); a record counted 11 where the
key counts 10 (F110); the top 50 with each player's team (a wording of
F017). "Tatum rec" and F097 are refused by rule.

## Rules for the work

- One structural change at a time, by the lead. Agents get bounded,
  disjoint pieces with their own stage comparison.
- Every step deletes the path it replaces in the same change. No
  dispatcher between an old and a new implementation outlives its slice.
- Measure first; every live run is scored against the key, not only
  diffed.
- A finding goes in `ISSUES.md` when it is found.
- One ollama caller at a time. Gates chained with `&&`.

## Not planned

A bigger model, any fall-through agent, more data. One unified relation
(considered by both reviewers and rejected: the per-relation rules -
rebuilt box scores, the phantom season, period lines, ranking floors - are
where correctness lives). A full grammar engine in place of the reader's
tuned rules (rejected as a rewrite; the claimed-span ledger is adopted).
