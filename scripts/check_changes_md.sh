#!/usr/bin/env bash
# Pre-commit gate: any commit touching src/ must also touch CHANGES.md, so
# the changelog can't silently drift behind the actual code (as opposed to
# auto-generating an entry from the commit message, which nobody reads
# critically at commit time and produces low-value entries).
#
# It also checks there is EXACTLY ONE "## Unreleased" heading. Two parallel
# branches each adding one merge without a conflict - git sees an insert in two
# places, not a clash - and the result is a changelog with two Unreleased
# sections, the second of which `scripts/bump_version.py` leaves behind when it
# renames the first. Nothing else noticed: the rule above only asks that the
# file was touched. Found on 2026-09-18 by reading the file, which is not a
# gate.

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

# Counted over the file on disk rather than the index: a duplicate heading is
# wrong whether or not this particular commit touched the changelog, and the
# next release is what it breaks.
headings="$(grep -c '^## Unreleased$' CHANGES.md || true)"
if [[ "${headings}" -gt 1 ]]; then
    echo "error: CHANGES.md has ${headings} '## Unreleased' headings, expected at most one." >&2
    echo "       Two branches each added one and git merged both without a conflict." >&2
    echo "       Move every entry under the first heading and delete the rest:" >&2
    grep -n '^## Unreleased$' CHANGES.md >&2
    exit 1
fi
