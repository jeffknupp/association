# Roadmap

The north star, what each spike bought, and the next steps - kept so that a
spike stays pointed at the goal instead of dissolving into one-off fixes. The
living record of each spike (numbers, decisions, dead ends) is the plan doc
"Reaching 90% on Real NBA Questions"; this file is the overview. `ISSUES.md`
is the ranked list of what is wrong; this file is where it is going.

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

### How it is measured

Yardstick v2 (`~/association-research/yardstick-v2/`): 175 primary questions
(89 of Jeff's own, 86 starred StatMuse rows) in 166 families of wordings,
graded against a blind answer key computed from the warehouse by agents who
never saw the system's answers. A question is good when it is correct, or a
clarification/refusal that was genuinely required. A family passes only when
every wording is right. Every change that can move an answer is followed by
a live run of all 277 wordings (one ollama caller - two corrupt each other's
routing) and a hand grade of every row that moved.

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

Refactors are proved differently: a golden comparison over every recorded
slot set (~500 player-relation cases, ~110 team-relation cases, the
compiler's 387), watched to fail on a one-token change, must come back
identical except for the rows the change targets.

## What the spikes built, and why

The pipeline is router -> templates -> compiler -> agent. The router is a 3B
model at temperature 0 that names an intent and fills slots; everything after
it is deterministic. The spikes changed what sits between the router and the
SQL.

### The algebra spike (2026-09-19): the thesis, half right

**Why.** On 2026-09-18 four agents in parallel moved the v1 score from 39% to
49% by teaching templates slots they could already have honored, and the
work made the problem visible: scoping was implemented per template
(O(templates x slots) of hand work, three agents producing three merge
conflicts teaching `venue` to three templates), the router prompt was a
global mutable (one added line moves slots on unrelated questions), and
names were generated rather than selected. The path of more templates
asymptotes around 60-65% and produces the rules engine we did not want.

**What it tested.** Whether questions are compositions of a few orthogonal
dimensions - subject, measure, aggregation, filters, scope, output - over a
handful of SQL skeletons, and whether the model could emit that algebra
directly instead of an intent.

**What it found.** Four skeletons (rows, scalar, grouped, streak) express 50
of 50 hand-encoded questions and all but 11 of 2,057 real ones. A compiler
over one relation reproduced five templates' numbers exactly (103 / 103) and
found a template bug the templates could not see in themselves. The model
emitting the whole algebra lost 93 to 43 on the shared questions: the 3B can
fill filter slots reliably but cannot compose a query. **So the algebra is
the right internal representation and the wrong model output.** Nothing was
merged; the baseline stood.

**Two product decisions** came out of it and live in one place each: an
unscoped count ("how many times has X ...") is a career, a stat line and a
plain log default to this season; and "stats vs X" is averages over every
meeting in scope with the last few meetings beneath.

### Step 2 (2026-09-19, v4.3.0): one definition of "a player's games"

**Why.** Every template that read a player's games wrote its own join, its
own did-not-play guard, its own phantom-1993 exclusion and its own rebuilt-
line rule. A correction had to be patched in three places, and the places
disagreed.

**What changed.** `query/player_games.py` is the one definition - the
season-keyed join, the guards, the tenure rule behind `without` - with three
readers (rows, one aggregate, aggregates per group) and a pair reader.
`game_log`, `player_stat`, `threshold_count`, `single_game_high`,
`player_matchup` and the four condition templates compose it instead of
writing SQL. Each port was a pure refactor proved by golden comparison, then
the scoping slots the spike had identified (`since`, a single game, `below` /
`above`, the two product decisions) were added on the relation.

**What it bought.** One home for every correction to the read. What it did
not buy: scoping still lived per template, which is what step 3 was for.

### Step 3 (2026-09-21/22): scoping is a property of the relation

**Why.** After step 2 the nine player-relation templates honored 41 of 135
template x slot cells and refused 94, each slot wired by hand into each
template - the O(templates x slots) matrix, still growing.

**What changed.** C0 ruled every cell on paper: a row-level filter means the
same thing under every skeleton, so no cell needed a per-template meaning
(9 of 135 needed a skeleton rule, against a stop rule of a third). C1 built
one scope once - `scoped_player`, `scoped_games`, `condition_player` in
`templates/common.py`, slots in and one `Narrowed` out - and moved six
templates onto it, 478 / 478 identical. C2 made `RELATION_SCOPING` the one
declaration, with `RELATION_SCOPING_EXCLUDED` naming each refused cell and
why (a reason about the answer, never about the code), and two source-reading
gates that fail a template listing its own slots or writing its own
narrowing clause. C4 did the same for teams: `query/team_games.py` replaced
three disagreeing definitions of "a team's games" (one of them answered the
wrong year for every postseason before 1994). C5 put shots on the relation
and gave the window (`order` + `limit`, cut after every other filter) its one
home. K3-1 added the calendar (`query/calendar.py`: a weekday, a month, a
holiday, "since <day>") as one clause reaching every template.

**What it bought.** A new narrowing dimension is one clause on `Narrowed`
that reaches every template at once, and an answer built through the
relation states its scope by construction (`Narrowed.filters()` writes "vs
the Boston Celtics, at home, without Joel Embiid") - which is what makes
Jeff's default rule satisfiable. It moved the yardstick by one question, as
the plan predicted: step 3 was maintenance cost, paid so the next work is
cheap. It also found the recorded-slots golden is the only net that catches
"declared and then dropped on the way to the relation" - a lexical gate
cannot.

### The skeleton spike (2026-09-22): the compiler, landed

**Why.** With scoping solved, what still restricted the answerable space was
shape and measure: one router intent per shape, each needing a prompt line,
a schema entry and a template - and the router prompt cannot grow. The
fall-through agent answers 1 question in 23 and does not finish 61% of the
time, so coverage IS template coverage.

**What changed.** One compiler over the player-games relation
(`query/compose/`): a `Query` names skeleton, measures, aggregate, group,
predicates and window; the relation supplies subject and scoping through the
shared steps, so the compiler never narrows by hand (the C3 gates walk it).
`adapt` maps an intent and its slots to that intent's default point;
`move_point` moves the point by the question's own words - measure words
("ts%", "plus minus", "fouled out"), skeleton words ("most ... in a game",
"how many ... won"), a league-wide subject with a position filter - with
guards where a move produced a fluent wrong ranking (a team subject, a
period). K1 reproduced the six relation templates exactly (232 / 0, 237 / 0
after the calendar landed); K2 answered 8 of 31 fall-throughs, all right
against the key. It landed as the step between a template's refusal and the
agent: `agent.py` composes before it falls through, and a compose refusal is
an answer.

**What it bought.** Eleven questions on the day it landed (107 -> 118): a
shape or a measure the templates lack no longer needs an intent. The
extension is bounded by the dimensions the relation carries - which is why
the next work was parity.

### The sweep and team parity (2026-09-23)

**Why.** Sixteen wrong answers were untouched by everything above - not
scoping, not shape: the router read a phrase wrongly or dropped the subject,
and the team relation lacked what the player relation had.

**What changed.** By cause, not by question: "including playoffs" is both
season types (`season_type_unstated`, one read); a closed season range fills
`since` and `until` (`until` had been filed for "the 2010s" and honored
nowhere - a decade silently read as "since 2010"); "since he joined the
league" is a career; "for <team>" beside a player is his tenure; a subject the
question names is restored when the router drops it, and a player named on a
team-only intent is refused by name. The team relation gained `situation`
and `until`, the compiler gained the team as a subject (`compose/team.py`),
`game_log` states the total or differential asked for, and a span ranking
counts every franchise. Then `query/refusals.py`: after the template and the
compiler both decline, a shape the agent has no better source for either - a
playoff round, an age, a conference, a stat other than points by quarter, a
game log "vs" another player - is refused in seconds naming what is missing,
instead of costing the agent's minute.

**What it bought.** 118 -> 136 of 175; wrong answers 16 -> 5, wrong-cause
refusals 4 -> 0. And two shapes the yardstick could not have found alone:
the key itself was wrong twice (it counted a phantom season and blended
playoff games into a regular-season figure), caught by re-measuring every
merged agent's load-bearing number.

### The day after (2026-09-24): refusals, partials, compiler moves

**Why.** After the sweep, 25 of the 39 remaining failures were fall-throughs
to an agent that answers 1 in 23 - a minute's wait for a refusal or a
fabrication - and seven partials were answers missing the one figure asked
for.

**What changed.** `query/refusals.py`: after the template and the compiler
both decline, a shape nothing here reads is refused in seconds naming what
is missing (a playoff round, an age, a conference or division, a stat other
than points by quarter, a game log "vs" another player, a team where a
player belongs) - each one paired in tests with the template's own refusal
so it can never shadow an answer. The router reads "stats for the sixers
when maxey scored 20+" as the team's record under the condition, drops a
period template's filler window, reads a team's half by its nickname (the
last known routing gap, 131/131), and files `ranked_by` so "highest scoring
triple doubles" reaches the compiler, which ranks the games and names each
player. The templates state "last 10 of 39 games", a career line under a
per-season table, attempts and percentage beside a made count, the stat
asked for in a splits table, each player's team in a ranking, and each
half's first season in a combined record. The compiler ranks boolean games
by another measure, reads several lines at once, takes a position word as
the subject, and carries the box-score caveats.

**What it bought.** 136 -> 155 of 175, fall-throughs 25 -> 11. Eighteen of
the 155 are refusals graded good because they name the true cause of a real
gap - a fuller system would answer them, and the roadmap's plan is what
would.

### Day two (2026-09-24, later): the pair relation, division, and the compiler's subjects

**What changed.** `player_matchup` narrows the first player's games through
the shared step (a teammate's absence, a venue, a date, a starter half, a
calendar, stated in the heading) and a player filed as the `opponent` is
the second of two players - the pair relation is on the relation. A team's
conference and division per season (`team_alignment`, from a second
standings request; the first one's "games back" is conference-relative and
would have changed) narrow either relation. The compiler reads an ordinal
season over everyone, a team where a player belongs on a boolean stat (the
Thunder's 180 regular-season triple-doubles, by player), the router's
filler word as no player; three more data gaps refuse fast. Sweep 2's seven
router fixes landed as the experiment's winning branch.

**What it bought.** 155 -> 161 of 175 (92.0%), families 144 -> 150 (90.4%),
fall-throughs 11 -> 3. The first run past the 90% line - with 19 of the 161
still honest refusals. The remaining failures are four wrong (two need a
clarification the system does not ask, one is period data, one is the
router's division capture), five partial, three fall-throughs.

## Where the remaining failures are

On the rest run (`live_rest.jsonl`, 175 primaries): 4 wrong, 3 partial,
11 fall-throughs, 2 clarifications that should not have been asked, and 18
honest refusals that a fuller system would answer. By cause:

- **Shape and measure the compiler does not move to yet** - the highest-
  scoring triple-double, a multi-line league count, a position word in the
  player slot, a team's rate. The compiler's job; no new intent.
- **The pair relation** - two named players' games against each other, and a
  player's record against another player. No relation carries it.
- **Period data** - a player's quarter figures other than points, a team's
  quarter by opponent. The per-period rebuild from plays holds points only.
- **Subject kinds the router drops** - a team or position named where the
  model filed a player, or nothing. Repaired after the fact today; should be
  one decision.
- **Genuinely unanswerable** - an age (no birth dates), a division (in the
  standings, unparsed), awards, coaches. Refused fast now; a parsed division
  would answer a few.

## Next steps (the next working day)

1. **Sweep 2 - done, as the Sonnet-vs-Opus-low experiment.** Both arms
   ran the identical seven-fix prompt; the Opus-low branch merged (`637ff4e`)
   after scoring: same routing (137/138), two live wins and no losses for
   Opus-low, at 65% of the tokens and ~1.2-1.3x the dollars; the Sonnet arm
   produced the one fluent wrong answer. Rule from it: Opus-low for
   sweep-shaped work (grammars measured over the corpus, judgment about what
   ships); Sonnet for bounded harness tasks. A general-purpose agent
   inherits the session's effort - control it with an agent definition.
   Details: `~/association-research/agent_review_2026-09-24.md`.
2. **Conference and division from the standings - done** (`team_alignment`,
   1988-2026, loaded): "vs the southeast division" narrows either relation,
   and since 87fa6a1 the router keeps the division's name (#213), so the
   question as typed answers.
3. **Done 2026-09-24:** the eleven fall-throughs sorted (seven router-side
   -> sweep 2; three data gaps -> refused fast: a team's stat by quarter,
   bench points, an attempts floor; one compiler shape left); the pair
   relation as a template (above); "lebron vs kawhi head to head" answers.
4. **Done 2026-09-24 (later):** an ordinal season over everyone ("Most
   points in 15th season played"), a team where a player belongs on a
   boolean stat ("thunder all-time triple doubles": 180, by player), three
   more fast refusals (a team's stat by quarter, bench points, an attempts
   floor). **Left:** the Hornets' best first-quarter scorer (period data),
   two questions that need a clarification the system does not ask, the
   pair as a compiler subject.
5. **Done 2026-09-24/25 (the rendering pass):** every answer on the web
   page drawn from typed data - `headline` and `notes` on every template
   with a renderer, stable keys for derived stats (a 2PT% history's column
   was its SQL expression), typeset tables, folds past 10-12 rows, rates as
   percents, signed margins, chart-only answers with their sentence, a note
   box that corrects rather than appends. Measured on three galleries (277
   yardstick answers, the deploy's history, Jeff's 17-question session)
   through `scripts/preview_answers.py`: text moved on exactly the three
   answers it was meant to (a championship refusal, two NetPoints
   refusals that used to rank points). And #213: "vs southeast division"
   keeps its name through the router (routing 140/140) and answers the
   key's 8 - which exposed and fixed the compiler averaging a boolean.
   **Left from it, done 2026-09-25:** `player_netpoints` now has a renderer
   (a season-totals card plus offense/defense and play-type-detail tables)
   and `team_record`'s month/venue/career branches carry their own
   `data["headline"]` instead of relying on the page's first-line fallback;
   the card also draws the season branch's seed/streak/split/points fields
   that used to be lost outside `answer`/`text`. **Still left:** a NetPoints
   single-game ranking has no fast relation.
6. **2026-09-25, `live_day3.jsonl` on `8ac55f0`: 161/175 (92.0%) - the
   total unchanged, the composition moved** (141 correct + 18 honest
   refusals + 2 clarifications; F055 flipped refusal -> correct). Four
   rows moved, all still right; one wording regression filed (P4: the
   compiler's career span says "(1994 on)"). Then plan item 1 was
   MEASURED before any code: one reading of the subject from the
   question's own spans agrees with today's ten-step repair chain on
   279 of 290 recorded questions; of the eleven left, four are chain
   bugs the reading gets right (a compare whose second player the router
   filed as the opponent, the two position-group logs, F114), six are
   slot encodings of the same subject, one a kind neither side has ("a
   Hawks player"). Method, rules and the landing order:
   `~/association-research/subject-kinds/RESULT.md`.
7. **Steps 1-2 landed 2026-09-25** (`9b745a1`, `f7aa9fc`): `query/subject.py`
   (the reading, 278/290 against the chain and right on the five where the
   chain is wrong - F049 joined the four once the `team_players` kind
   existed) and `query/decisions.py` - **decisions as data**, Jeff's ask:
   every reading of the question and override of a routed field as a
   `Decision(stage, field, before, after, reason)` on the history record,
   the `Answer`, the API and the page's "decisions" fold, never parsed
   back out of the trace. The subject reading is the first producer and
   writes no slot yet: text identical on 56 rendered recorded answers.
   **Step 3a-3b landed** (`55e186a`, `1963d0f`): `apply_subject` writes
   `player`/`players` from the reading - an invented router name replaced
   by the question's own or refused by name, a kept name respelled to the
   question's resolved one. Golden over 308 recorded questions: 306
   identical each step (the two are same-date row order, master's own
   noise, filed P4); `override_invented_players` still runs after and now
   acts on 4 answers (a question's own typo resolved by near spelling, and
   the opponent half). **Step 3c landed** (2026-09-25, later): the reading
   takes both - spellings from the anchored span resolver (a question's own
   typo: "Seph Curry" is Seth), a player filed as `opponent` as a routed
   name - and `override_invented_players` is deleted with its helpers;
   golden 306/308 again, the reading making all four writes itself. Found
   and fixed on the way: 3b's respelling from the whole-word match rewrote
   "kareem stats vs bob lanier" to Kareem Rush (ISSUES #123's shape, shipped
   in 4.4.0, not in the corpus - the chain's own span tests caught it once
   re-homed onto the reading). **Step 3d landed** (2026-09-25, later, in
   three increments - `1f585f2`, `0c4a2c3`, `0ba3526`): the reading writes
   the opponent team, the restored player, the own team, the team subject,
   a player filed in `team` and a team that displaced the player, and
   settles the INTENT where the router's cannot be about the subject
   (`Subject.intent`: a player's record vs a team is `with_without`; a
   pair is `player_matchup`, or `player_compare` where the question
   compares). `entities.scope_from_question`, `player_record_against_a_team`
   and `refusals.pair_from_opponent` are deleted; the fast path is
   nicknames -> reading -> a fingerprint's dropped players -> the reading
   applied -> name completion undone -> the team-only refusal. Golden
   306/308, 307/308, then 304/308 with the two moves being the chain bugs
   the step was written to reach: F114 answers the pair's meetings without
   Durant (not the Rockets' record), Brown/Tatum refuses by name for
   `since` (not a fall-through). Found on the way: the chain's unit tests
   are the second golden (they caught the 4.4.0 kareem regression, a rule
   no recorded question exercises). **Step 3e landed** (`91d1461`): the
   compiler (`move_point`/`repair`/`team_move_point`/`answer`) and
   `refusals.unanswerable` take the Subject the agent read; the position
   group, the filler word, the team in `player`, the dropped subject, the
   team the router left out and the position all come off it. 308/308.
   **Plan item 1 is closed**: one reading decides the subject, nothing in
   the query path derives it twice. Of the five chain bugs the measurement
   found, four answer or refuse honestly; F049 needs `period_leaderboard`
   to take a team (plan item 4). **Next:** a day4 live yardstick run to
   measure what steps 1-3e bought; then plan item 3 (the player condition
   `(player, side, predicate)` - F114's remainder) or plan item 2.

## The current plan: what buys the most correctness next

In order of lift per unit of structural change, each measured on the
yardstick before the next starts:

1. **Subject kinds, decided once - done 2026-09-25** (`query/subject.py`,
   steps 1-3e above). A question's subject is a player, a pair, a team, two
   teams, a position group, a team's players, or everyone; one reading
   from the question's own words before any template runs files the kind
   and the names, writes the slots the templates read, settles the intent
   where the router's cannot be about the subject, and refuses by name
   where nothing in the question can replace a router invention. The
   repair chain (`scope_from_question` and nine siblings), the two
   reroutes, and the compiler's and refusals' own re-derivations are gone.
   The "names are selected, not generated" tension from the first plan is
   closed by construction: every name a template reads is a span of the
   question or a router name the question supports.
2. **Skeleton x measure over both relations, in the compiler, and the intent
   enum shrinks.** The compiler already answers rows / scalar / grouped over
   two relations; each template it reproduces exactly is one the router no
   longer needs an intent for. Fewer intents means a shorter prompt, and the
   prompt is the router's ceiling. `CODE_ASSIGNED_INTENTS` is the route for
   any shape the question's own words name.
3. **The pair relation.** Done as a template, 2026-09-24: `player_matchup`
   narrows the first player's games through the shared step, so a
   teammate's absence, a venue, a date, a starter half and a calendar are
   honored and stated; a player filed as the `opponent` is the second of
   two players (`refusals.pair_from_opponent`). What remains is the pair as
   a compiler subject - measures and predicates over the meetings ("most
   points by curry vs lebron", "how many times did lebron score 30 vs
   kawhi") - which no template answers. **And its generalization** (Jeff,
   2026-09-25: "sixers record when embiid, maxey and edgecombe start"): a
   player CONDITION is `(player, side, predicate)` - own team or opponent;
   played / started / absent / scored 30+ - and either relation narrowed by
   ALL of a list of them. `with_without`'s `with_player`/`without` lists
   are the two-predicate special case; F114 "curry record vs lebron
   without kd" is Curry's games with two conditions (LeBron on the other
   side, Durant absent from his own), which is why the chain lost it. The
   subject reading's `companions` are these names with their stated role,
   so this follows subject kinds directly. The true N-way matchup (three
   players on court across teams) is an N-way event join and stays
   two-sided.
4. **The period relation.** Per-quarter figures beyond points, rebuilt from
   plays the way points are, as one relation the compiler can read - a
   player's or a team's quarter as a narrowing rather than three templates.
5. **Conference and division** from the standings we already fetch - the
   last unanswerable narrowing that is answerable in principle. **Done
   2026-09-25** end to end (`team_alignment`, the relations' shared step,
   the router's capture): "vs southeast division" answers as typed.

Not on the list: a bigger router (measured: does not fix names, and the
latency fear was overstated), improving the agent fall-through (an agent
with nothing to read fills the silence from its own weights), and more data
(every structural gap added up comes to under 14% of questions).

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
