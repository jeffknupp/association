# Changelog

Notable changes to `association`, newest first. Each entry links back to the
commit that made it for the full story.

Released versions follow [semantic versioning](https://semver.org): the major
number changes when something that used to work stops working, the minor
number when something is added, the patch number for fixes. What counts as
"something that used to work" here is the CLI surface - command and option
names, output shapes that scripts parse - and the documented Python API, not
the internal query templates or the router's intent set, which are expected to
grow continuously.

Sections dated rather than numbered predate the first release, when the project
had no published version to be compatible with.

## Unreleased

- **The steps that settle a player and his games are written once (step 3,
  C1).** `game_log` and `player_stat` each wrote out the same sequence - settle
  the span, resolve the name against it, settle an ordinal season once he is
  known, then narrow his games by opponent, venue, an absent teammate, a
  starter/bench half, a playoff-series game, lines on box-score columns and a
  date - and a fix to one had to be found and repeated in the other.
  `templates.common.scoped_player` and `scoped_games` are those steps, read
  from the slots in one place, so a narrowing the relation learns reaches every
  template built on them. No answer changes: proved by a golden comparison of
  460 recorded and constructed slot sets across the ten templates that read a
  player's games (answer text, data and refusals identical), with the
  comparison watched to fail when one slot was dropped from the shared
  function (14 cases moved). `period_split` is now on the same two steps -
  `_period_split_rows` reads `scoped_games` for its narrowing rather than its
  own call to `_narrow_player_games`, and `period_split` itself reads
  `scoped_player` for the name and the "current or named" season it already
  read one way - with the opponent still resolved eagerly through
  `_optional_team` beforehand, since the answer needs its name whether or not
  any games end up narrowed to it. Proved by the same golden comparison
  (460/460 identical), watched to fail when `without` was dropped from the
  narrowing (6 cases moved).
- **The router no longer invents a `season` or a `date` the question never
  states.** A bare `season` integer or `date` from the model used to be
  trusted on its own whenever the question named no year or day - measured
  live, "show me stats for sixers when maxey scored 20+ points" arrived with
  season=2023 (nothing in the text but "20+") and answered a real player's
  real average for a season nobody asked about, and "fingerprint maxey vs
  jaylen brown 2026" arrived with date='2026-01-01' and was refused ("not yet
  for a particular date") for a cause the question never gave. Both are now
  kept only when the question itself supports them - `_validate_season` still
  reads a year or "last season" from the text first, and now drops a leftover
  model guess rather than trusting it; `_route_calendar_slots` drops a
  model-supplied `date` unless a real calendar day is in the question. A year
  or day the text does name (`season_ref`, "last season", an ordinal season,
  a stated year, a stated calendar day) is unaffected. Fixes #95.
- **"Last N games" with no season type named now reads both types.** A "most
  recent games" question that never says "playoffs" or "regular season" used
  to default to the regular season alone, silently - "Show me the Knicks last
  5 games" listed games through April even after the team's season carried on
  into the postseason, and "what did Nikola Jokic do in his last 5 games?"
  left out the playoff games it should have named. `game_log` now reads both
  season types for that shape and merges them by date, and says in the
  heading what it found: "last 5 games of the 2026 postseason" where every
  kept game is one type, "last 6 games (1 regular season and 5 postseason)"
  where they are not. "Last 5 regular season games" and "last 5 playoff
  games" are unchanged - saying the type outright is how the default is
  corrected. Read from the question in `router._route_game_log_recent_span`,
  the same way `side` and `coach` are, so no other question's routing moves.
- **A name the question leaves open means whoever still plays, and the answer
  says so.** A name several players share used to ask "which one?" whenever
  more than one of them had a row in the seasons the answer could read - so
  "show maxey's games against boston in the past two seasons" asked about
  Marlon Maxey, who retired in 1994. With no season asked about, the one
  namesake who played the last season of the span is now the answer, and two
  who both played ("brown", "curry") are asked about exactly as before. A name
  given in full yields the same way where its owner has nothing in the seasons
  asked about and exactly one namesake does: "Jabari Smith" for 2026 is Jabari
  Smith Jr., where it answered "no 2026 games" about his father. This is a
  default, so it is said: the answer ends "('maxey' was read as Tyrese Maxey,
  the only match who played in 2025-26. Marlon Maxey also matches - use the
  full name, or name a season he played, to ask about him.)", and the same
  sentences are in `Answer.data["name_readings"]`. Measured on the warehouse,
  124 of the 391 surnames two or more players share stop asking, and in 67 of
  them a retired namesake has more games on record than the active player
  ("wade" is Dean Wade, "pippen" is Scotty Pippen Jr.) - which is why the
  sentence, with the wording that reaches the other player, is part of the
  change rather than a nicety. New: `entities.collect_name_readings`.
- **A router that could not be asked says so, instead of blaming the
  question.** `route()` collapsed three different failures into one: ollama
  unreachable, ollama unable to serve the router model, and the model replying
  with something unparsable all returned None, and the caller reported "the
  router returned no usable classification". Only the third is that. Reported
  from a laptop where the router model was not pulled: every question came
  back with that sentence, which reads as a fault in the question and points
  nowhere near ollama - and under `--disable-fallthrough` it is the entire
  error. The first two now raise `RouterUnavailable`, naming the model and the
  server ("ollama could not serve the router model 'qwen2.5:3b': model not
  found"). The rule that a router failure costs a round trip and never an
  answer is unchanged: `Agent._ask_inner` catches it and falls through exactly
  as before, and `scripts/check_routing.py` says it once and stops rather than
  reporting every case as a routing failure.
- **The web page recalls earlier questions, and every question can be copied.**
  ArrowUp steps back through the questions asked on the page and ArrowDown
  forward again, the way a terminal does: it holds at the oldest rather than
  wrapping, stepping past the newest restores the half-written question it
  interrupted, and a repeat of the question just asked is not a second entry.
  It is only history when the caret is on the first line (ArrowDown, the
  last), so a question typed across two lines still moves the caret between
  them; typing anything restarts recall from the newest, because someone who
  browsed to the oldest entry and then typed something fresh otherwise found
  ArrowUp doing nothing at all. Every question balloon now carries a copy
  button for its own text, using `navigator.clipboard` where the page is a
  secure context and falling back to a selection-based copy where it is not -
  `association web` prints a loopback URL, which IS one, but the same server
  answers on a LAN address over plain http, where the API is simply undefined.
  A failed copy says so rather than looking like nothing happened. The
  questions live in memory only, so a reload clears them, like the answers
  above them (ISSUES.md #69).
- **A player's record against one team is his games, not two franchises
  meeting.** "Embiid career record vs boston" and "Show Embiid's career record
  against Boston" both routed to `head_to_head`, which counts every
  76ers-Celtics meeting including the ones he sat out - and both fell through
  before reaching that wrong reading. They arrive two ways, with the player in
  the `teams` list and with him replaced by his own team and named only in the
  question, and `entities.player_record_against_a_team` turns both into
  `with_without`, whose split is exactly "the games he played against the ones
  he missed". The router cannot make that call itself: whether a name in
  `teams` is a player or a franchise is a fact about the warehouse, not about
  the words. `with_without` now honors `opponent` to go with it, narrowing
  both rows of the split together and naming the opponent in the title, since
  a record over one opponent's games headed as though it covered every game is
  the silent narrowing that module exists to stop. Measured: 76ers 13-15 in
  the 28 regular-season games Embiid played against Boston, 3-8 in the 11 he
  missed. The handler is re-resolved with the intent: it shipped once without
  that, so the trace read `head_to_head -> with_without` and head_to_head ran
  anyway and refused for wanting two team names, which every offline replay
  passed because the replay script resolves the handler after the rewrite and
  the pipeline resolved it before (#163).
- **One definition of what a question calls a box-score column.** `route()`
  reads the stat beside a threshold ("20+ points") and `templates/common.py`
  reads the same phrases onto columns ("under 14 fta"), and each kept its own
  copy of the vocabulary, because `router.py` imports nothing from
  `templates` on purpose. Nothing checked that they agreed, and
  `check_duplicate_names.py` could not - the two names differ. Both now read
  `association.query.measures.MEASURE_WORDS`, a leaf module with no imports of
  its own; the router names only which SPELLINGS its threshold grammar
  accepts, and takes what each means from there, so a spelling dropped from
  the shared map raises at import rather than silently narrowing the grammar.
  The rebuilt pattern was re-proved identical over the same 317 strings (#164).
- **A subject keeps the first name the question gave it.** The grammar that
  reads a dropped subject back out of a question captured ONE word, so a
  possessive gave a bare surname: measured over the 261-question corpus,
  "kobe bryant's stats vs rockets" named `bryant` - Bryant Reeves, Bryant
  Stith, Carter Bryant and Elijah Bryant, and not Kobe - and "Jaden
  mcdaniel's" and "steve adam's" did the same. It captures an optional
  leading word now, as the count grammars already did, gated on the same
  stopword list so "most points curry scored" still reads `curry` and never
  `points curry`. A bare "had"/"has" before a number is a subject position
  too ("sixers record when maxey had 10+ rebounds" named nobody, while the
  same question with "scored" answered), and the stopword list gained the
  function words that can sit before a name, which is what kept "when maxey"
  from being read as one. Net over the corpus: three full names recovered and
  four pieces of junk dropped, "game score nba leader" - which named a player
  called "game" - among them. No corpus question's routing or answer changes
  (#165).
- **A ranking asked for in a unit nothing is stored in is refused, by name.**
  "who were the top 10 in defensive netpoints / 90" fell through to the agent:
  `rate` was in no template's `HONORED_SCOPING`, so `check_scope` raised - a
  sentence that reads as a refusal in the trace and is not one, since the
  question then reached an agent with no per-90 anything to read and nothing
  to stop it filling the silence. `leaderboard` now declares `rate` and
  answers it: a season total where the metric has one, and otherwise a
  refusal naming the metric's real forms ("No leaderboard ranks netpoints
  defense per 90 minutes - the warehouse stores it only per game or per 100
  possessions"), which is computed from the metric rather than listed
  generically so it cannot offer a form that does not exist. The same
  omission had made `leaderboard`'s existing `rate == "total"` branch
  unreachable through the pipeline, so "most points this season" as a season
  total is answerable for the first time (#152).
- **A record "when X had 20+ points" reads its own threshold.** Measured live
  in the 2026-09-20 web session: "what was the sixers record this season when
  tyrese maxey had 20+ points?" came back with `stat='wins'` - "record" is
  what the model had to file under `stat`, which is required and whose enum
  has no won-lost record, so the nearest value it knew won - and the template
  refused with "record_when needs a known stat and a positive threshold, got
  'wins'/20" about a question that states its stat plainly. `route()` now
  reads "20+ points" as the one fact it is and sets both halves for
  `record_when`, the question's own words beating the model's guess; a
  question stating two thresholds is left alone to refuse, since there is no
  second threshold slot to put one in. The threshold vocabulary and the
  pattern that matches it are now one definition - the alternation is built
  from `_THRESHOLD_WORDS` - and the rebuilt pattern was proved to match
  identically over 317 strings. `ROUTER_PROMPT` and `ROUTER_SCHEMA` are
  untouched (#114).
- **A record "when X scored N" keeps X.** "what was the sixers record when
  maxey scored 15+ points?" routed to `record_when` with the team, the stat
  and the threshold all correct and no `player` at all, so the template raised
  "record_when needs a player", the question fell through, and the agent spent
  583 seconds producing nothing. The name was already sitting in the grammar
  `_SUBJECT_OF_HIGH` reads ("maxey scored"); `record_when` was simply never
  asked, so it joins `single_game_high` and `threshold_count` in
  `_SUBJECT_RESTORED_INTENTS`. Restoring can only help here, where it could
  not for the other two: a `record_when` with no player is not a league
  question but an unanswerable one. The question now answers 36-29 over the 65
  games Tyrese Maxey scored 15+, matching the figure measured by hand on
  `player_game_log`.
- **`record_when` answers a team's own threshold, with no player named at
  all.** "what was the celtics record when they scored 120 points" names no
  player by any grammar, so the template raised "record_when needs a player" -
  the wrong cause for a question that was never about a player - and fell
  through to the agent. `record_when` now reads a `team` slot with no
  `player` as the TEAM's own threshold: `points` is read straight off
  `real_games`' own score rather than a `team_box_stats` row, so it needs no
  box score at all and is immune to the empty 2013-2018 Chicago/New Orleans
  team boxes; every other whitelisted stat (`rebounds`, `assists`, `steals`,
  `blocks`, `threePointFieldGoalsMade`, `fieldGoalsMade`, `freeThrowsMade`,
  `fouls`) reads `team_box_stats` and says how many of the team's games it
  could not see there. `turnovers` reads `totalTurnovers`, which `DATA.md`
  establishes as ESPN's correct figure in every era; only `minutes` is refused
  by name, because a team has no minutes total. Measured on the 2026-09-20
  warehouse: the Celtics were 27-0 scoring 120+ and 29-26 under it in the 2026
  regular season. `record_when` stays in `PLAYER_REQUIRED_INTENTS`: a question
  naming exactly one player still restores him, which is what keeps "76ers
  record with 20+ points from tyrese maxey" - a wording the subject grammar
  does not fire on - from being answered as the 76ers' own scoring, while a
  question naming nobody reaches the team branch either way (#144).
- **A single-game shot chart or fingerprint names the game, not just its id.**
  A single-game shot chart's subtitle read `f"game {event_id}"` (e.g. "game
  401705764"), and its answer named only the player and the made/attempted
  split - neither said which game had been drawn, not the date, the opponent
  or the result, though `games` holds all three. The single-game fingerprint
  shared half the shape: it already named the date but not the opponent or
  result. Both now read "2026-01-25 @ MIN, W 111-85" in the subtitle and the
  answer - "vs" for a home game and "@" for a road one, as every other log
  here writes it, via the new `association.query.game_label.game_label` - one
  definition read by both renderers, so they cannot drift into two
  descriptions of the same game. The filename keeps the bare event id; only
  the reader-facing text changed. A game with no usable box-score row (a
  placeholder event, an unposted score, or a warehouse without
  `player_game_log`) falls back to the old bare-id/bare-date text rather than
  raising or printing "None vs None" (#155).
- **A fingerprint "vs" note stopped naming a player it holds as one it does
  not.** "show a fingerprint for maxey vs jaylen brown in 2026" answered a
  single polygon for Tyrese Maxey plus "only one of them matches anybody in
  the warehouse - check the spelling of the other" - false: `players` holds
  Jaylen Brown and `net_points_player_fingerprint` has his 2026 row.
  `entities.restore_dropped_players` compared `players_named_in`'s COUNT
  against the held slots' count, and "Maxey" alone names two players
  (Tyrese and Marlon), so `players_named_in`'s own strictness dropped it from
  its list - leaving one name on each side of the comparison and reading as
  "nothing to restore" even though the two single names were two different
  people. It now compares by CONTENT: a held name with any trace in the
  question (`entities._grounded`) is kept, and whatever the question names
  beyond that is added rather than used to replace the whole list.
  `entities.compared_but_unmatched` is now a second, independent guard against
  the same false claim - it takes a connection and resolves the leftover name
  against the roster before saying anything, so a resolvable name gets "was
  not included in this answer" and only a genuine non-match still gets the
  spelling note (#143).
- **A team written with its space left out is still the team.**
  "trailblazers stats last 10 games 3 point average 1st quarter" arrived with
  `team='Portland Trail Blazers'` correctly routed and then lost it:
  `entities._team_grounded` asks whether the question holds a WORD of the
  name, and the single token "trailblazers" equals none of {portland, trail,
  blazers}, so a team the question opens with was dropped as one it never
  mentioned - a regression from `178c21f`, which made an ungrounded team go
  even where no player was found. The nickname was only the visible half: six
  of the thirty teams have a two-word city, and `_team_named` resolved none of
  the seven run-together spellings, so `_team_after_versus` lost "vs
  goldenstate" and "vs newyork" outright as well. `_run_together` now derives
  the spellings of a name with a space taken out, and grounding, `_team_named`
  and `find_teams` all read them. It stays a lookup and never a guess: the
  letters must equal a whole run of the name's own words in its own order, so
  "la" and "trailblaze" still name no team, and all 44 forms of the 30 teams
  resolve to exactly the team they spell with no collisions. Measured over the
  261-question corpus: no answer changes, and the one question this reaches
  keeps its team instead of handing the agent a question with no team in it
  (#158).
- **A team's quarter is refused for a stat the linescore does not hold.**
  Restoring that team would have answered the same question's "3 point average
  1st quarter" with the Blazers' first-quarter POINTS - the fluent wrong
  answer this architecture exists to prevent - because `team_quarter_points`
  reads `home_linescores`/`away_linescores`, which hold one number per period
  and nothing else. A `stat` that does not resolve to points now raises,
  naming the linescore as the limit, so these questions fall through as they
  did before rather than being answered about something else (part of #161).
- **"All playoff games" draws every postseason, not just one presented as
  all of them.** "show a shot chart for steph curry in all playoff games"
  routed to a single defaulted season (2025) and drew it - 62 of 130 shots -
  with nothing in the answer saying it was one postseason out of the ten
  Curry has (2013-2019, 2022, 2023, 2025; 3,866 postseason shot rows on record, 3,494 of them carrying coordinates). None of
  `_SPAN_WORDS` ("career", "all-time", "ever", "in/of history") is in "all
  playoff games", so `_validate_span` now also reads "all"/"every
  <season-type> game(s)" (anchored so "all-star" cannot fire it) into `span`
  "career", and `shot_chart`/`shot_distance` now honor it - drawing (or
  averaging) every season of the season type asked for, 1,387/3,055 makes
  across Curry's whole postseason, and saying so. A career whose own start
  predates the 2002 shot floor (`COVERAGE["shot_chart"]`) says which seasons
  are missing rather than reading as though nothing were on record at all,
  or, where the whole career is before it (measured: Michael Jordan's
  1985-1998 postseasons), why nothing can be drawn (#141).
- **"past two seasons" reads as a season span, not a games count.** The
  router's own `limit` is where a relative season count landed instead:
  "show tyrese maxey's games against boston in the past two seasons" arrived
  with `limit=2` and `span="career"`, and answered his last 2 games of his
  CAREER where 7 were asked for (3 against Boston in 2025 and 4 in 2026,
  measured on `player_game_log`). `route()` now reads "past/last N
  seasons"/"...years" into `since` (current season minus N plus one, no
  `until` needed since nothing is played after "now"), and drops a `limit`
  whose only count word belongs to that phrase rather than to a real number
  of games ("last 5 games in the past two seasons" keeps its `limit`) (#140).
- **Both conditions of a two-condition count are answered.** `ROUTER_SCHEMA`
  carries one `threshold`, so a second condition survived only as a `fields`
  entry the template ignores: "who had the most 30+ point 10+ rebound games
  this year?" answered the 30+ point leader (Luka Doncic, 44) where the pair
  is Nikola Jokic with 20, and "How many 20+ point 5+ assist games did luka
  have?" answered 441 against 397. `route()` now reads every "N+ <stat>" the
  question states and, where it states more than one, carries them all as
  lines on box-score columns - the filters the relation already composes - so
  `threshold_count`, `game_log` and `player_stat` keep only the games that
  clear every one. A single condition is still the model's own `threshold`,
  untouched, and the "+" is required, so "top 10 rebound leaders" is a
  ranking and not a condition (#139).
- **A chart is not narrowed to one game the question never named.** On
  `shot_chart`, `shot_distance`, `player_netpoints` and `fingerprint` an
  `order` resolves to a single event id, where `game_log` only sorts, so a
  filler one costs a whole season: "a shot chart of steph curry's 2025 season
  for 3 point shots" drew one game, 7 of 12. `route()` now keeps an `order`
  on those four only where the question names a game at one end of the span.
  And "steph curry's last regular season game" came back as season 2025 - the
  model read "last regular season" as the season before this one, and the
  chart drew a game a year off; a single game named with no year and no
  season words now means the current season, which is the default every
  template already applies (#153).
- **A team's half is answered, and "most points in a half" is one game.**
  "Detroit Pistons most points in a first half this season" and "least points
  scored by the wizards in the first half this season" fell through: a team's
  half had no template, because the model maps "first half" onto period 1 and
  that is wrong for a team the same way it is for a player. The linescore
  already holds both quarters, so `team_quarter_points` sums them - it takes
  the `half` slot through the same `_period_scope` the player side uses - and
  a question asking for the most or the fewest gets that single game rather
  than the season's average, with every game named when two tie. Measured:
  the Pistons scored 81 in a first half against Indiana on 2026-04-12, their
  most, and the Wizards 37 against Chicago on 2026-04-07, their fewest.
- **A quarter or a half is narrowed by the relation, so it honors a
  teammate's absence and an order.** `period_split` kept its own copy of the
  played-game guard, so "scottie barnes stats 2nd half log without rj" was
  refused for a slot no period template honored while the relation had been
  answering exactly that narrowing for four other templates since the port.
  Its rows come from `_narrow_player_games` now: `without` composes there
  (with the teammate tenure rule and the clarifying question that comes with
  it), and `order` picks which end of the season the log's rows come from
  rather than always the most recent. The answer names the absent teammates,
  as every other narrowing here is named. Proved a pure refactor over 20
  recorded period questions - every answer byte-identical, and the comparison
  watched to fail on a one-token change. Measured: Barnes played 80 games in
  2026 and RJ Barrett played 55 of them, so his second half without him
  covers the 25 the template reports.
- **A quarter or a half can be ranked, not just looked up.** "who has the
  highest average 1st quarter points this season?" and "knicks 1st quarter
  scoring leaders playoffs" reached `other` and fell through to the agent:
  the router sends a period question with no named player there, and there
  was nothing else to send it to. `period_leaderboard` ranks players by their
  points in one quarter or half, reading the same source `period_split` reads
  the same way - the value of each made shot through `SHOT_VALUE_SQL`, never
  the play's prose - with that template's season accuracy gating unchanged.
  The denominator is games PLAYED, so a scoreless quarter counts as the zero
  it is; a per-game average needs the qualifier every other per-game ranking
  here applies (20 games, five in the postseason) and the answer names it; a
  named team narrows the ranking to that team's players rather than becoming
  the subject, and the ranking words win over the team's own quarter template
  so "knicks ... leaders" is not answered with the Knicks' first-quarter
  total. Measured: Luka Doncic led the league at 12.0 first-quarter points
  over 64 games in 2026.
- **"Record when X and Y played" is answered, not refused four ways.** All
  four phrasings a web session asked - "when both Embiid and Paul George
  played", "with Embiid and Paul George", "when Embiid and Paul George play",
  "when Embiid with Paul George" - routed to `record_when`, which divides a
  season by a NUMBER a player reached and so refused every one for want of a
  stat and a threshold. A thresholdless `record_when` that names players who
  played together is `with_without`'s question, which already divides by two
  teammates at once, and it now goes there: the 76ers were 13-11 in the 24
  games Embiid and George both played. Both sides of "when A with B" are
  read, since taking only the far side answered about George alone. A
  question that does name a threshold keeps its intent (#156).
- **A fingerprint pair is no longer refused over the router's own mis-slot.**
  "sga vs tyrese maxey fingerprint" put Maxey in `opponent`; the pair was
  correctly rebuilt into `players` and then `check_scope` refused the
  leftover slot, so a question the system answers under other words
  ("compare sga and tyrese maxey fingerprint") had no answer at all. An
  `opponent` that names no team and names a player the slots already ask
  about is dropped. One that names a player nobody asked about stays, so a
  template that cannot honor it still refuses rather than widening to every
  opponent (#157).
- **The fall-through agent gives up on a clock, and says what it could not
  answer.** An iteration cap never bounded the wait, because the cost is per
  model call: measured over 24 questions, every finished run spent nearly all
  its wall time inside 1-4 calls, **14 of the 24 never finished at all**, and
  one ran past 17 minutes before being killed by hand. `Agent` now takes
  `budget_seconds` (default 120, `--agent-budget` on `query` and `web`, 0 to
  remove the bound), checked before each call so the first always runs. When
  it gives up - on the clock or on the tool-call cap - it now names why the
  templates declined the question instead of saying "Gave up after too many
  tool-call iterations", which told a reader nothing about their own
  question (#129).
- **A faster check to iterate against.** `scripts/check_fast.sh` runs every
  gate except the Sphinx build plus every test except the one marked `slow`,
  in parallel: ~39s against ~106s for the full check, which itself drops from
  ~180s now that `pytest -n auto` (pytest-xdist, new in the `dev` extra) runs
  in CI and locally. The one `slow` test is the Eastern-date agreement check,
  57s of the suite's 80s; it still runs in CI and in every full local run.
- **A NetPoints rate asked for by any name is ranked as the rate.** "top 10
  in defensive netpoints / 100 possessions", "adjusted defensive netpoints",
  "offensive netpoints per 100 possessions" and "adjusted netpoints" all
  ranked season totals - a different list (Wembanyama, Holmgren, Queta by
  total; Wembanyama, Hartenstein, Capela per 100). `route()` now reads
  "adjusted", "per 100 possessions", "per possession" and "/ 100" and
  switches a NetPoints metric to its per-100 variant; a metric with no such
  form ("points per 100 possessions") and a per-90 rate get a `rate` slot no
  template honors, so the question is refused rather than ranked by the
  wrong unit (#152).
- **`--disable-fallthrough` on `query` and `web`, for development.** When no
  template can answer a question, the command returns an error saying why the
  fast path gave it up (no usable classification, an intent with no template,
  or the template's own refusal) instead of handing the question to the
  SQL-writing agent, which iterates for minutes at a time and rarely gets it
  right - "what was the sixers record this season when maxey scored 20+
  points?" spent twelve minutes of a pegged CPU on five wrong attempts. The
  CLI prints the reason and exits non-zero; `POST /api/ask` answers 501 with
  it; the stream sends it as its `error` event. `Agent` and `serve` take a
  `fallthrough` argument, and `FallthroughDisabled` lives in `query.answer`
  so the web layer can catch it without a model client. The changelog hook
  now also requires an `## Unreleased` heading whenever `src/` changes: the
  entry below landed inside the 4.3.0 section the day after that release.
- **A team the router put in a player's place stays where it belongs.** Four
  fluent wrong answers from one entity stage: "karl towns stats vs netslast 5
  games" restored Towns but dropped the Nets, answering his last five games
  against anybody; "magic vs nets last 10" named Magic Johnson by the one
  word "magic" and gave him the Magic's log; "Jersmi grant last 5 games vs
  the suns" and "stating centers vs phoenix suns log" answered the Suns' and
  the Lakers' logs, teams the question names only as the opponent or not at
  all. Now the opponent stays the opponent, a word that names a team the
  question is about is not a player, an "X vs Y" with nobody named is X's
  log against Y, and an invented team goes even when nobody is named - so
  the template refuses for want of a subject rather than answering for the
  wrong one. And a name typed with accents ("luka dončić") is folded to the
  plain letters the warehouse spells it in before it is matched.
- **The docs describe the shared relation.** The architecture page, the
  templates package docstring, the README and the usage guide now say that the
  box-score templates compose `query/player_games` rather than each writing
  its own SQL, and their examples of narrowings no template honors no longer
  name "under 14 FTA" and "since 2020", which both answer now.
- **"his last game" is one game.** A `player_stat` question naming a single
  game at one end of the span ("his last game", "her first game of 2026")
  gets `order` and `limit: 1` set together in `route()` from the question's
  own words, and `player_stat` hands it to `game_log`; "show maxey's stats for
  his last postseason game" answers that game rather than his postseason
  average. A filler `order` on a question naming no such game is still
  dropped, as before (closes #142).
- **An unscoped count by a named player reads as his career.** Product
  decision (2026-09-19): "how many times has embiid fouled out?" is 0 this
  season and 9 in his career, and only the second is the question. `route()`
  sends a `threshold_count` "how many" question with a player and no season
  in sight to his career; a season the question names ("this season", a
  year, "his 18th season") still wins, and a league-wide count keeps the
  default season. The answer names the scope it used, as it always did.
- **"Stats vs X" ends with the meetings behind the average.** Product
  decision (2026-09-19): averages over every meeting in scope, the game
  count, and a short footer of the meetings themselves, newest first
  (`RECENT_MEETINGS`, 5) - date, venue, result, points, rebounds, assists.
  "Jayson tatum stats per game vs sas" now says "in 1 game" and shows the
  only meeting beneath it. A per-game log is still `game_log`'s, for a
  question that says log, each game or last N.
- **"This postseason" names the current season**, like "this season" does:
  "maxey's stats for game 4 against the knicks this postseason" used to read
  as a career question and ask which Maxey. And a stat's own name is never a
  count's subject: "the most 30+ point 10+ rebound games" read "point" as a
  player and answered for Sir'Dominic Pointer.
- **"His 18th season" is a season, settled once the player is known.** "how
  many 40+ points games does lebron james have in his 18th season?" arrived
  as season 2018 - the ordinal read as a year - with LeBron dropped, and was
  answered as the 2018 league leaderboard; then it refused as a `situation`.
  `route()` keeps the ordinal as `season_n` and drops a year the question
  itself never named; `game_log`, `player_stat` and `threshold_count` resolve
  the player over his career first and then settle the ordinal against his
  regular seasons on record (`templates.common.settle_ordinal_season`),
  answering "in his 18th season (2021 regular season)". A player with fewer
  seasons than the ordinal is told how many he has rather than answered for
  his last one, and a league-wide count ("most points in 15th season played")
  refuses: the ordinal is a place in one player's career.
  The same question's subject, dropped by the model, is restored from a third
  grammar ("does lebron james have", beside #148's two), so it answers his one
  40-point game of 2020-21 rather than falling through.
- **"Game 4" is one game of each playoff series.** "Ayton stats in game 4
  playoff games" answered with his whole postseason, then refused as a
  `situation`, and "show maxey's stats for game 4 against the knicks" sent
  the agent into a six-minute loop (#145). `route()` reads "game N" (1-7,
  "game 7s" included - it is no longer a `round`) into a `game_n` slot;
  the relation numbers every postseason game within its series by date over
  `real_games` (the series' own games, so a game he sat out still counts
  toward the number) and keeps the Nth. `game_log` and `player_stat` honor
  it, saying "in game 4 of the series" with an opponent and "of each series"
  without; a regular-season question refuses, since nothing there is game 4,
  and a team's log refuses it for now.
- **A line on a box-score stat keeps a player's games.** "Sga games with
  under 14 fta in his whole career" used to be refused (`below` was a slot no
  template honored) and, before that, answered as 14 or MORE free throws
  made - the model's nearest stat, the other way round. `route()` now keeps
  the words after the number ("under 14 fta", as a list, one per phrase) and a
  new `above` slot carries a minutes floor ("with 25 minutes", "20+ mins",
  formerly a refused `situation`); `templates.common.measure_filters` reads
  them onto the `player_game` relation (`MEASURE_WORDS`: "fta", "fga",
  "mins", "threes", ... to a column) and refuses a word it cannot map rather
  than filtering on a guess. `game_log`, `player_stat` and `threshold_count`
  honor both, and the answer says what it kept ("with under 14 free throw
  attempts"). A `threshold_count` phrase carrying the count's own number is
  that count misread, so the phrase wins; one with another number is a second
  line beside it. A team's log refuses a line, since it has no such column.
- **`ISSUES.md` keeps one heading per priority tier, and a gate says so.**
  `scripts/check_issues_md.sh` (pre-commit and CI) requires exactly one
  `## P1:` .. `## P4:` heading, in order. An edit deleting the last entry of
  the P1 section had swallowed the `## P2` heading with it (898ef66), so every
  P2 read as a P1 and `sync_issues.py` would have labeled a new one that way;
  #127 had the P3 heading missing for days before that. Restored here.
- **A filler `limit` on `player_stat` is dropped whatever its size.** Now
  that a limited `player_stat` is answered as a log, a count the question never
  named would answer a different question: "Portis vs bulls 2019-20 to
  2023-24" arrived with `limit: 5` and became a three-game log where his
  averages were asked for. `route()` drops any limit on `player_stat` that no
  count word supports, not only a 1; a year is not a count, and neither half
  of "2019-20" is. "last 5 games" and "top 10" keep theirs.
- **A nickname another slot holds is not the subject.** "myles turner bucks
  stats without giannis last 10" routed with `without: ['giannis']`, and the
  nickname override, seeing exactly one nickname in the question, rewrote the
  subject to Giannis - who then could not play without himself.
  `override_nicknames` now leaves alone a nickname the router already put in
  another slot; the question answers with Turner's last 10 games without him.
- **`since` is a scope the relation honors.** "jokic vs cade since 2022"
  answers their 7 meetings across 2022-2026 instead of this season's one:
  `_span_of` and `_condition_scope` take `since` and build a span from that
  season on (the phantom still excluded, never earlier than the table
  reaches), `game_log`, `player_stat` and `player_matchup` declare it in
  `HONORED_SCOPING`, and a since-span's answer says "since 2022" rather than
  "over his career". Defined once on the relation's span builders, so any
  template on the relation gets it by declaring it.
- **A player's numbers over the last N games are the log with its averages.**
  `player_stat` with a `limit` or an `order` hands the question to `game_log`
  instead of refusing it - the product decision that "stats over his last N
  games" is a per-game log with averages beneath, never the season line.
  `player_stat` declares `order`. Note the dependency this creates: a filler
  `limit` the router emits on a question that named no count is dropped in
  `route()` (`_LIMIT_REFUSING_INTENTS`); the template now trusts the slot it
  is given, so that rule is what stands between "westbrook stats as a
  starter" and a one-game log.
- **The unseen-games counters have one home.** `player_games.scope_without_guard`
  is the span clause for the reads that count what the played guard drops
  (`_empty_box_scores`, `_rebuilt_in_scope`), replacing `templates.players._box_scope`;
  the relation's module docstring says why those reads omit the guard and
  where the team-level counterpart lives. With this, "a player's games" is
  defined once (closes #149).
- **The last player-games read is on the relation.** `conditions._player_games`
  (behind `streak`, `player_splits`, `record_when` and `with_without`) now
  derives its join, its played guard and its rebuilt-line blanking from
  `query/player_games.py` instead of its own join to `real_games` and
  `team_box_stats` - the side and the scores come from `games` directly.
  Measured first: on the 2026-09-19 warehouse no played box-score row sits on
  a game `real_games` drops (43,504 games, 43,353 real), so the two reads
  agreed by fact; now they agree by construction. `player_splits`' venue and
  opponent narrowing names the relation's columns for a player's games and
  keeps the team-box spelling for a team's. Pure refactor: all 11 recorded
  cases across those four templates and all 195 across the five player-games
  templates are byte-identical before and after.
- **The pair relation.** `player_games.paired_rows_sql` reads the games two
  players both played, on opposite teams or the same, as two reads of the
  player-games relation joined on the event - both under the played guard,
  with the rebuilt-line blanking applied by `player_games.column`. `player_matchup`'s
  meetings now come from it instead of `conditions._meetings`' own join to
  `real_games` and `team_box_stats`. Pure refactor: all 17 recorded matchup
  cases (195 cases across the five player-games templates) are byte-identical
  before and after.
- **One definition of "a player's games."** `query/player_games.py` is the
  relation every box-score template reads through: the season-keyed join to
  `games`, the phantom-1993 exclusion, the did-not-play and empty-line guard,
  the rebuilt-line opt-in and the teammate tenure rule live there once, with
  three skeleton readers (rows, one aggregate, aggregates per group) that
  `game_log`, `player_stat`, `threshold_count` and `single_game_high` now
  compose instead of writing their own SQL. A pure refactor, proved by a
  golden comparison: all 195 recorded slot sets across the five player-games
  templates return byte-identical answers and data before and after. Test
  fixtures that fed the stored box table alone now mirror the warehouse's
  shape (a `games` row per event), which is what let the relation's join
  reach them.
- **A question that names one half of the starter/bench split now filters by
  it, in every template that narrows a player's games.** "Jrue holiday last 50
  games as a starter" was refused, because `SPLIT_WORDS` records the *category*
  `starter_bench` and discards which half was named - right for `player_splits`,
  whose answer is both groups side by side, and useless to a template that has
  to filter. `TemplateContext` carries no question text, so `route()` reads the
  half (`_split_side`), the way it already reads the side of the ball: no
  `ROUTER_SCHEMA` change, so no other question's slots can move.
  The filter itself is one clause added to `_narrow_player_games`, which is
  where `opponent`, `venue` and `without` already compose over the same set of
  player-games - so it reached `game_log` and `player_stat` at once rather than
  being taught to each. That is the point: a new narrowing should become
  available to every caller, not to one template.
  Said in the answer, never silently: "Taurean Prince as a starter, last 7
  games" and "in 58 games as a starter" against 64 unfiltered. A question
  naming BOTH halves keeps the category and is still refused by `check_scope`
  for these two, because both groups side by side is `player_splits`' answer
  and not one they produce.
- **`period_split` honors the same named half**, filtering on the `starter`
  column of the box table it already joins - "Dominick Barlow scored 228 points
  in the 2nd half over 59 games as a starter" against 71 games unfiltered.
- **A `limit` of 1 the question never asked for no longer costs an answer.**
  The decoder reaches for 1 when it has nothing to put in a slot it must fill,
  and `player_stat` refuses any limit, so "westbrook stats as a starter for
  kings" was refused as "a game_log question" over a narrowing nobody
  requested. A bare `limit` of 1 with no `order` is now dropped for the intents
  that cannot honor `order`, unless the question names a count - the same
  filler rule `_route_side_and_order` already applied when an `order` carried
  it in.

- **A history file now names the build that produced it**:
  `<commit>-<hash>.log` rather than `<hash>.log`, with a matching `build:` line
  in the header. The release version alone cannot identify a behavior - dozens
  of commits share `4.2.0`, and the answers this project keeps changing are
  exactly the ones a reader needs to tie back to a build. A dirty tree is marked
  `<commit>-dirty`, since a run from uncommitted work is not reproducible from
  the commit alone.

  `git` is asked about the **package's own directory**, never the caller's, so
  running `association query` inside an unrelated checkout cannot stamp that
  repository's commit onto the run. Where there is no git, no checkout, or a
  wheel installed outside one, it falls back to the version - losing the file
  would be far worse than identifying it a little more loosely.

  Web sessions already wrote history and still do: `history.write()` runs from a
  `finally` in `Agent.ask()`, so it fires whatever the caller and whether or not
  the trace is discarded.

- **A truncated or partly-fabricated player name is now repaired from the
  question's own span, not just checked against it.** `override_invented_players`
  only ever asked whether a router-supplied name had ANY trace in the
  question at all - generous by design, so half a name could stand in for
  the whole of it. That let a name the router simply cut short pass as
  "grounded" and reach `resolve_player` unrepaired: "dennis schröder", typed
  correctly, arrived as just `'Dennis'` and asked a 7-way clarification the
  question never should have; "jayleyn brown" and "Aaron gordan" did the
  same over a typo'd half of a name the router dropped instead of
  completing. It also let a router-fabricated word survive next to a real
  one: "tatum rec home" arrived as `'Jaylen Tatum'` - the league's only
  Tatum, with an invented given name bolted on - and "Grady dick" arrived
  as `'Grady Dickinson'`, whose fabricated surname then sent
  `suggest_players`' surname-only backoff to **Hunter Dickinson**, a real
  but wholly unrelated player (ISSUES.md #122).

  `entities._question_derived_player` anchors each of the router's words to
  where it turns up in the question - exactly, or within a near spelling,
  since the router silently corrects typos - and resolves the question's
  own words there against the roster, never the router's spelling. Two
  guards keep it from trusting a coincidence: two or more anchored words
  are answered from exactly that range, never falling back to one of them
  alone (a bare given name that happens to be a rare, unrelated player's
  whole name too - "kareem stats vs bob lanier" names two players who
  retired before the 1993-94 floor, and a naive fallback answered Kareem
  Rush and Chaz Lanier, two real but wholly unrelated players, instead of
  refusing); and a single anchor is trusted alone only from the surname
  position, never a bare given name with nothing else corroborating it.
  `undo_name_completion` also leaves alone a name this now resolves, so a
  typo'd but already-repaired name is not cut back to an ambiguous
  fragment afterward.

  Measured against a 261-question replay of real StatMuse-style traffic
  (slots replayed through templates, no model call): 18 rows moved from a
  needless clarification or a "did you mean" non-answer to a direct,
  correct one; 18 more carry a fuller resolved name in the trace with no
  change to the answer given; zero rows moved the other way.
- **A stat the system can rank is now one it can also look up.** True shooting,
  effective FG%, usage rate and game score are computed from box scores by
  `fetch/advanced_stats.py` and have been rankable since 2.1.0, but
  `player_stat` knew only ESPN's own columns and refused them as unknown - so
  "kevin durant true shooting percentage career", with the router emitting
  `stat="ts_pct"` correctly, fell through to the agent with "no per-game column
  for stat 'ts_pct'". One concept carrying two vocabularies, each half correct
  on its own, which is why nothing caught it. `ADVANCED_STATS` is now the
  lookup half, and a test asserts the two lists still name the same set.

  Three things the career figure does that a first cut would not. It is
  **weighted by the stat's own denominator** - true shooting by true-shooting
  attempts, effective FG% by field-goal attempts - never averaged across
  seasons, which is the discipline the shooting percentages already state;
  usage and game score have no such denominator, so they answer for a season
  and **refuse a career** rather than report a mean of means. It **excludes the
  phantom 1993**, which is a copy of 1994 and would otherwise be counted twice.
  And it **says which seasons it could not see**: ESPN serves whole
  team-seasons of empty box scores from 2013 to 2018, so Jimmy Butler's 2013,
  2014, 2015 and 2017 carry games played, zero attempts and a NULL rate - an
  unfiltered answer credited his career rate with 290 games it never saw and
  named a span it did not cover.

  An advanced stat is also charged **its own coverage floor** (1994, from box
  scores) rather than the season line's (1977), so "Kareem's true shooting in
  1980" is refused in the words of the table the question actually depends on.
  A question that narrows the games (`opponent`, `venue`, `without`) is refused
  with that as its stated cause rather than answered with the season figure.
- **`game score` is a leaderboard metric.** `avg_game_score` was computed, stored
  and named to the SQL agent, and reachable by nothing on the fast path. It
  carries the same games qualifiers as every other per-game average: measured
  over 1994-2026, 28 season boards are led by a player under 20 games and all
  28 are postseasons, where the 5-game floor drops the genuinely small samples
  (Kawhi Leonard's 2 games leading 2023) and keeps the rest.
- **`leaderboard` and `player_stat` now reach that metric.** `stat` is the one
  REQUIRED slot in `ROUTER_SCHEMA`, so a constrained decoder filled it with the
  nearest value it knew - "game score nba leader" arrived with `stat='points'`
  and was answered "Luka Doncic led the league in points per game ... at
  33.5", correct about points and not about what was asked. `router.route()`
  now reads the two-word phrase off the question text, the same way
  `_validate_side` reads which half of a fingerprint was asked for, and sets
  the spelling each template expects (`avg_game_score` for `leaderboard`,
  `game_score` for `player_stat`) - anchored so "score" alone, which means
  points everywhere else in basketball, is not swept in with it.
- **`head_to_head` honors `venue` and `date` instead of refusing them.**
  "lakers vs mavs record last 10 home games played" narrows to the
  first-named team's home or road games; "celtics record vs sixers on
  november 11" answers one calendar date, the same way `game_log`'s own
  `date` replaces the season rather than being filtered inside it. Both are
  said in the answer text, not silently applied - a home-only record with no
  note would read exactly like the whole-season one.
- **`player_matchup` answers a player-vs-team question instead of refusing
  its `opponent`.** "sam hauser v mil", "julius randle stats vs blazers with
  minnestota" and "Curry vs dallas last q0 games" all reach this template
  with one player name and a team `opponent` - `router._route_matchup_against_team`
  exists for this exact shape but only sees the router's raw output, before
  `entities.scope_from_question` restores a name it drops afterward. One name
  and a team opponent is answered the way `game_log` answers it; a genuine
  two-player matchup with an opponent left over still refuses it, since it
  has no third team to narrow the meetings by.

  It now also honors `without`, for the same shape. "oubre vs warriors
  without embiid" and "de'aaron fox vs magic ... without wembyanama" each
  arrive with a second, fabricated "player" beside the real one - a garbled
  team name already resolved into `opponent`, or (checked against the
  warehouse) the real subject's own teammate, named a second time in
  `without`. `_player_matchup_drop_fabricated_second` eliminates the noise -
  a name matching no player at all outright, a name duplicating `without`
  only when confirmed by the same near-spelling resolution `without` already
  trusts - and folds the question into the one-player-and-a-team branch,
  which already reads `without` because `game_log` does. A genuine
  two-player matchup with a `without` left over is refused from inside the
  template rather than silently dropped, the same way `opponent` already is.
- **A name the router mis-slots as a team is recovered from the question, not
  just from the fragment left in the slot.** "Will Riley last 5 game s"
  arrived as `team='Riley'`, which `find_players` cannot settle alone - three
  Rileys share the surname - but the question spells the whole name.
  `entities._scope_from_question_player_in_team_slot` now falls back to
  `players_named_in`, gated on the fragment sharing a word with what it
  finds, the same discipline `override_invented_players` applies to an
  invented name. Measured against the 261-query StatMuse replay set: 7 rows
  move, all from `fell_through` to either a correct answer or an honest "did
  you mean" - none from `correct`.
- **`suggest_players` no longer gives up when a common surname alone matches
  too many players.** It now falls through to the stricter near-spelling pass
  instead of returning nothing, which recovers a real match the surname-only
  pass could not settle: "Dylon Harper" backs off to six real Harpers, too
  many to suggest on the surname alone, but only Dylan is also close on the
  given name.
- **`suggest_players` no longer suggests a player for a name that names a real
  team.** "Most reb by a hawk player history" answered "did you mean Spencer
  Hawes?" - a fluently wrong guess - because "Hawks" is one edit from a real
  surname. A name that resolves to a team outright is never offered as a near
  miss on a player now, regardless of edit distance.
- **`PLAYER_NICKNAMES` gained "og" (OG Anunoby), "rui" (Rui Hachimura, over an
  alphabetically-earlier "Rui Betancourt" the warehouse also holds), and
  "ant man"/"ant-man" (Anthony Edwards).**
- **`player_splits` honors a venue and/or an opponent** rather than falling
  through - "Jalen Duren away vs Denver", "Pat Spencer home vs the Suns" - the
  same filters `team_record` already applies to a team. A `limit` greater than
  1 is refused instead of silently answering the whole span it should have
  narrowed to: `player_splits` has no "last N games" mechanism the way
  `game_log` does, and answering the full season under that framing would be
  the exact silent substitution `check_scope` exists to stop. A bare `limit`
  of 1 is left alone - the router already uses it as filler elsewhere, and
  here it never changes the answer.
- **`team_record` honors a calendar month**, filtering ("76ers record in
  October, away") or breaking a record out by month ("Knicks record by
  month"), read from `games` rather than `standings`, which has no per-game
  date to filter or group by. Every other `situation` value - a weekday, an
  age, "since returning from injury", a division - is still refused: only a
  literal "in <month>" shape is read, deliberately not a month name found
  anywhere in the text, so a window ("since january 31st") is not mistaken for
  a month filter.
- **13 more query intents render as HTML instead of a raw `<pre>` block** on
  the web page: `player_stat`, `shot_distance` (a stat card each),
  `head_to_head` (a two-team score card), `team_quarter_points` and
  `period_split` (a sparkline plus a per-game table), `player_splits` (one
  table per split, home/away, starter/bench, wins/losses, by month),
  `with_without` and `record_when` (a two-row team-record comparison),
  `player_matchup` (a comparison table plus the recent-meetings log),
  `streak` (a ranked table for the league-wide shape, a plain one for a
  named player or team), `team_stat` (value and rank per metric),
  `team_leaderboard` (a ranked table) and `team_outlook` (a stat-card grid
  for BPI, record, chances and strength of schedule). Together with the
  seven `RENDERERS` already had, every intent but the tableless `coach`
  refusal now has one (ISSUES.md #110, staged plan step 1 - renderer-only,
  no template change: every key read was already on `Answer.data`). The
  prose answer is unchanged and stays available under the "text" disclosure;
  a rendering bug in any of the new renderers falls back to it rather than
  losing the answer, the same guard the existing seven already relied on.
  `tests/web/test_renderers.py`'s contract test now covers all 20.
- **`game_log` no longer answers a team's log when the question named a
  player** (#147). `game_log` took its `team` branch before it read `player`,
  so a `team` slot beside a named player won outright: "kobe bryant's stats
  vs rockets in the 2009 playoffs" answered the Lakers' own game instead of
  Kobe's, "Payton Prichard stats vs 76ers" answered an invented "Phoenix
  Suns"'s games (none) instead of Pritchard's 7, and "steph curry vs 76ers
  last 4 games" answered the 76ers' own last 4 games instead of Curry's
  against them. With a player named, his own team now narrows nothing and is
  dropped, a different team becomes his opponent (the Curry shape), and a
  team nothing resolves to is dropped exactly like an invented player name -
  but an `opponent` already named wins over all three, since a `team` slot
  beside it is the same noise the router routinely fills next to an
  already-correct opponent, not a second fact to reconcile.
- **A `threshold_count` question no longer answers the league's ranking when
  it names a player the model dropped** (#138). "how many times has embiid
  fouled out?" arrived with no `player` slot and answered "Karl-Anthony Towns
  had the most games with 6+ fouls" - a question about Joel Embiid, who has 0
  such games in the 2026 season it defaulted to and 9 in his regular-season
  career. The subject is now restored from the question's own grammar the way
  `single_game_high` already restores one for "most points curry scored in a
  game" - a name before a scoring verb or "fouled out", or carrying a
  possessive - so a genuine league question ("most 30+ point games this
  season") still stays league-wide.
- **A `threshold_count` player named with no verb at all is restored too**
  (#148), the shape #138's fix did not reach. "jamal murray games with 2
  threes including playoffs" arrived with no `player` slot and answered
  "Julian Champagnie had the most games with 2+ 3-pointers in the 2026
  postseason, with 19" - Murray, who has 58 postseason games and 426 career
  games with 2+ three-pointers made, was nowhere in it. A second grammar,
  read after the first, restores a name directly before "games with"/"games
  of" or before "`<N>[+] <stat>` games" - "Sga games with under 14 fta" and
  "murray 30 point games" name a subject the same ungrammatical way. It also
  keeps one extra word immediately before the name, to catch a first name:
  "jamal murray" resolves to one person where a bare "murray" is five players
  who all have a 2026 box score (Collin Murray-Boyles, Dejounte, Jamal,
  Keegan, Kris) and would only trade the league-ranking bug for a needless
  clarifying question. Kept as a separate pattern from the first grammar
  rather than folded in, because sharing one regex let a trailing possessive
  ("murray's games of ...") get swallowed whole - apostrophe and all - into
  the captured word once a "games of" ending sat in the same alternation as
  the possessive branch.
- **2-point percentage is answered as 2-point percentage, not overall
  shooting.** `stat` has no enum in `ROUTER_SCHEMA`, so "show me sga's 2pt
  percentage for the past 5 years" arrived at `player_history` with
  `stat='fieldGoalPct'` and answered overall FG% by season (55.3, 51.9, 53.5,
  51.0, 45.3) where the real 2-point split is 60.2, 57.1, 57.6, 53.3, 51.4 -
  measured against `player_season_stats_deduped`, seven of one session's 45
  questions answered this way. There is no stored 2-point make/attempt
  column, so `route()` now reads "2pt", "2-pt", "2 point", "two point" and
  "2p" against percentage/pct/% and sets `stat` to `twoPointFieldGoalPct`,
  which `player_history` and `player_stat` (season, career and box-score-
  narrowed) now compute from field goals less the three-point columns, always
  with the makes and attempts behind the percentage like every other shooting
  stat here.
- **A leaderboard refuses a shot-distance ranking, naming the real cause.**
  No leaderboard metric ranks shot distance, and none is planned. "who lead
  the league in avg 3 point distance" arrived at `leaderboard` with the
  nearest real metric the model knew (`threePointFieldGoalPct`) and answered
  Luke Kennard's 3-point PERCENTAGE, 47.8% - a real, fluently wrong number;
  its "shot distance" sibling with no metric word in it arrived with a filler
  `player: "player"` instead and was refused for naming a player the
  question does not mention - honest-sounding, and also the wrong cause,
  since no leaderboard could answer either question anyway. `route()` now
  reads "shot distance" / "3 point distance" / "distance for 3 point [shots]"
  on `leaderboard` questions into a sentinel `stat` and drops any `player`
  slot beside it, and `leaderboard` refuses on that sentinel before either
  wrong-cause path can run, pointing at `shot_distance` for one named player
  instead (#114).

## 4.2.0 - 2026-09-18
- **A NetPoints name ESPN spells with a generational suffix, or hyphenates
  differently, now matches too.** `match_key` reduces both sides to a
  comparable form in four measured steps - diacritics dropped, hyphens to
  spaces, whitespace collapsed, and a trailing suffix from a closed set
  removed - which recovers 710 of the 1,060 remaining unmatched per-game rows:
  `Jimmy Butler` to ESPN's `Jimmy Butler III` (498 rows, his whole per-game
  record), `Rondae Hollis-Jefferson` to `Rondae Hollis Jefferson` (144),
  `Trey Jemison`, `Billy Garrett`, `Darius Brown`. The suffix step is the one
  that can merge two real people, and it does - ESPN holds `Gary Payton` and
  `Gary Payton II`, `Tim Hardaway` and `Tim Hardaway Jr.`, fathers and sons, 31
  colliding keys in all - so a reduced spelling more than one athlete id can
  reach is never usable, and those names resolve only exactly. What is left
  unmatched is a different name rather than a different spelling (a nickname, a
  short first name, a middle name, a reversed order) and wants a curated list.
- **A NetPoints name spelled with diacritics now matches ESPN's spelling of
  it.** NetPoints' 2026 files say `Nikola Jokić` where ESPN's `players` says
  `Nikola Jokic`, so an exact match lost those players their whole 2026 season:
  Jokic matched every season from 2019 to 2025 and none of 2026, and
  re-fetching cost 682 already-resolved player-games. The name coming in from
  NetPoints is now folded (NFKD, combining marks dropped) when the exact
  spelling misses, which resolves 20 of the 39 unmatched names. Checked rather
  than assumed: no ESPN name carries a diacritic (0 of 3,080) and no two fold
  to the same string, so the fold cannot reach a player it was not already
  about, and the exact spelling always wins where both exist. Only diacritics -
  not case, punctuation or whitespace.
- **`scripts/backfill_netpoints_names.py`** re-parses the NetPoints tables that
  are matched by display name, without refetching ESPN. The 4.1.0 parser fixes
  (#22, #101) mean the Parquet on disk was written by the old code, so a load
  alone changes nothing - but a full `data pull --force` over the NetPoints era
  also refetches about 11,000 ESPN game summaries the fix does not touch, some
  40 minutes at the default rate limit. This runs the NetPoints steps of a pull
  and nothing else, through the same `Pipeline` methods, `_write_rows` and
  `warehouse.build`, and reports both measurements before and after: unmatched
  rows per table, and the row counts of the players ESPN files under two ids,
  which is the only one that shows #101 moving (that table drops an unmatched
  row rather than nulling its id, so its unmatched count is always 0).

## 4.1.0 - 2026-09-18
- The new pin rewriting refused its own first real bump, and now does not. Its
  post-rewrite check greps the whole tree for the old pin, so it fired on a
  comment inside `bump_version.py` - and would have fired on `CHANGES.md` at
  the next release, because a released entry quoting the tag it shipped under
  is a stale pin on purpose. The check honors the same exclusions the
  discovery does, and a test covers the case.
- The version directives on this release's new public symbols name 4.1.0, not
  4.0.2. Five agents working in parallel were each told 4.0.2, correct for a
  release of fixes; `GET /api/coverage` is a new endpoint, which makes this a
  minor bump. Corrected in the pre-release audit, which is what that audit is
  for - and the pinned install commands were rewritten by the bump script
  itself this time (#60), rather than by hand minutes before the tag.
- **The web page now reports `GET /api/coverage`, what the warehouse actually
  holds grouped into the three tiers a question can land in** - box score, box
  score plus play-by-play, and both of those plus NetPoints - and the page
  renders it as a row of pills under the header. This replaces `/api/health`'s
  single min/max season as the page's only claim about coverage, which read as
  "the whole span is answerable" when only its narrowest table was (#71): a
  2016 shot chart and a 2016 NetPoints fingerprint looked equally reasonable to
  ask for, and only one of them was. Each tier's floor is read straight from
  `association.nba.coverage.COVERAGE` - the same enforced table a template's
  own refusal reads - rather than recomputed from row counts, so the page and
  a refusal cannot say two different things about the same season; only how
  far a tier's data currently reaches (`last_season`) is counted live, since no
  floor records that. A season inside a tier's range but declared partial
  there (2002's play-by-play, 2002-2003's shot chart) is marked rather than
  shown as uniformly whole, and 1993 - ESPN's phantom copy of 1994 - is
  excluded from the box score tier's range rather than offered.
- **A shooting-percentage leaderboard's qualifier now scales to a shortened
  season instead of applying an 82-game-calibrated floor flat.** `ts_pct`
  (550 true-shooting attempts), `efg_pct` (480 field-goal attempts) and
  `fg_pct` (400 field-goal attempts) were measured against the warehouse: at
  the flat floors, 2020 qualified 157 players and 2021 155, against 174-184
  in every full season measured (2019, 2022-2026), and the 66-game 2012
  lockout season qualified 127 - a published rule scales per team game, so
  this was a stricter qualifier than the one it claimed to be, not a missing
  one. `LeaderboardMetric.scales_with_schedule` marks the three floors this
  applies to; `leaderboard.default_min_sample` scales them from the season's
  own MEDIAN `real_games` team-game count (72 for 2020 and 2021, 66 for
  2012), rounding half up, and falls back to the flat floor when
  `real_games` is unavailable. Re-measured: 2020 moves to 171-183 across the
  three metrics, 2021 to 176-220, 2012 to 176-211, and every full season is
  unchanged (proven identical, not just close, since the scaling factor at
  82/82 is 1). `LeaderboardResult.min_sample_applied` - already wired into
  the answer text - now names the real, scaled number rather than the flat
  one. `three_pt_pct` and `ft_pct` share the identical shape and are not
  scaled yet - see ISSUES.md.
- **NetPoints per-date rows that fail the exact display-name match now keep
  the source name instead of dropping it.** `parse_net_points_daily` and
  `parse_net_points_daily_players` used to write `athlete_id=None` with
  nothing else on the row, so a query grouping by `(event_id, athlete_id)`
  counted every unmatched row on a date as a duplicate of every other -
  2,190 rows in `net_points_player_game` and 64,210 in
  `net_points_player_game_fingerprint`, measured 2026-09-18. Both tables now
  carry `display_name` on every row (matched or not), and
  `net_points_player_game` also carries NBA.com's own `nba_player_id`
  (`plyrID` in the source), where the file provides one (ISSUES.md #22).
- **A NetPoints display name shared by exactly two locally-known athlete ids
  is no longer dropped unconditionally.** `Pipeline._name_to_athlete_id` used
  to treat any name with more than one match as permanently ambiguous, which
  meant every one of the 8 players ESPN files under two `athlete_id`s in the
  same box score (#87) had zero rows in `net_points_player_game`,
  `net_points_player_game_fingerprint` and `net_points_player_fingerprint`
  for their entire career - Corey Brewer, 985 `player_box_stats` rows over
  2008-2020, had no per-game NetPoints for a single one of them. A new
  `Pipeline._resolve_duplicate_athlete_pairs` applies the same proof
  `fetch/repairs/duplicate_athletes.py` uses to merge those ids at load time -
  the pair appears in the SAME team's box score for the SAME game - straight
  off the `player_box_stats` Parquet tree at fetch time, and resolves the name
  to the established id when that proof holds. Measured against the live
  warehouse: all 8 known pairs now resolve, to the identical id
  `player_box_stats_deduped` already treats as canonical, and none of the
  other 13 names shared by exactly two ids (real different people, #21) picked
  up a false match (ISSUES.md #101).
- **`scripts/bump_version.py` rewrites the pinned install commands itself**, so
  a release can no longer ship instructions that install an older one. The pins
  said `v1.4.0` through three later releases, 3.0.0 shipped still pointing at
  `v2.2.0`, and 4.0.0 and 4.0.1 were only right because a human edited them
  minutes before each bump. The script discovers them with `git grep` rather
  than a hardcoded list (a list of two files is what let `docs/usage.rst` rot
  unnoticed), refuses to run if a pin names neither the current nor the new
  version, and asserts no old pin survives the rewrite. It skips `CHANGES.md`,
  `tests/` and `scripts/`, which hold the same pattern as release history,
  fixtures and the regex itself - scanning them refused a real bump, which a
  test against this repository now catches.
- **`scripts/bump_version.py` now rewrites the install-command pins itself
  (#60).** Because PyPI is unreachable, `README.md`, `docs/installation.rst`
  and `docs/usage.rst` pin a release tag
  (`git+https://github.com/jeffknupp/association@vX.Y.Z`), and nothing rewrote
  them automatically - they said `v1.4.0` through three later releases, and
  3.0.0 shipped still pointing at `v2.2.0`. The bump script now finds every
  such pin with `git grep` (not a fixed file list, so a pin added to a new doc
  is covered the same way), rewrites `@v<current>` to `@v<new>` in the same
  run as the version bump, refuses outright if a pin names a version that is
  neither the current one nor the new one, and asserts no `@v<current>` pin
  survives anywhere in the tree afterward. `docs/releasing.rst` now describes
  this instead of telling a human to update the pins by hand.
- **A retired player's question that names no season no longer defaults to
  the current one and stops there.** `player_stat`, `single_game_high`,
  `game_log`, `player_netpoints` and `shot_chart` all read a missing `season`
  slot as "now", and a retired player's "now" is empty - "Allen Iverson's
  points" answered "Allen Iverson has no 2026 regular season numbers in the
  warehouse", true and about a year nobody asked for. The refusal now
  redirects to what the warehouse actually holds for him when the season was
  defaulted rather than named: "... He last appears in 2010. The warehouse
  holds his 1997-2010 regular seasons; name one, or ask for his career." Never
  substitutes an answer, only names where to ask again - the same discipline
  `entities.suggest_players` already follows for a near-miss name. A season
  the question names outright keeps its plain refusal, because that answer is
  correct as given: `_Span` now carries a `defaulted` flag from `_span_of` so
  every reader downstream can tell the two cases apart. `player_netpoints` and
  `shot_chart` do the same from their own local flag, since neither is built
  on `_Span`; neither offers "or ask for his career", since neither template
  has a career span to redirect to. (#18)

## 4.0.1 - 2026-09-18
- **A narrowed `game_log` or `player_stat` question over a season whose box
  scores ESPN served empty no longer says the games do not exist.** Both read
  `_no_narrowed_games` when their guard leaves nothing, and it used to check
  only for a recorded (or rebuilt) box score - so a stat outside
  `REBUILT_STATS` (turnovers, fouls, 3PM, `plusMinus`) sent the read back to
  the fetched lines, which are empty for every 2013-2018 Chicago or New
  Orleans game, and the answer became "No 2015 regular season games found for
  Anthony Davis" of a man who played 68. It now checks whether the games exist
  with an empty box score before saying they do not exist at all - "Anthony
  Davis played 68 games in the 2015 regular season, but the box score is empty
  for all of them" - the mirror-image bug `AGENTS.md` describes, in
  `single_game_high`'s own shape.
- **`player_stat` narrowed by `opponent`, `venue` or `without` now reads a line
  rebuilt from play-by-play in place of an empty ESPN box score, for a stat the
  rebuild gets right.** It never did before, even for points - the one stat
  measured most accurate - so "Anthony Davis points vs the Lakers in 2015"
  answered the same wrong-cause refusal as a stat the rebuild does not trust.
  `game_log` already read rebuilt lines for its own narrowed span; this brings
  `player_stat` in line with it. A shooting percentage still never widens to a
  rebuilt line - its attempts are outside what the rebuild was measured for.
- **A power-index answer whose snapshot carries no rating now says so**, rather
  than dropping the line. The power index is that answer's headline, so the old
  behavior left a reader with a record, a projection and chances and no sign
  that the number they asked for was missing. ESPN's 2026 regular-season
  snapshot is the live case - all 30 teams NULL in `bpi`, `bpioffense` and
  `bpidefense` while their records and projections are populated, and the only
  one of the table's 21 season/season-type groups with any NULL rating - and it
  is the snapshot a 2026 regular-season question now reads.
- **A regular-season BPI question now reads the regular-season power-index
  snapshot outright**, instead of whichever pre-playoff snapshot ESPN stamped
  last. Once the paging fix gave every snapshot all 30 teams, the play-in
  snapshot (season type 5) started postdating the regular-season one in 2023,
  2025 and 2026, so the same question named a different snapshot depending on
  the season - "how good were the Knicks in the 2026 regular season" answered
  from the play-in view. `team_outlook` (`query/templates/teams.py`) now finds
  the `season_type == 2` snapshot directly and only falls back to the latest
  other pre-playoff snapshot, then the postseason one, where no
  regular-season snapshot holds the team - true of no season the warehouse
  holds today (2017-2026), measured read-only. See `ISSUES.md`, "A
  regular-season BPI question answers from the play-in snapshot in 2023, 2025
  and 2026" (#88).
- **`ESPNClient.get_collection` now warns when the first page of a collection
  cannot be read at all**, instead of quietly returning `[]`. `_request_json`
  returns `None` for a 400 or 404, which used to hit
  `if not isinstance(data, dict): break` with `expected` still `None`, so the
  existing declared-vs-fetched warning had no count to compare against and
  never fired - an endpoint that started rejecting `limit=1000` with a 400
  would have reproduced the original 25-row power-index bug with nothing in
  the log. A 200 whose body is not the paged-collection shape (no `items`
  list) is the same failure and now warns the same way. A later page failing
  is unaffected: page one's `count` is already on record by then, so the
  declared-vs-fetched warning already covers it, and a genuinely empty
  collection (`count: 0`, one page, no items) still logs nothing.
- **The web page's games count no longer counts rows that are not games.**
  `games` carries 151 of its 43,504 rows that were never played - placeholders,
  team-slots naming an id no franchise has, phantoms and a duplicate - so the
  health line said 43,504 where 43,353 were played. It reads `real_games` now,
  falling back to `games` for a warehouse loaded before that view existed,
  which is the same check `conditions.box_source` makes for the filled box.

## 4.0.0 - 2026-09-17
- The version directives on this release's new public symbols name 4.0.0, and
  the pinned install commands in `README.md`, `docs/installation.rst` and
  `docs/usage.rst` point at `v4.0.0`. Both are pre-release corrections rather
  than changes: six directives said 3.1.0, written before the breaking change
  below settled the number, and 3.0.0 shipped with its pins still on v2.2.0.
- **A coach question is refused, naming the real cause, instead of falling
  through to the agent.** No table here holds a coach, so the agent queried
  tables with no such column and was then free to fill the silence from its own
  weights - the failure `check_coverage` exists to stop. The refusal says what
  is actually wrong rather than blaming the source, because "ESPN does not
  publish coaches" was probed and is false: it serves two coach collections and
  neither is usable (the season-by-season one ignores the season it is asked
  for and returns today's staff, the per-team one covers 12 of 30 teams in
  1996, never shows a mid-season change, and names the wrong coach for some
  franchises outright). `route()` assigns the intent from the question's own
  words, so `ROUTER_PROMPT` and `ROUTER_SCHEMA` are untouched - both hashes
  unchanged - and no other question's routing can have moved.
- **A player ESPN lists twice in one team's box score, under two
  `athlete_id`s, is now merged into one row.** Measured against the
  2026-09-17 warehouse: 8 players, 69 team-games, none before 2003. A new
  `player_box_stats_deduped` table (`association.fetch.repairs.duplicate_athletes`)
  picks the id with more career games carrying real minutes and keeps the
  real row over a fabricated all-zero blank; a pair is merged only where
  every shared game is safe (one side has no minutes, or both sides agree
  exactly), so a future pair that genuinely disagrees is left unmerged rather
  than guessed at. `player_game_log` now reads the merged table, so a
  per-game lookup for one of these 8 players (single-game highs, streaks) no
  longer sees a fractured career under two identities. Team-level sums and
  `player_advanced_stats` still read the raw table and are unaffected - see
  `ISSUES.md`.
- **A team-level question about Vancouver's 1995-96 season is no longer
  answered from NULLs it did not have to be.** ESPN serves an all-NULL
  `team_box_stats` row for every 1995-96 Grizzlies game - and, unrecorded
  until now, for 25 more teams' games against them that season, plus one 2000
  game - while its `player_box_stats` rows for the same games are real, with
  real minutes. `team_box_repair` now rebuilds field goals, three-pointers,
  free throws (with their shooting percentages), assists, steals, blocks,
  fouls, individual turnovers and the offensive/defensive rebound split from
  those player rows on any team row this shape touches - measured exact on
  all 2,387 surviving 1996 regular-season team rows and all 2,472 surviving
  2000 ones. `totalRebounds` and the columns the player box has no sibling
  for (team turnovers, technicals, flagrant fouls, points in the paint, a
  largest lead) still have nothing to rebuild from and stay NULL.
- **A team's rebounds are now comparable across the 2021/2022 season
  boundary.** ESPN's team box `totalRebounds` stopped counting rebounds it
  credits to no player from 2022 on, so a split spanning the change (or any
  comparison of an old season with a recent one) showed a team's rebounding
  falling off a cliff for no basketball reason - measured at ~52 a game in
  2019-2020, ~48 in 2021, 44.45 from 2022 on. `player_splits` and `streak`'s
  team-rebounds reads (`query/conditions.py`'s `_team_games` and `_TEAM_LINE`)
  now use `offensiveRebounds + defensiveRebounds`, which already equals ESPN's
  own `totalRebounds` in every season from 2022 on and is populated everywhere
  `totalRebounds` is. `team_metrics.py`'s `avgRebounds` (used by `team_stat`
  and `team_leaderboard`) was checked and needs no change: unlike the
  game-level column, ESPN's season aggregate already equals
  `avgOffensiveRebounds + avgDefensiveRebounds` in every season since 1994.
- **A playoff answer from 1995-1998 now says which games have no box score.**
  ESPN lists eight of those postseason games and serves each one a box score
  with no player lines in it - probed live, all eight return a `boxscore`
  carrying zero athlete lines where control games in the same seasons return
  24, the athlete gamelog omits them, and `plays` starts too late to rebuild
  them. 1997 is the one that costs an answer: the whole Chicago-Miami
  conference final, so Michael Jordan's postseason read 14 games and 439 points
  against ESPN's own 19 and 590, with nothing said. The game LIST is
  deliberately not caveated - those games are in it, with scores and a winner,
  so a playoff game count or head-to-head record over them is right.
- **Breaking: `Coverage.partial` and `postseason_partial` now map each season
  to its own sentence**, replacing a tuple of seasons plus one shared note;
  `partial_note` and `postseason_partial_note` are gone. `team_box_stats` is
  short five games of the 1997 playoffs and ten of 2001's for unrelated
  reasons, and one shared note named both in an answer about either - the
  wrong-cause noise that module exists to stop. Nothing outside
  `association.nba.coverage` read the two removed fields, and `season in
  coverage.partial` still works, so the change is breaking only for a caller
  that read a note off a `Coverage` directly. **The next release is therefore
  4.0.0**, which is what the version directives here name.
- **A 2013-2018 shooting leaderboard now says who is missing from it.** Those
  rates are summed from the box scores ESPN serves zeroed for every Chicago and
  New Orleans game, so 21 to 33 players a season clear the qualifying floor by
  ESPN's own season totals and fall under it in the advanced table - 153
  player-seasons, of which 83 are on the other 28 teams, short only the games
  they played against those two. The 2015 true-shooting board omits Tyson
  Chandler, its runner-up. The seasons are declared partial rather than filled
  from the rebuilt box: a rebuilt season total is exact about half the time and
  biased low, and no attempt column was ever measured.
- **A 2001 playoff question answered from the player tables now says what is
  missing.** Ten games of that postseason are not in ESPN's archive anywhere,
  and the caveat saying so was declared only on `games` and `team_box_stats` -
  which `single_game_high` and `threshold_count` never read, so "Shaquille
  O'Neal had 4 games with 30+ points" was stated as fact over 11 of the 16
  playoff games he played. `player_box_stats`, `player_game_log` and
  `player_season_advanced_stats` now declare it too, in the player's own words:
  40 of the 190 players with a 2001 postseason line are short in the box
  scores, 152 games in all. ESPN's per-player season line is complete and is
  deliberately left uncaveated, since it is what proves the box scores short.

## 3.0.0 - 2026-09-17
- **No function is more complex than radon grade C** (cyclomatic complexity
  20), enforced by a xenon gate in pre-commit and CI, with no module worse
  than C and the average no worse than B. The worst were the router's
  `route()` at 127, `parse_game_summary` at 61 and a set of query templates
  between 21 and 89; each is split into named steps called in the original
  order. No behavior changes: every split was checked against the original
  code by calling it with thousands of inputs (405,588 for `route()`, 4,433
  slot sets for the templates, every fixture for the parser) and comparing
  the full results, besides the test suite.
- **Breaking: the package is reorganized, so the next release is 3.0.0.**
  Nothing about what the commands or the query engine do changes; module paths
  do.
  - `association.cli` is a package: the commands are in
    `association.cli.commands`, and `association.repo_paths` is now
    `association.cli.paths`. `association.cli:main` (the console entry point)
    and `association.cli:cli` resolve as before.
  - The modules both `fetch` and `query` read moved from the package root
    into `association.nba`: `association.season`, `association.coverage`
    and `association.franchises` are now `association.nba.season`,
    `association.nba.coverage` and `association.nba.franchises`, and
    `association.net_points_categories` is `association.nba.netpoints`.
  - The load-time repairs and filtered tables moved under
    `association.fetch.repairs`: `game_repair`, `team_box_repair`,
    `season_totals_repair`, `reconstructed_box` and `real_games`.
  - `association.query.templates` is a package, one module per subject:
    `common` (the context and result types, the scoping and coverage checks,
    and the helpers more than one subject uses), `players`, `games`, `teams`,
    `shots`, `netpoints` and `splits`. The package re-exports `TEMPLATES` and
    the scoping API, so `from association.query.templates import TEMPLATES,
    check_scope, ...` still works; a private helper is imported from the
    module that defines it.
  - `ROUTER_PROMPT`, `ROUTER_SCHEMA`, `ROUTER_NUM_CTX` and
    `ROUTER_PROMPT_TOKEN_BUDGET` moved from `association.query.router` to
    `association.query.router_prompt`, byte-identical (hashed before and
    after), so what the model is told sits apart from what is done with its
    answer.
- **Importing the web API no longer loads ollama.** `association.web.runner`
  imported the query `Agent` at module level for a type annotation, so
  `import association.web.app` brought in the model client that AGENTS.md says
  the API layer must never import. The import is now type-checking only. The
  rule and the package layering are enforced by import-linter in pre-commit and
  CI: `cli` > `web` > `query | check` > `fetch` > the leaf modules, `fetch`
  and `query` independent, and no `fastapi`, `uvicorn` or `pydantic` outside
  `web`.
- **Spelling is checked.** codespell runs in pre-commit and CI with its
  British-to-American dictionary, and the 37 findings are fixed - mostly
  British forms in comments, docstrings and docs ("neighbouring", "cancelled",
  "behavior", "judgement"), plus "unparseable" and "pre-empts". Comments and
  docs only: the router's compiled patterns and the prompt text hash
  identically before and after.
- **Every imported package is declared.** `botocore` (imported by the
  NetPoints client) is now a core dependency and `pydantic` (imported by the
  web API) is in the `web` extra; both used to arrive only through `boto3` and
  `fastapi`. The `docs` extra names `packaging`, which `docs/conf.py` imports.
  Nothing new is installed. deptry now checks this in pre-commit and CI.
- **Dead code is now a gate.** vulture runs in pre-commit and CI at its lowest
  confidence, with the names only a framework calls listed, each with its
  caller, in `vulture_whitelist.py`. Its one real finding is removed:
  `TeamMetric.needs_opponent`, set on four team metrics and never read since
  the field was added.
- **Stricter lint, a dependency audit, and three pieces of dead code gone.**
  Ruff now also enforces `DTZ`, `BLE`, `RUF`, `PERF`, `C4`, `SIM`, `RET`, `PLW`
  and `PLE`; the 66 findings were fixed, and the handful that are deliberate (a
  blind `except` at a boundary, the local calendar date in `current_season`)
  carry an inline reason. No behavior changed: `eastern_day_utc_range` now
  builds UTC-aware datetimes, which format to the same strings. pip-audit runs
  over `uv.lock` in a new *Dependency audit* workflow on every push and weekly
  (`scripts/audit_dependencies.sh`). Removed as unused: `fetch.storage.write_row`
  (no caller outside its own test - `Pipeline._write_row` is the real path),
  `fetch.team_box_repair.REBUILT_COLUMNS` (never read; the repair's SQL names
  its columns itself), and the private `templates._as_int`.
- **The 2001 playoff caveat names every short series, and counts
  Philadelphia right.** A 2001 postseason answer said Philadelphia's run
  "reads 15 games" and named only the LAL-PHI Final and the MIL-PHI
  conference final. Finals Game 5, recovered by the scoreboard discovery pass,
  brought Philadelphia to 16, and MIL-CHA (two games) and LAL-SA (one) are
  short too. The note on `games` and `team_box_stats` now says ten games across
  all four series and 16 against ESPN's 23.
- **The README's first install command works.** It was
  `pip install 'association[web]'`, which the README's own note says does not
  work while PyPI is unreachable; it now installs from the release tag, as
  `docs/usage.rst` does too. The README also lists the 2001 playoffs' ten
  missing games among its known limitations, and its project layout names
  `season.py`, `franchises.py` and the load-time repairs.

## 2.2.0 - 2026-09-17
- **Docs brought up to date with the code.** A pass over the README, the
  Sphinx pages, `AGENTS.md`, `DATA.md`, `ISSUES.md` and the package docstrings
  for claims the last release's work had made untrue. `docs/architecture.rst`
  now describes the load-time repairs (`game_repair`, `team_box_repair`,
  `season_totals_repair`, `reconstructed_box`, `real_games`) and the
  temp-file warehouse swap; the README and `docs/usage.rst` list quarter and
  half questions, and the empty 2013-18 box scores are described as rebuilt
  from play-by-play for per-game answers rather than only counted; the
  development setup syncs the `web` extra the tests need; `docs/releasing.rst`
  says the PyPI upload currently fails. In the docstrings,
  `association.query.fingerprint` no longer says a single game cannot be
  fingerprinted, `conditions` no longer describes a fixed five-hour Eastern
  shift, `team_metrics` names `real_games` as its source, and
  `override_invented_players` no longer says an invented name falls through to
  the agent. Version directives that named 2.3.0 or 2.1.1 now name 2.2.0, the
  release they ship in.
- **The Pistons' 1990 title clincher is a Detroit win again.** ESPN serves
  Game 5 of the 1990 Finals (`100614008`, 14 June at Portland, Detroit 92-90)
  with the two teams on each other's sides - Detroit at home, losing 90-92 -
  so game logs printed the clincher as a home loss and the series read 3-2.
  A new load-time repair, `fetch/game_repair.py`, puts the team ids back
  (and `team_box_stats.home_away`), keyed on the event id and guarded on the
  stored row, so it is idempotent and stops by itself if ESPN corrects the
  game. Checked across all 570 postseason series for impossible win counts and
  out-of-format home games, it is the only such game outside 2001's missing
  ones.
- **Games stored with no tip time are dated the day they were played, not the
  day before.** ESPN stores such a game as midnight US Eastern - `04:00Z` in
  summer - and every date this project printed or filtered on went through a
  fixed five-hour shift, which is right for a real tip and moves a summer
  midnight to the previous day. 391 games printed a day early in every game
  log, single-game high and month split: 379 in 1988-1992, the whole
  1989-1992 postseason among them (the Pistons' 1990 title clincher on 14 June
  read 13 June), and 12 in the 2000-2001 postseason. A date question missed
  them for the same reason. The fix is the real Eastern clock rather than an
  era cutoff, because 2026 carries ten `04:00Z` stamps that are genuine 11pm
  EST tips: `association.season` gains `eastern_utc_offset_hours`,
  `eastern_date_sql` and `eastern_day_utc_range`, with the US daylight-time
  rules written out so no tz database is needed, and `eastern_date` follows
  them. All six places that turned a stamp into a date - `season`, the
  NetPoints matcher, `real_games`, `conditions`, `team_metrics` and the
  templates' date filter - now use them, which also retires five duplicate
  declarations of the offset (#82). Measured against the warehouse: exactly
  those 391 games move, 318 of 318 whose event id encodes the date now match
  it (0 did before), and `real_games` is unchanged at 43,353 rows. (#76)
- **2008's team rebound columns are rebuilt at load time.** ESPN serves the
  2008 regular season with the real offensive rebounds under
  `defensiveRebounds`, the team rebounds under `offensiveRebounds`, and a
  `totalRebounds` that counts the offensive boards twice - so "Celtics home and
  away splits 2008" printed 60.8 rebounds a game. A refetch serves the same
  values. `fetch/team_box_repair.py` now rebuilds the splits from the player
  box and the total as the players' rebounds plus the team figure, the
  definition of the seasons either side; the 2008 postseason is clean and
  untouched. (#74)
- **A full warehouse rebuild now builds into a temporary file and swaps it
  in, rather than replacing tables one statement at a time in `db_path`
  itself.** `association data pull`/`load`'s full-rebuild path is exactly the
  memory-hungry case `AGENTS.md` ("Working on the fetch path") describes -
  `plays` alone from 17,500 files, on the same connection loading 17 other
  tables - and building in place meant an interruption (an OOM kill or
  anything else) left the tables already replaced at their new contents and
  the rest at their old ones, with nothing recording that the build never
  finished. `fetch/warehouse._build_full` now writes into
  `<db_path>.building` and only replaces `db_path` once every load, repair
  and view succeeds; an interrupted build leaves the existing warehouse
  completely untouched, and the leftover `.building` file is itself the
  marker the next full build logs and replaces. It also stops a full rebuild
  from ever carrying forward a prior partial load's free space - the same 19
  tables and views measured 1.73 GiB in a repeatedly partial-loaded file
  against 0.92 GiB freshly built - since a full rebuild now always starts
  from an empty file. A partial `--tables` reload (used by `data pull`'s
  incremental path, and by the backfill scripts) is unaffected: it still
  writes `db_path` in place, because it depends on tables already there that
  it is not reloading.
- **A worktree can now run `association data pull`/`load` and the audit
  scripts with no `--data-dir`/`--db-path` at all.** All nine call sites
  (`cli.py`, and `check_routing`, `check_coverage`, `check_nicknames`,
  `check_net_points_games`, `check_team_box`, `backfill_season_totals`,
  `backfill_missing_playoffs` and `backfill_power_index` under `scripts/`)
  defaulted to the literal `./nba.duckdb` and `./data/parquet`, which a
  worktree does not have - both are gitignored build artifacts that live
  beside the main checkout. New module `association.repo_paths` resolves
  each default to the current directory's copy where one exists, else the
  main checkout's, found through `git rev-parse --git-common-dir`, else the
  original literal default unchanged. Verified read-only from a worktree with
  zero arguments: `check_coverage.py`, `check_nicknames.py` and
  `check_team_box.py` each ran and reported against the main checkout's
  warehouse rather than failing with "database does not exist".
- **A fresh worktree's venv is documented as needing a sync before the
  gates run**, and the `CHANGES.md` gate now says so when it checks nothing.
  `AGENTS.md` ("Before you commit") gets the line CI runs -
  `uv sync --frozen --extra dev --extra docs --extra web` - since `uv run`
  alone creates a venv with none of the `dev`/`docs`/`web` extras and
  `uv run pytest -q` fails before the suite starts. Separately,
  `scripts/check_changes_md.sh` read only `git diff --cached`, so it printed
  "Passed" with `src/` edited but nothing staged - the same check that would
  correctly fail once the edit was staged. It now says "nothing staged, so
  nothing to check" and still exits 0, rather than reading as a real pass.
- **Every team name an answer prints is the name it had that season.** A 2005
  Knicks log listed a game "vs Brooklyn Nets", eight years before the Nets moved;
  the 2008 standings put the Charlotte Hornets 23rd, a team that did not exist
  that year; every all-seasons streak answer ended "franchises are named as they
  are today", which was an honest description of a bug. ESPN keys a team by
  franchise and `teams` holds only today's names, so every row joined to it
  read today's.

  The franchise table moved to a neutral module, `association.franchises`,
  because both packages need it - `fetch/warehouse.py` builds the
  `player_game_log` view's abbreviations, and the query templates name teams
  everywhere else - and CLAUDE.md keeps those two from depending on each other.
  It gained each era's abbreviation (NJ, SEA, VAN), and two renderings of one
  lookup: `season_name` for Python and `season_name_sql`, a SQL expression that
  names each row for its OWN season, which a career log crossing a relocation
  needs. A test checks the two against each other at every season either side
  of every boundary. Both rename only when `teams` files today's name under
  that id.

  Applied at every place a team name is printed: team game logs and quarter
  scores, home/road standings, team metrics, record tables, all-seasons streaks,
  player-matchup meeting logs, with/without stints (a stint across a rename names
  both, "New Jersey Nets / Brooklyn Nets"), `period_split` rows, and the
  `player_game_log` view. The SQL agent's example queries in `prompt.py` are left
  alone, because changing them spends preamble budget.

  **The view needs a warehouse reload to show it** - it is built at load time -
  and was reloaded with this change. Every site was perturbed back to today's
  name and watched to fail; four of the first five came back MISSED until each
  had a test of its own, and the view's test had to move from 2005 to 1997 to
  catch anything, because the Grizzlies were already in Memphis by 2005.
- **The router prompt's size is documented correctly, and budgeted.**
  `query/router.py` said the prompt was "~430 tokens" in its published
  docstring and beside `ROUTER_NUM_CTX`; it is 9,989 characters, about 2,500
  tokens at the four characters a token the agent's budget is measured at,
  against a 4,096-token window. New `ROUTER_PROMPT_TOKEN_BUDGET` (three
  quarters of the window) and a test that fails when the prompt plus a long
  question passes it - the router's counterpart to `PreambleTooLarge`, as a
  test rather than a runtime check because the prompt is a constant. Not
  measured with the model's tokenizer; the figure is an estimate.
- **A stale comment in `router.route()` no longer credits the 2.0 REPL** with
  the `previous_question` follow-ups; it now says what arrives there
  (`Agent.last_question`, None in both shipped callers) and who the branch is
  for. No behavior change. The `docs/usage.rst` assists example now carries the
  "(minimum 20 games)" qualifier the answer prints. The entry that filed it
  said the answer also continued "Next: ..."; it does not, because the router
  emits `limit` 1 for "who leads" (the web renderer's own note on why a
  one-row ranking stays a sentence), and the template prints the list only
  past one row.
- **American spelling throughout `src/`.** 43 British spellings ("honour",
  "behavior", "labelled" and their forms) replaced there, and 24 more in the
  tests, including the user-visible
  `check_scope` trace "cannot honour" and three published docstrings. The
  router prompt and schema hash identically before and after, so no routing
  moved.
- **Two wrong comments corrected, no behavior change.** The comment above
  `team_metrics.TURNOVERS` said pre-2013 `team_season_stats.turnovers` was "the
  player turnovers alone"; re-measured, it is the full count with team
  turnovers in (equal to the box `totalTurnovers` season sum for 24-27 of 30
  teams, and to the player-only sum for none). The expression was right. The
  `player_season_stats_deduped` view and `leaderboard.not_a_postseason_copy`
  now both give the dropped-row figure as 436 of 7,941 rows (340 of 7,845
  player-seasons); one said 340 rows and the other 437.
- **A team name is read for its season, and the question's own team beats one
  the router could not ground.** Found through "duren v nets 1h gameloh", which
  fell through because the router wrote the opponent as "New Jersey Nets". That
  was not new - the router wrote it identically in all seven replays since the
  first - it was hidden, because the question used to be forced to the agent and
  nothing read the slot until `period_split` did.

  Two faults, and the second is the one that answered wrongly. **`teams` holds
  only today's 30 names**, so every former name resolved to nothing - New Jersey
  Nets, Seattle SuperSonics, Charlotte Bobcats, Vancouver Grizzlies, Washington
  Bullets. And **"Hornets" has belonged to two franchises**, so matching today's
  names answered "Hornets record 2008" with the 2008 Charlotte BOBCATS' 32-50;
  the Hornets that season were New Orleans, and went 56-26. ESPN's ids belong to
  the franchise, not the name - measured from where each team's home games were
  played, id 17 is the Nets in New Jersey and Brooklyn alike - so the fix is a
  list of the names the renamed and relocated franchises have carried and the
  seasons they carried them (`entities.FRANCHISE_ERAS`), read for the season a
  question is about. A name nobody held that season asks: "Hornets" in 2014 was
  neither franchise. An id is only trusted where the warehouse files today's name
  under it, because the first version renamed a test warehouse's Detroit Pistons,
  filed under id 3, to the New Orleans Pelicans.

  A name whose city the router invented is read by its nickname, unless the city
  contradicts it: "Portland Blazers" (ESPN writes Portland TRAIL Blazers)
  resolves, "Los Angeles Kings" does not, because Los Angeles is two other teams.

  **The precedence bug.** `scope_from_question` read "nets" after "v" correctly
  as the Brooklyn Nets, and then used it only if `opponent` was EMPTY - so a
  router string resolving to nothing beat a team the question names outright.
  The question now wins when the router's opponent is no team, or a team the
  question never mentions. It also fixed a second shape nobody had diagnosed:
  "andrew wiggins last 15 games vs warriors" arrived with the player and the team
  SWAPPED between slots, and kept his name as the opponent.

  Fixing resolution exposed a third fault, caught by replaying before shipping.
  "Keyonte George against blazers" arrived as `teams=["Portland Blazers"]`, and
  was answered correctly only BECAUSE that name failed to resolve. Resolving it
  let the "slots already carry this team" rule - written for `head_to_head`,
  which reads `teams` as its two sides - leave it there, and `player_stat`
  answered his whole 54-game season instead of his 2 games against Portland.
  With a player as the subject, a team after "vs" is his opponent wherever the
  router filed it.

  Measured by replaying the last run's recorded router output through the fixed
  entity stage and templates - exact for a change that lives entirely after the
  router, and checked by reproducing all 261 rows of the recorded run on the
  unchanged code first: 3 questions answer that fell through (Duren, Wiggins,
  Duncan Robinson), and nothing else moves.
- **`period_split`'s average was inflated, and now counts the games he
  played.** As first shipped, a game only counted if he made a shot in that
  period, so every scoreless quarter left the denominator: "RJ Barrett scored
  250 points in the 4th quarter over 46 games, averaging 5.4" for a player who
  played 57 and averaged **4.4**. The total was right, which is why it read as
  correct - it was graded correct in the replay that followed, and caught only
  when the new per-game log printed his games and the count looked short. A
  game with no shot data at all (2003's shots cover 986 of its games) is
  excluded rather than counted as a confident zero, and a game he sat out is
  no game.

  Two things the same replay found are fixed alongside it. A question asking
  for a log - "rj barrett 4th qtr log", "vj edgecombe 1st quarter scoring by
  game", 7 of the 11 the template answered - got a total and an average, and
  now lists the games under a header that still answers the season. And `stat`
  is the router's one required slot, so it arrives filled on questions that name
  no stat; "duren v nets 1h gameloh" came in with `stat="none"` and was refused
  as asking for something other than points. Only a stat the question names is
  kept, so "kd rebounds 4th quarter" is still refused rather than answered with
  his points.
- **A named player's quarter or half is answered, by a new `period_split`
  template.** This was the largest content gap in the 261-query feed replay -
  21 questions, every one forced to the agent because nothing answered the
  shape. "rj barrett 4th qtr log", "victor wembanyama vs sacramento first half
  log", "Devin Vassell nba player per game stats 1q". A TEAM's quarter has had
  a template for a while (`team_quarter_points`, read from the official
  linescore); a player's had none.

  **Nothing was blocking it but a stale comment.** `team_quarter_points` said a
  player's quarter score "needs the plays-table LAG() derivation", and that is
  not true: `shot_chart` already carries `athlete_id`, `period`, `made` and the
  shot's value, so the answer is a filtered sum.

  **The value is read through `SHOT_VALUE_SQL`, never guessed from the play's
  prose**, and that is the whole accuracy of the thing. Scored by looking for
  "three point" in the description, per-period points match ESPN's own
  linescores 76.8% of the time and the error is systematically -1: "makes
  24-foot running jump shot" is a three that scores as two. Read off the shot's
  own label and position it is **99.95%**. Over a whole game that gap hides
  inside a 98% figure; a quarter holds about ten field goals, so it does not.

  Validated a second way, which also settles a question the earlier entry had
  to leave open. Summed over all periods INCLUDING overtime, a player's season
  total matches his box score exactly for **550 of 578 player-seasons** in
  2026, mean error 0.138 points across a whole season - so the per-PLAYER
  attribution inside a period is sound, not just the team-level total.

  Accuracy is a property of the season, and the template says so instead of
  averaging it away. 2002 is refused, because `SHOT_VALUE_SQL` is NULL for
  20,534 of its made shots and a sum over them means nothing (4.9%). 2016 is
  refused too, at 76.5% - one quarter in four, the same season
  `reconstructed_box` singles out. 2003-2006 and 2013 answer with the measured
  figure attached. The other nineteen seasons run 99.2-100.0%.

  Points only. Rebounds and assists are not in `shot_chart` at all, and
  deriving them per period from `plays` carries its own fidelity per stat -
  fouls reconstruct at 83% - so a question asking for them is refused with that
  named as the reason rather than answered from a weaker source. A half is the
  two quarters it holds and never overtime.

  The intent is assigned in `route()` from the question's own words rather than
  added to `ROUTER_PROMPT` or `ROUTER_SCHEMA`, because both are load-bearing on
  every other question and a period is perfectly legible without the model's
  help. `CODE_ASSIGNED_INTENTS` records that, so the reachability gate can tell
  a deliberately unemittable intent from a dead one.
- **A player against a team is answered instead of falling through.** The
  single biggest theme in the feed replay: 60 of the 186 questions that are not
  answered correctly pit a player against a team, and three separate mechanisms
  each half-handled it.

  `player_matchup` needs two players. Given one and a team it had nothing to
  answer with, and the rule that redirects those only ever read the `players`
  LIST - the model routinely fills the singular `player` slot with an
  `opponent` instead ("keon ellis stats vs trailblazers", "Kd games vs
  wizards", "De'angelo russell vs pistons"). Eight feed queries fell through to
  the agent where `player_stat` and `game_log` answer them exactly, both
  honouring `opponent`. The redirect is gated on the opponent being a team:
  "jay huff game log vs Embiid" really is a matchup between two players, with
  the second one in the `opponent` slot.

  `_is_team_name` required a name's LAST word to be one of thirty nicknames, so
  a team named any other way read as a *player* - "mathurin v det", "sam hauser
  v mil", "pascal vs orlando". It now also accepts a city or an abbreviation,
  matched against the WHOLE name and never the last word, because three real
  players are surnamed Cleveland, Houston and Washington and a last-word rule
  turns PJ Washington into a team.

  **A misspelled team is deliberately still not matched**, and that was
  measured rather than assumed. `difflib` at 0.8 reaches the right team for the
  feed's three typos ("taptors", "warriners", "blakers") - and also for 16 real
  player surnames: Burks to Bucks, Hawkins to Hawks, Thornton to Toronto, Wheat
  to Heat. The model puts bare surnames in that slot routinely, so those
  collisions are live, and no cutoff separates them: "houstan"/"houston" and
  "taptors"/"raptors" are both one edit in seven characters. Three queries is
  not worth sixteen.
- **The team abbreviations people actually write now resolve.** ESPN's `teams`
  table abbreviates four teams "GS", "NO", "NY" and "SA", so `GSW`, `NOP`,
  `NYK` and `SAS` resolved to **nothing** and the team was lost from the
  question entirely. The full "Los Angeles Clippers" did too - the router is
  asked for full team names, and ESPN stores that one as "LA Clippers".

  An exact abbreviation also outranks a team whose name merely contains it.
  `ORL` is Orlando's abbreviation and a substring of "New Orleans", and both
  came back: an ambiguity offered over a question naming exactly one team, and
  a chance to answer about the other. Two teams in one city stay ambiguous, as
  they should - no team is abbreviated "LA".
- **A narrowing the router has no slot for is refused instead of dropped.**
  `check_scope` can only refuse a slot the router emits, and `ROUTER_SCHEMA`
  has no slot for a day of the week, a calendar holiday, an age, a minutes
  condition or "since returning from injury" - so those words never reached it
  and the template answered the *un-narrowed* question. Measured over 261 real
  StatMuse feed queries, this was the single largest cause of a wrong answer:
  14 of them, more than any other. "lebron james 2 3 pointers all-time vs jazz
  on tuesdays" returned his career average against Utah over 48 games, with the
  Tuesday, the threes and the "2" all silently gone; "anthony davis stats on
  christmas" returned a whole season average; "most triple doubles before
  turning 27" returned this season's leaders.

  A second pass covers the shapes the first measurement showed it had missed:
  one game of a playoff series ("Ayton stats in game 4 playoff games" returned
  his whole 10-game postseason) and a season named by ordinal ("his 18th
  season", which the model read as the year 2018 and answered with that
  season's league leaderboard). Both refuse.
- **A calendar day without a year is now answered, not refused.** "Desmond bane
  march 17" returned his most recent game, dated 2026-04-12 - a month off, and
  a different question. The year is not in the question and does not need to
  be: **a season fixes it.** Season Y runs from October of Y-1 through June of
  Y, so October to December belong to `season - 1` and January onward to
  `season` - this project's own numbering (`current_season`) applied to a
  month. "Desmond bane march 17" resolves to 2026-03-17, and `game_log`, which
  honours `date`, answers **"game on 2026-03-17, 16 PTS vs OKC"**.

  The first cut of this refused instead, on the reasoning that picking a year
  the question never states is a guess. It is not: the season states it, and
  refusing threw away an answer the warehouse holds. What genuinely cannot be
  resolved still refuses, and the three cases are worth naming - a year the
  question *does* state wins over the season's ("november 11 2019"); a date
  that opens a window is a range, not a day, so "since January 31" is refused
  rather than answered with one game; and a career question spans twenty
  Octobers and fixes no year at all. February 31 is not a date either.

  They are read from the question text into the existing `situation` slot,
  never asked of the model - the same move `_validate_side` makes for the side
  of the ball, and for the same reason: a new slot in `ROUTER_SCHEMA` moves
  slots on unrelated questions, while a regex in `route()` costs no prompt
  tokens and cannot. No template lists `situation` in `HONORED_SCOPING`, so
  each of these now refuses and falls through to the agent, which is the
  ranking this project uses - a refusal beats a fluent wrong answer.

  Checked against 343 real questions (the 261-query feed plus the 83 routing
  corpus cases): 14 feed queries match and **no corpus case does**, so nothing
  that routes correctly today starts refusing. Separately, `_AGENT_ONLY` knew
  `q1` but not `1q`, so "Duncan Robison 1q log" was answered with a whole-game
  line; three more feed queries fixed by the mirror pattern.
- **A triple-double abbreviation is no longer read as three-pointers.** "luka
  td3s home" answered with his *points* per game at home, because `td3s` became
  `shot_value: 3`. The replay filed it under "condition dropped", which was the
  wrong diagnosis - the venue was read and honoured correctly, and the fault is
  the metric. Triple-doubles exist as a leaderboard metric, but nothing counts
  them for one player and the season table they live on has no venue dimension,
  so the question goes to the agent, which can derive them from box scores.
  Spelled out, "triple double" already routed correctly; only the abbreviation
  was unreadable.
- **Splits, streaks and with/without read the rebuilt box line too - they were
  the half that still called a rebuilt game a game he missed.** Reading the
  rebuilt lines landed for the per-game templates first; every template that
  decides *whether a player appeared* kept asking `player_box_stats` for
  minutes, which a rebuilt row does not have. So the same season answered two
  ways: `game_log` listed Anthony Davis's 68 games of 2015 while
  `player_splits` said he "was listed in 82 box scores in the 2015 regular
  season but did not play in any of them". It now answers 68 games, 34 home
  and 34 away.

  The teammate half was worse, because it answered fluently and no caveat
  marked it. `with_without` asked the same question of the *other* player, so
  every teammate in a rebuilt game read as absent and the game was filed on
  the "without" side: "Anthony Davis without Eric Gordon, 2015" returned a log
  padded with games Gordon played. It now returns 20 games, which is the
  ground truth, and the split reads 68 played to 14 out.

  Three guards carried the fault, not one - the SQL that decides a player
  appeared, the SQL that decides a game has no box score, and a Python filter
  picking his games back out of a group - and each is now perturbation-tested
  separately. A rebuilt row still has no minutes, so minutes are averaged over
  the games that carry them rather than counting a rebuilt game as zero, and
  the columns the rebuild gets wrong (`UNGATED_ON_REBUILD`: turnovers, fouls,
  threes, attempts, rebound splits) are blanked rather than averaged in - a
  split that reads 9 turnovers off a rebuilt line is the quiet version of this
  bug, not a fix for it. A warehouse with no `player_box_stats_filled` view
  falls back to the raw table and answers exactly as it did before.
- **The power index stored 25 of ESPN's 90 rows a season, and nothing said so.**
  `seasons/<s>/powerindex` is a *collection* on ESPN's core API, which answers
  `{count: 90, pageIndex: 1, pageSize: 25, pageCount: 4, items: [...]}`.
  `fetch_power_index` read `items` from one response, so every season kept
  ESPN's first page - 2024 held 25 rows covering **9 of 30 teams** - and
  `team_outlook` reported that as ESPN having no snapshot for most teams. A
  short page and a short dataset are indistinguishable, which is how this
  survived long enough for `DATA.md` to record the missing rows as ESPN
  "keeping only postseason teams". It holds all 30, in every snapshot.

  `ESPNClient.get_collection` reads a collection to its end, asking for a large
  page and then paging until it holds the `count` the response declares, and
  warning if it never does. `scripts/backfill_power_index.py` re-fetched
  2017-2026: **250 rows to 630**.

  **`get_json` now warns when it hands back an unexhausted page**, naming the
  URL and the page count. That is the part that generalizes: of the ten
  endpoints this project reads, only this one is a paged collection - the other
  two core-API calls are single resources, and the site and web APIs return
  nested documents - so the guard exists for the eleventh, which will otherwise
  look exactly as correct as this one did.

  `team_outlook` also breaks a snapshot tie explicitly now. 2018's preseason and
  regular-season snapshots are both stamped 2020-10-12, the day ESPN backfilled
  them, one minute apart (07:47Z and 07:48Z) - so the regular season sorts last
  today and no answer is wrong. Ordering by date alone rests the choice on that
  minute; the tiebreak states it, and covers the case where the two stamps match
  exactly.
- **The 2000 playoffs are complete, recovered from a source the pull never
  read.** Games are discovered from each team's schedule, and ESPN's schedules
  simply stop: the 2000 postseason ended on 2000-06-01, missing the whole
  LAL-IND Final, WCF Games 6-7 and ECF Game 6. Its daily scoreboard is a
  second, independent list and **has** those games, so a postseason pull now
  makes a second discovery pass over it once the schedule's games are on disk,
  scanning forward from the latest date stored. Nine games recovered
  (70 -> 79), and every team in that postseason now matches ESPN's own season
  totals exactly - the Lakers went from 15 games to 23.

  That the pass runs *after* the fetch is the whole fix, not a detail: the scan
  needs a date to work forward from, and on a first pull the only dates that
  exist are the ones the fetch just wrote. Run during discovery instead, it
  found 70 ids for 2000 and none of the six Finals games - correct on a tree
  that already held them, useless on a clean one.

  Postseason-only, and it costs a healthy season nothing: measured against
  2024, the scoreboard and the schedules agree on all 82 games. 2001 recovers
  only its Finals Game 5, because 23 days across that postseason's conference
  finals and Final return no events at all - so that season is declared
  `postseason_partial` and its answers now say what is missing rather than
  stating a short series as fact. A 2001 regular-season question is unaffected;
  that is a separate field for exactly that reason.
- **A traded player's combined season row is rebuilt from his own stints when
  ESPN's disagrees with them.** ESPN's career endpoint returns one row per team
  stint plus a combined row, and 19 of its 2,062 combined rows contradict the
  stints they claim to combine - 13 are a byte-copy of a single stint, and 6
  (1977-1983) are entirely NULL. Everything downstream *preferred* that row, so
  it was the line every answer used: "Eric Murdock 1995-96 stats" read **9
  games at 6.9 a game** for a season he played **73** games of, and now reads
  73 games and 647 points.

  `player_season_stats` is rewritten at load time, like the team-box repair, so
  the deduped view and the leaderboard's own dedup both see it rather than one
  being fixed and the other left reading the broken row. Every formula was
  fitted against the whole warehouse before use - zero of 15,573 rows disagree
  with any of them - and the two that could not be identified are refused
  rather than approximated: `avgMinutes` is NULLed on a rebuilt row (it is the
  one average with no season total behind it; a games-weighted mean is right
  81% of the time and box-score minutes 61%), and a ratio over zero turnovers
  is NULL rather than the `inf` DuckDB produces.
- **Per-game answers now read the rebuilt box line, and say that they did.**
  Where ESPN serves an empty box score, `player_game_log` carries the figures
  rebuilt from play-by-play, and `single_game_high`, `game_log` and
  `threshold_count` read them. "What was Anthony Davis's highest-scoring game
  in 2015?" went from *0*, to a refusal, to **43, on 2014-11-22 vs UTAH** -
  with the answer saying the figure is rebuilt rather than fetched. His 2015
  game log lists 68 games where it used to report none, and "how many 20-point
  games did he have that season" went from **none, with a caveat** to a real
  count that says how many of those games were rebuilt.

  Counting is additive by construction and was checked rather than argued: an
  empty line carries 0, so it can never clear a threshold of 1 or more. Over
  2013-2018, across all seven readable stats, no athlete's count fell by a
  single game and the league-wide totals rose (20+ point games, 15,978 to
  18,488).

  Deliberately narrow, on measured grounds. Only the stats a rebuild gets right
  are read (`REBUILT_STATS`): per player-game against the 22,646 games of 2015
  whose real box score survived, free throws are exact to 0.0003, blocks 0.002,
  rebounds and assists 0.004, field goals made 0.005, steals 0.009 and points
  0.021 - but turnovers 0.080 and fouls 0.181, so those two are refused. Ask
  for a player's fouls in an empty season and the answer says the lines exist
  and were held back, rather than implying the data is missing.

  **Season totals are not read anywhere**, and that is the same measurement
  seen from further away: a season is exact only when the net error over every
  game is zero, so it lands right about half the time, and the error scales
  with games played - a right total averages 31.6 games, a wrong one 55.6.
  `minutes` is never invented; it prints blank on a rebuilt row, and the log
  says so beneath the table.
- **A row narrower than the rows after it no longer truncates the whole file.**
  `storage.write_rows` passed its rows straight to `pa.Table.from_pylist`, which
  takes the Parquet schema from the FIRST row and silently drops every key only
  later rows carry. Reproduced in isolation: `[{"season": 2014}, {"season":
  2016, "points": 299}]` writes a file with no `points` column at all, while the
  same two rows reversed keep it. Nothing raises, and the loss is permanent -
  the value never reaches disk.

  It fired on real data because ESPN's career endpoint leaves a season out of
  its `totals` category when the player scored nothing: a career whose
  *earliest* line is a scoreless one-game stint parses to a 26-key first row
  followed by 51-key ones. Five files were written that way - Seth Curry's among
  them, whose 2014 opens with a single game for Charlotte - each losing all 25
  totals columns for that player's whole career, which is why those players were
  missing from career and totals leaderboards entirely.

  Rows are now widened to the union of their keys before writing, and
  homogeneous rows are returned untouched so the common path - about 218,000
  files a pull, almost none of them ragged - pays nothing.

  This is older than the season-totals work and is not a regression from it:
  measured against the 2026-09-11 warehouse, no row that had a total then is
  NULL now, and 185 were repaired. The five files on disk are still truncated
  until they are fetched again through the fixed writer.
- **The warehouse answers from the rebuild where the stored line is empty.**
  New view `player_box_stats_filled`: `player_box_stats` with the rebuilt
  figures dropped into the 21,169 empty lines, under the stored table's own
  column names so it is a drop-in, plus a `reconstructed` flag marking exactly
  those rows. Anthony Davis's 2015 - stored as 82 games of zeros - reads 68
  games and 1,656 points through it, beside 14 rows correctly left as
  did-not-play. ESPN's own season table says 68 and 1,656.

  Three things it will not do. It never substitutes into a real line, so a
  player who genuinely scored 0 keeps his 0. It never invents `minutes`, which
  play-by-play cannot recover. And it drops the stored `plusMinus` on a
  substituted row rather than passing it through: that column looks like
  surviving data - it is not NULL, unlike every stat beside it - but across all
  21,169 rows it takes exactly one value, 0, and every team-game sums to 0.0.
  It is the same fabricated zero as the stats, wearing a different face.

  Neither view is in `KNOWN_TABLES`, so the SQL agent reaches neither. A
  substituted figure carries an obligation to say it was rebuilt, and an agent
  writing its own SQL has nowhere to put that.
- **2018's team box scores held their values under the wrong column names, and
  now hold their own.** Every non-empty 2018 row - both season types - was
  shifted: `assists` held the game's blocks, `steals` its turnovers, `blocks`
  its fouls, `fouls` its flagrant fouls, `fieldGoalPct` its FT% and
  `freeThrowPct` its 3P%. Per row, not on average: `assists` equalled the
  player-box block sum in 2,134 of 2,134 regular-season rows and the real
  assist sum in 1. Separately, the team `turnovers` column is 0 in every row up
  to 2012 and `teamTurnovers` holds a copy of `totalTurnovers` there rather
  than the handful of turnovers charged to a team.

  Both are ESPN's, and neither is fixed by refetching - a clean pull reproduced
  `team_box_stats` exactly, and the seasons either side of 2018 come from the
  same code and the same column order with the right values. So the correction
  is made at load time, in the new `fetch/team_box_repair.py`, from the game's
  own player rows: a team's assists ARE the sum of its players' assists, and
  ESPN agrees, its own team column equalling the player sum in 2,134 of 2,134
  rows in 2017. The two percentages are recomputed from the made and attempted
  columns beside them, which are right, rounded the way ESPN publishes them.

  What a user sees: `conditions._TEAM_LINE` reads `AVG(t.assists)`, so every
  2017-18 team split and with/without table reported the team's blocks as its
  assists. Boston's home assists go from 5.2 a game to 23.7, the Lakers' from
  4.8 to 24.4, Golden State's from 7.9 to 30.1; across the league, 4.8 to 23.0.

  Columns with no source are NULL rather than left holding another statistic:
  the player box has no flagrant fouls, technicals, points in the paint or team
  turnovers, so 2018's `flagrantFouls`, `technicalFouls`,
  `totalTechnicalFouls`, `totalTurnovers` and `pointsInPaint` (which is -1 in
  every row) are cleared, as is `teamTurnovers` before 2013. A wrong value that
  reads as a real one is the failure this project keeps producing, and nothing
  in `src` reads any of them.

  An empty team-game stays empty. Every Chicago and New Orleans game from 2013
  to 2018 has an all-NULL team row beside player rows listing everyone as
  having played with no minutes and every stat zero, and summing those would
  turn "ESPN has no box score" into "this team recorded no assists". A row is
  repaired only if it is non-empty AND its player rows carry minutes - fully
  corrected or fully untouched, never half of each - which also leaves alone
  the 117 all-NULL team rows whose player rows are real (Vancouver 1996,
  Chicago 2000), a different fault with a different fix.

  `scripts/check_team_box.py` verifies all of this against a built warehouse,
  the way `check_coverage.py` does for the coverage floors.
- **A box line rebuilt from play-by-play, for the games ESPN serves empty.**
  New view `player_box_stats_reconstructed`, built at load time over the 1,024
  events (of 1,025, all in 2013-2018) that have an empty box score and surviving
  plays. No other ESPN source has these numbers: the CDN box score on a
  different host serves the same zeros, the core API exposes no per-game athlete
  statistics, and the athlete gamelog omits the games - which also shows the gap
  follows the franchise, since Derrick Rose reads 0, 0, 0, 1, 61, 25 across
  2013-2018 and Aaron Brooks 51, 65, 0, 1, 60, 26, each zero exactly in his
  Chicago years.

  It is kept deliberately apart from `player_box_stats`: its own view covering
  only the empty games, snake_case columns so a derived value never looks like a
  fetched one, and absent from `KNOWN_TABLES` so the SQL agent can neither read
  nor describe it. No template reads it. A player who appears in no play is
  absent rather than zero, because a zero that reads as a real performance is
  the exact bug this whole area is about.

  Accuracy is documented per column on the module, and is not uniform: free
  throws 100%, blocks and rebounds 99.8%, assists 99.6%, field goals 99.5-99.6%,
  points 98.3% per game - but season totals are exact only 51.7% of the time
  (within 2, 72.2%), biased low, and 2016 is much the worst season. Minutes and
  plus-minus cannot be recovered at all.
- **A zero from an empty box score can no longer win a single-game high.**
  "What was Anthony Davis's highest-scoring game in 2015?" answered "0, on
  2014-10-28 vs ORL" - fluent, dated, and false. Every Chicago and New Orleans
  box score from 2013 to 2018 is stored with every
  player listed as having played, no minutes, and every stat 0. Those lines
  hold `0` rather than NULL, so they passed the "is not NULL" test beside them,
  and where a whole team-season is empty the maximum over it is one of the
  zeros.

  `single_game_high` now reads only lines with minutes, the same line
  `_played()` already drew in `conditions` - which is why streaks, splits and
  with/without were never affected. Measured against the warehouse, no
  unaffected answer moves: Davis's 2019 high is still 48, the 2015 league high
  is still Kyrie Irving's 57, and Stephen Curry's 2015 high is still 51.

  The refusal it leaves says which fact is missing. "He has no games" is false
  of a player who played 68 of them, so a season whose box scores are all empty
  now answers "no 2015 regular season games **with a box score** in the
  warehouse", and the note that follows gives the count and the years.
- **Rows that are not games are no longer counted as games.** `games` holds
  three kinds of row ESPN serves alongside the real ones, and each read path
  filtered a different subset of them, so the same warehouse answered the same
  question differently depending on which template got it. "How many times did
  the Mavs play the 76ers in 2003" answered 3 for a season holding 2 - one game
  stored under both `230104006` and `400222658` - and 1999-2000 matchups
  counted 0-0 placeholders as meetings nobody won.

  There is now one filtered list, `real_games`, built at load time
  (`fetch/real_games.py`) and read by `head_to_head`, `conditions`,
  `team_metrics`, the team game log and `team_quarter_points` alike. It drops
  151 of `games`' 43,494 rows: 134 placeholders scored 0-0 with no winner, 23
  team-slots naming an id no franchise has, 11 phantoms carrying a winner but
  no box score and a date-only stamp, and the one same-day duplicate left after
  those. Measured against the warehouse, the playoff records it corrects land
  on the real ones - Orlando 1995 from 11-11 to 11-10, Seattle 1997 from 7-7 to
  6-6 and 2000 from 2-6 to 2-3, Phoenix 1999 from 0-4 to 0-3, Portland 1999
  from 8-6 to 7-6 - and Miami stops having a 1995 postseason it never played.
  Chicago's 1999 game list falls from 100 rows to the 50 its standings line
  says it played, and 2000's from 162 to 80.

  Two things it deliberately does not do. It does not drop a game that is
  simply older than the box scores: every 1988-1992 game is stored date-only
  with no box score, so the phantom rule fires only where that season and
  season type have box scores at all. And it does not collapse season 1993,
  which is a phantom SEASON rather than a phantom row - that stays with
  `coverage.py` and the cross-season `QUALIFY` in `TEAM_GAMES_SQL`, which are
  the only things that can tell it from a real season.

  **Needs a `data load`**: the list is built at load time, so a warehouse built
  before this change does not have it.
- **A season line served with no totals is repaired from a second endpoint.**
  246 rows in `player_season_stats` carried `avgPoints` 15.0 beside a NULL
  `points`, so every career sum and totals leaderboard was silently short and
  19 players - Seth Curry among them - were dropped from the career scoring
  list outright, because every one of their season rows was NULL.

  The cause was two faults stacked, and only one is ESPN's. 53 of the 107
  affected career files were served with **only** an averages category, and the
  pull wrote that as-is and checkpointed it, so no later pull ever looked at
  them again. The "a refetch does not fix it" recorded against this was sound
  when it was measured - the surviving 2026-09-11 warehouse holds the same 246
  NULLs - so what the two measurements together show is that ESPN's answer
  CHANGED between 11 and 14 September. ESPN's own fault is the
  smaller half: even a complete payload omits a line from its `totals`
  category when the player scored nothing (55 of the 62 remaining rows) or when
  it is a traded player's combined row (the other 7).

  `fetch_player_season_stats` now repairs such a line from
  `player_season_totals_url`, the core per-season endpoint, before writing the
  file. Fetched rather than derived, and the difference is measurable:
  `avg × gamesPlayed` reproduces a known total exactly only 49% of the time
  (14,464 of 29,534 rows, worst error 4), because the `avg*` columns are
  rounded to one decimal. Seth Curry's career is the case for reading the real
  number rather than trusting that spread: derived, it comes to 5,547.4999 -
  right on the coin-flip, and 5,547 only because `round` went that way.

  The per-season endpoint has **no team dimension**, which is the whole
  difficulty: it answers a traded player's combined figure against every one of
  his stints, so David Wood's 21-, 4- and 37-game 1995-96 rows all come back
  208 points. `fill_missing_season_totals` therefore matches on games played
  and fills exactly one row per season, refusing outright when two rows tie -
  a stint that took the combined figure would read 208 points in 21 games with
  nothing anywhere to say it was wrong.

  Measured against the warehouse: 110 requests repair 226 of the 246 rows
  (123 from the career endpoint alone, which now serves totals for 87 of the
  107 files, and 103 from the per-season one). The remaining 20 are 14
  one-game lines that scored nothing and the 6 all-NULL combined rows from
  1977-1983, which ESPN answers 404 for. Career-leaderboard drops go from 19
  players to 0. Backfilled with `scripts/backfill_season_totals.py`.
- **A single-game high keeps the player the question named.** "most points
  curry scored in a game this season" came back from the router as
  `single_game_high` with no player slot at all, and the answer was the
  league's high - Bam Adebayo's - to a question about one man. The player is
  optional for that template (an empty slot means the league), so nothing
  downstream restored it, and `players_named_in` could not: "curry" is six
  players and it refuses to guess between them.

  The subject is now read from the question's grammar - a name before a scoring
  verb, or carrying a possessive - and handed to normal resolution, which asks
  "did you mean Seth Curry or Stephen Curry?". A word scan could not do this:
  "best" is Travis Best, "game" is Jaron Blossomgame, "high" is Haywood
  Highsmith and "single" is four players, so scanning would answer "the highest
  scoring game by a player this year" about somebody. Read from the text, so
  `ROUTER_PROMPT` and `ROUTER_SCHEMA` are unchanged.
- **Per-game leaderboards for points, rebounds, assists, steals and blocks
  apply a games minimum.** These five ranked every board unqualified, so the
  fewest games was the easiest route to the top of one: "who led the league in
  rebounding in 2001" answered Danny Fortson, who played 6 games, where
  Dikembe Mutombo led it over 79, and "who led the 2023 playoffs in scoring"
  answered Kawhi Leonard on 2 games rather than Devin Booker on 17. Measured
  over 1994-2026, two regular-season boards and 15 postseason ones were led
  from under the floors. They now use the same 20 games, and 5 in the
  postseason, that every newer per-game metric already used, and the answer
  names the qualifier it applied ("minimum 20 games") the way a percentage
  already named its attempts.

  Counts are deliberately left alone. A season total, a double-double count
  and a cumulative NetPoints figure need volume to rank at all, and no board
  of one was ever led from under these floors - the two triple-double boards
  that were are right, since nobody records more triple-doubles than he plays
  games. Career leaderboards already qualified and are unchanged.

  The five are now built by the same helper the newer per-game metrics use, so
  "a per-game metric" has one definition rather than two that can disagree,
  and a test over the whole registry fails if another arrives without both
  floors. That is what was missing: the omission read as deliberate, because a
  metric naming a qualifying column and no threshold is how this registry says
  "rank this unqualified".
- **Fix: "without X and Y" was answered about X alone.** The router read only
  the FIRST name out of a "without" or "with" phrase, so "Celtics record
  without Tatum and Brown" arrived as `without='Tatum'` and "Lakers record
  without Lebron and AD this season" as `'Lebron'`. The answer then covered the
  games one of the named players missed and said nothing about the other: a
  narrower question, answered fluently, with the dropped name nowhere on the
  page. Real questions have this shape - the StatMuse feed has "hornets record
  when brandon miller and lamelo and knueppel play this year".

  `router._names_after` now reads every name the phrase holds, joined by "and",
  "or", "nor" or a comma, and the three templates that honour `without`
  (`with_without`, `game_log`, `player_stat`) require all of them: a game
  counts as "without" only where NONE of the named players played, and
  `with_without`'s "with" row only where every one of them did. The games in
  between - one played, one sat - go on the other row, which is what stops a
  two-player question being answered about one player. `with_without` counts
  only the time the named players were all on the same team, as it already did
  for one, and its rows name which side is which ("Tatum and Brown out"
  against "Tatum or Brown played").

  Read out of the question text, so `ROUTER_PROMPT` and `ROUTER_SCHEMA` are
  unchanged (both hashed before and after) and no other question's routing can
  have moved - the same lever `_validate_season` and `_validate_side` use. The
  phrase parser also learned the question words ("how", "what", "who"...) as
  name terminators, so "without Tatum and how many wins" still reads one name
  rather than making a teammate out of the tail of the sentence.

  The `without` and `with_player` slots are lists of names now; a bare string
  is still read as one name (`entities.teammate_names`), since slot values are
  advisory everywhere else here. `player_stat` and `game_log` report
  `data["without"]` as a list, and `with_without` gained `data["teammates"]`.
  `scripts/check_routing.py` gained the two-name case, and the `ISSUES.md` P1
  entry it came from is closed.
- **Docs: three stale comments describing a gap `period_split` already
  closed.** `router.py` (the `_HALF_WORDS` comment and the one above
  `_is_team_quarter_points`) and the `team_quarter_points` docstring in
  `templates.py` still said a player's quarter or half had no template and
  would need a fragile plays-table `LAG()` derivation - true when written, and
  false since `31fe043` added `period_split`, which reads `shot_chart`
  instead. Comments and docstrings only; no behavior changed.

## 2.1.0 - 2026-09-11
The largest release so far on the query side. It adds eight new kinds of
question, and most existing ones can now be narrowed the way real questions
narrow them. The headline changes are below; each is detailed in its entry
further down.

- **New question types.**
  - A team's numbers, team rankings, and a team's outlook (BPI, playoff and
    title odds).
  - A player's splits: home and away, starter and bench, wins and losses, and
    by month.
  - A team's record, or a player's line, with and without a teammate.
  - A team's record when a player reaches a number.
  - The games two players played against each other.
  - Winning and losing streaks.
- **Questions narrowed the way people ask them.** A player's games and
  averages against one team, at home or away, over a career, or without a
  teammate. Career leaderboards for counting stats, averages and shooting
  percentages (usage, true shooting, eFG% and NetPoints have no career
  ranking). Career highs and career counts.
- **Names resolved from what the question says.** A name the router invented,
  dropped or completed is checked against the question. An ambiguous surname is
  narrowed to the players who played in the season asked about, and every match
  is considered, not just the first ten alphabetically.
- **Answers that were wrong, now right.**
  - True shooting and eFG% leaders qualify on attempts.
  - Shot distances are measured from the rim, not from a point 5.25 feet
    away from it.
  - A postseason before 1994 is found by the year it was played.
  - ESPN's copied postseason lines are dropped.
  - Every game is dated by the day it was played.
  - Per-game NetPoints rows land on the right game.
- **Narrow questions no longer get broader answers.** Some filters no template
  can apply yet: a playoff round, a season range, "under N", back-to-backs. A
  question with one of these now goes to the slower agent instead of being
  answered by a template about something else. A season the warehouse cannot
  reach is refused, with the reason.
- **Four holes an outside review found are closed.** Among them, the agent's
  SQL connection can no longer read the disk, and the web server no longer
  shares one conversation between every browser.

Known gaps and data faults are listed, ranked, in `ISSUES.md`. Among them:
every Chicago and New Orleans game from 2013 to 2018 but two has an empty box
score, and the 2000 and 2001 playoffs stop before the Finals.

- **A year before 1990 is read from the question.** The question text and the
  router's `season` slot both discarded any year below 1990, a floor that
  predates `coverage.py`. So "who led the league in scoring in 1980" was
  answered with the current season's leaders, and "Bulls record in 1985" with
  their current record. The floor is now the league's first season (1947), in
  one constant instead of two. A year below a table's first season reaches the
  coverage check and is refused, with the reason.
- **`--include-net-points-daily` says what it fetches.** Its help now names the
  per-game play-type table it also writes, and says it makes two requests per
  date, not one.
- **An ambiguous name is narrowed to the season being asked about before
  anybody is asked which one was meant.** "How did curry do against the
  celtics this year" answered `'Curry' matches more than one player - did you
  mean Dell Curry, Eddy Curry, JamesOn Curry, Michael Curry or Seth Curry (1
  others also match)?`. The warehouse holds six Currys, the list was sorted by
  name and the sentence names five, so the one it left out was Stephen - and
  four of the five it did name never played in 2026. It now asks `did you mean
  Seth Curry or Stephen Curry?`, the two who did.

  Every template that resolves a player (`player_stat`, `player_compare`,
  `player_history`, `player_netpoints`, `game_log`, `shot_distance`,
  `single_game_high`, `threshold_count`, `player_splits`, `with_without`,
  `record_when`, `player_matchup`, `streaks`) now narrows the candidates to
  those with a row in the table its answer is read from, for the season or
  span it will answer about - the rule charts already followed. A career has
  every season in scope, so it eliminates nobody who ever played, but it names
  whoever plays now first. It eliminates and never chooses: one survivor
  is the answer because nobody else has a row to answer from, and two or more
  are asked about, as Seth and Stephen still are. Three edges are deliberate.
  A name matched in full is not narrowed, so "Gary Payton" in 2026 is still
  told the father has no numbers rather than given his son's line. When
  narrowing eliminates everybody, the question is asked exactly as before. And
  `player_history` narrows over every season up to the one it is anchored at,
  since a history through 2026 still has Dell Curry's seasons to answer with.
  `player_netpoints` keeps a candidate with a row in either of its two tables,
  which disagree about who they hold (63 player-seasons are in the totals
  only, 8 in the fingerprint only). `_resolved_player` now requires the table,
  so a template cannot resolve a name without saying where its answer comes
  from - mypy refuses it.

  The cap was the other half. `find_players` returns the first ten matches
  alphabetically, and 71 name words match more than ten players ("Williams"
  matches 62), so narrowing that page was choosing by alphabet - which the
  chart path had been doing. In 2026's shot charts, 23 names drew one player
  while others matching the name also had shots on a later page: "Davis" drew
  Anthony Davis with JD Davison, Nigel Hayes-Davis and Trayce Jackson-Davis
  eligible, and in the fingerprints "Brown" drew Bruce Brown with two more
  Browns who had one. Narrowing now reads every match (`find_players(...,
  limit=None)`), on both paths and for the teammate a "without" names - which
  was narrowed to his teammates over the same first page - and those names
  ask. A clarification also
  names every candidate from the season asked about rather than counting any
  away (`Ambiguous.active`, passed to `clarification`) - 2026 has 14 players
  surnamed Williams, and all of them are named - and a history names whoever
  reached its last season first, so "Curry's scoring over the last 4 seasons"
  lists Seth and Stephen ahead of Dell. What the "others also match" count
  covers is now only ever players who could not be the answer, and it says "1
  other" when there is one.

  Measured against the warehouse, over 6,325 player names, name words and
  nicknames under nine template scopes: every one of the 5,349 that resolve to
  a single player today resolves to the same player. Of the 976 that ask, for
  2026 season lines 276 now resolve to the one candidate with a row, 191 ask
  about fewer players, 484 have nobody in the season and ask as before, and 25
  ask about as many or more - players the first page had hidden. No
  clarification in any scope counts away a player from the season asked
  about. Over the `check_routing.py` corpus, routed once and answered before
  and after through the agent's own slot pipeline, one answer changed - "What
  was Curry's first game of the season?" still asks, now between Seth and
  Stephen - and every name that resolved still does. Charts do change names
  that resolve today, and on purpose: besides the 23 above, 8 names in 2026's
  shot charts drew a best match with no shots while several matches had them
  ("Bob", "Marcus", "Scott") and now ask, and 4 draw the one match with data
  instead of a best match without.

  The extra work runs only for a name that matches more than one player: 2-9ms
  more for one season ("Curry" to "Williams") and up to 16ms for a history
  span, warm, against a question that spends about 3s in the router.

- **One player "compared" with a team is answered as his games against it.**
  "compare curry vs the celtics this season" arrived as `player_compare` with
  the Celtics as the second "player". Once they became the `opponent`,
  `player_compare` - which reads season lines only - refused it, and the
  question fell through to the agent while `player_stat` answers it exactly. It
  goes to `player_stat` now; two players and a team stay a comparison, and
  still refuse the opponent rather than comparing whole seasons. Checked by hand
  against the box scores: 22.5 / 5.0 / 6.5 over Curry's 2 games against Boston
  in 2024-25, and for 2025-26 "none of them", which is right - he missed both,
  inside a gap in his log from 31 January to 6 April.
  - A team in `players` is recognized by its LAST word being a nickname. The
    `player_matchup` reroute matched one anywhere in the name, which sent
    "magic johnson vs larry bird head to head" to `player_stat` - which refused
    it - instead of the head-to-head template. All 30 team names end in a
    nickname, and none of the warehouse's 3,080 player names does.
- **The team a player's question plays against is the opponent, whichever
  slot the router files it in.** "compare curry and lebron vs the celtics" came
  back with `team='Boston Celtics'` beside the two players. `scope_from_question`
  left it there - a team already in `team` is how `head_to_head` carries its
  own side - so no `opponent` was set, `check_scope` had nothing to refuse, and
  nothing read the slot. With "steph curry" the answer was their whole 2025-26
  lines, Curry's 43 games beside LeBron's 60, when Curry played none of them
  against Boston. Where the template reads a player and one is present, that
  team now moves to `opponent`, so `player_stat` and `game_log` answer over
  those games and every other template refuses. `head_to_head` and the team
  templates are untouched.
- **`threshold_count` answers a surname only one player with games that season
  has.** "How many 30-point games did Curry have last season" asked which
  Curry was meant even when only one of them played; the name is now narrowed
  to players with a box score in the season asked about, the way the charts
  already narrowed it, and answered when exactly one is left. Two who both
  played are still asked about, even when only one of them reached the
  threshold - letting the count choose the player would be the prominence
  tiebreak again. The narrowing reads every match rather than `find_players`'
  first ten, as the entry above describes: narrowing only that page would
  have answered "Williams" in 2023 with Alondes Williams, where 14 players
  matching the name played.
- **True shooting and effective FG% leaderboards qualify on attempts, not
  games.** Twenty games was the whole qualifier, so "best true shooting
  percentage last season" was led by Kai Jones at .804 on 109 shots, with
  Patrick Baldwin Jr.'s 35 third. `ts_pct` now needs 550 true-shooting
  attempts (FGA + 0.44 FTA) and `efg_pct` 480 field-goal attempts, floors
  checked against StatMuse's published 2025 and 2026 top 15s: the top ten now
  match in order for TS% in both seasons and for eFG% in 2025. 2026's eFG%
  differs by the rule itself - StatMuse qualifies on 300 *made* field goals,
  which shuts out Sam Merrill and Isaiah Joe, who have the attempts. The
  reasoning, and the band each floor was picked from, is above the two
  entries in `query/metrics.py`.

  Their postseason floors are 67 and 59, the same rate over 10 games instead
  of 82, where 20 games had left only the conference finalists and dropped
  Jarrett Allen's .792 over nine games for Isaiah Joe's .676.
  `get_leaderboard` returns `min_sample_column` beside `min_sample_applied`,
  since 20 is games for one metric and 550 is attempts for another.

  `player_season_advanced_stats` gains `field_goals_attempted` and
  `true_shooting_attempts`, summed from the same box scores as the
  percentages. An existing warehouse needs an `association data load` (any
  `--tables` subset: the views are rebuilt on every load) before the view has
  them; until then these two leaderboards fall through to the agent.

- **Slots the model put in the wrong place are read from the question.** The
  last run over the StatMuse questions found one more wrong answer and a set of
  refusals, each from a mis-filled slot:
  - "kevin durant true shooting percentage career" arrived as
    `stat='threePointFieldGoalPct'` and was answered with his 3-point
    percentage. True shooting, eFG% and usage named in a question are now read
    from it, and `player_stat`, which holds none of them, refuses.
  - An `order` and a `limit` of one the question never asked for ("evan mobley
    avg against bucks", "Celtics record without Tatum") are dropped rather than
    refused.
  - "luka ft log" goes to `game_log`; a `player_matchup` whose "players"
    include a team goes to that player's games against it; a `player_history`
    with no stat named, or asked against a team, becomes the `player_stat` line.
  - "worst record" ranks the league, "all-NBA" and "worst" are no longer read
    as teams, and a team metric named in the question ("lowest defensive
    rating") wins over the one the model invented.
  - `head_to_head` takes an `opponent` as its second team, and keeps reading
    names until two different teams resolve.
  - A player's name in the `team` slot becomes the subject ("Podziemski game
    log without curry").
- **Three smaller answers that said something false.**
  - The web page's game log showed "L" for a game with no recorded winner (134
    since 1994). It now shows a dash; the template already sent `won: null`.
  - A single game's NetPoints printed its UTC date, a day late for any evening
    tip. So did a single-game fingerprint's title, a shot distance's "in his
    most recent game" note and a team's quarter-by-quarter list. Each now
    prints the day the game was played, from one shared
    `season.eastern_date` - the query package had held two copies of it.
  - The fall-through agent's knowledge base still told it the hoop was at
    (25, 5.25) and that free throws have no coordinates. Both are wrong: the
    rim is at (25, 0), and free throws carry a position under the rim through
    2018. So its hand-written distance SQL was three feet short. The entry now
    matches `court.py`, at no extra length against the preamble's token budget.

- **Faults found while building the templates below, fixed where the data is
  read.** Every agent that built a template hit one of these, and each one
  produced a wrong answer that nothing flagged.
  - **Playoffs before 1993-94 were a year off.** ESPN files every season before
    1993-94 under the year it started. The postseason games labeled 1990 end
    with the 1991 Finals, so "the 1991 playoffs" returned 1992's.
    `head_to_head`, `team_quarter_points` and a team's `game_log` now select a
    postseason by the calendar year it was played in, as `team_record` already
    did, and exclude the phantom 1993 label. The 1987-88 playoffs are not in
    ESPN's archive at all, so the postseason floor for `games` and
    `team_box_stats` is 1989, not 1988, and that refusal now gives its own
    reason instead of the regular season's. `scripts/check_coverage.py` counts
    those postseasons the same way.
  - **Copied postseasons.** ESPN's career endpoint files some regular seasons
    a second time as the postseason; Eddy Curry had 527 "playoff games".
    `player_season_stats_deduped` now drops a postseason line that claims more
    than 28 games or repeats that season's regular-season games and points.
    That removes 340 of 7,845 rows and keeps every real run checked.
  - **2003 shots are partial.** 2003's play-by-play is complete, but only 986
    of its 1,190 games have located shots, so `shot_chart` caveats 2003 as
    well as 2002.
  - **Four more narrowing slots the router reads from the question text.**
    None is honored yet, so a question carrying `below` or `situation` now
    falls through to the agent where it used to be answered as a different
    question:
    - `below` ("games with under 14 FTA"): threshold_count answered 14 or more,
      the inverse.
    - `situation` (back-to-backs, overtime, a month, a conference, the All-Star
      break): `team_record` answered each with the whole season's record.
    - "fastest" and "slowest" now rank the right end of a team leaderboard.
    - A team line or team streak that names no stat no longer carries the one
      the model filled in.
  - **"last 8 games vs pistons"** with no season named now reaches back across
    seasons for the last eight meetings. It used to stop at the current
    season's four.
  - **Team nicknames** ("Sixers", "Cavs", "Mavs") resolve to teams instead of
    falling through.

- **A player's games against one team, his recent form and his career are
  answered now, where the entry below made them refuse.** `game_log` and
  `player_stat` honor `opponent`, `venue`, `span` and `without`, and
  `player_history` honors `span`. Run against the real warehouse:

  | Asked | Now |
  | --- | --- |
  | "jaylen brown last 8 games vs pistons" | his 4 games against Detroit this season, saying there are only 4; with "career", his last 8 meetings (2024-2026) |
  | "evan mobley avg against bucks" | 21.3 / 9.7 / 4.7 over the 3 games he played against them - a fourth was a DNP |
  | "Podziemski game log without curry" | asks which Curry, since Seth joined the Warriors in December; with Stephen named, the 39 games Stephen missed |
  | "Jokic career averages" | 22.2 / 11.1 / 7.5 over 810 games, 2016-2026 |
  | "What is Jokic's 3 point percentage this season" | 38.0% (112 of 295) - it used to fall through to the agent |

  - A player's log lists the games he played, adds the columns a named stat
    needs (FTM and FTA for "luka ft log", FGM and FGA for "kyle kuzma last 7
    games fgm") and ends with per-game averages over exactly the rows listed. A
    real stat it has no column for is refused rather than dropped, and so is a
    `threshold`. `player_stat` refuses a `limit`: an average over the last N
    games is that log's average row, not the season line.
  - `without` means the teammate did not play - a did-not-play entry or no row
    at all, since Stephen Curry's 2026 is 43 rows and none of them is a DNP -
    while he was on the same team. There is no roster table, so that is read off
    his own rows: the season's start counts if he ended the previous one on that
    team, the end counts unless he was traded away, and a mid-season arrival
    counts from his first game. LeBron James's first 2026 row is 2025-11-19, and
    Austin Reaves's 16 games without him include the 11 before it.
  - An ambiguous `without` name is narrowed to the players who were actually
    teammates - elimination, as `narrow_to_available` does - and asks only
    between those.
  - A career is summed from `player_season_stats_deduped` as totals over games,
    never an average of averages. Michael Jordan comes back with 32,292 points in
    1,072 games and 5,987 in 179 playoff games, and Kobe Bryant with 33,643 in
    1,346: the real totals.
  - Narrowed questions are answered from box scores, so they carry the
    box-score floor: Jordan's 1990 season line still answers, his 1990 line
    against the Knicks refuses, and a career against an opponent says his box
    scores begin in 1993-94.
  - An empty answer names what is actually missing: "played 65 games in the
    2026 regular season, none of them vs the Denver Nuggets" rather than
    "no games found", and "was not his teammate" rather than "did not miss
    any".
  - Dates are the Eastern date a game was played on. `date` matched the UTC day
    before, so "Jaylen Brown's game on 2026-01-19" found nothing, and asking
    for 2026-01-20 found the 19th's game.
  - A team's log listed every 1993-94 game twice (164 rows for the Celtics'
    82). It joined `games` on event_id alone, and the phantom 1993 season shares
    those ids; it keys on season too now. A game with no recorded winner (134
    since 1994, mostly the 1999 lockout season) shows "?" and is left out of the
    record, where it used to be counted as a loss.
- **Box scores from 2013 to 2018 are missing about an eighth of their points.**
  About 13% of team-games in those seasons list every player as having played,
  with no minutes and every stat zero, so summing a season's box scores gives
  87% of ESPN's season totals (2017: 225,786 points against 258,855). The
  box-score answers above leave those lines out of every average and say how
  many they left out. `threshold_count` and anything else that sums
  `player_box_stats` over those seasons still counts them as games of zeros.

- **Career leaderboards, career highs and career counts, each saying whose
  careers they cover.** `leaderboard`, `single_game_high` and `threshold_count`
  now honor `span` "career" instead of refusing it:

  - "career points leaders" ranks career totals: LeBron James, 43,440, the sum
    of his season rows. A bare stat name reads as a total in a career and per
    game in a season, as it always did. A career average (`avg_points`) is
    games-weighted and needs 400 games (50 in the postseason).
  - A career high or count reads every box score since 1993-94 for one player
    or for the league.

  None of these is an all-time answer, and each says so. Players are discovered
  from box scores, which begin in 1993-94. So the pool is every career that
  reached that season, counted in full, and nobody whose career ended before it.
  Kareem Abdul-Jabbar is not in the warehouse. A player whose career began
  earlier gets that sentence *before* the number. Michael Jordan's highest game
  in these box scores is 55, and the answer says his career began in 1984-85
  first.

  A career with a year named is refused rather than read, because "in 2024",
  "since 2015" and "through 2010" all arrive as the same two slots. A franchise
  career list is also refused, and so is a career ranking by usage, true
  shooting or NetPoints.

- **Every stat name the router is taught now ranks by something.** Turnovers,
  minutes, fouls, the three kinds of make, and FG%, 3P% and FT% all fell through
  to the agent from `leaderboard`. There are 16 new metrics (`BOX_SCORE_METRIC_NAMES`):
  - season totals;
  - per-game rates, which need 20 games (5 in the postseason);
  - shooting percentages, qualified on attempts. The minimums are 400 FGA, 200
    3PA and 125 FTA a season, and 2,000 / 1,000 / 600 over a career.

  The qualifier is named in the answer. A percentage shows its makes and
  attempts ("47.8% (117 of 245)"). `rate` "total" asks for a season total
  instead of the per-game default. The new metrics stay out of the agent's tool
  description, which has about 120 tokens of headroom; the agent reaches them by
  name.

- **Four data faults now handled where these answers read the data.**
  - *Copied postseasons.* 437 postseason rows in `player_season_stats` copy
    the same player's regular season. Eddy Curry never played a playoff game
    and had 527 "playoff games". His 2006-07 copy (1,576 points) topped that
    postseason's scoring total, and a career playoff list put him second.
    Leaderboards now drop these copies.
  - *Empty combined rows.* A traded player's combined row can be empty, as
    Moses Malone's 1976-77 row is, so career sums use the per-team rows.
  - *Double-counted 1993-94.* A career over box scores starts at 1994. Starting
    at the 1993 phantom counts every 1993-94 game twice.
  - *Empty box scores.* From 2012-13 through 2017-18, 161-166 games a season
    have box scores with every line blank. A count or high that touches them
    now says how many it could not see.

- **`single_game_high` dates a game by the day it was played.** It printed the
  UTC date, which is a day late for any tip after 7pm Eastern. LeBron James's
  61 was on 3 March 2014, not the 4th.

- **`threshold_count` resolves a named player to one person.** It matched every
  name containing the words, so "Curry" counted Seth's games and Stephen's
  together.

- **Five templates for questions about games under a condition.** The intents
  were already in the router's schema with nothing to answer them, so every
  one of these fell through to the agent. They are shapes S4, S6, S10, S12 and
  S13 of the StatMuse research, and each answers both halves of its comparison
  side by side:

  | Intent | Answers | Example |
  | --- | --- | --- |
  | `player_splits` | per-game averages home/away, starting/bench, in wins/losses, or by month (all four when no split is named); a team's too | "Nikola Jokic home and away splits" |
  | `with_without` | a team's record in the games a teammate played vs missed, and a player's averages in each | "Celtics record without Tatum" |
  | `record_when` | a team's record when a player reached a stat threshold vs when he fell short | "Sixers record when Embiid scores 30" |
  | `player_matchup` | two players' meetings on opposite teams: record, averages, the latest games | "Andre Drummond vs Al Horford game log" |
  | `streak` | a team's longest winning or losing run, a player's longest run of games at a threshold, or the league's | "most 40 point games in a row" |

  The SQL is in the new `query/conditions.py`, and its module docstring records
  four facts measured against the warehouse that decide what the answers mean:

  - **"Played" is a box-score row with minutes.** A missed game is a DNP row,
    no row at all, or (2006-2012) NULL minutes and zeros beside teammates who
    played.
  - **A missing box score is unknown, not a missed game.** ESPN lacks about one
    box score in eight from 2013 to 2018, every player listed with NULL
    minutes. LeBron James played all 82 games of 2017-18 and has six of these,
    so read as absences they would have been "Cavaliers without LeBron" games.
    They are left out of both sides, end a streak, and are counted in the answer.
  - **Days are US Eastern**, the same fixed shift the NetPoints matching uses,
    so a 7:30pm tip is not filed under the next day's month.
  - **0-0 placeholders with no winner (1999-2002) are not losses.**

  "Without" means inside the teammate's time on that team: the stint of box
  scores from his first appearance there to his last, broken by a trade or a
  season with no box score at all. StatMuse's "Nets record without KD" counts
  decades of Nets games before he arrived, and that is the answer this avoids.
  Cross-checked against standings: every split sums back to the team's
  record (Celtics 2026, 43-23 without Tatum and 13-3 with him, is their 56-26).

  One data fact found on the way, and not fixed here: before 1993-94 the
  warehouse files a season under the year it began, so its "1990" postseason
  is the 1991 playoffs. These templates refuse a pre-1994 postseason season
  rather than answer it under the wrong year's label.

- **Real questions were being answered about something else, and now refuse
  instead.** 99 questions were run through the fast path to the final answer:
  the routing corpus plus 45 real StatMuse queries. Nine of the StatMuse queries
  came back fast, fluent and wrong:

  | Asked | Answered |
  | --- | --- |
  | "jaylen brown last 8 games vs pistons" | the Celtics' last eight games |
  | "Luka Doncic game log vs Lakers" | the Lakers' log |
  | "Knicks home record" | their overall 53-29 |
  | "career points leaders" | this season's scoring leaders |
  | "which team scores the most points per game" | the players' leaders |
  | "Podziemski game log without curry" | his whole log |
  | "rj barrett 4th qtr log" | his whole last game |

  Two more were answered for the postseason without mentioning it.

  Every one of these is the router answering a narrower question's slots with a
  broader template, so every fix is the same move this project already makes for
  `order` and `date`. The question text is read for what it narrows to, and a
  template that cannot honor that refuses (`check_scope`) instead of answering
  about everything:

  - `opponent`, the team after "vs"/"against", via
    `entities.scope_from_question`. That function also undoes the router's two
    ways of losing the player: putting his own team in `team`, or putting the
    opponent there.
  - `venue` (home/away).
  - `span` ("career", "all-time"; a "career high this season" is still that
    season's best).
  - `without` (a teammate).

  None of the four is in `ROUTER_SCHEMA`, so the model's grammar did not change.
  Two more slots are read the same way:

  - `season_type` now comes from the question, never the model. It was wrong in
    both directions: "Sga record 36 plus points" and "lebron vs kawhi 2015" came
    back as playoff questions, and "tatum stats in the 2024 finals" as a
    regular-season one.
  - "qtr", "q4" and "first half" now reach the agent like "4th quarter" did.

  A team ranking asked as a player ranking is sent to `team_leaderboard`.
- **Team questions have templates: records, season numbers, rankings and
  ESPN's power index.** "Knicks home record" was refused and "which team scores
  the most points per game" fell through, and `team_season_stats` and
  `team_power_index` were read by no template at all. Most of the work was
  finding out which of their numbers can be believed.

  - `team_record` honors `venue`, `opponent` and `span`, and answers a
    postseason instead of refusing one. A season's record and its home/road
    split are the standings' own; the "Home"/"Road" strings agree with a tally
    of `games` for every team-season from 1994 to 2026 once each era's
    neutral-site rule is applied (through 2024 a neutral-site game counts for
    its designated home team, from 2025 for neither). Anything else is tallied
    from `games`, which has to be cleaned first: 0-0 phantoms with no winner
    (50 in 1999, 82 in 2000), a second event id for a game already listed, 1993
    under two labels, and the NBA Cup final, a regular-season game no standings
    count - left out of the record and mentioned beside it. Cleaned, the tally
    matches standings for every team-season from 1994 to 2026. Postseasons are
    found by the year they were played, because `games` labels every one before
    1994 by the year its season started: the games labeled 1990 end with the
    1991 Finals. Where the game list and a team's own totals disagree - the
    2000 and 2001 postseasons hold 15 of the Lakers' 23 games and 10 of their
    16 - the answer says so. A conference is refused by name, since nothing in
    the warehouse says which teams are in one.
  - `team_stat` and `team_leaderboard` read a new whitelist of team metrics,
    `query.team_metrics`. There is no rating column, so offensive, defensive
    and net rating are derived, and neither input could be taken as stored.
    ESPN's `possessions` counts every turnover twice before 2013 (114 a game in
    1994, against a real ~96), so possessions are recomputed as
    FGA - OREB + TOV + 0.44 x FTA with the turnover column that is right in each
    era; from 2009 that reproduces ESPN's own figure exactly. Points allowed are
    summed from `games` and used only where that game count equals the team's
    own - the 2000 regular season fails it for 28 of 29 teams, and is refused
    rather than rated. Checked: the 2026 Knicks' defensive rating is 110.47,
    100 x standings' 9,030 points allowed over ESPN's 8,173.92 possessions.
  - `team_outlook` reads ESPN's BPI, which is sparse - 2026 has a play-in
    snapshot of 13 teams and a postseason one of 12, and no regular-season one -
    so every answer names its snapshot, date and size, and a team missing from
    it is told which snapshots exist rather than that there is no data. Where a
    team stands is counted within the snapshot, because ESPN's rank columns hold
    values like 26,058 before 2022.

  A second pass over the ten StatMuse queries the fast path still answered
  found four more cases of the same shape. None is answered about something
  else any more:

  - **A playoff round.** "tatum stats in the 2024 finals" was answered with his
    whole postseason: 19 games, where the Finals were five. Nothing in the
    warehouse records a round, so no template can honor one, and the question
    falls through to the agent (`check_scope`).
  - **A split asked of a template that is not about splits.** "Joe Ingles stats
    when starting vs coming off the bench" came back as his season minutes. It
    falls through to the agent too.
  - **A range of seasons.** "most 3 pointers made since 2020" became one season
    and a threshold of 0, and was answered as "the most games with 0+
    3-pointers". It falls through to the agent, and so does any zero
    threshold, which counts every game.
  - **A record asked as a count.** "Sixers record when Embiid scores 30" was
    answered with the league's 30-point games. It now goes to `record_when`, and
    the question's one named player is restored wherever that template needs
    one.

- **The router knows the new question shapes, at the smallest prompt that
  kept every existing question in place.** `ROUTER_PROMPT` gained the eight new
  intents and five worked examples: a game log against one opponent, splits,
  with/without a teammate, two players' matchup, and a streak.

  The first version of this, with a longer line per intent and twelve examples,
  was measured and cut back. It added about 720 tokens, raised the router's
  mean latency from 2.3 to 3.4 seconds, and broke three questions that had
  routed correctly on every earlier run, all of them fingerprint or shot-chart
  questions about Curry. That is the prompt-length sensitivity AGENTS.md
  describes. The shipped version adds about 300 tokens.

  Four facts the question states outright are now read from its text in
  `route()`, which costs no tokens and cannot move another question's slots:

  - A fingerprint is only a fingerprint when the question names one.
    "Plot Curry's threes from last season" routed to `fingerprint` under both
    prompt revisions.
  - A per-game threshold the model left out ("scores 30 points", "36 plus
    points") is read from the question; "3 point" is a shot type, not a
    threshold of three.
  - A "career high" asked with `player_stat` goes to `single_game_high`.
  - A `threshold_count` still without a threshold is a season ranking and goes
    to `leaderboard`. "who has the most threes" arrived with none and fell
    through.

  `check_routing.py` now applies `scope_from_question` the way `agent.py`
  does, so it asserts on the slots a template actually sees, and it gained
  17 cases from real StatMuse queries. On the shipped prompt 70 of its 71
  cases passed; the 71st asserted the literal team string the model chose
  ("Lakers" against "Los Angeles Lakers", the same team), which AGENTS.md
  says a case must not do, and now asserts only what changes the answer.

- **1993-94 player games were listed four times.** `player_game_log` joined
  `games` and `player_advanced_stats` on `event_id` alone, and ESPN files the
  1993-94 season's 1,185 events under both 1993 and 1994 (the phantom in
  `coverage.py`). So each of those player-games matched two game rows and two
  advanced-stat rows: 112,780 rows for 28,195 games. A game log or single-game
  high over those seasons repeated every row. The joins are now keyed on
  `season` too. Measured first: `games.season` equals `player_box_stats.season`
  for every row, so the extra key drops nothing.

- **Shot distances and shot charts were measured from a rim 5.25 feet from
  where the data puts it, and every "threes" or "twos" question read only the
  shots ESPN happened to label.** Two bugs in one pipeline, both of the kind
  this project keeps producing: fast, fluent, and about a different question.

  `court.py` put the rim at `(25, 5.25)`, on the assumption that
  `coordinate_y` starts at the baseline. It starts at the rim. Most shot
  descriptions carry their own distance ("makes 26-foot three point jumper"),
  and from 2002 through 2012 that distance equals `round(hypot(x - 25, y))` for
  all 1.3 million of them; later seasons agree to within a foot on 99.8%. So
  every distance came out short - Stephen Curry's 2026 threes averaged 23.6
  feet, inside a 23.75-foot line, where from the rim they average 27.6 - and
  the chart drew its court around the same wrong point, putting every shot
  5.25 feet nearer the baseline than it was taken and a typical three on or
  inside the arc. `HOOP_Y` is now 0 (the baseline sits at -5.25), the court is
  drawn in the data's own frame, and the geometry the rest of the pipeline
  needs lives beside it: `SHOT_DISTANCE_SQL`, `BEYOND_THE_ARC_SQL` and
  `HAS_POSITION_SQL`.

  One thing that looks like a counterexample and is not: from 2023-11-02 the
  descriptions run 0.64 feet short of the rim, as if it had moved a foot. The
  coordinates did not - the three-point line separates ESPN's own labels
  exactly as well after that date as before it (99.93%), and worse from a rim
  a foot out (99.72%). ESPN changed its prose, not its frame.

  Separately, `points_attempted = 0` means *unlabeled*, not zero points, and
  both `shot_distance` and the shot chart filtered on it as a value. It is 0
  for every shot of 2002 and 2003, 96% of 2022's, and 23-28% of the field
  goals of each season from 2004 to 2012 - every one of those a miss. "Curry's
  threes in 2022" charted 38 of his 751 attempts, all misses; his 2010 twos
  were 501 attempts at 72.3% instead of 763 at 47.4%; Kobe Bryant's 2003 threes
  were "No 3-point shots". `shotchart.SHOT_VALUE_SQL` now derives a value
  where ESPN left none: the label, else `shot_type` for a free throw, else the
  description where it says "three point" or "two point", else - through
  2012, whose descriptions name every three - a two, else the shot's position
  against the line. Counted against the box score's three-point attempts it
  matches in 99.6-100% of player-games in every season from 2003 on, and 99.3%
  in 2022. 2002 does not get there (99.05%, with 1.6% of its threes unnamed and
  a third of its unlabeled shots undescribed), so a two- or three-point
  question about 2002 is refused with that reason; 2003 and 2022, which rest
  mostly on the derivation, answer with a note saying so.

  Also: from 2002 to 2018 every free throw carries a position under the rim,
  so "has coordinates" never excluded them. An unfiltered chart drew them as
  shots, and Curry's 2010 shot distance averaged in all 200 of his free throws
  among 1,343 "attempts" (1,143 now). They are excluded by value. `(0, 0)`, a
  point on the sideline that 2002 uses for 7,109 shots, counts as no position.
  A free-throw chart is refused rather than drawn as a dot. The example chart
  in the README and `docs/usage.rst` is redrawn from the same game.

- **`--workers` now reaches the per-game NetPoints fetch.** It was the last
  serial loop in the pipeline, and it is S3 round trips end to end: measured
  on the tail of the re-derivation below, 12 dates a second through the pool
  against 0.3 one at a time. `--rate-limit` was never the lever here - it
  bounds what ESPN sees and these requests do not go to ESPN. The three
  lookups the workers share are built before the pool and never written to
  afterwards, the writes already go through `_write_rows` under `_state_lock`,
  and the daily client's lazy Cognito exchange is now built under a lock of
  its own, so eight workers do not each run their own.

- **Per-game NetPoints rows were landing on the wrong game, and sometimes on
  the same one twice.** NetPoints names each daily file for the US Eastern
  date the games were played on and publishes no ESPN id, so the pull
  recovered the game by matching `(team, date)` against ESPN's own `games` -
  whose `date` is a UTC tip timestamp, a day ahead for anything after 7pm
  Eastern. That left a choice between `date + 1` and `date`, and **both
  orderings are wrong for some real schedule**: `date`-first steals a
  back-to-back's first night, and `date + 1`-first - what shipped - steals the
  *next* night, which then claims the same game again out of its own file.
  Season 2026's `net_points_player_game` held 611 duplicated
  `(event_id, athlete_id)` pairs, every one disagreeing with its twin, and
  `net_points_player_game_fingerprint` inherited 17,630 duplicated triples.
  Nothing looked wrong: the counts were plausible and every event_id was a
  real game the player really played in.

  The rule now reads the game's own Eastern date off the timestamp instead of
  guessing which side of midnight UTC it fell on - `NetPointsGameIndex`, one
  exact lookup, since a team plays at most one game per Eastern date. A fixed
  five-hour shift rather than a real time zone: EST and EDT disagree about a
  tip's date only in the midnight-to-1am Eastern hour, which no NBA game
  starts in, and a fixed offset needs no tz database on the machine running
  the pull. A date holding two of one team's games now resolves to neither
  rather than to whichever row was read last - a team cannot play twice in a
  day, so that is ESPN's clock being wrong, and it had been silently shadowing
  126 keys. The old UTC window survives as a fallback for the four games in
  late February 2020 whose stored tip time is hours from when they were played
  (Detroit at Portland, a 6pm Pacific tip, is recorded as `2020-02-24T12:00Z`),
  where the date is right even though the time is not.

  Verified against the source's own box score, which is the check that settles
  it: the daily file carries `pts` beside the NetPoints values and the parser
  drops it, so it is free, independent evidence of which game a row belongs
  to. NetPoints' 2025-10-25 file gives Ryan Kalkbrenner 14 points and its
  2025-10-26 file gives him 4 - the old rule sent both rows to the game where
  he scored 4. `scripts/check_net_points_games.py` runs that over a season,
  and the query side's `max(t_poss)` subquery, which existed only to pick
  deterministically between disagreeing twins, is gone.

- **A fingerprint for a single game.** "Show me a fingerprint for steph curry's
  last game in 2026" now draws that game. The play-type split does exist per
  game - it is in a second file the pull never read, and the claim in the entry
  below that it was not published was wrong. ESPN Analytics puts two objects on
  each date: `NBA/netpts/<season>/<date>.json`, which this project already
  fetched, and `NBA/netpts/<season>/<date>_player.json`, which is long format,
  one row per player per game per action type, 31 types covering every category
  the season file holds plus nine it does not. It is what the site's own
  per-game awards are computed from ("Facilitator" for net points passing,
  "Corner Pocket" for corner 3s).

  Fetched into `net_points_player_game_fingerprint` under its own checkpoint,
  so a pull that already has the box-score half backfills only what it is
  missing. Stored LONG rather than wide: the season file's shape would be 93
  columns here and would change again the next time ESPN adds a category. The
  categories are normalized on the way in to the column prefixes the season
  file uses, over the `FINGERPRINT_CATEGORIES` map that already existed, so one
  skill list drives both tables.

  Two things about the plot are deliberate and measured. Its numbers are that
  game's net points, not a per-100 rate - over ~30 possessions a per-100 rate
  turns one made corner three into a league-leading season figure - and its
  percentiles rank against every player-*game* in the season rather than
  against season rates, since a season average is the mean of games like this
  one and nearly any decent game would land in the 99th percentile against it.
  The new `Unit` carries the labels with the numbers so a per-game plot is
  never captioned "per 100 poss". Verified against the warehouse: Curry's
  2026-04-12 headline reads +6.31 (+2.39 offense, +3.92 defense), matching
  `net_points_player_game`'s row for that game exactly - a cross-check from a
  different source file.

  A single game's percentiles take the MIDDLE of a tied block rather than the
  top of it. In one game most players are exactly 0.00 in most categories, so
  counting everyone a value is at least as good as read "attempted no hook
  shots" as beating everyone else who also attempted none: +0.00 came out at
  the 89th percentile and the radar drew a long spoke for a skill the player
  never used. Every number in that table was individually defensible, and only
  looking at the rendered plot found it. A season keeps the older definition,
  where exact ties essentially do not occur and it is what puts the league best
  exactly on the outer ring.

  A `date` is still refused, and now says why: the router supplies a calendar
  date and this picks a player's first or last game of a season, which are
  different questions.

- **A NetPoints pull no longer spends three quarters of its requests being
  refused.** Every date with a local game was fetched, back to 1994, but the
  bucket answers 403 for anything before 2018-10-16. Measured on this
  warehouse: 7,835 dates, of which 1,769 are in range.

- **`SkillValue` and `PlayerFingerprint` no longer name their fields
  `per_100`.** They hold a per-100 rate for a season fingerprint and a game's
  own net points for a per-game one, so `value`, `total`, `league_average`,
  `league_best`, `overall`, `offense` and `defense` say what is there without
  claiming a unit the numbers may not be in.

- **A fingerprint asked for one game says so, instead of drawing the season.**
  "Show me a fingerprint for steph curry's last game in 2026" rendered his
  whole 2026 radar, titled with the season, with nothing saying the question
  had been widened. The template already refused this correctly - the slot
  never reached it. `ROUTER_PROMPT` instructs `order` for `game_log` and
  `shot_chart` only, so a fingerprint question carries no instruction to fill
  it: measured at temperature 0, "last game" and "first game" phrasings came
  back with no `order` 3/3, while "most recent game" - the prompt's own wording
  - came back with it 3/3. The third slot to need the fix `_validate_season`
  and `_validate_side` already use: `_validate_order` reads it out of the
  question, for the intents whose templates honor it (`ORDER_INTENTS`, guarded
  against `HONORED_SCOPING`). `ROUTER_PROMPT` and `ROUTER_SCHEMA` hash
  identically before and after, so no other question's routing moves.

  The refusal now also names what IS answerable, and names the right cause. A
  single game's NetPoints total is on record and its play-type split is not -
  and that split is missing from the *warehouse*, not from the world: ESPN
  Analytics publishes a second per-date file,
  `NBA/netpts/<season>/<date>_player.json`, carrying every player's NetPoints
  across 31 action types per game, which the pull does not read. So the
  sentence says the data has not been pulled rather than that it does not
  exist.

- **The agent's SQL connection can no longer read the disk.** `read_only=True`
  protects the database and says nothing about the machine under it: confirmed
  live against the built warehouse, `SELECT * FROM read_csv('/etc/passwd')`
  returned rows and `glob('/home/<user>/*')` listed dotfiles, through the same
  connection `run_sql` uses. Since `run_sql` runs SQL a model wrote from a
  question a stranger may have phrased, "only SELECT is allowed" was never the
  boundary it reads as - a SELECT is enough to put a file in the answer. The
  new `query.toolbox.connect_read_only` adds `enable_external_access=false`,
  and it costs nothing: no template and no agent tool reads a Parquet file,
  attaches a database or copies anything, so the warehouse was the whole
  surface either way. The fetch path is the opposite case and keeps its own
  connection - building the warehouse IS reading 208,000 files off disk.

- **The web server no longer shares one conversation between every browser.**
  `AgentRunner` reuses a single Agent so the DuckDB connection and ollama's
  keep-alive survive between requests - and its conversation was surviving too,
  which nothing intended and the docs already contradicted ("each message is a
  new question"). The history was the smaller half; `Agent.last_question` goes
  to the router as `previous_question`, so one person's "what about jokic" was
  routed against whatever a stranger had asked before it. `ask` now calls the
  new `Agent.reset_conversation` under the lock it already holds. Real
  multi-turn memory for the web UI means per-client conversations, which needs
  a session the API does not have yet; until then this is stateless on purpose
  rather than by accident.

- **Trimming a long conversation drops whole turns, never half of one.** The
  fixed slice cut at an offset, which lands between an `assistant` message
  carrying `tool_calls` and the `tool` results answering them, leaving the
  history opening on a result that answers nothing visible. Ollama accepts that
  rather than rejecting it - measured, so nothing fails and the cost is paid
  quietly, as a JSON blob spending context with no question attached to say
  what it was for. Measuring also narrowed the shape, which is not what it
  looks like: a conversation of uniform turns never splits, because every turn
  is an even number of messages while the offset is odd, so the cut lands in
  the same safe place forever. What breaks that parity is a round asking for
  two tools at once, since the loop appends one `tool` message per call - and
  mixed that way, a quarter of trims orphan a result.

- **`--log-level` no longer reconfigures logging for the whole process.**
  `logging.basicConfig` inside `data pull` and `data load` configures the
  *root* logger, so calling either from anything that had its own logging set
  up - a library, a test, the web server - replaced it. `_configure_logging`
  attaches one handler to the `association` logger instead, which every logger
  in the package is a child of. Same output from the CLI, and nothing outside
  the package touched.

- **A bare surname asks again, even when the router completed it.** "Who is
  better, tatum or brown" routed to `['Jayson Tatum', 'Jaylen Brown']` and was
  answered without a question. Tatum is one player and that completion is free;
  "brown" is ten, and the router picking Jaylen is exactly the prominence
  tiebreak measured and rejected above `PLAYER_NICKNAMES` - arriving through
  the model's guess instead of through code, where nothing downstream could
  see it. `entities.undo_name_completion` cuts a name back to the part the
  question actually carries whenever that part is ambiguous, and lets normal
  resolution decide: `find_players` applies the nickname table first, so
  "luka" still answers Luka Doncic and "steph curry" still answers Stephen,
  while "brown" and "edwards" ask. Measured over the `check_routing.py`
  corpus, no slot moves.

- **A name the question does not support is refused, not passed to the agent.**
  Falling through was the first fix and it was the wrong one: given
  "compare fingerprints for embiid vs jokic in 2026", the agent spent 55
  seconds writing a confident fingerprint - play-type percentages and all - for
  "Ronaldo Lopes", who does not exist. Same reasoning `check_coverage` already
  records for a season below a floor: nothing downstream does better, and an
  agent with nothing to find fills the silence from its own weights. The
  refusal fires only for the intents whose template actually reads a player
  slot (`PLAYER_INTENTS`, checked against the templates' own source), since a
  stray name on a `head_to_head` question changes no answer.

- **A fingerprint that lost a player to a typo says so.** "generate
  fingerprints for embiid vs jolic in 2026" drew Joel Embiid alone: "jolic"
  matches nobody and is not close enough to exactly one player to guess at.
  Recovering it was measured and rejected - a near-spelling search over a
  question's leftover words finds a spurious player in 29 of 51 corpus
  questions ("season" is one edit from Tari Eason, "most" from Quinten Post),
  and it does not find Nikola Jokic either. So the name stays lost and the
  answer states it, because one polygon where two were asked for is only a
  failure while nothing mentions it.

- **A fingerprint keeps every player the question named.** The same question
  arrived as a single `player` slot, so a two-player comparison was answered
  with one polygon and nothing said so - the project's oldest failure shape.
  `entities.restore_dropped_players` puts back the players the router dropped,
  for the fingerprint intent only: two polygons on shared axes is what a
  comparison means there, while widening a `player_stat` question the same way
  would answer a different one. Gated on the question saying it compares
  something, because `players_named_in` is strict but not infallible - "best"
  is Travis Best and "boston" is Brandon Boston Jr., so "plot jokic's
  fingerprint from his best season" names two players by its rules.

- **A comparison that named no stat shows the whole line again.** `stat` is the
  one required slot in `ROUTER_SCHEMA`, so the model fills it on every question
  whether the question named a stat or not: "compare sga and embiid" came back
  with `stat='points'` 12 times out of 12, which narrowed the comparison to a
  single average and quietly undid the wider default. `route()` now drops an
  unasked `stat` for `player_compare` alone - the same "read it from the
  question" fix `_validate_side` makes, and safe to keep loose because a word
  it misses only widens a comparison, while `leaderboard` would have nothing
  left to rank by.

- **A player the question never mentions is no longer answered about.** Asked
  to "compare sga and embiid", the 3B router returned
  `['Shai Gilgeous-Alexander', 'Jusuf Nurkic']` and the answer was a fluent,
  correct-looking table of two real players, one of whom the question never
  named. Nothing downstream could notice: "Jusuf Nurkic" resolves perfectly, so
  every check after the router passed.

  `entities.override_invented_players` now checks each router-supplied name
  against the question before a template reads it, the same way
  `override_nicknames` already checked nicknames. A name leaves a trace in four
  ways, all of them things the router legitimately does - the word itself, a
  near spelling of it, a nickname, or the initials ("KAT", "SGA") - and a name
  with none of them is not answered about. Where the question names somebody
  nothing else accounts for, that player takes its place; where it does not,
  the question is refused rather than answered about the wrong player (this
  first fell through to the agent instead - see the entry above for why that
  was worse). Measured
  over the whole `check_routing.py` corpus, no correctly-routed player slot
  moves, and the check costs 0.08ms a question.

- **A name nothing matches says what it might have meant.** The other half of
  the same failure: "compare sga and embid" routed to `'Jemel Embiid'` - the
  surname corrected, the given name invented - and since every token must
  match, one fabricated word buried a player the warehouse holds. That fell
  through to the agent, which resolves the same name against the same table.

  `entities.suggest_players` now backs a multi-word name off to its surname
  (exact matching on one fewer token, not fuzzy) and then, failing that, looks
  for near spellings of every token - which is what reaches the user's own
  typo, since "embid" is not a substring of "Embiid" and no amount of trusting
  the question finds it. The four places that reported "No player found
  matching X" share one sentence through `entities.no_match`, and
  `player_compare` answers with it rather than falling through. A suggestion
  naming more than five players is dropped: that is a directory, not a
  suggestion.

- **A player comparison shows the whole line, and the NetPoints summary with
  it.** "Compare Luka and SGA" answered with games, points, rebounds and
  assists - the same three-stat default `player_stat` uses. But the two
  questions are read differently: "how many points did Luka average" wants the
  number it asked for, while a comparison is asking which player is better,
  and three counting stats cannot answer that. They leave out both halves of
  the defensive line and everything a player gives back.

  `player_compare` now defaults to points, rebounds, assists, steals, blocks,
  turnovers, fouls and minutes, followed by NetPoints per 100 possessions -
  overall, offense and defense. Per 100 rather than season totals, because a
  comparison is exactly the question totals answer badly: they mostly rank by
  playing time. A table costs nothing per row, and prose is already refused
  here (the agent's prose version once said a player with 0.4 steals led one
  with 1.6). Naming a stat still narrows to it, so "who scores more" gets
  scoring rather than a wall. `player_stat` is unchanged.

  The NetPoints block is supplementary: a player with no row is left blank
  rather than drawn as +0.00, a season where nobody has one drops the block
  entirely, and a warehouse where the opt-in NetPoints fetch was never run
  still answers. `net_points_player` is deliberately absent from this
  template's `TEMPLATE_SOURCES`, since listing it would put a 2019 coverage
  floor on every comparison and refuse the 1994-2018 ones outright.

  The web page shows the same rows, reading `netpoints` without requiring it,
  so a pre-2019 comparison still renders as a table rather than falling back to
  text. `table()`'s alignment test now accepts a leading `+`; without that, one
  signed cell would have left-aligned an entire column of digits.
- **`fouls` is a stat the fast path can answer.** `ROUTER_PROMPT` has always
  listed it among the stat names the router may emit, but
  `PLAYER_STAT_COLUMNS` had no entry, so `_wanted_stats` raised and every
  question naming fouls fell through to the slow agent path.
- **A question about a season the warehouse cannot reach is refused, with the
  real reason.** Every table starts in a different year and the gaps are
  ESPN's, so an out-of-range question returned nothing - and nothing was then
  phrased as though the filters were wrong, or as a real answer. Both were
  measured on the current snapshot. A 1996 shot chart said `No shots found for
  Michael Jordan with the given filters`, blaming the filters for play-by-play
  that starts in 2002. Worse, a 1980 scoring leaderboard answered `Moses Malone
  led the league in points per game, at 25.8. Next: Bill Cartwright (21.7),
  John Long (19.4)` - drawn from a league of **seven players**, with Kareem
  Abdul-Jabbar, Larry Bird and Julius Erving absent from `players` entirely.

  `association/coverage.py` declares each table's earliest usable season and
  why it starts there; `templates.check_coverage` refuses below it. The
  refusal is returned as the answer rather than raised, unlike `check_scope`:
  falling through would put the same empty tables in front of an agent that is
  then free to fill the silence.

  A lookup and a ranking get different floors, because they fail differently.
  `player_season_stats` holds Jordan's real 1990 line, so his own average is
  still answered; ranking that season is refused, since the pool is 217 players
  against a ~350-player league. The two refusals are worded differently on
  purpose - telling somebody there is "no data for 1980" about a warehouse
  holding Moses Malone's real 1980 line would be the same false-cause answer
  facing the other way.

  Seasons that exist but only partly - 2002 play-by-play is about half a year -
  are answered with a caveat rather than refused. Playoffs reach back to 1989
  where regular seasons only reach 1994, so `games` carries both floors. Season
  1993, whose rows duplicate 1994, is declared a phantom rather than merely
  excluded, so it can be verified rather than assumed.

  `scripts/check_coverage.py` checks every floor against a built warehouse -
  28/28 on this snapshot - the same way `check_nicknames.py` checks the
  nickname table. Each floor was confirmed by moving it and watching the check
  fail.
- **"Wembanyama's defensive fingerprint" drew the whole fingerprint.** The
  router filled `stat` with "defensive" and left `side` unset, so the template
  fell back to its default and plotted all 20 spokes where the 5 defensive ones
  were asked for - a broader answer than the question, with nothing saying so.
  Deterministic, measured 6/6 at temperature 0, and failing for that exact
  wording even though it appears verbatim as a worked example in
  `ROUTER_PROMPT` with the right answer beside it.

  No prompt wording fixes it, because it is not a wording problem. `stat` is
  the one *required* slot, and a constrained decoder fills what it must before
  what it may: the adjective is spent on `stat` and the optional `side` is
  never considered. So `side` is now read from the question in `route()`, next
  to `_validate_season`, which reads the season from the question for exactly
  the same reason.

  `ROUTER_PROMPT` and `ROUTER_SCHEMA` are byte-identical - verified by hashing
  both before and after - so the model sees the same input and no other
  question's slots can move. `scripts/check_routing.py` goes 47/48 to 48/48,
  with the run otherwise line-for-line identical to the previous one.

  Deliberately conservative, like `override_nicknames`: a question naming both
  halves leaves the slot unset, since that already means the whole radar, and
  guessing between them would be the same bug facing the other way.
- **A chart no longer guesses which player a surname meant.** "Show me a
  fingerprint for Maxey" drew nothing and blamed the warehouse, because
  "Maxey" resolved best-match to Marlon Maxey, who last played in 1994, rather
  than Tyrese. Charts resolved names best-match on the reasoning that the plot
  is titled with the name that won, so a wrong match is obvious on sight - true
  right up until the wrong match is the reason no plot exists.

  `resolve_chart_player` now narrows the candidates to those with a row in the
  table the chart is drawn from, for the season being drawn, before taking a
  best match. This eliminates; it does not prefer. It never chooses between two
  players who both have the data - that is the prominence tiebreak recorded as
  measured and rejected above `PLAYER_NICKNAMES` - it only drops the ones who
  cannot be the answer. One survivor is the answer; two or more get the same
  clarifying question a numeric template asks.

  Measured against the warehouse: of the 566 players with a 2026 fingerprint,
  319 have a surname somebody else also matches. Narrowing resolves 100 of
  those outright and sends the remaining 219 - Wiggins, Bridges, Allen, the
  Antetokounmpos - to a question that deserves asking. League-wide, 221
  surnames put a player with no fingerprint ahead of one who has it, and 97 do
  the same for shot charts.

  When narrowing eliminates *everybody*, the best match stands: no answer to
  "which one did you mean" draws a plot either, so the renderer's own message
  says more than the question would.

  The extra query runs only when a name matched more than one player, and costs
  1.0ms (two candidates) to 3.1ms (ten) warm, 5.5-10.2ms on a cold process -
  against a question that spends about 3.15s in the router. Measured on the
  full warehouse, including `shot_chart` at 6.7M rows.
- **`find_players` ranks a name that starts a word above one it lands inside.**
  "Ball" offered Cedric Ceballos ahead of LaMelo, "Bey" offered Mike Tobey
  ahead of Saddiq, and "Ford" offered Al Horford ahead of Aleem Ford - each of
  them the candidate a chart then drew. Word-boundary matches now sort first
  and, when there are any, are the whole candidate list, which is the two-step
  `find_teams` has always used.

  The boundary is any non-letter rather than a space, so both halves of a
  hyphenated or apostrophed name start a word: "Alexander" still reaches Shai
  Gilgeous-Alexander and Nickeil Alexander-Walker, "Neal" still reaches
  Shaquille O'Neal. Substring matching is kept, so a fragment typed mid-word
  still finds the people it matches. Across the warehouse's 1,899 surnames this
  changes 46 best matches, resolves 36 names that used to ask, and every
  changed pick is the more plausible player.
- **A fingerprint nobody has is no longer reported as a season nobody has.**
  "Show me a fingerprint for Maxey" answered `No NetPoints fingerprint on
  record for season 2026` - a claim about league-wide coverage, and a false
  one: that season holds 566 players and Tyrese Maxey is among them. "Maxey"
  matches two players and `find_players` orders by name, so best-match
  resolution took Marlon Maxey, who retired in 1994, and nothing was drawn.

  `load_fingerprints` now names the players when the season has rows and they
  do not, and says how many players the season does hold. A name that resolved
  to the wrong person is visible in the answer instead of looking like a gap in
  the warehouse, and the empty-season message now means only what it says.

  `render_for_players` also carries the other matches onto the failure path.
  Names here resolve best-match, which is safe because the plot is titled with
  the name that won - and that is exactly what does not happen when nothing is
  drawn, so the one case that needed the runners-up was the one discarding
  them. The answer now ends `Note: other players also matched: Tyrese Maxey.`
- **Nicknames are read from the question, not from the router's guess.** Asking
  for "The Answer" returned Allen Iverson's numbers only by luck: the 3B router
  rewrites a nickname it recognizes and *invents* a player for one it does not,
  and the invented name is a real player who resolves cleanly. Measured, "The
  Answer" became `player='Klay Thompson'`, "The Glove" became `'Jayson Tatum'`,
  and "VC" became `'Victor Claver'` - each answered confidently, about the wrong
  person, in under two seconds. Nothing downstream could catch it, because by
  then the nickname was gone.

  `entities.override_nicknames` now matches the table against the user's own
  words and overrides the router's player slot before any template runs. It is
  deliberately narrow: a single `player` slot only when the question names
  exactly one nickname, a `players` list only when the counts match, since a
  wrong override is the same bug in the other direction.

  `PLAYER_NICKNAMES` grows from 21 to 78, sourced from Wikipedia's list of
  basketball nicknames and filtered to players the warehouse actually holds -
  which starts at 1993-94, so Bird and Kareem are not there to be named. The
  new entries reach the players the router got wrong: Iverson, Carter, Payton,
  Olajuwon, Malone, Robinson, Rodman, Pierce, Garnett, Webber, Hardaway.
  `scripts/check_nicknames.py` verifies against a built warehouse that every
  value names exactly one player and no key is a name belonging to somebody
  else.

  First names now resolve where they used to ask: "luka" is Doncic, "kobe" is
  Bryant. A question carrying only a shorthand cannot reliably have meant Luka
  Garza or Kobe Bufkin, so the clarifying question bought nothing. A shared
  *surname* still asks - "brown" offers ten candidates, and should - and so
  does "curry", where Seth and Stephen are both real answers.
- **`association data check` takes 17 seconds instead of 7.5 minutes.** It
  gathered every count with its own filtered query - one per cell of the
  report - and none of these Parquet trees is hive partitioned, so `WHERE
  season = ...` pruned no files and each query re-read the table in full. With
  37 seasons and 2 season types that was 74 scans of all 40,558 `games` files
  (238s) and 74 more of `shot_chart` (160s), 87% of the runtime between them.
  Each table is now counted once, grouped by season and season type, and the
  report indexes into that. The printed table is unchanged, byte for byte.
- **`data load` no longer runs out of memory building the warehouse.** A full
  build was killed by the OOM killer partway through, leaving the tables it had
  already replaced and the rest at their old contents. The cause was DuckDB's
  external file cache, which keeps the Parquet a statement read resident after
  that statement ends and accumulates across the 18 loads a build runs on one
  connection. It is sized for re-reading a few large files; this tree is
  208,000 small ones, and it charged far more per file than a file holds -
  `games` alone (40,558 files, 320 MiB on disk) parked 5.6 GiB in it. The
  warehouse connection now turns it off: a full build peaks at 3.0 GiB instead
  of being killed, and is no slower for it.

  Nothing DuckDB would have raised - it was accounting for 6.5 GiB of a 12.4
  GiB budget when the kernel killed the process, which is why this looked
  nothing like the earlier `plays` failure that `preserve_insertion_order`
  fixed. Both settings now sit in `_tune` with the symptom that tells them
  apart written down.
- **A chart taller than its frame now says so.** An inline chart is capped, and
  past the cap the frame scrolls - but it clipped its content dead flat, which
  reads as a rendering fault rather than as somewhere to scroll. The lower edge
  now fades out, and only while there is more below: the fade is dropped at the
  end of the scroll, where it would otherwise sit over the last row of a
  fingerprint's table. It is painted in the chart's own background color, read
  off the document inside the frame, because a chart page sets its own rather
  than inheriting the app's.

## 2.0.0 - 2026-09-09
- **Charts render inline in the web interface.** A question that draws a shot
  chart or a fingerprint now shows it in the conversation instead of naming a
  file path. `GET /api/artifacts/{name}` serves them out of the same directory
  the CLI writes to, so a chart made at the terminal is viewable in the browser
  and vice versa - and the CLI still writes the identical standalone file,
  verified byte-for-byte. Phase 3 of `docs/roadmap-2.0.md`.

  That directory belongs to whoever started the server, and a name arriving
  over HTTP decides which file comes back, so the name is checked twice: it has
  to match an allowlist that admits no separator and no `%` (so no encoded one
  either), and the *resolved* file has to sit directly in the resolved output
  directory, which is what catches a symlink with an innocent name. Neither
  check subsumes the other, and both are tested - including at the guard level
  rather than only through the route, because the router already rejects most
  traversal names before the guard sees them.

  Charts are drawn in an `<iframe>` with scripts disabled. `court.py` and
  `radar.py` emit pure HTML, SVG and CSS and never have emitted a script, so
  nothing is lost; a test asserts that stays true. Same-origin so the page can
  size the frame from its content, capped, with a link to the full file.
- **The web interface renders answers per question shape.** `Answer.data` was
  already structured - resolved names and numbers - so seven intents now come
  back as something better than fixed-width text: `leaderboard`,
  `threshold_count`, `single_game_high`, `game_log` and `player_compare` as
  tables, `player_history` as a sparkline over its numbers, `team_record` as a
  record card. Phase 2 of `docs/roadmap-2.0.md`.

  Nothing re-derives a sentence. Phrasing stays in the templates, in Python,
  once; a renderer picks a *caption*, from either a scope string the template
  computed or the answer's own first line. `single_game_high` gained a
  `question_shape` to that end, matching `leaderboard` and `threshold_count`.

  An intent with no renderer answers exactly as before, in the text the CLI
  prints - the normal case, not a failure - and so does a shape not worth a
  table: "who leads the league in assists?" returns one row, and one row is not
  a ranking. Every rendered answer keeps the full text one click away.

  The renderers are JavaScript and the templates are Python, so the contract is
  guarded from the Python side: each renderer declares the `data` keys it
  reads, and a test parses those out of the page and checks them against what
  each template actually produces. A renamed key fails that test instead of
  silently dropping a table and falling back to text, which looks like nothing
  happened.
- **Fixed: `ESPNClient.session`'s return type was unknown on Python 3.12 and
  below.** curl_cffi's `Session` is generic over its response type and only
  carries a default on 3.13+ (`TypeVar(default=...)` did not exist before it),
  so bare it read as `Session[Response]` or `Session[Unknown]` depending on the
  interpreter. Spelled out now. No runtime change.
- **Fixed: the type-completeness gate passed locally and failed in CI.**
  `pre-commit` runs hooks under `uv run`, which exports `VIRTUAL_ENV`; CI
  invokes the same script bare, and pyright resolved the package's imports out
  of the active venv only in the first case. The script now puts the venv's
  site-packages on `PYTHONPATH` itself, so it gives the same answer either way.
  Surfaced by the web layer: `--ignoreexternal` does not cover a class whose
  *base* cannot be resolved, which is what a pydantic model looks like when the
  optional `web` extra's packages are not on the path.
- **`association web`: a local web interface.** A chat-shaped page over the
  same router → template → answer pipeline the CLI uses, served until you quit
  it. Phase 1 of `docs/roadmap-2.0.md`.

  It needs the new `web` extra (`pip install 'association[web]'`), which adds
  `fastapi` and `uvicorn`; the core install stays at seven dependencies, and
  `association web` without them prints the install command rather than a
  traceback.

  Four things are deliberate rather than incidental:

  - **No default port.** It binds a free one and prints the full URL for the
    terminal to linkify. A fixed default collides with whatever else is
    running and has to be explained; `--port` is still there.
  - **One question at a time.** ollama keeps a single KV cache slot per model,
    so two questions in flight evict each other's prefix and both come back
    slow (measured: 1.3s becomes 11.2s). A question that arrives while another
    is running is told it is waiting.
  - **Progress streams.** `GET /api/ask/stream` sends the same trace
    `--verbose` prints as server-sent events, because a fall-through to the
    agent takes minutes and a spinner for that long is indistinguishable from
    a hang.
  - **Every answer says which path produced it.** Whether a template built the
    sentence from code or a 7B model wrote the SQL is the most useful single
    thing a reader can know about an answer, so the UI never hides it.

  The API is usable on its own: `POST /api/ask` returns the whole `Answer`,
  and `GET /api/health` reports the warehouse, the seasons in it, the models,
  and whether ollama is reachable. There is no conversation memory - each
  message is an independent question - and no way to turn the fast path off,
  which is not an oversight: the fast path is the product.
- **BREAKING: an answer is a value, not a printed string.**
  `Agent.ask` now returns an `Answer` (`association.query.answer`) instead of
  the answer text: the text, which path answered (`"fast"` or `"agent"`), the
  intent, the template's structured `data`, the files written, and the timing.
  Callers print `answer.text`.

  The rest of the reshaped public surface, for anyone using this as a library:
  `Agent(..., trace=...)` takes the callback its live output goes to;
  `Toolbox.take_artifacts()` drains the files its render tools wrote, since a
  tool's own return value is prose the model reads; and `Artifact`,
  `RenderResult`, `Timing` and `Answer` all live in `association.query.answer`.

  The fast path already computed all of it and threw most of it away.
  `TemplateResult.data` exists so a caller can render an answer itself, and
  `_try_fast_path` returned only `result.answer`, so nothing ever could - which
  is the first thing the 2.0 web UI needs. Phase 0 of `docs/roadmap-2.0.md`.

  Three smaller reshapings come with it, all on the same theme of the engine
  assuming it was talking to a terminal:

  - **Both chart renderers return the same shape.**
    `shotchart.render_for_player` and `render_shot_chart` returned a message
    with the file path formatted into the middle of a sentence;
    `fingerprint.render_for_players` returned `(message, path)` and
    `render_fingerprint` a message. All four now return a `RenderResult`
    (message, and the `Artifact` written - `None` when nothing was drawn), so
    showing a chart does not mean parsing a path back out of prose.
  - **`RunHistory` takes a `sink`** for its live trace, defaulting to stderr.
    The file still records every line regardless of `--verbose`, as before.
  - **`Agent.ask` takes a `label`** for the history file's `command:` line
    instead of reading `shlex.join(sys.argv)`, which is only ever true of a
    CLI. `Agent` also takes a `trace` callback, so nothing in the engine writes
    to a terminal on its own.

  The CLI is unchanged: verified byte-identical to 1.6.0 across ten questions
  covering ten intents, with `Answer.data` populated for every one.
- **The `ai` REPL is gone.** The web UI planned for 2.0 (see
  `docs/roadmap-2.0.md`) replaces it, and keeping both would mean two
  interactive front-ends with different capabilities over one engine. Removed
  with it: `query/repl.py`, `Agent.reset` (nothing else called it), and the
  REPL's mentions in the CLI epilog, README and usage docs. `association query`
  is unchanged. Until the web UI lands there is no interactive mode.
- **A plan for 2.0.** `docs/roadmap-2.0.md` writes down the web interface
  before any of it exists: what is in scope, the three API reshapings that make
  it a major version, the constraints the existing system imposes on it (one
  ollama KV cache slot, two paths that differ by two orders of magnitude), the
  framework comparison, and four phases with acceptance criteria.

## 1.6.0 - 2026-09-08
- **Pulls fetch several games at once.** Fetching is latency-bound: profiling a
  live pull put 96% of the main thread inside one curl call, at 5% CPU, zero
  bytes read from disk, and a 12ms round trip to a CDN whose cold responses take
  250-400ms. Requests were issued strictly one at a time, so a pull ran at 2.4
  requests/second against a `--rate-limit` of 10 that never once had to sleep -
  raising the limit did nothing at all.

  `--workers` (default 4) sets how many requests are in flight. It does not
  raise what ESPN sees: the rate limiter is shared across threads, so
  `--rate-limit` still bounds the request rate and this only stops a pull
  falling short of it. Measured over 12 cold game summaries: 2.32/s serial,
  5.95/s with four workers. `--workers 1` restores the old behavior exactly,
  down to running without a thread pool.

  Two things had to become thread-safe. `ESPNClient` now keeps one session per
  thread - a curl_cffi session wraps a single libcurl handle - while sharing the
  throttle, because a per-thread allowance would multiply the rate limit by the
  worker count. And `storage.write_rows` now writes through a temp name unique
  per process and thread: two games sharing a player both cache that player's
  bio, so two threads really do write one path at once, and a shared `.tmp` let
  them interleave into a single file that was then renamed into place looking
  perfectly normal.

## 1.5.0 - 2026-09-08
- **Releases carry their own wheel and sdist.** The publish workflow now
  attaches the built distributions to the GitHub release, not just to the
  workflow run: workflow artifacts are deleted after 90 days, and with PyPI
  publishing blocked on an account-access issue, a release could otherwise end
  up with nothing downloadable at all. `README.md` and `docs/installation.rst`
  say to install from a release tag until PyPI is reachable again; both notes
  are written to be deleted in one commit when it is.
- **A pull over completed seasons is instant again.** `association data pull
  --seasons 2024` on a season already on disk took over a minute and ended in
  an out-of-memory crash; it now takes 0.5s and makes no network request.

  Three separate causes, each fixed:

  - **NetPoints was fetched on every run, for every season.** The source is one
    league-wide flat file, so there was no per-season request to skip - but
    there was the whole download to skip, and there was no reason to rewrite
    2026's file during a pull of 2024. `fetch_net_points` now takes the seasons
    being pulled, writes only those, and skips the download entirely when each
    of them is finished and already on disk. Seasons before 2019 are skipped
    outright: NetPoints does not go back further, and the bucket answers 403.
  - **The warehouse was rebuilt in full after every pull**, including one that
    fetched nothing. `Pipeline` now records which tables it wrote and the CLI
    reloads only those; a run that wrote nothing rebuilds nothing. A warehouse
    that does not exist yet is still built in full.
  - **A full rebuild ran out of memory.** Loading `plays` from 17,500 Parquet
    files died with "could not allocate block of size 32.0 KiB (12.4 GiB/12.4
    GiB used)", and since each table is its own statement, the build aborted
    with the earlier tables already replaced and the rest left at their old
    contents. The build connection now sets `preserve_insertion_order=false`,
    which lets DuckDB stream the scan; row order carries no meaning in any of
    these tables. A full `data load` of 8.0M plays, 4.2M win-probability rows
    and 3.7M shots now completes in about 85 seconds.

  The behavior change to know about: a pull no longer backfills NetPoints for
  seasons it was not asked for. Pull the range you want, or run `data load`.
- **The stat glossary no longer shrinks with every partial pull.** It was
  written from only the endpoints a given run fetched, replacing whatever was
  on disk - so a current-season pull cut it from 140 keys to 94, dropping the
  box-score entries nothing was going to re-derive. It now merges with the file
  on disk, prefers freshly fetched descriptions, and does not rewrite an
  unchanged file (which would otherwise rebuild a warehouse table on every
  pull). Pre-existing, and invisible until the rebuild became conditional on
  what a run actually wrote.

## 1.4.0 - 2026-09-08
- **The agent can render fingerprints too.** `render_fingerprint` is now a tool
  the fall-through agent can call, not only a fast-path template, so a question
  the router does not classify as `fingerprint` can still produce the plot
  rather than a table of numbers. `PREAMBLE_TOKEN_BUDGET` goes from 6000 to
  6400 to fit it: the worst assembled question now measures 6,179, leaving 221
  tokens of headroom, and the budget stays well under the `AGENT_NUM_CTX // 2`
  bound that the existing test asserts.

  Two tests now guard the tool list itself, which is two lists of the same
  names kept in step by hand: every schema in `TOOLS` has a handler in the
  agent's dispatch table and vice versa, and every parameter a schema
  advertises is one its handler actually accepts. A name in one list and not
  the other is either a `KeyError` the moment the model calls it or a
  capability the model can never reach.

  `docs/architecture.rst` gains a section on the tool budget, because raising
  it is nearly out of road: each tool costs ~190 tokens of schema charged on
  every question, and two more would not fit. It lays out the levers in order -
  fold the renderers into one `render(kind, ...)` tool, select tool schemas per
  question the way knowledge-base entries already are, and keep porting shapes
  to templates so the agent sees fewer questions at all.

## 1.3.0 - 2026-09-08
- **NetPoints fingerprint plots.** `association query "plot SGA's fingerprint"`
  now renders a static HTML radar of a player's play-type NetPoints, the way
  `shot_chart` renders their shots - a new `fingerprint` router intent and
  template, with `query.fingerprint` doing the querying and `query.radar` the
  drawing (the same split `shotchart`/`court` already had).

  It follows espnanalytics.com's own Skill Fingerprint, which is where these
  numbers come from: the same 20 skills in the same five groups (scoring, shot
  types, creation, rebounding, defense), net points per 100 possessions, either
  as a percentile of the league or on one shared value scale, with the
  overall/offense/defense headline above the plot and every number repeated in a
  grouped table underneath - a radar is a shape, and the table is what makes it
  checkable. Naming two players draws both on shared axes and shades each
  category to whoever leads it, in their color, at an intensity carrying how
  far ahead they are.

  Three things are deliberate. A skill is a *(category, side)* pair, not a
  category: rim finishing and rim protection are different skills sharing a
  column prefix, and drawing one side per plot hid defense entirely. The
  aggregate categories (`two_pt`, `three_pt`, `total`) are off the radar because
  they double-count the slices beneath them. And a fingerprint scoped to one
  game is refused rather than answered with the season's shape - there is no
  per-game play-type breakdown anywhere in the warehouse, and
  `HONORED_SCOPING` therefore lists `fingerprint` as honoring `order`/`date`
  by saying so.

  Not wired into the fall-through agent as a fifth tool: its schema and prose
  cost ~205 tokens against 95 of headroom under `PREAMBLE_TOKEN_BUDGET`
  (the worst assembled question measures 5,905 of 6,000), and buying the room by
  trimming existing tool descriptions is a change to load-bearing prompt text
  that no offline gate can check. The fast path covers it; the budget is the
  thing to raise first if the agent should have it too.

## 1.2.0 - 2026-09-08
- **Version markers on the public API.** The docs already generate a page for
  every module (`docs/api/index.rst` runs `autosummary` recursively), but nothing
  said when anything appeared, so the reference read as though the package had
  always looked this way. `.. versionadded::` / `.. versionchanged::` now mark
  what moved: `query.models`, `shotchart.resolve_chart_player` and
  `render_for_player` as added in 1.2.0, `team_quarter_points` in 1.1.0, and the
  reshaping of `TemplateResult`, `render_shot_chart`, `AGENT_NUM_CTX` and
  `ROUTER_NUM_CTX`. Verified by grepping the rendered HTML, not by the build
  exiting 0 - four "Added in version" and four "Changed in version".

  `AGENTS.md` gains the convention, including the two things that bite: docstrings
  are reStructuredText and the docs build runs under `-W`, so a malformed one
  fails a gate; and a module-level constant needs an attribute docstring for a
  directive to attach to it.
- **`bump_version.py` now refreshes `uv.lock`.** The lock records the project's
  own version, and rewriting `pyproject.toml` alone left it a release behind -
  both v1.0.0 and v1.1.0 were tagged with a stale lock, and 1.0.0 needed a
  follow-up "sync uv.lock" commit to correct it. CI and Read the Docs install
  with `--frozen`, so the lock is what they actually build from. The bump now
  runs `uv lock`, asserts the new version landed in it, and commits it alongside.

- **Organisation pass on `query/`.** The last needless function-local import is
  gone; `cli.py`'s remaining ones now carry a comment saying they are deliberate,
  since they keep duckdb, pyarrow and ollama out of `--help` and a future tidy-up
  would otherwise "fix" them. `current_season` had two import paths - four
  modules took it from `association.season` and `leaderboard.py` from a
  re-export in `metrics.py`; the re-export is gone. And the two different
  `NUM_CTX` constants, 4,096 for the router and 16,384 for the agent, are now
  `ROUTER_NUM_CTX` and `AGENT_NUM_CTX`, so a reader cannot mistake one window
  for the other.

  `templates.py` had definitions sitting well below their first use -
  `_table_cell` was ~1,100 lines under it, `_resolved_player` ~370, `STAT_LINE`
  was defined *after* the function reading it, and `MAX_COMPARED_PLAYERS` sat
  above `head_to_head` while belonging to `player_compare`. Nothing is now
  defined more than 200 lines after first use.

  **Not split into a package, deliberately.** With the duplication gone the
  seams turned out poor: six helpers are shared by 3 to 11 templates each
  (`_period` by 11, `_resolved_player` by 7), so a split by subject would move
  most of the file into a shared module and buy indirection rather than
  decoupling. The reading-order problem was the real pain, and that is fixed.

- **A scoped shot chart now resolves the player once.** `shot_chart` resolved
  the name twice for a question like "chart Curry's last game": once to find the
  game to scope to, and again inside `render_shot_chart`. Both took the best
  match, and they agreed only because both spelled the tie-break the same way -
  a convention, not a guarantee. Had they ever diverged the result would have
  been a chart titled for one Curry showing a game the other one played: wrong,
  and invisible, because the plot looks entirely normal.

  Resolution is now a named step (`shotchart.resolve_chart_player`), and
  `render_for_player` takes the already-resolved player, so a second resolution
  is not something a caller can accidentally do. This was a latent hazard rather
  than a live bug - the two paths were identical code and did agree - which is
  why it needed a structural fix rather than a patch.

  The regression test asserts the name is resolved exactly once, and was
  confirmed to fail (`player resolved 2 times`) against the old shape before
  being kept. It also closes a smaller inconsistency: an unknown player used to
  raise `TemplateUnsupported` when `order` was set and return a message
  otherwise; now both return the message.

- **Deleted two pieces of dead code.** `TemplateResult.summary` was constructed
  24 times and read exactly nowhere - not by `src/`, not by the tests, not by the
  scripts - so every template was building an f-string nobody would ever see. The
  field is gone along with all 24 arguments.

  `leaderboard.py`'s `MAX_ROWS = 200` could never bind: the SQL `LIMIT` is
  clamped to `MAX_LIMIT = 100` before the query runs, so `fetchmany(200)` always
  got everything. It also shadowed `toolbox.MAX_ROWS`, which *is* load-bearing,
  making the two look like one rule applied twice. The fetch is now a plain
  `fetchall()` and the comment says the clamp is on the LIMIT itself.

- **Removed the repeated patterns in `query/`.** None of these were copy-pasted
  functions - they were the same shape written out again at each call site,
  which is how they escaped notice:

  - Player resolution existed seven times as an inline `match` block, while
    teams had had a `_resolved_team` helper all along. `_resolved_player` closes
    the asymmetry; `templates.py` loses ~50 lines.
  - The hoop coordinate `(25, 5.25)` was stated in both `court.py`, which draws
    the rim, and `templates.py`, which measures distance from it. Those two had
    to agree and nothing made them: a rim drawn somewhere other than where
    distance is measured is a disagreement no test would catch. It now lives in
    `court.py`, which owns the coordinate system.
  - The "first/most recent game" lookup that an `order` slot resolves to was
    written twice (`_scoping_game`), and the deduped season-line read twice
    (`_season_row`).
  - `estimate_tokens` had identical copies in `prompt.py` and `toolbox.py` - the
    only exact duplicate function in the package. `toolbox.py` imports it now.
  - Reading `shot_value` off the slots was spelled two different ways in
    `shot_chart` and `shot_distance` (`_shot_value`).

  The `ollama pull` block appeared in four places; `usage.rst` now points at
  `installation.rst` rather than restating it, and the CLI epilog interpolates
  the model defaults instead of naming them, so `--help` cannot disagree with
  what the flags actually default to. Output is byte-identical. The remaining
  two copies - `README.md` and `installation.rst` - are both standalone entry
  points, and the two `(25, 5.25)` literals left in `prompt.py` are worked SQL
  examples for the model, where a placeholder would be worse than a repeat.

- **One definition of the ollama model defaults**, in `query/models.py`, instead
  of a copy each in `cli.py` and `agent.py`. The copies could drift apart
  silently and the consequence was not cosmetic: `scripts/check_routing.py` reads
  the router default to decide what to validate, so a divergence would have meant
  the routing check passing against a model the CLI does not ship - while the
  project's own rule is that the prompt and the router model are one unit, and
  swapping either invalidates the check.

  The new module deliberately imports nothing heavy, so `cli.py` reads it at
  module level without pulling ollama or duckdb into startup; the `Agent` import
  stays lazy. Verified: importing it loads no ollama/duckdb/pyarrow.
- **Fixed the last stale routing-benchmark count.** `docs/architecture.rst` said
  models scored "28-30 out of 30" while naming `scripts/check_routing.py`, which
  has 39 cases. Same claim as the one corrected in `agent.py`, missed on that
  pass; both now describe the spread without quoting a total that goes stale.

## 1.1.0 - 2026-09-08
- **Prose pass over `fetch/`.** Same rule as the query side: keep the fact that
  changes what you write, drop the retelling. What stayed is the material a
  reader cannot reconstruct - NetPoints labelling a season by the year it
  STARTS while every other table uses the year it ENDS; the UTC-vs-local date
  offset and, crucially, *why the +1 day case must be checked first* (on
  back-to-back nights against the same opponent, the team's own unrelated game
  sits at the exact NetPoints-label date and would steal the match); why a
  marker rather than file existence is what stops a date being re-fetched
  forever; and why PER/WS/BPM/VORP are a deliberate gap rather than an oversight.
- **Same prose pass across the rest of `query/`, and four stale comments fixed.**
  The wrong ones mattered more than the long ones:

  - `entities.py` said an ambiguous name "falls through to the agent". It has not
    for some time - templates ask a clarifying question via `_clarify`, which is
    a handled outcome, and the docstring was describing the behavior the code
    was written to replace.
  - `prompt.py` described the tool as `get_leaderboard(metric, season,
    season_type, min_sample, limit)`, omitting `team` and `fields`. Both are real
    and both are in the JSON schema directly below it, so the model was reading a
    signature narrower than the tool it was calling.
  - `metrics.py` credited `toolbox.py` with building SQL from it. `toolbox.py`
    does not import it at all; `leaderboard.py`, `prompt.py` and `templates.py` do.
  - `agent.py` cited "the 30 real cases in scripts/check_routing.py", which has 39.
    The count is gone rather than corrected, since any count goes stale.

  The prompt edit was checked the way prompt edits should be: the other four
  prompt constants hash identically before and after, `git diff --stat` shows one
  line changed, and the assembled preamble is ~5,803 tokens against the 6,000 budget.
- **Trimmed the prose in `templates.py`** from 316 lines of comment/docstring to
  278, and fixed two stale ones. Each war story is cut to the fact that changes
  what you write next - the failing query shape, the schema trap, the measured
  number - and the retelling around it is gone. `team_quarter_points`'s
  21-line docstring is the clearest case: it now says a player's quarter score
  needs the plays-table derivation and a team's does not, instead of narrating
  three model calls.

  The two stale ones: a pointer to `toolbox.EXTRA_FIELD_COLUMNS` (it lives in
  `metrics.py`), and a module docstring still describing the template migration
  as in progress. Also removed a duplicate comment above `PLAYER_STAT_COLUMNS`
  that said the same thing twice, a leftover from a merge.
- **Removed the narrator model call from the fast path.** `Agent._narrate`,
  `NARRATOR_PROMPT` and `NARRATE_NUM_CTX` were unreachable: the call site fired
  only when a template returned `answer=None`, and all 24 `TemplateResult`
  constructions set it. The only thing exercising the path was a test that
  monkeypatched a fake template returning no answer.

  `TemplateResult.answer` is now a required `str` rather than `str | None`, so
  "the fast path makes no model call after the router" is enforced by the type
  checker instead of holding by coincidence across every template. That property
  is load-bearing - a second call with a different system prompt evicts the
  router's KV prefix, and a narrator is the last place on this path a number
  could be invented - so it should not have depended on nobody ever omitting a
  keyword argument.
- **Removed `FAST-PATH-MIGRATION.md`**: the migration it planned is finished.
  Every shape it listed is implemented - all thirteen templates are registered
  in `TEMPLATES` and present in `ROUTER_SCHEMA`'s intent enum, per-question
  preamble assembly and the `PreambleTooLarge` budget guard are in place, and
  each of its three open "what is left" items (`player_compare`, leaderboard
  `fields`, token-bounded results) landed. What the document taught that the
  code does not say for itself already lives in `AGENTS.md` and the comments at
  the sites concerned; keeping a finished plan around only invites reading it
  as a description of the present. The dangling `See FAST-PATH-MIGRATION.md`
  pointer in `prompt.py` is gone with it - the paragraph above it already
  carries the whole measurement.
- **`AGENTS.md`, with `CLAUDE.md` symlinked to it**: orientation for agents
  working on this codebase - the gates and why mypy runs twice, the enforced
  conventions, and the failure shapes this project actually produces. It points
  at `docs/architecture.rst` for design rather than restating it, so the two
  cannot drift.

  The section worth having is the recurring bug shape: every query failure
  found during development was a *missing or too-narrow* shape, never a broken
  one, which surfaces as a fast fluent answer to a different question than the
  one asked. Alongside it are the data traps that cost real time - the season
  named for the year it ends, the three NetPoints tables that disagree about
  `season_type`, and the six fingerprint categories that partition the total
  while the other fifteen overlap.

  `CLAUDE.md` is a symlink rather than a copy: one file, no drift, and both
  naming conventions resolve to it.

- **`head_to_head` no longer refuses a matchup just because the router split
  the two teams across slots**: "how many times did the 76ers play Boston?"
  (a city name, not a nickname) reliably routed the second team into `teams`
  as a one-element list and the first into the singular `team` slot instead
  of both into `teams`, so the template's two-name check failed and the
  question fell through to the agent - which then answered 45 games for a
  pair of teams that met 4 times, from unparenthesized SQL that let its
  season filter apply to only one team (`home_team_id = A OR home_team_id = B
  AND season = ?`). `head_to_head` now treats a `team` slot as a third
  candidate rather than rejecting the question, and the agent's knowledge
  base gained a worked example of the correct parenthesization for anyone who
  still reaches it. `scripts/check_routing.py` and
  `tests/query/test_templates.py` both gained coverage for the split-slot
  shape.
- **A new `team_quarter_points` template answers a named team's quarter/period
  score, including against a named opponent**: router.py's `_AGENT_ONLY`
  regex deliberately forces every "Nth quarter" question to the agent, since
  a PLAYER's quarter score has no stored column and needs a fragile plays-table
  `LAG()` derivation. A TEAM's quarter score got caught by the same regex even
  though it needs no derivation at all - `games.home_linescores`/
  `away_linescores` already store it exactly, per side, as a comma-separated
  list. Confirmed live: "how many points did the 76ers score in the 4th
  quarter against Boston this season?" spent 3 model calls (~150s) on SQL
  that filtered a nonexistent `games.period` column, then a broken `LAG()`
  over `play_id`, then abandoned the opponent JOIN entirely and compared
  `home_team_id` directly to `'PHI'`/`'BOS'` - the opaque-id-vs-abbreviation
  mistake the agent's own ALWAYS-ON prompt rule warns against, on every one of
  those calls, even with the relevant head-to-head and quarter-scoring
  knowledge-base entries both already selected for the question. The router
  gained a `team_quarter_points` intent (with `period` and `opponent` slots)
  and an exemption from `_AGENT_ONLY` for it - a named player still forces the
  agent - and the new template reads the correct side's linescores per game
  (via `team_box_stats.home_away`, the same join `game_log` already uses)
  rather than deriving anything, so it needs no `--include-pbp` data at all.
  The agent's knowledge base also gained a pointer to the linescores shortcut
  for any team-level question that still reaches it, and a combined worked
  example (quarter math *and* an opponent JOIN together) for the player-level
  case that still has no template. `TABLE_SUMMARY`, `tests/query/test_router.py`,
  `tests/query/test_templates.py`, and `scripts/check_routing.py` all gained
  coverage.
- **Command reference examples render as real code blocks**: the CLI epilog's
  example commands and setup instructions were rendering in the generated
  docs (`commands.rst`) as an RST line block - preserved line breaks, but
  plain text, with no monospacing, highlighting, or copy button. A
  `sphinx-click-process-epilog` hook in `docs/conf.py` now rewrites those runs
  into `.. code-block:: console` blocks; `association --help`'s terminal
  output is unchanged. Docs also gained `sphinx-copybutton`, so every code
  block across the site - not just this one - now has a copy button.
- **Fix: the three-point line rendered upside down in shot charts**. The arc
  in `query/court.py`'s SVG path used the wrong sweep-flag, so instead of
  bowing away from the basket toward half court it drew the minor arc on the
  near side of the chord - dipping *below* the baseline instead. Confirmed via
  the SVG spec's own endpoint-to-center arc math (the arc's midpoint landed at
  y=478, past the baseline at y=470, instead of y=182 above the corners).
  `tests/query/test_court.py` gained a regression test that parses the
  rendered arc and asserts it bulges toward smaller y.

## 1.0.0 - 2026-09-07
- **Semantic versioning, and tooling to hold to it**: `pyproject.toml` is now
  the only place a version number is written. The package reads it back through
  `importlib.metadata.version` and exposes `association.__version__`,
  `docs/conf.py` imports that instead of repeating the literal, and the CLI
  grew `-V`/`--version`. The publish workflow already parsed the same file to
  check the tag agrees, so there is one source of truth and nothing left to
  forget on a bump.

  `scripts/bump_version.py` takes `major`, `minor`, `patch` or an explicit
  version, and optionally commits and tags. It refuses to run on a dirty tree,
  refuses to reuse an existing tag (PyPI would not accept the version twice
  either), and requires a `## Unreleased` section in this changelog to rename -
  a release with no description of what changed is worse than one that failed
  to happen. It never pushes: pushing is the step that makes a release
  irreversible.

  `scripts/release.sh` creates the GitHub release from an already-pushed tag,
  using the changelog section for that version as the release notes so the two
  cannot disagree. It re-checks that the tag, the packaged version and the
  pushed tag all match before prompting, because publishing the release
  triggers a PyPI upload that cannot be undone.

  The `Documentation` URL now points at
  <https://association.readthedocs.io/en/latest/> rather than the GitHub README.

- **Docs build on Read the Docs**: `.readthedocs.yaml` builds the Sphinx site
  on every push and previews it on pull requests. It uses `build.commands` with
  `uv sync --frozen` rather than the `python.install` shorthand, so the
  published docs are built from `uv.lock` instead of whatever a resolver picks
  on the builder - this toolchain has already been broken once by an unpinned
  upgrade (Sphinx 9 against sphinx-click), and docs that drift from what CI
  verified are worse than docs that fail loudly.

  `scripts/build_docs.sh` now takes an optional output directory, defaulting to
  `docs/_build/html`, so Read the Docs builds through the same script as
  pre-commit and CI. One caller of `sphinx-build` means the `-W --keep-going`
  that gates a commit is the same one that gates the published build, rather
  than two flag lists drifting apart.

- **Packaged for PyPI**: the project now carries the metadata a published
  package needs - `readme`, a BSD 3-Clause `license` and `LICENSE` file,
  author, keywords, 15 classifiers and `[project.urls]` - and builds a clean
  119KB wheel and 200KB sdist. Both were verified by installing into a fresh
  virtualenv outside the repository and running the CLI, which is the only
  check that actually proves the entry point and `py.typed` survive packaging.
  `setuptools` is pinned to `>=77` because the SPDX-string `license` field is
  an error on older versions.

  A `MANIFEST.in` rounds out the sdist with the shell completions, the
  changelog and the tests, and explicitly prunes the local artifacts. The
  prunes are belt-and-braces - setuptools' default sdist would not sweep up the
  266MB warehouse or the 736MB data tree anyway - but the cost of being wrong
  there is uploading hundreds of megabytes of scraped data to a public index.

  Two things were wrong for anyone installing from PyPI rather than a checkout.
  The README's setup block still told users to pull only `qwen2.5:7b` as "the
  default model", which has been stale since the router was split out: a fresh
  install following it would have no `qwen2.5:3b` and the fast path would fail
  on every question. And its ten relative links to source files would 404 on
  PyPI, which renders the README standalone, so they are now absolute.

  `.github/workflows/publish.yml` publishes via PyPI Trusted Publishing (OIDC),
  so no API token is stored anywhere. It runs the full gate suite on the tagged
  commit rather than trusting CI was green, checks that the git tag matches the
  packaged version, and runs `twine check --strict` - PyPI rejects an
  unrenderable README at upload time, once the version number is already spent.
  A manual `workflow_dispatch` can rehearse the whole thing against TestPyPI.

  New `installation` and `releasing` documentation pages. The one step that
  cannot be automated - registering the trusted publisher on PyPI - is written
  down in `releasing`.

- **`ruff format` adopted, and gated**: the formatter reads the same
  `line-length = 200` the linter does, so it *joins* the long prompt and SQL
  strings up to that width rather than wrapping them at 88. The assumption
  behind leaving it out - that it would fight the deliberate line length - was
  simply wrong; nothing it produced exceeded 200 columns.

  Reformatting touched 26 files across `src`, `tests` and `scripts`. Because
  much of what moved is prompt text that the router and fall-through agent
  depend on byte-for-byte, the change was verified by hashing
  `KNOWLEDGE_BASE`, `SYSTEM_PROMPT_TEMPLATE`, `ALWAYS_ON_TOPICS`,
  `ROUTER_PROMPT` and `ROUTER_SCHEMA` before and after: identical. The edits
  are all implicit string concatenations being joined (same value, one line)
  and long SQL being un-wrapped. One is a real fix - a docstring that opened
  with a quote character (`""""Most games with N+ ...`) was ambiguous to read
  and is now spaced.

  `ruff format --check src tests scripts` runs in CI, and a `ruff-format` hook
  runs in pre-commit ordered *after* `ruff --fix`, so an autofix cannot leave a
  file unformatted.

## 2026-09-06

- **100% public-API type completeness, and CI**: `pyright --verifytypes
  association --ignoreexternal` now scores **100%**, up from a 90.4% baseline
  (311 known, 14 ambiguous, 19 unknown of 344 exported symbols). Getting there
  needed a PEP 561 `py.typed` marker - without it pyright reports "No py.typed
  file found" and scores 0% of zero symbols - plus annotations for module
  loggers and instance attributes, and real type arguments for the bare
  `dict`/`list`/`tuple` in signatures. `parse.py` gained a `Row` alias
  (`dict[str, Any]`) so its return types say what they are rather than merely
  satisfying the checker, distinct from the existing `JSON` alias for what ESPN
  sent us.

  `scripts/check_types_complete.sh` runs the gate. It installs the package into
  a scratch directory first, because `--verifytypes` inspects an INSTALLED
  package and the editable install in `.venv` resolves through an import hook
  pyright cannot follow.

  Adding `py.typed` had a consequence worth recording: `mypy tests` went from 6
  errors to 43, because the package had been treated as untyped when checking
  tests, so `Pipeline` and friends were `Any` and every mismatch passed
  silently. (It was already red at HEAD - the pre-commit hook had not been
  catching it.) Fixed properly rather than suppressed: `Pipeline` now declares
  `JsonFetcher` and `DailyNetPointsFetcher` protocols, which is the honest
  dependency - it never touches throttling, retries or TLS impersonation, only
  `get_json` - and means a test double no longer has to inherit a network
  client to stand in for one. That resolved 37 of the 43 on its own.

  New `.github/workflows/ci.yml` runs the whole suite on push and pull request:
  ruff, mypy over `src` and `tests` separately, type completeness, docstring
  coverage, pytest, and the docs build with `-W`, uploading the built HTML as
  an artifact. `uv sync --frozen` fails if the lockfile has drifted from
  pyproject rather than silently re-resolving. The one step left out was `ruff
  format --check`, on the assumption that the formatter would fight the
  deliberate line-length of 200 - which turned out to be wrong, and was
  reversed the next day (see above).

- **Sphinx documentation, and hooks that keep it honest**: `docs/` builds a
  full site - architecture, a command reference generated from the Click CLI
  itself (so it cannot drift from the flags the code accepts), usage recipes
  for the things people actually do (fetching a season range, checking
  consistency, forcing a refetch, keeping a live season current), a
  data-sources page, the changelog, and an API reference covering every module.

  The API tree is generated recursively by `autosummary` at build time rather
  than from checked-in stub files, so a new module appears without anyone
  remembering to add it - confirmed: 30 source modules, 30 generated pages.

  Docstring coverage went from 51% to **100%** (120 of 120 public items, every
  module), and two pre-commit hooks keep it there: `scripts/build_docs.sh`
  builds with `-W` so a broken cross-reference or a missing module fails the
  commit, and `scripts/check_docstrings.py` fails on any undocumented public
  item - Sphinx catches malformed docs but renders an undocumented function
  perfectly happily, just uselessly.

  The data-sources page states plainly what the endpoints are: publicly
  readable without authentication, undocumented, unsupported, and subject to
  change without notice - a warehouse built from them is a snapshot of what
  they returned that day. It also documents why the fetcher is deliberately
  unhurried (5 req/s by default, checkpointed, nothing re-fetched unless
  asked).

  Sphinx is pinned below 9 because sphinx-click 6.x calls
  `sphinx.ext.autodoc.mock` as a function, which is a module there.

- **Reject scope slots a template cannot honor**: three live failures in a row
  were slots the router extracted CORRECTLY and the template silently dropped -
  a shot chart of "his last game" drew the whole season (803 attempts, not 14),
  NetPoints for "his last game" reported all 43, and a record "over their last
  10 games" would have covered the full season. `check_routing.py` cannot catch
  any of them, because routing was right every time.

  `order` and `date` are now declared: each template lists the scope slots it
  honors, and the dispatcher falls through when a question scoped to particular
  games meets a template that would answer for a different span. Slow beats
  confidently wrong, which is the trade this design keeps making.

  Measured across the 38 routing cases before adding it, exactly one slot was
  emitted-but-unhonored: `order` on `shot_distance`. Rather than accept a
  fall-through there, `shot_distance` now honors it too - "his average 3pt shot
  distance in his last game" is a real question, and the data is the same table
  a single-game chart already reads. A test asserts every template claiming to
  honor a slot actually reads it, so the declaration cannot drift from the code.

- **NetPoints for a single game**: "Show steph curry's netpoints from his last
  regular season game" returned the whole season - 43 games, 1,329 minutes -
  even though the router had correctly emitted `order: "recent"` and
  `limit: 1`. The template ignored both. Same silent substitution as the shot
  chart fixed alongside it, and the same shape of fix: `player_netpoints` now
  honours `order`.

  A single game is a genuinely different answer rather than a filtered one.
  Per-game NetPoints live in `net_points_player_game`, which is opt-in
  (`data pull --include-net-points-daily`), uses the normal NUMERIC season_type
  unlike `net_points_player`, and carries no play-type fingerprint - that is
  season-level only, and the output says so rather than leaving its absence
  looking like missing data. It reports o/d/t NetPoints, possessions on each
  side, and win probability added. Without the opt-in table the template falls
  through rather than quietly answering for the season.

  Cross-checked against the raw table: Curry's 2026-04-13 game reads 2.3857
  offense / 3.924 defense / 6.3096 total, and it is the same game the shot
  chart resolves for "his last regular season game".

- **Fouling out, and shot charts of a single game**: two failures reported from
  real use.

  "How many times has Wembanyama fouled out of a game" - the router got the
  shape right (threshold_count) but emitted stat "fouls committed" with
  threshold 1. The template correctly refused both, and the question then hung
  in the agent until it was aborted at 95s. `fouls` was missing from the
  threshold vocabulary entirely, and "fouling out" is six personal fouls - an
  NBA rule rather than a judgement call, and not something a 3B reliably knows.
  `fouls` is now a threshold stat, and the phrase is normalized in the router
  to stat=fouls, threshold=6, so the rule lives in one place. Wembanyama fouled
  out twice in 2026.

  "Create a shot chart of steph curry's last regular season game" charted the
  whole season - 803 attempts instead of that game's 14. Nothing scoped the
  request to one game. `shot_chart` now honours `order` the same way `game_log`
  does, resolving it to that game's event_id. (14, not the 22 rows the game
  has: free throws carry no court coordinates and are excluded from a chart.)

- **NetPoints fingerprint: the six categories that actually partition the
  total, and defense as its own section**: two corrections to how the
  fingerprint was presented.

  First, the defense column was present but effectively invisible. Sorting one
  combined table by total magnitude buries every defensively significant play
  type below categories whose defense is ~0 - for SGA, `turnover` carries the
  largest defensive value of any category (170.9) and landed 15th of 21, with
  `foul` (-82.3) at 3rd only because its OFFENSIVE value is large. Offense and
  defense now get a section each, sorted by their own side, which is also how
  espnanalytics.com presents it.

  Second, and this reverses a claim made in the previous commit: the categories
  ARE a partition, six of them. `two_pt`, `three_pt`, `free_throw`, `turnover`,
  `rebound` and `foul` sum EXACTLY to the offensive and defensive totals -
  verified against the separately stored `net_points_player.offense`/`.defense`
  for every top-minutes player in 2026, maximum deviation 0.005. The earlier
  "they do not sum" note came from summing all 21 categories, which mixes the
  partition with 15 overlapping descriptive slices (a driving layup at the rim
  counts in `driving`, `layup` AND `rim`, and those 15 sum to roughly twice the
  total on their own).

  So the six are shown as the breakdown, each section printing its own sum so
  the reader can check it against the headline - offense 8.55 plus defense 1.36
  is 9.91 per 100 possessions, the figure `net_points_player` stores
  independently. The overlapping slices follow as labeled detail, kept out of
  the column that is meant to add up.

  Also fixes a shadowing bug mypy caught while restructuring: the category list
  was bound to `detail`, which the headline block already uses for a list of
  strings - the same class of bug as the `per_100` shadowing fixed in the
  previous commit.

- **player_netpoints: one player's NetPoints and play-type fingerprint**:
  reported from real use - "what were SGA's netpoint stats this season"
  answered "Nikola Jokic leads the team in NetPoints this season with a total
  of 451.24", after 149s and five model calls.

  NetPoints was exposed only as leaderboard METRICS - ways to rank the league -
  so a question about one player's NetPoints had no shape to land in. The
  router reasonably chose `player_stat` with stat "netpoints"; that template
  correctly refused the unsupported stat and fell through (the guard added with
  shot_distance working as intended); and the agent then called
  `get_leaderboard` for the league, dropped SGA entirely, and reported Jokic.

  `player_netpoints` reports the season line (overall/offense/defense, per 100
  possessions, minutes, games) from `net_points_player`, plus the 21 play-type
  categories from `net_points_player_fingerprint` sorted by magnitude - the
  breakdown behind espnanalytics.com's "Net Pts Fingerprint". It handles both
  of that data's traps: `net_points_player` uses its own STRING season_type
  (filtering it with the numeric one silently matches nothing) and the
  fingerprint table has no season_type column at all.

  The fingerprint is reported **per 100 possessions by default**, since season
  totals mostly rank by playing time and the fingerprint exists to compare
  players; `rate: "total"` asks for totals, and raw totals stay in the result
  data either way. Cross-checks against the independently stored season rate:
  the `total` category over 4,725 possessions gives 9.91 per 100, matching
  `overall_per_100_poss` exactly.

  Found by the tests while adding that: `_phrase_netpoints` unpacked the
  headline row over its own `per_100` parameter, so a `rate: "total"` request
  printed season totals under a "per 100 possessions" heading. Routing 38/38.

- **player_history: one player across several seasons**: reported from real
  use - "what was klay thompson's 3pt percentage over the past 4 seasons (with
  attempts/makes)" answered with the LEAGUE's true-shooting leaders for 2020,
  four of them, with assists and rebounds columns. Wrong player, wrong stat,
  wrong season, wrong columns.

  The cause was structural rather than a slip: every template answered about a
  SINGLE season, so a multi-season question had nowhere to go and the router
  put it in the nearest shape it had. `player_history` reports one player's
  stat by season, most recent first, defaulting to four seasons and taking the
  count from `limit`. A percentage comes with its makes and attempts, since a
  percentage without volume behind it is the thing people immediately ask "out
  of how many?" about - which is exactly what the question asked for. The
  router's stat vocabulary gained the shooting percentages
  (threePointFieldGoalPct, fieldGoalPct, freeThrowPct), which had no
  representation at all.

  `leaderboard` now also refuses outright when a `player` slot is set: it ranks
  the league or a team, never one named person, and silently dropping the named
  player is how Klay Thompson's question came back about Robert Williams III.
  That guard is independent of the routing fix, and would have turned this
  wrong answer into a slow one rather than a confident one.

  Also fixes the guard test added with `player_compare`: its parser treated
  wrapped continuation lines in the router prompt as intent names, so it failed
  on words like "did" and "the" once descriptions grew to two lines. Routing
  check 36/36.

- **shot_distance, and no more silent stat fallback**: reported from real use -
  "what was steph curry's avg 3pt shot distance" answered "Stephen Curry
  averaged 26.6 points, 3.6 rebounds and 4.7 assists per game". Two bugs, and
  the first one was mine rather than the model's.

  `player_stat` treated a stat it did not recognize the same as no stat at all
  and fell back to its default points/rebounds/assists line - a silent
  substitution inside a template, which is precisely what templates exist to
  prevent. `player_compare` had the identical bug. Both now distinguish "no
  stat named" (default line, fine) from "stat named but unsupported" (fall
  through). `PLAYER_STAT_COLUMNS` also gained the shooting stats the router
  emits routinely - threePointFieldGoalsMade, fieldGoalsMade, freeThrowsMade -
  which were missing and so triggered exactly that fallback.

  Forced to the agent, the question then failed a second way: the agent wrote
  the correct distance formula from its KNOWLEDGE_BASE entry but dropped BOTH
  the 3-point filter and the season filter, reporting the all-shots,
  all-seasons average of 16.94 feet as a current-season three-point distance.
  The real figure is 23.6.

  So shot distance earned a template. The hoop is at (25, 5.25) and free throws
  carry NULL coordinates - a fixed formula over known columns, nothing that
  needs judgement. It scopes to the current season like every other template
  and reads the shot value from either `shot_value` or the equivalent stat.

  Also adds a deliberately tiny list in the router that forces questions no
  template computes to the agent regardless of the model's classification, for
  subjects that read like a supported shape ("points in the 3rd quarter") and
  are otherwise absorbed by a near-miss template. Shot distance was its first
  entry and left it the same day by earning a template, which is the intended
  lifecycle. Routing check 34/34.

- **head_to_head, and a code-side guard for an id filter that can never
  match**: reported from real use - "how many times did the 76ers play boston?"
  answered "the Philadelphia 76ers did not play against the Boston Celtics",
  twice, in 93s and 143s. They played four times.

  Four separate defects behind one wrong answer. (1) No template covered games
  between two teams, so the router chose `team_record`, invented `limit: 100`,
  and the limit guard added in stage 2.4 correctly rejected it - falling through
  to the agent. (2) The agent wrote `home_team_id = 'PHI'`, but team_id is an
  opaque all-digit VARCHAR ('20'), so the filter silently matched nothing. (3)
  Its `A OR B AND season = current_season()` applied the season filter to only
  one side of the matchup, since AND binds tighter than OR. (4) It reported a
  zero count as a fact about the world rather than a suspect result.

  Defect 2 is the important one, and it is NOT a consequence of the recent
  knowledge-base trim: the rule against it is ALWAYS-ON, and the assembled
  prompt for that exact question contained it verbatim, including
  `WHERE home_team_id = 'NY'` spelled out as a worked WRONG example. The model
  had the rule in front of it and wrote the wrong form anyway - which is this
  project's whole thesis restated, so the fix is code, not more prose.

  New `head_to_head` template resolves both team names to ids, counts games in
  both home/away directions, parenthesizes the matchup so a season filter cannot
  bind to one side only, and reports the series record. It defaults to the
  current season like every other template rather than answering all-time.
  Separately, `run_sql` now returns a `warning` whenever a query compares an
  `*_id` column to a non-numeric literal - unconditionally, not just on an empty
  result, because the failing query was a `COUNT(*)` that returns one row
  containing 0 rather than no rows at all. 2.16s and correct. Routing 32/32.

- **Trim the knowledge base to what the fall-through path actually needs, and
  keep models warm**: with every common shape ported to a template, fourteen
  `KNOWLEDGE_BASE` entries had nothing left to do - game logs across home and
  away, records alongside a game list, first/last game, single-game-vs-season
  totals, per-game averages, traded-player dedup, double-doubles, shot-chart
  guidance and `made_only`, rate-stat minimum samples, NetPoints rate-vs-total,
  fingerprint categories, "top N by NetPoints alongside box-score stats", and
  "a specific game implies its season". Each is a rule in code now, tested,
  where it cannot be truncated away or half-remembered.

  The criterion, since it is the reusable part: a SHAPE a template owns goes; a
  SCHEMA FACT that makes arbitrary SQL silently wrong or silently empty stays.
  So `fieldGoalsMade` already including threes stays (wrong math, not an
  error), the ISO-timestamp date trap stays (zero rows, no error), NetPoints'
  string `season_type` stays (matches nothing, no error), and per-quarter
  scoring and shot distance stay because no template derives them. Two tests
  pin both halves of that criterion. 6,350 tokens across 26 entries became
  2,776 across 12, and an assembled preamble is now ~5,000-5,500 tokens.

  Also corrects two claims from the previous commit, both measured wrong.
  ollama's default ALREADY keeps both the router and agent models resident -
  `/api/ps` reports qwen2.5:7b and qwen2.5:3b loaded together - so
  `OLLAMA_MAX_LOADED_MODELS` needs no change, and setting it from this process
  would be a no-op anyway since the server reads it (a systemd unit here). And
  the ~175s a fall-through costs is not a model swap: it is prefill of the
  agent's preamble at roughly 33 tok/s under a 6-core CPUQuota, which happens
  with both models already resident.

  What does bite is the idle unload after ~5 minutes, costing ~15s to reload
  the router. Requests now pass `keep_alive` (30m, override with
  `ASSOCIATION_KEEP_ALIVE`), which is a per-request field and so actually works
  from the client. Routing check 30/30.

- **Read the season out of the question text, in code**: "last season" is
  arithmetic on a calendar, not language understanding, and it was the slot the
  router most reliably dropped - "plot Curry's threes from last season" and
  "best true shooting percentage last season" both came back with no season at
  all, so the answer silently covered the CURRENT season. Both were carried as
  `known_gap` cases in `check_routing.py`; both are now fixed and the check is
  30/30 with no gaps outstanding.

  New `query/season_text.py` handles "last/previous season", "this season",
  explicit years, and season spans in either form - `2023-24` and `2023-2024`
  both mean the season ENDING in 2024, ESPN's convention and the form a
  hyphen-blind year regex gets exactly one year wrong. It declines to guess
  when a question names two different years ("compare 2023 and 2024"), ignores
  out-of-range years and numeric thresholds ("30+ point games" is not a
  season), and does not mistake "last 5 games" for "last season".

  Deliberately additive rather than a replacement: the model's `season` and
  `season_ref` slots are KEPT as a fallback, so code wins where it finds an
  answer and the model's slot applies where it does not, and phrasings the
  parser has never seen ("in his rookie year") route exactly as well as before.
  That choice follows directly from the negative result recorded above -
  removing those properties from the schema bought nothing elsewhere, so there
  was no reason to give up the fallback to get the fix. Confirmed live: Curry's
  threes now chart the 2025 season rather than 2026, and true shooting resolves
  to 2025.

- **Route on a 3B, generate SQL on the 7B**: routing and SQL generation are
  different jobs and were sharing one model. Benchmarked over the 30 real
  questions in `scripts/check_routing.py` (new `scripts/bench_router_models.py`
  reproduces it), every model from 1.5B to 8B scored 28-30/30 on routing,
  because constrained decoding does the structural work and the model only has
  to classify and fill slots - not a 7B-sized job:

      qwen2.5:7b   4.7GB  30/30 intent  29/30 +slots  2.00s median
      phi4-mini    2.5GB  29/30         29/30         1.42s
      qwen2.5:3b   1.9GB  29/30         29/30         1.12s
      llama3.2:3b  2.0GB  29/30         28/30         1.05s
      qwen2.5:1.5b 1.0GB  29/30         27/30         0.93s
      qwen3:4b     2.5GB      -             -        ~20s   (thinking)

  So `--router-model` now defaults to `qwen2.5:3b` (1.8x faster, same accuracy,
  2.8GB less RAM) while `--model` keeps `qwen2.5:7b` for the fall-through
  agent. At n=30 a one-case difference is inside the noise, so the 3B/4B tier
  is effectively tied and was picked on size and speed. Thinking models are
  disqualified on latency rather than accuracy: qwen3:4b spent ~20s per
  question reasoning before emitting the same tiny JSON object.

  Switching models required re-validating the prompt against the new one, which
  is a lesson in itself: the 3B routed "how many points did Jokic score in the
  3rd quarter against Boston?" to `game_log` rather than `other`, which would
  have answered with a list of games instead of quarter scoring - the same
  silent substitution `single_game_high` was added to fix. A worked negative
  example in the router prompt fixed it; 30/30 on the 3B. `check_routing.py`
  now defaults to the router's model rather than the agent's, so it tests what
  actually ships, and run history records both models.

  Both fit in RAM together (~6.6GB of 16GB), and `OLLAMA_MAX_LOADED_MODELS=2`
  is worth setting: without it ollama unloads one to load the other whenever a
  question falls through, measured at 152s on a real fall-through query -
  nearly all swap rather than inference.

  Also measured and worth recording as a NEGATIVE result: removing `season` and
  `season_ref` from the router schema (extracting the season from the question
  text in code instead) did NOT improve the accuracy of the remaining slots -
  both variants scored 30/30 on them. The hypothesis that schema properties
  compete for the model's attention is not supported by this experiment,
  though the eval is at ceiling and so cannot detect a small effect either way.
  Deterministic season extraction is still worth doing for its own sake (30/30
  vs 29/30 on the season slot) but it is not a contention fix.

- **single_game_high: a shape whose absence was a wrong answer**: reported
  from real use - "who had the most assists in a single game and how many did
  he have" was answered "Nikola Jokic led the league in assists per game in the
  2026 regular season, at 10.7", in 1.76s. The real answer was Ryan Nembhard
  with 23, on 2026-04-13.

  Nothing was broken. There was simply no intent for a single-game MAXIMUM, so
  the router picked the nearest shape it had (`leaderboard`) and that template
  answered its own question correctly and confidently. A missing shape does not
  produce a refusal - it produces a fast, fluent answer to a DIFFERENT question,
  which is worse than the slow wrong answers this work started from, because
  nothing about it looks wrong. The fix is a template, not a prompt tweak.

  `single_game_high` reads `player_game_log`, so it reports the value and which
  game it was ("23, on 2026-04-13 vs CHI"), handles ties, supports a named
  player ("Jokic's highest rebound total in a single game"), and defaults to
  the current regular season like every other template. The router's
  `leaderboard` description now says explicitly that it covers SEASON stats and
  that single-game questions belong elsewhere - describing the neighbouring
  shape is part of adding a shape.

  Also observed and worth recording: adding an intent perturbed slot extraction
  on unrelated questions - "plot Curry's threes from last season" had been
  keeping its season and started dropping it again, so it is now marked as a
  known gap alongside the true-shooting one. That is a real property of routing
  everything through one small model, and the reason `check_routing.py` is run
  after every change rather than trusted from last time. 30/30.

- **Bound run_sql results by tokens, not rows**: a row cap does not bound what
  comes BACK. Measured, `SELECT * FROM player_game_log LIMIT 200` serialized to
  ~44,000 tokens - nearly three times the whole 16,384-token window, from a
  single tool call. Over `num_ctx` ollama cuts the prompt to about half,
  head-first and silently, throwing away the system prompt: the exact failure
  this codebase was rebuilt to eliminate, and reachable by the `SELECT *` a
  small model writes constantly.

  `run_sql` now returns as many rows as fit `MAX_RESULT_TOKENS` (2,000), chosen
  by binary search, and says how many were dropped with a pointer to aggregate
  in SQL (COUNT/SUM/GROUP BY) or select fewer columns rather than listing every
  row. 44,084 -> 1,828 tokens on that query. When even a single row is too
  large, the column list comes back instead - that is what the model needs to
  write a narrower query, and it beats a silently empty result.
  `get_leaderboard`'s model-supplied `limit` is clamped to 100 for the same
  reason.

- **Extra columns on leaderboards, and a bounded array slot**: "top 10 in
  NetPoints alongside their points per game" routed to the `leaderboard`
  template, which had no slot for the extra columns and answered without them -
  a silent partial answer. A `fields` slot now feeds `run_leaderboard`'s
  existing extra-columns support, and the answer switches from a sentence to a
  table once extra columns are asked for, with the qualifying minimum in the
  header so "why isn't X on this list?" has a visible answer. An UNKNOWN field
  falls through rather than being dropped.

  The bug worth remembering: the array slots had no `maxItems`. Under
  constrained decoding an unbounded array lets the grammar permit "one more
  item" forever, and the model takes that offer - it emitted
  `["points","minutes","minutes"]` on one question and then hung for over FIVE
  MINUTES on the next, because at ~10 tok/s on CPU a looping array is a stall,
  not a typo. With `maxItems` those questions route in ~2.2s. Bound every array
  slot in a constrained schema.

  The model still over-fills to the cap, so the template deduplicates and drops
  any field that restates the ranked metric - asked for "top scorers with their
  rebounds", the router also returned "points", which rendered the same 33.5
  twice under two headings. `check_routing.py` now treats a list expectation as
  a SUBSET check for the same reason (dropping a requested field is a bug, an
  extra one is noise), flushes its output so a running check no longer looks
  identical to a hung one, gained a `known_gap` marker for the "last season"
  slot the router reliably drops, and warns against running two copies at once -
  concurrent runs put a CPU-only ollama into a reload loop that wedges it for
  minutes. 27/27.

- **player_compare on the fast path, with a nickname table**: "compare Luka
  and SGA this season" used to fall through to the agent, which got it wrong
  for a reason no `KNOWLEDGE_BASE` entry could fix - it wrote correct SQL
  (`current_season()`, ILIKE name matching) but expanded "SGA" to
  `'%Scottie G. Allen%'` and compared Luka Doncic to Luka Garza, then reported
  that the player with 0.4 steals led the one with 1.6. Nickname resolution is
  a lookup, not something to hope a 7B model knows: `entities.PLAYER_NICKNAMES`
  is a curated table of 21 shorthands (SGA, Wemby, the Greek Freak, KD, CP3,
  ...), every one verified to match exactly one row in `players`, matched
  against the WHOLE query rather than as a substring so "book" resolves to
  Devin Booker while "notebook" resolves to nobody - which also fixed "Ant"
  previously matching every player with those letters in their name (Durant,
  Anthony, Antetokounmpo). "Luka" and "Curry" are deliberately absent: they are
  ordinary first names and surnames shared with real players, and the
  clarifying question is the honest answer - a curated table must not quietly
  become the popularity guess that was measured and rejected in stage 2.2.

  Output is a fixed-width table rather than prose, since comparisons are the
  one shape where a sentence actively hurts. 122s and wrong -> ~2s and correct.

  The bug worth remembering: `player_compare` was added to the router prompt
  and given a `players` slot, but not to the intent enum in `ROUTER_SCHEMA`, so
  constrained decoding could never emit it and every comparison silently routed
  to `player_stat`. Constrained decoding is exactly as literal as it sounds - an
  intent absent from the enum does not exist, however well the prompt describes
  it. There is now a test asserting every intent the prompt describes, and every
  ported template, appears in the enum. Also: `shot_chart` now reads "threes"
  from either `shot_value` or the equivalent box-score stat, since the router
  encodes it either way depending on wording - which incidentally closed the
  known "last season" gap on that question. Routing check 25/25.

- **Per-question prompt assembly, and a guard so the context cliff can never
  be silent again** (stage 3): the preamble that started all of this - 10,295
  tokens against a `NUM_CTX` of 8192, truncated head-first to 4,098 without an
  error - is now built per question. Five always-on rules that apply to any
  SQL, plus up to three `KNOWLEDGE_BASE` entries selected by keyword overlap
  with the question (`prompt.select_knowledge`). Measured: 4,337-5,821 tokens
  depending on the question, never truncated.

  The migration plan had called for DELETING each entry as its template
  landed. That was wrong, and worth recording as wrong: the agent still writes
  free-form SQL for every question no template covers, and those hit exactly
  the same traps - "compare Luka and SGA" needs the traded-player dedup rule
  and the named-player filtering rule just as much as a leaderboard did.
  Deleting them would not retire a cost, it would regress the one path that
  still writes SQL by hand. What actually cost something was every question
  paying for all 26 entries at once. Nothing is deleted now; almost nothing is
  loaded. Selection is plain keyword overlap rather than embeddings - no model
  call (the point is to spend less time, not more), deterministic, testable,
  and a miss is cheap, since a missing entry is just what the agent had before
  that entry existed. Entries whose trigger vocabulary differs from their own
  prose carry explicit `keywords`, because "how FAR was his average three?"
  never says "distance".

  `build_system_prompt` now raises `PreambleTooLarge` rather than handing
  ollama a prompt it will quietly cut in half, and a test asserts every
  assembled prompt fits - so adding a KB entry or a tool description that
  overflows fails in CI rather than in a wrong answer six weeks later. The
  original bug was silent for four commits.

  `NUM_CTX` raised 8192 -> 16384 on the (now rare) agent path. The truncation
  rule behind that was measured, not guessed: a prompt UNDER `num_ctx` is
  evaluated in full (~3,700 tokens at 8192 came back with `prompt_eval_count`
  3,696), one OVER it is cut to roughly half (10,093 at 8192 came back 4,098).
  A ~5,400-token preamble costs ~100s of CPU prefill on a fall-through
  question, against being quietly wrong at 8192. Also cut `get_leaderboard`'s
  tool schema 956 -> 686 tokens by replacing the 80-name metric enum with a
  description - `run_leaderboard` already answers a wrong name with a
  close-match suggestion and the full list, so the model recovers in one turn
  and pays for the list only when it needs it.

  Accepted cost: a per-question system prompt changes the cached prefix
  between questions, so the agent path no longer reuses the KV cache ACROSS
  questions. It still reuses it across the tool-call rounds WITHIN a question,
  which is where the cost compounded. Confirmed live on a fall-through
  question: 3 model calls in 122s, down from 5 in 385s, with the agent
  correctly using `current_season()` and ILIKE name matching that the old
  truncated prompt had discarded.

- **shot_chart on the fast path, completing stage 2** (2.5):
  `render_shot_chart` extracted out of `Toolbox` into `query/shotchart.py`
  taking `con` and `out_dir` explicitly - the same split `leaderboard.py` got,
  so the template and the agent tool are one implementation. Templates now
  take a `TemplateContext` (connection plus output directory) rather than a
  bare connection, since this is the first shape that writes a file.

  Two real bugs the port surfaced: `Toolbox.__init__` created `out_dir`, so
  the extracted function silently depended on someone else having made the
  directory first (it creates its own now); and `shot_chart` was the only
  template not defaulting an unspecified season to the current one, so "plot
  Curry's threes" charted his entire career in a single plot - 3,665 attempts
  across every season, now correctly 488 for the current one.

  Also a measured LIMIT of the schema lever found in 2.2. Requiring
  `season_ref` in addition to `stat` made things worse: it fixed one dropped
  season but crowded out other slots, and "most games with 15+ assists in
  2024?" started coming back with `season_ref: "current"` and no `season` at
  all - a named year silently replaced by the current one, worse than the miss
  it was meant to fix. Measured across five questions, optional-with-sharper-
  wording won 4/5 against required's 3/5, so it was reverted. Require the one
  slot that pays for itself, not every slot you wish the model would fill.

  Stage 2 is complete: every shape the agent's four tools covered now takes
  the fast path, plus several they did not. Routing check 23/23, 277 tests.

- **game_log and team_record on the fast path** (stage 2.4): `game_log` uses
  the team-perspective query both `KNOWLEDGE_BASE` entries describe -
  `team_box_stats` for opponent and home/away, `games` for `winner_team_id`,
  and `team_score`/`opponent_score` computed from `home_away` rather than
  reported raw, since raw home/away scores force a per-row guess about which
  number was this team's. `team_record` reads `standings` instead, which is
  authoritative for a full season and carries streak and seed alongside the
  record. A record over a LIMITED set of games is tallied in Python over
  exactly the rows being displayed, so the total cannot drift from the listing.

  Three guards, each for a failure those entries describe: `standings` has no
  `season_type`, so a playoff-record question falls through rather than
  answering with the regular-season number under a playoff-sounding label;
  `team_record` refuses a `limit` outright, because "how did they do in their
  last 10?" answered with the full-season record is a silent substitution (it
  is a `game_log` question, and the router now sends it there); and a
  malformed `date` slot is dropped rather than passed through, since
  `games.date` is a full ISO timestamp and `= 'YYYY-MM-DD'` is valid SQL that
  silently matches nothing.

  Two routing bugs the LIVE run caught that `check_routing.py` had not: "show
  me the Knicks LAST 5 games" routed as `order: "first"` and answered with
  October games, and "how did the Celtics do in their last 10 games?" routed
  to `team_record`. Both fixed in the router prompt, both now asserted in the
  check, and the second also guarded in code. The lesson is in the check now:
  assert every slot that changes the answer, not just the intent - an
  intent-only assertion passed while the answer was wrong.

  Also improved team matching while here: word-boundary matches now rank ahead
  of incidental substring hits, so "LA" offers "LA Clippers or Los Angeles
  Lakers" instead of nine teams including "Atlanta Hawks", and clarification
  lists are capped at five. And `standings` stores wins/losses/seed as DOUBLE,
  so a 53-29 record was printing as "53.0-29.0" - now formatted as the
  integers they are, with win percentage in the conventional .646 form.

- **Double-doubles and triple-doubles as leaderboard metrics** (stage 2.3):
  planned as a new per-game threshold template, landed as two entries in
  `LEADERBOARD_METRICS` instead. The `KNOWLEDGE_BASE` entry for this had
  already recorded that ESPN precomputes `doubleDouble`/`tripleDouble` as a
  season COUNT of such games, so "most triple-doubles" is a leaderboard, not a
  recount from `player_box_stats` - no new template, and it inherits the
  season default, traded-player dedup and team filtering for free. These had
  been routed to `other` on purpose since stage 1; they now take the fast
  path. Confirmed live: "Nikola Jokic led the league in triple-doubles in the
  2026 regular season, at 34."

- **player_stat on the fast path, and a constrained-decoding lever** (stage
  2.2): one named player's season numbers, read from
  `player_season_stats_deduped` so a traded player's multi-row season is
  already collapsed. Reports per-game and season-total together rather than
  trying to tell "how many points did X average" from "how many points did X
  score" - a distinction the router got wrong more often than right, and one
  that disappears by answering both.

  An incomplete name is answered with a question, not a guess. The migration
  plan had called for a prominence tiebreak (most minutes, dominance
  threshold); measured against the real warehouse, no threshold works - on
  season minutes "Luka" separates only 2.05x (Doncic vs. Garza), and on season
  points the ratios are 3.8x for "Luka" but 3.1x for "Brown", where Jaylen vs.
  Bruce Brown is genuinely ambiguous. Any threshold that resolves Luka also
  resolves Brown, wrongly and silently. So the template returns "'Luka'
  matches more than one player - did you mean Luka Doncic or Luka Garza?" in
  ~1.5s instead. That is a handled outcome, not a fall-through: the template
  knows exactly what is ambiguous, so handing the problem to an agent that
  would spend minutes and then guess is strictly worse.

  Found while wiring this up: with `stat` optional in `ROUTER_SCHEMA`, the
  model omitted it even for a question appearing VERBATIM as a worked example
  in the router prompt, and rewording the prompt did not fix it. Making `stat`
  required fixed it immediately - a constrained decoder only reliably
  considers a slot it is required to emit, and answers `""` when there is
  none (now pruned in `route()`, along with any other blank string slot).
  Prompt wording persuades; the schema decides. Also set
  `additionalProperties: false`, which stopped the model inventing junk slots
  like `shot_value: 0` on player questions. `scripts/check_routing.py` caught
  this - it went 14/16 before the fix and 16/16 after, which is exactly the
  regression it exists to catch, since no unit test can.

- **Shared entity resolution + the leaderboard shape on the fast path**
  (migration stages 2.0 and 2.1): new `query/entities.py` unifies the two
  name->id implementations that had grown separately - `get_leaderboard`
  resolved teams and errored on ambiguity, `render_shot_chart` resolved
  players and silently took the first match. Both are defensible for what they
  do, so the split is now explicit rather than accidental: `find_*` returns
  every candidate best-first and lets the caller choose, `resolve_*` returns
  `Entity | Ambiguous | NotFound` and never guesses. Templates use `resolve_*`,
  because a chart drawn for the wrong Curry is obvious on sight while a NUMBER
  attributed to the wrong Curry is indistinguishable from a right answer -
  ambiguity there falls through to the agent instead of being answered. An
  exact full-name match beats substring siblings, so "Jaylen Brown" resolves
  even alongside a hypothetical "Jaylen Brown Jr.".

  New `query/leaderboard.py` extracts the "top N players by X" query out of
  `toolbox.get_leaderboard`, which is now a thin JSON wrapper over it - the
  fast-path template and the agent tool are one implementation instead of two
  that can drift. The router's `stat` slot maps onto `LEADERBOARD_METRICS`
  through an EXPLICIT alias table rather than `get_close_matches`: fuzzy
  matching is right for suggesting a fix to a model that can then correct
  itself, but a template silently ranking by whichever metric happened to
  score highest is exactly the substitution failure this architecture exists
  to prevent - unmapped names fall through instead.

  Added a `season_type` slot (`regular`/`playoffs`) while here, because
  without one a playoff question silently answered for the regular season -
  the same "answered an easier question and said nothing" failure the standing
  rules were written for. Both templates honour it, and every generated answer
  now names its season and period outright so a substitution is visible rather
  than silent.

  New `scripts/check_routing.py` is the regression check for the part of the
  pipeline with no types: a fixed question set run through `route()` only,
  asserting intent and slots, including cases that must NOT be answered by a
  near-miss template (triple-doubles, per-quarter scoring, player
  comparisons). Currently 13/13 in ~1-2s per question. Confirmed live: "who
  were the top 10 in netpoints/100 possessions?" went from 31-168s through the
  agent to 1.8s; "top 5 scorers on the Lakers?" answers in 1.65s with the team
  resolved and named. `get_leaderboard`'s 939-token tool schema and the four
  `KNOWLEDGE_BASE` entries it makes redundant are deliberately NOT removed yet
  - the agent still needs the tool for leaderboards that also need an opponent
  or box-score join, so that is a stage-3 change with its own regression
  check, not a side effect of landing the template.

- **Intent router + deterministic query templates in front of the agent**:
  `query` and `ai` now route a question through a small classifier before the
  tool-calling agent ever runs. Motivated by a hard failure: "who had the most
  30+ point games this season?" failed three times in a row, each taking
  3-6 minutes and answering a season-scoring-average leaderboard for the wrong
  season instead. Root cause was not the question - `SYSTEM_PROMPT + TOOLS` had
  grown to 10,295 tokens against `NUM_CTX = 8192`, and ollama truncates
  head-first, so only 4,098 tokens ever reached the model. Confirmed with a
  canary marker at each end of the system prompt: only the tail one came back.
  The discarded head held `TABLE_SUMMARY` (the entire schema), both standing
  rules, and the first ~15 `KNOWLEDGE_BASE` entries - including the one whose
  worked example is exactly this query's `COUNT(*) ... >= threshold` pattern.
  What survived was the tool schemas, whose descriptions say to always prefer
  `get_leaderboard`, which is precisely what the model did. Traced the preamble
  size back through history: it crossed 8192 at `c209516` ("Add get_leaderboard
  tool") and stayed silent for four commits, growing 2,416 -> 10,295 tokens in
  eight days without ever shrinking.

  Truncation also explains the latency, which was 100% model prefill (`model
  385.41s (5 calls), tools 0.04s (3 calls)`): the truncation offset slides as
  the conversation grows, so ollama's KV prefix cache misses on every
  iteration. Measured directly - a two-turn conversation runs 70.0s then 62.8s
  when the prompt is truncated, but 36.5s then 2.9s when it fits.

  New `query/router.py` classifies the question into `{intent, slots}` under a
  JSON schema passed as ollama's `format`, so decoding is constrained rather
  than merely prompted - this removes the malformed-tool-call failure mode that
  `_extract_unrun_sql` and the `pending_error` fabrication guard exist to catch
  after the fact. Its prompt carries no schema, no SQL and no gotchas (~430
  tokens), so it fits and stays cached. New `query/templates.py` holds the
  deterministic side; `threshold_count` is the first shape ported, with every
  correctness rule (column whitelist, current-season default, regular-season
  filter, per-token player matching) in code rather than prose.

  Templates phrase their own answers, which is not just cosmetic: ollama keeps
  one KV cache slot per model by default, so a second model call with a
  different system prompt evicts the router's cached prefix. Measured - three
  consecutive router calls run 11.63s / 1.27s / 1.66s, but interleaving a
  narrator call puts the next router call back to 11.18s. Dropping the narrator
  removed the eviction, removed the last place on the fast path where a number
  could be invented, and produced better prose than the model had ("Nikola
  Jokic had the most games with 20+ rebounds in the 2026 regular season, with
  5." versus "...playing such games in 5 games").

  Everything not yet ported falls through to the existing agent untouched -
  unported intents, slots that fail validation, and any router or template
  failure - so a router slip degrades to the old slow path, never to a wrong
  answer. Two model slips seen while building it are now handled in code rather
  than prompt: relative seasons ("last season" once produced `season=20222023`,
  which as a SQL filter silently matches nothing) resolve from a `season_ref`
  enum next to `current_season()`, and triple-doubles route to `other` instead
  of mis-routing into `threshold_count`. Confirmed live: 385s and wrong ->
  2.50s warm / 12.0s cold and correct, one model call instead of five, 431
  prompt tokens instead of 4,098. Added `--no-fast-path` to force the agent
  path for side-by-side comparison, and `FAST-PATH-MIGRATION.md` with the
  remaining shapes, the `KNOWLEDGE_BASE` entries each retires, and the
  projected 10,295 -> ~3,700 token preamble.

## 2026-09-05

- **CLI cleanup: fix confusing/wrong defaults, remove flags that were cheap to
  make default**: reviewed every subcommand's flags for confusing or invalid
  combinations. Fixed two real bugs: (1) `data check`'s live ESPN cross-check
  was the DEFAULT (`--offline` was the opt-OUT), directly contradicting
  README's own "Known limitations" note describing it as opt-in via a
  `--live` flag that didn't even exist - replaced `--offline` with `--live`
  (off by default, matching the docs and making a plain `data check` fast
  and network-free); (2) `data pull` defaults to `--season-types 2,3`
  (skips preseason) while `data check` defaulted to a hardcoded `1,2,3`, so
  the natural `data pull` then `data check` sequence reported preseason as
  entirely missing for data nobody asked to fetch - fixed by making
  `--season-types` auto-discover from local data (a new `discover_season_types`
  in `check/report.py`, same pattern `--seasons` already used), removing the
  mismatch structurally instead of just picking a new hardcoded string that
  could drift out of sync again. Also removed `--advanced-stats` from `data
  pull`/`data load` entirely - it's pure computed DuckDB views over data
  already fetched (no extra network request, no meaningful storage), so
  there was no real reason to make every caller opt into it - `warehouse.build()`
  now always builds `player_advanced_stats`/`player_season_advanced_stats`
  when `player_box_stats` is present. This surfaced a real robustness gap:
  since these views now run unconditionally instead of behind an isolated
  opt-in flag, a `player_box_stats` missing a column the formulas need would
  crash the ENTIRE warehouse build, not just skip two optional views -
  fixed by checking for the required columns up front and skipping (logging
  why) instead of raising, the same graceful-skip already used when
  `player_box_stats` isn't loaded at all. This also makes the earlier
  `--fetch-only` + `--advanced-stats` silent-drop combination impossible,
  since there's no longer a separate flag to drop. Confirmed live against
  the real warehouse: `data load` (no flags) now builds both advanced-stats
  views automatically; `data check --seasons 2026` (no flags) correctly
  auto-discovers all three locally-present season types and completes in
  ~6s fully offline.

- **Per-run history logging + timing metrics (query/ai)**: added
  `query/history.py`'s `RunHistory`, written by every `Agent.ask()` call
  (both the one-shot `query` command and each turn of the interactive `ai`
  REPL) to a new file under `.history/` (gitignored), named with a random
  hash - the command invoked, the full tool-call/thinking trace, per-model-
  call and per-tool-call timing, and the final answer (or a traceback, on an
  exception - wrapped in `ask()`'s `try/finally` so this happens even when
  the call never returns normally). Captured regardless of whether
  `--verbose` was passed - `--verbose` now only controls whether that same
  trace is ALSO echoed to stderr live, not whether it's recorded at all, so
  a run nobody was watching still has full evidence to look back at
  afterward. Every run also prints a one-line timing summary to stderr
  (total time, model-inference-vs-tool-execution split) - confirmed live
  this makes the actual bottleneck obvious: a real `get_leaderboard` call
  took 0.05s against ~53s of model inference across two rounds.

- **NetPoints fingerprint categories as get_leaderboard metrics, plus a KB
  entry**: net_points_player_fingerprint (added earlier today) went
  unused in a live query ("who has the best rim scoring NetPoints?") - the
  model fell back to an unrelated per-game NetPoints leaderboard instead,
  since nothing pointed it at the new table. Added all 66 fingerprint
  category columns as get_leaderboard metrics (`<category>_o_net_pts` /
  `_d_net_pts` / `_t_net_pts` for each of the 22 categories - two_pt,
  three_pt, driving, fastbreak, rebound, turnover, rim, ...), generated from
  the same category list `parse.py` already uses to build those columns
  (extracted to a new shared `association/net_points_categories.py`, one
  source of truth for both). Required a new `LeaderboardMetric.has_season_type`
  flag - this table, unlike every other metric's table, has no season_type
  column at all. Kept the free-text tool description short (listing the ~14
  original metrics by name, describing the 66 fingerprint ones by their
  naming pattern instead of spelling out all of them) while the JSON schema's
  `enum` still lists every valid value. Also added a KNOWLEDGE_BASE entry for
  the run_sql fallback path. Confirmed live: the exact previously-failing
  question now resolves via `get_leaderboard(metric='rim_o_net_pts', ...)` in
  one call, matching the values already verified directly against the DB.

- **NetPoints per-player skill/play-type breakdown (net_points_player_fingerprint)**:
  a user asked whether we had enough NetPoints coverage to recreate
  espnanalytics.com's "Net Pts Fingerprint" page - investigation found the
  page draws from a third, not-yet-ingested file
  (`fingerprint-files/nbafingerprint_{start_year}.json`, same public,
  unauthenticated bucket as the season-level file), far richer than anything
  already ingested: 66 NetPoints columns per player-season - 22 shot/play-
  type categories (two_pt, two_pt_shooting, three_pt, three_pt_shooting,
  assist, bad_pass, corner, cutting, driving, fade, fast_break, floating,
  foul, free_throw, hook, layup, mid_range, putback, rebound, rim, total,
  turnover), each split into offense/defense/total. Added as a new default
  (not opt-in) table, `net_points_player_fingerprint`. Real wrinkle,
  confirmed live: a season with no file published yet returns HTTP 403 from
  this bucket, not the 404/400 every espn.com endpoint uses for missing data
  - handled explicitly rather than left to raise, the same shape of fix
  `netpoints_client.py` already needed for its own bucket's AccessDenied
  quirk. Keyed by NBA.com's own player id with no ESPN crosswalk provided,
  same as the per-game NetPoints data - resolved by exact display-name match
  against `players` (ambiguous/unmatched names dropped, not guessed).
  Bio fields the source also carries (height, draft year, date of birth)
  are deliberately not kept - real, already-sourced-from-ESPN data on
  `players`, not duplicated from a second source that might disagree. Backed
  into the same in-season-refresh rule added earlier today (re-fetches while
  the season is still current). Backfilled locally for every season NetPoints
  covers (2019-2026): 4,338 rows, verified live against the source (LeBron
  James's two_pt_o_net_pts/turnover_d_net_pts match espnanalytics.com's raw
  file exactly) and through a real query (rim-scoring NetPoints leaders for
  the current season come back as Jokic/Giannis/SGA - plausible real players,
  not noise).

- **Season-aggregate fetches now stay current during an in-progress season**:
  a user asked whether `data pull` still works correctly mid-season -
  investigation found it didn't, for anything that isn't a per-game fetch.
  `standings`, `team_season_stats`, `team_power_index`, `player_season_stats`,
  and NetPoints' season-level tables all used the same existence-check
  resumability as immutable per-game data, but these reflect ESPN's own
  live, evolving computation while a season is in progress - once first
  pulled, every later `data pull` silently kept whatever was fetched first,
  with no `--force` reminder and no way to tell from `data check` (which
  only reports row counts, not freshness). Worse for `player_season_stats`
  specifically: it was only ever fetched at all once EVERY game in a
  season+type was fully resolved - during an in-progress season it was
  never called even once, not just stale. Fixed by tying each fetch to
  season completeness instead of plain existence: `standings`/
  `team_power_index`/NetPoints re-fetch (a single cheap request each) as
  long as the season is still the current one by this project's season-ends
  calendar convention (extracted to a new shared `association/season.py`,
  used by both the fetch pipeline and the query engine's existing
  `current_season()`); `team_season_stats`/`player_season_stats` (one
  request per team/player) re-fetch based on the season+type's own
  completion marker instead, so they stop as soon as that season+type is
  actually done rather than waiting for the calendar to roll over in
  October. The player-stats loop also moved out from behind the
  "season fully resolved" gate so it runs (and refreshes) every pull while
  the season is still in progress, not just after it ends.

- **Expose plays, add a verified per-quarter-scoring derivation, and a
  "don't silently answer an easier question" rule**: a real question ("how
  many games did Steph Curry score more than 15 points in a single
  quarter?") wandered through three wrong queries (hallucinated column
  names, an unrelated pivot to a single specific date) and then, worst of
  all, silently gave up and answered with an unrelated full-season stats
  dump - presented as if it satisfied the original question, with no
  indication the real question had been abandoned. Root cause of the first
  part: `plays` (play-by-play, opt-in via `--include-pbp`) was never added
  to KNOWN_TABLES/TABLE_SUMMARY at all, despite existing in the warehouse -
  the model had no way to even discover it via describe_table. Added it,
  and a new KNOWLEDGE_BASE entry with a verified derivation: per-quarter
  points aren't a stored column, but each scoring play in `plays` carries
  the running home/away score, so a play's own point value is that score
  minus the immediately prior scoring play's score for the same side (a
  LAG() window function). Two real bugs found and fixed while building and
  validating this, both confirmed live: (1) ordering by play_id is wrong -
  it's not reliably sortable as an integer across a whole game (confirmed:
  it broke chronological order badly enough to attribute 90+ points to a
  single play) - fixed by ordering on period + clock parsed to seconds-
  remaining instead; (2) filtering to one player BEFORE the LAG() window
  function (in the same CTE) breaks the ordering context the same way -
  made this exact mistake twice while building the pattern, fixed by
  computing the window function over every scoring play in the game first,
  filtering to one player only in an outer query afterward. Even correct,
  this derivation was measured (live, across the full dataset) to disagree
  with the official player_box_stats game total for ~1% of player-games -
  documented as a known, honest limitation (likely real ESPN play-by-play
  vs. box-score inconsistencies) rather than presented as exact. Separately,
  added a standing rule directly in the system prompt: if a query can't be
  made to answer what was actually asked, say so - never silently substitute
  an easier question and present it as satisfying the original one.

- **Schema-level helpers for the run_sql fallback path: current_season()
  macro, player_season_stats_deduped view**: get_leaderboard (below) moves
  leaderboard correctness into Python for its fixed set of metrics, but
  run_sql is still the escape hatch for anything outside that set (a
  leaderboard needing an opponent/per-game join, single-player lookups,
  etc.) - and those ad hoc queries were still relying on the model
  correctly recalling and re-deriving the same rules from KNOWLEDGE_BASE
  prose every time. Added the same two rules directly to the DuckDB
  warehouse instead: a `current_season()` SQL macro (built unconditionally,
  before any table load, so it works even against an otherwise-empty
  database) replacing the multi-line CASE/EXTRACT expression the model
  previously had to write out by hand, and a `player_season_stats_deduped`
  view that already collapses a traded player's per-team-stint rows to the
  combined row - no QUALIFY pattern needed for an ad hoc season-total/
  average query. Updated the relevant KNOWLEDGE_BASE examples and
  TABLE_SUMMARY to point at both. Confirmed live against the real warehouse:
  `SELECT current_season()` returns 2026, and player_season_stats_deduped
  drops the ~3,475 duplicate stint rows player_season_stats carries for
  every traded player.

- **New get_leaderboard tool: correctness rules moved from prose into code**:
  a series of real "top N players by X" queries kept failing in different
  ways even with KNOWLEDGE_BASE entries covering each one - a season default
  dropped as soon as a second filter was also needed, a min-sample rule that
  only applied to the one metric it was written for, and (worst) two runs of
  the identical question with the same thinking model producing two
  different metrics and answers five minutes apart. Root cause: every
  KNOWLEDGE_BASE entry makes the model responsible for remembering and
  re-deriving one more rule from prose, on every query, and that stops
  composing reliably as the list grows - more "thinking" time doesn't fix a
  fundamentally stochastic process being asked to reproduce a growing
  checklist exactly. Added `get_leaderboard(metric, season, season_type,
  min_sample, team, fields, limit)` (`query/toolbox.py`, registry in new
  `query/metrics.py`) - a fixed, known set of metrics (usage_pct, ts_pct,
  efg_pct, avg_points/rebounds/assists/steals/blocks, netpoints_total/
  offense/defense, netpoints_per_100/offense_per_100/defense_per_100) where
  the season default, the qualifying minimum sample, the NetPoints string-
  vs-numeric season_type quirk, and traded-player dedup are all resolved
  once in Python instead of re-derived by the model per query. `team` and
  `fields` are deliberately narrow (a resolved team name/abbreviation, a
  whitelist of extra box-score columns) rather than a free-text filter or
  arbitrary column passthrough, which would just reopen the same SQL-
  generation reliability problem this tool exists to close. The model's job
  shrinks to picking a metric name and filling a few slots - confirmed live,
  the exact two failing questions ("top 10 by average usage rate" and
  "average netpoints in 2026") now resolve in one tool call each, with the
  default (non-thinking) model, correctly qualified, no KB-composition
  needed. KNOWLEDGE_BASE entries for these metrics kept as run_sql fallback
  guidance (for a leaderboard that also needs an opponent/per-game join),
  now pointing at get_leaderboard first.

- **Agent: never finalize an answer right after an unrecovered run_sql
  error**: a real query hit a column-not-found SQL error, and instead of
  retrying with a corrected query, the model finalized with a fabricated
  answer using literal `[Player Name 1]` / `[NetPoints Value]` placeholder
  text as if it were real data (confirmed live). This is worse than a wrong
  answer - it looks like real, if truncated, output. Added a second guard
  alongside the existing SQL-as-prose one: track whether the most recent
  `run_sql` call in the turn errored, and if the model then tries to finalize
  with plain prose (not unrun SQL, which the existing guard already handles),
  refuse it - nudge a retry (same MAX_ERROR_RECOVERIES=2 cap pattern as the
  existing recovery), and if still unrecovered after that, return an honest
  "I ran into an error... and wasn't able to recover" message with the real
  error shown, instead of trusting whatever the model wrote. Deliberately
  scoped to `run_sql` only, not `describe_table`/`render_shot_chart` - a
  `describe_table` miss (e.g. an unknown table name) doesn't mean the model
  lacks real data, since an earlier `run_sql` call in the same turn may have
  already succeeded; treating every tool error the same way broke an
  existing test where a `describe_table` call incidentally failed against an
  empty test database with unrelated real data already in hand.

- **KNOWLEDGE_BASE: rate-stat leaderboards need a minimum sample, and a
  standing current-season default**: a user's real query ("top 10 players by
  average usage rate") came back with Izaiah Brockington at #1 (65.93% over
  8 games), and the rest of the top 10 were all 1-3-game stints too - the
  real leaders (Embiid, Giannis, Doncic, ~37-39%) were buried below dozens of
  small-sample flukes. Confirmed live: usage_pct is a ratio, so a few
  unusual garbage-time minutes can swing it far past what any sustained role
  reaches; `WHERE games_played >= 20` fixes the leaderboard completely.
  Added a KNOWLEDGE_BASE entry generalizing this to any rate/percentage stat
  (usage_pct, ts_pct, efg_pct) - same principle as the NetPoints-per-100
  minimum-minutes entry added earlier the same day. Separately, added a
  standing rule (in the system prompt directly, not just the growing gotcha
  list) that a question naming no season should default to the CURRENT
  season computed from `CURRENT_DATE` - not whatever season happens to have
  the most data loaded, and not silently substituted without saying so if
  the current season has no data yet. Confirmed live this works reliably for
  a simple query ("who leads the league in points?" correctly resolved to
  the current season); a compound query needing both the season default AND
  a minimum-games filter in the same query did not reliably pick up the
  season filter despite three different prompt placements tried - noted as
  a known small-model instruction-following gap (consistent with prior ones
  documented in this file), not chased further given diminishing returns.

- **NetPoints per-100-possession rate columns on `net_points_player`**: a user
  asked whether NetPoints has a normalized (rate) form, since `overall`/
  `offense`/`defense` are season cumulative totals - confirmed live, two
  players with the identical 82 games this season range from -194.62 to
  +164.15, so ranking by the total alone rewards playing more possessions,
  not being better per-possession. Investigated whether espnanalytics.com's
  own "Net Points / 100 Poss" toggle computes that client-side or pulls it
  from somewhere else: confirmed live (via the site's own network requests)
  that it fetches a second, separate flat file - `nba_net_pts100_data.json`
  - on the same public, unauthenticated S3 bucket as the file already fetched
  for `net_points_player`, rather than computing the rate in the browser.
  Added `overall_per_100_poss`/`offense_per_100_poss`/`defense_per_100_poss`
  (ESPN Analytics' own pre-computed values, not a local approximation) and
  `total_minutes` (for a per-36 comparison instead, if wanted) by joining that
  file in at parse time on (athlete_id, season, net_points_season_type) -
  confirmed live to be a unique key in both files. A handful of degenerate
  stints (e.g. a single scoreless playoff game) exist in the totals file but
  are dropped from the rate file - left NULL there rather than guessed, same
  fail-safe pattern as every other NetPoints join in this project. No new
  fetch flag needed - both files are already covered by the existing
  (default, not opt-in) NetPoints fetch.

## 2026-09-04

- **KNOWLEDGE_BASE: fieldGoalsMade already includes 3-pointers**: a user
  caught real bad math in a live answer - 2x made-2pt + 3x made-3pt came out
  well above the player's actual points. Root cause: fieldGoalsMade/
  fieldGoalsAttempted are TOTAL field goals (2pt AND 3pt combined, the
  standard box-score convention), and threePointFieldGoalsMade/Attempted is
  a SUBSET already counted inside those totals - not a separate, additional
  category the way freeThrows is. A prior KNOWLEDGE_BASE example (added this
  same day, for the NetPoints leaderboard fan-out fix) used fieldGoalsMade
  as if it were 2-point-specific, which is exactly this bug. Fixed that
  example and added a dedicated entry: true 2-point makes/attempts are
  fieldGoalsMade - threePointFieldGoalsMade (same pattern for attempted).
  Verified live on all three tables that carry these fields
  (player_box_stats, team_box_stats, player_season_stats): points ==
  (fieldGoalsMade - threePointFieldGoalsMade)*2 + threePointFieldGoalsMade*3
  + freeThrowsMade, exactly, every row checked. Re-ran the original query:
  all 10 rows now reconcile correctly.

- **Fix NetPoints leaderboard fan-out, and a dead-end recovery-cap message**:
  a real query ("top 10 highest NetPoints, with opponent and box score
  stats") joined season-level `net_points_player` to per-game
  `player_box_stats` on athlete_id+season - fanning out into one row per
  game (not per player), so `ORDER BY ... LIMIT 10` returned up to 10 games
  from whichever one or two players had the highest season total, not 10
  distinct players. Also referenced a nonexistent `net_points_player.
  season_type` column (only the string `net_points_season_type` exists) and
  an unquoted `AS 2pta`-style alias (invalid - identifiers can't start with
  a digit unquoted). The model then made it worse across retries: guessed
  wrong snake_case column names instead of calling describe_table, and
  silently dropped the "opponent" requirement. Added a KNOWLEDGE_BASE
  pattern: a request for an opponent or per-game stats alongside NetPoints
  means one game, so use `net_points_player_game` (already has its own
  season_type and event_id) joined on event_id+athlete_id, not season -
  verified live, all 10 rows now match ground truth exactly. Also fixed:
  once the SQL-as-prose auto-recovery cap was exhausted, a model that kept
  printing SQL instead of running it got that raw prose returned as the
  final answer verbatim - including "Let's run this corrected query" that
  never ran (confirmed live). The agent now says plainly that it couldn't
  get a working query, rather than returning text that only looks like an
  action still in progress.
- **Switched the CLI from argparse to Click; added shell completion**: Click
  generates bash/zsh/fish completion directly from the command definitions
  (subcommands, options, `--log-level`'s choices), so there's nothing to
  write or keep in sync by hand - the whole reason for the switch. Ready-made
  scripts live in `completions/`; `README.md` documents both that and the
  dynamic `_ASSOCIATION_COMPLETE=...` alternative. All CLI behavior -
  commands, flags, defaults, help text - is unchanged; only the argument-
  parsing implementation and its tests (now using Click's `CliRunner`)
  changed.
- **KNOWLEDGE_BASE: exact-date filtering, and abbreviations vs ids**: found
  while verifying the NetPoints per-game work, but general bugs unrelated to
  it. A live query filtered `games.date = '2026-04-12'` - `date` is a full
  ISO timestamp (`2026-04-12T22:00Z`), so exact equality against a bare date
  silently matches nothing; needs `date LIKE 'YYYY-MM-DD%'`. A second query
  then filtered `home_team_id = 'NY'` - comparing an id column directly to a
  team abbreviation, the same silently-empty failure mode already documented
  for names, just not generalized to abbreviations. Extended the existing
  KNOWLEDGE_BASE entry and added a new one, both with corrected examples.
- **NetPoints per-game data (net_points_player_game, net_points_team_game)**:
  opt-in via `--include-net-points-daily`. The source's per-game breakdown
  lives in a *different* S3 bucket than the season-level files, and this one
  rejects unsigned requests - reached via the same anonymous AWS Cognito
  identity-pool credential exchange espnanalytics.com's own frontend uses to
  read it (confirmed live: a plain unsigned request gets 403 AccessDenied;
  the identity pool ID is meant to be public, embedded in the site's own
  client-side JS - added `boto3` as a dependency to do the same exchange).
  One request per date already covered locally (not per player, not per
  game - one file covers every game played that date). Every field uses
  NBA.com's own player/team/game IDs, with no crosswalk to ESPN's provided
  anywhere in the data; resolved instead by matching (team, date) against
  this project's own `games` table (a team plays at most one game per date,
  so this is exact) and by exact player display-name match against
  `players` (ambiguous/unmatched names are dropped, not guessed). Confirmed
  live and handled: ESPN's `games.date` is UTC and can be a full day ahead
  of the US-local date NetPoints files under (an OKC @ NYK game ESPN stores
  as `2026-03-05T00:00Z` is filed under `2026-03-04`). Two real bugs found
  and fixed by checking actual coverage after the first backfill, not just
  trusting a clean exit code: (1) date+1 has to be tried BEFORE the exact
  date, not after - a team playing the same opponent on back-to-back nights
  (confirmed live: New Orleans @ LA Clippers on both 2026-03-19 and -20) has
  its own unrelated game sitting at the exact label date, which silently
  stole the match before the offset case ever ran; (2) a local date's true
  NetPoints label (date-1) has to be fetched even when no OTHER local game
  falls on that exact calendar day, or it's never fetched at all - confirmed
  live, a Lakers game was missed entirely this way, not just mis-resolved.
  Only NetPoints' own unique fields are kept; real box-score numbers ESPN
  already provides aren't duplicated from this second source. Backfilled
  locally: 1,654 dates.
- **NetPoints (net_points_player, net_points_team)**: fetches ESPN Analytics'
  current advanced player/team metric (successor to the discontinued Real
  Plus-Minus) from espnanalytics.com's public, unauthenticated S3-hosted JSON
  - a different domain from ESPN's own API, needing none of the TLS-
  impersonation tricks the rest of the fetcher relies on. Fetched by default
  as part of `data pull` (not opt-in), covered in `data check`'s new net_pts
  column, and backfilled into the local warehouse. Two real gotchas found and
  handled: NetPoints labels a season by the year it starts, not ends (off by
  one from every other table here, confirmed against this project's own
  games-played counts); and NetPoints uses its own team abbreviations that
  disagree with ESPN's for 9 of 30 franchises (e.g. GSW vs ESPN's GS, and the
  player file and team file don't even agree with each other for San Antonio)
  - both are converted at ingest time so the stored tables behave like every
  other table in the warehouse. Per-game NetPoints exists too but needs one
  HTTP request per player against a different (NBA.com) ID scheme with no
  direct ESPN-id crosswalk - deliberately left for a follow-up.
- **KNOWLEDGE_BASE: winner_team_id location, and win/loss tallies**: a live
  query ("Knicks' last 20 games and their record") surfaced two more bugs -
  the model referenced `tbs.winner_team_id` (winner_team_id only exists on
  `games`, not `team_box_stats` - a column-not-found error), and separately
  reported the win/loss record backwards (7-13 instead of the actual 13-7)
  because it tried to count wins/losses by re-reading a list it had already
  printed instead of computing the tally in SQL. A follow-up run then showed
  a *correct* aggregate record sitting next to 6 individually misclassified
  games, because the model summarized "wins against X, Y, Z" from memory
  instead of listing each game's own row. Tightened the existing team-game-log
  KNOWLEDGE_BASE entry and added a new one (CTE + window-function tally
  pattern, plus an explicit instruction not to collapse per-game detail into
  a hand-sorted summary). Verified live: all 20 games and the record now
  match ground truth exactly.
- **CHANGES.md, enforced via pre-commit**: added this changelog and a
  `changes-md` pre-commit hook (`scripts/check_changes_md.sh`) that fails any
  commit touching `src/` unless `CHANGES.md` is staged too - a manual entry
  is required, nothing is auto-generated from the commit message.
- **Team home+away game log SQL pattern** (`99df120`): two real queries for a
  team's last N games both got the answer wrong in different ways - one
  matched both sides of every league game with no team filter at all
  (doubled rows, wrong teams), the other filtered on `home_team_id` only
  (silently dropping every away game) and aliased the home team's name onto
  a "winner" column instead of using `games.winner_team_id` - reporting wins
  for games the team had actually lost. Added a KNOWLEDGE_BASE pattern
  pointing at `team_box_stats` (opponent_team_id/home_away baked in) and
  `games.winner_team_id` directly, plus computed `team_score`/`opponent_score`
  columns instead of raw home/away score. Verified live: all 20 rows now
  match ground truth exactly.

## 2026-09-03

- **Stop replaying thinking traces into the model's context** (`ec2fe6a`):
  `Agent.ask()` was feeding each turn's full reasoning trace back into the
  model's own context on every later tool-call round. Stripped it before
  appending to history - confirmed live, cut a follow-up iteration's
  prompt-eval time from ~5.6s to ~0.9s, and the saving compounds with each
  further tool-call round.
- **Fix advanced-stats query failures** (`fafe8fe`): a real query wrongly
  concluded advanced stats weren't loaded for the 2026 season. Root cause:
  the model compared `athlete_id` (a VARCHAR id) directly to a player's name,
  which is valid SQL that silently returns zero rows - and it read the empty
  result as missing data. `player_game_log` also never joined
  `player_advanced_stats`, so even a correct query against it would have come
  up empty. Fixed the view join and added two KNOWLEDGE_BASE entries (id-vs-
  name filtering, `ORDER BY games.date` for "first/last game" phrasing).
- **`data load` subcommand** (`77249a0`): rebuilding the DuckDB warehouse
  meant `data pull --build-db-only`, which always rescanned every table's
  Parquet files. `data load [--tables t1,t2,...]` rebuilds from Parquet
  already on disk, in full or scoped to a subset, without re-fetching.
  Replaces `--build-db-only`, which is removed.
- **Opt-in computed advanced stats** (`f971cfe`): added `player_advanced_stats`
  / `player_season_advanced_stats` (true shooting %, effective FG%, usage
  rate, Hollinger game score) as computed DuckDB views, gated behind
  `--advanced-stats`. ESPN's team season stats already carry the team-level
  equivalents natively; only the player side had a real gap. PER, Win Shares,
  BPM, and VORP are deliberately excluded - they need league-wide baselines a
  closed-form box-score ratio doesn't have.

## 2026-08-28

- **Full type annotation** (`94be7b9`): annotated all of `src/` and `tests/`,
  enforced going forward via `disallow_untyped_defs`/`disallow_incomplete_defs`
  in mypy. Caught two genuine bugs along the way: a `parse_standings` return-
  type mismatch, and a `Pipeline.client: ESPNClient | None` gap (fixed with a
  guarded `_live_client` property).
- **ruff/mypy pre-commit hooks** (`891292e`): added `ruff` and `mypy` (local
  hooks, split into separate `src`/`tests` invocations to avoid a spurious
  dual-resolution conflict) as pre-commit checks, and fixed the violations
  they surfaced.
- **Initial commit** (`440b40a`): resumable fetch from ESPN's undocumented
  stats APIs into compact Parquet flat files, a DuckDB analytics warehouse
  built from them, and a natural-language query interface powered by a local
  LLM via Ollama - no cloud API calls anywhere.
