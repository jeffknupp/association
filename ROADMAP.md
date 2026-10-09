# Roadmap

**Status: ACCEPTED 2026-09-30, after two independent reviews; Phase 0
closed 2026-10-01, Phase 1 closed 2026-10-03, Phase 2 closed 2026-10-05**
(each by a live run - `live_parser24` and `live_parser28`: 167/175,
families 156/166, every answer identical to the run before - and an Opus
review of the phase whose findings are fixed or filed; `ROADMAP-HISTORY.md`,
the working log, entries 12 and 20-21; Phase 2's cleanup landed
2026-10-06). Phase 3 opened 2026-10-09; steps 0 and 1 and step 2's first two
slices, the span and the window, are merged; next: step 2's games' cuts ("Phase 3, the expected steps"). The reviews corrected the
measurements below and changed the phases. Jeff's decisions are recorded
under "Decisions"; one is still open (the season type a question that names
none reads) and does not block Phase 1.

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

**Phase 1, as it stands (2026-10-03, closed).** What it bought, as the
review of 2026-10-03 re-measured it: the planner, the subject reading and
the stages each run once in production (the stages twice on 4 of 628 and 59
of 2,082 out-of-corpus wordings, a child the stages declined); names come
from the index; the point reader is a reader module, and the reader reaches
the answer side through one listed edge, `point -> compose.adapt`, checked
on chains; the point's refusals are causes. What it did not buy, and where
it stands: the adapters still decide the intent's default point on the
answer side (Phase 2 deletes them); "refusals as causes" is true of the
point reader's three, not of the ~170 refusal sentences elsewhere (Phase
2's sayers); contracts 2, 4 and 5 are Phase 3's and 2's and are not started.

| Item | State |
| --- | --- |
| The planner runs once, outside the parser | done: `compose.plan.plan_point`, run by `Agent.ask` (`Agent.planned`) and handed to `compose.answer`; `parse.with_point` only reads. 628 of 628 questions identical in every stage; 8 of 1,378 unit-test calls differ, each the Reading now holding a point the planner later declines |
| The subject is read once | done: `parse.read_route` reads it, `Route.subject` carries it, `subject.settle_subject` settles it under the stages' intent with no name read again. Before, every question read it three times and 18 of 628 disagreed between passes. After: no answer, query, scope or intent moves on either population (628 questions; 1,383 unit-test calls). The subject RECORD differs on 5 questions (a companion named as typed, "kd", where the third pass had re-read the resolved name; a model's filler name now recorded; a bogus player "first quarter" no longer in it), and 185 decisions say what the one reading rested on. The corpus run takes about 220s where it took about 285s |
| A companion's name has one reader | done 2026-10-01: the stages' readers of "with X" and "without X" are gone; `subject._conditions` reads every name by its position in the phrase and the stages take them as `router.Beside`. Four recorded answers moved, each to its graded sibling wording's answer: two that named fewer teammates than the question (#310) and two refusals. 14 more differ only in a name written as the known player rather than as typed; 610 of 628 identical. Of 1,386 unit-test calls, 26 differ, all in those names. 25 wordings outside the corpus, with and without the model's names, keep the same count of names and the same intent as before |
| The stages run once | done 2026-10-02: `settle` runs once per question on 622 of 628 and twice on 4 (a child the stages declined; 59 of the 2,082 out-of-corpus wordings, 2.8%; 2 of the 628 are refused before any stage runs), where it ran 1 to 4 times (mean 1.68); `read_subject` decides nothing, `settle_subject` runs no stage, the Route carries the settled subject. Scope, point, query, result and answer identical on 628 of 628; 172 readings differ only in their decision records and in `subject.intent_reason`, which the Reading now carries. Out of the corpus, 2,710 wordings (the StatMuse feed with no names among them) read the same but three the old child run missed a stat on ("kyrie career 5 3s games on road" answers now) |
| The reader always writes the condition it read | done 2026-10-02: `_apply_conditions_honored` and the parser's two gates on it are gone; a condition on an intent that cannot honor it is refused by name ("how many times did the 76ers beat boston when embiid started" answered 2-2). 628 of 628 and every unit-test outcome identical; `subject -> compose` is no longer imported |
| The reader's imports of the answer side | 1 of 9 left (`[tool.importlinter]`): `point -> compose.adapt`, the intent's default point from the ten adapters, which goes with Phase 2's last slice. The two `parse -> compose` lines went 2026-10-03 with the point reader's move to `query/point.py`; the other five 2026-10-02: the team word's reader to `entities`, five intent sets to `reading`, the team metrics' words and the period columns to `measures` |
| Names from an in-memory index | done 2026-10-01: `query/names.py` (an Opus agent, then the three team lookups it was told to leave). Every `players`/`teams` lookup is answered from two indexes loaded once per question inside `Agent.ask` (`names.loaded`). Reader statements over the 277 yardstick questions: 10,658 before, 554 after - the two loads. A differential check of 478,304 calls against the old code on the real warehouse found no difference except the order of two players who share one `display_name` (21 such names; the first candidate never differs). 628 of 628 recorded questions and 1,386 of 1,386 unit-test calls identical |

**Phase 1, the order from here (Jeff, 2026-10-02, after a review of
Phases 0 and 1 on the new devbox;** `ROADMAP-HISTORY.md`, the working log,
entry 9). The review re-measured every claim above and found them true,
one regression the two populations could not see (a team read without the
question's season, fixed the same day, #316) and three places a step had
kept the path it replaced. Those come before the open items, because the
next step builds on the same reading order:

| Step | What | State |
| --- | --- | --- |
| 1 | The replay branch goes: `reading_from_route` no longer reads a subject of its own for a route that carries none, `Agent.ask` takes no `route`, and the preview script answers recorded questions with their recorded normalizer replies (#288). A test hands the agent a route at the reader's boundary | done 2026-10-02: 628 of 628 recorded questions identical; of 1,388 unit-test calls every outcome identical once five one-letter questions were lengthened. 2 tests deleted with the door, 39 call sites moved to `tests/routed.py` |
| 2 | The other two kept paths go: `compose.answer` planning again when `planned` is left out, and a name lookup outside `names.loaded` reading its table again | done 2026-10-02: `planned` is required by `compose.answer` and the snapshot; a lookup outside a block raises `names.NotLoaded` (901 tests had made one; every test now runs inside a block). 628 of 628 questions and all 1,389 unit-test outcomes identical |
| 3 | The snapshot's `query` stage records the planned `Query`, not the Reading's point; the snapshot's first line says the ollama version | done 2026-10-02: `query` is the planned `Query`/`TeamQuery`, the point is `reading.point`; the meta line carries `python`, `duckdb` and `ollama` (version, model, digest) and `compare` warns on each. Every other stage identical on 628 of 628; old `query` equals new `reading.point` on all 452 with a point |
| 4 | The ratchets tighten: SQL counted per module by statement, not listed by module; the D4 freeze holds presenters, scoping tables and web renderers as well as intents | done 2026-10-02: 103 statements in 19 modules, each count may only fall; `test_frozen_shapes` holds the templates, presenters, adapters, the 12 scoping declarations and the 22 renderers, each watched to fail |
| 5 | Documents that drifted: `AGENTS.md`'s "nine imports", the done table's 20 modules against Phase 0's 21, the records row (Phase 1 added `Planned`, `Beside` and `Route.subject`) | done 2026-10-02: the three corrected in place, and the stage-runs row carries its re-measured figure |
| 6 | Phase 1 proper: the stages run once, with the subject settled once; the reader always writes the condition it read; the reader's eight imports of the answer side, four of them `parse -> compose` for `read_point` | the stages run once (`bdfcbff`), the condition always written (`0bd4754`), five imports moved to the reader's side: done 2026-10-02. `read_point`: the five-step move below, done 2026-10-03 |

A reader change is also proven on wordings OUTSIDE the corpus, old tree
against new, before it lands: the companions step did this and caught
what the corpus could not; the subject step did not, and regressed.

**The `read_point` move, in order (Jeff, 2026-10-02).** `compose/move.py`
is the point reader: 1,101 lines, 40 functions, 26 of them taking the
question, reader code in the answer package, reached from the parser
through the last two imports the contract lists. It reads the point's
shape, measures, aggregate, predicates and window into a Reading nested
in the Reading (`Reading.point` - the nesting stays until Phase 3, where
the Reading becomes the point), and it consults the answer side three
ways: vocabulary (`core.COLUMNS`, `DERIVED`, `LINE`, `BOOLEAN_MEASURES`,
`team.GAME_MEASURES`, `SEASON_MEASURES`, `HISTORY_COLUMNS`); "can the
relation honor this" checks that raise `Unsupported` (a decline); and
refusal sentences built as `TemplateResult`s that rode on the Reading as
`point_refusal` (a `Cause` since step 4). Each sub-step is proven on the 628 recorded questions,
every unit-test outcome, and the 2,710 readings old tree against new (the 628 corpus questions and 2,082 outside it: 2,067 StatMuse feed questions with no names, 15 franchise wordings).

| Step | What | State |
| --- | --- | --- |
| 1 | Instrument: which decline and refusal sites fire, on the corpus, the feed and the unit tests | done 2026-10-02 (`~/association-research/stages/point-sites-1eb8677.txt`, `point_sites.py`). Of 40 raise sites the point reader reaches - 16 in `move.py` on the read path, 24 in `adapt.py`'s per-intent adapters - 18 fire on some population (10 and 8); 22 fire on none (6 and 16), four of those named by a unit test's expected message. Most of what fires is the reader saying what relation a question belongs to (`_everyone_guard`'s three: 56 corpus, 223 feed; `_everyone_point`: 29 and 67; "no adapter for" a chart intent: 73 and 21). The three refusals fire 10 times on the corpus and 4 on the feed, all rankings |
| 2 | Vocabulary to `measures.py`, as `STAT_ALIASES` went: the closed Measure type of `ROADMAP-TYPES.md` arrives by necessity | done 2026-10-02: eight name sets in `measures` (`GAME_COLUMNS`, `DERIVED_MEASURES`, `DERIVED_LINES`, `BOOLEAN_MEASURES`, `LINE`, `TEAM_GAME_MEASURES`, `TEAM_SEASON_MEASURES`, `HISTORY_STATS`), the SQL tables held to them by a test; the reader imports no SQL to learn a name. Identical on all three populations |
| 3 | Declines to the planner, one site at a time: the reader writes the point regardless and `plan_point` says `declined` with the same reason - the condition item's shape. A site that fires on no population and that no test names is deleted, not moved | done 2026-10-02 for the class the planner can judge (`e5b8954`): the five "cannot honor a cell" sites are `compose.plan._shape_declines`, with their sentences; 3 of 628 and 26 of 2,710 readings carry the point they declined before, every query, result and answer identical. The other 35 sites stay where they are, on purpose: 5 are the reader's own verdict that the question is no point on the player relation (`_everyone_guard`, `_everyone_point`, "no adapter for" a chart) - a reading, not a check against the answer; 30 are "the point needs a player / a stat / two names" checks inside the ten adapters, which Phase 2 deletes slice by slice, so restructuring them to hand the planner a partial point is work Phase 2 throws away. And nothing is deleted by coverage after all: Phase 3's rule ("the rest is mostly guards for recorded failure shapes") outranks the one written here; a silent site is left for its slice to judge |
| 4 | Refusals become causes: `point_refusal` holds a cause, not a `TemplateResult`; the planner builds the sentence | done 2026-10-03: `reading.Cause(kind, facts)` with the closed `CAUSES` (three kinds, all rankings), raised as `reading.PointRefused`, said by `compose.plan.refusal_result`; the reader imports no `TemplateResult` and no template. Identical on all three populations (10 of 628 and 14 of 2,710 refusals said word for word) |
| 5 | The file moves to `query/point.py`; the two ignores go; `point` joins the ratchet's reader set, so its 26 question-taking functions leave the list (a list edit, Jeff's) | done 2026-10-03: `query/point.py`; the two `parse -> compose` ignores gone, and ONE in their place, `point -> compose.adapt` - the intent's default point (`to_reading`, ten adapters that lean on the templates' helpers), which Phase 2 deletes slice by slice, so moving it now would be work thrown away. What the reader took besides went to its side: `Unsupported`, the limits, `ordinal_word`, `TEAM_ONLY_INTENTS`, `_career_scope` and `named_player_in` to `reading`; `METRIC_ALIASES`, `CAREER_METRIC_ALIASES`, `resolve_metric`, `MEASURE_ALIASES`, `WORD_MEASURES` and `stat_measure` to `measures`. `games_reading` (plans) went to `compose.plan`, `team_move_point` (read then plan, used by one test) to that test. `point` is in the ratchet's reader set; 28 entries left `ratchets.json`. Identical on all three populations |

**Phase 1, reviewed (2026-10-03).** An Opus agent reviewed the phase
adversarially at `423d559`, re-running every figure: the measured claims
held; the design goals held only where the gates looked. Its report, the
same-day response (six commits, two findings filed: #318, #319) and the
live run that closed the phase are in
`~/association-research/reviews/phase1-2026-10-03/` (`REVIEW.md` verbatim,
`RESPONSE.md` by commit), and the write-up Jeff reads is the Claude doc
"Phase 1 Review" (https://claude.ai/code/artifact/75355cb8-09ca-4cad-b559-753044d6d80d).
What an agent starting Phase 2 should take from it: the reader contract is
checked on chains and `point -> compose.adapt` is the one route, going with
the last adapter; `compose.answer` still re-plans 3 of 628 through
`games_reading` after the season-line presenter declines (the snapshot
recorded what ran, through `Agent.ran`; the re-plan went with slice
(iii), and `Agent.ran` with step 4, since the query run is the planned
one); the ~170 refusal
sentences outside the point reader become causes as each sayer is written;
the five ratchets catch the bypasses the review found and are name-based
still - a new way past one is a gate to add, not a trick to use; the
ledger undercounts (a word that changes any decision-record prose counts
as read), so 690 is not an upper bound.

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

**Phase 2, reviewed (2026-10-05).** An Opus agent reviewed the phase
adversarially at `93db30c`, re-running every population itself and
answering the 2,082 feed questions on both trees, which no step had done:
every one of the 212 differences on the 628 and the 478 on the readings is
enumerated in a commit; the live runs moved 1 answer of 277; the deletions
are real. Three exit claims are not true as written - "every answer reads
the compiler's SQL" (264 of 614 execute a compiled statement; 35 hand
statements moved whole behind `rows_of`/`values_of`, which the ratchet
does not count, so the 105 sites are 105 still), "`STATED_SCOPING` is the
one declaration left" (12 of 14 survive, unfrozen), the ledger (695, not
690: "team"/"teams" in five questions, three `team_leaderboard` and two
`team_outlook` - the review said three) - and 4 feed answers moved that no commit named (3 from the rate fix,
one of them now misleading: a 20-game qualifier met by games the rate does
not read). Its report, data and harnesses are in
`~/association-research/reviews/phase2-2026-10-05/`, its 14 findings are
in `ISSUES.md` (two P1s that predate the phase), and the write-up Jeff
reads is the Claude doc "Phase 2 Review"
(https://claude.ai/code/artifact/4877b1fa-0169-4cda-a606-d35bae737fd8).
Its cleanup list - (a) wrong-fix-now, (b) debt Phase 3 trips over, (c)
cosmetic - is the order from here, as Phase 1's was.

**After the review, the order (Jeff, 2026-10-05).** The cleanup list is
worked before Phase 3's Reading work, in this order, each item its own
commit proven on the populations: (a) the five wrong-fix-now items - the
rate's games count and qualifier over the games the rate reads, with a
note for the games it skipped (the one live wrong answer, first); the
freeze restored over the 12 scoping declarations, each labeled cell
table, `STATED_SCOPING` or debt; the three "What done means" rows and the
ledger corrected; the freeze test checking for a source file; the SQL
ratchet counting `rows_of`/`values_of` sites. Then (b)2 and (b)3, which
widen the proof before anything else moves - the planner as a recorded
unit-test boundary, and the 2,082 feed questions ANSWERED as a fourth
population. Then (b)1, (b)5 and (b)6 together as one step, "the Result is
typed": a run-time refusal is a `Cause`, `Result.facts` and `Span.source`
become cells, notes and causes, and the answer side dispatches on the
planned query's shape and relation, not the intent. (b)4 - the three
question re-reads in `agent.py` onto the Reading - is Phase 3's own first
item, since the Reading has to carry those values. (c) is taken in passing
by whoever is in the file.

**The cleanup, done (2026-10-05/06).** Four Opus agents, each proven on
the four populations: (a)1 `9b0ac75` - a measure a rebuilt line cannot
supply carries its own games count, a ranking's minimum qualifies on it
and a `lines_rebuilt` note names the games skipped (four feed answers
moved, each named; the Gibson row leaves the TS% ten); (a)2-(a)5 with #335
and #338 (`41faa66`..`4f50a2e`) - the freeze test checks source files, the
freeze is back over the 12 scoping declarations each labeled (4 cell
tables, 2 `STATED_SCOPING`, 6 debt with its owing step), the SQL ratchet
counts `rows_of`/`values_of` statements (38 in 11 -> 80 in 20, the honest
count, regenerated once with Jeff's say-so), the done table and the
ledger at the review's figures, the temp-directory leak closed, ISSUES.md
repointed; (b)2/(b)3 `2cf166e` - the planner is a recorded unit-test
boundary (1,408 -> 2,554 calls) and the 2,082 feed questions ANSWERED are
the fourth population (`stage_snapshots.py run --feed`); the typed Result
`bc04d95`/`c35f334`/`28debcd` - a refusal found at RUN is a `Refusal` or
`Clarify` with a kind from `result.RUN_CAUSES` (50, disjoint from the
reading's 17) said by one `refusal_phrase` table, answers worded outside
the sayer 35 -> 7 (all `refusals.unanswerable`, Phase 3's (b)4);
`Result.facts` is 17 typed records and the cells a read applied are a
typed `Cell` union on `Narrowing.cells` (period, line, role, game of a
series, calendar, met, shot value, a date); the answer side picks readers
and sayers by `plan.PointShape(relation, shape, by)` and the body's type,
never by intent (`compose/__init__.py` 72 -> 6 mentions, all prose; a test
holds it). Three `# Phase 3: needs ...` markers say where the Reading's
own shape, cells and declared tables take over. Nine of the review's
fourteen findings closed (#327, #329-#335, #338); #325, #326, #328 (the
answers), #336, #337 (cosmetic) and #324 stay open.

**From (b)2 and (b)3 on (2026-10-05), every step is proven on four
populations, base against tip, identical text and remarks included:** the
628 recorded questions answered (`scripts/stage_snapshots.py run`,
`compare`); every unit-test call across a stage boundary, the planner's
`plan_point` and `refusal_result` among them (`ASSOCIATION_STAGE_CALLS`,
`compare-calls`); the 2,710 readings (`reader_pop.py`, `reader_cmp.py`);
and the 2,082 feed questions answered (`stage_snapshots.py run --feed`,
`compare`; about four and a half minutes a tree). The brief the lead
gives an agent names all four.

**Phase 2, the expected steps (written 2026-10-03, before the phase; each
step is re-planned in its own row as it lands, as Phase 1's were).** The
unit of work is one intent: its reader and sayer written, measured both
ways on every recorded question it answers and every unit-test call it
makes, then its template body, presenter, adapter branch, scoping rows,
freeze entries, ratchet entries and renderer deleted in the same change.
Three rules hold throughout: `--values-only` is used for the sayer step
alone, and a rewording is reviewed once; a boundary that moves
(`compose.answer`, a presenter's call) is recorded in `tests/stage_calls.py`
on both sides before the old side goes; a test whose subject is deleted
(a template's sentence, a presenter, a scoping table) is deleted with it,
and a test of visible behavior moves to the stage that owns it only where
the snapshots do not already hold the case.

| Step | What | Deletes | Proof |
| --- | --- | --- | --- |
| 0 | The Result type and the first sayer, on one intent: `game_log` (the simplest "compiled and discarded" shape - rows). `ROADMAP-TYPES.md`'s `Result`/`Part` declared in code here, as the first phase that uses them; the sayer takes a Result and nothing else (contract 3's gate extended to it); notes travel as kinds and facts on the Result and are said by the sayer. This step sets the pattern every later slice copies, so it is reviewed once before step 1 | **done 2026-10-03** (`query/result.py`: `Result`, `Part`, `Rows`, `Span`, `Narrowing`, `Window`; `compose/logs.py` the reader, `compose/say.py` the sayer with one phrase per note kind). Deleted: the game log's body in `templates/games.py` (~600 lines, moved and split), `compose.present._present_game_log` and `_present_team_game_log` (presenters 12 -> 11), `_player_stat`'s rows branch. Kept, on purpose: the `game_log` adapter (its default point) and `STATED_SCOPING["game_log"]` (the one table declaring for every compiled intent) - the five adapters of slice (i) share `measure_filters`/`_stat_column`, and move to the reader's side together in step 1, not one at a time; the page's renderer stays keyed by intent until Phase 4. Measured first: the compiler's own `run` gives the same games and values as the template's read on 41 of 41 player logs the template answered, so step 1 can execute the compiled SQL for the log. Identical on 628 of 628 (text and remarks) and 1,391 of 1,391 unit-test calls; 6 tests added, none deleted (the log's tests already ran through the compiled path). Cost: the private-template-import ratchet grew 87 -> 91 by the relation's shared steps the moved body calls (`_Span`, `_span_of`, `_resolved_team`, `_slot_season`, `_no_narrowed_games`, `_log_carries_rebuilt`, `_period`, `_season_name`), listed with Jeff's say-so; the Result carries three things as words still (`Narrowing.phrase`, `Span.years`, `Result.empty`), named in `result.py`'s docstring |
| 1 | Slice (i), the rest: narrowed `player_stat`, `player_splits`, `record_when`, `period_split` - 55 answers that compiled a query and discarded it for the template's read. Execute the compiled SQL; the retired template's read goes | four template bodies, four presenters, four adapters, their scoping rows and renderers | **done 2026-10-04.** Seven commits, each identical on 628 of 628 (text and remarks) and every reading, and every unit-test call but the ones named: (a) the relation's shared steps public, `89d6fa3` (private-import ratchet 91 -> 59); (b) the five default points to the reader, five adapters gone (10 -> 5), `TemplateUnsupported` = `Unsupported`, `e05dae2`; (c) `record_when`'s reader and sayer, `14d9757`; (d) `player_splits`' (both branches, one sayer), `5876bc2`; then the merge (g) BEFORE (e) and (f), so those two were written once against the compiled statement (Jeff, 2026-10-04: the order with the least temporary code) - the log executing the compiled statement with `core.compile_over` as the seam (`e672922`), `record_when` as the compiler's new `line` group with a `margin` measure (`2b3e4df`), a player's splits as four grouped reads by `venue`/`starter`/`won`/the new `month_of_year`, measured 63 of 70 kind-reads equal first (`c6e9253`; one `core.rows_of` runs every compiled statement); (f) the narrowed `player_stat` as `compose/stats.py` with the `Scalar` body and the compiler's `line` AGGREGATE (per-game figures beside the sums, seasons and shot rates they are said from), measured 19 of 19 corpus and 13 of 15 synthetic cases equal first, `662910a`; (e) `period_split` as `compose/periods.py`, both shapes, the period relation's data moved to `player_games` under public names first (`b15eef1`), measured 18 of 18 equal once the compiler carried `opponent_name`, both presenters and 31 template helpers gone, `7a1e91c`. Fixed on the way, each asserted: two definitions of "the warehouse carries rebuilt lines" (one now, `box_source`); a rate over rebuilt lines summing attempts the rebuild never measured (`core._rate_sql`; FG% 93.3 -> 50.0 on the fixture); a made count alone over rebuilt lines stating those attempts ("43 of 64 (67.2%)" -> "43 in total"); a quarter's figures blanked for rebuilt games ("Anthony Davis turnovers by quarter 2015" refused, now 30/16/36/13); #301, #302; an empty-date sentence naming a season the date is not in. Presenters 12 -> 8, adapters 10 -> 5, private-template-import ratchet 91 -> 47, statements outside the relations 105 -> fewer in every touched module. NOT gone, on purpose: `STATED_SCOPING` (one table declares for every compiled intent; the planner's cells take it over in Phase 3), the page's renderers (Phase 4), `_present_player_stat_season_line` (slice (iii)), the team log and team splits' own reads (the team compiler has no rows or grouped compile; slice (iv)). Open from the slice: #320 (a quarter "over his last N games" answers the season) |
| 2 | Slice (ii): `threshold_count`, `single_game_high`, `player_matchup`, `streak` - 29 answers the compiler reads and the template words. The wording moves into sayers, the notes into the Result; each refusal sentence in those bodies becomes a `Cause` the sayer says (the point reader's three kinds grow by what these carry) | four template bodies, four presenters, four adapters; `refusals.py`'s entries for these shapes | **done 2026-10-04, in part.** Two Opus agents in parallel worktrees, each measuring first and executing the compiled statement; ten commits, every one identical on 628 of 628 (text and remarks), every unit-test call and every reading, except the 11 readings one decline fix moved (named): half A - `threshold_count` (`compose/counts.py`: a `Scalar` with `how="count"`, or a `Grouped` by player for the league) and `single_game_high` (`compose/highs.py`: a `Rows` with `by=` the measure), 49 of 49 presenter answers matched the compiled rows first; their adapters to `point.DEFAULT_POINTS` with the below/above line reader in `lines.threshold_count_line`; the defaulted-season redirect a `Decided("season_redirected")` with one phrase (the season line, fingerprint and shot chart now phrase through it); `5b1fab7`, `5b5cae5`, `d5ab763`, `8734711` (a decline named the wrong missing fact - "needs a player" to a question that named him; 11 readings reword). Half B - `streak` (`compose/runs.py`, the `Runs` body declared per the draft, `say_one_run`/`say_run_listing` shared with the team streak's presenter until slice (iv)) and `player_matchup` (`compose/pairs.py`: a `Grouped` by subject plus a `Rows` detail of the newest meetings; a teammate's absence emptying the meetings compiles the pair twice more where the template re-narrowed by hand), 25 of 25 matched first (11 corpus matchups, 19 synthetic since the corpus holds no player streak); `measures.streak_column`, `reading.DEFAULT_STREAK_LIMIT`; `8b5a617`, `adadd78`, `3c72a18`, `f6e474d`, `78dcbcc`, `8016a0d`; two unsaid remarks and a mis-aliased minutes column fixed. Presenters 8 -> 4, adapters 5 -> 1 (`with_without`, slice (iv)), private-template-import ratchet 47 -> 23, statements 96 -> 93. **NOT done, deliberately: the `Cause`s.** Every refusal in these shapes stays a plain decline with its exact sentence (inventoried in both agents' reports: a stat with no threshold, two names that are one person, a league-wide count on the one-player relation, ...), because a user-visible decline becomes a `Cause` only by moving the reading's verdict and `answered_by`, and the readings population holds the verdict identical. About 15 readings would move. Jeff agreed 2026-10-05: allowed, as one enumerated reading move in its own commit (the rule is in AGENTS.md, "Identical means identical"); done beside step 3. `refusals.py` had no entries for these shapes. The `--values-only` allowance was not used: text identical throughout |
| 3 | Slice (iii): the season-line relation - `leaderboard`, `player_history`, `player_compare`, unnarrowed `player_stat`, 81 answers. The riskiest slice: the existing readers (`leaderboard.py`'s floors, traded-player dedup, qualifiers; `_leaderboard_ranking`, `_player_compare_lines`, `_player_history_read`) MOVE under the relation, not re-derived; `games_reading`'s re-plan in `compose.answer` goes here, since the relation says for itself whether it reads a point; `MAX_LIMIT` becomes one | the last four template bodies and presenters, `present.py` itself, `adapt.py`'s last branches and the file, with the `point -> compose.adapt` ignore (the reader takes the default point from the relation's declared shapes) | **done 2026-10-05.** Three Opus agents in parallel worktrees - two on the compose side, one on the reader seam - every commit measured first and proved on the three populations; a live yardstick run before (`live_parser25`, on `122f288`) and after (`live_parser26`, on `fd3c726`): 277 of 277 answers identical, 167 of 175 and 156 of 166 both times. `leaderboard` -> `compose/rankings.py` over `leaderboard.rank_season_line` (the floors, dedup and qualifiers untouched; a `Grouped` by player with `ranked_by` so the ranking sayer tells a stat ranking from the league's count over a line by the body; 67 of 67 presenter answers matched first; `605a96f`). `player_stat`'s unnarrowed line, `player_history`, `player_compare` -> `query/season_line.py` (the relation: the subject settled, each statement the templates ran moved whole, executed through one door `core.values_of`) and `compose/seasons.py` (116 of 116 recorded and 29 synthetic matched first; `7e7a45b`); with them `PRESENTERS`, `present()` and the last three presenters are gone - `present.py` holds `STATED_SCOPING` and `present_team` until slice (iv). The reader seam: `with_without`'s adapter a default point and `compose/adapt.py` deleted, import-linter contract 1 with no ignores (`5153281`); `MAX_LIMIT` one definition, `reading.MAX_LIMIT` = 50 - the leaderboard's 100 never limited an answer, measured (`4c0e981`); the declines the user saw are `Cause`s, `CAUSES` 3 -> 16, each said by the missing fact - the ONE enumerated reading move Jeff allowed: 3 of 628 recorded questions (the two "per 90" rankings' readings, answers identical; "Display Luka's average assists for each consecutive year" refused by a bare message before, answered with its cause now), 20 of 2,710 readings, 31 unit-test calls in 19 rewritten tests (`7f3df4d`; the inventory of what stayed a decline, and why, is in its CHANGES entry); `games_reading`'s re-plan gone - the planner plans an unread season-line point as the game-level one (`plan._game_level`), 50 feed readings' planned query moves, every answer identical (`34d2a98`). Presenters 4 -> 0, adapters 1 -> 0 (the freeze retired), private-template-import ratchet 23 -> 16. Filed: #321 (36 league-wide questions read under the wrong intent refused with a relation hand-off). Not said today and left for a sayer allowed to reword: a leaderboard's defaulted season (54 of 67 answers) |
| 4 | Slice (iv): team shapes, about 40 answers - the team log, with/without, `team_record`, `team_stat`, `team_leaderboard`, `team_outlook`, `team_quarter_points`, `head_to_head`, `period_leaderboard`, `coach`'s refusal. Standings and projections become a team-season relation; `compose/team.py` is the team compiler already and absorbs the team templates' reads | the team templates (seven of the twelve in `TEMPLATES`), `check_scope` and `HONORED_SCOPING` with the last template that used them | **done 2026-10-05.** Four Opus agents in parallel worktrees, merged in finish order with each later branch rebased and re-proved on the merged tree, bracketed by live runs (`live_parser26` before on `fd3c726`, `live_parser27` after on `4db543a`: 277 of 277 identical but the one fix named below; 167 of 175, 156 of 166 both times). The team compiler (`compose/team.py`) gained `rows` and `grouped` compiles (`compile_team_over`; groups `venue`, `won`, `month_of_year`), `compile_team_run`/`range`/`presence`/`line`/`count`, all through `core.rows_of` taking `TeamCompiled` - measured 27 of 27 team logs and 24 of 24 split kind-reads equal first (`b7b554b`); the team log and splits execute them; the team streak, with/without and a team's own-line record are readers and sayers (`compose/runs.py`, `presence.py`, `records.py`) and **`compose/present.py` is deleted** - `STATED_SCOPING` lives in `compose/plan.py`, the presenter freeze is retired (`28088f7`, `e9b20a6`, `0cfdba2`). The team-season relation `query/team_seasons.py` (standings, the power index's projections, the season stat and record tables, the venue and since-bounded records) holds the statements moved whole through `core.values_of`; `team_stat`, `team_leaderboard`, `team_outlook` -> `compose/team_stats.py`, `coach` a `Cause` (`no_coach_table`, CAUSES 16 -> 17) - with three intents moving from a decline to a point in one enumerated commit (24 recorded readings, 175 of 2,710, answers identical; `07f024f`, `bedaf75`, `4eac80e`, `3435600`); `team_record`, `head_to_head`, `team_quarter_points`, `period_leaderboard` -> `compose/team_records.py`, `standings.py`, `meetings.py`, `periods.py`, four more intents gaining a point (45 recorded readings, 121 of 2,710), `templates/teams.py` deleted whole and `templates/games.py` down to one helper (`4db543a`). Beside it the season line became one module and one door (`leaderboard.py` folded into `season_line.py`, `season_redirect` and `seasons_on_record` with it; `787a8db`), the `ran` boundary went (`c0a96cc`), and a ranking's Result now names the season line as its relation (`a9aa7aa`, a defect found on the way). Fixed with the move, each asserted: the two "written and not said" floors on the Warriors' combined road record now said ("523-791 (.398) regular season from 1993-94 - ESPN's standings carry no home/road split before 1993-94, ..."); the team-only intent check that would have gone silent on compiled intents moved above the compiled branch in `agent.py`; `scripts/stage_snapshots.py remarks` reports 0 unsaid. Templates 12 -> 4 (the charts), presenters 0, adapters 0, private-template-import ratchet 16 -> 2, statements outside the relations 105 -> about 60. **The rule extended** (the lead's call, 2026-10-05, to be confirmed by Jeff): an intent gaining a point when it is ported is the same class of enumerated reading move as a decline becoming a Cause - a planned query needs a point, the answers stay identical, and every moved reading is named in the commit. **Still here, on purpose:** `check_scope` and `HONORED_SCOPING` - the four chart templates still call them through `agent._run_scoped_template`; they go with slice (v). Pattern compromises the agents named for slice (v) and Phase 3: `facts["missing"]` on a team-season Result with nothing to say (a Cause-like body would be cleaner); two `note_phrase` kinds that branch on a call argument (`consequence=`, `about=`) rather than a fact, because a fact would have changed recorded remarks; `TeamCompiled.span` typed `Any`; the team log narrows with `Scope(venue=...)` alone, so `situation`/`period`/`half` are dropped silently on a team log as they were before (filed) |
| 5 | Slice (v): charts - `shot_chart`, `fingerprint`, `shot_distance`, `player_netpoints`, 39 answers. Per the decision: declared shapes with their own readers and renderers, each named with its relation, the reader declining them from its own set rather than "no adapter for". No port onto shots/NetPoints relations until after Phase 3 | the four chart templates as templates (they remain as declared shapes); `TEMPLATES` empty or chart-only | **done 2026-10-05.** Two Opus agents: `fingerprint` and `player_netpoints` -> `compose/netpoints.py` (a `Chart` body declared per the draft, a `draw` step between reader and sayer writing the page so the sayer takes the Result alone; 51 of 51 and 14 of 14 identical first, 41 pages byte-identical; `templates/netpoints.py` deleted; `d8e22bb`..`d36ea50`), `shot_chart` and `shot_distance` -> `compose/shots.py` (38 recorded and 64 direct scopes identical first, 22 pages byte-identical; `templates/shots.py` deleted with `shotchart.render_*`; `bc9b524`..`6b46d56`). Each ran 0 statements of its own - every read through `core.values_of` over a `Statement` - so the SQL ratchet gained no entry. The reader's own set: `reading.CHART_INTENTS`, four `DEFAULT_POINTS` entries on the declared `netpoints`/`shots` relations, the planner declining beyond `STATED_SCOPING` with `check_scope`'s sentence; the reading move enumerated per branch (46 and 40 recorded readings, answers identical). `TEMPLATES` is empty. Fixed on the way, each tested: an unreachable `rate="total"` branch; a free-throw chart in a defaulted season adding a redirect after its refusal; a shot distance in an empty defaulted season now redirecting as the chart does (#18); #296. Filed: #323 (P1 - a date written as numbers is not read). Not a `Decided` yet: `compared_but_unmatched` reads the question's text in the answering loop - Phase 3's Reading carries the dropped names |
| 6 | Exit: `templates/` holds no body, `present.py`, `adapt.py`, `check_scope` and every scoping table but the per-relation cell tables are gone; `test_frozen_shapes.py` freezes only intents and renderers; the private-template-import ratchet is 0 and its check deleted; `AGENTS.md`'s template sections rewritten; a live run closes the phase and an independent review of it, as Phase 1 had | the freeze entries and ratchet checks that have nothing left to hold | the "What done means" table's Phase 2 rows at their Done values | **done 2026-10-05, but the live run and the review (the lead's).** One Opus agent, five commits on `893557d`, each identical on 628 of 628 recorded questions (text and remarks) and 2,710 of 2,710 readings, and on every unit-test call but the ones named. In an order of its own: the types first, so the shared steps moved once - `TemplateResult` is `answer.Reply`, `TemplateContext` `answer.AnswerContext`, the `TemplateUnsupported` alias gone (`efa28a4`); then `check_scope`, `HONORED_SCOPING`, `TEMPLATES` and `Agent`'s template branch deleted before anything moved, since they declared nothing (`1fae3b9`; the stage-call recorder's `template:` boundary recorded 0 of 1,408 calls - the readers behind `compose:answer` replaced it in step 0; the freezes of templates and of the 14 scoping declarations went, `test_frozen_shapes.py` holds the 25 intents and 22 renderers); a refusal naming a template that no longer exists says "has no reader" (`3018d27`; 5 unit-test calls, 0 recorded); then the move (`efd82f9`): 96 names to the modules of their callers under public names - `query/player_relation.py` 37 (new: a sibling of `player_games.py`, which together would be 2,600 lines), `query/coverage.py` 17 (new: `SOURCES` one table for every reader), `query/team_relation.py` 12 (new: in `team_games.py` it would be an import cycle through `conditions`), `season_line` 8, `entities` 5, `reading` 4 (`unhonored_scoping` there, not in `plan.py`, which imports every reader), `compose.say` 4, `season_text` 3, `conditions` 3, `player_games` 2, `compose.rankings` 1 - and 29 private aliases deleted; `templates/` deleted (3,011 lines at the step's start); the private-template-import ratchet 2 -> 0 and its check deleted; the SQL ratchet 38 statements before and after (`templates.*` 18 -> `player_relation` 16, `team_relation` 2, hand-edited: `--shrink` cannot add); `AGENTS.md`'s template sections rewritten (`5cb27ad`). Tests: 3 deleted with their subject, 11 moved from `check_scope` to `STATED_SCOPING` or the planner. Lines: `src/association` 45,916 -> 45,563. Still here, on purpose: `STATED_SCOPING` (Phase 3's cells), the page's 22 renderers (Phase 4), `docs/architecture.rst`'s description of the templates (Phase 4), and the word "template" in about 150 docstrings and comments that tell where a statement or a rule came from |

Order between steps 1-5 is the roadmap's (least to most risk, the
season line last among the player shapes, charts last of all); inside a
slice the intents go one at a time, each its own commit. A slice that
turns up a P1 fixes it in the code that exists before deleting that code.

**Open from step 1, for the next slices to settle (the agents' notes,
2026-10-04).** What the five ported readers still carry against the
target types, named so a slice takes it over rather than copies it:

- **A sayer branches on the body's type, and on nothing else**
  (`ROADMAP-TYPES.md`, "The shapes"). Two places break that today: a
  one-quarter `period_split` Result is a `Rows` body recognized in
  `say()` by a `"period"` key in `Result.facts`, because `Rows` has no
  `by` and the period is a cell the Result does not carry; and
  `read_ported` dispatches by INTENT (`game_log`, `record_when`, ...)
  before the sayer sees the body. Step 2 added the body fields the draft
  implies rather than keys in `facts` - `Scalar.how` (a count against an
  average), `Rows.by` (the measure a listing is ordered by, against
  `"date"`), `Grouped.by == "player"` for a league count - and the
  ranking sayer slice (iii) writes has to tell a count-over-a-line ranking
  from a stat ranking by its measure, not by intent. The draft's answer is that the period
  is a cell (`Part.cells`) and one `rows` sayer says every log; the
  slice that declares `Cell` (Phase 3's reading, or slice (ii) if it
  needs it first) collapses the two log sayers into one.
- **A decision rides on `Result.decisions` as a value** - decided
  2026-10-04 after step 1 (f) and (e): `result.Decided(kind, field,
  chose, before, instead_of, why, facts)`, phrased ONCE by
  `say.decision_phrase` and recorded through `notes.decided`, exactly as a
  `Note` is through `note_phrase`. The period redirect's `season_fallback`
  is the first; a slice that finds a `decided(...)` in a template body
  moves it onto the Result the same way, never into `facts`.
- **Words the Result still carries, on purpose:** `Narrowing.phrase` (the
  relation's `filters()`), `Span.years`/`Span.phrase`, and the `empty`
  sentence (the shared "which fact is missing" writer). Each goes when a
  sayer words it from cells; the Result's docstring lists them.
- **`STATED_SCOPING` stays until Phase 3** (one table declares, for every
  compiled intent, which narrowings the retired words state); the
  planner's cell checks take it over with the Reading's typed filters.
- **The snapshot's `query` stage is the planned query, and nothing
  reports another** (step 4, 2026-10-05). `compose.answer`'s `ran`
  callback and `Agent.ran` recorded the query the compiler executed where
  it differed from the planned one - `games_reading`'s re-plan, gone with
  step 3, after which every ported reader compiles the planned point and
  the two were the same object on every call. A slice that finds a reader
  running something other than its planned point plans it in the
  planner (`plan._game_level` is the precedent), never reports it beside.
- **The team log and the team splits read the team relation themselves:**
  the team compiler has no `rows` or `grouped` compile. Slice (iv) gives it
  them and the two readers execute the compiled statement like the
  player's.

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

**Phase 3, the expected steps (written 2026-10-09, before the phase; each
step is re-planned in its own row as it lands, as Phase 2's were).** The
paragraph above was written on 2026-09-30, before Phases 1 and 2 showed
how a stage is moved here: the "typed reader built beside the old one and
run in shadow" is not a second reader in `src/` - a dispatcher between an
old and a new reader is the shape "Every step deletes the path it
replaces" forbids - it is the four populations, which already diff every
reading on 2,710 wordings and every reader test's call, stage by stage.
So the reader converges on `ROADMAP-TYPES.md`'s `Reading` one part at a
time, each step writing the typed part, deleting the slot-era carrier and
the stage that read it in the same change, and proving on the four
populations that no query, result or answer moved and that every reading
moved only in the part the step typed. Where the step is in flight the
lead is `/home/jeff/code/association`'s session; agents work one at a time
on the parser-compiler seam (AGENTS.md, "Parallelism is for disjoint,
bounded, measurable work"), so the steps are serial unless a row says
otherwise.

What the reader is today, measured on `87cc782`: `router.py` 3,072 lines,
70 functions, 102 regex sites, 26 stages run in order by `_settle` over a
raw slot dict; `subject.py` 1,482 lines and 24 regex sites; `point.py`
1,543 and 20; `parse.py` 1,000 and 16; `Scope` 44 slots; 24 functions
outside the reader take the question (`scripts/ratchets.json`,
`question_outside_the_reader`), 6 modules outside it import `re`, 23
reader functions take a connection; the reader tests are
`test_router.py` 213, `test_entities.py` 99, `test_subject.py` 47,
`test_parser.py` 33, `test_refusals.py` 16. The done table's targets for
each are at the end of this file.

| Step | What | Deletes | Proof |
| --- | --- | --- | --- |
| 0 | (b)4 from the Phase 2 review: the three question re-reads in `agent.py` (`compared_but_unmatched`, `player_named_on_a_team_only_question`, `refusals.unanswerable` with `by_question` and `too_short`) become typed values and `reading.Cause`s on the Reading, read by the parser and said by the planner through `refusal_phrase` with the sentence identical - the order the planner says them in unchanged (the eight `unanswerable` checks only where the planner declines). The draft's `Unsupported(what, as_typed)` filter is what this declares. `entities.py`'s readers of the question (`players_named_in` and its kin) move to the reader's side once nothing else calls them | `refusals.py` (its regexes to the reader, its sentences to the phrase table), the three sites in `agent.py`; `question_outside_the_reader` 24 -> the trace writer alone; the regex ratchet's two `refusals` entries | the four populations; the one enumerated move is every reading whose verdict gains a cause, answers identical. **done 2026-10-09** (`c920976`). One Opus agent, eight commits on `87cc782` rebased onto master after the 5.0.0 release, re-proved by the lead on the rebased tree against a fresh run of master: 628 recorded questions, 619 identical and 9 readings moved (7 gaining a cause, 2 the fingerprint's `left_out`), result, answer and remarks identical on all 628; 2,082 feed answers, 1,918 identical and 164 readings moved (163 the causes, 1 the plus-minus fix keeping its stat), later stages identical on all 2,082; 2,710 readings, 2,537 identical and 173 moved, none outside the enumerated fields; unit-test calls 2,536 identical and 14 moved once aligned past 16 enumerated new `refusal_result` calls (`~/association-research/stages/calls_aligned.py`: `compare-calls` keys a call by its position in its test, so one added call reads as every later one moved - a step that adds a recorded call aligns first). Measured first (`refusal_sites.py`): per check, what fired on the 628 and the 2,082, with what sentence, and whether it reached the answer - no recognized question was answered by the compiler, none was recognized twice; the old checks against the new path on 2,710 readings plus 3,834 hand-built ones, 0 differ, 17 with a sentence perturbed. What the Reading carries now: `Reading.refused` (`championship`, `no_player_reading`: planned ahead of the point's own refusal, said before any reader runs), `Reading.unsupported` (the draft's `Unsupported` filter as causes - `playoff_round`, `non_calendar_situation`, `period_stat`, `period_as_condition`, `team_period_stat`, `bench_points`, `team_boolean_count` - recognized whatever answers, the first said only where the answer side declined, since 27 of the feed's 32 situations and all 5 bench-points questions fired after a RUN-time decline, not the planner's: the plan's "said where the planner declines" was wrong, and step 2's cell checks are what moves them to PLAN), `Reading.left_out` (the fingerprint's "vs" names), and `too_short` as the parser's first verdict; `CAUSES` 18 -> 27, every sentence word for word in `refusal_phrase`, page data held through `Refusal.shown`. The twelve question-reading functions of `entities.py` are `subject.py`'s and take the in-memory index (`entities.players_of`/`teams_of`), since moved as they were they would have added ten `con_in_the_reader` entries; `refusals._team_where_a_player_belongs` deleted (reached by no reading; a test holds its predicate); `conference_named` to `calendar.py`, `TEAM_PERIOD_COLUMNS` to `measures`. Ratchets: `question_outside_the_reader` 24 -> 1 (`history.RunHistory.write`, the trace writer, which runs in a `finally` with no Answer to carry the question), `regex_outside_the_reader` 6 -> 4; `con_in_the_reader` 23 and the SQL count 80 unchanged. Tests: 2 deleted with the dead check, `test_refusals.py` -> `test_unsupported.py` (12 re-seated), 5 re-seated or moved elsewhere, 3 added. Ledger 695 -> 694 ("championship" is read). Fixed on the way, each tested: a quarter's plus-minus answered as points ("vj edgecombe 2nd half plus minus" -> 567 points; now refused for plus-minus, 2 feed readings keep their stat), and the conference refusal's false "no conference or division membership" (one unit-test call moves). Filed: #339, #340 (P1: a conference the subject belongs to answered as the league; a companion's unread line answered as the with/without split), #341, #342 (P2), #343, #344 (P4). No live run: answers identical on every population, and the phase's own run is step 5 |
| 1 | The answer side's key is the Reading's own: `Reading.shape` and a `by` the reader names (the point's `shape`/`group` today, which `plan.shape_of(intent, query)` re-derives), so `shape_of`, `SHAPE_WORDS` and `words_stated` go and `Planned.shape` is read off the Reading; `Planned.floor` from the relation's declared tables (`coverage.SOURCES` keyed by relation and the tables the read touches, not the retired words). Before any filter moves, so the planner's cell checks of step 2 have a key that is not an intent. With it, the readings population's projection: `stage_snapshots.py` and `reader_pop.py` compare a Reading through one projection function (`reading.projected()`, the old slot dict and verdict), so a step that types a part proves it against the same baseline | the three `# Phase 3: needs ...` markers in `compose/plan.py`; `SHAPE_WORDS`; the intent key of `SOURCES` | the four populations; readings identical through the projection. **done 2026-10-09** (`ff23541`). One Fable agent (medium effort, Jeff's call for the day), two commits on `1f4c45f`, rebased by the lead onto a master that had moved by ten isolated fixes and re-proved against a fresh run of it: 628 of 628 recorded questions, 2,082 of 2,082 feed answers (but the two the fix commit names) and 2,710 of 2,710 readings identical with the four added point fields ignored and listed by count, 2,612 of 2,612 unit-test calls identical aligned past them. The point reader declares what the answer side keys on - `Reading.shape` in the target vocabulary (`scalar`, `rows`, `ranking`, `comparison`, `split`, `runs`, `chart`), `Reading.by`, and `Reading.on` (the nine target relations; `Reading.source` folded into it) - at 45 construction sites in `point.py`; `plan.point_shape` builds the key from them with the planner's one move (a season-line point re-planned at the game level is on `player_games`, 50 of 2,710), `plan.skeleton_of` derives the compiler's skeleton and source from them, and `Planned.shape` is set on every verdict. Deleted: `shape_of`, `_player_shape`, `_team_shape`, `_TEAM_WHOLE_SHAPES`, `_TEAM_SEASON_SHAPES`, `SHAPE_WORDS`, `words_stated`, `_stated_by_words`, `Planned.floor`, `TABLELESS_INTENTS`, `RANKING_INTENTS`, `coverage._BOX_SCORE_SCOPING` (freeze 12 -> 11, debt 6 -> 5), `_sources_for` and six resolvers. Kept, named: `SHAPE_NAMES` (a decline's sentence alone; step 2's typed causes reword it), `Reading.on` beside `Reading.relation` (measured: the target relation is not derivable from the point's fields - a quarter's log and a game log share shape and `by`, a team's total under `team_stat` is keyed to the season line on a point the team compiler plans - and `everyone` is not the subject's kind on 93 of 674 such points; step 2 retypes the subject), `Reading.by` a string doubling as a reader key (`line`, `count`, `measure` are reductions, not dimensions; honest only when step 4 keys readers on the grammar). The floor is the planned point's: `coverage.SOURCES` keyed by `PointShape`, one entry per route (a test holds `set(SOURCES) == set(_ROUTES)`), five resolvers where a table depends on the measure, `RELATION_SOURCES` for a point no reader takes; `check_coverage(shape, scope)`, `coverage_caveat(shape, scope, intent=)`, `ranking` the shape; a reading with no point has no floor (0 of 207 declined readings were floor-refused). Measured first (`shape_floor_pop.py`/`shape_floor_cmp.py`): 2,457 planned points under 32 keys, every key decided by (intent, relation, source, skeleton, group) plus the re-plan; the intent-keyed floor put three table sets under one key. The proof tooling: `stage_snapshots.py compare --ignore STAGE.PATH` and `reader_cmp.py --ignore`, each listing the field's values by count; `calls_aligned.py --drop`. Fixed on the way, each tested: #212 (a narrowed line held to the 1977 season-line floor, "Jordan's points in 1990 on tuesdays" answered "no games found"; no population answer moved) and, its own enumerated commit, a game-level league ranking floored by the box scores it reads ("how many players averaged 30 ppg in 1986", "most playoff wins by player nba 1990": the floor's sentence where "No games for every player" stood; the two feed answers). Tests: 1 deleted, 3 re-seated, 3 added, about 60 sites re-pointed through `tests/shapes.py` (the test-side name-to-key table until step 4). Ratchets unchanged |
| 2 | The filters, one family at a time, each slice its own commit: the typed `Filter` on the Reading (`ROADMAP-TYPES.md`, "Filter"), the stage(s) of `_settle` that read it rewritten as ONE tagger that claims the span it read (contract 2) and keeps its regexes in `query/lexicon.py` (created by the first slice; contract 6), the relation's cell table row saying how the cell is applied, said or refused (contract 4; the planner checks the typed cell against it, which is what retires `STATED_SCOPING` and the six debt declarations row by row), and the `Scope` slot deleted. Families, least risk first: the span (`season`, `season_type`, `season_type_unstated`, `span`, `since`, `until` -> `Span`); the window (`order`, `limit`, `rank`, `ranked_by`, `offset` -> `Window`); the games' cuts (`opponent`, `own_team`, `venue`, `date`, `situation` as calendar or alignment, `round`, `game_n`, `season_n` -> `Opponent`, `Tenure`, `Venue`, `OnDate`, `DateRange`, `Calendar`, `Round`, `GameOfSeries`, `SeasonOfCareer`); the period (`period`, `half` -> `Reading.period`); the line and the companions together (`stat`+`threshold`, `above`, `below`, the point's `predicates`, `ConditionSpec`, `PeriodCondition`, `with_player`, `without`, `conditions`, `router.Beside` -> `Line`, `Companion`, `Won`, `Met` - the five carriers of a line become one type, built from the words and never converted from the slot pair, the nine multi-line corpus questions the test); the subject's own (`position`, `team` as `of_team`, the `everyone` relation). The reader's `measures`/`aggregate`/`order`-by-measure go with the window and the line (the `Measure` catalog, `ROADMAP-TYPES.md`). Each slice measured first: a harness answering every recorded question with the slot and with the typed cell, the relation's cell applied both ways | per slice: its `_route_*` stages (26 in all), its `Scope` slots (44 in all), its rows of `STATED_SCOPING`, `RELATION_SCOPING*`'s untyped form and the debt declarations (`_TEAM_READER_REFUSES`, `COMPILER_SLOTS`, `SCOPING_SLOTS`, `_BOX_SCORE_SCOPING`, `_CONDITION_PLAYER_ONLY_CELLS`, `_MODEL_SLOTS`); at the end `Scope` itself, `STATED_SCOPING`, `WITH_WITHOUT_STATED`, and the freeze test holds one cell table per relation | per slice, the four populations through the projection; `claims_ledger.py` not grown; a behavioral test per (relation, cell) that applying the cell changes the result. **The span: done 2026-10-09** (`dd9f297`). One Fable agent (medium effort, the day's exception), one commit on `ff23541`, rebased by the lead onto a master that had moved by thirteen isolated fixes (two conflicts: the changelog and the coverage caveat's ranking flag beside the span read) and re-proved against a fresh run of it: 628 of 628 recorded questions, 2,082 of 2,082 feed answers and 2,710 of 2,710 readings identical through the projection with the one added record field, `reading.span`, ignored and listed by count; unit-test calls 2,611 of 2,624 identical and 13 moved, aligned past the added `claims` and the snapshot's `span` - every move in `tests/query/test_subject.py`, five tests that handed the stages a model-era `season=2026` on a question naming no season, which the stages now read from the words alone; the relation's `ResolvedSpan` identical on 2,710 of 2,710 (`~/association-research/stages/span_family.py --compare`, the measurement the type was designed from: `season` set on 758 readings, `season_type` 2,705, `span` 365, `since` 175, `until` 88, `season_type_unstated` 89); the ledger 694 of 2,409 in 393 questions, the report identical line for line; the ratchets unchanged. `reading.Span(season, season_type, both, career, since, until)` on `Scope.span` - fields, not the draft's union, since a career stands beside a named season on 3 readings, beside a range on 8, and the relation is what picks - with the six slots gone from `Scope` (`from_slots` builds it, `to_slots` and `projected()` give the old shape back); `query/lexicon.py` (22 named patterns, `season_spans`/`season_from_text`/`season_named`; `season_text.py` keeps the sayer's words); `query/span.py`, the one tagger, run last in `_settle` over a typed `SpanContext` of what the stages settled, claiming what it read (`reading.Claim`, `Reading.claims`; a nested claim folds, a partial overlap fails the reader); the three cells `career`/`range`/`both` in `RELATION_SCOPING` and `TEAM_RELATION_SCOPING` with 25 per-reader exclusion rows, no row of `STATED_SCOPING` naming a span cell, `SCOPING_SLOTS` and `_MODEL_SLOTS` shorter by the span's names; `Reading.subject_span` the one typed override where the point's `span`/`season` pair stood. Deleted: `router.py` 3,081 -> 2,675 lines (six stages, 16 regexes, `SEASON_TYPES`), `subject._N_SEASONS`, `point._EVER`; 5 tests deleted, 20 moved (`test_season_text.py` -> `test_lexicon.py`), 16 added (59 cases, a behavioral one per (relation, cell)). Filed: a condition read's covered scope reading a range's first season and not its last (P3). The pattern the next families copy is in `AGENTS.md` ("A filter family is read by ONE tagger") and `ROADMAP-TYPES.md` ("Decided"). Next: the window. **The window: done 2026-10-09** (`b76ef47`). One Fable agent (medium effort, the day's exception), one commit on `1599742`, rebased by the lead onto a master that had moved by sixteen isolated fixes and re-proved against a fresh run of it: 628 of 628, 2,082 of 2,082 and 2,710 of 2,710 identical through the projection with the one added record field, `reading.window`, ignored and listed by count; unit-test calls 2,625 of 2,626 identical and 1 moved, aligned past the added claims and the snapshot's window (test_subject.py's position-group test, which handed the stages a model-era limit=1 on a question naming none; it stops passing it); the relation's cut identical on 2,710 of 2,710 (`window_family.py --compare`, the measurement the type was designed from: `order` set on 250 readings, `limit` 352, `rank` 86, `ranked_by` 2, the point's `offset` 0 on all; the stages moved the grammar's read on 59, and every drop of the COUNT was undone by the parser's second read, so the five filler rules were dead and are deleted, not ported); the ledger 694 of 2,409, identical line for line; the ratchets unchanged. `reading.Window(order, count, of, rank, by)` on `Scope.window` - fields, not the draft's `order: recent|first|top|bottom`, since a count stands with no end on 102 readings and an end never without a count, and "top"/"bottom" name no end of the span - with the four slots gone from `Scope` and `Reading.offset` deleted; `query/window.py`, the one tagger, run just before the span's (whose three rules read the final window as context, `span._LAST` gone), claiming what it read; the cells `window` and `ranked_by` in `RELATION_SCOPING` and `window` in `TEAM_RELATION_SCOPING`, 7 exclusion rows renamed with their reasons, `compose.core.COMPILER_SLOTS` deleted (the freeze 11 -> 10, the debt 5 -> 4); `router.py` 2,675 -> 2,353 lines (five stages, `parse.window`/`window_scope`, 8 regexes). 7 tests deleted, 2 re-seated, 16 added (26 wordings; a behavioral check per (relation, cell)). Filed (P1): a window on a reader that refuses one is dropped before the planner can refuse it ("Warriors vs Mavs record last ten games" answers this season's three meetings; 4 answers on the 2,710), the model-era drop ported as-is to hold identical - a decline-to-Cause commit. Still open 9: a bare count on the games relations is a default, not a cell. A fix the agent made in passing (one game named as his in a phrasing the grammar misses: "his last home game" drew the season's home games) grew the ledger 694 -> 709 - "season" in ten of the corpus's Curry shot-chart and NetPoints wordings and "regular" in five of them ("create a shot chart of steph curry's last regular season game", "display a shot chart for steph curry's most recent regular season game", ...), words the one-game rule now reads across where deleting either had made the whole window vanish and so counted the word as read, an overcount exposed rather than a dependency lost - so it waited on its branch; Jeff's call the same evening, "merge and let it grow in this case": merged as `d12f899`, every population identical. Next: the games' cuts |
| 3 | The unread words on the Reading: every tagger has claimed its span since step 2, so `Reading.unread` is the content words nothing claimed, overlapping claims fail in the reader, and `scripts/claims_ledger.py` becomes the check from outside that the Reading's own `unread` agrees with deletion (the two measure the same thing two ways; a disagreement is a rule that matched a word it did not need). The nine unread numbers reviewed one by one: each is a `Line` or a `Window` the reader missed, or a refusal | the ledger's deletion probing as the only measure | the ledger's count not grown; the Reading's `unread` identical to it on the 628 |
| 4 | The exit: `intent` leaves the reader. The grammar names a subject kind, a shape and a `by` (`PARENT_GRAMMAR`, `KIND_ASSIGNED_INTENTS`, `CODE_ASSIGNED_INTENTS` re-keyed), `DEFAULT_POINTS`, `TEAM_SEASON_POINTS`, the subject reading's intent sets (`PLAYER_INTENTS`, `TEAM_ONLY_INTENTS`, `PLAYER_REQUIRED_INTENTS`, ...), `COMPILED_INTENTS`, `CHART_INTENTS`, `TABLELESS_INTENTS` and `coverage.SOURCES` become predicates on the Reading's kind, shape and relation; what is left of `_settle` after the taggers is the Route's construction, so `router.py` and `Route` go, `parse.read_route` returns the Reading, and the reader takes the names index rather than a connection (`con_in_the_reader` 23 -> 0; the reader issues 0 statements). `Answer.intent` stays for the page as ONE table from the planned `PointShape` to the retired label, which Phase 4 deletes with the renderers (D3: the page renders by shape). Reader tests re-seated as question-to-Reading snapshot cases (`tests/query/test_router.py`'s 213 first), the 25-intent freeze retired with the intent | `router.py`, `Route`, `reading.intent`, the intent sets, the regexes outside `lexicon.py` (the regex ratchet 6 -> 0 and deleted), the intent freeze | the four populations; the readings compared through the projection one last time, then the projection deleted and the snapshot records the typed Reading |
| 5 | A live run closes the phase (`live_parser29`, graded blind against `live_parser28`), and an independent review of it, as Phases 1 and 2 had; the done table's Phase 3 rows at their measured values | | 167/175, 156/166 not lower |

Three rules hold throughout, and two are Phase 2's: a decline or a
refusal becoming a Cause is ONE enumerated commit (Jeff's rule, AGENTS.md
"Identical means identical"); a boundary that moves (`parse.read_route`
returning a Reading in step 4) is recorded in `tests/stage_calls.py` on
both sides first; and new, for the reader: **a tagger claims the span it
read, and a word claimed twice fails the reader** - which is what makes
"the reading and the query are the same without the word" a property the
Reading states rather than one the ledger has to probe for.

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
| `TEMPLATES` | 12 entries; 0 since Phase 2, step 6 (2026-10-05): `templates/` is deleted | 0, or only declared chart shapes |
| `present.py`, `adapt.py`, `check_scope`, `HONORED_SCOPING`, `STATED_SCOPING` | present; since 2026-10-05 all deleted but `STATED_SCOPING` (`compose/plan.py`, which Phase 3's cells replace) - and it is not the one scoping declaration left: 12 remain, frozen by module and name in `test_frozen_shapes.py` since 2026-10-05, of which 4 are the per-relation cell tables the roadmap keeps, 2 are `STATED_SCOPING` and its `with_without` row, and 6 are debt, each labeled with the step that owes it (measured 2026-10-05, the review) | deleted, and of the scoping declarations only the per-relation cell tables left |
| private imports from `templates/` into `compose/` | 91; 0 since step 6, and the ratchet deleted | 0 |
| compiled answers whose read is the compiler's SQL | 45 of 205; since step 6, 614 of the 628 recorded questions are answered (`"fast"`) through `compose.answer` from a planned query, and 264 of those 614 execute a compiled statement - 297 execute only hand-written statements moved whole behind `core.rows_of`/`core.values_of` (the season line, the team seasons, standings, NetPoints, shots) and raw reads, 33 execute none (measured 2026-10-05, the review, `harness/exec_census.py`) | every answer |
| subject readings per question | 3 (1 since 2026-10-01; settled once, with no stage run, since 2026-10-02) | 1 |
| stage runs per question | mean 1.7 (1.68 measured 2026-10-02, up to 4); 1.00 since 2026-10-02, twice on 4 of 628 where the stages declined the child | 1 |
| planner runs per question | 2 (1 since 2026-10-01) | 1 |
| functions outside the reader that take the question | 168; 24 by the ratchet's count when it was written (2026-10-02); 1 since 2026-10-09, Phase 3 step 0 (`history.RunHistory.write`, the trace writer) | 0 |
| modules that execute SQL | 21 when the ratchet first listed them (the audit's own count was 20); 19 since 2026-10-01, `parse` and `subject` no longer do - 103 statements, counted per module since 2026-10-02; 11 modules and 38 statements since Phase 2, step 6 by that count (`player_relation` 16, `conditions` 7, `entities` 4, `team_relation`, `compose.logs`, `compose.team` 2 each, `compose.core`, `compose.splits`, `connection`, `game_label`, `player_games` 1 each) - which was blind to a hand-written statement run through `core.rows_of`/`core.values_of`; counting those too (since 2026-10-05), 80 statements in 20 modules: the 38, and `compose.team_stats` 9, `compose.seasons` 7, `season_line` 7, `compose.periods` 4, `compose.standings` 4, `compose.netpoints`, `compose.shots`, `fingerprint` 3 each, `compose.team_records` 2 (measured 2026-10-05, the review) | `relations/` only |
| SQL statements the reader issues per question | about 100 (38.5 once the subject was read once; 2 since 2026-10-01, the index's two loads) | 0 |
| records between question and answer | 8 at the audit; 11 since Phase 1 added `Planned` (the planner's verdict, which the target's Query-or-cause absorbs), `Beside` (the companions the stages are handed) and `Route.subject` | 4 |
| result shapes, and web renderers | 25 and 22 | one per shape |
| unread content words over the corpus (`scripts/claims_ledger.py`) | 695 of 2,409 in 393 questions since Phase 2 (measured 2026-10-05, the review): "team"/"teams" in five team questions, which counted as read only because the declined point's reason sentence named a team when the word was there - they gained a point in slice (iv), whose planned query is the same without the word; 690 of 2,409 since the stages ran once (seven words had counted as read only through the decision records a second stage run wrote); 683 of 2,409 on the replies re-recorded on the OVH devbox (2026-10-02); on the old box's replies 684 of 2,415, and 679 before the companions' one reader, which no longer needs "play" to see who played; 9 questions with an unread number | not grown; no unread number |
| stage snapshots, questions and unit-test calls | baseline | identical at the stated tolerance |
| tests bound to deleted structures | about 1,170; after Phase 2 none bound to a template, a presenter or an adapter - every template's test re-seated on its reader or deleted, slice by slice (step 6: 3 deleted, 11 moved) | 0: deleted, or re-seated at a stage |
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
