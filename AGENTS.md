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
  simulator's yardstick (`association.sim.validate`; about three minutes
  for the 750 held-out games with `--jobs 16`, game days in parallel with
  identical results, fifteen minutes with `--jobs 1`). The plan's
  build-order item 8 holds the baseline table. **Every change to the simulator from here is measured
  by running it and committing the report**; a metric that gets worse
  beyond noise needs a written reason. The first thing it found - free
  throws at 0.371 per field-goal attempt against 0.240 - was fixed the same
  day (and-ones counted twice; three-shot trips at 40% instead of 3.5%),
  with the before and after reports side by side in `results/`.
- **Eleven validation runs so far** (`scripts/sim_p8/results/`): the
  baseline, after the free-throw fix, with a pace constant, after the
  bonus-trip fix (`_trips`), two with team rebounds (`_teamreb`,
  `_teamreb_odds`), with the season-weighted window (`2026-10-12_weights`),
  with durations by situation (`2026-10-12_durations`), with usage shares
  (`2026-10-12_usage`), with on-court defense (`2026-10-12_defense`) and
  with games weighted by age, half-life 180 days (`2026-10-12_halflife`,
  the current state: possessions 101.3 against 100.8 with no constant,
  total +0.7, Brier 0.2163 against the strength model's 0.2253 and ESPN's
  0.1996; the real-minutes diagnostic `_real_minutes` sits beside the
  defense run it was made against). Before that run the
  pace matched by a constant (100.5
  against 100.8 possessions), free throws are 0.246 against 0.240 per
  field-goal attempt, threes 0.415 against 0.420, points per 100
  possessions 112.8 against 113.5, rebounds per team-game 10.7 + 32.9
  against 11.2 + 32.8, the total -2.1; Brier 0.2226, ahead of the
  strength model's 0.2250 for the first time, ESPN 0.1984. Two runs of
  one setup differed by 0.002 on Brier, so treat smaller moves as noise.
  The same code on the neighboring seasons (plan item 8, "By season"):
  2024 Brier 0.2313 level with the strength model, 2026 0.2207 against
  0.2250; games with a bottom-eight team are the easy ones for every
  forecaster, so a tanking season flatters the score rather than hurting
  it (`scripts/sim_p8/brier_by_team_strength.py` splits a run's JSON).
  Each fix was found by decomposing the run's JSON per team-game (points,
  FGA, FTA, rebounds, turnovers, real against simulated) and confirmed
  with `--explain` on a replay; do the same before changing a constant,
  and re-derive `PACE_CALIBRATION` when a change alters what the box
  formula counts. The window weights seasons `params.SEASON_DECAY` = 0.5
  (`Window.weight`; `scripts/sim_p8/season_weights.py` is the measurement)
  and every output names the weights.
- **Step 9, first half (P3) done 2026-10-12:** `association sim build` also
  writes `sim_possessions` and `sim_actions` (`association.sim.possessions`:
  `build_possessions`, `check_possessions`, `GameWalk`, `walk_game`,
  `free_throw_shape`, `START_TYPES`, `END_TYPES`), 2007-2026 in about two
  minutes after plays and segments; the acceptance table is in the module
  docstring, the plan's item 9 and `scripts/sim_p9/results/`. Read
  possessions by `poss_no`, actions by `seq`; an action with `poss_no NULL`
  is a technical free throw or foul, or an orphan scoring row with no team.
- **Step 9, durations by situation done 2026-10-12:** the engine's duration
  draw is a whole-second histogram per situation (`params.DURATION_KEYS`,
  `LeagueParams.durations`, measured by `rates.estimate` from the
  possession table; `scripts/sim_p9/durations.py` is the measurement) times
  the offense's `first_use_seconds` over the league's, plus the 2.4 s a miss
  spends before its rebound; the pace constant is gone and possessions
  match without one. A league estimated without the possession tables is
  refused by the engine. **Usage shares done 2026-10-12** too
  (`PlayerParams.usage_share`, zero means unknown and the per-minute sum
  is used): validated neutral, kept as the right unit. **Games are
  weighted by age** (`params.HALF_LIFE_DAYS` 180, inside every estimator
  query; `Window.half_life_days=None` for equal weights): Brier gain 0.005
  a game, measured by `scripts/sim_p9/game_weights.py`. **On-court defense
  done 2026-10-12** (D6 option 2): the defense an action meets is the five
  on the floor (`PlayerParams.defended_*`, `LeagueParams.defended`,
  `shrink.combine_defenders` with the measured mean - the possession
  table's unit is not the box score's, which the first run showed), the
  team's rate where none of the five is known; Brier gain 0.004 a game,
  the largest so far. Step 9 is complete; the plan's build order is done.
  Possession points plus technical free throws equal the final on 100% of
  team-games in every season; the box formula over player lines runs 1.2
  possessions above the count from 2019 (team rebounds and team turnovers),
  and 2026's FGA check is 95.8% because the box does not count a typed heave.
  Rules the data forced, each measured first (plan 0b, 0.14): a jump ball
  ends nothing (the turnover ESPN logs next does), a lone `1 of 1` and a
  flagrant trip keep the ball, a team rebound's side is its label (its
  `team_id` is the shooter's through 2012), a putback's rebound is logged
  after the putback in 2007-2017. `rates.estimate` still reads the box
  score; the P4 upgrades on these tables are the second half.
- **The rotation's share is measured** (`sim validate --real-minutes`,
  `sim/real_minutes.py`, 2026-10-12): with the real lineups the Brier is
  0.2161 against the model's 0.2214, so the rotation is worth up to 0.005
  of Brier, a fifth of the gap to ESPN; player coverage goes 0.744 to
  0.844 (over-covered), so the model's intervals are narrow only through
  the minutes, and players who really scored 20+ are covered 0.687 even
  with their minutes - star scoring is a possession-model miss.
- **Next**, in the order the last runs rank them: the rotation model
  (`rotation.RotationSettings`: a minimum stint, early hysteresis,
  foul-trouble awareness; minutes MAE 5.3, substitutions nineteen seconds
  into a game, eight-man rosters fouling out), measured against the
  real-minutes ceiling; star scoring (usage or efficiency of the high-usage
  player, `--explain` on a star's game); the simulated total's spread
  within a game (17.2 against 19.5); a position-group prior for the deep
  bench (plan 0b 0.16, `scripts/sim_p9/position_priors.py`); the
  offensive-rebound share
  0.247 against 0.255, which is 2025's within-season drift that no
  weighting of the data before January 1 reaches (plan 0b 0.16) - leave
  it; threes and free throws now match.
  Then, with the build order complete, the plan's section 4 (v2 work) in
  its order of value, the strength headroom to ESPN (0.022 of Brier)
  still running through rates and defense; and the simulated total's spread
  within a game, 17.2 against the real 19.5 since the durations came from
  the histograms (game-level pace variation the sim does not model). Every
  one is a `sim validate` run with the report committed.
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
