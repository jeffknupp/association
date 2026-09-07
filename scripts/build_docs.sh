#!/usr/bin/env bash
# Build the Sphinx documentation, failing on any warning.
#
# -W turns warnings into errors so a broken cross-reference, a module missing
# from the API tree, or a malformed docstring fails the commit rather than
# rotting silently in the generated HTML.
#
# --keep-going reports every problem in one pass instead of stopping at the
# first, so a batch of doc changes needs one fix cycle rather than several.
set -euo pipefail

SPHINX="${SPHINX_BUILD:-.venv/bin/sphinx-build}"
if [[ ! -x "$SPHINX" ]]; then
    echo "docs: $SPHINX not found - run 'uv sync --extra docs'" >&2
    exit 1
fi

exec "$SPHINX" -b html docs docs/_build/html -q -W --keep-going
