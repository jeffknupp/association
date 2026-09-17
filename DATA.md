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
- **Is it anywhere else on ESPN? No — four independent sources probed live on
  2026-09-14, and none has it.**
  - The **game summary** (what the pull reads) serves the zeros.
  - The **CDN box score**, `cdn.espn.com/core/nba/boxscore?xhr=1&gameId=...`, a
    different host and a different path, serves the *same* zeros. So the zeros
    are ESPN's, not an artifact of how `fetch/parse.py` reads the summary.
  - The **core API** exposes no per-athlete, per-game statistics at all.
    `events/{id}/competitions/{id}/competitors/{team}/roster` returns each
    player with `starter`, `didNotPlay` and `reason` but no statistics
    reference, and `.../roster/{athlete}/statistics` is a 404.
  - The **athlete gamelog**
    (`WEB_V3/athletes/{id}/gamelog?season=`) does not serve them empty — it
    **omits the games entirely**.
- **The gap follows the franchise, not the season or the player**, which the
  gamelog shows cleanly. Regular-season events it returns, 2013 through 2018:
  Anthony Davis (New Orleans throughout) 0, 1, 0, 1, 1, 1; **Derrick Rose 0, 0,
  0, 1, 61, 25**; **Aaron Brooks 51, 65, 0, 1, 60, 26**. Rose's and Brooks's
  zero years are exactly their Chicago years, and both have full gamelogs for
  the seasons they played elsewhere. LeBron James, on neither team, reads 70,
  73, 64, 71, 70, 77. Where our box scores are good the gamelog agrees with
  them to within a game, so it is a sound source that simply lacks this data
  too.
- **How we handle it:** `_empty_box_scores` (`query/templates.py`) counts the
  games a per-game answer could not see and says so; the caveat gives a count,
  not "every Bulls and Pelicans game". Since 2026-09-14 `single_game_high` also
  refuses to read these lines at all, because a stat column on them is `0`
  rather than NULL and the maximum over a wholly empty season was one of those
  zeros.
- **Tracked in:** ISSUES.md, "Nearly every Bulls and Pelicans box score from
  2013 to 2018 is zeros" (#1).

### ESPN files one player under two athlete ids in the same box score

- **What ESPN does:** lists the same person twice in one team's box score,
  under two different `athlete_id`s, sometimes with identical stat lines.
- **Evidence (2026-09-15):** grouping `player_box_stats` by `(event_id,
  team_id, display_name)` and counting distinct `athlete_id`:
  - **Isaiah Canaan** (`2490589`, `4412182`) in 20 Phoenix and Minnesota
    team-games in 2019, with identical lines in 18 of them.
  - **Corey Brewer** (`3191`, `4415554`) in 8 of 8 in 2019.
  - **Daryl Macon** (`4066243`, `4610145`) in 3 of 4 in 2020.
  - **Ken Johnson** 2003 (`1008`, `1972`, 33 games), with lines that differ.
  - The team box is not affected: its derived points (2·FGM + 3PM + FTM) equal
    the final score in every row of 2019, 2021 and 2026. The player sums
    overshoot in 23 team-games in 2019 — 15 Phoenix, 7 Philadelphia — which is
    most of the disagreement recorded under "Team box scores disagree slightly
    with player-box sums".
  - It crosses tables: `net_points_player` keys off ESPN's `dot_com_id`
    (`3059316` for Wayne Selden 2022) while the box scores and the name-matched
    fingerprint use the other id, so 8 `net_points_player_fingerprint` rows have
    no `net_points_player` row of the same id and season, and a join drops them.
- **Does a refetch fix it?** Not tested. The ids come back the way ESPN serves
  them, and both are real athlete records on its side.
- **How we handle it:** nothing yet. Anything summing player rows to a team
  total double-counts these games.
- **Tracked in:** ISSUES.md, "ESPN files one player under two athlete ids in the
  same box score".

### Vancouver 1996 is an empty TEAM box, not an empty player box

- **What ESPN does:** serves an all-NULL `team_box_stats` row for every
  Vancouver game in 1995-96, while serving that season's **player** box scores
  normally. This is a different fault from the Chicago and New Orleans entry
  above, where both tables are empty, and it was recorded here as the same one
  until 2026-09-14.
- **Evidence (re-measured on BOTH tables, 2026-09-14):**
  - `team_box_stats`: 82 of 82 Vancouver rows are all-NULL. The original entry
    was right about this half, and it was measured on this table alone.
  - `player_box_stats`: 936 rows across 78 of the 82 games, 12 rows a game —
    the league-normal roster size that season, since 2,189 of 1996's
    team-games have exactly 12 — and only 151 of the 936 rows lack minutes.
    **The player box is real.** For contrast, Chicago and New Orleans across
    2013-2018 have *zero* player rows with minutes.
  - Vancouver's box points total 7,030 against ESPN's own season table's 7,362.
    That gap is the 4 games absent from `player_box_stats` entirely, not a
    zeroed season.
  - Only **5** of 1,189 games in 1996 have no player box rows at all: 160127072,
    160207100, 160324082, 160329100 and 160405003. That is the "5 in 1996"
    figure this entry was written to overturn, and it was correct.
- **The Chicago pair was wrong, and is removed (2026-09-15).** Chicago 2000's
  82 all-NULL rows and 1999's 50 sit on **zero** `real_games` events: they are
  the 0-0 `T17:00Z` placeholder rows catalogued below, which `real_games`
  already drops. Chicago's real games carry normal team rows (80 in 2000, 50 in
  1999). Those seasons are not this fault.
- **Vancouver's opponents are hit too**, which had never been recorded: in 41
  of its 82 games the *opponent's* team row is all-NULL as well, and 37 of those
  sit beside real player rows, spread over 25 teams at 1-2 games each.
  League-wide, 115 all-NULL team rows on real 1996 games have real player rows
  beside them; with one 2000 game (`191102003`, ORL@NO) that makes the 117 the
  comment at `fetch/team_box_repair.py:108` counts.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**, which
  reproduced `team_box_stats` exactly.
- **How we handle it:** nothing yet, and `_empty_box_scores` does NOT catch
  these — it tests player minutes, which are present here — so a team-level
  answer for these three seasons carries no caveat at all.
- **Tracked in:** ISSUES.md, "Vancouver 1996 has an empty TEAM box, not an
  empty player box" (#67).

### The 2000 and 2001 playoffs stop before the Finals

- **What ESPN does:** its team schedules simply do not list the last rounds of
  those two postseasons, so the games are undiscoverable from the endpoint the
  pull walks. The rows are not wrong; they are absent.
- **Evidence, as first found (2026-09-11), before the recovery below fixed
  2000:** the 2000 postseason held 70 games and ended on 2000-06-01; the 2001
  postseason held 60 and ended on 2001-05-28. Missing from 2000: the whole
  LAL-IND Final (6 games), WCF LAL-POR games 6-7, ECF IND-NY game 6. Missing
  from 2001: the Final (5), ECF MIL-PHI games 5-7, WCF LAL-SA game 4, and 2
  games of MIL-CHA. The Lakers had 15 postseason games in 2000 and 10 in 2001,
  against 23 and 16 really played. Neighboring seasons are intact (1999 ends
  1999-06-26, 2002 ends 2002-06-13, 2003 ends 2003-06-15). **Re-measured
  2026-09-16, after the discovery-pass recovery: `real_games` now holds 75 for
  2000 and 61 for 2001, and the Lakers hold 23 postseason games in 2000 (all of
  them) and 11 in 2001 (still 5 short of 16).**
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**, which
  returned the same 70 rows for the 2000 postseason, ending on the same date,
  with `games` matching across all 43,494 rows.
- **How we handle it:** the daily scoreboard is a second, independent list of
  what was played, and it HAS the games the schedules drop - so a postseason
  pull makes a second discovery pass over it once the schedule's games are on
  disk, scanning forward from the latest date stored (`POSTSEASON_SCAN_DAYS`,
  28 days). That recovered all nine missing 2000
  games, including the whole LAL-IND Final. It recovers only ONE of 2001's:
  probed live through the project's own client, 23 days across that
  postseason's conference finals and Final return no events at all, so ESPN
  does not have them anywhere. `coverage.postseason_partial` declares the 2001
  postseason partial on both `games` and `team_box_stats` so answers say what
  is missing rather than stating a short series as fact.
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

### The career endpoint drops its totals category, unpredictably and in part

- **Re-measured 2026-09-14**, which rewrote this entry. It used to say the
  career endpoint returns the averages category and nothing else for these
  lines, permanently, and that a refetch does not fix it. Both halves were
  wrong; what was right is that 246 season lines carry `avgPoints` beside a
  NULL `points`.
- **What ESPN does:** its per-player career endpoint
  (`WEB_V3/athletes/{id}/stats?seasontype=`) publishes three categories —
  `averages`, `totals`, `miscellaneous` — and **two different things go wrong
  with the last two.**
  - **It sometimes serves the averages category alone.** Not per player and not
    permanently: **53** of the 107 affected files were stored that way (26
    columns instead of 51 — no `points` column at all), and re-requested on
    2026-09-14, **33 of those 53 now come back with all three categories**. The
    other 20 still do not. No file went the other way.
  - **Even a complete answer omits some lines from `totals`.** 62 of the NULL
    rows sit in files that DO have the category. **55 of those 62 scored
    nothing** (`avgPoints` 0 or NULL — a 1-2 game call-up), and the remaining
    **7 are traded players' combined rows**. Seth Curry's 2014 shows both: the
    `averages` category lists team 29, team 5 and the combined row, while
    `totals` lists team 5 alone, dropping his scoreless 1-game stint and the
    combined row with it.
- **Evidence:** 104 regular-season rows across 42 players and 142 postseason
  rows across 65 players have every counting total NULL, over 107
  (athlete, season_type) files. Seth Curry reads `avgPoints` 15.0 beside a NULL
  `points` for 2022, and all 18 of his regular-season rows from 2014 on are
  NULL-totaled. Lou Amundson (2007-2016) and David Wood (1989-1997) recur the
  same way, as do postseason lines for Nazr Mohammed (12 rows), Zach Randolph
  (9), Theo Ratliff (8) and Gabe Vincent (7). Six of the 246 are the all-NULL
  combined rows in the traded-players entry below.
- **Does a refetch fix it? On 2026-09-14, yes for 123 of the 246 rows — and
  that is ESPN changing, not the old measurement being wrong.** Re-requested
  live on 2026-09-14 and parsed with the real parser, the career endpoint
  serves a `totals` category for 87 of the 107 affected files; a 20-player
  sample taken independently the same day split 16 with totals, 4 without.
  **The earlier "no, proven by the 2026-09-11 fresh pull" was sound evidence
  for the day it was taken**, and was re-checked on 2026-09-14 against the
  warehouse that pull actually produced
  (`/home/jeff/association-fresh/nba.duckdb`): it holds `player_season_stats`
  with 29,780 rows and **the same 246 NULLs**, and 22 of its 23 objects match
  the live warehouse row for row. So that pull did fetch these files, and did
  get NULLs back. **The fact to carry forward is that this endpoint answered
  differently on two days three days apart** — a "does a refetch fix it"
  finding about it has a shelf life, and this one expired in three days.
- **A second endpoint has the rest**, found live 2026-09-14:
  `CORE_V2/seasons/{season}/types/{season_type}/athletes/{id}/statistics`
  answers with a real `points` for these lines, back to at least 1989 (David
  Wood 1991 → 432), and 404s only for the six all-NULL combined rows. It is a
  much wider source than the career endpoint — 112 stat names against the 51
  columns stored here, including `PER`, `RPM`, `ORPM`, `DRPM`, `VORP`, `WARP`
  and a whole `avg48*` family.
- **But it has no team dimension.** Keyed by (season, season_type, athlete)
  alone, so a player traded mid-season gets his COMBINED figure back against
  every one of his stint rows: all three of David Wood's 1995-96 stints (21, 4
  and 37 games) answer 208 points. It can fill a whole-season line; it cannot
  split one.
- **How we handle it:** `Pipeline._repair_season_totals` fetches the second
  endpoint for any line the career endpoint left totals-less, and
  `fill_missing_season_totals` matches on games played before using it — one
  row per season, refusing when two tie. 110 requests fill 226 of the 246 rows.
  The 20 left are 14 scoreless one-game stints and the 6 all-NULL combined
  rows.
- **Tracked in:** ISSUES.md, "246 season lines have NULL totals" (#5).

### The career endpoint drops its whole `miscellaneous` category for thin careers

- **What ESPN does:** serves `averages` and `totals` but no `miscellaneous`
  category for players with very little on record, so the line arrives without
  the ten fields that category carries.
- **Evidence:** of 4,937 career files on disk, **152 hold 41 columns** against
  the usual 51, and the ten they lack are exactly `doubleDouble`,
  `tripleDouble`, `technicalFouls`, `flagrantFouls`, `disqualifications`,
  `ejections`, `assistTurnoverRatio`, `stealTurnoverRatio`,
  `scoringEfficiency` and `shootingEfficiency`. All 152 keep `points`, so this
  is **not** the write truncation described in `CHANGES.md` ("A row narrower
  than the rows after it no longer truncates the whole file", fixed in
  `dc3e03a`, GitHub #80); it is the source omitting a category. 104 of the 152
  are postseason files, and the sample checked is a single 1-game 2021 line.
- **Does a refetch fix it?** Untested. It correlates with how little the player
  has on record, which suggests the source rather than the request.
- **How we handle it:** nothing needed. `union_by_name=true` at load means the
  missing columns read as NULL beside every other file's, and no template reads
  the ten.
- **Tracked in:** no action needed.

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

### ESPN's power index is a paged collection, and holds all 30 teams

**This entry said the opposite until 2026-09-15, and the correction is the
lesson.** It read "ESPN's power index keeps only postseason teams", on evidence
that `team_power_index` held exactly 25 rows a season. 25 is the page size of
ESPN's core API. The missing rows were never missing from ESPN.

- **What ESPN does:** answers `seasons/<s>/powerindex` as a *collection* —
  `{count, pageIndex, pageSize: 25, pageCount, items}` — and publishes one
  snapshot per season per season type rather than a dated series.
- **Evidence (probed live, 2026-09-15):** 2024 answers `count: 90,
  pageSize: 25, pageCount: 4`; with `limit=1000` it returns all 90. Across
  2017-2026 ESPN holds **630 rows, 30 teams in every snapshot**: preseason and
  regular season in 2017-18, regular only in 2019-21, regular and postseason in
  2022, and regular, postseason and play-in from 2023. ESPN's own rank columns
  still hold values like 26,058 before 2022.
- **What is still true:** there is no dated series. Each (season, season type)
  is written within a single calendar **day** and ESPN overwrites it. Not a
  single stamp, though — measured 2026-09-15, 7 of the 21 groups carry between
  2 and 9 distinct `lastUpdated` values, minutes apart, and the ranges of two
  snapshots on one day **interleave**: 2018's preseason runs 07:41Z-07:47Z and
  its regular season 07:39Z-07:48Z, both on 2020-10-12, the day ESPN
  backfilled them. So `max(last_updated)` is the only ordering that means
  anything, which is what the query uses, and the one-minute gap between the
  two maxima is what separates them. 2017's preseason carries 2019-11-22
  against its regular season's 2020-10-12. A snapshot's date says when ESPN
  last wrote it, not when it described.
- **Does a refetch fix it?** **Yes** — this was our read, not ESPN's data.
  `ESPNClient.get_collection` pages it, and
  `scripts/backfill_power_index.py` re-fetched 2017-2026.
- **How we handle it:** `team_outlook` names its snapshot, date and size in
  every answer, counts a team's standing within the snapshot rather than
  trusting ESPN's rank columns, and tells a missing team which snapshots exist.
- **Tracked in:** ISSUES.md, "The power index has no dated series" (#27).

### No birth dates anywhere, and conference membership is fetched but discarded

**Corrected 2026-09-15.** This section used to say ESPN publishes no
team-to-conference mapping through any endpoint the pull reads. That is wrong:
the standings response the pull already fetches is grouped by conference, and
the parser throws the grouping away. The birth-date half stands.

- **What ESPN does:** returns `conferenceCompetition` as false on every game
  (so the per-game flag really is useless), **but** serves standings as a tree:
  `standings?season=2026` has children `Eastern Conference` (15 teams) and
  `Western Conference` (15); 2004 gives 15 and 14, 1990 gives 13 and 14; and
  `&level=3` returns the six divisions at 5 teams each. It publishes no birth
  date through any endpoint the pull reads.
- **Evidence:** `games.conference_game` is False on all 43,504 rows.
  `parse_standings` (`fetch/parse.py:342`) walks `children` only to reach the
  entries and keeps no group name — its own test says the entries "appear at
  both conference and division level" (`tests/fetch/test_parse.py:413-414`).
  `standings` also carries "vs. Conf." and "vs. Div." records, real from 2004.
  No table holds a birth date.
- **Does a refetch fix it?** **For conference and division, yes** — a parser
  change plus a standings re-pull, no new endpoint. For birth dates, no.
- **How we handle it:** `_conference_refusal` refuses a conference named as the
  subject. Anything by age is refused or falls through.
- **Tracked in:** ISSUES.md, "Conference and division are in the standings we
  fetch, and the parser drops them" (#25) and "Shapes deferred for lack of data
  or logic" (#32).

### The athlete gamelog is a second source for per-game lines, and it counts All-Star games

- **What ESPN does:** `WEB_V3/athletes/{id}/gamelog?season=` returns one row
  per game for a player, carrying exactly the box line we store — MIN, FG,
  FG%, 3PT, 3P%, FT, FT%, REB, AST, BLK, STL, PF, TO, PTS — grouped under
  `seasonTypes`. Nothing in the pull reads it today; box lines come from the
  game summary instead.
- **Evidence it agrees with us:** for players on unaffected teams its
  regular-season counts match `player_box_stats` to within a game (LeBron James
  2013-2018: 70, 73, 64, 71, 70, 77 against our 69, 72, 63, 70, 69, 76 — the
  difference is the All-Star game, below). So it is a usable cross-check, and a
  candidate source if the summary is ever wrong in a *recoverable* way.
- **Two quirks to know before reading it.**
  - **It files the All-Star game under the regular season.** Anthony Davis's
    stray "regular-season" events in 2014, 2016, 2017 and 2018 are All-Star
    games, including `400935635`, his 52-point record. None of them is in our
    `games`, and they should not be: their team ids (31 and 32, Eastern and
    Western Conf All-Stars) are the same non-franchise ids that make the
    phantom rows in "`games` carries placeholder, duplicate and phantom rows".
  - **The `seasontype` parameter is ignored.** `?season=2015&seasontype=2`
    returns byte-identical JSON to `?season=2015`, as do `seasonType`, `type`
    and `split`. The only way to select is to read the `seasonTypes` array out
    of the response.
- **Does a refetch fix anything with it?** For the empty 2013-2018 Chicago and
  New Orleans box scores, **no** — it omits those games entirely (see that
  entry).
- **Tracked in:** nothing yet; recorded so the next reader does not have to
  re-probe it.

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
  | `fieldGoalPct` | FT% | 2,134 / 2,134 |
  | `freeThrowPct` | 3P% | 2,134 / 2,134 |
  | `fouls` | flagrant fouls | — |
  | `technicalFouls` | flagrant fouls | — |
  | `pointsInPaint` | nothing: **-1** | 2,134 / 2,134 |

  `assists` equals the real assist sum in 1 row of 2,134. No turnover or foul
  column is right either: `turnovers` averages 0.585 a game, `totalTurnovers`
  1.181 (and equals the player-box figure in 1 row of 2,134), `fouls` 0.043
  against a real 19.99. FGM, FGA, 3PM, FTM and rebounds are correct, and so is
  `threePointFieldGoalPct` — which is why 3P% appears twice in the row and FG%
  not at all. The control seasons prove it is ESPN and not a parser column
  order: in 2017, `assists` is the real assist sum in 2,134 of 2,134 rows, and
  in 2019 in 2,436 of 2,460 — the same code, the same column order, the right
  values.

  **Re-measured 2026-09-14, and the block is wider than first recorded.** The
  league means put each displacement beside its neighbors (2017 → 2018 → 2019):
  `technicalFouls` and `totalTechnicalFouls` 0.646 → **0.037** → 0.639, which is
  a flagrant-foul magnitude rather than a technical one; `pointsInPaint` 43.455
  → **-1.000** → 48.600, a sentinel in every row rather than a displaced value.
  The postseason moves identically (144 of 146 rows): `assists` 22.074 → 5.000 →
  23.012, `blocks` 4.716 → 20.740 → 4.872.

  **Two columns in the block that nothing proves wrong.** `teamTurnovers` means
  0.596 against 0.586 in 2017 and 0.548 in 2019, and the value standing in
  `turnovers` (0.585) matches *it* rather than any other statistic — the
  simplest reading is that the team-turnover value was written to both names.
  `fastBreakPoints` means 11.933 against 13.045 and 13.797, low but inside
  normal drift. Neither can be checked against the player box, which has
  neither statistic. `totalTurnovers` is not in this group: ESPN keeps it equal
  to `turnovers + teamTurnovers` in all 2,134 rows (and in every other season),
  so it inherits the wrong half and means 1.181 against a real ~14.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**, which
  reproduced `team_box_stats` exactly.
- **How we handle it:** corrected at load by
  :mod:`association.fetch.team_box_repair` — the five summable columns are
  re-derived from the game's own player rows (a team's assists ARE the sum of
  its players'; ESPN's own column equals that sum in 2,134 of 2,134 rows in
  2017), the two percentages are recomputed from the made/attempted columns
  beside them, and the columns with no source become NULL rather than keep
  another statistic's number. An empty team-game is left exactly as stored.
- **Tracked in:** ISSUES.md, "2018 team box scores have values under the wrong
  column names" (#8) — fixed in code, awaiting a backfill load.

### The team box `turnovers` column is zero before 2013

- **What ESPN does:** leaves the team box `turnovers` column at 0 and copies
  `totalTurnovers` into `teamTurnovers` for every season up to 2012. Only
  `totalTurnovers` carries a usable number.
- **Evidence:** measured per season over non-empty regular-season rows,
  `turnovers = 0` in 2,322 of 2,322 rows in 2000, 2,459 of 2,460 in 2011, 1,976
  of 1,980 in 2012 — and in **0** of 2,126 rows in 2013 and 0 of 2,134 in 2014.
  The changeover is exactly at 2013. `teamTurnovers = totalTurnovers` in the
  same rows, season for season.

  **`totalTurnovers` itself is right in that era**, measured 2026-09-14: it
  runs 0.549 a game above the player-box turnover sum in 1994 and 0.585 in
  2000, which is what the team turnovers it includes are worth in a season
  where the columns work (0.590 in 2013, 0.585 in 2017, 0.702 in 2026). So only
  two of the three turnover columns are wrong here — unlike
  `team_season_stats`, where the same shape does inflate the season total (see
  "ESPN's `possessions` counts every turnover twice before 2013"). The two
  tables need opposite rules, which is why each states its own.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull.**
- **How we handle it:** two places, for two tables. In `team_box_stats`,
  :mod:`association.fetch.team_box_repair` sums `turnovers` from the player
  rows at load (13.59 a game in 2011, against a stored 0.002) and clears
  `teamTurnovers`, leaving `totalTurnovers` alone. In `team_season_stats`,
  `team_metrics` recomputes possessions as FGA - OREB + TOV + 0.44 x FTA using
  the turnover column that is right in each era, rather than trusting ESPN's
  own `possessions`.
- **Tracked in:** ISSUES.md, "2018 team box scores have values under the wrong
  column names" (#8), which covers the pre-2013 turnovers under its second
  bullet — fixed in code, awaiting a backfill load.

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

### 2008's team rebound columns hold something other than rebounds

- **What ESPN does:** serves the 2008 team box line with `offensiveRebounds`,
  `defensiveRebounds` and `totalRebounds` all wrong, while every other column in
  the row is right. A different fault from 2018's, in a different season, and
  rebounds only.
- **Evidence:** measured 2026-09-14 over non-empty regular-season rows.
  `offensiveRebounds` equals the player-box sum in **188 of 2,460** rows,
  against 2,458 of 2,460 in 2007 and 2,454 of 2,460 in 2009;
  `defensiveRebounds` in **1 of 2,460**, against 2,453 and 2,458. The means say
  the same (2007 → 2008 → 2009): OREB 11.122 → **8.364** → 11.039, DREB 29.933
  → **11.200** → 30.256, totalRebounds 49.638 → **61.543** → 49.470. The player
  rows are fine — the player rebound sum is 41.98 a game, between 2007's 41.05
  and 2009's 41.29 — and 2008's `assists`, `steals`, `blocks` and `fouls` each
  match their player sums in 2,443 to 2,460 of 2,460 rows, so nothing else in
  the row moved. The team total sits **19.57** a game above the player sum,
  against about 8.2 in every neighboring season — and the arithmetic is exact:
  `totalRebounds` equals the player rebound sum plus the row's own stored OREB
  and DREB in **2,452 of 2,460** rows, so whatever those two columns hold is
  being added to the total on top of the players' rebounds. The **postseason is
  clean** (172 rows: 49.55 total, 11.08 offensive, 29.58 defensive), so the
  fault is the 2008 regular season alone.
- **What the stored columns hold.** `defensiveRebounds` is the team's real
  OFFENSIVE rebounds - it equals the player-box offensive sum in **2,458 of
  2,460** rows. `offensiveRebounds` is the rebounds credited to the team rather
  than a player: it means **8.36** a game, against a total-minus-players gap of
  8.59 in 2007 and 8.18 in 2009, and it matches a play-by-play count of
  player-less rebounds in 461 of 2,454 rows, the same weak rate the gap itself
  manages in 2007 (418) and 2009 (507). So the total is the players' rebounds
  plus the team's, plus the offensive boards a second time.
- **Does a refetch fix it?** **No.** `271107026` refetched on 2026-09-16 still
  serves Cleveland `totalRebounds` 70, `offensiveRebounds` 8,
  `defensiveRebounds` 15 - and even the box score's own totals line reads
  REB 23 / OREB 8 / DREB 15, beside player rows summing to 47 / 15 / 32.
- **How we handle it:** `fetch/team_box_repair.py` rebuilds the three columns
  at load time for the 2008 regular season: the splits from the player sums,
  and `totalRebounds` as the player rebound sum plus the stored
  `offensiveRebounds` (the team figure), so 2008 follows the same definition
  as the seasons either side. Rebuilt means: OREB 11.20, DREB 30.78, total 50.34.
- **Tracked in:** no action needed - repaired at load time.

### 1990 Finals Game 5 is served with the wrong home team and winner

- **What ESPN does:** serves `100614008`, Detroit's 92-90 title-clinching win
  at Portland on 14 June 1990, as Detroit at HOME scoring 90 and Portland the
  winner with 92.
- **Evidence:** summary refetched 2026-09-16: `home DET 90 winner False`,
  `away POR 92 winner True`, no linescores, no venue. The stored `games` row
  matches it field for field, so it is not the parser. The 1990 Finals reads
  3-2 from the stored winners against a real 4-1; no other Finals from 1989 to
  1996 is miscounted. 1988-1992 has no box scores or plays to cross-check
  against. The home/away flags, scores and winner flag agree with each other
  (a 90-92 away win); only the two team ids sit on the wrong competitors, and
  the event id encodes Detroit as home the same wrong way.
- **Is it the only one?** Every postseason pairing in `real_games` (570 series)
  was checked on 2026-09-16 for a series that ends when one team reaches the
  wins it needs and for home games in that era's format (2-2-1 first rounds
  through 2002, 2-2-1-1-1, 2-3-2 Finals 1985-2013). This game is the only
  failure outside 2001, whose failures are all games ESPN is missing. The
  1988-1992 regular seasons cannot be checked the same way: `real_games` holds
  only 28 of their games.
- **Does a refetch fix it?** **No** (above).
- **How we handle it:** `fetch/game_repair.py` swaps the two team ids at load
  time (and `team_box_stats.home_away`), keyed on the event id and only while
  the row still holds what ESPN serves.
- **Tracked in:** no action needed - repaired at load time.

### The team `totalRebounds` column stops including team rebounds in 2022

- **What ESPN does:** through 2020 the team box `totalRebounds` is the players'
  rebounds plus the team's own — the ones credited to no player; from 2022 it is
  exactly `offensiveRebounds + defensiveRebounds`, with the team rebounds gone.
  2021 is half and half. The column name does not change, so nothing marks the
  season the definition moved.
- **Evidence:** measured 2026-09-14. `totalRebounds` equals the player rebound
  sum in 0 to 3 of about 2,200 non-empty regular-season rows in every season
  from 1993 to 2018, then in 333 of 2,460 (2019), 314 of 2,118 (2020), 1,120 of
  2,160 (2021), and **2,460 of 2,460** in 2022, 2023 and 2024. The gap between
  the team figure and the player sum closes accordingly: +8.07 a game in 2018,
  +7.28 in 2019, +7.10 in 2020, +3.66 in 2021, **+0.00** from 2022.
  `team_season_stats` moves the same way — 53.17 rebounds a game in 2020, 49.00
  in 2021, 44.45 in 2022 — so this is ESPN's convention changing, not the box
  scores alone.
- **Does a refetch fix it?** **No** — a change of definition, not a bad value.
- **How we handle it:** nothing yet.
- **Tracked in:** ISSUES.md, "A team's rebounds are not comparable across 2021
  and 2022".

### `pointsInPaint` is -1 before 2009, and two lead columns exist only in 2026

- **What ESPN does:** fills the team box `pointsInPaint` with **-1** rather than
  leaving it empty, for every season through 2008 and again for all of 2018.
  `leadChanges` and `leadPercentage` are columns it has only just started
  publishing.
- **Evidence:** 41,417 team rows hold `pointsInPaint = -1` — every non-empty row
  from 1993 to 2008 (2,358 in 1994, 2,632 in 2008) and every one of 2018's
  2,280. `fastBreakPoints` and `turnoverPoints` never carry the sentinel.
  `leadChanges` and `leadPercentage` are non-null in 2,018 of 86,988 rows: 2 in
  2020 and 2,016 in 2026. Note the two tables use different sentinels for the
  same gap — `team_season_stats` uses 0 for the same era (see the docstring of
  :mod:`association.query.team_metrics`), `team_box_stats` uses -1.
- **Does a refetch fix it?** Not tested.
- **How we handle it:** 2018's are set to NULL by
  :mod:`association.fetch.team_box_repair`, because that season's whole block is
  displaced. Nothing handles the pre-2009 ones.
- **Tracked in:** ISSUES.md, "`pointsInPaint` is -1 for every team-game before
  2009".

### ESPN's career endpoint copies regular seasons into the postseason

- **What ESPN does:** files some regular seasons a second time as that player's
  postseason, producing playoff lines for runs that never happened.
- **Evidence:** Eddy Curry has 527 "playoff games" across 11 postseason rows in
  `player_season_stats` — seven regular seasons each filed twice. Across the
  table, the deduplicating view drops **436 of 7,941 postseason rows**, which
  is **340 of 7,845 player-seasons**. The two units are easy to conflate, and
  were: re-measured 2026-09-11 and again 2026-09-16, 436/7,941 is the row
  figure and 340/7,845 the player-season figure.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**, which
  reproduced `player_season_stats` exactly apart from 13 `position` values.
- **How we handle it:** `player_season_stats_deduped` drops a postseason line
  claiming more than 28 games (four best-of-seven rounds) or repeating that
  season's regular-season games and points exactly. Every real run checked was
  kept. **Read the deduped view for postseason lines; the raw table still has
  them.**
- **Tracked in:** no action needed. The unit-confusion docs bug this pointed to
  was ISSUES.md #43, "Three wrong statements in the docs," fixed 2026-09-16 (see
  ISSUES.md #93's note that both comments now give the re-measured 436/7,941
  and 340/7,845 figures); the dedup itself needed no further action either way.

### Traded players' combined season rows disagree with their own stints

- **What ESPN does:** returns a combined (team-less) season line for a traded
  player that does not equal the sum of that season's stints — sometimes
  copying a single stint, sometimes all NULL.
- **Evidence:** re-verified 2026-09-11: of 2,062 combined rows (`team_id`
  NULL), **26** disagree with the sum of that season's stints. 6 are entirely
  NULL (Moses Malone 1977, James Edwards 1978 and 1983, Bill Laimbeer 1982,
  Danny Schayes 1983, Sleepy Floyd 1983). 12 from 1996 copy a single stint,
  losing 3,042 points between them — Eric Murdock's combined row reads 9 games
  and 62 points against stints totaling 73 and 647. Jevon Carter 2023 drops a
  1-game stint. 7 have NULL points because a stint's totals are NULL.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**, which
  reproduced every one of these rows.
- **How we handle it:** before 2026-09-15, `player_season_stats_deduped` and
  the leaderboard's `dedup_traded` both *preferred* the combined row, which
  meant the wrong line was the one that showed. `fetch/season_totals_repair.py`
  now rebuilds a combined row from its own stints at load time wherever the
  two disagree, so every reader sees the summed line rather than ESPN's. 19
  rows as of 2026-09-15. `avgMinutes` is NULLed on a rebuilt row: it has no
  season total behind it and both approximations were fitted and rejected (81%
  and 61% exact).
- **Tracked in:** no action needed — repaired at load time. ISSUES.md, "One
  rule, two hand-maintained copies: the traded-player dedup" (#83) tracks the
  separate risk that the "prefer the combined row" rule is still hand-written
  in two other places (`player_season_stats_deduped` and `dedup_traded`).

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
  - **Phantoms that carry a winner** — a date-only stamp, no PLAYER box rows,
    often a team id missing from `teams`: `131205075` (team 75, filed under
    *both* 1993 and 1994), `150611014` (MIA-ORL, actually Houston's 1995 Finals
    Game 3, and Miami has no 1995 postseason), `170429031` and `170501031`
    (team 31), `171209083` (team 83), `190612021` (PHX-POR),
    `200422100`/`200424100`/`200505100` (team 100) and `200501028` (a copy of
    TOR-NY). They make 1994 and 1998 one game long against `team_season_stats`,
    by exactly 225 and 175 points.
  - **A date-only stamp is `T04:00Z` OR `T05:00Z`** — midnight US Eastern under
    EDT and under EST. This entry read "`T04:00Z`" until 2026-09-14, which is
    four of the eleven phantoms short: `131205075`, `171209083` and the two
    1992 team-75 rows are winter games and carry `T05:00Z`.
  - **The date-only stamp is not only a phantom marker.** ESPN stores every
    1988-1992 game (and 12 more in 2000-2001) with no real tip time, at
    midnight Eastern, and for these the written date genuinely IS the game
    date - re-measured 2026-09-16, `real_games` holds **379** `T04:00Z` rows in
    1988-1992 alone (1988 RS 11 / PO 62, 1989 PO 72, 1990 PO 68, 1991 RS 8 / PO
    73, 1992 RS 9 / PO 76). A fixed five-hour shift meant for a real tip time
    moved every summer (`T04:00Z`, EDT) one to the day BEFORE, and did so until
    2026-09-16: `season.py` now reads the real Eastern clock, which puts all
    391 (these 379 plus 2000-2001's 12) on their written date - 318 of 318
    whose old-format event id encodes the date agree with it. The same stamp
    in 2026 is not date-only: its ten `T04:00Z` rows all fall in November-March,
    where that is a real 11pm EST tip, and the clock dates them the day before.
  - **Every `games` row has `team_box_stats` rows**, phantoms and placeholders
    included — measured 2026-09-14, 43,494 of 43,494 (both figures now read
    43,504 of 43,504, +10 since the 2000 playoff discovery pass, `72b599c`,
    which added real games rather than junk ones and did not change the ratio).
    Only the PLAYER box is
    absent. This entry used to say the team `game_log` was safe "because it
    joins `team_box_stats`, which the phantoms lack"; that was wrong on its
    facts, and the log listed them. The 302 `team_box_stats` rows belonging to
    the 151 junk rows are **entirely NULL** — 0 of 302 carry rebounds or field
    goals — so nothing that SUMS that table was ever inflated by them; what
    they did was make the rows exist for a join to find.
  - **No other table has rows for them.** `player_box_stats`, `plays` and
    `shot_chart` hold **0** rows against the 151 dropped events, which is why
    every player-level path was already immune.
  - **"No box score" is not by itself evidence of a phantom.** All 595 games
    from 1988-1992 are stored date-only and have no player box score, because
    ESPN publishes none before 1993-94 — and 30-odd real games from 1994 on
    have no box score either (the whole 1997 ECF, the 1995 Finals Game 5, 1996
    SAC-SEA, 1998 UTAH-HOU). Every one of those real games carries a real tip
    time; only the phantoms carry both a date-only stamp and no box score in a
    season that has box scores.
  - **Team ids absent from `teams`**, re-counted 2026-09-11: `1202` (7 rows,
    1999-2000), `75` (6 rows, 1992-1994), `1300` (3, 1999-2000), `100` (3,
    2000), `31` (2, 1997), `125` (1, 1988), `83` (1, 1998).
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**, which
  reproduced `games` exactly — placeholders, duplicates and phantoms included.
- **How we handle it:** one shared filtered list. `real_games`
  (`fetch/real_games.py`) is built at load time and keeps 43,343 of the 43,494
  rows (now 43,353 of 43,504, +10 each since the 2000 playoff discovery pass,
  `72b599c` — the 151-row gap is unchanged); `head_to_head`, `conditions`,
  `team_metrics.TEAM_GAMES_SQL`, the team
  `game_log` and `team_quarter_points` all read it instead of `games`. It does
  NOT collapse season 1993, which is a phantom SEASON rather than a phantom row
  — that stays with `coverage.py` and the cross-season `QUALIFY` in
  `TEAM_GAMES_SQL`. Until 2026-09-14 each path filtered a different subset:
  `TEAM_GAMES_SQL` dropped placeholders and same-day duplicates but kept every
  phantom that had a winner, `conditions` filtered only on
  `winner_team_id IS NOT NULL`, and `head_to_head` and the team `game_log`
  filtered nothing.
- **Tracked in:** ISSUES.md, "The SQL agent and the web health line still read
  raw `games`" — what remains after the load-time list.

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
  game's own Eastern date off the timestamp (`season.eastern_date`, the real
  Eastern clock - it was a fixed five-hour shift, which is identical for every
  real tip and wrong only for date-only stamps), does **one exact lookup** since a team
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
- **But the ids are stable, because they belong to the FRANCHISE.** Measured
  2026-09-16 from the city each team's home games were played in: id 17 plays
  in East Rutherford and Newark, then Brooklyn from 2013 - one id, New Jersey
  and Brooklyn Nets alike. Id 25 moves from Seattle to Oklahoma City in 2009.
  Id 3 is in Charlotte through 2002, New Orleans from 2003 (Oklahoma City for
  2006 and 2007) - the Charlotte Hornets, New Orleans Hornets and Pelicans are
  one id. Id 30 first appears in 2005 and id 29 in 1996, their expansion years.
  So every game is filed under the right franchise; only the NAME is missing.
  The three pure renames (Bullets to Wizards, Hornets to Pelicans, Bobcats to
  Hornets) moved no arena, so nothing in the warehouse records their years.
- **Does a refetch fix it?** **No, proven by the 2026-09-11 fresh pull**
  (`teams` reproduced exactly, 30 rows).
- **How we handle it:** `entities.FRANCHISE_ERAS` lists every name the renamed
  and relocated franchises have carried, with the seasons they carried it, and
  team names are resolved for the season asked about. That matters more than
  it sounds: "Hornets" moved BETWEEN franchises, so matching today's names
  answered "Hornets record 2008" with the 2008 Charlotte Bobcats (id 30),
  where the Hornets that season were New Orleans (id 3). Every team name an
  answer prints is its name for the season of that row - the named team, a game
  log's opponents, standings, streaks, a matchup log's abbreviations, and the
  `player_game_log` view's `team_abbr`/`opponent_abbr` - through
  `franchises.season_name` and `season_name_sql`. The one exception is the SQL
  agent's own example queries in `query/prompt.py`, which print whatever
  `teams` holds, because changing them spends the preamble's token budget.
- **Tracked in:** nothing open; #17 was closed by the fix.

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
