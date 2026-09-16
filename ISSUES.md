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

## P1: wrong answer

*None open.* Emptied 2026-09-16, when the last entry here - a narrowing the
router has no slot for being dropped and the rest answered - was fixed for
every case the 261-query replay measures and re-ranked to P3, where what
remains of it is recorded. An empty section is a statement about what has been
*measured*, not a claim that nothing answers falsely: the replay is one
261-query sample of one feed, it still grades 29 answers (11%) as fluently
wrong for other reasons, and each of those is an entry below.

## P2: misleading or incomplete

### Shooting leaderboards silently drop 2013-2018 qualifiers
- **Found:** 2026-09-15, issues audit (P2 query auditor)
- **Evidence:** `player_season_advanced_stats` is built from the raw
  `player_box_stats`, so every Chicago and New Orleans player-season from
  2013-2018 has 0 true-shooting attempts there (Anthony Davis 2015: TSA 0.0,
  `ts_pct` NULL) even though `player_box_stats_filled` now rebuilds those
  games. 21-33 player-seasons a season (0 in 2012 and 2019) clear 550 TSA in
  `player_season_stats_deduped` but fall under it in the advanced table.
  - The 2015 TS% board says "Kyle Korver led ... at 0.69". Tyson Chandler has
    553.1 TSA and .697 by the season table against 521.6 in the advanced one,
    so under our own rule he would lead it. Rudy Gobert 2018 (.657) is missing
    the same way.
  - `coverage_caveat` returns None for these seasons.
- **User sees:** a shooting leaderboard that omits qualified players, with no
  note - and the omissions are not random, they are two franchises.
- **Next step:** build the advanced view from `player_box_stats_filled`, or
  caveat 2013-2018 shooting boards. Note the rebuild does not recover minutes,
  so any per-minute advanced figure stays out.
- **Source:** DATA.md, "Every Chicago and New Orleans game from 2013 to 2018 has an empty box score"
- **GitHub:** #85

### The 2001 playoff caveat never reaches two of the templates that need it
- **Found:** 2026-09-15, issues audit (P2 data auditor)
- **Evidence:** `coverage.postseason_partial=(2001,)` is declared on `games` and
  `team_box_stats`, and `caveat()` only fires for a table a template declares in
  `TEMPLATE_SOURCES`. `single_game_high` and `threshold_count` declare
  `player_game_log`/`player_box_stats`, which carry no `postseason_partial`, so
  a 2001 playoff question through either gets no note.
  - Shaquille O'Neal's 2001 postseason holds 11 of his 16 games in the box
    table; "had 4 games with 30+ points" comes back with nothing said.
- **User sees:** a short 2001 playoff count or single-game high, stated as fact,
  while the same season caveats correctly through `head_to_head` or `game_log`.
- **Next step:** add a `postseason_partial` entry for `player_box_stats` (and
  `player_game_log`), or have the caveat follow the season rather than the table.
- **Source:** DATA.md, "The 2000 and 2001 playoffs stop before the Finals"
- **GitHub:** #86

### ESPN files one player under two athlete ids in the same box score
- **Found:** 2026-09-15, issues audit - found independently by two auditors
- **Evidence:** grouping `player_box_stats` by `(event_id, team_id,
  display_name)` and counting distinct `athlete_id` finds one person listed
  twice in one game.
  - Isaiah Canaan (`2490589` and `4412182`) in 20 Phoenix and Minnesota
    team-games in 2019, **with identical lines in 18 of them**. Corey Brewer
    (`3191`, `4415554`) in 8 of 8 in 2019. Daryl Macon (`4066243`, `4610145`)
    in 3 of 4 in 2020. Ken Johnson 2003 (`1008`, `1972`, 33 games) shows the
    pattern with non-identical lines.
  - This explains most of #54's 2019 disagreement: the team box's derived
    points equal the final score in every row of 2019, 2021 and 2026, while the
    player sums overshoot in 23 team-games in 2019, almost all Phoenix (15) and
    Philadelphia (7).
  - It also crosses tables: `net_points_player` uses ESPN's `dot_com_id` while
    the box scores and the name-matched fingerprint use the other id, so 8
    `net_points_player_fingerprint` rows have no matching `net_points_player`
    row and joins drop them.
- **User sees:** a team total summed from player rows double-counts that player,
  and the player's own career is split across two ids.
- **Next step:** detect the duplicate pairs at load time and map them to one id.
  Some of #21's "shared display names" are this, not two players, so the
  ambiguity rule there drops a real player's data.
- **Source:** DATA.md, "ESPN files one player under two athlete ids" (to be added)
- **GitHub:** #87

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
  `real_games`: PHI -7, LAL -5, MIL -5, NO -2, SA -1.
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
- **Re-checked 2026-09-15:** holds (PHI -7, LAL -5, MIL -5, NO -2, SA -1
  against `real_games`), but the caveat text in `coverage.py:168,189` is now
  stale - it says Philadelphia "reads 15 games" when Finals Game 5 brought it to
  16, and it names only the Final and MIL-PHI while MIL-CHA (2) and LAL-SA (1)
  are also short. 2000 is clean: 75 games in `real_games`, matching ESPN. The
  "70 -> 79" figure elsewhere is the raw `games` count, which includes 4
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
  `team_box_stats` exactly: 1,100,170 and 86,988 rows, zero differences. ESPN
  still serves the zeroed lines today.
- **No other ESPN source has the data** (probed live 2026-09-14; see DATA.md
  for the detail). The CDN box score on a different host serves the same zeros,
  the core API exposes no per-athlete per-game statistics at any path, and the
  athlete gamelog omits the games outright. The gamelog also proves the gap
  follows the *franchise*: Derrick Rose reads 0, 0, 0, 1, 61, 25 across
  2013-2018 and Aaron Brooks 51, 65, 0, 1, 60, 26, each zero exactly in his
  Chicago years. So rebuilding from `plays` is the only route to a per-game
  number, and there is nothing to re-fetch.
- **Done 2026-09-14 - the rebuild exists.** `player_box_stats_reconstructed`
  (`fetch/reconstructed_box.py`) is a load-time view over the 1,024 of these
  1,025 events that have plays, with fidelity documented per column on the
  module. It is deliberately separate: its own view over the empty games only,
  snake_case columns, no template reads it, and it is absent from
  `KNOWN_TABLES` so the SQL agent can neither query nor describe it. A player
  appearing in no play is absent rather than zero.
- **Done 2026-09-14 - the warehouse now uses it.** `player_box_stats_filled`
  (same module) is `player_box_stats` with those figures substituted into the
  empty lines and a `reconstructed` flag on exactly those rows. Measured: 21,169
  of 1,100,170 rows substituted, row count conserved, and Anthony Davis's 2015
  reads 68 games / 1,656 points against ESPN's own 68 / 1,656, with his 14
  did-not-play rows correctly left alone. It never touches a real line, never
  invents `minutes`, and drops the stored `plusMinus` on a substituted row -
  that column is a uniform 0 placeholder across all 21,169, not data.
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

### Vancouver 1996 has an empty TEAM box, not an empty player box
- **Found:** 2026-09-11 writing DATA.md; **re-measured and corrected 2026-09-14**,
  which also moved it from P1 to here
- **Evidence:** this entry used to say all 82 games were stored "with every
  player row at NULL minutes and zero stats: the same shape as the Chicago and
  New Orleans seasons". Measured on **both** tables, that is wrong, and the two
  faults are not the same shape at all:
  - `team_box_stats`: 82 of 82 Vancouver rows are all-NULL. This half was right.
  - `player_box_stats`: 936 rows across 78 of the 82 games, 12 a game - the
    league-normal roster size that season (2,189 of 1996's team-games have
    exactly 12) - and only 151 of those 936 rows lack minutes. **The player box
    is real.** For contrast, Chicago and New Orleans across 2013-2018 have
    *zero* player rows with minutes.
  - Vancouver's box points total 7,030 against ESPN's own season table's 7,362.
    That shortfall is the 4 games missing from `player_box_stats` outright, not
    a zeroed season.
  - Only **5** of 1,189 games in 1996 have no player box rows at all - which is
    exactly the "5 in 1996" figure this entry was created to overturn. The
    original figure was correct.
  - **The Chicago half of this bullet was wrong** (re-measured 2026-09-15).
    Chicago 2000's 82 all-NULL rows and 1999's 50 sit on **zero** `real_games`
    events: they are the 0-0 `T17:00Z` placeholder rows that `real_games`
    already drops. Chicago's real games have normal team rows (80 in 2000, 50
    in 1999), so those two seasons are not this fault at all.
  - **Vancouver is wider than recorded.** In 41 of its 82 games the *opponent's*
    team row is all-NULL too, and 37 of those sit beside real player rows,
    spread over 25 teams at 1-2 games each. League-wide, 115 null team rows on
    real 1996 games have real player rows beside them; with one 2000 game
    (`191102003`, ORL@NO) that is the 117 the comment at
    `fetch/team_box_repair.py:88` counts - but that comment names Chicago 2000
    as the other case, which is wrong.
- **Source:** DATA.md, "Vancouver 1996 is an empty TEAM box, not an empty player box"
- **User sees:** nothing at all for a per-player question - those rows are
  sound. A team-level read of 1996 Vancouver, 2000 Chicago or 1999 Chicago gets
  NULLs, and `_empty_box_scores` does not count these (it tests player minutes),
  so such an answer carries no caveat.
- **Next step:** rebuild the team line by summing the player rows at load time.
  That works here precisely because the player rows survived, which is what
  makes this fault different from 2013-2018 and cheaper to fix.
- **GitHub:** #67

### A game log over empty box scores says the games do not exist
- **Found:** 2026-09-14, measuring what the empty 2013-18 box scores actually
  break
- **Evidence:** `game_log` filters on `_RECORDED` (`pgl.minutes IS NOT NULL`),
  so the empty lines are correctly left out - but when that removes everything,
  `_no_narrowed_games` (`query/templates.py`) falls to its `if not total`
  branch and reports the span as holding no games. Live against the warehouse:
  `game_log {"player": "Anthony Davis", "season": 2015}` answers "No 2015
  regular season games found for Anthony Davis." He played 68.
- **Source:** DATA.md, "Every Chicago and New Orleans game from 2013 to 2018 has an empty box score"
- **User sees:** a refusal that is confident and names the wrong missing fact -
  the season, rather than the box scores - and so sends the reader to look in
  the wrong place. This is the mirror-image bug `AGENTS.md` describes, and the
  same one `single_game_high` was given a sentence for on 2026-09-14.
- **Next step:** in `_no_narrowed_games`, when the span holds no recorded games
  but `_empty_box_scores` counts some, say so instead - "his 68 games in
  2014-15 have an empty box score" - the way `_phrase_single_game_high` now
  does. Watch it fail before believing it.
- **Re-checked 2026-09-15: the named example is fixed, the shape survives.**
  `game_log` for Davis 2015 now lists rebuilt games with a note. But naming any
  stat outside `REBUILT_STATS` (turnovers, fouls, 3PM, plusMinus) sends the log
  back to fetched lines and it says "No 2015 regular season games found for
  Anthony Davis" again - and a `player_stat` narrowed by opponent, venue or
  `without` over those seasons says the same, because `_box_score_player_stat`
  does not read rebuilt lines.
- **GitHub:** #72
### The SQL agent and the web health line still read raw `games`
- **Found:** 2026-09-14, building the shared `real_games` list (issue #7)
- **Evidence:** `real_games` (`fetch/real_games.py`) now holds the 43,343 rows
  of `games` that are actually games, and every template reads it. Two readers
  do not, both by design rather than oversight:
  - **The SQL agent.** `KNOWN_TABLES` and `TABLE_SUMMARY` (`query/prompt.py`)
    name `games` and not `real_games`, so any question that falls through to
    the agent gets SQL over the unfiltered table - the 134 placeholders, the 23
    team-slots naming an id no franchise has, the 11 phantoms and the one
    remaining duplicate. This is exactly the population the templates were
    just fixed for, reached by the slower path. Adding a line to
    `TABLE_SUMMARY` is not free: `PREAMBLE_TOKEN_BUDGET` is 6,400 and
    AGENTS.md forbids buying room by trimming that text.
  - **The web health line.** `_warehouse_seasons` (`web/app.py:197`) counts
    `games`, so the page says 43,494 where 43,343 were played.
- **User sees:** an agent-written answer that counts rows that are not games,
  with nothing to mark it as different from the template answer to the same
  question; and a games count on the web page that is 151 too high.
- **Not affected, measured:** `player_box_stats`, `plays` and `shot_chart` hold
  0 rows against the 151 dropped events, so the player paths (`_PLAYER_GAMES`,
  `fingerprint.py`) never counted one. The 302 `team_box_stats` rows that do
  exist for them are entirely NULL, so no sum over that table was inflated
  either - they only ever mattered because a join could find them.
- **Next step:** decide whether the agent should be pointed at `real_games` -
  renaming the table it sees costs no tokens, but it changes what `describe_table`
  and hand-written SQL mean, and `games` would then be reachable only by a name
  the preamble does not mention. Fix the health line either way; it is one
  identifier.
- **Source:** DATA.md, "`games` carries placeholder, duplicate and phantom rows"
- **GitHub:** #73

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
- **GitHub:** #10

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
  Take the flag from NetPoints, which labels the game `IST Championship` (69
  rows in `net_points_player_game`), rather than inferring it from "the last
  neutral-site game in Las Vegas", which breaks when the venue moves.
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
- **GitHub:** #13

### Smaller game and box-score gaps, 1994-2003
- **Found:** 2026-09-11, template work (agents A, D) and the issues audit
- **Evidence:**
  - **2000 regular season:** `games` holds 1,166 of 1,189 real games. 18 teams
    have 80 of their 82, 10 have 81, and LAC has all 82.
  - **Real regular-season games with no box score:** 6 in 1994, **5** in 1996,
    6 in 1997, 5 in 1998, 5 in 2000 and 1 in 2003. Most are road games at UTAH,
    CLE or WSH. (This read "83 in 1996" until 2026-09-14. That number counted
    empty *team box* rows, not games missing a player box score; Vancouver's
    1996 player rows are real. See "Vancouver 1996 has an empty TEAM box" under
    P2.)
  - **Real postseason games with no box score:** the entire 1997 ECF CHI-MIA
    (`170520014`, `170522014`, `170524004`, `170526004`, `170528014`),
    `150614019` (1995 Finals), `160502025` (1996 SAC-SEA) and `230503026` (1998
    UTAH-HOU).
  - **NULL minutes in 2006-2012** mean the player did not appear. Dropping those
    rows raised 2009's games-played agreement from 30 to 378 of 445 players.
- **User sees:** small shortfalls, with no caveat, in box-derived answers for
  those seasons.
- **Next step:** refetch the listed events through the pipeline. Check that every
  box-derived template treats NULL minutes as "did not play".
- **Source:** DATA.md, "Real postseason games with no box score"
- **Re-checked 2026-09-15:** the counts were taken from raw `games`. Against
  `real_games` the no-box seasons are 1994: 5, 1996: 5, 1997: 6, 1998: 4,
  2000: 4, 2003: 0 (this entry says 6/5/6/5/5/1); the 1994, 1998 and 2003
  differences are phantom rows. Pattern the entry misses: each season's gaps are
  one visiting team's road games (DAL 1994, VAN 1996, VAN/BOS 1997, DEN 1998,
  LAC 2000), and 23 of 24 are at UTAH, CLE or WSH.
- **GitHub:** #14

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
- **Source:** DATA.md, "The 2026 shot chart holds more shots than the box score"
- **GitHub:** #15

### `with_without` undercounts a season-long absence
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
  that his tenure "falls outside the 2021 regular season"; Durant/Nets 2020 is
  the same. `player_season_stats` has **no row** for a season a player missed
  entirely, so it cannot supply tenure. Worse, `game_log` and `player_stat` say
  "Klay Thompson was not Stephen Curry's teammate in any of his 63 games" - a
  wrong-cause sentence about a rostered, injured player.
- **GitHub:** #16

### Historical teams are shown under today's names
- **Found:** 2026-09-11, template work (agent A)
- **Evidence:** `teams` holds only the 30 current teams, so the 1993 Charlotte
  Hornets are labelled "New Orleans Pelicans".
- **User sees:** a 1990s game log or matchup naming a franchise by its current
  name.
- **Next step:** build a per-season team-name table from each game's own team
  names, and use it wherever a historical game is printed.
- **Source:** DATA.md, "`teams` holds only the 30 current franchises"
- **GitHub:** #17

### A retired player's question defaults to the current season
- **Found:** 2026-09-08 (reported, not re-verified)
- **Evidence:** "The Answer's avg points" answers "Allen Iverson has no 2026
  regular season numbers".
- **User sees:** a refusal that is true but beside the point: the question meant
  his career.
- **Next step:** when a player has no rows in the defaulted season, answer their
  last season or career, and say so.
- **Re-checked 2026-09-15: broader than filed.** The same "has no 2026
  numbers" shape appears in `player_stat`, `single_game_high`, `game_log` and
  `player_netpoints`; `shot_chart` says "No shots found ... with the given
  filters" without naming the season at all. Only `player_history` answers.
- **GitHub:** #18

### `player_history` answers "last N seasons on record", not a calendar window
- **Found:** 2026-09-11, while fixing name clarification
- **Evidence:** the query reads `season <= ? ORDER BY season DESC LIMIT ?` per
  player (`player_history` in `query/templates.py`), so a player with gaps, or
  one who retired, gets their last N seasons played. "Curry's scoring over the
  last 4 seasons" would give Dell Curry 1999-2002. The header now names the
  range the rows reach ("by regular season, 2023-2026"), so the seasons are
  labelled truthfully. Because the template reads that way, name narrowing
  keeps every Curry for the question, narrowed through the anchor season with
  Seth and Stephen named first. It cannot drop players with nothing in the
  calendar window.
- **User sees:** "last 5 seasons" answered with seasons from years ago, labelled
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

### Per-game NetPoints rows whose name did not match keep no name
- **Found:** 2026-09-11, building the warehouse comparison harness
- **Evidence:** `parse_net_points_daily` and `parse_net_points_daily_players`
  store `athlete_id = None` when the display name matches no single player, and
  drop the name. `net_points_player_game` has 2,190 such rows, and
  `net_points_player_game_fingerprint` has 64,210. Nothing on the row says who
  they were: 159 keys in the first table and 4,657 in the second hold two or
  more rows that differ only in their values, and 701 of those groups are exact
  copies.
- **User sees:** nothing for those players, with no caveat, and a query grouping
  by (event_id, athlete_id) counts the unmatched rows as duplicates.
- **Next step:** keep the source `displayName` (and NBA.com's id) on the row.
  Then count unmatched names per season to find which spellings the exact match
  misses.
- **Source:** DATA.md, "NetPoints publishes a display name, not a player id"
- **GitHub:** #22

### 2008's team rebound columns are wrong
- **Found:** 2026-09-14, while fixing the 2018 team-box shift (#8) — the 2008
  rebound means stood out beside the seasons either side of it
- **Evidence:** over non-empty 2008 regular-season rows, `offensiveRebounds`
  equals the player-box sum in 188 of 2,460 rows (2007: 2,458; 2009: 2,454) and
  `defensiveRebounds` in 1 of 2,460 (2,453; 2,458). Means, 2007 → 2008 → 2009:
  OREB 11.12 → 8.36 → 11.04, DREB 29.93 → 11.20 → 30.26, totalRebounds 49.64 →
  **61.54** → 49.47. The player rows are sound (their rebound sum is 41.98 a
  game, between 2007's 41.05 and 2009's 41.29), and 2008's assists, steals,
  blocks and fouls each match their player sums in 2,443-2,460 of 2,460 rows,
  so this is rebounds only and not the 2018 shift reaching back. `totalRebounds`
  equals the player rebound sum plus the row's own OREB and DREB in 2,452 of
  2,460 rows, which is the lead on what it is actually counting. The 2008
  **postseason is clean** (172 rows: 49.55 / 11.08 / 29.58), so the fault is the
  regular season alone.
- **User sees:** a 2008 team split or with/without table reports **61.5**
  rebounds a game against a real ~49.6, because `conditions._TEAM_LINE` reads
  `AVG(t.totalRebounds)`. Agent SQL over 2008's rebound columns is wrong the
  same way.
- **Next step:** rebuild `offensiveRebounds` and `defensiveRebounds` from the
  player sums the way `fetch/team_box_repair.py` already rebuilds 2018's
  assists — the module is shaped to take another season. `totalRebounds`
  includes team rebounds and so is not a player sum, so it likely has to be
  NULL. Refetch one 2008 event first, to confirm ESPN is the cause.
- **Source:** DATA.md, "2008's team rebound columns hold something other than rebounds"
- **GitHub:** #74

### A team's rebounds are not comparable across 2021 and 2022
- **Found:** 2026-09-14, while fixing #8
- **Evidence:** the team box `totalRebounds` is the players' rebounds plus the
  team's own through 2020, and exactly `offensiveRebounds + defensiveRebounds`
  from 2022. It equals the player rebound sum in 0-3 of ~2,200 rows a season
  from 1993 to 2018, 333 of 2,460 in 2019, 1,120 of 2,160 in 2021, and 2,460 of
  2,460 in 2022-2024. The gap closes +8.07 (2018), +7.28 (2019), +7.10 (2020),
  +3.66 (2021), +0.00 (2022 on). `team_season_stats` shows the same drop: 53.17
  rebounds a game in 2020, 49.00 in 2021, 44.45 in 2022.
- **User sees:** a team rebound figure that falls about 8 a game at 2021-22 for
  reasons that are ESPN's bookkeeping, with no caveat. `_TEAM_LINE`'s REB column
  and `_team_games`' `tbs.totalRebounds` are the reads; a span crossing the
  change, or any comparison of an old season with a recent one, is affected.
- **Next step:** decide what REB should mean and make it one thing —
  `offensiveRebounds + defensiveRebounds` is comparable in every season and is
  what ESPN now publishes — or caveat a span that crosses 2021. Check
  `team_metrics` for the same exposure on `team_season_stats`.
- **Source:** DATA.md, "The team `totalRebounds` column stops including team rebounds in 2022"
- **GitHub:** #75
### The 2000 and 2001 date-only stamps are dated a day early
- **Found:** 2026-09-14, building the shared `real_games` list (issue #7);
  **re-measured and reframed 2026-09-15** by the issues audit
- **This entry used to blame 2026, and that was wrong.** All ten of 2026's
  `T04:00Z` games are real 11pm-Eastern tips with full box scores: each has a
  Pacific home team (LAC, SAC, POR, LAL, GS) on a UTC Wednesday, 2026 has 276
  ordinary late tips at 02:00-03:30Z, and the shift is corroborated by
  collisions - OKC plays at POR in `401810035` on the Wednesday, so LAC-OKC
  `401810025` cannot also be that day. The NetPoints daily file agrees. **The
  five-hour shift is correct for all ten.**
- **Evidence for the real cases:** date-encoded old-format event ids (YYMMDD +
  team) make the shift checkable: it is exact for 23,490 of 23,490 real-tip
  games. All **12** `T04:00Z` games in `real_games` - 2 in April 2000, 9 in the
  2000 postseason, 1 in the 2001 postseason - match their **written** date and
  **none** matches the shifted one.
  - **10 of the 12 arrived with the 2026-09-15 scoreboard recovery** (`72b599c`),
    which is how a long-standing fault became visible on a marquee series.
  - The whole 2000 LAL-IND Final is affected: `200607013` is Game 1, played
    7 June 2000, and every template prints **6 June**. The series reads
    6/8/10/13/15/18 June against a real 7/9/11/14/16/19.
- **User sees:** every date shown for those 12 games is one day early, stated as
  fact - a game log, a single-game high, a month split.
- **Next step:** read a date-only stamp (`T04:00Z`/`T05:00Z` with no tip time)
  as its written date instead of shifting it. The 12 rows are identifiable
  without a list: the stamp's own time is midnight Eastern.
- **Source:** DATA.md, "`games` carries placeholder, duplicate and phantom rows"
- **GitHub:** #76

## P3: refusal or gap

### A franchise's former name resolves to nothing
- **Found:** 2026-09-16, replaying the feed after the `period_split` fixes
- **Evidence:** "duren v nets 1h gameloh" routed correctly to `period_split`
  and arrived with `opponent="New Jersey Nets"` - the model's expansion of
  "nets", and the franchise's name until 2012. `entities.find_teams("New Jersey
  Nets")` returns `[]`, so the question falls through. `_TEAM_NICKNAMES`
  already maps shorthand ("sixers", "cavs") and the four abbreviations ESPN
  does not use; it has no former names. The same shape is likely for "Seattle
  SuperSonics", "New Orleans Hornets", "Charlotte Bobcats" and "Vancouver
  Grizzlies", none of them measured.
- **User sees:** a fall-through to the agent for a team the warehouse holds.
- **Next step:** check which former names the router emits and which resolve,
  then decide per name. Mapping "New Jersey Nets" to Brooklyn is safe - one
  franchise, one team id. "Charlotte Hornets" is not a former name at all, and
  "New Orleans Hornets" is the Pelicans, so this cannot be a blanket rule. See
  also #17, which is the same history seen from the answer's side.

### A quarter or half is answered for a player, and for nobody else
- **Found:** 2026-09-16 auditing the feed; **the player half shipped the same
  day** as `period_split`
- **Fixed.** 21 of the 261 feed queries ask for a quarter or a half and every
  one fell through, because nothing answered the shape. A named player's single
  period now has a template: `shot_chart` carries `athlete_id`, `period`,
  `made` and the shot's value, so it is a filtered sum, and the value is read
  through `SHOT_VALUE_SQL` - 99.95% against ESPN's linescores, where guessing
  it from the play's prose is 76.8%. Summed over all periods including
  overtime, a player's season total matches his box score exactly for 550 of
  578 player-seasons.
- **What is still not answered**, and it is most of the rest of that 21:
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


### `games.date` is a VARCHAR that DuckDB will not cast
- **Found:** 2026-09-16, during the query-set audit (six ad-hoc date queries,
  every one of which failed on the obvious form first)
- **Evidence:** the column holds `2021-10-23T22:00Z`.
  `CAST(g.date AS TIMESTAMP)` fails with `invalid timestamp field format`, and
  `g.date >= DATE '2020-01-26'` fails with `Cannot compare VARCHAR and DATE`.
  Only `strptime(g.date, '%Y-%m-%dT%H:%MZ')` works.
- **User sees:** not a wrong answer - an error the SQL-writing agent then has
  to recover from, spending a tool round trip on every date-filtered question
  against a 16k context that has no room for it.
- **Next step:** either derive a real `TIMESTAMP` (or an Eastern `game_date`
  DATE, which `season.eastern_date` already defines) at load time in
  `fetch/warehouse.py`, or state the exact `strptime` form in the agent
  preamble beside the existing date rules. Note the second option spends
  preamble budget, which is measured and tight - prefer the first.

### Whether ESPN publishes coaches is unverified
- **Found:** 2026-09-16, query-set audit
- **Evidence:** "nick nurse coaching record all-time nba in december on the
  road" has nothing to read: none of the warehouse's 25 tables holds a coach.
  Whether any endpoint the pull already reaches serves them was **not checked**,
  and is deliberately not asserted here either way.
- **User sees:** a fall-through on any coach question.
- **Next step:** probe the endpoints for a coach field before filing anything
  further. If ESPN does serve them and the pull discards them, that half is a
  `DATA.md` entry - the same shape as the conference-membership correction -
  and this entry links to it.

### Each narrowing the router has no slot for needs its own regex
- **Found:** 2026-09-15 replaying 261 real StatMuse feed queries through the
  fast path; **fixed for every measured case and re-ranked P1 -> P3 on
  2026-09-16**, after the second pass measured zero left.
- **What it was.** `check_scope()` refuses a narrowing a template cannot
  honour, but it can only see slots the router emits, and `ROUTER_SCHEMA` has
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
  date pre-empts "did you mean Bam Adebayo?". Right on its own terms - the date
  was being dropped too - but worth knowing the refusal is not free.
- **User sees:** nothing wrong today. The risk is the next unhandled narrowing.
- **Next step:** design the general check rather than adding a ninth regex - a
  catch-all slot, or a test that every meaningful word in the question reached
  some slot, so an unrecognized narrowing refuses by default instead of being
  ignored by default. Re-measure against `fastpath_after_84b.jsonl`, which is
  the current baseline.
- **Source:** the wrong answers are ours, not ESPN's; no DATA.md entry.
- **GitHub:** #84

### A regular-season BPI question answers from the play-in snapshot in 2023, 2025 and 2026
- **Found:** 2026-09-15, reviewing `4ef119f` before merging it
- **Evidence:** now that the paging fix gives every snapshot 30 teams, the
  play-in snapshot holds the team too, so `team_outlook`'s `pre` list (season
  types 1, 2 and 5) is ordered by date and `candidates[-1]` takes the latest.
  Measured read-only: the play-in stamp postdates the regular-season one in
  2023 (04-15 vs 04-10), 2025 (04-19 vs 04-14) and 2026 (04-18 vs 04-13), but
  not in 2024, whose regular-season snapshot is stamped 2024-06-28. Before the
  paging fix the 13-team play-in snapshot simply did not hold most teams and
  lost by default.
- **User sees:** a correct, clearly labeled answer - but the same question
  names a different snapshot depending on the season, and "how good were the
  Knicks in the 2026 regular season" is answered from the play-in view.
- **Next step:** decide whether `season_type=2` should prefer the
  regular-season snapshot outright rather than the latest pre-playoff one, and
  pin whichever it is with a test. It is a deliberate choice either way; today
  nothing records that it was made.
- **Source:** DATA.md, "ESPN's power index is a paged collection, and holds all
  30 teams"
- **GitHub:** #88

### Season 2021's regular-season BPI snapshot is a day-one projection
- **Found:** 2026-09-15, reviewing `4ef119f`
- **Evidence:** all 30 of season 2021's rows are stamped 2020-12-22 - opening
  day of 2020-21 - with `numwins` and `numlosses` both 0. `team_outlook`
  answers "2021 regular-season snapshot (updated 2020-12-22, 30 teams) ... BPI
  -5.9 ... no games played yet, projected 16-56" for the Knicks, who finished
  41-31. The caveat at `templates.py:3050` cannot fire, because it tests
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

### `get_collection` goes quiet on the exact failure it exists to make loud
- **Found:** 2026-09-15, reviewing `4ef119f`
- **Evidence:** `_request_json` returns `None` for any status in
  `NOT_FOUND_STATUS = {400, 404}` (`fetch/client.py:53`). In `get_collection`
  that hits `if not isinstance(data, dict): break` with `expected` still
  `None`, so the "collection %s declared %d items, fetched %d" warning cannot
  fire. The method returns `[]`, `parse_power_index([])` yields no rows, and
  `Pipeline._write_rows` no-ops on an empty list - leaving the season's old,
  possibly short Parquet in place with nothing in the log. An endpoint that
  rejects `limit=1000` with a 400 reproduces the original 25-row bug silently.
  No test covers the `None` path, and the docstring's "Returns an empty list
  where `get_json` would return None" is unasserted.
- **User sees:** nothing - a table quietly one pull behind, which is exactly
  how the 25-row power index survived for months.
- **Next step:** log at WARNING when a collection read ends on a non-dict first
  page, and add a test with a session that answers 400.
- **GitHub:** #90

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
- **Re-checked 2026-09-15:** still four unhonored slots (`below`, `round`,
  `since`, `situation`), but "by month" has left this entry - it is
  `split=month` and `player_splits` answers it for a player or a team.
- **GitHub:** #23

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
- **GitHub:** #24

### Conference and division are in the standings we fetch, and the parser drops them
- **Found:** 2026-09-11, template work (agent B); **cause corrected 2026-09-15**
  by the issues audit
- **The source does have it.** The standings response the pull already fetches
  is grouped: `standings?season=2026` returns children named
  `Eastern Conference` (15 teams) and `Western Conference` (15); 2004 returns
  15 and 14, 1990 returns 13 and 14; and `&level=3` returns the six divisions
  at 5 teams each. `standings` also carries "vs. Conf." and "vs. Div." records,
  populated from 2004.
- **Evidence:** `parse_standings` (`fetch/parse.py:346`) walks `children`
  purely to reach the entries and throws the group name away - its own test
  says so (`tests/fetch/test_parse.py:397`). `games.conference_game` is False
  on all 43,504 rows. No table maps a team to a conference, so
  `_conference_refusal` refuses a conference named as the subject ("who leads
  the east"), while "Western Conference standings" matches `router._SITUATION`
  first and is handed to an agent with no conference data either.
- **User sees:** a refusal for "who leads the East", and an ungrounded agent
  answer for "Western Conference standings".
- **Next step:** record the conference (and division at `level=3`) from the
  standings children, then re-pull `standings`. **Not** the static per-season
  table this entry used to propose.
- **Source:** DATA.md, "No conference, division or birth-date data anywhere" -
  that section is wrong for conference and division, and right for birth dates
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
  metrics. `TABLE_SUMMARY` does not list the two new view columns (left out for
  the preamble budget), so the agent has to `describe_table` to find them.
- **User sees:** "best true shooting among players with 50 games" makes the agent
  write SQL, more slowly.
- **Next step:** accept a `min_games` alongside `min_sample` in the tool, if the
  budget allows.
- **GitHub:** #28

### Fingerprint for a specific date
- **Found:** before 2026-09-11 (docstring)
- **Evidence:** the fingerprint template in `query/templates.py` says "... but not
  yet for a particular date". `game_log` already honors `date`.
- **User sees:** a helpful refusal.
- **Next step:** resolve the date to the player's game with `_eastern_day`, then
  draw the single-game fingerprint.
- **GitHub:** #29

### Franchise career leaderboards
- **Found:** before 2026-09-11 (docstring)
- **Evidence:** the leaderboard in `query/templates.py` says "Refused until that
  is decided". The rule for relocated franchises is open.
- **User sees:** a refusal for "timberwolves career leaders in total points".
- **Next step:** decide the relocation rule, then map it.
- **Re-checked 2026-09-15:** the "User sees" is wrong. `_career_leaderboard`
  raises `TemplateUnsupported`, which is a fall-through to the agent, not a
  refusal the user reads.
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
- **GitHub:** #33

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
- **GitHub:** #34

### The web page keeps no history, so closing the tab loses every answer
- **Found:** 2026-09-14, requested
- **Evidence:** each turn is built straight into the DOM
  (`ask()` in `web/static/index.html`) and nothing else holds it: there is no
  `localStorage`, no `sessionStorage` and no server-side store, and
  `AgentRunner` (`web/runner.py`) keeps only the lock and the current question.
  A reload, a crash or a server restart loses the thread. The artifacts a
  question produced do survive, as files in the output directory, but nothing
  records which question drew them, so an orphaned chart cannot be traced back
  to what was asked.
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

### The connection indicator is written once at load and never updated
- **Found:** 2026-09-14, requested
- **Evidence:** `web/static/index.html` calls `/api/health` exactly once, on
  load, and writes a status line from it ("3,043 games, 1994-2026", plus
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

### The web page never says what data the warehouse actually holds
- **Found:** 2026-09-14, requested
- **Evidence:** the page's only claim about coverage is the status line written
  once at load - "3,043 games, 1994-2026" - built from `/api/health`, whose
  `_warehouse_seasons` is a `min(season)`, `max(season)` and `count(*)` over
  `games` alone. That range is true and misleading in exactly the way
  `coverage.py` exists to prevent: it reads as "1994 to 2026 is answerable",
  when box scores start in 1994, play-by-play in 2003 (2002 is about half),
  shot charts in 2002 (partial through 2003) and NetPoints in 2019. The real
  per-season, per-season_type coverage is already computed by
  `association data check` (`check/report.py`) and the enforced floors already
  live in `coverage.py`; neither reaches the page.
- **User sees:** no way to tell what is answerable before asking. A 2016 shot
  chart and a 2016 NetPoints fingerprint look equally reasonable to ask for,
  and only one of them is.
- **Next step:** surface a readable subset of `data check` at the top right of
  a session, grouped by what a season actually supports, in three tiers:
  - **box score** - `games`, `player_box_stats`, `team_box_stats`
  - **+ play-by-play** - adds `plays` and `shot_chart`
  - **+ NetPoints** - adds the five NetPoints tables

  Show the season span each tier covers and mark the partial and phantom
  seasons `coverage.py` already declares. Three constraints. Read the tiers
  from `COVERAGE` rather than restating them in the page, or they become a
  fourth copy of the floors to drift out of date. Compute the counts at
  startup or cache them: `data check` scans the Parquet tree, and the health
  endpoint is on the path a polling indicator would hammer. And keep it
  honest about the difference `coverage.py` already draws - a season can be
  present, partial, unrepresentative for ranking, or a phantom, and "1994" is
  not one number for every table.
- **Re-checked 2026-09-15: the example is now worse.** `_warehouse_seasons`
  reads `min(season), max(season), count(*) FROM games`, which today returns
  1988, 2026 and 43,504 - so the page would claim "43,504 games, 1988-2026"
  when the regular-season floor is 1994 and 151 of those rows are not games.
- **GitHub:** #71
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

## P4: tooling, docs, low impact

### The BPI preseason tiebreak cannot be perturbation-tested through the template
- **Found:** 2026-09-15, reviewing `4ef119f` before merging it
- **Evidence:** `scripts/perturb.py` against
  `tests/query/test_team_templates.py`: **removing** the
  `CASE season_type WHEN {BPI_PRESEASON} THEN 0 ELSE 1 END` from
  `query/templates.py:2983` is MISSED (62 passed, exit 0), while **reversing**
  it to `THEN 1 ELSE 0` is CAUGHT by
  `test_a_same_dated_preseason_snapshot_never_wins_the_regular_season_question`.
  The reason it cannot be tested is the useful part: the `CASE` maps preseason
  to 0 and every other type to 1, and DuckDB already emits the groups in
  ascending `season_type`, so the guard agrees with the engine's incidental
  order for *every* possible pair. Reordering the fixture's inserts was tried
  and changes nothing.
- **User sees:** nothing. The guard is correct and worth keeping - it defends
  against an ordering DuckDB does not promise - but it is unprotected, so a
  later refactor can drop it silently. `4ef119f`'s message says "Five
  perturbations, each watched to fail", which is not true of this one; the
  test's docstring now records that.
- **Next step:** either accept it as untestable-by-construction (the docstring
  is then the record), or make the ordering explicit in Python where a test can
  reach it, rather than leaving the decision inside an `ORDER BY`.
- **GitHub:** #92

### The "postseason copy" rule is written twice, and both figures are stale
- **Found:** 2026-09-15, issues audit (P4 data/query auditor)
- **Evidence:** the rule that drops a postseason line ESPN copied from the
  regular season exists twice, in different words:
  `fetch/warehouse.py:182` (the `player_season_stats_deduped` view: more than 28
  games, or games+points equal to that season's regular-season line on any team)
  and `query/leaderboard.py:186` `not_a_postseason_copy` (games plus the value
  columns, on the same team). They agree today - each drops 436 of 7,941 rows -
  but nothing keeps them in step.
  - Both comments are stale: the view says "340 of 7,845 postseason rows" and
    the docstring says "437 of the 7,941". Re-measured: raw 7,941 rows, deduped
    7,505, so **436 rows** (340 player-seasons).
- **User sees:** nothing today. It is the same hand-maintained-pair shape as
  #83, with the added trap that the two spellings could diverge silently.
- **Next step:** export one helper and call it from both, the way #83 proposes
  for the traded-player dedup. Fix both figures while there.
- **GitHub:** #93

### `MAX_LIMIT` is 100 in one module and 50 in another
- **Found:** 2026-09-15, in the cross-module constant scan written after #6/#9
- **Evidence:** `query/leaderboard.py:38` declares `MAX_LIMIT = 100` (the cap on
  a model-supplied limit on the agent path); `query/templates.py:118` declares
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

### The five-hour Eastern offset is defined five times under four names
- **Found:** 2026-09-15, in the cross-module constant scan
- **Evidence:** the same NBA fact - a US Eastern date is UTC minus five hours,
  and EST/EDT disagree only in an hour no game starts in - is declared at
  `season.py:25` (`_EASTERN_SHIFT`), `fetch/real_games.py:89`
  (`EASTERN_OFFSET_HOURS`), `fetch/parse.py:728` (`_EASTERN_OFFSET`),
  `query/conditions.py:67` (`_EASTERN_OFFSET_HOURS`) and
  `query/templates.py:707` (`_EASTERN_SHIFT`). Four of the five are private, so
  no module can import another's.
- **User sees:** nothing today - all five hold 5. If one is ever changed
  without the others, dates drift between the fetch path, the game list and the
  query path, and a game lands on the wrong calendar day in one answer and the
  right one in the next. That is the fault `DATA.md` ("A NetPoints date is not
  an ESPN date") already cost this project once.
- **Next step:** one public declaration in `season.py` - the module that
  already owns season arithmetic - imported by the other four. `fetch` importing
  `season` is an edge that already exists (`current_season`).
- **A name-only scan cannot see this.** `scripts/check_duplicate_names.py`
  reports the two `_EASTERN_SHIFT` copies and is structurally blind to the
  other three, which wear different names. Finding those needed a human reading
  a grep for `hours=5`.
- **Re-checked 2026-09-15: six copies, not five.** The sixth is a bare
  literal in SQL - `query/team_metrics.py:291`, `- INTERVAL 5 HOUR AS DATE`
  inside `TEAM_GAMES_SQL` - whose own comment cites `parse._EASTERN_OFFSET`
  without using it. Neither the name scan nor a grep for `hours=5` finds it.
- **GitHub:** #82

### One rule, two hand-maintained copies: the traded-player dedup
- **Found:** 2026-09-15, while fixing #9
- **Evidence:** "prefer the combined row over the per-team stints" is written
  as SQL in `fetch/warehouse.py:198` (the `player_season_stats_deduped` view)
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
- **Evidence:** 41,417 `team_box_stats` rows hold `pointsInPaint = -1` — every
  non-empty row from 1993 to 2008 (2,358 in 1994, 2,632 in 2008) plus all 2,280
  of 2018's. `fastBreakPoints` and `turnoverPoints` never carry the sentinel,
  and `team_season_stats` uses 0 rather than -1 for the same era, so the two
  tables mark the same gap differently.
- **User sees:** nothing today — no template reads the column. Agent SQL asking
  for points in the paint in an old season gets -1 a game, which reads as a
  number rather than as a gap.
- **Next step:** NULL the sentinel at load, beside the 2018 clearing
  `fetch/team_box_repair.py` already does. One predicate, `pointsInPaint = -1`,
  and no season needs naming.
- **Source:** DATA.md, "`pointsInPaint` is -1 before 2009, and two lead columns exist only in 2026"
- **Re-checked 2026-09-15: count and two claims are stale.** `pointsInPaint =
  -1` is now 39,157 rows, not 41,417, because `team_box_repair` NULLs 2018's.
  **The claim that `team_season_stats` uses 0 for the same era is wrong** - its
  `pointsInPaint` is -1.0 in every team-season 1994-2008; only
  `fastBreakPoints` is 0. `query/team_metrics.py:24` and the user-visible
  refusal `_PAINT_REASON` (`:90`) both repeat the error.
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
- **Re-checked 2026-09-15:** three more scripts default the same way and are
  not in the list above - `check_team_box.py:136`, `backfill_season_totals.py:73`
  and `backfill_missing_playoffs.py:69` (the last two added this week).
- **GitHub:** #37

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
- **GitHub:** #38

### A fresh worktree cannot run the gates with `uv run` alone
- **Found:** 2026-09-11, while qualifying true shooting and eFG% (`f66e1f1`)
- **Evidence:** `uv run` creates the worktree's venv without the `dev` extra, so
  `uv run pytest -q` fails with `Failed to spawn: pytest` until
  `uv sync --frozen --extra dev --extra docs --extra web` (CI's line) has run.
- **User sees:** nothing. An agent loses time.
- **Next step:** add the sync line to "Before you commit" in `AGENTS.md`.
- **GitHub:** #39

### The docs gate passes with field markup printed as text
- **Found:** 2026-09-11, fixing the literal `:rtype:` lines
- **Evidence:** `scripts/build_docs.sh` passed `-W` while 68 functions on 18 of
  40 API pages printed a literal `:rtype: ...` line. A field marker inside a
  paragraph is valid reStructuredText, so docutils had nothing to warn about.
  Sphinx's fallback also added a real Return type field, so each of those
  functions showed its return type twice. It was found only by grepping the
  built HTML.
- **User sees:** stray markup in the published API docs, and nothing fails.
- **Next step:** make `build_docs.sh` fail when the text of
  `api/generated/*.html` contains `:rtype:`, `:param ` or `:type `. Watch it
  fail by removing the shim in `docs/conf.py`.
- **GitHub:** #40

### An incremental docs build ignores a behavior change in `docs/conf.py`
- **Found:** 2026-09-11, fixing the literal `:rtype:` lines
- **Evidence:** after the `:rtype:` shim went into `docs/conf.py`,
  `build_docs.sh` over an existing `docs/_build` still produced all 68
  literals. Sphinx re-reads sources only when a registered config value
  changes, and a patched function is not one, so it reused the pickled
  doctrees. Only `rm -rf docs/_build` showed the fix. CI builds from a clean
  checkout. A clean build took 11 seconds here. What `-E` would cost on every
  commit was not measured.
- **User sees:** nothing. An agent can read a stale build as a failed fix or as
  a working one, and the pre-commit docs gate is green either way.
- **Next step:** pass `-E` in `build_docs.sh`, or rebuild from scratch when
  `docs/conf.py` is newer than the build environment.
- **Re-checked 2026-09-15: this and #50 are the same defect** (no `-E` in
  `build_docs.sh`) and should merge. Measured: incremental no-op 2.7s, `-E`
  11.0s, clean 14.8s. With the shim disabled, an incremental build shows 0 pages
  with literal `:rtype:` where a fresh output dir shows 23.
- **GitHub:** #41

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
- **GitHub:** #42

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
- **Source:** DATA.md, "ESPN's `possessions` counts every turnover twice before 2013"
- **Re-checked 2026-09-15:** one fixed, one half fixed, one still wrong.
  (1) `AGENTS.md`'s "5/240/668" is gone and `DATA.md` is right.
  (2) `query/team_metrics.py:72` still claims pre-2013 `turnovers` is "player
  turnovers alone" - re-measured, `team_season_stats.turnovers` equals the box
  `totalTurnovers` sum for 25-27 of 30 teams and the player-only sum for 0.
  (3) `fetch/warehouse.py:184` still says "340 of 7,845"; it is 436 rows / 340
  player-seasons, and `DATA.md:548` wrongly says warehouse.py "previously" said
  it.
- **GitHub:** #43

### Broad `except duckdb.Error` in `_single_game_netpoints`
- **Found:** 2026-09-11, repo audit
- **Evidence:** `_single_game_netpoints` in `query/templates.py` catches every
  DuckDB error. `fingerprint.py` already narrowed the same pattern to the
  missing-table error.
- **User sees:** a SQL bug reported as "unavailable", then a slow fall-through.
- **Next step:** catch `duckdb.CatalogException` only.
- **Re-checked 2026-09-15:** the pattern occurs twice. The second is
  `templates.py:4117`, in the comparison's NetPoints section, which catches
  `duckdb.Error` and returns `{}` - so a SQL bug there makes the NetPoints rows
  silently disappear from a comparison.
- **GitHub:** #44

### Postseason shooting floors are scaled, not calibrated
- **Found:** 2026-09-11, while qualifying true shooting and eFG% (`f66e1f1`)
- **Evidence:** `ts_pct` 67 and `efg_pct` 59 are the season floors times 10/82.
  No published postseason list applies a qualifier (StatMuse's 2025 playoff
  leader shot 150% on two attempts), so there was nothing to check them against.
  They leave 81-92 qualified players per postseason in 2025 and 2026.
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
  play that season.
- **GitHub:** #47

### Clarifications can name twenty players
- **Found:** 2026-09-11, season-narrowing branch
- **Evidence:** `Ambiguous.active` makes a clarification name every candidate
  from the season asked about (`entities.clarification`). The surname with the
  most players in one season is Williams: 15 in 1998 and 1999, 14 in 2026.
  "Will" names 18 players in 2026 and 20 in 1998.
- **User sees:** a long "did you mean" sentence. Whether it reads acceptably in
  the CLI and on the web page was not checked.
- **Next step:** look at a 20-name clarification on the web page.
- **GitHub:** #48

### The CHANGES.md hook passes on an unstaged tree
- **Found:** 2026-09-11, season-narrowing branch
- **Evidence:** `scripts/check_changes_md.sh` reads only `git diff --cached`. So
  `pre-commit run --all-files` with `src/` edited and nothing staged reports
  "CHANGES.md updated....Passed". With `src/` staged alone, it fails as
  intended.
- **User sees:** nothing. An agent can read the pass as a real check.
- **Next step:** have the hook say it checked nothing when the index is empty.
- **GitHub:** #49

### The docs gate keeps stale pages after a change to `docs/conf.py` code
- **Found:** 2026-09-11, merging `92cf1e5` into the season-narrowing branch
- **Evidence:** `scripts/build_docs.sh` builds incrementally into
  `docs/_build/html`, with no `-E` and no clean output directory. `92cf1e5`
  fixed the literal `:rtype:` lines with a function in `docs/conf.py`, not a
  config value, so Sphinx did not treat the change as one that invalidates its
  cached environment. After the merge, the pre-commit docs hook passed and
  `docs/_build/html` still had the literal line on 18 of 40 pages. A fresh
  build of the same tree into an empty directory had it on 0 of 40, with 18
  "Return type" fields on the `association.query.entities` page. A page Sphinx
  does not re-read also re-emits none of its warnings, so the local `-W` gate
  can pass where a fresh build fails. That is the "local run weaker than CI"
  shape `AGENTS.md` records for the other gates. CI's own docs build was not
  checked. A change to `cli.py` alone does the same to `commands.html`: after
  the 2.1.0 help-text change, the page kept its 2026-09-10 build until a clean
  build replaced it.
- **User sees:** nothing directly. An agent reading the built HTML sees stale
  pages, and a docs warning can go unnoticed until CI.
- **Next step:** pass `-E` (or clear the output directory) in `build_docs.sh`,
  and time it against the incremental build.
- **GitHub:** #50

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
  - `largestLead` is filled on 47,480 of 83,261 non-empty team box rows, and
    `leadChanges` on 2,018.
  - `net_points_team` holds 2026 only, which is inherent to the source.
- **User sees:** nothing today. Any template that starts reading these would.
- **Next step:** measure each one before a template reads it.
- **Source:** DATA.md, "`dnp_reason` is set on players who played"
- **Re-checked 2026-09-15:** figures reproduce, two details changed. Player
  `plusMinus` **is** read now (the game log's "+/-" column,
  `templates.py:3076`), and `team_season_stats.plusMinus` is -1 in 828 rows
  (2009 on) and NULL in 675 before that, not "-1 in every season" as
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
  does not show.
- **User sees:** a subtitle count one or two higher than the number of dots.
- **Next step:** clamp heaves to the edge of the plot, or note them in the
  subtitle.
- **Re-checked 2026-09-15: larger than filed.** Positioned shots past the
  half-court line: 475 in 2024, 581 in 2025, 1,083 in 2026. Rendering Luka
  Doncic's 2026 season draws 1,479 markers with **22 off the canvas**, not the
  one or two this entry describes.
- **GitHub:** #55

### A slow agent answer cannot be cancelled
- **Found:** before 2026-09-11 (`web/app.py` comment, `roadmap-2.0.md`)
- **Evidence:** closing the tab does not stop the inference. The planned fix, a
  cancelled flag checked between tool calls, is not built.
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

### The router prompt's documented size is five times too small
- **Found:** 2026-09-11, docs survey for 2.1.0
- **Evidence:** `query/router.py` says the prompt is "~430 tokens", in both its
  published module docstring and the comment on `ROUTER_NUM_CTX`.
  `ROUTER_PROMPT` is now 9,989 characters, about 2,500 tokens at four
  characters a token. That is an estimate, not measured with the tokenizer.
  The context is `ROUTER_NUM_CTX = 4096`, and there is no budget guard like
  `PREAMBLE_TOKEN_BUDGET`. ollama truncates an over-length prompt
  head-first, silently.
- **User sees:** nothing yet, with about 1,500 tokens of headroom. A few more
  intent lines and the head of the prompt starts to disappear. Every question
  then routes worse, and there is no error.
- **Next step:** read `prompt_eval_count` from one router call and correct both
  comments. Then add a test that fails when `ROUTER_PROMPT` plus a long question
  passes a set budget, the way `PreambleTooLarge` guards the agent.
- **Re-checked 2026-09-15:** `router.py:11,272` still say "~430 tokens".
  `len(ROUTER_PROMPT)` is 9,989 characters, about 2,497 tokens at 4 chars/token,
  against `ROUTER_NUM_CTX = 4096`. No test or budget guard exists.
- **GitHub:** #59

### The release script does not update the install pins
- **Found:** 2026-09-11, docs survey for 2.1.0
- **Evidence:** because PyPI is unreachable, `README.md` and
  `docs/installation.rst` pin `git+https://github.com/jeffknupp/association@vX.Y.Z`.
  `scripts/bump_version.py` rewrites only `pyproject.toml`, `uv.lock` and
  `CHANGES.md`. So the pins said `v1.4.0` through three later releases, until
  they were updated by hand for 2.1.0.
- **User sees:** install instructions that install an old release.
- **Next step:** have the bump script rewrite `@v<current>` to `@v<new>` in both
  files, and refuse if a pin names neither version.
- **GitHub:** #60

### British spellings in `src/`
- **Found:** 2026-09-11, docs survey for 2.1.0
- **Evidence:** there are 24 hits for honour, normalis- and colour in `src/`.
  Some are user-visible: the `check_scope` trace message ("cannot honour") and
  docstrings in `query/router.py` that are published. `AGENTS.md` requires
  American spelling, and the 2.1.0 changelog was corrected.
- **User sees:** "honour" in a `--verbose` trace, and mixed spelling in the API
  docs.
- **Next step:** replace them, with a CHANGES line, since the change touches
  `src/`.
- **Re-checked 2026-09-15: the spellings moved.** "honour" family: 27
  occurrences on 26 lines (`router.py` 11, `templates.py` 15), including the
  user-visible trace "cannot honour" (`templates.py:390,556`) and three
  published docstrings. normalis- and colour are now 0. Not listed in this
  entry: "behaviour" x3 and "labelled/mislabelling" x6.
- **GitHub:** #61

### A coverage caveat is added to a refusal that drew nothing
- **Found:** 2026-09-11, docs edits for 2.1.0
- **Evidence:** "plot Kobe Bryant's threes in 2002" is refused, and the answer
  still ends "...so the answer covers part of the year". That is the partial-
  season caveat for 2002 shots, attached to an answer that covers nothing.
- **User sees:** a refusal that also claims to cover part of a season.
- **Next step:** skip `coverage_caveat` when the template's result is a refusal.
- **GitHub:** #62

### Stale leftovers from the 2.0 REPL and an example that stopped early
- **Found:** 2026-09-11, docs edits for 2.1.0
- **Evidence:** a comment in `router.route()` says "The `ai` REPL gets real
  follow-ups", but the REPL was removed in 2.0.0 and nothing passes
  `previous_question` now. In `docs/usage.rst`, the example "who leads the
  league in assists?" shows only the first sentence of an answer that continues
  "Next: ...".
- **User sees:** a docs example shorter than the real output.
- **Next step:** correct the comment, and paste the example's full answer.
- **Re-checked 2026-09-15:** "nothing passes `previous_question`" is
  imprecise - `agent.py:247` passes `self.last_question`, but it is always None
  in both shipped callers (the CLI builds a new Agent per question; the web
  resets per request), so the parameter and the router branch at
  `router.py:811` are dead in practice rather than unreferenced. The stale
  `usage.rst` example is real regardless, and is now also missing the
  "(minimum 20 games)" qualifier the answer prints.
- **GitHub:** #63

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
- **GitHub:** #66

### Free-throw coordinates stop after 2018
- **Found:** 2026-09-11, writing DATA.md
- **Evidence:** free throws carry a court position through 2018 and then stop:
  93 of 60,813 in 2019, and none by 2026.
- **Source:** DATA.md, "Free throws carry a court position from 2002 to 2018"
- **User sees:** nothing today. A shot chart excludes free throws by value, not
  by position, so the gap changes no answer. Anything that started reading a
  free throw's coordinates would be reading nothing for recent seasons.
- **Next step:** none until something reads them. Recorded so the next reader
  does not mistake the gap for a parser fault.
- **GitHub:** #68

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
    first row alone. See "A row narrower than the rows after it truncated the
    whole file" under P1. Two earlier drafts of this entry blamed ESPN flipping
    within minutes, and then "something between the two callers"; both were
    guesses made ahead of the trace, and both are withdrawn.
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
