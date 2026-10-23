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

### Several named stats are not read as a list: a line or log falls back to a fixed set of columns, a sum is never computed, and a stat outside the set is refused
- **Found:** 2026-10-23, the lead, measuring Jeff's question on the Giannis refusal (lane p6-periods' finding 1) over the feed's 42 questions naming two or more stats.
- **Evidence:** the measure tagger reads one stat; with several named on a line or a log it reads none (`Reading.measure` None) and the line shows its fixed columns (PTS REB AST STL BLK TO FG% 3P% FT%): "Tyus jones points reb ast last 10 games", "westbrook ppg, rpg apg for the lakers". A sum is never computed: "josh hart points + rebounds + assists game log vs boston", "cade cunningham assists+rebounds all-time game log vs 76ers". A named stat outside the fixed set beside another is refused "ask about one stat" (six "curry ... ts% and efg% and turnover percentage" wordings). A filter stat is read as the measure and the display stats dropped ("jalen suggs points+rebounds game log 15+ fga"). "pippen ppg, rpg apg for the bulls" answers the Bulls' team points per game. The period floor checks the fixed line, so "Giannis first half points rebounds assists gamelog vs washington" is refused over field goal attempts. Lines over several stats (counts, rankings: "most games with 20 pts, 10 reb, 5 ast") read correctly. <!-- codespell:ignore hart - Josh Hart, a player -->
- **User sees:** the named stats only where the fixed set happens to hold them; no sum where one was asked; a refusal for a stat outside the set; one fluent wrong answer (Pippen).
- **Next step:** the measure tagger reads every named stat into the point's typed `measures` (and "+" between them as a sum, a derived measure), the line and log show the named columns leading, the period floor and bounds read only the named columns, a filter stat stays a filter; Pippen's "for the bulls" reads the player. Each wording a reader case.
- **Priority:** P1 - the asked stats ignored; one fluent wrong answer.
- **Source:** ours.
- **GitHub:** #540

### A quarter's free-throw percentage is read as points: "jokic 4th quarter free throw shooting percentages" answers his 4th-quarter points
- **Found:** 2026-10-23, lane p6-periods, reading its moved answers.
- **Evidence:** four feed wordings ("jokic / lebron / luka / shai 4th quarter free throw shooting percentages") answer the period line led by points; the free-throw-percentage words are not read as the measure on the period read (before and after the lane's change).
- **User sees:** a fluent figure for a different stat.
- **Next step:** the measure tagger reads "free throw (shooting) percentage(s)" on a period question as FT%, which the period line holds (FTM, FTA); a reader case per wording.
- **Priority:** P1 - a fluent wrong answer.
- **Source:** ours.
- **GitHub:** #527

### A lone word of a name reads the only player on record with it, the rest of the name unread: "tyrese maxi last 5 games vs pistons" answers for Maxi Kleber
- **Found:** 2026-10-17, lane p6-round (the playoff round), probing its moved readings.
- **Evidence:** a word that is a whole word of exactly one player's name resolves to him even where the question's other name word belongs to someone else: "tyrese maxi last 5 games vs pistons" answers Maxi Kleber's games ("tyrese" unread), "wilt chamberlain vs kareem stats" Kareem Rush's. A blanket rule breaks right readings ("lebron rookie stats", "zion william"): 288 lone-first-name matches across the 2,698 readings.
- **User sees:** a fluent answer about a player the question did not name.
- **Next step:** a rule on the reader's own unread words (a name word left unread beside the matched one refuses or re-resolves), measured on the 288.
- **Priority:** P1 - fluent wrong player.
- **Source:** ours.
- **GitHub:** #517

### "before / until / through <bare year>" is read as that one season: "most 3s in an nba game before 2010" answers 2010's high
- **Found:** 2026-10-17, lane p6-sentence (D17), measuring the feed sample's FS142 after the game-level ranking moved to its reader.
- **Evidence:** the span tagger reads a bare year after "before", "until", "through" as the season itself, not a range bound. Feed wordings: "most 3s in an nba game before 2010" (FS142; the key: Kobe Bryant and Donyell Marshall, 12 each), "most made 3 pointers before 1991", "Most made 3 pointers before 1995", "which team has made the most three pointers in a season before 2015". A closing date ("prior to 02/03/2024") is a different entry, refused by value.
- **User sees:** a fluent answer over one season where a range was asked.
- **Next step:** `span.read_span` reads a bare year after these words as a range bound (`until`), deciding whether "before" excludes the year named (it should: "before 2010" ends with 2009); a reader case per wording; the feed moves enumerated.
- **Priority:** P1 - a fluent answer for the wrong span, on four feed wordings.
- **Source:** ours.
- **GitHub:** #505

### "most points without missing in a nba game" answers the plain high, past "without missing"
- **Found:** 2026-10-17, lane p6-sentence (D17), reading its feed moves.
- **Evidence:** the reading leaves ['without', 'missing'] unread and the answer is the plain single-game high (Bam Adebayo, 83). The question asks the most points in a game with no missed shot.
- **User sees:** a fluent answer to a different question.
- **Next step:** read a perfect-shooting condition (field goals attempted = made, or free throws too) as a line on the games, or refuse it by a cause naming the words; until then `Reading.defaulted` should refuse the unread "without missing".
- **Priority:** P1 - fluent wrong.
- **Source:** ours.
- **GitHub:** #506

### Two adjacent bare years are read as a two-season range: "nba most steals player 2023 2024" answers the two seasons' total where the key reads 2023-24
- **Found:** 2026-10-17, lane p6-sentence (D17), FS243 against its key.
- **Evidence:** `lexicon.RANGE_BARE_YEARS` (deliberate since #289) reads "2023 2024" as 2023 through 2024. The sample's key reads the one season 2023-24 (Shai Gilgeous-Alexander and De'Aaron Fox, 150 each); the answer is the two-season total (Gilgeous-Alexander 262). Three feed wordings ("nba most blocks / steals / steals player 2023 2024").
- **User sees:** a two-season figure where one season was meant (the heading shows the range).
- **Next step:** Jeff's call between the two readings; if one season, "YYYY YYYY+1" reads as that season (the name it ends in), a reader case per wording.
- **Priority:** P1 pending Jeff's call - fluent, the span visible in the heading.
- **Source:** ours.
- **GitHub:** #507

### A singular "game" against a team is read as the career's log: "D'Angelo Russell game against the Timberwolves" lists his last 10 of 21 meetings
- **Found:** 2026-10-17, the lead, the keyed score (`~/association-research/stages/score_keyed.py`) over step 2 (runs 79 -> 86): the feed sample's FS331, graded `correct` when it answered his most recent game, moved under decision D13's "versus" career rule to "D'Angelo Russell vs the Minnesota Timberwolves, last 10 of 21 games of his career (2016-2026 regular seasons)".
- **Evidence:** the key reads the singular "game" as his most recent game vs Minnesota (2025-11-17 for DAL, L 96-120: 8 pts, 4 ast, 2 reb in 22 min); the reading has no window, so the log's default of ten stands over the career the versus rule implied. The yardstick's F028 ("How many points did Jokic score in the 3rd quarter against Boston?", the key's dominant reading the most recent meeting) moved the same way, to a career total.
- **User sees:** a ten-game career log where one game, the latest, was asked - fluently.
- **Next step:** a singular "game" (not "games", "gamelog", "log") beside a versus word or an opponent is a window of one, the newest, on the games relations (`window.read_window`'s games word, `point.window_under`); the span rule stands under it. Each wording re-run against its key (FS331 the sample's, F028 the yardstick's); `score_keyed.py` then holds them.
- **Priority:** P1 - a fluent answer to a different question on a graded wording.
- **Source:** ours.
- **GitHub:** #490

### A player named by a surname others share, with no model name, is read as nobody and the league answered: "Nba jamison career high point" answers Bam Adebayo's high
- **Found:** 2026-10-17, lane p6-homographs, reading the homograph harness's SURNAME list against the feed's recorded replies.
- **Evidence:** the subject reading reads a player from the question's own words only where a span is a whole word of exactly ONE player's name (`subject.players_named_in`), or the model named him; a surname several players share ("jamison", "brunson", "randle", "curry", "davis"), a name the warehouse lacks beside namesakes ("rick barry", "billy cunningham", "rip hamilton") or a father and son ("jaren jackson") is read by nothing when the recorded model reply names nobody, and the question is the league's. On the feed at `37934b66`: 81 questions whose reply names nobody, whose reading holds no player, team or companion, and whose words hold a whole word of two or more players' names (`~/association-research/stages/lone_surnames.py` over a feed run); 15 answer fluently, 6 of them wrong - "Nba jamison career high point" and "Nba rip hamilton career high point" ("Bam Adebayo had the most points in a single game ..."), "curry log vs phx dreb" and "jaren jackson jr. vs nets 30+ minutes" (every player vs the Suns, the Nets), "Billy Cunningham win% per season for games played" and "bill russell vs maurice stokes record" (the 30 teams' 2026 record) - and 66 refuse, mostly `ranking_needs_stat` ("brunson vs celtics last 5 games", "randle vs toronto", "wade 2011 playoff stats", "Love stats 2012-14"), the wrong cause. 15 are feed-sample ids (FS023, FS182, FS238, FS298, FS212, FS222, FS314, FS038, FS067, FS376, FS059, FS328, FS329, FS348, FS005), 9 graded `refused_wrong_cause`.
- **User sees:** the league's answer, fluently, where one player was named (6 on the feed), or a refusal naming the wrong cause (66).
- **Next step:** the subject reading reads a lone surname (or a run of name words) of several players as typed where the reading holds no other subject - the rule "alone as the subject with nothing else to be" - for the entity index to resolve by the recency rule (visible) or ask; a dictionary word only where it is capitalized or beside another word of a name (`_question_players_in_full`, `37934b66`). Every refusal that turns fluent graded by the sample's key, never by reading the answer; then the rest of the 81.
- **Priority:** P1 - six fluent answers about the league where a player was named.
- **Source:** ours (the reader; the 3B's empty replies are the trigger).
- **GitHub:** #488

### A team ranking asked of the best single season across years answers the current season: "Which team has made the most three pointers in a season" ranks 2026's teams
- **Found:** 2026-10-14, Phase 5, step 6 (lane L), refusing the players' half (`point._everyone_single_season`, the `single_season_ranking` cause).
- **Evidence:** on `98b22dd`'s feed run, "Which team has made the most three pointers in a season" answers "3-pointers made per game, 2026 regular season - highest first, of 30 teams: 1 Charlotte Hornets 16.4 ...", "which team has made the most three pointers in a season before 2015" the 2015 season's, and "highest ppg*ts% by a team in a season" 2026's points per game - each a per-game figure over one season where the most in any season was asked ("made", "season" unread). The players' ranking refuses the class by name since lane L's commit; the team ranking (`point.team_read_point`, the team-season relation) has no such check.
- **User sees:** one season's per-game team table, fluently, where the best season of any year was asked.
- **Next step:** the team ranking refuses `lexicon.SINGLE_SEASON_RANKED` where the span is no season the words name, as `point._everyone_single_season` does - or reads the team-seasons table ranked across seasons (a new by, D11's) - each wording enumerated.
- **Source:** ours.
- **Priority:** P1 - a narrower question answered fluently; 3 of the 2,082.
- **GitHub:** #441

### "Embiid's record against Boston this year" answers a with/without split of the 76ers over the regular season; the question is Embiid's own games, playoffs included
- **Found:** 2026-09-26, Jeff reviewing the pipeline walkthrough page (yardstick-v2 F088, all five wordings; blind key 4-2).
- **Evidence:** `live_day10.jsonl` (bcf30a8): the router files `head_to_head` (team PHI, opponent BOS); the reading's rule "a player's record against a team, not two teams meeting" (`subject._decide_intent`) reroutes to `with_without` with `without=[Joel Embiid]`, and the template answers "Philadelphia 76ers with and without Joel Embiid vs the Boston Celtics, 2026 regular season: out 2 1-1, played 2 1-1". The key (blind, reading a, dominant): the 76ers went 4-2 in the 6 games Embiid played vs Boston in 2025-26 - 2 regular-season meetings and playoff Games 4-7 (he has no box row for Games 1-3). Two faults: the SHAPE (a split with an "out" row nobody asked for, where the question is his games and their record - dates, outcomes, and reasonably his line in each), and the SCOPE ("this year" narrowed to the regular season by the unstated-season-type default, `_validate_season_type`; `season_type_unstated` widens only "last N games" and "including playoffs" wordings today).
- **User sees:** a fluent table about a different question, with the right numbers for the narrower scope it states - which is why the blind grader passed all five wordings; re-graded wrong on Jeff's call (overrides run day10). Day10 is 160/175 and families 150/166 after the re-grade.
- **Parser path (2026-09-27, plan item 6 steps b-d):** the SHAPE is fixed - the parser reads a player's record with no companion as `player_splits` ("Joel Embiid vs the Boston Celtics, splits, 2026 regular season (2 games he played) ... W-L 1-1"), graded correct in `live_parser1`-`3` under Jeff's 2026-09-27 instruction to score the season type as the regular-season default, and the router reader that still answered the with/without split is gone (step d). The SCOPE half (regular season only) is the deferred season-type decision, unchanged.
- **Next step:** two decisions for the parser (ROADMAP: the consolidation). Shape: a player set against a team is his games narrowed by opponent - the player-games relation, rows with W/L and his line plus the tally - never the team's with/without split (the reroute rule goes; the compiler's rows shape with a record summary is the nearest thing today). Scope: "this year" / "this season" with no type named reads both types and says so, the way `season_type_unstated` already does for "last N games" - a policy for every question, to confirm with Jeff, since the regular-season default is what every per-game average answers under today.
- **Priority:** P1 - a different question answered fluently (tagged at Phase 5's close, 2026-10-14: the review found it untagged in the P1 section).
- **Source:** ours, not ESPN's.
- **GitHub:** #231

### A question naming no season defaults to 2026 where its words read the career: the remainder after D13's first pass
- **Found:** 2026-10-15, the feed sample's grading (Phase 6, step 1), the lead's hand pass; Jeff's ruling D13 the same day. Rewritten 2026-10-16 by lane p6-span, which read the career for ten wording classes (`span.IMPLIED_CAREERS`, `CHANGES.md`): of the sample's 54 verdicts in the class (`season-default` and its log variant), 41 now read the span the key reads; lane p6-d15 read FS225 and FS032 (D14 and D15) and the period reads' careers (FS027, FS177, FS354: `point._PERIOD_GAMES_ASKS`, `compose.periods` across seasons) and said the career as the default it is (the `career_implied` decision), and FS391 ("jalen brown vs pacer playoff double team") now answers his 8 playoff games against Indiana fluently past "double team", a defense nothing in the warehouse records (the key's own reading: not answerable as asked, then the whole record) - no meaning class holds the word.
- **Evidence (what remains, the sample's ids):** one season asked across years, which no reader takes (FS162 "michael jordan's best season", FS332 "highest plus minus season by an active player", FS327 "... on a season": #441's shape for players); a team's all-time roster ranking (FS214 "least games played with lakers"); a floor of attempts one season could hold (FS310 "worst 3pt% with atleast 1000 3pt attempted": Harden attempted 1,028 in 2018-19, so the floor alone does not imply the career); an active player's playoff log (FS043 "anthony edward's playoff game log": the newest 10 rows are 2026's either way, the key's 52-game career a page further); a first name nobody resolves before "vs" (FS095 "ron artest vs carmelo anthony": Metta World Peace's old name, guarded so his career is not answered for Carmelo alone).
- **User sees:** a refusal naming 2026 where the career holds the games (the season superlatives), or a 2026 ranking where an all-time one was asked.
- **Next step:** the season superlative as #441's shape for a player; each re-graded against the sample's key.
- **Priority:** P1 - the remainder of the largest wrong-answer class the sample found.
- **Source:** ours.
- **GitHub:** #470

## P2: misleading or incomplete

### A franchise career ranking is declined at RUN, not by the planner: "Most reb by a hawk player history of all time" says "Nothing here answers"
- **Found:** 2026-10-23, lane p6-alts (D23's pilot)
- **Evidence:** `rankings._leaderboard_career_refusals` declines a franchise's career leaderboard after the planner planned it; 3 feed answers, and 5 offered alternatives lead to it.
- **User sees:** a generic decline where a cause or an answer belongs.
- **Next step:** move it to the planner as a cause (one enumerated commit), or let the game-level ranking answer it.
- **Priority:** P2.
- **Source:** ours.
- **GitHub:** #528

### The compiler's own-sentence log skips D15's tenure cut: "Quentin grimes individual gamelog usage rating without joel embiid over his career" reads 2022-2026
- **Found:** 2026-10-23, lane p6-alts (D23's pilot)
- **Evidence:** the usage log reads Grimes's whole career; "...game log without joel embiid over his career" reads the 2025-2026 tenure (63 games); F159's key is 67.
- **User sees:** a career "without" log over seasons the two were never teammates.
- **Next step:** the compiler's log narrows by the companion's tenure as the readers do.
- **Priority:** P2.
- **Source:** ours.
- **GitHub:** #529

### "active players" is unread: "highest plus minus active players" ranks every player
- **Found:** 2026-10-23, lane p6-alts (D23's pilot)
- **Evidence:** `Reading.unread` holds "active"; the ranking reads every player, and its career alternative every career since 1994 per game, where the keys (FS112, FS390) want active players' career totals.
- **User sees:** a ranking over players the words excluded.
- **Next step:** read "active" as the players with a box score in the latest season (`names.ActivePlayers` holds them).
- **Priority:** P2.
- **Source:** ours.
- **GitHub:** #530

### The research harness counts a RUN refusal as fluent, because the snapshot records none
- **Found:** 2026-10-23, lane p6-periods; the lead measured it
- **Evidence:** `fluent()` (`~/association-research/reviews/phase5-2026-10-14/harness/fluent.py`, used by `classify82` and `score_keyed`) reads only `answered_by`, `refused` and "Nothing here answers"; 72 of the feed's "fluent" answers at sweep93 are RUN refusals ("No 2026 regular season games found for Damian Lillard", "No player found matching 'mpj'"); a fluent -> refusal move (Giannis's period log) was classed F->F.
- **User sees:** nothing; the proof's classes miss refusal moves.
- **Next step:** the stage snapshot records the RUN refusal's kind on the answer (`stages.py`, one field), and `fluent()` reads it.
- **Priority:** P2 - tooling the proof rests on.
- **Source:** ours.
- **GitHub:** #531

### Team readers rebuild the cuts field by field, so a new cut is dropped silently until each is taught
- **Found:** 2026-10-17, lane p6-round (the playoff round), fixing `320546c9` ("knicks second round record 2026" answered the postseason's 16-3, the round's 4-0).
- **Evidence:** `compose/highs.py`, `logs.py`, `periods.py`, `meetings.py` build a team's `Cuts` by naming each field, so a cut added to `reading.Cuts` reaches them only when each is edited; the round was the third such drop (`7ae6a571`: a team's record over a teammate's line kept only venue and opponent).
- **User sees:** a fluent answer over more games than asked, nothing said - the failure shape the project keeps producing.
- **Next step:** pass the scope's cuts whole (a `dataclasses.replace` over the read's own), or a source test that every `Cuts(` built on the answer side names every field.
- **Priority:** P2 - a silent narrowing dropped, found three times.
- **Source:** ours.
- **GitHub:** #518

### "best 3 point shooters" is read as made threes, not the percentage: "nba best 3 point shooters last 5 years" ranks 3PM
- **Found:** 2026-10-17, lane p6-sentence (D17), FS253 against its key.
- **Evidence:** the key ranks 3P% with a floor of 1,000+ attempts over the five seasons; the measure tagger reads "3 point" as made threes, and the game-level ranking qualifies by games only, not attempts.
- **User sees:** a ranking of volume where accuracy was asked.
- **Next step:** "best <stat> shooters" reads the stat's percentage, with an attempts qualifier the ranking states (`Reading.minimum` by attempts on the game-level ranking).
- **Priority:** P2 - a fluent answer to a neighboring question, its measure in the heading.
- **Source:** ours.
- **GitHub:** #508

### Three wrong causes on rankings: "nba best shooting percentages 2023", "most career points by a point guard below 8000", "most 10+ pointd regular season games"
- **Found:** 2026-10-17, lane p6-sentence (D17), the sample's ranking ids.
- **Evidence:** FS072 gets the generic `ranking_needs_stat` though "shooting percentages" names a stat family; FS137 gets `line_names_no_stat` though "points" stands before the subject - it is a career-total line no reader keeps; FS395's "pointd" is a misspelled stat word no near-spelling reader handles.
- **User sees:** a refusal naming the wrong missing thing.
- **Next step:** per id: name which percentage (FG%, 3P%, TS%) the ranking needs; refuse FS137 by the career-total line; a near spelling of a stat word reads as the stat, as a name's does.
- **Priority:** P2 - wrong causes.
- **Source:** ours.
- **GitHub:** #509

### A log shows its newest ten rows, where decision D24 asks every row: "Rudy gobert first half games this season" lists 10 of 76
- **Found:** 2026-10-17, the lead, the Phase 6 step 5 re-grade: 6 yardstick wordings (F060, F149, F153, F156, F158, F159) and 11 sample ids (FS016, FS058, FS068, FS098, FS132, FS208, FS096, FS126, FS282, FS378, FS400) graded `partial` for showing the newest ten of N; **decided 2026-10-23 (D24, Jeff): every row, the standard retired** - the yardstick's parser32 overrides withdrawn (162 -> 155 of 175).
- **Evidence:** `reading.DEFAULT_GAME_LOG_LIMIT = 10`, applied in `point.py` (three log points), `compose/team.py` (the team log) and `compose/periods.py` (the period log) through `_clamp_limit`; F147 is a line of averages where the rows were asked.
- **User sees:** ten rows of N, the total said.
- **Next step:** the default cap goes - a log reads every row its reading selects, a limit only where the words state a window; the page's renderers show the whole table (scrolling; `page_renders.py` before and after), the CLI prints it; F147's "stats on christmas" lists each game under the line. Every log answer on the populations moves (rows added, figures kept): one enumerated commit, classed.
- **Priority:** P2 - a partial answer on 17 graded wordings.
- **Source:** ours.
- **GitHub:** #503

### A name is not eliminated by season type: "Leonard final 2013" asks Kawhi Leonard or Meyers Leonard, though only Kawhi played that postseason
- **Found:** 2026-10-17, lane p6-causes (the sample's FS059); the round half fixed by lane p6-round (`f3190215`: "final" beside a year is the Finals).
- **Evidence:** with Kawhi named, FS059 asks "Kawhi Leonard or Meyers Leonard?"; the name elimination (`entities.resolve_player`, `_named_in_full`) asks who played in the SEASONS asked, not in the season type - both played 2013, only Kawhi the 2013 postseason. The recorded reply names nobody, so on the recorded population FS059 is refused "No player or team is read" (the entry below on a unique name the model leaves out).
- **User sees:** a question back where one candidate alone could be meant.
- **Next step:** the elimination reads the span's season type where it is the postseason.
- **Priority:** P3.
- **Source:** ours.
- **GitHub:** #491

### A team's points allowed is read as a player ranking: "nba team points allowed per game 2025" is refused as a team-relations question
- **Found:** 2026-10-17, lane p6-causes, the sample's FS091; "Least points allowed nba teams this season per game" the same.
- **Evidence:** "points allowed" is no measure of the teams' parent row, so the words fall to the players' ranking and the planner refuses `team_relations_question`; the key ranks all 30 teams by points allowed per game.
- **User sees:** a refusal with a false cause where a table of 30 teams was asked.
- **Next step:** the teams' parent row reads "points allowed" (the opponents' points, `Measure.whose`) as a team metric of the season table.
- **Priority:** P2.
- **Source:** ours.
- **GitHub:** #492

### The subject seam's leftovers from step 3: a team after a window or in a list, "ron artest", "mpj", "dirk novinski", "playpff", and "may" with no model name
- **Found:** 2026-10-17, lane p6-causes, held sample ids its files could not reach (the subject reading's).
- **Evidence:** FS367 "spurs vs okc log on road" reads no team for "spurs" beside "vs okc" (`subject_not_read`); FS160 and FS286 place no team after a window or in a list of opponents (`team_not_read`); FS095 "ron artest" and FS296 "dirk novinski" are not read (a nickname, a typo beyond the near-spelling pass); FS209 "mpj" is no initialism on record; FS279 "playpff" is read as a name; FS238 and FS298 read "may" as Sean May where the feed's replies name nobody (#488's class).
- **User sees:** refusals naming the wrong missing thing on nine sample wordings.
- **Next step:** each in `subject.py`/`entities.py`: a team word beside a versus word read as the team whatever else stands; a window or a list before a team; "ron artest" and "mpj" in the nickname and initials tables; a typo past one edit asked about by name; "may" under the homograph rule when no model name anchors it (#488).
- **Priority:** P2 - nine wrong-cause refusals on graded wordings.
- **Source:** ours.
- **GitHub:** #493

### A team's games or count over a line, given up or its own, are named from the words only after a window: "celtics games where they gave up 120 points" is refused for an unread number, and "celtics game log when they score 120+ points" answers its record over the line
- **Found:** 2026-10-13, the P1 sweep's lane L (`sweep-L-325`), probing #348's neighbors; the record half fixed by lane T2 (`Line.whose`, the team's record over a line split by the opponents' score or box row); the log half by Phase 5, step 6's lane T (`p5s6-T`): a team's log keeps its games past a line on its own score or box row or its opponents' (`TeamNarrowed.narrow_line`, `compose.logs._team_log_lines`) - "celtics last 10 games when allowing 120+ points" lists the 7 games of 2026 the Celtics allowed 120+, where it was declined "relation: a line on a box-score stat keeps a PLAYER's games; a team's log has no such column".
- **Evidence:** probed on `p5s6-T` with the main warehouse and no model: the log reads a line only where a window names the log ("celtics last 10 games when ..."). Without one, the grammar names the team's season line (`team_seasons/scalar/line`) and the point drops the line, so step 1's cause refuses the number: "celtics games where they gave up 120 points", "how many games did the celtics allow 130 points this season", "celtics games with 120+ points this season", "how many games did the celtics score 130 points this season", "celtics 120 point games this season", "how many 120 point games did the celtics have this season", "celtics record in games they allowed 130 points this season" (the line tagger reads no line from "allowed 130 points" there) - each "'120' is a number this reading did not use ...". And "celtics game log when they score 120+ points" is read as the team's record over the line (`team_games/split/line`) and answers "Boston Celtics record when they had 120+ points ..." - a log asked, a record answered, visibly. 0 of the 628 and 0 of the 2,082 hold the wordings.
- **User sees:** a refusal naming the number for a team's games or count over a line; a record split where a log was asked ("game log when ...").
- **Next step:** (lane L's seam, the count's grammar and the line tagger, Phase 5, step 6) name a team's count from "how many games did X allow N" and its log from "games where they gave up N" as the scored form would be named; name a team's log from "games where/with/when <a typed line>" and "game log when <line>" as the player's log is named, and a team's count of games over a line (the record over the line's G column, or a count key - what a new shape brings, D11, if the record is not taken as the count); read "in games they allowed N points" as an opponent's line. The relation reads the line on the log already.
- **Source:** ours.
- **Priority:** P2 - a record answered where a log was asked (visible, its heading says "record"); the rest refusals by name.
- **GitHub:** #430
### A season line on a percentage, on the game-level ranking or on a figure no season line holds is refused: "nba players who average 18 ppg 3.5 assists and shot 54% or better" has no reader
- **Found:** 2026-10-12, the Phase 3 review's cleanup (b)5 (the feed's unread numbers).
- **Evidence:** since 2026-10-13 (the P1 sweep's lane L, D4 lifted for the season ranking's shapes by Jeff) the season ranking keeps its players past a line on the season's average or total, a career's on a career ranking (`reading.Line.per`, the `season_line` cell), "how many players ..." counts them (`reading.PLAYERS_COUNTED`), and "most seasons averaging 30 ppg" ranks the players by how many of their seasons hold the line (`player_seasons/ranking/seasons`, `season_line.rank_seasons_counted`). What is left: (1) a percentage beside a season line ("and shot 54% or better") is refused by `season_line_unread`; (2) a season line on a ranking the game level reads - a position group ("least amount of fga per game to score 18 points per game by a guard", 2) or a qualifier in a unit the metric does not qualify by ("highest true shooting percentage in a playoff run minimum 10 games and average 10 ppg") - is declined ("players who averaged 18+ points per game cannot be read here"); (3) a count of seasons over a figure no season line holds ("most 60 win seasons", a team's wins; "most seasons with a per of 30") is refused by `seasons_counted`; (4) "nba all-time ast/to leaders with > 5 apg" ranks career assists, "ast/to" (`assistTurnoverRatio`) read by no measure word; (5) "How many players averaged 30 ppg in the 80s" answers 2026, the decade unread (the span's, #393); (6) a player's own count of seasons ("how many 30 ppg seasons does lebron have") is not read.
- **User sees:** for (1)-(3) a refusal or decline naming the line; for (4) a ranking by the wrong figure under the right line; for (5) the current season, named in the answer.
- **Next step:** a percentage season line (`fieldGoalPct` and kin over the season's makes and attempts), the season line on the game-level ranking, "ast/to" as a measure word, a player's own count of seasons.
- **Priority:** P2 - (4) and (5) answer a narrower or different question on 2 of the 2,082, the rest are refusals.
- **Source:** ours.
- **GitHub:** #390 (#391, the count of seasons, folded into it)
### Every reader keyed on a line but the team's record reads its number as "N+", whatever its comparison
- **Found:** 2026-10-13, the P1 sweep's lane T, fixing #325's answer half.
- **Evidence:** `lines.threshold_of` hands a reader the keyed line's NUMBER alone, and every caller but the team's record (since lane T) compares with `>=`: the point reader's log predicate (`point._threshold_line`: `[(col, ">=", threshold)]`) and its count, record, run and high points (`point.py` 382, 399, 690, 839, 914, 998), the player's record over a line (`compose.records._own_line`), the count (`compose.counts`), the run (`compose.runs`), the log (`compose.logs`). No reading writes a keyed line with another comparison today (0 of the 123 keyed lines on the 2,710 readings are not `>=`), so nothing answers wrong yet - but the reading half of #325 (or any "more than N" / "N or fewer" a tagger keys) would be answered as "N+" on every one of these shapes, fluently.
- **User sees:** nothing yet; once a keyed line under a number is read, "maxey record when he scores 10 or fewer points" would answer his 10+ point games. Since #325's reading half (2026-10-13, lane L's `sweep-L-325`) the reader keys a line under a number on a TEAM's record alone (`point._team_record_own_line`), the one reader that reads the comparison; a player's record keeps the line as a narrowing beside its keyed `>=`, as before.
- **Next step:** read the keyed line whole (`threshold_line`) where a reader compares, as `compile_team_line` does, or refuse a comparison the shape does not say (a cause naming the line) - in the same change that first writes a keyed line other than `>=`, with a test per shape.
- **Source:** ours.
- **Priority:** P2 - latent: a wrong answer the moment a tagger keys a line under a number.
- **GitHub:** #410

### A team's stat asked under a companion's line or presence answers the team's record: "how many fgm did the celtics make when tatum scored 30" answers the Celtics' record when Tatum had 30+ points
- **Found:** 2026-10-13, the Phase 4 review (`~/association-research/reviews/phase4-2026-10-13/REVIEW.md`, finding 2); measured and narrowed to the reader in the P1 sweep's lane T3 (2026-10-14).
- **Evidence:** on `8de7d33`, through the agent with the recorded-reply shape of reply: "how many fgm did the celtics make when tatum scored 30", "celtics rebounds when tatum scored 30" and "celtics points per game when tatum scores 30+" all read `player_games/split/line` with the measure `points` - the companion's line's stat (`point._measure_key_by_words`: `context.line_stat` overwrites the key the words or the model named) - and answer "Boston Celtics record when Jayson Tatum had 30+ points ..."; the asked stat survives nowhere on the Reading, and the claims ledger cannot see it either (the model's `fieldGoalsMade` accounts for "fgm", so `unread` is empty). The presence half: "sixers rebounds per game when embiid plays" answers "Philadelphia 76ers with and without Joel Embiid" (W-L and margin, no rebounds), and the feed holds 3 of the class - "golden state's offensive rating with green", "suns offensive rating when all three of kevin durant, devin booker, and bradley beal played", "Suns offensive rating when durant, booker and beel played" (`offensive`, `rating` unread). 0 of the 628.
- **User sees:** a record (or a with/without record) where a team figure was asked; the heading says "record", so the substitution is visible.
- **Next step:** the reader keeps the named stat apart from the line's (`point.measure_under` under `LINE_RECORD`: a stat the words name other than the line's own is the figure asked, not overwritten), and the planner refuses a team figure other than the record over a companion's line or presence by a cause naming the figure and the condition (a kind in `reading.CAUSES`) until the team relation reads it - the team's per-game line per group, as `compose.presence`'s player half shows his (`conditions._TEAM_LINE` holds points, allowed, rebounds, assists, threes, FG%). Not fixed in lane T3: both halves start in the reader, which the lane's brief left untouched for this entry.
- **Source:** ours.
- **Priority:** P2 - a different figure than asked, visibly.
- **GitHub:** #401

### The team log drops the stat it was asked for and shows the score: "show the 76ers 3pt% for each game last season" lists W/L and the score, no 3pt%
- **Found:** 2026-10-13, Jeff's queries on `a5a4e68`.
- **Evidence:** the reading carries `measure={'key': 'threePointFieldGoalPct'}` and the compiled team log executes `measures=['points']` - the team-games relation has no box-score join (`AGENTS.md`, "A team can be the subject"), so a team log can show the score and the result and nothing else, and nothing says the asked stat was dropped. The same question with "pace" reads the model's `points` over the words' "pace" (`measures=['points']`, `measure.key` `points`), where the season-line form of the question ("the 76ers game with the highest pace") reads `pace` from the words: the log row's measure on a team keeps the model's key. The team compiler's TOTAL refuses a box-score count narrowed to a window for the same want of a join; the log does not.
- **User sees:** a log of scores where a per-game shooting figure was asked, with no sentence saying so.
- **Next step:** a team log asked with a box-score stat refuses by a cause naming the stat (as the team total does) until the team-games relation joins `team_box_stats`, which would let it show the column; and the measure tagger reads the words over the key on the team log as it does on the season line.
- **User sees:** ten games where the season's every game was asked. Since the P1 sweep's lane T (2026-10-13) a team's log says the count it cut from - "last 10 of 82 games of the 2025 regular season" - as a player's had (F149), so the narrowing is stated; the window is still the newest 10.
- **Next step:** (the window tagger's, lane S) "for each game", "every game", "game by game", "per game" (on a log) read as the whole span - a window of no count, or the log's window set to the span's games; a test per wording; the 2,710 readings and the feed measured for the wordings' reach before the rule lands.
### A team log's measure keeps the model's key over the words: "show the 76ers pace for each game last season" reads `points`
- **Found:** 2026-10-13, Jeff's queries on `a5a4e68`; rewritten 2026-10-13 by the P1 sweep's lane T, which fixed the answer side.
- **Evidence:** the team-games relation joins `team_box_stats` since lane T (`compose.team.team_figure`): a team's log asked for a box-score figure shows it beside each game and over them all ("Philadelphia 76ers, last 10 of 82 games ...  3P% 27.3% (12-44) ... 3-point percentage: ..."), and a figure no team game carries is refused by name (`team_figure_unread`: "A team's games carry no pace game by game ..."). What is left is the reading: "show the 76ers pace for each game last season" with the model's `points` reads `measure.key` `points` (the words' "pace" unread), where the season-line form ("the 76ers game with the highest pace") reads `pace` from the words; with the model's `pace` the measure reads None. So the log shows the scores and its total points, and the pace refusal is never reached. Two teams' meetings read the same way since #326 (2026-10-13, lane T): "knicks vs celtics pace" with the model's `pace` reads no measure and answers the series record alone, where a hand-built reading's `pace` is refused by name (`team_figure_unread`, tested).
- **User sees:** the scores and a points total where pace was asked, nothing saying pace was not read.
- **Next step:** (the measure tagger's, lane L) the words over the key on a team log as on the season line; the log then refuses pace by name, as it does on a hand-built reading.
- **Source:** ours.
- **Priority:** P2 - the asked figure silently missing.
- **GitHub:** #403

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

### A comparison over a season no named player has a line in prints a table of dashes, not a refusal
- **Found:** 2026-09-30, roadmap review (agent A), under a faked October
  date (see the rollover entry in P1).
- **Evidence:** `compose.seasons._player_compare_lines`: "compare sga and
  embiid" with the season defaulted to 2027 prints the table with no
  figures and the note "(Shai Gilgeous-Alexander and Joel Embiid have no
  2027 ...)" (the verb's number and the join were fixed 2026-10-09; the
  empty table stands).
- **User sees:** an empty table that reads as an answer.
- **Next step:** refuse, naming the season, when no named player has a
  line (`compose/seasons.py`; a reader's `Result.empty` refusal, as the
  advanced line's and the shot chart's are).
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
  from the question's words (`router.CODE_ASSIGNED_ASKS`, and the parser's
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
  league"), and `team_record` reads the opponent narrowing this entry lists it under
  since 2026-10-14 (#341, closed: tallied from the team's games through
  `TeamNarrowed.narrow_alignment`). Unchanged by this fix: `_conference_refusal`
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
  (`test_a_fabricated_surname_extension_is_discarded_not_the_models_wrong_guess`)
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

### A pre-1994 legend whose surname a current player shares is asked "did you mean", with no word about the era: "how many times did bill russell score below 10 points"
- **Found:** 2026-09-18 (the entry's first shape, "kareem stats vs bob lanier" refused as "no player matching" with nothing about the era, is fixed 2026-10-09: a player's name of two or more words that nothing matches and nothing is near says "ESPN's box scores, which the warehouse's players come from, start with 1993-94, so a player whose career ended before then may not be on record" - `compose.say._say_name_unmatched`).
- **Evidence:** the feed's "how many times did bill russell score below 10 points" answers "No player found matching 'bill russell' - did you mean Bryon Russell, D'Angelo Russell, Russell Westbrook or Walker Russell Jr.?" (the surname back-off, `entities.suggest_players` pass 1), and "kareem stats vs bob lanier" "No player found matching 'Bob Lanier' - did you mean Chaz Lanier?". The suggestion reads as a spelling problem; the real cause is that Russell and Lanier retired before 1994. The back-off cannot tell a legend's surname from a made-up given name on a current player's ("Jemel Embiid", the shape it was built for), so the era clause is not said on it: it would be noise on every typo.
- **User sees:** a question back naming the wrong men, where the honest sentence names the era.
- **Next step:** a table of the pre-1994 names people ask about (the Hall of Fame, say) checked before the surname back-off, so a name on it is refused by the era outright - a declared vocabulary, like `PLAYER_NICKNAMES`; or the back-off's candidates narrowed to those sharing the given name's initial before the clause is said. Measure how many feed questions name a pre-1994 player first (the 26 betting wordings were counted the same way).
- **Source:** ours (the warehouse's floor is ESPN's, `DATA.md` "Coverage floors").
- **Partly fixed (lane p6-round, `7a06b501`, 2026-10-17):** "kareem abdul jabbar" is read as the whole name, not Kareem Rush; the other legends stand.
- **GitHub:** #123
### No future template gets a renderer for free
- **Found:** 2026-09-18, scoping study requested ("richer HTML answers -
  cards, sparklines, tables"); rewritten 2026-10-12 with Phase 4, step 1,
  which keyed the page's renderers by the planned shape.
- **Status:** the page renders by `Answer.shape` - one renderer per
  `reading.Shape` (`static/index.html`, `RENDERERS`: 7, where 22 were keyed
  by the label) - but each renderer still names the BODIES its shape's
  readers say (`bodies`: 28 bodies over 23 draws, each with its `needs`, chosen by `by`, the
  relation where two readers of one `by` differ, and the compiler's
  `skeleton`), because `Answer.data` is each sayer's own dict (`leaders`
  with `display_name`/`value` for one ranking, `player`/`games` for
  another, `teams` with `display` for a third), not the typed body the
  reader returned (`result.Result`: `Grouped`, `Rows`, `Scalar`, `Runs`,
  `Chart`). So a new reader under an existing shape gets nothing for free:
  its data falls back to the sentence until its body is drawn.
- **Evidence:** measured on `phase4-laneB` (the two populations' 2,710 fast
  and refused answers rendered through the page in Chromium,
  `~/association-research/stages/page_renders.py`): 1,881 draw a body, by 23
  draws across the 7 renderers; the 22 retired renderers' code survives as
  those draws (2 merged: a player's quarter game by game and a team's
  quarter total are one draw, `figureGamesBody`; the compiler's three
  skeletons one, `compiledBody`; a chart's frames moved into its renderer).
- **Next step:** carry the typed body on the API beside `data`
  (`ROADMAP-TYPES.md`, Still open 7: "Phase 4's renderers take the body,
  the cells and the cause, and both go") - the `Result`'s parts serialized
  with their `by`/`how`/`kind` - and draw each shape's body generically from
  it: a ranking's rows by its `by` and measure, a split's groups, a
  scalar's values; then delete the per-reader draws one shape at a time,
  each proved by `page_renders.py` before and after. That is an API data
  change (an added field), which step 1 was told not to make.
- **User sees:** nothing wrong today - every reader's body is drawn. The
  gap reaches a reader once a new reader ships with a data dict no body
  reads.
- **Priority note:** P3 (placed beside #69, the page's other structural
  gap) - a design point, not a bug.
- **GitHub:** #110 - heading changed from "`player_netpoints` still renders
  as a `<pre>` block, and no future template gets a renderer for free" to
  this one; the issue's title needs the same update
  (`scripts/sync_issues.py` only opens issues for headings with no
  `- **GitHub:**` line yet, so a rename here does not retitle it).

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

### An apostrophe typed inside a name's word splits the name, and the question is answered without its player
- **Found:** 2026-09-28, fixing #259 - the one research-corpus question the fold moved from a right answer to a wrong one.
- **Evidence:** stubbed offline through the whole agent (main warehouse, names `["jo’sh hart", "philadelphia"]`): before #259, Josh Hart's last 3 games against the 76ers; after, the league read, "No games for every player vs the Philadelphia 76ers in the 2026 regular season" - what "jo'sh hart" with a straight apostrophe answered all along. The entity index's accent fold (`entities._fold`, NFKD to ASCII) drops a U+2019 outright, so "jo’sh" read as "josh"; a straight apostrophe splits the word (`entities._words`), so "jo'sh hart" is the words jo, sh, hart, which no player holds, and its near spellings are three players (Isaiah Hartenstein, Jason Hart, Josh Hart), which ask rather than default. Every name that really carries an apostrophe (32 players, "D'Angelo Russell", "De'Aaron Fox") splits the same way on both sides and matches. <!-- codespell:ignore hart - Josh Hart's surname -->
- **User sees:** a refusal-shaped answer naming the wrong cause ("no games for every player"), the player dropped.
- **Next step:** in the entity index, read a word with an apostrophe inside it (not a possessive at its end) joined as well as split - "jo'sh" as "josh" - the way the accent fold happened to read a typographic one; a case in `tests/query/test_entities.py`.
- **Source:** ours.
- **GitHub:** #271

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

### "top 10 players in offensive netpoints per 50 possessions" answers season totals, and the "50" counts as read
- **Found:** 2026-10-11, the Phase 3 review (`~/association-research/reviews/phase3-2026-10-11/`, finding 3).
- **Evidence:** on `4de1801` with the model's stat `netpoints_offense_per_100`: "... led the league in offensive NetPoints (season total) ..., at 403.9", `Reading.unread` = ["players"]; with `netpoints_offense`: unread = players, per, possessions - the "50" is never unread. "per possessions" and "per 100 possessions" answer per 100. The same on `87cc782`. Step 3's ROADMAP row says a number other than 100 "is left unread, never read as 100"; measured, it is claimed and the rate is dropped to the total - a word whose presence makes the reader read LESS, claimed, the case `span.needed`'s `gives_up` exists for.
- **User sees:** totals where a rate was asked, under a heading that says "season total".
- **Next step:** refuse a rate per N possessions for N != 100 by a cause naming the unit, and add the "50" case to `span.needed`'s `gives_up` checks so the number stays unread until it is.
- **Priority:** P2 - a different figure than asked, with the unit said.
- **GitHub:** #376

### A "compare A to B" whose A is words nothing resolves answers B alone, with no sentence saying a side was dropped: "compare Karl anything town to khris middleton" is Middleton's line
- **Found:** 2026-10-13, Jeff reading lane N's held disagreement for #346 (relayed by the lead).
- **Evidence:** on `8c84100` (sweep-N), the feed's no-names reading of "compare Karl anything town to khris middleton" holds `who.players == ['Khris Middleton']`, unread `['compare', 'karl', 'anything', 'town']`, `misread` empty, `left_out` None, and answers "Khris Middleton averaged 10.2 points, 3.7 rebounds and 2.8 assists per game in 63 games in the 2026 regular season." The one note that says a compared side was dropped, `subject.compared_but_unmatched` (`Reading.left_out`, said as the `name_left_out` decision), takes "vs" and not "compare" by design ("compare Jokic's fingerprint to last season" compares seasons), and is read for a fingerprint alone (`parse._reading_from_route_left_out`, `agent._unmatched_fingerprint`). Measured: of the 2,082 feed questions, 4 hold "compare" and 1 is this class (a compare word, name-shaped unread words on one side of "to"/"and"/"with", one player read); of the 2,710 readings and the 628, no other ("compare khris middleton" compares nothing; "compare curry vs the celtics this season" is a player against a team).
- **User sees:** a comparison asked, one side silently dropped - a narrower answer with nothing saying so.
- **Next step:** read the dropped side on the Reading where a compare word joins two sides by "to"/"and"/"with" and one side is name-shaped words no claim covers (`Reading.left_out` beyond the fingerprint), and say it on every shape the answer side answers (`agent._unmatched_fingerprint`'s note generalized, `say.say_left_out`) - or refuse by a cause naming the words (`reading.CAUSES`, `say.refusal_phrase`). The reading half is lane N's (subject/parse); the saying half is the answer side's (`agent.py`, `compose/say.py`). Reach 1 of the 2,082.
- **Priority:** P2 - a narrower answer than asked, with no caveat.
- **Source:** ours.
- **GitHub:** #423


### A range of seasons reaching under the ranking's floor is answered over what is left, or as "No games ... (1994-1989)": "most pts in the 1980s"
- **Found:** 2026-10-13, the P1 sweep (lane S), fixing #393 (a decade written short).
- **Evidence:** `coverage.check_coverage` and `coverage_refusal` read `Span.season` alone (`coverage.py:286`, `:321`) and return None for a range, so a range of seasons is never checked against a floor. "most pts in the 1980s" and, since #393, "most pts in the 80s" and "How many players averaged 30 ppg in the 80s" (2 of the 2,082 feed questions) answer "No games for every player in the regular season career (1994-1989)." - a range clamped to the ranking floor until it ends before it starts; "most pts from 1985 to 1995" answers "every player, regular season career (1994-1995) ..." with nothing saying the nine seasons before 1994 were left out, where "most points per game 1985" is refused by the floor's own sentence ("A league-wide ranking for 1985 would not be one - ...").
- **User sees:** "No games" for a decade the league played (a wrong cause), or a narrower range answered as the one asked.
- **Next step:** the coverage check reads a range's seasons: refuse by the floor's sentence where the range's last season is under the floor, and say the seasons it could not read (a `floor` note) where only its first is - `coverage.py`, the answer side's (lane T's in the sweep).
- **Priority:** P2 - a refusal naming the wrong cause, or a narrower answer with the cut shown only in the heading.
- **Source:** ours.
- **Also (lane p6-traps, 2026-10-17, the widening inventory):** a span reaching below the box-score floor on a games relation is answered over the covered part, the floor said only in the heading: "highest ppg scored in the 1990s decade" answers "career (1994-1999)", "highest scoring games of supersonics players" 1994-2008, "most wins by player nba 1990s" 1994-1999 (`widening_checks.py`'s floor_crossed).
- **GitHub:** #413

### A line on a percentage is refused rather than read, and a bare or glued percentage line is read by nothing: "kobe bryant career games with 40 field goal percentage" lists his last 10 of 1,331 games
- **Found:** 2026-10-10, Phase 3, step 4, slice (b) - the joined claims (`intent+measure`, 11 on the 2,710) hid it.
- **Evidence:** since the P1 sweep's lane L (2026-10-13) a line stated over or at a number by its lead is read (`lexicon.LINE_OVER_OR_AT`, `EQUALS_LINE`: "more than 40 points", "above 9 rebounds", "exactly 25 field goal attempts", "fga = 25"), and one on a percentage is refused by name (`line_on_a_percentage`): no column of `player_game_log` holds a game's percentage, so "kobe bryant career games with more than 40 field goal percentage" (and 5 more feed wordings) says so by that cause on every key. What is left: (1) the relation could derive a percentage line from the makes and the attempts (`player_games.Narrowed.narrow_measure` writes `pgl.<column>` only); (2) a percentage line stated bare or glued is read by nothing and the whole log answers - "kobe bryant career games with 40 field goal percentage" (last 10 of 1,331), "kobe bryant games with a field goal percentage under40.0%" (3 wordings, the number glued to "under", a decimal), "how many regular-season games did kobe bryant have with a field-goal percentage 40%" (2); (3) a season's line stated over or at a number on the league ("players who made exactly 129 three pointers in the NBA in 2025-26", "nba players with seasons with a 4% 3pt", 3 wordings) is #390's, left unread on the ranking of players' seasons on purpose.
- **User sees:** a refusal naming the percentage for (1); the whole log for (2); the plain ranking for (3).
- **Next step:** a percentage line on the player relation as `100.0 * made / NULLIF(attempted, 0)` beside `narrow_measure`'s column (the relation's file), each stated wording a test; then read a bare "N <percentage words>" after "with" and a decimal or glued number as a line.
- **Since Phase 5, step 1 (2026-10-14):** 4 of its wordings ("kobe bryant career games with 40 field goal percentage", "players who made exactly 129 three pointers in the NBA in 2025-26", ...) refuse by name now (`unread_number`); a refusal by name is the standing floor, the fix is the read.
- **Priority:** P2 - (2) answers a broader question on 6 of the 2,082; (1) and (3) are refusals or #390's.
- **GitHub:** #371

The 391 date-only games printed a day early (#76), 2008's team rebound columns
(#74) and the swapped 1990 Finals Game 5 were fixed on 2026-09-16. Before adding
to this section, re-read the P2s against the P1 definition: that is how both of
those were found.

### "without threes" is read as the player Tree Rollins: "most ppg without threes" refuses as games without a teammate
- **Found:** 2026-10-14, the Phase 5 review (the Phase 4 review's #404 class, "without free throws", seen on "threes").
- **Evidence:** the absence reader ("without X") takes "threes" as a near spelling of Tree Rollins and the question asks for games without that teammate; "without threes"/"without free throws"/"excluding threes" name a stat the figure should leave out (points minus 3-pointers), which nothing reads.
- **User sees:** a refusal about a teammate nobody named.
- **Next step:** an absence whose words name a stat (`measure.named` over the span after "without") is no teammate - refuse by name as a figure nothing computes (`fact_not_read`), beside #404; the read (points less a component) is a later shape.
- **Trialed:** 2026-10-17, lane p6-homographs (applied and reverted): a companion's name that is a stat's own words (`lexicon.STAT_WORDS`, whole) read as no name in `subject._companion_names_segment` turns the refusal into fluent wrong answers - "most ppg without threes" answers "Kon Knueppel led the league in 3-pointers made in the 2026 regular season, at 273" and "most ppg excluding free throws" the free-throws-made ranking, since the measure tagger then reads the stat. So the subject side waits on the cause: a fact `lexicon.ABSENT_FACTS` names - `("a figure less one of its parts", re.compile(r"\b(?:without|excluding|minus|not\s+counting|less)\s+(?:free[\s-]+throws?|fts?|threes|3s|3[\s-]*pointers?|three[\s-]*pointers?|dunks|layups)\b", re.IGNORECASE))`, the `Literal` widened to it - refused as `fact_not_read` before the subject's companion is read (the lexicon lane's file); then the companion segment skips the stat's words, and a test per wording. Held by question in `~/association-research/stages/homographs.py` (three: the two "most ppg" wordings and "Aaron holiday most 2pt fieldgoals without threes").
- **Priority:** P2 - a refusal naming the wrong cause.
- **Source:** ours.
- **GitHub:** #449

### A postseason NetPoints answer prints the whole season's play-type split under a postseason label
- **Found:** 2026-09-30, the notes inventory for `ROADMAP-TYPES.md` (an Opus agent reading `011091f`; reported, not re-verified).
- **Evidence:** the season fingerprint table has no season type (`compose/netpoints.py`, `templates/netpoints.py:98-102` when found; `fingerprint.py:457,979-980`); its sayer (`compose.say`'s NetPoints phrases; `templates/netpoints.py:113,331` when found) labels the answer "{season} postseason" and `fingerprint.py:853` captions the plot "{season} season". Nothing says the split is the whole season's.
- **User sees:** a play-type split labeled as the playoffs' that is not.
- **Next step:** confirm against a playoff NetPoints question; say the split covers the whole season, or refuse the split for a postseason.
- **GitHub:** #299
- **Restored at Phase 5's close (2026-10-14):** the entry had been lost in a merge while GitHub #299 stayed open (the Phase 5 review).

### Reader cases that pin a reading no answer should rest on: contradicting lines, a dropped second line, a stat word read as another
- **Found:** 2026-10-15, lane p6-tests, renaming every case in `tests/query/reader_cases.json` (the index is the case's position in the file).
- **Evidence:** [180] and [181] "games with less than 20 points" / "fewer than 5 turnovers" carry contradicting lines, `points < 20` beside a keyed `points >= 20`; [345] "sixers record when maxey had 20+ points and 5+ assists" holds only the points line - "5+ assists" is neither a line nor unread and not held in `tests/grammar_drops.py`, a silent narrowing; [37] "compare sga and embiid on rebounding" reads points and "rebounding" is not unread; [536] "james harden stats with no paul george" reads George as having played and "no" is unread; [272] and [71] carry `who.kind=everyone` with players `['wemby']` and `['ever']`; [328] and [331] carry `who.teams=['Magic Johnson']`; [137] and [138] "curry playoff stats 2015-18" with no model names become a league ranking with no `defaulted` cause; [529] against [81]: "since he joined the Warriors" flips between the Warriors' line and Curry's tenure by whether the model gives a name; [439] against [440]: "games has embiid won" is a record, "... lost" is `unknown_stat`.
- **User sees:** on [345], [37], [536] a fluent answer to a narrower or different question (a record on one of two lines; points where rebounding was asked; games George played where his absence was asked); on the rest a reading that would answer wrong if the shape were reached, or an inconsistent refusal.
- **Next step:** reproduce each on the warehouse through `Agent.ask` and re-rank: [345], [37] and [536] are P1 if the answer is as the reading says; the contradicting lines on [180]/[181] need the keyed line and the narrowing line to be one; the everyone-with-players and teams-of-a-player cases are the subject reading's (Phase 6, step 4).
- **Priority:** P2 until reproduced through the answering loop; three of them are likely P1.
- **Source:** ours.
- **GitHub:** #458

### A plus-minus read before 2008-09 reads ESPN's zeros as figures
- **Found:** 2026-10-15, the feed sample's keyer (shard B), re-measured by the lead.
- **Evidence:** `player_box_stats.plusMinus` is 0, never NULL, on every played row before 2009 (DATA.md, "`player_box_stats.plusMinus` is 0, never NULL, on every played row before 2008-09": 26,502 played rows in 2008, none non-zero; 24,544 of 26,235 in 2009). A career plus-minus, a per-game plus-minus over a span reaching before 2009, or a ranking by plus-minus in a season before 2009 sums or ranks zeros.
- **User sees:** a fluent figure that is partly or wholly zeros ("LeBron's career plus-minus" short by his first six seasons; a 2008 plus-minus ranking of all zeros) with no caveat.
- **Next step:** a coverage floor for plus-minus at 2009 (`nba/coverage.py`, the measure's table resolved per question) so a read reaching below it is refused or caveated as the empty box scores are (`_empty_box_scores`); a test per reader that reads the column.
- **Priority:** P2 - a figure quietly wrong where the span reaches before 2009.
- **Source:** ESPN.
- **GitHub:** #462

### A double-double count reaching before 1994 reads ESPN's zeros
- **Found:** 2026-10-15, the feed sample's keyer (shard A), re-measured by the lead.
- **Evidence:** `player_season_stats.doubleDouble` is 0, not NULL, on every season line before 1993-94 (DATA.md): the regular-season sum is 0 for 1990-1993 against 2,474 in 1994. A career double-double count or a per-season history over a span reaching before 1994 (Karl Malone, Charles Barkley, Hakeem Olajuwon) is short every early season, and a ranking over those seasons is all zeros.
- **User sees:** a fluent count that is too low, or a ranking of zeros, with no caveat.
- **Next step:** a coverage floor for the double-double and triple-double columns at 1994 on the season table (`nba/coverage.py`), refused or caveated as the empty box scores are; check `tripleDouble` the same way.
- **Priority:** P2 - a figure quietly wrong where the span reaches before 1994.
- **Source:** ESPN.
- **GitHub:** #465

### A position group reads one point-in-time label
- **Found:** 2026-10-15, the feed sample's keyer (shard A), re-measured by the lead.
- **Evidence:** every player carries one position across all his seasons, the bio's current label (DATA.md, "A player's bio is point-in-time"): Shawn Marion "SG", Kobe Bryant "SF", LeBron James "F", Jordan and Wade a generic "G". "most career rebounds by a small forward" answers Paul Pierce (7,527) with LeBron (12,095) outside the group; "point guards under 8,000 career points" sees 237 "PG" players and not the 862 "G".
- **User sees:** a fluent ranking of the wrong group, with the group named as if it were exact.
- **Next step:** the position group's read (`Query.position`, `point._everyone_point`) folds the generic labels into their groups (G into PG and SG, F into SF and PF) and says the label is the current roster's, not the season's; or refuses a position group over past seasons by name.
- **Priority:** P2 - a fluent ranking of a group the data cannot form.
- **Source:** ESPN.
- **GitHub:** #466

### A game score over empty box scores reads 0.0
- **Found:** 2026-10-15, the feed sample's keyer (shard A), re-measured by the lead.
- **Evidence:** 762 rows of `player_season_advanced_stats` carry `avg_game_score = 0.0` with `ts_pct` NULL (DATA.md): the Chicago and New Orleans seasons 2013-2018, built from the empty box scores. DeMarcus Cousins 2018 reads 0.0 over 48 games played. A game-score ranking lists them at the bottom; a player's own game score answers 0.0.
- **User sees:** a 0.0 said as a figure.
- **Next step:** the build writes NULL where no box score was read (`fetch/warehouse.py`'s advanced table), and the ranking's qualifier excludes NULL; backfill by `association data load --tables player_season_advanced_stats`.
- **Priority:** P2 - a fluent zero.
- **Source:** ours (the build).
- **GitHub:** #467

## P3: refusal or gap

### The period floor refuses over a column the words did not ask: "Giannis first half points rebounds assists gamelog vs washington" is refused for field goal attempts
- **Found:** 2026-10-23, lane p6-periods (keep-and-bound, #525/#526)
- **Evidence:** a list of three stats is read as the whole line, so the floor (more than one game in ten missing) reads every column: 5 of 37 games miss on FGA.
- **User sees:** a refusal naming a stat he did not ask about.
- **Next step:** the floor reads the named stats when the words name several (or the reader reads a list of stats) - Jeff's call which.
- **Priority:** P3.
- **Source:** ours.
- **GitHub:** #532

### Team period reads are still trusted per season
- **Found:** 2026-10-23, lane p6-periods (keep-and-bound, #525/#526)
- **Evidence:** `team_games.TEAM_PERIOD_AGREEMENT` and `TEAM_PERIOD_REFUSE_BELOW` remain; no per-game gap exists for a team line, so 2016 team columns other than points are refused whole.
- **User sees:** a refusal for a team's 2016 quarter rebounds or assists.
- **Next step:** a team-game gap table beside `plays_complete`, and the keep-and-bound rule.
- **Priority:** P3.
- **Source:** ours.
- **GitHub:** #533

### A career offered to a one-season player returns the same figures
- **Found:** 2026-10-23, lane p6-alts (D23's pilot)
- **Evidence:** 37 of 631 offered alternatives answer as the original (28 of them VJ Edgecombe, "over his career (2026 regular season)").
- **User sees:** a button that changes nothing.
- **Next step:** a relation-side first-season check before a career is offered.
- **Priority:** P3.
- **Source:** ours.
- **GitHub:** #534

### "against in 2026" reads "in" as the Indiana Pacers
- **Found:** 2026-10-23, lane p6-alts (D23's pilot)
- **Evidence:** "How many teams has Luka recorded a triple double against in 2026" takes opponent = Indiana; the offered-wording guard caught it, so no button offers it.
- **User sees:** a narrowing to Indiana the words did not ask (on the typed question).
- **Next step:** the opponent reader takes no lone "in" before a year.
- **Priority:** P3.
- **Source:** ours.
- **GitHub:** #535

### A career comparison and a career fingerprint comparison are declined
- **Found:** 2026-10-23, lane p6-alts (D23's pilot)
- **Evidence:** the planner's `cannot_honor` career cell blocks F137's, F013's and FS255's other reading.
- **User sees:** no career alternative where a season comparison was answered.
- **Next step:** a career comparison reader over the season line's career rows.
- **Priority:** P3.
- **Source:** ours.
- **GitHub:** #536

### A round with no season named reads the current season, where the words imply the career: "jimmy butler nba finals stats"
- **Found:** 2026-10-17, lane p6-round (the playoff round), FS189 against its key.
- **Evidence:** FS189's key reads Butler's two Finals (2020, 2023: 11 games, 24.1 points); the answer is "No 2026 postseason games found for Jimmy Butler III ... ask for his career". The same on "lebron james win loss record in the finals", "lebron james 4th quarter in all finals", "Caleb martin ecf", "kyrie in nba finals game 7". And the league's "most blocks / steals / free throws / three-pointers in a game 7" is refused: honoring a series game there is one clause, but D13's guard keeps a game of a series off the career "most X in a game" reads, so it would rank 2026's Game 7s alone.
- **User sees:** a refusal pointing at the career wording, where the career was what was asked.
- **Next step:** Jeff's call under D13 - whether a named round (and a game of a series) implies the career (`span.IMPLIED_CAREERS`); then the game-7 clause.
- **Priority:** P3 pending Jeff's call.
- **Source:** ours.
- **GitHub:** #519

### When the recorded reply names nobody, a name only the words hold is not read: "dirk 2011 finals stats" is refused "No player or team is read"
- **Found:** 2026-10-17, lane p6-round (the playoff round), reading the round's refusals.
- **Evidence:** "dirk 2011 finals stats", "shaquille o'neal 2004 finals stats" and Duncan, Brunson, Leonard, Gasol and Fox wordings (FS059, FS182, FS188, FS348 among them) are refused for no subject where the recorded normalizer reply named no player; with the name supplied each answers its key. The before tree does the same on "dirk 2011 playoff stats", so it is not the round's.
- **User sees:** a refusal on a question naming a player plainly.
- **Next step:** measure whether the subject reading should read a unique whole-name match from the words when the model names none (`subject.players_named_in`), against the invented-name rules in AGENTS.md ("Names").
- **Priority:** P3.
- **Source:** ours (the model's reply, read past).
- **GitHub:** #520

### The round's remainders: the rounds around one, the meetings tally, a team ranking and a quarter within a round
- **Found:** 2026-10-17, lane p6-round (the playoff round), closing #10.
- **Evidence:** "before / until / vs the finals" stay the `playoff_round` cause; the two-team meetings tally refuses a round ("knicks record vs spurs in the 2026 finals"); a team ranking refuses it; the period readers step aside for a round, so "Lebron total 4th quarter stats 4th quarter nba finals vs miami" is the compiler's log of six games, no total (`result.RoundOf`, `PERIOD_ROUND`). "nba finals game log 2025" (the yardstick's F165) is now declined as having no player subject, where the Finals' games are a team log of the two teams.
- **User sees:** a refusal, or a log where a total was asked.
- **Next step:** each reader honors the `round` cell through its relation's shared step; the league's Finals log reads the two teams.
- **Priority:** P3.
- **Source:** ours.
- **GitHub:** #521

### The warehouse holds no trade dates, so a teammate's stint ends at his next team's first game: "kyrie second half game log without luka" counts 33 of the key's 30
- **Found:** 2026-10-17, lane p6-traps, fixing #480 (a stint now runs to the day before his first row for his next team).
- **Evidence:** FS354 answers 33 games at 14.4 second-half points, the key 30 at 14.4: the Mavericks' games of 2025-02-02 to 02-08 fall after the trade (2025-02-01) and before Doncic's first Lakers game. Grimes with Luka is off by 1-2 the same way. `information_schema` holds no transactions table.
- **User sees:** a "without" count a few games high around a trade.
- **Next step:** Phase 8: read ESPN's transactions and end a stint at the trade date (`conditions.stints`).
- **Priority:** P3 - a published fact not pulled; the error is a few games.
- **Source:** the pull (ESPN publishes transactions).
- **GitHub:** #510

### A waived player's stint runs to his team's last game of the season
- **Found:** 2026-10-17, lane p6-traps, writing `conditions.stints`.
- **Evidence:** a stint ends at the earlier of his next team's first game and his team's last game of the season of his last row; a player waived who never appears for another team keeps the stint to season's end, so games after the waiver count as "without" him. It is the price of counting a season-ending injury as absence.
- **User sees:** a "without" split counting games after a release.
- **Next step:** measure on the feed's "without" wordings how often each rule is wrong; transactions (above) settle both.
- **Priority:** P3.
- **Source:** ours.
- **GitHub:** #511

### The highs reader reads no offensive or defensive rebounds, nor a high under a yes/no predicate
- **Found:** 2026-10-17, lane p6-sentence (D17), measuring what `compose/sentence.py` still answers.
- **Evidence:** `THRESHOLD_STAT_COLUMNS` holds no rebound side; those highs and a high under a predicate (F124's triple-doubles) fall to the compiler's rows: 33 feed answers on `rows/measure`. The rows are right; the sentence is the compiler's table, not the high's.
- **User sees:** a table where a sentence naming the record holder is said elsewhere.
- **Next step:** the highs reader takes the rebound sides and a predicate's games.
- **Priority:** P3.
- **Source:** ours.
- **GitHub:** #512

### A career ranking by position reads the box scores only, so a pre-1994 career is short: "most career rebounds by a small forward" leaves out Pippen
- **Found:** 2026-10-17, lane p6-sentence (D17), FS026.
- **Evidence:** the answer is Pierce 7,520, Bryant 7,026, Gay 6,283 (the key 7,527, 7,047, 6,283); Scottie Pippen's 7,494 is missing because most of it predates the box scores. The season line (`player_season_stats`, from 1977) holds it, but a ranking by position cannot use it.
- **User sees:** a career ranking missing a player whose career began before 1994, with the 1994 floor in the heading.
- **Next step:** a ranking by position over a career reads the season line where it holds the position; otherwise say the floor in the sentence.
- **Priority:** P3.
- **Source:** ours.
- **GitHub:** #513

### "shooting percentages" names no measure: "nba current best shooting percentages" is refused for a ranking stat
- **Found:** 2026-10-17, lane p6-causes, the sample's FS072.
- **Evidence:** the plural "shooting percentages" (and "best shooting percentage") matches no catalog alias; the key ranks FG%, 3P% and FT% together.
- **User sees:** `ranking_needs_stat` where three columns were asked.
- **Next step:** an alias reading the three splits as one line, or a question back naming the three.
- **Priority:** P3.
- **Source:** ours.
- **GitHub:** #494

### A misspelled stat word is unread: "most 30+ pointd regular season games" is refused for a ranking stat
- **Found:** 2026-10-17, lane p6-causes, the sample's FS395 ("most 10/20/30/40+ pointd ...").
- **Evidence:** "pointd" matches no stat word; the line's number stands with no stat and the ranking refuses `ranking_needs_stat`.
- **User sees:** a refusal with a false cause.
- **Next step:** a near spelling of a stat word after a line's number is read as the word and said, as "quater" is (#457's fix).
- **Priority:** P3.
- **Source:** ours.
- **GitHub:** #495

### The coverage floor is not said under a reading's own refusal: "Knicks fgm 1988-89" names the reading's cause, not the floor
- **Found:** 2026-10-17, lane p6-causes, the sample's FS174, FS245 and FS380 (Magic Johnson's Finals before 1994).
- **Evidence:** the floor is checked on the planned point's scope; a reading that refuses before any point (a round, an unread fact) has no point and so no floor, and its cause is true but not the deepest - no data for 1989 would answer first.
- **User sees:** a refusal that sends the user to rephrase a question no data could answer.
- **Next step:** check the floor of the cause's shape over the span before saying the reading's refusal - a change to AGENTS.md's rule that a refusal the reading comes to has no floor; Jeff's view first.
- **Priority:** P3.
- **Source:** ours.
- **GitHub:** #496

### The first game a yes/no stat holds is not read: "westbrook first triple double in one game" is refused for an unknown stat
- **Found:** 2026-10-17, lane p6-causes, the sample's FS146.
- **Evidence:** "first <yes/no stat>" asks the earliest game the predicate holds; no window reads "first" over a yes/no stat.
- **User sees:** `unknown_stat`.
- **Next step:** "first <yes/no stat>" is a log over the stat in ascending date order with a limit of one.
- **Priority:** P3.
- **Source:** ours.
- **GitHub:** #497

### A minimum within one season's reach keeps the season default unsaid: "worst 3pt% with atleast 1000 3pt attempted" answers "No players qualified in 2026"
- **Found:** 2026-10-17, lane p6-causes, the sample's FS310.
- **Evidence:** D13's minimum rule reads the career only above 85 games; 1,000 attempts is within a season's reach, so the season default stands and the refusal says no qualifier without the career wording.
- **User sees:** a true refusal with no way to the career answer (Charles Barkley, 26.6%).
- **Next step:** say the season default with the career wording, as the D13 decision sentence does.
- **Priority:** P3.
- **Source:** ours.
- **GitHub:** #498

### A career and a named season asked at once are refused: FS117 names both
- **Found:** 2026-10-17, lane p6-causes, the sample's FS117.
- **Evidence:** the span reader refuses both ends named at once; the key answers either reading.
- **User sees:** a refusal.
- **Next step:** Jeff's span policy: the named season as the default with the career wording, or a question back.
- **Priority:** P3.
- **Source:** ours.
- **GitHub:** #499

### A model's name holding a request verb names nobody: "trace jokic's mark from his best season" asks "did you mean Nikola Jokic?"
- **Found:** 2026-10-17, lane p6-homographs (`~/association-research/stages/homographs.py`, the corpus's one GRAMMAR hit).
- **Evidence:** the model copied "trace jokic" whole; both its words anchor in the question, so the respelling reads the two-word window, which names nobody (`subject.question_derived_player` never falls back to one word between two anchors - the "kareem stats vs bob lanier" rule), and the name stands as typed. "trace" is an imperative verb no lexicon list holds (`CONTENT_STOPWORDS` holds "show", "give", "list", "display", but no list is read as a request verb by the subject reading), so nothing tells the verb from a given name; "bob" in "bob lanier" is a dictionary word too, so the dictionary cannot. 1 of the 628 (a hold-out wording), 0 of the feed.
- **User sees:** "No player found matching 'trace jokic' - did you mean Nikola Jokic?", a question back where Jokic was named.
- **Next step:** a lexicon set of the request verbs a question opens with ("trace", "show", "list", "display", "give", "plot", "chart", ...; the lexicon lane's file), and the subject reading drops a model name's leading word in it where the rest of the name stands as one player; a test. Held by question in the homograph harness until then.
- **Priority:** P3 - a question back where one player was named.
- **Source:** ours.
- **GitHub:** #489

### A team's points off turnovers is on file and read by nothing: "toronto raptors points off turnovers" is refused
- **Found:** 2026-10-16, lane p6-words, fixing #456 (points scored one way read as their last word).
- **Evidence:** `team_box_stats.turnoverPoints` is filled for every team-game 1995-2026 (2026: 2,632 rows, none NULL, mean 17.9); `team_season_stats.turnoverPoints` only through the 2021 regular season (0 for every team from the 2021 playoffs on). No team metric reads either (`team_metrics.TEAM_METRICS`, `compose.team.TEAM_GAME_FIGURES`), so the phrase is refused by `fact_not_read` (`lexicon.PLAY_TYPE_POINTS`). A season metric over `team_season_stats` would answer 0 for 2022-2026; the read needs the box rows summed per season. Second-chance points are on neither table.
- **User sees:** a refusal saying the figure is not read, for a figure the warehouse holds; 1 of the 2,070 feed questions.
- **Next step:** a `points_off_turnovers` team metric whose season figure sums the box rows (the narrowed path's `TEAM_GAME_FIGURES` entry `{b}.turnoverPoints`, the unnarrowed one off `team_box_stats` where the season table is 0), its first season 1995; then `PLAY_TYPE_POINTS` names it.
- **Priority:** P3 - a refusal of a figure on file.
- **Source:** ours (the season table's gap is ESPN's: worth a `DATA.md` line when this is read).
- **GitHub:** #487
### A companion's line stated by "shoots" is not read: "knicks record in playoff games when Mitchell Robinson shoots 4 ft" is refused for an unread number
- **Found:** 2026-10-16, lane p6-subject (#340's fix; the feed sample's FS045).
- **Evidence:** `lexicon.CONDITION_HAS_LINE` reads a companion's line after "has", "had", "gets", "records", "posts" and a box-score word; "shoots" is none, so "shoots 4 ft" reads no line and the reading refuses "4" as an unread number. One feed wording (FS045).
- **User sees:** a refusal naming the number, where the Knicks' playoff record in Robinson's 4+ FT games was asked.
- **Next step:** a lexicon pattern for a companion's shooting line (the lexicon lane's file) - a verb (`shoots|shot|takes|took|attempts|attempted` for attempts, `makes|made|hits|hit` for makes), the number with an optional plus, and a shot word (`ft|fts|free throws?|fg|fgs|field goals?|threes|3s|3 pointers|three pointers`) - with the column by verb and shot word (`freeThrowsAttempted`/`freeThrowsMade`, ...: the feed sample's key for FS045 reads "shoots 4 ft" as 4+ free-throw ATTEMPTS, 16-5 in 21; `MEASURE_WORDS` holds no bare "ft"); then a branch in `subject._condition_role` beside `CONDITION_HAS_LINE`'s (the subject lane's file), and the team's record over the line reading an attempts column (`measures.THRESHOLD_STAT_NAMES` holds `freeThrowsMade` and no attempts column: check `lines.measure_filters` takes it). A test per verb.
- **Priority:** P3 - a refusal where an answer exists.
- **Source:** ours.
- **GitHub:** #485

### A teammate's surname that is one player's exactly is read as him, though a near spelling was the subject's teammate: "rui hachimura stats without reeves" reads Antonio Reeves
- **Found:** 2026-10-16, lane p6-span's prototype of D15, re-measured by lane p6-d15.
- **Evidence:** "reeves" is Antonio Reeves's surname exactly, so the teammate's resolution (`compose.presence._with_without_resolve`, `player_relation._resolved_teammate`, through `entities.resolve_player`) takes him and never reaches the near-spelling pass that would find Austin Reaves, Hachimura's Lakers teammate - the near spelling is read only for a span that matches no player exactly (`entities.read_near_spelling`, AGENTS.md "Typos are the entity index's job"). The answer says "Rui Hachimura and Antonio Reeves were never on the same team together" (the feed's one wording). Under D15 a teammate who never shared the subject's team keeps that refusal (lane p6-d15's guard), so it is a refusal naming the player read, not a fluent split over him.
- **User sees:** a refusal naming Antonio Reeves where Austin Reaves was meant; "without austin reaves" answers.
- **Next step:** the teammate's resolution eliminates a candidate who never shared the subject's team before choosing (the move `_resolved_teammate` already makes between namesakes, `_teammates_among`), and then reads a single near spelling who did - said as a name reading; check both conditions of the default rule (visible, correctable) first.
- **Priority:** P3 - a refusal that names the player read.
- **Source:** ours.
- **GitHub:** #481

### A dated "since" beside a conference is read as a single season and a career at once: "celtics all-time record vs eastern conference including playoffs since september 11th 2001"
- **Found:** 2026-10-14, the P1 sweep's lane T3, fixing #341 (the record against a conference).
- **Evidence:** on `48a0c45` the cuts tagger keeps the alignment as the situation ("vs eastern conference") and lets the worded since-date go (`tests/query/test_cuts.py`, `test_a_worded_since_beside_another_circumstance_keeps_that_one`: a `Situation` holds one circumstance), and the span then reads the date's year as the season with "all-time" as a career: `span` `{career: true, season: 2001, both: true}`. The record refuses it "a career span and a single season at once" (it said "no calendar narrowing ... a conference or a division is not" until #341 closed). 1 of the 2,082 feed questions; none of the 628.
- **User sees:** a refusal naming a contradiction the user did not write; the record since that day against the East is answerable (both narrowings are the team relation's).
- **Next step:** the span tagger reads a worded since-date the cuts tagger set aside as `since` (the season its day falls in) rather than as a season, or the situation carries both circumstances (lane S's files: `cuts.py`, `span.py`).
- **Source:** ours.
- **Priority:** P3 - a refusal naming the wrong cause.
- **GitHub:** #434

### "most ppg without free throws" is refused as if "free throws" were a teammate: "games without free throws cannot be read here"
- **Found:** 2026-10-13, the Phase 4 review (`~/association-research/reviews/phase4-2026-10-13/REVIEW.md`, finding 3).
- **Evidence:** `fe806a6` feed run, 2 questions ("most ppg without free throws", "most ppg excluding free throws"), the planner's decline.
- **User sees:** a refusal naming the wrong thing (a player's absence).
- **Next step:** "without|excluding <stat words>" is no companion; refuse "points excluding free throws" by a cause naming the derived stat, or read it as points minus free throws made.
- **Source:** ours.
- **Priority:** P3 - a refusal naming the wrong cause.
- **GitHub:** #404

### A team total named by a metric's alias stays the per-game line: "how many fgm did the knicks make this season" answers field goals per game
- **Found:** 2026-10-10, Phase 3 step 2's measure slice, holding the team compiler's total identical
- **Evidence:** `point._team_measure` reads a team's game-level or season-total measure from the words (`lexicon.TEAM_WORD_MEASURES`) and then from the measure's key - but a measure written as a team metric's ALIAS text ("fgm", "ppg", "field goals", "scoring") never matched a column name before the slice (the alias text was the slot's value), so the team compiler's total never read it and the team-season reader answered the per-game line; the catalog would read "fgm" as `fieldGoalsMade` and the total, which moves 12 of the 2,710 readings (`team_stat` with an alias-spelled stat: "pippen ppg, rpg apg for the bulls", "most fgm + opp fgm in a game by Knicks; 1988-89", "How many 10+ point leads did the Sacramento Kings have last season", ...) - some to a better answer ("how many fgm" as a total), most to a worse one ("ppg" summed). `measure.named_by_a_team_metric` keeps the slot-era reading on purpose, and `tests/query/test_measure.py` pins it.
- **User sees:** a per-game figure where a season total was asked, with a "how many ... made" wording the total reader exists for.
- **Next step:** the reading should turn on `Measure.how`, not the spelling: a team's measure with `how="total"` (the words' "how many ... made"/"total") is the total, per game otherwise - which also reads "pippen ppg" as the per-game line it is. Enumerate the 12 first.
- **Priority:** P3 - a wrong per-game answer where a total was asked, on a wording the alias happens to catch; 0 of the 12 recorded readings is a clear case.
- **GitHub:** #362

### An overtime period named in the words is refused as a situation, never read as a period
- **Found:** 2026-10-09, Phase 3, step 2, the period (the family measured on all 2,710 readings; the cuts slice counted these 4 among the 33 unread situations).
- **Evidence:** `period.which_period` reads a quarter (1-4) or a half (1-2) and nothing else, though `reading.Period(number=5)` and the relations' `narrow_periods` take an overtime period by number (`period_label(5)` is "overtime", the team relation's `test_an_overtime_no_game_reached_is_null_not_zero`). "overtime" is `lexicon.SITUATION`'s word, so the 4 of the 2,710 readings that name one - "most points in an overtime game by a team nba", "most points in an overtime game nba", "most quadruple overtime points in a game by a team nba", "most triple overtime + quadruple overtime points in a game by a team nba", all `period_leaderboard` by the grammar's `overtime` word, all the feed's - carry `situation="overtime"` and are refused by the planner ("period_leaderboard cannot honor ['situation'] ..."); "OT"/"in ot" is read by nothing (0 of the 2,710 name it). Reading "overtime" as `Period(number=5)` would move those 4 answers from a refusal to a ranking by the overtime period's line, and "an overtime game" asks for the WHOLE game's points in games that went to overtime, not the period's - a different question still, which no cut expresses.
- **User sees:** a refusal naming the situation, where the first two ask a question the warehouse could answer (the plays carry every period).
- **Next step:** two shapes to tell apart from the words: an overtime PERIOD's figures ("points in overtime", "in ot", "3rd overtime") as `Period(number=5+)`, which the relations already apply; and a game that went to overtime as a games' cut (the draft's `Calendar`-like circumstance, over `games`' period count). Each a decline-to-Cause commit with the 4 feed answers enumerated.
- **Priority:** P3 - a refusal where an answer exists; 4 feed readings, none of the 628.
- **Source:** ours.
- **GitHub:** #355

### A condition read's covered scope reads a range's first season and not its last: a split, a run or a matchup "from 2019-20 to 2023-24" with nothing to show is refused "since 2019"
- **Found:** 2026-10-09, Phase 3, step 2 (the span typed): `player_relation.condition_scope` takes the typed `Span` now and its docstring says so - the retired bodies it serves (`compose.splits`, `compose.runs` through `core.run_scope`, `compose.pairs`) passed `since` alone, never `until`, and still do.
- **Evidence:** `src/association/query/player_relation.py` `condition_scope`: `_Scope(None, kind, max(since, scope.first), scope.phantoms)` with no last season; `compose/splits.py:193`, `compose/pairs.py:56-57`, `compose/core.py` `run_scope`. The games themselves are read through `span_of` (`scoped_player`), which bounds both ends, so the rows are right; only the `_Scope` the "no games" refusal (`player_relation.no_games`: "in any regular season on record (2019 onward)"), its label and the empty-box-score count are built from is open at the far end. On the 2,710 readings 4 carry a closed range on one of these shapes (2 `player_splits`, 2 `player_matchup`, 0 `streak`), none of them a "no games" case.
- **User sees:** a refusal or a caveat naming "since 2019" where "2019-20 to 2023-24" was asked, on a split, run or matchup with no games in the range - a wrong-cause sentence, not a wrong answer, and rare.
- **Next step:** `_Scope` gains a `last`, `condition_scope` sets it from `span.until`, `where_in`/`label` say "from X through Y" as `ResolvedSpan.during` does, and the three readers' counts bound by it; one unit test per reader with a closed range and no games.
- **GitHub:** #349

### A team's log drops a calendar, quarter or half narrowing silently
- **Found:** 2026-10-05, porting the team log onto the team compiler (Phase 2, step 4)
- **Evidence:** `compose/logs.py` `read_team_log` narrows the team's games with `Scope(venue=...)` and the opponent/date alone (`team_games`), while its shape's row (`team_relation.TEAM_RELATION_SCOPING_EXCLUDED[PointShape("team_games", "rows", "date")]`, `STATED_SCOPING["game_log"]` until Phase 3, step 2's closing slice) states `situation` - so a team log asked "in January" lists the whole span under a heading that does not say so. It was the retired template's behavior too. Measured 2026-10-10 (`~/association-research/stages/cells_family.py`): 0 of the 61 team logs among the 2,710 answered questions carry a situation, a quarter, a teammate or an ordinal season; by construction (`--matrix`, each cell set on the 49 team-log points), a quarter or half is stepped aside for and declined ("a team's log or splits narrowed beyond their own words has no reader" - the row does not state it, so that half of this entry no longer holds), while a situation, a teammate who PLAYED and an ordinal season reach the reader and are not read (the row takes a teammate, a line and an ordinal season beyond the team relation for the reader to refuse in its own words; `_team_log_refusals` refuses an absence, a line and a series game only).
- **User sees:** a whole-season team log for "knicks games in january" with no mention of January (a narrower question answered broader - the failure shape at the top of AGENTS.md), if any such question reaches the log.
- **Next step:** narrow through `team_games` with the scope's situation (and say it in the heading), and refuse a played teammate and an ordinal season in `_team_log_refusals` beside the absence - or take `situation` out of the team log's row so the planner's check steps aside for it; with a wording that reaches each, a test, and every moved answer named.
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
- **Feed numbers:** the feed's unread numbers, read one by one in the Phase 3 review's cleanup (b)5 (2026-10-12, `claims_ledger.py run --feed` on `4c595b8`, answers from `stage_snapshots.py run --feed`): 3 - "best nba shooting percentages from farther than 30 feet" (refused as a team metric), "best shooting percentage from beyond 30 feet in the last 5 years" (the points ranking), "tyrese haliburton shot chart from 30+ feet" (the whole chart's refusal).
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

### Awards and All-Star selections are refused by name; whether ESPN serves an honors collection anywhere is unprobed
- **Found:** 2026-09-18, the algebra spike's attack pass over the large
  StatMuse set (`~/association-research/algebra-spike/stage1/attack_report.md`)
- **Evidence:** no table here holds an MVP, an All-Star, All-NBA or
  All-Defensive selection or a vote share (DATA.md, "No award, All-Star or
  All-NBA selection anywhere"). Since the P1 sweep's lane L (2026-10-13) the
  words are refused by name before any reader runs (`lexicon.ABSENT_FACTS`'
  `award`, the `fact_not_read` cause): "nba mvps in 2010's", "1999 sixth man
  of the year", "most all star appearances for a nets player" and 19 other
  feed questions answered a points ranking, a season line or a log until
  then. What is left is the source: nobody has checked whether ESPN's core
  API serves an honors collection the pull does not read.
- **User sees:** "No award, All-Star or All-NBA selection is on file here,
  so 'mvps' cannot be read. ..." - a refusal naming the missing fact.
- **Next step:** probe ESPN's core API for an honors or awards collection,
  with the live check the coach entry used (DATA.md, "ESPN publishes
  coaches"), before anyone writes "ESPN does not publish awards"; if it
  serves one, a table and a reader would answer these 22 feed questions.
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

### A team's splits count blank box-score columns from field-goal attempts alone
- **Found:** 2026-10-01, closing ROADMAP Phase 0 (a kind on every remark); the splits agent, reading `templates/splits.py:609-613` (the count is `compose/team.py`'s `blank` column now, said by `compose.say`); reported, not measured.
- **Evidence:** the count behind "Rebounds, assists, 3-pointers and FG% are missing from N of those games' box scores" tests only `fieldGoalsAttempted IS NULL`. That holds for the wholly empty 2013-18 team rows; but `record_when`'s team branch shows a column can be blank alone (2018 `totalTurnovers` is NULL on rows with a real box score), and a row with attempts and no assists would be averaged with nothing said.
- **User sees:** possibly an average over fewer games than the table's G column, uncaveated.
- **Next step:** one query over `team_box_stats`: rows where `fieldGoalsAttempted` is set and `assists`, a rebound column or `threePointFieldGoalsMade` is NULL, by season. If any, count per column.
- **GitHub:** #312

### The feed's 91 questions whose Reading and deletion disagree are held but not read one by one
- **Found:** 2026-10-11, the Phase 3 review (`~/association-research/reviews/phase3-2026-10-11/`, finding 9).
- **Evidence:** `claims_ledger.py run --feed`: the Reading's `unread` and the deletion disagree on 91 of the 2,082 feed questions (the 628's 3 are `HELD_DISAGREEMENTS`, each with its reason). Since the cleanup's (b)5 (2026-10-12) they are held by question (`scripts/ledger_held.json`; `report` exits 1 on a new one or a held one gone), with the unread words (3,660) and the 177 questions holding an unread number - which (b)5 read one by one: 6 fixed there (a span "to" a bare year, "2015 16"), the rest filed by shape (the five P1 entries of 2026-10-12 above, and the "Feed numbers" lines on #369, #371, #357, #345, #270, #388, #189 and #146) or not bugs (a typo nothing reads, a number restating a season read, a refusal whose number the refusal needs no reading of: "finals", "27 years old").
- **User sees:** nothing directly; a disagreement is a claim over a word the reading does not depend on, or a word read without a claim.
- **Next step:** read the 91 as step 3 read the 628's: each a subject-reading claim declared and never probed (`ROADMAP-TYPES.md`, Still open 16), or a rule reading a word it does not claim - fix the second kind, and give each held one its reason as `HELD_DISAGREEMENTS` does.
- **Priority:** P3 - a check whose population is too small to see the shapes it is for.
- **GitHub:** #382

### A range closed by a date ("before", "until", "prior to" a day) is refused by value: "steph curry vs hawks prior to 02/03/2024"
- **Found:** 2026-10-13, the P1 sweep (lane S), fixing #323 (days written in numbers).
- **Evidence:** the cuts tagger reads "before|until|through|prior to <day>" (`lexicon.CALENDAR_DATE`'s and `NUMERIC_DAY`'s range group) as a situation, and `calendar.parse_situation` reads no kind for it, so the relation refuses it by value: "steph curry vs hawks prior to 02/03/2024" (the feed) -> "'prior to 02/03/2024' is not something the games are read by - ...", as "steph curry vs hawks before february 3 2024" is. A "since" is read (`since_date`, every game from the day on); its mirror is not. 1 of the 2,082 feed questions; until #323 it answered the 2024 season's 2 meetings ("prior" unread), the worded form the one game on February 3.
- **User sees:** an honest refusal of a question the warehouse can answer.
- **Next step:** an `until_date` calendar kind (`eastern_date < day`, `calendar_clause`) with the span closed at the season the day falls in and opened at the career's first season - which the span cannot state today (an `until` with no `since` is no range `Span` reads) - a test per wording.
- **Priority:** P3 - a refusal.
- **Source:** ours.
- **GitHub:** #414

### The teams' ranking over every season is refused: "nba winningest percent franchises all-time"
- **Found:** 2026-10-14, the P1 sweep's lane T3, measuring #372's "winning percentage" class.
- **Evidence:** since Phase 5, step 6 (lane N) "winningest" and "franchises" name the teams' ranking (`lexicon.LEAGUE_TEAMS_RANKED_ROW`; it ranked the league's players by points per game), and the teams' ranking declines a career: "nba winningest percent franchises all-time" (1 of the 2,082 feed questions) answers "Nothing here answers this question as a ranking of teams: every season of a career cannot be read here - it would answer for a different span than was asked." It reads a range ("winningest teams since 2015", "most wins nba 2020s"). "losingest" is left unread by the row: nothing reads its end (the window tagger's `rank`), so read as the teams' ranking it would rank the best records.
- **User sees:** an honest refusal where the franchises' all-time records were asked.
- **Next step:** the teams' ranking over a career read as the range from the standings' first season (`team_seasons`, lane T's relation), with the franchises' former names folded in by team id; "losingest" read as the ranking's worst end by the window tagger.
- **Source:** ours.
- **Priority:** P3 - a refusal by name.
- **GitHub:** #433

### A team's opponents' percentage, and a ranking of teams by a figure given up, are refused: "celtics opponents fg% this season", "which team gave up the most rebounds"
- **Found:** 2026-10-13, the P1 sweep's lane L, finishing #348; the answer side fixed by lane T2, the reader's remainder by Phase 5, step 6's lane L (a box-score count given up is the opponents', summed by the team compiler, `CHANGES.md`).
- **Evidence:** on `p5s6-L`, "celtics opponents fg% this season" is refused "no team metric for stat 'fg%'" (a percentage is no total the compiler sums, and the team-season relation has no opponent percentage), and "which team gave up the most rebounds" is refused "no team metric for stat 'rebounds'" - the teams' ranking reads the team-season table's metrics, of which only points allowed is an opponent's figure. Neither answers a different question since the step (the ranking answered the teams' own rebounds before it).
- **User sees:** a refusal - naming the stat, not that the opponents' figure is what is missing.
- **Next step:** the opponents' percentage from the games' opponent box rows (made over attempted, summed), and a ranking of teams by the opponents' summed box rows (the team relation's, lane T's seam); and the refusal saying "the opponents'" where the measure's `whose` is theirs.
- **Source:** ours.
- **Priority:** P3 - a refusal (0 of the 2,710 readings).
- **GitHub:** #425

### A team named beside a player in words no read places is refused by name: "Mikal bridges logs at philly", "kd stats in 4th quarter playoffs okc"
- **Found:** 2026-10-14, Phase 5, step 2, reading the moves of the possessive-nickname fix ("Lebrons season outcome during lakers" became LeBron's 2026 line, "during lakers" applied by nothing).
- **Evidence:** since Phase 5, step 6 (lane N) a team the words name on a player's question that no read places is refused by the `team_not_read` cause naming it (`subject.team_not_read`), and the tenure is read after "for", "with", "with the", "as (a)", "on", "during", "joined", "in <nickname>" and from a team word beside his name, the opponent's "t wolves" too. Still refused by the cause on the 2,082 feed questions (13): "Mikal bridges logs at philly" and "Scoot henderson at pelicans log" ("at <team>": the opponent with the venue away - the venue is the cuts tagger's, `cuts.py`, and the opponent the subject reading's; read as the opponent alone it would answer his home games too); "kd stats in 4th quarter playoffs okc" and "ochai agbaji shot chart 2023-24 utah" (a team trailing the question, no lead); "kd stats in 4th quarter playoffs without the warriors" (no team is absent); "jaylen brown stats vs last 10 games the knicks" (the versus span holds a window before the team); "OG vs celtics since playing on the knicks" ("OG" is two letters, too short for the tenure's player-before check, `subject._named_before`); "allen iverson vs michael jordan vs bulls" and its twin (a pair, no tenure is one player's); "anthony davis 2020 playoffs vs Portland, houston and nuggets" (three opponents); "Saddiq Bey stats with Detroit Pistons and Atlanta Hawks" (two tenures); "paul george and kawhi stats with the clippers" (a pair's tenure); "how old was shaq when he won in miami" (an age).
- **User sees:** a refusal naming the team, where his games at a team, or two teams, were asked.
- **Next step:** "at <team>" with lane L (the cuts tagger reads the venue "away" from "at" before the opponent the subject reading reads); several opponents or tenures as a list (a cell holding more than one team) is a relation change; each wording enumerated.
- **Priority:** P3 - a refusal by name; 0 of the 628, 13 of the 2,082.
- **Source:** ours.
- **GitHub:** #436

### A single bare "N stat" line on a reader whose shape is not a line is read by nothing: "2 threes in games Jamal Murray played including playoffs" answers his 3.2 threes per game
- **Found:** 2026-10-09, Phase 3 step 2's line slice (`~/association-research/stages/line_family.py`); the stated forms fixed and the rest re-measured 2026-10-10, Phase 3, step 3, where the Reading's own unread words name each number.
- **Evidence:** the lines tagger's rule (`line.read_lines`): every line on `threshold_count`, `record_when`, `streak` and `single_game_high` (`point.THRESHOLD_ASKS`, the four shapes' keys since Phase 3, step 4, the key's reading `point.lines_under` since its slice (b)); since Phase 3, step 3 one line stated outright ("N+ stat", "at least N stat") on a log or a player's splits (`point._LINES_ONE_NARROWS`: "anthony edwards log with at least 50 points" lists his one 50-point game, where it listed his last 10); elsewhere a floor of minutes, a line under a number, or two or more "N+ stat" pairs. A bare "N stat" with no "+" is read by nothing outside the line readers, nor one stated outright on a player's line (`player_stat`: "lebron 20+ points this season" can ask whether he averages it). On the 2,710 readings, with the number in `Reading.unread`: "2 threes in games Jamal Murray played including playoffs" (`player_stat`, one of the 628: his 3.2 threes per game over 81 games), "centers min 2 threes v bos gamelog 2024" (the centers' log vs Boston, every game), "harden 61 points" (the league's scoring leaders - "harden" unread too), "how many 30 point games did maxey have without embiid" (the with/without split, not a count), "most 40/50/60 point games in nba history" (refused "team_leaderboard cannot honor ['span']" - a count of a PLAYER's games read as a team's), "Lakers playoff games with exactly 99 points since 2000" (the Lakers' total points).
- **Fixed since (2026-10-13, the P1 sweep's lane L):** a line stated over or at a number by its lead or a plus on any box-score stat ("15+ fga", "13+ free throw attempts", "15 or more field goal attempts", "more than 40 points", "exactly 25 fga") is read where a line under a number is (#371 and this entry's first class); "jalen suggs game log 15+ fga" lists his 13 such games. "how many 30 point games did maxey have without embiid" is a count beside the companion on this tree. Still open, below: the bare forms; "at least 15 fga" (the minimum reader takes "at least N <stat>" as a ranking's qualifier and the log drops it - lane S's `window.read_minimum`); "how many games did lebron have 15 or more field goal attempts" and "most games with 15+ fga" (a count over a line on a stat the threshold spellings do not hold: the first is answered as the narrowed log, "last 10 of 36 games", the second as the FGA-per-game ranking, since `line.threshold_named` reads the threshold spellings only and the rules read a count with none as a ranking).
- **User sees:** a fluent answer to a broader question - every game, or a ranking - with the number the question gave set aside.
- **Next step:** one at a time, each with the feed's moves enumerated: a bare "N stat" after "games with" on a log or a line ("2 threes in games ... played" as games with 2+ threes); "how many N point games ... without X" as a count beside the companion (the count's child grammar, not the with/without split); "most N point games in nba history" a count of players' games under the league subject, not the team ranking; "exactly N" as an equality line. The pair grammar's own false pairs stay out ("2pt percentage", "top 20 rebound leaders" read as lines by `lexicon.threshold_pairs`; a test per wording).
- **Since Phase 5, step 1 (2026-10-14):** the entry's wordings with a bare "N stat" line on a non-line shape ("2 threes in games Jamal Murray played including playoffs", "harden 61 points" and 7 more of the 2,082) refuse by name now (`unread_number`); what remains is the READ of the line; a refusal by name is the standing floor, the fix is the read.
- **Since Phase 5, step 2 (2026-10-14):** ONE line the words set on the games - "games with 2 threes", "2 threes in games he played", "30 point games" (`lexicon.LINE_ON_GAMES_BEFORE`/`_AFTER`, `LineCandidate.games`) - narrows a player's line, log or splits however it is stated (`point._LINES_ON_GAMES_NARROW`), and "min N stat" is at least N: "2 threes in games Jamal Murray played including playoffs" (the 628's one) answers his line over his 59 games with 2+ threes, "centers min 2 threes v bos gamelog 2024" the centers' games with 2+ threes. Still open: a bare "N stat" with no games word on a non-line shape ("harden 61 points": one game or a line - refused by name, the right floor), two or more bare lines on one game off a line shape ("lebron games with 30 points 10 rebounds"), a count's second line ("most 50 point games with only 1 fta", #358), a line on a stat the threshold spellings do not hold ("steph curry games with 15 field goals attempted", "lebron james points per game when taking 15 shots") and a line of 0 ("rodman games with 0 field goal attempts") - each refused by name since step 1.
- **Priority:** P3 - was P1, a fluent wrong answer, until Phase 5, step 1; now a refusal by name - a broader question answered fluently; 1 of the 628, 9 of the 2,082.
- **Feed numbers:** the feed's unread numbers, read one by one in the Phase 3 review's cleanup (b)5 (2026-10-12, `claims_ledger.py run --feed` on `4c595b8`, answers from `stage_snapshots.py run --feed`): 16 questions hold the unread number of a single line on a game a non-line reader leaves unread - "jalen suggs game log 15+ fga" (2), "steph curry games with 15 field goals attempted" (2), "lebron james points per game when taking 15 shots" (2), "Towns over 9 rebound", "games with 13+ free throw attempts by the 76ers" (2, a team's), "rodman games with 0 field goal attempts", "knicks record in playoff games when Mitchell Robinson shoots 4 ft", "most 50 point games with only 1 fta" (a count's second line, #358), "warriors' record last season when committing 10 or fewer turnovers" (3 wordings, #325), "fewest field goal misses ... at least 92 fgm + opp fgm"; every one answered `fast` without the line.
- **GitHub:** #357

### A team's single-game high is answered as the team's season line: "most points in a game in cavs history"
- **Found:** 2026-09-21, live fast-path sample `~/association-research/statmuse-2026-09-large/live_sample200_2026-09-21/`; the span half fixed in the P1 sweep (lane S, 2026-10-13).
- **Evidence:** "in <team> history", "in <team> franchise history", "of the <team>'s history" and "franchise leader/record" are read as every season on record since the sweep (`lexicon.CAREER_WORDS`): "most points in a game in pistons history power forward" answers the Pistons power forwards' best games since 1994, naming the team ("every power forward with the Detroit Pistons, regular season career (1994 on) - top 3 by points"). But "most points in a game in cavs history", "... in pistons history" and "most points in the history of the philadelphia 76ers" are read under the team's season line (`team_seasons`/`scalar`/`line`, #364's shape) and are refused now ("every season of a career cannot be read here"), where they answered the 2026 points per game.
- **User sees:** an honest refusal where the franchise's (or its players') best single game was asked.
- **Next step:** #364's - the team named beside "in a game" read as its players' single-game high (`team_players`), which the career span now reaches over every season.
- **Priority:** P3 - a refusal.
- **GitHub:** #167

### "nba most fga with 0 fgm single game" ranks the league's FGA highs with "0 fgm" unread, and "without fgm" is read as a teammate
- **Found:** 2026-09-27, checking the answers the #168 fix moved. Rewritten 2026-10-13 (lane N of the P1 sweep), which made "nba" alone no team word where a measure is named (`parse._parent_asked_nba_unmeasured`), so both wordings are the players' now and "single game" is the high (`CHANGES.md`).
- **Evidence:** on sweep-N after that fix, "nba most fga with 0 fgm single game" answers "every player, 2026 regular season - top 3 by FGA: Cade Cunningham 45 ..." - the high over FGA, the "0 fgm" line unread (the lines tagger reads no line at 0, lane L's); "nba most fga without fgm single game" reads "fgm" as an absent teammate and answers "No player found matching 'fgm'.", and "Nba most fga without fgm" is refused "games without fgm cannot be read here".
- **User sees:** the league's biggest FGA games where games with no made field goal were asked (a broader question, fluently); a refusal naming "fgm" as a player.
- **Next step:** the lines tagger reads a line at 0 ("0 fgm", and "without fgm" as fgm at 0) - lane L's `line.py`; then the high over FGA keeps the line, or the point reader refuses the predicate by a cause naming it until the compiler has one.
- **Source:** ours.
- **Since Phase 5, step 1 (2026-10-14):** "nba most fga with 0 fgm single game" refuses by name now (`unread_number`: the 0); what remains is the line at 0; a refusal by name is the standing floor, the fix is the read.
- **Priority:** P3 - was P1, a fluent wrong answer, until Phase 5, step 1; now a refusal by name - a broader question answered (1 of the 2,082), and a refusal by the wrong cause (2).
- **GitHub:** #266

### A ranking within the subject's own conference or division is refused: "top scorers in the western conference this season"
- **Found:** 2026-10-09, Phase 3 step 0; refused by a cause in the P1 sweep (lane S, 2026-10-13).
- **Evidence:** since the sweep a conference or division on a ranking that no "vs"/"versus"/"against" leads ("in the western conference", "who leads the east", "eastern conference players points leaders", "atlantic division standings": `cuts.own_alignment`, `lexicon.ALIGNMENT_AS_OPPONENT`) is refused before any reader runs (`own_alignment`): "A ranking within a conference or division ('in the western conference') is read by nothing here yet - ...", where "top scorers in the western conference this season" ranked every player's games against the West and "who leads the east in scoring" the league. Off a ranking the words keep the opponent's reading ("lebron points in the west": his games against the West). None of the 628 or the 2,082 holds the class (the wordings are probes).
- **User sees:** an honest refusal where a conference's own leaders were asked.
- **Next step:** a ranking pool narrowed by the player's or team's own alignment that season (`team_alignment` on the team's id and season, the standings' conference and division), then the cause goes. #25 is the same gap from the data side.
- **Priority:** P3 - a refusal.
- **GitHub:** #339

### A window with a misspelled "last", or with no "games" after its count, is no window: "jalen brunson lasst 5 games" answers his season line
- **Found:** 2026-10-13, the P1 sweep (lane S), what #369's fix left: #369 read a misspelled "games" after "last N" (one edit from "game(s)", and "match(es)", `lexicon.WINDOW_GAMES`); the feed's other unread window numbers are a different shape.
- **Evidence:** the 2,082 feed answers on the fix's tree (`stage_snapshots.py run --feed`): a misspelled "last" - "aaron gorodn lat 5 games" (the league's 2026 points ranking), "jalen brunson lasst 5 games" (his 2026 season line); a count with no "games" - "jalen brunson stats last 5 away" (his 38 road games), "Sengun last 5 stats vs knicks" (his 2 games against them this season), "bradley beal 15 games" (his 6 games of 2026); and four nothing here reads - "hield ast 5 games" ("ast" is "last" or assists), "How many threes did Pritchard make 2 games ago" (the league's ranking), "ja morant last 10 first quarters against nets" (a window of quarters), "warriors fir 10 game redorn evry season" (a window per season, misspelled). <!-- codespell:ignore evry - the feed's question as typed -->
- **User sees:** a season line, a ranking or this season's meetings where the last N games were asked; nothing says the window was set aside.
- **Next step:** a "last" one edit off ("lat", "lasst") read as the window's end beside a count and "games" - excluding the words one edit from "last" that are words of their own ("east", "least", "lost", "list", "fast"; "at least 5 games" is a line) - and "last N" before a venue or "stats" with nothing after it read as N games; each measured on both corpora first, a test per wording.
- **Since Phase 5, step 1 (2026-10-14):** 9 of the 2,082 ("aaron gorodn lat 5 games", "bradley beal 15 games", "hield ast 5 games", ...) refuse by name now (`unread_number`, `unread_window`); what remains is reading the window; a refusal by name is the standing floor, the fix is the read.
- **Priority:** P3 - was P1, a fluent wrong answer, until Phase 5, step 1; now a refusal by name - a different question answered fluently; 9 of the 2,082.
- **Source:** ours.
- **GitHub:** #411

### A player's first or last N seasons is read by nothing: "lebron james stats through their first 9 seasons" answers his 2026 line
- **Found:** 2026-10-12, the Phase 3 review's cleanup (b)5 (the feed's unread numbers).
- **Evidence:** 3 of the 2,082 feed questions: "lebron james stats through their first 9 seasons" (his 2026 line), "Compare Jayson Tatum stats to LeBron James stats through their first 9 seasons" (their 2026 lines), "Any Player with better Stats in his first 9 seasons than LeBron in his last 9 seasons?" (his line since 2018 - "last 9 seasons" read as the relative "past N seasons" of the league's calendar, `span._relative_since`, and "first 9" by nothing). The window tagger excludes a count of seasons from its own ends (`lexicon.COUNTED_END`'s `(?!\s+(?:seasons?|years?))`), and the span reads "last N seasons" as the calendar's, never the player's own.
- **User sees:** a wrong answer: one season's line where the first nine of his career were asked.
- **Next step:** an end of a player's OWN seasons ("his first/last N seasons", "through their first N") read as a span over the seasons on record for him (`season_line.seasons_on_record`), the comparison's per player; refused by a cause until then.
- **Since Phase 5, step 1 (2026-10-14):** the quoted wordings refuse by name (`unread_number`: the N of "first N seasons"), the honest floor; the read needs a span relative to the player's own seasons on record - a typed field on the shared `reading.Span`, resolved in `player_relation.span_of` and the season line, per player for a comparison (lane L's measurement, step 6).
- **Priority:** P3 - was P1 until step 1; now a refusal by name. Before: 3 of the 2,082.
- **GitHub:** #418
### A player's first or last N seasons is refused: "lebron james stats through their first 9 seasons"
- **Found:** 2026-10-12, the Phase 3 review's cleanup (b)5 (the feed's unread numbers); refused by a cause in the P1 sweep (lane S, 2026-10-13).
- **Evidence:** a count of a career's own seasons by their place ("their first 9 seasons", "his last 9 seasons", "first 2 years": `lexicon.OWN_SEASONS`) is refused by the parser before any reader runs (`own_seasons_window`, `window.own_seasons_window`): "A career's own seasons by their place ('their first 9 seasons') are read by nothing here yet - name the seasons themselves ...". 3 of the 2,082 feed questions ("lebron james stats through their first 9 seasons", "Compare Jayson Tatum stats to LeBron James stats through their first 9 seasons", "Any Player with better Stats in his first 9 seasons than LeBron in his last 9 seasons?"); until the sweep they answered his 2026 line, their 2026 lines and LeBron's line since 2018.
- **User sees:** an honest refusal where the first nine seasons of a career were asked.
- **Next step:** read it: the window tagger types it as a window of SEASONS with its end (`Window(order, count, of="seasons")`, beside a history's "past 5 years"), and the season line resolves it per player over the seasons he has on record (`season_line.seasons_on_record`, the relation's: the first or last N of his regular seasons) - a comparison per player, each over his own; then the cause goes.
- **Priority:** P3 - a refusal.
- **Source:** ours.
- **GitHub:** #392

### A count over a line at 0 is read by nothing: "most 0 point games in nba team history" is refused by its number
- **Found:** 2026-10-12, the Phase 3 review's cleanup (b)5 (the feed's unread numbers); narrowed by the P1 sweep's lane N (the league's "N point games" read as the players' count, a count beside a teammate's absence as his count) and by Phase 5, step 6's lane L (a count of triple-doubles over a line, #437, read with both predicates, `CHANGES.md`).
- **Evidence:** on `p5s6-L`, "most 0 point games in nba team history" is refused "'0' is a number this reading did not use ..." (`unread_number`): the lines tagger reads no line at 0 ("a threshold of 0 counts every game", the count's own cause), and "0 point games" means the games at exactly 0 - a scoreless game - which no line reads (a line is at or over its number). "nba most fga with 0 fgm single game" is #266's. Neither answers a different question since Phase 5, step 1.
- **User sees:** a refusal naming the number.
- **Next step:** read "0 <stat> games" (and "scoreless games") as a line at exactly 0 (`op` "=", which the lines carry - `lexicon.EQUALS_LINE`), a count of those games; measure the feed first.
- **Source:** ours.
- **Priority:** P3 - a refusal by name; 1 of the 2,082.
- **GitHub:** #394

### A token holding a digit that is no number as written is refused by name, never read: "most 3ot points in a game by a team nba", "lakers play in 20222-23"
- **Found:** 2026-10-14, the Phase 5 review: 15 of the ledger's 24 held unread-number questions answered fluently past such a token (step 1's number class read whole numbers only).
- **Evidence:** since step 7's narrowing (2026-10-14, the lead) a glued token with a digit that names no measure and is no season form (`lexicon.MEANING_GLUED_DIGITS`, `MEANING_SEASON_FORM`) is an unread number and refused by name: "3ot" (overtime points, five feed wordings), "20222-23" (a garbled season), "67-15" (a record asked as a condition). What remains is the read: overtime periods as a period (#355), a record as a line on the team's record, a garbled season corrected or asked back.
- **User sees:** a refusal naming the token, where the plain answer past it stood.
- **Next step:** one read at a time, by reach on the 24 held questions; a case per wording.
- **Priority:** P3 - a refusal by name.
- **Source:** ours.
- **GitHub:** #450

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
- **Restored at Phase 5's close (2026-10-14):** the entry had been lost in a merge while GitHub #240 stayed open (the Phase 5 review).
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
- **Restored at Phase 5's close (2026-10-14):** the entry had been lost in a merge while GitHub #241 stayed open (the Phase 5 review).
- **GitHub:** #241

### "last time X did Y", "last triple double" and "last week" are refused by name; the read - the most recent game over a line - is not done
- **Found:** 2026-10-15, the lead, the unread-meaning harness on the Phase 5 close run; the wrong answers (the season high, a count, the season line for ten feed wordings) fixed the same day at `2ee1f513` by the window class refusing them (`lexicon.MEANING_WINDOW_NEXT` counts a time word, a yes/no measure's word and a period word).
- **Evidence:** "last time chris paul dropped 20 assists in a game", "scottie barnes last triple double", "tyler herro last week", "When was the last time the Knicks opened the nba season" answer "'last' asks for a stretch of games this reading did not count - say how many ("last 5 games") or which one ("last game")"; ten feed wordings, held as refusals in `~/association-research/stages/unread_meaning.py`.
- **User sees:** a refusal that names the word, with a wording that reaches the newest N games but not the newest game OVER A LINE.
- **Next step:** the read: "last time X did Y" and "last <yes/no>" is a log over the line with a window of one, newest first (`player_games/rows/date` with `lines` and `window.count=1`, a key the grammar names); "last week" a date range of seven days back from the latest game; under Jeff's lifting if the planner needs a shape for it (Phase 6, step 2).
- **Priority:** P3 - a refusal by name where an answer exists.
- **Source:** ours.
- **GitHub:** #453

### A team's points before 1994 may reach the standings' zeros
- **Found:** 2026-10-15, the feed sample's keyer (shard B), re-measured by the lead.
- **Evidence:** `standings.pointsFor` and `avgPointsFor` are 0 for every team from 1988 to 1993 (DATA.md). Which reads reach those columns is not checked: a team's season points come from `team_season_stats` on the unnarrowed read, and the standings relation serves records; if any read takes points from `standings` for those seasons it answers 0.
- **User sees:** a 0 or a 0.0 per game for a team season before 1994, if such a read exists; otherwise nothing.
- **Next step:** grep the standings relation and `team_seasons` for the two columns; where one is read, floor it at 1994 or read `team_season_stats` instead.
- **Priority:** P3 - unverified reach.
- **Source:** ESPN.
- **GitHub:** #463

### A pre-1994 season named in a question reaches two different seasons by table
- **Found:** 2026-10-15, the feed sample's keyer (shard B), re-measured by the lead.
- **Evidence:** `player_season_stats` and `standings` label 1988-89 as 1989; `games` and everything derived from it label it 1988 (DATA.md, "`player_season_stats` labels a pre-1993-94 season by the year it ENDS"). "Ewing 1989 stats" answers 1988-89 from the season line; "Ewing's games in 1989" answers 1989-90 from the games; a read joining the two tables by `season` before 1994 is off by one year.
- **User sees:** two answers naming "1989" about different seasons, depending on the shape; a joined read (a season line qualified by games played) misaligned.
- **Next step:** one reading of a pre-1994 year for every table - the postseason rule (select by the calendar year played) extended to the regular season on `games`, or the season-line and standings labels shifted at load - decided with the coverage floors in view (`player_season_stats` reaches 1977 as a survivor sample).
- **Priority:** P3 - the era is under most floors; the inconsistency is real.
- **Source:** ESPN.
- **GitHub:** #464

### Play-in games are in no answer
- **Found:** 2026-10-15, the feed sample's keyer (shard A), re-measured by the lead.
- **Evidence:** `games` holds no play-in game since 2021 (DATA.md, "Play-in games are not in `games` at all"); NetPoints holds 545 play-in rows. A log over April, a team's record in a span, a player's game count in a season are short the play-in games; "miami's play-in games 2023" has nothing to read.
- **User sees:** a fluent answer short those games, with no caveat; a play-in question refused for want of a reading or answered from the regular season.
- **Next step:** find the endpoint's request shape for the play-in (the standings endpoint knows the season type), pull it as its own `season_type`, and say it in the coverage declaration; until then a coverage note on any span that includes mid-April from 2021.
- **Priority:** P3 - a gap in the data, visible only where a question names it.
- **Source:** ESPN.
- **GitHub:** #468

### A period read cannot see the 31 games without a full linescore
- **Found:** 2026-10-15, the feed sample's keyer (shard A), re-measured by the lead.
- **Evidence:** 31 real games from 1994 to 2000 have fewer than four period scores on one side (DATA.md): a quarter, half or overtime question over a span holding them reads them as missing with no note.
- **Next step:** the period reads count the games they could not see, as `_empty_box_scores` does for a per-game answer, and say so.
- **Priority:** P3 - a small gap, unsaid.
- **Source:** ESPN.
- **GitHub:** #469

## P4: tooling, docs, low impact

### The web check's documented command no longer launches
- **Found:** 2026-10-23, lane p6-alts (D23's pilot)
- **Evidence:** `uv run --with playwright python scripts/check_web_ui.py` resolves playwright 1.64, which wants chromium build 1248; the box has 1243; `--with playwright==1.63.0` works.
- **User sees:** nothing.
- **Next step:** pin `playwright==1.63.0` in AGENTS.md's command (and `page_renders.py`'s).
- **Priority:** P4.
- **Source:** ours.
- **GitHub:** #537

### A "since" range is worded as a career: "the past two seasons" says "regular season career (2025 on)"
- **Found:** 2026-10-23, lane p6-alts (D23's pilot)
- **Evidence:** the span's phrase for a range with no end is the career's.
- **User sees:** a heading that calls two seasons a career.
- **Next step:** say the range ("since 2024-25").
- **Priority:** P4.
- **Source:** ours.
- **GitHub:** #538

### A one-game window carries the D13 sentence: "... in his last game" says "No season was named"
- **Found:** 2026-10-23, lane p6-alts (D23's pilot)
- **Evidence:** the offered latest-meeting wording answers one game and still appends the career sentence.
- **User sees:** a sentence about seasons beside one game.
- **Next step:** no D13 sentence where the window is one game.
- **Priority:** P4.
- **Source:** ours.
- **GitHub:** #539

### Champions are derivable from the round-4 series winner
- **Found:** 2026-10-17, lane p6-round (the playoff round), the survey of what ESPN serves.
- **Evidence:** every postseason holds 15 series (8/4/2/1); the Finals' winner is the team with four wins in round 4 (`real_games.playoff_round`, `series_game`). "Who won the NBA Finals in 2003-04?" is `championship`, refused.
- **User sees:** a refusal on a fact the warehouse holds.
- **Next step:** a champion read needs a new shape (D11, Jeff's lifting).
- **Priority:** P4.
- **Source:** ours.
- **GitHub:** #522

### A shot chart's file name depends on the rows' order, so a warehouse rebuild renames it
- **Found:** 2026-10-17, the lead, a baseline run on master before and after `real_games` was rebuilt (no code change).
- **Evidence:** "Kawhi shot chart 2018 2019" wrote `shotchart_kawhi_leonard_69g_400975179_401071603.html` before and `..._401071028_401071783.html` after, every figure identical: the name takes the first and last event id of rows read without an order (DuckDB keeps none, `preserve_insertion_order=false`). Four feed answers.
- **User sees:** nothing but the file name; the populations see a move no change made.
- **Next step:** name the file from the sorted ids (min, max).
- **Priority:** P4.
- **Source:** ours.
- **GitHub:** #523

### A per-game figure at an exact half rounds to even: Kobe's 2002 Finals assists said 5.2 where the key says 5.3
- **Found:** 2026-10-17, the lead, FS103 against its key.
- **Evidence:** 21 assists over 4 games is 5.25; the sayer's format rounds the float half to even (5.2), the key and the record books half up (5.3).
- **User sees:** a figure 0.1 off the published one.
- **Next step:** round half up in the sayer's per-game format (`decimal`), measured on the populations.
- **Priority:** P4.
- **Source:** ours.
- **GitHub:** #524

### The rebuild's attempts and rebound sides are measured but nothing reads them
- **Found:** 2026-10-17, lane p6-traps, fixing #501.
- **Evidence:** `reconstructed_box.py` measures FGA 99.5%, FTA 100% and OREB/DREB 99.8% exact per game; `conditions.UNGATED_ON_REBUILD`'s comment said no attempt column was measured. So FG% and FT% are read over fetched games only and per-game attempts leave rebuilt games out.
- **User sees:** a percentage or attempts figure over fewer games than it could (said by the `lines_rebuilt` note).
- **Next step:** decide whether FGA, FTA, OREB, DREB join `player_games.REBUILT_STATS`.
- **Priority:** P4.
- **Source:** ours.
- **GitHub:** #514

### A tie for the top larger than TIE_ROOM is cut at limit + 10
- **Found:** 2026-10-17, lane p6-traps, writing `player_games.ordered_cut`.
- **Evidence:** a tie at the cut too big for the room is left out whole (and the game-level ranking says it, "and N more tied"), but a tie for the TOP has nothing ahead of it and is cut at the room, said as whole. No case in either population.
- **User sees:** nothing yet.
- **Next step:** say "and N more" where a top tie is cut.
- **Priority:** P4.
- **Source:** ours.
- **GitHub:** #515

### Research tooling: the widening harness flags whole ties; page_renders pulls an unpinned playwright
- **Found:** 2026-10-17, lanes p6-traps and p6-sentence.
- **Evidence:** `widening_checks.py`'s tie_at_cutoff flags the last two listed rows sharing a figure; since #476 a tie at the cut is listed whole, so all 13 feed inventory flags are whole ties ("luka highest scoring games vs suns": top 4 of 3 asked). `stages/page_renders.py` under `uv run --with playwright` pulls a playwright whose Chromium is not installed.
- **User sees:** nothing.
- **Next step:** the tie check reads whether the NEXT row past the listing ties (the warehouse) or skips rows listed beyond the asked count; pin `playwright==1.63.0`.
- **Priority:** P4.
- **Source:** ours.
- **GitHub:** #516

### A module-level constant reassigned in its own module is caught by no gate
- **Found:** 2026-10-17, lane p6-causes: `lexicon.POSITION_GROUPS` assigned twice in the lexicon; ruff, mypy and `check_duplicate_names.py` (cross-module only) said nothing, and one test failed.
- **Evidence:** the duplicate-names check is name-only across modules by design; within a module a second assignment silently wins.
- **User sees:** nothing until a value moves.
- **Next step:** a hook that flags a repeated module-level assignment of an UPPER_CASE name within one module.
- **Priority:** P4.
- **Source:** ours.
- **GitHub:** #500

### The sayer prints a range's start twice: "since December 11, 2023 since 2023"
- **Found:** 2026-10-14, Phase 5's step 6, lane L, probing #412's wordings on master (`5a621f5`); pre-existing.
- **Evidence:** a player's line over a dated range says the date and the span's opening season both - "... in 141 games since December 11, 2023 since 2023" - where one of the two states the range (the same shape as #415's "in 141 games since <date> ... from 2023-24", filed from the first sweep).
- **User sees:** a heading that says its range twice, the second a looser restatement of the first.
- **Next step:** `say`'s span phrase drops the season clause where a calendar date opened the range (the `since` fact beside `Span.since`); one test; measure the 2,082 headings holding "since" twice.
- **Priority:** P4 - wording.
- **Source:** ours.
- **GitHub:** #442

### A true-shooting line over a date range is a mean of per-game percentages: "averaged 59.4% TS% per game"
- **Found:** 2026-10-14, Phase 5's step 6, lane L, probing a history's measures on master; pre-existing.
- **Evidence:** a player's line narrowed to a date range reports a shooting percentage as the mean of each game's percentage ("averaged 59.4% TS% per game"), where the season line's figure is the ratio of the sums (`compose.core.RATES`); the two differ wherever his attempts vary by game.
- **User sees:** a percentage that is not the one a season line would give for the same games.
- **Next step:** a rate over a narrowing is the ratio of the games' sums, as the compiler's `RATES` already state for the per-game aggregate; one test over a range whose games differ in attempts; measure which narrowed lines carry a percentage on the 2,082.
- **Priority:** P4 - a figure off by the averaging method, on narrowed percentage lines only.
- **Source:** ours.
- **GitHub:** #443

### The page's team log prints ESPN's side for a neutral-site game, where the sentence says "(neutral site)"
- **Found:** 2026-10-14, Phase 5, step 6's lane T, fixing the team log's neutral-site label in the sentence.
- **Evidence:** `compose.logs._team_log_games` marks a game played on neither team's floor (`neutral: True`, set only where true) and `say.say_team_log` says it "vs Orlando Magic (neutral site)"; `src/association/web/static/index.html`'s team log body (`g.home_away === "away" ? "at " : "vs "`, about line 1113) reads `home_away` alone, so the page's table still prints "at Orlando Magic" for the NBA Cup semifinal in Las Vegas. 0 recorded answers list a neutral-site game in a team's log.
- **User sees:** on the web page only, a neutral-site game in a team's log labeled as ESPN's home or road side, beside a sentence that says it was neutral.
- **Next step:** the renderer reads `g.neutral` and prints "vs ... (neutral site)" as the sentence does; `~/association-research/stages/page_renders.py` before and after.
- **Source:** ours.
- **Priority:** P4 - no recorded answer reaches it; the sentence is right.
- **GitHub:** #439

### 116 plain declines no recorded question reaches are still sentences, not causes
- **Found:** 2026-10-14, Phase 5, step 5, after its last class: the worded-declines harness reads 0 on both populations, and the source still holds `raise Unsupported("<sentence>")` at 116 sites outside `reading.py` (`compose/core.py` 21, `point.py` 15, `compose/team.py` 15, `compose/team_records.py` 12, `player_relation.py` 10, `compose/rankings.py` 7, `compose/splits.py` 6, `compose/logs.py` 6, and 13 more modules).
- **Evidence:** `grep -rn 'raise Unsupported(' src/association/query --include='*.py'`. Some are a reader stepping aside for the compiler's own sentence (no user sees them), some guard states the planner already refuses, and some would reach a user as "Nothing here answers this question as ...: <sentence>", answered `refused` - the shape step 5 removed from every recorded question (12 of the 628 and 176 of the 2,082 on `da2c39c`, 0 and 0 after).
- **User sees:** nothing on the recorded populations; on a new wording that reaches one, a decline said as a sentence (some still code-speak: "a team point names no measure", "no team compile for the 'x' shape").
- **Next step:** read each site, delete the unreachable, and make each reachable one a cause (`ShapeDeclined` with a kind at the end of `reading.CAUSES` and its phrase in `say.refusal_phrase`), one enumerated commit per class as step 5 did; `~/association-research/stages/worded_declines.py --zero` over the populations is the check, and a new recorded wording that reaches one fails it.
- **Source:** ours.
- **Priority:** P4 - no recorded question reaches one.
- **GitHub:** #438

### A line said over a since-date states the date and the span's opening season both: "... in 141 games since December 11, 2023 since 2024 (2024-2026 regular seasons)"
- **Found:** 2026-10-13, the P1 sweep (lane S), reading #353's moved answer.
- **Evidence:** "Nick Richards stats after Dec 11 2023" (the feed, on #353's fix) answers "Nick Richards averaged 8.5 points, 7.3 rebounds and 0.7 assists per game in 158 games since December 11, 2023 since 2024 (2024-2026 regular seasons)."; a team's record does the same on master ("celtics record since 1/26/2020": "The Boston Celtics are 343-167 (.673) since January 26, 2020 in the regular seasons since 2020."). The narrowing's phrase (the calendar cut) and the span's phrase (the season the cuts tagger opened the span at, `CutsRead.dated_since`) are each said; the log's heading says the date alone ("since December 11, 2023, last 10 of 158 games (2024-2026 regular seasons)").
- **User sees:** a redundant, slightly confusing clause; the figures are right.
- **Next step:** the sayer drops the span's "since <season>" where a `since_date` or `date_range` calendar cut opened it, as the log's heading does (`compose/say.py`, the answer side's).
- **Priority:** P4 - wording only.
- **Source:** ours.
- **GitHub:** #415

### "1 of these game has no box score from ESPN": the plural sits on the wrong word
- **Found:** 2026-10-13, the Phase 4 review (`~/association-research/reviews/phase4-2026-10-13/REVIEW.md`, finding 6).
- **Evidence:** `compose/say.py:265`, `f"{shown} of these game{'s have' if shown != 1 else ' has'} ..."`; 1 of the 628 and 4 feed answers (8 occurrences), both trees.
- **User sees:** "1 of these game has".
- **Next step:** "1 of these games has" - the noun always plural, the verb by the count; an enumerated commit (5 answers move in wording).
- **Source:** ours.
- **Priority:** P4 - a sentence's grammar.
- **GitHub:** #405

### The trace and the decisions name a team question's key by the player relation: "lakers longest winning streak" says "the words 'streak' name player_games/runs/line"
- **Found:** 2026-10-13, the Phase 4 review (`~/association-research/reviews/phase4-2026-10-13/REVIEW.md`, finding 8).
- **Evidence:** 65 team-kind readings of the 2,710 (62 logs, 3 streaks) are asked as `player_games/rows/date` or `player_games/runs/line` and planned on `team_games`; since Phase 4, step 2 the decision reason prints the key path where the label said `streak` or `game_log`.
- **User sees:** a misleading trace line (`--verbose`, the history file); no answer changes.
- **Next step:** print the key without its relation where the grammar's key is kind-free, or say in AGENTS.md and the trace's docstring that the asked key's relation is the grammar's, not the subject's.
- **Source:** ours.
- **Priority:** P4 - a trace line.
- **GitHub:** #406

### The SQL ratchet is blind to an aliased or `getattr`'d execute
- **Found:** 2026-10-13, the Phase 4 review (`~/association-research/reviews/phase4-2026-10-13/REVIEW.md`, finding 9), perturbing the ratchet.
- **Evidence:** `compose/logs.py` given `run = con.execute; return run("SELECT 1")`, or `getattr(con, "execute")(q)`: `scripts/check_ratchets.py` reads 74 on `fe806a6` and 80 on `d4f04b9`, unchanged; a direct `con.execute`, `con.sql`, `values_of`, `_executed` or a home-made door is caught on `fe806a6`.
- **User sees:** nothing; a gate with a hole.
- **Next step:** count any `.execute`/`.sql`/`.query` attribute read on a connection-typed name, and `getattr` with those names, not only calls; a test watched to fail on each form.
- **Source:** ours.
- **Priority:** P4 - tooling.
- **GitHub:** #407

### A measure tagger's claim holds the team's name: "how many 3 pointers have the magic made" is claimed whole as the measure
- **Found:** 2026-10-10, Phase 3, step 2's subject slice, adding the subject reading's claims.
- **Evidence:** on the 2,710 readings 4 settled team names claim no characters of their own: their words sit inside a `measure` claim the measure tagger made over the whole team-total phrase (`lexicon.TEAM_TOTAL`: "how many 3 pointers have the magic made", "so far this season, how many three-pointers have the magic made?", "How many 10+ point leads did the Sacramento Kings have", "How many games in the 23-24 NBA season did the Los Angeles lakers have"), so the team's claim folds into it (`span.claimed`). Every reading is right; the claims say "measure" for the team's words.
- **User sees:** nothing today; step 3's unread words would count the claim's characters as the measure's.
- **Next step:** the measure tagger claims the measure's own words ("3 pointers", "made"), not the span its total pattern matched; the 4 readings' claims move, nothing else.
- **Source:** ours.
- **Priority:** P4 - claims precision, 4 readings.
- **GitHub:** #368


### The 2-point percentage has no measure on the games relation, so a narrowed one is the season's
- **Found:** 2026-10-10, Phase 3 step 2's measure slice, writing the catalog (`measure.CATALOG["twoPointFieldGoalPct"]`: no `measure`, no `column`, no metric)
- **Evidence:** `stat_measure("twoPointFieldGoalPct")` was None on `508d643` (the compiler's `two_pct` derived measure exists, `compose.core.DERIVED`, but no alias reached it), so the point reader's `_default_player_stat` reads the whole line for a 2-point percentage narrowed to games ("lebron 2pt percentage vs the celtics") and the season line's reader answers it over the season instead (`season_line.SHOOTING_STATS` holds it). 22 of the 2,710 readings carry it, all histories or unnarrowed lines, so no population answer moves; the catalog records the gap as the code has it.
- **User sees:** a season figure where a narrowed one was asked, with no sentence saying so.
- **Next step:** give the key its games-relation measure (`two_pct`) in the catalog and check `compose/stats.py` reads the rate's parts; enumerate the moved readings (the point's `measures` change for the 22).
- **Priority:** P4 - no recorded question narrows it; filed from the catalog's own gap.
- **GitHub:** #363

### A teammate who played beside an absent one on the with/without split is dropped from the split without a word
- **Found:** 2026-10-09, Phase 3 step 2's line slice, writing the companions' projection
- **Evidence:** the slot-era writer wrote `with_player` only where no teammate was absent (the split divides by the absent ones then), so a `Companion(predicate="played")` beside a `Companion(predicate="absent")` on the split is held typed and read by nothing (the slot-era writer's rule, which `reading.companion_slot_names` and the parser's list of the names the reading settled on still read the typed companions by since Phase 3, step 4, slice (c) deleted the projection: the split's teammates who played count only with nobody absent; `tests/query/test_line.py::test_the_door_types_the_splits_teammates_and_a_role_beside_them` pins the typed value holding both). No wording tried reaches the split with both: "76ers record with maxey without embiid", "phi record without embiid when maxey plays", "76ers record with maxey playing and without embiid" all read `team_leaderboard` and refuse by Embiid's name; "76ers with and without embiid when maxey plays" reads a league `leaderboard`. 0 of the 2,710 readings carry both.
- **User sees:** nothing today; a wording that reached the split would answer the team's record with and without one player where the games the other played were asked.
- **Next step:** when the split's reader (`compose/presence.py`) takes the typed companions directly, narrow the games by the companions who played before dividing by the absent ones, and say so; and read the grammar for a team's record with one teammate and without another, which no row names today.
- **Priority:** P4 - no wording reaches it.
- **GitHub:** #360

### The sayer's import contract is checked on direct imports only: `compose.say` reaches duckdb through four modules' constants
- **Found:** 2026-10-05, Phase 2 step 6, when the last phrase helpers left `templates/` (the condition `pyproject.toml` named for checking the contract on chains).
- **Evidence:** `allow_indirect_imports = false` on "The compiler's sentence reads no warehouse" breaks it: `compose.say` imports `compose.logs` (`LOG_PERCENTAGES`, `log_key`), `conditions` (the split tables and labels), `player_games` (`PERIOD_LOG_COLUMNS`, `PERIOD_RATES`, `period_columns`) and `season_line` (`NETPOINTS_COMPARE_ROWS`, the season line's column tables), each of which imports duckdb for its statements.
- **User sees:** nothing; a sayer that reached the warehouse through one of those modules would pass the gate.
- **Next step:** move those constants into modules with no statements (the relation's vocabulary beside `measures.py`), then set `allow_indirect_imports = false` and watch it fail on a planted import.
- **GitHub:** #324

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
  - The ordinal suffix is written four times: `reading.ordinal_word` and `templates/common.py`'s `_ordinal` (since deleted) (identical, same module), `player_games.py:407`, `fingerprint.py:727`. All agree today.
  - Month names are defined six times (router.py:501, :511, :1084; calendar.py:44; conditions.py:451; subject.py:301), and `parse._PLAYER_LOG` omits May and June; number words were five copies until fe53c72 gave the parser one table (router.py:469, :477, :1706 and subject.py:111 still hold their own).
  - 13 of `PARENT_GRAMMAR`'s 30 rows have no direct `parent_intent` case in tests/query/test_parser.py (period_leaderboard, period_split, coach, team_players, the team top-N leaderboard, team streak, the team_stat fallback, player_netpoints, player+companions, both position rows, the everyone streak, everyone finals/game_log); the measurement that exercises them lives outside the repo.
  - DuckDB's `levenshtein`/`damerau_levenshtein` count UTF-8 bytes, not letters: `damerau_levenshtein('ö','o')` is 2 and `('şengün','sengun')` 4 (measured with the rapidfuzz switch, which agrees with DuckDB on all 142,880 ASCII word pairs tried). The entity index measures with them (entities.py:347, :397, :1400); where an accented string reaches them unfolded it is charged double - not checked whether any does.
- **User sees:** maintenance - a vocabulary fixed in one copy and not the others, or a grammar row deleted with no repo test failing.
- **Next step:** one table per concept (months, ordinals, number words) imported everywhere - the span and window slices gave the lexicon the season and count tables, the games' cuts slice takes the months; a parametrized `parent_intent` table with a meta-assert that every row is hit, watched to fail. (The fold's translate table and the two comments landed 2026-10-09.)
- **Source:** ours.
- **GitHub:** #244

### Two decisions share the label "subject kind": a streak's `kind` slot is recorded as the subject's kind
- **Found:** 2026-09-27, the step (b) audit agent, reading day10 traces.
- **Evidence:** `subject._apply_child_intent` records every settled slot as `Decision("subject", key, ...)`, and a streak's `kind` slot ("win"/"loss") is one of them. On day10's "what was the sixers longest winstreak this year?" the trace holds `subject kind: 'team'` and then `subject kind: 'win' (read for streak)`. The web page's decisions fold shows both, and any reader keyed on (stage, field) takes the wrong one; `~/association-research/parser-greenfield/measure.py` had to filter by `SUBJECT_KINDS`.
- **User sees:** two contradictory "kind" lines in the decisions fold of a streak answer.
- **Next step:** give slot rewrites their own field prefix or stage ("slot", key), and keep "kind" for the subject alone.
- **Source:** ours.
- **GitHub:** #234

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
  uses in `test_readers.py`.
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

### Numbers an answer states that are only in its text, so no comparison of values can see them move
- **Found:** 2026-09-30, the review of `ROADMAP-TYPES.md` (an Opus agent over the stage snapshot of the 628 recorded questions at `7f6425b`); the answers quoted were re-read from the snapshot.
- **Evidence:** the shot chart's made/attempted ("10/22 made"); a single game's NetPoints possessions and win probability added; a career's true-shooting attempts; a matchup's "met 27 times in all, 4 of them with ..."; the compiled team total's playoff addendum ("78 more over a 7-game playoff run", `compose/team.py:297`). None is in `data`, so `scripts/stage_snapshots.py compare --values-only` would pass a rewording that dropped or changed one. Also in the snapshot: a combined line is carried twice in the nine multi-line questions (`stat`/`threshold` say `assists`/20 for "20+ point 5+ assist" while `above` holds both lines) - harmless today, a trap when the five carriers of a line become one type.
- **User sees:** nothing today.
- **Next step:** each moves into `data` (then the Result) before the Phase 2 slice that rewords its answer; `ROADMAP-TYPES.md` lists them.
- **GitHub:** #309

### Typed remarks: what the wrapping left uneven
- **Found:** 2026-10-01, closing ROADMAP Phase 0 (a kind on every remark); the five agents' reports.
- **Evidence:** (1) one withheld-stat remark has two wordings ("is not counted from a rebuilt line", `templates/players.py:258` when found; "is not read from a rebuilt line", `players.py:1779`) and the second carries `label` with no `stat` key, since only the label reaches it from `compose/present.py:531` (deleted 2026-10-05). (2) "so whether he played is unknown" is said with two or more teammates (`compose.say`'s with/without sentence; `templates/splits.py:1007` when found). (3) `_team_outlook_missing`'s hint always says "ask about the regular season", whichever snapshot holds the team (`compose.team_stats._team_outlook_missing_notes` and `compose.say`; `templates/teams.py:1829` when found). (4) `templates/games.py:1286` and `:1414` (gone with `templates/`) bound a local named `note`, which shadows the import: a later wrap inside either function raises `UnboundLocalError` on that path alone. (5) `floor` is used for "Covers his whole career on record" (`compose.say`, "Covers his whole ... career on record"; `templates/shots.py:452` when found), which says nothing was clipped. (6) a period ranking's minimum records why it was halved and not of what (`games.py:1952-1975`: `most` is only in the sentence). (7) NetPoints' `data["notes"]` restate three facts in other words than the text (`templates/netpoints.py:211-217` against 337, 355, 372 when found; `compose/netpoints.py` and `compose.say`'s NetPoints sayer now); only the text side is recorded. (8) the corpus reaches about half of the 98 writers; the rest are checked by the suite running them and by the source-reading test, not by a recorded answer.
- **Re-measured 2026-10-09:** (1) and (4) went with Phase 2's one phrase per kind and the deletion of `templates/`; (2) is fixed - "whether they played is unknown" with several teammates (`compose.say`, 3 feed answers moved); (3) is moot - a regular-season question only a postseason snapshot holds takes the substitution path with its own note (`postseason_substitute`), so the hint is reached only where a regular-season snapshot holds the team, and "ask about the regular season" is right there. What remains is (5), (6), (7) and (8).
- **User sees:** a wrong pronoun in (2), a possibly wrong hint in (3); otherwise nothing.
- **Next step:** each goes when its kind gets its one phrase (`ROADMAP.md`, Phase 2); rename the two locals in (4) with the next change to that file.
- **GitHub:** #313

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

### "diabate last 20 game" lost its "of 73" since `9da65c0`
- **Found:** 2026-10-11, the Phase 3 review (`~/association-research/reviews/phase3-2026-10-11/`, finding 11).
- **Evidence:** "Moussa Diabate, last 20 of 73 games of the 2026 regular season" on `2ae0be8` became "Moussa Diabate, last 20 games of the 2026 regular season" on `9da65c0` (and "last 10 game" likewise): on the window-mark path, where the log row reads the typed window and not its own "last N games" words, the log's total left the heading. Among the ten enumerated moves of `9da65c0` (`CHANGES.md`).
- **User sees:** a heading without the total that the fuller wording ("last 20 games") still gives.
- **Next step:** check what the log says its total from on a window read without "games" (`compose.logs`, the window note), and say it on both paths.
- **Priority:** P4 - a heading's count.
- **GitHub:** #384

### Two closed sets only the tests' slot door checked, and door slots no caller passes
- **Found:** 2026-10-12, Phase 4, step 0 (the test doors deleted, #385).
- **Evidence:** `tests/slots.py` checked two values no typed value checks: the split against `reading.Split`'s closed set (`_one_of`) and a calendar day against `YYYY-MM-DD` (`_iso_day`). `Filtered` has no `__post_init__` and `Cuts` checks none of its fields, so a hand-built `Reading(split="by_weekday")` reaches the splits reader and raises `KeyError: 'by_weekday'` there, and `Cuts(date="April 12")` reaches the game log and raises `ValueError: Invalid isoformat string` - loud, not silent, and unreachable from the words (the split tagger and the cuts tagger write only valid values: 0 of the 2,710 readings hold another). The two door tests that held these (`test_an_unknown_split_is_refused`, `test_a_malformed_date_never_reaches_the_game_log`) went with the door. Beside them: `reading.Measure.from_slots` reads seven slots and its two callers (`parse.py:403`, `subject.py:1396`) pass `stat` alone, and `reading.Subject.from_slots` reads `teams`, which `parse.py` never passes - the rest were the door's; `measure.column_of`, which only the door called, was deleted with it.
- **User sees:** nothing - a reader's error on a value no reading writes.
- **Next step:** check `split` in a `Filtered.__post_init__` (the Reading's chains to it) and `date` in `Cuts.__post_init__`, raising `ScopeError` as every other typed value does; narrow the two `from_slots` doors to what their callers pass, or replace `Measure.from_slots({"stat": s})` with a `Measure` built from `measure.key_of(s)`.
- **Priority:** P4 - no reachable answer moves.
- **GitHub:** #395

### The web UI screenshot in the docs and the README still shows the retired "template" badge
- **Found:** 2026-10-12, Phase 4, step 7 (the documents): `docs/_static/web_ui_example.png`, used by `docs/usage.rst` and `README.md`, was taken before step 1 keyed the page's renderers by the shape, so its header reads "template leaderboard" where the page now says "answered · ranking by player, player seasons". The caption says so since step 7.
- **Evidence:** the badge text in `src/association/web/static/index.html` (the meta line names the shape since `c7e0f20`); a no-model preview render of the page shows "server unreachable" in its header, so the screenshot cannot be retaken offline.
- **User sees:** a documentation image a step behind the page; nothing in an answer.
- **Next step:** retake the screenshot against a live `association web` server (headless Chromium, as the other `docs/_static` shots were made), and `docs/_static/web_chart_example.png`/`web_chart_dark_example.png` beside it if their headers carry the badge too; drop the caption's note in the same commit.
- **Source:** ours.
- **Priority:** P4 - a documentation image.
- **GitHub:** #399

### `CHANGES.md` gives two outcomes for the nine feed questions two step 6 lanes both moved
- **Found:** 2026-10-14, the Phase 5 review, checking every merge composes its parents three-way.
- **Evidence:** nine feed questions were moved by lane N's subject-reading commits and by lane L's ranking commits in the same step; each lane's `CHANGES.md` entry names the outcome on its own tree, and the merged tree's answer is the reading-side lane's (right), which neither entry nor the merges `0143de96` and `b9ac210b` record.
- **User sees:** nothing; a reader of the changelog finds two outcomes for one question.
- **Next step:** a note under the two entries naming the nine and the merged outcome (the lead, at the phase's close), and the merge rule that a both-moved question is named in the merge commit.
- **Priority:** P4 - the changelog.
- **Source:** ours.
- **GitHub:** #451

### Two commit messages' counts are off by one: `bd5a9ae` says "the same 6" readings (2 moved), `95cb7bf` "0 of the 628" (1 answer moved)
- **Found:** 2026-10-14, the Phase 5 review, bisecting every move to its commit.
- **Evidence:** both commits are pushed; the moves are in the review's data (`reviews/phase5-2026-10-14/data/`) and in `CHANGES.md` where the later commit named the earlier's move.
- **User sees:** nothing.
- **Next step:** recorded here and in ROADMAP-HISTORY entry 47; a pushed message is not rewritten.
- **Priority:** P4 - the record.
- **Source:** ours.
- **GitHub:** #452


### `check_web_ui.py`'s documented command fails on a playwright newer than the installed browser
- **Found:** 2026-10-15, the p6-comments lane, checking the web page after `GET /api/health`'s `models` key was renamed.
- **Evidence:** AGENTS.md ("Working on the web path") says to run `uv run --with playwright python scripts/check_web_ui.py`; `--with playwright` resolves the newest release (1.64.0 that day), whose Chromium headless shell is revision 1248, while `~/.cache/ms-playwright` on the devbox holds 1243 - the run dies in `BrowserType.launch` ("Executable doesn't exist"). `uv run --with playwright==1.63.0 python scripts/check_web_ui.py` (revision 1243) passes 39 of 39. The check also never reads `models` from the health response, so a renamed key there is checked by `tests/web/test_app.py` alone.
- **User sees:** nothing; an agent following AGENTS.md gets a crash instead of the page check, and a later playwright release breaks it again.
- **Next step:** pin the version in AGENTS.md's command (or in a `scripts/check_web_ui.sh` wrapper) to the one whose browser the box holds, or run `playwright install chromium-headless-shell` as a documented step; have the stub health response carry `models` and assert the indicator's title.
- **Source:** ours.
- **Priority:** P4 - tooling.
- **GitHub:** #459

### `--model` names the normalizer's model now, and a pre-5.0 script passing it picks the normalizer
- **Found:** 2026-10-15, the p6-comments lane (the 7.0.0 rename of `--router-model` to `--model`, on Jeff's instruction).
- **Evidence:** `--model` was the fall-through agent's 7B option until 5.0.0, and `tests/cli/test_commands.py`'s stale-option test held that passing it fails loudly ("No such option"); since the rename it is accepted, so a script written for 4.x that passes `--model qwen2.5:7b` now runs the normalizer on the 7B - slower (1.84s against 0.82s a question, `query/models.py`) and measured to add nothing - with no error. The test's list drops `--model`.
- **User sees:** a slower normalizer, only for a script four majors old.
- **Next step:** none needed unless such a script turns up; `CHANGES.md`'s 7.0.0 entry names the reuse.
- **Source:** ours.
- **Priority:** P4 - low impact.
- **GitHub:** #460

