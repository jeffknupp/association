# Known issues

Problems found while working and not fixed, each with what was measured and
what was not. Newest first. Delete an entry when it is fixed, and say which
commit fixed it in `CHANGES.md` rather than here.

## Found while qualifying true shooting and eFG% on attempts

### `fg_pct` and `efg_pct` qualify on different floors over the same denominator

`fg_pct` (from c158c44) needs 400 field-goal attempts and `efg_pct` needs
480. Both divide by FGA, so a player can qualify for one and not the other.

What was measured is eFG%, not FG%. Against StatMuse's published eFG% top 15s
(300 made field goals per 82 games), a 400-FGA floor put 4 unlisted players
into 2025's top 15 and 6 into 2026's; 480 put in 1 and 3. Nobody has checked
`fg_pct`'s 400 against a published FG% list, and NBA.com's FG% rule is 300
made field goals. Either the two should share a number or the comment on each
should say why they differ.

### A warehouse built before a view change is not detected

`player_season_advanced_stats` is a view, and its SQL is stored in the
warehouse file. Code that reads a column the stored view lacks gets a Binder
error; for `ts_pct`/`efg_pct` the template then falls through to the agent,
and nothing tells the user that `association data load` would fix it.
Confirmed against a real pre-change warehouse. What the agent answers in that
state was not measured (it needs ollama); it can reach the old 20-game shape
through `run_sql`. A check comparing the stored view's columns to what the
metrics name, at startup or in `data check`, would make the state visible.

### Qualifiers are flat across shortened seasons

The 550/480 floors assume an 82-game schedule. At 550 true-shooting attempts,
2020 qualifies 157 players and 2021 qualifies 155, against 174-184 in
2019 and 2022-2026. The 2012 lockout season (66 games) was not measured.
Published rules scale per team game ("per 82 team games"), and so could this -
but `min_sample_applied` is a single printed number, so a scaled floor also
needs the answer text to stay honest about it.

### Postseason floors are scaled, not calibrated

`ts_pct` 67 and `efg_pct` 59 are the season floors times 10/82. No published
postseason list applies a qualifier at all (StatMuse's 2025 playoff leader shot
150% on two attempts), so there was nothing to check them against. They leave
81-92 qualified players per postseason in 2025 and 2026.

### The Basketball-Reference rule was never read directly

Basketball-Reference, whose true-shooting qualifier is reportedly on
true-shooting attempts, answers 403 to automated fetches, including
`/about/rate_stat_req.html`. The floors were checked against StatMuse, which
states its own rules (725 points; 300 made field goals). If Basketball-
Reference publishes a modern TSA figure, it is worth comparing with 550.

### `min_sample` changed units for two metrics on the agent path

`get_leaderboard(metric="ts_pct", min_sample=50)` now means 50 attempts, not
50 games. The tool description says "games/minutes/attempts (depends on the
metric)" and the result now carries `min_sample_column`, but a question that
gives a games minimum ("best true shooting among players with 50 games") can
no longer be expressed through the tool for these two metrics; the agent has
to write SQL. `TABLE_SUMMARY` does not list the two new view columns (left
out for the preamble budget), so the agent has to `describe_table` to find
them.

### A fresh worktree cannot run the gates with `uv run` alone

`uv run` creates the worktree's venv without the `dev` extra, so
`uv run pytest -q` fails with `Failed to spawn: pytest` until
`uv sync --frozen --extra dev --extra docs --extra web` (CI's line) has run.
The "Before you commit" section of `CLAUDE.md` does not say so.
