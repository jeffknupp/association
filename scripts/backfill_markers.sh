#!/usr/bin/env bash
# Backfill _complete/_resolved markers for data fetched before those markers
# existed (everything pulled prior to the pipeline refactor that added them).
#
# This is just `association data pull` over the known local season range - no
# separate logic to duplicate or drift out of sync with the real pipeline.
# Its existing resumability already makes this cheap:
#   - Any game/team-stat/player-stat file already on disk is skipped outright
#     (a single stat() call) - none of the ~1200+ games/season already fetched
#     get re-downloaded.
#   - The only network calls are: the season+type schedule pull (needed once
#     to discover the full official game list, including any postponed/
#     cancelled games we don't have a local marker for yet), and a summary()
#     call for each event_id that has neither a games/*.parquet file nor a
#     _resolved/*.marker - i.e. games whose outcome we don't already know.
#   - Once a season+type turns out fully accounted for (every discovered game
#     either played or resolved-as-never-played), pull writes its _complete
#     marker - so re-running this script later against the same range is
#     near-instant (every already-backfilled season+type short-circuits on
#     that marker with zero network calls).
#
# Usage:
#   ./scripts/backfill_markers.sh                  # defaults to 2020-2026, all season types
#   ./scripts/backfill_markers.sh 2020 2026 1,2,3   # explicit range/types

set -euo pipefail

FIRST_SEASON="${1:-2020}"
LAST_SEASON="${2:-2026}"
SEASON_TYPES="${3:-1,2,3}"

cd "$(dirname "$0")/.."
.venv/bin/association data pull --seasons "${FIRST_SEASON}-${LAST_SEASON}" --season-types "${SEASON_TYPES}"
