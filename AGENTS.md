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
- **Next: step 2 (P2), segments** - `sim/segments.py` over `sim_plays`, with
  the sibling checks in the plan's P2 table measured on 2019-2026 first.

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
