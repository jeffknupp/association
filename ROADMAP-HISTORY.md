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

28. **Phase 3, step 2, the line and the companions (2026-10-09).** The
    fifth family and the largest, the one the types draft calls "the one
    that matters": one Fable agent at medium effort measured the five
    carriers of a line and the three of a companion on all 2,710 readings
    first - a threshold on 120, a floor or under-line on 39, two or more
    "N+ stat" pairs on 11 (and in all 11 the slot pair contradicted the
    words, as the draft predicted, while the relation had narrowed by the
    words already), a period line on 2, a companion's line on 23, 185
    companions beside a subject - and built the `Line` from the words,
    never from the pair. What moved: `reading.Line` on `Scope.lines` and
    `reading.Companion` on `Scope.companions`, seven slots and two record
    types gone (`ConditionSpec`, `PeriodCondition`), the point reader's
    own re-read of the number's words deleted, `router.Beside` deleted
    with the stages handed typed companions, `query/line.py` the one
    tagger with the period line's words blanked rather than cut,
    `WITH_WITHOUT_STATED` folded into the one table; `router.py` 214 lines
    shorter. Proved on all four populations through the projection with
    ONE enumerated move, the slice's fix: "most games with 20 pt,s 10 reb,
    5 ast" read its first line as 20 rebounds because the retired re-read
    could not pass the comma; it reads 20 points from the words now. The
    ledger FELL for the first time, 709 -> 702: "points" in the seven
    team-record wordings "when Maxey scores 20+ points", which the typed
    companion line reads without the word. What it taught: the companions
    could be typed with no second reader of a name, since the subject
    reading already read every phrase and only lacked the claims; `Won`
    and `Met` are not filters the words carry but the point's predicate
    and the pair shape's key, so the draft's rows for them are the
    shape's (open items 11, 12); step 1's floor harness had been reading a
    field dead since the period slice and reporting "identical" on errors
    - a harness that fails on both trees compares nothing, and is mended
    in the research repo; and three wrong answers sit on the seam the
    measure slice and the count shape own (a single "20+ points" on a log
    reads nothing; a count's later lines are dropped; a companion's line
    is read as the subject's), filed as P1s. Cost: no rebase (master had
    not moved) and a fresh four-population run on both trees.

29. **Phase 3, step 2, the measure (2026-10-10).** The sixth family, the
    one the lead split out of the line's slice: one Fable agent at medium
    effort (dispatched on the 9th under the day's exception) measured the
    seven measure slots and the point's `measures`/`aggregate` on all
    2,710 readings first - `stat` set on 1,004, under 69 spellings; where
    the model's key and the words' both stood (240) they disagreed on 58
    and the words won every one; 24 keys no answer-side vocabulary held,
    and the normalizer's `games_played` the one enum key nothing reads -
    and wrote one catalog of 42 keys with 390 spellings into it, in place
    of six vocabularies. What moved: `reading.Measure` on `Scope.measure`,
    seven slots gone, four alias tables and two modules' `re` imports
    deleted, `query/measure.py` the catalog and the one tagger, the model's
    required `stat` entering as context the words may confirm; `router.py`
    421 lines shorter. Proved on all four populations through the
    projection with the prompt and schema hashes unchanged, and SIX
    enumerated feed moves from the slice's one fix (and 13 more
    readings whose planned query carries the stat now while the result and
    answer hold): the stages had dropped
    the words' measure along with the model's key on whole-line readers
    where the stat-word list did not know the word, so "grayson allen 3s
    made last season" answered his points line; it answers his 3-pointers.
    The ledger held at 702. What it taught: a slot-era spelling cannot
    leave the typed value while sixty refusal sentences print it, so
    `as_typed` rides on the `Measure` by necessity and is named as such;
    the point's `measures` and `aggregate` are the compiler's names and
    their retyping is `Query`'s shape, RUN's input, so it waits for step 4
    (open item 13); a shot's value is the measure's, not a relation cell,
    because a stat naming the same shots arrives beside it; and the one
    reader of which stat a question names can hold the model's key as
    context without a single answer moving, which is the shape the
    subject's names will take. Cost: no rebase (master had not moved) and
    a fresh four-population run on both trees.

30. **Phase 3, step 2, the subject's own (2026-10-10).** The seventh and
    last family, and the first slice run on Opus after the one-day Fable
    exception: the agent measured the four name slots and the Subject
    record on all 2,710 readings first - the model's spans and the words
    disagree on 134 and the words win all 134; a stage still writes a name
    on 58; a player stands under the team kind on 23 readings and under
    the everyone kind on 11, so the kind is a fact the names cannot give -
    and typed `reading.Subject(kind, players, teams, position)` on
    `Scope.subject`, written by the one subject reading, which now claims
    the names' and the position's spans (2,621 claims on the 2,710). The
    stages are handed the names and write none; the lexicon took the
    family's words and `subject.py` holds no pattern; `_MODEL_SLOTS` is
    gone; `router.py` 338 lines shorter. The slice alone is identical on
    every population. Three fixes rode with it, each enumerated: a quarter
    read as the player who scored in it ("first quarter" was Tim
    Quarterman); a player's name after "vs" clipped to a team by a
    word-prefix match ("TIM" the Timberwolves); and "76ers", the one team
    spelled with a digit, split by the name reader into the word "ers" and
    no team at all - 18 feed answers move on that one, 12 to the question
    asked, and the recorded corpus holds it only in the reading, because
    the model's span had always carried the name production reads. What
    it taught: the feed population reads the words alone, so a fix the
    model's span was masking shows there and nowhere else, and the two
    answers that moved to a team's season line where one game was asked
    are what production already said - a P1 on the team relation, filed;
    the stages' last three name writes (the grammar's subject, a nickname's
    opponent, the quarter's player) are step 4's, since they choose an
    intent as they write; and a helper that nearly took a connection took
    the team index instead, which is how `con_in_the_reader` stays at 23.
    Cost: no rebase (master had not moved) and a fresh four-population run
    on both trees.

31. **Phase 3, step 2 closed (2026-10-10).** The closing slice, on Opus:
    with all seven families typed and their cells in the two relation
    tables, the agent measured every verdict the planner and the readers
    gave by a cell on the 2,710 answered questions (212 verdicts under 62
    kinds; 3 of the stated table's 32 rows never planned, 98 of the 138
    exclusion rows never hit) and, by construction, 68,794 verdicts over
    every distinct planned point under every cell variant, against a
    connection that raises on first use - then replaced the planner's
    slot-era checks with one: the exclusion tables keyed by the planned
    point as a record per shape, and `cells_unhonored` over the typed
    values' cells and the tables, read by the planner and by the answer
    side before it asks any reader. Twenty-one names and a parameter on
    twenty-nine readers went; the freeze test holds the four cell tables
    and nothing else, and the debt it carried since 2026-10-05 is zero.
    The slice is identical on every population with nothing ignored. The
    measurement found the step's last fluent wrong answers: the league's
    own read had no subject for six cells and the planner never asked, so
    "most blocks in a game 7" listed the postseason's highs - sixty-two
    feed answers now refuse by the cell's name, each listed. What it
    taught: a per-shape row needs three facts of the shape, not reasons
    alone, because the sentences a decline takes come in four forms and
    a fifth table was the one thing the freeze forbade; a check built by
    construction over every planned point and cell catches a table
    perturbation the population never reaches, which the three
    deliberate ones proved; and `Scope` stays until step 4 deletes the
    Route that carries it. Step 2 took two days, eight slices and eight
    agents (six on Fable under the 9th's exception, two on Opus), each
    measured before writing and proved on four populations through a
    projection of the typed part back to the record it replaced. Cost:
    no rebase and a fresh four-population run on both trees.

32. **Phase 3, step 3 (2026-10-10).** The Reading states the words
    nothing read. One Opus agent measured every content word of the 2,710
    readings two ways first - is it claimed, and does deleting it change
    the reading or the planned query - and found the two measures
    disagreeing on 344 of the 628: 76 words a pattern matched and did not
    need, 409 a rule read without claiming (293 of them the intent's own
    choice). So a claim became what a rule's reading DEPENDS on, measured
    by the rule itself (`span.needed` trims a tagger's claim by deleting
    each word and asking it again; `span.read_by` claims what a
    whole-question decision turned on), with the subject reading read
    once and never probed, and `Reading.unread` is the content words no
    claim covers, by one counting rule the ledger now reads too. The
    ledger reports every question where the two lists differ: 344 -> 3,
    all the subject reading's, and Jeff ruled 625 of 628 the exit with
    the three allowlisted by name. The count fell 702 -> 690, five fixes
    riding with the step and enumerated (a year range, a hyphenated age,
    a count over five bare lines - the yardstick's own answer - a stated
    line on a log, a spelling bug whose fix exposed one overcounted word,
    "against", kept by Jeff's call). What it taught: the deletion probe
    cannot see a word that suppresses a reading, so both years of "2024
    and 2025" counted as read while nothing read them, and leaving such
    words unclaimed is what surfaced #289 and #345 - agreement with the
    ledger is the check, not the goal; "a number among them is a bug" has
    its exception in the "100" that restates a rate; and the claim join
    hides a double read, so it is held by kind and count (48 today, 43 of
    them the intent's words over a tagger's, which step 4 makes one
    reading) rather than reverted, since the raise would fail 44 correct
    readings. Cost: about one millisecond more a reading, no rebase, and
    a fresh four-population run on both trees.

33. **Phase 3, step 4, slice (a) (2026-10-10).** The intent leaves the
    reader. One Opus agent measured the intent against the planned point's
    key on all 2,710 readings first and found the map many-to-many in both
    directions - `player_stat` plans four different keys, the player log's
    key is reached by two intents, and 118 readings plan no key at all -
    so the key the grammar names is what the words ASK, a `PointShape`
    on `Reading.asked`, and the planned point stays what the point IS.
    Every intent set became a predicate or a lookup on the key, the
    answering loop asks whether anything was asked rather than whether an
    intent is compiled, and the intent string survives only as the page's
    label from one table, held to the key by a test; intent-related sites
    in the query package fell from 558 to 159. The one enumerated commit
    made every shape's decline a cause the planner says in the identical
    sentence: 69 answers moved from "refused" to "fast" with their text
    unchanged, the class Jeff's rule permits. What it taught: the joins
    the ledger holds did not shrink, because 13 of the 17 intent-and-line
    joins are a line the lines tagger reads only because of the key - the
    grammar cannot name a shape from a typed line while the tagger waits
    on the grammar - so the key-dependent tagger rules move to the planner
    first, which is slice (b)'s work where the settle order is undone
    anyway; and the freeze that held 25 intent names now holds 25 keys,
    the names gone. Cost: no rebase and a fresh four-population run on
    both trees.

34. **Phase 3, step 4, slice (b) (2026-10-11).** The Route and
    `router.py` go. One Opus agent, reviewed by the lead in flight and
    presented to Jeff before the merge: the reader takes the names index
    and no connection (both ratchets at zero, the lists kept empty so a
    new violation still fails), the parser's last step recorded as a
    boundary on both trees before the old one went, the stages' rules
    that move what the words ask kept as one module over a typed working
    reading, the taggers run once in dependency order, and the Scope
    folded into a frozen base the Reading and every planned query inherit,
    with the cells declared once. The taggers read the words alone and one
    function per family in the point reader applies the key, because those
    rules change what the Reading says while the planner's check decides
    what a relation honors. The grammars read a typed line and a typed
    opponent through marks that keep every character's position, so the
    joins fell from 48 to 15 with no reshaping; the fifteen left each have
    a named owner. One correction in flight: the active-players set was
    read once a question, now lazily, and statements per question are
    measured at 5.16, unchanged. Proved identical on every population with
    nothing ignored but the twelve readings whose declined-child decision
    gave way to unread numbers, the P1 the ledger could not see until
    then. What it taught: a regex grammar can read typed values
    positionally, which is a bridge, and the end state is grammar rows as
    predicates over the typed values; a step's measurement harness is
    where the per-question cost shows, since the ratchets count sites and
    not statements. Cost: the lead's in-flight review and a fresh
    four-population run on both trees.
35. **Phase 3, step 4, slice (c) (2026-10-11).** The lead's own, three
    commits. The router's 201 tests re-seated as 456 question-to-Reading
    snapshot cases, one call of the parser's door each with the typed
    record as the expected value, generated by one script against the
    real names tables carried as test data (a sampled index read 54 of 430
    cases differently; a `tests/data/` is caught by the repo's `data/`
    ignore and was never committed - the one amend). The slot-era
    projection deleted, every family's `to_slots` with it, and the stage
    snapshot recording the typed Reading - proved by running the tree
    before with the new record functions swapped in, so the four
    populations compared with nothing ignored and held identical. Then
    the joined claims: the parent grammar reads the companions' phrases,
    the windows of games and a dated range as marks beside the lines' and
    the opponent's, the rules are handed the team words and a reached
    companion's "when" clause typed, a period rule's side effect that
    wrote a nickname as a player's team is gone, and the bench recognizer
    reads the typed measure; joins 15 -> 4 (#354's) on the 2,710 and 5 ->
    0 on the 628, the held table empty. Measured: the probed companion
    claims can land on a word a grammar row reads ("log"), so the marks
    take the phrases whole; the single-game window's stretch swallows a
    period's words, so it stays words; the window tagger reads windows the
    log row's words did not ("last 10 game", "last 10" closing a question,
    "last 4 away games"), so 14 readings' key moved to the log and 10
    answers with them, each named - a change for Jeff to keep or narrow.
    What it taught: a mark is only safe where the typed value's stretch
    is the words the row read and no other row's; and a side effect in a
    boolean check (a team written while asking whether players rank) is
    found the day the words it writes are claimed. Cost: the lead's day,
    three four-population runs.

36. **Phase 3, step 5 - the close (2026-10-11).** The live run
    `live_parser29` at `4de1801`, 277 of 277 equal to the offline
    prediction (276 of 277 model replies identical to the recorded ones),
    graded blind against `live_parser28` - the Phase 2 close's run, so the
    grading spans the phase: 167 of 175 and 156 of 166, as before; 4
    answers moved by values (a decline now said as a cause, a page label,
    two league-wide logs ordered within a game by athlete id since
    `634c2af`), 12 by wording, every grade standing. Then the review
    (Opus, read-only, no model; `~/association-research/reviews/phase3-2026-10-11/`),
    which ran the 628 and the 2,082 whole at each of the phase's 69 `src`
    commits and found every one of the 41 and 259 answer moves counted in
    its commit's CHANGES entry - the phase's identity claim holds per
    commit, which no step's proof had shown - and found the exit's regex
    clause false inside the reader (18 `re.` sites and the parent grammar's
    23 rows in `point.py`, `parse.py` and `line.py`; the ratchet counts
    modules outside it), `Reading.intent` still a cause fact the sayer keys
    four phrase tables on, the ledger at 702 of 2,411 in 405 against the
    documents' 690, 10 records between question and answer, and five new
    wrong or misleading answers, none a step's: "when playing 20 or more
    minutes" read by nothing, a stated minimum ignored, "40 point playoff
    games" refused for a false cause, "per 50 possessions" as totals, two
    kept slice (c) moves narrower than asked. For the next bisect, which
    commit holds which fix's source after the rebase that let one absorb
    its neighbors': `bfe09f7` (message: two ISSUES entries closed) holds
    #36's `compose/sentence.py`, #254's `compose/shots.py` ORDER BY and
    #337's `conditions.box_source` cache, with no CHANGES touch (the hook
    was bypassed); `6aa0dca` (#257) holds #262's and #297's; `d1078c8`
    (#315, "628 of 628, 2,082 of 2,082 ... identical") holds #285/#217's
    `compose/logs.py` and `say.py` and #336's `team_records.py` and moves
    13 recorded and 62 feed answers - their CHANGES entries are the
    CHANGES-only commits beside them (`f9b1ca2`, `63e66c5`, `e14c589`;
    `e360c81`, `04cb52d`; `b6661ad`, `774321f`). What it taught: a ratchet
    is only as wide as its population - the regex ratchet read 0 by
    counting the wrong modules, the unread-number rule by counting the 628
    alone (191 on the feed) - and a proof per step is not a proof per
    commit until someone runs every commit. Cost: the live run's half hour,
    the review's 85 minutes and 470k tokens, the lead's evening. Filed: 13
    entries. Phase 3 closed.

37. **The Phase 3 review's cleanup, worked in order (2026-10-11 to
    2026-10-12).** Jeff's ruling the night the phase closed: "work the
    cleanup list in order, (a) first". (a) the same night - the lead's
    `regex_in_the_reader` ratchet (point 12, parse 5, line 1), then one
    Opus agent's nine commits on the seam: a floor of minutes read, a
    stated minimum the tenth typed filter (`reading.Minimum`, read by the
    window tagger, honored by the rankings or refused by name), "percent"
    spelled out, a season type between a count's line and "games", both
    ends of a window named at once and a window in every season each
    refused by a cause, and two count/record fixes the floor exposed; 21
    feed answers moved, every one counted. The merge itself tested the
    ratchet: the branch deleted a point-reader pattern and the gate said
    GONE, shrunk to 11. (b) and (c) the next morning, one Opus agent, 53
    commits: the private-use range stripped; the claims ledger holding
    the feed as it holds the 628 (`scripts/ledger_held.json`, `--shrink`),
    and the feed's 177 unread numbers read one by one - 12 kinds, six new
    P1s, two reader fixes on the way; the intent label out of the cause
    facts (a `shape` fact, the key) and the sayer's tables; the decline
    sentences reworded from a slot list to what the words asked (5 of the
    628 and 130 of the 2,082, enumerated, `answered_by` unchanged, the
    slot-name readers deleted); CI holding every pushed commit to the
    CHANGES rule (unfloored over the phase it names exactly `bfe09f7`);
    and 44 test-only commits re-seating about 1,100 of 1,600 test-door
    call sites, the doors standing for the 250 left. The lead re-verified
    both merges against fresh runs of the source before them: every
    difference on the 628, the 2,082 and the 2,710 reconciled to the
    commits' enumerations, nothing unnamed. What it taught: a cleanup
    worked as a ranked list with one agent on the seam at a time merges
    cleanly, and each agent found two or more real bugs beside the ones it
    was sent for ("per 36 minutes" read as a floor; "stats by season" with
    no stat as one season's line; a count's line dropped beside a floor at
    the same number) - the review's list was the start of the sweep, not
    its extent. Cost: the lead's night and morning, two agents of about
    2.2 and 2.3 hours (565k and 604k tokens). Filed: #387-#394, all P1.
    Left: #385's remainder, Phase 4's first item.

38. **Phase 4, step 0 (2026-10-12).** The test doors go. One Opus agent,
    31 commits in 1.7 hours, rebased and merged at `e120fc4`: 29 test
    files re-seated on the typed Reading and the `PointShape`,
    `tests/slots.py` and `tests/shapes.py` deleted with their last
    callers, `tests/routed.py` kept to the helpers that take typed values,
    13 door-subject tests (42 cases) deleted, the suite 3,029 -> 2,987,
    and `measure.column_of` gone from `src` as the door's only caller.
    Proved two ways beyond the four populations (every one identical):
    4,094 of 4,096 unit-test calls identical, and every Reading the suite
    constructs recorded on both trees through a scratch plugin - 3 new
    Readings in the whole suite, all hand-built "other" cases whose label
    nothing reads. What it taught: a codemod that re-seats a test can
    leave it asserting that a value equals itself - five such tests from
    the cleanup's (b)1 were found by reading them, not by the suite - so
    the construction-level proof (what each test BUILDS, not only what it
    calls across a stage) is the check for test-only rewrites; and ast
    columns are bytes, not characters, which an accented name showed. The
    Phase 4 rows were accepted by Jeff the same day, with the lanes to
    run in parallel after this step; the lanes wait for Jeff's go-ahead
    while the box is busy with other work. Cost: the agent's 1.7 hours
    (633k tokens), the lead's re-verification. Filed: #395 (P4). Closed:
    #385.

39. **Phase 4, the lanes' first merges (2026-10-12).** Jeff's go-ahead the
    same evening; five Opus agents at once from `aaa0a8a`, each in its own
    worktree with a fresh before-tree run of its own, and the lead
    re-proving each merge on the merged tree in a chain - master, then
    lane B's tip, then C3's, then A's, then C2's, each lane's tree the
    next one's before tree, every compare on the lead's own runs. Merged,
    in order: lane B's step 1 (`7441c35`: `Answer.shape`, the page's 22
    renderers -> 7 by shape, every recorded answer rendered in Chromium
    before and after), lane C3 (`429c0b3`: `netpoints.py` and `shots.py`,
    9 statements, the ratchet's `_read_table` miscount fixed), lane A's
    first slice (`2af564e`: `reading.Aggregate` a closed enum) and lane C2
    (`a9ecbf6`: the periods', logs', splits' and standings' 11 statements
    to the relations, `standings.py` new). Every merge identical on the
    four populations and both ledgers with nothing ignored but lane B's
    new `answer.shape` field; the SQL ratchet 80 in 20 -> 79 in 17; four
    merge-time conflicts in C2's (`CHANGES.md`, the ratchet list, both
    import contracts, `AGENTS.md`), each a union of disjoint edits,
    resolved by hand and gated. Then, later the same evening, lane C1
    (`13304d1`: the season line's, the team seasons' and the team-games
    relation's reads, 75 in 14) and lane A's measures slice on top of it
    (`93016f3`: `Reading.measures` a `tuple[Measure, ...]`, proved by
    naming each typed list back through the catalog), the same way; the
    four follow-ups the C agents named went to one more agent; and lane
    B's step 2 (`ed737bd`: the label gone from the Reading, the Answer and
    the API, the decline sentence saying the shape in words - 11 of the
    628 and 219 of the 2,082 moved, each checked by the lead as the label
    swapped for its phrase and nothing else), with step 4 dispatched
    behind it. One incident: the follow-up agent gated a rebase with
    `--exec` running the suite, the exec's `GIT_DIR` reached the tests
    that run git, and they wrote `core.bare=true` and a test identity
    into the shared `.git/config` - reverted by the lead after checking
    each claim, the worktree rebuilt from its branch ref, one other
    agent's commit re-authored, and the tests now strip `GIT_*` from
    their environment (that agent's successor's commit). Lane C4 landed
    last (`edbc46e`): the SQL ratchet reads 74 statements in 12 modules,
    every one a relation's, and no `compose` module runs a statement -
    step 5 closed, with the ratchet's door rule widened on the lead's
    call (#397), for Jeff to confirm or revert. Open for Jeff from
    lane A: the aggregate is not a function of the shape alone (27
    readings in three keys move under either design), and the typed
    `Query.measures` follows step 5. What it taught: merging each lane
    onto the previous lane's tip before that lane had reached master kept
    the chain moving (one lane's gate runs while the previous lane's proof
    runs) at the price of a redo had a proof failed - none did; and an
    `AGENTS.md` that documents the conflict markers trips a resolver that
    greps for them anywhere, so the check is anchored to a line start.
    Cost: the eight agents' 6.4 hours (2.61M tokens) and the lead's eight
    re-verifications of about ten minutes each. Filed: #396 (P1), #397
    (P4). Rewritten: #110.

40. **Phase 4, steps 4 and 6 (2026-10-12, late).** The last two lane
    steps, each one Opus agent, each merged by the lead after its own
    re-verification on the merged tree. Step 6 (`feb3c4a`, lane A): the
    grammar reads typed values, not marks - `lexicon.GrammarText` carries
    what each tagger typed at each character, the parent rows are
    predicates over it and the positional child grammars regexes over a
    spelling of the typed values alone; every mark, the #383 intake and
    the reader's 17 `re` calls gone (15 moved to the lexicon by
    the first slice, 2 with the marks), the regex ratchet 17 -> 0 and
    folded into one held-empty rule for the whole query package; the
    2,710 readings held identical with their claims (a new harness,
    `claims_pop.py`, since the readings' record never held a Claim) and
    two enumerated fixes beside it (#354, #396: five feed readings, two
    answers). Step 4 (`abbfb74`, lane B): the ten records between the
    question and the answer measured by a census of every construction
    and read on the 628, then four - `Planned` folded into the plan's one
    value (`compose.plan.Verdict`) and `Reply` into `Answer` - with every
    population identical and 4,710 of 4,710 calls identical across the
    fold. What it taught: a merge with no conflicts still needs the gate
    and the reader cases checked (two lanes had regenerated
    `reader_cases.json` in different fields, and git joined them; both
    times the check read 0 moved); and a proof the harnesses cannot see
    (claims, record constructions) is written as a harness first, not
    asserted. Cost: the two agents' 2.5 hours (1.0M tokens), the lead's
    two re-verifications. Closed: #354, #396 (P1), #397. Open for Jeff:
    the ask's spelling alphabet (a mark under another name, or not), the
    `PointShape` as a field of the query family. Then step 7's
    documents (`6b38bb3`: the architecture page rewritten for the four
    stages, AGENTS.md dated, the done table measured, #386's docstring
    sweep) and the live run, the lead's own: `live_parser30` on
    `fe806a6`, 167/175 and 156/166, parser29's score to the question,
    four decline sentences reworded and nothing else moved - the harness
    itself needed the phase's two renames (`Answer.shape` for the label,
    `Agent.query` for `Agent.planned`) before it could record a run. The
    review was dispatched 2026-10-13.

41. **Phase 4 closed (2026-10-13): the review.** One Opus agent, read-only
    at `fe806a6` against `d4f04b9`, 64 minutes, every population re-run
    itself and both answered populations at every one of the phase's 39
    `src` commits and the nine merges three-way: every move the one a
    commit names, the live run equal to the offline prediction 277 of 277
    with the model's replies equal to the recorded ones, the deletions
    done, the documents close. Findings transcribed: 1 P1, 1 P2, 1 P3, 4
    P4 (one fixed on the spot), one for the research repo; with Jeff's
    two P2s from his own queries the same evening (#400-#407). Positions on
    the five calls: derive the aggregate (1 reading moves, not 27), keep
    the door rule, the spelling alphabet is no mark, keep `conditions`
    counted, keep the shape derived. What the phase cost end to end: nine
    agents' 7.9 hours (3.1M tokens) on the lanes and the documents, the
    reviewer's hour, and the lead's twelve re-verifications and one live
    run; one incident (the rebase's exec), repaired and guarded. What it
    taught beyond entry 40: a harness that reads a record field the phase
    deletes fails on every row at once, which reads like a broken tree
    until the traceback is read (`run_live.py` on `Answer.intent`), so a
    phase that deletes a field lists the harnesses that read it with the
    field; and a before-tree compare of a decline rewording needs the
    label's paths ignored at every record they stood at (seven), or the
    whole population reads as moved. Next: Jeff's call - the reviewer
    recommends the P1 list under the standing proof rules, with the
    aggregate's derivation and the worded declines folded in.

42. **The P1 sweep (2026-10-13): four lanes along the files' seams.**
    Jeff's call after the close: a small swarm on the 45 open P1s, one
    Opus agent per seam (spans/windows/cuts, lines/measures/count grammar,
    subject/names/companions, the team relation and compiler), each in its
    own worktree from pushed master, the phase's proof rules standing, the
    lead merging every push on the merged tree and re-proving it on fresh
    runs of the four populations (32 merge runs, `master-sweep1..32`).
    Jeff stopped and restarted all four once (fresh agents on the same
    worktrees, a state check first) and cut lane L after its last batch.
    What landed on master `c93fffa`: 77 fix commits; of the lanes' 46
    entries 30 closed, 12 narrowed to a named remainder, 4 untouched; 25
    new entries filed on the way, 13 of them P1, most on the team seam
    after lane T finished; open P1s 45 -> 22; 514 of the 2,082 feed
    answers and 99 of the 628 moved against Phase 4's close, each named
    in its commit, the direction checked at every merge (a fluent wrong
    answer to a refusal allowed, never the reverse - the "nba" fix was
    gated on a measure read from the words for that reason). D4 lifted
    twice by Jeff for shapes the measurements asked for (the 26th and
    27th keys), with "what a new shape brings" recorded as the rule.
    What it cost: nine agent runs over about nine hours, and the lead's
    32 verifications. What it taught: a lane's held-ledger and
    reader-cases files cannot be merged by taking a side (a lane's entry
    survives nowhere else; a case recorded without another lane's field
    fails on the merged tree) - regenerate both on the merged tree and
    union the held lists at the measured counts; keep-both ISSUES merges
    resurrect deleted entries, so the resolver is three-way and the close
    cross-checks every listed entry against its GitHub state (nine found
    and removed); the closed cause tables (`reading.CAUSES`, the sayer's
    phrases, the test's examples) conflict whenever two lanes add a
    cause, and the resolution is both sides minus what master deleted;
    and new P1s appear as fast as old ones close because each fix stops
    at the next seam (a reader fix exposes a relation gap), which is
    Phase 5's step 6 to count rather than a sign the sweep went backward.
    A second dispatch the same evening (two agents on the new `opus-medium`
    type, Jeff's default from then on): lane T2 closed #416 and #426, fixed
    an unfiled P1 and was cut after #426; lane L2 closed its five (#422,
    #428, #408, #370, #114) and stopped as briefed; nine merges (runs
    33-41), 61 feed answers moved against the first close, open P1s 22 ->
    19. Two resurrected entries caught by the same GitHub cross-check.
    A third dispatch on 2026-10-14 (lane T3, the team seam's P2s): #341,
    #173, #427 and #372 closed - #372's remainder as the 28th key under
    D4's third lifting - #401 narrowed to the reader; six merges (runs
    42-47), 21 feed answers moved; open P1s 21 of 195, every lane's list
    done and the remainders unowned, which is Phase 5's step 6 to own.

43. **Phase 5 opened (2026-10-14): the rows accepted.** Jeff accepted the
    eight rows drafted at the sweep's start, with the figures re-measured
    on `520a94b` (87 of 1,807 answered feed questions and 9 of 603 carry an
    unread word of meaning; the ledger at 3,023 and 86, 700 and 7) and the
    stale lane assignments rewritten, and decided the review's five calls
    the same morning (D5-D11): the aggregate derived and the point's
    `PointShape` one field, dispatched as one agent; the rest kept as the
    reviewer left them. Order: step 1 (the lead, alone), then 2 and 5 in
    parallel, 6 by reach, 7 last. Open P1s 21 of 195 at the open.

44. **Phase 5, steps 1, 2, 4 and 5 (2026-10-14): the defaults become
    refusals, the declines become causes.** Step 1, the lead's, in one
    commit (run 49): a word that carries a meaning the reading defaulted
    past - a number, a stat, a window word, a date - is a cause the planner
    says over a planned query, 6 of the 628 and 82 of the 2,082 answers
    moving from a fluent answer past the word to the refusal naming it; the
    ledgers fell as the refusals claimed their words (700 -> 690, 3,023 ->
    2,879). Step 4's code, one agent (runs 48, 50): the point declares one
    `PointShape`, the aggregate is derived from it and the catalog's yes/no
    facet, zero moves but a refused Danish football question. Steps 2 and
    5, two agents in parallel (runs 51-58): the grammar's typed-value drops
    at 0 live with 31 held by rule or reason, and every planner decline a
    cause with facts, 192 worded sentences to 0 with every rewording named
    and six literal "None"s fixed. Three harnesses in the research repo
    are the exits, each at 0 on `4f82e1b`. What it taught: a harness and
    the parser must share their guards or the exit is measured by a looser
    rule than the code (four guards were found that way: a leftover of the
    read measure's words, the read quarter restated, an ordinal after a
    month, a slashed figure); a cause said at the point's place preempts
    the floor and the unsupported check, so it is said at the decline's
    place (21 "finals" questions showed it); and the alignment tool's point
    detection had been blind since the intent label went (Phase 4, step 2),
    so every "calls aligned" figure since then was computed with the point
    left in - now fixed. Open P1s 19 of 194 at the night's end; step 6 is
    the queue.

45. **Phase 5, step 6 (2026-10-14 night): the P1 queue, three lanes
    under D12.** The proof rule relaxed first (D12: one feed run per push,
    sharded to 77 seconds; the ledger's pass sharded to 28), after four
    agents' transcripts showed 55-70% of their wall clock in proof and
    gate and 1-2 minutes of work per 8-10 minute commit. Three
    `opus-medium` lanes on the sweep's seams (the subject reading; the
    taggers and the count grammar; the team relation), 14 merges (runs
    59-72), each verified by the lead on the merged tree: 12 of the 19 open
    P1s closed, 5 narrowed to refusals by name, 4 unfiled P1s fixed on the
    way, 2 filed at the end (#440, #441); 252 feed answers moved, 157 of
    them fluent answers past a word becoming refusals that name it and 77
    becoming reads; the feed's fluent answers 1,653 -> 1,506. The step's
    cadence under D12: 6-8 minutes per commit, about 24 commits an hour
    across the lanes. What it taught: a keep-both merge of ISSUES.md keeps
    both versions of an entry a lane rewrote when a later lane's branch
    still holds the old text (two duplicated and two closed ones
    resurrected, all caught by the close's cross-check against GitHub and
    the duplicate-heading check); and the agents kept more
    proof than D12 asked, running the corpus and per-commit feed runs for
    attribution, so the saving went to cleaner enumeration rather than
    time. Open P1s 2 of 189 at the close.

46. **Phase 5, step 7 (2026-10-14 night): the live run.** `live_parser31`
    on master after step 6 (the lead, ollama's one caller): 167/175 and
    156/166, parser30's score to the question, with 7 answers moved
    against parser30 and each graded. It earned its place: two of the
    seven were regressions no offline proof had flagged, because both
    moved in the allowed direction on paper - a correct answer refused by
    name ("three points made" read as an unread stat beside the
    3-pointers the tagger took, step 1's guard) and a correct answer
    declined by a cell (the sweep's #352 stopped dropping a window on two
    teams' meetings, and the meetings read had no window to honor). Both
    fixed on the same tree (the guard by stem; the meetings read slicing
    the last N from its one read, "their last 10 of 60 meetings ... the
    Lakers won 6-4", the key's own reading), 3 corpus and 3 feed answers
    moved, the score taken again. What it taught: "fluent to refusal" is
    the allowed direction only where the fluent answer was wrong, and the
    offline compare cannot tell a correct answer from a wrong one - the
    yardstick's key can, which is why the live run closes a phase.
    Remaining in the step: the independent review and the AGENTS.md
    compaction.

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

47. **Phase 5 closed (2026-10-15): the review, the compaction, and what
    the review found.** One Opus agent, read-only, reviewed the phase as
    Phases 3 and 4 were reviewed: both populations at all 62 `src` trees
    of `520a94b..3dc37821`, every move named by its commit, every merge the
    three-way composition of its parents (nine feed questions two lanes
    both moved recorded nowhere - filed), the live run equal to the offline
    run on 277 of 277, D12 safe. Its verdict against the rows: the exits
    hold as the harnesses measure them, and the harnesses measure less
    than the rows say - step 1's unread-meaning harness shares the
    parser's guards, so its zero is self-consistency; step 1's first
    classes refused correct answers no population held ("top scorers",
    "who may lead the league", a list of stats on a log), and 15 of the
    ledger's held unread-number questions answered fluently past a glued
    digit; the open P1 list was 9, not 2, once the section was read by its
    tags. The lead fixed the classes the same night (`f365d627`: 21
    refusals back to correct answers, 13 fluent answers past a glued digit
    refused by name, the refusal said after the floor), adopted the
    review's grade-aware compare as a standing check on every corpus
    compare - the check that would have caught both of step 7's
    regressions offline - and repaired ISSUES.md (re-sectioned by tag,
    three lost entries restored, eight filed, GitHub aligned). A second
    agent compacted AGENTS.md under its rules: 3,067 lines to 1,800, every
    one of 474 rule sentences mapped to its place with a check that was
    watched to fail, 51 paragraphs of provenance quoted verbatim into this
    file's last section, the review's nine inaccuracies corrected. What
    the phase taught, beyond its rows: a refusal rule proved on the
    recorded populations alone is proved on what people typed there, not
    on what they will type - the live run and a probe outside the corpus
    are the only checks on the other side; "fluent to refusal" is the
    allowed direction only where the fluent answer was wrong, which a
    grade can tell and a diff cannot; and a keep-both merge of a ranked
    list needs a cross-check at every close. What it cost: eleven agent
    runs over two days (the sweep's three dispatches, step 4's one, steps
    2 and 5's two, step 6's three, the review, the compaction), the
    lead's step 1 and 75 verified runs, one live run. Open P1s 9 of 194
    at the close; the review's next-phase list is recorded in the Phase 5
    section's status.
48. **The Phase 5 close's coverage row was wrong, and the narrowing restored
    wrong answers (2026-10-15, the lead).** The done table's coverage row
    recorded `unread_meaning.py` at 0 / 0 from `bd5a9aee`; the narrowing of
    step 1's classes (`f365d627`) landed after that measurement and was never
    re-measured. Re-run on the close baselines: 22 of the feed's 1,693 answered
    questions and 4 of the corpus's 608 carry an unread meaning word. The
    narrowing's entry said it restored "21 refusals back to the correct
    answers the classes had refused"; a reading of each answer finds 17 of
    the feed's 22 fluent WRONG answers - "last time chris paul dropped 20
    assists in a game" answers his season high, "scottie barnes last triple
    double" the count of them, "tyler herro last week" his season line,
    "jaylen brown 2022 nba final stats" his regular season, "James harden
    rebound and assist vs POR" his rebounds alone, "toronto raptors points
    off turnovers" their turnovers, "lebron james first quater log" the <!-- codespell:ignore quater - the feed's question as typed -->
    whole game's log. Filed as five P1s the same day; the harness now holds
    every flagged question by text with its grade and fails on a new one
    (research `17100a4`). The lesson for the proof rules: the offline
    compare sees a refusal become fluent and cannot tell a right answer from
    a wrong one in EITHER direction - the lead graded 21 moves by eye and
    got 17 wrong - which is what Phase 6's graded sample is for.


49. **The comment sweep (2026-10-15, lane p6-comments): the source says
    what the code does, with no history.** Jeff's rule for the sweep: a
    comment, a docstring and a document describe the code that exists and
    why it is as it is; history lives in the changelog, this file and
    ISSUES.md. One agent, from `abdc6cba`: every comment and docstring
    under `src/` that narrated the router, its slots and stages, the
    templates, presenters and adapters, the `Scope`, the fall-through
    agent, an intent's name, a phase, a step, a date or a commit was
    rewritten as a present-tense reason or deleted (about 2,400 lines out,
    1,900 in, over ~90 modules), each commit proved comment-only by
    `~/association-research/stages/ast_equal.py` (the AST with docstrings
    stripped) and every push by the feed against a fresh run. Moved here
    rather than kept in the code: the 362 `versionadded` bodies that said
    where a symbol lived before it was public (`git show abdc6cba` holds
    them); the measurements the module docstrings carried of each family's
    typing (`stages/span_family.py`, `window_family.py`, `cuts_family.py`,
    `line_family.py`, `period_family.py`, `measure_family.py`,
    `route_family.py` on the 2,710 readings), which keep their numbers
    where they justify a design and lose their dates; the dated "Jeff's
    finding" attributions in the web page. The public surface lost the
    router's names in one breaking change (7.0.0: `--model`,
    `DEFAULT_MODEL`, `Agent(model=)`, `ModelUnavailable`, the health
    endpoint's `normalizer` key, the history file's `model:` line), and the
    strings the source writes lost theirs in enumerated moves (the
    subject's and the parser's decision reasons, 62 and 61 feed readings;
    the bench-points refusal, 4 feed and 2 corpus answers; 33 reader
    declines). AGENTS.md lost its dates and phases, keeping its "(history)"
    pointers. What it found wrong rather than old: the answering loop's
    docstring and AGENTS.md on `answered_by="refused"`, AGENTS.md's
    "harnesses held at 0", a docstring naming a parse function that does
    not exist, two comments naming helpers that do not exist, and the CLI
    help naming a template.
50. **Phase 6 accepted (2026-10-16) on step 1's numbers.** The feed sample -
    425 wordings keyed blind with the SQL beside every answer, graded blind,
    every numerator verdict re-read by the lead against the warehouse and the
    71 moved toward acceptable read by Jeff, who reversed three - gave the
    project its first correctness rates on real questions: of 243 answered,
    23.0% wrong, 26.3% partial, 50.6% correct; of 169 refusals, 46.7% a false
    cause, 36.1% a true limit of the system on a read the data answers, 17.2%
    the right cause. Jeff's readings became D13 (a question naming no season
    reads the career where its words imply history), D14 (a stretch of games
    includes the playoffs; a stretch of seasons stays), D15 ("without Y" is
    every game Y did not play for X's team), a standing order that garbage
    wordings leave the populations, and the priority: the honest-limit
    refusals, what the data answers and the system cannot produce. Step 2 was
    dispatched the same day in two lanes along the files' seams.
51. **Step 2's sayer lane landed (2026-10-16, master `17d87c49`).** The
    first of the two lanes: the three-stat line now carries the full box line
    beneath its sentence wherever the words asked the line (#471; the
    sentence's figures proved kept by a script, 349 of 349 feed answers, 58
    of 58 corpus), a quarter's or half's figure carries the period's whole
    line, a name reading beneath a table stands on its own line, and a count
    of games after a half's stats is honored (#473 closed; three fluent wrong
    figures in the feed corrected). The lead's runs 79 to 80: feed 419 of
    2,082 moved, corpus 71 of 628, every move the lane's changelog
    enumerated and no reading, planned query or ledger count moved; the 18
    grade-aware flags each lines added under an unchanged sentence. Twelve
    of the sample's 14 three-stat partials now show what the key requires;
    the two left are a player's log, the span lane's files. The lane filed
    "ecf" read by nothing (#475, P1).

52. **Step 2's span lane landed (2026-10-16, master `b66df9a0`).** D13 as
    ten rules in the span tagger, each the kind of history words can imply
    (an opponent, a pair, a companion who played, a holiday, a count over a
    line, the single game's high, an inactive player asked his line, ...),
    standing under the keys that read over a career, with the guards that
    keep a career off; D14 on the games relations and on two teams'
    meetings; "last N meetings" counted. The lead's runs 80 to 81: feed 472
    of 2,082 readings moved and 363 answers reworded, every one fluent on
    both sides and 39 of them a "no games in 2026" sentence that now
    answers over the career; corpus 61 readings and 39 answers; 15
    grade-aware flags, each the yardstick's own career reading but F153,
    whose rows are identical under a career heading. Forty of the lane's 57
    sample ids match the key. D15 was measured (29 feed moves; FS227 then
    exact) and held because its two sentences live in the sayer, which the
    other lane owned until it landed; a follow-up lane takes it with the
    period reads under D14 and the D13 decision sentence. Issues #366 and
    #446 closed; #476-#479 filed.

53. **Step 2's follow-up lane landed (2026-10-16, master `e8f3d52e`),**
    closing step 2's three decisions on every relation: D15 over one season
    with its sentences, D14 and D13 on the period reads, the D13 default
    said with the wording that asks one season, and the player log's full
    line. The lead's runs 81 to 82: feed 554 of 2,082 moved - 269 the new
    sentence alone, 211 the same figures with columns added, 65 the
    with/without and period figures the decisions move, 3 period refusals
    answered; corpus 96, the 34 grade-aware flags each the sentence or the
    columns but nine period reads that now match the yardstick's own
    readings. Issues #471, #472, #474 closed; #480, #481 filed. Two calls
    left to Jeff: a "without" naming no season where the two are not
    teammates this season, and the regular-season-only career read where
    the keys count the playoffs (#231).

54. **The subject-seam P1 lane landed (2026-10-16, master `a1860237`),**
    after the lead's curation of the feed (twelve wordings that are neither a
    question nor a request out, `bad8e4ac`): a companion's role read from
    its own clause (a denial is an absence), a companion's bare number
    refused by the stat it lacks, a word near both a team and a player asked
    about, a word beside the model's name never read alone as that name.
    Eight feed answers moved, one corpus reading, no graded answer; FS339
    matches the key. Four entries filed for the lexicon lane with the lines
    they need (#482-#485).

55. **The lexicon-seam P1 lane landed (2026-10-16, master `659f6fee`),**
    the last of step 2's five lanes: four refusals by name (a round's
    abbreviation, the Finals in the singular, points scored one way, a
    misspelled quarter read and said), two stats on a one-stat shape, a
    player's wins, "total" on the season ranking, "top N games" as the
    highs, "with no X", and a coverage floor over a whole range. Feed 57
    moved, 41 in the answer, no graded answer; the unread-meaning list 16
    to 3. The merge's keep-both resolution of ISSUES.md brought back four
    entries the subject lane had deleted; cross-checked against GitHub and
    removed (`f7a97f1c`). Open P1s: 6, three of them Jeff's lifting.

56. **Step 4 landed (2026-10-17, master `7ac32895`) and the external review
    was recorded.** The homograph harness traces every name to the word it
    was read from and holds four grammar-sense hits by text; the rule, read
    once in the subject reading, names nobody from a month in a date or
    from "best" and "head"; a start or bench role beside an absence is
    kept; "jimmy butler" is Jimmy Butler III. Five feed moves, the corpus
    identical. The same day Jeff passed on an independent review of the
    code and the process (`reviews/phase6-2026-10-17/` in the research
    repo, with the lead's response): its process findings - vertical lanes,
    no lane edits to `ISSUES.md`, the per-commit chain cut to D12's letter -
    were applied at once; its code findings (`Reading.point`, the widened
    span's downstream traps, a keyed-score proof, a token-stream reader)
    are the work after step 5, with seven calls put to Jeff.

57. **The seven calls decided (Jeff, 2026-10-17): D16-D22 and the order
    of work after step 5.** The proof moves to the keyed score over the 702
    graded questions with the ungraded moves classed (D16); the compiler's
    own sentence stops answering the unread shapes (D17); a flat table may
    pass the complexity gate with a reason (D18); the five slow hooks move
    to the push and every ratchet is one-way (D19, the ratchets landed the
    same day); the changelog is generated from the commits at release
    (D20); a day's constrained-AST experiment, offline (D21); Phase 7 is
    the token-stream reader, gated by the keyed score and required to cut
    net lines (D22). After step 5: the keyed-score script and the
    `sentence.py` fix, `Reading.point` flattened, the widened-span traps
    as one vertical lane, the playoff round lifted and wired.

58. **Step 3 landed (2026-10-17, master `622d9d4c`).** The refusal names
    the right cause on 26 more of the sample's 79 false causes (28 were
    answered by step 2's lanes; 25 are held by wording, 13 of them the
    subject seam's): three new causes, three facts no table holds, a said
    default for a career high's stat, a misspelled "last", the season line's
    attempts, "but" and a denied role, the denial guard deleted, rebuilt
    games counted under a yes/no stat. The first merge scored by the keyed
    score (0 regressions, 28 candidates) and the widening harness (2 ties
    at a high's cutoff, #476's class). Issues #448, #483, #484 closed; ten
    findings filed.

59. **Step 5's live run and re-grades (2026-10-17).** `live_parser32` on
    `ea6695b1`; 99 of 277 moved against parser31; four blind graders over
    the yardstick's 71 graded moved wordings and the sample's 205 moved
    answers; 12 unsure rows settled against the warehouse. The sample's
    rates since step 1: wrong 23.1% -> 13.9%, partial 26.4% -> 21.5%,
    correct 50.4% -> 64.6%; false cause 46.7% -> 27.6%, true limit 36.1%
    -> 41.8%, right cause 17.2% -> 30.6%. The yardstick 167 -> 161 of 175
    and 156 -> 150 of 166: seven ten-row logs held at the standing
    standard and filed, eight real moves down named in the row (three a
    period read's 2016 exclusion, Jeff's call; a made-threes count over
    rebuilt games, P1; "vs <player>" on a log, P2; two Jeff's calls on D13
    and D15; one unsure).

60. **Post-step-5 lanes 1 and 3 landed (2026-10-17, master `777032d9`).**
    Jeff took the external review as Phase 6's review. Lane p6-sentence
    (D17, merged `99a6452c`): the unread rows shape deleted - its sample
    ids were 5 of 5 correct after D13, so they were routed to the highs
    reader, not refused as D17 said - and the game-level ranking of
    players read by `rankings.read_game_ranking` and said by
    `say.say_game_ranking`; keyed score 12 graded moves, 4 flagged
    (FS247/263/403/415, each the key's figures, columns dropped), FS214
    and FS392 to the key, no refusal<->fluent move. Lane p6-traps
    (merged `777032d9`): #501, #476, #477, #478, #480 and the widening
    inventory's span_vs_body and caveat_repeated, inventory 28 -> 24 on
    the feed (13 whole-tie false positives, 11 floor flags) and 3 -> 0 on
    the corpus; keyed score 40 graded moves, 9 flagged and each read
    against its key (F160 434, FS334 all four 29s, FS354 33 of 30 - no
    trade dates). The lanes met on one rule at the merge (a whole tie
    listed; one too big to list said by its count) - no textual
    conflict, one combined-tree test. Found in passing: `check_fast.sh`
    exited with pytest's status whatever the hooks said (fixed
    `9f9d557b`); two of p6-sentence's commits are red on the complexity
    hook in history.

61. **`Reading.point` flattened (2026-10-17, master `049a027d`).** Lane
    p6-flatten, alone on the seam: the point's fields are written onto the
    parser's Reading (`reading.POINT_FIELDS`), its verdict one field
    (`point_verdict`: read, `PointDeclined(why)` or the `Cause`); 33 src
    and 80 test sites of the nested point gone, `src/association/query`
    +94 lines (the new types' docstrings; no net cut - the gain is one
    record, and `reader_cases.json` 118,491 -> 88,205 lines). Measured
    first: the outer Reading and its point disagreed on `span` (4 corpus,
    10 feed), `lines` (5 feed) and `measure` (10 feed), the point's value
    standing in each, since the planner planned from it. Proved under a
    record mapping (`stages/flatten_map.py`): 628, 2,070, 2,698, 578 and
    8,567 calls identical, a perturbed span caught. One real gap found and
    fixed before it (`cfbe6e77`): the point reader's own count career was
    said with no D13 sentence - 4 corpus and 8 feed answers gain it, the
    planned query identical on every question; the lead's re-proof
    confirmed all 12 as the sentence appended, F133 and F098 kept.

62. **The playoff round (2026-10-17, master `0cf5a6c9`).** Lane p6-round,
    alone on the seam and the fetch path, D4/D11 lifted for it. Surveyed
    first: ESPN serves a `playoff` series object with the wins as of each
    game for every postseason 1989-2026, a round type on the scoreboard
    from 2004 and a round-and-game note from 2009 - #10's "the games carry
    no round label" was true of our table, not of ESPN. `real_games` gains
    `playoff_round`, `series_game`, `playoff_conference`, derived from the
    bracket (15 series a postseason, 8/4/2/1); the derivation agreed with
    all 1,915 typed games and 1,610 round notes, so no "derived" caveat;
    the fetch parses ESPN's own fields for the backfill's check
    (`scripts/backfill_series_games.py`, `check_playoff_rounds.py`, not
    run). The lead rebuilt `real_games` on the warehouse before the merge
    (43,353 rows, unchanged; 3,076 rounds) and re-ran master's baseline on
    it: 628 identical, 9 feed moves from row order alone (shot-chart file
    names, filed). The merge: keyed score 23 graded moves, 0 regressions,
    12 sample ids to their keys (the lead checked FS069, FS103, FS252,
    FS386); feed 91 refusal->fluent (the lane recomputed every one from
    the box scores) and 32 refusal->refusal, corpus 2; widening 0 flags;
    feed ledger 2,540 -> 2,521. Fixed with it: a team's log, record, best
    game and quarter answering the whole postseason under a round, a
    team's record over a teammate's line dropping every cut but venue and
    opponent, quarters named together refused by name, "kareem abdul
    jabbar". Open for Jeff: whether a round implies the career (FS189).

63. **2016's period reads by per-game completeness (2026-10-23, master
    `2df2c296`).** Lane p6-complete. The 2016 fault was measured first as
    absent rows at the source (a live refetch returns the same 477 rows of
    400827996), per player-game, not per season; `fetch/repairs/plays_complete`
    records per player-game and stat whether the play-by-play adds up to the
    box (628,074 rows; 2016 complete on points in 90.4%), and the period log
    reads count a game of an under-threshold season where every column read
    adds up, naming the rest per player. The shot frame, a shot's value and
    the period line's rules moved to `nba/` so fetch and query sum the same
    line. The lead loaded the table into the warehouse before the merge and
    re-ran master's baseline on it: 5 corpus and 5 feed answers moved, all
    toward their keys - F028 and F058 and the sample's FS027 now exact
    (138/19, 117/26), F059 383 over 28 (the rest the playoffs, #231), FS078
    7.0 over 32 against the key's 7.0 over 33. Attempts are read by the same
    flag (the shot table, 89.3% complete in 2016) - the brief had kept them
    out; the lane measured and the lead agreed.

64. **Period reads keep every game and state the bound (2026-10-23, master
    `3f074179`; #525, #526, Jeff's call).** Dropping a game whose columns did
    not add up moved graded answers away from their keys (the keys come from
    the same plays); so every game counts, `plays_complete` holds the signed
    gap per column (box less plays), each column the answer shows says its
    bound ("1st-quarter assists may be short by up to 2: on 2016-02-10 ..."),
    and a read refuses only where more than one game in ten miss in a column
    read (2004 first-quarter assists stay refused at 70%). The season tables
    (`PERIOD_RECONCILIATION`, `PERIOD_AGREEMENT`) are retired. Moves: corpus
    15, feed 33, toward the keys (F058 117/26, F059 29 games, FS078 exact
    231/33; F060, FS016, FS208, FS381 at the key's counts); one refusal (a
    Giannis log, over an unasked column, filed). The local merge commit
    deployed before the live load, so the site's period reads failed
    17:34-17:37; Jeff: that downtime is acceptable, add no process for it.

65. **D23's pilot: the span default's other reading offered as a ready
    question (2026-10-23, master `64083636`).** `Reading.alternatives`
    (`reading.Alternative`, kind `span`), each wording re-read by the parser
    and offered only if it reaches the alternative and plans (631 offered on
    the populations, 0 fail; 566 answer the other reading); a button under
    the answer, "Also:" in the CLI. Of the measurement's 24 span cases, 14
    now offer a question reaching the key's dominant reading, 2 with figures
    off. The inline second figure measured at +13.8% mean time, not built.
    Fixed on the way: "No season was named" over a named range, the D13
    sentence on a question back, a raw error on a career ranking with no
    career form (`no_career_ranking`, CAUSES 68). No answer regressed.

66. **D25's three classes offered (2026-10-23, master `72efa203`).** Lane
    p6-alts2: the season type ("including the playoffs"), a named round or a
    series game with no season ("over his career") and "last season" in the
    off-season ("2025-26 season") are offered as alternatives beside today's
    default; `Alternative` reshaped to (kind, Span, question). No answer
    text, result or plan moved; 0 graded moves. Offers: feed 495 -> 1,040,
    corpus 135 -> 283; answers offering 2 at most (feed 277, corpus 61) -
    the first build offered 3 on 10 answers where "his last game" (both
    types, D14) and "including the playoffs" interacted, so no season type
    is offered beside a latest meeting. Exit: 14 of 16 offered questions
    reach the key (F062 2.5/31, F059 38, F150 10 career game 4s, FS189 11
    Finals games, seven of eight "last season"); F149 waits on D24's
    every-row log, FS134 on the ranking's qualifier. Three reader bugs the
    offered wordings exposed were fixed. #231 and #519 closed as answered
    under D25 (provisional).



## AGENTS.md's provenance, folded 2026-10-14

Phase 5, step 7 (Jeff's call, 2026-10-14) compacted `AGENTS.md` to its rules: each rule a short
paragraph with its one-line reason. The dated stories the rules carried until then - what was
measured, when, in which commit, the counts at the time - are kept here VERBATIM, as they stood in
`AGENTS.md` at master `3dc37821` (3,067 lines), under that file's section names, each paragraph
with its old line number and where its rule stands now. A rule in `AGENTS.md` that ends
"(history)" points here. The figures are as of the dates they give, not today's: today's counts
are in `AGENTS.md` and the files it names (`scripts/ratchets.json`, `scripts/ledger_held.json`).
The whole old file is `git show 3dc37821:AGENTS.md`; the mapping of its 474 rule-like sentences
to the compacted text is `~/association-research/reviews/phase5-2026-10-14/agents-rules-after.md`.

### While the pipeline is rebuilt

*Lines 39-43; the rule now: the section's opening (standing rules since D10).*

`ROADMAP.md` replaces the query path's half-ported middle (templates,
presenters, adapters, six scoping declarations) with four stages. Until its
Phase 4 these rules apply to every change under `src/association/query/`,
whoever makes it. Each is a gate or a test, so a change that breaks one
fails rather than drifts.

*Lines 74-123; the rule now: "No new shape but by Jeff's lifting of D4".*

- **New shapes are frozen** (decision D4). No new shape the words can ask,
  template, presenter, scoping table or renderer.
  `tests/query/test_frozen_shapes.py` holds the 28 shapes the grammar can
  name (the grammar's keys, `reading.PointShape` as relation, shape and
  `by`, since Phase 3, step 4 - the 25 intents' names until then, a freeze
  retired with the intent; the 26th and 27th, `reading.PLAYERS_COUNTED`, how many
  players a season line holds, and a team's best single game,
  `team_games/rows/measure`, both by Jeff's lifting of D4 on 2026-10-13,
  ISSUES.md #390 and #364; the 28th, `reading.TEAM_SEASON_HISTORY`, a
  team's season line season by season over a range or every season,
  `team_seasons/split/season`, by Jeff's lifting on 2026-10-14, ISSUES.md
  #372 - named by a player's history's words under a team's line, and keyed
  so by the point reader for a team's line whose span is more than one
  season, `point._team_season_key`), the page's 7 renderers (one per
  `reading.Shape`, since Phase 4, step 1 - 22 keyed by the label until
  then) and the 4 scoping
  declarations left in `src` (by module and name, read from the source),
  none of which is added; and that `templates/`,
  `compose/present.py` and `compose/adapt.py` do not come back as source
  files. Phase 2 (2026-10-03 to 2026-10-05) removed the twelve templates
  (the registry stood empty after the shot chart, and step 6 deleted it
  with `check_scope`, `HONORED_SCOPING` and the `templates` package), the
  presenters and `compose/present.py`, and the ten adapters and
  `compose/adapt.py`. Step 6 deleted the scoping freeze too while 12 of its
  14 declarations were still there; restored 2026-10-05, it labels each
  one, and since Phase 3, step 2's closing slice it holds the 4 the
  roadmap keeps and nothing else - the per-relation cell tables, one table
  of cells and one table of shapes' rows per relation
  (`RELATION_SCOPING` and `RELATION_SCOPING_EXCLUDED` on
  `query/player_relation.py`, `TEAM_RELATION_SCOPING` and
  `TEAM_RELATION_SCOPING_EXCLUDED` on `query/team_relation.py`), the debt
  at 0. Step 2 retired the rest slice by slice: the window took
  `compose.core.COMPILER_SLOTS` (`ranked_by` is the typed window's `by`),
  the companions `WITH_WITHOUT_STATED` (a row of the planner's table), the
  subject's own `router._MODEL_SLOTS` (the subject is handed on typed
  since; the router went with Phase 3, step 4), and step 1 had taken
  `coverage._BOX_SCORE_SCOPING` (the floor follows the planned point's
  relation); the closing slice made the planner's check one function over
  the typed cells and the tables (`compose.plan.cells_unhonored`,
  `cells_declined`), each shape's row a `reading.ShapeCells` keyed by the
  planned point's `PointShape`, and deleted the planner's `STATED_SCOPING`,
  `compose.plan._TEAM_READER_REFUSES`, `reading.SCOPING_SLOTS` and
  `conditions._CONDITION_PLAYER_ONLY_CELLS` - each a field of a shape's
  row now (what the words do not state, what is refused outright, what is
  taken beyond the relation, where a decline is said, whether one named
  half of the starter/bench split is all it honors, what needs a named
  player). A P1 wrong answer is
  still fixed, in the code that exists. The sections below describe the
  readers and the sayer, because they are what runs; they are not an
  invitation to add a shape.

*Lines 124-183; the rule now: "What the roadmap is deleting may not grow".*

- **What the roadmap is deleting may not grow.** `scripts/check_ratchets.py`
  lists today's violations of each direction by name
  (`scripts/ratchets.json`): a function outside the reader that takes the
  question's text (any parameter holding it, not only one named
  `question`; 24 when Phase 3 opened, 1 since its first step - the trace
  file's writer, `history.RunHistory.write`, which records the question
  asked and answers nothing), how many statements each module executes (105 in 19
  modules when it was first counted, 2026-10-02; 80 in 20 since
  2026-10-05; 74 in 12 since Phase 4, step 5 (2026-10-12): the relation
  modules and their shared steps (`conditions` among them), `entities`
  (the name index's two loads and the resolver's elimination read) and
  `connection` (the latest season), and no module of `compose/` - counted
  per module because a listed module could grow
  statements freely; `.execute`, `.executemany`, `.sql`, `.query` and the
  package's own string-SQL helpers since 2026-10-03, when a statement
  through `entities._read_table` counted as none (each found by its body
  since 2026-10-12 - a function that runs the SQL it is handed, as a
  parameter (`con.execute(sql)`) or an attribute of one
  (`con.execute(statement.sql, ...)`), or hands its parameter on to such a
  function - and its own call of that SQL no longer counted beside its
  callers', which read the name index's two loads as three and the
  compiler's one door, `compose.core._executed`, as a statement of
  `compose.core`'s, #397); and since 2026-10-05
  every `core.rows_of`/`core.values_of` call over a statement the compiler
  did not build, in the module making the call, and every call of a
  function that only hands its parameter to one, such as
  `season_line._values` - Phase 2 moved 35 hand-written statements behind
  that door, where the count read 38 and the sites were 105 still; the
  argument's tracing is `_origin`'s docstring), a module but the lexicon
  that calls `re` or imports it at run time, or a module outside the
  reader that takes a reader's private pattern (`regex_outside_the_lexicon`
  since Phase 4, step 6; `regex_outside_the_reader` until then, which saw
  modules OUTSIDE the reader only: 6, then 4 since `refusals.py` went, 3
  since `calendar.py`'s patterns moved to the lexicon with the games'
  cuts, 0 since Phase 3, step 4, slice (b), where `entities`' last pattern
  became `lexicon.word_start` - and `regex_in_the_reader` beside it, the
  `re` calls each reader module but the lexicon made, by module, since
  2026-10-11: `point` 12, `parse` 5 with the parent grammar's 23 rows,
  `line` 1, the 18 the Phase 3 review found the step 4 exit "the reader's
  regexes outside the lexicon are gone" blind to; 2 after step 6's first
  slice moved the point reader's and the refusals' patterns to the
  lexicon, 0 after its second made the grammar's rows predicates over
  typed values with their words in the lexicon, and folded in then - an
  import under `TYPE_CHECKING`, for an annotation, is none), and a reader
  function that
  takes a DuckDB connection, by its name or its annotation (23, 0 since
  slice (b): the reader takes the names' in-memory index,
  `names.NameIndex`, which the answering loop reads before the parser
  runs, `entities.names_of`). The two at zero are held as empty lists, so
  a new one fails. Two more are gone: a private name `compose/` took from
  `templates/`, which reached 0 and was deleted with `templates/` (step 6),
  and `regex_in_the_reader`, folded into `regex_outside_the_lexicon` at 0
  (Phase 4, step 6). It fails
  on a NEW one or a count that grew, and on a listed one that is GONE or a
  count that fell, so the lists only shrink. A NEW failure is fixed in the
  code - pass the Reading, not the question; narrow through the relation's
  shared steps, not a new `execute` - and adding to the list is Jeff's
  call. A GONE failure is the ratchet working: run
  `scripts/check_ratchets.py --shrink` (it only removes and lowers) and
  commit the shorter list with the change.

*Lines 184-195; the rule now: "The reader does not import the answer side".*

- **The reader does not import the answer side, and the answer side never
  reaches the model** (`[tool.importlinter]`). The reader's imports of
  `compose`, `templates` and the relations were listed by name, and the
  list is empty since 2026-10-05: the contract holds with no ignored edge
  (nine when it was written; the last, `point -> compose.adapt`, went
  with the last adapter, whose default point the reader now reads itself,
  `point.DEFAULT_POINTS` - the rest were vocabulary and intent sets that
  now live on the reader's side: `subject.team_named_in`, `reading`'s
  intent sets, limits, `ordinal_word` and `Unsupported`,
  `measures.STAT_ALIASES`, `PERIOD_COLUMNS`, the metric and measure
  aliases and `resolve_metric`/`stat_measure`). A new one fails; an
  ignore is not the way past it.

*Lines 196-275; the rule now: "A change to the pipeline is proven on the recorded populations".*

- **A change to the pipeline is proven on the recorded populations -
  since D12 (Jeff, 2026-10-14, Phase 5's step 6 on) one run per PUSH: the
  feed (`scripts/stage_snapshots.py run --feed --workers 4`, about 80
  seconds sharded) on the branch tip against a fresh run of the tip
  before it, compared with nothing ignored but a field the push names,
  every moved answer enumerated and attributed to the push; the corpus at
  the lead's merge per batch; the unit-test calls only where a stage
  boundary moves; the readings at the agent's option for a reader-only
  change; the ledger report, the ratchets and the three Phase 5 harnesses
  at every commit. The four-population proof per commit below was the
  rewrite's rule (Phases 1-5 through step 5), and it is what the tools
  still do when asked - the description stands as the tools' reference.**
  Until D12: proven stage by stage, on four
  populations. `scripts/stage_snapshots.py run OUT.jsonl` answers the 628
  recorded questions through the whole agent with no model (the
  normalizer's recorded replies, the date pinned, DuckDB single-threaded,
  about two minutes) and writes what each stage produced: the reading
  (with the reader's own verdict on the point: declined, or the cause it
  refuses by), the query the planner planned (the one the compiler runs:
  a season-line point its reader does not read is planned as the
  game-level one, 3 of the 628 on 2026-10-05), the result's values,
  the answer. `compare` reports
  the first stage each question differs in and exits 1. The second
  population is every call the unit tests make across a stage boundary
  (`tests/stage_calls.py`: the parser's two steps, the planner's
  `plan_point` and `refusal_result`, `compose.answer` with the reason it
  declined by, `Agent.ask`): run the suite on each tree with
  `ASSOCIATION_STAGE_CALLS=<dir>` and `compare-calls` the two
  directories. It keys a call by its position among its test's calls
  and records each Reading whole, so a step that ADDS a recorded call
  (a `refusal_result` the planner now says) reads as every later call
  of that test moved, and a Reading field added with an empty default
  reads as every call moved: align first, with
  `~/association-research/stages/calls_aligned.py` (the enumerated new
  calls by their cause's kind taken out, the new empty fields stripped,
  a renamed test mapped), which is how Phase 3's step 0 proved 2,536 of
  2,550 identical and 14 moved. The planner joined on 2026-10-05 (#331): the refusals the
  templates said are the planner's since Phase 2, and 93 tests recorded
  no refusal, decline or answer text until then (1,408
  calls became 2,554: 1,085 `plan_point`, 61 `refusal_result`, the 1,408
  identical). The third is the 2,710 readings,
  `~/association-research/stages/reader_pop.py` (the 628 and the 2,082
  questions outside them, read and planned, no answer; `reader_cmp.py`
  compares two, in seconds). The fourth is those 2,082 ANSWERED:
  `run --feed OUT.jsonl` answers them as `run` answers the 628, from
  `~/association-research/stages/feed_recorded.jsonl` (`feed_replies.py`
  writes it: the StatMuse feed with no names and no stat, which no model
  was asked, and 15 franchise and companion wordings with theirs - tree
  against tree on equal input, not production's answer), about four and
  a half minutes a tree, and `compare` reads it unchanged. It is the one
  that sees an answer move on a question the corpus does not hold: the
  Phase 2 review found 4 moved answers no commit named there (#332).
  Three things to hold to:
  - **Run the "before" tree, not a stored file.** The baseline is
    `PYTHONPATH=<before>/src ... run before.jsonl`, and the first line of
    each file says which copy of the code it read; check it. Measured
    2026-09-30: two runs of one tree are identical on both populations
    (628 of 628 questions, text included; 1,363 of 1,363 calls), and on
    2026-10-05 two copies of one tree on the other two (2,082 of 2,082
    feed answers; 2,554 of 2,554 calls with the planner), so ANY
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
    reads; a decline is a dead end the user gets no help from. The same
    class, under the same terms (2026-10-05, slice (iv)): an intent whose
    reading declined while its retired template answered GAINED a point when it was
    ported, because a planned query needs one - the readings and the
    planned query move for exactly those questions, the answers do not.
  - **A boundary that moves is recorded on both sides first.** A phase
    that replaces `compose.answer` adds the new boundary to
    `tests/stage_calls.py` before it deletes the old one.

*Lines 276-361; the rule now: "A word the reading does not depend on is counted".*

- **A word the reading does not depend on is counted, and a number among
  them is a bug.** `scripts/claims_ledger.py run` reads each recorded
  question whole and again with each content word deleted (no model, about
  twelve seconds since names come from the index), and reports the words whose deletion leaves the reading
  and the planned query unchanged. It measures from outside the reader, so
  the count means the same before and after the reader is replaced.
  Baseline: 695 of 2,409 content words in 393 of 628 questions, most of
  them words that restate the shape ("average", "record", "stats"),
  measured on the replies re-recorded on the OVH devbox (2026-10-05, the
  Phase 2 review). It read 690 in 391 before Phase 2: five words, "team"
  in "nba team with least playoff wins since 2022" (and its "fewest"
  twin) and in "which team had the best record?", "teams" in the two
  "knicks playoff statistics vs other historic teams" wordings, counted
  as read only because the point the reader declined for them carried a
  reason sentence that named a team when the word was there; slice (iv)
  gave them a point, the same without the word, and none is needed -
  "playoff wins" and "best record" over the league are teams' already,
  and the whole comparison clause of the Knicks questions is unread
  (`ISSUES.md`, "A question with zero valid readings..."). It read 683 at `42e95d3`: seven words ("points" in "when maxey
  scored 20+ points", "shot" in "shot distance") counted as read only
  because the parser's decision records named the slots a second stage
  run had moved; with the stages run once there is no second run to
  list, and the reading and the query are the same without them. On the old box's replies it was 679 of 2,415 at
  `011091f` and 684 once the one reader of companions no longer needed
  the verb in "when Embiid and Paul George play"; 709 after the window
  slice's fix (`d12f899`, Jeff's call to let it grow), 702 after the line
  slice, 690 after Phase 3, step 3 - and 702 of 2,411 in 405 questions since
  step 4's slice (c) (2026-10-11; the same at Phase 4's close, 2026-10-13): twelve words a window read as the typed
  window no longer needs ("games" after "last 10 ..."), left unread by
  Jeff's ruling to keep that slice's moves; on the 2,082 feed questions
  (`claims_ledger.py run --feed`) 2,879 words are unread (3,658 at Phase
  4's close, 3,023 after the P1 sweep, 2,879 since Phase 5, step 1's
  refusals claim the words they name; 2026-10-14), 25 questions
  hold an unread number (177 at Phase 4's close, 86 after the sweep; 191 at the Phase 3 review; read one by one in
  the cleanup's (b)5, six fixed and the rest filed by shape) and 74 disagree
  between the Reading's list and the deletion (91 until the sweep). The 628's: 690 words, 5 numbers. Since the review's cleanup
  (b)5 (2026-10-12) both populations are HELD, in
  `scripts/ledger_held.json`, as the ratchets are: the unread words by
  count and the questions holding an unread number by question, each
  population's, and the feed's disagreements by question and its joins by
  kind (none since Phase 4, step 6 fixed #354's `situation+date` 4). `run` writes which population it read
  on its first line, and `report` exits 1 on a grown count or a question
  new to a list, and - on a run of the whole population - on one that fell
  or left it, until `report --shrink` lowers the file with the change (it
  only removes and lowers) - step 3
  being where "18-year-old" and the twelve
  words of "since 2000-01, how many games have players recorded 33 points,
  13 rebounds, 10 assists, 2 blocks, and 2 steals?" came to be read (689)
  and the "against" of "evaluate sga against embiid" stopped being read
  through a spelling the window took from the next name (690, an overcount
  exposed, as `d12f899`'s fifteen words were; Jeff's call, 2026-10-10, to
  let it grow by that one word: 702 -> 690 across the step). Seven
  questions have an unread NUMBER since then: five "100"s of "per 100
  possessions" (the rate reads per 100 from "per possessions" alone, so the
  number restates it), the "2" of "in 2 hours", which nothing can read, and
  the "2" of "2 threes in games Jamal Murray played" (`ISSUES.md` #357).
  After a reader change, run it on both populations and read the unread
  numbers first; the counts may not grow, and `report` says so. It is blind to a word only the model could have
  dropped, and a word a rule matched and did not need ("games" in "last 10
  games") is unread in both measures, since the claims are cut to the
  words each rule needed. Since Phase 3, step 3 the Reading states the same list
  itself: `Reading.unread`, the content words no claim covers, counted by
  the ledger's own rule moved to the lexicon (`lexicon.content_words`,
  `CONTENT_STOPWORDS`, `MODEL_STAT_WORDS`; the model's names and stat ride
  into the parser's last step, `parse._reading`'s `model_names`/`model_stat`,
  so a word the model accounts for is left out as the ledger leaves it
  out), recorded beside the reading
  (`unread`). `run` records it beside the deletion's list and `report`
  names every question where the two differ and exits 1 on one: they
  measure one thing two ways, so a disagreement is a finding - a claim
  over a word the reading does not depend on, or a word a stage reads
  without claiming it. They agree on 625 of the 628 since step 3 (284
  before its claims), and 625 is the step's exit by Jeff's ruling
  (2026-10-10): the 3 that differ are the subject reading's, which is read
  once and never probed for its claims ("head" in "lebron vs kawhi head to
  head" stops the spelling reader completing "kawhi"; "against" where the
  second team named is the opponent anyway, twice), and the ledger holds
  them by question text, each with its reason (`HELD_DISAGREEMENTS`):
  `report` exits 0 while the questions that differ are exactly those, and
  1 on any other - or on a held one that agrees now, which leaves the list
  with the change that cleared it, as a ratchet's GONE entry does. And a disagreement the other way round is the
  one that found answers: a word whose presence makes a rule read LESS -
  "2024 and 2025", two seasons read as none - is read by the deletion (take
  one year away and the other is read) and claimed by nothing
  (`span.needed`'s `gives_up`), and the step fixed the readers it named
  (`CHANGES.md`).

*Lines 362-377; the rule now: "A caveat, a stated default or a definition is written through `query/notes.py`" (the kinds were 28 when Phase 0 closed them).*

- **A caveat, a stated default or a definition is written through
  `query/notes.py`.** `note(kind, text, **facts)` for something about the
  data or a term the answer uses, `decided(kind, text, field=, chose=, ...)`
  for something the question left open and the system chose ("could the
  question have said it differently?"). Both hand the sentence back
  unchanged and, inside `Agent.ask`, record the kind and facts on the
  answer (`Answer.notes`, `Answer.decisions`) and in the stage snapshot's
  `remarks`. The 29 kinds (21 notes, 8 decisions; 28 when Phase 0 closed
  them) and each kind's fact names are closed
  (`NOTE_KINDS`, `DECISION_KINDS`, `FACTS`): a new remark uses one, and
  adding a kind or a fact name is a deliberate edit there. Facts are plain
  values, never a sentence or an object. Wrap at the writer, once, not
  where the sentence is attached. `scripts/stage_snapshots.py remarks`
  lists a remark that was written and never reached its answer;
  `compare` holds the remarks identical unless `--ignore-remarks` is
  given, which is only for a change whose point is to record more.

### Before you commit

*Lines 403-436; the rule now: "While iterating, run `scripts/check_fast.sh`" and "A slow test is found, not marked".*

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

The same cost hid in fixtures, a few rows at a time. DuckDB's Python API
binds `?` parameters slowly, so a fixture built row by row through
`executemany` or one `execute` per row pays it again for every test that
asks for it: `cx_ctx` (`test_compose.py`) and `league`
(`test_conditions.py`) spent 34 of their files' 39 seconds on it
(2026-10-13; a multi-row `INSERT` with the values bound is no cure, since the
binding is the cost). A fixture's rows go through
`tests/warehouse_rows.py`'s `insert_rows`, one Arrow table per table, and
`pytest --durations` showing the time under `setup` is the sign to look.


*Lines 443-455; the rule now: "The interpreter is pinned" and "A Python script a gate runs goes through that interpreter by name".*

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

*Lines 467-495; the rule now: "The CHANGES rule is also held per COMMIT" and the three ways the local run was weaker than CI.*

A second is not a hook because a hook cannot see it: the CHANGES rule held
per COMMIT (`scripts/check_changes_per_commit.py`, the `changelog-per-commit`
job in `ci.yml`). The hook reads the index it runs on; a rebase that folds
neighbors together, or a hook skipped once, leaves a commit holding `src/`
with no entry - `bfe09f7` did (ISSUES.md #380, the Phase 3 review). The job
reads every non-merge commit a push or a pull request carries (the pushed
head alone after a force-push), history before 2026-10-12 left out. Run it
on a branch before pushing: `uv run python scripts/check_changes_per_commit.py
--range origin/master..HEAD`. A commit it names is amended or split; it
cannot be fixed by a later commit.

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


*Lines 498-512; the rule now: "The docs gate rebuilds from scratch".*

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

*Lines 526-543; the rule now: "`CHANGES.md`".*

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

### Conventions

*Lines 585-624; the rule now: "Every module lives in a package".*

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
    what the model sees in `normalizer.py`, the grammar's rules that settle
    what the words ask in `grammar.py`, the words every tagger reads by in
    `lexicon.py` - the span,
    window, cuts, period, line and measure families' since Phase 3, step 2,
    the calendar's own patterns and the team metrics' words among them,
    and the subject's (who a question is about: the team words, cities
    and abbreviations, the singulars, the position words, the versus and
    "for" phrases, the grammar a dropped subject is read back from, where a
    name ends) - and the seven taggers, `span.py`,
    `window.py`, `cuts.py`, `period.py`, `line.py`, `split.py` and
    `measure.py`, which also holds the measure catalog;
    `calendar.py` is the situation's parsers and clauses over the lexicon's
    words),
    entities, the relations (`player_games.py` and its
    shared steps `player_relation.py`, `team_games.py` and
    `team_relation.py`, `season_line.py`, `team_seasons.py` and the
    standings beside it, `standings.py`, which builds and runs the four
    statements a team's record reads, and the two declared
    relations' own since Phase 4, step 5: `netpoints.py`, the NetPoints
    relation, which builds and runs every statement the NetPoints reader
    and the fingerprint read and a comparison's NetPoints rows, and
    `shots.py`, the shot relation, every
    statement its chart and distance read - each relation module runs
    its own statements, and `compose/` runs only what the compiler built
    or a relation hands it, Phase 4, step 5), the
    compiler's readers and the sayer (`compose/`), the coverage
    declaration (`coverage.py`), renderers, the answering loop
    (`agent.py`). `web/` - the local web interface.

*Lines 632-637; the rule now: "Call-time imports are absolute".*

- **Call-time imports are absolute.** A `from .fetch import warehouse` inside a
  function resolves against wherever the module lives *when it is called*:
  moving `cli.py` into `cli/` turned seven of them into imports of
  `association.cli.fetch`, which fails only when the command runs - the import
  of the module itself was fine. Write `from association.fetch import ...`
  inside functions.

*Lines 643-649; the rule now: "American spelling".*

- **American spelling.** "defense", "offense", "serialize". British spellings
  drifted back in twice after being removed wholesale in `c09d6f7`, so
  codespell now enforces it (`en-GB_to_en-US`, `[tool.codespell]`). A word it
  flags that is right - a basketball token, a regex stem, a misspelling a test
  depends on - takes `# codespell:ignore <word> - <why>` on its line, or joins
  `ignore-words-list` if it recurs. `CHANGES.md` is skipped: released entries
  quote the spellings that were fixed.

*Lines 658-676; the rule now: "Mark public API changes with a version directive".*

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
  surface is worth marking — internal helpers and the set of question shapes are
  explicitly outside the compatibility promise (see the preamble in
  `CHANGES.md`). Module-level constants need an attribute docstring (a string
  literal directly *after* the assignment) for the directive to attach.

### The failure shape this project keeps producing

*Lines 700-772; the rule now: "A reader on a relation does not declare, or apply, scoping of its own".*

**A reader on a relation does not declare, or apply, scoping of its own.**
The player-games relation (`query/player_games.py`, its shared steps in
`query/player_relation.py`) and the team-games relation (`query/team_games.py`,
its steps in `query/team_relation.py`) each carry the narrowing once -
the games' cuts (`reading.Cuts.CELLS`, Phase 3, step 2: `opponent`,
`tenure` (the player relation's alone),
`venue`, `date`, `situation` (a calendar narrowing, an alignment of the
opponent, or words nothing reads, parsed once into `reading.Situation`
and refused by value where neither), `game_n`, `season_n` (the player
relation's alone); `round` is a cut of no relation, refused wherever it
is set), without, split, below/above, the period's one typed cell
(`reading.Period.CELLS`, Phase 3, step 2: `period`, a quarter or a half -
`Period(number, half)` on `Reading.period` - which changes what a read SEES of each game rather than
which games: the relation rebuilds the period's line of every game from
the shots and plays, `narrow_periods`), the line family's typed cells
(Phase 3, step 2: `reading.Line.CELLS` - `line`, a line on a stat the
relation keeps the games under or over, a phrase kept under or over a
number or two or more "N+ stat" pairs on one game, applied by
`narrow_measures` through `lines.measure_filters`; `period_line`, a line
in a quarter or half, applied by `_apply_period_condition`; the shape's
OWN line, `Line.keyed` - a count's "30+ points", a record's "scores 30",
a run's, a high's - is read into the point's predicate by
`lines.threshold_line` and sets no cell - and `reading.Companion.CELLS` -
`companion`, a player beside the subject with his role, `played`,
`absent`, `started`, `bench` or `reached` with his `Line`, on the
subject's `own` side or the `opponent`'s, one cell because no table
honors or refuses one without the others, applied by
`_narrow_player_games`/`condition_player`, and the with/without split's
names; a decline says each cell by what the words asked under it - "games
without Joel Embiid", "the last 5 games" - `reading.cell_facts` phrased
by `say.cell_phrase`), the window's typed cell (`reading.Window.CELLS`,
Phase 3, step 2: `window`, an end of the span with its count, cut after
every other filter by `relation_window`; a bare count is no cell, each
reader's own parameter, which the games relations read as the newest N;
`ranked_by`, the measure a boolean-game ranking is ordered by, the
player relation's alone), and the span's three typed cells (`reading.Span.CELLS`,
Phase 3, step 2: `career`, every season on record; `range`, a career cut
at one or both ends, `since`/`until`; `both`, both season types in one
read) - applied in the shared steps (`scoped_player`/`scoped_games`,
`scoped_team`/`team_games`, `condition_player`; the span by `span_of`,
which takes the typed `Reading.span` and resolves it once for every reader)
and declared once (`RELATION_SCOPING`, with one row per shape a reader
reads, `RELATION_SCOPING_EXCLUDED`, keyed by the planned point's
`PointShape` - one `reading.ShapeCells` each, since Phase 3, step 2's
closing slice: the cells its words do not state, each with a reason about
the answer; of those, the ones the planner refuses outright, saying the
reason (a run's one date, two players' meetings against a third team);
what it takes beyond the relation's table (a season-line ranking's unit, a
team log's teammate it refuses in its own words); where a cell its words
do not state is declined when its reader is the point's only answer
(`declined`: by the planner, or by a team's own season's reader when
asked); whether it honors one named half of the starter/bench split only;
what only a named player's games carry. A shape states its relation's
table and what it takes, less its row's exclusions
(`compose.plan.cells_stated`), and the planner's one check reads the
typed values' cells against that (`cells_unhonored`) - for the planner,
for the season line's readers and for the answer side, which steps aside
before it asks a reader: no reader takes a list of its own, and `team_relation.py` keeps
the team relation's rows the same way). Two source-reading tests in
`tests/query/test_templates.py` enforce it, and they were watched to fail: a
shape a reader reads with no row, a row excluding a cell its relation does
not carry or without a reason, a reader taking a list of its own, or a
reader that writes `pgl.opponent_team_id = ?` or
`g.date >= ? AND g.date < ?` anywhere it reaches, fails the suite. So a new
scoping dimension is one clause on `Narrowed` plus a warehouse-verified test
per reader it turns on - never a slot taught to one reader at a time, which is
how twelve slots ended up honored on `game_log` and one on `single_game_high`
over the same relation when they were templates. An exclusion's reason is
about the answer ("one game is not a run"), never about the code ("not wired"),
and is a key, never a sentence: a `reading.DeclineReason` member, worded once
by the sayer (`say.decline_reason_phrase`), which a row given a sentence
refuses where it is built - so a decline's cause carries the key as its
`reason` fact (Phase 5, step 5; the rows held the sentences until then).

*Lines 778-786; the rule now: "The mirror image: a refusal that names the wrong cause".*

**The same bug has a mirror image: a refusal that names the wrong cause.** It
reads as honest, so nothing looks wrong. "Show me a fingerprint for Maxey"
answered `No NetPoints fingerprint on record for season 2026` — a claim about
league-wide coverage, and false; that season holds 566 players and Tyrese
Maxey is one of them. The real cause was that "Maxey" had resolved to Marlon
Maxey, who retired in 1994. Before writing a "no data" message, check which
fact is actually missing: the season, the player, or the match. They are
different sentences, and the wrong one sends the reader to look in the wrong
place.

*Lines 788-878; the rule now: "Names": "A model invents names, and an invented name resolves" and its three rules.*

**A model invents names, and an invented name resolves.** This is the
worst-behaved version of the shape above, because nothing about the answer
looks wrong. "Compare sga and embiid" routed to
`['Shai Gilgeous-Alexander', 'Jusuf Nurkic']` and produced a correct table of
two real players, one of whom the question never mentioned - every check after
the router passed, because "Jusuf Nurkic" is a real person who resolves
cleanly. The nickname version of this was known first (the router filled "The
Answer" in as Klay Thompson); the general version is that **any** name a model
supplies may be fiction - the router's then, the normalizer's now.

So a name is checked against the question before a reader reads it:
`subject.read_subject` reads who the question is about from its own spans -
ONCE per question, in `parse.read_route`; the parser's child step and its
last step settle that reading under the key the grammar's rules chose
(`subject.settle_subject`, which reads no name and asks the warehouse
nothing) - and `subject.apply_subject` writes those names onto the
Reading's one typed subject (`reading.Subject` on `Reading.who`; until
5.0.0 this was `entities.override_invented_players`) inside the parser's
last step (`parse._reading`), whose `Reading`, `read_route`'s return, is
all the agent answers from. Until 2026-10-02 a recorded route replayed
through `Agent.ask(route=...)` had its subject read in that last step, on
the settled scope instead of the one `read_route` reads from, and the two
paths disagreed on 9 of 12 wordings naming a franchise's old name.
`Agent.ask` takes a question and nothing else; a test that needs a
particular reading of the words stands in for `read_route`
(`tests/routed.py`: `Routed`, `ask_routed`, `with_subject`, `reading_of`,
which hand a test's own key and typed scope to the parser's last step), and a harness
answers recorded QUESTIONS with their
recorded normalizer replies, as `scripts/stage_snapshots.py` and
`scripts/preview_answers.py` do. Do not
add a second `read_subject` call to the live path: three readings of one
question is what this replaced, and they disagreed on 18 of the 628 recorded
questions. What counts as the question
supporting a name (`subject.question_supports`) is deliberately generous, because the model's expansions are usually the
useful kind: the word itself, a near spelling of it (the model silently
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
  Since 2026-10-13 a name the question never held never reaches the
  subject's players at all (`parse._read_route_players`, ISSUES.md #373
  closed): it rides on `Reading.misread`, so the kind and the key are the
  question's own, and `apply_subject` replaces it from the question's names
  where the count is exact - counted against the model's names the
  question does hold, `Subject.supported` - or reports it for the by-name
  refusal.
- **Trim a name back to the part the question holds only where that part is
  ambiguous.** Expanding half a name is usually the model doing its job:
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
  slot. Refuse only where the answer would actually be about
  that player (`reading.PLAYER_ASKS`, the shapes whose reader reads one; it was checked against the
  templates' own source while they existed): a
  stray name on a `head_to_head` question changes no answer, and refusing over
  it would break a question that works.

*Lines 880-901; the rule now: "A model drops names" and "A name nobody typed correctly is not repaired by fuzzy-matching".*

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

*Lines 903-914; the rule now: "Which name a model substitutes moves between sessions".*

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

*Lines 916-953; the rule now: "Before saying nothing matched, check whether something nearly did" and "Typos are the entity index's job".*

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
King" as Gerard King, each saying so. A team's near spelling is read the
same way in one place only, since 2026-10-13 (ISSUES.md #247): the word a
versus word leads, where it is near exactly one team's word
(`entities.near_team`) and is no player's whole word or near spelling - "gui
last 5 games vs sours" is the Spurs' games, kept as typed on the reading and
read by `entities.resolved_team`, which says so ("('sours' matches no team
exactly and was read as the San Antonio Spurs ...)"); "vs clevland" stays
unread, being near Antonius Cleveland too.

*Lines 955-984; the rule now: "A reasonable default beats a question, where the default is visible and can be corrected".*

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
`entities.collect_name_readings` and attached in `agent.py`, so a reader called
directly does not show it. The sentence is not optional: of 391 surnames two or
more players share, 124 now resolve, and in 67 of those a retired namesake has
more games on record than the active one ("wade" is Dean Wade, "pippen" is
Scotty Pippen Jr.). It is still elimination and not the prominence tiebreak:
two namesakes who both played ("brown", "curry") are asked about, and nothing
ranks them. Before extending the rule to another open slot, check both
conditions - a default with no wording that reaches the alternative is the case
that is not allowed.

*Lines 986-1011; the rule now: "A question too short to be one is refused unread" and "A best match is only safe where a wrong one is visible".*

**A question too short to be one is refused unread.** Jeff's rule,
2026-09-29: short or nonsensical questions are refused with a generic
sentence ("I couldn't understand your question, 'Tatum rec'. Please try
re-phrasing it."), and no effort is spent on them - most of the StatMuse
feed's two-word rows are a user hitting enter before the question was typed,
and guessing at "Tatum rec" answered his splits. It is the parser's first
verdict: `parse.too_short` (fewer than `parse.MIN_QUESTION_WORDS`, three)
reads the words as typed into the `too_short` cause, and `Agent.ask` asks it
before the normalizer, so a short question costs no model call, and has the
planner say it (`compose.plan.refusal_result`, answered `"refused"`).
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

### Working on the query path

*Lines 1015-1031; the rule now: the section's opening (no templates, no fall-through: the retired agent's measurement).*

The pipeline is parser → planner → a reader over a relation → the sayer →
deterministic answer, and a refusal naming why where nothing has a reading of
the question. There are no templates: Phase 2 (2026-10-03 to 2026-10-05)
moved every intent's read into a reader that returns a `Result` and every
sentence into the sayer, and step 6 deleted `templates/` with the relations'
shared steps moved home. There is no fall-through either: until 5.0.0 a
question the fast path could not answer went to a tool-calling agent that
wrote SQL by hand, and measured (ISSUES.md #129, 24 questions at production
defaults) it answered 1 in 23, did not finish 61% of the time, and was wrong
five times in six where it finished - so it is gone, with its prompt, its
tools and its budget. `Agent.ask` (`agent.py`) answers
`answered_by="refused"` with the reason the parser or the compiler gave the
question up with (`agent.refusal_text`; words that ask for no shape:
"what its words ask has no reader"), in the same second; a reader's own refusal (a clarification, a "no
match", a shape nothing reads named by its cause) is `"fast"`, since looking
at the question and having something to say is an answer. A question the
yardstick grades as "fell through" is one of these refusals now.

*Lines 1033-1073; the rule now: "The reader is the parser" and "Everything but the names and the stat is read from the words".*

**The reader is the parser** (ROADMAP plan item 6; the router's model
classification went in step (d)). The model only copies names verbatim and
picks a stat (`query/normalizer.py`); `parse.read_route` reads everything else
from the words into the Reading it returns: the parent grammar and a child
grammar name what the words ask, the grammar's rules settle it
(`query/grammar.py`, `grammar.settle`), and the taggers, one per family, read the filters into the Reading's typed fields in the
order each reads what the ones before it settled (`parse._tagged`: the
split, the lines, the measure, the period, the cuts, the window, the span
last). Nothing carries a route between the words and the Reading, and
the filters are the Reading's own fields (`reading.Filtered`: `who` - the
typed subject, `Reading.subject` being the subject reading's record -
`cuts`, `companions`, `lines`, `measure`, `span`, `split`, `period`,
`window`, and since 2026-10-11 `minimum` - a ranking's qualifier as the
question states it, `reading.Minimum`, read by the window tagger's
`window.read_minimum` with its words blanked out of what every other
tagger and the grammar read, kept on the players' ranking alone by
`point.minimum_under`, applied by the season-line ranking where the
metric qualifies by its unit (`metrics.qualifies_by`) and by the
game-level ranking in games, refused by a cause naming the floor
elsewhere, ISSUES.md #377), a frozen base every planned query inherits too (`Query`,
`TeamQuery`, whose compiler measure is `metric`, `NetPointsQuery`,
`ShotQuery`, `TeamSeasonQuery`), with the cells declared once on it
(`Filtered.CELLS`, `cells()`): a relation's step narrows by the query
itself, and every test states its reading in the typed fields and a
shape by its `PointShape` key. The populations record the typed Reading
(`stages._reading_record`: the ten filters by field, the key the words
asked, the subject reading's own record, the point's own fields with its
typed filters, the planned query's fields), and a decline says each cell
by what the words asked under it, read off the typed values
(`reading.cell_facts`, phrased by `say.decline_body`). Two things follow, and both matter when you add
a shape:

- **Everything but the names and the stat has to be read from the
  words** (the router's model filled it until 5.0.0), or it is silently
  absent: `fields` ("with their rebounds and
  assists"), a team's quarter, the window ("last 10 games" - the window
  tagger's, run just before the span's, which reads the season type
  beside it). The hold-out comparison
  that found those (`~/association-research/yardstick-v2/holdout_compare.py`:
  the recorded corpus's questions outside day10, both readers, no model) is
  the check to rerun after a table change, beside the yardstick.

*Lines 1074-1484; the rule now: "A filter family is read by ONE tagger", "A tagger reads the words alone", "Patterns live in the lexicon alone", "The subject has one reader and one writer" and "The families, by tagger" (each family's first measurement here).*

- **A filter family is read by ONE tagger, from the lexicon, and claims
  the characters it read** (Phase 3, step 2; the span is the first, the
  window the second, the games' cuts the third, the period the fourth, the
  line and the companions the fifth, the measure the sixth, the subject's
  own the seventh). Since Phase 3, step 3 a claim says what a rule's
  reading DEPENDS on, measured by the rule itself: a tagger's claims are
  cut to the words it could not do without - each word deleted in turn,
  the claims ledger's own probe, and the tagger asked again over the
  context its own words give it (`span.needed`: "games" in "last 10
  games", "100" in "per 100 possessions", a "regular season" that reads
  the default go) - and the words outside them its reading turns on are
  claimed beside them under the family's name (the possessive that makes
  "curry's last game" one game of his), except a word whose presence made
  it read LESS (`gives_up`: "2024 and 2025" read as no season is two
  unread years, never the span's). The grammars, the grammar's rules and
  the point reader, rules over the whole question with lookaheads and blockers,
  claim the words their decisions turned on (`span.read_by`, `what`
  `intent`, `point`, `refused`: "compare" keeps a pair off the matchup
  row). The subject reading is read once and is never probed: it declares
  its claims where it reads (the names, the companions' phrases, the
  kind's "player", a compare verb's "with", the season the names are
  settled in). The content words no claim covers are `Reading.unread`
  ("A word the reading does not depend on is counted"). Two claims that
  overlap without one holding the other join into one named for both
  (`span.claimed`), and the joins are HELD by kind and count (Jeff's
  ruling, 2026-10-10): `claims_ledger.py report` lists the 628's by `what`
  against `HELD_JOINS` and exits 1 on a new kind or a grown count, and -
  on a run of every recorded question - on one that fell or vanished until
  the table is lowered with the change.
  None is held since step 4's slice (c): the 628 have no join, and the
  2,710 had only #354's four (`situation+date`, one tagger reading a day's
  month again - a word two RULES of one tagger read is still a bug) until
  Phase 4, step 6 fixed them: no join on either population since, and any
  new one fails the report. The
  joins that were held - a child grammar's words over a tagger's
  (`intent+line` 12, `intent+opponent` 4, ...) went with slice (b)'s
  marks, the companions', the windows' and a dated range's with slice
  (c)'s, and a recognizer beside the measure (`refused+measure`) when it
  read the typed measure.
  The subject's own has no tagger: its one reader is the
  subject reading (`subject.read_subject`, once per question, below), and
  its one writer `subject.apply_subject`, which writes ONE typed value,
  `reading.Subject(kind, players, teams, position)` on `Reading.who`, in
  place of the four slots `player`, `players`, `team`, `teams` and the
  point's `position` (2026-10-10). The kind is the reading's (a team record
  keyed on a companion's line is still the team's question, with the
  companion its player); one player is the one a reader reads
  (`Subject.player`), two or more a comparison or a pair; one team the one
  the read is about or narrows to (`Subject.team` - the draft's `of_team`
  where the kind is the league's or a position group's). The grammar's
  rules are HANDED the typed subject and the opponent the reading read
  (`grammar.settle(question, asked, subject=, opponent=)`) and read the
  subject the question's grammar names and the team nicknames the words
  hold through the subject reading's own readers
  (`subject.subject_named_in`, `subject.team_words_in`); where they settle
  a name under the key they choose (a count's, a high's or a line record's
  dropped subject from the grammar, 30 of the 2,710 readings; a team's
  quarter from its one nickname, 24; a quarter's player read back, 1) it is
  the reading's word, written on the typed subject the taggers and the
  last step read - the grammar's writes (`ROADMAP-TYPES.md`, Still open
  15). A test hands its names to the rules typed, through
  `tests/routed.py`'s `staged` (`who=`, `opponent=`). Four stages that wrote a name the parser never hands
  - a rank word or a pseudo-team as the team, a team in the players list,
  one player listed as a comparison - went with the subject's slice (0 of
  the 2,710 readings reached them).
  The subject's words are the lexicon's (`TEAM_NICKNAME`, `TEAM_CITIES`,
  `TEAM_ABBREVIATIONS`, `TEAM_SINGULARS`, `POSITION_WORDS`,
  `POSITION_GROUPS`, `AGAINST_PHRASE`, `FOR_TEAM_PHRASE`, `VERSUS_WORD`,
  `SUBJECT_OF_HIGH`/`COUNT`/`HAVE`, `NOT_A_SUBJECT`, `NO_NAME_HAS`,
  `MAX_NAME_WORDS`, `NEVER_A_NAME`, `FILLER_PLAYER_WORDS`, the common words
  that name a team or a player, the child grammars' words, `MEETING_WORDS`):
  `subject.py` compiles no pattern of its own. The reading claims the
  characters of the names it settled (`subject.subject_claims`: each
  player's words, nickname or initials, each team's words, abbreviation or
  nickname, the opponent and the tenure with the "vs" or "for" before
  them - `player`, `team`, `opponent`, `tenure`, `position`), which ride
  `Reading.claims`. Measured first
  (`~/association-research/stages/subject_family.py`, the four slots at
  every place they are written on all 2,710 readings, the Subject record,
  the words, the planner's verdict and the names' resolution in the
  index): the model's player spans and the words' names differ on 134
  readings and the words win every one; the stages wrote a name after the
  subject reading on 58 (the grammar's subject 30, a team's nickname 27, a
  quarter's player 1) and no other stage fired; `apply_subject` moved the
  player on 10. The four slots are gone, and the typed value is recorded
  (`stages._reading_record`, `who`, beside the subject reading's own
  record `subject`). No cell: no relation
  table declares a name, and a position group narrows the league's read
  through `Query.position`, which the point honors on the league's own
  moves alone (`point._everyone_point`).
  `query/lexicon.py` holds every pattern the family is read by, named,
  with its reason beside it, and nothing that reads the warehouse or the
  answer side (contract 6: regexes only in the lexicon; it is the lowest
  layer of the reader - held by the `regex_outside_the_lexicon` ratchet,
  empty: no module but the lexicon calls `re` or imports it at run time,
  since Phase 4, step 6 moved the point reader's 11 patterns, the
  refusals' 3, `line.py`'s one and the parent grammar's rows here; the
  `regex_in_the_reader` count it took in stood at the 18 the Phase 3
  review found inside the reader until then). **A tagger reads the words alone, never the key
  the words ask** (Phase 3, step 4, slice (b)): each family is read twice
  over in `parse._tagged` - the tagger reads every candidate the words
  hold (`line.LineCandidates`, `window.WindowCandidates`,
  `span.SpanCandidates`, `measure.MeasureWords`, the period and the cuts
  as read), and ONE function per family in `query/point.py` applies the
  key (`lines_under`, `measure_under`, `period_under`, `cuts_under`,
  `window_under`, `span_under`, with the key sets that were the taggers' -
  `THRESHOLD_ASKS`, `PERIOD_ASKS`, `ORDER_ASKS`, `LIMIT_COUNTS_SEASONS`
  and the private ones beside them). They are the point reader's because
  they change what the Reading SAYS (a bare "30 points" is a count's own
  line and nothing on a log; a quarter stands on a period shape alone);
  the planner's check is a different one, whether a relation honors a
  cell (`plan.cells_unhonored`). Measured before the move and after
  (`~/association-research/stages/route_family.py`, each key's reading
  ablated to no key and each rule switched off alone on all 2,710
  readings): the key moved the lines on 124 readings, the measure on 176,
  the period on 120, the window on 150, the span on 219 and the cuts on
  none, by the same rules row for row on both trees, and the four
  populations and the claims ledger held identical. A claim is cut to the
  words the tagger AND the key's reading need together
  (`span.needed` probes the composition), so no word is claimed that the
  key's reading does not read. `query/period.py` is the period's tagger:
  `read_period(question)` reads what a read SEES of each
  game - one quarter or one half, by ordinal or by number ("1st quarter",
  "q4", "4th qtr", "first half", "2h": `WHICH_QUARTER`, `WHICH_HALF`,
  `ORDINAL_PERIODS`; a half is read first and apart, since a half is never
  a quarter) - from the words alone, writes `reading.Period`
  on `Reading.period` and claims what it read. `which_period(text)` is the
  ONE reader of which period any words name: the grammar's rules call it
  to choose among the three period keys (`grammar.settle`; the words they
  choose by - `QUARTER_WORDS`, `HALF_WORDS`,
  `PERIOD_LEADERS`, `PERIOD_GAMES_WORDS`, `PERIOD_AS_CONDITION`,
  `BY_QUARTER` - are the lexicon's, as are the point reader's guard
  `PERIOD_GUARD`, the grammar's `PERIOD_INTENT_WORDS`, the
  `period_as_condition` cause's `PERIOD_WORD` and the line slice's
  `PERIOD_CONDITION`), and `line.read_period_line` calls it for a
  condition's period, so no second copy of "first | second | 1st | 2nd"
  exists. Measured first (`~/association-research/stages/period_family.py`,
  the two slots as each stage set them on all 2,710 readings, with the
  words, the intent chosen, the planner's verdict and the relations'
  resolution): 120 readings carry a period (79 quarters, 41 halves - 82
  `period_split`, 23 `period_leaderboard`, 15 `team_quarter_points`), no
  stage moved one between the route and the reading, no period word fell
  inside another tagger's claim, and the value was written ONLY under the
  three period intents - six readings name a quarter and carry none (a
  subject nothing resolved, the point reader declining them by its guard);
  the key's reading keeps that (`point.PERIOD_ASKS`, the three keys, `point.period_under`) and claims nothing where
  it writes nothing. A "by quarter" question reads NO period (the
  breakdown is the point's shape, `by` `"period"`), and a period as
  a CONDITION on which games count is the line slice's value
  (`period_condition`), untouched. The two slots are gone, a model-era
  `period`/`half` key is no argument the parser takes, and the typed
  value is recorded (`stages._reading_record`, `period`). ONE cell,
  `period`, in both relation tables, a decline saying the quarter or half
  asked ("the 2nd half"), and a shape's row of its relation's table says
  whether it honors one. `query/cuts.py` is the cuts' tagger:
  `read_cuts(question, CutsContext)` reads which games of the span a read
  sees - a venue, a circumstance (`SITUATION`: a weekday, a month, a
  holiday, "since <day>", the opponent's conference or division, or words
  nothing narrows by - an age, overtime), one calendar day (`CALENDAR_DATE`,
  the year fixed by the season the words name or the current one, never
  on a career question; a date that opens a range or stands on a career
  question is a situation, and the year it wrote is handed to the span
  tagger; a range of calendar days with their years, `DATE_RANGE`, is
  one situation cut across seasons, a `date_range` narrowing, and hands
  the span the seasons its first and last day fall in - one season where
  both fall in it - and both season types where it can hold a postseason
  game and names no type, ISSUES.md #345), a playoff round, a game of a
  series, an ordinal season - over
  the opponent and absent teammates the subject
  reading read (an opponent that is the `without` list again is dropped;
  the tenure the subject reading writes after the taggers; a venue beside
  the splits reader's venue split is the split's, dropped by
  `point.cuts_under`); it writes
  `reading.Cuts` on `Reading.cuts`, the situation parsed once into
  `reading.Situation` (`situation_of`: the words, and the
  `CalendarNarrowing` or `AlignmentNarrowing` they name, which the
  relations apply and never re-read), and claims what it read - the
  opponent and the tenure it claims nothing for, since the subject
  reading, which claims nothing yet, read them. Measured first
  (`~/association-research/stages/cuts_family.py`, the eight slots as
  each stage set them on all 2,710 readings): no stage moved a slot
  between the route and the reading but the tenure (14, the subject's);
  a date and its own month stand together on 4 ("in march 24 2018"),
  two claims over one word, which `span.claimed` joins into one named for
  both (a partial overlap raised until this slice, and "lebron's last game
  7" - the window's "last game" and the postseason's "game 7" - raised out
  of the reader to the user). The eight slots are gone, and the typed
  value is recorded (`stages._reading_record`, `cuts`). The tables declare the cuts
  by name (`Cuts.CELLS` less `round` in `RELATION_SCOPING`, less `round`,
  `tenure` and `season_n` in `TEAM_RELATION_SCOPING`), a shape whose
  words state fewer says so in its row, and `reading.cell_set(scope, cell)` is the one reading of a cell name
  against the filters for the tables that list cells by name. `query/line.py` is the lines' tagger:
  `read_lines(question)` reads the lines on a stat a question
  keeps the subject's games past - "30+ points", "20+ point 5+ assist
  games", "scores 30", "fouled out" (fouls at six), "under 14 fta", "at
  most 5 turnovers", "with 25 minutes" - from the words alone, each with
  how the words state it (`LineCandidate.how`), and `point.lines_under`
  writes the subject's `reading.Line` values on `Reading.lines` (the measure as the game
  column's key, `op`, `value`, `as_typed`, `keyed` for the shape's own
  line, `narrows` for one the relation filters by, `period` for a line
  in a quarter, and `whose` for a line on the opponents' figure - after a
  given-up verb or "opponents", `lexicon.GIVEN_UP_LINE_LEAD`, its claim
  taking those words: the `opponent_line` cell, which a team's record over
  a line takes and every other shape refuses, ISSUES.md #426) and claims
  each line's characters once; a reader whose
  shape is a line (`point.THRESHOLD_ASKS`: the count, the record, the
  streak, the high) reads a bare "N stat" as its own, every other reader
  only what the words say outright - a floor of minutes ("with 25
  minutes", "when playing 20 or more minutes", "playing 30+ minutes"; "over
  26 minutes" and "more than 26 minutes" keep the games OVER the number,
  `lexicon.OVER_LEADS`), a line under a
  number, a line stated over or at a number by its lead on any stat
  ("more than 40 points", "exactly 25 fga", "fga = 25":
  `lexicon.LINE_OVER_OR_AT`, `EQUALS_LINE`, ISSUES.md #371 - but on a
  ranking of players' seasons, where it is a season's line; under a line's
  shape "more than 40" is the shape's own "41+"), a line on a player's
  SEASON figure on a ranking of players' seasons alone ("averaged 30 ppg",
  "> 5 apg", "10000 points": `Line.per`, `season` or `total`, the
  `season_line` cell, kept by `season_line.ranking_statement` on the
  season's deduped box row and by `career_ranking_statement` over a
  career's rows; the game lines read inside one set aside, ISSUES.md #390;
  "how many players ..." counts them, `reading.PLAYERS_COUNTED`, and "most
  seasons averaging 30 ppg" orders the ranking by how many of a player's
  seasons hold the line, the planned key `player_seasons/ranking/seasons`
  - no asked key of its own, as the games count is planned
  `player_games/ranking/count`, ISSUES.md #391),
  two or more "N+ stat"
  pairs - as the router's stages read them
  until 5.0.0 (a single
  "20+ points" on a log is read by nothing, ISSUES.md). `threshold_named`
  is what the grammar's rules ask before the lines are read (a count with no
  line is a ranking). `read_period_line(question)` is the parser's reader
  of a line in a quarter or half ("after making one three in the first
  quarter"), run before the grammar and the taggers, which BLANK its words to spaces - not
  cut - so the period is not read as the one measured and every later
  claim keeps its offset; `period.which_period` reads its period. The lines
  are read from the WHOLE question, a companion's phrase included:
  measured, leaving a companion's line to the companion moved one recorded
  reading ("In his 18th season, how many games with 40+ points did Lebron
  James have?": the phrase reads as a companion's, the subject is LeBron
  himself, restored by the grammar's rules, and a count with no line became a
  ranking), and the two claims fold into one. Measured first
  (`~/association-research/stages/line_family.py`, the FIVE carriers of a
  line - the `stat`/`threshold` pair, `above`/`below`, the point's
  predicates, a companion's `reached` entry, `period_condition` - and the
  three companion slots as each stage set them on all 2,710 readings): a
  threshold on 120, a floor or a line under a number on 39, two or more
  pairs on 11 - where the slot pair contradicted the words every time
  ("20+ point 5+ assist" read `stat: assists, threshold: 20`, the relation
  narrowing by the words and the pair unread; the typed `Line` is built
  from the words, never from the pair) - a line in a quarter on 2, a
  companion's line on 23 (every one a team's record, the line becoming the
  record's own, `subject._apply_team_record_when`), and the point's own
  re-read of the number's words (`point._everyone_threshold_predicates`,
  `_numbered_stat_lines`, deleted) differed from the words on 1 ("most
  games with 20 pt,s 10 reb, 5 ast": the pattern could not pass the comma
  and fell back to the model's "rebounds" - the one answer this slice
  moved). The seven slots are gone, and the typed values are recorded
  (`stages._reading_record`, `lines`, `companions`; the parser's list of
  the names the reading settled on still reads a teammate who played as
  the slot era did - `with_player` where the point is the with/without
  split and a condition on any other point). `query/measure.py` is the measure's tagger AND the one
  catalog of what a question can ask about: `read_measure(question)`
  reads every word the measure is read by (`MeasureWords`), and
  `point.measure_under(asked, words, MeasureContext)` reads the measure
  from them - the words' own reading over the
  normalizer's key (`measure.named`: the grammar first, `MEASURE_GRAMMAR`,
  then the longest box-score word, `MEASURE_WORDS`), then every rule the
  stages wrote by, in their order (fouling out is the count's stat; a
  games count under a line's reader is no measure; a quarter's, a
  comparison's, a line's, a team's or a run's stat is kept only where the
  words name one, `names_a_stat`; an advanced metric; game score by the
  reader's own spelling; a 2-point percentage; a ranking by shot distance,
  the `shot_distance` sentinel; the attempts beside a make; the LONGEST
  team-metric alias the words hold, `team_metric_named`, "allowed" after it
  making it the opponent's, as "opp" before a stat does and, on a team's
  key, a verb giving points up wherever it stands ("how many points did
  the sixers allow", `lexicon.GIVEN_UP_VERB`, `GIVEN_UP_KEYS`); a NetPoints rate folded into its key and any
  other unit kept as the `rate` cell; a team's total; the stat the lines
  tagger read beside a line; a run's result; a fingerprint's side; a chart's
  shots; the columns beside a ranking) - into `reading.Measure(key,
  as_typed, how, unit, whose, side, shot_value, category, won, beside)` on
  `Reading.measure`, and claims what it read. The model's key is CONTEXT
  (`raw["stat"]`, the one REQUIRED key the normalizer fills whether or not
  the question names a stat): the words win where they name a measure, and
  where they name none the key stands as it did, so no answer moves; the
  grammar's rules that turn on a games key read the same context, the
  words over the key, before the tagger runs. `measure.CATALOG` is the one
  catalog (`MeasureKey`, 42 keys; per key how it is read on each relation:
  the game column, the compiler's measure, the ranking's metric for a bare
  name, a career, a total, a per-game figure, the team metric, the
  opponent's, and whether it is a yes or a no of each game, `yes_no`:
  a double-double, a triple-double, a foul-out, a win, a game played -
  the compiler's `BOOLEAN_MEASURES` are read from it) and `measure.ALIASES` every spelling the six vocabularies
  named a measure by (322 distinct spellings, re-counted 2026-10-13 where
  this said 390: the normalizer's keys, the grammar's, the
  box-score words, the team metrics' alias texts, the leaderboard metrics'
  names, the compiler's derived names), through `key_of`; the answer side's
  lookups (`measures.resolve_metric`, `stat_measure`, `stat_column`,
  `log_extras`, `period_split_measure`, `streak_column`,
  `team_metrics.resolve_team_metric`) take the `Measure` and read the
  catalog's facets, and `tests/query/measure_spellings.json` holds what each
  returned for every reachable spelling before the catalog, which a test
  holds them to on the facets each vocabulary reached. `Measure.as_typed`
  is what a refusal prints - a team metric's
  alias text ("fgm") is one, and `named_by_a_team_metric` is how the team
  compiler's total still declines it (ISSUES.md). Measured first
  (`~/association-research/stages/measure_family.py`, the seven slots and
  the point's `measures`/`aggregate` as each stage set them on all 2,710
  readings): a stat on 1,004 readings in 69 spellings - the model's on 280
  (23 distinct), the words' on 958 (37), the two disagreeing on 58 with the
  words winning every one, the model's key standing alone on 32 - a rate on
  13, a per-game log on 38, a side on 1, a shot value on 13 (all 3), columns
  beside a ranking on 7, a run's kind on 6; no stage moved a slot between
  the route and the reading; 24 readings carried a key no answer-side
  vocabulary held (`shot_distance`, the ranking's sentinel; `games_played`,
  read by nothing; the grammar's given-up keys, refused by name). The seven
  slots are gone, and the typed value is recorded
  (`stages._reading_record`, `measure`). ONE cell, `rate` (`Measure.CELLS`): honored by the
  season-line ranking and the team compiler's total, declined by every
  other reader in the sentence it had, taken beyond the relation by the
  ranking's and the team-season shapes' rows (`ShapeCells.taken`, through
  `Measure.CELLS`) and by the team compiler's own sum, declared in no
  relation table (a unit narrows no games). The aggregate is a closed
  enum (`reading.Aggregate`, Phase 4, step 3: `NONE`, `PER_GAME`,
  `TOTAL`, `COUNT`, `RECORD`, and the compiler's own `LINE`) on the
  `Query` and the `TeamQuery`, and a record writes a member as its value
  (`stages.plain`). It is the PLANNER's since Phase 5, step 4 (D5, Jeff,
  2026-10-14): the point reader stamped it at 37 sites until then, and
  `compose.plan.aggregate_of` derives it from what the point is read as
  (its declared `read_as`, never the planned key: a season-line point
  planned at the game level keeps its per-game figure) and its measure -
  the catalog's `yes_no` facet on the point's `Measure` or on a measure it
  keeps its games by held true, and `Measure.how` "total", which the
  point reader writes where it reads "total" on the game-level ranking.
  Its table: rows and charts, NetPoints and the shots `NONE`; a team's
  own season `TOTAL` (the team compiler's sum where the season reader
  declines); the season line and the periods `PER_GAME`; runs `COUNT` on
  a team's games, `NONE` otherwise; a comparison `RECORD` by opponent,
  `PER_GAME` by subject, `NONE` by met; a split or a scalar by `line`
  `COUNT` where it counts a yes or a no, else `RECORD` (split) or
  `PER_GAME` (scalar); a split by splits or presence `RECORD`, by anything
  else `PER_GAME`; a scalar by count, record or total that, by `""`
  `TOTAL` on a team relation and `COUNT` otherwise; a ranking on the games
  `COUNT` by count, of a yes or a no or with no measure, `TOTAL` for a
  measure asked as a total, `PER_GAME` otherwise. Measured first: on the
  2,710 readings the planned key alone left 52 points off the stamped
  value, with `Reading.relation` 39, with the reviewer's facet 24 - the
  three remainders were a games-played ranking (6, `games_played` joined
  the facet), a team's total stamped on its season's shape (6, its own
  query carrying none) and "Lyngby bk total corners last 5 games average"
  (declined either way) - and the declared shape and the held-true
  predicate (the "how many games has embiid won when he scores 30 points"
  wordings outside the populations) close them: every planned `Query` and
  `TeamQuery` on the 2,710 and the 518 reader cases reduces as it did.
  The point's `measures` are typed too
  (`tuple[Measure, ...]`, each the catalog's key, stated through
  `measure.measured` from the names the point reader reads a measure by),
  and the compilers' names are built once, where the planner hands the
  point over (`compose.core.measure_names`, `compose.team.team_metric`).
  The `Query`'s `measures` are still those names: retyping them is the
  answer side's, whose readers test and replace them by name. `query/window.py` is the window's tagger:
  `read_window(question, WindowContext)` reads, and `point.window_under`
  keeps under the key, which rows a question keeps
  and from which end - the grammar's count and end ("last 10 games", "his
  first game", "top 5"), a history's "past 5 years" as its count of
  SEASONS (`Window.of`), one game of a player's line ("his last home
  game"), an end filled from the looser `ORDER_WORDS` on a reader that
  honors one and dropped on a reader that refuses one beside a count it
  can still take, a team ranking's end (`rank`), the measure a ranking of
  boolean games is ordered by (`by`) - the tagger over the words and
  whether the stat is a yes/no one, the key's reading over what the words
  ask (`point.ORDER_ASKS`, `LIMIT_COUNTS_SEASONS`) - writes `reading.Window` on
  `Reading.window`, and claims what it read. Measured first
  (`~/association-research/stages/window_family.py`, the four slots as
  each stage set them on all 2,710 readings): the parser read the grammar
  twice, before the stages and again after, and every stage that DROPPED a
  count (the model-era filler rules: `_names_a_count` and its five callers)
  was undone by the second read, since the grammar was the count's one
  source - so those rules were dead, and went rather than being ported; the
  model-era `order`/`limit`/`rank`/`ranked_by` is no argument the parser
  takes. The four slots are gone, and the typed value is recorded
  (`stages._reading_record`, `window`). `query/span.py` is the span's tagger: `read_span(question, SpanContext)` (and `point.span_under` over its `SpanCandidates`) reads
  the seasons and the season type - a season named, a career, a range, the
  postseason, both types - in the order the retired stages read them
  (measured first, `~/association-research/stages/span_family.py`: the six
  slots as each stage set them on all 2,710 readings), from the words and
  the few facts read before it (a window, a player, a dated range; what the
  words ask is the key's reading - a history's relative seasons, the three
  implied careers, a log's both types), writes one typed value (`reading.Span`, on
  `Reading.span`) and returns the `Claim`s - `(start, end, what)` - of the
  characters it consumed, which the parser carries onto `Reading.claims`.
  A claim inside another's characters folds into it (the "playoffs" in
  "including the playoffs"); two claims that overlap without one holding
  the other are joined into one claim named for both (`span.claimed`,
  since a29c7fd). The span tagger runs LAST among the taggers
  (`parse._tagged`), after
  the subject is settled and the period, cuts and window taggers have run, because three of
  its rules read the typed window (a bare "last N games" log reads both
  types; a count's "how many" with a player named is his career; a log's
  "last N vs the Pistons" is every meeting - `SpanContext.window_named`,
  `order`, `limit` come from the `Window`), and a model-era span or window
  key (`season`, `season_ref`, `season_type`, `order`, `limit`, ...) is no
  argument the parser takes: the words are the one source. The six slots
  are gone, and the typed value is recorded (`stages._reading_record`,
  `span`). The point
  reader writes no span of its own: where it settles the subject's span
  apart from the scope's (a date names its own game, so the name is
  settled over the career; a run's season is this one outright, never a
  defaulted one) it says so in `Reading.subject_span`, the one typed
  override.

*Lines 1488-1530; the rule now: "A name is recognized from the in-memory index, never by a statement".*

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
  all-ASCII column, table order where the SQL had no `ORDER BY`. A name
  lookup takes the index (`find_players(players, ...)`,
  `find_teams(teams, ...)`, `team_abbreviations(teams)`,
  `suggest_players(index, ...)`, checked where it needs the rows by
  `checked_players` / `team_columns`), never a connection; a caller on
  the answer side hands it `entities.players_of(con)`, `teams_of(con)` or
  `names_of(con)`. Do not write `FROM players` or `FROM teams` on the
  reader's path again. The reader of the question's words holds no
  connection at all (Phase 3, step 4, slice (b); `con_in_the_reader` 23 ->
  0): `parse.read_route`, its last step `parse._reading`, `classify_span`
  and `subject.read_subject` take a `names.NameIndex` - the players' and
  the teams' indexes, which `Agent.ask` reads before the parser runs
  (`entities.names_of`), and `names.ActivePlayers`, the players with a box
  score in the latest season on record, which a typo near more than five
  players is narrowed by (`entities.suggest_players`; the one warehouse
  fact the reader reads beyond the two tables, through `classify_span` on
  2 of the 2,710 readings), read on first use in the block through the
  index (`NameIndex.active`, a reader the answering loop hands it) so no
  question that does not narrow pays for it. Measured over the 628
  recorded questions through `Agent.ask` with a counting connection
  (`~/association-research/stages/statements_per_question.py`,
  2026-10-10): 5.17 statements a question before the slice - the two table
  loads inside the parser's first lookups, the latest season and a
  box-score narrowing on 2 questions (8 statements) - and 5.16 after it:
  the two loads in the answering loop, before the parser, and in the
  parser the latest season and the active set on the same 2 questions (4
  statements, read once a block where each lookup read its own); every
  statement after the parser identical (1,358, and the latest season once
  a question). The reader issued 38.5 a question before the index.
  One thing is not reproduced: the order of two players who share a
  `display_name` (21 names), which DuckDB's sort left unspecified; the
  index uses table order.

*Lines 1531-1573; the rule now: "Who stands beside the subject has one reader: the subject reading".*

- **Who stands beside the subject has one reader: the subject reading.**
  "without X", "with X out", "when X and Y play", "in games X missed",
  "when X scores 20+ points", "vs X" are read by `subject._conditions_read`
  (and `_versus_companions`), each name with its role as a typed
  `reading.Companion` - `player` as the question spells him, `side` (`own`
  or `opponent`), `predicate` (`played`, `absent`, `started`, `bench`,
  `reached`) and a reached role's `Line` - with a `Claim` on the phrase
  (`Subject.claims`, which the parser carries onto `Reading.claims`), and
  ONE writer puts them on the Reading: `subject.apply_subject`
  (`_apply_companions`: every companion, the other side's first, the
  subject himself excluded unless absent; `Reading.companions` - and on
  the path that refuses an invented name too, so "jay huff game
  log vs Embiid" keeps Embiid beside the refusal). The grammar's rules
  and the taggers are HANDED the typed companions
  (`grammar.settle(..., companions=)`, `parse._tagged`) and read no name
  themselves: the rules decide a with/without split from them and the
  cuts tagger cuts the opponent against the absent ones
  (`grammar.absent`), and a model-era `without`, `with_player` or
  `conditions` key is no argument the parser takes. Until 5.0.0 the
  router's stages had readers of their own for the same phrases; the two
  disagreed on seven of the 628 recorded questions, and "When Embiid
  plays with Paul George, what is the PHI record?" answered for Embiid
  alone (#310). Do not give a stage a name regex again. The phrases the
  reading reads by are the lexicon's (`ABSENCE_WORDS`, `ABSENT_NAMED`,
  `COMPANION`, `COMPARED_WITH`, `COMPANION_STOP`, `COMPANION_IN_GAMES`,
  `VERSUS_PHRASE`, `CONDITION_STARTED`, `CONDITION_BENCH`,
  `CONDITION_ABSENT`, `WHEN_REACHES`, `NAME_PIECES`, `NAME_SHAPED`,
  `NAME_JOINERS`, `NAME_STOPWORDS`). Three rules of that reading, each
  measured against the old one on wordings outside the corpus before the
  stage readers went:
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

*Lines 1575-1665; the rule now: "A shape is a reader and a sayer", "The point declares what it is read and said as" and "The answer side chooses by shape".*

- **A shape Phase 2 has ported is a reader and a sayer, and nothing else
  answers it.** What travels between them is typed (2026-10-05, "the
  Result is typed", the Phase 2 review's cleanup (b)1, (b)5, (b)6), and a
  change keeps it so:
  - **A reader returns `Result | Refusal | Clarify`** (`query/result.py`),
    never a sentence. A refusal found at RUN is a `Refusal(kind, facts)`
    whose kind is in the closed `result.RUN_CAUSES` (a fact found in the
    warehouse), disjoint from the reading's `reading.CAUSES` (a fact read
    from the words) - a test holds the two apart; a question back is a
    `Clarify(asked, candidates, ...)`. The relation modules
    (`no_narrowed_games`, `no_games`, `condition_team_no_games`,
    `entities.clarify`, the coverage floor as `coverage.floor_refusal`)
    return the same types. ONE phrase table words every cause,
    `say.refusal_phrase(kind, facts)`, beside `note_phrase` and
    `decision_phrase`; the planner's `refusal_result` says a reading's
    `Cause` through it. A new refusal is a kind in `RUN_CAUSES` and a
    phrase there. `Refusal.shown`/`under` say what the page shows beside
    the sentence and under which keys, since the readers' pages differed
    and the answers' data is held identical.
  - **A cell the read applied is a typed value, not a key.**
    `Narrowing.cells` holds the `result.Cell` union (`Period`, `OnDate`,
    `Line`, `Role`, `GameOfSeries`, `Calendar`, `Companions`, `Met`,
    `ShotValue`), read by type (`narrowing.cell(Role)`, `lines()`,
    `period`); a span cut is `Span.since`/`until`/`ordinal`; a split's or
    a run's parameter is its body's (`Grouped.of`, `Runs.line`/`won`).
    What the sentence needs beside the body is ONE frozen record per shape
    (`result.Facts`: `CountFacts`, `LogFacts`, `LineFacts`, ...), read
    through `say._facts(result, Kind)`, which raises on another shape's
    record. There is no untyped `facts` mapping to add a key to; a shape
    that needs a new fact adds a field to its record. A read that found
    nothing keeps its body's shape (an empty `Rows`, a `Scalar` with no
    values) and gives its reason as `Result.empty`, a `Refusal`.
  - **The answer side chooses by shape, never by intent, and the shape is
    the point reader's own.** Since Phase 3, step 1 (2026-10-09) the point
    declares what it is read and said as on the Reading itself, as ONE
    field since Phase 5, step 4 (D9; three, `shape`, `by` and `on`, until
    then): `Reading.read_as`, a `reading.PointShape` - its `shape` in the
    target's vocabulary (`scalar`, `rows`, `ranking`, `comparison`,
    `split`, `runs`, `chart`), its `by` (what one row is, how a scalar is
    reduced or what a reader's rows are ordered by; `""` where no reader
    takes the point and the compiler's own sentence answers) and its
    `relation` (`reading.PointRelation`: `player_games`, `player_periods`,
    `player_seasons`, `team_games`, `team_periods`, `team_seasons`,
    `team_snapshots`, `netpoints`, `shots`). Every `Reading(...)` in
    `query/point.py` names it, by a key's constant where the value is one
    of the grammar's keys (`reading.PLAYER_LOG`, ...) and as
    `PointShape(...)` otherwise; a shared move read under several shapes'
    words says what it is read as from what the words asked
    (`point._ROWS_BY`, `_COUNT_SHAPES`, keyed by the grammar's key since
    step 4), and a team-season shape's point on the team relation is
    stamped its season's shape (`point.TEAM_SEASON_POINTS`, the three
    keys, each the point's shape). The planner plans that key as it
    stands (`plan.point_shape`; its one move: a season-line point
    re-planned at the game level is on `player_games`, held by a test
    over every reader case) on every verdict, and derives the
    compiler's skeleton and source from it (`plan.skeleton_of`:
    `ranking`/`split` group, a `comparison` by `met` is the pair, a
    `split` by `line` compiles as the scalar record its reader says as a
    split, a team's total under its season's shape is the team compiler's
    scalar; `Query.source` is `"seasons"` for a point on `player_seasons` -
    `Reading.source` is gone). `compose.answer` picks the reader from
    `_ROUTES` by that key alone; the shapes' rows of the two relation
    tables are keyed by it (`RELATION_SCOPING_EXCLUDED`,
    `TEAM_RELATION_SCOPING_EXCLUDED`, one row per route since step 2's
    closing slice). No table names a shape by a retired name: the trace
    and the records name a key by `reading.key_path` (`relation/shape/by`),
    and a decline says what the words were read as asking in the shape's
    words - the sayer's one phrase per key the grammar names
    (`say.asked_as`, framed by `say.nothing_answers`): "Nothing here
    answers this question as a comparison of players: the seasons since
    2022-23 cannot be read here - ...", "a team's record has no reading of
    this point" (the planned shape, a `no_reading_of_point` cause's
    `planned` fact); `coverage.SOURCES` is keyed by it (below). `say()`
    chooses the sayer from the headline body's type and the one field
    that says what it is (`Scalar.how`, `Grouped.by`, `Rows.by`,
    `Chart.kind`) - never `Span.source`, the relation or a facts key; a
    sayer reached so may still tell its subject's kind or a cell it says
    (a quarter's `Period`, a matchup's `Met`). The intent is gone from the
    reader since Phase 3, step 4, and the label it left behind since Phase
    4, step 2: the grammar names what the words ask as a `PointShape`
    (`Reading.asked`, below, "What the words ask comes from the question's
    own words"), the point reader and the taggers key on it, and the
    decisions about what the words ask record the `field` `asked`, each
    value and reason a `key_path`. A cause carries the key, never a label
    (its `shape` fact): the sayer's phrase tables are keyed by the
    `PointShape` (`say._LINE_SHAPES`, `_STAT_FOR`, `_REACHING`,
    `_ASKED_AS`), and no sentence or page prints a retired name. Proved on the four populations: 628 of 628, 2,082 of 2,082
    and the 2,710 readings identical with the point's `by`, `on`, `shape`
    and `source` left out (`compare --ignore`, `reader_cmp.py --ignore`:
    each lists the field's values by count), 2,608 of 2,608 unit-test
    calls aligned past the same fields (`calls_aligned.py --drop`).

*Lines 1667-1828; the rule now: "Where each shape is read" (the table) and the three slice rules: a note said once, the reader executes the compiled statement, what a relation measures lives on the relation.*

  The game log is the first (2026-10-03, `ROADMAP.md`,
  "Phase 2, the expected steps", step 0): `compose/logs.py` reads a
  player's or a team's log into a `Result` (`query/result.py` - the rows,
  the count the window cut them from, the per-row summary, the remarks as
  `Note(kind, facts)`), and `compose/say.py` words it, taking the Result and
  nothing else (the import contract "The compiler's sentence reads no
  warehouse" holds it, with `compose.sentence`). `compose.answer` reads a
  rows-shaped `game_log` (and the `player_stat` window the retired template
  handed to the log) through them, and so, since,
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
  `compose/pairs.py`; the next paragraph), a team's or the league's
  longest run of wins or losses (`compose/runs.py`'s `read_team_streak`,
  since step 4: the team compiler's `run` shape,
  `compose.team.compile_team_run`, into the same `Runs` body the player's
  run is, said by the same `say_streak`), a team's log and splits
  (`compose/logs.py`, `compose/splits.py`, executing the team compiler's
  `rows` and `grouped` compiles, `compose.team.compile_team_over`, since
  step 4), a team's record with and without named teammates
  (`compose/presence.py`, since step 4: the team compiler's `presence`
  statement, `compose.team.compile_team_presence` over the relation cell
  `conditions.presence_games_sql`, into a `Grouped` by `presence`, said by
  `say.say_with_without`), and a team's record over its own line
  (`compose/records.py`'s `read_team_record_when`, since step 4: the team
  compiler's `line` and `count` statements, `compose.team.compile_team_line`
  and `compile_team_count`, said by `say_record_when`), the league's leaders by a
  season-line metric (`compose/rankings.py`, since 2026-10-05: a `Grouped`
  ranking by `player` whose `ranked_by` is the metric - a count of games
  over a line ranks by `"games"`, which is how the sayer tells the two
  apart - read through `season_line.rank_season_line`, which keeps the
  dedup, the qualifier and the career pool exactly; the default qualifier
  is the `minimum` decision it was, one the question states is what was
  asked and no decision (`RankingFacts.stated_minimum`), the career pool a
  `floor` note), and a
  player's unnarrowed line, his stat season by season and two or more
  players' lines side by side (`compose/seasons.py`, since 2026-10-05,
  over the SEASON LINE - `query/season_line.py`: one row per player per
  season in `player_season_stats_deduped`, and the advanced stats beside
  it. That module settles the subject and builds each statement the
  retired templates ran, moved whole - since step 4 the ranking's too
  (`query/leaderboard.py`, folded in whole with its floors, dedup and
  qualifiers) and every read of a player's seasons on record (the
  defaulted-season redirect, `season_redirect`, which the charts and the
  game relation's "no games" sentence call; `seasons_on_record`;
  `seasons_played`); `compose.core.values_of` (or `rows_of`, for a
  ranking's rows by name) executes every one through the compiler's one
  `execute`, and nothing else runs a season-line statement. The module
  RUNS them too: since Phase 4, step 5 a reader in `compose/seasons.py`
  names the read it takes (`season_line.season_row`, `career_row`,
  `advanced_row`, `history_rows`, `history_total`, `netpoints_row` - the
  statement run and its row or rows handed back) and executes nothing
  itself, so the SQL ratchet counts those statements on `season_line`,
  the relation that owns the table. The line is a `Scalar` and
  the history a `Grouped` by `season` with a career's `summary` part, the
  comparison a `Grouped` by `subject`, each on a `Span` whose `source` is
  `"seasons"`; the line's `Scalar.how` is `"season"`, which is how `say()`
  tells it from the games relation's line, and a comparison over the
  games relation carries a `Met` cell). No shape has a presenter now (the team's went with step 4). Three rules the slice set, which every later slice follows:
  - **A note is written as data and said once.** The reader builds
    `Note("window_short", {found, asked, ...})`; the sayer phrases it
    (`say.note_phrase`, ONE phrase per kind) and records it through
    `notes.note`, so the answer's remarks are the Result's notes. The
    compiler's own sentence phrases the same kinds through `note_phrase`
    too (`compose.core._box_notes`, over `player_relation.box_score_notes_read`);
    `tests/query/test_answer_notes.py` reads a
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
    `filters()`) and the span's `years`. The "no games" reason is typed
    since 2026-10-05: `Result.empty` is a `result.Refusal` (a kind from
    `RUN_CAUSES` and its facts, from the shared
    `player_relation.no_narrowed_games`), said by `say.refusal_phrase`.
  - **What the relation measures lives on the relation; what a sayer says
    lives in the sayer.** A player's quarter or half (`compose/periods.py`,
    2026-10-04) took its data to `query/player_games.py`
    (`PERIOD_RECONCILIATION`, `PERIOD_RATES`, `period_distrust` - why a
    season is refused, as facts - and `period_agreement_notes` - its
    caveats, as notes) and its words to `compose/say.py` (`period_noun`,
    `period_caveat`, and the `period_untrusted` refusal's phrase). The relations' shared steps
    went home in step 6 (2026-10-05): the player relation's to
    `query/player_relation.py`, the team relation's to
    `query/team_relation.py`, the entity settlers (`resolved_player`,
    `resolved_team`, `optional_team`, `clarify`) to `entities.py`, the
    season words (`season_phrase`, `season_label`, `SEASON_TYPE_NAMES`) to
    `season_text.py`, the value words (`format_value`, `table_cell`,
    `count_games`) to `compose/say.py`, the season line's columns
    (`PLAYER_STAT_COLUMNS`, `HISTORY_COLUMNS`, `SHOOTING_STATS`,
    `ADVANCED_STATS`) to `season_line.py`, and the floors' declaration
    (`check_coverage`, `coverage_caveat`, `SOURCES`) to `coverage.py` -
    under public names, with no private alias kept. A relation's step
    writes no sentence: a refusal it finds (`no_narrowed_games`, `no_games`,
    `condition_team_no_games`, `entities.clarify`) is a typed
    `result.Refusal` or `result.Clarify` the sayer words; the one step that
    still phrases a note for the compiler's own sentence
    (`compose.core._box_notes`) takes the sayer's helper through a
    call-time import, since `compose` imports the relations. Since Phase 4,
    step 5 a statement a reader ran by hand is its relation's as well: a
    relation function builds it, runs it through the compiler's one door
    (a call-time import of `compose.core.values_of`, as the season line's
    reads do) and hands back rows or values, and the reader keeps its
    words and its Result. The period's: the league's ranking by a quarter's
    stat, one quarter of its points-by-quarter table and the most games in
    a pool narrowed to an opponent or a venue
    (`player_games.period_ranking`, `period_quarter`, `most_games`, beside
    the statements and `PERIOD_RECONCILIATION`), and a team's quarter or
    half (`team_games.period_games`); `compose/periods.py` runs only the
    compiled statements. The game log's and the splits': how many games a
    log was cut from and their seasons (`player_relation.log_total`),
    whether a narrowing left a player no games his span holds
    (`player_relation.narrowing_emptied`, beside `no_narrowed_games`), and
    a team's games with its narrowing off, which an empty team log names
    as missing (`team_games.unnarrowed_games`). The standings', a module
    of their own (`query/standings.py`, the team-season relation's reads
    of ESPN's `standings` and the season totals they are checked against:
    `season_row`, `career_rows`, `first_season`, `season_games`, with the
    four statements they run, built there since the step's close), which
    `compose/standings.py` reads. The season line's (`season_line.season_row`
    and the four beside it; `compose/seasons.py` executes nothing, and
    takes a comparison's NetPoints rows from the NetPoints relation,
    `netpoints.netpoints_row`, which the season line ran until the step's
    close), the
    team seasons' (`team_seasons`' `read_team_lines`, `read_team_records`
    and the five beside them, the team compiler's season total among
    them; `compose/team_stats.py` executes nothing), and the team-games
    relation's shared steps hold the three a team reader ran over the
    games (`team_relation.team_season_game_counts`, `team_game_list_gaps`,
    `team_games_total`; `compose/team_records.py` and `compose/team.py`
    execute nothing). And a single-game chart's label
    (`game_label.game_label`, which the fingerprint and the shot chart
    print) words the row the player-games relation reads for it
    (`player_games.game_label_row`, beside the relation's join) and runs
    nothing.
  - **Proved identical, text and all.** 628 of 628 recorded questions and
    1,391 of 1,391 unit-test calls; the slice rewords nothing. Step 0 grew
    the private-template-import ratchet by the shared steps the moved body
    called, with Jeff's say-so, because they were the relation's steps
    mis-homed in `templates.common`; step 6 moved them home and the
    ratchet reached 0 and was deleted.

*Lines 1829-1847; the rule now: "Two shapes have skeletons of their own".*

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

*Lines 1848-2021; the rule now: "Every shape the words ask is the compiler's", the causes, "The declared relations keep their own readers" and "A reader says what its row states".*

- **Every shape the words ask is the compiler's, and the answering loop asks it once.**
  `agent.py`'s `_run_compiled` hands `compose.answer(ctx, reading)` the
  point the parser read from the question's words once (`Reading.point`,
  `parse.read_route`'s last step) and the planner's verdict on it, planned once
  per question AFTER the parser has read the point
  (`compose.plan.plan_point`; the parser reads and does not plan). The
  planning is ONE value, the Query-or-Cause (`compose.plan.Verdict`, kept
  on `Agent.query`): the planned query, the refusal it came to unworded
  (a `result.Refusal`, worded by the sayer where it is answered) or the
  decline it gave the point up with (the `Unsupported` carrying its
  sentence, a `ShapeDeclined` carrying its cause); what the point is read
  and said as is `compose.plan.planned_shape`'s, from the Reading and the
  planning. The compiler runs the planned point and never reads the
  question itself. There is no slot door: a caller with a Reading of
  its own (a test handing the compiler a subject it built) reads the point
  into it with `parse.with_point`. Where the point reader refuses (a
  ranking by shot distance, by a stat nothing ranks, under a floor or in a
  unit nothing applies; a shape over a line missing its stat, its number
  or its subject; a matchup without two players; a quarter's figure the
  plays do not rebuild - seventeen kinds since 2026-10-05) the Reading
  carries a `reading.Cause` - a kind from the closed `CAUSES` and plain
  facts - and the planner says it (`compose.plan.refusal_result`): the
  reader builds no sentence, and a new cause is an entry there and a
  sentence here. It is raised as `reading.PointRefused`, a kind of
  `Unsupported`, so a shared helper the answer side also calls
  (`lines.measure_filters`, `measures.streak_column`) stays a decline
  there with its old message. A plain `Unsupported` out of the point
  reader is a decline, kept only where the reading hands the question on
  (another relation's question, an intent with no default point) or where
  `read_point`'s order makes it unreachable; one the user would see as
  "Nothing here answers this question" becomes a Cause (Jeff's rule,
  "Identical means identical"). An answer the answer side says
  (`answer.Answer`, which the sayer and the compiler's own sentence
  return - the text, the data and the files - and the answering loop
  stamps with the question, the path, the timing, the decisions and the
  planned shape) is answered
  `answered_by="fast"`, with the name readings and the coverage caveat
  attached - including when it is itself
  a refusal (a clarification, a "no match"): looking at the question and
  having something to say about it is an answer. A shape's own decline -
  a narrowing its words do not state, a point its only reader reads
  nothing of - is a cause since Phase 3, step 4
  (`reading.ShapeDeclined`, raised by `compose.plan.cells_declined` and
  `compose._read`, the planning itself where the planner declines, its
  cause handed to `compose.answer`'s `declined_by`; `cannot_honor`,
  `no_reading_of_point`), said by the planner in the decline's own
  sentence, word for word, at the decline's place - after the floor and
  the reading's causes - and answered `"fast"`: 6 of the 628 and 63 of
  the 2,082 moved so, nothing else. `None` is refused with
  the compiler's reason (the decline's sentence), after the coverage floor
  and the reading's own causes have had their look: a refusal the words
  come to before any point (`Reading.refused` - a championship, both ends
  of the span named at once, `window.contradicting_ends`, a window of
  games taken in each season, `window.per_season_window`, a fact no table
  holds or nothing reads - a draft position, an award, a birth date, a height,
  `lexicon.ABSENT_FACTS` - a player
  named on a question whose shape has no reading for one), which the
  planner says before any reader runs, and what the words name that
  nothing reads (`Reading.unsupported` - a playoff round, an age, a
  quarter's stat nothing rebuilds, bench points, ...), said by the planner
  only where the answer side declined, so a question the compiler answers
  is answered; and, since Phase 5, step 1 (2026-10-14), a word that
  carries a meaning the reading defaulted past (`Reading.defaulted`, one
  `Cause` with the words by class: an unread number that is no rate's
  unit, no season the span read and no read line's or period's value,
  `unread_number`; an unread stat word the measure tagger reads to a key
  other than the one taken, `unread_stat`; an unread window word where no
  window was read, `unread_window`; an unread month, or a slashed day
  after a word that leads a date, where no calendar narrowing was,
  `unread_date` - the lexicon's `MEANING_*` classes), read in the parser's
  last step after the unread words are known
  (`parse._reading_from_route_defaulted`), claiming its words so the ledger
  reads them, and said by the planner only where the planning produced a
  query (`plan.plan_point`), so a decline or a refusal the words came to
  otherwise keeps its own sentence and a question that would have been
  answered fluently past the word - "aaron gorodn lat 5 games" as his
  season averages, "anthony davis 2020 playoff first 3 rounds" as his
  whole postseason - is refused by name. A word that restates the shape ("record", "stats",
  "most") is in no class, a leftover of the read measure's own words
  ("point" of "point differential") names no other stat, and a figure
  written with a slash ("went 7/14 from three") is no day. The rule's
  harness is `~/association-research/stages/unread_meaning.py`, which
  classifies by the same guards; it read 86 of the 1,807 feed questions
  answered through a reader and 8 of the 628's 603 before the step, and
  0 and 0 after. All three are read by the parser (`parse.read_route`'s
  last step) and said through `refusal_phrase`; nothing after the parser
  re-reads the question for them.
  Nothing in the package may reach ollama - it is a compiler, not a
  model - and it narrows the relation only through the shared steps
  (`query/player_relation.py`, `query/team_relation.py`; see "A reader on
  a relation does not declare, or apply, scoping of its own" above). Every
  key the grammar names is the compiler's (`Reading.asked` set; "no
  shape", `None`, is refused with the reason "what its words ask has no
  reader"): the 25 keys the retired intents were and the count of players
  a season line holds (`reading.PLAYERS_COUNTED`, 2026-10-13), a team's
  best single game (`reading.TEAM_GAME_HIGHS`, 2026-10-13) and a team's
  history season by season (`reading.TEAM_SEASON_HISTORY`, 2026-10-14,
  read by `compose.team_stats.read_team_history`), whose names `ISSUES.md`,
  `CHANGES.md` and the history still use (`threshold_count`, `single_game_high`, `record_when`, `player_history`,
  `game_log`, `player_stat`, `player_splits`, `leaderboard`,
  `period_split`, `player_compare`, `streak`, `player_matchup`,
  `with_without`, `head_to_head`, `team_quarter_points`,
  `period_leaderboard`, `team_record`, `team_stat`, `team_leaderboard`,
  `team_outlook`, `coach`, `player_netpoints`, `fingerprint`,
  `shot_distance`, `shot_chart`). The NetPoints relation's two (on a
  declared relation, `reading.on_a_declared_relation` over
  `DECLARED_RELATIONS`, read as their default
  points on the declared `netpoints` relation and planned as
  `compose.netpoints.NetPointsQuery`) keep their own readers and renderer,
  per the decision "Charts are declared shapes", over the NetPoints
  relation (`query/netpoints.py`, since Phase 4, step 5: every statement
  the two read is built and run there, through `core.values_of`, and
  `compose/netpoints.py` and `fingerprint.py` run none):
  `read_player_netpoints` reads a season's line, its categories or one
  game's through it (`netpoints.season_totals`, `season_categories`,
  `game_line`); `read_fingerprint` reads through `query/fingerprint.py`'s
  loaders (the percentile pool and the polygons from one read, the
  relation's `season_fingerprints`, `game_fingerprints` and
  `fingerprint_game_of`) into a `Chart`, which
  `compose.netpoints.draw_fingerprint` writes with `query/radar.py` before
  `compose.say.say_fingerprint` words it - a reader reads and names no file
  that does not exist yet, and the sayer takes the Result and nothing
  else. The "vs" note for a fingerprint that drew one polygon is read by
  the parser (`Reading.left_out`, a `reading.LeftOut` of the players held
  and the names the words also hold - `subject.compared_but_unmatched`)
  and said by the sayer as the `name_left_out` decision
  (`say.say_left_out`); the answering loop attaches it after the coverage
  caveat (`agent._unmatched_fingerprint`), reading the Reading and nothing
  else. The shot relation's
  (`compose.shots.ShotQuery` on the declared `shots` relation, declined
  beyond what its retired template's words state, its shape's row of
  `RELATION_SCOPING_EXCLUDED`)
  is read the same way: `read_shot_chart` and `read_shot_distance` are the
  retired templates' reads moved whole, their statements the shot relation's since Phase 4, step 5
  (`query/shots.py`: `narrowed_games`, the games a narrowing sends a shot
  read to through the player-games relation's `games_subquery`;
  `distance`; `chart_shots`), executed through `core.values_of` there,
  over `shotchart.SHOT_VALUE_SQL` and the court's `HAS_POSITION_SQL` and
  `SHOT_DISTANCE_SQL` - fragments kept where they are, since the
  player-games relation's period rebuild reads the value too and the
  drawing the court's geometry - and said by the sayer - a
  shot chart's `Chart` (the marks, the counts drawn, the caption, the file
  name) drawn first by `compose.shots.draw_shot_chart` over
  `court.render_court_html`, between the read and the sayer, as the
  fingerprint is. A shape Phase 2's slice (iv) ported from a template the
  reader gave no point has a default point of its own
  (`point.DEFAULT_POINTS`: its readings gained one when it moved), is
  declined beyond the scoping its retired words state (its shape's row,
  `declined="plan"`) by the planner, in the sentence the retired scope
  check refused it with (`compose.plan.cells_declined`), and
  is read and said with nothing after it: a decline is refused, never
  handed to the compilers' own sentences (`compose._read_only`, a route
  marked `only`).
  **"The compiler answers them" means each is a reader and the sayer over
  a compiled statement** (the paragraphs above). Until Phase 2 most were
  still read and worded by the retired template's body through a
  presenter (`compose/present.py`): measured over the 277 yardstick
  questions (2026-09-30), of 205 answers by these intents the compiler's
  own SQL read 45 and its own sentence worded 16. The presenters went
  slice by slice, the last of them with step 4; the templates' registry
  emptied with step 5, and step 6 deleted `check_scope`,
  `HONORED_SCOPING`, `TEMPLATES`, the answering loop's template branch and
  `templates/` itself. A reader says what its retired template's words
  state (its shape's row of its relation's cell table since Phase 3, step
  2's closing slice) and steps aside for a
  narrowing beyond them, so the compiler's own
  sentence, which states every narrowing the relation applied, answers; a
  narrowing the relation cannot honor at all is refused by the planner,
  and the refusal names the planner's reason. Retiring a template was
  measured first: every call its unit tests made, and every recorded
  question it answered, answered both ways and compared - the recorded
  questions alone showed one shape the template still carried; the unit
  tests showed five. A change to a reader is proved the same way, on the
  stage snapshots and the unit-test calls.

*Lines 2022-2052; the rule now: "A team can be the subject, not only a narrowing".*

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
  `scoped_team`/`team_games`, the same shared steps every team reader
  narrows through - never a hand-written clause here either. A box-score
  count (3-pointers made, not a game-outcome figure) narrowed to a window is
  summed over the narrowed games' own team box rows, saying how many games
  had none (`compose.team.team_figure`, a LEFT JOIN of `team_box_stats` on
  the team's row or the opponent's, the P1 sweep's lane T, ISSUES.md #403;
  it refused before, for want of the join), and a team's log shows the same
  figure beside each game; a percentage is no total and is declined, and a
  figure no team game carries (pace, a rating, usage) is refused by name
  (`team_figure_unread`). `read_point` tries `team_read_point` before the
  league-wide reading, and nothing after the parser restores a subject:
  "magic" is also Magic Johnson's given name, and the compiler's own
  dropped-subject restoration (`repair()`, gone with plan item 6, step (e))
  once invented him from a team reference - the shape `subject.apply_subject`
  exists to catch. `team_named_in` restores a dropped team from the
  question's own team word, the way `players_named_in` does a player.

*Lines 2061-2135; the rule now: "The model": the prompt and the schema, the token budget, required slots, hashing the prompt.*

- **A prompt and its constrained schema must agree.** A value a prompt
  teaches that the schema's enum lacks can never be emitted under
  constrained decoding, so it silently becomes something else: the router's
  `player_compare` was described in its prompt and missing from its schema's
  enum, and every comparison routed to `player_stat`.
  `test_the_prompt_and_the_schema_agree` (`tests/query/test_normalizer.py`)
  holds the normalizer's examples to its enum; the other half of what the
  router's pair of tests guarded - that every shape is reachable - is
  `test_every_intent_is_reachable_from_the_reader`
  (`tests/query/test_grammar.py`, the grammar's structural tests - the
  reader's behavior on a wording is a case in `tests/query/reader_cases.json`,
  regenerated by `scripts/reader_cases.py`): the parser's `PARENT_GRAMMAR`, the
  grammar's `CODE_ASSIGNED_ASKS` or the subject reading's `KIND_ASSIGNED_ASKS`.
- **A prompt has a token budget, and ollama enforces none.** ollama
  truncates an over-length prompt *silently and head-first*: the retired
  agent's original bug was a 10,295-token preamble against `NUM_CTX = 8192`,
  which discarded the schema and correctness rules while keeping the tool
  descriptions, and nothing said so. The normalizer's prompt is ~330 tokens
  against `NORMALIZER_NUM_CTX = 2048`, and
  `test_the_prompt_leaves_room_for_the_question_and_the_reply`
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
  for - and the normalizer's `stat` is required too. The measure tagger
  drops it on the whole-line readers where the words name no stat
  (`measure.names_a_stat` over `lexicon.STAT_WORDS`). Note why the word list can be
  loose there and could not be anywhere else: for a comparison, a missed word
  widens the answer to a line that still holds the stat asked about, while
  `leaderboard` with no stat has nothing to rank by. The drop is the
  MODEL's key's alone: until the measure slice it took a measure the WORDS
  named where the list missed its word ("grayson allen 3s made last
  season": "3s" is a box-score word and no `STAT_WORDS` word, so the
  3-pointers the grammar read went with the key and his points line
  answered; 6 feed answers moved when the rule was narrowed, each named in
  the commit). The comparison keeps dropping the words' key too - its whole
  line holds the stat, and its reader refuses a named one.

  Two ways out, and prefer the second. Making a slot *required* works (that
  is why `stat` is) but was measured and reverted for the router's
  `season_ref`, because requiring more slots crowds out others. Reading the
  value **from the question text** costs nothing and cannot move any other
  slot: that is what the parser does for every slot but the names and the
  stat - and for the stat too, where the words name one (the measure
  tagger reads the words over the key) - and what `_validate_season` and
  `_validate_side` did for the
  router. Hash `NORMALIZER_PROMPT` and `NORMALIZER_SCHEMA` before and after a
  change to prove the model's input is unchanged - if both hashes match, the
  normalizer's recorded replies still stand and the stage snapshots
  (below) are the whole check.
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

*Lines 2136-2294; the rule now: "What the words ask": the key, the children, "A grammar reads the values the taggers typed", "A refusal names the missing thing".*

- **What the words ask comes from the question's own words, never from the model.**
  Since Phase 3, step 4 the grammar names no intent: it names a
  `reading.PointShape` - the relation, the shape and the `by` of the point
  the words ask for bare (`Reading.asked`, `Subject.asked`;
  `None` for no shape, the retired `other`), one key per retired intent
  (`reading.PLAYER_LOG`, `PLAYER_LINE`, `PLAYER_RANKING`, ... `TEAM_COACH`);
  a sentence says a key in words (`say.asked_as`), the trace and the
  records by `reading.key_path`. The kind is no part of the key: it is the subject reading's,
  read before the grammar, which the grammar is keyed BY. Measured first
  (`~/association-research/stages/intent_family.py`, the 2,710
  readings): an intent maps to up to four planned keys (`player_stat`:
  the season line 405, the games 262, rows by date 15 - the relation and
  sometimes the shape follow the typed filters), and two planned keys are
  reached by more than one intent (a player's games by date from
  `game_log` 297 and `player_stat` 15; the game-level ranking from
  `leaderboard` 367, `game_log` 3, `other` 1) - so the key is what the
  words ASK and the planned `PointShape` what the point IS. Every intent set and intent-keyed table is a
  set of those keys or a predicate on one (`reading.PLAYER_ASKS`,
  `TEAM_ONLY_ASKS`, `PLAYER_REQUIRED_ASKS`, `SUBJECT_RESTORABLE_ASKS`,
  `OWN_TEAM_RESTORABLE_ASKS`, `on_a_declared_relation`, `point.DEFAULT_POINTS`, `TEAM_SEASON_POINTS`,
  `_ROWS_BY`, `_COUNT_SHAPES`, the key's readings of the taggers' families
  and their sets - `point.THRESHOLD_ASKS`, `PERIOD_ASKS`, `ORDER_ASKS`,
  ...), and every key is the compiler's.
  The parser's `PARENT_GRAMMAR` names the parent by the subject's kind, and
  the grammar's rules assign `grammar.CODE_ASSIGNED_ASKS` (a player's
  quarter, players ranked by one, a coach) from the text, so a new shape is
  a grammar row or a rule, with no prompt edit and nothing to move on
  another question. A refusal especially: a question
  nothing can answer needs the model's help least. `coach` is the worked
  example - the word is unmistakable, nothing else in the warehouse is named
  it, and a bare surname is deliberately not matched ("nurse" and "rivers"
  are ordinary words, the substring trap `players_named_in` exists for).
  Since 2026-10-05 it has no template: the point reader refuses it by a
  cause (`reading.Cause("no_coach_table")`, `point._read_point`) and the
  planner says it (`compose.plan.refusal_result`, `COACH_REFUSAL`) - a
  refusal the reading comes to is a cause, not a body to run. A refusal
  the reading comes to has no point, so no coverage floor is checked for
  it (`coverage.sources_for(None, ...)` is empty): appending "there is no
  data for 1996" to a sentence that already explains what is missing would
  name a second, wrong cause.

  The children come one step later, where the subject's KIND is known:
  `subject.KIND_ASSIGNED_ASKS` (`_CHILD_GRAMMARS`). A child of a parent
  the grammar names - a count of 30+ point games under `game_log`, a history
  over the past 4 seasons under `player_stat`, a streak under `team_record` -
  is named by its words AND gated on the kind the reading settled, which is
  what keeps "how many times did the 76ers play boston" (two teams) off
  `threshold_count` and "who lead the league in avg 3 point distance" (no
  player) off `shot_distance`. Measured when the seven left the router's
  prompt: 0 false positives over 261 recorded questions of other intents.
  **A grammar reads the values the taggers typed, never their words**
  (Phase 3, step 4, slices (b) and (c), as marks written into the question;
  since Phase 4, step 6 by value). What a grammar reads is the grammar's
  text, `lexicon.GrammarText`: the question's words with every character
  a tagger typed written as `lexicon.TYPED` (U+FFFC, the same for every
  kind, so a pattern over the words sees that something stands there -
  no word, no space, no digit - and never what), and beside them what was
  typed at each character, a closed `lexicon.TypedKind`: a line's number
  by how it is stated (`line_plus`, `line_at_least`, `line_number`), the
  words that lead a line under a number and a line's stat (`line_lead`,
  `line_stat`) - the lines tagger's (`line.LinesNamed`); the opponent the
  subject reading read, with its versus word, by that word
  (`opponent_against`, `opponent_versus`, `opponent`;
  `subject.opponent_read`); each companion's phrase as the subject reading
  read it, by whether a presence word is in it (`companion_with`, which the
  with/without split's rows read, or `companion`; the phrases whole, from
  `subject.Subject.companion_phrases` - the probed claims are cut to the
  words the companion reading needed and can land on a word a row reads);
  each window of games the window tagger reads (`window`:
  `window.WindowCandidates.of_games`, the grammar's end and count and the
  looser end words - not a ranking's "top 5", a count of seasons or one
  game named as the subject's own, whose stretch reaches over words other
  rows read); and a day written in numbers, or a range opened on one
  (`date`: `cuts.CutsRead.dated`, "since 1/26/20", "on 2026-01-02"; a
  worded calendar day and a month stay words). The child grammars read the text with each line typed whole
  (`LinesNamed.marked`); the parent grammar and the grammar's rules with
  each line's NUMBER typed (`numbered`: they read a stat word as a
  measure's, "most 10 points games" is no team ranking) and the rules the
  typed values beside it (`LinesNamed.threshold`, `below`); the parent
  grammar the rest typed over it, a later value over an earlier, a line
  inside a companion's phrase kept the line (`parse.grammar_text`, which
  called with the question alone types the windows and the days, for a
  test of the rows over words alone). A value is asked BY VALUE, two ways.
  A parent row is a predicate over the text (`lexicon.GrammarRow`): words
  read anywhere (`Words`, the lexicon's `*_ROW` patterns), a value
  standing anywhere (`Stands`: "a window of games is read here"), right
  after the words that lead it (`StandsAfter`: "games" and then the
  opponent, `_PLAYER_LOG`'s "games against boston") or right before the
  words after it (`StandsBefore`: a window and then "log"), and any, all
  or none of those (`AnyOf`, `AllOf`, `NoneOf`) - `parse.PARENT_GRAMMAR`
  holds the rows, which kinds each applies to and the shape it names; the
  lexicon holds the words. A child grammar's or the rules' pattern that
  reads a value WHERE it stands among the words ("a line's number, then
  its stat, then 'games'") is an `lexicon.Ask`: a regex whose values are
  `lexicon.typed(kind, ...)` fragments, matched against the text with each
  typed character spelled by its kind (`GrammarText.spelled`, built from
  the typed values when the ask is searched and nowhere else - a character
  of that alphabet in the question's own words is written as `TYPED`, so
  no question can spell a value by typing it; ISSUES.md #383, where a
  typed mark forged a 10-game window, needs no intake since). The rules
  are handed the team nicknames the words hold (`subject.team_words_in`,
  read once by `grammar.settle` and claimed as the `team` where a rule
  writes one), a reached companion's "when" clause (`reached_when`;
  `lexicon.WHEN_REACHES` stays for a clause with no companion in it) and
  his name as a line record's player (`grammar._restored_subject`). Every
  typed value keeps each character where it stands, so a row's distances
  hold and a child's words in its decision are the question's own, and a
  rule's probe deletes a word from the text with the values in place
  (`GrammarText.without`, which `span.needed`/`read_by` take as they take a
  string). A grammar so claims its own words around the value and the
  value's characters are its tagger's: measured on the 2,710 readings
  (`route_family.py`), `intent+line` 17 -> 0 and `intent+opponent` 5 -> 0
  joined claims at slice (b), every reading, answer and unread word
  identical, and the 11 left but #354's at slice (c), where a window read
  as the typed window no longer needs its "games" (48 of the 2,710
  readings' unread words grew by it, 12 of the 628's) and a broader window
  the tagger reads is a log as "last 10 games" is (14 readings' key moved
  and 10 answers with them, 1 of the 628 and 9 feed, each named in
  `CHANGES.md`; kept by Jeff's ruling, 2026-10-11 - two of them answer a
  narrower question than asked, `ISSUES.md`); Phase 4, step 6's move from
  marks to values moved none of the 2,710 readings, claims included. A
  row that reads a number or a versus word reads the typed value beside
  it (`N_PLUS`, `LINE_NUMBER_NO_PLUS`, `MEETING_WORDS`, `_PAIR_MEETING`); a
  new row does the same. The count's child grammar reads no bare digits
  beside its lines: a count is named from a line the tagger read, so a
  number no line is read from ("games with 40 field goal percentage")
  stays in `Reading.unread`, where it was hidden in the child's claim
  (`intent+measure`, 11 -> 0; ISSUES.md, the P1 it hid). A season type or
  a venue may stand between the typed line and "games" ("how many 40
  point playoff games does kobe have", ISSUES.md #375).
  The child is named from the grammar alone (`subject.child_named`), and
  the grammar's rules run ONCE, under it (`parse._read_route_staged`); they
  may decline it (a count with no threshold is a ranking), and only then
  run again under the parent - 4 of the 628 recorded questions, every one a
  history read as a line - before the taggers run once, under the key that
  stands. Until 2026-10-02 the stages ran under the parent,
  again under each child to see whether it held, and once more under the
  one that did (1.7 runs a question, up to four), and the subject reading
  ran them too, under "other", before the parser ran them at all.
  `subject.read_subject` reads and decides nothing (under no shape,
  `None`); `subject.settle_subject` writes the key and why with no rule
  run, and the parser's last step reads the SETTLED subject and settles
  nothing. Add a
  case to `port_check.py`'s corpus (`~/association-research/intent-shrink/`)
  and to `tests/query/test_subject.py` for each wording a grammar gains.
- **A refusal names the missing thing, never only the slot.**
  `check_coverage`'s reasoning, and it applies past the floors: a coach
  question once reached an agent that queried tables with no coach column and
  was then free to fill the silence from its own weights; refused for want
  of a reading it would tell the reader nothing. A shape the
  warehouse has no column for is a cause the READER comes to from the
  words - a kind in `reading.CAUSES` with plain facts, carried on the
  Reading (`point_refusal`, `refused` or `unsupported`) and said by the
  planner through `say.refusal_phrase`; `coach` (`no_coach_table`) and
  `Reading.unsupported`'s seven are the worked cases. Never a sentence
  built where the question is re-read after the parser. Before
  writing the refusal, check what the source actually serves - "ESPN does not publish
  coaches" was the obvious sentence and it is false, and a refusal naming the
  wrong cause reads as honest while sending the reader somewhere useless.

*Lines 2295-2333; the rule now: "Regression nets and the research repository", "The recorded replies are this machine's", "Only one ollama caller at a time".*

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

### Working on the fetch path

*Lines 2337-2355; the rule now: the three things that keep `data pull` cheap to re-run.*

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

*Lines 2357-2362; the rule now: "A change that alters warehouse data is not finished until the warehouse holds it".*

**A change that alters warehouse data is not finished until the warehouse
holds it.** This covers a parser fix, a new or reshaped view, a new column, a
corrected derived table, and a fetch fix. A fix merged but never loaded looks
done in the code and stays wrong in every answer. The view fixes in `220f8aa`
were in the code for hours, and reached the warehouse only when someone ran a
separate `data load`. Do both halves:

*Lines 2397-2408; the rule now: "Fetching is latency-bound, and the fetch path is threaded".*

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

*Lines 2410-2453; the rule now: "A NetPoints date is not an ESPN date" and its three load-bearing rules.*

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

*Lines 2455-2462; the rule now: "Filtering a read by season prunes no files".*

**Filtering a read by season prunes no files.** The trees are laid out under
`season=X/season_type=Y` directories but read raw, not hive partitioned (the
reason is in the comment above `TABLES` — the directory names would collide
with the embedded columns), so `WHERE season = 2024` over `read_parquet` is a
filter applied *after* reading all 40,558 `games` files, not a way to read
fewer. `data check` counted one season/season_type per query on that
assumption and took 7.5 minutes for what one grouped scan does in 3 seconds.
Count once and group; do not filter in a loop.

*Lines 2464-2493; the rule now: "A full `warehouse.build()` is memory-hungry, and needs two settings".*

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

### Working on the web path

*Lines 2500-2505; the rule now: "The server answers one question at a time".*

- **The server answers one question at a time, and that is not caution.**
  ollama keeps a single KV cache slot per model, so two questions in flight
  evict each other's prefix and both come back slow (1.3s becomes 11.2s).
  `AgentRunner` holds a `threading.Lock` for the whole of `ask`. A
  `threading.Lock` and not `asyncio.Lock`: everything below is blocking, and
  FastAPI runs `def` endpoints in a threadpool already.

*Lines 2509-2526; the rule now: "Nothing in `web/app.py` may import a model client" and "Events carry trace lines verbatim".*

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
- **Events carry trace lines verbatim.** Do not parse `"-> (compose) ... shape=..."`
  back into structured fields. Everything a client acts on — which path
  answered, the planned shape (`Answer.shape`, the `reading.PointShape` the
  answer side chose its reader by: relation, shape, by; since Phase 4,
  step 1), the timing, the artifacts — is on the `answer` event,
  as values, because Phase 0 put it there. Parsing prose back out is the exact
  move that phase removed.

*Lines 2536-2543; the rule now: "`GET /api/artifacts/{name}` serves a directory a person owns".*

- **`GET /api/artifacts/{name}` serves a directory a person owns.** The name is
  checked twice, and the two checks stop different things: an allowlist regex
  rules out anything shaped like a path, and resolving the file and requiring
  it to sit directly in the resolved output directory catches a symlink whose
  *name* is perfectly innocent. Keep both. Test the guard directly as well as
  through the route - measured, with the guard removed most traversal names
  still 404 because Starlette never matches a path parameter containing a
  separator, so a route-only test proves less than it looks like it does.

*Lines 2548-2564; the rule now: "The page renders an answer by its planned shape".*

- **The page renders an answer by its planned shape, never by its label.**
  `static/index.html`'s `RENDERERS` holds one renderer per `reading.Shape`
  (`scalar`, `rows`, `ranking`, `comparison`, `split`, `runs`, `chart`;
  Phase 4, step 1, where 22 were keyed by `Answer.intent`), read off
  `Answer.shape`. Each names the bodies its shape's readers say (`bodies`:
  a draw and the `Answer.data` keys it `needs`) and chooses one by the
  shape's `by`, the relation where two readers of one `by` say different
  bodies, and the compiler's own body (`skeleton`) where no reader took the
  point (`of`); a chart's body is its artifacts' frames, each framed by its
  kind (`chartFrame`). An answer with no shape - a refusal, a turn this
  browser kept from before - is its sentence. The bodies still read each
  reader's own `data` keys, since the API's data is the sayer's per-reader
  dict and not the typed body (ISSUES.md, "No future template gets a
  renderer for free"). A change to a renderer is proven by rendering every
  recorded answer through the page before and after
  (`~/association-research/stages/page_renders.py`, Chromium, the stage
  snapshots' records, seconds): step 1 drew 2,709 of 2,710 identically.

### Data gotchas

*Lines 2588-2599; the rule now: "This season" is the latest season with games on record.*

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

*Lines 2610-2619; the rule now: "Do not conclude a source does not publish something from one file's schema".*

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

*Lines 2634-2690; the rule now: the coverage floors' three rules, shot coordinates, the career endpoint, the empty team-seasons and the team-box-only fault.*

- **Each table starts in a different year, and the gaps are ESPN's, not ours.**
  A question is only answerable as far back as its *narrowest* table, and there
  is no pull that fills these in. **`DATA.md` ("Coverage floors") has the table
  and what is before each floor**; `association/nba/coverage.py` is the enforced
  copy. Three rules to carry while writing code:
  - **Select a postseason by the calendar year it was played in, never by
    label** (`team_relation.team_span_clause`, `team_metrics.games_scope`,
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
  Where those games are rebuilt from play-by-play, a measure the rebuild
  never measured (a rate over attempts, turnovers, minutes:
  `core.unread_on_rebuilt`) is read over the fetched games alone, so it
  carries its own count (`"<measure>_games"`), a ranking by it qualifies on
  that count, and the answer says the games it could not read (a
  `lines_rebuilt` note, `what` "unread"; ISSUES.md #327, closed).

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

*Lines 2693-2741; the rule now: "The floors are enforced, not just documented".*

**Those floors are enforced, not just documented.** `association/nba/coverage.py`
holds them as a table — `COVERAGE`, one entry per queryable table — and
`query/coverage.py`'s `check_coverage(shape, scope)` refuses a question
whose planned point lands under one. Add an entry whenever a reader reads
a new table, and declare the shape's tables in `coverage.SOURCES`, keyed
by the planned point's `reading.PointShape` (one entry per route in
`compose._ROUTES`, which a test holds equal; resolved per question where the table depends on the
measure asked for - an advanced stat, a ranking's metric, a team ranking's
standings - and `RELATION_SOURCES` for a point no reader takes; keyed by
intent and resolved from slot lists of its own until Phase 3, step 1,
which is how `player_stat`'s floor read four slots where the reader read
a longer list, ISSUES.md #212, closed). The answering loop checks the
floor of the point the words named (`compose.plan.planned_shape`, on every
verdict) over the point's own scope; a reading with no point has no floor. Three
things about that module are load-bearing:

- **It returns the refusal rather than raising it.** That is the opposite of
  a reader declining a narrowing, and deliberate: a decline lets the
  compiler's own sentence take its turn at the same point, and it may do
  better. Nothing does better here: a season under the floor is empty for
  every reader, and (while the agent existed) an agent handed it queried the
  same empty tables, more slowly, and was then free to fill the silence from
  its own weights.
- **A lookup and a ranking have different floors.** `player_season_stats` holds
  Michael Jordan's real 1990 line, so his own average is answerable from it;
  ranking that season is not, because the pool is 217 players against a
  ~350-player league. In 1980 the pool is *seven* — and before this existed,
  "who led the league in scoring in 1980" answered "Moses Malone, at 25.8.
  Next: Bill Cartwright (21.7)". Kareem, Bird and Erving are not in `players`
  at all. `first_ranking_season` is that second floor, and it applies
  where the planned shape is a `ranking` (the flag only speaks through a
  season-line table's ranking floor; on a count's or a high's box-score
  tables the box floor is the narrower and names itself either way).
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

### Verifying your work

*Lines 2747-2762; the rule now: "A refactor is proven by a golden comparison, not by the suite alone".*

- **A refactor is proven by a golden comparison, not by the suite alone.** The
  complexity refactor that split the router's `route()` (its stages became
  `router._settle`'s, and the grammar's rules and the taggers in Phase 3,
  step 4), `parse_game_summary` and the
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

*Lines 2768-2774; the rule now: "Read the fixture" and "Exit 0 is not proof".*

- **Read the fixture, do not guess what it contains.** Several wrong test
  assertions came from assuming a shot count or a made/attempted split.
- **Exit 0 is not proof.** A Sphinx build passed `-W` with the version variable
  silently deleted, because `release` is optional. Check the rendered output,
  the built artifact, the actual string — not the return code. And beware that
  `cmd | tail` reports *tail's* status: a failing `data load` read as exit 0
  that way, twice, before it was piped to a file instead.

*Lines 2775-2782; the rule now: "Check rendered HTML in a real browser engine" and "Install and run it".*

- **Check rendered HTML output in a real browser engine.** The chart pages
  paint from CSS variables, and WeasyPrint does not resolve them inside SVG —
  it drew the fingerprint's blue polygon gray and its group-colored labels
  black, which would have shipped as a wrong screenshot. A headless Chromium
  renders them correctly (the `docs/_static` shots are made that way).
- **Install and run it, for anything packaging-related.** The wheel and sdist
  are verified by installing into a fresh venv *outside the repo* and running
  the CLI; nothing else proves the entry point and `py.typed` survived.

*Lines 2785-2832; the rule now: the stale `.pyc`, "A guard is worth nothing until you have watched it fail", `scripts/perturb.py`'s three traps, import success, the worktree's installed package, parallel branches' helper names.*

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

*Lines 2833-2859; the rule now: the `&&` merge chain, `CLAUDE.md`, `/tmp`, `git rebase --exec`.*

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
- **Never gate rebased commits with `git rebase --exec` running the suite
  or the hooks.** The rebase exports `GIT_DIR` to the command, and the tests
  that build a scratch repository (`git init`, `git config`, `git commit`
  in a `tmp_path`) and the scripts they call inherited it: on 2026-10-12 they
  wrote into the real shared repository - `core.bare=true` and
  `user.name=Test` in its config, test commits on a worktree's HEAD, its
  index clobbered. `tests/conftest.py` removes every `GIT_*` variable before
  a test runs now (`without_git_variables`), but a hook is not a test: squash
  or reorder with no `--exec`, then run the gate chain once on the tip, as a
  plain command.

### Saying what you measured

*Lines 2864-2895; the rule now: the three rules and "One concept, one definition".*

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
  report a reader's behavior from a direct call when the answering loop adds
  something - `agent.py` appends the coverage caveat, so a reader called
  directly looks like it is missing one. Compare against `real_games` rather
  than `games` for anything counted against `team_season_stats`; two "new
  findings" in one session were known phantom rows seen through the unfiltered
  table.

**One concept, one definition.** Ruff's F811 and mypy's `no-redef` catch a name
defined twice in one module and are blind to the same name in two - so
`MAX_LIMIT` was 100 in `query/leaderboard.py` and 50 in `query/templates/common.py`
until 2026-10-05 (one cap now, `reading.MAX_LIMIT`), and the NBA's five-hour
Eastern offset was once declared six times under five names.
`scripts/check_duplicate_names.py` reports cross-module constant collisions
against an allowlist of the ones already filed, so it fails only on a new one.
It is deliberately name-only: comparing values pairs `DEFAULT_LIMIT` with
`EASTERN_OFFSET_HOURS` and buries the real finding in coincidence. It also
cannot see a concept duplicated under *different* names, which is most of those
six Eastern offsets - that still needs somebody reading a grep.

### Recording findings

*Lines 2899-2904; the rule now: "Anything that needs follow-up goes in `ISSUES.md`".*

**Anything that needs follow-up goes in `ISSUES.md`, including what you were
not looking for.** Most of this project's data problems were found in passing:
the empty 2013-18 box scores turned up while building a streak template, and
the missing 2000 and 2001 playoff games while checking coverage floors. Each
then lived only in a chat report. A finding that is not in the file is one the
next agent has to rediscover from scratch, or never does.

*Lines 2965-3027; the rule now: "Dispatching agents": the four rules of 2026-09-28 and the re-verification after a merge.*

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

### Releasing

*Lines 3035-3061; the rule now: "The distribution is `association-py`", Trusted Publishing, the changelog through stdin.*

**The distribution is `association-py`; the import package, the `association`
command and the repository keep the bare name.** Decided 2026-10-09, when
the account was back and PyPI refused `association` as a project name though
nobody holds it - no project row (PyPI's per-project RSS route answers 404,
as for a made-up name, where a registered project with no releases answers
200), no release ever, no name confusable with it under PyPI's rule over its
full index of 909,700 projects, and no standard-library module; of the four
causes PyPI's own help page lists, that leaves an administrator's
prohibition, for which it gives no appeal. So `pyproject.toml`'s `name`,
`association.__version__`'s metadata lookup, `docs/conf.py` and the web
extra's install hint (`association-py[web]`) read the new name, and the
wheel is `association_py-X.Y.Z-py3-none-any.whl`. Publishing is Trusted
Publishing (OIDC) and nothing is stored in the repository: the publisher on
PyPI is registered for project `association-py`, owner `jeffknupp`,
repository `association`, workflow `publish.yml`, environment `pypi` - the
environment name included, since the job runs under it and a publisher
without it does not match. 5.0.0 was the first upload (2026-10-09), checked
by installing `association-py[web]==5.0.0` from PyPI into a fresh venv
outside the repo and running the CLI; the tags before it (4.0.1 to 4.4.0)
carry the old name in their metadata, cannot be uploaded, and stay on
GitHub only. The install lines in `README.md`, `docs/installation.rst` and
`docs/usage.rst` are PyPI's and pin no tag; `bump_version.py` still rewrites
any tag pin a doc grows. One more thing the first upload taught: a
changelog section can be larger than an argument or an environment variable
may hold (128 KiB; 5.0.0's was 293,016 bytes), so `release.sh` passes the
notes through stdin at both points, and the release page carries the
section's leading entries with a pointer to the rest.
