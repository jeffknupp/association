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
| sweep + team parity | `43f242f` | **136 / 175 (77.7%)** | **125 / 166 (75.3%)** |

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

## Where the remaining failures are

On the sweep run (`live_sweep.jsonl`, 175 primaries): 5 wrong, 7 partial,
25 fall-throughs, 2 clarifications that should not have been asked. By cause:

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

1. **The seven partials** (#203) - a window that cuts qualifying games says
   "10 of 49"; a career line under a per-season table; attempts and
   percentage beside a made count; the stat asked for in a splits table; 50
   rows and teams where asked. Template-side, in flight.
2. **Compiler moves** - a ranking of the games that satisfy a boolean measure
   by another measure ("highest scoring triple doubles", #199); a league-wide
   count with several lines; a position word as the subject; the box-score
   caveats a composed answer still lacks (#197). In flight.
3. **Merge, gate, live run, grade** - the same loop as every landing.
4. **The five still wrong** - one is a `record_when` question the router now
   reads (F087, fixed); one is period data; two need a clarification the
   system does not ask; one is a pair with a with/without split.

## The current plan: what buys the most correctness next

In order of lift per unit of structural change, each measured on the
yardstick before the next starts:

1. **Subject kinds, decided once.** A question's subject is a player, a
   team, a position group, everyone, or a pair - today that is inferred in
   four places (the router's slots, `entities.scope_from_question`, the
   compiler's `move_point`, the refusals). One reading, made from the
   question's own words before any template runs, that files the subject
   kind and name and refuses by name when the intent cannot be about it.
   This is the "names are selected, not generated" tension from the first
   plan, closed by construction: every name a template reads is a span of
   the question.
2. **Skeleton x measure over both relations, in the compiler, and the intent
   enum shrinks.** The compiler already answers rows / scalar / grouped over
   two relations; each template it reproduces exactly is one the router no
   longer needs an intent for. Fewer intents means a shorter prompt, and the
   prompt is the router's ceiling. `CODE_ASSIGNED_INTENTS` is the route for
   any shape the question's own words name.
3. **The pair relation.** Two players' games joined on the event, under both
   guards (`paired_rows_sql` exists); `player_matchup` is its one template.
   Head-to-head, "vs <player>", a record against a player, with/without over
   a pair - a whole family of fall-throughs and one of the five wrong.
4. **The period relation.** Per-quarter figures beyond points, rebuilt from
   plays the way points are, as one relation the compiler can read - a
   player's or a team's quarter as a narrowing rather than three templates.
5. **Conference and division** from the standings we already fetch - the
   last unanswerable narrowing that is answerable in principle.

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
  keeps the structural change, and this file says which it is.
