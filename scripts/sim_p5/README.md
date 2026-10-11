# Phase 5 acceptance

The engine (`association.sim.engine`) has no sibling in the warehouse yet - the first measured game is step 8's replay harness - so these two scripts are its checks until then, both on the hand-written toy league in `tests/sim/toy.py` (near 2025-26's rates; two ten-man rosters of three archetypes; the home side a slightly better defense, the away side two possessions quicker). `results/` holds each recorded run.

- `benchmark.py`: the plan's budget, 10,000 simulations of one matchup under 60 seconds on one core. Recorded 2026-10-11 (Python 3.14.8, x86_64, one process): **10,000 games in 26.08 s, 2.61 ms a game**. `--profile` runs 500 under cProfile: the hottest own-time lines are the event records' construction (`SimEvent.__init__`, 9%), `_emit` (7%), the `PlayerGameState` records built for each team's `RotationContext` at every dead ball (`_context`, 21% cumulative) and `shrink.combine` in the per-game `_Player` setup (13% cumulative - the same matchup's rates are recombined for every seed; caching them across games of one matchup is the first lever if the budget is ever missed, and vectorizing across games under a `sim` extra the second, per D9).
- `sanity.py`: 1,000 games, the per-team per-game averages beside what the inputs imply. Recorded 2026-10-11:

  | per team-game | home sim | away sim | implied by the inputs |
  | --- | --- | --- | --- |
  | points | 112.4 | 108.1 | - |
  | possessions per 48 | 95.5 | 95.5 | 100.0 (the two paces' mean) |
  | FGA per 48 | 83.5 | 84.2 | - |
  | 3PA share of FGA | 0.420 | 0.418 | 0.414 (the fives' usage-weighted zone mix) |
  | FTA per 48 | 26.7 | 27.1 | - |
  | TOV per possession | 0.133 | 0.134 | 0.120 per USE; times the ~1.11 uses a possession holds = 0.133 |
  | OREB share of own misses | 0.243 | 0.249 | 0.250 |
  | AST / STL / BLK / PF per 48 | 23.6 / 7.1 / 5.1 / 16.9 | 22.6 / 7.0 / 4.9 / 16.8 | - |
  | points per possession | 1.174 | 1.129 | - |

  Home won 583 of 1,000, 23 went to overtime, the mean margin +4.33 (the 2.5 points of home court plus the home side's better toy defense). Two things the table says about the loop, both expected and both written in the module docstring: the simulated pace runs 4.5% under the input, because the duration draw's mean is the whole possession's (`1440 / pace`) while offensive rebounds, non-shooting fouls and the clock a period's last possession leaves add on top of it - the P3 upgrade (durations by start type from the possession table) replaces the draw, and until then a total is about 4.5% light; and a per-minute mix is a probability per use, so turnovers per POSSESSION come out at the mix times the uses a possession holds, which is why the estimator must supply `forced_turnover_rate` and `foul_rate` per possession, as the league's are. Shooting fouls drawn: 0.116 per possession (implied 0.090 trips per use plus and-ones, times 1.11); non-shooting defensive fouls: 0.060 per possession (from `NON_SHOOTING_DEFENSIVE_SHARE`).
