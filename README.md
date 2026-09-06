# association

A local-first NBA stats pipeline: resumable fetch from ESPN's (undocumented)
stats APIs into compact Parquet flat files, a DuckDB analytics warehouse built
from them, and a natural-language query interface powered entirely by a local
LLM via [Ollama](https://ollama.com) — no cloud API calls anywhere.

```
association data pull --seasons 2020-2026        # fetch, resumable
association data load --tables games,standings   # rebuild the warehouse from Parquet already on disk
association data check                            # audit coverage vs. ESPN
association query "who led the league in assists?"
association ai --think                            # interactive REPL
```

## Setup

```bash
uv sync                          # or: pip install -e .
brew install ollama
ollama serve &
ollama pull qwen2.5:7b           # default model, ~4.7GB
ollama pull qwen3:8b             # optional: visible reasoning (--think), ~5GB
```

**Shell completion** (tab-complete subcommands, options, and `--log-level`'s
choices) — the CLI is built on [Click](https://click.palletsprojects.com),
which generates these directly from the command definitions, so there's
nothing to keep in sync by hand. Two ways to enable it:

- Source one of the ready-made scripts in [`completions/`](completions/):
  ```bash
  # bash
  echo 'source /path/to/association/completions/association.bash' >> ~/.bashrc
  # zsh
  echo 'source /path/to/association/completions/association.zsh' >> ~/.zshrc
  # fish
  cp completions/association.fish ~/.config/fish/completions/
  ```
- Or generate it fresh (picks up any future CLI changes automatically):
  ```bash
  eval "$(_ASSOCIATION_COMPLETE=bash_source association)"   # bash, in ~/.bashrc
  eval "$(_ASSOCIATION_COMPLETE=zsh_source association)"    # zsh, in ~/.zshrc
  _ASSOCIATION_COMPLETE=fish_source association | source    # fish, in ~/.config/fish/config.fish
  ```

The `completions/` scripts are generated, not hand-written - after changing a
command or option in `cli.py`, regenerate them with
`./scripts/generate_completions.sh`.

## Data model

Fetches teams, games/box scores (player and team level), standings, player
and team season stats, ESPN's Basketball Power Index, and optionally
play-by-play/shot charts/win probability. Grain and table design borrow the
dimension/fact split popularized by the `nbadb` project, built directly
against what ESPN's API actually returns rather than reimplementing it.

**Computed tables** (always built, not sourced from ESPN — see "Query engine"
below for why this isn't gated behind a flag):
`player_advanced_stats` and `player_season_advanced_stats` — true shooting %,
effective FG%, usage rate, and Hollinger game score, per game and per season.
ESPN's team season stats already carry these natively (`effectiveFGPct`,
`trueShootingPct`, `paceFactor`), but its player stats endpoint doesn't, so
only the player side needs a computed layer — see
[`fetch/advanced_stats.py`](src/association/fetch/advanced_stats.py) for the
exact formulas and why PER/Win Shares/BPM/VORP are deliberately excluded.

**NetPoints** — ESPN Analytics' current advanced player/team rating (points
contributed above average, split into offense/defense), from
[espnanalytics.com](https://espnanalytics.com), not ESPN's own API:

- `net_points_player` / `net_points_team` (season-level, **fetched by
  default**): public, unauthenticated JSON on S3. Confirmed live: no
  `robots.txt` disallow, no auth/CORS barrier. Uses its own team-abbreviation
  scheme (translated to this project's team_id at parse time — see
  `fetch/parse.py`'s `NET_POINTS_ABBREV_TO_ESPN`) and its own
  season-*starts* convention (converted to season-*ends* on ingest).
  `overall`/`offense`/`defense` are season CUMULATIVE totals, not a rate —
  `overall_per_100_poss`/`offense_per_100_poss`/`defense_per_100_poss` (plus
  `total_minutes`) come from a second, separate file on the same public
  bucket (`nba_net_pts100_data.json`), joined in by player+season+season-type.
  Confirmed live: espnanalytics.com's own "Net Points / 100 Poss" toggle
  fetches that second file rather than computing the rate in the browser, so
  this reuses ESPN Analytics' own numbers rather than approximating one.
- `net_points_player_fingerprint` (season-level, **fetched by default**):
  behind espnanalytics.com's "Net Pts Fingerprint" page — a per-player
  breakdown by shot/play type (2pt, 3pt, driving, fastbreak, rebound,
  turnover, and 17 more), each split into offense/defense/total NetPoints —
  66 NetPoints columns per player-season. One file per season on the same
  public bucket as the season-level file above, but this one returns 403
  (not the usual 404/400) for a season with no file yet — confirmed live,
  handled the same way `netpoints_client.py` already handles its bucket's
  own AccessDenied quirk. Keyed by NBA.com's own player id like the per-game
  data below, with the same exact-display-name-match resolution (ambiguous/
  unmatched names dropped, not guessed). Bio fields the source also carries
  (height, draft year, date of birth) aren't kept — real, already-sourced-
  from-ESPN data on `players`, not duplicated from a second source that
  might disagree.
- `net_points_player_game` / `net_points_team_game` (per-game, **opt-in**
  via `--include-net-points-daily`): the source's own per-game/per-player
  breakdown, one request per date already covered locally rather than per
  player or per game. This bucket rejects unsigned requests — reached via
  the same anonymous AWS Cognito identity-pool credential exchange
  espnanalytics.com's own frontend uses (see `fetch/netpoints_client.py`),
  needing `boto3`. Every field in this data uses NBA.com's own player/team/
  game IDs, not ESPN's, with no crosswalk provided — resolved instead by
  matching (team, calendar date) against this project's own already-fetched
  `games` table (a team plays at most one game per date, so this is exact,
  not a guess), and by exact player display-name match against `players`
  (ambiguous or unmatched names are left out rather than guessed). One
  real wrinkle, confirmed live: ESPN's `games.date` is UTC and can land a
  full calendar day ahead of the US-local date NetPoints files under (e.g.
  an OKC @ NYK game ESPN stores as `2026-03-05T00:00Z` is filed under
  `2026-03-04`) — resolution checks date+1 FIRST, falling back to the exact
  date only if that misses (checking exact-date first was a real, confirmed
  bug: a team playing the same opponent on back-to-back nights has its own
  unrelated game sitting at the exact label date, stealing the match before
  the offset case could run). The fetch set also includes each local date
  minus one day, not just the dates ESPN itself reports — otherwise a
  date whose only local game's true label is the day before, with no other
  local game to trigger fetching that day, is silently never fetched at
  all (confirmed live, a Lakers game was missed entirely this way). Only
  the genuinely new fields are kept (the NetPoints metric itself, plus
  usage/possession/win-probability-added context); real box-score numbers
  ESPN already provides (points, rebounds, minutes, ...) aren't duplicated
  from this second source.

## Design

**Storage** — one small Parquet file per unit of fetched work (one game, one
player-season, etc.), written atomically (temp file + rename). A file's
existence *is* the resumability checkpoint; no separate manifest to drift out
of sync.

**Completion markers** — resumability at the file level doesn't scale to
"is this whole season already done" without re-deriving it every run. A
`_complete/season=X/season_type=Y.marker` sentinel makes that an O(1) check:
once every game ESPN's schedule reports for a season+type is either played
(has a `games/*.parquet` row) or permanently settled as never-played
(postponed/cancelled — tracked separately as a `_resolved/*.marker`), the
season is marked closed and every future `pull` or `check` skips it outright
instead of re-hitting ESPN's schedule endpoint.

**In-season data stays current, not frozen at first pull** — per-game data
(box scores, play-by-play) is correctly immutable once fetched, but
season-*aggregate* data (`standings`, `team_season_stats`, `team_power_index`,
`player_season_stats`, and NetPoints' season-level tables) reflects ESPN's
own live, evolving computation while a season is in progress — plain
existence-check resumability would freeze these at whatever they were the
first time they were pulled. `standings`/`team_power_index`/NetPoints keep
re-fetching a season until it's no longer the *current* one
(`current_season()` in [`association/season.py`](src/association/season.py),
the same year-a-season-ends convention used everywhere else — a season keeps
re-fetching a little past when it's actually over, until the next one starts
in October, which is harmless since each is a single cheap request);
`team_season_stats`/
`player_season_stats` (one request per team/player) instead re-fetch based on
the season+type's own completion marker, so they stop as soon as that
season+type is genuinely done rather than waiting for the calendar to roll
over. `data check`'s table only reports row *counts*, not freshness — it
can't distinguish current-season data from stale current-season data, only
`--live` cross-checks against ESPN catch that.

**Warehouse** — a DuckDB file built from the Parquet tree via `read_parquet`
with `union_by_name` (tolerates schema drift between files, e.g. an early-
season stat ESPN hasn't computed yet) and *not* hive-partitioned (every row
already embeds its own `season`/`season_type`/`team_id` columns, so inferring
a second copy from the directory layout only invites type collisions).
Rebuilding the warehouse is idempotent and cheap — safe to re-run any time.
`data pull` rebuilds it automatically after fetching; `data load` rebuilds it
straight from Parquet already on disk without touching the network, and
`--tables` scopes that to a subset instead of rescanning everything (a full
warehouse rebuild rereads every Parquet file, which gets slow as the tree
grows).

**Query engine** — a local Ollama model gets four tools: `describe_table`
(schema lookup on demand, so table summaries stay short even for 100+-column
tables), `get_leaderboard` (a "top N players by X" query for a fixed, known
set of metrics — the season default, qualifying minimum sample, and
traded-player dedup are all resolved once in Python, in
[`query/metrics.py`](src/association/query/metrics.py), instead of
re-derived by the model from prose on every query — see "Design" below for
why), `run_sql` (read-only, `SELECT`/`WITH` only, backed by a read-only
DuckDB connection as a hard guarantee, for anything `get_leaderboard` doesn't
cover), and `render_shot_chart` (renders a static HTML/SVG court plot). A
growing `KNOWLEDGE_BASE` of concrete schema/domain gotchas (hoop coordinates,
a trade-mid-season double-counting trap in season stats, double-double/
triple-double definitions, ...) gets appended to whenever a real question
produces a wrong answer — small local models follow a copy-pasteable SQL
pattern far more reliably than an abstract instruction. Two hard backstops in
the agent loop: if the model ends a turn by printing SQL as prose instead of
calling `run_sql`, it extracts and runs it anyway rather than handing back an
unexecuted recipe; and if it tries to finalize an answer right after an
unrecovered `run_sql`/`get_leaderboard` error, it's refused a nudge to retry
instead — confirmed live, without this a query error once produced a
fabricated answer with literal `[Player Name 1]`-style placeholder text
presented as real data.

**Run history** — every `query`/`ai` call writes a file under `.history/`
(gitignored), named with a random hash, holding the command invoked, the full
tool-call/thinking trace, per-model-call and per-tool-call timing, and the
final answer (or a traceback, if the call raised) — regardless of whether
`--verbose` was passed. `--verbose` only additionally echoes that same trace
to stderr live; the file always gets everything, so a confusing or wrong
answer from an unwatched run still has its full evidence on disk afterward.
Every run also prints a one-line timing summary to stderr (total time, and
the model-inference-vs-tool-execution split) — in practice the model call
dominates end-to-end latency by a wide margin (confirmed live: a single
`get_leaderboard` call took 0.05s against two ~26s model-inference rounds).

The warehouse itself also carries two schema-level helpers so ad hoc
`run_sql` queries (not covered by `get_leaderboard`) don't have to re-derive
common correctness rules either: a `current_season()` SQL macro (the year a
season ENDS, computed from today's real date — the same rule `get_leaderboard`
applies in Python) and a `player_season_stats_deduped` view (one row per
player per season, already collapsed past the traded-player multi-row trap).

**Why a dedicated leaderboard tool at all** — real "top N players by X"
questions kept failing in different ways even with a `KNOWLEDGE_BASE` entry
covering each one: a season default that got dropped as soon as a second
filter was also needed, a minimum-sample rule that only applied to the one
metric it was written for, and — worst — two runs of the identical question
with the same *thinking* model producing two different metrics and answers
five minutes apart. Every `KNOWLEDGE_BASE` entry makes the model responsible
for remembering and re-deriving one more rule from prose on every query, and
that stops composing reliably as the list grows — more "thinking" time
doesn't fix a fundamentally stochastic process being asked to reproduce a
growing checklist exactly. `get_leaderboard` moves the correctness for a
fixed, known set of metrics out of prose and into code instead, so the
model's job shrinks to picking a metric name and filling a few slots.

## Project layout

```
src/association/
  cli.py            entrypoint: data pull|load|check, query, ai
  fetch/            client (curl_cffi — see below), endpoints, parse, storage, pipeline, warehouse
  check/            data coverage report, cross-checked live against ESPN
  query/            prompt/knowledge base, tools, court renderer, agent loop, REPL
scripts/
  backfill_markers.sh   re-derive completion markers for data fetched before they existed
completions/          generated bash/zsh/fish shell completion scripts (see Setup)
tests/              pytest, one file per source module
.history/           per-run command/trace/timing logs from query|ai (gitignored, see Run history above)
```

## Notable implementation details

- **TLS fingerprinting**: ESPN's CDN blocks plain `requests`/`httpx` with a
  403 even with a browser User-Agent — only `curl` and TLS-impersonating
  clients get through. The HTTP client uses `curl_cffi` for this reason;
  swapping it back to `requests` will silently break every fetch.
- **Preseason has no team season stats** on ESPN's side at all (confirmed
  live, not a gap) — the fetcher skips that request rather than re-querying
  an endpoint that structurally never returns data.
- Season numbering follows ESPN's convention: the year a season *ends*
  (the 2023-24 season is `season=2024`) - except NetPoints' own source data,
  which labels a season by the year it *starts*; converted on ingest so the
  stored `season` column matches every other table.
- NetPoints' season-level files (espnanalytics.com, not espn.com) need none
  of the TLS-impersonation tricks above - a plain request succeeds. Its
  per-game bucket is the opposite problem: it rejects unsigned requests
  outright, needing a Cognito credential exchange instead (see the NetPoints
  section above and `fetch/netpoints_client.py`).

## Testing

```bash
uv sync --extra dev
pytest -q
```

Tests are regression-first: most exist because a specific real bug (a pyarrow
type-collision crash, a shot-chart sentinel coordinate, a postponed game that
would've been re-fetched forever, ...) produced wrong or crashing output, not
because a line of code needed generic coverage.

## Linting / type checking

`ruff` and `mypy` run as pre-commit hooks (`ruff --fix`, then `mypy` over
`src/` and, separately, `tests/`). The whole codebase is fully type-annotated
and mypy runs with `disallow_untyped_defs`/`disallow_incomplete_defs` - new
code without annotations fails the hook, it's not just checking whatever
happens to already have hints.

```bash
uv sync --extra dev
pre-commit install     # one-time, wires the git hook
pre-commit run --all-files   # run manually against everything
```

A third hook enforces [CHANGES.md](CHANGES.md): any commit touching `src/`
must also update it, or the commit is rejected.

## Known limitations

- ESPN's stats API is undocumented and unofficial — endpoints or shapes can
  change without notice.
- ESPN's own Real Plus-Minus (RPM) isn't available at a stable JSON endpoint
  (only ever found rendered into a webpage) - NetPoints (see above) is its
  successor and *is* included. PER, Win Shares, BPM, and VORP are still not
  included, for a different reason: see `player_advanced_stats` above.
- NetPoints only has data back to the 2018-19 season (`season=2019`) -
  nothing earlier exists on their side, confirmed live (both the season-level
  and per-game endpoints).
- `net_points_team` (season-level) only ever reflects the single current
  season - `net_points_team_game` (per-game, `--include-net-points-daily`)
  has full history instead.
- `net_points_player_game` matches players by exact display-name text -
  formatting differences between ESPN's and NetPoints' own name spelling
  (accents, suffixes) will leave a real player's game rows unmatched rather
  than wrongly matched, but that does mean a small amount of real coverage
  gets dropped silently instead of guessed.
- `data check --live` cross-checks are opt-in and can be slow for seasons
  without a local completion marker yet — `pull` first to build those up.
