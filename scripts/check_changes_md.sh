#!/usr/bin/env bash
# Pre-commit gate on CHANGES.md's HEADINGS (the entries themselves are
# generated from the commits at release, scripts/changelog_from_git.py,
# decision D20 - no commit is asked for an entry): EXACTLY ONE "## Unreleased"
# heading at most (two parallel branches each adding one merge without a
# conflict, and scripts/bump_version.py renames only the first), and every
# released version keeping its own heading.

set -euo pipefail

cd "$(dirname "$0")/.."

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

# Every released version keeps its own heading: renaming a release's heading
# to "## Unreleased", instead of adding one above it, puts that release's
# entries under Unreleased, and the next release's notes repeat them all. The
# version in pyproject.toml is checked everywhere (the heading of the release
# just made, the one such a rename removes); every other tag is checked where tags exist -
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
