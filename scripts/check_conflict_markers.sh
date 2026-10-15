#!/usr/bin/env bash
# Pre-commit gate: no tracked file may hold a merge-conflict marker.
#
# A merge resolved by a script that failed, chained to `git add` and the
# commit with `;` rather than `&&`, lands `<<<<<<< HEAD` in a file - and every
# other gate passes, because a marker is valid Markdown and the changelog hook
# counts headings, not markers.
#
# The start and end markers and the diff3 base marker (`||||||| `) are
# checked - a merge resolved under zdiff3 by a script that reads "ours" up to
# the first `=======` keeps the base marker; a bare `=======` line is a
# legitimate setext underline in Markdown.

set -euo pipefail

cd "$(dirname "$0")/.."
found="$(git ls-files -z | xargs -0 grep -nE '^(<<<<<<< |>>>>>>> |[|]{7} )' -- 2>/dev/null || true)"
if [[ -n "${found}" ]]; then
  echo "merge-conflict markers in tracked files:" >&2
  echo "${found}" >&2
  exit 1
fi
tracked="$(git ls-files | wc -l)"
echo "no conflict markers in ${tracked// /} tracked files"
