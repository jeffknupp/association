# Known issues

Problems found while working and not fixed, each with what was measured and
what was not. Newest first. Delete an entry when it is fixed, and say which
commit fixed it in `CHANGES.md` rather than here.

## Found while narrowing ambiguous names to the season asked about

### A player the router drops is answered as "the league"

"most points curry scored in a game this season" routed to `single_game_high`
with no `player` slot at all (qwen2.5:3b, temperature 0, 2026-09-11), and the
answer was "Bam Adebayo had the most points in a single game in the 2026
regular season: 83" - fluent, fast, and about a different question.

Nothing downstream can repair it as things stand. `scope_from_question`
restores a dropped player only for `PLAYER_REQUIRED_INTENTS` (`record_when`),
deliberately: where the player is optional an empty slot means the league, and
filling it risks a player the question only appears to name ("best" is Travis
Best). `players_named_in` could not restore this one anyway, since "curry" is a
whole word of six players' names. What would work is noticing that the
question names a player-shaped word the slots lost and asking. Seen on this
one wording; how often a check like that would fire on the routing corpus was
not measured.

### "How did curry do against the celtics" falls through from `player_compare`

The question routes two ways. Reported in the wild: `player_stat` with
`opponent='Boston Celtics'`. Measured on 2026-09-11: `player_compare` with
`players=['Stephen Curry', 'Boston Celtics']`. `scope_from_question` now moves
the Celtics into `opponent` and leaves one player, but the intent stays
`player_compare`, which cannot honor an opponent, so `check_scope` raises and
the question goes to the agent. `player_stat` answers it correctly: "Stephen
Curry played 43 games in the 2026 regular season, none of them vs the Boston
Celtics", which checks out - he has no box-score row in either Warriors-Celtics
game (2026-02-20, 2026-03-18). Handing a `player_compare` that has been reduced
to one player to `player_stat` would reach that answer. What the agent says
instead was not measured; it needs ollama.

### A router-invented name near a real one falls through instead of asking

"how many rebounds does davis average" routed to `player_stat` with
`player='Davies (Davic)'`. `override_invented_players` counts it as grounded,
since "davies" is one edit from "davis". Nothing matches it, and
`suggest_players` offers nothing, so the template raises and the question goes
to the agent rather than asking which Davis. The parenthesized second token is
the likely reason the suggestion pass finds nobody, because every token must be
near some word of the name, but that was not confirmed. Seen once.

### Narrowing counts a row with no minutes as having played

A candidate survives narrowing with any row in the table for the season, and
`player_game_log` and `player_box_stats` carry rows for games a player did not
play. JamesOn Curry has 84 of them in the game log - 82 with Chicago in 2007-08
and 2 with the Clippers in 2009-10, all with no minutes - against a single
season line (2010, one game, no points). So a player who only sat on a bench
stays in a clarification for that season. It errs toward asking and never
toward a wrong player. Filtering on minutes, as `conditions._played` does,
would tighten it. How many asks it widens was not measured.

### `player_history` reads each player's last N seasons on record, not the last N seasons

"Curry's scoring over the last 4 seasons" reads `season <= 2026 ... LIMIT 4`
per player, so for Dell Curry it would list 1999-2002, labeled with those
years since the header now names the range the rows reach. Narrowing follows
what the template reads - any season up to the one it is anchored at - so it
cannot drop retired players from that question. It asks about all six Currys,
naming Seth and Stephen first. If the template read the calendar window the
question names, narrowing could read it too, though `narrow_to_available`
would need a lower bound it does not take today.

### A clarification names every candidate from the season, however many

`Ambiguous.active` means a clarification never counts an in-season candidate
away, so it is only as short as the season's list. The surname with the most
players in one season is Williams: 15 in 1998 and 1999, 14 in 2026. A
fragment reaches further - "Will" names 18 players in 2026 and 20 in 1998.
Whether a twenty-name question reads acceptably, in the CLI or on the web page,
was not checked.

### The CHANGES.md hook passes when nothing is staged

`scripts/check_changes_md.sh` reads only `git diff --cached`, so
`pre-commit run --all-files` on an unstaged tree reports `CHANGES.md
updated....Passed` with `src/` changed and `CHANGES.md` untouched. CLAUDE.md
says to `git add` first, which does make it check - watched failing with `src/`
staged alone. The hook could say it checked nothing instead of reporting a
pass.

### The API docs print a literal `:rtype:`

In a docs build made on 2026-09-11, before this merge, 14 of 38 generated API
pages showed `:rtype: <type>` as body text. It appears in functions nobody
touched recently (`nicknames_in`, `override_nicknames`, `no_match`), and in
each case the docstring ends in a `versionadded`/`versionchanged` directive
without a `Returns:` section. `suggest_players`, which has a `Returns:`
section, and `resolve_team`, which has no directive, render correctly.
`sphinx_autodoc_typehints` inserting the field without a blank line before it
is the likely cause (`autodoc_typehints = "description"`), but that was not
confirmed.

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
