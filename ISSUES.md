# Open issues

Everything known to be wrong, missing or unverified that still needs follow-up,
ranked by what a user would see. `AGENTS.md` ("Recording findings") says when
to add an entry and how. The short version: **record every finding, including
the ones that are not part of your task, and delete an entry in the same commit
that fixes it.** A fix that touches `src/` also gets a `CHANGES.md` entry; any
other fix is recorded by its commit message.

## Priorities

- **P1: wrong answer.** The system answers fluently and the answer is false,
  or answers a different question than the one asked. Silently wrong data that a
  template reads belongs here too.
- **P2: misleading or incomplete.** The numbers are right as far as they go,
  but they are short of the truth with no caveat, or a refusal names the wrong
  cause.
- **P3: refusal or gap.** A question real users ask is refused or falls through
  to the agent, or data the source publishes is missing from the warehouse.
- **P4: tooling, docs, low impact.** Nothing a user sees, or so rare it does
  not matter yet.

Within a priority, entries are ordered by how many questions they touch.

## Entry format

```
### Short title
- **Found:** YYYY-MM-DD, during what work
- **Evidence:** the query or file:line, with numbers
- **User sees:** a wrong answer / a refusal / nothing, with an example question
- **Next step:** the concrete thing to do first
```

"Reported, not re-verified" in an entry means the evidence comes from an
earlier session's notes and nobody has reproduced it since. Reproduce it before
fixing it. Unless an entry says otherwise, warehouse numbers were measured
read-only against `/home/jeff/code/association/nba.duckdb` on 2026-09-11.
They were measured again after that day's 15:24 `association data load` at
`3d3c8c6`, and none of them had changed, because the load re-read the same
Parquet files. Its only effect was to make the view fixes from `e1cc1c8` live.
**That no-change claim does not extend past `72b599c` (the 2000 playoff
discovery-pass fix, 2026-09-15)**, which moved `games` and `real_games` each
+10, `player_box_stats` +240 and `team_box_stats` +20; any figure measured
before that commit needs re-checking against the current warehouse.

## P1: wrong answer

### "For the <team>" beside a player is read as his own-team tenure even when the team is the subject: "show me stats for sixers when maxey scored 20+ points"
- **Found:** 2026-09-23, grading `live_sweep.jsonl` (yardstick-v2 F087) after
  the sweep merged (`28dfb9d`).
- **Evidence:** routes to `player_stat` with `player='Maxey', season=2026`
  (the `record_when` shape - a TEAM's record in the games a player reached a
  threshold - is never chosen), and now the "for/with the <team>" reading
  (`entities._scope_from_question_own_team`, F166's fix) fires on "for
  sixers", answering "Tyrese Maxey averaged 21.1 points per game in 387
  games with the Philadelphia 76ers over his career (2021-2026 regular
  seasons)". Before the sweep it answered his 2026 average. The key: the
  76ers are 35-28 in 2025-26 regular-season games where Maxey scored 20+
  (39-32 with the playoffs).
- **User sees:** a fluent line about the wrong subject (the player's
  average, where the team's record under a condition was asked) - the scope
  it did use is stated, which is why it is a different wrong answer and not
  a hidden one.
- **Next step:** two halves. The router: "stats for <team> when <player>
  scored N+" is `record_when` (the team is the subject, the player is the
  condition) - `_names_a_count`/the threshold grammar already sees the "20+";
  route it by the text (`CODE_ASSIGNED_INTENTS`-style) and add the case to
  `check_routing.py`. The own-team reading: do not fire it when the team is
  the grammatical subject of the sentence ("stats for sixers when ..." - the
  team precedes "when"/"in games"), only when it follows the player ("lebron
  ... for Miami", "westbrook ... for kings").
- **Source:** ours, not ESPN's.
- **GitHub:** #198

### The agent fall-through answers 1 question in 23, and does not finish 61% of the time
- **Found:** 2026-09-18, the first measurement of the agent path in this project
- **Evidence:** 24 questions stratified across the four fall-through causes, run
  through the real `Agent` at production defaults (`qwen2.5:7b` agent,
  `qwen2.5:3b` router, `fast_path=True`) against the live warehouse. **14 of 24
  did not finish within 240s**; one ran past 17 minutes before being killed by
  hand, pinning the 7B at 560% CPU throughout. Of the 9 that finished: **1
  correct, 1 partial, 6 wrong, 1 appropriately refused.** Median latency of the
  9: **155.5s** (range 81.7-192.4). The "documented 55s case" is the floor, not
  a typical case. Raw rows in
  `~/association-research/statmuse-2026-09/agent_results.jsonl`.
- **User sees:** a 2.5-minute wait that usually produces nothing, and when it
  does produce something it is wrong five times out of six. The fast path
  answers the same class of question correctly 48.8% of the time in 2-6s.
- **Bounded 2026-09-20, not yet resolved.** The wait is now capped:
  `Agent` takes `budget_seconds` (default 120, `--agent-budget`, 0 to remove
  it), checked before each model call so the first always runs, and giving up
  names why the templates declined the question rather than saying "Gave up
  after too many tool-call iterations". That removes the 17-minute case and
  the silent 4-minute one, and it is why this entry is no longer about the
  wait.
- **What remains is the product decision**, which is the user's: whether the
  fall-through runs at all by default. The measurement argues it should not -
  1 correct in 23, and `check_coverage`'s own stated reasoning ("the agent
  would query the same empty tables, more slowly, and is then free to fill
  the silence from its own weights") describes exactly what it was measured
  doing. Against that, `--disable-fallthrough` was deliberately scoped as
  development-only when it was added, which says the path is wanted in
  production. Until that is settled, the honest options are unchanged:
  default it off with an opt-in flag, or gate it to shapes it can serve.
- **GitHub:** #129

### Three fabricated agent answers, each verified false against the warehouse
- **Found:** 2026-09-18, grading the agent measurement above
- **Evidence:** each re-verified independently by the lead, read-only:
  - `lebron james 2 3 pointers all-time vs jazz on tuesdays` answered **"0 made
    out of 12,688"** (2PT) and **"0 made out of 5,923"** (3PT), 0.0% both.
    Measured: **399/695 (57.4%) 2PT and 77/238 (32.4%) 3PT** vs Utah. It also
    dropped "Tuesdays" silently - `render_shot_chart` cannot honor it.
  - `jonas valancunas vs last 10 games min` answered "there are no box stats
    available for Jonas Valanciunas in the last 10 games." **False** -
    `player_box_stats` holds those games with real minutes (4, 10, 3, 8, 6...).
    This is the wrong-cause refusal shape `AGENTS.md` names in the Maxey
    fingerprint example, now reproduced live rather than historically.
  - `Most reb by a hawk player history` answered "Jalen Johnson, 18", silently
    narrowing "history" to the current season. Measured all-time single-game
    high on record for the Hawks: **Dikembe Mutombo, 29** (2000 and 2001).
- **User sees:** fluent, confidently formatted, false answers with precise-looking
  denominators - the exact failure shape at the top of `AGENTS.md`, on the path
  that exists as the safety net.
- **Next step:** these are symptoms of the entry above, not separate bugs. Fix
  the path, not the three answers.
- **GitHub:** #130


### "game score" is answered with points per game, because the router substitutes a stat it knows
- **Found:** 2026-09-18, while making the computed advanced stats lookup-able
- **Evidence:** in the StatMuse replay corpus
  (`~/association-research/statmuse-2026-09/fastpath_after_rows_graded.jsonl`),
  "game score nba leader" arrives at `leaderboard` with `stat: 'points'` - not
  with an unknown stat, and not with no stat. `stat` is a REQUIRED slot in
  `ROUTER_SCHEMA`, so the decoder fills it with the nearest value it knows, and
  "game score" is not one of them. The answer is "Luka Doncic led the league in
  points per game in the 2026 regular season (minimum 20 games), at 33.5" -
  correct about points, and not what was asked. Game Score is Hollinger's
  single-game composite and it is a different ranking (`avg_game_score` for
  2026: Jokic 28.7, Doncic 26.4, Gilgeous-Alexander 26.4).
- **User sees:** a fluently wrong answer, with no sign anything was
  substituted. This is the failure shape at the top of `AGENTS.md`, arriving
  through a required slot rather than through a template.
- **Next step:** the metric half is done - `avg_game_score` is in
  `LEADERBOARD_METRICS` with its games qualifiers, and `game_score` is in
  `templates.players.ADVANCED_STATS` so `player_stat` can look it up too. The
  routing half is now also in code: `router._route_game_score` reads the
  two-word phrase off the question text (anchored `\bgame\s*scores?\b`, so
  "score" alone - which means points everywhere else - is not swept in) and
  sets the spelling each template expects (`avg_game_score` for `leaderboard`,
  `game_score` for `player_stat`; every other intent is left alone, since
  neither `player_compare` nor `player_history` nor `game_log` reads
  `ADVANCED_STATS`). `ROUTER_PROMPT` and `ROUTER_SCHEMA` are untouched -
  confirmed by hashing both before and after the change and by `git diff`
  reporting no change to `router_prompt.py`. Unit tests cover the positive
  case for both intents, the negative controls ("pacers score", "what was the
  score of the game", "Total points scored by the toronto raptors", "least
  points scored by the wizards", and the "scored" boundary case), a question
  genuinely about points, and that the value is left alone outside
  `leaderboard`/`player_stat`; both the intent-spelling table and the regex
  anchor were perturbed with `scripts/perturb.py` and CAUGHT.
  **The confirming measurement ran on 2026-09-20** against the live router
  (`scripts/check_routing.py`, 104 cases, qwen2.5:3b): 104 routed as expected,
  including the five cases added for the 2-point-percentage and shot-distance
  fixes below. The `fastpath_feed.py` replay over all 261 is still not re-run
  against ollama; the offline `reroute_recorded.py` replay stands in for it and
  reports no corpus row moved.
- **Note:** the same substitution is worth checking for every stat name the
  router does not know. "ats okc" and "players with the highest scoring triple
  doubles" are graded `wrong metric` in the same corpus.
  Another, found 2026-09-19 re-scoring the corpus after the B4 port:
  "Jabari smith defensive rebounds vs lakers last 5 games" arrives with
  `stat: 'rebounds'` - the enum has no defensive rebounds, so the log would
  show total rebounds under a question that asked for one kind.
  Two more from the 2026-09-20 web session (build `178c21f-dirty`, 45
  questions), **fixed 2026-09-20** (see `CHANGES.md`): "show me sga's 2pt
  percentage for the past 5 years" arrived as `player_history` with
  `stat: 'fieldGoalPct'` and answered overall FG% by season where 2-point
  percentage was asked for - `route()` now reads "2pt"/"2-pt"/"2 point"/"two
  point"/"2p" against percentage/pct/% and sets `stat` to
  `twoPointFieldGoalPct`, which `player_history` and `player_stat` now
  compute from field goals less the three-point columns (there is no stored
  2-point make/attempt column). And "who lead the league in avg 3 point
  distance" arrived as `leaderboard` with `stat: 'threePointFieldGoalPct'`
  and named Luke Kennard's 47.8%, while the "shot distance" phrasing arrived
  with a filler `player: 'player'` and was refused for naming a player it
  does not mention - `route()` now reads "shot distance"/"3 point
  distance"/"distance for 3 point" on `leaderboard` questions into a
  sentinel `stat` and drops the filler player, and `leaderboard` refuses on
  the sentinel, naming the real cause and pointing at `shot_distance` for one
  named player, before either wrong-cause path can run.

The 391 date-only games printed a day early (#76), 2008's team rebound columns
(#74) and the swapped 1990 Finals Game 5 were fixed on 2026-09-16. Before adding
to this section, re-read the P2s against the P1 definition: that is how both of
those were found.
- **GitHub:** #114


### `single_game_high` ignores a named team, and answers the league's high for the default season
- **Found:** 2026-09-21, live fast-path sample `~/association-research/statmuse-2026-09-large/live_sample200_2026-09-21/` (200 seeded-random reasonable StatMuse questions through the live router and templates on master `31b2ec6`, main checkout's `nba.duckdb`)
- **Evidence:** 3 of 200 sampled questions, all answered fluently and wrong.
  "most points in a game in cavs history", "most points in a game by a knicks
  playter" and "most points in a game in pistons history power forward" each
  route to `single_game_high` with `team` set and no season, and each answers
  "Bam Adebayo had the most points in a single game in the 2026 regular season:
  83". Reproduced offline by calling the template with
  `{'stat': 'points', 'team': 'Cleveland Cavaliers', 'season_type': 2}`.
  `HONORED_SCOPING["single_game_high"]` is `{"span"}` and `team` is not a
  scoping slot, so nothing refuses it - the `limit`/`stat` shape of #125 on a
  third slot. The corpus row "kawhi most threes in a game" is the same bug with
  the player dropped instead (answers Curry and Trey Murphy).
  5 of 1,972 reasonable large-set questions match the narrow regex
  `in a game.*(history|by a \w+ player)`; 65 (3.3%) say history/all-time/franchise.
- **User sees:** another team's player, another season, no caveat.
- **Next step:** honor `team` on the relation (`player_games` already carries
  the team), read "history"/"all-time" as `span: career`, and refuse a
  subject the template was given and cannot use. We hold the data.
- **GitHub:** #167

### A two-digit season ("23-24") is answered for the wrong year
- **Found:** 2026-09-21, same live sample
- **Evidence:** "points per game leaders for the 23-24 nba season" answers the
  2023 season (Embiid 33.1). Rerouted offline with the model stubbed:
  "2023-24" gives `season: 2024`, "23-24" keeps the model's `2023` -
  `_validate_season` reads the four-digit form from the text and not the
  two-digit one. 15 of 1,972 reasonable large-set questions use the form
  (`(?<![\d-])\d{2}-\d{2}(?![\d-])`, which also catches "02-03 to 06-07"
  ranges and "23/24" is not counted); the corpus has "nba leaders in plus minus
  in 25-26".
- **User sees:** the right shape for the season before the one asked.
- **Next step:** teach `_validate_season`'s text read "YY-YY" and "YY/YY"
  (consecutive years only, so "10-12" stays a score). `route()` only; hash the
  prompt constants to prove nothing else can move.
- **Re-measured 2026-09-21, after #95's fix landed.** The symptom changed, not
  the gap: "23-24" still is not read as a year, so a bare model `season` for
  it is dropped rather than kept (#95 no longer trusts a `season` int with
  nothing in the text to back it), and the leaderboard now answers the
  *current* season (2026) instead of the year before the one asked (2023) -
  still wrong, for the same underlying reason (`_validate_season` cannot read
  "23-24"), just a different wrong year. The fix above is unchanged and still
  open.
- **GitHub:** #168

### A thresholdless `threshold_count` is rewritten to `leaderboard` before the subject is restored
- **Found:** 2026-09-21, a Sonnet agent replaying the 172 distinct
  `.history/*.log` web-session questions offline through `31b2ec6`
- **Evidence:** "how many games did embid play" answers "Tyrese Maxey led the
  league in minutes per game in the 2026 regular season, at 38.0". The agent's
  trace: `_route_threshold` (`router.py`, ~1672) rewrites to `leaderboard`
  without checking for a subject, and runs before `_route_subject_slots`, which
  excludes `leaderboard` from `_SUBJECT_RESTORED_INTENTS`; `_SUBJECT_OF_HAVE`
  has no "play/played" verb either. **Lead re-check:** with a `player` slot
  present the rewrite keeps it (`leaderboard` then refuses a named player), so
  the wrong answer needs the router to have dropped the player as well.
  1 of 172 web-session questions; population in the large set unmeasured (it
  depends on the model's slots, not on a regex). The live sample has 4
  `threshold_count` fall-throughs on `threshold: 0` for "most X" questions
  ("luka stats most turnovers"), the refusing side of the same rewrite.
- **User sees:** a league leaderboard in a stat nobody asked about.
- **Next step:** guard the rewrite on the question naming no player
  (`players_named_in`), and send a named player's "how many games" to
  `player_stat`.
- **GitHub:** #169

## P2: misleading or incomplete

### "Since 2000-01" still reads the default season live, although route() reads it on stubbed slots
- **Found:** 2026-09-24, grading `live_day2.jsonl` (yardstick-v2 F161) after
  sweep 2 merged (`637ff4e`).
- **Evidence:** "players with 33 point and 13 rebound and 10 assist 2 blocks
  and 2 steals games since 2000-01" composes over "2026 regular season -
  last 3 games" on the live run, exactly as before the sweep, while
  `tests/query/test_router.py`'s stubbed case for the same wording files
  `since: 2001`. Both arms of sweep 2 passed the stubbed test; neither could
  run the model. The live trace is the place to look: the model's raw
  slots for this wording (a `season` beside the range? a `limit`?) and
  which post-processing step wins.
- **User sees:** the right five lines counted over one season where 26 were
  asked - the scope is stated, so it is correctable.
- **Next step:** capture the live raw slots (`association query --verbose`),
  add the case to `check_routing.py` with the exact expectation, and fix
  whichever step drops `since`.
- **Source:** ours.
- **GitHub:** not yet filed

### `player_stat`'s coverage floor is computed from the wrong slot list, and misses `situation`, `since`, `game_n`
- **Found:** 2026-09-24, in passing while verifying the K3-2 conference/division
  narrowing did not need a new coverage-floor entry of its own.
- **Evidence:** `templates.common._sources_for_player_stat` decides whether a
  question reads the season line (`player_season_stats_deduped`, floor 1977)
  or box scores (`player_game_log`, floor 1994) by checking
  `_BOX_SCORE_SCOPING = ("opponent", "venue", "without")` against the slots -
  but the template's OWN decision of which table to actually read,
  `players._player_stat_reads_box_scores`, checks a longer list: also
  `situation`, `since`, `measures` (below/above), `game_n`, `season_type_unstated`
  and `own_team`. A question that sets one of the five slots the floor check
  does not know about is checked against the WRONG table's floor (1977, too
  lenient) while actually reading the narrower one (1994). Reproduced
  read-only against `/home/jeff/code/association/nba.duckdb`, 2026-09-24:
  `check_coverage("player_stat", {"player": "Michael Jordan", "stat":
  "points", "season": 1990, "situation": "on tuesdays"})` returns `None` (no
  floor problem), and the template then answers "No 1990 regular season
  games found for Michael Jordan" - true of `player_game_log` alone, and the
  same false-cause shape AGENTS.md warns about ("a refusal that names the
  wrong cause"): the real cause is `player_game_log`'s 1994 floor, not that
  Jordan skipped Tuesdays in 1990. Pre-existing (the calendar half of
  `situation` has been honored since step 3, K3, before this session), not
  introduced by K3-2's conference/division addition - just found while
  checking whether K3-2 needed a similar fix and it did not (the alignment
  narrowing's own floor, 1988, is never the binding one regardless, so this
  gap is `since`/`game_n`/`measures`/`own_team`'s to begin with, situation is
  only one of five).
- **User sees:** a "no games found" answer for a pre-1994 box-score-narrowed
  question about a player whose career reaches back that far, instead of the
  informative "player game logs only go back to 1994" sentence every other
  under-floor question on this template gets.
- **Next step:** make `_sources_for_player_stat` call
  `_player_stat_reads_box_scores` (or the same slot list) instead of its own
  narrower `_BOX_SCORE_SCOPING`, so the two decisions read the same slots. Not
  fixed here: `templates/players.py` is outside this task's file ownership.
- **Source:** ours, not ESPN's.
- **GitHub:** #212

### The router invents a name in the `opponent` slot, and the refusal repeats it: "jay huff game log vs Embiid" refuses about Nikola Jokic
- **Found:** 2026-09-24, grading `live_rest.jsonl` (yardstick-v2 F142).
- **Evidence:** routes `player_matchup {'player': 'Jaylen Huff', 'opponent':
  'Nikola Jokic'}` - "Embiid" became Jokic (the known lowercase-embiid
  substitution, AGENTS.md "Why embiid specifically"), and "Jay Huff" became
  Jaylen Huff. `override_invented_players` checks `player`/`players` against
  the question and never `opponent`, so the invented opponent survived into
  `refusals._opponent_is_a_player`, whose sentence names him: "'Nikola
  Jokic' is a player, not a team". The cause is right (a pair question);
  the name is one the question never held.
- **User sees:** a refusal about a player they did not mention.
- **Next step:** run the invented-name check over `opponent` too (a name
  with no word in the question is dropped, or replaced from
  `players_named_in` when the count is exact), before any refusal or
  template reads it.
- **Source:** ours, not ESPN's.
- **GitHub:** #206

### "Since 2000-01" is not read as a span: a league-wide multi-line count answers the default season
- **Found:** 2026-09-24, grading `live_rest.jsonl` (yardstick-v2 F161).
### A composed league-wide read ignores `since`/`until`: "... games since 2000-01" answers the current season
- **Found:** 2026-09-24, grading `live_rest.jsonl` (yardstick-v2 F161);
  re-diagnosed the same day fixing the router half of #207.
- **Evidence:** "players with 33 point and 13 rebound and 10 assist 2 blocks
  and 2 steals games since 2000-01" now routes `since: 2001` (the router
  read "since 2000" before, a season early - fixed). But
  `compose/core.py:_resolve_everyone` settles the span from `season`/`span`
  alone and never reads `since`/`until`, so the composed rows are "2026
  regular season" whatever the router sent: `compose.answer` with
  `since: 2001` prints 3 rows, all 2026. Measured on the warehouse
  (`player_game_log` joined to `real_games`): 11 regular-season games since
  2001 clear all five lines (1 more in a postseason), 3 of them in 2026.
  The "filler `limit: 3`" this entry used to name was not a limit at all:
  every recorded routing of this question (yardstick-v2 live runs and the
  baseline replays) is `since: 2000` with no `limit`, and "last 3 games" is
  the row count of the 2026-only read.
- **User sees:** 3 games of one season where a 26-season span was asked -
  the span is stated, so it is correctable, but no wording reaches the span.
- **Next step:** `_resolve_everyone` reads `since`/`until` into the span the
  way `scoped_player` does for a named player (compose owns it).
- **Source:** ours, not ESPN's.
- **GitHub:** #207

### A short, genuinely ambiguous question is guessed at rather than asked about: "Tatum rec"
- **Found:** 2026-09-23, working yardstick-v2's wrong-land bucket 4
  (`~/association-research/yardstick-v2/wrong_land.md`, F112).
- **Evidence:** routes `player_stat {'player': 'Jaylen Tatum', 'season':
  2026, 'season_type': 2}` and answers "Jayson Tatum averaged 21.8 points,
  10 rebounds and 5.3 assists ... in 16 games" - a specific, confident stat
  line for a question the key marks unanswerable as written ("no record
  type - team win-loss vs. personal statistical record, no opponent, no
  time frame"). This is NOT the invented-name shape `override_invented_players`
  already catches: "Tatum" is a real word IN the question, so the router's
  "Jaylen Tatum" survives that check on its own strict rule (any one word of
  the name is enough, and "Tatum" is one). The actual fault is that "rec" is
  read as nothing in particular and the whole question collapses to a
  default player-stat line, when a careful reader would ask what "rec"
  means before guessing.
- **User sees:** a fluent, specific-looking answer to a question that has no
  single right reading - the mirror-image failure shape AGENTS.md warns
  about, dressed as data rather than as the refusal it should be.
- **Next step:** unclear how to fix narrowly without a general "ask when
  ambiguous" rule this project has deliberately avoided (a reasonable
  default beats a question, per Jeff's rule - but there is no reasonable
  default between "team record" and "personal stat line" here, unlike a
  namesake pick). Possibly: a bare "rec"/"record" with a name and nothing
  else (no stat word, no opponent, no season) is genuinely ambiguous between
  `team_record` and `player_stat` in a way the router can't resolve, and
  should refuse naming BOTH readings rather than silently picking one -
  needs measuring how often this shape appears in the routing corpus before
  building anything, since a broad "ask on short questions" rule risks
  breaking working ones.
- **GitHub:** #200

### A question with zero valid readings gets a fluent, self-chosen answer: "25-26 knicks playoff statistics vs other historic teams"
- **Found:** 2026-09-23, same session, yardstick-v2 F097.
- **Evidence:** routes `team_leaderboard {'stat': 'win_percentage', 'team':
  'New York Knicks', 'season_type': 3}` and answers a 16-team postseason
  win-percentage ranking - a real, computed answer, but to a framing the
  question never named (no comparison metric, no named set of "historic
  teams" to compare against). The key marks this as needing clarification
  with zero valid readings.
- **User sees:** a confident, well-formatted table that answers a question
  nobody asked, with nothing marking it as a guess at what was meant.
- **Next step:** per AGENTS.md ("when it cannot be repaired, say so"), this
  may be unfixable without guessing what "vs other historic teams" should
  compare - filed rather than attempted. If a future pass wants to try:
  `team_leaderboard` already refuses a `limit` for `team_record` (rejects
  ranking a single team); a comparable refusal for `team_leaderboard` when a
  `team` is named ALONGSIDE no comparison metric the question itself states
  ("vs" with nothing concrete after it) might be the shape, but was not
  measured here.
- **GitHub:** #201

### A pair relation with a with/without split is not built: "steph curry record vs lebron regular season without kd"
- **Found:** 2026-09-23, same session, yardstick-v2 F114.
- **Evidence:** routes `with_without {'stat': 'wins', 'team': 'Los Angeles
  Lakers', 'limit': 10, 'fields': ['steals', 'rebounds'], 'season': 2026,
  'season_type': 2, 'without': ['kd']}` and answers the Houston Rockets'
  with/without-Durant record - an entirely different question (LeBron's
  Lakers, not Curry's Warriors vs Lakers, ever came up). The underlying
  shape - two named players' head-to-head record, further split by a
  THIRD player's presence/absence - has no relation built for it:
  `with_without` narrows one team by one absent player;
  `player_matchup`/`head_to_head` narrow two sides' meetings but read no
  `without` at all (`HONORED_SCOPING["player_matchup"]` explicitly refuses
  it for a genuine two-player matchup, in `templates/common.py`).
- **User sees:** a wrong answer, fluently, about players and a team the
  question never named.
- **Next step:** not attempted - a genuinely new relation (a pairing of two
  players' meetings, further split by a third player's team-tenure absence,
  mirroring `_tenure_clause`'s existing "teammate's absence" reading but
  applied to one SIDE of a matchup rather than to a single player's own
  games). Scope and cost not assessed; filed for whoever picks up
  `player_matchup`'s own `without` refusal next.
- **GitHub:** #202

### Five P7-bucket "partial" answers from the yardstick are still open
- **Found:** 2026-09-23, same session - not reached; recorded from
  `~/association-research/yardstick-v2/wrong_land.md`'s own evidence rather
  than independently re-diagnosed, since no time remained in this pass to
  read each one's code path. Listed here so the next agent does not have to
  rediscover the list from scratch, with the file's own F-numbers for the
  full evidence (query, route, answer, key) each already carries:
  - **F058**/**F060** - `period_split` with "each game"/"every game" in the
    question still applies a `limit` of 1 as a window, showing one row where
    every game's row was asked for; aggregate totals are otherwise right.
  - **F100** - a "fewest playoff wins since 2022" ranking lists only the 28
    teams that qualified, when two more (Hornets, Wizards) belong in the
    zero-win tie for never having qualified at all.
  - **F128**/**F129** - "last N games" chronological logs (crossing
    season-type boundaries, ISSUES.md's own `season_type_unstated`
    mechanism from this session's bucket 1) now find the right games but do
    not state the total/point-differential sum the question asked for,
    leaving it for the reader to add up the rows.
- **User sees:** mostly right answers, each short of the full truth in one
  specific, named way per the key's own grading notes above.
- **Next step:** each needs its own read of the relevant template
  (`period_split`, `team_leaderboard`) - not attempted in this session.
  Priority within this group should follow AGENTS.md's own ordering (a
  wrong number > a missing one > a missing label), which was not assessed
  per-item here.
- **GitHub:** #203

### Two callers sharing one ollama instance corrupt each other's router output; ambient CPU load alone does not
- **Found:** 2026-09-22, investigating the "router's slots depend on which
  llama-server load answered" finding below (moved here, re-measured, and
  re-diagnosed - the original entry's hypothesis was wrong about the
  mechanism). Measured on this machine, ollama 0.33.3, CPU-only, 8 cores,
  16 GB, `qwen2.5:3b` digest `357c53fb`, `ROUTER_PROMPT`/`ROUTER_SCHEMA`
  unchanged throughout (confirmed by `git diff` on `router_prompt.py` before
  and after: empty), temperature 0, against
  `~/association-research/yardstick-v2/repro/moved_c4.json` (the 29 questions
  the original finding flagged) and a baseline extracted from that day's full
  live run (`live_c2.jsonl`).
- **Evidence:** two separate hypotheses were tested and only one reproduces.
  **(1) Reload/load-time CPU load, alone, does not move routing.** 5 solo
  unload+reload cycles on a quiet machine, then 5 more with the machine
  driven to load average ~9-30 by `python3 -c 'while True: pass'` busy loops
  (never the repo's test suite) running at each reload: all 10 cycles landed
  on the *identical* 29/29 routing, letter for letter, differing from the
  day-before baseline in exactly the same 3 (of 29) minor slot encodings every
  time (e.g. `{'season': 2026}` vs `{'limit': 1}` on "who leads the league in
  points per game?" - both resolve to the same answer, the kind of encoding
  difference `AGENTS.md` already says not to grade). This 3-of-29 pattern is
  byte-identical to the historic `reroute_c4_reload2.jsonl` (the "mostly
  recovered" second reload from the original investigation), meaning every
  solo reload done today - 10 of them, spanning a 3x range of load average -
  landed in the same "good" regime the original investigation only reached
  after two reloads. The `llama-server` launch line was identical across all
  10 (`-b 512 -ub 512 --flash-attn auto -np 1`, no batch or cache-type
  change); the only variation seen was an explicit `-t 8` present on some
  loads and absent on others (`ps`/`/proc/<pid>/cmdline`), uncorrelated with
  which of the two regimes came up. Pinning `num_thread` in the router's
  ollama `options` (tested directly in `router.py`, reverted - see below) made
  no difference either, consistent with thread count not being the lever.
  **(2) Two callers hitting the same ollama instance concurrently corrupts
  one of them, reproducibly.** Running two independent `reroute.py`
  processes against the 29 questions at the same moment (each doing its own
  `ollama stop` + first request, simulating two agents or a CLI-plus-web-server
  both asking questions right after an idle-unload) reproduced 18 of 29
  flipped - not the stable 3 - three times out of three trials, with the
  *identical* 18 questions flipping each time (diffed byte-for-byte across
  trials) regardless of which of the two processes "won". Unlike the stable 3,
  these are real intent-level wrong answers: "Bam adebeyo jan 19" (`player_stat`
  with `date: '2026-01-19'`) becomes `game_log` with `order: recent, limit: 1`
  - the most recent game, not the one asked about; "Centers stats game log vs
  kings" turns `team: 'Kings'` into the fabricated `'Los Angeles Kings'`;
  "vj edgecombe three points made per game after making one three in first
  quarter" drops from `period_split` to `other`, falling through where it
  used to answer. Pinning `num_thread` (tested the same way) did not prevent
  this either - unsurprising once the trigger is understood: this is not a
  thread-sizing question. `llama-server` runs `-np 1` (one parallel decode
  slot); two independent HTTP clients issuing `ollama.chat` calls against one
  slot at the same moment is exactly the condition `scripts/check_routing.py`'s
  docstring already warns about ("two concurrent runs... put ollama into a
  reload loop that wedges it for minutes") - what is new here is that the
  failure mode is not only a multi-minute wedge, it is silent, fluent, wrong
  routing with no error and no slowdown severe enough to notice.
- **User sees:** the same question answered differently, or a different
  question answered fluently, whenever something else is also asking ollama a
  question at the same moment - most plausibly what happened on 2026-09-22
  during the original bad-instance capture (another process on the machine,
  not simply "load average 10" as originally guessed).
- **Not fixed by anything tried here.** `num_thread`/`num_batch`/`seed`
  pinning is a load-time option and this is a request-concurrency problem;
  no `router.py` change was made (temporary test edit reverted, confirmed by
  empty `git diff`). The actual guard already exists at the process level -
  `AgentRunner`'s lock serializes calls *within one process*
  (`docs`/`AGENTS.md`, "The server answers one question at a time") - but
  nothing serializes *across* processes: two `association web` instances, or
  a CLI invocation racing a running web server, both talking to the same
  ollama, would hit this.
- **Next step:** decide whether cross-process serialization is worth adding
  (a lock file or a documented "run one instance" rule already implicit in
  `check_routing.py`'s docstring but not enforced anywhere outside that
  script), and extend the same docstring's warning to say what the failure
  looks like now that it has been measured (silent wrong routing, not only a
  wedge). Compare yardstick runs only when nothing else was asking ollama
  anything at the same time, not merely "within one server load" as the
  original entry said - a load-time comparison does not catch this.
- **Source:** ours (a single-slot ollama instance under two concurrent
  callers), not the prompt, and not ambient CPU load.
- **Re-ranked P1 -> P2 at the merge (2026-09-22):** the web path cannot
  trigger this - `web.runner.AgentRunner` holds one lock for the whole of
  `ask`, for exactly the single-slot reason - so a web user never sees it.
  It bites two CLI invocations at once and, above all, measurement runs:
  the yardstick's 29-question "bad instance" was almost certainly this.
  Rule for any live run: one caller, and confirm it with `pgrep` first.
- **GitHub:** #171

### `game_log`'s venue narrowing counts a neutral-site game as home or away; `team_record`'s does not
- **Found:** 2026-09-22, step 3 C4 (the team-games relation), while porting
  `game_log`'s team half and `head_to_head` onto `query/team_games.py`.
  Widened 2026-09-22, step 3 C4 (`player_splits`/`record_when`/`streak`'s
  team branches ported onto the same relation): all three read the same
  `common.team_games` venue clause, so they carry the identical gap. Widened
  again 2026-09-22, step 3 C4b: `team_quarter_points` now settles its games
  through `common.team_games` too (it previously read no venue narrowing at
  all), so a "Knicks home first-quarter scoring" question can now silently
  include a neutral-site game's own linescore the same way.
- **Evidence:** `game_log` narrows a team's venue with `tg.side = ?` alone
  (`templates/common.py: team_games`, carried over unchanged from the old
  `_team_game_log_filters`'s `tbs.home_away = ?`); `team_record`'s own venue
  split treats a neutral-site game as neither home nor away
  (`templates/teams.py: _games_record_games`'s `"neutral" if r[2] else r[1]`,
  and `team_leaderboard`'s `_venue_records`, `tg.side = ? AND NOT tg.neutral`).
  Measured against the 2026-09-22 warehouse: `game_log(team="New York
  Knicks", venue="home", season=2026)` lists the 2025-12-16 NBA Cup final (a
  neutral-site Las Vegas game the Knicks were the designated home side of) as
  one of their "home" games, while `team_record(team="New York Knicks",
  season=2026, venue="home")` answers "30-10 (.750) at home ... (1
  neutral-site game counts as neither home nor away)" over the same season.
  Confirmed pre-existing rather than introduced by C4's port: the golden
  comparison (`~/association-research/algebra-spike/step3`) shows this
  `game_log` case byte-identical before and after the port, and the old
  `_team_game_log_filters` read `team_box_stats.home_away`, which ESPN sets
  to a real side for a neutral-site game too - so the gap already existed
  in the pre-C4 code, just under a different name.
  Now measured on `player_splits` and `streak` too, same warehouse:
  `player_splits(team="New York Knicks", venue="home", season=2026)` shows
  "31-10" at home (41 games) against `team_record`'s cup-final-excluded
  "30-10" (40 games) for the same team-season; `streak(team="New York
  Knicks", kind="win", venue="home", season=2026, season_type=2)` reports a
  matched streak running "2025-11-14 to 2025-12-16" - 2025-12-16 is the Cup
  final's own Eastern date, inside a counted "home" winning streak.
- **User sees:** "Knicks last 10 home games" (or any team's) silently
  includes a game played at a neutral site, with nothing in the answer
  saying so - the numbers are real, but the label ("home") is not. Now also:
  a team's home/away splits (`player_splits`), a home-only threshold record
  (`record_when`) or a home winning streak (`streak`) can each include one
  neutral-site game a season, same silent label.
- **Next step:** decide the one rule (exclude a neutral-site game from
  `team_games`'s venue narrowing the way the record functions already do, or
  narrow it and say so in the answer) and apply it in `templates/common.py:
  team_games`, which `game_log`'s team half, `head_to_head`,
  `player_splits`/`record_when`/`streak`'s team branches and now
  `team_quarter_points` all read through - one change reaches all six. Not
  fixed here: this carve-out is a proven pure refactor, and picking a rule is
  a behavior change with its own golden re-score.
- **Source:** ours, not ESPN's - `team_box_stats.home_away` and
  `games.neutral_site` both correctly describe the game; the gap is which of
  the two `game_log`'s venue narrowing reads.
- **GitHub:** #173

### "Since he joined the league" becomes one season, the year he joined
- **Found:** 2026-09-21, yardstick-v2 live run (`live_31b2ec6.jsonl`)
- **Evidence:** "Show me luka's avg assists in each year since he joined the
  league" routed to `player_history` with `season=2019, limit=10` and answered
  "Luka Doncic, assists per game by regular season, 2019" - one row, where
  eight seasons were asked for. The model did the arithmetic (he joined in
  2018-19) and spent it on `season` where the question means `since`. The
  sibling wording "year over year" answers 2022-2026, the five-season default.
- **User sees:** a one-row table, titled with the year, so the narrowing is
  visible - which is why this is P2 and not P1.
- **Next step:** related to the invented-season entry (#95) but not fixed by
  it: "since he joined", "since his rookie year", "over his career" on
  `player_history` want `span=career` read from the question's own words in
  `route()`.
- **Re-measured 2026-09-21, after #95's fix landed** (`route()` on this
  commit, recorded slots from `live_31b2ec6.jsonl` pushed back through it with
  the model stubbed): `season` now drops (nothing in the text names 2019) and
  `limit=10` survives, so `player_history` reads `latest = current_season()`
  (2026) with a 10-season window - Luka has played 8, so this particular
  question now gets all of them, by coincidence rather than by fix. The
  prediction below this entry's evidence ("the five-season default") was
  wrong: `limit` was 10 in the recording, not absent, and `player_history`'s
  own default (`DEFAULT_HISTORY_SEASONS`) is 4, not 5. Left open: a player
  with a career longer than the model's `limit` (or a recording with no
  `limit` at all, which reads a real 4-season default) still gets a short
  answer with nothing saying so - #95 removed the ONE-season floor this entry
  was filed against, not the general gap.
- **Source:** ours, not ESPN's.
- **GitHub:** #174

### "His best season" is answered with a season nobody determined
- **Found:** 2026-09-21, working #95 (the invented-season entry above) -
  found in passing while checking `~/association-research/yardstick-v2/live_namerule.jsonl`
  for every row carrying a `season` the question's text does not state.
- **Evidence:** "plot jokic's fingerprint from his best season" routed to
  `fingerprint` with `season=2022` and rendered "Rendered NetPoints
  fingerprint (total) for Nikola Jokic (2022 season, percentile scale)" - a
  real season, with no code anywhere that determines which of Jokic's seasons
  was actually his best by any stat. Nothing in `route()` reads "best
  season"/"his best"/"career year" as a request to look one up; the model
  supplied 2022 on its own, the same way it supplies an invented `season` for
  any other unstated year (#95's shape exactly, just with a superlative
  standing in for a year). Re-measured on this commit, after #95's fix: the
  bare `season` now drops (nothing in the text names a year), so the same
  question renders the CURRENT season instead - also not necessarily his best,
  just a different unexamined guess.
- **User sees:** a fingerprint titled with a real season, so the narrowing is
  visible (same reasoning the "since he joined the league" entry above is P2
  and not P1) - but the season shown answers a different question than "his
  best", with nothing saying so.
- **Next step:** either read "best season"/"his best year"/"career year" as a
  request `route()` can recognize (`CODE_ASSIGNED_INTENTS` is not the right
  mechanism - this is a slot value, not an intent) and resolve deterministically
  against a stat (which stat "best" means is itself unstated and would need a
  default), or refuse the shape rather than silently substituting a season -
  in the spirit of "prefer refusing to guessing" (`AGENTS.md`).
- **Source:** ours, not ESPN's.
- **GitHub:** #175

### Season 2021's regular-season BPI snapshot is a day-one projection
- **Found:** 2026-09-15, reviewing `4ef119f`; **re-ranked P3 -> P2 on 2026-09-16** - a preseason projection presented as a season's index, with no caveat
- **Evidence:** all 30 of season 2021's rows are stamped 2020-12-22 - opening
  day of 2020-21 - with `numwins` and `numlosses` both 0. `team_outlook`
  answers "2021 regular-season snapshot (updated 2020-12-22, 30 teams) ... BPI
  -5.9 ... no games played yet, projected 16-56" for the Knicks, who finished
  41-31. The caveat at `query/templates/teams.py` cannot fire, because it tests
  `str(updated)[:4] > str(season)` and `"2020" > "2021"` is False - it was
  written for the 2017-2020 snapshots, which are stamped *after* their season.
  Pre-existing; the backfill now shows it for 30 teams rather than 9.
- **User sees:** a preseason projection presented as a season's power index,
  with only "no games played yet" hinting at it.
- **Next step:** widen the caveat to cover a snapshot dated *before* the season
  it describes, or say "preseason projection" when `numwins + numlosses = 0`.
- **Source:** DATA.md, "ESPN's power index is a paged collection, and holds all
  30 teams"
- **GitHub:** #89

### ESPN files one player under two athlete ids in the same box score
- **Found:** 2026-09-15, issues audit - found independently by two auditors.
  Re-measured, extended and partly fixed 2026-09-17.
- **Evidence:** grouping `player_box_stats` by `(event_id, team_id,
  display_name)` and counting distinct `athlete_id` finds **8 players, 69
  team-games** - Isaiah Canaan, Corey Brewer, Daryl Macon, Ken Johnson (the
  four originally found) plus four single-game 2019 cases new to this count:
  Tahjere McCall, John Jenkins, Mitchell Creek, Henry Ellenson. Full counts and
  the classification of every one of the 69 games (both sides real and
  identical / one real beside a fabricated zero / both blank) are in
  `DATA.md`.
  - This explains most of #54's 2019 disagreement: the team box's derived
    points equal the final score in every row of 2019, 2021 and 2026, while the
    player sums overshoot in 23 team-games in 2019 - **re-measured 2026-09-16:
    Phoenix 14, Philadelphia 7, Sacramento 1, Minnesota 1** (this read "almost
    all Phoenix (15) and Philadelphia (7)" until then, which is 22 of the 23
    and misses the two one-game teams).
- **Fixed and backfilled 2026-09-17.**
  `association.fetch.repairs.duplicate_athletes` merges each pair at load
  time into `player_box_stats_deduped`: the id with more career games carrying
  real minutes wins, the other id's rows for that game are dropped, and
  `player_game_log` now reads the merged table. A pair is merged only where
  every shared game is safe (one side has no minutes, or both sides agree
  exactly) - measured true for all 69 - so a future pair that disagrees for
  real is left unmerged rather than guessed at.

  Backfilled with `association data load --tables player_box_stats` and
  re-measured against the rebuilt table: **0 remaining duplicates** (the
  query at the top of this entry returns nothing), 69 rows merged away,
  1,100,341 rows against the raw table's 1,100,410. No real line was lost -
  the deduped table agrees with `player_box_stats_filled` on every shared key,
  and 70 filled keys disappear against a net 69 because Ken Johnson's ghost id
  `1008` holds one game (`221129001`) that his real id `1972` does not, so
  that row is relabelled rather than dropped: he ends with 34 rows, 32 points
  and 16 games carrying minutes, exactly what the real id already had plus
  that one blank. Corey Brewer's points rise from 7,224 under his canonical id
  to 7,479 for an unrelated reason worth knowing - the deduped table is built
  on `player_box_stats_filled`, so it carries the rebuilt lines for his 30
  games against Chicago and New Orleans in 2013-2018.
- **What this does NOT fix, and remains open:**
  - **The team-total double-count** (#54's 23 team-games) is unresolved:
    `query/team_metrics.py` and `query/conditions.py` sum `player_box_stats`
    directly, not the new deduped table. Re-scope or hand off once #54's owner
    is free to switch that source.
  - **`player_advanced_stats` and `player_season_advanced_stats`**
    (`fetch/advanced_stats.py`) also read raw `player_box_stats` and are built
    before the merge runs, so the 8 players still show two athlete_ids' worth
    of advanced stats for their affected seasons.
  - **The NetPoints per-game tables lose these players' entire careers, not
    just the affected season** - a bigger, separate consequence measured
    2026-09-17 and filed as its own entry immediately below, since fixing it
    needs a fetch-time change this load-time repair cannot reach.
- **User sees:** a team total summed from player rows double-counts that
  player (open); a per-game lookup through `player_game_log` (single-game
  highs, streaks, career-from-box-scores) now sees one identity (fixed, once
  loaded); an advanced-stats or NetPoints per-game question about one of these
  8 players still does not (open, see above and the entry below).
- **Source:** DATA.md, "ESPN files one player under two athlete ids" (`DATA.md:118`)
- **GitHub:** #87

### Duplicate-athlete-id players are invisible to NetPoints' per-game tables, for their whole career
- **Found:** 2026-09-17, while fixing #87
- **Fixed in code, not yet backfilled**, 2026-09-18. `Pipeline._name_to_athlete_id()`
  (`fetch/pipeline.py`) now resolves a display name shared by exactly two
  `athlete_id`s in `players` when the pair is provably one person: a new
  `_resolve_duplicate_athlete_pairs` reads `player_box_stats` off disk at
  fetch time and applies the exact proof `fetch/repairs/duplicate_athletes.py`
  uses to merge these ids at load time - the two ids appear in the SAME
  team's box score for the SAME game - then picks the established id (more
  career rows with real minutes, then more rows, then the lower id), the
  identical tiebreak that module's own ranking uses. A pair that never shares
  a game is left unresolved, same as before.
- **Re-measured 2026-09-18 against the live warehouse and the current Parquet
  tree** (`/home/jeff/code/association/data/parquet`, this worktree's copy of
  the code, `PYTHONPATH` confirmed via `association.__file__`): calling the
  new `_name_to_athlete_id()` directly resolves all 8 of #87's players
  (Isaiah Canaan, Corey Brewer, Daryl Macon, Ken Johnson, Tahjere McCall, John
  Jenkins, Mitchell Creek, Henry Ellenson), each to the SAME id
  `player_box_stats_deduped` already treats as canonical for that name -
  checked directly against the warehouse, all 8 agree exactly. Of the other 13
  names in `players` shared by exactly two ids (the rest of #21's 21 - Mike
  James, Chris Johnson, Tony Mitchell, Dee Brown, Marcus Williams, Wayne
  Selden, Chris Smith, Reggie Williams, Ray Spalding, Trevon Scott, Greg
  Monroe, Brandon Williams, Chris Wright), **zero** picked up a false match -
  none of them share a game, so the pair-resolution correctly leaves every one
  of them dropped. `players` holds no name shared by three or more ids today,
  so that branch (which the code also refuses to resolve, matching
  `duplicate_athletes_sql`'s own `HAVING COUNT(DISTINCT athlete_id) = 2`) is
  untested against real data, only against a fixture
  (`test_name_to_athlete_id_still_drops_a_name_shared_by_three_or_more`).
- **Performance, measured 2026-09-18:** `_resolve_duplicate_athlete_pairs`
  reads all of `player_box_stats` (42,724 files, 501 MiB, 1.1M rows) once per
  call, ~8-9s on this machine - filtering by `athlete_id` at read time (tried:
  `pyarrow.compute.field("athlete_id").isin(...)`) does not skip files, since
  each file is one game and carries no per-file statistics an `isin` predicate
  can use to skip it. `_name_to_athlete_id()` is only called from
  `fetch_net_points_fingerprint` (skipped by its own on-disk/season checkpoint
  for every season except the current one and any `--force`d one) and once
  from `fetch_net_points_daily` (opt-in, called once per run, not per date),
  so this does not touch the "seasons already on disk" 0.4s no-op case
  `AGENTS.md` describes - it adds a bounded ~9s to a run that already makes a
  NetPoints network request for the season(s) in question.
- **User sees:** was "no data" for a real, sometimes years-long career, for a
  reason that had nothing to do with NetPoints coverage; will see real
  per-game NetPoints and fingerprint data for these 8 players once backfilled.
- **Backfill command** (not run yet):
  `python scripts/backfill_netpoints_names.py` from the main checkout. It runs
  the NetPoints steps of a pull and nothing else - the same `Pipeline` fetch
  methods, the same `_write_rows`, the same `warehouse.build` - and reports
  these 8 players' row counts before and after, which is the measurement that
  shows this entry moving. A full `association data pull --force` over the
  NetPoints era works too and is what the script replaces, but it also refetches
  about 11,000 ESPN game summaries the fix does not touch, some 40 minutes at
  the default rate limit. Then `association data load` (the
  pull already reloads what it wrote, so this is only needed if the pull is
  split from the load). Re-measure with the query in this entry's evidence
  and update `player_box_stats_deduped`'s own cross-check if `duplicate_athletes.py`
  changes what it considers canonical for any of these 8 names before the
  backfill runs.
- **Source:** DATA.md, "ESPN files one player under two athlete ids" (`DATA.md:118`)
- **GitHub:** #101

### The 2001 playoffs are missing about ten games, and ESPN has them nowhere
- **Found:** 2026-09-11, template work (agent B); 2000 fixed and this rewritten 2026-09-15
- **Fixed for 2000.** The games ESPN's team schedules drop ARE on its daily
  scoreboard, which is a second, independent list of what was played.
  A postseason pull now makes a second discovery pass over it once the
  schedule's games are on disk, scanning forward from the latest date stored
  (`POSTSEASON_SCAN_DAYS`, 28 days), and
  `scripts/backfill_missing_playoffs.py` ran it over the two affected seasons.
  The ordering is load-bearing: the first version scanned during discovery,
  before anything was fetched, so it had no date to anchor on and a
  from-scratch pull of 2000 found none of the six Finals games.
  Nine games recovered, including the whole LAL-IND Final: the 2000 postseason
  went 70 -> 79 games, and **every team in it now matches ESPN's own
  `team_season_stats` exactly** (LAL 15 -> 23, IND 16 -> 23, POR 14 -> 16; zero
  discrepancies league-wide, counted from `real_games`).
- **What remains, and why it is not a P1.** 2001 recovered only its Finals Game
  5 (60 -> 61 games). Probed live through the project's own client, 23 days
  across that postseason's conference finals and Final return **no events at
  all** - so Games 1-4 of LAL-PHI and the end of MIL-PHI are not in ESPN's
  archive anywhere, and no pull will add them. Five teams are still short in
  `real_games`: PHI -7, LAL -5, MIL -5, CHA (id 3) -2, SA -1.
- **The answer now says so**, which is the difference from the original P1. The
  2001 postseason is declared `postseason_partial` on both `games` and
  `team_box_stats`, so a 2001 playoff question carries a note naming what is
  missing instead of stating a short series as fact. A 2001 REGULAR-season
  question carries nothing: that season is complete, and `postseason_partial`
  is a separate field from `partial` precisely so one does not caveat the
  other.
- **The 2000 standings still share the old gap.** `standings` agrees with the
  short game list rather than with reality - the Lakers are 67-13 there against
  a real 67-15 - and that is a regular-season fault tracked separately under
  "Smaller game and box-score gaps, 1994-2003" (#14).
- **User sees:** a 2001 playoff answer that is short by up to seven games for
  one team, with a caveat saying so. No wrong answer is stated as fact.
- **Next step:** nothing actionable here - it is ESPN's gap and it is declared.
  Re-check if ESPN ever backfills its own archive.
- **Source:** DATA.md, "The 2000 and 2001 playoffs stop before the Finals"
- **Re-checked 2026-09-17:** holds. Per-team game counts in `real_games`
  against ESPN's own `team_season_stats` totals (points / avgPoints): PHI
  16/23, LAL 11/16, MIL 13/18, CHA (id 3, the Charlotte Hornets in 2001) 8/10,
  SA 12/13 - ten games in all: LAL-PHI Games 1-4, three of MIL-PHI, two of
  MIL-CHA and one of LAL-SA. The caveat in `coverage.py` now names all four
  series and says 16. 2000 is clean: 75 games in `real_games`, matching ESPN.
  The "70 -> 79" figure above is the raw `games` count, which includes 4
  placeholder rows.
- **GitHub:** #6

### Nearly every Bulls and Pelicans box score from 2013 to 2018 is zeros
- **Found:** 2026-09-11, template work; characterized in the issues audit
- **Evidence:** a team-game is empty when every player row has NULL minutes and
  every stat is 0, and its `team_box_stats` row is all NULL.
  - **Scale:** 978 regular-season events (322-332 team-games a season,
    13.1-13.5%) and 47 postseason events. The postseason ones are not in
    `AGENTS.md`. There are none in 2011, 2012, 2019 or 2020.
  - **It is two teams, not scattered games.** Chicago is empty for 82 of 82
    games every season (81 in 2016), and New Orleans for 82 of 82. Every other
    team's 4-7 empty games are its games against them, and only 7 events
    involve neither: 400278127, 400278386, 400278387, 400278388, 400489088,
    400900132 and 400975285. The postseason empties are every CHI series in 2013,
    2014, 2015 and 2017, and NO's in 2015 and 2018.
  - **Two games escaped it:** 400828584 (2016, LAL v CHI) and one 2017 playoff
    game have real box scores.
  - **The source.** The raw Parquet has the same zeros, written 2026-09-08. The
    parser only writes 0 when ESPN sends "0", so ESPN apparently served zeroed
    lines. That is inferred, not checked against the live source.
  - **What survived.** Plays and shots exist for 977 of the 978 events
    (400828893 has neither), at normal density. `player_season_stats` and
    `team_season_stats` are unaffected: Anthony Davis has 1,656 points in 2015
    there and 0 in his box scores.
  - **The caveat undersells it.** `_empty_box_scores` caveats count the games;
    they do not say "every Bulls and Pelicans game".
- **User sees (re-measured live 2026-09-14; the original claim here was too
  broad, and three of the six paths it named were already sound):**
  - **`single_game_high` answered a zero as a real maximum** - "Anthony Davis's
    highest point total in a single game in the 2015 regular season was 0, on
    2014-10-28 vs ORL". Fluent, dated and false. **Fixed 2026-09-14**: the
    template now reads only lines with minutes.
  - **`game_log` names the wrong cause.** It already leaves these lines out, so
    it says "No 2015 regular season games found for Anthony Davis" - of a man
    who played 68. Split out as its own P2 entry.
  - **`threshold_count` is low but honest**: "Anthony Davis had no games with
    20+ points in the 2015 regular season. 68 of Anthony Davis's games in
    2014-15 have an empty box score in this warehouse, so the count may be
    low." The real answer is about 59. The caveat fires and is accurate; only
    "may be low" undersells "every one of them".
  - **Streaks, splits and with/without WERE affected, and this entry said they
    were not.** The claim read "`conditions` guards every read with
    `_played()`, which already requires `minutes IS NOT NULL`" - true as
    written, and exactly backwards as a conclusion: a rebuilt line has no
    minutes, so that guard is what *excluded* every rebuilt game. Audited
    2026-09-15, it made `player_splits` answer "Anthony Davis was listed in 82
    box scores in the 2015 regular season but did not play in any of them",
    and made `with_without` file every game a teammate played as one he
    missed. **Fixed 2026-09-15**; splits read 68 games and "Davis without Eric
    Gordon" returns the correct 20. The lesson is worth more than the bug: the
    guard was read for what it required, not for what it therefore excluded
    once the data underneath it changed shape.
  - Box-score points remain 86.5-87.3% of season totals across 2013-2018, so
    anything summing the box scores is still short.
- **A refetch does not fix it.** A full pull of 1988-2026 with current code on
  2026-09-11, into a separate warehouse, reproduced `player_box_stats` and
  `team_box_stats` exactly: 1,100,170 and 86,988 rows, zero differences, **as
  measured that day.** ESPN still serves the zeroed lines today.
- **No other ESPN source has the data** (probed live 2026-09-14; see DATA.md
  for the detail). The CDN box score on a different host serves the same zeros,
  the core API exposes no per-athlete per-game statistics at any path, and the
  athlete gamelog omits the games outright. The gamelog also proves the gap
  follows the *franchise*: Derrick Rose reads 0, 0, 0, 1, 61, 25 across
  2013-2018 and Aaron Brooks 51, 65, 0, 1, 60, 26, each zero exactly in his
  Chicago years. So rebuilding from `plays` is the only route to a per-game
  number, and there is nothing to re-fetch.
- **Done 2026-09-14 - the rebuild exists.** `player_box_stats_reconstructed`
  (`fetch/repairs/reconstructed_box.py`) is a load-time view over the 1,024 of these
  1,025 events that have plays, with fidelity documented per column on the
  module. It is deliberately separate: its own view over the empty games only,
  snake_case columns, no template reads it, and it is absent from
  `KNOWN_TABLES` so the SQL agent can neither query nor describe it. A player
  appearing in no play is absent rather than zero.
- **Done 2026-09-14 - the warehouse now uses it.** `player_box_stats_filled`
  (same module) is `player_box_stats` with those figures substituted into the
  empty lines and a `reconstructed` flag on exactly those rows. Measured
  2026-09-14: 21,169 of 1,100,170 rows substituted, row count conserved, and
  Anthony Davis's 2015 reads 68 games / 1,656 points against ESPN's own 68 /
  1,656, with his 14 did-not-play rows correctly left alone. It never touches a
  real line, never invents `minutes`, and drops the stored `plusMinus` on a
  substituted row - that column is a uniform 0 placeholder across all 21,169,
  not data. **Re-measured 2026-09-16, against the current warehouse (after
  `72b599c`'s 2000 playoff discovery pass): still 21,169 rows substituted, now
  out of 1,100,410** - the playoff recovery added real, non-empty rows, so the
  substituted count is unchanged and only the denominator moved.
  `team_box_stats` is 87,008 rows today, not 86,988. Through
  `player_box_stats_filled`, the rebuilt box's season total for the two
  franchises' 2013-2018 team-seasons runs roughly 96-100% of
  `player_season_stats` (99%+ outside 2016, which the module's own docstring
  already flags as the weak season) - so "still about 87%" below is true only
  of a reader on the raw `player_box_stats` table, or on
  `player_season_advanced_stats`, which is built from it (the board now says
  so - DATA.md, "Every Chicago and New Orleans game from 2013 to 2018 has an
  empty box score"), or of
  agent-written SQL that reads the raw table directly.
- **Done 2026-09-14 - the per-game templates read it.** `player_game_log` is
  built from `player_box_stats_filled`, and `single_game_high` and `game_log`
  read rebuilt lines for the stats a rebuild gets right (`REBUILT_STATS`:
  points, rebounds, assists, steals, blocks, field goals made, free throws
  made). Davis's 2015 high went from a false `0`, to a refusal, to **43 on
  2014-11-22 vs UTAH**, with the answer saying the figure is rebuilt; his 2015
  game log lists 68 games where it reported none. Turnovers (0.080 mean error)
  and fouls (0.181) are refused, and that refusal names the decision rather
  than implying missing data.
- **Done 2026-09-15 - `threshold_count` counts them too.** "How many 20-point
  games did Davis have in 2015" went from "no games ... the count may be low"
  to **52**, with the answer saying all 52 were rebuilt. The league-wide board
  moved with it: 2015's 30-point games read Harden 35 (was 34), Westbrook 29
  (was 25), and Anthony Davis now appears at 17, where the old answer listed
  none of the Chicago or New Orleans games and disclaimed "162 games ... may be
  low". Counting is additive by construction - an empty line carries 0, so it
  can never clear a threshold of 1 or more - and that was measured rather than
  argued: over 2013-2018, across all seven readable stats, **no athlete's count
  fell by a single game** and the totals rose (20+ point games, 15,978 to
  18,488). Fouls and turnovers are not counted from a rebuilt line, and a count
  of none then names the decision instead of implying missing data.
- **Done 2026-09-15 - the condition templates read it.** `player_splits`,
  `streak`, `record_when`, `player_matchup` and `with_without` resolve their
  table through `box_source()` and count a rebuilt game as one he played. This
  closed the contradiction above, where one season answered 68 games through
  `game_log` and "did not play in any of them" through `player_splits`.
  Minutes are averaged over the games that carry them rather than counting a
  rebuilt game as zero, and `UNGATED_ON_REBUILD` blanks the columns the
  rebuild gets wrong instead of averaging them in.
- **What remains, and why this is no longer a P1.** Nothing answers falsely now
  - though note that this line first appeared on 2026-09-14, when the
  conditions bug above was live and unfound, so read it as a claim about what
  has been checked rather than a guarantee. As of 2026-09-15 every per-game
  and per-condition template reads the rebuilt line or says why it will not.
  What
  is left is a SHORTFALL, which is P2 by this file's own definitions - season
  aggregates stay on the stored table on purpose, because a rebuilt season
  total is exact only about half the time and its error grows with games played
  (right totals average 31.6 games, wrong ones 55.6). So box-derived sums over
  2013-2018 are still about 87% of ESPN's own season totals, no refetch changes
  that, and the entry stays open to record it.
- **Source:** DATA.md, "Every Chicago and New Orleans game from 2013 to 2018 has an empty box score"
- **GitHub:** #1

### The SQL agent and the web health line still read raw `games`
- **Found:** 2026-09-14, building the shared `real_games` list (issue #7)
- **Evidence:** `real_games` (`fetch/repairs/real_games.py`) now holds the 43,353 rows
  of `games`'s 43,504 that are actually games (both counts moved +10 with
  `72b599c`'s 2000 playoff recovery; the gap is still 151), and every TEAM
  template reads it. `_PLAYER_GAMES` (`query/templates/common.py`) still joins raw
  `games` rather than `real_games` - harmlessly today, since no player row
  falls on one of the 151 dropped events (see "Not affected, measured" below).
  Two readers do not read `real_games` at all, both by design rather than
  oversight:
  - **The SQL agent.** `KNOWN_TABLES` and `TABLE_SUMMARY` (`query/prompt.py`)
    name `games` and not `real_games`, so any question that falls through to
    the agent gets SQL over the unfiltered table - the 134 placeholders, the 23
    team-slots naming an id no franchise has, the 11 phantoms and the one
    remaining duplicate. This is exactly the population the templates were
    just fixed for, reached by the slower path. Adding a line to
    `TABLE_SUMMARY` is not free: `PREAMBLE_TOKEN_BUDGET` is 6,400 and
    AGENTS.md forbids buying room by trimming that text.
  - **The web health line. Fixed 2026-09-18.** `_warehouse_seasons`
    (`web/app.py`) counted `games`, so the page said 43,504 where 43,353 were
    played. It reads `real_games` now, through `_game_span`, which asks the
    catalog for the view and falls back to `games` for a warehouse loaded
    before that view existed.
- **User sees:** an agent-written answer that counts rows that are not games,
  with nothing to mark it as different from the template answer to the same
  question. The web page's count is fixed.
- **Not affected, measured:** `player_box_stats`, `plays` and `shot_chart` hold
  0 rows against the 151 dropped events, so the player paths (`_PLAYER_GAMES`,
  `fingerprint.py`) never counted one. The 302 `team_box_stats` rows that do
  exist for them are entirely NULL, so no sum over that table was inflated
  either - they only ever mattered because a join could find them.
- **Next step, and it needs a decision rather than a patch:** whether the
  agent should be pointed at `real_games`. Renaming the table it sees costs no
  tokens, but it changes what `describe_table` and hand-written SQL mean, and
  `games` would then be reachable only by a name the preamble does not
  mention - and `KNOWN_TABLES`/`TABLE_SUMMARY` are model-facing text, which is
  not edited without measuring what it does to every other question. The
  health line, which needed no such decision, is done.
- **Source:** DATA.md, "`games` carries placeholder, duplicate and phantom rows"
- **GitHub:** #73

### A named playoff round is refused - the games carry no round label, and the Finals are derivable
- **Found:** 2026-09-11, repo audit; **re-measured 2026-09-18** over the
  2,285-question large StatMuse set (`~/association-research/statmuse-2026-09-large/`):
  **124 of 2,285 (5.4%) mention the Finals in some form**, and a read of a
  sample says nearly all need the round `games` does not carry (Finals-only
  lookups, Finals game logs, "who won the Finals", Finals MVP). The set's own
  capability classifier flags 3 of the 124, because "needs a round" is not in
  its checklist - so this entry's share of real traffic is well above what its
  worked example suggested.
- **Evidence:** `check_scope` raises on `round`, and `agent.py` then hands the
  question to the SQL agent, although `games` has no series or round column.
  This is the "nothing does better here" case where `check_coverage` returns a
  refusal instead.
- **User sees:** "tatum stats in the 2024 finals" takes 30-120 seconds, and the
  agent is free to answer for the whole postseason.
- **Next step:** return a refusal naming the missing round data, the way
  `_conference_refusal` does. Deriving rounds from series order is a separate
  P3 job.
- **Re-measured 2026-09-21: the largest single fall-through cause in a live
  sample, and the Finals are derivable.** 10 of 200 seeded-random reasonable
  large-set questions fell through on `round` (all ten say "finals": "michael
  jordan career finals stats", "how many 40 point finals games does kobe
  have"); 124 of the 1,972 reasonable questions (6.3%) name a round. The
  Finals need no round column: the series holding each calendar year's last
  postseason game in `real_games` is 4-7 games for 36 of 38 years (2001 shows 1
  game - DATA.md's missing 2001 playoffs - and 1994 shows 14, the phantom-1993
  duplicates, so key on `season` too). Prefer answering the Finals and refusing
  the other rounds to refusing all of them. Evidence:
  `~/association-research/statmuse-2026-09-large/live_sample200_2026-09-21/`.
- **GitHub:** #10
- **Re-measured 2026-09-24:** the fall-through half is fixed - `round` is
  now refused fast, naming the missing label and the repair (the two teams
  and the season), by `association.query.refusals` after the template and
  the compiler both decline; "nba finals game log 2025" no longer reaches
  the agent. What remains is the derivation above: rounds from series order,
  the Finals first.

### The NBA Cup final is counted as a regular-season game in most answers
- **Found:** 2026-09-11, transcript review; verified in the issues audit
- **Evidence:**
  - **How it is stored:** `season_type` 2, neutral site, `venue_city = 'Las
    Vegas'`, with no flag. The finals are 401607495 (LAL-IND), 401734908
    (OKC-MIL) and 401809839 (NY-SA).
  - **Leaving it out makes the numbers match.** Without it, W-L from `games`
    matches `standings` for all 30 teams in 2024-2026; with it, exactly the two
    finalists are a game off. Games-derived season points exceed
    `team_season_stats` by exactly the final's points (232, 178, 237). Box
    scores exceed the season table by exactly each finalist's points in it:
    Anthony Davis 2024 has 77 games and 1,917 points in box scores, against 76
    and 1,876.
  - **Who handles it:** `team_record` excludes it (`NOT cup_final`, derived as
    "the last Las Vegas game"). `conditions._Scope.where` (team streaks,
    `with_without`, splits), `head_to_head` and every box-derived player
    aggregate do not.
- **User sees:** records, streaks and splits for the finalists and their
  players, and those players' counts and totals, one game off the official
  numbers.
- **Next step:** compute a `cup_final` flag once at load, and apply it in
  `conditions`, `head_to_head` and the box-derived regular-season aggregates.
  **Corrected 2026-09-16: the table named above is wrong.** The 69
  `IST Championship` rows are in `net_points_player` - per-season (26 in 2024,
  25 in 2025, 18 in 2026), keyed by the string `net_points_season_type`, with
  **no `event_id`** - not in any per-game table. The per-game tables file all
  three finals as an ordinary `season_type = 2` row, indistinguishable from a
  regular-season game by that column. A load-time flag has to come from the
  venue instead, or from intersecting the IST player set with the Las Vegas
  games - there is no per-game NetPoints label to read directly. A `cup_final`
  flag already exists, but only inside `TEAM_GAMES_SQL`
  (`team_metrics.py:282-292`): it takes `arg_max(event_id, date)` per season
  over neutral-site Las Vegas games, and "last" is load-bearing rather than
  incidental - 2025 and 2026 each hold **three** Las Vegas games, not one.
- **Source:** DATA.md, "The NBA Cup final is stored as a regular-season game"
- **GitHub:** #11

### `fg_pct` and `efg_pct` qualify on different floors over the same denominator
- **Found:** 2026-09-11, while qualifying true shooting and eFG% on attempts (`f66e1f1`)
- **Evidence:** `fg_pct` needs 400 field-goal attempts, `efg_pct` 480, and both
  divide by FGA. Only eFG% was measured: against StatMuse's published eFG% top
  15s (300 made field goals per 82 games), a 400-FGA floor put 4 unlisted
  players into 2025's top 15 and 6 into 2026's, while 480 put in 1 and 3.
  Nobody has checked `fg_pct`'s 400 against a published FG% list. NBA.com's
  FG% rule is 300 made field goals.
- **User sees:** an FG% leaderboard that may include players a published list
  excludes, and a player who qualifies for one percentage and not the other.
- **Next step:** check `fg_pct` against a published list. Then either share one
  number, or say in each comment why they differ.
- **GitHub:** #12

### `three_pt_pct` and `ft_pct` are the same shape as #13 and are not scaled
- **Found:** 2026-09-18, while fixing #13 (shooting qualifiers flat across
  shortened seasons)
- **Evidence:** `three_pt_pct` (200 attempts) and `ft_pct` (125 attempts) are
  built by the same `_percentage()` helper as `fg_pct`, calibrated the same
  way - "5, 2.5 and 1.5 attempts a game... over 82... games" - and so carry
  the identical 82-game-flat flaw #13 measured for `ts_pct`/`efg_pct`/`fg_pct`.
  Not measured here: #13's evidence and next step named only those three
  floors (`query/metrics.py:220,230,398` at the time), so only those three
  were fixed (`LeaderboardMetric.scales_with_schedule`,
  `leaderboard.default_min_sample`) - extending it to two more floors nobody
  had measured would have been a guess, not a fix.
- **User sees:** a 3-point or free-throw percentage leaderboard for a
  shortened season (2020, 2021, the 2012 lockout season, and any earlier
  strike/lockout season) applies a stricter-than-published qualifier, the
  same way #13's three floors did before the fix.
- **Next step:** measure `three_pt_pct` and `ft_pct`'s qualifying counts for
  2020/2021/2012 against full seasons the way #13 was measured, then set
  `scales_with_schedule=True` on both in `_percentage()`'s callers
  (`query/metrics.py`) - the scaling mechanism (`leaderboard.py`,
  `_team_games_for_season`/`_scale_min_sample`) already handles any metric
  that flag is set on.
- **GitHub:** #104

### Smaller game and box-score gaps, 1994-2003
- **Found:** 2026-09-11, template work (agents A, D) and the issues audit;
  **the refetch question settled per event 2026-09-17**
- **Evidence:**
  - **2000 regular season:** `games` holds 1,166 of 1,189 real games. 18 teams
    have 80 of their 82, 10 have 81, and LAC has all 82.
  - **Real regular-season games with no box score, counted against
    `real_games`:** 5 in 1994, 5 in 1996, 6 in 1997, 4 in 1998, 4 in 2000 and 0
    in 2003 - 24 games total, re-measured 2026-09-17 and unchanged. Each
    season's gaps are one visiting team's road games - DAL 1994, VAN 1996,
    VAN/BOS 1997, DEN 1998, LAC 2000 - and **23 of the 24 are at UTAH, CLE or
    WSH**; the exception is `160405003`.
  - **Real postseason games with no box score:** the entire 1997 ECF CHI-MIA
    (`170520014`, `170522014`, `170524004`, `170526004`, `170528014`),
    `150614019` (1995 Finals Game 4, ORL at HOU), `160502025` (1996 SAC-SEA) and
    `230503026` (1998 HOU at UTAH).
  - **A refetch does not fix any of them.** All 32 were probed live through the
    project's own client on 2026-09-17: every one returns a summary carrying a
    `boxscore` object with **zero athlete lines**, while six control games in
    the same seasons return 24 each through the identical code path. The
    athlete gamelog omits them too, and `plays` holds nothing for any of the 32
    - all are before 2002, where `plays` starts - so there is nothing to
    rebuild either. Their `team_box_stats` rows all exist and all carry NULL
    stats.
  - **NULL minutes in 2006-2012** mean the player did not appear. Dropping those
    rows raised 2009's games-played agreement from 30 to 378 of 445 players.
- **User sees:** the postseason half now carries a caveat naming the missing
  games (`coverage.postseason_partial`, 1995-1998), so a playoff count or
  single-game high says what it could not see. **The regular-season half still
  has none:** 24 games spread over five seasons, at most 5 in one season out of
  ~1,190, so a per-game average is off in the third decimal and a season total
  is short by up to two games for one team.
- **Next step:** decide whether 24 games across five seasons is worth a
  regular-season caveat, given the postseason one is now in place. A season
  total for an affected team (DAL 1994, VAN 1996, VAN/BOS 1997, DEN 1998, LAC
  2000) is the case that would benefit; a league-wide average is not. Also
  check that every box-derived template treats NULL minutes as "did not play".
- **Source:** DATA.md, "Real postseason games with no box score" and "The 2000
  regular season is short, and the 2000 standings share the gap"
- **GitHub:** #14

### The 2026 shot chart holds more shots than the box score
- **Found:** 2026-09-11, shot-frame fix (shot agent)
- **Evidence:** 1,165 player-games, across the regular season and postseason,
  have more shots in `shot_chart` than in the box score, 1,207 extra in all.
  Curry has 488 threes against 484 3PA. Neither `plays` nor `shot_chart` holds
  a duplicate `play_id`. The extras look like end-of-period heaves: 1,141 of
  those player-games have extra 3PA. **The "1,084 shots under a second" figure
  did not reproduce on re-check (2026-09-16) and was measured wrong**: `clock`
  is stored as `MM:SS` for most of a period and as bare seconds-with-tenths
  (e.g. `"57.3"`) inside the last minute, and reading only the second format
  as a number silently dropped every shot still in `MM:SS`. Parsing both forms
  and filtering total seconds remaining `< 1.0` gives **1,131** shots in the
  1,141 player-games with extra 3PA, and **1,135** across all 1,165. That is
  still a correlation, not proof.
- **User sees:** shot charts and shot-distance answers count shots that are not
  in the box score.
- **Next step:** check whether box scores leave out buzzer heaves (a shot after
  the horn, or one ESPN logs but does not credit). If they do, filter the chart
  the same way.
- **Source:** DATA.md, "The 2026 shot chart holds more shots than the box score"
- **GitHub:** #15

### `with_without` refuses a season-long absence, and `game_log` says he was never a teammate
- **Found:** 2026-09-11, template work (agent C)
- **Evidence:** a teammate's stint is read from box-score rows, so a whole season
  without rows breaks it. Klay Thompson's 2020-21 and Kevin Durant's 2019-20
  with the Nets are not counted as games "without" him.
- **User sees:** "Warriors record without Klay" for 2020-21 comes back with no
  "without" games, or too few.
- **Next step:** read roster tenure from `player_season_stats` team rows, not
  from box-score presence.
- **Re-checked 2026-09-15: it now refuses instead of undercounting, and the
  next step below cannot work.** `with_without` for Klay Thompson 2021 answers
  that his tenure "falls outside the 2021 regular season" - the refusal built
  in the `if not games:` branch of `with_without` (`query/templates/splits.py`).
  Durant/Nets 2020 is the same. `player_season_stats` has **no row** for a
  season a player missed entirely, so it cannot supply tenure. Worse,
  `game_log` and `player_stat` say "Klay Thompson was not Stephen Curry's
  teammate in any of his 63 games" - the wrong-cause sentence built in
  `_no_narrowed_games` (`query/templates/common.py`) - about a rostered, injured
  player.
- **GitHub:** #16

### `player_history` answers "last N seasons on record", not a calendar window
- **Found:** 2026-09-11, while fixing name clarification
- **Evidence:** the query reads `season <= ? ORDER BY season DESC LIMIT ?` per
  player (`player_history` in `query/templates/players.py`), so a player with gaps, or
  one who retired, gets their last N seasons played. "Curry's scoring over the
  last 4 seasons" would give Dell Curry 1999-2002. The header now names the
  range the rows reach ("by regular season, 2023-2026"), so the seasons are
  labeled truthfully. Because the template reads that way, name narrowing
  keeps every Curry for the question, narrowed through the anchor season with
  Seth and Stephen named first. It cannot drop players with nothing in the
  calendar window.
- **User sees:** "last 5 seasons" answered with seasons from years ago, labeled
  as such but not flagged as outside the window the question asked about.
- **Next step:** decide whether "last N seasons" means the calendar window. If
  it does, read that window and narrow names by it too; `narrow_to_available`
  would need a lower bound it does not take today.
- **GitHub:** #19

### Two fingerprints asked for without "vs" or "compare" draw one
- **Found:** documented in `AGENTS.md` as an accepted cost; listed by the repo audit
- **Evidence:** `restore_dropped_players` acts only on a comparison word, and
  `compared_but_unmatched` only on "vs".
- **User sees:** "plot jokic and embiid fingerprints" draws one polygon, with no
  note that a second name was dropped.
- **Next step:** when two players are named and only one is drawn, say so,
  without restoring the second.
- **GitHub:** #20

### The NetPoints season fingerprint matches players mid-pull, so a name can be lost
- **Found:** 2026-09-11, comparing a fresh full pull against the existing warehouse
- **Evidence:** `fetch_net_points_fingerprint(season)` runs inside the season
  loop in `Pipeline.pull`, and resolves NetPoints' `displayName` through
  `_name_to_athlete_id()`, which drops a name shared by more than one player
  already on disk. So the map depends on how many players the pull has
  discovered so far. Comparing the existing warehouse with a 1988-2026 pull on
  2026-09-11, `net_points_player_fingerprint` differs by 12 rows, and every
  athlete involved shares a display name with exactly one other player:
  - **10 rows only in the old warehouse**, whose pull fetched 2020-2026 first:
    Corey Brewer (2020), Henry Ellenson (2020, 2021), Greg Monroe (2022), Mike
    James (2021), Brandon Williams (2022, 2024, 2025), Ray Spalding (2021),
    Wayne Selden (2022).
  - **2 rows only in the fresh warehouse**, whose pull went 1988 upward and so
    knew the older Wayne Selden and Daryl Macon before their namesakes existed:
    both in 2019.
  
  21 display names in `players` are shared by 42 players, so this can hit any
  of them. The per-game NetPoints tables are unaffected: `fetch_net_points_daily`
  runs after the loop, when every player is on disk, and those tables matched
  exactly.
- **User sees:** a fingerprint that is missing for a player who has one, with no
  reason given, and a warehouse whose contents depend on the order seasons were
  pulled.
- **Next step:** build the name map once, after the season loop, from the
  complete `players` table, and re-resolve the fingerprint files then. Keep the
  source `displayName` on the row either way, so an unmatched name can be
  recovered.
- **Source:** DATA.md, "NetPoints publishes a display name, not a player id"
- **GitHub:** #21

### A NetPoints name that is a different NAME, not a different spelling, matches nothing
- **Found:** 2026-09-18, what remains of #112 once the spelling differences
  were bridged. **Every spelling difference between the two sources is now
  handled** (`parse.match_key`: diacritics, hyphens, whitespace, generational
  suffixes); these are not spellings.
- **Evidence:** measured against the re-fetched warehouse,
  `net_points_player_game` holds **683** rows with no `athlete_id`, down from
  2,190 before any of this work, and they are two piles that want opposite
  treatment:
  - **350 rows where NetPoints uses a different name.** `Carlton Carrington`
    against ESPN's `Bub Carrington` (82 - a nickname), `Alexandre Sarr` against
    `Alex Sarr` (67) and `Nathan Mensah` against `Nate Mensah` (25 - a formal
    against a short first name), `Cam Reynolds` against `Cameron Reynolds` (24
    - the same thing in reverse), `Omari Spellman` against ESPN's
    `Omari Rasulala Spellman` (95 - a middle name), `Cui Yongxi` against
    `Yongxi Cui` (5 - reversed order), and `NA Nene` against ESPN's `"Nene "`
    (49 - a mononym, with a trailing space on ESPN's side).
  - **333 rows whose name belongs to two different people** - Brandon Williams
    (140), Wayne Selden (78), Greg Monroe (69) and the rest of the 13 real
    shared-name pairs. **These are correctly refused and want no fix.** Nothing
    on a NetPoints row says which of the two it is, and this project's worst
    failures are all a rule that guessed.
- **User sees:** a per-game NetPoints or fingerprint question about one of
  those ~9 players returns nothing, with no caveat.
- **Next step:** a curated alias list, not a rule - which is the conclusion
  `query/entities.py` already reached with `PLAYER_NICKNAMES` after measuring
  and rejecting a prominence tiebreak. Each entry is a judgment somebody makes
  once and can be checked (`Bub Carrington` IS Carlton Carrington), where a
  general first-name rule would match `Chris Johnson` to a different
  `Christopher Johnson`. Nine names cover all 350 rows, so the list is small.
  ESPN's trailing space in `"Nene "` is worth stripping when the map is built
  whatever else happens.
- **Source:** DATA.md, "NetPoints publishes a display name, not a player id"
- **GitHub:** #113

### A player's 2026 NetPoints games do not sum to his season line, and the two possession columns are different units
- **Found:** 2026-09-19, while explaining Paul Reed's 2026 NetPoints rank by hand
- **Evidence:** read-only against `/home/jeff/code/association/nba.duckdb` at
  `f5c917f`. Population: the 375 players with 500+ minutes in
  `net_points_player` (season 2026, `'Regular Season'`) who also have a 2026
  fingerprint row. Summing `net_points_player_game.t_net_pts` (season 2026,
  `season_type = 2`) and comparing with `net_points_player.overall`: 124 of 375
  differ by more than 1 net point, 35 by more than 5, the largest by 15.9
  (Shai Gilgeous-Alexander, 452.5 summed against 468.3; Victor Wembanyama 250.5
  against 263.9; OG Anunoby 66.9 against 53.8). 17 of the 375 also disagree on
  the games count (Wembanyama: 65 game rows, 64 in the season file). Not
  established which side is off: a game matched to the wrong event, the NBA Cup
  final counted on one side only (see the NBA Cup entry above), or the season
  file revised after the daily files were pulled. Separately,
  `net_points_player_game.t_poss` is not the season file's `total_poss`: the
  per-player ratio of summed `t_poss` to `total_poss` has median 0.38 (range
  0.27 to 0.57), so it reads as possessions the player was involved in, not
  possessions on the floor. Neither unit is written down anywhere.
- **User sees:** nothing today. A per-100 rate built from the game table with
  `t_poss` as the denominator would come out about 2.6 times the season file's
  `overall_per_100_poss` (Paul Reed: 10.0 against 4.51), and a season total
  built by summing games disagrees with the season line for a third of the
  qualified players.
- **Next step:** for the five largest gaps, diff the player's game rows against
  the daily files by date to see whether a game is missing, doubled or filed
  under another `season_type`. Then record what `t_poss`, `o_poss` and `d_poss`
  measure in `DATA.md`.
- **GitHub:** #154

### `shot_chart`'s empty refusal never names the season, even when one was asked for
- **Found:** 2026-09-18, fixing #18 (the retired-player default-season bug)
- **Evidence:** `shotchart.render_for_player`'s empty branch
  (`query/shotchart.py`, `if not shots: message = f"No shots found for
  {resolved_name} with the given filters."`) never mentions ``season`` at all -
  unlike `player_stat` ("no 1999 regular season numbers") and `game_log` ("No
  1999 regular season games found"), which both name the season in the plain
  refusal. `shot_chart(ctx, {"player": "Stephen Curry", "season": 1999})`
  against a warehouse with only current-season shots answers exactly "No shots
  found for Stephen Curry with the given filters." - true when no filters were
  given (a bare `player` and `season` are not filters this sentence counts),
  and misleading when they were, since it does not say which one emptied the
  result.
- **User sees:** a refusal that does not say which season it is refusing, and
  reads as though a filter (`shot_value`, `period`, ...) is why nothing was
  found even when the question named nothing but a player and a season. #18's
  fix appends a redirect naming the season only for a *defaulted* season with
  something to redirect to; an *explicit* season with nothing on record - or a
  defaulted one where the player has no shots on record at all - still gets
  this unscoped sentence.
- **Next step:** have `render_for_player`'s empty branch say the season and
  season_type it queried (mirroring `_period`), and separately list which
  filters (if any) were actually applied, rather than a blanket "with the
  given filters" that fires even with none. Threading that through touches
  `shotchart.py`'s shared renderer, which the agent's `render_shot_chart` tool
  also calls - check both callers before changing the message shape.
- **Source:** ours, not ESPN's.
- **GitHub:** #105

### `opponent` can hold garbage nothing else in the slots explains, and blocks an otherwise-answerable question
- **Found:** 2026-09-18, entity-resolution pass over the StatMuse replay set
- **Evidence:** two shapes, neither a name-matching problem:
  - "bane game log without anthony black and franz wagner this season" arrives
    with `opponent='Anthony Black, Franz Wagner'` - a comma-joined restatement
    of the *same two names* already correctly in `without=['anthony black',
    'franz wagner']`. `entities.scope_from_question` has no rule for an
    `opponent` that duplicates `without`; it is left in place, fails to
    resolve as a team ("no team matching 'Anthony Black, Franz Wagner'"), and
    the whole question falls through even though every piece of scoping it
    actually needs is already sitting in `without`.
  - "Clippers ats record last 15 games at home" arrives with `team='Los
    Angeles Clippers'` (correct) and `opponent='home'` beside `venue='home'` -
    the same fact written twice, once as a bogus opponent. No `vs`/`against`
    phrase exists for `_scope_from_question_opponent` to correct it with, so it
    is left alone and fails to resolve as a team ("no team matching 'home'").
    This second one is not a clean fix even if `opponent` is dropped: "ats"
    means against-the-spread, which nothing in the warehouse stores, so the
    question is unanswerable on the stat alone regardless.
- **User sees:** a fall-through to the agent for the first (which the agent
  might still get right by reading `without` itself); the second would still
  need a separate refusal for the unsupported "ats" stat even if `opponent`
  were fixed.
- **Next step:** in `entities.py`, drop an `opponent` that (a) does not
  resolve as a team via `_team_named`, and (b) either duplicates names already
  present in `without`, or is literally the venue word already in `venue`
  ("home"/"away"). Not attempted here: the payoff on the second case is
  capped by the separate "ats" gap, and the first needs a decision about
  whether dropping `opponent` outright is safe versus trying to fold it back
  into `without` (already correct) - a judgment call better made alongside
  whichever template's `HONORED_SCOPING` actually reads these two rows.
- **Source:** ours, not ESPN's.
- **GitHub:** #118

### A quarter or half is answered for a player, and for nobody else
- **Found:** 2026-09-16 auditing the feed; **the player half shipped the same
  day** as `period_split`
- **Fixed.** **Re-counted 2026-09-16 with a quarter/half regex over the whole
  feed: 25 of the 261 feed queries ask for a quarter or a half, not 21, and 24
  of the 25 fell through before the fix** - one was a `team_quarter_points`
  partial rather than a full fall-through. A named player's single period now
  has a template: `shot_chart` carries `athlete_id`, `period`, `made` and the
  shot's value, so it is a filtered sum, and the value is read through
  `SHOT_VALUE_SQL` - 99.95% against ESPN's linescores, where guessing it from
  the play's prose is 76.8%. Summed over all periods including overtime, a
  player's season total matches his box score exactly for 550 of 578
  player-seasons. **After the fix the 25 grade correct 6, partial 5, clarified
  2, unclear 1, fell through 11.**
- **What is still not answered**, and it is most of the rest of that 25:
  - **A non-points stat, or a `split`/`without`/`order` narrowing, on a
    player's period.** `period_split` refuses these (4 of the 25, e.g.
    "scottie barnes stats 2nd half log without rj").
  - **A TEAM's half.** `team_quarter_points` reads one period out of the
    linescore and has no notion of a half, so "Detroit Pistons most points in a
    first half this season" and "least points scored by the wizards in the
    first half" still fall through. This is the cheapest of the four: the
    linescore is exact and a half is two of its entries added together.
  - **A breakdown across all four quarters.** "nba playerspoints by quarter
    average", "points per quarter for Luka". `period_split` answers ONE period
    by design; this is a different shape and is deliberately left alone rather
    than answered for a period nobody named.
  - **A position group as the subject.** "each center 1q pts log vs nugget" -
    the same gap position groups have everywhere, not a period problem.
  - **A ranking within a period.** "knicks 1st quarter scoring leaders" wants a
    leaderboard restricted to a quarter.
- **User sees:** for the shapes above, a slow agent answer or a whole-game line
  where one quarter was asked for.
- **Next step:** the team half, which is two linescore entries added.
- **GitHub:** #96


### A coach question has nothing to read, and ESPN's coaches are not worth reading
- **Found:** 2026-09-16, query-set audit; **the source question settled by live
  probing 2026-09-17**, which is what this entry asked for
- **Evidence:** "nick nurse coaching record all-time nba in december on the
  road" has nothing to read: none of the warehouse's 20 base tables or 6 views
  holds a coach, and there is no column anywhere named `%coach%`.
  - **ESPN does serve coaches** - so this is not a gap in the source, which
    the original entry deliberately did not assert either way. What it serves
    is unreliable, and that is the finding. `seasons/{year}/coaches` ignores
    the season entirely (asked for 1977 it answers with Doug Christie and JJ
    Redick); the team-scoped `seasons/{year}/teams/{team}/coaches` honors it
    but returns a coach for only **12 of 30 teams in 1996** (29 of 30 in 2010,
    30 of 30 in 2024), never returns two for one team-season in the 90
    sampled - so a mid-season change is invisible - and is wrong for whole
    franchises: Detroit is empty or wrong in all nine seasons sampled from
    1994 to 2026.
- **User sees:** a fall-through on any coach question, which lands on the SQL
  agent with no coach column to find - the case `check_coverage` exists for,
  except that nothing declares it, so the agent is free to fill the silence
  from its own weights.
- **Next step:** a decision, not a fetch. Either (a) leave it unfetched and
  refuse a coach question with a sentence naming the real cause, which needs a
  router intent and so a `ROUTER_PROMPT` edit - and any such edit moves slots
  on unrelated questions, so it needs `scripts/check_routing.py` run after it;
  or (b) fetch the team-scoped endpoint and caveat it hard, which means
  publishing a coaching record that is wrong about Detroit for two decades.
  (a) is the cheaper and more honest of the two.
- **Source:** DATA.md, "ESPN publishes coaches, and the collection that looks
  league-wide is not historical"
- **GitHub:** #97

### Each narrowing the router has no slot for needs its own regex
- **Found:** 2026-09-15 replaying 261 real StatMuse feed queries through the
  fast path; **fixed for every measured case and re-ranked P1 -> P3 on
  2026-09-16**, after the second pass measured zero left.
- **What it was.** `check_scope()` refuses a narrowing a template cannot
  honor, but it can only see slots the router emits, and `ROUTER_SCHEMA` has
  no slot for a weekday, a holiday, an age, a minutes condition, "since
  returning from injury", a calendar date, a game of a playoff series or a
  season named by ordinal. Those words never reached it, so the template
  answered the *un-narrowed* question - the largest single cause of a wrong
  answer in the replay.
- **A calendar day is now ANSWERED, not refused.** The first cut refused it
  with the rest, reasoning that picking a year the question does not state is a
  guess. It is not - the season states it, since season Y runs October of Y-1
  through June of Y - and refusing threw away an answer the warehouse holds.
  `_validate_date` resolves it and `game_log` answers the game: "Desmond bane
  march 17" went from his most recent game (a month off) to **2026-03-17, 16
  PTS vs OKC**, measured as the only row that moved in that replay and the
  first question in this work to go from wrong to *correct* rather than to a
  refusal. What still refuses is what genuinely fixes no day: a window ("since
  January 31"), a career question (twenty Octobers), and February 31.
- **Fixed in `61c1bef` and the second pass**, by reading each shape from the
  question text into `situation`, which no template lists in `HONORED_SCOPING`,
  so `check_scope` refuses and the question falls through to the agent. Every
  alternative was perturbed individually and watched to fail.
- **Measured across two replays:** fluently wrong 44 (17%) -> 33 (13%) -> **29
  (11%)**, and **correct is 67 (26%) in all three runs** - 15 wrong answers
  removed without losing one right answer. **No wrong answer in the sample
  drops a condition any more**; what is left is wrong entity (11), named player
  dropped (8), wrong metric (6) and wrong scope (4), all different entries.
- **Why it is still open, at P3.** The fix is a list of regexes, one per shape
  somebody happened to ask in a 261-query sample. The structural fault is
  untouched: **`check_scope` still cannot refuse what `ROUTER_SCHEMA` never
  emits**, so the next narrowing nobody has thought of is dropped silently and
  answered fluently, exactly as these eight were. It is P3 rather than P1
  because no measured question is wrong today - but the mechanism that produced
  11 of them is still there, and the only thing standing in front of it is a
  regex somebody has to remember to extend.
- **It also costs a clarification.** "Bam adebeyo jan 19" went from `clarified`
  to `fell_through`: `check_scope` runs before name resolution, so refusing the
  date preempts "did you mean Bam Adebayo?". Right on its own terms - the date
  was being dropped too - but worth knowing the refusal is not free.
- **Re-checked 2026-09-16, and the mechanism moved.** "Bam adebeyo jan 19" now
  arrives with `date="2023-01-19"` already resolved and routed to
  `player_stat`, which does not honor `date` - so it still refuses before
  name resolution and still preempts "did you mean Bam Adebayo?", but the
  cause is no longer `check_scope` dropping the date; it is `player_stat` not
  redirecting a resolved `date` to `game_log`. That gap is now tracked
  separately under #94.
- **User sees:** nothing wrong today. The risk is the next unhandled narrowing.
- **Next step:** design the general check rather than adding a ninth regex - a
  catch-all slot, or a test that every meaningful word in the question reached
  some slot, so an unrecognized narrowing refuses by default instead of being
  ignored by default. Re-measure against `fastpath_after_rows_graded.jsonl`,
  which is the current baseline (261 rows: correct 87, wrong 29, fell_through
  94, clarified 25, refused 12, partial 12, unclear 2).
- **Source:** the wrong answers are ours, not ESPN's; no DATA.md entry.
- **GitHub:** #84

### Three question filters are recognized but no template answers them
- **Found:** 2026-09-11, template work and final corpus run
- **Evidence:** `SCOPING_SLOTS` against `HONORED_SCOPING` (`query/templates/common.py`):
  - `since`/`until`: "most 3 pointers made since 2020" - `since` is honored
    by `game_log`, `player_stat` and `player_matchup` since 2026-09-19 (the
    relation's span builders read it); `leaderboard`, `team_leaderboard` and
    `threshold_count` still refuse it, and `until` is set beside `since`
    (`router.py`) but is in neither `HONORED_SCOPING` nor `SCOPING_SLOTS`, so
    `check_scope` cannot see it. Harmless while every template honoring
    `since` reads a span with no end; it becomes a silent wrong answer the day
    one honors `since` without reading `until`. Add `until` to
    `SCOPING_SLOTS` before, or with, that.
  - `situation`: "Celtics record on back to backs", overtime, a conference, a
    weekday, a holiday, an age, "since returning" - still refused everywhere.
    Two of its shapes have left it: a minutes floor ("with 25 minutes", "20+
    mins") is the `above` slot now, and "by month" is `split=month`.
  - `round`: "tatum stats in the 2024 finals".

  `below` ("Sga games with under 14 fta") left this list on 2026-09-19:
  `game_log`, `player_stat` and `threshold_count` honor it and `above` through
  `measure_filters`, which reads the column from the phrase's own words and
  refuses a word it cannot map.
- **User sees:** every such question goes to the slow agent. Fall-through
  counts by slot over the 261-row corpus on `ebf0d9e`'s successor:
  `situation` 15, `since` 3, `round` 1 (`replay_rerouted_b4c.jsonl`, the
  refusal's own list of slots).
- **Next step:** `situation` for `team_record`: back-to-backs need the
  Eastern date (`season.eastern_date`); a weekday is the same date. Then
  `since` for `leaderboard`/`threshold_count`, which read their own season
  scope rather than a `_Span` - unify that first (see "Player-game narrowing
  is compositional; season scoping is not", below).
- **GitHub:** #23

### Two players against one team has no template
- **Found:** 2026-09-11, while making `opponent` refuse or narrow; **moved up
  within P3 on 2026-09-16** - the latest replay shows this touches more
  questions than its original position reflected
- **Evidence:** `player_compare` and `player_matchup` read season lines and
  honor no `opponent` (`HONORED_SCOPING`, `query/templates/common.py`), so "compare
  curry and lebron vs the celtics" refuses on the template path and falls
  through. Before the fix that moved the Celtics out of `team`, it compared
  the two players' whole 2026 seasons. `_narrow_player_games` already builds
  one player's box-score line against one opponent for `player_stat`.
- **Re-checked 2026-09-16: no longer a one-off construction - six feed
  questions fall through on it in the latest replay**, all on
  `player_matchup`/`player_compare cannot honor ['opponent']`: "sam hauser v
  mil", "Curry vs dallas last q0 games", "julius randle stats vs blazers with
  minnestota", "oubre vs warriors without embiid", "de'aaron fox vs magic
  ...", "stating centers vs suns". Commit `d7a8db1` does not touch this shape.
- **Fixed 2026-09-18 (partial, `player_matchup` only):** the router routes
  these as a fake two-player matchup - one real name plus a team it could not
  place anywhere else - not a genuine comparison, so `player_matchup` now
  recognizes exactly one player name plus a team `opponent` and answers it the
  way `game_log` answers "player vs team" (`HONORED_SCOPING["player_matchup"]`
  and the new branch at the top of `player_matchup`, `query/templates/games.py`).
  Confirmed against the recorded routing corpus (`replay_recorded_routes.py`):
  "sam hauser v mil", "julius randle stats vs blazers with minnestota" and
  "Curry vs dallas last q0 games" now answer (the last as a clarifying
  question - "Curry" is ambiguous, correctly).
- **Fixed 2026-09-18 (the two remaining `player_matchup` rows):** "oubre vs
  warriors without embiid" and "de'aaron fox vs magic ... without wembyanama"
  each carry a second name in `players` *and* a `without` - a garbled team
  name in the first case ("Warriners", already correctly resolved into
  `opponent` by `entities.scope_from_question`), a genuinely-resolvable but
  spurious second player in the second (Victor Wembanyama, Fox's own Spurs
  teammate, named a second time - typo and all - in `without`). Checked
  against the warehouse: Fox and Wembanyama are both on the Spurs (team 24) in
  season 2026, and Oubre and Embiid are both on the 76ers (team 20) - so in
  both rows `without` names a genuine TEAMMATE of the real subject, not an
  opposing player, and `game_log`'s existing definition ("a game only where
  none of the named teammates played") is the right one; no new semantic was
  needed. `player_matchup` now honors `without` for exactly this shape
  (`HONORED_SCOPING["player_matchup"]`), and
  `_player_matchup_drop_fabricated_second` (`query/templates/games.py`)
  eliminates the noise before falling into the one-player-and-a-team branch:
  a second name matching no player at all is dropped outright, and a second
  name is dropped for duplicating `without` only when
  `_player_matchup_confirms_teammate` CONFIRMS the two name the same player -
  reusing `_resolved_teammate`'s own near-spelling resolution rather than a
  fresh fuzzy match, and never merely because the two share a team. A genuine
  two-player matchup with a leftover `without` is refused from inside the
  template rather than silently dropped, the same way a leftover `opponent`
  already is. Confirmed against the recorded routing corpus: "oubre vs
  warriors without embiid" now answers a one-game narrowed log (verified
  against the warehouse: Oubre has exactly one game against the Warriors this
  season, 2026-02-03, and Embiid has no box-score row for it); "de'aaron fox
  vs magic ... without wembyanama" now answers "did you mean Victor
  Wembanyama?" - a typo genuinely one edit from ambiguous otherwise, and the
  same honest suggestion `game_log`'s own `without` already gives for a typo,
  rather than guessed at. No other row in the 261-question corpus moved.
- **Still open:** `player_compare` is untouched, so "compare curry and lebron
  vs the celtics" and "stating centers vs suns" (also a position-group
  question, a separate gap) still fall through.
- **Next step:** let `player_compare` honor `opponent` by building each
  player's line through `_narrow_player_games`.
- **GitHub:** #34

### A team is the real subject of a question routed to a player-only template
- **Found:** 2026-09-18, entity-resolution pass over the StatMuse replay set
  (`fastpath_after_rows_graded.jsonl`)
- **Evidence:** two of the 261 questions name a team where a player belongs,
  and no amount of slot repair fixes them, because the template itself has no
  team-shaped answer: "cavaliers 3 pointers every game" routes to
  `shot_distance` with `player='Cavaliers'` - a per-player shot-distance
  template, with nothing that reports a team's makes-per-game; "rebounds
  allowed per team" routes to `team_stat` with `team='any_team'` - the
  question wants every team ranked, which is `team_leaderboard`'s shape, not
  `team_stat`'s single-team one. Moving the text into a `team` slot changes
  nothing: `entities.py` can name the entity correctly, but `check_scope`/the
  handler still has no column or shape to answer from. ("oklahoma city thunder
  all-time triple doubles vs west" looked like a third instance but is not -
  "vs west" is Western Conference scoping, which is #25's gap, not this one.)
- **User sees:** a fall-through to the slow agent for both.
- **Next step:** not an `entities.py` fix. Either teach the router to route a
  bare team subject with no player words to a team-shaped intent
  (`team_stat`/`team_leaderboard`), or add the missing shapes (a team's
  per-game shot-distance breakdown; `team_leaderboard` ranking every team by a
  counting stat with no `stat` narrowed to one metric already listed).
- **GitHub:** #121

### Conference and division are in the standings we fetch, and the parser drops them
- **Found:** 2026-09-11, template work (agent B); **cause corrected 2026-09-15**
  by the issues audit
- **The source does have it.** The standings response the pull already fetches
  is grouped: `standings?season=2026` returns children named
  `Eastern Conference` (15 teams) and `Western Conference` (15); 2004 returns
  15 and 14, 1990 returns 13 and 14; and `&level=3` returns the six divisions
  at 5 teams each. `standings` also carries "vs. Conf." and "vs. Div." records,
  populated from 2004.
- **Evidence:** `parse_standings` (`fetch/parse.py:342`) walks `children`
  purely to reach the entries and throws the group name away - its own test
  says so (`tests/fetch/test_parse.py:413-414`). `games.conference_game` is
  False on all 43,504 rows. No table maps a team to a conference, so
  `_conference_refusal` refuses a conference named as the subject ("who leads
  the east"), while "Western Conference standings" matches `router._SITUATION`
  first and is handed to an agent with no conference data either.
- **User sees:** a refusal for "who leads the East", and an ungrounded agent
  answer for "Western Conference standings".
- **Next step:** record the conference (and division at `level=3`) from the
  standings children, then re-pull `standings`. **Not** the static per-season
  table this entry used to propose.
- **Source:** DATA.md, "No conference, division or birth-date data anywhere"
  (`DATA.md:376`, corrected 2026-09-15)
- **Fixed 2026-09-24, for the OPPONENT-narrowing half only (K3-2).** A new
  `team_alignment` table (`fetch.parse.parse_team_alignment`, a second
  request to the standings endpoint at `&level=3` - see DATA.md, "The
  standings endpoint holds conference and division, at two different request
  shapes") carries season, team_id, conference and division back to 1988,
  same floor as `standings`. `query.calendar.parse_alignment` reads a
  `situation` value naming one ("vs the west", "against eastern conference
  teams", "vs southeast division", "in the west"), and both relations'
  shared narrowing steps (`Narrowed.narrow_alignment`/
  `TeamNarrowed.narrow_alignment`, applied by `_apply_situation` in
  `templates/common.py`) narrow to games against an opponent aligned that way
  IN THAT GAME'S OWN SEASON - so every template that already honored
  `situation` (`game_log`, `player_stat`, `threshold_count`, `player_splits`,
  `record_when`, `streak`, `team_record`, `team_leaderboard`,
  `team_quarter_points`, `team_stat`, and the compiler, which reads the same
  shared step) reads it at once, and `query.refusals._non_calendar_situation`
  stops refusing it. Verified against a scratch warehouse built from a fresh
  pull of `team_alignment` for every season 1988-2026 plus the main
  warehouse's other tables (copied, read-only, never written): yardstick-v2
  F055 "alperen sengun double-doubles vs southeast division career away"
  gives exactly 8 double-doubles in 22 career road games vs Southeast
  Division opponents, all regular season, matching the key; F152 "oklahoma
  city thunder all-time triple doubles vs west" gives exactly 101 of 177
  Thunder player triple-doubles since 2009 against Western Conference
  opponents, matching the key (90 regular season + 11 postseason of 166 + 11) -
  though the team-aggregate-of-a-player-boolean-stat half of that answer is
  the compiler's to assemble, not this relation clause's; what is verified
  here is that the relation delivers the right game set for it to aggregate
  over.
- **What remains (the SUBJECT-is-a-conference half - "who leads the East",
  "Western Conference standings").** Unchanged by this fix: `_conference_refusal`
  (`templates/teams.py`) still refuses a `team`/`opponent` slot that names a
  conference or division, and "Western Conference standings" still falls
  through to an agent with no conference data grounded for it either, because
  neither shape reads `situation` - the router files a conference/division
  named as the SUBJECT into a team slot, not a narrowing. Answering it would
  need either a `team_leaderboard`-shaped read grouped by `team_alignment`
  (e.g. every West team's record, ranked) or a `team_record`-style read of
  each team's OWN "vs. Conf."/"vs. Div." standings column - a different shape
  from the opponent-narrowing this entry now answers, and outside this task's
  scope (`_conference_refusal` and `router._SITUATION` are not this agent's
  files). Re-titled narrower rather than closed, since half the original
  finding is still open.
- **GitHub:** #25

### A player's career TS% is refused
- **Found:** 2026-09-11, final corpus run
- **Evidence:** "kevin durant true shooting percentage career" routes to
  `player_stat` with `stat='ts_pct'`, which refuses. It is derivable from
  career totals: PTS / (2 × (FGA + 0.44 FTA)).
- **User sees:** a fall-through to the agent. The wrong 3P% answer this used to
  give is fixed.
- **Next step:** add TS% and eFG% to `player_stat` as computed ratios, like
  `SHOOTING_STATS`.
- **Re-checked 2026-09-15: wider than filed.** `player_stat` refuses
  `ts_pct`/`efg_pct` for a single season too, not only a career, although
  `player_season_advanced_stats` holds both per season.
- **GitHub:** #26

### A games minimum cannot be given to the agent's TS%/eFG% leaderboard tool
- **Found:** 2026-09-11, while qualifying true shooting and eFG% (`f66e1f1`)
- **Evidence:** `get_leaderboard(metric="ts_pct", min_sample=50)` now means 50
  attempts, not 50 games. The result carries `min_sample_column`, but a
  games-based minimum can no longer be expressed through the tool for these two
  metrics.
- **Re-checked 2026-09-16: the "does not list" half is stale.** `TABLE_SUMMARY`
  (`query/prompt.py:55,59,60`) has listed `ts_pct`/`efg_pct`/`usage_pct` since
  `f971cfe`, so the agent does not need `describe_table` to find the columns
  themselves. What is still missing is the tool parameter: `get_leaderboard`
  (`query/prompt.py:509`, schema at `:626`; dispatched in
  `query/toolbox.py:229-255`) takes `min_sample` only, with no `min_games`.
- **User sees:** "best true shooting among players with 50 games" makes the agent
  write SQL, more slowly.
- **Next step:** accept a `min_games` alongside `min_sample` in the tool, if the
  budget allows.
- **GitHub:** #28

### Fingerprint for a specific date
- **Found:** before 2026-09-11 (docstring)
- **Evidence:** the fingerprint template in `query/templates/netpoints.py` says "... but not
  yet for a particular date". `game_log` already honors `date`.
- **User sees:** a helpful refusal.
- **Next step:** resolve the date to the player's game with `_eastern_day`, then
  draw the single-game fingerprint.
- **GitHub:** #29

### Franchise career leaderboards
- **Found:** before 2026-09-11 (docstring)
- **Evidence:** the leaderboard in `query/templates/players.py` says "Refused until that
  is decided". The rule for relocated franchises is open.
- **User sees:** a refusal for "timberwolves career leaders in total points".
- **Next step:** decide the relocation rule, then map it.
- **Re-checked 2026-09-15:** the "User sees" is wrong. `_career_leaderboard`
  raises `TemplateUnsupported`, which is a fall-through to the agent, not a
  refusal the user reads.
- **Re-checked 2026-09-16: the relocation rule is no longer open.** The refusal
  is still at `query/templates/players.py` (`_career_leaderboard`, raising
  `TemplateUnsupported("franchise career leaderboards are not supported")`),
  but "the rule for relocated franchises is open" is now decided elsewhere:
  `association/nba/franchises.py` established that an ESPN `team_id` belongs to
  the franchise, not the name, so a per-`team_id` sum already follows a
  relocation correctly. What remains is mechanical, not a decision: lift the
  refusal, title the resulting list by the franchise's era (using
  `franchises.season_name`, the way every other answer already names a team
  for its season), and handle the one franchise with two ids in its history -
  the Charlotte Hornets, id 3 (1989-2002, then New Orleans) and id 30
  (Charlotte again, 2015 on) - as two lists rather than one merged one.
- **GitHub:** #30

### Data no template reads
- **Found:** 2026-09-11, field audit
- **Evidence:**
  - **Tables never read:** `plays`, `win_probability`, `net_points_team`,
    `net_points_team_game` and `stat_glossary`.
  - **Tables partly read:** `team_season_stats` (30 of 115 columns),
    `team_power_index` (17 of 75), `team_box_stats` (11 of 34) and `standings`
    (14 of 25).
  - **Columns unused by name resolution:** `teams.location`, `name` and
    `nickname`.
- **User sees:** questions about clutch play, comebacks, win probability, team
  NetPoints and paint or fast-break points go to the agent.
- **Next step:** re-run the field audit after each template round, and take the
  most-asked shapes first.
- **Re-checked 2026-09-15: partly out of date.** Points in the paint and
  fast-break points are now read from `team_season_stats` by `team_metrics`, and
  `plays` is read indirectly through the rebuilt box view. Still unread by any
  template: `win_probability`, `net_points_team`, `net_points_team_game`,
  `stat_glossary`.
- **Re-checked 2026-09-16: the "columns unused by name resolution" bullet is
  wrong.** `entities.py` now queries `teams.name` (`ILIKE` against a nickname,
  `:682`) and `teams.location` (`ILIKE` against a city, `:688`). Only
  `teams.nickname` is unused anywhere.
- **GitHub:** #31

### Shapes deferred for lack of data or logic
- **Found:** 2026-09-11, query-shape research
- **Evidence:**
  - **Clutch play and game-winners** need clock parsing of `plays`.
  - **Comebacks** need the running score from `plays`, or `win_probability`.
  - **Playoff-series situations** need series order from `games`.
  - **Anything by age** has no data behind it: no table holds a birth date. That
    is inherent until a bio source is added.
- **User sees:** a fall-through to the agent.
- **Next step:** take them in that order.
- **Source:** DATA.md, "No conference, division or birth-date data anywhere"
  (`DATA.md:376`, corrected 2026-09-15) - the birth-date half only; conference
  and division are now #25's finding, not this one's.
- **GitHub:** #32

### A router-invented name one edit from a real one falls through instead of asking
- **Found:** 2026-09-11, probing the season-narrowing branch
- **Evidence:** "how many rebounds does davis average" routed to `player_stat`
  with `player='Davies (Davic)'`. `override_invented_players` counts it as
  grounded, since "davies" is one edit from "davis". Nothing matches it, and
  `suggest_players` offers nobody, so `_resolved_player` raises
  `TemplateUnsupported`. The likely reason the suggestion pass finds nobody is
  the parenthesized second token, since every token must be near some word of
  the name. That was not confirmed. Seen once.
- **User sees:** the question goes to the agent, rather than asking which Davis
  was meant.
- **Next step:** reproduce it. If it recurs, drop punctuated tokens before
  `suggest_players`, or back off to the word the question holds.
- **Re-checked 2026-09-15:** reproduces, but the suspected cause is only half
  right. Dropping the parenthesized token would not help: `suggest_players`
  returns [] for "Davies" alone too, because a single token skips the surname
  pass and the near-spelling pass finds 23+ "Davis" players against
  `MAX_CLARIFY_CANDIDATES=5`. Backing off to the question's own word does work.
- **Re-checked 2026-09-16: "23+" was not a measurement, and the count depends
  on what is being counted.** Against the current `players` table: **20**
  `display_name`s carry "Davis" as a whole word (a space-delimited token, so
  "Davis" the name and "Davis Bertans" count, "Hayes-Davis" does not), **21**
  have "davis" as a substring of the SURNAME specifically (adds "JD Davison",
  drops "Davis Bertans" since its surname is "Bertans"), and **41** are within
  one edit (Levenshtein distance) of "davis" on some name token. Any of the
  three clears `MAX_CLARIFY_CANDIDATES=5` many times over, so the conclusion is
  unaffected - but "23+" should be replaced with one of these, named.
- **GitHub:** #33

### The surname backoff in `suggest_players` can confidently name a different real player
- **Found:** 2026-09-18, entity-resolution pass over the StatMuse replay set
- **Fixed 2026-09-18, for the reported case** (span-contract work,
  `entities._question_derived_player`): "Grady dick last 10 games" now
  answers Gradey Dick's real game log directly. The fix is not a patch to
  `suggest_players` - it never reaches it for this shape anymore.
  `override_invented_players` now resolves "Grady Dickinson" from the
  question's own words *before* `resolve_player`/`suggest_players` ever see
  it: "Grady" anchors the question's own (typo'd) given name, the window
  extends to the adjacent "dick", and "grady dick" resolves to Gradey Dick
  by the same near-spelling-plus-exact discipline `suggest_players` already
  used - just run against the question's literal words instead of the
  router's fabricated surname. Measured against the 261-question replay:
  this row moves from "did you mean Hunter Dickinson?" (wrong, confident) to
  the correct answer, and a dedicated regression test
  (`test_a_fabricated_surname_extension_is_discarded_not_the_router_s_wrong_guess`)
  pins it.
- **What remains open, narrower than before:** `suggest_players`' own
  surname-only pass (pass 1) is untouched and still ignores the given name
  once a surname matches exactly and uniquely - the same shape could still
  reach it and misname someone the way Hunter Dickinson did, but only when
  *no* window around any anchor resolves first. That needs both the given
  name AND the surname to fail every exact-or-near check
  `_question_derived_player` tries (unlikely for an ordinary "First Last"
  question, since the two are adjacent and the window search tries the pair
  together) - not reproduced since the fix, and not chased further here for
  lack of a live example. The original "Jemel Embiid" tension this entry's
  own evidence describes (tightening the surname-only pass would break the
  case it exists for) is unchanged and still true of `suggest_players`
  itself.
- **Evidence (original report):** "Grady dick last 10 games" (the real player
  is "Gradey Dick") routed to `game_log` with `player='Grady Dickinson'` -
  the given name correct-ish, the surname fabricated. `suggest_players`'
  surname-only pass (`entities.py`, pass 1) drops back to the last token,
  finds exactly one player whose surname is "Dickinson" - Hunter Dickinson,
  a real but wholly unrelated player - and returns him without ever checking
  the given name.
- **Source:** ours (a matching heuristic), not ESPN's.
- **GitHub:** #122

### A pre-1994 legend gets "no player matching", not the coverage-floor refusal
- **Found:** 2026-09-18, entity-resolution pass over the StatMuse replay set
- **Evidence:** "kareem stats vs bob lanier" routes to `player_matchup` with
  `players=['Kareem Abdul-Jabbar', 'Bob Lanier']`; both retired before the
  warehouse's 1993-94 floor and neither is in `players` at all (`SELECT ...
  FROM players WHERE display_name ILIKE '%abdul-jabbar%'` and `'%lanier%'`
  each return zero rows, checked 2026-09-18 against
  `/home/jeff/code/association/nba.duckdb`) - this is DATA.md's documented
  fact ("Coverage floors": "Kareem Abdul-Jabbar, Larry Bird and Julius Erving
  are not in `players` at all"), not a name-matching bug: no amount of
  suggestion or nickname repair can find a row that does not exist.
  `suggest_players` correctly returns nothing for both names. The refusal that
  reaches the user is the generic `no_match` sentence, "no player matching
  'Kareem Abdul-Jabbar'", which reads exactly like a typo problem - the same
  false-cause shape as the Maxey example in `AGENTS.md`, one step earlier: the
  question is not asking about a player our matching failed to find, it is
  asking about a player who played before the warehouse's discovery mechanism
  (box scores from 1994) could ever have found him.
- **User sees:** a fall-through that names the wrong cause; a person reading
  "no player matching" goes to check their spelling, not learn that pre-1994
  legends are out of reach entirely.
- **Next step:** not fixable in `entities.py` - there is no near-spelling
  distance from "no such row" to "the era is too early". Best done where
  `check_coverage`/`no_match` meet a `PLAYER_INTENTS` template: when
  `find_players`/`suggest_players` both come back empty for a name that is
  otherwise well-formed (no digits, no obvious typo signal), consider whether
  a coverage-floor sentence ("ESPN's box scores start in 1994; ... may have
  played earlier than that") is more honest than "no player matching".
  Speculative until measured against how many other empty-`players`-match
  cases are actually pre-1994 legends versus genuine typos.
- **Source:** DATA.md, "Coverage floors" (`player_season_stats` section).
- **GitHub:** #123

### The web page keeps no history, so closing the tab loses every answer
- **Found:** 2026-09-14, requested
- **Evidence:** each turn is built straight into the DOM
  (`ask()` in `web/static/index.html`) and nothing else holds it: there is no
  `localStorage`, no `sessionStorage` and no server-side store. **Corrected
  2026-09-16:** `AgentRunner` (`web/runner.py:71-72`) keeps only `self.agent`
  and `self._lock` - there is no "current question" attribute to lose; a reload,
  a crash or a server restart loses the thread precisely because nothing is
  kept at all. The artifacts a question produced do survive, as files in the
  output directory, but nothing records which question drew them, so an
  orphaned chart cannot be traced back to what was asked.
- **User sees:** no way to reread yesterday's answer, compare two runs of the
  same question, or send somebody a link to one.
- **Next step:** persist each turn - question, the `answer` payload, the trace
  lines and the artifact names. Decide first where it lives: `localStorage` is
  a one-file change and stays per-browser, a server-side store is shareable and
  puts history beside the artifacts it references. Then serve it
  (`GET /api/history`) and rebuild the thread from it at load. The renderers
  already work from the `answer` payload alone, so a stored turn replays
  without re-asking.
- **Priority note:** ranked here as a gap rather than P4 because it is the
  whole session a user loses, not a detail of one answer.
- **GitHub:** #69

### `player_netpoints` still renders as a `<pre>` block, and no future template gets a renderer for free
- **Found:** 2026-09-18, scoping study requested ("richer HTML answers - cards,
  sparklines, tables"). Checked first: no open or closed issue on this repo
  covers it; the nearest neighbors are #69 (above) and the chart renderers
  (`court.py`, `radar.py`), which already draw `shot_chart` and `fingerprint`
  as standalone HTML artifacts.
- **Status: stage 1 of the plan below shipped 2026-09-18** (a separate agent
  session, `web/static/index.html` + `tests/web/test_renderers.py` only, no
  template touched). `RENDERERS` now covers 20 of the 24 `TEMPLATES` entries -
  the original seven (`leaderboard`, `threshold_count`, `single_game_high`,
  `player_history`, `player_compare`, `game_log`, `team_record`), the two
  chart intents (`shot_chart`, `fingerprint`), and thirteen more added in this
  pass: `player_stat`, `shot_distance` (a stat card each), `head_to_head` (a
  two-team score card), `team_quarter_points`/`period_split` (sparkline plus a
  per-game table), `player_splits` (one table per split kind), `with_without`/
  `record_when` (a two-row team-record comparison), `player_matchup`
  (comparison table plus the recent-meetings log), `streak` (a ranked table
  for the league-wide shape, a plain one for a named player or team),
  `team_stat` (value and rank per metric), `team_leaderboard` (a ranked
  table) and `team_outlook` (a stat-card grid). Every new renderer read a key
  already on `Answer.data` - no template changed, matching what the inventory
  below found. Checked in a real browser, light and dark: `.stats`, `.h2h` and
  `.group-title` (the three new CSS shapes) render correctly against both
  palettes, since they use only the existing color tokens. What remains is
  stages 2-4 below, which do reach into a template or reshape the fallback
  path - out of scope for a renderer-only pass and left here rather than
  folded into a "done" entry.
- **What already existed before stage 1, for the record.**
  `web/static/index.html`'s `RENDERERS` table already turned `Answer.data`
  into a table, a ranked list, a two-column comparison, a season-by-season
  sparkline, or a W-L record card, for the original seven intents. Two more,
  `shot_chart` and `fingerprint`, get a chart drawn server-side and shown in a
  sandboxed iframe (`chartFrame()`). Every other intent fell through
  `renderBody()` ("no renderer, or a key it needs is missing" both read
  `null`) to `el("pre", null, a.text)`: literally the raw sentence in an
  unstyled `<pre>`, which is the "raw unstyled terminal text" the request
  asked to fix. `tests/web/test_renderers.py` is the contract test - it reads
  `RENDERERS`' `needs` lists back out of the page and asserts each template
  still produces every key its renderer reads, so a new renderer plugs into
  an existing, exercised guard rather than inventing one.
- **Architectural facts, established from code, that constrain the design:**
  - `TemplateResult.data` (`query/templates/common.py:438`) already is "the
    same result as structured values - resolved names and numbers, no ids and
    no schema" (its own docstring, `query/answer.py:96-117`), and has been
    carried out on `Answer.data` since 2.0. Confirmed against the live
    warehouse (`nba.duckdb`, read-only) by calling all 24 `TEMPLATES` entries
    directly with representative slots: every one returns a populated,
    JSON-serializable `dict` with resolved names (e.g. `'Nikola Jokic'`,
    `'Denver Nuggets'`) and no `athlete_id`/`team_id`/`event_id` anywhere in
    the 24 payloads inspected, matching what the docstring claims. A renderer
    can consume `data` with no query-path change for any of the 24 - see the
    inventory below for which ones are worth it.
  - `answered_by == "agent"` answers carry `intent=None` and `data=None`
    (`AnswerResponse`, `web/app.py:78-95`; `Answer`, `query/answer.py:104-116`),
    so a renderer needs a text-only fallback by construction, and `renderBody`
    already supplies it (`index.html:680-681`, `if (!spec || !a.data) return
    null`). **Reported, not re-verified**: `ISSUES.md`'s own baseline
    (`fastpath_after_rows_graded.jsonl`, cited in "Each narrowing the router
    has no slot for needs its own regex" above, 261 rows) counts 94
    `fell_through` - about 36% of that sample - against 87 `correct`, 29
    `wrong`, 25 `clarified`, 12 `refused`, 12 `partial`, 2 `unclear`. That
    file is not checked into this tree, so the exact `fell_through` ->
    `answered_by=="agent"` mapping could not be re-run here; treat 36% as an
    order-of-magnitude estimate of how often the page has no `data` to render
    at all, not a re-measured figure.
  - The page is one self-contained HTML file (`web/static/index.html`, 32KB),
    inline CSS and JS, declared in `[tool.setuptools.package-data]`
    (`pyproject.toml:90-95`) and checked to exist at `serve()` startup. A
    renderer is a function added to the existing `RENDERERS` object plus, at
    most, a few lines of shared CSS (`.record`, `.spark` already exist for the
    card and sparkline shapes) - no build step, no second asset, no new
    dependency.
  - Chart iframes run `sandbox="allow-same-origin"` with scripts off
    (`chartFrame()`, `index.html:612`); `allow-same-origin` is load-bearing so
    `fit()` (`index.html:637-652`) can read `frame.contentDocument` to size the
    frame to its content. This constrains only the two chart intents, which
    already work this way - it says nothing about the plain-data intents,
    whose tables/cards render inline in the page's own DOM, not in an iframe.
  - `answer.text` is what the CLI prints, unchanged, and the CLI has no
    `--json` flag (`cli/commands.py:317`, `click.echo(agent.ask(...).text)` is
    the only place `.text` is read outside the web layer - grepped, nothing
    else in `cli/` or `web/runner.py` touches `.answer`/`.text` directly). The
    web API's `/api/ask` and `/api/ask/stream` responses carry `text` as one
    field of `AnswerResponse` alongside `data`, so a JSON consumer of the API
    already gets both and loses nothing either way. A richer page is additive:
    `text` stays the sentence of record for the CLI and for any API caller
    that ignores `data`.
- **Per-intent inventory**, as it stood before stage 1 (called directly
  against `nba.duckdb`, read-only, with representative slots for all 24
  `TEMPLATES` entries - full coverage, none skipped for time). "Renderer
  today" is now stale for the thirteen stage 1 shipped - see "Status" above -
  and kept here only so the "needs template change" column, which is still
  accurate, has its evidence beside it.

  | intent | `data` carries | UI it could support | needs template change |
  |---|---|---|---|
  | `player_stat` | one stat's `gamesPlayed`/avg/total | single stat card | no - **shipped** |
  | `leaderboard` | ranked `leaders` list, `fields` | ranked table | - (already had one) |
  | `threshold_count` | ranked `leaders` (player, games) | ranked table | - (already had one) |
  | `team_record` | wins/losses/pct/home/road/last_ten | W-L card | - (already had one) |
  | `game_log` | list of games, per-game box line | table | - (already had one) |
  | `shot_chart` | `path` to a drawn SVG chart | chart artifact | - (already had one) |
  | `player_compare` | per-player stat dict + NetPoints | comparison table | - (already had one) |
  | `single_game_high` | ranked `games` (player, value, date, opp) | ranked table | - (already had one) |
  | `head_to_head` | `games` count, `wins` per team | small 2-team card | no - **shipped** |
  | `team_quarter_points` | per-game list (date, opponent, points), `total` | sparkline over games | no - **shipped** |
  | `period_split` | per-game list (date, opponent, points), average | sparkline over games | no - **shipped** |
  | `shot_distance` | one number (`avg_feet`) + `attempts` | single stat card | no - **shipped** |
  | `player_history` | season-by-season one-stat series | table + sparkline | - (already had one) |
  | `player_netpoints` | per-category offense/defense/total list (`fingerprint`) | bar chart - same category set the `fingerprint` chart already draws | **yes - still open, stage 2 below** |
  | `fingerprint` | `path` to a drawn radar chart | chart artifact | - (already had one) |
  | `player_splits` | grouped table: home/away, starter/bench, win/loss, by month | multi-group table | no - **shipped** |
  | `with_without` | two-row group comparison (played/out), `tenure` | two-column comparison | no - **shipped** |
  | `record_when` | two-row group comparison (`reached`/`fell_short`) | two-column comparison | no - **shipped** |
  | `player_matchup` | `averages` (2-player comparison dict) + per-meeting `games` list when they met | comparison table + game log | no - **shipped** |
  | `streak` | list of `streaks` (length, from, to) | small ranked list / timeline | no - **shipped** |
  | `team_stat` | dict of metric -> {value, rank, of} | ranked stat table (card grid) | no - **shipped** |
  | `team_leaderboard` | ranked `teams` list | ranked table | no - **shipped** |
  | `team_outlook` | many single values: bpi, record, projections, `chances` dict | multi-card grid | no - **shipped** |
  | `coach` | `message`, `unanswerable` | refusal - prose only, correctly | **n/a - nothing structured to show, by design** |

  The one genuine gap is `player_netpoints`: its `fingerprint` list is the
  identical category/offense/defense/total shape the `fingerprint` template
  already hands to `radar.py`, so the cheapest richer UI for it is not a new
  renderer at all but drawing the same chart artifact from `player_netpoints`'
  own data - it touches the template (adding an `Artifact`), which is why it
  was left out of a renderer-only pass rather than folded in.
- **User sees:** nothing wrong - the plain-text answers are correct - but
  `player_netpoints` still gets a `<pre>` block on a page that renders every
  other intent but the tableless `coach` refusal as a table, card or chart.
  It is also the one remaining intent whose answer is the same shape
  (offense/defense/total per category) as a chart the page already draws
  (`fingerprint`), so the gap reads as more visible than one plain-text
  answer among twenty-three - a reader who has seen a fingerprint radar
  once would reasonably expect the same category list here to draw one too.
- **Next step - the plan's remaining stages, cheapest first, each
  independently shippable:**
  1. ~~**Renderer-only, no template change.**~~ **Shipped 2026-09-18** - see
     "Status" above.
  2. **`player_netpoints` as a chart, not a table.** Draw the same radar chart
     `fingerprint` does, from `player_netpoints`' own `fingerprint` list,
     rather than writing a fourteenth bar-shaped renderer. **Touches the
     template** (`player_netpoints` gains an `Artifact`, `query/templates/netpoints.py`),
     not only `index.html`. **Risk:** `radar.py`'s existing per-game-vs-per-100
     `Unit` labeling (`AGENTS.md`, "A single game's fingerprint...") has to
     stay correct for a season-level input, since `player_netpoints` is a
     season aggregate and `fingerprint` can be either - reusing the renderer
     wrong would silently mislabel the scale, exactly the failure shape this
     project keeps producing.
  3. **A generic fallback formatter for everything with no dedicated
     renderer**, rather than a 24th hand-written one: for any `data` dict with
     no `RENDERERS` entry, render flat scalar keys as a small card grid and
     any list-of-dicts key as a table, generically, in `renderBody`'s `null`
     branch. This is the stage that makes the *baseline* ("every template's
     answer render as formatted HTML rather than raw unstyled terminal text")
     true for every future template too, not just the 24 named above, without
     writing a renderer per intent forever. **Risk:** a generic renderer is
     more likely to produce a confusing layout for a shape nobody has looked
     at (the recurring failure shape here is a too-narrow answer, and a
     too-generic one is the same risk turned around) - ship it behind the
     same `try/catch` fallback to `<pre>` so a bad generic render never loses
     the correct sentence, and look at a handful of real answers per intent
     before trusting it for `coach`-shaped refusals and other prose-only
     results, which should probably opt out entirely.
  4. **`coach`-and-friends stay prose.** Not every `data` payload should grow
     a UI - `coach`'s `data` is a refusal message, and rendering that as a
     "card" would dress up a sentence as if it were a number. Exclude
     refusal-shaped payloads (a `message`/`unanswerable` pair, or any answer
     with `answered_by != "fast"`) from stage 3's generic renderer explicitly,
     rather than letting it try and fall back silently - a silent fallback
     here reads as "nothing to show" when the real content is the sentence
     itself.
- **Priority note:** P3 (placed beside #69, the page's other structural gap),
  not P1/P2 - nothing here is wrong. Every one of the 24 answers is correct
  and already readable as plain text; `player_netpoints` getting a `<pre>`
  block where its sibling `fingerprint` gets a chart is a real gap in what a
  user sees rather than a bug, so it is ranked as a gap and not inflated to a
  wrong-answer priority it does not meet.
- **Could not verify (stage 1):** whether a generic stage-3 renderer looks
  good for every shape in practice, which needs eyes on real output rather
  than a data-shape read, is unchanged by shipping stage 1 - still open. The
  thirteen stage 1 renderers were themselves checked against a real Chromium
  render, light and dark, with synthetic data matching every shape the
  per-intent inventory above describes (including the league-wide vs.
  single-subject `streak` shapes, and `with_without`'s subject-vs-no-subject
  column set) - not just asserted via the DOM-level contract test.
- **Could not verify (stage 2, unstarted):** the exact `fell_through` ->
  `answered_by=="agent"` mapping in the 261-row baseline (file not in this
  tree, and re-running the router was out of scope - ollama was not run for
  this task); and whether `player_netpoints`'s aggregate-season fingerprint
  plots correctly on `radar.py`'s per-game percentile scale without changes
  beyond wiring - that needs the scale checked against real numbers, not
  assumed from the shared category names.
- **GitHub:** #110

### The connection indicator is written once at load and never updated
- **Found:** 2026-09-14, requested
- **Evidence:** `web/static/index.html` calls `/api/health` exactly once, on
  load, and writes a status line from it (the example status here was
  "3,043 games, 1994-2026" when this was written; re-checked 2026-09-16, the
  same call would today read "43,504 games, 1988-2026" - see #71's re-check
  for that number - so read the figure as illustrative, not current), plus
  "ollama unreachable" when `ollama_ready` is false; "server unreachable" if
  the fetch itself fails). Nothing polls afterwards. If ollama stops, the
  server restarts, or the warehouse is replaced mid-session, the page goes on
  showing what was true when it was opened. The data needed is already on the
  response: `HealthResponse` carries `ollama_ready` and `busy`, and both are
  live properties on the runner. The `queued` SSE event, which says a question
  is waiting behind another, is rendered only as a line of trace text.
- **User sees:** a page that looks connected when it is not. The first sign of
  trouble is asking a question and waiting for a failure.
- **Next step:** poll `/api/health` on an interval and on window focus, and
  render a real indicator with the states the server already distinguishes -
  connected, busy, queued, ollama down, server unreachable. One caution:
  `_warehouse_seasons` opens its own DuckDB connection per call, so cache the
  season figures and poll only the liveness fields, or the indicator pays for
  a query every few seconds.
- **GitHub:** #70

### ESPN publishes PER, RPM, VORP and WARP per player-season, and we store none of it
- **Found:** 2026-09-14, fixing the NULL-totals issue (#5)
- **Evidence:** the core per-season endpoint now read by
  `endpoints.player_season_totals_url`
  (`CORE_V2/seasons/{season}/types/{season_type}/athletes/{id}/statistics`)
  carries **112 stat names** against the 51 columns `player_season_stats`
  holds. Among the 61 not stored: `PER`, `RPM`, `ORPM`, `DRPM`, `VORP`,
  `WARP`, `NBARating`, `plusMinus`, `usageRate`, `trueShootingPct`,
  `effectiveFGPct`, `estimatedPossessions`, `pointsInPaint`, `offReboundRate`,
  `defReboundRate`, `assistRatio`, `turnoverRatio`, `brickIndex` and the whole
  `avg48*` family. Confirmed live for Seth Curry 2024 (`PER` 13.4). The parser
  deliberately drops them (`parse.SEASON_TOTAL_STAT_NAMES`) rather than widen
  the table as a side effect of a bug fix.
- **User sees:** a refusal or a fall-through for any question naming one —
  "who led the league in PER", "what is Jokic's VORP". `player_season_advanced_stats`
  computes its own `ts_pct`/`efg_pct`/`usage_pct` from box scores, so those
  three have an answer already; the rest have none. ESPN's own `plusMinus` and
  `usageRate` would also be a cross-check on the computed ones.
- **The cost is not the request.** These values ride on the response the repair
  already fetches — but only for the ~110 broken lines. Storing them for every
  player-season is one request per (athlete, season, season_type), which the
  career endpoint currently covers in one request per (athlete, season_type):
  roughly 30x the requests for the whole warehouse. A separate decision, and a
  separate table is probably the right shape.
- **Next step:** decide whether the advanced columns justify a per-season fetch
  at all. If they do, a new `player_season_advanced_espn` table keyed
  (athlete_id, season, season_type) — not extra columns on `player_season_stats`,
  whose rows are per-team and which this endpoint cannot split.
- **Source:** DATA.md, "The career endpoint drops its totals category,
  unpredictably and in part"
- **GitHub:** #77

### A question naming two seasons routes with only one, and the answer never says so
- **Found:** 2026-09-18, adding `team_record`'s month split
- **Evidence:** "knicks record by month 2024 2025" (a question naming both the
  2023-24 and 2024-25 seasons, most plausibly asking for both broken out by
  month) routes with `slots = {"team": "New York Knicks", "season": 2024,
  "split": "month", ...}` - the second year is dropped entirely, with nothing
  in the slots recording that the question named it. `team_record` now
  answers the by-month table for 2024 alone, correctly and completely for that
  one season - but the answer has no way to know a second season was asked
  for, since the router never carried it past routing. Measured against
  `replay_recorded_routes.py` and the built warehouse: the 2024 table it
  returns is numerically exact (November 9-5 through April 6-2, cross-checked
  against a direct SQL tally), so this is not a wrong answer - it is a
  narrower one, stated as though it were the whole question.
- **User sees:** a correct, complete answer for one of the two seasons named,
  with no caveat that the other was dropped - the same shape #19
  (`player_history`) and #20 (a fingerprint comparison losing its second name)
  already describe for a name or a season silently narrowed.
- **Next step:** this is a router-level gap (`ROUTER_SCHEMA`'s `season` slot
  takes one integer, not a list or a range), not a template one - no template
  file can restore a second season the router never emitted. Fixing it needs
  either a `season` slot that can carry a span, or reading a second year out
  of the question text the way `CODE_ASSIGNED_INTENTS` does for other slots,
  and either one needs `scripts/check_routing.py` run after, per
  `router_prompt.py`'s own rules.
- **GitHub:** #124

### Two more router typo'd names resolve to a safe clarification rather than a direct answer, and a stricter fix was measured and reverted
- **Found:** 2026-09-18, building the span-contract fix for entity resolution
  (`entities._question_derived_player`, ISSUES.md #122's fix)
- **Evidence:** "kon knepuell stats last 10 games" (router: `player='Kon
  Knepuvel'`) and "gui last 5 games vs sours" (router, after its own given-name
  fabrication is repaired: `player='Gui Santos'`) both still ask "'Kon'/'Gui'
  matches more than one player - did you mean ... ?" rather than answering
  directly, even after the span-contract fix. Both fragments ("Kon", "Gui")
  are in fact EXACT, globally unique whole-word matches
  (`entities._exact_name_span`) - the same shape `players_named_in` already
  uses elsewhere to answer confidently - so a version of this fix that also
  trusted a bare matched fragment's own exact uniqueness inside
  `undo_name_completion` answered both directly (`Kon Knueppel`, `Gui
  Santos`).
- **Why it was reverted rather than shipped:** measured on the same 261-row
  replay, that version also answered "kareem stats vs bob lanier" with
  **Kareem Rush** - a real but wholly unrelated player - because "Kareem"
  alone is *equally* an exact, globally unique whole-word match, and Kareem
  Abdul-Jabbar (like Bob Lanier) retired before the warehouse's 1993-94
  floor and has no row to be found under at all (DATA.md, "Coverage
  floors"). Nothing in the fragment-uniqueness check can tell "the surname
  is garbled beyond this repair" (Kon, Gui - real players, just spelled
  worse than this fix's edit budgets reach) apart from "the intended person
  simply is not in `players`" (Kareem, Bob Lanier) - both are a single
  given name, exactly and uniquely matching someone real but unrelated. The
  position-based guard that protects `_question_derived_player` itself
  (trust a lone anchor only from the surname position) does not help here
  either: "Kon" and "Kareem" are both given names, the same position, with
  opposite right answers. A P1 wrong-entity answer for a real, if narrow,
  question shape (a pre-1994 legend named alongside a typo'd modern player)
  was judged worse than two rows staying a safe, if imperfect, clarification
  that already names the right player among its options - so the
  fragment-uniqueness branch was removed before this shipped, and
  `test_two_anchored_words_that_fail_together_do_not_fall_back_to_one` /
  `test_a_given_name_anchor_alone_is_not_trusted_but_its_window_is` pin the
  guard that would otherwise regress if this is attempted again.
- **User sees:** an unnecessary "did you mean Kon Knueppel, John Konchar, or
  Yanic Konan Niederhauser?" (or the Gui-Santos equivalent) where a direct
  answer is possible - safe, not misleading, but a clarification the
  question did not need to ask.
- **Next step:** needs a second, independent signal the way #122's own
  "Next step" already called for before this fix existed - something that
  tells "Kon"/"Gui" apart from "Kareem"/"Bob Lanier" other than exact
  uniqueness, e.g. checking whether the OTHER word of the router's name has
  literally any surname-shaped near neighbor in the question at all (Kon
  Knepuvel's "Knepuvel" is one edit outside this fix's own budget of
  "knepuell", the question's own spelling, and could be caught by widening
  that budget slightly; Kareem Abdul-Jabbar's "Abdul"/"Jabbar" have no
  question word anywhere near them). Not attempted here - it needs measuring
  against the corpus the same way the guard that replaced it was, and this
  session's budget did not extend to a second round of that measurement.
- **Source:** ours (a matching heuristic), not ESPN's.
- **GitHub:** #131

### A career-span `shot_distance` drops an unseparable season and loses the derived-season caveat, silently
- **Found:** 2026-09-20, fixing #141 (`shot_chart`/`shot_distance` honoring a
  career `span`)
- **Evidence:** one named season already refuses a `shot_value`-filtered
  distance in `UNSEPARABLE_SHOT_VALUES` (2002) and notes a
  `DERIVED_SHOT_VALUES` one (2003, 2022) - `templates/shots.py`'s
  `shot_distance`, both checks keyed on `season`. A career span (`span:
  "career"`) leaves `season` `None`, so neither check ever fires: 2002's rows
  are silently excluded from the `{SHOT_VALUE_SQL} = ?` filter (their value is
  NULL there, by design - see `shotchart.SHOT_VALUE_SQL`) with nothing saying
  so, and a 2003/2022 season folded into the sum carries none of the "read
  from the description" caveat a single-season query gives it. Measured
  against `/home/jeff/code/association/nba.duckdb`: Kobe Bryant's career
  three-point postseason shot distance (`shot_distance(ctx, {"player": "Kobe
  Bryant", "season_type": 3, "span": "career", "shot_value": 3})`) answers
  "26.2 feet, over 717 attempts" and correctly notes his 1997-2001 postseasons
  are pre-floor and not shown - but says nothing about 2002's 557 postseason
  shot_chart rows, none of which can contribute to that 717 (all NULL under
  the value filter), or about 2003's 421, which DO contribute but without the
  derived-value note a lone `season=2003` query gives
  ("ESPN did not label 2003's shots as twos or threes, so they are read from
  the description..."). `shot_chart` (the plot, not the average) does not have
  this gap - `shotchart._render_for_player_notes` already aggregates both
  notes as a set over every season a multi-season draw actually kept.
- **User sees:** a career average with no shot_value filter reads fine; one
  WITH a filter (threes, twos) whose career overlaps 2002 or 2003 gets a
  number that is short of the truth (2002 silently thinned) or unexplained
  (2003 unlabeled by caveat), with no sign either happened.
- **Next step:** in `shot_distance`'s career branch, collect the distinct
  seasons actually kept by the query (a `season` column is already selectable
  alongside the aggregate) and build the same two notes
  `shotchart._render_for_player_notes` does, or factor that helper out for
  both callers to share.
- **Priority note:** P2 - the number given is correct over what it actually
  summed; the gap is what it does not say.
- **GitHub:** #159

### "76ers" is not a word, so every "vs 76ers" question loses its opponent
- **Found:** 2026-09-21, reading today's corpus fall-throughs
- **Evidence:** `entities._words` splits on `[^A-Za-z]+`, so
  `_words("myles turner vs 76ers last 5 games")` is
  `['myles', 'turner', 'vs', 'ers', 'last', 'games']` and `_team_after_versus`
  returns None, while `_team_named("76ers")` resolves team 20 fine. The same
  slot shape with "nyk" works ("tim hardaway vs nyk"). 22 of 1,972 reasonable
  large-set questions say "76ers", 12 of 2,285 after vs/against.
- **User sees:** "game_log needs a team or a player" - a fall-through - for the
  one franchise whose name starts with a digit.
- **Next step:** keep digits inside a word in `_words` (or fold "76ers" to
  "sixers" in `_fold`), then re-run the entity golden comparison: `_words`
  feeds `players_named_in`, where a stray number must not start naming people.
- **GitHub:** #176

### A second player in `opponent` is never moved to `players`
- **Found:** 2026-09-21, live sample; also corpus "jay huff game log vs Embiid"
- **Evidence:** "giannis stats against jokic" routes to `player_matchup` with
  `player: 'Giannis Antetokounmpo', opponent: 'Nikola Jokic'`.
  `players_named_in` finds both; `scope_from_question` changes nothing; the
  template says "no team matching 'Nikola Jokic'" and falls through.
- **User sees:** the slow agent, for the most ordinary two-player question.
- **Next step:** in the entity stage, an `opponent` that names no team and IS a
  player the question names joins `players`. Eliminates, never chooses.
- **GitHub:** #177

### A name written without its periods matches nobody ("Pj washington")
- **Found:** 2026-09-21, live sample
- **Evidence:** `find_players(con, "Pj Washington")` is empty;
  `find_players(con, "P.J. Washington")` finds athlete 4278078.
  `players_named_in` also finds nobody in "Pj washington vs pacers game by
  game". 22 of 1,972 reasonable large-set questions use an initial-pair name
  (`\b(pj|cj|tj|aj|rj|kj|dj|og|jj)\b`); some ("CJ McCollum") are stored
  without periods and work.
- **User sees:** "no player matching 'Pj Washington'", then the agent.
- **Next step:** fold periods out of both sides in `find_players` the way
  accents already are (178c21f).
- **GitHub:** #178

### A retired player with no season named is refused instead of answered over his career
- **Found:** 2026-09-21, live sample - 4 of 200: "Allen Iverson playoffs vs
  raptors", "dwight howard vs Marc gasol", "Yao Ming playoffs stats with the
  Houston rockets", "kawhi last 10 playoff games"
- **Evidence:** the season defaults to 2026 and the answer is "No 2026
  postseason games found for Allen Iverson. He last appears in 2009 ... name
  one, or ask for his career." Honest, and #18's deliberate choice - but the
  question named no season, so 2026 is ours, not the user's.
- **User sees:** a refusal that tells them how to re-ask.
- **Next step:** a product decision first: when the question states no season
  (`_validate_season` found none in the text) and the player has no row in the
  default, answer `span: career` and say so. Re-score the corpus either way.
- **GitHub:** #179

### The name check refuses a possessive typo it used to answer ("embids")
- **Found:** 2026-09-21, the web-session replay (agent), confirmed by the lead
- **Evidence:** "show me embids 3pt percentage over the last 5 years" answered
  on the 2026-09-09 build; on `31b2ec6`
  `override_invented_players` returns `invented=['Joel Embiid']`: "embids" is
  two edits from "embiid" against `_edit_budget`'s one for a six-letter word.
- **User sees:** "This was read as a question about Joel Embiid, who the
  question does not mention".
- **Next step:** strip a trailing possessive "s" before measuring, rather than
  widening the budget - #131 measured what a wider budget costs.
- **GitHub:** #180

### A subject `game_log` was not given is not restored from the question
- **Found:** 2026-09-21, live sample
- **Evidence:** "Luka doncic last 5 away hames" routes to `game_log` with no
  `player`; `players_named_in` returns `['Luka Doncic']`, `scope_from_question`
  changes nothing, and the template raises "game_log needs a team or a player".
  Not traced further. 7 corpus rows and 3 sample rows end on that message; most
  of the others are names nobody typed correctly, which nothing can restore.
- **User sees:** a fall-through.
- **Next step:** find why `_scope_from_question_restore_player` does not fire
  when neither `player` nor `team` is set.
- **GitHub:** #181

### A leaderboard "and the team they play for" request silently drops team
- **Found:** 2026-09-21, yardstick-v2 key-building (A_netpoints_shots slice,
  `live_31b2ec6.jsonl`)
- **Evidence:** "show the top 50 in total adjusted netpoints and the team they
  play for" and "who are the top 50 in total adjusted netpoints with the team
  they play for" both route to `leaderboard` with `stat=netpoints_per_100,
  limit=50`; the first arrives with `fields=['steals','rebounds','assists']`
  (none of which the question asked for), the second with no `fields` at all.
  Neither answer names a team anywhere. The cause is structural, not a router
  miss: `EXTRA_FIELD_COLUMNS` (`query/metrics.py`) is a fixed whitelist of
  `points/rebounds/assists/steals/blocks/minutes` sourced from
  `player_season_stats`, and has no team entry at all - there is no slot value
  that could have produced one. The ranking itself and every value in it (SGA
  9.91 down to Stephon Castle 2.04) checks out exactly against
  `net_points_player`.
- **User sees:** a fluent, numerically correct top-50 list with the explicitly
  requested "team" column simply absent, no caveat that it could not be added.
- **Next step:** add a `team` entry to `EXTRA_FIELD_COLUMNS` (join
  `player_season_stats.team_id` -> `teams.abbreviation` the same way the other
  five extra fields already join), or have the leaderboard template say
  explicitly that team is not an available field when asked for one that is
  not in the whitelist.
- **Source:** ours, not ESPN's.
- **GitHub:** #182

### `_no_games`'s "did not play" is also the wrong cause when a real narrowing empties the pool
- **Found:** 2026-09-22, step 3 C2 (record_when and streak read the relation's
  full narrowing set: `opponent`, `venue`, `without`, `split`, `game_n`,
  `since`, `season_n`, `below`, `above`).
- **Evidence:** `common._no_games` (called by `_record_when_query` and every
  other template on the relation when the narrowed query returns zero rows)
  counts a player's box-score rows over the WHOLE scope - ignoring whatever
  narrowed the main query to nothing - and if that count is nonzero, says
  "X was listed in N box scores ... but did not play in any of them." That
  sentence is about whether he played at all, and a narrowing that legitimately
  excludes every game he DID play (a venue, an opponent, a teammate's absence,
  a box-score line) is a different fact entirely. Measured against
  `/home/jeff/code/association/nba.duckdb`: `record_when(con, {"player": "Jayson
  Tatum", "stat": "points", "threshold": 25, "below": ["under 3 assists"]})`
  (against `tests/query/test_conditions.py`'s `league` fixture, where every
  played row is fixed at 3 assists) returns "Jayson Tatum was listed in 5 box
  scores in the 2026 regular season but did not play in any of them" - false;
  he played 3 of those 5 games (e1, e4, e7), and the other two are a DNP and a
  missing box score. The same shape is reachable through any narrowing that
  empties the pool - an opponent he never scored a qualifying game against, a
  venue, a teammate's absence - not only a `below`/`above` line, so this is a
  more general form of #109 (which is specifically about the empty-2013-2018
  box-score population): #109's fix (threading `box_source(con)` through
  `_no_games`) does not touch this one, since the count there would still be
  scope-wide rather than narrowing-aware.
- **User sees:** a confident, false "did not play in any of them" for a player
  who clearly did, on a question that only narrowed him out of the row set -
  the exact false-cause shape AGENTS.md warns about ("check which fact is
  actually missing").
- **Next step:** `_no_games` needs to know whether the EMPTY result came from
  him having no games at all in scope, or from a real narrowing (`Narrowed`)
  excluding every one of them, and say which. The narrowed object is already
  available at every call site (`record_when`, `player_splits`, `with_without`,
  `streak`); the fix is likely a second count run without the narrowing
  applied (the unnarrowed total already has a helper in
  `_no_narrowed_games`/`Narrowed.clauses(narrowed=False)`) compared against the
  narrowed zero, the same two-fact split `_no_narrowed_games` already makes
  for game_log/player_stat.
- **Source:** ours, not ESPN's.
- **GitHub:** #183

## P3: refusal or gap

### The router keeps only the word "division" of "vs southeast division", so the alignment narrowing never sees the division
- **Found:** 2026-09-24, re-asking yardstick-v2 F055 after `team_alignment`
  landed (master `02fd795`, warehouse loaded).
- **Evidence:** "alperen sengun double-doubles vs southeast division career
  away" routes `player_splits` with `situation: 'division'` - `router._SITUATION`
  captures `\bdivision\b` alone (and "east(ern)/west(ern) conference",
  "vs the east/west"), never the division's NAME. `calendar.parse_alignment`
  reads "vs the southeast division" / "against eastern conference teams" and
  refuses the bare word honestly: "'division' names a conference or
  division, but not in a shape this reads". The relation half is done (the
  same question with the full phrase as the slot answers 8 double-doubles in
  22 road games, the key exactly).
- **User sees:** a refusal naming the right cause and the wording that
  works - but the question as typed does not answer.
- **Next step:** in `route()`, capture the division name with the word
  (`(?:atlantic|central|southeast|northwest|pacific|southwest|midwest)\s+division`)
  and the "vs (the) east/west" forms as the `situation` value, the way the
  month and holiday captures keep their phrase; add the case to
  `check_routing.py`. `router.py` is the experiment arms' file while sweep 2
  runs - after it merges.
- **Source:** ours.
- **GitHub:** #213
### `team_leaderboard` excludes `situation`, so "best record since <day>" still falls through
- **Found:** 2026-09-24, fixing yardstick-v2 F104's routing.
- **Evidence:** "Best NBA record since January 31st 201" now routes
  `team_leaderboard {'stat': 'record', 'rank': 'best', 'limit': 1,
  'situation': 'since january 31st'}` (it was `team_record` with no team).
  Called directly on that tree, `check_scope` refuses: `team_leaderboard
  cannot honor ['situation']`, and `compose.answer` returns None, so it
  reaches the agent. The exclusion's reason in
  `templates/common.py:TEAM_RELATION_SCOPING_EXCLUDED["team_leaderboard"]`
  ("a leaderboard ranks a season, not the games in one weekday, month or
  holiday within it") is about narrowing the pool, and a "since <day>"
  window is not that: "best record since January 31" ranks every team over
  a window, which is the standings question the key asks.
- **User sees:** the slow agent, for a standings question.
- **Next step:** the template owner lets `team_leaderboard` honor a
  `since_day` situation (the team relation already applies it,
  `TeamNarrowed.narrow_calendar`), keeping the weekday/month/holiday cells
  excluded if that reasoning holds for them.
- **Priority note:** P3 - one corpus question, a fall-through.
- **GitHub:** #214

### A player's team record since an absolute date, both season types, is refused: "towns home rec including playoffs since 1/26/20 vs spurs"
- **Found:** 2026-09-24, fixing yardstick-v2 F110 (it used to answer the
  Raptors' record, a team the question never names; now refused by name).
- **Evidence:** the key asks for Towns's teams' home record against the
  Spurs in his games since 2020-01-26, regular season and playoffs. No slot
  carries an absolute start date: `situation: "since january 26"` is read
  within EACH season (`calendar.parse_situation`'s `since_day`), so
  `since: 2020` beside it would drop October-January of every later season;
  and "1/26/20" is not read by `router._validate_date` at all (month names
  only). No player-relation template answers "his team's record in his
  games" with both season types combined either.
- **User sees:** a refusal naming Towns ("team record has no reading for
  one") - honest about the subject, silent about the date window.
- **Next step:** an absolute `after`/`before` date slot on both relations
  (one clause on `Narrowed` and `TeamNarrowed`), and a numeric-date reading
  ("1/26/20") in `route()`; then this is record_when/player_splits-shaped.
- **Priority note:** P3 - one corpus question, refused rather than wrong.
- **GitHub:** #215

### `game_log`'s "last 10 of N games" heading misses the without branch
- **Found:** 2026-09-24, grading `live_rest.jsonl` (yardstick-v2 F158).
- **Evidence:** "bane game log without anthony black and franz wagner this
  season" says "last 10 games of the 2026 regular season" where 15 games
  qualify (key: a contiguous 15-game stretch), while the same template says
  "last 10 of 39 games" for a measure-narrowed log (F149). The count before
  the window is computed on the plain and measure paths and not on the
  teammate-absence one.
- **User sees:** a page of a log with no word on how many games it is a page
  of - the shape F149 was fixed for.
- **Next step:** count through `whole_span` on the without path too, the
  way `_game_log_window_of` does for the others.
- **Source:** ours.
- **GitHub:** #208

### A composed league-wide `threshold_count` falls through when the router's `stat` already names the threshold's own column
- **Found:** 2026-09-24, building F161's multi-line move in
  `query/compose/move.py`.
- **Evidence:** `_everyone_threshold_predicates` adds the phrase's own
  column as a predicate only when it differs from the ranking `measure`
  variable (`_stat_measure(slots.get("stat"))`) - a dedup meant for
  `_everyone_ranking`'s grouped-by-player point, where `measure` is what
  the read is grouped and ordered by. `_everyone_threshold_count`'s point
  uses no `measure` at all (only `predicates`), so whenever the router's own
  `stat` slot already names the same column the threshold phrase does - the
  ordinary case, not an edge one - the predicate is silently skipped and the
  count has nothing to count, raising `Unsupported` in
  `_everyone_threshold_count`. Reproduced against `nba.duckdb`:
  `compose.answer(ctx, "threshold_count", {"stat": "points", "threshold":
  30, "season_type": 2}, "who had the most games with 30+ points this
  season")` returns `None` (falls through to the agent) - a very ordinary
  routing of a very ordinary question.
- **User sees:** a slow, unreliable agent fall-through (AGENTS.md: 1 correct
  in 9 finished runs) for a league-wide threshold count phrased plainly,
  where the fast path already answers the same shape correctly whenever the
  router's `stat` happens to differ from the phrase's word (see
  `test_the_questions_own_number_names_its_column_not_the_routers_stat`,
  `tests/query/test_compose.py`).
- **Next step:** `_everyone_threshold_count` needs the phrase's own column
  regardless of what `measure` says, since it never reads `measure` at all -
  either pass `_everyone_threshold_predicates` a `None` measure when the
  caller is a count (not a ranking), or give `_everyone_threshold_count` its
  own predicate read independent of the ranking dedup. Found while building
  F161's `_everyone_multi_line_games` (same file); not fixed here to keep
  that change to its own scope - a fixture test
  (`test_a_single_number_stat_line_still_counts_by_player`) pins today's
  behavior (a `stat` that differs from the phrase) so a fix does not regress
  it silently.
- **Source:** ours, not ESPN's.
- **GitHub:** #209

### `team_record`'s combined-season-types sentence drops the regular half's "standings from 1993-94" caveat
- **Found:** 2026-09-23, grading `live_sweep.jsonl` (yardstick-v2 F116).
- **Evidence:** "warriors all-time record including playoff record at away"
  now answers "574-843 (.405) combined on the road, including the playoffs
  (523-791 (.398) regular season, 51-52 (.495) playoffs)" plus the 2000
  standings-gap note. Asked for one season type, the regular half says
  "across the 33 regular seasons from 1993-94 through 2025-26 - ESPN's
  standings carry no home/road split before 1993-94", and the playoff half
  "in every postseason from 1989 through the latest". Combined
  (`templates/teams.py`, `_combined_record_result`), only "Note:" lines are
  carried over (`_extract_note`), so the two halves' different starting
  seasons are not stated - the reader cannot see that the regular half starts
  five seasons later than the playoff half. Measured: the 523-791 is right
  for what standings hold (the key's 550-811 counted the phantom 1993 season
  and six pre-1994 stray games in the game list; `DATA.md`).
- **User sees:** a combined record with no word about the two spans it
  combines; the number is right but its coverage is not stated.
- **Next step:** carry each half's span into the parenthesis - "(523-791
  regular season from 1993-94, 51-52 playoffs from 1989)" - from the halves'
  own `data` (add the first season there if it is not), not by parsing their
  sentences.
- **Source:** ours (the standings' own floor is ESPN's, in `DATA.md`).
### The router cannot ask `leaderboard` to show each player's team (F017)
- **Found:** 2026-09-23, fixing yardstick-v2 F017 ("who are the top 50 in
  total adjusted netpoints with the team they play for").
- **Evidence:** `leaderboard`'s `fields` slot now accepts `"team"` and adds a
  "team" column, read from `player_game_log` (the season's most recent
  team, with a "Team is each player's most recent team that season." note
  when a shown player was traded) - `templates/players.py`,
  `_leaderboard_fields`/`_leaderboard_show_teams`. Warehouse-verified
  directly (`leaderboard(ctx, {"stat": "netpoints_per_100", "fields":
  ["team"], "limit": 50})` lists all 50 players with a team beside each).
  But `ROUTER_SCHEMA`'s `fields` enum (`router_prompt.py`) is
  `["points","rebounds","assists","steals","blocks","minutes"]` - no
  `"team"` - so the router can never emit `fields: ["team"]` under
  constrained decoding, whatever the question's wording. F017's own trace
  (`slots={'stat': 'netpoints_per_100', 'limit': 50, 'season_type': 2}`)
  confirms this: no `fields` at all. Also note the row-count half of F017's
  complaint ("neither the full 50 ... is given") was not reproducible
  measured directly against this worktree's code before this change - a
  50-row request already returned exactly 50 rows in the answer text
  (1,238 characters); the yardstick log's own captured `answer` field cuts
  off mid-word ("...Jalen") in a way consistent with the LOG's display
  truncation, not the system's real output. Not re-measured on a from-
  scratch git-bisect, so recorded as a discrepancy rather than closed as a
  separately-fixed bug.
- **User sees:** a leaderboard that never shows team names, even for
  wording that explicitly asks for them ("with the team they play for",
  "and their team") - the router's own words, not the template's.
- **Next step:** add `"team"` to `ROUTER_SCHEMA`'s `fields` enum and a line
  in `ROUTER_PROMPT` describing when to set it, then verify with
  `scripts/check_routing.py` (both belong to the router owner - this
  session's split assigns `router_prompt.py`/`router.py` there, and
  changing either without re-running that script risks moving an unrelated
  question's routing per `AGENTS.md`, "Any edit to ROUTER_PROMPT moves
  slots on unrelated questions").
- **Source:** ours.
- **GitHub:** #210

### No leaderboard metric ranks average three-point shot distance
- **Found:** 2026-09-23, fixing the wrong-cause refusal `leaderboard` gives
  for `stat: "shot_distance"` (yardstick-v2 F019 - "who lead the league in
  avg 3 point distance"/"...for 3 point shots"). The wording was fixed in the
  same commit (it used to say "no leaderboard ranks shot distance", which
  reads as impossible and is false), but the underlying gap the reworded
  refusal now honestly names is still open.
- **Evidence:** the key computes a real league leader straight from
  `shot_chart` - Kristaps Porzingis, 27.37 ft average three-point shot
  distance over the 2025-26 regular season, with a 100-attempt floor and
  end-of-period heaves (game clock under 3 seconds) excluded; leaving heaves
  in changes the leader (Alperen Sengun, 29.62 ft, 9% heaves). Nothing in
  `LEADERBOARD_METRICS`/`query/leaderboard.py` computes this - `shot_distance`
  is a per-player metric (`templates.shots.shot_distance`) with no
  league-wide ranking built over it.
- **User sees:** a refusal naming the true cause now ("Shot distance is not
  ranked league-wide yet - ask about one named player's average shot
  distance instead") rather than a false one, but the question itself is
  still unanswered by the fast path.
- **Next step:** a `shot_distance` leaderboard metric, with its own
  qualifying floor (attempts) and heave exclusion measured the way
  `SHOT_VALUE_SQL`'s per-season caveats already are for the per-player read -
  not simply plugged into `LEADERBOARD_METRICS` with somebody else's minimum,
  since an unqualified leader is a single desperation heave (checked: the
  warehouse's unfiltered leader is not Porzingis).
- **GitHub:** #205

### `period_leaderboard` stays off the player-games relation
- **Found:** 2026-09-22, step 3 C5's own second task: assess whether a
  no-player read of the relation (`player_games.league()`) would let
  `period_leaderboard` honor `opponent`/`venue`/`since`/`span`/`date` for a
  league-wide or team-wide ranking, the way `period_split` now does for one
  player. Assessed and not ported - documented in the template's own
  docstring (`templates/games.py: period_leaderboard`), per the README's
  instruction to write down why rather than guess at a port.
- **Evidence:** two separate blockers, not one:
  - `opponent`/`venue`/`date` would need a NEW no-player narrowing step:
    `common._narrow_player_games` (what `common.scoped_games` calls)
    hardcodes `pgl.athlete_id = ?` into its base clause, so it cannot build a
    league-wide `Narrowed` at all today - `player_games.league()` gives the
    bare relation, but nothing turns an opponent/venue/date slot into a
    clause on it without a player to resolve `without`/tenure against, which
    is exactly the piece C1 left off the relation for `threshold_count` and
    `single_game_high`'s own no-player modes.
  - `since`/`span` reopen, at league scale, the identical bug just found and
    fixed for `period_split`'s own `span`/`since` in this same commit
    (`RELATION_SCOPING_EXCLUDED["period_split"]`, `templates/common.py`) -
    both were previously silently wrong the same way (a career/`since` sum
    that answered real games but headed them with the wrong single season),
    not merely refused, and are refused now rather than shipped like that
    again: `PERIOD_RECONCILIATION`
    is measured per season, so a leaderboard ranged over several seasons would
    need that caveat applied once per season summed into each player's total,
    or the range refused outright - porting the READ alone would let a
    badly-reconciled season (2016, 76.5%) drag rankings with no caveat naming
    it. `date` has a narrower problem even alone: it narrows to ONE game, and
    `PER_GAME_MIN_GAMES` (the qualifier a per-game leaderboard needs, or the
    leader is whoever played once and scored eight) would then disqualify
    every player at once.
- **User sees:** "who led the league in 1st quarter scoring against the
  Celtics this season" and the like still refuse (falls through to the agent,
  which per `AGENTS.md` has nothing better to read here either - no other
  source has per-quarter box scores) rather than answering a narrower,
  real question.
- **Next step:** the `opponent`/`venue` half looks buildable without the
  `since`/`span`/`date` half's problems - a no-player `_narrow_player_games`
  variant (or a generalization that makes `player` optional) that skips
  `without`/tenure entirely, plus `opponent`/`venue`/`team` clauses copied from
  the existing pattern, single-season only. Worth a dedicated pass rather than
  folding into a future step 3 task, since it is new capability (a behavior
  change with its own golden cases), not a pure port.
- **GitHub:** #185

### `player_splits` cannot honor a teammate's absence, a box-score line, a playoff-series game or an ordinal season when the subject is a team, not a player
- **Found:** 2026-09-22, step 3 C2 work on `player_splits`/`period_split`
  (out of that task's scope - the fix landed only for the PLAYER subject,
  through `common.condition_player`; `_player_splits_team` was never
  touched). Still open after step 3, C4 ported `_player_splits_team` onto
  the team-games relation (`common.team_games`/`TeamNarrowed`): that port
  gave the team branch `opponent`/`venue` for `record_when`/`streak` too
  (see "record_when's team branch and streak's team/league branches..."
  below) but did not add any of these four - the gap below is unchanged,
  only the code it points at moved. Still open after step 3, C4b: `game_n`
  now has a relation to lean on (`common.team_games` reads it off `slots`
  directly, and `record_when`'s team branch answers it for real - see
  CHANGES.md), which narrows the "Next step" below, but `player_splits`
  itself still refuses `game_n` for a team with no player named by name,
  before `_player_splits_team` is ever called - unchanged here. `since`
  is not one of the four this entry is about (`_player_splits_team` has
  read it since 4.3.0), but its LABEL was wrong in the same shape this
  project keeps producing - a since-bounded span rendered the identical
  "every regular season on record (...)" text a plain career gets, with
  nothing saying a starting year had been named at all. Fixed in the same
  commit that added `since` to `record_when`/`streak`'s team branches
  (`_team_span_label` now reads `span.since`), as a shared-helper fix
  rather than a `player_splits`-specific one.
- **Evidence:** `templates/common.py`'s `HONORED_SCOPING["player_splits"]`
  declares `without`, `below`/`above`, `game_n` and `season_n` for the intent
  as a whole, with no distinction between a named-player question and a
  named-team-only one - so `check_scope` lets them through regardless of
  which subject the question turns out to have, and `_player_splits_team`
  reads none of them. Measured read-only against
  `/home/jeff/code/association/nba.duckdb` before this was caught:
  `player_splits(ctx, {"team": "Philadelphia 76ers", "split": "wins_losses",
  "without": "Joel Embiid", "season": 2024, "season_type": 2})` answered "The
  Philadelphia 76ers, in wins and losses, 2024 regular season (82 games)" -
  identical to the same call with `without` left out, with the heading saying
  nothing about Embiid, who played only 39 of those 82 games (same warehouse,
  same query filtered to his own `athlete_id`) - the honest "without" answer
  is a 43-game pool, not 82. That is the wrong-answer shape (a silent
  narrowing `check_scope` exists to stop, missed here only because the slot
  IS declared honored, by the other subject the same intent can have), so it
  is refused now rather than shipped: `player_splits` raises
  `"player_splits cannot honor below/above, game_n, season_n or without for a
  team with no player named"` when any of the four are set and no player is
  named, the same way a starter/bench split is already refused for a team.
- **User sees:** a refusal (falls through to the agent) for "76ers splits
  without Embiid" and the like, rather than the fluent wrong answer above.
- **Next step:** teach `_player_splits_team` the four narrowings for real -
  `without` is the one with the clearest path, the same teammate-absence
  filter `with_without` already has for a team subject
  (`_with_without_games`, `conditions.py`): add a teammate-absence clause to
  the `TeamNarrowed` `common.team_games` already builds (`TeamNarrowed.narrow`,
  `query/team_games.py`) - the same tenure-and-played-guard shape
  `_narrow_player_games`'s `without` loop uses for a player subject - and say
  so in the heading via `TeamNarrowed.filters()`, which already carries
  opponent/venue for this same function. `game_n` (one game of each series,
  by team) now has a relation to lean on - `common.team_games` reads it off
  `slots` unconditionally (step 3, C4b), so teaching `player_splits` this one
  is removing its own explicit refusal for the team-only shape, not building
  new narrowing machinery. `below`/`above` (a line on `team_box_stats`) still
  has no shared step to lean on - `record_when`'s team branch reads its own
  threshold column this way (`_TEAM_RECORD_WHEN_JOIN`, `splits.py`), but
  that is a single named column joined once, not the general "keep games
  under/over a line" shape `measure_filters`/`narrow_measures` give the
  player relation; `season_n` has no team equivalent at all and should
  likely stay refused.
- **Source:** ours, not ESPN's.
- **GitHub:** #186

### "Career ... in 2015" is 2015 on the condition templates and a refusal on the others
- **Found:** 2026-09-21, step 3 C1 (folding the two readers of a player's
  games into one).
- **Evidence:** `templates.common._condition_scope` (player_splits,
  record_when, streak, with_without) lets a named season beat `span=career`
  - "the router keeps a named year alongside it, and 'career ... in 2015' is
  asking about 2015" - while `_span_of` (game_log, player_stat,
  threshold_count, single_game_high, period_split) raises "a career span and
  the 2015 season at once" and the question falls through to the agent. Same
  slots, opposite outcomes, by template.
- **User sees:** on one intent an answer for 2015; on another, the slow agent.
  Which one depends on the router's intent choice, not on the question.
- **Next step:** one rule, in `scoped_player`, for both. The condition
  templates' reading is the useful one (a named year is more specific than
  "career"); decide it as a product decision in C2, then delete the branch
  from `_span_of`. C1 kept each as it was so the refactor could be proved
  pure.
- **Source:** ours, not ESPN's.
- **GitHub:** #187

### "Total points scored ... in the last N games" lists the games but never sums them
- **Found:** 2026-09-21, while fixing "'Last N games' means the last N
  regular-season games..." (removed above). One of that entry's five
  questions, "Total points scored by the toronto raptord in the last 10
  games", routes to `game_log`, which now finds the right ten games (the fix
  above) but still only lists each game's score - `_team_game_log_games`'s
  `data` carries no summed total, only `wins`/`losses`/`games`. Measured
  against the real warehouse, `nba.duckdb` (read-only): the Raptors' true last
  10 games (2026-04-09 through 2026-05-03, three regular-season and seven
  postseason) sum to 1,150 points, and the answer states none of them - a
  reader has to add the ten scores themselves.
- **User sees:** a correct, complete game-by-game listing with no total,
  though "total" is the word the question used.
- **Next step:** `game_log` (or a `total: true`/`stat` reading on it) could sum
  the listed games' scores when the question asks for a total - `_named_a_stat`
  already reads "total" as naming a stat for other intents. Unrelated to the
  season-type mixing fix beside it: this is about what the answer states, not
  which games it found.
- **Source:** ours, not ESPN's.
- **GitHub:** #188

### No league ranking by shot distance, and the refusal reads as if the data could not do it
- **Found:** 2026-09-21, yardstick-v2 blind key against build 50c1faa.
- **Evidence:** "who lead the league in avg 3 point distance?" and "who lead
  the league in shot distance for 3 point shots?" (both Jeff's) answer "No
  leaderboard ranks shot distance across the league." The blind keyer computed
  one from `shot_chart`: Kristaps Porzingis, 27.37 ft, with a 100-attempt floor
  and end-of-period heaves excluded - and measured why both are needed (one
  81-foot heave tops the unfiltered list; Sengun is 29.62 ft with heaves and
  26.65 without, on the same 141 shots).
- **User sees:** a refusal that is true of the templates and reads as a claim
  about the data.
- **Next step:** a `shot_distance` ranking with a stated attempts floor and a
  stated heave rule, reading `SHOT_VALUE_SQL`. Reword the refusal meanwhile:
  "shot distance is answered for one player, not ranked across the league yet".
- **Source:** ours, not ESPN's.
- **GitHub:** #189

### Foul-out counts: check which column they read - the season table undercounts
- **Found:** 2026-09-21, yardstick-v2 blind keyer P5; re-measured by the lead.
- **Evidence:** see `DATA.md`, "`disqualifications` in the season table
  undercounts foul-outs". Victor Wembanyama has 3 box-score games with 6 fouls
  (one in 2024, two in the 2026 regular season) and
  `player_season_stats.disqualifications` sums to 1.
- **User sees:** nothing confirmed wrong yet - "How many times has webanyama
  fouled out of a game" asks for a clarification of the typo in the run.
- **Next step:** confirm `threshold_count`'s "fouled out" reads
  `player_box_stats.fouls >= 6` and not the season column, with a test pinning
  Wembanyama's 3.
- **Source:** ESPN's; see DATA.md.
- **GitHub:** #190

### A position group as the subject has no template, and six corpus questions want one
- **Found:** 2026-09-20, tallying what still falls through after the
  quarters-and-halves work
- **Evidence:** six reasonable corpus questions make a position the subject -
  "Centers stats game log vs kings", "stating centers vs phoenix suns log",
  "stating centers vs suns", "forwards with 20+ mins vs gsw log", "each center
  1q pts log vs nugget", "highest 3 point percentage in a season. by a
  shooting guard with at least 400 attempts". None is answered: the router
  files the position in `team` or `opponent` or drops it, and the templates
  then refuse for want of a player ("game_log needs a team or a player") or
  answer the opponent's own log. **The warehouse can answer them**: `players`
  carries `position_abbr` for all 3,101 rows - G 862, F 724, C 502, SG 254,
  PF 252, SF 247, PG 237, NA 22 - so "centers" is a filter on the subject the
  same way a team is.
- **User sees:** a refusal, or the opposing team's log, for a question that
  names its subject as clearly as a player's name would.
- **One of the six is now answered, from the compose side** (this session):
  "highest 3 point percentage in a season. by a shooting guard with at least
  100 attempts" arrives with the router's own `player` slot holding
  literally "shooting guard" (not dropped, not filed as `team`/`opponent` -
  this question's own routing shape) - `query/compose/move.py`'s
  `_position_only_player`/`_drop_position_only_player` reads a slot that
  holds NOTHING but a position word as the position-group subject rather
  than a player name to resolve, so `_everyone_point` reaches its existing
  position filter the same way "centers game log" already did. Also added:
  a "with at least N games" phrase now sets the ranking's own minimum
  sample (`_ranking_minimum`), replacing the default
  `PER_GAME_MIN_GAMES` floor. Warehouse-verified against `nba.duckdb`,
  season 2025 (2024-25, which carries specific position codes - see
  DATA.md): "highest 3-point percentage in 2025 by a shooting guard with at
  least 40 games" correctly ranks Alec Burks (42.5%, 49 games), Malik
  Beasley (41.6%, 82 games), Shake Milton (35.8%), Brandon Boston Jr.
  (35.0%) - descending, as asked.
- **Still open, and this is why the exact target question above is not
  fully answered:** an ATTEMPTS floor ("with at least 100 attempts") is
  refused by name (`_everyone_ranking` raises rather than silently dropping
  it or misreading it as a games count - the relation has only a
  minimum-GAMES `HAVING` clause today, no minimum-attempts one) - a real
  capability gap, not a bug, and worth its own entry if built. **A second,
  independent gap surfaced verifying this**: the CURRENT season (2026)
  carries almost no specific position codes at all (`SG`/`PG`/`PF`/`SF`),
  only the generic `G`/`F`/`C` - see DATA.md, "The current season's roster
  carries generic position codes" - so a position-GROUP question answered
  for "this season" with no year named returns nothing even once the
  subject reads correctly, and the four questions this entry filed under
  "Centers"/"stating centers"/"forwards" remain open (a router-side slot
  drop this worktree does not own).
- **Next step:** an attempts (or makes, or minutes) floor on a league
  ranking - a `HAVING SUM(<attempts column>) >= N` the relation does not
  carry yet, keyed off the same measure being ranked; the other four
  questions need the router to stop filing a position word as `team` or
  dropping it outright.
- **GitHub:** #160
- **Re-measured 2026-09-24:** the compiler reads a position group as its
  subject now (`compose.move._position`, "every center vs the Sacramento
  Kings" - yardstick-v2 F153/F154 graded correct on the live sweep run),
  reached when the template refuses; a `player` slot holding only a
  position word is the compose agent's next step (F056). Left open for the
  corpus questions that route to a template which answers the team's own
  numbers without refusing.

### A team's per-quarter average of anything but points has no source
- **Found:** 2026-09-20, finishing the quarters-and-halves work
- **Evidence:** "trailblazers stats last 10 games 3 point average 1st quarter"
  asks for a team's first-quarter three-point average. `team_quarter_points`
  reads `games.home_linescores`/`away_linescores`, which hold one total per
  period and nothing else, so it can answer points and only points; the
  player-side `period_split` refuses every other stat for its own reason (only
  points are in `shot_chart`).
- **Half of this is now closed.** Restoring the team the run-together nickname
  had lost would have handed this question a POINTS answer to a three-point
  question - measured, "The Portland Trail Blazers scored 2428 total points in
  the 1st quarter ... averaging 29.6 per game" - so
  `_team_quarter_points_check_stat` now refuses a `stat` that does not resolve
  to points, naming the linescore as the limit. It raises
  `TemplateUnsupported` rather than refusing outright, which keeps today's
  behavior for these questions exactly: they fall through.
- **User sees:** the agent, slowly, for any team per-quarter question about
  something other than points. No longer a wrong answer.
- **Next step:** a team's per-quarter THREE-POINT figures are derivable -
  `shot_chart` carries `team_id`, `period` and the shot's value through
  `SHOT_VALUE_SQL`, which is how `period_split` already counts a player's -
  so answer threes per quarter from that table under the same per-season
  accuracy gating `PERIOD_RECONCILIATION` applies. Rebounds and assists have
  no such source and stay refused.
- **GitHub:** #161

### An award or All-Star question has no table to refuse from, so the agent is free to invent one
- **Found:** 2026-09-18, the algebra spike's attack pass over the large
  StatMuse set (`~/association-research/algebra-spike/stage1/attack_report.md`)
- **Evidence:** 11 of 2,285 real questions ask for an honor outright - "nba
  mvps in 1980's", "how many times has bam adebayo been selected to
  all-defensive team", "most all star appearances for a nets player",
  "players with 5 or more nba all stars and 5 or more all nba teams since
  2000" - and no table holds a selection or a vote share (DATA.md, "No award,
  All-Star or All-NBA selection anywhere"). Nothing catches the shape: no
  coverage floor (there is no table to put one on), no keyword refusal the way
  `coach` has one in `CODE_ASSIGNED_INTENTS`, and `TABLELESS_INTENTS` has no
  entry for it. Not re-verified end to end - running the router is out of
  scope for a read-only pass - but the path is the one `check_coverage`'s own
  reasoning describes: a question with nothing to find reaches the agent,
  which fills the silence from its own weights, as it did for the "Ronaldo
  Lopes" fingerprint recorded above.
- **User sees:** after 30-120 seconds, a confident list of MVPs or All-Star
  counts the warehouse cannot have produced, with nothing marking it as
  invented.
- **Next step:** cheapest first - a code-assigned `award` intent that refuses
  on the words (MVP, All-Star, All-NBA, All-Defensive, Rookie of the Year,
  Sixth Man, Defensive Player of the Year), exactly the `coach` mechanism.
  Then probe whether ESPN's core API serves an honors collection at all,
  with the live check the coach entry used, before anyone writes "ESPN does
  not publish awards".
- **Re-measured 2026-09-21: this is a wrong answer on the fast path, not only
  an agent risk.** In the live sample (same directory as #10's note) "nba mvps
  in 2010's" answered "Nene led the league in true shooting % in the 2010
  regular season" and "1999 sixth man of the year" answered the 1999 minutes
  leader - `stat` is required, so the decoder spends the award on it. 2 of 200
  wrong, 3 more fell through; 27 of 1,972 reasonable large-set questions name
  an award (regex also catches "since the all star break"). P1 by the file's
  own definition.
- **GitHub:** #146

### `_subject_named_in`'s original grammar reads a bare noun as a subject on four corpus questions
- **Found:** 2026-09-19, measuring #148's new "games with"/"games of"/point-games
  grammar against the whole routing corpus - the pre-existing `scored`/`score`/
  possessive grammar was measured alongside it for a baseline, not touched
- **Evidence:** `_SUBJECT_OF_HIGH` in `router.py` (`_SUBJECT_WORDS`'s
  exclusion list) already predates #148 and was not changed by it. Run over
  `/home/jeff/association-research/statmuse-2026-09/feed_queries.txt` (261
  lines), it returns a word on 8 of them; 4 are real player surnames
  (`mcdaniel`, `bryant`, `adam`) correctly matched, but 4 are not names at
  all: `"2024 nba stephen curry double double per game scored on fridays"` ->
  `"game"` (via `"game scored"`), `"game score nba leader"` -> `"game"` (via
  `"game score"`), `"Total points scored by the toronto raptord in the last
  10 games"` -> `"points"`, `"pacers score"` -> `"pacers"`. Not reproduced
  against a live router - running one is out of scope here and restricted to
  one agent at a time - so whether any of these four actually reach
  `single_game_high`/`threshold_count` with no `player` slot (the only path
  that reads `_subject_named_in` at all) is unmeasured.
- **User sees:** if one of these routes there with no player, `_resolved_player`
  is asked to resolve "game", "points" or "pacers" as a player name - almost
  certainly a "no player found" refusal that names the wrong cause (AGENTS.md,
  "the same bug has a mirror image"), for a question that was never about a
  player at all. The two "points scored" lines are plainly team/box-score
  questions ("the toronto raptord", "the wizards"), which argues they route
  elsewhere and this is dormant, but that is exactly the kind of confident
  guess this file exists to replace with a measurement.
- **Next step:** run these four (plus the second `"points scored"` line,
  `"least points scored by the wizards in the first half this season"`)
  through the real router once someone can, and if any reaches
  `single_game_high`/`threshold_count` with no player, add "game", "games",
  "score", "points" and team-name words to `_SUBJECT_WORDS` the way #148 added
  its own stopwords to `_COUNT_SUBJECT_WORDS`.
- **GitHub:** #151

### `record_when`'s team branch and `streak`'s team/league branches still refuse `without`/`split`/`season_n`/`below`/`above` (and `streak` alone still refuses `game_n`)
- **Found:** 2026-09-22, step 3 C2 (record_when and streak read the relation's
  full narrowing set). Narrowed 2026-09-22, step 3 C4 (`opponent`/`venue`
  wired for both team branches) and again step 3, C4b (`since` wired for
  both team branches and the league win/loss branch; `game_n` wired for
  `record_when`'s team branch only) - each cell closed here no longer
  appears below.
- **Evidence:** `HONORED_SCOPING["record_when"]` and `["streak"]`
  (`templates/common.py`) still claim the whole relation set for the WHOLE
  intent, not just the player branch - `check_scope` cannot tell the
  branches apart from the slots alone, since it runs before the template
  does. `splits._condition_needs_player_refusal` is what actually narrows
  the team-only/league-wide shape's refused set now
  (`_CONDITION_PLAYER_ONLY_CELLS = ("without", "split", "season_n", "below",
  "above")`, plus `"game_n"` passed as `streak`'s own `*extra` at its one
  call site - `record_when`'s team branch does not pass it, so `game_n`
  reaches `common.team_games` there and is genuinely answered). Measured
  against the 2026-09-22 warehouse: `record_when(team="Boston Celtics",
  stat="points", threshold=100, season_type=3, since=1991)` now answers a
  4-game reached pool (2-2) and a 2-game short one instead of refusing;
  `streak(team="Boston Celtics", season_type=3, since=1991)` finds the
  longest of the Celtics' own per-season win streaks across that whole span
  instead of refusing to look past the current season.
  `game_n` stays refused for `streak` specifically (both its team and
  league branches, since they share one call site,
  `_condition_needs_player_refusal("streak", slots, "game_n")`) for a
  reason about the answer, not the code: the games `game_n` numbers ("game 4
  of each series") are not consecutive to each other, so a run computed over
  only those games would silently answer a run over a scattered subset
  rather than the real games in between - a streak needs every game in
  between to tell whether the run continued.
- **User sees:** a refusal ("record_when cannot honor ['below'] without a
  named player...") for a team-only or league-wide question naming a
  teammate's absence, a box-score line, a starter/bench half or (for
  `streak` only) a series game number - rather than an answer. `since` and
  (for `record_when`) `game_n` now get real answers (fixed).
- **Next step:** `below`/`above` (a line on `team_box_stats`, the shape
  `record_when`'s own threshold-column join - `_TEAM_RECORD_WHEN_JOIN` -
  already reads for a different purpose) is the next clearest shape; there
  is no shared "team measure filter" step yet the way `measure_filters`/
  `narrow_measures` is for a player, so it needs one before either team
  branch can read it. `without` and `split` have no team-scale reading at
  all and should likely stay refused; `season_n` likewise (a team has no
  "18th season" the way a player does).
- **Source:** ours, not ESPN's.
- **GitHub:** #191

### A `since` span before the 2002 shot floor gets no caveat that shots are clipped
- **Found:** 2026-09-22, step 3, C5.
- **Evidence:** `templates/shots.py`'s `_shot_chart_message`/`shot_distance`
  gate `_career_shot_note` on `span.career and span.since is None` - added in
  this step to fix a real bug (a `since`-bounded read was claiming to "cover
  his whole career on record" against his UNBOUNDED range, which is false of
  a bounded one). The fix is correct for what it removes, but nothing replaced
  it: `_career_shot_note`'s other job - saying when the 2002 shot floor clips
  part of what was asked for - is exactly as relevant to "since 1998" (which
  the floor DOES clip) as to a plain career, and now says nothing for either
  case reached through `since`. `_shots_has_narrowing` still routes `since`
  through `common.scoped_games`, whose own season clause is bounded by
  `table="player_game_log"`'s floor (1994), not `shot_chart`'s (2002), so the
  read itself stays numerically correct (the semi-join to `shot_chart` drops
  the pre-2002 games on its own) - only the caveat is missing.
- **User sees:** "chart Curry's shots since 1998" silently draws only
  2002-on with no note that 1998-2001 are missing, the same silent-narrowing
  shape `AGENTS.md` names as this project's worst failure mode, though here
  the NUMBER is still right - only the caveat is gone.
- **Next step:** extend `_career_shot_note` (or a sibling) to take the actual
  requested floor (`span.since` when set, else the real career start) instead
  of always comparing against the player's own first season, so a `since`
  read gets the same "seasons left out" sentence a plain career already does.
- **Source:** ours, not ESPN's.
- **GitHub:** #192

## P4: tooling, docs, low impact

### The web UI's note feature learns an answer's history file by parsing a trace line, not by reading it off `Answer`
- **Found:** 2026-09-24, adding `POST /api/notes` (annotating an answer,
  saved into its own history file).
- **Evidence:** `association.query.agent.Agent.ask` (`query/agent.py`, out of
  this task's ownership) never returns the history file it wrote - it only
  names it in its `finally` block, as one exact trace line:
  `f"[history] {path}  {history.summary_line()}"`. `AnswerResponse.history_file`,
  the field the page needs to attach a note to an answer, has no other source
  than that line, so `association.web.runner._ask_history_file`
  (`src/association/web/runner.py`) matches it with a regex
  (`_HISTORY_TRACE_LINE`) inside the wrapped trace sink `AgentRunner.ask`
  installs. This is exactly the shape `AGENTS.md` ("Working on the web path")
  warns against elsewhere ("do not parse trace lines back into structured
  fields") - here it is unavoidable without editing `query/agent.py` or
  `query/answer.py`, both explicitly out of this task's scope, so the answer
  event still carries `history_file` as a real value rather than the page
  parsing prose, but the *server-side* extraction is coupled to one literal
  f-string in a file this change could not touch.
  `tests/web/test_app.py::test_agent_runner_reads_the_history_file_off_the_traces_own_history_line`
  pins the exact string and was watched to fail (perturbing the regex) - but
  it exercises a fake `Agent`-shaped stub, not the real one, so it cannot
  catch a change to `agent.py`'s own f-string; nothing in the suite currently
  does, because that file is out of this task's scope and its own tests
  (`tests/query/test_agent.py`) are not this task's to extend either.
- **User sees:** nothing today - the coupling holds, proven by
  `scripts/check_web_ui.py`'s Playwright run against the real page script and
  the offline test above against the real trace-line format. It would degrade
  silently rather than error if `agent.py`'s `f"[history] {path} ..."` line
  ever changed shape: `_ask_history_file` would quietly return None for every
  question, `AnswerResponse.history_file` would go null, and the note control
  would simply stop appearing under every answer - no crash, no failing test
  outside `runner.py`'s own fixture-based one.
- **Next step:** the durable fix is for `Agent.ask` to return the history
  path it already computes (or a small wrapper carrying it) directly, the way
  `AgentRunner.Answered` now does for the web layer - removing the trace-line
  parse entirely. That is a `query/agent.py` / `query/answer.py` change,
  outside this task's ownership; whoever next touches either file should
  fold it in and delete `_HISTORY_TRACE_LINE` / `_ask_history_file`
  (`src/association/web/runner.py`) in the same change. Until then, a
  regression here would surface only as a support report ("the note button
  never shows up") or by rerunning `scripts/check_web_ui.py`, not by a gate.
- **Source:** ours, not ESPN's.

### `team_alignment` is not declared in every `TEMPLATE_SOURCES` tuple that can now read it
- **Found:** 2026-09-24, landing the K3-2 conference/division narrowing.
- **Evidence:** `situation` reaching `Narrowed.narrow_alignment`/
  `TeamNarrowed.narrow_alignment` means `team_alignment` is read by every
  template whose `HONORED_SCOPING` includes `situation` (by
  `_relation_scoping(intent)`'s default, essentially every template on
  either relation) - but `templates.common.TEMPLATE_SOURCES` was not updated
  to list `team_alignment` alongside `player_game_log`/`games`/`team_games`
  for any of them. Deliberately: `team_alignment`'s coverage floor (1988,
  the same request `standings` already floors at) is provably never the
  BINDING one in any declared combination today - `nba.coverage.unavailable`
  takes the NARROWEST (latest-starting) floor among a tuple's tables, and
  every table already declared alongside a box-score or `games` read floors
  at 1989 or 1994, both later than 1988. `scripts/check_coverage.py` (30/30,
  run against a scratch warehouse with `team_alignment` loaded) and
  `test_every_declared_source_has_a_floor` both pass without the addition. A
  handful of the affected entries (`player_stat`, `game_log`, `team_record`,
  `team_leaderboard`, `team_stat`, `team_quarter_points`, `threshold_count`,
  `single_game_high`, `period_split`, `period_leaderboard`, `shot_chart`,
  `shot_distance`) are literal tuples in `templates/common.py`, in this
  task's ownership; the rest (`player_splits`, `with_without`, `record_when`,
  `player_matchup`, `streak`) share `_PLAYER_GAME_TABLES`/`_TEAM_GAME_TABLES`
  from `conditions.py`, outside it.
- **User sees:** nothing today - this is a no-op given the current floors,
  not a wrong-cause refusal. It would only start mattering if a table
  currently floored later than 1988 were ever relaxed to something earlier,
  at which point `team_alignment` should have been the binding declared
  floor and was not.
- **Next step:** add `team_alignment` to the literal `TEMPLATE_SOURCES`
  tuples above and to `_PLAYER_GAME_TABLES`/`_TEAM_GAME_TABLES` in
  `conditions.py`, for completeness rather than correctness. Cheap and
  behavior-neutral (proven by the reasoning above); left for whoever owns
  `conditions.py`/the templates outside `common.py`, or a follow-up pass.
- **Source:** ours, not ESPN's.
- **GitHub:** #216
### A career `game_log`'s heading names one season in parentheses beside a career count
- **Found:** 2026-09-24, checking the answer to "bam adebayo career games in
  the month of march" (yardstick-v2 F096) after routing it to `game_log`.
- **Evidence:** `game_log` with `span: career` heads its answer "Bam Adebayo
  in March, last 10 of 118 games of his career (2026 regular season)"; with
  no situation, "Bam Adebayo, last 10 of 640 games of his career (2026
  regular season)", and for Dwyane Wade "... of 1054 games of his career
  (2019 regular season)". The parenthesis is the season the ten rows shown
  come from, but it sits beside the career count and reads as the span of
  all 118 (which run 2018-2026: 118 played March games by Eastern date,
  re-measured on `player_game_log` x `real_games`).
- **User sees:** a heading that seems to contradict itself - a career count
  labeled with one season. The numbers are right.
- **Next step:** the template owner words the parenthesis as what it is
  ("shown: 2026") or drops it on a career span.
- **Priority note:** P4 - wording, the figures are correct.
- **GitHub:** #217

### A league-wide rows read orders ties by chance when several players share one game
- **Found:** 2026-09-24, by the compose agent re-running `k2_run_pkg.py`
  (reported to the lead; transcribed here).
- **Evidence:** a "rows over everyone" read (`compose` with
  `subject="everyone"`, e.g. "biggest triple double ever" - top N by points)
  orders by the measure and the date only; two players with the same figure
  in the same game have no tiebreaker, so repeated runs list them in either
  order. Observed as row-order flips between two otherwise identical
  `k2_run_pkg.py` runs.
- **User sees:** the same question listing tied rows in a different order
  from one run to the next - never a different set of rows or a wrong
  number.
- **Next step:** add `athlete_id` (and `event_id`) as the last ORDER BY keys
  in the relation's league reader (`player_games.league()` / `rows_sql`), the
  same tie rule the golden harness normalizes for.
- **Source:** ours.
- **GitHub:** #211

### `head_to_head`'s venue-narrowed sentence says "won the series" for a since-bounded or career span too
- **Found:** 2026-09-22, step 3, team cells (`team_record`/`head_to_head`
  honoring `since`/`game_n`/`span`).
- **Evidence:** `templates/games.py: _head_to_head_span_result` calls
  `_head_to_head_narrowed_phrase` (unchanged wording) when `venue` narrows a
  since-bounded or career meeting count, and that function always says "the
  X won the series N-M" - accurate for one season, which is what it was
  written for, but a since-bounded or ongoing rivalry has not "won" anything
  settled. The venue-LESS path added in the same change
  (`_head_to_head_span_phrase`) says "lead the all-time series" instead, on
  purpose, for exactly this reason - the two paths now disagree with each
  other over the same shape of span.
- **User sees:** "Celtics vs Knicks home games since 2020" answers "...; the
  Boston Celtics won the series 9-5" rather than "lead the series 9-5" - a
  word choice, not a wrong number.
- **Next step:** thread the same `whose`-style wording `_head_to_head_span_phrase`
  uses into `_head_to_head_narrowed_phrase`'s win/loss sentence, gated on
  `since is not None or career`, the same way its `scope` phrase already is.
- **Source:** ours.
- **GitHub:** #195

### The team-games postseason span clause is duplicated by a module boundary, not by drift
- **Found:** 2026-09-22, porting `with_without` onto `query/team_games.py`'s
  relation (step 3, C4).
- **Evidence:** `templates/common._team_span_clause` (the postseason-by-
  calendar-year clause over `tg.eastern_date`/`tg.season`) now has a second
  copy, `conditions._with_without_team_span_clause`
  (`query/conditions.py`), four lines of identical logic under a different
  name. Not drift - `conditions.py`'s own module docstring says "nothing here
  imports `templates`, so the dependency runs one way", and `templates/common.py`
  already imports FROM `conditions.py` (`_Scope`, `_game_scope`, `box_source`),
  so the reverse import would cycle. `query/team_games.py` itself has no such
  restriction (it imports only `.entities` and `nba.season`), so it is where a
  shared clause builder could live without either module reaching into the
  other. `scripts/check_duplicate_names.py` cannot see this: it is a duplicated
  CONCEPT under two names, which is exactly the shape AGENTS.md ("One concept,
  one definition") says the script is blind to.
- **User sees:** nothing yet - both copies are correct and golden-identical
  (`~/association-research/algebra-spike/step3`, 482 with_without-inclusive
  cases). The risk is a future edit to one copy (a new season-type wrinkle,
  say) landing only in the one the editor happened to be in.
- **Next step:** `conditions._team_games(scope)` still has two more callers to
  port onto the relation - `player_splits` and `streak`'s team branches
  (README_c4.md's own next-agents note) - and each will face the same import
  boundary. Once all three are ported, move the clause itself into
  `query/team_games.py` as a small public function taking `(season, season_type,
  first, phantoms)` rather than either module's own scope/span dataclass, and
  have `templates.common._team_span_clause` and every `conditions.py` copy
  delegate to it. Not done here: three call sites is still one branch's
  decision to make, not a refactor to force mid-port on work another agent may
  be doing in parallel on the same file.
- **Priority note:** P4 - no wrong answer today, a maintenance risk if the
  next two ports each add their own copy instead of reading this one first.
- **GitHub:** #193

### "Points by quarter" asks for all four at once, and every template answers one
- **Found:** 2026-09-20, finishing the quarters-and-halves work
- **Evidence:** "nba playerspoints by quarter average" reaches `other` and
  falls through. `router._period_asked` returns a single period or a single
  half, and both period templates take exactly one of those, so a question
  asking for the breakdown across all four quarters has nothing to route to -
  `_AGENT_ONLY` matches "by quarter" and sends it to `other` for want of a
  legible single period.
- **User sees:** the slow agent, for a question the shot table can answer four
  times over.
- **Next step:** low priority - one corpus question, and it is malformed
  ("playerspoints"). If it is picked up, the shape is `period_leaderboard`'s
  query grouped by period rather than filtered to one, and the answer is a
  four-column table; decide first whether it means the league's average by
  quarter or one player's, which the question does not say.
- **Priority note:** filed P4 rather than P3 because a single malformed
  question is the whole evidence, and the shape is a table nobody has asked
  for twice.
- **GitHub:** #162

### `since` reaches the metric templates only by a second season-scoping path
- **Found:** 2026-09-18, looking for the next compositional-scoping win after
  the starter/bench filter landed; **narrowed 2026-09-19** when `since` landed
  on the relation
- **Evidence:** `since` is composed once now: `_span_of` and `_condition_scope`
  build a span from that season on, and `game_log`, `player_stat` and
  `player_matchup` honor it by declaring it (`HONORED_SCOPING`,
  `templates/common.py`) - "jokic vs cade since 2022" answers their 7 meetings.
  What is left is the templates that do not sit on the player-games relation
  and read `slots.get("season")` into their own metric SQL: `leaderboard`
  ("most steals by bucks players 2010s", `run_leaderboard`) and
  `team_leaderboard` ("nba team with least playoff wins since 2022",
  `templates/teams.py`). Both still refuse `since`, and `until` is set beside
  `since` in `route()` but is in neither `HONORED_SCOPING` nor `SCOPING_SLOTS`
  (see #23). So the "three separate season-handling paths" this entry first
  counted are two: the relation's span, and the metric templates' own.
- **User sees:** the two leaderboard questions fall through; every other
  `since` question answers.
- **Next step:** give `run_leaderboard` / `run_career_leaderboard` and
  `team_leaderboard` a span argument built by `_span_of` rather than a bare
  season, so a range reaches them the way it reaches the relation, and add
  `until` to `SCOPING_SLOTS` in the same change.
- **GitHub:** #137

### A team word only names a player when a second word of that player's name is present
- **Found:** 2026-09-18, after a per-question candidate enum regressed on team
  references and a special-case list was proposed instead
- **Evidence:** the PERSON/ORG overlap between the 30 teams and 3,080 players is
  **8 words reaching 23 players** - `antonio`, `boston`, `cleveland`, `houston`,
  `magic`, `orlando`, `washington`, `york`. Small enough to enumerate, which is
  what prompted the question, but a suppression list is wrong: **four of the 23
  are active with real records** (P.J. Washington has 468 games, plus Orlando
  Robinson, TyTy Washington Jr., Brandon Boston Jr.), so "P.J. Washington vs gsw"
  would break.

  The rule that needs no list: **a colliding word names a player only when some
  OTHER word of that player's name is also in the question.** Measured over the
  261-question corpus, **13 of 13** colliding occurrences classify correctly -
  `magic vs nets last 10` and `most total career points on the houston rockets`
  read as teams, `P.J. Washington vs gsw` reads as the player because "p.j." is
  present. It survives the hard edges too: `orlando robinson vs magic` resolves
  BOTH correctly in one question, and `boston celtics vs brandon boston jr`
  reads as the player.

  Note the two different word sets this needs. A single letter may CONFIRM a
  colliding word ("p.j." confirming "washington") but must never FIND a
  candidate on its own - `AGENTS.md` records that a one-letter span makes the
  possessive left by "Jokic's" name John S. Williams.
- **User sees:** today, `magic vs nets last 10` is answered about Magic Johnson
  by the enum prototype, and the shipped pipeline resolves a player for
  `Most reb by a hawk player history`. The rule is not yet in `src/`.
- **Next step:** this belongs in `entities.py` beside `players_named_in`, which
  already enforces whole-word matching and whose own notes record that "boston"
  is Brandon Boston Jr. It generalizes something the codebase half-knew, and it
  needs no maintenance when a rookie named Memphis arrives.
- **Script:** `~/association-research/statmuse-2026-09/candidate_enum.py`,
  `team_words()` and the `teams` argument to `candidates()`; runs offline.
- **GitHub:** #136


### Router name fidelity is a per-model property, not a floor - and a smaller, faster model beats the current default on it
- **Found:** 2026-09-18, sweeping six local models over the same 60 name-bearing
  corpus questions, one model resident at a time, graded through the real
  `override_invented_players` / `find_teams` against the read-only warehouse
- **Evidence:**

  | model | warm median | clean | needed repair | fabricated |
  | --- | --- | --- | --- | --- |
  | qwen2.5:3b (current) | 3.36s | 64% | 34% | 2% |
  | **llama3.2:3b** | **2.66s** | **73%** | 25% | **2%** |
  | gemma3:4b | 3.09s | 57% | 31% | 12% |
  | phi4-mini | 5.31s | 50% | 32% | 18% |
  | qwen2.5:7b | 5.86s | 64% | 28% | 8% |
  | llama3.1:8b | 5.60s | 80% | 18% | 2% |

  Fabrication is **not** a constant across families, which an earlier two-family
  reading suggested: both llama models and the current qwen2.5:3b sit at 2%,
  while qwen2.5:7b is 8%, gemma3:4b 12% and phi4-mini 18%. What varies far more
  is the clean rate - 50% to 80%. `phi4-mini` emitted a player slot on only 28
  of 60 questions, so its row is not comparable to the others.
- **User sees:** nothing yet. `llama3.2:3b` is the same size class as the
  current default and is both faster and cleaner, so it is a candidate upgrade.
- **Settled: it is not adoptable.** `scripts/check_routing.py --model
  llama3.2:3b` scores **76/85** against the current default's **85/85**, and the
  nine failures are ordinary shapes rather than edge cases - "which team scores
  the most points per game" routed `team_stat` instead of `team_leaderboard`,
  "how did curry do against the celtics this year" routed `head_to_head`
  instead of `player_stat`, "Giannis stats by month" routed `player_stat`
  instead of `player_splits`.

  Hand-grading the 20 intent disagreements on the corpus sample independently
  reached the same verdict: **qwen2.5:3b is better on 14, llama3.2:3b on 3, 3
  are ties.** Its dominant failure is routing a player-vs-team question to
  `head_to_head` with the *player* in the `teams` slot (`tim hardaway vs nyk`,
  `keon ellis stats vs trailblazers`, `jokic vs cade since 2022`), and it puts
  question text in the `stat` slot (`stat: "P.J. Washington vs gsw"`). It also
  invented `Jalen Mathurin` (Bennedict) and `Thaddeus Portis` (Bobby), and
  produced `team: "Vancouver Grizzlies"` - a franchise dissolved in 2001 - from
  "vj edgecombe".

  `qwen3:4b` is separately disqualified on latency: a single cold call took
  **244.7s**, because it emits visible reasoning before the JSON.

  **The metric that suggested otherwise was measuring the wrong thing.** "Clean
  name rate" counts a correctly-spelled name as clean wherever it lands, so a
  name in the wrong slot scores well and is useless. Do not rank routers on name
  fidelity alone; pair it with `check_routing`'s ground truth.
- **Next step:** stay on `qwen2.5:3b`. `ROUTER_PROMPT` and the intent taxonomy
  are tuned around this model's failure modes, so a family swap trades one set
  of failures for another rather than net-improving. Re-tuning the prompt for
  another family is a real project, not a free win.
- **Raw data:** `~/association-research/statmuse-2026-09/router_*_prodgraded.jsonl`,
  shared sample `router_bench_sample.json`, runner `router_bench.py`.
- **GitHub:** #134

### A per-question candidate enum could make a fabricated name unemittable
- **Found:** 2026-09-18, testing whether the `format=` grammar could constrain
  `player` the way it already constrains `intent`
- **Evidence:** `ROUTER_SCHEMA` is compiled into a decoder grammar by ollama, so
  an intent outside the enum can never be emitted. `player` is a free string, so
  a name with no basis in the question can be. Measured offline over the
  261-question replay, building a candidate set per question from the roster
  indexed by whole word, plus the curated nickname table:
  **recall 154/157 = 98.1%** against resolved names that are genuinely on the
  roster, with an enum of **median 9, p90 39, max 70** names - small enough to
  compile per call and negligible against the 4096-token window.

  All three apparent misses are cases where the current pipeline resolves to a
  player the question never names, and the enum would refuse them:
  `jay huff game log vs Embiid` -> **Jayson Tatum**;
  `Ingram game log against the tockets` -> **Shai Gilgeous-Alexander**;
  `garland on mondays game log` -> **Bradley Beal**. So recall on correct
  resolutions is effectively total, and the misses are three wrong answers the
  constraint would prevent.
- **User sees:** nothing yet - this is feasibility only.
- **Live result, 2026-09-18:** it works, and the distortion risk inverted.
  **Zero grammar failures** - ollama 0.33.3 compiles a per-call enum of 1-70
  names without complaint - and constrained decoding is slightly **faster**
  (2.62s against 2.83s median), since a narrower grammar is less to search. The
  router is deterministic at temperature 0 (verified: identical calls give
  identical slots), so every difference is attributable to the enum.

  Other slots moved on 9 of 19 questions, but graded: **5 improvements, 1
  regression, 3 neutral.** Constraining `player` freed the decoder to get other
  slots right - `luka ft log` went `stat: fieldGoalsMade` to `freeThrowsMade`;
  `Cody Martin reb log` went `stat: none` to `rebounds`; and
  `jay huff game log vs Embiid` went from `players: ['Jayson Tatum', 'Nikola
  Jokic']` - two players the question never names - to `player: 'Jay Huff'` with
  the intent corrected to `game_log`.

  **The one regression was a bug in the candidate generator, not the concept.**
  It near-matched at edit distance 1 without excluding ordinary words, so
  **"while" reached "white"** and `barlow ... while starting` offered every
  player named White; the model picked Jahidi White. This is the exact trap
  `AGENTS.md` records for a different fuzzy matcher ("season" is one edit from
  Tari Eason), reintroduced in a file whose own docstring cites it. Fixed by
  refusing to near-match any word in the system dictionary - a user's misspelled
  name is not an ordinary word, so `achiwawa`, `cossoko`, `jayleyn`, `knepuell`,
  `dylon` and `fraymond` all still resolve.
- **Recall after that fix: 152/157 (96.8%)**, enum median 5, p90 30, max 68.
  Three of the five misses are fabrications the enum correctly refuses, so
  recall on genuinely-correct resolutions is 152/154 (98.7%). The two real
  losses are normalization - `adam's` against `adams`, `Amen` against `Amen`
  with an accent.
- **Next step:** fold the question's words with `fetch/parse.py`'s `match_key`
  before matching - it already drops diacritics and punctuation for the
  NetPoints name work, and would recover both remaining losses. Then run the
  full 261 both ways and grade **end-to-end answers**, not slots: 19 questions
  shows the mechanism works and is far too small to size the gain.
- **Script:** `~/association-research/statmuse-2026-09/candidate_enum.py`,
  runs offline with no model.
- **GitHub:** #135


### The agent can finalize having made zero tool calls, delivering its own plan as the answer
- **Found:** 2026-09-18, measuring the agent path
- **Evidence:** 2 of the 9 answers that finished made **zero** tool calls and
  returned the narration as the final answer - `stating centers vs suns` ->
  "First, I'll use `player_season_stats_deduped`... Let's start by filtering..."
  (~82s), and `myles turner vs 76ers last 5 games` -> "I will write a SQL query
  to fetch the relevant player box statistics. Let's proceed with this query."
  (~156s). `Agent._ask_inner_finalize` guards SQL-written-as-prose and
  finalize-after-a-tool-error, but not finalize-with-no-tool-call-ever.
- **User sees:** 80-155 seconds of waiting for text that reads as work in
  progress and contains no data. Worse than a timeout, because it looks like an
  answer.
- **Next step:** add the third guard beside the two existing ones in
  `_ask_inner_finalize`.
- **GitHub:** #132

### `models.py`'s model-size note is true about intent and silent about names
- **Found:** 2026-09-18, benchmarking router models on name fidelity
- **Evidence:** the comment says every model from 1.5B to 8B landed "within a
  case or two" over `check_routing.py`'s cases. True, and about *intent
  classification*, which is already ~99.6% stable run to run. Measured
  separately over 60 name-bearing corpus questions, single model resident,
  graded through the real `override_invented_players`/`find_teams`:
  **qwen2.5:3b 64% clean / 4.5% unrepairable fabrication; qwen2.5:7b 64% /
  9.4%; llama3.1:8b 80% / 3.6%.** Bigger within the same family made
  fabrication *more* common, not less (n is ~50 per model, so 4.5% vs 9.4% is
  not decisive - but it is certainly not the improvement a bigger-router
  argument needs). Warm latency: 3b 3.36s median, 7b 5.86s, llama3.1:8b 5.60s;
  cold load 33.4s / 68.5s / 68.6s.
- **User sees:** nothing directly. This exists so the model-size question is not
  re-litigated from scratch.
- **Next step:** note the measurement near that comment. The operative finding
  is that at every size and family tested, 18-34% of player slots needed the
  repair layer - the router model is not where the leverage is.
- **GitHub:** #133


### `limit` is not a scoping slot, so a template that ignores it does so silently
- **Found:** 2026-09-18, merging the StatMuse scoping branches and re-measuring
- **Evidence:** `SCOPING_SLOTS` (`templates/common.py:87`) holds `order`,
  `date`, `opponent`, `venue`, `span`, `without`, `round`, `split`, `since`,
  `below` and `situation` - **not `limit`** - so `check_scope` cannot refuse a
  template that is handed one and does nothing with it. `head_to_head` reads no
  `limit` anywhere in its body. Measured on the merged tree: "lakers vs mavs
  record last 10 home games played" arrives with
  `{'teams': [...], 'limit': 10, 'venue': 'home'}` and answers "The Los Angeles
  Lakers and the Dallas Mavericks met 2 times in the Los Angeles Lakers' home
  games of the 2026 regular season" - the venue honored, the "last 10" dropped
  without a word.
- **User sees:** a narrower answer than was asked for, with its season named
  but no sign that "last 10" was ignored. It reads as a complete answer to the
  question asked.
- **How it surfaced:** this is not new. `limit` has always been unguarded and
  `head_to_head` has always ignored it; the row used to fall through on
  `venue`, which hid it. Honoring a slot can expose a *different* slot that
  nothing was checking - the same shape the StatMuse README records for
  `tim hardaway vs nyk`, where a fixed fall-through revealed #18.
- **Next step:** decide per template, not globally. Adding `limit` to
  `SCOPING_SLOTS` would make every template that does not list it refuse, which
  is right for `head_to_head` (a limit there is a real narrowing) and wrong for
  the several templates that already read `limit` deliberately and would then
  need it added to `HONORED_SCOPING` in the same commit or start refusing
  questions they answer correctly today. Audit which templates read `limit`
  first, then move it in one change with those entries. Check `rate`, `fields`
  and `stat` for the same shape while there.
- **Priority note:** filed P4 rather than P2 because exactly one corpus row
  shows it and that row is audit-flagged as over-specific; re-rank if an audit
  of the other templates finds more.
- **A second instance, found 2026-09-18 while fixing #34's `without` rows:**
  `player_matchup`'s genuine two-player branch (`_player_matchup_answer`,
  `query/templates/games.py`) reads neither `stat` nor `fields` - the summary
  table always shows minutes/points/rebounds/assists/FG% regardless of what
  either slot asks for. Not new behavior and not touched by this session's
  fix (the one-player-and-a-team branch it now shares delegates to
  `game_log`, which DOES read `stat` via `_log_extras`, so that half is fine).
  No corpus row currently shows a `player_matchup` two-player question naming
  a specific `stat`, so this is unmeasured rather than confirmed-wrong - worth
  folding into the audit this entry already calls for.
- **Third instance, 2026-09-21 (web-session replay):** "Create a shot chart
  for steph curry's last two games of the regular season" draws the whole 2026
  season (374/803); `shot_chart` resolves `order` to one event and has no
  notion of `limit` > 1. See also the `single_game_high`/`team` entry under P1.
- **GitHub:** #125


### `stat` is the same unguarded shape as `limit`, and the enum-required slot makes it worse
- **Found:** 2026-09-18, while routing "game score" (#114) and checking
  whether the required-slot mechanism that fixed it could hide the same
  problem in reverse
- **Evidence:** of 161 corpus questions (the 261-query StatMuse feed) carrying
  a `stat` slot, 94 carry one the question does not support and **64 carry a
  value not in `ROUTER_SCHEMA`'s enum at all** - `'vs Portland Trail Blazers'`,
  `'made_rebounds'`, `'games_played_against'`, `'per_game'`, `'playoffs'`,
  `'none'`, `'all'`. `stat` is not in `SCOPING_SLOTS`
  (`templates/common.py:87`) any more than `limit` was, so `check_scope`
  cannot refuse a template that is handed one of these and ignores it.
- **User sees:** nothing today - harmless only because every template that
  currently reads `stat` for these intents either validates it against a
  whitelist before using it (`player_stat`'s `PLAYER_STAT_COLUMNS` /
  `ADVANCED_STATS`, `leaderboard`'s `resolve_metric`) or ignores it outright.
  The risk is latent: a future template, or a future stat lookup added to an
  existing one, that trusts `stat` without a whitelist check would substitute
  silently the same way `limit` did for `head_to_head` - "correct data, wrong
  question, no sign anything was dropped" is this project's own definition of
  a P1.
- **Next step:** an audit, not a patch - this entry is explicitly out of scope
  for #114's fix. Enumerate every template that reads `slots.get("stat")` and
  confirm each validates against an explicit table before using the value
  (the same discipline `resolve_metric` and `ADVANCED_STATS` already apply);
  flag any that does not. Given how large the unsupported-value population is
  (94 of 161, 64 outside the enum), consider whether `stat` belongs in
  `SCOPING_SLOTS` for the templates that do not already self-guard, the same
  decision `limit` above is waiting on.
- **Priority note:** filed P4 because it is unmeasured harm today, not a
  wrong answer - re-rank to P1/P2 if the audit finds a template that trusts
  `stat` unchecked.
- **GitHub:** #126


### Plus/minus can be neither ranked nor looked up, though the data is complete
- **Found:** 2026-09-18, while making the computed advanced stats lookup-able
- **Evidence:** "nba leaders in plus minus in 25-26" routes to `leaderboard`
  with `stat: 'plus_minus'` - the router names it correctly - and falls through
  with "no leaderboard metric for stat 'plus_minus'". The data is there and is
  complete where it matters: **0 of 860,230 `player_box_stats` rows with real
  minutes have a NULL `plusMinus`** (measured read-only against
  `nba.duckdb`, 2026-09-18), confirming `DATA.md`'s corrected note that the
  NULLs are a strict subset of the did-not-play rows. Summed for 2026 it gives
  a sensible board: Gilgeous-Alexander +788, Holmgren +678, Wembanyama +664.
- **User sees:** a fall-through to the agent on a stat people ask about often.
- **Next step:** unlike true shooting, this has no season-level table to rank -
  `player_season_stats` has no `plusMinus` column, and every
  `LeaderboardMetric` names a pre-aggregated table. It needs a derived season
  aggregate in `fetch/warehouse.py` (summed from `player_box_stats` over rows
  with real minutes, so the did-not-play rows cannot pull it toward zero), a
  `COVERAGE` entry, and then a metric. That is a warehouse change and wants a
  `data load` after it. **Do not** use `team_season_stats.plusMinus`, which
  `DATA.md` records as an ESPN placeholder (-1.0 on 828 of 1,503 rows).
- **Source:** DATA.md, "NULL minutes mean \"did not appear\", and NULL
  `plusMinus` is a subset of them"
- **GitHub:** #115

### No metric on `player_season_advanced_stats` can have a career ranking
- **Found:** 2026-09-18, while adding `avg_game_score` as a leaderboard metric
- **Evidence:** `leaderboard.py:490` builds a weighted career value as
  `SUM(t.{numerator} * t.gamesPlayed) / NULLIF(SUM(t.gamesPlayed) ...)`, and
  `t.gamesPlayed` is `player_season_stats`' spelling of that column. The
  advanced table calls it `games_played`, so a `CareerAggregate` on any metric
  reading it would generate SQL against a column that does not exist. The
  `LeaderboardMetric` docstring explains the missing careers for usage and true
  shooting as "they need team context the season rows do not carry", which is a
  real argument for usage and not the reason the code could not do it anyway.
- **User sees:** nothing today - `ts_pct`, `efg_pct`, `usage_pct` and
  `avg_game_score` all have `career=None`, so no career ranking is offered
  rather than offered and broken. It is a ceiling, not a bug.
- **Next step:** if a career game-score or true-shooting ranking is ever
  wanted, take the games column from the metric rather than hardcoding it
  (`LeaderboardMetric.min_sample_column` already names it for both tables), and
  correct the docstring's stated reason at the same time.
- **GitHub:** #116

### A career advanced rate counts a season ESPN served almost, but not entirely, empty
- **Found:** 2026-09-18, while adding the career true-shooting figure
- **Evidence:** the new career answer excludes seasons whose rate is NULL and
  says how many it left out, which covers the fully-empty 2013-2018
  team-seasons. A season ESPN served *partly* is not caught: Jimmy Butler's
  2016 has 67 games played and **18.32 true-shooting attempts** at .710, so it
  counts as a season that is present, contributes 18 of his 8,606 career
  attempts, and adds its 67 games to the "in 641 games" the answer prints.
- **User sees:** a career games count that is slightly overstated - 641 where
  the rate really rests on about 574. The rate itself is unaffected to three
  decimals, because it is weighted by attempts and 18 of 8,606 is noise.
- **Next step:** decide whether a per-season attempt floor belongs inside a
  career sum at all. It probably reads better as a second clause on the same
  sentence ("and 1 more is nearly empty") than as a silent exclusion, since
  excluding it would make the games count right and the attempt count wrong.
- **GitHub:** #117

### `ISSUES.md` has no `## P3: refusal or gap` heading, so P2 and P3 entries are merged
- **Found:** 2026-09-18, looking for where to file two new refusal/gap
  findings and finding no P3 section to put them in
- **Evidence:** the priority definitions at the top of the file list four
  tiers, but `grep -n "^## P" ISSUES.md` finds only `## P1`, `## P2` and
  `## P4` - every P3-shaped entry ("Each narrowing the router has no slot for
  needs its own regex", "Two players against one team has no template", the
  two filed alongside them today) sits under `## P2: misleading or
  incomplete` instead, undifferentiated from actual P2s.
- **User sees:** nothing - this is about the file's own readability, not an
  answer.
- **Next step:** add the missing `## P3: refusal or gap` heading in the right
  place and move the P3-shaped entries currently under `## P2` beneath it.
  Left undone here since it touches many entries other agents may be editing
  concurrently and risks a merge conflict far out of proportion to the fix.
- **GitHub:** #127

### The header status line still states coverage as a single misleading range, beside a correct one
- **Found:** 2026-09-18, while fixing #71 (the web page never says what data
  the warehouse actually holds)
- **Evidence:** #71's fix added `GET /api/coverage` and a row of tier pills
  under the header (`web/app.py`, `web/static/index.html`) that correctly say
  "box score: 1994-2026", "+ play-by-play: 2002-2026 (partial: 2002, 2003)"
  and "+ NetPoints: 2019-2026" against the live warehouse. The header's own
  status line, built from `/api/health`'s `_warehouse_seasons`, is untouched
  and still reads "43,353 games, 1988-2026" - the exact wrong-cause phrasing
  #71 was filed about, now sitting one line above its own correction. #71's
  fix was scoped to adding the tiers, not to rewording the line that used to
  be the page's only coverage claim; #70 (the connection indicator) already
  owns making that status line live and is the natural place to also soften
  its wording now that the tiered breakdown is right below it.
- **User sees:** two claims about the same warehouse, one general and
  technically true ("43,353 games, 1988-2026"), one specific and correct (the
  tier pills) - a reader who does not look at both could still walk away with
  the misleading one, though the correct one is now on the page for anyone who
  reads past the header.
- **Next step:** when #70 makes the status line live, consider dropping its
  season range (the tiers already say it, per table) and keeping just the
  game count and liveness state - or word it as "raw row count" rather than a
  season span, so it stops looking like a coverage claim at all.
- **GitHub:** #106

### `get_collection`'s declared-vs-fetched warning depends on page one carrying a `count`
- **Found:** 2026-09-17, fixing #90 (the first-page-goes-quiet bug above).
- **Evidence:** `get_collection` (`fetch/client.py`) now warns when page one
  itself cannot be read (non-dict, or a dict with no `items` list), and a
  later page's failure is covered by the existing "collection %s declared %d
  items, fetched %d" check - but only because `expected` was set from page
  one's `count` field. If page one is a well-formed paged object that happens
  to omit `count` (or has it as something other than an `int`), `expected`
  stays `None` for the whole read, and a later page failing the same way as
  the original bug - non-dict, or dict-without-items - ends the loop with
  nothing logged, the same silent short read #90 was about.
- **User sees:** nothing, same as #90 did - a table quietly short. Unmeasured
  whether this actually happens: every ESPN core-API collection response seen
  in this codebase's fixtures and tests carries `count`, so this is a gap in
  the design rather than an observed failure.
- **Next step:** either warn on any non-first-page read failure directly
  (dropping the `expected is not None` guard on that specific log line), or
  assert page one's response always carries an integer `count` and warn if it
  does not. Small enough to fold into whichever change next touches
  `get_collection`.
- **GitHub:** #107

### The team-splits caveat uses one bit to stand for several columns
- **Found:** 2026-09-17, fixing "Vancouver 1996 has an empty TEAM box, not an
  empty player box" (#67).
  **Rewritten the same day, after both fixes landed: the P2 it was filed as
  does not exist.** It was written against `_TEAM_LINE`'s rebounds column
  reading `AVG(t.totalRebounds)`, and that column now reads
  `AVG(t.offensiveRebounds + t.defensiveRebounds)` - which the rebuild fills -
  so the caveat does not understate anything. Two concurrent changes, each
  sound alone, and the risk was in the pair.
- **Evidence:** `_player_splits_team` (`query/templates/splits.py`) says
  "Rebounds, assists, 3-pointers and FG% are missing from N of those games'
  box scores" from a single count of `fieldGoalsAttempted IS NULL` - one bit
  standing in for "this row has nothing", covering four columns that could in
  principle be short in different games. Measured against the rebuilt
  warehouse (2026-09-17, after the `data load`): for the Grizzlies' own 1996
  games the `fieldGoalsAttempted IS NULL` count and the
  `offensiveRebounds`/`defensiveRebounds` NULL count are **both 4** of 82, and
  league-wide over 1996 and 2000 both are **20** - they agree exactly, because
  this fault fills every rebuildable column together or none of them.
  `totalRebounds` is still NULL on all 82, and nothing user-facing reads it:
  the only remaining `totalRebounds` reads are on `player_season_stats`
  (`query/metrics.py`), where a player's rebounds carry no team bucket.
- **User sees:** nothing today - verified, not assumed.
- **Next step:** none required. If a future repair ever fills one of those
  four columns without the others, this caveat will be wrong and silent, so
  the column-specific test is worth writing then: count each column's NULLs
  separately and word the sentence around whichever are actually short, the
  way `_box_missing`/`_empty_box_scores` already separate "no box score at
  all" from "box score present but a stat is NULL".
- **Source:** DATA.md, "Vancouver 1996 is an empty TEAM box, not an empty
  player box"
- **GitHub:** #102

### A single-game-high list cut at a tie picks the players at random
- **Found:** 2026-09-17, by the templates complexity refactor's golden
  comparison; re-measured the same day
- **Evidence:** `single_game_high` orders by the stat descending, then
  `game_date`, and nothing breaks a tie on the same date. Eight identical calls
  of `{'stat': 'turnovers', 'season': 1996, 'limit': 10}` against the main
  warehouse gave two answers (5 and 3 times): the tenth name is Latrell
  Sprewell or Vernon Maxwell, both with 9 on 1996-04-06. With `stat: fouls,
  season: 2015` the order of Andre Drummond and Dwight Howard (6 each,
  2014-10-29) swaps the same way. DuckDB's parallel execution returns tied rows
  in no fixed order.
- **User sees:** the same question listing a different last player, or the
  same players in a different order, from one ask to the next - and no sign
  that more players tie at the cutoff. Arguably P2 (incomplete without saying
  so); ranked here because every name shown is correct.
- **Next step:** add a deterministic tiebreak (`athlete_id`, or the display
  name) to the ORDER BY, and consider saying "N more tied" when the limit cuts
  through a tie. A behavior change, so it was kept out of the refactor.
- **GitHub:** #99

### Advanced-stat aggregates are not bit-reproducible between runs
- **Found:** 2026-09-17, by the complexity refactor's golden comparison of
  `run_leaderboard`; re-measured the same day
- **Evidence:** the same query, `SELECT athlete_id, usage_pct, ts_pct FROM
  player_season_advanced_stats WHERE season=2024 AND season_type=2`, run on four
  fresh read-only connections to the main warehouse: 588 rows each, and against
  the first run 143, 185 and 184 rows differ, by at most 1.07e-14 (e.g.
  `10.60335677967314` vs `10.603356779673142`). The view is computed at query
  time (`fetch/advanced_stats.py`), and DuckDB's parallel `SUM` combines partial
  sums in a different order run to run.
- **User sees:** nothing - every value is printed to one or two decimals. A
  leaderboard ordering could in principle flip between two players tied to 14
  digits.
- **Next step:** none needed for answers. Anything comparing results across
  runs (a golden test, a cache check) should round, or the view could round
  `usage_pct`/`ts_pct`/`efg_pct` to a sane precision at build time.
- **GitHub:** #100

### `games.date` is a VARCHAR, and the agent is taught only part of how to filter it
- **Found:** 2026-09-16 during the query-set audit; **corrected and re-ranked
  P3 -> P4 the same day** by the issues audit
- **Evidence:** the column holds `2021-10-23T22:00Z`. The first version of this
  entry said only `strptime` works, and that was wrong: `CAST(date AS
  TIMESTAMP)` fails with `invalid timestamp field format`, but `CAST(date AS
  DATE)` works (`= DATE '2026-04-12'` -> 7 rows; `>= DATE '2020-01-26'` -> 8,202,
  the same as `strptime`), and so does a plain string comparison (`date >
  '2020-01-26'` -> 8,202). `KNOWLEDGE_BASE`'s "Filtering by an exact calendar
  date" (`query/prompt.py:330-339`) already teaches `LIKE 'YYYY-MM-DD%'` and
  `CAST(date AS DATE)`, but is selected only on date and month keywords and
  never shows the range form.
- **User sees:** nothing directly - at worst a recoverable agent error on its
  first try at a date range.
- **Next step:** add the range form to that knowledge entry, which is cheaper
  than a load-time column. Every date the project PRINTS goes through
  `season.eastern_date`, so this is about agent SQL only.
- **GitHub:** #98


### The 2026 regular-season power index snapshot carries no BPI rating for any team
- **Found:** 2026-09-18, while fixing #88 below. **The caveat half was fixed
  the same day; what remains is the missing data itself.**
- **Evidence:** measured read-only against the live warehouse: every
  `(season, season_type)` group `team_power_index` holds except one has zero
  NULL `bpi`; `season=2026, season_type=2` (stamped `2026-04-13T09:43Z`) has
  all 30 team rows NULL in `bpi`, `bpioffense` and `bpidefense`, while the same
  rows' win/loss, projected record, playoff/title chances and
  strength-of-schedule columns are all populated. See DATA.md, "ESPN's power
  index is a paged collection, and holds all 30 teams", for the full query and
  numbers.
- **Fixed 2026-09-18 - the omission is no longer silent.**
  `_team_outlook_bpi_line` used to return None on a NULL rating, dropping the
  line; it now says "no BPI rating in this snapshot - ESPN left it empty for
  all 30 teams, though the record and projections below are its own". That
  matters more here than it would elsewhere because the power index IS this
  answer's headline: a reader got a record, a projection and chances with no
  sign that the number they asked for was absent. The "also has" line already
  names the season's other snapshots, which DO carry ratings, so the sentence
  points at where the number is. Pinned by
  `test_a_snapshot_with_no_rating_says_so_rather_than_dropping_the_line` and
  one perturbation watched to fail.
- **User sees:** a 2026 regular-season outlook that names the missing rating
  and prints everything else ESPN does serve on those rows. Before #88 the
  same question read the 2026 play-in snapshot, which has a rating - so the
  answer is still shorter than it was, in exchange for reading the snapshot
  actually asked about, and it now says so.
- **Next step:** check whether a refetch of 2026 fills in `bpi`, the way
  `scripts/backfill_power_index.py` fixed the paging fault. If ESPN serves a
  rating today, this closes; if it does not, the caveat is the answer and this
  becomes a `DATA.md` fact alone.
- **Priority:** P4 - the omission is stated, so nothing is misleading; what is
  left is one season's missing column and an unrun refetch.
- **GitHub:** #108

### The "postseason copy" rule is written twice
- **Found:** 2026-09-15, issues audit (P4 data/query auditor)
- **Evidence:** the rule that drops a postseason line ESPN copied from the
  regular season exists twice, in different words:
  `fetch/warehouse.py:239` (the `player_season_stats_deduped` view: more than 28
  games, or games+points equal to that season's regular-season line on any team)
  and `query/leaderboard.py:186` `not_a_postseason_copy` (games plus the value
  columns, on the same team). They agree today - each drops 436 of 7,941 rows -
  but nothing keeps them in step. (Both comments now give the re-measured
  figure, 436 of 7,941 rows / 340 player-seasons, fixed 2026-09-16 with #43;
  only the duplication remains.)
- **User sees:** nothing today. It is the same hand-maintained-pair shape as
  #83, with the added trap that the two spellings could diverge silently.
- **Next step:** export one helper and call it from both, the way #83 proposes
  for the traded-player dedup.
- **GitHub:** #93

### `MAX_LIMIT` is 100 in one module and 50 in another
- **Found:** 2026-09-15, in the cross-module constant scan written after #6/#9
- **Evidence:** `query/leaderboard.py:38` declares `MAX_LIMIT = 100` (the cap on
  a model-supplied limit on the agent path); `query/templates/common.py` declares
  `MAX_LIMIT = 50` (what `_clamp_limit` clamps a template to). Same name, two
  different facts, neither importing the other.
- **User sees:** nothing wrong today - each is used only in its own module, and
  both caps are deliberate. The risk is a reader or an agent who learns one and
  applies it to the other, or a future refactor that "deduplicates" them into
  whichever value it happened to see first.
- **Next step:** rename by what each governs - `AGENT_MAX_LIMIT` and
  `TEMPLATE_MAX_LIMIT` - rather than unifying them, since the two caps are
  answering different questions. Then drop the name from `ALLOWED` in
  `scripts/check_duplicate_names.py`.
- **Priority note:** P4 because no answer is wrong; it is a trap laid for the
  next change, not a fault in this one.
- **GitHub:** #81

### One rule, two hand-maintained copies: the traded-player dedup
- **Found:** 2026-09-15, while fixing #9
- **Evidence:** "prefer the combined row over the per-team stints" is written
  as SQL in `fetch/warehouse.py:259` (the `player_season_stats_deduped` view)
  and twice in `query/leaderboard.py` (lines 300 and 324, the `dedup_traded`
  QUALIFY). Fixing #9 had to touch both, and a fix that touched only one would
  have left the leaderboard reading the broken row while the deduped view was
  correct - green tests either way.
- **User sees:** nothing now that both are repaired at load time. The coupling
  remains: a third reader of `player_season_stats` would need the same rule
  written a fourth time.
- **Next step:** export the QUALIFY fragment from one module the way
  `not_a_postseason_copy()` already exports its own rule from
  `query/leaderboard.py`, and have both call it.
- **GitHub:** #83

### `pointsInPaint` is -1 for every team-game before 2009
- **Found:** 2026-09-14, while fixing #8
- **Evidence:** **39,157** `team_box_stats` rows hold `pointsInPaint = -1` -
  every non-empty row from 1993 to 2008 (2,358 in 1994, 2,632 in 2008); 2018's
  are no longer among them, since `team_box_repair` now NULLs those (this entry
  originally counted 41,417, including 2018's 2,280). `fastBreakPoints` and
  `turnoverPoints` never carry the sentinel. **`team_season_stats.pointsInPaint`
  is also -1.0 in every team-season from 1994 to 2008** - re-measured, this is
  not the same gap marked differently: the entry originally said
  `team_season_stats` uses 0 for the same era, which is wrong (only its
  `fastBreakPoints` is 0). `query/team_metrics.py:24` and the user-visible
  refusal `_PAINT_REASON` (`:98`) both still repeat that error.
- **User sees:** nothing today — no template reads the column. Agent SQL asking
  for points in the paint in an old season gets -1 a game, which reads as a
  number rather than as a gap.
- **Next step:** NULL the sentinel at load, beside the 2018 clearing
  `fetch/repairs/team_box_repair.py` already does. One predicate, `pointsInPaint = -1`,
  and no season needs naming.
- **Source:** DATA.md, "`pointsInPaint` is -1 before 2009, and two lead columns exist only in 2026"
- **GitHub:** #78

### "...against the celtics last season" is answered as a game log
- **Found:** 2026-09-11, while making `opponent` refuse or narrow
- **Evidence:** "how did steph curry do against the celtics last season"
  routed to `game_log` (1 run) and listed his 2 games, not his averages over
  them. The trace logs the intent after `route()` rewrites it. `route()` sends a
  `player_matchup` naming a team to `game_log` whenever `_GAMES_WORDS` matches,
  and it matches the "last" in "last season" (checked offline). Whether the
  model chose `game_log` itself was not separated.
- **User sees:** the right games, as a list rather than a line. The same
  question with "this year" answers with the line.
- **Next step:** log the raw router output for the question when ollama is
  free. If it is the matchup rule, stop "last season" counting as "last N
  games".
- **GitHub:** #35

### A player's single qualifying game reads "1 games"
- **Found:** 2026-09-11, while narrowing `threshold_count` by season
- **Evidence:** `templates._phrase_threshold_count` builds the named-player
  sentence as `f"{player} had {games} {label} {when}."` with `label` always
  "games with ...", so one game prints "Aay Jones had 1 games with 30+ points in
  the 2026 regular season." (seen in a test fixture). The league-wide sentences
  are not affected.
- **User sees:** a plural typo in "how many 40-point games did Brunson have
  this season" whenever the answer is one.
- **Next step:** say "1 game with" when `games == 1`, and add the case to
  `test_answer_for_a_single_named_player`.
- **GitHub:** #36

### A warehouse built before a view change is not detected
- **Found:** 2026-09-11, while qualifying true shooting and eFG% (`f66e1f1`)
- **Evidence:** a view's SQL is stored in the warehouse file. Code that reads a
  column the stored view lacks gets a Binder error; for `ts_pct`/`efg_pct`, the
  template then falls through to the agent. This was confirmed against a real
  pre-change warehouse. The 2026-09-11 data load at `3d3c8c6` brought the
  current warehouse up to date.
- **User sees:** after any view change and before the next `data load`, a slow
  fall-through, with nothing saying a load would fix it.
- **Next step:** in `data check` or at startup, compare the stored views' columns
  against what the code reads. The backfill rule in `AGENTS.md` ("Working on the
  fetch path") is the process half of this.
- **Re-checked 2026-09-16, unchanged - reassessed as bigger than a P4, needs
  an owner decision on approach.** Confirmed against the main warehouse
  read-only that it is current (`player_game_log` already has `team_abbr`/
  `opponent_abbr` from the same-day franchise-naming change), so there is
  nothing to reproduce right now, but the underlying gap is real. Two ways to
  build "the stored view against what the code reads", and both cost more
  than this priority: (1) compare each view's *stored* SQL text
  (`duckdb_views()`) against what today's code would emit - correct and
  self-maintaining, but the three builders (`_build_views`,
  `advanced_stats.build_views`, `reconstructed_box.build_views`) currently
  only ever *execute* their `CREATE OR REPLACE VIEW` text, so this needs
  refactoring each to also hand back the SQL string unexecuted, which touches
  the core build path #51/#66 just changed; (2) hand-maintain an expected
  column list per view (the `COVERAGE`-table pattern) - smaller, but a second
  place the columns are declared, which is exactly the "one concept, one
  definition" shape this project already tries to avoid (`AGENTS.md`,
  "Saying what you measured"). Neither is a small mechanical fix; left open
  for the owner to pick a direction.
- **GitHub:** #38

### The `:rtype:` shim in `docs/conf.py` waits on dropping Python 3.10
- **Found:** 2026-09-11, fixing the literal `:rtype:` lines
- **Evidence:** `docs/conf.py` backports sphinx-autodoc-typehints 3.2.0's
  placement guard over the locked 3.0.1. 3.2.0 needs Sphinx 8.2 and Python
  3.11, and `requires-python` is `>=3.10`. The shim is gated on the installed
  version, so an upgrade makes it inert rather than wrong.
- **User sees:** nothing.
- **Next step:** when 3.10 is dropped, upgrade and delete
  `_rtype_insert_index` in the same commit. Check the rendered pages after the
  move from Sphinx 8.1.3 to 8.2; nobody has.
- **Re-checked 2026-09-16:** unchanged - `uv.lock` holds
  sphinx-autodoc-typehints 3.0.1 and Sphinx 8.1.3, `requires-python` is still
  `>=3.10`. Dropping 3.10 is the owner's call, so nothing to do here yet. The
  docs gate now checks the rendered pages for the literal markup
  (`scripts/check_docs_markup.py`), so the upgrade, when it comes, is verified
  by the gate rather than by somebody remembering to look.
- **GitHub:** #42

### Broad `except duckdb.Error` in `_single_game_netpoints`
- **Found:** 2026-09-11, repo audit
- **Evidence:** `_single_game_netpoints` (`query/templates/netpoints.py`) catches
  every DuckDB error. `fingerprint.py` already narrowed the same pattern to the
  missing-table error.
- **User sees:** a SQL bug reported as "unavailable", then a slow fall-through.
- **Next step:** catch `duckdb.CatalogException` only.
- **Re-checked 2026-09-15:** the pattern occurs twice. The second is
  `query/templates/players.py`, in `_compare_netpoints`, which catches `duckdb.Error`
  and returns `{}` - so a SQL bug there makes the NetPoints rows silently
  disappear from a comparison.
- **GitHub:** #44

### Postseason shooting floors are scaled, not calibrated
- **Found:** 2026-09-11, while qualifying true shooting and eFG% (`f66e1f1`)
- **Evidence:** `ts_pct` 67 and `efg_pct` 59 (`query/metrics.py:221` and `:231`,
  `postseason_min_sample=`) are the season floors times 10/82. No published
  postseason list applies a qualifier (StatMuse's 2025 playoff leader shot
  150% on two attempts), so there was nothing to check them against. They
  leave 81-92 qualified players per postseason in 2025 and 2026.
- **User sees:** a stated but uncalibrated postseason qualifier.
- **Next step:** none until a published postseason rule is found.
- **Re-checked 2026-09-15: a published postseason rule now exists.**
  Basketball-Reference's qualifier page loads (see #46) and gives "Playoffs,
  Season 50 TSA". Ours is 67. At 50 the postseason pool would be 94 players in
  2025 and 102 in 2026, against 81 and 90 today.
- **GitHub:** #45

### Basketball-Reference's qualifying rule was never read directly
- **Found:** 2026-09-11, while qualifying true shooting and eFG% (`f66e1f1`)
- **Evidence:** Basketball-Reference answers 403 to automated fetches, including
  `/about/rate_stat_req.html`. The floors were checked against StatMuse (725
  points; 300 made field goals).
- **User sees:** nothing.
- **Next step:** if a modern true-shooting-attempts figure is published there,
  compare it with 550.
- **Re-checked 2026-09-15: the page is readable, and it disagrees with us.**
  One request with a browser User-Agent to `/about/rate_stat_req.html` returns
  200. It publishes TS%: "2021-22 to present NBA 500 TSA", **prorated** in short
  seasons ("on pace for 500 TSA; stats prorated to 82-game season"), and FG%:
  300 FG. No eFG% rule. Our TS% floor is 550; at 500 the 2025 pool is 203
  players against 183. The proration bears directly on #13.
- **GitHub:** #46

### Name narrowing counts a row with no minutes as a game played
- **Found:** 2026-09-11, season-narrowing branch
- **Evidence:** a candidate survives `entities.narrow_to_available` with any row
  in the table for the season, and `player_game_log`/`player_box_stats` carry
  rows with no minutes. JamesOn Curry has 84 such game-log rows: 82 with Chicago
  in 2007-08 and 2 with the Clippers in 2009-10. He has one season line (2010,
  one game, no points).
- **User sees:** a clarification that also names a player who only sat on the
  bench that season. It errs toward asking, never toward a wrong player.
- **Next step:** measure how many asks it widens. If many, narrow the
  box-score tables on minutes, as `conditions._played` does.
- **Re-checked 2026-09-15: the next step below carries a trap.** 440
  player-seasons have box rows but no played row, and **158 of them are Bulls
  and Pelicans players from 2013-2018** who did play - narrowing on `_played`
  would eliminate them. 131 of the 440 share a surname with a player who did
  play that season. (`narrow_to_available` is `entities.py:1101`.) **Re-checked
  again 2026-09-16: the count moves with the definition** - collapsing
  `season_type` (as above) gives 440; counting `player_box_stats` rows per
  `(athlete, season, season_type)` instead gives 449; the exact figure depends
  on which is meant, so read "440" as one measurement rather than the only
  correct one.
- **GitHub:** #47

### `_no_games` can still say "did not play" of a game with no box score
- **Found:** 2026-09-17/18, fixing #72 (`_no_narrowed_games` naming the wrong
  missing fact for `game_log`/`player_stat`)
- **Evidence:** `_no_games` (`query/templates/common.py:1081`, used by
  `player_splits`, `with_without`, `record_when`, `player_matchup` and
  `streak` in `splits.py`/`games.py` when their `box_source()`-aware main
  query finds zero played games) reads raw `player_box_stats` directly rather
  than through `box_source()`. That is the right table for a game the player
  genuinely has no row for, but wrong for the population #72 fixed elsewhere:
  a row that exists, is not `did_not_play`, and has no minutes because ESPN
  served the whole team's box score empty. `_no_games` would call that "was
  listed in N box scores ... but did not play in any of them" - the same
  wrong-cause shape #72 fixed, just via the "did not play" sentence instead of
  "no games found".
  - **Measured against `/home/jeff/code/association/nba.duckdb`:** restricted
    to the real empty-TEAM-box population (every player's line NULL for that
    event/season, matching `_empty_box_scores`'s own definition - 21,204
    player rows), 38 of them have no `reconstructed` counterpart in
    `player_box_stats_filled` either, so even the rebuild-aware main query
    cannot find them as played. (An unrestricted count that also picks up the
    ~10,000-a-season 2006-2012 "appearances nobody made" rows - which are
    correctly "did not play", not an ESPN empty box score - is 62,058; that is
    the wrong population and should not be repeated as this finding's size.)
  - The 38 are one to two rows per affected player-season, scattered across
    2013, 2015 and mostly 2016. Not checked: whether any of the five templates
    above ever narrows a real question down to a span consisting ENTIRELY of
    one of these 38 rows and nothing else - a player's season otherwise has
    many real or rebuild-covered games, so `_no_games` firing at all over one
    of these needs a further narrowing (a single game, a specific opponent, a
    `with_without` split) that happens to land exactly there. No such question
    was tried live.
- **User sees:** a possible "X was listed in N box scores ... but did not play
  in any of them", stated as fact, about a game where he may well have played
  and ESPN simply never published his line. Not confirmed against a real
  question - the population is real, whether it is ever reached is not.
- **Next step:** thread `box_source(con)` through `_no_games` (it already
  reaches every call site through `scope`/`con`) so its count reads the same
  table the main query did, and give it `_no_narrowed_games`'s two-fact split
  - genuinely no row, vs. a row with an empty box score - rather than
  collapsing both into "did not play". Add a test with an orphaned row (no
  `reconstructed` counterpart) as the fixture, the same shape `_all_box_scores_empty`
  uses in `test_templates.py`.
- **Source:** DATA.md, "Every Chicago and New Orleans game from 2013 to 2018 has an empty box score"
- **GitHub:** #109

### Clarifications can name twenty players
- **Found:** 2026-09-11, season-narrowing branch
- **Evidence:** `Ambiguous.active` (`entities.py:935`) makes a clarification
  name every candidate from the season asked about (`entities.clarification`,
  `entities.py:957`). The surname with the most players in one season is
  Williams: 15 in 1998 and 1999, 14 in 2026. "Will" names 18 players in 2026
  and 20 in 1998.
- **User sees:** a long "did you mean" sentence. Whether it reads acceptably in
  the CLI and on the web page was not checked.
- **Next step:** look at a 20-name clarification on the web page.
- **GitHub:** #48

### A failed warehouse build leaves no marker
- **Found:** 2026-09-08 (reported)
- **Evidence:** each table load is its own statement, so an out-of-memory kill
  leaves the earlier tables replaced and the rest at their old contents
  (`AGENTS.md`, fetch path).
- **User sees:** answers from a half-updated warehouse, with nothing saying so.
- **Next step:** record a build-complete marker, and have `data check` report a
  build that did not finish.
- **Re-checked 2026-09-15: broader than filed.** Three in-place rewrites now
  run after the table loads - `team_box_repair`, `season_totals_repair` and
  `real_games` - each its own `CREATE OR REPLACE TABLE`. A build killed between
  a load and its repair leaves an unrepaired `team_box_stats` or
  `player_season_stats` that looks perfectly normal.
- **Fixed 2026-09-16 for a full rebuild only.** `fetch/warehouse._build_full`
  now loads every table plus all three repairs and every view into
  `<db_path>.building`, and only replaces `db_path` once all of it succeeds -
  covering the load AND the repairs the note above found missing, since both
  happen before the swap. An interrupted full build leaves the existing
  warehouse completely untouched, and the leftover `.building` file is the
  marker: the next full build logs it and replaces it. Watched fail by
  monkeypatching a mid-build raise and asserting the warehouse file's bytes
  are unchanged (`tests/fetch/test_warehouse.py`).
- **Still open: a partial `--tables` reload writes in place, unprotected.**
  `data pull`'s incremental path (after the first pull) and all three
  `scripts/backfill_*.py` always call `warehouse.build` with an explicit
  `tables=` subset, which depends on tables already in `db_path` that it is
  not reloading and so cannot go through the temp-file swap without copying
  the whole file first. A kill between a partial load and its repair still
  leaves an unrepaired table looking normal. `data check` still does not read
  the warehouse at all (only the Parquet tree), so "have `data check` report
  a build that did not finish" is also still open for both paths - that half
  needs an owner decision: whether `data check` should gain a warehouse
  dependency it deliberately does not have today.
- **GitHub:** #51

### The agent can return an empty answer
- **Found:** 2026-09-08, pre-existing in 1.6.0 (reported, not re-verified)
- **Evidence:** seen in session notes and not reproduced since.
- **User sees:** a blank answer after a long wait.
- **Next step:** reproduce it, then have the agent loop treat an empty final
  message as a failure.
- **GitHub:** #52

### Columns that look wrong but that nothing reads
- **Found:** 2026-09-11, template and shot work; `dnp_reason` widened while
  making `opponent` refuse or narrow
- **Evidence:**
  - `dnp_reason` is set on 382,435 box rows where the player played, about a
    third of `player_box_stats`: 382,378 of them "COACH'S DECISION", at 22.7
    minutes on average, in every season from 2013 to 2026 (24,000-28,600 a
    season). 13,160 of 2026's are starters. Only `fetch/parse.py` touches the
    column. `did_not_play` is the field that says whether a player sat.
  - `plusMinus` is NULL on 14.3% of box rows: a subset of the rows with NULL
    minutes, not the same set. 83,224 rows have no minutes but a real
    plus-minus.
  - `team_season_stats.plusMinus` is a -1.0 placeholder.
  - `largestLead` is filled on 47,480 of 83,281 non-empty team box rows
    (`fieldGoalsAttempted IS NOT NULL`; this read "83,261" until 2026-09-16),
    and `leadChanges` on 2,018.
  - `net_points_team` holds 2026 only, which is inherent to the source.
- **User sees:** nothing today. Any template that starts reading these would.
- **Next step:** measure each one before a template reads it.
- **Source:** DATA.md, "`dnp_reason` is set on players who played"
- **Re-checked 2026-09-15:** figures reproduce, two details changed. Player
  `plusMinus` **is** read now (the game log's "+/-" column - three sites,
  `query/templates/games.py`), and `team_season_stats.plusMinus` is -1 in
  828 rows (2009 on) and NULL in 675 before that, not "-1 in every season" as
  `team_metrics.py:25` says.
- **GitHub:** #53

### Team box scores disagree slightly with player-box sums in 2019, 2021 and 2026
- **Found:** 2026-09-11, issues audit
- **Evidence:** against the player-box sums for the same team and game:
  - 2019: `assists` matches in 2,436 of 2,460 rows, `steals` in 2,445 and
    `turnovers` in 2,440.
  - 2021: `blocks` matches in 2,139 of 2,160 and `steals` in 2,142.
  - 2026: `assists` matches in 2,450 of 2,462.

  It is not known whether the team row or the player rows are at fault.
- **User sees:** nothing measurable yet. A team with/without or split table may
  be off by a count in a few games.
- **Next step:** compare a handful of the disagreeing games against the source
  box score.
- **Source:** DATA.md, "Team box scores disagree slightly with player-box sums in some seasons"
- **GitHub:** #54

### Shots past half court are counted but drawn off the canvas
- **Found:** 2026-09-11, shot-frame fix
- **Evidence:** the shot chart's subtitle counts heaves that the half-court plot
  does not show. Positioned shots past the half-court line: 475 in 2024, 581 in
  2025, 1,083 in 2026.
- **User sees:** rendering Luka Doncic's 2026 season draws 1,479 markers with
  **22 off the canvas** - not "one or two higher", which is what this entry
  originally estimated before it was measured. `court.py:135-137` (the
  `sx`/`sy` coordinate mapping) has no clamp of any kind.
- **Next step:** clamp heaves to the edge of the plot, or note them in the
  subtitle.
- **GitHub:** #55

### A slow agent answer cannot be canceled
- **Found:** before 2026-09-11 (`web/app.py` comment, `roadmap-2.0.md`)
- **Evidence:** closing the tab does not stop the inference. The planned fix, a
  canceled flag checked between tool calls, is not built.
- **User sees:** the next question waits behind an abandoned one.
- **Next step:** build the flag.
- **GitHub:** #56

### The agent's tool budget is full
- **Found:** before 2026-09-11 (`docs/architecture.rst`, "The tool budget")
- **Evidence:** measured 2026-09-11 with `prompt.estimate_tokens`, the budget
  check's own counter. The five tool schemas cost 1,442 tokens. With no
  knowledge entries selected, the preamble is 4,743 tokens, leaving 1,657 of
  headroom against `PREAMBLE_TOKEN_BUDGET = 6400`. With the three largest
  entries selected, it is 6,302, leaving 98. So a sixth tool does not fit, and a
  question that selects the largest entries is one short entry away from
  `PreambleTooLarge`. The docs disagree about the headroom, and all three are
  wrong: `docs/architecture.rst` says "a few hundred tokens", `AGENTS.md` says
  "about 220", and the 2.1.0 changelog says "about 120". The cheapest lever, folding `render_shot_chart` and
  `render_fingerprint` into one tool, is not done.
- **User sees:** nothing yet. It blocks any new agent tool.
- **Next step:** fold the two render tools when a new tool is next needed.
- **GitHub:** #57

### The PyPI upload fails
- **Found:** documented in `AGENTS.md` ("Releasing")
- **Evidence:** trusted publishing answers `invalid-publisher`, pending an
  account-access issue. It is not a workflow bug.
- **User sees:** releases only on GitHub.
- **Next step:** register the publisher once the account is back, then upload
  each tagged version.
- **GitHub:** #58

### A coverage caveat is added to a refusal that drew nothing
- **Found:** 2026-09-11, docs edits for 2.1.0
- **Evidence:** "plot Kobe Bryant's threes in 2002" is refused, and the answer
  still ends "...so the answer covers part of the year". That is the partial-
  season caveat for 2002 shots, attached to an answer that covers nothing.
- **User sees:** a refusal that also claims to cover part of a season.
- **Next step:** skip `coverage_caveat` when the template's result is a refusal.
- **GitHub:** #62

### A player's bio is fetched once and never refreshed
- **Found:** 2026-09-11, comparing a fresh full pull against the existing warehouse
- **Evidence:** `Pipeline._cache_player_bio` returns early when the file exists,
  so a bio is whatever ESPN said the day that player was first seen. Against a
  2026-09-11 pull, 270 of 3,101 `players` rows differ: 265 jersey numbers, 8
  short names (Enes Kanter is now "E. Freedom", Jimmy Butler "J. Butler III")
  and one position. The same reclassification moves 13 `player_season_stats`
  rows and 11 rows of `player_season_stats_deduped` (`PG` to `G`, athlete
  3907387).
- **User sees:** nothing today. No template reads `jersey`, `short_name`,
  `position_abbr` or `position`; they appear only in the agent's schema summary.
  Anything that starts reading them gets a stale value.
- **Next step:** re-fetch a bio when it is older than some age, or on `--force`,
  and record when it was fetched. Note that jersey and position are
  point-in-time facts stored as if they were static.
- **Source:** DATA.md, "A player's bio is point-in-time, stored as if it were static"
- **GitHub:** #64

### The stat glossary keeps whichever source described a key last
- **Found:** 2026-09-11, comparing a fresh full pull against the existing warehouse
- **Evidence:** `Pipeline.write_glossary` merges `{**existing, **self.glossary}`,
  so the last run to describe a key wins, and a key described by two endpoints
  takes whichever ran last. Both warehouses hold 211 keys, and `points` differs:
  the existing one says label "Points", description "Total Points", source
  `standings`; the fresh one says "PTS", "Points", source `player_box_stats`.
- **User sees:** nothing yet; no template reads the glossary. The agent can, and
  would get whichever description was written last.
- **Next step:** decide a source precedence per key, or keep one row per source.
- **Source:** DATA.md, "One stat key is described differently by two endpoints"
- **GitHub:** #65

### The warehouse file keeps the space of every load it has had
- **Found:** 2026-09-11, comparing a fresh full pull against the existing warehouse
- **Evidence:** the same 19 tables and 4 views, with identical row counts, take
  1.73 GiB in the existing warehouse against 0.92 GiB in the freshly built
  one. `warehouse.build` replaces tables in place, and DuckDB reuses the file's
  free space only for later writes, so repeated partial loads leave it behind.
- **User sees:** nothing. It is disk and a slower cold read.
- **Next step:** build into a temporary file and swap it in, or run a periodic
  compaction, if the size matters.
- **Re-checked 2026-09-15:** `PRAGMA database_size` read-only gives 7,101
  total blocks (1.73 GiB) against 3,781 used (0.92 GiB) - 47% free, matching the
  entry.
- **Fixed 2026-09-16 for a full rebuild.** `fetch/warehouse._build_full` now
  builds every table into a brand new `<db_path>.building` file rather than
  replacing tables in the existing one, so a full `data load` (or the first
  `data pull`) never carries a prior partial load's free space forward - the
  chosen fix was the first option in the entry's own next step, done as one
  change with #51's marker (the same temp-file-and-swap covers both). Not yet
  backfilled against the main warehouse - the next full `data load` will pick
  it up; running one is a real rebuild and outside a read-only session's
  constraints here.
- **Still open: a partial `--tables` reload writes in the existing file and
  can still accumulate free space**, since it cannot go through the same
  swap without first copying the whole file (see #51's still-open note - the
  two are the same underlying constraint). The main warehouse is 1.73 GiB
  today; whether that residual growth from partial loads alone is worth a
  periodic compaction command is the "if the size matters" the original next
  step already flagged as optional.
- **Measured 2026-09-16:** the first full `association data load` through the
  new path took the main warehouse from 1.73 GiB to 987 MiB, with every table's
  row count unchanged and `check_coverage`/`check_nicknames`/`check_team_box`
  passing. Space left by partial `--tables` reloads still accumulates until the
  next full load.
- **GitHub:** #66

### ESPN's career endpoint answered differently on two days three days apart
- **Found:** 2026-09-14, fixing the NULL-totals issue (#5). **The first version
  of this entry blamed a pull that never refetched. That was wrong, and it was
  withdrawn before it spread.**
- **Evidence: both measurements are sound, and they disagree because the source
  changed.**
  - **2026-09-11:** a full pull into a separate warehouse returned these lines
    with no totals. Re-checked on 2026-09-14 against the warehouse that pull
    actually produced (`/home/jeff/association-fresh/nba.duckdb`):
    `player_season_stats` holds 29,780 rows carrying **the same 246 NULLs**, and
    22 of its 23 objects match the live warehouse row for row. That pull did
    request these files, and did get NULLs back.
  - **2026-09-14:** the same endpoint, the same keys, serves a `totals` category
    for **87 of the 107** affected files. An independent 20-player sample the
    same day split 16 with totals, 4 without.
  - **Five files resisted the repair, and the cause turned out to be OURS, not
    ESPN's.** Traced 2026-09-14: the client received the full three-category
    payload every time - a recorder wrapped around the pipeline's own client
    proves it - and `_repair_season_totals` did its job, taking Seth Curry's
    rows from 16 non-NULL to 17. The data was destroyed at the moment of
    WRITING, by `storage.write_rows`, which took its Parquet schema from the
    first row alone. See `CHANGES.md` ("A row narrower than the rows after it
    no longer truncates the whole file"), fixed in `dc3e03a`, GitHub #80
    (closed). Two earlier drafts of this entry blamed ESPN flipping within
    minutes, and then "something between the two callers"; both were guesses
    made ahead of the trace, and both are withdrawn.
- **What this is not.** The withdrawn version inferred "the pull never issued a
  request" from Parquet mtimes in the MAIN tree — which can say nothing about a
  pull that wrote into a separate tree by design — and on that basis named four
  other entries (the per-game NetPoints tables, the 2000/2001 playoff gaps, the
  empty 2013-2018 box scores, the traded-player combined rows) as possibly
  resting on the same void. They are not: the fresh warehouse holds all 23
  objects with matching row counts.
- **User sees:** nothing directly. The cost falls on us. A "does a refetch fix
  it" finding about this endpoint has a shelf life, and this one expired in
  three days — acting on the stale one aimed the fix at deriving totals from
  `avg × gamesPlayed`, exact only 49% of the time, when the real numbers were
  one request away.
- **Next step:** keep dating these claims (every one in `DATA.md` already says
  2026-09-11) and treat one older than a release as unverified rather than
  false. The cheap re-verification is a forced fetch of a single affected key,
  not a whole pull. And because the flip happens inside a single run, a
  backfill over this endpoint should be **re-run until the count stops
  falling** rather than trusted after one pass - re-running costs only the rows
  still NULL.
- **GitHub:** #79

### The SQL agent can read a team-rebounds column with the 2021/2022 discontinuity
- **Found:** 2026-09-17, while fixing #75 (the deterministic
  `player_splits`/`streak` team-rebounds read)
- **Evidence:** `team_season_stats.totalRebounds` (the season-total column,
  read as `totalRebounds / gamesPlayed`) reproduces the exact drop the fixed
  issue was about - 53.17 a game in 2020, 49.00 in 2021, 44.45 in 2022 - while
  its sibling `avgRebounds` does not (974 of 975 team-seasons 1994-2026 already
  equal `avgOffensiveRebounds + avgDefensiveRebounds`, see DATA.md). No
  template reads the raw `totalRebounds` column - only `run_sql`, the
  SQL-writing agent's fallback tool, can reach it, since `team_season_stats` is
  one of the tables its preamble describes.
- **User sees:** nothing today - this is a gap nobody has hit, not a wrong
  answer delivered. A question that falls through to the agent and asks it to
  compare or trend a team's rebounds across the 2021/2022 boundary (e.g. "how
  have the Celtics' rebounds trended since 2019") could have the agent write
  `SUM(totalRebounds)` or read the column directly, producing a fluent,
  ESPN-accurate-per-row, cross-era-incomparable number with no caveat - the
  same failure `player_splits` used to have, one level down in the tool stack.
- **Next step:** either add a `run_sql` preamble note steering a rebounds
  question toward `avgOffensiveRebounds + avgDefensiveRebounds` (cheap, but
  competes for the same token budget every other preamble addition does - see
  AGENTS.md, "The tool budget"), or measure how often `run_sql` actually gets a
  rebounds-trend question before spending budget on it. Not fixed here: out of
  scope for the deterministic-template fix, and unmeasured how often it fires.
- **Source:** DATA.md, "The team `totalRebounds` column stops including team
  rebounds in 2022"
- **GitHub:** #103

### The agent's prompt says there is no per-game fingerprint, and the warehouse holds one
- **Found:** 2026-09-18, while writing up the query path
- **Evidence:** `query/prompt.py:525-528` tells the agent that
  `render_fingerprint` covers one season and "There is no per-game fingerprint:
  say so rather than plotting a season for a question about one game". The
  same prompt's `TABLE_SUMMARY` (`prompt.py:73-76`) describes
  `net_points_player_game_fingerprint`, the per-game play-type table, and the
  `fingerprint` template draws a single game from it when `order` is set. The
  tool does not reach that table, but the sentence claims the data does not
  exist.
- **User sees:** rarely anything. The fast path answers a one-game
  fingerprint question itself. A question that reaches the agent anyway, such as
  one the router mis-slots or one that falls through on another scoping slot,
  gets a refusal that names the wrong cause ("no per-game fingerprint exists").
  P4 on reach; the shape is P2's wrong-cause refusal.
- **Next step:** reword the tool line to describe what the tool cannot do ("this
  tool draws seasons only") rather than what the data lacks. Hash the preamble
  and re-check `PREAMBLE_TOKEN_BUDGET` headroom, since this text is charged on
  every agent call.
- **GitHub:** #128
