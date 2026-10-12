# Working on the `simulation` branch

This checkout is a copy of `association` with a standalone warehouse
(`nba.duckdb`, 1.1 GB, copied 2026-09-24) and ONE purpose: a
possession-level NBA game simulator, built from that warehouse, on the
`simulation` branch, isolated from the query-pipeline work in the main
repository. `SIMULATION_PLAN.md` is the plan; its sections 0b and 0c hold
what Phase 0 measured (2026-10-11) and how the plan was amended. Read the
plan first, this file second.

The main project's orientation is kept beside this file as
`AGENTS-association.md`. **Most of it does not apply here** (Jeff,
2026-10-11): the query pipeline's rules, the ratchets, the roadmap, the
import contracts around the reader, the `ISSUES.md`/`DATA.md` process and
the GitHub issue sync are the main repository's concerns. Do not let them
restrict the exploration. What DOES carry over is listed below, because it
was learned the hard way and is about the data, not the pipeline.

## Jeff's rules for this branch

- **Recent seasons first.** The simulator is for CURRENT data, plus as much
  history as informs it. Simulating a game set in one of the past three
  completed seasons is a nice-to-have; historical replays before that are
  not a goal for the foreseeable future. So when a measurement shows a
  tradeoff across eras, choose what is right for the last one to five
  seasons and say which eras the choice gives up. Phase 0 did exactly this
  for the play ordering (plan, D3): the sort that fixes 2018-2026 would
  have been preferred even if it had broken 2007-2012, and as it happens it
  improves them too.
- **Install what you need, fetch what you need.** Packages may be added;
  live ESPN calls and refetches are allowed (the fetch client is
  `association.fetch.client.ESPNClient`, the game summary is
  `endpoints.summary_url()` with `params={"event": id}`). Do not run ollama
  or `association query` here; nothing in the simulator needs a model.
- **Measure before deciding, and commit the measurement.** Phase 0's
  scripts and their printed output live in `scripts/sim_phase0/`; each
  later phase's sibling checks go in `scripts/sim_<phase>/` the same way.
  A number in a docstring or in the plan names the script that produced it.
  The test suite stays offline and never opens `nba.duckdb`; the scripts
  are where the warehouse is read.
- **Refuse rather than approximate, and say what you did.** A game whose
  lineups cannot be reconstructed is counted and excluded, not patched. A
  default the simulator chose (a window, a shrinkage prior, a player near
  the league prior) is stated in the output.

## What still applies from the main orientation

- **The data traps.** `SIMULATION_PLAN.md` section 5 is the list; the
  entries behind it are in `DATA.md` (read-only here; a new fact about ESPN
  found on this branch goes in the plan's section 0b, not in `DATA.md`,
  until this work merges). The ones that bit Phase 0: `points_attempted = 0`
  means unlabeled (use `query.shotchart.SHOT_VALUE_SQL`); shot coordinates
  are rim-origin and `(0, 0)` is no position; `real_games`, never `games`;
  `player_box_stats_deduped` for rosters; Chicago and New Orleans 2013-2018
  have empty box scores but real plays and a surviving `starter` flag;
  NULL minutes mean did not appear.
- **A season is named for the year it ends**; "this season" is the latest
  with games on record (`ASSOCIATION_TODAY` pins the harnesses).
- **The gates still run** (`scripts/check_fast.sh`, `uv run pre-commit run
  --all-files`, `uv run pytest -q -n auto`), and a fresh venv needs
  `uv sync --frozen --extra dev --extra docs --extra web` first. A gate that
  blocks simulator work for a pipeline reason (an import contract, a
  ratchet list, the `CHANGES.md` hook) is adjusted on this branch with a
  comment saying why, rather than worked around. `CLAUDE.md` is a symlink to
  this file; edit this file, never the link.
- **A same-size edit within a second can be served from a stale `.pyc`**;
  `/tmp` is shared; a script run from a worktree resolves the installed
  package unless `PYTHONPATH=<tree>/src` says otherwise.
- **Write the result, not the reasoning.** Name the population behind every
  number, the script that measured it, and which copy of the data.

## Where the work stands

- **Step 1 (P1) done 2026-10-11:** `association sim build` writes
  `sim_play_kinds` and `sim_plays` to `sim.duckdb` (`association.sim.database`,
  `vocab`, `plays`), 2007-2026 in 19 seconds; the acceptance table is in
  `sim/plays.py`'s docstring and `scripts/sim_p1/results/`. Read `sim_plays`
  ordered by `seq`; never by `play_id`.
- **Step 2 (P2) done 2026-10-11:** `sim_segments` and `sim_team_periods`
  (`association.sim.segments`), built by the same command; 2021-2026
  resolve 99.8-99.9% of seconds, minutes within one 99.6-99.99%, plus-minus
  exact 96.6-98.6%. Read only `resolved` segments for any estimate. The
  same-clock convention is ESPN's: a substitution changes the lineup at
  once but the segment closes at the next clock reading
  (`scripts/sim_p2/plus_minus_conventions.py` is the measurement).
- **Step 3 done 2026-10-11:** `association.sim.params` (`Rate`, `Mix`,
  `Window`, `PlayerParams`, `TeamParams`, `LeagueParams`, the zone names)
  and `association.sim.shrink` (shrinkage, fitted prior strength, the
  odds-ratio combination). Build against these fields; add a field in your
  own commit rather than reshaping one.
- **Steps 4, 5 and 6 done 2026-10-11**, three agents in parallel, merged:
  `sim/zones.py` + `sim/rates.py` (`rates.estimate(con, window, team_ids,
  athlete_ids)`, 0.8-1.5 s for the whole league; the fitted strengths and
  the zone table are in the docstrings and the plan's build-order item 4),
  `sim/engine.py` + `sim/events.py` (`simulate(GameSetup, seed)`, 10,000
  toy games in 26-28 s; the constants it invented for want of league
  fields - heave make rate, block share, non-shooting foul share, the
  duration draw - are listed in the plan's item 5 for the estimator to
  supply), `sim/rotation.py` (`build_profiles`, `build_league_rotation`,
  `RotationModel`; calibration in the plan's item 6). Contracts to keep:
  `TeamParams.forced_turnover_rate` and `foul_rate` are per opponent
  POSSESSION; a possession's mean duration is 1440 / mean(pace) seconds;
  `RotationModel` raises `RotationError` with fewer than five available and
  the engine raises `EngineError`, neither plays four.
- **Step 7 done 2026-10-11:** `association sim game HOME AWAY` and
  `association sim replay EVENT_ID` (`association.sim.game` wires
  `rates.estimate` -> `rotation.RotationModel` -> `engine.simulate`;
  `association.sim.render` words it). `uv run association sim game NY BOS
  --n 1000 --pbp` is the quickest way to see the whole thing run (7 s).
  **To debug a game, add `--explain`**: every line of play-by-play is
  followed by the decisions that produced it - the candidates with their
  probabilities, the inputs (a make's player, defense and league rates and
  the home multiplier), the draw and what was chosen - and `--pbp-seed K`
  picks which game of the run is printed. A run that the engine refuses
  names the failing seed; `sim replay EVENT --n 1 --seed K --pbp --explain`
  replays exactly that game. Programmatically: `engine.simulate(setup,
  seed, trace=True)` puts the same records on `SimGame.decisions`, and
  `game.traced(prepared, seed)` does it for a prepared matchup.
- **Step 8 done 2026-10-11:** `association sim validate --season 2025
  --from 2025-01-01 --n 200 --out scripts/sim_p8/results/<name>.md` is the
  simulator's yardstick (`association.sim.validate`; about 15 minutes for
  the 750 held-out games). The plan's build-order item 8 holds the
  baseline table. **Every change to the simulator from here is measured
  by running it and committing the report**; a metric that gets worse
  beyond noise needs a written reason. The first thing it found - free
  throws at 0.371 per field-goal attempt against 0.240 - was fixed the same
  day (and-ones counted twice; three-shot trips at 40% instead of 3.5%),
  with the before and after reports side by side in `results/`.
- **Six validation runs so far** (`scripts/sim_p8/results/`): the
  baseline, after the free-throw fix, with `engine.PACE_CALIBRATION`,
  after the bonus-trip fix (`_trips`), and two with team rebounds
  (`_teamreb`, `_teamreb_odds` as committed). The pace matches (100.5
  against 100.8 possessions), free throws are 0.250 against 0.240 per
  field-goal attempt, points per 100 possessions 113.0 against 113.5,
  rebounds per team-game 10.5 + 32.9 against 11.2 + 32.8, the total -1.9;
  Brier 0.2261 against the strength model's 0.2242 and ESPN's 0.1979, and
  two runs of one setup differed by 0.002, so treat smaller Brier moves as
  noise. Each fix was found by decomposing the run's JSON per team-game
  (points, FGA, FTA, rebounds, turnovers, real against simulated) and
  confirmed with `--explain` on a replay; do the same before changing a
  constant, and re-derive `PACE_CALIBRATION` when a change alters what the
  box formula counts.
- **Next**, in the order the last run ranks them: the offensive-rebound
  share (0.243 simulated against 0.255: the player-line share rises a
  point a season and the equal-weighted three-season window lags it -
  weight `game.window_for` toward the latest season or shorten it for
  team and league rates, the first "recent seasons first" change the
  harness asks for; it is also the total's -1.9 and the 1.4 missing
  field-goal attempts a team-game); too many foul-outs on eight-man real
  rosters (8 games refused); the rotation's substitutions nineteen seconds
  into a game (a minimum stint or early hysteresis in
  `rotation.RotationSettings`); player points coverage 0.740 and assists
  0.851 against 0.800; minutes mean absolute error 5.3; threes 0.409
  against 0.420 per attempt and free throws 4% high
  (`NON_SHOOTING_DEFENSIVE_SHARE`, or the bonus path's trip length). Then
  step 9, P3 (possessions and actions) and the upgrades that need it:
  durations by start type (replacing the calibration constant), per-player
  on-court defense, usage per possession. Every one is a `sim validate`
  run with the report committed.
- **Dispatching agents here:** make their worktrees by hand from the
  intended SHA (`git worktree add -b <branch> .claude/worktrees/<name>
  <sha>`); the Agent tool's own worktree isolation branched from master,
  and three agents rightly stopped at their SHA check. Remove the
  worktrees before running the gates on the merged tree (deptry scans
  them). Read gate results from `.venv/bin/pre-commit run --all-files
  --verbose` and the exit status; the `uv run` wrapper was seen printing
  every hook as Passed while exiting 1.

## Facts about `plays` every phase relies on (Phase 0, 2026-10-11)

Details and the per-season tables are in the plan's section 0b.

- **Order of play is `period, clock DESC, play_id::HUGEINT`.** It reproduces
  ESPN's served order exactly from 2019 and beats numeric id order on
  lineup contradictions in every season (2026: 0.73% -> 0.017% of touches).
  Numeric id order is NOT the order of play from 2013 on; string order of
  `play_id` is wrong everywhere.
- **The clock parses on every row** (`M:SS`, `MM:SS`, `SS.s`).
- **`Start Period` ends in 2012; `End Game` is missing in ~20% of
  2013-2017 games.** Period boundaries come from the `period` column.
- **A substitution's first participant enters, the second leaves**, and
  ~2,600 substitutions in 2018-2020 name one player, whose role the text's
  shape gives.
- **The running score is stale on 1-5% of non-scoring rows from 2013**, and
  off on 0.1-0.2% of scoring rows. Points come from the play's own type and
  text; the score is a per-game check against the max over the game.
- **A team rebound is a rebound type with no participants**, logged after a
  missed first free throw among other places, and ends no possession.
- **2007 is the build floor**; 2002-2003 logs are truncated and 2004-2006
  lack second participants and starter flags. Blemishes to flag per season:
  2013 and 2016 (made shots typed as misses), 2018-2020 (one-id
  substitutions).

## Recording findings

A fault in ESPN's data or a wrong-answer risk found on this branch goes in
`SIMULATION_PLAN.md` section 0b (a fact) or in a `## Open questions`
section at the end of the plan (a task), with the script or query and the
count, and in the report to Jeff. Nothing is filed in the main repository's
`ISSUES.md` from here until this branch merges; at that point the entries
move and get GitHub issues per the main orientation.
