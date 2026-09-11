# What the source does

A catalog of the data faults and limits on **ESPN's side**. Each entry is a
fact about what the source serves, with the evidence for it.

**This file and `ISSUES.md` are two halves of one finding, and they are not
interchangeable.**

- **`DATA.md` is what the source does.** Wrong values, missing games, odd
  labeling, where each table's history starts, conventions that mislead. Facts
  about ESPN, each with evidence.
- **`ISSUES.md` is what we do about it.** The fix, the workaround, the caveat.
  Entries there link back to the `DATA.md` section they come from.
- **A `DATA.md` entry is not a task.** It gets no GitHub issue and nothing
  closes it, because nothing we write changes what the source serves. The
  actionable half lives in `ISSUES.md`. An entry here whose "Tracked in" reads
  "no action needed" is finished as it is.

Why the split is worth the trouble: almost every entry below was first written
as a bug in our own code. Reading a fault as ours costs a refetch that cannot
work, and the strongest evidence that a fault is ESPN's is a fresh pull that
reproduces it. **On 2026-09-11 a full 1988-2026 pull with current code was made
into a separate warehouse and compared table by table against the existing one
(`out_old_vs_new/report.md`): 18 of 23 objects were byte-identical, including
`games`, `plays`, `shot_chart`, `player_box_stats`, `team_box_stats`,
`standings` and every per-game NetPoints table.** Where an entry below says a
refetch does not fix it, that comparison is why.

Numbers here were measured read-only against
`/home/jeff/code/association/nba.duckdb` on 2026-09-11, and re-verified the
same day for this file. Where a re-measurement disagreed with what was
previously recorded, the entry says so.

Entry format, and it leads with what ESPN does rather than with our code:

```
### Short title
- **What ESPN does:** one or two sentences
- **Evidence:** numbers, seasons, example ids
- **Does a refetch fix it?** yes / no, proven by the 2026-09-11 fresh pull / untested
- **How we handle it:** what the code does today, or "nothing yet"
- **Tracked in:** ISSUES.md entry title + GitHub issue number, or "no action needed"
```

Groups are ordered by how many answers they touch, and entries within a group
likewise.

---

## Missing data

### Every Chicago and New Orleans game from 2013 to 2018 has an empty box score

- **What ESPN does:** serves a box score in which every player on both teams is
  listed as having played with NULL minutes and every stat zero, and the team
  line all NULL. It is not scattered games: it is two franchises for six
  straight seasons, playoffs included.
- **Evidence:** 978 regular-season events and 47 postseason events. Per season
  the empty share of regular-season team-games is 332 (13.51%), 326 (13.25%),
  324 (13.17%), 322 (13.09%), 326 (13.25%) and 326 (13.25%) for 2013-2018, and
  exactly 0 in 2011, 2012, 2019 and 2020. New Orleans owns 492 of the empty
  team-games (82 of 82 in each of the six seasons) and Chicago 491 (81 in
  2016); every other team's 35-37 are its games against those two. All 978 are
  empty on *both* sides — there are no half-empty games in this range.
  Box-score points come to 86.5-87.3% of ESPN's own season totals across the
  six seasons (86.95, 87.31, 86.94, 86.81, 86.92, 86.45). The plays and shots
  for those games survived, and `player_season_stats` and `team_season_stats`
  are unaffected.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull.**
  `player_box_stats` and `team_box_stats` came back byte-identical, 1,100,170
  and 86,988 rows with zero differences. ESPN still serves the zeroed lines.
- **How we handle it:** `_empty_box_scores` (`query/templates.py`) counts the
  games a per-game answer could not see and says so. The caveat undersells it —
  it gives a count, not "every Bulls and Pelicans game".
- **Tracked in:** ISSUES.md, "Nearly every Bulls and Pelicans box score from
  2013 to 2018 is zeros" (#1).

### Vancouver's whole 1996 season has an empty box score too

- **What ESPN does:** the same zeroed-box-score fault as the entry above, in a
  season nobody had attributed to a team. Every 1995-96 Vancouver Grizzlies
  game has an empty team box line.
- **Evidence:** **re-measured 2026-09-11 and larger than previously recorded.**
  125 empty regular-season team-box rows in 1996, of which the Grizzlies (id
  `MEM`, Vancouver at the time) own 82 — their complete schedule. Counted by
  game, 42 real 1996 regular-season games are empty on both sides and a further
  41 on one side, so 83 games are touched, not the "5 in 1996" that
  `ISSUES.md` records under the smaller 1994-2003 gaps. The same recount moves
  three neighboring years: 1994 is 6 fully empty games (recorded as 5), 1998 is
  5 (recorded as 4), 2000 is 5 (recorded as 4), and 2003 has 1 that was not
  listed at all. 1997 is 6, as recorded.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**, which
  reproduced `team_box_stats` exactly.
- **How we handle it:** nothing specific. `_empty_box_scores` counts these the
  same way it counts the 2013-2018 ones, so a 1996 answer carries a count but
  no named cause.
- **Tracked in:** ISSUES.md, "Smaller game and box-score gaps, 1994-2003"
  (#14) — whose 1996 figure this entry corrects.

### The 2000 and 2001 playoffs stop before the Finals

- **What ESPN does:** its team schedules simply do not list the last rounds of
  those two postseasons, so the games are undiscoverable from the endpoint the
  pull walks. The rows are not wrong; they are absent.
- **Evidence:** the 2000 postseason holds 70 games and ends on 2000-06-01; the
  2001 postseason holds 60 and ends on 2001-05-28. Missing from 2000: the whole
  LAL-IND Final (6 games), WCF LAL-POR games 6-7, ECF IND-NY game 6. Missing
  from 2001: the Final (5), ECF MIL-PHI games 5-7, WCF LAL-SA game 4, and 2
  games of MIL-CHA. The Lakers have 15 postseason games in 2000 and 10 in 2001,
  against 23 and 16 really played. Neighboring seasons are intact (1999 ends
  1999-06-26, 2002 ends 2002-06-13, 2003 ends 2003-06-15).
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**, which
  returned the same 70 rows for the 2000 postseason, ending on the same date,
  with `games` matching across all 43,494 rows.
- **How we handle it:** only `team_record` notices, through `_game_list_gaps`.
  `coverage.py` declares no partial season for either, so nothing else caveats
  them.
- **Tracked in:** ISSUES.md, "The 2000 and 2001 playoffs stop before the
  Finals" (#6).

### The 2000 regular season is short, and the 2000 standings share the gap

- **What ESPN does:** omits 23 real regular-season games from 2000, and its
  *standings* for that season agree with the short game list rather than with
  reality — so the two sources corroborate each other while both being wrong.
- **Evidence:** `games` holds 1,248 rows for the 2000 regular season, of which
  82 are scoreless placeholders (below), leaving 1,166 real games against
  1,189 really played. 18 teams have 80 of their 82, 10 have 81, LAC has all
  82. For all 29 teams the standings' W-L equals the W-L counted from `games`,
  while `team_season_stats` gives every team 82 games: the Lakers are 67-13 in
  both standings and the game tally, against a real 67-15. The 1999 lockout
  season is consistent by the same arithmetic (775 rows less 50 placeholders =
  725 real games, which is 29 teams x 50 / 2).
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**
  (`games` and `standings` both reproduced exactly).
- **How we handle it:** `team_metrics` refuses to rate the 2000 regular season
  — points allowed are used only where the game count equals the team's own,
  which fails for 28 of 29 teams there.
- **Tracked in:** ISSUES.md, "Smaller game and box-score gaps, 1994-2003"
  (#14).

### Real postseason games with no box score

- **What ESPN does:** serves an empty box for a scattering of real playoff
  games, including an entire conference finals and two Finals games.
- **Evidence:** the whole 1997 ECF CHI-MIA (`170520014`, `170522014`,
  `170524004`, `170526004`, `170528014`); `150614019`, a 1995 Finals game;
  `160502025` (1996 SAC-SEA); `230503026` (1998 UTAH-HOU). Every pre-1994
  postseason is empty in bulk as well — 62 games in 1988, 72 in 1989, 68 in
  1990, 73 in 1991, 76 in 1992 — which is the box-score floor below, not a
  scattering.
- **Does a refetch fix it?** **Untested for these specific events.** The fresh
  pull reproduced `team_box_stats` exactly, which is strong evidence, but no
  single event was refetched with `--force` to confirm.
- **How we handle it:** `_empty_box_scores` counts them.
- **Tracked in:** ISSUES.md, "Smaller game and box-score gaps, 1994-2003"
  (#14).

### 246 season lines are served with no totals

- **What ESPN does:** its per-player career endpoint returns the averages
  category for a season and nothing in the totals category, so the line has
  `avgPoints` and a NULL `points`.
- **Evidence:** 104 regular-season rows across 42 players and 142 postseason
  rows across 65 players have every counting total NULL. In 98 of the
  regular-season rows the `avg*` columns are still filled — Seth Curry's 2022
  reads `avgPoints` 15.0 beside a NULL `points`, and every one of his 18
  regular-season rows from 2014 on is NULL-totaled. The other 6 are the all-NULL
  combined rows in the traded-players entry below. Lou Amundson (2007-2016) and
  David Wood (1989-1997) recur the same way, as do postseason lines for Nazr
  Mohammed (12 rows), Zach Randolph (9), Theo Ratliff (8) and Gabe Vincent (7).
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**, which
  reproduced `player_season_stats` exactly apart from 13 `position` values.
- **How we handle it:** nothing yet. Totals leaderboards and career sums drop
  these seasons silently.
- **Tracked in:** ISSUES.md, "246 season lines have NULL totals" (#5).

### NetPoints publishes a display name, not a player id

- **What ESPN does:** keys its NetPoints files by `displayName` alone, with no
  athlete id, so a name shared by two players cannot be resolved from the row.
- **Evidence:** 2,190 rows in `net_points_player_game` and 64,210 in
  `net_points_player_game_fingerprint` carry a NULL `athlete_id` because the
  name matched no single player. 21 display names in `players` are shared by 42
  players. 159 keys in the first table and 4,657 in the second hold two or more
  rows that differ only in their values, and 701 of those groups are exact
  copies — both warehouses show identical counts.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**: the
  per-game NetPoints tables matched exactly, duplicate keys included.
- **How we handle it:** the parser stores `athlete_id = None` and drops the
  name, so nothing on the row says who it was.
- **Tracked in:** ISSUES.md, "Per-game NetPoints rows whose name did not match
  keep no name" (#22), and "The NetPoints season fingerprint matches players
  mid-pull, so a name can be lost" (#21).

### ESPN's power index keeps only postseason teams

- **What ESPN does:** publishes BPI as a single current snapshot per season
  rather than a dated series, and by the time a season is archived that
  snapshot holds only the play-in and postseason field.
- **Evidence:** `team_power_index` has exactly 25 rows in every season from
  2017 to 2026. 2017-2021 pair BPI values with final records. ESPN's own rank
  columns hold values like 26,058 before 2022.
- **Does a refetch fix it?** **No, and no pull can recover the past** — the
  earlier snapshots were never archived anywhere reachable.
- **How we handle it:** `team_outlook` names its snapshot, date and size in
  every answer, counts a team's standing within the snapshot rather than
  trusting ESPN's rank columns, and tells a missing team which snapshots exist.
- **Tracked in:** ISSUES.md, "The power index (BPI) keeps one snapshot per
  season" (#27).

### No conference, division or birth-date data anywhere

- **What ESPN does:** returns `conferenceCompetition` as false on every game it
  serves here, and publishes no team-to-conference mapping and no birth date
  through any endpoint the pull reads.
- **Evidence:** `games.conference_game` is False on all 43,494 rows. No table
  maps a team to a conference or division. No table holds a birth date.
- **Does a refetch fix it?** **No** — a different endpoint (or a different
  source entirely, for birth dates) would be needed.
- **How we handle it:** `_conference_refusal` refuses a conference named as the
  subject. Anything by age is refused or falls through.
- **Tracked in:** ISSUES.md, "No conference or division data" (#25) and
  "Shapes deferred for lack of data or logic" (#32).

---

## Wrong values

### 2018 team box scores hold values under the wrong column names

- **What ESPN does:** serves the 2018 team box line with its values shifted
  across the column names, so each column holds a *different real statistic*.
  The values are right; the labels are wrong. Real team assists appear nowhere
  in the row.
- **Evidence:** every non-empty 2018 regular-season row (2,134 of 2,134)
  matches player-box sums this way, and 144 of 146 postseason rows:

  | Column | Actually holds | Matching rows |
  | --- | --- | --- |
  | `assists` | blocks | 2,134 / 2,134 |
  | `steals` | turnovers | 2,132 / 2,134 |
  | `blocks` | fouls | 2,134 / 2,134 |
  | `flagrantFouls` | steals | 2,134 / 2,134 |
  | `fieldGoalPct` | FT% | — |
  | `freeThrowPct` | about 3P% | — |

  `assists` equals the real assist sum in 1 row of 2,134. No turnover or foul
  column is right either: `turnovers` averages 0.585 a game, `totalTurnovers`
  1.181 (and equals the player-box figure in 1 row of 2,134), `fouls` 0.043
  against a real 19.99. FGM, FGA, 3PM, FTM and rebounds are correct. The
  control seasons prove it is ESPN and not a parser column order: in 2017,
  `assists` is the real assist sum in 2,134 of 2,134 rows, and in 2019 in 2,436
  of 2,460 — the same code, the same column order, the right values.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**, which
  reproduced `team_box_stats` exactly.
- **How we handle it:** nothing yet. `conditions._TEAM_LINE` reads
  `AVG(t.assists)`, so 2017-18 team split tables report blocks (about 4.8 a
  game) as assists.
- **Tracked in:** ISSUES.md, "2018 team box scores have values under the wrong
  column names" (#8).

### The team box `turnovers` column is zero before 2013

- **What ESPN does:** leaves the team box `turnovers` column at 0 and copies
  `totalTurnovers` into `teamTurnovers` for every season up to 2012. Only
  `totalTurnovers` carries a usable number.
- **Evidence:** measured per season over non-empty regular-season rows,
  `turnovers = 0` in 2,322 of 2,322 rows in 2000, 2,459 of 2,460 in 2011, 1,976
  of 1,980 in 2012 — and in **0** of 2,126 rows in 2013 and 0 of 2,134 in 2014.
  The changeover is exactly at 2013. `teamTurnovers = totalTurnovers` in the
  same rows, season for season.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull.**
- **How we handle it:** `team_metrics` recomputes possessions as
  FGA - OREB + TOV + 0.44 x FTA using the turnover column that is right in each
  era, rather than trusting ESPN's own `possessions`.
- **Tracked in:** ISSUES.md, "2018 team box scores have values under the wrong
  column names" (#8), which covers the pre-2013 turnovers under its second
  bullet.

### ESPN's `possessions` counts every turnover twice before 2013

- **What ESPN does:** publishes a `possessions` figure in `team_season_stats`
  that double-counts turnovers for the pre-2013 seasons.
- **Evidence:** 114 possessions a game in 1994, against a real figure around
  96. From 2009 onward the recomputation reproduces ESPN's own number exactly.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**
  (`team_season_stats` reproduced exactly).
- **How we handle it:** possessions are recomputed rather than read
  (`query/team_metrics.py`); neither input is taken as stored.
- **Tracked in:** no action needed — handled where the data is read.

### ESPN's career endpoint copies regular seasons into the postseason

- **What ESPN does:** files some regular seasons a second time as that player's
  postseason, producing playoff lines for runs that never happened.
- **Evidence:** Eddy Curry has 527 "playoff games" across 11 postseason rows in
  `player_season_stats` — seven regular seasons each filed twice. Across the
  table, the deduplicating view drops **436 of 7,941 postseason rows**, which
  is **340 of 7,845 player-seasons**. Both `AGENTS.md` and
  `fetch/warehouse.py` previously gave "340 of 7,845 rows", conflating the two
  units; re-measured 2026-09-11, 436/7,941 is the row figure and 340/7,845 the
  player-season figure.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**, which
  reproduced `player_season_stats` exactly apart from 13 `position` values.
- **How we handle it:** `player_season_stats_deduped` drops a postseason line
  claiming more than 28 games (four best-of-seven rounds) or repeating that
  season's regular-season games and points exactly. Every real run checked was
  kept. **Read the deduped view for postseason lines; the raw table still has
  them.**
- **Tracked in:** ISSUES.md, "Three wrong statements in the docs" (#43) for the
  unit confusion. The dedup itself needs no further action.

### Traded players' combined season rows disagree with their own stints

- **What ESPN does:** returns a combined (team-less) season line for a traded
  player that does not equal the sum of that season's stints — sometimes
  copying a single stint, sometimes all NULL.
- **Evidence:** re-verified 2026-09-11: of 2,062 combined rows (`team_id`
  NULL), **26** disagree with the sum of that season's stints. 6 are entirely
  NULL (Moses Malone 1977, James Edwards 1978 and 1983, Bill Laimbeer 1982,
  Danny Schayes 1983, Sleepy Floyd 1983). 12 from 1996 copy a single stint,
  losing 3,042 points between them — Eric Murdock's combined row reads 9 games
  and 62 points against stints totalling 73 and 647. Jevon Carter 2023 drops a
  1-game stint. 7 have NULL points because a stint's totals are NULL.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**, which
  reproduced every one of these rows.
- **How we handle it:** `player_season_stats_deduped` and the leaderboard's
  `dedup_traded` both *prefer* the combined row, so the wrong line is the one
  that shows.
- **Tracked in:** ISSUES.md, "Traded players' combined season rows are wrong in
  26 cases" (#9).

### `games` carries placeholder, duplicate and phantom rows

- **What ESPN does:** serves three distinct kinds of junk row alongside real
  games, all of which look like games.
- **Evidence:**
  - **Placeholders** — 134 regular-season rows with no score and no winner
    (1999: 50, 2000: 82, 2001: 1, 2002: 1), **133 of which involve Chicago**.
    Most are stamped 16:00Z or 17:00Z beside the real game, e.g. `400216711`
    (1999-02-05 UTAH v CHI 0-0) next to `190205026`. Their opponents are often
    team ids that do not exist in `teams` at all (`1202`, `1300`).
  - **Duplicates** — the same game under two event ids. 2003-01-04 DAL-PHI
    102-83 is stored as both `230104006` (17:30Z) and `400222658` (18:30Z),
    same teams, same score, one hour apart.
  - **Phantoms that carry a winner** — date-only `T04:00Z` stamps, no box rows,
    often a team id missing from `teams`: `131205075` (team 75, filed under
    *both* 1993 and 1994), `150611014` (MIA-ORL, actually Houston's 1995 Finals
    Game 3, and Miami has no 1995 postseason), `170429031` and `170501031`
    (team 31), `171209083` (team 83), `190612021` (PHX-POR),
    `200422100`/`200424100`/`200505100` (team 100) and `200501028` (a copy of
    TOR-NY). They make 1994 and 1998 one game long against `team_season_stats`,
    by exactly 225 and 175 points.
  - **Team ids absent from `teams`**, re-counted 2026-09-11: `1202` (7 rows,
    1999-2000), `75` (6 rows, 1992-1994), `1300` (3, 1999-2000), `100` (3,
    2000), `31` (2, 1997), `125` (1, 1988), `83` (1, 1998).
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**, which
  reproduced `games` exactly — placeholders, duplicates and phantoms included.
- **How we handle it:** unevenly. `team_metrics.TEAM_GAMES_SQL` drops
  placeholders and same-day duplicates but keeps phantoms that have a winner;
  `conditions` filters only on `winner_team_id IS NOT NULL`; `head_to_head`
  counts every one of these rows; the team `game_log` joins `team_box_stats`,
  which the phantoms lack.
- **Tracked in:** ISSUES.md, "`games` holds placeholder, duplicate and phantom
  rows that templates count" (#7).

### The 2026 shot chart holds more shots than the box score

- **What ESPN does:** logs shots in the play-by-play that its own box score
  does not credit as attempts.
- **Evidence:** re-verified 2026-09-11 with free throws excluded — **1,165
  player-games** have more field goals in `shot_chart` than the box score's
  FGA, **1,207 extra shots** in total. Stephen Curry has **488** charted 2026
  threes against **484** 3PA in the box score. Neither `plays` nor `shot_chart`
  holds a duplicate `play_id`. The extras look like end-of-period heaves —
  1,141 of those player-games have extra 3PA and hold 1,084 shots taken with
  under a second on the clock — but that is a correlation, not proof.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**
  (`plays` and `shot_chart` both reproduced exactly).
- **How we handle it:** nothing yet.
- **Tracked in:** ISSUES.md, "The 2026 shot chart holds more shots than the box
  score" (#15).

### Team box scores disagree slightly with player-box sums in some seasons

- **What ESPN does:** publishes a team line and player lines for the same game
  that do not quite add up, in a handful of games a season. Which side is wrong
  is not known.
- **Evidence:** re-verified 2026-09-11 over non-empty regular-season rows —
  2019: `assists` agrees in 2,436 of 2,460, `steals` in 2,445, `turnovers` in
  2,440, `blocks` in 2,459. 2021: `blocks` in 2,139 of 2,160, `steals` in
  2,142, `assists` in 2,158, `turnovers` in 2,137. 2026: `assists` in 2,450 of
  2,462, `steals` in 2,459, `blocks` in 2,461, `turnovers` in 2,458.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull.**
- **How we handle it:** nothing. No template reads the team columns in a way
  that surfaces it yet.
- **Tracked in:** ISSUES.md, "Team box scores disagree slightly with player-box
  sums in 2019, 2021 and 2026" (#54).

### A NetPoints date is not an ESPN date

- **What ESPN does:** names each daily NetPoints file for the **US Eastern**
  date the games were played on, while storing a **UTC** tip timestamp in
  `games` that rolls over to the next day for anything tipping after 7pm
  Eastern. The two conventions are both internally consistent and cannot be
  matched on the calendar day.
- **Evidence:** matching on the UTC date meant choosing between `date + 1` and
  `date`, and *both orderings are wrong for some real schedule*: `date`-first
  steals a back-to-back's first night, `date + 1`-first steals the next night,
  after which that night's own file claims the same game again. 505 doubly
  claimed team-games since 2019, 611 disagreeing `(event_id, athlete_id)` pairs
  in 2026's `net_points_player_game` alone, with no error anywhere — the row
  count looked right and every event_id was a real game the player really
  played in. Separately, four games in late February 2020 are stored hours from
  when they were played (Detroit at Portland, a 6pm Pacific tip, is recorded as
  `2020-02-24T12:00Z`), so their Eastern date is meaningless while their stored
  date is still right. 126 `(team, UTC date)` keys were shadowed in the
  NetPoints era by ESPN clock errors putting two of one team's games on one
  date.
- **Does a refetch fix it?** **Not applicable** — this is a convention
  mismatch between two of ESPN's own products, not a bad value.
- **How we handle it:** `NetPointsGameIndex` (`fetch/parse.py`) reads the
  game's own Eastern date off the timestamp with a **fixed five-hour shift**
  (EST and EDT disagree about a tip's date only in the midnight-to-1am Eastern
  hour, which no NBA game starts in), does **one exact lookup** since a team
  plays at most one game per Eastern date, resolves a date holding two of one
  team's games to **neither**, and keeps the UTC window as a fallback in the
  old order for the February 2020 games. The daily file's `pts` column is
  dropped by the parser deliberately, which makes it a free independent
  cross-check that a row landed on the right game;
  `scripts/check_net_points_games.py` runs that over a whole season.
- **Tracked in:** no action needed — fixed in `f152b5e`.

### `team_season_stats.plusMinus` is a placeholder

- **What ESPN does:** returns -1.0 rather than a real plus-minus for much of
  the table.
- **Evidence:** re-measured 2026-09-11 — **828 of 1,503 rows** are exactly
  -1.0, not all of them as previously recorded.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull.**
- **How we handle it:** nothing reads it.
- **Tracked in:** ISSUES.md, "Columns that look wrong but that nothing reads"
  (#53).

---

## Labeling and conventions

These mislead rather than being wrong. Each is a convention ESPN applies
consistently, which is exactly what makes it dangerous: the data looks healthy.

### A season is named for the year it ends

- **What ESPN does:** labels 2023-24 as season `2024`.
- **Evidence:** consistent across every table from 1994 on. See `season.py`.
- **Does a refetch fix it?** **Not applicable** — a convention, not a fault.
- **How we handle it:** `season.py` is the single place that knows.
- **Tracked in:** no action needed.

### Before 1993-94, ESPN labels a season by the year it STARTED

- **What ESPN does:** switches labeling convention between 1993 and 1994. The
  postseason it files under 1990 is the 1991 playoffs.
- **Evidence:** re-verified 2026-09-11 — the postseason labeled 1988 runs
  1989-04-27 to 1989-06-13; 1989 runs 1990-04-26 to 1990-06-14; **1990 runs
  1991-04-25 to 1991-06-12, the 1991 Finals**; 1991 runs 1992-04-23 to
  1992-06-14; 1992 runs 1993-04-29 to 1993-06-20. From 1994 the label and the
  calendar year agree. Matched by label, "the 1991 playoffs" answered 1992's.
- **Does a refetch fix it?** **Not applicable** — a convention.
- **How we handle it:** **a postseason is selected by the calendar year it was
  played in, never by label** (`templates._season_games`,
  `team_metrics.games_scope`, `check_coverage.py`). That is exact for every
  season, because every postseason is played inside the year its season is
  named for.
- **Tracked in:** no action needed.

### Season 1993 is a phantom: its rows are a copy of 1994

- **What ESPN does:** answers `season=1993` and `season=1994` with the
  **identical** set of events, so 1993 looks like a complete season by every
  row count and is not the 1992-93 season at all. It is the labeling change
  above, seen from the other side.
- **Evidence:** re-verified 2026-09-11 — 1,185 distinct events under 1993,
  1,185 under 1994, and **1,185 shared**: a total overlap. Both run 1993-11-06
  to 1994-06-23. `player_box_stats` holds 28,195 rows under each label. It is
  the only duplicated pair in the warehouse — every other season's event ids
  are disjoint. It is confined to `games` and what derives from it;
  `standings` comes from a different endpoint and its 1993 rows are genuinely
  the 1992-93 season.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**, which
  reproduced `games` exactly.
- **How we handle it:** `coverage.py` declares 1993 a `phantom` rather than
  merely excluding it, so `check_coverage.py` can verify the duplication
  instead of reading a full-looking season as a floor set too high. **Joins
  over this era must key on `season` as well as `event_id`** — a game log that
  joined on `event_id` alone listed every 1993-94 player-game four times
  (112,780 rows for 28,195 games).
- **Tracked in:** no action needed.

### The NBA Cup final is stored as a regular-season game

- **What ESPN does:** files the in-season tournament final with `season_type`
  2 and no flag of any kind, though the league does not count it in the
  standings. It is marked only by being at a neutral site in Las Vegas.
- **Evidence:** re-verified 2026-09-11 — `401607495` (2024, LAL-IND, 123-109),
  `401734908` (2025, OKC-MIL, 81-97) and `401809839` (2026, NY-SA, 124-113),
  all `season_type` 2, `neutral_site` true, `venue_city` Las Vegas. Leaving it
  out makes the numbers agree: with it in, exactly the two finalists are a game
  off the standings in each of 2024, 2025 and 2026 (IND 47-35 in standings
  against a 47-36 tally, LAL 47-35 against 48-35, and so on for MIL/OKC and
  NY/SA); games-derived season points exceed `team_season_stats` by exactly the
  final's points (232, 178, 237). NetPoints names it explicitly — its
  `net_points_season_type` column carries an `IST Championship` value on 69
  rows, beside `Regular Season` (4,465), `Playoffs` (1,765) and `PlayIn` (545).
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull.**
- **How we handle it:** `team_record` excludes it (`NOT cup_final`, derived as
  "the last Las Vegas game"). `conditions._Scope.where`, `head_to_head` and
  every box-derived player aggregate do not.
- **Tracked in:** ISSUES.md, "The NBA Cup final is counted as a regular-season
  game in most answers" (#11).

### The NetPoints tables disagree with each other about `season_type`

- **What ESPN does:** uses a different season-type convention in each NetPoints
  product.
- **Evidence:** `net_points_player` has its own **string** column
  (`net_points_season_type`), whose values are `Regular Season` (4,465),
  `Playoffs` (1,765), `PlayIn` (545) and `IST Championship` (69).
  `net_points_player_game` and `net_points_player_game_fingerprint` use the
  normal **numeric** 2/3. `net_points_player_fingerprint` has **no season_type
  column at all**.
- **Does a refetch fix it?** **Not applicable** — a convention.
- **How we handle it:** nothing central. **Filtering the string column with a
  numeric matches nothing, with no error** — this is the trap.
- **Tracked in:** no action needed; the rule is repeated in `AGENTS.md`.

### `points_attempted = 0` means unlabeled, not zero points

- **What ESPN does:** leaves its own shot-value label at 0 for whole seasons
  rather than omitting it, so the column reads as a real value.
- **Evidence:** re-verified 2026-09-11 — 0 for **every** shot of 2002 and 2003
  (218,635 rows in 2003 alone), 96% of 2022's, and 22.8% of 2010's field goals
  (48,621 of 213,554), which is within the 23-28% recorded for 2004-2012. Every
  one of those is a miss, so reading the label as a value gives a 72% field
  goal percentage. 2026 has no 0 rows at all. Filtering on it charted 38 of
  Stephen Curry's 751 threes in 2022, all misses; Kobe Bryant's 2003 threes
  came back as "No 3-point shots".
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull.**
- **How we handle it:** **never filter `points_attempted` for a shot's value.**
  `shotchart.SHOT_VALUE_SQL` derives it: the label where there is one, else
  `shot_type` for a free throw, else the description where it says "three
  point" or "two point", else — through 2012, whose descriptions name every
  three — a two, else the shot's position against the line. Counted against the
  box score it matches 99.6-100% of player-games from 2003 on and 99.3% in
  2022. 2002 does not get there (99.05%), so a two-or-three question about 2002
  is refused (`UNSEPARABLE_SHOT_VALUES`), and 2003 and 2022 answer with a note
  (`DERIVED_SHOT_VALUES`).
- **Tracked in:** no action needed.

### Shot coordinates measure y from the rim, not the baseline

- **What ESPN does:** puts the origin of `coordinate_y` at the rim. Assuming
  the baseline breaks nothing visibly — distances stay plausible, just three
  feet short, and a court drawn around the same wrong point looks
  self-consistent.
- **Evidence:** the rim is at `(25, 0)` (`court.HOOP_X`, `court.HOOP_Y`). Most
  shot descriptions carry their own distance ("makes 26-foot three point
  jumper"), and from 2002 through 2012 that distance equals
  `round(hypot(x - 25, y))` for all 1.3 million of them; later seasons agree to
  within a foot on 99.8%. With the old `(25, 5.25)`, Stephen Curry's 2026
  threes averaged 23.6 feet — inside a 23.75-foot line — where from the rim
  they average 27.6. One thing that looks like a counterexample and is not:
  from 2023-11-02 the descriptions run 0.64 feet short of the rim, as if it had
  moved; the coordinates did not, since the three-point line separates ESPN's
  own labels 99.93% of the time after that date and only 99.72% from a rim a
  foot out. **ESPN changed its prose, not its frame.**
- **Does a refetch fix it?** **Not applicable** — a convention, and it was our
  reading of it that was wrong.
- **How we handle it:** `court.py` holds the geometry in the data's own frame
  (`HOOP_Y = 0`, baseline at -5.25), and `SHOT_DISTANCE_SQL`,
  `BEYOND_THE_ARC_SQL` and `HAS_POSITION_SQL` live beside it so a shot and the
  lines it is judged against share one origin.
- **Tracked in:** no action needed. The method is worth keeping: **where a
  column has a sibling that restates it — a described distance, a box score's
  attempts — fit against the sibling before trusting a constant.**

### Free throws carry a court position from 2002 to 2018

- **What ESPN does:** gives every free throw a position under the rim for those
  seasons, and stops doing so afterwards.
- **Evidence:** re-verified 2026-09-11 — free throws with coordinates: 26,092
  of 26,092 in 2002, 64,775 of 64,775 in 2010, 56,988 of 56,988 in 2018, then
  **93 of 60,813 in 2019 and 0 of 62,023 in 2026**. So "has coordinates" never
  excluded them: an unfiltered chart drew them as shots, and Curry's 2010 shot
  distance averaged in all 200 of his free throws among 1,343 "attempts".
  Separately, `(0, 0)` — a point on the sideline — is used for 7,109 of 2002's
  shots and counts as no position.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull.**
- **How we handle it:** free throws are excluded by value rather than by
  missing coordinates, and a free-throw chart is refused rather than drawn as a
  dot.
- **Tracked in:** no action needed.

### `dnp_reason` is set on players who played

- **What ESPN does:** fills the did-not-play reason on box rows for players who
  were on the floor, so the column does not mean what its name says.
- **Evidence:** re-verified 2026-09-11 — of 1,100,170 box rows, 464,814 carry a
  `dnp_reason` and **382,435 of those are rows where `did_not_play` is false**;
  361,335 have real minutes. "COACH'S DECISION" accounts for 451,282 of all
  such rows, at 22.7 minutes on average. `did_not_play` is the field that
  actually says whether a player sat.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull.**
- **How we handle it:** only `fetch/parse.py` touches the column; nothing reads
  it.
- **Tracked in:** ISSUES.md, "Columns that look wrong but that nothing reads"
  (#53).

### NULL minutes mean "did not appear", and NULL `plusMinus` is a subset of them

- **What ESPN does:** omits minutes rather than sending zero for a player who
  did not appear, and omits plus-minus for some but not all of those rows.
- **Evidence:** **re-measured 2026-09-11, and the previously recorded
  relationship does not hold.** `plusMinus` is NULL on 156,918 rows (14.26%)
  and `minutes` on 240,142. The NULL-plusMinus rows are a **strict subset** of
  the NULL-minutes rows, not the same set: 156,918 have both, **0** have a NULL
  plus-minus with real minutes, and **83,224 have NULL minutes but a real
  plus-minus**. `ISSUES.md` records these as "exactly the rows with NULL
  minutes", which is wrong in one direction. NULL minutes run 15-17% of rows in
  2004-2005 and 27-31% from 2006 to 2014.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull.**
- **How we handle it:** `conditions._played` narrows on minutes. Dropping
  NULL-minute rows raised 2009's games-played agreement from 30 to 378 of 445
  players.
- **Tracked in:** ISSUES.md, "Smaller game and box-score gaps, 1994-2003" (#14)
  and "Columns that look wrong but that nothing reads" (#53) — the latter's
  claim this entry corrects.

### Only six of the 21 fingerprint categories partition the total

- **What ESPN does:** publishes 21 play-type categories of which only six
  partition the season total; the other 15 are overlapping slices.
- **Evidence:** the six (`FINGERPRINT_PARTITION`) are two_pt, three_pt,
  free_throw, turnover, rebound and foul, and they sum to the season average
  almost exactly. Summing all 21 is meaningless.
- **Does a refetch fix it?** **Not applicable** — a taxonomy, not a fault.
- **How we handle it:** `FINGERPRINT_PARTITION` names the six.
- **Tracked in:** no action needed.

### The play-type split exists per game as well as per season, in a second file

- **What ESPN does:** publishes **two** objects per date —
  `NBA/netpts/<season>/<date>.json` and
  `NBA/netpts/<season>/<date>_player.json`. The first carries 57 fields per
  player-game with exactly three NetPoints values among them (offense, defense,
  total) and counting stats for the rest; the second is long format, one row
  per player per game per action type, 31 types covering every category the
  season file holds plus nine it does not (`atb`, `bank`, `dunk`, `grenade`,
  and the dead-ball ones).
- **Evidence:** the second file went unread for months. The season file's
  absence of a game id was read as "the source does not publish this per game",
  and the daily file's `assister` / `putback` / `corner`-shaped field **names**
  were read as the taxonomy — but their **values** are integer counts
  (`pts: 36`, `assister: 4`), not net points. The site's own per-game awards
  ("Facilitator" for net points passing, "Corner Pocket" for corner 3s) were
  visible evidence against that reading the whole time.
- **Does a refetch fix it?** **Yes — and it did.** The second file is now
  fetched behind `--include-net-points-daily`.
- **How we handle it:** `net_points_player_game_fingerprint` holds it, **long**
  rather than wide — one row per player per game per `category`, because the
  wide shape would be 93 columns and would change again the next time ESPN adds
  a category. Categories are normalized to the season file's own column
  prefixes on the way in, so one skill list drives both tables; the query side
  pivots.
- **Tracked in:** no action needed. Worth recording as a **method** failure:
  checking one file's schema and one file's field names, without checking a
  value against the season columns or looking at what the site's own page
  fetches, produced a confident and wrong claim about what exists.

### `teams` holds only the 30 current franchises

- **What ESPN does:** serves a current-teams endpoint with no historical
  franchise names, so a relocated or renamed team is unidentifiable from it.
- **Evidence:** `teams` has exactly 30 rows. The 1993 Charlotte Hornets are
  labeled "New Orleans Pelicans", and 1996 Vancouver appears as Memphis.
  Separately, seven team ids appearing in `games` are absent from `teams`
  entirely (see the placeholder/phantom entry).
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**
  (`teams` reproduced exactly, 30 rows).
- **How we handle it:** nothing. Historical games print under today's names.
- **Tracked in:** ISSUES.md, "Historical teams are shown under today's names"
  (#17).

### A player's bio is point-in-time, stored as if it were static

- **What ESPN does:** serves whatever jersey number, short name and position
  are current on the day the bio is fetched, with no effective date.
- **Evidence:** against the 2026-09-11 pull, **270 of 3,101 `players` rows
  differ** from the existing warehouse: 265 jersey numbers, 8 short names (Enes
  Kanter is now "E. Freedom", Jimmy Butler "J. Butler III", Kevin Knox
  "K. Knox II") and one position. The same reclassification moves 13
  `player_season_stats` rows and 11 of `player_season_stats_deduped` (`PG` to
  `G`, athlete 3907387). This is the **only** substantive difference the whole
  fresh-pull comparison found outside the NetPoints fingerprint name-matching.
- **Does a refetch fix it?** **Yes** — but it replaces one point-in-time value
  with another, so it is not a fix so much as a re-snapshot.
- **How we handle it:** `Pipeline._cache_player_bio` returns early when the
  file exists, so a bio is whatever ESPN said the day that player was first
  seen. No template reads these columns today.
- **Tracked in:** ISSUES.md, "A player's bio is fetched once and never
  refreshed" (#64).

### One stat key is described differently by two endpoints

- **What ESPN does:** describes the same `stat_key` differently depending on
  which endpoint returns it.
- **Evidence:** both warehouses hold 211 glossary keys and exactly one differs:
  `points` is label "Points", description "Total Points", source `standings` in
  the existing warehouse, and "PTS", "Points", source `player_box_stats` in the
  fresh one — whichever ran last wins.
- **Does a refetch fix it?** **No** — the outcome depends on pull order, not on
  freshness.
- **How we handle it:** `Pipeline.write_glossary` merges
  `{**existing, **self.glossary}`. Nothing reads the glossary.
- **Tracked in:** ISSUES.md, "The stat glossary keeps whichever source
  described a key last" (#65).

---

## Coverage floors

**Each table starts in a different year, and the gaps are ESPN's.** A question
is only answerable as far back as its *narrowest* table, and **there is no pull
that fills these in** — verified live against three endpoints (the game
summary, `core/.../plays`, and the athlete gamelog), all of which return empty
for the years below. `data check` reporting zeros there is correct.

"Usable" is deliberately not "present": 82 games where a league plays 1,100 is
rows, not a season.

| Table | Usable from | What is before it |
| --- | --- | --- |
| `standings` | 1988 | — league-wide and real all the way back (23 teams in 1988, 27 by 1990) |
| `games`, `team_box_stats` (postseason) | 1989 | — full brackets all the way back; the 1987-88 playoffs are not in ESPN's archive |
| `games` (regular), `player_box_stats`, `team_box_stats`, `team_season_stats` | **1994** | one team's 82 games per season, and nothing at all for 1989-90 |
| `player_season_stats` (one named player) | 1977 | nothing — but see the survivor-sample note below |
| `player_season_stats` (rankings) | **1994** | a survivor sample, not a league |
| `plays` | 2003 (2002 is ~half) | nothing |
| `shot_chart` | 2002, caveated for 2002 (509 of 1,190 games) and 2003 (986) | nothing |
| `team_power_index` | 2017 | nothing |
| `win_probability` | 2018 | nothing |
| NetPoints (all five tables) | 2019 | the bucket answers 403 |

Re-verified 2026-09-11: pre-1996 `games` holds 82 regular-season games in each
of 1988, 1991 and 1992 and **none at all** in 1989 and 1990, against 1,108 in
1994; `standings` starts at 1988 with 23 teams and reaches 27 by 1990; `plays`
holds 244,717 rows in 2002 against 469,429 in 2003; `shot_chart` covers 509 of
1,190 games in 2002, 986 of 1,190 in 2003 and 1,175 of 1,189 in 2004;
`team_power_index` starts at 2017 and `win_probability` at 2018; every
NetPoints table starts at 2019 except `net_points_team`, which holds **2026
only** and is inherent to the source.

Three traps in that table.

**`shot_chart` is derived from `plays`.** Both come out of the same game
summary, so there is no separate shot source to fetch for 2002 and earlier.

**Season 1993 is a phantom** — see the labeling section. Treat 1994 as the
earliest real regular season and 1993 as a copy of it, not as evidence of a
fetch bug.

**`player_season_stats` looks like an exception and is not.** It reaches back to
1977 because it is fetched per player over a whole career once that player is
discovered — and players are discovered from box scores, which start in 1994.
So the deep history is only the handful of careers that lasted into 1993-94.
Re-verified 2026-09-11, counting **distinct regular-season players**: **2 in
1977, 7 in 1980, 141 in 1988, 217 in 1990, 403 in 1994**. (`AGENTS.md`
previously gave "5 in 1977, 240 in 1988, 668 in 1994"; those are *row* counts
across both season types — confirmed: 5, 240 and 668 rows respectively — and
the distinct-player figures are the ones `coverage.py` uses.) It is a survivor
sample, not league-wide coverage: a leaderboard over it before ~1994 measures
who played longest. Kareem Abdul-Jabbar, Larry Bird and Julius Erving are not
in `players` at all, and before the floors existed "who led the league in
scoring in 1980" answered "Moses Malone, at 25.8. Next: Bill Cartwright (21.7)"
— drawn from a league of seven.

**A lookup and a ranking have different floors**, which is why the table lists
`player_season_stats` twice. ESPN holds Michael Jordan's real 1990 line, so his
own average is answerable; ranking that season is not.

These floors are **enforced, not just documented** — `association/coverage.py`
holds them as a table and `templates.check_coverage()` refuses a question that
lands under one. `scripts/check_coverage.py` verifies every floor against a
built warehouse. See `AGENTS.md` for the rules an agent must follow when adding
one.
