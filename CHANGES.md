# Changelog

Notable changes to `association`, newest first. Each entry links back to the
commit that made it for the full story.

Released versions follow [semantic versioning](https://semver.org): the major
number changes when something that used to work stops working, the minor
number when something is added, the patch number for fixes. What counts as
"something that used to work" here is the CLI surface - command and option
names, output shapes that scripts parse - and the documented Python API, not
the internal query templates or the router's intent set, which are expected to
grow continuously.

Sections dated rather than numbered predate the first release, when the project
had no published version to be compatible with.

## Unreleased
- **`head_to_head` no longer refuses a matchup just because the router split
  the two teams across slots**: "how many times did the 76ers play Boston?"
  (a city name, not a nickname) reliably routed the second team into `teams`
  as a one-element list and the first into the singular `team` slot instead
  of both into `teams`, so the template's two-name check failed and the
  question fell through to the agent - which then answered 45 games for a
  pair of teams that met 4 times, from unparenthesized SQL that let its
  season filter apply to only one team (`home_team_id = A OR home_team_id = B
  AND season = ?`). `head_to_head` now treats a `team` slot as a third
  candidate rather than rejecting the question, and the agent's knowledge
  base gained a worked example of the correct parenthesization for anyone who
  still reaches it. `scripts/check_routing.py` and
  `tests/query/test_templates.py` both gained coverage for the split-slot
  shape.
- **Command reference examples render as real code blocks**: the CLI epilog's
  example commands and setup instructions were rendering in the generated
  docs (`commands.rst`) as an RST line block - preserved line breaks, but
  plain text, with no monospacing, highlighting, or copy button. A
  `sphinx-click-process-epilog` hook in `docs/conf.py` now rewrites those runs
  into `.. code-block:: console` blocks; `association --help`'s terminal
  output is unchanged. Docs also gained `sphinx-copybutton`, so every code
  block across the site - not just this one - now has a copy button.
- **Fix: the three-point line rendered upside down in shot charts**. The arc
  in `query/court.py`'s SVG path used the wrong sweep-flag, so instead of
  bowing away from the basket toward half court it drew the minor arc on the
  near side of the chord - dipping *below* the baseline instead. Confirmed via
  the SVG spec's own endpoint-to-center arc math (the arc's midpoint landed at
  y=478, past the baseline at y=470, instead of y=182 above the corners).
  `tests/query/test_court.py` gained a regression test that parses the
  rendered arc and asserts it bulges toward smaller y.

## 1.0.0 - 2026-09-07
- **Semantic versioning, and tooling to hold to it**: `pyproject.toml` is now
  the only place a version number is written. The package reads it back through
  `importlib.metadata.version` and exposes `association.__version__`,
  `docs/conf.py` imports that instead of repeating the literal, and the CLI
  grew `-V`/`--version`. The publish workflow already parsed the same file to
  check the tag agrees, so there is one source of truth and nothing left to
  forget on a bump.

  `scripts/bump_version.py` takes `major`, `minor`, `patch` or an explicit
  version, and optionally commits and tags. It refuses to run on a dirty tree,
  refuses to reuse an existing tag (PyPI would not accept the version twice
  either), and requires a `## Unreleased` section in this changelog to rename -
  a release with no description of what changed is worse than one that failed
  to happen. It never pushes: pushing is the step that makes a release
  irreversible.

  `scripts/release.sh` creates the GitHub release from an already-pushed tag,
  using the changelog section for that version as the release notes so the two
  cannot disagree. It re-checks that the tag, the packaged version and the
  pushed tag all match before prompting, because publishing the release
  triggers a PyPI upload that cannot be undone.

  The `Documentation` URL now points at
  <https://association.readthedocs.io/en/latest/> rather than the GitHub README.

- **Docs build on Read the Docs**: `.readthedocs.yaml` builds the Sphinx site
  on every push and previews it on pull requests. It uses `build.commands` with
  `uv sync --frozen` rather than the `python.install` shorthand, so the
  published docs are built from `uv.lock` instead of whatever a resolver picks
  on the builder - this toolchain has already been broken once by an unpinned
  upgrade (Sphinx 9 against sphinx-click), and docs that drift from what CI
  verified are worse than docs that fail loudly.

  `scripts/build_docs.sh` now takes an optional output directory, defaulting to
  `docs/_build/html`, so Read the Docs builds through the same script as
  pre-commit and CI. One caller of `sphinx-build` means the `-W --keep-going`
  that gates a commit is the same one that gates the published build, rather
  than two flag lists drifting apart.

- **Packaged for PyPI**: the project now carries the metadata a published
  package needs - `readme`, a BSD 3-Clause `license` and `LICENSE` file,
  author, keywords, 15 classifiers and `[project.urls]` - and builds a clean
  119KB wheel and 200KB sdist. Both were verified by installing into a fresh
  virtualenv outside the repository and running the CLI, which is the only
  check that actually proves the entry point and `py.typed` survive packaging.
  `setuptools` is pinned to `>=77` because the SPDX-string `license` field is
  an error on older versions.

  A `MANIFEST.in` rounds out the sdist with the shell completions, the
  changelog and the tests, and explicitly prunes the local artifacts. The
  prunes are belt-and-braces - setuptools' default sdist would not sweep up the
  266MB warehouse or the 736MB data tree anyway - but the cost of being wrong
  there is uploading hundreds of megabytes of scraped data to a public index.

  Two things were wrong for anyone installing from PyPI rather than a checkout.
  The README's setup block still told users to pull only `qwen2.5:7b` as "the
  default model", which has been stale since the router was split out: a fresh
  install following it would have no `qwen2.5:3b` and the fast path would fail
  on every question. And its ten relative links to source files would 404 on
  PyPI, which renders the README standalone, so they are now absolute.

  `.github/workflows/publish.yml` publishes via PyPI Trusted Publishing (OIDC),
  so no API token is stored anywhere. It runs the full gate suite on the tagged
  commit rather than trusting CI was green, checks that the git tag matches the
  packaged version, and runs `twine check --strict` - PyPI rejects an
  unrenderable README at upload time, once the version number is already spent.
  A manual `workflow_dispatch` can rehearse the whole thing against TestPyPI.

  New `installation` and `releasing` documentation pages. The one step that
  cannot be automated - registering the trusted publisher on PyPI - is written
  down in `releasing`.

- **`ruff format` adopted, and gated**: the formatter reads the same
  `line-length = 200` the linter does, so it *joins* the long prompt and SQL
  strings up to that width rather than wrapping them at 88. The assumption
  behind leaving it out - that it would fight the deliberate line length - was
  simply wrong; nothing it produced exceeded 200 columns.

  Reformatting touched 26 files across `src`, `tests` and `scripts`. Because
  much of what moved is prompt text that the router and fall-through agent
  depend on byte-for-byte, the change was verified by hashing
  `KNOWLEDGE_BASE`, `SYSTEM_PROMPT_TEMPLATE`, `ALWAYS_ON_TOPICS`,
  `ROUTER_PROMPT` and `ROUTER_SCHEMA` before and after: identical. The edits
  are all implicit string concatenations being joined (same value, one line)
  and long SQL being un-wrapped. One is a real fix - a docstring that opened
  with a quote character (`""""Most games with N+ ...`) was ambiguous to read
  and is now spaced.

  `ruff format --check src tests scripts` runs in CI, and a `ruff-format` hook
  runs in pre-commit ordered *after* `ruff --fix`, so an autofix cannot leave a
  file unformatted.

## 2026-09-06

- **100% public-API type completeness, and CI**: `pyright --verifytypes
  association --ignoreexternal` now scores **100%**, up from a 90.4% baseline
  (311 known, 14 ambiguous, 19 unknown of 344 exported symbols). Getting there
  needed a PEP 561 `py.typed` marker - without it pyright reports "No py.typed
  file found" and scores 0% of zero symbols - plus annotations for module
  loggers and instance attributes, and real type arguments for the bare
  `dict`/`list`/`tuple` in signatures. `parse.py` gained a `Row` alias
  (`dict[str, Any]`) so its return types say what they are rather than merely
  satisfying the checker, distinct from the existing `JSON` alias for what ESPN
  sent us.

  `scripts/check_types_complete.sh` runs the gate. It installs the package into
  a scratch directory first, because `--verifytypes` inspects an INSTALLED
  package and the editable install in `.venv` resolves through an import hook
  pyright cannot follow.

  Adding `py.typed` had a consequence worth recording: `mypy tests` went from 6
  errors to 43, because the package had been treated as untyped when checking
  tests, so `Pipeline` and friends were `Any` and every mismatch passed
  silently. (It was already red at HEAD - the pre-commit hook had not been
  catching it.) Fixed properly rather than suppressed: `Pipeline` now declares
  `JsonFetcher` and `DailyNetPointsFetcher` protocols, which is the honest
  dependency - it never touches throttling, retries or TLS impersonation, only
  `get_json` - and means a test double no longer has to inherit a network
  client to stand in for one. That resolved 37 of the 43 on its own.

  New `.github/workflows/ci.yml` runs the whole suite on push and pull request:
  ruff, mypy over `src` and `tests` separately, type completeness, docstring
  coverage, pytest, and the docs build with `-W`, uploading the built HTML as
  an artifact. `uv sync --frozen` fails if the lockfile has drifted from
  pyproject rather than silently re-resolving. The one step left out was `ruff
  format --check`, on the assumption that the formatter would fight the
  deliberate line-length of 200 - which turned out to be wrong, and was
  reversed the next day (see above).

- **Sphinx documentation, and hooks that keep it honest**: `docs/` builds a
  full site - architecture, a command reference generated from the Click CLI
  itself (so it cannot drift from the flags the code accepts), usage recipes
  for the things people actually do (fetching a season range, checking
  consistency, forcing a refetch, keeping a live season current), a
  data-sources page, the changelog, and an API reference covering every module.

  The API tree is generated recursively by `autosummary` at build time rather
  than from checked-in stub files, so a new module appears without anyone
  remembering to add it - confirmed: 30 source modules, 30 generated pages.

  Docstring coverage went from 51% to **100%** (120 of 120 public items, every
  module), and two pre-commit hooks keep it there: `scripts/build_docs.sh`
  builds with `-W` so a broken cross-reference or a missing module fails the
  commit, and `scripts/check_docstrings.py` fails on any undocumented public
  item - Sphinx catches malformed docs but renders an undocumented function
  perfectly happily, just uselessly.

  The data-sources page states plainly what the endpoints are: publicly
  readable without authentication, undocumented, unsupported, and subject to
  change without notice - a warehouse built from them is a snapshot of what
  they returned that day. It also documents why the fetcher is deliberately
  unhurried (5 req/s by default, checkpointed, nothing re-fetched unless
  asked).

  Sphinx is pinned below 9 because sphinx-click 6.x calls
  `sphinx.ext.autodoc.mock` as a function, which is a module there.

- **Reject scope slots a template cannot honor**: three live failures in a row
  were slots the router extracted CORRECTLY and the template silently dropped -
  a shot chart of "his last game" drew the whole season (803 attempts, not 14),
  NetPoints for "his last game" reported all 43, and a record "over their last
  10 games" would have covered the full season. `check_routing.py` cannot catch
  any of them, because routing was right every time.

  `order` and `date` are now declared: each template lists the scope slots it
  honors, and the dispatcher falls through when a question scoped to particular
  games meets a template that would answer for a different span. Slow beats
  confidently wrong, which is the trade this design keeps making.

  Measured across the 38 routing cases before adding it, exactly one slot was
  emitted-but-unhonored: `order` on `shot_distance`. Rather than accept a
  fall-through there, `shot_distance` now honors it too - "his average 3pt shot
  distance in his last game" is a real question, and the data is the same table
  a single-game chart already reads. A test asserts every template claiming to
  honor a slot actually reads it, so the declaration cannot drift from the code.

- **NetPoints for a single game**: "Show steph curry's netpoints from his last
  regular season game" returned the whole season - 43 games, 1,329 minutes -
  even though the router had correctly emitted `order: "recent"` and
  `limit: 1`. The template ignored both. Same silent substitution as the shot
  chart fixed alongside it, and the same shape of fix: `player_netpoints` now
  honours `order`.

  A single game is a genuinely different answer rather than a filtered one.
  Per-game NetPoints live in `net_points_player_game`, which is opt-in
  (`data pull --include-net-points-daily`), uses the normal NUMERIC season_type
  unlike `net_points_player`, and carries no play-type fingerprint - that is
  season-level only, and the output says so rather than leaving its absence
  looking like missing data. It reports o/d/t NetPoints, possessions on each
  side, and win probability added. Without the opt-in table the template falls
  through rather than quietly answering for the season.

  Cross-checked against the raw table: Curry's 2026-04-13 game reads 2.3857
  offense / 3.924 defense / 6.3096 total, and it is the same game the shot
  chart resolves for "his last regular season game".

- **Fouling out, and shot charts of a single game**: two failures reported from
  real use.

  "How many times has Wembanyama fouled out of a game" - the router got the
  shape right (threshold_count) but emitted stat "fouls committed" with
  threshold 1. The template correctly refused both, and the question then hung
  in the agent until it was aborted at 95s. `fouls` was missing from the
  threshold vocabulary entirely, and "fouling out" is six personal fouls - an
  NBA rule rather than a judgement call, and not something a 3B reliably knows.
  `fouls` is now a threshold stat, and the phrase is normalized in the router
  to stat=fouls, threshold=6, so the rule lives in one place. Wembanyama fouled
  out twice in 2026.

  "Create a shot chart of steph curry's last regular season game" charted the
  whole season - 803 attempts instead of that game's 14. Nothing scoped the
  request to one game. `shot_chart` now honours `order` the same way `game_log`
  does, resolving it to that game's event_id. (14, not the 22 rows the game
  has: free throws carry no court coordinates and are excluded from a chart.)

- **NetPoints fingerprint: the six categories that actually partition the
  total, and defense as its own section**: two corrections to how the
  fingerprint was presented.

  First, the defense column was present but effectively invisible. Sorting one
  combined table by total magnitude buries every defensively significant play
  type below categories whose defense is ~0 - for SGA, `turnover` carries the
  largest defensive value of any category (170.9) and landed 15th of 21, with
  `foul` (-82.3) at 3rd only because its OFFENSIVE value is large. Offense and
  defense now get a section each, sorted by their own side, which is also how
  espnanalytics.com presents it.

  Second, and this reverses a claim made in the previous commit: the categories
  ARE a partition, six of them. `two_pt`, `three_pt`, `free_throw`, `turnover`,
  `rebound` and `foul` sum EXACTLY to the offensive and defensive totals -
  verified against the separately stored `net_points_player.offense`/`.defense`
  for every top-minutes player in 2026, maximum deviation 0.005. The earlier
  "they do not sum" note came from summing all 21 categories, which mixes the
  partition with 15 overlapping descriptive slices (a driving layup at the rim
  counts in `driving`, `layup` AND `rim`, and those 15 sum to roughly twice the
  total on their own).

  So the six are shown as the breakdown, each section printing its own sum so
  the reader can check it against the headline - offense 8.55 plus defense 1.36
  is 9.91 per 100 possessions, the figure `net_points_player` stores
  independently. The overlapping slices follow as labeled detail, kept out of
  the column that is meant to add up.

  Also fixes a shadowing bug mypy caught while restructuring: the category list
  was bound to `detail`, which the headline block already uses for a list of
  strings - the same class of bug as the `per_100` shadowing fixed in the
  previous commit.

- **player_netpoints: one player's NetPoints and play-type fingerprint**:
  reported from real use - "what were SGA's netpoint stats this season"
  answered "Nikola Jokic leads the team in NetPoints this season with a total
  of 451.24", after 149s and five model calls.

  NetPoints was exposed only as leaderboard METRICS - ways to rank the league -
  so a question about one player's NetPoints had no shape to land in. The
  router reasonably chose `player_stat` with stat "netpoints"; that template
  correctly refused the unsupported stat and fell through (the guard added with
  shot_distance working as intended); and the agent then called
  `get_leaderboard` for the league, dropped SGA entirely, and reported Jokic.

  `player_netpoints` reports the season line (overall/offense/defense, per 100
  possessions, minutes, games) from `net_points_player`, plus the 21 play-type
  categories from `net_points_player_fingerprint` sorted by magnitude - the
  breakdown behind espnanalytics.com's "Net Pts Fingerprint". It handles both
  of that data's traps: `net_points_player` uses its own STRING season_type
  (filtering it with the numeric one silently matches nothing) and the
  fingerprint table has no season_type column at all.

  The fingerprint is reported **per 100 possessions by default**, since season
  totals mostly rank by playing time and the fingerprint exists to compare
  players; `rate: "total"` asks for totals, and raw totals stay in the result
  data either way. Cross-checks against the independently stored season rate:
  the `total` category over 4,725 possessions gives 9.91 per 100, matching
  `overall_per_100_poss` exactly.

  Found by the tests while adding that: `_phrase_netpoints` unpacked the
  headline row over its own `per_100` parameter, so a `rate: "total"` request
  printed season totals under a "per 100 possessions" heading. Routing 38/38.

- **player_history: one player across several seasons**: reported from real
  use - "what was klay thompson's 3pt percentage over the past 4 seasons (with
  attempts/makes)" answered with the LEAGUE's true-shooting leaders for 2020,
  four of them, with assists and rebounds columns. Wrong player, wrong stat,
  wrong season, wrong columns.

  The cause was structural rather than a slip: every template answered about a
  SINGLE season, so a multi-season question had nowhere to go and the router
  put it in the nearest shape it had. `player_history` reports one player's
  stat by season, most recent first, defaulting to four seasons and taking the
  count from `limit`. A percentage comes with its makes and attempts, since a
  percentage without volume behind it is the thing people immediately ask "out
  of how many?" about - which is exactly what the question asked for. The
  router's stat vocabulary gained the shooting percentages
  (threePointFieldGoalPct, fieldGoalPct, freeThrowPct), which had no
  representation at all.

  `leaderboard` now also refuses outright when a `player` slot is set: it ranks
  the league or a team, never one named person, and silently dropping the named
  player is how Klay Thompson's question came back about Robert Williams III.
  That guard is independent of the routing fix, and would have turned this
  wrong answer into a slow one rather than a confident one.

  Also fixes the guard test added with `player_compare`: its parser treated
  wrapped continuation lines in the router prompt as intent names, so it failed
  on words like "did" and "the" once descriptions grew to two lines. Routing
  check 36/36.

- **shot_distance, and no more silent stat fallback**: reported from real use -
  "what was steph curry's avg 3pt shot distance" answered "Stephen Curry
  averaged 26.6 points, 3.6 rebounds and 4.7 assists per game". Two bugs, and
  the first one was mine rather than the model's.

  `player_stat` treated a stat it did not recognize the same as no stat at all
  and fell back to its default points/rebounds/assists line - a silent
  substitution inside a template, which is precisely what templates exist to
  prevent. `player_compare` had the identical bug. Both now distinguish "no
  stat named" (default line, fine) from "stat named but unsupported" (fall
  through). `PLAYER_STAT_COLUMNS` also gained the shooting stats the router
  emits routinely - threePointFieldGoalsMade, fieldGoalsMade, freeThrowsMade -
  which were missing and so triggered exactly that fallback.

  Forced to the agent, the question then failed a second way: the agent wrote
  the correct distance formula from its KNOWLEDGE_BASE entry but dropped BOTH
  the 3-point filter and the season filter, reporting the all-shots,
  all-seasons average of 16.94 feet as a current-season three-point distance.
  The real figure is 23.6.

  So shot distance earned a template. The hoop is at (25, 5.25) and free throws
  carry NULL coordinates - a fixed formula over known columns, nothing that
  needs judgement. It scopes to the current season like every other template
  and reads the shot value from either `shot_value` or the equivalent stat.

  Also adds a deliberately tiny list in the router that forces questions no
  template computes to the agent regardless of the model's classification, for
  subjects that read like a supported shape ("points in the 3rd quarter") and
  are otherwise absorbed by a near-miss template. Shot distance was its first
  entry and left it the same day by earning a template, which is the intended
  lifecycle. Routing check 34/34.

- **head_to_head, and a code-side guard for an id filter that can never
  match**: reported from real use - "how many times did the 76ers play boston?"
  answered "the Philadelphia 76ers did not play against the Boston Celtics",
  twice, in 93s and 143s. They played four times.

  Four separate defects behind one wrong answer. (1) No template covered games
  between two teams, so the router chose `team_record`, invented `limit: 100`,
  and the limit guard added in stage 2.4 correctly rejected it - falling through
  to the agent. (2) The agent wrote `home_team_id = 'PHI'`, but team_id is an
  opaque all-digit VARCHAR ('20'), so the filter silently matched nothing. (3)
  Its `A OR B AND season = current_season()` applied the season filter to only
  one side of the matchup, since AND binds tighter than OR. (4) It reported a
  zero count as a fact about the world rather than a suspect result.

  Defect 2 is the important one, and it is NOT a consequence of the recent
  knowledge-base trim: the rule against it is ALWAYS-ON, and the assembled
  prompt for that exact question contained it verbatim, including
  `WHERE home_team_id = 'NY'` spelled out as a worked WRONG example. The model
  had the rule in front of it and wrote the wrong form anyway - which is this
  project's whole thesis restated, so the fix is code, not more prose.

  New `head_to_head` template resolves both team names to ids, counts games in
  both home/away directions, parenthesizes the matchup so a season filter cannot
  bind to one side only, and reports the series record. It defaults to the
  current season like every other template rather than answering all-time.
  Separately, `run_sql` now returns a `warning` whenever a query compares an
  `*_id` column to a non-numeric literal - unconditionally, not just on an empty
  result, because the failing query was a `COUNT(*)` that returns one row
  containing 0 rather than no rows at all. 2.16s and correct. Routing 32/32.

- **Trim the knowledge base to what the fall-through path actually needs, and
  keep models warm**: with every common shape ported to a template, fourteen
  `KNOWLEDGE_BASE` entries had nothing left to do - game logs across home and
  away, records alongside a game list, first/last game, single-game-vs-season
  totals, per-game averages, traded-player dedup, double-doubles, shot-chart
  guidance and `made_only`, rate-stat minimum samples, NetPoints rate-vs-total,
  fingerprint categories, "top N by NetPoints alongside box-score stats", and
  "a specific game implies its season". Each is a rule in code now, tested,
  where it cannot be truncated away or half-remembered.

  The criterion, since it is the reusable part: a SHAPE a template owns goes; a
  SCHEMA FACT that makes arbitrary SQL silently wrong or silently empty stays.
  So `fieldGoalsMade` already including threes stays (wrong math, not an
  error), the ISO-timestamp date trap stays (zero rows, no error), NetPoints'
  string `season_type` stays (matches nothing, no error), and per-quarter
  scoring and shot distance stay because no template derives them. Two tests
  pin both halves of that criterion. 6,350 tokens across 26 entries became
  2,776 across 12, and an assembled preamble is now ~5,000-5,500 tokens.

  Also corrects two claims from the previous commit, both measured wrong.
  ollama's default ALREADY keeps both the router and agent models resident -
  `/api/ps` reports qwen2.5:7b and qwen2.5:3b loaded together - so
  `OLLAMA_MAX_LOADED_MODELS` needs no change, and setting it from this process
  would be a no-op anyway since the server reads it (a systemd unit here). And
  the ~175s a fall-through costs is not a model swap: it is prefill of the
  agent's preamble at roughly 33 tok/s under a 6-core CPUQuota, which happens
  with both models already resident.

  What does bite is the idle unload after ~5 minutes, costing ~15s to reload
  the router. Requests now pass `keep_alive` (30m, override with
  `ASSOCIATION_KEEP_ALIVE`), which is a per-request field and so actually works
  from the client. Routing check 30/30.

- **Read the season out of the question text, in code**: "last season" is
  arithmetic on a calendar, not language understanding, and it was the slot the
  router most reliably dropped - "plot Curry's threes from last season" and
  "best true shooting percentage last season" both came back with no season at
  all, so the answer silently covered the CURRENT season. Both were carried as
  `known_gap` cases in `check_routing.py`; both are now fixed and the check is
  30/30 with no gaps outstanding.

  New `query/season_text.py` handles "last/previous season", "this season",
  explicit years, and season spans in either form - `2023-24` and `2023-2024`
  both mean the season ENDING in 2024, ESPN's convention and the form a
  hyphen-blind year regex gets exactly one year wrong. It declines to guess
  when a question names two different years ("compare 2023 and 2024"), ignores
  out-of-range years and numeric thresholds ("30+ point games" is not a
  season), and does not mistake "last 5 games" for "last season".

  Deliberately additive rather than a replacement: the model's `season` and
  `season_ref` slots are KEPT as a fallback, so code wins where it finds an
  answer and the model's slot applies where it does not, and phrasings the
  parser has never seen ("in his rookie year") route exactly as well as before.
  That choice follows directly from the negative result recorded above -
  removing those properties from the schema bought nothing elsewhere, so there
  was no reason to give up the fallback to get the fix. Confirmed live: Curry's
  threes now chart the 2025 season rather than 2026, and true shooting resolves
  to 2025.

- **Route on a 3B, generate SQL on the 7B**: routing and SQL generation are
  different jobs and were sharing one model. Benchmarked over the 30 real
  questions in `scripts/check_routing.py` (new `scripts/bench_router_models.py`
  reproduces it), every model from 1.5B to 8B scored 28-30/30 on routing,
  because constrained decoding does the structural work and the model only has
  to classify and fill slots - not a 7B-sized job:

      qwen2.5:7b   4.7GB  30/30 intent  29/30 +slots  2.00s median
      phi4-mini    2.5GB  29/30         29/30         1.42s
      qwen2.5:3b   1.9GB  29/30         29/30         1.12s
      llama3.2:3b  2.0GB  29/30         28/30         1.05s
      qwen2.5:1.5b 1.0GB  29/30         27/30         0.93s
      qwen3:4b     2.5GB      -             -        ~20s   (thinking)

  So `--router-model` now defaults to `qwen2.5:3b` (1.8x faster, same accuracy,
  2.8GB less RAM) while `--model` keeps `qwen2.5:7b` for the fall-through
  agent. At n=30 a one-case difference is inside the noise, so the 3B/4B tier
  is effectively tied and was picked on size and speed. Thinking models are
  disqualified on latency rather than accuracy: qwen3:4b spent ~20s per
  question reasoning before emitting the same tiny JSON object.

  Switching models required re-validating the prompt against the new one, which
  is a lesson in itself: the 3B routed "how many points did Jokic score in the
  3rd quarter against Boston?" to `game_log` rather than `other`, which would
  have answered with a list of games instead of quarter scoring - the same
  silent substitution `single_game_high` was added to fix. A worked negative
  example in the router prompt fixed it; 30/30 on the 3B. `check_routing.py`
  now defaults to the router's model rather than the agent's, so it tests what
  actually ships, and run history records both models.

  Both fit in RAM together (~6.6GB of 16GB), and `OLLAMA_MAX_LOADED_MODELS=2`
  is worth setting: without it ollama unloads one to load the other whenever a
  question falls through, measured at 152s on a real fall-through query -
  nearly all swap rather than inference.

  Also measured and worth recording as a NEGATIVE result: removing `season` and
  `season_ref` from the router schema (extracting the season from the question
  text in code instead) did NOT improve the accuracy of the remaining slots -
  both variants scored 30/30 on them. The hypothesis that schema properties
  compete for the model's attention is not supported by this experiment,
  though the eval is at ceiling and so cannot detect a small effect either way.
  Deterministic season extraction is still worth doing for its own sake (30/30
  vs 29/30 on the season slot) but it is not a contention fix.

- **single_game_high: a shape whose absence was a wrong answer**: reported
  from real use - "who had the most assists in a single game and how many did
  he have" was answered "Nikola Jokic led the league in assists per game in the
  2026 regular season, at 10.7", in 1.76s. The real answer was Ryan Nembhard
  with 23, on 2026-04-13.

  Nothing was broken. There was simply no intent for a single-game MAXIMUM, so
  the router picked the nearest shape it had (`leaderboard`) and that template
  answered its own question correctly and confidently. A missing shape does not
  produce a refusal - it produces a fast, fluent answer to a DIFFERENT question,
  which is worse than the slow wrong answers this work started from, because
  nothing about it looks wrong. The fix is a template, not a prompt tweak.

  `single_game_high` reads `player_game_log`, so it reports the value and which
  game it was ("23, on 2026-04-13 vs CHI"), handles ties, supports a named
  player ("Jokic's highest rebound total in a single game"), and defaults to
  the current regular season like every other template. The router's
  `leaderboard` description now says explicitly that it covers SEASON stats and
  that single-game questions belong elsewhere - describing the neighbouring
  shape is part of adding a shape.

  Also observed and worth recording: adding an intent perturbed slot extraction
  on unrelated questions - "plot Curry's threes from last season" had been
  keeping its season and started dropping it again, so it is now marked as a
  known gap alongside the true-shooting one. That is a real property of routing
  everything through one small model, and the reason `check_routing.py` is run
  after every change rather than trusted from last time. 30/30.

- **Bound run_sql results by tokens, not rows**: a row cap does not bound what
  comes BACK. Measured, `SELECT * FROM player_game_log LIMIT 200` serialized to
  ~44,000 tokens - nearly three times the whole 16,384-token window, from a
  single tool call. Over `num_ctx` ollama cuts the prompt to about half,
  head-first and silently, throwing away the system prompt: the exact failure
  this codebase was rebuilt to eliminate, and reachable by the `SELECT *` a
  small model writes constantly.

  `run_sql` now returns as many rows as fit `MAX_RESULT_TOKENS` (2,000), chosen
  by binary search, and says how many were dropped with a pointer to aggregate
  in SQL (COUNT/SUM/GROUP BY) or select fewer columns rather than listing every
  row. 44,084 -> 1,828 tokens on that query. When even a single row is too
  large, the column list comes back instead - that is what the model needs to
  write a narrower query, and it beats a silently empty result.
  `get_leaderboard`'s model-supplied `limit` is clamped to 100 for the same
  reason.

- **Extra columns on leaderboards, and a bounded array slot**: "top 10 in
  NetPoints alongside their points per game" routed to the `leaderboard`
  template, which had no slot for the extra columns and answered without them -
  a silent partial answer. A `fields` slot now feeds `run_leaderboard`'s
  existing extra-columns support, and the answer switches from a sentence to a
  table once extra columns are asked for, with the qualifying minimum in the
  header so "why isn't X on this list?" has a visible answer. An UNKNOWN field
  falls through rather than being dropped.

  The bug worth remembering: the array slots had no `maxItems`. Under
  constrained decoding an unbounded array lets the grammar permit "one more
  item" forever, and the model takes that offer - it emitted
  `["points","minutes","minutes"]` on one question and then hung for over FIVE
  MINUTES on the next, because at ~10 tok/s on CPU a looping array is a stall,
  not a typo. With `maxItems` those questions route in ~2.2s. Bound every array
  slot in a constrained schema.

  The model still over-fills to the cap, so the template deduplicates and drops
  any field that restates the ranked metric - asked for "top scorers with their
  rebounds", the router also returned "points", which rendered the same 33.5
  twice under two headings. `check_routing.py` now treats a list expectation as
  a SUBSET check for the same reason (dropping a requested field is a bug, an
  extra one is noise), flushes its output so a running check no longer looks
  identical to a hung one, gained a `known_gap` marker for the "last season"
  slot the router reliably drops, and warns against running two copies at once -
  concurrent runs put a CPU-only ollama into a reload loop that wedges it for
  minutes. 27/27.

- **player_compare on the fast path, with a nickname table**: "compare Luka
  and SGA this season" used to fall through to the agent, which got it wrong
  for a reason no `KNOWLEDGE_BASE` entry could fix - it wrote correct SQL
  (`current_season()`, ILIKE name matching) but expanded "SGA" to
  `'%Scottie G. Allen%'` and compared Luka Doncic to Luka Garza, then reported
  that the player with 0.4 steals led the one with 1.6. Nickname resolution is
  a lookup, not something to hope a 7B model knows: `entities.PLAYER_NICKNAMES`
  is a curated table of 21 shorthands (SGA, Wemby, the Greek Freak, KD, CP3,
  ...), every one verified to match exactly one row in `players`, matched
  against the WHOLE query rather than as a substring so "book" resolves to
  Devin Booker while "notebook" resolves to nobody - which also fixed "Ant"
  previously matching every player with those letters in their name (Durant,
  Anthony, Antetokounmpo). "Luka" and "Curry" are deliberately absent: they are
  ordinary first names and surnames shared with real players, and the
  clarifying question is the honest answer - a curated table must not quietly
  become the popularity guess that was measured and rejected in stage 2.2.

  Output is a fixed-width table rather than prose, since comparisons are the
  one shape where a sentence actively hurts. 122s and wrong -> ~2s and correct.

  The bug worth remembering: `player_compare` was added to the router prompt
  and given a `players` slot, but not to the intent enum in `ROUTER_SCHEMA`, so
  constrained decoding could never emit it and every comparison silently routed
  to `player_stat`. Constrained decoding is exactly as literal as it sounds - an
  intent absent from the enum does not exist, however well the prompt describes
  it. There is now a test asserting every intent the prompt describes, and every
  ported template, appears in the enum. Also: `shot_chart` now reads "threes"
  from either `shot_value` or the equivalent box-score stat, since the router
  encodes it either way depending on wording - which incidentally closed the
  known "last season" gap on that question. Routing check 25/25.

- **Per-question prompt assembly, and a guard so the context cliff can never
  be silent again** (stage 3): the preamble that started all of this - 10,295
  tokens against a `NUM_CTX` of 8192, truncated head-first to 4,098 without an
  error - is now built per question. Five always-on rules that apply to any
  SQL, plus up to three `KNOWLEDGE_BASE` entries selected by keyword overlap
  with the question (`prompt.select_knowledge`). Measured: 4,337-5,821 tokens
  depending on the question, never truncated.

  The migration plan had called for DELETING each entry as its template
  landed. That was wrong, and worth recording as wrong: the agent still writes
  free-form SQL for every question no template covers, and those hit exactly
  the same traps - "compare Luka and SGA" needs the traded-player dedup rule
  and the named-player filtering rule just as much as a leaderboard did.
  Deleting them would not retire a cost, it would regress the one path that
  still writes SQL by hand. What actually cost something was every question
  paying for all 26 entries at once. Nothing is deleted now; almost nothing is
  loaded. Selection is plain keyword overlap rather than embeddings - no model
  call (the point is to spend less time, not more), deterministic, testable,
  and a miss is cheap, since a missing entry is just what the agent had before
  that entry existed. Entries whose trigger vocabulary differs from their own
  prose carry explicit `keywords`, because "how FAR was his average three?"
  never says "distance".

  `build_system_prompt` now raises `PreambleTooLarge` rather than handing
  ollama a prompt it will quietly cut in half, and a test asserts every
  assembled prompt fits - so adding a KB entry or a tool description that
  overflows fails in CI rather than in a wrong answer six weeks later. The
  original bug was silent for four commits.

  `NUM_CTX` raised 8192 -> 16384 on the (now rare) agent path. The truncation
  rule behind that was measured, not guessed: a prompt UNDER `num_ctx` is
  evaluated in full (~3,700 tokens at 8192 came back with `prompt_eval_count`
  3,696), one OVER it is cut to roughly half (10,093 at 8192 came back 4,098).
  A ~5,400-token preamble costs ~100s of CPU prefill on a fall-through
  question, against being quietly wrong at 8192. Also cut `get_leaderboard`'s
  tool schema 956 -> 686 tokens by replacing the 80-name metric enum with a
  description - `run_leaderboard` already answers a wrong name with a
  close-match suggestion and the full list, so the model recovers in one turn
  and pays for the list only when it needs it.

  Accepted cost: a per-question system prompt changes the cached prefix
  between questions, so the agent path no longer reuses the KV cache ACROSS
  questions. It still reuses it across the tool-call rounds WITHIN a question,
  which is where the cost compounded. Confirmed live on a fall-through
  question: 3 model calls in 122s, down from 5 in 385s, with the agent
  correctly using `current_season()` and ILIKE name matching that the old
  truncated prompt had discarded.

- **shot_chart on the fast path, completing stage 2** (2.5):
  `render_shot_chart` extracted out of `Toolbox` into `query/shotchart.py`
  taking `con` and `out_dir` explicitly - the same split `leaderboard.py` got,
  so the template and the agent tool are one implementation. Templates now
  take a `TemplateContext` (connection plus output directory) rather than a
  bare connection, since this is the first shape that writes a file.

  Two real bugs the port surfaced: `Toolbox.__init__` created `out_dir`, so
  the extracted function silently depended on someone else having made the
  directory first (it creates its own now); and `shot_chart` was the only
  template not defaulting an unspecified season to the current one, so "plot
  Curry's threes" charted his entire career in a single plot - 3,665 attempts
  across every season, now correctly 488 for the current one.

  Also a measured LIMIT of the schema lever found in 2.2. Requiring
  `season_ref` in addition to `stat` made things worse: it fixed one dropped
  season but crowded out other slots, and "most games with 15+ assists in
  2024?" started coming back with `season_ref: "current"` and no `season` at
  all - a named year silently replaced by the current one, worse than the miss
  it was meant to fix. Measured across five questions, optional-with-sharper-
  wording won 4/5 against required's 3/5, so it was reverted. Require the one
  slot that pays for itself, not every slot you wish the model would fill.

  Stage 2 is complete: every shape the agent's four tools covered now takes
  the fast path, plus several they did not. Routing check 23/23, 277 tests.

- **game_log and team_record on the fast path** (stage 2.4): `game_log` uses
  the team-perspective query both `KNOWLEDGE_BASE` entries describe -
  `team_box_stats` for opponent and home/away, `games` for `winner_team_id`,
  and `team_score`/`opponent_score` computed from `home_away` rather than
  reported raw, since raw home/away scores force a per-row guess about which
  number was this team's. `team_record` reads `standings` instead, which is
  authoritative for a full season and carries streak and seed alongside the
  record. A record over a LIMITED set of games is tallied in Python over
  exactly the rows being displayed, so the total cannot drift from the listing.

  Three guards, each for a failure those entries describe: `standings` has no
  `season_type`, so a playoff-record question falls through rather than
  answering with the regular-season number under a playoff-sounding label;
  `team_record` refuses a `limit` outright, because "how did they do in their
  last 10?" answered with the full-season record is a silent substitution (it
  is a `game_log` question, and the router now sends it there); and a
  malformed `date` slot is dropped rather than passed through, since
  `games.date` is a full ISO timestamp and `= 'YYYY-MM-DD'` is valid SQL that
  silently matches nothing.

  Two routing bugs the LIVE run caught that `check_routing.py` had not: "show
  me the Knicks LAST 5 games" routed as `order: "first"` and answered with
  October games, and "how did the Celtics do in their last 10 games?" routed
  to `team_record`. Both fixed in the router prompt, both now asserted in the
  check, and the second also guarded in code. The lesson is in the check now:
  assert every slot that changes the answer, not just the intent - an
  intent-only assertion passed while the answer was wrong.

  Also improved team matching while here: word-boundary matches now rank ahead
  of incidental substring hits, so "LA" offers "LA Clippers or Los Angeles
  Lakers" instead of nine teams including "Atlanta Hawks", and clarification
  lists are capped at five. And `standings` stores wins/losses/seed as DOUBLE,
  so a 53-29 record was printing as "53.0-29.0" - now formatted as the
  integers they are, with win percentage in the conventional .646 form.

- **Double-doubles and triple-doubles as leaderboard metrics** (stage 2.3):
  planned as a new per-game threshold template, landed as two entries in
  `LEADERBOARD_METRICS` instead. The `KNOWLEDGE_BASE` entry for this had
  already recorded that ESPN precomputes `doubleDouble`/`tripleDouble` as a
  season COUNT of such games, so "most triple-doubles" is a leaderboard, not a
  recount from `player_box_stats` - no new template, and it inherits the
  season default, traded-player dedup and team filtering for free. These had
  been routed to `other` on purpose since stage 1; they now take the fast
  path. Confirmed live: "Nikola Jokic led the league in triple-doubles in the
  2026 regular season, at 34."

- **player_stat on the fast path, and a constrained-decoding lever** (stage
  2.2): one named player's season numbers, read from
  `player_season_stats_deduped` so a traded player's multi-row season is
  already collapsed. Reports per-game and season-total together rather than
  trying to tell "how many points did X average" from "how many points did X
  score" - a distinction the router got wrong more often than right, and one
  that disappears by answering both.

  An incomplete name is answered with a question, not a guess. The migration
  plan had called for a prominence tiebreak (most minutes, dominance
  threshold); measured against the real warehouse, no threshold works - on
  season minutes "Luka" separates only 2.05x (Doncic vs. Garza), and on season
  points the ratios are 3.8x for "Luka" but 3.1x for "Brown", where Jaylen vs.
  Bruce Brown is genuinely ambiguous. Any threshold that resolves Luka also
  resolves Brown, wrongly and silently. So the template returns "'Luka'
  matches more than one player - did you mean Luka Doncic or Luka Garza?" in
  ~1.5s instead. That is a handled outcome, not a fall-through: the template
  knows exactly what is ambiguous, so handing the problem to an agent that
  would spend minutes and then guess is strictly worse.

  Found while wiring this up: with `stat` optional in `ROUTER_SCHEMA`, the
  model omitted it even for a question appearing VERBATIM as a worked example
  in the router prompt, and rewording the prompt did not fix it. Making `stat`
  required fixed it immediately - a constrained decoder only reliably
  considers a slot it is required to emit, and answers `""` when there is
  none (now pruned in `route()`, along with any other blank string slot).
  Prompt wording persuades; the schema decides. Also set
  `additionalProperties: false`, which stopped the model inventing junk slots
  like `shot_value: 0` on player questions. `scripts/check_routing.py` caught
  this - it went 14/16 before the fix and 16/16 after, which is exactly the
  regression it exists to catch, since no unit test can.

- **Shared entity resolution + the leaderboard shape on the fast path**
  (migration stages 2.0 and 2.1): new `query/entities.py` unifies the two
  name->id implementations that had grown separately - `get_leaderboard`
  resolved teams and errored on ambiguity, `render_shot_chart` resolved
  players and silently took the first match. Both are defensible for what they
  do, so the split is now explicit rather than accidental: `find_*` returns
  every candidate best-first and lets the caller choose, `resolve_*` returns
  `Entity | Ambiguous | NotFound` and never guesses. Templates use `resolve_*`,
  because a chart drawn for the wrong Curry is obvious on sight while a NUMBER
  attributed to the wrong Curry is indistinguishable from a right answer -
  ambiguity there falls through to the agent instead of being answered. An
  exact full-name match beats substring siblings, so "Jaylen Brown" resolves
  even alongside a hypothetical "Jaylen Brown Jr.".

  New `query/leaderboard.py` extracts the "top N players by X" query out of
  `toolbox.get_leaderboard`, which is now a thin JSON wrapper over it - the
  fast-path template and the agent tool are one implementation instead of two
  that can drift. The router's `stat` slot maps onto `LEADERBOARD_METRICS`
  through an EXPLICIT alias table rather than `get_close_matches`: fuzzy
  matching is right for suggesting a fix to a model that can then correct
  itself, but a template silently ranking by whichever metric happened to
  score highest is exactly the substitution failure this architecture exists
  to prevent - unmapped names fall through instead.

  Added a `season_type` slot (`regular`/`playoffs`) while here, because
  without one a playoff question silently answered for the regular season -
  the same "answered an easier question and said nothing" failure the standing
  rules were written for. Both templates honour it, and every generated answer
  now names its season and period outright so a substitution is visible rather
  than silent.

  New `scripts/check_routing.py` is the regression check for the part of the
  pipeline with no types: a fixed question set run through `route()` only,
  asserting intent and slots, including cases that must NOT be answered by a
  near-miss template (triple-doubles, per-quarter scoring, player
  comparisons). Currently 13/13 in ~1-2s per question. Confirmed live: "who
  were the top 10 in netpoints/100 possessions?" went from 31-168s through the
  agent to 1.8s; "top 5 scorers on the Lakers?" answers in 1.65s with the team
  resolved and named. `get_leaderboard`'s 939-token tool schema and the four
  `KNOWLEDGE_BASE` entries it makes redundant are deliberately NOT removed yet
  - the agent still needs the tool for leaderboards that also need an opponent
  or box-score join, so that is a stage-3 change with its own regression
  check, not a side effect of landing the template.

- **Intent router + deterministic query templates in front of the agent**:
  `query` and `ai` now route a question through a small classifier before the
  tool-calling agent ever runs. Motivated by a hard failure: "who had the most
  30+ point games this season?" failed three times in a row, each taking
  3-6 minutes and answering a season-scoring-average leaderboard for the wrong
  season instead. Root cause was not the question - `SYSTEM_PROMPT + TOOLS` had
  grown to 10,295 tokens against `NUM_CTX = 8192`, and ollama truncates
  head-first, so only 4,098 tokens ever reached the model. Confirmed with a
  canary marker at each end of the system prompt: only the tail one came back.
  The discarded head held `TABLE_SUMMARY` (the entire schema), both standing
  rules, and the first ~15 `KNOWLEDGE_BASE` entries - including the one whose
  worked example is exactly this query's `COUNT(*) ... >= threshold` pattern.
  What survived was the tool schemas, whose descriptions say to always prefer
  `get_leaderboard`, which is precisely what the model did. Traced the preamble
  size back through history: it crossed 8192 at `c209516` ("Add get_leaderboard
  tool") and stayed silent for four commits, growing 2,416 -> 10,295 tokens in
  eight days without ever shrinking.

  Truncation also explains the latency, which was 100% model prefill (`model
  385.41s (5 calls), tools 0.04s (3 calls)`): the truncation offset slides as
  the conversation grows, so ollama's KV prefix cache misses on every
  iteration. Measured directly - a two-turn conversation runs 70.0s then 62.8s
  when the prompt is truncated, but 36.5s then 2.9s when it fits.

  New `query/router.py` classifies the question into `{intent, slots}` under a
  JSON schema passed as ollama's `format`, so decoding is constrained rather
  than merely prompted - this removes the malformed-tool-call failure mode that
  `_extract_unrun_sql` and the `pending_error` fabrication guard exist to catch
  after the fact. Its prompt carries no schema, no SQL and no gotchas (~430
  tokens), so it fits and stays cached. New `query/templates.py` holds the
  deterministic side; `threshold_count` is the first shape ported, with every
  correctness rule (column whitelist, current-season default, regular-season
  filter, per-token player matching) in code rather than prose.

  Templates phrase their own answers, which is not just cosmetic: ollama keeps
  one KV cache slot per model by default, so a second model call with a
  different system prompt evicts the router's cached prefix. Measured - three
  consecutive router calls run 11.63s / 1.27s / 1.66s, but interleaving a
  narrator call puts the next router call back to 11.18s. Dropping the narrator
  removed the eviction, removed the last place on the fast path where a number
  could be invented, and produced better prose than the model had ("Nikola
  Jokic had the most games with 20+ rebounds in the 2026 regular season, with
  5." versus "...playing such games in 5 games").

  Everything not yet ported falls through to the existing agent untouched -
  unported intents, slots that fail validation, and any router or template
  failure - so a router slip degrades to the old slow path, never to a wrong
  answer. Two model slips seen while building it are now handled in code rather
  than prompt: relative seasons ("last season" once produced `season=20222023`,
  which as a SQL filter silently matches nothing) resolve from a `season_ref`
  enum next to `current_season()`, and triple-doubles route to `other` instead
  of mis-routing into `threshold_count`. Confirmed live: 385s and wrong ->
  2.50s warm / 12.0s cold and correct, one model call instead of five, 431
  prompt tokens instead of 4,098. Added `--no-fast-path` to force the agent
  path for side-by-side comparison, and `FAST-PATH-MIGRATION.md` with the
  remaining shapes, the `KNOWLEDGE_BASE` entries each retires, and the
  projected 10,295 -> ~3,700 token preamble.

## 2026-09-05

- **CLI cleanup: fix confusing/wrong defaults, remove flags that were cheap to
  make default**: reviewed every subcommand's flags for confusing or invalid
  combinations. Fixed two real bugs: (1) `data check`'s live ESPN cross-check
  was the DEFAULT (`--offline` was the opt-OUT), directly contradicting
  README's own "Known limitations" note describing it as opt-in via a
  `--live` flag that didn't even exist - replaced `--offline` with `--live`
  (off by default, matching the docs and making a plain `data check` fast
  and network-free); (2) `data pull` defaults to `--season-types 2,3`
  (skips preseason) while `data check` defaulted to a hardcoded `1,2,3`, so
  the natural `data pull` then `data check` sequence reported preseason as
  entirely missing for data nobody asked to fetch - fixed by making
  `--season-types` auto-discover from local data (a new `discover_season_types`
  in `check/report.py`, same pattern `--seasons` already used), removing the
  mismatch structurally instead of just picking a new hardcoded string that
  could drift out of sync again. Also removed `--advanced-stats` from `data
  pull`/`data load` entirely - it's pure computed DuckDB views over data
  already fetched (no extra network request, no meaningful storage), so
  there was no real reason to make every caller opt into it - `warehouse.build()`
  now always builds `player_advanced_stats`/`player_season_advanced_stats`
  when `player_box_stats` is present. This surfaced a real robustness gap:
  since these views now run unconditionally instead of behind an isolated
  opt-in flag, a `player_box_stats` missing a column the formulas need would
  crash the ENTIRE warehouse build, not just skip two optional views -
  fixed by checking for the required columns up front and skipping (logging
  why) instead of raising, the same graceful-skip already used when
  `player_box_stats` isn't loaded at all. This also makes the earlier
  `--fetch-only` + `--advanced-stats` silent-drop combination impossible,
  since there's no longer a separate flag to drop. Confirmed live against
  the real warehouse: `data load` (no flags) now builds both advanced-stats
  views automatically; `data check --seasons 2026` (no flags) correctly
  auto-discovers all three locally-present season types and completes in
  ~6s fully offline.

- **Per-run history logging + timing metrics (query/ai)**: added
  `query/history.py`'s `RunHistory`, written by every `Agent.ask()` call
  (both the one-shot `query` command and each turn of the interactive `ai`
  REPL) to a new file under `.history/` (gitignored), named with a random
  hash - the command invoked, the full tool-call/thinking trace, per-model-
  call and per-tool-call timing, and the final answer (or a traceback, on an
  exception - wrapped in `ask()`'s `try/finally` so this happens even when
  the call never returns normally). Captured regardless of whether
  `--verbose` was passed - `--verbose` now only controls whether that same
  trace is ALSO echoed to stderr live, not whether it's recorded at all, so
  a run nobody was watching still has full evidence to look back at
  afterward. Every run also prints a one-line timing summary to stderr
  (total time, model-inference-vs-tool-execution split) - confirmed live
  this makes the actual bottleneck obvious: a real `get_leaderboard` call
  took 0.05s against ~53s of model inference across two rounds.

- **NetPoints fingerprint categories as get_leaderboard metrics, plus a KB
  entry**: net_points_player_fingerprint (added earlier today) went
  unused in a live query ("who has the best rim scoring NetPoints?") - the
  model fell back to an unrelated per-game NetPoints leaderboard instead,
  since nothing pointed it at the new table. Added all 66 fingerprint
  category columns as get_leaderboard metrics (`<category>_o_net_pts` /
  `_d_net_pts` / `_t_net_pts` for each of the 22 categories - two_pt,
  three_pt, driving, fastbreak, rebound, turnover, rim, ...), generated from
  the same category list `parse.py` already uses to build those columns
  (extracted to a new shared `association/net_points_categories.py`, one
  source of truth for both). Required a new `LeaderboardMetric.has_season_type`
  flag - this table, unlike every other metric's table, has no season_type
  column at all. Kept the free-text tool description short (listing the ~14
  original metrics by name, describing the 66 fingerprint ones by their
  naming pattern instead of spelling out all of them) while the JSON schema's
  `enum` still lists every valid value. Also added a KNOWLEDGE_BASE entry for
  the run_sql fallback path. Confirmed live: the exact previously-failing
  question now resolves via `get_leaderboard(metric='rim_o_net_pts', ...)` in
  one call, matching the values already verified directly against the DB.

- **NetPoints per-player skill/play-type breakdown (net_points_player_fingerprint)**:
  a user asked whether we had enough NetPoints coverage to recreate
  espnanalytics.com's "Net Pts Fingerprint" page - investigation found the
  page draws from a third, not-yet-ingested file
  (`fingerprint-files/nbafingerprint_{start_year}.json`, same public,
  unauthenticated bucket as the season-level file), far richer than anything
  already ingested: 66 NetPoints columns per player-season - 22 shot/play-
  type categories (two_pt, two_pt_shooting, three_pt, three_pt_shooting,
  assist, bad_pass, corner, cutting, driving, fade, fast_break, floating,
  foul, free_throw, hook, layup, mid_range, putback, rebound, rim, total,
  turnover), each split into offense/defense/total. Added as a new default
  (not opt-in) table, `net_points_player_fingerprint`. Real wrinkle,
  confirmed live: a season with no file published yet returns HTTP 403 from
  this bucket, not the 404/400 every espn.com endpoint uses for missing data
  - handled explicitly rather than left to raise, the same shape of fix
  `netpoints_client.py` already needed for its own bucket's AccessDenied
  quirk. Keyed by NBA.com's own player id with no ESPN crosswalk provided,
  same as the per-game NetPoints data - resolved by exact display-name match
  against `players` (ambiguous/unmatched names dropped, not guessed).
  Bio fields the source also carries (height, draft year, date of birth)
  are deliberately not kept - real, already-sourced-from-ESPN data on
  `players`, not duplicated from a second source that might disagree. Backed
  into the same in-season-refresh rule added earlier today (re-fetches while
  the season is still current). Backfilled locally for every season NetPoints
  covers (2019-2026): 4,338 rows, verified live against the source (LeBron
  James's two_pt_o_net_pts/turnover_d_net_pts match espnanalytics.com's raw
  file exactly) and through a real query (rim-scoring NetPoints leaders for
  the current season come back as Jokic/Giannis/SGA - plausible real players,
  not noise).

- **Season-aggregate fetches now stay current during an in-progress season**:
  a user asked whether `data pull` still works correctly mid-season -
  investigation found it didn't, for anything that isn't a per-game fetch.
  `standings`, `team_season_stats`, `team_power_index`, `player_season_stats`,
  and NetPoints' season-level tables all used the same existence-check
  resumability as immutable per-game data, but these reflect ESPN's own
  live, evolving computation while a season is in progress - once first
  pulled, every later `data pull` silently kept whatever was fetched first,
  with no `--force` reminder and no way to tell from `data check` (which
  only reports row counts, not freshness). Worse for `player_season_stats`
  specifically: it was only ever fetched at all once EVERY game in a
  season+type was fully resolved - during an in-progress season it was
  never called even once, not just stale. Fixed by tying each fetch to
  season completeness instead of plain existence: `standings`/
  `team_power_index`/NetPoints re-fetch (a single cheap request each) as
  long as the season is still the current one by this project's season-ends
  calendar convention (extracted to a new shared `association/season.py`,
  used by both the fetch pipeline and the query engine's existing
  `current_season()`); `team_season_stats`/`player_season_stats` (one
  request per team/player) re-fetch based on the season+type's own
  completion marker instead, so they stop as soon as that season+type is
  actually done rather than waiting for the calendar to roll over in
  October. The player-stats loop also moved out from behind the
  "season fully resolved" gate so it runs (and refreshes) every pull while
  the season is still in progress, not just after it ends.

- **Expose plays, add a verified per-quarter-scoring derivation, and a
  "don't silently answer an easier question" rule**: a real question ("how
  many games did Steph Curry score more than 15 points in a single
  quarter?") wandered through three wrong queries (hallucinated column
  names, an unrelated pivot to a single specific date) and then, worst of
  all, silently gave up and answered with an unrelated full-season stats
  dump - presented as if it satisfied the original question, with no
  indication the real question had been abandoned. Root cause of the first
  part: `plays` (play-by-play, opt-in via `--include-pbp`) was never added
  to KNOWN_TABLES/TABLE_SUMMARY at all, despite existing in the warehouse -
  the model had no way to even discover it via describe_table. Added it,
  and a new KNOWLEDGE_BASE entry with a verified derivation: per-quarter
  points aren't a stored column, but each scoring play in `plays` carries
  the running home/away score, so a play's own point value is that score
  minus the immediately prior scoring play's score for the same side (a
  LAG() window function). Two real bugs found and fixed while building and
  validating this, both confirmed live: (1) ordering by play_id is wrong -
  it's not reliably sortable as an integer across a whole game (confirmed:
  it broke chronological order badly enough to attribute 90+ points to a
  single play) - fixed by ordering on period + clock parsed to seconds-
  remaining instead; (2) filtering to one player BEFORE the LAG() window
  function (in the same CTE) breaks the ordering context the same way -
  made this exact mistake twice while building the pattern, fixed by
  computing the window function over every scoring play in the game first,
  filtering to one player only in an outer query afterward. Even correct,
  this derivation was measured (live, across the full dataset) to disagree
  with the official player_box_stats game total for ~1% of player-games -
  documented as a known, honest limitation (likely real ESPN play-by-play
  vs. box-score inconsistencies) rather than presented as exact. Separately,
  added a standing rule directly in the system prompt: if a query can't be
  made to answer what was actually asked, say so - never silently substitute
  an easier question and present it as satisfying the original one.

- **Schema-level helpers for the run_sql fallback path: current_season()
  macro, player_season_stats_deduped view**: get_leaderboard (below) moves
  leaderboard correctness into Python for its fixed set of metrics, but
  run_sql is still the escape hatch for anything outside that set (a
  leaderboard needing an opponent/per-game join, single-player lookups,
  etc.) - and those ad hoc queries were still relying on the model
  correctly recalling and re-deriving the same rules from KNOWLEDGE_BASE
  prose every time. Added the same two rules directly to the DuckDB
  warehouse instead: a `current_season()` SQL macro (built unconditionally,
  before any table load, so it works even against an otherwise-empty
  database) replacing the multi-line CASE/EXTRACT expression the model
  previously had to write out by hand, and a `player_season_stats_deduped`
  view that already collapses a traded player's per-team-stint rows to the
  combined row - no QUALIFY pattern needed for an ad hoc season-total/
  average query. Updated the relevant KNOWLEDGE_BASE examples and
  TABLE_SUMMARY to point at both. Confirmed live against the real warehouse:
  `SELECT current_season()` returns 2026, and player_season_stats_deduped
  drops the ~3,475 duplicate stint rows player_season_stats carries for
  every traded player.

- **New get_leaderboard tool: correctness rules moved from prose into code**:
  a series of real "top N players by X" queries kept failing in different
  ways even with KNOWLEDGE_BASE entries covering each one - a season default
  dropped as soon as a second filter was also needed, a min-sample rule that
  only applied to the one metric it was written for, and (worst) two runs of
  the identical question with the same thinking model producing two
  different metrics and answers five minutes apart. Root cause: every
  KNOWLEDGE_BASE entry makes the model responsible for remembering and
  re-deriving one more rule from prose, on every query, and that stops
  composing reliably as the list grows - more "thinking" time doesn't fix a
  fundamentally stochastic process being asked to reproduce a growing
  checklist exactly. Added `get_leaderboard(metric, season, season_type,
  min_sample, team, fields, limit)` (`query/toolbox.py`, registry in new
  `query/metrics.py`) - a fixed, known set of metrics (usage_pct, ts_pct,
  efg_pct, avg_points/rebounds/assists/steals/blocks, netpoints_total/
  offense/defense, netpoints_per_100/offense_per_100/defense_per_100) where
  the season default, the qualifying minimum sample, the NetPoints string-
  vs-numeric season_type quirk, and traded-player dedup are all resolved
  once in Python instead of re-derived by the model per query. `team` and
  `fields` are deliberately narrow (a resolved team name/abbreviation, a
  whitelist of extra box-score columns) rather than a free-text filter or
  arbitrary column passthrough, which would just reopen the same SQL-
  generation reliability problem this tool exists to close. The model's job
  shrinks to picking a metric name and filling a few slots - confirmed live,
  the exact two failing questions ("top 10 by average usage rate" and
  "average netpoints in 2026") now resolve in one tool call each, with the
  default (non-thinking) model, correctly qualified, no KB-composition
  needed. KNOWLEDGE_BASE entries for these metrics kept as run_sql fallback
  guidance (for a leaderboard that also needs an opponent/per-game join),
  now pointing at get_leaderboard first.

- **Agent: never finalize an answer right after an unrecovered run_sql
  error**: a real query hit a column-not-found SQL error, and instead of
  retrying with a corrected query, the model finalized with a fabricated
  answer using literal `[Player Name 1]` / `[NetPoints Value]` placeholder
  text as if it were real data (confirmed live). This is worse than a wrong
  answer - it looks like real, if truncated, output. Added a second guard
  alongside the existing SQL-as-prose one: track whether the most recent
  `run_sql` call in the turn errored, and if the model then tries to finalize
  with plain prose (not unrun SQL, which the existing guard already handles),
  refuse it - nudge a retry (same MAX_ERROR_RECOVERIES=2 cap pattern as the
  existing recovery), and if still unrecovered after that, return an honest
  "I ran into an error... and wasn't able to recover" message with the real
  error shown, instead of trusting whatever the model wrote. Deliberately
  scoped to `run_sql` only, not `describe_table`/`render_shot_chart` - a
  `describe_table` miss (e.g. an unknown table name) doesn't mean the model
  lacks real data, since an earlier `run_sql` call in the same turn may have
  already succeeded; treating every tool error the same way broke an
  existing test where a `describe_table` call incidentally failed against an
  empty test database with unrelated real data already in hand.

- **KNOWLEDGE_BASE: rate-stat leaderboards need a minimum sample, and a
  standing current-season default**: a user's real query ("top 10 players by
  average usage rate") came back with Izaiah Brockington at #1 (65.93% over
  8 games), and the rest of the top 10 were all 1-3-game stints too - the
  real leaders (Embiid, Giannis, Doncic, ~37-39%) were buried below dozens of
  small-sample flukes. Confirmed live: usage_pct is a ratio, so a few
  unusual garbage-time minutes can swing it far past what any sustained role
  reaches; `WHERE games_played >= 20` fixes the leaderboard completely.
  Added a KNOWLEDGE_BASE entry generalizing this to any rate/percentage stat
  (usage_pct, ts_pct, efg_pct) - same principle as the NetPoints-per-100
  minimum-minutes entry added earlier the same day. Separately, added a
  standing rule (in the system prompt directly, not just the growing gotcha
  list) that a question naming no season should default to the CURRENT
  season computed from `CURRENT_DATE` - not whatever season happens to have
  the most data loaded, and not silently substituted without saying so if
  the current season has no data yet. Confirmed live this works reliably for
  a simple query ("who leads the league in points?" correctly resolved to
  the current season); a compound query needing both the season default AND
  a minimum-games filter in the same query did not reliably pick up the
  season filter despite three different prompt placements tried - noted as
  a known small-model instruction-following gap (consistent with prior ones
  documented in this file), not chased further given diminishing returns.

- **NetPoints per-100-possession rate columns on `net_points_player`**: a user
  asked whether NetPoints has a normalized (rate) form, since `overall`/
  `offense`/`defense` are season cumulative totals - confirmed live, two
  players with the identical 82 games this season range from -194.62 to
  +164.15, so ranking by the total alone rewards playing more possessions,
  not being better per-possession. Investigated whether espnanalytics.com's
  own "Net Points / 100 Poss" toggle computes that client-side or pulls it
  from somewhere else: confirmed live (via the site's own network requests)
  that it fetches a second, separate flat file - `nba_net_pts100_data.json`
  - on the same public, unauthenticated S3 bucket as the file already fetched
  for `net_points_player`, rather than computing the rate in the browser.
  Added `overall_per_100_poss`/`offense_per_100_poss`/`defense_per_100_poss`
  (ESPN Analytics' own pre-computed values, not a local approximation) and
  `total_minutes` (for a per-36 comparison instead, if wanted) by joining that
  file in at parse time on (athlete_id, season, net_points_season_type) -
  confirmed live to be a unique key in both files. A handful of degenerate
  stints (e.g. a single scoreless playoff game) exist in the totals file but
  are dropped from the rate file - left NULL there rather than guessed, same
  fail-safe pattern as every other NetPoints join in this project. No new
  fetch flag needed - both files are already covered by the existing
  (default, not opt-in) NetPoints fetch.

## 2026-09-04

- **KNOWLEDGE_BASE: fieldGoalsMade already includes 3-pointers**: a user
  caught real bad math in a live answer - 2x made-2pt + 3x made-3pt came out
  well above the player's actual points. Root cause: fieldGoalsMade/
  fieldGoalsAttempted are TOTAL field goals (2pt AND 3pt combined, the
  standard box-score convention), and threePointFieldGoalsMade/Attempted is
  a SUBSET already counted inside those totals - not a separate, additional
  category the way freeThrows is. A prior KNOWLEDGE_BASE example (added this
  same day, for the NetPoints leaderboard fan-out fix) used fieldGoalsMade
  as if it were 2-point-specific, which is exactly this bug. Fixed that
  example and added a dedicated entry: true 2-point makes/attempts are
  fieldGoalsMade - threePointFieldGoalsMade (same pattern for attempted).
  Verified live on all three tables that carry these fields
  (player_box_stats, team_box_stats, player_season_stats): points ==
  (fieldGoalsMade - threePointFieldGoalsMade)*2 + threePointFieldGoalsMade*3
  + freeThrowsMade, exactly, every row checked. Re-ran the original query:
  all 10 rows now reconcile correctly.

- **Fix NetPoints leaderboard fan-out, and a dead-end recovery-cap message**:
  a real query ("top 10 highest NetPoints, with opponent and box score
  stats") joined season-level `net_points_player` to per-game
  `player_box_stats` on athlete_id+season - fanning out into one row per
  game (not per player), so `ORDER BY ... LIMIT 10` returned up to 10 games
  from whichever one or two players had the highest season total, not 10
  distinct players. Also referenced a nonexistent `net_points_player.
  season_type` column (only the string `net_points_season_type` exists) and
  an unquoted `AS 2pta`-style alias (invalid - identifiers can't start with
  a digit unquoted). The model then made it worse across retries: guessed
  wrong snake_case column names instead of calling describe_table, and
  silently dropped the "opponent" requirement. Added a KNOWLEDGE_BASE
  pattern: a request for an opponent or per-game stats alongside NetPoints
  means one game, so use `net_points_player_game` (already has its own
  season_type and event_id) joined on event_id+athlete_id, not season -
  verified live, all 10 rows now match ground truth exactly. Also fixed:
  once the SQL-as-prose auto-recovery cap was exhausted, a model that kept
  printing SQL instead of running it got that raw prose returned as the
  final answer verbatim - including "Let's run this corrected query" that
  never ran (confirmed live). The agent now says plainly that it couldn't
  get a working query, rather than returning text that only looks like an
  action still in progress.
- **Switched the CLI from argparse to Click; added shell completion**: Click
  generates bash/zsh/fish completion directly from the command definitions
  (subcommands, options, `--log-level`'s choices), so there's nothing to
  write or keep in sync by hand - the whole reason for the switch. Ready-made
  scripts live in `completions/`; `README.md` documents both that and the
  dynamic `_ASSOCIATION_COMPLETE=...` alternative. All CLI behavior -
  commands, flags, defaults, help text - is unchanged; only the argument-
  parsing implementation and its tests (now using Click's `CliRunner`)
  changed.
- **KNOWLEDGE_BASE: exact-date filtering, and abbreviations vs ids**: found
  while verifying the NetPoints per-game work, but general bugs unrelated to
  it. A live query filtered `games.date = '2026-04-12'` - `date` is a full
  ISO timestamp (`2026-04-12T22:00Z`), so exact equality against a bare date
  silently matches nothing; needs `date LIKE 'YYYY-MM-DD%'`. A second query
  then filtered `home_team_id = 'NY'` - comparing an id column directly to a
  team abbreviation, the same silently-empty failure mode already documented
  for names, just not generalized to abbreviations. Extended the existing
  KNOWLEDGE_BASE entry and added a new one, both with corrected examples.
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
