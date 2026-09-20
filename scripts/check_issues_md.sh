#!/usr/bin/env bash
# Pre-commit gate: ISSUES.md keeps exactly one heading per priority tier, in
# order - "## P1: ...", "## P2: ...", "## P3: ...", "## P4: ...".
#
# The headings are load-bearing, not decoration: `scripts/sync_issues.py`
# labels a GitHub issue by the section its entry sits in, and the priority
# definitions at the top of the file promise a reader four ranked lists. A
# heading can vanish without anything noticing - an edit deleting the last
# entry of one section swallowed the next section's heading with it
# (898ef66 took "## P2" out; every P2 then read as a P1, and the sync would
# have filed a new P2 under the P1 label), and #127 records the P3 heading
# missing for days before that. Nothing else reads the headings, so nothing
# else fails.

set -euo pipefail

cd "$(dirname "$0")/.."

status=0
for tier in P1 P2 P3 P4; do
    count="$(grep -c "^## ${tier}: " ISSUES.md || true)"
    if [[ "${count}" -ne 1 ]]; then
        echo "error: ISSUES.md has ${count} '## ${tier}: ...' headings, expected exactly one." >&2
        status=1
    fi
done
if [[ "${status}" -ne 0 ]]; then
    echo "       The entries under a missing heading are read as the tier above it; put it back where the tier's first entry starts." >&2
    exit "${status}"
fi

order="$(grep -o '^## P[1-4]' ISSUES.md | tr -d '\n')"
if [[ "${order}" != "## P1## P2## P3## P4" ]]; then
    echo "error: ISSUES.md's priority headings are out of order: ${order}" >&2
    exit 1
fi
