# Phase 4 acceptance (step 4: zones and the box-score estimator)

Both scripts read this checkout's `sim.duckdb` (built 2026-10-11, 2007-2026) and `nba.duckdb` (copied 2026-09-24) read-only, by path:

    PYTHONPATH=src .venv/bin/python scripts/sim_p4/zone_table.py sim.duckdb nba.duckdb
    PYTHONPATH=src .venv/bin/python scripts/sim_p4/estimate_report.py sim.duckdb nba.duckdb

`zone_table.py` is P4.1's check: the share of charted field goals per zone and the make rate per zone, 2019-2026, with the value-against-geometry disagreements, the positions missing, the typed and geometric heaves, the and-one rule's coverage of `Free Throw - 1 of 1` rows, free-throw trips against FTA/2, and the Python rule against the SQL rule on every distinct point. `estimate_report.py` is P4-box's: `rates.estimate` on the 2024-2026 window as of the day after the last 2026 regular-season game, for the Thunder and the Celtics - timing, the league parameters, every fitted strength, a star, a bench player and a near-prior player, and the sanity checks against known league figures. `results/` holds each recorded run, named by date; the tables in `sim/zones.py`'s and `sim/rates.py`'s docstrings are copied from them.
