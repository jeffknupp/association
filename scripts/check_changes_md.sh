#!/usr/bin/env bash
# Pre-commit gate: any commit touching src/ must also touch CHANGES.md, so
# the changelog can't silently drift behind the actual code (as opposed to
# auto-generating an entry from the commit message, which nobody reads
# critically at commit time and produces low-value entries).

set -euo pipefail

cd "$(dirname "$0")/.."
staged="$(git diff --cached --name-only)"

if echo "$staged" | grep -q '^src/' && ! echo "$staged" | grep -q '^CHANGES\.md$'; then
    echo "error: src/ changed but CHANGES.md wasn't updated - add an entry describing this change." >&2
    exit 1
fi
