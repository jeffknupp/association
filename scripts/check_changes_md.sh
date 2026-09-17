#!/usr/bin/env bash
# Pre-commit gate: any commit touching src/ must also touch CHANGES.md, so
# the changelog can't silently drift behind the actual code (as opposed to
# auto-generating an entry from the commit message, which nobody reads
# critically at commit time and produces low-value entries).

set -euo pipefail

cd "$(dirname "$0")/.."
staged="$(git diff --cached --name-only)"

# This only ever sees the index, never the working tree - see AGENTS.md,
# "Before you commit" ("--all-files means every file git knows about"). With
# nothing staged (an unstaged src/ edit, or a plain clean tree), the grep
# below matches nothing either way and the hook exits 0 without having
# checked anything - say so, rather than let that read as a real pass.
if [[ -z "${staged}" ]]; then
    echo "CHANGES.md check: nothing staged, so nothing to check - git add first if src/ changed." >&2
    exit 0
fi

if echo "${staged}" | grep -q '^src/' && ! echo "${staged}" | grep -q '^CHANGES\.md$'; then
    echo "error: src/ changed but CHANGES.md wasn't updated - add an entry describing this change." >&2
    exit 1
fi
