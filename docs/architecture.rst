Architecture
============

Three independent stages, each usable on its own: **fetch** writes Parquet,
**load** builds a DuckDB warehouse from it, and **query** answers questions
against that warehouse. Nothing later in the chain needs the network.

.. code-block:: text

   ESPN endpoints ──► fetch/ ──► data/parquet/ ──► load ──► nba.duckdb
                                                                │
   question ──► READ ──► PLAN ──► RUN ◄─────────────────────────┘
                 │         │        │
                 │         │        └──► SAY ──► answer
                 │         │
                 └─────────┴──► a refusal naming why

Fetch
-----

:mod:`association.fetch.pipeline` drives the whole pull.
:class:`association.fetch.client.ESPNClient` handles transport — rate limiting,
retries, and TLS fingerprinting via ``curl_cffi``, because ESPN's CDN rejects
plain ``requests``/``httpx``. :mod:`association.fetch.parse` flattens each
JSON response into tabular rows, and :mod:`association.fetch.storage` writes
Parquet plus completion markers.

Those markers are what make a pull **resumable**: a season/season-type marked
complete is skipped entirely on the next run, so re-running is cheap and
interrupting is safe. ``--force`` overrides that, deliberately.

Load
----

:mod:`association.fetch.warehouse` builds ``nba.duckdb`` from whatever Parquet
is on disk — no network. Loading each table is one ``CREATE OR REPLACE TABLE
... read_parquet(...)`` statement; after all of them come the load-time
repairs and views, in dependency order, every one of which also reruns on a
partial ``association data load --tables ...``:

* :mod:`association.fetch.repairs.game_repair` puts the teams of a game ESPN serves on
  the wrong sides back where they played (one game so far: 1990 Finals Game
  5). Runs ahead of ``real_games``, which copies ``games``, and of every view
  that reads a winner or a side.
* :mod:`association.fetch.repairs.team_box_repair` rewrites ``team_box_stats`` in
  place, correcting three column-level faults ESPN serves (2018's stats
  shifted under a neighboring column's name, ``turnovers`` zero before 2013,
  2008's rebound columns holding something else).
* :mod:`association.fetch.repairs.season_totals_repair` rewrites ``player_season_stats``
  in place, rebuilding a traded player's combined-season row from his own
  stints where ESPN's career endpoint disagrees with itself.
* :mod:`association.fetch.advanced_stats` builds the computed
  ``player_advanced_stats`` / ``player_season_advanced_stats`` views (true
  shooting %, eFG%, usage rate, game score) — pure closed-form formulas over
  already-fetched columns, so they are always rebuilt when ``player_box_stats``
  is present rather than gated behind a flag.
* :mod:`association.fetch.repairs.reconstructed_box` builds
  ``player_box_stats_reconstructed`` and ``player_box_stats_filled``, rebuilding
  a per-game box line from ``plays`` for the roughly 1,025 Chicago/New Orleans
  games (2013-2018) ESPN serves with every stat zero. Deliberately its own
  views rather than a rewrite of ``player_box_stats`` itself, and deliberately
  absent from :data:`association.nba.coverage.KNOWN_TABLES` — a reconstructed
  number sitting in the same column as a fetched one would be indistinguishable
  from it.
* :mod:`association.fetch.repairs.real_games` builds the ``real_games`` table: the rows
  of ``games`` that are actually games, with ESPN's placeholder, duplicate and
  phantom rows read past once rather than re-filtered by every read.

The same pass also creates the ``current_season()`` macro and the
``player_season_stats_deduped`` view that collapses a traded player's multiple
rows and drops the postseason lines ESPN's career endpoint copied from a
regular season.

Rebuilding is idempotent and cheap, so it is the right loop when iterating on
schema or parsing. A **full** rebuild (``tables=None``, i.e. no ``--tables``
flag) builds into ``<db_path>.building`` and only renames it over ``db_path``
once every load, repair and view finishes without raising, so an interrupted
build — OOM kill or otherwise — leaves the previous warehouse untouched rather
than half-replaced; a leftover ``.building`` file is itself evidence of one and
is replaced by the next full build. It also runs with
``preserve_insertion_order=false`` and ``enable_external_file_cache=false``
(see ``AGENTS.md``, "Working on the fetch path", for the two out-of-memory
failures each setting fixes). A **partial** rebuild (``--tables``) writes
``db_path`` in place, since it depends on tables already there that it is not
reloading.

Query: four stages
------------------

A question is answered in four stages, each with one input and one output,
and each the only place its kind of work is done:

.. code-block:: text

   question, the model's names and stat
      │  READ   the only code that sees the question's text; no statement
      │         but the active players' read, for a typo near six names
      ▼
   Reading      who it is about, what the words ask, ten typed filters,
      │         the point, the words nothing read
      │  PLAN   checks the point's cells against its relation's table and
      ▼         picks the relation - or comes to why there is none
   Verdict      the Query-or-Cause: a planned query, a refusal, or a decline
      │  RUN    the only code that builds or executes SQL
      ▼
   Outcome      a Result (a typed body, the cells applied, the notes),
      │         a Refusal or a Clarify - never a sentence
      │  SAY    takes the Outcome and nothing else; one phrase per kind
      ▼
   Answer       the text, the planned shape, the data the page draws

:meth:`association.query.agent.Agent.ask` is the loop that runs them, once
each per question. It asks the model once (READ's first step), reads the
players' and teams' names into memory once, before the parser runs
(:func:`association.query.entities.names_of`, inside
:func:`association.query.names.loaded`), plans once
(:func:`association.query.compose.plan.plan_point`) and hands the plan to
the answer side (:func:`association.query.compose.answer`). Measured over
the 628 recorded questions: the subject is read once on every
question the parser reads, the grammar's rules run once (twice on 4, each a
child the rules declined), the planner runs once, and the reader issues no
statement but on 2 questions, where a typo near more than five players is
narrowed by who played in the latest season.

The design rests on one measurement. A model asked to do two jobs at once -
understand the question *and* write correct SQL for it - needs the entire
schema and every correctness rule resident for every question: **10,295
tokens against a context window of 8,192**, which ollama truncates
head-first and silently, so what the model loses is the schema and the
correctness rules. So understanding the question is code - grammar tables
over its words and typed readers - and SQL is compiled, never written by a
model ("Refusals", below). What is left for a model is the smallest job
there is.

READ: the model, then the words
-------------------------------

**The model** copies the names out of the question exactly as typed and
picks one stat key - :func:`association.query.normalizer.normalize`, under a
two-field JSON schema passed as ollama's ``format``, so decoding is
*constrained* rather than merely prompted. The prompt is about 330 tokens
against a 2,048-token window, carries no schema and no SQL, and costs one
call of about a second. Both fields are required, because a slot the schema
does not require is one the decoder may never fill; and a required one is
filled whether or not the question asked for it, so the parser treats the
stat as context and reads the words over it. Nothing the model returns is
trusted: a name it supplies may be fiction ("compare sga and embiid" once
came back with Jusuf Nurkic, a real player the question never named), and
every name is checked against the question before anything reads it.

A question of fewer than three words is refused before the model is asked
(:func:`association.query.parse.too_short`): most such rows are a question
not yet typed, and guessing at "Tatum rec" answered something else.

**The parser** reads everything else from the words
(:func:`association.query.parse.read_route`) into one
:class:`~association.query.reading.Reading`, in this order:

1. **Who the question is about is read once**
   (:func:`association.query.subject.read_subject`): a player, two or more,
   a team, two teams, a position group, or everyone, from the question's own
   spans, with the model's names as a hint. A name counts only where the
   question supports it - the word itself, a near spelling, a nickname or
   the initials (:func:`~association.query.subject.question_supports`) - and
   a span names a player only where it equals a whole word of exactly one
   player's name (:func:`~association.query.subject.players_named_in`), so
   "the highest scoring game" names no Blossomgame. The same reading reads
   who stands beside the subject - "without X", "when X and Y play", "when X
   scores 20+", "vs X" - as typed companions, each with its side and role.
   Names are recognized from an in-memory index of the ``players`` and
   ``teams`` tables (:class:`association.query.names.NameIndex`), never by a
   statement; a single near spelling reads as that player and says so
   (:func:`association.query.entities.read_near_spelling`).
2. **Two phrases are read first and blanked**: a line in a quarter that
   conditions which games count ("after making one three in the first
   quarter"), and a ranking's stated qualifier ("minimum 100 games played"),
   each written over by spaces of equal length so every later claim keeps
   its place.
3. **What the words ask is named by a grammar, keyed on the subject's
   kind.** The parent grammar (:data:`association.query.parse.PARENT_GRAMMAR`)
   is a table of rows, each a predicate over the grammar's text
   (:class:`association.query.lexicon.GrammarText`: the question's words,
   with what a tagger typed at each character - a line's number, the
   opponent, a companion's phrase, a window of games, a dated range - held
   apart from the words as a closed kind). A row reads "a window of games
   stands here" by value, never from the words the window was read from. A
   child grammar then narrows the parent where the words and the kind name
   one (:func:`association.query.subject.child_named`: a count of 30+ point
   games under a game log, a history over the past four seasons under a
   player's line), and the grammar's rules
   (:func:`association.query.grammar.settle`) settle the key - a coach, a
   quarter or a half, a team where a player ranking was named, a line with
   no number. What the words ask is a
   :class:`~association.query.reading.PointShape` - a relation, a shape and
   a ``by``, one of the 28 the grammar can name (``Reading.asked``) - or no
   shape at all.
4. **Each filter family is read by one tagger, in the order each reads what
   the ones before it settled**: the split (:mod:`~association.query.split`),
   the lines on a stat (:mod:`~association.query.line`), the measure
   (:mod:`~association.query.measure`, which is also the one catalog of what
   a question can ask about), the period (:mod:`~association.query.period`),
   the games' cuts (:mod:`~association.query.cuts`), the window
   (:mod:`~association.query.window`) and the span
   (:mod:`~association.query.span`) - last, because three of its rules read
   the typed window. A tagger reads the words alone and returns every
   candidate they hold; ONE function per family in
   :mod:`association.query.point` keeps the candidates the key reads
   (``lines_under``, ``measure_under``, ``period_under``, ``cuts_under``,
   ``window_under``, ``span_under``): a bare "30 points" is a count's own
   line, and nothing on a log. Every pattern a family is read by is a named
   constant in :mod:`association.query.lexicon`, with its reason beside it;
   no other module of the query package calls ``re``.
5. **The point reader** (:func:`association.query.point.read_point`) reads
   the point the answer is computed from onto the Reading itself - one
   Reading per question, its verdict one field,
   ``Reading.point_verdict``: the key's default point, moved by
   what the words say (a measure, a skeleton), declaring the
   shape it is read and said as, one field, ``Reading.read_as`` (a
   :class:`~association.query.reading.PointShape`: its relation, one of
   nine, its shape, one of seven - ``scalar``, ``rows``, ``ranking``,
   ``comparison``, ``split``, ``runs``, ``chart`` - and its ``by``). Where
   the words come to a refusal instead - a ranking by a stat nothing ranks,
   a matchup without two players, a quarter's figure the plays do not
   rebuild - it carries a :class:`~association.query.reading.Cause` (a kind
   from a closed set, :data:`~association.query.reading.CAUSES`, and plain
   facts), never a sentence.
6. **The parser's last step** writes the Reading: the subject reading's
   names onto the typed subject (:func:`association.query.subject.apply_subject`,
   their one writer), the claims, and the content words nothing read.

**The Reading** is what every stage after the parser answers from, and it
holds no question text. Its ten filters are typed values
(:class:`association.query.reading.Filtered`, the base every planned query
shares too): ``who`` (the typed subject), ``cuts`` (which games: an
opponent, a venue, a date, a calendar or alignment situation, a game of a
series, an ordinal season), ``companions``, ``lines``, ``measure``,
``span`` (a season, a career, a range, both season types), ``split``,
``period`` (a quarter or a half), ``window`` (an end of the span and a
count) and ``minimum`` (a ranking's stated qualifier). Beside them: the key
the words ask; the point's own fields and the point reader's verdict
(``point_verdict``: read, declined with why, or its cause); the causes the
words came to (``refused``, before any point; ``unsupported``, what the words name that
nothing reads); the decisions made on the way; the names the question never
supported (``misread``); and ``left_out``, the names a "vs" fingerprint held
that no polygon drew.

**Each word is read once, and a word nothing read is recorded.** Every rule
claims the characters it read (:class:`~association.query.reading.Claim`),
cut to the words its reading depends on - each word deleted in turn and the
rule asked again (:func:`association.query.span.needed`) - so "games" in
"last 10 games" is claimed by no rule. Two claims that overlap without one
holding the other are joined into one named for both
(:func:`association.query.span.claimed`), and every join is a finding the
claims ledger names (there are none on either population). The content
words no claim covers are ``Reading.unread``, counted by the rule the claims
ledger measures with from outside the reader
(:func:`association.query.lexicon.content_words`): 681 unread content
words over the 628 recorded questions (``scripts/ledger_held.json``), most of them words that restate the
shape ("average", "record", "stats").

PLAN: one check, one value
--------------------------

:func:`association.query.compose.plan.plan_point` plans the Reading once and
returns the Query-or-Cause, one value
(:data:`association.query.compose.plan.Verdict`):

* a **planned query** on its relation - the player compiler's
  :class:`~association.query.compose.core.Query` (a player's or the league's
  games, or the season line), the team compiler's
  :class:`~association.query.compose.team.TeamQuery`, a team's own season
  (:class:`~association.query.compose.team_stats.TeamSeasonQuery`), or a
  declared relation's (:class:`~association.query.compose.netpoints.NetPointsQuery`,
  :class:`~association.query.compose.shots.ShotQuery`) - each holding the
  Reading's ten filters as its own, with the compiler's measure names built
  once from the point's typed measures where it is handed over
  (:func:`~association.query.compose.core.measure_names`), and the
  aggregate the planner's own since decision D5, derived from what the
  point is read as and its measure
  (:func:`~association.query.compose.plan.aggregate_of`);
* a **refusal**, unworded (a :class:`~association.query.result.Refusal`:
  the words' own cause, the point reader's, a floor no ranking applies);
* or a **decline**, with its reason (an
  :class:`~association.query.reading.Unsupported`; a
  :class:`~association.query.reading.ShapeDeclined` where a shape declines by
  a cause the planner says - ``cannot_honor``, ``no_reading_of_point``).

The shape the answer is read and said as is the planned
:class:`~association.query.reading.PointShape`
(:func:`~association.query.compose.plan.planned_shape`): the point's own
``Reading.read_as``, with the planner's one move - a season-line
point its reader does not read is planned at the game level, on
``player_games``. The answer side chooses a reader, the coverage floor and
the cells a shape honors by this key and by nothing else.

**One table of cells per relation is the planner's allow list.** A cell is
one narrowing a relation can apply - an opponent, a venue, a date, a window,
a period, a line, a companion, a span cut: the 21 of
:attr:`Filtered.CELLS <association.query.reading.Filtered.CELLS>`. Each
relation declares the cells it narrows by once
(:data:`association.query.player_relation.RELATION_SCOPING`,
:data:`association.query.team_relation.TEAM_RELATION_SCOPING`), and each
shape a reader reads has a row beside it
(:data:`~association.query.player_relation.RELATION_SCOPING_EXCLUDED`,
:data:`~association.query.team_relation.TEAM_RELATION_SCOPING_EXCLUDED`: a
:class:`~association.query.reading.ShapeCells` keyed by the ``PointShape``)
- the cells its words do not state, each with a reason about the answer
("one game is not a run"), never about the code; the ones it refuses
outright; what it takes beyond the relation's table. The planner's one check
reads the point's typed cells against the row
(:func:`~association.query.compose.plan.cells_unhonored`), and
:func:`~association.query.compose.plan.cells_declined` refuses, before any
reader runs, what a shape refuses outright. A reader declares and applies no
scoping of its own - two tests read the source to keep it so - which makes a
new narrowing one clause on the relation's shared steps, never a cell
taught to one reader at a time, which leaves twelve honored on one reader
and one on another over the same relation.

**A season a table cannot reach is refused, with the reason.**
:func:`association.query.coverage.check_coverage` reads the planned point's
tables (:data:`association.query.coverage.SOURCES`, keyed by the
``PointShape``) against the floors in :mod:`association.nba.coverage`. A
lookup and a ranking have different floors - ``player_season_stats`` holds
Michael Jordan's real 1990 line but only 217 players of that season, so his
own average is answerable and a ranking of the season is not - and a
missing season and an unrepresentative one are different sentences. The
refusal is returned as the answer, never raised: a season under the floor is
empty for every reader, and nothing does better. A season that exists only
in part (2002's play-by-play is about half a year) is answered with a
caveat instead (:func:`~association.query.coverage.coverage_caveat`).

RUN: the relations, and the compiler's one door
-----------------------------------------------

:func:`association.query.compose.answer` takes the Reading and the Verdict,
and never reads the question. A refusal is said; a decline is a cause the
planner says in the decline's own sentence. A planned query is read by its
shape's reader, chosen from one table keyed by the ``PointShape`` (36 keys):
``compose.logs``, ``compose.stats``, ``compose.counts``, ``compose.highs``,
``compose.runs``, ``compose.pairs``, ``compose.seasons``,
``compose.rankings``, ``compose.presence`` and the rest. A reader returns a
``Result``, a ``Refusal`` or a ``Clarify`` (:mod:`association.query.result`),
never a sentence. A reader that is not its point's only answer steps aside
for a cell its words do not state, and the compiler's own SQL
(:func:`association.query.compose.core.run`, or the team compiler's,
:func:`association.query.compose.team.run_team`) and sentence
(:mod:`association.query.compose.sentence`), which state every narrowing the
relation applied, answer instead; a reader that is its point's only answer (a
team's own season, a chart) declines rather than answer for something
else.

**Every statement is a relation's.** The relations are the modules that own
a table and the rules of reading it:

* the **player-games relation** - :mod:`association.query.player_games` and
  its shared steps, :mod:`association.query.player_relation`: one row per
  player per game played, with the season-keyed join, the phantom season,
  the played-game guard, the rebuilt-line rule, the starter/bench category
  and a quarter's line rebuilt from the shots and plays, each defined once.
  A reader settles its subject through
  :func:`~association.query.player_relation.scoped_player` and its games
  through :func:`~association.query.player_relation.scoped_games`, and every
  narrowing - an opponent, a venue, a teammate's absence, a line, one
  Eastern date, a span (:func:`~association.query.player_relation.span_of`),
  and the window, cut after every other filter - is applied there, once.
  What a companion needs read (who played, who started, a teammate's stints
  on the roster) is :mod:`association.query.conditions`;
* the **team-games relation** - :mod:`association.query.team_games` and
  :mod:`association.query.team_relation`
  (:func:`~association.query.team_relation.scoped_team`,
  :func:`~association.query.team_relation.team_games`), which is where a
  postseason is selected by the calendar year it was played in and the NBA
  Cup final is kept out of a regular-season record - facts written once,
  because copies of them drift apart;
* the **season line** (:mod:`association.query.season_line`): one row per
  player per season in ``player_season_stats_deduped``, the advanced stats
  beside it, the rankings' floors, dedup and qualifiers, and the
  defaulted-season redirect;
* a **team's seasons** (:mod:`association.query.team_seasons`) and the
  **standings** (:mod:`association.query.standings`);
* the declared relations, **NetPoints** (:mod:`association.query.netpoints`)
  and **located shots** (:mod:`association.query.shots`), whose answers are
  charts drawn by :mod:`association.query.radar` and
  :mod:`association.query.court`;
* and the name index's two loads (:mod:`association.query.entities`) and the
  warehouse's latest season (:mod:`association.query.connection`).

Those twelve modules hold every statement the package runs - 77 by
``scripts/check_ratchets.py``'s count. ``compose/`` executes only what the
compiler compiles or a relation hands it, through one door
(``compose.core.rows_of`` and ``values_of``). The compiler's skeletons are
``rows``, ``scalar`` (one aggregate, or a player's whole line beside the
sums it is said from), ``grouped`` (a ranking, a comparison, a split),
``run`` (the longest runs of consecutive games one predicate holds along, in
Eastern-date order) and ``pair`` (two named players' lines over the games
they met in); the team compiler's add a team's ``presence`` split, its games
inside named teammates' time on the team divided by whether they played.

Every correctness rule is in code rather than in prose: season defaults
(the latest season with games on record, not the calendar's), traded-player
dedup, minimum-sample floors, the home/away perspective flip, the string
``season_type`` NetPoints uses, the season each table's data starts in
(:mod:`association.nba.coverage`), and dating a game by its US Eastern day
rather than ESPN's UTC timestamp (:func:`association.nba.season.eastern_date`).

**A Result is typed.** Its body is one of a few shapes - ``Rows``,
``Scalar``, ``Grouped``, ``Runs``, ``Chart`` - on a ``Span`` and a
``Narrowing`` whose cells are typed values (a ``Period``, a ``Line``, a
``Role``, a ``Met``, ...); what a shape's sayer needs beside its body is one
frozen record per shape (``CountFacts``, ``LogFacts``, ...); a remark is a
``Note`` of a kind and its facts; a read that found nothing keeps its body's
shape and gives its reason as ``Result.empty``. A refusal found at RUN is a
``Refusal`` whose kind is in :data:`association.query.result.RUN_CAUSES` (a
fact found in the warehouse), disjoint from the reading's
:data:`~association.query.reading.CAUSES` (a fact read from the words).

SAY: one phrase per kind
------------------------

:func:`association.query.compose.say.say` takes an Outcome and nothing else
- no connection, no relation, no question - and chooses the sayer from the
headline body's type and the one field that says what it is (``Scalar.how``,
``Grouped.by``, ``Rows.by``, ``Chart.kind``). Each note kind, decision kind
and refusal cause has ONE phrase there
(:func:`~association.query.compose.say.note_phrase`,
:func:`~association.query.compose.say.decision_phrase`,
:func:`~association.query.compose.say.refusal_phrase`), so rewording one is
editing one line. A decline is said in the shape's words, one phrase per key
the grammar names ("Nothing here answers this question as a ranking of
teams: ...", :func:`~association.query.compose.say.nothing_answers`).

Every caveat, stated default and definition is written through
:mod:`association.query.notes` - :func:`~association.query.notes.note` for
something about the data, :func:`~association.query.notes.decided` for
something the question left open and the system chose - each a kind from a
closed set with plain facts, recorded on the answer beside the sentence it
stays in. A remark that was written and did not reach its answer is listed
(:func:`association.query.notes.unsaid`), which is what stops a reworded
answer from losing a caveat while every number still matches.

The answer
----------

:meth:`association.query.agent.Agent.ask` returns an
:class:`association.query.answer.Answer`, not a string. ``answer.text`` is
what the CLI prints; everything beside it is what the pipeline computed:

* ``answered_by`` - ``"fast"`` where something here read the question and
  had something to say (an answer, a clarification, a "no match", a refusal
  a reader or the planner came to by its cause - every decline too, and
  words that ask for no shape), ``"refused"`` only for a question too
  short to be one (fewer than three words), refused unread, a reply the
  normalizer could not give, or a value the Reading cannot hold.
* ``shape`` - the planned ``PointShape`` the answer was read and said as,
  or ``None`` where the words came to no point. The web page renders by it:
  seven renderers, one per shape (``RENDERERS`` in
  ``web/static/index.html``), each choosing a body by ``by``.
* ``data`` - the answer as resolved names and numbers, so a caller renders
  the result rather than parsing the sentence; ``artifacts`` - the files a
  chart wrote; ``notes`` and ``decisions`` - the remarks as kinds and facts;
  ``history_file`` and ``timing``.

The live trace is a sink rather than a print:
:class:`association.query.history.RunHistory` records every line to the
run's history file regardless, and hands it to ``sink`` — stderr by default —
when ``verbose``. Its lines name the stages: ``(normalizer)`` what the model
copied, ``(parser)`` the key the words ask and the filters as typed values,
``(decision)`` each choice the reader made, ``(reading)`` the point, and
``(compose)`` the key asked and the shape planned. Nothing in the engine
writes to a terminal on its own, which is what lets the web page stream the
same lines.

Refusals
--------

What nothing here reads is refused in the same second, naming the missing
thing: words that ask for no shape ("what its words ask has no reader"), a
narrowing the relation cannot honor, a season under a table's floor, a
reply the normalizer could not use, a name the question never supported
(refused by name, :func:`association.query.entities.misread_players`, and
only where the answer would be about that player), or - where the shape is
one the warehouse has no column for - the thing itself: the playoff rounds
around one ("before the finals"), an age, a stat by quarter the plays cannot rebuild, a coach
(``Reading.unsupported`` and the point reader's causes, said by the
planner). Before writing one, check which fact is missing: "No NetPoints
fingerprint on record for season 2026" about a player who resolved to the
wrong Maxey reads as honest while sending the reader somewhere useless.

No model is handed such a question to answer by writing SQL: a model with
nothing to read fills the silence from its own weights (measured over 24
questions, #129: one answered in 23, wrong five times in six where it
finished).

Why deterministic readers rather than better prompting
------------------------------------------------------

The case against prompt-only correctness is empirical. Asked how many times two
teams had played, the SQL-writing agent of the time wrote ``home_team_id =
'PHI'`` — team ids are opaque all-digit strings, so the filter matched nothing,
and it reported that the teams had never met. The rule against exactly that
was in its prompt, verbatim, with that exact wrong form spelled out as a
worked example.

A reader cannot make that mistake, because resolving a name to an id is code
that runs the same way every time. That is also why the agent is gone rather
than improved: every measured failure of the fast path is a missing or
too-narrow shape - a fast, fluent answer to an adjacent question - which is a
grammar row, a tagger's rule or a reader to add; and a model with nothing to
read does not fill the gap, it fills the silence.

One model
---------

The normalizer only copies names and picks one stat key, and constrained
decoding does the structural work: measured over the 277 day10 wordings,
qwen2.5:3b copies 299 of 302 names verbatim at 0.82s a question median, and
qwen2.5:7b adds nothing on names and 13 of 162 on the stat - almost all of it
the 2-point/3-point confusion, which the measure tagger reads from the words
instead - at 1.84s. So the normalizer runs a 3B (``--model``), and it is
the only model. Thinking models are disqualified on latency rather than
accuracy: measured on a structured call of the same size, qwen3:4b spends
about 20 seconds per question reasoning before emitting the same small JSON
object qwen2.5:3b produces in about one.

How the design is held
----------------------

Each contract between the stages is a gate or a test, so a change that
breaks one fails rather than drifts (``AGENTS.md``, "While the pipeline is
rebuilt", has the rules for changing them):

1. **The text stays in the reader.** No record after the Reading holds the
   question, and the answer side never reaches the model: two import-linter
   contracts in ``pyproject.toml`` ("The reader does not import the answer
   side", with no ignored edge, and "The answer side never asks the
   model"), and a ratchet on functions outside the reader that take the
   question's text (one: the history file's writer, which records it).
2. **Each word is read once.** The subject reading runs once a question (a
   test holds it), and the grammar's rules and the planner once (measured);
   every rule claims what it read; and the claims ledger
   (``scripts/claims_ledger.py``) measures from outside the reader which
   content words the reading does not depend on, holds each population's
   count and the questions holding an unread number
   (``scripts/ledger_held.json``), and fails where the Reading's own list
   and the deletion's disagree beyond the three questions it holds by name.
3. **A sayer cannot read the warehouse, and only the relations run SQL.**
   ``compose.say`` and ``compose.sentence`` import no ``duckdb``; the SQL
   ratchet lists the twelve relation modules' statements and fails on a new
   one anywhere; and no function in the reader takes a connection (a ratchet
   held at zero).
4. **One table of cells per relation**, with a row per shape and a reason
   per excluded cell, read by the planner's one check, and frozen by module
   and name (``tests/query/test_frozen_shapes.py``, which also freezes the 25
   keys the grammar can name and the page's seven renderers: no new shape
   while the pipeline is rebuilt).
5. **Everything the Result carries is said.** ``scripts/stage_snapshots.py
   remarks`` lists a remark written and never said, and the snapshots hold
   the remarks identical.
6. **One representation, one vocabulary.** One closed measure catalog
   (:data:`association.query.measure.CATALOG`, 42 keys), one ``Line`` type
   for a line on a stat, and regexes only in the lexicon (a ratchet held at
   zero).

**A change to the query path is proven stage by stage, on four
populations**, against a fresh run of the tree before it.
``scripts/stage_snapshots.py run`` answers the 628 recorded questions
through the whole agent, with the model's recorded replies in its place,
and writes what each stage produced - the reading, the planned query, the
result's values, the answer - and ``compare`` names the first stage each
question differs in; ``run --feed`` does the same for 2,082 questions from
StatMuse's feed that the corpus does not hold; every call the unit tests
make across a stage boundary is recorded (``tests/stage_calls.py``) and
compared; and 2,710 readings are read and planned with no answer. Two runs
of one tree are identical on every population, so any difference is the
change.

Auditing
--------

:mod:`association.check.report` compares what is on disk against what the
endpoints say should exist, per season and season type, and can cross-check
live with ``--live``. Every question, from ``query`` or ``association web``,
also writes a full trace to ``.history/`` — the command, the model's reply,
the reading, the decisions, the point, per-stage timings, and the final
answer — whether or not ``--verbose`` was passed.
