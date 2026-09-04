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

## Data model

Fetches teams, games/box scores (player and team level), standings, player
and team season stats, ESPN's Basketball Power Index, and optionally
play-by-play/shot charts/win probability. Grain and table design borrow the
dimension/fact split popularized by the `nbadb` project, built directly
against what ESPN's API actually returns rather than reimplementing it.

**Computed tables** (opt-in via `--advanced-stats`, not sourced from ESPN):
`player_advanced_stats` and `player_season_advanced_stats` — true shooting %,
effective FG%, usage rate, and Hollinger game score, per game and per season.
ESPN's team season stats already carry these natively (`effectiveFGPct`,
`trueShootingPct`, `paceFactor`), but its player stats endpoint doesn't, so
only the player side needs a computed layer — see
[`fetch/advanced_stats.py`](src/association/fetch/advanced_stats.py) for the
exact formulas and why PER/Win Shares/BPM/VORP are deliberately excluded.

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

**Query engine** — a local Ollama model gets three tools: `describe_table`
(schema lookup on demand, so table summaries stay short even for 100+-column
tables), `run_sql` (read-only, `SELECT`/`WITH` only, backed by a read-only
DuckDB connection as a hard guarantee), and `render_shot_chart` (renders a
static HTML/SVG court plot). A growing `KNOWLEDGE_BASE` of concrete
schema/domain gotchas (hoop coordinates, a trade-mid-season double-counting
trap in season stats, double-double/triple-double definitions, ...) gets
appended to whenever a real question produces a wrong answer — small local
models follow a copy-pasteable SQL pattern far more reliably than an abstract
instruction. As a hard backstop, if the model ends a turn by printing SQL as
prose instead of calling `run_sql`, the agent extracts and runs it anyway
rather than handing back an unexecuted recipe.

## Project layout

```
src/association/
  cli.py            entrypoint: data pull|load|check, query, ai
  fetch/            client (curl_cffi — see below), endpoints, parse, storage, pipeline, warehouse
  check/            data coverage report, cross-checked live against ESPN
  query/            prompt/knowledge base, tools, court renderer, agent loop, REPL
scripts/
  backfill_markers.sh   re-derive completion markers for data fetched before they existed
tests/              pytest, one file per source module
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
  (the 2023-24 season is `season=2024`).

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
  (only ever found rendered into a webpage), so it isn't included. PER, Win
  Shares, BPM, and VORP are also not included, but for a different reason:
  see `player_advanced_stats` above.
- `data check --live` cross-checks are opt-in and can be slow for seasons
  without a local completion marker yet — `pull` first to build those up.
