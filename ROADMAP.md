# Roadmap

Where the work is going, in order. `ISSUES.md` is the ranked list of what is
wrong now; `ROADMAP-HISTORY.md` is how the project got here - what each step
measured, bought and cost, as written at the time. Code and commit messages
cite the plan by item ("plan item 6, step (d), part 3"); the numbers are
stable, and each item's status is under "The plan items" below.

## The goal

**A correct answer to every reasonable question of at least mild complexity,
or an honest refusal that names what is missing - fast, from the warehouse,
never from a model's weights.** "Reasonable" is the corpus's own term: a
question a careful human could answer from box scores, play-by-play, shot
charts, standings and NetPoints. 90% of those is the target.

Two properties are not negotiable on the way there:

- **Refuse rather than approximate.** A fluent answer to a different question
  than the one asked is the failure shape this project produces; a refusal
  that names the wrong cause is its mirror. Both are graded worse than a
  fall-through.
- **A reasonable default beats a question, if the value used is displayed and
  a follow-up wording reaches the alternative.** (Jeff, 2026-09-21.) A silent
  default is still the worst failure; a stated one is an answer.

## Where it stands (2026-09-29, `5e72747`)

**165 of 175 questions (94.3%), 154 of 166 families (92.8%)** on the live
yardstick run (parser13, the period relation merged and F062 refused by
name): 2 wrong, 5 partial, 3 fall-throughs.

The pipeline: a 3B model (qwen2.5:3b) copies the names out of the question
and picks one stat key (`query/normalizer.py`); the parser reads everything
else from the question's own words and writes one typed `Reading` - who the
question is about, the intent, the scope (`query/parse.py`,
`query/reading.py`); a template or the compiler (`query/compose`) answers
the Reading, or a refusal names what is missing (`query/refusals.py`). The
SQL-writing agent is the last resort, and a poor one - it answered 1
question in 23 when last measured. Seven intents are the compiler's alone
(`compose.COMPILED_INTENTS`); `game_log`, `player_stat` and `player_splits`
joined them in step (g) (parser9-11, 2026-09-28/29: 162/175, 0 answers
moved).

The 13 families still failing, by cause:

- **A period as a condition** (plan item 4, what is left). "vj edgecombe
  three points made per game after making one three in first quarter"
  (F062) is refused by name: nothing keeps the games where a period held a
  line (#275). A team's non-points figure per quarter is measured and not
  built (#161).
- **A span or a window the parser does not read.** "... games since
  2000-01" (F161) answers one season; "Most reb by a hawk player history"
  (F125) ranks 2026 alone; "Best NBA record since January 31st 201" (F104)
  falls through, since a team ranking cannot take a date; "bam adebayo
  career games in the month of march" (F096) counts his 118 March games
  right but lists the last 10 under a heading that says "2026 regular
  season".
- **A question that needs asking back.** "Tatum rec" (F112) and "25-26
  knicks playoff statistics vs other historic teams" (F097) get a guess
  where the key wants a clarification.
- **Two active players named Curry.** "Plot Curry's threes from last season"
  (F003) falls through, and a wording of F002 ("How far was Curry average
  three pointer?") asks Seth or Stephen. The rule is to ask when two
  namesakes both played (AGENTS.md); the key reads Curry as Stephen.
- **One each.** "rebounds allowed per team" (F101): no team metric holds it.
  "towns home rec including playoffs since 1/26/20 vs spurs" (F110): 11
  games where the key counts 10. "steph curry record vs lebron regular
  season without kd" (F114): the key reads "without KD" as every game Durant
  was not his teammate. A wording of F017 ("who are the top 50 in total
  adjusted netpoints"): the 50 rows and each player's team.

## Next, in order

1. **The templates the compiler can reproduce go** (item 6, step (g); item
   2). `game_log`, `player_stat` and `player_splits` are gone (2026-09-28
   and -29): the log's and the splits' team halves are points on the team
   relation said by the templates' own readers; 35/35, 42/42 and 5/5
   recorded questions at parity or better once three compiler readings the
   measurement found were fixed (a date replacing the season, the team
   window sum over both season types, a threshold on a log). Live after
   `game_log` and `player_stat` (parser9, parser10): 162/175, 0 moved. What
   is left on the player relation: `streak`, `with_without`,
   `player_matchup`, `period_split`, `leaderboard`, `player_compare` - each
   a shape of its own or a reader the compiler lacks; measure the same way
   before retiring any. A template
   survives only for a shape of its own: a chart, a fingerprint, a streak, a
   matchup, a quarter. How to retire one is part 4's method: every call its
   unit tests make and every recorded question it answers, answered both
   ways and compared (`~/association-research/intent-shrink/g/`: a pytest
   plugin records the unit-test calls; `parity_corpus.py` the recorded
   questions; `compare_trees.py` the before-and-after) - the recorded
   questions alone found one shape the template still carried, the unit
   tests five.
2. **The period relation - the team half** (item 4). The player half is
   done (2026-09-29, an Opus agent's branch merged at `621ab51`): a quarter
   or half narrows the player-games relation (`Narrowed.narrow_periods`),
   its line rebuilt from the shots and the plays (`player_games.period_line_sql`)
   and validated per season and per column against the box score
   (`PERIOD_AGREEMENT`, `scripts/check_period_lines.py`: 375/375 cells;
   from 2007 on every column agrees in 99%+ of player-games but a few
   seasons carried as caveats, and 2002-2005 assists/steals/blocks are
   refused under 90%). F048, F049, F060 and F066-F068 answer. What is left:
   a team's per-quarter figure beyond points (#161, a period narrowing on
   `TeamNarrowed` summing the players' lines and the team's no-athlete
   plays), a named player's four-quarter breakdown (#162), shooting
   percentages in a period, a period as a condition (#275), and
   `period_leaderboard` narrowed by opponent, venue, date or a range (#185).
3. **The rest of the pair relation** (item 3). The pair as a compiler
   subject ("most points by curry vs lebron", "how many times did lebron
   score 30 vs kawhi"), and the opponent-side condition ("vs lakers without
   lebron": the relation reads `side="opponent"`, and nothing writes it).
4. **Re-plan from what is still failing**, after a live run.

Step 1 finishes the parser consolidation (item 6) and should move no
answers: it is proved by golden and the rehearsal, then a live run. Steps
2 and 3 are new capability, each measured on the yardstick.

**Waiting on a decision (Jeff's):**

- **Which season type a question that names none reads.** Today it is the
  regular season, except a "last N games" window, which reads both - and
  the grading scores it that way for now. The rule on the table: records,
  lists and counts read both season types, averages the regular season
  (#231's scope half: "Embiid's record against Boston this year").
- **Whether a short ambiguous question asks back or defaults visibly.**
  F112 ("Tatum rec") and F097 are graded wrong for guessing.

**Not planned, and why:** a bigger model (the 7B added no names and 13 of
162 stats, for a second a question more); a better fall-through agent (an
agent with nothing to read fills the silence from its own weights); more
data (every structural gap added up is under 14% of questions).

## The plan items

The numbered items code and commits cite, with their status. Each one's full
text as written is the last section of `ROADMAP-HISTORY.md`.

1. **Subject kinds, decided once - done 2026-09-25.** One reading of who a
   question is about (`query/subject.py`) - a player, a pair, a team, two
   teams, a position group, a team's players, everyone - from its own words.
2. **Skeleton x measure in the compiler, and the intent set shrinks -
   partly done.** Seven child intents are assigned from the words (2c: the
   router's intents went from 23 to 16, before the router itself went); the
   compiler reproduced four templates exactly (2a) and they retired
   (item 6, part 4). What is left is item 6, step (g) - next step 1.
3. **The pair relation and the player condition - done but for next step
   3.** `player_matchup` reads the pair on the relation (2026-09-24); a
   condition `(player, side, predicate)` narrows either relation, with each
   companion's role read off the question (2026-09-26).
4. **The period relation - the player half done 2026-09-29** (next step 2 is the team half).
5. **Conference and division - done 2026-09-25** (`team_alignment`).
6. **The parser: one Reading, one writer of slots - in progress.** The
   model copies names and picks a stat; the parser writes everything else as
   one typed Reading.
   - (a) the Reading, and the planner in front of the compiler - done
     (`43eca0c`).
   - (b) the grammar tables and (c) the normalizer in place of the router's
     prompt - done (`6e4e4aa`, `2b2e9d3` .. `fe53c72`; live 162/175 on
     `efef90d`).
   - (d) one writer. Part 1, the typed `Scope` and `Reading` (`1f344e4`);
     part 2, every template reads the Scope (`3aaa755` .. `36d68a6`); part
     3, the router reader and the repair chain deleted and the parser
     writing the Reading the agent consumes (`1240612`, `0e649ae`,
     `33cfd60`, `4406133`, `fd88e48`; live 162/175, no answer moved) - done.
     Part 4, the four templates the compiler answered first retired - the
     compiler answers `threshold_count`, `single_game_high`, `record_when`
     and `player_history` alone, in their words (`compose.COMPILED_INTENTS`,
     `4b3aff9`, `8c808b7`; live 162/175, no answer moved) - done.
   - (e) the compiler's reading of the question moved into the parser -
     done. The parser reads the compiler's point once and the Reading
     carries it (`Reading.point`); the compiler plans and runs the point it
     is handed (`compose.answer`) and repairs no slot; a position
     group is a subject, never a player; and the intent the words assign is
     a recorded decision (#258) (`1702db8`, `31c87f9`, `0ede991`; live
     162/175, no answer moved). The word tables still live beside the
     compiler (`compose.move.read_point`), and the parser is their one
     caller on the live path.
   - (f) the planner refuses from the Reading, and nothing after the model
     holds a slot dict - done. `compose.answer(ctx, reading)` is the
     compiler's one door and the refusals read the Reading (`ef0c6f0`);
     `plan.plan` refuses a narrowing the relation cannot honor as the parser
     plans the point, the four compiled intents' presenters declare what
     their words state (`compose.present.STATED_SCOPING`) in place of
     `HONORED_SCOPING`, and a compiled question falls through with the
     compiler's own reason (`2df8754`); `Route` carries the typed Scope the
     stages return (`86adde7`), and the subject reading takes and returns it
     (`33a1d3f`). Live 162/175, no answer moved. `check_scope` remains the
     gate for the templates that remain, and goes with them in (g).
   - (g) the templates the compiler can reproduce go - next step 1.

## How it is measured

Yardstick v2 (`~/association-research/yardstick-v2/`): 175 primary questions
(89 of Jeff's own, 86 starred StatMuse rows) in 166 families of wordings,
graded against a blind answer key computed from the warehouse by agents who
never saw the system's answers. A question is good when it is correct, or a
clarification or refusal that was genuinely required; a family passes only
when every wording is right. The season type is graded as the regular-season
default until the decision above is made.

A change is checked by four nets, cheapest first:

- **The tests** (`pytest`, offline, no model): a case per wording a grammar
  table gains, watched to fail.
- **The rehearsal** (`rehearsal_all.py`): all 628 questions with a recorded
  model reply - the 277 yardstick wordings, 75 more from the recorded
  corpus, 276 paraphrases nothing was tuned on - through the whole agent
  with no model. It reproduces a live run exactly, so it is the loop.
- **Golden** (`~/association-research/golden/`): recorded routes replayed
  through everything after the parser - the 631 the router recorded (v1)
  and the 628 the parser recorded (v2) - every answer compared. A refactor
  must come back identical, and the comparison is watched to fail on a
  one-token change.
- **The live run** (`run_live.py`, graded with `score_blind.py`): all 277
  wordings with the model, one ollama caller at a time; every row that moved
  is graded by hand. This is the record.

| when | build | primary questions | families |
| --- | --- | --- | --- |
| 2026-09-18, before the spike (v1 corpus, 217 StatMuse rows) | | 39.2% -> 48.8% | |
| 2026-09-21, yardstick v2 first score | `50c1faa` | 99 / 175 (56.6%) | |
| step 3 C1-C3 | `c64c2f2` | 106 / 175 | 96 / 166 |
| step 3 C4-C5 | | 107 / 175 (61.1%) | 97 / 166 |
| the compiler landed | `7fce95a`+ | 118 / 175 (67.4%) | 108 / 166 |
| sweep + team parity | `43f242f` | 136 / 175 (77.7%) | 125 / 166 (75.3%) |
| fast refusals, the partials, compiler moves | `08482db` | 155 / 175 (88.6%) - 135 correct, 18 honest refusals, 2 required clarifications | 144 / 166 (86.7%) |
| pair relation, division, compiler moves, sweep 2 | `a7b902a` | **161 / 175 (92.0%)** - 140 correct, 19 honest refusals, 2 required clarifications | **150 / 166 (90.4%)** |
| subject kinds, the intent set 23 -> 16, the player condition (days 3-10) | `8ac55f0` .. `bcf30a8` | 161 / 175 (92.0%); 160 / 175 after F088's re-grade | 150 / 166 |
| the parser reads the question (item 6, step c) | `efef90d` | 162 / 175 (92.6%) | 151 / 166 (91.0%) |
| one writer: the router and the repair chain deleted (item 6, step d, part 3) | `a884272` | 162 / 175 (92.6%), no answer moved | 151 / 166 (91.0%) |
| four templates retired: the compiler answers them alone (item 6, step d, part 4) | `8c808b7` | 162 / 175 (92.6%), no answer moved | 151 / 166 (91.0%) |
| the compiler's reading moved into the parser (item 6, step e) | `0ede991` | 162 / 175 (92.6%), no answer moved | 151 / 166 (91.0%) |
| the planner refuses from the Reading; no slot dict after the model (item 6, step f), and #259-#261 | `33a1d3f` | 162 / 175 (92.6%), no answer moved | 151 / 166 (91.0%) |

## How it got here

One line each; `ROADMAP-HISTORY.md` has the why, the numbers and the dead
ends.

- **2026-09-19, the algebra spike.** Questions are compositions of a few
  dimensions over four skeletons; the algebra is the right internal
  representation and the wrong model output.
- **2026-09-19, one definition of a player's games** (v4.3.0,
  `query/player_games.py`).
- **2026-09-21/22, scoping is a property of the relation** - for a
  player's games, a team's and shots, declared once (`RELATION_SCOPING`).
- **2026-09-22, the compiler** (`query/compose/`), between a template's
  refusal and the agent: 107 -> 118.
- **2026-09-23/24, the sweep, team parity and fast refusals**
  (`query/refusals.py`), then the pair relation, conference and division:
  118 -> 161 (92.0%).
- **2026-09-25/26, one reading of the subject** (item 1), decisions as data,
  child intents from the words (item 2c) and the player condition (item 3):
  161, the same total on a shorter prompt.
- **2026-09-26, the architecture review:** the pipeline was back in
  rules-engine territory (five writers of the same slots after the model),
  so item 6 - one parser, one Reading - went next.
- **2026-09-27, item 6 (a)-(c):** the Reading, the grammar tables, and the
  model reduced to names and a stat: 162 (92.6%), twice as fast.
- **2026-09-27, item 6 (d) parts 1-3:** the typed Scope; the router and the
  repair chain deleted; the parser writes the Reading, and the agent only
  consumes it.
- **2026-09-27, item 6 (d) part 4:** four templates retired - the compiler
  answers `threshold_count`, `single_game_high`, `record_when` and
  `player_history` alone, in their words.
- **2026-09-27, item 6 (e):** the compiler's reading of the question moved
  into the parser - the Reading carries the compiler's point, the compiler
  repairs no slot, and the intent the words assign is a decision: 162, no
  answer moved.
- **2026-09-28, item 6 (f):** the compiler and the refusals take the
  Reading, the planner refuses from it, and the typed Scope runs from the
  stages to the answer - no slot dict after the model: 162, no answer moved.

## The rules a spike keeps

- Measure first; grade every moved row against the key; re-measure a merged
  agent's load-bearing number yourself.
- A refactor is proved by golden comparison, a behavior change by the
  yardstick; a gate is watched to fail before it counts.
- One ollama caller at a time. Never chain a gate with `;`.
- Contained fixes go to agents in worktrees with disjoint files; the lead
  keeps the structural change, and this file says which it is. Opus-low for
  sweep-shaped work, Sonnet for bounded harness tasks (measured 2026-09-24);
  every agent takes its before-golden from its own worktree's package and
  says which copy it read.
