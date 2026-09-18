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
- **Next step:** decide whether the fall-through survives in its current form.
  It is inconsistent with `check_coverage`'s own stated reasoning - refusing
  because "the agent would query the same empty tables, more slowly, and is
  then free to fill the silence from its own weights" - which is exactly what
  it was measured doing. Gating it to the question shapes it can actually serve,
  or replacing it with a refusal that names the shape, both beat the status quo.
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
  anchor were perturbed with `scripts/perturb.py` and CAUGHT. **Still open:**
  re-running `scripts/check_routing.py` and the full `fastpath_feed.py` replay
  against ollama, which this agent was told not to run - the dispatching
  session is running that confirming measurement separately.
- **Note:** the same substitution is worth checking for every stat name the
  router does not know. "ats okc" and "players with the highest scoring triple
  doubles" are graded `wrong metric` in the same corpus.

The 391 date-only games printed a day early (#76), 2008's team rebound columns
(#74) and the swapped 1990 Finals Game 5 were fixed on 2026-09-16. Before adding
to this section, re-read the P2s against the P1 definition: that is how both of
those were found.
- **GitHub:** #114

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

### A calendar date on `player_stat` or `head_to_head` falls through instead of answering the game
- **Found:** 2026-09-16, issues audit, from the latest replay; **narrowed
  2026-09-18** - `head_to_head`'s half of this is fixed, see below
- **Evidence:** "Bam adebeyo jan 19" routes to `player_stat` with
  `date="2023-01-19"` resolved, and falls through on `player_stat cannot
  honor ['date']`. `game_log` honors `date` (`query/templates/common.py`), and
  `player_stat` already declines a `limit` as a `game_log` question
  (`query/templates/players.py`, falling through rather than redirecting) and has no
  equivalent for a `date`. (The 2023 in the first example is itself the model's
  invention - see the next entry.)
- **User sees:** a slow agent answer for a question a template answers
  exactly, and no "did you mean Bam Adebayo?", since `check_scope` refuses
  before the name is resolved.
- **Next step:** in `route()`, send a `player_stat` with a resolved `date` to
  `game_log` with a limit of 1, the same way `CODE_ASSIGNED_INTENTS` handles a
  period.
- **Fixed 2026-09-18 (head_to_head half):** `head_to_head` now honors `date`
  (`HONORED_SCOPING`, `query/templates/games.py`) the same way `game_log`'s own
  `date` replaces the season rather than being filtered inside it - "celtics
  record vs sixers on november 11" now answers "The Boston Celtics and the
  Philadelphia 76ers met once on 2025-11-11; the Philadelphia 76ers won the
  series 1-0." `player_stat`'s half (`query/templates/players.py`) is
  untouched and still falls through.
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

### `split="starter_bench"` reaches `game_log`/`period_split` with no direction, so it stays refused
- **Found:** 2026-09-18, fixing `player_matchup`'s and `head_to_head`'s
  `check_scope` refusals in `query/templates/games.py`
- **Evidence:** `router.SPLIT_WORDS["starter_bench"]` matches either
  "starter"/"starting"/"starts" OR "bench"/"reserve" in one pattern, and
  `router.py:1079` (`slots["split"] = splits[0]`) records only the category
  name, "starter_bench" - never which word actually matched. `player_splits`
  is built around that: with `split="starter_bench"` it shows BOTH groups
  side by side (`_player_splits_answer`, `query/templates/splits.py`), which
  is a legitimate way to honor a directionless slot. `game_log` and
  `period_split` cannot do the same thing usefully - "Jrue holiday last 50
  games as a starter" wants exactly his starts filtered in, not his last 50
  games (both starts and bench appearances) with a column added - and
  `TemplateContext` carries no raw question text for either template to
  recover which word was actually used the way `router._validate_venue`
  recovers "home" vs "away" for `venue`. `player_box_stats.starter` (boolean)
  exists and would support the filter once the direction reaches it.
  Five reasonable feed queries hit this: "Jrue holiday last 50 games as a
  starter", "kyle kuzma last 50 games as a starter", "taurean prince game log
  as a starter" (`game_log`), "zach collins first quarter stats last 5 games
  as a starter log", "barlow stats in the second half this season while
  starting vs pacers" (`period_split`).
- **User sees:** a fall-through to the slow agent for all five, rather than a
  filtered log or a refusal.
- **Next step:** two options, and the first is cheaper. (a) Read the
  direction from the question text the same way `_validate_venue` does for
  `venue`, into a new slot (e.g. `slots["starter"] = True/False`) in
  `router.py`, then filter `game_log`/`period_split` on
  `player_box_stats.starter`. (b) Extend `game_log` to show both groups the
  way `player_splits` does, narrowed and counted separately - more work, and
  answers a broader question than the one asked ("his last 50 games, split by
  starter/bench" rather than "his last 50 starts") unless very carefully
  worded. Not attempted here: guessing the direction (assuming "starter_bench"
  always means "starter", since every measured example says so) was
  considered and rejected - a "bench" question asked the same way would get a
  fluently wrong answer, the failure mode this project ranks worst.
- **GitHub:** not yet filed
- **GitHub:** #119

### A weekday or holiday `situation` narrowing falls through to the slow agent instead of a fast refusal
- **Found:** 2026-09-18, reading `/home/jeff/association-research/statmuse-2026-09/query_set_audit.md`
  while judging `game_log`'s `situation` refusals
- **Evidence:** `situation` is deliberately unhonored by every template
  ("Each narrowing the router has no slot for needs its own regex", above),
  so a weekday-shaped question - "jamal murray career games on Tuesdays" - hits
  `check_scope`, raises `TemplateUnsupported`, and falls all the way through to
  the slow SQL-writing agent (`agent.py`'s `except TemplateUnsupported:
  return None`). The audit that catalogued all 261 feed queries flags 8 of them
  as a weekday split, calls all 8 low-value, and says explicitly: "the owner
  should know the feed keeps asking: if the decision is 'never', it is worth a
  refusal that says so rather than a fall-through that spends 55 agent-seconds."
  The same reasoning applies to the two other `game_log` `situation` rows
  judged here: "paul reed gamelog with 25 minutes" (the audit's own #103,
  flagged ambiguous - "at least" vs "exactly" 25 minutes, so answering it would
  be a guess) and "forwards with 20+ mins vs gsw log" (a position-group
  subject, a different missing shape entirely, not a single-player or
  single-team question `game_log` has any way to answer).
- **User sees:** a ~55-second wait for an answer that, for the weekday case,
  the system could know instantly it cannot give.
- **Next step:** a fast, worded refusal for a `situation` shape that is known
  never to be answerable (weekday, holiday) needs a check that runs BEFORE
  `check_scope`'s raise sends the question to the agent - `check_scope` itself
  only ever raises (never returns a refusal `TemplateResult`), by design, so
  it is the wrong place to add one. This is a router/agent-level mechanism
  change, not a `games.py` template fix, and was not attempted here for that
  reason - `HONORED_SCOPING` is documented to mean "actually filters by it and
  says so," and a refusal is neither.
- **GitHub:** not yet filed
- **GitHub:** #120
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

### Only 7 of 24 templates render as anything but a `<pre>` block; the rest are plain text with structured data sitting unused beside them
- **Found:** 2026-09-18, scoping study requested ("richer HTML answers - cards,
  sparklines, tables"). Checked first: no open or closed issue on this repo
  covers it; the nearest neighbors are #69 (above) and the chart renderers
  (`court.py`, `radar.py`), which already draw `shot_chart` and `fingerprint`
  as standalone HTML artifacts.
- **What already exists, so this is "extend a pattern" and not "build one."**
  `web/static/index.html`'s `RENDERERS` table (`index.html:500-585`) already
  turns `Answer.data` into a table, a ranked list, a two-column comparison, a
  season-by-season sparkline, or a W-L record card, for exactly seven intents:
  `leaderboard`, `threshold_count`, `single_game_high`, `player_history`
  (table + sparkline), `player_compare` (table, with a NetPoints section that
  appears only when the data has one), `game_log`, `team_record` (a card). Two
  more intents, `shot_chart` and `fingerprint`, get a chart drawn server-side
  and shown in a sandboxed iframe (`chartFrame()`, `index.html:601-633`). Every
  other intent - 15 of 24 - falls through `renderBody()`
  (`index.html:680-686`, "no renderer, or a key it needs is missing" both read
  `null`) to `el("pre", null, a.text)` (`index.html:753`): literally the raw
  sentence in an unstyled `<pre>`, which is the "raw unstyled terminal text"
  the request asked to fix. `tests/web/test_renderers.py` is the existing
  contract test for the seven - it reads `RENDERERS`' `needs` lists back out of
  the page and asserts each template still produces every key its renderer
  reads, so a new renderer plugs into an existing, exercised guard rather than
  inventing one.
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
    already work this way - it says nothing about the 15 plain-text intents,
    whose new tables/cards would render inline in the page's own DOM, not in
    an iframe.
  - `answer.text` is what the CLI prints, unchanged, and the CLI has no
    `--json` flag (`cli/commands.py:317`, `click.echo(agent.ask(...).text)` is
    the only place `.text` is read outside the web layer - grepped, nothing
    else in `cli/` or `web/runner.py` touches `.answer`/`.text` directly). The
    web API's `/api/ask` and `/api/ask/stream` responses carry `text` as one
    field of `AnswerResponse` alongside `data`, so a JSON consumer of the API
    already gets both and loses nothing either way. A richer page is additive:
    `text` stays the sentence of record for the CLI and for any API caller
    that ignores `data`.
- **Per-intent inventory** (called directly against `nba.duckdb`, read-only,
  with representative slots for all 24 `TEMPLATES` entries - full coverage,
  none skipped for time). "Renderer today" is what `index.html` already draws;
  "needs template change" means `data` is too thin for the UI named and the
  template itself would have to grow a field, not just the renderer.

  | intent | `data` carries | UI it could support | renderer today | needs template change |
  |---|---|---|---|---|
  | `player_stat` | one stat's `gamesPlayed`/avg/total | single stat card | none (`<pre>`) | no |
  | `leaderboard` | ranked `leaders` list, `fields` | ranked table | **yes** | - |
  | `threshold_count` | ranked `leaders` (player, games) | ranked table | **yes** | - |
  | `team_record` | wins/losses/pct/home/road/last_ten | W-L card | **yes** | - |
  | `game_log` | list of games, per-game box line | table | **yes** | - |
  | `shot_chart` | `path` to a drawn SVG chart | chart artifact | **yes** (iframe) | - |
  | `player_compare` | per-player stat dict + NetPoints | comparison table | **yes** | - |
  | `single_game_high` | ranked `games` (player, value, date, opp) | ranked table | **yes** | - |
  | `head_to_head` | `games` count, `wins` per team | small 2-team card | none (`<pre>`) | no (2 numbers) |
  | `team_quarter_points` | per-game list (date, opponent, points), `total` | small multiple / sparkline over games | none (`<pre>`) | no |
  | `period_split` | per-game list (date, opponent, points), average | sparkline over games | none (`<pre>`) | no |
  | `shot_distance` | one number (`avg_feet`) + `attempts` | single stat card | none (`<pre>`) | no |
  | `player_history` | season-by-season one-stat series | table + sparkline | **yes** | - |
  | `player_netpoints` | per-category offense/defense/total list (`fingerprint`) | bar chart - same category set the `fingerprint` chart already draws | none (`<pre>`) | no (shape matches an existing chart type) |
  | `fingerprint` | `path` to a drawn radar chart | chart artifact | **yes** (iframe) | - |
  | `player_splits` | grouped table: home/away, starter/bench, win/loss, by month | multi-group table | none (`<pre>`) | no |
  | `with_without` | two-row group comparison (played/out), `tenure` | two-column comparison | none (`<pre>`) | no |
  | `record_when` | two-row group comparison (`reached`/`fell_short`) | two-column comparison | none (`<pre>`) | no |
  | `player_matchup` | `averages` (2-player comparison dict) + per-meeting `games` list when they met | comparison table + game log | none (`<pre>`) | no |
  | `streak` | list of `streaks` (length, from, to) | small ranked list / timeline | none (`<pre>`) | no |
  | `team_stat` | dict of metric -> {value, rank, of} | ranked stat table (card grid) | none (`<pre>`) | no |
  | `team_leaderboard` | ranked `teams` list | ranked table | none (`<pre>`) | no |
  | `team_outlook` | many single values: bpi, record, projections, `chances` dict | multi-card grid | none (`<pre>`) | no |
  | `coach` | `message`, `unanswerable` | refusal - prose only, correctly | none (`<pre>`) | **n/a - nothing structured to show, by design** |

  Every intent but `coach` has `data` rich enough for at least one richer UI
  with **no template change** - the thinness the task brief asked to watch for
  did not turn up. The one genuine gap is `player_netpoints`: its
  `fingerprint` list is the identical category/offense/defense/total shape the
  `fingerprint` template already hands to `radar.py`, so the cheapest richer
  UI for it is not a new renderer at all but drawing the same chart artifact
  from `player_netpoints`' own data - worth flagging separately if that path
  is taken, since it touches the template (adding an `Artifact`) rather than
  only `index.html`.
- **User sees:** nothing wrong - the plain-text answers are correct - but 15 of
  24 intents get a `<pre>` block on a page that otherwise renders seven of them
  as tables, cards and a sparkline, so the page reads as inconsistent between
  questions that happen to share a shape with an existing renderer and ones
  that do not. `game_log`-shaped answers (`team_quarter_points`, `period_split`,
  the `games` list inside `player_matchup`) are the most visible gap, since
  `game_log` itself already proves the table is wanted.
- **Next step - a staged plan, cheapest first, each stage independently
  shippable:**
  1. **Renderer-only, no template change.** Add `RENDERERS` entries for
     `team_stat`, `team_leaderboard`, `player_splits`, `with_without`,
     `record_when`, `head_to_head`, `streak`, `team_quarter_points`,
     `period_split`, `player_matchup`, `team_outlook`, `player_stat`,
     `shot_distance` - all 13 have `data` today, so this is JS plus
     `tests/web/test_renderers.py` additions (`CASES` grows one entry and one
     `needs` list per intent). Do the two-row comparisons
     (`with_without`/`record_when`, already the shape the task asked for as
     "a home/away or with/without split") and `player_matchup`
     (comparison table + reuse of the `game_log` table renderer for its
     `games` list) first - they read the most like the existing `player_compare`
     and `game_log` renderers, so the risk is lowest. **Risk:** none to the
     query path; the risk is entirely "a renderer bug swallows a fine answer,"
     which `renderBody`'s `try/catch` (`index.html:684-685`) already guards
     against by falling back to `<pre>` - so the failure mode this stage can
     produce is already handled.
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
  and already readable as plain text; this is a real gap in what a user sees
  (a `<pre>` block where six sibling questions get a table) rather than a bug,
  so it is ranked as a gap and not inflated to a wrong-answer priority it does
  not meet.
- **Could not verify:** the exact `fell_through` -> `answered_by=="agent"`
  mapping in the 261-row baseline (file not in this tree, and re-running the
  router was out of scope - ollama was not run for this task); whether a
  generic stage-3 renderer looks good for every shape in practice, which
  needs eyes on real output rather than a data-shape read; and whether
  `player_netpoints`'s aggregate-season fingerprint plots correctly on
  `radar.py`'s per-game percentile scale without changes beyond wiring - that
  needs the scale checked against real numbers, not assumed from the shared
  category names.
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
- **GitHub:** none yet
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
- **GitHub:** not yet filed
- **GitHub:** #131

## P4: tooling, docs, low impact

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
- **GitHub:** not yet filed
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
