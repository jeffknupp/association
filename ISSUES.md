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
- **P3: refusal or gap.** A question real users ask is refused, or data the
  source publishes is missing from the warehouse.
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

### "warriors' record last season when committing 10 or fewer turnovers" answers the whole season
- **Found:** 2026-10-05, the Phase 2 review (`~/association-research/reviews/phase2-2026-10-05/REVIEW.md`)
- **Evidence:** the "or fewer" line on a team's own stat is not read; the answer is the Warriors' 48-34 2025 standings line with no mention of turnovers, while "... when they had 18+ turnovers" reads the line and answers a record table. Same on `4bb026a` (before Phase 2) and `93db30c`; also 11 and 12 or fewer. Review data: `data/feed-93db30c.jsonl.gz`, names `["warriors"]`, stat `turnovers`.
- **User sees:** a fluent answer to a broader question - the season's record where the record under a turnover line was asked.
- **Next step:** read "N or fewer"/"N or less" as a below line in the team condition reader (`lines.measure_filters` reads it for a player); until then refuse by name.
- **GitHub:** #325

### "knicks vs celtics total points" answers the season series record, not the points
- **Found:** 2026-10-05, the Phase 2 review (`~/association-research/reviews/phase2-2026-10-05/REVIEW.md`)
- **Evidence:** the answer is "met 4 times ... won the series 3-1". Before Phase 2 the reading held a team `total` of points that the template ignored; since `4db543a` the reading itself is a `record`. Same answer on both trees, names `["knicks","celtics"]`, stat `points`.
- **User sees:** a different question answered fluently.
- **Next step:** a `head_to_head` with a box-score stat is the meetings' totals (the team compiler's rows read sums them), or a refusal naming the stat.
- **GitHub:** #326

### Two seasons named before the team are dropped: "2024 and 2025 Knicks record by month" answers 2026
- **Found:** 2026-09-30, the review of `ROADMAP-TYPES.md` (an Opus agent over the stage snapshot of the 628 recorded questions at `7f6425b`); the answers quoted were re-read from the snapshot.
- **Evidence:** the reading's scope holds no `season`, `since` or `until` (`{'season_type': 2, 'split': 'month', 'team': 'New York Knicks'}`) and the answer is "The New York Knicks, record by month, the 2026 regular season". The same words in another order ("knicks record by month 2024 2025") read `since: 2024, until: 2025` and answer both seasons.
- **User sees:** a by-month record for a season he did not ask about; the season is printed.
- **Next step:** the season reader takes "N and M" before the subject as it does after it (`season_text`); a case in `tests/query/test_parser.py`.
- **GitHub:** #289

### A number the question states is dropped and a broader question answered: three paraphrases the unread-words ledger found
- **Found:** 2026-09-30, `scripts/claims_ledger.py` over the 628 recorded
  questions at `011091f` (the first run of the instrument: 11 questions
  have a number the reading does not depend on; these three are answered
  fluently without it).
- **Evidence:** (1) "since 2000-01, how many games have players recorded
  33 points, 13 rebounds, 10 assists, 2 blocks, and 2 steals?" reads
  `leaderboard` with `stat: rebounds` and answers a rebounds-per-game
  ranking since 2001 (Andre Drummond 11.9 ...); none of the five lines is
  read. The yardstick's own wording of it (F161, "players with 33 point
  and 13 rebound ... games since 2000-01") is answered correctly. (2) "2
  threes in games Jamal Murray played including playoffs" answers his 3.2
  threes per game over 81 games; the 2 is not read. (3) "18-year-old
  Lebron's ppg total" answers "20.9 points per game in 60 games in the
  2026 regular season"; the age is not read, and `refusals` has a named
  refusal for an age that did not fire on this wording.
- **User sees:** a fluent answer to a broader question than the one
  asked, with nothing saying a number was set aside.
- **Next step:** reader fixes, one per wording: "N <stat>, N <stat> ...
  and N <stat>" joined by commas reads as lines the way "and"-joined ones
  do; a hyphenated age ("18-year-old") reaches the age refusal. The
  structural answer is `ROADMAP.md` contract 2 (a content word nothing
  claimed is recorded, measured, then said or refused); re-run the ledger
  after each reader change and read its unread NUMBERS first.
- **GitHub:** #290

### A quarter named as a vague condition is read as the period asked about: "jokic assists per game after a big first quarter" answers his first-quarter assists
- **Found:** 2026-09-30, checking the period condition (ROADMAP step 2);
  re-verified offline the same day at `011091f`.
- **Evidence:** "jokic assists per game after a big first quarter"
  (normalizer reply `jokic` / `assists`) reads `period_split` with
  `period: 1` and answers "Nikola Jokic had 240 assists in the 1st quarter
  over 65 games of the 2026 regular season, averaging 3.7." The question
  asks for his whole-game assists in games whose first quarter was "big".
  `parse.read_period_condition` reads a condition only where a number
  names the line ("after making one three in the first quarter"), and
  `refusals._period_as_condition` did not fire: "big" names no line.
- **User sees:** a fluent answer to a different question.
- **Next step:** "after|following|when ... <quarter>" with no line is a
  condition nothing can hold: refuse it naming the missing number, rather
  than read the quarter as the period. A reader fix; goes with the read
  stage's work (`ROADMAP.md`, Phase 1) unless it shows up in the wild
  first.
- **GitHub:** #291

### "single game" before a boolean stat is read as a single-game high: "who has the most single game triple doubles" answers a single-game high, never the triple-double leaderboard
- **Found:** 2026-09-28, a probe the #260 fix agent invented (in no corpus) and reported; re-measured by the lead on the merged tree (`95c8a21`, the main warehouse, the parser with no model).
- **Evidence:** `parse.read_route` reads "who has the most single game triple doubles" (and "... this season") as parent `leaderboard`, then the `single_game_high` child (`subject._CHILD_GRAMMARS`, the `\bsingle[- ]game\b` alternative #260 added): `intent=single_game_high kind=everyone slots={'stat': 'triple_double', 'season_type': 2}`, reason "the words 'single game' name single_game_high". "who has the most triple doubles" and "most triple doubles in a single season" stay `leaderboard`. A triple-double is a boolean measure (`compose.core.BOOLEAN_MEASURES`): "single game" here modifies the stat - every triple-double is one game's - not the question's shape, and a league-wide single-game high of a boolean has nothing to rank by. Through the agent with the route as read (answered_by=fast intent=single_game_high): "every player, 2026 regular season with a triple-double - top 3 by points:   2025-12-25  Nikola Jokic     vs MIN  W  points 56  minutes 43  rebounds 16  assists 15   2025-11-10  Cade Cunningham  vs WSH  W  points 46  minutes 45  rebounds 12  assists 11   2025-12-18  Luka Doncic      @ UTAH W  points ".
- **User sees:** that, where the count of triple-doubles per player - the leaderboard's answer - was asked: a different question, answered fluently or refused for a cause that is not the question's.
- **Next step:** the `single_game_high` child declines where the stat is a boolean measure (`BOOLEAN_MEASURES`: a count of such games is the leaderboard's question), with cases in `tests/query/test_subject.py` and `port_check.py`'s corpus, and the rehearsal checked for false negatives ("most points in a single game" must still read as the high). Small: the gate is one condition in `subject._child_intent` or the row's own pattern.
- **Source:** ours.
- **GitHub:** #265

### "Embiid's record against Boston this year" answers a with/without split of the 76ers over the regular season; the question is Embiid's own games, playoffs included
- **Found:** 2026-09-26, Jeff reviewing the pipeline walkthrough page (yardstick-v2 F088, all five wordings; blind key 4-2).
- **Evidence:** `live_day10.jsonl` (bcf30a8): the router files `head_to_head` (team PHI, opponent BOS); the reading's rule "a player's record against a team, not two teams meeting" (`subject._decide_intent`) reroutes to `with_without` with `without=[Joel Embiid]`, and the template answers "Philadelphia 76ers with and without Joel Embiid vs the Boston Celtics, 2026 regular season: out 2 1-1, played 2 1-1". The key (blind, reading a, dominant): the 76ers went 4-2 in the 6 games Embiid played vs Boston in 2025-26 - 2 regular-season meetings and playoff Games 4-7 (he has no box row for Games 1-3). Two faults: the SHAPE (a split with an "out" row nobody asked for, where the question is his games and their record - dates, outcomes, and reasonably his line in each), and the SCOPE ("this year" narrowed to the regular season by the unstated-season-type default, `_validate_season_type`; `season_type_unstated` widens only "last N games" and "including playoffs" wordings today).
- **User sees:** a fluent table about a different question, with the right numbers for the narrower scope it states - which is why the blind grader passed all five wordings; re-graded wrong on Jeff's call (overrides run day10). Day10 is 160/175 and families 150/166 after the re-grade.
- **Parser path (2026-09-27, plan item 6 steps b-d):** the SHAPE is fixed - the parser reads a player's record with no companion as `player_splits` ("Joel Embiid vs the Boston Celtics, splits, 2026 regular season (2 games he played) ... W-L 1-1"), graded correct in `live_parser1`-`3` under Jeff's 2026-09-27 instruction to score the season type as the regular-season default, and the router reader that still answered the with/without split is gone (step d). The SCOPE half (regular season only) is the deferred season-type decision, unchanged.
- **Next step:** two decisions for the parser (ROADMAP: the consolidation). Shape: a player set against a team is his games narrowed by opponent - the player-games relation, rows with W/L and his line plus the tally - never the team's with/without split (the reroute rule goes; the compiler's rows shape with a record summary is the nearest thing today). Scope: "this year" / "this season" with no type named reads both types and says so, the way `season_type_unstated` already does for "last N games" - a policy for every question, to confirm with Jeff, since the regular-season default is what every per-game average answers under today.
- **Source:** ours, not ESPN's.
- **GitHub:** #231

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
  `season_line.ADVANCED_STATS` so `player_stat` can look it up too. The
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
- **Parser path (5.0.0, the router gone):** "game score nba leader" answers
  the game-score leaderboard (Jokic 28.68), read from the words by
  `parse.MEASURE_GRAMMAR` before the model's key, and "defensive rebounds" is
  `MEASURE_WORDS`' own key. The shape stays possible: the normalizer's `stat`
  is required over a closed enum (`NORMALIZER_STATS`), so a stat word neither
  the measure grammar nor the enum holds can still get the nearest key the
  model knows - unmeasured on the normalizer; "ats okc" and "players with the
  highest scoring triple doubles" are the cases to check.

The 391 date-only games printed a day early (#76), 2008's team rebound columns
(#74) and the swapped 1990 Finals Game 5 were fixed on 2026-09-16. Before adding
to this section, re-read the P2s against the P1 definition: that is how both of
those were found.
- **GitHub:** #114

### A team's single-game high is answered for this season only, and does not name the team: "most points in a game in cavs history"
- **Found:** 2026-09-21, live fast-path sample `~/association-research/statmuse-2026-09-large/live_sample200_2026-09-21/` (200 seeded-random reasonable StatMuse questions through the live router and templates on master `31b2ec6`); re-measured 2026-09-27 on the compiler, which answers `single_game_high` alone (plan item 6, step (d), part 4).
- **Evidence:** the router-era template ignored `team` and answered the league's high ("Bam Adebayo ... 83") for "most points in a game in cavs history", "most points in a game by a knicks playter" and "most points in a game in pistons history power forward". The compiler narrows to the team's players now - `compose.answer(ctx, "single_game_high", {"stat": "points", "team": "Cleveland Cavaliers", "season_type": 2}, "most points in a game in cavs history")` answers "Donovan Mitchell had the most points in a single game in the 2026 regular season: 48, on 2025-12-12 vs WSH. Next: Donovan Mitchell (46), Donovan Mitchell (45)." (the main warehouse) - but "history" is not read as a career, and the sentence never says the games are the Cavaliers'. 65 of 1,972 reasonable large-set questions (3.3%) say history/all-time/franchise.
- **User sees:** the right team's players over the wrong span - this season, where the question asks the franchise's history - with nothing saying a team narrowed it.
- **Next step:** read "history"/"all-time"/"franchise" beside a team as `span: career` (the parser's window table), and have the single-game sentence name the team it narrowed to (`Narrowed.filters()` already writes it); a test per wording on the relation.
- **GitHub:** #167

### A team's `record_when` misreads "they score N points when X starts" as X's own threshold, dropping the start
- **Found:** 2026-09-27, closing the #144 entry above this replaces: checking whether its own "companion bends it the other way" example ("76ers record when they score 120 points when embiid starts") survived the compiler-decline fix below.
- **Evidence:** measured read-only against `/home/jeff/code/association/nba.duckdb`. `subject.read_subject(con, "76ers record when they score 120 points when embiid starts", "record_when", {"team": "Philadelphia 76ers", "stat": "points", "threshold": 120, "season_type": 2})` returns `subject.conditions = (Companion(name='Joel Embiid', predicate='reached', stat='points', threshold=120),)` - the team's own "they score 120 points" is folded into Embiid's condition, and "starts" is not read at all. `_apply_team_record_when` (`subject.py:1064`) then fires as designed - its own rule is "the TEAM's record in the games a companion reached a line" ("Sixers record when Embiid scores 30", a real, correctly-answered shape) - and rewrites the slots to `{'player': 'Joel Embiid', 'stat': 'points', 'threshold': 120, 'team': 'Philadelphia 76ers'}` on the strength of that misread condition. `record_when` (and `compose.answer`, identically) then answers "Philadelphia 76ers record when Joel Embiid had 120+ points, 2026 regular season: 120+ points 0 0-0 ... under 120 points 38 24-14" - a real player's real 0-for-120 record, for a question about the TEAM's own scoring with Embiid's start as its actual (and separately dropped) condition. Neither of this session's `record_when` fixes touches it: a `player` slot is already set by the time the question reaches `record_when`, so it never reaches `compose.team`'s `TeamQuery` or the team branch's own `conditions` refusal at all - confirmed by re-running both after the fixes, identical output.
- **User sees:** a fluent, confidently wrong-subject answer - a real player's real record, for a question about his team's own scoring.
- **Next step:** `read_subject`'s companion-condition parsing needs to keep "they/the team score N" (the team's own subject-level threshold) and "X starts"/"X comes off the bench" (a role with no stat attached) as two separate facts, rather than folding both into one `Companion(predicate="reached", ...)` - fix belongs in `query/subject.py`, not this session's files.
- **Source:** ours.
- **GitHub:** #246

### A misspelled team name is dropped by the parser, and the question is answered without it: "gui last 5 games vs sours" lists his last 5 games, none against the Spurs
- **Found:** 2026-09-27, plan item 6 step (d) part 3 (deleting the router reader), re-measuring #131 ("gui last 5 games vs sours") on the parser path.
- **Evidence:** the parser path offline on 33cfd60 (and before it on 96b4b65), the normalizer stubbed with the question's own name spans (what the model is told to copy, and what `parse._as_typed` puts back when it corrects one), read-only on the main warehouse: "gui last 5 games vs sours" routes `{'player': 'Gui Santos', 'order': 'recent', 'limit': 5, ...}` with no `opponent` and lists his last 5 games (LAC, SAC, HOU, CLE, DEN), where "gui last 5 games vs spurs" lists his last 4 against San Antonio; "jalen brunson points vs the celtcs this season" answers his season average (26 points in 74 games); "lakers record vs the nuggest" answers the Lakers' season record (53-29). `parse.classify_span` reads a span as a team only by its exact word, nickname, code or name (`team_named_in`, `find_teams`), and the index's near-spelling pass is for players only, so the typo'd span is nobody's and is dropped, and nothing says a word of the question went unread.
- **User sees:** a fluent answer to the un-narrowed question - the player's or team's whole window or season - with the opponent it names nowhere in it.
- **Next step:** a near spelling of exactly one franchise's word or nickname (within the index's edit budget, against the 30 franchises' words only) reads as that team and says so, the way `entities.read_near_spelling` does for a player; a span near nothing refuses by name rather than vanishing. A `tests/query/test_parser.py` case per wording above, watched to fail, and the hold-out comparison.
- **Source:** ours.
- **GitHub:** #247

### A start the question denies reads as a start: "maxey game log when embiid doesn't start" lists his games WITH Embiid starting
- **Found:** 2026-09-27, plan item 6 step (d) follow-ups, probing negated roles beside the teammate-role fix.
- **Evidence:** `subject._condition_role` finds "start" in "embiid doesn't start" and returns `started`; it has no negation, and the relation has no "did not start" predicate (`player_games.CONDITION_PREDICATES`: played, absent, started, bench, reached). On a relation template the condition narrows to the opposite games: the agent (parser reader, normalizer stubbed, main warehouse), identical on `0a7140a` and this branch, answers "maxey game log when embiid doesn't start" with "Tyrese Maxey with Joel Embiid starting, last 10 of 35 games of the 2026 regular season" - the 35 games Embiid started, where the question asked for the 35 he did not (38 started of the 76ers' 82 per `with_without`'s own split, of which Maxey played 35). The parser keeps a denied start on `with_without` for a stat question ("maxey points when embiid doesn't start" shows both halves), but the log row, the chart row and a record's `player_splits` row come first.
- **User sees:** the inverse of the question, labeled with the condition it inverted ("with Joel Embiid starting").
- **Next step:** read a denied start or bench in `_condition_role` (the parser's `parse._DENIED_ROLE` is the pattern) and either refuse it on a filter or add a `not_started` predicate to the relation (NOT EXISTS over the started clause, bounded by the tenure an absence already carries); a test per wording.
- **Source:** ours.
- **GitHub:** #248

### "nba most fga with 0 fgm single game" is read as the teams' field goals made, and "without fgm single" as a teammate
- **Found:** 2026-09-27, checking the answers the #168 fix moved (then part of #260, closed by 2026-09-28's fix: "single game" with no article is one game, and "this season's single game with the most assists" and both "most 3 pointers made in single game ..." read as one); what remains is its own shape.
- **Evidence:** stubbed offline through the whole agent (main warehouse, names `[]`, stat `fieldGoalsAttempted`): "nba most fga with 0 fgm single game" routes `team_leaderboard` `{'stat': 'fgm', 'rank': 'most'}` and answers "Field goals made per game, 2026 regular season - highest first, of 30 teams: Miami Heat 43.7 ..."; "nba most fga without fgm single game" routes the same with `without: ['fgm single']` and falls through ("team_leaderboard cannot honor ['without']"). `parse.PARENT_GRAMMAR`'s everyone row reads "nba" beside "most" as a team ranking, and `single_game_high`'s child row applies only under the player relation's parents (`subject._PLAYER_RELATION_PARENTS`), so "single game" is never read; the measure is "fgm" where "fga" is the ranked one; and the router's without reader takes "fgm single" for a teammate. These are the last 2 of the 29 research-corpus questions saying "single game" that are not read as one game.
- **User sees:** a wrong answer - the teams' field goals made per game, where one player's single game was asked; a refusal for the second.
- **Next step:** let `single_game_high`'s row apply under `team_leaderboard` for the everyone kind where no team word ("team", "franchise") is written, measured on the rehearsal and `intent-shrink/port_check.py` first; a single game "with 0 fgm" is a predicate on the high the compiler has not got, so it refuses by name until it has one.
- **Source:** ours.
- **GitHub:** #266

### A month named with its calendar year is read as the season ending that year: "How many points did De'aaron fox average in November 2023" answers November 2022
- **Found:** 2026-09-28, fixing #259 (one of the 19 research-corpus questions with a typographic apostrophe).
- **Evidence:** stubbed offline through the whole agent (main warehouse, names `["De'aaron fox"]`): routes `{'player': "De'Aaron Fox", 'season': 2023, 'situation': 'in november'}` and lists "De'Aaron Fox in November, last 10 of 13 games of the 2023 regular season", dated 2022-11-11 to 2022-11-30. November 2023 is in the 2024 season (2023-24): `season_text.season_from_text` reads "2023" as the season ending that year, which is right for a year alone and wrong beside October, November or December. Of the 3,084 distinct research-corpus questions, 7 name a month with its year; this is the only one whose month falls before the new year ("Luka doncic march 2026", "steph stats april 2021" and the rest read right).
- **User sees:** a wrong answer - the right player's games from a year earlier, captioned as the month asked.
- **Next step:** where a month from October to December is written with its year, read the season as that year + 1 (`router._validate_season` beside the situation reader); a case per month in `tests/query/test_router.py`.
- **Source:** ours.
- **GitHub:** #267

### A possessive on a name several players share drops the player: "Curry's stats by month 2016" answers the league's 2016 scorers
- **Found:** 2026-09-28, fixing #259.
- **Evidence:** stubbed offline through the whole agent (main warehouse, names `["Curry's"]`, and `["Curry’s"]`, which reads the same since #259): routes `leaderboard` with no player and answers "every player, 2016 regular season, by player (points per game, minimum 20 games): Stephen Curry ... James Harden ...". `parse.classify_span` strips the possessive for its team check but looks the player up as given: `find_players(con, "Curry's")` needs a player holding the word "s", and the near-spelling fallback takes only a single near spelling, which "Curry" (six players) is not - so the span is nobody's and dropped. "curry stats by month 2016" keeps the name, and the template asks which Curry.
- **User sees:** a wrong answer - the league ranked where one player, a name several share, was asked about.
- **Next step:** in `classify_span`, look the player up with the possessive stripped too (the `low` it already computes), so a shared surname stays a player's span for the template to ask about; a case in `tests/query/test_parser.py`.
- **Source:** ours.
- **GitHub:** #268

### A team named for a player's tenure beside an opponent is dropped: "d'angelo russell vs pistons as a laker" answers his 2026 games as a Maverick
- **Found:** 2026-09-28, fixing #259 (the question carries a typographic apostrophe; its straight twin reads the same).
- **Evidence:** stubbed offline through the whole agent (main warehouse, names `["d'angelo russell", "pistons", "laker"]`): routes `{'player': "D'Angelo Russell", 'opponent': 'Detroit Pistons'}` with no team, and answers "D'Angelo Russell averaged 17.5 points ... in 2 games vs the Detroit Pistons in the 2026 regular season" - his 2026 season is Dallas's (`player_season_stats_deduped`: 26 games for the Mavericks); his Lakers years are 2016-2017 and 2023-2025. The model's "laker" is dropped by `parse._read_route_names` once the reading placed an opponent, and `subject._apply_own_team` writes no `own_team` where an opponent is set (`subject.py:1091`), so "as a laker" narrows nothing.
- **User sees:** a wrong answer - another team's games, where a tenure was asked.
- **Next step:** read "as a <team>" beside an opponent as `own_team` (the tenure) rather than dropping it; a case in `tests/query/test_one_writer.py`.
- **Source:** ours.
- **GitHub:** #269

### "3's" is not read as threes: "Gabe Vincent 3’s as a laker" answers his points line
- **Found:** 2026-09-28, fixing #259.
- **Evidence:** stubbed offline through the whole agent (main warehouse, names `["Gabe Vincent", "laker"]`, stat `threePointFieldGoalsMade`): routes `player_stat` with no stat and answers "Gabe Vincent averaged 4.4 points, 1 rebounds and 1.4 assists per game in 53 games in the 2026 regular season". `parse.MEASURE_GRAMMAR` reads "3s", "threes" and "3 pointers", and "3's" is none of them; the model's key is not kept for a line the words do not name. "Jarred 3’s as a laker" is the corpus's other one. Both also carry "as a laker", which the answer does not honor (Vincent's 2026 line is not a Laker's).
- **User sees:** a wrong answer - his scoring line where his threes were asked.
- **Next step:** read "3's" (and "3’s", folded since #259) as threes made in `MEASURE_GRAMMAR`; a case in `tests/query/test_parser.py`.
- **Source:** ours.
- **GitHub:** #270

### A date written as numbers is not read: "sga game log on 2026-01-02" lists his last 10 games, "sga fingerprint on 2026-01-02" draws his season
- **Found:** 2026-10-05, Phase 2 step 5 (the NetPoints charts), measuring synthetic fingerprint wordings.
- **Evidence:** through the whole agent on the main warehouse, normalizer stubbed to names `["sga"]`, `ASSOCIATION_TODAY=2026-09-30`, at `70eba2b`: "sga game log on 2026-01-02" reads `game_log {'player': 'sga', 'season_type': 2}` (no `date`, no season) and answers "Shai Gilgeous-Alexander, last 10 of 68 games of the 2026 regular season: ..."; "sga points on 2026-01-02" answers his 2026 season average (31.1 in 68 games); "sga fingerprint on 2026-01-02" draws his 2026 season fingerprint - where "sga fingerprint on january 2, 2026" reads `date: '2026-01-02'` and is refused in the fingerprint's own words, and "sga points on january 2, 2026" reads the date. `router._validate_date` reads only a month name (`_CALENDAR_DATE`); an ISO date, and a bare "1/2/2026" outside a range word (`_NUMERIC_DATE_RANGE` needs "since"/"after"/"from"), are read as nothing.
- **User sees:** a wrong answer - a window, a season line or a season's chart where one dated game was asked, the heading naming the span it did read.
- **Next step:** read `YYYY-MM-DD` (and `M/D/YYYY` outside a range word, which a shooting line like "7/14" never has a year for) as a day in `router._validate_date`; a case per form in `tests/query/test_parser.py`; the readings population will move for every question holding one (none of the 628 recorded questions does).
- **Source:** ours.
- **GitHub:** #323

### A conference or division the players or teams belong to is answered as the whole league, or as games against it
- **Found:** 2026-10-09, Phase 3 step 0, probing which questions reach `calendar.conference_named` (none of the 2,710 readings does)
- **Evidence:** through the whole agent on `4b9c254`, replies as `run --feed` gives them (no names; the stat where named): "who leads the east in scoring" answers "Luka Doncic led the league in points per game in the 2026 regular season ... at 33.5" - the league, and a Western player; "who leads the east" answers every player by points per game; "top scorers in the western conference this season" answers "every player against Western Conference teams" - `situation` "in the western conference" read as the OPPONENT's alignment (`calendar.parse_alignment` takes "in the west" as a narrowing to games against it), where the question asks for players IN the West. "best record in the east" is refused ("team_leaderboard cannot honor ['situation']"), the one wording probed that does not answer. #25's "What remains" said "a refusal for 'who leads the East'": no longer true.
- **User sees:** a fluent answer to a different question - the league's leaders, or every player's games against the conference.
- **Next step:** read a conference or division the subject belongs to ("in the east", "the east's", "eastern conference players/teams") as the subject's own alignment - a ranking pool narrowed by `team_alignment` on the player's or team's own team that season - distinct from "vs/against the east" (the opponent's); until a reader takes it, refuse it by name rather than answer the league. #25 is the same gap from the data side.
- **GitHub:** #339

### A team's record when a companion reaches a line the stages cannot read is answered as the with/without split
- **Found:** 2026-10-09, Phase 3 step 0, while measuring whether `refusals._team_where_a_player_belongs` was reachable (it was not, and this is why)
- **Evidence:** feed answers on `4b9c254`: "knicks record in playoff games when mitchell robinson has 4 fta" and "... has 6 fta" answer "New York Knicks with and without Mitchell Robinson, 2026 postseason"; probed with names: "sixers record when maxey has 20+" (stat points) and "celtics record when tatum has 30" answer the with/without split too. `subject._CHILD_GRAMMARS` names `record_when` for each (`\brecord\b.*\b(when|with)\b.*\b(scored|scores|had|has)\b.*\d+`), the stages decline it when they read no line (no stat word after the number, or an abbreviation such as "fta"), and the parent the reading falls back to for a team with a companion is `with_without`. The deleted check refused exactly this shape ("A record split by 'X' needs a line ...") but sat after the compiler, which the with/without split satisfies first.
- **User sees:** the team's record with and without the player, where its record in the games he reached a line was asked.
- **Next step:** read the companion's line from "has N" with the normalizer's stat and from box-score abbreviations ("fta", "3pm") in the condition reader (`subject._condition_role`, `_CONDITION_THRESHOLD`); where a number is named and no line can be read, refuse naming the missing stat rather than fall back to the parent.
- **GitHub:** #340

## P2: misleading or incomplete

### "Stephen Curry free throw chart" (no "shot") answers his season averages
- **Found:** 2026-10-05, the Phase 2 review
- **Evidence:** read as `player_stat` on both trees; "free throw shot chart" reads as `shot_chart` and refuses correctly (no free-throw chart).
- **User sees:** a season line where a chart was asked for.
- **Next step:** "chart" with a player and no other shape word is a chart, or a refusal naming it - never the line (`subject`'s grammar).
- **GitHub:** #328

### A team that is the SUBJECT, named by a franchise's old name, is read without the question's season: "what was the bobcats record against the hornets in 2012" asks "did you mean New Orleans Hornets or Charlotte Bobcats?"
- **Found:** 2026-10-02, reviewing Phase 1 on wordings outside the corpus;
  what is left of the entry after the player's own team and the opponent
  were fixed the same day (the subject reading takes the question's own
  season: "chris paul assists for the hornets in 2008" and "kobe points
  against the hornets in 2008" answer for New Orleans).
- **Evidence:** the team a question is ABOUT is found by
  `subject._team_word` -> `entities.team_named_in` (`compose.team.team_named_in` until 2026-10-02), a whole-word match
  against today's `teams` table that takes no season. "bobcats" reads as
  today's Charlotte Hornets, and with `season: 2012` that name is two
  franchises. Measured on the real warehouse at the fix's tree, the date
  pinned to 2026-09-30. A team subject under a name it still carries, and
  an old name beside no season, are unaffected.
- **User sees:** a clarification naming 'Charlotte Hornets', which the
  question never typed, where "bobcats" and "2012" say which franchise is
  meant.
- **Next step:** give `_team_word` the season `read_subject` now holds, and
  read a franchise's old name through `entities.franchise_by_name` before
  the whole-word match; a case beside
  `test_a_team_is_read_in_the_season_the_question_names`.
- **GitHub:** #316

### In the off-season "last season" reads the season before the one just finished: "how many points did luka average last season" answers 2024-25 on 2026-09-30
- **Found:** 2026-09-30, with the season-rollover fix; re-verified offline
  the same day at `011091f`.
- **Evidence:** with the date pinned to 2026-09-30, "how many points did
  luka average last season" reads `season: 2025` and answers "Luka Doncic
  averaged 28.2 points per game in 50 games in the 2025 regular season."
  "This season" is 2026 (the latest with games on record), so "last
  season" is one before it. Between the last game of a season and the
  first of the next, a person saying "last season" most likely means the
  one that just ended (2025-26). The answer names the season it read, so
  the default is visible and "2025-26" reaches the other.
- **User sees:** a correct figure for a season one earlier than meant,
  with the season stated.
- **Next step:** Jeff's call: whether, while the latest season on record
  is over (no game in N days, or the calendar's season is ahead of it),
  "last season" reads the latest season on record and says so. Then one
  rule in `season_text`, with the answer stating which season it read.
- **GitHub:** #292

### A shot chart for a name two active players share is refused as naming nobody: "Plot Curry's threes from last season" says "shot_chart needs a player name"
- **Found:** 2026-09-30, roadmap review (agent B), on live parser22 and an
  instrumented offline replay; not re-measured by the lead.
- **Evidence:** the answer is "Nothing here answers this question:
  shot_chart: shot_chart needs a player name." The question names Curry;
  the cause is two active namesakes - elsewhere ("curry assist each game")
  the same name gets "did you mean Seth Curry or Stephen Curry?".
- **User sees:** a refusal naming the wrong missing fact (yardstick F003).
- **Next step:** the chart resolver's ambiguity reaches the refusal as the
  clarification, with a test.
- **GitHub:** #280

### A refusal for an unhonored narrowing prints the intent's identifier and a Python list: "player_compare: player_compare cannot honor ['since']"
- **Found:** 2026-09-30, roadmap review (agent B), on live parser22.
- **Evidence:** "compare Jaylen Brown and Jason Tatum's netpoints over the
  past four seasons" -> "Nothing here answers this question:
  player_compare: player_compare cannot honor ['since'] - it would answer
  for a different span than was asked."; "Best NBA record since January
  31st 201" -> "... team_leaderboard cannot honor ['situation'] ...".
- **User sees:** a slot name where AGENTS.md's rule is that a refusal
  names the missing thing, never only the slot.
- **Next step:** each cell's phrase in the relation's cell table (the new
  roadmap's contract 4), read by the refusal.
- **GitHub:** #281

### A comparison over a season no named player has a line in prints a table of dashes, not a refusal
- **Found:** 2026-09-30, roadmap review (agent A), under a faked October
  date (see the rollover entry in P1).
- **Evidence:** `compose.seasons._player_compare_lines`: "compare sga and
  embiid" with the season defaulted to 2027 prints the table with no
  figures and the note "(Shai Gilgeous-Alexander, Joel Embiid has no 2027
  ...)" - the wrong number of the verb as well.
- **User sees:** an empty table that reads as an answer.
- **Next step:** refuse, naming the season, when no named player has a
  line; fix the plural.
- **GitHub:** #282

### The compiler's team total ignores "no season type named": "total points by the raptors in the last 10 games" reads the regular season only
- **Found:** 2026-09-25, plan item 2 step 2a's parity harness
  (`~/association-research/intent-shrink/parity.py`, compiler alone against
  the template on the same slots).
- **Evidence:** `game_log` with `team`, `order: recent`, `limit: 10` and
  `season_type_unstated: True` (the router's "last N games, no type named").
  The template merges both types by date - "Toronto Raptors, last 10 games
  (3 regular season and 7 postseason) (5-5)" - while `compose/team.py`'s
  narrowed total reads one type: "The Toronto Raptors had 1,198 points over
  their last 10 games (6-4)", a different ten games, and the sentence does
  not say "regular season". Same for "KNICKS point differential over the
  last 7 games": +62 (6-1, all postseason) against the compiler's +46 (5-2).
  `_team_narrowed` counts the window as a narrowing but nothing in the team
  path reads `season_type_unstated` (the player path now honors it, through
  `scoped_player`).
- **User sees:** today nothing on the recorded corpus - the template answers
  these first. It is what the compiler would say for them the moment
  `game_log`'s team half is folded into it, and what it says for any team
  total the template refuses: a fluent total over the wrong games.
- **Next step:** read both types in `_compile_team_games_total` when
  `season_type_unstated` is set (the team-games relation's own
  `season_type` clause), or refuse it by name there as `_check_relation_scoping`
  does for the league-wide player read; a fixture test with a postseason game
  inside the window.
- **Source:** ours, not ESPN's.
- **GitHub:** #226

### A composed team season total carries a caveat about a different table: "88 3-pointers over the complete 2001 postseason (23 games). Note: ... Philadelphia's run reads 16 games against the 23"
- **Found:** 2026-09-25, checking the compiler's partial-season caveat while
  removing its duplicate (plan item 2 step 2a).
- **Evidence:** "how many 3 pointers did the sixers make in the 2001
  playoffs" composed as `team_stat` reads `team_season_stats` - ESPN's own
  23-game season line, complete - and the answering loop then appends
  `coverage_caveat("team_stat", slots)` (`agent._try_compose`), which is
  keyed by the INTENT's tables (`TEMPLATE_SOURCES`), not the table the
  compiler read, and says the run "reads 16 games against the 23". The
  compiler's own table-accurate note (`compose/team.py`'s
  `_team_coverage_note`, over `team_season_stats`: none) was removed in the
  same change because every composed answer printed the intent's note twice
  over it; the misfit itself predates that.
- **User sees:** a correct total beside a note contradicting its game count.
  Reached only where the `team_stat` template refuses and the compiler
  answers.
- **Next step:** let a composed result name the tables it read (a
  `TemplateResult.data` key `agent.py` reads before `coverage_caveat`), and
  have `_try_compose` call `association.nba.coverage.caveat` over those
  instead of the intent's.
- **Source:** ours, not ESPN's - the missing 2001 games themselves are
  `DATA.md`'s ("The 2000 and 2001 playoffs stop before the Finals").
- **GitHub:** #227

### Template data the page cannot render from
- **Found:** 2026-09-24/25, building the web page's typeset tables and going
  through both preview galleries (`/tmp/claude-1000/gallery_before_live` and
  `..._after_live`, 277 answers each, and `..._before_hist`/`..._after_hist`)
  answer by answer against `/home/jeff/code/association/nba.duckdb`. One
  entry, as the task that found these asked for, since every line below is
  the same shape: the page has a rendered body but the fact is only in
  `text`, not in `data`, so it cannot appear under the body without either
  re-parsing the sentence (which the page used to do, unreliably enough to
  need fixing - see the next bullet) or being dropped. The templates agent
  works from this list.
- **The big one - `answerNotes`'s text-parsing fallback is now off wherever a
  renderer built a real body**, because it was producing exact duplicates
  (`team_outlook`'s stat card, every line under it again as a "note" -
  Jeff's finding, live). That fix is right, but it also turned off the ONLY
  thing that ever surfaced these lines, and re-measuring the full 277-answer
  gallery after the fix shows it is not a small list:
  - `with_without` (14 of 14 answers with a "Counted: games inside ...
    tenure ... Played means ..." line) - e.g. "show 76ers record without
    maxey in 2025": "Counted: games inside Tyrese Maxey's time with the team
    (2020-12-23 to 2026-05-10) ... Played means Tyrese Maxey appeared in the
    game; out is a DNP or no box-score row at all." Gone from the page;
    still in the "text" toggle.
  - `record_when` (10 of 11): "Over the 70 games he played; a game he missed
    is in neither row."
  - ~~`team_record` (5 of 8, every one with the wins/losses card rather than
    the month table): the seed, streak, home/road split, last-10 and
    points-for/against line~~ **Fixed 2026-09-25**: the card now reads
    `data.home`/`data.road`/`data.last_ten`/`data.games_behind`/
    `data.points_for`/`data.points_against` directly (they were already on
    `data` - `_standings_season` set them; nothing in the page read them),
    and the month/venue/career branches that had no `data["headline"]` at
    all now do (`_standings_season_venue`, `_standings_career`,
    `_standings_career_venue`, `_team_record_by_month`,
    `_team_record_by_month_span` - `query/templates/teams.py` then; `compose/standings.py` and `compose/team_records.py`'s `_by_month`/`_by_month_span` since Phase 2). See the
    (now-deleted) "`team_record`'s standings/games-record card renderer
    always duplicates its own caption" entry for the caption half of this,
    which turned out to already be fixed before this session started
    (`caption: null` landed in `586ed35`, before that entry was filed).
  - `player_splits` (3 of 4): "Played means he appeared in the game, and W-L
    is his team's record in those games. Months go by the US Eastern date of
    the game."
  - `game_log`, team path (2 of 28): `games.py`'s `_team_game_log_total_line`
    ("Total points: 1,130." / "Point differential: +62 (+8.86 per game).")
    is appended to `answer` only (`_team_game_log_rows`), never to `data`, so
    a `stat` asking for a team total or differential lists the right games
    with the actual number nowhere on the page.
  - `game_log`, player path with a `without` narrowing (1 of 28): the
    "Without Anthony Black and Franz Wagner means games neither of them
    played while on the same team - a did-not-play entry, or no line in the
    box score at all" clause (`_box_score_notes`).
  - ~~`player_netpoints` (2 of 2, though this intent has no renderer at all -
    see the next bullet): the per-100-possession summary line and the
    play-type disclaimer.~~ **Fixed 2026-09-25**: both now reach
    `data["notes"]` (`_netpoints_notes`, `query/templates/netpoints.py` then; since Phase 2 notes on `compose/netpoints.py`'s Result, said by `compose.say._say_netpoints_note`),
    alongside `data["notes"]`'s own new units line ("Categories are per 100
    possessions over 1,329 possessions."). See the (now-narrowed) "No future
    template gets a renderer for free" entry above for the renderer itself.
  - `player_history` (2 of 21, the `career` span only): the career total/
    rate line ("Luka Doncic's career total: 4,230 assists.",
    `_player_history_career_count`/`_rate`) is appended to `answer` after
    the per-season table and never reaches `data`.
  - `player_compare`, `streak`, `period_split` (1 each): a coverage-style
    aside ("Luka Doncic has no 2026 postseason numbers in the warehouse.",
    "Streaks are counted within one season.", a defaulted-season redirect).

  Rough total when this was written: about 42 of 277 answers in the live
  gallery lost a line this way; minus the two bullets fixed above (2
  `player_netpoints` + 5 `team_record`), that is arithmetic against the
  original count, not a re-measurement - the gallery this was measured
  against (`/tmp/claude-1000/gallery_before_live`) is not on disk in this
  session to re-run. The fix is one shape repeated: give each of these its
  own `data["notes"]` entry (`agent.py`'s `_note` helper already shows how -
  the same list `player_compare`'s "vs" refusal and the fingerprint
  substitution note already use, which the page renders correctly today)
  instead of only appending to `answer`.
- **`player_history`'s `twoPointFieldGoalPct` reads land under a raw SQL
  expression as their dict key, not a header.**
  `compose/seasons.py` (`read_player_history`, over `season_line.history_statement`; `templates/players.py`'s `_player_history` when this was found) builds `history` as
  `dict(zip(["season", "games"] + [c for c, _ in columns], r, ...))` - `c` is
  `HISTORY_COLUMNS["twoPointFieldGoalPct"][1][i][0]`, the SELECT expression
  itself (`"100.0 * (fieldGoalsMade - threePointFieldGoalsMade) /
  NULLIF(fieldGoalsAttempted - threePointFieldGoalsAttempted, 0)"`), where
  every other stat's tuple has a real column name there. `_phrase_history`
  reads the same wrong keys back out for the CLI text (so the text is fine -
  it uses the tuple's second element, the header, only for the printed
  column titles) but the JSON `data.seasons` a reader gets is `{"season":
  2026, "games": 60, "100.0 * (fieldGoalsMade - threePointFieldGoalsMade) /
  NULLIF(...)": 58.6, "(fieldGoalsMade - threePointFieldGoalsMade)": 396,
  "(fieldGoalsAttempted - threePointFieldGoalsAttempted)": 676}`. Two
  consequences, both reproduced against the current warehouse ("show me
  lebron's 2pt percentage for the past 20 years"): the page's table header
  is that raw expression text (very wide, useless), and the sparkline reads
  `s[d.stat]` (`d.stat` is the clean `"twoPointFieldGoalPct"`, which is not a
  key in `s` at all) and drew a line through `undefined` points - the page
  now guards against that specifically (`sparkPoints`, never draws from a
  non-finite value) so the visible bug today is only the unreadable header,
  but the underlying key is still wrong. Fix: zip on `[h for _, h in
  columns]` like `_phrase_history` does, or add `columns`/headers to `data`
  the way `game_log` already does.
- **User sees:** a correct but visually stripped-down answer for the notes
  (still one click away, under "text"), and a broken-looking table for the
  `twoPointFieldGoalPct` history case.
- **Next step:** the bullets above, in the templates package - none of it is
  a page fix.
- **Source:** ours (the page's own gap), except `twoPointFieldGoalPct` and
  the `_team_game_log_total_line`/career-total lines, which are template
  bugs (a wrong dict key; an answer-only append) independent of the page.
- **GitHub:** #218

### `player_stat`'s coverage floor is computed from the wrong slot list, and misses `situation`, `since`, `game_n`
- **Found:** 2026-09-24, in passing while verifying the K3-2 conference/division
  narrowing did not need a new coverage-floor entry of its own.
- **Evidence:** `coverage._sources_for_player_stat` decides whether a
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
  fixed here: `templates/players.py` was outside that task's file ownership (the box-score decision is `reading.scope_reads_box_scores` now).
- **Source:** ours, not ESPN's.
- **GitHub:** #212

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
- **Parser path (5.0.0, the router gone):** the parser reads "rec" beside one
  player as his own record (`player_splits`, from its recorded reply
  `names: ['Tatum']`): "Jayson Tatum, splits, 2026 regular season (16 games he
  played)", W-L by venue and 13-3 as a starter - a different guess from the
  router's (Jaylen Tatum's stat line), still made without saying the question
  could mean the team's record.
- **GitHub:** #200

### A question with zero valid readings gets a fluent, self-chosen answer: "25-26 knicks playoff statistics vs other historic teams"
- **Found:** 2026-09-23, same session, yardstick-v2 F097. On `live_day5.jsonl`
  (e8c68ba, the shorter router prompt) it routes `team_outlook` instead and
  answers the Knicks' BPI snapshot - a different self-chosen framing, the
  same fault.
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
- **Also (2026-09-30, the types review):** "nba team with least playoff wins since 2022" and "NBA team with the fewest playoff wins since 2022" rank by win percentage ("worst record first"): New Orleans 2-8 is 4th and Utah 2-4 is 8th though they tie on wins.
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
  (a lock file; AGENTS.md's "Only one ollama caller at a time", where the
  routing check's docstring warning moved when the check was deleted, is
  enforced nowhere). Compare yardstick runs only when nothing else was asking
  ollama anything at the same time, not merely "within one server load" as
  the original entry said - a load-time comparison does not catch this.
- **After 5.0.0:** the router's model call is gone; the normalizer asks the
  same qwen2.5:3b on the same single-slot instance, so the mechanism applies
  to it unchanged - unmeasured on the normalizer.
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
  (`team_relation.team_games`, carried over unchanged from the old
  `_team_game_log_filters`'s `tbs.home_away = ?`); `team_record`'s own venue
  split treats a neutral-site game as neither home nor away
  (`compose.team_records`'s `"neutral" if row["neutral"] else row["side"]`, `_games_record_games` in `templates/teams.py` when found,
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
  narrow it and say so in the answer) and apply it in `team_relation.py:
  team_games`, which `game_log`'s team half, `head_to_head`,
  `player_splits`/`record_when`/`streak`'s team branches and now
  `team_quarter_points` all read through - one change reaches all six. Not
  fixed here: this carve-out is a proven pure refactor, and picking a rule is
  a behavior change with its own golden re-score.
- **Source:** ours, not ESPN's - `team_box_stats.home_away` and
  `games.neutral_site` both correctly describe the game; the gap is which of
  the two `game_log`'s venue narrowing reads.
- **GitHub:** #173

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
  just a different unexamined guess. On the parser (5.0.0) no model supplies
  a season at all, and it renders the current season the same way: "Rendered
  NetPoints fingerprint (total) for Nikola Jokic (2026 season, percentile
  scale)".
- **User sees:** a fingerprint titled with a real season, so the narrowing is
  visible (which is why this is P2 and not P1) - but the season shown answers
  a different question than "his best", with nothing saying so.
- **Next step:** either read "best season"/"his best year"/"career year" as a
  request the parser's grammar recognizes (a slot value, not an intent) and
  resolve deterministically
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
  41-31. The caveat in `compose/team_stats.py` (`read_team_outlook`) cannot fire, because it tests
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

### A named playoff round is refused - the games carry no round label, and the Finals are derivable
- **Found:** 2026-09-11, repo audit; **re-measured 2026-09-18** over the
  2,285-question large StatMuse set (`~/association-research/statmuse-2026-09-large/`):
  **124 of 2,285 (5.4%) mention the Finals in some form**, and a read of a
  sample says nearly all need the round `games` does not carry (Finals-only
  lookups, Finals game logs, "who won the Finals", Finals MVP). The set's own
  capability classifier flags 3 of the 124, because "needs a round" is not in
  its checklist - so this entry's share of real traffic is well above what its
  worked example suggested.
- **Evidence:** `check_scope` (the planner since 2026-10-05) raises on `round`, and `agent.py` then hands the
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
  `season_line.default_min_sample`) - extending it to two more floors nobody
  had measured would have been a guess, not a fix.
- **User sees:** a 3-point or free-throw percentage leaderboard for a
  shortened season (2020, 2021, the 2012 lockout season, and any earlier
  strike/lockout season) applies a stricter-than-published qualifier, the
  same way #13's three floors did before the fix.
- **Next step:** measure `three_pt_pct` and `ft_pct`'s qualifying counts for
  2020/2021/2012 against full seasons the way #13 was measured, then set
  `scales_with_schedule=True` on both in `_percentage()`'s callers
  (`query/metrics.py`) - the scaling mechanism (`season_line.py`,
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
- **Found:** 2026-09-11, shot-frame fix (shot agent); **cause found
  2026-09-29** (period relation work)
- **Evidence:** 1,165 player-games in 2026 have more field goal attempts in
  `shot_chart` than in the box score, 1,207 extra in all. 2026 alone types
  the end-of-period heave: 1,153 `Heave Jump Shot` rows, none made, and no
  other season has the type. Leaving the MISSED heaves out, 61 player-games
  (62 shots) are over - so the box score does not count a missed heave as an
  attempt, and ~95% of the excess is exactly that. The period line already
  leaves them out (`player_games._PERIOD_HEAVE_MISS`: 2026 attempts agree
  with the box score in 99.6% of player-games, against 95.8% with them).
- **User sees:** shot charts and shot-distance answers count 2026 heaves that
  are not attempts in the box score (Curry: 488 charted threes against 484
  3PA).
- **Next step:** the same exclusion in the shot reader (`query/compose/shots.py`;
  `shotchart.SHOT_VALUE_SQL`) - a chart may still want to draw a heave, but a count
  or an average should not include it; say which in the answer.
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
  in the `if not games:` branch of `_with_without_read`
  (`compose/presence.py`; the template retired into the compiler's
  `presence` group on 2026-09-30, and the reader kept the branch).
  Durant/Nets 2020 is the same. `player_season_stats` has **no row** for a
  season a player missed entirely, so it cannot supply tenure. Worse,
  `game_log` and `player_stat` say "Klay Thompson was not Stephen Curry's
  teammate in any of his 63 games" - the wrong-cause sentence built in
  `no_narrowed_games` (`query/player_relation.py`) - about a rostered, injured
  player.
- **GitHub:** #16

### `player_history` answers "last N seasons on record", not a calendar window
- **Found:** 2026-09-11, while fixing name clarification
- **Evidence:** the query reads `season <= ? ORDER BY season DESC LIMIT ?` per
  player (`season_line.history_statement`, read by `compose.seasons.read_player_history`), so a player with gaps, or
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
- **Evidence:** the chart's empty sentence (`compose.say.say_shot_chart`, `"No
  shots found for {name} with the given filters."`; `shotchart.render_for_player`'s
  empty branch until Phase 2, step 5) never mentions ``season`` at all -
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
- **Next step:** have `say_shot_chart`'s empty sentence say the season and
  season_type it queried (mirroring `_period`), and separately list which
  filters (if any) were actually applied, rather than a blanket "with the
  given filters" that fires even with none. Threading that through touches
  the sayer only (the Result carries the span).
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
    the whole question is refused even though every piece of scoping it
    actually needs is already sitting in `without`.
  - "Clippers ats record last 15 games at home" arrives with `team='Los
    Angeles Clippers'` (correct) and `opponent='home'` beside `venue='home'` -
    the same fact written twice, once as a bogus opponent. No `vs`/`against`
    phrase exists for `_scope_from_question_opponent` to correct it with, so it
    is left alone and fails to resolve as a team ("no team matching 'home'").
    This second one is not a clean fix even if `opponent` is dropped: "ats"
    means against-the-spread, which nothing in the warehouse stores, so the
    question is unanswerable on the stat alone regardless.
- **User sees:** a refusal for the first; the second would still
  need a separate refusal for the unsupported "ats" stat even if `opponent`
  were fixed.
- **Next step:** in `entities.py`, drop an `opponent` that (a) does not
  resolve as a team via `_team_named`, and (b) either duplicates names already
  present in `without`, or is literally the venue word already in `venue`
  ("home"/"away"). Not attempted here: the payoff on the second case is
  capped by the separate "ats" gap, and the first needs a decision about
  whether dropping `opponent` outright is safe versus trying to fold it back
  into `without` (already correct) - a judgment call better made alongside
  whichever reader's `STATED_SCOPING` row (`compose/plan.py`; `HONORED_SCOPING` until 2026-10-05) actually reads these two rows.
- **Source:** ours, not ESPN's.
- **GitHub:** #118

### A quarter or half is answered for a player or a team, and for nobody else
- **Found:** 2026-09-16 auditing the feed; the player half shipped the same
  day as `period_split`; **re-scoped 2026-09-29** when the period became a
  narrowing of the player-games relation, and again the same day when it
  became one of the team-games relation too (ROADMAP plan item 4)
- **What is answered now:** a named player's quarter or half for any column
  the period's line rebuilds from the shots and plays (points, FG, FT, threes,
  rebounds, assists, steals, blocks, turnovers, fouls -
  `player_games.PERIOD_COLUMNS`), per game or averaged, under every relation
  narrowing `period_split` honors; a ranking of players by any of those
  columns in one quarter or half (`period_leaderboard`); every player's four
  quarters side by side (F048); and a TEAM's quarter or half for the same
  columns ("trailblazers stats last 10 games 3 point average 1st quarter",
  F065; "how many first-quarter rebounds do the Knicks average"), its points
  from the linescore and the rest from its players' period lines plus its own
  plays (`team_games.team_period_line_sql`), caveated per season by
  `team_games.TEAM_PERIOD_AGREEMENT`.
- **What is still not answered:**
  - **A team's per-quarter figure in the seasons its play-by-play does not add
    up** - refused, naming the season and the measured agreement: 70 of 375
    season-columns, 60 of them 2002-2006, and in 2007+ field goals in 2013
    and 2016, turnovers in 2007, 2008, 2013 and 2016, rebounds in 2013,
    assists in 2016, steals in 2013 and 2018, fouls in 2018 (DATA.md, "A
    team's play-by-play does not add up to its box score in six seasons").
  - **A position group as the subject** ("each center 1q pts log vs nugget") -
    the same gap position groups have everywhere.
  - **A team's record, series or ranking in one quarter** (`team_record`,
    `head_to_head`, `team_leaderboard` exclude `period`/`half` in
    `TEAM_RELATION_SCOPING_EXCLUDED`: a quarter has no winner to count) - by
    design, not a gap to fill.
- **User sees:** for the shapes above, a refusal naming the cause - no longer
  a whole-game line where one quarter was asked for.
- **Next step:** none open here. Built 2026-09-30: the shooting percentage
  (`player_games.PERIOD_RATES`, a ratio of the period's sums for a
  player and a team; a period RANKING by one is refused by name, since a
  games-played minimum is not an attempts minimum), a named player's four
  quarters side by side (`compose.core._compile_by_period`, the `period`
  group; #162 closed) and a period as a condition on which games count
  (`Scope.period_condition`, read by `parse.read_period_condition` and
  applied by `Narrowed.narrow_period_condition`; #275 closed). What a team
  cannot do in one quarter (a record, a series, a ranking) stays by design.
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
- **User sees:** since (a) below landed, the refusal: `coach` is assigned
  from the question's words (`CODE_ASSIGNED_INTENTS`, and the parser's
  `PARENT_GRAMMAR`) and answers that no table here holds a coach and why
  ESPN's are not worth reading. Before it, a fall-through to an agent with no
  coach column, free to fill the silence from its own weights.
- **Next step:** a decision, not a fetch. Either (a) leave it unfetched and
  refuse a coach question with a sentence naming the real cause - done, with
  no prompt edit, since the intent is read from the words; or (b) fetch the
  team-scoped endpoint and caveat it hard, which means publishing a coaching
  record that is wrong about Detroit for two decades. (a) is the cheaper and
  more honest of the two; close this unless (b) is wanted.
- **Source:** DATA.md, "ESPN publishes coaches, and the collection that looks
  league-wide is not historical"
- **GitHub:** #97

### Each narrowing the router has no slot for needs its own regex
- **Found:** 2026-09-15 replaying 261 real StatMuse feed queries through the
  fast path; **fixed for every measured case and re-ranked P1 -> P3 on
  2026-09-16**, after the second pass measured zero left.
- **What it was.** `check_scope()` (the planner's check against `STATED_SCOPING` and the relation's cells since 2026-10-05, `compose/plan.py`) refuses a narrowing a template cannot
  honor, but it can only see slots the reader emits, and the router's schema
  had no slot for a weekday, a holiday, an age, a minutes condition, "since
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
  so `check_scope` refuses and the question is refused. Every
  alternative was perturbed individually and watched to fail.
- **Measured across two replays:** fluently wrong 44 (17%) -> 33 (13%) -> **29
  (11%)**, and **correct is 67 (26%) in all three runs** - 15 wrong answers
  removed without losing one right answer. **No wrong answer in the sample
  drops a condition any more**; what is left is wrong entity (11), named player
  dropped (8), wrong metric (6) and wrong scope (4), all different entries.
- **Why it is still open, at P3.** The fix is a list of regexes, one per shape
  somebody happened to ask in a 261-query sample. The structural fault is
  untouched: **`check_scope` still cannot refuse what the reader never
  emits** (the router's schema until 5.0.0; the parser's grammar tables and
  the stages since), so the next narrowing nobody has thought of is dropped silently and
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
- **Evidence:** `SCOPING_SLOTS` (`reading.py`) against `HONORED_SCOPING` (`compose.plan.STATED_SCOPING` since 2026-10-05):
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
  honor no `opponent` (`HONORED_SCOPING`, `compose.plan.STATED_SCOPING` since 2026-10-05), so "compare
  curry and lebron vs the celtics" refuses on the template path and falls
  through. Before the fix that moved the Celtics out of `team`, it compared
  the two players' whole 2026 seasons. `_narrow_player_games` already builds
  one player's box-score line against one opponent for `player_stat`.
- **Re-checked 2026-09-16: no longer a one-off construction - six feed
  questions are refused on it in the latest replay**, all on
  `player_matchup`/`player_compare cannot honor ['opponent']`: "sam hauser v
  mil", "Curry vs dallas last q0 games", "julius randle stats vs blazers with
  minnestota", "oubre vs warriors without embiid", "de'aaron fox vs magic
  ...", "stating centers vs suns". Commit `d7a8db1` does not touch this shape.
- **Fixed 2026-09-18 (partial, `player_matchup` only):** the router routes
  these as a fake two-player matchup - one real name plus a team it could not
  place anywhere else - not a genuine comparison, so `player_matchup` now
  recognizes exactly one player name plus a team `opponent` and answers it the
  way `game_log` answers "player vs team" (`HONORED_SCOPING["player_matchup"]`
  and the new branch at the top of `player_matchup`, `query/templates/games.py` then; `compose/pairs.py` since Phase 2).
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
  `_player_matchup_drop_fabricated_second` (`query/templates/games.py`, deleted with it; the pair is read by `compose/pairs.py` now)
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
  question, a separate gap) are still refused.
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
  nothing: `entities.py` can name the entity correctly, but the scope check (the planner since 2026-10-05)/the
  handler still has no column or shape to answer from. ("oklahoma city thunder
  all-time triple doubles vs west" looked like a third instance but is not -
  "vs west" is Western Conference scoping, which is #25's gap, not this one.)
- **User sees:** a refusal for both, on the router.
- **Parser path (5.0.0):** the kind reading routes a bare team subject to a
  team-shaped intent. "cavaliers 3 pointers every game" (normalizer stubbed
  with the team's span and `threePointFieldGoalsMade`) answers `team_stat`:
  "The Cleveland Cavaliers' 3-pointers made per game was 14.3 in the 2026
  regular season (82 games), 8th-best of 30 teams." - an average, where "every
  game" may want the log; "rebounds allowed per team" names a key no team
  metric holds and refuses by name.
- **Next step:** not an `entities.py` fix, and no longer a routing one: the
  missing shapes (a team's per-game breakdown of one stat; `team_leaderboard`
  ranking every team by a counting stat it allows).
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
  first and was handed to an agent (since removed) with no conference data either.
- **User sees:** a refusal for "who leads the East", and (before the agent
  went) an ungrounded agent answer for "Western Conference standings".
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
  `team_relation.py`) narrow to games against an opponent aligned that way
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
  "Western Conference standings").** Re-measured 2026-10-09: "who leads the
  east" is no longer refused - it answers the whole league (P1, "A conference
  or division the players or teams belong to is answered as the whole
  league"), and `team_record` does NOT read the opponent narrowing this entry
  lists it under (P2, "A team's record against a conference or division is
  refused as 'no calendar narrowing'"). Unchanged by this fix: `_conference_refusal`
  (`templates/teams.py` then; `calendar.conference_named` and the sayer's `conference_named` phrase, from `compose/team_records.py` and `team_stats.py`, now - its sentence said "no conference or division membership" until 2026-10-09 and names the tally that is missing now; no reading of the 2,710 puts a conference in a team slot, measured that day) still refuses a `team`/`opponent` slot that names a
  conference or division, and "Western Conference standings" was still handed
  to an agent with no conference data grounded for it either, because
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
- **User sees:** a refusal naming the stat. The wrong 3P% answer this used to
  give is fixed.
- **Next step:** add TS% and eFG% to `player_stat` as computed ratios, like
  `SHOOTING_STATS`.
- **Re-checked 2026-09-15: wider than filed.** `player_stat` refuses
  `ts_pct`/`efg_pct` for a single season too, not only a career, although
  `player_season_advanced_stats` holds both per season.
- **GitHub:** #26

### Fingerprint for a specific date
- **Found:** before 2026-09-11 (docstring)
- **Evidence:** the fingerprint template in `query/templates/netpoints.py` said "... but not
  yet for a particular date" (its reader is `compose/netpoints.py` since Phase 2, which draws a first or most recent game only). `game_log` already honors `date`.
- **User sees:** a helpful refusal.
- **Next step:** resolve the date to the player's game with `_eastern_day`, then
  draw the single-game fingerprint.
- **GitHub:** #29

### Franchise career leaderboards
- **Found:** before 2026-09-11 (docstring)
- **Evidence:** the leaderboard in `query/templates/players.py` said "Refused until that
  is decided" (`compose.rankings._leaderboard_career_refusals` since Phase 2). The rule for relocated franchises is open.
- **User sees:** a refusal for "timberwolves career leaders in total points".
- **Next step:** decide the relocation rule, then map it.
- **Re-checked 2026-09-15:** the "User sees" is wrong. `_career_leaderboard`
  raises `TemplateUnsupported`, which is a plain refusal naming the slot, not
  a refusal the user can act on.
- **Re-checked 2026-09-16: the relocation rule is no longer open.** The refusal
  is still at `query/compose/rankings.py` since 2026-10-05
  (`_leaderboard_career_refusals`, raising
  `TemplateUnsupported("franchise career leaderboards are not supported")`;
  `templates/players.py`'s `_career_leaderboard` until Phase 2's slice (iii)),
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
  NetPoints and paint or fast-break points are refused naming only the slot.
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
- **User sees:** a refusal naming only the slot.
- **Next step:** take them in that order.
- **Source:** DATA.md, "No conference, division or birth-date data anywhere"
  (`DATA.md:376`, corrected 2026-09-15) - the birth-date half only; conference
  and division are now #25's finding, not this one's.
- **GitHub:** #32

### A router-invented name one edit from a real one is refused instead of asking
- **Found:** 2026-09-11, probing the season-narrowing branch
- **Evidence:** "how many rebounds does davis average" routed to `player_stat`
  with `player='Davies (Davic)'`. `override_invented_players` counts it as
  grounded, since "davies" is one edit from "davis". Nothing matches it, and
  `suggest_players` offers nobody, so `_resolved_player` raises
  `TemplateUnsupported`. The likely reason the suggestion pass finds nobody is
  the parenthesized second token, since every token must be near some word of
  the name. That was not confirmed. Seen once.
- **User sees:** the question is refused, rather than asking which Davis
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
- **User sees:** a refusal that names the wrong cause; a person reading
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

### No future template gets a renderer for free
- **Found:** 2026-09-18, scoping study requested ("richer HTML answers -
  cards, sparklines, tables"). Originally filed together with
  "`player_netpoints` still renders as a `<pre>` block", which is now fixed
  (see below) - what remains is the design point the old title's second
  half named, so the entry is narrowed to that rather than closed outright.
- **Status:** stage 1 (a renderer per existing shape) shipped 2026-09-18.
  `player_netpoints`, the one intent stage 1 left as a `<pre>` block, got its
  own renderer 2026-09-25 (`RENDERERS.player_netpoints`,
  `web/static/index.html`): a stat card for the season totals
  (`data.totals`) plus an Offense/Defense table of the six partition
  categories and a table of the overlapping play-type detail, all read
  straight off `data.fingerprint`'s own stable keys (each row now also
  carries `partition: bool`, so the page and the printed CLI table split
  offense/defense the same way without a second copy of
  `FINGERPRINT_PARTITION`). `data.headline` is now the display sentence
  (`answer.split("\n")[0]`), not the raw six-number SQL row it used to be
  stored under that key, and `data.notes` carries the season line's own
  minutes/games/per-100-rate detail, what units the category rows are in,
  and the play-type overlap disclaimer -
  `query/templates/netpoints.py`'s `_netpoints_totals`/`_netpoints_notes` then; `compose/netpoints.py` and `compose.say._netpoints_totals` since Phase 2.
  Not the radar-chart route the entry originally proposed as "stage 2": its
  own stated risk (reusing `radar.py`'s per-game-vs-per-100 `Unit` labeling
  for a season aggregate without checking the scale first) is exactly the
  failure shape this project keeps producing, and a table read off
  `fingerprint`'s own keys carries none of it. `RENDERERS` now covers all 24
  `TEMPLATES` entries but `coach`, which stays prose by design (nothing
  structured to show).
- **What remains is a design point, not a fix for anything wrong today.**
  Every one of the 24 typed intents already has a hand-written renderer, so
  nothing is currently stuck behind a generic fallback - the gap is only for
  the NEXT template. Today, shipping one with no `RENDERERS` entry falls
  through to `el("pre", null, a.text)` - correct, but back to unstyled
  terminal text - rather than drawing a table/card for free from its own
  `data` shape, the way the original request asked for once and for all.
  1. **A generic fallback formatter** for any `data` dict with no
     `RENDERERS` entry: flat scalar keys as a small card grid, and any
     list-of-dicts key as a table, generically, in `renderBody`'s `null`
     branch. **Risk:** a generic renderer is more likely to produce a
     confusing layout for a shape nobody has looked at (the recurring
     failure shape here is a too-narrow answer, and a too-generic one is the
     same risk turned around) - ship it behind the same `try/catch` fallback
     to `<pre>` so a bad generic render never loses the correct sentence,
     and look at a handful of real answers per intent before trusting it.
  2. **Refusal-shaped payloads stay prose**, excluded from the generic
     renderer explicitly (a `message`/`unanswerable` pair, or any answer
     with `answered_by != "fast"`) rather than letting it try and fall back
     silently - a silent fallback reads as "nothing to show" when the real
     content is the sentence itself.
- **User sees:** nothing wrong today - every current intent has a renderer.
  The gap only reaches a reader once a new template ships with no
  `RENDERERS` entry of its own.
- **Priority note:** P3 (placed beside #69, the page's other structural
  gap) - a design point, not a bug; no template today is affected.
- **Could not verify:** whether a generic fallback formatter looks good for
  every shape in practice needs eyes on real output rather than a data-shape
  read, and is unstarted.
- **GitHub:** #110 - heading changed from "`player_netpoints` still renders
  as a `<pre>` block, and no future template gets a renderer for free" to
  this one; the issue's title needs the same update
  (`scripts/sync_issues.py` only opens issues for headings with no
  `- **GitHub:**` line yet, so a rename here does not retitle it).

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
- **User sees:** a refusal for any question naming one —
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

### A career-span `shot_distance` drops an unseparable season and loses the derived-season caveat, silently
- **Found:** 2026-09-20, fixing #141 (`shot_chart`/`shot_distance` honoring a
  career `span`)
- **Evidence:** one named season already refuses a `shot_value`-filtered
  distance in `UNSEPARABLE_SHOT_VALUES` (2002) and notes a
  `DERIVED_SHOT_VALUES` one (2003, 2022) - `compose.shots.read_shot_distance`
  (`templates/shots.py`'s `shot_distance` until Phase 2, step 5), both checks keyed on `season`. A career span (`span:
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
  this gap - `compose.shots._shot_chart_notes` already aggregates both
  notes as a set over every season a multi-season draw actually kept.
- **User sees:** a career average with no shot_value filter reads fine; one
  WITH a filter (threes, twos) whose career overlaps 2002 or 2003 gets a
  number that is short of the truth (2002 silently thinned) or unexplained
  (2003 unlabeled by caveat), with no sign either happened.
- **Next step:** in `shot_distance`'s career branch, collect the distinct
  seasons actually kept by the query (a `season` column is already selectable
  alongside the aggregate) and build the same two notes
  `compose.shots._shot_chart_notes` does, or factor that helper out for
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
- **User sees:** "game_log needs a team or a player" - a refusal - for the
  one franchise whose name starts with a digit.
- **Next step:** keep digits inside a word in `_words` (or fold "76ers" to
  "sixers" in `_fold`), then re-run the entity golden comparison: `_words`
  feeds `players_named_in`, where a stray number must not start naming people.
- **GitHub:** #176

### A name written without its periods matches nobody ("Pj washington")
- **Found:** 2026-09-21, live sample
- **Evidence:** `find_players(con, "Pj Washington")` is empty;
  `find_players(con, "P.J. Washington")` finds athlete 4278078.
  `players_named_in` also finds nobody in "Pj washington vs pacers game by
  game". 22 of 1,972 reasonable large-set questions use an initial-pair name
  (`\b(pj|cj|tj|aj|rj|kj|dj|og|jj)\b`); some ("CJ McCollum") are stored
  without periods and work.
- **User sees:** "no player matching 'Pj Washington'".
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

### A subject `game_log` was not given is not restored from the question
- **Found:** 2026-09-21, live sample
- **Evidence:** "Luka doncic last 5 away hames" routes to `game_log` with no
  `player`; `players_named_in` returns `['Luka Doncic']`, `scope_from_question`
  changes nothing, and the template raises "game_log needs a team or a player".
  Not traced further. 7 corpus rows and 3 sample rows end on that message; most
  of the others are names nobody typed correctly, which nothing can restore.
- **User sees:** a refusal naming only the slot.
- **Next step:** find why `_scope_from_question_restore_player` does not fire
  when neither `player` nor `team` is set.
- **GitHub:** #181

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

### A double-double count routed player_stat with no stat is answered with the default line, and "career" is lost
- **Found:** 2026-09-26, the season-line parity run (`parity.py`, the
  `subject-kinds` tree, the main warehouse).
- **Evidence:** yardstick-v2 F055 "alperen sengun double-doubles vs
  southeast division career away", on its recorded day2 route
  (`player_stat`, `situation: "vs southeast division"`, `venue: "away"`, no
  `stat`, no `span`): `player_stat` answers "Alperen Sengun averaged 16.6
  points, 8 rebounds and 4.2 assists per game in 5 games on the road against
  the Southeast Division in the 2026 regular season" - the default line for
  one season, where the question asked for a count of double-doubles over a
  career. The compiler on the same slots says "had 8 games with a
  double-double ... in the regular season career (2022-2026)", the key's
  answer (the 8 of 22 verified under the K3-2 conference/division fix). The recorded HISTORY route
  of the same question (`player_splits`, `situation: "division"`) refuses
  instead with "'division' names a conference or division, but not in a
  shape this reads - try ... \"vs the southeast division\"", which asks the
  user to type what they typed.
- **User sees:** a wrong answer, fluently, on the day2 route; a refusal
  naming the wrong cause on the history route. Which route the current
  router gives is a live-run question (not measured here: no ollama).
- **Next step:** `player_stat` (and `game_log`) should raise
  `TemplateUnsupported` when the question's words name a boolean measure
  its line does not carry (`compose.move.BOOLEAN_MEASURES` via
  `_measure_words`), so the compiler's count answers it; and the
  non-calendar-situation refusal should quote the question's own phrase,
  not the slot's one word.
- **Source:** ours, not ESPN's.
- **GitHub:** #229

### A league ranking by 2-point percentage is refused as an outside figure; five other box-score keys the normalizer can emit rank only through the compiler, under raw column names
- **Found:** 2026-09-27, plan item 6 step (d) part 3, re-aiming `tests/query/test_career_spans.py`'s vocabulary test from the router's prompt to `NORMALIZER_STATS`.
- **Evidence:** six player keys in `NORMALIZER_STATS` resolve to no leaderboard metric (`leaderboard.resolve_metric` returns None): `fieldGoalsAttempted`, `freeThrowsAttempted`, `threePointFieldGoalsAttempted`, `offensiveRebounds`, `defensiveRebounds`, `twoPointFieldGoalPct`. Parser path offline on 33cfd60 (and before it on 96b4b65), normalizer stubbed with the key, read-only on the main warehouse: the template refuses each and the compiler ranks five from box scores - "who leads the league in offensive rebounds this season" answers "every player, 2026 regular season, by player (offensiveRebounds per game, minimum 20 games): Steven Adams ... 4.5", the column's own name as the label (the attempts read "FTA", "FGA", "3PA") - and refuses the sixth: "who has the best 2 point percentage this season" answers "No ranking reads 'twoPointFieldGoalPct' on the player-games relation - it only ranks the box-score measures it knows, not a NetPoints or other outside figure." A 2-point percentage is a box-score figure (field goals less threes; `player_history` and `player_stat` already compute it), so the refusal names the wrong cause.
- **User sees:** a refusal naming the wrong cause for a 2-point-percentage ranking; for the two rebound halves, a right ranking labeled "offensiveRebounds per game".
- **Next step:** leaderboard metrics for the six (2-point percentage with an attempts floor the way `fg_pct` has one); then `test_every_box_score_stat_the_model_may_name_ranks_by_a_metric` can read `NORMALIZER_STATS` itself rather than the router's fourteen names.
- **Source:** ours.
- **GitHub:** #249

### A single-game high tied between two games of one player names him twice: "Stephen Curry and Stephen Curry tied for the most 3-pointers in a single game"
- **Found:** 2026-09-27, checking the answers the #168 fix moved.
- **Evidence:** stubbed offline through the whole agent (main warehouse): "most 3 pointers made in a single game 24-25" answers "Stephen Curry and Stephen Curry tied for the most 3-pointers in a single game in the 2025 regular season, with 12 each. Next: Damian Lillard (10)." Curry made 12 on 2025-02-27 against Orlando and on 2025-04-01 against Memphis (Eastern dates, `player_game_log`). The tie sentence (`compose.say._single_game_high_phrase`; `templates/players.py`'s `_single_game_high_answer` when found, which the compiler answers `single_game_high` with) joins the tied rows' player names and gives no date, so one player's two games read as a typo; and "Next" names one of the seven players who made 10 that season, the cut at a tie #99 already records.
- **User sees:** a right number in a sentence that reads as a mistake, with neither game's date.
- **Next step:** in the tie branch, name a player once with each of his games' dates ("Stephen Curry, twice - 12 on 2025-02-27 vs ORL and 2025-04-01 vs MEM"), and the dates beside several players' names; a case with one player's two tied games.
- **Source:** ours.
- **GitHub:** #262

### An apostrophe typed inside a name's word splits the name, and the question is answered without its player
- **Found:** 2026-09-28, fixing #259 - the one research-corpus question the fold moved from a right answer to a wrong one.
- **Evidence:** stubbed offline through the whole agent (main warehouse, names `["jo’sh hart", "philadelphia"]`): before #259, Josh Hart's last 3 games against the 76ers; after, the league read, "No games for every player vs the Philadelphia 76ers in the 2026 regular season" - what "jo'sh hart" with a straight apostrophe answered all along. The entity index's accent fold (`entities._fold`, NFKD to ASCII) drops a U+2019 outright, so "jo’sh" read as "josh"; a straight apostrophe splits the word (`entities._words`), so "jo'sh hart" is the words jo, sh, hart, which no player holds, and its near spellings are three players (Isaiah Hartenstein, Jason Hart, Josh Hart), which ask rather than default. Every name that really carries an apostrophe (32 players, "D'Angelo Russell", "De'Aaron Fox") splits the same way on both sides and matches. <!-- codespell:ignore hart - Josh Hart's surname -->
- **User sees:** a refusal-shaped answer naming the wrong cause ("no games for every player"), the player dropped.
- **Next step:** in the entity index, read a word with an apostrophe inside it (not a possessive at its end) joined as well as split - "jo'sh" as "josh" - the way the accent fold happened to read a typographic one; a case in `tests/query/test_entities.py`.
- **Source:** ours.
- **GitHub:** #271

### A player's advanced stat in one 2013-2018 season is refused naming the wrong cause, with a ranking's caveat appended
- **Found:** 2026-09-30, the notes inventory for `ROADMAP-TYPES.md` (an Opus agent reading `011091f`; reported, not re-verified).
- **Evidence:** `compose.say` (the season line's advanced-stat sentence; `templates/players.py:1419-1423` when found) says the stat "is computed from box scores, which start in 1994" where the value is NULL because that season's box scores are empty; `coverage_caveat` then appends `nba/coverage.py:250-257` ("missing from this ranking entirely") to a one-player lookup.
- **User sees:** a refusal that sends him to the wrong fact, and a note about a ranking he did not ask for.
- **Next step:** reproduce with a Chicago or New Orleans player's usage in 2015; name the empty box scores as the cause.
- **GitHub:** #293

### A coverage caveat is the first declared table's, whoever the answer is about: a player's 2001 playoff quarter gets the team-worded games note
- **Found:** 2026-09-30, the notes inventory for `ROADMAP-TYPES.md` (an Opus agent reading `011091f`; reported, not re-verified).
- **Evidence:** `nba/coverage.py:517` returns the first source's note; `period_split` and `period_leaderboard` list `shot_chart`, `games`, ... (`coverage.SOURCES`), so a 2001 postseason player answer says "Philadelphia's run reads 16 games ... a series can look shorter" - wording the module's own comment (`coverage.py:192-193`) calls wrong for a player. Same family as "A composed team season total carries a caveat about a different table".
- **User sees:** a caveat about a different table than the one his number came from.
- **Next step:** the caveat goes with the relation the answer read (`ROADMAP.md`, a `partial_season(table, season)` note from RUN), not with the intent.
- **GitHub:** #294

### The unseen-games note names 2013-2018 as the cause whatever seasons the games are in
- **Found:** 2026-09-30, the notes inventory for `ROADMAP-TYPES.md` (an Opus agent reading `011091f`; reported, not re-verified).
- **Evidence:** `conditions.py:389` always says "ESPN lacks about one game in eight from 2013 to 2018"; over the filled view those seasons are mostly rebuilt (`conditions.py:321-326`), so the unseen games may be elsewhere. Needs a measurement: which seasons the unseen games of a recorded split fall in.
- **User sees:** a cause that may be false for his span.
- **Next step:** measure over the corpus's splits; say the seasons the unseen games are in.
- **GitHub:** #295

### A streak in a finished season is marked "still going at the last game on record"
- **Found:** 2026-09-30, the notes inventory for `ROADMAP-TYPES.md` (an Opus agent reading `011091f`; reported, not re-verified).
- **Evidence:** `open` means the run reached the partition's last game (`conditions.py:811,835`). `_single_streak` guarded on the current season (`templates/splits.py:1560` when found; the streak is `compose/runs.py` and `compose.say` now, with `Run.still_open`); `templates/splits.py:1526` and `compose/sentence.py:285,301` do not.
- **User sees:** "still going" beside a streak that ended with its season.
- **Next step:** reproduce with a league-wide streak ranking for 2024; guard the two unguarded writers.
- **GitHub:** #297

### The game-list caveat says a narrowed record "is off by those games"
- **Found:** 2026-09-30, the notes inventory for `ROADMAP-TYPES.md` (an Opus agent reading `011091f`; reported, not re-verified).
- **Evidence:** `compose.team_records._game_list_gaps` (`templates/teams.py:967,803` when found) ignores opponent, venue, month, calendar and game_n, so a record against one opponent or in one month in a season whose list is short says "this tally is off by those games" though the missing games may be none of the ones counted.
- **User sees:** a caveat claiming an error the answer may not have.
- **Next step:** say the season's list is short by N games and that they may fall outside the narrowing, or check whether they do.
- **GitHub:** #298

### A postseason NetPoints answer prints the whole season's play-type split under a postseason label
- **Found:** 2026-09-30, the notes inventory for `ROADMAP-TYPES.md` (an Opus agent reading `011091f`; reported, not re-verified).
- **Evidence:** the season fingerprint table has no season type (`compose/netpoints.py`, `templates/netpoints.py:98-102` when found; `fingerprint.py:457,979-980`); its sayer (`compose.say`'s NetPoints phrases; `templates/netpoints.py:113,331` when found) labels the answer "{season} postseason" and `fingerprint.py:853` captions the plot "{season} season". Nothing says the split is the whole season's.
- **User sees:** a play-type split labeled as the playoffs' that is not.
- **Next step:** confirm against a playoff NetPoints question; say the split covers the whole season, or refuse the split for a postseason.
- **GitHub:** #299

### A team's record by month silently drops seasons under the games floor, and never carries the gap note
- **Found:** 2026-09-30, the notes inventory for `ROADMAP-TYPES.md` (an Opus agent reading `011091f`; reported, not re-verified).
- **Evidence:** `compose.team_records._by_month_span` (`templates/teams.py:1179-1180` when found) skips seasons below the floor with no remark; the by-month tables (`_by_month`, `_by_month_span`) never call `_game_list_gaps`.
- **User sees:** a span of months that leaves seasons out without saying so.
- **Next step:** reproduce with an all-time by-month record; say which seasons the table starts from.
- **GitHub:** #300

### A league-wide compiled answer carries no box-score caveat
- **Found:** 2026-09-30, the notes inventory for `ROADMAP-TYPES.md` (an Opus agent reading `011091f`; reported, not re-verified).
- **Evidence:** `compose/core.py:899-900` reads the notes only for a named player; the `threshold_count` presenter did say them league-wide (`templates/players.py:259` when found; `compose/counts.py` and `compose.say.say_threshold_count` now).
- **User sees:** a league-wide count or list over 2013-2018 with nothing saying games are empty or rebuilt.
- **Next step:** measure on "most 40 point games 2013-2018" both ways; the note belongs to the relation's read, not to one presenter.
- **GitHub:** #303

### A surname that is also a word loses the subject, and the refusal names a slot: "since 1/26/20, what are the towns home records including playoffs against the spurs?"
- **Found:** 2026-09-30, the review of `ROADMAP-TYPES.md` (an Opus agent over the stage snapshot of the 628 recorded questions at `7f6425b`); the answers quoted were re-read from the snapshot.
- **Evidence:** the subject reads as `everyone` ("towns" lowercase after "the"), the intent as `leaderboard`, and the answer is "leaderboard: the relation cannot honor ['season_type_unstated'] - it would answer for a different span than was asked." The wording "towns home rec including playoffs since 1/26/20 vs spurs" answers.
- **User sees:** a refusal that names an internal slot, about a question whose player was never read.
- **Next step:** the wrong cause is #281's shape (a refusal for an unhonored narrowing prints identifiers); the dropped subject is the reader's.
- **GitHub:** #304

### "consecutive" is read as a streak: "Display Luka's average assists for each consecutive year" is refused as a streak with no threshold
- **Found:** 2026-09-30, the review of `ROADMAP-TYPES.md` (an Opus agent over the stage snapshot of the 628 recorded questions at `7f6425b`); the answers quoted were re-read from the snapshot.
- **Evidence:** the parser's decision is "the words 'consecutive' name streak", `kind: win` is written with nothing in the question naming a win, and the answer is "streak: a streak of a stat needs both a known stat and a positive threshold, got 'assists'/None." The question is a history by season.
- **User sees:** a refusal about a streak he did not ask for.
- **Next step:** "each/every consecutive year|season" is a history; a case in `tests/query/test_subject.py`.
- **GitHub:** #305

### A ranking in a player's Nth season answers the current season only: "Most points in 15th season played"
- **Found:** 2026-09-30, the review of `ROADMAP-TYPES.md` (an Opus agent over the stage snapshot of the 628 recorded questions at `7f6425b`); the answers quoted were re-read from the snapshot.
- **Evidence:** answers "every player in their 15th season, 2026 regular season, by player (points per game, minimum 20 games)" - Jimmy Butler 20.0. The question reads as all-time (the most points anyone scored in his 15th season). The season is stated, and no wording is offered that reaches every season.
- **User sees:** this season's players in their 15th year, where the best 15th seasons ever were asked for.
- **Next step:** Jeff's call on the default (a `season_n` ranking with no season named reads every season); possibly known.
- **GitHub:** #306

### A player's quarter "over his last N games" answers his whole season when this season holds more than N
- **Found:** 2026-10-04, porting `period_split` to a reader (`compose/periods.py`, Phase 2 step 1e), on the warehouse at `/home/jeff/code/association/nba.duckdb`.
- **Evidence:** the point `{player: Giannis Antetokounmpo, period: 1, order: recent, limit: 5}` answers "Giannis Antetokounmpo scored 227 points in the 1st quarter over 36 games of the 2026 regular season, averaging 6.3." - before and after the port. The period read is every game of the span by design (`test_a_period_log_takes_the_end_of_the_season_the_question_asked_for`: "The header still answers the whole season either way"); a window only picks which games the log beneath shows, and with no "log"/"by game" in the question (`per_game` false) nothing shows them. The cross-season redirect (`_period_redirect`) DOES read the window, but only when this season holds no games at all - so "last 5 games as a starter" is 5 games for Zach Collins (no 2026 starts) and would be the whole season for anyone with starts.
- **User sees:** a season figure for "last 5 games" - the count of games is in the sentence, so it is visible, but no wording reaches the five games.
- **Next step:** Jeff's call, since the whole-season header is a tested decision: read the window (`relation_window`) as the games the figure is over, as the by-quarter read already does through the compiler ("over his last N games"), and say it in the header. Moves the answers of any recorded question with a window and more games than it in the season (none of the 19 today).
- **Priority note:** P2 (misleading).
- **GitHub:** #320

### A team's quarter over its last N games is refused as an unknown player: "display the first quarter scores for the Sixers' most recent 10 games" answers "No player found matching 'first quarter'"
- **Found:** 2026-10-01, reading the subject once (ROADMAP Phase 1), in the stage snapshot of the 628 recorded questions at `7394ac5`; the answer is the same before and after that change.
- **Evidence:** the reading's scope is `{'player': 'first quarter', 'period': 1, 'order': 'recent', 'limit': 10, 'stat': 'points'}` under `period_split`, a player's intent, though the subject is the team (`kind: team`, Philadelphia 76ers; the normalizer's names were `["Sixers"]`). The answer is "No player found matching 'first quarter' - did you mean Tim Quarterman?". The wording "show sixers first quarter scoring for their last 10 games" answers the team's quarter (`team_quarter_points`).
- **User sees:** a refusal about a player he never named.
- **Next step:** find what writes the words "first quarter" into `player` (the parser's last step restores "the one player the question names" for a player-required intent); a team subject with a quarter is `team_quarter_points`, never `period_split`. A case in `tests/query/test_parser.py`.
- **GitHub:** #314

### A team's record against a conference or division is refused as "no calendar narrowing"
- **Found:** 2026-10-09, Phase 3 step 0, probing `conference_named`
- **Evidence:** on `4b9c254`: "celtics record vs the west", "celtics record against western conference teams this season" and "celtics wins vs the southeast division" are refused "Nothing here answers this question: team_record: no calendar narrowing in situation 'vs the west' - a weekday, a month, a holiday or "since <day>" is read; an age, a conference or a division is not" (`compose/team_records.py`, `_team_record_month_and_split`). The relations read the same words as an alignment narrowing ("tatum points per game vs the west" answers 18.9 in 7 games), and #25 lists `team_record` among the shapes that read it - it does not.
- **User sees:** a refusal saying a conference is not read, about a narrowing the team relation applies.
- **Next step:** let `read_team_record` take an alignment `situation` through the team relation's tally (`TeamNarrowed.narrow_alignment`) rather than the standings line; until then the refusal says the record reader's own gap, not that conferences are unread. Correct #25's list in the same change.
- **GitHub:** #341

### The team-only player check reads a month or an ordinary word as a player
- **Found:** 2026-10-09, Phase 3 step 0 (`~/association-research/stages/refusal_sites.py`)
- **Evidence:** 3 of the 2,082 feed answers (no names, no stat, as `run --feed` asks them): "curry playoff stats from may 10th 2019 to april 30th 2023 including record and ts% ..." and its "may 13th" twin are refused "This was read as a question about Sean May, a player, but team leaderboard has no reading for one ..."; "Nba curry most 3PM in Jan 3 2019" names Jan Vesely. `subject.player_named_on_a_team_only_question` reads `players_named_in` raw ("may" and "jan" are whole surnames) while the subject reading drops a dictionary word nobody routed (`subject._question_players`, `_dictionary()`), so two readers of "who the words name" disagree; `_named_only_by_a_common_word` knows only "best" and "head". With the normalizer's names ("curry") production reads these as Curry questions, so the names-less population is where it shows.
- **User sees:** a refusal naming a player the question never meant (the wrong cause).
- **Next step:** read the team-only check's player from the subject reading's own players (`Reading.subject`), which already excludes ordinary words, rather than a second `players_named_in` pass; re-measure the three.
- **GitHub:** #342

## P3: refusal or gap

### A team's log drops a calendar, quarter or half narrowing silently
- **Found:** 2026-10-05, porting the team log onto the team compiler (Phase 2, step 4)
- **Evidence:** `compose/logs.py` `read_team_log` narrows the team's games with `Scope(venue=...)` and the opponent/date alone (`team_games`), while `compose.plan.STATED_SCOPING["game_log"]` declares `situation`, `period` and `half` as stated - so a team log asked "in January" or "in the 4th quarter" lists the whole span under a heading that does not say so. It was the retired template's behavior too; not measured on any population yet.
- **User sees:** a whole-season team log for "knicks games in january" with no mention of January (a narrower question answered broader - the failure shape at the top of AGENTS.md), if any such question reaches the log.
- **Next step:** count the readings whose team log carries one of the three cells (`~/association-research/stages/reader_pop.py` output); then either narrow through `narrow_team_games` with the full scope, or remove the three cells from the team log's stated set so the planner declines them by name.
- **GitHub:** #322

### League-wide questions read under the wrong intent are refused "Nothing here answers" with the relation's hand-off
- **Found:** 2026-10-05, inventorying the point reader's declines for the
  declines -> Causes move (Phase 2, step 3;
  `~/association-research/stages/decline_sites.py`, the whole agent over
  the 2,710 readings of `reader_pop.py`).
- **Evidence:** 36 readings reach the user as a decline the point reader
  meant as a hand-off to another relation, all on intents the compiler
  alone answers: 17 `leaderboard` questions about teams ("1997 nba team
  defensive rating rankings", "Least points allowed nba teams this season
  per game", "Teams to score 90 points in their first 2 games") declined
  "a team, an opponent's figure or a franchise is the team relation's
  question" (`point._everyone_guard`); 2 `leaderboard` questions about a
  half ("rj barrett most assist in a single half") declined "a quarter or
  half is the period relation's question"; 17 `game_log` questions with no
  player read ("andrew iggins game log vs mavericks", "this season's bane
  game log excluding anthony black and franz wagner" - the one in the
  recorded corpus) declined "no player subject and no ranking or
  position-group reading of the question" (`point._everyone_point`). The
  feed readings carry no normalizer names, so production (where the model
  copies names) reaches fewer of the 17 logs; the 19 leaderboards do not
  depend on names. The same sites are hand-offs for template intents (the
  team or period template answers; 340 more readings), so they stay
  declines rather than causes.
- **User sees:** a refusal, "Nothing here answers this question:
  leaderboard: a team, an opponent's figure or a franchise is the team
  relation's question." - true, but the question had an answer
  (`team_leaderboard`, `period_leaderboard`) the stages did not route to.
- **Next step:** route a league-wide ranking whose words name teams (or a
  half) to `team_leaderboard`/`period_leaderboard` in the stages, measured
  on these 19 and the corpus; for the logs, check how many survive with the
  recorded normalizer's names before deciding whether "a game log needs a
  player or a team" should be a Cause.
- **GitHub:** #321

### The planner lets five player cells through to a team's readers on trust, with no test per cell
- **Found:** 2026-09-30, roadmap reviews (both agents), about the fix in
  `35d1676`.
- **Evidence:** `compose/plan.py` `_TEAM_READER_REFUSES` passes `without`,
  `below`, `above`, `season_n` and `conditions` for a team's rows, run,
  grouped and record shapes because each reader refuses them with its own
  sentence. Nothing asserts, per (shape, cell) through `compose.answer`,
  that the reader does. It is a fourth scoping declaration encoding a
  property of template code inside the planner.
- **User sees:** nothing today; if a reader stops refusing one, the
  narrowing is dropped silently - the shape of the P1 fixed in `35d1676`.
- **Next step:** a (shape x cell) test through `compose.answer`; then a
  refusal-by-name becomes a row of the team relation's cell table.
- **GitHub:** #283

### The compiler has no NetPoints measure, so a single-game NetPoints ranking has nowhere to land
- **Found:** 2026-09-24, fixing a live finding on the rendered page
  (`/tmp/claude-1000/preview6`): "who had the highest netpoint game this
  season?" and "... highest total netpoint game ..." routed `single_game_high`
  with a NetPoints stat, which the `single_game_high` template refuses (no
  such column), and the compiler (`query/compose`) then silently answered a
  ranking of POINTS instead - `_everyone_ranking` (`compose/move.py`)
  defaulted an unmapped measure to `"points"` the same way it does for a
  genuinely stat-less ranking ("top scorers"), with no way to tell the two
  apart from the answer. Fixed in this commit: a `stat` that was NAMED but
  does not map to a measure this relation knows is now a named `Refused`
  ("No ranking reads 'netpoints' on the player-games relation ...") rather
  than a silent substitution - see `test_a_stat_this_relation_cannot_read_is_refused_not_defaulted_to_points`
  (`tests/query/test_compose.py`).
- **User sees:** the refusal now names the real cause instead of a fluent,
  wrong ranking; the question itself still has no answer.
- **Next step:** `net_points_player_game` (per-player-per-game NetPoints,
  `DATA.md`) is a real table the compiler does not read at all - a second
  relation, or a narrow addition to this one, would let a NetPoints
  single-game ranking answer as fast as a points one does. Not attempted
  here: it is a new relation, not a one-line fix, and outside this session's
  scope (the "look nice" data-shape pass).
- **Source:** ours.
- **GitHub:** #220

### `team_leaderboard` excludes `situation`, so "best record since <day>" is still refused
- **Found:** 2026-09-24, fixing yardstick-v2 F104's routing.
- **Evidence:** "Best NBA record since January 31st 201" now routes
  `team_leaderboard {'stat': 'record', 'rank': 'best', 'limit': 1,
  'situation': 'since january 31st'}` (it was `team_record` with no team).
  Called directly on that tree, `check_scope` (the planner since 2026-10-05) refuses: `team_leaderboard
  cannot honor ['situation']`, and `compose.answer` returns None, so it is
  refused. The exclusion's reason in
  `team_relation.py:TEAM_RELATION_SCOPING_EXCLUDED["team_leaderboard"]`
  ("a leaderboard ranks a season, not the games in one weekday, month or
  holiday within it") is about narrowing the pool, and a "since <day>"
  window is not that: "best record since January 31" ranks every team over
  a window, which is the standings question the key asks.
- **User sees:** a refusal, for a standings question.
- **Next step:** the template owner lets `team_leaderboard` honor a
  `since_day` situation (the team relation already applies it,
  `TeamNarrowed.narrow_calendar`), keeping the weekday/month/holiday cells
  excluded if that reasoning holds for them.
- **Priority note:** P3 - one corpus question, a refusal.
- **GitHub:** #214

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

### `team_record`'s combined-season-types sentence drops the regular half's "standings from 1993-94" caveat
- **Found:** 2026-09-23, grading `live_sweep.jsonl` (yardstick-v2 F116).
- **Evidence:** "warriors all-time record including playoff record at away"
  now answers "574-843 (.405) combined on the road, including the playoffs
  (523-791 (.398) regular season, 51-52 (.495) playoffs)" plus the 2000
  standings-gap note. Asked for one season type, the regular half says
  "across the 33 regular seasons from 1993-94 through 2025-26 - ESPN's
  standings carry no home/road split before 1993-94", and the playoff half
  "in every postseason from 1989 through the latest". Combined
  (`compose.say._say_combined_record`; `templates/teams.py`'s `_combined_record_result` when found), only "Note:" lines are
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
- **GitHub:** #204

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
  `LEADERBOARD_METRICS`/`query/season_line.py` computes this - `shot_distance`
  is a per-player metric (`compose.shots.read_shot_distance`) with no
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
- **Evidence:** `templates/common.py`'s `HONORED_SCOPING["player_splits"]` (`compose.plan.STATED_SCOPING["player_splits"]` since 2026-10-05)
  declares `without`, `below`/`above`, `game_n` and `season_n` for the intent
  as a whole, with no distinction between a named-player question and a
  named-team-only one - so `check_scope` lets them through regardless of
  which subject the question turns out to have, and `_player_splits_team` (`compose.splits._team_splits` now)
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
- **User sees:** a refusal for "76ers splits
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
- **Evidence:** `player_relation.condition_scope` (player_splits,
  record_when, streak, with_without) lets a named season beat `span=career`
  - "the router keeps a named year alongside it, and 'career ... in 2015' is
  asking about 2015" - while `_span_of` (game_log, player_stat,
  threshold_count, single_game_high, period_split) raises "a career span and
  the 2015 season at once" and the question is refused. Same slots, opposite
  outcomes, by template.
- **User sees:** on one intent an answer for 2015; on another, a refusal.
  Which one depends on the router's intent choice, not on the question.
- **Moved 2026-09-27 (plan item 6, step (d), part 4):** `threshold_count`
  and `single_game_high` are the compiler's alone now, and the compiler reads
  the named year - the condition templates' reading - so of `_span_of`'s five
  only `game_log`, `player_stat` and `period_split` still refuse.
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
- **User sees:** nothing wrong - "How many times has webanyama fouled out of a
  game" asked for a clarification of the typo in the run; since the
  near-spelling default (2026-09-27) the fast path answers "Victor Wembanyama
  had 3 games with 6+ fouls in his regular season career", the box-score count.
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
- **Re-measured 2026-09-25 (subject kinds, steps 3d-3e):** the reading
  says `position` for all six, the position phrase in `player` (F056) is
  no longer read as a player, and the team the router files in `team` for
  a position-group log is its opponent, not the subject - "Centers stats
  game log vs kings" and "forwards with 20+ mins vs gsw log" answer "every
  center vs the Sacramento Kings" / "every forward vs the Golden State
  Warriors with at least 20 minutes" on the recorded golden. Still open:
  the attempts floor above, and the current season's generic position
  codes (DATA.md).

### An award or All-Star question has no table to refuse from, so nothing refuses it by name
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
  scope for a read-only pass - but the path was the one `check_coverage`'s
  own reasoning describes: a question with nothing to find reached the
  fall-through agent (removed 2026-09-29), which filled the silence from its
  own weights, as it did for the "Ronaldo Lopes" fingerprint.
- **User sees:** on the fast path, a wrong answer (below); with the agent
  gone, otherwise a refusal naming only the intent.
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
- **Parser path (5.0.0):** still wrong. "nba mvps in 2010's" (normalizer
  stubbed with no names and no stat) reads parent `leaderboard` with
  `since: 2010, until: 2019` and answers the 2026 points-per-game board
  ("every player, regular season career (2010-2019), by player (points per
  game, minimum 20 games): Kevin Durant 28.0 ...") - the award unread. (The
  range was dropped too until `_resolve_everyone` read `since`/`until`,
  the fix for the retired #207.)
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
- **Evidence:** `compose.plan.STATED_SCOPING["record_when"]` and
  `["streak"]` (until 2026-09-30 `HONORED_SCOPING`, `templates/common.py`)
  still claim the whole relation set for the WHOLE intent, not just the
  player branch - the declaration cannot tell the branches apart from the
  slots alone; since `streak` retired into the compiler's `run` shape, its
  point (`point._default_streak`; `compose.adapt._adapt_streak` until slice (ii)) refuses the team-only cells by
  name before the point is planned, with the same sentence. `splits._condition_needs_player_refusal` is what actually narrows
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
- **Evidence:** `compose/shots.py`'s `read_shot_chart`/`read_shot_distance`
  (the templates' bodies until Phase 2, step 5) gate the career floor note (`_shots_career_floor`) on `span.career and span.since is None` - added in
  this step to fix a real bug (a `since`-bounded read was claiming to "cover
  his whole career on record" against his UNBOUNDED range, which is false of
  a bounded one). The fix is correct for what it removes, but nothing replaced
  it: the floor note's other job - saying when the 2002 shot floor clips
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
- **Next step:** extend `_shots_career_floor` (or a sibling) to take the actual
  requested floor (`span.since` when set, else the real career start) instead
  of always comparing against the player's own first season, so a `since`
  read gets the same "seasons left out" sentence a plain career already does.
- **Source:** ours, not ESPN's.
- **GitHub:** #192

### A typo of a common surname suggests nobody: "crry" is "No player found"
- **Found:** 2026-09-27, measuring the near-spelling default (ROADMAP plan
  item 6, step (c))
- **Evidence:** `/home/jeff/association-research/typo-default/measure.py`,
  read-only on `nba.duckdb`, `entities.py` at the near-spelling-default
  commit: of 10,602 one-letter drops, doubles and adjacent swaps of the 591
  2026 players' surnames, **2,584** are near more than
  `MAX_CLARIFY_CANDIDATES` players, so `suggest_players` drops the list and
  `read_near_spelling` has nothing to take. "crry" (six Currys within one
  edit) is `No player found matching 'crry'.`; "stephen crry" resolves.
- **User sees:** a refusal that reads as "no such player" for a typo of a
  real one. Once the normalizer copies names verbatim (step (c)), a bare typo'd
  surname reaches this where the router used to correct it silently.
- **Next step:** narrow an over-long near-spelling list the way
  `resolve_player` narrows namesakes - to who played in the season asked about,
  else the latest - before dropping it, and ask (or default visibly, one
  survivor) over what is left. Measure how many of the 2,584 then land on
  exactly the player misspelled.
- **Source:** ours.
- **GitHub:** #240

### A typo inside a hyphenated surname reaches nobody: "Gilgeous-Alexandr"
- **Found:** 2026-09-27, measuring the near-spelling default
- **Evidence:** `entities._NEAREST_WORD` splits the DISPLAY NAME on non-letters
  but compares the typed token whole, so "Gilgeous-Alexandr" is measured
  against "Gilgeous" and "Alexander" separately and is near neither;
  "Gilgeous Alexandr" (a space) resolves to Shai Gilgeous-Alexander. Of the 98
  one-letter drops of the nine punctuated 2026 surnames that no substring
  matches, 97 have no near spelling at all
  (`/home/jeff/association-research/typo-default/hyphen.py`).
- **User sees:** "No player found matching 'Jackson-Davs'." for Trayce
  Jackson-Davis.
- **Next step:** split the typed tokens on the same non-letter class before
  the near-spelling pass (every piece must be near some word, as now), and
  re-run the measurement.
- **Source:** ours.
- **GitHub:** #241

### "rebounds allowed per team" has no team metric to rank, and is refused
- **Found:** 2026-09-27, plan item 6 step (b) (the lead's offline run of the agent with the parser; the parser measurement's DEV row "rebounds allowed per team" and its paraphrase "per team rebounds allowed").
- **Evidence:** `team_metrics._ALIASES` holds one opponent metric, `opponent_points` ("points allowed"); nothing else a team gives up (rebounds, assists, threes, turnovers forced) is a `TEAM_METRICS` key. Before this change the parser read the stat as `rebounds` and `team_leaderboard` answered the teams' OWN rebounds per game, best first - a fluent answer to a different question. `MEASURE_GRAMMAR` now reads "rebounds allowed" as the key `rebounds allowed`, which `resolve_team_metric` does not know, so `team_leaderboard` raises `TemplateUnsupported` ("no team metric for stat 'rebounds allowed'") and the question is refused. The router path (day10) routed `team_stat` with no team and fell through too.
- **User sees:** a refusal naming only the missing metric - to a question the warehouse can answer (`team_box_stats` holds both sides of every game).
- **Next step:** opponent box-score metrics in `TEAM_METRICS` (the opponent's row of the same game, the way `opponent_points` reads points against), then the aliases "rebounds allowed", "assists allowed", "threes allowed" that `MEASURE_GRAMMAR` already emits; until then the refusal names the missing metric.
- **Source:** ours, not ESPN's.
- **GitHub:** #242

### `player_stat` has no per-game line for attempts: "embiid 3pt attempts per game" is refused
- **Found:** 2026-09-27, the step (c) hold-out comparison (the 75 recorded routing-corpus questions outside day10, `~/association-research/yardstick-v2/holdout_compare.py`).
- **Evidence:** `player_stat: no per-game column for stat 'threePointFieldGoalsAttempted'` - `season_line.PLAYER_STAT_COLUMNS` holds the made columns, whose line reports the attempts beside them ("585 of 1,727 (33.9%)"), and no attempted column; `router._route_attempted_stat` rewrites a made stat to the attempted one whenever the question says "attempts" and not "made", on both readers.
- **User sees:** a refusal naming the stat for a per-game attempts question, where the made line would have answered it in passing. Asking for both ("3pt attempts and made") answers, from the made line.
- **Next step:** `player_stat` is the compiler's now (2026-09-28, plan item 6 step (g)) and the reason is the same, from its season-line presenter (`compose.present._present_player_stat_season_line`, through `templates.players._wanted_stats` - `compose.seasons._player_line_season` and `season_line.wanted_stats` since Phase 2): give the season-line reader the attempted columns (per game and total), or answer an attempted stat from the made line with the attempts per game computed; a warehouse-verified test on Embiid's career line.
- **Source:** ours.
- **GitHub:** #243

### "while X plays" names no teammate: "maxey points while embiid plays" is refused
- **Found:** 2026-09-27, plan item 6 step (d) follow-ups, probing played companions.
- **Evidence:** the parser reads Embiid as a `played` companion and routes `with_without` ("while" is a companion keyword), but the stage that writes `with_player` reads "with X" and "when X plays" only (`router._WHEN_PLAYED` is anchored on "when"), and `with_without` reads a played teammate from `with_player` alone - so the template has no teammate: "with_without needs exactly one teammate, got ['maxey']", a refusal, on `0a7140a` and this branch alike. "maxey points when embiid plays" answers.
- **User sees:** a refusal, where "when" would have answered.
- **Next step:** let `_WHEN_PLAYED` (and `_WHEN_WITH`) take "while"; a parser test on the wording.
- **Source:** ours.
- **GitHub:** #250

### A name several players share is asked about over a range of seasons, where the range's own seasons would settle it: "curry playoff stats 2015-18" asks among six Currys
- **Found:** 2026-09-28, fixing #261 (the short range now reads as the range it writes, and meets the gap every range already had).
- **Evidence:** stubbed offline through the whole agent (main warehouse): "curry playoff stats 2015-18", "curry playoff stats 2015-2018" and "curry stats from 2015-18" each route `since`/`until` and answer "'curry' matches more than one player - did you mean Seth Curry, Stephen Curry, Dell Curry, Eddy Curry or JamesOn Curry (1 other also matches)?"; "Love stats 2012-14" asks among Caleb Love, Kevin Love and Lawson Lovering; "jordan stats 95-98" lists 28 players. `player_relation.career_end` narrows a name by the current season whenever no single season is asked (`through=current_season()`), so a range's own seasons never narrow it - where one season does: "curry playoff stats 2018" is Stephen Curry, the only Curry in that postseason. Stephen Curry holds all 63 of the Currys' 2015-2018 postseason games (`player_game_log`, minutes > 0): Seth has none, and Dell, Eddy, JamesOn and Michael Curry had retired.
- **User sees:** a question where the answer was settled - the range names whose seasons to look in.
- **Next step:** narrow by the range - `until` as the season a name left open is settled by, `since`..`until` as the seasons a candidate needs a row in (`entities.narrow_to_available` takes `season`/`through` today) - in `player_relation`'s resolution; a case per template reading `since`/`until`.
- **Source:** ours.
- **GitHub:** #272

### A "last N games" question naming no season type refuses a calendar or a range of seasons instead of reading it
- **Found:** 2026-09-28, plan item 6 step (g), giving the team compiler's window sum the both-types read the team log had (`compose.logs._team_mixed_rows` now).
- **Evidence:** the both-types read ("his/their last 5 games", `season_type_unstated`, both season types merged by date) narrows by an opponent and a venue only: `_team_game_log_mixed` and `_player_game_log_mixed` take neither `since`/`until` nor a calendar `situation`, and dropped them silently before this commit ("knicks last 5 games on tuesdays" listed their last 5 games on any day). `compose.logs.read_team_log` and `compose.team._compile_team_games_mixed` refuse the three now ("a window over both season types is read for a plain 'last N games' only"); the player log's mixed read (`compose.logs._player_log_mixed`) is not guarded the same way yet - the parser never sets `season_type_unstated` beside `since` (`router._route_game_log_recent_span`), but a calendar can reach it.
- **User sees:** a refusal where the single-type read ("knicks last 5 regular season games on tuesdays") answers.
- **Next step:** read the calendar and the range in the merged read - `_team_mixed_games` and `_player_game_log_mixed` taking the scope's `situation`/`since`/`until` through the same shared steps (`team_games`, `scoped_games`) the single-type read uses - and guard the player log's mixed read until then; a test per relation.
- **Source:** ours.
- **GitHub:** #274

### The empty-box rebuild counts turnovers and fouls by the older rules
- **Found:** 2026-09-29, period relation (plan item 4)
- **Evidence:** `fetch/repairs/reconstructed_box.py` counts a turnover as a
  `%Turnover%` type (missing `Traveling`) and a foul as `%Foul%` but
  technicals (counting an offensive foul's turnover half as a second foul,
  missing `Shooting Block`/`Personal Block`/`Offensive Charge`). Its own
  measured accuracy is turnovers 92.5% and fouls 83.3% (2015). The period
  line's rules for the same columns (`player_games._PERIOD_TURNOVER`,
  `_PERIOD_FOUL`) measure 99.8% and 99.7% on 2015's real box scores, and
  since 2026-09-29 also read a `No Turnover` or `Not Available` play whose
  text says "turnover" (436 in 2018, 344 in 2016) and a `Not Available` one
  whose text says "foul" (89 in 2018).
  `REBUILT_STATS` leaves both columns out of every answer because of those
  figures, so a rebuilt Bulls or Pelicans 2013-18 game shows no turnovers or
  fouls at all.
- **User sees:** a per-game answer over the empty 2013-18 box scores skips
  turnovers and fouls ("could not see N games") where it could read them.
- **Next step:** reuse the period line's rules in the rebuild, re-measure
  2015 against its surviving box scores, and admit the two columns to
  `REBUILT_STATS` if they clear the others' ~99%; needs a
  `data load --tables player_box_stats` (the views are built at load time).
- **Source:** DATA.md, "A play's type does not always say what the box score
  counts it as"
- **GitHub:** #276

### A team named as its own opponent is refused with no reason given
- **Found:** 2026-09-29, period relation's team half
- **Evidence:** "celtics 2nd half turnovers vs boston" reads as
  `team_quarter_points` with team "celtics" and opponent "Boston Celtics"
  (`parse.read_route`, measured on the read-only warehouse); the shared step
  `team_relation.team_games` raises `TemplateUnsupported("a team cannot be
  its own opponent")`, `compose.team.run_team` turns the same raise into
  `Unsupported`, and the question leaves the fast path with nothing said about
  why. Every team template reaches the same raise.
- **User sees:** a refusal naming only the compiler's reason ("a team cannot
  be its own opponent") for a question whose real problem is that it names
  one team twice - or, likelier, meant a different opponent.
- **Next step:** return a `TemplateResult` from `team_games` naming it ("the
  Boston Celtics are named as both the team and its opponent - name the team
  they played"), with a test per team template.
- **GitHub:** #277

### 2013 and 2016 period lines could read the made shots the plays mistype
- **Found:** 2026-09-29, period relation's team half
- **Evidence:** DATA.md, "A team's play-by-play does not add up to its box
  score in six seasons": the plays are short a made field goal in 304 of
  2013's and 985 of 2016's team-games while their own running score reaches
  the final score in 299 and 975 of them. `TEAM_PERIOD_AGREEMENT` refuses
  field goals in both seasons (83.1%, 56.7%), 2016 assists (65.6%) and 2016
  turnovers (64.9%); the player table caveats 2016 at 90.4%.
- **User sees:** a refusal for a team's per-quarter field goals, assists or
  turnovers in 2013 or 2016, and a caveat on a player's.
- **Next step:** measure whether a shot play whose team's running score rises
  by its value on the next play can be read as the make (2016's "Kyle Korver
  misses three point jumper", the score rising by three) - a rule in
  `period_line_sql`'s shots, checked by both checkers.
- **Source:** DATA.md, "A team's play-by-play does not add up to its box
  score in six seasons"
- **GitHub:** #278

### A record over both season types drops each half's floor and neutral-site remarks: "warriors all-time record including playoff record at away"
- **Found:** 2026-10-01, closing ROADMAP Phase 0 (a kind on every remark); measured by `scripts/stage_snapshots.py remarks` over the 628 recorded questions at the merged tree: 2 answers have a remark written and not said, both this one.
- **Evidence:** `_team_record_combined_types` answers each season type through `_team_record_route`, and `_combined_record_result` (`templates/teams.py` then; `compose.say._say_combined_record` now) writes its own heading and keeps only each half's "Note:" tail, found by searching the text (`_extract_note`). So the halves' heading floors ("ESPN's standings carry no home/road split before 1993-94", "the warehouse's game list starts with the 1989 playoffs") and any neutral-site remark are written and never said. The combined answer shows the years each span starts from, with no cause.
- **User sees:** "from 1993-94" and "from 1989" with nothing saying why the record starts there; where neutral-site games are in the span, home and road halves that do not add up, unexplained.
- **Next step:** the combined result takes its halves' remarks from the notes each recorded (`query/notes.py`), not from their text; goes with the team shapes' slice (`ROADMAP.md`, Phase 2 (iv)). Until then `stage_snapshots.py remarks` exits 1 on the corpus for these two, which is the check working.
- **GitHub:** #311

### A team's splits count blank box-score columns from field-goal attempts alone
- **Found:** 2026-10-01, closing ROADMAP Phase 0 (a kind on every remark); the splits agent, reading `templates/splits.py:609-613` (the count is `compose/team.py`'s `blank` column now, said by `compose.say`); reported, not measured.
- **Evidence:** the count behind "Rebounds, assists, 3-pointers and FG% are missing from N of those games' box scores" tests only `fieldGoalsAttempted IS NULL`. That holds for the wholly empty 2013-18 team rows; but `record_when`'s team branch shows a column can be blank alone (2018 `totalTurnovers` is NULL on rows with a real box score), and a row with attempts and no assists would be averaged with nothing said.
- **User sees:** possibly an average over fewer games than the table's G column, uncaveated.
- **Next step:** one query over `team_box_stats`: rows where `fieldGoalsAttempted` is set and `assists`, a rebound column or `threePointFieldGoalsMade` is NULL, by season. If any, count per column.
- **GitHub:** #312

## P4: tooling, docs, low impact

### A combined record says "(1 neutral-site game counts as neither home nor away)" with no home or road figure in the answer
- **Found:** 2026-10-05, the Phase 2 review (`~/association-research/reviews/phase2-2026-10-05/REVIEW.md`)
- **Evidence:** "How many wins did the Knicks have this season including playoffs" since `4db543a`; also `test_team_record_combines_both_season_types_for_one_season`. One of the four feed answers that moved unenumerated.
- **User sees:** a note about a split the answer does not show.
- **Next step:** say the neutral-site count only beside a home or road figure.
- **GitHub:** #336

### The sayer's import contract is checked on direct imports only: `compose.say` reaches duckdb through four modules' constants
- **Found:** 2026-10-05, Phase 2 step 6, when the last phrase helpers left `templates/` (the condition `pyproject.toml` named for checking the contract on chains).
- **Evidence:** `allow_indirect_imports = false` on "The compiler's sentence reads no warehouse" breaks it: `compose.say` imports `compose.logs` (`LOG_PERCENTAGES`, `log_key`), `conditions` (the split tables and labels), `player_games` (`PERIOD_LOG_COLUMNS`, `PERIOD_RATES`, `period_columns`) and `season_line` (`NETPOINTS_COMPARE_ROWS`, the season line's column tables), each of which imports duckdb for its statements.
- **User sees:** nothing; a sayer that reached the warehouse through one of those modules would pass the gate.
- **Next step:** move those constants into modules with no statements (the relation's vocabulary beside `measures.py`), then set `allow_indirect_imports = false` and watch it fail on a planted import.
- **GitHub:** #324

### A log narrowed by a season range is headed "last 7 games of his career"
- **Found:** 2026-09-30, roadmap review (agent A).
- **Evidence:** "show maxey's games against boston in the past two
  seasons" is headed "..., last 7 games of his career (2025-2026 regular
  seasons)"; the 7 rows are every regular-season game against Boston since
  2024-25 (checked against `real_games`). The point carries `span:
  'career'` beside `since: 2025`.
- **User sees:** a right list under a heading that says "career".
- **Next step:** head the log by its narrowing.
- **GitHub:** #285

### `Scope.stat` holds raw phrases and two key vocabularies for one measure
- **Found:** 2026-09-30, roadmap review (agent A).
- **Evidence:** "how many 3 pointers have the magic made ..." carries
  `stat: '3 pointers'` (`router._route_team_slots` -> `_team_metric_in`,
  `router.py:2716-2719`); the parser's measure grammar and the compiler's
  word table key the same measures differently (`threePointFieldGoalPct`
  vs `three_pct`, `plus_minus` vs `plusMinus`); two label tables.
- **User sees:** nothing directly; every reader of `stat` maps it again.
- **Next step:** one closed measure type (new roadmap, Phase 0).
- **GitHub:** #286

### 2018's `teamTurnovers` is kept though it is not the game's
- **Found:** 2026-09-29, period relation's team half
- **Evidence:** `fetch/repairs/team_box_repair.py` keeps 2018's
  `teamTurnovers` ("nothing proves [it] wrong") while clearing the rest of the
  shifted block; it matches the game's own team turnovers in the plays in 986
  of 2,280 team-games and the opponent's in 965, against 99.9-100% in 2016,
  2017 and 2019.
- **User sees:** nothing through a template (none reads the column); a raw
  read of `team_box_stats` gets a wrong per-game figure.
- **Next step:** clear it with the rest of the 2018 block in
  `team_box_repair` (a `data load --tables team_box_stats`), and say so in
  its module docstring.
- **Source:** DATA.md, "2018's `teamTurnovers` is not the game's own"
- **GitHub:** #279

### `player_stat` given a `team` slot answers this season and never mentions the team: "lebron stats as a starter for Miami"
- **Found:** 2026-09-27, the step (c) rehearsal (the whole agent with the parser as reader and the normalizer's recorded replies, `~/association-research/yardstick-v2/run_offline_parser.py`), on an intermediate parser that wrote the player's own team into `team`.
- **Evidence:** slots `{'player': 'LeBron James', 'team': 'Miami Heat', 'season_type': 2, 'split': 'starter'}` answered "LeBron James averaged 20.9 points, 6.1 rebounds and 7.2 assists per game in 60 games as a starter in the 2026 regular season." - his Lakers season, with Miami nowhere in the sentence. With no `team` slot the subject stage reads the own team and the span it implies, and the same question answers "... in 294 games with the Miami Heat as a starter over his career (2011-2014 ...)". The parser does not write the own team into `team` (`parse._read_route_names`; the subject stage writes it to `own_team`), so it does not reach this; the router reader did whenever its model filed the team, and it is gone (5.0.0).
- **Ranked P4 (2026-09-27):** the parser writes a player's own team to `own_team`, never `team` (`subject._apply_own_team`), so no question read today reaches this; only a recorded route carrying `team` beside a player does (golden's router routes).
- **User sees:** nothing on the parser. A fluent, correct-looking line about a different team's season for any route that carries `team` on `player_stat` - a recorded one, or a reader that files the own team there (step 3c moves the own-team writer into the parser).
- **Next step:** `player_stat` is the compiler's now (2026-09-28, plan item 6 step (g)) and the same route still answers the Lakers season (re-measured on the main warehouse): `compose.core._apply_team_slot` reads no `team` on a per-game average by design, and `plan._check_relation_scoping` lets the slot through (`COMPILER_SLOTS`) - either honor it as the own-team narrowing the subject stage already builds (`own_team`), or refuse it on that skeleton; warehouse-verified test on this question.
- **GitHub:** #237

### Package review leftovers: one fold, one ordinal, one month list, and an untested third of the parent grammar
- **Found:** 2026-09-27, the package review of plan item 6's steps (a)-(d) (read-only agent; not each re-verified by the lead).
- **Evidence:**
  - `entities._fold` (entities.py:255) drops letters with no decomposition: "Aşık" -> "Ask", "Đorđević" -> "orevic", "Søren" -> "Sren"; `fetch/parse.py:975` folds differently, keeping them. A 10-entry `str.maketrans` (or anyascii, ISC) fixes it.
  - The ordinal suffix is written four times: `reading.ordinal_word` and `templates/common.py`'s `_ordinal` (since deleted) (identical, same module), `player_games.py:407`, `fingerprint.py:727`. All agree today.
  - Month names are defined six times (router.py:501, :511, :1084; calendar.py:44; conditions.py:451; subject.py:301), and `parse._PLAYER_LOG` omits May and June; number words were five copies until fe53c72 gave the parser one table (router.py:469, :477, :1706 and subject.py:111 still hold their own).
  - 13 of `PARENT_GRAMMAR`'s 30 rows have no direct `parent_intent` case in tests/query/test_parser.py (period_leaderboard, period_split, coach, team_players, the team top-N leaderboard, team streak, the team_stat fallback, player_netpoints, player+companions, both position rows, the everyone streak, everyone finals/game_log); the measurement that exercises them lives outside the repo.
  - `pyproject.toml`'s import-linter comment says pydantic comes from `association[web]`; it is in every core install through ollama (uv.lock), which imports it at load.
  - `nba/season.py:40` says every day 1970-2040 is checked against zoneinfo; the test covers 1976-01-01 to 2039-12-31.
  - DuckDB's `levenshtein`/`damerau_levenshtein` count UTF-8 bytes, not letters: `damerau_levenshtein('ö','o')` is 2 and `('şengün','sengun')` 4 (measured with the rapidfuzz switch, which agrees with DuckDB on all 142,880 ASCII word pairs tried). The entity index measures with them (entities.py:347, :397, :1400); where an accented string reaches them unfolded it is charged double - not checked whether any does.
- **User sees:** "Ömer Aşık" typed with the Turkish letter matches nobody; the rest is maintenance - a vocabulary fixed in one copy and not the others, or a grammar row deleted with no repo test failing.
- **Next step:** one table per concept (months, ordinals, number words) imported everywhere; a parametrized `parent_intent` table with a meta-assert that every row is hit, watched to fail; the fold's translate table; the two comments corrected.
- **Source:** ours.
- **GitHub:** #244

### Two decisions share the label "subject kind": a streak's `kind` slot is recorded as the subject's kind
- **Found:** 2026-09-27, the step (b) audit agent, reading day10 traces.
- **Evidence:** `subject._apply_child_intent` records every settled slot as `Decision("subject", key, ...)`, and a streak's `kind` slot ("win"/"loss") is one of them. On day10's "what was the sixers longest winstreak this year?" the trace holds `subject kind: 'team'` and then `subject kind: 'win' (read for streak)`. The web page's decisions fold shows both, and any reader keyed on (stage, field) takes the wrong one; `~/association-research/parser-greenfield/measure.py` had to filter by `SUBJECT_KINDS`.
- **User sees:** two contradictory "kind" lines in the decisions fold of a streak answer.
- **Next step:** give slot rewrites their own field prefix or stage ("slot", key), and keep "kind" for the subject alone.
- **Source:** ours.
- **GitHub:** #234

### `team_alignment` is not declared in every `TEMPLATE_SOURCES` tuple that can now read it
- **Found:** 2026-09-24, landing the K3-2 conference/division narrowing.
- **Evidence:** `situation` reaching `Narrowed.narrow_alignment`/
  `TeamNarrowed.narrow_alignment` means `team_alignment` is read by every
  template whose `HONORED_SCOPING` (`compose.plan.STATED_SCOPING` since 2026-10-05) includes `situation` (by
  `_relation_scoping(intent)`'s default, essentially every template on
  either relation) - but `templates.common.TEMPLATE_SOURCES` (`coverage.SOURCES` now) was not updated
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
  `shot_distance`) are literal tuples in `coverage.py` (`SOURCES`), in this
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
- **Evidence:** `templates/games.py: _head_to_head_span_result` (`compose.meetings._head_to_head_over_span` now) calls
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
- **Evidence:** `team_relation.team_span_clause` (the postseason-by-
  calendar-year clause over `tg.eastern_date`/`tg.season`) now has a second
  copy, `conditions._with_without_team_span_clause`
  (`query/conditions.py`), four lines of identical logic under a different
  name. Not drift - `conditions.py`'s own module docstring says "nothing here
  imports `templates`, so the dependency runs one way", and `templates/common.py` (`team_relation.py` now)
  already imported FROM `conditions.py` (`_Scope`, `_game_scope`, `box_source`),
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
  have `team_relation.team_span_clause` and every `conditions.py` copy
  delegate to it. Not done here: three call sites is still one branch's
  decision to make, not a refactor to force mid-port on work another agent may
  be doing in parallel on the same file.
- **Priority note:** P4 - no wrong answer today, a maintenance risk if the
  next two ports each add their own copy instead of reading this one first.
- **GitHub:** #193

### `since` reaches the metric templates only by a second season-scoping path
- **Found:** 2026-09-18, looking for the next compositional-scoping win after
  the starter/bench filter landed; **narrowed 2026-09-19** when `since` landed
  on the relation
- **Evidence:** `since` is composed once now: `_span_of` and `_condition_scope`
  build a span from that season on, and `game_log`, `player_stat` and
  `player_matchup` honor it by declaring it (`HONORED_SCOPING`,
  `compose.plan.STATED_SCOPING` since 2026-10-05) - "jokic vs cade since 2022" answers their 7 meetings.
  What is left is the templates that do not sit on the player-games relation
  and read `slots.get("season")` into their own metric SQL: `leaderboard`
  ("most steals by bucks players 2010s", `run_leaderboard`) and
  `team_leaderboard` ("nba team with least playoff wins since 2022",
  `templates/teams.py` then; `compose/team_stats.py` over `team_seasons.py` now, which reads it since slice (iv)). Both still refuse `since`, and `until` is set beside
  `since` by the stages (`router._route_season_range`) but is in neither `HONORED_SCOPING` nor `SCOPING_SLOTS`
  (see #23). So the "three separate season-handling paths" this entry first
  counted are two: the relation's span, and the metric templates' own.
- **User sees:** the two leaderboard questions are refused; every other
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
  `Most reb by a hawk player history` (that one, re-measured 2026-10-09 on
  `4b9c254`, reads as the Atlanta Hawks' players' rebounding leaders - the
  subject reading takes "hawk" as the team). The rule is not yet in `src/`.
- **Next step:** this belongs in `entities.py` beside `players_named_in`, which
  already enforces whole-word matching and whose own notes record that "boston"
  is Brandon Boston Jr. It generalizes something the codebase half-knew, and it
  needs no maintenance when a rookie named Memphis arrives.
- **Script:** `~/association-research/statmuse-2026-09/candidate_enum.py`,
  `team_words()` and the `teams` argument to `candidates()`; runs offline.
- **GitHub:** #136

### `limit` is not a scoping slot, so a template that ignores it does so silently
- **Found:** 2026-09-18, merging the StatMuse scoping branches and re-measuring
- **Evidence:** `SCOPING_SLOTS` (`reading.py`) holds `order`,
  `date`, `opponent`, `venue`, `span`, `without`, `round`, `split`, `since`,
  `below` and `situation` - **not `limit`** - so `check_scope` (the planner since 2026-10-05) cannot refuse a
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
  `query/templates/games.py` then; `compose/pairs.py` and `compose.say.say_player_matchup` now) reads neither `stat` nor `fields` - the summary
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
  (`reading.py`) any more than `limit` was, so `check_scope`
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
  with `stat: 'plus_minus'` - the router names it correctly - and is refused
  with "no leaderboard metric for stat 'plus_minus'". The data is there and is
  complete where it matters: **0 of 860,230 `player_box_stats` rows with real
  minutes have a NULL `plusMinus`** (measured read-only against
  `nba.duckdb`, 2026-09-18), confirming `DATA.md`'s corrected note that the
  NULLs are a strict subset of the did-not-play rows. Summed for 2026 it gives
  a sensible board: Gilgeous-Alexander +788, Holmgren +678, Wembanyama +664.
- **User sees:** a refusal on a stat people ask about often.
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
- **Evidence:** `season_line.py` (`leaderboard.py:490` when found) builds a weighted career value as
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

### Some P3-shaped entries still sit under the P2 heading
- **Found:** 2026-09-18, looking for where to file two new refusal/gap
  findings and finding no P3 section to put them in; rewritten 2026-10-09
  (Phase 3 step 0): the heading itself is back, and held by a gate
  (`scripts/check_issues_md.sh`, which names this entry), so what is left
  is the entries filed under P2 while it was missing.
- **Evidence:** refusal/gap entries filed before the heading returned still
  sit under `## P2: misleading or incomplete` - "Two players against one
  team has no template" among them - undifferentiated from actual P2s.
- **User sees:** nothing - this is about the file's own readability, not an
  answer.
- **Next step:** move the P3-shaped entries under `## P2` beneath `## P3`, in
  a change no parallel agent is editing the file in (the merge-conflict
  risk the first version of this entry named).
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
- **Evidence:** `_player_splits_team` (`query/templates/splits.py`; `compose.splits._team_splits` and its sayer now) says
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
  and `query/season_line.py` `not_a_postseason_copy` (games plus the value
  columns, on the same team). They agree today - each drops 436 of 7,941 rows -
  but nothing keeps them in step. (Both comments now give the re-measured
  figure, 436 of 7,941 rows / 340 player-seasons, fixed 2026-09-16 with #43;
  only the duplication remains.)
- **User sees:** nothing today. It is the same hand-maintained-pair shape as
  #83, with the added trap that the two spellings could diverge silently.
- **Next step:** export one helper and call it from both, the way #83 proposes
  for the traded-player dedup.
- **GitHub:** #93

### One rule, two hand-maintained copies: the traded-player dedup
- **Found:** 2026-09-15, while fixing #9
- **Evidence:** "prefer the combined row over the per-team stints" is written
  as SQL in `fetch/warehouse.py:259` (the `player_season_stats_deduped` view)
  and twice in `query/season_line.py` (the traded-player dedup, `dedup_traded`
  QUALIFY). Fixing #9 had to touch both, and a fix that touched only one would
  have left the leaderboard reading the broken row while the deduped view was
  correct - green tests either way.
- **User sees:** nothing now that both are repaired at load time. The coupling
  remains: a third reader of `player_season_stats` would need the same rule
  written a fourth time.
- **Next step:** export the QUALIFY fragment from one module the way
  `not_a_postseason_copy()` already exports its own rule from
  `query/season_line.py`, and have both call it.
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

### A warehouse built before a view change is not detected
- **Found:** 2026-09-11, while qualifying true shooting and eFG% (`f66e1f1`)
- **Evidence:** a view's SQL is stored in the warehouse file. Code that reads a
  column the stored view lacks gets a Binder error; for `ts_pct`/`efg_pct`, the
  template then refuses. This was confirmed against a real
  pre-change warehouse. The 2026-09-11 data load at `3d3c8c6` brought the
  current warehouse up to date.
- **User sees:** after any view change and before the next `data load`, a
  refusal, with nothing saying a load would fix it.
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
- **Evidence:** `_single_game_netpoints` (`query/templates/netpoints.py`; `compose.netpoints._netpoints_game` now) catches
  every DuckDB error. `fingerprint.py` already narrowed the same pattern to the
  missing-table error.
- **User sees:** a SQL bug reported as "unavailable", then a refusal.
- **Next step:** catch `duckdb.CatalogException` only.
- **Re-checked 2026-09-15:** the pattern occurs twice. The second is
  `compose/seasons.py`, in `_player_compare_netpoints`, which catches `duckdb.Error`
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
- **Evidence:** `no_games` (`query/player_relation.py`, used by
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
  `query/templates/games.py` then; `compose/logs.py` now), and `team_season_stats.plusMinus` is -1 in
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
  `position_abbr` or `position`. Anything that starts reading them gets a
  stale value.
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

### Two NetPoints columns have a different type from the same column everywhere else
- **Found:** 2026-09-25, while checking the warehouse's structure for join
  performance.
- **Evidence:** measured on `/home/jeff/code/association/nba.duckdb` (DuckDB
  1.5.5). Every table stores `athlete_id` as VARCHAR and `season` as BIGINT,
  except for two columns:
  - `net_points_player.athlete_id` is BIGINT. All 26 Parquet files are
    `int64`, because `parse_net_points_player` stores NetPoints' numeric
    `dot_com_id` as it arrives (`fetch/parse.py:808`).
  - `net_points_player_fingerprint.season` is DOUBLE (`2021.0`). All 8 files
    are `double`, because `parse_net_points_fingerprint` adds 1 to a source
    value that is already a float (`fetch/parse.py:1248`).

  Nothing is broken today. DuckDB casts implicitly on comparison, so all three
  cross-type joins return rows: `net_points_player` x `players` on
  `athlete_id` (6,837), `net_points_player_fingerprint` x `player_season_stats`
  on `(athlete_id, season)` (7,658), and `net_points_player` x
  `net_points_player_fingerprint` (6,802). The templates filter
  `net_points_player` with a bound string parameter, which also casts. No
  reader in `src/` selects `net_points_player.athlete_id` into Python.
- **User sees:** nothing yet. The risk is in Python: a row read from
  `net_points_player` carries an `int` `athlete_id`. A dict keyed by the `str`
  ids every other table returns would miss it, and the reader would answer "no
  match" with no error.
- **Next step:** cast the two columns in the warehouse build
  (`fetch/warehouse.py`, the per-table `CREATE TABLE ... AS SELECT`):
  `athlete_id` to VARCHAR and `season` to BIGINT. Better still, fix the two
  parsers so a fresh pull writes the same types. The parser fix only reaches
  files already on disk after a `--force` re-pull, so it needs the load-time
  cast as well. Then run `data load --tables net_points_player
  net_points_player_fingerprint` and re-run the three joins above. Do not
  convert ids to integers across the warehouse to make joins faster: measured
  on in-memory copies, integer ids made typical joins about 2x faster, but
  those joins took 7-82 ms against a router call of over a second. The switch
  would also mean changing every place the Python code binds or looks up a
  string id, and any place missed would fail as a silent "no match".
- **Source:** ours.
- **GitHub:** #224

### A right name that is not on record reads as a near-spelled one: "Willis Reed" is Willie Reed
- **Found:** 2026-09-27, measuring the near-spelling default
- **Evidence:** of 36 famous pre-1994 names absent from `players`
  (`/home/jeff/association-research/typo-default/famous.py`), two are within
  the edit budget of exactly one player and now resolve to him: "Willis Reed"
  to Willie Reed and "Bernard King" to Gerard King. The other 34 read nobody
  (none of them has a single near spelling). Each answer ends "('Willis Reed'
  matches no player exactly and was read as Willie Reed, the only near
  spelling on record - ...)", so the reading is visible; before, the first
  asked "did you mean Davon Reed, ... or Willie Reed?" and the second "did you
  mean Gerard King?".
- **User sees:** an answer about a different, visible player where the real
  one is out of the warehouse's reach (#123's shape).
- **Next step:** none unless it recurs in a live run; if it does, #123's
  coverage-floor sentence is the fix, not a narrower edit budget, which would
  lose real typos (3,597 of the 10,602 measured surname typos default, all to
  the right player).
- **Source:** ours; DATA.md "Coverage floors" for why the players are absent.
- **GitHub:** #245

### `player_netpoints`' season-totals reading (`rate: "total"`) cannot be reached
- **Found:** 2026-09-27, plan item 6 step (d) round 2 (moving `templates/netpoints.py` onto the typed Scope).
- **Evidence:** `HONORED_SCOPING["player_netpoints"]` (`compose.plan.STATED_SCOPING` since 2026-10-05) is `{"order"}` and `rate` is in `SCOPING_SLOTS`, so `check_scope("player_netpoints", {"rate": "total"})` raises "player_netpoints cannot honor ['rate']" before the template runs (measured on 0a7140a). The parser writes no `rate` for "shai gilgeous-alexander netpoints season totals" or "... total netpoints this season" either (route `{'stat': 'netpoints', 'player': 'Shai Gilgeous-Alexander', 'season_type': 2}`), so both answer the play-type categories per 100 possessions. The template's `rate != "total"` branch ("Totals stay in `data`, and the `rate` slot asks for them") is reached only by a direct call - the shape `leaderboard`'s `rate` had before it was listed.
- **User sees:** per-100 category tables for a totals question. The headline carries the season totals ("468.3 overall (403.9 offense, 64.4 defense)"), so the number asked for is there; only the breakdown is in the other unit.
- **Next step:** decide whether per 100 possessions is the right breakdown for "total netpoints". If totals should be reachable, list `rate` for `player_netpoints` and have the parser read "total(s)" beside NetPoints as `rate: "total"`, with a test through the agent path; if not, delete the branch.
- **Source:** ours.
- **GitHub:** #252

### The Scope's door admits a float for an integer closed set: `shot_value` 3.0, `season_type` 3.0, `half` 2.0
- **Found:** 2026-09-27, plan item 6 step (d) round 2, checking which values the removed template fallbacks could still meet.
- **Evidence:** `reading._one_of` checks membership by equality, so `Scope.from_slots({"shot_value": 3.0})` keeps 3.0 - a float against `Literal[1, 2, 3]` - and `season_type` 3.0 and `half` 2.0 likewise, while `_whole` refuses `season` 2025.0 and `limit` 2.0 (measured on 0a7140a). The typed readers trust the Literal: `shots._shot_value` returns the float where the slot-dict code returned `int(3.0)`, `fingerprint` reads a season type of 3.0 as 3.0 where the slot-dict code fell back to the regular season, and `common._player_relation_season_type` passes it on the same way.
- **User sees:** nothing today: nothing writes these slots from a float - the stages and the parser write integers, and the router's schema typed `shot_value` as an integer until its model call went (5.0.0).
- **Next step:** make `_one_of` refuse a value whose type is not the matching allowed member's type (bool is already refused), with a test per closed-set field, watched to fail first.
- **Source:** ours.
- **GitHub:** #253

### The normalizer's `names` array has no `maxItems`, the bound the router's schema put on every array after a live hang
- **Found:** 2026-09-27, plan item 6 step (d) part 3, re-aiming the router's schema tests at the normalizer: `test_array_slots_are_bounded` could not be kept, because it would fail.
- **Evidence:** `NORMALIZER_SCHEMA["properties"]["names"]` is `{"type": "array", "items": {"type": "string"}}` (`query/normalizer.py`). The router's schema bounded `players` and `fields` because, measured live, an unbounded array under constrained decoding let the grammar permit "one more item" forever: the model emitted `["points","minutes","minutes"]` on one question and then hung for over five minutes on the next (the comment went with `router_prompt.py`). Not seen on the normalizer: three live runs over the 277 day10 wordings finished at about 5.3 minutes each.
- **User sees:** nothing measured; the risk is a question that hangs for minutes.
- **Next step:** bound `names` (measure the most names one recorded reply holds first), then re-record the normalizer's replies and make a live run the record, since a schema edit is a model-input change (AGENTS.md, "Any edit to the model's prompt").
- **Source:** ours.
- **GitHub:** #255

### A composed count under a condition prints its line raw: "had 34 games points >= 30 with Joel Embiid starting"
- **Found:** 2026-09-27, plan item 6 step (d) follow-ups, once a teammate's start reached the compiler-first counts as a condition.
- **Evidence:** "how many 30 point games did maxey have when embiid started" (parser reader, main warehouse) answers "Tyrese Maxey had 34 games points >= 30 with Joel Embiid starting in the regular season career (2021-2026)", where the same count unnarrowed reads "Tyrese Maxey had 86 games with 30+ points in his regular season career (2020-21 through 2025-26)". The number is right (34, checked against `player_game_log` directly); the narrowed sentence (`compose/sentence.py`) prints the predicate as `points >= 30` and drops "with ... in his".
- **User sees:** a right count in an awkward sentence.
- **Next step:** say the line the way the unnarrowed count does ("with 30+ points") before the narrowing's own phrase.
- **Source:** ours.
- **GitHub:** #257

### Easter is refused with a sentence that says a holiday is read
- **Found:** 2026-09-27, fixing #238.
- **Evidence:** stubbed offline through the whole agent (main warehouse): "lebron stats on easter" answers "'easter' is not something the games are read by - a weekday, a month, a holiday, "since <day>", a conference or a division is. Ask without it, or with one of those." (`refusals._non_calendar_situation`, refusals.py:103) - refusing a holiday while listing a holiday among what is read. Before #238's fix the same sentence answered "thanksgiving", "new years" and "martin luther king"; Easter is the one holiday word left that reaches it (`calendar.UNREAD_HOLIDAYS`, captured on purpose so it is refused rather than dropped). No research-corpus question names Easter.
- **User sees:** a refusal whose reason contradicts itself.
- **Next step:** give a name in `calendar.UNREAD_HOLIDAYS` its own sentence in `_non_calendar_situation` ("Easter moves with the church calendar, which is not read here - name its date instead"), or read Easter as a per-year list of dates, since it is no weekday-of-a-month rule.
- **Source:** ours.
- **GitHub:** #263

### Four readers after the parser still read the question as typed, typographic apostrophe and all
- **Found:** 2026-09-28, fixing #259 (the fold is at the parser's door, `parse.read_route` and `parse.reading_from_route`).
- **Evidence:** `query/agent.py` hands the raw question to `refusals.by_question` (line 457), `refusals.unanswerable` (lines 463, 522, 560), `player_named_on_a_team_only_question` (line 370) and `entities.compared_but_unmatched` (line 502), after the Reading is settled. None of the 25 typographic-apostrophe questions measured for #259 (the corpus's 19 and six probes) answered differently for it - every move they made is the parser's - but a denial ("doesn’t") or a possessive ("Embiid’s") each of those reads would read as typed.
- **User sees:** nothing measured.
- **Next step:** fold the question once where `Agent.ask` receives it for everything after the normalizer - or carry the parser's folded question on the Reading - so no reader after the parser keeps its own.
- **Source:** ours.
- **GitHub:** #273

### `_PLAYER_GAMES` still joins raw `games` rather than `real_games`
- **Found:** 2026-09-14, building the shared `real_games` list (issue #7);
  **re-scoped 2026-09-29** when the SQL-writing agent, the other reader of raw
  `games`, was removed
- **Evidence:** `real_games` (`fetch/repairs/real_games.py`) holds the 43,353
  rows of `games`'s 43,504 that are actually games (the gap is 151: 134
  placeholders, 23 team slots naming an id no franchise has, 11 phantoms and
  one duplicate), and every TEAM template reads it. `_PLAYER_GAMES`
  (`query/player_games.py`) still joins raw `games` - harmlessly today:
  `player_box_stats`, `plays` and `shot_chart` hold 0 rows against the 151
  dropped events, so no player read ever counted one, and the 302
  `team_box_stats` rows that exist for them are entirely NULL. The web health
  line was fixed 2026-09-18 (`_game_span` reads `real_games`), and the SQL
  agent whose schema summary named `games` is gone (5.0.0).
- **User sees:** nothing today.
- **Next step:** point `_PLAYER_GAMES` at `real_games` behind the golden
  comparison (no answer should move, by the measurement above), or record
  here why the raw table stays.
- **Source:** DATA.md, "`games` carries placeholder, duplicate and phantom rows"
- **GitHub:** #73

### Caveats in the answer's text that never reach `data["notes"]`, and remarks glued onto refusals
- **Found:** 2026-09-30, the notes inventory for `ROADMAP-TYPES.md` (an Opus agent reading `011091f`; reported, not re-verified).
- **Evidence:** thirteen groups (the inventory, section 5.1; `~/association-research/stages/notes-inventory-011091f.md`): every caveat of `single_game_high` and narrowed `player_stat`, both charts, the season-line redirect, the combined team record, the BPI headline remarks, the compiler's team path (`compose/__init__.py:85-99` has no `notes`); `record_when` bundles three or four remarks into one string (`templates/splits.py:1112,1279` when found; `compose/records.py` and `compose.say.say_record_when` now). The coverage caveat and name readings are appended to whatever came back, refusals and clarifications included (`agent.py:431-434,529-532`).
- **User sees:** on the web page, which renders from `data`, fewer caveats than the CLI's text holds.
- **Next step:** the typed notes of `ROADMAP.md` Phase 0 (every writer records a kind and facts) replace `data["notes"]`; do not patch the thirteen one by one.
- **GitHub:** #307

### A list of matched names is printed as a Python list, and four small wording faults
- **Found:** 2026-09-30, the notes inventory for `ROADMAP-TYPES.md` (an Opus agent reading `011091f`; reported, not re-verified).
- **Evidence:** `compose.say.decision_phrase`'s `also_matched` (`shotchart.py:418` until Phase 2, step 5) and `fingerprint.py:951` interpolate the list itself ("['Seth Curry', ...]"); `fingerprint.py:890` joins it. "({A}, {B} has no ...)" at `templates/players.py:1924` when found (`compose.say` now). The box-score floor is "the 1993-94 season" in `templates/common.py:2534` (gone with `templates/`) and "the 1994 regular season" in `conditions.py:269`. A history `limit` over 20 silently becomes 4 (`season_line.history_seasons`, over `MAX_HISTORY_SEASONS`). `render_shot_chart` and `render_fingerprint` (`shotchart.py:186`, `fingerprint.py:1035`) have no caller in `src`, only tests.
- **User sees:** brackets and quotes in a chart's note; otherwise nothing wrong, only uneven.
- **Next step:** join the names; the rest goes with each kind's one phrase (Phase 2).
- **GitHub:** #308

### Numbers an answer states that are only in its text, so no comparison of values can see them move
- **Found:** 2026-09-30, the review of `ROADMAP-TYPES.md` (an Opus agent over the stage snapshot of the 628 recorded questions at `7f6425b`); the answers quoted were re-read from the snapshot.
- **Evidence:** the shot chart's made/attempted ("10/22 made"); a single game's NetPoints possessions and win probability added; a career's true-shooting attempts; a matchup's "met 27 times in all, 4 of them with ..."; the compiled team total's playoff addendum ("78 more over a 7-game playoff run", `compose/team.py:297`). None is in `data`, so `scripts/stage_snapshots.py compare --values-only` would pass a rewording that dropped or changed one. Also in the snapshot: a combined line is carried twice in the nine multi-line questions (`stat`/`threshold` say `assists`/20 for "20+ point 5+ assist" while `above` holds both lines) - harmless today, a trap when the five carriers of a line become one type.
- **User sees:** nothing today.
- **Next step:** each moves into `data` (then the Result) before the Phase 2 slice that rewords its answer; `ROADMAP-TYPES.md` lists them.
- **GitHub:** #309

### Typed remarks: what the wrapping left uneven
- **Found:** 2026-10-01, closing ROADMAP Phase 0 (a kind on every remark); the five agents' reports.
- **Evidence:** (1) one withheld-stat remark has two wordings ("is not counted from a rebuilt line", `templates/players.py:258` when found; "is not read from a rebuilt line", `players.py:1779`) and the second carries `label` with no `stat` key, since only the label reaches it from `compose/present.py:531` (deleted 2026-10-05). (2) "so whether he played is unknown" is said with two or more teammates (`compose.say`'s with/without sentence; `templates/splits.py:1007` when found). (3) `_team_outlook_missing`'s hint always says "ask about the regular season", whichever snapshot holds the team (`compose.team_stats._team_outlook_missing_notes` and `compose.say`; `templates/teams.py:1829` when found). (4) `templates/games.py:1286` and `:1414` (gone with `templates/`) bound a local named `note`, which shadows the import: a later wrap inside either function raises `UnboundLocalError` on that path alone. (5) `floor` is used for "Covers his whole career on record" (`compose.say`, "Covers his whole ... career on record"; `templates/shots.py:452` when found), which says nothing was clipped. (6) a period ranking's minimum records why it was halved and not of what (`games.py:1952-1975`: `most` is only in the sentence). (7) NetPoints' `data["notes"]` restate three facts in other words than the text (`templates/netpoints.py:211-217` against 337, 355, 372 when found; `compose/netpoints.py` and `compose.say`'s NetPoints sayer now); only the text side is recorded. (8) the corpus reaches about half of the 98 writers; the rest are checked by the suite running them and by the source-reading test, not by a recorded answer.
- **User sees:** a wrong pronoun in (2), a possibly wrong hint in (3); otherwise nothing.
- **Next step:** each goes when its kind gets its one phrase (`ROADMAP.md`, Phase 2); rename the two locals in (4) with the next change to that file.
- **GitHub:** #313

### User text reaches the name index's LIKE unescaped: a name slot holding `%` or `_` matches most of the roster
- **Found:** 2026-10-01, the name-index agent's report (Phase 1); reported, not re-verified.
- **Evidence:** `entities.find_players(con, "_")` and `find_players(con, "%")` match most players: the pattern is built from the text with no escape, as the SQL's `ILIKE ?` was. The index reproduces it on purpose (no answer may move). Nothing in the 628 recorded questions triggers it. Also recorded there: two players who share a `display_name` (21 names, "Chris Smith", "Dee Brown") came back from `ORDER BY display_name` in an order DuckDB left unspecified and that changed with the rows being sorted; the index returns them in table order, so `find_players(..., limit=None)` differs from the old code in the order of such a pair on 30 of 478,304 inputs, never in the first candidate.
- **User sees:** nothing today; a name typed as "_" would ask among dozens of players.
- **Next step:** treat `%` and `_` in a name slot as literal characters, with a test; then no name matches them.
- **GitHub:** #315

### The stages run twice on 59 of 2,082 out-of-corpus wordings (2.8%); the roadmap quotes the corpus figure alone
- **Found:** 2026-10-03, the Opus review of Phase 1.
- **Evidence:** `~/association-research/stages/reader_pop.py` at `c38600f`: 63 of 2,710 readings run the stages twice - 4 of the 628 corpus questions and 59 of the 2,082 outside it (every one a child the stages declined, then the parent). `ROADMAP.md`'s "stages run once" row and `CHANGES.md` state the corpus figure.
- **User sees:** nothing; a second run costs no model call.
- **Next step:** state both figures in the roadmap row; Phase 3's grammar (the child named from the words alone) removes the second run.
- **GitHub:** #318

### `tests/routed.with_subject` is a reading path production never takes: it names the child with no stage run and skips `_two_teams`
- **Found:** 2026-10-03, the Opus review of Phase 1.
- **Evidence:** `tests/routed.py` ("a child the stages would decline stands"); 39 call sites hand the stages a route this way. A test can pass on a route `read_route` would never produce.
- **User sees:** nothing.
- **Next step:** for each call site whose question is in the recorded corpus, assert the route `with_subject` builds equals `read_route`'s; or route `ask_routed` through `read_route` with the route's slots stubbed and record which tests differ.
- **GitHub:** #319

### Two causes, two sentences, for a quarter's stat nothing rebuilds
- **Found:** 2026-10-09, Phase 3 step 0, moving `refusals._period_stat` onto the Reading
- **Evidence:** a player's quarter of an unrebuilt stat is refused by the point reader's `no_period_stat` cause ("A quarter or half has no per-period 'minutes' - the period's line rebuilds points, fieldGoalsMade, ..." - column names) and the league's quarter ranking by the reading's `period_stat` ("By quarter or half, a line is rebuilt from the play-by-play - points, field goals, ... - and 'minutes' is not among them ...") - one missing fact, two kinds and two wordings in `say.refusal_phrase`; `period_stat` is also recognized beside `no_period_stat` on a player's question, unsaid there.
- **User sees:** nothing wrong; two phrasings of one refusal.
- **Next step:** one cause for the fact (the reading's, decided from the words), one sentence in plain words; with Phase 3's period family (step 2), since it rewords an answer.
- **GitHub:** #343

### `calendar.conference_named` is reached by no reading
- **Found:** 2026-10-09, Phase 3 step 0, moving it out of `refusals.py`
- **Evidence:** none of the 2,710 readings (the 628 recorded, the feed's and the extra wordings) puts a conference or division word in the `team`, `opponent` or `teams` slot - the parser refuses one as a name (`test_a_conference_or_a_pronoun_is_never_a_name`) and the team readers resolve only real teams - so the `conference_named` refusal `compose.team_records` and `compose.team_stats` check at RUN fires only for a hand-built Reading (`test_a_conference_is_refused_by_name`). Its sentence was false until `14c4ba8`.
- **User sees:** nothing.
- **Next step:** delete it with its RUN cause and phrase when the conference-as-subject reading lands (the P1 "A conference or division the players or teams belong to ..."), or keep it as that reading's guard - decide there.
- **GitHub:** #344
