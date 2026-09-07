# Query fast-path migration

Staged replacement of the single "understand the question AND write the SQL"
model call with a router → template → answer pipeline. Stage 1 has landed;
this is the plan for stages 2 and 3.

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

### 2.0 Shared entity resolution (prerequisite)

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

**Known gap this closes:** the router already extracts a `team` slot
inconsistently — "What was the Lakers record last season?" routed with
`{'season': 2025}` and no team. Slot extraction for named entities needs a
worked example per shape in `ROUTER_PROMPT`, and the template must treat a
missing entity as fall-through, never as "all teams".

### 2.1 `leaderboard` — highest value

Wraps the existing `toolbox.get_leaderboard`, which is already stage-2-shaped:
it owns the season default, min-sample floors, and traded-player dedup.

- map the router's `stat` slot onto `LEADERBOARD_METRICS` via the fuzzy
  matcher already in `get_leaderboard` (`get_close_matches`)
- on no match, `TemplateUnsupported` → fall through
- deterministic phrasing: rank, name, value, and the season named outright

**Retires:** `get_leaderboard`'s **939 tokens** of tool schema from the
always-on preamble — the single largest fixed cost in `TOOLS`, most of it the
80-name metric enum. Plus 4 KB entries (~1,574 tok): traded-player dedup,
"per game"/"average", rate-stat minimum sample, NetPoints per-100 vs total.

### 2.2 `player_stat`

One named player's season numbers. Needs 2.0. Reads
`player_season_stats_deduped`, so traded-player handling is free.

**Retires:** "Filtering SQL to one named player or team" (297 tok), and the
remainder of "per game"/"average".

### 2.3 `threshold_count` extensions — `double_double` / `triple_double`

Currently routed to `other` on purpose. Same table and shape as
`threshold_count`, different predicate (count categories ≥ 10).

**Retires:** "Double-double / triple-double definitions" (199 tok).

### 2.4 `game_log` and `team_record`

These two share the hard part: a team's games span `home_team_id` and
`away_team_id`, so both need the same opponent/result CTE. Build it once,
use it for the list (`game_log`) and the tally (`team_record`).

**Retires:** the two largest remaining KB entries — "A team's game log across
home AND away games" (454 tok) and "A record/tally alongside a list of games"
(485 tok) — plus "First/most recent/last game" (140), "Filtering by an exact
calendar date" (129), "A specific game already implies its season" (54).

### 2.5 `shot_chart`

Thin wrapper over the existing `toolbox.render_shot_chart`.

**Retires:** its **366 tokens** of tool schema, plus 2 KB entries (177 tok):
"Shot charts (visual) vs. shot-related numbers" and "render_shot_chart's
made_only parameter".

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

- a fixed question set run through `route()` only (cheap — all cache hits,
  ~1.5s each), asserting intent and slots. Grow it with every ported shape;
  it is the regression suite for the part that has no types.
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
