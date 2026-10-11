# Possession-level game simulation: implementation plan

A plan for adding a possession-by-possession NBA game simulator to
`association`, built entirely from the warehouse. It is written to be executed
by agents, phase by phase, and assumes the reader has already read `AGENTS.md`,
`DATA.md` and `docs/architecture.rst`. Nothing here overrides them.

**Goal.** Given two rosters (or a real game to replay), generate synthetic
games at play-by-play resolution: an event log that reads like ESPN's
play-by-play, a box score per simulated game, and distributions over many
games (margin, totals, player lines, minutes). It is for fun and for
understanding, not for betting, and nothing in it needs to be fast enough for
live use.

**Non-goals for v1.** Season simulation, player aging, injury modeling, trade
evaluation, a web UI, and any model trained outside the warehouse.

---

## 0. Decisions needed before work starts

Each decision names the phase it blocks, a recommendation, and what changes if
the recommendation is not taken. **Agents must not start a blocked phase until
its decision is recorded** at the top of this file (replace "Recommended" with
"Decided", with a date). If Jeff says "go with the recommendations", every
recommendation below becomes the decision.

**All decided 2026-10-11** (Jeff: "go with the recommendations, including
D15"), with the Phase 0 measurements and the section 0c review folded in.

| id | decision | blocks | decided 2026-10-11 |
| --- | --- | --- | --- |
| D1 | Where the code lives | all | new `association.sim` package, ABOVE `query` in the layer contract; imports `query.court` and `query.shotchart.SHOT_VALUE_SQL`, copies nothing; no refactor now (0c, item 7) |
| D2 | Where derived tables live | P1 | a separate `sim.duckdb` |
| D3 | How plays are ordered | P1 | `ORDER BY period, clock DESC, play_id::HUGEINT` (measured; numeric id order failed) |
| D4 | Earliest season supported | P1 | build tables from 2007; estimate from the last 1-5 seasons by default; per-season blemish flags for 2013, 2016, 2018-2020 |
| D14 | Refetch for `scoreValue`, `wallclock`, served position | none | deferred; do it only if a P3 sibling check needs it |
| D15 | Build order | all | engine and rotation on box-score and shot-chart rates FIRST; the possession table (P3) and on-court defense are the fidelity upgrade after a first validated game (section 3, "Build order") |
| D5 | Which games a rate is estimated from | P4 | one `as_of` parameter, point-in-time |
| D6 | How defense is modeled | P4 | v1: team-level defense (option 1) plus player-level steal, block and rebound weights; option 2 after P3 |
| D7 | What a rate shrinks toward | P4 | league mean |
| D8 | Aging and injuries | P4 | out of scope |
| D9 | numpy | P5 | not in v1 |
| D10 | Simulation fidelity in v1 | P5 | start-type conditioning moves to the P3 upgrade (it needs possessions); team fouls, the bonus, foul-outs and last shots stay in v1; end-game fouling no |
| D11 | Output formats | P7 | text play-by-play, box score, JSON |
| D12 | Public API status | P7 | experimental, outside the compatibility promise |

### D1. Where the code lives

**Recommended:** a new package, `association.sim`, as a sibling of `query` and
`check` in the layer contract:

```toml
layers = [
    "association.cli",
    "association.web",
    "association.query | association.check | association.sim",
    "association.fetch",
    "association.nba",
]
```

and added to the independence contract with `fetch` and `query`. The simulator
reads the warehouse; it never needs `fetch`'s code or `query`'s.

That forces one refactor first: court geometry (`query/court.py`'s constants
and `SHOT_DISTANCE_SQL`, `BEYOND_THE_ARC_SQL`, `HAS_POSITION_SQL`) and
`query/shotchart.py`'s `SHOT_VALUE_SQL` move to `nba/` (for example
`nba/court.py`), with `query` re-importing them. `render_court_html` stays in
`query`. This is a pure move, proven by a golden comparison as `AGENTS.md`
requires. **Do not copy the SQL into `sim`**: two copies of the shot-value
rule is how the `points_attempted = 0` bug comes back.

*Alternative:* put `sim` above `query` in the layers so it can import
`query.court` directly. Cheaper now, but it couples the simulator to the
package that imports ollama.

**Review, 2026-10-11 (section 0c, item 7): take the alternative for now.**
`query/court.py` and `query/shotchart.py`'s constants import nothing that
reaches a model, this branch is isolated, and a pure move is as cheap at
merge time as today. `association.sim` sits above `query` in the layer
contract and imports the SQL constants; it still never copies them.

### D2. Where derived tables live

**Recommended:** a separate DuckDB file, `sim.duckdb` beside `nba.duckdb`,
which `ATTACH`es the warehouse read-only. `association sim build` (re)creates
it. Reasons: the warehouse build stays as fast as it is today; nothing new
appears where the SQL agent could find it (see the `KNOWN_TABLES` reasoning in
`fetch/repairs/reconstructed_box.py`); and a rebuild of the simulator's
tables never risks the warehouse.

*Alternative:* build the tables inside `nba.duckdb` during `data load`. Worth
it only if D13-style reuse (below) is wanted soon.

### D3. How plays are ordered

**Measured 2026-10-11 (section 0b, checks 0.2 and 0.2b-0.2i): order by
`period, clock_remaining DESC, TRY_CAST(play_id AS HUGEINT)`.** Not by
`play_id` alone, which the original recommendation named and which fails:

- `play_id` IS the event id followed by an unpadded sequence number
  (`4017047274`, `40170472710`; 14-digit zero-padded before 2013), every one
  numeric, none duplicated, so the cast is safe and string order is wrong.
- But numeric id order is NOT ESPN's order of play. ESPN's own served list
  (the JSON array, which the parser iterates and whose position it drops) is
  clock-monotone in 38 of 38 live games probed, and differs from id order on
  665 of 2,992 plays in the 2026 sample (22%), 743 of 2,833 in 2024, 86 of
  1,900 in 2019. A substitution can carry an id ~100 sequence numbers later
  than the dead ball it happened at (`401809511347`, clock 8:15, between ids
  255 and 256), and a shot-plus-rebound can be appended after the period's
  other plays (`401584959267-268`).
- The sort above REPRODUCES ESPN's served order exactly in 2019, 2022, 2024
  and 2026 (0 positions differ) and on 2,959 of 2,964 positions in 2025.
  Before 2019 it differs on a few same-clock groups a game (2010: 52 of 1,709
  positions; 2013: 14 of 1,819), where ESPN's tiebreak within one clock
  reading is not id order.
- On the measure that matters for P2, a touch of the ball by a player the
  substitutions say is on the bench (`scripts/sim_phase0/p10_lineup_contradictions.py`:
  a touch after the player's exit and before his re-entry in the period, or
  before his first entry when his first substitution event is an entry,
  2004-2026, regulation periods), the sort beats id order in
  EVERY season: 2026 0.731% -> 0.017%, 2024 0.572% -> 0.013%, 2021-2024
  0.013-0.014%, and 2007-2012 0.10-0.13% -> 0.006-0.028%. Id order was
  worst in exactly the seasons the simulator cares about.

Two residues the sort does not fix, both recorded in 0b: 2018-2020 stay at
0.16-0.20% because ~2,600 substitutions there name one player; and
2013-2017 scoring rows carry a stale running score 1-2% of the time.

`sequenceNumber` exists in the live JSON and equals id order, so capturing
it would not help. What WOULD be worth capturing on a refetch (D14) is the
list position itself, `scoreValue` (ESPN's own value of each scoring play)
and `wallclock` (present from 2013 in some games, every game from 2016).
None of the three is needed to build P1-P3 for 2019 on.

### D4. Earliest season supported

**Measured 2026-10-11: build the derived tables from 2007; estimate rates
from the last one to five seasons by default.** Jeff's rule (2026-10-11):
the simulator is for CURRENT data; a sim set in one of the past three
completed seasons is a nice-to-have and older replays are not a goal, so an
old season's fault never vetoes a choice that is right for recent ones.

Why 2007 and not 2004 (section 0b): 2002-2003 play logs are truncated (598
of 1,277 games in 2003 never reach the final score, 254 of them by more
than 40 points); 2004-2006 lack the second participant on most plays
(assists, steals and blocks cannot be attributed, `DATA.md`), are short a
point or two in 18-24% of games, have 188-246 team-games with no `starter`
flag at all in 2005-2006, and show 2-3x the lineup contradictions of 2007
(0.19-0.25% against 0.028%). From 2007 every season has: substitutions in
every team-game (0 team-games without one from 2009; 2 in 2016), five
starters flagged on every team-game but one or two a season, plays reaching
the final score in 97.8% of games (2007) rising to 99.4-99.6% (2019-2026),
and lineup contradictions under 0.03% - with three blemishes to carry as
per-season flags, not floors: 2013 and 2016 (made shots the plays type as
misses, `DATA.md`; 2016's 1,334 `Not Available` scoring plays), 2018-2020
(one-id substitutions, 0.16-0.20% contradictions) and 2013-2017 (End Game
row missing in ~20% of games). The floor is enforced in one place
(`sim/coverage.py`) and a request below it is refused with the reason.

### D5. Which games a rate is estimated from

**Recommended:** every rate function takes `as_of: (season, date)` and reads
only games strictly before it. "Fun" simulations pass the end of a season;
replay validation passes the replayed game's date. One code path means
validation cannot accidentally leak the game it is predicting.

### D6. How defense is modeled

Options, in increasing cost:

1. Team-level only: each team's opponent shooting by zone, forced turnover
   rate, foul rate and defensive rebounding, from the season.
2. **Recommended for v1:** per-player *on-court* defensive factors (opponent
   make rate by zone, turnover rate, free-throw rate and offensive rebounding
   while he is on the floor), heavily shrunk, and averaged over the five
   defenders. Needs only segments (P2) and possessions (P3).
3. NetPoints defensive rating (2019+) mapped to a multiplier. Available, but
   its scale is not directly a probability adjustment, and it covers only 2019
   on. A v2 option, or a prior for option 2.
4. Ridge-regression (RAPM-style) adjustments on possession outcomes. The best
   and the most work. v2.

### D7. What a rate shrinks toward

**Recommended:** the league mean for the season, with a prior strength
estimated from the data (P4.3). Position-group priors are tempting but
`DATA.md` records that the current season's positions are generic (`G`/`F`/`C`)
while earlier seasons are specific, so a position prior would change meaning
between seasons. Shot-profile clusters are a good v2 prior.

### D8. Aging and injuries

**Recommended: out of scope.** ESPN publishes no birth dates (`DATA.md`, "No
birth dates anywhere"), so any age curve needs an outside source, which is a
new data dependency. Availability is an input: the caller names the players,
or replay mode uses the players who actually appeared.

### D9. numpy

**Recommended: not in v1.** Pure Python with `random.Random(seed)` keeps the
dependency list unchanged and the engine easy to read. P5 sets a performance
budget; if it is missed after profiling, add numpy under a new `sim` extra and
vectorize across simulated games, not within one.

### D10. Simulation fidelity in v1

**Recommended in v1:** possession start type (after a made basket, a
defensive rebound, a live-ball turnover, a dead ball, a period start) as a
conditioning variable on duration, shot mix and efficiency. It is cheap and
transition possessions are materially different. Also in v1: team fouls and
the bonus, personal fouls and foul-outs, and end-of-period last shots.

**Deferred to v2:** intentional end-game fouling, timeouts, the shot clock as
an explicit state, challenges, and technical fouls.

### D11. Output formats

**Recommended:** (a) a text play-by-play in ESPN's style for one simulated
game, (b) a box score per simulated game, (c) a summary over N games, and (d)
JSON for all three. No web UI in v1.

### D12. Public API status

**Recommended:** the `sim` package is experimental. Add a line to the
`CHANGES.md` preamble placing it outside the compatibility promise, like the
template and intent set. Docstrings are still required and still published.

### Not decisions, but worth recording

- **D13 (opportunity, not in scope).** Segments give minutes and plus-minus,
  which `reconstructed_box.py` says play-by-play "cannot recover" for the
  2013-2018 Chicago and New Orleans games. They can, if lineups can be
  reconstructed - and Phase 0 check 0.7 found the `starter` flag survives on
  all 1,032 of those team-games, so P2.1 has its period-1 anchor there.
  Record this in `ISSUES.md` once P2's accuracy is measured; do not wire it
  into `player_box_stats_filled` as part of this work.

### D14. Refetch to capture what the parser drops (nice to have)

The live summary JSON carries, per play, `sequenceNumber` (equals id order,
useless), `wallclock` (an ISO timestamp, every play from 2016, most from
2013, none in 2010), `scoreValue` (ESPN's value of the play, 0-3) and the
list position, which is ESPN's order of play. The parser keeps none of
them. Ordering is recoverable without them (D3). The two worth having are
`scoreValue`, which would replace the shot-value derivation for plays, and
the served position, which settles the same-clock tiebreak before 2019.
Cost: the parser change plus a refetch with `--include-pbp` of the seasons
wanted; 2019-2026 is ~10,500 games, about 35 minutes at 5 requests a second.
Not blocking anything; do it if a P3 sibling check is fighting the score
columns.

### D15. Build order: a running engine before a possession table?

Section 0c argues the alternative. The plan as written reaches a first
simulated game only after P1, P2 and P3 all pass their sibling checks. Every
rate in P4 except possession duration, possession start type and the
on-court defensive factors can be estimated per minute from
`player_box_stats_deduped` and `shot_chart` alone, with possessions from the
box formula. A v1 engine on those rates, with P2's segments for rotation,
gives something to validate (P8) weeks earlier, and P3 then becomes the
fidelity upgrade it is. Jeff's call; the recommendation is to take it.

---

## 0b. Phase 0 results (measured 2026-10-11)

Read-only against this checkout's `nba.duckdb` (14,241,999 plays, 2002-2026)
with the scripts in `scripts/sim_phase0/` (their printed output is beside
them in `results/`). Two live probes of ESPN's summary endpoint (41 games)
are there too. Every number below is reproducible from those scripts. The
plays table was identical to the live feed on all 41 probed games (same
play ids, none missing either way).

**0.1 Vocabulary.** 164 distinct `type` values (`results/types_by_season.csv`
has them per season). What P1's `vocab.py` must know that the plan did not:
`Start Period` exists only 2002-2012; from 2013 a period's first row is the
jump ball or a substitution at 12:00, so period starts are synthesized from
the `period` column. `End Period` is present in every season (about 4.05 a
game); `End Game` is missing from ~20% of 2013-2017 games and present in
99.9% from 2018. Two jump-ball types (`Jump Ball` through 2026, `Jumpball`
from 2020) with THREE participants (the two jumpers and who gained
possession). A generic `Rebound` type (152 rows, 2013-2017). Fouls whose
type never says foul: `Shooting Block` (2005-2018), `Personal Block`
(2011-2017), `Offensive Charge`, `Personal Take Foul`, `Transition Take
Foul`. `Traveling` is a turnover whose type does not say so. ESPN's "No X"
types (`No Turnover`, `No Foul`, `No Ejection`, `No Timeout`, `No
Violation`) mean the opposite in some seasons (`DATA.md`). `Heave Jump Shot`
is 2026 only. Timeout types are `Full Timeout`, `Short Timeout` (to 2017),
`Official Time Out` (to 2018), `OfficialTVTimeOut` (2002-2003). Review and
challenge rows (`Coach's Challenge (...)`, `Ref-Initiated Review (...)`)
exist from 2020 and 2023 and are never touches. Text style changed in 2013
("made" -> "makes"); the second participant's role is in the text
("(X assists)", "(X steals)", "X blocks Y's ...", "(X draws the foul)").

**0.2 Ordering.** Decided above (D3). The clock parses on 100% of rows in
every season (formats `M:SS`, `MM:SS`, `SS.s`, `S.s`; no NULLs). Under
numeric id order, clock inversions over 0.5s run 0.13-0.67% of plays a
season (not under 0.1% in any), 0.2-0.7% of ACTION plays from 2010, and
plays logged after the `End Period` row of their own period occur in
30-65% of games (2026: 2,442 rows in 856 games). Under the period/clock/id
sort the lineup contradictions fall as recorded in D3. One caution:
the sort moves a row whose CLOCK is wrong rather than its id; in 2007-2012
that puts ~1,000 rows a season (substitutions and rebounds logged at an
earlier clock than their running score implies) ahead of where they
happened, which the score-monotonicity check sees (995-1,349 decreases a
season against 6-9 under id order) and the lineup check does not mind
(0.006-0.028%). For recent seasons the sort reduces decreases too (2026:
3,980 -> 249; 2025: 3,871 -> 199).

**0.3 Substitutions.** The first participant id is the ENTERING player and
the text reads "X enters the game for Y" in every season; the text starts
with the first id's `display_name` on 96-99.99% of two-id substitutions and
starts with the SECOND id's name on 5 of 1.43 million (2013), so the
remainder are spelling differences ("JJ Redick" / "J.J. Redick", "Metta
World Peace" / "Ron Artest"), not reversed order. **One-id substitutions**:
808 in 2018, 1,001 in 2019, 782 in 2020 (1.2-1.4% of those seasons' subs;
0-15 a season otherwise, 51-214 in 2003-2008). Their text names ONE player
and the id is his: "John Collins enters the game for " (the id is the
entrant) or "enters the game for Quinn Cook" (the id is the player
leaving). P2 reads the role from the text's shape and treats the other
player as unknown: the leaving slot is filled by whichever on-floor player
next appears as a substitute's leaver or never touches the ball again, else
the team-period is unresolved. Rosters agree: of 2019-2026 two-id
substitutions, 0-40 a season name a player not on the deduped box roster of
that team-game.

**0.4 Substitution coverage.** No team-game without a substitution from
2009 on (2 in 2016; 2007: 26, 2008: 12; 2004: 28; 2003: 412; 2002: 1,375 -
the 2002 and 2003 logs are partial). Median substitutions per team-game
rose from 18-19 (2004-2010) to 26-28 (2018-2025) and 31 in 2026; the 10th
percentile is 13-16 through 2017 and 20-23 since.

**0.5 Who appears from the bench.** The plan's test (is the player's next
substitution an entry?) does not discriminate: 1.5% for `Technical Foul`
against 0.3-0.5% for ordinary shot types, since a bench player with a
technical usually never enters that period. Measured instead as touches by
a player with no minutes and `did_not_play`, excluding the empty-box
team-games (which make every Chicago and New Orleans 2013-2018 player look
like a bench player): `Delay of Game` 2.1%, `No Ejection` 0.5%, `Technical
Foul` 0.38%, `Inbound Foul` 0.36%, flagrant free throws 0.2-0.25%,
`Ejection` 0.23%, `Personal Take Foul` 0.22%, everything else under 0.1%.
So the `bench_possible` kinds are the technical, ejection and delay types,
and the risk either way is small: no type is a bench appearance more than
one time in 50.

**0.6 Literals P3 depends on.** Rebound types are `Offensive Rebound` and
`Defensive Rebound`; a TEAM rebound is the same type with a `team_id` and
NULL `athlete_id`/participants (2019-2026: 13,000-17,000 offensive and
4,800-6,500 defensive team rebounds a season; the offensive ones are mostly
the dead-ball rebound ESPN logs after a missed first free throw, e.g.
"Wizards offensive team rebound" at the shooting foul's clock). A steal is
the turnover's second participant, named in the text ("(X steals)"), on
49-58% of turnovers in every season from 2003. Free-throw types are
`Free Throw - 1 of 1`, `1 of 2`, `2 of 2`, `1 of 3`, `2 of 3`, `3 of 3`,
`- Technical`, `- Flagrant 1 of 2` (and `1 of 1`, `1-3 of 3`), `- Clear Path
1 of 2`. Under the period/clock/id sort, the `2 of 2` follows its `1 of 2`
immediately 47% of the time, within 3 rows 93%, within 8 rows 99.9% (2019+;
16 trips of 187,323 have no second), with substitutions (115,823) and the
dead-ball team offensive rebound (46,234) between them: P3's trip logic
must look past both, and must NOT end a possession on that team rebound.
An and-one (made FG, `Shooting Foul`, `Free Throw - 1 of 1` by the same
team at the same clock) is adjacent in that strict shape 3,100-4,000 times
a season against 5,400-7,300 `1 of 1` rows; the rest have substitutions
between. Scoring plays that are neither a shot by the positive rule nor a
free throw: 2016's 1,334 `Not Available` rows and a few dozen mislabeled
rebounds and substitutions in 2013-2017; ZERO from 2018.

**0.7 Starter flag.** Exactly five starters on every team-game in 2008-2010
and 2013-2015, 2020-2026, and all but one or two team-games in 2011, 2012,
2016-2019. 2005-2006 have 188 and 246 team-games with NO starter flagged;
2002 has 84. The flag survives on all 1,032 Chicago and New Orleans
2013-2018 empty-box team-games (D13).

**0.8 Running score (new check).** The plays reach the real final score
(max over the game) in 99.4-99.6% of games 2019-2026, 97.8-99.5% in
2007-2018, but only 53% (2003), 75% (2004), 79% (2005), 76% (2006) before
that, mostly 1-2 points short in 2004-2006 and by whole periods in 2003.
From 2013 the `home_score`/`away_score` columns are STALE on 1.3-4.8% of
non-scoring rows (offensive rebounds 4.7%, substitutions 2.4%, shooting
fouls 3.8%, tip shots 14%) under id order, and 0.1-0.2% of scoring rows
disagree with the previous scoring row by other than 1-3 points even under
the clock sort (2025-2026: 77 and 108 rows). So P3 takes a play's points
from its own type and text (free throw 1, else the shared `SHOT_VALUE_SQL`
value), never from a score delta, and uses the running score only as the
per-game sibling check. The `End Game` row's score matches the final in
only 84-91% of 2018-2026 games (the row is sometimes written before the
last free throws), which is why the check uses the max.

**0.9 Shot positions (for P4.1).** From 2015, 99.95-100% of charted field
goals carry a position; 2022's `points_attempted` is 0 on 96% of shots
(`SHOT_VALUE_SQL` handles it); and from 2019 the shot chart holds exactly
the plays the positive shot rule names (0-1 rows differ a season). Heaves
are typed only in 2026 (1,153, none made); earlier seasons' heaves are
ordinary `Jump Shot` rows and need the geometric rule.

## 0c. Review of the plan (2026-10-11)

**Sound, and worth building as a possession simulator.** The three derived
tables rest on data that is clean where it matters: the lineup walk will
contradict the substitutions on about one touch in 7,000 from 2021, the
plays hold ESPN's own order once sorted, and shot positions are complete.
The sibling-check discipline, the shrinkage with fitted `k`, the odds-ratio
combination and the seeded engine are the right shapes. Changes to make:

1. **D3 and D4 as measured above.** The original D3 would have put a fifth
   of recent plays out of order, and the 2004 floor would have spent effort
   on seasons nobody will simulate.
2. **Order the work for an early first game (D15).** Engine (P5) and
   rotation (P6) against box-derived rates first, P3 second. Keep P2 in the
   first wave: it is cheap, now known to be reliable, and P6 needs it.
3. **P1: synthesize period boundaries** from the `period` column, since the
   `Start Period` marker ends in 2012 and `End Game` is unreliable
   2013-2017; make `seq` the period/clock/id rank; carry the two jump-ball
   types and three participants.
4. **P2: read a one-id substitution's role from the text's shape** and treat
   the unnamed player as unknown rather than failing the team-period; the
   fill rule then usually resolves it.
5. **P3: points from the play, not the score;** free-throw trips tolerate
   substitutions and the dead-ball team rebound between attempts; the
   and-one rule looks past substitutions at the same clock.
6. **P8: add a team-strength baseline**, not only the home-win rate. The
   warehouse holds ESPN's `team_power_index` and the season-level team
   stats; a logistic model on net rating is the benchmark a possession
   simulator has to beat before its extra machinery is justified.
7. **D1: do not do the court-geometry refactor now.** On an isolated branch
   whose rules Jeff has loosened (`AGENTS.md`), `association.sim` imports
   `query.court`'s constants and `query.shotchart.SHOT_VALUE_SQL` directly;
   `court.py` imports nothing that reaches ollama. Put `sim` above `query`
   in the layer contract. The move to `nba/` is right if this ever merges,
   and a pure move then is as cheap as a pure move now.
8. **D6: shrink hard, or start team-level.** With one to five seasons per
   player, per-player on-court defensive factors averaged over five
   defenders will shrink to nearly the team's figure anyway; team-level
   defense (option 1) plus player-level steal, block and rebound weights is
   an honest v1 and a fair baseline for option 2.
9. **Tests cannot read `nba.duckdb`, but a measurement script can.** Keep
   the plan's rule for the suite, and put each phase's sibling check in
   `scripts/sim_*` as Phase 0's are, with the output committed beside it.

---

## 1. Ground rules for agents

These restate `AGENTS.md` where it matters most here, plus rules specific to
this work.

- **Measure against a sibling before trusting a derived number.** Every
  derived table in this plan has a sibling it must agree with: segments
  against box `minutes`, `plusMinus` and `starter`; possessions against the
  box possession formula and the final score; simulated league averages
  against real ones. Each phase lists its sibling checks. The numbers go in the
  module docstring, measured, with the date, in the style of
  `reconstructed_box.py`.
- **Refuse rather than approximate.** A game whose lineups cannot be
  reconstructed is flagged and excluded from estimation, not patched with a
  guess. A simulation request the data cannot support (a season below the
  floor, a player with no possessions in the window) is refused with the
  specific missing fact, following the "refusal that names the wrong cause"
  section of `AGENTS.md`.
- **Derived values stay visibly derived.** Nothing in this plan writes into a
  stored table. Columns are snake_case, as in `advanced_stats.py`.
- **Read `DATA.md` before trusting a column.** The traps that matter here are
  listed in section 5.
- **Every play type is classified or explicitly ignored.** An unclassified
  type is a build failure above a threshold, never a silent drop.
- **Determinism.** Every simulation takes a seed. The same seed and inputs
  give byte-identical output. Tests depend on this.
- **Tests are offline** and build tiny Parquet fixtures in `tmp_path`, as
  `tests/fetch/repairs/test_reconstructed_box.py` does. Nothing reads
  `nba.duckdb` in the suite. Measurements against the real warehouse are run
  by hand and recorded, not asserted in tests.
- **Gates:** `scripts/check_fast.sh` while iterating; the full check before
  committing; `CHANGES.md` under the single `## Unreleased`; American
  spelling; radon grade C at worst per function.

---

## 2. Architecture

```
nba.duckdb (read-only)                       sim.duckdb
┌──────────────────────┐   ATTACH   ┌───────────────────────────────────────┐
│ plays                │──────────▶ │ P1 sim_play_kinds   (type → kind)     │
│ shot_chart           │            │ P1 sim_plays        (ordered, timed)  │
│ player_box_stats_*   │            │ P2 sim_period_starters                │
│ real_games           │            │ P2 sim_segments     (both lineups)    │
│ players, teams       │            │ P3 sim_possessions                    │
│ win_probability      │            │ P3 sim_actions      (per-event facts) │
│ net_points_* (v2)    │            └───────────────────────────────────────┘
└──────────────────────┘                          │
                                                  ▼
                          P4 sim/rates.py  → PlayerParams, TeamParams (as_of)
                          P6 sim/rotation.py → RotationProfile
                                                  │
                                                  ▼
                          P5 sim/engine.py  (possession state machine, seeded)
                                                  │
                                                  ▼
                          P7 sim/render.py, CLI: association sim ...
                          P8 sim/validate.py → report
```

Proposed modules (names are suggestions; keep one responsibility each):

| module | phase | responsibility |
| --- | --- | --- |
| `sim/database.py` | P1 | open `sim.duckdb`, attach the warehouse, schema version |
| `sim/vocab.py` | P1 | the play-type classification table, as Python data |
| `sim/plays.py` | P1 | build `sim_plays` |
| `sim/segments.py` | P2 | period starters and segment walk |
| `sim/possessions.py` | P3 | possession boundaries and per-action facts |
| `sim/zones.py` | P4 | shot zones, from shared court geometry |
| `sim/rates.py` | P4 | parameter estimation with shrinkage |
| `sim/rotation.py` | P6 | rotation profiles and the substitution policy |
| `sim/engine.py` | P5 | the state machine |
| `sim/render.py` | P7 | text play-by-play, box score, JSON |
| `sim/validate.py` | P8 | replay harness and report |

---

## 3. Phases

Dependency graph. Phases on one line can run in parallel once their inputs
exist:

```
P0 → P1 → P2 → P3 → P4 ─┬→ P5 ─┬→ P7 → P8
                        └→ P6 ─┘
```

P5 can start against hand-written parameters as soon as P4's dataclasses are
defined, before estimation works. P6 needs P2's segments only.

### Build order under D15 (decided 2026-10-11)

The graph above is the plan's dependency order; the ORDER OF WORK is this,
each step a commit (or a few) on the `simulation` branch with its sibling
check measured on 2019-2026 and recorded in the module docstring:

1. **P1, `sim_plays`** - `sim/database.py`, `sim/vocab.py`, `sim/plays.py`:
   every type classified, the D3 ordering as `seq`, period boundaries
   synthesized, shot value through the shared SQL. Check: plays kept and
   unmapped per season; the running score's max reaches the final in the
   games kept.
2. **P2, segments** - `sim/segments.py` over `sim_plays`: period starters,
   the walk, the one-id substitution rule. Checks: minutes, plus-minus,
   period-1 starters, resolution, phantoms, against the table in P2. This
   is the one derived table the first engine needs (for P6).
3. **P4 dataclasses** - `PlayerParams`, `TeamParams`, `LeagueParams`, the
   shrinkage and the odds-ratio helpers with their unit tests, as ONE small
   commit the next two steps build against. **Done 2026-10-11:**
   `sim/params.py` (the records, `Rate` and `Mix` carrying their trials and
   prior so the output can say "near the league prior", the zone names) and
   `sim/shrink.py` (`shrink`, `shrink_mix`, `fit_strength`,
   `fit_mix_strength`, `combine`, `combine_defenders`). The rates are PER
   MINUTE on the floor in v1 (D15: no possession table yet), and the
   defense is the team's (D6, option 1); steps 4-6 build against exactly
   these fields and add to them in their own commits.
4. **P4-box, estimation from the box and the shot chart** (parallel with 5
   and 6) - `sim/zones.py` and `sim/rates.py`: per player, per-minute usage
   (field-goal attempts, free-throw trips and turnovers per minute), zone
   mix and make rate by zone from `shot_chart`, assist and rebound and
   steal and block weights per minute, free-throw rate, foul rate; per
   team, pace from the box possession formula and team-level defense
   (opponent efficiency by zone, forced turnover rate, foul rate,
   defensive-rebound rate). All as `as_of` windows with fitted `k`.
5. **P5, the engine** (parallel) - against hand parameters first, then
   P4-box's; invariants and the performance budget as written.
6. **P6, rotation** (parallel) - from P2's segments, as written.
7. **P7, output and CLI** - `sim game`, `sim replay`, `sim build`.
8. **P8, validation** - the replay harness with BOTH baselines (home-win
   rate and a team-strength logistic fit on net rating or
   `team_power_index`), run on 2025 from January 1. This is the first
   measured game and the regression gate for everything after.
9. **P3, possessions and actions**, then the P4 upgrades that need them:
   possession duration by start type (D10), per-player on-court defensive
   factors (D6 option 2), usage as a share of lineup possessions. Each is
   accepted only if P8's metrics do not get worse.

Steps 1-3 are sequential and one agent's work; 4, 5 and 6 are three
agents in parallel once 3 has merged; 7 and 8 follow 5 and 6.

### Phase 0. Verify assumptions against the warehouse

**Done 2026-10-11; results in section 0b, scripts in `scripts/sim_phase0/`.**
The checks below are kept as written so the results can be read against
what was asked; where a check's design turned out not to discriminate (0.5)
or a check was missing (0.8, 0.9), 0b says what was measured instead.

Read-only queries against `nba.duckdb`. Output: a short note appended to
`DATA.md` for anything that is a fact about ESPN, and the answers to D3 and D4.
No code is committed in this phase except, optionally, a script under
`scripts/`.

The queries below assume a helper view with a parsed clock. Create it in a
scratch session, not in the warehouse:

```sql
CREATE TEMP VIEW p AS
SELECT *,
       str_split(participant_athlete_ids, ',') AS parts,
       CASE WHEN clock LIKE '%:%'
            THEN CAST(split_part(clock, ':', 1) AS INTEGER) * 60 + CAST(split_part(clock, ':', 2) AS DOUBLE)
            ELSE TRY_CAST(clock AS DOUBLE) END AS clock_remaining
FROM plays;
```

**0.1 Play-type vocabulary.** Every distinct `type` with counts per season,
plus five example `text` values each. This becomes `sim/vocab.py`.

```sql
SELECT season, type, count(*) AS n, any_value(text) AS example
FROM p GROUP BY ALL ORDER BY season, n DESC;
```

**0.2 Ordering (decides D3).** Does `play_id` start with `event_id`, and does
numeric `play_id` order agree with game time?

```sql
WITH o AS (
    SELECT season, event_id, play_id, period, clock_remaining,
           lead(period)          OVER w AS next_period,
           lead(clock_remaining) OVER w AS next_clock
    FROM p
    WINDOW w AS (PARTITION BY season, event_id ORDER BY TRY_CAST(play_id AS HUGEINT))
)
SELECT season,
       count(*)                                                        AS plays,
       count(*) FILTER (WHERE NOT starts_with(play_id, event_id))      AS id_not_prefixed,
       count(*) FILTER (WHERE TRY_CAST(play_id AS HUGEINT) IS NULL)    AS id_not_numeric,
       count(*) FILTER (WHERE next_period < period
                           OR (next_period = period AND next_clock > clock_remaining + 0.5)) AS inversions
FROM o GROUP BY season ORDER BY season;
```

Pass: inversions under 0.1% of plays in every supported season. Inspect a
sample of inversions either way. Some are real (a substitution logged a tick
late), and they tell P2 what to tolerate.

**0.3 Substitution participant order.** Which participant is entering?

```sql
SELECT p.season, count(*) AS subs,
       count(*) FILTER (WHERE starts_with(p.text, pl.display_name)) AS first_id_is_named_first
FROM p
JOIN players pl ON pl.athlete_id = p.parts[1]
WHERE p.type = 'Substitution'      -- confirm the literal in 0.1
GROUP BY p.season ORDER BY p.season;
```

Then read the text pattern ("X enters the game for Y" or whatever 0.1 shows)
to confirm the named-first player is the one entering.

**0.4 Substitution coverage (decides D4).** Per season: team-games with zero
substitutions, and the distribution of substitutions per team-game. A season
where a meaningful share of team-games has no substitutions cannot be
reconstructed and is below the floor.

**0.5 Who can appear while on the bench.** Count technical fouls, ejections
and any other types whose participants are frequently *not* on the floor
(test: the player's next substitution in the same period is an entry). These
become the `bench_possible` kinds in P1.

**0.6 Possession sanity inputs.** Confirm the literals for defensive and
offensive rebounds, team rebounds, turnovers (and how a steal is written),
end-of-period markers, jump balls, and free-throw types ("1 of 2", "2 of 2",
technical). P3 depends on each.

**0.7 Starter flag.** Share of real games where each team has exactly five
`starter = true` rows in `player_box_stats_deduped`, per season, including the
2013-2018 Chicago and New Orleans games.

### Phase 1. Classified, ordered plays

**P1.1 `sim/vocab.py`.** A mapping from every `type` in 0.1 to one `kind`:

```
shot, free_throw, offensive_rebound, defensive_rebound, team_rebound,
turnover, steal (if separate), foul_personal, foul_shooting, foul_offensive,
foul_technical, foul_flagrant, substitution, timeout, jump_ball, violation,
ejection, period_start, period_end, game_end, ignore
```

plus flags: `bench_possible` (from 0.5) and `live_ball` for turnovers where
determinable. Shots are identified by what the type names, reusing the
positive list and reasoning in `reconstructed_box.py`'s `_SHOT`, not by
exclusion. The unmapped-type rule: the build counts unmapped plays per
season and fails above 0.05% of plays; below that it logs them. A test
asserts every type in the fixture maps.

**P1.2 `sim_plays`.** One row per play:

```sql
CREATE OR REPLACE TABLE sim_plays AS
SELECT
    p.event_id, p.season, p.season_type, p.play_id,
    -- D3: ESPN's order of play, not id order (section 0b, 0.2).
    row_number() OVER (PARTITION BY p.season, p.event_id ORDER BY p.period, p.clock_remaining DESC, TRY_CAST(p.play_id AS HUGEINT)) AS seq,
    p.period,
    p.clock_remaining,
    CASE WHEN p.period <= 4 THEN (p.period - 1) * 720 + (720 - p.clock_remaining)
         ELSE 2880 + (p.period - 5) * 300 + (300 - p.clock_remaining) END AS elapsed,
    p.team_id, p.athlete_id, p.parts, p.type, k.kind, k.bench_possible,
    p.text, p.home_score, p.away_score, p.scoring_play
FROM (
    -- The same parsing as Phase 0's scratch view, owned here.
    SELECT *,
           str_split(participant_athlete_ids, ',') AS parts,
           CASE WHEN clock LIKE '%:%'
                THEN CAST(split_part(clock, ':', 1) AS INTEGER) * 60 + CAST(split_part(clock, ':', 2) AS DOUBLE)
                ELSE TRY_CAST(clock AS DOUBLE) END AS clock_remaining
    FROM nba.plays
) AS p
JOIN nba.real_games g ON g.event_id = p.event_id AND g.season = p.season
LEFT JOIN sim_play_kinds k USING (type)
WHERE p.season >= :floor;
```

Shot value for a `shot` play comes from the shared `SHOT_VALUE_SQL` joined via
`shot_chart` on `play_id`, never from `points_attempted` (`DATA.md`).

**Acceptance.** Per season, recorded in the module docstring: plays kept,
plays unmapped, games kept against `real_games`.

**Done 2026-10-11** (`association sim build`, 19 seconds for 2007-2026;
`scripts/sim_p1/results/build_2026-10-11.out`; the per-season table is in
`sim/plays.py`'s docstring). 0 unmapped plays of 11,878,125. Points summed
from the plays equal the final in 99.5-100% of games in 2019-2026 except
2022 (95.3%: the position-derived shot values misread a two for a three in
about one game in twenty - a NEW fact for the P3 and P4 work, which must
exclude or flag those games), and in 2013 (68.8%) and 2016 (50.0%), the
two seasons `DATA.md` records as typing made shots as misses. The points
check passes MORE games than the running score reaches in six of the eight
recent seasons, which confirms "points from the play, not the score".

**Phase 0 corrections (0b, 0.1 and 0.6).** `period_start` is synthesized
from the first row of each `period`, not read from `Start Period` (gone
after 2012); `game_end` likewise from the last row (`End Game` is missing
in ~20% of 2013-2017 games). A team rebound is a rebound type with NULL
participants. Both jump-ball types map to `jump_ball`, with three
participants. `Traveling`, `Shooting Block`, `Personal Block`,
`Offensive Charge` and the take fouls are classified by the rules
`player_games._PERIOD_TURNOVER` / `_PERIOD_FOUL` already encode.

**Tests.** Clock parsing ("11:42", "42.3", "0.0", NULL); overtime elapsed;
ordering by period, clock then numeric id when string order differs ("…9"
before "…10") and when a later id carries an earlier clock; unmapped-type
counting.

### Phase 2. Lineups: period starters and segments

A **segment** is a run of plays during which neither team's five changes. It
is the unit everything downstream attributes to.

**P2.1 Period starters (SQL).** For each period, a player is on the floor at
the start if his first appearance in that period is anything other than
entering as a substitute. First-quarter starters also come from the box
`starter` flag, which is the check on this rule.

```sql
CREATE OR REPLACE TABLE sim_period_starters AS
WITH roster AS (
    -- Team membership per game from the box, with ESPN's duplicate ids merged.
    SELECT event_id, season, athlete_id, team_id, starter
    FROM nba.player_box_stats_deduped
),
touches AS (
    SELECT sp.event_id, sp.season, sp.period, sp.seq,
           unnest(sp.parts)                AS athlete_id,
           generate_subscripts(sp.parts, 1) AS pos,
           sp.kind
    FROM sim_plays sp
    WHERE sp.parts IS NOT NULL
      AND NOT coalesce(sp.bench_possible, false)   -- technicals, ejections: see 0.5
      AND sp.kind <> 'ignore'
),
roled AS (
    SELECT event_id, season, period, seq, athlete_id,
           CASE WHEN kind = 'substitution' AND pos = 1 THEN 'in'    -- entering id, per 0.3
                WHEN kind = 'substitution' AND pos = 2 THEN 'out'
                ELSE 'action' END AS role
    FROM touches
),
first_touch AS (
    SELECT event_id, season, period, athlete_id,
           arg_min(role, seq) AS first_role,
           min(seq)           AS first_seq
    FROM roled
    GROUP BY ALL
)
SELECT f.event_id, f.season, f.period, r.team_id, f.athlete_id,
       f.first_role <> 'in'       AS on_at_start,
       r.starter                  AS box_starter,
       'first_touch'              AS source
FROM first_touch f
JOIN roster r USING (event_id, season, athlete_id)
WHERE f.first_role <> 'in';
```

This leaves three cases per team-period, handled in Python (P2.2):

- **Exactly five:** done.
- **Fewer than five:** a player was on the floor the whole period without
  appearing in any play, or the rule missed him. Fill from the previous
  period's closing lineup: players who closed the previous period, are not
  already counted, and do not enter by substitution in this period. If that
  gives exactly five, mark the filled players `source = 'carried'`. Otherwise
  the team-period is **unresolved**.
- **More than five:** a missing substitution. The team-period is unresolved.

For period 1, use the box starters when there are exactly five of them, and
record agreement between the inferred set and the box set as the accuracy
measure for the rule itself.

**P2.2 Segment walk (Python).** Per game, per period:

```python
lineup = {team: set(period_starters[team][period]) for team in teams}
open_at = period_start_elapsed
for play in plays_in_period_ordered_by_seq:
    if play.kind == "substitution":
        team, entering, leaving = ...
        close_segment(until=play.elapsed, until_seq=play.seq)   # if any time or play elapsed
        if leaving not in lineup[team] or entering in lineup[team]:
            mark_unresolved(team, from_seq=play.seq)            # through the end of the period
        else:
            lineup[team].remove(leaving); lineup[team].add(entering)
        open_at = play.elapsed
    elif play.parts and not play.bench_possible:
        for athlete in play.parts:
            if athlete is on a team and not in lineup[team_of(athlete)]:
                record_phantom(play, athlete)                   # a count, reviewed; does not repair
close_segment(until=period_end_elapsed)
```

Consecutive substitutions at the same clock (the usual case) close a
zero-length segment, which is dropped. A segment row carries:

```
event_id, season, period, segment_no, start_seq, end_seq, start_elapsed, end_elapsed,
home_lineup (sorted list of 5), away_lineup, home_points, away_points,   -- from score deltas
carried (bool: any member came from the fill rule), resolved (bool)
```

Unresolved segments are kept, with `resolved = false`, so the build can count
them, but P4 excludes them from every estimate.

**Sibling checks, measured per season and recorded:**

| check | how | target to beat before moving on |
| --- | --- | --- |
| minutes | per player-game, segment seconds / 60 vs box `minutes` | 97% within 1 minute |
| plus-minus | per player-game, segment point differential vs box `plusMinus` | 95% exact |
| period-1 starters | inferred set vs box `starter` set | 99% of team-games identical |
| resolution | share of game-seconds in resolved segments | 99% |
| phantoms | plays by a player not on the floor, per game | reported, reviewed |

The targets are starting points, not claims about what the data allows. If a
season misses one, investigate before lowering it, and record what was found
in `DATA.md` (ESPN's fault) or the module docstring (ours).

**Phase 0 corrections (0b, 0.3).** A one-id substitution (2018-2020,
~2,600) has its role read from the text: "X enters the game for " means
the id enters, "enters the game for Y" means it leaves; the other player is
unknown. The expected contradiction rate to beat is in D3: 0.013-0.02% of
touches from 2021.

**Done 2026-10-11** (`sim/segments.py`; `association sim build`, 44 seconds
for plays and segments 2007-2026; `scripts/sim_p2/results/build_2026-10-11.out`,
the per-season table in the module docstring). Every target met for the
seasons that matter: 2021-2026 resolve 99.84-99.93% of game seconds (target
99%), open period 1 with the box five in 100% of team-games (99%), put
99.6-99.99% of player-games within a minute of the box (97%) and
96.6-98.6% at the box's exact plus-minus (95%), with 10-41 phantoms a
season. Three things the implementation settled or found:

- **ESPN's plus-minus convention.** Applying each substitution where it is
  served matched the box plus-minus in 42.16% of 2025's player-games;
  applying every substitution at a clock reading after that reading's plays
  matched 98.14% but broke period openings (a player subbed in at 12:00 who
  takes the jump ball at 12:00). The walk does both: the lineup changes at
  once, for presence and for the consistency check, and the segment closes
  at the next clock reading, so a free throw after a substitution at the
  same clock is credited to the five who were there before it
  (`scripts/sim_p2/plus_minus_conventions.py`).
- **A one-id substitution marks the team unresolved** for the rest of the
  period rather than carrying an unknown slot; 2018-2020 resolve 94.9-95.7%
  of seconds for it, and no later season loses more than 0.2%. Carrying the
  unknown player is an upgrade for those three seasons only.
- **ESPN's 2007-2008 `plusMinus` is not a plus-minus.** 0 of 2008's 2,632
  team-games sum to five times the margin (2,630 of 2,630 in 2025; 2,249 of
  2,266 in 2015), so the 4.6-4.9% agreement there is the column's fault.
  New fact for `DATA.md` when this merges. Also: 996-1,114 touches a season
  in 2013-2015 are by an id outside the deduped box roster - ESPN's second
  id for one player, which the deduped box merges and the plays keep.

**Tests.** A hand-built fixture game covering: a normal substitution; two
substitutions at one clock; a player on for a whole quarter who touches
nothing (fill rule); a substitution whose leaving player is not on the floor
(unresolved); a one-id substitution of each shape; a technical foul by a
bench player (not a touch); a later id with an earlier clock; overtime.

### Phase 3. Possessions and actions

**P3.1 Possession boundaries.** A possession ends on:

- a made field goal, unless a free throw by the same team follows at the same
  clock (and-one), in which case it ends on the last free throw;
- the last free throw of a trip if made; if missed, on the rebound that
  follows;
- a defensive rebound, including a defensive team rebound;
- a turnover;
- the end of a period.

It does *not* end on an offensive rebound, a non-shooting foul before the
bonus, a jump ball won by the offense, or a kicked-ball violation. Technical
free throws belong to no possession (record them separately, since they still
score). Each possession records:

```
event_id, season, period, poss_no, offense_team_id, start_seq, end_seq,
start_elapsed, end_elapsed, start_type, end_type, points, segment_no (at start)
```

`start_type` is one of `period_start`, `after_made`, `after_dreb`,
`after_live_turnover`, `after_dead_turnover`, `after_ft`, `after_other`.
Events inside a possession are attributed to the segment that is current at
that event, because substitutions happen during free-throw trips.

**P3.2 Actions.** One row per meaningful event within a possession, with the
facts P4 counts: shooter, zone (from `shot_chart` coordinates via `sim/zones.py`),
value, made, assisted by, blocked by, fouled by, free throws awarded, rebound
by and type, turnover by, stolen by, live or dead. Assists, steals and blocks
come from the *second* participant, as `reconstructed_box.py` documents.

**Sibling checks:**

| check | target |
| --- | --- |
| sum of possession points + technical free throws = final score, per team-game | 100% (a hard gate) |
| offense possessions per team-game vs box formula `FGA - OREB + TOV + 0.44 FTA` | mean absolute difference under 2 |
| the two teams' possession counts per game | differ by at most the number of periods |
| player FGA, FTA, OREB, DREB, AST, TOV summed from actions vs box | same exact-match rates as `reconstructed_box.py` reports, or better |

Note that the box `turnovers` column is zero before 2013 at team level
(`DATA.md`); compare player-level turnovers there.

**Phase 0 corrections (0b, 0.6 and 0.8).** A play's points come from its
type and text (a free throw is 1; a shot's value is `SHOT_VALUE_SQL`'s),
never from a `home_score`/`away_score` delta, which is stale on 1-5% of
non-scoring rows from 2013 and wrong on 0.1-0.2% of scoring rows. The
running score is the per-game sibling check only, and the final to check
against is the max over the game's rows, not the `End Game` row's. A trip's
free throws may be separated by substitutions and by the dead-ball team
offensive rebound ESPN logs after a missed first attempt, which ends no
possession. Games whose plays do not reach the final score (0.4-0.6% from
2019) are excluded from estimation with a count, not patched.

### Phase 4. Parameter estimation

**P4.1 Zones.** Six zones plus heaves, in the shared rim-origin frame
(`DATA.md`, "Shot coordinates measure y from the rim"): restricted area
(under 4 feet), paint non-restricted (4-14), mid-range (14 to the arc), corner
three, above-the-break three, and **heave** (a three from beyond 35 feet with
under 3 seconds on the period clock). Heaves are modeled only in end-of-period
logic and excluded from every shooting rate; the 2026 excess shots in
`DATA.md` look like exactly these. Shots with no position (`HAS_POSITION_SQL`
false) get a zone only when their value is known, and then only "two, unknown
zone" or "three, unknown zone", which estimation excludes from zone rates but
counts toward usage.

**P4.2 What is estimated.** Per player, from possessions and actions in
resolved segments in the `as_of` window (D5):

| parameter | form | trials |
| --- | --- | --- |
| usage weight | share of the lineup's possession-ending uses while on the floor | team uses while on |
| action mix given a use | shot / turnover / shooting foul drawn | his uses |
| zone mix given a shot | categorical over zones | his shots |
| make rate by zone | binomial | his shots in the zone |
| and-one rate by zone | binomial | his makes in the zone |
| assist rate on a make, by zone | binomial | his makes |
| assist weight | share of assists while on the floor | team assists while on |
| free-throw rate | binomial | his attempts |
| offensive and defensive rebound weight | share of available rebounds while on | available rebounds while on |
| live-ball share of turnovers | binomial | his turnovers |
| steal weight, block weight | share while on defense | opponent turnovers, opponent misses |
| personal-foul rate | per defensive possession on the floor | defensive possessions |
| defensive factors (D6) | opponent make rate by zone, turnover rate, free-throw rate, offensive-rebound rate while on | defensive possessions |

Per team: possession duration distribution by `start_type` (empirical, as a
histogram), home-court adjustment (a single league-level multiplier on
efficiency, estimated from the window), and the team-level rebound split.

Parameters are frozen dataclasses (`PlayerParams`, `TeamParams`,
`LeagueParams`) with full annotations, since `pyright --verifytypes` must stay
at 100%.

**P4.3 Shrinkage.** Every binomial rate is estimated as

```
posterior = (successes + k * mu) / (trials + k)
```

where `mu` is the league rate in the window and `k` is the prior strength for
that rate. Estimate `k` per rate from the data, not by hand: split each
qualifying player-season into odd and even games, and choose `k` to minimize
the squared error of the shrunk odd-game rate against the raw even-game rate
(weighted by even-game trials). Categoricals use a Dirichlet prior with
concentration `k * mu_vector`, fit the same way. Record every `k` in the
module docstring with the season it was fit on. Expect `k` to be large for
three-point make rate and small for free-throw attempt rate. That is the
regression-to-the-mean behavior, applied deliberately.

**P4.4 Combining offense and defense.** For a probability with an offensive
rate `o`, a defensive factor `d` expressed as a rate, and a league rate `l`,
use the odds-ratio rule:

```
odds(p) = odds(o) * odds(d) / odds(l)
```

Defensive factors for five defenders are combined by averaging their
log-odds deviations from the league rate, weighted equally. That weighting is
a v1 choice, recorded as such.

**Refusals.** A requested player with zero possessions in the window is
refused by name, with the window stated. A player below a minimum (say 100
possessions) is simulated at nearly the league prior, and the output says so.

**Tests.** Shrinkage with zero trials returns `mu`; with huge trials returns
the raw rate; the `k` fitter recovers a known `k` on synthetic data; the odds
combination is symmetric and returns `o` when `d = l`.

### Phase 5. The engine

A deterministic state machine. State:

```
period, clock_remaining, offense, score, lineups (5 + 5), personal fouls,
team fouls per period, possession start_type, rng
```

One possession:

1. Sample the time the possession would take from the offense's duration
   distribution for this `start_type`, scaled by both teams' pace. If it
   exceeds the clock, run end-of-period logic: with under 3 seconds, a heave
   from the heave zone; otherwise a normal action and the period ends.
2. Choose the user: lineup usage weights, normalized over the five on the
   floor.
3. Choose the action from the user's action mix, adjusted by the defense
   (turnover and foul factors) via P4.4. A non-shooting foul committed by the
   defense, before the bonus, adds a team foul and restarts step 1 with the
   remaining time; in the bonus it becomes two free throws.
4. Shot: choose the zone, then make or miss with the defense-adjusted rate;
   on a make, assisted or not (assister by assist weights among the other
   four); and-one draw. On a miss: blocked or not (blocker by block weights),
   then rebound: offense or defense by the lineups' combined rebound rates via
   P4.4, the rebounder by weights. An offensive rebound continues the
   possession with a short put-back-weighted duration.
5. Turnover: live or dead; stealer by steal weights on a live one.
6. Free throws: each make by the shooter's rate; a missed last free throw is
   a rebound as in step 4.
7. Personal fouls are charged to a defender chosen by foul weights, team fouls
   increment, and a sixth foul removes the player (the rotation handles the
   replacement).
8. Emit events in a structured form (`SimEvent` dataclasses: kind, clock,
   team, actors, outcome). Rendering is P7's job, not the engine's.
9. At each dead ball, call the rotation policy (P6) for both teams.

Overtime is five minutes, repeated until the score is not tied.

**Invariants, asserted in tests and cheap enough to check at runtime in a
debug mode:** five players per side always; score equals the sum of scoring
events; team minutes sum to 240 plus 25 per overtime; no player on the floor
with six fouls; possession alternates except after offensive rebounds.

**Performance budget.** 10,000 simulations of one matchup in under 60 seconds
on one core. Measure before optimizing. If it is missed after profiling, see
D9.

**Tests.** Same seed, same output; the invariants over 500 random seeds with
toy parameters; a degenerate team that never misses scores exactly two or
three points per possession; a team with a 100% turnover rate never shoots.

### Phase 6. Rotation

**P6.1 Rotation profiles.** From resolved segments in the window, per team:

- each player's probability of being on the floor in each game minute
  (48 one-minute bins, plus overtime), conditioned on his having played;
- his minutes target (mean minutes when he played);
- how coaches respond to foul trouble: the on-floor probability by (fouls,
  period), pooled league-wide with shrinkage, since team samples are small;
- blowout behavior: the margin and time at which starters' on-floor
  probability collapses, pooled league-wide.

**P6.2 Substitution windows.** Only on dead balls. Which dead balls allow
substitutions is measured, not assumed: from `sim_plays`, the distribution of
the play kind immediately before each substitution. Allow exactly the kinds
that account for, say, 99% of real substitutions.

**P6.3 Policy.** At a window, each available player gets a score:

```
score = on_floor_prob(minute) * foul_factor(fouls, period) * fatigue(continuous_minutes) * blowout_factor(margin, time)
```

Choose the top five, with hysteresis: substitute only when the incoming
player's score exceeds the outgoing player's by a margin, so the sim does not
churn. Fatigue is a simple function of continuous minutes on the floor, fit
so that simulated stint lengths match real ones.

**Calibration target.** Over 1,000 simulations with the real roster, each
player's mean minutes is within 2 of his target, and the distribution of stint
lengths matches the real one (compare quantiles).

**Tests.** A fouled-out player never returns; a player marked unavailable
never enters; with hysteresis at infinity, starters play every minute.

### Phase 7. Output and CLI

Commands, as a Click group beside the existing ones:

```
association sim build [--seasons 2019-2026]            # (re)create sim.duckdb
association sim game BOS NYK --season 2026 [--as-of DATE] [--n 10000] [--seed 7] [--pbp] [--json]
association sim replay EVENT_ID [--n 1000] [--seed 7]  # real game, real available players, point-in-time
association sim validate --season 2025 [--from DATE] [--n 1000]
```

`sim game` with `--pbp` prints one simulated game's play-by-play in ESPN's
phrasing ("Jalen Brunson makes 18-foot pullup jump shot (Mikal Bridges assists)"),
using a small template per event kind, and its box score. Without `--pbp` it
prints the summary over N games: win probability, margin and total quantiles,
and each player's median and 10th-90th percentile line. Every output states
the rate window (`as_of`), the seed, and any player simulated near the league
prior, following the "a default must say what it did" rule in `AGENTS.md`.

Team and player names resolve through the same entity resolution the query
path uses only if D1 allows `sim` to reach it; otherwise `sim` takes team
abbreviations and athlete ids and does its own minimal lookup against
`teams` and `players`. Do not import `query.entities` across the independence
contract.

`--json` emits the same content, structured, with a schema version.

### Phase 8. Validation

**Replay harness.** For each game in a held-out set (for example, the 2025
regular season from January 1 on): estimate with `as_of` the game date, use
the players who actually appeared, simulate N times, and compare with what
happened.

Metrics, reported per run as a markdown file and recorded in the module
docstring when they change:

| metric | what good looks like |
| --- | --- |
| home win probability, Brier score | lower than a home-win-rate baseline AND a team-strength baseline (a logistic fit on the teams' season net rating, or ESPN's `team_power_index`); compared with ESPN's first `win_probability` sample for the same games (2018+) |
| margin PIT histogram | roughly flat; a U shape means the spread is too narrow |
| total points: mean error and spread | mean error near 0; simulated sd close to actual sd |
| league pace, offensive rating, 3PA rate, FT rate, OREB rate, TOV rate | each within a small tolerance of actual (set the tolerance on the first run and treat it as a regression gate) |
| player points, rebounds, assists: 80% interval coverage | close to 80% |
| player minutes: mean error, and coverage | mean error near 0 |

**The gate is no regression.** Once a baseline exists, a change that makes any
metric worse beyond noise needs a written reason. This is the simulator's
equivalent of the yardstick in `ROADMAP.md`.

Validation runs are slow and read the real warehouse, so they are never part
of the test suite. The suite tests the harness itself on a two-game fixture.

---

## 4. Suggested v2 work, in rough order of value

1. RAPM-style ridge adjustments for defense (D6 option 4), using possessions
   and segments directly.
2. End-game logic: intentional fouling, timeouts, two-for-ones, the shot clock
   as state.
3. Shot-profile cluster priors (D7) and NetPoints as a defensive prior.
4. Season simulation: schedule from `real_games`, availability as an input
   rate, standings and playoff odds.
5. Player-level matchup terms, only if the replay metrics show a residual
   pattern that they would explain.
6. D13: segment-derived minutes and plus-minus for the empty 2013-2018 games,
   as a separately flagged column, following `reconstructed_box.py`'s rules.

---

## 5. Data traps that will bite this work

Each is in `DATA.md`; this is the list to check against, not a restatement.

- **Chicago and New Orleans, 2013-2018:** box stats are zeroed, but plays
  survived and the `starter` flag may have (Phase 0.7). Segments and
  possessions for these games come from plays and are fine; sibling checks
  against box minutes and plus-minus are impossible for them, so exclude those
  games from the P2 accuracy measurement and say so.
- **Duplicate athlete ids in one box score:** use `player_box_stats_deduped`
  for roster membership, or a player appears twice on one team.
- **`points_attempted = 0` means unlabeled:** shot value only through the
  shared `SHOT_VALUE_SQL`.
- **Shot coordinates are rim-origin**, and `(0, 0)` means no position.
- **Free throws carry positions through 2018:** exclude by value, never by
  missing coordinates.
- **2026 has more charted shots than box attempts**, mostly end-of-period
  heaves: the heave zone exists for this.
- **The team `turnovers` column is zero before 2013**, and ESPN's own
  `possessions` column double-counts turnovers before then: compute possessions
  from player turnovers or from P3, never from those columns.
- **NULL minutes mean "did not appear":** use `did_not_play` and minutes
  together for availability in replay mode.
- **`games` has placeholder, duplicate and phantom rows:** always start from
  `real_games`.
- **Season 1993 is a phantom copy of 1994:** irrelevant above the floor, but
  every join keys on `season` as well as `event_id`, as the existing views do.
- **The 2016 `Not Available` scoring plays:** 180 scoring plays that no shot
  rule classifies. They score in P3 (so the score check passes) but cannot
  contribute to shooting rates; count them.
- **No birth dates:** no aging (D8).

---

## 6. Notes for running this with parallel agents

- P0 is one agent, and it gates everything else. Its output is facts, so it
  should be reviewed by Jeff before D3 and D4 are recorded.
- The D1 refactor (moving court geometry and `SHOT_VALUE_SQL` to `nba/`) is a
  separate, small branch that lands first, with its golden comparison.
- P1, P2 and P3 are sequential and touch different modules; one agent each
  is fine, handing off through the tables and their measured sibling checks.
- P4, P5 and P6 can run in parallel once P4's dataclasses are merged. Merge
  the dataclasses as their own small commit so the other two build against
  something stable.
- Every branch adds to the one `## Unreleased` section of `CHANGES.md` and may
  touch `pyproject.toml` (the import-linter contract, the vulture whitelist for
  Click commands). Expect textual merge conflicts there and resolve them by
  hand; the changelog hook catches a duplicated Unreleased heading, not a lost
  entry.
- Version directives: `sim` is experimental (D12), but new public symbols
  still get `.. versionadded::`. Work out the number from `git tag` and
  `## Unreleased` at the time, and expect to re-check it before release, per
  `AGENTS.md`.
