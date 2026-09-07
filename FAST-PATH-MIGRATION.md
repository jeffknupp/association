# Query fast-path migration

Staged replacement of the single "understand the question AND write the SQL"
model call with a router → template → answer pipeline.

**Landed:** stage 1 (router + `threshold_count`), 2.0 (`entities.py`), 2.1
(`leaderboard`), 2.2 (`player_stat`), 2.3 (double/triple-doubles), 2.4
(`game_log` + `team_record`), 2.5 (`shot_chart`), stage 3 (per-question
prompt assembly + budget guard). **The migration is complete.** Remaining
ideas are in "What is left" at the end.

## Why

`SYSTEM_PROMPT + TOOLS` reached **10,295 tokens** against `NUM_CTX = 8192`.
ollama truncates head-first, so only 4,098 tokens ever reached the model, and
the discarded head held `TABLE_SUMMARY`, both STANDING RULEs, and the first
~15 KNOWLEDGE_BASE entries. Measured on this box (8 CPU cores, no GPU):

| | before | after stage 1 |
|---|---|---|
| "most 30+ point games" | 385s, wrong season, wrong question | **2.5s warm / 12.0s cold, correct** |
| model calls | 5 | 1 |
| tokens per call | 4,098 (of 10,295 intended) | 431 |

Two effects compound. A prompt that fits stays in ollama's KV prefix cache
(measured: 2.9s vs 62.8s on a second turn); a truncated one re-prefills every
iteration because the truncation offset slides as the conversation grows.

## Shape of the system

```
question → route()          ~430 tok, constrained JSON, ~1.5s warm
         → TEMPLATES[intent]  deterministic SQL, ~0.02s
         → TemplateResult.answer   deterministic prose, no model call
                    ↓ intent not ported, or slots fail validation
         → the existing tool-calling agent, unchanged
```

Falling through costs one ~1.5s round trip and changes no answer. That
asymmetry is the whole reason shapes can be ported one at a time.

## Stage 2 — port the remaining shapes

Order is by traffic × cheapness. Each step is independently shippable and
independently revertable.

### 2.0 Shared entity resolution (prerequisite) — DONE

Three of the five shapes below need "Lakers"/"LAL"/"Luka" → a real id.
`toolbox.get_leaderboard` and `toolbox.render_shot_chart` each already
implement this differently. Extract one `entities.py`:

- `resolve_player(con, text) -> PlayerRef | Ambiguous | NotFound`
- `resolve_team(con, text) -> TeamRef | Ambiguous | NotFound`

Ambiguity must be a return value, not a guess — `render_shot_chart` currently
takes `match[0]` and mentions the others in a trailing note, which is fine for
a chart and wrong for a number. Templates return `TemplateUnsupported` on
ambiguity so the question falls through rather than answering about the wrong
player.

Landed as `query/entities.py`: `find_*` returns every candidate best-first
(the caller decides), `resolve_*` returns `Entity | Ambiguous | NotFound` and
never guesses. `get_leaderboard` and `render_shot_chart` both moved onto it,
each keeping its existing behavior — a chart of the wrong Curry is obvious on
sight, a *number* attributed to the wrong Curry is not, so only the chart takes
a best match.

The `team` slot gap is fixed: a worked example (`Top 5 scorers on the Lakers?`)
was added to `ROUTER_PROMPT` and "Lakers" now resolves.

**How 2.2 resolved the ambiguity gap — not as planned.** The plan called for
a prominence tiebreak (most minutes, with a dominance threshold). Measured
against real data, no threshold works: on season minutes "Luka" separates only
2.05× (Doncic vs. Garza), and on season points the ratios are 3.8× for "Luka"
but 3.1× for "Brown" — where Jaylen vs. Bruce Brown is genuinely ambiguous.
Any threshold that resolves Luka also resolves Brown, wrongly and silently.

So `player_stat` **asks** instead: an ambiguous name returns a clarification
("'Luka' matches more than one player - did you mean Luka Doncic or Luka
Garza?") in ~1.5s. That is a handled outcome, not a fall-through — the
template knows exactly what is ambiguous, so passing the problem to an agent
that would spend minutes and then guess is strictly worse. No threshold to
tune, no silent misattribution.

### 2.1 `leaderboard` — highest value — DONE

Wraps the existing `toolbox.get_leaderboard`, which is already stage-2-shaped:
it owns the season default, min-sample floors, and traded-player dedup.

Landed as `query/leaderboard.py`: the query itself was extracted out of
`toolbox.get_leaderboard`, which is now a thin JSON wrapper over it, so the
template and the agent tool are one implementation rather than two that drift.

One deviation from the original plan, deliberately: the `stat` slot maps onto
`LEADERBOARD_METRICS` through an **explicit alias table**, not
`get_close_matches`. Fuzzy matching is right for suggesting a fix to a model
that can then correct itself, but a template silently ranking by whichever
metric scored highest is exactly the substitution failure this architecture
exists to prevent. Unmapped names fall through.

Also added while here: a `season_type` slot (`regular`/`playoffs`). Without it
a playoff question silently answered for the regular season — the same
"answered an easier question and said nothing" failure the standing rules were
written for. Both templates honour it and name the period in every answer.

**Still to retire in stage 3:** `get_leaderboard`'s **939 tokens** of tool
schema — the single largest fixed cost in `TOOLS`, most of it the 80-name
metric enum — plus 4 KB entries (~1,574 tok): traded-player dedup, "per
game"/"average", rate-stat minimum sample, NetPoints per-100 vs total. Held
back deliberately: the agent still needs `get_leaderboard` for the shapes the
template does not cover (a leaderboard that also needs an opponent or
box-score join), so removing the tool is a stage-3 decision with its own
regression check, not a side effect of landing the template.

### 2.2 `player_stat` — DONE

One named player's season numbers, from `player_season_stats_deduped` so
traded-player handling is free. Reports per-game and season-total together
rather than trying to tell "how many points did X average" from "how many
points did X score" — a distinction the router got wrong more often than
right, and one that disappears entirely by answering both.

**A constrained-decoding lever worth remembering:** with `stat` optional in
`ROUTER_SCHEMA`, the model omitted it even for a question that appears
verbatim as a worked example in the prompt, and rewording the prompt did not
fix it. Making `stat` *required* fixed it immediately — a constrained decoder
only reliably considers a slot it is required to emit, and answers `""` when
there is none (pruned in `route()`). Prompt wording persuades; the schema
decides. Reach for the schema first when a slot goes missing.

**Still to retire in stage 3:** "Filtering SQL to one named player or team"
(297 tok), and the remainder of "per game"/"average".

### 2.3 double-doubles / triple-doubles — DONE, and not as planned

The plan assumed a new `threshold_count` variant recounting categories ≥ 10
from `player_box_stats`. Unnecessary: the KB entry itself records that ESPN
already precomputes `doubleDouble`/`tripleDouble` as a season COUNT of such
games, so this is a *leaderboard metric*, not a new shape. Landed as two
entries in `LEADERBOARD_METRICS` plus two aliases — no new template, and it
inherits season defaults, traded-player dedup and team filtering for free.

Worth noting for the shapes still to come: read the KB entry before building
the template it retires. It is a record of what was already learned about the
schema, and twice now it has said the work is smaller than the plan assumed.

**Still to retire in stage 3:** "Double-double / triple-double definitions"
(199 tok).

### 2.4 `game_log` and `team_record` — DONE

`game_log` uses the shared team-perspective query (`team_box_stats` for
opponent and home/away, `games` for `winner_team_id`, `team_score`/
`opponent_score` computed from `home_away` rather than reported raw).
`team_record` reads `standings` instead, which is authoritative for a full
season and carries streak and seed. The record over a *limited* set of games
is tallied in `game_log`, in Python, over exactly the rows being displayed.

Three guards this shape needed, each for a failure the KB entries describe:

- `standings` has no `season_type`, so a playoff-record question falls through
  rather than being answered with the regular-season number under a
  playoff-sounding label.
- `team_record` refuses a `limit` outright — "how did they do in their last
  10?" answered with the full-season record is a silent substitution. It is a
  `game_log` question, and the router now sends it there.
- A malformed `date` slot is dropped rather than passed through, since
  `games.date` is a full ISO timestamp and `= 'YYYY-MM-DD'` is valid SQL that
  silently matches nothing.

**Two routing bugs the live run caught that the check had not.** "Show me the
Knicks **last** 5 games" routed as `order: "first"` and answered with October
games; and "how did the Celtics do in their last 10 games?" routed to
`team_record`, which would have reported the full season. Both are fixed in
the router prompt, both now have assertions in `check_routing.py`, and the
second also has the code-level guard above. Lesson: assert every slot that
changes the answer, not just the intent — an intent-only assertion passed
while the answer was wrong.

**Still to retire in stage 3:** the two largest remaining KB entries — "A
team's game log across home AND away games" (454 tok) and "A record/tally
alongside a list of games" (485 tok) — plus "First/most recent/last game"
(140), "Filtering by an exact calendar date" (129), "A specific game already
implies its season" (54).

### 2.5 `shot_chart` — DONE

`render_shot_chart` was extracted out of `Toolbox` into `shotchart.py` taking
`con` and `out_dir` explicitly, the same split `leaderboard.py` got, so the
template and the agent tool are one implementation. Templates now take a
`TemplateContext` (connection + output directory) rather than a bare
connection, since this is the first shape that needs to write a file.

Two bugs the port surfaced, both real:

- `Toolbox.__init__` created `out_dir`, so the extracted function silently
  depended on someone else having made the directory first. It creates its own
  now.
- `shot_chart` was the only template not defaulting an unspecified season to
  the current one, so "plot Curry's threes" charted his entire career in a
  single plot (3,665 attempts). Now scoped and labeled like everywhere else.

**Still to retire in stage 3:** its **366 tokens** of tool schema, plus 2 KB
entries (177 tok).

**A measured limit of the schema lever, worth recording.** Stage 2.2 found
that requiring a slot in `ROUTER_SCHEMA` makes the decoder actually emit it.
Here, requiring `season_ref` as well made things *worse*: it fixed one dropped
season but crowded out others, and "most games with 15+ assists in 2024?"
started returning `season_ref: "current"` with no `season` at all — a named
year silently replaced by the current one, worse than the miss it was meant to
fix. Measured across five questions, optional-with-sharper-wording won 4/5
against required's 3/5. The lever is real but not free: require the one slot
that pays for itself, not every slot you wish the model would fill.

**Known gap:** "plot Curry's threes from last season" drops `season_ref` (it
gets `shot_value` right instead), so "last season" resolves to the current
season. The answer names the season, so it is visible rather than silent, but
the year can be wrong. `check_routing.py` asserts only what is reliable here
and carries a comment saying why.

## Stage 3 — retire the preamble — DONE, and the plan here was wrong

> **Superseded by a later pass.** The reasoning below was right *at the time*:
> the agent still handled comparisons, game logs and records, so the entries
> those needed had to stay. Once stages 2.1–2.5 plus `player_compare` and
> `single_game_high` landed, only genuinely novel questions reach the agent, and
> fourteen entries were removed — see "Trimming the knowledge base" below.
> Per-question assembly remains the mechanism; the list it assembles from is
> now much smaller.

**The deletion plan does not survive contact.** Every step above says which
KB entries it "retires", and that reasoning was wrong. The agent still writes
free-form SQL for every `other` question, and those questions hit exactly the
same schema traps: "compare Luka and SGA" needs the traded-player dedup rule
and the named-player filtering rule just as much as a leaderboard did.
Deleting those entries would not retire a cost, it would regress the one path
that still has to write SQL by hand.

What was actually costing something was that **every question paid for all 26
entries**. So stage 3 assembles the preamble per question instead: an
always-on core of five rules that apply to any SQL, plus up to three entries
selected by keyword overlap with the question (`prompt.select_knowledge`).
Nothing is deleted; almost nothing is loaded.

Selection is plain keyword overlap, not embeddings — it needs no model call
(the point is to spend less time, not more), it is deterministic and testable,
and a miss is cheap: a missing entry is what the agent had before that entry
existed, while a truncated prompt loses the schema itself. Entries whose
trigger vocabulary differs from their own prose carry explicit `keywords`
("how FAR was his average three?" never says "distance").

**Measured end state:**

| component | before | after |
|---|---|---|
| `TABLE_SUMMARY` | 1,328 | 1,328 (kept — the fall-through path needs it) |
| KNOWLEDGE_BASE | 6,585 | 870 always-on + ≤1,500 selected |
| standing rules + tool prose | ~890 | ~890 |
| `TOOLS` | 1,498 | 1,246 |
| **total** | **10,295, truncated to 4,098** | **4,337–5,821, never truncated** |

`get_leaderboard`'s tool schema went 956 → 686 tokens by replacing the
80-name metric enum with a description: `run_leaderboard` already answers a
wrong name with a close-match suggestion and the full list, so the model
recovers in one turn and pays for the list only when it needs it. The tool
itself is kept — the fall-through path is where a leaderboard that also needs
an opponent or box-score join ends up.

What stays is the genuinely irreducible material: NetPoints semantics, the
per-quarter LAG() derivation, shot-distance math, what's computed vs. what
doesn't exist. That is a real knowledge base. The rest was teaching a model
to write SQL we already know how to write.

**The budget guard.** `build_system_prompt` raises `PreambleTooLarge` rather
than handing ollama a prompt it will quietly cut in half. The original bug was
silent for four commits; it cannot be silent again. A test asserts every
assembled prompt fits, so adding a KB entry or a tool description that
overflows now fails in CI rather than in a wrong answer six weeks later.

**`NUM_CTX` is 16384, up from 8192.** The truncation rule was measured, not
guessed: a prompt *under* `num_ctx` is evaluated in full (a ~3,700-token
prompt at 8192 came back with `prompt_eval_count` 3,696), and one *over* it is
cut to roughly half (10,093 at 8192 came back 4,098). A ~5,400-token preamble
costs ~100s of CPU prefill on a fall-through question — against being quietly
wrong at 8192. For a path this rare that is the right trade.

**One accepted cost:** a per-question system prompt changes the cached prefix
between questions, so the agent path no longer reuses the KV cache
*across* questions. It still reuses it across the tool-call rounds *within* a
question, which is where the cost compounded (five rounds at ~75s each). Rare
path; right way round.

## `single_game_high` — a shape whose absence was a wrong answer

Reported from real use: "who had the most assists in a single game and how
many did he have" was answered **"Nikola Jokic led the league in assists per
game in the 2026 regular season, at 10.7"** — in 1.76s. The real answer was
Ryan Nembhard with 23, on 2026-04-13.

Nothing was broken. There was simply no intent for a single-game *maximum*, so
the router picked the nearest shape it had (`leaderboard`) and the template
answered that question correctly and confidently. **A missing shape does not
produce a refusal — it produces a fast, fluent answer to a different
question**, which is worse than the slow wrong answers this migration started
from, because nothing about it looks wrong.

Two things follow, and both are now in place:

- `single_game_high` reads `player_game_log`, so it reports the value *and*
  which game it was ("23, on 2026-04-13 vs CHI"). It handles ties, a named
  player ("Jokic's highest rebound total"), and defaults to the current
  regular season like every other template.
- The router's `leaderboard` line now says explicitly that it is for *season*
  stats and that single-game questions belong elsewhere. Describing the
  neighbouring shape is part of adding a shape.

**Also observed:** adding an intent perturbed slot extraction on unrelated
questions — "plot Curry's threes from last season" had been keeping its season
and started dropping it again. That is a real property of routing everything
through one small model, and the reason `check_routing.py` exists and is run
after every change rather than trusted from last time.

## Model choice, measured

Routing is classification under a JSON schema, and it is not a 7B-sized job.
Over the 30 cases in `check_routing.py`, every model from 1.5B to 8B scored
28–30/30 — constrained decoding does the structural work. `qwen2.5:3b` matches
`qwen2.5:7b` at 1.8× the speed and 2.8GB less RAM and is now the router
default; the agent keeps the 7B for hand-written SQL.
`scripts/bench_router_models.py` reproduces the table.

**Thinking models are wrong for this**, on latency rather than accuracy:
`qwen3:4b` spent ~20s per question reasoning before emitting the same tiny JSON
object. `--think` applies to the agent only.

**Changing the router model means re-validating the prompt.** The prompt had
been tuned against the 7B; on the 3B, "how many points did Jokic score in the
3rd quarter against Boston?" started routing to `game_log` instead of `other`,
which would have answered with a list of games. A worked negative example fixed
it. Treat the prompt and the model as one unit — swapping either invalidates
the check.

**A negative result worth keeping.** Removing `season`/`season_ref` from the
schema and extracting the season from question text in code did *not* improve
the remaining slots — both variants scored 30/30 on them. The idea that schema
properties compete for the model's attention is unsupported by this experiment.
The eval is at ceiling, so it cannot rule out a small effect, but the honest
summary is: trim the schema because a slot is better done in code, not because
trimming buys accuracy elsewhere.

**Which is exactly why `query/season_text.py` landed the way it did.** Reading
the season out of the question text is worth doing on its own merits — it was
the slot the router most reliably dropped, and both `known_gap` cases were the
same failure, answering for the current season when the question said "last
season". But since trimming the schema buys nothing, the model's `season` /
`season_ref` slots are *kept as a fallback*: code wins when it finds an answer,
the model's slot applies when it doesn't, so phrasings the parser has never
seen ("in his rookie year") route exactly as well as before. Both `known_gap`
markers are gone and the check is 30/30 with no gaps.

## Trimming the knowledge base

With every common shape ported, the fall-through path handles only questions no
template covers, and fourteen entries had nothing left to do. Removed: game
logs across home and away, records alongside a game list, first/last game,
single-game-vs-season totals, per-game averages, traded-player dedup,
double-doubles, shot-chart guidance and `made_only`, rate-stat minimum samples,
NetPoints rate-vs-total, fingerprint categories, "top N by NetPoints alongside
box-score stats", and "a specific game implies its season". Each is a rule in
code now, tested, where it cannot be truncated away or half-remembered.

**The criterion, since it is the reusable part:** a *shape* a template owns
goes; a *schema fact* that makes arbitrary SQL silently wrong or silently empty
stays. So `fieldGoalsMade` already including threes stays (wrong math, not an
error), the ISO-timestamp date trap stays (zero rows, no error), NetPoints'
string `season_type` stays (matches nothing, no error), and per-quarter scoring
and shot distance stay because no template derives them. Two tests pin both
halves of that criterion so the next trim has something to argue against.

6,350 tokens across 26 entries became 2,776 across 12; an assembled preamble is
now ~5,000–5,500 tokens. This is a side project and every entry is one `git
revert` away, which is what made an aggressive trim the right call rather than
a risky one.

## `head_to_head`, and the limit of prompt-based correctness

"How many times did the 76ers play boston?" answered "they did not play against
the Boston Celtics", twice. They played four times.

Four defects behind one wrong answer: no template for games between two teams
(so it fell through); `home_team_id = 'PHI'` against an all-digit id column
(silently zero rows); `A OR B AND season = ...` binding the season to one side
of the matchup; and a zero count reported as a fact about the world.

**The second one is the point.** That rule is *always-on*, and the assembled
prompt for that exact question contained it verbatim — including
`WHERE home_team_id = 'NY'` written out as a worked WRONG example. The model
had it in front of it and wrote the wrong form anyway. No amount of prompt
work fixes that; it is the argument for templates, restated by the system
itself after every other argument had been made.

So: a `head_to_head` template that resolves names to ids in code, plus a
`run_sql` guard that flags any `*_id` compared to a non-numeric literal. The
guard is unconditional rather than empty-result-only, because the failing query
was a `COUNT(*)` — one row containing zero, not zero rows.

## `shot_distance`, and a substitution inside a template

"What was steph curry's avg 3pt shot distance" came back as "26.6 points, 3.6
rebounds and 4.7 assists per game".

**The first bug was in a template, not the agent.** `player_stat` treated an
unrecognized stat the same as no stat at all and fell back to its default
stat line — the exact silent substitution this design exists to prevent,
committed by the code meant to prevent it. `player_compare` had it too. Both
now separate "no stat named" from "stat named but unsupported"; only the first
gets a default.

The lesson generalises past this fix: **a default is only safe where the user
named nothing.** Any template with a fallback should be read with that
distinction in mind.

Forced to the agent, the question failed again — the correct distance formula,
with both the 3-point filter and the season filter dropped, reporting an
all-shots all-seasons 16.94 as a current-season three-point figure (real answer
23.6). A fixed formula over known columns is template work, so it became one.

The router also gained a very short list of subjects forced to the agent
regardless of classification, for questions that read like a supported shape.
Shot distance was its first entry and left it the same day by earning a
template — that is the lifecycle, not a workaround to accumulate in.

## `player_history`, and a whole dimension nothing covered

"What was klay thompson's 3pt percentage over the past 4 seasons (with
attempts/makes)" came back as the *league's* true-shooting leaders for 2020,
with assists and rebounds columns.

This one was structural rather than a slip. **Every template answered about a
single season.** A multi-season question had nowhere to go, so the router put
it in the nearest shape it had, and `leaderboard` cheerfully dropped the named
player. Worth noting as a distinct failure mode from the others in this
document: the earlier ones were missing *shapes*, this was a missing
*dimension* cutting across the shapes that existed.

`player_history` reports one player's stat by season, most recent first, four
seasons by default. Percentages come with makes and attempts, because a
percentage without volume is the thing people immediately ask "out of how
many?" about.

`leaderboard` also refuses outright when a `player` slot is set — it ranks the
league or a team, never one named person. That guard is independent of the
routing fix and would have made this a slow answer rather than a confident
wrong one, which is the trade this design keeps choosing.

## `player_netpoints`: data that existed only as a ranking

"What were SGA's netpoint stats this season" answered "Nikola Jokic leads the
team in NetPoints", after 149s.

The chain is worth reading, because the guards worked and it still failed. The
router chose `player_stat` with stat `netpoints` — reasonable. `player_stat`
refused the unsupported stat rather than substituting its default line, which
is exactly the fix from the previous report. Then the agent, having fallen
through, called `get_leaderboard` for the league and dropped the player.

**A guard that turns a wrong answer into a slow one is only worth having if
something downstream can answer.** NetPoints existed solely as leaderboard
metrics — ways to rank the league — so there was nothing to fall through *to*.

`player_netpoints` reports the season line plus the 21 play-type categories
behind espnanalytics.com's "Net Pts Fingerprint", per 100 possessions by
default (season totals mostly rank by playing time, and the fingerprint exists
to compare players). It handles both of that data's traps: `net_points_player`
uses its own string `season_type`, and the fingerprint table has no
`season_type` column at all.

## What is left

- ~~**`player_compare`**~~ — DONE. The agent got this wrong for a reason no
  KNOWLEDGE_BASE entry could fix: it wrote correct SQL (`current_season()`,
  ILIKE matching) but expanded "SGA" to `'%Scottie G. Allen%'` and compared
  Luka Doncic to Luka Garza. Nickname resolution is a lookup, not something to
  hope a 7B model knows — `entities.PLAYER_NICKNAMES` is a curated, auditable
  table of 21 shorthands, every one verified to match exactly one row in
  `players`, matched against the *whole* query so "book" resolves to Devin
  Booker while "notebook" does not. "Luka" and "Curry" are deliberately absent:
  they are ordinary first names and surnames shared with real players, and the
  clarifying question is the honest answer. The output is a fixed-width table,
  not prose — comparisons are the one shape where a sentence actively hurts,
  and the agent's prose version had claimed a player with 0.4 steals led one
  with 1.6. 122s and wrong → ~2s and correct.

  **The bug worth remembering from this one:** `player_compare` was added to
  the router prompt and given a `players` slot, but not to the intent enum in
  `ROUTER_SCHEMA` — so constrained decoding could never emit it, and every
  comparison silently routed to `player_stat`. Constrained decoding is exactly
  as literal as it sounds: an intent absent from the enum does not exist, no
  matter how well the prompt describes it. There is now a test asserting every
  intent the prompt describes, and every ported template, appears in the enum.
- ~~**`fields` on leaderboards**~~ — DONE. A `fields` slot feeds
  `run_leaderboard`'s existing extra-columns support, and the answer switches
  from a sentence to a table once extra columns are asked for (a sentence
  carrying three numbers per player across ten players is unreadable). The
  qualifying minimum is printed in the header, so "why isn't X on this list?"
  has a visible answer. An *unknown* field falls through rather than being
  dropped — silently ignoring it would answer a narrower question than was
  asked, which is the failure this whole architecture exists to prevent.

  **The bug worth remembering:** the array slots had no `maxItems`. Under
  constrained decoding an unbounded array lets the grammar permit "one more
  item" forever, and the model takes that offer — it emitted
  `["points","minutes","minutes"]` on one question and then hung for **over
  five minutes** on the next, because at ~10 tok/s on CPU a looping array is a
  stall, not a typo. With `maxItems` those questions route in ~2.2s. Bound
  every array slot in a constrained schema.

  The model still over-fills to the cap, so the template deduplicates and
  drops any field that restates the ranked metric (asked for "top scorers with
  their rebounds", the router also returned "points", which rendered the same
  33.5 twice under two headings). `check_routing.py` now treats a list
  expectation as a *subset* check for the same reason: dropping a field the
  user asked for is a bug, an extra one is only noise.
- ~~**A second look at `MAX_ROWS`**~~ — DONE, and it was worse than suspected.
  A row cap does not bound what comes *back*: measured, `SELECT * FROM
  player_game_log LIMIT 200` serializes to **~44,000 tokens** — nearly three
  times the whole 16,384-token window, from a single tool call. Over `num_ctx`
  ollama cuts the prompt to about half, head-first and silently, throwing away
  the system prompt: the exact failure this codebase was rebuilt to eliminate,
  reachable by the `SELECT *` a small model writes constantly.

  Results are now bounded by **tokens**, not rows — as many rows as fit
  `MAX_RESULT_TOKENS` (2,000), chosen by binary search, with the dropped count
  and a pointer to aggregate in SQL or select fewer columns. 44,084 → 1,828
  tokens. When even one row is too large the column list comes back instead,
  which is what the model needs to write a narrower query. `get_leaderboard`'s
  model-supplied `limit` is clamped for the same reason.

## Sequencing and verification

Each shape ships as its own commit: template + tests + router examples + the
KB deletions it earns + a `CHANGES.md` note.

Verification per shape, since unit tests can't catch a routing regression:

- `scripts/check_routing.py` — a fixed question set run through `route()`
  only (cheap — all cache hits, ~1.5s each), asserting intent and slots, and
  including cases that must NOT be answered by a near-miss template. Grow it
  with every ported shape; it is the regression suite for the part that has no
  types. Currently 30/30 with no `known_gap` cases outstanding. The marker
  remains available (reported as GAP, not counted as a failure) for a future
  weakness that is visible rather than silent. It previously covered — reported as GAP
  and not counted as a failure, so a real regression still stands out. That
  one is "best true shooting percentage **last season**", where the router
  drops `season_ref` and the answer covers the current season instead; every
  template names the season it used, so it is visible rather than silent, and
  requiring `season_ref` in the schema was measured and made other slots
  worse.

  Two rules learned the hard way while filling it in. **Assert every slot that
  changes the answer, not just the intent** — an intent-only assertion passed
  while "the Knicks' *last* 5 games" was answering with October games. And
  **do not assert slots that merely restate a default** — an absent season
  already means the current one in every template, so requiring the router to
  say so makes the check brittle without making any answer more correct.
- `--no-fast-path` runs the same question through the old agent for
  side-by-side comparison while both paths exist.

## Risks

- **Router prompt growth.** Each intent costs ~40 tokens (one line + one
  example) against the ~400 a KB entry costs on every call. It is bounded by
  the number of query *shapes*, which saturates; schema gotchas do not. Still,
  keep the examples terse and re-measure `len(ROUTER_PROMPT)//4` per commit.
- **Silent mis-routing.** The dangerous failure is a question routed to a
  template that answers a *different* question confidently — exactly the
  original bug. Mitigation: templates validate slots and raise
  `TemplateUnsupported` rather than filling in defaults, and every answer
  names its season and scope outright so a substitution is visible.
- **KV cache thrashing.** ollama keeps one cache slot per model by default, so
  a second call with a different system prompt evicts the router's prefix
  (measured: 1.3s → 11.2s). Templates that phrase their own answer avoid this.
  If a shape genuinely needs model narration, set `OLLAMA_NUM_PARALLEL=2`
  first and re-measure.
- **REPL follow-ups.** The router sees only the current question plus one line
  of prior context. Multi-turn chains deeper than that will mis-route; they
  fall through to the agent, which is slow but correct.
