# The target types

**Status: DRAFT 2, partly decided (Jeff, 2026-10-01); see "Decided" and
"Still open". Nothing here is code yet.**
`ROADMAP.md`, Phase 0: "the target types written down and reviewed: the
Reading's typed filters, the Result, the measure type, and the closed set
of shapes. Shapes are named here, before any sayer is written, so 'shape'
does not become intent renamed." This is that document. Each type is
declared in code by the phase that first uses it, never ahead of it (an
unused type is dead code, and a gate here).

Draft 1 was reviewed by an independent Opus agent against the code and
the corpus before this was written, and by a second agent that inventoried
every remark the code emits. Draft 1 was lossy in seven ways; "What the
review changed" lists them. Both reports are kept:
`~/association-research/stages/types-review-011091f.md` and
`notes-inventory-011091f.md`.

Evidence is the stage snapshot of the 628 recorded questions on `7f6425b`
(`~/association-research/stages/corpus-7f6425b.jsonl`) unless a line says
otherwise.

## Decided (Jeff, 2026-10-01)

- **Ranking, comparison and split are three shapes** over one `Grouped`
  body and one table renderer. Which one a part is follows from a rule on
  the subject's kind and `by`, held by a test with no exceptions ("The
  shapes").
- **A decision and a note are two types.** A decision is something the
  question left open and the system chose; a note is something about the
  data. What that split still leaves to decide is under "Still open" 1.
- **`pair` is not a shape, on one condition: any number of subjects and
  any number of players beside them, each with its own role, stay
  expressible.** They do; "More than one player" shows how.

## What must be decided before which phase

| Phase | Needs from this document |
| --- | --- |
| Phase 0, the last item (a kind on every remark where it is written) | "Still open" 1: the decision and note split |
| Phase 1 (the read stage becomes a stage) | nothing. It keeps today's `Reading` and `Scope` and is proven by identical snapshots |
| Phase 2, before its first slice | "Still open" 2 to 5: parts, `record_when`, `period`, the default line |
| Phase 2, slice (iv), team shapes | "Still open" 6: `team_snapshots` |
| Phase 2, slice (v), charts | "Still open" 7: two charts, not four |
| Phase 3 (one reader) | the Filter union, `Measure`, `Window`, `Unsupported`: reviewed again with the shadow reader's diffs in hand |

## What exists, in one table

| Record | Today | Size | The problem |
| --- | --- | --- | --- |
| what was read | `Reading` holding a `Scope`, an `intent`, a `Subject` and a second `Reading` (the point) | 39 slots, 18 point fields | two vocabularies for one question, translated both ways |
| what will run | `Query` or `TeamQuery`, built twice | 14 and 5 fields | planned inside the parser, then again |
| what came back | `TemplateResult.data`, a dict | 57 distinct key sets over the corpus | no common result; each read and worded by its own code |
| what is said | `Answer.text`, with `data["notes"]` on 98 of 628 | sentences | a caveat is a string; nothing can check it survived |

## The four records

Sketches, in Python's notation. Every type is frozen and closed (a
`Literal` or a union of dataclasses); none holds the question's text, a
connection or a slot dict.

### Reading - what READ produces

```python
class Reading:
    subject: Subject
    measures: tuple[Measure, ...]  # () where the question names none (a whole line, a record)
    shape: Shape
    by: tuple[Dimension, ...]  # what one row is; () for scalar and rows
    filters: tuple[Filter, ...]  # which games count
    period: Period | None  # what a read SEES of each game: a quarter or half
    window: Window | None  # which rows are kept, and ordered by what
    minimum: Minimum | None  # a qualifier the question states ("at least 400 attempts")
    span: Span  # seasons and season type, each stated or defaulted
    also: tuple[Column, ...]  # columns shown beside the measure: a Measure, or an attribute ("team")
    unread: tuple[str, ...]  # content words nothing claimed
    decisions: tuple[Decision, ...]


class Subject:
    kind: Literal["player", "players", "team", "teams", "everyone"]
    names: tuple[str, ...]  # as the question typed them
    position: str | None  # "everyone" only: guards, centers
    of_team: str | None  # "everyone" only: a team's players ("most rebounds by a Hawk")
```

No `intent`: the shape, `by` and the subject's kind say what it named. No
`point`: the Reading IS the point.

`Window(order: recent | first | top | bottom, count, by: Measure | None)`
replaces `order`, `limit`, `rank`, `ranked_by` and the point's `order`,
`direction`, `limit` (`offset` is 0 in all 628 and goes). `by` is what
`ranked_by` carries today: "the highest scoring triple-doubles" keeps
games by one line and orders them by another measure.

`Span(seasons: one | range | career | since, season_type: regular |
playoffs | both, stated: frozenset)` replaces `season`, `season_type`,
`season_type_unstated`, `span`, `since`, `until`. What was not stated is
defaulted by the planner, and a default is a note.

### Filter - one closed union in place of 39 slots

| Filter | Says | Today's carriers |
| --- | --- | --- |
| `Opponent(team \| alignment)` | against a team, or against a conference or division | `opponent`; `situation` for "vs the West" |
| `Tenure(team)` | while he played for a team | `own_team` |
| `Venue(home \| away)` | where | `venue` |
| `Role(starter \| bench)` | how he entered the game | `split` as a filter |
| `OnDate(day)` | one day | `date` |
| `DateRange(from, to, each_season)` | a cut in dates: "since 1/26/20" across seasons, "since January 31st" within each | `situation`, and a second time as `since` |
| `Calendar(kind, value)` | a month, a weekday, a holiday | `situation`, as worded |
| `GameOfSeries(n)`, `SeasonOfCareer(n)`, `Round(name)` | a position in a series, a career, a postseason | `game_n`, `season_n`, `round` |
| `Companion(player, side, predicate)` | a named player played, sat, started or came off the bench, on his team or the other | `with_player`, `without`, `conditions` |
| `Line(measure, op, value, who, period)` | a stat reached, missed or equaled a number - the subject's or a companion's, in the game or in a quarter | `stat`+`threshold`, `above`, `below`, the point's `predicates`, `ConditionSpec(reached)`, `PeriodCondition` |
| `Won(bool)` | his team won | a streak's `kind`, the `won` predicate |
| `ShotValue(1 \| 2 \| 3)` | shots relation only | `shot_value` |
| `Met(opposite \| same \| either)` | the games every named subject played in | the `pair` shape |
| `Unsupported(what, as_typed)` | something the reader recognized and nothing reads: an age, a rate per 90 minutes | refusal text, or nothing |

`Line` is the one that matters: a line on a stat has five carriers today,
each read by different code and honored by different relations. One type
means one cell per relation. The migration has a trap: in the nine
multi-line questions `stat`/`threshold` contradict `above` ("20+ point 5+
assist" reads `stat: assists, threshold: 20`), so a `Line` is built from
the words, never converted from the slot pair.

`Unsupported` exists so a refusal keeps its cause. "netpoints defense per
90 minutes" is refused by name today; with a closed measure type the
reader could not write "per 90" down, and the question would become a
silently narrower one.

A `Dimension` is closed and may carry a parameter: `player`, `team`,
`subject` (the names in the question), `season`, `season_type`, `month`,
`venue`, `opponent`, `role`, `won`, `period`, `category` (NetPoints),
`presence(of: names)`, `line(Line)`.

### More than one player

Nothing in the Reading is a pair. A subject holds any number of names, and
any number of `Companion` filters stand beside it, each with its own side
and role:

| Question | Subject | Filters | Shape |
| --- | --- | --- | --- |
| "steph curry record vs lebron regular season without kd" | Curry | `Companion(LeBron, opponent, played)`, `Companion(Durant, own, absent)` | scalar (a record) |
| "curry record vs lebron and kawhi without kd or klay at home" | Curry | four `Companion`s, `Venue(home)` | scalar |
| "curry vs lebron" | Curry, LeBron | `Met(opposite)` | comparison, with the meetings as a detail part |
| "compare curry and lebron" | Curry, LeBron | none | comparison |

`Met(side)` is one more filter: the games every named subject played in,
on opposite sides for "vs". So a matchup is a comparison with one filter,
and the difference between "compare" and "vs" is that filter and the
relation it forces (games, not the season line).

What the code honors today is narrower than the type, and the planner's
cell table says so rather than the type: `Met` is built for exactly two
subjects (`player_games.paired_rows_sql`), and the reader turns a third
name into a `Companion` (`subject._versus_companions`). `Companion`s are
already any number, on either side (step 3 of the last roadmap).

### Measure - a closed type, one catalog

```python
class Measure:
    key: MeasureKey  # points ... ts_pct, usage_pct, netpoints, point_differential, shot_distance, record
    how: Literal["per_game", "total", "count", "rate", "per_100", "percentile", "none"]
    whose: Literal["own", "opponent"]  # "points allowed" is the opponent's points
    category: str | None  # NetPoints only: one of the 21 play-type categories
    side: Literal["offense", "defense", "total"] | None  # NetPoints only
```

`MeasureKey` is the key set of one catalog, grown from `metrics.py`. Per
key the catalog says: how it is read on each relation (a column, made
over attempted for a rate, a flag counted, the season line's column);
whether it has a reading in a quarter (`PERIOD_BLANKED` today); the
qualifier a ranking needs; its label. A key, a `how` or a period with no
reading on a relation is the planner's refusal, with that as its cause.

A value is a number, a ratio with its parts (45 of 126, 35.7%), or a
record (W, L). `record` is a key whose value is that pair.

Today one measure has up to four keys (`threePointFieldGoalPct`,
`three_pct`, `three_pt_pct`, `three_point_pct`) across six vocabularies:
the normalizer's 38 keys, `LEADERBOARD_METRICS` (99), `TEAM_METRICS` (29),
the compiler's measures, `parse.py`'s own word table, and raw phrases.
The corpus uses 37 distinct `stat` values, 8 of them in no catalog.

Where the question names no measure, the line shown is a declared default
per relation and shape (a log: MIN PTS REB AST; a split: ten columns and
the team's W-L; a comparison: eight averages and NetPoints). That table is
the one place the old intents' habits survive, so it is declared, small
and reviewed.

### Query - what PLAN produces

```python
class Query:
    relation: Relation
    subject: Entities  # resolved: ids and display names, or everyone
    measures: tuple[Measure, ...]
    shape: Shape
    by: tuple[Dimension, ...]
    cells: tuple[Cell, ...]  # each Filter resolved and found in the relation's cell table
    period: Period | None
    window: Window | None
    minimum: Minimum | None  # stated, or the catalog's default for a ranking
    span: ResolvedSpan  # the seasons and season types that will be read
    decisions: tuple[Decision, ...]  # what the question left open and the planner chose
```

or `Clarify` or `Refusal` (below). The planner is the only code that
resolves a name (today's `available` narrowing is derived from the
relation), picks the relation and checks a filter against that relation's
cell table. It runs once, outside the reader.

`Relation = player_games | team_games | player_seasons | team_seasons`,
and the declared ones: `shots`, `netpoints`, `team_snapshots` (ESPN's
power index). The season line is a relation of its own: 30% of all
answers read it and the compiler has no model of it. A query names one
relation; an answer that reads two (a comparison's season line and its
NetPoints) is two parts from two queries.

### Outcome - what RUN produces

```python
Outcome = Result | Clarify | Refusal


class Result:
    subject: Entities
    span: ResolvedSpan
    cells: tuple[Cell, ...]  # the narrowings actually applied
    games: int | None  # games counted
    parts: tuple[Part, ...]  # the first is the headline
    decisions: tuple[Decision, ...]  # the planner's, carried through
    notes: tuple[Note, ...]  # what the read found about the data


class Part:
    role: Literal["answer", "summary", "detail"]
    body: Scalar | Rows | Grouped | Runs | Chart
    cells: tuple[Cell, ...]  # where a part is narrower than the whole (a venue record beside the season's)
    notes: tuple[Note, ...]  # a note about this part only


class Clarify:  # a question back: 20 of 628
    asked: str  # the name as typed
    candidates: tuple[str, ...]  # or near spellings


class Refusal:  # about 50 of 628
    cause: Cause  # closed
    facts: Mapping[str, Value]  # the floor and the first season on record; "last appears 2010"
```

The sayer takes an `Outcome` and nothing else. Contract 5 is checkable
because of it: every cell, every note and every decision that must be said
has to appear in the answer.

## The shapes

A shape is the form of one part's body. It is not what the question is
about; the relation, the measures and `by` say that.

| Shape | Body | One row is |
| --- | --- | --- |
| `scalar` | `values: (Measure, value)...` | the whole narrowed set, reduced |
| `rows` | `columns`, `rows`, `total_before_window` | one game of one entity (the entity is a column when the subject is everyone) |
| `ranking`, `comparison`, `split` | one `Grouped` body: `by`, `rows: (key, games, values, span, rank)...`, `qualifier`, `total_groups`, optional `total` row | one value of `by` |
| `runs` | `runs: (owner, length, first, last, still_open)...` | one run of consecutive games a `Line` or `Won` holds along |
| `chart` | the artifact, and the counts it drew (made, attempted) | `shot_chart` and `fingerprint` only |

A row may carry a flag of its own (`rebuilt`: this game's line comes from
play-by-play). A grouped value may be keyed by period as well as measure
(a ranking with a column per quarter).

The three grouped shapes share a body and a table renderer and differ in
more than a sentence:

| | ranking | comparison | split |
| --- | --- | --- | --- |
| `by` | an entity over the league | the named subjects | a dimension of the games |
| orientation | a row per entity | subjects as columns | a row per value |
| headline | names the leader | names nobody | names nobody |
| order | by the measure, ties share a rank | the question's order | the dimension's own (months, home then away) |
| qualifier, truncation | yes ("minimum 550 attempts", top 10 of 451) | no | no |
| default line | one measure, plus `also` | the whole line, plus NetPoints | the whole line, plus the team's W-L |

The rule, with no exceptions and held by a test: `by` is an entity kind
and the subject is everyone, a position group or a team's players -
`ranking`; `by` is `subject` and two or more are named - `comparison`;
`by` is anything else - `split`. Checked against the corpus's edge cases:
a league-wide count of games over a line is a ranking; a ranking with a
column per quarter is a ranking whose values are keyed by period;
`record_when` (by `line`), `with_without` (by `presence`), a team's
regular season beside its playoffs (by `season_type`) and NetPoints by
`category` are splits. A sayer may branch on the shape and on nothing
else.

### Where today's 25 intents land

Counts are corpus answers by today's intent. About 52 of the 610 are
refusals or clarifications and have no body.

| Intent | Relation | Parts | Answers |
| --- | --- | --- | --- |
| `player_stat`, unnarrowed | player_seasons | scalar | 50 |
| `player_stat`, narrowed | player_games | scalar, with recent games as detail rows (7); a log (2) | 28 |
| `team_stat` | team_seasons | scalar | 3 |
| `team_record` | team_games, team_seasons | five forms: a standings line (scalar, with home and road records as narrower parts) 7; a venue record beside the season's 3; a month all-time with its games 2; regular season and playoffs (a split by `season_type`) 2; by month (a split by `season`, `month`) 2 | 16 |
| `team_outlook` | team_snapshots | scalar, and chances as a split by round | 4 |
| `head_to_head` | team_games + `Opponent` | scalar (wins per team) | 10 |
| `team_quarter_points` | team_games + `period` | scalar with the games as detail; rows by the measure for an extreme | 9 |
| `game_log` | player_games 48, team_games 17, everyone by position 4 | rows by date, with a summary | 71 |
| `single_game_high` | player_games; everyone in 19 | rows by the measure | 29 |
| `period_split` | player_games + `period` | scalar, with the games as detail | 19 |
| `threshold_count` | player_games + `Line` | scalar (a count) 26; ranking by `player` 11; rows 1 | 38 |
| `record_when` | team_games + `Line` | split by `line`, with a total row 22; scalar (a count of wins) 2 | 24 |
| `player_history` | player_seasons | split by `season` | 38 |
| `player_splits` | player_games | four split parts (venue, month, role, won) in 16 of 18 | 19 |
| `with_without` | team_games + `Companion` | split by `presence` | 13 |
| `leaderboard` | player_seasons 72; player_games 5 | ranking by `player`; rows for a `Window.by` ordering of games 2 | 87 |
| `period_leaderboard` | player_games + `period` | ranking by `player`; a column per quarter in 2 | 10 |
| `team_leaderboard` | team_seasons | ranking by `team` | 11 |
| `player_compare` | player_seasons and netpoints | two comparison parts | 28 |
| `player_matchup` | player_games + `Met` | comparison, with the meetings as detail rows of (game, player) | 11 |
| `streak` | team_games in all 5 | runs; an owner per run league-wide (2) | 5 |
| `player_netpoints` | netpoints | scalar, and a split by `category` | 6 |
| `shot_distance` | shots | scalar | 16 |
| `shot_chart`, `fingerprint` | shots, netpoints | chart | 62 |
| `coach` | none | refusal | 3 |

The other 18 of the 628 were refused with no answer; 16 of them had been
read to an intent and were refused for a narrowing nothing honors.

Twenty-two intents become seven relations and six table shapes (scalar,
rows, ranking, comparison, split, runs); two are charts, and `coach` is a
refusal with a cause. The trace's label is the pair: "ranking by player on
player_seasons".

## Notes - the kinds

Inventoried at `011091f` (the full table with `file:line` for every
writer and attach site is `notes-inventory-011091f.md`):

| What | Count |
| --- | --- |
| distinct remarks the code writes | 69 (about 80 with variants) |
| functions or constants that write one | about 75 |
| places one is glued onto an answer, a heading or `data["notes"]` | about 92, five of them in `agent.py` |
| facts said by more than one function in different words | 14 (empty or missing box scores: 11 places; the box-score floor: 7; rebuilt lines: 5) |
| remarks in the text and missing from `data["notes"]` | 13 groups, among them every caveat of `single_game_high`, narrowed `player_stat`, the charts and the compiler's team path |

So a caveat is not one thing today: the same fact has up to eleven
wordings, and whether the page shows it depends on which template said it.

```python
class Decision:  # the question left it open; the system chose
    kind: DecisionKind  # closed
    chose: Value
    instead_of: tuple[Value, ...]  # what else it could have been, where that is a finite list
    why: Reason  # closed: the only one active, a near spelling, no season named ...


class Note:  # about the data, or about a term the answer uses
    kind: NoteKind  # closed
    facts: Mapping[str, Value]  # numbers, names, seasons - never a sentence
```

The dividing question is **could the question have said it differently?**
A name read as one player, a season nobody named, a minimum nobody
stated: yes, so a decision, and the answer owes the reader the value it
used and a way to the alternative (Jeff's rule, 2026-09-21). A game with no
box score, a floor, what "played" means: no, so a note.

The 69 remarks are instances of about 25 kinds. One phrase per kind, in
the sayer; the facts fill it. The first two families below are decisions,
the next two notes, and the last is divided ("Still open" 1).

| Family | Kinds | Remarks replaced |
| --- | --- | --- |
| **How a name was read** | `name_reading(typed, read, also, why)` with `why` = only one active, a namesake, a near spelling; `name_unmatched(names)`; `name_dropped(names)`; `also_matched(names)` | 6 |
| **A default taken** | `season_default(read)`; `season_type_default(read)`; `season_redirected(asked, answered, first, last)`; `window_short(asked, found)` | 5, plus every "in the 2026 regular season" in a headline where the question named neither |
| **What the source lacks** | `floor(table, first, excluded)`; `partial_season(table, season)`; `games_unseen(games, why)` with `why` = no box score, an empty box score, no play-by-play; `lines_rebuilt(games, columns)`; `rebuilt_agreement(season, column, pct)`; `stat_withheld(stat, games, why)`; `seasons_missing(seasons, stat)`; `standings_short(season, held, played)`; `game_list_disagrees(season, listed, played)`; `shots_unlabeled(season, shots)`; `snapshot(kind, date, why)` | 34 |
| **What a word in the answer means** | `definition(term)` for a closed set of terms: played, out, without, month, streak rule, overtime, rating formula, rank, neutral site, tenure counted, the pool a split is over | 16 |
| **A qualifier** | `minimum(games or attempts, of)`; `qualified(total, shown)`; `still_open()` | 4 |

Four of the 69 are not notes: a career total line, a playoff addendum, the
Cup final and the recent-meetings footer are extra VALUES, and become
parts.

How Phase 0 attaches a kind without moving an answer: each writer function
(the 75, not the 92 attach sites) records `Note(kind, facts)` in a
collector as it builds its sentence, the way `collect_name_readings` works
today; `Agent.ask` puts the collected notes on the answer and the stage
snapshot. The sentences stay exactly as they are until a Phase 2 slice
gives the kind its one phrase. From then the snapshot comparison holds the
notes fixed while the wording changes.

## Numbers that must become values first

`compare --values-only` proves a reworded answer kept its numbers only for
numbers that are in `data`. These are in the text alone today, and each
moves into `data` before the slice that rewords its answer:

- a shot chart's made and attempted;
- a single game's NetPoints possessions and win probability added;
- a career's true-shooting attempts;
- a matchup's "met 27 times in all, 4 of them with Durant";
- a compiled team total's playoff addendum ("78 more over a 7-game run").

## What the review changed

| Draft 1 | Found | Draft 2 |
| --- | --- | --- |
| one body, plus `summary` and `detail` | 16 splits carry four groupings; standings, NetPoints and a two-season by-month record are multi-part; detail is common (about 45 answers), not rare | `Result.parts`, each a typed body with a role |
| a row is a game | in 26 answers the subject is everyone and a row is (player, game); league-wide runs have an owner; rows carry a rebuilt flag, a span, a tied rank | row types say so |
| `Result \| Refusal` | 20 answers are questions back; refusals carry facts | `Result \| Clarify \| Refusal(cause, facts)` |
| `Measure(key, how, side)` | no per-100, percentile or per-N-minutes; no opponent's figure; no NetPoints category; six vocabularies, not four | `whose`, `category`, more `how`, `Unsupported` |
| `Window(order, count)`, no minimum | nowhere for `ranked_by` or a stated qualifier | `Window.by`, `Minimum` |
| `Calendar` for every `situation` | date cuts and conferences are not calendar values | `DateRange`, `Opponent(alignment)` |
| the 25-intent table | six rows wrong, among them `team_record` (five forms), `period_split` (never grouped), `player_netpoints` (not a chart) | corrected, with the counts |

## Still open

1. **What the decision and note split leaves to decide.**
   - *The dividing rule.* Proposed: "could the question have said it
     differently?", as above.
   - *The borderline kinds.* Under that rule a ranking's default minimum
     and its cut to the top ten are decisions (the question can state
     either), and `definition`, `window_short` (asked for ten, found
     seven) and `still_open` are notes. Proposed: so.
   - *One type or two with the trace.* `decisions.Decision` exists since
     4.4.0 and records EVERY reading (the subject's kind, each slot
     written); most are never said. Proposed: one type with a closed
     `kind`, and a declared subset of kinds that must be said, which
     contract 5 checks. The alternative is a second type for the said
     ones, and two lists to keep in step.
   - *Who may decide.* Proposed: only PLAN. RUN finds facts and writes
     notes. A redirect that depends on data ("no games this season, so
     his most recent five") is then the planner's, decided when it settles
     the span, not discovered by whatever words the answer.
   - *How much a said decision must say.* A name reading states the
     wording that reaches the alternative ("use the full name, or name a
     season he played"). A defaulted season is only shown ("in the 2026
     regular season"). Proposed: each kind declares which it is; name
     readings and redirects state the wording, the season shows the value.
     Whether the season TYPE states it ("say playoffs for the postseason")
     goes with the season-type default still open in `ROADMAP.md`.
2. **Parts.** An answer is a tuple of typed parts, the first the
   headline. The alternative is several Results per answer, which
   duplicates the subject and span and splits contract 5's bookkeeping.
3. **`record_when` is a split** by `line` with a total row, in 22 of 24;
   the other two are counts. One question family, two shapes: the reader
   decides from the words.
4. **`period` has three roles**: what a read sees (its own field), a
   filter (a `Line` with a period), and a dimension (a column per
   quarter). Proposed as above, with the catalog saying which measures
   have a reading in a quarter.
5. **The default line** per relation and shape is a declared table. It is
   where the old intents' habits survive; proposed: accept that, keep it
   small, and review its rows as each slice lands.
6. **`team_outlook`** reads ESPN's power-index snapshots: per date and
   per kind, with provenance that matters (a snapshot stamped after its
   season). Proposed: a declared relation, `team_snapshots`, not columns
   on team_seasons.
7. **Charts are two, not four.** Only `shot_chart` and `fingerprint` draw
   an artifact. `shot_distance` is a scalar on `shots` and
   `player_netpoints` a scalar and a split on `netpoints`. `ROADMAP.md`
   says "the four chart answers keep their own readers and renderers";
   proposed: that still holds for their READERS (the relations are
   declared, not ported), while their answers use the common shapes.
