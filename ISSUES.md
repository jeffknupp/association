# Open issues

Everything known to be wrong, missing or unverified that still needs follow-up,
ranked by what a user would see. `AGENTS.md` ("Recording findings") says when
to add an entry and how. The short version: **record every finding, including
the ones that are not part of your task, and delete an entry in the same commit
that fixes it**, saying in `CHANGES.md` which commit fixed it.

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

## P1: wrong answer

### Every Bulls and Pelicans box score from 2013 to 2018 is zeros
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
  - **The source.** The raw Parquet has the same zeros, written 2026-09-08. The
    parser only writes 0 when ESPN sends "0", so ESPN apparently served zeroed
    lines. That is inferred, not checked against the live source.
  - **What survived.** Plays and shots exist for 977 of the 978 events
    (400828893 has neither), at normal density. `player_season_stats` and
    `team_season_stats` are unaffected: Anthony Davis has 1,656 points in 2015
    there and 0 in his box scores.
  - **The caveat undersells it.** `_empty_box_scores` caveats count the games;
    they do not say "every Bulls and Pelicans game".
- **User sees:** zeros read as real games for whole seasons of Anthony Davis,
  Jimmy Butler and Derrick Rose, in game logs, single-game highs, threshold
  counts, streaks, splits and with/without answers. For example: "Anthony
  Davis game log 2015", "Butler 30-point games 2017". Box-score points are
  86.5-87.3% of season totals across 2013-2018.
- **Next step:** refetch 400828004, 400459778 (a CHI playoff game) and 400975625
  with `--force`. If ESPN now returns stats, refetch all 1,025 events. If not,
  rebuild the box lines from `plays` (everything except minutes and
  plus-minus), and caveat by team until then. Either way, backfill through the
  real fetch and load path ("Working on the fetch path" in `AGENTS.md`).

### Surname lookups rank alphabetically and cap at ten
- **Found:** 2026-09-11, final corpus run; widened in the follow-up session, and
  again while narrowing `threshold_count` by season
- **Evidence:** "how did curry do against the celtics this year" answers "did you
  mean Dell Curry, Eddy Curry, JamesOn Curry, Michael Curry or Seth Curry (1
  others also match)?". Stephen Curry is the one cut off. `find_players` returns
  the first 10 matches alphabetically, 71 name words match more than 10
  players, and "Davis" drew Anthony Davis's chart because of the alphabet.
  - **Narrowing the truncated list picks a player.** `resolve_chart_player`
    narrows those first 10 to the ones with rows in the season and draws a lone
    survivor, but the player meant can be past the cut. Comparing
    `narrow_to_available` over `find_players` at `MAX_CANDIDATES = 10` and
    unbounded, over the 71 words and every season: one player is drawn where
    several matching players have rows in 390 (word, season) pairs for
    `shot_chart` and 122 for `net_points_player_fingerprint`. "Williams" draws
    Alan Williams's 2016 shot chart (8 have one) and Alondes Williams's 2023
    fingerprint (14 have one).
- **User sees:** a clarification that hides the player meant, and in the chart
  path a chart of the wrong player: "Williams shot chart 2016" draws Alan
  Williams.
- **Next step:** **fix in progress** in worktree `peaceful-goldberg-c807ba`. It is
  staged, not committed, on `5daa007`, and must be rebased on master, which is 20
  commits ahead. `threshold_count` now narrows by season, and only when fewer
  than `MAX_CANDIDATES` match (`templates._resolved_player`), so a name at the
  cap is still asked about and still cut alphabetically. The chart path needs
  the same guard, or `find_players` has to stop truncating before anything
  narrows it. `single_game_high` and `streak` still do not narrow by season.

### Per-game leaderboards for points, rebounds and assists apply no games minimum
- **Found:** 2026-09-11, template work (agent D); measured in the issues audit
- **Evidence:** `avg_points` and the other original per-game metrics set
  `min_sample_column="gamesPlayed"` with no `default_min_sample`
  (`query/metrics.py`). `leaderboard.default_min_sample()` therefore applies
  none in either season type. The newer per-game metrics use
  `PER_GAME_MIN_GAMES` (20) and `PER_GAME_MIN_POSTSEASON_GAMES` (5). Leaders
  below those floors in `player_season_stats_deduped`, 1994 onward:
  - **Regular season:** rebounds in 2000 (Dennis Rodman, 12 games, 14.3) and
    2001 (Danny Fortson, 6 games, 16.3; Dikembe Mutombo led).
  - **Postseason:** 15 season-stat leaders. Kawhi Leonard leads 2023 scoring
    on 2 games (34.5), and Rod Strickland leads 1995 assists on 3.
- **User sees:** "who led the league in rebounding in 2001" answers Danny
  Fortson, and "who led the 2023 playoffs in scoring" answers Kawhi Leonard.
  No qualifier is stated, because none was applied.
- **Next step:** give the original per-game metrics the same
  `PER_GAME_MIN_GAMES`/`PER_GAME_MIN_POSTSEASON_GAMES` the newer ones use. Add
  a test in the Fortson shape.

### The router drops a named player from a single-game high
- **Found:** 2026-09-11, while fixing name clarification (reported, not re-verified)
- **Evidence:** "most points curry scored in a game this season" arrived with no
  `player` slot. `restore_dropped_players` (`query/entities.py`) restores a
  dropped name for `fingerprint` only.
- **User sees:** a league-wide answer to a question about one player: Bam
  Adebayo's season high, not Curry's.
- **Next step:** add the question to `scripts/check_routing.py` and reproduce it.
  Then restore a name the question holds for `single_game_high`, under the
  exact-count rule `override_invented_players` uses.

### "without X and Y" drops the second player
- **Found:** 2026-09-11, template work (agent C); verified in the issues audit
- **Evidence:** `router._name_after(_WITHOUT, ...)` returns `'Tatum'` for
  "Celtics record without Tatum and Brown", and `'Lebron'` for "Lakers record
  without Lebron and AD this season".
- **User sees:** a with/without answer about one absent player, presented as the
  answer to a question about two. A real feed question of this shape: "hornets
  record when brandon miller and lamelo and knueppel play this year".
- **Next step:** refuse when the phrase holds "and" or a comma (small and
  honest), or read every name and require all of them.

### 246 season lines have NULL totals
- **Found:** 2026-09-11, issues audit
- **Evidence:** in `player_season_stats`, 104 regular-season rows (42 players)
  and 142 postseason rows (65 players) have every counting total NULL. In 98
  of the regular-season rows (37 players), every `avg*` column is still filled.
  The other 6 are the all-NULL combined rows listed in the traded-players entry.
  Example: Seth Curry 2022, `avgPoints` 15.0 and `points` NULL. The same
  players recur:
  - Seth Curry: 2014-2017 and 2019-2026, 18 rows, and no 2018 row at all.
  - Lou Amundson: 2007-2016.
  - David Wood: 1989-1997.
  - Postseason lines for Nazr Mohammed (12 rows), Zach Randolph (9), Theo
    Ratliff (8) and Gabe Vincent (7).

  The parser merges ESPN's averages and totals
  categories on (season, teamId), and for these keys the totals category
  contributed nothing (`fetch/parse.py`).
- **User sees:** totals leaderboards and career sums silently drop these
  seasons. Seth Curry's career points lose every season from 2014 on.
- **Next step:** refetch athlete 2326307's career stats through the pipeline. If
  the totals are still missing, fill them from `avg × gamesPlayed` at load and
  say so.

### The 2000 and 2001 playoffs stop before the Finals
- **Found:** 2026-09-11, template work (agent B); characterized in the issues audit
- **Evidence:** postseason games are checked against `team_season_stats`
  `gamesPlayed`. 2002-2026 match for every team.
  - **2000:** there is nothing after 2000-06-01. Missing are the whole LAL-IND
    Final (6 games), WCF LAL-POR games 6-7 and ECF IND-NY game 6. The Lakers
    have 15 of 23 games.
  - **2001:** there is nothing after 2001-05-28. Missing are the Final (5), ECF
    MIL-PHI games 5-7, WCF LAL-SA game 4 and 2 games of MIL-CHA. The Lakers
    have 10 of 16.
  - **The 2000 standings share these gaps.** For all 29 teams, their W-L equals
    the W-L counted from `games`, while `team_season_stats` has 82 games for
    each. The Lakers are 67-13, against a real 67-15.
  - **Nothing catches it.** `coverage.py` declares no partial season, and
    `check_coverage.py` passes because it compares against a share of the
    median season.
- **User sees:** no 2000 or 2001 Finals, and "LAL-POR 2000" read as a 3-2 series.
  Playoff records are short for the teams that went deepest. All of it is
  stated as fact, with no caveat. Only `team_record` notices, through
  `_game_list_gaps`.
- **Next step:** re-discover the events for 2000-06-02 to 06-19 and 2001-05-27 to
  06-15, by date or from the finalists' schedules. Until then, add `partial=`
  caveats to `COVERAGE` for these two postseasons.

### `games` holds placeholder, duplicate and phantom rows that templates count
- **Found:** 2026-09-11, template work (agent B), the repo audit and the issues audit
- **Evidence:**
  - **Placeholders:** 134 regular-season rows with no score and no winner
    (1999: 50, 2000: 82, 2001: 1, 2002: 1), and 133 of them involve Chicago.
    Most are stamped 16:00Z or 17:00Z beside the real game, e.g. `400216711`,
    1999-02-05 UTAH v CHI 0-0, next to `190205026`. Two have no real game
    within a day: `400218915` (2000-04-18 CHI v PHI) and `400218927`
    (2000-04-19 DET v CHI). They may sit where missing games belong; that is
    not yet checked against a schedule.
  - **Duplicates:** by Eastern date, among games with a winner, there are two.
    One is 2003-01-04 DAL-PHI 102-83, stored as both `230104006` and
    `400222658`. The other is the 2000 postseason TOR-NY copy below. An
    earlier count of 20 in 1999 and 13 in 2000 was mostly placeholder pairs.
  - **Phantoms that carry a winner:** they have date-only `T04:00Z` stamps and
    no box rows, and often a team id missing from `teams`:

    | Year | Events | Detail |
    |---|---|---|
    | 1994 | `131205075` | team 75 vs DAL |
    | 1995 | `150611014` | MIA-ORL; actually Houston's Finals Game 3, and MIA has no 1995 postseason |
    | 1997 | `170429031`, `170501031` | team 31 vs SEA |
    | 1998 | `171209083` | team 83 vs DEN |
    | 1999 | `190612021` | PHX-POR |
    | 2000 postseason | `200422100`, `200424100`, `200505100` | team 100 vs SEA |
    | 2000 postseason | `200501028` | a copy of TOR-NY |

    They make 1994 and 1998 one game long against `team_season_stats`, by
    exactly 225 and 175 points.
  - **Who filters what:**
    - `head_to_head` counts every one of these rows.
    - `team_metrics.TEAM_GAMES_SQL` drops placeholders and same-day duplicates
      but keeps phantoms that have a winner.
    - `conditions` filters only on `winner_team_id IS NOT NULL`.
    - The team `game_log` has no filter, but joins `team_box_stats`, which the
      phantoms lack.
- **User sees:** "how many times did the Mavs play the 76ers in 2003" counts one
  game twice, and 1999-2000 matchups count 0-0 "meetings" nobody won. Playoff
  records are inflated for ORL 1995, SEA 1997 and 2000, and PHX and POR 1999.
- **Next step:** at load, exclude rows with a team id not in `teams`, a
  date-only stamp and no box rows. Then build `head_to_head` and `conditions` on
  one shared filtered game list.

### 2018 team box scores have values under the wrong column names
- **Found:** 2026-09-11, template work (agent B); characterized in the issues audit
- **Evidence:**
  - **2018 misalignment:** every non-empty 2018 regular-season
    `team_box_stats` row (2,134) matches player-box sums like this. So do 144
    of the 146 postseason rows:

    | Column | Actually holds |
    |---|---|
    | `assists` | blocks |
    | `steals` | turnovers |
    | `blocks` | fouls |
    | `flagrantFouls` | steals |
    | `fieldGoalPct` | FT% |
    | `freeThrowPct` | about 3P% |

    Real team assists appear nowhere in the row, and no turnover or foul
    column is right either:
    - `turnovers` averages 0.58 a game.
    - `totalTurnovers` averages 1.18, and equals the player-box turnovers in 1
      row of 2,134.
    - `fouls` averages 0.04, against a real 19.99.

    FGM, FGA, 3PM, FTM and rebounds are right. 2017 and 2019 come from the same
    code with the same column order, so ESPN is the likely cause (unverified).
  - **1994-2012 turnovers:** `turnovers` is 0 in nearly every row (2,322-2,459
    rows a season in 2000-2011), and `teamTurnovers` copies `totalTurnovers`.
    Only `totalTurnovers` is usable.
  - **Season stats are fine:** `team_season_stats` is correct in both eras.
- **User sees:** team with/without and split tables for 2017-18 report blocks
  (about 4.8 a game) as assists, because `conditions._TEAM_LINE` reads
  `AVG(t.assists)`. Agent SQL over `team_box_stats` is wrong for 2018 stats and
  for turnovers before 2013.
- **Next step:** at load, rebuild the 2018 columns and the pre-2013 turnover
  columns from player-box sums. Refetch one 2018 event to confirm the cause.

### Traded players' combined season rows are wrong in 26 cases
- **Found:** 2026-09-11, template work (agent D); counted in the issues audit
- **Evidence:** of 2,062 combined rows (`team_id` NULL) in
  `player_season_stats`, 26 disagree with the sum of that season's stints:
  - **6 are all NULL:** Moses Malone 1977, James Edwards 1978 and 1983, Bill
    Laimbeer 1982, Danny Schayes 1983, and Sleepy Floyd 1983.
  - **12 from 1996 copy a single stint,** losing 3,042 points between them. Eric
    Murdock's row reads 9 games and 62 points, against stints totalling 73 and
    647.
  - **Jevon Carter 2023** drops a 1-game stint.
  - **7 have NULL points** because a stint's totals are NULL; see the
    NULL-totals entry above.
  
  `player_season_stats_deduped` (`fetch/warehouse.py`) and the leaderboard's
  `dedup_traded` both prefer the NULL-team row.
- **User sees:** "Eric Murdock 1995-96 stats" answers 9 games at 6.9 points a
  game. `player_compare` and `player_history` show the same wrong line.
- **Next step:** use the combined row only when it equals its stints' sum, and
  sum the stints otherwise. Add a warehouse test in the Murdock shape.

## P2: misleading or incomplete

### A named playoff round falls through to the agent, which has no better source
- **Found:** 2026-09-11, repo audit
- **Evidence:** `check_scope` raises on `round`, and `agent.py` then hands the
  question to the SQL agent, although `games` has no series or round column.
  This is the "nothing does better here" case where `check_coverage` returns a
  refusal instead.
- **User sees:** "tatum stats in the 2024 finals" takes 30-120 seconds, and the
  agent is free to answer for the whole postseason.
- **Next step:** return a refusal naming the missing round data, the way
  `_conference_refusal` does. Deriving rounds from series order is a separate
  P3 job.

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

### Shooting qualifiers are flat across shortened seasons
- **Found:** 2026-09-11, while qualifying true shooting and eFG% (`f66e1f1`)
- **Evidence:** the 550/480 floors assume an 82-game schedule. At 550
  true-shooting attempts, 2020 qualifies 157 players and 2021 qualifies 155,
  against 174-184 in 2019 and in 2022-2026. The 2012 lockout season (66 games)
  was not measured. Published rules scale per team game.
- **User sees:** fewer qualified players in short seasons. The qualifier is
  stated, but it is harsher than the published one.
- **Next step:** scale the floor per team game, and keep `min_sample_applied`
  honest about the scaled number.

### Smaller game and box-score gaps, 1994-2003
- **Found:** 2026-09-11, template work (agents A, D) and the issues audit
- **Evidence:**
  - **2000 regular season:** `games` holds 1,166 of 1,189 real games. 18 teams
    have 80 of their 82, 10 have 81, and LAC has all 82.
  - **Real regular-season games with no box score:** 5 in 1994, 5 in 1996, 6 in
    1997, 4 in 1998 and 4 in 2000. Almost all are road games at UTAH, CLE or
    WSH.
  - **Real postseason games with no box score:** the entire 1997 ECF CHI-MIA
    (`170520014`, `170522014`, `170524004`, `170526004`, `170528014`),
    `150614019` (1995 Finals), `160502025` (1996 SAC-SEA) and `230503026` (1998
    UTAH-HOU).
  - **2002 scores:** 20 points short across 6 teams, with equal game counts.
  - **NULL minutes in 2006-2012** mean the player did not appear. Dropping those
    rows raised 2009's games-played agreement from 30 to 378 of 445 players.
- **User sees:** small shortfalls, with no caveat, in box-derived answers for
  those seasons.
- **Next step:** refetch the listed events through the pipeline. Check that every
  box-derived template treats NULL minutes as "did not play".

### The 2026 shot chart holds more shots than the box score
- **Found:** 2026-09-11, shot-frame fix (shot agent)
- **Evidence:** 1,165 player-games, across the regular season and postseason,
  have more shots in `shot_chart` than in the box score, 1,207 extra in all.
  Curry has 488 threes against 484 3PA. Neither `plays` nor `shot_chart` holds
  a duplicate `play_id`. The extras look like end-of-period heaves: 1,141 of
  those player-games have extra 3PA, and they hold 1,084 shots taken with
  under a second on the clock. That is a correlation, not proven.
- **User sees:** shot charts and shot-distance answers count shots that are not
  in the box score.
- **Next step:** check whether box scores leave out buzzer heaves (a shot after
  the horn, or one ESPN logs but does not credit). If they do, filter the chart
  the same way.

### `with_without` undercounts a season-long absence
- **Found:** 2026-09-11, template work (agent C)
- **Evidence:** a teammate's stint is read from box-score rows, so a whole season
  without rows breaks it. Klay Thompson's 2020-21 and Kevin Durant's 2019-20
  with the Nets are not counted as games "without" him.
- **User sees:** "Warriors record without Klay" for 2020-21 comes back with no
  "without" games, or too few.
- **Next step:** read roster tenure from `player_season_stats` team rows, not
  from box-score presence.

### Historical teams are shown under today's names
- **Found:** 2026-09-11, template work (agent A)
- **Evidence:** `teams` holds only the 30 current teams, so the 1993 Charlotte
  Hornets are labelled "New Orleans Pelicans".
- **User sees:** a 1990s game log or matchup naming a franchise by its current
  name.
- **Next step:** build a per-season team-name table from each game's own team
  names, and use it wherever a historical game is printed.

### A retired player's question defaults to the current season
- **Found:** 2026-09-08 (reported, not re-verified)
- **Evidence:** "The Answer's avg points" answers "Allen Iverson has no 2026
  regular season numbers".
- **User sees:** a refusal that is true but beside the point: the question meant
  his career.
- **Next step:** when a player has no rows in the defaulted season, answer their
  last season or career, and say so.

### `player_history` answers "last N seasons on record", not a calendar window
- **Found:** 2026-09-11, while fixing name clarification
- **Evidence:** a player with gaps (the Dell Curry example) gets their last N
  seasons played, labelled as if they were consecutive.
- **User sees:** "last 5 seasons" spanning more than five years, with no note.
- **Next step:** state the actual seasons covered in the answer.

### Two fingerprints asked for without "vs" or "compare" draw one
- **Found:** documented in `AGENTS.md` as an accepted cost; listed by the repo audit
- **Evidence:** `restore_dropped_players` acts only on a comparison word, and
  `compared_but_unmatched` only on "vs".
- **User sees:** "plot jokic and embiid fingerprints" draws one polygon, with no
  note that a second name was dropped.
- **Next step:** when two players are named and only one is drawn, say so,
  without restoring the second.

## P3: refusal or gap

### Four question filters are recognized but no template answers them
- **Found:** 2026-09-11, template work and final corpus run
- **Evidence:** `SCOPING_SLOTS` against `HONORED_SCOPING` (`query/templates.py`):
  - `since`/`until`: "most 3 pointers made since 2020";
  - `below`: "Sga games with under 14 fta";
  - `situation`: "Celtics record on back to backs", overtime, by month;
  - `round`: "tatum stats in the 2024 finals".
  
  All are derivable from existing tables. Of 45 real questions, 6 still fall
  through (`fastpath_r3.jsonl`), and three of those are these filters.
- **User sees:** every such question goes to the slow agent.
- **Next step:** first `since` for `leaderboard`/`threshold_count`, reusing the
  career-span code. Then `situation` for `team_record`: back-to-backs need the
  Eastern date (`season.eastern_date`).

### A player's stats by quarter or half
- **Found:** 2026-09-11, query-shape research (10% of the StatMuse feed)
- **Evidence:** `_AGENT_ONLY` (`query/router.py`) forces these to `other` by
  design. Points rebuilt from `plays` match the box score for 99.9% of
  player-games in 2010, but only 96.5% in 2026. One 2026 game gave a player 24
  points against a box score of 16, so play ordering (`play_id` as a bigint, and
  `LAG()`) needs care.
- **User sees:** "rj barrett 4th qtr log" always goes to the agent.
- **Next step:** materialize per-player, per-period points at warehouse build,
  reconcile them against the box score, then add a template with a 2003 floor.

### No conference or division data
- **Found:** 2026-09-11, template work (agent B) and repo audit
- **Evidence:** `games.conference_game` is False on all 43,494 rows
  (`fetch/parse.py` reads `conferenceCompetition`). No table maps a team to a
  conference, so `_conference_refusal` refuses "Western Conference standings" by
  name.
- **User sees:** a refusal for conference standings, or "who leads the East".
- **Next step:** a static team-to-conference table, per season.

### A player's career TS% is refused
- **Found:** 2026-09-11, final corpus run
- **Evidence:** "kevin durant true shooting percentage career" routes to
  `player_stat` with `stat='ts_pct'`, which refuses. It is derivable from
  career totals: PTS / (2 × (FGA + 0.44 FTA)).
- **User sees:** a fall-through to the agent. The wrong 3P% answer this used to
  give is fixed.
- **Next step:** add TS% and eFG% to `player_stat` as computed ratios, like
  `SHOOTING_STATS`.

### The power index (BPI) keeps one snapshot per season
- **Found:** 2026-09-11, template work (agent B) and repo audit
- **Evidence:** `team_power_index` has 25 rows in each season 2017-2026: only the
  play-in and postseason teams. `fetch_power_index` (`fetch/pipeline.py`)
  overwrites one file per season, and 2017-2021 pair BPI values with final
  records.
- **User sees:** `team_outlook` has nothing for most teams in past seasons.
- **Next step:** write dated snapshots going forward. Past seasons cannot be
  recovered.

### A games minimum cannot be given to the agent's TS%/eFG% leaderboard tool
- **Found:** 2026-09-11, while qualifying true shooting and eFG% (`f66e1f1`)
- **Evidence:** `get_leaderboard(metric="ts_pct", min_sample=50)` now means 50
  attempts, not 50 games. The result carries `min_sample_column`, but a
  games-based minimum can no longer be expressed through the tool for these two
  metrics. `TABLE_SUMMARY` does not list the two new view columns (left out for
  the preamble budget), so the agent has to `describe_table` to find them.
- **User sees:** "best true shooting among players with 50 games" makes the agent
  write SQL, more slowly.
- **Next step:** accept a `min_games` alongside `min_sample` in the tool, if the
  budget allows.

### Fingerprint for a specific date
- **Found:** before 2026-09-11 (docstring)
- **Evidence:** the fingerprint template in `query/templates.py` says "... but not
  yet for a particular date". `game_log` already honors `date`.
- **User sees:** a helpful refusal.
- **Next step:** resolve the date to the player's game with `_eastern_day`, then
  draw the single-game fingerprint.

### Franchise career leaderboards
- **Found:** before 2026-09-11 (docstring)
- **Evidence:** the leaderboard in `query/templates.py` says "Refused until that
  is decided". The rule for relocated franchises is open.
- **User sees:** a refusal for "timberwolves career leaders in total points".
- **Next step:** decide the relocation rule, then map it.

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

### Two players against one team has no template
- **Found:** 2026-09-11, while making `opponent` refuse or narrow
- **Evidence:** `player_compare` and `player_matchup` read season lines and
  honor no `opponent` (`HONORED_SCOPING`, `query/templates.py`), so "compare
  curry and lebron vs the celtics" refuses on the template path and falls
  through (1 run through the fast path). Before the fix that moved the
  Celtics out of `team`, it compared the two players' whole 2026 seasons.
  `_narrow_player_games` already builds one player's box-score line against
  one opponent for `player_stat`. The question was constructed while testing,
  not seen in the StatMuse feed.
- **User sees:** a fall-through to the agent. What the agent answers was not
  measured (it needs the agent model).
- **Next step:** let `player_compare` honor `opponent` by building each
  player's line through `_narrow_player_games`.

## P4: tooling, docs, low impact

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

### Data commands and check scripts default to paths a worktree does not have
- **Found:** 2026-09-11, routing check, then repo audit
- **Evidence:** `--db-path` defaults to `./nba.duckdb`, and `--data-dir` to
  `./data/parquet`. The affected commands are `association data pull`/`load`
  (`cli.py`) and the scripts `check_routing.py`, `check_coverage.py`,
  `check_nicknames.py` and `check_net_points_games.py`. In a worktree, the
  check scripts fail with "database does not exist".
- **User sees:** nothing. An agent loses a run, or runs a backfill against the
  wrong files.
- **Next step:** default to the main checkout's files, found through
  `git rev-parse --git-common-dir`.

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

### `CHANGES.md` has two `## Unreleased` headings
- **Found:** 2026-09-11, repo audit
- **Evidence:** the second heading holds entries committed two minutes before
  Release 1.5.0, whose bump renamed only the first heading.
  `scripts/bump_version.py` replaces with `count=1` and does not warn.
- **User sees:** an "Unreleased" section in the middle of the published
  changelog.
- **Next step:** fold the orphaned entries into 1.5.0, and make the script refuse
  when there is more than one heading.

### A fresh worktree cannot run the gates with `uv run` alone
- **Found:** 2026-09-11, while qualifying true shooting and eFG% (`f66e1f1`)
- **Evidence:** `uv run` creates the worktree's venv without the `dev` extra, so
  `uv run pytest -q` fails with `Failed to spawn: pytest` until
  `uv sync --frozen --extra dev --extra docs --extra web` (CI's line) has run.
- **User sees:** nothing. An agent loses time.
- **Next step:** add the sync line to "Before you commit" in `AGENTS.md`.

### Three wrong statements in the docs
- **Found:** 2026-09-11, repo audit and issues audit
- **Evidence:**
  - `AGENTS.md` ("Data gotchas") gives `player_season_stats` "5 players in 1977,
    240 in 1988, 668 in 1994". Those are row counts across season types; the
    distinct regular-season players are 2, 141 and 403, as in `coverage.py`.
  - The comment above `TURNOVERS` in `query/team_metrics.py` says pre-2013
    `team_season_stats.turnovers` is "the player turnovers alone". It already
    includes team turnovers: it equals the box `totalTurnovers` for 26-29 of 30
    teams. The expression is right; only the comment is wrong.
  - `AGENTS.md` and `fetch/warehouse.py` say the deduplicated view drops "340 of
    7,845" postseason rows. Those are player-seasons. Counted in rows, it is 436
    of 7,941.
- **User sees:** nothing. An agent reads wrong facts.
- **Next step:** correct all three.

### Broad `except duckdb.Error` in `_single_game_netpoints`
- **Found:** 2026-09-11, repo audit
- **Evidence:** `_single_game_netpoints` in `query/templates.py` catches every
  DuckDB error. `fingerprint.py` already narrowed the same pattern to the
  missing-table error.
- **User sees:** a SQL bug reported as "unavailable", then a slow fall-through.
- **Next step:** catch `duckdb.CatalogException` only.

### Postseason shooting floors are scaled, not calibrated
- **Found:** 2026-09-11, while qualifying true shooting and eFG% (`f66e1f1`)
- **Evidence:** `ts_pct` 67 and `efg_pct` 59 are the season floors times 10/82.
  No published postseason list applies a qualifier (StatMuse's 2025 playoff
  leader shot 150% on two attempts), so there was nothing to check them against.
  They leave 81-92 qualified players per postseason in 2025 and 2026.
- **User sees:** a stated but uncalibrated postseason qualifier.
- **Next step:** none until a published postseason rule is found.

### Basketball-Reference's qualifying rule was never read directly
- **Found:** 2026-09-11, while qualifying true shooting and eFG% (`f66e1f1`)
- **Evidence:** Basketball-Reference answers 403 to automated fetches, including
  `/about/rate_stat_req.html`. The floors were checked against StatMuse (725
  points; 300 made field goals).
- **User sees:** nothing.
- **Next step:** if a modern true-shooting-attempts figure is published there,
  compare it with 550.

### 14 of 38 API doc pages print a literal `:rtype:` line
- **Found:** 2026-09-11 (reported, not re-verified)
- **Evidence:** seen in the built HTML by the name-clarification session.
- **User sees:** stray markup in the published API docs.
- **Next step:** build the docs and grep the HTML for `:rtype:`.

### A failed warehouse build leaves no marker
- **Found:** 2026-09-08 (reported)
- **Evidence:** each table load is its own statement, so an out-of-memory kill
  leaves the earlier tables replaced and the rest at their old contents
  (`AGENTS.md`, fetch path).
- **User sees:** answers from a half-updated warehouse, with nothing saying so.
- **Next step:** record a build-complete marker, and have `data check` report a
  build that did not finish.

### Tied leaderboard rows were seen shuffling between runs
- **Found:** 2026-09-11 (reported, not re-verified)
- **Evidence:** `query/leaderboard.py` now breaks ties by `display_name`, so the
  source of the shuffle is not located.
- **User sees:** the same question listing tied players in a different order.
- **Next step:** reproduce it with a tied metric before changing anything.

### The agent can return an empty answer
- **Found:** 2026-09-08, pre-existing in 1.6.0 (reported, not re-verified)
- **Evidence:** seen in session notes and not reproduced since.
- **User sees:** a blank answer after a long wait.
- **Next step:** reproduce it, then have the agent loop treat an empty final
  message as a failure.

### Columns that look wrong but that nothing reads
- **Found:** 2026-09-11, template and shot work; `dnp_reason` widened while
  making `opponent` refuse or narrow
- **Evidence:**
  - `dnp_reason` is set on 382,435 box rows where the player played, about a
    third of `player_box_stats`: 382,378 of them "COACH'S DECISION", at 22.7
    minutes on average, in every season from 2013 to 2026 (24,000-28,600 a
    season). 13,160 of 2026's are starters. Only `fetch/parse.py` touches the
    column. `did_not_play` is the field that says whether a player sat.
  - `plusMinus` is NULL on 14.3% of box rows. That is exactly the rows with
    NULL minutes, not a random gap.
  - `team_season_stats.plusMinus` is a -1.0 placeholder.
  - `largestLead` is filled on 47,480 of 83,261 non-empty team box rows, and
    `leadChanges` on 2,018.
  - `net_points_team` holds 2026 only, which is inherent to the source.
- **User sees:** nothing today. Any template that starts reading these would.
- **Next step:** measure each one before a template reads it.

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

### Shots past half court are counted but drawn off the canvas
- **Found:** 2026-09-11, shot-frame fix
- **Evidence:** the shot chart's subtitle counts heaves that the half-court plot
  does not show.
- **User sees:** a subtitle count one or two higher than the number of dots.
- **Next step:** clamp heaves to the edge of the plot, or note them in the
  subtitle.

### A slow agent answer cannot be cancelled
- **Found:** before 2026-09-11 (`web/app.py` comment, `roadmap-2.0.md`)
- **Evidence:** closing the tab does not stop the inference. The planned fix, a
  cancelled flag checked between tool calls, is not built.
- **User sees:** the next question waits behind an abandoned one.
- **Next step:** build the flag.

### The agent's tool budget is full
- **Found:** before 2026-09-11 (`docs/architecture.rst`, "The tool budget")
- **Evidence:** five tools leave about 220 tokens of headroom, so a sixth does
  not fit. The cheapest lever, folding `render_shot_chart` and
  `render_fingerprint` into one tool, is not done.
- **User sees:** nothing yet. It blocks any new agent tool.
- **Next step:** fold the two render tools when a new tool is next needed.

### The PyPI upload fails
- **Found:** documented in `AGENTS.md` ("Releasing")
- **Evidence:** trusted publishing answers `invalid-publisher`, pending an
  account-access issue. It is not a workflow bug.
- **User sees:** releases only on GitHub.
- **Next step:** register the publisher once the account is back, then upload
  each tagged version.
