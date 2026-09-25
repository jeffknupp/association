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
# And a MISSING heading, when src/ changed, means the entry for this change
# landed inside the last release's section: the first commit after a release
# did exactly that on 2026-09-20, and the release notes on GitHub then
# disagreed with the file. Zero headings is fine only for a commit that
# changes no source.
if echo "${staged}" | grep -q '^src/' && [[ "${headings}" -eq 0 ]]; then
    echo "error: src/ changed but CHANGES.md has no '## Unreleased' heading - add one above your entry." >&2
    echo "       Without it the entry sits inside the last release's section." >&2
    exit 1
fi
if [[ "${headings}" -gt 1 ]]; then
    echo "error: CHANGES.md has ${headings} '## Unreleased' headings, expected at most one." >&2
    echo "       Two branches each added one and git merged both without a conflict." >&2
    echo "       Move every entry under the first heading and delete the rest:" >&2
    grep -n '^## Unreleased$' CHANGES.md >&2
    exit 1
fi

# Every released version keeps its own heading. The fix for the 2026-09-20
# slip above renamed "## 4.3.0 - 2026-09-19" to "## Unreleased" instead of
# adding a heading, so 4.3.0's entries sat under Unreleased and 4.4.0's
# release notes would have repeated all of them. Found on 2026-09-25 by
# reading the file after the bump - which is not a gate. The version in
# pyproject.toml is checked everywhere (the heading of the release just made,
# the one this slip removes); every other tag is checked where tags exist -
# CI's shallow clone has none, and says so rather than passing quietly.
version="$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])")"
tags="$(git tag --list 'v[0-9]*.[0-9]*.[0-9]*')"
if [[ -z "${tags}" ]]; then
    echo "CHANGES.md check: no release tags in this clone, so only ${version}'s heading is checked." >&2
fi
versions="$(printf '%s\n%s\n' "${version}" "${tags//v/}" | sort -u)"
missing=""
while read -r v; do
    [[ -z "${v}" ]] || grep -q "^## ${v} " CHANGES.md || missing="${missing} ${v}"
done <<< "${versions}"
if [[ -n "${missing}" ]]; then
    echo "error: CHANGES.md has no '## X.Y.Z - <date>' heading for released version(s):${missing}." >&2
    echo "       A release's entries must stay under its own heading; put the heading back" >&2
    echo "       above them (the tag's copy of CHANGES.md has it) rather than renaming it." >&2
    exit 1
fi
