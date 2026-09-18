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

Empty. The 391 date-only games printed a day early (#76), 2008's team rebound
columns (#74) and the swapped 1990 Finals Game 5 were fixed on 2026-09-16. Before declaring this section empty,
re-read the P2s against the P1 definition: that is how both of those were
found.

## P2: misleading or incomplete

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
    player sums overshoot in 23 team-games in 2019 - **re-measured 2026-09-16:
    Phoenix 14, Philadelphia 7, Sacramento 1, Minnesota 1** (this read "almost
    all Phoenix (15) and Philadelphia (7)" until then, which is 22 of the 23
    and misses the two one-game teams).
  - It also crosses tables: `net_points_player` uses ESPN's `dot_com_id` while
    the box scores and the name-matched fingerprint use the other id, so 8
    `net_points_player_fingerprint` rows have no matching `net_points_player`
    row and joins drop them.
- **User sees:** a team total summed from player rows double-counts that player,
  and the player's own career is split across two ids.
- **Next step:** detect the duplicate pairs at load time and map them to one id.
  Some of #21's "shared display names" are this, not two players, so the
  ambiguity rule there drops a real player's data.
- **Source:** DATA.md, "ESPN files one player under two athlete ids" (`DATA.md:103`)
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
    `fetch/repairs/team_box_repair.py:108` counts. That comment used to name Chicago
    2000 as the other case; it was corrected on 2026-09-17. Re-measured
    2026-09-17 against the live warehouse (read-only): 115 in 1996, 2 rows
    (both sides) on the single 2000 game - 117 confirmed exactly.
- **Source:** DATA.md, "Vancouver 1996 is an empty TEAM box, not an empty player box"
- **User sees (before the fix below):** nothing at all for a per-player
  question - those rows are sound. A team-level read of 1996 Vancouver gets
  NULLs, and `_empty_box_scores` does not count these (it tests player
  minutes), so such an answer carries no caveat. **This was half wrong.** The
  team branch of `player_splits` (`query/templates/splits.py`) does caveat: it
  counts rows with `fieldGoalsAttempted IS NULL` and says "Rebounds, assists,
  3-pointers and FG% are missing from N of those games' box scores and are
  averaged over the rest." But `_team_games` (`conditions.py:257-271`), which
  backs team `streak` and `record_when`, reads
  `tbs.totalRebounds`/`tbs.assists` directly with no NULL count or caveat at
  all - so a 1996 Grizzlies rebound streak or threshold question silently
  excluded all 82 games, with nothing said.
- **Fixed in code 2026-09-17, not yet backfilled.**
  `fetch/repairs/team_box_repair.py` now rebuilds a team row that is itself
  all-NULL (`_EMPTY_TEAM_ROW`) from its game's player rows, for every column a
  player sum can prove exactly: `fieldGoalsMade/Attempted`,
  `threePointFieldGoalsMade/Attempted`, `freeThrowsMade/Attempted` (with their
  percentages), `assists`, `steals`, `blocks`, `turnovers`, `fouls`, and the
  `offensiveRebounds`/`defensiveRebounds` split. Measured against the 2,387
  surviving (non-empty) 1996 regular-season team rows and the 2,472 surviving
  2000 ones - the population `AGENTS.md` asks for, games in the SAME seasons
  whose team row survived - every one of those columns equals the player sum
  in 100% of rows, and ESPN's own `round(100 * made / attempted)` reproduces
  its stored shooting percentages in 100% as well. `totalRebounds` is
  deliberately NOT rebuilt: it runs 8.80 a game above the player oreb+dreb sum
  on those same 2,387 rows and matches it in only 1 of 2,387, because before
  2022 it includes the team's own boards, credited to no player (`DATA.md`,
  "The team `totalRebounds` column stops including team rebounds in 2022").
  Neither is `teamTurnovers`, `totalTurnovers`, `technicalFouls`,
  `totalTechnicalFouls`, `flagrantFouls`, `pointsInPaint`, `fastBreakPoints`,
  `largestLead`, `leadChanges` or `leadPercentage` - the player box has no
  sibling for any of them, so they stay NULL on a rebuilt row exactly as ESPN
  served them.

  This makes the `_team_games` half of "User sees" above moot for the columns
  it reads (`totalRebounds`, `assists`): `assists` is now rebuilt and populated
  for these rows, so a 1996 Grizzlies assist streak or threshold question will
  see them; a `totalRebounds` one still will not, correctly, since that column
  has no source to rebuild from and stays NULL. `conditions.py` was not
  touched (out of this change's scope) and needs no change for `assists` to
  start working - the fix is entirely upstream, in what `team_box_stats`
  holds after a load.

  **Not yet backfilled against the live warehouse.** The fix is load-time
  (`fetch/repairs/team_box_repair.py`, run from `_repair_and_build_views` on
  every `warehouse.build`), so it needs `association data load` (not a
  `data pull`) against the real data directory to reach
  `/home/jeff/code/association/nba.duckdb`:
  `association data load --data-dir ./data/parquet --db-path ./nba.duckdb`
  (or the project's own default paths, run from the main checkout). Re-measure
  afterwards with the same query this entry's evidence used, and confirm the
  117 rows now hold real `assists`/`fieldGoalsMade`/etc and NULL `totalRebounds`.
- **GitHub:** #67

### The team-splits rebounds caveat goes quiet on exactly the games it exists for
- **Found:** 2026-09-17, fixing the Vancouver 1996 team-box entry above.
- **Evidence:** `_player_splits_team` (`query/templates/splits.py:225-233`) adds
  "Rebounds, assists, 3-pointers and FG% are missing from N of those games'
  box scores and are averaged over the rest" by counting rows where
  `fieldGoalsAttempted IS NULL` in `_TEAM_LINE`'s base query - a single bit
  standing in for "this row has nothing". Once `team_box_repair` rebuilds the
  117 Vancouver/1996-opponent/2000 rows above, that bit flips to "populated"
  for all of them, because `fieldGoalsAttempted` genuinely is now real - but
  `totalRebounds` (what `_TEAM_LINE`'s "rebounds" column reads,
  `conditions.py:388`, `AVG(t.totalRebounds)`) was deliberately left NULL on
  every one of those rows, since nothing in the player box can rebuild it (see
  the Vancouver entry above). Measured against the live warehouse (read-only,
  before the fix is backfilled but the count does not depend on the backfill):
  for the Grizzlies' own 1996 games (`team_id='29'`), the `blanks` count this
  caveat uses would drop from 82 to 4 once the rebuild is loaded; league-wide
  over the 28 affected team-seasons in 1996 and 2000, the drop is 137 to 20.
  So a "rebounds ... are missing" sentence that used to cover 82 Grizzlies
  games will cover 4, understating by 78 - correct for assists/3PM/FG%, which
  really are no longer missing, but wrong for rebounds specifically, the first
  word the sentence names.
- **Source:** DATA.md, "Vancouver 1996 is an empty TEAM box, not an empty
  player box"; "The team `totalRebounds` column stops including team rebounds
  in 2022" (why `totalRebounds` cannot be rebuilt at all, in any era).
- **User sees:** a team rebounds split or average over a span touching 1996
  Vancouver (or one of its 25 1996 opponents, or the 2000 ORL/NO game) that
  silently averages a NULL `totalRebounds` into the shown figure over 78 more
  games than the caveat admits, once the load-time fix above is backfilled.
  Before the backfill, the caveat is accurate (blanks really is 82) - this is
  a *consequence* of the Vancouver fix landing, not a bug that exists yet in
  the live warehouse.
- **Next step:** give `_player_splits_team`'s caveat a second, column-specific
  test - count `totalRebounds IS NULL` separately from `fieldGoalsAttempted IS
  NULL` and word the sentence around whichever columns are actually short,
  the way `_box_missing`/`_empty_box_scores` already separate "no box score at
  all" from "box score present but a stat is NULL" elsewhere. Out of this
  fix's scope (`query/templates/splits.py` and `query/conditions.py`, not
  touched here - `conditions.py` is another agent's concurrent work).
- **Priority:** P2 (misleading - the averaged number is right, the caveat that
  is supposed to flag its gap is not, once the fix above is backfilled).

### A game log over empty box scores says the games do not exist
- **Found:** 2026-09-14, measuring what the empty 2013-18 box scores actually
  break
- **Evidence:** `game_log` filters on `_RECORDED` (`pgl.minutes IS NOT NULL`),
  so the empty lines are correctly left out - but when that removes everything,
  `_no_narrowed_games` (`query/templates/common.py`) falls to its `if not total`
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
- **Confirmed live 2026-09-16, and wider than the line above says.**
  `game_log {"player": "Anthony Davis", "season": 2015, "stat": "turnovers"}`
  and the same with `"stat": "fouls"` both answer "No 2015 regular season games
  found for Anthony Davis." `_box_score_player_stat`
  (`query/templates/players.py`) calls `narrowed.clauses()` at its default
  (`rebuilt: bool = False`, `query/templates/common.py`), so **any** `player_stat`
  narrowed by opponent, venue or `without` over those seasons gives the same
  wrong-cause sentence - **even for points**, the one stat the rebuild gets
  right: "Anthony Davis points vs the Lakers in 2015" answers the same refusal
  rather than reading `player_box_stats_filled`.
- **GitHub:** #72
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
  - **The web health line.** `_warehouse_seasons` (`web/app.py:196`) counts
    `games`, so the page says 43,504 where 43,353 were played.
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

### Shooting qualifiers are flat across shortened seasons
- **Found:** 2026-09-11, while qualifying true shooting and eFG% (`f66e1f1`)
- **Evidence:** the 550/480 floors assume an 82-game schedule. At 550
  true-shooting attempts, 2020 qualifies 157 players and 2021 qualifies 155,
  against 174-184 in 2019 and in 2022-2026. **The 2012 lockout season (66
  games) now measured: 127 qualify at 550 TSA** (`player_season_stats_deduped`),
  the lowest of any season checked - consistent with a shortened schedule.
  **2013-2018 read 143-157 in `player_season_advanced_stats`, but that is not
  the schedule** - those six seasons are full 82-game ones, and the low count
  is the empty-box-score fault (#1, and those seasons are now declared
  `partial` on that table so a board says who is missing from it):
  `player_season_advanced_stats` is built
  from raw `player_box_stats`, whose Chicago and New Orleans rows are zeroed
  those years, so real qualifiers are undercounted there specifically, not
  flattened by a short season. Published rules scale per team game.
- **User sees:** fewer qualified players in short seasons. The qualifier is
  stated, but it is harsher than the published one.
- **Next step:** scale the floor per team game, and keep `min_sample_applied`
  honest about the scaled number. The floors to change live in
  `query/metrics.py` (`default_min_sample=550` at `:220`, `=480` at `:230`,
  the `fg_pct` 400-attempt floor at `:398`), not in `leaderboard.py`.
- **GitHub:** #13

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
- **Reproduced live 2026-09-16, direct template calls, no router involved.**
  `game_log` with `{"player": "Tim Hardaway", "opponent": "New York Knicks"}`
  answers "No 2026 regular season games found for Tim Hardaway."; `player_stat`
  with `{"player": "Allen Iverson", "stat": "points"}` answers "Allen Iverson
  has no 2026 regular season numbers in the warehouse." Both resolved the
  retired player himself, with no clarification offered, even though "Tim
  Hardaway Jr." exists in the warehouse and could have been asked about.
- **GitHub:** #18

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

## P3: refusal or gap

### A calendar date on `player_stat` or `head_to_head` falls through instead of answering the game
- **Found:** 2026-09-16, issues audit, from the latest replay
- **Evidence:** "Bam adebeyo jan 19" routes to `player_stat` with
  `date="2023-01-19"` resolved, and falls through on `player_stat cannot
  honor ['date']`; "celtics record vs sixers on november 11" does the same on
  `head_to_head`. `game_log` honors `date` (`query/templates/common.py`), and
  `player_stat` already declines a `limit` as a `game_log` question
  (`query/templates/players.py`, falling through rather than redirecting) and has no
  equivalent for a `date`. (The 2023 in the first example is itself the model's
  invention - see the next entry.)
- **User sees:** a slow agent answer for a question a template answers
  exactly, and no "did you mean Bam Adebayo?", since `check_scope` refuses
  before the name is resolved.
- **Next step:** in `route()`, send a `player_stat` with a resolved `date` to
  `game_log` with a limit of 1, the same way `CODE_ASSIGNED_INTENTS` handles a
  period; decide separately whether `head_to_head` should honor `date`.
- **Source:** ours, not ESPN's.
- **GitHub:** #94

### The router invents a `date` or a `season` the question never states
- **Found:** 2026-09-16, issues audit and the "Bam adebeyo jan 19" investigation
- **Evidence:** "2024 nba stephen curry double double per game scored on
  fridays" arrived with `date="2024-12-15"`, and "Best NBA record since January
  31st" with `date="2023-01-31"`. Separately, 12 of the 261 feed queries name
  no year and arrive with a non-current `season` from the model (2025 four
  times, 2023 three times); `_validate_season` accepts any year in range when
  the question names none, which is how "jan 19" became 2023-01-19. None of the
  12 is graded correct - they fall through, are wrong, or are unclear.
- **User sees:** nothing wrong today on `date`, because both examples are
  refused. But a model-invented `date` on `game_log` or `fingerprint` answers a
  game nobody asked about, and an invented `season` narrows any question to a
  year nobody named.
- **Next step:** in `route()`, keep a model-supplied `date` only when
  `_CALENDAR_DATE` finds one in the question, and a model-supplied `season`
  only when the question names a year or a relative season - the rule
  `override_invented_players` applies to names.
- **Source:** ours, not ESPN's.
- **GitHub:** #95

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

### Four question filters are recognized but no template answers them
- **Found:** 2026-09-11, template work and final corpus run
- **Evidence:** `SCOPING_SLOTS` against `HONORED_SCOPING` (`query/templates/common.py`):
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
- **Re-checked 2026-09-16, and a fifth slot is missing from the same list.**
  `until` is set at `router.py:1269` alongside `since`, but is in neither
  `HONORED_SCOPING` nor `SCOPING_SLOTS` (`query/templates/common.py`) - so `check_scope`
  cannot see it to refuse it. Harmless today, because `until` is only ever set
  together with `since` and no template honors `since`, so the question is
  refused over `since` first. It becomes a silent wrong answer the day a
  template honors `since` without reading `until`: add `until` to
  `SCOPING_SLOTS` before, or with, the first `since`. The stale
  refusal counts against `fastpath_r3.jsonl` are replaced by the current
  fall-through counts by slot: `situation` 20, `since` 4, `below` 2, `round` 1.
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
- **User sees:** a fall-through to the agent, for six real questions and not
  only the constructed one this entry started from.
- **Next step:** let `player_compare` honor `opponent` by building each
  player's line through `_narrow_player_games`.
- **GitHub:** #34

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

### `get_collection` goes quiet on the exact failure it exists to make loud
- **Found:** 2026-09-15, reviewing `4ef119f`; **re-ranked P3 -> P4 on 2026-09-16** - nothing a user sees, which is P4's definition
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

### A regular-season BPI question answers from the play-in snapshot in 2023, 2025 and 2026
- **Found:** 2026-09-15, reviewing `4ef119f` before merging it; **re-ranked P3 -> P4 on 2026-09-16** - the answer is correct and says which snapshot it read
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

### The BPI preseason tiebreak cannot be perturbation-tested through the template
- **Found:** 2026-09-15, reviewing `4ef119f` before merging it
- **Evidence:** `scripts/perturb.py` against
  `tests/query/test_team_templates.py`: **removing** the
  `CASE season_type WHEN {BPI_PRESEASON} THEN 0 ELSE 1 END` from
  `query/templates/teams.py` is MISSED (62 passed, exit 0), while **reversing**
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
