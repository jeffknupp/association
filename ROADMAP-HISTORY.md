# Roadmap history

How the project got to where `ROADMAP.md` starts: what each spike measured,
bought and cost, the working days that carried the plan out, and the plan
items' full text - kept as written at the time, so the reasoning behind a
decision can be found again. `ROADMAP.md` is where the work is going. The
sections up to "The plan items" are frozen as written; "The rewrite's
working log" is added to as each step of the current roadmap lands, so what
a step measured, moved and cost survives the roadmap's own status tables
being rewritten. The living record of each spike (numbers, decisions, dead
ends) is the plan doc "Reaching 90% on Real NBA Questions", and the research
runs are in `~/association-research/`.

Moved out of `ROADMAP.md` unedited on 2026-09-27. Code and commit messages
cite the plan items by number ("plan item 6, step (d), part 3"); the items'
full text is the last section here, and their current status is in
`ROADMAP.md`.

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

## Where the failures were (the rest run, 2026-09-24)

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

## The working-day log, 2026-09-24 to 2026-09-26

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
8. **2026-09-25, `live_day4.jsonl` on `91d1461`: 161/175 (92.0%), families
   150/166 - the total unchanged, as a structural step should leave it;
   wrong 4 -> 3, partial 5 -> 6** (F114 from the Rockets' with/without
   record to Curry-vs-LeBron meetings, partial for the default season and
   an unsaid empty tenure). Seven rows moved, five of them the career-span
   rewording (33d3315), all still right. Then plan item 2 was MEASURED
   before any code (`~/association-research/intent-shrink/RESULT.md`):
   folding a child intent into a parent through the pipeline is a dead end
   (the parent's template answers first, fluently, its own question); the
   compiler alone reproduces the child's NUMBERS under its own intent
   (K1 holds) but not its presentation, and does not recover the skeleton
   from the words for a named subject under a parent. The route that
   shrinks the prompt without a compiler port is the roadmap's own
   `CODE_ASSIGNED_INTENTS`: seven word grammars gated on the subject
   reading's kind reach 83-100% recall and **zero false positives** on 352
   recorded (question, intent) pairs. **Next:** the grammars into the
   reading's `_decide_intent` (a no-op while the children stay in the
   enum - golden-neutral), then one schema/prompt edit removing the group,
   `check_routing` and a live run - the first prompt shrink.
9. **2026-09-25, plan item 2 step 2c-i landed: the seven children are
   assigned from the question's words, gated on the subject's kind**
   (`subject.KIND_ASSIGNED_INTENTS`, `_CHILD_GRAMMARS`), their slots from
   the router's own stages run again under the child (`router.settle`,
   which `route()` now shares) - so the threshold, the seasons count, a
   streak's kind and a split have one reader, not one per child. Measured
   (`~/association-research/intent-shrink/RESULT.md`): 0 false positives
   on 261 non-child recorded rows, 83-100% recall per child across every
   parent it might arrive as, `settle` idempotent on 271/277 live rows (the
   rest code drift), golden 308/308 unchanged - a no-op while the children
   stay in the enum, by construction.
10. **2026-09-25, 2c-ii: the seven children left `ROUTER_PROMPT` and
   `ROUTER_SCHEMA`** - 23 intents to 16, 9,989 to 7,852 characters, ~2,500
   to ~1,960 tokens (prompt sha256 `8c6dd731...` to `b24de459...`, schema
   `2bc79a93...` to `b44332c0...`). The first prompt edit since the
   warning above, and it moved slots exactly as warned: `check_routing`
   came back 129/140, with the model now filing a quarter question as the
   team's (#170's shape again), "who attempted the most" as `player_stat`,
   a one-player `player_compare`, "td3s" as a shot chart, a streak under
   `team_outlook` and a team's "how many ... made" total as `team_stat`'s
   per-game line - each fixed by a text rule in `route()` or a parent
   added to a grammar (`_names_a_period_subject`, `_WHO_RANKS`,
   `_route_one_player_intents`, `_DRAW_WORDS`, `_route_team_total` with
   `team_stat` stepping aside for the compiler's season total), none by
   touching the prompt again; 140/140 after, golden 308/308, no recorded
   route moves. **Next:** the live yardstick run (day5) on this tree, which
   grades which parent the 3B model actually picks for each child's
   wordings.
11. **2026-09-25, day5 on the 2c-ii tree, three passes (e4b0abe, 95a7603,
   e8c68ba; `~/association-research/intent-shrink/RESULT.md`).** The
   children reach their templates through the parents the model now
   picks (0 false positives on recorded routes at every pass, golden
   308/308, `check_routing` 140/140), and nineteen text rules met the
   model's new habits under the shorter prompt without touching it again
   (`Subject.filler`, `_route_one_player_intents`, `_route_team_total`,
   `_names_a_period_subject`, the invented opponent, ...). What remains is
   drift the reading cannot see - a `since` comparison filed as
   `player_compare`, a differential as `with_without`, a `limit: 1` on
   "which team"/"game 4" - filed as one ISSUES.md entry with a text rule
   each as the next step, and a compiler usage-rate bug the drift exposed
   (P1). The day5 score with those rows graded is below day4; 2c-ii stays
   on the `subject-kinds` branch until that batch lands and a fourth run
   grades it - master holds 2c-i (e8a255c), which is golden-neutral.
12. **2026-09-26, days 6-8: back to 161/175 (92.0%), families 150/166 -
   day4's numbers exactly, on the 16-intent prompt - and merged to master
   (c34609e).** Three more passes, each moving only the rows its rules
   targeted (6, then 3, then 1 against the run before): the drift batch
   (`_route_pair_over_seasons`, a series game's filler order, a team log
   for "the last N games" with no teammate, ties at a ranking's cut, the
   differential under the model's spellings, "including playoffs" vs a
   team as a career, a team's triple-double total refused for its cause),
   and step 2a from an Opus agent in parallel: `compose/present.py` says
   `threshold_count`, `single_game_high` and `record_when` in their
   templates' words (18/18, 10/10, 10/10 on the recorded corpus,
   `~/association-research/intent-shrink/parity.py`), `game_log` for the
   player subject (19/26), narrowed `player_stat` (6/29); `player_history`
   and unnarrowed `player_stat` read the season line, a second relation,
   not a presenter (#228). No template deleted yet. What the shrink cost
   and bought, net: the prompt is 21% shorter, the router's own drift is
   met by nineteen text rules plus seven, F130 and "how far away was
   Stephen Curry shooting from" answer where they did not, and the two
   rows still below day4 are the `check_routing` GAP case (F142) against
   F130's gain. **Next:** plan item 3 (the player condition), or 2a's
   remainder (the season line as a relation) - and a merge-conflict gate
   is the 17th hook, after a merge landed markers twice.

13. **2026-09-26, plan item 3 (the player condition) landed and merged
   (29fc1b4, then bcf30a8): day9 161/175 (92.0%), families 150/166; day10
   161/175, families 151/166 (91.0%).** A condition is
   `player_games.Condition(player, side, predicate, line, tenure)` -
   played / absent / started / bench / reached - and the relation is
   narrowed by all of a list of them through the shared step
   (`Narrowed.conditions`; `without` and `tenure` are views of it). The
   reading gives every companion his role off the phrase's own words
   (`Subject.conditions`, `Companion(name, predicate, stat, threshold)`),
   `apply_subject` writes the roles the router's slots cannot carry as the
   `conditions` slot where the template honors it, a team subject with a
   reached companion is `record_when` under any of seven router intents,
   `with_without` splits by each teammate's own predicate ("celtics
   record when tatum starts"), a pair's "record" with no season spans
   their careers, and a matchup an absence empties says what it counted
   (F114 "steph curry record vs lebron regular season without kd": 27
   meetings, 4 with Durant beside Curry, none he missed as a teammate -
   still partial, since the key reads "without KD" as every game Durant
   was not on Curry's team). The last half of F087 ("show me splits for
   the sixers when maxey scores 20+ points") needed one more rule: a
   companion the router named nobody for is read from the phrase's own
   leading word, in the question's spelling, where it is a whole word of
   some player's name and not an ordinary word (`_unrouted_companions`) -
   one recorded route of 277 moved, F087's, and day10 confirmed it live.
   **Re-graded the same evening:** F088 ("Embiid's record against Boston
   this year", five wordings) is wrong, not correct - a with/without split
   of the 76ers over the regular season, where the question is his own
   games and "this year" includes the playoffs (key 4-2 in the 6 he played);
   the blind grader had passed it for stating its scope. Day10 is
   **160/175 (91.4%), families 150/166** after the re-grade. ISSUES.md has
   the entry; it is the first case for the parser (the reroute rule is the
   wrong shape; the unstated-season-type default is a policy to settle).
   Not done: an opponent-side condition from the reading ("vs lakers
   without lebron" - the relation reads `side="opponent"`, nothing writes
   it), the N-way matchup. **Next:** plan item 4 (the period relation),
   then re-plan from the fourteen families still not good (the quarter
   questions F048/F049, the two active "Curry" namesakes F002/F003, the
   no-valid-reading refusals F097/F112/F104, the singles).

14. **2026-09-26, the architecture review: are we back in rules-engine
   territory? Measured yes, and measured what to do about it.** Jeff's
   worry: the router picks a parent intent and a growing post-processing
   pipeline corrects it, and agents cannot say where a value came from.
   Counted on day10: the final intent is the router's on 72% of rows (66 of
   the 78 moves are the seven children, by design); code rewrites a slot on
   91/277 rows; ~70 post-model rewrite points across five writers (settle's
   26 stages and 96 regexes, the reading, `apply_subject`'s 14 steps, the
   leftover repairs, the compiler's `move_point`), against ~40 across three
   before the compiler (2026-09-22). Withholding the router on the 277
   recorded routes (`~/association-research/router-withheld/RESULT.md`):
   intent only 95/277 answers reproduced; intent + its names/stat 223; a
   40-rule kind+words grammar for the intent with the router's slots 254.
   Greenfield measurements (`~/association-research/parser-greenfield/RESULT.md`):
   the 3B at a names+stat-only job copies names verbatim 299/302, ~97%
   recall, 0.8s; the 7B adds nothing on names, +13/162 on the stat (the
   2pt/3pt confusion), +1s - the 3B keeps the job and the netpoints family
   goes in the measure table; a no-router pipeline built from today's
   settle stages reproduces only 167/277, and the gap is five nameable
   things (window words, the measure vocabulary, typo policy, name-vs-team
   by context, per-intent slot shapes); on 277 faithful 7B paraphrases the
   deterministic parse holds ~89% (kind 250, parent 231 raw; 50
   disagreements read by hand), ~93% with the possessive-name fix. The
   literature says this is the known shape - PRECISE's semantically
   tractable questions (Popescu, Etzioni, Kautz, IUI 2003), the Overnight
   grammar (Wang, Berant, Liang, ACL 2015), grammar-constrained decoding
   for small models (Geng et al., EMNLP 2023), Tableau's Eviza/Ask Data
   compiling to an intermediate language. The walkthrough page (three
   pipelines, four questions):
   https://claude.ai/artifact/XQCA4A4knDotv6KAndnqML. Grading note from
   the same review: the blind grader passes an answer that states a
   narrower scope than the question (F088), so a stated narrower scope is
   partial at best from now on. **Decision: the parser consolidation is
   plan item 6, and next.** Deferred product change, Jeff's: a bare "this
   year" (and stats generally) reading the playoffs in by default.

## The rewrite's working log, 2026-09-30 on

The roadmap these entries belong to is `ROADMAP.md` (accepted 2026-09-30).
The one before it is `ROADMAP-2026-09.md`, archived with a banner saying
where it ended and which two of its statements were wrong. Baselines and
agents' reports named here are in `~/association-research/stages/`.

1. **The audit that ended the last roadmap (2026-09-30).** Jeff asked for
   an honest look at what had been built. The last live run, parser22,
   scored 167 of 175 questions (95.4%) and 156 of 166 families - the score
   was good and the architecture half-ported. Replaying its 277 questions
   with every presenter, compile and SQL execution instrumented: of 205
   answers by a "compiled" intent the compiler's own SQL read 45 and its
   own sentence worded 16; 55 compiled a query and discarded it; 94 never
   compiled one. `query/` had grown 4,748 lines in five days while nine
   templates "retired". Two independent Opus reviews corrected the lead's
   numbers (the first draft said the compiler read 16) and its diagnosis:
   two vocabularies for one question, a reader that depends on the answer
   side, `intent` as context inside the reader, no common result. Cost:
   the lead's first count was wrong and would have gone into the roadmap
   unreviewed.
2. **The new roadmap (2026-09-30).** READ -> PLAN -> RUN -> SAY, six
   contracts, phases 0 to 4. Jeff's decisions: wording may change (D1),
   the answer side before the reader (D2), `intent` goes entirely (D3),
   new shapes frozen (D4), charts declared, notes typed, unclaimed words
   measured first, tests retired with what they test. Open: the season
   type a question that names none reads.
3. **The October 1 rollover, found by a reviewer and fixed the day
   before (2026-09-30, `d00512f`).** From October 1 the calendar's season
   was 2027, with no games: every unstated season would have read it.
   "This season" is now the latest season with games on record
   (`season_on_record`, entered by `Agent.ask`); `ASSOCIATION_TODAY` pins
   the date for the suite and every harness. Fifteen tests encoded the
   2025-26 season and would have gone red on the day.
4. **Phase 0 - make it measurable (2026-09-30 to 2026-10-01, closed).**
   - *Stage snapshots* (`scripts/stage_snapshots.py`, `query/stages.py`,
     `tests/stage_calls.py`): the reading, the planned query, the result's
     values and the answer per question, compared stage by stage, on two
     populations. Two runs of one tree are identical on both (628 of 628
     recorded questions, text included; 1,363 unit-test calls), so any
     difference is the change. A perturbed token fails each at the stage
     it was made in. An empty comparison is exit 2: a wrong path read as
     "0 differ" once during the build.
   - *Ratchets and contracts*: three import contracts and
     `scripts/check_ratchets.py` (the eighteenth gate) list today's
     violations by name - 50 functions outside the reader take the
     question, 21 modules run SQL, 91 private template imports, nine reader
     imports of the answer side - and fail on a new one and on a listed one
     that is gone. The 25 intents are frozen by a test.
   - *The unread-words ledger* (`scripts/claims_ledger.py`): each content
     word deleted in turn, from outside the reader. 679 of 2,415 words do
     not move the reading; nine questions have an unread number, and three
     of those were fluent answers to a broader question, found on the
     instrument's first run.
   - *Typed remarks* (`query/notes.py`): an Opus agent inventoried 69
     distinct remarks from about 75 writers, the same fact in up to eleven
     wordings. 28 closed kinds with closed fact names; 98 writers wrapped,
     the sentences unmoved; 180 of 628 recorded answers carry 234 typed
     remarks. The new check found two answers that write a caveat and never
     say it (#311). Five Opus agents wrapped the template modules in
     parallel, one module group each.
   - *The target types* (`ROADMAP-TYPES.md`): draft 1 was reviewed by an
     Opus agent against the corpus before Jeff read it and was lossy in
     seven ways (16 splits answers carry four groupings; 20 answers are
     clarifications, not refusals; the measure type lacked the opponent's
     figure). Decided 2026-10-01: ranking, comparison and split are three
     shapes; a decision and a note are two types; no `pair` shape.
   - *What it cost*: 21 findings filed in one day from the instruments and
     the two agents, three of them new wrong answers. Agent worktrees are
     cut from pushed master, so five agents dispatched on unpushed commits
     all stopped at their first check and had to be redispatched. Cleaning
     up afterwards, the lead removed every worktree under
     `.claude/worktrees` by pattern - 42, of which 37 were earlier
     sessions' - and their branches; 66 dangling commits were pinned under
     `refs/rescue/2026-10-01/`, and uncommitted files in those worktrees,
     if any, are gone. The lesson is in `AGENTS.md`'s own terms: look at
     the target before deleting, and delete by name.
5. **Phase 1, step 1 - the planner runs once (2026-10-01, `2e24173`).**
   The parser planned the point it read and the compiler planned it again.
   Now `compose.plan.plan_point` is the PLAN stage, run once by
   `Agent.ask`. No answer moved: 628 of 628 identical in every stage; 8 of
   1,378 unit-test calls differ, each the Reading now holding a point the
   planner later declines. Reader imports of the answer side: nine to
   eight.
6. **Phase 1, step 2 - the subject is read once (2026-10-01, `6af7c08`).**
   Three readings per question, which disagreed on 18 of 628. Now one,
   carried on the route and settled under the stages' intent. No answer,
   query, scope or intent moved on either population; 5 subject records
   and 185 decision reason texts differ, each now saying what the one
   reading rested on. The corpus run went from about 285s to about 220s.
7. **Phase 1, step 3 - a companion's name has one reader (2026-10-01,
   `a8f02f6`).** The stages had regexes of their own for "with X" and
   "without X" beside the subject reading's; they disagreed on seven
   recorded questions. The stage readers are gone and the stages are
   handed the subject's companions. Four answers moved, each to exactly
   its graded sibling wording's answer (two wrong answers naming fewer
   teammates than asked, #310, and two refusals). The first attempt -
   simply deleting the stage readers - lost names on wordings OUTSIDE the
   corpus: "without curry" and "Tatum and Brown" are ordinary words the
   subject reading skipped unless the model had copied them, and "warriors
   record in games curry missed" answered the whole record. The corpus
   showed none of it; a harness of 25 such wordings, each read with the
   model's names and with none, old tree against new, did. The stage
   reader's strength - a name read by its position in the phrase - was
   folded into the subject's, and the 50 cases then kept their names and
   intents. The ledger grew by five words, all the verb in "when X and Y
   play", which the one reader does not need.

8. **Phase 1, step 4 - names from an in-memory index (2026-10-01,
   `b8ac3a2` and the commit after it).** One Opus agent, about 67 minutes
   and 364k tokens, confined to `entities.py` and a new `names.py`. Its
   differential check - 478,304 calls of 31 lookups against the old code
   on the real warehouse - is what made the change safe: it caught that
   DuckDB's ILIKE lowers only ASCII on an all-ASCII column (so "İndiana"
   finds no team), that its Damerau-Levenshtein counts UTF-8 bytes, and
   that RE2 folds only the Kelvin sign and the long s. The lead then moved
   the three team lookups the agent had been told to leave. Reader
   statements over the 277 yardstick questions: 10,658 (38.5 a question,
   down from about 100 when the subject was read three times) to 554, the
   two loads per question. No answer moved: 628 of 628, 1,386 of 1,386
   calls. Not reproduced: the order of two players sharing a
   `display_name`, which DuckDB's sort left unspecified.

9. **The move to the OVH devbox, and a review of Phases 0 and 1
   (2026-10-02).** A second agent reviewed the work so far on the new
   machine and re-measured its claims: one subject reading, one planning,
   two reader statements per question, the ratchets' counts, 628 of 628
   snapshots identical across the two machines. It found one regression
   the two populations could not see - since the subject is read before
   the stages settle the season, a team named by a franchise's old name is
   read without it, and "chris paul assists for the hornets in 2008" asks
   which Hornets (#316) - and three paths kept beside their replacements
   (the replayed route's own subject reading among them). The move itself
   cost three things nobody had pinned. *The interpreter*: no
   `.python-version`, so the new box took 3.14, where `names.sql_lower`
   disagreed with DuckDB on 27 code points; it had only ever agreed on
   3.12 and 3.13. Python 3.14 is now pinned and required, and the lowering
   is a table generated from DuckDB itself (`206dd74`). *The model*:
   ollama 0.33.3 became 0.35.0 on another CPU, and 53 of 628 normalizer
   replies changed; the parser absorbed all but one answer, the live run
   (parser23, `42e95d3`) is word for word parser22 - 167 of 175, 156 of
   166, median 0.50s where it was 1.19s - and the replies were re-recorded
   and the baselines retaken (the ledger: 683 of 2,409). *The research
   directory*: it had no history at all; it is a private repository now.
   Gates on the new box: the fast check 24s, the full check 37s, the
   corpus snapshots 102s, the ledger 12s.

10. **Phase 1, the order from here, steps 1-5 and the stages run once
    (2026-10-02).** Step 1 (`b2ee61c`): `Agent.ask` takes no route and a
    route with no subject is refused, not read for again; the preview
    script answers recorded questions with recorded replies; 39 test call
    sites moved to `tests/routed.py`. Step 2 (`fcd9c68`): the compiler and
    the snapshot take their planning; a name lookup outside `names.loaded()`
    raises (901 tests had made one). Step 3 (`f5b2682`): the snapshot's
    query stage is the planned Query; the run's first line names Python,
    DuckDB and ollama. Step 4 (`6a5eb6a`): SQL counted per module (103 in
    19); the D4 freeze holds templates, presenters, adapters, scoping
    declarations and renderers. Step 5 (`c2438b1`): the drifted figures.
    Then the stages run once: the child named from the grammar, the stages
    run under it first, the parent only where they decline - 1.00 runs a
    question where it was 1.68, nothing but decision records and
    `intent_reason` moving on the corpus, and 2,710 wordings old tree against new (628 corpus, 2,082 outside it)
    read the same but three that gained a stat. Cost: the ledger reads 690
    where it read 683, because a second run's decision records had been
    counting as reads. Then the condition always written (`0bd4754`: two
    wrong answers found by hand become refusals by name; `subject` no
    longer imports `compose`) and five of the reader's imports moved to
    the reader's side (`1eb8677`; two left, both `read_point`), with one
    push that went up red because a chain ran the hooks with `;`. The
    `read_point` move was planned in five steps and its first taken: of
    the 40 decline and refusal sites the point reader reaches, 18 fire on
    some population and 22 on none.
11. **The `read_point` move, steps 2-4 (2026-10-02 to 2026-10-03).** Step 2
    (`579e533`): the measures closed by name in `measures.py`, the SQL
    tables held to those names by a test; the reader imports no SQL to
    learn a name. Step 3 (`e5b8954`): the five "cannot honor a cell" sites
    are the planner's (`compose.plan._shape_declines`); 3 of 628 and 26 of
    2,710 readings carry the point they declined before, every answer
    identical. Scoped on the way: 5 sites are the reader's own verdict
    about the relation and stay; 30 "needs a player / a stat" checks in
    the adapters wait for Phase 2's slices; nothing deleted by coverage.
    Step 4: the three refusals are a `reading.Cause` the planner says
    (`compose.plan.refusal_result`), so the Reading carries no
    `TemplateResult` and the reader builds no sentence; identical on all
    three populations, 10 of 628 refusals said word for word. Cost of the
    three steps: none measured - the stage records, the ledger and the
    answers are unchanged. Step 5 (2026-10-03): the file is
    `query/point.py`, a reader module to the contract and the ratchet (28
    entries left `ratchets.json`); the two `parse -> compose` ignores are
    gone and one stands in their place, `point -> compose.adapt`, for the
    intent's default point - the ten adapters lean on the templates'
    helpers, and Phase 2 deletes them slice by slice, so moving them now
    would be work thrown away. The vocabulary the reader took besides
    (`Unsupported`, the limits, the ordinal, the team-only intents, the
    metric and measure aliases) went to `reading` and `measures`, and the
    two functions that planned went to the planner and to the one test
    that used them. Identical on all three populations.
12. **Phase 1 reviewed and closed (2026-10-03).** An Opus agent reviewed
    the phase adversarially against the roadmap: every measured claim held
    when re-run (628/628, 1,391/1,391, 2,710/2,710, the ledger's 690), and
    the design goals held only where the gates looked. Fixed the same day:
    "2,710 out-of-corpus" was 2,082 outside the corpus (`e8bff4a`, with the
    harness committed to `association-research/stages`); the snapshot's
    query stage recorded the planned query where `games_reading` re-ran a
    different one on 3 of 628, and omitted the reader's verdict on the
    point (`5d25df5`); six ways past the ratchets, each now caught and held
    by a test, and a fifth ratchet for reader functions taking a
    connection (`56f0422`); `Subject.question`, `adapt.to_query` and a
    duplicate helper deleted (same); the streak's team and league cell
    checks moved to the planner, the month names to the reader's side so
    the contract could be checked on chains, the scoping freeze widened to
    14 (`c38600f`). Filed: #318, #319. Then the live run the roadmap
    requires: `live_parser24` at `9254804`, 167/175, families 156/166,
    277 of 277 answers identical to parser23 (median 0.5s). The scorer's
    "moved" test now compares result and answer values, since the reading
    and query records are Phase 1's instruments and their shape moved with
    the work. Cost: none measured in answers; the ratchet lists grew by
    one check (26 reader functions take a connection, 24 now).
13. **Phase 2, step 0 - the Result and the first sayer, on the game log
    (2026-10-03).** Measured first: the compiler's own `run` of the planned
    query gives the same games and values as the template's read on 41 of
    41 player logs the template answered on the corpus (17 team logs and 8
    compiler-said ones aside), so slice (i)'s "execute the compiled SQL" is
    planned on numbers. Then the move: `query/result.py` (Result, Part,
    Rows, Span, Narrowing, Window), `compose/logs.py` (the template's
    body, reading into a Result), `compose/say.py` (the words, one phrase
    per note kind, recording through `notes.note`); two presenters and the
    log's ~600 lines in `templates/games.py` deleted. Identical on 628 of
    628, text and remarks included, and 1,391 of 1,391 unit-test calls.
    Kept on purpose: the `game_log` adapter (slice (i)'s five share
    `measure_filters`, which moves to the reader's side once, in step 1),
    `STATED_SCOPING["game_log"]` (one table declares for every compiled
    intent), the page's intent-keyed renderer (Phase 4). Cost: the
    private-template-import ratchet 87 -> 91, the relation's shared steps
    (`_Span`, `_span_of`, `_resolved_team`, ...) now called from `compose`;
    they are mis-homed in `templates.common` and step 1 moves them to the
    relation modules, which shrinks the list for every later slice. The
    Result still carries three things as words (`Narrowing.phrase`,
    `Span.years`, `Result.empty`), named in its docstring as what the
    sayers take over.

14. **Phase 2, step 1 - slice (i) split, then merged onto the compiled
    statement (2026-10-03 to 2026-10-04).** Four sub-steps split the rest
    of the slice as step 0 split the log, each identical on 628 of 628
    (text and remarks) and every unit-test call: (a) the relation's shared
    steps given public names (`89d6fa3`; the private-import ratchet 91 ->
    59), (b) the five default points to the reader (`query/point.py`;
    adapters 10 -> 5) with what they read moved first - `lines.py`,
    `measures`, `reading`, `entities` - and `TemplateUnsupported` made
    `reading.Unsupported` (`e05dae2`; 43 unit-test calls differ in the
    exception's class name alone), (c) `record_when`'s reader and sayer
    (`14d9757`), (d) `player_splits`' both branches into one sayer
    (`5876bc2`; presenters 12 -> 9). Then the order question: the row's
    title says "execute the compiled SQL", and what had landed was each
    template's read moved into a reader that still ran its own statement
    beside the compiled one. Jeff's rule (2026-10-04): whichever order
    leaves the least temporary code - so the merge (g) goes before the two
    shapes not yet split, (f) narrowed `player_stat` and (e)
    `period_split`, which are then written once against the compiled
    statement. The first merge, the player's log: `read_player_log`
    compiles the point with the log's columns as measures and runs that;
    the mixed read compiles each season type over the settled subject
    through `compile_over`, the second half of `compile_query` made a
    seam. Identical on 628 of 628 and 1,393 of 1,393. What it turned up and
    fixed in passing (AGENTS.md, the rewrite's stance): two definitions of
    "the warehouse carries rebuilt lines" (`log_carries_rebuilt` over the
    log, `box_source` over the view - every compiled read was a latent
    Binder error against a fixture holding one without the other), now one
    in `box_source`; two fixtures built unlike the warehouse (a log not
    over the filled view; a bare day where `games.date` holds a UTC
    stamp), now built its way. The second and third merges the same day:
    `record_when` as a grouped read by the compiler's new `line` group (the
    point's one predicate as the key - reached, short, blank - with each
    group's record, seasons and teams, and a `margin` measure), its planned
    point untouched; a player's splits as four grouped reads by `venue`,
    `starter`, `won` and the new `month_of_year` (measured 63 of 70
    kind-reads equal before writing it, the seven apart the career month
    tables, where the compiler's `month` is a season's year-and-month).
    Each identical on 628 of 628, every unit-test call and every reading -
    except three calls on the rebuilt fixture, where the splits' FG% moved
    93.3 -> 50.0: the template summed makes over every game and attempts
    over the fetched ones, the compiler summed both raw, and neither was
    the rule every other unmeasured column follows; `core._rate_sql` now
    skips rebuilt rows in both sums where a rate reads such a column, and
    the test asserts it. One `core.rows_of` runs every compiled statement
    (the statement ratchet: core 2 -> 1, logs 6 -> 4, records 1 -> 0,
    player_games 2 -> 1). Then (f) and (e) in parallel, two Opus agents in
    worktrees off pushed master, each measuring first and each written
    once against the compiled statement: the narrowed `player_stat`
    (`compose/stats.py`, a `Scalar` body, the compiler's `line` aggregate
    reading the sums and shot rates the sentence is said from beside the
    per-game figures; 19 of 19 corpus and 13 of 15 synthetic cases equal,
    the two apart a made count over rebuilt lines stating attempts the
    rebuild never measured, fixed; `662910a`) and `period_split`
    (`compose/periods.py`, both shapes; the period relation's tables moved
    to `player_games` first, `b15eef1`; 18 of 18 equal once the compiler
    carried the opponent's name as a label measure; both presenters and 31
    helpers gone, two remaining templates taking the moved names at call
    time; #301 and #302 fixed with it; `7a1e91c`). The lead merged each
    after re-running its gates, suite, ratchets and the 628 itself. Step 1
    closed 2026-10-04: presenters 12 -> 8, adapters 10 -> 5, the
    private-import ratchet 91 -> 47. What the agents said about the
    pattern, for the next slice to settle: a one-quarter Result is
    recognized in `say()` by a `"period"` key in `facts` because `Rows`
    has no `by`; a `decided(...)` remark rides in `facts` because Notes
    take note kinds only; the Result's `empty` sentence and `Narrowing.
    phrase` are still words.

15. **Phase 2, step 2 - slice (ii) in parallel (2026-10-04).** After
    step 1 closed, two conventions settled before dispatch: a decision
    rides on `Result.decisions` as a `Decided` value phrased once by
    `say.decision_phrase` (the period redirect's `season_fallback` moved
    onto it the same day; `b2926b0`), and the agents' pattern notes went
    onto `ROADMAP.md` as "Open from step 1". Then two Opus agents in
    worktrees off pushed master, split along the template seam: half A
    `threshold_count` + `single_game_high` (49 of 49 presenter answers
    matched the compiled rows before a line was written; adapters to the
    reader first; `Scalar.how` and `Rows.by` so the sayer branches on the
    body; a decline that named the wrong missing fact fixed, 11 readings
    rewording), half B `streak` + `player_matchup` (the `Runs` body
    declared; 25 of 25 matched, 19 of them synthetic because the corpus
    holds no player streak; the team streak's presenter now words through
    the same two helpers; two unsaid remarks and a mis-aliased column
    fixed). The second agent rebased onto the first's merged work and
    re-proved on the merged tree before reporting; the lead re-ran gates,
    suite, ratchets and the 628 before each merge. Presenters 8 -> 4,
    adapters 5 -> 1, private-template-import ratchet 47 -> 23. Left open
    on purpose: the row's "each refusal becomes a `Cause`" - both agents
    hit the same wall, that a user-visible decline becomes a Cause only by
    moving the reading's verdict, which the readings population holds
    identical; about 15 readings; put to Jeff.

16. **Phase 2, step 3 - the season line, and the reader seam (2026-10-05).**
    Jeff's rule first: a user-visible decline becoming a `Cause` is a
    permitted reading move, one commit, every moved reading enumerated
    (`122f288`; AGENTS.md "Identical means identical"). Then three Opus
    agents off that commit: the leaderboard (`605a96f`), the season line's
    three player shapes with `query/season_line.py` as the relation
    (`7e7a45b`), and the reader seam - the last adapter and `adapt.py`
    gone with contract 1's last ignore, `MAX_LIMIT` one value, `CAUSES`
    3 -> 16 with the enumerated move (3 recorded questions, 20 readings,
    31 calls), and `games_reading`'s re-plan into the planner (50 feed
    readings' planned query). Each later agent rebased onto the earlier
    merges and re-proved on the merged tree; the lead re-ran gates, suite,
    ratchets and the 628 before each merge, and bracketed the slice with
    live runs: parser25 before, parser26 after, 277 of 277 identical.
    Presenters 4 -> 0 and the freeze on them retired; what is left of the
    middle is `present.py`'s `STATED_SCOPING` and the two team presenters
    (slice (iv)), and the words in `templates/` the team shapes and the
    charts still carry. What the agents said about the pattern: the
    season line has three entry points (`rank_season_line`, `values_of`,
    `season_redirect`) worth folding into one; a comparison and a matchup
    share a `Grouped` by subject and are told apart by `Span.source`; the
    `ran` boundary now always equals the planned query and could go.

17. **Phase 2, step 4 - the team shapes, four agents at once (2026-10-05).**
    After step 3 the lead did two things first: a leaderboard's defaulted
    season became a `Decided` the heading's own phrase is written through
    (no new remark - the value was already visible), and the agents' notes
    went to the roadmap. Then four Opus agents off `1f66894`: the season
    line's fold and the `ran` boundary; the team compiler's `rows`/`grouped`
    compiles, merged FIRST so the two template agents could compile
    through them; `team_stat`/`team_leaderboard`/`team_outlook`/`coach`
    with the team-season relation; `team_record`/`head_to_head` and the two
    period shapes. Merge order was finish order, each later branch rebased
    (one squash-merged after three moves of master) and re-proved on the
    merged tree, the lead re-running gates, suite, ratchets and the 628
    before every merge, and bracketing the slice with live runs (parser26,
    parser27: one answer moved, the Warriors floor fix). Two enumerated
    reading moves beyond the Cause rule - seven intents gaining a point so
    they can be planned (69 recorded readings in all, 296 of 2,710,
    answers identical) - taken by the lead as the same class and recorded
    in AGENTS.md for Jeff to confirm. `templates/teams.py` is gone,
    `present.py` is gone, `leaderboard.py` is gone; `TEMPLATES` holds the
    four charts. What the middle still carries, for slice (v) and Phase 3:
    `check_scope`/`HONORED_SCOPING` (the charts' last callers),
    `STATED_SCOPING` in `plan.py`, `facts["missing"]` on an empty
    team-season Result, two note phrases branching on a call argument.

18. **Phase 2, step 5 - the charts (2026-10-05).** Two Opus agents, each
    chart a declared shape with its own reader, a draw step and a sayer,
    the renderers untouched, every statement through the season line's
    `Statement` door - the ratchet gained no entry. 63 chart pages
    byte-identical across the port. `TEMPLATES` is empty; `check_scope`,
    `HONORED_SCOPING` and `templates/common.py`'s shared steps are step
    6's. One P1 found by a measurement harness, not by a user: a date
    written as numbers is never read (#323).

19. **Phase 2, step 6 - the exit (2026-10-05).** One Opus agent on
    `893557d`. With `TEMPLATES` empty the shell was all that was left, and
    it went in an order chosen to move each name once: the result and
    context types to `query/answer.py` first (`Reply`, `AnswerContext`),
    then `check_scope`, `HONORED_SCOPING`, `TEMPLATES` and the agent's
    template branch deleted (they declared and ran nothing), then the
    relations' shared steps moved home - 96 names, public, no alias kept -
    and `templates/` deleted with them. Measured before each commit and
    after, on the three populations: identical but for one refusal
    reworded ("has no template yet" named a missing template; "has no
    reader" now, 5 unit-test calls) and one renamed test. Costs: two new
    modules, `player_relation.py` and `team_relation.py`, beside the
    relations rather than in them (size, and a cycle through `conditions`);
    `unhonored_scoping` on the reader's side because `plan.py` imports
    every reader; the SQL ratchet's two new entries hand-edited, the
    statements moved and not grown. What the agent found: the import
    contract that keeps the sayer off the warehouse cannot be checked on
    chains yet - `compose.say` takes constants from four modules that hold
    statements too; about 150 docstrings still say "template" as history.

20. **Phase 2 reviewed and closed (2026-10-05).** The closing live run
    (`live_parser28` on `93db30c`: 277 of 277 identical to parser27, 167 of
    175, 156 of 166) and an Opus review that re-ran every population and
    added one nobody had run - the 2,082 feed questions answered, not read.
    Held: every enumerated move, every deletion, the score. Did not hold:
    three "done" rows (compiled reads 264 of 614; 12 scoping declarations
    unfrozen; the ledger 695), and 4 feed answers moved unnamed, one of
    them now misleading (a rate's qualifier counting games the rate skips).
    What the phase built, in the reviewer's words: a reader and a sayer per
    retired template, moved - a fair Phase 2, not yet the target's shapes.
    Fourteen findings filed (#325-#338; two P1s older than the phase). The
    cleanup list is the order from here.

21. **The review's cleanup (2026-10-05/06).** The lead's order, Jeff's
    say-so: (a) first - the one live wrong answer, then the tooling that
    had let the done table overstate; (b)2/(b)3 - the proof widened to a
    recorded planner and the feed answered - before anything else moved;
    then "the Result is typed" as one agent's step. Six commits of Opus
    agents, each proven on four populations. What changed in kind: a
    refusal found at run time is a typed value from a closed set, the
    Result's bag is seventeen records and a cell union, and nothing on
    the answer side reads the intent to choose. What the review had
    found - 105 statement sites counted as 38, 12 declarations unfrozen,
    4 feed answers moved unseen - is measured and held by a gate now.
    Phase 3 opens with (b)4: the three question re-reads in `agent.py`
    onto the Reading.
22. **Phase 3, step 0 (2026-10-09).** The lead wrote the phase's expected
    steps before the first (the 2026-09-30 "shadow reader" re-planned as
    the four populations: no second reader in `src/`), and one Opus agent
    took step 0. What it measured: every refusal the answering loop still
    decided from the question's own text - eleven checks - asked of every
    question on the 628 and the 2,082, with what fired, what it said and
    whether it reached the answer; none was answered by the compiler, none
    fired twice. What moved: those readings are the parser's, as a refusal
    the words come to (`Reading.refused`), the draft's `Unsupported` filter
    (`Reading.unsupported`, said only where the answer side declines, since
    most of its questions decline at RUN, not at PLAN - a correction to the
    brief) and the fingerprint's left-out names; `refusals.py` is gone,
    the twelve name readers of `entities.py` are the subject reading's and
    take the index, and the ratchet of question-taking functions outside
    the reader reads 1. Proved on all four populations with every moved
    reading enumerated by question (9 of 628, 164 of 2,082, 173 of 2,710,
    14 calls), answers identical everywhere. What it cost: the agent's
    base moved under it when 5.0.0 was cut mid-step (the first PyPI upload,
    as `association-py`), so the lead rebased and re-proved against a
    fresh run of master; and `compare-calls` needed an aligning harness,
    since a step that adds a recorded call shifts every later call of its
    test. Two wrong answers fixed in passing (a quarter's plus-minus
    answered as points; a false conference sentence), two P1s filed (#339,
    #340) for the filter families step 2 reaches.
23. **Phase 3, step 1 (2026-10-09).** The answer side's key stops being
    derived from the intent: the point reader declares `shape`, `by` and
    the relation it is `on`, the planner builds the key and the compiler's
    skeleton from those, and the coverage floor is declared per key with
    the relation's tables. One Fable agent at medium effort (the day's
    exception), measured first on 2,457 planned points under 32 keys. What
    it taught: the target relation is not a function of the point's other
    fields (a quarter's log and a game log share shape and `by`), so the
    Reading carries `on` beside today's `relation` until step 2 retypes the
    subject; and a floor keyed by intent had put three table sets under one
    key, which is how "Jordan's points in 1990 on tuesdays" was held to the
    season line's 1977 floor (#212, fixed) and two feed rankings were
    refused "No games" instead of by the box-score floor (fixed, the two
    named). The lead merged it onto a master ten isolated fixes ahead of its
    base - the parallel stream Jeff asked for, each proven the same way -
    and re-proved it there: identical on all four populations with the
    four added point fields ignored and listed by count.
24. **Phase 3, step 2, the span (2026-10-09).** The first filter family
    typed, and the pattern for the rest: one Fable agent at medium effort
    (the day's exception) measured the six span slots first - which stage
    set each, by which words, on all 2,710 readings, with the planner's
    verdict and the relation's resolution of them
    (`~/association-research/stages/span_family.py`) - and designed the
    type from the table rather than the draft: fields, not a union, since
    a career stands beside a named season on 3 readings and beside a
    range on 8, and the relation picks between them, refusing where it
    cannot. What moved: `reading.Span` on `Scope.span`, the six slots
    gone; `query/lexicon.py` with the family's 22 patterns and their
    reasons; `query/span.py`, ONE tagger run last in the stages over a
    typed context of what they settled, in the order the six stages read
    (the words, a dated range, the range's own forms, the implied
    careers, both types for a bare last-N log), claiming the characters it
    consumed; the three cells in both relation tables with per-reader
    exclusions; `router.py` 406 lines shorter. What it taught: a retyped
    part is proven with no field ignored by keeping the record's old
    shape through a projection (`Scope.projected()` and its kin) and
    recording the typed value beside it, which the readings, the 628 and
    the feed all compared identical through; the unit-test calls showed
    the one real move, five subject tests handing the stages a model-era
    season no path produces any more; and the regular season named
    outright cannot be told from the default (`ROADMAP-TYPES.md`, open
    item 8). Cost: the lead's rebase over thirteen isolated fixes (two
    conflicts), the four populations run fresh on both trees, the
    relation's resolution re-held on 2,710 of 2,710. Filed: a P3, the
    condition read's covered scope open-ended for a closed range.
25. **Phase 3, step 2, the window (2026-10-09).** The second family, on
    the span's pattern: one Fable agent at medium effort measured the four
    window slots on all 2,710 readings first - which stage set each, by
    which words, and what the relation cut - and found that the parser
    read the grammar twice, before and after the stages, so every drop of
    the COUNT the model-era filler rules made was put back by the second
    read: five rules and their callers were dead, and are deleted, not
    ported. What moved: `reading.Window(order, count, of, rank, by)` on
    `Scope.window` (fields, because a count stands with no end on 102
    readings and an end never without one); `query/window.py`, the one
    tagger, run just before the span's, which takes the typed window as
    context; the cells in both relation tables, `COMPILER_SLOTS` gone with
    them; `router.py` 322 lines shorter. Proved identical on all four
    populations through the projection, the relation's cut re-held on
    2,710 of 2,710, the ledger identical. What it taught: the model-era
    rule that dropped an end on a reader that refuses one hides a wrong
    answer ("Warriors vs Mavs record last ten games" answers this
    season's three), and keeping identical meant porting it and filing it
    as the P1 it is, for a decline-to-Cause commit; and a bare count on
    the games relations is a default nothing says out loud (open item 9).
    A fix made in passing - one game named as his in a phrasing the
    grammar misses - grows the unread-word ledger by 15 words that were
    only ever counted as read by accident, so it waited on the branch
    under the "may not grow" rule; Jeff's call the same evening was to
    merge and let it grow (`d12f899`), the first growth the ledger has
    been allowed, and its words are on record. Cost: the lead's rebase
    over sixteen isolated fixes and a fresh four-population run on both
    trees.

26. **Phase 3, step 2, the games' cuts (2026-10-09).** The third family,
    on the span's and the window's pattern: one Fable agent at medium
    effort measured the eight cut slots on all 2,710 readings first -
    which stage set each, by which words, what the planner declined by
    them and what the relation resolved them to (an opponent on 503, a
    round on 119, a situation on 96, a venue on 88, a date on 47, a tenure
    on 14, a series game on 12, an ordinal season on 4; of the 96
    situations 42 parse to a calendar, 21 to an alignment and 33 to words
    nothing reads) - and typed them from what the readings said. What
    moved: `reading.Cuts` on `Scope.cuts`, eight fields rather than the
    draft's nine types, because the readings hold them together (an
    opponent beside a venue 32 times) and a relation applies each as one
    more clause over the same rows; `query/cuts.py`, the one tagger, run
    before the window's and the span's, taking the opponent and the tenure
    from the subject reading, the one reader of who stands against or
    beside the subject; the lexicon grown by the calendar's whole
    vocabulary and one month table, so `calendar.py` holds no pattern and
    the regex ratchet fell 4 -> 3; the cells in both relation tables with
    66 reasoned exclusion rows; `router.py` 205 lines shorter. Proved
    identical on all four populations through the projection, the
    relation's resolution re-held on 2,710 of 2,710, the ledger identical
    at 709. What it taught: a situation is one cell holding three readings
    (a calendar, an alignment, words nothing reads), and splitting it
    moves 33 unread situations from RUN to PLAN with a different sentence
    - a decline-to-Cause commit of its own, not this slice's (open item
    10); a reader that states no cut carries seven near-identical
    exclusion rows, which the planner's cell checks should replace with a
    per-reader shorthand; and the claims gate had caught nothing in its
    one day but one wording - "lebron's last game 7", where the window's
    "last game" and the series' "game 7" are both right and the reader
    raised a traceback to the user - so a partial overlap is now one claim
    named for both readings (`a29c7fd`), a revision of the span slice's
    decision, for Jeff to keep or reverse. Cost: the lead's rebase over
    six commits (one conflict, the changelog) and a fresh four-population
    run on both trees.

27. **Phase 3, step 2, the period (2026-10-09).** The fourth family, the
    smallest, on the pattern of the three before it: one Fable agent at
    medium effort measured the two period slots on all 2,710 readings
    first - which stage set each, by which words, which intent the words
    chose, and what the relation resolved them to (a quarter on 79, a half
    on 41; no stage moved a slot) - and found that the words name a period
    on 128 readings while 120 carry one: the two period conditions, whose
    words the parser cuts before the stages see them, and six readings on
    a subject nothing resolved, which the point's guard declines. What
    moved: `reading.Period(number, half)` on `Scope.period`, one cell in
    both relation tables where the slots had two (the 22 paired exclusion
    rows are 11), `query/period.py` the one tagger with `which_period` the
    one reader of which period any words name - the intent stage and the
    condition's reader both read through it - and thirteen named patterns
    in the lexicon, three of them copies collapsed (the point's guard, the
    parser's three grammar rows, the condition's pattern); `router.py` 95
    lines shorter. Proved identical on all four populations through the
    projection, the relation's resolution re-held on 2,710 of 2,710, the
    ledger identical at 709, no claim joined with another tagger's. What
    it taught: the six unread-subject readings are the first measured case
    of the target's rule that the planner, not the stage, should refuse a
    cell on a reader that takes none - ported as the stages wrote them and
    recorded as a decline-to-Cause commit (open item 3); an overtime
    period is words nothing reads today, and "an overtime game" is a
    games' cut, not a period, so it is filed and not read; and the parser's
    cut of a condition's words leaves a latent offset for every claim
    after them, which the line slice closes by blanking instead of
    cutting. Cost: no rebase (master had not moved) and a fresh
    four-population run on both trees.

## The plan items, as written

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
   any shape the question's own words name. **Status 2026-09-26:** 2c
   done (23 -> 16 intents); 2a at parity for threshold_count 18/18,
   single_game_high 10/10, record_when 10/10, player_history 20/20 -
   none deleted yet; folded into item 6, which deletes them behind the
   Reading rather than behind the slots.
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
   two-sided. **Landed 2026-09-26** (item 13 above): the condition on
   the relation, the roles off the reading, `record_when` for a team under
   a companion's line, `with_without` by predicate; the opponent side and
   the N-way join remain.
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

6. **The parser: one Reading, one writer of slots - (a)-(c) done, (d) parts 1-3 done, part 4 next (item 14 above).**
   The model's job shrinks to a names+stat normalizer on the 3B (names as
   spans of the question, one key from a closed measure table, no intent
   enum, ~300 tokens - the prompt-length drift class goes with the enum).
   One parser module reads the question into a typed Reading: kind,
   subject, opponent, companions with roles, measures, predicates, scope,
   window, shape, with the words each field came from - today's settle
   stages, the subject reading and the compiler's `move_point` become its
   grammar tables, each row a test case. A planner turns the Reading into a
   point on a relation and refuses a narrowing the relation lacks (it
   replaces `check_scope`); the compiler answers it; a template survives
   only for a shape of its own (chart, fingerprint, streak, matchup,
   quarter) and reads the Reading, never slots. The relations, the
   coverage floors, name resolution with the recency rule and the caveats
   do not change. Steps, each proven before the next: (a) the IR and the
   planner in front of the compiler for the four shapes already at
   parity, golden-neutral; (b) the grammar tables, measured on the 277
   day10 wordings and on the 277 paraphrases as the held-out set (never
   tuned on; `parser-greenfield/paraphrases_7b_v2.jsonl`); (c) the
   normalizer replaces the router prompt - a live run, merge at >= 160/175
   (day10 after the F088 re-grade); (d) the templates read the Reading and
   the five slot writers are deleted. Two decisions before (b): typo policy
   (the normalizer may correct, or the index defaults visibly on a single
   near spelling), and where "this year" stops (deferred). Design doc:
   `~/association-research/parser-greenfield/DESIGN.md`.
   **Step (a) landed 2026-09-26 (43eca0c):** `query/reading.py` and
   `compose/plan.py`; the compiler's word reading builds the Reading, the
   planner copies it into the point; `-> (reading) ...` in every composed
   trace; the four parity intents are compiler-first. Golden 631/631 over
   the routing corpus and the day10 routes (a stronger check than a live
   run for a refactor: the same routes, byte-identical answers).
   **Step (b) inputs, 2026-09-27 (an Opus agent; `IR_AUDIT.md`,
   `RESULT_b_baseline.md`, `measure.py` in `parser-greenfield/`):** the
   Reading as designed expresses 223/277 day10 wordings, 46 need one
   addition (a period 18, both season types 13, a series game 3, ...), 7
   need a clarification; 20 families have no dominant key reading and the
   answer states its default. Baseline with the router withheld: DEV kind
   267/277, intent 256/277, window 186/277, stat 152/203; held-out kind
   257, intent 223. Two reading gaps it surfaced are fixed (c1812e2).
   **Step (b), first tables, 2026-09-27 (6e4e4aa):** `query/parse.py` -
   spans classified by the index before they can be subjects (the typo
   policy: one near spelling is that player), `PARENT_GRAMMAR` by kind,
   `WINDOW_GRAMMAR`, `MEASURE_GRAMMAR` (the NetPoints family) before the
   model's key, two teams from the words; the children's grammars gain
   the paraphrases' shapes (0 false positives). Router withheld: day10
   kind 274/277, intent 267/277, stat 176/203, window 225/277; the
   untouched v3 paraphrases kind 264/277, intent 242/277 (87.4%; ~8 of
   the 35 misses are the paraphrase changing the meaning). Nothing live
   calls it yet; golden 631/631. Remaining before (c): the 90% held-out
   intent target, the scope/window tables settle still owns.
   **Step (b) done, step (c) done, 2026-09-27 (2b2e9d3 .. fe53c72): the
   parser reads the question by default.** (b): held-out (v3) intent
   251/277 = 90.6%, kind 268; DEV kind 275, intent 261 (the DEV loss is
   the F088 rows whose reference holds the with/without reading Jeff ruled
   wrong). (c): `query/normalizer.py` (the measured prompt, names verbatim
   + one stat key) and `parse.read_route`, which writes the name slots from
   the subject reading, reads the window before the stages, a team's
   quarter, a ranking's `fields` and re-anchors a name the model corrected
   to the question's spelling; the entity index reads a single near
   spelling visibly (`entities.read_near_spelling`). Live runs, graded
   with the season type as the regular-season default (Jeff, deferred):
   **162/175 (92.6%), families 151/166, 3 wrong** (`live_parser3.jsonl`,
   efef90d) against the router's 160/175, 150/166, 4 wrong (day10) - at
   1.08s a question median against 2.28s, 5.3 minutes a run against 11.3.
   The recorded replies reproduce live exactly (three live runs, 0 answers
   moved against their offline rehearsal), so
   `yardstick-v2/run_offline_parser.py` is the loop and a live run the
   record. A hold-out the fixes were not found on - the recorded corpus's
   75 questions outside day10, both readers, no model
   (`holdout_compare.py`) - found six parser-only wrong answers (efef90d);
   of the 34 answers still differing, 30 compare against a recorded route
   missing its name slot and the other 4 are the parser's equal or better.
   `Agent`, `association query` and `association web` default to
   `reader="parser"`; `--reader router` is the rollback. Lessons: a
   tuning set flatters (the yardstick alone would have shipped six wrong
   answers the hold-out found); a slot the router's model filled has to be
   read from the words or it is silently absent; and a replay tool that
   stubs one reader silently runs the other once the default moves
   (`preview_answers.py` pins the router now).
   **Package review (2026-09-27, Jeff: prefer well-used packages):** one
   adoption recommended - rapidfuzz's `DamerauLevenshtein` under
   `subject.question_supports`, one edit metric with the index's DuckDB
   `damerau_levenshtein` (ISSUES: the swapped-letter entry); hypothesis as
   a dev-extra trial for compiler properties; DuckDB's own
   `json_serialize_sql` instead of sqlglot for a structural SQL check.
   Rejected with reasons: lark (keyword spotting, not a CFG), spaCy (20
   dependencies, splits "30+"), dateparser/dateutil (fuzzy-read stat
   numbers as dates), pydantic in `query` (the core contract),
   sqlglot/ibis/pypika (DuckDB-only syntax, allowlisted strings already).
   The real maintenance cost it found is one vocabulary written several
   times (measure words six, months six, number words five - the parser's
   now one table). Next: (d) - templates read the Reading, the router's
   slot writers and `ROUTER_PROMPT` go.
   **Step (d), in progress 2026-09-27** (Jeff: typed dataclasses, not
   pydantic - the Reading is built by our own code, so the type checkers
   catch the mistakes before commit at no runtime cost; a `__post_init__`
   holds the one range rule, limit >= 1). Proven step by step with golden
   631/631 and the parser rehearsal, each a pure refactor until the last:
   (1) done, 1f344e4 - `Scope`, frozen and keyword-only, one typed field
   per slot, `Literal` where the values are a closed set; `Reading` the
   same; `Scope.from_slots` the one door a slot dict comes in by (raises on
   a key or value it does not type - all 1,185 recorded routes pass and
   round-trip). (2a) done, 3aaa755 - every template takes the Reading,
   flipped mechanically (25 templates, 762 test calls), bodies still on
   `to_slots()`; `conditions` typed (`ConditionSpec`); nonsense values
   refused at the door. (2b) in flight - round 1: `templates/common.py`'s
   shared steps and the compiler's `Query` read the typed Scope (two
   agents, disjoint files); round 2: one agent per template module; then
   the transition shims go. (3) one writer: the parser builds the Scope
   directly, and the router reader (`route()`, `ROUTER_PROMPT`, the model
   classification) and the slot-repair chain that exists to correct it
   (`apply_subject`, `override_nicknames`, `restore_dropped_players`,
   `undo_name_completion`) are deleted - measured, because it can move
   answers, with the rehearsal, the hold-out and a live run; golden moves
   from replaying recorded router routes to replaying recorded Readings.
   Measured first (`yardstick-v2/repair_ablation.py`: each repair step
   patched to a no-op, 352 questions - day10 and the hold-out - answered by
   the parser with recorded replies): `override_nicknames`,
   `restore_dropped_players` and `undo_name_completion` move 0 answers, so
   they go; `apply_subject` moves 6, and each is a writer the parser still
   lacks rather than a repair - the reroute to `record_when` ("76ers record
   with 20+ points from tyrese maxey"), the own team and the career span it
   implies ("lebron stats as a starter for Miami"), the position group
   ("Centers stats game log vs kings", "... by a shooting guard with at
   least 400 attempts") - so those parts move into the parser and the rest
   of it goes. By sub-step: `_apply_own_team` 2, `_apply_players` 2,
   `_apply_position` 1, the other eight 0 each - the `record_when` reroute
   moves only with the whole of it gone, so two sub-steps overlap there and
   the move is re-measured end to end.
   (4) the four parity templates the compiler answers retire.
   **(3), the router reader, done 2026-09-27 (1240612, on 33cfd60's 3c):**
   `route()` and its model call, `router_prompt.py` (`ROUTER_PROMPT`,
   `ROUTER_SCHEMA`, `ROUTER_NUM_CTX`), `Agent(reader=)`, `READERS` and
   `--reader`, and `scripts/check_routing.py` and `bench_router_models.py`
   are gone; the stages stay (`settle` runs them over the parser's raw
   route), `_MODEL_SLOTS` is a literal (the schema's 18 slot properties,
   checked equal before the schema went), and `router.py` no longer imports
   ollama. Proven with no model, on 33cfd60: golden 631/631 identical against
   `out_lead_3c2` (it replays recorded routes through `Agent.ask(route=)`,
   which never reached the reader); the parser's offline rehearsal 0 moved,
   its routes, traces, answers and fall-through reasons identical on all 277
   rows; the hold-out's 75 questions unchanged on both sides (38 identical
   answers before and after). Tests 2251 -> 2239: 17 deleted (the router's
   model call, its prompt against its schema, the reader choice) and 5 where
   the property still binds (every template reachable from the parser's
   grammar, the stages or the subject reading; the stages' side and order
   values against the typed Scope; the normalizer's schema and window). The
   routing check's 140 cases live on frozen in the research corpus
   (`intent-shrink/routing_cases.py`), so golden and the hold-out keep their
   354 corpus rows. Found on the way: the parser drops a misspelled team
   name and answers without it ("gui last 5 games vs sours" lists his last 5
   games, none against the Spurs - ISSUES P1).
   **(3), the repair chain, first half done 2026-09-27 (0e649ae, 33cfd60):**
   measured over every question with a recorded normalizer reply - 628,
   day10 and the recorded corpus and day10's paraphrases
   (`yardstick-v2/rehearsal_all.py`, which also records which repair step
   fired on each). On the parser's output `override_nicknames` fired 108
   times and moved two answers, both through an own-team misreading it hid
   ("for me" read as Memphis, fixed); `restore_dropped_players` fired 4 times
   and moved none; `undo_name_completion` fired once, harmfully ("Jan 19 Bam
   Adeyebu", a surname the reading had read, cut back to the ambiguous
   "Bam"). All three are gone, and the completion cut is the parser's, on
   the model's own spelling (`parse._as_typed_part`). `apply_subject` keeps
   only the steps that fire on parser output - the names, the restored
   player, the own team and its tenure, the position group, a team's
   record_when from a companion's line, the companions' roles; the
   router-only repairs never fired and went, with the `team_restored` slot.
   Golden has two halves now: v1 replays the recorded router routes
   (re-baselined at 33cfd60 - 48 router-shaped rows moved through 3c, each
   a question the parser reads unchanged), v2 (`golden/golden_v2.py`) the
   parser's own recorded routes, whose 628 replays answer exactly as the
   live read does. The round-2 agents' wrong answers, merged the same day:
   a teammate's role, a matchup's date and a shot read's calendar
   (8b7f1f8), record_when's blank group, the team branches' `until` and
   #144 (e08165c), the parser's teammate roles, companions and "scores 30"
   (582b3fa), a typo'd possessive and the lone "in games he played" player
   (79fa4e7).
   **(3), one writer, done 2026-09-27 (4406133, fd88e48):** the parser
   writes the Reading and the agent only consumes it.
   `parse.reading_from_route` is the parser's last step - the subject
   reading and its writers (`apply_subject`, called from nowhere else now),
   returned as one typed `Reading` that carries what it decided
   (`Reading.decisions`) and the model's names nothing in the question
   could replace (`Reading.misread`); the agent records the one, refuses
   the other by name, hands the Reading's Scope to `check_scope`,
   `check_coverage` and `coverage_caveat` and the Reading itself to the
   template, and writes no slot. A pure refactor: the rehearsal's 628
   answers and traces identical, golden v1 631/631 and v2 628/628; with the
   writers skipped as a perturbation, 6 of the 10 golden rows where one
   fires answer differently. Then the reroutes that never fire on the
   parser's output went - `_decide_intent`'s player-record-against-a-team
   (#163), pair-through-the-opponent and compare reroutes,
   `_apply_intent`'s child, with_without and pair rewrites, and
   `Subject.routed_opponent` - leaving a team's `record_when` under a
   companion's line (18 of the 628) as the reading's one settlement; the
   child grammar's tests read through the parser's own child step
   (`parse._read_route_child`, split out of `read_route`), and the reroute
   tests became tests of what the parser reads for the same questions.
   Measured: the rehearsal's 628 answers and traces identical, golden v2
   628/628; golden v1 moved on 78 of its 631 router routes, every one a
   route shaped by the router that leaned on a deleted reroute (60 recorded
   under a parent the child rewrite moved, 15 a player's record filed as
   `head_to_head`, the rest pairs and a comparison), and every one a
   question the rehearsal holds, where the parser's own reading answers it
   unchanged - so v1 is re-baselined here, as it was at 3c. The live run
   that closes (3), `live_parser5.jsonl` on fd88e48: 162/175 (92.6%),
   families 151/166, 3 wrong - 0 answers moved against `live_parser4`,
   and 277/277 identical to the offline rehearsal (median 1.22s). Next:
   (4). What is left of the slot dict: the
   parser still builds its route as one and converts at the end
   (`Scope.from_slots`), `compose.answer` and `refusals.unanswerable` still
   take `scope.to_slots()`, and the check functions still accept a dict
   from the tests - each is a consumer to move onto the Scope, not a
   second writer.
