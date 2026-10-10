# The target types

**Status: DRAFT 2, partly decided (Jeff, 2026-10-01); see "Decided" and
"Still open". Parts are code since Phase 2 - "Decided" says which, and
how the code departs from the draft.**
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
  data. Five points under it, all decided 2026-10-01 as proposed:
  - *The dividing rule* is "could the question have said it differently?"
  - *The borderline kinds:* a ranking's default minimum and its cut to the
    top ten are decisions; `definition`, `window_short` and `still_open`
    are notes.
  - *One type.* `decisions.Decision`, which already records every reading
    for the trace, gains a closed `kind`; the kinds that must be said are a
    declared subset, and contract 5 checks them.
  - *Only PLAN decides.* RUN finds facts and writes notes. A redirect that
    depends on data ("no games this season, so his most recent five") is
    the planner's, decided when it settles the span.
  - *How much a said decision says* is declared per kind: a name reading
    and a redirect state the wording that reaches the alternative, a
    defaulted season shows its value. Whether the season TYPE states it
    goes with the season-type default still open in `ROADMAP.md`.

  In code since the same day (`query/notes.py`): the kinds are attached
  where each remark is written, and the sentences have not moved.
- **`pair` is not a shape, on one condition: any number of subjects and
  any number of players beside them, each with its own role, stay
  expressible.** They do; "More than one player" shows how.

- **Declared in code by "the Result is typed"** (2026-10-05, the Phase 2
  review's cleanup (b)1, (b)5, (b)6; the lead's brief, no answer moved):
  - `Outcome = Result | Clarify | Refusal` as drafted, in `query/result.py`.
    `Refusal(kind, facts)` takes a kind from the union of two closed
    sets: the READING's causes (`reading.CAUSES`, decided from the words,
    pinned by the readings population) and the READ's (`result.RUN_CAUSES`,
    facts found in the warehouse: no games, a floor, a name nothing
    matches, ...), kept disjoint so a kind says which stage refused. One
    phrase table words both (`compose.say.refusal_phrase`). Beyond the
    draft, on purpose: `shown`/`under` - the page's values beside the
    sentence and the keys it reads the sentence under, because the
    readers' pages differed and the answers' data was held identical.
    `Clarify` has `kind`, `active` and `why` (`ambiguous` or
    `near_spelling`) beside the draft's two fields.
  - The draft's `cells`, on `Narrowing.cells` rather than `Result.cells`
    and `Part.cells` (every Result's cells applied to its first part so
    far): the minimal `Cell` union the old facts keys needed - `Period`
    (the draft's `Reading.period`, with its label), `OnDate`, `Line`,
    `Role`, `GameOfSeries`, `Calendar`, `Companions`, `Met`, `ShotValue`.
    An opponent, a venue and the teammates absent stay `Narrowing`'s own
    fields; `Span` gained `since`, `until`, `ordinal`. A dimension's
    parameter is its body's: `Grouped.of` (the `line(Line)` and
    `presence(of: names)` dimensions), `Runs.line`/`won` ("a Line or Won").
  - What a sayer needs beside the body is one frozen record per shape
    (`result.Facts`, 17 records) - not in the draft, which has no such
    field; it is what is left of the old `facts` mapping once its cells
    and its dispatch keys were taken out, and each record is a list of
    what Phase 4's renderers and the shape sayers still have to fold into
    bodies and cells.
  - `Scalar.how` names every scalar's reduction (`season`, `per_shot`,
    `per_100`, `total`, `projection`, `ranked` beside the drafted ones),
    and the sayer is chosen by the body's type and that one field
    (`Grouped.by`, `Rows.by`, `Chart.kind`) - "a sayer may branch on the
    shape and on nothing else", with the subject's kind and a cell the
    answer says (a quarter, a pair's `Met`) read inside it.
  - `PointShape(relation, shape, by)` is the planned point's shape - the
    draft's `Query.relation`/`shape`/`by` triple, carried beside today's
    `Query` on the plan (`compose.plan.Planned.shape`); the answer side
    reads only it. Settled by the planner from the reading's intent where
    the query alone could not tell two retired templates' words apart
    (`plan.shape_of`) until Phase 3, step 1, which the point reader names
    it since (below). Relations named beyond the draft's seven:
    `player_periods` and `team_periods` (a quarter or half of the games).

- **Declared in code by Phase 3, step 0** (2026-10-09, the lead's brief;
  the question re-reads in the answering loop moved onto the Reading, no
  answer moved):
  - The draft's `Unsupported(what, as_typed)` filter, as
    `Reading.unsupported: tuple[Cause, ...]`: what the words name that
    nothing reads, a `reading.Cause` per thing recognized, whose kind is
    the `what` (`playoff_round`, `non_calendar_situation`, `period_stat`,
    `period_as_condition`, `team_period_stat`, `bench_points`,
    `team_boolean_count`) and whose facts hold the words as typed (the
    round, the situation and what it reads as, the stat) beside the
    intent the page shows. A `Cause` rather than a new type because the
    planner says it through the one phrase table, and because
    `reading.Unsupported` already names the decline exception; it becomes
    a member of the Filter union, keeping its `as_typed`, when step 2
    declares the union. Recognized whatever answers, said only where the
    answer side declines.
  - `Reading.refused: Cause | None` - a refusal the words come to that no
    point answers past (`championship`; `no_player_reading`, a player
    named on a question whose shape has no reading for one), planned
    ahead of the point's own refusal. Not in the draft, which has no
    verdict on the Reading but the point's; it goes where the Reading
    stops carrying a point beside itself.
  - `too_short` is the parser's verdict before any Reading exists
    (`parse.too_short` returns the cause). `reading.CAUSES` 17 -> 27.

- **Declared in code by Phase 3, step 1** (2026-10-09, the lead's brief;
  no answer, result, remark or planned query moved on the four
  populations):
  - The draft's `Reading.shape` and `Reading.by`, named by the point
    reader: `shape` takes the target's seven (`reading.Shape`: `scalar`,
    `rows`, `ranking`, `comparison`, `split`, `runs`, `chart`), `by` is
    `PointShape.by` as it stood (what one row is for a grouped shape; how
    a scalar is reduced or what a reader's rows are ordered by - `line`,
    `count`, `date`, `measure` - and `""` where no reader takes the point).
    `by` is a string, not the draft's `tuple[Dimension, ...]`, because it
    still doubles as a reader's key; it narrows to the dimension when step
    4 keys the readers on the grammar.
  - `Reading.on`, the relation the point is read on, in the target's
    vocabulary (`reading.PointRelation`, the nine): the draft puts the
    relation on the Query alone and has the planner pick it, but two
    points the planner cannot tell apart from the fields - a quarter's log
    against a game log (`player_periods` against `player_games`, the same
    shape and `by`) and a team's own total said as its season's shape
    (`team_seasons` on a point the team compiler plans) - have to be named
    by the reader, so it names the relation outright. Today's
    `Reading.relation` (which compiler plans it, and the subject's
    everyone-ness: `player | everyone | team | ...`) stays beside it until
    step 2 retypes the subject (the `everyone` relation is in its list),
    measured not derivable from the subject's kind (581 of 674 `everyone`
    points have an `everyone` subject; 10 are `player`, 10 `team`, 35
    `team_players`, 48 `position`).
  - `Reading.source` is gone: the season line is `on="player_seasons"`,
    and the planner derives `Query.source` and `Query.skeleton` from the
    three (`plan.skeleton_of`) - one vocabulary on the Reading (contract 6).
    `PointShape` lives in `reading` and is built by the planner on every
    verdict (`plan.point_shape`), the one move the planner makes being a
    season-line point re-planned at the game level (`player_games`).
  - `coverage.SOURCES` is keyed by the `PointShape` (one entry per route,
    resolved per question where the table depends on the measure; the
    relation's own tables for a point no reader takes), the ranking floor
    applies where the shape is a `ranking`, and a reading with no point
    has no floor.
  - `Reading.left_out: LeftOut | None` - a fingerprint's "vs" the reading
    holds one side of: `LeftOut(held, names)`, the players held and the
    names the words also name (none: only one compared name matched
    anybody). A plain value, not the draft's `Met`: `Met` is the games two
    subjects shared, a cell of the pair relation; this is a reading of the
    words the answer states as the `name_left_out` decision. Read for a
    fingerprint alone, the one shape that says it.

- **Declared in code by Phase 3, step 2, the span** (2026-10-09, the lead's
  brief; no answer, result, remark, planned query or reading moved on the
  four populations - every reading compared through the projection, the
  typed value recorded beside it):
  - The draft's `Span`, as `reading.Span` on `Scope.span`, in place of the
    six slots `season`, `season_type`, `season_type_unstated`, `span`,
    `since`, `until`: `season` (one named, the year it ends in; never a
    default), `season_type` (3 where the words said the postseason, 2
    otherwise, None where no reader settled it), `both` (both types in one
    read: named outright, or none named on a "last N games" log),
    `career`, `since`, `until`. **Fields, not the draft's union over
    `one | range | career`**, because the readings hold the parts together
    and the relation is what picks between them, with its refusal where
    it cannot: measured on the 2,710 readings (`span_family.py`), a career
    beside a named season 3 times ("a career span and the 2001 season at
    once", refused by `span_of`), a career beside a range 8 (the range
    wins), both types beside a season 4, a range 3, a career 8. The draft's
    `stated` is a property (`season`, `career`, `range`, `postseason`,
    `both`); the regular season named outright is not told from the default
    (every reader reads it as the default, nothing says it was asked), and
    that is the one part a planner's season-type default note would need -
    still open below.
  - **One tagger per family, claiming what it read** (contract 2):
    `span.read_span(question, SpanContext) -> SpanRead(span, claims)`, over
    `lexicon.py`'s named patterns (contract 6; the module exists from this
    slice, the other families' words follow with their slices), run once
    and last in the stages, with the facts the earlier stages settled
    handed to it as a typed context (the intent, whether a player and a
    window were named, "vs"/"how many"/"record", the window, a date, a game
    of a series, an ordinal season, a range opened on a dated day) rather
    than read from the slot dict. `reading.Claim(start, end, what)`;
    `Reading.claims` (the Route carries them); a nested claim folds, a
    partial overlap raises (`span.claimed`). Step 3 reads the unread words
    off the claims.
  - **The relation's cell table carries the family's row** (contract 4):
    `RELATION_SCOPING` and `TEAM_RELATION_SCOPING` hold the three cells by
    name (`Span.CELLS`: `career`, `range`, `both`), each applied by
    `span_of` (which takes the `Span`) through `scoped_player`/`scoped_team`,
    said by `ResolvedSpan.during`/`years` and `team_span_label`, and
    refused per reader in `RELATION_SCOPING_EXCLUDED` /
    `TEAM_RELATION_SCOPING_EXCLUDED` with a reason about the answer - the
    season line's, the NetPoints relation's and the team-season readers'
    rows live in the table of the relation whose steps settle their span.
    `STATED_SCOPING`'s rows name no span cell: `relation_scoping(intent)`
    carries the relation's, `relation_span(intent)` /
    `team_relation_span(intent)` a reader's own; `unhonored_scoping` reads
    the typed value's cells against them and still says the old slot
    names in a decline ("cannot honor ['since']", `reading._CELL_SLOT_NAMES`)
    until the decline-to-Cause commit rewords it. The `both` cell is
    "words only" for the three readers whose exclusions the planner
    refuses outright (period_split, streak, player_matchup): they step
    aside for it and the compiler's sentence, which reads both, answers -
    as the retired bodies did.
  - **The record keeps its old shape through a projection**, which is how
    a retyped part is proven identical with no field ignored:
    `Scope.projected()`, `Reading.projected()`, `Query.projected()` give
    `stages.plain` the slot-era keys (the six slots; the point's
    `span`/`season` pair) from the typed values, and the typed value is
    recorded beside the reading under its own key (`span`), listed by count.
  - `Reading.subject_span: Span | None` (and `Query.subject_span`): the one
    typed override where the point reader settles the subject's span apart
    from the scope's - over the career for a date (the name narrowed over
    every season, the date the scope), this season outright for a run (an
    empty run is refused, not redirected). The `span`/`season` slot pair
    on the point and the Query, and the point reader's own "ever" rule
    (every one of its 48 matches was the lexicon's career word too), are
    gone.
  - Not this slice's, on purpose: `season_n` stays a slot (the games'
    cuts family, `SeasonOfCareer`); the window's half of "the past N
    seasons" went to the window's slice (below); the guard words of other
    families the tagger reads ("vs", "how many", "record") reach it as
    context until their slices move them to the lexicon ("last" is the
    window's, `lexicon.LAST_WORD`, since its slice).

- **Declared in code by Phase 3, step 2, the window** (2026-10-09, the
  lead's brief; no answer, result, remark, planned query or reading moved on
  the four populations - every reading compared through the projection, the
  typed value recorded beside it):
  - The draft's `Window`, as `reading.Window` on `Scope.window`, in place
    of the four slots `order`, `limit`, `rank`, `ranked_by` (and the point's
    `offset`, 0 on all 2,710 readings, deleted): `order` (which end of the
    span, `recent` or `first`, where the words said; never a default),
    `count` (how many rows; never below 1), `of` (`games`, or `seasons` for
    a history's "past 5 years"), `rank` (which end of a team ranking:
    `most`, `fewest`, `best`, `worst`), `by` (the measure a ranking of the
    games over a yes/no stat is ordered by). **Fields, not the draft's
    `order: recent | first | top | bottom`**: measured on the 2,710 readings
    (`window_family.py`), a count stands with no end 102 times ("top 5", a
    history's seasons, "last 10 home games" on a reader with no window of
    its own) and an end never without a count; "top"/"bottom" name no end
    of the SPAN - a ranking's own rank word says which end of the ranking -
    so the grammar reads them as the count alone, as the stages did. `rank`
    is the draft's missing row ("nowhere for `ranked_by` or a stated
    qualifier" -> `Window.by`; the qualifier, `Minimum`, is still open).
  - One tagger: `window.read_window(question, WindowContext) ->
    WindowRead(window, claims)`, over `lexicon.py`'s `WINDOW_GRAMMAR`,
    `ORDER_WORDS`, `SINGLE_GAME`, `RANK_WORDS`, `RANKED_BOOLEAN_GAMES`,
    `RANKED_BY_WORD` (and the family's intent guards: `GAMES_WORDS`,
    `WHO_RANKS`, `PERIOD_TOP`, `LOG_OR_WINDOW_WORDS`, `LAST_WORD`), run in
    `router._settle` just before the span tagger, which takes the typed
    window as its context; the context is the settled intent and whether
    the stat is a yes/no one. Measured first: the parser read the grammar
    before the stages and again after them, and every stage that dropped a
    COUNT (`_names_a_count` and its five callers, the model-era filler
    rules) was undone by the second read, so those rules were dead and
    went; what the stages did move on the 2,710 - a history's seasons (38),
    an end dropped on a reader with no window (21), a team ranking's `rank`
    (86), `ranked_by` (2) - the tagger reads the same way, the relation's
    resolution (`relation_window`, `history_seasons`) identical on 2,710 of
    2,710.
  - The cells (contract 4): `Window.CELLS` is `window` (an end with its
    count: the slot `order` until now) and `ranked_by`; both in
    `RELATION_SCOPING` (the compiler's boolean-game ranking reads `by`),
    `window` in `TEAM_RELATION_SCOPING`, each reader's refusal of `window`
    in the `*_EXCLUDED` rows under the cell's name with its reason, and no
    row of `STATED_SCOPING` naming `order`; `COMPILER_SLOTS` deleted. **A
    bare count is not a cell**: no reader ever refused or stepped aside for
    one - each takes it as its own parameter (rows, runs, meetings or
    seasons to show), and the games relations read it as the newest N
    (`relation_window`) - so `Window.cells()` reports `window` only where
    an end was named. Still open below.
  - The record keeps its old shape through the projection
    (`Scope.projected()`, `to_slots()`: `ranked_by` after `fields`, `rank`
    after `kind`, `order` and `limit` last; `Reading.projected()` emits the
    point's `offset` as 0), and the typed value is recorded beside the
    reading as `window`.

- **Declared in code by Phase 3, step 2, the games' cuts** (2026-10-09, the
  lead's brief; no answer, result, remark, planned query or reading moved on
  the four populations - every reading compared through the projection, the
  typed value recorded beside it):
  - The draft's `Opponent`, `Tenure`, `Venue`, `OnDate`, `DateRange`,
    `Calendar`, `Round`, `GameOfSeries` and `SeasonOfCareer`, as
    `reading.Cuts(opponent, tenure, venue, date, situation, round, game_n,
    season_n)` on `Scope.cuts`, in place of the eight slots `opponent`,
    `own_team`, `venue`, `date`, `situation`, `round`, `game_n`,
    `season_n`. **Fields, not nine types**, because the readings hold them
    together and a relation applies each as one more clause over the same
    rows: measured on the 2,710 readings (`cuts_family.py`), an opponent
    beside a venue 32 times, a round beside a situation 5, a series game
    beside a round 3 and beside an opponent 2, a date beside its own month
    4. The `situation` is one typed value, `reading.Situation(text,
    calendar, alignment)`: the words as typed and what they parse to - a
    `CalendarNarrowing` (the draft's `Calendar`; its `since_day` and
    `since_date` kinds are the draft's `DateRange`), an
    `AlignmentNarrowing` (the alignment half of the draft's `Opponent`), or
    neither (the draft's `Unsupported` for this family: an age, overtime,
    "since returning" - refused by value, as before). One cell for the
    three rather than three, because the relations' tables declare the
    `situation` and a decline names it so; measured: 96 readings carry
    one, 42 a calendar narrowing, 21 an alignment, 33 neither.
  - One tagger: `cuts.read_cuts(question, CutsContext) -> CutsRead(cuts,
    claims, dated_since)`, over `lexicon.py`'s `VENUE_HOME`/`VENUE_AWAY`,
    `SITUATION`, `CALENDAR_DATE`, `NUMERIC_DATE_RANGE`, `ROUND_WORDS`,
    `SERIES_GAME`, `ORDINAL_SEASON`, and the calendar's own readers of a
    situation value (`SITUATION_WEEKDAY`, `SITUATION_MONTH`,
    `SITUATION_SINCE_DAY`, `SITUATION_SINCE_NUMERIC`, `ALIGNMENT` with
    `ALIGNMENT_NAMES`, `BARE_MONTH`, `HOLIDAY_SPELLINGS` and
    `HOLIDAY_WORDS`, `CONFERENCE_WORDS`; `calendar.py` holds no pattern of
    its own and imports no regex engine), run in `router._settle` at the
    position of the last stage that wrote a cut, before the window's and
    the span's taggers (the span reads the date, the series game, the
    ordinal season and the year a dated range opened at from it); the
    context is the settled intent, the split, and the subject reading's
    opponent and absent teammates. The opponent and the tenure are the
    subject reading's (`read_subject`, `_apply_own_team`): the tagger takes
    the first and claims nothing for either. Measured first: no stage moved
    a cut between the route and the reading except the tenure (14, the
    subject's own write).
  - **A word two readings share is one claim named for both.** "his last
    game 7" is the window's "last game" and the postseason's "game 7";
    "in march 24 2018" the month and the day in it. `span.claimed` joins a
    partial overlap into one claim (`"window+game_n"`) instead of raising:
    on master "lebron's last game 7" raised `ValueError` out of the reader
    to the user, and the cuts tagger's "game 4" claim beside the window's
    "last game" would have widened that. The nested-claim fold is unchanged.
  - The cells (contract 4): the seven cuts by name in `RELATION_SCOPING`
    (`Cuts.CELLS` less `round`: no game is labeled by its round, so every
    reader refuses it and no table declares it) and five in
    `TEAM_RELATION_SCOPING` (`team_relation.TEAM_CUTS`: less `tenure` and
    `season_n`, one player's), each applied by `scoped_games` /
    `league_games` / `team_games` (the tenure by `scoped_games` now, where
    the compiler alone passed it until this slice) and said by
    `Narrowed.filters` / `TeamNarrowed.filters`; a reader whose words state
    fewer takes `relation_cuts(intent)` / `team_relation_cuts(intent)` with
    its exclusions in the `*_EXCLUDED` rows under the cut's name with its
    reason (a season line's three readers, a high, a count, the NetPoints
    two, the quarter ranking, a team's line and projection, the with/without
    split), and no row of `STATED_SCOPING` names a cut. `SCOPING_SLOTS` is
    shorter by the seven; `_MODEL_SLOTS` by `date` (a model-era date is
    dropped at the stages' door with the span's and window's keys);
    `reading.cell_set(scope, cell)` is the one reading of a cell name
    against a Scope for the tables that still list cells by name
    (`_CONDITION_PLAYER_ONLY_CELLS`, the planner's per-reader exclusions,
    the shot readers' narrowing check). `_TEAM_READER_REFUSES` and
    `_CONDITION_PLAYER_ONLY_CELLS` keep `season_n`: removing it would hand
    a team log's or a team streak's ordinal season to the planner's
    sentence where the reader's own, naming the missing player, answers
    today - the decline-to-Cause commit those two are owed by.
  - The record keeps its old shape through the projection
    (`Scope.projected()`, `to_slots()`: the eight slots emitted where the
    fields stood, `_CUT_SLOT_POSITIONS` - the opponent and the tenure
    after `teams`, the date and the situation after the span, the series
    game, the ordinal season and the round after the split, the venue
    after the period condition), and the typed value is recorded beside
    the reading as `cuts`.
  - Not this slice's, on purpose: a situation nothing reads is still
    refused by the relation by value, at RUN, with the sentence it had
    (`Reading.unsupported`'s `non_calendar_situation` is said only where
    the answer side declined) - moving it to PLAN rewords 33 answers and is
    a decline-to-Cause commit; the subject reading claims nothing for the
    names it reads (the subject's own slice); the split beside the venue
    is the role family's.

- **Declared in code by Phase 3, step 2, the period** (2026-10-09, the
  lead's brief; no answer, result, remark, planned query or reading moved
  on the four populations - every reading compared through the
  projection, the typed value recorded beside it):
  - The draft's `period: Period | None` ("what a read SEES of each game:
    a quarter or half"), as `reading.Period(number, half)` on
    `Scope.period`, in place of the two slots `period` (1-4, or an
    overtime period by number, which nothing reads from the words) and
    `half` (1-2); None is the whole game. **One cell, `period`, not two:**
    measured on the 2,710 readings (`period_family.py`), 120 carry one - 79
    quarters and 41 halves, never both - both relations apply a quarter and
    a half through one step (`narrow_periods`, over the period's rebuilt
    line of each game), and every reader's exclusion rows paired the two
    slots under one reason in two wordings (rewritten as one). The slot
    name a decline said (`period` or `half`) is kept by `Period.unhonored`
    until the decline-to-Cause commit rewords it; the RUN-side cell the
    read applied keeps its label, `result.Period(label, periods)`, and the
    sayer's words stay where they are.
  - One tagger: `period.read_period(question, PeriodContext) ->
    PeriodRead(period, claims)`, over the lexicon's `WHICH_QUARTER`,
    `WHICH_HALF` and `ORDINAL_PERIODS`, run in `router._settle` once the
    intent is settled and before the cuts', the window's and the span's;
    the context is the settled intent alone. `period.which_period(text)`
    is the one reader of which period any words name: the intent stage
    chooses among the three period intents by it (the choice itself is
    step 4's, over the lexicon's `QUARTER_WORDS`, `HALF_WORDS`,
    `PERIOD_LEADERS`, `PERIOD_GAMES_WORDS`, `PERIOD_AS_CONDITION`,
    `BY_QUARTER`), and the line slice's condition reader
    (`parse.read_period_condition`, over `lexicon.PERIOD_CONDITION`) reads
    a condition's period through it. Measured first: three writers of one
    value (the router's `_period_asked` through the intent stage's `raw |=
    asked`, under the two player-side intents and a team's half; the
    parser's `_read_route_period` for a team's quarter), none moving it
    between the route and the reading, no period word inside another
    tagger's claim; and the value written ONLY under the three period
    intents - six readings name a quarter and carry none, their subject
    unread and the point reader declining them by its guard. The tagger
    keeps the value under those three readers (`PERIOD_INTENTS`) and
    claims nothing where it writes nothing, so those six readings are
    identical; whether the planner should refuse a period on a reader that
    takes none (the typed design) is a decline-to-Cause commit, still open
    below.
  - The cells (contract 4): `Period.CELLS` is `period`, in
    `RELATION_SCOPING` (applied by `apply_period`, said by
    `Narrowed.filters`) and `TEAM_RELATION_SCOPING` (`_team_games_apply_period`,
    `TeamNarrowed.filters`); each reader's refusal of it in the `*_EXCLUDED`
    rows under the one name with one reason (the log, the line, a run, the
    splits, a record, a matchup, the two shot readers on the player
    relation; a leaderboard, a record and a series on the team relation);
    `STATED_SCOPING`'s ranking-by-a-quarter row takes
    `relation_period(intent)` where it listed the two names;
    `SCOPING_SLOTS` loses the two, `_MODEL_SLOTS` loses `period`, and a
    model-era `period`/`half` key a route still carries is dropped at the
    stages' door with the span's (`_MODEL_SPAN_KEYS`); `cell_set(scope,
    "period")` is whether a value is set.
  - The record keeps its old shape through the projection
    (`Scope.projected()`, `to_slots()`: `period` and `half` where the two
    fields stood, after `split` and before `period_condition`), and the
    typed value is recorded beside the reading as `period`.
  - Not this slice's, on purpose: a period as a CONDITION on which games
    count (`PeriodCondition`, `Scope.period_condition`, the
    `period_as_condition` cause) is the line slice's; "by quarter" reads no
    period (the point's shape); an overtime period named in the words
    ("most points in an overtime game", 4 of the 2,710, every one a
    `situation` the relation refuses by value) is measured and not read -
    reading it as a period 5+ would move answers.
- **Declared in code by Phase 3, step 2, the line and the companions**
  (2026-10-09, the lead's brief; one feed answer moved, named, on the four
  populations - every reading compared through the projection, the two
  typed values recorded beside it):
  - The draft's `Line(measure, op, value, who, period)` as
    `reading.Line(measure, op, value, period, as_typed, keyed, narrows)`
    on `Scope.lines`, in place of the FIVE carriers a line had - the
    `stat`/`threshold` slot pair, the `above` and `below` phrases kept
    whole, the point's re-reading of the number's words into a predicate,
    a companion's `reached` entry, and `period_condition`
    (`PeriodCondition`, gone). `measure` is the game column's key (the
    `Measure` catalog is the next slice's; `Scope.stat`, `rate`,
    `per_game`, `side`, `shot_value`, `fields` and `kind` stay). The
    draft's `who` lives where the value does: on the Scope it is the
    subject's, on a `Companion` the companion's. Two bits the draft did
    not have, each a fact the slots carried by their NAME: `keyed`, the
    shape's own line (the draft's `line(Line)` dimension - a count's, a
    record's, a run's, a high's; the `threshold` slot), read into the
    point's predicate and setting no cell; `narrows`, a line the relation
    filters the games by (`below`/`above`). A line that is neither (the
    second and later bare "N stat" of "20 pts, 10 reb, 5 ast") is read
    and applied by the league's multi-line listing alone, as before.
    Built from the words, never from the slot pair: in 11 of the 2,710
    readings the pair contradicted the words ("20+ point 5+ assist" read
    `stat: assists, threshold: 20`), and the typed value is the words'.
  - The draft's `Companion(player, side, predicate)` as
    `reading.Companion(player, side, predicate, line)` on
    `Scope.companions`, in place of three slots (`with_player`, `without`,
    `conditions`), read by ONE reader (the subject reading, with a claim
    on the phrase) and written by ONE writer (`subject.apply_subject`);
    the stages take the typed companions where they took `router.Beside`
    (gone) and read no name. **One cell, `companion`, not two:** no table
    ever honored or refused `without` apart from `conditions`.
  - One tagger for the lines: `line.read_lines(question, LineContext) ->
    LinesRead(lines, claims, stat)`, over the lexicon's `THRESHOLD`,
    `THRESHOLD_PAIR`, `THRESHOLD_WORDS`, `SCORED`, `FOULED_OUT`, `ABOVE`,
    `BELOW`, `MEASURE_PHRASE`, `AT_MOST_LEADS` (and `MEASURE_WORDS`, moved
    there from `measures.py`), run in `router._settle` after the subject's
    slots and before the games-won read; `line.read_period_line` is the
    parser's reader of a line in a quarter (`lexicon.PERIOD_CONDITION`,
    `CONDITION_NUMBERS`, `CONDITION_STAT_WORDS`), its words blanked, not
    cut, so later claims keep their offsets. The reading's gating is the
    stages' measured rule: a bare "N stat" is a line only on a reader whose
    shape is one (`THRESHOLD_INTENTS`); elsewhere a floor of minutes, a
    line under a number, two or more pairs.
  - The cells (contract 4): `Line.CELLS` is `line` and `period_line`,
    `Companion.CELLS` is `companion`, all three in `RELATION_SCOPING`
    (applied by `narrow_measures`, `_apply_period_condition`,
    `_narrow_player_games`/`condition_player`); the with/without split's
    row of `STATED_SCOPING` names `companion` (the team relation's one
    cell its words state; `WITH_WITHOUT_STATED` folded into the table);
    `_TEAM_READER_REFUSES` and `_CONDITION_PLAYER_ONLY_CELLS` name the
    cells; `SCOPING_SLOTS` is `split` and `rate`; a decline still says the
    slot names (`companion_slot_names`, `line_slot_names`,
    `slot_names_set`) until the decline-to-Cause commit rewords it.
  - The record keeps its old shape through the projection
    (`Scope.projected()`, `to_slots()`: the seven slots where they stood,
    `_ATTACHED_SLOT_POSITIONS`); `with_player` is the projection's one
    intent-dependent key - the with/without split's name where the point
    is the split and none is absent, a `conditions` entry on any other
    point, as the two slot writers wrote him (`split_by_presence`, read
    off the intent or the `presence` group) - and the typed values are
    recorded beside the reading as `lines` and `companions`.
  - Not this slice's, on purpose: `Won` and `Met`. A team's run of wins is
    the point's `won` predicate (`Reading.kind`, the `kind` slot the brief
    keeps), and a matchup's `Met` is the pair shape's own `by="met"` on
    the Reading - neither is a filter the relations apply, so neither is
    a `Filter` member here. Still open below.

- **Declared in code by Phase 3, step 2, the measure** (2026-10-10, the
  lead's brief; no answer, result, remark, planned query or reading moved
  on the four populations - every reading compared through the
  projection, the typed value recorded beside it):
  - The draft's `Measure(key, how, whose, category, side)` as
    `reading.Measure(key, as_typed, how, unit, whose, side, shot_value,
    category, won, beside)` on `Scope.measure`, in place of the seven slots
    `stat`, `rate`, `per_game`, `side`, `shot_value`, `fields`, `kind`.
    `key` is the catalog's (`measure.CATALOG`, `MeasureKey`: 42 keys, the
    normalizer's spelling where it has one and the compiler's otherwise,
    one key for `threePointFieldGoalPct`/`three_pct`/`three_pt_pct`/
    `three_point_pct`; per key how it is read on each relation - the game
    column, the compiler's derived measure, the ranking's metric for a bare
    name, a career, a total and a per-game figure, the team metric and the
    opponent's), `how` the draft's less `count`, `rate` and `percentile`
    (a count is the shape's, `per_100` is the one rate, nothing ranks a
    percentile) plus `per_90`, a unit nothing holds that the refusal names.
    **Beyond the draft, each a fact the slots carried:** `as_typed`, the
    spelling the reader wrote (what a refusal prints: "no team metric for
    stat 'ppg'" - and what the slot-era `stat` projects to; a key no
    catalog holds, `career_playoffs`, is `key=None` with its spelling),
    `unit`, the rate's words (the `rate` cell, a reader honors or declines
    it by name), `shot_value` (the shots relation's: the measure's rather
    than a cell of the relation, since a stat naming the same shots arrives
    beside it on 5 of 13 and the relation reads the value first), `won` (a
    run's result, the `kind` slot - open item 12's proposal, taken: a run
    of wins is a measure of `wins`, the words' "losing streak" read beside
    whatever stat is named, as the stages wrote it on every run), `beside`
    (the columns a ranking shows, `fields`). `category` is set only where a
    fingerprint metric is named outright (`assist_o_net_pts`: `netpoints`
    of the `assist` category on offense, 66 names). A Measure with no key
    and no spelling holds what the question says about its measure without
    naming which (a unit, a side, which shots, a run's result).
  - **The six vocabularies are lookups into the catalog** (`measure.ALIASES`,
    390 spellings, `key_of`): the normalizer's keys, the grammar's, the
    box-score words, the team metrics' alias texts (`lexicon.TEAM_METRIC_WORDS`,
    `STAT_ALIASES`), the leaderboard metrics' names (a metric named
    outright keeps its form: `avg_points` over a career stays the average),
    the compiler's derived names. `measures.resolve_metric`, `stat_measure`,
    `stat_column`, `log_extras`, `period_split_measure`, `streak_column`
    and `team_metrics.resolve_team_metric` take the `Measure` and read the
    facets; the alias tables they read (`METRIC_ALIASES`,
    `CAREER_METRIC_ALIASES`, `MEASURE_ALIASES`, `WORD_MEASURES`,
    `normalize_stat`) are gone. Where two spellings of one key resolved
    differently before (a team alias text had no game column, an
    abbreviation no metric), the key's own spelling decides - measured on
    `tests/query/measure_spellings.json` (391 spellings, the four lookups
    on `508d643`): every facet a vocabulary reached is unchanged, and 12
    spellings no reader reached with the facets that moved are unified.
    The team compiler's total still declines a measure named by a team
    metric's alias (`measure.named_by_a_team_metric`): the alias text never
    matched a column, so the per-game line answered, and holding that
    identical is a finding, not a design (ISSUES.md).
  - **One tagger** (contract 2): `measure.read_measure(question,
    MeasureContext) -> MeasureRead(measure, claims)`, in `router._settle`
    at the position of the last stage that wrote one of the seven, over
    the settled intent, the model's key as CONTEXT, the stat the lines
    tagger read beside a line and whether one is keyed, and a test's side,
    shot value and columns. The words' own measure (`measure.named`: the
    grammar, `lexicon.MEASURE_GRAMMAR`, then the longest box-score word)
    stands over the model's key, and where the words name none the key
    stands as it did; the intent stages that turn on a games key read the
    same context before the tagger. It reproduces the sixteen writers the
    stages had, in their order (`measure.py`'s docstring lists them), and
    claims what it read: 890 claims on the 2,710 readings, and two
    overlapping stretches of ONE reading ("netpoints per 100" and "per 100
    possessions") are one claim of it (`span.claimed` keeps the name where
    both readings are the same; 6 read as `measure+measure` before).
  - The cell (contract 4): `Measure.CELLS` is `rate` alone - a unit asked
    for, which the season-line ranking honors (a total, a NetPoints
    metric's per-100 form; a unit the metric has no form of is the point's
    `ranking_unit` refusal) and the team compiler's unnarrowed total
    honors, and every other reader declines in the sentence it had ("the
    relation cannot honor ['rate']"). Named in `STATED_SCOPING`'s ranking
    row and the planner's team-scalar cells through `Measure.CELLS`;
    `SCOPING_SLOTS` is `split` alone; declared in NO relation table, since
    a unit narrows no games - the measure's other parts are the shape's
    (what is read), not cells (which games are read).
  - The record keeps its old shape through the projection (`to_slots()`,
    `projected()`: the seven slots where the fields stood, the lines'
    three after `stat`, the window's `ranked_by` after `fields` and its
    `rank` after `kind`; a side or a rate the key's own spelling folds in,
    `netpoints_defense_per_100`, `avg_game_score`, is left out, as the
    slots never carried it apart), and the typed value is recorded beside
    the reading as `measure`.
  - Not this slice's, on purpose: the point's `measures` (a list of the
    compiler's names) and `aggregate` stay the compiler's, derived from the
    `Measure` through the catalog at 54 construction sites - retyping them
    as `tuple[Measure, ...]` is the `Query`'s shape, RUN's input (open item
    13 below); the subject's own slots (`player`, `players`, `team`,
    `teams`, `position`) are the next slice's.

- **Declared in code by Phase 3, step 2, the subject's own** (2026-10-10,
  the lead's brief; no answer, result, remark, planned query or reading
  moved on the four populations - every reading compared through the
  projection, the typed value recorded beside it; 14 unit-test calls in 7
  tests moved, each a test handing a router-era slot shape the count now
  projects):
  - The draft's `Subject(kind, names, position, of_team)` as
    `reading.Subject(kind, players, teams, position)` on `Scope.subject`,
    in place of the four slots `player`, `players`, `team`, `teams` and the
    point's `Reading.position`. **The names are two fields beside the kind,
    not the draft's one `names` tuple, and the kind is the reading's, not
    what the names say**, because the readings hold them apart: measured on
    the 2,710 readings (`subject_family.py`), players and a team together on
    43 (22 team records keyed on a companion's line, 21 a team named beside
    a player that nothing placed), a player under a `team` kind on 23, a
    player under `everyone` on 11 (the grammar's subject a count or a high
    settles where the reading named nobody), a team beside a position group
    on 9 (the draft's `of_team`, which is `teams` here). One player is never
    carried in the plural slot and two never in the singular, and a list of
    teams never at all, so the COUNT says which slot each projects to
    (`player` for one, `players` for two or more; `team`, `teams` the same)
    - a slot dict that listed one name in a plural slot or split two teams
    between `team` and `teams` comes back as the count says (the 7 tests).
    The draft's `players`/`teams` kinds are `pair`/`teams`, and it had no
    `position` or `team_players` kind; both are the reading's
    (`subject.SUBJECT_KINDS`, `reading.SubjectKind`). `Subject.player` and
    `Subject.team` are the one player and the one team a reader reads.
  - **One reader, one writer, no tagger.** The subject reading
    (`subject.read_subject`, once per question) is the family's reader and
    `subject.apply_subject` its one writer of the typed value (the kind and
    the position group the reading's, the names as the stages and the
    reading's settling left them). `subject.Subject`, the reading's record,
    IS a `reading.Subject` - the typed value as read - with the fields only
    the parser's settling needs beside it (the opponent and the player's own
    team the cuts tagger takes, the companions, the evidence, the names the
    model invented or filed as filler, the season named, the intent and why,
    the claims). The stages are handed `router.Named` (the typed subject,
    the opponent, the grammar's subject and the team words the reading read)
    and read no name of their own; the three name moves they still make
    under the intent they settle (a count's or a high's subject from the
    grammar, 30 readings; a team's quarter from its one nickname, 27; a
    quarter's player read back, 1) are the reading's words written on the
    typed subject. `router._MODEL_SLOTS` went (the freeze 9 -> 8, the debt 4
    -> 3), and `settle` refuses a name passed as a slot.
  - The point reader takes the kind, the names and the position from the
    typed value (`read_point` writes the reading's kind and position onto
    it, as the parser's last step did, for a Reading built by hand), and a
    position group narrows the league's own moves alone
    (`point._everyone_point`; every other move reads the scope without it):
    the planner's `Query.position` is the point's subject's position, and
    `Reading.projected` emits the old `position` field from it where the
    point is the league's.
  - **No cell.** No relation table declares, honors or refuses a name: a
    reader narrows to whom it is about and a name nothing resolves is the
    relation's own refusal (`name_unmatched`, a clarification).
    `Subject.CELLS` is empty; a behavioral test per relation shows the typed
    subject changes what each reads (one player's games against
    another's, the league's read narrowed to a team's players and to a
    position group, the team relation's team, a pair's two players).
  - The family's words are the lexicon's (team nicknames, cities,
    abbreviations, singulars, positions, the versus and "for" phrases, the
    subject-of grammars and the words that are never a subject, where a name
    ends, the words that are never a name, the child grammars' words, two
    teams meeting); the subject reading claims the names, the teams, the
    opponent, the tenure and the position group it settled
    (`subject.subject_claims`; `player`, `team`, `opponent`, `tenure`,
    `position`).
  - The record keeps its old shape through the projection
    (`Subject.to_slots`, `slot_record`, `Scope.to_slots`, `Scope.projected`,
    `Reading.projected`'s `position`), and the typed value is recorded
    beside the reading as `who` (the reading's record keeps the key
    `subject`).
  - Not this slice's, on purpose: the intent and why it is not the route's
    stay on the reading's record (step 4); `split` is the role family's.

- **Declared in code by Phase 3, step 2's closing slice, the planner's cell
  checks** (2026-10-10, the lead's brief; no answer, result, remark,
  planned query or reading moved on the four populations, compared with
  nothing ignored - no record field was added):
  - **One table of cells per relation, and one row per shape on it**
    (contract 4). `RELATION_SCOPING` and `TEAM_RELATION_SCOPING` keep the
    cells each relation applies; `RELATION_SCOPING_EXCLUDED` and
    `TEAM_RELATION_SCOPING_EXCLUDED` are keyed by the planned point's
    `PointShape` (by the retired templates' names until now), one row per
    route in `compose._ROUTES` (a test holds the keys equal, beside
    `SHAPE_NAMES` and `coverage.SOURCES`), each a
    `reading.ShapeCells`: `unstated` (the cells its words do not state,
    each with its reason, word for word where the row existed),
    `refused` (of those, what the planner refuses outright, saying the
    first one's reason: a run, a quarter's split, two players' meetings),
    `taken` (beyond the relation's table: the ranking's unit, a team
    record's month, the player's cells a team's log, splits and record
    over a line refuse in their own words, the team compiler's unit on a
    team's own season), `declined` (`"plan"` where the shape's reader is
    the point's only answer and the planner declines what its words do not
    state - a comparison, the declared relations, the team shapes Phase 2
    ported; `"read"` for a team's own season, whose reader declines when
    asked and whose point the team compiler's sum may still answer),
    `sides` (one named half of the starter/bench split, never the bare
    category - `unhonored_scoping`'s special case), `named_player` and
    `named_subject` (a run's cells only a named player's, or a named team's
    or player's, games carry). **A record per shape, not per cell**,
    because three of its facts are the shape's and not a cell's (where a
    decline is said, the split's half, the subject a run needs), and the
    measurement held every one: 68,794 (template, cell) verdicts by
    construction and 2,710 answered questions identical on both trees
    (`~/association-research/stages/cells_family.py`, `--matrix`).
  - **One check** (`compose.plan.cells_unhonored(scope, key)`, the
    `(slot, cell, why)` of every cell a scope sets beyond what the shape's
    words state, `cells_stated(key)`; and `cells_declined(point, key)`, what
    the planner refuses before any reader runs), read by the planner, the
    season line's readers' check and the answer side, which steps aside
    before asking a reader: no reader takes a list of its own. It replaces
    `STATED_SCOPING`, the four branches of `_shape_declines` (by the
    reading's intent), `reading.unhonored_scoping` with `_SPLIT_SIDE_ONLY`,
    `_excluded_cells_set`, `_team_shape_cells` with `_TEAM_READER_REFUSES`,
    `_streak_league_cells`, `conditions.condition_needs_player_refusal` with
    `_CONDITION_PLAYER_ONLY_CELLS`, `team_stats.team_season_declines`, the
    readers' `stated=` and `relation_scoping`/`relation_span`/
    `relation_cuts`/`relation_period` and their team kin. The relation's own
    check stays the compiler's (`compose.core._check_relation_scoping`, the
    relation's table and what the shape takes), over `Scope.cells()`.
  - **Every cell a scope sets is `Scope.cells()`** - each family's and the
    split's, the role family's one cell (`Scope.CELLS` names it: `Split`
    is a closed set of names, not a type, so the cell is named on the
    Scope rather than on `reading.Split`); `reading.cell_slots` says each
    by the slot names it was declared under, so every decline is word for
    word; `SCOPING_SLOTS` is gone.
  - **Keyed by the shape, not the intent**: measured on the 2,597 readings
    with a point, the slot-era branches' intent and the planned shape agree
    on 2,596 (the other is refused before it is planned), so the re-key
    moves no verdict on the populations; a hand-built point naming no
    intent is judged by its shape now.
  - Not this slice's, on purpose: `Scope` folds into `Reading` with step 4,
    where `router.Route` (which carries a Scope) is deleted - folding it
    now would touch every `scope.` site twice (the lead's bound); the
    declines' sentences still say the retired names and slots
    (`SHAPE_NAMES`, `cell_slots`) until a decline-to-Cause commit rewords
    them. A fix it made in passing, its own enumerated commit: the league's
    own read refuses the cells its subject cannot settle (a date, a series
    game, a teammate, a split, a tenure, a window), which it dropped - 62
    feed answers move from a broader question answered to a refusal.

- **Declared in code by Phase 3, step 3, the unread words** (2026-10-10,
  the lead's brief; no answer, result, remark, planned query or reading
  moved on the four populations but the field the step adds and the
  enumerated fixes it found):
  - The draft's `Reading.unread: tuple[str, ...]`, as drafted: the content
    words of the question no claim covers, in the question's order, spelled
    as the claims ledger spells a word (lowercased, its edge punctuation
    dropped) - by the ledger's own rule, moved to the lexicon so both read
    it (`content_tokens`, `CONTENT_STOPWORDS`, `without_possessive`,
    `MODEL_STAT_WORDS`, `read_by_the_model`, `content_words`,
    `unread_words`, `without_word`). A word the model's reply accounts for
    - a word of a name it copied or the reading settled on, a word of the
    stat it picked - is no content word, as the ledger counts: measured,
    the claims do not make that exclusion redundant (194 of the 1,201 such
    words on the 628 are claimed by no rule, a tagger reading the same
    value from the model's key without them), so the Route carries the
    model's reply (`Route.model_names`, `model_stat`). Recorded beside the
    reading (`unread`); never a refusal ("Unclaimed words go to the trace,
    are measured, and only then considered").
  - **A claim says what a rule's reading depends on, measured by the rule
    itself** - the property `Reading.unread` needs to agree with the
    ledger's deletion. Step 2's claims were the characters each rule
    consumed, and disagreed with the deletion on 485 content words of the
    628 (76 claimed and not read, 409 read and not claimed). A tagger's
    claims are cut to the words it could not do without (`span.needed`:
    each word deleted, the ledger's own probe, and the tagger asked again
    over the context its own words give it), with the words outside them
    its reading turns on claimed under the family's name (`outside=`), but
    never a word whose presence made it read LESS (`gives_up=`, a suppressed
    reading's words stay unread: "2024 and 2025" read as no season). A rule
    over the whole question - the parent grammar, the child grammars, the
    stages before the taggers, the point reader's word tables, the
    refusals' recognizers - claims the words its decision turned on
    (`span.read_by`; `what` `intent`, `point`, `refused`), since a row
    written as lookaheads consumes no stretch of characters. The subject
    reading is read once and never probed: it declares its claims where it
    reads. Agreement with the ledger on the 628: 284 before, 624 after.
  - `Claim.what` gains `intent`, `point`, `refused`, `subject`, and each
    family's name for a word claimed outside its own claims (`window`,
    `span`, `measure`, `line`, `cuts`, `period`, `companion`).
  - The ledger is the check from outside, as the roadmap's step 3 row
    says: its deletion probe stays the measure, `run` records the Reading's
    list beside its own, `report` names every question they differ on and
    exits 1.

- **Declared in code by Phase 3, step 4, slice (a), the intent leaves the
  reader** (2026-10-10):
  - What the words ask is a `PointShape` (`Reading.asked`), named by the
    three choosers - the parent grammar by the subject's kind, the child
    grammars, the stages before the taggers - one key per retired intent,
    the intent's own default point (`reading.PLAYER_LOG`, `PLAYER_LINE`,
    ... `TEAM_COACH`; `None` is no shape, the retired `other`). Not the
    planned key: measured on the 2,710 readings (`intent_family.py`), an
    intent maps to up to four planned keys, the relation and sometimes
    the shape following the typed filters (`player_stat` 405 on the
    season line, 262 on the games, 15 as rows by date), and two planned
    keys are reached by two or three intents. So the grammar's key says
    what is ASKED and the point reader moves it to what the point IS.
  - The subject's kind is not in the key. The grammar is keyed BY the kind
    (the subject reading reads it first); a key with the kind in it would
    make a player's log and a team's two keys for one row of the grammar,
    and every consumer reads the kind from the subject already.
  - The page's label is ONE table, keyed by the shape (`reading.SHAPE_NAMES`,
    the asked key for the label and the planned key for a decline's
    name), read through `asked_label`; `Reading.intent` is the label,
    filled from the key and held to it. Keyed by the PLANNED key it could
    not reproduce `Answer.intent` (the 19 readings above, and the 118
    with no planned key), so the label is keyed by what was asked.
  - The coach is the key `(team_seasons, scalar, coach)`: a coach is a fact
    of a team's season, and the warehouse holds no column for it - the
    point reader refuses the key by that cause (`no_coach_table`).

## What must be decided before which phase

| Phase | Needs from this document |
| --- | --- |
| Phase 0, the last item (a kind on every remark where it is written) | nothing more: decided |
| Phase 1 (the read stage becomes a stage) | nothing. It keeps today's `Reading` and `Scope` and is proven by identical snapshots |
| Phase 2, before its first slice | "Still open" 1 to 4: parts, `record_when`, `period`, the default line |
| Phase 2, slice (iv), team shapes | "Still open" 5: `team_snapshots` |
| Phase 2, slice (v), charts | "Still open" 6: two charts, not four |
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
the next two notes, and the last is divided: `minimum` and the cut are decisions, `still_open` a note.

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
| `Measure(key, how, side)` | no per-100, percentile or per-N-minutes; no opponent's figure; no NetPoints category; six vocabularies, not four | `whose`, `category`, more `how`, `Unsupported` - settled in code by Phase 3, step 2's measure slice: `whose` and `category` as drafted, `how` as `per_game`/`total`/`per_100`/`per_90` (no `count`, `rate` or `percentile`: the count is the shape's), the `Unsupported` unit as `Measure.unit`, the words kept beside a `how` of None |
| `Window(order, count)`, no minimum | nowhere for `ranked_by` or a stated qualifier | `Window.by`, `Minimum` |
| `Calendar` for every `situation` | date cuts and conferences are not calendar values | `DateRange`, `Opponent(alignment)` |
| the 25-intent table | six rows wrong, among them `team_record` (five forms), `period_split` (never grouped), `player_netpoints` (not a chart) | corrected, with the counts |

## Still open

1. **Parts.** An answer is a tuple of typed parts, the first the
   headline. The alternative is several Results per answer, which
   duplicates the subject and span and splits contract 5's bookkeeping.
2. **`record_when` is a split** by `line` with a total row, in 22 of 24;
   the other two are counts. One question family, two shapes: the reader
   decides from the words.
3. **`period` has three roles**: what a read sees (its own field -
   `reading.Period` on `Scope.period` since Phase 3, step 2, the period
   slice above), a filter (a `Line` with a period - `Line.period` on
   `Scope.lines` since the line slice, the `period_line` cell), and a
   dimension (a column per quarter - the point's `by="period"`). The catalog saying which measures
   have a reading in a quarter is still the measure step's. Open from
   the period slice: a period named on a reader that takes none (six of
   the 2,710 readings, each with a subject nothing resolved) carries no
   value, as the stages wrote none, and the point reader's guard declines
   it ("a quarter or half is the period relation's question"); the typed
   design has the tagger write it and the planner refuse the cell - a
   decline-to-Cause commit with those six readings enumerated.
4. **The default line** per relation and shape is a declared table. It is
   where the old intents' habits survive; proposed: accept that, keep it
   small, and review its rows as each slice lands.
5. **`team_outlook`** reads ESPN's power-index snapshots: per date and
   per kind, with provenance that matters (a snapshot stamped after its
   season). Proposed: a declared relation, `team_snapshots`, not columns
   on team_seasons.
6. **Charts are two, not four.** Only `shot_chart` and `fingerprint` draw
   an artifact. `shot_distance` is a scalar on `shots` and
   `player_netpoints` a scalar and a split on `netpoints`. `ROADMAP.md`
   says "the four chart answers keep their own readers and renderers";
   proposed: that still holds for their READERS (the relations are
   declared, not ported), while their answers use the common shapes.
7. **`Refusal.shown`/`under` and the per-shape `Facts` records** (declared
   2026-10-05, "the Result is typed"): both exist to keep today's page
   data identical. Proposed: Phase 4's renderers take the body, the cells
   and the cause, and both go - a renderer that needs a value a record
   holds is the signal that the value belongs on the body.
8. **The regular season named outright.** `Span.stated` cannot tell "regular
   season" said from the default, since every reader reads 2 either way;
   the tagger claims the words. A planner that notes a defaulted season
   type (the open season-type default in `ROADMAP.md`) needs a bit the
   tagger has and the value does not carry; add it when that note is
   written, not before.
9. **A bare count on the games relations is a default, not a cell.** "top
   5" and "last 10 home games" on a reader with no window of its own both
   reach the relation as a count with no end, which `relation_window` reads
   as the newest N - a default nothing says out loud, and one a reader
   that refuses an end never sees refused: "Warriors vs Mavs record last
   ten games" is answered over this season's three meetings, the end
   dropped before the planner could refuse it (ISSUES.md, "A window on a
   reader that refuses one..."), and "bottom 5" is read as a count alone,
   the grammar's `(top|bottom) N` row naming no end. The typed value holds
   the fact (`Window.order` is `None`); the planner's note of a defaulted
   end, the refusal of a dropped one, and a `bottom` end for a ranking are
   decisions for the step that types the `Measure` and the ranking's
   direction, with every moved answer named.
10. **A situation nothing reads is a cell the relation refuses, not the
    planner.** `Situation.read` is False on 33 of the 2,710 readings (an
    age, overtime, the All-Star break, "since returning", a bare
    "division"), and each still reaches RUN, where the relation refuses it
    by value with the sentence it always had; `Reading.unsupported`'s
    `non_calendar_situation` is said only where the answer side declined.
    Three cells (`calendar`, `alignment`, and the unread words as the
    draft's `Unsupported`) with the planner refusing the third is the
    typed shape, and it rewords those 33 answers - a decline-to-Cause
    commit of its own, after this slice. The same for a worded since-date
    with a year: "since january 31st 2020" is read as the 2020 season cut
    at January 31 (the span's year and the calendar's day, separately),
    where "since 1/31/2020" is every game from that date on across seasons
    (`since_date`); the typed value records what was read, and the
    decision which the words mean is open (ISSUES.md, "A worded since-date
    with a year...").
11. **The team relation declares no `companion` cell, and the companions it
    honors are its shapes' rows'.** Four readers on `team_games` take a
    companion beyond the team relation's table (`ShapeCells.taken`, since
    step 2's closing slice): the with/without split divides by them, and
    a team's log, splits and record over a line refuse them in their own
    words (a teammate's absence is with_without's question; a role is a
    named player's) - where the PLAYER relation's rows declared them and
    `compose.plan._TEAM_READER_REFUSES` let them through until then. The
    typed design has the cell on the team relation's table with a reasoned
    exclusion per team reader; that moves no answer either, and is left
    for the decline-to-Cause commit that rewords the team readers' own
    refusals. Measured on the way: a teammate who PLAYED and an ordinal
    season the planner lets through to a team's log are not read
    (`compose.logs.read_team_log` refuses an absence, a line and a series
    game, and narrows by the venue, the opponent and the date alone, so a
    situation is dropped too) - 0 of the 2,710 readings carry one, and the
    wordings tried ("celtics game log when jayson tatum plays") read the
    with/without split (ISSUES.md #322).
12. **`Won` and `Met` are not filters.** The draft lists both under
    `Filter`; in the code a team's run of wins is the point's `won`
    predicate, and two players' meetings are the pair shape itself
    (`Reading.by="met"`, the pair relation's own read) - neither is a cell
    a relation table declares or a reader declines. `Won` is typed with the
    `Measure` since the measure slice (`Measure.won`, the `kind` slot: a
    run's result, read from the words beside whatever stat is named);
    `Met` stays the shape's `by`.
13. **The point's `measures` and `aggregate` are the compiler's names.**
    `Reading.measures` is a list of the compiler's column and derived
    names and `aggregate` one of five, derived from `Scope.measure` through
    the catalog at 54 construction sites in `point.py`; the draft's
    `Query.measures: tuple[Measure, ...]` with `how` as the aggregate is
    RUN's input, and retyping it means the compiler (`compose.core`,
    `compose.team`, 1,345 and 846 lines) reads `Measure` values. Proposed:
    with step 4, when the point reader is re-keyed on the grammar and the
    `by` tables go.
14. **A position group beside a named player is read and then dropped.**
    "centers vs gobert gamelog with 34 minutes 2024" reads a `player`
    subject (Gobert) carrying the position group C, and the point reader
    honors a position on the league's moves alone, so the answer is
    Gobert's own log - the question is the centers' games against him (1 of
    the 2,710 readings). The typed design reads it as the position group's
    games with Gobert an opponent-side companion; the planner refusing a
    position on a point that takes none is the decline-to-Cause shape.
    ISSUES.md.
15. **The stages still settle three names.** The grammar's subject of a
    count or a high, a team's quarter from its one nickname and a quarter's
    player read back are written on the typed subject by the stages, under
    the intent they choose, from the reading's own words (`router.Named`).
    Step 4, which keys the readers on the grammar and deletes the stages,
    moves the three to the subject reading's settling under the intent.

16. **The subject reading's claims are declared, not measured** (Phase 3,
    step 3). Every other rule's claims are cut to what its reading depends
    on by asking it again with each word deleted; the subject reading is
    read once (the read-once rule, `test_the_subject_is_read_once_per_question`)
    and is not asked again, so its claims are where it reads - and 3 of the
    628 recorded questions disagree with the ledger for it (4 until the
    step's last commit): "head" in
    "lebron vs kawhi head to head" (the spelling reader gives up on
    "kawhi" with it there - a suppressor, unclaimed), "against" in "How
    many points did the 76ers score in the 4th quarter against Boston this
    season?" and "This season, how many times did the 76ers play against
    the Celtics?" (claimed with the opponent, which the second team named
    gives anyway), and until the step's last commit "against" in
    "evaluate sga against embiid" (read only through a spelling the window
    took from the next name, fixed there, which grows the ledger by that
    word). Measured at
    the step's claims commit: with the subject reading asked again per
    content word (settled under the route's intent, a suppressor left
    unclaimed), the 628 disagreed on 1 question and the 2,710 on 23,
    against 5 and 100 with its claims declared, at about 2.5 ms a question
    more - and a second reading of the subject per word is what the
    read-once rule forbids. Jeff's: keep the rule and the residue, or allow the
    subject reading to be asked again for its claims alone.
17. **A partial overlap of two claims joins them, named for both** (since
    `a29c7fd`; the span slice's decision was that it fails the reader).
    Measured on the 2,710 at the step's end: 48 joined claims, where step 2
    closed with 4. 43 are a child grammar's words (claimed whole, since
    the decision says them) over a tagger's - `intent+line` 17 ("how many
    games did luka have with 20+ points and 5+ assists"),
    `intent+measure` 11, `intent+opponent` 5, `window+intent` 3,
    `companion+intent` 2, `opponent+intent` 2, `intent+companion` 1,
    `situation+intent` 1, `opponent+intent+window` 1: two readings that
    both need the word - the grammar names the intent from the line's
    words and the tagger reads its value - which step 4's grammar, naming
    the shape from the typed values, is to make one. 1 is a recognizer
    beside the measure (`refused+measure`, "bench points allowed"): both
    need it. 4 are two rules of ONE tagger reading one word
    (`situation+date`, "in march 24 2018": the cuts tagger reads the day
    and the month in it again as a situation, applied twice - ISSUES.md,
    "A month is read from the words of a day in it", P4) - the double read
    the join hides.
    Failing the reader on a partial overlap would now fail 48 readings, 44
    of them two readings each right. **Jeff's ruling (2026-10-10): the
    join stays**, and the joins are held by kind and count
    (`claims_ledger.py`'s `HELD_JOINS`, the 628's: `intent+line` 12,
    `intent+opponent` 4, `companion+intent` 2, `intent+companion` 1,
    `situation+intent` 1, `refused+measure` 1; the report fails on a new
    kind or a grown count). Step 4 is held to zero joins but #354's four.
    Measured at step 4's slice (a), which re-keyed the grammar and left
    every claim where it was (the 43 unchanged): the intent joins come
    from all three choosers - the parent grammar alone 10 (`intent+opponent`
    5 by `_PLAYER_LOG`'s "games against", `window+intent` 3,
    `intent+companion` 1, `situation+intent` 1), a child grammar with the
    parent 17 (`intent+measure` 11, `intent+line` 6), a child grammar
    alone or with the stages 2 (`intent+line`), all three 8
    (`intent+line`), the stages with the parent 5 (`companion+intent` 2,
    `opponent+intent` 2, `opponent+intent+window` 1), the stages alone 1
    (`intent+line`) (`~/association-research/stages/intent_joins_src.py`) - and 13 of the
    17 `intent+line` joins are a line the lines tagger reads only BECAUSE
    of the key (`line.THRESHOLD_ASKS`: a bare "30 points" is a line on a
    count, nothing on a log; `intent_joins_dep.py`). A grammar naming the
    shape from the tagger's typed value cannot come first while the
    tagger reads by the grammar's key: the key-dependent rules move out of
    the taggers (to the planner or the point reader) before the grammar can
    read their values. That is the stages' deletion (slice (b)), where
    `_settle`'s order is undone anyway.
