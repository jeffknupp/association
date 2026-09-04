# Changelog

Notable changes to `association`, newest first. Each entry links back to the
commit that made it for the full story.

## 2026-09-04

- **NetPoints per-game data (net_points_player_game, net_points_team_game)**:
  opt-in via `--include-net-points-daily`. The source's per-game breakdown
  lives in a *different* S3 bucket than the season-level files, and this one
  rejects unsigned requests - reached via the same anonymous AWS Cognito
  identity-pool credential exchange espnanalytics.com's own frontend uses to
  read it (confirmed live: a plain unsigned request gets 403 AccessDenied;
  the identity pool ID is meant to be public, embedded in the site's own
  client-side JS - added `boto3` as a dependency to do the same exchange).
  One request per date already covered locally (not per player, not per
  game - one file covers every game played that date). Every field uses
  NBA.com's own player/team/game IDs, with no crosswalk to ESPN's provided
  anywhere in the data; resolved instead by matching (team, date) against
  this project's own `games` table (a team plays at most one game per date,
  so this is exact) and by exact player display-name match against
  `players` (ambiguous/unmatched names are dropped, not guessed). Confirmed
  live and handled: ESPN's `games.date` is UTC and can be a full day ahead
  of the US-local date NetPoints files under (an OKC @ NYK game ESPN stores
  as `2026-03-05T00:00Z` is filed under `2026-03-04`). Two real bugs found
  and fixed by checking actual coverage after the first backfill, not just
  trusting a clean exit code: (1) date+1 has to be tried BEFORE the exact
  date, not after - a team playing the same opponent on back-to-back nights
  (confirmed live: New Orleans @ LA Clippers on both 2026-03-19 and -20) has
  its own unrelated game sitting at the exact label date, which silently
  stole the match before the offset case ever ran; (2) a local date's true
  NetPoints label (date-1) has to be fetched even when no OTHER local game
  falls on that exact calendar day, or it's never fetched at all - confirmed
  live, a Lakers game was missed entirely this way, not just mis-resolved.
  Only NetPoints' own unique fields are kept; real box-score numbers ESPN
  already provides aren't duplicated from this second source. Backfilled
  locally: 1,654 dates.
- **NetPoints (net_points_player, net_points_team)**: fetches ESPN Analytics'
  current advanced player/team metric (successor to the discontinued Real
  Plus-Minus) from espnanalytics.com's public, unauthenticated S3-hosted JSON
  - a different domain from ESPN's own API, needing none of the TLS-
  impersonation tricks the rest of the fetcher relies on. Fetched by default
  as part of `data pull` (not opt-in), covered in `data check`'s new net_pts
  column, and backfilled into the local warehouse. Two real gotchas found and
  handled: NetPoints labels a season by the year it starts, not ends (off by
  one from every other table here, confirmed against this project's own
  games-played counts); and NetPoints uses its own team abbreviations that
  disagree with ESPN's for 9 of 30 franchises (e.g. GSW vs ESPN's GS, and the
  player file and team file don't even agree with each other for San Antonio)
  - both are converted at ingest time so the stored tables behave like every
  other table in the warehouse. Per-game NetPoints exists too but needs one
  HTTP request per player against a different (NBA.com) ID scheme with no
  direct ESPN-id crosswalk - deliberately left for a follow-up.
- **KNOWLEDGE_BASE: winner_team_id location, and win/loss tallies**: a live
  query ("Knicks' last 20 games and their record") surfaced two more bugs -
  the model referenced `tbs.winner_team_id` (winner_team_id only exists on
  `games`, not `team_box_stats` - a column-not-found error), and separately
  reported the win/loss record backwards (7-13 instead of the actual 13-7)
  because it tried to count wins/losses by re-reading a list it had already
  printed instead of computing the tally in SQL. A follow-up run then showed
  a *correct* aggregate record sitting next to 6 individually misclassified
  games, because the model summarized "wins against X, Y, Z" from memory
  instead of listing each game's own row. Tightened the existing team-game-log
  KNOWLEDGE_BASE entry and added a new one (CTE + window-function tally
  pattern, plus an explicit instruction not to collapse per-game detail into
  a hand-sorted summary). Verified live: all 20 games and the record now
  match ground truth exactly.
- **CHANGES.md, enforced via pre-commit**: added this changelog and a
  `changes-md` pre-commit hook (`scripts/check_changes_md.sh`) that fails any
  commit touching `src/` unless `CHANGES.md` is staged too - a manual entry
  is required, nothing is auto-generated from the commit message.
- **Team home+away game log SQL pattern** (`99df120`): two real queries for a
  team's last N games both got the answer wrong in different ways - one
  matched both sides of every league game with no team filter at all
  (doubled rows, wrong teams), the other filtered on `home_team_id` only
  (silently dropping every away game) and aliased the home team's name onto
  a "winner" column instead of using `games.winner_team_id` - reporting wins
  for games the team had actually lost. Added a KNOWLEDGE_BASE pattern
  pointing at `team_box_stats` (opponent_team_id/home_away baked in) and
  `games.winner_team_id` directly, plus computed `team_score`/`opponent_score`
  columns instead of raw home/away score. Verified live: all 20 rows now
  match ground truth exactly.

## 2026-09-03

- **Stop replaying thinking traces into the model's context** (`ec2fe6a`):
  `Agent.ask()` was feeding each turn's full reasoning trace back into the
  model's own context on every later tool-call round. Stripped it before
  appending to history - confirmed live, cut a follow-up iteration's
  prompt-eval time from ~5.6s to ~0.9s, and the saving compounds with each
  further tool-call round.
- **Fix advanced-stats query failures** (`fafe8fe`): a real query wrongly
  concluded advanced stats weren't loaded for the 2026 season. Root cause:
  the model compared `athlete_id` (a VARCHAR id) directly to a player's name,
  which is valid SQL that silently returns zero rows - and it read the empty
  result as missing data. `player_game_log` also never joined
  `player_advanced_stats`, so even a correct query against it would have come
  up empty. Fixed the view join and added two KNOWLEDGE_BASE entries (id-vs-
  name filtering, `ORDER BY games.date` for "first/last game" phrasing).
- **`data load` subcommand** (`77249a0`): rebuilding the DuckDB warehouse
  meant `data pull --build-db-only`, which always rescanned every table's
  Parquet files. `data load [--tables t1,t2,...]` rebuilds from Parquet
  already on disk, in full or scoped to a subset, without re-fetching.
  Replaces `--build-db-only`, which is removed.
- **Opt-in computed advanced stats** (`f971cfe`): added `player_advanced_stats`
  / `player_season_advanced_stats` (true shooting %, effective FG%, usage
  rate, Hollinger game score) as computed DuckDB views, gated behind
  `--advanced-stats`. ESPN's team season stats already carry the team-level
  equivalents natively; only the player side had a real gap. PER, Win Shares,
  BPM, and VORP are deliberately excluded - they need league-wide baselines a
  closed-form box-score ratio doesn't have.

## 2026-08-28

- **Full type annotation** (`94be7b9`): annotated all of `src/` and `tests/`,
  enforced going forward via `disallow_untyped_defs`/`disallow_incomplete_defs`
  in mypy. Caught two genuine bugs along the way: a `parse_standings` return-
  type mismatch, and a `Pipeline.client: ESPNClient | None` gap (fixed with a
  guarded `_live_client` property).
- **ruff/mypy pre-commit hooks** (`891292e`): added `ruff` and `mypy` (local
  hooks, split into separate `src`/`tests` invocations to avoid a spurious
  dual-resolution conflict) as pre-commit checks, and fixed the violations
  they surfaced.
- **Initial commit** (`440b40a`): resumable fetch from ESPN's undocumented
  stats APIs into compact Parquet flat files, a DuckDB analytics warehouse
  built from them, and a natural-language query interface powered by a local
  LLM via Ollama - no cloud API calls anywhere.
