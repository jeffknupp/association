# Phase 1 acceptance

The sibling check for `sim_plays` is the build itself: `uv run association sim build` prints one row per season (games kept, plays, unmapped, games whose running score reaches the final, games whose points by team sum to it). `results/` holds each recorded run, named by date, against this checkout's `nba.duckdb` (copied 2026-09-24). Recorded 2026-10-11: 19 seconds for 2007-2026 on 24 cores, a 469 MB `sim.duckdb`.
