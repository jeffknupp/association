# Query fast-path migration

Staged replacement of the single "understand the question AND write the SQL"
model call with a router → template → answer pipeline.

**Landed:** stage 1 (router + `threshold_count`), 2.0 (`entities.py`), 2.1
(`leaderboard`), 2.2 (`player_stat`), 2.3 (double/triple-doubles), 2.4
(`game_log` + `team_record`), 2.5 (`shot_chart`). **Stage 2 is complete.**
**Next:** stage 3.

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
each keeping its existing behaviour — a chart of the wrong Curry is obvious on
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
  single plot (3,665 attempts). Now scoped and labelled like everywhere else.

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

## Stage 3 — retire the preamble

Delete each KB entry as its template lands (each step above names its own).
The preamble is the progress bar, and it is also the escape hatch's latency,
so this is not bookkeeping — it is the second half of the speedup.

**Projected end state** (from today's measured components):

| component | now | after |
|---|---|---|
| `TABLE_SUMMARY` | 1,328 | 1,328 (keep — the escape hatch needs it) |
| KNOWLEDGE_BASE | 6,585 | ~1,775 |
| standing rules + tool prose | ~880 | ~400 |
| `TOOLS` | 1,498 | ~193 (`run_sql` + `describe_table` only) |
| **total** | **10,295** | **~3,700** |

What stays is the genuinely irreducible material: NetPoints semantics, the
per-quarter LAG() derivation, shot-distance math, what's computed vs. what
doesn't exist. That is a real knowledge base. The rest was teaching a model
to write SQL we already know how to write.

Two guards to add in this stage:

1. **A startup assertion** that fails loudly if the rendered preamble exceeds
   `NUM_CTX`. This bug was silent for four commits (it crossed the limit at
   `c209516`, "Add get_leaderboard tool"). It must never be silent again.
2. **Retrieval instead of always-on** for what remains: select 2-3 KB entries
   by keyword match on the question, so the escape hatch pays ~2,000 tokens
   rather than ~3,700, and adding an entry stops taxing every future query.

## Sequencing and verification

Each shape ships as its own commit: template + tests + router examples + the
KB deletions it earns + a `CHANGES.md` note.

Verification per shape, since unit tests can't catch a routing regression:

- `scripts/check_routing.py` — a fixed question set run through `route()`
  only (cheap — all cache hits, ~1.5s each), asserting intent and slots, and
  including cases that must NOT be answered by a near-miss template. Grow it
  with every ported shape; it is the regression suite for the part that has no
  types. Currently 23/23.

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
